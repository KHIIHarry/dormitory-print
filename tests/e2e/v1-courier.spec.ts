import { spawn, type ChildProcess } from "node:child_process";
import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * V1 courier delivery browser contracts.
 *
 * Runs the app in REAL API mode (VITE_DEMO_MODE=false) against a dedicated Vite
 * server and intercepts /api/v1 at the network boundary. It asserts the exact
 * requests the courier UI sends, the single-request atomic batch flow, field
 * retention after a refusal, and that a single proof only renders the server
 * DTO. It is a frontend contract test, not backend acceptance: the batch
 * endpoint must still be exercised against PostgreSQL.
 */

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
  const port = test.info().project.name === "mobile" ? 5205 : 5204;
  REAL_BASE = `http://127.0.0.1:${port}`;
  if (await reachable(`${REAL_BASE}/courier/`)) return;
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
    if (await reachable(`${REAL_BASE}/courier/`)) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Vite real-mode server did not start on ${REAL_BASE}`);
});

test.afterAll(() => {
  if (startedHere) server?.kill("SIGTERM");
});

test.use({ channel: "chrome", trace: "off" });

type Call = {
  method: string;
  path: string;
  body: unknown;
  headers: Record<string, string>;
};

type CourierState = {
  calls: Call[];
  batchStatus: "PUBLISHED" | "DELIVERING";
  batchVersion: number;
  orders: Record<string, unknown>[];
  batchProofStatus: number;
  singleProofStatus: number;
};

const envelope = (data: unknown) => ({ data, requestId: "e2e-courier" });
const errorEnvelope = (
  code: string,
  message: string,
  details?: Record<string, unknown>,
) => ({ error: { code, message, details }, requestId: "e2e-courier" });

const staffSession = {
  id: "staff-2",
  role: "COURIER",
  name: "验收配送员",
  username: "courier",
  mustChangePassword: false,
  emailVerified: true,
  capabilities: ["delivery.proof"],
  emailMasked: "c***@example.com",
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
  locationMaxAgeSeconds: 45,
  locationMaxAccuracyMeters: 25,
  formats: ["pdf", "doc", "docx", "jpg", "png"],
  features: {
    resumableUploads: true,
    serverDrafts: true,
    afterSales: true,
    selfDeletionRequest: true,
  },
};

const batch = (state: CourierState) => ({
  id: "batch-1",
  name: "晚间配送 · 东区",
  date: "2026-10-30",
  time: "19:00 – 20:00",
  courierId: "staff-2",
  status: state.batchStatus,
  orderIds: ["ORD-1", "ORD-2"],
  version: state.batchVersion,
});

function courierOrder(
  id: string,
  assignmentId: string,
  status: string,
  overrides: Record<string, unknown> = {},
) {
  return {
    id,
    pickupCode: id === "ORD-1" ? "A1001" : "A1002",
    version: 4,
    contentRevision: 1,
    status,
    nickname: "验收同学",
    phone: "13800138000",
    locationId: "B1",
    locationName: "知行苑 1 栋",
    locationSnapshot: "知行苑 1 栋 101",
    packingStatus: "PACKED",
    packingVersion: 2,
    packingChecklistVersion: 1,
    addressReview: null,
    deliveryAssignmentId: assignmentId,
    batchId: "batch-1",
    deliveryStatus: status === "DELIVERED" ? "DELIVERED" : "STARTED",
    assignment: {
      id: assignmentId,
      orderId: id,
      batchId: "batch-1",
      batchName: "晚间配送 · 东区",
      courierId: "staff-2",
      courierName: "验收配送员",
      status: status === "DELIVERED" ? "DELIVERED" : "STARTED",
      version: 1,
      addressReview: null,
      locationSnapshot: "知行苑 1 栋 101",
      startedAt: "2026-10-30T10:00:00.000Z",
      deliveredAt: status === "DELIVERED" ? "2026-10-30T11:00:00.000Z" : null,
    },
    proof: null,
    requestedSlot: { id: "S1", name: "晚间配送", start: "19:00", end: "20:00" },
    confirmedSlot: { id: "S1", name: "晚间配送", start: "19:00", end: "20:00" },
    allowedActions:
      status === "DELIVERING" ? ["SUBMIT_PROOF", "REPORT_EXCEPTION"] : [],
    blockers: [],
    history: [],
    ...overrides,
  };
}

function approvedException(assignmentId = "asg-1", assignmentVersion = 1) {
  return {
    id: `exception-${assignmentId}`,
    status: "APPROVED",
    assignmentId,
    assignmentVersion,
    reason: "设备无法定位，管理员已核验",
    failureCode: "DEVICE_UNAVAILABLE",
    approvedAt: "2026-10-30T10:30:00.000Z",
  };
}

function initialState(
  batchStatus: "PUBLISHED" | "DELIVERING" = "DELIVERING",
): CourierState {
  return {
    calls: [],
    batchStatus,
    batchVersion: batchStatus === "PUBLISHED" ? 5 : 6,
    orders: [
      courierOrder("ORD-1", "asg-1", "DELIVERING"),
      courierOrder("ORD-2", "asg-2", "DELIVERING"),
    ],
    batchProofStatus: 200,
    singleProofStatus: 200,
  };
}

async function installApi(page: Page, state: CourierState) {
  const json = (
    route: Route,
    data: unknown,
    status = 200,
    details?: Record<string, unknown>,
  ) =>
    route.fulfill({
      status,
      headers: {
        "content-type": "application/json",
        "cache-control": "no-store",
      },
      body: JSON.stringify(
        status >= 400
          ? errorEnvelope(
              (data as { code: string }).code,
              (data as { message: string }).message,
              details,
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

    if (path === "/auth/me") return json(route, staffSession);
    if (path === "/courier/auth/me") return json(route, staffSession);
    if (path === "/public/config") return json(route, publicConfig);
    if (path === "/public/map")
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
    if (path === "/courier/batches" && method === "GET")
      return json(route, { items: [batch(state)] });
    if (path === "/courier/batches/batch-1" && method === "GET")
      return json(route, {
        batch: batch(state),
        orders: state.orders,
        buildings: [{ id: "B1", name: "知行苑 1 栋", type: "DORMITORY" }],
      });
    if (path === "/courier/batches/batch-1/start" && method === "POST") {
      state.batchStatus = "DELIVERING";
      state.batchVersion = 6;
      state.orders = state.orders.map((order) => ({
        ...order,
        status: "DELIVERING",
      }));
      return json(route, {
        action: "START",
        batches: [batch(state)],
        orders: state.orders,
      });
    }
    if (path === "/assets" && method === "POST")
      return json(route, {
        assetId: "asset-1",
        url: "https://example.test/shared-proof.jpg",
        status: "READY",
        purpose: "DELIVERY_PROOF",
      });
    if (
      path === "/courier/batches/batch-1/delivery-proofs/batch" &&
      method === "POST"
    ) {
      if (state.batchProofStatus >= 400)
        return json(
          route,
          {
            code: "VERSION_CONFLICT",
            message: "订单状态已变化，整批未提交",
          },
          409,
          { blockers: ["VERSION_CONFLICT"] },
        );
      state.orders = state.orders.map((order) => ({
        ...order,
        status: "DELIVERED",
        deliveryStatus: "DELIVERED",
        proof: {
          assignmentId: order.deliveryAssignmentId,
          deliveredAt: "2026-10-30T11:00:00.000Z",
          placementNote: "一楼取件架第二层",
        },
        allowedActions: [],
      }));
      return json(route, {
        batchId: "batch-1",
        proofs: state.orders.map((order) => ({
          assignmentId: order.deliveryAssignmentId,
          deliveredAt: "2026-10-30T11:00:00.000Z",
        })),
        orders: state.orders,
      });
    }
    if (path === "/courier/orders/ORD-1/proof" && method === "POST") {
      if (state.singleProofStatus >= 400)
        return json(
          route,
          { code: "LOCATION_REQUIRED", message: "必须提交真实定位" },
          422,
          { blockers: ["LOCATION_REQUIRED"] },
        );
      await new Promise((resolve) => setTimeout(resolve, 800));
      state.orders = state.orders.map((order) =>
        order.id === "ORD-1"
          ? {
              ...order,
              status: "DELIVERED",
              deliveryStatus: "DELIVERED",
              version: 5,
              proof: {
                id: "proof-1",
                orderId: "ORD-1",
                assignmentId: "asg-1",
                photoUrl: null,
                skipPhotoReason: "客户要求放门口",
                placementNote: "门口鞋柜",
                deliveredAt: "2026-10-30T11:00:00.000Z",
              },
            }
          : order,
      );
      return json(route, {
        order: state.orders.find((order) => order.id === "ORD-1"),
        proof: {
          id: "proof-1",
          orderId: "ORD-1",
          assignmentId: "asg-1",
          photoAssetId: null,
          skipPhotoReason: "客户要求放门口",
          placementNote: "门口鞋柜",
          deliveredAt: "2026-10-30T11:00:00.000Z",
          operatorId: "staff-2",
        },
      });
    }
    return json(
      route,
      { code: "NOT_FOUND", message: `未覆盖的接口 ${method} ${path}` },
      404,
    );
  });
}

async function boot(page: Page, state: CourierState) {
  await page.addInitScript(() => {
    const result = {
      coords: {
        latitude: 31.2304,
        longitude: 121.4737,
        accuracy: 12,
        altitude: null,
        altitudeAccuracy: null,
        heading: null,
        speed: null,
      },
      timestamp: Date.now(),
    } as GeolocationPosition;
    Object.defineProperty(navigator, "geolocation", {
      configurable: true,
      value: {
        getCurrentPosition: (
          success: (value: GeolocationPosition) => void,
          error?: (value: GeolocationPositionError) => void,
        ) => {
          void error;
          success(result);
        },
        watchPosition: () => 1,
        clearWatch: () => undefined,
      },
    });
  });
  await installApi(page, state);
  await page.goto(appUrl("/courier/batches/batch-1"));
  await expect(page.getByText("知行苑 1 栋")).toBeVisible();
}

test("starts a published batch once and opens batch proof after the server confirms", async ({
  page,
}) => {
  const state = initialState("PUBLISHED");
  await boot(page, state);

  await expect(page.getByText("批次已发布，确认后整批开始配送")).toBeVisible();
  await page.getByRole("button", { name: "开始整批配送" }).click();

  await expect(
    page.getByRole("button", { name: "批量送达本楼栋" }),
  ).toBeVisible();
  const starts = state.calls.filter(
    (call) => call.path === "/courier/batches/batch-1/start",
  );
  expect(starts).toHaveLength(1);
  expect(starts[0].body).toEqual({ expectedVersion: 5 });
});

test("submits one shared-photo atomic batch request with per-order assignment ids", async ({
  page,
}) => {
  const state = initialState("DELIVERING");
  await boot(page, state);

  await page.getByRole("button", { name: "批量送达本楼栋" }).click();
  const first = page.locator(".batch-proof-order").filter({ hasText: "A1001" });
  const second = page
    .locator(".batch-proof-order")
    .filter({ hasText: "A1002" });
  await first.locator('input[type="checkbox"]').first().check();
  await second.locator('input[type="checkbox"]').first().check();

  await page
    .locator('input[type="file"].batch-proof-file')
    .setInputFiles("public/assets/icon-512.png");
  await expect(page.getByAltText("批量交付照片预览")).toBeVisible();

  await page.getByRole("button", { name: "获取当前位置" }).click();
  await expect(page.getByText("已选 2 单")).toBeVisible();
  await page.locator(".batch-proof textarea").last().fill("一楼取件架第二层");

  await page.getByRole("button", { name: "确认批量送达（2 单）" }).click();
  await expect(
    page.getByText("服务端已确认本楼栋批量送达，订单状态已更新"),
  ).toBeVisible();

  const proofs = state.calls.filter(
    (call) =>
      call.path === "/courier/batches/batch-1/delivery-proofs/batch" &&
      call.method === "POST",
  );
  expect(proofs).toHaveLength(1);
  const payload = proofs[0].body as {
    photoAssetId: string;
    placementNote: string;
    items: {
      orderId: string;
      deliveryAssignmentId: string;
      expectedVersion: number;
      expectedAssignmentVersion?: number;
      position?: { accuracyMeters: number; coordinateSystem: string };
    }[];
  };
  expect(payload.photoAssetId).toBe("asset-1");
  expect(payload.placementNote).toBe("一楼取件架第二层");
  expect(payload.items.map((item) => item.orderId)).toEqual(["ORD-1", "ORD-2"]);
  expect(payload.items.map((item) => item.deliveryAssignmentId)).toEqual([
    "asg-1",
    "asg-2",
  ]);
  expect(payload.items.every((item) => item.expectedVersion === 4)).toBe(true);
  expect(
    payload.items.every((item) => item.expectedAssignmentVersion === 1),
  ).toBe(true);
  expect(payload.items[0].position).toMatchObject({
    accuracyMeters: 12,
    coordinateSystem: "WGS84",
  });
  expect(
    state.calls.some(
      (call) =>
        call.method === "POST" &&
        /\/courier\/orders\/.+\/proof$/.test(call.path),
    ),
  ).toBe(false);
});

test("a refused batch keeps every field and never falls back to single proofs", async ({
  page,
}) => {
  const state = initialState("DELIVERING");
  state.batchProofStatus = 409;
  await boot(page, state);

  await page.getByRole("button", { name: "批量送达本楼栋" }).click();
  const first = page.locator(".batch-proof-order").filter({ hasText: "A1001" });
  const second = page
    .locator(".batch-proof-order")
    .filter({ hasText: "A1002" });
  await first.locator('input[type="checkbox"]').first().check();
  await second.locator('input[type="checkbox"]').first().check();
  await page
    .locator('input[type="file"].batch-proof-file')
    .setInputFiles("public/assets/icon-512.png");
  await expect(page.getByAltText("批量交付照片预览")).toBeVisible();
  await page.getByRole("button", { name: "获取当前位置" }).click();
  const note = page.locator(".batch-proof textarea").last();
  await note.fill("提交失败也要保留这段说明");

  await page.getByRole("button", { name: "确认批量送达（2 单）" }).click();
  await expect(
    page.getByText("本次批量提交未生效，所有订单保持原状态"),
  ).toBeVisible();

  expect(
    await first.locator('input[type="checkbox"]').first().isChecked(),
  ).toBe(true);
  expect(
    await second.locator('input[type="checkbox"]').first().isChecked(),
  ).toBe(true);
  await expect(note).toHaveValue("提交失败也要保留这段说明");
  await expect(page.getByAltText("批量交付照片预览")).toBeVisible();
  const detailRefetches = state.calls.filter(
    (call) => call.method === "GET" && call.path === "/courier/batches/batch-1",
  );
  expect(detailRefetches.length).toBeGreaterThanOrEqual(2);
  expect(
    state.calls.some(
      (call) =>
        call.method === "POST" &&
        /\/courier\/orders\/.+\/proof$/.test(call.path),
    ),
  ).toBe(false);
});

test("batch approval comes from the server, never from a self-asserted checkbox", async ({
  page,
}) => {
  const state = initialState("DELIVERING");
  state.orders = [
    courierOrder("ORD-1", "asg-1", "DELIVERING", {
      locationExceptionApproval: approvedException(),
    }),
  ];
  await boot(page, state);
  await page.getByRole("button", { name: "批量送达本楼栋" }).click();

  await expect(
    page.getByText("服务端已批准定位例外 #exception-asg-1"),
  ).toBeVisible();
  await expect(
    page.getByText("该订单已获管理员定位例外批准（本次执行）"),
  ).toHaveCount(0);
  await expect(
    page.getByText("本次全部订单按各自当前配送执行的服务端批准提交"),
  ).toBeVisible();

  await page
    .locator('input[type="file"].batch-proof-file')
    .setInputFiles("public/assets/icon-512.png");
  await expect(page.getByAltText("批量交付照片预览")).toBeVisible();
  await page.getByRole("button", { name: "确认批量送达（1 单）" }).click();
  await expect(
    page.getByText("服务端已确认本楼栋批量送达，订单状态已更新"),
  ).toBeVisible();

  const proofs = state.calls.filter(
    (call) =>
      call.method === "POST" &&
      call.path === "/courier/batches/batch-1/delivery-proofs/batch",
  );
  expect(proofs).toHaveLength(1);
  const item = (proofs[0].body as { items: Record<string, unknown>[] })
    .items[0];
  expect(item.deliveryAssignmentId).toBe("asg-1");
  expect(item.position).toBeUndefined();
  expect(item.skipLocationReason).toBeUndefined();
  expect(item.locationFailureCode).toBeUndefined();
});

test("single proof offers the approved exception only from the server DTO", async ({
  page,
}) => {
  const state = initialState("DELIVERING");
  state.orders = [
    courierOrder("ORD-1", "asg-1", "DELIVERING", {
      locationExceptionApproval: approvedException(),
    }),
    courierOrder("ORD-2", "asg-2", "DELIVERING"),
  ];
  await boot(page, state);
  await page.goto(appUrl("/courier/orders/ORD-1/proof"));
  await expect(
    page.getByRole("heading", { name: "完成这次交付" }),
  ).toBeVisible();
  await expect(
    page.getByText("该订单已获管理员定位例外批准（本次配送执行）"),
  ).toHaveCount(0);
  const approvedControl = page.getByLabel(
    "使用服务端已批准的定位例外（本次配送执行）",
  );
  await expect(approvedControl).toBeVisible();
  await approvedControl.check();

  await page.getByLabel("因特殊情况无法拍照，填写免拍照原因").check();
  await page
    .getByLabel("免拍照原因（必填、独立记录，客户凭证可见）")
    .fill("设备无法定位且已获批准");
  await page.getByRole("button", { name: "确认资料已送达" }).click();
  await expect(page.getByText("服务端已确认交付，凭证已更新")).toBeVisible({
    timeout: 5000,
  });

  const proofs = state.calls.filter(
    (call) =>
      call.method === "POST" && call.path === "/courier/orders/ORD-1/proof",
  );
  expect(proofs).toHaveLength(1);
  expect(proofs[0].body).toMatchObject({
    deliveryAssignmentId: "asg-1",
    skipPhotoReason: "设备无法定位且已获批准",
    position: null,
    skipLocationReason: null,
    locationFailureCode: null,
  });
});

test("single proof waits for the server DTO and uses the real assignment id", async ({
  page,
}) => {
  const state = initialState("DELIVERING");
  await boot(page, state);
  await page.goto(appUrl("/courier/orders/ORD-1/proof"));
  await expect(
    page.getByRole("heading", { name: "完成这次交付" }),
  ).toBeVisible();

  await page.getByLabel("因特殊情况无法拍照，填写免拍照原因").check();
  await page
    .getByLabel("免拍照原因（必填、独立记录，客户凭证可见）")
    .fill("客户要求放门口");
  await page.getByRole("button", { name: "获取当前位置" }).click();
  await page
    .getByLabel("方便同学找到资料（选填，客户凭证可见）")
    .fill("门口鞋柜");

  await page.getByRole("button", { name: "确认资料已送达" }).click();
  await expect(
    page.getByRole("button", { name: "等待服务端确认…" }),
  ).toBeVisible();
  await expect(page.getByText("服务端已确认交付，凭证已更新")).toBeVisible({
    timeout: 5000,
  });

  const proofs = state.calls.filter(
    (call) =>
      call.method === "POST" && call.path === "/courier/orders/ORD-1/proof",
  );
  expect(proofs).toHaveLength(1);
  expect(proofs[0].body).toMatchObject({
    deliveryAssignmentId: "asg-1",
    skipPhotoReason: "客户要求放门口",
    placementNote: "门口鞋柜",
    expectedVersion: 4,
  });
  expect(
    (proofs[0].body as { deliveryAssignmentId: string }).deliveryAssignmentId,
  ).not.toBe("batch-1");
  await expect(page.getByText("此订单已完成交付")).toBeVisible();
});
