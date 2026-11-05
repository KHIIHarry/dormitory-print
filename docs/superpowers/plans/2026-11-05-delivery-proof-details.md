# 订单详情交付凭证 Implementation Plan

> **For agentic workers:** Execute inline with executing-plans. Preserve the current workspace's backend fixes and do not commit unrelated work.

**Goal:** 在现有管理端和配送端订单详情中完整展示已交付凭证，不新增路由或导航。

**Architecture:** 复用一个只读 `DeliveryProofPanel`，沿用 Panel、Button、Modal、现有配色与原生焦点管理。管理端插入打印文件之后、订单动态之前；配送端以凭证卡替代已送达的单行提示。支持管理订单的 `photoUrl` 和配送订单的 `photoAssetId`，后者通过原受权资产接口读取。

**Tech Stack:** React / TypeScript / CSS / Vitest / Playwright Chromium + WebKit。

## Global Constraints

- 遵循 DESIGN.md、src/AGENTS.md、docs/UIUX-移动端布局规范.md 和现有前端开发指引。
- 不增加导航入口、业务写操作、定位请求、第三方地图或私有内容持久化。
- 照片完整显示，不裁掉凭证；当前页复用 Modal 放大查看，支持关闭、Esc、焦点返回。
- 显示服务端交付时间、WGS84 坐标、精度、采集时间、独立免拍照/定位例外原因、摆放说明。
- 未交付不显示凭证卡；已送达但无凭证显示准确的暂无凭证状态。
- 照片加载、失败、重试不得影响交付事实；权限仍由现有后端校验。
- 320/375/390/430/768/1440px，子项 min-width:0，正文14–16px，交互目标≥44px；不在根节点隐藏横向溢出。

### Task 1: 受权照片适配与只读凭证卡

**Files:** `src/lib/types.ts`、`src/courier/task-state.ts`、新增 `src/components/delivery-proof.tsx` 与 `delivery-proof.css`。

- [x] 先在 `tests/delivery-proof-view.test.ts` 断言 `normalizeProof({assignmentId:'a',photoAssetId:'photo',deliveredAt:'2026-11-05T05:10:38Z'})` 保留照片资产 ID；运行观察失败。
- [x] 在 Proof 类型加入可选 photoAssetId，normalizer 保留该字段；卡片从 photoUrl 或原资产接口显示照片。
- [x] 卡片复用 Panel 和 Modal，照片 object-fit:contain；详情在窄屏垂直排列，长说明和连续文字可换行。
- [x] 照片失败时显示重试，不把失败当免拍照；缺原因时显示未记录，不编造原因。

### Task 2: 接入两个现有详情

**Files:** `src/admin/orders.tsx`、`src/courier/pages.tsx`。

- [x] Playwright 挂载实际 AdminOrderDetail、CourierOrder，先断言“交付凭证”卡可见并观察现有页面失败。
- [x] 在已有页面插入共享组件，以订单/身份/凭证为 key，切换订单或身份时释放照片预览。
- [x] 保留配送中的拍照交付及异常操作，已交付凭证只读，不重复提交。

### Task 3: 移动端、状态与构建验收

**Files:** 新增 `tests/e2e/delivery-proof-details.spec.ts`，更新 `playwright.mobile-ui.config.ts` 的 testMatch。

- [x] 覆盖照片与真实信息、免拍照与定位例外、缺失凭证、图片403后重试、放大关闭与焦点恢复；确认无新增导航或业务提交。
- [x] Chromium/WebKit 在六种宽度测量页面、卡片内容区、照片和按钮，保存截图。
- [x] 运行 `npm run test:e2e:mobile-ui`、`npm test`、`npm run build`、`git diff --check`。
- [x] 成批检查桌面/手机截图，必要修复后统一复核；报告模拟范围，不宣称 iPhone 真机通过。

验证结果与模拟边界见 `docs/incremental/2026-11-05-delivery-proof-ui.md`。
