import { spawn, type ChildProcess } from "node:child_process";
import { mkdirSync, rmSync, rmdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { test, expect, type Page } from "@playwright/test";

// The bundled headless shell is not installed in this workspace; use the
// locally installed Chrome channel like the existing real-mode e2e suite.
test.use({ channel: "chrome", trace: "off" });

// The demo checks use the real Vite dev server and the in-memory demo identity
// store. They do not exercise the backend API and are not backend acceptance.
test.describe("unified DormPrint app", () => {
  test("serves one root manifest identity and identical legacy aliases", async ({
    page,
  }) => {
    for (const path of ["/u/", "/manage/", "/courier/"]) {
      await page.goto(path);
      await expect(page.locator('link[rel="manifest"]')).toHaveAttribute(
        "href",
        "/manifest.webmanifest",
      );
      const manifest = await page.evaluate(async () =>
        (await fetch("/manifest.webmanifest")).json(),
      );
      expect(manifest).toMatchObject({
        id: "/",
        start_url: "/u/",
        scope: "/",
        short_name: "DormPrint",
      });
    }

    for (const path of [
      "/u/manifest.webmanifest",
      "/manage/manifest.webmanifest",
      "/courier/manifest.webmanifest",
    ]) {
      const response = await page.request.get(path);
      expect(response.ok()).toBe(true);
      expect(await response.json()).toMatchObject({
        id: "/",
        start_url: "/u/",
        scope: "/",
      });
    }
  });

  test("ordinary personal page has no staff entry or standalone staff login", async ({
    page,
  }) => {
    await page.goto("/u/account");
    await expect(page.getByText("登录已验证的账号")).toBeVisible();
    await expect(page.locator(".staff-portal")).toHaveCount(0);
    await expect(page.getByText("员工入口")).toHaveCount(0);
    await expect(page.getByText("员工登录")).toHaveCount(0);
    await expect(page.getByText("管理端", { exact: true })).toHaveCount(0);
    await expect(page.getByText("配送端", { exact: true })).toHaveCount(0);

    // A deep link to a protected workspace goes to the one customer login and
    // carries a safe return target instead of opening an employee login page.
    await page.goto("/manage/orders?tab=1");
    await expect(page).toHaveURL(
      (url) =>
        url.pathname === "/u/account/login" &&
        url.searchParams.get("returnTo") === "/manage/orders?tab=1",
    );
    await expect(
      page.getByRole("heading", { name: "欢迎回来" }),
    ).toBeVisible();
    await expect(page.getByText("员工登录")).toHaveCount(0);

    await page.goto("/courier/orders/ORD-1");
    await expect(page).toHaveURL(
      (url) =>
        url.pathname === "/u/account/login" &&
        url.searchParams.get("returnTo") === "/courier/orders/ORD-1",
    );
  });

  test("legacy staff login URLs redirect to the shared customer login", async ({
    page,
  }) => {
    await page.goto("/manage/login");
    await expect(page).toHaveURL(
      (url) =>
        url.pathname === "/u/account/login" &&
        url.searchParams.get("returnTo") === "/manage/",
    );
    await expect(
      page.getByRole("heading", { name: "欢迎回来" }),
    ).toBeVisible();

    await page.goto("/courier/login");
    await expect(page).toHaveURL(
      (url) =>
        url.pathname === "/u/account/login" &&
        url.searchParams.get("returnTo") === "/courier/",
    );
  });

  test("legacy staff credential URLs redirect to base customer account flows", async ({
    page,
  }) => {
    const flows = [
      ["/manage/account/setup", "/u/account/register", "建立你的打印账号"],
      ["/manage/account/forgot", "/u/account/forgot", "找回密码"],
      ["/manage/account/reset", "/u/account/reset", "设置新密码"],
      ["/manage/account/verify", "/u/account/verify", "确认邮箱"],
      ["/courier/account/setup", "/u/account/register", "建立你的打印账号"],
      ["/courier/account/forgot", "/u/account/forgot", "找回密码"],
      ["/courier/account/reset", "/u/account/reset", "设置新密码"],
      ["/courier/account/verify", "/u/account/verify", "确认邮箱"],
    ] as const;

    for (const [legacy, target, heading] of flows) {
      await page.goto(`${legacy}?email=staff%40example.test`);
      await expect(page).toHaveURL(
        (url) =>
          url.pathname === target &&
          url.searchParams.get("email") === "staff@example.test",
      );
      await expect(
        page.getByRole("heading", { name: heading, exact: true }),
      ).toBeVisible();
      for (const staffTitle of [
        "员工首次设置密码",
        "找回员工密码",
        "重置员工密码",
        "验证员工邮箱",
      ]) {
        await expect(page.getByText(staffTitle, { exact: true })).toHaveCount(
          0,
        );
      }
    }
  });

  test("admin can enter the courier workspace and return without losing identity", async ({
    page,
  }) => {
    await page.goto("/preview");
    await page.getByRole("button", { name: "打开管理端" }).click();
    await expect(page).toHaveURL(/\/manage\/?$/);
    await expect(
      page.getByRole("link", { name: "返回用户端", exact: true }),
    ).toBeVisible();
    await expect(page.getByText("返回用户端", { exact: true })).toBeVisible();
    await expect(
      page.getByRole("link", { name: "配送端", exact: true }),
    ).toBeVisible();
    await expect(page.getByText("员工登录")).toHaveCount(0);

    await page.getByRole("link", { name: "配送端", exact: true }).click();
    await expect(page).toHaveURL(/\/courier\/$/);
    await expect(
      page.getByRole("link", { name: "管理端", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "返回用户端", exact: true }),
    ).toBeVisible();
    await expect(page.getByText("返回用户端", { exact: true })).toBeVisible();

    await page.getByRole("link", { name: "返回用户端", exact: true }).click();
    await expect(page).toHaveURL(/\/u\/account$/);
    const portal = page.locator(".staff-portal");
    await expect(portal).toContainText("管理员");
    await expect(
      portal.getByRole("link", { name: "进入管理端", exact: true }),
    ).toBeVisible();
    await expect(
      portal.getByRole("link", { name: "进入配送端", exact: true }),
    ).toBeVisible();
    await expect(portal.getByText("员工登录")).toHaveCount(0);

    await portal.getByRole("link", { name: "进入管理端", exact: true }).click();
    await expect(page).toHaveURL(/\/manage\/?$/);

    // Staff logout is explicit and clears the one customer session; it must not
    // leave a stale registered customer behind on the login screen.
    await page.getByRole("button", { name: "退出登录" }).click();
    await expect(page).toHaveURL(
      (url) =>
        url.pathname === "/u/account/login" &&
        url.searchParams.get("returnTo") === "/manage/",
    );
    await expect(page.getByText("员工登录")).toHaveCount(0);
  });

  test("courier gets a courier workspace only and cannot open admin", async ({
    page,
  }) => {
    await page.goto("/preview");
    await page.getByRole("button", { name: "打开配送端" }).click();
    await expect(page).toHaveURL(/\/courier\/$/);
    await expect(
      page.getByRole("link", { name: "返回用户端", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "管理端", exact: true }),
    ).toHaveCount(0);

    await page.getByRole("link", { name: "返回用户端", exact: true }).click();
    await expect(page).toHaveURL(/\/u\/account$/);
    const portal = page.locator(".staff-portal");
    await expect(portal).toContainText("配送员");
    await expect(
      portal.getByRole("link", { name: "进入配送端", exact: true }),
    ).toBeVisible();
    await expect(
      portal.getByRole("link", { name: "进入管理端", exact: true }),
    ).toHaveCount(0);

    // Client-side navigation keeps the demo identity, but StaffGuard must send
    // an insufficient role to the user account with a safe error, not to a
    // standalone employee login.
    await page.evaluate(() => {
      window.history.pushState({}, "", "/manage");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await expect(page).toHaveURL(/\/u\/account$/);
    await expect(
      page.getByText("当前账号没有访问该工作台的权限"),
    ).toBeVisible();
    await expect(page.getByText("员工登录")).toHaveCount(0);
  });

  test("removes only legacy DormPrint scoped workers in a real browser", async ({
    page,
  }) => {
    test.skip(
      test.info().project.name === "mobile",
      "one shared browser fixture for the desktop project",
    );
    const uWorker = resolve(process.cwd(), "public/u/sw.js");
    const otherWorker = resolve(process.cwd(), "public/other/sw.js");
    mkdirSync(dirname(uWorker), { recursive: true });
    mkdirSync(dirname(otherWorker), { recursive: true });
    const worker =
      "self.addEventListener('install', () => self.skipWaiting());";
    writeFileSync(uWorker, worker);
    writeFileSync(otherWorker, worker);
    try {
      await page.goto("/other/");
      await page.evaluate(async () => {
        await navigator.serviceWorker.register("/other/sw.js", {
          scope: "/other/",
        });
      });
      await page.evaluate(async () => {
        await navigator.serviceWorker.register("/u/sw.js", { scope: "/u/" });
      });
      await expect
        .poll(async () =>
          page.evaluate(async () =>
            (await navigator.serviceWorker.getRegistrations()).map(
              (registration) => new URL(registration.scope).pathname,
            ),
          ),
        )
        .toEqual(expect.arrayContaining(["/u/", "/other/"]));

      await page.reload();
      await expect
        .poll(
          async () =>
            page.evaluate(async () =>
              (await navigator.serviceWorker.getRegistrations()).map(
                (registration) => new URL(registration.scope).pathname,
              ),
            ),
          { timeout: 10_000 },
        )
        .not.toContain("/u/");
      expect(
        await page.evaluate(async () =>
          (await navigator.serviceWorker.getRegistrations()).map(
            (registration) => new URL(registration.scope).pathname,
          ),
        ),
      ).toContain("/other/");
    } finally {
      rmSync(uWorker, { force: true });
      rmSync(otherWorker, { force: true });
      try {
        rmdirSync(dirname(otherWorker));
      } catch {
        // The directory may predate this test or contain another fixture.
      }
    }
  });
});

/**
 * Frontend contract test for the one customer-base login. It runs the app in
 * REAL API mode against a dedicated Vite server and intercepts /api/v1 at the
 * network boundary. It is NOT backend acceptance: the forthcoming account
 * worker must still prove Staff.account_id binding in PostgreSQL.
 */
test.describe("one customer login (real API contract, not backend acceptance)", () => {
  // Two project workers each start a cold Vite server; leave room for the
  // first dependency optimization instead of failing the login form wait.
  test.describe.configure({ timeout: 90_000 });
  let base = "http://127.0.0.1:5202";
  let server: ChildProcess | null = null;
  let startedHere = false;

  test.beforeAll(async () => {
    const port = test.info().project.name === "mobile" ? 5203 : 5202;
    base = `http://127.0.0.1:${port}`;
    const reachable = async (url: string) => {
      try {
        const response = await fetch(url, {
          signal: AbortSignal.timeout(1500),
        });
        return response.ok || response.status < 500;
      } catch {
        return false;
      }
    };
    if (await reachable(`${base}/u/`)) return;
    server = spawn(
      process.platform === "win32" ? "npx.cmd" : "npx",
      ["vite", "--port", String(port), "--strictPort", "--host", "127.0.0.1"],
      {
        cwd: process.cwd(),
        env: { ...process.env, VITE_DEMO_MODE: "false" },
        stdio: "ignore",
      },
    );
    startedHere = true;
    for (let attempt = 0; attempt < 80; attempt++) {
      if (await reachable(`${base}/u/`)) return;
      await new Promise((resolveStart) => setTimeout(resolveStart, 250));
    }
    throw new Error(`Vite real-mode server did not start on ${base}`);
  });

  test.afterAll(() => {
    if (startedHere) server?.kill("SIGTERM");
  });

  test("login refreshes the effective staff session and exposes only permitted workspaces", async ({
    page,
  }: {
    page: Page;
  }) => {
    let authed = false;
    const customerSession = {
      role: "CUSTOMER",
      authKind: "REGISTERED",
      account: {
        id: "ACC-1",
        emailMasked: "t***@example.com",
        displayName: "验收同学",
        verified: true,
        status: "ACTIVE",
        version: 2,
        notificationPreferences: {
          orderCreated: false,
          orderStatusUpdated: false,
          referralReward: false,
        },
      },
      capabilities: [],
    };
    const staffSession = {
      accountId: "ACC-1",
      id: "STAFF-1",
      role: "ADMIN",
      name: "验收管理员",
      username: "admin",
      emailMasked: "a***@example.com",
      emailVerified: true,
      isSuperAdmin: false,
      mustChangePassword: false,
      capabilities: ["mail.manage"],
    };
    const courierFetches: string[] = [];
    const publicConfig = {
      serviceEnabled: true,
      announcement: "",
      prices: {
        bwSingle: 50,
        bwDuplex: 80,
        colorSingle: 150,
        colorDuplex: 250,
        photo: 300,
        corner: 100,
        side: 150,
      },
      wechat: "dormprint",
      phone: "13800138000",
      qrUrl: "",
      photoWidthMm: 127,
      photoHeightMm: 89,
      margins: { top: 3, bottom: 3, left: 3, right: 3 },
      maxFiles: 10,
      maxMb: 50,
      bindingApproved: true,
      quoteMinutes: 15,
      unpaidTimeoutMinutes: 60,
      formats: ["pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "jpg", "png", "heic"],
      features: {
        resumableUploads: true,
        serverDrafts: true,
        afterSales: true,
        selfDeletionRequest: true,
      },
    };

    await page.route("**/api/v1/**", async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname.replace(/^\/api\/v1/, "");
      const send = (data: unknown, status = 200) =>
        route.fulfill({
          status,
          contentType: "application/json",
          body: JSON.stringify({ data, requestId: "e2e-contract" }),
        });
      const deny = (status: number, code: string) =>
        route.fulfill({
          status,
          contentType: "application/json",
          body: JSON.stringify({
            error: { code, message: "登录状态已失效" },
            requestId: "e2e-contract",
          }),
        });

      if (path === "/customer/auth/login" && request.method() === "POST") {
        authed = true;
        return send(customerSession);
      }
      if (path === "/customer/auth/logout") {
        authed = false;
        return send({ ok: true });
      }
      if (path === "/customer/auth/me")
        return authed ? send(customerSession) : deny(401, "UNAUTHENTICATED");
      if (path === "/auth/me")
        return authed ? send(staffSession) : deny(401, "UNAUTHENTICATED");
      if (path === "/customer/session") return send(customerSession);
      if (path === "/customer/account") return send(customerSession.account);
      if (path === "/customer/account/sessions") return send({ items: [] });
      if (path === "/customer/referrals/code")
        return send({
          code: "ABC123",
          shareText: "邀请你使用宿舍打印",
          rules: "邀请首单成功后按活动规则奖励",
        });
      if (path === "/customer/referrals")
        return send({ items: [], total: 0, qualified: 0, granted: 0 });
      if (path.startsWith("/courier/")) courierFetches.push(path);
      if (path === "/public/config") return send(publicConfig);
      return send({ items: [] });
    });

    await page.goto(`${base}/u/account/login`);
    await page.locator('input[type="email"]').fill("student@example.com");
    await page.locator('input[type="password"]').fill("password123");
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await expect(page.getByText("账号已就绪")).toBeVisible();
    await page.getByRole("button", { name: "暂不关联" }).click();
    await expect(page).toHaveURL(`${base}/u/account`);

    const portal = page.locator(".staff-portal");
    await expect(portal).toBeVisible();
    await expect(
      portal.getByRole("link", { name: "进入管理端", exact: true }),
    ).toBeVisible();
    await expect(
      portal.getByRole("link", { name: "进入配送端", exact: true }),
    ).toHaveCount(0);
    await expect(page.getByText("员工登录")).toHaveCount(0);

    await portal.getByRole("link", { name: "进入管理端", exact: true }).click();
    // A limited mail admin must not land on the default-deny dashboard.
    await expect(page).toHaveURL(`${base}/manage/settings/mail`);
    await expect(
      page.getByRole("heading", { name: "邮件设置" }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "返回用户端", exact: true }),
    ).toBeVisible();

    await page.getByRole("link", { name: "返回用户端", exact: true }).click();
    await expect(page).toHaveURL(`${base}/u/account`);

    // A mail-only admin cannot enter the courier deep link and must not cause
    // courier resource prefetches before StaffGuard redirects.
    await page.goto(`${base}/courier/`);
    await expect(page).toHaveURL(`${base}/u/account`);
    await expect(
      page.getByText("当前账号没有访问该工作台的权限", { exact: false }),
    ).toBeVisible();
    expect(courierFetches).toEqual([]);

    // The invite QR is generated locally from the authorized invite URL and is
    // not rendered until the explicit preview action.
    await page
      .getByRole("link", { name: "邀请好友", exact: true })
      .last()
      .click();
    await expect(page).toHaveURL(`${base}/u/invite`);
    await expect(
      page.getByRole("button", { name: "邀请二维码", exact: true }),
    ).toBeVisible();
    await expect(page.getByTestId("link-qr-image")).toHaveCount(0);
    await page.getByRole("button", { name: "邀请二维码", exact: true }).click();
    await expect(page.getByTestId("link-qr-image")).toBeVisible();
    await expect(
      page.getByText(`${base}/u/account/register?invite=ABC123`, {
        exact: true,
      }),
    ).toBeVisible();

    await page.goto(`${base}/u/account`);
    await page.getByRole("button", { name: "退出客户账号" }).click();
    await expect(page).toHaveURL(`${base}/u/`);

    // Logout must clear the effective staff session too: the admin deep link
    // falls back to the one customer login, with no standalone staff form.
    await page.goto(`${base}/manage`);
    await expect(page).toHaveURL(
      (url) =>
        url.pathname === "/u/account/login" &&
        url.searchParams.get("returnTo") === "/manage",
    );
    await expect(page.getByText("员工登录")).toHaveCount(0);
  });

  test("a signed-in customer without a staff role gets a safe account error, not a login loop", async ({
    page,
  }) => {
    const account = {
      id: "ACC-2",
      emailMasked: "n***@example.com",
      displayName: "普通同学",
      verified: true,
      status: "ACTIVE",
      version: 1,
      notificationPreferences: {
        orderCreated: false,
        orderStatusUpdated: false,
        referralReward: false,
      },
    };
    const customerSession = {
      role: "CUSTOMER",
      authKind: "REGISTERED",
      account,
      capabilities: [],
    };
    let authed = false;
    await page.route("**/api/v1/**", async (route) => {
      const path = new URL(route.request().url()).pathname.replace(
        /^\/api\/v1/,
        "",
      );
      const send = (data: unknown, status = 200) =>
        route.fulfill({
          status,
          contentType: "application/json",
          body: JSON.stringify({ data, requestId: "e2e-contract" }),
        });
      if (path === "/customer/auth/login") {
        authed = true;
        return send(customerSession);
      }
      if (path === "/customer/auth/me")
        return authed
          ? send(customerSession)
          : route.fulfill({
              status: 401,
              contentType: "application/json",
              body: JSON.stringify({
                error: { code: "UNAUTHENTICATED", message: "请登录" },
                requestId: "e2e-contract",
              }),
            });
      if (path === "/auth/me")
        return route.fulfill({
          status: 401,
          contentType: "application/json",
          body: JSON.stringify({
            error: { code: "UNAUTHENTICATED", message: "没有员工身份" },
            requestId: "e2e-contract",
          }),
        });
      if (path === "/customer/session") return send(customerSession);
      if (path === "/customer/account") return send(account);
      if (path === "/customer/account/sessions") return send({ items: [] });
      if (path === "/public/config") return send({});
      return send({ items: [] });
    });

    await page.goto(`${base}/manage/orders?tab=1`);
    await expect(page).toHaveURL(
      (url) =>
        url.pathname === "/u/account/login" &&
        url.searchParams.get("returnTo") === "/manage/orders?tab=1",
    );
    await page.locator('input[type="email"]').fill("normal@example.com");
    await page.locator('input[type="password"]').fill("password123");
    await page.getByRole("button", { name: "登录", exact: true }).click();

    await expect(page).toHaveURL(`${base}/u/account`);
    await expect(
      page.getByText("当前账号没有访问该工作台的权限"),
    ).toBeVisible();
    await expect(page.getByText("员工登录")).toHaveCount(0);
  });
});
