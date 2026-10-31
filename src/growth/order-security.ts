import type { CustomerSession, OrderAccess } from "./types";
export function canOrderAction(
  order: { id: string; status: string; allowedActions: string[] },
  action: string,
  access: OrderAccess | null,
) {
  if (!order.allowedActions?.includes(action)) return false;
  if (action === "ORDER_REVISE_PENDING" || action === "ORDER_CANCEL_PENDING") {
    if (order.status !== "PENDING_CONFIRMATION") return false;
  }
  if (
    action === "ORDER_CHANGE_LOCATION_PRE_DELIVERY" &&
    ["DELIVERING", "DELIVERED", "DELIVERY_EXCEPTION", "CANCELLED"].includes(
      order.status,
    )
  )
    return false;
  if (
    access &&
    (access.orderId !== order.id ||
      (access.expiresAt && Date.parse(access.expiresAt) <= Date.now()) ||
      !access.scopes.includes(action) ||
      !access.allowedActions.includes(action))
  )
    return false;
  return true;
}
export function orderAccessUrl(origin: string, orderId: string, token: string) {
  if (!token || token.length < 32)
    throw new Error("服务未返回有效的高熵订单凭据");
  const url = new URL("/u/order-link", origin);
  url.searchParams.set("orderId", orderId);
  url.hash = new URLSearchParams({ token }).toString();
  return url.toString();
}

export function effectiveOrderAccess(
  order: { id: string; ownerAccountId?: string; accessKind?: string },
  customer: CustomerSession,
  access: OrderAccess | null,
) {
  if (
    order.accessKind === "OWNER" ||
    order.accessKind === "GUEST_OWNER" ||
    order.accessKind === "IMPORTED_GRANT" ||
    (customer.authKind === "REGISTERED" &&
      !!order.ownerAccountId &&
      order.ownerAccountId === customer.account?.id)
  )
    return null;
  return access?.orderId === order.id ? access : null;
}
