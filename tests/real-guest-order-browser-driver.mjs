import { chromium, expect } from '@playwright/test';
import { join } from 'node:path';

const base = process.env.DORMPRINT_BROWSER_ORIGIN;
const orderId = process.env.DORMPRINT_BROWSER_ORDER_ID;
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const contexts = [];

async function pageFor(viewport) {
  const context = await browser.newContext({ viewport, reducedMotion: 'reduce', serviceWorkers: 'block' });
  contexts.push(context);
  return { context, page: await context.newPage() };
}

async function login(page, email) {
  await page.getByLabel('邮箱', { exact: true }).fill(email);
  await page.getByLabel('密码', { exact: true }).fill('Testing-only-Password-42!');
  await page.getByRole('button', { name: '登录', exact: true }).click();
}

async function adminMutation(page, action, reason, days = 1) {
  await page.getByRole('button', { name: action === 'reset' ? '重置链接' : '关闭链接', exact: true }).click();
  if (action === 'reset') await page.getByLabel('有效天数（1–14 天）').fill(String(days));
  await page.getByLabel('操作原因').fill(reason);
  const responsePromise = page.waitForResponse(response =>
    response.url().endsWith(`/guest-access/${action}`) && response.request().method() === 'POST');
  await page.getByRole('button', { name: '确认操作', exact: true }).click();
  const response = await responsePromise;
  expect(response.status(), await response.text()).toBe(200);
}

async function openGuestLink(page) {
  const responsePromise = page.waitForResponse(response => response.url().endsWith('/guest-access/open'));
  const popupPromise = page.waitForEvent('popup');
  await page.getByRole('button', { name: '打开游客订单页', exact: true }).click();
  const [response, popup] = await Promise.all([responsePromise, popupPromise]);
  expect(response.status()).toBe(200);
  const { accessToken } = (await response.json()).data;
  await expect(page).toHaveURL(new RegExp(`/manage/orders/${orderId}$`));
  await expect(popup).toHaveURL(new RegExp(`/u/orders/${orderId}$`));
  await expect(popup.getByText('游客订单链接默认有效 14 天', { exact: true })).toBeVisible();
  await popup.goto(base + '/u/orders');
  await expect(popup.getByRole('heading', { name: '还没有这样的订单' })).toBeVisible();
  await popup.close();
  return accessToken;
}

async function resume(page) {
  await page.evaluate(() => window.dispatchEvent(new Event('online')));
}

async function verifyCustomerShell(page, state, active = false) {
  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: width < 768 ? 844 : 1000 });
    await page.evaluate(async () => {
      await document.fonts.ready;
      window.scrollTo({ top: 0, behavior: 'instant' });
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    });
    await expect(page.locator('.site-header')).toBeVisible();
    await expect(page.getByRole('link', { name: '宿舍打印首页', exact: true })).toBeVisible();
    await expect(page.locator('.site-footer')).toContainText('用心打印，认真对待每一页');
    await expect(page.locator('.bottom-nav')).toBeVisible({ visible: width < 768 });
    const metrics = await page.evaluate(() => {
      const banner = document.querySelector('.guest-access-banner');
      const main = document.querySelector('main.content-page');
      const rect = main?.getBoundingClientRect();
      const css = main ? getComputedStyle(main) : null;
      return { scroll: document.documentElement.scrollWidth,
        banner: banner?.getBoundingClientRect().toJSON(),
        contentLeft: rect && css ? rect.left + parseFloat(css.paddingLeft) : null,
        contentRight: rect && css ? rect.right - parseFloat(css.paddingRight) : null };
    });
    expect(metrics.scroll).toBeLessThanOrEqual(width + 1);
    if (active) {
      const navigation = (await page.getByRole('link', { name: '返回我的订单', exact: true }).boundingBox());
      expect(navigation.y + navigation.height).toBeLessThanOrEqual(metrics.banner.y);
      expect(Math.abs(metrics.banner.left - metrics.contentLeft)).toBeLessThanOrEqual(1);
      expect(Math.abs(metrics.banner.right - metrics.contentRight)).toBeLessThanOrEqual(1);
      await expect(page.locator('.guest-access-banner')).toContainText(/剩余\s*\d+\s*天\s*\d+\s*小时/);
    }
    if (width === 390 || width === 1440) {
      await page.screenshot({ path: join(process.env.DORMPRINT_BROWSER_QA_DIR, `${state}-${width}.png`), fullPage: true });
      await page.screenshot({ path: join(process.env.DORMPRINT_BROWSER_QA_DIR, `${state}-${width}-viewport.png`) });
      if (active && width === 390) {
        await expect(page.getByRole('button', { name: '复制联系文案', exact: true })).toBeVisible();
        await expect(page.locator('.order-contact-message')).toHaveCount(0);
        await page.locator('.detail-code').screenshot({ path: join(process.env.DORMPRINT_BROWSER_QA_DIR, 'order-contact-copy-390.png') });
      }
    }
  }
}

