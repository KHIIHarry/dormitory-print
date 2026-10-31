> **本轮业务链调整（2026-10-30，新增2—7项）**：以 `docs/superpowers/specs/2026-10-30-dormprint-v1-adjustments.md` 为本轮增补。纳入链接转二维码、同照片批量送达、选填送达文字、统一应用及个人页权限入口、员工1—6级百分比优惠/超管打印永久免费、定额或比例配送工资。原三套独立PWA/三个初始入口方案取消；身份安全隔离继续保留。N01—N07及F01—F03已确认，金额/身份/交付按最终答案落地，不得遗漏新增模块。

> **V1最终定版覆盖（2026-10-30）**：本文件中的旧未决项/冲突规则由 `docs/superpowers/specs/2026-10-30-dormprint-v1-scope.md`（Q01—Q36及R01—R06全部已批准）替代。完整范围见配套V1功能交付矩阵。不得以旧D-02/D-04/D-07未批准为由省略指定纸张补打、按册装订或人工打印队列；Excel/HEIC、分片续传、售后退款、细分权限等均已纳入。自动关单、收款占容量、配送员start、默认只读链接及数据清理必须按新规则执行。

# DormPrint H5 / PWA：前端增量开发 Prompt（账号、订单链接、邮箱、邀请优惠与满量折扣）

## 统一账号取代旧员工凭据链（本轮最终规则）

基础客户账号是唯一登录主体；Staff仅为accountId关联的角色、能力、等级和超管标记。旧文出现的员工专属注册/密码、临时密码、独立员工会话与STAFF_SETUP/STAFF_RESET页面均作为旧版迁移说明，不再作为V1交付要求；员工改密、邮箱核验和找回走同一客户账号安全链，后台只授予/撤销能力并可撤销该账号会话。游客及单单授权仍独立，不能由角色/同名/手机号推导所有权。每账号两设备合并计数，第三台拒绝；员工高权限7天重新验证不新增另一密码。原有已注册客户、当前有效权限及最后超管保护均需真实服务端检查。


> **文件性质：增量补丁，不替代原《01-前端开发Prompt.md》。** 将原前端 Prompt、原后端 Prompt 和本文件一并交给前端 Codex。现有用户端、管理端、配送员端页面已基本完成，**优先在现有仓库上增量改造，不重做 UI、不改原业务主流程**。本文件与《04-DormPrint-后端增量开发Prompt.md》共用增量接口约定。
>
> 版本：`DORMPRINT-IDENTITY-GROWTH-1.3` / 2026-10-29；基础契约：`DORMPRINT-CONTRACT-1`；API 仍为 `/api/v1`；使用原有 camelCase、`{data,requestId}` / `{error,requestId}`、`Idempotency-Key`、`expectedVersion` 和权限体系。

## 0. 优先执行的总原则

1. **保留已建前端**：先审阅文件、目录、路由、状态管理、API 层和现有样式；先给出「复用/新增/微调」清单，再直接实现。禁止把现有打印、预览、计价、地图、配送、PWA 重写成另外一个项目。
2. **本补丁优先级**：本轮用户已明确确认「邮箱注册登录 + 游客继续使用 + 微信人工发送订单专属链接 + 注册优惠 + 邀请码推广 + 三端账号管理 + SMTP/邮件模板后台配置」，属于新增业务的已批准范围；原 D-06 按 01/02 修订拆为已批准 D-06A/B 与未批准 D-06C；本轮五项业务确认及新增满量折扣见下方统一修订条款。仍不得未经确认启用「仅手机号/短提取码找回完整历史订单」或短信验证；D-01、D-02、D-03、D-04、D-05、D-07、D-08 保持原决策门槛。
3. **保留原核心不变量**：微信好友由用户主动添加、管理员手工发送链接；不实现微信 OAuth、微信机器人、自动付款、微信支付或自动打印。线下人工收款、最终 PDF 的 `artifactId + sha256`、按张计价、装袋配送强守卫、交付照片/定位规则均不得修改。
4. **统一账号与角色授权**：用户端 `CUSTOMER` 可为 `ANONYMOUS`（游客）或 `REGISTERED`（已验证邮箱账号）；管理/配送权限由超管授予现有已验证客户账号，按accountId关联ADMIN/COURIER能力，统一客户登录后派生有效员工上下文。用户注册入口不能自授员工权限；不再有独立员工密码和第二套登录。
5. **契约先定、前端可先开发、后端联调**：先完成本补丁账号授权/优惠状态机与 OpenAPI diff，再新增 API 按本补丁及后端补丁中的统一契约建立前端 typed client 和 MSW mock（仅开发环境）；不可在生产环境使用模拟成功或假邮件发送。所有未知后端能力显示真实不可用提示。
6. **未定数值可后台配置**：优惠金额、有效期、限额、邮件重发冷却时间不允许硬编码成业务事实；前端示例使用占位文案，真实显示以服务端公开配置/响应为准。新增满量折扣按 0A.10 完整实现。

## 0A. 2026-10-29 用户确认后的统一修订契约（前后端必须一致）

修订标识：DORMPRINT-IDENTITY-GROWTH-1.3。下列业务来自用户对本次审查问题的明确回复，不再作为待决项反复询问：

1. 微信订单链接允许查询，待确认可修改或撤销，付款确认后到实际配送开始前可修改楼栋；配送中/送达后不可自行修改。
2. 已预约到订单的优惠受锁价保护，自然过期或普通活动停止不影响既有订单；风控冻结转人工核对，禁止静默加价。
3. 允许应付 0 元；管理员人工确认零元结算后才可打印，零元单不触发邀请奖励。
4. 邀请首单按第一笔成功送达且实付大于 0 的合格订单认定，先创建未送达不占资格，每受邀人最多一次。
5. 游客关联账号后轮换游客会话；退出账号后不得再借旧游客身份读取已关联订单，须登录账号或持有效订单链接。
6. 新增满量优惠：按本订单 A4 实体用纸累计，自动仅折 A4 打印费；照片/装订不参与，与优惠券自动比较取优惠金额更大的一种、不叠加。

本节同步修复审查发现的领域约束，正文按本节执行。未指定的优惠金额、期限、存储/备份周期不擅自填值；D-01/02/03/04/05/07/08 继续原门槛。D-06A=APPROVED、D-06B=APPROVED，D-06C=NOT_APPROVED（不做手机号/短码公共找回、短信验证）。实现前更新决策登记与旧文档引用，不保留“D-01—D-08 全部未决”的笼统结论。

### 0A.1 链接权限、单单导入和委托编辑

- 管理员签发的标准用户链接含 ORDER_VIEW、FINAL_PDF_PREVIEW、DELIVERY_PROOF_VIEW、ORDER_REVISE_PENDING、ORDER_CANCEL_PENDING、ORDER_CHANGE_LOCATION_PRE_DELIVERY、ORDER_IMPORT。状态约束取 A1/原状态机，两者取交集；scope 不是跳过付款、版本、预览或配送守卫的通行证。
- FINAL_PDF_PREVIEW 仅允许该订单当前合法标准化/最终 PDF 的受权预览，以便修改前核对；不增加源 Office/原始照片下载、不授权账号资料、其他订单、账号优惠券列表或任何员工能力。二进制预览天然可被客户端保存，不能以“隐藏下载按钮”保证不可复制；授权范围依资源与版本校验。
- ORDER_REVISE_PENDING 使用原 preparation→最终 PDF→previewAcks→quote→revisions；必须限定 replacesOrderId、expectedOrderVersion，复用的旧资产属于此单，上传新资产属于当前委托会话。激活时再次校验源 grant 未撤销/过期及订单仍待确认；不能以已生成 preparation 洗掉委托来源。
- 委托编辑不把 checkoutAccountId 改成链接访客的账号。已有优惠按订单快照/本订单预约处理；不得读取或换用原 owner 账号的其他券。若编辑导致原优惠资格不满足，阻止激活并由有权 owner 重新确认报价/选券；不静默删券加价，也不借导入冒领新客/邀请资格。
- 导入账号使用独立 account_order_grants，字段含 accountId、orderId、sourceGrantId、scopes、expiresAt、revokedAt、version；禁止 ORDER_GRANT 写入 customer_principal_links。导入不是所有权转移，不包含同游客 principal 的其他订单。
- 派生 scopes 不得大于源 scopes、有效期不得晚于源 grant；每次访问检查完整源链，源撤销/到期立即使派生授权失效。真正 owner/principal 合并带来的账号权限独立，不因撤销一个微信链接而消失；不能仅凭“该单出现在账号列表”升级成 owner 权限。
- grant 不能反复导入或再派生绕过源失效。允许多个合法访问渠道时，以各自真实权限判断，不从只读/导入身份推断账号所有权。
- 兑换返回限定 scope 的 Cookie 会话与 allowedActions；前端据此显示修改/撤销/改楼栋，后端在每次动作及资产读取重新检查，链接失效时明确说明。

### 0A.2 客户、游客、员工与链接会话隔离

