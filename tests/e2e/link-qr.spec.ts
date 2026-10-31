import { spawn, type ChildProcess } from "node:child_process";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { createServer } from "node:net";
import { test, expect, type Page, type Route } from "@playwright/test";
import QRCode from "qrcode";

/**
 * Link-QR acceptance tests.
 *
 * The QR image is generated in the browser by qrcode. No QR decoder is
 * installed in this environment (Chrome's BarcodeDetector is unavailable,
 * macOS Vision/Swift is broken, and no zbar/jsqr dependency is allowed), so
 * this suite proves authenticity by reading the downloaded PNG's module matrix
 * and comparing it with the matrix qrcode produces for the exact URL. That is
 * stricter than a display-only/mocked test and catches fragment loss or
 * re-encoding. A revoked-link scan is then exercised against the real
 * OrderLinkPage with a 403 exchange response.
 */

const CHROME = { channel: "chrome", trace: "off" } as const;
test.use(CHROME);
test.describe.configure({ timeout: 60_000 });

const PRIVATE_ORDER_ID = "ORD-QR-42";
const REVOKED_TOKEN = "REVOKED".repeat(5);

let BASE = "http://127.0.0.1:5202";
let server: ChildProcess | null = null;

async function freePort() {
  const probe = createServer();
  await new Promise<void>((resolve, reject) => {
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", resolve);
  });
  const address = probe.address();
  const port = typeof address === "object" && address ? address.port : 0;
  await new Promise<void>((resolve) => probe.close(() => resolve()));
  if (!port) throw new Error("Could not reserve a Vite test port");
  return port;
}

async function reachable(url: string) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(1500) });
    return response.ok || response.status < 500;
  } catch {
    return false;
  }
}

