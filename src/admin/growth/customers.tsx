import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Button,
  Empty,
  Field,
  PageHeading,
  Panel,
  Notice,
} from "../../components/ui";
import { ADMIN } from "../../lib/api";
import { useGrowthResource } from "../../growth/client";
import type { CustomerRecord } from "../../growth/types";
import {
  AccessState,
  ConfirmAction,
  ResourceState,
  SubmitState,
  mutate,
  useAdminAccess,
  useSubmit,
} from "./common";
import { localDate, yuan } from "./logic";
const statuses: Record<string, string> = {
  PENDING_VERIFICATION: "待验证",
  ACTIVE: "正常",
  SUSPENDED: "已停用",
};
const customerActions: Record<string, string> = {
  suspend: "冻结账号",
  restore: "解冻账号",
  "resend-verification": "重新发送验证提示",
  "send-recovery": "发起安全找回邮件",
};
export function CustomersPage() {
  const access = useAdminAccess("customer.manage");
  const [query, setQuery] = useState("");
  const resource = useGrowthResource<{ items: CustomerRecord[] }>(
    `/admin/customers${query}`,
  );
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const params = new URLSearchParams();
    new FormData(e.currentTarget).forEach((v, k) => {
      if (String(v).trim()) params.set(k, String(v).trim());
    });
    setQuery(params.size ? `?${params}` : "");
  };
  return (
    <div className="admin-growth">
      <PageHeading
        title="客户管理"
        description="按验证状态、业务记录及邀请码查找客户。联系方式仅显示掩码。"
      />
      <AccessState access={access} />
      <Panel>
        <form onSubmit={submit} className="growth-filter">
          <Field label="客户搜索">
            <input name="q" placeholder="客户名称或编号" />
          </Field>
          <Field label="邮箱验证">
            <select name="verified">
              <option value="">全部</option>
              <option value="true">已验证</option>
              <option value="false">未验证</option>
            </select>
          </Field>
          <Field label="账号状态">
            <select name="status">
              <option value="">全部</option>
              {Object.entries(statuses).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </Field>
          <Field label="邀请码">
            <input name="inviteCode" />
          </Field>
          <Field label="注册开始日期">
            <input type="date" name="createdFrom" />
          </Field>
          <Field label="注册结束日期">
            <input type="date" name="createdTo" />
          </Field>
          <Field label="最低订单数">
            <input type="number" name="minOrders" min="0" step="1" />
          </Field>
          <Field label="最低券数">
            <input type="number" name="minCoupons" min="0" step="1" />
          </Field>
          <Button type="submit">筛选客户</Button>
        </form>
      </Panel>
      <ResourceState {...resource} empty={resource.data?.items.length === 0} />
      {!resource.loading &&
        (!resource.error || resource.hasData) &&
        resource.data &&
        resource.data.items.length > 0 && (
          <Panel>
            <div className="growth-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>客户 / 掩码邮箱</th>
                    <th>验证 / 状态</th>
                    <th>订单 / 券 / 邀请</th>
                    <th>邀请码</th>
                    <th>注册时间</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {resource.data.items.map((c) => (
                    <tr key={c.id}>
                      <td>
                        <b>{c.displayName}</b>
                        <small>{c.emailMasked}</small>
                      </td>
                      <td>
                        {c.verified ? "已验证" : "未验证"} ·{" "}
                        {statuses[c.status]}
                      </td>
                      <td>
                        {c.orderCount} / {c.couponCount} / {c.referralCount}
                      </td>
                      <td>{c.inviteCode || "—"}</td>
                      <td>{localDate(c.createdAt)}</td>
                      <td>
                        <Link
                          className="text-link"
                          to={`${ADMIN}/customers/${encodeURIComponent(c.id)}`}
                        >
                          查看详情
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        )}
    </div>
  );
}
export function CustomerDetailPage() {
  const { id } = useParams();
  const access = useAdminAccess("customer.manage");
  const resource = useGrowthResource<CustomerRecord>(
    `/admin/customers/${encodeURIComponent(id || "")}`,
  );
  const submit = useSubmit(),
    [action, setAction] = useState("");
  const customer = resource.data;
  return (
    <div className="admin-growth">
      <PageHeading
        title="客户详情"
        description="账号操作需要权限、明确原因和确认，并保留业务审计。"
        action={
          <Link className="btn secondary" to={`${ADMIN}/customers`}>
            返回客户列表
          </Link>
        }
      />
      <AccessState access={access} />
      <ResourceState {...resource} />
      <SubmitState state={submit} />
      {customer && (!resource.error || resource.hasData) && (
        <>
          <Panel title={customer.displayName}>
            <dl className="growth-facts">
              <div>
                <dt>邮箱</dt>
                <dd>{customer.emailMasked}</dd>
              </div>
              <div>
                <dt>验证状态</dt>
                <dd>{customer.verified ? "已验证" : "未验证"}</dd>
              </div>
              <div>
                <dt>账号状态</dt>
                <dd>{statuses[customer.status]}</dd>
              </div>
              <div>
                <dt>注册时间</dt>
                <dd>{localDate(customer.createdAt)}</dd>
              </div>
              <div>
                <dt>订单 / 优惠券 / 邀请数</dt>
                <dd>
                  {customer.orderCount} / {customer.couponCount} /{" "}
                  {customer.referralCount}
                </dd>
              </div>
              <div>
                <dt>邀请码</dt>
                <dd>{customer.inviteCode || "—"}</dd>
              </div>
            </dl>
            <Notice>
              冻结仅限制新登录、新下单和使用优惠；既有订单继续按业务规则履约。
            </Notice>
            <div className="button-row growth-section">
              {Object.entries(customerActions).map(([key, label]) => (
                <Button
                  key={key}
                  variant="secondary"
                  disabled={
                    !access.allowed ||
                    !customer.allowedActions.includes(key) ||
                    submit.pending ||
                    !submit.online
                  }
                  onClick={() => setAction(key)}
                >
                  {label}
                </Button>
              ))}
            </div>
          </Panel>
          <Panel title="有权查看的订单">
            {customer.orders.length ? (
              <div className="growth-table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>提取码</th>
                      <th>订单状态</th>
                      <th>金额</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {customer.orders.map((o) => (
                      <tr key={o.id}>
                        <td>{o.pickupCode}</td>
                        <td>{o.status}</td>
                        <td>{yuan(o.totalCents)}</td>
                        <td>
                          <Link
                            className="text-link"
                            to={`${ADMIN}/orders/${encodeURIComponent(o.id)}`}
                          >
                            查看订单
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty title="暂无可查看订单" />
            )}
          </Panel>
        </>
      )}
      {action && customer && (
        <ConfirmAction
          title={customerActions[action]}
          pending={submit.pending}
          error={submit.error}
          onClose={() => setAction("")}
          onConfirm={async (reason) => {
            const result = await submit.run(
              () =>
                mutate(
                  `/admin/customers/${encodeURIComponent(customer.id)}/${action}`,
                  { reason, expectedVersion: customer.version },
                ),
              action === "send-recovery" || action === "resend-verification"
                ? "请求已受理；邮件投递结果以发送状态为准"
                : "账号状态已更新",
            );
            if (result.ok) resource.reload();
            return result.ok;
          }}
        >
          将对 {customer.displayName}（{customer.emailMasked}）执行「
          {customerActions[action]}」。邮件操作受服务端限流和发送能力约束。
        </ConfirmAction>
      )}
    </div>
  );
}
