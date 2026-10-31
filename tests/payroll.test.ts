import { describe, expect, it } from "vitest";

import {
  PAYROLL_PATHS,
  applyPolicyPayload,
  entryFlags,
  entryOriginalDeliveryPeriod,
  manualEntryPayload,
  normalizePayrollPeriod,
  policyModeLabel,
  policyPayload,
  settlementPayload,
  settlementSelection,
  shanghaiPeriod,
  suggestedSettlementEntries,
  wageKindLabel,
  type WageEntry,
} from "../src/v1/payroll-api";

function entry(overrides: Partial<WageEntry> = {}): WageEntry {
  return {
    id: "entry-1",
    version: 1,
    staffId: "staff-1",
    orderId: null,
    assignmentId: null,
    sourceKey: "DELIVERY:order-1",
    kind: "DELIVERY",
    amountCents: 250,
    period: "2026-10",
    reason: "",
    actorId: "",
    snapshot: {},
    createdAt: "2026-10-30T12:00:00+00:00",
    settlementId: null,
    settledAt: null,
    needsConfiguration: false,
    ...overrides,
  };
}

describe("payroll period", () => {
  it("uses the Chinese month even when UTC is still the previous day", () => {
    expect(shanghaiPeriod("2026-10-31T15:59:00Z")).toBe("2026-10");
    expect(shanghaiPeriod("2026-10-31T16:30:00Z")).toBe("2026-11");
  });

  it("rejects malformed months", () => {
    expect(normalizePayrollPeriod(" 2026-10 ")).toBe("2026-10");
    expect(() => normalizePayrollPeriod("2026-13")).toThrow();
    expect(() => normalizePayrollPeriod("2026-1")).toThrow();
    expect(() => normalizePayrollPeriod("")).toThrow();
  });
});

describe("policy payloads", () => {
  it("builds a fixed policy with an explicit version and trimmed reason", () => {
    expect(
      policyPayload({
        mode: "FIXED",
        fixedCents: "250",
        rateBps: "0",
        enabled: true,
        expectedVersion: 3,
        reason: " 调价 ",
      }),
    ).toEqual({
      mode: "FIXED",
      fixedCents: 250,
      rateBps: 0,
      enabled: true,
      expectedVersion: 3,
      reason: "调价",
    });
  });

  it("accepts a percentage at the 10000 bps boundary", () => {
    expect(
      policyPayload({ mode: "PERCENT", fixedCents: 0, rateBps: 10000 }).rateBps,
    ).toBe(10000);
  });

  it("rejects rates beyond 10000 bps, negative fixed pay and fractions", () => {
    expect(() =>
      policyPayload({ mode: "PERCENT", fixedCents: 0, rateBps: 10001 }),
    ).toThrow("工资比例");
    expect(() =>
      policyPayload({ mode: "FIXED", fixedCents: -1, rateBps: 0 }),
    ).toThrow("固定工资");
    expect(() =>
      policyPayload({ mode: "FIXED", fixedCents: 1.5, rateBps: 0 }),
    ).toThrow("固定工资");
  });

  it("omits expectedVersion when creating a policy", () => {
    expect(
      policyPayload({ mode: "FIXED", fixedCents: 0, rateBps: 0 }),
    ).not.toHaveProperty("expectedVersion");
  });
});

describe("manual and settlement payloads", () => {
  it("requires a non-blank manual reason and validates optional period", () => {
    expect(() =>
      manualEntryPayload({
        staffId: "staff-1",
        amountCents: 50,
        reason: "   ",
      }),
    ).toThrow("人工调整原因");
    expect(
      manualEntryPayload({
        staffId: " staff-1 ",
        orderId: " order-1 ",
        amountCents: "-30",
        period: "2026-10",
        reason: " 补扣 ",
      }),
    ).toEqual({
      staffId: "staff-1",
      amountCents: -30,
      reason: "补扣",
      orderId: "order-1",
      period: "2026-10",
    });
    expect(() =>
      manualEntryPayload({
        staffId: "staff-1",
        amountCents: 1.5,
        reason: "补发",
      }),
    ).toThrow("整数");
  });

  it("rejects duplicate settlement entries and omits an empty selection", () => {
    expect(() =>
      settlementPayload({
        staffId: "staff-1",
        period: "2026-10",
        entryIds: ["a", "a"],
        reason: "月结",
      }),
    ).toThrow("不可重复");
    expect(
      settlementPayload({
        staffId: "staff-1",
        period: "2026-10",
        entryIds: [],
        reason: " 月结 ",
      }),
    ).toEqual({ staffId: "staff-1", period: "2026-10", reason: "月结" });
    expect(() =>
      settlementPayload({
        staffId: "staff-1",
        period: "2026-10",
        reason: "",
      }),
    ).toThrow("发放原因");
  });
});

