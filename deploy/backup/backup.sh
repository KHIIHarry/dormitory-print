#!/usr/bin/env bash
# Hourly incremental / daily encrypted backup of PostgreSQL + the private file
# volume.  It never reports success unless pg_dump, manifest verification,
# restic upload and (daily) retention all succeeded.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

MODE="hourly"
INIT_REPOSITORY=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --mode) MODE="${2:-}"; shift 2 ;;
        --init) INIT_REPOSITORY=1; shift ;;
        -h|--help)
            printf 'usage: %s [--mode hourly|daily] [--init]\n' "$0"
            exit 0
            ;;
        *) die 2 "usage: $0 [--mode hourly|daily] [--init]" ;;
    esac
done
case "$MODE" in hourly|daily) ;; *) die 2 "invalid --mode '$MODE'" ;; esac

WORK_DIR=""
cleanup() {
    local status=$?
    if [ "$status" -ne 0 ]; then
        log "BACKUP_FAILED mode=$MODE status=$status"
    fi
    if [ -n "$WORK_DIR" ] && [ -d "$WORK_DIR" ]; then
        rm -rf "$WORK_DIR"
    fi
}
trap cleanup EXIT

# Fail closed before touching the database when the remote target is absent.
require_restic
require_pg

STATE="$(state_dir)"
# Defaults to /storage in the backup container; tests may point it elsewhere.
STORAGE="$(storage_root)"
mkdir -p "$STATE" "$STORAGE" || die 5 "BACKUP_STATE_UNAVAILABLE: cannot create state/storage directories"
WORK_DIR="$(mktemp -d "$STATE/work.$(date -u +%Y%m%dT%H%M%SZ).XXXXXX")"
chmod 0700 "$WORK_DIR"

capacity_check

export RESTIC_CACHE_DIR="${RESTIC_CACHE_DIR:-$STATE/restic-cache}"
mkdir -p "$RESTIC_CACHE_DIR"

if ! restic cat config >/dev/null 2>&1; then
    if [ "$INIT_REPOSITORY" -eq 1 ]; then
        restic init || die 4 "BACKUP_FAILED: encrypted repository initialization failed"
    else
        die 3 "BACKUP_UNAVAILABLE: encrypted repository is unreachable or not initialized; run once with --init after configuring RESTIC_*"
    fi
fi

# Hold the storage maintenance lock for the DB snapshot + file scan + upload.
# A purge worker must take the same lock before unlinking private files.
acquire_maintenance_lock

DUMP="$WORK_DIR/db.dump"
MANIFEST="$WORK_DIR/files.manifest"
IMMUTABLE="$WORK_DIR/immutable.tsv"
EXPECTED="$WORK_DIR/expected.tsv"
METADATA="$WORK_DIR/metadata.json"

log "pg_dump start database=$PGDATABASE"
pg_dump --format=custom --compress=6 --no-owner --no-privileges --no-password \
    --file="$DUMP" || die 4 "BACKUP_FAILED: pg_dump failed"
[ -s "$DUMP" ] || die 4 "BACKUP_FAILED: pg_dump produced an empty file"
DUMP_SHA="$(sha256sum "$DUMP" | awk '{print $1}')"

# Expected private objects from the database snapshot.  Export rows are
# private CSVs with an expiry too, so they belong to the consistency check.
EXPECTED_QUERY="
SELECT 'ASSET', id, storage_key, sha256
  FROM assets WHERE deleted_at IS NULL AND storage_key <> ''
UNION ALL
SELECT 'ARTIFACT', ar.id, a.storage_key, ar.sha256
  FROM artifacts ar JOIN assets a ON a.id = ar.asset_id
UNION ALL
SELECT 'EXPORT', id, storage_key, ''
  FROM export_jobs WHERE storage_key IS NOT NULL AND storage_key <> ''
"
psql -X --no-password -A -t -F $'\t' -c "$EXPECTED_QUERY" > "$EXPECTED" \
    || die 4 "BACKUP_FAILED: could not list expected private files"

# Immutable final PDFs and their recorded hashes.
psql -X --no-password -A -t -F $'\t' -c "
SELECT ar.id, a.storage_key, ar.sha256
  FROM artifacts ar JOIN assets a ON a.id = ar.asset_id
 ORDER BY ar.id
" > "$IMMUTABLE" || die 4 "BACKUP_FAILED: could not list immutable PDF hashes"

# Validate every database-referenced object against the real file hash before
# it is uploaded.  A missing/mismatched immutable PDF aborts the backup.
while IFS=$'\t' read -r kind object_id key expected_sha; do
    [ -n "$key" ] || continue
    is_safe_storage_key "$key" || die 4 "BACKUP_INTEGRITY: unsafe storage key for $kind $object_id"
    path="$STORAGE/$key"
    [ -f "$path" ] || die 4 "BACKUP_INTEGRITY: missing file for $kind $object_id key=$key"
    if [ -n "$expected_sha" ]; then
        actual_sha="$(sha256sum "$path" | awk '{print $1}')"
        [ "$actual_sha" = "$expected_sha" ] \
            || die 4 "BACKUP_INTEGRITY: hash mismatch for $kind $object_id key=$key"
    fi
