# 售后单接入 24 小时回收站

沿用已批准的 V1 R02/R03 生命周期。售后列表和详情提供删除入口，使用已有
`POST /admin/trash`，`targetType: AFTER_SALE`，不增加平行删除系统。

售后仅在 COMPLETED、CLOSED、REJECTED、CANCELLED、CANCELED、REFUNDED 状态
允许删除。删除设置 `AfterSale.deleted_at` 并增加版本，列表、详情和动作隐藏已删除对象。
24 小时内经原恢复接口清除标记；到期沿用现有清理任务。退款记录独立保留在原订单账目中，
永久清理售后时仅解除退款到售后的可空外键，不改变金额、优惠或工资，不重复生成财务汇总。
备份重放须记录这次外键解除。相关写操作遵守先订单、后售后的加锁顺序。

方案比较：仅加按钮无法隐藏或清理；连退款凭证一起删除会扩大财务影响；选择独立清理售后、
保留退款账目。界面复用现有按钮、原因确认框和错误提示；仅 `data.manage` 管理员可见删除。

## 实施与验证

- [x] 后端先增加失败测试：已退款/已拒绝/已撤回/已完成进回收站、列表详情隐藏、动作拒绝、
      恢复、24h到期自动清理、退款金额与记录保持、处理中阻断、版本和权限校验。
- [x] 增加模型字段和迁移，接通维护生命周期、售后读取与动作守卫、备份外键重放。
      使用隔离 PostgreSQL schema，先红后绿，验证 Alembic upgrade/check/downgrade。
- [x] 前端单独实现售后删除确认组件，接列表和详情；先运行失败的浏览器测试，再实现入口，
      覆盖成功、具体阻断、无删除权限、仍在处理状态；保持既有视觉和布局。
- [x] 运行相关 Python/前端测试与构建，桌面和移动端各一次视觉检查，审查最终变更。

不提交、不部署。保留本聊天上一轮时段修复，最终报告实际通过与剩余失败。

## 最终实现与验收记录

- 前端：`src/v1/after-sale-trash.tsx` 共用原因确认和错误恢复，接入 `src/v1/admin.tsx`
  列表、详情；`src/lib/trash-blockers.ts` 补售后阻断原因。
- 后端：`backend/app/models.py`、`after_sales.py`、`maintenance.py` 完整接入；
  `storage_coordination.py` 与 `deploy/backup/apply_tombstones.py` 记录并重放退款外键解除。
- 迁移：`backend/alembic/versions/d2e4f6a8b010_after_sale_trash.py`，回填原有回收站售后标记；
  退款记录可独立于已清理售后保留。更新服务前需在 backend 运行 `.venv/bin/alembic upgrade head`。
- 售后恢复先锁订单、再锁售后；关联订单也已删除时返回明确冲突，要求先恢复订单。
  订单永久清理使用同一订单锁。PostgreSQL 并发测试验证不再出现成功恢复后马上被清理的竞态。
- 旧数据库备份仍有退款必填售后约束；重放新删除记录前只对这个已知字段应用可空兼容变更，
  避免恢复旧备份时遗漏删除或丢失退款。覆盖真实 PostgreSQL 子进程重放。

实际验证（Python 命令均在 backend 执行）：

1. 初始 `.venv/bin/pytest -q tests/test_trash_after_sales.py --tb=short`：12 失败、6 通过，
   确认缺标记、退款引用阻断、已删除售后仍能访问。
2. `.venv/bin/pytest -q tests/test_trash_after_sales.py tests/test_after_sales.py tests/test_maintenance.py tests/test_migrations.py tests/test_storage_coordination.py tests/test_backup_tools.py tests/test_finance_extensions.py --tb=short`：115 通过。
3. 补旧备份 NOT NULL 回归：先真实失败，修复后
   `.venv/bin/pytest -q tests/test_trash_after_sales.py tests/test_migrations.py tests/test_storage_coordination.py tests/test_backup_tools.py --tb=short`：73 通过。
4. 补零元 ZERO_PAYABLE、清理前重检、订单恢复顺序及并发回归；恢复相关两项先失败，修正锁后
   `.venv/bin/pytest -q tests/test_trash_after_sales.py tests/test_maintenance.py tests/test_storage_coordination.py --tb=short`：90 通过。
5. `.venv/bin/pytest -q tests/test_trash_after_sales.py tests/test_slot_deleted_references.py --tb=short`：39 通过（补并发两项前）。
6. `npx playwright test tests/e2e/after-sale-trash.spec.ts --workers=2`：14 通过，覆盖桌面/手机；
   原页面红灯确认缺少删除按钮。两端最终确认框截图已人工检查。
7. `npm run build`：通过；`npx vitest run tests/trash-blockers.test.ts tests/v1-admin-alignment.test.ts`：20 通过。
8. UI 检测器与 `git diff --check`：通过；最终集成审查无待处理发现。

验收范围内无失败或跳过；现有 FastAPI/httpx 弃用与打包提示不影响通过。未部署，未操作线上数据。
