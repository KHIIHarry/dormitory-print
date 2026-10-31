import { readFileSync } from "node:fs";
import { describe, it, expect } from "vitest";
import { adminResources, adminNavAllowed } from "../src/admin/resources";
const doc = JSON.parse(readFileSync("docs/openapi.yaml", "utf8"));
const s = doc.components.schemas;
describe("v1.3共享契约与按路由资源", () => {
  it("员工创建、编辑与结算撤回都有版本化契约", () => {
    expect(
      doc.paths["/admin/staff"].post.requestBody.content["application/json"]
        .schema.$ref,
    ).toContain("StaffCreateInput");
    expect(
      doc.paths["/admin/staff/{staff_id}"].patch.requestBody.content[
        "application/json"
      ].schema.$ref,
    ).toContain("StaffUpdateInput");
    expect(s.StaffUpdateInput.required).toContain("expectedVersion");
    expect(
      doc.paths["/preparations/{preparation_id}/withdraw-checkout"].post,
    ).toBeDefined();
  });
  it("导出及恢复默认模板接受真实的审计原因和筛选字段", () => {
    expect(s.ExportInput.required).toContain("reason");
    expect(s.ExportInput.properties.filters.additionalProperties).toBe(false);
    expect(Object.keys(s.ExportInput.properties.filters.properties)).toEqual(
      expect.arrayContaining(["q", "type", "status", "riskHold"]),
    );
    expect(s.TemplateRestoreInput.$ref).toContain("ReasonInput");
  });
  it("邀请规则及全量金额汇总具备明确字段", () => {
    expect(s.CampaignInput.required).toEqual(
      expect.arrayContaining([
        "qualificationExpiresAt",
        "qualificationMinSpendCents",
        "qualificationService",
      ]),
    );
    expect(s.DashboardView.required).toEqual(
      expect.arrayContaining([
        "activeOrderCount",
        "couponDiscountCents",
        "volumeDiscountCents",
        "cashReceivedCents",
        "refundCents",
        "manualAdjustmentCents",
      ]),
    );
  });
  it("账号与敏感增量模块不预读不相关的后台资源", () => {
    expect(adminResources("/manage/settings/mail")).toEqual([]);
    expect(adminResources("/manage/settings/mail-templates")).toEqual([]);
    expect(adminResources("/manage/customers")).toEqual([]);
    expect(adminResources("/manage/account")).toEqual([]);
    expect(adminResources("/manage/staff", ["staff.manage"])).toEqual(["staff"]);
    expect(adminResources("/manage/print/task-123", ["production.manage"])).toEqual([
      "orders",
      "config",
    ]);
    expect(adminResources("/manage/batches/b1", ["*"])).toEqual([
      "orders",
      "batches",
      "staff",
    ]);
    expect(adminResources("/manage")).not.toContain("audit");
    expect(adminNavAllowed("/manage/settings/mail", ["mail.manage"])).toBe(
      true,
    );
    expect(adminNavAllowed("/manage/staff", ["mail.manage"])).toBe(false);
  });
});