done < "$EXPECTED"

# Complete file manifest of the private volume (tmp and the coordination lock
# are deliberately excluded; everything committed is included).
: > "$MANIFEST"
while IFS= read -r -d '' file; do
    relative="${file#./}"
    digest="$(sha256sum "$STORAGE/$relative" | awk '{print $1}')"
    printf '%s  ./%s\n' "$digest" "$relative" >> "$MANIFEST"
done < <(cd "$STORAGE" && find . -type f \
    ! -path './tmp/*' ! -path './.coordination/*' \
    -print0 | LC_ALL=C sort -z)

python3 "$SCRIPT_DIR/verify_manifest.py" \
    --manifest "$MANIFEST" --root "$STORAGE" --immutable "$IMMUTABLE" \
    || die 4 "BACKUP_FAILED: local manifest/immutable PDF verification failed"

# Durably export deletion tombstones before the snapshot.  The journal is
# cumulative across runs and is uploaded with this snapshot for replay on
# restore.  Incomplete journals make restore refuse rather than revive data.
python3 "$SCRIPT_DIR/apply_tombstones.py" export \
    --output "$WORK_DIR/tombstones.jsonl" \
    --cumulative "$STATE/tombstones.jsonl" \
    || die 4 "BACKUP_FAILED: tombstone journal export failed"

created_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
manifest_sha="$(sha256sum "$MANIFEST" | awk '{print $1}')"
asset_count="$(wc -l < "$EXPECTED" | tr -d ' ')"
file_count="$(wc -l < "$MANIFEST" | tr -d ' ')"
cat > "$METADATA" <<EOF
{
  "version": 1,
  "mode": "$MODE",
  "createdAt": "$created_at",
  "database": "$PGDATABASE",
  "databaseDumpSha256": "$DUMP_SHA",
  "manifestSha256": "$manifest_sha",
  "referencedObjects": $asset_count,
  "files": $file_count,
  "masterKeyFingerprint": "${DORMPRINT_MASTER_KEY_FINGERPRINT:-}",
  "resticRepositoryConfigured": true
}
EOF

log "restic backup start mode=$MODE files=$file_count objects=$asset_count"
if ! restic backup \
    --json \
    --host "${BACKUP_HOSTNAME:-dormprint}" \
    --tag dormprint --tag "$MODE" \
    --exclude "$STORAGE/tmp" \
    --exclude "$STORAGE/.coordination" \
    --exclude "$WORK_DIR/restic.log" \
    "$STORAGE" "$WORK_DIR" > "$WORK_DIR/restic.log" 2>&1; then
    tail -n 40 "$WORK_DIR/restic.log" >&2 || true
    die 4 "BACKUP_FAILED: restic upload failed"
fi

snapshot_id="$(python3 - "$WORK_DIR/restic.log" <<'PY'
import json, re, sys
snapshot = ''
for line in open(sys.argv[1], encoding='utf-8'):
    try:
        event = json.loads(line)
    except ValueError:
        continue
    if event.get('message_type') == 'summary':
        candidate = event.get('snapshot_id', '')
        if isinstance(candidate, str) and re.fullmatch(r'[0-9a-f]{8,64}', candidate):
            snapshot = candidate
print(snapshot)
PY
)"
[ -n "$snapshot_id" ] || die 4 "BACKUP_FAILED: restic returned no snapshot id"
restic cat snapshot "$snapshot_id" >/dev/null \
    || die 4 "BACKUP_FAILED: cannot confirm the uploaded snapshot"

if [ "$MODE" = daily ]; then
    # Union policy: 168 hourly points (7 days) plus the latest point of each
    # of the last 30 days.  This is the only automatic rotation command.
    # Each dump has a unique work directory. Grouping by paths would retain a
    # separate last snapshot for every run forever, defeating the time policy.
    if ! restic forget --group-by host --tag dormprint \
        --keep-hourly 168 --keep-daily 30 --keep-last 1 --prune \
        > "$WORK_DIR/forget.log" 2>&1; then
        tail -n 40 "$WORK_DIR/forget.log" >&2 || true
        die 4 "BACKUP_FAILED: retention/prune failed"
    fi
fi

cat > "$STATE/last-success.json" <<EOF
{
  "epoch": $(date -u +%s),
  "createdAt": "$created_at",
  "mode": "$MODE",
  "snapshot": "$snapshot_id",
  "databaseDumpSha256": "$DUMP_SHA",
  "manifestSha256": "$manifest_sha",
  "files": $file_count,
  "referencedObjects": $asset_count
}
EOF
chmod 0600 "$STATE/last-success.json" 2>/dev/null || true

printf 'BACKUP_OK snapshot=%s mode=%s files=%s objects=%s\n' \
    "$snapshot_id" "$MODE" "$file_count" "$asset_count"
