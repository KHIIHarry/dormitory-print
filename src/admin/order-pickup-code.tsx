import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ADMIN } from "../lib/store";
import type { AdminOrderPickup, Order } from "../lib/types";
import {
  OrderOriginIcon,
  OrderOriginIndicator,
} from "../components/order-origin-icon";
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
          <OrderOriginIcon accountOwned={placedWithAccount} />
        </Link>
      ) : (
        <OrderOriginIndicator
          accountOwned={placedWithAccount}
          className="order-pickup-origin"
        />
      )}
    </>
  );
}

export function OrderCustomerIdentity({
  order,
}: {
  order: Pick<
    Order,
    "pickupCode" | "adminPickup" | "accountOwned" | "customerSummary"
  >;
}) {
  const pickup = adminPickupOf(order);
  const accountOwned = order.accountOwned ?? pickup.placedWithAccount;

  return (
    <div className="order-customer-identity">
      <OrderCustomerIcon
        placedWithAccount={accountOwned}
        customerDetailId={
          accountOwned
            ? (order.customerSummary?.accountId ?? pickup.customerDetailId)
            : null
        }
        label="查看客户详情"
      />
      <span>
        {accountOwned ? order.customerSummary?.email || "账号订单" : "游客订单"}
      </span>
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
