import { chromium, expect } from "@playwright/test";
import { mkdirSync } from "node:fs";
const base = process.env.DORMPRINT_BROWSER_ORIGIN;
const today = process.env.DORMPRINT_FINANCE_TODAY;
const out = "output/finance-qa";
mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ channel: "chrome", headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
  reducedMotion: "reduce",
});
const page = await context.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
try {
  await page.goto(base + "/u/account/login");
  await page
    .getByLabel("邮箱", { exact: true })
    .fill("finance-browser-admin@example.test");
  await page
    .getByLabel("密码", { exact: true })
    .fill("Testing-only-Password-42!");
  const login = page.waitForResponse(
    (r) =>
      r.url().includes("/customer/auth/login") &&
      r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "登录", exact: true }).click();
  expect((await login).status()).toBe(200);
  await page.goto(base + "/manage/finance");
  await expect(
    page.getByRole("heading", { name: "耗材与收支", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".finance-list > li")).toHaveCount(20);
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await expect(page.locator(".finance-list > li")).toHaveCount(3);
  await page.getByLabel("类别筛选").selectOption("INK");
  await expect(page.getByText("暂无支出记录", { exact: true })).toBeVisible();
  await expect(page.locator(".finance-pagination")).toContainText("第 1 页");
  await page.getByLabel("类别筛选").selectOption("");
  await page.getByRole("button", { name: "记录支出", exact: true }).click();
  const form = page.locator(".finance-entry");
  await form.getByLabel("金额（元）", { exact: true }).fill("25");
  await form.getByLabel("纸张张数", { exact: true }).fill("500");
  await form
    .getByLabel("备注（选填）", { exact: true })
    .fill("浏览器真实入库核对");
  await form
    .getByRole("button", { name: "保存支出", exact: true })
    .evaluate((button) => {
      button.click();
      button.click();
    });
  await expect(page.getByText("支出已记录", { exact: true })).toBeVisible();
  await expect(page.locator(".finance-entry")).toHaveCount(0);
  let response = await page.request.get(
    base + "/api/v1/admin/finance/expenses?pageSize=50",
  );
  let data = (await response.json()).data;
  expect(data.total).toBe(24);
  const saved = data.items.find((r) => r.note === "浏览器真实入库核对");
  expect(saved.amountCents).toBe(2500);
  expect(saved.paperSheets).toBe(500);
  const row = page
    .locator(".finance-list > li")
    .filter({ hasText: "浏览器真实入库核对" });
  await row.getByRole("button", { name: "作废", exact: true }).click();
  await row.getByLabel("作废原因").fill("验收后作废");
  await row.getByRole("button", { name: "确认作废", exact: true }).click();
  await expect(row.getByText("已作废", { exact: true })).toBeVisible();
  response = await page.request.get(base + "/api/v1/admin/finance/summary");
  data = (await response.json()).data;
  expect(data.paperPurchaseCents).toBe(2147483647 + 2200);
  expect(data.paperSheets).toBe(11500);
  await page.getByRole("button", { name: "累计", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "累计收支", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "记录支出", exact: true }).click();
  await form.getByLabel("金额（元）", { exact: true }).fill("15.50");
  await form.getByLabel("纸张张数", { exact: true }).fill("10");
  await form.getByLabel("类别", { exact: true }).selectOption("INK");
  await expect(form.getByLabel("纸张张数", { exact: true })).toHaveCount(0);
  await form.getByLabel("类别", { exact: true }).selectOption("PAPER");
  await expect(form.getByLabel("纸张张数", { exact: true })).toHaveValue("");
  for (const width of [1440, 768, 390, 375, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    const metrics = await page.evaluate(() => {
      const page = document.querySelector(".finance-page");
      const bounds = page.getBoundingClientRect();
      return {
        scroll: document.documentElement.scrollWidth,
        viewport: innerWidth,
        left: bounds.left,
        right: bounds.right,
        spacing: getComputedStyle(page).letterSpacing,
        amounts: [...page.querySelectorAll(".finance-metrics dd")].map(
          (el) => ({
            height: el.getBoundingClientRect().height,
            line: parseFloat(getComputedStyle(el).lineHeight),
          }),
        ),
        inputs: [...page.querySelectorAll("input,select,textarea,button")]
          .filter((el) => el.getBoundingClientRect().height > 0)
          .map((el) => ({
            height: el.getBoundingClientRect().height,
            width: el.getBoundingClientRect().width,
            text: el.textContent,
          })),
      };
    });
    expect(metrics.scroll, JSON.stringify(metrics)).toBeLessThanOrEqual(
      width + 1,
    );
    expect(metrics.right, JSON.stringify(metrics)).toBeLessThanOrEqual(
      width + 1,
    );
    expect(metrics.spacing).toBe("normal");
    for (const amount of metrics.amounts)
      expect(amount.height).toBeLessThanOrEqual(amount.line + 1);
    for (const control of metrics.inputs)
      expect(control.height, JSON.stringify(control)).toBeGreaterThanOrEqual(
        43.9,
      );
    await page.screenshot({ path: `${out}/${width}.png`, fullPage: true });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator(".finance-page").screenshot({ path: `${out}/mobile.png` });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page
    .locator(".finance-page")
    .screenshot({ path: `${out}/desktop.png` });
  await form.getByLabel("纸张张数", { exact: true }).fill("10");
  await context.setOffline(true);
  await form.getByRole("button", { name: "保存支出", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("离线");
  await expect(form.getByLabel("金额（元）", { exact: true })).toHaveValue(
    "15.50",
  );
  await context.setOffline(false);
  await form.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("button", { name: "月份", exact: true }).click();
  const previous = new Date(`${today.slice(0, 7)}-01T00:00:00Z`);
  previous.setUTCMonth(previous.getUTCMonth() - 1);
  await page
    .getByLabel("统计月份", { exact: true })
    .fill(previous.toISOString().slice(0, 7));
  await expect(
    page.getByRole("heading", { name: "暂无支出记录", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".finance-pagination")).toContainText("第 1 页");
  const mobileContext = await browser.newContext({
    storageState: await context.storageState(),
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
    deviceScaleFactor: 2,
    reducedMotion: "reduce",
  });
  const mobile = await mobileContext.newPage();
  await mobile.goto(base + "/manage/finance");
  await expect(mobile.locator(".finance-list > li")).toHaveCount(20);
  await mobile.getByRole("button", { name: "记录支出", exact: true }).tap();
  await expect(mobile.getByLabel("金额（元）", { exact: true })).toBeVisible();
  expect(
    await mobile
      .getByLabel("金额（元）", { exact: true })
      .evaluate((el) => parseFloat(getComputedStyle(el).fontSize)),
  ).toBeGreaterThanOrEqual(16);
  await mobile.screenshot({ path: `${out}/touch-mobile.png`, fullPage: true });
  await mobileContext.close();
  expect(errors).toEqual([]);
  console.log(
    "REAL_FINANCE_PASS desktop/mobile live API, exact cash, persistence, double-submit, void, paging, widths 320/375/390/768/1440",
  );
} finally {
  await context.close();
  await browser.close();
}
