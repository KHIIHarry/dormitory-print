import { requestActivity } from "./request-activity";
export const DEMO =
  import.meta.env.MODE === "demo" ||
  (import.meta.env.DEV && import.meta.env.VITE_DEMO_MODE !== "false");
export const ADMIN = (
  import.meta.env.VITE_ADMIN_BASE_PATH || "/manage"
).replace(/\/$/, "");
export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
    public details?: unknown,
  ) {
    super(message);
  }
}
const pendingKeys = new Map<string, string>();
const activeRequests = new Set<AbortController>();
let requestContextGeneration = 0;
export function invalidateRequestContext() {
  requestContextGeneration++;
  for (const controller of activeRequests) controller.abort();
  activeRequests.clear();
  pendingKeys.clear();
}
export type UploadProgress = { loaded: number; total?: number };
export type RequestOptions = {
  method?: string;
  body?: unknown;
  key?: string;
  signal?: AbortSignal;
  onUploadProgress?: (progress: UploadProgress) => void;
  onUploadComplete?: () => void;
};
function decodeResponse<T>(status: number, json: any): T {
  if (
    status < 200 ||
    status >= 300 ||
    !json ||
    !Object.prototype.hasOwnProperty.call(json, "data")
  )
    throw new ApiError(
      json?.error?.code ||
        (status >= 200 && status < 300
          ? "INVALID_RESPONSE"
          : "SERVICE_UNAVAILABLE"),
      json?.error?.message ||
        (status === 401
          ? "登录已过期，请重新登录"
          : status === 403
            ? "无权访问此内容"
            : status === 409
              ? "数据已发生变化，请刷新后重新核对"
              : "暂时无法连接服务，请稍后重试"),
      status,
      json?.error?.fieldErrors
        ? { ...(json?.error?.details || {}), fieldErrors: json.error.fieldErrors }
        : json?.error?.details,
    );
  return json.data as T;
}
/** A lost response can follow a committed write. A preflight OFFLINE never sent it. */
export function isUncertainSubmission(error: unknown): boolean {
  if (error instanceof TypeError) return true;
  if (!(error instanceof ApiError)) return false;
  return (
    error.status >= 500 ||
    error.status === 408 ||
    ["NETWORK_ERROR", "TIMEOUT", "INVALID_RESPONSE"].includes(error.code)
  );
}
export async function request<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  if (!navigator.onLine)
    throw new ApiError("OFFLINE", "网络已断开，请联网后重试", 0);
  const method = options.method || "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET") {
    headers["Idempotency-Key"] = options.key || crypto.randomUUID();
    const csrf = document.cookie
      .split("; ")
      .find((c) => c.startsWith("csrfToken="))
      ?.split("=")[1];
    if (csrf) headers["X-CSRF-Token"] = decodeURIComponent(csrf);
  }
  if (options.body && !(options.body instanceof FormData))
    headers["Content-Type"] = "application/json";
  const controller = new AbortController();
  activeRequests.add(controller);
  const abort = () => controller.abort();
  options.signal?.addEventListener("abort", abort, { once: true });
  if (options.signal?.aborted) controller.abort();
  const url = `${import.meta.env.VITE_API_BASE_URL || "/api/v1"}${path}`;
  const body =
    options.body instanceof FormData
      ? options.body
      : options.body
        ? JSON.stringify(options.body)
        : undefined;
  const finishActivity = method !== "GET" && path !== "/customer/session" ? requestActivity.begin() : () => {};
  try {
    if (controller.signal.aborted)
      throw new DOMException("请求已取消", "AbortError");
    if (
      (options.onUploadProgress || options.onUploadComplete) &&
      typeof XMLHttpRequest !== "undefined"
    ) {
      return await new Promise<T>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        let settled = false;
        const finish = (fn: () => void) => {
          if (settled) return;
          settled = true;
          controller.signal.removeEventListener("abort", abortXHR);
          fn();
        };
        const abortXHR = () => {
          xhr.abort();
          finish(() => reject(new DOMException("请求已取消", "AbortError")));
        };
        xhr.open(method, url);
        xhr.withCredentials =
          typeof location === "undefined" ||
          new URL(url, location.href).origin === location.origin;
        Object.entries(headers).forEach(([key, value]) =>
          xhr.setRequestHeader(key, value),
        );
        // Register listeners before send; no synthetic network percentages.
        xhr.upload.onprogress = (e) => {
          if (!settled && !controller.signal.aborted)
            options.onUploadProgress?.({
              loaded: e.loaded,
              total: e.lengthComputable && e.total > 0 ? e.total : undefined,
            });
        };
        xhr.upload.onload = () => {
          if (!settled && !controller.signal.aborted)
            options.onUploadComplete?.();
        };
        xhr.onload = () =>
          finish(() => {
            if (controller.signal.aborted) {
              reject(new DOMException("请求已取消", "AbortError"));
              return;
            }
            try {
              resolve(
                decodeResponse<T>(xhr.status, JSON.parse(xhr.responseText)),
              );
            } catch (error) {
              reject(
                error instanceof SyntaxError
                  ? new ApiError(
                      "INVALID_RESPONSE",
                      "服务端响应无法确认，请核实任务状态",
                      xhr.status,
                    )
                  : error,
              );
            }
          });
        xhr.onerror = () =>
          finish(() =>
            reject(
              new ApiError("NETWORK_ERROR", "网络连接中断，请核实任务状态", 0),
            ),
          );
        xhr.ontimeout = () =>
          finish(() =>
            reject(new ApiError("TIMEOUT", "请求超时，请核实任务状态", 0)),
          );
        xhr.onabort = () =>
          finish(() => reject(new DOMException("请求已取消", "AbortError")));
        controller.signal.addEventListener("abort", abortXHR, { once: true });
        if (controller.signal.aborted) abortXHR();
        else xhr.send(body);
      });
    }
    const response = await fetch(url, {
      method,
      headers,
      credentials: "same-origin",
      cache: "no-store",
      signal: controller.signal,
      body,
    });
    const json = await response.json().catch(() => null);
    if (controller.signal.aborted)
      throw new DOMException("请求已取消", "AbortError");
    return decodeResponse<T>(response.status, json);
  } finally {
    finishActivity();
    activeRequests.delete(controller);
    options.signal?.removeEventListener("abort", abort);
  }
}
export async function mutation<T>(
  path: string,
  body: unknown,
  method = "POST",
  options: { signal?: AbortSignal } = {},
): Promise<T> {
  const startContext = requestContextGeneration;
  const operation = Array.from(
    new Uint8Array(
      await crypto.subtle.digest(
        "SHA-256",
        new TextEncoder().encode(method + path + JSON.stringify(body)),
      ),
    ),
    (byte) => byte.toString(16).padStart(2, "0"),
  ).join("");
  if (startContext !== requestContextGeneration)
    throw new ApiError("AUTH_CONTEXT_CHANGED", "身份已变更，请重新提交", 409);
  let key = pendingKeys.get(operation);
  if (!key) {
    key = crypto.randomUUID();
    pendingKeys.set(operation, key);
  }
  try {
    const result = await request<T>(path, {
      method,
      body,
      key,
      signal: options.signal,
    });
    if (pendingKeys.get(operation) === key) pendingKeys.delete(operation);
    return result;
  } catch (error) {
    if (
      error instanceof ApiError &&
      error.status > 0 &&
      !isUncertainSubmission(error)
    )
      if (pendingKeys.get(operation) === key) pendingKeys.delete(operation);
    throw error;
  }
}
export const money = (cents: number) => `¥${(cents / 100).toFixed(2)}`;
export const now = () =>
  new Date().toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour12: false,
  });
