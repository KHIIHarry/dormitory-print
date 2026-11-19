import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ADMIN } from "../lib/store";
import type { AdminOrderPickup, Order } from "../lib/types";
import "./order-pickup-code.css";

export function adminPickupOf(order: {
  pickupCode?: string | null;
  adminPickup?: AdminOrderPickup | null;
}): AdminOrderPickup {
  return (
    order.adminPickup ?? {
      pickupCode: order.pickupCode || "—",
      placedWithAccount: false,
      customerDetailId: null,
    }
  );
}

function OrderCustomerIcon({
  placedWithAccount,
  customerDetailId,
  label,
}: {
  placedWithAccount: boolean;
  customerDetailId: string | null;
  label: string;
}) {
  const icon = placedWithAccount && (
    <span className="order-pickup-icon">
      <svg
        width="18"
        height="18"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <circle cx="12" cy="8" r="3.5" />
        <path d="M5 21v-1.5a4 4 0 0 1 4-4h6a4 4 0 0 1 4 4V21" />
      </svg>
    </span>
  );
  return (
    <>
      {placedWithAccount && customerDetailId ? (
        <Link
          className="order-pickup-origin"
          to={`${ADMIN}/customers/${encodeURIComponent(customerDetailId)}`}
          aria-label={label}
          title="查看客户详情"
          onClick={(event) => event.stopPropagation()}
        >
          {icon}
        </Link>
      ) : (
        <span
          className="order-pickup-origin"
          role={placedWithAccount ? "img" : undefined}
          aria-label={placedWithAccount ? "账号订单" : undefined}
          aria-hidden={placedWithAccount ? undefined : true}
        >
          {icon}
        </span>
      )}
    </>
  );
}

export function OrderCustomerIdentity({ order }: { order: Order }) {
  const pickup = adminPickupOf(order);
  if (!(order.accountOwned ?? pickup.placedWithAccount))
    return <p className="order-customer-identity">游客订单</p>;

  return (
    <div className="order-customer-identity">
      <OrderCustomerIcon
        placedWithAccount
        customerDetailId={
          order.customerSummary?.accountId ?? pickup.customerDetailId
        }
        label="查看客户详情"
      />
      <span>{order.customerSummary?.email || "账号订单"}</span>
    </div>
  );
}

export function OrderPickupCode({
  pickup,
  href,
  subline,
  trailing,
}: {
  pickup: AdminOrderPickup;
  href: string;
  subline?: ReactNode;
  trailing?: ReactNode;
}) {
  return (
    <div className="order-pickup-code">
      <OrderCustomerIcon
        placedWithAccount={pickup.placedWithAccount}
        customerDetailId={pickup.customerDetailId}
        label={`查看 ${pickup.pickupCode} 的客户详情`}
      />
      <Link
        className="order-pickup-value"
        to={href}
        onClick={(event) => event.stopPropagation()}
      >
        <span className="order-pickup-line">
          <strong>{pickup.pickupCode}</strong>
          {trailing}
        </span>
        {subline && <small>{subline}</small>}
      </Link>
    </div>
  );
}
