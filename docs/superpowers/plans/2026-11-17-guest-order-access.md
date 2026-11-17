# 游客订单访问、账号归属与提取码组件执行方案

**目标**　游客订单最多保有一个有效只读链接，默认 14 天，可关闭或在 1–14 天内重置。关联已验证账号后，订单在「我的订单」长期可查，旧游客入口同时收回。各后台工作页面复用同一个提取码组件。

**架构**　在现有 FastAPI、PostgreSQL 和 Alembic 中统一订单归属与游客访问判断，复用订单、授权、会话、审计和幂等机制。React 和 TypeScript 按服务端状态渲染，Vitest、Playwright 与 PostgreSQL 集成测试分层验收。按下列任务顺序执行，每项先复现失败，再实现并复测。

## 执行状态（2026-11-18）

本地前后端实现及回归已完成。下文勾选表示本地实现、隔离 PostgreSQL 或浏览器验证完成，不表示已部署生产。生产脱敏快照预演、生产同步发布和物理设备验收保留为未勾选项。本轮迁移使用隔离 PostgreSQL 的历史结构样本和真实 Alembic 链验证，没有读取或修改生产数据。

## 业务验收口径

| 场景 | 客户侧 | 管理端 |
| --- | --- | --- |
| 登录下单 | 新订单直接进入所有者的「我的订单」 | 「客户信息」，小人图标可识别登录下单 |
| 游客下单、链接有效 | 唯一链接可只读查看。原下单设备在有效期内可按原业务状态操作或确认关联。本机订单和链接页遵守同一个截止时间 | 「订单专属链接」显示开放时间、到期时间与倒计时，可打开、关闭或重置 |
| 游客链接到期 | 当前页和旧链接显示「游客订单链接已到期」。联系管理员获取新链接 | 到期状态下可重置。期限从重置成功时起算 |
| 管理员关闭、有效期内重置 | 旧链接显示「游客订单链接已停止访问」；原链接会话停止授权 | 关闭后可重置；重置成功时立刻切换到新链接 |
| 游客订单已关联 | 已验证的所有者从「我的订单」查看，旧链接停止访问 | 自动切换到「客户信息」。提取码通过历史来源识别为游客下单 |
| 转发链接、其他设备 | 有效链接可只读查看。登录或收到转发链接均不足以建立归属 | 跨设备关联由有权限的管理员核实订单及目标账号，显式确认单笔订单 |

- 游客期限按服务端 UTC 时刻计算，1 天为 24 小时，区间为 `now < expiresAt`。界面显示本地业务时区 Asia/Shanghai 的日期时间。剩余时间向下取整为天和小时，非零且不足一小时显示「不足 1 小时」。
- 页面只承诺关联后可长期查看订单记录。源文件、印制件和交付凭证继续按项目保留规则处理。关闭和重置管控访问入口，生产、结算、配送、售后、配额和订单业务版本按原流程演进。
- 管理端的打开、关闭、重置沿用 `order.access-link.manage`。显示完整邮箱或跳到客户详情要求 `customer.manage`。跨设备绑定要求同时具备两项权限，或具备 `*`。页面、接口和响应数据采用同一权限约束。
- 先读 [DESIGN.md](../../../DESIGN.md)、[移动端布局规范](../../UIUX-移动端布局规范.md)、[后端模块约定](../../../backend/CONTRACT.md)。中文正常字距，手机点击目标最小 44 px，编辑控件至少 16 px，窄容器允许换行，不通过根节点隐藏溢出。样稿采用已确认的小人样式和失效页，订单管理标题为「全部订单」，管理首页标题为「最近订单」。
- 撰写时已有登录修复及配送批次变更。实施前检查 `git status --short` 和重叠文件的 diff，将本次修改与已有工作分别核对。由生成器重建契约时同时验证最新基线上的配送接口变更。

## 一、订单归属和游客授权的基础契约

**文件**　`backend/app/models.py`、`backend/app/core.py`、`backend/app/auth.py`、`backend/app/auth_schemas.py`、`backend/app/commerce.py`、`backend/app/growth.py`、`backend/app/maintenance.py`，新迁移 `backend/alembic/versions/ce72aa117014_guest_order_access.py`。开始前用 `cd backend && .venv/bin/alembic heads` 核对迁移基线，撰写时为 `b8c10d2e4f16`。测试放在 `backend/tests/test_guest_order_access.py`。

