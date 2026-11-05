# 管理端与配送端订单详情：交付凭证展示

## 完成范围

依照 `DESIGN.md`、`src/AGENTS.md`、`docs/UIUX-移动端布局规范.md` 和前端开发指引，在现有详情中加入只读凭证卡，未添加导航或路由。

- 管理端：位于打印文件与任务之后、订单动态之前。
- 配送端：位于订单信息之后，替代原先仅交付时间的已送达提示。
- 显示照片、服务端交付时间、WGS84 坐标与精度、定位采集时间、摆放说明；免拍照和定位例外分别呈现。
- 照片完整呈现，复用现有 Modal 放大；支持 Esc、关闭与焦点返回。WebKit 指针点击时显式记录照片按钮焦点，避免关闭后焦点丢失。
- 图片加载、403/读取失败后重试、无照片原因、已送达但无凭证均有独立反馈。图片失败不会改变已交付事实。
- 配送端兼容后端的 `photoAssetId`，交付确认和刷新读取均能通过原受权资产接口显示照片。
- 长中文与连续英文标识可换行，桌面双列、窄屏单列，44px 交互目标，卡片内容区不溢出。身份切换立即清理旧照片弹窗；不新增私有缓存、定位调用或业务提交。

## 修改文件

- 新增 `src/components/delivery-proof.tsx`、`src/components/delivery-proof.css`：共享凭证展示与局部样式。
- `src/admin/orders.tsx`、`src/courier/pages.tsx`：接入现有详情。
- `src/lib/types.ts`、`src/courier/task-state.ts`：保留受权照片资产 ID。
- 新增 `tests/delivery-proof-view.test.ts`、`tests/e2e/delivery-proof-details.spec.ts`；更新 `playwright.mobile-ui.config.ts` 和现有 `tests/e2e/v1-courier.spec.ts` 的成功展示断言。

## 实际验证

先观察单元测试因丢失照片资产 ID 失败，以及两个现有详情因没有交付凭证标题失败，再实现。

| 命令 | 结果 |
| --- | --- |
| `npm run test:e2e:mobile-ui` | 36 passed，9.0s；包含 Chromium/WebKit 原生月份回归与交付详情 |
| `npm run test:e2e -- tests/e2e/v1-courier.spec.ts --project=mobile` | 6 passed，13.4s；开始批次、原子批量交付、拒绝保留、定位审批与单单交付 |
| `npm test` | 67 个文件、382 项测试通过 |
| `npm run build` | TypeScript 与 Vite 生产构建通过 |
| `git diff --check` | 通过 |

宽度覆盖 320、375、390、430、768、1440px，测量页面 scrollWidth、横向偏移、卡片内容区、照片及交互目标。额外覆盖长说明、照片403后重试、320px失败态高度、缺失凭证、独立例外、Esc焦点恢复和身份切换。

已成批检查两端桌面与手机截图，修正 Safari 焦点和窄屏失败态后复核。截图位于 `output/delivery-proof-qa/`：

- `admin-webkit-390.png` / `admin-chromium-1440.png`
- `courier-webkit-320.png` / `courier-webkit-1440.png`
- `admin-webkit-photo-dialog.png` / `courier-webkit-photo-error.png`

**边界：** 浏览器使用受控接口/图片测试数据挂载实际页面组件，另运行真实 API 模式下的网络契约流程；不是生产服务器、真实用户资产或实际 iPhone Safari 验收。WebKit 移动模拟不等于真机。尚未部署上线。构建保留原有 Zod 注释与混合静态/动态导入告警，无构建失败。
