import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { planResources } from "../src/lib/resources";
import { requestedSlotOf, confirmedSlotOf } from "../src/v1/customer";
import * as customerOrders from "../src/customer/orders";

it("keeps the management slot catalog authoritative during refresh", () => {
  for (const path of [
    "/manage/slots",
    "/manage/orders",
    "/manage/orders/order-1",
    "/manage/batches/batch-1",
  ]) {
    expect(
      planResources(path, "", "ADMIN", ["delivery.manage"]).find(
        (r) => r.name === "slots",
      )?.path,
    ).toBe("/admin/slots");
  }
  expect(
    planResources("/manage/orders/order-1", "", "ADMIN", [
      "payment.manage",
    ]).some((r) => r.path === "/admin/slots"),
  ).toBe(false);
});

it("reads historical promises from the embedded arrangement when public slots disappear", () => {
  const requested = {
    slotId: "closed",
    date: "2026-11-01",
    start: "12:00",
    end: "13:00",
  };
  const confirmed = {
    slotId: "closed",
    date: "2026-11-01",
    start: "14:00",
    end: "15:00",
  };
  const order = {
    arrangement: { originalRequest: requested, confirmed },
  } as any;
  expect(requestedSlotOf(order)).toEqual(requested);
  expect(confirmedSlotOf(order)).toEqual(confirmed);
});

it("shows actionable unpaid slot proposals and hides actions from read-only grants", () => {
  const Panel = (customerOrders as any).ArrangementPanel;
  expect(Panel).toBeTypeOf("function");
  const order = {
    status: "PENDING_CONFIRMATION",
    slotProposal: { slotId: "next", reason: "已截单", confirmed: false },
    arrangement: { status: "REQUESTED", effective: {} },
  };
  const props = {
    order,
    busy: false,
    canAgree: true,
    onAccept: async () => {},
    onReject: async () => {},
  };
  const html = renderToStaticMarkup(createElement(Panel, props));
  expect(html).toContain("接受新安排");
  expect(html).toContain("拒绝新安排");
  expect(html).toContain("已截单");
  const readonlyHtml = renderToStaticMarkup(
    createElement(Panel, { ...props, canAgree: false }),
  );
  expect(readonlyHtml).not.toContain("接受新安排");
  expect(readonlyHtml).not.toContain("拒绝新安排");
});

import * as pricing from "../src/growth/pricing";
it("requires an explicit unmatched receipt and reuses its net cash without another receipt", () => {
  const settle = (pricing as any).settlementWithReceipt;
  expect(settle).toBeTypeOf("function");
  const receipts = [
    {
      id: "late-1",
      amountCents: 600,
      refundedCents: 100,
      refundableCents: 500,
      status: "UNMATCHED",
    },
    { id: "late-2", amountCents: 500, status: "UNMATCHED" },
  ];
  expect(() => settle(500, "5.00", false, receipts, "")).toThrow("选择");
  expect(settle(500, "5.00", false, receipts, "late-1")).toMatchObject({
    receivedCents: 500,
    receiptId: "late-1",
  });
  expect(() => settle(600, "6.00", false, receipts, "late-1")).toThrow("净额");
  expect(() => settle(500, "5.00", false, receipts, "missing")).toThrow();
  expect(settle(500, "5.00", false, [], "").receiptId).toBeUndefined();
  expect(() => settle(500, "5.00", false, [], "", true)).toThrow("已登记");
});

import * as checkout from "../src/customer/checkout";
it("stops offering a stale slot exactly at cutoff without waiting for polling", () => {
  const selectable = (checkout as any).slotCurrentlySelectable;
  expect(selectable).toBeTypeOf("function");
  const slot = {
    enabled: true,
    selectable: true,
    cutoffAt: "2026-11-01T04:00:00Z",
  };
  expect(selectable(slot, Date.parse("2026-11-01T03:59:59Z"))).toBe(true);
  expect(selectable(slot, Date.parse("2026-11-01T04:00:00Z"))).toBe(false);
  expect(
    selectable(
      { ...slot, selectable: false },
      Date.parse("2026-11-01T03:59:59Z"),
    ),
  ).toBe(false);
});