- [x] 增加 `Order.owner_account_id` 外键及索引，作为客户侧归属的唯一来源。同一张表增加整数 `guest_access_version`，默认 1，独立于订单的履约版本。新登录订单提交时写入当前已验证账号。游客新单写入空值。历史记录通过任务六回填。任务一和任务六共用一次发布迁移，本地开发阶段先在隔离 schema 验证，每个中间提交均不单独部署。保留原 `principal_id` 作为下单来源和原设备证明，`checkout_account_id` 继续表示结算快照中的账号。
- [x] 归属检查集中到 `owns_order(db, actor, order)`。账号订单要求 `actor.account_id == order.owner_account_id`；未关联订单要求原游客 principal 匹配，且唯一游客入口仍有效。继续复用 `authorize_order` 检查实际业务能力。链接访问只允许 `ORDER_VIEW`、`FINAL_PDF_PREVIEW`、`DELIVERY_PROOF_VIEW`，已登录用户打开他人的游客链接时同样受这三个权限约束。客户侧无证明的直接访问返回通用 404。
- [x] 订单列表查询采用同样的所有者、有效期和会话条件，分页之前过滤。已关联订单出现在客户详情和所有者的「我的订单」。优惠、邀请奖励、结算和统计中表示下单快照的查询继续读取 `checkout_account_id`。
- [x] 更新账号注销判定、账号数据清理、订单恢复的账号锁和复查逻辑。关联单计入目标账号的未完订单。账号注销后执行既有数据保留规则，留下拒绝游客重新绑定或重新开启的归属事实。允许恢复的业务订单也须重新核对当前关联账号状态。

**返回字段**

```ts
type GuestAccessView = {
  status: "ACTIVE" | "EXPIRED" | "CLOSED" | "NO_LINK";
  version: number;
  openedAt: string | null;
  expiresAt: string | null;
  serverNow: string;
  canOpen: boolean;
};
type AdminOrderPickup = {
  pickupCode: string;
  placedWithAccount: boolean;
  customerDetailId: string | null; // 仅登录下单且有 customer.manage 时返回
};
```

管理订单详情的区块根据当前归属返回。已关联订单对有客户管理权限者提供最小客户摘要 `accountId, email, displayName, createdAt, verified`。受限员工只能拿到归属类别，界面显示「客户信息」的权限提示。

**先测后改**　新增「游客会话与账号订单分离」「关联后客户详情包含订单且 checkout 快照原值仍可查询」「注销/恢复与关联并发」用例。前两项分别以其他账号和仅持旧游客 cookie 的客户端断言 404；关联后向所有者断言 200。注销竞争使用两个 PostgreSQL 独立事务，断言关联和注销条件按提交顺序重新计算。

## 二、单链接的签发、打开、关闭与重置

**文件**　`backend/app/auth.py`、`backend/app/auth_schemas.py`、`backend/app/commerce.py`、`backend/app/core.py`、`backend/app/main.py`、`backend/app/models.py`。测试加入 `backend/tests/test_guest_order_access.py`，更新 `backend/tests/test_auth.py`。

