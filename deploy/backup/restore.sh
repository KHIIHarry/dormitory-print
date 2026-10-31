#!/usr/bin/env bash
# Restore an encrypted snapshot into a NEW database and a NEW empty private
# volume.  It refuses to overwrite the primary database/storage, verifies file
# and immutable-PDF hashes, replays deletion tombstones, then revokes every
# restored session/grant/token.  Never point this at production-in-place.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

SNAPSHOT="latest"
TARGET_DATABASE=""
TARGET_STORAGE=""
RESTORE_ROOT=""
EXTERNAL_JOURNAL=""
ALLOW_ANY_DATABASE=0
ALLOW_MISSING_TOMBSTONES=0
RECREATE=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --snapshot) SNAPSHOT="${2:-}"; shift 2 ;;
        --target-database) TARGET_DATABASE="${2:-}"; shift 2 ;;
        --target-storage) TARGET_STORAGE="${2:-}"; shift 2 ;;
        --restore-root) RESTORE_ROOT="${2:-}"; shift 2 ;;
        --journal) EXTERNAL_JOURNAL="${2:-}"; shift 2 ;;
        --allow-any-database) ALLOW_ANY_DATABASE=1; shift ;;
        --allow-missing-tombstones) ALLOW_MISSING_TOMBSTONES=1; shift ;;
        --recreate) RECREATE=1; shift ;;
        -h|--help)
            printf 'usage: %s --target-database NAME --target-storage DIR [--snapshot ID] [--restore-root DIR] [--journal FILE] [--recreate]\n' "$0"
            exit 0
            ;;
        *) die 2 "usage: $0 --target-database NAME --target-storage DIR [--snapshot ID]" ;;
    esac
done

[ -n "$TARGET_DATABASE" ] || die 2 "usage: --target-database is required"
[ -n "$TARGET_STORAGE" ] || die 2 "usage: --target-storage is required"
[[ "$TARGET_DATABASE" =~ ^[A-Za-z0-9_]+$ ]] \
    || die 2 "REFUSED: target database name must be alphanumeric/underscore"
[ "$TARGET_DATABASE" != "${PGDATABASE:-}" ] \
    || die 2 "REFUSED: target database equals the primary PGDATABASE; restore to a separate database"
if [ "$ALLOW_ANY_DATABASE" -ne 1 ]; then
    [[ "$TARGET_DATABASE" == dormprint_restore_* ]] \
        || die 2 "REFUSED: target must start with dormprint_restore_ unless --allow-any-database is given"
fi

TARGET_STORAGE_ABS="$(cd "$(dirname -- "$TARGET_STORAGE")" 2>/dev/null && pwd || printf '%s' "$(dirname -- "$TARGET_STORAGE")")/$(basename -- "$TARGET_STORAGE")"
PRIMARY_STORAGE="$(storage_root)"
PRIMARY_STORAGE_ABS="$(cd "$PRIMARY_STORAGE" 2>/dev/null && pwd || printf '%s' "$PRIMARY_STORAGE")"
[ "$TARGET_STORAGE_ABS" != "$PRIMARY_STORAGE_ABS" ] \
    || die 2 "REFUSED: target storage equals the primary private volume"

# The master key must already exist in the independent secret manager.  It is
# never read from the backup and never printed.
[ -n "${DORMPRINT_SECRET_KEY:-}" ] || die 2 "REFUSED: DORMPRINT_SECRET_KEY must be restored independently before restoring data"
[ "${#DORMPRINT_SECRET_KEY}" -ge 32 ] \
    || die 2 "REFUSED: DORMPRINT_SECRET_KEY must contain at least 32 characters"
if [ -n "${DORMPRINT_MASTER_KEY_FINGERPRINT:-}" ]; then
    actual_fingerprint="$(printf '%s' "$DORMPRINT_SECRET_KEY" | sha256sum | awk '{print $1}')"
    [ "$actual_fingerprint" = "${DORMPRINT_MASTER_KEY_FINGERPRINT,,}" ] \
        || die 2 "REFUSED: restored master key fingerprint does not match the independent record"
fi

require_restic
need_command pg_restore
need_command psql
need_command python3
need_command sha256sum

STATE="$(state_dir)"
mkdir -p "$STATE" "$TARGET_STORAGE" || die 5 "RESTORE_FAILED: cannot create restore directories"
if find "$TARGET_STORAGE" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
    die 2 "REFUSED: target storage is not empty; use a dedicated empty volume"
fi

ROOT="${RESTORE_ROOT:-$STATE/restore.$(date -u +%Y%m%dT%H%M%SZ)}"
mkdir -p "$ROOT"
log "restic restore start snapshot=$SNAPSHOT target-root=$ROOT"
restic restore "$SNAPSHOT" --target "$ROOT" \
    || die 4 "RESTORE_FAILED: restic restore failed"
