import { test, expect, type Page } from "@playwright/test";
test("homepage contains print services and no delivery action", async ({
  page,
}) => {
  await page.goto("/u/");
  await expect(
    page.getByRole("heading", { name: "你需要打印什么？" }),
  ).toBeVisible();
  await expect(page.locator(".service-card")).toHaveCount(2);
  await expect(page.locator('a[href^="/manage"]')).toHaveCount(0);
});
test("document order reaches contact page without clearing the order", async ({
  page,
}) => {
  await page.goto("/u/new");
  await page.getByRole("button", { name: "使用示例文件", exact: true }).click();
  await page.getByRole("button", { name: "使用演示 PDF（12 页）" }).click();
  await page.getByRole("button", { name: "下一步", exact: true }).click();
  await page.getByRole("button", { name: "生成最终预览" }).click();
  await page
    .getByRole("checkbox", { name: "我已检查这份最终 PDF，确认打印设置" })
    .click();
  await page
    .getByRole("button", { name: "我已确认最终打印格式", exact: true })
    .click();
  await page.getByLabel("微信昵称 *", { exact: true }).fill("端到端验收同学");
  await page.getByLabel("手机号码 *", { exact: true }).fill("13800138000");
  await page
    .getByLabel("订单备注（选填）", { exact: true })
    .fill("第一份资料请单独整理\n方便分开领取");
  await page
    .getByRole("button", { name: "知行苑 1 栋", exact: true })
    .last()
    .click();
  await page
    .getByRole("button", { name: "10月29日 · 星期四 晚间配送 19:00 – 20:00" })
    .click();
  await page.getByRole("checkbox", { name: /我确认本次应付/ }).check();
  await page.getByRole("button", { name: "确认并提交订单" }).click();
  await expect(page).toHaveURL(/\/contact$/);
  await expect(
    page.getByRole("heading", { name: "订单提交成功！" }),
  ).toBeVisible();
  await page.getByRole("link", { name: "查看订单详情" }).click();
  await expect(
    page.getByRole("heading", { name: "等待管理员确认付款" }),
  ).toBeVisible();
  await expect(page.locator(".order-note-text")).toHaveText(
    "第一份资料请单独整理\n方便分开领取",
  );
});
test("unauthed staff route redirects and admin demo renders operational pages", async ({
  page,
}) => {
  await page.goto("/manage/packing");
  await expect(page).toHaveURL(/\/manage\/login/);
  await page.getByRole("button", { name: "进入管理员演示" }).click();
  await expect(
    page.getByRole("heading", { name: "装袋中心", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "确认资料齐全并已装袋" }).first(),
  ).toBeDisabled();
});
test("courier cannot skip geolocation", async ({ page }) => {
  await page.goto("/courier/login");
  await page.getByRole("button", { name: "进入配送员演示" }).click();
  await page.getByRole("link", { name: /午后配送 · 西区/ }).click();
  await page.getByRole("link", { name: /B5T9D/ }).click();
  await page.getByRole("link", { name: "拍照并完成交付" }).click();
  await expect(
    page.getByRole("button", { name: "获取当前位置" }),
  ).toBeVisible();
  await expect(page.getByText("管理员：填写原因跳过定位")).toHaveCount(0);
  await page.getByRole("button", { name: "确认资料已送达" }).click();
  await expect(page.getByText("配送员必须获取真实定位")).toBeVisible();
});

test("checkout login/adoption/withdrawal keeps PDF acknowledgment and requires a fresh amount confirmation", async ({
  page,
}) => {
  await page.goto("/preview");
  await page.getByRole("button", { name: "文档设置示例", exact: true }).click();
  await page.getByRole("button", { name: "生成最终预览", exact: true }).click();
  const acknowledgment = page.getByRole("checkbox", {
    name: "我已检查这份最终 PDF，确认打印设置",
    exact: true,
  });
  await acknowledgment.click();
  await page
    .getByRole("button", { name: "我已确认最终打印格式", exact: true })
    .click();
  await page.getByRole("checkbox", { name: /我确认本次应付/ }).check();
  await page
    .getByRole("button", { name: "登录 / 注册以使用账号优惠", exact: true })
    .click();
  await page.getByRole("button", { name: "体验演示账号", exact: true }).click();
  await page
    .getByRole("button", { name: "确认仅关联本次结算", exact: true })
    .click();
  await page
    .getByRole("button", { name: "撤回本次账号结算，改为游客", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "仅关联本次结算到账号", exact: true }),
  ).toBeVisible();
  await expect(acknowledgment).toBeChecked();
  await expect(
    page.getByRole("checkbox", { name: /我确认本次应付/ }),
  ).not.toBeChecked();
});

