#!/usr/bin/env bash
# Shared helpers for DormPrint backup/restore scripts.  Sourced, not executed.

set -o pipefail

log() {
    printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >&2
}

die() {
    local code="$1"
    shift
    printf '%s: %s\n' "$code" "$*" >&2
    exit "$code"
}

need_command() {
    command -v "$1" >/dev/null 2>&1 || die 3 "BACKUP_UNAVAILABLE: required command '$1' is not installed"
}

storage_root() {
    printf '%s' "${DORMPRINT_STORAGE_ROOT:-/storage}"
}

state_dir() {
    printf '%s' "${BACKUP_STATE_DIR:-/state}"
}

# Fail before doing any work when the encrypted off-site target is not real.
require_restic() {
    require_restic_config
    need_command restic
}

require_restic_config() {
    local repository="${RESTIC_REPOSITORY:-}"
    case "$repository" in
        ""|*replace-*|*example.com*|*example.invalid*)
            die 3 "BACKUP_UNAVAILABLE: RESTIC_REPOSITORY is not configured with a real encrypted off-site repository"
            ;;
    esac
    if [ -n "${RESTIC_PASSWORD_FILE:-}" ]; then
        [ -r "$RESTIC_PASSWORD_FILE" ] || die 3 "BACKUP_UNAVAILABLE: RESTIC_PASSWORD_FILE is not readable"
    elif [ -n "${RESTIC_PASSWORD:-}" ]; then
        case "$RESTIC_PASSWORD" in
            *replace-*) die 3 "BACKUP_UNAVAILABLE: RESTIC_PASSWORD still contains the placeholder" ;;
        esac
    else
        die 3 "BACKUP_UNAVAILABLE: set RESTIC_PASSWORD_FILE or RESTIC_PASSWORD"
    fi
}

require_pg() {
    need_command pg_dump
    need_command psql
    [ -n "${PGHOST:-}" ] || die 3 "BACKUP_UNAVAILABLE: PGHOST is not configured"
    [ -n "${PGDATABASE:-}" ] || die 3 "BACKUP_UNAVAILABLE: PGDATABASE is not configured"
    [ -n "${PGUSER:-}" ] || die 3 "BACKUP_UNAVAILABLE: PGUSER is not configured"
    [ -n "${PGPASSWORD:-}" ] || die 3 "BACKUP_UNAVAILABLE: PGPASSWORD is not configured"
}

# The storage-maintenance coordination lock.  The backup holds this shared
# file lock for the whole database + file scan + restic upload; maintenance
# purge must take the same lock before unlinking files or deleting rows.
acquire_maintenance_lock() {
    local root lockfile wait_seconds
    root="$(storage_root)"
    mkdir -p "$root/.coordination" || die 5 "BACKUP_LOCK_FAILED: cannot create coordination directory"
    lockfile="$root/.coordination/maintenance.lock"
    exec 9>"$lockfile" || die 5 "BACKUP_LOCK_FAILED: cannot open $lockfile"
    wait_seconds="${BACKUP_LOCK_TIMEOUT_SECONDS:-300}"
    command -v flock >/dev/null 2>&1 || die 3 "BACKUP_UNAVAILABLE: flock is not installed"
    flock -w "$wait_seconds" 9 || die 5 "BACKUP_BUSY: maintenance/purge holds the storage lock; retry later"
    log "maintenance-lock acquired file=$lockfile"
}

capacity_check() {
    local script="${SCRIPT_DIR:-$(dirname "$0")}/check_capacity.sh"
    if [ -x "$script" ] || [ -f "$script" ]; then
        if ! bash "$script"; then
            log "CAPACITY_ALERT detected; backup will still attempt to protect data"
        fi
    fi
}

is_safe_storage_key() {
    case "$1" in
        ""|/*|*..*|*\\*) return 1 ;;
        *) return 0 ;;
    esac
}
