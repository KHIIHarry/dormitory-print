> **本轮业务链调整（2026-10-30，新增2—7项）**：以 `docs/superpowers/specs/2026-10-30-dormprint-v1-adjustments.md` 为本轮增补。纳入链接转二维码、同照片批量送达、选填送达文字、统一应用及个人页权限入口、员工1—6级百分比优惠/超管打印永久免费、定额或比例配送工资。原三套独立PWA/三个初始入口方案取消；身份安全隔离继续保留。N01—N07及F01—F03已确认，金额/身份/交付按最终答案落地，不得遗漏新增模块。

## 2026-10-31 账号与管理修订（以此节为准）

本节对应本轮用户明确的新规则，覆盖旧文中“客户注销须管理员审核”的描述；实现契约以当前 FastAPI OpenAPI 为准。

- 账号注销使用 `POST /api/v1/customer/account/deletion-request`，无需管理员审核。有未完成订单、售后、退款或未结账务则返回 `BLOCKED`，不撤销现有登录；满足条件则返回 `RECOVERY`、`recoveryUntil`，立即退出全部设备并进入 7 天恢复期。正确密码登录在期限内恢复账号，返回 `recovered:true`；期限后禁止通过登录恢复，后台任务按保留与账务规则完成注销。历史审批记录继续兼容，新恢复期记录不能被管理员提前审批。
- 系统初始化账号为唯一超级管理员：基础角色仍为 `ADMIN`，以服务端 `isSuperAdmin` 为准；普通公开注册不自动提权。数据库部分唯一索引限制最多一位，交接必须由现任超管执行，在同一事务先降级原持有人再授予新持有人，禁止先清空唯一超管。现有初始化账号无需重建或重置密码。
- 有效绑定账号的超级管理员享整单 100% 免单，独立于普通员工等级是否配置；未提交旧报价在身份权益变化后必须重新报价，已提交报价保持快照。服务未启用或单价未配置仍须先完成服务配置，免单不能绕过正常报价与最终 PDF 确认。
- 审计返回真实 `createdAt/actorId/actorLabel/targetType/targetId/reason/requestId/data`，提供服务端关键词、操作者、时间范围及游标分页。展示真实已记录变更，不补造缺失事实，敏感字段须脱敏。
- 邀请分享文案包含同站 `/u/account/register?invite=邀请码`，注册页自动预填；最终归因仍由后端校验。链接转二维码工具仅编码网页链接，不生成微信好友、群或收款码，也不授予权限。

详细实现与验证见 `docs/qa/2026-10-31-auth-lifecycle-repair.md`、`docs/qa/2026-10-31-superadmin-benefits-repair.md`、`docs/qa/2026-10-31-config-audit-repair.md`。


> **V1最终定版覆盖（2026-10-30）**：本文件中的旧未决项/冲突规则由 `docs/superpowers/specs/2026-10-30-dormprint-v1-scope.md`（Q01—Q36及R01—R06全部已批准）替代。完整范围见配套V1功能交付矩阵。不得以旧D-02/D-04/D-07未批准为由省略指定纸张补打、按册装订或人工打印队列；Excel/HEIC、分片续传、售后退款、细分权限等均已纳入。自动关单、收款占容量、配送员start、默认只读链接及数据清理必须按新规则执行。

# DormPrint H5 / PWA：后端增量开发 Prompt（统一身份、邮件、订单链接、邀请优惠与满量折扣）

## 统一账号取代旧员工凭据链（本轮最终规则）

基础客户账号是唯一登录主体；Staff仅为accountId关联的角色、能力、等级和超管标记。旧文出现的员工专属注册/密码、临时密码、独立员工会话与STAFF_SETUP/STAFF_RESET页面均作为旧版迁移说明，不再作为V1交付要求；员工改密、邮箱核验和找回走同一客户账号安全链，后台只授予/撤销能力并可撤销该账号会话。游客及单单授权仍独立，不能由角色/同名/手机号推导所有权。每账号两设备合并计数，第三台拒绝；员工高权限7天重新验证不新增另一密码。原有已注册客户、当前有效权限及最后超管保护均需真实服务端检查。


> **文件性质：原《02-后端开发Prompt.md》的增量补丁，不替代原文件。** 将原前端、原后端 Prompt 与新增《03-DormPrint-前端增量开发Prompt.md》一并交给后端 Codex。在原定后端实施阶段**一次性纳入本补丁的表结构、接口、权限、定时任务与测试**，不要先写一套无法扩展的后端再推倒重来。
>
> 版本：`DORMPRINT-IDENTITY-GROWTH-1.3` / 2026-10-29；基础契约：`DORMPRINT-CONTRACT-1`；同源 `/api/v1`；沿用原 JSON `camelCase`、成功/失败包装、UTC ISO 8601、`Asia/Shanghai` 业务日期、金额整数分、版本锁与 `Idempotency-Key`。


## 2026-10-30 前端定稿同步补丁（后端开工必读）

本节记录已经落地的前端交互及本轮用户明确修改，标识 `DORMPRINT-FRONTEND-HANDOFF-2026-10-30`。与旧段落冲突时，本节仅在下述范围优先；基础 `DORMPRINT-CONTRACT-1`、身份营销 `DORMPRINT-IDENTITY-GROWTH-1.3`、其余未批准事项及安全/履约边界不变。配套真实结构以 `docs/openapi.yaml` 为基线，不能把前端内存演示当成已上线服务。

### 1. 图片上传后先选纸张，图片不等于照片纸

- 上传原图完成后，用户逐张选择 **A4 纸打印** 或 **5 寸照片纸打印**；照片入口也不能强制选择照片纸。同单允许混合 PDF、A4 图片和照片纸任务。尚未选择纸张的图片不能进入准备、报价或下单。
- **当前前端已接入的协议**：原图通过 `POST /assets`，用途 `PRINT_SOURCE` 上传。选 A4 时，前端将原图按 EXIF 朝向校正、完整等比居中排成一页 210×297mm A4 PDF，四周预留 10mm，再将此 PDF 作为新的 `PRINT_SOURCE` 资产上传；准备单使用该 PDF 的 `assetId` 和 `kind:'DOCUMENT'`。首次默认彩色、单面、一份，随后可设置黑白/彩色、单双面、份数等文档参数。转换后仍须经过服务端正常的校验、标准化与最终 artifact 生成，不能把客户端生成文件直接当成可信最终 PDF。
- 选 5 寸照片纸时，准备单使用原图的 `assetId` 和 `kind:'PHOTO'`，沿用照片方向、裁剪、四边留白及份数流程。前端保留原图，以便切回照片纸；切换纸张会清除该图片旧设置、旧 artifact 与确认状态。不得把 A4 PDF 当成原始照片再次裁剪。
- `imagePrintMedium:'PENDING'|'A4'|'PHOTO'`、`imageSource` 和本地切换版本是当前前端状态，不是新增的服务端 `kind` 枚举，也没有新增图片专用上传/转换接口。后端不能要求前端尚未发送的原图关联字段才能处理 DOCUMENT；若需要增强来源审计，必须先补齐双方契约再实施。
- A4 图片按 DOCUMENT 的真实用纸与单价计费，计入 A4 满量折扣；5 寸照片按张计费，不计入 A4 满量，装订也不计入该折扣。服务端依据实际标准化文件重新计算页数、份数展开、奇数页空白背面和纸张数，禁止相信客户端金额或页数。单文件切换不能改动其他文件或绕过失效报价检查。
- 前端图片入口接收 JPEG/PNG/WebP，后端需验证 MIME、魔数、可解码性、像素上限和资源配额，并提供真实支持能力；将 WebP 纳入对应测试。HEIC、其他 Office 格式等没有因此默认启用。5 寸照片仍仅接受合法图片，A4 DOCUMENT 仍只接收合法可打印源/标准化 PDF。

### 2. 最终 PDF 的两条入口与确认链

- **查看最终 PDF**：只打开受权预览，不显示确认按钮，不修改 `previewAcks`。
- **点击未勾选的确认框**：打开该任务的确认预览，确认框继续保持未勾选。预览下方显示“**我已确认最终打印格式**”。当前 PDF 成功加载并渲染后按钮才可用；加载中、失败、资源过期、身份变化或 artifact 已替换时不能确认。点击按钮后才记录该 artifact 的确认并关闭预览；关闭窗口/按 Escape 不确认。取消已确认的勾选会撤销该任务确认。
- 多个最终任务逐项确认；全部当前 artifact 均已确认且报价、联系/配送信息等原条件满足，才解锁提交。修改打印参数/纸张、重建 PDF、hash 或内容版本变化须重新确认受影响文件；仅报价变化且 manifest 完全未变时沿用原规则保留 PDF 确认，但应付金额仍须重新明确确认。
- 请求仍为 `previewAcks:[{artifactId,sha256}]`，不新增虚构的“前端按钮已点击”接口。后端在创建/修订事务中逐项校验准备单归属、内容版本、全部当前 artifact ID 与真实 SHA-256，拒绝缺项、重复、其他订单/准备单的 artifact、旧 hash 和未就绪/被撤销资源；不能只比较列表长度。浏览器按钮状态不能证明用户真实阅读，也不能代替服务端授权、金额和一致性检查。
- 最终 PDF 必须不可变且与打印任务下载文件一致；受权二进制读取失败必须返回规范错误，不用示例 PDF、公开存储地址或假缩略图兜底。返回可供预览的同源受权 URL、页数与纸张数；缩略图若另建，应派生自同一最终 artifact，不能成为另一套确认依据。

### 3. 订单备注：独立选填字段，最多 30 字

- 创建 `POST /orders` 和待确认修订 `POST /orders/{id}/revisions` 都增加可选 `customerNote:string`，首尾 trim 后最多 30 字；允许省略或空字符串，不设为必填。超限返回 422，`fieldErrors.customerNote` 给出可显示错误。与 OpenAPI 的 `OrderSubmit`、`RevisionInput` 保持一致。
- 将备注保存到订单及修订快照；有权客户和管理员的订单详情返回 `customerNote`。前端同次应用内返回设置页保留输入；跨刷新仍不承诺恢复私有草稿。不把备注写入公开配置、通用日志或公开检索接口。
- 该字段与管理员内部 `note`、收款备注、异常原因严格分开。修订客户备注不能清空内部备注；内部备注不能泄漏给无权客户。空白备注不额外显示空白区块。纯文本显示并保留换行，禁止按 HTML 执行；备注不会修改打印参数、计价、履约状态或已有权限。