- 三种基础会话为guestSession、customerAccountSession、orderAccessSession，使用互不覆盖的HttpOnly/Secure/SameSite Cookie；员工上下文从统一客户账号会话及当前数据库Staff授权派生，旧独立staffSession Cookie不再授予权限。所有写接口校验CSRF/Origin；pendingRegistrationSession仍是独立短期受限凭据，不覆盖已验证账号会话。
- `/customer/session` 只初始化/轮换游客，不覆盖账号登录；客户owner、游客及订单scope权限分别计算；员工接口从同一基础客户会话派生当前员工能力，访问客户页面不自动以管理员身份越权。退出统一账号同步失去员工上下文；只撤销员工权限不抹去客户身份与订单所有权。
- 新账号验证创建独立账号主 principal；邮箱验证/登录本身不自动合并游客。link-guest 必须同时证明游客会话和已验证账号并显式确认，原子关联 principal，撤销该已关联 principal 的旧游客会话，当前浏览器换为全新未关联游客 principal；旧游客 Cookie 在其他页面/设备也不能继续授权。独立有效订单链接不因此被撤销。
- 账号退出清掉账号 Cookie、私有缓存和预览、取消旧请求，保留新游客主体；不会把已关联 principal 又还给游客。其他已验证账号不得认领已关联给另一账户的 principal。
- 前端拆分 customerSession/staffSession/orderAccess 状态，缓存 key 包含主体与授权范围；异步响应校验当前身份代次，切账号/退出后旧响应不得回填敏感订单/优惠。
- 结算中登录使用共享 checkout controller/同标签登录弹层等保留 preparationId、manifest、previewAcks 和草稿，不在 URL/持久浏览器缓存塞文件/凭证。不愿合并游客时仍可凭当前游客会话继续这次游客无账号券的结算流程（仍可享满量）；用账号券必须显式关联这次结算的合法主体，不隐式合并全部游客订单。
- 若已登录但不选择合并历史游客订单，可使用 `POST /preparations/{id}/adopt-checkout`（`{expectedVersion,confirm:true}`），同时证明账号与当前游客 preparation，创建仅本次 preparation/输入 manifest 的 checkout_use_grant。新订单归账号主 principal，原资产 ID/owner 不覆盖，仅记录对此单的合法使用引用；不导入其他游客订单。此单创建时 checkoutAccountId 固定；未创建前撤回该授权可改回游客并重新报价。不能凭导入旧订单授权使用 adopt-checkout 抢走旧订单 owner。
- 新标签/不同设备不承诺恢复内存草稿；上传页不再读取或展示服务端 preparation 草稿，用户离开后需重新准备；已生成的 preparation 仍只服务当前结算，不能伪造原 previewAcks。登录/退出/切号只重新核验身份和优惠，PDF 未变化不重转。

### 0A.3 账价快照与人工零元结算

- 统一公式：subtotalCents=sum(非负正向商品行)，deliveryFeeCents=0，totalCents=subtotalCents-discountCents，0≤discountCents≤selectedEligibleSubtotalCents≤subtotalCents；selectedEligibleSubtotalCents 为所选券适用小计、满量A4小计或员工优惠原打印单整单小计，券/满量/员工等级三类优惠最多取一种。折扣不是负 PriceLine。Order 返回 currentQuoteId、contentRevision、完整 quote 快照和结算记录。
- Quote 扩展 checkoutAccountId（游客为 null）、selectedEligibleSubtotalCents、promotionRuleVersion、discountAllocations（按 lineKey/relatedId 的整数分分摊）、selectedPromotionType=NONE/COUPON/VOLUME_TIER/STAFF_GRADE；身份与活动版本由服务端判定，不信任前端自填。订单首次创建时记录不可回溯重写的 checkoutAccountId、checkoutAuthKind 与邀请资格来源，导入/合并不能把历史游客单变成注册首单。
- 全部页面、微信付款文案、收款、统计使用同一订单快照，不按当前价目/活动重新算历史应付。Quote 未就绪是 null/显式状态；禁止 `prep.total || total`、`Number(value) || 原价` 把 0 当未生成，真实模式禁止回退本地估价。禁止固定 `quote-v1`，收款提交服务端 currentQuoteId。
- 扩展原 `POST /admin/orders/{id}/confirm-payment` 请求：`{expectedVersion,contentRevision,quoteId,receivedCents,settlementKind:'CASH_MANUAL'|'ZERO_PAYABLE',note?}`。CASH_MANUAL 必须 receivedCents=totalCents>0；ZERO_PAYABLE 必须两者都为 0、管理员明确人工确认，空白实收输入不能被 Number(空串) 当作人工零元确认，且无阻断风控。均复用原生产前置与幂等事务，只有成功才进入 PENDING_PRINT；仅所选 COUPON 才将有效 RESERVED 券核销，VOLUME_TIER/STAFF_GRADE/NONE不产生券核销。
- 零元记录 settlementKind=ZERO_PAYABLE、cashReceivedCents=0、discountCents、确认人/时间，不描述为已收到微信转账。零元页面提示“无需转账，等待管理员确认零元结算”，仍可联系微信；不自动放行、不产生邀请有效实付。
- 统计分开商品原价、优惠让利、订单应付、现金实收、零元确认数量、退款/人工调整，不将补贴当现金营收。
- 这是金额语义升级，仅无优惠路径保持数值兼容；先改 OpenAPI（含 additionalProperties:false 的严格 schema），再生成类型/部署前端与后端。未升级客户端不得接受/提交自己不能正确显示的优惠报价；版本/能力不符明确要求升级，不隐藏折扣。

### 0A.4 券状态机、预约保护、变更与回收站

券主体状态为 AVAILABLE/RESERVED/USED/EXPIRED/REVOKED；风控 riskHold=NONE/FROZEN 独立于主体状态，不用 FROZEN 覆盖预约单号/已核销事实。

| 事件 | 前置与结果 |
|---|---|
| 新报价/新预约 | 已验证账号、本人券、AVAILABLE 且未冻结、在可用期限、活动/服务/额度合法；报价不占券，提交订单事务才 RESERVED |
| 同一订单沿用原券改版 | 允许本单 RESERVED；重新检查改后商品/门槛，保留已锁定的券规则；原子换 quote 与预约，不误判“其他订单已占用” |
| 自然过期/活动停止 | AVAILABLE 不再新预约；已 RESERVED 仍按原订单快照结算，不重价、不自动释放，不将活动停止变为风控冻结 |
| 人工正数/零元结算 | 检查当前订单/报价、riskHold 无阻断，RESERVED→USED，与结算/生产资格同事务 |
| 用户合法撤销/待确认普通删除 | 释放本单预约；当前仍合法则 AVAILABLE，否则 EXPIRED/REVOKED，riskHold 保留；同动作幂等不重复返券 |
| 风控冻结/撤销已预约券 | 保留订单原金额与预约历史，建立阻断结算的人工核对异常；不能静默恢复原价或自动扣余额 |
| 冻结解除 | 去除 riskHold 并记录原因，未解决的其他 blocker 仍阻断；不可伪造已付款/已打印 |
| 已核销后的纠错 | 只追加调整/冻结账目；不能直接返成 AVAILABLE，不能改既有已付款订单价 |

- 修改待确认订单换券/取消券须生成新报价并用户确认；释放旧券与占用新券、订单版本/报价切换在同事务，失败全回滚。新券按新预约资格，原券已预约自然过期不妨碍本单保持原约定。
- 待确认订单进入回收站后不占预约；24h 内恢复先检查原券和预算是否仍可恢复。可合法按原锁定优惠价恢复才重建预约；不行则保留历史价、加 REQUOTE_REQUIRED blocker，用户明确确认新报价前不能收款/生产。恢复不能从另一单抢回已用券。已付款删除仍受 D-05，不能借回收站重发 USED 券。
- 账号冻结不取消已付款履约；未付款且涉及被冻结优惠的订单需人工核对，不偷偷改价。禁止通过删除、退出账号、重复修改或延迟任务重复返券。
- 不引入未经批准的“待付款超时自动涨价/释放券”机制；要关闭订单必须走合法撤销/删除动作并保留历史。

### 0A.5 活动预算与优惠分摊

- 首版账号券仅固定金额 FIXED_AMOUNT，新增自动满量优惠 VOLUME_TIER；每单最多一券且满量与券最多采用一种，后台叠加字段只读 false，其他优惠形式不作为已实现能力展示。金额/门槛/期限/发行上限由后台明确配置，无配置则不启用。
- 满减门槛以适用服务正向行合计 eligibleSubtotalCents 为基数；仅 A4 券不能靠照片金额凑门槛或抵扣照片。折扣=min(券面额,eligibleSubtotalCents)，按可优惠行比例向下取整分摊，剩余分按稳定 lineKey 排序补齐，总分摊必须恰等于折扣；这些值写入不可变报价。
- 区分发行额度、预留优惠负债、已兑现让利。若设置 budgetCents，发券按面值预留预算（reservedLiabilityCents），核销时将实际抵扣转 realizedDiscountCents，并释放未抵扣面额差；AVAILABLE 券过期/合法撤销释放其负债，订单取消仅释放预约不重复释放仍可用券的发行负债。
- 数据库保证 reservedLiabilityCents+realizedDiscountCents≤budgetCents（预算缺省表示管理员明确未设该上限）；已发券/已预约义务不能因调小预算而抹掉。券面额/规则有版本快照，活动修改不覆盖旧券，停止发行不撤销已承诺义务。
- RESERVED 券自然过期仍保护其负债；风控冻结只阻断使用，不直接释放仍有有效承诺的预算。每个发放/预约/核销/释放事件有业务唯一键，防多次扣减/返还。

