import { expect, test, type Page } from "@playwright/test";

type Surface = "admin" | "courier";
type Scenario = "photo" | "exceptions" | "missing" | "pending" | "unconfirmed";

const orderFileNames = [
  "报告.docx",
  "免费_PDF打印版_AIGC检测报告_[商务数据分析期中课程论文+示例24111].pdf",
  `${"ResearchReport2026".repeat(12)}.pdf`,
];

async function mount(
  page: Page,
  surface: Surface,
  scenario: Scenario = "photo",
) {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const main = await (await page.request.get("/src/main.tsx")).text();
  const react = main.match(/from "([^"]*\/react\.js\?[^"]*)"/)![1];
  const dom = main.match(/from "([^"]*\/react-dom_client\.js\?[^"]*)"/)![1];
  const source = await (await page.request.get("/src/admin/orders.tsx")).text();
  const router = source.match(
    /from "([^"]*\/react-router-dom\.js\?[^"]*)"/,
  )![1];
  await page.route("**/src/lib/store.tsx*", (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: `export const ADMIN='/manage',DEMO=false; export const useStore=()=>window.proofFixture.store;`,
    }),
  );
  await page.route("**/src/growth/identity.tsx*", (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: `export const useIdentity=()=>({generation:window.proofFixture?.generation||0,staff:{id:'staff-2',role:'${surface === "admin" ? "ADMIN" : "COURIER"}',capabilities:['*']}});`,
    }),
  );
  await page.route("**/src/growth/client.ts*", (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: `export const getIdentityGeneration=()=>window.proofFixture?.generation||0,advanceIdentity=()=>0;
      export const growthRequest=async()=>({items:[],summary:{totalCents:0,paidCents:0,remainingCents:0,activeCount:0},enabled:true});
      export const useGrowthResource=()=>({data:{items:[]},hasData:true,loading:false,error:'',reload:async()=>{}});`,
    }),
  );
  await page.route("**/api/v1/assets/proof-photo/content*", async (route) => {
    // A local, deterministic image stands in for the protected binary endpoint.
    await route.fulfill({
      contentType: "image/svg+xml",
      body: `<svg xmlns="http://www.w3.org/2000/svg" width="900" height="680" viewBox="0 0 900 680"><rect width="900" height="680" fill="#dcc7b0"/><rect y="450" width="900" height="230" fill="#9b7860"/><rect x="220" y="120" width="460" height="430" rx="8" fill="#fffaf3"/><rect x="250" y="150" width="400" height="370" fill="#f4ede3" stroke="#d3bba2"/><text x="300" y="250" font-size="42" fill="#654d3b">DormPrint</text><text x="300" y="310" font-size="26" fill="#725b48">YVSPS</text><path d="M300 360h290m-290 35h290m-290 35h200" stroke="#c8b49c" stroke-width="9"/></svg>`,
    });
  });
  await page.route("**/proof-details-fixture", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><div id="root"></div>
    <script type="module">import R from '/@react-refresh';R.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;</script>
    <script type="module">
      import React from '${react}';import ReactDOM from '${dom}';import {MemoryRouter,Routes,Route} from '${router}';
      import {AdminOrderDetail} from '/src/admin/orders.tsx';import {CourierOrder} from '/src/courier/pages.tsx';
      import {createDemo,makeFile} from '/src/lib/demo.ts';import '/src/styles.css';
      const surface=${JSON.stringify(surface)},scenario=${JSON.stringify(scenario)};
      const data=createDemo(),order=data.orders[0];
      Object.assign(order,{id:'proof-order',pickupCode:'YVSPS',status:scenario==='pending'?'DELIVERING':'DELIVERED',packingStatus:'PACKED',batchId:'proof-batch',nickname:'验收同学',phone:'13800138000',history:[{title:'资料已送达',time:'2026-11-05T05:10:38Z',description:'交付凭证已记录'}]});
      if(scenario==='pending')order.allowedActions=['SUBMIT_PROOF','REPORT_EXCEPTION'];
      if(scenario==='unconfirmed')Object.assign(order,{status:'PENDING_CONFIRMATION',packingStatus:'PENDING',files:${JSON.stringify(orderFileNames)}.map(name=>makeFile(name,19))});
      const proof={placementNote:'资料放在一楼收发台左侧，请及时领取。'+ '长备注与连续标识'.repeat(12)+'ABCDE'.repeat(20),deliveredAt:'2026-11-05T05:10:38Z',position:{latitude:31.490191,longitude:121.829420,accuracyMeters:12,capturedAt:'2026-11-05T05:10:20Z',coordinateSystem:'WGS84'}};
      if(surface==='admin')proof.photoUrl='/api/v1/assets/proof-photo/content';else proof.photoAssetId='proof-photo';
      if(scenario==='exceptions'){delete proof.photoUrl;delete proof.photoAssetId;delete proof.position;proof.skipPhotoReason='收件人已当面领取，明确要求不拍照';proof.skipLocationReason='楼内无信号，管理员已核对交付位置';}
      order.proof=['missing','pending','unconfirmed'].includes(scenario)?undefined:proof;
      data.orders=[order];data.batches=[{id:'proof-batch',date:'2026-11-05',time:'11:30 – 13:30',orderIds:[order.id],courierId:'staff-2',status:scenario==='pending'?'DELIVERING':'COMPLETED',version:1}];
      window.proofFixture={generation:0,store:{data,role:surface==='admin'?'ADMIN':'COURIER',resources:{},toast:()=>{},act:async()=>true,updateOrder:async()=>{},refresh:async()=>{},actionIssues:{}}};
      const component=surface==='admin'?AdminOrderDetail:CourierOrder,path=surface==='admin'?'/manage/orders/proof-order':'/courier/orders/proof-order';
      const root=ReactDOM.createRoot(document.getElementById('root'));
      window.proofFixture.render=()=>{const detail=React.createElement('main',{className:surface==='admin'?'admin-content':'courier-content'},React.createElement(component));root.render(React.createElement(MemoryRouter,{initialEntries:[path]},React.createElement(Routes,null,React.createElement(Route,{path:surface==='admin'?'/manage/orders/:id':'/courier/orders/:id',element:surface==='admin'?React.createElement('div',{className:'admin-main'},detail):detail}))));};
      window.proofFixture.render();
    </script></html>`,
    }),
  );
  await page.goto("/proof-details-fixture");
  await expect(
    page.getByRole("heading", {
      name: surface === "admin" ? "订单 YVSPS" : "配送订单",
      exact: true,
    }),
  ).toBeVisible();
  expect(errors).toEqual([]);
  return errors;
}

test("后台订单详情的长文件名完整换行，六档宽度不溢出", async ({
  page,
}, info) => {
  await mount(page, "admin", "unconfirmed");
  const panel = page.locator(".detail-grid > div > .panel").filter({
    has: page.getByRole("heading", { name: "打印文件与任务", exact: true }),
  });
  await expect(panel.locator(".file-info strong")).toHaveText(orderFileNames);
  await page.evaluate(() => document.fonts.ready);

  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const metrics = await panel.evaluate((element) => {
      const card = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      const contentRight =
        card.right - parseFloat(style.paddingRight) - element.clientLeft;
      const names = [...element.querySelectorAll(".file-info strong")].map(
        (name) => {
          const range = document.createRange();
          range.selectNodeContents(name);
          const lines = [...range.getClientRects()];
          return {
            lineCount: new Set(lines.map((line) => Math.round(line.top))).size,
            right: Math.max(...lines.map((line) => line.right)),
            availableRight: name.getBoundingClientRect().right,
          };
        },
      );
      return {
        width: document.documentElement.clientWidth,
        scrollWidth: document.documentElement.scrollWidth,
        scrollX,
        cardRight: card.right,
        badgeRight: Math.max(
          ...[...element.querySelectorAll(".file-row .badge")].map(
            (badge) => badge.getBoundingClientRect().right,
          ),
        ),
        contentRight,
        names,
      };
    });
    expect(metrics.scrollWidth, `${width}px viewport`).toBeLessThanOrEqual(
      metrics.width + 1,
    );
    expect(metrics.scrollX).toBe(0);
    expect(metrics.cardRight).toBeLessThanOrEqual(metrics.width);
    expect(metrics.badgeRight).toBeLessThanOrEqual(metrics.contentRight + 1);
    expect(metrics.names[0].lineCount).toBe(1);
    if (width <= 430) expect(metrics.names[1].lineCount).toBeGreaterThan(1);
    expect(metrics.names[2].lineCount).toBeGreaterThan(1);
    for (const name of metrics.names) {
      expect(name.right).toBeLessThanOrEqual(name.availableRight + 1);
    }
    if (width === 320 || width === 1440) {
      await panel.screenshot({
        path: info.outputPath(`order-file-names-${width}.png`),
      });
    }
  }
});

async function expectContained(page: Page) {
  const metrics = await page.evaluate(() => {
    const panel = document.querySelector<HTMLElement>(".delivery-proof-panel")!;
    const box = panel.getBoundingClientRect(),
      style = getComputedStyle(panel);
    const left = box.left + parseFloat(style.paddingLeft),
      right = box.right - parseFloat(style.paddingRight);
    return {
      scroll: document.documentElement.scrollWidth,
      viewport: innerWidth,
      offset: scrollX,
      rootOverflow: [
        getComputedStyle(document.documentElement).overflowX,
        getComputedStyle(document.body).overflowX,
      ],
      items: [
        ...panel.querySelectorAll<HTMLElement>(
          ".delivery-proof-layout,.delivery-proof-photo,.delivery-proof-facts,dd,button",
        ),
      ]
        .filter((el) => el.getClientRects().length > 0)
        .map((el) => ({
          left: el.getBoundingClientRect().left,
          right: el.getBoundingClientRect().right,
          parentLeft: left,
          parentRight: right,
          tag: el.tagName,
          height: el.getBoundingClientRect().height,
        })),
    };
  });
  expect(metrics.scroll, JSON.stringify(metrics)).toBeLessThanOrEqual(
    metrics.viewport + 1,
  );
  expect(metrics.offset).toBe(0);
  expect(metrics.rootOverflow).not.toContain("hidden");
  expect(metrics.rootOverflow).not.toContain("clip");
  for (const item of metrics.items) {
    expect(item.left, JSON.stringify(item)).toBeGreaterThanOrEqual(
      item.parentLeft - 1,
    );
    expect(item.right, JSON.stringify(item)).toBeLessThanOrEqual(
      item.parentRight + 1,
    );
    if (item.tag === "BUTTON") expect(item.height).toBeGreaterThanOrEqual(44);
  }
}

for (const surface of ["admin", "courier"] as const) {
  test(`${surface} 订单详情嵌入交付凭证，六档宽度不溢出`, async ({ page }) => {
    const errors = await mount(page, surface);
    await expect(
      page.getByRole("heading", { name: "交付凭证", exact: true }),
    ).toBeVisible({ timeout: 5000 });
    const panel = page.locator(".delivery-proof-panel");
    await expect(
      panel.getByRole("img", { name: "资料实际交付照片", exact: true }),
    ).toBeVisible();
    await expect(
      panel.getByText("31.490191, 121.829420", { exact: true }),
    ).toBeVisible();
    await expect(
      panel.getByText("WGS84 · 精度 ±12 m", { exact: true }),
    ).toBeVisible();
    await expect(
      panel.getByText("2026/11/05 13:10:38", { exact: true }),
    ).toBeVisible();
    await expect(
      panel.getByText("2026/11/05 13:10:20", { exact: true }),
    ).toBeVisible();
    for (const width of [320, 375, 390, 430, 768, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await expectContained(page);
      await panel.scrollIntoViewIfNeeded();
      await page.screenshot({
        path: `output/delivery-proof-qa/${surface}-${test.info().project.name}-${width}.png`,
        fullPage: true,
      });
    }
    await page.setViewportSize({ width: 390, height: 844 });
    const open = panel.getByRole("button", {
      name: "查看交付照片大图",
      exact: true,
    });
    await open.click();
    const dialog = page.getByRole("dialog", { name: "交付照片", exact: true });
    await expect(dialog).toBeVisible();
    const close = await dialog
      .getByRole("button", { name: "关闭", exact: true })
      .boundingBox();
    expect(close!.height).toBeGreaterThanOrEqual(44);
    await page.screenshot({
      path: `output/delivery-proof-qa/${surface}-${test.info().project.name}-photo-dialog.png`,
      fullPage: true,
    });
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(open).toBeFocused();
    expect(errors).toEqual([]);
  });

  test(`${surface} 照片与定位例外独立显示`, async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 844 });
    await mount(page, surface, "exceptions");
    const panel = page.locator(".delivery-proof-panel");
    await expect(
      panel.getByText("收件人已当面领取，明确要求不拍照", { exact: true }),
    ).toBeVisible();
    await expect(
      panel.getByText("楼内无信号，管理员已核对交付位置", { exact: true }),
    ).toBeVisible();
    await expect(panel.getByRole("img")).toHaveCount(0);
    await expectContained(page);
  });

  test(`${surface} 照片读取失败可重试，不伪装成免拍照`, async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 844 });
    await mount(page, surface);
    await page.route("**/api/v1/assets/proof-photo/content*", (route) =>
      route.fulfill({ status: 403, body: "forbidden" }),
    );
    await page.reload();
    await expect(
      page.getByText("交付照片暂时无法查看", { exact: true }),
    ).toBeVisible();
    await expect(
      page
        .locator(".delivery-proof-panel")
        .getByText("免拍照说明", { exact: true }),
    ).toHaveCount(0);
    await expectContained(page);
    const contained = await page
      .locator(".delivery-proof-photo-stage")
      .evaluate((stage) => {
        const box = stage.getBoundingClientRect(),
          status = stage
            .querySelector(".delivery-proof-photo-status")!
            .getBoundingClientRect();
        return status.top >= box.top - 1 && status.bottom <= box.bottom + 1;
      });
    expect(contained).toBe(true);
    await page.screenshot({
      path: `output/delivery-proof-qa/${surface}-${test.info().project.name}-photo-error.png`,
      fullPage: true,
    });
    await page.unroute("**/api/v1/assets/proof-photo/content*");
    await page.route("**/api/v1/assets/proof-photo/content*", (route) =>
      route.fulfill({
        path: "public/assets/study-desk.webp",
        contentType: "image/webp",
      }),
    );
    await page
      .getByRole("button", { name: "重新加载照片", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "查看交付照片大图", exact: true }),
    ).toBeEnabled();
  });

  test(`${surface} 身份切换立即关闭旧照片预览`, async ({ page }) => {
    await mount(page, surface);
    await page
      .getByRole("button", { name: "查看交付照片大图", exact: true })
      .click();
    await expect(
      page.getByRole("dialog", { name: "交付照片", exact: true }),
    ).toBeVisible();
    await page.evaluate(() => {
      const fixture = (window as any).proofFixture;
      fixture.generation++;
      fixture.store.data.orders[0].proof = undefined;
      window.dispatchEvent(new Event("dormprint:identity"));
      fixture.render();
    });
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(
      page.getByRole("img", { name: "资料实际交付照片", exact: true }),
    ).toHaveCount(0);
  });

  test(`${surface} 已送达无凭证显示真实缺失状态`, async ({ page }) => {
    await mount(page, surface, "missing");
    await expect(
      page
        .locator(".delivery-proof-panel")
        .getByText("暂无交付凭证", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "查看交付照片大图", exact: true }),
    ).toHaveCount(0);
  });

  test(`${surface} 配送中保留原操作且不展示交付凭证`, async ({ page }) => {
    await mount(page, surface, "pending");
    await expect(page.locator(".delivery-proof-panel")).toHaveCount(0);
    if (surface === "courier")
      await expect(
        page.getByRole("link", { name: "拍照并完成交付", exact: true }),
      ).toBeVisible();
    else
      await expect(
        page.getByRole("link", { name: "处理配送交付", exact: true }),
      ).toBeVisible();
  });
}