async function chooseAdminMenu(page: Page, label: string, group: string) {
  await page.locator(".admin-sidebar").waitFor({ state: "attached" });
  const drawer = page.getByRole("button", { name: "展开导航", exact: true });
  if (
    (await drawer.isVisible()) &&
    (await drawer.getAttribute("aria-expanded")) !== "true"
  )
    await drawer.click();
  const section = page.getByRole("button", { name: group, exact: true });
  if ((await section.getAttribute("aria-expanded")) !== "true")
    await section.click();
  await page
    .locator(".admin-sidebar nav")
    .getByRole("link", { name: label, exact: true })
    .click();
  if (await drawer.isVisible()) {
    await expect(drawer).toHaveAttribute("aria-expanded", "false");
    await expect(page.locator(".admin-main")).not.toHaveAttribute("inert", "");
  }
}

test("admin settings siblings have exactly one active sidebar entry", async ({
  page,
}) => {
  await page.goto("/manage/login");
  await page
    .getByRole("button", { name: "进入管理员演示", exact: true })
    .click();
  const sidebar = page.locator(".admin-sidebar nav");
  for (const [label, group] of [
    ["邮件配置", "系统与团队"],
    ["邮件模板", "系统与团队"],
    ["优惠活动", "客户与优惠"],
    ["满量折扣", "客户与优惠"],
    ["系统配置", "系统与团队"],
  ]) {
    await chooseAdminMenu(page, label, group);
    await expect(sidebar.locator('a[aria-current="page"]')).toHaveCount(1);
    await expect(sidebar.locator('a[aria-current="page"]')).toHaveText(label);
  }
  await chooseAdminMenu(page, "订单管理", "订单与生产");
  await page.getByRole("link", { name: "K7M4P", exact: true }).click();
  await expect(sidebar.locator('a[aria-current="page"]')).toHaveCount(1);
  await expect(sidebar.locator('a[aria-current="page"]')).toHaveText(
    "订单管理",
  );
  await expect(
    sidebar.getByRole("button", {
      name: "订单与生产",
      exact: true,
      includeHidden: true,
    }),
  ).toHaveAttribute("aria-expanded", "true");
});

test("admin accordion is compact, keyboard operable and closes the previous group", async ({
  page,
}) => {
  await page.goto("/manage/login");
  await page
    .getByRole("button", { name: "进入管理员演示", exact: true })
    .click();
  await page.locator(".admin-sidebar").waitFor({ state: "attached" });
  const drawer = page.getByRole("button", { name: "展开导航", exact: true });
  const mobile = await drawer.isVisible();
  if (mobile) await drawer.click();
  const sidebar = page.locator(".admin-sidebar nav");
  await expect(sidebar.getByRole("button", { expanded: true })).toHaveCount(0);
  await expect(sidebar.locator('a[href$="/settings/mail"]')).toBeHidden();
  const system = sidebar.getByRole("button", {
    name: "系统与团队",
    exact: true,
  });
  await system.press("Enter");
  await expect(system).toHaveAttribute("aria-expanded", "true");
  await expect(
    sidebar.getByRole("link", { name: "邮件配置", exact: true }),
  ).toBeVisible();
  await sidebar.getByRole("button", { name: "配送管理", exact: true }).click();
  await expect(system).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator("#admin-nav-system")).toHaveAttribute("inert", "");
  await expect(sidebar.getByRole("button", { expanded: true })).toHaveCount(1);
  if (mobile) {
    const close = page.getByRole("button", { name: "关闭导航", exact: true });
    await expect(close).toBeVisible();
    await close.press("Escape");
    await expect(drawer).toHaveAttribute("aria-expanded", "false");
    await expect(drawer).toBeFocused();
    await expect(page.locator(".admin-main")).not.toHaveAttribute("inert", "");
    await expect(page.locator(".admin-sidebar")).toHaveAttribute("inert", "");
  }
});

