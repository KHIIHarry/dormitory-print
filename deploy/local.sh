#!/usr/bin/env bash
set -euo pipefail

deploy_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
local_env_file="${DORMPRINT_LOCAL_ENV_FILE:-$deploy_dir/.env.local}"

if [[ ! -f "$local_env_file" ]]; then
  (
    umask 077
    set -o noclobber
    {
      printf 'POSTGRES_PASSWORD=%s\n' "$(openssl rand -hex 24)"
      printf 'REDIS_PASSWORD=%s\n' "$(openssl rand -hex 24)"
      printf 'DORMPRINT_SECRET_KEY=%s\n' "$(openssl rand -hex 48)"
      printf 'DORMPRINT_PUBLIC_ORIGIN=http://localhost:8080\n'
      printf 'DORMPRINT_PUBLIC_HOST=localhost\n'
    } > "$local_env_file"
  )
  printf 'Created local credentials: %s\n' "$local_env_file" >&2
fi
chmod 600 "$local_env_file"

compose=(docker compose --project-name dormprint-local
  --env-file "$local_env_file"
  -f "$deploy_dir/compose.yaml" -f "$deploy_dir/compose.local.yaml")

action="${1:-up}"
if [[ $# -gt 0 ]]; then shift; fi
case "$action" in
  up)
    docker info > /dev/null
    "${compose[@]}" build api proxy
    "${compose[@]}" up -d --wait postgres redis
    "${compose[@]}" run --rm --no-deps migrate
    "${compose[@]}" up -d --wait api file-worker business-worker beat proxy
    "${compose[@]}" run --rm --no-deps doctor
    printf '\nDormPrint is running at http://localhost:8080\n'
    ;;
  init-admin)
    "${compose[@]}" run --rm --no-deps init-admin python -m app.cli init-admin "$@"
    ;;
  doctor)
    "${compose[@]}" run --rm --no-deps doctor "$@"
    ;;
  *)
    "${compose[@]}" "$action" "$@"
    ;;
esac
