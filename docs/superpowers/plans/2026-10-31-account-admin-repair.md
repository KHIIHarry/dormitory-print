# Account/admin/courier repair plan

Nine user-reported issues are in scope. Codex owns frontend and integration review. Backend changes must run through resident codex-dsh-desktop workers, as explicitly requested by the user.

1. Trace current live contracts and reproduce menus/courier header/config/audit failures; preserve all unrelated deployment work.
2. Codex frontend ownership: account menu placement, courier/header dimensions, payroll form proportions, config response adapter, audit presentation/query controls, superadmin labels/benefit display, self-deletion recovery UI, invite URL prefill/share, QR-tool explanation and examples.
3. Resident DSH auth ownership: auth.py/auth_schemas.py, new account lifecycle helper, necessary shared model/migration and jobs entry point only, named auth/lifecycle regression tests. Implement bounded self-deletion with unfinished-order guard, seven-day sign-in recovery and uniqueness/bootstrap guards. Do not edit maintenance.py/config/growth/benefits/frontend/deploy.
4. Resident DSH config/audit ownership: config.py and config schemas, maintenance.py audit/query routes only (no lifecycle/deletion edits), named configuration/audit regression tests. Correct config loading and complete redacted audit DTO/filter pagination; coordinate shared schema requests with Codex.
5. Resident DSH benefits ownership: benefits.py/benefits_schemas.py, growth.py quote integration, named benefits regression tests. Correct the unique active superadmin's whole-order 100% reduction; preserve immutable quotes and manual zero settlement. Do not edit auth/models/config/maintenance/frontend/deploy.
6. Codex reviews all diffs and backend handoffs, refreshes generated contract after workers complete, verifies meaningful isolated PostgreSQL tests and frontend tests/build, and performs bounded desktop/mobile browser acceptance. No fake DTO filling or demo fallback on real API errors.
7. Document new decisions and remaining real-environment limitations accurately. Explain QR tools as URL-to-QR, separate from WeChat friend/payment codes.