- [x] 在现有 `AccessGrant` 增加内部 `encrypted_token`，复用 `seal/unseal` 保存高熵令牌的密文，保留 `token_hash` 作兑换索引。只允许授权管理员通过打开接口，以及原设备在游客提交成功时取得链接。禁止对通用订单 DTO、日志、报表、持久化前端存储写入明文、URL 或密文。接口按项目策略使用 `no-store` 和 `no-referrer`，令牌仍在 URL fragment。
- [x] 新游客下单在订单事务内创建默认 14 天的链接。新账号下单响应的 `accessToken` 为 null。对 `order_access_grants(order_id)` 添加部分唯一索引，谓词为 `token_hash IS NOT NULL AND source_grant_id IS NULL AND revoked_at IS NULL`。过期根记录仍占索引位置，重置事务先撤销该行再插入新行，时间判断与索引约束分别负责可用性和唯一性。
- [x] 所有关闭、重置、兑换和绑定操作按订单行锁串行判定，统一使用订单 → 授权记录 → 链接会话顺序。兑换先查询 token 哈希以定位订单，拿到订单锁后再复读授权状态与 hash。需同时锁账户的关联流程先按账户 ID 顺序锁相关账户，再锁订单并复核这些账户。关闭或重置后，已兑换 cookie、派生授权和历史授权写操作立即失效，其他订单的 cookie 不受影响。
- [x] 管理动作采用订单访问操作版本，避免旧界面关闭或替换新的链接。关闭、重置和绑定后推进版本号。并发同版本的第二次提交返回 409 并提示刷新，原请求幂等重试可核验状态。期限可留空，服务端取 14 天。显式传入 null、布尔值、字符串、浮点类型、0 或 15 时返回 422。缺少操作版本或原因同样返回 422。重置后从本次签发时间计时，过期靠判断完成，无需定时任务才能失效。优先用唯一未撤销的根按截止时间显示 ACTIVE 或 EXPIRED；只有已撤销的历史根时显示 CLOSED，从无根记录时显示 NO_LINK。
- [x] 修改幂等重放对私有响应的授权检查。对 `POST /orders`、`POST /order-access/exchange` 和订单相关写操作，重放前核对当前订单、访问版本、授权根和相关会话，拒绝返回失效前的订单正文、旧链接、旧 `Set-Cookie`。失效时间、手动关闭、重置和绑定均走这道检查。同一有效授权上的普通网络重试按现有幂等约定执行。旧路由的缓存响应在部署切换时清除，已移除路由先匹配白名单再允许读取重放记录。

| API | 输入及结果 |
| --- | --- |
| `GET /admin/orders/{id}/guest-access` | 返回当前 `GuestAccessView`。已关联订单返回 409 `ORDER_ALREADY_OWNED`，引导前端刷新区块。状态版本来自 `guest_access_version` |
| `GET /admin/orders/{id}/guest-access/open` | 显式打开时才返回当前 `{ orderId, accessToken }`。服务端复核有效和管理权限、记录审计。历史 token 不可还原时返回 `GUEST_LINK_RESET_REQUIRED` |
| `POST /admin/orders/{id}/guest-access/close` | `{ expectedVersion, reason }`。事务内关闭根授权、旧派生授权和会话，返回 `GuestAccessView` |
| `POST /admin/orders/{id}/guest-access/reset` | `{ expectedVersion, days, reason }`，`days` 默认 14。事务内清除旧入口并签发新根授权，返回 `GuestAccessView` |
| `POST /order-access/exchange` | 沿用 `{ orderId, accessToken }`。有效时返回只读订单与访问到期时间。有效 token 到期返回 410 `ORDER_GRANT_EXPIRED`，关闭、重置或关联返回 410 `ORDER_GRANT_REVOKED`。令牌无效或订单不匹配返回通用错误 |
| `GET /customer/orders/{id}/guest-access` | 当前原设备或有效链接会话可核验该单入口和服务端时间，并返回 `canClaimOnDevice`。仍能识别的原设备或失效会话只接收最小状态，现有客户端上下文可显示失效提示。无该单证明返回通用 404 |

**先测后改**　分别固定服务端时间验证创建后 14 天、截止时刻、1 天重置和 14 天重置。以持旧 cookie 的访客、持旧 URL 的访客、复用原幂等键的访客分别验证关闭、重置、关联后都拿不到订单和文件。使用 PostgreSQL 两事务同时重置并同时兑换，断言切换后只有一个有效根授权，并用数据库查询确认唯一索引约束生效。

## 三、永久关联和授权旁路清理

**文件**　`backend/app/auth.py`、`backend/app/core.py`、`backend/app/commerce.py`、`backend/app/media.py`、`backend/app/after_sales.py`、`backend/app/supplementary.py`、`backend/app/maintenance.py`。测试更新 `backend/tests/test_auth.py`、`backend/tests/test_media.py`、`backend/tests/test_v1_integration.py`，具体授权矩阵在 `backend/tests/test_guest_order_access.py`。

