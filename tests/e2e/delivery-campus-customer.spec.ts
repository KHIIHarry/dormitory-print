import { PDFDocument } from "pdf-lib";
import { expect, test } from "@playwright/test";

test("switching campuses replaces the available slots and keeps checkout progress", async ({
  page,
}) => {
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
    data.orders[0]={...data.orders[0],locationId:'missing-building',locationSnapshot:{name:'旧楼快照',campusId:'campus-east'},allowedActions:['ORDER_CHANGE_LOCATION_PRE_DELIVERY']};
    data.slots=[
      {id:'east-slot',campusId:'campus-east',name:'东校区时段',date:'2099-01-01',weekday:'周四',start:'12:00',end:'13:00',enabled:true,selectable:true,capacity:10},
      {id:'west-slot',campusId:'campus-west',name:'西校区时段',date:'2099-01-01',weekday:'周四',start:'12:00',end:'13:00',enabled:true,selectable:true,capacity:10},
      {id:'legacy',name:'未归属时段',date:'2099-01-01',weekday:'周四',start:'12:00',end:'13:00',enabled:true,selectable:true,capacity:10}]; const file=data.orders.flatMap(o=>o.files).find(f=>f.kind==='DOCUMENT');
    const listeners=new Set();
    let store={data,draft:[file],editingOrderId:null,checkoutMemory:{},savedAccessLinks:{},resources:{config:{hasData:true,loading:false,error:''},slots:{hasData:true},buildings:{hasData:true}},pendingActions:{},actionIssues:{},loading:false,refreshing:false,refreshError:'',error:'',authReady:true,role:null};
    const apply=(value,previous)=>typeof value==='function'?value(previous):value;
    Object.assign(store,{toast:()=>{},updateOrder:async()=>{window.reviewFixture.updated=true;return true},clearActionIssues:()=>{},refresh:async()=>{},setDraft:()=>{},setEditingOrderId:()=>{},setSavedAccessLinks:()=>{},setData:v=>{store.data=apply(v,store.data)},setCheckoutMemory:v=>{store.checkoutMemory=apply(v,store.checkoutMemory)}});
    window.reviewFixture={get store(){return store},calls:[],removeBuilding(id){store={...store,data:{...store.data,buildings:store.data.buildings.filter(b=>b.id!==id)}};listeners.forEach(f=>f())}, setBuildingCatalogReady(ready){store={...store,resources:{...store.resources,buildings:{hasData:ready}}};listeners.forEach(f=>f())},setReady(ready){store={...store,resources:{config:{hasData:ready,loading:!ready,error:''}}};listeners.forEach(f=>f())}};
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
  await page.getByLabel("微信昵称 *", { exact: true }).fill("保留的昵称");
  await page.getByRole("button", { name: /东校区/ }).first().click();
  await expect(page.getByRole("button", { name: /东校区时段/ })).toBeVisible();
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
