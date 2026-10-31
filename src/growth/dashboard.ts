import type { components } from "../lib/api-schema";
import type { Order } from "../lib/types";
export type DashboardSummary = components["schemas"]["DashboardSummary"];
/** Only used by the in-memory development adapter. Production totals are server aggregates. */
export function demoDashboardSummary(
  orders: Order[],
  deliveryExceptionCount = 0,
): DashboardSummary {
  const active = orders.filter((o) => o.status !== "CANCELLED");
  const sum = (pick: (o: Order) => number) =>
    active.reduce((total, o) => total + pick(o), 0);
  return {
    orderCount: orders.length,
    pendingPaymentCount: orders.filter(
      (o) => o.status === "PENDING_CONFIRMATION",
    ).length,
    deliveredCount: orders.filter((o) => o.status === "DELIVERED").length,
    pendingPrintTaskCount: orders
      .flatMap((o) => o.files || [])
      .filter((f) => f.printStatus === "PENDING").length,
    pendingBindingCount: orders
      .flatMap((o) => o.files || [])
      .filter(
        (f) => f.bindingType !== "NONE" && f.bindingStatus !== "COMPLETED",
      ).length,
    pendingPackingCount: orders.filter((o) => o.status === "PENDING_PRINT")
      .length,
    readyForDeliveryCount: orders.filter(
      (o) => o.status === "READY_FOR_DELIVERY",
    ).length,
    deliveryExceptionCount,
    subtotalCents: sum((o) => o.quote?.subtotalCents ?? o.totalCents),
    couponDiscountCents: sum((o) =>
      o.quote?.selectedPromotionType === "COUPON" ? o.quote.discountCents : 0,
    ),
    volumeDiscountCents: sum((o) =>
      o.quote?.selectedPromotionType === "VOLUME_TIER"
        ? o.quote.discountCents
        : 0,
    ),
    staffDiscountCents: sum((o) => o.quote?.selectedPromotionType === "STAFF_GRADE" ? o.quote.discountCents : 0),
    supplementaryFeesCents: 0,
    supplementaryCashReceivedCents: 0,
    supplementaryRefundCents: 0,
    supplementaryOutstandingCents: 0,
    unmatchedReceiptCents: 0,
    unmatchedReceiptRefundCents: 0,
    payableCents: sum((o) => o.totalCents),
    cashReceivedCents: sum((o) =>
      o.settlement?.settlementKind === "CASH_MANUAL"
        ? o.settlement.cashReceivedCents
        : 0,
    ),
    zeroPayableCount: active.filter(
      (o) => o.settlement?.settlementKind === "ZERO_PAYABLE",
    ).length,
    refundCents: 0,
    manualAdjustmentCents: 0,
  };
}
export function assertDashboardSummary(value: DashboardSummary) {
  for (const key of [
    "orderCount",
    "pendingPaymentCount",
    "deliveredCount",
    "pendingPrintTaskCount",
    "pendingBindingCount",
    "pendingPackingCount",
    "readyForDeliveryCount",
    "deliveryExceptionCount",
    "subtotalCents",
    "couponDiscountCents",
    "volumeDiscountCents",
    "payableCents",
    "cashReceivedCents",
    "zeroPayableCount",
    "refundCents",
    "manualAdjustmentCents",
  ] as const) {
    if (
      !Number.isSafeInteger(value?.[key]) ||
      (key !== "manualAdjustmentCents" && value[key] < 0)
    )
      throw new Error("统计响应不兼容，请更新服务端后重新加载");
  }
  return value;
}
