import { expect, test } from "@playwright/test";

test("shared toasts fit short copy and wrap long copy within the screen gutters", async ({
  page,
}, info) => {
  const main = await (await page.request.get("/src/main.tsx")).text();
  const react = main.match(/from "([^"]*\/react\.js\?[^"]*)"/)![1];
  const dom = main.match(/from "([^"]*\/react-dom_client\.js\?[^"]*)"/)![1];
  await page.route("**/feedback-fixture", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><div id="root"></div>
      <script type="module">
        import RefreshRuntime from '/@react-refresh';
        RefreshRuntime.injectIntoGlobalHook(window);
        window.$RefreshReg$ = () => {};
        window.$RefreshSig$ = () => type => type;
        window.__vite_plugin_react_preamble_installed__ = true;
      </script>
      <script type="module">
        import React from '${react}';
        import ReactDOM from '${dom}';
        import { OperationFeedback } from '/src/components/operation-feedback.tsx';
        import '/src/styles.css';
        const root = ReactDOM.createRoot(document.getElementById('root'));
        window.showToast = message => root.render(React.createElement(OperationFeedback, {toast:{message,tone:'success'},dismiss:()=>{}}));
        window.showToast('校区设置已保存');
      </script></html>`,
    }),
  );
  await page.goto("/feedback-fixture");
  const toast = page.getByRole("status");
  const short = "校区设置已保存";
  const long =
    "校区设置已保存。请核对各校区的地图和楼栋标记，再发布最新的配送地图。订单编号 " +
    "ABCDE12345".repeat(12);

  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: 844 });
    const gutter = width < 768 ? 12 : 24;
    await page.evaluate((message) => (window as any).showToast(message), short);
    await expect(toast).toHaveText(short);
    const shortBox = (await toast.boundingBox())!;
    const lineHeight = await toast.evaluate((element) =>
      parseFloat(getComputedStyle(element).lineHeight),
    );
    expect(shortBox.height).toBeLessThanOrEqual(lineHeight + 22);
    expect(shortBox.width).toBeLessThan(width - 2 * gutter);
    if (width < 768)
      expect(
        Math.abs(shortBox.x + shortBox.width / 2 - width / 2),
      ).toBeLessThanOrEqual(1);
    if (width === 390 || width === 1440)
      await page.screenshot({
        path: info.outputPath(`feedback-short-${width}.png`),
      });

    await page.evaluate((message) => (window as any).showToast(message), long);
    await expect(toast).toHaveText(long);
    const longBox = (await toast.boundingBox())!;
    expect(longBox.x).toBeGreaterThanOrEqual(gutter - 1);
    expect(longBox.x + longBox.width).toBeLessThanOrEqual(width - gutter + 1);
    if (width < 768)
      expect(longBox.width).toBeGreaterThanOrEqual(width - 2 * gutter - 1);
    else expect(longBox.width).toBeGreaterThan(460);
    expect(
      await toast.evaluate(
        (element) => element.scrollWidth - element.clientWidth,
      ),
    ).toBeLessThanOrEqual(1);
    if (width === 390 || width === 1440)
      await page.screenshot({ path: info.outputPath(`feedback-${width}.png`) });
  }
});