### 0A.6 邀请首单、乱序事件与纠错

- 条件为：订单创建当时 checkoutAccountId 对应已验证受邀账号、有效邀请关系、合格商品/门槛、人工 CASH_MANUAL 结算且 cashReceivedCents>0、成功送达。游客事后导入、零元确认、仅注册、仅创建/付款未送达均不达标。
- 第一笔“成功送达”以服务端交付事务原子认定为准，不按订单创建顺序或 worker 消费先后。交付事务锁邀请关系（固定锁顺序），若 qualifiedOrderId 未设则写入该单和 qualificationSequence/qualifiedAt；并发成功以数据库串行提交顺序认定，每受邀人唯一资格。outbox 只按已选资格发券，不重新抢首单。
- 邀请奖励规则在邀请邮箱验证生效时保存 campaignVersion、有效奖励资格截止（须活动明确配置，可显式无截止）、门槛/面额/预算策略；没有启用活动就不承诺奖励，不在以后补开活动追溯。普通停止发行不重写已有规则；到资格截止仍不合格则不发。
- 达标与发券分状态 QUALIFIED/PENDING_BUDGET/GRANTED/DISQUALIFIED/REVERSAL_REVIEW，预算不足记 PENDING_BUDGET，不虚报奖励已发、不替换成后续订单、不重复达标；补足预算可按同一资格幂等发放。
- 交付被合法纠错后按事件追加撤销/冻结记录。奖励尚未使用可按规则冻结/撤销；已用于另一笔已付款订单时进入 REVERSAL_REVIEW 人工处理，不回滚该订单价格、不自动扣款或把券余额改成负数、不删除历史。资格仍最多一个，不通过反复送达/撤回重复奖励。

### 0A.7 验证邮件、首次员工初始化与安全载荷

- 未验证邮箱更正使用短期 pendingRegistrationSession/registrationChallenge，不允许任意提交 email 修改别人账号。新增 `POST /customer/auth/change-pending-email`，DTO `{registrationChallengeId,newEmail}`，同时验证受限会话、CSRF、限流；旧验证 token 作废，重建新邮箱挑战，邀请码暂存不重复领券；不能修改已验证账号，后者仍用 request/confirm-email-change。
- 邮件 actionUrl 的 GET 只显示无副作用落地页，邮箱验证/改密/员工初始化均需用户明确按钮 POST 消耗一次性 token，避免邮件扫描/预取触发。安全 token 置 fragment，页面在内存暂存并清除地址栏，不上报到日志/第三方；注册验证与找回的业务动作不能由自动 useEffect 执行。
- 员工授权：用户正常注册并验证基础客户账号→超管按accountId选择该账号→授予角色、细分能力、等级或明确超管标记→ACTIVE。员工使用同一基础账号密码，入口随授权显示、随撤权消失；找回和改密沿用基础账号安全链。停用权限不注销客户身份，不签发员工临时密码。
- 验证/重置挑战长期只存 hash，mail outbox/幂等重放按短期加密 payload 保存发送所需原凭证，成功发送或保留窗口结束销毁；SMTP secret 独立加密。不能要求“只存不可逆 hash”同时让 worker 反解发邮件。
- 配置/模板编辑有版本，测试发送不产生真实可操作 token；已排队消息按其快照发送。SMTP 故障不回滚已经确认的收款、打印、装袋或送达事实。

### 0A.8 需要同步的新增接口与能力

以下加入共同 OpenAPI，并与正文第 7/8 章一起生成类型；新建资源不需要虚构 expectedVersion，修改资源必须版本/幂等校验：

| 方法/路径（相对 /api/v1） | 请求/响应及约束 |
|---|---|
| POST /customer/auth/change-pending-email | registrationChallengeId/newEmail，受限待验证会话证明；统一安全响应 |
| POST /customer/account/link-guest | confirm:true + 两种合法会话；返回 rotationCompleted:true、customerPrincipalId、可见范围；原子撤销旧游客会话并设置新 guest Cookie |
| POST /preparations/{id}/adopt-checkout | expectedVersion/confirm:true，当前游客+已验证账号双证明；仅本次preparation的checkout_use_grant，不导入其他订单 |
| POST /customer/account/import-order | orderId/confirm:true，需源 grant 的 ORDER_IMPORT；返回 accountOrderGrantId/sourceGrantId/scopes/expiresAt，非 owner 转移 |
| POST /preparations/{id}/requote | promotionIntent 可选，按 0A.10 的自动择优语义；返回完整新 Quote、checkoutAccountId、适用小计与分摊；未传字段沿用上次选择模式重报，不默默换身份 |
| GET /orders/{id} | currentQuoteId、quote 快照、settlement 摘要、allowedActions/blockers；角色裁剪不泄露 owner 账号/其他券 |
| POST /admin/orders/{id}/confirm-payment | settlementKind/receivedCents/quoteId/contentRevision/expectedVersion，正数与零元均需管理员人工确认 |
| POST /admin/promotions/coupons/{id}/freeze | expectedVersion/reason，设置 riskHold=FROZEN；RESERVED 关联订单加 blocker |
| POST /admin/promotions/coupons/{id}/unfreeze | expectedVersion/reason，解除风控，保留主体状态与其他 blocker |
| POST /admin/promotions/records/export | 白名单筛选/字段、权限、行数上限、审计；返回受控 exportJobId，不导出 token/SMTP secret/文档内容 |
| GET /admin/promotions/exports/{id} | 仅发起者及有导出管理权限管理员；状态/到期时间及受权下载入口；no-store |

新增错误：SETTLEMENT_KIND_INVALID、COUPON_RISK_HOLD、REQUOTE_REQUIRED、ORDER_IMPORT_SCOPE_DENIED、PENDING_REGISTRATION_REQUIRED、CLIENT_UPGRADE_REQUIRED。登录账号不存在性仍使用公开统一响应。

### 0A.9 新增数据的生命周期与实施门槛

- account_order_grants、pending session/challenge、优惠账本/奖励资格/预算、SMTP 配置版本/secret refs、mail outbox 受保护 payload、导出文件逐类登记起算点、失效条件、清理与备份恢复策略，不能统套“订单文件删除 24 小时”。
- 挑战到期/使用即无效，秘密载荷短期加密并清理；已撤销 grant 与旧游客 session 在备份恢复后仍须失效。账号/优惠账本的必要历史保留最小审计，不把删账号变成删收款事实。旧 SMTP 配置恢复不自动重新启用旧凭据；密钥轮换依赖检查不丢失未发送作业。
- 正常资产、账号/邮件/导出内容具体保留数值、备份政策仍需配置批准；未批准禁止自动永久清理正常有效业务数据。临时秘密载荷 TTL 必须在启用相应功能前明确配置，不能无限保存。
- 先完成上述领域/授权契约与下方满量规则的 OpenAPI diff；前端随后可用按该契约的开发 mock，后端并行实现同一契约，最后真实联调。本轮仅修订 Prompt；后续按本文启动实施时，必须诚实区分 UI 已有与后端未联调，不把本次文档修订当成功能已实现。

### 0A.10 新增已批准功能：A4 打印张数满量折扣与自动择优

**业务口径**：同一订单全部 A4 文档当前有效打印任务的实际纸张张数相加；不跨订单/账号历史累计。单面 n×copies，双面 ceil(n/2)×copies，各任务先独立向上取整再加；系统补的空白背面不额外加纸，原文档空白页正常计，明确跳过页不计。照片、装订、袋子、重印/补打的额外耗纸均不累计门槛。多文件均为本单 A4 打印才合并统计。

**配置与档位**：管理端“系统配置 → 满量折扣”支持启停、规则版本、生效时间、档位列表（id、minSheets 正整数、payRateBps 整数 0—10000）、公开规则说明及操作日志。payRateBps 是应付比例：9 折=9000，8.5 折=8500，10 折=10000，0 折=0；UI 输入折数精确转整数，最多三位小数，禁止把“9 折”当作减 90%。重复门槛拒绝保存，门槛升高不得出现更差应付比例；未配置或未启用无满量优惠，不写死张数或折数。单/多档均支持，至少达到 minSheets（含等号）才符合。

**金额算法**：a4Sheets 为累计用纸，a4SubtotalCents 为 A4 打印正向价格行之和（不含照片、装订），不取订单总价乘折扣。每个命中档位：

```
volumePayableCents = floor((a4SubtotalCents * payRateBps + 5000) / 10000)
volumeDiscountCents = a4SubtotalCents - volumePayableCents
```

采用整数分四舍五入，仅对 A4 小计一次舍入，不逐页/逐文件舍入后相加。计算使用足够范围整数/Decimal 防溢出。多个命中档位取 discount 最大者，档位同额按 minSheets 较大、再稳定 tierId 排序，避免依赖数组输入顺序。10 折或最终减免为 0 时 selectedPromotionType=NONE，不消耗券；0 折合法时仍须原人工零元结算守卫。

**与优惠券自动择优**：

