import { expect, test } from "@playwright/test";

test.use({ channel: "chrome", trace: "off" });

test("successive direct changes reuse the returned order version", async ({
  page,
}) => {
  page.on("pageerror", (error) =>
    console.log("DELIVERY FIXTURE ERROR", error.message),
  );
  const main = await (await page.request.get("/src/main.tsx")).text();
  const reactUrl = main.match(/from "([^\"]*\/react\.js\?[^\"]*)"/)![1];
  const domUrl = main.match(
    /from "([^\"]*\/react-dom_client\.js\?[^\"]*)"/,
  )![1];
  await page.route("**/src/growth/client.ts*", (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: `
    export const getIdentityGeneration=()=>0,advanceIdentity=()=>0;
    export const useGrowthResource=()=>({data:null,loading:false,hasData:false,reload:async()=>{}});
    export async function growthRequest(path,options={}) {
      const f=window.deliveryFixture;
      if(options.method==='POST') {
        f.versions.push(options.body.expectedVersion);
        if(options.body.expectedVersion!==f.order.version) throw new Error('VERSION_CONFLICT');
        if (!path.endsWith('/slot-change')) throw new Error('WRONG_ENDPOINT');
        f.order={...f.order,version:f.order.version+1,requestedSlotId:options.body.slotId};
        return {...f.order};
      }
      return {items:[{...f.order}]};
    }
  `,
    }),
  );
  await page.route("**/delivery-ui-fixture", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: `
    <html><body><div id="root"></div><script type="module">
    import RefreshRuntime from '/@react-refresh'; RefreshRuntime.injectIntoGlobalHook(window);
    window.$RefreshReg$=()=>{}; window.$RefreshSig$=()=>type=>type; window.__vite_plugin_react_preamble_installed__=true;
    </script><script type="module">
    import React from '${reactUrl}'; import ReactDOM from '${domUrl}';
    import {SlotProposals} from '/src/admin/logistics.tsx';
    import '/src/styles.css';
    window.deliveryFixture={versions:[],order:{id:'pending',pickupCode:'P0001',version:1,status:'PENDING_CONFIRMATION',campusId:'campus-east'}};
    ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(SlotProposals,{slots:[{id:'next',campusId:'campus-east',date:'2030-11-01',name:'下个时段',start:'12:00',end:'13:00',enabled:true,capacity:30},{id:'later',campusId:'campus-east',date:'2030-11-02',name:'稍后时段',start:'12:00',end:'13:00',enabled:true,capacity:30}]}));
    </script></body></html>
  `,
    }),
  );
  await page.goto("/delivery-ui-fixture");
  for (let i = 0; i < 2; i++) {
    await page
      .getByRole("button", { name: "调整配送时段", exact: true })
      .click();
    await page
      .getByLabel("配送时段", { exact: true })
      .selectOption(i === 0 ? "next" : "later");
    await page.getByLabel("调整原因", { exact: true }).fill(`调整原因${i}`);
    await page
      .getByRole("button", { name: "保存配送时段", exact: true })
      .click();
    if (i === 0) await expect(page.getByRole("dialog")).toHaveCount(0);
  }
  await expect
    .poll(() => page.evaluate(() => (window as any).deliveryFixture.versions))
    .toEqual([1, 2]);
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("legacy inspection loads only on demand and failed notification retry requires a reason", async ({
  page,
}) => {
  const main = await (await page.request.get("/src/main.tsx")).text();
  const reactUrl = main.match(/from "([^\"]*\/react\.js\?[^\"]*)"/)![1];
  const domUrl = main.match(
    /from "([^\"]*\/react-dom_client\.js\?[^\"]*)"/,
  )![1];
  await page.route("**/src/growth/client.ts*", (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: `
    export const getIdentityGeneration=()=>0,advanceIdentity=()=>0;
    export const useGrowthResource=()=>({data:null,loading:false,hasData:false,reload:async()=>{}});
    export async function growthRequest(path,options={}) {
      window.deliveryFixture.calls.push({path,method:options.method||'GET',body:options.body});
      if(path.includes('delivery-repair')) return {readOnly:true,generatedAt:'2026-11-01T04:00:00Z',counts:{unconfiguredSlots:1,paidUnassignedOrders:1,ambiguousGroups:0,detachedBatches:0},unconfiguredSlots:[{slotId:'legacy-slot',name:'旧时段',nextAction:'核实并补齐真实接单规则'}],paidUnassignedOrders:[{orderId:'paid-order',nextAction:'保留实收并核对分配'}],ambiguousGroups:[],detachedBatches:[],historicalCounts:{deliveredOrders:2},nextSteps:['唯一候选仅供核对，需使用现有调度。']};
      return {...window.deliveryFixture.notice,status:'PENDING',version:2,lastError:''};
    }
  `,
    }),
  );
  await page.route("**/delivery-notice-fixture", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: `
    <html><body><div id="root"></div><script type="module">
    import RefreshRuntime from '/@react-refresh'; RefreshRuntime.injectIntoGlobalHook(window);
    window.$RefreshReg$=()=>{}; window.$RefreshSig$=()=>type=>type; window.__vite_plugin_react_preamble_installed__=true;
    </script><script type="module">
    import React from '${reactUrl}'; import ReactDOM from '${domUrl}';
    import {DeliveryRepairPanel} from '/src/admin/logistics.tsx';
    import {DeliveryNoticePanel} from '/src/admin/orders.tsx';
    import {growthRequest} from '/src/growth/client.ts'; import '/src/styles.css';
    window.deliveryFixture={calls:[],notice:{id:'notice-1',version:1,status:'FAILED',attempts:3,lastError:'SMTPUnavailable',createdAt:'2026-11-01T04:00:00Z'}};
    function Fixture(){const [notice,setNotice]=React.useState(window.deliveryFixture.notice);return React.createElement(React.Fragment,null,React.createElement(DeliveryRepairPanel),React.createElement(DeliveryNoticePanel,{notices:[notice],canRetry:true,onRetry:async(n,reason)=>setNotice(await growthRequest('/admin/delivery-notices/'+n.id+'/retry',{method:'POST',body:{expectedVersion:n.version,reason}}))}));}
    ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(Fixture));
    </script></body></html>
  `,
    }),
  );
  await page.goto("/delivery-notice-fixture");
  await expect(
    page.getByRole("button", { name: "查看旧数据检查", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => (window as any).deliveryFixture.calls),
  ).toEqual([]);
  await page
    .getByRole("button", { name: "查看旧数据检查", exact: true })
    .click();
  await expect(
    page.getByText("唯一候选仅供核对，需使用现有调度。", { exact: true }),
  ).toBeVisible();
  await page.getByText("待补接单规则的时段：1", { exact: true }).click();
  await expect(
    page.getByText("时段 ID：legacy-slot", { exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => (window as any).deliveryFixture.calls),
  ).toEqual([{ path: "/admin/delivery-repair/preview", method: "GET" }]);
  await page.getByRole("button", { name: "重试通知", exact: true }).click();
  await page.getByRole("button", { name: "确认重试", exact: true }).click();
  await expect(page.getByText("请填写重试原因", { exact: true })).toBeVisible();
  await page.getByLabel("重试原因", { exact: true }).fill("人工核对后重试");
  await page.getByRole("button", { name: "确认重试", exact: true }).click();
  await expect(page.getByText("等待发送", { exact: true })).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(
    await page.evaluate(() => (window as any).deliveryFixture.calls.at(-1)),
  ).toEqual({
    path: "/admin/delivery-notices/notice-1/retry",
    method: "POST",
    body: { expectedVersion: 1, reason: "人工核对后重试" },
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: `output/delivery-ui-qa/repair-notices-${test.info().project.name}.png`,
    fullPage: true,
  });
});
