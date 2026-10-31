import {beforeEach,describe,it,expect} from 'vitest';
import {demoRequest,getDemoGrowth,syncDemoOrders,defaultVolume,validateDemoOrderAction,visibleDemoOrders} from '../src/growth/demo';
import {buildQuote} from '../src/growth/pricing';
import type {Order} from '../src/lib/types';
const lines=[{key:'p',relatedId:'p',unit:'SHEET' as const,quantity:5,unitPriceCents:10,amountCents:50,service:'A4' as const}];
const db=getDemoGrowth();
beforeEach(()=>{db.orders.clear();db.preps.clear();db.coupons=[];db.links=[];db.tokens.clear();db.volume=structuredClone(defaultVolume);db.guestPrincipalId='guest-test';db.customer={role:'CUSTOMER',authKind:'ANONYMOUS',capabilities:[]};db.access=null;db.imports.clear();});
describe('开发适配严格遵守报价和授权规则',()=>{
 it('其他订单预约券不能进入新报价，零元报价可预约',async()=>{
  await demoRequest('/__demo/customer',{method:'POST'});
  db.coupons.push({id:'c',couponGrantId:'cg',campaignId:'cp',name:'测试',amountCents:50,minSpendCents:0,service:'A4',status:'RESERVED',reservedOrderId:'other',riskHold:'NONE',validUntil:null,version:1});
  const p=await demoRequest('/preparations',{method:'POST',body:{lines,checkoutAccountId:db.account.id}}) as any;
  expect(p.quote.discountCents).toBe(0);db.coupons[0].status='AVAILABLE';
  const next=await demoRequest('/preparations',{method:'POST',body:{lines,checkoutAccountId:db.account.id}}) as any;
  expect(next.quote.totalCents).toBe(0);
  await demoRequest('/__demo/reserve',{method:'POST',body:{preparationId:next.id,quoteId:next.quote.id,orderId:'o'}});
  expect(db.coupons[0].status).toBe('RESERVED');
 });
 it('冻结与陈旧报价阻止预约；不能用旧版本结算',async()=>{
  await demoRequest('/__demo/customer',{method:'POST'});
  db.coupons.push({id:'c',couponGrantId:'cg',campaignId:'cp',name:'测试',amountCents:50,minSpendCents:0,service:'A4',status:'AVAILABLE',riskHold:'NONE',validUntil:null,version:1});
  const p=await demoRequest('/preparations',{method:'POST',body:{lines,checkoutAccountId:db.account.id}}) as any;db.coupons[0].riskHold='FROZEN';
  await expect(demoRequest('/__demo/reserve',{method:'POST',body:{preparationId:p.id,quoteId:p.quote.id,orderId:'o'}})).rejects.toThrow();
  const o={id:'o',version:2,currentQuoteId:'q',status:'PENDING_CONFIRMATION',totalCents:0,contentRevision:1,blockers:[{code:'COUPON_FROZEN',message:'冻结'}]} as Order;db.orders.set('o',o);
  expect(()=>validateDemoOrderAction('/admin/orders/o/confirm-payment',{quoteId:'q',expectedVersion:2,contentRevision:1,receivedCents:0,settlementKind:'ZERO_PAYABLE'})).toThrow();
 });
 it('预约券冻结保留锁价，解冻和撤单分别解除阻断和返券',async()=>{
  db.coupons.push({id:'c',couponGrantId:'cg',campaignId:'cp',name:'测试',amountCents:50,minSpendCents:0,service:'A4',status:'RESERVED',reservedOrderId:'o',riskHold:'NONE',validUntil:null,version:1});
  const q=buildQuote(lines,defaultVolume,db.coupons,{},'account','o');
  const o={id:'o',version:1,currentQuoteId:q.id,quote:q,status:'PENDING_CONFIRMATION',totalCents:q.totalCents,contentRevision:1} as Order;db.orders.set('o',o);
  await demoRequest('/admin/promotions/coupons/cg/freeze',{method:'POST',body:{expectedVersion:1,reason:'核对'}});
  expect(db.orders.get('o')?.totalCents).toBe(0);expect(db.orders.get('o')?.blockers?.map(b=>b.code)).toContain('COUPON_FROZEN');
  await demoRequest('/admin/promotions/coupons/cg/unfreeze',{method:'POST',body:{expectedVersion:2,reason:'核对完成'}});
  expect(db.orders.get('o')?.blockers?.map(b=>b.code)).not.toContain('COUPON_FROZEN');
  syncDemoOrders([{...db.orders.get('o')!,status:'CANCELLED'}]);expect(db.coupons[0].status).toBe('AVAILABLE');expect(db.coupons[0].reservedOrderId).toBeUndefined();
 });
 it('导入只派生一单授权，撤销源链接后不可再访问',async()=>{
  const o={id:'o',ownerPrincipalId:'someone-else',status:'PENDING_CONFIRMATION',allowedActions:['ORDER_CANCEL_PENDING']} as Order;db.orders.set('o',o);
  const link=await demoRequest('/admin/orders/o/access-links',{method:'POST',body:{reason:'测试'}}) as any;
  await demoRequest('/order-access/exchange',{method:'POST',body:{orderId:'o',accessToken:link.accessToken}});await demoRequest('/__demo/customer',{method:'POST'});
  await demoRequest('/customer/account/import-order',{method:'POST',body:{orderId:'o',confirm:true}});
  expect(visibleDemoOrders('customer',db.customer,null)).toHaveLength(1);expect(o.ownerPrincipalId).toBe('someone-else');
  await demoRequest(`/admin/orders/o/access-links/${link.id}/revoke`,{method:'POST',body:{expectedVersion:1,reason:'撤销'}});
  expect(visibleDemoOrders('customer',db.customer,null)).toHaveLength(0);
 });
});
