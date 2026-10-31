#!/usr/bin/env node
/**
 * Add the *frontend compatibility aliases* on top of the authoritative OpenAPI
 * document produced by scripts/generate-contract.mjs.
 *
 * This script must never add, remove or guess a backend route. Its only job is
 * to expose a small set of names that published frontend modules already
 * import, pointing each one at the real backend DTO. Every alias carries a
 * provenance description so it cannot be mistaken for a backend resource.
 *
 * Run `npm run contract:generate` from the repository root.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const docsPath = resolve(root, "docs/openapi.yaml");

const ALIASES = {
  Quote: "QuoteDTO",
  VolumePolicy: "VolumePolicyDTO",
  Coupon: "CouponDTO",
  PriceLine: "PriceLineDTO",
  CustomerAccount: "CustomerAccountView",
  CustomerSession: "CustomerSessionView",
  StaffSession: "StaffSessionView",
  OrderAccess: "OrderAccessView",
  AccessLink: "AccessLinkView",
  MailConfig: "MailConfigDTO",
  MailTemplate: "MailTemplateDTO",
  Campaign: "CampaignDTO",
  DashboardSummary: "DashboardView",
};

function aliasDescription(target) {
  return `前端兼容别名，实际后端 DTO 为 ${target}。仅用于保持已发布前端类型的导入兼容，不新增后端路由或行为。`;
}

function requireVariant(object, name, path) {
  let value = object;
  for (const part of path.split(".")) value = value?.[part];
  if (!value) throw new Error(`无法生成 ${name} 别名：实际 schema 缺少 ${path}`);
  return structuredClone(value);
}

function unique(values) {
  return [...new Set(values.filter(Boolean))];
}

export function extendContract(doc) {
  const schemas = doc.components?.schemas;
  if (!schemas) throw new Error("docs/openapi.yaml 缺少 components.schemas。");

  for (const [alias, target] of Object.entries(ALIASES)) {
    if (!schemas[target])
      throw new Error(`无法生成 ${alias} 别名：实际 DTO ${target} 不存在。`);
    schemas[alias] = {
      $ref: `#/components/schemas/${target}`,
      description: aliasDescription(target),
    };
  }

  const customerRole =
    schemas.CustomerSessionView?.properties?.role?.const ?? "CUSTOMER";
  const staffRoles = schemas.StaffSessionView?.properties?.role?.enum ?? [];
  const roleValues = unique([customerRole, ...staffRoles]);
  if (!roleValues.length)
    throw new Error("无法生成 Role 别名：会话 DTO 未声明角色。");
  schemas.Role = {
    type: "string",
    enum: roleValues,
    description:
      "前端兼容别名。由实际 CustomerSessionView.role 与 StaffSessionView.role 的枚举值派生，并非独立后端 DTO。",
  };

  const orderStatuses = requireVariant(
    schemas,
    "OrderStatus",
    "OrderView.properties.status.enum",
  );
  schemas.OrderStatus = {
    type: "string",
    enum: orderStatuses,
    description:
      "前端兼容别名。复制自实际 OrderView.status 的枚举值，并非独立后端 DTO。",
  };

  // The published frontend still names the mail-template restore payload
  // TemplateRestoreInput; the backend calls the same shape MailReasonInput.
  if (!schemas.MailReasonInput)
    throw new Error("无法生成 TemplateRestoreInput 别名：MailReasonInput 不存在。");
  schemas.TemplateRestoreInput = {
    $ref: "#/components/schemas/MailReasonInput",
    description:
      "前端兼容别名，实际后端恢复模板请求体为 MailReasonInput；仅保留类型导入兼容，不对应独立路由。",
  };

  // Compatibility-only flat export form used by the frontend. The backend
  // routes use PromotionExportCreate (promotions) and ExportCreate (orders /
  // finance), so this alias must not be treated as a request body of either.
  const filters = requireVariant(
    schemas,
    "ExportInput",
    "PromotionExportFilters",
  );
  filters.additionalProperties = false;
  const fields = requireVariant(
    schemas,
    "ExportInput",
    "PromotionExportCreate.properties.fields",
  );
  const reason = requireVariant(
    schemas,
    "ExportInput",
    "PromotionExportCreate.properties.reason",
  );
  schemas.ExportInput = {
    type: "object",
    additionalProperties: false,
    required: ["reason", "fields"],
    properties: { filters, fields, reason },
    description:
      "前端兼容别名，仅用于已发布前端导出表单的类型导入；实际后端请求体是 PromotionExportCreate / ExportCreate。filters 复制自实际 PromotionExportFilters。",
  };

  return doc;
}

export function main() {
  const doc = JSON.parse(readFileSync(docsPath, "utf8"));
  extendContract(doc);
  writeFileSync(docsPath, `${JSON.stringify(doc, null, 2)}\n`, "utf8");
  console.log(
    `docs/openapi.yaml: added ${Object.keys(ALIASES).length + 4} provenance-annotated frontend aliases`,
  );
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(resolve(process.argv[1])).href
)
  main();
