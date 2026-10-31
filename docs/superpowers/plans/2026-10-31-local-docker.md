# Local Docker Implementation Plan

**Goal:** Run the full application in Docker at http://localhost:8080 with persistent isolated data.

**Architecture:** Add a local overlay to the existing production Compose topology. Build the frontend inside Docker and provide a repeatable shell entrypoint for secrets, builds, migrations and startup.

**Tech Stack:** Docker Compose >=2.24.4, Node 24, Nginx, existing Python/Celery/PostgreSQL/Redis services.

## Constraints

- Preserve production HTTPS settings and existing databases.
- Bind only 127.0.0.1:8080; persist random credentials and volumes across restarts.
- Never create default administrator passwords or seed demo data.

## Task 1: Local packaging and startup

- [x] Add `tests/deploy/local-docker.test.mjs`: render the actual merged Compose config through `bash deploy/local.sh config --format json` with a temporary environment file; verify loopback-only entry, HTTP cookie settings, isolated image/project names, no host frontend mount, and internal-only converters.
- [x] Run `node --test tests/deploy/local-docker.test.mjs` and observe missing-entrypoint failures.
- [x] Add `deploy/frontend.Dockerfile` and its context ignore file, `deploy/compose.local.yaml`, and `deploy/local.sh`. The script generates credentials once using OpenSSL, builds API/frontend images, waits for PostgreSQL/Redis, migrates, and starts application services.
- [x] Run the test again; also run the existing deployment checks with `backend/.venv/bin/pytest -q backend/tests/test_deployment.py`.
- [x] Document commands in README, including interactive administrator creation and data-preserving shutdown.

## Task 2: Runtime acceptance

- [x] Run `bash deploy/local.sh up` and inspect actual build/startup results.
- [x] Check `bash deploy/local.sh ps`, run `doctor`, fetch `/u/`, `/api/v1/health/ready`, and `/api/v1/public/config`; inspect cookie flags and Nginx config.
- [x] Inspect Celery worker registered queues and ping both workers, then review `git diff --check` and the final diff.

## Acceptance results (2026-10-31)

- `bash deploy/local.sh up`: exit 0; all seven long-running services healthy.
- `node --test tests/deploy/local-docker.test.mjs tests/deploy/local-docker.smoke.mjs`: 2 passed. The HTTP smoke test first reproduced `/u/` returning 403, then passed after correcting Nginx SPA fallback.
- `backend/.venv/bin/pytest -q backend/tests/test_deployment.py`: 14 passed, one existing Starlette/httpx deprecation warning.
- `doctor`: PostgreSQL, Redis and storage READY; LibreOffice and Ghostscript AVAILABLE; migration `1e66446aad7c`.
- Celery inspect: both workers returned pong; file worker consumes only files, business worker only business.
- `nginx -t` passed inside the read-only frontend image with tmpfs; copied configuration permissions are explicit.
- `git diff --check`: clean. Credentials are ignored by Git and retained in `deploy/.env.local` with mode 600.
- Administrator initialization awaits the operator's real email and interactively entered password; SMTP and off-site backups are not configured in this local environment.
