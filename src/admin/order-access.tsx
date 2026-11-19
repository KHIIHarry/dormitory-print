import {
  SearchSuggestions,
  useSearchTerm,
} from "../components/search-suggestions";
import { useEffect, useRef, useState } from "react";
import { ExternalLink } from "lucide-react";
import { Button, Field, Modal, Notice, Panel } from "../components/ui";
import { useIdentity } from "../growth/identity";
import { growthRequest, useGrowthResource } from "../growth/client";
import { orderAccessUrl } from "../growth/order-security";
import type { CustomerRecord } from "../growth/types";
import { ApiError } from "../lib/api";
import { formatDisplayTime } from "../lib/display-format";
import { useStore } from "../lib/store";
import type { Order } from "../lib/types";
import {
  guestAccessActionPayload,
  type GuestAccessView,
} from "../v1/admin-api";
import "./order-access.css";

const labels = {
  ACTIVE: "开放中",
  EXPIRED: "已到期",
  CLOSED: "已关闭",
  NO_LINK: "尚无链接",
};
function remaining(view: GuestAccessView, elapsed: number) {
  const milliseconds =
    Date.parse(view.expiresAt || "") -
    Date.parse(view.serverNow) -
    Math.max(0, elapsed);
  if (!Number.isFinite(milliseconds) || milliseconds <= 0) return "已到期";
  const hours = Math.floor(milliseconds / 3_600_000);
  if (hours === 0) return "不足 1 小时";
  return `${Math.floor(hours / 24)} 天 ${hours % 24} 小时`;
}

