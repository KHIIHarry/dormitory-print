import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { expect, it, vi } from "vitest";
const fixture = vi.hoisted(() => ({ name: "document.pdf" }));

vi.mock("../src/lib/store", async () => {
  const { createDemo } = await import("../src/lib/demo");
  const data = createDemo();
  return {
    useStore: () => ({
      data,
      draft: [{ ...data.orders[0].files[0], name: fixture.name }],
      setDraft: () => {},
    }),
  };
});
vi.mock("../src/components/doc-preview", () => ({
  DocPreview: () => createElement("div", {}, "原始文件预览模块"),
}));
import { SettingsPage } from "../src/customer/settings";

it("keeps print settings and the final PDF step without the original upload preview", () => {
  const html = renderToStaticMarkup(
    createElement(MemoryRouter, {}, createElement(SettingsPage)),
  );
  expect(html).toContain("设置你的打印方式");
  expect(html).toContain("生成最终预览");
  expect(html).not.toContain("原始文件预览模块");
  expect(html).not.toContain("当前为原文件预览");
  expect(html).not.toContain("preview-aside");
});

it("directs spreadsheet users to the next-step PDF instead of the removed preview", () => {
  fixture.name = "spreadsheet.xlsx";
  const html = renderToStaticMarkup(
    createElement(MemoryRouter, {}, createElement(SettingsPage)),
  );
  expect(html).toContain("下一步最终 PDF");
  expect(html).not.toContain("本页右侧");
});
