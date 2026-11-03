# 售后单管理员邮件实施计划

**Goal:** 创建售后单时通知有售后处理权限的管理员，并保持现有邮件视觉与编辑体验。

**Architecture:** 复用 `mail.py` 注册及安全渲染、`mail_html.py` 布局组件、`growth.py` 通知入队工具，在 `after_sales.create_case` 成功创建后调用。生成前端 HTML 副本，并同步 demo 元数据与样例。

**Tech Stack:** FastAPI、SQLAlchemy、PostgreSQL、React、TypeScript、pytest、Vitest。

## 约束

邮件只通知有售后处理权限且邮箱已验证的启用 ADMIN。每单每人只入队一次；入队异常不回滚售后；不新增表或发送机制；UI 继承现有设计。

## 实施与验证

- [x] 在 `backend/tests/test_mail.py` 添加模板、权限、去重、停用、异常隔离及 SMTP 内容测试；在 `backend/tests/test_after_sales.py` 添加创建触发和回滚测试；在 `tests/growth-continuation.test.ts` 添加 demo 预览测试。运行新测试确认功能缺失导致失败。
- [x] 在 `backend/app/mail.py` 注册模板和示例变量；在 `backend/app/mail_html.py` 复用 `_shell`、`_multi_facts`、`_note`、`_button`、`_fallback`。
- [x] 在 `backend/app/growth.py` 添加 `notify_admin_after_sale_created(db, order, case)`，使用 `_queue` 和业务去重键；在 `backend/app/after_sales.py` 调用。
- [x] 同步 `src/growth/demo.ts`，运行 `backend/.venv/bin/python scripts/export-mail-templates.py`。
- [x] 运行 `cd backend && .venv/bin/pytest tests/test_mail.py tests/test_after_sales.py`、相关 Vitest、`npm run build`；渲染桌面与手机邮件预览并检查差异和格式。

## 验证结果

- PostgreSQL：`cd backend && .venv/bin/pytest tests/test_mail.py tests/test_after_sales.py -q`，53 项通过。最后调整邮件窄屏标签后，相关 6 项再次通过。
- 前端：`npm test -- tests/growth-continuation.test.ts tests/admin-growth.test.ts`，23 项通过。
- `npm run build` 和 `git diff --check` 通过。
- 本地 SMTP 实际接收新模板邮件，验证 HTML 转义、脱敏与管理入口。
- Chrome 预览 800、375、320 px 宽度均无横向溢出；截图位于 `output/mail-preview/`。
- 机械样式扫描仅提示沿用既有 `_note` 的 3 px 左边线；按用户指定保持与其余邮件一致。依赖现有弃用/构建提示未在本次修改。
