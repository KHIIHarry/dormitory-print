import { expect, test, type Page } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { createDemo } from "../../src/lib/demo";

let server: ViteDevServer;
let baseURL: string;
test.use({ serviceWorkers: "block" });
test.beforeAll(async () => {
  process.env.VITE_DEMO_MODE = "false";
  server = await createServer({
    cacheDir: `node_modules/.vite-guest-admin-${test.info().project.name}`,
    server: {
      host: "127.0.0.1",
      port: 5325 + test.info().parallelIndex,
      hmr: false,
      watch: null,
    },
  });
  await server.listen();
  const address = server.httpServer!.address();
  if (!address || typeof address === "string")
    throw new Error("Vite failed to start");
  baseURL = `http://127.0.0.1:${address.port}`;
});
test.afterAll(async () => {
  await server?.close();
  delete process.env.VITE_DEMO_MODE;
});

type Surface =
  | "dashboard"
  | "orders"
  | "order-detail"
  | "print"
  | "print-detail"
  | "binding"
  | "after-sales"
  | "after-sale-detail"
  | "access";
async function mount(page: Page, surface: Surface, restricted = false) {
  const data = createDemo();
  data.orders = data.orders.slice(0, 2).map((order, index) => ({
    ...order,
    createdAt: "2026-11-17T06:26:00Z",
    accountOwned: index === 0,
    identificationProgress:
      index === 0
        ? { enabled: true, completed: 0, total: 2, status: "PENDING" }
        : null,
    adminPickup: {
      pickupCode: order.pickupCode,
      placedWithAccount: index === 0,
      customerDetailId: index === 0 && !restricted ? "customer-1" : null,
    },
  }));
  const task = {
    id: "task-1",
    orderId: data.orders[0].id,
    pickupCode: data.orders[0].pickupCode,
    adminPickup: data.orders[0].adminPickup,
    fileId: data.orders[0].files[0].id,
    fileName: "课堂资料.pdf",
    kind: "DOCUMENT",
    bindingType: "CORNER",
    status: "PENDING",
    active: true,
    artifactId: "artifact-1",
    artifactSha256: "a".repeat(64),
    pages: 4,
    sheetCount: 2,
    copies: 1,
    sides: "DUPLEX",
    flip: "LONG_EDGE",
    colorMode: "BW",
    pageRange: "1-4",
    openAttemptId: null,
    lastAttempt: null,
    reprints: [],
    orderStatus: "PENDING_PRINT",
    orderVersion: 1,
    sheetCatalog: [],
    printStatus: "COMPLETED",
    version: 1,
  };
  const guest = {
    ...task,
    id: "task-2",
    orderId: data.orders[1].id,
    pickupCode: data.orders[1].pickupCode,
    adminPickup: data.orders[1].adminPickup,
  };
  let owned = false;
  let view = {
    status: "ACTIVE",
    version: 1,
    openedAt: "2026-11-17T00:00:00Z",
    expiresAt: "2026-12-01T00:00:00Z",
    serverNow: "2026-11-17T00:00:00Z",
    canOpen: true,
  };
  const requests: { path: string; body: unknown }[] = [];
  const customer = {
    accountId: "customer-1",
    email: `${"long-address-".repeat(9)}@example.com`,
    displayName: "同学",
    createdAt: "2026-10-01T00:00:00Z",
    verified: true,
  };
  const item = {
    id: "case-1",
    orderId: task.orderId,
    adminPickup: task.adminPickup,
    orderCode: task.pickupCode,
    version: 1,
    status: "REQUESTED",
    source: "CUSTOMER",
    responsibility: "MERCHANT",
    solution: "REFUND",
    reason: "错印",
    suggestedCents: 100,
    approvedCents: null,
    adjustmentBasis: "",
    data: {},
    createdAt: "2026-11-17T00:00:00Z",
  };
  await page.route("**/api/v1/**", (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    const body = route.request().postDataJSON();
    requests.push({ path, body });
    let result: unknown = {};
    if (path === "/auth/me")
      result = {
        id: "admin",
        role: "ADMIN",
        capabilities: restricted
          ? [
              "orders.view",
              "production.manage",
              "payment.manage",
              "order.access-link.manage",
            ]
          : ["*"],
        emailVerified: true,
      };
    else if (path.endsWith("/guest-access/open"))
      result = { orderId: "order-2", accessToken: "x".repeat(40) };
    else if (path.endsWith("/guest-access/close")) {
      view = {
        ...view,
        status: "CLOSED",
        version: view.version + 1,
        canOpen: false,
      };
      result = view;
    } else if (path.endsWith("/guest-access/reset")) {
      view = {
        ...view,
        status: "ACTIVE",
        version: view.version + 1,
        expiresAt: new Date(
          Date.parse(view.serverNow) + Number(body.days) * 86400000,
        ).toISOString(),
        canOpen: true,
      };
      result = view;
    } else if (path.endsWith("/guest-owner")) {
      owned = true;
      result = { orderId: "order-2", ownerAccountId: "customer-1" };
    } else if (path.endsWith("/guest-access")) result = view;
    else if (path === "/admin/orders/order-2")
      result = {
        ...data.orders[1],
        accountOwned: owned,
        customerSummary: owned ? customer : null,
      };
    else if (path.startsWith("/admin/orders/")) result = data.orders[0];
    else if (path === "/admin/print-tasks")
      result = {
        tasks:
          surface === "print"
            ? [
                {
                  ...task,
                  id: "identification-1",
                  kind: "IDENTIFICATION",
                  fileId: "",
                  pages: 2,
                  status: "COMPLETED",
                },
                task,
                guest,
              ]
            : [task, guest],
        counts: {},
        nextTaskId: task.id,
      };
    else if (path.startsWith("/admin/print-tasks/")) result = task;
    else if (path === "/admin/binding-tasks") result = { tasks: [task, guest] };
    else if (path === "/admin/after-sales")
      result = {
        items: [
          item,
          {
            ...item,
            id: "case-2",
            orderId: guest.orderId,
            orderCode: guest.pickupCode,
            adminPickup: guest.adminPickup,
          },
        ],
        nextCursor: null,
      };
    else if (path.startsWith("/admin/after-sales/")) result = item;
    return route.fulfill({ json: { ok: true, data: result } });
  });
  const main = await (await page.request.get(`${baseURL}/src/main.tsx`)).text();
  const react = main.match(/from "([^"]*\/react\.js\?[^"]*)"/)![1];
  const dom = main.match(/from "([^"]*\/react-dom_client\.js\?[^"]*)"/)![1];
  const source = await (
    await page.request.get(`${baseURL}/src/admin/orders.tsx`)
  ).text();
  const router = source.match(
    /from "([^"]*\/react-router-dom\.js\?[^"]*)"/,
  )![1];
  await page.route("**/src/lib/store.tsx*", (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: `export const ADMIN='/manage',DEMO=false;const data=${JSON.stringify(data)};export const useStore=()=>({data,resources:{},refresh:async()=>{},toast:()=>{},act:async()=>true,updateOrder:()=>{}});`,
    }),
  );
  await page.route("**/src/growth/identity.tsx*", (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: `export const useIdentity=()=>({generation:0,staff:{id:'admin',role:'ADMIN',capabilities:${JSON.stringify(restricted ? ["orders.view", "production.manage", "payment.manage", "order.access-link.manage"] : ["*"])}}});`,
    }),
  );
  await page.route("**/admin-guest-fixture", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><div id="root"></div><script type="module">import R from '/@react-refresh';R.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;</script><script type="module">
import React from '${react}';import ReactDOM from '${dom}';import {MemoryRouter,Routes,Route} from '${router}';
import {Dashboard} from '/src/admin/dashboard.tsx';import {AdminOrders,AdminOrderDetail} from '/src/admin/orders.tsx';import {PrintCenter,PrintTask,BindingCenter} from '/src/admin/production.tsx';import {AdminAfterSalesPage,AdminAfterSaleDetail} from '/src/v1/admin.tsx';import {AdminOrderAccess} from '/src/admin/order-access.tsx';import '/src/styles.css';
const surface=${JSON.stringify(surface)};const components={'dashboard':Dashboard,'orders':AdminOrders,'order-detail':AdminOrderDetail,'print':PrintCenter,'print-detail':PrintTask,'binding':BindingCenter,'after-sales':AdminAfterSalesPage,'after-sale-detail':AdminAfterSaleDetail};
const component=surface==='access'?React.createElement(AdminOrderAccess,{order:${JSON.stringify(data.orders[1])}}):React.createElement(components[surface]);
ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(MemoryRouter,{initialEntries:[surface==='order-detail'?'/order-1':surface==='print-detail'?'/task-1':surface==='after-sale-detail'?'/case-1':'/']},React.createElement('main',{className:'admin-content'},React.createElement(Routes,null,React.createElement(Route,{path:'/:id?',element:component})))));
</script></html>`,
    }),
  );
  await page.goto(`${baseURL}/admin-guest-fixture`);
  return requests;
}

for (const surface of [
  "dashboard",
  "orders",
  "order-detail",
  "print",
  "print-detail",
  "binding",
  "after-sales",
  "after-sale-detail",
] as Surface[]) {
  test(`${surface} keeps registered and guest pickup targets aligned`, async ({
    page,
  }, info) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await mount(page, surface);
    await expect(page.locator(".order-pickup-code").first()).toBeVisible();
    if (surface === "order-detail")
      await expect(
        page.locator(".admin-detail-banner .identification-progress"),
      ).toHaveText("订单识别页面进度 0/2");
    if (surface === "print") {
      await expect(
        page
          .locator(".print-order-card")
          .first()
          .locator(".identification-progress"),
      ).toHaveText("识别页 2/2");
      await expect(
        page
          .locator(".print-order-card")
          .nth(1)
          .locator(".identification-progress"),
      ).toHaveCount(0);
    }
    for (const width of [320, 375, 390, 430, 768, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      const metrics = await page
        .locator(".order-pickup-code")
        .evaluateAll((nodes) =>
          nodes.map((node) => {
            const origin = node
              .querySelector(".order-pickup-origin")!
              .getBoundingClientRect();
            const code = node
              .querySelector(".order-pickup-value")!
              .getBoundingClientRect();
            const box = node.getBoundingClientRect();
            return {
              originWidth: origin.width,
              originHeight: origin.height,
              codeHeight: code.height,
              offset: code.left - box.left,
            };
          }),
        );
      for (const metric of metrics) {
        expect(metric.originWidth).toBe(44);
        expect(metric.originHeight).toBe(44);
        expect(metric.codeHeight).toBeGreaterThanOrEqual(44);
        expect(metric.offset).toBe(48);
      }
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth - innerWidth,
        ),
      ).toBeLessThanOrEqual(1);
      if (surface === "print") {
        const controls = await page
          .locator(".filter-panel input,.filter-panel select")
          .evaluateAll((nodes) =>
            nodes.map((node) => ({
              height: node.getBoundingClientRect().height,
              fontSize: parseFloat(getComputedStyle(node).fontSize),
            })),
          );
        for (const control of controls) {
          expect(control.height).toBeGreaterThanOrEqual(44);
          if (width <= 430) expect(control.fontSize).toBeGreaterThanOrEqual(16);
        }
      }
      if (width === 390 || width === 1440)
        await page.screenshot({
          path: info.outputPath(`${surface}-${width}.png`),
          fullPage: true,
        });
    }
    expect(errors).toEqual([]);
  });
}
test("restricted staff sees an identification icon with no customer request", async ({
  page,
}) => {
  const requests = await mount(page, "orders", true);
  await expect(
    page.locator('.order-pickup-origin[role="img"]').first(),
  ).toBeVisible();
  await expect(page.locator('a[href*="/customers/"]')).toHaveCount(0);
  expect(
    requests.filter((request) => request.path.startsWith("/admin/customers/")),
  ).toHaveLength(0);
});
test("guest link closes, resets and switches to customer facts after association", async ({
  page,
}, info) => {
  const requests = await mount(page, "access");
  await expect(page.getByText("开放中", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "关闭链接", exact: true }).click();
  await page.getByLabel("操作原因").fill("客户要求关闭");
  await page.getByRole("button", { name: "确认操作", exact: true }).click();
  await expect(page.getByText("已关闭", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "重置链接", exact: true }).click();
  await expect(page.getByLabel("有效天数（1–14 天）")).toHaveValue("14");
  await page.getByLabel("有效天数（1–14 天）").fill("1");
  await page.getByLabel("操作原因").fill("核实后重置");
  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth - innerWidth,
      ),
    ).toBeLessThanOrEqual(1);
    const input = page.getByLabel("有效天数（1–14 天）");
    expect(
      await input.evaluate((node) => node.getBoundingClientRect().height),
    ).toBeGreaterThanOrEqual(44);
    if (width <= 430)
      expect(
        await input.evaluate((node) =>
          parseFloat(getComputedStyle(node).fontSize),
        ),
      ).toBeGreaterThanOrEqual(16);
    if (width === 390 || width === 1440)
      await page.screenshot({
        path: info.outputPath(`reset-${width}.png`),
        fullPage: true,
      });
  }
  await page.getByRole("button", { name: "确认操作", exact: true }).click();
  await expect(page.getByText("开放中", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "关联到客户账号", exact: true })
    .click();
  await page.getByLabel("目标客户账号 ID").fill("customer-1");
  await page.getByLabel("已核实订单与目标已验证账号，确认关联此单").check();
  await page.getByLabel("操作原因").fill("微信核实原订单与邮箱");
  await page.getByRole("button", { name: "确认操作", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "客户信息", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "订单专属链接", exact: true }),
  ).toHaveCount(0);
  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth - innerWidth,
      ),
    ).toBeLessThanOrEqual(1);
    if (width === 390 || width === 1440)
      await page.screenshot({
        path: info.outputPath(`customer-facts-${width}.png`),
        fullPage: true,
      });
  }
  expect(
    requests.find((request) => request.path.endsWith("/guest-access/reset"))
      ?.body,
  ).toEqual({ expectedVersion: 2, reason: "核实后重置", days: 1 });
  expect(
    requests.find((request) => request.path.endsWith("/guest-owner"))?.body,
  ).toEqual({
    expectedVersion: 3,
    reason: "微信核实原订单与邮箱",
    accountId: "customer-1",
  });
});

test("opening fetches the private link only after an explicit click", async ({
  page,
}) => {
  const requests = await mount(page, "access");
  await expect(
    page.getByRole("button", { name: "打开游客订单页" }),
  ).toBeEnabled();
  expect(
    requests.filter((request) => request.path.endsWith("/guest-access/open")),
  ).toHaveLength(0);
  await page.route("**/u/order-link?orderId=order-2", (route) =>
    route.fulfill({ contentType: "text/html", body: "<p>客户订单入口</p>" }),
  );
  await page.getByRole("button", { name: "打开游客订单页" }).click();
  await expect(page).toHaveURL(/\/u\/order-link\?orderId=order-2#token=x{40}$/);
  expect(
    requests.filter((request) => request.path.endsWith("/guest-access/open")),
  ).toHaveLength(1);
});

test("a concurrent access version refresh keeps the operator's reason", async ({
  page,
}) => {
  await mount(page, "access");
  await page.getByRole("button", { name: "重置链接", exact: true }).click();
  await page.getByLabel("操作原因").fill("已核实原始订单");
  await page.route("**/api/v1/admin/orders/order-2/guest-access", (route) =>
    route.fulfill({
      json: {
        ok: true,
        data: {
          status: "ACTIVE",
          version: 9,
          openedAt: "2026-11-17T00:00:00Z",
          expiresAt: "2026-12-01T00:00:00Z",
          serverNow: "2026-11-17T00:00:00Z",
          canOpen: true,
        },
      },
    }),
  );
  await page.getByRole("button", { name: "确认操作", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("访问状态已更新");
  await expect(page.getByLabel("操作原因")).toHaveValue("已核实原始订单");
});

test("an unknown reset result is reconciled before another mutation", async ({
  page,
}) => {
  await mount(page, "access");
  await page.getByRole("button", { name: "重置链接", exact: true }).click();
  await page.getByLabel("操作原因").fill("核实后重置");
  let attempted = 0;
  await page.route(
    "**/api/v1/admin/orders/order-2/guest-access/reset",
    async (route) => {
      attempted++;
      await page.route("**/api/v1/admin/orders/order-2/guest-access", (read) =>
        read.fulfill({
          json: {
            ok: true,
            data: {
              status: "ACTIVE",
              version: 2,
              openedAt: "2026-11-17T00:00:00Z",
              expiresAt: "2026-12-01T00:00:00Z",
              serverNow: "2026-11-17T00:00:00Z",
              canOpen: true,
            },
          },
        }),
      );
      return route.abort("failed");
    },
  );
  await page.getByRole("button", { name: "确认操作", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByRole("button", { name: "确认操作", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "上次操作后访问状态已更新",
  );
  await page.getByRole("button", { name: "确认操作", exact: true }).click();
  expect(attempted).toBe(1);
  await expect(page.getByLabel("操作原因")).toHaveValue("核实后重置");
});
