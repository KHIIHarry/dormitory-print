import { describe, expect, it } from "vitest";
import {
  parseCents,
  parseRate,
  validateTiers,
  previewVolume,
  safeExportUrl,
  mailStatus,
} from "../src/admin/growth/logic";
describe("admin growth financial and security rules", () => {
  it("converts decimal yuan exactly and rejects blank, negative and fractional cents", () => {
    expect(parseCents("12.30")).toBe(1230);
    expect(parseCents("0")).toBe(0);
    for (const value of ["", "-1", "0.001", "1e3", "  "])
      expect(() => parseCents(value)).toThrow();
  });
  it("supports three decimal discount digits including zero and ten", () => {
    expect(parseRate("8.555")).toBe(8555);
    expect(parseRate("0")).toBe(0);
    expect(parseRate("10")).toBe(10000);
    expect(() => parseRate("10.001")).toThrow();
    expect(() => parseRate("8.5555")).toThrow();
  });
  it("rejects duplicate thresholds and rates worsening at larger thresholds", () => {
    expect(() =>
      validateTiers([
        { id: "a", minSheets: 10, payRateBps: 9000 },
        { id: "b", minSheets: 10, payRateBps: 8000 },
      ]),
    ).toThrow();
    expect(() =>
      validateTiers([
        { id: "a", minSheets: 10, payRateBps: 8000 },
        { id: "b", minSheets: 20, payRateBps: 9000 },
      ]),
    ).toThrow();
    expect(() =>
      validateTiers([{ id: "a", minSheets: 0, payRateBps: 8000 }]),
    ).toThrow();
  });
  it("previews only eligible A4 amount with half-up rounding and deterministic ties", () => {
    const tiers = [
      { id: "b", minSheets: 20, payRateBps: 9000 },
      { id: "a", minSheets: 10, payRateBps: 9000 },
    ];
    expect(previewVolume(tiers, 20, 105, true)).toEqual({
      tierId: "b",
      discountCents: 10,
      payableCents: 95,
    });
    expect(previewVolume(tiers, 9, 105, true)).toBeNull();
    expect(previewVolume(tiers, 20, 105, false)).toBeNull();
    expect(
      previewVolume(
        [{ id: "a", minSheets: 1, payRateBps: 10000 }],
        2,
        100,
        true,
      ),
    ).toBeNull();
  });
  it("accepts only same-origin controlled promotion export URLs", () => {
    const origin = "https://print.example";
    expect(
      safeExportUrl("/api/v1/admin/promotions/exports/job/download", origin),
    ).toBe(
      "https://print.example/api/v1/admin/promotions/exports/job/download",
    );
    expect(safeExportUrl("https://evil.example/download", origin)).toBeNull();
    expect(safeExportUrl("javascript:alert(1)", origin)).toBeNull();
    expect(safeExportUrl("/api/v1/admin/customers", origin)).toBeNull();
  });
  it("allows same-origin blob exports exclusively in explicit demo mode", () => {
    const origin = "https://print.example";
    const blob = "blob:https://print.example/1234";
    expect(safeExportUrl(blob, origin)).toBeNull();
    expect(safeExportUrl(blob, origin, true)).toBe(blob);
    expect(
      safeExportUrl("blob:https://evil.example/1234", origin, true),
    ).toBeNull();
  });
  it("never describes queued or SMTP acceptance as delivery", () => {
    expect(mailStatus("QUEUED")).toContain("入队");
    expect(mailStatus("SMTP_ACCEPTED")).toContain("不代表");
  });
});

import { buildMailPatch } from "../src/admin/growth/logic";
import type { MailConfig } from "../src/growth/types";
const config: MailConfig = {
  version: 4,
  enabled: true,
  host: "smtp.example.invalid",
  port: 587,
  security: "STARTTLS",
  authEnabled: true,
  username: "operator",
  secretConfigured: true,
  fromName: "DormPrint",
  fromAddress: "sender@example.invalid",
  replyTo: "",
  timeoutSeconds: 15,
  lastTest: { status: "QUEUED", time: "2026-01-01", message: "safe" },
};
describe("SMTP patch privacy and versioning", () => {
  it("omits a blank secret and all response-only fields", () => {
    const patch = buildMailPatch(config, "", "配置调整");
    expect(patch).toMatchObject({
      expectedVersion: 4,
      reason: "配置调整",
      host: "smtp.example.invalid",
    });
    for (const key of [
      "secret",
      "secretConfigured",
      "version",
      "lastTest",
      "allowInsecure",
    ])
      expect(patch).not.toHaveProperty(key);
  });
  it("updates a secret only when supplied and clears only explicitly", () => {
    expect(buildMailPatch(config, "replacement", "轮换")).toHaveProperty(
      "secret",
      "replacement",
    );
    expect(buildMailPatch(config, "", "撤销", true)).toEqual({
      expectedVersion: 4,
      clearSecret: true,
      reason: "撤销",
    });
  });
  it("rejects insecure transport when not deployment-approved", () => {
    expect(() =>
      buildMailPatch({ ...config, security: "NONE" }, "", "调整"),
    ).toThrow();
    expect(
      buildMailPatch(
        { ...config, security: "NONE", allowInsecure: true },
        "",
        "调整",
      ),
    ).toHaveProperty("security", "NONE");
  });
});

import {
  buildTemplatePreview,
  buildTestMailBody,
  buildVolumePatch,
} from "../src/admin/growth/logic";
import type { MailTemplate, VolumePolicy } from "../src/growth/types";
describe("operation-specific admin request contracts", () => {
  it("keeps template preview free of save-only metadata", () => {
    const template = {
      key: "CUSTOMER_VERIFY_EMAIL",
      subject: "主题",
      textBody: "正文",
      htmlBody: "<p>正文</p>",
      enabled: true,
      version: 3,
      critical: true,
    } as MailTemplate;
    expect(buildTemplatePreview(template)).toEqual({
      subject: "主题",
      textBody: "正文",
      htmlBody: "<p>正文</p>",
    });
  });
  it("test mail sends only the destination and never draft content or a version", () => {
    expect(buildTestMailBody(" test@example.invalid ")).toEqual({
      to: "test@example.invalid",
    });
  });
  it("preserves the stable volume policy identity and audit reason when publishing a new version", () => {
    const policy: VolumePolicy = {
      id: "policy-stable",
      version: 4,
      enabled: false,
      effectiveAt: null,
      description: "",
      tiers: [],
    };
    const changes = {
      enabled: true,
      effectiveAt: null,
      description: "规则说明",
      tiers: [{ id: "tier-1", minSheets: 20, payRateBps: 9000 }],
    };
    expect(buildVolumePatch(policy, changes, " 配置调整 ")).toEqual({
      id: "policy-stable",
      expectedVersion: 4,
      ...changes,
      reason: "配置调整",
    });
  });
});