try {
  const admin = await pageFor({ width: 1440, height: 1000 });
  const original = await pageFor({ width: 390, height: 844 });
  const forwarded = await pageFor({ width: 375, height: 812 });
  await original.context.addCookies([{
    name: 'dormprint_guest_session', value: process.env.DORMPRINT_BROWSER_GUEST_COOKIE,
    url: base, httpOnly: true, sameSite: 'Lax',
  }]);
  await admin.page.goto(base + '/u/account/login');
  await login(admin.page, 'guest-browser-admin@example.test');
  await expect(admin.page).toHaveURL(/\/u\/account$/);
  await admin.page.goto(`${base}/manage/orders/${orderId}`);
  await expect(admin.page.getByRole('heading', { name: '订单专属链接', exact: true })).toBeVisible();
  await expect(admin.page.getByText('尚无链接', { exact: true })).toBeVisible();
  await expect(admin.page.locator('.order-customer-identity .order-guest-icon')).toBeVisible();
  await adminMutation(admin.page, 'reset', '真实浏览器核实后开放十四天', 14);
  const firstToken = await openGuestLink(admin.page);
  const link = token => `${base}/u/order-link?orderId=${orderId}#token=${token}`;
  await forwarded.page.goto(link(firstToken));
  await expect(forwarded.page).toHaveURL(new RegExp(`/u/orders/${orderId}$`));
  await expect(forwarded.page.getByText('游客订单链接默认有效 14 天', { exact: true })).toBeVisible();
  expect(await forwarded.page.evaluate(token =>
    JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }).includes(token), firstToken)).toBe(false);
  await original.page.goto(`${base}/u/orders/${orderId}`);
  await expect(original.page.getByText('游客订单链接默认有效 14 天', { exact: true })).toBeVisible();
  await original.page.screenshot({ path: join(process.env.DORMPRINT_BROWSER_QA_DIR, 'guest-active-mobile.png'), fullPage: true });
  await verifyCustomerShell(original.page, 'guest-active-full', true);
  await original.page.setViewportSize({ width: 390, height: 844 });
  const expiredPage = await original.context.newPage();
  await expiredPage.goto(`${base}/u/order-link?orderId=${process.env.DORMPRINT_BROWSER_EXPIRED_ORDER_ID}#token=${process.env.DORMPRINT_BROWSER_EXPIRED_TOKEN}`);
  await expect(expiredPage.getByRole('heading', { name: '游客订单链接已到期', exact: true })).toBeVisible();
  await verifyCustomerShell(expiredPage, 'guest-expired-full');
  await expiredPage.close();

  await admin.page.goto(`${base}/manage/orders/${orderId}`);
  await adminMutation(admin.page, 'close', '真实浏览器提前关闭');
  for (const page of [original.page, forwarded.page]) {
    await resume(page);
    await expect(page.getByRole('heading', { name: '游客订单链接已停止访问', exact: true })).toBeVisible();
  }
  await forwarded.page.screenshot({ path: join(process.env.DORMPRINT_BROWSER_QA_DIR, 'guest-closed-mobile.png'), fullPage: true });
  await verifyCustomerShell(forwarded.page, 'guest-closed-full');
  await forwarded.page.setViewportSize({ width: 375, height: 812 });
  await forwarded.page.getByRole('link', { name: '联系管理员', exact: true }).click();
  await expect(forwarded.page).toHaveURL(/\/u\/help#contact-admin$/);
  await expect(forwarded.page.locator('#contact-admin')).toBeFocused();
  await adminMutation(admin.page, 'reset', '真实浏览器重新开放并废止旧链接');
  const nextToken = await openGuestLink(admin.page);
  expect(nextToken).not.toBe(firstToken);
  await forwarded.page.goto(link(firstToken));
  await expect(forwarded.page.getByRole('heading', { name: '游客订单链接已停止访问', exact: true })).toBeVisible();
  await forwarded.page.goto(link(nextToken));
  await expect(forwarded.page.getByText('游客订单链接默认有效 14 天', { exact: true })).toBeVisible();
  await original.page.goto(`${base}/u/orders/${orderId}`);
  await original.page.getByRole('link', { name: '注册或登录，保存到我的订单', exact: true }).click();
  await login(original.page, 'guest-browser-user@example.test');
  await expect(original.page).toHaveURL(new RegExp(`/u/orders/${orderId}$`));
  const claimResponsePromise = original.page.waitForResponse(response =>
    response.url().endsWith(`/customer/orders/${orderId}/claim`) && response.request().method() === 'POST');
  await original.page.getByRole('button', { name: '确认关联到我的订单', exact: true }).click();
  const claimed = await claimResponsePromise;
  expect(claimed.status(), await claimed.text()).toBe(200);
  expect((await claimed.json()).data).toEqual({ orderId, ownerAccountId: process.env.DORMPRINT_BROWSER_OWNER_ID });
  await expect(original.page.getByText('游客订单链接默认有效 14 天', { exact: true })).toHaveCount(0);
  await original.page.reload();
  await expect(original.page.getByText(process.env.DORMPRINT_BROWSER_PICKUP_CODE, { exact: true }).first()).toBeVisible();
  await resume(forwarded.page);
  await expect(forwarded.page.getByRole('heading', { name: '游客订单链接已停止访问', exact: true })).toBeVisible();
  await admin.page.goto(`${base}/manage/orders/${orderId}`);
  await expect(admin.page.getByRole('heading', { name: '客户信息', exact: true })).toBeVisible();
  await expect(admin.page.locator('.order-customer-identity .order-guest-icon')).toHaveCount(0);
  await expect(admin.page.locator('.order-customer-identity a.order-pickup-origin svg')).toBeVisible();
  await expect(admin.page.getByText('guest-browser-user@example.test', { exact: true })).toBeVisible();
  await admin.page.screenshot({ path: join(process.env.DORMPRINT_BROWSER_QA_DIR, 'guest-claimed-admin-desktop.png'), fullPage: true });

  const expiredOrderId = process.env.DORMPRINT_BROWSER_EXPIRED_ORDER_ID;
  const targetEmail = 'guest-browser-user@example.test';
  await admin.page.goto(`${base}/manage/orders/${expiredOrderId}`);
  await expect(admin.page.locator('.order-customer-identity .order-guest-icon')).toBeVisible();
  await admin.page.getByRole('button', { name: '关联到客户账号', exact: true }).click();
  const dialog = admin.page.getByRole('dialog', { name: '关联到客户账号' });
  await dialog.getByRole('combobox', { name: '目标客户邮箱或账号 ID' }).fill('guest-browser-us');
  await dialog.getByRole('option', { name: /guest-browser-user@example.test/ }).click();
  await expect(dialog.getByText(targetEmail, { exact: true })).toBeVisible();
  const confirm = dialog.getByRole('button', { name: '确认操作', exact: true });
  await expect(confirm).toBeDisabled();
  await dialog.getByLabel('已核实订单与目标已验证账号，确认关联此单').check();
  await dialog.getByLabel('操作原因').fill('已核实订单与客户注册邮箱');
  const bindResponse = admin.page.waitForResponse(response =>
    response.url().endsWith(`/admin/orders/${expiredOrderId}/guest-owner`));
  await confirm.click();
  const binding = await bindResponse;
  expect(binding.status()).toBe(200);
  expect((await binding.json()).data).toEqual({
    orderId: expiredOrderId, ownerAccountId: process.env.DORMPRINT_BROWSER_OWNER_ID,
  });
  await expect(admin.page.locator('.order-customer-identity')).toContainText(targetEmail);
  await expect(admin.page.locator('.order-customer-identity .order-guest-icon')).toHaveCount(0);
  await expect(admin.page.locator('.order-customer-identity a.order-pickup-origin svg')).toBeVisible();
  await original.page.goto(`${base}/u/orders`);
  await expect(original.page.locator(`a[href="/u/orders/${expiredOrderId}"]`)).toBeVisible();
  console.log('REAL_GUEST_BROWSER_PASS: actual UI and API reset, open, close, reset, old-link denial, original-device claim, admin association by email, forwarded-session revocation, account ownership, no token storage.');
} finally {
  for (const context of contexts) await context.close();
  await browser.close();
}
