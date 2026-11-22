import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Clock3, MessageCircle, Home } from "lucide-react";
import { Button, Notice } from "../components/ui";
import { useStore } from "../lib/store";
import { growthRequest } from "../growth/client";
import { useIdentity } from "../growth/identity";
import {
  guestAccessRemaining,
  guestAccessStatus,
  ownsAccountOrder,
  type CustomerGuestAccess,
} from "./guest-order-policy";
import "./guest-order-access.css";

export function GuestOrderUnavailable({
  status,
}: {
  status: "EXPIRED" | "CLOSED" | "NO_LINK";
}) {
  const expired = status === "EXPIRED";
  return (
    <main className="guest-unavailable">
      <section className="guest-status-card">
        <span className="guest-status-icon">
          <Clock3 size={26} strokeWidth={1.7} />
        </span>
        <h1>{expired ? "游客订单链接已到期" : "游客订单链接已停止访问"}</h1>
        <p>
          {expired
            ? "这份订单的游客访问期限已结束。"
            : "这份订单的游客访问入口已关闭。"}
          <br />
          <span>请联系管理员获取新的订单链接。</span>
        </p>
        <div className="guest-status-actions">
          <Link className="btn" to="/u/help#contact-admin">
            <MessageCircle size={17} />
            联系管理员
          </Link>
          <Link className="btn secondary" to="/u/">
            <Home size={17} />
            返回首页
          </Link>
        </div>
      </section>
    </main>
  );
}

export function GuestOrderBoundary({
  navigation,
  ...props
}: {
  orderId: string;
  children: ReactNode;
  navigation?: ReactNode;
}) {
  return (
    <>
      {navigation && (
        <div className="content-page order-page-navigation">{navigation}</div>
      )}
      <GuestOrderAccessBoundary {...props} />
    </>
  );
}

