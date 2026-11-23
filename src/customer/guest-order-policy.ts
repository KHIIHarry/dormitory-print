import type { CustomerSession } from "../growth/types";

export const originalGuestAccessVersion = 1;

export function guestAccessPath(orderId: string) {
  return `/customer/orders/${encodeURIComponent(orderId)}/guest-access`;
}

export type CustomerGuestAccess = {
  status: "ACTIVE" | "EXPIRED" | "CLOSED" | "NO_LINK";
  version: number;
  expiresAt: string | null;
  serverNow: string;
  canClaimOnDevice: boolean;
};
type ClockSample = Pick<
  CustomerGuestAccess,
  "status" | "expiresAt" | "serverNow"
>;
export function guestAccessStatus(sample: ClockSample, elapsed: number) {
  if (sample.status !== "ACTIVE") return sample.status;
  return !sample.expiresAt ||
    Date.parse(sample.expiresAt) <=
      Date.parse(sample.serverNow) + Math.max(0, elapsed)
    ? "EXPIRED"
    : "ACTIVE";
}
export function guestAccessRemaining(sample: ClockSample, elapsed: number) {
  const hours = Math.max(
    0,
    Math.floor(
      (Date.parse(sample.expiresAt || "") -
        Date.parse(sample.serverNow) -
        Math.max(0, elapsed)) /
        3600000,
    ),
  );
  return hours
    ? `剩余 ${Math.floor(hours / 24)} 天 ${hours % 24} 小时`
    : "不足 1 小时";
}
export function ownsAccountOrder(
  order: { ownerAccountId?: string | null; accessKind?: string },
  customer: CustomerSession,
) {
  return (
    customer.authKind === "REGISTERED" &&
    !!order.ownerAccountId &&
    order.ownerAccountId === customer.account?.id
  );
}
