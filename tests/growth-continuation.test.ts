import { beforeEach, describe, it, expect } from "vitest";
import { demoRequest, getDemoGrowth, defaultVolume } from "../src/growth/demo";
import { buildQuote } from "../src/growth/pricing";
import type { Order } from "../src/lib/types";
const db = getDemoGrowth();
const lines = [
  {
    key: "a4",
    relatedId: "f",
    service: "A4" as const,
    unit: "SHEET" as const,
    quantity: 10,
    unitPriceCents: 10,
    amountCents: 100,
  },
];
beforeEach(() => {
  db.preps.clear();
  db.orders.clear();
  db.coupons = [];
  db.records = [];
  db.access = null;
  db.links = [];
  db.imports.clear();
  db.volume = structuredClone(defaultVolume);
  db.guestPrincipalId = "guest-test";
  db.customer = { role: "CUSTOMER", authKind: "ANONYMOUS", capabilities: [] };
});
describe("v1.3续作回归", () => {
  it("账号准备单可以撤回为游客报价，PDF产物保持不变，旧报价不能提交", async () => {
    const p = (await demoRequest("/preparations", {
      method: "POST",
      body: { lines, artifacts: [{ sha256: "unchanged" }] },
    })) as any;
    await demoRequest("/__demo/customer", { method: "POST" });
    await demoRequest(`/preparations/${p.id}/adopt-checkout`, {
      method: "POST",
      body: { expectedVersion: 1, confirm: true },
    });
    const adopted = p.quote.id;
    const q = (await demoRequest(`/preparations/${p.id}/withdraw-checkout`, {
      method: "POST",
      body: { expectedVersion: 2, confirm: true },
    })) as any;
    expect(q.checkoutAccountId).toBeNull();
    expect(q.id).not.toBe(adopted);
    expect(p.version).toBe(3);
    expect(p.artifacts).toEqual([{ sha256: "unchanged" }]);
    await expect(
      demoRequest("/__demo/reserve", {
        method: "POST",
        body: { preparationId: p.id, quoteId: adopted, orderId: "o" },
      }),
    ).rejects.toThrow();
  });
  it("游客轮换后旧报价失效，合法账号可重新核验准备单，旧游客不能继续使用", async () => {
    const p = (await demoRequest("/preparations", {
      method: "POST",
      body: { lines },
    })) as any;
    await demoRequest("/__demo/customer", { method: "POST" });
    const oldQuoteId = p.quote.id;
    await demoRequest("/customer/account/link-guest", {
      method: "POST",
      body: { confirm: true },
    });
    await expect(
      demoRequest(`/preparations/${p.id}/adopt-checkout`, {
        method: "POST",
        body: { expectedVersion: 1, confirm: true },
      }),
    ).rejects.toThrow();
    await expect(
      demoRequest("/__demo/reserve", {
        method: "POST",
        body: { preparationId: p.id, quoteId: oldQuoteId, orderId: "o" },
      }),
    ).rejects.toThrow();
  });
  it("单单受托人不能因修订门槛变化静默丢掉预约券", async () => {
    const c = {
      id: "c",
      couponGrantId: "cg",
      campaignId: "campaign",
      accountId: "owner",
      name: "预约券",
      amountCents: 50,
      minSpendCents: 80,
      service: "A4" as const,
      status: "RESERVED" as const,
      reservedOrderId: "o",
      riskHold: "NONE" as const,
      validUntil: null,
      version: 1,
    };
    db.coupons.push(c);
    const q = buildQuote(lines, db.volume, [c], {}, "owner", "o");
    db.orders.set("o", {
      id: "o",
      version: 1,
      status: "PENDING_CONFIRMATION",
      checkoutAccountId: "owner",
      ownerAccountId: "owner",
      ownerPrincipalId: "account-owner",
      quote: q,
    } as Order);
    await expect(
      demoRequest("/preparations", {
        method: "POST",
        body: {
          replacesOrderId: "o",
          lines: [{ ...lines[0], quantity: 5, amountCents: 50 }],
        },
      }),
    ).rejects.toThrow(/持有人/);
    expect(c.status).toBe("RESERVED");
  });
  it("优惠记录筛选会约束结果", async () => {
    db.records.push(
      ...["USED", "RESERVED"].map((status, i) => ({
        id: String(i),
        accountMasked: "a***@example.invalid",
        campaignName: "活动",
        type: status,
        status,
        amountCents: 20,
        reason: "测试",
        time: new Date().toISOString(),
        version: 1,
      })),
    );
    const result = (await demoRequest(
      "/admin/promotions/records?status=USED",
    )) as any;
    expect(result.items.map((r: any) => r.status)).toEqual(["USED"]);
  });
  it("HTML模板预览显示编辑后的HTML并拒绝主动内容", async () => {
    const r = (await demoRequest(
      "/admin/mail/templates/ORDER_CREATED/preview",
      {
        method: "POST",
        body: {
          subject: "{{brandName}}",
          textBody: "文本",
          htmlBody: "<h2>{{displayName}}</h2>",
        },
      },
    )) as any;
    expect(r.htmlBody).toBe("<h2>示例同学</h2>");
    await expect(
      demoRequest("/admin/mail/templates/ORDER_CREATED/preview", {
        method: "POST",
        body: { htmlBody: '<img src="https://example.invalid/tracker">' },
      }),
    ).rejects.toThrow();
  });
});

