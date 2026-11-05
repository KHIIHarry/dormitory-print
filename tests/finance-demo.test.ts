import { beforeEach, expect, it } from "vitest";
import { demoRequest, getDemoGrowth, setDemoStaff } from "../src/growth/demo";
import { shanghaiToday } from "../src/admin/finance-api";
const staff = {
  id: "finance-admin",
  role: "ADMIN",
  capabilities: ["finance.manage"],
} as any;
beforeEach(() => {
  setDemoStaff(null);
  getDemoGrowth().orders.clear();
});
it("keeps finance demo records functional and requires finance permission", async () => {
  await expect(demoRequest("/admin/finance/summary")).rejects.toThrow();
  setDemoStaff(staff);
  const expense = (await demoRequest("/admin/finance/expenses", {
    method: "POST",
    body: {
      category: "PAPER",
      spentOn: shanghaiToday(),
      amountCents: 2500,
      paperSheets: 500,
      note: "测试纸张",
    },
  })) as any;
  expect(expense.category).toBe("PAPER");
  let summary = (await demoRequest("/admin/finance/summary")) as any;
  expect(summary.paperSheets).toBe(500);
  expect(summary.balanceCents).toBe(-2500);
  await expect(
    demoRequest(`/admin/finance/expenses/${expense.id}/void`, {
      method: "POST",
      body: { expectedVersion: 99, reason: "错误版本" },
    }),
  ).rejects.toThrow();
  await demoRequest(`/admin/finance/expenses/${expense.id}/void`, {
    method: "POST",
    body: { expectedVersion: expense.version, reason: "测试作废" },
  });
  summary = (await demoRequest("/admin/finance/summary")) as any;
  expect(summary.paperSheets).toBe(0);
  expect(summary.paperUnitYuan).toBeNull();
});

it("groups demo receipt instants by Shanghai month rather than their UTC text", async () => {
  setDemoStaff(staff);
  getDemoGrowth().orders.set("cross-month", {
    id: "cross-month",
    settlement: {
      settlementKind: "CASH_MANUAL",
      cashReceivedCents: 980,
      discountCents: 0,
      confirmedAt: "2026-10-31T16:30:00Z",
      confirmedBy: "admin",
    },
  } as any);
  const oct = (await demoRequest(
    "/admin/finance/summary?month=2026-10",
  )) as any;
  const nov = (await demoRequest(
    "/admin/finance/summary?month=2026-11",
  )) as any;
  expect(oct.confirmedReceivedCents).toBe(0);
  expect(nov.confirmedReceivedCents).toBe(980);
});
