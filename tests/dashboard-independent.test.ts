import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { expect, it, vi } from "vitest";
const state = vi.hoisted(() => ({
  summary: {
    data: null,
    loading: true,
    refreshing: false,
    error: "",
    reload: () => {},
  },
  resources: {} as Record<string, unknown>,
}));
vi.mock("../src/lib/api", async (original) => ({
  ...(await original<typeof import("../src/lib/api")>()),
  DEMO: false,
}));
vi.mock("../src/growth/client", () => ({
  useGrowthResource: () => state.summary,
}));
vi.mock("../src/lib/store", () => ({
  ADMIN: "/manage",
  useStore: () => ({
    data: {
      orders: [],
      batches: [
        {
          id: "batch",
          name: "独立批次",
          time: "12:00",
          orderIds: [],
          status: "PLANNED",
        },
      ],
      buildings: [],
      exceptions: [],
    },
    resources: state.resources,
    refresh: () => {},
  }),
}));
import { Dashboard } from "../src/admin/dashboard";
const render = () =>
  renderToStaticMarkup(
    createElement(MemoryRouter, {}, createElement(Dashboard)),
  );
it("keeps heading and available batches visible while summary alone loads or fails", () => {
  let html = render();
  expect(html).toContain("下午好，管理员");
  expect(html).toContain("独立批次");
  expect(html).not.toContain("¥0.00");
  state.summary.loading = false;
  state.summary.error = "统计连接失败";
  html = render();
  expect(html).toContain("独立批次");
  expect(html).toContain("统计连接失败");
  expect(html).toContain("最近订单");
});
it("does not render empty recent orders while their first request is pending", () => {
  state.summary.loading = false;
  state.summary.error = "统计连接失败";
  state.resources = {
    orders: { loading: true, hasData: false, error: "", key: "orders" },
  };
  const html = render();
  expect(html).toContain("最近订单");
  expect(html).not.toContain("今天还没有订单");
});
