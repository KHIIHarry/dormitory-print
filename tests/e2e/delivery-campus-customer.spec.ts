import { PDFDocument } from "pdf-lib";
import { expect, test } from "@playwright/test";

test("switching campuses replaces the available slots and keeps checkout progress", async ({
  page,
}, info) => {
  const transformed = await (
    await page.request.get("/src/customer/review.tsx")
  ).text();
  const reactUrl = transformed.match(/from "([^"]*\/react\.js\?[^"]*)"/)![1];
  await page.route("**/src/lib/store.tsx*", (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: `
    import React from '${reactUrl}'; const {useSyncExternalStore}=React;
    import {createDemo} from '/src/lib/demo.ts';
    const data=createDemo();
    data.config.unpaidTimeoutMinutes=30;
    data.orders[0]={...data.orders[0],locationId:'missing-building',locationSnapshot:{name:'旧楼快照',campusId:'campus-east'},allowedActions:['ORDER_CHANGE_LOCATION_PRE_DELIVERY']};
    data.slots=[
      {id:'east-slot',campusId:'campus-east',name:'东校区时段',date:'2099-01-01',weekday:'周四',start:'12:00',end:'13:00',cutoffAt:'2098-12-31T14:00:00Z',enabled:true,selectable:true,capacity:10},
      {id:'west-slot',campusId:'campus-west',name:'西校区时段',date:'2099-01-01',weekday:'周四',start:'12:00',end:'13:00',enabled:true,selectable:true,capacity:10},
      {id:'legacy',name:'未归属时段',date:'2099-01-01',weekday:'周四',start:'12:00',end:'13:00',enabled:true,selectable:true,capacity:10}]; const file=data.orders.flatMap(o=>o.files).find(f=>f.kind==='DOCUMENT');
    const listeners=new Set();
    let store={data,draft:[file],editingOrderId:null,checkoutMemory:{},savedAccessLinks:{},resources:{config:{hasData:true,loading:false,error:''},slots:{hasData:true},buildings:{hasData:true}},pendingActions:{},actionIssues:{},loading:false,refreshing:false,refreshError:'',error:'',authReady:true,role:null};
    const apply=(value,previous)=>typeof value==='function'?value(previous):value;
    Object.assign(store,{toast:()=>{},updateOrder:async()=>{window.reviewFixture.updated=true;return true},clearActionIssues:()=>{},refresh:async()=>{},setDraft:()=>{},setEditingOrderId:()=>{},setSavedAccessLinks:()=>{},setData:v=>{store.data=apply(v,store.data)},setCheckoutMemory:v=>{store.checkoutMemory=apply(v,store.checkoutMemory)}});
    window.reviewFixture={get store(){return store},calls:[],removeBuilding(id){store={...store,data:{...store.data,buildings:store.data.buildings.filter(b=>b.id!==id)}};listeners.forEach(f=>f())}, setBuildingCatalogReady(ready){store={...store,resources:{...store.resources,buildings:{hasData:ready}}};listeners.forEach(f=>f())},setReady(ready){store={...store,resources:{config:{hasData:ready,loading:!ready,error:''}}};listeners.forEach(f=>f())}};
    window.reviewFixture.updateSlotWindow=(id,start,end)=>{
      store={...store,data:{...store.data,slots:store.data.slots.map(slot=>slot.id===id?{...slot,start,end}:slot)}};
      listeners.forEach(f=>f());
    };
    export const DEMO=false,ADMIN='/manage'; export const Provider=({children})=>children;
    export const useStore=()=>useSyncExternalStore(fn=>{listeners.add(fn);return()=>listeners.delete(fn)},()=>store);
  `,
    }),
  );
  await page.route("**/src/growth/identity.tsx*", (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: `
    const identity={generation:0,customer:{authKind:'ANONYMOUS',capabilities:[]},staff:null,orderAccess:null,staffReady:true,customerReady:true};
    export const IdentityProvider=({children})=>children; export const useIdentity=()=>identity;
  `,
    }),
  );
  await page.route("**/src/growth/client.ts*", (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: `
    import {buildQuote} from '/src/growth/pricing.ts'; import {demoPriceLines} from '/src/customer/checkout.ts';
    export const getIdentityGeneration=()=>0,advanceIdentity=()=>0;
    export const useGrowthResource=()=>({data:null,loading:false,error:'',reload:async()=>{},refreshing:false,hasData:false});
    export async function growthRequest(path,options={}){
      const fixture=window.reviewFixture;fixture.calls.push(path);
      const file=fixture.store.draft[0],config=fixture.store.data.config;
      const quote=buildQuote(demoPriceLines([file],config),config.volumeDiscounts||{enabled:false,id:'volume',version:1,tiers:[]},[],{selectionMode:'AUTO'});
      return {id:'prep-config',version:1,status:'READY',quote,artifacts:[{id:'pdf-config',fileId:file.id,segmentId:'s',sha256:'a'.repeat(64),url:'/fixture.pdf',pages:1,sheets:1,blankBacks:0,range:'1',copies:1,color:'BW',sides:'SINGLE',flip:'NONE'}]};
    }
  `,
    }),
  );
  page.on("pageerror", (error) =>
    console.log("HARNESS PAGE ERROR", error.message),
  );
  const pdf = await PDFDocument.create();
  pdf.addPage([595.28, 841.89]);
  const pdfBytes = Buffer.from(await pdf.save());
  await page.route("**/fixture.pdf", (route) =>
    route.fulfill({ contentType: "application/pdf", body: pdfBytes }),
  );
  await page.route("**/api/v1/orders/*/after-sales", (route) => route.fulfill({ json: { items: [] } }));
  await page.goto("/u/review");
  await expect(page.getByLabel("微信昵称 *", { exact: true })).toBeVisible();
  const fees = page.locator(".checkout-summary");
  await expect(fees.getByText(/有效期至/)).toBeVisible();
  await expect(fees.getByText(/报价版本|价格版本|后台配置/)).toHaveCount(0);
  await expect(fees.getByText("合计应付")).toBeVisible();
  await expect(fees.getByText(/照片与装订不计入满量折扣/)).toBeVisible();
  await expect(fees.getByText(/订单提交后锁定报价/)).toBeVisible();
  await expect(fees.getByRole("checkbox", { name: /我确认本次应付/ })).toBeVisible();
  await expect(fees.getByText(/核对提取码与金额后线下付款/)).toBeVisible();
  await page.getByLabel("微信昵称 *", { exact: true }).fill("保留的昵称");
  await page.getByRole("button", { name: /东校区/ }).first().click();
  const east = page.getByRole("button", { name: /东校区时段/ });
  await expect(east).toContainText("12:00 – 13:00");
  await east.click();
  await page.evaluate(() =>
    (window as any).reviewFixture.updateSlotWindow("east-slot", "09:30", "09:45"),
  );
  await expect(east).toContainText("09:30 – 09:45");
  await expect(east).toContainText(/截单.*22:00/);
  await expect(east).toHaveClass(/selected/);
  const slotPanel = page.locator("section").filter({
    has: page.getByRole("heading", { name: "选择预约配送时段" }),
  });
  const slotHint = slotPanel.getByText(/^所选为申请时段/);
  const deadlineHint = slotPanel.getByText(
    "提交后 30 分钟内未确认收款，订单将自动关闭。",
    { exact: true },
  );
  await expect(deadlineHint).toBeVisible();
  const typography = (hint: typeof slotHint) =>
    hint.evaluate((element) => {
      const { color, fontSize, fontWeight, lineHeight } = getComputedStyle(element);
      return { color, fontSize, fontWeight, lineHeight };
    });
  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(east).toBeVisible();
    expect(await typography(deadlineHint)).toEqual(await typography(slotHint));
    const [slotBounds, deadlineBounds] = await Promise.all([
      slotHint.boundingBox(),
      deadlineHint.boundingBox(),
    ]);
    expect(deadlineBounds?.x).toBe(slotBounds?.x);
    expect(deadlineBounds?.width).toBe(slotBounds?.width);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    if (width === 390 || width === 1440) {
      await slotPanel.screenshot({
        path: info.outputPath(`checkout-slot-hints-${width}.png`),
      });
      await east.screenshot({ path: info.outputPath(`checkout-window-${width}.png`) });
      await fees.screenshot({ path: info.outputPath(`checkout-fees-${width}.png`) });
    }
  }
  await expect(page.getByRole("button", { name: /西校区时段/ })).toHaveCount(0);
  const firstBuilding = page.locator(".building-choices button").first();
  await firstBuilding.click();
  await expect(firstBuilding).toHaveClass(/selected/);
  const selectedBuilding = await page.evaluate(() => (Object.values((window as any).reviewFixture.store.checkoutMemory)[0] as any).building);
  await page.evaluate(() => (window as any).reviewFixture.setBuildingCatalogReady(false));
  await page.evaluate((id) => (window as any).reviewFixture.removeBuilding(id), selectedBuilding);
  await expect.poll(() => page.evaluate(() => (Object.values((window as any).reviewFixture.store.checkoutMemory)[0] as any).building)).toBe(selectedBuilding);
  await page.evaluate(() => (window as any).reviewFixture.setBuildingCatalogReady(true));
  await expect.poll(() => page.evaluate(() => (Object.values((window as any).reviewFixture.store.checkoutMemory)[0] as any).building)).toBe("");
  await page.getByRole("button", { name: /东校区时段/ }).click();
  await page.getByRole("button", { name: /西校区/ }).first().click();
  await expect(page.getByRole("button", { name: /东校区时段/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /西校区时段/ })).toBeVisible();
  await expect(page.getByLabel("微信昵称 *", { exact: true })).toHaveValue("保留的昵称");
  await expect(page.locator(".slot-choices button.selected")).toHaveCount(0);
  await page.goto("/u/orders/order-1");
  await expect(page.getByText("旧楼快照")).toBeVisible();
  await page.getByRole("button", { name: "修改楼栋" }).click();
  const locationSelect = page.getByLabel("选择同校区的新楼栋");
  await expect(locationSelect.locator("option")).not.toHaveCount(1);
  await locationSelect.selectOption("building-1");
  await expect(page.getByRole("button", { name: "保存楼栋" })).toBeEnabled();
  await page.evaluate(() => (window as any).reviewFixture.removeBuilding("building-1"));
  await expect(locationSelect).toHaveValue("");
  await expect(page.getByRole("button", { name: "保存楼栋" })).toBeDisabled();
});