1. 自动模式下，已验证账号比较所有当前可用本人券各自的实际减免与最佳满量减免；游客只有满量候选。每候选按自己的适用商品/门槛计算，不能只比较券面额。
2. 采用减免金额更大的一种，不叠加；同额优先 VOLUME_TIER 保留券，券之间同额按 validUntil 最早、再稳定 couponGrantId 排序。只有最终 COUPON 在下单事务 RESERVED、结算事务 USED；满量胜出不占券、不核销券、不扣券预算。
3. `PromotionIntent={selectionMode?:'AUTO'|'NO_COUPON'|'SPECIFIC_COUPON',couponGrantId?:string|null}`。新结算默认 AUTO。NO_COUPON 只排除券，仍自动计算满量；SPECIFIC_COUPON 仅将指定券与满量比较，满量更优或同额仍选满量；不能强制两者叠加。兼容原显式 couponGrantId:null 映射 NO_COUPON，非空且无 mode 映射 SPECIFIC_COUPON；未传 promotionIntent 的 requote 保留上次模式。每次身份切换重新核验主体，不能沿用另一账号的券。
4. Quote 输出 `selectionMode,selectedPromotionType,selectedEligibleSubtotalCents,volumeDiscountCandidate,discounts,discountCents,totalCents,promotionRuleVersion`。`volumeDiscountCandidate` 包含 policyId/policyVersion/tierId/minSheets/a4Sheets/a4SubtotalCents/payRateBps/discountCents，未命中为 null；前端显示为何用满量或券，未采用的券仍可用。只显示当前主体有权的券候选信息，不向链接访客泄漏 owner 券列表。
5. `discounts` 最多一条，判别联合：`{kind:'COUPON',couponGrantId,campaignId,amountCents,description}` 或 `{kind:'VOLUME_TIER',policyId,policyVersion,tierId,a4Sheets,payRateBps,amountCents,description}`；NONE 时空数组。volume 项没有伪造 couponGrantId/campaignId，PriceLine 不加入负金额。
6. 所选优惠按其 eligible 行比例分摊整数分（见 0A.5）；满量只向 A4 打印行分摊，照片/装订行减免为 0。记录获胜优惠、规则和行分摊快照，不能运行时拿最新配置重算历史订单。

**事务、锁价与修订**：

- 建单事务重检报价版本、所选优惠、可用券及必要额度。自动模式资格发生影响获胜结果的变化，返回 COUPON_REQUIRES_REQUOTE/QUOTE_EXPIRED 并生成可确认新报价，不暗中换价。未获胜券的变化不应消耗或阻断已合法的满量方案。所有模式都防伪造前端 a4Sheets、折数、折扣金额。
- 已提交订单的满量优惠与券一样锁价，后来改档位/停用不会自动影响；只改地址也不重新折扣。修改待确认订单的文件/份数/分段会重新计算纸张和报价，用当前适用的满量配置；旧有效订单保留到用户确认新价且事务切换。已预约原券的继续使用按 0A.4 保护，不按最新活动静默撤销。
- 改版导致优惠从 COUPON→VOLUME_TIER 时同事务释放本单券预约；VOLUME_TIER→COUPON 时同事务占券，任何失败保持原版本/价/券不变。总价变动必须用户重新确认，PDF 未变只换优惠不重转/不清空合法预览确认。
- 取消、删除、恢复满量优惠只处理订单快照，无券可返；若恢复后改内容需合法重报。已付款内容/金额变化继续受 D-01，不因满量功能开放后台改价后门。
- 满量是全体用户可用的规则优惠，不是账户券发行；不计入券面值负债，不制造 coupon_grant，不挪用注册/邀请发券预算。统计单列 volumeDiscountCents 与 couponDiscountCents，总折扣等于两者之和且同单最多一项非零。

**接口与模型**：`GET/PATCH /admin/config/volume-discounts` 使用 expectedVersion/幂等/审计，PATCH 为完整规则版本更新，默认未启用、档位空；不得两个配置源。`GET /public/config` 返回公开启用档位/说明及版本；`POST /preparations` 和 requote、orders/revisions 共用同一 PricingService。新增 `volume_discount_policy_versions/volume_discount_tiers`（或等价强类型配置）及 quote/order 优惠快照，已有已付款单迁移填 discount=0 或原历史事实，不能按新档位追溯让利。

**界面**：后台支持添加/编辑/移除档位、同一组规则预览、启停、生效时间、版本冲突及变更范围提示。用户结算显示“本单 A4 用纸 X 张”“满 Y 张享 Z 折”“A4 原价/满量可减/券可减/本次选用/最终应付”，未达门槛提示还差几张时只作提示，不自动加份数。照片/装订仍按原价显示，游客不需注册即可享满量；账号优惠区不能误写“游客只能原价”。

**专门验收**：前后端各追加 VOLUME-01—VOLUME-12：

| 编号尾号 | 输入/动作 | 必须结果 |
|---|---|---|
| 01 | 门槛少1张/恰达/多1张、未启用/空档位 | 含等号命中，未启用不折；无硬编码业务阈值 |
| 02 | 前4页单面×2+后6页双面×2、分属多文件也相同 | 8+6=14张；按任务独立取整后累计 |
| 03 | 7页双面×3、跳页、系统补白、补打 | 12张，补白不多加，补打不使原订单升档 |
| 04 | A4+照片+装订混单 | 仅A4张数达门槛，仅A4费打折，其他减免为0 |
| 05 | 多档、重复门槛、9折/8.5折 | 最大实际减免，重复拒绝；9000/8500不误作减免比例 |
| 06 | A4小计1001分、9折（示例仅测试） | 应付901分、满量减100分；按A4小计一次舍入 |
| 07 | 满量比券大/小/同额、两券同额 | 大者胜，同额满量保留券，券排序稳定；不叠加 |
| 08 | 游客与注册账号、显式不用券/指定券 | 游客自动满量，AUTO全候选择优，NO_COUPON仍满量，指定券仍与满量比较 |
| 09 | 满量胜出、优惠类型切换及并发抢券 | 未用券不预约/核销；改版占券释放事务一致，失败保留原单 |
| 10 | 改后台规则/停用、改地址、待确认改份数 | 旧单锁价，改地址不重价；改份数新报价新确认，旧单未提交前保留 |
| 11 | 伪造张数/折数/金额、旧客户端、0元 | 服务器独立复算；不兼容阻断；0元仍人工确认且无邀请奖励 |
| 12 | 取消/删除/恢复、刷新快照、后台对账 | 无假返券，折扣来源/规则/分摊可追溯、用户与管理应付一致 |

上述示例数字只用于自动测试，不能 seed 为营业价格或默认启用档位。

## 1. 新页面及路由（延续现有 `/u/`、`/courier/`、`ADMIN_BASE_PATH`）

| 应用 | 页面/路径建议 | 具体功能 | 处理方式 |
|---|---|---|---|
| 用户端 | `/u/` | 游客直接上传；右上角「登录/注册」或账号头像；展示权益而非强迫登录 | 微调 |
| 用户端 | `/u/account/login` | 邮箱+密码登录、注册引导、忘记密码、继续游客使用 | 新增 |
| 用户端 | `/u/account/register` | 邮箱+密码+可选邀请码、接受协议、提交后验证邮箱 | 新增 |
| 用户端 | `/u/account/verify` | 邮箱验证码/验证链接落地页、过期/重发/更换邮箱 | 新增 |
| 用户端 | `/u/account/forgot`、`/u/account/reset` | 申请重置密码、通过一次性邮件凭据设置新密码 | 新增 |
| 用户端 | `/u/account` | 我的资料、已验证邮箱、修改密码、绑定/找回本机游客订单、退出登录 | 新增 |
| 用户端 | `/u/account/security` | 会话设备列表（如后端支持）、退出其他设备、账号安全状态 | 新增 |
| 用户端 | `/u/rewards` | 我的优惠券、失效原因、优惠活动规则 | 新增 |
| 用户端 | `/u/invite` | 专属邀请码、分享文案、邀请记录与奖励状态 | 新增 |
| 用户端 | `/u/order-link` | 高熵订单专属链接兑换入口；无需先登录邮箱 | 新增 |
| 用户端 | `/u/orders`、`/u/orders/:id` | 游客看本机会话订单；注册用户看已绑定账号的订单；链接访客只看该单 | 增强 |
| 管理端 | `ADMIN_BASE_PATH + /customers` | 客户账号列表、验证状态、订单数量、邀请数据，安全查询 | 新增 |
| 管理端 | `ADMIN_BASE_PATH + /customers/:id` | 客户详情、停用/恢复、触发找回邮件、邀请/优惠/订单关联及审计 | 新增 |
| 管理端 | `ADMIN_BASE_PATH + /staff` | 完善管理员/配送员创建、启停、强制重置、权限与会话撤销 | 增强 |
| 管理端 | `ADMIN_BASE_PATH + /settings/mail` | SMTP 服务器参数、发送身份、连接测试、启停与状态 | 新增 |
| 管理端 | `ADMIN_BASE_PATH + /settings/mail-templates` | 邮件模板编辑、默认恢复、变量清单、预览/测试发送 | 新增 |
| 管理端 | `ADMIN_BASE_PATH + /settings/promotions` | 活动、邀请码、优惠券、发放规则及使用规则配置 | 新增 |
| 管理端 | `ADMIN_BASE_PATH + /promotions/records` | 邀请/券发放/使用/冻结/撤销记录、异常审计 | 新增 |
| 管理端 | `ADMIN_BASE_PATH + /orders/:id` | 查询链接生成、复制微信发送文案、重新签发/撤销及访问历史 | 增强 |
| 配送员端 | `/courier/account` | 我的账号、修改密码、验证邮箱（若绑定）、退出与设备管理 | 新增 |
| 管理端 | 个人菜单 | 管理员个人资料、密码、邮箱验证与登出 | 增强 |

