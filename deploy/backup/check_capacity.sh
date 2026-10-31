#!/usr/bin/env bash
# Private-volume capacity alert.  This is a real command with a non-zero exit
# status, intended to be wired to the deployment monitoring/alerting system.
# It never claims that an external alert was delivered by itself.
set -Eeuo pipefail

path="${1:-${DORMPRINT_STORAGE_ROOT:-/storage}}"
threshold="${DORMPRINT_CAPACITY_ALERT_PERCENT:-85}"
[[ "$threshold" =~ ^[0-9]{1,3}$ ]] || {
    echo "CAPACITY_INVALID: DORMPRINT_CAPACITY_ALERT_PERCENT must be an integer" >&2
    exit 2
}
[ -d "$path" ] || {
    echo "CAPACITY_UNAVAILABLE: storage path does not exist: $path" >&2
    exit 3
}
used="$(df -P "$path" | awk 'NR==2 {gsub(/%/, "", $5); print $5}')"
available="$(df -P "$path" | awk 'NR==2 {print $4}')"
[[ "$used" =~ ^[0-9]+$ ]] || {
    echo "CAPACITY_UNAVAILABLE: cannot read filesystem usage for $path" >&2
    exit 3
}
if [ "$used" -ge "$threshold" ]; then
    echo "CAPACITY_ALERT path=$path used=${used}% threshold=${threshold}% available_kb=$available" >&2
    exit 10
fi
echo "CAPACITY_OK path=$path used=${used}% threshold=${threshold}% available_kb=$available"
