> **本轮业务链调整（2026-10-30，新增2—7项）**：以 `docs/superpowers/specs/2026-10-30-dormprint-v1-adjustments.md` 为本轮增补。纳入链接转二维码、同照片批量送达、选填送达文字、统一应用及个人页权限入口、员工1—6级百分比优惠/超管打印永久免费、定额或比例配送工资。原三套独立PWA/三个初始入口方案取消；身份安全隔离继续保留。N01—N07及F01—F03已确认，金额/身份/交付按最终答案落地，不得遗漏新增模块。

> **V1最终定版覆盖（2026-10-30）**：本文件中的旧未决项/冲突规则由 `docs/superpowers/specs/2026-10-30-dormprint-v1-scope.md`（Q01—Q36及R01—R06全部已批准）替代。完整范围见配套V1功能交付矩阵。不得以旧D-02/D-04/D-07未批准为由省略指定纸张补打、按册装订或人工打印队列；Excel/HEIC、分片续传、售后退款、细分权限等均已纳入。自动关单、收款占容量、配送员start、默认只读链接及数据清理必须按新规则执行。

# 宿舍打印 H5 / PWA：前端正式开发 Prompt

## 统一账号取代旧员工凭据链（本轮最终规则）

基础客户账号是唯一登录主体；Staff仅为accountId关联的角色、能力、等级和超管标记。旧文出现的员工专属注册/密码、临时密码、独立员工会话与STAFF_SETUP/STAFF_RESET页面均作为旧版迁移说明，不再作为V1交付要求；员工改密、邮箱核验和找回走同一客户账号安全链，后台只授予/撤销能力并可撤销该账号会话。游客及单单授权仍独立，不能由角色/同名/手机号推导所有权。每账号两设备合并计数，第三台拒绝；员工高权限7天重新验证不新增另一密码。原有已注册客户、当前有效权限及最后超管保护均需真实服务端检查。


> 使用方式：将本文件全文交给前端 AI 编程工具；覆盖用户端、管理端、配送员端。与后端 Prompt 配套，公共契约一致。本文是详细开发指令，业务未决项有明确门槛，不能当作已批准需求。

## A. 项目背景、需求来源与不可更改的约束