- [x] 增加 `POST /customer/orders/{id}/claim`，输入 `{ confirm: true }`。已验证当前账号、仍有效的原游客 cookie 与该单 `principal_id` 匹配、入口未过期或关闭时允许关联。直接读实际游客 cookie，身份验证及前端 `canClaimOnDevice` 均使用同一条件。并发时以事务内复核为准。重复绑定到同一账号返回已完成，另一个账号或转发链接持有者不能覆盖归属。
- [x] 增加 `POST /admin/orders/{id}/guest-owner`，输入 `{ accountId, expectedVersion, reason }`。管理员在人为核实订单原始信息和目标账号后操作，目标必须是已验证的可用账号；审计只记录必要非凭据字段。已过期或关闭的订单在人工核验后可直接关联，经账号查看。接口拒绝覆盖既有归属。
- [x] 两个入口共享一次事务写入归属、结束该单游客授权并刷新相关读取数据。只处理当前订单。客人同一设备下的其他订单及上传草稿由自身身份管理。
- [x] 梳理以下现有入口，移除绕过 `owns_order` 的分支。

| 路径 | 修改和验收 |
| --- | --- |
| 订单列表、详情、修改、取消、地址、配送时段和支付确认 | 客户订单统一用订单归属和游客时限。业务表单继续使用原订单版本和状态校验。账号关联后的游客订单能完成原本允许的修订，报价账号继续使用原结算快照 |
| 订单附件、原稿、预览件、交付凭证和补件 | 已关联到订单的私有资源按该订单和对应 scope 校验，再考虑业务管理权限和实际配送分配。原游客资产 `principal_id` 不能绕过关闭和绑定。纯上传或草稿仍按项目原有资源身份校验 |
| 售后详情与写入 | 原游客所有者在入口有效且订单满足售后条件时保有自身权限。共享令牌持有人只读查看订单，不能借售后接口读取内部材料或提交申请 |
| 旧链接编辑订单和准备单 | 删除 `ORDER_REVISE_PENDING` 等游客链接可选编辑 scope 的通路，删除基于授权根编辑、列出、下载准备单的逻辑。历史关联字段保留供迁移、审计和清理；仍有效的订单本人操作使用独立订单归属 |
| 批量游客关联与单单限时导入 | 移除旧 `/customer/account/link-guest` 会话级归属和 `/customer/account/import-order` 派生授权写入入口。账号设置改为前往本机订单逐单关联，登录完成仍按安全的 `returnTo` 返回，保留已修复的会话过期恢复流程 |

**先测后改**　同一原游客设备创建两单，关联其中一单。验证账号仅取得这一单，原设备对被关联订单的旧入口失效，另一单仍按原时限可查。跨设备使用只读链接、已登录但无原 cookie、伪造 orderId 时绑定失败。分别断言关闭或过期后不能用直链访问私有 PDF、交付照片、售后详情、准备单旧版本和补件写操作。比较关联前后的订单状态、报价、结算金额和优惠记录。

## 四、管理端组件、接口数据与区块切换

**文件**　新建 `src/admin/order-pickup-code.tsx` 与 `src/admin/order-pickup-code.css`，替换 `src/admin/dashboard.tsx`、`src/admin/orders.tsx`、`src/admin/production.tsx`、`src/v1/admin.tsx`、`src/admin/growth/customers.tsx` 以及盘点出的 `src/admin/logistics.tsx`、`src/admin/operations.tsx` 等工作页面。接入 `src/v1/admin-api.ts`、`src/lib/types.ts`、`backend/app/production.py` 与 `production_schemas.py`、`backend/app/after_sales.py` 及相关列表 DTO。移除 `src/growth/order-links.tsx` 中的旧管理端授权表。

