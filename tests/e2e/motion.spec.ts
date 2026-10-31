import { test, expect, type Page, type Route } from "@playwright/test";
let runtimeErrors: string[] = [];
test.beforeEach(async ({ page }) => {
  runtimeErrors = [];
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
});
test.afterEach(() => expect(runtimeErrors).toEqual([]));
async function mountWindows(page: Page) {
  await page.goto("/u/help");
  await page.getByRole("heading", { name: "有什么可以帮到你？" }).waitFor();
  const ui = await (await page.request.get("/src/components/ui.tsx")).text();
  const main = await (await page.request.get("/src/main.tsx")).text();
  const react = ui.match(/from\s+["']([^"']*\/react\.js[^"']*)["']/)?.[1];
  const dom = main.match(
    /from\s+["']([^"']*\/react-dom_client\.js[^"']*)["']/,
  )?.[1];
  expect(react).toBeTruthy();
  expect(dom).toBeTruthy();
  await page.evaluate(
    async ({ react, dom }) => {
      const RModule = await import(react!),
        DModule = await import(dom!);
      const R = RModule.default || RModule,
        D = DModule.default || DModule;
      const U = await import("/src/components/ui.tsx");
      const host = document.createElement("div");
      host.id = "motion-harness";
      document.body.prepend(host);
      function Windows() {
        const [open, setOpen] = R.useState(false),
          [inner, setInner] = R.useState(false),
          [buttonBusy, setButtonBusy] = R.useState(false);
        (window as any).motionSetBusy = setButtonBusy;
        (window as any).motionSetOpen = setOpen;
        return R.createElement(
          R.Fragment,
          null,
          R.createElement(
            "button",
            { id: "motion-open", onClick: () => setOpen(true) },
            "打开测试窗口",
          ),
          R.createElement(
            "div",
            { id: "motion-width-box", style: { width: "min(600px,80vw)" } },
            R.createElement(
              U.Button,
              { className: "full", busy: buttonBusy },
              "保存设置",
            ),
          ),
          open &&
            R.createElement(
              U.Modal,
              { title: "窗口生命周期验收", onClose: () => setOpen(false) },
              R.createElement(
                "button",
                { id: "motion-inner", onClick: () => setInner(true) },
                "打开内层",
              ),
              R.createElement("button", null, "末尾操作"),
              inner &&
                R.createElement(
                  U.Modal,
                  { title: "内层窗口验收", onClose: () => setInner(false) },
                  R.createElement("button", null, "内层操作"),
                ),
            ),
        );
      }
      D.createRoot(host).render(R.createElement(Windows));
    },
    { react, dom },
  );
}
test("conditional modal exit stays inert and reopening does not lose its layer or scroll lock", async ({
  page,
}) => {
  await mountWindows(page);
  await page.locator("#motion-open").click();
  const dialog = page.getByRole("dialog", {
    name: "窗口生命周期验收",
    exact: true,
  });
  await expect(dialog).toBeVisible();
  await expect(page.locator("#root")).toHaveAttribute("inert", "");
  await dialog.getByRole("button", { name: "关闭", exact: true }).click();
  await expect(
    page.locator('.modal-backdrop[data-state="closing"]'),
  ).toHaveCount(1);
  await page.evaluate(() => (window as any).motionSetOpen(true));
  await expect(dialog).toBeVisible();
  await expect(page.locator(".modal-backdrop")).toHaveCount(1);
  await expect(page.locator("#root")).toHaveAttribute("inert", "");
  await dialog.getByRole("button", { name: "关闭", exact: true }).click();
  await expect(page.locator(".modal-backdrop")).toHaveCount(0);
  await expect(page.locator("#root")).not.toHaveAttribute("inert", "");
  await expect(page.locator("#motion-open")).toBeFocused();
});
test("nested modal closes only the top layer and reduced motion during exit unlocks reliably", async ({
  page,
}) => {
  await mountWindows(page);
  await page.locator("#motion-open").click();
  await page.getByRole("button", { name: "打开内层", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "内层窗口验收" }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "内层窗口验收" })).toHaveCount(
    0,
  );
  await expect(
    page.getByRole("dialog", { name: "窗口生命周期验收" }),
  ).toBeVisible();
  await expect(page.locator("#root")).toHaveAttribute("inert", "");
  await expect(page.locator("#motion-inner")).toBeFocused();
  await page.keyboard.press("Escape");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(page.locator(".modal-backdrop")).toHaveCount(0);
  await expect(page.locator("#root")).not.toHaveAttribute("inert", "");
  expect(
    await page.evaluate(() => document.documentElement.style.overflowY),
  ).toBe("");
});
let fixture: any;
test.beforeAll(async ({ browser }) => {
  const p = await browser.newPage();
  await p.goto(
    (process.env.MOTION_BASE_URL || "http://127.0.0.1:5177") + "/u/",
  );
  fixture = await p.evaluate(async () =>
    (await import("/src/lib/demo.ts")).createDemo(),
  );
  await p.close();
});
async function realApi(
  page: Page,
  overrides: Record<string, (route: Route) => Promise<void>> = {},
) {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    if (overrides[path]) return overrides[path](route);
    if (path === "/auth/me")
      return route.fulfill({
        status: 401,
        json: { error: { code: "UNAUTHENTICATED", message: "登录已过期" } },
      });
    const responses: Record<string, unknown> = {
      "/customer/auth/me": {
        role: "CUSTOMER",
        authKind: "ANONYMOUS",
        capabilities: [],
      },
      "/customer/session": {},
      "/public/config": fixture.config,
      "/public/map": { items: fixture.buildings },
      "/public/slots": { items: fixture.slots },
      "/customer/orders": { items: fixture.orders },
    };
    const order = fixture.orders.find((o: any) => path === `/orders/${o.id}`);
    if (order) return route.fulfill({ json: { data: order } });
    if (path in responses)
      return route.fulfill({ json: { data: responses[path] } });
    return route.fulfill({
      status: 501,
      json: {
        error: { code: "TEST_UNSUPPORTED", message: "验收未提供此接口" },
      },
    });
  });
}
test("slow first order load preserves title and search, then refresh failure retains authorized content", async ({
  page,
}) => {
  let release!: () => void;
  const hold = new Promise<void>((r) => (release = r));
  let detailReads = 0;
  const id = fixture.orders[0].id;
  await realApi(page, {
    "/customer/orders": async (route) => {
      await hold;
      await route.fulfill({ json: { data: { items: fixture.orders } } });
    },
    [`/orders/${id}`]: async (route) => {
      detailReads++;
      if (detailReads === 1)
        return route.fulfill({ json: { data: fixture.orders[0] } });
      await new Promise((r) => setTimeout(r, 400));
      return route.fulfill({
        status: 503,
        json: { error: { code: "UNAVAILABLE", message: "网络暂时不可用" } },
      });
    },
  });
  await page.goto("http://127.0.0.1:5175/u/orders");
  await expect(
    page.getByRole("heading", { name: "我的订单", exact: true }),
  ).toBeVisible();
  await expect(page.getByPlaceholder("搜索提取码或文件名")).toBeVisible();
  await expect(
    page.locator(".customer-orders .resource-skeleton"),
  ).toBeVisible();
  await page.screenshot({
    path: `output/motion-qa/order-initial-${test.info().project.name}.png`,
    fullPage: true,
  });
  release();
  await page.locator(`.customer-orders a[href="/u/orders/${id}"]`).click();
  await expect(
    page.getByText(fixture.orders[0].pickupCode, { exact: true }).first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "刷新状态", exact: true }).click();
  await expect(page.getByText(/更新失败，当前为上次数据/)).toBeVisible();
  await expect(
    page.getByText(fixture.orders[0].pickupCode, { exact: true }).first(),
  ).toBeVisible();
  await expect(page.locator(".resource-skeleton")).toHaveCount(0);
});
test("public help remains usable when configuration fails and never asks for private orders", async ({
  page,
}) => {
  const paths: string[] = [];
  await realApi(page, {
    "/public/config": async (route) => {
      paths.push("/public/config");
      return route.fulfill({
        status: 503,
        json: { error: { code: "UNAVAILABLE", message: "配置服务暂不可用" } },
      });
    },
    "/customer/orders": async (route) => {
      paths.push("/customer/orders");
      return route.fulfill({
        status: 500,
        json: { error: { message: "unrelated" } },
      });
    },
  });
  await page.goto("http://127.0.0.1:5175/u/help");
  await expect(
    page.getByRole("heading", { name: "有什么可以帮到你？" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "哪些文件可以打印？" }),
  ).toBeVisible();
  await expect(page.getByText(/配置服务暂不可用/)).toBeVisible();
  expect(paths).not.toContain("/customer/orders");
});
test("leaving a pending upload retains a retryable item and old response cannot revive it", async ({
  page,
}) => {
  const keys: string[] = [];
  let release!: () => void;
  const hold = new Promise<void>((r) => (release = r));
  let calls = 0;
  await realApi(page, {
    "/assets": async (route) => {
      calls++;
      keys.push(route.request().headers()["idempotency-key"]);
      if (calls === 1) await hold;
      try {
        await route.fulfill({
          json: {
            data: { assetId: "image-" + calls, status: "READY", pages: 1 },
          },
        });
      } catch {}
    },
  });
  await page.goto("http://127.0.0.1:5175/u/new");
  await expect(
    page.getByRole("button", { name: "选择文件", exact: true }),
  ).not.toHaveAttribute("aria-disabled", "true");
  await page
    .locator("input[type=file]")
    .setInputFiles("public/assets/icon-512.png");
  await expect(page.getByText("icon-512.png", { exact: true })).toBeVisible();
  await expect(
    page.locator(".file-progress-visual .task-progress-ring"),
  ).toBeVisible();
  await page.screenshot({
    path: `output/motion-qa/upload-pending-${test.info().project.name}.png`,
    fullPage: true,
  });
  await page.getByRole("link", { name: "首页", exact: true }).first().click();
  await expect(page).toHaveURL(/\/u\/$/);
  await expect(
    page.getByRole("heading", { name: "你需要打印什么？" }),
  ).toBeVisible();
  await page.goBack();
  await expect(page.getByText(/页面已离开，上传结果待核实/)).toBeVisible();
  release();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "重试", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "选择图片打印纸张" }),
  ).toBeVisible();
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
});
test("rapid admin query A to B to A replaces obsolete flight and settles without waiting for polling", async ({
  page,
}) => {
  const flights: Route[] = [];
  await realApi(page, {
    "/auth/me": async (route) =>
      route.fulfill({
        json: {
          data: {
            id: "staff-1",
            role: "ADMIN",
            name: "管理员",
            username: "admin",
            capabilities: ["*"],
          },
        },
      }),
    "/admin/config": async (route) =>
      route.fulfill({ json: { data: fixture.config } }),
    "/admin/orders": async (route) => {
      flights.push(route);
    },
  });
  await page.goto("http://127.0.0.1:5175/manage/orders?status=PENDING_PRINT");
  await expect(
    page.getByRole("heading", { name: "订单管理", exact: true }),
  ).toBeVisible();
  await expect.poll(() => flights.length).toBe(1);
  await page.getByLabel("订单状态", { exact: true }).selectOption("DELIVERED");
  await expect.poll(() => flights.length).toBe(2);
  await page
    .getByLabel("订单状态", { exact: true })
    .selectOption("PENDING_PRINT");
  await expect.poll(() => flights.length).toBe(3);
  await flights[2].fulfill({
    json: {
      data: {
        items: fixture.orders.filter((o: any) => o.status === "PENDING_PRINT"),
      },
    },
  });
  await expect(page.locator(".admin-content .resource-skeleton")).toHaveCount(
    0,
  );
  await expect(
    page.locator(".admin-content tbody").getByText("R8F2A", { exact: true }),
  ).toBeVisible();
  try {
    await flights[1].fulfill({
      json: {
        data: {
          items: fixture.orders.filter((o: any) => o.status === "DELIVERED"),
        },
      },
    });
    await flights[0].fulfill({ json: { data: { items: [] } } });
  } catch {}
  await expect(
    page.locator(".admin-content tbody").getByText("R8F2A", { exact: true }),
  ).toBeVisible();
});
test("identity invalidation disables upload until current configuration is obtained", async ({
  page,
}) => {
  let configs = 0,
    release!: () => void;
  const pending = new Promise<void>((r) => (release = r));
  await realApi(page, {
    "/public/config": async (route) => {
      configs++;
      if (configs > 1) await pending;
      try {
        await route.fulfill({ json: { data: fixture.config } });
      } catch {}
    },
  });
  await page.goto("http://127.0.0.1:5175/u/new");
  await expect(
    page.getByRole("button", { name: "选择文件", exact: true }),
  ).toBeEnabled();
  await page.evaluate(async () =>
    (await import("/src/growth/client.ts")).advanceIdentity(),
  );
  await expect(
    page.getByRole("button", { name: "选择文件", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByText("正在读取服务配置，暂时不能上传或提交"),
  ).toBeVisible();
  release();
});

test("busy button keeps its current responsive width after viewport changes", async ({
  page,
}) => {
  await mountWindows(page);
  await page.setViewportSize({ width: 320, height: 900 });
  const button = page.locator("#motion-width-box button");
  const before = await button.evaluate(
    (el) => el.getBoundingClientRect().width,
  );
  await page.evaluate(() => (window as any).motionSetBusy(true));
  await expect(button).toBeDisabled();
  const after = await button.evaluate((el) => el.getBoundingClientRect().width);
  expect(Math.abs(after - before)).toBeLessThan(1);
  await page.setViewportSize({ width: 280, height: 900 });
  expect(
    await button.evaluate((el) => el.getBoundingClientRect().width),
  ).toBeLessThanOrEqual(224);
});
