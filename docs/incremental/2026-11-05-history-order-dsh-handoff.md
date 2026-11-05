# DSH 交接：历史订单状态、动态与送达邮件

本轮要求：代码推送 GitHub main，并告知 DSH 如何核查、修复历史问题。此文是交接说明，不代表已经部署、修改生产订单或补发真实邮件。

## 先确认是哪一种问题

截图提取码为 `YVSPS`，用户端与管理端都显示已送达，用户端还有交付照片。已确认的问题是两端共用动态缺配送、送达事件；截图本身不能证明主状态错误。不要为补一条动态把订单回退后重新交付。

更新到本次 main 后，`commerce.order_history` 会从配送执行的 `startedAt` 和有效交付凭证的服务端时间合并动态，管理端详情、用户端详情与独立动态接口共用这一结果。已有历史订单无须补写 JSON history 或改变主状态；新前端同时展示两端凭证。先部署一致版本的前端/API/业务 worker/维护任务运行端，再重新读取详情核对。具体部署由负责人安排，本交接不执行上线。

## 只读证据核查

先用提取码和截图中的创建时间定位真实订单 UUID；以下查询不包含照片、定位内容、手机号、完整邮箱或邮件受保护载荷。

```sql
BEGIN TRANSACTION READ ONLY;

SELECT o.id, o.pickup_code, o.status, o.version, o.content_revision,
       o.created_at, o.deleted_at, o.settlement_kind, o.packing_status,
       o.data->>'deliveryProofId' AS projected_proof_id,
       o.data->>'deliveredAt' AS projected_delivered_at
FROM orders o WHERE o.pickup_code = 'YVSPS';

SELECT a.order_id, a.id AS assignment_id, a.batch_id, a.active,
       a.status AS assignment_status, a.version AS assignment_version,
       a.data->>'startedAt' AS started_at,
       a.data->>'deliveredAt' AS assignment_delivered_at,
       p.id AS proof_id, p.active AS proof_active,
       p.data->>'serverDeliveredAt' AS server_delivered_at,
       p.created_at AS proof_created_at
FROM orders o JOIN delivery_assignments a ON a.order_id = o.id
LEFT JOIN delivery_proofs p ON p.assignment_id = a.id AND p.order_id = o.id
WHERE o.pickup_code = 'YVSPS'
ORDER BY a.created_at, p.created_at;

SELECT s.id, s.order_id, s.status, s.solution, s.created_at
FROM after_sales s JOIN orders o ON o.id = s.order_id
WHERE o.pickup_code = 'YVSPS' ORDER BY s.created_at;

ROLLBACK;
```

补充核查收款/退款、生产/装订/装袋结果、异常、审计、批次、工资与邀请奖励记录。注意售后重做会停用旧配送执行并保留旧送达凭证；旧凭证不能覆盖当前重做中的状态。已删除、已撤销、退款或售后中的订单单列人工核对。

## 按事实修复

| 现有事实 | 处理 |
| --- | --- |
| 订单已送达，当前执行与凭证一致，仅动态缺失 | 使用本次动态投影，刷新/重新查询即可；不改状态、时间、金额，不重走交付接口 |
| 当前有效执行已有真实有效凭证，订单投影仍停在配送中等旧值，且无后续售后/纠错 | 属于待确认的投影不一致；逐单给出凭证、版本、拟修字段清单，核对后以受控事务修正投影，保留原凭证和服务端时间 |
| 状态已送达但没有可对应的真实凭证，或凭证只属于旧执行 | 不能凭状态、短码或截图补造凭证；核查真实交付、审计和当前履约阶段，先列异常及所需证据，再决定回到哪个合法阶段 |
| 旧订单已送达，随后进入商家责任免费重做 | 保留旧送达历史，以当前重做任务、装袋、新配送执行为准，不恢复成旧已送达状态 |
| 批次停在配送中、其执行实际均已结束 | 核对后复用 `delivery.refresh_batch` 的现有投影规则；该函数只处理 DELIVERING 批次，不借此重写已完成/已取消批次 |
| 历史时段、校区、分配关系异常 | 使用现有 `GET /api/v1/admin/delivery-repair/preview` 只读预检。它不是修复 apply 接口；归批/改派仍走原调度守卫，不重复收款或占容量 |

当前不存在一个可以任意批量改订单状态的合法通用接口。确实需要修正错误投影时，DSH 应先交付限定订单 UUID 的只读 preview/修复清单，列明旧值、新值、order/assignment/batch 版本、依据 proof ID、原服务端时间、排除项与原因。若现有受控接口无法处理，先实现并审查针对该类错误的幂等修复工具，而不是直接执行一条批量 UPDATE。

修复执行要求：锁序复用现有配送事务，apply 前重新检查版本、当前分配、有效凭证和后续售后；一单事实有变化则拒绝该单。只修已确认错误的投影字段，调用 `touch` 与 `audit` 记录操作者/原因/前后值；事务失败回滚，重复执行不得产生新业务事实。需要更新 deliveryProofId、deliveredAt 或 delivery 内投影时全部使用已核对凭证/执行的原值。不能以当前时间代替历史交付时间。

不要调用 `_record_delivery_proof` 或重新提交 proof 来“修状态”：它还会写工资、邀请奖励、通知等事实。不要重复登记收款、改历史报价、重复占时段、重复计薪、重发奖励，也不要用 `refresh_production` 强行回退已送达/已关闭订单；其代码刻意冻结这些状态。

## 历史送达邮件

按 `2026-11-05-delivery-completion.md` 的只读查询检查原任务和账号资格。

- 本次修复后的交付会保留 ORDER_DELIVERED 通知意图。未获得 EmailJob 的 PENDING 通知，在配置恢复后由原维护任务恢复，按原凭证去重。
- 已入队任务由原业务 worker 发送/重试；确认未发送的 FAILED/EXPIRED 走 `retry_delivery_notice` 或原管理员重试路径。
- UNKNOWN 先核实服务商日志，不能直接补发；SMTP_ACCEPTED 不自动再发，且不等同进入收件箱。
- 修复前无通知意图、无邮件任务的订单不会自动补发。先核查原收件账号、原发信记录与是否确实未发送；需要补发时使用逐单受控清单，绑定原 proof ID、原交付时间及有效收件人，保存审计和幂等键。不要仅以订单当前 version 调用通知函数扫全库，避免重复发送已有历史通知。
- 游客/未绑定有效已验证下单账号的订单，不从电话、昵称或操作者账号推断收件邮箱。

## 验收和 DSH 返回格式

本轮只要求 DSH 接收说明、只读复核修复路径，不修改源码/生产数据、不部署、不向真实用户发信。

返回：已理解的三类问题（状态投影、动态展示、邮件）；现有代码可直接解决的部分；真实状态错误仍需哪些逐单事实。若后续获准实现修复工具，须先做 PostgreSQL 隔离测试，覆盖售后旧凭证不覆盖新执行、错误版本拒绝、单单事务回滚、重复修复不重复计薪/奖励/邮件，并保存修前修后摘要。不要把 DSH turn 完成当作生产验收。