路由使用现有项目路由器的等效写法；所有管理子路径均拼接可配置 `ADMIN_BASE_PATH`，不得在用户端公开管理端入口或配置值。支持 360/390/768/1440 像素，管理端桌面优先、用户/配送端移动优先；保持已有品牌色、组件、字号、页面结构。每页均需加载/空/失败/403/429/离线/重试状态。

## 2. 用户端：无强制注册，但用户可随时升级

### 2.1 入口和身份导航

- 首次进入沿用原 `POST /customer/session`，创建 **匿名访客会话**，无需登录表单；首页「我要打印」始终可进入（除原服务停单等限制），不要弹出强制注册遮罩。
- 游客导航显示「我的订单（本机）」「登录/注册」「我的优惠（登录后）」；已注册导航显示「我的订单」「优惠券」「邀请好友」「账号设置」。身份用 `/customer/auth/me` 或共享会话能力端点统一探测，禁止凭 localStorage 中 `isLoggedIn` 判断。
- 游客原有必填订单联系信息 **微信昵称+手机号** 继续保持；它们只是联系字段，不是已验证的账号登录凭据。注册用户可经明确确认自动填入最近的联系信息，但始终允许在订单内修改，并不强制使用账号邮箱作为订单联系方式。
- 访客可打开单订单专属链接，但这**不等同于注册登录**；不要把某一单的授权扩展为全部历史订单。

### 2.2 邮箱注册/登录/验证/找回

- 注册：邮箱、密码、确认密码、**可选邀请码**、协议与隐私提示；输入正确与否由前后端校验，邀请记录在注册时暂存且需验证邮箱后才生效。
- 注册成功先显示「请验证邮箱」，未验证邮箱不得领取注册权益或使用正式账号的跨设备历史订单；邮箱收不到时支持冷却计时后的重发、垃圾箱提示、修改地址。SMTP 暂不可用时公开提示「邮箱注册暂不可用，仍可游客下单」，不得假装已发送。
- 验证后可自动进入已认证账户会话（按后端契约），保留原页面返回目标和当前访客订单合并提示。
- 登录：邮箱+密码，密码显示开关；失败不得泄露账户是否存在；支持忘记密码、重置链接过期/重复使用与重新申请；提供加载/限流/服务故障提示。
- 注销时只退出对应 CUSTOMER 身份，不误操作管理员/配送员会话；敏感查询缓存与 PDF/object URL 必须销毁。
- 账号中心保留「邮件通知偏好」，仅用于邀请奖励等运营邮件；付款成功、签收成功、账号注销等事务性邮件不受订阅开关影响。安全验证与密码找回信始终发送。下单回执与订单进度邮件已停发，避免高频状态邮件推高成本。
- 如需更换已验证邮箱，先发新邮箱验证邮件并在一次性凭证确认成功后更新，原邮箱在确认前继续有效；支持新地址验证的等待、重发和失效状态。必要时使用 `credentials: 'include'` 和后端约定的 CSRF 机制；不可储存密码、验证码、邮箱重置 token、长期 Session Token 到 localStorage/IndexedDB。

### 2.3 游客订单和账号订单的整合

- `/u/orders` 根据实际授权区分三个情况：游客当前会话的订单、注册账号下已归属订单、通过单订单链接临时授权的一笔订单；列表必须分别说明访问范围。
- 当前游客在登录/注册成功时，弹出「是否将这台设备当前游客订单关联到账号」；登录过程保留访客 Cookie 直至用户选择，不得无提示自动合并；确认合并成功后按 0A.2 原子撤销旧游客会话并轮换新主体；仅传有权的匿名会话与显式确认，不能凭微信昵称、手机号、提取码批量认领。
- 用户打开此前保存的单订单链接后，可选择「将这一单添加到我的账号」，只有 **已验证邮箱登录 + 当前有效订单访问授权 + 二次确认** 方可提交绑定；绑定不会自动导入别的游客订单；派生账号授权继承源 grant 的 scope 和到期/撤销约束，不转移 owner。
- 相同订单不能在列表中重复出现；换设备无凭据时提供“联系管理员请求重发链接”，不得承诺输入手机号即可找回全部订单。
- 当前订单的状态、版本、交付记录、PDF 权限仍遵循原始订单契约；登录身份不能改变原订单状态或打印资产版本。

## 3. 管理员手动微信发送：专属订单查询链接

### 3.1 用户端链接交付

- 下单成功页面保留原 **短提取码、应付金额、管理员微信号与加好友二维码**，并额外加入「保存本订单专属查询链接」「复制链接」「如何找回订单」区域；游客也能保存。
- **短提取码只用于线下订单识别**，不作为私密查询凭据；短码/手机号/微信昵称不能替代访问 token。
- 订单链接路由采用 `https://<站点>/u/order-link?orderId=<订单UUID>#token=<高熵token>` 的概念结构；实际 URL 由同源配置与原始 `accessToken` 生成，不在页面示例硬编码域名或 token。`#token` 放在 URL fragment，页面读取后立刻调用 `POST /order-access/exchange`，成功或明确失败时清除地址栏 fragment（`history.replaceState`），把访问会话保存在受控 HttpOnly Cookie 中。
- 在 `order-link` 首次加载时**不要上报第三方埋点、不要预加载外站资源、不要将含 fragment 的完整 URL 记入日志**。响应和页面设 `Cache-Control: no-store`、合适 Referrer-Policy；私有查询页面不进入 PWA 离线缓存。
- 保存提醒写明「链接相当于此订单的访问凭证，请勿分享到群聊/公开平台」，可复制/通过系统原生分享（如浏览器支持）；分享按钮只是用户主动操作，不是自动发送微信。
- 访客链接有有效期或撤销时显示「链接已失效，请联系打印员重新发送」；已生成订单但管理员尚未发送时仍保留原游客本机会话查询能力。

### 3.2 管理端订单详情：一键生成并复制

- 在已有订单详情中新增「订单查询链接」独立卡片，显示：当前有效链接数量、最近签发时间、签发者、状态、有效期；**不展示历史 token 明文**。
- 按钮「生成新查询链接」调用 `POST /admin/orders/{id}/access-links`，成功仅在当前响应中拿到一次性 `accessToken`，构建客户端可复制的完整 URL；给管理员「复制查询链接」和「复制微信联系文案」两个按钮。
- 管理员使用该链接**手动通过微信聊天发送**，不提供微信机器人/微信消息 API；默认联系文案可包含短提取码、打印进度链接及隐私提醒，但不得包含敏感 PDF、交付定位的直接公开 URL。
- 列表支持「撤销此链接」，确认后调用 `POST /admin/orders/{id}/access-links/{grantId}/revoke`；撤销旧链接不删除订单、不影响其他有效链接、不误杀账号本身访问权限。
- `POST` 超时按幂等 key 查询/重试，不在网络不确定时创建无数个 grant；复制失败可手动选择文本。已经离开成功页面后的管理员不能重新显示旧 token，只能新签发。
- 用户下单成功响应若含 `accessToken`，在本次会话可按原规则建立可保存链接；不要尝试从 GET 订单详情重新读取明文 token。

## 4. 邀请码、优惠券、满量折扣与价格展示（纳入本次开发，不是纯预留）

### 4.1 可配置运营规则

- 用户注册时可填邀请码，非必填。验证邮箱后获得后台配置的「新人券」（如果活动启用且符合资格）；**若后台启用「受邀新人额外奖励」活动，成功填写合法邀请码并验证邮箱者可另获受邀人券**（多券可持有，但首版每单最多用一张）；邀请人奖励以被邀请人**第一笔成功送达且实付大于 0 的合格订单**认定；先下单未送达不占资格，零元单不触发，按统一修订条款原子认定。没有对应活动或额度为 0 时展示实际规则，不假装人人有固定金额。
- 新用户自身优惠与邀请人奖励分别计账，避免只注册、不打印就重复刷奖励；默认只允许一个被邀请关系，且不可自邀。邀请奖励是否为满减券、有效期、每日/月上限、最低门槛均以后端活动版本为准。
- 账号「我的邀请」显示邀请码、复制、分享文案、邀请总数、**待首单/已达标/奖励已发/不合格**，只暴露必要统计，不显示被邀请人的邮箱、电话或文件名。
- 「我的优惠券」按可用/已使用/已过期分类，显示门槛、有效期、适用服务及不能使用原因；账户停用、过期、已使用和资格不足不能被前端绕过。

### 4.2 结算页融合原有不可变报价

