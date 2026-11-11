import { beforeEach, expect, it } from "vitest";
import { settlementInput, settlementWithReceipt } from "../src/growth/pricing";
import {
  demoRequest,
  getDemoGrowth,
  syncDemoOrders,
  validateDemoOrderAction,
  visibleDemoOrders,
} from "../src/growth/demo";
import type { Order } from "../src/lib/types";

beforeEach(() => {
  getDemoGrowth().orders.clear();
});

it("keeps payment notes private in demo owner and shared-link projections", async () => {
  const db = getDemoGrowth();
  const order = {
    id: "private-note",
    ownerPrincipalId: db.guestPrincipalId,
    files: [],
    settlement: {
      settlementKind: "CASH_MANUAL",
      cashReceivedCents: 980,
      discountCents: 0,
      confirmedAt: "2026-11-05T01:00:00Z",
      confirmedBy: "admin",
      note: "内部备注",
    },
  } as unknown as Order;
  db.orders.set(order.id, order);
  const owner = visibleDemoOrders("customer", db.customer, null)[0];
  expect(owner.settlement?.note).toBeUndefined();
  syncDemoOrders([owner]);
  expect(db.orders.get(order.id)?.settlement?.note).toBe("内部备注");
  const link = (await demoRequest(`/admin/orders/${order.id}/access-links`, {
    method: "POST",
    body: { reason: "测试" },
  })) as any;
  const ownerExchange = (await demoRequest("/order-access/exchange", {
    method: "POST",
    body: { orderId: order.id, accessToken: link.accessToken },
  })) as any;
  expect(ownerExchange.order.settlement.note).toBeUndefined();
  db.orders.get(order.id)!.ownerPrincipalId = "someone-else";
  const grantExchange = (await demoRequest("/order-access/exchange", {
    method: "POST",
    body: { orderId: order.id, accessToken: link.accessToken },
  })) as any;
  expect(grantExchange.order.settlement.note).toBeUndefined();
  expect(
    visibleDemoOrders("customer", db.customer, db.access)[0].settlement?.note,
  ).toBeUndefined();
  expect(
    visibleDemoOrders("admin", db.customer, null)[0].settlement?.note,
  ).toBe("内部备注");
});

it.each(["9.80", "10.50"])(
  "settles actual cash %s only after accepting the difference",
  (cash) => {
    expect(() => settlementInput(1000, cash, false)).toThrow();
    expect(settlementInput(1000, cash, false, true)).toEqual({
      receivedCents: Math.round(Number(cash) * 100),
      settlementKind: "CASH_MANUAL",
      paymentDifferenceAccepted: true,
    });
  },
);

it("requires explicit approval to waive a positive payable without recording cash", () => {
  expect(() => settlementInput(1685, "0", false)).toThrow("免单");
  expect(settlementInput(1685, "0.00", false, true)).toEqual({
    receivedCents: 0,
    settlementKind: "WAIVED_MANUAL",
    paymentDifferenceAccepted: true,
  });
});

it("preserves normal and zero settlements without declaring a difference", () => {
  expect(settlementInput(1000, "10", false, true)).toEqual({
    receivedCents: 1000,
    settlementKind: "CASH_MANUAL",
  });
  expect(settlementInput(0, "0", true)).toEqual({
    receivedCents: 0,
    settlementKind: "ZERO_PAYABLE",
  });
  expect(() => settlementInput(0, "0", false, true)).toThrow();
  expect(() => settlementInput(0, "1", true, true)).toThrow();
});

it.each([
  "",
  " ",
  "-1",
  "1.001",
  "NaN",
  "Infinity",
  "21474836.48",
  "99999999999999999999",
])("rejects invalid/out of range cash %s", (cash) => {
  expect(() => settlementInput(1000, cash, false, true)).toThrow();
});

it("matches a receipt to actual cash independently of the immutable payable", () => {
  const receipts = [
    {
      id: "receipt",
      amountCents: 1100,
      refundedCents: 50,
      status: "UNMATCHED",
    },
  ];
  expect(
    settlementWithReceipt(
      1000,
      "10.50",
      false,
      receipts,
      "receipt",
      false,
      true,
    ),
  ).toMatchObject({
    receivedCents: 1050,
    receiptId: "receipt",
    paymentDifferenceAccepted: true,
  });
  expect(() =>
    settlementWithReceipt(
      1000,
      "10.40",
      false,
      receipts,
      "receipt",
      false,
      true,
    ),
  ).toThrow("净额");
});

it("enforces the same acknowledgement and zero rules in demo actions", () => {
  getDemoGrowth().orders.set("variance", {
    id: "variance",
    status: "PENDING_CONFIRMATION",
    version: 1,
    contentRevision: 1,
    currentQuoteId: "q",
    totalCents: 1000,
  } as Order);
  const body = {
    expectedVersion: 1,
    contentRevision: 1,
    quoteId: "q",
    receivedCents: 980,
    settlementKind: "CASH_MANUAL",
  };
  const path = "/admin/orders/variance/confirm-payment";
  expect(() => validateDemoOrderAction(path, body)).toThrow();
  expect(() =>
    validateDemoOrderAction(path, { ...body, paymentDifferenceAccepted: true }),
  ).not.toThrow();
  expect(() =>
    validateDemoOrderAction(path, {
      ...body,
      receivedCents: 0,
      settlementKind: "ZERO_PAYABLE",
      paymentDifferenceAccepted: true,
    }),
  ).toThrow();
  const waiver = { ...body, receivedCents: 0, settlementKind: "WAIVED_MANUAL" };
  expect(() => validateDemoOrderAction(path, waiver)).toThrow();
  expect(() =>
    validateDemoOrderAction(path, { ...waiver, paymentDifferenceAccepted: true }),
  ).not.toThrow();
});
