import { expect, test, type Page } from "@playwright/test";

type Surface = "finance" | "personal-payroll" | "admin-payroll";
async function mount(page: Page, surface: Surface) {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
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
      body: `export const ADMIN='/manage',DEMO=false;export const useStore=()=>({data:{},toast:()=>{}});`,
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
      body: `
    export const getIdentityGeneration=()=>0,advanceIdentity=()=>0,growthRequest=async()=>({});
    const salary={staffId:'admin',staffName:'管理员',entryCount:0,grossCents:0,deductionsCents:0,netCents:0,paidCents:0,unpaidCents:0,carryForwardCents:0,availableCents:0,needsConfigurationCount:0};
    export function useGrowthResource(path){let data=null;
      if(path==='/auth/me')data={id:'admin',role:'ADMIN',capabilities:['*']};
      else if(path?.startsWith('/admin/finance/summary'))data={month:'2026-11',confirmedReceivedCents:40,refundCents:0,netReceivedCents:40,paperPurchaseCents:0,inkPurchaseCents:0,otherExpenseCents:0,consumablesBalanceCents:40,paidWagesCents:0,balanceCents:40,paperSheets:0,paperUnitYuan:null,legacyArchiveCount:1};
      else if(path?.startsWith('/admin/finance/expenses'))data={items:[],total:0,page:1,pageSize:20};
      else if(path?.startsWith('/payroll/me'))data={staff:{id:'admin',name:'管理员'},period:'2026-11',summary:salary,entries:[],carryForward:[],settlements:[]};
      else if(path?.startsWith('/admin/payroll/ledger'))data={period:'2026-11',summaries:[],entries:[],carryForward:[],needsConfiguration:[]};
      else if(path?.startsWith('/admin/payroll/policies'))data={defaultPolicy:null,overrides:[]};
      else if(path?.startsWith('/admin/payroll/staff'))data=[];
      return {data,loading:false,refreshing:false,hasData:!!data,error:'',reload:async()=>{}};
    }`,
    }),
  );
  await page.route("**/native-month-fixture", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: `
    <!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><div id="root"></div>
    <script type="module">import R from '/@react-refresh';R.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;</script>
    <script type="module">import React from '${react}';import ReactDOM from '${dom}';import {MemoryRouter} from '${router}';import {AdminFinance} from '/src/admin/finance.tsx';import {StaffPayrollPanel,AdminPayrollPage} from '/src/v1/payroll.tsx';import '/src/styles.css';
      const surface=${JSON.stringify(surface)}, component=surface==='finance'?AdminFinance:surface==='personal-payroll'?StaffPayrollPanel:AdminPayrollPage;
      const content=React.createElement('main',{className:surface==='personal-payroll'?'courier-content':'admin-content'},React.createElement(component));
      ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(MemoryRouter,null,surface==='personal-payroll'?content:React.createElement('div',{className:'admin-main'},content)));
    </script></html>`,
    }),
  );
  await page.goto("/native-month-fixture");
  await expect(page.locator('input[type="month"]').first()).toBeVisible();
  expect(errors).toEqual([]);
}
async function expectContained(page: Page) {
  const metrics = await page.evaluate(() => {
    const root = document.documentElement;
    return {
      viewport: innerWidth,
      scroll: root.scrollWidth,
      offset: scrollX,
      controls: [
        ...document.querySelectorAll<HTMLInputElement>('input[type="month"]'),
      ].map((input) => {
        const field = input.closest(".field")!,
          panel = input.closest(".panel,.modal,.finance-toolbar")!;
        const rect = input.getBoundingClientRect(),
          box = field.getBoundingClientRect();
        const parent = panel.getBoundingClientRect(),
          css = getComputedStyle(panel);
        return {
          left: rect.left,
          right: rect.right,
          width: rect.width,
          fieldLeft: box.left,
          fieldRight: box.right,
          contentLeft: parent.left + parseFloat(css.paddingLeft),
          contentRight: parent.right - parseFloat(css.paddingRight),
          font: parseFloat(getComputedStyle(input).fontSize),
          height: rect.height,
        };
      }),
    };
  });
  expect(metrics.scroll, JSON.stringify(metrics)).toBeLessThanOrEqual(
    metrics.viewport + 1,
  );
  expect(metrics.offset).toBe(0);
  for (const control of metrics.controls) {
    expect(control.left, JSON.stringify(control)).toBeGreaterThanOrEqual(
      Math.max(0, control.fieldLeft, control.contentLeft) - 1,
    );
    expect(control.right, JSON.stringify(control)).toBeLessThanOrEqual(
      Math.min(metrics.viewport, control.fieldRight, control.contentRight) + 1,
    );
    expect(control.height).toBeGreaterThanOrEqual(44);
    if (metrics.viewport <= 540)
      expect(control.font).toBeGreaterThanOrEqual(16);
  }
}
for (const surface of [
  "finance",
  "personal-payroll",
  "admin-payroll",
] as Surface[]) {
  test(`${surface}: legacy native minimum cannot widen the field`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: 320, height: 844 });
    await mount(page, surface);
    // Stress the lower-specificity intrinsic minimum used by older native
    // themes. Desktop WebKit emulation alone does not reproduce UIKit styling.
    await page.addStyleTag({
      content: 'input[type="month"] { min-width: 320px; }',
    });
    await expectContained(page);
    expect(
      await page
        .locator('input[type="month"]')
        .first()
        .evaluate((el) => getComputedStyle(el).appearance),
    ).toBe("none");
  });
  test(`${surface}: native month remains inside its field and padded card`, async ({
    page,
  }, info) => {
    await mount(page, surface);
    for (const width of [320, 375, 390, 430, 768, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await expectContained(page);
      const input = page.locator('input[type="month"]').first();
      await input.fill("2026-10");
      await expect(input).toHaveValue("2026-10");
      if (width === 390 || width === 1440)
        await page.screenshot({
          path: info.outputPath(`${surface}-${width}.png`),
          fullPage: true,
        });
    }
    if (surface === "finance") {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.getByRole("button", { name: "累计", exact: true }).click();
      await expect(page.locator('input[type="month"]')).toHaveCount(0);
      await page.getByRole("button", { name: "月份", exact: true }).click();
      await expectContained(page);
    }
    if (surface === "admin-payroll") {
      await page.setViewportSize({ width: 320, height: 844 });
      await page.getByRole("button", { name: "人工条目", exact: true }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await expectContained(page);
    }
  });
}
