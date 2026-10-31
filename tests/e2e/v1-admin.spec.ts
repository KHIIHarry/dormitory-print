import { test, expect } from "@playwright/test";
import { existsSync } from "node:fs";
import {
  configSectionPayload,
  afterSaleReviewPayload,
  reprintPayload,
  packPayload,
  exportPayload,
  staffCapabilityPayload,
  assignmentIdForOrder,
  type AfterSaleCase,
  type PrintTaskView,
  type PackingTaskView,
} from "../../src/v1/admin-api";

// The local machine has no matching ms-playwright download; CI keeps the
// repository default instead of hard-coding a platform-specific browser path.
const systemChrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
test.use(
  existsSync(systemChrome)
    ? { launchOptions: { executablePath: systemChrome } }
    : {},
);

const baseConfig = {
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
  qrUrl: "/api/v1/assets/qr-1/content",
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
} as any;

test.describe("V1 admin strict request contracts", () => {
  test("prices patch only carries price fields and the server version", () => {
    const body = configSectionPayload("prices", baseConfig, 4);
    expect(body).toEqual({
      expectedVersion: 4,
      bwSingle: null,
      bwDuplex: 30,
      colorSingle: 100,
      colorDuplex: null,
      photo: 200,
      corner: 50,
      side: 100,
    });
    expect(body).not.toHaveProperty("serviceEnabled");
    expect(body).not.toHaveProperty("wechat");
  });

  test("services and workflow patches are section scoped", () => {
    const services = configSectionPayload("services", baseConfig, 2);
    expect(services.expectedVersion).toBe(2);
    expect(services).toMatchObject({
      serviceEnabled: true,
      bindingLimits: { CORNER: 40, SIDE: 60 },
      formats: ["pdf", "docx"],
    });
    expect(services).not.toHaveProperty("prices");
    const workflow = configSectionPayload("workflow", baseConfig, 7);
    expect(workflow).toMatchObject({
      expectedVersion: 7,
      unpaidTimeoutMinutes: 60,
      pauses: {
        newOrders: false,
        fileProcessing: false,
        production: true,
        delivery: false,
      },
    });
    expect(workflow).not.toHaveProperty("announcement");
  });

  test("retention is fixed-policy and refuses invented values", () => {
    const body = configSectionPayload("retention", baseConfig, 3);
    expect(body).toEqual({
      expectedVersion: 3,
      sourceDays: 7,
      finalDays: 30,
      proofDays: 30,
      draftHours: 24,
      exportHours: 24,
      trashHours: 24,
    });
    expect(() =>
      configSectionPayload("retention", { ...baseConfig, sourceDays: 1 }, 3),
    ).toThrow();
  });

  test("after-sale review keeps suggestion and human override distinct", () => {
    const item: AfterSaleCase = {
      id: "as-1",
      orderId: "o-1",
      version: 3,
      status: "REVIEWING",
      source: "ADMIN",
      responsibility: "MERCHANT",
      solution: "REFUND",
      reason: "漏印",
      suggestedCents: 1200,
      approvedCents: null,
      adjustmentBasis: "",
      data: {},
      createdAt: "2026-10-30T10:00:00Z",
    };
    const body = afterSaleReviewPayload(item, {
      decision: "APPROVE",
      solution: "REFUND",
      approvedCents: 1500,
      adjustmentBasis: "按影响 3 份重算",
      reason: "核实漏印",
    });
    expect(body).toEqual({
      expectedVersion: 3,
      decision: "APPROVE",
      solution: "REFUND",
      approvedCents: 1500,
      adjustmentBasis: "按影响 3 份重算",
      reason: "核实漏印",
    });
    expect(() =>
      afterSaleReviewPayload(item, {
        decision: "APPROVE",
        solution: "REFUND",
        approvedCents: 1500,
        adjustmentBasis: "",
        reason: "未写依据",
      }),
    ).toThrow(/依据/);
    const keepSuggestion = afterSaleReviewPayload(item, {
      decision: "APPROVE",
      solution: "REFUND",
      approvedCents: 1200,
      reason: "按建议金额",
    });
    expect(keepSuggestion).not.toHaveProperty("approvedCents");
    expect(keepSuggestion).not.toHaveProperty("adjustmentBasis");
    expect(() =>
      afterSaleReviewPayload(item, {
        decision: "APPROVE",
        solution: "REPRINT",
        reason: "改方案",
      }),
    ).toThrow(/客户/);
    expect(() =>
      afterSaleReviewPayload(
        { ...item, responsibility: "CUSTOMER" },
        {
          decision: "APPROVE",
          solution: "REFUND",
          approvedCents: 100,
          adjustmentBasis: "部分退",
          reason: "自身原因",
        },
      ),
    ).toThrow(/全额/);
  });

  test("reprints use server task id and physical sheet ids", () => {
    const task = {
      id: "task-1",
      orderVersion: 9,
      sheetCatalog: [
        { sheetId: "c1-s1" },
        { sheetId: "c1-s2" },
        { sheetId: "c2-s1" },
      ],
    } as PrintTaskView;
    expect(reprintPayload(task, "SHEETS", ["c1-s1", "c1-s1", "c2-s1"], "缺页")).toEqual({
      expectedVersion: 9,
      mode: "SHEETS",
      sheetIds: ["c1-s1", "c2-s1"],
      reason: "缺页",
    });
    expect(() => reprintPayload(task, "SHEETS", [], "缺页")).toThrow();
    expect(reprintPayload(task, "WHOLE", [], "整任务重打")).toMatchObject({
      mode: "WHOLE",
      sheetIds: [],
    });
  });

  test("packing sends the current checklist version and server file ids", () => {
    const packing = {
      id: "pack-1",
      orderId: "o-1",
      checklistVersion: 6,
      checklist: {
        version: 6,
        items: [{ fileId: "f-1" }, { fileId: "f-2" }],
      },
    } as PackingTaskView;
    expect(packPayload(packing, 12, ["f-1", "f-2"])).toEqual({
      expectedVersion: 12,
      checklistVersion: 6,
      checks: [
        { fileId: "f-1", confirmed: true },
        { fileId: "f-2", confirmed: true },
      ],
    });
    expect(() => packPayload(packing, 12, ["f-1"])).toThrow(/清单/);
  });

  test("delivery proof must use the assignment id, not the batch id", () => {
    const batch = { orderIds: ["o-1", "o-2"], assignmentIds: ["a-1", "a-2"] };
    expect(assignmentIdForOrder(batch, "o-2")).toBe("a-2");
    expect(assignmentIdForOrder(batch, "o-3")).toBeNull();
    expect(assignmentIdForOrder(null, "o-1")).toBeNull();
  });

  test("exports only accept orders or finance kinds", () => {
    expect(
      exportPayload("FINANCE", { status: "DELIVERED" }, ["orderId", "amountCents"], "对账"),
    ).toEqual({
      kind: "FINANCE",
      filters: { status: "DELIVERED" },
      fields: ["orderId", "amountCents"],
      reason: "对账",
    });
    expect(() => exportPayload("PROMOTIONS" as any, {}, ["id"], "x")).toThrow();
  });

  test("staff capability edits keep expected version and reason", () => {
    expect(
      staffCapabilityPayload({ id: "s-1", version: 5 } as any, ["config.manage"], "授权配置"),
    ).toEqual({
      expectedVersion: 5,
      capabilities: ["config.manage"],
      reason: "授权配置",
    });
    expect(() =>
      staffCapabilityPayload({ id: "s-1", version: 5 } as any, ["config.manage"], ""),
    ).toThrow(/原因/);
  });
});