- 原 `/u/review` 增加「优惠券」可选择面板：游客提示「注册登录后可用优惠券」，但不得阻止游客下单及自动满量优惠。
- 服务端 `quote.lines` 中打印、装订等正常**非负**价格行不变；新增报价汇总 `subtotalCents`、`discounts[]`、`discountCents`、`totalCents`（最终应付价）。配送费 `deliveryFeeCents` 在 V1 仍为 0。
- 选择、取消或变更优惠券，调用 **`POST /preparations/{id}/requote`**（增量请求包含 `promotionIntent`），获取新的、不可变的 `quoteId` 和快照，再显示原价、优惠减免、最终应付；不得仅用前端 `subtotal-discount` 作为权威价格。
- 只改优惠券且最终 PDF hash 未变，沿用已有 `previewAcks`，但**金额变更要求再次明确确认**；改打印设置仍严格按原流程重新生成 PDF 与预览确认。
- `POST /orders` 仍以所选 `quoteId`、最终 PDF 确认为准；提交成功后订单固定报价和已锁定优惠，不应因后台改活动而悄然改变。
- 已付款订单不因后续注册/邀请而追溯减价、自动退款；原 D-01 未批准不得通过优惠操作绕过已付款订单的内容/价格变更控制。
- 用户端订单详情、联系管理员付款文案显示**优惠后应付**；管理员订单确认收款和账目也用同一个订单金额快照。实际支付不等于应付时仍走原人工核对异常，不自动变 PENDING_PRINT。

### 4.3 管理端营销页面

- 活动配置：开启/关闭新客注册券、受邀新人券、邀请人奖励；优惠形式、面额（整数分）、满减门槛、有效期、总预算/总份数、每用户使用次数、每笔最多一张、叠加规则只读显示“首版不叠加”，不可开启、活动说明、生效时间与版本。
- 管理员查看：新注册/邮箱已验证用户数、券发行/锁定/已核销/失效数、优惠让利金额、邀请奖励明细；展示明确统计口径，不能前端假算后台统计。
- 运营管理提供禁用活动、停止发放、对违规券作有理由的冻结/撤销，以及导出权限控制；不得修改已完成订单的价格事实。

## 5. 后台 SMTP 及默认邮件模板：必须提供可操作页面

### 5.1 SMTP 配置 `/settings/mail`

用与现有后台配置一致的表单、版本及保存风格，分区展示：

| 设置组 | 字段 / 交互 |
|---|---|
| 邮件能力 | 启用/停用发送、配置状态、最近测试结果；禁用时前端给出注册/找回不可用的真实提示 |
| 连接设置 | SMTP 主机、端口、连接安全 `STARTTLS` / `SSL_TLS`（隐式 TLS）/ 禁用（仅明确批准的可信环境）、连接超时（后端设置范围） |
| 身份认证 | 用户名、SMTP 授权码/密码（输入与重新设置，**不回显已有值**）、是否启用 SMTP AUTH（如提供方支持） |
| 发件标识 | 发件人名称、发件邮箱、Reply-To（可选）、品牌站点链接来自安全配置 |
| 运维测试 | 「保存设置」「测试连接与身份认证」「发送测试邮件到指定地址」；显示返回时间、结果、去标识化错误类别，不显示机密信息 |

- 填写密码后显示「已配置 / 未配置」或「保持原密钥」，**禁止 GET 返回真实密码/授权码**；编辑空密码字段表示“不改变”，清除必须另行显式操作且受权限限制。
- `保存` 与 `测试发送` 是不同动作：连接成功不代表邮件成功到达收件箱；界面分别区分「已入队」「SMTP 已接受」「发送失败」及“收件箱投递状态未知”，不虚报送达。
- `SMTP_FROM_ADDRESS`、`host` 等都不得写死在前端；密码不要写入 console、错误弹窗、浏览器缓存、URL。
- 显示配置版本冲突 409、权限不足 403、限流 429，提交中按钮防连点；重要修改要求二次确认并写后台审计。

### 5.2 邮件模板 `/settings/mail-templates`

至少支持以下模板，后台首次安装自动写入**安全默认模板**，管理员可编辑主题、纯文本正文与（可选）HTML 正文、启停非关键业务提醒并**恢复默认**：

| key | 模板 | 触发条件 |
|---|---|---|
| `CUSTOMER_VERIFY_EMAIL` | 验证邮箱 | 注册或换绑邮箱请求 |
| `CUSTOMER_RESET_PASSWORD` | 重置密码 | 用户请求重置 |
| `ADMIN_ORDER_CREATED` | 新订单通知（管理员） | 订单创建，发给有接单/打印权限且邮箱已验证的管理员；含客户昵称、电话与添加微信提示 |
| `COURIER_DISPATCH` | 配送派单通知（配送员） | 配送批次发布，只发给该批次的配送员；含日期、时段、订单数 |
| `DELIVERY_UPDATED` | 配送信息变更 | 配送楼栋/时段变更，发给订单客户 |
| `PAYMENT_SUCCESS` | 付款成功 | 订单确认收款，事务性发给客户，不受订阅开关影响 |
| `ORDER_DELIVERED` | 签收成功 | 配送签收，事务性发给客户，不受订阅开关影响 |
| `REFERRAL_REWARD_GRANTED` | 邀请奖励到账 | 邀请奖励发放，按客户订阅偏好发送 |
| `ACCOUNT_DELETED` | 账号注销受理 | 客户提交注销申请，事务性发给客户 |
| `ORDER_CREATED` | 下单回执（已停发） | 保留默认可编辑模板，当前不再向客户发送 |
| `ORDER_STATUS_UPDATED` | 订单进度（已停发） | 保留默认可编辑模板，当前不再向客户发送 |

- 默认文案必须为完整可编辑的中文版本，支持变量例如 `{{brandName}}`、`{{displayName}}`、`{{logoUrl}}`、`{{expiresMinutes}}`、`{{actionUrl}}`、`{{orderCode}}`、`{{orderStatus}}`、`{{rewardText}}`、`{{orderAmount}}`、`{{paidAmount}}`、`{{customerNickname}}`、`{{customerPhone}}`、`{{courierName}}`、`{{batchName}}`、`{{deliveryDate}}`、`{{deliverySlot}}`、`{{deliveryLocation}}`、`{{deliveredAt}}`、`{{orderCount}}`、`{{courierLink}}`、`{{deletionDeadline}}`；变量来自后端返回的 **白名单**，每个模板显示字段说明，不允许管理员通过模板输入任意表达式执行。`{{logoUrl}}` 只能作为 `<img src>` 使用，发送时由服务端替换为本站固定品牌图标，不接受自定义远程图片。
- 提供「变量插入菜单」「预览邮件」「发送测试邮件」「恢复官方默认」「查看最后修改人/时间」「保存版本」。预览仅使用虚拟脱敏数据，不泄露真实用户邮箱、私人文件、订单凭证。
- 验证/重置类关键安全邮件不能被普通运营开关直接禁用而仍开放对应功能；若模板缺失或 mail 不可用，相关用户操作应安全失败，并给出游客模式回退入口。
- 预览应以真实后端渲染/校验输出为准；HTML 仅安全模板能力，禁止脚本、任意远程资源和任意未经转义的用户输入。HTML 与纯文本两版都可发送。

## 6. 三端账号体系补全

### 6.1 管理员端：员工账号

- 继续用原有 `/auth/login` 和 `/admin/staff`，新增/完善员工列表、添加账号、编辑展示名与角色、停用/启用、强制修改密码、撤销全部会话、查看最近登录及状态。
- 可选择员工邮箱并发初始设置/找回邮件；无邮件时，仅由管理员**一次性安全创建临时初始凭据并通过线下渠道交付**（前端只显示一次、不可再次从服务器读取），员工首次登录必须改密码。
- 不添加公开的员工注册入口；普通配送员无权创建或提升员工权限。账号禁用、角色调整即时要求重新鉴权，管理端显示会话已失效。
- 防止删除/停用最后一个有完整管理权限的活跃管理员、自己的误锁定等；按服务器返回的授权能力显示按钮，仍必须由服务器严格校验。
- 管理员「个人账号」可修改密码、设置/验证邮箱、检查会话；SMTP 密码仅有有权配置人员可写、任何人都不能读取明文。

### 6.2 配送员端：个人账号

- 沿用原独立 `/courier/` 登录（不要改为 CUSTOMER 邮箱登录），补充 `/courier/account`：查看用户名/角色/展示名、修改自己的密码、验证邮箱（若支持）、退出当前或所有设备。
- 密码过期/被管理员强制重置时，先进入安全设置流程，配送任务不可操作直至完成；改派/停用后应立即撤销订单操作能力。
- 不增加查看打印原件、后台优惠数据、付款记录、用户账号管理或 SMTP 设置的权限。

### 6.3 用户管理后台

- 管理员可筛选已验证/未验证/已停用、注册时间、订单数、券数、邀请码等；联系方式仅在业务必要时显示并尽量掩码，导出和查看完整个人资料受授权审计。
- 用户详情可以「重新发送验证提示」「发起安全找回邮件」「冻结/解冻」「查看其订单列表（原有管理权限内）」；重发与找回均须权限、原因、限流，不能通过管理端邮件功能泄露密码；**不允许管理员查询用户密码、验证码、重置令牌或直接以用户身份登录**。
- 冻结只影响用户新登录/新下单/使用优惠，已形成订单仍须允许按业务安全规则继续履约并保留审计。不能因为冻结直接取消全部已付款订单。

## 7. 增量 API 对接清单（前后端共享，不独立发明）

