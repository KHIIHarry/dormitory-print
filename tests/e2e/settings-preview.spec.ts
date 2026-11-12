import { expect, test } from "@playwright/test";
import { PDFDocument } from "pdf-lib";

test.use({ channel: "chrome", trace: "off" });

test("single-page segments switch to simplex and skipped pages require explicit selection", async ({
  page,
}) => {
  await page.goto("/preview");
  await page.getByRole("button", { name: "文档设置示例", exact: true }).click();
  await page.getByRole("button", { name: "高级分段", exact: true }).click();
  const segment = page.locator(".segment-editor").first();
  const range = segment.getByRole("textbox", { name: "打印页码", exact: true });
  const duplex = segment.getByRole("button", { name: "双面", exact: true });
  const single = segment.getByRole("button", { name: "单面", exact: true });
  const preview = page.getByRole("button", {
    name: "生成最终预览",
    exact: true,
  });
  await expect(duplex).toHaveClass(/selected/);
  await range.fill("1");
  await expect(single).toHaveClass(/selected/);
  await expect(duplex).toBeDisabled();
  await expect(segment.getByRole("combobox")).toHaveCount(0);
  await expect(page.locator(".settings-bottom strong")).toHaveText("¥0.20");
  await expect(preview).toBeDisabled();

  const choose = page.getByRole("button", {
    name: "选择跳过打印页",
    exact: true,
  });
  await choose.click();
  let dialog = page.getByRole("dialog", { name: "选择跳过打印页" });
  await expect(
    dialog.getByRole("checkbox", { name: "第 1 页", exact: false }),
  ).toBeDisabled();
  await expect(
    dialog.getByText("已在分段 1 中打印", { exact: true }),
  ).toBeVisible();
  await dialog.getByRole("checkbox", { name: "第 2 页", exact: true }).check();
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await choose.click();
  dialog = page.getByRole("dialog", { name: "选择跳过打印页" });
  await expect(
    dialog.getByRole("checkbox", { name: "第 2 页", exact: true }),
  ).not.toBeChecked();
  await dialog
    .getByRole("button", { name: "选择全部未分配页", exact: true })
    .click();
  await dialog.getByRole("button", { name: "确认跳过", exact: true }).click();
  await expect(page.locator(".modal-backdrop")).toHaveCount(0);
  await expect(preview).toBeEnabled();
  await expect(
    page.getByText("已选择跳过：2–12 页", { exact: true }),
  ).toBeVisible();

  await range.fill("1-2");
  await expect(duplex).toBeEnabled();
  await expect(single).toHaveClass(/selected/);
  await duplex.click();
  await expect(duplex).toHaveClass(/selected/);
  await choose.click();
  dialog = page.getByRole("dialog", { name: "选择跳过打印页" });
  await expect(
    dialog.getByRole("checkbox", { name: "第 2 页", exact: false }),
  ).toBeDisabled();
  await expect(
    dialog.getByRole("checkbox", { name: "第 2 页", exact: false }),
  ).not.toBeChecked();
  await dialog.getByRole("button", { name: "取消", exact: true }).click();

  await expect(page.locator(".modal-backdrop")).toHaveCount(0);
  await range.fill("5");
  await expect(duplex).toBeDisabled();
  await expect(single).toHaveClass(/selected/);
  await expect(preview).toBeDisabled();
  await choose.click();
  dialog = page.getByRole("dialog", { name: "选择跳过打印页" });
  await dialog
    .getByRole("button", { name: "选择全部未分配页", exact: true })
    .click();
  await expect(dialog).toHaveCSS("opacity", "1");
  await page.screenshot({
    path: `output/config-draft-qa/skip-pages-${test.info().project.name}.png`,
    fullPage: false,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await dialog.getByRole("button", { name: "确认跳过", exact: true }).click();
  await expect(preview).toBeEnabled();
  await page.screenshot({
    path: `output/config-draft-qa/single-page-${test.info().project.name}.png`,
    fullPage: true,
  });
});

test("uploaded one-page documents disable duplex in normal mode", async ({
  page,
}) => {
  const pdf = await PDFDocument.create();
  pdf.addPage();
  await page.goto("/u/new");
  await expect(
    page.getByLabel("选择文件", { exact: true }),
  ).toBeEnabled();
  await page.locator('input[type="file"]').setInputFiles({
    name: "one-page.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from(await pdf.save()),
  });
  await page.getByRole("button", { name: "下一步", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "单面", exact: true }),
  ).toHaveClass(/selected/);
  await expect(
    page.getByRole("button", { name: "双面", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "生成最终预览", exact: true }),
  ).toBeEnabled();
});

test("skip selector preserves choices across pages and labels each allocated segment", async ({
  page,
}) => {
  const pdf = await PDFDocument.create();
  for (let i = 0; i < 65; i++) pdf.addPage();
  await page.goto("/u/new");
  await expect(
    page.getByLabel("选择文件", { exact: true }),
  ).toBeEnabled();
  await page
    .locator('input[type="file"]')
    .setInputFiles({
      name: "long.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from(await pdf.save()),
    });
  await page.getByRole("button", { name: "下一步", exact: true }).click();
  await page.getByRole("button", { name: "高级分段", exact: true }).click();
  await page.getByRole("textbox", { name: "打印页码", exact: true }).fill("1");
  await page.getByRole("button", { name: "添加页码分段", exact: true }).click();
  await page
    .getByRole("textbox", { name: "打印页码", exact: true })
    .nth(1)
    .fill("2");
  await page
    .getByRole("button", { name: "选择跳过打印页", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "选择跳过打印页" });
  await expect(
    dialog.getByRole("checkbox", {
      name: "第 1 页，已在分段 1 中打印",
      exact: true,
    }),
  ).toBeDisabled();
  await expect(
    dialog.getByRole("checkbox", {
      name: "第 2 页，已在分段 2 中打印",
      exact: true,
    }),
  ).toBeDisabled();
  await dialog.getByRole("checkbox", { name: "第 60 页", exact: true }).check();
  await dialog.getByRole("button", { name: "下一组", exact: true }).click();
  await dialog.getByRole("checkbox", { name: "第 61 页", exact: true }).check();
  await dialog.getByRole("button", { name: "上一组", exact: true }).click();
  await expect(
    dialog.getByRole("checkbox", { name: "第 60 页", exact: true }),
  ).toBeChecked();
  await dialog
    .getByRole("button", { name: "选择全部未分配页", exact: true })
    .click();
  await expect(dialog.getByRole("status")).toHaveText("已选择 63 页：3–65");
  await dialog.getByRole("button", { name: "清空选择", exact: true }).click();
  await expect(dialog.getByRole("status")).toHaveText("尚未选择跳过页");
  await dialog
    .getByRole("button", { name: "选择全部未分配页", exact: true })
    .click();
  await dialog.getByRole("button", { name: "确认跳过", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "生成最终预览", exact: true }),
  ).toBeEnabled();
});

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