- [x] 使用共享 `OrderPickupCode({ pickup, href, subline, trailing })` 组件，`pickup` 为 `AdminOrderPickup`。SVG 18 px，触控区 44 × 44 px，图标和提取码分别可聚焦。小人描边 1.7，访客预留同宽空位，表头、提取码和时间左对齐。代码链接使用调用页面原来的订单或任务目标，支持优先星标等现有内容。打印单、导出及通知用文本提取码，因为这些输出无法承载交互元素。
- [x] 登录下单且具备客户管理权限时，小人点击跳 `${ADMIN}/customers/:id`。权限不足时显示仅供识别的图标，无客户详情链接。游客关联后显示「客户信息」区块，小人仍按历史下单来源显示。生产、装订、打包、配送、售后任务 DTO 从后端批量关联订单取得这组字段，避免页面逐行发订单或客户请求，客户和配送员公开 DTO 分开控制可见字段。
- [x] 管理详情按服务端状态选择「客户信息」或「订单专属链接」。从只读接口读取区块所需数据，绑定、关闭、重置和重新进入页面时刷新一次。资料区展示当前注册邮箱、昵称、注册时间、验证状态，长邮箱正常折行。游客区展示唯一链接状态、开放/到期时间、倒计时及操作。历史链接可访问但不可还原时，显示「重置后可从后台打开」。
- [x] 「打开游客订单页」按需获取凭据并从当前页面跳到真实客户链接。重置弹窗用现有 `Field` 显示 1–14 天、默认 14、预计到期时间及原因，确认后使用服务端返回时刻。关闭确认明确访问立即结束。提交期间禁止重复操作；409 刷新状态并解释新旧版本；网络异常保留填写内容，请求重试前先核对结果。

**先测后改**　新增 `tests/e2e/guest-order-access-admin.spec.ts`，并加入 `playwright.mobile-ui.config.ts` 的测试匹配，挂载管理首页、全部订单、打印列表/详情、装订列表/详情及售后列表/详情。检查登录行与游客行提取码等距、44 px 点击区、权限不足不发客户详情请求。用真实界面交互分别验证打开、关闭、重置、关联切换和冲突恢复；使用 PostgreSQL API 测试验证不同后台能力返回的摘要数据。

## 五、客户端有效期、失效页与注册登录续接

**文件**　`src/customer/orders.tsx`、`src/customer/account.tsx`、`src/customer/account/security.ts`、`src/customer/review.tsx`、`src/customer/help.tsx`、`src/growth/identity.tsx`、`src/growth/order-security.ts`、`src/lib/store.tsx`、`src/v1/customer.tsx`。建议新建 `src/customer/guest-order-access.tsx` 与 `guest-order-access.css`。

- [x] 有效页放置「游客订单链接默认有效 14 天」及「剩余 X 天 X 小时」。以 `serverNow` 和本地经过时间换算截止点，页面恢复可见、重新联网及定期可见页复核时重新请求服务端。断网到期时本地切换，应用重新联网后以服务端为准。若同单存在账号本人权限，正常账号视图直接用订单归属。
- [x] 到期时终止在途私有请求、清除该订单的 React Query/store 展示数据并关闭 PDF/交付凭证预览，随后显示样稿中的到期页。提前关闭、重置或关联以相同路径显示「已停止访问」。识别失败和网络错误使用通用错误或重试状态。地址栏仍是原始分享网址时由兑换返回状态；无原链接、原会话与本页上下文的访问只显示普通不可访问入口。
- [x] 「联系管理员」跳到 `/u/help#contact-admin`。为帮助中心现有「还需要帮助？」卡片接入锚点，在懒加载内容挂载后定位并转移焦点，便于找到「复制管理员微信号」。配置缺失时沿用帮助中心提示。另一动作「返回首页」指向 `/u/`。
- [x] 「注册或登录，保存到我的订单」进入现有账号流程。仅传安全的站内订单返回路径，验证邮件往返后仍由原浏览器的服务器身份确认。浏览器历史、`localStorage`、`sessionStorage`、日志和 PWA 缓存中不保存 raw token。注册或登录结束后，返回该单显示明确的确认按钮；只有转发链接时提示联系管理员核验。原设备提交后提供「复制专属链接」与原有私密二维码入口，账号单只展示账号订单入口。关停时清除旧链接在内存中的副本。
- [x] 使用同一状态和文案更新订单提交成功页、订单详情和售后涉及的链接引导。删除旧 `ORDER_IMPORT`、`LINK_GUEST`、`IMPORTED_GRANT`、`OrderImportButton` 和旧「暂不关联本机订单」弹层调用。保留账号失效后重新登录回到原页的行为并改写测试中的旧弹层步骤。

