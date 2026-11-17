import { expect, test } from "@playwright/test";
import { PDFDocument } from "pdf-lib";

test("PDF preview supports first and last page at mobile and desktop widths", async ({ page }, info) => {
  const source = await (await page.request.get("/src/main.tsx")).text();
  const react = source.match(/from "([^"]*\/react\.js\?[^"]*)"/)![1];
  const dom = source.match(/from "([^"]*\/react-dom_client\.js\?[^"]*)"/)![1];
  const pdf = await PDFDocument.create();
  for (let number = 1; number <= 5; number++) pdf.addPage().drawText(`Page ${number}`);
  const bytes = Buffer.from(await pdf.save());
  await page.route("**/preview-test.pdf", (route) => route.fulfill({ contentType: "application/pdf", body: bytes }));
  await page.route("**/preview-pagination-fixture", (route) => route.fulfill({
    contentType: "text/html",
    body: `<!doctype html><html lang="zh-CN"><meta name="viewport" content="width=device-width, initial-scale=1"><div id="root"></div>
    <script type="module">import R from '/@react-refresh';R.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;</script>
    <script type="module">import React from '${react}';import ReactDOM from '${dom}';import {DocPreview} from '/src/components/doc-preview.tsx';import '/src/styles.css';
    const file={id:'test-pdf',name:'课堂资料.pdf',pages:5,kind:'DOCUMENT',url:'',segments:[]};
    ReactDOM.createRoot(document.getElementById('root')).render(React.createElement('main',{style:{maxWidth:640,margin:'16px auto',padding:16}},React.createElement(DocPreview,{file,pdfUrl:'/preview-test.pdf'})));
    </script></html>`,
  }));
  await page.goto("/preview-pagination-fixture");
  const preview = page.locator(".document-preview").first();
  const first = preview.getByRole("button", { name: "第一页", exact: true });
  const last = preview.getByRole("button", { name: "最后一页", exact: true });
  await expect(first).toBeVisible();
  await expect(preview.locator(".preview-pager")).toContainText("1 / 5");
  await expect(first).toBeDisabled();
  await last.click();
  await expect(preview.locator(".preview-pager")).toContainText("5 / 5");
  await expect(last).toBeDisabled();
  await preview.getByRole("button", { name: "上一页", exact: true }).click();
  await expect(preview.locator(".preview-pager")).toContainText("4 / 5");
  await first.click();
  await expect(preview.locator(".preview-pager")).toContainText("1 / 5");
  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1);
    const pager = preview.locator(".preview-pager");
    const bounds = (await pager.boundingBox())!;
    for (const button of await pager.getByRole("button").all()) {
      const box = (await button.boundingBox())!;
      expect(box.width).toBeGreaterThanOrEqual(44);
      expect(box.height).toBeGreaterThanOrEqual(44);
      expect(box.x).toBeGreaterThanOrEqual(bounds.x - 1);
      expect(box.x + box.width).toBeLessThanOrEqual(bounds.x + bounds.width + 1);
    }
    if (width === 390 || width === 1440)
      await pager.screenshot({ path: info.outputPath(`preview-pager-${width}.png`) });
  }
});