### 4. 后台操作入口收紧，API 语义不变

- 员工每行用 SVG 图标打开：编辑授权/启停、配置等级及细分能力、撤销该账号会话；密码设置和找回统一进入基础账号安全页，取消员工临时密码与独立设置邮件；邮件模板每行用编辑图标进入明确标题的编辑窗口。隐藏文字不改变能力校验、禁用原因、版本锁、危险操作确认及审计。
- 沿用 `/admin/staff/{id}` 及 `send-setup`、`revoke-sessions`、`issue-temporary-password` 等既有接口，继续执行最后管理员、自身保护、会话撤销、首次改密与一次性密钥返回规则；配送员不能因知道 URL 获得管理权限。模板编辑/恢复/测试的输入白名单、样例渲染和 SMTP 受理语义保持不变。
- 管理员微信二维码的**整个虚线上传框可点击，也可拖入一张图片上传**，框内使用用户提供的微信 SVG 原图（`public/assets/wechat.svg`）与简短提示，没有独立上传图标按钮/原生“选择文件”按钮。点击选择与拖拽共用单文件格式校验、上传锁和上传任务；拖入/离开/松开有高亮反馈，无效文件不替换已保存二维码。仍以 `POST /assets`、`purpose:'PUBLIC_WECHAT_QR'` 上传，并按当前前端返回可公开引用的 `url`；上传成功只更新编辑草稿，保存配置成功才发布。公开二维码资产与私有 `PRINT_SOURCE`、最终 PDF、交付凭证严格隔离。后端不能接受 `blob:` 或任意外站 URL 冒充已发布二维码，旧配置在失败时保留。

### 5. 异步、幂等与动效对接边界

- 上传按钮短任务不闪现加载态，超过约 350ms 才显示提示；窗口淡入淡出、后台折叠菜单是前端表现。后端不要为配合动画故意 sleep，也不以某个动画结束/固定秒数作为 READY、付款、打印或上传成功依据。
- QUEUED/PROCESSING/READY/FAILED 以真实任务为准；没有真实进度就不提供伪百分比。资产就绪、准备任务完成、邮件受理/实际投递、导出受理/文件可下载分别表达。服务端始终检查用途、配额、权限、CSRF、当前身份和版本，废弃代际的前端回调不能恢复已撤销权限。
- 断网/超时后显示“结果待核实”不等于已失败。相同幂等 key 与相同请求体应回原结果，不重新建单/上传/签发凭据；不同载荷重用 key 返回冲突。已知任务按现有 ID 查询；本次前端审查仍未发现可靠的“按提交幂等键查询未知结果”或“按上传键查询资产”契约，后端开工时必须明确其查询/恢复方案并写进 OpenAPI，再与前端联调，不能声称当前已有自动恢复能力。
- 取消本地传输不代表 worker 已取消；服务端任务取消/跨刷新恢复若要支持，需先形成明确契约。打印仍须人工确认实体结果；邮件受理不承诺送达，零元单仍需管理员人工确认后履约。

### 6. 同一账号最多同时登录 2 台设备

- 用户新增约束：每个已注册基础CUSTOMER账号（包括已授予ADMIN/COURIER权限的账号）合计最多2个有效登录设备，角色工作区不增加名额。该限制必须在真实后端统一实施，前端隐藏按钮或只检查列表长度不构成限制；匿名游客和仅授予单订单访问的链接会话不是账号登录，不占注册账号设备名额，也不能借此获得账号权限。
- 这里的“设备”按服务端签发的稳定、不透明 device-session 身份计算。一个浏览器配置中的多个标签页共用同一组会话 Cookie，算 1 台；刷新访问令牌、Cookie 轮换、同设备重复登录都更新同一设备记录，不增加名额。不同浏览器配置或清除凭据后重新登录按新设备处理，不依赖 User-Agent/IP/客户端任填 deviceId 来合并或绕过限制，也不新增硬件指纹采集。
- **第三台策略：拒绝新增登录，已有两台保持在线。** 用户凭据通过校验且符合登录条件后，若已有 2 台有效设备，则返回 `409 DEVICE_LIMIT_REACHED`，规范错误消息提示“此账号最多同时登录 2 台设备，请先在已登录设备退出一个设备后重试”。不得偷偷踢出旧设备，不创建可用的新会话、第三组刷新令牌或消耗名额的半成品。普通凭据错误仍走原统一认证失败，避免通过上限错误枚举账号。
- 所有会新建账号登录会话的入口（登录、若会建会话的验证/首次设置/恢复流程等）均复用同一名额服务。发放会话必须锁定账号/设备名额并原子清理已失效记录、校验数量、创建或更新会话；并发三台首次登录最多两台成功。不能先 count 再无锁 insert；refresh-token 轮换、并发重试和幂等回放不得重复增加设备。记录 accountId/principal 命名空间、deviceSessionId、创建/最近活动/到期/撤销时间和审计原因；已到期或被撤销会话不计数。
- 当前设备退出、按 sessionId 撤销和 `ALL_OTHERS` 沿用 `/customer/account/sessions/revoke`、`/auth/sessions/revoke` 及原退出接口。必须即时让旧 access/refresh 会话失效并释放名额；ALL_OTHERS 保留当前设备。密码重置、管理员撤销、员工停用等按原安全规则撤销，受限首次改密员工会话只要属于有效登录也占一个名额。会话预认证、邮箱验证/重置 token 本身不作为登录设备。
- 设备列表只返回本人有权的设备，标记 current，不能泄漏其他账号记录或明文 Cookie/token。前端每行退出使用带可访问名称的 SVG 图标，批量“退出其他所有设备”保持文字按钮；原二次确认、审计和权限守卫继续执行。
- 新增后端验收：第 1/2 台成功，第 3 台 409；同浏览器多标签与轮换不增计数；并发登录无超发；撤销/到期后新设备可登录；旧令牌不能刷新复活；不同账号、客户/员工身份互不串名额；无权者不能撤销别人的设备。该规则本次加入后端开发 Prompt，不把尚未实现的真实设备上限记为前端已完成联调。

### 7. 开工验收与交付

1. 用同一张真实图片分别走 A4/5 寸照片流程，混合订单逐文件计价，切换纸张后旧 artifact/hash 确认失效；不能通过请求伪造种类、金额、页数或资产归属。
2. 直接提交缺失、重复、过期、跨准备单或旧 hash 的 `previewAcks` 均被拒绝；正常查看不自动确认。预览授权撤销或过期时前端不可确认。
3. 备注留空、30 字、31 字、换行及脚本样例均验收；31 字 422，纯文本正确回显，修订保留内部备注。
4. 员工/邮件图标对应原接口，验证无权、并发版本冲突、最后超管、自身撤销和基础账号权限同步；二维码上传、保存失败不能假成功或公开私有打印资产。
5. 验证真实异步、幂等重复、提交超时未知结果、权限撤销和身份切换；补齐未定的恢复接口后同步双方实现。按原要求提供迁移、OpenAPI、实际集成测试、部署说明和仍待联调清单。


## 0. 补丁与原文的优先级、明确批准范围

1. 只对新增身份、SMTP、订单访问链接、营销与用户/员工账号功能产生增量效力。**凡未在本补丁显式改变的原有行为，以原 01/02 Prompt 为准**：不可变最终 PDF、单订单短提取码、报价一致性、人工微信付款确认、生产与装袋、配送员权限、地图/时段、交付照片/定位、回收站 24 小时、各端 PWA 隔离继续保留。
2. 用户本轮已确认：**邮箱注册登录与游客同时保留**；游客下单后可由管理员手动微信发送「单订单专属查询网页链接」；注册可领取优惠、通过邀请码邀请新用户并有奖励；新增后台 SMTP 参数与默认邮件模板编辑；完善三端账号。以上均为本轮**必须实现的业务模块**，不是留空占位。
3. 原 D-06 **部分定版**：批准游客匿名会话、高熵单订单链接、管理员签发/重发/撤销链接，以及当前合法匿名会话向已验证账号明确绑定；不批准「只输入手机号/微信号/短提取码即可公开找回全部历史订单」。丢失凭证可联系管理员，经人工核验后由管理员生成新的单订单链接；不得通过公开找回接口绕过权限。
4. 原 D-01、D-02、D-03、D-04、D-05、D-07、D-08 **不因本次补丁而自动批准**。若与本补丁相关的具体保留期、营销金额或 SMTP 提供方未给出，代码实现安全、可配置的机制，不制造虚构业务金额或默认真实连接凭据。
5. `Role` 依然只有 `CUSTOMER / ADMIN / COURIER`；注册用户与游客只是 CUSTOMER 身份种类，不增加破坏旧权限矩阵的角色。员工仍走原 `/auth/*`；客户另走 `/customer/auth/*`；管理员仍拥有原配送业务能力，配送员绝不能访问员工管理、邮箱管理、促销、付款或原始打印资产。
6. **不接入**微信 OAuth、微信支付、微信机器人、自动发微信、短信验证码、支付平台或自动打印控制。新增 SMTP 只负责邮件注册验证/找回/可选通知，不暗中改变线下人工付款。

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
- 新标签/不同设备不承诺恢复内存草稿；已生成 preparation 只有合法主体可恢复，无法恢复时明确提示重新准备，不能伪造原 previewAcks。登录/退出/切号只重新核验身份和优惠，PDF 未变化不重转。

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

## 1. 先做统一数据模型与迁移：不要推翻原有匿名系统

### 1.1 身份与归属

在原 `customer_sessions`、`staff`、`staff_sessions`、`order_access_grants` 上扩展，不创建并行“第二份订单数据库”。推荐关系：

```text
customer_principals (匿名或可归属订单主体)
    ├── customer_sessions (已有，持久访客 Cookie)
    ├── customer_principal_links (有权主体关联到正式账号)
    └── orders / preparations / assets (继续沿用原 ownerId / ownerType)

customer_accounts (已注册邮箱账户)
    ├── email_verification_challenges
    ├── customer_account_sessions
    ├── account_recovery_challenges
    ├── customer_principal_links (一个已验证账户可关联多个合法游客主体)
    ├── referral_profiles / referral_relations
    └── coupons / coupon_redemptions

staff (原表；ADMIN / COURIER)
    ├── staff_sessions (原表)
    ├── staff_credential_challenges (受邀激活/重置)
    └── 可选已验证员工邮箱及能力标记

order_access_grants (原表；新增签发/撤销/访问范围元数据)
mail_config_versions / mail_template_versions / email_outbox
promotion_campaigns / promotion_grants / referral_events / promotion_ledger
```