**先测后改**　新增 `tests/e2e/guest-order-access.spec.ts`，并加入 `playwright.mobile-ui.config.ts` 的测试匹配，验证新链接打开、短期限、倒计时跳零、提前关闭、注册/登录续接、确认绑定、旧链接旧 QR 失效、帮助锚点和无联系方式配置。检查链接分享时 fragment 未进入请求 URL 或 storage，页面续开时不会把过期前的订单短暂绘制出来。

## 六、历史迁移、合同更新与旧流程下线

**文件**　任务一的 Alembic 迁移、`backend/app/models.py`、`backend/CONTRACT.md`、`docs/openapi.yaml`、`src/lib/api-schema.d.ts`、`scripts/generate-contract.mjs`、`scripts/extend-growth-contract.mjs`、`src/growth/demo.ts`。测试放在 `backend/tests/test_guest_order_access_migration.py`，更新已有 demo 和续接测试。

- [ ] 在脱敏 PostgreSQL 快照预演。归属以可靠的订单 principal 关联为先，如果只有可靠的直接下单账号快照则按快照回填。出现两个不同账号时停止该批上线并人工核对，禁止通过昵称、手机号或邮箱字符串猜测归属。对已归属订单关闭所有旧根、派生授权和兑换会话。
- [x] 对未关联历史单按 `created_at DESC, id DESC` 保留最新、未撤销且在期限内的一条根，截止时间取 `min(原到期时间, 签发时间 + 14 天)`。保留链接只允许三项只读 scope，其他根及所有旧派生授权结束有效期，兑换会话同步回收或限制到新的截止时间。无可用根时，以最新历史根决定显示状态。如果该根未撤销且因新期限到期，将其作为唯一未撤销的过期记录保留；如果该根已被撤销，则关闭所有剩余根。相应显示到期、已关闭或尚无链接，由管理员重置。旧根只保存哈希，原客户可继续使用符合条件的旧链接；管理员首次重置后才可打开同一条有效链接。迁移后加入唯一索引并查重，异常应使迁移事务回滚。
- [x] 删除实际运行的多链接新增、丢失补发和单条撤销路由 `POST /admin/orders/{id}/access-links`、`.../reissue`、`.../{grantId}/revoke`，删除旧列表 `GET .../access-links`。一并删除旧账号导入与批量关联、可选链接 scope 表、demo 的多链接和派生存储、旧文案及旧类型。保留历史授权行、审计记录与必要的旧令牌失效识别。禁止旧缓存客户端调用时因重放而返回成功。
- [x] 正常更新契约后跑下列检查，生成 OpenAPI 和 TS 定义，清理重复旧 schema 和 mock alias。测试用例逐个改成新业务断言，不能沿用以旧流程成功为预期的用例。

```bash
npm run contract:generate
rg -n 'ORDER_IMPORT|IMPORTED_GRANT|/access-links|/customer/account/import-order|/customer/account/link-guest|丢失后补发|撤销授权|签发新链接' \
  src backend/app docs/openapi.yaml scripts tests
git diff --check
```

运行期搜索应无旧入口，回归测试中用于断言旧请求失败的字面量可以保留。检查 `npm run build:demo` 能走同一状态模型，并确认相关旧测试 `tests/order-link-policy.test.ts`、`tests/order-authorization.test.ts`、`tests/growth-continuation.test.ts`、`tests/growth-demo.test.ts`、`tests/payment-variance.test.ts`、`tests/e2e/link-qr.spec.ts` 均完成迁移或由覆盖相同职责的新测试替换。

- [ ] 部署时暂停受影响写入，按仓库备份和恢复说明完成快照、迁移、API 与前端同步发布，再恢复访问。旧前端缓存请求也必须由新服务端拒绝。预演确认回退步骤不会借旧 API 重新启用已关闭的链接。若切换后已有新归属或换链记录，优先修复前进；整库恢复须单独核对切换后的业务和履约记录。

## 七、验证命令与交付标准

