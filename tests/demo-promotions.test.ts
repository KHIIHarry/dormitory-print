import { describe, expect, it } from "vitest";
import {
  grantCampaignCoupons,
  qualifyReferral,
  promotionSummary,
  type DemoPromotionState,
  type PromotionReferral,
  type PromotionOrder,
} from "../src/growth/demo-promotions";
import type { Campaign } from "../src/growth/types";
const now = "2026-10-29T12:00:00.000Z";
function campaign(change: Partial<Campaign> = {}): Campaign {
  return {
    id: "campaign",
    version: 2,
    name: "活动",
    kind: "NEW_CUSTOMER",
    enabled: true,
    amountCents: 100,
    minSpendCents: 0,
    service: "ALL",
    validityDays: 7,
    budgetCents: null,
    issuanceLimit: null,
    perAccountLimit: 1,
    startsAt: null,
    endsAt: null,
    description: "",
    stackable: false,
    qualificationExpiresAt: null,
    qualificationMinSpendCents: 0,
    qualificationService: "ALL",
    ...change,
  };
}
function state(campaigns: Campaign[] = []): DemoPromotionState {
  return {
    now,
    campaigns,
    coupons: [],
    referrals: [],
    records: [],
    accounts: ["new", "referrer", "other"].map((id) => ({
      id,
      verified: true,
      status: "ACTIVE",
      emailMasked: "x***@example.invalid",
    })),
  };
}
function relation(
  rewards: Campaign[] = [campaign({ kind: "REFERRER" })],
): PromotionReferral {
  return {
    id: "referral",
    inviteeAccountId: "new",
    referrerAccountId: "referrer",
    inviteeLabel: "新***",
    status: "WAITING_FIRST_ORDER",
    createdAt: "2026-10-01T00:00:00.000Z",
    rewardText: "以已确认规则为准",
    rewardCampaigns: structuredClone(rewards),
  };
}
function order(change: Partial<PromotionOrder> = {}): PromotionOrder {
  return {
    id: "first",
    status: "DELIVERED",
    checkoutAccountId: "new",
    checkoutAuthKind: "REGISTERED",
    settlement: { settlementKind: "CASH_MANUAL", cashReceivedCents: 100 },
    ...change,
  };
}
describe("demo campaign issuance", () => {
  it("does not invent coupons without eligible campaigns or verified active recipients", () => {
    const s = state();
    expect(grantCampaignCoupons(s, "new", "NEW_CUSTOMER").granted).toHaveLength(
      0,
    );
    s.campaigns.push(campaign());
    s.accounts[0].verified = false;
    expect(grantCampaignCoupons(s, "new", "NEW_CUSTOMER").granted).toHaveLength(
      0,
    );
    s.accounts[0].verified = true;
    s.accounts[0].status = "SUSPENDED";
    expect(grantCampaignCoupons(s, "new", "NEW_CUSTOMER").granted).toHaveLength(
      0,
    );
  });
  it("uses start inclusive and end exclusive and skips disabled campaigns", () => {
    const s = state([
      campaign({ id: "started", startsAt: now }),
      campaign({ id: "ended", endsAt: now }),
      campaign({ id: "future", startsAt: "2026-11-01T00:00:00Z" }),
      campaign({ id: "disabled", enabled: false }),
    ]);
    expect(
      grantCampaignCoupons(s, "new", "NEW_CUSTOMER").granted.map(
        (c) => c.campaignId,
      ),
    ).toEqual(["started"]);
  });
  it("issues independent kinds once and preserves an immutable face/rule snapshot", () => {
    const s = state([campaign(), campaign({ id: "invited", kind: "INVITEE" })]);
    const first = grantCampaignCoupons(s, "new", "NEW_CUSTOMER").granted[0];
    expect(first).toMatchObject({
      accountId: "new",
      campaignVersion: 2,
      amountCents: 100,
      status: "AVAILABLE",
      riskHold: "NONE",
    });
    s.campaigns[0].amountCents = 900;
    expect(first.amountCents).toBe(100);
    expect(grantCampaignCoupons(s, "new", "NEW_CUSTOMER").granted).toHaveLength(
      0,
    );
    expect(grantCampaignCoupons(s, "new", "INVITEE").granted).toHaveLength(1);
  });
  it("enforces budget face liability across accounts and does not release frozen or expired reserved obligations", () => {
    const s = state([campaign({ budgetCents: 100 })]);
    grantCampaignCoupons(s, "new", "NEW_CUSTOMER");
    const held = s.coupons[0];
    held.status = "RESERVED";
    held.riskHold = "FROZEN";
    held.validUntil = "2000-01-01T00:00:00Z";
    expect(
      grantCampaignCoupons(s, "other", "NEW_CUSTOMER").skipped[0].reason,
    ).toBe("BUDGET_EXHAUSTED");
    held.status = "EXPIRED";
    expect(
      grantCampaignCoupons(s, "other", "NEW_CUSTOMER").granted,
    ).toHaveLength(1);
  });
  it("enforces global issuance and per-account limits independently of released budget", () => {
    const s = state([campaign({ issuanceLimit: 1 })]);
    grantCampaignCoupons(s, "new", "NEW_CUSTOMER");
    s.coupons[0].status = "REVOKED";
    expect(
      grantCampaignCoupons(s, "other", "NEW_CUSTOMER").skipped[0].reason,
    ).toBe("ISSUANCE_LIMIT");
    const t = state([campaign({ perAccountLimit: 0 })]);
    expect(grantCampaignCoupons(t, "new", "NEW_CUSTOMER").granted).toHaveLength(
      0,
    );
  });
  it("accounts for actual used discounts and releases unused face value", () => {
    const s = state([campaign({ amountCents: 100, budgetCents: 150 })]);
    grantCampaignCoupons(s, "new", "NEW_CUSTOMER");
    s.coupons[0].status = "USED";
    s.coupons[0].realizedDiscountCents = 50;
    expect(
      grantCampaignCoupons(s, "other", "NEW_CUSTOMER").granted,
    ).toHaveLength(1);
    expect(promotionSummary(s)).toMatchObject({
      issued: 2,
      used: 1,
      reservedLiabilityCents: 100,
      couponDiscountCents: 50,
    });
  });
});
describe("demo referral qualification", () => {
  it("matches the invitee relation instead of the first unrelated row and rewards only the referrer", () => {
    const c = campaign({ kind: "REFERRER" }),
      s = state([c]);
    s.referrals.push(
      { ...relation([c]), id: "other-relation", inviteeAccountId: "other" },
      relation([c]),
    );
    const result = qualifyReferral(s, order());
    expect(result.qualified).toBe(true);
    expect(s.referrals[0].status).toBe("WAITING_FIRST_ORDER");
    expect(s.referrals[1]).toMatchObject({
      qualifiedOrderId: "first",
      status: "GRANTED",
    });
    expect(s.coupons.map((c) => c.accountId)).toEqual(["referrer"]);
  });
  it("rejects anonymous/imported, zero settlement, undelivered and unverified-at-checkout orders", () => {
    for (const change of [
      { checkoutAuthKind: "ANONYMOUS" },
      { checkoutAuthKind: "ORDER_ACCESS" },
      { checkoutAccountVerified: false },
      { status: "DELIVERING" },
      { settlement: { settlementKind: "ZERO_PAYABLE", cashReceivedCents: 0 } },
    ]) {
      const c = campaign({ kind: "REFERRER" }),
        s = state([c]);
      s.referrals.push(relation([c]));
      expect(qualifyReferral(s, order(change)).qualified).toBe(false);
      expect(s.coupons).toHaveLength(0);
    }
  });
  it("locks the first delivered order, retries insufficient budget and never duplicates rewards", () => {
    const c = campaign({ kind: "REFERRER", budgetCents: 0 }),
      s = state([c]);
    s.referrals.push(relation([c]));
    qualifyReferral(s, order());
    expect(s.referrals[0]).toMatchObject({
      qualifiedOrderId: "first",
      status: "PENDING_BUDGET",
    });
    s.campaigns[0].budgetCents = 100;
    qualifyReferral(s, order({ id: "second" }));
    expect(s.coupons).toHaveLength(0);
    qualifyReferral(s, order());
    qualifyReferral(s, order());
    expect(s.coupons).toHaveLength(1);
    expect(s.referrals[0].status).toBe("GRANTED");
  });
  it("keeps promised snapshots after ordinary campaign stop but does not reward newly enabled unsnapshotted activity", () => {
    const c = campaign({ kind: "REFERRER" }),
      s = state([{ ...c, enabled: false, amountCents: 900 }]);
    s.referrals.push(relation([c]));
    qualifyReferral(s, order());
    expect(s.coupons[0].amountCents).toBe(100);
    const none = state([c]);
    none.referrals.push({ ...relation(), rewardCampaigns: [] });
    expect(qualifyReferral(none, order()).qualified).toBe(false);
    expect(none.coupons).toHaveLength(0);
  });
  it("checks referral deadline and selected merchandise threshold", () => {
    const c = campaign({ kind: "REFERRER" }),
      s = state([c]);
    s.referrals.push({
      ...relation([c]),
      qualificationExpiresAt: "2026-10-20T00:00:00Z",
    });
    expect(qualifyReferral(s, order()).qualified).toBe(false);
    const t = state([c]);
    t.referrals.push({
      ...relation([c]),
      qualificationService: "A4",
      qualificationMinSpendCents: 200,
    });
    expect(qualifyReferral(t, order()).qualified).toBe(false);
  });
  it("does not reuse an existing qualified invitee through a duplicate relation", () => {
    const c = campaign({ kind: "REFERRER" }),
      s = state([c]);
    s.referrals.push(relation([c]));
    qualifyReferral(s, order());
    s.referrals.push({ ...relation([c]), id: "duplicate" });
    qualifyReferral(s, order({ id: "later" }));
    expect(s.coupons).toHaveLength(1);
    expect(s.referrals[1].status).toBe("WAITING_FIRST_ORDER");
  });
});
describe("promotion summary", () => {
  it("keeps coupon and volume realized amounts distinct and counts only settled snapshots", () => {
    const s = state([campaign()]);
    grantCampaignCoupons(s, "new", "NEW_CUSTOMER");
    s.coupons[0].status = "USED";
    const grantId = s.coupons[0].couponGrantId;
    s.orders = [
      order({
        quote: {
          lines: [],
          discounts: [
            {
              kind: "COUPON",
              couponGrantId: grantId,
              campaignId: "campaign",
              amountCents: 60,
              description: "券",
            },
          ],
        },
      }),
      order({
        id: "volume",
        quote: {
          lines: [],
          discounts: [
            {
              kind: "VOLUME_TIER",
              policyId: "p",
              policyVersion: 1,
              tierId: "t",
              a4Sheets: 10,
              payRateBps: 9000,
              amountCents: 40,
              description: "满量",
            },
          ],
        },
      }),
      order({
        id: "unsettled",
        settlement: undefined,
        quote: {
          lines: [],
          discounts: [
            {
              kind: "VOLUME_TIER",
              policyId: "p",
              policyVersion: 1,
              tierId: "t",
              a4Sheets: 10,
              payRateBps: 9000,
              amountCents: 999,
              description: "待确认",
            },
          ],
        },
      }),
    ];
    expect(promotionSummary(s)).toMatchObject({
      couponDiscountCents: 60,
      volumeDiscountCents: 40,
      realizedDiscountCents: 100,
      reservedLiabilityCents: 0,
      unresolvedUsedCount: 0,
    });
  });
});