**迁移关键点**：

- 若原用户订单/资产 `ownerId` 已指向匿名主体，**不得登录后批量覆盖成邮箱账户 ID**。通过 `customer_principal_links` 建立授权归属，按已验证账号关联的 principal 集合查询，避免破坏历史资产、报价、PDF hash 和订单引用。
- 首次匿名访问沿用 `POST /customer/session`；该会话应关联独立 `customerPrincipalId`；已有用户会话保持可迁移。
- 一个 CUSTOMER 请求只有合法的当前匿名主体或登录的已验证账号主体才能访问相关订单。链接授权是**单订单 scope**，不能自动获得整个 principal 的权限。
- 不使用手机号、微信昵称、订单短码、邮箱文本碰撞作为自动账户合并依据。若游客会话与已登录账户同时存在，必须用户主动确认并在事务中执行，防跨账号关联。
- 所有账号都有 `createdAt/updatedAt/version`，删除/封停使用独立状态与审计；员工账号不能通过公开注册产生。

### 1.2 建议新增/扩展表（具体字段写入最终 ER + Alembic 迁移）

| 表 / 变更 | 必需字段与唯一约束 / 行为 |
|---|---|
| `customer_accounts` | id, emailNormalized (唯一), passwordHash, status=`PENDING_EMAIL/ACTIVE/SUSPENDED`, emailVerifiedAt, displayName?, version, createdAt, lastLoginAt, sessionVersion、可选通知偏好（亦可独立表）；邮箱不公开返回其他用户 |
| `customer_principals` | id、来源和创建时间；原匿名 owner 可直接复用等效 ID |
| `customer_principal_links` | id, principalId, accountId, origin=`GUEST_SESSION/REGISTRATION`（禁止 ORDER_GRANT 关联整个 principal）, linkedAt, verifiedProofRef；避免一主体跨多个账户未经授权重复认领 |
| `checkout_use_grants` | preparationId, accountId, guestPrincipalId, inputManifestHash, scopes, confirmedAt, revokedAt, version；只授权当前草稿的账号结算，不能导入历史订单/修改旧订单owner |
| `volume_discount_policy_versions / volume_discount_tiers` | version/enabled/effectiveAt、tierId/minSheets/payRateBps；重复门槛及倒退折扣拒绝，使用版本快照 |
| `account_order_grants` | id, accountId, orderId, sourceGrantId, scopes, expiresAt, revokedAt, version；单单导入专用，不转移 owner；源 grant 撤销/到期时派生权限失效 |
| `customer_account_sessions` | id, accountId, tokenHash, expiresAt, lastSeenAt, revokedAt, sessionVersion；浏览器仅安全 Cookie |
| `email_verification_challenges` | accountId, tokenHash, purpose, expiresAt, consumedAt, attempts, metadata；一次性；重新签发使旧验证流程按规则失效 |
| `account_recovery_challenges` | accountId, tokenHash, purpose=`RESET_PASSWORD/CHANGE_EMAIL`, expiresAt, consumedAt, issuedAt, attempts |
| `order_access_grants`（扩展） | `orderId`, tokenHash, scope, createdByType/Id, expiresAt, revokedAt, lastUsedAt, createdAt, reason；保留原仅创建响应返回明文 token 的规则 |
| `staff`（扩展） | emailNormalized?, emailVerifiedAt?, mustChangePassword, status/enabled, sessionVersion, lastLoginAt, capabilities?；保留唯一 username 与原 `ADMIN/COURIER` |
| `staff_credential_challenges` | staffId, tokenHash, purpose=`SETUP/RESET`, expiresAt, consumedAt, issuedBy, attempts；不能公开自注册员工 |
| `mail_config_versions` | host, port, securityMode, smtpUsername, encryptedSecretRef, fromName, fromAddress, replyTo?, enabled, version, updatedBy, updatedAt |
| `mail_template_versions` | templateKey, subject, textBody, htmlBody?, version, isDefault, enabled, allowedVariables, updatedBy/At；编辑创建新版本不抹历史 |
| `email_outbox` | id, templateKey, templateVersion, recipientRef, envelopeRecipientEncryptedOrProtected, renderPayloadEncryptedOrMinimal, status, attempts, nextAttemptAt, sentAt, providerResponseCode, lastErrorClass, idempotencyEventId |
| `promotion_campaigns` | id, kind, status, startsAt/endsAt, rulesJsonSchemaValidated, budgetCents?, issueLimit?, perUserLimit?, discountCents, minSpendCents, version, updatedBy |
| `referral_profiles` | accountId, uniqueCode, enabled, createdAt；不可把邮箱、手机号作为邀请码 |
| `referral_relations` | inviteeAccountId 唯一、inviterAccountId、registeredAt、qualifiedOrderId?, status、qualifiedAt；自邀拒绝 |
| `coupon_grants` | id, accountId, campaignId, faceValueCents, minSpendCents, validFrom/Until, status=`AVAILABLE/RESERVED/USED/EXPIRED/REVOKED`, riskHold=`NONE/FROZEN`, reservedOrderId?, version；冻结为独立阻断标记，保留预约/已用事实 |
| `promotion_redemptions` | couponId, orderId, quoteId, amountCents, stage, releasedAt?, consumedAt?, idempotencyRef；同一券不能重复抵扣多单 |
| `promotion_ledger` | id, campaignId, accountId, referralRelationId?, orderId?, eventType, amountCents, createdAt, actor, reason, uniqueBusinessKey；只追加不覆盖 |

SMTP 密钥仅加密存储；验证码、订单令牌、重置令牌的长期验证记录仅存 hash，**禁止明文持久化**。worker 发送及幂等重放可使用限时、加密的凭证载荷，成功发送或保留窗口结束后清除；不能要求从 hash 还原原 token。迁移必须可在原生产数据上运行、回滚方案明确，不删除原引用。

### 1.3 本次修订必须落库的额外事实

- `account_order_grants` 独立于 principal links；外键 sourceGrantId、scope 上限、派生到期和撤权检查不可省。
- `orders` 首次创建的 checkoutAccountId/checkoutAuthKind、currentQuoteId；`quotes` 的 checkoutAccountId、selectedEligibleSubtotalCents、discountAllocations、ruleVersion；`payment_records` 的 settlementKind/cashReceivedCents/discountCents。原游客 ownerId 不批量改写。
- `customer_sessions` 的 principal/sessionVersion/revokedAt 与轮换记录；`pending_registration_sessions` 及 challenge 仅允许待验证更正/重发，不取得正式账号能力。
- 券 riskHold 与状态分离；预算 reservedLiability/realizedDiscount 字段与唯一业务流水；`referral_relations` 增 qualificationSequence、qualifiedOrderId、规则快照及 rewardStatus。交付时锁定资格、outbox 发放，禁止倒过来。
- `staff` 的 PENDING_SETUP/ACTIVE/DISABLED 与 mustChangePassword；受限初始化会话和普通员工会话由中间件逐接口区分，不仅由前端页面守卫。
- 为恢复订单补 REQUOTE_REQUIRED blocker，不增加不必要的主状态；pending 风控阻断不回滚原锁价，零元人工确认也不跳过生产/配送强守卫。

## 2. 三端账号安全与登录体系

### 2.1 客户邮箱注册/登录/找回（CUSTOMER）

- **注册**：`POST /customer/auth/register` 接收 `{email,password,inviteCode?}`。校验长度/格式/强度、邮箱唯一性、限流与注册频率；只创建 PENDING_EMAIL，不直接产生已验证的注册奖励。数据库事务预存邀请归属，但不发奖励。
- **验证**：生成密码学随机的一次性邮箱验证 token，存 hash、设置可配置短期失效时间，通过可靠 outbox 发 `CUSTOMER_VERIFY_EMAIL`；`POST /customer/auth/verify-email` 接收 token 并单次消耗，激活账号、为注册账号创建/确认**独立的账号主 principal** 与登录会话、颁发活动允许的新人券；**绝不在邮箱验证时自动关联当前游客 principal**，须由前端明确提示并经 `link-guest` 二次确认。邮件重复送达不产生重复券。
- **注册服务可用性**：若邮件系统未启用或未具备有效配置，提供公共可用性标识并**拒绝新邮箱注册的生效流程**（或预先明确的可补发 PENDING_EMAIL 策略），绝不能返回“邮件已发送”假成功；游客下单完全不受此故障影响。
- **登录**：`POST /customer/auth/login` 只接受已验证 ACTIVE 的 CUSTOMER，使用成熟密码哈希（推荐 Argon2id，参数按现有运行环境锁定）、限流与必要的统一错误信息，防账户枚举。创建独立客户 Cookie，会话旋转、过期、撤销；不让员工账户通过此接口登录。
- **找回**：`POST /customer/auth/forgot-password` 对不存在邮箱也提供防枚举的统一响应；已存在账号产生一次性短效重置 token，邮件投递入队。`POST /customer/auth/reset-password` 验证 token、改密码并撤销既有登录会话；禁用重复使用和过期 token。
- **账号中心**：查看和修改非认证资料、更改密码（需当前密码或有效重新认证）、查看与撤销登录会话，允许已验证客户自主设置可选订单/邀请通知邮件订阅（默认关闭）。更改邮箱由 `POST /customer/account/request-email-change` 申请新邮箱验证、`POST /customer/account/confirm-email-change` 消耗一次性凭证，成功后才原子切换登录邮箱；不能直接覆盖已验证邮箱。
- 邮箱规范化：明确可信域名大小写规则；不要擅自删除邮箱中的 `.`、`+tag` 或按某个邮箱提供商习惯推断账号相同。
- 私密链接/密码更改/邮箱更改等接口 `Cache-Control: no-store`，CSRF、安全 Cookie、严格 Origin、跨站请求限制、登录/注册/重置/验证码限流，日志不得包含凭证。

### 2.2 关联游客订单与导入单订单

