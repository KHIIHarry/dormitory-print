# 配送时段与配送操作绑定校区：改动范围计划

> 执行时使用 executing-plans 逐项推进。本文件是已获准实施的方案与改动清单；代码已接通，最终验收见 docs/incremental/2026-11-03-campus-delivery.md。涉及实际存量的具体分配仍须先生成真实数据预检报告。

**Goal:** 新建配送时段必须选择一个校区，预约、结算、容量、封批、批次和后续配送统一遵守该校区归属。

**Architecture:** 每个 DeliverySlot 属于一个 Campus；同一日期、同一时间可以为不同校区创建独立时段。沿用现有 DeliveryGroup、DeliveryBatch、DeliveryAssignment，不新增第二套履约体系。所有写操作从数据库解析校区，前端筛选之外再执行服务端一致性校验。

**Tech Stack:** React / TypeScript、FastAPI / SQLAlchemy、PostgreSQL、Alembic、Vitest、Playwright。

## 当前代码事实

- `backend/app/models.py` 的 DeliverySlot 无 campus_id，capacity/occupied/sealed_at 均为全局时段字段。
- DeliveryGroup 已按 `(slot_id, campus_id)` 唯一；自动批次已有 campus_id，但时段本身不限定校区。
- `backend/app/delivery_plan.py` 的 apply_seal 会封住时段全部组和批次。
- `src/admin/logistics.tsx` 明确显示“同一时段各校区共享总容量”“冻结所有校区名单”。
- `src/customer/review.tsx` 切换校区会清理不匹配的楼栋，但不会因此清理旧时段；时段列表没有校区过滤。
- `backend/app/commerce.py` 校验楼栋和时段各自可用，没有校验二者属于同一校区；改楼栋也没有跨校区守卫。
- 手工建批只写 slot_id，不写 campus_id/group_id；`_campus_mismatch_blocker` 从已有订单推断校区，空批次无法据此约束第一笔订单。
- `src/lib/campus.ts` 的 campusesOfBatch 从成员订单推断校区，空批次也无法显示确定归属。
- 当前工作区存在其他未提交修改，执行本计划时必须保留，不能覆盖或一并提交。

## 方案选择

| 方案 | 影响与取舍 |
| --- | --- |
| **推荐：一个时段一个校区** | 符合“创建时选择校区”；现有容量与封批状态自然按校区独立，改动集中且容易验证。 |
| 一个共享时段关联多个校区，在 DeliveryGroup 管容量和封批 | 可以复用公共时间配置，但需把接单状态、容量、锁和接口身份全部下沉到组，改造更大。 |
| 只加页面校区筛选 | 数据仍共享容量、封批仍跨校区，接口仍可提交不匹配组合，不能满足本次要求。 |

## 建议统一的业务规则

1. 新时段必须指定有效校区；只有一个校区时可预选，但保存仍提交明确的 campusId。
2. 时段已绑定订单、时段建议、批次、变更记录或回收站引用后，不允许普通编辑更换校区；没有业务引用的空时段才允许调整，并检查版本。
3. 同名、同日期、同时间的不同校区时段互不影响；本次不额外禁止合法的时间重叠。
4. 容量按校区时段独立计数。继续仅在人工收款/零元结算时占用；拆批、并批、补送分配不重复占用。
5. 校区选定后，楼栋、时段、批次归属一致。前端不得把“缺少校区”默认解释成“所有校区适用”。
6. 封批对象是明确校区下的明确时段。操作入口可以先筛选校区；打开操作框后校区由时段锁定，不提供能与时段冲突的第二个独立选择。
7. 同校区改楼栋沿用现有地址复核；跨校区不能只改 locationId。待确认订单通过同时重选楼栋和时段的修订完成；已结算订单本轮先阻断直接跨校区，提示管理员协调，独立的跨校区迁单流程不隐含开发。
8. 校区 selectionEnabled 控制新的选点/预约，不自动取消、冻结已有履约。封批停止接单但不取消已有有效生产和配送。
9. 继续使用现有员工能力权限；本次校区绑定不隐含新增员工按校区授权制度。

## 改动模块与具体位置

### 1. 数据模型、迁移和存量识别

**位置：** `backend/app/models.py`、`backend/alembic/versions/` 新迁移、`backend/app/delivery_repair.py`。

