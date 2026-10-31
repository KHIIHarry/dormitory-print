import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { expect, it, vi } from "vitest";
vi.mock("../src/lib/store", () => ({
  ADMIN: "/manage",
  DEMO: false,
  useStore: () => ({
    loading: true,
    error: "",
    refreshing: false,
    refreshError: "",
    refresh: () => {},
    actionIssues: {},
    resources: {},
  }),
}));
vi.mock("../src/components/presence", () => ({
  useReducedMotion: () => false,
}));
import { ResourceOutlet } from "../src/components/resource-outlet";
it.each(["/manage", "/manage/"])(
  "工作台%s的独立内容不被全页加载占位卸载",
  (path) => {
    const html = renderToStaticMarkup(
      createElement(
        MemoryRouter,
        { initialEntries: [path] },
        createElement(
          Routes,
          {},
          createElement(
            Route,
            { path: "/manage", element: createElement(ResourceOutlet) },
            createElement(Route, {
              index: true,
              element: createElement("div", {}, "已挂载工作台"),
            }),
          ),
        ),
      ),
    );
    expect(html).toContain("已挂载工作台");
    expect(html).not.toContain("resource-skeleton");
  },
);
