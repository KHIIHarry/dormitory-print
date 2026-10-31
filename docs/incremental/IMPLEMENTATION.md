# DORMPRINT-IDENTITY-GROWTH-1.3 增量实施

本次直接采用用户指定 01/03 的已确认规则；04 用于共享 API 契约对照。保留原页面与主状态机，保留预览目录文字入口分类排版。

## 复用 / 新增 / 微调
复用 components/ui、PDF.js、domain 页码/用纸、各端布局与全部生产/配送页。
新增 growth 类型/客户端/开发适配、独立 identity provider、客户账号/授权链接、管理增长运营模块及员工安全。
微调 store 会话隔离、review 报价/优惠/零元、订单快照与收款、导航/路由、PWA 敏感路径规则、决策文档。

## 不变量
- 邮箱账号、员工、游客、订单授权独立；客户退出不改员工角色。私有数据仅内存；异步读取带身份代次。
- Quote 为严格结构，totalCents 可以为0，不回退本地原价。正常价格行非负，最多一个获胜优惠。
- 满量仅本单 A4 实体纸张，只折A4费。配置默认关闭、空档位；同额满量优先，不占券。
- 原订单锁价；零元必须人工 ZERO_PAYABLE；收款引用 currentQuoteId。邀请奖励以首笔实付正数且成功送达为准。
- 链接仅单单scope。token只读fragment后立即从地址清除；安全邮件GET不消费token。导入不转移owner。
- DEMO明确显示，生产请求无模拟成功/假发信；无后端仅交付前端接入和开发演练，不虚报真实SMTP/数据库事务已联调。

## 实施任务
- [x] 1. growth-types、OpenAPI diff、typed client、报价/权限/并发代次测试。
- [x] 2. 独立IdentityProvider与安全resource hook；保留同标签Checkout内存。
- [x] 3. 客户账号全套、优惠/邀请/订单链接页面。
- [x] 4. SMTP/模板/活动/满量/运营记录/客户管理页面。
- [x] 5. 员工账号/初始化/邮箱/会话、员工管理完善。
- [x] 6. 下单/修订优惠、金额确认、成功链接、订单权限、管理员签发/收款/统计整合。
- [x] 7. 测试、构建、浏览器检查、页面清单与真实联调边界更新。

## 分工接口
页面只依赖 src/growth/client.ts 的 growthRequest<T>(path,{method?,body?}) 与 useGrowthResource<T>(path)；API前缀隐含/api/v1。
useIdentity() 提供 customer、staff、orderAccess、generation、refreshCustomer、refreshStaff、loginCustomer(email,password)、logoutCustomer、demoCustomer、setStaff、setOrderAccess、invalidate。
Customer DTO: role CUSTOMER / authKind ANONYMOUS|REGISTERED / account? / capabilities:string[]。
Account DTO: id,emailMasked,displayName,verified,status,version,notificationPreferences。
Staff DTO: id,role ADMIN|COURIER,name,username,emailMasked?,emailVerified?,mustChangePassword?,capabilities:string[]。
useGrowthResource 返回 {data:T|null,loading:boolean,error:string,reload:()=>Promise<void>}。导出统一 CustomerAuthForm({mode:'login'|'register'|'forgot'|'verify'|'reset',embedded?,onSuccess?}) 供结算登录弹窗复用。

实际测试与联调边界见 `../qa/2026-10-30-增量验收.md`，勾选表示前端实现交付，不表示真实后端/SMTP/设备已验收。
