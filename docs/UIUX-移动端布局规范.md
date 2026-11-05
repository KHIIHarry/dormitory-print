# UI/UX 移动端布局与原生表单规范

适用：用户端、管理端、配送端的新增页面与布局修复。沿用 [DESIGN.md](../DESIGN.md) 的视觉系统。本规范与浏览器回归是前端交付条件，不以截图“看起来没问题”代替功能验证。

## 1. 本次问题与修复原则

2026-11-05 的 iPhone 截图显示：收支页的月份筛选撑宽工具栏、页面可横向滑动；工资页的月份控件越过卡片右内边距。项目原有原生日期样式覆盖 date/time/datetime-local，遗漏了 month；收支工具栏又依赖 Flex 的临时换行。

修复采用共享原生控件约束，加上手机端明确的工具栏分行，不靠隐藏溢出遮住问题。个人工资、工资管理和人工工资弹窗同步检查。

**验证边界：** 桌面 Chromium 的 iPhone 触控模拟不能代表 iOS Safari。桌面 WebKit 的移动模拟也不包含完整 UIKit 原生控件行为。本轮保留用户的真机截图作为问题证据，增加 Chromium/WebKit 双引擎测试及低优先级原生最小宽度压力测试；不得将这些测试描述成“已在真实 iPhone 验收”。

## 2. 容器必须允许收缩

- 页面、卡片、Flex/Grid 子项按内容设置 `min-width: 0`；Grid 弹性列用 `minmax(0, 1fr)`。
- 自适应表单列需允许在窄容器中小于桌面基准，例如 `repeat(auto-fit, minmax(min(100%, 180px), 1fr))`。
- 子控件按父内容区计算宽度。卡片有左右 padding 时，控件不得超过扣除 padding 后的边界。
- 带内边距的内容区不要使用 `100vw`；不要用固定最小宽度维持桌面布局。
- 禁止在 html/body/应用根节点上添加 `overflow-x: hidden/clip` 掩盖问题。只有明确设计为横向滚动的表格、预览区可以局部滚动。

## 3. 原生日期/月/时间控件统一处理

使用现有 `Field`，保留真正的 input 类型、label 关联、受控 value、disabled 与校验行为。不要为规避宽度问题改成不可访问的伪输入框。

共享规则位于 `src/styles.css`，必须同时覆盖 `date`、`month`、`time`、`datetime-local`：

```css
.field input[type="date"],
.field input[type="month"],
.field input[type="time"],
.field input[type="datetime-local"] {
  display: block;
  box-sizing: border-box;
  width: 100%;
  min-width: 0;
  max-width: 100%;
  inline-size: 100%;
  min-inline-size: 0;
  max-inline-size: 100%;
  -webkit-appearance: none;
  appearance: none;
}
```

WebKit 的日期文字区域也约束最小宽度与对齐，不能只限制外层 Field。`appearance: none` 仅清除外观尺寸影响，不应取消原生选择功能。

手机端可编辑控件字号至少 16px，点击区域至少 44px，防止 iOS 聚焦放大和难以点击。不要通过禁止页面缩放规避问题。可见标签、焦点边框和错误提示必须保留。

WebKit 原生单行 select 可能忽略单独的 `min-height`（本次真实流程曾测得22px）；共享样式须同时提供明确的 `height: 44px`。该规则只作用于没有 `multiple`/`size` 的单行下拉框，不能把多选列表强制压成一行。

## 4. 工具栏与卡片排版

- 手机端先按任务分组：切换/主要操作一行；需要完整日期文字的月份筛选单独占一行。
- 操作组可以在极窄空间内继续换行，但不可挤压月份框，也不能打乱阅读和键盘操作顺序。
- 月份/累计切换后，不保留无意义的空列或固定占位宽度。
- 手机端优先完整展示金额。长金额或负金额放不下时改为单列，不能拆成多行数字，也不能缩小到难以阅读。
- 中文使用系统字体、正常字距；不要给普通中文标签使用品牌标题的 letter-spacing。金额用 tabular-nums。
- 长备注允许自然换行；长网址/连续英文可 `overflow-wrap: anywhere`。金额不得用这一规则任意断字。
- 处理空、加载、失败、禁用与刷新状态。失败保留输入；提交中阻止重复点击。底部按钮不得被固定导航或安全区遮住。

## 5. 必须执行的浏览器验收

### 自动回归

首次安装：

```sh
npm run test:e2e:install
npm run test:e2e:install-webkit
```

布局改动后：

```sh
npm run test:e2e:mobile-ui
npm test
npm run build
```

`playwright.mobile-ui.config.ts` 同时运行 Chromium 与 WebKit。`tests/e2e/native-month.spec.ts` 挂载真实页面组件，覆盖收支页、个人工资、工资管理及人工条目弹窗；受控数据只替代接口，不替代原生控件或实际 CSS。

宽度至少检查 320、375、390、430、768、1440px，并测试中文月份、切换月份/累计、空记录、极窄容器。涉及收支流程时还应运行隔离数据库的 `backend/tests/test_finance_browser.py`，它使用真实 API，并分别运行 Chromium/WebKit。

### 不能省略的断言

- 页面 `scrollWidth <= innerWidth + 1`，且不通过根节点隐藏溢出实现。
- 页面未发生水平偏移；控件左右边缘均处于 Field 和父卡片**内容区**之内。
- 手机控件字号 ≥16px，高度 ≥44px；聚焦/选择值后仍不溢出。
- 月份能够正常修改，禁用态有效；弹窗中的同类控件也受约束。
- 大金额不拆行，长备注不撑宽，卡片内边距在两侧保持一致。

### 视觉与真机检查

每次成批查看桌面、手机截图，集中修复后统一复核，不通过反复缩小字号救布局。Safari 相关问题至少跑 WebKit；具备真机条件时，再检查 iPhone Safari 的实际原生选择器、聚焦缩放、横竖屏和软键盘。缺少真机条件必须在交付记录说明，不得把 Chromium 触控模拟当作 Safari 验收。

## 6. 类似问题的排查顺序

1. 记录浏览器、系统、CSS 视口宽度、问题控件及是否聚焦，不按截图像素直接猜 CSS 宽度。
2. 测量首个越界元素：对比它、Field、卡片内容区和页面的 bounds/scrollWidth。
3. 检查原生控件外观、默认最小宽度、父级 Flex/Grid 自动最小尺寸、固定宽度和 padding/box-sizing。
4. 先补共享规则，再调整局部任务布局；同类输入在其他页面一并回归。
5. 先让回归测试失败，再修复并通过双引擎测试，最后更新本文与截图证据。
