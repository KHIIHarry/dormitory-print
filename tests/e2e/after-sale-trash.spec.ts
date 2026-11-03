import { spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

const systemChrome =
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
test.use(
  existsSync(systemChrome)
    ? { launchOptions: { executablePath: systemChrome } }
    : {},
);
let base = "";
let server: ChildProcess | undefined;
test.beforeAll(async () => {
  const port = test.info().project.name === "mobile" ? 5227 : 5226;
  base = `http://127.0.0.1:${port}`;
  server = spawn(
    "npx",
    ["vite", "--port", String(port), "--strictPort", "--host", "127.0.0.1"],
    {
      env: { ...process.env, VITE_DEMO_MODE: "false" },
      stdio: "ignore",
    },
  );
  for (let attempt = 0; attempt < 80; attempt++) {
    try {
      if ((await fetch(base)).ok) return;
    } catch {
      /* starting */
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("Real-mode Vite server did not start");
});
test.afterAll(() => server?.kill("SIGTERM"));

const ended = {
  id: "as-ended",
  orderId: "order-1",
  orderCode: "DP1001",
  version: 7,
  status: "REFUNDED",
  source: "CUSTOMER",
  responsibility: "MERCHANT",
  solution: "REFUND",
  reason: "漏印已退款",
  suggestedCents: 500,
  approvedCents: 500,
  data: {},
  createdAt: "2026-11-03T08:00:00Z",
};

async function fixture(
  page: Page,
  options: { capabilities?: string[]; failure?: string; status?: string } = {},
) {
  const writes: unknown[] = [];
  let removed = false;
  let reads = 0;
  const item = { ...ended, status: options.status || ended.status };
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    let data: unknown = {};
    if (path === "/auth/me")
      data = {
        id: "admin",
        role: "ADMIN",
        capabilities: options.capabilities || ["payment.manage", "data.manage"],
        emailVerified: true,
      };
    if (path === "/customer/auth/me")
      data = { authKind: "GUEST", role: "CUSTOMER" };
    if (path === "/admin/after-sales") {
      reads++;
      data = {
        items: removed
          ? []
          : [
              item,
              {
                ...ended,
                id: "as-active",
                orderCode: "DP1002",
                status: "REQUESTED",
              },
            ],
      };
    }
    if (path === "/admin/after-sales/as-ended") {
      reads++;
      data = item;
    }
    if (path === "/admin/orders/order-1")
      data = { id: "order-1", status: "DELIVERED" };
    if (path === "/admin/trash" && route.request().method() === "POST") {
      writes.push(route.request().postDataJSON());
      if (options.failure) {
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({
            error: {
              code: options.failure,
              message: "对象仍存在有效引用或未结业务",
              details: {
                blockers: [
                  "AFTER_SALE_ACTIVE:as-ended",
                  "REFUND_REVIEW_PENDING",
                ],
              },
            },
          }),
        });
        return;
      }
      removed = true;
      data = { id: "trash-1", targetType: "AFTER_SALE", targetId: item.id };
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ data }),
    });
  });
  return { writes, reads: () => reads };
}

test("ended cases enter the existing trash with a trimmed reason and refresh the list", async ({
  page,
}, info) => {
  const state = await fixture(page);
  await page.goto(`${base}/manage/after-sales`);
  const row = page.getByRole("row").filter({ hasText: "DP1001" });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "删除售后单" }).click();
  const dialog = page.getByRole("dialog", { name: "删除售后单" });
  await expect(dialog).toContainText("24 小时");
  await expect(dialog).toContainText("自动清理");
  await expect(dialog).toContainText("退款记录与订单财务记录保留");
  await expect(
    dialog.getByRole("button", { name: "移入回收站" }),
  ).toBeDisabled();
  await dialog.getByLabel("删除原因").fill("  已处理完毕，清理重复售后  ");
  await expect(dialog).toHaveCSS("opacity", "1");
  await page.screenshot({
    path: info.outputPath("after-sale-trash-confirm.png"),
    fullPage: false,
  });
  await dialog.getByRole("button", { name: "移入回收站" }).click();
  await expect(dialog).not.toBeVisible();
  await expect(row).toHaveCount(0);
  expect(state.writes).toEqual([
    {
      targetType: "AFTER_SALE",
      targetId: "as-ended",
      expectedVersion: 7,
      reason: "已处理完毕，清理重复售后",
    },
  ]);
});