- DeliverySlot 新增 campus_id 外键与索引；新建接口强制非空。
- 统一 DeliverySlot、DeliveryGroup、DeliveryBatch 的校区与时段关系。已绑定时段的组/批次必须校区一致；评估用 `(slot_id, campus_id)` 复合外键约束，并先处理历史例外。
- 复用现有只读配送检查入口，报告缺校区时段、跨校区共享时段、空批次归属缺失、订单/批次/时段冲突、占用数不一致及存量建议/回收站引用。
- 保留可空历史字段的过渡期；只有明确标记且停止新接单的历史记录可以暂未归属，不能直接在未知存量上加全表 NOT NULL。

### 2. 时段管理接口与接单状态

**位置：** `backend/app/delivery_schemas.py`、`backend/app/delivery.py`、`backend/app/delivery_plan.py`。

- SlotInput/SlotUpdate/SlotView 及快照增加 campusId；视图增加 campusName，处理缺失校区的历史展示。
- `/public/slots`、`/admin/slots` 支持校区筛选。未传筛选时仍可返回带明确校区的列表，兼容现有资源加载；公开列表排除校区不可新选或归属未处理的时段。
- 新建、编辑、停用、归档均检查真实校区与引用，审计包含校区身份。
- slot_acceptance/ensure_slot_accepting 增加缺校区与所属校区不可新预约的状态处理；区分新申请资格和既有履约资格，避免停用校区导致已有配送中断。
- 多个 `_slot_snapshot` / `_requested_slot_snapshot` 与统一 slot_snapshot 同步补齐，避免某个写入分支丢失校区信息。

### 3. 下单、修订、收款与容量

**位置：** `backend/app/commerce.py`、`backend/app/delivery_plan.py`、`backend/app/after_sales.py`。

- 抽出统一的校区匹配校验：楼栋校区必须等于时段校区；客户端 campusId 只用于请求表达，不能覆盖数据库归属。
- 提交订单、未付款修订、提出新时段、客户接受建议、人工结算、零元结算和已付款改期全部调用校验。
- 客户接受建议和实际结算时分别重验，防止接受后地址/时段发生变化。
- 归批前校验 slot/campus/group/batch/order 的一致性，在同一事务完成占容量与分配。
- 改期确认继续原子释放原时段、占用新时段；退款释放原确认时段；保留已到款待核对路径，校区冲突不能丢失实收凭证。
- 增加地址变更的同校区约束；地址改变时清除或作废不再匹配的待确认时段建议，重新提出建议需走原确认流程。
- 并发校验覆盖校区切换/停用、时段归档、收款、封批及改期；先梳理现有锁顺序，再采用一致顺序，不另立会与既有事务互锁的路径。

### 4. 封批预览、提交与自动截单

**位置：** `backend/app/delivery.py` 的 seal 接口、`backend/app/delivery_plan.py` 的 seal_preview/apply_seal/persist_seal_facts/batch_effective_sealed。

- 预览与提交计划持久化 slotId、campusId、版本和校区范围内的订单/批次/分配快照。
- 影响数、待确认申请、阻断项仅计算目标校区时段；即使没有收款或 DeliveryGroup 尚未创建，也能显示确定校区与待申请数。
- 提交检查计划所属操作者、有效期、校区、时段、成员与版本；预览后发生相关改变返回计划失效。
- 只冻结属于该校区时段的组和批次；发现旧数据跨校区引用时阻断并列出冲突，不静默封住其他校区。
- 自动截单、提前封批、批次发布和开始配送使用相同归属规则。东校区封批后，西校区同一时间的独立时段继续接单。

### 5. 手工/自动批次及配送执行

**位置：** `backend/app/delivery.py`、`backend/app/delivery_plan.py`、`backend/app/delivery_schemas.py`。

- 手工选择时段建批时，服务端派生 campus_id，并关联对应 DeliveryGroup；空批次创建后即有校区。
- 保留接口已有的自定义时间建批能力时，必须显式提供 campusId；它没有时段容量，不因本次改造新增容量占用。
- 自动归批、加单、移单、拆批、并批、延期、取消、删除均校验源/目标批次的显式归属；跨校区并批禁止。
- 拆批继承 slot/campus/group/封批事实；预览和真正执行使用同一守卫，避免空批次或旧数据绕过。
- 发布、开始配送、地址复核批准、单单送达及同楼栋共照批量送达均复核有效分配和归属；批准地址复核不能让跨校区冲突通过。
- 不扩大配送员权限，不改变现有位置、照片、装袋、生产和售后阻断。

