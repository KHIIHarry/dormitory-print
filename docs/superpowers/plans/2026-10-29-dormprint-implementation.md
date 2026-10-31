# DormPrint Frontend Implementation Plan

**Goal:** 完成需求中的三端页面、高保真响应式界面、可操作开发演示和真实 API 边界。
**Architecture:** React Router 三端路由，统一 UI、业务校验、内存演示数据与 typed API transport。TanStack Query 负责真实配置读取。
**Tech Stack:** React、TypeScript、Vite、Lucide、PDF.js、Vitest、Playwright。

## Global Constraints
- 用户端无管理入口。未决 D-01—D-08 不批准；生产禁止 mock 回退。
- 本次仅本地前端，不购买或部署服务。文件/联系方式不持久化。

## Tasks
- [x] 1. 基础契约与业务测试：src/lib/domain.ts、tests/domain.test.ts。先运行失败测试，再实现页码合法性、单双面纸张、强制装袋、交付权限。
- [x] 2. 共享视觉与导航：src/components、src/styles.css，按 DESIGN.md 实现桌面和移动布局及错误/离线/空状态。
- [x] 3. 用户闭环：src/customer。上传、PDF.js 页数、文件设置/分段/装订、照片裁剪、预览报价、楼栋时段、下单、微信引导、列表详情、帮助与个人页。
- [x] 4. 管理生产：src/admin。认证、仪表盘、订单筛选、收款核对、独立打印视图、实际结果确认、装订、逐项装袋。
- [x] 5. 调度运营：批次与影响预览、时段、地图热点、配置、员工、异常、24h 回收站、只读审计。
- [x] 6. 配送：src/courier。独立认证、楼栋批次、任务详情、真实定位请求、拍照与例外、交付与异常。
- [x] 7. PWA 和接口：三端独立 manifest/SW，network-only 业务数据。生产构建禁演示，生成 API 类型。
- [x] 8. 验证与交付：npm test、npm run build、浏览器全部路由与响应式/业务闭环。README、页面清单、验收报告与剩余联调边界。

实际范围与未验收项目见 docs/qa/TEST-REPORT.md；完成标记表示本次前端页面/演示交付，不表示真实后端与真机验收。
