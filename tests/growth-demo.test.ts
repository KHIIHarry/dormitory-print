import { beforeEach, describe, it, expect } from "vitest";
import {
  demoRequest,
  getDemoGrowth,
  syncDemoOrders,
  defaultVolume,
  validateDemoOrderAction,
  visibleDemoOrders,
} from "../src/growth/demo";
import { buildQuote } from "../src/growth/pricing";
import type { Order } from "../src/lib/types";
const lines = [
  {
    key: "p",
    relatedId: "p",
    unit: "SHEET" as const,
    quantity: 5,
    unitPriceCents: 10,
    amountCents: 50,
    service: "A4" as const,
  },
];
const db = getDemoGrowth();
beforeEach(() => {
  db.deliveryFeeCents = 0;
  db.orders.clear();
  db.preps.clear();
  db.coupons = [];
  db.links = [];
  db.tokens.clear();
  db.volume = structuredClone(defaultVolume);
  db.guestPrincipalId = "guest-test";
  db.customer = { role: "CUSTOMER", authKind: "ANONYMOUS", capabilities: [] };
  db.access = null;
  db.guestVersions.clear();
  db.staff = {
    id: "admin",
    role: "ADMIN",
    name: "管理员",
    username: "admin",
    capabilities: ["*"],
    mustChangePassword: false,
  } as any;
});
describe("开发适配严格遵守报价和授权规则", () => {
  it("delivery config reaches preparation and requote snapshots", async () => {
    db.deliveryFeeCents = 250;
    const prep = (await demoRequest("/preparations", {
      method: "POST",
      body: { lines },
    })) as any;
    expect(prep.quote.totalCents).toBe(300);
    db.deliveryFeeCents = 100;
    const next = (await demoRequest(`/preparations/${prep.id}/requote`, {
      method: "POST",
      body: {},
    })) as any;
    expect(next.totalCents).toBe(150);
    expect(next.deliveryFeeCents).toBe(100);
  });
  it("其他订单预约券不能进入新报价，零元报价可预约", async () => {
    await demoRequest("/__demo/customer", { method: "POST" });
    db.coupons.push({
      id: "c",
      couponGrantId: "cg",
      campaignId: "cp",
      name: "测试",
      amountCents: 50,
      minSpendCents: 0,
      service: "A4",
      status: "RESERVED",
      reservedOrderId: "other",
      riskHold: "NONE",
      validUntil: null,
      version: 1,
    });
    const p = (await demoRequest("/preparations", {
      method: "POST",
      body: { lines, checkoutAccountId: db.account.id },
    })) as any;
    expect(p.quote.discountCents).toBe(0);
    db.coupons[0].status = "AVAILABLE";
    const next = (await demoRequest("/preparations", {
      method: "POST",
      body: { lines, checkoutAccountId: db.account.id },
    })) as any;
    expect(next.quote.totalCents).toBe(0);
    await demoRequest("/__demo/reserve", {
      method: "POST",
      body: { preparationId: next.id, quoteId: next.quote.id, orderId: "o" },
    });
    expect(db.coupons[0].status).toBe("RESERVED");
  });
  it("冻结与陈旧报价阻止预约；不能用旧版本结算", async () => {
    await demoRequest("/__demo/customer", { method: "POST" });
    db.coupons.push({
      id: "c",
      couponGrantId: "cg",
      campaignId: "cp",
      name: "测试",
      amountCents: 50,
      minSpendCents: 0,
      service: "A4",
      status: "AVAILABLE",
      riskHold: "NONE",
      validUntil: null,
      version: 1,
    });
    const p = (await demoRequest("/preparations", {
      method: "POST",
      body: { lines, checkoutAccountId: db.account.id },
    })) as any;
    db.coupons[0].riskHold = "FROZEN";
    await expect(
      demoRequest("/__demo/reserve", {
        method: "POST",
        body: { preparationId: p.id, quoteId: p.quote.id, orderId: "o" },
      }),
    ).rejects.toThrow();
    const o = {
      id: "o",
      version: 2,
      currentQuoteId: "q",
      status: "PENDING_CONFIRMATION",
      totalCents: 0,
      contentRevision: 1,
      blockers: [{ code: "COUPON_FROZEN", message: "冻结" }],
    } as Order;
    db.orders.set("o", o);
    expect(() =>
      validateDemoOrderAction("/admin/orders/o/confirm-payment", {
        quoteId: "q",
        expectedVersion: 2,
        contentRevision: 1,
        receivedCents: 0,
        settlementKind: "ZERO_PAYABLE",
      }),
    ).toThrow();
  });
  it("预约券冻结保留锁价，解冻和撤单分别解除阻断和返券", async () => {
    db.coupons.push({
      id: "c",
      couponGrantId: "cg",
      campaignId: "cp",
      name: "测试",
      amountCents: 50,
      minSpendCents: 0,
      service: "A4",
      status: "RESERVED",
      reservedOrderId: "o",
      riskHold: "NONE",
      validUntil: null,
      version: 1,
    });
    const q = buildQuote(lines, defaultVolume, db.coupons, {}, "account", "o");
    const o = {
      id: "o",
      version: 1,
      currentQuoteId: q.id,
      quote: q,
      status: "PENDING_CONFIRMATION",
      totalCents: q.totalCents,
      contentRevision: 1,
    } as Order;
    db.orders.set("o", o);
    await demoRequest("/admin/promotions/coupons/cg/freeze", {
      method: "POST",
      body: { expectedVersion: 1, reason: "核对" },
    });
    expect(db.orders.get("o")?.totalCents).toBe(0);
    expect(db.orders.get("o")?.blockers?.map((b) => b.code)).toContain(
      "COUPON_FROZEN",
    );
    await demoRequest("/admin/promotions/coupons/cg/unfreeze", {
      method: "POST",
      body: { expectedVersion: 2, reason: "核对完成" },
    });
    expect(db.orders.get("o")?.blockers?.map((b) => b.code)).not.toContain(
      "COUPON_FROZEN",
    );
    syncDemoOrders([{ ...db.orders.get("o")!, status: "CANCELLED" }]);
    expect(db.coupons[0].status).toBe("AVAILABLE");
    expect(db.coupons[0].reservedOrderId).toBeUndefined();
  });
  it("转发链接登录后保持只读，关闭后原会话和旧链接一起失效", async () => {
    const o = {
      id: "o",
      ownerPrincipalId: "someone-else",
      status: "PENDING_CONFIRMATION",
      allowedActions: ["ORDER_CANCEL_PENDING"],
    } as Order;
    db.orders.set("o", o);
    await demoRequest("/admin/orders/o/guest-access/reset", {
      method: "POST",
      body: { expectedVersion: 1, days: 14, reason: "测试" },
    });
    const link = (await demoRequest(
      "/admin/orders/o/guest-access/open",
    )) as any;
    await demoRequest("/order-access/exchange", {
      method: "POST",
      body: { orderId: "o", accessToken: link.accessToken },
    });
    await demoRequest("/__demo/customer", { method: "POST" });
    const [view] = visibleDemoOrders("customer", db.customer, db.access);
    expect(view.accessKind).toBe("ORDER_GRANT");
    expect(view.allowedActions).not.toContain("ORDER_CANCEL_PENDING");
    await expect(
      demoRequest("/customer/orders/o/claim", {
        method: "POST",
        body: { confirm: true },
      }),
    ).rejects.toThrow();
    expect(o.ownerAccountId).toBeUndefined();
    await demoRequest("/admin/orders/o/guest-access/close", {
      method: "POST",
      body: { expectedVersion: 2, reason: "关闭" },
    });
    expect(visibleDemoOrders("customer", db.customer, db.access)).toHaveLength(
      0,
    );
    await expect(
      demoRequest("/order-access/exchange", {
        method: "POST",
        body: { orderId: "o", accessToken: link.accessToken },
      }),
    ).rejects.toThrow();
  });
  it("原设备逐单关联保留其他游客订单、草稿和结算快照", async () => {
    const first = {
      id: "one",
      ownerPrincipalId: "guest-test",
      checkoutAccountId: undefined,
      status: "PENDING_CONFIRMATION",
    } as Order;
    const second = { ...first, id: "two" };
    db.orders.set(first.id, first);
    db.orders.set(second.id, second);
    for (const id of ["one", "two"])
      await demoRequest("/admin/orders/" + id + "/guest-access/reset", {
        method: "POST",
        body: { expectedVersion: 1, reason: "测试" },
      });
    const prep = (await demoRequest("/preparations", {
      method: "POST",
      body: { lines },
    })) as any;
    const principal = db.guestPrincipalId;
    await demoRequest("/__demo/customer", { method: "POST" });
    await demoRequest("/customer/orders/one/claim", {
      method: "POST",
      body: { confirm: true },
    });
    expect(first.ownerAccountId).toBe(db.customer.account?.id);
    expect(first.checkoutAccountId).toBeUndefined();
    expect(second.ownerAccountId).toBeUndefined();
    expect(db.guestPrincipalId).toBe(principal);
    expect(
      ((await demoRequest("/customer/orders/two/guest-access")) as any).status,
    ).toBe("ACTIVE");
    expect(
      ((await demoRequest("/customer/orders/one/guest-access")) as any).status,
    ).toBe("CLOSED");
    expect(db.preps.get(prep.id)).toBe(prep);
  });
});

