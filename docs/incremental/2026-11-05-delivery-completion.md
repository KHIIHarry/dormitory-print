# 交付动态与送达邮件修复

## 已确认的问题

截图中的用户端和管理端都显示已送达，用户端也能读取交付照片。两端的动态却都停在收款确认：原配送代码写入了配送执行、交付凭证和订单状态，但没有把这些事实纳入共用动态。

邮件已有送达触发器。正常配置下原测试可以入队；但 SMTP 不可用、模板停用或作业入队异常时，原 `_queue` 会吞掉异常，不保留后续可恢复的通知意图。这是确定的可靠性缺口，不能仅凭截图断定线上该笔订单就是这一原因。

## 代码变更

- `backend/app/commerce.py`：订单详情和独立动态接口共用 `order_history`，从配送执行 `startedAt` 和有效交付凭证的服务端时间补齐配送开始、送达事件。已有历史订单也适用；不修改原业务数据、不用当前状态或浏览时间推测交付时间。动态不携带照片、定位、摆放说明，凭证权限保持独立。
- `backend/app/growth.py`：正式交付使用已有 `DeliveryNotice` 表保留送达通知意图，以凭证 ID 去重，冻结事件时间和邮件变量。入队失败保留错误码，原维护任务恢复尚未获得 `EmailJob` 的通知。已入队邮件沿用原 worker 重试；终态失败/过期沿用管理员显式重试，结果未知禁止直接补发，SMTP 已受理不重复发送。
- `backend/tests/test_delivery_completion.py`：覆盖单单/批量交付、两端与独立动态接口一致、历史缺记录恢复、重复读取去重、仅状态不编造动态、三类入队故障恢复、本地 SMTP 实际接收和结果未知保护。

不需要数据库迁移。部署时 API、业务 worker、维护任务运行端需更新到一致版本；邮件发送和恢复仍依赖业务 worker 与定时维护任务正常运行。

## 线上核查与历史订单边界

这次没有连接生产数据库、上线服务或向真实用户补发邮件。修复前已经丢失通知意图的历史订单不会被自动扫描补发，以免对已发送或结果未知的邮件重复通知。

对截图中的提取码 `YVSPS`，可在生产环境只读核查以下信息，先确认真实订单 ID。不要导出 `protected_payload`、SMTP 密钥或完整收件邮箱。

```sql
SELECT o.id, o.pickup_code, o.status,
       o.checkout_account_id IS NOT NULL AS registered_checkout,
       a.status AS account_status, a.verified_at IS NOT NULL AS verified,
       a.deleted_at IS NOT NULL AS account_deleted
FROM orders o
LEFT JOIN accounts a ON a.id = o.checkout_account_id
WHERE o.pickup_code = 'YVSPS';

SELECT e.business_key, e.status, e.attempts, e.created_at,
       e.available_at, e.expires_at, e.data->>'lastErrorClass' AS last_error
FROM email_jobs e
WHERE e.template_key = 'ORDER_DELIVERED'
  AND EXISTS (
    SELECT 1 FROM orders o WHERE o.pickup_code = 'YVSPS'
      AND e.business_key LIKE 'order-email:ORDER_DELIVERED:' || o.id || ':%'
  )
ORDER BY e.created_at;

SELECT n.kind, n.status, n.last_error, n.email_job_id, n.created_at
FROM delivery_notices n JOIN orders o ON o.id = n.order_id
WHERE o.pickup_code = 'YVSPS'
ORDER BY n.created_at;
```

- 无送达任务：检查下单时是否绑定有效已验证账号，以及当时的 SMTP/模板状态。旧版本可能已丢失入队失败记录，不能从“无任务”直接推出唯一原因。
- `QUEUED` / 重试等待：检查业务 worker、维护任务与作业错误。
- `FAILED` / `EXPIRED`：按任务错误定位，修复后走受控显式重试。
- `UNKNOWN`：先核查服务商日志，不能直接重发。
- `SMTP_ACCEPTED`：只证明 SMTP 服务受理，需进一步查服务商投递记录、退信与收件端过滤。

## 验证

先运行新增测试观察到动态只有收款记录、故障通知意图为空的预期失败，再实施修复。

从 `backend` 运行 PostgreSQL 隔离 schema 测试（SMTP 为本机测试服务）：

```sh
.venv/bin/pytest tests/test_delivery_completion.py tests/test_delivery_notice_recovery.py tests/test_batch_delivery.py tests/test_delivery.py tests/test_commerce.py tests/test_mail.py -q --tb=short
```

最终完整回归：**130 passed，65.21 秒**。包括结果未知禁止补发、过期且确认未发送任务的显式重试。存在 6 条原有 Starlette/httpx 与 PyMuPDF 依赖弃用警告，不是测试失败。`git diff --check` 通过。未进行生产 SMTP 投递验收。