### 6. 管理端界面

**位置：** `src/admin/logistics.tsx`、`src/admin/orders.tsx`、`src/admin/campus-filter.tsx`、`src/lib/campus.ts`。

- 时段页加校区筛选、校区列；新建必选校区，编辑显示是否允许更换及原因，容量文案改为该校区时段。
- 封批、停用、移除提示明确校区；封批弹窗不再展示“冻结所有校区名单”。
- 建批先选校区再选该校区时段；切换校区清除旧时段。空批次展示服务端明确校区，成员推断只用于历史异常诊断。
- 批次加单/移单/合并候选、订单详情 PendingSlotProposal、SlotProposals、PaidArrangementProposals 均按订单或批次校区过滤。
- 任意候选列表刷新或选择对象改变后，清除已经不合法的选中值；业务冲突显示具体原因。
- 管理页校区元数据应来自有权限的管理资源，避免只有新下单公开校区时无法编辑停用校区的历史业务。

### 7. 客户端、配送员端与公共状态

**位置：** `src/customer/review.tsx`、`src/customer/checkout.ts`、`src/customer/orders.tsx`、`src/courier/pages.tsx`、`src/lib/types.ts`、`src/lib/resources.ts`、`src/lib/store.tsx`、`src/lib/demo.ts`、`src/v1/customer.tsx`、`src/v1/admin-api.ts`。

- 下单流程为校区 → 楼栋 → 本校区时段；切校区清理不匹配的楼栋和时段，保留文件、打印设置和其他填写。
- 可预约判断、空状态、提交按钮、提交前复核和草稿恢复均使用同一校区范围，不能只过滤展示列表。
- 待确认修订进入页面时核对旧时段归属；跨校区须重选，服务端错误返回时保持工作进度。
- 订单改楼栋限制同校区；订单/配送计划/历史快照显示校区，时段从公开列表消失后仍能查看历史安排。
- Slot、Batch、SlotBrief、配送安排快照类型补齐字段，减少当前依赖 any 的遗漏；演示数据一并绑定校区。
- 资源刷新保留管理时段目录的权威来源；若引入按校区请求，缓存键必须含 campusId，防止切校区串列表。

### 8. 校区管理、售后、回收站、通知与导出

**位置：** `backend/app/campuses.py`、`backend/app/after_sales.py`、`backend/app/maintenance.py`、`backend/app/delivery_repair.py`、`backend/app/growth.py`、`backend/app/exports.py`。

- 删除校区先检查时段、组、批次及受保护历史引用，返回明确冲突，避免外键错误；停用校区保留已有配送记录。
- 售后补打/重送保持原校区，受控分配不重复收费或占容量；若原时段已结束，复用现有售后调度规则，不重新开放原时段。
- 回收站恢复批次保持校区/时段/组及封批状态，不自动恢复成员、不重新接单；缺失或不一致归属给出阻断。
- 永久清理与已有时段引用释放逻辑一起回归，保留有效外键与最小历史记录。
- 配送变更通知和导出增加校区身份/名称，使用事件快照保留历史语义；校区改名不能改写既有承诺。
- 工资和优惠算法沿用原规则，仅回归校区化安排不会导致重复结算、退款调整或丢失关联。
- 核查备份/恢复工具对新增列和迁移顺序的兼容，不能把旧共享时段恢复成可面向全部校区接单的时段。

### 9. 接口契约与文档

**位置：** 后端实际 DTO、`docs/openapi.yaml`、`src/lib/api-schema.d.ts`、配送闭环业务说明及必要 README 说明。

- 运行 `npm run contract:generate` 从真实后端生成契约，不手改生成文件。
- 记录新增必填字段、校区筛选参数、冲突错误、历史例外及共享容量规则的替代，避免旧文案继续指导实现。

## 存量数据处理顺序