test("blocked deletion preserves the reason and explains concrete unfinished work", async ({
  page,
}) => {
  await fixture(page, { failure: "TRASH_BLOCKED" });
  await page.goto(`${base}/manage/after-sales`);
  await page
    .getByRole("row")
    .filter({ hasText: "DP1001" })
    .getByRole("button", { name: "删除售后单" })
    .click();
  const dialog = page.getByRole("dialog", { name: "删除售后单" });
  await dialog.getByLabel("删除原因").fill("复核后删除");
  await dialog.getByRole("button", { name: "移入回收站" }).click();
  await expect(dialog.getByRole("alert")).toContainText("售后仍在处理中");
  await expect(dialog.getByRole("alert")).toContainText("退款仍待审核");
  await expect(dialog.getByLabel("删除原因")).toHaveValue("复核后删除");
  await expect(dialog).not.toContainText("配置已被其他管理员修改");
});

test("version conflict offers reload and leaves the entered reason visible", async ({
  page,
}) => {
  const state = await fixture(page, { failure: "VERSION_CONFLICT" });
  await page.goto(`${base}/manage/after-sales`);
  await page
    .getByRole("row")
    .filter({ hasText: "DP1001" })
    .getByRole("button", { name: "删除售后单" })
    .click();
  const dialog = page.getByRole("dialog", { name: "删除售后单" });
  await dialog.getByLabel("删除原因").fill("清理重复记录");
  await dialog.getByRole("button", { name: "移入回收站" }).click();
  await expect(dialog.getByRole("alert")).toContainText("售后单已更新");
  await expect(dialog.getByLabel("删除原因")).toHaveValue("清理重复记录");
  const before = state.reads();
  await dialog.getByRole("button", { name: "重新加载售后单" }).click();
  await expect.poll(state.reads).toBeGreaterThan(before);
  await expect(dialog).not.toBeVisible();
});

test("ongoing cases explain why deletion is disabled", async ({ page }) => {
  await fixture(page);
  await page.goto(`${base}/manage/after-sales`);
  const row = page.getByRole("row").filter({ hasText: "DP1002" });
  await expect(row.getByRole("button", { name: "删除售后单" })).toBeDisabled();
  await expect(row).toContainText("请先结束售后处理");
});

test("admins without data.manage see no delete entry in list or detail", async ({
  page,
}) => {
  await fixture(page, { capabilities: ["payment.manage"] });
  await page.goto(`${base}/manage/after-sales`);
  await expect(page.getByText("DP1001", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "删除售后单" })).toHaveCount(0);
  await page.goto(`${base}/manage/after-sales/as-ended`);
  await expect(page.getByRole("heading", { name: /售后案件/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "删除售后单" })).toHaveCount(0);
});

test("wildcard admin can delete from detail and return to list", async ({
  page,
}) => {
  const state = await fixture(page, { capabilities: ["*"] });
  await page.goto(`${base}/manage/after-sales/as-ended`);
  await page.getByRole("button", { name: "删除售后单" }).click();
  const dialog = page.getByRole("dialog", { name: "删除售后单" });
  await dialog.getByLabel("删除原因").fill("售后已结束");
  await dialog.getByRole("button", { name: "移入回收站" }).click();
  await expect(page).toHaveURL(`${base}/manage/after-sales`);
  expect(state.writes).toHaveLength(1);
});

test("ongoing detail cannot be deleted", async ({ page }) => {
  await fixture(page, { status: "REPRINTING" });
  await page.goto(`${base}/manage/after-sales/as-ended`);
  await expect(page.getByRole("button", { name: "删除售后单" })).toBeDisabled();
  await expect(
    page.getByText("请先结束售后处理", { exact: true }),
  ).toBeVisible();
});