- `POST /customer/account/link-guest`：登录/注册动作必须保留仍有效的游客 Cookie 供用户决定是否关联（不自动丢弃访客身份）；此接口必须同时持有**当前游客授权会话**及已验证 CUSTOMER 会话，并明确提交用户确认；以事务将该 principal 关联到当前账户，保留资产和订单原有 ownerId，同时撤销其旧游客会话并设置全新未关联游客 Cookie；退出账号不恢复旧游客权限。防止把已属于另一个客户的 principal 转绑，冲突返回 409。
- `POST /customer/account/import-order`：必须持有有效 `POST /order-access/exchange` 产生的**该订单专属访问会话**和已验证 CUSTOMER 登录态，并得到针对该订单的二次确认；通过 account_order_grants 为该订单增加继承源 scope、源到期/撤销约束的账号访问授权，**不得自动获得订单原 owner 其他订单**。不要通过“订单 ID + 提取码”执行导入。
- 账号列订单时合并已关联主体的订单与显式导入的订单，稳定排序、去重、分页、对象级鉴权；已取消/已删除/已清理按原策略展示或隐藏。账号冻结不能导致其既有订单被静默取消。
- 首次注册/登录发生在用户已准备 PDF/结算中时，不能擅自重置内容版本；现有 preparation 的归属通过合法 principal link 识别，必要时要求用户明确继续/刷新报价。

### 2.3 管理员/配送员（保留原员工接口）

- 原 `POST /auth/login`、`POST /auth/logout`、`GET /auth/me` **保留 username/password**；客户邮箱注册不能进入该认证域。
- 原 `GET/POST /admin/staff`、`PATCH /admin/staff/{id}` 保留原语义，增加可选员工邮箱、展示名、状态、权限能力、强制改密标记、停用/启用、查会话和撤销会话。角色仍 `ADMIN / COURIER`；权限能力只能缩小/细分管理员权限，不允许 COURIER 获得管理业务能力。
- 任何管理员都应可执行原配送员业务；**仅具备 `staff.manage`、`mail.manage`、`promotion.manage` 等管理能力的 ADMIN** 可管理相应敏感配置；初始安全管理员具备所需管理能力。若现有系统没有细分能力，初版可由全部 ADMIN 执行管理动作，但必须保留管理员身份和完整审计，禁止在 COURIER 开后门。
- 首个管理员只能由原一次性安全初始化流程生成，不允许默认口令或开放员工自注册。新增管理员/配送员由有权限的管理员创建；有邮箱时可发送一次性 `STAFF_INVITE_OR_SETUP` 邮件设置密码；无邮箱或邮件不可用时由 `POST /admin/staff/{id}/issue-temporary-password` 生成一次性随机临时凭据，管理员**仅看到一次**、带外传达并强制首次改密；仅存 hash、限时有效、不可从 GET 重新读取。
- 员工可自助修改密码、查看/撤销自身会话，绑定邮箱后可通过验证流程找回；管理员强制重置、改角色、停用、撤权时立即递增 `sessionVersion`/撤销会话；不得导致「被改派配送员还能提交旧 proof」。
- 后端强制保护最后一个有效、拥有完整管理员能力的账户不被停用/删除/降权；不能依赖前端按钮禁用。
- 邮箱密码与员工密码使用独立命名空间和会话 Cookies，可同浏览器并存，但 CUSTOMER 身份绝不能访问 `/admin`、`/courier` 员工接口；权限每次按授权对象检查，不因在同一浏览器出现其他 Cookie 而混用主体。

## 3. 管理员签发订单专属链接（不等于 OAuth）

沿用原 `POST /orders` 在创建成功且有权的上下文中返回一次 `accessToken`，以及 `POST /order-access/exchange` 的 `{orderId,accessToken}`。增量添加后台签发/撤销能力：

```text
订单已创建，原短 pickupCode 用于线下匹配
    ↓
管理员打开订单 → [生成专属查询链接]
    ↓
POST /admin/orders/{id}/access-links
    ↓
后端生成 >=128-bit CSPRNG token，仅存 hash，返回 grantId + token（仅本次）
    ↓
前端在 HTTPS 同源下生成 /u/order-link?orderId=...#token=...
    ↓
管理员复制并通过微信人工发送；不调用微信 API
    ↓
用户打开链接 → POST /order-access/exchange → 限定此单的 Cookie 会话
    ↓
用户查看本单状态、应付快照、配送凭证；不能查看其他单/整个账号
```

- 新签发字段：`scope=['ORDER_VIEW','FINAL_PDF_PREVIEW','DELIVERY_PROOF_VIEW','ORDER_REVISE_PENDING','ORDER_CANCEL_PENDING','ORDER_CHANGE_LOCATION_PRE_DELIVERY','ORDER_IMPORT']`；这是用户已确认的单订单阶段性委托权限，详见统一修订条款。没有订单所属账号权限、原件下载或其他订单权限；每次动作仍须校验有效 grant、订单状态、版本与依赖。
- 为 grant 设置可配置的到期时间、每单有效数量上限与单管理员签发速率；管理员可撤销，普通 GET 只返回 `grantId,createdAt,expiresAt,lastUsedAt,revokedAt,createdBy` 元信息，不返回 token 明文。
- `POST /admin/orders/{id}/access-links` 需管理员登录、有权限、幂等与审计；响应仅首次包含 token。断网重试遵循原幂等响应加密回放窗口，不产生大量重复链接。`POST /admin/orders/{id}/access-links/{grantId}/revoke` 使用 reason、expectedVersion（或明确 grant.version）与幂等。
- URL fragment 仅前端读取；绝不将 token 写入 URL 查询字符串、服务器 access log、Referrer、第三方脚本、错误日志、统计事件。交换成功后前端移除 fragment；响应授权安全 Cookie，原 token 不持久化到 localStorage。兑换失败统一错误并限流。
- 原短 `pickupCode`、微信昵称、手机号、手机号后四位、订单 ID **均非查询授权材料**。管理员手动核验补发是一项运营动作，但补发后的令牌依然只授权该单，不是批量账号找回。
- 订单撤销后禁止修改/再次撤销，仍允许有效链接查询撤销结果；删除/清理、管理员主动撤权时按原业务权限规则失效；用户可在未到期的合法链接下查看本单最新状态，不因配送改期换订单版本就无故失效。

## 4. 邀请码、优惠券与满量折扣：与原报价/人工收款完整一致

### 4.1 运营规则（后台可配置，代码全部实现）

- `REGISTRATION_BONUS`：已验证邮箱的新客户可领取新人券；**金额/门槛/有效期由管理员配置**，无活动时不发券；匿名游客不享受账号券，但可正常下单并享自动满量优惠。
- `INVITEE_BONUS`：若后台单独启用「受邀新人奖励」，填写有效邀请码且验证邮箱后的受邀用户可领取额外券；与普通注册券分账，多券可持有但首版单笔订单最多使用一张，不叠加。
- `REFERRAL_REWARD`：每个已验证客户有唯一邀请码；被邀请新用户注册时可填，**非必填**，每个被邀请人最多登记一次、禁止自邀。邀请关系经过邮箱验证后有效，但不会立即奖励邀请人。
- **合格邀请完成条件**（本轮统一规则）：被邀请人以该**已验证注册账户身份**创建的订单中，第一笔成功送达且已人工确认实付大于 0 的合格订单，才认定邀请达标；先下单未送达不占资格，零元结算不达标。认定须与交付事务原子记录，不能由乱序 outbox 消费顺序决定。访客先打印、事后导入的订单不用于回溯领取邀请奖励。判定首单不能靠用户点击“已付款”，不允许取消/未交付单提前触发。
- 每活动可设置：启用/暂停、活动区间、面额 Cents、门槛 Cents、券有效期、总预算/总券数、每账号数量限制、叠加规则（首版**固定单订单最多一券、不叠加**，后台不可开启）、适用服务、邀请成功奖励规则。配置初始禁用/无金额，**不预设真实业务优惠价格**。
- 默认发「可用于后续订单的优惠券」，不是可提现余额，不接入现金红包或微信转账；资格、核销和运营原因全部可审计。支持后台对违规券冻结/撤销及失效说明，禁止管理员静默修改既有已付款订单金额。

### 4.2 与不可变报价的兼容设计（必须严格按此实现）

原打印/装订正向行算法不变，但最终应付公式明确升级为商品小计减折扣。无优惠路径保持原数值；旧严格 schema 与客户端必须按本次 OpenAPI diff 升级，不能宣称完全透明兼容：

```ts
// 新增类型；原 PriceLine 保留非负 unitPriceCents/amountCents
interface PromotionIntent {
  selectionMode?: 'AUTO'|'NO_COUPON'|'SPECIFIC_COUPON';
  couponGrantId?: string | null; // 仅账号券受账号限制；null只排除券，不关闭满量
}
type DiscountApplied =
  | {kind:'COUPON';couponGrantId:string;campaignId:string;amountCents:number;description:string}
  | {kind:'VOLUME_TIER';policyId:string;policyVersion:number;tierId:string;
     a4Sheets:number;payRateBps:number;amountCents:number;description:string};
interface VolumeDiscountCandidate {
  policyId:string;policyVersion:number;tierId:string;minSheets:number;a4Sheets:number;
  a4SubtotalCents:number;payRateBps:number;discountCents:number;
}
interface QuoteV13 {
  id: string;
  priceVersion: number;
  expiresAt: string;
  lines: PriceLine[];                   // 继续为打印/装订等正向收费明细
  subtotalCents: number;                // sum(lines.amountCents)
  deliveryFeeCents: 0;                  // V1 不变
  discounts: DiscountApplied[];        // 最多一个优惠来源：COUPON 或 VOLUME_TIER
  discountCents: number;                // 0 <= discount <= subtotal
  totalCents: number;                   // 最终应付 = subtotal + deliveryFee - discount
  promotionRuleVersion?: string | null;
  selectionMode:'AUTO'|'NO_COUPON'|'SPECIFIC_COUPON';
  selectedPromotionType:'NONE'|'COUPON'|'VOLUME_TIER';
  checkoutAccountId:string|null;
  selectedEligibleSubtotalCents:number;
  volumeDiscountCandidate:VolumeDiscountCandidate|null; // 完整字段按0A.10生成OpenAPI
  discountAllocations:{lineKey:string;relatedId:string;amountCents:number}[];
}
```

