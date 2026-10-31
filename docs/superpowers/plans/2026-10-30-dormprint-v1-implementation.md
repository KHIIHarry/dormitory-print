# DormPrint V1 Implementation Plan

> **For agentic workers:** Execute with resident codex-dsh-desktop module workers. The human explicitly selected this execution mode. Follow test-driven-development; Codex performs integration/review/acceptance. No additional execution-choice approval is needed.

**Goal:** Deliver every approved item in the V1 scope and 33-module matrix with real persistence, conversion, permissions and three-client integration.

**Architecture:** Modular FastAPI service, synchronous SQLAlchemy PostgreSQL transactions, durable job/outbox and Celery dispatch, private disk storage. One shared identity/order/config system; each DSH module has disjoint ownership and imports the shared CONTRACT.md interfaces.

**Tech Stack:** Python3.12, FastAPI/Pydantic, SQLAlchemy/Alembic/PostgreSQL, Celery/Redis, LibreOffice, PyMuPDF/Pillow/HEIF, existing React/TypeScript/Vite. Resolved dependencies locked in backend/requirements.txt.

## Global constraints

- Scope: docs/superpowers/specs/2026-10-30-dormprint-v1-scope.md, all Q/R decisions final.
- No paid original content change. Actual print start and refund mutex; immutable original PDF/quote/history.
- Integer cents, strict camelCase DTO, owner/role/state checks, persistent idempotency, database constraints.
- Real conversion for PDF/DOC/DOCX/PPT/PPTX/XLS/XLSX/JPEG/PNG/WebP/HEIC; no mock production fallback.
- Paper accounting and booklet rules, manual printing result/packing, assigned courier may start guarded batch.
- Cash capacity at settlement; unmatched funds retained, full slots require agreed new slot, closed orders refund/new order.
- Business records not age-purged; explicit admin delete24h then guarded purge with anonymous totals/minimal deletion audit. Files and backups rotate on approved schedule, temporary secrets erased.
- Preserve existing uncommitted frontend work. Shared files belong to Codex. DSH writes only assigned modules/tests.

## Task 1: Shared persistence and secure request transaction (Codex)

Files: backend/app/{settings,db,models,core,config,main}.py; backend/tests/{conftest,test_core}.py; backend/alembic/; backend/requirements.*.

Interfaces: all precise classes/functions and snapshot conventions in backend/CONTRACT.md.

- [x] Write missing-app test and observe failure (`ModuleNotFoundError: app`).
- [x] Write actor/CSRF/envelope/version/idempotency and PostgreSQL active-assignment constraint tests.
- [x] Implement Settings, Base models, transaction/session dependencies, crypto, origin/CSRF, canonical JSON idempotency with encrypted cookie/result replay and authorized operation recovery.
- [x] Implement versioned config DTO/seed defaults without real prices/credentials and gated public capabilities.
- [x] Run `cd backend && .venv/bin/pytest tests/test_core.py -q`; expected all pass against PostgreSQL.
- [x] Generate/review initial Alembic migration and upgrade empty database; do not use application create_all.

Acceptance examples:

```python
def test_replay_precedes_version(client, api):
    first = api(client, '/admin/config/announcement', {'expectedVersion':1,'announcement':'营业'}, method='PATCH', key='stable-test-key')
    replay = api(client, '/admin/config/announcement', {'expectedVersion':1,'announcement':'营业'}, method='PATCH', key='stable-test-key')
    assert replay.status_code == first.status_code == 200
    assert replay.json()['data'] == first.json()['data']
```

## Task 2: Unified base identity and employee authorization (DSH auth)

Files: app/auth.py, app/auth_schemas.py, tests/test_auth.py. Uses shared Account/Principal/Staff/AuthSession/OneTimeToken/AccessGrant.

- [x] Write and observe failing tests for guest/account/staff separation, invalid grants and concurrent third-device rejection.
- [x] Implement declared auth entry points, all current customer auth/security/profile/link/import/access-link and existing-account staff-grant routes (old independent staff credentials retired), unified account sessions, current capability checks and last-superadmin protection.
- [x] Implement account export request/deletion application (approval execution belongs to maintenance), session/recovery limits and token consumption/expiry.
- [x] Run module PostgreSQL tests, report exact results and unresolved cross-module dependencies; Codex reviews permissions and frontend DTOs before acceptance.

```python
def test_third_device_rejected(account_login_client_factory):
    a,b,c = [account_login_client_factory() for _ in range(3)]
    assert a.status_code == b.status_code == 200
    assert c.status_code == 409
    assert c.json()['error']['code'] == 'DEVICE_LIMIT_REACHED'
```

## Task 3: Mail and transactional pricing/growth (DSH growth)

Files: app/growth.py, app/mail.py, module schemas, tests/test_growth.py, tests/test_mail.py. Interfaces build_quote/reserve_coupon/settle_coupon/release_coupon/on_registration_verified/qualify_referral/on_refund and mail processors as CONTRACT.md.

