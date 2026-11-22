import { expect, test } from "@playwright/test";

async function fixture(
  page: import("@playwright/test").Page,
  view:
    | "slots"
    | "batches"
    | "proposals"
    | "paid"
    | "paid-late"
    | "detail"
    | "packing"
    | "binding",
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
    export function useStore(){const [,setTick]=React.useState(0);React.useEffect(()=>{window.fixture.refresh=()=>setTick(v=>v+1)},[]);return {data:window.fixture.data, pendingActions:{}, toast:(message)=>window.fixture.toast=message, act:async(path,body,_update,_method,_success,onError)=>{window.fixture.calls.push({path,body});if(window.fixture.saveError){onError(new Error(window.fixture.saveError),window.fixture.saveError);return false}return true}}}
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
      if(path.endsWith('/slot-change')) {
        window.fixture.calls.push({path,body:options.body});
        if (window.fixture.changeError) throw new Error(window.fixture.changeError);
        const order = window.fixture.orders[0];
        return {...order, version: order.version + 1, requestedSlotId: options.body.slotId};
      }
      if(path.includes('/admin/orders')) return {items:window.fixture.orders || []};
      if(path==='/admin/couriers') return {items:[{id:'c1',name:'配送员甲',role:'COURIER',enabled:true,status:'ACTIVE'}]};
      if(path==='/admin/binding-tasks') return {tasks:[
        {id:'internal-binding-id',orderId:'o1',pickupCode:'HV7KR',fileId:'file-1',fileName:'实验报告（终稿）.docx',bindingType:'CORNER',status:'READY',printStatus:'COMPLETED'},
        {id:'internal-binding-long',orderId:'o1',pickupCode:'HV7KR',fileId:'file-2',fileName:'verylong'.repeat(25)+'.pdf',bindingType:'SIDE',status:'WAITING_PRINT',printStatus:'ATTEMPTED'},
        {id:'internal-binding-unnamed',orderId:'o1',pickupCode:'HV7KR',fileId:'file-3',fileName:'',bindingType:'CORNER',status:'WAITING_PRINT',printStatus:'PENDING'}
      ]};
      if(path==='/admin/packing-tasks') return {tasks:[{id:'internal-packing-id',orderId:'o1',pickupCode:'T8DUW',status:'PACKED',checklistVersion:2,checklist:{blockers:[],items:[{fileId:'internal-file-id',name:'实验报告（终稿）.docx',printStatus:'COMPLETED',bindingStatus:'NOT_REQUIRED'},{fileId:'long-file',name:'verylong'.repeat(23)+'.pdf',printStatus:'COMPLETED',bindingStatus:'NOT_REQUIRED'},{fileId:'missing-file-name',name:'',printStatus:'COMPLETED',bindingStatus:'NOT_REQUIRED'}]}}]};
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
      import {Slots,Batches,SlotProposals,PaidArrangementProposals,BatchDetail} from '/src/admin/logistics.tsx'; import {BindingCenter,PackingCenter} from '/src/admin/production.tsx'; import '/src/styles.css';
      const campuses=[{id:'east',name:'东校区',selectionEnabled:true},{id:'west',name:'西校区',selectionEnabled:true}];
      const slots=[{id:'e1',campusId:'east',campusName:'东校区',name:'东时段',date:'2030-11-01',start:'12:00',end:'13:00',enabled:true,capacity:30,version:1},{id:'w1',campusId:'west',campusName:'西校区',name:'西时段',date:'2030-11-01',start:'12:00',end:'13:00',enabled:true,capacity:30,version:1}];
      window.fixture={calls:[],campuses,slots,orders:[{id:'order',pickupCode:'P1',locationId:'building',status:'PENDING_CONFIRMATION',version:1}],data:{campuses:[],slots,buildings:[],orders:[],staff:[],batches:[{id:'legacy',name:'旧空批次',campusId:'old',campusName:'停用校区',status:'PLANNED',date:'2030-11-01',time:'12:00',orderIds:[],version:1}]}};
      if ('${view}'.startsWith('paid')) window.fixture.orders=[{id:'paid-order',pickupCode:'PAID1',locationId:'building',status:'PENDING_PRINT',version:1,confirmedSlotId:'e1',arrangement:{effective:{id:'e1',name:'东时段',startsAt:'2030-11-01T05:30:00Z',endsAt:'2030-11-01T06:30:00Z'}}}];
      if ('${view}' === 'paid-late') slots.push(
        {...slots[0],id:'earlier',name:'午间配送',start:'11:15',end:'11:35',selectable:false,lateRescheduleSelectable:true},
        {...slots[0],id:'started',name:'已经出发',selectable:false,lateRescheduleSelectable:false},
        {...slots[1],id:'other-campus',selectable:false,lateRescheduleSelectable:true},
      );
      if ('${view}' === 'detail') {window.fixture.data.batches=[{id:'source',name:'测试批次',campusId:'east',campusName:'东校区',status:'PLANNED',date:'2030-11-01',time:'12:00 – 13:00',orderIds:['o1'],version:1,allowedActions:['ASSIGN_COURIER']}];window.fixture.data.orders=[{id:'o1',pickupCode:'P1',locationId:'building',status:'PAID',packingStatus:'PACKED'}];window.fixture.data.buildings=[{id:'building',name:'楼栋',campusId:'east'}];}
      const component=${view === "slots" ? "React.createElement(Slots)" : view === "batches" ? "React.createElement(Batches)" : view.startsWith("paid") ? "React.createElement(PaidArrangementProposals,{slots,buildings:[{id:'building',campusId:'east'}]})" : view === "packing" ? "React.createElement(PackingCenter)" : view === "binding" ? "React.createElement(BindingCenter)" : view === "detail" ? "React.createElement(MemoryRouter,{initialEntries:['/admin/batches/source']},React.createElement(Routes,null,React.createElement(Route,{path:'/admin/batches/:id',element:React.createElement(BatchDetail)})))" : "React.createElement(SlotProposals,{slots:[{...slots[0],cutoffAt:new Date(Date.now()+60000).toISOString()}],buildings:[{id:'building',campusId:'east'}]})"};
      ReactDOM.createRoot(document.getElementById('root')).render('${view}'==='detail' ? component : React.createElement(BrowserRouter,null,component));
    </script>`,
    }),
  );
  await page.goto("/campus-admin-fixture");
  if (view === "detail") {
    await expect
      .poll(() => page.evaluate(() => typeof (window as any).fixture?.refresh))
      .toBe("function");
  }
}

test("current delivery time uses the effective batch window", async ({
  page,
}) => {
  await fixture(page, "paid");
  const row = page.getByRole("row").filter({ hasText: "PAID1" });
  await expect(row).toContainText("13:30–14:30");
  await expect(row).not.toContainText("12:00–13:00");
});

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
}, info) => {
  await fixture(page, "slots");
  await page.getByRole("button", { name: "新建时段" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("配送校区").selectOption("east");
  const autoPublish = dialog.getByRole("checkbox", { name: "截单后自动发布" });
  await expect(autoPublish).not.toBeChecked();
  await dialog.getByLabel("配送日期").fill("2030-11-02");
  await dialog.getByLabel("时段名称").fill("新东时段");
  await dialog.getByLabel("预计配送开始").fill("12:00");
  await dialog.getByLabel("预计配送结束").fill("13:00");
  await dialog.getByLabel("开放下单时间").fill("2030-11-01T08:00");
  await dialog.getByLabel("截单时间").fill("2030-11-02T10:00");
  await autoPublish.check();
  await dialog.screenshot({
    path: info.outputPath("slot-auto-publication.png"),
  });
  await dialog.getByRole("button", { name: "保存时段" }).click();
  await expect
    .poll(() =>
      page.evaluate(() => (window as any).fixture.calls[0]?.body.campusId),
    )
    .toBe("east");
  expect(
    await page.evaluate(
      () => (window as any).fixture.calls[0]?.body.autoPublish,
    ),
  ).toBe(true);
  await page
    .getByRole("row")
    .filter({ hasText: "东时段" })
    .getByRole("button", { name: "提前封批" })
    .click();
  await expect(page.getByRole("dialog")).toContainText("东校区");
  await expect(page.getByRole("dialog")).not.toContainText("所有校区");
});

test("blocked slot save keeps the input and shows the error next to save", async ({
  page,
}, info) => {
  await fixture(page, "slots");
  await page.evaluate(() => {
    (window as any).fixture.saveError = "该时段仍有有效预约，无法修改时间";
  });
  await page
    .getByRole("row")
    .filter({ hasText: "东时段" })
    .getByRole("button", { name: "编辑" })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("时段名称").fill("编辑内容保留");
  await dialog.getByLabel("开放下单时间").fill("2030-11-01T08:00");
  await dialog.getByLabel("截单时间").fill("2030-11-01T10:00");
  const save = dialog.getByRole("button", { name: "保存时段" });
  await save.click();
  const error = dialog.getByRole("alert");
  await expect(error).toHaveText("该时段仍有有效预约，无法修改时间");
  await expect(error).toBeInViewport();
  await expect(save).toBeInViewport();
  await expect(dialog.getByLabel("时段名称")).toHaveValue("编辑内容保留");
  await dialog.screenshot({ path: info.outputPath("slot-save-error.png") });
  await page.evaluate(() => {
    (window as any).fixture.saveError = "";
  });
  await save.click();
  await expect(dialog).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).fixture.toast)).toBe(
    "配送时段已保存",
  );
});

test("slot editor locks batched fields and saves only editable changes", async ({
  page,
}, info) => {
  await fixture(page, "slots");
  await expect.poll(() =>
    page.evaluate(() => typeof (window as any).fixture?.refresh),
  ).toBe("function");
  await page.evaluate(() => {
    const fixture = (window as any).fixture;
    Object.assign(fixture.slots[0], {
      editConstraints: { campusLocked: true, timeLock: "BATCH" },
      batchCount: 1,
      batchWindow: {
        batchId: "source", date: "2030-11-01", weekday: "星期五",
        start: "09:30", end: "09:45",
      },
      openAt: "2030-11-01T03:00:00Z",
      cutoffAt: "2030-11-01T05:00:00Z",
      occupied: 3,
    });
    fixture.data.slots = [...fixture.slots];
    fixture.refresh();
  });
  const row = page.getByRole("row").filter({ hasText: "东时段" });
  await expect(row).toContainText("09:30 – 09:45");
  await row.getByRole("button", { name: "编辑" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("预计配送开始")).toHaveValue("09:30");
  await expect(dialog.getByLabel("预计配送结束")).toHaveValue("09:45");
  await expect(dialog.getByLabel("截单时间")).toHaveValue("2030-11-01T13:00");
  for (const label of [
    "配送校区", "配送日期", "预计配送开始", "预计配送结束", "开放下单时间", "截单时间",
  ]) await expect(dialog.getByLabel(label, { exact: true })).toBeDisabled();
  await expect(dialog.getByRole("link", { name: "前往配送调度" }))
    .toHaveAttribute("href", "/admin/batches/source");
  const autoPublish = dialog.getByRole("checkbox", { name: "截单后自动发布" });
  await expect(autoPublish).toBeEnabled();
  await expect(dialog.getByRole("checkbox", { name: /人工开启接单/ })).toBeEnabled();
  const save = dialog.getByRole("button", { name: "保存时段" });
  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const metrics = await dialog.evaluate((panel) => {
      const bounds = panel.getBoundingClientRect();
      const style = getComputedStyle(panel);
      return {
        overflow: panel.scrollWidth - panel.clientWidth,
        documentOverflow: document.documentElement.scrollWidth - innerWidth,
        controls: [...panel.querySelectorAll(".field input,.field select")].map((control) => {
          const box = control.getBoundingClientRect();
          return {
            fits: box.left >= bounds.left + parseFloat(style.paddingLeft) - 1 &&
              box.right <= bounds.right - parseFloat(style.paddingRight) + 1,
            height: box.height,
            font: parseFloat(getComputedStyle(control).fontSize),
          };
        }),
      };
    });
    expect(metrics.overflow).toBeLessThanOrEqual(1);
    expect(metrics.documentOverflow).toBeLessThanOrEqual(1);
    for (const control of metrics.controls) {
      expect(control.fits).toBe(true);
      expect(control.height).toBeGreaterThanOrEqual(44);
      if (width <= 430) expect(control.font).toBeGreaterThanOrEqual(16);
    }
    if (width === 390 || width === 1440) {
      await dialog.evaluate((panel) => { panel.scrollTop = 0; });
      await dialog.screenshot({ path: info.outputPath(`slot-lock-${width}.png`) });
      await save.scrollIntoViewIfNeeded();
      await dialog.screenshot({ path: info.outputPath(`slot-actions-${width}.png`) });
    }
  }
  await page.clock.fastForward(3_600_001);
  await expect(autoPublish).toBeDisabled();
  await dialog.getByLabel("时段名称").fill("更新显示名称");
  await page.evaluate(() => {
    const fixture = (window as any).fixture;
    fixture.data.slots = fixture.slots.map((slot: any) =>
      slot.id === "e1"
        ? { ...slot, batchWindow: { ...slot.batchWindow, start: "10:00", end: "10:30" } }
        : slot,
    );
    fixture.refresh();
  });
  await expect(dialog.getByLabel("预计配送开始")).toHaveValue("10:00");
  await expect(dialog.getByLabel("预计配送结束")).toHaveValue("10:30");
  await expect(dialog.getByLabel("时段名称")).toHaveValue("更新显示名称");
  const capacity = dialog.getByLabel("预约容量（单）");
  await capacity.fill("2");
  await save.click();
  await expect(dialog.getByRole("alert")).toHaveText("容量不能小于已占用数量");
  await capacity.fill("4");
  await save.click();
  await expect(dialog).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).fixture.calls[0]?.body)).toEqual({
    expectedVersion: 1, name: "更新显示名称", capacity: 4, reason: "",
  });
});

test("slot editor allows only missing booking rules on legacy batched slots", async ({ page }) => {
  await fixture(page, "slots");
  await page.evaluate(() => {
    Object.assign((window as any).fixture.slots[0], {
      editConstraints: { campusLocked: true, timeLock: "BATCH" },
      batchCount: 1,
      cutoffAt: "2030-11-01T03:00:21Z",
      autoPublish: true,
    });
  });
  await page.getByRole("row").filter({ hasText: "东时段" })
    .getByRole("button", { name: "编辑" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("预计配送开始")).toBeDisabled();
  await expect(dialog.getByLabel("截单时间", { exact: true })).toBeDisabled();
  await dialog.getByLabel("开放下单时间").fill("2030-11-01T10:00");
  const autoPublish = dialog.getByRole("checkbox", { name: "截单后自动发布" });
  await autoPublish.uncheck();
  await expect(autoPublish).toBeDisabled();
  await dialog.getByRole("button", { name: "保存时段" }).click();
  await expect(dialog).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).fixture.calls[0]?.body)).toEqual({
    expectedVersion: 1, openAt: "2030-11-01T10:00", cutoffAt: "2030-11-01T03:00:21Z",
    autoPublish: false, reason: "",
  });
});

test("slot editor updates locks when a batch is generated while open", async ({ page }) => {
  await fixture(page, "slots");
  await page.getByRole("row").filter({ hasText: "东时段" })
    .getByRole("button", { name: "编辑" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("预计配送开始").fill("12:15");
  await dialog.getByLabel("时段名称").fill("保留名称");
  await page.evaluate(() => {
    const fixture = (window as any).fixture;
    fixture.slots = fixture.slots.map((slot: any) => slot.id === "e1" ? {
      ...slot, version: 2, occupied: 1, batchCount: 1,
      openAt: "2030-11-01T03:00:00Z", cutoffAt: "2030-11-01T05:00:00Z",
      editConstraints: { campusLocked: true, timeLock: "BATCH" },
    } : slot);
    fixture.data.slots = fixture.slots;
    fixture.refresh();
  });
  const start = dialog.getByLabel("预计配送开始");
  await expect(start).toBeDisabled();
  await expect(start).toHaveValue("12:00");
  await expect(dialog.getByLabel("时段名称")).toHaveValue("保留名称");
  await dialog.getByRole("button", { name: "保存时段" }).click();
  await expect(dialog).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).fixture.calls[0]?.body)).toEqual({
    expectedVersion: 2, name: "保留名称", reason: "",
  });
});

test("dispatch hides emptied automatic batches and shows them again after new orders", async ({
  page,
}) => {
  await fixture(page, "batches");
  await expect(page.getByRole("link", { name: /旧空批次/ })).toBeVisible();
  await page.evaluate(() => {
    const fixture = (window as any).fixture;
    const automatic = {
      ...fixture.data.batches[0],
      source: "AUTO",
      isRegular: true,
    };
    fixture.data.batches.push(
      { ...automatic, id: "source", name: "原配送批次", orderIds: ["o1"] },
      { ...automatic, id: "target", name: "目标配送批次", orderIds: ["o2"] },
    );
    fixture.refresh();
  });
  const source = page.getByRole("link", { name: /原配送批次/ });
  const target = page.getByRole("link", { name: /目标配送批次/ });
  const plannedCount = page
    .locator(".production-stats > div")
    .filter({ hasText: "规划中" });
  await page.getByRole("button", { name: "规划中", exact: true }).click();
  await expect(source).toBeVisible();
  await expect(plannedCount.locator("strong")).toHaveText("3");

  await page.evaluate(() => {
    const fixture = (window as any).fixture;
    fixture.data.batches[1].orderIds = [];
    fixture.data.batches[2].orderIds = ["o1", "o2"];
    fixture.refresh();
  });
  await expect(source).toHaveCount(0);
  await expect(target.locator(".batch-count strong").first()).toHaveText("2");
  await expect(page.getByRole("link", { name: /旧空批次/ })).toBeVisible();
  await expect(plannedCount.locator("strong")).toHaveText("2");
  await page.getByRole("button", { name: "全部批次", exact: true }).click();
  await expect(source).toHaveCount(0);

  await page.evaluate(() => {
    const fixture = (window as any).fixture;
    fixture.data.batches = [fixture.data.batches[1]];
    fixture.refresh();
  });
  await expect(page.getByText("还没有配送批次", { exact: true })).toBeVisible();

  await page.evaluate(() => {
    const fixture = (window as any).fixture;
    fixture.data.batches[0].orderIds = ["o3"];
    fixture.refresh();
  });
  await expect(source.locator(".batch-count strong").first()).toHaveText("1");
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

test("slot change submission rechecks an elapsed cutoff", async ({ page }) => {
  await page.clock.install();
  await fixture(page, "proposals");
  await page.getByRole("button", { name: "调整配送时段" }).click();
  await page.getByLabel("配送时段", { exact: true }).selectOption("e1");
  await page.getByLabel("调整原因").fill("调整时间");
  await page.clock.fastForward(61000);
  await page.getByRole("button", { name: "保存配送时段" }).click();
  await expect(page.getByText("请选择该订单校区可预约的时段")).toBeVisible();
});

test("已结算订单可调到同校区已截单但未出发的批次", async ({ page }, info) => {
  await fixture(page, "paid-late");
  await page.getByRole("button", { name: "调整配送时段" }).click();
  const dialog = page.getByRole("dialog");
  const slot = dialog.getByLabel("配送时段", { exact: true });
  await expect(slot.locator("option")).toHaveCount(2);
  await slot.selectOption("earlier");
  await expect(dialog.getByText("已发布的批次会邮件通知配送员。", { exact: false })).toBeVisible();
  const save = dialog.getByRole("button", { name: "保存配送时段" });
  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const field = await slot.evaluate((select) => {
      const bounds = select.getBoundingClientRect();
      const parent = select.parentElement!.getBoundingClientRect();
      return {
        fits: bounds.left >= parent.left && bounds.right <= parent.right + 1,
        height: bounds.height,
        fontSize: parseFloat(getComputedStyle(select).fontSize),
      };
    });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1);
    expect(field.fits).toBe(true);
    expect(field.height).toBeGreaterThanOrEqual(44);
    if (width < 768) expect(field.fontSize).toBeGreaterThanOrEqual(16);
    if (width === 390 || width === 1440) {
      await dialog.screenshot({ path: info.outputPath(`late-slot-change-${width}.png`) });
    }
  }
  await dialog.getByLabel("调整原因").fill("微信确认提前配送");
  await save.click();
  await expect(dialog).toBeHidden();
  expect(await page.evaluate(() => (window as any).fixture.calls)).toContainEqual({
    path: "/admin/orders/paid-order/slot-change",
    body: { expectedVersion: 1, slotId: "earlier", reason: "微信确认提前配送" },
  });
});

test("a rejected slot change preserves the form and a successful retry closes it", async ({
  page,
}, info) => {
  await page.clock.install();
  await fixture(page, "proposals");
  await page.evaluate(() => {
    (window as any).fixture.changeError = "目标批次已发布，不能加入";
  });
  await page.getByRole("button", { name: "调整配送时段" }).click();
  const dialog = page.getByRole("dialog");
  const slot = dialog.getByLabel("配送时段", { exact: true });
  const reason = dialog.getByLabel("调整原因");
  const save = dialog.getByRole("button", { name: "保存配送时段" });
  await slot.selectOption("e1");
  await reason.fill("已在微信协调");
  await save.click();
  await expect(dialog.getByRole("alert")).toHaveText(
    "目标批次已发布，不能加入",
  );
  await expect(dialog.getByRole("alert")).toBeInViewport();
  await expect(save).toBeInViewport();
  await expect(slot).toHaveValue("e1");
  await expect(reason).toHaveValue("已在微信协调");
  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const geometry = await dialog.evaluate((panel) => {
      const bounds = panel.getBoundingClientRect();
      const buttons = [
        ...panel.querySelectorAll<HTMLButtonElement>(
          ".order-slot-change-actions button",
        ),
      ];
      const controls = [
        ...panel.querySelectorAll<HTMLElement>("select, textarea"),
      ];
      return {
        dialogWidth: bounds.width,
        documentWidth: document.documentElement.scrollWidth,
        actionHeights: buttons.map(
          (button) => button.getBoundingClientRect().height,
        ),
        controlsFit: controls.every((control) => {
          const rect = control.getBoundingClientRect();
          return rect.left >= bounds.left && rect.right <= bounds.right;
        }),
      };
    });
    expect(geometry.documentWidth).toBeLessThanOrEqual(width);
    expect(geometry.dialogWidth).toBeLessThanOrEqual(width);
    expect(geometry.controlsFit).toBe(true);
    expect(Math.min(...geometry.actionHeights)).toBeGreaterThanOrEqual(44);
    if ([390, 1440].includes(width)) {
      await dialog.screenshot({
        path: info.outputPath(`order-slot-change-${width}.png`),
      });
    }
  }
  await dialog.screenshot({
    path: info.outputPath("order-slot-change-error.png"),
  });
  await page.evaluate(() => {
    (window as any).fixture.changeError = "";
  });
  await save.click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("row").filter({ hasText: "P1" })).toContainText(
    "东时段",
  );
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

test("batch order codes leave space for contact and destination fields", async ({
  page,
}, info) => {
  await fixture(page, "detail");
  await page.evaluate(() => {
    const fixture = (window as any).fixture;
    const [original] = fixture.data.orders;
    fixture.data.orders = [
      {
        ...original,
        pickupCode: "W8WMW",
        status: "READY_FOR_DELIVERY",
        nickname: "同学甲",
        phone: "13800000001",
        slotId: "e1",
      },
      {
        ...original,
        id: "o2",
        pickupCode: "7ZXVT",
        status: "READY_FOR_DELIVERY",
        nickname: "用于检查换行的较长客户昵称",
        phone: "13800000002",
        slotId: "e1",
        adminPickup: {
          pickupCode: "7ZXVT",
          placedWithAccount: true,
          customerDetailId: "customer-1",
        },
      },
    ];
    fixture.data.campuses = fixture.campuses;
    fixture.data.batches[0].orderIds = ["o1", "o2"];
    fixture.refresh();
  });
  const panel = page
    .locator(".panel")
    .filter({
      has: page.getByRole("heading", { name: "批次订单", exact: true }),
    });
  await expect(
    panel.getByRole("link", { name: "W8WMW", exact: true }),
  ).toBeVisible();
  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const layout = await panel.evaluate((element) => {
      const table = element.querySelector("table")!;
      const wrapper = table.parentElement!.getBoundingClientRect();
      const card = element.getBoundingClientRect();
      const padding = getComputedStyle(element);
      const heading = table.querySelector("th")!;
      const range = document.createRange();
      range.selectNodeContents(heading);
      const title = range.getBoundingClientRect();
      return {
        pageWidth: document.documentElement.scrollWidth,
        wrapperFits:
          wrapper.left >= card.left + parseFloat(padding.paddingLeft) - 2 &&
          wrapper.right <= card.right - parseFloat(padding.paddingRight) + 2,
        rows: Array.from(table.querySelectorAll("tbody tr"), (row) => {
          const [pickup, contact, destination] = row.querySelectorAll("td");
          const code = pickup.querySelector("strong")!.getBoundingClientRect();
          const link = pickup
            .querySelector(".order-pickup-value")!
            .getBoundingClientRect();
          const contentLeft = (cell: HTMLElement) =>
            cell.getBoundingClientRect().left +
            parseFloat(getComputedStyle(cell).paddingLeft);
          const phone = contact.querySelector("small")!.getBoundingClientRect();
          return {
            codeToContact: contentLeft(contact) - code.right,
            contactToDestination: contentLeft(destination) - phone.right,
            headingOffset: Math.abs(code.left - title.left),
            targetHeight: link.height,
          };
        }),
      };
    });
    expect(layout.pageWidth).toBeLessThanOrEqual(width + 1);
    expect(layout.rows).toHaveLength(2);
    for (const row of layout.rows) {
      expect(row.codeToContact).toBeGreaterThanOrEqual(24);
      expect(row.headingOffset).toBeLessThanOrEqual(1);
      expect(row.contactToDestination).toBeGreaterThanOrEqual(24);
      expect(row.targetHeight).toBeGreaterThanOrEqual(44);
    }
    expect(layout.wrapperFits).toBe(true);
    if (width === 390 || width === 1440)
      await panel.screenshot({
        path: info.outputPath(`batch-order-spacing-${width}.png`),
        scale: "css",
      });
  }
});

test("first courier assignment omits the reason unless other fields change", async ({
  page,
}, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page, "detail");
  await page.getByRole("button", { name: "编辑批次 / 指派配送员" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("配送员", { exact: true }).selectOption("c1");
  await expect(dialog.getByLabel("修改原因")).toHaveCount(0);
  await dialog.getByLabel("批次名称").fill("同时修改名称");
  await expect(dialog.getByLabel("修改原因")).toBeVisible();
  await dialog.getByRole("button", { name: "保存批次信息" }).click();
  await expect(dialog.getByText("请填写修改原因")).toBeVisible();
  await dialog.getByLabel("批次名称").fill("测试批次");
  await expect(dialog.getByLabel("修改原因")).toHaveCount(0);
  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const layout = await dialog.evaluate((element) => {
      const box = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return {
        pageWidth: document.documentElement.scrollWidth,
        offset: scrollX,
        left: box.left + parseFloat(style.paddingLeft),
        right: box.right - parseFloat(style.paddingRight),
        controls: Array.from(
          element.querySelectorAll(".field input, .field select"),
          (control) => {
            const rect = control.getBoundingClientRect();
            const field = control.closest(".field")!.getBoundingClientRect();
            return {
              left: rect.left,
              right: rect.right,
              height: rect.height,
              fieldLeft: field.left,
              fieldRight: field.right,
              fontSize: parseFloat(getComputedStyle(control).fontSize),
            };
          },
        ),
      };
    });
    expect(layout.pageWidth).toBeLessThanOrEqual(width + 1);
    expect(layout.offset).toBe(0);
    for (const control of layout.controls) {
      expect(control.left).toBeGreaterThanOrEqual(
        Math.max(layout.left, control.fieldLeft) - 1,
      );
      expect(control.right).toBeLessThanOrEqual(
        Math.min(layout.right, control.fieldRight) + 1,
      );
      expect(control.height).toBeGreaterThanOrEqual(44);
      if (width < 768) expect(control.fontSize).toBeGreaterThanOrEqual(16);
    }
    if (width === 390 || width === 1440)
      await dialog.screenshot({
        path: info.outputPath(`first-courier-assignment-${width}.png`),
      });
  }
  await dialog.getByRole("button", { name: "指派配送员", exact: true }).click();
  await expect
    .poll(() => page.evaluate(() => (window as any).fixture.calls[0]))
    .toEqual({
      path: "/admin/batches/source",
      body: { courierId: "c1", expectedVersion: 1 },
    });
});

test("packing checklist shows names and wraps long files inside the card", async ({
  page,
}, info) => {
  await fixture(page, "packing");
  const card = page.locator(".packing-card");
  await expect(card.getByText("实验报告（终稿）.docx")).toBeVisible();
  await expect(card.getByText("文件 3", { exact: true })).toBeVisible();
  await expect(card).not.toContainText(/internal-packing-id|missing-file-name/);
  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const bounds = await card.evaluate((element) => {
      const box = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      const contentRight = box.right - parseFloat(style.paddingRight);
      const names = Array.from(element.querySelectorAll(".packing-check b"));
      return {
        pageWidth: document.documentElement.scrollWidth,
        offset: scrollX,
        contentRight,
        nameRight: Math.max(
          ...names.map((name) => name.getBoundingClientRect().right),
        ),
      };
    });
    expect(bounds.pageWidth).toBeLessThanOrEqual(width + 1);
    expect(bounds.offset).toBe(0);
    expect(bounds.nameRight).toBeLessThanOrEqual(bounds.contentRight + 1);
    if (width === 390 || width === 1440)
      await card.screenshot({
        path: info.outputPath(`packing-names-${width}.png`),
      });
  }
});

test("binding cards show original filenames and wrap long names", async ({
  page,
}, info) => {
  await fixture(page, "binding");
  const list = page.locator(".production-list");
  await expect(
    list.getByText("实验报告（终稿）.docx", { exact: true }),
  ).toBeVisible();
  await expect(list).not.toContainText(/internal-binding-|\bCOMPLETED\b/);
  await expect(list.getByText("文件名未记录", { exact: true })).toBeVisible();
  const name = list.getByText(`${"verylong".repeat(25)}.pdf`, { exact: true });
  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const bounds = await name.evaluate((element) => {
      const card = element.closest(".panel")!;
      const content = card.getBoundingClientRect();
      const style = getComputedStyle(card);
      const text = document.createRange();
      text.selectNodeContents(element);
      const textBox = text.getBoundingClientRect();
      return {
        pageWidth: document.documentElement.scrollWidth,
        offset: scrollX,
        contentLeft: content.left + parseFloat(style.paddingLeft),
        contentRight: content.right - parseFloat(style.paddingRight),
        textLeft: textBox.left,
        textRight: textBox.right,
      };
    });
    expect(bounds.pageWidth).toBeLessThanOrEqual(width + 1);
    expect(bounds.offset).toBe(0);
    expect(bounds.textLeft).toBeGreaterThanOrEqual(bounds.contentLeft - 1);
    expect(bounds.textRight).toBeLessThanOrEqual(bounds.contentRight + 1);
    if (width === 390 || width === 1440)
      await list.screenshot({
        path: info.outputPath(`binding-names-${width}.png`),
      });
  }
});

test("withdrawing a started batch requires confirmation and sends its version", async ({
  page,
}, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page, "detail");
  await page.evaluate(() => {
    const fixture = (window as any).fixture;
    fixture.data.batches = [
      {
        ...fixture.data.batches[0],
        status: "DELIVERING",
        version: 5,
        allowedActions: ["WITHDRAW_PUBLICATION"],
      },
    ];
    fixture.refresh();
  });
  await page.getByRole("button", { name: "撤回发布", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "撤回发布" });
  const confirm = dialog.getByRole("button", { name: "确认撤回" });
  await expect(confirm).toBeDisabled();
  await dialog
    .getByRole("checkbox", { name: "我已确认所有资料尚未交给客户" })
    .check();
  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const bounds = await dialog.evaluate((element) => {
      const box = element.getBoundingClientRect();
      const text = element.querySelector("textarea")!.getBoundingClientRect();
      const padding = getComputedStyle(element);
      return {
        scroll: document.documentElement.scrollWidth,
        left: text.left,
        right: text.right,
        contentLeft: box.left + parseFloat(padding.paddingLeft),
        contentRight: box.right - parseFloat(padding.paddingRight),
      };
    });
    expect(bounds.scroll).toBeLessThanOrEqual(width + 1);
    expect(bounds.left).toBeGreaterThanOrEqual(bounds.contentLeft - 1);
    expect(bounds.right).toBeLessThanOrEqual(bounds.contentRight + 1);
    if (width === 390 || width === 1440)
      await dialog.screenshot({
        path: info.outputPath(`withdraw-publication-${width}.png`),
      });
  }
  await confirm.click();
  await expect
    .poll(() => page.evaluate(() => (window as any).fixture.calls[0]))
    .toEqual({
      path: "/admin/batches/source/withdraw-publication",
      body: { expectedVersion: 5, confirmNoHandover: true, reason: "" },
    });
});
