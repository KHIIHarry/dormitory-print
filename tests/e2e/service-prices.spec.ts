import { expect, test, type Page } from "@playwright/test";

async function mount(
  page: Page,
  surface: "help" | "settings",
  free: boolean | "blank" = false,
) {
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
      body: `export const ADMIN='/manage',DEMO=false;export const useStore=()=>window.priceFixture.store;`,
    }),
  );
  await page.route("**/src/growth/identity.tsx*", (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: `export const useIdentity=()=>({generation:0,staff:{id:'admin',role:'ADMIN',capabilities:['*']}});`,
    }),
  );
  await page.route("**/src/growth/client.ts*", (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: `export const getIdentityGeneration=()=>0,advanceIdentity=()=>0;
      export const growthRequest=async(path,options)=>{window.priceFixture.writes.push({path,body:options.body});return {version:2,...options.body}};
      export const useGrowthResource=()=>({data:{items:[]},loading:false,reload:async()=>{}});`,
    }),
  );
  await page.route("**/service-prices-fixture", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><div id="root"></div>
    <script type="module">import R from '/@react-refresh';R.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;</script>
    <script type="module">
      import React from '${react}';import ReactDOM from '${dom}';import {MemoryRouter} from '${router}';
      import {Help} from '/src/customer/help.tsx';import {Settings} from '/src/admin/operations.tsx';
      import {PriceRows} from '/src/components/ui.tsx';import {buildQuote} from '/src/growth/pricing.ts';
      import {createDemo} from '/src/lib/demo.ts';import '/src/styles.css';
      const data=createDemo();data.config.prices={bwSingle:40,bwDuplex:60,colorSingle:70,colorDuplex:95,photo:200,corner:5,side:10,delivery:250};
      if(${free === true})Object.keys(data.config.prices).forEach(k=>data.config.prices[k]=0);
      if(${free === "blank"}){data.config.prices.corner=null;data.config.prices.delivery=null;data.config.prices.bwSingle=0;}
      data.config.sections={prices:{version:1}};
      window.priceFixture={writes:[],store:{data,resources:{config:{hasData:true,loading:false}},refresh:async()=>{},toast:()=>{},act:async()=>true}};
      const policy={id:'p',version:1,enabled:false,effectiveAt:null,description:'',tiers:[]};
      const quote=buildQuote([{key:'binding',relatedId:'file',unit:'BOOKLET',quantity:3,unitPriceCents:5,amountCents:15,service:'BINDING'}],policy,[],{},null,undefined,${free === true ? 0 : 250});
      ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(MemoryRouter,null,
        ${surface === "help" ? "React.createElement(Help)" : "React.createElement('main',{className:'admin-content'},React.createElement(Settings))"},
        React.createElement('section',{'aria-label':'报价验证',className:'panel'},React.createElement(PriceRows,{quote,total:quote.totalCents}))));
    </script></html>`,
    }),
  );
  await page.goto("/service-prices-fixture");
  await expect(
    page.getByRole("heading", {
      name: surface === "help" ? "有什么可以帮到你？" : "系统配置",
    }),
  ).toBeVisible();
}

async function contained(page: Page) {
  const metrics = await page.evaluate(() => ({
    width: innerWidth,
    scroll: document.documentElement.scrollWidth,
    controls: [
      ...document.querySelectorAll<HTMLInputElement>(".price-config input"),
    ].map((el) => {
      const rect = el.getBoundingClientRect(),
        panel = el.closest(".panel")!;
      const p = panel.getBoundingClientRect(),
        style = getComputedStyle(panel);
      return {
        left: rect.left,
        right: rect.right,
        height: rect.height,
        font: parseFloat(getComputedStyle(el).fontSize),
        min: p.left + parseFloat(style.paddingLeft),
        max: p.right - parseFloat(style.paddingRight),
      };
    }),
    prices: [...document.querySelectorAll(".price-rows b")].map((el) => {
      const range = document.createRange();
      range.selectNodeContents(el);
      return range.getClientRects().length;
    }),
  }));
  expect(metrics.scroll).toBeLessThanOrEqual(metrics.width + 1);
  for (const c of metrics.controls) {
    expect(c.left).toBeGreaterThanOrEqual(c.min - 1);
    expect(c.right).toBeLessThanOrEqual(c.max + 1);
    expect(c.height).toBeGreaterThanOrEqual(44);
    if (metrics.width <= 540) expect(c.font).toBeGreaterThanOrEqual(16);
  }
  for (const lines of metrics.prices) expect(lines).toBeLessThanOrEqual(1);
}

test("help lists configured binding and delivery fees with correct units", async ({
  page,
}, info) => {
  await mount(page, "help");
  const rows = page.locator("aside .price-rows > div");
  await expect(rows).toHaveCount(8);
  await expect(rows.filter({ hasText: "左上角单钉" })).toContainText(
    "¥0.05 / 份",
  );
  await expect(rows.filter({ hasText: "左侧双钉" })).toContainText(
    "¥0.10 / 份",
  );
  await expect(rows.filter({ hasText: "配送费" })).toContainText("¥2.50 / 次");
  await expect(
    page.getByText(
      "按实际纸张计价，配送费为 0。装订收费规则尚未配置时暂不开放。",
    ),
  ).toHaveCount(0);
  const quote = page.getByRole("region", { name: "报价验证" });
  await expect(quote).toContainText("3份");
  await expect(quote.locator(".price-total")).toContainText("¥2.65");
  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    await contained(page);
    if ([390, 1440].includes(width))
      await page.screenshot({
        path: info.outputPath(`help-${width}.png`),
        fullPage: true,
      });
  }
});

test("all zero prices display red free labels", async ({ page }) => {
  await mount(page, "help", true);
  const prices = page.locator("aside .price-rows b");
  await expect(prices).toHaveCount(8);
  for (const price of await prices.all()) {
    await expect(price).toHaveText("免费");
    await expect(price).toHaveCSS("color", "rgb(187, 81, 72)");
  }
});

test("blank prices stay unconfigured while an individual zero is free", async ({
  page,
}) => {
  await mount(page, "help", "blank");
  const rows = page.locator("aside .price-rows > div");
  for (const label of ["左上角单钉", "配送费"]) {
    await expect(rows.filter({ hasText: label })).toContainText("暂未配置");
  }
  await expect(rows.filter({ hasText: "A4 黑白单面" }).locator("b")).toHaveText(
    "免费",
  );
  await expect(rows.filter({ hasText: "左侧双钉" })).toContainText(
    "¥0.10 / 份",
  );
});

test("settings edits and saves delivery in cents with binding per copy", async ({
  page,
}, info) => {
  await mount(page, "settings");
  await expect(
    page.getByLabel("左上角单钉（元 / 份）", { exact: true }),
  ).toHaveValue("0.05");
  await expect(
    page.getByLabel("左侧双钉（元 / 份）", { exact: true }),
  ).toHaveValue("0.1");
  const delivery = page.getByLabel("配送费（元 / 次）", { exact: true });
  await expect(delivery).toHaveValue("2.5");
  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    await contained(page);
    if ([390, 1440].includes(width))
      await page.screenshot({
        path: info.outputPath(`settings-${width}.png`),
        fullPage: true,
      });
  }
  for (const [input, expected] of [
    ["3.25", 325],
    ["0", 0],
    ["", null],
  ] as const) {
    await delivery.fill(input);
    await page.getByRole("button", { name: "预览并保存", exact: true }).click();
    await page
      .getByRole("button", { name: "确认保存当前分区", exact: true })
      .click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    // The aria-hidden exit snapshot retains the background's inert lock until
    // its animation completes. Wait for removal before editing the next price.
    await expect(page.locator(".modal-backdrop")).toHaveCount(0);
    expect(
      await page.evaluate(() => (window as any).priceFixture.writes.at(-1)),
    ).toMatchObject({
      path: "/admin/config/prices",
      body: { delivery: expected },
    });
  }
});