describe("promotion ledger edge cases", () => {
  it("retains unknown USED face exposure for budget without inventing realized statistics", () => {
    const s = state([campaign({ budgetCents: 150 })]);
    grantCampaignCoupons(s, "new", "NEW_CUSTOMER");
    s.coupons[0].status = "USED";
    expect(
      grantCampaignCoupons(s, "other", "NEW_CUSTOMER").granted,
    ).toHaveLength(0);
    expect(promotionSummary(s)).toMatchObject({
      couponDiscountCents: 0,
      unresolvedUsedCount: 1,
      unresolvedUsedFaceCents: 100,
    });
  });
  it("retries partial multi-campaign referral rewards without issuing the completed campaign twice", () => {
    const a = campaign({ id: "a", kind: "REFERRER", budgetCents: 100 }),
      b = campaign({ id: "b", kind: "REFERRER", budgetCents: 0 });
    const s = state([a, b]);
    s.referrals.push(relation([a, b]));
    qualifyReferral(s, order());
    expect(s.coupons.map((c) => c.campaignId)).toEqual(["a"]);
    expect(s.referrals[0].status).toBe("PENDING_BUDGET");
    s.campaigns[1].budgetCents = 100;
    qualifyReferral(s, order());
    expect(s.coupons.map((c) => c.campaignId)).toEqual(["a", "b"]);
    expect(s.referrals[0].status).toBe("GRANTED");
  });
  it("counts per-account rewards across separate legitimate referral relations", () => {
    const c = campaign({ kind: "REFERRER", perAccountLimit: 1 }),
      s = state([c]);
    s.referrals.push(relation([c]), {
      ...relation([c]),
      id: "second-relation",
      inviteeAccountId: "other",
    });
    qualifyReferral(s, order());
    qualifyReferral(
      s,
      order({ id: "other-order", checkoutAccountId: "other" }),
    );
    expect(s.coupons).toHaveLength(1);
    expect(s.referrals[1].status).toBe("DISQUALIFIED");
  });
  it("uses the committed delivery time rather than delayed processing time for a qualification deadline", () => {
    const c = campaign({ kind: "REFERRER" }),
      s = state([c]);
    s.referrals.push({
      ...relation([c]),
      qualificationExpiresAt: "2026-10-20T00:00:00Z",
    });
    expect(
      qualifyReferral(
        s,
        order({ proof: { deliveredAt: "2026-10-19T00:00:00Z" } }),
      ).qualified,
    ).toBe(true);
  });
  it("never turns a previously empty reward snapshot into a later reward promise", () => {
    const s = state();
    s.referrals.push({ ...relation(), rewardCampaigns: [] });
    s.campaigns.push(campaign({ kind: "REFERRER" }));
    qualifyReferral(s, order());
    expect(s.referrals[0].qualifiedOrderId).toBeUndefined();
    expect(s.coupons).toHaveLength(0);
  });
});

it('邀请资格沿用活动快照中的独立门槛、服务和截止，不按奖励券使用门槛推断',()=>{
 const promised=campaign({kind:'REFERRER',qualificationExpiresAt:'2026-11-01T00:00:00Z',qualificationMinSpendCents:150,qualificationService:'A4',minSpendCents:999});
 const s=state([promised]);s.referrals.push(relation([promised]));
 const o=order({quote:{lines:[{key:'f',relatedId:'f',service:'A4',unit:'SHEET',quantity:10,unitPriceCents:10,amountCents:100}]} as any});
 expect(qualifyReferral(s,o).qualified).toBe(false);
 o.quote!.lines[0]={...o.quote!.lines[0],quantity:20,amountCents:200};
 s.campaigns[0].qualificationMinSpendCents=9999;
 expect(qualifyReferral(s,o).qualified).toBe(true);
 expect(s.referrals[0].qualifiedCampaignIds).toEqual(['campaign']);
 const expired=state([promised]);expired.now='2026-11-02T00:00:00Z';expired.referrals.push(relation([promised]));
 expect(qualifyReferral(expired,order({id:'late',deliveredAt:expired.now,quote:o.quote})).qualified).toBe(false);
});