规范版本：1.0-prompt / 2026-10-29；共享契约编号：DORMPRINT-CONTRACT-1。
需求依据：[宿舍打印系统规划完整分享记录](https://chatgpt.com/share/6ae2bfe3-4e94-83e8-9281-788077b1b05d?no_universal_links=1)。
本文件是供 AI 编程工具执行的开发指令，不表示聊天中所有建议都已获得业务确认。按“用户后续明确确认 > 用户早期表述 > 助手讨论建议”处理冲突。另一份 Prompt 使用同一个契约编号；禁止各自发明接口与枚举。
本次交付仅编写开发 Prompt，不代表已经开始建设应用。将本文件用于后续开发时，先完成契约、决策与实施计划，再逐模块实现。


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

## D. 你的角色、工作方式与前端交付目标

你是资深前端工程师和产品交互设计师。依据 A—C 与本文件其余部分，交付真正可连接后端运行的用户、管理、配送三端，不交付只有静态页面的展示项目。不要在本任务中擅自实现另一套服务端业务规则。

执行要求：先检查现有目录、AGENTS.md、包管理器和技术约束；没有代码时采用下面的参考方案并在架构决策中注明；存在项目则优先保持现有技术栈。先产出页面/路由清单、共享类型、交互状态表、OpenAPI 对接计划与测试计划，再按模块实现。待决规则仅阻塞依赖功能，不能让整个项目停留在计划阶段。每阶段完成可运行、可验收的纵向闭环。

参考前端栈（实施建议，不是聊天已指定）：React + TypeScript + Vite，路由使用 React Router，服务端缓存使用 TanStack Query，表单使用 React Hook Form + Zod，PDF 采用自托管 PDF.js 及匹配 worker，测试采用 Vitest / Testing Library / Playwright。选择实施时兼容的稳定版本并锁定依赖，不虚构版本号。所有字体、图标与 PDF worker 尽量本地打包，打印私有文件不能转交在线预览服务。

复用一个React应用，按用户、管理、配送工作区拆模块和懒加载，共享ui、api-client、contracts及utils；统一构建和安装。用户端路径 `/u/`，配送员 `/courier/`，管理端路径由 `ADMIN_BASE_PATH` 决定；根路径仅重定向用户端。管理路径是路由配置，不是密码；有权员工可从个人页发现入口，路由可见不授予管理能力。

## E. 视觉、布局与通用交互

采用中文、清晰、轻量的校园服务界面。用户端移动优先，桌面保持适当内容宽度；管理端桌面侧栏+工作区，手机转可触控卡片/详情页；配送端单手可操作、大按钮、少层级。参考验收视口 360、390、768、1440 px。数字金额和提取码使用易辨认数字样式，提取码可复制、可放大，不堆砌营销大图。

主状态使用稳定的语义色并同时显示文字/图标，颜色不能成为唯一线索：

| 状态 | 颜色方向 | 附加提示 |
|---|---|---|
| 待确认 | 琥珀/橙 | 等待管理员核对微信付款 |
| 待打印 | 蓝 | 细分正在打印/整理装订/待装袋 |
| 已打印（待配送） | 紫 | 资料已装袋，显示配送计划 |
| 配送中 | 青 | 当前配送安排 |
| 已送达 | 绿 | 查看交付凭证 |
| 已撤销 | 灰 | 不再处理 |
| 配送异常 | 红 | 原因与待处理说明 |

颜色具体值由 UI token 定义，文本对比度应合格。键盘可操作、明确 focus、表单有 label、错误与字段关联，交互触点建议至少 44 px。动画尊重 prefers-reduced-motion。

所有列表/详情/弹窗必须覆盖：首次加载、空数据、请求失败、403、404、离线、过期资产、处理中、提交中、成功、版本冲突。长文件名、中文标点、异常长昵称、手机号换行、iOS 安全区、输入法弹出、缩放后的地图都必须处理。不得有假按钮、死链接、永久加载或用 alert 替代核心流程。

关键写操作不乐观宣告成功。按钮防连点，保持同次操作幂等 key；超时提示“结果待核实”并查询/用同 key 重试，不能直接发新建单请求。409 显示变化并重新拉取，不自动覆盖别人修改；金额变化必须重新确认。服务端报价未就绪使用 null/显式状态；合法 0 元不得通过 `value || 原价` 回退，真实模式也不得用本地估价替代正式快照。成功后更新所有受影响查询，防止列表和详情不一致。

## F. 用户端页面与完整流程

### F1. 首页 `/u/`

展示营业状态、公告、A4 文档/5 寸照片入口、公开价目、PDF 优先提示、下单步骤及订单入口。配置没加载成功不能显示假单价；暂停接单仍允许查询历史订单。只展示已启用服务，未配置的收费项明确提示暂不可用。

### F2. 文件工作台 `/u/new`

1. 多文件选择/拖拽，手机原生选择支持；显示文件名、大小、上传进度、转换阶段、重试/移除。上传和转换分开展示，不用一个伪造百分比代表全部处理。
2. 从后端读取格式/数量/大小限制并提前提示；后端拒绝仍按错误码展示。格式不支持、加密、损坏、字体/转换问题显示解决方法，不伪造页数。
3. 上传后显示服务端标准化 PDF 与实际页数。通过 PDF.js 分页渲染/懒加载缩略图，提供放大、翻页、跳页；大文件控制 canvas 数量与内存，离开页面释放资源。
4. 用户可添加多份文档和照片，但每个文件仍是独立配置单位。错误文件必须修复或主动移除后才能继续。
5. 本地草稿最多保存非敏感 UI 设置；不把原文件 blob、手机号、凭证或订单私有内容放进 localStorage/IndexedDB。离开有未提交修改时提示，浏览器重启可能需要重新选择文件，不能假装能恢复文件。

### F3. 文档设置

默认普通模式对全选页一次设置黑白/彩色、单双面、翻转、份数。高级模式添加分段行，页码输入支持 `1-4,7,9-12` 并转换为 B3 ranges；所有页码 1-based，空范围/0/负数/小数/反向区间/重复均提示。

单面时 flip=NONE 且隐藏翻转选项；双面默认 LONG_EDGE，可选 SHORT_EDGE。只显示 A4，不提供缩印。前端可即时提示预计用纸量，最终金额与页数以服务端为准。

提供“页面分配摘要”：每页归属、重复页、未选择页。遗漏页必须通过“明确跳过这些页”写入 excludedPages，禁止默认补打或静默丢弃。修改分段、份数、颜色、翻转或装订后，把最终准备状态置为需重新生成，不允许继续沿用旧预览确认。

### F4. 装订选择组件

每个 A4 文件三个单选卡：无需装订、左上角单钉、左侧双钉。用 CSS 纸张层叠、斜向单钉、左侧两枚纵向订钉绘制示意；不依赖原聊天附件可下载，示意需有可读标签，订书钉不进入打印 PDF。单价来自配置，不出现固定 0.09。

D-04 批准后展示按册价格及册数；不同分段份数不一致时按批准策略阻止并解释，可提供显式“统一为 X 份”操作供用户选择，不能自动改数。显示预计每册实体纸张数和装订上限，不把多文件合并装订。未批准 D-04 时展示服务尚未配置，不以免费装订绕过计价。

### F5. 照片编辑

仅选允许的图片，预览前按 EXIF 校正朝向；支持横竖方向、90 度旋转、份数、COVER/CONTAIN。COVER 可拖动、缩放、调整裁剪，CONTAIN 完整保留且禁用裁剪框；裁剪坐标严格按 B3 的旋转后基准传输。

预览显示真实比例纸张、四边物理留白与内部可打印区域。COVER 的目标是填满内部区域，绝不能写“无边距铺满整张纸”。四边默认 3 mm，显示后端当前配置；实际纸宽高由后台配置，不能把 5 寸等同任意尺寸。编辑完成调用服务端生成 PDF，再显示该 PDF 作为最终预览，前端 canvas 编辑图不能代替确认件。

### F6. 最终预览与结算 `/u/review`

- POST preparations 后展示 QUEUED/PROCESSING/READY/FAILED，失败可重试，不进入确认下单。
- 每个独立任务展示最终 PDF、原始页段、颜色/单双面/翻转、原份数、已展开 PDF 页数、预计纸张数。系统补空白背面与份数边界清晰标示为旁侧信息，不额外插入未经同意的封面。
- 用户逐任务确认，记录每个 artifactId + sha256；“我已检查”只能反映用户点击，不虚构用户阅读了每页。
- 报价明细按单价×纸张/册数显示，打印费、装订费、配送 0、总价、报价版本。准备就绪且已确认全部 PDF 才可提交。
- 填写微信昵称、手机号；地图热点或楼栋列表搜索单选楼栋，两者同步；不提供楼层/门牌输入，不调用定位。
- 预约只用后台时段，格式日期+星期+名称。时段关闭/容量变化时保留其他输入，引导重新选择；无可用时段允许保留工作进度但阻止下单。
- 显示用户预约与管理员最终安排可能不同；不把预约写成准时送达承诺。
- 准备生成失败调用 preparation retry；报价过期调用 requote；若只更新报价但 PDF 哈希不变可保留 PDF 确认，价格变化需另行确认金额；内容/配置导致 PDF 变化则重新生成并重看。

### F7. 下单成功、微信引导 `/u/orders/:id/contact`

突出显示提取码、应付金额、待确认状态、管理员微信号、添加好友二维码、可复制的联系文本：“提取码 XXX，本次应付 ¥X.XX，微信昵称 XXX”。用户自行发送消息。不自动调用微信支付/转账、不承诺 H5 可直接加好友。

二维码支持点击放大、长按/保存提示和微信号复制回退；剪贴板失败可手动选择文本。提供订单访问链接的保存提示。专属链接是隐私凭据，不混入发给管理员的普通付款文案，不展示其他用户信息。

优惠后应付为 0 时显示“无需转账，等待管理员确认零元结算”，保留微信联系入口但不引导转账 0 元。

没有“我已付款”一按就改变主状态的逻辑；如提供“查看是否已确认”，只刷新服务端。二维码配置缺失按运营配置错误提示联系，不展示虚构二维码。

### F8. 订单详情 `/u/orders/:id`、本机订单 `/u/orders`

详情显示状态时间线、提取码、价格快照、文件摘要、生产进度、楼栋、原预约、最新计划和改期/取消原因。已送达显示照片或免拍照说明、实际定位及精度/采集时间、服务器交付时间、摆放备注；定位例外明确标注，不能显示为定位成功。

同会话可列自己的订单；通过专属链接进入时按 D-06 的限定会话授权。建议 token 放 URL fragment，兑换后 history.replaceState 移除；禁止在日志、分析和 Referer 传出 token，不存长期 localStorage。链接丢失时展示联系管理员流程，公共提取码查询不能返回私有资料。

按 allowedActions 显示修改、撤销、修改楼栋按钮。待确认改文件创建新 preparation，重新预览、报价并通过 revisions 提交，成功前保留旧有效订单；付款确认竞态收到 409 后停止提交。确认付款后隐藏内容编辑，仅配送前可改楼栋；已分配批次发生地址变化时展示“待管理员复核”，不假装旧配送计划仍确定。

在线状态同步采用统一查询层，可先用建议 10 秒轮询（可配置）+页面重新可见时刷新；后台标签页暂停/退避，网络恢复拉最新数据；服务端状态为准。不得用本地计时器自动推进主状态。

## G. 管理端页面与操作闭环

### G1. 登录、权限与首页

登录后取得角色；非管理员不能进入管理页，退出清空私有查询缓存、关闭预览/释放 blob URL。会话失效后重登返回原页面，不能自动重放未确认的危险动作。

总览统计今日订单、待付款确认、待打印任务、待结果确认、打印异常、待装订文件、待装袋订单、已装袋待配送、今日批次、配送异常、临近预约仍未完工。点击统计卡可进入对应筛选队列。区分订单数/文件数/任务数，不混为一个计数。

### G2. 订单管理

组合筛选：提取码、昵称、手机号、原文件名、楼栋、下单日期、预约时段、实际计划、批次、主状态、打印类型。提取码忽略首尾空格/大小写；服务端分页排序；筛选写 URL 便于返回恢复。

列表显示提取码、昵称/电话、创建时间、金额、文件/任务数量、楼栋、原预约、最新计划、状态及异常。详情以文件→任务分层，能查看当前与历史版本、价格行、付款、打印尝试、装订、装袋、配送、日志。

确认付款先展示服务端 currentQuoteId、当前内容版本与折扣后应收金额，输入实际收到金额；禁止固定 `quote-v1`。0 元订单提供独立的“人工确认零元结算”，不声称收到微信付款；不相等不能直接确认，跳转人工核对流程。提交 expectedVersion/contentRevision/quoteId；成功才进入待打印。用户同时改价导致 409 时展示新旧变化，要求重新核对，不能悄悄重新提交。

支持内部备注、优先级（通过明确契约字段），优先级不改价格和预约时间。撤回、删除、交付纠错均有专门表单、原因、影响预览，禁止提供可任意切换订单状态的下拉框。

### G3. 打印中心与打印视图

三种筛选视图：按订单、按打印类型、按配送批次，共用任务 ID。任务卡明确：提取码、文件名、原页码、当前 PDF 版本、黑白/彩色、单/双面、翻转、纸型、份数已展开、PDF 页数、预计纸张数、状态、最近尝试。

打印交互：

1. 打开独立受权打印视图，读取最终资产和当前任务权限；预加载 PDF，资源失效可重新鉴权获取同版本文件。
2. 展示显眼核对清单：“打印窗口 1 份”“对应单双面/翻转/颜色”“A4 或实际照片尺寸”“不缩印”“照片按校准实际尺寸”。不同任务各自核对。
3. 用户直接点击打印按钮时调用浏览器/PDF.js 打印能力；异步取件要预加载或先打开同源打印页，避免 await 后被弹窗拦截。记录 attempt 的准确含义为“请求尝试”，不是系统已经出纸。
4. 尝试记录失败则阻止继续并允许重试；打印 UI 被拦截/取消则保留待核实记录并可报 CANCELLED。afterprint 仅做 UI 清理。
5. 返回后显示独立“确认实际打印完成”“标记失败/取消”。完成提交成功才改变任务状态；刷新页面后仍能找到 ATTEMPTED 队列。
6. 重开未确认尝试时提醒可能重复打印。成功任务重打填写原因，失败整任务重试不加价；按纸张补打受 D-02 控制，显示份号、纸张号、正反面/原页码映射及最终补打 PDF。
7. 不在文件内容里附加提取码或广告；标签/核对信息在打印 PDF 外侧。需要标签打印时另立批准需求，不改变用户确认件。

目标打印环境优先桌面 Chromium 并在实际系统验证；Safari/PWA/手机不支持时提供受权 PDF 打开/下载后人工打印回退，并保持人工确认流程。禁止宣称能静默控制打印机。

### G4. 装订中心

按原始文件列任务，显示各分段完成情况、完整册数、装订示意、页序整理要求、实体纸张数。全部打印完成才允许点“已整理且装订完成”。不装订的文件显示无需，不伪造已装订任务。支持异常/有原因撤回；受影响文件需要重印时展示需要重新核对装订状态。

### G5. 装袋中心

以订单为卡片，显示大提取码、目标楼栋、所有文件与任务/份数、照片张数、装订要求与完成情况。逐项勾选实体齐全，再点“确认资料齐全并已装袋”。提交当前清单版本；服务端拒绝时显示 blocker 和对应任务入口。

未打印/装订完成只能展示阻断原因，不显示可以强制跳过的普通按钮。PACKED 才是可配送；打印全完成但未装袋时用户仍为待打印、辅助提示待装袋。撤回 PACKED 填原因；配送中/送达后使用受控纠错，不直接回滚。

### G6. 配送调度与时段

按日期、用户预约、计划时间、楼栋、状态筛选并多选订单。支持新建/发布批次、增减、转移、拆分、合并、指派配送员、改期、延期、取消、删除/恢复。所有批量写操作先显示服务端 preview 的数量、影响订单、时间与原因，再 commit；preview 失效重新预览。

批次详情分别显示“规划中订单”和“可实际配送订单”，允许未完工订单加入计划；开始配送前显示全部阻断项。建议整批 start 采用全有或全无：有未装袋/地址待复核等不合格单就拒绝，管理员先移出或修复。不静默只出发一部分。

用户改楼栋后突出显示待复核；复核确认前不能出发。取消当天配送须填原因，影响用户展示但不清除打印/装订/装袋事实；已送达订单记录保持。时段配置以具体日期创建、开关、容量/截止（如启用）为字段；关闭时段不删除已有订单。

### G7. 地图编辑、系统配置、员工、异常、回收站与审计

- 地图：上传底图，点击新增热点、拖动、编辑楼栋类型/名称/配送开关；归一化坐标相对实际图片区域计算，处理 object-fit 留白、缩放/平移。提供列表坐标编辑与键盘回退。更换图片创建版本，必须确认迁移或重新定位后发布，不能直接沿用错误坐标。
- 配置：打印四种单价、照片单价、两种装订单价，服务开关、照片尺寸/四边 3 mm 初始值、默认翻转、限制、微信二维码/号/电话/引导语、公告、暂停接单、时段、保留策略、账号。未配置值与 0 元必须明确区分。
- 价格变更预览“仅新报价生效”，不改历史订单；配置也带 version 防并发。已确认固定规则（如免费配送、必须装袋）不能伪装成可关闭开关。
- 员工：新增/停用配送员、重置凭证的安全入口、角色展示，密码不明文回显。
- 异常中心：类型、关联订单/任务、阻断级别、原因、处理人、历史、解决动作；简单点“已解决”不能自动把打印标成完成。
- 回收站：对象类型、删除人、原因、服务端删除/到期时间、剩余恢复时间。恢复先预览冲突选择合法关联；永久删除再次确认具体对象与不可恢复影响，不删除仍被引用资产。未批准删除已付款订单时入口禁用并解释。
- 审计：按操作者、对象、动作、时间检索；只读展示重要前后值和原因，不提供普通编辑/清空日志按钮。

## H. 配送员端与管理端共用的交付能力

从个人页进入 `/courier/`，统一基础账号登录并校验当前配送权限；只显示自己批次、楼栋分组、待送数量、提取码、昵称、电话及最新时间安排。打电话使用 tel 链接，避免额外复制负担。不能看到打印资产下载、金额管理、改期、增减批次按钮。管理员进入同一配送组件可处理所有有权任务，角色差异由服务端返回的 allowedActions 驱动。

订单交付页：

1. 先确认当前分配仍有效、订单已开始配送且装袋合法；刷新时动态更新，改派后不得继续上传/提交。
2. 用户点击“获取当前位置”，调用 geolocation，显示获取中、拒绝、超时、无能力、成功和精度；不以手填楼栋替代。只在配送动作中请求，不在普通用户首页请求。
3. 调用 `<input type=file accept=image/* capture=environment>` 作为拍照优先入口，提供相册备用；浏览器不保证直接启动相机。照片显示预览、上传中、成功/重试/移除；移除后 photoAssetId 清空。
4. 正常必须成功上传照片；“跳过拍照”单独填写原因。上传失败不等于已获准跳过，必须主动走例外。
5. 定位失败配送员只可重试/报异常/联系管理员；管理员可用“填写原因跳过定位”。隐藏按钮之外还要应对服务端 403。
6. 照片例外与定位例外可以分别发生，原因不能互相代替。填空白/全空格不算有效原因；长度从契约读取。
7. 可填摆放说明辅助用户找资料，不能当成定位成功。交付提交以服务器成功响应为准；显示交付时间，转已送达，用户凭证同步可见。
8. 无需再次输入提取码；不离线排队自动提交交付。弱网重复点击保持同一幂等 key；断网恢复确认是否已成功后再继续。

配送异常表单选类型、填原因，提交后显示待管理员处理；不允许配送员自行重新安排批次或自动重印。

## I. PWA、缓存、安全与性能

- 一个应用使用唯一根manifest，统一id/name/short_name/start_url/scope、display=standalone、theme_color、192/512及maskable图标；默认进入用户端，角色工作区从个人页进入。
- 使用唯一根scope `/` 的本应用SW，迁移本系统旧/u、管理路径、/courier注册，保留无关应用注册。管理路径变化同步路由和深链接fallback，不再生成独立PWA身份；管理/配送深链接刷新不能404。
- 缓存仅版本化静态 JS/CSS/图标/离线空壳。API、带权限资产、手机号、照片、定位、令牌一律 network-only，并配合 Cache-Control:no-store。禁止默认 Workbox 规则缓存所有 GET/导航内容。
- 离线空壳明确显示离线，禁用付款确认、打印结果、装订、装袋、下单、交付。网络恢复重新鉴权、拉新状态，不默默自动提交旧动作。
- SW 更新提示延迟到当前编辑/打印/交付操作完成，不能突然 reload 丢失编辑。退出/角色切换清理内存状态和私有预览。
- 文件名与日志内容当文本渲染，不直接 dangerouslySetInnerHTML。后端 origin/资产 URL 受控，不能嵌入任意用户 URL。
- 正式部署接 HTTPS，同源 cookie 会话及 CSRF；前端不把管理密码/长期 token 放 localStorage，不引入收集文件/电话的第三方埋点。
- PDF/裁剪/地图编辑懒加载；列表服务端分页，大文档页虚拟化；释放 canvas、object URL 和上传任务，避免移动端内存峰值。
- Web Push、短信通知、微信机器人不属于已确认范围。状态同步先提供轮询，不能用虚假通知功能占位。

## J. 前端开发阶段与必须交付物

1. 契约与框架：三应用目录/路由、UI token、生成 API 类型、会话与权限、空错误状态、决策清单；提交运行说明。
2. 用户下单闭环：真实上传/转换状态、分段/照片、最终 PDF、报价、地图/时段、下单、微信引导、订单详情/待确认修改。
3. 管理生产闭环：搜索收款、打印视图与尝试结果、整任务重试、装订、强制装袋；用户端同步。
4. 配送闭环：地图热点编辑、时段与批次、地址复核、配送员交付、定位/照片例外、异常与用户凭证。
5. 运营与交付：配置、角色、审计、回收站、PWA、兼容性/端到端验证；根据已批准决策实现选配模块。

前端独立开发可使用 MSW 等按 OpenAPI 构造开发 mock，并明确标识开发模式；生产构建禁止自动回退 mock。后端不可用时显示服务错误，不能把假下单当成功。最终交付必须切真实 API 完成闭环。

交付源代码、锁文件、环境变量示例（无真实密钥）、三端启动/构建命令、API 类型生成命令、测试报告、页面清单、部署静态目录说明、已批准/未决清单、真实浏览器与真机人工验收清单。每阶段报告修改点、验证命令与实际结果；未测试项明确写未测试，不声称已全部通过。

## K. 前端验收清单

- FE-01：多文件并分别配置，最终是一单/一码；无法转换的文件不能提交。
- FE-02：前 4 页单面和后 6 页双面×2，界面两个任务、纸张合计 14，分别可打开打印。
- FE-03：7 页双面×3，最终预览 24 PDF 页、12 张纸，显示三份边界与空白背面。
- FE-04：越界、重复、遗漏、份数非法显示字段错误；主动跳页与误漏页区分。
- FE-05：改任意输出设置后旧 previewAcks 失效；切换已完成的新版本不能继续打印旧文件。
- FE-06：照片两种适配、旋转、EXIF、四边不对称边距均与服务端最终 PDF 一致。
- FE-07：单钉/双钉 CSS 位置清晰、动态价格；两个文件不能合并装订，D-04 未批准不可上线收费。
- FE-08：无价目、无二维码、无配送时段、服务停用均有真实提示，不能靠假默认值提交。
- FE-09：下单超时重复请求使用同 key，最终只有一个订单；专属链接不会进入普通微信联系文案。
- FE-10：用户修改与管理员确认收款竞态收到 409 后明确提示；不能旧金额确认。
- FE-11：打印窗口取消/关闭，任务仍待结果确认；刷新后找到未确认尝试；人工确认后才完成。
- FE-12：未装订不能装袋，未装袋不能配送；禁用原因可定位到相关文件/任务。
- FE-13：取消当天配送显示原因和最新安排；用户只看到自己的订单，不看到同批其他人。
- FE-14：地图缩放、图片留白和横竖屏切换时热点正确；换底图要求迁移确认。
- FE-15：配送员定位失败无跳过权限；管理员原因跳过可用；照片失败/无原因不能交付。
- FE-16：交付重复提交不重复创建凭证，照片/坐标/时间或例外在用户端正确显示。
- FE-17：批次恢复冲突不会覆盖新分配；永久清理有明确确认，已清理文件显示已过期。
- FE-18：单应用安装/启动、个人页按权限切换工作区/返回用户端、各工作区刷新深链接；离线空壳不泄漏私有数据，不能离线执行业务。
- FE-19：360/390/768/1440 布局、键盘、长文本、iOS 安全区通过；真实打印机核对纸型/翻转/张数。
- FE-20：生产构建没有 mock 回退；无权限直达路径、会话过期、改派、断网、签名/资源过期均正确处理。

至少自动化覆盖组件输入校验、状态标签、API 错误、冲突处理、完整下单→收款→打印→装订→装袋→配送的 E2E。原生打印框、PWA 安装、定位权限和相机必须额外人工验收，不能只用自动化模拟宣布完成。

## L. 给开发工具的最终执行指令

现在请先阅读完整文件，输出你识别到的硬约束、D-01—D-08 未决项、路由清单、前后端接口依赖和分阶段计划。先将现有单一 role/store 拆分为客户、员工和订单授权状态，保留已有 UI；报价 DTO、结算页和收款请求同步升级。对于已批准范围持续实现并验证；遇到未批准业务只暂停对应分支，不擅自定规则。全过程以真实 API、不可变最终 PDF 和可审计业务动作完成交付，禁止用漂亮静态页面替代系统功能。

## 本轮新增业务链交付要求（2—7及追加费用，已确认）

- 链接二维码：订单专属链接、邀请链接、管理员合法HTTPS网址均在本地生成、可预览下载；保留fragment和授权边界，不新签发权限/延长到期、不送第三方。
- 批量送达：同一批次、同一楼栋多选订单，一次上传同一张照片并可填共享文字描述；每单独立核验权限/当前执行/版本/照片/定位或审批例外，整批原子提交，重复不重复送达/发券/工资。各客户只可见自己的凭证。
- 文字送达描述选填，单单和批量均展示；没有照片仍必填免拍原因，不代替定位及审批。
- 统一身份与界面：一个PWA、一个普通客户账号登录入口。正常注册客户后由超管给该accountId授予管理员/配送权限，权限跟账号跨设备；个人页仅在有对应权限时显示工作区入口，普通用户不见；所有员工/超管都可返回用户端。取消三个初始入口/三独立PWA/第二套员工登录和密码。保留工作区路由与细分服务端能力；保护最后超管、拒绝自提权和跨客户身份越权，统一账号两台设备额度。
- 员工优惠：最多1—6级，每级配置百分比减免，员工权限有效就长期享有；超管原打印单100%免单，仍人工零元结算。原打印订单含装订整单参加员工折扣，与券/满量三择优不叠加；资格服务端判定，已提交锁价，撤权只影响新报价。
- 工资统计：可选默认/员工覆盖的每单定额或折后实收比例模式；实际送达记给当时配送员并锁策略，按月账本和人工发放记录。退款后按原比例追加调整，不修改已发记录；免费重做/补送不重复计件，可有理由手工补记；定额不随追加费用变化，不自动转账。
- 追加费用：管理员在原单下按笔新增最终金额、必填用途、是否参加比例工资，提交即生效；客户可查看并提异议。原单付款前后/送达后均可追加，新增应收/实收/退款/欠收独立记录，原Quote/收款/履约事实不变。追加金额不参加任何原单优惠；参与比例工资的部分只按真实净收款计算，送达后追加收退款更新原配送员的工资差额，未收/未勾选不计入。
- 前端入口、严格DTO、迁移、授权、并发版本、持久化幂等、收退款/工资审计、导出、删除引用/保留及真实联调全部覆盖，不接受只改Prompt/文案/演示。详细最终答案及收费公式见 `docs/superpowers/specs/2026-10-30-dormprint-v1-adjustments.md`。
