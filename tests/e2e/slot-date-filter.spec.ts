import { expect, test, type Page } from "@playwright/test";

async function mountSlots(page: Page) {
  await page.clock.install({ time: new Date("2026-11-06T04:00:00Z") });
  const main = await (await page.request.get("/src/main.tsx")).text();
  const react = main.match(/from "([^"]*\/react\.js\?[^"]*)"/)![1];
  const dom = main.match(/from "([^"]*\/react-dom_client\.js\?[^"]*)"/)![1];
  const source = await (
    await page.request.get("/src/admin/logistics.tsx")
  ).text();
  const router = source.match(
    /from "([^"]*\/react-router-dom\.js\?[^"]*)"/,
  )![1];
  await page.route("**/src/lib/store.tsx*", (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: `import React from '${react}';
      export const ADMIN='/admin',DEMO=true;
      export function useStore(){const [slots,setSlots]=React.useState(window.slotFixture.slots);window.slotFixture.update=setSlots;return {data:{slots,campuses:window.slotFixture.campuses,buildings:[],orders:[],batches:[],staff:[]},act:async()=>true,toast:()=>{},pendingActions:{}};}`,
    }),
  );
  await page.route("**/slot-date-fixture", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><div id="root"></div>
      <script type="module">import R from '/@react-refresh';R.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;</script>
      <script type="module">import React from '${react}';import ReactDOM from '${dom}';import {MemoryRouter} from '${router}';import {Slots} from '/src/admin/logistics.tsx';import '/src/styles.css';
        const base={campusId:'east',campusName:'东校区',enabled:true,capacity:30,start:'12:00',end:'13:00',weekday:'',version:1};
        window.slotFixture={campuses:[{id:'east',name:'东校区',selectionEnabled:true},{id:'west',name:'西校区',selectionEnabled:true},{id:'empty',name:'空校区',selectionEnabled:true}],slots:[
          {...base,id:'old',name:'上月历史',date:'2026-10-01'},
          {...base,id:'monday',name:'周一时段',date:'2026-11-02'},
          {...base,id:'today',name:'当天时段',date:'2026-11-06'},
          {...base,id:'sunday',name:'周日时段',date:'2026-11-08'},
          {...base,id:'west',name:'西校区当天',date:'2026-11-06',campusId:'west',campusName:'西校区'},
          {...base,id:'next',name:'下周时段',date:'2026-11-09'},
          {...base,id:'future',name:'未来时段',date:'2027-01-01'}]};
        ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(MemoryRouter,null,React.createElement('div',{className:'admin-main'},React.createElement('main',{className:'admin-content'},React.createElement(Slots)))));
      </script></html>`,
    }),
  );
  await page.goto("/slot-date-fixture");
}

test("defaults to this week and combines quick ranges, custom dates and campus", async ({
  page,
}) => {
  await mountSlots(page);
  const preset = page.getByLabel("日期范围", { exact: true });
  const start = page.getByLabel("开始日期", { exact: true });
  const end = page.getByLabel("结束日期", { exact: true });
  const rows = page.locator("tbody tr");
  await expect(preset).toHaveValue("week");
  await expect(start).toHaveValue("2026-11-02");
  await expect(end).toHaveValue("2026-11-08");
  await expect(rows).toHaveCount(4);
  await expect(
    page.getByText("2026年11月02日 — 2026年11月08日", { exact: false }),
  ).toBeVisible();
  await preset.selectOption("today");
  await expect(rows).toHaveCount(2);
  await page.getByLabel("校区筛选").selectOption("east");
  await expect(rows).toHaveCount(1);
  await preset.selectOption("month");
  await expect(start).toHaveValue("2026-11-01");
  await expect(end).toHaveValue("2026-11-30");
  await expect(rows).toHaveCount(4);
  await start.fill("2026-11-02");
  await end.fill("2026-11-08");
  await expect(preset).toHaveValue("week");
  await start.fill("2026-11-06");
  await end.fill("2026-11-06");
  await expect(preset).toHaveValue("today");
  await start.fill("2026-11-01");
  await end.fill("2026-11-30");
  await expect(preset).toHaveValue("month");
  await start.fill("2026-11-03");
  await end.fill("2026-11-05");
  await expect(preset).toHaveValue("custom");
  await expect(rows).toHaveCount(0);
  await expect(page.getByText("所选日期范围内暂无配送时段")).toBeVisible();
  await start.fill("2026-11-07");
  await expect(page.getByRole("alert")).toContainText(
    "结束日期不能早于开始日期",
  );
  await preset.selectOption("all");
  await expect(start).toHaveValue("2026-10-01");
  await expect(end).toHaveValue("2027-01-01");
  await expect(rows).toHaveCount(6);
  await page.getByLabel("校区筛选").selectOption("west");
  await expect(start).toHaveValue("2026-11-06");
  await expect(end).toHaveValue("2026-11-06");
  await expect(preset).toHaveValue("all");
  await expect(rows).toHaveCount(1);
  await page.getByLabel("校区筛选").selectOption("empty");
  await expect(start).toHaveValue("");
  await expect(end).toHaveValue("");
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.getByLabel("校区筛选").selectOption("");
  await page.evaluate(() => {
    const fixture = (window as any).slotFixture;
    fixture.update([
      ...fixture.slots,
      { ...fixture.slots[0], id: "new", name: "新增未来", date: "2027-02-01" },
    ]);
  });
  await expect(end).toHaveValue("2027-02-01");
  await expect(rows).toHaveCount(8);
  await page.evaluate(() => (window as any).slotFixture.update([]));
  await expect(start).toHaveValue("");
  await expect(end).toHaveValue("");
  await expect(page.getByText("尚未配置配送时段")).toBeVisible();
});

test("relative ranges follow the Shanghai date while custom dates stay fixed", async ({
  page,
}) => {
  await mountSlots(page);
  const start = page.getByLabel("开始日期", { exact: true });
  const end = page.getByLabel("结束日期", { exact: true });
  const preset = page.getByLabel("日期范围", { exact: true });
  await preset.selectOption("today");
  await expect(start).toHaveValue("2026-11-06");
  await preset.selectOption("week");
  await expect(start).toHaveValue("2026-11-02");
  await page.clock.setSystemTime(new Date("2026-11-08T16:00:00Z"));
  await page.clock.runFor(60_000);
  await expect(start).toHaveValue("2026-11-09");
  await expect(end).toHaveValue("2026-11-15");
  await start.fill("2026-10-02");
  await end.fill("2026-10-03");
  await page.clock.setSystemTime(new Date("2026-12-01T04:00:00Z"));
  await page.clock.runFor(60_000);
  await expect(start).toHaveValue("2026-10-02");
  await expect(end).toHaveValue("2026-10-03");
});

test("date controls stay inside the card from mobile to desktop", async ({
  page,
}, info) => {
  await mountSlots(page);
  await page.addStyleTag({
    content: 'input[type="date"] { min-width: 320px; }',
  });
  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.getByLabel("结束日期", { exact: true }).focus();
    const metrics = await page
      .locator(".slot-filter-toolbar")
      .evaluate((toolbar) => {
        const panel = toolbar.closest(".panel")!;
        const box = panel.getBoundingClientRect();
        const style = getComputedStyle(panel);
        return {
          viewport: innerWidth,
          scroll: document.documentElement.scrollWidth,
          offset: scrollX,
          rootOverflow: [document.documentElement, document.body].map(
            (element) => getComputedStyle(element).overflowX,
          ),
          contentLeft: box.left + parseFloat(style.paddingLeft),
          contentRight: box.right - parseFloat(style.paddingRight),
          controls: [...toolbar.querySelectorAll("input,select")].map(
            (input) => {
              const rect = input.getBoundingClientRect();
              const field = input.closest(".field")!.getBoundingClientRect();
              return {
                left: rect.left,
                right: rect.right,
                height: rect.height,
                fieldLeft: field.left,
                fieldRight: field.right,
                font: parseFloat(getComputedStyle(input).fontSize),
              };
            },
          ),
        };
      });
    expect(metrics.scroll).toBeLessThanOrEqual(width + 1);
    expect(metrics.offset).toBe(0);
    expect(metrics.rootOverflow).not.toContain("hidden");
    expect(metrics.rootOverflow).not.toContain("clip");
    for (const control of metrics.controls) {
      expect(control.left).toBeGreaterThanOrEqual(
        Math.max(metrics.contentLeft, control.fieldLeft) - 1,
      );
      expect(control.right).toBeLessThanOrEqual(
        Math.min(metrics.contentRight, control.fieldRight) + 1,
      );
      expect(control.height).toBeGreaterThanOrEqual(44);
      if (width <= 540) expect(control.font).toBeGreaterThanOrEqual(16);
    }
    if (width === 390 || width === 1440)
      await page.screenshot({
        path: info.outputPath(`slot-date-filter-${width}.png`),
        fullPage: true,
      });
  }
});
