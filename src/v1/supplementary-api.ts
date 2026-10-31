import { ApiError, mutation, request } from "../lib/api";

/** Local DTOs mirror backend/app/supplementary_schemas.py (strict camelCase). */
export type SupplementaryMovement = {
  id: string;
  kind: "RECEIPT" | "REFUND" | string;
  amountCents: number;
  reason: string;
  createdAt: string;
  actorId?: string | null;
  payrollStatus?: string | null;
};

export type SupplementaryDispute = {
  id: string;
  version: number;
  principalId?: string | null;
  reason: string;
  status: string;
  resolution: string;
  createdAt: string;
  resolvedAt?: string | null;
  actorId?: string | null;
};

export type SupplementaryCharge = {
  id: string;
  orderId: string;
  purpose: string;
  amountCents: number;
  includeInPayroll: boolean;
  status: "ACTIVE" | "CANCELLED" | string;
  grossReceivedCents: number;
  refundedCents: number;
  netReceivedCents: number;
  outstandingCents: number;
  creditCents: number;
  createdBy?: string | null;
  createdAt: string;
  updatedAt: string;
  version: number;
  movements: SupplementaryMovement[];
  disputes: SupplementaryDispute[];
  data: Record<string, unknown>;
};

export type SupplementarySummary = {
  orderId: string;
  orderVersion: number;
  original: {
    totalCents: number;
    cashReceivedCents: number;
    refundedCents: number;
    settlementKind?: string | null;
    status: string;
  };
  extras: {
    chargeCount: number;
    activeChargeCount: number;
    cancelledChargeCount: number;
    feeTotalCents: number;
    grossReceivedCents: number;
    refundedCents: number;
    netReceivedCents: number;
    outstandingCents: number;
    includeInPayrollFeeTotalCents: number;
  };
  combined: {
    totalCents: number;
    grossReceivedCents: number;
    refundedCents: number;
    netReceivedCents: number;
    outstandingCents: number;
  };
};

export type SupplementaryList = {
  items: SupplementaryCharge[];
  summary: SupplementarySummary;
};

export type CreateChargePayload = {
  purpose: string;
  amountCents: number;
  includeInPayroll: boolean;
  expectedVersion: number;
};

export type MovementPayload = {
  amountCents: number;
  reason: string;
  expectedVersion: number;
};

export type ReasonPayload = {
  reason: string;
  expectedVersion: number;
};

export type DisputePayload = {
  reason: string;
  expectedVersion: number;
};

export type ResolveDisputePayload = {
  resolution: string;
  status: "RESOLVED" | "REJECTED";
  expectedVersion: number;
};

const orderPath = (orderId: string) =>
  `/orders/${encodeURIComponent(orderId)}/supplementary-charges`;
const adminOrderPath = (orderId: string) =>
  `/admin/orders/${encodeURIComponent(orderId)}/supplementary-charges`;
const chargePath = (chargeId: string) =>
  `/admin/supplementary-charges/${encodeURIComponent(chargeId)}`;

export const adminChargesPath = adminOrderPath;
export const customerChargesPath = orderPath;
export const receiptPath = (chargeId: string) =>
  `${chargePath(chargeId)}/receipts`;
export const refundPath = (chargeId: string) =>
  `${chargePath(chargeId)}/refunds`;
export const cancelPath = (chargeId: string) =>
  `${chargePath(chargeId)}/cancel`;
export const customerDisputePath = (orderId: string, chargeId: string) =>
  `${orderPath(orderId)}/${encodeURIComponent(chargeId)}/disputes`;
export const resolveDisputePath = (chargeId: string, disputeId: string) =>
  `${chargePath(chargeId)}/disputes/${encodeURIComponent(disputeId)}/resolve`;

export function listAdminCharges(orderId: string, signal?: AbortSignal) {
  return request<SupplementaryList>(adminOrderPath(orderId), { signal });
}

export function listCustomerCharges(orderId: string, signal?: AbortSignal) {
  return request<SupplementaryList>(orderPath(orderId), { signal });
}

export function createCharge(orderId: string, payload: CreateChargePayload) {
  return mutation<SupplementaryCharge>(adminOrderPath(orderId), payload);
}

export function receiveCharge(chargeId: string, payload: MovementPayload) {
  return mutation<SupplementaryCharge>(receiptPath(chargeId), payload);
}

export function refundCharge(chargeId: string, payload: MovementPayload) {
  return mutation<SupplementaryCharge>(refundPath(chargeId), payload);
}

export function cancelCharge(chargeId: string, payload: ReasonPayload) {
  return mutation<SupplementaryCharge>(cancelPath(chargeId), payload);
}

export function disputeCharge(
  orderId: string,
  chargeId: string,
  payload: DisputePayload,
) {
  return mutation<SupplementaryDispute>(
    customerDisputePath(orderId, chargeId),
    payload,
  );
}

export function resolveDispute(
  chargeId: string,
  disputeId: string,
  payload: ResolveDisputePayload,
) {
  return mutation<SupplementaryDispute>(
    resolveDisputePath(chargeId, disputeId),
    payload,
  );
}

/** Parse a yuan input without binary floating point. Returns null if invalid. */
export function yuanToCents(value: string): number | null {
  const text = value.trim();
  if (!/^\d+(?:\.\d{1,2})?$/.test(text)) return null;
  const [yuan, fraction = ""] = text.split(".");
  const cents = Number(yuan) * 100 + Number(fraction.padEnd(2, "0"));
  return Number.isSafeInteger(cents) ? cents : null;
}

export function centsToYuanInput(cents: number): string {
  return (Math.max(0, cents) / 100).toFixed(2);
}

export function chargeDraftError(draft: {
  purpose: string;
  amount: string;
}): string {
  if (!draft.purpose.trim()) return "请填写费用用途，客户将看到该说明";
  const cents = yuanToCents(draft.amount);
  if (cents === null || cents <= 0) return "请输入大于 0 的准确金额";
  return "";
}

export function amountError(
  value: string,
  maximumCents: number,
  label: string,
): string {
  const cents = yuanToCents(value);
  if (cents === null || cents <= 0) return "请输入大于 0 的准确金额";
  if (cents > maximumCents)
    return `${label}不能超过 ${centsToYuanInput(maximumCents)} 元`;
  return "";
}

/** The server refused a fee receipt because it exceeded the remaining amount. */
export function isReceiptOvershoot(error: unknown): error is ApiError {
  return (
    error instanceof ApiError && error.code === "RECEIPT_EXCEEDS_OUTSTANDING"
  );
}

/** Authoritative remaining amount from the server's 422 details, when present. */
export function receiptOvershootRemainingCents(error: ApiError): number | null {
  const details = error.details;
  if (!details || typeof details !== "object") return null;
  const remaining = (details as { remainingCents?: unknown }).remainingCents;
  return typeof remaining === "number" &&
    Number.isSafeInteger(remaining) &&
    remaining >= 0
    ? remaining
    : null;
}

export function reasonError(value: string): string {
  return value.trim() ? "" : "请填写原因，操作记录将保留审计";
}
