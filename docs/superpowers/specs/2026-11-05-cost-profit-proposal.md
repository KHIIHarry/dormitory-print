# 简化耗材与收支核算（用户已批准实施）

2026-11-05：用户批准累计/月份采购支出法，不做逐单墨耗、库存或消耗成本。并要求完成桌面/移动布局、中文与数字间距及交互状态验收。旧差额结算已合并 main 并删除原分支；本次在 main 实施。

## 产品

- 管理端新入口“耗材与收支”，沿用现有暖棕后台视觉。简短中文，中文不拉字距，数字使用等宽数码，触控不小于44px；320/375/390/768/1440宽度不出现页面横向溢出。
- 本月（可切换月份）/累计视图；净实收、耗材支出、耗材后结余、扣工资等支出后的结余，纸张数量和参考均价做辅助信息。数值允许负数，缺少纸张采购时均价显示“—”。
- 采购与支出录入：日期、类别（纸张/墨水/其他）、金额、备注选填；纸张须填张数，切换类别清空不适用张数字段。不可录入未来日期、负数/空金额、超过两位小数或非整数张数。
- 记录分页；类别筛选仅影响列表；切换月份/类别时回到第一页。录入成功刷新汇总和列表，失败保留输入；忙碌时不能重复提交。作废必须原因及版本校验；不直接删除/覆盖历史记录。
- 仅 ADMIN + finance.manage（含 *）可查看和修改；入口与接口权限一致，客户/配送员不能获取金额。

## 口径

- 已确认收款 = 原单 Payment.amount_cents + 补充费用 RECEIPT；退款 = 原单 Refund + 补充费用 REFUND。排除未分配实收，不把应收或零元单算现金收入。软删除订单仍保留财务事实。
- 净实收 = 已确认收款 - 退款；耗材支出 = ACTIVE纸张采购 + ACTIVE墨水采购；耗材后结余 = 净实收 - 耗材支出。
- 最终收支结余 = 耗材后结余 - 已发工资 WageSettlement.amount_cents - ACTIVE其他支出。不再扣工资计提或逐单耗材成本。
- 纸张参考均价 = 纸张采购金额 / 纸张总张数（元，4位小数），仅展示，不重复扣减。
- 月份采用 Asia/Shanghai 的自然月：收入/退款用实际确认记录时间，工资用 paid_at，采购用人工填写的支出日期。不按订单创建时间归月。
- 已永久清理订单：累计读取 AnonymousLedger 原有汇总；本轮起清理前增加不含身份的 cashFlowByDay（按日收款/退款），用于精确归月。旧归档缺少实际收付日期时仅计入累计，月份展示一条简短提示，绝不猜日期。

## API / 模块边界

由 root 负责 backend/app/finance.py、模型迁移、历史归档接入、真实 PostgreSQL测试、契约及路由/权限整合。前端组件单独实现。

- GET /admin/finance/summary?month=YYYY-MM（省略为累计）→ {month, confirmedReceivedCents, refundCents, netReceivedCents, paperPurchaseCents, inkPurchaseCents, otherExpenseCents, consumablesBalanceCents, paidWagesCents, balanceCents, paperSheets, paperUnitYuan:string|null, legacyArchiveCount}。
- GET /admin/finance/expenses?month=&category=&page=1&pageSize=20 → {items,total,page,pageSize}。空参数应省略。
- POST /admin/finance/expenses → {category:'PAPER'|'INK'|'OTHER',spentOn:'YYYY-MM-DD',amountCents,paperSheets?:number|null,note?:string}。
- POST /admin/finance/expenses/{id}/void → {expectedVersion,reason}。
- Expense view: {id,version,category,spentOn,amountCents,paperSheets:number|null,note,status:'ACTIVE'|'VOIDED',voidReason,createdAt}。
- 所有写操作复用现有 Origin/CSRF/幂等事务；金额整数分；不新增依赖、用户或订单系统。

## 验收

真实 PostgreSQL验证权限、金额/日期/张数校验、幂等、作废版本冲突、原单差额/补充费/退款/工资合计、上海跨月边界、软删除/永久归档不丢账。前端真实组件在多宽度下验证表单、筛选分页、空/加载/失败/忙碌状态、长备注、大金额、中文间距、键盘与减少动画。批量截图检查桌面和手机，最多修订一轮后复核。
