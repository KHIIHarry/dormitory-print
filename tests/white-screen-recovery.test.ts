import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  canAutoReload,
  CHUNK_RELOAD_COOLDOWN_MS,
  isChunkLoadError,
} from "../src/lib/chunk-recovery";
import { ErrorFallback } from "../src/components/error-boundary";

const mainSource = readFileSync(
  new URL("../src/main.tsx", import.meta.url),
  "utf8",
);
const outletSource = readFileSync(
  new URL("../src/components/resource-outlet.tsx", import.meta.url),
  "utf8",
);

describe("chunk load recovery", () => {
  it.each([
    "Failed to fetch dynamically imported module: https://print.test/assets/orders-a1b2.js",
    "error loading dynamically imported module",
    "Importing a module script failed.",
    "Loading chunk 42 failed.",
    "Unable to preload CSS for /assets/orders.css",
  ])("recognises a stale chunk failure: %s", (message) => {
    expect(isChunkLoadError(new Error(message))).toBe(true);
  });

  it("ignores ordinary business and network errors", () => {
    expect(isChunkLoadError(new Error("订单版本已过期，请刷新"))).toBe(false);
    expect(isChunkLoadError(new TypeError("Failed to fetch"))).toBe(false);
    expect(isChunkLoadError(undefined)).toBe(false);
  });

  it("only allows one automatic reload inside the cooldown window", () => {
    const now = 1_700_000_000_000;
    expect(canAutoReload(0, now)).toBe(true);
    expect(canAutoReload(now - CHUNK_RELOAD_COOLDOWN_MS, now)).toBe(false);
    expect(canAutoReload(now - CHUNK_RELOAD_COOLDOWN_MS - 1, now)).toBe(true);
  });

  it("wires both the root boundary and Vite preload recovery", () => {
    expect(mainSource).toContain("installChunkRecovery()");
    expect(mainSource).toContain('<AppErrorBoundary scope="root">');
    expect(mainSource).toContain("<App />");
    expect(outletSource).toContain('<AppErrorBoundary scope="route"');
  });
});

describe("error fallback", () => {
  it("renders an actionable recovery screen for a failed lazy chunk", () => {
    const html = renderToStaticMarkup(
      createElement(ErrorFallback, {
        error: new Error(
          "Failed to fetch dynamically imported module: /assets/orders-123.js",
        ),
        scope: "route",
      }),
    );
    expect(html).toContain("页面资源加载失败");
    expect(html).toContain("刷新页面");
    expect(html).toContain("返回首页");
    expect(html).not.toContain("错误信息");
  });

  it("renders the root variant as a full-page recovery screen", () => {
    const html = renderToStaticMarkup(
      createElement(ErrorFallback, {
        error: new Error("boom"),
        scope: "root",
      }),
    );
    expect(html).toContain("app-error--root");
    expect(html).toContain("页面暂时出了点问题");
    expect(html).toContain("重试");
    expect(html).toContain("boom");
  });

  it("keeps a manual reload available while automatically recovering", () => {
    const html = renderToStaticMarkup(
      createElement(ErrorFallback, {
        error: new Error("Failed to fetch dynamically imported module"),
        recovering: true,
      }),
    );
    expect(html).toContain("正在更新到最新版本");
    expect(html).toContain("立即刷新");
  });
});