function GuestOrderAccessBoundary({
  orderId,
  children,
}: {
  orderId: string;
  children: ReactNode;
}) {
  const store = useStore();
  const identity = useIdentity();
  const order = store.data.orders.find((item) => item.id === orderId);
  const owned =
    !!order &&
    (order.accessKind === "OWNER" ||
      ownsAccountOrder(order, identity.customer));
  const guest =
    !order ||
    order.accessKind === "GUEST_OWNER" ||
    order.accessKind === "ORDER_GRANT" ||
    identity.orderAccess?.orderId === orderId;
  const required = !owned && guest;
  const [sample, setSample] = useState<{
    value: CustomerGuestAccess;
    sampledAt: number;
    sampledWallTime: number;
    orderId: string;
    generation: number;
  } | null>(null);
  const [, setTick] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [claimError, setClaimError] = useState("");
  const [retry, setRetry] = useState(0);
  const stopped = useRef("");
  const current =
    sample?.orderId === orderId && sample.generation === identity.generation
      ? sample
      : null;
  const elapsed = current
    ? Math.max(
        0,
        performance.now() - current.sampledAt,
        Date.now() - current.sampledWallTime,
      )
    : 0;
  const status = current ? guestAccessStatus(current.value, elapsed) : null;
  const clearGuestCredential = () => {
    store.setSavedAccessLinks((links) => {
      if (!(orderId in links)) return links;
      const next = { ...links };
      delete next[orderId];
      return next;
    });
    if (identity.orderAccess?.orderId === orderId)
      identity.setOrderAccess(null);
  };
  useEffect(() => {
    if (owned) clearGuestCredential();
  }, [
    owned,
    orderId,
    identity.orderAccess?.orderId,
    store.savedAccessLinks[orderId],
  ]);

  useEffect(() => {
    if (!required || !orderId) return;
    let disposed = false;
    let active: AbortController | null = null;
    const verify = async () => {
      active?.abort();
      const controller = new AbortController();
      active = controller;
      try {
        const value = await growthRequest<CustomerGuestAccess>(
          `/customer/orders/${encodeURIComponent(orderId)}/guest-access`,
          { signal: controller.signal },
        );
        if (disposed || controller.signal.aborted) return;
        if (
          !["ACTIVE", "EXPIRED", "CLOSED", "NO_LINK"].includes(value.status) ||
          !value.serverNow ||
          !Number.isFinite(Date.parse(value.serverNow)) ||
          (value.status === "ACTIVE" &&
            (!value.expiresAt || !Number.isFinite(Date.parse(value.expiresAt))))
        )
          throw new Error("订单访问状态暂时无法核验，请重试。");
        setSample({
          value,
          sampledAt: performance.now(),
          sampledWallTime: Date.now(),
          orderId,
          generation: identity.generation,
        });
        setError("");
      } catch (failure) {
        if (!disposed && !controller.signal.aborted)
          setError(
            (failure as Error).message || "订单访问状态暂时无法核验，请重试。",
          );
      }
    };
    void verify();
    const resume = () => {
      if (document.visibilityState === "visible") void verify();
    };
    const timer = window.setInterval(() => {
      setTick((value) => value + 1);
      if (document.visibilityState === "visible" && navigator.onLine)
        void verify();
    }, 30000);
    const clock = window.setInterval(() => setTick((value) => value + 1), 1000);
    window.addEventListener("online", resume);
    document.addEventListener("visibilitychange", resume);
    return () => {
      disposed = true;
      active?.abort();
      clearInterval(timer);
      clearInterval(clock);
      window.removeEventListener("online", resume);
      document.removeEventListener("visibilitychange", resume);
    };
  }, [required, orderId, identity.generation, retry]);
  useEffect(() => {
    if (!required || !status || status === "ACTIVE") return;
    const key = `${orderId}:${status}:${identity.generation}`;
    if (stopped.current === key) return;
    stopped.current = key;
    window.dispatchEvent(
      new CustomEvent("dormprint:order-access-ended", { detail: { orderId } }),
    );
    if (identity.orderAccess?.orderId === orderId)
      identity.setOrderAccess(null);
  }, [required, status, orderId, identity.generation]);
  if (!required) return <>{children}</>;
  if (status && status !== "ACTIVE")
    return <GuestOrderUnavailable status={status} />;
  if (!current || error)
    return (
      <main className="content-page">
        <Notice type={error ? "error" : undefined}>
          {error || "正在核验订单访问期限…"}
          {error && (
            <Button
              variant="secondary"
              onClick={() => setRetry((value) => value + 1)}
            >
              重试
            </Button>
          )}
        </Notice>
      </main>
    );
  const returnTo = `/u/orders/${encodeURIComponent(orderId)}`;
  const claim = async () => {
    if (busy) return;
    setBusy(true);
    setClaimError("");
    try {
      await growthRequest(
        `/customer/orders/${encodeURIComponent(orderId)}/claim`,
        { method: "POST", body: { confirm: true } },
      );
      clearGuestCredential();
      identity.invalidate();
      await identity.refreshCustomer();
      await store.refresh();
      store.toast("已关联到我的订单，可长期查看订单记录");
    } catch (failure) {
      setClaimError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <div className="content-page guest-access-section">
        <section
          className="guest-access-banner"
          role="status"
          aria-label="游客订单访问期限"
        >
          <div className="guest-access-summary">
            <strong>游客订单链接默认有效 14 天</strong>
            <p>{guestAccessRemaining(current.value, elapsed)}</p>
          </div>
          <p>
            关联已验证的账号后，可在「我的订单」长期查看订单记录。
            {!identity.customer.account?.verified && (
              <>
                {" "}
                <Link
                  className="guest-account-link"
                  to={`/u/account/login?${new URLSearchParams({ returnTo })}`}
                >
                  注册或登录，保存到我的订单
                </Link>
              </>
            )}
          </p>
          {claimError && <Notice type="error" reveal>{claimError}</Notice>}
          {identity.customer.account?.verified ? (
            current.value.canClaimOnDevice ? (
              <Button busy={busy} onClick={() => void claim()}>
                确认关联到我的订单
              </Button>
            ) : (
              <p>
                请联系管理员核验订单与账号后关联。
                <Link to="/u/help#contact-admin">联系管理员</Link>
              </p>
            )
          ) : null}
        </section>
      </div>
      {children}
    </>
  );
}