test.beforeAll(async () => {
  const port = await freePort();
  BASE = `http://127.0.0.1:${port}`;
  server = spawn(
    process.execPath,
    [
      "node_modules/vite/bin/vite.js",
      "--port",
      String(port),
      "--strictPort",
      "--host",
      "127.0.0.1",
    ],
    {
      cwd: process.cwd(),
      env: { ...process.env, VITE_DEMO_MODE: "false" },
      stdio: "ignore",
    },
  );
  for (let attempt = 0; attempt < 80; attempt++) {
    if (await reachable(`${BASE}/u/`)) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Vite real-mode server did not start on ${BASE}`);
});

test.afterAll(() => {
  if (server && server.exitCode === null) server.kill("SIGTERM");
});

function privateLink() {
  const token = "Q".repeat(32);
  return `${BASE}/u/order-link?orderId=${PRIVATE_ORDER_ID}&note=a%2Fb#token=${token}&scope=order%2Fread`;
}

async function harnessHash() {
  for (let attempt = 0; attempt < 120; attempt++) {
    try {
      const source = await (
        await fetch(`${BASE}/src/components/link-qr.tsx`, {
          cache: "no-store",
        })
      ).text();
      const match = source.match(
        /\/node_modules\/\.vite\/deps\/react\.js\?v=([A-Za-z0-9_-]+)/,
      );
      if (match) {
        const react = await fetch(
          `${BASE}/node_modules/.vite/deps/react.js?v=${match[1]}`,
        );
        const reactDom = await fetch(
          `${BASE}/node_modules/.vite/deps/react-dom_client.js?v=${match[1]}`,
        );
        if (react.ok && reactDom.ok) return match[1];
      }
    } catch {
      // Vite is still optimizing dependencies; retry below.
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("Vite React dependency hash was not available");
}

function harnessHtml(hash: string) {
  const importMap = {
    imports: {
      react: `/node_modules/.vite/deps/react.js?v=${hash}`,
      "react-dom/client": `/node_modules/.vite/deps/react-dom_client.js?v=${hash}`,
    },
  };
  return `<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Link QR harness</title>
    <link rel="stylesheet" href="/src/styles.css" />
    <script type="importmap">${JSON.stringify(importMap)}</script>
    <script type="module">
      import { injectIntoGlobalHook } from "/@react-refresh";
      injectIntoGlobalHook(window);
      window.$RefreshReg$ = () => {};
      window.$RefreshSig$ = () => (type) => type;
    </script>
  </head>
  <body>
    <div id="root"></div>
    <script type="module">
      import React from "react";
      import ReactDOM from "react-dom/client";
      import { LinkQrButton } from "/src/components/link-qr.tsx";
      import { AdminQRCodeToolsPage } from "/src/v1/qr-tools.tsx";
      const config = window.__LINK_QR_HARNESS || {};
      const root = ReactDOM.createRoot(document.getElementById("root"));
      if (config.mode === "admin") {
        root.render(React.createElement(AdminQRCodeToolsPage));
      } else {
        root.render(React.createElement(LinkQrButton, {
          value: config.value,
          label: "查看私有链接二维码",
          sensitive: true,
        }));
      }
    </script>
  </body>
</html>`;
}

async function mountHarness(page: Page, mode: "button" | "admin", value = "") {
  await page.addInitScript(
    ({ nextMode, nextValue }) => {
      window.__LINK_QR_HARNESS = { mode: nextMode, value: nextValue };
    },
    { nextMode: mode, nextValue: value },
  );
  const hash = await harnessHash();
  await page.route("**/__link-qr-harness", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html; charset=utf-8",
      body: harnessHtml(hash),
    }),
  );
  await page.goto(`${BASE}/__link-qr-harness`, {
    waitUntil: "domcontentloaded",
  });
  await expect(
    page.getByTestId(mode === "admin" ? "admin-qr-input" : "link-qr-trigger"),
  ).toBeVisible({ timeout: 20_000 });
}

function expectedModules(value: string) {
  const qr = QRCode.create(value, { errorCorrectionLevel: "M" });
  return {
    size: qr.modules.size,
    bits: Array.from(qr.modules.data, (value) => (value ? 1 : 0)),
  };
}

function bitsDigest(bits: number[]) {
  return createHash("sha256").update(Uint8Array.from(bits)).digest("hex");
}

function pngDimensions(bytes: Buffer) {
  expect(bytes.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");
  return {
    width: bytes.readUInt32BE(16),
    height: bytes.readUInt32BE(20),
  };
}

async function readDownloadedMatrix(
  page: Page,
  base64: string,
  size: number,
  width: number,
) {
  return page.evaluate(
    async ({ imageBase64, moduleCount, margin, imageWidth }) => {
      const image = new Image();
      image.src = `data:image/png;base64,${imageBase64}`;
      await image.decode();
      const canvas = document.createElement("canvas");
      canvas.width = imageWidth;
      canvas.height = imageWidth;
      const context = canvas.getContext("2d", { willReadFrequently: true });
      if (!context) throw new Error("2d context unavailable");
      context.drawImage(image, 0, 0, imageWidth, imageWidth);
      const pixels = context.getImageData(0, 0, imageWidth, imageWidth).data;
      const scale = imageWidth / (moduleCount + margin * 2);
      const bits: number[] = [];
      for (let row = 0; row < moduleCount; row += 1) {
        for (let col = 0; col < moduleCount; col += 1) {
          const x = Math.floor((margin + col + 0.5) * scale);
          const y = Math.floor((margin + row + 0.5) * scale);
          const offset = (y * imageWidth + x) * 4;
          bits.push(pixels[offset] < 128 ? 1 : 0);
        }
      }
      return bits;
    },
    { imageBase64: base64, moduleCount: size, margin: 2, imageWidth: width },
  );
}

test("private order link: no pre-click leak, exact QR PNG, purge on close and identity change", async ({
  page,
}) => {
  const value = privateLink();
  const token = new URL(value).hash.replace(/^#/, "");
  await mountHarness(page, "button", value);
  await page.evaluate(() => {
    const target = window as unknown as { __revokedQrBlobs?: string[] };
    target.__revokedQrBlobs = [];
    const original = URL.revokeObjectURL.bind(URL);
    URL.revokeObjectURL = (url: string) => {
      target.__revokedQrBlobs?.push(url);
      original(url);
    };
  });

  const before = await page.evaluate(() => ({
    text: document.body.innerText,
    html: document.body.innerHTML,
  }));
  expect(before.text).not.toContain("token=");
  expect(before.text).not.toContain(value);
  expect(before.html).not.toContain("token=");

  await page.getByTestId("link-qr-trigger").click();
  const dialog = page.getByRole("dialog", { name: "链接二维码预览" });
  await expect(dialog).toBeVisible();
  await expect(page.getByTestId("link-qr-value")).toHaveText(value);
  await expect(page.getByTestId("link-qr-image")).toBeVisible({
    timeout: 15_000,
  });
  await expect(dialog).toContainText("链接被撤销或过期后，扫码仍会失败");
  await expect(dialog).toContainText("不是微信好友／群二维码");

  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByTestId("link-qr-download").click(),
  ]);
  expect(download.suggestedFilename()).toBe("dormprint-link-qr.png");
  const file = test.info().outputPath("link-qr.png");
  await download.saveAs(file);
  const bytes = await readFile(file);
  const dimensions = pngDimensions(bytes);
  expect(dimensions.width).toBe(dimensions.height);
  expect(dimensions.width).toBeGreaterThan(400);
  expect(dimensions.width).toBeLessThanOrEqual(512);

  const expected = expectedModules(value);
  expect(expected.size).toBeGreaterThan(20);
  const actualBits = await readDownloadedMatrix(
    page,
    bytes.toString("base64"),
    expected.size,
    dimensions.width,
  );
  expect(actualBits).toHaveLength(expected.bits.length);
  expect(bitsDigest(actualBits)).toBe(bitsDigest(expected.bits));

  await page.keyboard.press("Escape");
  expect(await page.getByTestId("link-qr-image").count()).toBe(0);
  expect(await page.getByTestId("link-qr-value").count()).toBe(0);
  await expect(dialog).toBeHidden();
  expect(await page.getByTestId("link-qr-image").count()).toBe(0);
  expect(await page.getByTestId("link-qr-value").count()).toBe(0);
  const revokedBlobs = await page.evaluate(
    () =>
      (window as unknown as { __revokedQrBlobs?: string[] }).__revokedQrBlobs ??
      [],
  );
  expect(revokedBlobs.some((url) => url.startsWith("blob:"))).toBe(true);

  await page.getByTestId("link-qr-trigger").click();
  await expect(page.getByTestId("link-qr-image")).toBeVisible({
    timeout: 15_000,
  });
  await page.evaluate(async () => {
    const client = await import("/src/growth/client.ts");
    client.advanceIdentity();
  });
  await expect(dialog).toBeHidden();
  expect(await page.getByTestId("link-qr-image").count()).toBe(0);

  // The QR contains the exact fragment; the token itself was never put in a
  // page attribute, and only the explicitly opened dialog displays it.
  expect(token).toBe(`token=${"Q".repeat(32)}&scope=order%2Fread`);
});

test("admin QR tool: HTTPS-only validation, local generation, exact fragment", async ({
  page,
}) => {
  const apiRequests: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/")) {
      apiRequests.push(request.url());
    }
  });
  await mountHarness(page, "admin");

  const input = page.getByTestId("admin-qr-input");
  const check = page.getByTestId("admin-qr-check");

  await input.fill("http://example.com/notice");
  await check.click();
  await expect(page.getByRole("alert")).toContainText(
    "后台自定义链接必须使用 HTTPS",
  );

  await input.fill("javascript:alert(1)");
  await check.click();
  await expect(page.getByRole("alert")).toContainText(
    "后台自定义链接必须使用 HTTPS",
  );

  await input.fill(`https://example.com/${"a".repeat(2400)}`);
  await check.click();
  await expect(page.getByRole("alert")).toContainText("最多 2331 字节");
  await expect(page.getByRole("alert")).toContainText("不会截断");

  const value = "https://example.com/notice/legal?doc=a%2Fb#section-7";
  await input.fill(value);
  await check.click();
  await expect(page.getByTestId("admin-qr-result")).toBeVisible();
  await page.getByRole("button", { name: "预览并下载二维码" }).click();
  await expect(
    page.getByRole("dialog", { name: "链接二维码预览" }),
  ).toBeVisible();
  await expect(page.getByTestId("link-qr-value")).toHaveText(value);
  await expect(page.getByTestId("link-qr-image")).toBeVisible({
    timeout: 15_000,
  });
  expect(apiRequests).toEqual([]);
});