> 下面路径与后端补丁第 8 章一一对应。具体 DTO、校验、错误码以共同 OpenAPI 为唯一结构化真相；保留原 API 不改路径和状态枚举。

| 路径 | 用法 |
|---|---|
| `GET /customer/auth/me` | 当前 CUSTOMER 为游客/已验证用户、能力和可领取权益 |
| `POST /customer/auth/register` | 邮箱注册、密码、可选邀请码；先待验证 |
| `POST /customer/auth/verify-email`、`POST /customer/auth/resend-verification` | 验证和重发 |
| `POST /customer/auth/login`、`POST /customer/auth/logout` | 用户账号登录/退出 |
| `POST /customer/auth/forgot-password`、`POST /customer/auth/reset-password` | 找回、重置 |
| `GET/PATCH /customer/account`、`POST /customer/account/change-password` | 账号中心 |
| `PATCH /customer/account/notification-preferences` | 已验证客户更新可选邮件通知订阅设置 |
| `POST /customer/account/request-email-change`、`POST /customer/account/confirm-email-change` | 验证新邮箱后才完成换绑 |
| `POST /customer/account/link-guest`、`POST /customer/account/import-order` | 明确同意后关联本机游客身份或单订单 |
| `GET /customer/account/sessions`、`POST /customer/account/sessions/revoke` | 账号会话安全 |
| `POST /order-access/exchange` | **保留原接口**，单订单高熵令牌兑换授权 |
| `GET /admin/orders/{id}/access-links`、`POST /admin/orders/{id}/access-links` | 管理端查看元数据 / 签发链接 |
| `POST /admin/orders/{id}/access-links/{grantId}/revoke` | 管理员撤销一条订单访问授权 |
| `GET /customer/coupons`、`GET /customer/referrals`、`GET /customer/referrals/code` | 用户优惠与邀请数据 |
| `POST /preparations/{id}/requote` | **扩展原接口**，`promotionIntent` 可选；变更券后重新报价 |
| `GET/PATCH /admin/config/volume-discounts` | A4实际张数门槛与折数档位、启停、版本；自动与券择优 |
| `POST /preparations/{id}/adopt-checkout` | 双会话+confirm及版本，仅本次结算授权，不合并历史游客订单 |
| `GET/PATCH /admin/config/mail`、`POST /admin/mail/test-connection`、`POST /admin/mail/test-send` | SMTP 配置和测试 |
| `GET /admin/mail/templates`、`GET/PATCH /admin/mail/templates/{key}` | 邮件模板列表与更新 |
| `POST /admin/mail/templates/{key}/preview`、`POST /admin/mail/templates/{key}/test-send`、`POST /admin/mail/templates/{key}/restore-default` | 邮件模板预览、测试和恢复默认 |
| `GET/POST /admin/promotions/campaigns`、`PATCH /admin/promotions/campaigns/{id}` | 运营活动管理 |
| `GET /admin/promotions/records`、`POST /admin/promotions/coupons/{id}/revoke` | 运营账目与带理由撤销 |
| `GET /admin/customers`、`GET /admin/customers/{id}`、`POST /admin/customers/{id}/suspend`、`POST /admin/customers/{id}/restore` | 客户管理 |
| `POST /admin/customers/{id}/resend-verification`、`POST /admin/customers/{id}/send-recovery` | 管理员经授权补发验证/重置邮件（限流和审计） |
| `GET/POST /admin/staff`、`PATCH /admin/staff/{id}` | **保留原接口**，扩展员工账号资料/权限与状态 |
| `POST /admin/staff/{id}/send-setup`、`POST /admin/staff/{id}/revoke-sessions` | 员工初始化及会话撤销 |
| `POST /admin/staff/{id}/issue-temporary-password` | 已由N05取消，兼容地址返回410；不在界面提供入口，不签发第二套密码 |
| `GET /auth/me`、`POST /auth/login`、`POST /auth/logout` | **保留原员工认证接口** |
| `GET /auth/sessions`、`POST /auth/change-password`、`POST /auth/sessions/revoke` | 员工自助账号安全 |
| `POST /auth/setup-password`、`POST /auth/forgot-password`、`POST /auth/reset-password` | 员工邀请初始化与凭证找回 |
| `POST /auth/email/request-verification`、`POST /auth/email/verify` | 员工邮箱验证，找回密码的前置条件 |

报价升级保留**无优惠路径的数值兼容**，不是旧客户端的完全透明变更：`quote.totalCents` 继续代表订单最终应付，新增 `subtotalCents`、`discounts[]`、`discountCents`，未使用优惠时折扣 0、总价与原版完全一致。不得修改原 `PriceLine` 的非负商品金额，也不得使配送费从 0 改为收费。

## 8. 契约优先、前端可先实现的实施顺序

### 8.0 现有实现的强制迁移清单（保留界面，不保留错误数据流）

- `src/lib/store.tsx` 的单一 role 与公共 data 改为分域会话/分域查询；账号登录/退出与 grant 兑换不串员工 role。旧路径 `/u/profile`、`/courier/profile` 可重定向新 account 页面，保留书签可用。
- `src/lib/types.ts` 的 Order 补 currentQuoteId、完整报价/折扣、结算类型、checkout 身份与受控授权视图；DTO 以 OpenAPI 生成，不在组件中手写缩水版 quote 断言兼容。
- `src/customer/review.tsx` 移除正式报价的本地原价回退（尤其 `prep.total || total`），就绪前显示加载/错误，0 为合法值；优惠面板只改变 quote，不丢失未变化 PDF 的合法 previewAcks。快速换券使用请求序号/取消旧请求，旧响应不能覆盖最新选择。
- 收款页不再传固定 `quote-v1`，读取 order.currentQuoteId；显示 subtotal/discount/total 与独立零元人工确认。用户订单详情、微信文案和后台统计统一快照，不能一个展示原价另一个收优惠价。
- Review 卸载会丢失组件内 preparation/确认状态，因此结算中登录必须用共享 checkout 状态或不卸载结算的交互；邮件落地页不自动消耗凭证。游客合并轮换前后、登录不同账号、待验证更正邮箱、普通退出均有独立测试。
- 扩充 `docs/openapi.yaml` 后运行项目类型生成命令；生成文件不手改。在后续实施任务同步 README/PRODUCT/设计决策的 D-06 子项状态与待联调说明，不把所有 D 项一并批准。

**阶段 FE-A — 基础与路由（必须先完成）**

1. 审阅现有项目并输出变更影响表；新增 API 接口类型、状态及能力开关、可复用 UI 组件；完善三端登录态区分。
2. 接好 `/u/account/*`、`/u/rewards`、`/u/invite`、`/u/order-link` 与后台新增设置路由；浏览器刷新深链接不能 404。
3. 以 OpenAPI 契约与受控开发 mock 验证空/加载/失败状态，旧下单功能必须原样可用。

**阶段 FE-B — 用户端（本轮重点）**

4. 邮箱注册、验证、登录、忘记密码、个人中心，保留「游客直接打印」入口。
5. 下单成功页保存链接、单订单链接兑换/失效、游客订单合并/单单绑定及订单查询。
6. 邀请码、优惠券、满量折扣档位与服务端自动择优、结算重新报价、邀请码与收益记录。

**阶段 FE-C — 管理与配送端（本轮重点）**

7. 管理员订单「创建/复制/撤销链接」；客户账号列表和详情。
8. SMTP 连接设置、测试发送、邮件模板增改/预览/测试/恢复默认；营销配置、满量折扣配置和记录。
9. 管理员/配送员员工登录、账号设置、密码更改、员工权限与账号管理 UI 完善。

**阶段 FE-D — 与后端统一联调**

10. 对照后端增量 Prompt 提交 OpenAPI 差异表；联调真实邮件及账号安全、游客链接、引用/发券/核销、人工收款、配送完成奖励。
11. 删除生产 mock 回退与调试 token；回归原完整打印→人工收款→装订→装袋→配送链路。

每一阶段直接在现有仓库提交可运行修改，报告变更文件、测试命令、真实测试结果和未实现/后端依赖项。不得用只画页面的截图冒充功能可用；后端尚未接入时可以完成页面和接口层，并明确标识「待真实联调」。

## 9. 必须覆盖的新增验收用例

| 编号 | 用例与判定 |
|---|---|
| FE-ADD-01 | 首次游客打开首页即可打印，不强制登录；微信昵称/手机号原订单字段未消失。 |
| FE-ADD-02 | SMTP 未配置时邮箱注册/找回显示不可用且可游客打印，不能报“邮件发送成功”。 |
| FE-ADD-03 | 未验证邮箱不能领注册奖励或查正式账号跨端历史；验证后权限及时更新。 |
| FE-ADD-04 | 当前游客登录/注册后明确授权关联订单，非本机订单不会因相同手机号/昵称被导入。 |
| FE-ADD-05 | 单订单链接打开后只能访问该单，地址栏 token 兑换后消失；短提取码猜测无权读取私密订单。 |
| FE-ADD-06 | 管理端生成查询链接只显示一次；复制文案后可微信人工发送；撤销后旧链接失效。 |
| FE-ADD-07 | 修改优惠券后 quoteId / 应付金额由后端重新返回，用户重新确认金额；PDF SHA-256 不因此变化。 |
| FE-ADD-08 | 管理端确认付款看到与用户相同的折扣后应付，不符合不能自动确认收款。 |
| FE-ADD-09 | 邀请码非必填、不可自邀；奖励未达标显示待生效；个人邀请页不泄露好友敏感信息。 |
| FE-ADD-10 | SMTP 密码无明文回显，保存/测试/发送状态严格区分；修改表单有版本冲突提示。 |
| FE-ADD-11 | 邮件模板主题/正文预览、插入变量、恢复默认、测试发送可完整走通；测试预览不得使用真实 token。 |
| FE-ADD-12 | 配送员不能访问邮件、营销、员工管理；管理员有配送权限，员工被停用后当前操作被拒。 |
| FE-ADD-13 | 360/390/768/1440 屏幕下账号、卡券、管理配置表单可用，三端 PWA 和私有资源缓存隔离。 |
| FE-ADD-14 | 原 FE-01—FE-20 回归不变：打印、 PDF、地图、人工收款、批次、装袋、交付均正常。 |

