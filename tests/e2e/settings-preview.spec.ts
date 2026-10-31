import { expect, test } from "@playwright/test";

test.use({ channel: "chrome", trace: "off" });

test("print settings omit the original preview and the next step still renders the final PDF", async ({
  page,
}) => {
  await page.goto("/preview");
  await page.getByRole("button", { name: "文档设置示例", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "设置你的打印方式" }),
  ).toBeVisible();
  await expect(page.locator(".document-preview, .preview-aside")).toHaveCount(
    0,
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: `output/config-draft-qa/settings-${test.info().project.name}.png`,
    fullPage: true,
  });
  await page.getByRole("button", { name: "生成最终预览", exact: true }).click();
  await page
    .getByRole("checkbox", {
      name: "我已检查这份最终 PDF，确认打印设置",
      exact: true,
    })
    .first()
    .click();
  const confirm = page.getByRole("button", {
    name: "我已确认最终打印格式",
    exact: true,
  });
  await expect(confirm).toBeEnabled();
  await expect(page.getByRole("dialog").locator("canvas")).toBeVisible();
});
