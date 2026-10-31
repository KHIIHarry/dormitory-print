import { describe, it, expect } from "vitest";
import { canOrderAction, orderAccessUrl } from "../src/growth/order-security";
describe("订单授权边界", () => {
  it("服务器动作、状态和单单范围同时允许才可编辑", () => {
    const order = {
      id: "o1",
      status: "PENDING_CONFIRMATION",
      allowedActions: ["ORDER_REVISE_PENDING"],
    };
    expect(canOrderAction(order, "ORDER_REVISE_PENDING", null)).toBe(true);
    expect(
      canOrderAction(
        { ...order, status: "PENDING_PRINT" },
        "ORDER_REVISE_PENDING",
        null,
      ),
    ).toBe(false);
    expect(
      canOrderAction(order, "ORDER_REVISE_PENDING", {
        orderId: "o1",
        scopes: ["ORDER_VIEW"],
        allowedActions: order.allowedActions,
        expiresAt: null,
        sourceGrantId: "g",
      }),
    ).toBe(false);
    expect(
      canOrderAction(order, "ORDER_REVISE_PENDING", {
        orderId: "o1",
        scopes: order.allowedActions,
        allowedActions: [],
        expiresAt: null,
        sourceGrantId: "g",
      }),
    ).toBe(false);
  });
  it("不得把一单授权用于另一单，也不得使用过期授权", () => {
    const order = {
      id: "o1",
      status: "READY_FOR_DELIVERY",
      allowedActions: ["ORDER_CHANGE_LOCATION_PRE_DELIVERY"],
    };
    expect(
      canOrderAction(order, "ORDER_CHANGE_LOCATION_PRE_DELIVERY", {
        orderId: "o2",
        scopes: order.allowedActions,
        allowedActions: order.allowedActions,
        expiresAt: null,
        sourceGrantId: "g",
      }),
    ).toBe(false);
    expect(
      canOrderAction(order, "ORDER_CHANGE_LOCATION_PRE_DELIVERY", {
        orderId: "o1",
        scopes: order.allowedActions,
        allowedActions: order.allowedActions,
        expiresAt: "2000-01-01",
        sourceGrantId: "g",
      }),
    ).toBe(false);
  });
  it("高熵凭据只能在fragment，不能用短提取码", () => {
    const url = new URL(
      orderAccessUrl("https://print.example", "o1", "a".repeat(64)),
    );
    expect(url.searchParams.get("orderId")).toBe("o1");
    expect(url.search).not.toContain("aaaa");
    expect(url.hash).toContain("token=");
    expect(() =>
      orderAccessUrl("https://print.example", "o1", "K7M4P"),
    ).toThrow();
  });
});

import { effectiveOrderAccess } from "../src/growth/order-security";
it("独立账号所有权不会被同单的只读或过期链接缩小", () => {
  const account = {
    role: "CUSTOMER" as const,
    authKind: "REGISTERED" as const,
    capabilities: [],
    account: { id: "owner" },
  } as any;
  const link = {
    orderId: "o",
    sourceGrantId: "g",
    scopes: ["ORDER_VIEW"],
    allowedActions: ["ORDER_VIEW"],
    expiresAt: "2000-01-01",
  };
  expect(
    effectiveOrderAccess({ id: "o", ownerAccountId: "owner" }, account, link),
  ).toBeNull();
  expect(
    effectiveOrderAccess({ id: "o", ownerAccountId: "other" }, account, link),
  ).toBe(link);
  expect(
    effectiveOrderAccess({ id: "o", accessKind: "GUEST_OWNER" }, account, link),
  ).toBeNull();
});
