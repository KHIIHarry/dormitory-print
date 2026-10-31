import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const root = resolve(import.meta.dirname, "..");
const docsPath = resolve(root, "docs/openapi.yaml");
const python = process.env.PYTHON || resolve(root, "backend/.venv/bin/python");

function runNode(script: string) {
  execFileSync(process.execPath, [resolve(root, script)], {
    cwd: root,
    stdio: "pipe",
    maxBuffer: 64 * 1024 * 1024,
  });
}

function generate() {
  runNode("scripts/generate-contract.mjs");
  runNode("scripts/extend-growth-contract.mjs");
  return readFileSync(docsPath, "utf8");
}

function actualSpec(): any {
  return JSON.parse(
    execFileSync(python, [resolve(root, "backend/export_openapi.py")], {
      cwd: root,
      encoding: "utf8",
      maxBuffer: 64 * 1024 * 1024,
    }),
  );
}

function committed(): any {
  return JSON.parse(readFileSync(docsPath, "utf8"));
}

function stripPrefix(path: string) {
  return path.startsWith("/api/v1/") ? path.slice("/api/v1".length) : path;
}

function operations(doc: any) {
  return Object.entries(doc.paths).flatMap(([path, item]) =>
    Object.entries(item as Record<string, any>)
      .filter(([method]) =>
        ["get", "post", "put", "patch", "delete", "head", "options"].includes(
          method,
        ),
      )
      .map(([method, operation]) => ({ path, method, operation: operation as any })),
  );
}

function itemMethods(item: any) {
  return Object.fromEntries(
    Object.entries(item as Record<string, any>).filter(([method]) =>
      ["get", "post", "put", "patch", "delete", "head", "options"].includes(
        method,
      ),
    ),
  );
}

