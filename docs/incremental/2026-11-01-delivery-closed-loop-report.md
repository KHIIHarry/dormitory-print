# 配送闭环增量实施报告（2026-11-01）

## 1. 范围与结论

本轮按 `docs/superpowers/plans/2026-11-01-delivery-closed-loop-dsh-prompt.md` 完成配送闭环的主链路：

- 配送时段新增「开放下单时间 / 截单时间」，统一服务端可预约判定（公开列表、下单校验、确认收款共用同一套状态）。
- 确认收款（正款或零元）在同一事务内确认时段、占用容量、自动归集「时段 + 校区」组和常规配送批次、创建唯一有效配送执行；失败不留下「已收款但未归批」。
- 管理员提前封批（预览 → 明确提交，必填原因、版本校验、不可重开）；封批前不能发布时段批次，截单后名单冻结。
- 新增「最新配送安排」：已结算订单可发起跨时段改期，客户在订单页明确接受/拒绝；接受时同事务迁移容量与执行；同日 ETA 调整只通知未送达订单，不把整批改为延后。
- `DELIVERY_UPDATED` 邮件扩展变更类型、原/新时间、公开原因、是否需要确认，并持久化 `DeliveryNotice` 发送意图与去重键；SMTP 结果回写通知状态。
- 管理端时段/批次、客户端可选时段与安排确认、配送员端封批/未发布状态均已接入。
- 新增 Alembic 迁移、OpenAPI 契约与前端生成类型。

未做/未验证的内容见第 7 节，不能把演示数据、Mock 或局部测试当作完整验收。

## 2. 变更文件

### 后端

| 文件 | 变更 |
| --- | --- |
| `backend/app/models.py` | 时段新增 open/cutoff/seal 列；订单新增 arrangement 版本；新增 `DeliveryGroup`、`DeliveryChange`、`DeliveryNotice`；批次新增 slot/campus/group/source/is_regular/seal 列与常规批次部分唯一索引 |
| `backend/app/delivery_plan.py`（新增，662 行） | 可预约状态机、封批预览/执行、自动归集组/常规批次/执行、最新安排计算、改期提案/接受/拒绝、变更事实 |
| `backend/app/delivery.py` | 时段视图/校验、封批预览与提交路由、批次发布封批守卫、批次显式归属、RESCHEDULE 改为 ETA 变更、拆分继承封批、封批/安排字段 |
| `backend/app/delivery_schemas.py` | 时段开放/截单校验、SlotView/BatchView 扩展、封批计划/结果 DTO |
| `backend/app/commerce.py` | 下单与结算接入统一可预约判定、结算自动归批、校区缺失/自动归批失败现金保护、安排 DTO 与三条路由、订单安排视图 |
| `backend/app/growth.py` | 安排通知、`DeliveryNotice` 持久化、邮件去重、时间格式化 |
| `backend/app/mail.py` / `mail_html.py` | `DELIVERY_UPDATED` 新变量、模板与样例、通知状态回写 |
| `backend/alembic/versions/b1f2c3d4e5a6_delivery_closed_loop.py`（新增，189 行） | 显式列/FK/检查/索引迁移；旧批次仅回填 `slot_id` |

### 前端

| 文件 | 变更 |
| --- | --- |
| `src/admin/logistics.tsx` | 时段列表/表单（开放、截单、状态、容量、封批）、封批预览、批次卡片与详情封批/自动归集提示、已结算订单改期提案入口 |
| `src/customer/review.tsx` | 只展示可选时段、截单时间、跨截单/失效选择提示 |
| `src/customer/orders.tsx` | 「最新配送安排」面板、接受/拒绝改期 |
| `src/courier/pages.tsx` | 自动归集、名单冻结、封批未发布状态 |
| `src/lib/types.ts` / `demo.ts` / `src/v1/admin-api.ts` | 时段与安排类型、演示数据 |
| `src/lib/api-schema.d.ts` / `docs/openapi.yaml` | 由真实 FastAPI 重新生成，未手改 |

### 测试

