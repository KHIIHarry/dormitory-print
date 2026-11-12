import { expect, test } from "@playwright/test";

test("the installed upload page opens the native picker from a direct click", async ({
  page,
}, testInfo) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "userAgent", { value: "Android Chrome" });
    const matchMedia = window.matchMedia.bind(window);
    window.matchMedia = (query) =>
      matchMedia(query === "(display-mode: standalone)" ? "all" : query);
    const click = HTMLInputElement.prototype.click;
    HTMLInputElement.prototype.click = function () {
      if (this.type !== "file") click.call(this);
    };
  });
  await page.goto("/u/new");
  const picker = page.locator('input[type="file"]');
  await expect(picker).toBeVisible();
  const chooserPromise = page.waitForEvent("filechooser");
  await picker.click();
  const chooser = await chooserPromise;
  expect(chooser.isMultiple()).toBe(true);
  await chooser.setFiles("public/assets/icon-512.png");
  const dialog = page.getByRole("dialog", { name: "选择图片打印纸张" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "关闭", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "复制上传链接" }),
  ).toBeVisible();

  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const { height, contained, fitsViewport } = await picker.evaluate(
      (input) => {
        const bounds = input.getBoundingClientRect();
        const dropzone = input.closest(".dropzone")!;
        const container = dropzone.getBoundingClientRect();
        const style = getComputedStyle(dropzone);
        return {
          height: bounds.height,
          contained:
            bounds.left >= container.left + parseFloat(style.paddingLeft) &&
            bounds.right <= container.right - parseFloat(style.paddingRight),
          fitsViewport: document.documentElement.scrollWidth <= innerWidth + 1,
        };
      },
    );
    expect(height).toBeGreaterThanOrEqual(44);
    expect(contained && fitsViewport).toBe(true);
    if (width === 320 || width === 1440) {
      await page.screenshot({
        path: testInfo.outputPath(`upload-${width}.png`),
      });
    }
  }
});

test("Android WeChat gets document MIME filters and a browser fallback", async ({
  page,
}, testInfo) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "userAgent", {
      value: "Android MicroMessenger/8.0",
    });
  });
  await page.goto("/u/new");
  const picker = page.locator('input[type="file"]');
  const accept = (await picker.getAttribute("accept"))?.split(",");
  expect(accept).toEqual(
    expect.arrayContaining([
      ".pdf",
      "application/pdf",
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      "image/jpeg",
    ]),
  );
  await expect(
    page.getByRole("button", { name: "复制上传链接" }),
  ).toBeVisible();

  for (const width of [320, 375, 390, 430]) {
    await page.setViewportSize({ width, height: 900 });
    const layout = await page.locator(".dropzone").evaluate((zone) => {
      const primary = zone.querySelector(".upload-picker")!;
      const help = zone.querySelector(".upload-browser-help > span")!;
      const copy = zone.querySelector(".upload-browser-help button")!;
      const formats = zone.querySelector(".supported-types")!;
      const center = (element: Element) => {
        const bounds = element.getBoundingClientRect();
        return bounds.left + bounds.width / 2;
      };
      return {
        primaryOffset: Math.abs(center(primary) - center(zone)),
        copyOffset: Math.abs(center(copy) - center(zone)),
        copyHeight: copy.getBoundingClientRect().height,
        helpGap:
          copy.getBoundingClientRect().top -
          help.getBoundingClientRect().bottom,
        formatsFontSize: parseFloat(getComputedStyle(formats).fontSize),
        tracking: [primary, help, copy].map(
          (element) => getComputedStyle(element).letterSpacing,
        ),
      };
    });
    expect(layout.primaryOffset).toBeLessThanOrEqual(1);
    expect(layout.copyOffset).toBeLessThanOrEqual(1);
    expect(layout.copyHeight).toBeGreaterThanOrEqual(44);
    expect(layout.helpGap).toBeGreaterThanOrEqual(12);
    expect(layout.formatsFontSize).toBeGreaterThanOrEqual(12);
    expect(
      layout.tracking.every(
        (spacing) => spacing === "normal" || spacing === "0px",
      ),
    ).toBe(true);
    if (width === 390) {
      await page
        .locator(".dropzone")
        .screenshot({ path: testInfo.outputPath("wechat-upload-390.png") });
    }
  }

  await page.goto("/u/new?type=photo");
  await expect(picker).toHaveAttribute("accept", /^image\/jpeg/);
  await expect(page.getByRole("button", { name: "复制上传链接" })).toHaveCount(
    0,
  );
});