import * as adminOrders from "../src/admin/orders";
it("shows failed and unknown delivery notices without claiming email delivery", () => {
  const Panel = (adminOrders as any).DeliveryNoticePanel;
  expect(Panel).toBeTypeOf("function");
  const notices = [
    {
      id: "failed",
      version: 1,
      status: "FAILED",
      lastError: "SMTPUnavailable",
      attempts: 3,
      createdAt: "2026-11-01T04:00:00Z",
    },
    {
      id: "unknown",
      version: 2,
      status: "UNKNOWN",
      attempts: 1,
      createdAt: "2026-11-01T04:00:00Z",
    },
    {
      id: "accepted",
      version: 3,
      status: "ACCEPTED",
      attempts: 1,
      createdAt: "2026-11-01T04:00:00Z",
    },
  ];
  const html = renderToStaticMarkup(
    createElement(Panel, { notices, canRetry: true, onRetry: async () => {} }),
  );
  expect(html).toContain("SMTPUnavailable");
  expect(html).toContain("结果未知");
  expect(html).toContain("SMTP 已接受");
  expect(html).not.toContain("邮件已送达");
  expect((html.match(/重试通知/g) || []).length).toBe(1);
  const readonlyHtml = renderToStaticMarkup(
    createElement(Panel, { notices, canRetry: false, onRetry: async () => {} }),
  );
  expect(readonlyHtml).not.toContain("重试通知");
});

it("offers payment operators an in-order slot coordination form using only selectable options", () => {
  const Panel = (adminOrders as any).PendingSlotProposal;
  expect(Panel).toBeTypeOf("function");
  const html = renderToStaticMarkup(
    createElement(Panel, {
      slots: [
        {
          id: "open",
          name: "可预约",
          enabled: true,
          selectable: true,
          date: "2030-11-01",
          start: "12:00",
          end: "13:00",
        },
        { id: "closed", name: "已封批", enabled: true, selectable: false },
      ],
      proposal: { slotId: "before", confirmed: true },
      onSubmit: async () => {},
    }),
  );
  expect(html).toContain("可预约");
  expect(html).not.toContain("已封批");
  expect(html).toContain("客户已接受");
  expect(html).toContain("提出时段建议");
});

it("keeps an accepted unpaid suggestion distinct from a confirmed slot and a rejected one under coordination", () => {
  const Panel = customerOrders.ArrangementPanel;
  const base = {
    order: {
      status: "PENDING_CONFIRMATION",
      slotProposal: { slotId: "next", confirmed: true },
      arrangement: { status: "REQUESTED", effective: {} },
    } as any,
    canAgree: true,
    busy: false,
    onAccept: async () => {},
    onReject: async () => {},
  };
  const html = renderToStaticMarkup(createElement(Panel, base));
  expect(html).toContain("已接受时段建议");
  expect(html).toContain("名额尚未保证");
  expect(html).not.toContain("接受新安排");
  const rejectedHtml = renderToStaticMarkup(
    createElement(Panel, {
      ...base,
      order: {
        ...base.order,
        slotProposal: { slotId: "next", rejected: true },
      },
    }),
  );
  expect(rejectedHtml).toContain("已拒绝时段建议");
  expect(rejectedHtml).toContain("等待管理员继续协调");
});

it("renders the effective delivery window and pending status in the order list summary", () => {
  const Summary = (customerOrders as any).OrderArrangementSummary;
  expect(Summary).toBeTypeOf("function");
  const html = renderToStaticMarkup(
    createElement(Summary, {
      order: {
        arrangement: {
          status: "WAITING_CUSTOMER",
          effective: { date: "2026-11-01", start: "14:00", end: "15:00" },
          reason: "运力调整",
        },
      },
    }),
  );
  expect(html).toContain("等待你确认");
  expect(html).toContain("14:00");
  expect(html).toContain("运力调整");
});

import { MemoryRouter } from "react-router-dom";
import { PaidArrangementProposals } from "../src/admin/logistics";
it("keeps paid-order refresh reachable when the initial response is empty or failed", () => {
  const html = renderToStaticMarkup(
    createElement(
      MemoryRouter,
      {},
      createElement(PaidArrangementProposals, { slots: [] }),
    ),
  );
  expect(html).toContain("刷新已结算订单");
});

import * as logistics from "../src/admin/logistics";
it("offers an explicit read-only legacy-data inspection without an automatic repair action", () => {
  const Panel = (logistics as any).DeliveryRepairPanel;
  expect(Panel).toBeTypeOf("function");
  const html = renderToStaticMarkup(createElement(Panel));
  expect(html).toContain("查看旧数据检查");
  expect(html).toContain("只读");
  expect(html).not.toContain("自动修复");
});