it("导入的只读范围不会变成所有权，也不暴露原件与未授权PDF", async () => {
  const o = {
    id: "restricted",
    ownerPrincipalId: "other",
    ownerAccountId: "other",
    allowedActions: ["ORDER_REVISE_PENDING", "ORDER_CANCEL_PENDING"],
    files: [
      {
        id: "f",
        url: "private-original.docx",
        pdfUrl: "private-final.pdf",
        finalArtifacts: [{ url: "private-final.pdf" }],
      },
    ],
  } as Order;
  db.orders.set(o.id, o);
  const result = (await demoRequest("/admin/orders/restricted/access-links", {
    method: "POST",
    body: { reason: "测试" },
  })) as any;
  db.links[0].scopes = ["ORDER_VIEW", "ORDER_IMPORT"];
  await demoRequest("/order-access/exchange", {
    method: "POST",
    body: { orderId: o.id, accessToken: result.accessToken },
  });
  await demoRequest("/__demo/customer", { method: "POST" });
  await demoRequest("/customer/account/import-order", {
    method: "POST",
    body: { orderId: o.id, confirm: true },
  });
  const { visibleDemoOrders, syncDemoOrders } =
    await import("../src/growth/demo");
  const [view] = visibleDemoOrders("customer", db.customer, null);
  expect(view.accessKind).toBe("IMPORTED_GRANT");
  expect(view.allowedActions).not.toContain("ORDER_REVISE_PENDING");
  expect(view.files[0].url).toBe("");
  expect(view.files[0].pdfUrl).toBeUndefined();
  syncDemoOrders([view]);
  expect(db.orders.get(o.id)?.files[0].url).toBe("private-original.docx");
});
it("预约券撤销保留金额和预约事实并阻断结算，普通解冻不能清除撤销核对", async () => {
  const c = {
    id: "c",
    couponGrantId: "cg",
    campaignId: "campaign",
    name: "券",
    amountCents: 100,
    minSpendCents: 0,
    service: "A4" as const,
    status: "RESERVED" as const,
    reservedOrderId: "o",
    riskHold: "NONE" as const,
    validUntil: null,
    version: 1,
  };
  db.coupons.push(c);
  db.orders.set("o", {
    id: "o",
    totalCents: 0,
    files: [],
    blockers: [],
    quote: buildQuote(lines, db.volume, [c], {}, "owner", "o"),
  } as unknown as Order);
  await demoRequest("/admin/promotions/coupons/cg/revoke", {
    method: "POST",
    body: { expectedVersion: 1, reason: "核对撤销" },
  });
  expect(c.status).toBe("RESERVED");
  expect(db.orders.get("o")?.totalCents).toBe(0);
  expect(
    db.orders.get("o")?.blockers?.some((b) => b.code === "COUPON_REVOKED"),
  ).toBe(true);
  await demoRequest("/admin/promotions/coupons/cg/unfreeze", {
    method: "POST",
    body: { expectedVersion: 2, reason: "解除冻结" },
  });
  expect(
    db.orders.get("o")?.blockers?.some((b) => b.code === "COUPON_REVOKED"),
  ).toBe(true);
});
it("客户可见筛选条件在开发适配中都被执行", async () => {
  const account = db.accounts[0];
  account.createdAt = "2026-10-01T00:00:00Z";
  expect(
    ((await demoRequest("/admin/customers?verified=false")) as any).items,
  ).toHaveLength(0);
  expect(
    ((await demoRequest("/admin/customers?inviteCode=INVALID")) as any).items,
  ).toHaveLength(0);
  expect(
    ((await demoRequest("/admin/customers?createdFrom=2026-10-02")) as any)
      .items,
  ).toHaveLength(0);
  expect(
    ((await demoRequest("/admin/customers?minOrders=1&minCoupons=1")) as any)
      .items,
  ).toHaveLength(0);
});
