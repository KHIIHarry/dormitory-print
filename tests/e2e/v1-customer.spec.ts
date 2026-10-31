import { spawn, type ChildProcess } from "node:child_process";
import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * V1 customer frontend contract tests.
 *
 * These tests run the app in REAL API mode (VITE_DEMO_MODE=false) against a
 * dedicated Vite server and intercept /api/v1 at the network boundary. They
 * verify the frontend's exact requests, pending/error/uncertain/retry states and
 * read-only gating. They are NOT a substitute for backend acceptance: Codex's
 * PostgreSQL/browser suite must still exercise the real API and files.
 */

// Each Playwright worker/project owns its own dev server so one project's
// teardown cannot break the other while sharing a port.
let REAL_BASE = "http://127.0.0.1:5199";
let server: ChildProcess | null = null;
let startedHere = false;
const appUrl = (path: string) => `${REAL_BASE}${path}`;

async function reachable(url: string) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(1500) });
    return response.ok || response.status < 500;
  } catch {
    return false;
  }
}

test.beforeAll(async () => {
  const port = test.info().project.name === "mobile" ? 5201 : 5200;
  REAL_BASE = `http://127.0.0.1:${port}`;
  if (await reachable(`${REAL_BASE}/u/`)) return;
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
    if (await reachable(`${REAL_BASE}/u/`)) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Vite real-mode server did not start on ${REAL_BASE}`);
});

test.afterAll(() => {
  if (startedHere) server?.kill("SIGTERM");
});

// The bundled headless shell is not installed in this workspace; use the
// locally installed Chrome channel so the contract suite still runs.
test.use({
  channel: "chrome",
  // Shared test-results can be cleaned by parallel module workers; this suite
  // checks behavior, so avoid racing trace artifacts.
  trace: "off",
});

type Call = {
  method: string;
  path: string;
  body: unknown;
  headers: Record<string, string>;
};

const envelope = (data: unknown) => ({
  data,
  requestId: "e2e-contract",
});

const errorEnvelope = (code: string, message: string) => ({
  error: { code, message },
  requestId: "e2e-contract",
});

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

function orderFixture(overrides: Record<string, unknown> = {}) {
  return {
    id: "ORD-1",
    pickupCode: "ABC12",
    version: 3,
    contentRevision: 1,
    status: "PENDING_PRINT",
    nickname: "验收同学",
    phone: "13800138000",
    locationId: "B1",
    slotId: "S1",
    requestedSlotId: "S1",
    confirmedSlotId: "S1",
    slotStatus: "CONFIRMED",
    createdAt: "2026-10-30T02:00:00.000Z",
    closeAt: "2026-10-30T03:00:00.000Z",
    totalCents: 1200,
    refundedCents: 0,
    currentQuoteId: "Q1",
    quote: {
      id: "Q1",
      priceVersion: 3,
      expiresAt: "2026-10-30T12:00:00.000Z",
      lines: [
        {
          key: "L1",
          relatedId: "F1",
          unit: "SHEET",
          quantity: 10,
          unitPriceCents: 50,
          amountCents: 500,
          service: "A4",
        },
        {
          key: "L2",
          relatedId: "F1",
          unit: "BOOKLET",
          quantity: 1,
          unitPriceCents: 100,
          amountCents: 100,
          service: "BINDING",
        },
      ],
      subtotalCents: 600,
      discountCents: 0,
      totalCents: 1200,
      deliveryFeeCents: 0,
      checkoutAccountId: "ACC-1",
      selectedEligibleSubtotalCents: 600,
      promotionRuleVersion: "v1",
      selectionMode: "AUTO",
      selectedPromotionType: "NONE",
      volumeDiscountCandidate: null,
      discounts: [],
      discountAllocations: [],
    },
    checkoutAccountId: "ACC-1",
    checkoutAuthKind: "REGISTERED",
    ownerAccountId: "ACC-1",
    ownerPrincipalId: "P-1",
    accessKind: "OWNER",
    files: [],
    packingStatus: "BLOCKED",
    addressReview: false,
    note: "",
    customerNote: "",
    priority: false,
    history: [],
    settlement: {
      settlementKind: "CASH_MANUAL",
      cashReceivedCents: 1200,
      discountCents: 0,
      confirmedAt: "2026-10-30T02:10:00.000Z",
      confirmedBy: "admin",
    },
    actualStartedAt: null,
    allowedActions: [
      "ORDER_VIEW",
      "FINAL_PDF_PREVIEW",
      "ORDER_AFTER_SALES",
    ],
    ...overrides,
  };
}

type ApiState = {
  calls: Call[];
  order: Record<string, unknown>;
  cases: Record<string, unknown>[];
  deletionRequests: Record<string, unknown>[];
  exportStatus: string;
  exportFailures: number;
};

async function installApi(
  page: Page,
  options: {
    order?: Record<string, unknown>;
    cases?: Record<string, unknown>[];
    deletionRequests?: Record<string, unknown>[];
    exportStatus?: string;
    exportFailures?: number;
  } = {},
) {
  const state: ApiState = {
    calls: [],
    order: options.order || orderFixture(),
    cases: options.cases || [],
    deletionRequests: options.deletionRequests || [],
    exportStatus: options.exportStatus || "READY",
    exportFailures: options.exportFailures || 0,
  };

  const json = (route: Route, data: unknown, status = 200) =>
    route.fulfill({
      status,
      headers: { "content-type": "application/json", "cache-control": "no-store" },
      body: JSON.stringify(
        status >= 400
          ? errorEnvelope(
              (data as { code: string }).code,
              (data as { message: string }).message,
            )
          : envelope(data),
      ),
    });

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api\/v1/, "");
    const method = request.method();
    let body: unknown;
    try {
      body = request.postData() ? JSON.parse(request.postData()!) : undefined;
    } catch {
      body = request.postData();
    }
    state.calls.push({
      method,
      path,
      body,
      headers: request.headers(),
    });

    if (path === "/customer/auth/me") return json(route, customerSession);
    if (path === "/auth/me")
      return json(route, { code: "AUTH_REQUIRED", message: "未登录" }, 401);
    if (path === "/customer/session") return json(route, customerSession);
    if (path === "/public/config") return json(route, publicConfig);
    if (path === "/public/map")
      return json(route, {
        items: [{ id: "B1", name: "知行苑 1 栋", type: "DORMITORY", x: 50, y: 50, enabled: true }],
        mapUrl: "",
      });
    if (path === "/public/slots")
      return json(route, {
        items: [
          {
            id: "S1",
            date: "2026-10-30",
            weekday: "星期四",
            name: "晚间配送",
            start: "19:00",
            end: "20:00",
            enabled: true,
            capacity: 5,
            occupied: 2,
            remaining: 3,
            selectable: true,
            version: 2,
          },
        ],
      });
    if (path === "/customer/account") return json(route, customerSession.account);
    if (path === "/customer/account/exports" && method === "POST") {
      if (state.exportFailures > 0) {
        state.exportFailures--;
        return json(
          route,
          { code: "SERVICE_UNAVAILABLE", message: "服务暂时不可用，请重试" },
          503,
        );
      }
      return json(route, { exportJobId: "EXP-1" });
    }
    if (path === "/customer/account/exports/EXP-1" && method === "GET")
      return json(route, {
        id: "EXP-1",
        status: state.exportStatus,
        createdAt: "2026-10-30T04:00:00.000Z",
        expiresAt: "2026-10-31T04:00:00.000Z",
        downloadUrl: null,
      });
    if (path === "/customer/account/deletion-requests" && method === "GET")
      return json(route, { items: state.deletionRequests, nextCursor: null });
    if (path === "/customer/account/deletion-requests" && method === "POST") {
      const created = {
        id: "DEL-1",
        status: "REQUESTED",
        createdAt: "2026-10-30T04:10:00.000Z",
        reason: (body as { reason?: string })?.reason || "",
        blockers: [],
      };
      state.deletionRequests = [created, ...state.deletionRequests];
      return json(route, created);
    }
    if (path === "/orders/ORD-1/after-sales" && method === "GET")
      return json(route, { items: state.cases, nextCursor: null });
    if (path === "/orders/ORD-1/after-sales" && method === "POST") {
      const payload = body as {
        responsibility: string;
        solution: string;
        reason: string;
        affectedItems?: { lineKey: string; quantity: number }[];
      };
      const created = {
        id: "AS-NEW",
        orderId: "ORD-1",
        version: 1,
        status: "REQUESTED",
        source: "CUSTOMER",
        responsibility: payload.responsibility,
        solution: payload.solution,
        reason: payload.reason,
        suggestedCents: 500,
        approvedCents: null,
        adjustmentBasis: "",
        data: payload.affectedItems
          ? { affectedItems: payload.affectedItems }
          : {},
        createdAt: "2026-10-30T04:20:00.000Z",
      };
      state.cases = [created, ...state.cases.filter((item) => item.id !== created.id)];
      return json(route, created);
    }
    const choice = path.match(
      /^\/orders\/ORD-1\/after-sales\/([^/]+)\/choice$/,
    );
    if (choice && method === "POST") {
      const id = decodeURIComponent(choice[1]);
      const payload = body as { expectedVersion: number; solution: string };
      state.cases = state.cases.map((item) =>
        item.id === id
          ? { ...item, solution: payload.solution, version: item.version + 1 }
          : item,
      );
      return json(route, state.cases.find((item) => item.id === id));
    }
    if (path === "/orders/ORD-1" && method === "GET")
      return json(route, state.order);
    return json(
      route,
      { code: "NOT_FOUND", message: `未在契约测试中模拟 ${method} ${path}` },
      404,
    );
  });
  return state;
}

test("order after-sales requires allowedActions, posts exact DTOs and separates applied/approved/registered refund", async ({
  page,
}) => {
  const cases = [
    {
      id: "AS-CHOICE",
      orderId: "ORD-1",
      version: 4,
      status: "REQUESTED",
      source: "ADMIN",
      responsibility: "MERCHANT",
      solution: "REFUND",
      reason: "商家核实错印",
      suggestedCents: 600,
      approvedCents: 600,
      adjustmentBasis: "按受影响 A4 项目折后金额",
      data: {},
      createdAt: "2026-10-29T04:00:00.000Z",
    },
    {
      id: "AS-APPROVED",
      orderId: "ORD-1",
      version: 5,
      status: "REFUND_APPROVED",
      source: "CUSTOMER",
      responsibility: "MERCHANT",
      solution: "REFUND",
      reason: "装订散页",
      suggestedCents: 400,
      approvedCents: 500,
      adjustmentBasis: "管理员自定义调整：补装订损耗",
      data: {},
      createdAt: "2026-10-29T04:30:00.000Z",
    },
    {
      id: "AS-DONE",
      orderId: "ORD-1",
      version: 7,
      status: "REFUNDED",
      source: "CUSTOMER",
      responsibility: "MERCHANT",
      solution: "REFUND",
      reason: "缺页",
      suggestedCents: 500,
      approvedCents: 600,
      adjustmentBasis: "管理员自定义调整：补打纸张损耗",
      data: {
        refundAmountCents: 600,
        refundRecordedAt: "2026-10-30T03:30:00.000Z",
      },
      refund: {
        amountCents: 600,
        registeredAt: "2026-10-30T03:30:00.000Z",
      },
      createdAt: "2026-10-28T04:00:00.000Z",
    },
  ];
  const state = await installApi(page, { cases });

  await page.goto(appUrl("/u/orders/ORD-1"));
  await expect(
    page.getByRole("heading", { name: "订单详情" }),
  ).toBeVisible();
  await expect(page.getByText("申请，未保证名额")).toBeVisible();
  const afterSales = page
    .locator("section.panel")
    .filter({ hasText: "售后与退款" });
  await expect(afterSales).toBeVisible();
  await afterSales
    .getByRole("button", { name: "申请售后 / 取消退款" })
    .click();
  const form = afterSales.locator(".v1-form");
  await form
    .getByRole("button", { name: "商家责任 · 补打 / 退款" })
    .click();
  await form
    .getByRole("button", { name: "免费重做 / 补打", exact: true })
    .click();
  await form
    .getByLabel("申请原因 *")
    .fill("第 3 页漏印，申请补打受影响部分");
  await form.locator(".v1-affected input[type=checkbox]").first().check();
  await form.locator(".v1-affected input[type=number]").fill("2");
  await form.getByRole("button", { name: "提交售后申请" }).click();

  await expect
    .poll(() => state.calls.filter((call) => call.path === "/orders/ORD-1/after-sales" && call.method === "POST").length)
    .toBe(1);
  const created = state.calls.find(
    (call) => call.path === "/orders/ORD-1/after-sales" && call.method === "POST",
  );
  expect(created?.body).toEqual({
    expectedVersion: 3,
    responsibility: "MERCHANT",
    solution: "REPRINT",
    reason: "第 3 页漏印，申请补打受影响部分",
    affectedItems: [{ lineKey: "L1", quantity: 2 }],
  });

  await expect(
    page.getByText("已提交，等待管理员核实").first(),
  ).toBeVisible();
  await expect(
    page.getByText("管理员已核实商家责任，请选择处理方式：").first(),
  ).toBeVisible();
  await expect(
    page.locator(".v1-status").filter({ hasText: "管理员已登记退款" }),
  ).toHaveCount(1);
  await expect(
    page
      .locator(".v1-status")
      .filter({ hasText: "已批准退款，等待管理员线下登记" }),
  ).toHaveCount(1);
  await expect(page.getByText("调整依据：管理员自定义调整：补装订损耗")).toBeVisible();
  await expect(
    page.getByText(/订单已确认收款或进入履约，原文件、打印参数和应付快照不会修改/),
  ).toBeVisible();
  await expect(page.getByText("调整依据：管理员自定义调整：补打纸张损耗")).toBeVisible();

  await page
    .locator(".v1-case")
    .filter({ hasText: "商家核实错印" })
    .getByRole("button", { name: "退受影响部分" })
    .click();
  await expect
    .poll(() => state.calls.filter((call) => call.path.endsWith("/choice")).length)
    .toBe(1);
  const choice = state.calls.find((call) => call.path.endsWith("/choice"));
  expect(choice?.body).toEqual({ expectedVersion: 4, solution: "REFUND" });
});

test("read-only or disallowed order never exposes after-sales creation", async ({
  page,
}) => {
  await installApi(page, {
    order: orderFixture({
      allowedActions: ["ORDER_VIEW", "FINAL_PDF_PREVIEW"],
    }),
    cases: [],
  });
  await page.goto(appUrl("/u/orders/ORD-1"));
  const afterSales = page
    .locator("section.panel")
    .filter({ hasText: "售后与退款" });
  await expect(afterSales).toBeVisible();
  await expect(
    afterSales.getByRole("button", { name: "申请售后 / 取消退款" }),
  ).toHaveCount(0);
  await expect(
    afterSales.getByText(/服务端 allowedActions 未开放售后申请/),
  ).toBeVisible();
});

test("personal data export retries the same idempotency key after an uncertain response and offers a real download", async ({
  page,
}) => {
  const state = await installApi(page, { exportFailures: 1 });
  await page.goto(appUrl("/u/account"));
  await expect(page.getByRole("heading", { name: "账号设置" })).toBeVisible();
  const dataPanel = page
    .locator("section.panel")
    .filter({ hasText: "导出我的个人资料" });
  await expect(dataPanel).toBeVisible();
  await dataPanel
    .getByRole("button", { name: "申请导出我的资料" })
    .click();
  await expect(
    dataPanel.getByText(/提交结果待核实/).first(),
  ).toBeVisible();
  await dataPanel.getByRole("button", { name: "安全重试" }).first().click();
  await expect(page.getByText("已就绪，可下载")).toBeVisible();
  const download = dataPanel.getByRole("link", { name: "下载资料" });
  await expect(download).toHaveAttribute(
    "href",
    /\/api\/v1\/customer\/account\/exports\/EXP-1\/download$/,
  );

  const exports = state.calls.filter(
    (call) => call.path === "/customer/account/exports" && call.method === "POST",
  );
  expect(exports).toHaveLength(2);
  expect(exports[0].headers["idempotency-key"]).toBe(
    exports[1].headers["idempotency-key"],
  );
  expect(exports[0].headers["idempotency-key"]).toBeTruthy();
});

test("deletion request needs explicit confirmation, posts confirm:true and shows pending administrator review without deleting", async ({
  page,
}) => {
  const state = await installApi(page, { deletionRequests: [] });
  await page.goto(appUrl("/u/account"));
  const deletion = page
    .locator("section.panel")
    .filter({ hasText: "申请注销账号" });
  await deletion.getByLabel("注销原因（选填）").fill("不再使用");
  const confirm = deletion.getByRole("checkbox");
  await expect(
    deletion.getByRole("button", { name: "提交注销申请" }),
  ).toBeDisabled();
  await confirm.check();
  await deletion.getByRole("button", { name: "提交注销申请" }).click();
  await expect(page.getByText("注销申请等待管理员审核")).toBeVisible();
  await expect(page.getByText("已按保留规则处理")).toHaveCount(0);

  const posted = state.calls.find(
    (call) =>
      call.path === "/customer/account/deletion-requests" &&
      call.method === "POST",
  );
  expect(posted?.body).toEqual({ confirm: true, reason: "不再使用" });
  const listed = state.calls.some(
    (call) =>
      call.path === "/customer/account/deletion-requests" &&
      call.method === "GET",
  );
  expect(listed).toBe(true);
  const stored = await page.evaluate(() =>
    Object.keys(window.localStorage),
  );
  expect(stored).toEqual([]);
});

/* ------------------------------------------------------------------------- */
/* Upload: Excel options, HEIC normalized preview, resume and cancellation    */
/* ------------------------------------------------------------------------- */

type UploadState = {
  calls: { method: string; path: string; body: unknown; headers: Record<string, string>; raw?: string }[];
  excel: unknown;
  sessionCreates: number;
  chunkAttempts: number;
  normalizedFetches: number;
  assetCanceled: boolean;
  assetReady: boolean;
};

const onePixelPng = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2n1kAAAAASUVORK5CYII=",
  "base64",
);

function preparationFixture() {
  return {
    id: "P1",
    version: 2,
    status: "READY",
    contentRevision: 1,
    expiresAt: "2026-10-31T04:00:00.000Z",
    cancelRequested: false,
    quote: {
      ...orderFixture().quote,
      id: "Q1",
      subtotalCents: 600,
      discountCents: 0,
      totalCents: 600,
      selectedEligibleSubtotalCents: 600,
      expiresAt: "2026-10-31T12:00:00.000Z",
    },
    files: [
      {
        clientFileId: "F1",
        assetId: "A1",
        kind: "DOCUMENT",
        name: "资料.pdf",
        status: "READY",
        pages: 3,
        excludedPages: [],
        bindingType: "NONE",
        segments: [
          {
            clientSegmentId: "SEG1",
            ranges: [{ from: 1, to: 3 }],
            colorMode: "BW",
            sides: "SINGLE",
            flip: "NONE",
            copies: 1,
          },
        ],
        copies: null,
        orientation: null,
        fit: null,
        rotationDegrees: null,
        crop: null,
      },
    ],
    artifacts: [
      {
        id: "ART1",
        fileId: "F1",
        segmentId: "SEG1",
        sha256: "c".repeat(64),
        url: "/api/v1/assets/A2/content",
        pages: 3,
        sheets: 3,
        blankBacks: 0,
        range: "1-3",
        copies: 1,
        color: "BW",
        sides: "SINGLE",
        flip: "NONE",
      },
    ],
    validation: { ok: true, errors: [], warnings: [] },
    checkoutAccountId: null,
    canAdopt: true,
    failureCode: null,
    failureMessage: null,
    replacesOrderId: null,
    expectedOrderVersion: null,
    createdAt: "2026-10-30T04:00:00.000Z",
    updatedAt: "2026-10-30T04:05:00.000Z",
  };
}

async function installUploadApi(
  page: Page,
  options: {
    firstChunkFailure?: boolean;
    assetReady?: boolean;
    normalized?: boolean;
    failFirstChunk?: boolean;
    preparation?: boolean;
  } = {},
) {
  const state: UploadState = {
    calls: [],
    excel: undefined,
    sessionCreates: 0,
    chunkAttempts: 0,
    normalizedFetches: 0,
    assetCanceled: false,
    assetReady: !!options.assetReady,
  };
  const session = (received = 0, total = 0) => ({
    id: "S1",
    status: "UPLOADING",
    purpose: "PRINT_SOURCE",
    name: "upload.bin",
    totalBytes: total,
    receivedBytes: received,
    nextOffset: received,
    chunkSize: 1024 * 1024,
    ranges: received ? [{ start: 0, end: received - 1 }] : [],
    sha256: "a".repeat(64),
    mime: "application/octet-stream",
    expiresAt: "2026-10-31T04:00:00.000Z",
    assetId: null,
    cancelRequested: false,
  });

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api\/v1/, "");
    const method = request.method();
    const postData = request.postData();
    let body: unknown;
    try {
      body = postData ? JSON.parse(postData) : undefined;
    } catch {
      body = postData;
    }
    state.calls.push({
      method,
      path,
      body,
      headers: request.headers(),
      raw: postData || undefined,
    });
    const json = (data: unknown, status = 200) =>
      route.fulfill({
        status,
        headers: { "content-type": "application/json" },
        body: JSON.stringify(
          status >= 400
            ? { error: data, requestId: "e2e-upload" }
            : { data, requestId: "e2e-upload" },
        ),
      });

    if (path === "/customer/auth/me") return json(customerSession);
    if (path === "/auth/me")
      return json({ code: "AUTH_REQUIRED", message: "未登录" }, 401);
    if (path === "/customer/session") return json(customerSession);
    if (path === "/public/config")
      return json({
        ...publicConfig,
        features: { ...publicConfig.features, resumableUploads: true },
      });
    if (path === "/preparations" && method === "GET")
      return json({
        items: options.preparation ? [preparationFixture()] : [],
        total: options.preparation ? 1 : 0,
      });
    if (path === "/preparations/P1" && method === "GET")
      return json(preparationFixture());
    if (path === "/preparations/P1/requote" && method === "POST")
      return json({ ...preparationFixture().quote, priceVersion: 4 });
    if (path === "/customer/coupons" && method === "GET")
      return json({ items: [] });
    if (path === "/public/map")
      return json({
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
    if (path === "/public/slots")
      return json({
        items: [
          {
            id: "S1",
            date: "2026-10-30",
            weekday: "星期四",
            name: "晚间配送",
            start: "19:00",
            end: "20:00",
            enabled: true,
            capacity: 5,
            occupied: 2,
            remaining: 3,
            selectable: true,
            version: 2,
          },
        ],
      });
    if (path === "/upload-sessions" && method === "POST") {
      state.sessionCreates++;
      state.excel = (body as { excelOptions?: unknown })?.excelOptions;
      const total = (body as { totalBytes: number }).totalBytes;
      return json({ ...session(0, total), name: (body as { name: string }).name });
    }
    const chunk = path.match(/^\/upload-sessions\/S1\/chunks$/);
    if (chunk && method === "PUT") {
      state.chunkAttempts++;
      if (options.failFirstChunk && state.chunkAttempts === 1)
        return json(
          { code: "SERVICE_UNAVAILABLE", message: "服务暂时不可用" },
          503,
        );
      const offset = Number(url.searchParams.get("offset") || 0);
      const total = Number(
        state.calls.find((call) => call.path === "/upload-sessions")?.body &&
          (state.calls.find((call) => call.path === "/upload-sessions")!
            .body as { totalBytes: number }).totalBytes,
      );
      const length = postData ? Buffer.byteLength(postData) : 0;
      void offset;
      return json({ ...session(offset + length, total), name: "upload.bin" });
    }
    if (path === "/upload-sessions/S1/complete" && method === "POST")
      return json({
        assetId: "A1",
        status: "QUEUED",
        kind: "XLSX",
        name: "upload.bin",
        size: 100,
        sha256: "b".repeat(64),
        pages: null,
        duplicate: false,
      });
    if (path === "/upload-sessions/S1" && method === "GET")
      return json({ ...session(0, state.calls.find((c) => c.path === "/upload-sessions")?.body ? (state.calls.find((c) => c.path === "/upload-sessions")!.body as { totalBytes: number }).totalBytes : 100), name: "upload.bin" });
    if (path === "/upload-sessions/S1" && method === "DELETE")
      return json({ ...session(), status: "CANCELLED" });
    if (path === "/assets/A1" && method === "GET") {
      if (options.preparation)
        return json({
          id: "A1",
          assetId: "A1",
          status: "READY",
          purpose: "PRINT_SOURCE",
          name: "资料.pdf",
          mime: "application/pdf",
          size: 2048,
          sha256: "b".repeat(64),
          pages: 3,
          kind: "PDF",
          error: null,
          retryable: false,
          url: null,
          cancelRequested: false,
          normalizedAssetId: null,
          createdAt: "2026-10-30T04:00:00.000Z",
        });
      if (state.assetCanceled)
        return json({
          id: "A1",
          assetId: "A1",
          status: "FAILED",
          purpose: "PRINT_SOURCE",
          name: "upload.bin",
          mime: "application/octet-stream",
          size: 100,
          sha256: "b".repeat(64),
          pages: null,
          kind: "PDF",
          error: "任务已取消",
          failureCode: "CANCELED",
          retryable: false,
          url: null,
          cancelRequested: true,
          normalizedAssetId: null,
          createdAt: "2026-10-30T04:00:00.000Z",
        });
      if (state.assetReady)
        return json({
          id: "A1",
          assetId: "A1",
          status: "READY",
          purpose: "PRINT_SOURCE",
          name: "upload.bin",
          mime: "image/heic",
          size: 100,
          sha256: "b".repeat(64),
          pages: 1,
          kind: "HEIC",
          error: null,
          retryable: false,
          dimensions: { width: 10, height: 10 },
          url: "/api/v1/assets/A1/content",
          cancelRequested: false,
          normalizedAssetId: null,
          ...(options.normalized
            ? { normalizedUrl: "/api/v1/assets/A1-normalized/content" }
            : {}),
          createdAt: "2026-10-30T04:00:00.000Z",
        });
      return json({
        id: "A1",
        assetId: "A1",
        status: "PROCESSING",
        purpose: "PRINT_SOURCE",
        name: "upload.bin",
        mime: "application/octet-stream",
        size: 100,
        sha256: "b".repeat(64),
        pages: null,
        kind: "PDF",
        error: null,
        retryable: true,
        url: null,
        cancelRequested: false,
        normalizedAssetId: null,
        createdAt: "2026-10-30T04:00:00.000Z",
      });
    }
    if (path === "/assets/A1/cancel" && method === "POST") {
      state.assetCanceled = true;
      return json({
        id: "A1",
        assetId: "A1",
        status: "PROCESSING",
        purpose: "PRINT_SOURCE",
        name: "upload.bin",
        mime: "application/octet-stream",
        size: 100,
        sha256: "b".repeat(64),
        pages: null,
        kind: "PDF",
        error: null,
        retryable: false,
        url: null,
        cancelRequested: true,
        normalizedAssetId: null,
        createdAt: "2026-10-30T04:00:00.000Z",
      });
    }
    if (path === "/assets/A1-normalized/content" && method === "GET") {
      state.normalizedFetches++;
      return route.fulfill({
        status: 200,
        headers: { "content-type": "image/png", "cache-control": "no-store" },
        body: onePixelPng,
      });
    }
    return json(
      { code: "NOT_FOUND", message: `未模拟 ${method} ${path}` },
      404,
    );
  });
  return state;
}

test("upload waits for account hydration and the upload session before accepting files", async ({ page }) => {
  await installUploadApi(page, { assetReady: true });
  let resolveAccount!: () => void;
  const accountGate = new Promise<void>((resolve) => { resolveAccount = resolve; });
  let resolveSession!: () => void;
  const sessionGate = new Promise<void>((resolve) => { resolveSession = resolve; });
  await page.route("**/api/v1/customer/auth/me", async (route) => {
    await accountGate;
    await route.fulfill({ json: envelope(customerSession) });
  });
  await page.route("**/api/v1/customer/session", async (route) => {
    await sessionGate;
    await route.fulfill({ json: envelope(customerSession) });
  });
  try {
    await page.goto(appUrl("/u/new"));
    const choose = page.getByRole("button", { name: "选择文件", exact: true });
    await expect(choose).toBeDisabled();
    resolveAccount();
    await expect(page.locator(".header-profile")).toHaveAttribute("href", "/u/account");
    await expect(choose).toBeDisabled();
    resolveSession();
    await expect(choose).toBeEnabled();
  } finally {
    resolveAccount();
    resolveSession();
  }
});

test("Excel upload asks for print area/pagination and sends strict excelOptions on the resumable session", async ({
  page,
}) => {
  const state = await installUploadApi(page, { assetReady: true });
  await page.goto(appUrl("/u/new"));
  await page.locator('input[type="file"]').setInputFiles({
    name: "成绩表.xlsx",
    mimeType:
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: Buffer.from("PK\u0003\u0004fake-xlsx"),
  });
  await expect(
    page.getByRole("heading", { name: "Excel 打印区域与分页" }),
  ).toBeVisible();
  await page.getByLabel("打印区域（选填）").fill("a1:f40");
  await page.getByRole("button", { name: "确认并上传" }).click();

  await expect
    .poll(() => state.sessionCreates)
    .toBe(1);
  expect(state.excel).toMatchObject({
    printArea: "A1:F40",
    fitToWidth: 1,
    fitToHeight: 0,
    gridlines: false,
  });
  const create = state.calls.find(
    (call) => call.path === "/upload-sessions" && call.method === "POST",
  );
  expect((create?.body as { sha256: string }).sha256).toMatch(/^[a-f0-9]{64}$/);
  expect(create?.body).toMatchObject({
    name: "成绩表.xlsx",
    purpose: "PRINT_SOURCE",
  });
  expect(
    state.calls.some(
      (call) => call.path === "/assets" && call.method === "POST",
    ),
  ).toBe(false);
  await expect(page.getByRole("button", { name: "下一步" })).toBeEnabled({
    timeout: 8000,
  });
  expect(
    state.calls.filter((call) => call.method === "PUT").length,
  ).toBeGreaterThan(0);
});

test("HEIC upload uses the server READY normalized PNG for the local display source and never selects paper before it", async ({
  page,
}) => {
  const state = await installUploadApi(page, {
    assetReady: true,
    normalized: true,
  });
  await page.goto(appUrl("/u/new"));
  await page.locator('input[type="file"]').setInputFiles({
    name: "IMG_0001.HEIC",
    mimeType: "image/heic",
    buffer: Buffer.from("fake-heic-bytes"),
  });
  await expect(
    page.getByRole("heading", { name: "选择图片打印纸张" }),
  ).toBeVisible({ timeout: 8000 });
  expect(state.normalizedFetches).toBe(1);
  await expect(page.locator(".image-medium-source img")).toHaveAttribute(
    "src",
    /^blob:/,
  );
  const create = state.calls.find(
    (call) => call.path === "/upload-sessions" && call.method === "POST",
  );
  expect(create?.body).toMatchObject({ name: "IMG_0001.HEIC" });
});

test("a lost chunk reuses the same session, offset and idempotency key on retry", async ({
  page,
}) => {
  const state = await installUploadApi(page, {
    failFirstChunk: true,
    assetReady: true,
    normalized: false,
  });
  await page.goto(appUrl("/u/new"));
  await page.locator('input[type="file"]').setInputFiles({
    name: "资料.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4 fake"),
  });
  await expect(page.getByText(/上传结果待核实/)).toBeVisible({
    timeout: 8000,
  });
  await page
    .locator(".file-error")
    .getByRole("button", { name: "重试" })
    .click();
  await expect
    .poll(() => state.chunkAttempts)
    .toBeGreaterThanOrEqual(2);
  expect(state.sessionCreates).toBe(1);
  expect(
    state.calls.some(
      (call) => call.path === "/upload-sessions/S1" && call.method === "GET",
    ),
  ).toBe(true);
  const puts = state.calls.filter(
    (call) => call.method === "PUT" && call.path.endsWith("/chunks"),
  );
  expect(puts).toHaveLength(2);
  expect(puts[0].headers["idempotency-key"]).toBe(
    puts[1].headers["idempotency-key"],
  );
  const offsets = state.calls
    .filter((call) => call.method === "PUT")
    .map((call) => (call.raw ? call.raw.length : 0));
  expect(offsets[0]).toBe(offsets[1]);
});

test("processing cancellation records a request, then polls the real final result instead of claiming an immediate cancel", async ({
  page,
}) => {
  const state = await installUploadApi(page, {
    assetReady: false,
    normalized: false,
  });
  await page.goto(appUrl("/u/new"));
  await page.locator('input[type="file"]').setInputFiles({
    name: "资料.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4 fake"),
  });
  const cancel = page.getByRole("button", { name: "取消任务" });
  await expect(cancel).toBeVisible({ timeout: 8000 });
  await cancel.click();
  await expect(
    page.getByText("取消申请已登记，等待当前处理结束；最终是否取消以服务端结果为准。"),
  ).toBeVisible();
  await expect
    .poll(() =>
      state.calls.some(
        (call) => call.path === "/assets/A1/cancel" && call.method === "POST",
      ),
    )
    .toBe(true);
  await expect(
    page.getByText("任务已取消（服务端最终结果）"),
  ).toBeVisible({ timeout: 15000 });
});


test("requested slot and auto-close deadline are shown before settlement; unpaid orders cannot open after-sales", async ({
  page,
}) => {
  await installApi(page, {
    order: orderFixture({
      status: "PENDING_CONFIRMATION",
      settlement: null,
      closeAt: "2026-10-31T12:00:00.000Z",
      allowedActions: ["ORDER_VIEW", "ORDER_CANCEL_PENDING"],
    }),
  });
  await page.goto(appUrl("/u/orders/ORD-1"));
  await expect(page.getByText("申请，未保证名额")).toBeVisible();
  await expect(
    page.getByText(/未人工确认收款，订单将按配置期限自动关闭/),
  ).toBeVisible();
  await expect(
    page.getByText(/未结算订单请直接在订单费用中撤销，无需申请售后/),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "申请售后 / 取消退款" }),
  ).toHaveCount(0);
});

test("server draft restores uploaded files and preparation state, while unuploaded local files still need reselection", async ({
  page,
}) => {
  await installUploadApi(page, { preparation: true, assetReady: true });
  await page.goto(appUrl("/u/new"));
  await expect(
    page.getByText(/发现服务端保存的打印草稿/),
  ).toBeVisible();
  await page.getByRole("button", { name: "恢复这份草稿" }).click();
  await expect(page).toHaveURL(/\/u\/review$/);
  await expect(
    page.getByRole("heading", {
      name: "最后检查一下，就可以下单了",
    }),
  ).toBeVisible();
  await expect(page.getByText("确认最终打印文件")).toBeVisible();
  await expect(
    page.getByRole("button", { name: /查看最终 PDF/ }),
  ).toBeVisible();
  // A price-version change requires a fresh amount confirmation.
  await expect(
    page.getByText(/价格版本已由 v3 更新为 v4/),
  ).toBeVisible();
  await expect(page.getByText(/本次报价价格版本 v4/)).toBeVisible();
  await expect(page.getByText(/有效期至/)).toBeVisible();
  // Contact fields are intentionally not restored from the server draft.
  await expect(page.getByLabel("微信昵称 *")).toHaveValue("");
});


test("Codex routes /u/orders/:id/after-sales and /u/account/data render the exported V1 pages", async ({
  page,
}) => {
  await installApi(page, { cases: [] });
  await page.goto(appUrl("/u/orders/ORD-1/after-sales"));
  await expect(
    page.getByRole("heading", { level: 1, name: "售后与退款" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "申请售后 / 取消退款" }),
  ).toBeVisible();

  await page.goto(appUrl("/u/account/data"));
  await expect(
    page.getByRole("heading", { name: "个人资料与账号数据" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "申请导出我的资料" }),
  ).toBeVisible();
});
