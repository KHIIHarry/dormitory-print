# 全端页面清单

管理路径以下使用本地默认 `/manage`，可构建时配置。三端共用组件，均有响应式布局。`/preview` 仅用于开发页面审阅，正式构建不开放。

| 角色 | 路由 | 页面 / 主要内容 |
|---|---|---|
| 用户 | `/u/` | 首页：营业公告、文档/照片服务、价目与流程；无单独配送下单 action |
| 用户 | `/u/new` | 多文件工作台、拖拽、类型与大小检查、读取/转换/失败重试 |
| 用户 | `/u/new/settings` | 普通与高级分段、页码分配、排除页、颜色/单双面/翻转/份数、CSS 装订示意 |
| 用户 | `/u/new/photo/:id` | 真实比例照片、四边留白、横竖/旋转、裁剪拖动/缩放、完整保留 |
| 用户 | `/u/review` | 逐任务 PDF.js 最终预览、逐项确认、报价、联系信息、地图楼栋、开放时段 |
| 用户 | `/u/orders/:id/contact` | 成功、短提取码、线下微信文案、真实二维码配置/缺失提示、刷新状态 |
| 用户 | `/u/orders` | 当前会话订单、搜索与状态筛选 |
| 用户 | `/u/orders/:id` | 时间线、文件/生产、最新安排、交付凭证、待确认修订/撤销、配送前改楼栋 |
| 用户 | `/u/help` | FAQ、单价表、装订与配送规则 |
| 用户 | `/u/profile` | 兼容旧书签，重定向账号页 |
| 管理 | `/manage/login` | 员工认证、演示入口、密码显示切换 |
| 管理 | `/manage` | 统计、生产待办、今日批次、最近订单 |
| 管理 | `/manage/orders` | 多条件订单检索、URL 筛选、分页界面 |
| 管理 | `/manage/orders/:id` | 文件→任务层级、版本/日志、收款核对、备注/优先级、地址复核、管理员交付入口 |
| 管理 | `/manage/print` | 按订单/类型/批次查看、结果队列 |
| 管理 | `/manage/print/:id` | 最终 PDF.js、独立分段、打印核对、尝试、实际结果确认、整任务重打 |
| 管理 | `/manage/binding` | 按原文件装订、分段完成阻断、人工确认 |
| 管理 | `/manage/packing` | 大提取码、逐项实体资料核对、强制装袋、阻断入口 |
| 管理 | `/manage/batches` | 批次概览、新建、状态筛选 |
| 管理 | `/manage/batches/:id` | 规划/可配送区分，增减/转移/拆分/合并/改期/取消/删除，影响预览、发布和整批开始 |
| 管理 | `/manage/slots` | 具体日期时段、开放开关、容量、关闭不删旧预约 |
| 管理 | `/manage/map` | 底图、热点新增/拖动/数值编辑、楼栋类型/开关、新图迁移确认与发布预览 |
| 管理 | `/manage/settings` | 价格、服务限制、照片校准、联系二维码、公告、未决业务清单 |
| 管理 | `/manage/staff` | 账号/角色、停用与凭证重置表单，不回显已有密码 |
| 管理 | `/manage/exceptions` | 异常类型、原因、状态和处理记录，不自动推进生产 |
| 管理 | `/manage/trash` | 24h 恢复窗口、原因、恢复预览、永久清理确认 |
| 管理 | `/manage/audit` | 操作人、动作、对象、时间、原因，只读 |
| 配送 | `/courier/login` | 独立员工认证 |
| 配送 | `/courier/` | 分配批次、待送数量 |
| 配送 | `/courier/batches/:id` | 按楼栋分组的任务 |
| 配送 | `/courier/orders/:id` | 提取码、楼栋、联系方式、tel、当前安排，无打印文件与财务管理 |
| 配送 | `/courier/orders/:id/proof` | 设备真实定位、拍照上传、照片例外、管理员定位例外、交付确认 |
| 配送 | `/courier/orders/:id/exception` | 异常类型、详细原因与提交 |
| 配送 | `/courier/profile` | 兼容旧书签，重定向账号页 |

PWA：`/u/manifest.webmanifest`、`/manage/manifest.webmanifest`、`/courier/manifest.webmanifest`；各自路径 `sw.js` 与 `offline.html`。

## v1.3 新增与增强

| 角色 | 路由 | 页面 / 主要内容 |
|---|---|---|
| 用户 | `/u/account/login`、`/u/account/register` | 登录、邮箱注册、邀请码、游客回退、显式关联提示 |
| 用户 | `/u/account/verify` | 验证/重发/更换待验证邮箱，fragment 凭据明确消费 |
| 用户 | `/u/account/forgot`、`/u/account/reset` | 邮件找回与新密码设置、过期状态 |
| 用户 | `/u/account` | 昵称、通知偏好、改密、换邮箱、游客关联、退出 |
| 用户 | `/u/account/security` | 当前设备/会话、安全状态与会话撤销 |
| 用户 | `/u/rewards`、`/u/invite` | 券状态、冻结原因、邀请分享和奖励进度 |
| 用户 | `/u/order-link` | 高熵凭据兑换、单单导入、来源撤销约束 |
| 用户 | `/u/review` | 自动择优、指定/不用券、账号结算采用/撤回、金额重新确认、真实0元 |
| 管理 | `/manage/customers`、`/manage/customers/:id` | 脱敏客户筛选、详情、业务记录、停用/恢复/恢复邮件 |
| 管理 | `/manage/settings/mail` | SMTP/凭据保留或明确清除、连接/发送状态 |
| 管理 | `/manage/settings/mail-templates` | 七种中文模板、变量、安全HTML预览、测试、恢复默认 |
| 管理 | `/manage/settings/promotions` | 新客/受邀/邀请人固定金额活动、预算/限额、资格规则快照 |
| 管理 | `/manage/settings/volume-discounts` | 张数/折数档位、启停/生效、影响预览、版本/审计原因 |
| 管理 | `/manage/promotions/records` | 券/奖励/风控筛选、独立让利统计、受控异步导出 |
| 管理 | `/manage/account` | 工作账号、改密、验证邮箱、安全会话管理 |
| 管理 | `/manage/account/setup`、`forgot`、`reset`、`verify` | 员工初始化/找回/一次性凭据明确确认 |
| 配送 | `/courier/account` | 配送工作账号及安全会话 |
| 配送 | `/courier/account/setup`、`forgot`、`reset`、`verify` | 配送员工初始化/找回/验证；受限会话先改密 |

管理路径仍由 ADMIN_BASE_PATH 配置。mustChangePassword 会话只允许自身改密与退出，不加载业务数据。预览目录入口维持原字号的文字样式，按相关功能同行排列，无可见分类标题。
