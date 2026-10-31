import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { RefreshStatus } from "../src/components/feedback";
import { ResourceState } from "../src/admin/growth/common";

it("快速后台刷新不立即插入可见提示、挤动已加载页面", () => {
  const html = renderToStaticMarkup(
    createElement(RefreshStatus, { loading: true }),
  );
  expect(html).toBe("");
});
it("后台更新失败保留原因和重试入口", () => {
  const html = renderToStaticMarkup(
    createElement(RefreshStatus, {
      loading: false,
      error: "网络连接失败",
      retry: () => {},
    }),
  );
  expect(html).toContain("网络连接失败");
  expect(html).toContain("重试");
});
it("已加载的空结果在后台刷新中保持占位内容，避免整块消失", () => {
  const html = renderToStaticMarkup(
    createElement(ResourceState, {
      loading: false,
      hasData: true,
      refreshing: true,
      empty: true,
      error: "",
      reload: () => {},
    }),
  );
  expect(html).toContain("暂无记录");
  expect(html).not.toContain("resource-skeleton");
});
