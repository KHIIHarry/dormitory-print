#!/usr/bin/env bash
# Long-running scheduler for the backup service.  It runs one backup at the
# top of every UTC hour and performs the retention pass on the configured
# daily hour.  Failures are logged and recorded; they are never converted into
# a successful backup record.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

STATE="$(state_dir)"
DAILY_HOUR="${BACKUP_DAILY_UTC_HOUR:-2}"
[[ "$DAILY_HOUR" =~ ^[0-9]{1,2}$ ]] || die 2 "invalid BACKUP_DAILY_UTC_HOUR"
DAILY_HOUR=$((10#$DAILY_HOUR))
[ "$DAILY_HOUR" -ge 0 ] && [ "$DAILY_HOUR" -le 23 ] || die 2 "BACKUP_DAILY_UTC_HOUR must be 0-23"
mkdir -p "$STATE"

record_attempt() {
    local status="$1"
    local mode="$2"
    local message="$3"
    printf '{"epoch": %s, "mode": "%s", "status": "%s", "message": "%s", "at": "%s"}\n' \
        "$(date -u +%s)" "$mode" "$status" "${message//\"/\'}" \
        "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" > "$STATE/last-attempt.json"
}

trap 'log "backup scheduler stopping"' TERM INT

log "backup scheduler started daily-utc-hour=$DAILY_HOUR"
while true; do
    now="$(date -u +%s)"
    wait_seconds=$(( 3600 - (now % 3600) + 5 ))
    log "next backup in ${wait_seconds}s"
    sleep "$wait_seconds"
    hour=$((10#$(date -u +%H)))
    if [ "$hour" -eq "$DAILY_HOUR" ]; then
        mode="daily"
    else
        mode="hourly"
    fi
    if bash "$SCRIPT_DIR/backup.sh" --mode "$mode"; then
        record_attempt "ok" "$mode" "backup completed"
    else
        status=$?
        record_attempt "failed" "$mode" "backup exited $status"
        log "BACKUP_FAILED mode=$mode status=$status (will retry next hour)"
    fi
done
