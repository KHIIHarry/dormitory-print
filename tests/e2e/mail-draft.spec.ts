import { expect, test, type Page } from "@playwright/test";

test.use({ channel: "chrome", trace: "off" });

async function mailFixture(page: Page) {
  const state = {
    reads: 0,
    version: 1,
    writes: [] as Record<string, unknown>[],
  };
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    let data: unknown = {};
    if (path === "/customer/auth/me")
      data = {
        authKind: "REGISTERED",
        role: "CUSTOMER",
        capabilities: [],
        account: { id: "fixture-account", verified: true, status: "ACTIVE" },
      };
    if (path === "/auth/me")
      data = {
        id: "fixture-admin",
        accountId: "fixture-account",
        role: "ADMIN",
        capabilities: ["mail.manage"],
        isSuperAdmin: true,
        mustChangePassword: false,
      };
    if (path === "/admin/config/mail") {
      if (route.request().method() === "PATCH") {
        const body = route.request().postDataJSON();
        state.writes.push(body);
        if (body.expectedVersion !== state.version)
          return route.fulfill({
            status: 409,
            contentType: "application/json",
            body: JSON.stringify({
              error: { code: "VERSION_CONFLICT", message: "配置版本已变化" },
            }),
          });
        state.version++;
      } else state.reads++;
      data = {
        version: state.version,
        enabled: true,
        host: "smtp.example.invalid",
        port: 465,
        security: "SSL_TLS",
        timeoutSeconds: 10,
        username: "mailer@example.invalid",
        fromName: "示例发件人",
        fromAddress: "mailer@example.invalid",
        replyTo: "",
        authEnabled: true,
        secretConfigured: true,
        allowInsecure: false,
      };
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ data }),
    });
  });
  await page.goto("/manage/settings/mail");
  await expect(page.getByLabel("SMTP 主机", { exact: true })).toHaveValue(
    "smtp.example.invalid",
  );
  return state;
}

async function returnToPage(page: Page) {
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "hidden",
    });
    document.dispatchEvent(new Event("visibilitychange"));
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

test("keeps unpublished SMTP fields and authorization code across foreground refresh", async ({
  page,
}) => {
  const state = await mailFixture(page);
  await page
    .getByLabel("SMTP 主机", { exact: true })
    .fill("smtp.edited.invalid");
  await page
    .getByLabel("SMTP 授权码 / 密码", { exact: true })
    .fill("synthetic-unpublished-code");
  await page.getByLabel("发件人名称", { exact: true }).fill("尚未发布的名称");
  const reads = state.reads;
  state.version = 2;
  await returnToPage(page);
  await expect.poll(() => state.reads).toBeGreaterThan(reads);
  await expect(page.getByLabel("SMTP 主机", { exact: true })).toHaveValue(
    "smtp.edited.invalid",
  );
  await expect(
    page.getByLabel("SMTP 授权码 / 密码", { exact: true }),
  ).toHaveValue("synthetic-unpublished-code");
  await expect(page.getByLabel("发件人名称", { exact: true })).toHaveValue(
    "尚未发布的名称",
  );
  await expect(
    page.getByText("SMTP 配置 · 版本 1", { exact: true }),
  ).toBeVisible();
  expect(state.writes).toHaveLength(0);
});

test("keeps a secret-only edit in memory and clears it only after a successful save", async ({
  page,
}) => {
  const state = await mailFixture(page);
  const secret = page.getByLabel("SMTP 授权码 / 密码", { exact: true });
  await secret.fill("synthetic-unpublished-code");
  const reads = state.reads;
  await returnToPage(page);
  await expect.poll(() => state.reads).toBeGreaterThan(reads);
  await expect(secret).toHaveValue("synthetic-unpublished-code");
  const stored = await page.evaluate(() =>
    JSON.stringify([
      Object.entries(localStorage),
      Object.entries(sessionStorage),
    ]),
  );
  expect(stored).not.toContain("synthetic-unpublished-code");
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  await page.getByLabel("操作原因", { exact: true }).fill("更新隔离测试配置");
  await page.getByRole("button", { name: "确认操作", exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(1);
  await expect(secret).toHaveValue("");
});

test("keeps the editing version on conflict and reloads only after explicit discard", async ({
  page,
}) => {
  const state = await mailFixture(page);
  await page
    .getByLabel("SMTP 主机", { exact: true })
    .fill("smtp.edited.invalid");
  const secret = page.getByLabel("SMTP 授权码 / 密码", { exact: true });
  await secret.fill("synthetic-unpublished-code");
  state.version = 2;
  const reads = state.reads;
  await returnToPage(page);
  await expect.poll(() => state.reads).toBeGreaterThan(reads);
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  await page.getByLabel("操作原因", { exact: true }).fill("验证并发保护");
  await page.getByRole("button", { name: "确认操作", exact: true }).click();
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText(
    "409",
  );
  expect(state.writes[0].expectedVersion).toBe(1);
  await expect(secret).toHaveValue("synthetic-unpublished-code");
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await page
    .getByRole("button", { name: "放弃修改并重新加载", exact: true })
    .click();
  await expect(page.getByLabel("SMTP 主机", { exact: true })).toHaveValue(
    "smtp.example.invalid",
  );
  await expect(secret).toHaveValue("");
  await expect(
    page.getByText("SMTP 配置 · 版本 2", { exact: true }),
  ).toBeVisible();
});

test("refreshes an untouched form and does not restore a secret after page reload", async ({
  page,
}) => {
  const state = await mailFixture(page);
  state.version = 2;
  await returnToPage(page);
  await expect(
    page.getByText("SMTP 配置 · 版本 2", { exact: true }),
  ).toBeVisible();
  await page
    .getByLabel("SMTP 授权码 / 密码", { exact: true })
    .fill("synthetic-unpublished-code");
  await page.reload();
  await expect(
    page.getByLabel("SMTP 授权码 / 密码", { exact: true }),
  ).toHaveValue("");
});
