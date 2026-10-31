#!/usr/bin/env node
/**
 * Build docs/openapi.yaml from the authoritative FastAPI schema.
 *
 * `backend/export_openapi.py` calls `app.main.create_app().openapi()`, which is
 * the only complete source of truth (FastAPI 0.162 mounts routers lazily, so
 * walking `app.routes` can miss routes). This script only transports and
 * documents that schema:
 *
 *   - the `/api/v1` URL prefix moves from every path into `servers`;
 *   - write operations document the transaction-security headers that
 *     `backend/app/main.py` actually requires;
 *   - every error response is the normalized `ApiError` envelope;
 *   - no route is added here. Frontend compatibility aliases live in
 *     scripts/extend-growth-contract.mjs.
 *
 * Run `npm run contract:generate` from the repository root.
 */
import { execFileSync } from "node:child_process";
import { existsSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const docsPath = resolve(root, "docs/openapi.yaml");
const API_PREFIX = "/api/v1";
const WRITE_METHODS = new Set(["post", "put", "patch", "delete"]);
const READ_METHODS = new Set(["get", "head", "options"]);
const METHODS = [...WRITE_METHODS, ...READ_METHODS];
const API_ERROR_REF = { $ref: "#/components/schemas/ApiError" };

const apiErrorSchema = {
  type: "object",
  description:
    "后端统一错误信封：main.py 的 DomainError、参数校验、HTTPException 与未处理异常均返回该结构。",
  required: ["error", "requestId"],
  additionalProperties: false,
  properties: {
    error: {
      type: "object",
      required: ["code", "message"],
      additionalProperties: false,
      properties: {
        code: {
          type: "string",
          description:
            "稳定的机器可读错误码，例如 VALIDATION_ERROR、IDEMPOTENCY_KEY_REQUIRED、RESOURCE_CONFLICT。",
        },
        message: { type: "string", description: "面向用户的中文错误说明。" },
        details: {
          type: "object",
          description: "可选的附加错误详情。",
          additionalProperties: true,
        },
        fieldErrors: {
          type: "object",
          description:
            "可选的字段级错误；值可能是单条消息，也可能是消息数组。",
          additionalProperties: {
            oneOf: [
              { type: "string" },
              { type: "array", items: { type: "string" } },
            ],
          },
        },
      },
    },
    requestId: { type: "string", description: "服务端请求追踪标识。" },
  },
};

function pythonExecutable() {
  const candidates = [
    process.env.PYTHON,
    resolve(root, "backend/.venv/bin/python"),
    resolve(root, "backend/.venv/Scripts/python.exe"),
  ].filter(Boolean);
  const found = candidates.find((candidate) => existsSync(candidate));
  if (!found)
    throw new Error(
      "未找到 backend/.venv Python；请先创建后端虚拟环境，或通过 PYTHON 指定解释器。",
    );
  return found;
}

export function loadActualSpec() {
  const output = execFileSync(
    pythonExecutable(),
    [resolve(root, "backend/export_openapi.py")],
    { cwd: root, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 },
  );
  return JSON.parse(output);
}

function errorResponse(description) {
  return {
    description,
    content: { "application/json": { schema: API_ERROR_REF } },
  };
}

function securityHeaders(path) {
  const bootstrap = path === "/customer/session";
  return [
    {
      name: "Idempotency-Key",
      in: "header",
      required: true,
      description:
        "所有写请求必填。同一操作标识与相同请求内容会重放首次结果；相同标识用于不同请求会返回 409。",
      schema: { type: "string", minLength: 1 },
    },
    {
      name: "X-CSRF-Token",
      in: "header",
      required: !bootstrap,
      description: bootstrap
        ? "首次匿名会话创建允许省略；成功响应会通过 Set-Cookie 下发非 HttpOnly 的 csrfToken Cookie。"
        : "必须与非 HttpOnly 的 csrfToken Cookie 值完全一致；缺失或不匹配返回 403 CSRF_REQUIRED。",
      schema: { type: "string" },
    },
    {
      name: "Origin",
      in: "header",
      required: false,
      description:
        "浏览器写请求应携带；若携带了不在后端允许列表中的 Origin，返回 403 ORIGIN_REJECTED。",
      schema: { type: "string" },
    },
  ];
}

function normalizeOperation(path, method, operation) {
  const normalized = structuredClone(operation);
  const isWrite = WRITE_METHODS.has(method);
  const isRead = READ_METHODS.has(method);
  if (!isWrite && !isRead)
    throw new Error(`不支持的 HTTP 方法：${method.toUpperCase()} ${path}`);
  normalized.responses ??= {};

  if (isWrite) {
    const injected = new Set(["Idempotency-Key", "X-CSRF-Token", "Origin"]);
    const existing = (normalized.parameters ?? []).filter(
      (parameter) =>
        !(parameter.in === "header" && injected.has(parameter.name)),
    );
    normalized.parameters = [...existing, ...securityHeaders(path)];
    normalized.responses[403] ??= errorResponse(
      "事务安全校验失败：Origin 不被允许，或 X-CSRF-Token 与 csrfToken Cookie 不匹配。",
    );
  }

  if (normalized.responses[422]) {
    // FastAPI generated 422 -> HTTPValidationError, but main.py normalizes all
    // validation failures into the same ApiError envelope.
    normalized.responses[422] = errorResponse(
      "输入内容不符合要求，统一返回 VALIDATION_ERROR 错误信封。",
    );
  } else if (isWrite) {
    normalized.responses[422] = errorResponse(
      "缺少 Idempotency-Key，或请求内容未通过字段校验；错误码分别为 IDEMPOTENCY_KEY_REQUIRED / VALIDATION_ERROR。",
    );
  }

  normalized.responses.default = errorResponse(
    "未单独列出的错误（包括 409 冲突与 500 INTERNAL_ERROR）统一使用 ApiError 错误信封。",
  );
  return normalized;
}

function collectSchemaRefs(value, refs = new Set()) {
  if (Array.isArray(value)) {
    for (const item of value) collectSchemaRefs(item, refs);
    return refs;
  }
  if (!value || typeof value !== "object") return refs;
  for (const [key, child] of Object.entries(value)) {
    if (
      key === "$ref" &&
      typeof child === "string" &&
      child.startsWith("#/components/schemas/")
    ) {
      refs.add(child.slice("#/components/schemas/".length));
    } else {
      collectSchemaRefs(child, refs);
    }
  }
  return refs;
}

export function buildContract(actual) {
  const doc = structuredClone(actual);
  if (!doc.paths || typeof doc.paths !== "object")
    throw new Error("后端 OpenAPI 缺少 paths。");

  doc.servers = [{ url: API_PREFIX, description: "DormPrint V1 API prefix" }];
  doc.components ??= {};
  doc.components.schemas ??= {};
  doc.components.schemas.ApiError = apiErrorSchema;

  const paths = {};
  for (const [rawPath, rawItem] of Object.entries(doc.paths)) {
    if (rawPath !== API_PREFIX && !rawPath.startsWith(`${API_PREFIX}/`))
      throw new Error(`后端路径不以 ${API_PREFIX} 开头：${rawPath}`);
    const path = rawPath.slice(API_PREFIX.length) || "/";
    const item = structuredClone(rawItem);
    for (const method of Object.keys(item)) {
      if (METHODS.includes(method))
        item[method] = normalizeOperation(path, method, item[method]);
    }
    paths[path] = item;
  }
  doc.paths = paths;

  // These two schemas only described FastAPI's raw 422 body. Every 422 now
  // resolves to ApiError; drop the stale pair when no operation still uses it.
  const referenced = collectSchemaRefs(doc.paths);
  for (const name of ["HTTPValidationError", "ValidationError"]) {
    if (!referenced.has(name)) delete doc.components.schemas[name];
  }
  return doc;
}

export function writeContract(doc) {
  writeFileSync(docsPath, `${JSON.stringify(doc, null, 2)}\n`, "utf8");
}

export function main() {
  const doc = buildContract(loadActualSpec());
  writeContract(doc);
  const operations = Object.values(doc.paths).reduce(
    (sum, item) =>
      sum + Object.keys(item).filter((key) => METHODS.includes(key)).length,
    0,
  );
  console.log(
    `docs/openapi.yaml: ${Object.keys(doc.paths).length} paths, ${operations} operations, ${Object.keys(doc.components.schemas).length} schemas`,
  );
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(resolve(process.argv[1])).href
)
  main();