满量专项使用本文件 0A.10 的 `FE-VOLUME-01`—`FE-VOLUME-12`，与原验收共同执行。

### 修订专项验收（与原 ADD 用例共同执行）

| 编号 | 场景 | 必须结果 |
|---|---|---|
| FE-FIX-01 | 阶段性链接与单单导入 | 链接待确认可编辑/撤销、配送前可改楼栋，付款后不能改文件、配送后不能改地址；导入不增加 scope，不得到同主体另一订单或原件下载权。 |
| FE-FIX-02 | 源授权撤销/过期 | 已兑换、已导入、已准备未激活的版本均不能借源失效继续读写；真实 owner 权限不受无关链接撤销影响。 |
| FE-FIX-03 | 游客关联与退出 | 合并原子轮换，旧游客 Cookie 在原/其他页面均失效；退出账号后看不到已关联单，独立有效订单链接仍可按 scope 使用；员工会话不被客户退出清除。 |
| FE-FIX-04 | 等待收款时券自然过期/活动停止 | 已 RESERVED 单按快照结算不涨价；AVAILABLE 不再新预约；风控冻结产生人工 blocker，不静默重价。 |
| FE-FIX-05 | 修改/撤销/删除/恢复券预约 | 本单沿用原券、换券、取消用券原子处理；两单抢券不双占；删除释放一次，恢复冲突阻断并要求用户确认重报，不抢另一单已用券。 |
| FE-FIX-06 | 零元与真实报价引用 | 0元总价不回退原价，微信文案不要求转账0元；管理员明确ZERO_PAYABLE才生产，正数CASH_MANUAL校验相符，提交真实currentQuoteId，零元不触发奖励。 |
| FE-FIX-07 | 邀请首单并发及乱序 | 先下单未送达不占资格；第一个合格实付正数交付事务认定唯一qualifiedOrderId，双单并发/事件乱序/重放仍只发一次，事后导入游客单不达标。 |
| FE-FIX-08 | 奖励预算与已用奖励纠错 | 预算不足进入PENDING_BUDGET，不换后续订单；恢复预算幂等补发；已用于另一已付款单的奖励纠错进入人工复核，不改旧单价格或自动扣款。 |
| FE-FIX-09 | 预算与适用服务分摊 | 发行/预约/核销/过期/释放不超预算、不重复返还；仅A4券不抵照片，整数分分摊等于总折扣，叠加开关不可开启。 |
| FE-FIX-10 | 账号/结算切换竞态 | 登录保留合法preparation与PDF确认；切号/退出使不再合法的优惠重报，快速选券A→B旧A响应不覆盖B，旧主体网络响应不回填当前用户。 |
| FE-FIX-11 | 邮件与员工初始化 | 基础账号邮箱更正须证明身份；GET预取不消费token；只对已验证基础账号授予员工权限，不新建员工凭据；撤权、两设备限制和最后超管保护由服务端执行。 |
| FE-FIX-12 | 兼容迁移与敏感保留 | 严格Quote schema及生成类型同步；旧无优惠单discount=0，优惠客户端不兼容明确阻断；授权撤销/会话失效在恢复备份后仍有效，敏感发送载荷不无限保留。 |

## 10. 最终交付要求（交给 Codex 的执行指令）

请**立即在已存在的前端项目上做增量开发**。先读取并对照原 `01`/`02` Prompt 与本 `03`/`04` 补丁；列出真正需要改动的文件，先实现新增页面与交互，保持已完成页面的设计体系。所有按钮必须有状态/错误处理和真实契约接入点；后端暂不可用时只能使用显式开发 mock。新增 UI、接口类型、权限测试、用户路径 E2E 与原有打印回归测试一并交付。不得省略后台 SMTP 及默认邮件内容管理、满量折扣与优惠自动择优、优惠邀请码、客户/管理员/配送员账号管理。

## 前端实现契约补齐记录（2026-10-30）

以下为 0A 规则落地所需的前端/后端共享补充；已同步 04 和 OpenAPI 生成器，真实服务须实现后才能联调使用。

- `POST /preparations/{id}/withdraw-checkout`：请求 `{expectedVersion,confirm:true}`，须同时证明当前 preparation 与其结算账号，未建单且非旧单修订。撤回本次 checkout_use_grant，递增 preparation.version，返回新的游客 Quote。旧 quoteId 不可再提交；未变更的 manifest/PDF/previewAcks 保留。整 principal 已显式关联账号时不能借此变回游客；返回领域错误并重新核验。
- `Campaign`/创建及编辑请求含 `qualificationExpiresAt:string|null`（null 明确表示不限期）、`qualificationMinSpendCents`、`qualificationService:A4|PHOTO|ALL`；仅 REFERRER 用于有效首单资格，独立于奖励券自身使用门槛。验证邀请时保存完整规则快照；预算不足重试仍使用已锁定达标订单与活动集合。
- `GET /admin/dashboard` 返回 `DashboardSummary` 全量统计：订单/生产待办数量、商品原价、优惠券/满量让利、应付、现金实收、零元数量、退款与人工调整。不可从某页订单列表计算全量财务。
- `POST /admin/staff`、`PATCH /admin/staff/{id}` 使用严格员工 DTO，编辑含 expectedVersion，均带 reason；凭据仍走单独安全流程。
- 优惠导出输入为 `{filters:{q?,type?,status?,riskHold?,campaignId?,accountId?,from?,to?},fields,reason}`。模板恢复默认输入为 `{expectedVersion,reason}`。
- 订单视图可返回 `accessKind:OWNER|GUEST_OWNER|ORDER_GRANT|IMPORTED_GRANT`，表示服务端判定的当前有效授权来源；allowedActions 为所有独立有效来源的动作并集再与状态相交。来源受限视图不返回原件 URL，PDF/交付凭证按 scope 裁剪，不因另一个窄链接压制独立所有权/导入授权。

## 本轮新增业务链交付要求（2—7及追加费用，已确认）

- 链接二维码：订单专属链接、邀请链接、管理员合法HTTPS网址均在本地生成、可预览下载；保留fragment和授权边界，不新签发权限/延长到期、不送第三方。
- 批量送达：同一批次、同一楼栋多选订单，一次上传同一张照片并可填共享文字描述；每单独立核验权限/当前执行/版本/照片/定位或审批例外，整批原子提交，重复不重复送达/发券/工资。各客户只可见自己的凭证。
- 文字送达描述选填，单单和批量均展示；没有照片仍必填免拍原因，不代替定位及审批。
- 统一身份与界面：一个PWA、一个普通客户账号登录入口。正常注册客户后由超管给该accountId授予管理员/配送权限，权限跟账号跨设备；个人页仅在有对应权限时显示工作区入口，普通用户不见；所有员工/超管都可返回用户端。取消三个初始入口/三独立PWA/第二套员工登录和密码。保留工作区路由与细分服务端能力；保护最后超管、拒绝自提权和跨客户身份越权，统一账号两台设备额度。
- 员工优惠：最多1—6级，每级配置百分比减免，员工权限有效就长期享有；超管原打印单100%免单，仍人工零元结算。原打印订单含装订整单参加员工折扣，与券/满量三择优不叠加；资格服务端判定，已提交锁价，撤权只影响新报价。
- 工资统计：可选默认/员工覆盖的每单定额或折后实收比例模式；实际送达记给当时配送员并锁策略，按月账本和人工发放记录。退款后按原比例追加调整，不修改已发记录；免费重做/补送不重复计件，可有理由手工补记；定额不随追加费用变化，不自动转账。
- 追加费用：管理员在原单下按笔新增最终金额、必填用途、是否参加比例工资，提交即生效；客户可查看并提异议。原单付款前后/送达后均可追加，新增应收/实收/退款/欠收独立记录，原Quote/收款/履约事实不变。追加金额不参加任何原单优惠；参与比例工资的部分只按真实净收款计算，送达后追加收退款更新原配送员的工资差额，未收/未勾选不计入。
- 前端入口、严格DTO、迁移、授权、并发版本、持久化幂等、收退款/工资审计、导出、删除引用/保留及真实联调全部覆盖，不接受只改Prompt/文案/演示。详细最终答案及收费公式见 `docs/superpowers/specs/2026-10-30-dormprint-v1-adjustments.md`。