- 新增 `backend/tests/test_delivery_closed_loop.py`（578 行，13 个测试）：时段创建必须配置、状态视图、公开列表过滤、截单下单拒绝、自动归集/同校区分组、封批预览/提交、发布必须封批、时段窗口锁定、已结算改期确认与拒绝、ETA 变更保留批次状态、并发结算共享组/批次。
- 既有测试助手增加 `configure_slot` / `ensure_test_campus`，并为自动归批后的手工改派测试显式释放自动执行。

## 3. 关键实现说明

### 3.1 时段可预约状态

`delivery_plan.slot_acceptance` 的优先级：

`已归档 → 待补配置（缺开放/截单） → 已提前封批 → 人工停用 → 未到开放 → 已截单 → 已满 → 服务暂停 → 接单中`

公开列表只返回 `acceptanceStatus == 'OPEN'`；下单与结算复用同一函数，错误码分别为 `SLOT_ARCHIVED / SLOT_CONFIG_MISSING / SLOT_SEALED / SLOT_DISABLED / SLOT_NOT_OPEN / SLOT_CUTOFF / SLOT_FULL / SERVICE_CLOSED`。旧时段缺配置仍可完成既有订单履约，但停止新申请与自动归批（旧数据兼容路径）。

### 3.2 结算自动归批

- 先校验订单/报价/内容版本/金额/状态，再锁时段并做可预约判定。
- 配置齐全的时段：按订单地址的 `Location.campus_id` 找/建 `DeliveryGroup(slot_id, campus_id)`，再找/建 `is_regular=True` 的 `PLANNED` 无人批次，名称 `日期 时段名 · 校区名`，创建一条有效 `DeliveryAssignment`。容量、收款、结算、Payment、生产刷新、审计、通知意图在同一事务。
- 分组/批次/执行创建在保存点内，失败会整体回滚并抛出 `AUTO_BATCH_FAILED`；若已产生真实收款，回滚保存点后保留 `UNMATCHED` Receipt 并挂起可处理的业务异常，避免吞掉现金。
- 已配置时段但地址缺校区：在收款落账前拒绝 `CAMPUS_CONFIG_MISSING`；不会默认归入某个校区。
- 同一时段 + 校区共用一个组；不同校区各自成组；拆分产生的子批次继承时段/校区/组/封批事实，但不占用常规批次唯一槽位。

### 3.3 封批与发布

- 封批只由管理员发起：`POST /admin/slots/{id}/seal/preview` 生成 10 分钟计划（`BatchPlan`），列出各校区待确认申请、批次数与阻断；`POST /admin/slots/{id}/seal` 校验计划/版本/权限，写时段、组、批次 `sealed_*` 事实并审计。不可重开。
- 时段批次发布前必须满足「已截单或已提前封批」；否则 `BATCH_NOT_SEALED`。无时段的手工批次保持原有发布语义。
- 时段窗口/开放/截单在存在有效订单或批次后禁止直接编辑（`SLOT_TIME_LOCKED`）；批次窗口变更若有订单/执行，返回 `CHANGE_PREVIEW_REQUIRED`，必须走变更服务。

### 3.4 最新安排与改期

- `arrangement_for_order` 统一计算：版本、状态、当前有效时间、原申请/确认快照、原因、批次/执行、待确认提案、最近公开变更。
- 同日 ETA：批次 RESCHEDULE 只更新窗口并记录每个未送达订单的 `DeliveryChange(ETA_UPDATED)`，批次状态不变；已送达订单不写变更。
- 跨时段：管理员对已结算订单发起 `POST /admin/orders/{id}/arrangement-proposals`；客户 `POST /orders/{id}/arrangement-agreement` 后才迁移容量、确认时段、批次与执行；`arrangement-rejection` 保留原安排。接受/拒绝都需要版本，且不会重新收款、改价或改文件。

### 3.5 通知

- `DeliveryNotice` 以 `change.id + order.id + DELIVERY_UPDATED` 为去重键，状态覆盖 `PENDING / NO_EMAIL / ACCEPTED / FAILED / UNKNOWN`。
- 邮件落库在业务事务内，SMTP 失败不回滚安排；worker 的 SMTP 结果回写通知状态。
- 默认模板新增变更类型、原时间、最新安排、公开原因、是否需确认；纯文本与 HTML 均更新，模板测试通过。