- [x] Start with failing integer-sheet volume/coupon/rounding/budget/reservation/refund tests and safe template tests.
- [x] Implement immutable full Quote, approved candidate tie-break, proportional allocation, rule snapshots, coupon budgets and protected RESERVED obligations.
- [x] Implement registration/invite qualification atomic at delivery; refunds recompute net eligibility and issue unique7day actual-discount compensation without altering old USED rows.
- [x] Implement real SMTP configuration/test/queue, TLS/DNS/SSRF protections, seven editable safe templates, secret encryption/destruction, customer admin and promotion routes.
- [x] Run real database tests and a local SMTP protocol fixture; distinguish local acceptance from production-provider delivery.

```python
def test_odd_duplex_three_copies_is_twelve_sheets():
    from app.pdf_engine import sheet_count
    assert sheet_count(7,3,'DUPLEX') == 12
```

## Task 4: Assets, resumable upload and immutable output (DSH media)

Files: app/media.py, app/pdf_engine.py, app/media_schemas.py, tests/test_media.py, tests/test_pdf_engine.py. Uses ASSET/PREPARATION persistent jobs.

- [x] Write real PDF/image source tests for7-page duplex3copies ->24PDFpages/12sheets, odd blanks per copy, EXIF/photo geometry, malformed/foreign assets, old preview manifest rejection inputs.
- [x] Implement streaming bounded private upload, actual-format verification, semantic multipart idempotency, chunk resume/merge/cancel and asset ownership/content Range guards.
- [x] Implement isolated actual Office/PDF/HEIC processing and strict Excel region/page options; lease-safe atomic file publication, cancellation and bounded retries.
- [x] Implement preparations/requote/adopt/withdraw/recovery, immutable artifacts/maps and exact frontend artifact adapters; call shared growth quote builder.
- [x] Implement sheet-aware nonbillable reprint artifact helper.
- [x] Run domain and PostgreSQL tests using actual files; production worker and parser limits are verified independently.

## Task 5: Printing and delivery modules (DSH production + DSH delivery)

Disjoint files: app/production.py + production_schemas.py + tests/test_production.py; app/delivery.py + delivery_schemas.py + tests/test_delivery.py.

- [x] Write failing guard tests for unpaid print, unresolved attempt/refund lock, uncompleted binding/packing, foreign courier proof, batch active uniqueness and all-or-nothing start.
- [x] Production implements actual-start, attempts/results/reprints, binding, checklist packing, dependency projection and whitelisted corrections using shared order/task models.
- [x] Delivery implements map/slot admin/public routes, preview/commit batch scheduling, courier DTOs, assigned start, current assignment proof and independent location/photo exceptions.
- [x] All batch changes lock deterministically; proof qualifies referral in the same database transaction; assigned courier never sees printable files/management finances.
- [x] Run PostgreSQL concurrency/authorization tests then review root order-view integration.

## Task 6: Orders, receipts, after-sales, cleanup and exports (Codex)

Files: app/commerce.py, app/after_sales.py, app/maintenance.py, app/jobs.py, app/exports.py, matching tests.

- [x] Write failing end-to-end transaction tests: all/current preview acks, conflicting revision/receipt, quote locking, slot full retains unmatched cash, auto-close mutex.
- [x] Implement order creation/revision/views/current task activation, owner/grant filtered reads, legal location updates and receipt capacity agreement.
- [x] Implement both customer/admin after-sales entry, responsibility gates, refund lock, original-discount suggestion and reasoned override, capped actual-cash refunds, zero settlement cancellation and growth callbacks.
- [x] Implement guarded admin delete/trash restore/purge, anonymized ledger/minimal deletion audit, account deletion approvals, approved file retention and secret destruction; no age-purge normal business rows.
- [x] Implement personal/promotion/order/receipt/refund CSV jobs with whitelists, caps, CSV formula escaping, private expiry/download.
- [x] Implement Celery durable job claim/lease/retry/cancel + scheduler for outbox, auto-close and approved maintenance. Run cross-module PostgreSQL tests with real worker processors.

## Task 7: Three-client integration and production packaging (Codex + resident frontend DSH)

