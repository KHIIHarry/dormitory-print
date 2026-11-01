import { spawn, type ChildProcess } from "node:child_process";
import { expect, test, type Page } from "@playwright/test";

/**
 * Numeric form interaction contracts.
 *
 * Runs the app in REAL API mode (VITE_DEMO_MODE=false) against a dedicated
 * Vite server, then intercepts /api/v1 at the network boundary. The default
 * `npm run dev` server boots in demo mode, where /auth/me is answered by the
 * in-memory demo store and would redirect these admin routes to login.
 */

let BASE = "";
let server: ChildProcess | null = null;
let startedHere = false;

async function reachable(url: string) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(1500) });
    return response.ok || response.status < 500;
  } catch {
    return false;
  }
}

test.beforeAll(async () => {
  const port = test.info().project.name === "mobile" ? 5207 : 5206;
  BASE = `http://127.0.0.1:${port}`;
  if (await reachable(`${BASE}/manage/slots`)) return;
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
    if (await reachable(`${BASE}/manage/slots`)) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Vite real-mode server did not start on ${BASE}`);
});

test.afterAll(() => {
  if (startedHere) server?.kill("SIGTERM");
});

test.use({ channel: "chrome", trace: "off" });

const config = {
  prices: {
    bwSingle: null,
    bwDuplex: 30,
    colorSingle: 100,
    colorDuplex: null,
    photo: 200,
    corner: 50,
    side: 100,
  },
  serviceEnabled: true,
  maxFiles: 10,
  maxMb: 50,
  maxOrderMb: 200,
  maxPages: 1000,
  maxCopies: 100,
  maxPixels: 40000000,
  maxExpandedPages: 10000,
  maxFinalMb: 500,
  formats: ["pdf", "docx"],
  photoEnabled: false,
  photoCalibrated: false,
  bindingLimits: { CORNER: 40, SIDE: 60 },
  photoWidthMm: 127,
  photoHeightMm: 89,
  margins: { top: 3, bottom: 3, left: 3, right: 3 },
  a4Margins: { top: 3, bottom: 3, left: 3, right: 3 },
  wechat: "dormprint",
  phone: "13800138000",
  qrUrl: "",
  displayName: "DormPrint",
  guide: "联系管理员",
  announcement: "正常营业",
  unpaidTimeoutMinutes: 60,
  locationMaxAgeSeconds: 300,
  locationMaxAccuracyMeters: 100,
  shortCodeLength: 5,
  pauses: {
    newOrders: false,
    fileProcessing: false,
    production: true,
    delivery: false,
  },
  sections: {
    prices: { version: 1 },
    services: { version: 1 },
    photo: { version: 1 },
    contact: { version: 1 },
    announcement: { version: 1 },
    workflow: { version: 1 },
    retention: { version: 1 },
  },
};

async function adminShell(page: Page) {
  const writes: { path: string; body: Record<string, unknown> }[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    let data: unknown = {};
    if (
      route.request().method() === "PATCH" &&
      path.startsWith("/admin/config/")
    ) {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      writes.push({ path, body });
      data = { version: 2, ...body };
    }
    if (path === "/customer/auth/me")
      data = {
        authKind: "REGISTERED",
        role: "CUSTOMER",
        capabilities: [],
        account: { id: "fixture-account", verified: true, status: "ACTIVE" },
      };
    if (path === "/auth/me")
      data = {
        id: "fixture-admin",
        accountId: "fixture-account",
        role: "ADMIN",
        capabilities: ["*"],
        isSuperAdmin: true,
        mustChangePassword: false,
        emailVerified: true,
      };
    if (path === "/admin/config") data = config;
    if (path === "/admin/slots") data = { items: [] };
    if (path === "/public/slots") data = { items: [] };
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ data }),
    });
  });
  return writes;
}

test("delivery slot capacity can be cleared and re-entered, and blank is rejected on save", async ({
  page,
}) => {
  await adminShell(page);
  await page.goto(`${BASE}/manage/slots`);
  await page.getByRole("button", { name: "新建时段", exact: true }).click();
  const capacity = page.getByLabel("预约容量（单）", { exact: true });
  await expect(capacity).toHaveValue("30");
  await capacity.fill("");
  await expect(capacity).toHaveValue("");
  await capacity.fill("45");
  await expect(capacity).toHaveValue("45");
  await page.getByLabel("配送日期", { exact: true }).fill("2026-12-01");
  await page.getByLabel("时段名称", { exact: true }).fill("晚间配送");
  await page.getByLabel("开始时间", { exact: true }).fill("19:00");
  await page.getByLabel("结束时间", { exact: true }).fill("20:00");
  await capacity.fill("");
  await page.getByRole("button", { name: "保存时段", exact: true }).click();
  await expect(page.getByRole("status")).toContainText(
    "预约容量必须是 1 到 100000 之间的整数",
  );
});

test("service limits and decimal calibration fields keep an empty editing state", async ({
  page,
}) => {
  await adminShell(page);
  await page.goto(`${BASE}/manage/settings`);
  await page.getByRole("button", { name: "服务与限制", exact: true }).click();
  const maxFiles = page.getByLabel("单订单最多文件数", { exact: true });
  await expect(maxFiles).toHaveValue("10");
  await maxFiles.fill("");
  await expect(maxFiles).toHaveValue("");
  await maxFiles.fill("25");
  await expect(maxFiles).toHaveValue("25");

  await page.getByRole("button", { name: "打印文件校准", exact: true }).click();
  const margin = page.getByLabel("上留白（mm）", { exact: true }).first();
  await expect(margin).toHaveValue("3");
  await margin.fill("");
  await expect(margin).toHaveValue("");
  await margin.fill("3.5");
  await expect(margin).toHaveValue("3.5");
});

test("price fields keep yuan display and cents precision across an empty edit", async ({
  page,
}) => {
  const writes = await adminShell(page);
  await page.goto(`${BASE}/manage/settings`);
  const price = page.getByLabel("A4 黑白双面（元 / 张）", { exact: true });
  await expect(price).toHaveValue("0.3");
  await price.fill("");
  await expect(price).toHaveValue("");
  await price.fill("0.35");
  await expect(price).toHaveValue("0.35");
  await page.getByRole("button", { name: "预览并保存", exact: true }).click();
  await page
    .getByRole("button", { name: "确认保存当前分区", exact: true })
    .click();
  await expect(page.getByRole("status")).toContainText("配置已保存");
  expect(writes.at(-1)?.path).toBe("/admin/config/prices");
  expect(writes.at(-1)?.body).toMatchObject({
    expectedVersion: 1,
    bwDuplex: 35,
    bwSingle: null,
    colorDuplex: null,
  });
});

test("SMTP port can be cleared without snapping to zero, then re-entered", async ({
  page,
}) => {
  await adminShell(page);
  await page.route("**/api/v1/admin/config/mail", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        data: {
          version: 1,
          enabled: true,
          host: "smtp.example.invalid",
          port: 465,
          security: "SSL_TLS",
          timeoutSeconds: 10,
          username: "mailer@example.invalid",
          fromName: "示例发件人",
          fromAddress: "mailer@example.invalid",
          replyTo: "",
          authEnabled: true,
          secretConfigured: true,
          allowInsecure: false,
        },
      }),
    });
  });
  await page.goto(`${BASE}/manage/settings/mail`);
  const port = page.getByLabel("端口", { exact: true });
  await expect(port).toHaveValue("465");
  await port.fill("");
  await expect(port).toHaveValue("");
  await port.fill("587");
  await expect(port).toHaveValue("587");
});
