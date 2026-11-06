import { expect, test } from "@playwright/test";

test.use({ channel: "chrome" });

async function fixture(
  page: import("@playwright/test").Page,
  view: "slots" | "batches" | "proposals" | "detail",
) {
  if (view === "slots")
    await page.clock.install({ time: new Date("2030-11-01T04:00:00Z") });
  page.on("pageerror", (error) => console.log("FIXTURE ERROR", error.message));
  const main = await (await page.request.get("/src/main.tsx")).text();
  const reactUrl = main.match(/from "([^\"]*\/react\.js\?[^\"]*)"/)![1];
  const logistics = await (
    await page.request.get("/src/admin/logistics.tsx")
  ).text();
  const routerUrl = logistics.match(
    /from "([^"]*\/react-router-dom\.js\?[^"]*)"/,
  )![1];
  const domUrl = main.match(
    /from "([^\"]*\/react-dom_client\.js\?[^\"]*)"/,
  )![1];
  await page.route("**/src/lib/store.tsx*", (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: `
    import React from '${reactUrl}';
    export const ADMIN='/admin', DEMO=false;
    export function useStore(){const [,setTick]=React.useState(0);React.useEffect(()=>{window.fixture.refresh=()=>setTick(v=>v+1)},[]);return {data:window.fixture.data, pendingActions:{}, toast:(message)=>window.fixture.toast=message, act:async(path,body)=>{window.fixture.calls.push({path,body});return true}}}
  `,
    }),
  );
  await page.route("**/src/growth/client.ts*", (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: `
    export const getIdentityGeneration=()=>0,advanceIdentity=()=>0;
    export const useGrowthResource=()=>({data:null,loading:false,hasData:false,reload:async()=>{}});
    export async function growthRequest(path,options={}) {
      if(path==='/admin/campuses') return {items:window.fixture.campuses};
      if(path==='/admin/slots') return {items:window.fixture.slots};
      if(path.includes('/seal/preview')) return {slot:{version:1},impact:{pendingApplications:0,batchCount:0},blockers:[]};
      if(path.includes('/admin/orders')) return {items:window.fixture.orders || []};
      if(path==='/admin/couriers') return {items:[]};
      return {};
    }
  `,
    }),
  );
  await page.route("**/api/v1/admin/batch-operations/preview", (route) =>
    route.fulfill({ json: { data: { planId: "plan", blockers: [] } } }),
  );
  await page.route("**/campus-admin-fixture", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: `
    <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><div id="root"></div><script type="module">import RefreshRuntime from '/@react-refresh';RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;</script>
    <script type="module">
      import React from '${reactUrl}'; import ReactDOM from '${domUrl}'; import {BrowserRouter,MemoryRouter,Routes,Route} from '${routerUrl}';
      import {Slots,Batches,SlotProposals,BatchDetail} from '/src/admin/logistics.tsx'; import '/src/styles.css';
      const campuses=[{id:'east',name:'东校区',selectionEnabled:true},{id:'west',name:'西校区',selectionEnabled:true}];
      const slots=[{id:'e1',campusId:'east',campusName:'东校区',name:'东时段',date:'2030-11-01',start:'12:00',end:'13:00',enabled:true,capacity:30,version:1},{id:'w1',campusId:'west',campusName:'西校区',name:'西时段',date:'2030-11-01',start:'12:00',end:'13:00',enabled:true,capacity:30,version:1}];
      window.fixture={calls:[],campuses,slots,orders:[{id:'order',pickupCode:'P1',locationId:'building',status:'PENDING_CONFIRMATION',version:1}],data:{campuses:[],slots,buildings:[],orders:[],staff:[],batches:[{id:'legacy',name:'旧空批次',campusId:'old',campusName:'停用校区',status:'PLANNED',date:'2030-11-01',time:'12:00',orderIds:[],version:1}]}};
      if ('${view}' === 'detail') {window.fixture.data.batches=[{id:'source',name:'测试批次',campusId:'east',campusName:'东校区',status:'PLANNED',date:'2030-11-01',time:'12:00',orderIds:['o1'],version:1}];window.fixture.data.orders=[{id:'o1',pickupCode:'P1',locationId:'building',status:'PAID',packingStatus:'PACKED'}];window.fixture.data.buildings=[{id:'building',name:'楼栋',campusId:'east'}];}
      const component=${view === "slots" ? "React.createElement(Slots)" : view === "batches" ? "React.createElement(Batches)" : view === "detail" ? "React.createElement(MemoryRouter,{initialEntries:['/admin/batches/source']},React.createElement(Routes,null,React.createElement(Route,{path:'/admin/batches/:id',element:React.createElement(BatchDetail)})))" : "React.createElement(SlotProposals,{slots:[{...slots[0],cutoffAt:new Date(Date.now()+60000).toISOString()}],buildings:[{id:'building',campusId:'east'}]})"};
      ReactDOM.createRoot(document.getElementById('root')).render('${view}'==='detail' ? component : React.createElement(BrowserRouter,null,component));
    </script>`,
    }),
  );
  await page.goto("/campus-admin-fixture");
}

