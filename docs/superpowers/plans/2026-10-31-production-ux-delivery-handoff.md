# 2026-10-31 生产使用反馈：持续交接

用户要求 17 项，前端由 Codex 负责，后端规则/API/对接由 codex-dsh-desktop 完成，Codex 审查。用户最新指令覆盖旧的“只有到配送时间才可开始配送”；时间均为预计。绝不直接改线上数据、重置数据库或擅自发布。当前工作区 `/path/to/dormprint`，基线 `cbde6b5`；请先 `git status` 保护其他并行工作和用户数据。客户端生产域名见截图，未经明确授权不访问或修改私有数据。保留 10s 后台轮询、前一轮修复的静默刷新、安全边界、真实收费及 PDF 确认链。

## 待完成清单

1. 移动底部导航边距、安全区、比例；不遮挡内容、动态浏览器工具栏。
2. 弱网所有长请求/操作明确反馈并同步防重复：用户动作即禁用、显式加载、保留请求幂等；不可误把正常 10 秒后台轮询显示为全页遮罩；错误反馈可重试并保留输入。
3. DOCX 等文档原文件预览无法显示；共享 DocPreview 修好授权规范化源、加载/失败/图片路径；全屏放大真实可读，触摸缩放与分页清晰，不影响最终 PDF 逐项确认及安全（必须已渲染当前正确 hash 方可确认）。
4. 文件大小显示：小于 0.05 MB 的真实非零文件不显示 0.0 MB（KB/bytes 自适应）；确认服务端 DTO 实际 size 及恢复草稿场景。
5. iOS 聚焦输入自动放大：移动端输入/选择/文本框至少16 CSS px，保留辅助功能及 pinch zoom，不设置 user-scalable=no。
6. 提交验证失败准确滚动并聚焦第一个错误字段，配无障碍描述和偏移；包含手机等服务端/客户端错误，不能为未授权来源自动执行危险动作。
7. DSH 生产报告：map 发布后 public_config 合并额外字段导致 500；严格公开契约白名单，不泄露元数据，回归真实发布/读取。DSH 权限 worker 处理。
8. 用户查看是否确认：只有服务端真实确认收款/零元结算后显示浅绿不可点击确认信息；等待/取消/失败等不应误认已付款。新订单数据异步刷新保持按钮防重。
9. 管理详情左右分栏巨大空白，优化所有相同结构按自然阅读顺序布局；保留操作、权限及移动响应。
10. 打印任务页：不改字体大小，规范间距，按份数/单双面/颜色/页数分色浅底标签、突出当前实际打印设置；页码只展示用户选择的实际页（不能改变 PDF 内容/账务），复用现有 DocPreview 和已授权最终 PDF，不新造预览；人工打印完成、补打语义不变。
11. 明确跳过页码字段与上方添加分段保留舒适间距。
12. 批次、各处卡片说明/按钮/表格之间留白，按类似组件共享 CSS、勿放大字体。
13. 图16 账号页移动适配，正确页边距、宽度和动态字体；修正输入自动放大根因。
14. 图17 新建批次标签/输入/帮助文本排版，统一控件尺度。
15. 预计时段不限制开始配送，仍校验发布、订单已装袋、配送员权限等，不允许越权/破坏状态机。DSH 配送 worker 处理。
16. 新建配送批次选择已配置配送时段，依据 slotId 服务端数据权威快照（日期时间只读），关联客户所选配送时间；支持安全删除不用的时段和批次，不孤立进行中订单/历史账务，不物理破坏真实数据；明确冲突错误。前后端共同联调。DSH 配送 worker 处理服务端。
17. 统一 ISO 时间展示（上海/用户清楚时区，一致格式，非法值优雅占位）；API原值/排序/存储不改变。

## 当前 DSH 分工（进行中）

- `dormprint-admin-align`：delivery.py / delivery_schemas.py / tests/test_delivery*；落实 #15-16，不操作线上。报告 `docs/qa/2026-10-31-delivery-flow-handoff.md`。
- `dormprint-permissions`：config.py / tests/test_public_config_map_metadata.py；#7，不编辑 delivery.py。报告 `docs/qa/2026-10-31-public-config-fix.md`。
- `dormprint-courier-ui`：media.py / media_schemas.py / tests/test_media_preview_metadata.py，调查 #3/4 的授权预览和文件字节接口。报告 `docs/qa/2026-10-31-media-preview-handoff.md`。
- 调用 DSH `dsh_get_progress`/`dsh_wait_worker` 跟进并审查变更；仅报告完成不代表验收。若额度到达限制，补充实际状态、未完成项和清晰下一步，避免误报完成。