function resolvePointer(doc: any, pointer: string) {
  return pointer
    .replace(/^#\//, "")
    .split("/")
    .map((part) => decodeURIComponent(part).replaceAll("~1", "/").replaceAll("~0", "~"))
    .reduce((value: any, part) => value?.[part], doc);
}

function assertRefsResolve(doc: any) {
  const problems: string[] = [];
  const visit = (value: any, pointer: string) => {
    if (Array.isArray(value)) {
      value.forEach((item, index) => visit(item, `${pointer}/${index}`));
      return;
    }
    if (!value || typeof value !== "object") return;
    for (const [key, child] of Object.entries(value)) {
      if (key === "$ref" && typeof child === "string") {
        if (!child.startsWith("#/") || resolvePointer(doc, child) === undefined)
          problems.push(`${pointer}: ${child}`);
      } else if (
        key === "mapping" &&
        child &&
        typeof child === "object" &&
        !Array.isArray(child)
      ) {
        for (const [name, ref] of Object.entries(child)) {
          if (typeof ref !== "string" || resolvePointer(doc, ref) === undefined)
            problems.push(`${pointer}/mapping/${name}: ${String(ref)}`);
        }
      } else {
        visit(child, `${pointer}/${key}`);
      }
    }
  };
  visit(doc, "#");
  expect(problems).toEqual([]);
}

describe("authoritative OpenAPI generation", () => {
  it(
    "regenerates docs/openapi.yaml deterministically",
    () => {
      const first = generate();
      const second = generate();
      expect(second).toBe(first);
      expect(JSON.parse(first)).toEqual(committed());
    },
    120_000,
  );

  it("contains exactly the routes implemented by create_app().openapi()", () => {
    const doc = committed();
    const spec = actualSpec();
    expect(Object.keys(doc.paths).sort()).toEqual(
      Object.keys(spec.paths).map(stripPrefix).sort(),
    );
    for (const { path, method, operation } of operations(doc)) {
      const actualPath = Object.keys(spec.paths).find(
        (candidate) => stripPrefix(candidate) === path,
      );
      expect(actualPath, `${method.toUpperCase()} ${path}`).toBeDefined();
      expect(
        Object.keys(itemMethods(doc.paths[path])).sort(),
        `methods for ${path}`,
      ).toEqual(Object.keys(itemMethods(spec.paths[actualPath!])).sort());
      expect(operation.operationId).toBe(
        spec.paths[actualPath!][method].operationId,
      );
    }
  });

  it("strips the /api/v1 prefix and declares it as the only server", () => {
    const doc = committed();
    expect(doc.servers).toEqual([
      { url: "/api/v1", description: "DormPrint V1 API prefix" },
    ]);
    expect(Object.keys(doc.paths).filter((path) => path.startsWith("/api/v1"))).toEqual(
      [],
    );
  });

  it("documents writes with required Idempotency-Key and CSRF headers", () => {
    const doc = committed();
    for (const { path, method, operation } of operations(doc)) {
      const headers = (operation.parameters || [])
        .filter((parameter: any) => parameter.in === "header")
        .map((parameter: any) => parameter.name);
      if (["get", "head", "options"].includes(method)) {
        expect(headers, `${method.toUpperCase()} ${path}`).not.toContain(
          "Idempotency-Key",
        );
        continue;
      }
      const idempotency = operation.parameters.find(
        (parameter: any) =>
          parameter.in === "header" && parameter.name === "Idempotency-Key",
      );
      const csrf = operation.parameters.find(
        (parameter: any) =>
          parameter.in === "header" && parameter.name === "X-CSRF-Token",
      );
      expect(idempotency?.required, `${method.toUpperCase()} ${path}`).toBe(true);
      expect(csrf, `${method.toUpperCase()} ${path}`).toBeDefined();
      expect(csrf.required, `${method.toUpperCase()} ${path}`).toBe(
        path !== "/customer/session",
      );
    }
  });

  it("documents the normalized ApiError envelope and keeps every reference valid", () => {
    const doc = committed();
    const error = doc.components.schemas.ApiError;
    expect(error.required).toEqual(["error", "requestId"]);
    expect(error.properties.error.required).toEqual(["code", "message"]);
    expect(error.properties.error.properties.fieldErrors.type).toBe("object");
    for (const { path, method, operation } of operations(doc)) {
      expect(
        operation.responses.default?.content?.["application/json"]?.schema?.$ref,
        `${method.toUpperCase()} ${path} default`,
      ).toBe("#/components/schemas/ApiError");
      for (const [code, response] of Object.entries(
        operation.responses as Record<string, any>,
      )) {
        const schema = response?.content?.["application/json"]?.schema;
        if (code === "422")
          expect(schema?.$ref, `${method.toUpperCase()} ${path} 422`).toBe(
            "#/components/schemas/ApiError",
          );
      }
    }
    assertRefsResolve(doc);
  });

  it("keeps only provenance-annotated frontend aliases for consumed names", () => {
    const doc = committed();
    const schemas = doc.components.schemas;
    const aliases: Record<string, string> = {
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
    for (const [alias, target] of Object.entries(aliases)) {
      expect(schemas[alias]?.$ref, alias).toBe(`#/components/schemas/${target}`);
      expect(schemas[alias]?.description, alias).toMatch(/前端兼容别名/);
    }
    expect(schemas.Role.description).toMatch(/前端兼容别名/);
    expect(schemas.Role.enum).toEqual(["CUSTOMER", "ADMIN", "COURIER"]);
    expect(schemas.OrderStatus.description).toMatch(/前端兼容别名/);
    expect(schemas.OrderStatus.enum).toEqual(
      schemas.OrderView.properties.status.enum,
    );
    expect(schemas.TemplateRestoreInput.$ref).toContain("ReasonInput");
    expect(schemas.ExportInput.required).toContain("reason");
    expect(schemas.ExportInput.properties.filters.additionalProperties).toBe(false);
    expect(Object.keys(schemas.ExportInput.properties.filters.properties)).toEqual(
      expect.arrayContaining(["q", "type", "status", "riskHold"]),
    );
  });
});
