import { expect, test } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { createDemo } from "../../src/lib/demo";

test.use({ serviceWorkers: "block" });

let server: ViteDevServer;
let baseURL: string;

test.beforeAll(async () => {
  process.env.VITE_DEMO_MODE = "false";
  const { parallelIndex, project } = test.info();
  server = await createServer({
    cacheDir: `node_modules/.vite-print-task-${project.name}`,
    server: {
      host: "127.0.0.1",
      port: 5235 + parallelIndex,
      hmr: false,
      watch: null,
    },
  });
  await server.listen();
  const address = server.httpServer?.address();
  if (!address || typeof address === "string") {
    throw new Error("Real-mode Vite server did not start");
  }
  baseURL = `http://127.0.0.1:${address.port}`;
});

test.afterAll(async () => {
  await server?.close();
  delete process.env.VITE_DEMO_MODE;
});

const cases = [
  { id: "long", sides: "DUPLEX", flip: "LONG_EDGE", label: "双面（长边翻转）" },
  {
    id: "short",
    sides: "DUPLEX",
    flip: "SHORT_EDGE",
    label: "双面（短边翻转）",
  },
  {
    id: "legacy",
    sides: "DUPLEX",
    flip: "NONE",
    label: "双面（翻转方向未记录）",
  },
  { id: "single", sides: "SINGLE", flip: "NONE", label: "单面" },
] as const;

test("打印中心和任务详情显示每项任务的双面翻转方向", async ({ page }, info) => {
  const tasks = cases.map(({ label, ...task }) => ({
    ...task,
    orderId: "order-1",
    pickupCode: "T8DUW",
    fileId: `file-${task.id}`,
    fileName: `${task.id}.pdf`,
    kind: "DOCUMENT",
    bindingType: "NONE",
    status: "COMPLETED",
    active: true,
    artifactId: `artifact-${task.id}`,
    artifactSha256: "a".repeat(64),
    pages: 20,
    sheetCount: task.sides === "DUPLEX" ? 10 : 20,
    copies: 1,
    colorMode: "BW",
    pageRange: "1-20",
    openAttemptId: null,
    lastAttempt: null,
    reprints: [],
    orderStatus: "PENDING_PRINT",
    orderVersion: 15,
    sheetCatalog: [],
  }));
  const { config } = createDemo();
  await page.route("**/api/v1/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    let data: unknown = {};
    if (path === "/api/v1/auth/me") {
      data = {
        id: "admin",
        role: "ADMIN",
        capabilities: ["*"],
        emailVerified: true,
      };
    } else if (
      path === "/api/v1/admin/config" ||
      path === "/api/v1/public/config"
    ) {
      data = config;
    } else if (path === "/api/v1/admin/print-tasks") {
      data = { tasks, counts: { COMPLETED: tasks.length }, nextTaskId: null };
    } else if (path.startsWith("/api/v1/admin/print-tasks/")) {
      data = tasks.find((task) => path.endsWith(`/${task.id}`));
    }
    return route.fulfill({ json: { data } });
  });

  await page.goto(`${baseURL}/manage/print`);
  for (const { id, label } of cases) {
    const card = page
      .locator(".print-task-card")
      .filter({ hasText: `${id}.pdf` });
    await expect(card.locator(".print-task-details > p")).toContainText(
      `· ${label} ·`,
    );
    await card.getByRole("link", { name: "打开任务" }).click();
    await expect(
      page.getByLabel("打印设置").getByText(label, { exact: true }),
    ).toBeVisible();
    await page.getByRole("link", { name: "返回打印中心" }).click();
  }

  const summary = page.locator(".print-task-details > p").first();
  await expect(summary).toBeVisible();
  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const overflow = await summary.evaluate((element) => {
      const parent = element.parentElement!;
      return Math.max(
        element.scrollWidth - element.clientWidth,
        parent.scrollWidth - parent.clientWidth,
        document.documentElement.scrollWidth - window.innerWidth,
      );
    });
    expect(overflow, `${width}px viewport`).toBeLessThanOrEqual(1);
    if (width === 320 || width === 1440) {
      await page
        .locator(".print-task-card")
        .first()
        .screenshot({
          path: info.outputPath(`print-task-${width}.png`),
        });
    }
  }
});
