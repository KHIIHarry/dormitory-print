import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  invalidateRequestContext,
  isUncertainSubmission,
  mutation,
  request,
} from "../src/lib/api";
class FakeXHR {
  static instances: FakeXHR[] = [];
  upload: { onprogress?: (event: any) => void; onload?: () => void } = {};
  headers: Record<string, string> = {};
  status = 200;
  responseText = "";
  withCredentials = false;
  method = "";
  url = "";
  body: unknown;
  onload?: () => void;
  onerror?: () => void;
  onabort?: () => void;
  ontimeout?: () => void;
  constructor() {
    FakeXHR.instances.push(this);
  }
  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  setRequestHeader(k: string, v: string) {
    this.headers[k] = v;
  }
  send(body: unknown) {
    this.body = body;
  }
  abort() {
    this.onabort?.();
  }
  resolve(data: unknown, status = 200) {
    this.status = status;
    this.responseText = JSON.stringify({ data });
    this.onload?.();
  }
}
afterEach(() => {
  invalidateRequestContext();
  vi.unstubAllGlobals();
  FakeXHR.instances = [];
});
function setup() {
  vi.stubGlobal("navigator", { onLine: true });
  vi.stubGlobal("document", { cookie: "csrfToken=csrf-value" });
  vi.stubGlobal("XMLHttpRequest", FakeXHR);
  vi.stubGlobal(
    "fetch",
    vi.fn().mockRejectedValue(new Error("progress request must use XHR")),
  );
}
describe("real upload transport", () => {
  it("keeps credentials, CSRF, idempotency, progress and server confirmation separate", async () => {
    setup();
    const body = new FormData();
    body.append("purpose", "PRINT_SOURCE");
    const progress = vi.fn(),
      sent = vi.fn();
    const result = request("/assets", {
      method: "POST",
      body,
      key: "same-operation",
      onUploadProgress: progress,
      onUploadComplete: sent,
    });
    const xhr = FakeXHR.instances[0];
    expect(xhr).toBeDefined();
    expect(xhr.withCredentials).toBe(true);
    expect(xhr.headers["X-CSRF-Token"]).toBe("csrf-value");
    expect(xhr.headers["Idempotency-Key"]).toBe("same-operation");
    expect(xhr.headers["Content-Type"]).toBeUndefined();
    xhr.upload.onprogress?.({ loaded: 20, total: 100, lengthComputable: true });
    expect(progress).toHaveBeenLastCalledWith({ loaded: 20, total: 100 });
    xhr.upload.onprogress?.({ loaded: 40, total: 0, lengthComputable: false });
    expect(progress).toHaveBeenLastCalledWith({ loaded: 40, total: undefined });
    let complete = false;
    result.then(() => (complete = true));
    xhr.upload.onload?.();
    await Promise.resolve();
    expect(sent).toHaveBeenCalledOnce();
    expect(complete).toBe(false);
    xhr.resolve({ assetId: "asset", status: "PROCESSING" });
    await expect(result).resolves.toEqual({
      assetId: "asset",
      status: "PROCESSING",
    });
  });
  it("aborts active upload on identity change and never resolves a later response", async () => {
    setup();
    const p = request("/assets", {
      method: "POST",
      body: new FormData(),
      onUploadProgress: () => {},
    });
    const checked = expect(p).rejects.toMatchObject({ name: "AbortError" });
    invalidateRequestContext();
    FakeXHR.instances[0].resolve({ assetId: "old" });
    await checked;
  });
  it("keeps unknown submission keys across server errors and removes them on explicit rejection", async () => {
    vi.stubGlobal("navigator", { onLine: true });
    vi.stubGlobal("document", { cookie: "" });
    const headers: HeadersInit[] = [];
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, opts: RequestInit) => {
        headers.push(opts.headers!);
        calls++;
        return new Response(
          JSON.stringify(
            calls < 3
              ? { error: { code: "UNAVAILABLE", message: "unknown" } }
              : { data: { id: "created" } },
          ),
          { status: calls < 3 ? 500 : 200 },
        );
      }),
    );
    await expect(mutation("/orders", { a: 1 })).rejects.toMatchObject({
      status: 500,
    });
    await expect(mutation("/orders", { a: 1 })).rejects.toMatchObject({
      status: 500,
    });
    await mutation("/orders", { a: 1 });
    expect((headers[0] as any)["Idempotency-Key"]).toBe(
      (headers[1] as any)["Idempotency-Key"],
    );
    expect((headers[1] as any)["Idempotency-Key"]).toBe(
      (headers[2] as any)["Idempotency-Key"],
    );
  });
  it("treats a lost response as uncertain while explicit rejection and preflight offline remain definite", () => {
    expect(isUncertainSubmission(new TypeError("Failed to fetch"))).toBe(true);
    expect(isUncertainSubmission(new ApiError("UNKNOWN", "error", 500))).toBe(
      true,
    );
    expect(
      isUncertainSubmission(new ApiError("CONFLICT", "conflict", 409)),
    ).toBe(false);
    expect(isUncertainSubmission(new ApiError("OFFLINE", "offline", 0))).toBe(
      false,
    );
  });
});