test("admin settings exposes section, workflow, retention and pause controls", async ({
  page,
}) => {
  await page.goto("/preview");
  await page.getByRole("button", { name: "打开管理端", exact: true }).click();
  if (test.info().project.name === "mobile") {
    await page.getByRole("button", { name: "展开导航", exact: true }).click();
    await expect(page.getByRole("button", { name: "展开导航", exact: true })).toHaveAttribute("aria-expanded", "true");
  }
  await page.getByRole("button", { name: "系统与团队" }).click();
  await page.getByRole("link", { name: "系统配置", exact: true }).click();
  await expect(page.getByRole("heading", { name: "系统配置" })).toBeVisible();
  for (const label of ["价目", "服务与限制", "打印文件校准", "微信与联系", "公告与营业"]) {
    await expect(page.getByRole("button", { name: new RegExp(label) })).toBeVisible();
  }
  await page.getByRole("button", { name: /打印文件校准/ }).click();
  const a4Heading = page.getByRole("heading", { name: "A4 文件打印" });
  const photoHeading = page.getByRole("heading", { name: "5 寸照片校准" });
  await expect(a4Heading).toBeVisible();
  await expect(photoHeading).toBeVisible();
  const a4Box = await a4Heading.boundingBox();
  const photoBox = await photoHeading.boundingBox();
  expect(a4Box && photoBox && a4Box.y < photoBox.y).toBe(true);
  await page.getByRole("button", { name: /工作流/ }).click();
  await expect(page.getByText("待付款自动关闭")).toBeVisible();
  await expect(page.getByText("暂停新订单")).toBeVisible();
  await page.getByRole("button", { name: /保留策略/ }).click();
  await expect(page.getByText("原件与中间 PDF")).toBeVisible();
});
