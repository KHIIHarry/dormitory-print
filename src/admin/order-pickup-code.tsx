import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ADMIN } from "../lib/store";
import type { AdminOrderPickup } from "../lib/types";
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
  const icon = pickup.placedWithAccount && (
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
    <div className="order-pickup-code">
      {pickup.placedWithAccount && pickup.customerDetailId ? (
        <Link
          className="order-pickup-origin"
          to={`${ADMIN}/customers/${encodeURIComponent(pickup.customerDetailId)}`}
          aria-label={`查看 ${pickup.pickupCode} 的客户详情`}
          title="登录下单 · 查看客户详情"
          onClick={(event) => event.stopPropagation()}
        >
          {icon}
        </Link>
      ) : (
        <span
          className="order-pickup-origin"
          role={pickup.placedWithAccount ? "img" : undefined}
          aria-label={pickup.placedWithAccount ? "登录下单" : undefined}
          aria-hidden={pickup.placedWithAccount ? undefined : true}
        >
          {icon}
        </span>
      )}
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