- **不要增加负金额的 PriceLine**，保持原 `price_items/quote_lines/payment_records` 的非负 CHECK 约束；折扣独立快照。未采用任何优惠（含未命中满量）时 `discounts=[]`、`discountCents=0`、原 `totalCents` 保持不变；未选券但命中满量时仍须返回 VOLUME_TIER 折扣，不能当作无优惠。
- 原 `POST /preparations/{id}/requote` 添加**可选** `{promotionIntent?:{selectionMode?:'AUTO'|'NO_COUPON'|'SPECIFIC_COUPON',couponGrantId?:string|null}}` 请求字段。校验当前身份、账号券归属/资格、活动规则、报价版本，返回**新不可变 quote**；未传 promotionIntent 保持既有 selectionMode，继续过期重报；默认新结算 AUTO，显式 null 仅不使用券仍算满量。若仅优惠变动，最终 PDF 原 `artifactId + sha256 + contentRevision` 不变，不强迫重新转换文件，但**金额变化要重新确认**。
- `POST /orders` 的 `OrderSubmit` 沿用原字段及确认列表，并按本文件 2026-10-30 同步补丁增加可选 `customerNote`（最多 30 字），继续通过 `quoteId` 确认选券后的不可变报价；前端不得提交自定义抵扣金额。提交事务锁定券与活动额度，做重复资格校验，生成订单+报价快照+券 `RESERVED` 记录；重复提交/并发不同订单抢同一券只能一个成功。
- 只有所选优惠为 COUPON 的创建订单（PENDING_CONFIRMATION）才保留券，不因等待人工付款而提前核销；在原 `POST /admin/orders/{id}/confirm-payment` **人工正数收款应收实收严格相等，或明确人工确认零元结算**且订单成功进入 PENDING_PRINT 时，原子将券 `RESERVED→USED` 并记消费账。用户在待确认阶段合法撤销时释放券（但不恢复已过期/已撤销/活动规则限制的券）；支付已经确认则不自动退款/重算。
- 若管理员人工确认的金额与 `order.totalCents` 不一致，严格走原收款核对异常，不自动按原价转打印。任何历史付款确认、订单详情、管理端统计都必须使用下单时的**折扣后应付快照**；原价/折扣分别展示供对账。
- 改订单打印参数仍按原 `revisions`、新准备、新 quote、旧订单保留至原子切换；待确认修改可能需先释放旧券预约、原子预约新券，版本/券变更冲突返回 409。对处于已付款及后续状态的订单，不允许利用促销模块做内容/价格后门；原 D-01 未批准仍阻断。
- 邀请资格在成功交付事务中原子认定并写 qualifiedOrderId，`DELIVERED` outbox 仅按既定资格派发奖励，`uniqueBusinessKey` 唯一，事件重放或配送凭证重复提交不产生二次发券；退回纠错/异常通过**追加撤销或冻结账目**处理，不删除原奖励历史。

### 4.3 反作弊与公平性

- 限制活动期内每用户奖励次数和全活动预算，并基于真实数据库唯一约束防并发重复发券、重复注册领取、同单多次抵扣；同邮箱/同邀请码自邀必须拒绝。
- IP、设备指纹可以作为有风险的安全信号，不能当唯一用户身份；不默认采集侵入性设备指纹。管理员可冻结疑似作弊奖励，操作原因进入审计。
- 分享文案不得把用户邮箱、电话、打印文件名发给被邀请人；邀请码仅用于推广，不是账号密码或订单访问凭据。

## 5. SMTP 管理后台必须真正可用（含测试、密钥、安全状态）

### 5.1 配置模型

在原 `/admin/config` 强类型配置框架下增加独立 `mail` section，同时提供有权限的 `/admin/config/mail` 管理接口。字段：

```ts
type MailSecurityMode = 'STARTTLS' | 'SSL_TLS' | 'NONE';
type MailConfigPublicAdminView = {
  enabled: boolean;
  host: string | null;
  port: number | null;
  securityMode: MailSecurityMode;
  smtpAuthEnabled: boolean;
  username: string | null;
  hasSecret: boolean;       // 永远不返回 password/authorizationCode 明文
  fromName: string | null;
  fromAddress: string | null;
  replyTo: string | null;
  connectionTimeoutSeconds: number;
  configurationStatus: 'UNCONFIGURED'|'READY'|'INVALID'|'DISABLED';
  version: number;
  lastConnectionTest?: {testedAt:string; status:'PASS'|'FAIL'; errorClass?:string};
  lastSendTest?: {testedAt:string; status:'ACCEPTED'|'FAILED'; errorClass?:string};
};
```

- 管理端允许设置 SMTP Host、Port、`STARTTLS` / `SSL_TLS` 隐式 TLS、AUTH 账号、SMTP 授权码/密码、发件名称/邮箱、可选 Reply-To、连接超时、启停。明确启用 STARTTLS 时必须验证服务端证书并强制升级；`NONE` 默认不允许公网环境使用（只有明确受信任的内网部署决策可开放）。TLS 证书校验不能被普通后台开关随意关闭。
- 邮箱服务商（QQ 邮箱、163、企业邮箱等）参数由管理员自主填写，不在前后端写死某提供方，不假设个人邮箱一定能作为 SMTP 服务使用；供应商要求的独立授权码须在界面说明。
- 管理配置中的密码只以服务端密钥加密保存，数据库只保存 `encryptedSecretRef` / 加密 ciphertext；主加密密钥取受控环境/KMS，**不可通过 API 导出明文、不得写在代码仓库、配置审计或异常日志**；读取返回 `hasSecret`。替换密钥只允许显式请求；空值默认维持原密钥，显式清除需二次确认和权限校验。
- 防 SMTP 配置成为 SSRF 通道：验证 host、端口、DNS 解析/最终连接 IP，默认拒绝环回、私网、link-local、metadata 地址和内网敏感服务；如部署须连接内部邮件网关，由受限运维 allowlist 明确授权。限制连接超时、重定向/代理、单测发送地址、每管理员测试频率；test-send 不接受任意批量收件人或恶意邮件头。
- 配置更改按原 `expectedVersion` / 幂等/审计版本处理；只管理员拥有 `mail.manage` 能读非密钥配置及写操作，配送员和客户 403。公开 `/public/config` 最多只暴露 `customerEmailAuthAvailable` 这类**布尔能力**，不可暴露主机/用户名/模板/发件密钥。

### 5.2 测试发送与可观测性

- `POST /admin/mail/test-connection` 真实执行 DNS、TCP/TLS、SMTP EHLO、必要 AUTH 检查（按配置），返回去敏错误类别如 `MAIL_DNS_FAILED/MAIL_TLS_FAILED/MAIL_AUTH_FAILED/MAIL_CONNECTION_FAILED`，不会输出 SMTP 密码、完整会话记录。
- `POST /admin/mail/test-send` 只发送到当前有权限管理员明确输入且通过验证的测试地址，调用**同一发送管道**并返回 `emailJobId/status`；提示 `ACCEPTED` 仅表示 SMTP 服务器接受，不证明到达收件箱。
- SMTP 故障不阻塞游客下单、管理员线下收款、打印、装袋、配送；邮件必须走可靠数据库 outbox + worker（可复用原队列机制），设有限重试、指数退避、超限告警和「待投递/服务器已接受/失败」状态，不直接在业务事务中长时间同步发 SMTP。
- 注册/密码找回等关键业务必须给用户安全、真实的状态：接口请求被接收不等于已投递，失败可有限重发或提示配置不可用，避免因邮件失败导致不可恢复账号；内部运维可看真实状态，公开端避免可用于枚举邮箱的细粒度投递查询。
- 发送队列监测积压、失败分类、模板 key、时间、发送方/收件方脱敏摘要；严禁日志/监控包含一次性 actionUrl、邮件正文中的凭证或完整原文档信息。

## 6. 后台「默认邮件内容」与模板编辑系统：必须实现，不只是字符串常量

### 6.1 必须预置的可编辑模板

系统首次部署/数据迁移 seed 以下中文默认模板，保留原始系统默认版本，可在后台编辑主题、纯文本正文、可选安全 HTML 正文、预览、测试、恢复默认、版本与最后修改人。**不得以删除模板、空白主题作为正常可用状态**。每种模板允许变量在代码层白名单限定：

| key | 默认主题 | 必需变量 | 业务触发 |
|---|---|---|---|
| `CUSTOMER_VERIFY_EMAIL` | `{{brandName}}｜请验证您的邮箱` | brandName, actionUrl, expiresMinutes | 账号注册/邮箱变更确认 |
| `CUSTOMER_RESET_PASSWORD` | `{{brandName}}｜重置账号密码` | brandName, actionUrl, expiresMinutes | 重设密码 |
| `STAFF_INVITE_OR_SETUP` | `{{brandName}}｜设置员工账号密码` | brandName, actionUrl, expiresMinutes, roleName | 管理员邀请员工 |
| `STAFF_RESET_PASSWORD` | `{{brandName}}｜员工账号密码重置` | brandName, actionUrl, expiresMinutes | 员工重置 |
| `ORDER_CREATED` | `{{brandName}}｜订单 {{orderCode}} 已创建` | brandName, orderCode, orderStatus, orderLink | 已订阅的注册客户订单回执 |
| `ORDER_STATUS_UPDATED` | `{{brandName}}｜订单 {{orderCode}} 进度更新` | brandName, orderCode, orderStatus, orderLink | 已订阅的注册客户订单状态更新 |
| `REFERRAL_REWARD_GRANTED` | `{{brandName}}｜邀请奖励已发放` | brandName, rewardText | 已订阅注册客户奖励通知 |

**默认纯文本正文**（种子数据须真实包含，不可只生成 key 名）：

```text
[CUSTOMER_VERIFY_EMAIL]
您好！感谢使用 {{brandName}}。
请通过以下链接验证您的邮箱：
{{actionUrl}}
此链接将在 {{expiresMinutes}} 分钟后失效，且只能使用一次。
如果这不是您的操作，请忽略本邮件。

[CUSTOMER_RESET_PASSWORD]
您好！我们收到了您的 {{brandName}} 账号密码重置请求。
请通过以下链接设置新密码：
{{actionUrl}}
链接将在 {{expiresMinutes}} 分钟后失效，且只能使用一次。
如果不是您本人申请，请忽略本邮件。任何工作人员都不会索要您的密码。

[STAFF_INVITE_OR_SETUP]
您好！您已受邀成为 {{brandName}} 的 {{roleName}}。
请打开以下一次性链接设置员工账号密码：
{{actionUrl}}
链接将在 {{expiresMinutes}} 分钟后失效。
若您不了解此次邀请，请联系项目管理员，不要设置密码。

[STAFF_RESET_PASSWORD]
您好！这是您的 {{brandName}} 员工账号密码重置链接：
{{actionUrl}}
链接将在 {{expiresMinutes}} 分钟后失效，且只能使用一次。
如非本人申请，请立即联系管理员。

[ORDER_CREATED]
您好！您在 {{brandName}} 的订单 {{orderCode}} 已提交。
当前状态：{{orderStatus}}
您可通过以下入口查看订单：{{orderLink}}
请按页面指引联系打印员并完成线下付款，订单不会自动确认为已付款。

[ORDER_STATUS_UPDATED]
您好！您的 {{brandName}} 订单 {{orderCode}} 状态已更新：{{orderStatus}}。
查看订单：{{orderLink}}
实际配送时间请以最新订单页面为准。

[REFERRAL_REWARD_GRANTED]
您好！感谢您邀请朋友使用 {{brandName}}。
您获得的奖励为：{{rewardText}}
请登录「我的优惠券」查看使用条件、有效期与活动说明。
```

