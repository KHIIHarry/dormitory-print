# 管理端增长模块交付记录

新增入口 `src/admin/growth.tsx`，导出：CustomersPage、CustomerDetailPage、MailSettingsPage、MailTemplatesPage、PromotionSettingsPage、PromotionRecordsPage、VolumeDiscountsPage。入口加载 `src/admin/growth.css`；实现位于 `src/admin/growth/`。未修改主路由、共享 store/types/client/pricing、其他既有文件；未提交 commit。

复用 Panel / Field / Button / Modal / Notice / PageHeading / Empty 及现有暖棕设计。数据接口使用 `growthRequest` 和 `useGrowthResource`，成功后刷新；统一离线、加载失败、权限、403/409/429、请求锁、防重复提交及模态内错误显示。

## 对接约定

- `GET /auth/me` 返回 StaffSession，能力检查使用 `customer.manage`、`mail.manage`、`promotion.manage`、`promotion.export`；`*` 也可授权。客户动作另取 CustomerRecord.allowedActions 的 suspend/restore/resend-verification/send-recovery。
- 列表响应 `{items:T[]}`，详情/配置返回对象。CustomerRecord.orders 为有权查看的订单；仅展示 emailMasked，不读取原始邮箱或凭据。
- SMTP PATCH 使用 expectedVersion/reason，空输入不含 secret，清除仅发送 clearSecret:true 并二次确认。NONE 仅 allowInsecure=true 可选且序列化再次验证。测试发送使用 `{expectedVersion,to}`；连接/发送结果支持 status、time/testedAt、message、code、emailJobId，明确不等于投递成功。
- 模板编辑先 GET 对应 key；PATCH 携 expectedVersion/subject/textBody/htmlBody/enabled/reason。预览和测试交给后端安全样例管道；HTML 预览使用空 sandbox iframe 与 CSP，禁止脚本、远程资源、表单。关键模板不可禁用。
- 营销新建无伪造版本，编辑带 expectedVersion。金额元精确转整数分；时段、预算、限额、有效期、适用服务及三类固定金额活动可交互配置。单单单券/不叠加只读。服务端可在 campaign 列表返回 stats，或在 records 列表返回 summary 展示运营统计；前端不推算后台汇总。
- 优惠 freeze/unfreeze/revoke 均传 expectedVersion/reason；历史核销只读。导出 POST 使用白名单 filters/fields/reason，返回 exportJobId；GET 作业返回 status/expiresAt/downloadUrl/error/rowCount。生产下载必须同源 `/api/v1/admin/promotions/exports/{id}/download`；仅 DEMO=true 可额外接受同源 Blob URL。
- 满量 PATCH 为完整规则更新 `{expectedVersion,enabled,effectiveAt,description,tiers,reason}`。不预置营业档位。局部纯逻辑校验正整数/重复门槛/折数最多三位/高门槛不能更差；预览按 A4 小计一次整数四舍五入、同额稳定选档位，只表示当前规则对新报价的影响。配置可选返回 auditLogs 展示操作日志；未返回时明确提示无历史数据。

## 校验

- `npm test -- tests/admin-growth.test.ts`：10 项纯逻辑测试覆盖金额、折数、门槛、整数舍入、禁用与零减免、导出 URL、邮件状态、密钥序列化及无加密传输权限。
- `npx tsc --noEmit`：通过。
- Impeccable 机械检查：新增 UI 文件未发现问题。
- 按分配约束未操作浏览器，未进行页面视觉/真实后端/真实 SMTP/真实投递联调；这些边界不能视为已验证。