test("campus filter has a usable control and clear spacing above compact slot rows", async ({
  page,
}, testInfo) => {
  await fixture(page, "slots");
  await page
    .locator("#root")
    .evaluate((element) => element.classList.add("admin-content"));
  const filter = page.getByRole("combobox", { name: "校区筛选" });
  await expect(filter).toBeVisible();
  const panel = filter.locator("xpath=ancestor::section[1]");
  const geometry = await filter.evaluate((element) => {
    const bounds = element.getBoundingClientRect();
    const panel = element.closest("section")!;
    const table = panel.querySelector("table")!.getBoundingClientRect();
    const buttons = [
      ...panel.querySelector("tbody tr")!.querySelectorAll("button"),
    ];
    return {
      height: bounds.height,
      gap: table.top - bounds.bottom,
      borderRadius: parseFloat(getComputedStyle(element).borderRadius),
      actionTops: buttons.map((button) => button.getBoundingClientRect().top),
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
    };
  });
  await panel.screenshot({
    path: testInfo.outputPath("campus-filter-layout.png"),
  });
  expect(geometry.height).toBeGreaterThanOrEqual(44);
  expect(geometry.gap).toBeGreaterThanOrEqual(16);
  expect(geometry.borderRadius).toBeGreaterThanOrEqual(8);
  expect(
    Math.max(...geometry.actionTops) - Math.min(...geometry.actionTops),
  ).toBeLessThan(2);
  expect(geometry.documentWidth).toBeLessThanOrEqual(geometry.viewportWidth);
  await filter.selectOption("east");
  await expect(
    panel.getByRole("row").filter({ hasText: "东时段" }),
  ).toHaveCount(1);
  await expect(
    panel.getByRole("row").filter({ hasText: "西时段" }),
  ).toHaveCount(0);
  await filter.selectOption("");
  await expect(
    panel.getByRole("row").filter({ hasText: "西时段" }),
  ).toHaveCount(1);
});

test("creates a campus slot and seal identifies its locked campus", async ({
  page,
}) => {
  await fixture(page, "slots");
  await page.getByRole("button", { name: "新建时段" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("配送校区").selectOption("east");
  await dialog.getByLabel("配送日期").fill("2030-11-02");
  await dialog.getByLabel("时段名称").fill("新东时段");
  await dialog.getByLabel("预计配送开始").fill("12:00");
  await dialog.getByLabel("预计配送结束").fill("13:00");
  await dialog.getByLabel("开放下单时间").fill("2030-11-01T08:00");
  await dialog.getByLabel("截单时间").fill("2030-11-02T10:00");
  await dialog.getByRole("button", { name: "保存时段" }).click();
  await expect
    .poll(() =>
      page.evaluate(() => (window as any).fixture.calls[0]?.body.campusId),
    )
    .toBe("east");
  await page
    .getByRole("row")
    .filter({ hasText: "东时段" })
    .getByRole("button", { name: "提前封批" })
    .click();
  await expect(page.getByRole("dialog")).toContainText("东校区");
  await expect(page.getByRole("dialog")).not.toContainText("所有校区");
});

test("batch creation filters slots and disabled empty batches keep campus identity", async ({
  page,
}) => {
  await fixture(page, "batches");
  await expect(page.getByText("停用校区", { exact: true })).toBeVisible();
  await page.getByLabel("校区筛选").selectOption("old");
  await expect(page.getByText("旧空批次")).toBeVisible();
  await page.getByRole("button", { name: "新建批次" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("配送校区").selectOption("east");
  await expect(dialog.getByLabel("配送时段").locator("option")).toContainText([
    "请选择配送时段",
    "东时段",
  ]);
  await dialog.getByLabel("配送时段").selectOption("e1");
  await dialog.getByLabel("配送校区").selectOption("west");
  await expect(dialog.getByLabel("配送时段")).toHaveValue("");
  await expect(dialog.getByLabel("配送时段").locator("option")).toContainText([
    "请选择配送时段",
    "西时段",
  ]);
});

test("proposal submission rechecks an elapsed cutoff", async ({ page }) => {
  await page.clock.install();
  await fixture(page, "proposals");
  await page.getByRole("button", { name: "建议变更时段" }).click();
  await page.getByLabel("建议时段", { exact: true }).selectOption("e1");
  await page.getByLabel("变更原因").fill("调整时间");
  await page.clock.fastForward(61000);
  await page.getByRole("button", { name: "提交建议" }).click();
  await expect(page.getByText("请选择该订单校区可预约的时段")).toBeVisible();
});

test("batch membership refresh invalidates an open operation preview", async ({
  page,
}) => {
  await fixture(page, "detail");
  await page.getByRole("button", { name: "移出订单" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("checkbox").check();
  await dialog.getByRole("button", { name: "预览操作影响" }).click();
  await expect(
    page.getByRole("button", { name: "确认执行移出订单" }),
  ).toBeVisible();
  await page.evaluate(() => {
    (window as any).fixture.data.batches[0] = {
      ...(window as any).fixture.data.batches[0],
      orderIds: [],
      version: 2,
    };
    (window as any).fixture.refresh();
  });
  await expect(
    page.getByRole("button", { name: "确认执行移出订单" }),
  ).toHaveCount(0);
  await expect(dialog.getByText("没有可选择的订单")).toBeVisible();
});