test("submenu focus frames stay within the animated clip at both ends", async ({
  page,
}) => {
  await page.goto("/manage/login");
  await page
    .getByRole("button", { name: "进入管理员演示", exact: true })
    .click();
  for (const label of ["配送调度", "校园地图"]) {
    await chooseAdminMenu(page, label, "配送管理");
    const drawer = page.getByRole("button", { name: "展开导航", exact: true });
    if (
      (await drawer.isVisible()) &&
      (await drawer.getAttribute("aria-expanded")) !== "true"
    )
      await drawer.click();
    const link = page
      .locator(".admin-sidebar")
      .getByRole("link", { name: label, exact: true });
    await link.press("Shift");
    await expect(link).toBeFocused();
    await expect
      .poll(async () =>
        link.evaluate((el) => {
          const row = el.getBoundingClientRect(),
            clip = el.closest(".admin-nav-clip")!.getBoundingClientRect(),
            style = getComputedStyle(el);
          const outset = Math.max(
            0,
            parseFloat(style.outlineWidth) + parseFloat(style.outlineOffset),
          );
          return (
            row.left - outset >= clip.left &&
            row.right + outset <= clip.right &&
            row.top - outset >= clip.top &&
            row.bottom + outset <= clip.bottom
          );
        }),
      )
      .toBe(true);
  }
});

test("uploaded images require a paper choice and can use A4 document printing", async ({
  page,
}) => {
  await page.goto("/u/new");
  const chooserPromise = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "选择文件", exact: true }).click();
  const chooser = await chooserPromise;
  await chooser.setFiles("public/assets/icon-512.png");
  const dialog = page.getByRole("dialog", {
    name: "选择图片打印纸张",
    exact: true,
  });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "关闭", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "下一步", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "为 icon-512.png 选择打印纸张", exact: true })
    .click();
  await page
    .getByRole("button", { name: "使用 A4 纸打印", exact: true })
    .click();
  await expect(dialog).toBeHidden();
  await expect(
    page.getByText("A4 图片 · 1 页", { exact: false }),
  ).toBeVisible();
  await page.getByRole("button", { name: "下一步", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "A4 图片打印设置", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "生成最终预览", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "查看最终 PDF", exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/本单 A4 用纸 1 张/)).toBeVisible();
});

test("final PDF checking requires the confirmation preview, while view-only cannot acknowledge", async ({
  page,
}) => {
  await page.goto("/preview");
  await page.getByRole("button", { name: "文档设置示例", exact: true }).click();
  await page.getByRole("button", { name: "生成最终预览", exact: true }).click();
  const ack = page.getByRole("checkbox", {
    name: "我已检查这份最终 PDF，确认打印设置",
    exact: true,
  });
  await page.getByRole("button", { name: "查看最终 PDF", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "我已确认最终打印格式", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await expect(ack).not.toBeChecked();
  await ack.click();
  await expect(ack).not.toBeChecked();
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await expect(ack).not.toBeChecked();
  await ack.click();
  const confirm = page.getByRole("button", {
    name: "我已确认最终打印格式",
    exact: true,
  });
  await expect(confirm).toBeEnabled();
  await confirm.click();
  await expect(ack).toBeChecked();
  await ack.uncheck();
  await expect(ack).not.toBeChecked();
});

test("checkout stays inside the mobile viewport with a long filename", async ({ page }) => {
  const { PDFDocument } = await import("pdf-lib");
  const pdf = await PDFDocument.create();
  pdf.addPage();
  const bytes = await pdf.save();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/u/new");
  await page.locator('input[type="file"]').setInputFiles({
    name: "Group10_Stop Buying Eco-Friendly Bags with a long filename for testing.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from(bytes),
  });
  await page.getByRole("button", { name: "下一步", exact: true }).click();
  await page.getByRole("button", { name: "生成最终预览" }).click();
  await expect(page.getByRole("heading", { name: "确认最终打印文件" })).toBeVisible();
  for (const width of [320, 375, 430, 767]) {
    await page.setViewportSize({ width, height: 844 });
    const layout = await page.evaluate(() => {
      const card = document.querySelector(".checkout-main > .panel")!.getBoundingClientRect();
      return {
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        left: card.left,
        right: document.documentElement.clientWidth - card.right,
      };
    });
    expect(layout.overflow, `viewport ${width}px`).toBeLessThanOrEqual(1);
    expect(Math.abs(layout.left - layout.right), `viewport ${width}px`).toBeLessThanOrEqual(1);
  }
});
