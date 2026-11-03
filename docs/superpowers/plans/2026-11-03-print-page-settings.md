# 打印页码设置 Implementation Plan

**Goal:** 落实已确认的单页强制单面和跳过页勾选规则。

**Architecture:** 纯函数集中校正草稿分段；复用现有弹窗展示页面勾选；后端在展开页码后校正输出参数，使 PDF 和报价一致。

**Tech Stack:** React、TypeScript、Vitest、Playwright、FastAPI、PostgreSQL。

## 执行清单

- [x] 为单页校正、计价、已分配页归属补 Vitest 回归用例；先运行确认缺少实现。浏览器用例另确认原逻辑在页码改为 1 后仍选择双面。
- [x] 在 `src/lib/print-settings.ts` 实现校正与页面归属，在 `src/lib/store.tsx` 统一应用，覆盖上传与恢复草稿。
- [x] 在 `src/customer/settings.tsx` 禁用单页双面，替换跳过输入；在独立选择器中复用 Modal，提供确认/取消、全部未分配页选择，长列表分页。
- [x] 补桌面/移动端 Playwright 用例：单双面切换、跳过选择、打印页不可勾选、取消保留设置、未分配阻止预览。
- [x] 在 `backend/tests/test_media.py` 补真实 PDF/报价回归；确认原实现生成 DUPLEX 产物导致断言失败，再在 `backend/app/media.py` 校正单页分段。
- [x] 更新帮助文案；运行全量前端单测、构建、目标浏览器测试、后端媒体测试和变更审查。

## 验证结果

- `npm test`：60 个文件、351 项通过。
- `npm run build`：通过；保留已有依赖注释及混合导入警告。
- `npx playwright test --config playwright.settings.config.ts --project preview-desktop --project preview-mobile`：8 项通过，包括单页上传、跳过确认/取消、65 页分页、多分段归属和最终 PDF 预览。
- 在 `backend` 执行 `.venv/bin/pytest tests/test_media.py -q --tb=short`：35 项通过，使用隔离 PostgreSQL schema 和真实 PDF 字节；保留现有依赖弃用警告。
- 界面机械检查无发现；桌面与移动截图已检查。
- 独立只读代码审查未发现问题；`git diff --check` 通过。
