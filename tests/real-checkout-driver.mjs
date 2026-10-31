import { chromium, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';
const base = process.env.DORMPRINT_BROWSER_ORIGIN;
const email = process.env.DORMPRINT_BROWSER_EMAIL;
const pdf = process.env.DORMPRINT_BROWSER_PDF;
const building = process.env.DORMPRINT_BROWSER_BUILDING || '验收宿舍楼';
const slotName = process.env.DORMPRINT_BROWSER_SLOT || '验收时段';
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 1365, height: 900 }, reducedMotion: 'reduce' });
const page = await context.newPage();
const diag = { identity: [], requests: [], responses: [], failed: [], rows: [] };
page.on('request', (request) => {
  if (request.url().includes('/api/v1/') && request.method() !== 'GET')
    diag.requests.push({ t: Date.now(), method: request.method(), url: request.url() });
});
page.on('response', (response) => {
  if (response.url().includes('/api/v1/'))
    diag.responses.push({ t: Date.now(), status: response.status(), url: response.url() });
});
page.on('requestfailed', (request) => {
  diag.failed.push({ t: Date.now(), url: request.url(), error: request.failure()?.errorText });
});
page.on('console', (message) => {
  if (message.type() === 'error') diag.failed.push({ t: Date.now(), console: message.text() });
});
async function noteIdentity(label) {
  diag.identity.push({
    t: Date.now(), label,
    header: await page.locator('.header-profile').innerText().catch(() => null),
  });
}
try {
  await page.goto(base + '/u/account/login');
  await page.getByLabel('邮箱', { exact: true }).fill(email);
  await page.getByLabel('密码', { exact: true }).fill('Testing-only-Password-42!');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  const skip = page.getByRole('button', { name: '暂不关联', exact: true });
  await expect(skip).toBeVisible({ timeout: 20000 });
  await skip.click();
  await expect(page).toHaveURL(/\/u\/account\/?$/);
  await expect(page.getByRole('heading', { name: '账号设置', exact: true })).toBeVisible({ timeout: 20000 });

  await noteIdentity('login-complete');
  await page.goto(base + '/u/new');
  await noteIdentity('new-page-loaded');
  await expect(page.locator('.header-profile')).toContainText('我的账户', { timeout: 30000 });
  await noteIdentity('account-hydrated');
  const chooseFile = page.getByRole('button', { name: '选择文件', exact: true });
  await expect(chooseFile).toBeEnabled({ timeout: 30000 });
  await page.locator('input[type="file"]').setInputFiles(pdf);
  await noteIdentity('file-selected');
  diag.rows.push(await page.locator('.file-list').innerText().catch(() => null));
  await expect(page.getByText('已就绪', { exact: true }).first()).toBeVisible({ timeout: 90000 });
  const next = page.getByRole('button', { name: '下一步', exact: true });
  await expect(next).toBeEnabled({ timeout: 90000 });
  await next.click();

  await expect(page).toHaveURL(/\/u\/new\/settings$/);
  const generate = page.getByRole('button', { name: '生成最终预览', exact: true });
  await expect(generate).toBeEnabled({ timeout: 90000 });
  await generate.click();

  await expect(page).toHaveURL(/\/u\/review$/);
  const adopt = page.getByRole('button', { name: '仅关联本次结算到账号', exact: true });
  const adoptedNotice = page.getByText('本次结算已明确关联到当前账号。', { exact: true });
  await expect.poll(async () => {
    if (await adoptedNotice.isVisible().catch(() => false)) return 'adopted';
    if (await adopt.isVisible().catch(() => false) && await adopt.isEnabled().catch(() => false)) return 'adoptable';
    return 'waiting';
  }, { timeout: 60000 }).toMatch(/adopted|adoptable/);
  if (await adopt.isVisible().catch(() => false) && await adopt.isEnabled().catch(() => false)) {
    await adopt.click();
    await page.getByRole('button', { name: '确认仅关联本次结算', exact: true }).click();
  }
  await expect(adoptedNotice).toBeVisible({ timeout: 30000 });

  const ack = page.getByRole('checkbox', { name: '我已检查这份最终 PDF，确认打印设置', exact: true });
  await ack.click();
  await page.getByRole('button', { name: '我已确认最终打印格式', exact: true }).click();
  await expect(ack).toBeChecked({ timeout: 10000 });

  await page.getByLabel('微信昵称 *', { exact: true }).fill('真实浏览器验收同学');
  await page.getByLabel('手机号码 *', { exact: true }).fill('13800138000');
  await page.getByLabel('订单备注（选填）', { exact: true }).fill('真实浏览器上传与结算验收');
  await page.getByRole('button', { name: building, exact: true }).last().click();
  await page.getByRole('button', { name: new RegExp(slotName) }).first().click();
  await page.getByRole('checkbox', { name: /我确认本次应付/ }).check();
  mkdirSync('docs/qa/live-checkout', { recursive: true });
  await page.screenshot({ path: 'docs/qa/live-checkout/checkout-contact.png', fullPage: true });

  const orderResponsePromise = page.waitForResponse(
    (response) => response.url().endsWith('/api/v1/orders') && response.request().method() === 'POST',
    { timeout: 60000 },
  );
  await page.getByRole('button', { name: '确认并提交订单', exact: true }).click();
  const orderResponse = await orderResponsePromise;
  const orderBody = await orderResponse.json();
  expect(orderResponse.status(), JSON.stringify(orderBody)).toBe(200);
  const order = orderBody.data.order;
  expect(order.status).toBe('PENDING_CONFIRMATION');
  expect(order.totalCents).toBeGreaterThan(0);
  expect(order.cashReceivedCents ?? 0).toBe(0);
  expect(order.checkoutAccountId).toBeTruthy();
  await expect(page).toHaveURL(/\/u\/orders\/[^/]+\/contact$/, { timeout: 30000 });
  await expect(page.getByRole('heading', { name: '订单提交成功！', exact: true })).toBeVisible({ timeout: 30000 });
  await page.screenshot({ path: 'docs/qa/live-checkout/checkout-success.png', fullPage: true });
  console.log('REAL_CHECKOUT_PASS ' + JSON.stringify({
    orderId: order.id,
    quoteId: order.currentQuoteId,
    totalCents: order.totalCents,
    status: order.status,
    checkoutAccountId: order.checkoutAccountId,
    url: page.url(),
  }));
} catch (error) {
  mkdirSync('docs/qa/live-checkout', { recursive: true });
  await page.screenshot({ path: 'docs/qa/live-checkout/checkout-error.png', fullPage: true }).catch(() => {});
  console.error('REAL_CHECKOUT_DIAG ' + JSON.stringify(diag));
  throw error;
} finally {
  await context.close();
  await browser.close();
}
