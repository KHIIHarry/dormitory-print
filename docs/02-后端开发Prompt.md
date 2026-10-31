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

# 宿舍打印 H5 / PWA：后端正式开发 Prompt

## 统一账号取代旧员工凭据链（本轮最终规则）

基础客户账号是唯一登录主体；Staff仅为accountId关联的角色、能力、等级和超管标记。旧文出现的员工专属注册/密码、临时密码、独立员工会话与STAFF_SETUP/STAFF_RESET页面均作为旧版迁移说明，不再作为V1交付要求；员工改密、邮箱核验和找回走同一客户账号安全链，后台只授予/撤销能力并可撤销该账号会话。游客及单单授权仍独立，不能由角色/同名/手机号推导所有权。每账号两设备合并计数，第三台拒绝；员工高权限7天重新验证不新增另一密码。原有已注册客户、当前有效权限及最后超管保护均需真实服务端检查。


> 使用方式：将本文件全文交给后端 AI 编程工具。与前端 Prompt 配套，先产出结构化契约再开发；无需依赖对话上下文理解业务。业务未决项有明确门槛，不能当作已批准需求。


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


## A. 项目背景、需求来源与不可更改的约束

规范版本：1.0-prompt / 2026-10-29；共享契约编号：DORMPRINT-CONTRACT-1。
需求依据：[宿舍打印系统规划完整分享记录](https://chatgpt.com/share/6ae2bfe3-4e94-83e8-9281-788077b1b05d?no_universal_links=1)。
本文件是供 AI 编程工具执行的开发指令，不表示聊天中所有建议都已获得业务确认。按“用户后续明确确认 > 用户早期表述 > 助手讨论建议”处理冲突。另一份 Prompt 使用同一个契约编号；禁止各自发明接口与枚举。
当前前端已按上述同步补丁实现并提供内存演示；真实后端尚待开发。后端开工时先核对本文、04 增量与当前 OpenAPI，再完成契约、决策与实施计划并逐模块实现。


> **2026-10-29 已确认修订**：本文件与 `03-DormPrint-前端增量开发Prompt.md`、`04-DormPrint-后端增量开发Prompt.md`（DORMPRINT-IDENTITY-GROWTH-1.3）配套使用。新增邮箱账号/游客共存、授权订单链接、优惠与邮件规则由增量契约覆盖；本文件已同步其关键不变量。用户本轮明确确认：链接可按订单阶段修改/撤销、已预约优惠保护、零元人工结算、首笔成功送达且实付大于 0 的邀请奖励、游客关联后轮换并撤销旧游客访问。另已确认满量折扣：按本订单所有 A4 文档实际用纸张数，只折 A4 打印费；照片/装订不参与，与优惠券自动比较取更优惠的一种，不叠加。其他原未决事项不自动批准。

### A1. 业务目标与范围

建设校园宿舍打印 H5 / PWA，包含用户端、管理端、配送员端，共用一个后端。已有服务器，但配置、操作系统、域名、存储容量尚未提供。业务闭环：上传 → 服务端转换 → 配置打印 → 生成最终 PDF → 用户预览并确认报价 → 下单 → 添加管理员微信并线下付款 → 管理员确认收款 → 人工打印 → 按文件装订 → 按订单人工装袋 → 校园楼栋配送 → 拍照、定位交付 → 用户查询凭证。

必须保留以下边界：

1. 不接入微信支付、自动收款识别、支付回调、线上退款或打印机云服务。不自动给管理员发送微信消息。用户自行复制提取码并发送。
2. 普通用户不展示管理/配送入口；基础账号被授予对应权限后，个人页显示对应入口并可返回用户端。工作区路径可配置，且必须经过当前账号权限校验；路径保密不能替代授权。
3. 管理员拥有配送员的全部业务能力；配送员仅能查看、交付分配给自己的配送任务及提交异常，不能调度、收款、打印或读取打印文件。
4. 每单可以多个文件；每文件可以多个页码分段打印任务；每订单一个短提取码、一份合并应付金额、一个目标楼栋、一个塑封袋。
5. 文档仅 A4，每面一页，不支持 2 合 1 / 4 合 1 等缩印。支持黑白、彩色、自动双面，按页码段设置颜色、单双面、份数。双面长边/短边翻转，默认长边。
6. 分段任务分别生成最终 PDF、分别点击打印，不跨任务共用同一张纸。服务端提前展开份数；管理员打印窗口只设 1 份。双面奇数页每份补必要空白背面，不跨份共用纸张。
7. 用户看到并确认的最终 PDF 与管理员使用的 PDF 必须是同一不可变版本和 SHA-256。修改任何影响输出的设置均使对应预览确认失效。
8. 5 寸照片仅接受图片；支持“铺满可打印区域（裁剪）”和“完整保留（可额外留白）”。实际纸张毫米尺寸由后台校准；四边留白初始均为 3 mm，可分别调整。不得继续使用讨论中的 0.2 mm 或 2 mm。
9. 装订方式为不装订、左上角斜向单钉、左侧纵向双钉。每个文件独立装订；不同文件不能合并成册。同文件多个分段完成后按原始页序整理。
10. 所有打印和装订单价由后台配置；装订价格不固定为 0.09 元。单/双面均按实际纸张张数分别定价，不按印刷面统一计价。V1.0 配送费为 0。
11. 用户必填微信昵称、手机号，选择管理员地图热点对应的教学楼/宿舍楼；不填写楼层、门牌号。用户端不调用定位 API。
12. 配送时间只能选管理员提前开放的“日期 + 星期 + 时段名称”；无配置就是无可用时段，不生成虚构选项。管理员可以改期，用户显示最新安排；取消当天配送须有原因。
13. 一个订单同时最多一个有效配送批次。支持批次增减订单、转移、拆分、合并、改期、取消、删除、恢复、分配配送员。批次提前规划不等于可以提前交付。
14. 用户待确认可改文件、参数、联系信息、楼栋、预约时段或撤销；确认付款后仅在实际配送开始前修改楼栋。配送中及已送达不允许自行修改。撤销与删除分开。
15. 打印完成、装订完成、装袋完成分别记录；全部有效打印和必要装订完成后，必须由管理员人工确认已装袋，才允许开始配送。该要求不可通过后台开关关闭。
16. 点击打印仅创建打印尝试；关闭对话框、afterprint、窗口失焦均不是成功证据，必须人工确认实际打印结果。
17. 交付位置必须来自配送员/管理员设备定位 API；地图热点坐标和手填描述不得代替真实定位。服务端记录交付时间。定位失败仅管理员填原因可跳过，配送员不允许跳过。
18. 交付正常必须有上传成功的照片。管理员和配送员均可填写有效原因跳过照片；与跳过定位独立记录。用户可查看自己的交付照片、实际地点/坐标、时间或例外说明；不要求再次输入提取码。
19. 订单、批次、上传文件等可删除业务数据进入 24 小时回收站，可恢复、可由授权管理员提前永久清理，过期由后台清理。不能破坏有效引用；正常订单文件保留期与回收站期限不是同一概念。
20. 统一为一个应用与PWA安装入口，默认进入用户首页；个人页按当前员工权限显示管理/配送工作区入口并可返回用户端。私有文件、订单联系方式、交付照片、定位与凭证不得进入离线缓存；离线不得提交业务动作。

### A2. 决策登记：已批准增量与原未决事项分开

除上方已确认修订及 D-06A/B 外，以下未决项中的“建议”仍是待定方案，不是原用户已确认的业务。开始相关模块前输出决策清单并请求业务负责人定版；可继续开发无依赖模块。用 decision_status=UNRESOLVED/APPROVED 记录决策，不得把空值当作同意。普通运营配置不能绕过决策门槛。

| 编号 | 未决事项 | 建议实现及未确认时边界 |
|---|---|---|
| D-01 | 付款后管理员是否可以特批换文件、改参数、处理差价 | 建议版本化变更单 + 线下补退差额记录；未确认不开通已付款内容替换，仍可进行普通状态纠错 |
| D-02 | 补打范围 | 建议整任务重打 + 按实体纸张补打；整任务失败重试是基本能力，指定纸张补打未批准时关闭 |
| D-03 | 正常订单文件、交付照片、备份的保留期 | 分类可配置、部署前决定具体周期；未确认不得自动永久清理正常订单资产；不影响已确认的回收站 24 小时规则 |
| D-04 | 装订收费单位与各分段份数不一致的处理 | 建议按完整装订册收费、各分段份数必须一致、上限按每册实体张数；未确认装订计价策略不得上线接单，不擅自自动统一份数 |
| D-05 | 已付款订单管理员删除权限及退款处理 | 建议有原因的高权限软删除，收退款记录保留；未确认禁止删除已付款订单，其他可删除对象照常支持回收站 |
| D-06 | 已部分批准：跨设备授权与账号关联 | D-06A=APPROVED：游客及高熵单订单链接，管理员签发/撤销，按阶段修改与撤销；D-06B=APPROVED：已验证邮箱账号、显式游客合并/单单导入及轮换；D-06C=NOT_APPROVED：手机号/短码公共找回或短信验证，不实现；丢失凭证联系管理员人工核验补发。详见 03/04 v1.3 |
| D-07 | 连续批量打开打印任务 | 建议队列逐项打印与确认；未确认不自动连续弹窗，不将多个不同纸型/单双面任务拼成一个打印作业 |
| D-08 | 正式技术栈、服务器、私有存储和备份方案 | 本文提供参考栈，非原讨论定论；先检查已有仓库与环境，形成部署决策，不擅自购买服务或发布生产 |

已批准的满量优惠：管理员可配置“满 minSheets 张、payRateBps 对应折数”的多档规则，结算由服务端按 03/04 v1.3 自动计算；未登录游客同样享受符合条件的满量折扣。打印/装订单价仍可独立配置，折扣不改变 PDF 和纸张数。

额外技术细化同样属于实施建议：短码默认 5 位且避开 0/O/1/I；报价有效期、轮询间隔、上传配额、定位新鲜度及精度阈值、默认支持格式白名单等均作为可审查配置。不得宣称原记录已指定这些数值。

## B. 两端共享契约 DORMPRINT-CONTRACT-1

### B1. 通用约定

- 同源 HTTPS，API 前缀 `/api/v1`；UTC ISO 8601 存储/传输，业务日期与显示时区 Asia/Shanghai。手机号按字符串；所有 ID 按不透明字符串使用。
- 金额单位整数分，字段后缀 `Cents`；数量为整数，尺寸字段 `Mm`；图片坐标归一化 [0,1]，真实定位采用 WGS84 经纬度并标注坐标系。
- API JSON 使用 camelCase。成功：`{data, requestId}`；失败：`{error:{code,message,fieldErrors?,details?},requestId}`。列表 data 为 `{items,nextCursor,total?}`。
- 读取对象携带 `version`（乐观锁），内容版本另外为 `contentRevision`。重要写操作头 `Idempotency-Key`，请求体 `expectedVersion`。相同主体+路由+key+相同请求返回原结果；相同 key 不同请求返回 409。幂等命中应在旧版本检查前返回已有结果。
- 401 重新认证；403 无权限；404 不存在或不可见；409 版本/状态/幂等冲突；410 资源已清理；413 大小超限；415 不支持格式；422 业务校验失败；429 限流；503 服务暂不可用。
- 业务错误码至少覆盖 VERSION_CONFLICT、INVALID_TRANSITION、PREVIEW_REQUIRED、QUOTE_EXPIRED、PRICE_NOT_CONFIGURED、SERVICE_DISABLED、FILE_NOT_READY、PAGE_RANGE_INVALID、PACKING_REQUIRED、BATCH_CONFLICT、ADDRESS_REVIEW_REQUIRED、LOCATION_REQUIRED、PHOTO_OR_REASON_REQUIRED、DECISION_REQUIRED、RESOURCE_REFERENCED。
- API 返回 `allowedActions:string[]` 与 `blockers:{code,message,relatedId?}[]` 帮助前端展示，服务端仍逐次重新检查。JSON 禁止任意 status 字段覆盖业务状态。
- `/public/config` 只返回公开接单配置、可用价目和服务说明；不包含账号、私有存储 key、管理路径、敏感运维配置。
- 建立 OpenAPI 3.x 作为生成类型与客户端的唯一结构化来源；本文是必需语义基线。先补全下面每个接口的 DTO、枚举、错误及示例，再编码。契约变化同步修改前后端，不能仅在一侧修改。

### B2. 公共枚举与状态

| 对象 | 枚举 |
|---|---|
| Role | CUSTOMER / ADMIN / COURIER |
| OrderStatus | PENDING_CONFIRMATION / PENDING_PRINT / READY_FOR_DELIVERY / DELIVERING / DELIVERED / CANCELLED / DELIVERY_EXCEPTION |
| PrintStatus | PENDING / ATTEMPTED / COMPLETED / EXCEPTION / VOIDED |
| BindingStatus | NOT_REQUIRED / WAITING_PRINT / READY / COMPLETED / EXCEPTION |
| PackingStatus | BLOCKED / READY / PACKED / EXCEPTION |
| BatchStatus | PLANNED / PUBLISHED / DELIVERING / COMPLETED / CANCELLED / POSTPONED |
| DeliveryStatus（每次配送执行） | PLANNED / STARTED / DELIVERED / EXCEPTION / CANCELLED |
| AssetStatus | UPLOADING / QUEUED / PROCESSING / READY / FAILED / DELETED / PURGED |
| ColorMode / Sides / Flip | BW 或 COLOR / SINGLE 或 DUPLEX / NONE 或 LONG_EDGE 或 SHORT_EDGE |
| BindingType / PhotoFit | NONE 或 CORNER 或 SIDE / COVER 或 CONTAIN |
| ExceptionStatus | OPEN / IN_PROGRESS / RESOLVED / CLOSED |

不装订文件返回 BindingStatus=NOT_REQUIRED，不生成实际加工任务；需要装订的文件才有 BindingTask。资产转换状态不等于打印任务状态。删除使用独立 deletedAt、purgeAt，不增加 DELETED 订单主状态。

主流程：PENDING_CONFIRMATION → PENDING_PRINT → READY_FOR_DELIVERY → DELIVERING → DELIVERED。进入 PENDING_PRINT 需人工确认正数付款或人工确认 ZERO_PAYABLE；后文付款已确认/已收款作为生产资格前置时均包含合法零元人工结算，不代表零元收到现金。
待确认可撤销为 CANCELLED。配送失败为 DELIVERY_EXCEPTION，管理员解决并重新安排、重新检查成品条件后回 READY_FOR_DELIVERY；有资料损坏则先回生产处理。回退必须是有原因、有历史、会重算依赖的纠错动作，不能直接改枚举。

### B3. 核心数据形状

下列为必要字段；实施时在 OpenAPI 中定义完整必填性、长度、上下界与角色裁剪视图：

```ts
type SegmentInput = {
  clientSegmentId: string;
  ranges: {from: number; to: number}[]; // 1-based，含首尾
  colorMode: 'BW'|'COLOR'; sides: 'SINGLE'|'DUPLEX';
  flip: 'NONE'|'LONG_EDGE'|'SHORT_EDGE'; copies: number;
};
type DocumentInput = {
  clientFileId: string; assetId: string; kind: 'DOCUMENT';
  segments: SegmentInput[]; excludedPages: number[];
  bindingType: 'NONE'|'CORNER'|'SIDE';
};
type PhotoInput = {
  clientFileId: string; assetId: string; kind: 'PHOTO'; copies: number;
  orientation: 'PORTRAIT'|'LANDSCAPE'; fit: 'COVER'|'CONTAIN';
  rotationDegrees: 0|90|180|270;
  crop: null|{x:number;y:number;width:number;height:number};
  // crop 相对 EXIF 校正后再旋转的图片归一化；CONTAIN 时为 null
};
type PreparationInput = {
  files: (DocumentInput|PhotoInput)[];
  replacesOrderId?: string; expectedOrderVersion?: number;
};
type PreviewAck = {artifactId:string; sha256:string};
type OrderSubmit = {
  preparationId:string; quoteId:string; previewAcks:PreviewAck[];
  contact:{wechatNickname:string; phone:string};
  locationId:string; slotId:string;
  customerNote?:string; // 选填，trim 后最多 30 字；不等于管理员内部 note
};
type PriceLine = {
  key:string; relatedId:string; unit:'SHEET'|'BOOKLET';
  quantity:number; unitPriceCents:number; amountCents:number;
};
type FinalArtifact = {
  id:string; sha256:string; contentRevision:number;
  pdfPages:number; sheets:number; expandedCopies:number;
  sourcePageMapAvailable:boolean;
};
type DeliveryProofInput = {
  expectedVersion:number; deliveryAssignmentId:string;
  photoAssetId:string|null; skipPhotoReason:string|null;
  position:null|{latitude:number;longitude:number;accuracyMeters:number;
    capturedAt:string;coordinateSystem:'WGS84'};
  locationFailureCode:string|null;
  skipLocationReason:string|null; // 只有 ADMIN 可以非空
  placementNote:string|null; // 辅助摆放说明，不替代 position
};
```

Preparation 返回 `{id,status,contentRevision,artifacts,validation,quote?}`，quote 包含 `{id,priceVersion,expiresAt,lines,subtotalCents,discounts,discountCents,totalCents,deliveryFeeCents:0,promotionRuleVersion?,selectedPromotionType,volumeDiscountCandidate?}`，订单返回 `currentQuoteId` 和完整报价快照；扩展字段按 03/04 v1.3 定义。预览确认集合必须覆盖当前全部最终 artifacts。资产 URL 通过受权接口获得，不将存储路径当公开 URL。
Order 用户视图至少包含 id、pickupCode、version、contentRevision、status、productionSummary、金额快照、文件与任务摘要、楼栋快照、原预约时段、最新配送安排、变更原因历史、自己的交付凭证、allowedActions、blockers。配送员视图只含履约所需联系方式、楼栋、提取码、配送信息，不含打印资产和金额管理数据。

### B4. 接口目录

以下路径均相对 `/api/v1`。`{id}` 按对应资源 ID，所有写接口落实权限、幂等和适用的版本检查。

| 方法与路径 | 用途/主要请求与响应 |
|---|---|
| GET /public/config | 服务、公开价目、微信联系、上传限制、决策批准能力列表 |
| GET /public/map | 地图图片版本、可配送楼栋热点 |
| GET /public/slots?date= | 可预约时段；日期、星期、开始/结束时间、名称、是否可选 |
| POST /customer/session | 建立免注册安全会话；不代表手机号已验证 |
| POST /auth/login；POST /auth/logout；GET /auth/me | 统一账号的员工上下文查询；旧登录地址仅兼容基础账号凭据，不恢复独立员工密码 |
| POST /order-access/exchange | `{orderId,accessToken}` 换取限定订单访问会话；非短提取码授权 |
| POST /assets | multipart 文件+用途；返回 assetId、转换状态；照片凭证另受配送权限限制 |
| GET /assets/{id} | 所有者可查处理状态、失败码、标准化页数与尺寸 |
| GET /assets/{id}/content | 鉴权二进制，支持合法 Range；可用于原件/标准 PDF/最终 PDF/凭证，按资产权限区分 |
| POST /assets/{id}/retry | 可重试的转换任务，不创建假成功 |
| POST /preparations；GET /preparations/{id} | 异步最终 PDF 准备；参数为 PreparationInput |
| POST /preparations/{id}/retry | 对当前失败且可重试的最终生成作业重试，禁止把旧版本激活为当前件 |
| POST /preparations/{id}/requote | 文件内容不变时更新过期/变价报价，返回新 quote；不替换 PDF |
| POST /orders | OrderSubmit；返回 order、仅创建时返回的高熵 accessToken |
| GET /customer/orders；GET /orders/{id} | 仅当前主体有权访问的订单列表/详情 |
| POST /orders/{id}/revisions | `{expectedVersion,...OrderSubmit}`；待确认修改，原子替换内容版本 |
| POST /orders/{id}/cancel | `{expectedVersion,reason?}`；仅待确认用户撤销 |
| PATCH /orders/{id}/location | `{expectedVersion,locationId}`；配送开始前更改楼栋 |
| GET /orders/{id}/history；GET /orders/{id}/proof | 角色裁剪后的历史/自己的凭证 |
| GET /admin/dashboard；GET /admin/orders | 后台统计、组合筛选、游标分页与稳定排序 |
| POST /admin/orders/{id}/confirm-payment | `{expectedVersion,contentRevision,quoteId,receivedCents,settlementKind,note?}`；CASH_MANUAL 要求应付/实收大于 0 且相等，ZERO_PAYABLE 要求两者均为 0；均人工确认 |
| GET /admin/print-tasks；GET /admin/print-tasks/{id} | 按订单/类型/批次过滤同一批任务，含最终 PDF 和打印设置 |
| POST /admin/print-tasks/{id}/attempts | `{expectedVersion,artifactId}`；创建尝试并返回 attemptId；不保证实际弹出打印框 |
| POST /admin/print-attempts/{id}/result | `{expectedVersion,outcome:'COMPLETED'|'FAILED'|'CANCELLED',reason?}` |
| POST /admin/print-tasks/{id}/reprints | `{expectedVersion,mode:'WHOLE'|'SHEETS',sheetIds?,reason}`；SHEETS 受 D-02 控制 |
| GET /admin/binding-tasks；POST /admin/binding-tasks/{id}/complete | 文件装订队列/完成；带 expectedVersion |
| GET /admin/packing-tasks；POST /admin/orders/{id}/pack | 装袋队列/人工核对；请求含 expectedVersion、当前清单版本和逐项确认 |
| POST /admin/corrections | `{targetType,targetId,expectedVersion,action,reason,...actionPayload}`；动作白名单，不能任意改状态 |
| GET /admin/batches；POST /admin/batches；PATCH /admin/batches/{id} | 批次列表、新建、编辑时间/配送员等；修改须版本与原因 |
| POST /admin/batch-operations/preview | `{action,orderIds,sourceBatchIds?,targetBatchId?,versions,payload}`；返回短期 planId、影响清单/阻断项 |
| POST /admin/batch-operations/commit | `{planId}`；动作 ADD/REMOVE/TRANSFER/SPLIT/MERGE/RESCHEDULE/CANCEL，重检版本 |
| POST /admin/batches/{id}/publish | 发布计划，不把订单标记配送中 |
| POST /admin/batches/{id}/start | `{expectedVersion}`；整批原子检查，有不合格项整批拒绝，管理员先移出不合格订单 |
| GET /courier/batches；GET /courier/batches/{id} | 仅分配自己的批次，管理员可访问全部 |
| GET /courier/orders/{id} | 配送专用裁剪视图；分配失效即撤权 |
| POST /courier/orders/{id}/proof | DeliveryProofInput；管理员也使用该业务动作，无需重复登录 |
| POST /courier/orders/{id}/exception | `{expectedVersion,type,reason}`；开始配送后的配送失败 |
| GET /admin/exceptions；PATCH /admin/exceptions/{id} | 异常处理/解决；不能直接略过真实生产依赖 |
| GET /admin/config；PATCH /admin/config/{section} | 版本化价格、服务、微信、限制、保留策略等；字段白名单 |
| GET/POST /admin/slots；PATCH /admin/slots/{id} | 时段配置，关闭不删除已约订单 |
| POST /admin/maps；PATCH /admin/maps/{id} | 地图版本上传、热点配置/迁移；发布前预览 |
| GET/POST /admin/staff；PATCH /admin/staff/{id} | 配送员/管理员账号及状态，密码不在响应返回 |
| POST /admin/trash | `{targetType,targetId,expectedVersion,reason}`；受引用约束/决策限制 |
| GET /admin/trash；POST /admin/trash/{id}/restore-preview | 回收站列表/恢复冲突预览 |
| POST /admin/trash/{id}/restore；POST /admin/trash/{id}/purge | 恢复/永久清理；原因、版本、关联选择 |
| GET /admin/audit-logs | 只读审计检索 |

D-01 若批准，新增变更单 prepare/activate 两阶段接口到同一 OpenAPI；批准前不制造可调用的内容替换后门。D-06A/B 按 03/04 v1.3 的已批准授权执行，D-06C 不实现；不提供手机号/短码公共找回或假短信。

### B5. 核心计价与页序不变量

设某任务选择的原始内容页数为 n，份数为 c：

- 单面用纸 S=n×c；最终 PDF 页数 n×c。
- 双面用纸 S=ceil(n/2)×c；最终 PDF 页数 2×ceil(n/2)×c；奇数 n 每份末尾含一个空白背面。
- 每任务金额=S×对应 SINGLE/DUPLEX 与 BW/COLOR 单价；照片每份一张纸；装订若 D-04 批准按册，费用=册数×对应装订单价；商品小计 subtotalCents=各正向收费行之和，配送行=0；最终应付 totalCents=subtotalCents-discountCents，0≤discountCents≤subtotalCents；折扣来源为 COUPON 或 VOLUME_TIER，最多采用一种，由服务端自动取优惠金额最大者，同额优先满量以保留券。无优惠时 discountCents=0；应付可为 0，但必须由管理员人工确认零元结算后才可生产，零元单不触发邀请奖励。折扣独立快照，不写负金额 PriceLine。
- 原始空白页也是所选内容页；系统补的空白背面不额外作为一张纸收费。不同任务即使打印属性相同也不自动拼接共享纸张。
- 页码必须正整数、范围合法、同任务和跨任务均不得重复。未选页面必须与 excludedPages 精确对应并由用户明确确认；不允许默默漏页。页序默认按原始页码升序。
- 建立最终 PDF 页索引 → 文件版本 → 原页码/空白 → 任务 → 份号 → 实体纸张号 → 正反面的映射，供整理装订、核对及可选补打使用。
- 例：7 页双面×3=12 张纸，PDF 24 页。前 4 页单面×2 加后 6 页双面×2=14 张纸、两个任务，不能算成一个混合双面任务。

## C. 浏览器能力与事实边界

普通网页调用打印界面，不承诺自动指定驱动单双面/长短边/颜色/纸盒/材质，也不承诺知道实际出纸结果。`afterprint` 可以在预览关闭时发生，不能作为成功回执。定位需要安全上下文和设备授权；地图归一化坐标不是真实经纬度。PWA manifest 的 scope 与 Service Worker scope 是不同配置，均需检查。

技术依据（开发前核验目标浏览器行为，不能仅凭文档宣称真机通过）：

- [MDN window.print](https://developer.mozilla.org/en-US/docs/Web/API/Window/print)
- [MDN afterprint](https://developer.mozilla.org/en-US/docs/Web/API/Window/afterprint_event)
- [MDN Geolocation API](https://developer.mozilla.org/en-US/docs/Web/API/Geolocation_API)
- [MDN manifest scope](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Manifest/Reference/scope)

## D. 你的角色、后端目标与参考架构

你是资深后端工程师、数据库与文件处理工程师。按照 A—C 和本文件其余部分实现可部署到已有服务器的模块化后端，支撑三端共用数据与文件、真实转换/最终 PDF、计价、人工收款、生产、配送及运营闭环。不能用内存数组、假转换或返回固定报价冒充完成。

先检查已有仓库、AGENTS.md、服务器可用说明和技术约束；不得读取无关私密文件。无既有栈时参考：Python FastAPI + Pydantic、SQLAlchemy + Alembic、PostgreSQL、Redis + Celery worker、LibreOffice headless、可维护的 PDF/图像处理库、私有文件存储。该栈属于实施建议，D-08 决定正式方案；版本以实施时兼容稳定版本锁定。

采用模块化单体 API + 独立转换 worker + 定时维护任务，避免为小规模宿舍业务设计大量微服务。建议目录：app/modules/auth、assets、preparations、orders、pricing、production、delivery、config、trash、audit；app/domain 管状态机/不变量；app/infrastructure 管数据库/队列/存储；workers 管耗时文件任务；tests 分 domain/integration/security。

前端独立构建，API 通过同源反向代理；API 不控制打印机，只有打印尝试/结果的业务记录。Redis/worker 任务消息不是最终业务真相，PostgreSQL 是权威记录。

先输出 ER 图、状态转换表、权限矩阵、OpenAPI 与迁移方案，再实现接口及测试。所有补充技术决策写 ADR，不能把建议伪装成原需求。待决事项按 A2 分支处理，禁止擅自开通收费、删除、换文件等能力。

## E. 数据库模型、字段与约束

统一主键不透明 UUID；核心可修改对象有 version，createdAt/updatedAt 使用服务端时间；软删除另设 deletedAt/deletedBy/deleteReason/purgeAt。历史内容版本不可变，用户 view 由服务端裁剪。金额整数分，数据库 CHECK 非负；不使用 float 存金额。

| 表/聚合 | 关键字段及约束 |
|---|---|
| customer_sessions | 匿名主体 ID、会话 hash、有效期、撤销时间；不默认手机号已验证 |
| staff / staff_sessions | 唯一 username、强密码 hash、ADMIN/COURIER、enabled、sessionVersion、会话到期/撤销；不存明文密码 |
| order_access_grants | orderId、主体/令牌 hash、scope、有效期、撤销记录；原 token 仅创建响应（含受保护幂等重放）输出 |
| assets | ownerType/ownerId、用途、原文件名、私有 storageKey、真实 MIME、bytes、sha256、status、failureCode、页数/尺寸、引用/保留状态 |
| conversion_jobs | assetId、输入 hash、处理器/字体/配置版本、状态、attempt、错误分类、开始/结束时间、输出 assetId；唯一任务去重键 |
| preparations | ownerId、inputHash、configVersion、status、contentRevision、目标订单与基准 version、完整输入、验证结果、产物集合 |
| orders | pickupCode 唯一、ownerId、currentRevisionId、orderStatus、productionSummary、version、联系信息、楼栋/时段快照、优先级、地址待复核、删除标记 |
| order_revisions | (orderId,revisionNumber) 唯一、输入与配置快照、artifact manifest、quoteId、创建人/时间、激活记录；不可覆盖旧内容 |
| order_files | revisionId、clientFileId、DOCUMENT/PHOTO、原件/标准化 assetId、顺序、装订要求、照片参数、sourcePageCount |
| print_tasks | revisionId、fileId、分段 ranges、颜色/单双面/翻转/份数、最终 artifactId、用纸数、PrintStatus、version、active 标记 |
| page_sheet_maps | taskId、artifactVersion、pdfPageIndex、sourcePageIndex 或 blank、copyIndex、sheetIndex、front/back；唯一页索引 |
| print_attempts | taskId、artifactId、操作人、请求/结果时间、结果、失败原因、operationId；保留多次尝试 |
| reprint_jobs | 原 task/attempt、WHOLE/SHEETS、sheetIds、产物、status、reason、nonBillable=true；不得新增收费行 |
| binding_tasks | fileId/revisionId 唯一、方式、完整册数、每册纸张数、BindingStatus、version、确认人/时间；NONE 不创建实际任务 |
| packing_tasks | orderId 唯一当前记录、PackingStatus、manifestRevision、清单 hash、确认人/时间、撤回历史、version |
| price_rule_versions / price_items | 规则版本、key、unitPriceCents 可空、enabled、生效时间、修改人；未配置 null 与免费 0 不混同 |
| quotes / quote_lines | preparationId、priceVersion、expiresAt、currency=CNY、总额、行 key/关联对象/数量/单位/单价/金额；不可覆盖 |
| payment_records | orderId、revisionId、quoteId、应收/实收、类型、人工确认人/时间、原因、operationId；无支付平台交易回调 |
| maps / delivery_locations | 底图 asset/version、楼栋 ID、名称/类型、归一化 x/y、enabled；更换地图不能静默错位 |
| delivery_slots | 业务日期、开始/结束时间、显示名、enabled、可选容量/截止、version；结束必须晚于开始 |
| delivery_batches | 名称、计划起止 UTC、配送员、BatchStatus、version、延期/取消原因、发布时间等 |
| delivery_assignments | orderId、batchId、实际执行状态、active、楼栋快照、addressReviewRequired、操作者、version；一个订单至多一个 active=true |
| delivery_plan_history | 订单/批次、原/新安排、原因、变更人/时间，和用户原预约分开 |
| delivery_proofs | orderId、assignmentId、照片资产或 skipPhotoReason、真实坐标/精度/采集时间或管理员跳过原因、serverDeliveredAt、操作者、revision/supersedes；已确认凭证不可原地覆盖 |
| business_exceptions | type、orderId、targetType/Id、severity、blocksProduction/blocksDelivery、reason、ExceptionStatus、处理人/时间/记录 |
| business_corrections | 对象、动作、前后快照、原因、操作者、关联版本、补偿动作、operationId；不接受任意 SQL/任意字段 |
| config_versions / business_decisions | section、schema、version、值、生效时间、操作者；决策批准者/时间与 UNRESOLVED/APPROVED 单独保存 |
| trash_entries | 类型/ID、删除/到期时间、关联快照、可恢复状态、清理 job 状态/错误、version |
| audit_logs / status_events | actor、动作、对象、前后关键值、原因、requestId、operationId、服务端时间；追加写，脱敏 |
| idempotency_records / outbox_events | 主体+路由+key 唯一、请求 hash、结果引用、处理状态；事务提交后可靠派发事件 |

必须有数据库层约束：

1. pickupCode 唯一，碰撞重试，不依赖“随机所以不会重复”。内部 UUID 与短码分开，不把短码作为访问令牌。
2. 每订单有效配送 assignment 唯一，使用部分唯一索引或等效数据库机制；禁止仅靠 API 先查再插。
3. 文件属于内容版本，任务属于该文件/版本，asset 引用存在且不能跨 owner 越权；数据库外键与应用校验协同。
4. 每文件当前装订任务至多一个，每订单当前装袋任务一个。任务状态完成不自动代表装袋完成。
5. CHECK 份数、数量正整数、金额非负、归一化坐标 0—1、经纬度合法、边距和小于纸宽高。历史引用默认 RESTRICT，不用无差别 cascade 删除业务事实。
6. 索引覆盖后台提取码/手机号、订单状态+创建时间、楼栋、预约、有效批次、打印状态/属性、异常状态、purgeAt。文件名/昵称搜索按实际规模选择适当索引并分页。
7. 审计记录不得复制原文件内容、密码、token、完整外部签名 URL；敏感联系信息访问受控。审计保留与永久删除涉及的最小记录边界必须在保留策略里说明。

## F. 认证、访问授权与权限矩阵

默认同源 HttpOnly + Secure + SameSite cookie 会话；写接口 CSRF 防护，严格 CORS/origin，可信代理明确配置。员工密码采用成熟的强密码哈希；登录限流、失败记录、会话到期/撤销、停用账号立即失效；生产无默认 admin/admin 密码。通过一次性安全初始化命令设置首个管理员，不能公开注册 ADMIN。

匿名会话管理用户上传与本机订单。订单专属访问令牌使用密码学高熵随机数（建议至少 128 bit），只存 hash，只对单订单授权，可撤销；POST exchange 后换受限会话。仅订单 ID/5 位提取码/手机号及后四位绝不能读取隐私文件。D-06A/B 已批准部分按 03/04 v1.3 实现；D-06C 不开放手机号/短码公共找回，不加入假短信。

| 操作 | CUSTOMER | COURIER | ADMIN |
|---|---|---|---|
| 上传文档/照片、准备订单 | 自己会话内 | 不允许 | 按管理业务权限 |
| 读取订单/文档 | 自己授权订单 | 仅配送裁剪视图，不可文档 | 可审计管理访问 |
| 修改待确认内容/撤销 | 自己订单 | 否 | 受状态与原因约束 |
| 确认付款、打印、装订、装袋 | 否 | 否 | 是，服务端检查前置条件 |
| 改配送楼栋 | 自己订单配送前 | 否 | 是，需重评批次 |
| 调度/改期/配置/员工/回收站 | 否 | 否 | 是，受决策/引用限制 |
| 查看配送任务/上传交付照片 | 否 | 自己有效分配 | 全部有效任务 |
| 跳过交付照片 | 否 | 有原因可 | 有原因可 |
| 跳过定位 | 否 | 绝对不允许 | 定位失败且有原因 |
| 送达确认/配送异常 | 否 | 自己已开始任务 | 可处理全部，仍要校验 |

每个 ID 操作逐次对象级授权，防止替换 assetId/preparationId/orderId/assignmentId 访问他人内容。配送员改派/停用后不能继续下载凭证或提交旧任务；上传交付照片也要检查分配和用途，不能把公开图片 ID 冒充自己的交付资产。

## G. 上传、转换、最终 PDF 与异步作业

### G1. 接收与安全处理

- 支持格式以管理员启用且转换器真实支持的白名单交集为准。参考初始范围 PDF、DOC/DOCX、PPT/PPTX；图片 JPEG/PNG/WebP，可选择 A4 或 5 寸照片纸；是否支持 XLS/XLSX、HEIC 等形成显式兼容清单并测试，不能写“所有 Office 都支持”。
- 流式上传，反向代理/API/业务配置三层限制大小、单订单累计、数量、页数、图片解码像素、展开后份数/最终 PDF 大小和存储配额。前端限制不替代服务端。
- 校验扩展名、真实 MIME、文件签名与解析结果；随机存储 key，不拼接用户文件名进路径/命令；拒绝路径穿越、压缩炸弹、损坏/超大图像、加密 PDF、可执行伪装。宏/外链不执行。
- 原文件隔离存储，转换进程非 root、无对外网络、只读运行环境+独立临时目录、有 CPU/内存/时间/进程限制；LibreOffice 独立 profile，避免并发锁冲突。不要调用 shell=True 拼接文件名。
- 原件、标准 PDF、最终打印 PDF、交付照片分别明确用途和权限。Office 字体库固定并记录版本，转换异常不能静默删页；可检测的缺字/损坏阻止就绪或要求用户处理，不承诺机器可检测所有排版问题。
- 照片/凭证清除无关 EXIF，朝向先校正；交付实际位置以独立定位字段记录，不从照片 EXIF 推断。

### G2. 状态、队列与可靠性

资产 UPLOADING→QUEUED→PROCESSING→READY 或 FAILED。POST 返回 202 与资源 ID，前端轮询；失败记录可读错误码、可重试性，内部错误路径不直接暴露。

所有转换/生成在 worker，去重 key 由输入 hash+设置 hash+转换器/字体/配置版本构成，并包含权限隔离信息，禁止跨用户存在性泄漏。worker 可能重复执行，只有一个当前结果。输出先写临时对象、校验页数/hash/可读性，成功后原子发布元数据；不能出现 READY 指向半文件。

API 事务写 job/outbox，提交后发送队列；定时补偿扫描未派发/卡住任务。重试有上限和退避；不可修复的加密/损坏不无限重试。超时、worker 崩溃、重启都能被重新认领，租约/版本防旧 worker 覆盖新产物。提供处理时长、队列深度和失败率监控。

### G3. 文档标准化与分段生成

1. PDF 校验并规范化页面旋转、页面尺寸；A4 输出每面只放一页。非 A4/横版页面保持比例适配 A4、禁止裁掉内容；旋转/缩放策略固定并写入 metadata，让用户预览确认。不允许合并多页缩印。
2. Office 先转标准 PDF，再获得实际页数及尺寸，不能根据源文件页数字段计价。
3. 按 B5 校验分段与 excludedPages，内容页升序。不同单双面/颜色任务绝不跨纸共用；不要为节省纸张擅自重分段。
4. 为每任务每份复制选页。单面不插入双面空白；双面奇数每份末尾补一页空白（包括最后一份），再串接下一份，得到完整最终 PDF。
5. BW 任务须确定输出策略：建议服务端生成灰度最终 PDF，让预览和打印文件内容一致；彩色保留。若灰度转换会栅格化，先评估清晰度/字体/矢量损失，固定分辨率并做验收；不得预览彩色而偷偷打印不同 PDF。驱动灰度仍提示管理员核对。
6. 生成 page_sheet_maps，区分系统空白与原始空白，不把空白背面计成额外纸张。
7. artifact manifest 保存 hash、页数、纸张数、份数、纸型/设置快照、输入 hash、工具链版本；用户最终确认的是 manifest 中各 PDF。
8. 不添加用户没确认的页眉、水印、订单码、广告、封面或装订钉图到 PDF；份数/任务边界用 metadata 和外侧工作清单表达。

### G4. 照片生成的几何规则

纸张宽 W、高 H（毫米），边距 l/r/t/b；可打印尺寸 Pw=W-l-r，Ph=H-t-b，必须均大于 0。PDF 点单位 mm×72/25.4。照片先 EXIF 校正，再按 rotationDegrees 旋转；crop 相对于该结果归一化。

COVER：校验裁剪框在图内且符合可打印区域纵横比（数值容差需定义），将选区完整填满 Pw×Ph。CONTAIN：不裁剪，缩放系数 min(Pw/Iw,Ph/Ih)，居中放置，允许内侧额外留白。横竖切换时纸张尺寸与边距含义统一按最终朝向定义，在前后端契约中固定，不靠 CSS 猜测。

照片一份一页、单面、彩色，copies 展开为 c 页，计 c 张照片纸；照片无 A4 分段和装订。初始四边 3 mm，尺寸必须由管理员实际纸张校准后启用服务；不承诺物理无白边。保留输入裁剪、配置版本和最终输出 hash，后来改边距不重生成旧订单文件。

## H. 报价、版本、订单创建与人工收款

### H1. 准备与报价

POST preparations 验证资源归属、配置、服务开启、决策批准、页码/份数、装订能力；全部最终 PDF READY 且经验证后才能生成有效 quote。生成中失败不返回看似确定的可提交价格。

计价统一使用服务端 PricingService 与 B5。各打印任务独立向上取整，再累加；不得先合并页数再 ceil。装订上限若 D-04 采用实体张数，每册总张数是各分段每份用纸之和，不能把 copies 乘进单册厚度。

报价不可变，包含所用价格与服务配置版本、各行明细、有效期、总价。报价有效期由配置决定；实施建议可选 15 分钟，但属于技术配置而非原需求。旧报价过期/价格版本变化按明确策略拒绝提交并要求 requote；已创建订单保留原价格直到合法内容修改。只改配送地址不重算旧价格，配送始终 0。

### H2. 创建/修改订单

POST orders 在一个事务中重检：当前匿名/账号/单订单委托授权的资源归属与 scope（见 04 v1.3）、preparation READY、manifest hash、previewAcks 完整匹配、当前报价有效、服务/接单开关、楼栋可用、时段开放及可选容量、微信昵称手机号合法。前端总价不能作为权威数据；客户端传入修改后的价格字段应拒绝。

事务创建 Order、不可变 Revision、文件/任务引用、价格快照、装订/装袋记录、权限 grant、状态事件与审计，生成唯一短码，初始 PENDING_CONFIRMATION。需要预约容量时加锁/条件更新，防止并发超售。创建成功返回 accessToken；长期 grant 表仅存 hash。为满足创建响应的幂等重放，原始含 token 响应仅在幂等重试窗口内加密保存，密钥独立管理、仅原主体同请求可解密重返，窗口结束删除加密响应；日志与普通幂等查询不得暴露该响应。不能因为第一次网络响应丢失让用户失去订单访问。

修改待确认订单：新 preparation 在独立草稿中生成，旧有效内容保持可读。revisions 提交时锁订单并校验 expectedVersion/状态；原子激活新 revision+quote、旧任务失效，旧 PDF 不覆盖。过期 worker 无权把旧准备重新激活。收款已先完成则拒绝用户修改；修改先完成则旧付款确认请求拒绝。只改联系方式可使用同 manifest 创建新订单快照，但必须保持并发校验。

撤销：只允许自己的待确认订单，事务内释放预约/有效批次规划、标记 CANCELLED、停止后续执行、保留审计；不是直接删除资产。撤销后不能普通恢复成待打印，重新下单使用新码。

### H3. 人工收款与零元结算确认

confirm-payment 只有 ADMIN 可用，事务锁订单、确认其当前状态/版本/quoteId/contentRevision；实际收到金额不等应收金额时只能记录核对异常，不能进入 PENDING_PRINT。报价有效期仅限制创建/修改提交；已提交且内容未变的订单不得因 quote.expiresAt 已过而拒绝正常收款或自动改价。正数应付要求 settlementKind=CASH_MANUAL、receivedCents=totalCents>0；零元应付要求 settlementKind=ZERO_PAYABLE、receivedCents=0，管理员明确确认而非自动放行；追加不可变结算记录，零元不计现金实收和邀请有效实付。两种人工结算均追加 payment record（含 kind 与折扣快照），激活生产任务资格、写状态与审计；重复 key 不重复记录，另一 key 再次确认也不能重复入账。

付款记录不可原地覆盖。补款/退款仅是线下人工记录，不调用支付系统。D-01 未批准不得付款后替换文件；如批准则引入 change request：新文件/配置→新产物与报价→差额核对→人工确认新版本激活，期间锁定受影响生产，保留旧事实。不得自动作废已实际完成的打印记录或让旧 URL 作为新任务打印。

## I. 服务端状态机、加工与纠错

领域服务集中实现 transition/guards/effects；接口薄层仅负责认证、DTO 与调用。前端按钮、批次编辑、管理员权限都不能绕过不变量。

### I1. 打印任务及尝试

- 待确认订单即使已准备出 PDF，也无实际打印资格；attempts 要求已确认付款、当前有效 revision、未撤销/删除、无阻断异常。
- PENDING→ATTEMPTED：创建尝试记录。服务端无法证明打印框实际打开，界面文案不要比证据更强。
- ATTEMPTED→COMPLETED：管理员提交明确成功结果；失败→EXCEPTION，取消保留尝试结果并回 PENDING。多个未结尝试要拦截或要求显式处理前一条，防重复。
- EXCEPTION 可创建新的重试尝试；COMPLETED 若误标，专门纠错 action+reason 撤回；受影响装订/装袋和订单摘要重算。VOIDED 不可继续执行。
- 打印结果提交需检查 attempt 对应任务/当前 artifact；过期 attempt 或已被新版本替代的结果不可覆盖当前完成状态。

### I2. 重打/补打

整任务失败重试复用同一原任务 PDF，并新增尝试，不增加费用。已完成任务发现缺失/损坏时先开生产异常，重新评估完成有效性，阻止已装袋订单直接出发。

D-02 批准指定纸张补打后：输入为稳定 sheetId 或 copyIndex+sheetIndex，按 page_sheet_maps 取完整正反面；奇数份末尾包含原来的空白背面。单面每张一页，双面每张两页，保留原输出顺序，生成独立 nonBillable 补打 PDF，与原任务和失败原因关联。禁止直接接受用户任意最终 PDF 页码拼接造成正反错位。

补打完成只解决对应缺陷，原任务完成与阻断异常在事务内一致更新；重算装订、装袋。补打不额外创建一笔用户账单。不向原 PDF 插入新页，不悄悄增加 copies。

### I3. 装订、装袋及订单投影

需要装订的文件所有当前有效打印任务完成→WAITING_PRINT 转 READY；管理员核对页序/每册齐全并完成实体装订→COMPLETED。不需装订返回 NOT_REQUIRED。任一打印/装订异常未解决禁止装订完成。

装袋 BLOCKED→READY 需：付款已确认、当前有效任务全部完成、必要装订全部完成、无阻断生产异常、文件版本有效、订单未撤销/删除。READY 只是等待人工，不自动变 PACKED。

pack 请求带当前 manifestRevision 和清单确认，服务端重新检验全部前置条件，并记录核对人、时间、清单版本。管理员人工确认→PACKED，订单主状态才可 PENDING_PRINT→READY_FOR_DELIVERY。

装袋撤回：仅管理员+原因，未实际配送可回 READY/BLOCKED 并把订单恢复到 PENDING_PRINT；若配送已开始或已送达，转专门纠错流程。不能撤回后订单仍留可配送的错误投影。

订单主状态与子状态均有持久化事实；每次事务通过统一聚合服务刷新投影，避免定时异步重算窗口允许提前配送。worker 也只能调用同一规则。

### I4. 普通纠错和高风险纠错

corrections 的 action 为显式白名单，如 REOPEN_PRINT、REOPEN_BINDING、UNPACK、REVIEW_ADDRESS、CORRECT_PROOF。每动作定义允许源状态、角色、必填原因、前置检查与补偿影响；不允许 actionPayload={status:any} 的万能后门。

配送中/已送达的生产撤回、已付款替换文件、已送达凭证纠正单独建变更记录；按已批准决策处理。凭证纠正追加 supersedes 关系保留原记录，不能覆盖照片/时间伪造原交付。不清除原付款/打印尝试历史。

## J. 地图、预约、批次和配送执行

### J1. 地图与楼栋

地图底图图片私有管理上传、可公开配送地图资产有明确用途，不能借公开地图接口下载任意资产。每热点 x/y 归一化 0—1 与 mapVersion 绑定；楼栋名称/类型/是否可配送可编辑。更换底图先建新版本并确认热点迁移/重定位，发布原子切换；历史订单楼栋名称/地图版本快照不被覆盖。

不接受用户楼层/门牌字段；避免自由备注绕过配送到楼栋规则。真实配送位置使用单独经纬度字段，绝不使用图片热点坐标代替。

### J2. 预约与地址变更

时段按 Asia/Shanghai 具体业务日期创建，星期从日期计算；明确 startsAt/endsAt 转 UTC，不能存互相矛盾的手填星期。无配置、过期、关闭、超过截止/容量（若启用）的时段不可选；下单时重新检查。

关闭时段不能删已有预约；调整安排保留原预约+配送计划历史，用户看到最新预计起止和原因。下单时选择的 slot 与管理员 batch 时间各自保存，不混成一个字段。

配送前用户改 location，锁订单与 active assignment 并校验未 STARTED。修改成功给现有 assignment 打 addressReviewRequired，配送前阻断，管理员确认或转移批次后解除；地址修改与批次 start 并发只允许一个先成功。配送后拒绝用户修改。

### J3. 批次调度与开始配送

计划批次可包含未完工但合法的订单，不可包含已撤销/删除/已送达订单。发布时要求有效配送员/明确时间。一个订单最多一个 active assignment。

批量操作采用 preview→commit，preview 存 action、目标对象及版本、影响清单、短时有效期；commit 事务重新授权、校验版本/不变量，按确定排序加锁防死锁。TRANSFER/SPLIT/MERGE 原子释放旧 active 并建立新关联，不能出现短暂双分配；审计保留变更历史。过期或数据变化返回 409，不部分静默执行。

start 的强制条件（每单均成立）：

```
已人工确认付款 AND 所有当前有效打印完成
AND 所有必要装订完成 AND packingStatus=PACKED
AND 非撤销/非删除 AND 无阻断交付异常
AND active assignment 合法 AND 配送员有效
AND 地址已复核 AND 批次已发布/时间安排有效
```

参考执行策略：start 只允许 ADMIN 发起（原记录配送员仅确认送达/异常）；一次全批原子检查，任何订单未合格整批拒绝并返回 blockers，不静默丢单。成功将 assignment 转 STARTED、订单转 DELIVERING、批次转 DELIVERING。

PLANNED→PUBLISHED→DELIVERING→COMPLETED；未开始可 POSTPONED/CANCELLED；延期重新安排后重新发布。配送中取消/删除须先处理各个 STARTED 执行的取消/异常和未送达单重新安排，不能整体抹除已送达事实；已完成批次保留历史。

删除/取消批次仅取消未完成执行、解除未送达订单有效关联，不删订单或付款/生产事实。无生产问题的订单回 READY_FOR_DELIVERY 等待重新安排。完成批次要求没有 STARTED 未结项；已异常项必须可见其解决/转移去向，不能把“批次完成”当成所有订单送达。

### J4. 交付凭证与异常

proof 仅当前有效被分配 COURIER 或 ADMIN，要求订单 DELIVERING、assignment STARTED、付款/装袋条件仍有效且无阻断异常。读取并锁当前分配，防改派时旧配送员交付。

正常 position 必填，校验 latitude/longitude/accuracy/capturedAt 的数值及时间合法性；位置太旧/精度太差采用已批准配置要求重采样。浏览器数据只能证明客户端报告，不能声称可完全杜绝 GPS 伪造。没有强制地理围栏的确认需求，不擅自用楼栋距离拒绝交付。

定位失败：只有 ADMIN 可以填 locationFailureCode+skipLocationReason 跳过，COURIER 传该字段一律拒绝，绝不因为有“管理员入口 URL”就授权。不能把填了原因当作定位成功。

照片必须是当前交付范围内拥有且 READY 的图片资产；否则明确 skipPhotoReason。两条独立规则分别校验，空白原因不合法。图片上传失败不能自动变免拍照。可补摆放说明但不能代替坐标。

在一个事务中保存不可变 proof、服务端 serverDeliveredAt、当前执行完成、订单 DELIVERED、状态事件/审计；检查其他批次状态并更新汇总。重复提交返回同一结果；普通第二次交付被拒绝。用户只能查看自己的凭证，不能看到同批其他联系信息。

配送异常要类型+原因，当前执行转 EXCEPTION、订单 DELIVERY_EXCEPTION，管理员重新安排；若资料丢失损坏，必须关联生产阻断、重新生产/装订/装袋，不能只重新配送凭空缺失的资料。

## K. 配置、回收站与文件生命周期

### K1. 配置 schema 与生效规则

所有配置 section 强类型验证、版本控制、审计、生效时间，不接受任意 JSON 任意覆盖。公开/管理/运维配置分层，密码、数据库连接和存储密钥只在安全环境配置，不能在公开 API 暴露。

价格：A4_BW_SINGLE、A4_BW_DUPLEX、A4_COLOR_SINGLE、A4_COLOR_DUPLEX、PHOTO_5IN、BIND_CORNER、BIND_SIDE；单价初始 null、启用前必填。DELIVERY_FEE 在 V1.0 强制 0，普通配置不得改成收费。服务：颜色/单双面/照片/装订开关、默认翻转、实际照片毫米尺寸、四边默认 3 mm、装订方式名字和上限。

限制：白名单、单文件/订单大小、文件数、页数、份数、最终展开大小、图片像素、转换并发与超时、存储配额。联系：管理员微信号、好友二维码、展示名称、手机号、引导文案；运营：公告、接单开关、容量、时段、提取码策略。

保留策略、账号/会话策略、备份/健康状态只在有权限管理视图。历史订单按价格/文件/服务快照不随当前配置变动；公共服务关闭阻止新报价，不删除历史已付款单任务。紧急暂停执行应另有显式操作，不能滥用改价达到停单。

### K2. 24 小时回收站

普通删除事务中设置 deletedAt、purgeAt=deletedAt+24h，创建 trash entry、停止后续执行/撤销链接、关联审计。删除订单的权限受 D-05 控制。用户 CANCELLED 与管理员 deletedAt 独立。

删除批次不能 cascade 删除订单；已交付 proof 保留。删除资产如被有效订单/标准 PDF/最终件引用则拒绝 RESOURCE_REFERENCED，或先合法替换版本解除引用。删除订单后资产进入受控保留，不能在可恢复期立即物理删除。

restore-preview 列出关联冲突、已丢失/清理资产、订单已改派等；restore 只恢复仍符合条件的对象与显式选择的合法关联。不得覆盖新批次 assignment；恢复后再过领域状态守卫，不自动标记装袋/恢复收款。24 小时后拒绝恢复，即使清理 job 尚未执行。

永久清理任务并发安全、可重复执行：锁定到期对象→标记 PURGING→重新检查引用/保留约束→删除可删对象→记录最小清理结果。失败可重试告警；对象未物理删除不得宣告 PURGED。小窗口对象删除与元数据更新靠清理状态+补偿机制处理，数据库事务不能假装回滚对象存储删除。

清理时区分目标聚合内部的失效引用与其他有效业务引用：前者按已批准清理计划去除内容保留最小 tombstone/历史元数据，后者必须阻止物理删除。历史记录可保留 assetId 和 hash 并标注内容已过期，不必把仍有历史外键等同于永久保留二进制。

主动永久清理需 ADMIN、原因和具体对象确认，不允许无审计的一键清空所有业务数据。不可删仍有效引用或未决法定/业务保留范围；回收站恢复不等于从备份恢复。

### K3. 正常保留与备份

D-03 定版后才运行正常订单资产清理；原件、标准化 PDF、最终 PDF、凭证分类策略，未完成订单不得因普通周期被误清理。D-03 未决仅允许孤立临时作业按明确临时 TTL 清理，不永久清理正常订单资产。

备份数据库、私有资产、配置/工具链版本有一致性快照/manifest；加密、受控访问、明确周期与到期策略。业务永久删除不自动意味着所有离线备份立刻擦除，应在部署策略说明备份过期和恢复后删除 tombstone 重放，防已删数据复活。恢复演练必须证明订单、PDF hash、凭证、权限与有效批次关联一致。

## L. 事务、幂等、资源一致性与可观测性

- 创建/修改订单、付款确认、打印结果、装订、装袋、批次关系、开始配送、交付、删除/恢复都使用数据库事务。
- expectedVersion 用条件更新或锁+compare 防丢更新。对同订单相关子状态使用统一锁顺序；禁止查完条件后在无锁状态下写入。
- 幂等记录范围包含 actor+HTTP route+key，持久化请求 hash/处理状态/响应引用，与业务事务保持一致。两个相同 key 并发只有一个执行。网络超时/进程重启后可安全恢复；幂等 key 保留期限按最大重试窗口制定并写文档。
- 对象存储/文件系统与数据库跨资源无全局事务，用 staged→published、outbox、引用检查、孤儿回收和对账任务；不把“await 上传成功”视为整体原子。
- 文件服务支持授权 Range/正确 Content-Type/Content-Disposition，no-store/nosniff；签名 URL 若采用必须短期且限定资源，不能在审计中存 token。需要即时撤权的打印/凭证优先 API 鉴权流，禁止永不过期公开链接。
- 异常统一模型，解决记录不抹掉；production/delivery blocker 用明确字段，不能仅前端红标。纠错记录 before/after、reason、actor、server time、operationId。
- 日志 requestId 串联 API/job/outbox，不记录文档内容/密码/会话/敏感完整坐标。监测转换失败、积压、磁盘、对象存储、数据库、清理失败、备份失败、异常未解决时长。
- GET health/liveness/readiness（可内部使用）分别反映进程与依赖健康；worker/存储不可用时拒绝需要能力的操作，不能仍返回“系统可接单”。
- 配置 max upload 等形成统一能力返回；数据库/API/PWA 时间来源明确，不能用浏览器时间决定回收站或付款交付时刻。

## M. 测试与后端验收矩阵

使用实际 PostgreSQL 集成测试验证事务/唯一约束，不只 mock 仓库；文件处理使用小型真实 PDF、DOCX、PPTX、JPEG/PNG fixtures；通过解析输出页数/几何/hash/页序映射验证，不只断言函数被调用。时间可注入方便测试清理边界，生产仍用服务器时间。

| 编号 | 场景 | 必须得到的结果 |
|---|---|---|
| BE-01 | 多文件订单 | 一单一码，文件/任务/快照/权限完整，不重复收费 |
| BE-02 | 前4页单面+后6页双面各2份 | 8+6=14张，两个独立任务，无跨任务共纸 |
| BE-03 | 7页双面3份 | PDF24页、12张，每份第8面空白；三份映射完整 |
| BE-04 | 1页/2页/奇数/偶数、空白原页 | ceil 按每任务每份计算，系统补白与原白页可区分 |
| BE-05 | 越界、重复、遗漏、小数、0份 | 422；只有显式 excludedPages 才允许遗漏 |
| BE-06 | 单面携带长边/照片携带装订 | 拒绝无效组合，不能默默改成别的要求 |
| BE-07 | 加密/损坏/伪装/超大图/超配额 | 拒绝或转换失败，不生成假页数/有效报价 |
| BE-08 | worker 重复执行/崩溃/超时 | 一个当前有效产物，无半文件 READY，无重复收费 |
| BE-09 | 照片横竖、EXIF、旋转、COVER/CONTAIN | 几何正确，四边3mm/非对称边距反映在最终PDF |
| BE-10 | 改设置但提交旧预览 hash | PREVIEW_REQUIRED；不能下单/激活旧版本 |
| BE-11 | 缺价、停服务、报价过期、配置并发 | 对应错误码，null不当0；旧订单快照不变 |
| BE-12 | 篡改前端价格、assetId归属 | 拒绝；总价服务端计算，不能读他人资产 |
| BE-13 | 用户改订单与管理员收款并发 | 仅合法一方先成功；另一方409，无旧报价付款 |
| BE-14 | 同幂等key并发/不同body重用 | 同请求一个结果，不同body409，不重复订单/付款 |
| BE-15 | 收款金额不符 | 待核对异常，不激活待打印 |
| BE-16 | 未付款、旧版本或已撤销尝试打印 | 服务端拒绝；afterprint不改变完成事实 |
| BE-17 | 打印尝试失败、取消、人工完成 | 尝试历史保留，结果与任务状态一致 |
| BE-18 | 整任务重试/已批准纸张补打 | 不加价；双面完整正反面、份号和空白正确 |
| BE-19 | 未全部打印确认装订/未装订确认装袋 | 拒绝，不仅靠前端按钮阻止 |
| BE-20 | 人工装袋完成/撤回 | READY_FOR_DELIVERY正确；撤回阻断且保留历史 |
| BE-21 | 未装袋直接调start/proof | PACKING_REQUIRED，不能越过加工流程 |
| BE-22 | 一个订单并发加入两个批次 | 数据库唯一约束生效，最多一个active |
| BE-23 | 批次规划未完工单/启动含未完工单 | 允许规划、整批启动拒绝并返回清单 |
| BE-24 | 批量转移/拆并事务中失败 | 完整回滚，无孤儿关联/重复分配 |
| BE-25 | 地址修改与配送开始并发 | 不出现出发后用户修改；地址待复核阻断 |
| BE-26 | 改期、取消当日、删批次 | 需原因，用户见新安排，生产/已送达凭证不丢失 |
| BE-27 | 配送员读取他人批次/打印文件/改价 | 对象级拒绝，DTO不泄漏打印资产或管理金额 |
| BE-28 | 定位失败跳过、照片无原因跳过 | 配送员跳过定位403；管理员有原因才可；照片规则独立 |
| BE-29 | 照片未READY/他人资产/过期定位 | 拒绝完成，不存在假送达记录 |
| BE-30 | 正常交付及重试/改派竞态 | 一条有效凭证，服务端时间，旧配送员不能送达 |
| BE-31 | 配送异常/资料损坏 | 保留原因，重新安排；损坏必须重生产核对 |
| BE-32 | 猜提取码/输手机号/换订单ID | 无权读文件、私有订单、照片、定位 |
| BE-33 | 24h前恢复/恰好到期/过期 | 边界正确，过期不能恢复，恢复不覆盖新分配 |
| BE-34 | 有效引用资产删除/清理重试 | 引用保护；清理失败可重试且不虚报PURGED |
| BE-35 | D-01—D-08未批准 | 相关危险/未定分支返回DECISION_REQUIRED，无隐藏后门 |
| BE-36 | 配置/打印完成/装袋/纠错 | actor、reason、before/after、版本、服务器时间可追溯 |
| BE-37 | 备份恢复 | 数据和资产hash/权限/关联一致，已删数据不无意复活 |
| BE-38 | 真实服务器断网/磁盘满/worker停止 | 明确失败和告警，可恢复，无丢单/伪完成 |

测试分层：领域计价与状态转移单测、数据库/文件 worker 集成测试、OpenAPI 契约测试、认证越权与上传安全测试、并发/幂等测试、全链路 E2E。以小规模预期负载压测上传+转换+查询混合场景，记录环境/并发/耗时/内存，不虚构性能指标。浏览器打印与实体出纸仍需前端/真实打印机人工验收。

## N. 部署、运维与交付要求

D-08 确定前先提供本地 Docker Compose 参考，不擅自公网部署。正式部署按服务器 CPU/内存/存储给转换 worker 设保守并发，并实测；低内存环境不能无限并发 LibreOffice。

参考 Compose 包含 reverse proxy、API、worker、scheduler、PostgreSQL、Redis，私有持久卷或经批准对象存储。数据库/Redis 不直接暴露公网；上传根目录不做静态目录公开。反向代理按三端路径配置 SPA fallback 和 `/api/v1` 路由，上传体大小、超时、Range、TLS、真实IP策略一致。

环境变量示例至少：DATABASE_URL、REDIS_URL、SESSION_SECRET、PUBLIC_ORIGIN、ADMIN_BASE_PATH、STORAGE_DRIVER、PRIVATE_STORAGE_ROOT 或受控对象存储参数、CONVERTER_TIMEOUT、WORKER_CONCURRENCY、TZ/业务时区、日志级别。真实密钥不提交 Git，不由前端读取。收费、照片边距、时段和微信配置存业务配置表，不把所有业务设置塞环境变量。

数据库通过迁移升级，不靠启动自动 drop/recreate；生产 seed 不带演示订单、假手机号、示例收费、默认密码或自动批准决策。提供首次管理员初始化、配置必要价目/纸型/二维码/地图/时段、检查接单能力的命令与手册。

交付：完整源代码、锁定依赖、ER 图、迁移、完整 OpenAPI、权限/状态机表、队列与存储说明、.env.example、Compose/部署配置、启动/测试命令、备份恢复/清理/故障处理手册、真实测试报告、待决列表。每模块说明“实际实现/未实现/受决策阻塞”，不能隐藏 stub。

## O. 实施顺序及对开发工具的最终指令

阶段1：检查环境与决策、建立数据库/认证/配置 schema、共享 OpenAPI、状态机及测试骨架。
阶段2：真实上传→转换→分段/照片→最终 PDF→预览确认→报价→订单版本；联调用户端。
阶段3：人工收款→打印尝试/结果/重试→装订→强制装袋；实现数据库并发检查。
阶段4：地图/时段→批次规划调度→地址复核→开始配送→定位照片交付→异常；联调配送端及用户凭证。
阶段5：回收站/审计/配置完整性、批准后的选配变更/补打、备份恢复、安全和部署验收。

现在先输出业务硬约束、D-01—D-08决策状态、ER/状态机/接口契约计划及各阶段验收标准；随后持续实现已批准范围并运行验证。任何接口不得通过传任意 status 或管理员万能权限绕过付款、打印、装订、装袋和配送授权。不以模拟文件处理、假报价、无持久化记录或缺失权限测试宣称系统完成。交付报告附真实命令结果、未决项和未验证的真实环境限制。

## 本轮新增业务链交付要求（2—7及追加费用，已确认）

- 链接二维码：订单专属链接、邀请链接、管理员合法HTTPS网址均在本地生成、可预览下载；保留fragment和授权边界，不新签发权限/延长到期、不送第三方。
- 批量送达：同一批次、同一楼栋多选订单，一次上传同一张照片并可填共享文字描述；每单独立核验权限/当前执行/版本/照片/定位或审批例外，整批原子提交，重复不重复送达/发券/工资。各客户只可见自己的凭证。
- 文字送达描述选填，单单和批量均展示；没有照片仍必填免拍原因，不代替定位及审批。
- 统一身份与界面：一个PWA、一个普通客户账号登录入口。正常注册客户后由超管给该accountId授予管理员/配送权限，权限跟账号跨设备；个人页仅在有对应权限时显示工作区入口，普通用户不见；所有员工/超管都可返回用户端。取消三个初始入口/三独立PWA/第二套员工登录和密码。保留工作区路由与细分服务端能力；保护最后超管、拒绝自提权和跨客户身份越权，统一账号两台设备额度。
- 员工优惠：最多1—6级，每级配置百分比减免，员工权限有效就长期享有；超管原打印单100%免单，仍人工零元结算。原打印订单含装订整单参加员工折扣，与券/满量三择优不叠加；资格服务端判定，已提交锁价，撤权只影响新报价。
- 工资统计：可选默认/员工覆盖的每单定额或折后实收比例模式；实际送达记给当时配送员并锁策略，按月账本和人工发放记录。退款后按原比例追加调整，不修改已发记录；免费重做/补送不重复计件，可有理由手工补记；定额不随追加费用变化，不自动转账。
- 追加费用：管理员在原单下按笔新增最终金额、必填用途、是否参加比例工资，提交即生效；客户可查看并提异议。原单付款前后/送达后均可追加，新增应收/实收/退款/欠收独立记录，原Quote/收款/履约事实不变。追加金额不参加任何原单优惠；参与比例工资的部分只按真实净收款计算，送达后追加收退款更新原配送员的工资差额，未收/未勾选不计入。
- 前端入口、严格DTO、迁移、授权、并发版本、持久化幂等、收退款/工资审计、导出、删除引用/保留及真实联调全部覆盖，不接受只改Prompt/文案/演示。详细最终答案及收费公式见 `docs/superpowers/specs/2026-10-30-dormprint-v1-adjustments.md`。
