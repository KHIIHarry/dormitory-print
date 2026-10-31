import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { expect, it, vi } from "vitest";
const state = vi.hoisted(() => ({
  orders: { loading: true, hasData: false, error: "", key: "q" },
  resource: {
    data: {
      items: [
        {
          id: "c",
          displayName: "保留客户",
          emailMasked: "x***",
          status: "ACTIVE",
          verified: true,
          orderCount: 1,
          couponCount: 0,
          referralCount: 0,
        },
      ],
    },
    loading: false,
    refreshing: false,
    hasData: true,
    error: "刷新网络失败",
    reload: () => {},
  },
}));
vi.mock("../src/lib/store", () => ({
  ADMIN: "/manage",
  useStore: () => ({
    data: {
      orders: [
        {
          id: "o",
          pickupCode: "旧查询订单",
          nickname: "旧查询订单",
          phone: "",
          files: [],
          status: "DELIVERED",
          totalCents: 100,
          createdAt: "",
        },
      ],
      buildings: [],
      slots: [],
      batches: [],
    },
    resources: { orders: state.orders },
    refresh: () => {},
  }),
}));
vi.mock("../src/growth/client", () => ({
  useGrowthResource: () => state.resource,
}));
vi.mock("../src/admin/growth/common", async (original) => ({
  ...(await original<typeof import("../src/admin/growth/common")>()),
  useAdminAccess: () => ({
    allowed: true,
    loading: false,
    error: "",
    reload: () => {},
  }),
}));
import { AdminOrders } from "../src/admin/orders";
import { CustomersPage } from "../src/admin/growth/customers";
const render = (component: typeof AdminOrders) =>
  renderToStaticMarkup(
    createElement(MemoryRouter, {}, createElement(component)),
  );
it("keeps order filters/header mounted while replacing cross-query results with a skeleton", () => {
  const html = render(AdminOrders);
  expect(html).toContain("搜索提取码、昵称、手机号或文件名");
  expect(html).toContain("提取码 / 下单时间");
  expect(html).toContain("resource-skeleton");
  expect(html).not.toContain("旧查询订单");
  expect(html).not.toContain("没有符合条件的订单");
});
it("keeps same-query records visible with refresh error feedback", () => {
  const html = render(CustomersPage);
  expect(html).toContain("刷新网络失败");
  expect(html).toContain("保留客户");
});