describe("settlement selection", () => {
  it("nets positive and negative entries for one staff member", () => {
    const selection = settlementSelection(
      [
        entry({ id: "a", amountCents: 100 }),
        entry({ id: "b", amountCents: -30 }),
        entry({ id: "c", amountCents: 50 }),
      ],
      "2026-10",
    );
    expect(selection).toEqual({ netCents: 120, error: "" });
  });

  it("rejects negative net, settled rows, markers, mixed staff and future months", () => {
    expect(
      settlementSelection([entry({ amountCents: -10 })], "2026-10").error,
    ).toContain("负数");
    expect(
      settlementSelection(
        [entry({ settlementId: "settlement-1" })],
        "2026-10",
      ).error,
    ).toContain("已发放");
    expect(
      settlementSelection(
        [entry({ kind: "NEEDS_CONFIGURATION", needsConfiguration: true })],
        "2026-10",
      ).error,
    ).toContain("待配置");
    expect(
      settlementSelection(
        [entry({ id: "a", staffId: "a" }), entry({ id: "b", staffId: "b" })],
        "2026-10",
      ).error,
    ).toContain("一个员工");
    expect(
      settlementSelection([entry({ period: "2026-11" })], "2026-10").error,
    ).toContain("未来月份");
  });

  it("suggests current unpaid entries and prior unpaid positive and negative carry-forward", () => {
    const rows = suggestedSettlementEntries({
      entries: [
        entry({ id: "current" }),
        entry({ id: "settled", settlementId: "s" }),
        entry({ id: "marker", needsConfiguration: true }),
      ],
      carryForward: [
        entry({ id: "negative", period: "2026-09", amountCents: -40 }),
        entry({ id: "positive", period: "2026-09", amountCents: 40 }),
      ],
    });
    expect(rows.map((row) => row.id)).toEqual(["current", "negative", "positive"]);
  });
});

describe("labels and API paths", () => {
  it("exposes all visible flags including refund-after-settlement", () => {
    expect(
      entryFlags(
        entry({
          kind: "REFUND_ADJUSTMENT",
          amountCents: -100,
          settlementId: "settlement-1",
        }),
      ),
    ).toEqual(["退款调整", "已发放", "冲减", "退款后追加"]);
    expect(
      entryOriginalDeliveryPeriod(
        entry({
          kind: "REFUND_ADJUSTMENT",
          period: "2026-02",
          snapshot: { originalDeliveryPeriod: "2026-01" },
        }),
      ),
    ).toBe("2026-01");
    expect(entryOriginalDeliveryPeriod(entry())).toBeNull();
    expect(wageKindLabel("DELIVERY")).toBe("交付计件");
    expect(wageKindLabel("NEEDS_CONFIGURATION")).toBe("待配置工资");
    expect(policyModeLabel("PERCENT")).toContain("比例");
  });

  it("never sends a staff id on the employee endpoint", () => {
    expect(PAYROLL_PATHS.me("2026-10")).toBe("/payroll/me?period=2026-10");
    expect(PAYROLL_PATHS.me()).not.toContain("staffId");
    expect(PAYROLL_PATHS.staff).toBe("/admin/payroll/staff");
    expect(PAYROLL_PATHS.ledger({ period: "2026-10", staffId: "staff-1" })).toBe(
      "/admin/payroll/ledger?period=2026-10&staffId=staff-1",
    );
    expect(PAYROLL_PATHS.staffPolicy("a/b")).toBe(
      "/admin/payroll/policies/staff/a%2Fb",
    );
    expect(PAYROLL_PATHS.applyPolicy("o 1")).toBe(
      "/admin/payroll/deliveries/o%201/apply-policy",
    );
  });

  it("builds an apply-policy body with a mandatory reason", () => {
    expect(applyPolicyPayload("p1", " 补录 ")).toEqual({
      policyId: "p1",
      reason: "补录",
    });
    expect(() => applyPolicyPayload(null, " ")).toThrow();
  });
});