1. **先只读预检。** 本次仅检查代码，尚未读取业务数据库，不能假定没有跨校区旧单。
2. **可唯一归属：** 从现有订单楼栋、显式批次、组及所有历史引用交叉验证；一致后回填。空时段不能盲目归给默认校区。
3. **多校区共享：** 按校区生成替代时段和原时段到新时段的映射。原记录停止新接单并保留历史；待履约引用、组、批次及未完成建议按已审核映射迁移，保持现有 assignment ID、执行状态和关联账目。
4. **容量须单独核对：** 不能给每个新时段复制原总容量。先按现有结算/改期/退款的容量规则核算各校区占用，与原 occupied 对账；再显式分配剩余容量。无法对账或未确认容量的新时段保持关闭。
5. **封批事实继承：** 原已封批或已截单，不因拆分而恢复接单；保留开放/截单时间及操作审计。存量执行中、已送达、退款、售后和垃圾桶逐类保留事实，不重新归批。
6. **快照与计划：** 不改写原申请、原结算和原通知的历史快照；记录迁移映射与迁移后的有效安排。旧封批/批次操作预览全部失效，待客户接受的建议核对目标归属后重建有效表达，不自动标为已接受。
7. **无法唯一判断：** 列出对象 ID 和冲突供处理，阻止新的错误关联；已有合法执行和历史查询继续可用。完成预检后再确定具体迁移批次与容量值。
8. **迁移验证：** 对升级前后订单、容量、账目、有效分配、组和批次逐项对账；真实 PostgreSQL 测升级与恢复。已产生新校区业务后回退优先恢复备份，不能靠直接删除 campus_id 丢失归属。

## 执行顺序与验收

- [x] 先补有业务意义的失败测试与存量预检，再做模型/迁移及统一归属校验。
- [x] 接通提交、修订、时段建议、收款、零元结算、改期、退款、自动归批和地址修改。
- [x] 接通手工建批、封批预览/提交、拆并批、发布、开始与交付；预览和提交校验保持一致。
- [x] 完成三端界面、状态恢复、演示数据和资源加载，生成接口契约。
- [x] 验证存量、回收站、售后、通知、导出及真实 PostgreSQL 并发；审查业务闭环后再安排发布。

最低验收用例：

- 东/西校区同一时间各建时段；各自满额、停用、封批、移除都不影响另一校区。
- 东校区楼栋配西校区时段，提交/修订/提出建议/接受建议/结算/改期全部拒绝，不增加容量或有效分配；实际到账按原待核对路径保留。
- 切换校区、草稿恢复、刷新及候选列表变化均清理失效时段；空状态准确限定当前校区。
- 东校区空批次不能加入西校区第一笔订单；拆批继承校区；跨校区移单/并批/地址复核批准均阻断。
- 封批预览后目标校区新增收款或成员变化必须重新预览；另一校区独立时段变化不使本计划失效。
- 收款与封批并发、同校区争最后一个名额、不同校区独立结算、改期与退款并发，均验证容量与唯一有效分配。
- 同校区改楼栋保留地址复核；跨校区待确认修订要求匹配时段，已结算直接改楼栋受阻。
- 单校区旧时段回填、多校区旧时段拆分、容量不明、旧建议、执行中、售后与垃圾桶升级后可查询且不会重复收款/归批。

验证命令（实际运行结果见配套验收说明；迁移检查在隔离 PostgreSQL schema 执行）：

```sh
# 仓库根目录
npm run contract:generate
npm test
npm run build
npm run test:e2e -- tests/e2e/delivery-ui-refresh.spec.ts

# backend 目录，真实 PostgreSQL 隔离 schema
.venv/bin/pytest -q tests/test_delivery.py tests/test_delivery_closed_loop.py tests/test_delivery_slot_race.py tests/test_delivery_batch_audit.py tests/test_delivery_repair.py tests/test_delivery_reacceptance.py tests/test_batch_delivery.py tests/test_campuses.py tests/test_commerce.py tests/test_slot_deleted_references.py tests/test_trash_batches.py tests/test_after_sales.py tests/test_migrations.py
.venv/bin/alembic check
```

新增校区隔离与迁移用例纳入对应测试文件；浏览器回归覆盖桌面和移动视口。数据库预检未完成前不执行实际存量写入。代码与测试已实施；尚未部署，未执行实际业务库迁移。
