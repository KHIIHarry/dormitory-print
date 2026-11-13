import { expect, test } from "@playwright/test";
import { PDFDocument, StandardFonts, rgb } from "pdf-lib";
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

const fileNames: Record<(typeof cases)[number]["id"], string> = {
  long: `${"LectureNotes".repeat(12)}.pdf`,
  short: "课堂练习.pdf",
  legacy: "历史上传资料.pdf",
  single: "",
};

test("打印中心和任务详情显示每项任务的双面翻转方向", async ({ page }, info) => {
  const tasks = cases.map(({ label, ...task }) => ({
    ...task,
    orderId: "order-1",
    pickupCode: "T8DUW",
    fileId: `file-${task.id}`,
    fileName: fileNames[task.id],
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
  const identification = {
    ...tasks[0], id: "identification", fileId: "", kind: "IDENTIFICATION",
    fileName: "订单识别页.pdf", sides: "SINGLE", flip: "NONE", pages: 2,
    sheetCount: 2, pageRange: "", artifactId: "artifact-identification",
    generatedAt: "2026-11-12T01:00:00+00:00", status: "PENDING",
  };
  const inProgress = {
    ...tasks[1], id: "another-order", orderId: "order-2", pickupCode: "R9KPF",
    fileName: "另一单.pdf", status: "ATTEMPTED", openAttemptId: "existing-attempt",
    actualStartedAt: "2026-11-12T01:00:00+00:00",
  };
  const companion = {
    ...inProgress,
    id: "another-pending",
    fileName: "同单待打印文件.pdf",
    status: "PENDING",
    openAttemptId: null as string | null,
    actualStartedAt: null as string | null,
    refundLocked: false,
  };
  const queue = [...tasks, identification, inProgress, companion];
  const { config } = createDemo();
  const pdf = await PDFDocument.create();
  const sample = pdf.addPage([595, 842]);
  const font = await pdf.embedFont(StandardFonts.Helvetica);
  sample.drawText("Sample print document", {
    x: 56, y: 760, size: 24, font, color: rgb(0.12, 0.12, 0.12),
  });
  for (let index = 0; index < 12; index++) {
    sample.drawText("Print settings and page order verification", {
      x: 56, y: 700 - index * 24, size: 13, font,
    });
  }
  const pdfBytes = Buffer.from(await pdf.save());
  const starts: unknown[] = [];
  await page.addInitScript(() => { window.open = () => null; });
  await page.route("**/api/v1/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith("/api/v1/assets/"))
      return route.fulfill({ contentType: "application/pdf", body: pdfBytes });
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
      data = { tasks: queue, counts: {}, nextTaskId: inProgress.id };
    } else if (path.endsWith("/another-pending/attempts")) {
      companion.openAttemptId = "new-attempt";
      companion.status = "ATTEMPTED";
      companion.orderVersion++;
      data = { attemptId: companion.openAttemptId };
    } else if (path.endsWith("/order-2/start-production")) {
      starts.push(route.request().postDataJSON());
      companion.actualStartedAt = "2026-11-12T02:00:00+00:00";
      companion.orderVersion++;
      data = { actualStartedAt: companion.actualStartedAt, version: companion.orderVersion };
    } else if (path.startsWith("/api/v1/admin/print-tasks/")) {
      data = queue.find((task) => path.endsWith(`/${task.id}`));
    }
    return route.fulfill({ json: { data } });
  });

  await page.goto(`${baseURL}/manage/print`);
  const order = page.getByRole("button", { name: /T8DUW/ });
  await expect(order).toHaveAttribute("aria-expanded", "false");
  await expect(order).toContainText("打印已完成");
  await expect(order).toContainText("4/4 项完成");
  const second = page.getByRole("button", { name: /R9KPF/ });
  await expect(second).toContainText("已开印，待完成");
  await expect(second).toContainText("待结果确认");
  await expect(page.locator(".print-task-card")).toHaveCount(0);
  await order.click();
  const firstCard = page.locator(".print-task-card").first();
  await expect(firstCard).toContainText("订单识别页.pdf");
  await expect(firstCard).toContainText("可选");
  await expect(page.getByRole("link", { name: /下一项/ })).toHaveAttribute("href", "/manage/print/another-order");
  await expect(page.getByText("另一单.pdf")).toBeHidden();
  await firstCard.getByRole("link", { name: "打开任务" }).click();
  await expect(page.getByRole("heading", { name: "识别页记录" })).toBeVisible();
  await expect(page.getByRole("button", { name: "确认已开印", exact: true })).toHaveCount(0);
  const checkPdfLayout = async (kind: "identification" | "document") => {
    const pdfPanel = page.locator(".panel").filter({
      has: page.getByRole("link", { name: "打开最终 PDF", exact: true }),
    });
    for (const width of [320, 375, 390, 430, 768, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      const layout = await pdfPanel.evaluate((panel) => {
        const actions = panel.querySelector(".button-row")!;
        const hint = panel.querySelector("p.muted")!;
        const frame = panel.getBoundingClientRect();
        const padding = getComputedStyle(panel);
        const left = frame.left + parseFloat(padding.paddingLeft);
        const right = frame.right - parseFloat(padding.paddingRight);
        const hintBox = hint.getBoundingClientRect();
        return {
          overflow: document.documentElement.scrollWidth - innerWidth,
          hintGap: hintBox.top - actions.getBoundingClientRect().bottom,
          hintLineHeight: parseFloat(getComputedStyle(hint).lineHeight),
          hintFits: hintBox.left >= left - 1 && hintBox.right <= right + 1,
          buttons: [...actions.querySelectorAll(".btn")].map((button) => {
            const box = button.getBoundingClientRect();
            const style = getComputedStyle(button);
            return {
              height: box.height,
              fontSize: parseFloat(style.fontSize),
              letterSpacing: style.letterSpacing,
              fits: box.left >= left - 1 && box.right <= right + 1,
            };
          }),
        };
      });
      expect(layout.overflow, `${width}px task page overflow`).toBeLessThanOrEqual(1);
      expect(layout.hintGap, `${width}px gap below PDF actions`).toBeGreaterThanOrEqual(8);
      expect(layout.hintLineHeight, `${width}px hint line height`).toBeGreaterThanOrEqual(20);
      expect(layout.hintFits).toBe(true);
      expect(layout.buttons).toHaveLength(2);
      for (const button of layout.buttons) {
        expect(button.height, `${width}px PDF action target`).toBeGreaterThanOrEqual(44);
        expect(button.fontSize).toBeGreaterThanOrEqual(14);
        expect(button.letterSpacing).toBe("normal");
        expect(button.fits).toBe(true);
      }
      if (width === 390 || width === 1440) {
        await pdfPanel.locator(".print-pdf-tools").screenshot({
          path: info.outputPath(`pdf-actions-${kind}-${width}.png`),
        });
      }
    }
    await expect(pdfPanel.getByText("打印结束后回到本页确认结果。", { exact: false })).toBeVisible();
  };
  await checkPdfLayout("identification");
  await page.getByRole("link", { name: "返回打印中心" }).click();
  for (const { id, label } of cases) {
    const fileName = fileNames[id] || "文件名未记录";
    const card = page.locator(".print-task-card").filter({ hasText: fileName });
    await expect(card.getByText(fileName, { exact: true })).toBeVisible();
    await expect(card).not.toContainText(`任务 ${id}`);
    await expect(card).not.toContainText("SHA");
    await expect(card.locator(".print-task-details > p")).toContainText(
      `· ${label} ·`,
    );
    await card.getByRole("link", { name: "打开任务" }).click();
    await expect(
      page.locator(".page-heading").getByText(fileName, { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByLabel("打印设置").getByText(label, { exact: true }),
    ).toBeVisible();
    await expect(page.getByText("SHA-256", { exact: true })).toBeHidden();
    await page.getByText("任务技术信息", { exact: true }).click();
    await expect(page.getByText("SHA-256", { exact: true })).toBeVisible();
    await page.getByRole("link", { name: "返回打印中心" }).click();
    await expect(order).toHaveAttribute("aria-expanded", "true");
  }

  const summary = page.locator(".print-task-card")
    .filter({ hasText: fileNames.long }).locator(".print-task-details > p");
  await expect(summary).toBeVisible();
  await second.click();
  const secondCard = page.locator(".print-order-card").filter({ has: second });
  await expect(secondCard.getByText("请先核实本次打印结果")).toBeVisible();
  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const name = page.getByText(fileNames.long, { exact: true });
    const nameFits = await name.evaluate((element) => {
      const heading = element
        .closest(".print-task-top")!
        .getBoundingClientRect();
      const range = document.createRange();
      range.selectNodeContents(element);
      return Array.from(range.getClientRects()).every(
        (rect) => rect.right <= heading.right + 1,
      );
    });
    expect(nameFits, `${width}px filename`).toBe(true);
    const overflow = await summary.evaluate((element) => {
      const parent = element.parentElement!;
      return Math.max(
        element.scrollWidth - element.clientWidth,
        parent.scrollWidth - parent.clientWidth,
        document.documentElement.scrollWidth - window.innerWidth,
      );
    });
    expect(overflow, `${width}px viewport`).toBeLessThanOrEqual(1);
    const targetHeight = await order.evaluate((element) => element.getBoundingClientRect().height);
    expect(targetHeight, `${width}px order toggle`).toBeGreaterThanOrEqual(44);
    const actions = await secondCard.locator(".print-task-card").evaluateAll((rows) =>
      rows.map((row) => {
        const button = row.querySelector(".task-card-actions .btn")!.getBoundingClientRect();
        const badge = row.querySelector(".badge")!.getBoundingClientRect();
        return {
          buttonRight: button.right,
          buttonHeight: button.height,
          badgeRight: badge.right,
          centerOffset: Math.abs((button.top + button.bottom - badge.top - badge.bottom) / 2),
        };
      }),
    );
    expect(actions).toHaveLength(2);
    expect(Math.abs(actions[0].buttonRight - actions[1].buttonRight), `${width}px button column`).toBeLessThanOrEqual(1);
    expect(Math.abs(actions[0].badgeRight - actions[1].badgeRight), `${width}px status column`).toBeLessThanOrEqual(1);
    for (const action of actions) {
      expect(action.centerOffset, `${width}px status and action centered`).toBeLessThanOrEqual(1);
      expect(action.buttonHeight).toBeGreaterThanOrEqual(44);
    }
    if (width === 320 || width === 1440) {
      await page
        .locator(".print-order-card")
        .first()
        .screenshot({
          path: info.outputPath(`print-task-${width}.png`),
        });
    }
  }
  await secondCard.locator(".print-task-card").filter({ hasText: companion.fileName })
    .getByRole("link", { name: "打开任务" }).click();
  await expect(page.getByRole("heading", { name: "实际开印", exact: true })).toHaveCount(0);
  const confirm = page.getByRole("button", { name: "确认已开印", exact: true });
  await expect(confirm).toHaveCount(0);
  await checkPdfLayout("document");
  await page.getByRole("button", { name: "记录打印尝试并打开 PDF" }).click();
  await expect(confirm).toBeVisible();
  await expect(page.getByText("打印窗口被拦截", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "关闭提示" }).click();
  await expect(page.getByText("打印窗口被拦截", { exact: false })).toBeHidden();
  expect(starts).toHaveLength(0);
  await expect(page.getByRole("button", { name: "确认记录当前尝试" })).toBeDisabled();
  await expect(page.locator(".preview-canvas canvas")).toHaveAttribute("width", /\d{4}/);
  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const preview = page.locator(".print-task-full-width");
    const gap = await preview.evaluate((layout) =>
      layout.getBoundingClientRect().right - layout.firstElementChild!.getBoundingClientRect().right,
    );
    expect(gap, `${width}px preview right edge`).toBeLessThanOrEqual(1);
    const confirmBox = await confirm.boundingBox();
    expect(confirmBox!.height).toBeGreaterThanOrEqual(44);
    expect(confirmBox!.x + confirmBox!.width).toBeLessThanOrEqual(width);
    if (width === 390 || width === 1440) {
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: info.outputPath(`print-task-confirm-${width}.png`), fullPage: true, scale: "css" });
    }
    const canvasRatioError = await page.locator(".preview-canvas canvas").evaluate((canvas: HTMLCanvasElement) => {
      const box = canvas.getBoundingClientRect();
      return Math.abs(box.width / box.height - canvas.width / canvas.height);
    });
    expect(canvasRatioError).toBeLessThan(0.01);
    await confirm.click();
    const dialog = page.getByRole("dialog", { name: "确认已开印" });
    const modalLayout = await dialog.evaluate((element) => {
      const box = element.getBoundingClientRect();
      const field = element.querySelector("input")!;
      return {
        fits: box.left >= 0 && box.right <= innerWidth,
        scrollOverflow: element.scrollWidth - element.clientWidth,
        inputFont: parseFloat(getComputedStyle(field).fontSize),
        targets: [...element.querySelectorAll("button")].map((button) => button.getBoundingClientRect().height),
      };
    });
    expect(modalLayout.fits).toBe(true);
    expect(modalLayout.scrollOverflow).toBeLessThanOrEqual(1);
    expect(modalLayout.inputFont).toBeGreaterThanOrEqual(16);
    for (const height of modalLayout.targets) expect(height).toBeGreaterThanOrEqual(44);
    if (width === 390 || width === 1440)
      await page.screenshot({ path: info.outputPath(`print-start-dialog-${width}.png`), scale: "css" });
    await dialog.getByRole("button", { name: "返回", exact: true }).click();
    await expect(dialog).toBeHidden();
  }
  await confirm.click();
  const dialog = page.getByRole("dialog", { name: "确认已开印" });
  await expect(dialog).toContainText("打印中");
  await dialog.getByRole("button", { name: "返回", exact: true }).click();
  await expect(dialog).toBeHidden();
  expect(starts).toHaveLength(0);
  await confirm.click();
  companion.refundLocked = true;
  companion.orderVersion++;
  await dialog.getByRole("button", { name: "确认开印", exact: true }).click();
  await expect(dialog).toBeHidden();
  expect(starts).toEqual([{ expectedVersion: 17, attemptId: "new-attempt", reason: "" }]);
  await expect(page.locator(".print-pdf-tools").getByText("已开印", { exact: true })).toBeVisible();
  await expect(page.getByText("退款审核期间暂停新的打印和补打。", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "建立补打并记录尝试" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "确认记录当前尝试" })).toBeEnabled();
});