## 初步源码事实

- `src/components/doc-preview.tsx` 对 pdfUrl 同源认证后 pdf.js Canvas，非本地 PDF（DOCX）的原文件直接显示“预览资源不可用”；放大是再次嵌套普通 Modal 和 DocPreview，页面无法真正铺满/双层弹窗。共享组件已被客户设置、最终确认和后台打印任务引用。
- `src/components/ui.tsx` 文件大小 `(file.size / 1024 / 1024).toFixed(1)`，小文件四舍五入为 0.0 MB；非零必须显示合理 KB。
- `src/lib/store.tsx` 每 10 秒可见页面刷新。`src/components/feedback.tsx` 最近已修正后台刷新导致的版面闪动，保持后台静默，不能为每次轮询插入顶栏进度。
- `src/customer/orders.tsx` 订单成功页当前“查看是否已确认”始终按钮；须以 settlement/status 真实服务端事实作判断并考虑退款/取消/零元结算。
- `backend/app/delivery.py::_start_blockers` 有 `now < batch.starts_at` gate，与用户新要求冲突；后端 worker 正处理。
- 移动导航 `.bottom-nav` 在 `src/styles.css` 的移动断点；账号页面 `src/customer/account.css`。

## 已完成 / 当前状态

- 刚才“周期刷新闪白”任务：正常后台同步静默、保留已加载空态、修正 /manage/ 路由；前端 286 测试和构建通过。更新本地 8080 的 proxy 容器已启动，待确认 healthy 状态。代码当前已在基线 HEAD（新指令到达时 git 工作区干净）。
- 本次 17 项：已启动上述 3 个 DSH 后端任务，前端已完成问题定位，代码尚待修改；不得声称整批已完成。

## 2026-10-31 本轮实际进展（接手前必读，覆盖上方旧状态）

### 已落地，仍需端到端验收

- DSH 配置 worker 已在 `backend/app/config.py` 将公共配置按 `PublicConfigView` 白名单投影；具体见 `docs/qa/2026-10-31-public-config-fix.md`。Codex 复跑配置与媒体契约合计 **5 passed**，配送套件 **33 passed**。
- DSH 媒体 worker 证实服务器已提供受权 `previewUrl/normalizedUrl`，并非需要新接口。`src/customer/settings.tsx` 已根据资产状态加载其标准化 PDF；`src/customer/upload.tsx` 上传后采用后端实际字节数；`src/components/ui.tsx` 与 `src/lib/display-format.ts` 对小文件显示 KB/B。`src/components/doc-preview.tsx` 的放大改为全屏 Modal，并保留当前页；尚未真机验证捏合缩放和 Safari。
- `src/lib/display-format.ts` 为 ISO 日期提供上海时间可读格式，已应用订单、配送和部分管理页，尚须全仓检查所有时间字段。
- `src/lib/request-activity.ts` 和 `src/main.tsx` 显示用户写请求的状态，排除 GET 轮询和 `/customer/session`；并发计数/重复结束有测试。尚须对全部用户发起的慢 GET、路由跳转和防重复做弱网实际端到端检查，不应声称全覆盖。
- 订单联系页依据后端 settlement.confirmedAt 显示非按钮浅绿确认；零元结算文案区分，未做生产订单联调。提交表单已给昵称、手机、楼栋、时段、备注添加首个客户端错误滚动聚焦，尚须检查服务端字段错误。
- 手机导航安全区、账号页边距、输入控件 16px、跳页字段间距已调；管理订单详情改为主内容后展示补充区。尚需桌面和移动端截图检查、检查其他同类页面。
- 正式打印任务页已重用 DocPreview 加载任务授权 PDF，分色显示份数、单双面、色彩、纸张和范围，订单状态用 Badge；尚需验证任务范围是否只代表实际用户选择、排除页是否真正不出现在最终 PDF、整页布局及真实任务授权。
- 已同步 DSH 生产 `POSTPONED -> PUBLISH`、`PUBLISHED -> START`，并加筛选、统计和配送端提醒；`src/admin/logistics.tsx` 新建批次选择已有启用 slot 并提交 `slotId`；时段移除走受控归档 API、保持历史。批次原有 DELETE 流程需继续验收；尚未加显示已归档、批次详情 slotName 展示和时段冲突操作体验。
- 前端完整测试在上述大部分改动时为 **288 passed**、`npm run build` 成功；此后增加时段归档、预览标签、布局和新测试，已重新 `tsc -b` 与针对性 4 tests 通过，必须最后再次完整测试与 build。`git diff --check` 通过（最终变更后需再跑）。