### 3.6 API 变更

新增：

- `POST /admin/slots/{slot_id}/seal/preview`
- `POST /admin/slots/{slot_id}/seal`
- `POST /admin/orders/{order_id}/arrangement-proposals`
- `POST /orders/{order_id}/arrangement-agreement`
- `POST /orders/{order_id}/arrangement-rejection`

扩展字段：

- 时段：`openAt / cutoffAt / acceptanceStatus / selectable / blockers / pendingApplicationCount / batchCount / sealedAt / sealedByName / sealReason`
- 批次：`campusId / campusName / groupId / source / isRegular / sealedAt / sealedByName / sealReason / paidCount / preparedCount / unpreparedCount / unassignedCount / allowedActions`
- 订单：`arrangement`（原申请、确认、当前有效安排、待确认提案、最近变更）；`settlement.batchId / batchName`

错误码新增：`SLOT_CONFIG_REQUIRED / SLOT_CONFIG_MISSING / SLOT_NOT_OPEN / SLOT_CUTOFF / SLOT_SEALED / SLOT_TIME_LOCKED / BATCH_NOT_SEALED / CHANGE_PREVIEW_REQUIRED / CAMPUS_CONFIG_MISSING / AUTO_BATCH_FAILED / SEAL_BLOCKED / RESCHEDULE_FAILED / NO_PENDING_ARRANGEMENT`。

## 4. 迁移影响

- 新 head：`b1f2c3d4e5a6`（`down_revision = 7d3c9a1e5b20`）。
- 新增可空列，旧时段不会因迁移被强制补时间；旧批次仅回填 `slot_id`（来自 `data.slotId`），不建组、不改派、不发邮件。
- 常规批次唯一索引只约束 `is_regular = true AND deleted_at IS NULL`，不会阻止显式拆分/补送。
- 在一次性数据库 `dormprint_migration_check` 上验证：`alembic upgrade head` 成功；`alembic check` 输出 `No new upgrade operations detected`；`alembic downgrade -1` 与再次 `upgrade head` 成功；随后删除该库。
- 生产部署未执行；真实库未改动。

## 5. 验证记录（实际命令与结果）

| 命令 | 结果 |
| --- | --- |
| `git status --short` | 23 个已跟踪文件修改，5 个新增（计划文档、`delivery_plan.py`、迁移、闭环测试、本报告） |
| `npm run contract:generate` | 退出码 0；`204 paths, 231 operations, 407 schemas`；17 个前端别名；`api-schema.d.ts` 重新生成 |
| `npm test` | 58 个测试文件 / **330 passed**，4.43s |
| `npm run build` | 成功，`✓ built in 2.26s` |
| `git diff --check` | 无输出（无空白错误） |
| `cd backend && .venv/bin/pytest -q` | **662 passed, 2 skipped, 1 warning**，319.31s（跳过项为需显式开启的真实浏览器测试） |
| `cd backend && .venv/bin/alembic heads` | `b1f2c3d4e5a6 (head)` |
| 迁移一次性库校验 | upgrade / check / downgrade / re-upgrade 全部成功 |

浏览器（已完成根因修复）：