test("用户订单在实际开印后自动显示打印中并可筛选", async ({ page }, info) => {
  const { config, buildings, slots, orders } = createDemo();
  const order = { ...orders[1], actualStartedAt: null as string | null };
  order.files[0].printStatus = "ATTEMPTED";
  await page.clock.install();
  await page.route("**/api/v1/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    let data: unknown = {};
    if (path === "/api/v1/public/config") data = config;
    else if (path === "/api/v1/public/map") data = { items: buildings, campuses: [] };
    else if (path === "/api/v1/public/slots") data = { items: slots };
    else if (path === "/api/v1/customer/orders") data = { items: [order] };
    else if (path === `/api/v1/orders/${order.id}`) data = order;
    else if (path.endsWith("/after-sales")) data = { items: [] };
    return route.fulfill({ json: { data } });
  });

  await page.goto(`${baseURL}/u/orders`);
  const card = page.locator(".customer-order-card");
  await expect(card.getByText("待打印", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "打印中", exact: true }).click();
  await expect(card).toHaveCount(0);
  await page.getByRole("button", { name: "待打印", exact: true }).click();
  await card.click();
  const status = page.locator(".detail-status");
  await expect(status.getByText("待打印", { exact: true })).toBeVisible();
  await expect(page.getByText("等待打印结果确认", { exact: true })).toHaveCount(0);
  order.actualStartedAt = "2026-11-12T02:00:00+00:00";
  await page.clock.fastForward(10_000);
  await expect(status.getByText("打印中", { exact: true })).toBeVisible();
  await expect(page.getByText("等待打印结果确认", { exact: true })).toBeVisible();
  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1);
    if (width === 390 || width === 1440)
      await status.screenshot({ path: info.outputPath(`customer-printing-${width}.png`), scale: "css" });
  }
  await page.getByRole("link", { name: "返回我的订单" }).click();
  await page.getByRole("button", { name: "待打印", exact: true }).click();
  await expect(card).toHaveCount(0);
  await page.getByRole("button", { name: "打印中", exact: true }).click();
  await expect(card.getByText("打印中", { exact: true })).toBeVisible();
  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1);
    const tab = await page.getByRole("button", { name: "打印中", exact: true }).boundingBox();
    expect(tab!.height).toBeGreaterThanOrEqual(44);
  }
  order.status = "READY_FOR_DELIVERY";
  await page.clock.fastForward(10_000);
  await expect(card).toHaveCount(0);
  await page.getByRole("button", { name: "待配送", exact: true }).click();
  await expect(card.getByText("待配送", { exact: true })).toBeVisible();
  order.status = "PENDING_PRINT";
  await page.goto(`${baseURL}/u/orders/${order.id}/contact`);
  await expect(page.locator(".contact-order").getByText("打印中", { exact: true })).toBeVisible();
  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1);
  }
});