Files: existing src/lib and relevant customer/admin/courier screens, new focused V1 screens, docs/openapi.yaml, scripts contract generation, src/lib/api-schema.d.ts, deploy/*, backend Dockerfile, deployment/QA docs.

- [x] Snapshot/read actual existing interfaces; upgrade strict OpenAPI response DTOs, operation recovery, chunk/job/draft/order/refund/approval/export/deletion contracts.
- [x] Wire existing config forms to per-section versions and approved decision capabilities; remove obsolete unresolved-only blocks, support Excel/HEIC/resume and new feature flows without losing prior UI changes.
- [x] Add real after-sales/refund/start/slot agreement/location approval/permissions/deletion/exports/pause/retention screens and DTO adapters. Correct assignment IDs and capability guards.
- [x] Run `npm test`, `npm run api:generate`, `npm run build`, backend full tests, Alembic empty upgrade, real HTTP upload->checkout->settlement->print->pack->delivery->refund plus real-browser upload->final preview->checkout and unified-account/QR verification.
- [x] Package locked dependency Docker images, same-origin reverse proxy, isolated file worker, persistent private volumes, hourly encrypted remote backups + daily restore points and recovery manifests. Validate Compose and restore to separate database/files.
- [x] Update each delivery-matrix row with code/test evidence. Record production SMTP/physical printing/GPS/camera and actual infrastructure still requiring real configuration; never claim these from fixtures.

No task is complete from a worker handoff alone. Codex inspects diff, runs relevant acceptance tests, reuses resident worker for corrections, and proceeds until the approved scope is delivered or a concrete external requirement prevents a particular real-environment validation.

## 本轮继续实施与增补任务（2026-10-30）

- [x] 把用户2—7增补写入四份原Prompt、定版增补稿和交付矩阵。
- [x] DSH integration：全后端基线测试；新增真实全模块HTTP上传→作业→准备→下单→收款→生产→配送→售后重做联调，并报告实际缺陷。所有应用共享模块仍由Codex审核修复。
- [x] DSH unified-ui：单PWA与本系统旧SW迁移；个人页基础账号登录和按权限入口；工作区返回用户端；保持客户归属/员工能力隔离及现有页面。限定main/layouts/account及PWA文件，先测试再实现。
- [x] Codex：N01—N07及F01—F03细节已确认并同步文档。
- [x] Codex/DSH：二维码采用本地实现；共照批量交付按最终边界新增严格DTO/原子事务/逐单凭证；对齐配送页面。
- [x] Codex：员工等级/超管资格、身份关联、三类优惠比较；整数分分摊和不可变quote；迁移及新报价撤权检查。
- [x] Codex与DSH限定模块：定额/比例工资策略、有效交付唯一账本、退款/纠错追加调整、人工发放记录与本人/管理员界面。
- [x] Codex：OpenAPI/生成器、全量测试/生产构建/真实联调和部署文档统一更新；保留实机/线上未验证项。

新增N/F规则全部已获明确答复；按定稿直接实施，不重复请求批准。

- [x] DSH unified-auth：普通账号关联Staff授权、同账号登录、最后超管和两设备规则；取消独立员工登录业务链。
- [x] DSH staff-benefits：6级整单优惠、超管免单、三选一报价/资格校验。
- [x] DSH payroll：送达唯一计件、策略锁定、原单/勾选追加费用净收款、退款追加调整、月度账本/人工发放。
- [x] DSH supplementary：追加费用/用途/是否计比例工资、即时生效、独立收退款、异议及客户/管理面板；Codex接订单汇总/导出。
- [x] DSH permissions：配送/文件/清理细分权限与共照批量交付原子服务；DSH courier-ui：对应真实配送界面。
- [x] DSH qr：本地二维码与私密链接保护；Codex接入订单、邀请及后台网址工具。

## 根集成进度补充（代码与本地验收）

- [x] 统一账号运行时：旧STAFF Cookie不再授权；客户页面移除员工越权上下文；显式超管、设备额度与即时撤权。
- [x] 原单追加费用/工资/员工等级/QR与主路由、导航、客户和后台订单详情接线。
- [x] 根实际Chrome+Vite代理+FastAPI+PostgreSQL的统一登录、授权、第二设备、第三设备拒绝及撤权测试，含桌面/390px截图；不拦截API。
- [x] 根实际HTTP/PG原闭环和本轮N/F扩展10项，作业状态修复后连同jobs共13项通过。
- [x] 全量前端267项的一次通过；后续路由/额外测试新增后须最终重复。
- [x] Alembic升级/回退包含旧员工记录且不自动绑定/提升超管，迁移无漂移检查。
- [x] 独立代码审查发现项修复；同一事务的备份锁/分阶段清理/跨线程提交和回滚验证。
- [x] 匿名财务导出与回收站金额对账、工资跨月调整最终回归。
- [x] 所有worker代码稳定后，根执行全后端、全前端、OpenAPI一致性、生产构建及真实浏览器最终验收。


## 2026-10-31 最终交付边界

以上勾选表示代码、接口及本机可执行检查已完成，不表示真实营业服务器已经上线；最终完整复跑结果以 `docs/qa/2026-10-31-v1-delivery.md` 为准。实际环境仍需提供服务器/域名/TLS、SMTP、异地备份目标与独立密钥、实体打印机和相机/GPS实测。未执行真实生产RPO/RTO或实体出纸验收，不将这些记作代码漏开发，也不声称已通过。

本轮额外修复：上传必须等账号及上传会话就绪；ASGI幂等重复请求使用非阻塞数据库尝试锁、限时异步等待和独立事务完成线程池；取消请求先等待正在执行的DB调用结束，再释放Session。实际Uvicorn并发回归覆盖17个相同操作及同时发生的不相关写入。

最终根验收：`DORMPRINT_RUN_BROWSER_TESTS=1 pytest -q` 546 passed（无跳过）；前端274 passed；桌面/移动工作区专项79 passed、1 skipped；生产构建、真实账号/二维码/上传下单、数据库迁移、当前代码镜像核验及本地加密恢复演练通过。详见上述根交付报告。