| 项目 | 结果 |
| --- | --- |
| 根因 1 | `@playwright/test 1.64.1` 需要 chromium / chromium-headless-shell `1248`，本机缓存只有 `1228`；首次运行实际用了旧缓存，复跑才暴露 |
| 根因 2 | 默认 `playwright.config.ts` 把需要 5175/5177 专用服务的 `motion.spec.ts` 一起跑，导致 motion 用例连错端口；motion 配置又继承了 `testIgnore`，单独跑反而 0 用例 |
| 修复 | 用 `npm run test:e2e:install` 下载 1248 到工作区 `.playwright-browsers`（已加入 `.gitignore`）；`test:e2e` 固定 `PLAYWRIGHT_BROWSERS_PATH`；默认配置排除 motion，motion 配置显式 `testIgnore: []`；新增 `test:e2e:motion` 脚本 |
| 定向验证 | `npm run test:e2e -- tests/e2e/frontend.spec.ts -g "homepage contains\|document order reaches"` = **4 passed**（desktop + mobile） |
| motion 验证 | 桌面端 conditional modal 用例 **1 passed**；移动端同一用例断言失败（closing 状态计数 0），不是浏览器启动问题 |
| `frontend.spec.ts` 全量 | **6 passed / 5 failed**；5 个失败均依赖已移除的管理员/配送员演示登录（`/manage/login` 已重定向到统一客户登录），不是浏览器启动失败 |
| 全量 e2e | 仍未全绿：除上述登录入口外，还有 review-config 测试桩触发全局错误边界、移动端 motion closing 状态断言失败；这些是测试与产品现状不一致，不是浏览器版本抖动 |
| 响应式截图 | 375 / 390 / 768 / 1280 订单列表与 390 订单详情，见下方链接 |

截图：

- [订单列表 375](../qa/delivery-orders-375.png)
- [订单列表 390](../qa/delivery-orders-390.png)
- [订单列表 768](../qa/delivery-orders-768.png)
- [订单列表 1280](../qa/delivery-orders-1280.png)
- [订单详情 390](../qa/delivery-order-detail-390.png)

Playwright 使用方式：

```bash
npm run test:e2e:install   # 首次或依赖升级后，安装匹配的 chromium 1248 到 .playwright-browsers（已安装时幂等跳过）
npm run test:e2e           # 默认 UI 套件（自动排除 motion）
npm run test:e2e:motion    # motion 专用套件（自行启动 5175/5177 服务）
```

## 6. 已实现但未直接验证

以下代码路径已实现，但本轮没有独立自动化或真实环境证据，不能列为已验收：

- 结算保存点失败后 `AUTO_BATCH_FAILED` 保留 `UNMATCHED` 收款的管理端提示与人工处理界面。
- 并发抢最后一个容量时「仅一人成功、失败方现金保留」的线程级测试（当前并发测试容量为 4，验证同组同批次）。
- 封批与结算同时发生的串行化测试。
- 旧数据「只读修复预览/显式 apply」管理流程（迁移只做安全回填）。
- SMTP 真实投递；只验证了模板、入队、去重回写代码与单元测试。
- 真实后端 + 浏览器的完整「下单 → 收款 → 自动归批 → 封批 → 发布 → 开始 → 送达 → 改期邮件」链路；后端链路由 pytest 覆盖，浏览器只验证了演示数据 UI。

## 7. 已知限制与后续建议

1. 管理端/配送员端本轮只做了代码与构建验证，未取得管理端截图：当前演示入口只保留客户端「体验演示账号」，没有可用的管理员演示登录（旧 e2e 中的「进入管理员演示」按钮已不存在）。
2. Playwright 浏览器版本已统一到 1248，定向客户链路与桌面 motion 用例可复跑；但全量 e2e 仍因「管理员演示」入口移除、review-config 测试桩错误边界、移动端 motion 断言失败而未全绿，不能宣称浏览器全量通过。
3. 前端没有为本轮新增面板补充组件测试，仍为既有 330 个测试。
4. 旧时段（缺开放/截单）会显示 `CONFIG_MISSING`，但尚未提供批量修复入口；管理员需逐条编辑时段补齐规则。
5. 封批只支持整时段 × 全部校区；按校区单独封批不在本轮范围。
6. `backend/AGENTS.md` / `CONTRACT.md` 历史上把共享模型、迁移等文件划给 Codex；本轮用户计划明确要求交付完整闭环，因此修改了这些共享文件。最终合并前需由项目负责人复核归属与迁移顺序。

## 8. 结论

后端业务主链路、迁移、契约、三端主要 UI 与自动化测试已落地，后端全量 662 通过、前端 330 通过、构建与契约生成成功。浏览器完整链路、真实 SMTP、最后容量/封批并发与旧数据修复流程仍未达到可宣称「全部验收」的程度，按第 6、7 节如实记录。
