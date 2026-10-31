import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { request, mutation, invalidateRequestContext } from "../src/lib/api";
beforeEach(() => {
  vi.stubGlobal("navigator", { onLine: true });
  vi.stubGlobal("document", { cookie: "" });
  invalidateRequestContext();
});
afterEach(() => {
  invalidateRequestContext();
  vi.unstubAllGlobals();
});
describe("身份边界的网络取消与幂等", () => {
  it("成功响应允许显式null与0，不将空状态当网络失败", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce({ ok: true, json: async () => ({ data: null }) })
        .mockResolvedValueOnce({ ok: true, json: async () => ({ data: 0 }) }),
    );
    expect(await request("/auth/me")).toBeNull();
    expect(await request("/count")).toBe(0);
  });
  it("身份失效中止尚未返回的私有请求", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url, options) =>
          new Promise((_resolve, reject) =>
            options.signal.addEventListener("abort", () =>
              reject(new DOMException("aborted", "AbortError")),
            ),
          ),
      ),
    );
    const pending = request("/private");
    invalidateRequestContext();
    await expect(pending).rejects.toThrow("aborted");
  });
  it("同一身份网络重试复用幂等键，换身份后不能复用旧键", async () => {
    const keys: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url, options) => {
        keys.push(options.headers["Idempotency-Key"]);
        throw new Error("offline");
      }),
    );
    await expect(mutation("/private", { confirm: true })).rejects.toThrow();
    await expect(mutation("/private", { confirm: true })).rejects.toThrow();
    expect(keys[0]).toBe(keys[1]);
    invalidateRequestContext();
    await expect(mutation("/private", { confirm: true })).rejects.toThrow();
    expect(keys[2]).not.toBe(keys[0]);
  });
});

it("异步摘要期间发生身份切换，旧写操作不能带新Cookie发出", async () => {
  const digest = crypto.subtle.digest.bind(crypto.subtle);
  let finish!: () => void;
  const hold = new Promise<void>((resolve) => {
    finish = resolve;
  });
  const spy = vi
    .spyOn(crypto.subtle, "digest")
    .mockImplementation(async (...args) => {
      await hold;
      return digest(...args);
    });
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  const pending = mutation("/private", { confirm: true });
  invalidateRequestContext();
  finish();
  await expect(pending).rejects.toThrow("身份已变更");
  expect(fetch).not.toHaveBeenCalled();
  spy.mockRestore();
});
