import { describe, expect, it } from "vitest";
import { configSectionPayload } from "../src/v1/admin-api";
import { buildMailPatch, type MailDraft } from "../src/admin/growth/logic";

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
};

describe("config numeric drafts", () => {
  it("keeps blank prices as unconfigured instead of coercing them to zero", () => {
    const body = configSectionPayload(
      "prices",
      { ...config, prices: { ...config.prices, bwSingle: null, photo: "" } },
      4,
    );
    expect(body).toMatchObject({
      expectedVersion: 4,
      bwSingle: null,
      photo: null,
    });
  });

  it("accepts complete service limits and coerces numeric strings", () => {
    const body = configSectionPayload(
      "services",
      { ...config, maxFiles: "25", bindingLimits: { CORNER: null, SIDE: 60 } },
      2,
    );
    expect(body).toMatchObject({
      expectedVersion: 2,
      maxFiles: 25,
      maxMb: 50,
      bindingLimits: { CORNER: null, SIDE: 60 },
    });
  });

  it("rejects blank, decimal and out-of-range required service limits", () => {
    expect(() =>
      configSectionPayload("services", { ...config, maxFiles: null }, 1),
    ).toThrow("单订单最多文件数不能为空");
    expect(() =>
      configSectionPayload("services", { ...config, maxMb: 1.5 }, 1),
    ).toThrow("单文件大小上限必须是整数");
    expect(() =>
      configSectionPayload("services", { ...config, maxCopies: 0 }, 1),
    ).toThrow("单任务最大份数必须在 1 到 1000 之间");
    expect(() =>
      configSectionPayload(
        "services",
        { ...config, bindingLimits: { CORNER: 0, SIDE: 60 } },
        1,
      ),
    ).toThrow("左上角单钉每册张数上限必须在 1 到");
  });

  it("keeps decimal photo calibration while rejecting blank or unsafe values", () => {
    const body = configSectionPayload(
      "photo",
      {
        ...config,
        photoWidthMm: 127.5,
        photoHeightMm: 89,
        margins: { top: 3.5, bottom: 3, left: 3, right: 3 },
      },
      9,
    );
    expect(body).toMatchObject({
      expectedVersion: 9,
      photoWidthMm: 127.5,
      margins: { top: 3.5, bottom: 3, left: 3, right: 3 },
    });
    expect(() =>
      configSectionPayload(
        "photo",
        { ...config, margins: { top: null, bottom: 3, left: 3, right: 3 } },
        9,
      ),
    ).toThrow("照片留白上不能为空");
    expect(() =>
      configSectionPayload("photo", { ...config, photoHeightMm: 501 }, 9),
    ).toThrow("纸张高度必须在 0.1 到 500 之间");
  });

  it("validates workflow limits but keeps an empty timeout as unchanged", () => {
    const body = configSectionPayload(
      "workflow",
      {
        ...config,
        unpaidTimeoutMinutes: null,
        locationMaxAgeSeconds: "300",
        shortCodeLength: 6,
      },
      3,
    );
    expect(body).toMatchObject({
      expectedVersion: 3,
      locationMaxAgeSeconds: 300,
      locationMaxAccuracyMeters: 100,
      shortCodeLength: 6,
    });
    expect(body).not.toHaveProperty("unpaidTimeoutMinutes");
    expect(() =>
      configSectionPayload(
        "workflow",
        { ...config, locationMaxAccuracyMeters: null },
        3,
      ),
    ).toThrow("定位最大误差不能为空");
    expect(() =>
      configSectionPayload("workflow", { ...config, shortCodeLength: 13 }, 3),
    ).toThrow("提取码长度必须在 5 到 12 之间");
  });
});

const mailDraft: MailDraft = {
  version: 7,
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
  lastTest: null,
};

describe("mail numeric drafts", () => {
  it("allows clearing the port while editing but requires a valid integer on save", () => {
    expect(() =>
      buildMailPatch({ ...mailDraft, port: null }, "", "调整端口"),
    ).toThrow("SMTP 端口不能为空");
    expect(() =>
      buildMailPatch({ ...mailDraft, port: 0 }, "", "调整端口"),
    ).toThrow("SMTP 端口必须是 1 到 65535 之间的整数");
    expect(() =>
      buildMailPatch({ ...mailDraft, port: 25.5 }, "", "调整端口"),
    ).toThrow("SMTP 端口必须是 1 到 65535 之间的整数");
  });

  it("keeps a re-entered port and timeout in the patch", () => {
    const body = buildMailPatch(
      { ...mailDraft, port: 587, timeoutSeconds: 20 },
      "synthetic-secret",
      "  调整 SMTP  ",
    );
    expect(body).toMatchObject({
      expectedVersion: 7,
      port: 587,
      timeoutSeconds: 20,
      reason: "调整 SMTP",
      secret: "synthetic-secret",
    });
    expect(() =>
      buildMailPatch({ ...mailDraft, timeoutSeconds: null }, "", "调整超时"),
    ).toThrow("连接超时不能为空");
  });
});