- **模板来源不等于可执行代码**：仅实现 `{{approvedVar}}` 变量替换，禁止任意模板表达式、任意代码、读取服务器环境、网络请求、JS、未授权 HTML 标签。纯文本防邮件头注入；HTML 强制安全转义用户值/有限安全标签，不允许远程图片、第三方脚本或跟踪像素。渲染前校验所需变量与格式，测试预览使用样例脱敏变量。
- 系统安全邮件（验证、密码重置、员工初始化）即使管理员改模板，也必须维持必要的一次性操作链接、安全声明与有效期。若必需变量被删除，更新返回 422，不能保存一份无法找回账号的坏模板。
- 预览/测试邮件均使用单独安全测试变量及特定测试 token（不代表真实可操作凭据），不能误触发真实邮箱验证或生成账户。模板版本写入 `email_outbox`，已经排队的消息按其已快照模板/变量发送，不因后台新编辑意外改内容。
- 邮件通知只发送给**已验证邮箱且明确订阅**的账户；订阅默认关闭，通过 `PATCH /customer/account/notification-preferences` 管理，并按最后一次同意时间与类别审计；订单消息不能默认向游客手机号/微信号发送邮件，也不能自动发管理员微信。订单邮件的 `orderLink` 使用已登录用户入口或有权限的有限信息页面，**不能发送管理员用的单订单高熵链接给无关收件人**；不在邮件内嵌原文档、完整电话、定位坐标或交付照片。
- `mail_template_versions` 支持 edit、preview、test-send、restore-default、启停**非关键**运营模板。关闭订单通知不能关闭验证/找回安全邮件，除非同时安全关闭依赖它们的注册/找回能力。

## 7. 权限矩阵新增项与数据保护

| 行为 | ANONYMOUS CUSTOMER | REGISTERED CUSTOMER | COURIER | ADMIN |
|---|---|---|---|---|
| 不注册下单与本机会话订单 | 自己会话 | 自己已授权的订单 | 否 | 原管理能力 |
| 保存/兑换本单专属链接 | 凭合法令牌，仅该单 | 凭合法令牌，仅该单，可显式导入 | 否 | 可审计签发/撤销 |
| 注册/登录/验证邮箱 | 可发起 | 本账号修改/找回 | 员工域另行处理 | 员工域另行处理 |
| 查看账号历史订单 | 否；只看自己访客会话 | 自己关联的订单 | 否 | 原管理范围 |
| 领取/使用优惠和邀请好友 | 否 | 符合资格时是 | 否 | 可管理活动，不以客户身份使用 |
| 看邮件 SMTP 配置/模板 | 否 | 否 | 否 | 需 `mail.manage` |
| 管理客户/冻结账户 | 否 | 否 | 否 | 需相应管理权限 |
| 管理员工/撤销他人会话 | 否 | 否 | 否 | 需 `staff.manage` |
| 调度/收款/打印/装订/装袋 | 否 | 否 | 否 | 按原矩阵 |
| 查看/提交本人分配的配送任务 | 否 | 否 | 按原矩阵 | 保留全部原配送能力 |

- 用户会话、员工会话、订单链接会话分别绑定明确主体/权限；认证中间件必须按路径和作用域选择，不允许错误的 Cookie 自动提升权限。
- SMTP 密钥、重置令牌、邮件消息正文、邀请码奖励账本不得进入公开 `/public/config`；敏感 GET 用 `no-store`。员工、管理员、用户更新身份后，立即更新或清理相关会话缓存。
- 管理员查看客户联系信息仅为订单履约/风控，支持按管理能力裁剪和审计；普通员工/配送员不会因为知道一个 email 就能找回或篡改账号。用户导出/删除与隐私保留遵守已批准的业务策略，不绕过原 D-03 / D-05。

## 8. 增量 REST 接口统一清单（必须同时写入 OpenAPI 3.x）

仍使用原成功 `{data,requestId}` / 失败 `{error:{code,message,fieldErrors?,details?},requestId}`；已有路径不删除，新增 API 按以下表对接前端《03》；重要写操作使用幂等及条件版本，不需要版本的新建/一次性令牌消耗需有原子一次性检查。所有错误不得泄漏账号存在性。

### 8.1 客户认证及绑定

| Method / Path | 请求或主要响应（简化，最终由 OpenAPI 完整定义） |
|---|---|
| `GET /customer/auth/me` | `{role:'CUSTOMER',authKind:'ANONYMOUS'|'REGISTERED',account?:{id,emailMasked,verified,status},capabilities}` |
| `POST /customer/auth/register` | `{email,password,inviteCode?}` → 安全待验证响应；需要邮件可用 |
| `POST /customer/auth/verify-email` | `{token}` → 一次性验证，激活 ACCOUNT，会话按合同建立 |
| `POST /customer/auth/resend-verification` | `{email}` → 安全统一响应、限流 |
| `POST /customer/auth/login` | `{email,password}` → 安全客户登录会话 |
| `POST /customer/auth/logout` | 注销当前客户会话 |
| `POST /customer/auth/forgot-password` | `{email}` → 防枚举响应 |
| `POST /customer/auth/reset-password` | `{token,newPassword}` → 消耗 token，撤销旧会话 |
| `GET /customer/account`、`PATCH /customer/account` | 已验证账号资料、修改可修改字段 `{expectedVersion,...}` |
| `POST /customer/account/request-email-change`、`POST /customer/account/confirm-email-change` | 新邮箱重新验证并原子换绑；旧邮箱在此之前保持有效 |
| `POST /customer/account/change-password` | 当前密码认证、修改密码、会话轮换 |
| `PATCH /customer/account/notification-preferences` | `{expectedVersion,orderCreated?,orderStatusUpdated?,referralReward?}`，仅已验证客户可设置 |
| `POST /customer/account/link-guest` | `{confirm:true,guestSessionProof?}`；同时持有两种安全会话，原子归属绑定 |
| `POST /customer/account/import-order` | `{orderId,confirm:true}`；同时持有已登录账户+该订单限定授权 |
| `GET /customer/account/sessions` | 本账号活跃会话裁剪列表 |
| `POST /customer/account/sessions/revoke` | `{sessionId|'ALL_OTHERS'}` 安全撤销 |

### 8.2 订单访问链接

| Method / Path | 作用 |
|---|---|
| `POST /order-access/exchange` | **保留原接口** `{orderId,accessToken}`，兑换仅单订单 Cookie 权限 |
| `GET /admin/orders/{id}/access-links` | 当前订单访问授权元数据（无原 token） |
| `POST /admin/orders/{id}/access-links` | `{scope?,expiresAt?,reason?}`，高熵 token 仅创建/受保护幂等重放返回 |
| `POST /admin/orders/{id}/access-links/{grantId}/revoke` | `{expectedVersion,reason}`，撤销单条授权 |

### 8.3 促销、报价与管理

| Method / Path | 作用 |
|---|---|
| `GET /customer/coupons` | 本账号券列表及适用状态 |
| `GET /customer/referrals` | 本账号邀请统计和状态（脱敏） |
| `GET /customer/referrals/code` | 本账号唯一邀请码、分享配置 |
| `POST /preparations/{id}/requote` | **原接口增量**：selectionMode及couponGrantId按0A.10，自动比较满量/券，返回新quote |
| `GET/PATCH /admin/config/volume-discounts` | 版本化满量档位/启停配置，只影响新报价，不重价旧单 |
| `POST /preparations/{id}/adopt-checkout` | 双会话证明+expectedVersion/confirm:true，只授权本次preparation的合法账号结算 |
| `GET/POST /admin/promotions/campaigns` | 活动列表/新建，金额和规则严格校验 |
| `PATCH /admin/promotions/campaigns/{id}` | 版本化编辑、启停、预算/时段控制 |
| `GET /admin/promotions/records` | 按用户/活动/状态检索发放、使用、奖励和冻结流水 |
| `POST /admin/promotions/coupons/{id}/revoke` | `{reason,expectedVersion}`，不能抹除已核销历史 |

### 8.4 邮件配置、模板与测试

| Method / Path | 作用 |
|---|---|
| `GET /admin/config/mail` | 仅去密钥版配置及 version |
| `PATCH /admin/config/mail` | 版本化更新合法字段，secret 可更新/显式清除，不回显明文 |
| `POST /admin/mail/test-connection` | 真连接/校验及脱敏结果 |
| `POST /admin/mail/test-send` | 发一封安全测试信（指定且受限制的目的地址） |
| `GET /admin/mail/templates` | 模板列表/状态/版本/变量说明 |
| `GET /admin/mail/templates/{key}`、`PATCH /admin/mail/templates/{key}` | 完整模板读取、编辑及校验 |
| `POST /admin/mail/templates/{key}/preview` | 样例变量预览（不能触发真实凭证） |
| `POST /admin/mail/templates/{key}/test-send` | 使用安全样例发送测试信 |
| `POST /admin/mail/templates/{key}/restore-default` | 恢复安全预置模板，保留版本及审计 |

### 8.5 客户与员工账号管理