export function AdminOrderAccess({ order }: { order: Order }) {
  const { staff, generation } = useIdentity();
  const { refresh } = useStore();
  const capabilities = staff?.capabilities || [];
  const unrestricted = !!staff && !staff.mustChangePassword;
  const canManage =
    unrestricted &&
    (capabilities.includes("*") ||
      capabilities.includes("order.access-link.manage"));
  const canCustomer =
    unrestricted &&
    (capabilities.includes("*") || capabilities.includes("customer.manage"));
  const detail = useGrowthResource<Order>(
    `/admin/orders/${encodeURIComponent(order.id)}`,
  );
  const latest = detail.data?.id === order.id ? detail.data : null;
  const current = order.accountOwned ? order : (latest ?? order);
  const owned = current.accountOwned === true;
  const access = useGrowthResource<GuestAccessView>(
    canManage && !owned
      ? `/admin/orders/${encodeURIComponent(order.id)}/guest-access`
      : null,
  );
  const [view, setView] = useState<GuestAccessView | null>(null);
  const [receivedAt, setReceivedAt] = useState(Date.now());
  const [now, setNow] = useState(Date.now());
  const [action, setAction] = useState<"close" | "reset" | "owner" | null>(
    null,
  );
  const [days, setDays] = useState("14");
  const [reason, setReason] = useState("");
  const [accountQuery, setAccountQuery] = useState("");
  const [target, setTarget] = useState<CustomerRecord | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const customerTerm = useSearchTerm(target ? "" : accountQuery);
  const lookup = useGrowthResource<{ items: CustomerRecord[] }>(
    action === "owner" && canManage && canCustomer && customerTerm
      ? `/admin/customers?${new URLSearchParams({ q: customerTerm, verified: "true", status: "ACTIVE", limit: "12" })}`
      : null,
  );
  const candidates = lookup.error
    ? []
    : (lookup.data?.items ?? []).filter(
        (customer) => customer.verified && customer.status === "ACTIVE",
      );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [resetRequired, setResetRequired] = useState(false);
  const running = useRef(false);
  const epoch = useRef(0);
  const uncertainVersion = useRef<number | null>(null);
  const context = useRef({ orderId: order.id, generation });
  context.current = { orderId: order.id, generation };
  useEffect(() => {
    epoch.current++;
    setAction(null);
    setError("");
    setView(null);
    setBusy(false);
    running.current = false;
    uncertainVersion.current = null;
    return () => {
      epoch.current++;
      running.current = false;
    };
  }, [order.id, generation]);
  useEffect(() => {
    if (access.data) {
      setView(access.data);
      setReceivedAt(Date.now());
      setNow(Date.now());
    }
  }, [access.data]);
  useEffect(() => {
    if (access.failure?.code === "ORDER_ALREADY_OWNED") void detail.reload();
  }, [access.failure?.code]);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);
  const reload = async () => {
    await detail.reload();
    await access.reload();
    void refresh();
  };
  const begin = (next: "close" | "reset" | "owner") => {
    uncertainVersion.current = null;
    setAction(next);
    setReason("");
    setDays("14");
    setAccountQuery("");
    setTarget(null);
    setConfirmed(false);
    setError("");
  };
  const execute = async (opening = false) => {
    if (running.current || !view) return;
    let body: Record<string, unknown> | undefined;
    try {
      if (!opening) {
        body = guestAccessActionPayload(
          view,
          reason,
          action === "reset" ? days : undefined,
        );
        if (action === "owner") {
          if (!target || !confirmed)
            throw new Error("请查找目标账号，并确认已核实订单和目标账号");
          body = { ...body, accountId: target.id };
        }
      }
    } catch (issue) {
      setError((issue as Error).message);
      return;
    }
    const tab = opening ? window.open("", "_blank") : null;
    if (opening && !tab) {
      setError("浏览器阻止了新标签页，请允许弹出窗口后重试。");
      return;
    }
    if (tab) tab.opener = null;
    let navigated = false;
    const origin = { ...context.current, epoch: epoch.current };
    const active = () =>
      origin.orderId === context.current.orderId &&
      origin.generation === context.current.generation &&
      origin.epoch === epoch.current;
    running.current = true;
    setBusy(true);
    setError("");
    try {
      if (tab) {
        const result = await growthRequest<{
          orderId: string;
          accessToken: string;
        }>(`/admin/orders/${order.id}/guest-access/open`);
        if (!active() || tab.closed) return;
        tab.location.replace(
          orderAccessUrl(
            window.location.origin,
            result.orderId,
            result.accessToken,
          ),
        );
        navigated = true;
      } else {
        // Reconcile the version before retrying an operation with an unknown result.
        const latest = await growthRequest<GuestAccessView>(
          `/admin/orders/${order.id}/guest-access`,
        );
        if (!active()) return;
        setView(latest);
        setReceivedAt(Date.now());
        if (
          uncertainVersion.current !== null &&
          latest.version !== uncertainVersion.current
        ) {
          await reload();
          setError(
            "上次操作后访问状态已更新，请核对最新结果。如需再次操作，请关闭弹窗后重新选择。",
          );
          return;
        }
        if (latest.version !== view.version) {
          await reload();
          setError("访问状态已更新，请核对最新状态后再次确认。");
          return;
        }
        const path =
          action === "owner" ? "guest-owner" : `guest-access/${action}`;
        uncertainVersion.current = view.version;
        const result = await growthRequest<
          GuestAccessView | { orderId: string; ownerAccountId: string }
        >(`/admin/orders/${order.id}/${path}`, { method: "POST", body });
        if (!active()) return;
        if ("status" in result) {
          setView(result);
          setReceivedAt(Date.now());
        }
        uncertainVersion.current = null;
        setAction(null);
        setResetRequired(false);
        await reload();
      }
    } catch (issue) {
      if (!active()) return;
      if (
        issue instanceof ApiError &&
        issue.code === "GUEST_LINK_RESET_REQUIRED"
      )
        setResetRequired(true);
      if (issue instanceof ApiError && issue.status === 409) {
        uncertainVersion.current = null;
        await reload();
        setError("访问状态已变化，已刷新最新状态。请核对后再次确认。");
      } else {
        setError(
          (issue as Error).message ||
            "操作结果未确认，请联网后重试。重试时将先核对当前状态。",
        );
        await reload();
      }
    } finally {
      if (!navigated) tab?.close();
      if (active()) {
        running.current = false;
        setBusy(false);
      }
    }
  };
  if (owned)
    return (
      <Panel className="admin-order-access" title="客户信息">
        {canCustomer && current.customerSummary ? (
          <dl className="order-access-facts">
            <div>
              <dt>昵称</dt>
              <dd>{current.customerSummary.displayName || "未填写"}</dd>
            </div>
            <div>
              <dt>注册时间</dt>
              <dd>{formatDisplayTime(current.customerSummary.createdAt)}</dd>
            </div>
            <div>
              <dt>验证状态</dt>
              <dd>{current.customerSummary.verified ? "已验证" : "未验证"}</dd>
            </div>
          </dl>
        ) : canCustomer ? (
          <Notice>
            客户资料暂未返回。
            <Button variant="secondary" onClick={detail.reload}>
              刷新资料
            </Button>
          </Notice>
        ) : (
          <Notice>
            当前账号未获客户管理权限，请联系有权限的管理员查看客户资料。
          </Notice>
        )}
        {detail.error && (
          <Notice type="error">
            {detail.error}
            <Button variant="secondary" onClick={detail.reload}>
              刷新资料
            </Button>
          </Notice>
        )}
      </Panel>
    );
  if (!canManage)
    return (
      <Panel title="订单专属链接">
        <Notice>当前账号未获订单链接管理权限。</Notice>
      </Panel>
    );
  const activeLink =
    view?.status === "ACTIVE" && remaining(view, now - receivedAt) !== "已到期";
  const status =
    view?.status === "ACTIVE" && !activeLink ? "EXPIRED" : view?.status;
  const estimate =
    Number.isInteger(Number(days)) && Number(days) >= 1 && Number(days) <= 14
      ? new Date(
          Date.parse(view?.serverNow || new Date().toISOString()) +
            Math.max(0, now - receivedAt) +
            Number(days) * 86_400_000,
        ).toISOString()
      : null;
  return (
    <Panel
      className="admin-order-access"
      title="订单专属链接"
      action={
        status && (
          <span className={activeLink ? "badge green" : "badge gray"}>
            {labels[status]}
          </span>
        )
      }
    >
      {access.loading && <p role="status">正在读取链接状态…</p>}
      {access.error && (
        <Notice type="error">
          {access.error}
          <Button variant="secondary" onClick={reload}>
            刷新状态
          </Button>
        </Notice>
      )}
      {view && (
        <>
          {view.status !== "NO_LINK" && (
            <dl className="order-access-facts">
              <div>
                <dt>开放时间</dt>
                <dd>{formatDisplayTime(view.openedAt)}</dd>
              </div>
              <div>
                <dt>到期时间</dt>
                <dd>{formatDisplayTime(view.expiresAt)}</dd>
              </div>
              {activeLink && (
                <div>
                  <dt>剩余时间</dt>
                  <dd>{remaining(view, now - receivedAt)}</dd>
                </div>
              )}
            </dl>
          )}
          {(resetRequired || (activeLink && !view.canOpen)) && (
            <Notice>重置后可从后台打开。</Notice>
          )}
          <div className="order-access-actions">
            <Button
              className="order-access-open"
              disabled={busy || !activeLink || !view.canOpen}
              onClick={() => void execute(true)}
            >
              <ExternalLink size={16} strokeWidth={1.7} aria-hidden="true" />
              打开游客订单页
            </Button>
            <Button
              variant="secondary"
              disabled={busy || !activeLink}
              onClick={() => begin("close")}
            >
              关闭链接
            </Button>
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() => begin("reset")}
            >
              重置链接
            </Button>
            {canCustomer && (
              <Button
                variant="secondary"
                disabled={busy}
                onClick={() => begin("owner")}
              >
                关联到客户账号
              </Button>
            )}
          </div>
        </>
      )}
      {error && !action && (
        <Notice type="error">
          <span role="alert">{error}</span>
        </Notice>
      )}
      {action && (
        <Modal
          title={
            action === "close"
              ? "关闭游客订单链接"
              : action === "reset"
                ? "重置游客订单链接"
                : "关联到客户账号"
          }
          onClose={() => {
            if (!busy) setAction(null);
          }}
        >
          <div className="form-stack admin-order-access">
            <Notice type="warning">
              {action === "close"
                ? "确认后，当前链接和已打开的游客会话立即停止访问。"
                : action === "reset"
                  ? "确认后，旧链接和游客会话立即停止访问，新链接从重置成功时开始计时。"
                  : "请先核实订单原始信息和目标账号。确认后，此单进入目标账号的「我的订单」，旧游客入口立即停止访问。"}
            </Notice>
            {action === "reset" && (
              <>
                <Field label="有效天数（1–14 天）">
                  <input
                    type="number"
                    min="1"
                    max="14"
                    step="1"
                    value={days}
                    onChange={(event) => setDays(event.target.value)}
                    disabled={busy}
                  />
                </Field>
                <p className="form-hint">
                  预计到期时间 {formatDisplayTime(estimate)}
                  。以服务端重置成功时间为准。
                </p>
              </>
            )}
            {action === "owner" && (
              <>
                <SearchSuggestions
                  label="目标客户邮箱或账号 ID"
                  placeholder="输入注册邮箱的部分字符"
                  value={accountQuery}
                  onValueChange={(value) => {
                    setAccountQuery(value);
                    setTarget(null);
                    setConfirmed(false);
                  }}
                  disabled={busy}
                  autoCapitalize="none"
                  spellCheck={false}
                  minimum={2}
                  inline
                  pending={lookup.loading}
                  status={
                    customerTerm
                      ? lookup.error || "未找到匹配的已验证账号。"
                      : undefined
                  }
                  options={candidates.map((customer) => ({
                    key: customer.id,
                    value: customer.email,
                    label: customer.displayName || "未命名客户",
                    detail: customer.email,
                    keywords: customer.id,
                  }))}
                  onPick={(option) =>
                    setTarget(
                      candidates.find(
                        (customer) => customer.id === option.key,
                      ) ?? null,
                    )
                  }
                />
                {target && (
                  <div className="order-owner-result" role="status">
                    <strong>
                      {target.displayName || "未命名客户"} · 已验证
                    </strong>
                    <span>{target.email}</span>
                  </div>
                )}
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={confirmed}
                    onChange={(event) => setConfirmed(event.target.checked)}
                    disabled={busy || !target}
                  />
                  已核实订单与目标已验证账号，确认关联此单
                </label>
              </>
            )}
            <Field label="操作原因">
              <textarea
                rows={3}
                maxLength={500}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                disabled={busy}
              />
            </Field>
            {error && (
              <Notice type="error">
                <span role="alert">{error}</span>
              </Notice>
            )}
            <Button
              disabled={
                busy ||
                (action === "owner" &&
                  (!target || !confirmed || !reason.trim()))
              }
              onClick={() => void execute()}
            >
              {busy ? "正在提交…" : "确认操作"}
            </Button>
          </div>
        </Modal>
      )}
    </Panel>
  );
}
