import { PDFDocument } from "pdf-lib";
import { expect, test } from "@playwright/test";

test("checkout waits for current config before preparing and blocks commit when config is invalidated", async ({
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
    const data=createDemo(); const file=data.orders.flatMap(o=>o.files).find(f=>f.kind==='DOCUMENT');
    const listeners=new Set();
    let store={data,draft:[file],editingOrderId:null,checkoutMemory:{},savedAccessLinks:{},resources:{config:{hasData:false,loading:true,error:''}},pendingActions:{},actionIssues:{},loading:false,refreshing:false,refreshError:'',error:'',authReady:true,role:null};
    const apply=(value,previous)=>typeof value==='function'?value(previous):value;
    Object.assign(store,{toast:()=>{},refresh:async()=>{},setDraft:()=>{},setEditingOrderId:()=>{},setSavedAccessLinks:()=>{},setData:v=>{store.data=apply(v,store.data)},setCheckoutMemory:v=>{store.checkoutMemory=apply(v,store.checkoutMemory)}});
    window.reviewFixture={get store(){return store},calls:[],setReady(ready){store={...store,resources:{config:{hasData:ready,loading:!ready,error:''}}};listeners.forEach(f=>f())}};
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
  await page.goto("/u/review");
  await expect(page.getByLabel("微信昵称 *", { exact: true })).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => (window as any).reviewFixture.calls.length))
    .toBe(0);
  await page.evaluate(() => (window as any).reviewFixture.setReady(true));
  const ack = page.getByRole("checkbox", {
    name: "我已检查这份最终 PDF，确认打印设置",
    exact: true,
  });
  await expect(ack).toBeVisible();
  await ack.click();
  await page
    .getByRole("button", { name: "我已确认最终打印格式", exact: true })
    .click();
  await page.getByRole("checkbox", { name: /我确认本次应付/ }).check();
  const submit = page.getByRole("button", {
    name: "确认并提交订单",
    exact: true,
  });
  await expect(submit).toBeEnabled();
  await page.evaluate(() => (window as any).reviewFixture.setReady(false));
  await expect(submit).toBeDisabled();
  const previousCalls = await page.evaluate(
    () => (window as any).reviewFixture.calls.length,
  );
  await page.evaluate(() => (window as any).reviewFixture.setReady(true));
  await expect(ack).toBeChecked();
  await expect
    .poll(() => page.evaluate(() => (window as any).reviewFixture.calls.length))
    .toBeGreaterThan(previousCalls);
});
