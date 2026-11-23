import { expect, test } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { createDemo } from "../../src/lib/demo";

let server: ViteDevServer;
let origin: string;

test.use({ serviceWorkers: "block" });

test.beforeAll(async () => {
  process.env.VITE_DEMO_MODE = "false";
  server = await createServer({
    cacheDir: `node_modules/.vite-notifications-${test.info().project.name}`,
    server: {
      host: "127.0.0.1",
      port: 5470 + test.info().parallelIndex,
      hmr: false,
      watch: null,
    },
  });
  await server.listen();
  const address = server.httpServer!.address();
  if (!address || typeof address === "string")
    throw new Error("Vite did not start");
  origin = `http://127.0.0.1:${address.port}`;
});

test.afterAll(async () => {
  await server?.close();
  delete process.env.VITE_DEMO_MODE;
});

test("notification bell, read order, five filters and exact exception link", async ({
  page,
}, info) => {
  const data = createDemo();
  const items = [
    {
      id: "exception-1",
      kind: "LOCATION_EXCEPTION",
      title: "定位例外待审批",
      context: "ZW982",
      createdAt: "2026-11-22T10:30:00Z",
      read: false,
      taskStatus: "PENDING",
      targetId: "requested-location",
    },
    {
      id: "schedule-1",
      kind: "BATCH_CHANGED",
      title: "配送计划已变更",
      context: "晚间配送计划",
      createdAt: "2026-11-22T09:30:00Z",
      read: false,
      taskStatus: null,
      targetId: "batch-1",
    },
    {
      id: "order-1",
      kind: "PAYMENT_PENDING",
      title: "订单待确认收款",
      context: "RC884",
      createdAt: "2026-11-22T08:30:00Z",
      read: true,
      taskStatus: "HANDLED",
      targetId: data.orders[0].id,
    },
  ];
  let unreadCount = 10;
  let unreadLookupUnavailable = false;
  const writes: string[] = [];
  const filters: string[] = [];
  const orderDetails: string[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/v1", "");
    let result: unknown = {};
    if (path === "/auth/me")
      result = {
        id: "staff-1",
        name: "验收员工",
        role: "ADMIN",
        capabilities: ["delivery.manage", "payment.manage"],
        emailVerified: true,
      };
    else if (path === "/customer/auth/me" || path === "/customer/session")
      result = { role: "CUSTOMER", authKind: "ANONYMOUS" };
    else if (path === "/admin/notifications/unread") {
      if (unreadLookupUnavailable) {
        await route.fulfill({ status: 503 });
        return;
      }
      result = { unreadCount };
    }
    else if (path === "/admin/notifications") {
      const filter = url.searchParams.get("filter") || "ALL";
      filters.push(filter);
      const visible = items.filter(
        (item) =>
          filter === "ALL" ||
          (filter === "UNREAD" && !item.read) ||
          (filter === "READ" && item.read) ||
          item.taskStatus === filter,
      );
      result = {
        items: [...visible].sort((a, b) => Number(a.read) - Number(b.read)),
        unreadCount,
        total: visible.length,
        page: 1,
        pageSize: 20,
      };
    } else if (path === "/admin/notifications/read-all") {
      writes.push("read-all");
      for (const item of items) item.read = true;
      unreadCount = 0;
      result = { unreadCount };
    } else if (path.endsWith("/read")) {
      const id = decodeURIComponent(path.split("/")[3]);
      const item = items.find((item) => item.id === id)!;
      writes.push(id);
      if (!item.read) unreadCount--;
      item.read = true;
      result = { unreadCount };
    } else if (path === "/admin/orders") result = { items: [] };
    else if (path === `/admin/orders/${data.orders[0].id}`) {
      orderDetails.push(path);
      result = data.orders[0];
    } else if (path === "/admin/exceptions")
      result = {
        items: [
          {
            id: "unrelated",
            type: "DELIVERY_EXCEPTION",
            status: "OPEN",
            reason: "另一条配送异常",
            createdAt: items[0].createdAt,
            version: 1,
          },
          {
            id: "requested-location",
            type: "LOCATION_EXCEPTION",
            status: "OPEN",
            reason: "设备无法定位，需要审批",
            orderId: data.orders[0].id,
            createdAt: items[0].createdAt,
            version: 1,
          },
        ],
      };
    else if (path === "/public/config" || path === "/admin/config")
      result = data.config;
    else if (path === "/public/map")
      result = { items: data.buildings, campuses: data.campuses };
    else if (path === "/public/slots" || path === "/admin/slots")
      result = { items: data.slots };
    await route.fulfill({
      json: { data: result, requestId: "notifications-e2e" },
    });
  });

  await page.goto(origin + "/manage/exceptions");
  const bell = page.getByRole("link", { name: /通知中心.*10 条未读/ });
  await expect(bell).toHaveText("9+");
  await bell.click();
  const row = page.locator(".notification-row");
  const unreadRow = row.first();
  const readRow = row.last();
  const mark = unreadRow.getByRole("button", { name: /定位例外待审批/ });
  const title = unreadRow.getByRole("heading", { name: "定位例外待审批" });
  const readAll = page.getByRole("button", { name: "一键已读" });
  const refresh = page.getByRole("button", { name: "刷新通知" });
  await expect(readAll).toBeEnabled();
  await expect(refresh).toHaveText("");
  await expect(title.locator("button")).toHaveCount(0);
  await expect(unreadRow).toContainText("定位例外待审批");
  await expect(readRow).toHaveAttribute("data-read", "true");
  const readBackground = await readRow.evaluate(
    (element) => getComputedStyle(element).backgroundColor,
  );
  await expect(unreadRow).not.toHaveCSS("background-color", readBackground);
  const titleWeight = (item: typeof row) =>
    item
      .locator("h2")
      .evaluate((element) => Number(getComputedStyle(element).fontWeight));
  expect(await titleWeight(unreadRow)).toBeGreaterThan(
    await titleWeight(readRow),
  );
  expect(
    await title.evaluate(
      (element) => getComputedStyle(element, "::before").content,
    ),
  ).toBe('""');
  expect(writes).toEqual([]);
  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth - innerWidth,
      ),
    ).toBeLessThanOrEqual(1);
    const rowWidths = await row.evaluateAll((elements) =>
      elements.map((element) => element.scrollWidth - element.clientWidth),
    );
    for (const overflow of rowWidths) expect(overflow).toBeLessThanOrEqual(1);
    const titleLines = await title.evaluate((element) => {
      const range = document.createRange();
      range.selectNodeContents(element);
      return range.getClientRects().length;
    });
    expect(titleLines).toBe(1);
    await expect(readAll).toHaveCSS("font-size", "15px");
    const labelOffset = await readAll.evaluate((element) => {
      const button = element.getBoundingClientRect();
      const range = document.createRange();
      range.selectNodeContents(element);
      const label = range.getBoundingClientRect();
      return {
        x: Math.abs(
          label.left + label.width / 2 - (button.left + button.width / 2),
        ),
        y: Math.abs(
          label.top + label.height / 2 - (button.top + button.height / 2),
        ),
      };
    });
    expect(labelOffset.x).toBeLessThanOrEqual(1);
    expect(labelOffset.y).toBeLessThanOrEqual(2);
    const allBox = (await readAll.boundingBox())!;
    const refreshBox = (await refresh.boundingBox())!;
    expect(allBox.x + allBox.width).toBeLessThan(refreshBox.x);
    expect(allBox.height).toBeGreaterThanOrEqual(44);
    expect(allBox.height).toBe(refreshBox.height);
    expect(refreshBox.height).toBeGreaterThanOrEqual(44);
    expect(refreshBox.width).toBe(refreshBox.height);
    expect((await bell.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    for (const control of await page
      .locator(
        ".notification-filters button, .notification-row button, .notification-row a",
      )
      .all())
      expect((await control.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    if (width === 390 || width === 1440)
      await page.screenshot({
        path: info.outputPath(`notifications-${width}.png`),
        fullPage: true,
      });
  }

  const schedule = row.filter({ hasText: "配送计划已变更" });
  await schedule.click({ position: { x: 16, y: 20 } });
  await expect(schedule).toHaveAttribute("data-read", "true");
  await expect(schedule).toHaveCSS("background-color", readBackground);
  await expect(schedule.getByRole("button")).toHaveCount(0);
  await expect(mark).toBeVisible();
  await expect(
    page.getByRole("link", { name: /通知中心.*9 条未读/ }),
  ).toHaveText("9");
  expect(writes).toEqual(["schedule-1"]);
  await expect(page).toHaveURL(/\/manage\/notifications$/);
  await page.getByRole("button", { name: "刷新通知" }).click();
  await expect(row.first()).toContainText("定位例外待审批");
  await page.getByRole("button", { name: "未读", exact: true }).click();
  await expect(row).toHaveCount(1);
  await page.getByRole("button", { name: "已读", exact: true }).click();
  await expect(row).toHaveCount(2);
  await page.getByRole("button", { name: "已处理", exact: true }).click();
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("订单待确认收款");
  await page.getByRole("button", { name: "未处理", exact: true }).click();
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("定位例外待审批");
  expect(filters).toEqual(
    expect.arrayContaining(["ALL", "UNREAD", "READ", "PENDING", "HANDLED"]),
  );
  await row.getByRole("link", { name: "查看详情", exact: true }).click();
  await expect(page).toHaveURL(
    /\/manage\/exceptions\?exception=requested-location$/,
  );
  await expect(page.getByText("设备无法定位，需要审批")).toBeVisible();
  await expect(page.getByText("另一条配送异常")).toHaveCount(0);
  await expect.poll(() => writes).toEqual(["schedule-1", "exception-1"]);
  await expect(
    page.getByRole("link", { name: /通知中心.*8 条未读/ }),
  ).toHaveText("8");
  await page.getByRole("link", { name: /通知中心/ }).click();
  await page.getByRole("button", { name: "已处理", exact: true }).click();
  await row.getByRole("link", { name: "查看详情", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: `订单 ${data.orders[0].pickupCode}` }),
  ).toBeVisible();
  expect(orderDetails).toContain(`/admin/orders/${data.orders[0].id}`);
  expect(writes).toEqual(["schedule-1", "exception-1"]);
  await page.getByRole("link", { name: /通知中心/ }).click();
  unreadLookupUnavailable = true;
  await readAll.click();
  await expect(
    page.getByRole("link", { name: "通知中心", exact: true }),
  ).toHaveText("");
  await expect(readAll).toBeDisabled();
  expect(writes).toEqual(["schedule-1", "exception-1", "read-all"]);
  await page.getByRole("button", { name: "未读", exact: true }).click();
  await expect(page.getByText("没有未读通知")).toBeVisible();
  await page.getByRole("button", { name: "未处理", exact: true }).click();
  await expect(row).toHaveCount(1);
  await expect(row.first()).toHaveAttribute("data-read", "true");
  await expect(row.first()).toContainText("待处理");
});
