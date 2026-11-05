# Simple cash finance implementation plan

> **For agentic workers:** Use subagent-driven-development for bounded frontend work; root owns financial correctness, shared files, schema/migrations and acceptance. Work on main per explicit user instruction. Do not create new branches/chats.

**Goal:** Implement the approved purchase-based consumables and cash balance page.
**Architecture:** OperatingExpense preserves procurement/other cash expenses, while existing Payment/Refund/SupplementaryMovement/WageSettlement facts supply income and payouts. Aggregate by Shanghai natural month or all-time and preserve anonymous daily cash facts before order purge.
**Tech Stack:** existing React/TypeScript/Vite, FastAPI/Pydantic/SQLAlchemy/PostgreSQL/Alembic, Vitest/Playwright.

## Global constraints

Approved spec: docs/superpowers/specs/2026-11-05-cost-profit-proposal.md. No stock valuation, per-order consumables costing, fabricated income or new dependencies. Integer cents. Finance capability and server guards required. User authorized main merge/branch removal; no new worktree or branch.

### Task 1: Financial API, persistence, archival integrity (root)

Files: backend/app/{models,finance,main,maintenance}.py, backend/alembic/versions/f4a6b8c0d012_cash_expenses.py, backend/tests/test_finance.py.

- [x] Write PostgreSQL API regressions for positive whole paper sheets, paid date, invalid amounts, permission isolation, monthly cash totals, wage payment, idempotent create and versioned void. Run `.venv/bin/pytest -q tests/test_finance.py`, observe missing-route/model failures.
- [x] Add OperatingExpense relational checks, indexed spent_on, audit fields and ACTIVE/VOIDED status; create matching Alembic migration from e3f5a7b9c011. Include finance router in default app.
- [x] Implement the four API operations from the spec, strict request DTOs, Shanghai filters, SQL sums, stable list pagination, Decimal unit price, audit and existing middleware transactions.
- [x] Implement order_cash_days(db, order_id) and preserve it as cashFlowByDay in _order_ledger; use only orderCount archive summaries to avoid double counting other deletion ledgers. Legacy archives are all-time only and explicitly counted for monthly warning.
- [x] Run new tests and targeted export/maintenance/payroll/payment regression suites; test migration using dedicated PostgreSQL schema, never production database.

### Task 2: UI and scoped typed client (frontend implementer)

Files: src/admin/finance.tsx, src/admin/finance.css, src/admin/finance-api.ts; tests/finance.test.ts. Root integrates routes/navigation/capability and demo boundary.

- [x] Read approved spec and incumbent controls. Add meaningful client validation tests before implementation, run red then green.
- [x] Implement AdminFinance page with month/cumulative tabs, month input, restrained cash overview, purchase quantity/average, stable list paging and category filter, concise procurement form and versioned void flow. Use growthRequest/useGrowthResource; correct stale fetch/refresh/error behavior, no fake fallback.
- [x] Mobile uses stacked record rows or responsive CSS grid with explicit labels; desktop uses aligned rows. Avoid table horizontal overflow, forced letter spacing or decorative dashboard clutter. Buttons >=44px, visible focus, safe area, native date/numeric input, readable 320px/200% zoom.
- [x] Parent will run real-component browser fixture tests for active states, correction, filtering, busy/error handling, long notes and large numbers at 320/375/390/768/1440 widths.

### Task 3: Integration and review (root)

Files: src/{main.tsx,admin/navigation.ts,admin/resources.ts,v1/admin-api.ts,growth/demo.ts}; generated docs/openapi.yaml and src/lib/api-schema.d.ts; tests/real-finance-driver.mjs + backend/tests/test_finance_browser.py and relevant navigation/capability tests.

- [x] Add route /manage/finance, navigation under system/team and finance.manage capability; ensure no unrelated store prefetch. Demo may return explicit empty initial financial facts with functional demo expense create/void; real mode always API.
- [x] Generate contract `npm run contract:generate`, run `npm test`, `npm run build` and targeted PostgreSQL regression suites.
- [x] Browser verification includes actual API persistence and one batched desktop/mobile screenshot round plus mechanical detector; fix all found material issues together, confirm once. Independent financial and UI review, resolve actionable findings.
- [x] Update README with scope/formula and migration requirement; record actual acceptance, inspect diff, retain main and report outcome. Push only within current user-authorized integration scope.

## Acceptance evidence

- `npm test`: 66 files, 381 tests passed; production build passed (existing dependency annotation/chunk warnings only).
- New real PostgreSQL finance tests cover create/void idempotency, amount/date/permission guards, Shanghai boundaries, archived facts, a deterministic concurrent-purge race, and fresh-schema Alembic upgrade/check.
- Live Chrome + actual FastAPI/PostgreSQL/Vite test passed: double-click prevents duplicate rows, persisted amounts, versioned void, month/category/pagination, offline form retention, 320/375/390/768/1440 widths, actual touch emulation, 44px controls and no split large amounts.
- Impeccable mechanical detector returned no findings. Desktop/mobile screenshot review resolved large-amount wrapping, excess form margins and mobile text sizing in one correction batch.
- Independent financial review findings resolved and re-reviewed; final screenshots have no material findings.
- Migration `f4a6b8c0d012` must run on backend upgrade; no production deployment performed.
- Final targeted backend regression: 138 passed; live finance browser acceptance: 1 passed. All test schemas were isolated and removed.