| Method / Path | 作用 |
|---|---|
| `GET /admin/customers`、`GET /admin/customers/{id}` | 带权限的客户列表、详情、业务统计 |
| `POST /admin/customers/{id}/suspend`、`POST /admin/customers/{id}/restore` | `{reason,expectedVersion}`，保留订单业务事实 |
| `POST /admin/customers/{id}/resend-verification`、`POST /admin/customers/{id}/send-recovery` | 管理员经授权请求补发或触发找回邮件，必须限流/审计/不能返回 token |
| `GET/POST /admin/staff`、`PATCH /admin/staff/{id}` | **保留原接口**，扩展安全员工账号资料和状态 |
| `POST /admin/staff/{id}/send-setup` | 发送员工邀请/设置邮件，邮件不可用时可切带外安全凭据流程 |
| `POST /admin/staff/{id}/issue-temporary-password` | `{reason}` → 仅本次返回随机临时口令；存 hash、短期有效、强制修改、审计 |
| `POST /admin/staff/{id}/revoke-sessions` | `{reason}` 管理员撤销员工会话 |
| `POST /auth/setup-password`、`POST /auth/reset-password` | 一次性员工设置/找回 token 消耗 |
| `POST /auth/forgot-password` | 员工有已验证邮箱时按防枚举规则申请找回 |
| `GET /auth/sessions`、`POST /auth/change-password`、`POST /auth/sessions/revoke` | 员工本人的安全操作 |
| `POST /auth/email/verify`、`POST /auth/email/request-verification` | 可选员工邮箱验证（启用员工邮件找回时必须实现） |

**对原接口的兼容备注**：

- 原 `/auth/login` 为员工登录，不能改成邮箱客户登录；原 `/customer/session` 为访客入口，不能废弃。`GET /customer/orders` 同时识别其授权的匿名/正式客户主体；`GET /orders/{id}` 仍执行对象级授权。
- 原 `/admin/config/{section}` 可以作为 `mail` 更新底层实现；外部公开 `/admin/config/mail` 必须和前端保持一致，不出现两个独立 mail 配置源。
- 原 `quote.totalCents` 是当前有效订单最终应付；新增折扣只允许改变新报价，且保留不可变价格快照及旧报价历史。原 `pickupCode` 不改格式/权限。
- 原 `GET /public/config` 可增加 `{features:{customerEmailAuthAvailable,guestCheckoutEnabled,promotionsEnabled}}` 等纯布尔字段；运营活动显示信息用无敏公开接口或受权业务响应，敏感价格策略和账号信息不公开。

### 8.6 新增错误码（沿用原码不移除）

`EMAIL_NOT_VERIFIED`, `EMAIL_SERVICE_UNAVAILABLE`, `EMAIL_TOKEN_INVALID`, `EMAIL_TOKEN_EXPIRED`, `EMAIL_RATE_LIMITED`, `AUTH_ACCOUNT_SUSPENDED`, `AUTH_REAUTH_REQUIRED`, `DEVICE_LIMIT_REACHED`, `ACCOUNT_LINK_CONFLICT`, `ORDER_GRANT_EXPIRED`, `ORDER_GRANT_REVOKED`, `ORDER_GRANT_SCOPE_DENIED`, `PROMOTION_NOT_ELIGIBLE`, `PROMOTION_EXPIRED`, `PROMOTION_BUDGET_EXCEEDED`, `COUPON_UNAVAILABLE`, `COUPON_ALREADY_RESERVED`, `COUPON_REQUIRES_REQUOTE`, `REFERRAL_INVALID`, `REFERRAL_ALREADY_ASSIGNED`, `MAIL_CONFIG_INVALID`, `MAIL_TLS_FAILED`, `MAIL_AUTH_FAILED`, `MAIL_SEND_FAILED`, `MAIL_TEMPLATE_INVALID`, `MAIL_TEMPLATE_REQUIRED_VARIABLE_MISSING`。

映射现有 HTTP 401/403/404/409/410/422/429/503 等语义；安全敏感认证错误公开统一化，内部 errorClass 可另存审计。所有接口必须有 DTO、枚举、至少一成功及失败示例、角色权限和限流说明。

## 9. 真实异步与事务边界

- 邮件注册验证事务：客户记录 + 验证 challenge + outbox 在同一数据库事务提交，worker 异步发送；失败可安全重试且同一业务动作不重复创建账号/邀请码/奖励。安全敏感邮件渲染时使用受保护 payload，发送完成后按照最小保留原则移除明文链接载荷。
- 优惠下单事务：锁 `quote`、券、活动余额/额度、订单和预计时段（若配置容量）；只有选中 COUPON 才锁券/券发行额度，VOLUME_TIER 只锁对应不可变规则引用；提交订单+引用+券预约+审计+outbox 一致完成。冲突 409/422 不部分扣券、不创建第二单；幂等重试回原结果。
- 收款事务：按 settlementKind 区分正数收款与零元人工确认，均严格 `receivedCents === currentOrder.totalCents` 且引用当前有效 quote / contentRevision，若所选为 COUPON 则原子 `RESERVED→USED`，否则无券可核销，写结算、状态、奖励资格基础事实；失败不能产生已用券或打印权限。
- 完成配送：保留原交付强守卫；同事务原子认定首个实付大于 0 的送达资格，再由 outbox 可靠派发，邀请奖励幂等唯一键发券；配送员不能主动触发奖励接口。服务端重试不重发券。
- 管理员签发 token：令牌只返回一次，grant hash/审计写事务；同幂等 key 重试在有限加密回放窗口取得原响应。撤销生效要能即时使兑换会话的单订单访问范围失效（授权校验 grant.revokedAt，不只靠 Cookie 自身过期）。
- 邮件通知必须异步且容错，失败不改变打印/收款/交付状态；可选订单通知限频/去重，避免多次状态纠正刷屏。管理员模板编辑不更改已排队业务事件的事实语义。

## 10. 环境配置、部署和隐私

在原后端部署手册、`.env.example` 和健康检查中增加：

```dotenv
# 示意：不填真实密码，不在版本控制中保存生产 SMTP 授权码
MAIL_MASTER_KEY=                  # 存量加密配置的主密钥，部署环境安全提供
MAIL_OUTBOX_WORKER_CONCURRENCY=1
MAIL_RETRY_MAX_ATTEMPTS=5        # 运维建议值，最终可配置
MAIL_ALLOWED_HOSTS=              # 可选：运维批准的 SMTP 目的主机 allowlist
CUSTOMER_GUEST_COOKIE_NAME=dormprint_guest_session
CUSTOMER_ACCOUNT_COOKIE_NAME=dormprint_account_session
ORDER_ACCESS_COOKIE_NAME=dormprint_order_access_session
STAFF_AUTH_COOKIE_NAME=dormprint_staff_session
PUBLIC_ORIGIN=                   # 与原配置共用，用于生成仅此站点的安全邮件链接
```

- SMTP server/port/账号/授权码/发件人/邮件模板**主要通过管理后台版本化配置，不需要每次改 .env 重启**；只有密钥、运维安全策略、worker 参数等进入环境变量。密钥轮换与旧加密值解密迁移流程写清楚。
- PWA Service Worker 继续只缓存静态壳；客户/管理员/配送员 Session、邮箱验证链接、SMTP 设置、客户订单、券明细、邀请数据、文件、交付证据均 network-only，服务器响应 `Cache-Control:no-store`。`/u/order-link` 页面不得把 URL fragment token 缓存进错误日志/离线数据。
- SMTP 来源 IP、邮件提供商发信频率和 SPF/DKIM/DMARC 等域名投递要求因实际提供方而异，部署时明确验证；不把“SMTP 已连接”等同于已能进入收件箱。站点必须 HTTPS；邮件 actionUrl 必须严格由固定 `PUBLIC_ORIGIN` + 合法受控 path 构造，避免 Host 头注入/钓鱼跳转。
- 备份包括客户与营销账本、模板版本、非明文的 SMTP 配置与必要密钥恢复策略；不能通过备份把已撤销凭证/已删除个人信息重新变为有效。原 D-03 正常文件保留期仍需部署前单独定版。

## 11. 新增后端验收矩阵（与原 BE-01—BE-38 叠加）

| 编号 | 场景 | 必须结果 |
|---|---|---|
| BE-ADD-01 | 无 SMTP 配置、浏览器第一次访问 | 游客仍可真实上传/创建订单；邮箱注册不谎报发出验证邮件 |
| BE-ADD-02 | 邮箱注册、重复邮箱、频繁注册和不存在邮箱找回 | 防重复、防枚举、限流，邮件安全且无账号泄露 |
| BE-ADD-03 | 验证/重置 token 过期、重放、并发使用 | 仅首次合法成功，重放失败，密码与会话正确轮换 |
| BE-ADD-04 | 未验证账号与冻结账号 | 无正式账号优惠/跨端查询，不能间接拥有其他客户订单 |
| BE-ADD-05 | 访客授权主体与注册账号合并 | 原订单、原资产 ID、PDF hash 不变；没有无凭据的跨账号合并 |
| BE-ADD-06 | 只凭订单链接/短码/手机号猜测 | 链接仅按 scope 与订单阶段读写该单；短码/手机号不能访问；不能通过链接登录账号 |
| BE-ADD-07 | 管理员生成、复制、撤销链接；已兑换链接撤销 | 明文 token 仅创建响应，撤销立即使该 grant 的授权访问失效 |
| BE-ADD-08 | 新注册用户输入合法邀请码 / 无邀请码 / 自己邀请码 | 关系仅一次、可选、禁止自邀；未验证不发券 |
| BE-ADD-09 | 游客、非本人优惠券、已过期、重复使用、两单并发抢券 | 资格校验正确，不重用、不负价、不超额发放 |
| BE-ADD-10 | 优惠重新报价后提交 | 价格行非负、PDF SHA 不变、quoteId 新、totalCents=原价-折扣 |
| BE-ADD-11 | 待确认撤销与确认付款并发 | 券预约释放或核销与订单状态严格一致，无两边同时成功 |
| BE-ADD-12 | 管理员实收与折扣后总价不同 | 仍阻断 PENDING_PRINT，记录核对异常，不能拿原价对账 |
| BE-ADD-13 | 被邀请人已验证且首单完成交付；DELIVERED 重试 | 邀请人恰好得到一次合规奖励券，未交付不触发 |
| BE-ADD-14 | 模板修改/恢复、变量无效/缺少 `actionUrl` | 服务端拦截坏模板；原默认版本可恢复，预览不泄露真凭据 |
| BE-ADD-15 | SMTP TLS 证书错、账号错误、超时、邮件 provider 失败 | 真实失败分类、延迟重试、用户业务主流程不被邮件异常推进/撤销 |
| BE-ADD-16 | 管理员查询已设置 SMTP password、日志、审计、测试 | 无明文泄露；测试邮件只到授权目标并限流 |
| BE-ADD-17 | SMTP Host 指向本机、私网、云 metadata | 默认 SSRF 防护阻断，运维 allowlist 例外受控 |
| BE-ADD-18 | 配送员直接调用 SMTP、活动配置、员工管理接口 | 403；管理员仍可以进行原有配送操作 |
| BE-ADD-19 | 管理员新增/停用/降权员工，强制换密 | 旧会话即时失效；不能停掉最后一个可管理的 ADMIN |
| BE-ADD-20 | 同一个浏览器存在 CUSTOMER + STAFF Cookie | 角色域不串权限，不因同时登录发生纵向提权 |
| BE-ADD-21 | 邮件通知用户未订阅/游客、订单状态高频变化 | 不给游客发邮件、不向未订阅者发营销/状态邮件，不重复发送敏感内容 |
| BE-ADD-22 | 旧版本不使用优惠券 + 原有 BE-01—BE-38 | 原订单报价、打印、人工收款、强制装袋/配送全链路未被破坏 |