重点检查同一个入口关闭后的所有结果。维护测试所需时间通过注入或冻结服务端时钟控制。将并发请求发自两条独立 PostgreSQL 事务，明确断言获胜、失败和最终数据。建议最先落下以下组合：

| 测试 | 操作 | 成功标准 |
| --- | --- | --- |
| 旧入口重放 | 先兑换并记录请求键，然后关闭、重置或绑定。使用原链接、原 cookie、同一请求键分别重试 | 旧凭据无法读取订单、附件和私人响应；新链接在重置成功后可用 |
| 同单重置竞争 | 使用同一操作版本并发提交两个不同重置请求 | 一次成功、一次 409，数据库只有一条未撤销根授权 |
| 精确到期 | 时间冻结在截止前一毫秒及截止时刻，分别请求兑换和详情 | 截止前可读，截止时返回已到期，原设备的详情也停止授权 |
| 同单认领竞争 | 两个不同的已验证账号同时尝试将一单写入账号 | 只有满足设备证明或人工核验且先提交者可关联，第二次不会改写所有者 |
| 可用性回归 | 账号失效后重新登录、已付款订单售后、打印装订任务上下文、客人原有上传草稿 | 各流程执行既有状态校验，已关联订单走账号归属，其他订单与草稿继续可用 |

- [x] 对每项先运行相关新增测试，记录预期失败，再实现并跑到通过。完成后运行仓库回归。在独立 PostgreSQL 上执行迁移升级与约束检查，测试固定服务时钟，避免使用等待 14 天或依赖运行时本机时区。

```bash
npm run test:e2e:mobile-ui
npm test
npm run build
npm run build:demo
git diff --check
cd backend
.venv/bin/pytest -q tests/test_guest_order_access.py tests/test_guest_order_access_migration.py
.venv/bin/pytest -q
.venv/bin/alembic heads
.venv/bin/alembic check
```

- [x] Chromium 与 WebKit 均检查 320、375、390、430、768、1440 px。保存并查看失效页、带倒计时的有效页、客户信息、重置弹窗、帮助入口和后台共享组件的手机及桌面截图。断言组件表头和提取码对齐、点击区域至少 44 px、编辑字号至少 16 px、长邮箱换行、横向滚动仅在既定表格容器内，图标不会引发相邻订单或打印任务导航。
- [x] 使用实际 API 和隔离 PostgreSQL 核对四种身份，原游客、持转发链接的其他设备、账号所有者、受限管理员。至少验证创建 → 关闭/到期 → 重置 → 关联的完整链路、两台设备访问、幂等重试、并发竞争、历史迁移、联系人帮助定位及旧接口失败。报告真实 API 测试和模拟接口测试的差异。
- [ ] 若具备物理设备，分别验证 iPhone Safari、微信内置浏览器、已安装 PWA 对 URL fragment、邮件往返、后台恢复与失效状态的处理。交付如实列明实机与模拟范围、命令结果和未核实项。

## 八、追加的识别页与预览调整

- [x] 识别页不计入实际文件打印完成项；打印中心在原状态组件前单列「识别页 x/x」。采用当前生产任务/生成任务计算，避免订单快照较旧导致进度回退。用户订单详情和管理订单详情展示「订单识别页面进度 x/x」，仅启用生成且有生成任务/识别页时出现；生成中显示 0/0 和准备状态。
- [x] 新生成识别 PDF 在「在线上传文件，预约宿舍配送。」下方同左边界添加「配送地点：校区名称」，优先使用订单校区快照，缺失时读实际校区。中文正常字距；长校区名称换行并调整文本块位置，保留二维码间距。既有不可变 PDF 不重新覆盖。
- [x] 共享 PDF 预览保留上一页/下一页，增加首页/末页双箭头，正确处理边界禁用和真实 PDF 页码，按钮至少 44 × 44 px。
- [x] 打印中心窄屏筛选项独立成行，解决校区选项因同排空间不足被截断；保留 16 px 编辑字号。

## 九、实际验收记录

