# PURGE failure recovery

Scope: developer fixes requested on 2026-11-05. Production configuration and
production data cleanup remain with the production operator.

Follow-up DSH report: also include all sibling quotes for purged preparations,
and accept the actual anonymous finance ledger date/day-bucket schema in the
backup exporter and restore validator. Do not move or rewrite ledger facts.

Use the existing admin purge action as the explicit retry entry point, retaining
authorization, reason, version checks, idempotency and storage-before-row lock
ordering. Reuse the unique job instead of introducing duplicate queue records.
Automatic retries remain bounded; manual retries restart an exhausted budget.

- [x] Reproduce missing order delivery dependencies, empty-batch planning failure,
  rollback-only error handling and exhausted manual retries in PostgreSQL tests.
- [x] Delete order notices before delivery changes; initialize empty-batch blockers.
- [x] Roll back unusable worker sessions, preserve business error codes, normalize
  database errors and bound their retry/backoff policy.
- [x] Rearm failed jobs through the authorized manual purge action, preserving
  active worker leases and the unique business key.
- [x] Verify staging rollback preserves original files and removes quarantine.
  Capture batch history detaches and support delivery history in backup replay.
- [x] Run focused backend checks and independent code review.
- [x] Finish full backend check and rerun the timing-sensitive concurrency group.
- [x] Prepare the reviewed patch for main; production deployment stays with its operator.
- [x] Reproduce and fix sibling quotes for order revisions and standalone drafts,
  preserving preparations/quotes referenced by other orders or revisions.
- [x] Reproduce and fix strict ledger validation for ISO `*Date` fields and
  `cashFlowByDay` buckets, rejecting arbitrary strings/JSON and nonfinite values.
- [x] Verify actual purged order ledger export/replay and review the follow-up fixes.
- [x] Stabilize two existing same-day slot edit tests: their relative +30h
  fixture crosses midnight after 16:00 Shanghai time. Use 10:00–12:00 two days
  ahead in these two tests only, keeping all original assertions and production
  delivery rules. Their module rerun passed 22 tests and independent review.

Regression coverage must include real PostgreSQL constraints, file preservation
after rollback, successful retry, no retries beyond the automatic cap, business
blockers, active leases, stale versions, and replay of deletion/detach tombstones.

Verification: from `backend`, `.venv/bin/pytest tests/test_purge_recovery.py
tests/test_jobs.py tests/test_maintenance.py tests/test_trash_batches.py
tests/test_trash_after_sales.py tests/test_storage_coordination.py
tests/test_backup_tools.py -q --tb=short` passed all 126 tests, including 18 new
cases. An independent read-only review found the post-commit GC retry cap gap;
its regression failed before the fix and passed afterward. The final review has
no remaining critical or important findings. Existing dependency deprecation
warnings remain.

The broad `.venv/bin/pytest -q --tb=short` run finished with 895 passed,
4 opt-in browser tests skipped, and one timeout in
`test_concurrent_idempotent_http_requests_do_not_block_the_event_loop[True]`.
Its 0.9-second local HTTP deadline expired through the environment's proxy
transport. The first separate run passed all 3 cases, while a later repeat with
the proxy inherited reproduced the timeout. Running
`NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost .venv/bin/pytest
tests/test_http_concurrency.py -q --tb=short` outside the network sandbox passed
all 3 cases in 5.36 seconds. The broad run is not claimed as completely green;
the localhost proxy environment must be accounted for in its timing result.

Follow-up verification: `.venv/bin/pytest tests/test_purge_recovery.py
tests/test_backup_tools.py -q --tb=short` passed 51 tests, including real
PostgreSQL purge/export/replay, order revision and shared-preparation cases, and
strict validation failures. The two newly reported defects were reproduced
before implementation. Independent review found no critical or important issues.
The final source-code full run with localhost in `NO_PROXY`/`no_proxy` completed
with 923 passed, 4 opt-in browser tests skipped and the two same-day slot tests
failing on their original clock-dependent data. The fixture-only fix above was
made while that run was in progress; the complete delivery audit module then
passed all 22 tests. Production code is unchanged since that full run began.

Final focused verification: `.venv/bin/pytest tests/test_purge_recovery.py
tests/test_jobs.py tests/test_maintenance.py tests/test_trash_batches.py
tests/test_trash_after_sales.py tests/test_storage_coordination.py
tests/test_backup_tools.py tests/test_delivery_batch_audit.py -q --tb=short`
passed all 176 tests in 119.19 seconds. A post-full-run
`.venv/bin/pytest --lf -q --tb=short` also passed both previously failing tests.
`git diff HEAD --check` passed. No schema migration is needed.
