import { beforeEach, expect, it, vi } from "vitest";
const state = vi.hoisted(() => ({ documents: [] as any[] }));
vi.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: {},
  getDocument: () => state.documents.shift(),
}));
import { renderPage } from "../src/lib/pdf";
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => (resolve = r));
  return { promise, resolve };
}
beforeEach(() => {
  (globalThis as any).File = class File {};
  (globalThis as any).document = {
    createElement: () => ({ width: 0, height: 0 }),
  };
});
it("does not expose a partially rendered page before completion", async () => {
  const pending = deferred();
  const drawImage = vi.fn();
  const target = {
    width: 42,
    height: 60,
    getContext: () => ({ drawImage }),
  } as any;
  const render = vi.fn(() => ({ promise: pending.promise, cancel: vi.fn() }));
  state.documents.push({
    promise: Promise.resolve({
      numPages: 3,
      getPage: async () => ({
        getViewport: () => ({ width: 100, height: 140 }),
        render,
      }),
    }),
    destroy: vi.fn(async () => {}),
  });
  const result = renderPage(new Uint8Array([1]), target, 2);
  await vi.waitFor(() => expect(render).toHaveBeenCalled());
  expect(target.width).toBe(42);
  expect(drawImage).not.toHaveBeenCalled();
  pending.resolve();
  await result;
  expect(target.width).toBe(100);
  expect(drawImage).toHaveBeenCalledOnce();
});
it("cancels PDF tasks and never commits a superseded render", async () => {
  const pending = deferred();
  const cancel = vi.fn();
  const destroy = vi.fn(async () => {});
  const drawImage = vi.fn();
  const render = vi.fn(() => ({ promise: pending.promise, cancel }));
  state.documents.push({
    promise: Promise.resolve({
      numPages: 3,
      getPage: async () => ({
        getViewport: () => ({ width: 100, height: 140 }),
        render,
      }),
    }),
    destroy,
  });
  const abort = new AbortController();
  const result = renderPage(
    new Uint8Array([1]),
    { getContext: () => ({ drawImage }) } as any,
    2,
    abort.signal,
  );
  await vi.waitFor(() => expect(render).toHaveBeenCalled());
  abort.abort();
  pending.resolve();
  await expect(result).rejects.toMatchObject({ name: "AbortError" });
  expect(cancel).toHaveBeenCalledOnce();
  expect(destroy).toHaveBeenCalled();
  expect(drawImage).not.toHaveBeenCalled();
});
it("destroys the loading task when decoding fails", async () => {
  const destroy = vi.fn(async () => {});
  state.documents.push({
    promise: Promise.reject(new Error("invalid PDF")),
    destroy,
  });
  await expect(renderPage(new Uint8Array([1]), {} as any)).rejects.toThrow(
    "invalid PDF",
  );
  expect(destroy).toHaveBeenCalledOnce();
});
it("renders a sharper preview while limiting memory for unusually tall pages", async () => {
  const scales: number[] = [];
  const render = vi.fn(() => ({ promise: Promise.resolve(), cancel: vi.fn() }));
  state.documents.push({
    promise: Promise.resolve({
      numPages: 1,
      getPage: async () => ({
        getViewport: ({ scale }: { scale: number }) => {
          scales.push(scale);
          return { width: 600 * scale, height: 1200 * scale };
        },
        render,
      }),
    }),
    destroy: vi.fn(async () => {}),
  });
  const target = { getContext: () => ({ drawImage: vi.fn() }) } as any;
  await renderPage(new Uint8Array([1]), target, 1, undefined, 2200);
  expect(scales[1]).toBeGreaterThan(2);
  expect(target.width * target.height).toBeLessThanOrEqual(8_000_000);
});