it("admin demo projections expose registered pickup origins only with customer capability", () => {
  const order = { id: "registered", pickupCode: "AB123", checkoutAccountId: db.account.id,
    ownerAccountId: db.account.id, files: [] } as unknown as Order;
  db.orders.set(order.id, order);
  db.staff = { id: "admin", role: "ADMIN", capabilities: ["production.manage"] } as any;
  const restricted = visibleDemoOrders("admin", db.customer, null)[0];
  expect(restricted.adminPickup).toEqual({ pickupCode: "AB123", placedWithAccount: true, customerDetailId: null });
  expect(restricted.customerSummary).toBeNull();
  db.staff.capabilities = ["customer.manage"];
  const allowed = visibleDemoOrders("admin", db.customer, null)[0];
  expect(allowed.adminPickup?.customerDetailId).toBe(db.account.id);
  expect(allowed.customerSummary?.email).toBe(db.accounts[0].email);
});


it("courier demo identity follows account association instead of the checkout snapshot", () => {
  const order = { id: "courier-identity", pickupCode: "AB123", files: [] } as unknown as Order;
  db.orders.set(order.id, order);
  expect(visibleDemoOrders("courier", db.customer, null)[0].accountOwned).toBe(false);
  order.ownerAccountId = db.account.id;
  expect(order.checkoutAccountId).toBeUndefined();
  expect(visibleDemoOrders("courier", db.customer, null)[0].accountOwned).toBe(true);
});
