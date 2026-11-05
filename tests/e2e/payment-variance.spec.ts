import { expect, test, type Page } from "@playwright/test";

test.use({ channel: "chrome" });

// Mount the real detail/form component with a deterministic store; API cash
// persistence, permissions and transaction behavior are covered in PostgreSQL.
async function fixture(page: Page, legacyReceipt = false, exports = false) {
  await page.emulateMedia({ reducedMotion: "reduce" });
  if (exports)
    await page.route("**/src/growth/client.ts*", (route) =>
      route.fulfill({
        contentType: "text/javascript",
        body: `export const getIdentityGeneration=()=>0,advanceIdentity=()=>0;
      export const useGrowthResource=()=>({data:{id:'admin',role:'ADMIN',capabilities:['export.manage']},loading:false,error:'',reload:async()=>{}});
      export async function growthRequest(path,options={}){window.fixture.calls.push({path,body:options.body});return {id:'export',status:'READY'}}`,
      }),
    );
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
      body: `import React from '${react}';
      export const ADMIN='/manage',DEMO=false;
      export function useStore(){const [,tick]=React.useState(0);return {
        data:window.fixture.data,actionIssues:{},refresh:async()=>{},updateOrder:()=>{},
        toast:(message)=>window.fixture.toast=message,
        act:async(path,body,update)=>{window.fixture.calls.push({path,body});window.fixture.data=update(window.fixture.data);tick(v=>v+1);return true}
      }}`,
    }),
  );
  await page.route("**/src/growth/identity.tsx*", (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: `export const useIdentity=()=>({staff:{id:'admin',capabilities:['*']},customer:{authKind:'ANONYMOUS'}});`,
    }),
  );
  await page.route("**/src/growth/order-links.tsx*", (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: "export const AdminAccessLinks=()=>null;",
    }),
  );
  await page.route("**/src/v1/supplementary.tsx*", (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: "export const AdminSupplementaryPanel=()=>null;",
    }),
  );
  await page.route("**/payment-variance-fixture", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: `<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><div id="root" class="admin-content"></div>
      <script type="module">import RefreshRuntime from '/@react-refresh';RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;</script>
      <script type="module">
        import React from '${react}';import ReactDOM from '${dom}';import {MemoryRouter,Routes,Route} from '${router}';
        import {AdminOrderDetail} from '/src/admin/orders.tsx';import {AdminDataOperationsPage} from '/src/v1/admin.tsx';import {buildQuote} from '/src/growth/pricing.ts';import '/src/styles.css';
        const quote=buildQuote([{key:'a',relatedId:'a',unit:'SHEET',quantity:10,unitPriceCents:100,amountCents:1000,service:'A4'}],{id:'p',version:1,enabled:false,tiers:[]},[]);
        const legacy=${JSON.stringify(legacyReceipt)};
        const order={id:'order',pickupCode:'P1001',version:1,contentRevision:1,status:'PENDING_CONFIRMATION',currentQuoteId:quote.id,quote,totalCents:1000,files:[],history:[],packingStatus:'WAITING_PRINT',nickname:'测试同学',phone:'13800000000',createdAt:'2026-11-05T01:00:00Z',locationId:'building',slotId:'slot',blockers:legacy?[{code:'PAYMENT_MISMATCH',message:'历史收款待核对'}]:[],unmatchedReceipts:legacy?[{id:'receipt',amountCents:980,status:'UNMATCHED',createdAt:'2026-11-05T02:00:00Z'}]:[]};
        window.fixture={calls:[],data:{orders:[order],campuses:[],buildings:[],slots:[],batches:[],staff:[]}};
        ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(MemoryRouter,{initialEntries:['/manage/orders/order']},React.createElement(Routes,null,React.createElement(Route,{path:'/manage/orders/:id',element:React.createElement(${exports ? "AdminDataOperationsPage" : "AdminOrderDetail"})}))));
      </script>`,
    }),
  );
  await page.goto("/payment-variance-fixture");
}