DUMP="$(find "$ROOT" -type f -name db.dump -print | LC_ALL=C sort | tail -n 1)"
MANIFEST="$(find "$ROOT" -type f -name files.manifest -print | LC_ALL=C sort | tail -n 1)"
IMMUTABLE="$(find "$ROOT" -type f -name immutable.tsv -print | LC_ALL=C sort | tail -n 1)"
SOURCE_STORAGE="$(find "$ROOT" -type d -name storage -print | LC_ALL=C sort | head -n 1)"
[ -n "$DUMP" ] && [ -s "$DUMP" ] || die 4 "RESTORE_FAILED: snapshot has no database dump"
[ -n "$MANIFEST" ] || die 4 "RESTORE_FAILED: snapshot has no file manifest"
[ -n "$SOURCE_STORAGE" ] || die 4 "RESTORE_FAILED: snapshot has no private storage tree"

# Create the new database.  Never drop the primary, even with --recreate.
if [ "$TARGET_DATABASE" = "${PGDATABASE:-}" ]; then
    die 2 "REFUSED: target database equals the primary PGDATABASE"
fi
exists="$(psql -X --no-password -d postgres -A -t \
    -c "SELECT 1 FROM pg_database WHERE datname = '$TARGET_DATABASE'")"
if [ -n "$exists" ]; then
    if [ "$RECREATE" -eq 1 ]; then
        psql -X --no-password -d postgres -v ON_ERROR_STOP=1 \
            -c "DROP DATABASE \"$TARGET_DATABASE\"" \
            || die 4 "RESTORE_FAILED: could not recreate target database"
    else
        die 2 "REFUSED: target database already exists; use --recreate for a disposable restore database"
    fi
fi
psql -X --no-password -d postgres -v ON_ERROR_STOP=1 \
    -c "CREATE DATABASE \"$TARGET_DATABASE\"" \
    || die 4 "RESTORE_FAILED: could not create target database"

pg_restore --no-owner --no-privileges --exit-on-error \
    --dbname="$TARGET_DATABASE" "$DUMP" \
    || die 4 "RESTORE_FAILED: pg_restore failed"

# Restore private files into the new volume, then verify every manifest hash
# and every immutable final-PDF hash against both files and database rows.
( cd "$SOURCE_STORAGE" && find . -mindepth 1 -maxdepth 1 -print0 | \
    while IFS= read -r -d '' entry; do
        cp -a "$entry" "$TARGET_STORAGE/"
    done )
python3 "$SCRIPT_DIR/verify_manifest.py" \
    --manifest "$MANIFEST" --root "$TARGET_STORAGE" --immutable "$IMMUTABLE" \
    --database-name "$TARGET_DATABASE" \
    || die 4 "RESTORE_FAILED: restored private files do not match the snapshot manifest"

# Deletion tombstones: an external (newer) journal is merged with the journal
# inside the snapshot.  Incomplete entries abort the restore.
JOURNALS=()
if [ -n "$EXTERNAL_JOURNAL" ]; then
    [ -r "$EXTERNAL_JOURNAL" ] || die 4 "RESTORE_FAILED: external tombstone journal is not readable"
    JOURNALS+=("$EXTERNAL_JOURNAL")
fi
SNAPSHOT_JOURNAL="$(find "$ROOT" -type f -name tombstones.jsonl -print | LC_ALL=C sort | tail -n 1)"
if [ -n "$SNAPSHOT_JOURNAL" ]; then
    JOURNALS+=("$SNAPSHOT_JOURNAL")
fi
if [ "${#JOURNALS[@]}" -eq 0 ]; then
    if [ "$ALLOW_MISSING_TOMBSTONES" -ne 1 ]; then
        die 2 "REFUSED: no tombstone journal; refusing to expose data whose later deletions cannot be replayed"
    fi
    log "RESTORE_WARNING: no tombstone journal and --allow-missing-tombstones was supplied"
else
    MERGED="$ROOT/tombstones.merged.jsonl"
    : > "$MERGED"
    for journal in "${JOURNALS[@]}"; do
        cat "$journal" >> "$MERGED"
    done
    python3 "$SCRIPT_DIR/apply_tombstones.py" \
        --journal "$MERGED" --database "$TARGET_DATABASE" --storage "$TARGET_STORAGE" \
        || die 4 "RESTORE_FAILED: deletion tombstone replay failed"
fi

psql -X --no-password -v ON_ERROR_STOP=1 -d "$TARGET_DATABASE" \
    -f "$SCRIPT_DIR/post_restore.sql" \
    || die 4 "RESTORE_FAILED: post-restore revocation failed"

cat > "$STATE/last-restore.json" <<EOF
{
  "epoch": $(date -u +%s),
  "snapshot": "$SNAPSHOT",
  "database": "$TARGET_DATABASE",
  "storage": "$TARGET_STORAGE_ABS",
  "restoredAt": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
}
EOF
chmod 0600 "$STATE/last-restore.json" 2>/dev/null || true

printf 'RESTORE_OK snapshot=%s database=%s storage=%s\n' \
    "$SNAPSHOT" "$TARGET_DATABASE" "$TARGET_STORAGE_ABS"
