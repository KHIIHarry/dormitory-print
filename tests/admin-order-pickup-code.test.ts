import { createElement } from "react";
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { OrderPickupCode } from "../src/admin/order-pickup-code";

const render = (pickup: {
  pickupCode: string;
  placedWithAccount: boolean;
  customerDetailId: string | null;
}) =>
  renderToStaticMarkup(
    createElement(
      MemoryRouter,
      null,
      createElement(OrderPickupCode, {
        pickup,
        href: "/manage/print/task-1",
        subline: "11/17 14:26",
      }),
    ),
  );
describe("admin pickup code", () => {
  it("keeps task navigation and customer navigation independently accessible", () => {
    const html = render({
      pickupCode: "A7K8M",
      placedWithAccount: true,
      customerDetailId: "customer-1",
    });
    expect(html).toContain('href="/manage/print/task-1"');
    expect(html).toContain('href="/manage/customers/customer-1"');
    expect(html).toContain("查看 A7K8M 的客户详情");
    expect(html).toContain("11/17 14:26");
  });
  it("renders a noninteractive identification icon for restricted staff", () => {
    const html = render({
      pickupCode: "A7K8M",
      placedWithAccount: true,
      customerDetailId: null,
    });
    expect(html).toContain("账号订单");
    expect(html.match(/<a /g)).toHaveLength(1);
    expect(html).not.toContain("/customers/");
  });
  it("reserves the same icon slot for an order placed as guest", () => {
    const html = render({
      pickupCode: "K5Q2R",
      placedWithAccount: false,
      customerDetailId: null,
    });
    expect(html).toContain("order-pickup-origin");
    expect(html).not.toContain("<svg");
    expect(html.match(/<a /g)).toHaveLength(1);
  });
});

import {
  guestAccessActionPayload,
  type GuestAccessView,
} from "../src/v1/admin-api";
const view: GuestAccessView = {
  status: "ACTIVE",
  version: 3,
  openedAt: null,
  expiresAt: null,
  serverNow: "2026-11-17T00:00:00Z",
  canOpen: true,
};
it("validates reset days and preserves the separate access version", () => {
  expect(guestAccessActionPayload(view, " 核实后重置 ", "14")).toEqual({
    expectedVersion: 3,
    reason: "核实后重置",
    days: 14,
  });
  for (const value of ["", "0", "15", "1.5"])
    expect(() => guestAccessActionPayload(view, "原因", value)).toThrow();
  expect(() => guestAccessActionPayload(view, " ", "14")).toThrow();
});
