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
    restic_lock_retry_seconds >/dev/null
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

# --- restic repository locks -------------------------------------------------
# These are restic's own encrypted lock objects, separate from the storage
# maintenance flock below.  Automation must never remove active locks:
# `restic unlock` only removes locks older than restic's stale threshold, and
# --remove-all is intentionally not used here.

restic_lock_retry_seconds() {
    local value="${BACKUP_RESTIC_LOCK_RETRY_SECONDS:-300}"
    case "$value" in
        ''|*[!0-9]*) die 2 "invalid BACKUP_RESTIC_LOCK_RETRY_SECONDS '$value' (0-3600)" ;;
    esac
    if [ "$value" -gt 3600 ]; then
        die 2 "BACKUP_RESTIC_LOCK_RETRY_SECONDS must be between 0 and 3600"
    fi
    printf '%s' "$value"
}

restic_lock_count() {
    local output
    output="$(restic list locks 2>/dev/null)" || return 1
    printf '%s\n' "$output" | awk 'NF { count++ } END { print count + 0 }'
}

# Remove only locks restic considers stale.  Active locks are preserved and
# reported; `restic unlock` intentionally returns success in that case.
restic_unlock_stale() {
    local context="$1"
    local before="unknown" after="unknown" removed="unknown" output=""
    before="$(restic_lock_count)" || before="unknown"
    log "RESTIC_LOCKS context=$context before=$before"
    if ! output="$(restic unlock 2>&1)"; then
        log "RESTIC_UNLOCK_FAILED context=$context"
        if [ -n "$output" ]; then
            printf '%s\n' "$output" | tail -n 20 >&2
        fi
        return 1
    fi
    if [ -n "$output" ]; then
        log "RESTIC_UNLOCK_OUTPUT context=$context $(printf '%s' "$output" | tr '\n' ' ')"
    fi
    after="$(restic_lock_count)" || after="unknown"
    if [ "$before" != "unknown" ] && [ "$after" != "unknown" ] \
        && [ "$after" -le "$before" ]; then
        removed=$((before - after))
    fi
    log "RESTIC_UNLOCK context=$context removed=$removed remaining=$after"
    return 0
}

restic_output_has_lock_error() {
    local logfile="$1"
    [ -f "$logfile" ] || return 1
    grep -Eqi 'repository is already locked|repo already locked|failed to lock repository|lock was created at' "$logfile"
}

# Run one restic operation.  A lock failure triggers one stale-lock cleanup and
# one bounded retry with --retry-lock; the first attempt does not wait.  The
# retry output goes to a separate file so an earlier lock error cannot mask a
# different failure on the second attempt.  Lock contention is returned as
# exit 11, matching restic's own lock-contention code.
run_restic_with_lock_recovery() {
    local operation="$1"
    local logfile="$2"
    shift 2
    local retry_seconds rc retry_log
    retry_seconds="$(restic_lock_retry_seconds)"

    : > "$logfile"
    rc=0
    restic "$@" >>"$logfile" 2>&1 || rc=$?
    if [ "$rc" -eq 0 ]; then
        return 0
    fi
    if [ "$rc" -ne 11 ] && ! restic_output_has_lock_error "$logfile"; then
        return "$rc"
    fi

    log "RESTIC_LOCK_CONTENTION operation=$operation exit=$rc; attempting stale-lock cleanup"
    if ! restic_unlock_stale "retry:$operation"; then
        log "RESTIC_LOCK_RECOVERY_FAILED operation=$operation; preserving exit=$rc"
        if [ "$rc" -eq 11 ]; then
            return 11
        fi
        return "$rc"
    fi

    retry_log="${logfile}.retry"
    : > "$retry_log"
    printf '\n# restic lock recovery retry operation=%s retry-lock=%ss\n' \
        "$operation" "$retry_seconds" >>"$logfile"
    rc=0
    restic "$@" --retry-lock "${retry_seconds}s" >>"$retry_log" 2>&1 || rc=$?
    cat "$retry_log" >>"$logfile"
    if [ "$rc" -eq 0 ]; then
        rm -f "$retry_log"
        log "RESTIC_LOCK_RECOVERED operation=$operation retry_lock=${retry_seconds}s"
        return 0
    fi
    if [ "$rc" -eq 11 ] || restic_output_has_lock_error "$retry_log"; then
        rm -f "$retry_log"
        log "RESTIC_LOCK_TIMEOUT operation=$operation retry_lock=${retry_seconds}s; active lock preserved"
        return 11
    fi
    rm -f "$retry_log"
    return "$rc"
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
