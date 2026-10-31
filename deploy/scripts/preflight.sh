#!/usr/bin/env bash
# Preflight checks before exposing a production Compose project.  It checks
# deployment facts that cannot be inferred and never prints secret values.
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
failures=0
fail() { printf 'PREFLIGHT_FAIL %s\n' "$*" >&2; failures=$((failures + 1)); }
ok() { printf 'PREFLIGHT_OK %s\n' "$*"; }

secret="${DORMPRINT_SECRET_KEY:-}"
[ "${#secret}" -ge 32 ] || fail "DORMPRINT_SECRET_KEY must contain at least 32 characters"
origin="${DORMPRINT_PUBLIC_ORIGIN:-}"
[[ "$origin" == https://* ]] || fail "DORMPRINT_PUBLIC_ORIGIN must be an https:// origin"
[ -n "${POSTGRES_PASSWORD:-}" ] || fail "POSTGRES_PASSWORD is required"
[ -n "${REDIS_PASSWORD:-}" ] || fail "REDIS_PASSWORD is required"
[ "${POSTGRES_PASSWORD:-}" != "local-test-only" ] || fail "POSTGRES_PASSWORD must not be the local test password"
[ "${REDIS_PASSWORD:-}" != "local-test-only" ] || fail "REDIS_PASSWORD must not be the local test password"
[ -n "${DORMPRINT_MAIL_ALLOWED_HOSTS:-}" ] || printf 'PREFLIGHT_WARN SMTP host allowlist is empty; email registration stays unavailable\n'

dist="$ROOT/dist/index.html"
[ -f "$dist" ] || fail "dist/index.html is missing; run npm ci && npm run build before starting the proxy"
[ -f "$ROOT/deploy/nginx/templates/default.conf.template" ] || fail "nginx template is missing"
[ -x "$ROOT/deploy/backup/backup.sh" ] || [ -f "$ROOT/deploy/backup/backup.sh" ] || fail "backup tooling is missing"

if [ -z "${RESTIC_REPOSITORY:-}" ] || [ -z "${RESTIC_PASSWORD:-}${RESTIC_PASSWORD_FILE:-}" ]; then
    printf 'PREFLIGHT_WARN encrypted off-site backup is not configured; backups will report BACKUP_UNAVAILABLE\n'
else
    command -v restic >/dev/null 2>&1 || printf 'PREFLIGHT_WARN restic is only installed inside the backup image\n'
fi

if [ -n "${DORMPRINT_SECRET_KEY:-}" ] && [ -n "${DORMPRINT_MASTER_KEY_FINGERPRINT:-}" ]; then
    actual="$(printf '%s' "$DORMPRINT_SECRET_KEY" | sha256sum | awk '{print $1}')"
    [ "$actual" = "${DORMPRINT_MASTER_KEY_FINGERPRINT,,}" ] \
        || fail "DORMPRINT_MASTER_KEY_FINGERPRINT does not match the configured secret"
fi

if [ "$failures" -ne 0 ]; then
    printf 'PREFLIGHT_FAILED count=%s\n' "$failures" >&2
    exit 1
fi
printf 'PREFLIGHT_PASS\n'
