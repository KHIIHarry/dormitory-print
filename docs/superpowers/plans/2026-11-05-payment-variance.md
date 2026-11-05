# Payment variance implementation plan

> **For agentic workers:** Use executing-plans to implement the approved plan in this session. No new chats or unrelated costs/profit functionality.

**Goal:** Accept explicitly acknowledged positive over/underpayments and account for actual cash.

**Architecture:** Extend the existing ConfirmPayment request and settlement snapshot. Keep the immutable quote, Receipt/Payment cash ledgers, existing locking/idempotency, and fulfilment transaction; update the delivery guard to recognize accepted variance.

**Tech Stack:** React/TypeScript, FastAPI/Pydantic, PostgreSQL/SQLAlchemy, Vitest, Playwright.

## Global constraints

- User-approved spec: `docs/superpowers/specs/2026-11-05-payment-variance.md`.
- No zero-cash settlement of a positive payable; no change to discounts or original quote.
- Receipt reuse must equal the selected receipt's remaining net cash.
- No extra dependencies or unrelated refactors. Existing checkout and capability guards remain.

### Task 1: Settlement domain and API

Files: `src/growth/pricing.ts`, `src/growth/demo.ts`, `backend/app/commerce.py`, `backend/app/delivery.py`; tests in `tests/payment-variance.test.ts` and `backend/tests/test_payment_variance.py`.

- [x] Add and run failing tests for `settlementInput(1000, '9.80', false, true)`, positive overpayments, missing acceptance, unchanged zero-payable rules, and safe integer cents.
- [x] Add PostgreSQL API tests posting `paymentDifferenceAccepted: true` for `receivedCents: totalCents ± delta`; assert immutable total, actual Receipt/Payment amounts, allocated receipt, snapshot and audit, one capacity increment, and one payment on idempotent replay.
- [x] Extend `ConfirmPayment` with `payment_difference_accepted: StrictBool = False`. Before recording a new receipt, reject an unacknowledged positive variance; preserve existing invalid-type and receipt recovery behavior.
- [x] Accept `CASH_MANUAL` only when `total_cents > 0` and `received_cents > 0`; accept `ZERO_PAYABLE` only for both zero. Save `payableCents`, `paymentDifferenceCents`, `paymentDifferenceAccepted`, and admin-only `note` in settlement; save the same cash facts in Payment data and audit.
- [x] Extend frontend `settlementInput` with optional fourth boolean and `settlementWithReceipt` with optional last boolean. Include acceptance only for a difference. Require receipt net equal to entered cash, independently of payable.
- [x] Change delivery's payment guard to allow differing cash only with a matching accepted snapshot, retaining Payment/order consistency checks.
- [x] Run `npm test -- tests/payment-variance.test.ts tests/growth-pricing.test.ts tests/delivery-ui-regressions.test.ts tests/growth-demo.test.ts` and `.venv/bin/pytest -q tests/test_payment_variance.py` from backend.

### Task 2: UI, export and contract

Files: `src/admin/orders.tsx`, `src/lib/types.ts`, `backend/app/exports.py`, `src/v1/admin.tsx`, generated `docs/openapi.yaml` and `src/lib/api-schema.d.ts`; browser tests in `tests/e2e/payment-variance.spec.ts`.

- [x] Browser tests exercise the real order detail component: display difference, require checkbox, reset on amount change/reopen, submit actual cents/acceptance/optional note, and keep normal amount confirmation working.
- [x] Add difference state reset to every cash input/receipt selection/open handler. Display the signed difference and explicit final-settlement checkbox using existing Notice, Field and checkbox styles. Show settled payable/cash/difference/admin note in detail.
- [x] Add optional export fields `paymentDifferenceCents`, `paymentDifferenceAccepted`, `paymentNote` to existing order/finance exports, blank for unsettled/non-order rows; include difference in default frontend finance export.
- [x] Generate API schema with `npm run contract:generate`; update superseded payment instructions to link the approved change.
- [x] Run targeted PostgreSQL suites for settlement, fulfilment, finance exports, after-sales and payroll; run Vitest, production build, and browser tests on desktop/mobile. Inspect one batched screenshot round and run the Impeccable detector once on changed UI.
- [x] Review `git diff --check` and full diff; report actual validation, remaining deployment limits and the separate costs/profit design.

## Validation evidence

- `npm test`: 63 files, 374 tests passed.
- Targeted real PostgreSQL settlement, delivery, exports, after-sales and payroll suites: 189 passed (existing deprecation warning).
- `npm run test:e2e -- tests/e2e/payment-variance.spec.ts --workers=2`: 8 desktop/mobile checks passed.
- `npm run build`: passed; existing dependency annotation and chunk warnings remain.
- API contract regenerated from FastAPI. Impeccable detector: no findings; desktop/mobile screenshots inspected.
- Independent review finding about demo note privacy fixed and re-reviewed; no remaining findings.
- User authorized commit and push of the full settlement feature on `codex/payment-variance`; not deployed. The discussion-only costs/profit proposal is excluded from this feature commit.
- Pre-push verification: 374 Vitest tests, 16 PostgreSQL payment-variance tests, 8 desktop/mobile browser tests, and production build passed.