| 验证 | 实际结果 |
| --- | --- |
| `npm run test:e2e:mobile-ui -- --workers=3` | Chromium / WebKit 222 项通过；320、375、390、430、768、1440 px |
| `npm test` | 70 个文件、406 项通过 |
| `npm run build` / `npm run build:demo` | 正式模式及演示模式构建通过 |
| PostgreSQL 后端全量 `pytest -q --tb=short` | 1,042 项通过、5 项按条件跳过；跳过的真实浏览器账号、游客和下单三项另行执行通过 |
| 游客生命周期 + 历史迁移 + Alembic 链专项 | 35 项通过，含最后补充的双账号关联竞争、关联与注销竞争 |
| `DORMPRINT_RUN_BROWSER_TESTS=1 pytest -q tests/test_real_browser.py` | 实际 Chrome + FastAPI + PostgreSQL + Vite，2 项通过，无 API mock；手机宽 375 / 390、桌面宽 1440 |
| `DORMPRINT_RUN_BROWSER_TESTS=1 pytest -q tests/test_real_checkout_browser.py` | 实际 Chrome + API + PostgreSQL，上传、文件处理、预览、提交订单 1 项通过；确认账号归属且无游客凭据 |
| `unified-app.spec.ts` 员工登录续接专项 | Chrome 桌面和手机模拟 2 项通过；删除旧「暂不关联」弹层步骤 |
| PDF 识别页专项 | 3 项通过，含配送地点位置及超长校区文本不截断 |
| 契约及旧入口检查 | 从生成器更新 OpenAPI / TypeScript；运行时代码只保留旧路由重放的拒绝识别，旧签发/补发/撤销/批量导入入口已移除 |

手机和桌面截图已复核：有效/到期页、提取码对齐、客户信息、重置弹窗、打印状态、帮助定位和预览翻页。Chromium 和 WebKit 为浏览器模拟；没有 iPhone Safari、微信内置浏览器或已安装 PWA 的物理设备验收结果。邮件登录续接覆盖安全站内返回路径，尚未在真实邮件客户端与微信跨应用环境核验。

部署需同步执行 `ce72aa117014` 迁移及前后端更新。迁移升级、模型差异检查与兼容数据回退已在隔离数据库验证；升级后若已发生新的单笔账号归属，降级会主动阻止删除这项归属，应前向修复或按部署前备份与新增业务核对后恢复。生产数据回填存在可靠归属冲突时迁移直接停止，不猜测所有者。

## 十、独立代码复核后的补充修正

- [x] 恢复超时订单时，在账号锁之后取得订单锁，再核对实际归属是否与原账号快照相同。归属改变则返回 409，要求重新核对，避免恢复已进入注销流程的客户订单；不在持有订单锁时逆序追加账号锁。
- [x] 文件列表与已完成上传会话同样通过私有文件访问校验。游客入口关闭、到期或关联后，旧设备不能通过这些接口读取文件名称、哈希、上传结果等信息；未提交到订单的草稿上传继续可用。
- [x] 补充费用争议先锁订单，再核对当前所有者和入口权限。关闭链接或关联账号已提交后，旧游客请求无法新增争议。
- [x] 在实际 PostgreSQL/API 测试中将关联、注销、关闭操作插入原校验与加锁之间，先复现旧行为错误再验证修正。独立复核确认上述三类问题已修正，未提出其他重要发现。

## 十一、完整客户页面与生产交接

- [x] 核对实际路由：有效、到期与关闭页面均位于 `CustomerLayout`，复用同一品牌页眉、页脚和手机底部导航；先前组件截图不代表完整应用页面。
- [x] 有效期提示改为复用 `.content-page` 内容容器，修复手机和中等屏宽的左右边距不一致；新增 Chromium/WebKit 六档宽度回归，核对提示边框与订单内容区边缘。
- [x] 实际 Chrome + API + PostgreSQL 验证有效、到期、关闭三类完整页面，检查六档宽度并保存 390 / 1440 px 的整页与首屏截图；包含默认 14 天、剩余天/小时、注册登录引导和管理员帮助定位。截图为隔离测试订单，没有生产数据。
- [x] 编写 [生产端拉取与迁移说明](../../incremental/2026-11-18-guest-order-access.md)，并替换部署手册中与本次停写升级要求冲突的旧快捷命令，明确先构建新迁移镜像再迁移。生产发布由 DSH 按实际部署方式执行。