### DSH 配送接口审查 / 必须继续的事项

- DSH 已完成 `backend/app/delivery.py`, `delivery_schemas.py`, `tests/test_delivery.py`；详细规则/接口见 `docs/qa/2026-10-31-delivery-flow-handoff.md`。`POST /admin/batches` 带 `slotId` 时禁止同时带 date/time；slot 删除为归档；ORDER_SLOT_MISMATCH 时必须由客户确认新时段；预计时间不再阻止发布/开始，其他权限和状态检查保留。
- `docs/openapi.yaml` 因配送契约生成而变更；需要核对和更新生成的 `src/lib/api-schema.ts`（运行项目 contract 命令前先查看脚本，避免覆盖其他工作）。
- 至少真实或隔离环境浏览器验证：DOCX 上传 -> 设置原文件预览 -> 最终 PDF 确认及放大翻页、低网速点击防重、无效手机滚动聚焦；配送时段 -> 批次 -> 订单分配 -> 预计时段外开始 -> 安全移除；取消、退款、零元结算不能错误显示收款。
- 检查 `/customer/session` 之外所有后台写请求会不会使全局状态提示每隔几秒闪动；只允许用户主动操作的进度提示，不得恢复原有后台刷新白屏。
- 严禁未经明确授权访问/修改线上私有订单或发布；保留工作区所有尚未提交的变更，不 reset/stash。当前工作是部分实施，不可向用户宣称 17 项全部完成。

### 最后同步（17:07）

- 完整前端检查：`npx vitest run` **50 文件、290 tests passed**；`npm run build` 成功；`git diff --check` 通过。之后批次时段目录切换至真实 `/admin/slots` 并修正只改配送员时不应发送 date/time 导致时段脱钩，已跑 `tsc -b`，还需重跑相关测试/构建。
- 已用 `npm run api:generate` 从更新的 `docs/openapi.yaml` 更新 `src/lib/api-schema.d.ts`，通过 `tsc -b`。
- 本地 `http://127.0.0.1:5199/u/` 演示首页可正常加载，尚不能代替真实账户下业务流程及移动端验收。临时 Vite 会话需关闭；未触碰线上。
- 已再次向 `dormprint-admin-align` 排队交付只读配送契约审查，必要时由其修后端，并保留前端改动。检查该 worker 当前状态与其后续报告，勿仅以 queued 推断完成。

### 并发问题（DSH 正处理中，未验收）

`dormprint-admin-align` 审查发现归档时段与 `backend/app/commerce.py` 提交/修改订单存在并发窗口，可能使进行中的订单引用刚归档的时段。已将 commerce.py 和相关测试纳入该 worker 后端所有权，要求先复现、按项目统一锁顺序修复并测试，同时检查配送开始时的时段一致性；不要把先前 33 个配送测试通过当作这项并发问题已经通过。请等待其报告、审查 diff、重跑测试后再考虑上线。
- 全屏 PDF 将 pdf.js 目标宽度从 720 提高到 1800，减轻放大模糊；保留授权、当前页和取消渲染。最近提交提示页面在 settlement 已确认后同步修改了顶部说明，避免继续提示付款；这两项后只重新通过 `tsc -b`、4 项聚焦测试与 `git diff --check`，需最终 build/完整测试和真机验收。
- 17:09 DSH 已接受扩大 commerce.py 范围修复归档/订单并发竞态，状态 running；必须检查其最终代码、死锁风险和并发测试，不能按之前 33 项通过判定此次并发修复成功。

### 后续 UI 修复（17:37）

- 根据新截图修正管理端订单详情的独立列布局、订单链接操作按钮排版，以及打印任务的实际开印、补打和结果卡片间距；预览普通/全屏分辨率提升至 1440/2200 像素，PDF 渲染限制最大约 800 万像素以控制移动端内存，保留渲染完成后再提交画布的行为。
- `npm run build` 成功；`npx vitest run` **291 tests passed**，含新增的 PDF 高清和内存上限测试。演示订单页在默认宽度及 390px 下检查，卡片独立排列、无横向溢出；真机 PDF 缩放和真实订单打印流程仍须验收。