test("requires acknowledgement of each cash amount and submits actual money", async ({
  page,
}, info) => {
  await fixture(page);
  await page.getByRole("button", { name: "确认线下收款", exact: true }).click();
  const dialog = page.getByRole("dialog");
  const cash = dialog.getByLabel("实际收到金额（元）");
  const accept = dialog.getByRole("checkbox", {
    name: "接受差额并结算",
  });
  const submit = dialog.getByRole("button", {
    name: "已核对，确认实际收款",
    exact: true,
  });
  await cash.fill("9.80");
  await expect(dialog.getByRole("status")).toHaveText("差额：-¥0.20");
  await expect(dialog.locator(".red-text")).toHaveCSS(
    "color",
    "rgb(187, 81, 72)",
  );
  await expect(dialog).not.toContainText("不挂欠款");
  await expect(submit).toBeDisabled();
  await accept.check();
  await expect(submit).toBeEnabled();
  await cash.fill("10.50");
  await expect(dialog.getByRole("status")).toHaveText("差额：+¥0.50");
  await expect(dialog.locator(".green-text")).toHaveCSS(
    "color",
    "rgb(62, 128, 95)",
  );
  await expect(accept).not.toBeChecked();
  await expect(submit).toBeDisabled();
  await accept.check();
  await dialog.getByLabel("收款备注（选填）").fill("顾客自愿多付，已核对");
  await dialog.screenshot({ path: info.outputPath("payment-variance.png") });
  await submit.click();
  await expect(dialog).toHaveCount(0);
  const calls = await page.evaluate(() => (window as any).fixture.calls);
  expect(calls).toHaveLength(1);
  expect(calls[0].body).toMatchObject({
    receivedCents: 1050,
    paymentDifferenceAccepted: true,
    note: "顾客自愿多付，已核对",
  });
  const snapshot = page
    .getByRole("heading", { name: "费用快照" })
    .locator("xpath=ancestor::section[1]");
  await expect(snapshot).toContainText("¥10.50");
  await expect(snapshot).toContainText("+¥0.50");
});

test("resets acknowledgement and optional note on reopening, keeps equal cash simple", async ({
  page,
}) => {
  await fixture(page);
  await page.getByRole("button", { name: "确认线下收款", exact: true }).click();
  let dialog = page.getByRole("dialog");
  await dialog.getByLabel("实际收到金额（元）").fill("9.80");
  await dialog.getByRole("checkbox").check();
  await dialog.getByLabel("收款备注（选填）").fill("上次未提交");
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await page.getByRole("button", { name: "确认线下收款", exact: true }).click();
  dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("实际收到金额（元）")).toHaveValue("");
  await expect(dialog.getByLabel("收款备注（选填）")).toHaveValue("");
  await dialog.getByLabel("实际收到金额（元）").fill("9.80");
  await expect(dialog.getByRole("checkbox")).not.toBeChecked();
  await dialog.getByLabel("实际收到金额（元）").fill("10.00");
  await expect(dialog.getByRole("checkbox")).toHaveCount(0);
  await dialog
    .getByRole("button", { name: "已核对，确认实际收款", exact: true })
    .click();
  expect(
    await page.evaluate(() => (window as any).fixture.calls[0].body),
  ).toMatchObject({ receivedCents: 1000, note: "" });
});

test("reconciles a historical mismatch using its existing cash receipt", async ({
  page,
}) => {
  await fixture(page, true);
  await page.getByRole("button", { name: "确认线下收款", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("combobox").selectOption("receipt");
  await expect(dialog.getByLabel("实际收到金额（元）")).toHaveValue("9.80");
  await dialog.getByRole("checkbox").check();
  await dialog
    .getByRole("button", { name: "已核对，确认实际收款", exact: true })
    .click();
  expect(
    await page.evaluate(() => (window as any).fixture.calls[0].body),
  ).toMatchObject({
    receivedCents: 980,
    receiptId: "receipt",
    paymentDifferenceAccepted: true,
  });
});

test("finance export defaults include real cash and variance without stale order-only fields", async ({
  page,
}) => {
  await fixture(page, false, true);
  await page.getByLabel("导出类型").selectOption("FINANCE");
  for (const field of [
    "payableCents",
    "cashReceivedCents",
    "paymentDifferenceCents",
  ]) {
    await expect(
      page.getByRole("checkbox", { name: field, exact: true }),
    ).toBeChecked();
  }
  await page.getByLabel("导出原因").fill("核对差额收款");
  await page.getByRole("button", { name: "申请导出", exact: true }).click();
  await expect(page.getByText("状态：")).toBeVisible();
  const body = await page.evaluate(() => (window as any).fixture.calls[0].body);
  expect(body.kind).toBe("FINANCE");
  expect(body.fields).toEqual(
    expect.arrayContaining([
      "payableCents",
      "cashReceivedCents",
      "paymentDifferenceCents",
    ]),
  );
  expect(body.fields).not.toContain("pickupCode");
  expect(body.fields).not.toContain("status");
});