type ExchangeCall = {
  orderId: string;
  accessToken: string;
};

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
  formats: [
    "pdf",
    "doc",
    "docx",
    "ppt",
    "pptx",
    "xls",
    "xlsx",
    "jpg",
    "png",
    "heic",
  ],
  features: {
    resumableUploads: true,
    serverDrafts: true,
    afterSales: true,
    selfDeletionRequest: true,
  },
};

function envelope(data: unknown, status = 200) {
  return status >= 400
    ? { error: data, requestId: "e2e-link-qr" }
    : { data, requestId: "e2e-link-qr" };
}

async function installRevokedApi(page: Page, calls: ExchangeCall[]) {
  const json = (route: Route, data: unknown, status = 200) =>
    route.fulfill({
      status,
      headers: {
        "content-type": "application/json",
        "cache-control": "no-store",
      },
      body: JSON.stringify(envelope(data, status)),
    });

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api\/v1/, "");
    if (path === "/order-access/exchange" && request.method() === "POST") {
      const body = JSON.parse(request.postData() || "{}") as ExchangeCall;
      calls.push(body);
      return json(route, { code: "LINK_REVOKED", message: "链接已失效" }, 403);
    }
    if (path === "/customer/auth/me") {
      return json(route, { code: "AUTH_REQUIRED", message: "未登录" }, 401);
    }
    if (path === "/auth/me") {
      return json(route, { code: "AUTH_REQUIRED", message: "未登录" }, 401);
    }
    if (path === "/customer/session") {
      return json(route, { code: "AUTH_REQUIRED", message: "未登录" }, 401);
    }
    if (path === "/public/config") return json(route, publicConfig);
    if (path === "/public/map") {
      return json(route, {
        items: [
          {
            id: "B1",
            name: "知行苑 1 栋",
            type: "DORMITORY",
            x: 50,
            y: 50,
            enabled: true,
          },
        ],
        mapUrl: "",
      });
    }
    if (path === "/public/slots") return json(route, { items: [] });
    return json(route, { code: "NOT_FOUND", message: path }, 404);
  });
}

test("revoked order link never grants authority: exact exchange token, failure UI, no persistence", async ({
  page,
}) => {
  const calls: ExchangeCall[] = [];
  await installRevokedApi(page, calls);
  const value = privateLink();
  const parsed = new URL(value);
  const expectedToken = new URLSearchParams(parsed.hash.slice(1)).get("token");

  await page.goto(value, { waitUntil: "domcontentloaded" });
  await expect(
    page.getByText(/链接已失效、缺少凭据或暂时无法兑换/),
  ).toBeVisible({ timeout: 20_000 });
  await expect(
    page.getByRole("heading", { name: "这一单已授权访问" }),
  ).toHaveCount(0);

  expect(calls).toHaveLength(1);
  expect(calls[0]).toEqual({
    orderId: PRIVATE_ORDER_ID,
    accessToken: expectedToken,
  });

  const final = new URL(page.url());
  expect(final.hash).toBe("");
  expect(final.searchParams.get("orderId")).toBe(PRIVATE_ORDER_ID);
  const storage = await page.evaluate(() =>
    JSON.stringify({ ...localStorage, ...sessionStorage }),
  );
  expect(storage).not.toContain(expectedToken);
});