测试要求：数据库级唯一约束/并发事务测试；真正的邮件 outbox worker 与可控 SMTP 测试服务器（集成测试不得误发真实信箱）；授权绕过、跨账号、会话撤销、模板注入、SMTP SSRF、折扣计算、交付重复事件等自动化。联调时通过真实受控 SMTP 环境手工检验收件箱；未做真人收件测试不能宣称送达验证通过。

满量专项使用本文件 0A.10 的 `BE-VOLUME-01`—`BE-VOLUME-12`，与原验收共同执行。

### 修订专项验收（与原 ADD 用例共同执行）

| 编号 | 场景 | 必须结果 |
|---|---|---|
| BE-FIX-01 | 阶段性链接与单单导入 | 链接待确认可编辑/撤销、配送前可改楼栋，付款后不能改文件、配送后不能改地址；导入不增加 scope，不得到同主体另一订单或原件下载权。 |
| BE-FIX-02 | 源授权撤销/过期 | 已兑换、已导入、已准备未激活的版本均不能借源失效继续读写；真实 owner 权限不受无关链接撤销影响。 |
| BE-FIX-03 | 游客关联与退出 | 合并原子轮换，旧游客 Cookie 在原/其他页面均失效；退出账号后看不到已关联单，独立有效订单链接仍可按 scope 使用；员工会话不被客户退出清除。 |
| BE-FIX-04 | 等待收款时券自然过期/活动停止 | 已 RESERVED 单按快照结算不涨价；AVAILABLE 不再新预约；风控冻结产生人工 blocker，不静默重价。 |
| BE-FIX-05 | 修改/撤销/删除/恢复券预约 | 本单沿用原券、换券、取消用券原子处理；两单抢券不双占；删除释放一次，恢复冲突阻断并要求用户确认重报，不抢另一单已用券。 |
| BE-FIX-06 | 零元与真实报价引用 | 0元总价不回退原价，微信文案不要求转账0元；管理员明确ZERO_PAYABLE才生产，正数CASH_MANUAL校验相符，提交真实currentQuoteId，零元不触发奖励。 |
| BE-FIX-07 | 邀请首单并发及乱序 | 先下单未送达不占资格；第一个合格实付正数交付事务认定唯一qualifiedOrderId，双单并发/事件乱序/重放仍只发一次，事后导入游客单不达标。 |
| BE-FIX-08 | 奖励预算与已用奖励纠错 | 预算不足进入PENDING_BUDGET，不换后续订单；恢复预算幂等补发；已用于另一已付款单的奖励纠错进入人工复核，不改旧单价格或自动扣款。 |
| BE-FIX-09 | 预算与适用服务分摊 | 发行/预约/核销/过期/释放不超预算、不重复返还；仅A4券不抵照片，整数分分摊等于总折扣，叠加开关不可开启。 |
| BE-FIX-10 | 账号/结算切换竞态 | 登录保留合法preparation与PDF确认；切号/退出使不再合法的优惠重报，快速选券A→B旧A响应不覆盖B，旧主体网络响应不回填当前用户。 |
| BE-FIX-11 | 邮件与员工初始化 | 基础账号邮箱更正须证明身份；GET预取不消费token；只对已验证基础账号授予员工权限，不新建员工凭据；撤权、两设备限制和最后超管保护由服务端执行。 |
| BE-FIX-12 | 兼容迁移与敏感保留 | 严格Quote schema及生成类型同步；旧无优惠单discount=0，优惠客户端不兼容明确阻断；授权撤销/会话失效在恢复备份后仍有效，敏感发送载荷不无限保留。 |

## 12. 后端实施顺序（与原后端一起做）

1. **契约优先**：检查原仓库、原 Prompt 和前端补丁；输出新增表、权限矩阵、OpenAPI diff、迁移风险、账号/营销状态机；逐一标记本轮批准范围与仍未批准的 D-01—D-08。
2. **基础设施/账号**：在原后端阶段 1 加入 `customer_principals` 归属、邮箱注册/验证/找回、基础账号Staff授权、统一账号Session与员工上下文、中间件、邮件模板 seed、mail config 和 outbox；确保无 SMTP 时游客路径可用。
3. **订单与优惠**：在原后端的准备/quote/order 开发阶段直接加入高熵链接签发/兑换/撤销、账号绑定、注册奖励、邀请码、券预约/核销、满量折扣与自动择优报价；与原生产打印快照保持一致。
4. **人工收款与配送**：在原 `confirm-payment` 事务中落实券核销，在原 `DELIVERED` 事件中落实邀请奖励；管理后台统计/审计及用户状态由 API 正式提供，不能写静态 mock。
5. **联调/验收**：以前端新增 `03` 页面接口为逐项验收目标；验证真 SMTP、安全邮件、游客/注册双轨订单、链接跨设备打开、手动微信发送、奖励到账、工作人员改密/停用，最后执行原全量 BE-01—BE-38 与新增 BE-ADD-01—22。

## 13. 交付给 Codex 的最终执行指令

请在开发原始后端时**把本增量需求作为同一套后端的正式组成部分**，不要做第二套用户系统、第二套订单系统或绕过原 PDF 与人工收款状态机的“优惠版下单”。先基于原版契约和本补丁写完整 ER 图、数据库迁移、OpenAPI 3.x、业务状态机及审计规则，再持续开发可运行 API、worker、配置与测试；复用原项目技术栈及基础模块。所有 SMTP 管理端字段、测试连接/发送、默认邮件模板及可编辑管理、客户/员工账号管理、游客链接与邀请优惠都必须实际实现；未配置外部 SMTP 时提供诚实的不可用状态与不影响游客下单的降级。交付真实测试记录、配置清单、完整接口、部署和密钥轮换说明，不得只输出架构图或空接口。

## 与前端实现同步的契约补齐（2026-10-30，待真实后端接入）

- 实现 `POST /preparations/{id}/withdraw-checkout`，严格 `{expectedVersion,confirm:true}`；双会话证明、未建单、非旧单修订，撤销本次 checkout_use_grant 并原子切换至新的游客 Quote/版本，旧报价失效，PDF manifest 不变则保留预览确认。整游客 principal 已合并账号不能通过本操作逆转所有权；返回明确领域错误。
- 活动增加显式 `qualificationExpiresAt:string|null`（null 为不限期）、`qualificationMinSpendCents`、`qualificationService:A4|PHOTO|ALL`；REFERRER 邀请验证时快照保存，与奖励券的 minSpendCents/service 分开；达标首次事件锁定适用活动集合，预算重试不能重新匹配后来活动。
- `/admin/dashboard` 使用生成的 DashboardSummary 返回全量统计，区分 subtotal/couponDiscount/volumeDiscount/payable/cashReceived/zeroPayable/refund/manualAdjustment，不依赖订单分页 items。
- 员工创建/修改、优惠导出和模板恢复请求及审计原因，以生成器中 StaffCreateInput/StaffUpdateInput、ExportInput、TemplateRestoreInput 为统一严格结构；筛选使用 q 和 type。
- 订单响应按有效来源返回 accessKind=OWNER/GUEST_OWNER/ORDER_GRANT/IMPORTED_GRANT，allowedActions 是独立合法来源并集与状态守卫交集；裁剪无权原件/PDF/交付凭证。不可把活跃的窄链接当成覆盖独立 owner 权利的唯一来源。

## 本轮新增业务链交付要求（2—7及追加费用，已确认）

- 链接二维码：订单专属链接、邀请链接、管理员合法HTTPS网址均在本地生成、可预览下载；保留fragment和授权边界，不新签发权限/延长到期、不送第三方。
- 批量送达：同一批次、同一楼栋多选订单，一次上传同一张照片并可填共享文字描述；每单独立核验权限/当前执行/版本/照片/定位或审批例外，整批原子提交，重复不重复送达/发券/工资。各客户只可见自己的凭证。
- 文字送达描述选填，单单和批量均展示；没有照片仍必填免拍原因，不代替定位及审批。
- 统一身份与界面：一个PWA、一个普通客户账号登录入口。正常注册客户后由超管给该accountId授予管理员/配送权限，权限跟账号跨设备；个人页仅在有对应权限时显示工作区入口，普通用户不见；所有员工/超管都可返回用户端。取消三个初始入口/三独立PWA/第二套员工登录和密码。保留工作区路由与细分服务端能力；保护最后超管、拒绝自提权和跨客户身份越权，统一账号两台设备额度。
- 员工优惠：最多1—6级，每级配置百分比减免，员工权限有效就长期享有；超管原打印单100%免单，仍人工零元结算。原打印订单含装订整单参加员工折扣，与券/满量三择优不叠加；资格服务端判定，已提交锁价，撤权只影响新报价。
- 工资统计：可选默认/员工覆盖的每单定额或折后实收比例模式；实际送达记给当时配送员并锁策略，按月账本和人工发放记录。退款后按原比例追加调整，不修改已发记录；免费重做/补送不重复计件，可有理由手工补记；定额不随追加费用变化，不自动转账。
- 追加费用：管理员在原单下按笔新增最终金额、必填用途、是否参加比例工资，提交即生效；客户可查看并提异议。原单付款前后/送达后均可追加，新增应收/实收/退款/欠收独立记录，原Quote/收款/履约事实不变。追加金额不参加任何原单优惠；参与比例工资的部分只按真实净收款计算，送达后追加收退款更新原配送员的工资差额，未收/未勾选不计入。
- 前端入口、严格DTO、迁移、授权、并发版本、持久化幂等、收退款/工资审计、导出、删除引用/保留及真实联调全部覆盖，不接受只改Prompt/文案/演示。详细最终答案及收费公式见 `docs/superpowers/specs/2026-10-30-dormprint-v1-adjustments.md`。
