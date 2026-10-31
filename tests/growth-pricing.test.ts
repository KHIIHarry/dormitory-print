import {describe,it,expect} from 'vitest';
import {parsePayRate,validateVolumePolicy,selectVolume,buildQuote,assertQuote,eligibleReferral} from '../src/growth/pricing';
import type {VolumePolicy,Coupon,PriceLine} from '../src/growth/types';
const policy:VolumePolicy={id:'p',version:1,enabled:true,effectiveAt:null,description:'test',tiers:[{id:'tier',minSheets:14,payRateBps:9000}]};
const lines:PriceLine[]=[{key:'a',relatedId:'a',unit:'SHEET',quantity:14,unitPriceCents:100,amountCents:1400,service:'A4'},{key:'photo',relatedId:'photo',unit:'SHEET',quantity:1,unitPriceCents:500,amountCents:500,service:'PHOTO'}];
const coupon:Coupon={id:'c',couponGrantId:'c',campaignId:'campaign',name:'test',amountCents:140,minSpendCents:0,service:'A4',status:'AVAILABLE',riskHold:'NONE',validUntil:null,version:1};
describe('growth v1.3 pricing',()=>{
 it('interprets pay rates precisely and validates duplicate/worsening tiers',()=>{expect(parsePayRate('9')).toBe(9000);expect(parsePayRate('8.555')).toBe(8555);expect(()=>parsePayRate('8.5555')).toThrow();expect(validateVolumePolicy({...policy,tiers:[...policy.tiers,{id:'b',minSheets:14,payRateBps:9500}]}).length).toBeGreaterThan(0)});
 it('includes the threshold and rounds A4 subtotal only once',()=>{expect(selectVolume(13,1001,policy)).toBeNull();expect(selectVolume(14,1001,policy)?.discountCents).toBe(100);expect(selectVolume(14,1001,{...policy,enabled:false})).toBeNull()});
 it('ties prefer volume, preserve coupons and allocate nothing to photos',()=>{const quote=buildQuote(lines,policy,[coupon],{selectionMode:'AUTO'},'account');expect(quote.selectedPromotionType).toBe('VOLUME_TIER');expect(quote.totalCents).toBe(1760);expect(quote.discountAllocations.find(a=>a.lineKey==='photo')?.amountCents??0).toBe(0)});
 it('NO_COUPON preserves volume while specific coupons still compete',()=>{expect(buildQuote(lines,policy,[coupon],{selectionMode:'NO_COUPON'},'a').selectedPromotionType).toBe('VOLUME_TIER');expect(buildQuote(lines,policy,[{...coupon,amountCents:200}],{selectionMode:'SPECIFIC_COUPON',couponGrantId:'c'},'a').selectedPromotionType).toBe('COUPON')});
 it('zero payable is valid and cannot trigger paid referral eligibility',()=>{const q=buildQuote(lines,{...policy,tiers:[]},[{...coupon,service:'ALL',amountCents:2000}],{},'a');expect(q.totalCents).toBe(0);expect(()=>assertQuote(q)).not.toThrow();expect(eligibleReferral({status:'DELIVERED',checkoutAccountId:'a',settlementKind:'ZERO_PAYABLE',cashReceivedCents:0})).toBe(false)});
 it('guests cannot use account coupons and malformed quote snapshots are rejected',()=>{expect(buildQuote(lines,{...policy,enabled:false},[coupon],{},null).discountCents).toBe(0);expect(()=>assertQuote({...buildQuote(lines,policy,[],{},null),totalCents:1501})).toThrow()});
});

function staffQuote() {
 const q=buildQuote([...lines,{key:'binding',relatedId:'booklet',unit:'BOOKLET',quantity:1,unitPriceCents:100,amountCents:100,service:'BINDING'}],{...policy,enabled:false},[],{},'employee-account');
 const terms={source:'STAFF_GRADE' as const,staffId:'staff-1',discountLevel:2,reductionBps:2000,payRateBps:8000,policyId:'staff-grades',policyVersion:3};
 return {...q,totalCents:1600,discountCents:400,selectedEligibleSubtotalCents:2000,selectedPromotionType:'STAFF_GRADE' as const,staffDiscountCandidate:{...terms,discountCents:400,computedAt:new Date().toISOString()},discounts:[{kind:'STAFF_GRADE' as const,...terms,amountCents:400,description:'员工2级，减免20%'}],discountAllocations:[{lineKey:'a',relatedId:'a',amountCents:280},{lineKey:'photo',relatedId:'photo',amountCents:100},{lineKey:'binding',relatedId:'booklet',amountCents:20}]};
}
it('accepts server employee whole-order discounts including binding',()=>{
 expect(()=>assertQuote(staffQuote())).not.toThrow();
});
it('rejects invalid employee percentage and grade snapshots',()=>{
 const q=staffQuote();
 expect(()=>assertQuote({...q,discounts:[{...q.discounts[0],discountLevel:7}]})).toThrow();
 expect(()=>assertQuote({...q,discounts:[{...q.discounts[0],payRateBps:12000}]})).toThrow();
 expect(()=>assertQuote({...q,checkoutAccountId:null})).toThrow();
});
