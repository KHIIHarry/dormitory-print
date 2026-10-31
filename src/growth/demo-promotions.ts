import type {
  Campaign,
  Coupon,
  CustomerAccount,
  PriceLine,
  PromotionRecord,
  Quote,
  ReferralRecord,
} from "./types";

/** Metadata belongs to the demo ledger, not to the public coupon DTO. */
export type PromotionCoupon = Coupon & {
  accountId?: string;
  campaignVersion?: number;
  sourceKey?: string;
  issuedAt?: string;
  realizedDiscountCents?: number;
};
export type PromotionReferral = ReferralRecord & {
  inviteeAccountId?: string;
  referrerAccountId?: string;
  qualifiedOrderId?: string;
  qualifiedCampaignIds?: string[];
  qualificationSequence?: number;
  /** Immutable copies of eligible REFERRER campaigns at invitation verification. */
  rewardCampaigns?: Campaign[];
  qualificationExpiresAt?: string | null;
  qualificationMinSpendCents?: number;
  qualificationService?: Campaign["service"];
};
export type PromotionOrder = {
  id: string;
  status: string;
  checkoutAccountId?: string | null;
  checkoutAuthKind?: string;
  checkoutAccountVerified?: boolean;
  settlement?: {
    settlementKind: string;
    cashReceivedCents: number;
    discountCents?: number;
  };
  quote?: Pick<Quote, "lines" | "discounts">;
  deliveredAt?: string;
  proof?: { deliveredAt?: string };
};
export type DemoPromotionState = {
  campaigns: Campaign[];
  coupons: PromotionCoupon[];
  referrals: PromotionReferral[];
  accounts: Pick<
    CustomerAccount,
    "id" | "verified" | "status" | "emailMasked"
  >[];
  records?: PromotionRecord[];
  orders?: PromotionOrder[] | Map<string, PromotionOrder>;
  /** Set in tests or event adapters; otherwise the current wall clock is used. */
  now?: string;
};
export type GrantSkipReason =
  | "NO_ACCOUNT"
  | "ACCOUNT_NOT_ELIGIBLE"
  | "INVALID_RULES"
  | "DISABLED"
  | "OUTSIDE_WINDOW"
  | "ALREADY_GRANTED"
  | "PER_ACCOUNT_LIMIT"
  | "ISSUANCE_LIMIT"
  | "BUDGET_EXHAUSTED";
export type GrantResult = {
  granted: PromotionCoupon[];
  alreadyGranted: PromotionCoupon[];
  skipped: { campaignId: string; reason: GrantSkipReason }[];
};
const cents = (value: unknown): value is number =>
  typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
function time(state: DemoPromotionState) {
  const value = state.now ? Date.parse(state.now) : Date.now();
  if (!Number.isFinite(value)) throw new Error("演示事件时间无效");
  return value;
}
function sum(values: number[]) {
  const result = values.reduce((total, value) => total + BigInt(value), 0n);
  if (result > BigInt(Number.MAX_SAFE_INTEGER))
    throw new Error("运营金额超出整数分安全范围");
  return Number(result);
}
function inWindow(campaign: Campaign, now: number) {
  const start = campaign.startsAt ? Date.parse(campaign.startsAt) : -Infinity;
  const end = campaign.endsAt ? Date.parse(campaign.endsAt) : Infinity;
  return (
    !Number.isNaN(start) && !Number.isNaN(end) && start <= now && now < end
  );
}
function validRules(campaign: Campaign) {
  return (
    !!campaign.id &&
    Number.isSafeInteger(campaign.version) &&
    campaign.version > 0 &&
    cents(campaign.amountCents) &&
    campaign.amountCents > 0 &&
    cents(campaign.minSpendCents) &&
    ["A4", "PHOTO", "ALL"].includes(campaign.service) &&
    campaign.stackable === false &&
    Number.isSafeInteger(campaign.perAccountLimit) &&
    campaign.perAccountLimit >= 0 &&
    (campaign.budgetCents == null || cents(campaign.budgetCents)) &&
    (campaign.issuanceLimit == null || cents(campaign.issuanceLimit)) &&
    (campaign.validityDays == null ||
      (Number.isSafeInteger(campaign.validityDays) &&
        campaign.validityDays > 0))
  );
}
function expired(coupon: PromotionCoupon, now: number) {
  return (
    coupon.status === "EXPIRED" ||
    (coupon.status === "AVAILABLE" &&
      !!coupon.validUntil &&
      Date.parse(coupon.validUntil) <= now)
  );
}
function liability(coupon: PromotionCoupon, now: number) {
  // Reserved and frozen promises survive natural expiry and ordinary campaign stops.
  return coupon.status === "RESERVED" ||
    (coupon.status === "AVAILABLE" && !expired(coupon, now))
    ? coupon.amountCents
    : 0;
}
function orderList(state: DemoPromotionState) {
  return state.orders instanceof Map
    ? [...state.orders.values()]
    : state.orders || [];
}
function realizedFromOrders(state: DemoPromotionState) {
  const coupon = new Map<string, number>();
  const volume = new Map<string, number>();
  for (const order of orderList(state)) {
    if (!order.settlement || !order.quote) continue;
    for (const discount of order.quote.discounts) {
      if (!cents(discount.amountCents)) continue;
      if (discount.kind === "COUPON")
        coupon.set(discount.couponGrantId, discount.amountCents);
      else if (discount.kind === "VOLUME_TIER")
        volume.set(order.id, discount.amountCents);
    }
  }
  return { coupon, volume };
}
function usedValue(coupon: PromotionCoupon, known: Map<string, number>) {
  if (coupon.status !== "USED") return { known: 0, unresolved: 0 };
  const amount =
    coupon.realizedDiscountCents ?? known.get(coupon.couponGrantId);
  return cents(amount) && amount <= coupon.amountCents
    ? { known: amount, unresolved: 0 }
    : { known: 0, unresolved: coupon.amountCents };
}
function campaignBudget(
  state: DemoPromotionState,
  campaignId: string,
  now: number,
) {
  const known = realizedFromOrders(state).coupon;
  return sum(
    state.coupons
      .filter((c) => c.campaignId === campaignId)
      .map((c) => {
        const used = usedValue(c, known);
        return sum([liability(c, now), used.known, used.unresolved]);
      }),
  );
}
function grantSelected(
  state: DemoPromotionState,
  accountId: string,
  campaigns: Campaign[],
  source: string,
  promised = false,
): GrantResult {
  const result: GrantResult = { granted: [], alreadyGranted: [], skipped: [] };
  const now = time(state),
    account = state.accounts.find((a) => a.id === accountId);
  for (const campaign of [...campaigns].sort((a, b) =>
    a.id.localeCompare(b.id),
  )) {
    const skip = (reason: GrantSkipReason) =>
      result.skipped.push({ campaignId: campaign.id, reason });
    const key = `${source}:${campaign.id}:${accountId}`;
    const already = state.coupons.find((c) => c.sourceKey === key);
    if (already) {
      result.alreadyGranted.push(already);
      skip("ALREADY_GRANTED");
      continue;
    }
    if (!account) {
      skip("NO_ACCOUNT");
      continue;
    }
    if (!account.verified || account.status !== "ACTIVE") {
      skip("ACCOUNT_NOT_ELIGIBLE");
      continue;
    }
    // Face amount and coupon rules remain frozen, mutable limits come from current config.
    const limits = promised
      ? state.campaigns.find((c) => c.id === campaign.id) || campaign
      : campaign;
    if (!validRules(campaign) || !validRules(limits)) {
      skip("INVALID_RULES");
      continue;
    }
    if (!promised && !campaign.enabled) {
      skip("DISABLED");
      continue;
    }
    if (!promised && !inWindow(campaign, now)) {
      skip("OUTSIDE_WINDOW");
      continue;
    }
    const issued = state.coupons.filter((c) => c.campaignId === campaign.id);
    if (
      issued.filter((c) => c.accountId === accountId).length >=
      campaign.perAccountLimit
    ) {
      skip("PER_ACCOUNT_LIMIT");
      continue;
    }
    if (
      limits.issuanceLimit != null &&
      issued.length >= limits.issuanceLimit
    ) {
      skip("ISSUANCE_LIMIT");
      continue;
    }
    if (
      limits.budgetCents != null &&
      BigInt(campaignBudget(state, campaign.id, now)) +
        BigInt(campaign.amountCents) >
        BigInt(limits.budgetCents)
    ) {
      skip("BUDGET_EXHAUSTED");
      continue;
    }
    const validUntil =
      campaign.validityDays == null
        ? null
        : new Date(now + campaign.validityDays * 86400000).toISOString();
    const id = crypto.randomUUID();
    const coupon: PromotionCoupon = {
      id,
      couponGrantId: id,
      campaignId: campaign.id,
      name: campaign.name,
      amountCents: campaign.amountCents,
      minSpendCents: campaign.minSpendCents,
      service: campaign.service,
      status: "AVAILABLE",
      riskHold: "NONE",
      validUntil,
      version: 1,
      accountId,
      campaignVersion: campaign.version,
      sourceKey: key,
      issuedAt: new Date(now).toISOString(),
    };
    state.coupons.push(coupon);
    state.records?.unshift({
      id: crypto.randomUUID(),
      accountId,
      campaignId: campaign.id,
      accountMasked: account.emailMasked,
      campaignName: campaign.name,
      couponGrantId: id,
      type: "ISSUED",
      status: "AVAILABLE",
      riskHold: "NONE",
      amountCents: coupon.amountCents,
      reason:
        campaign.kind === "REFERRER"
          ? "邀请首单达标奖励"
          : campaign.kind === "INVITEE"
            ? "受邀账号验证奖励"
            : "新客账号验证奖励",
      time: coupon.issuedAt!,
      version: 1,
    });
    result.granted.push(coupon);
  }
  return result;
}
/** One verification event grants each configured kind at most once per account/campaign. */
export function grantCampaignCoupons(
  state: DemoPromotionState,
  accountId: string,
  kind: Campaign["kind"],
): GrantResult {
  return grantSelected(
    state,
    accountId,
    state.campaigns.filter((c) => c.kind === kind),
    `ACCOUNT_${kind}`,
  );
}
export type QualificationResult = {
  qualified: boolean;
  referral: PromotionReferral | null;
  granted: PromotionCoupon[];
  reason: string;
  skipped?: GrantResult["skipped"];
};
function eligibleAmount(
  lines: PriceLine[] | undefined,
  service: Campaign["service"],
  fallback: number,
) {
  if (!lines) return service === "ALL" ? fallback : 0;
  return sum(
    lines
      .filter((line) => service === "ALL" || line.service === service)
      .map((line) => (cents(line.amountCents) ? line.amountCents : 0)),
  );
}
/** Call synchronously in delivery commit order. A retry cannot replace qualifiedOrderId. */
export function qualifyReferral(
  state: DemoPromotionState,
  order: PromotionOrder,
): QualificationResult {
  const fail = (
    reason: string,
    referral: PromotionReferral | null = null,
  ): QualificationResult => ({
    qualified: false,
    referral,
    granted: [],
    reason,
  });
  const accountId = order.checkoutAccountId;
  if (
    order.status !== "DELIVERED" ||
    order.checkoutAuthKind !== "REGISTERED" ||
    !accountId ||
    order.checkoutAccountVerified === false ||
    order.settlement?.settlementKind !== "CASH_MANUAL" ||
    !cents(order.settlement.cashReceivedCents) ||
    order.settlement.cashReceivedCents <= 0
  )
    return fail("ORDER_NOT_ELIGIBLE");
  const relations = state.referrals.filter(
    (r) => r.inviteeAccountId === accountId,
  );
  const previouslyQualified = relations.find((r) => !!r.qualifiedOrderId);
  const relation =
    previouslyQualified ||
    relations.find((r) => r.status === "WAITING_FIRST_ORDER");
  if (!relation) return fail("NO_INVITATION");
  if (relation.qualifiedOrderId && relation.qualifiedOrderId !== order.id)
    return fail("FIRST_ORDER_ALREADY_LOCKED", relation);
  if (["GRANTED", "DISQUALIFIED", "REVERSAL_REVIEW"].includes(relation.status))
    return {
      qualified: !!relation.qualifiedOrderId,
      referral: relation,
      granted: [],
      reason: "ALREADY_PROCESSED",
    };
  if (!relation.referrerAccountId || relation.referrerAccountId === accountId)
    return fail("INVALID_INVITATION", relation);
  const registeredAt = Date.parse(relation.createdAt);
  let snapshots = (relation.rewardCampaigns || []).filter(
    (c) =>
      c.kind === "REFERRER" &&
      c.enabled &&
      validRules(c) &&
      inWindow(c, registeredAt),
  );
  if (!snapshots.length) return fail("NO_PROMISED_REWARD", relation);
  if (!relation.qualifiedOrderId) {
    const delivered = order.deliveredAt || order.proof?.deliveredAt;
    const deliveredTime = delivered ? Date.parse(delivered) : time(state);
    const expires = relation.qualificationExpiresAt
      ? Date.parse(relation.qualificationExpiresAt)
      : Infinity;
    if (
      !Number.isFinite(deliveredTime) ||
      Number.isNaN(expires) ||
      deliveredTime < registeredAt ||
      deliveredTime > expires
    )
      return fail("QUALIFICATION_EXPIRED", relation);
    const minimum = relation.qualificationMinSpendCents ?? 0;
    const eligible = eligibleAmount(
      order.quote?.lines,
      relation.qualificationService || "ALL",
      order.settlement.cashReceivedCents,
    );
    if (!cents(minimum) || eligible <= 0 || eligible < minimum)
      return fail("MERCHANDISE_THRESHOLD", relation);
    snapshots = snapshots.filter((c) => {
      const deadline = c.qualificationExpiresAt
        ? Date.parse(c.qualificationExpiresAt)
        : Infinity;
      const amount = eligibleAmount(
        order.quote?.lines,
        c.qualificationService || "ALL",
        order.settlement!.cashReceivedCents,
      );
      return (
        deliveredTime <= deadline &&
        amount > 0 &&
        amount >= (c.qualificationMinSpendCents ?? 0)
      );
    });
    if (!snapshots.length) return fail("CAMPAIGN_QUALIFICATION", relation);
    relation.qualifiedCampaignIds = snapshots.map((c) => c.id);
    relation.qualifiedOrderId = order.id;
    relation.qualifiedAt = new Date(deliveredTime).toISOString();
    relation.qualificationSequence =
      Math.max(0, ...state.referrals.map((r) => r.qualificationSequence || 0)) +
      1;
    relation.status = "QUALIFIED";
  }
  snapshots = snapshots.filter(
    (c) =>
      !relation.qualifiedCampaignIds ||
      relation.qualifiedCampaignIds.includes(c.id),
  );
  const result = grantSelected(
    state,
    relation.referrerAccountId,
    snapshots,
    `REFERRAL_${relation.id}`,
    true,
  );
  const complete =
    result.granted.length + result.alreadyGranted.length === snapshots.length;
  const funding = result.skipped.some(
    (s) => s.reason === "BUDGET_EXHAUSTED" || s.reason === "ISSUANCE_LIMIT",
  );
  relation.status = complete
    ? "GRANTED"
    : funding
      ? "PENDING_BUDGET"
      : result.skipped.some((s) => s.reason === "PER_ACCOUNT_LIMIT")
        ? "DISQUALIFIED"
        : "QUALIFIED";
  relation.rewardText = complete
    ? "邀请奖励已发放，请查看对应优惠券。"
    : funding
      ? "首单已达标，奖励额度不足，等待按同一资格补发。"
      : relation.status === "DISQUALIFIED"
        ? "已达到活动规定的账号奖励次数上限。"
        : "首单已达标，等待奖励资格核对。";
  return {
    qualified: true,
    referral: relation,
    granted: result.granted,
    reason: relation.status,
    skipped: result.skipped,
  };
}
export function promotionSummary(state: DemoPromotionState) {
  const now = time(state),
    realized = realizedFromOrders(state);
  const used = state.coupons.map((c) => usedValue(c, realized.coupon));
  const knownIds = new Set(
    state.coupons
      .filter((c) => c.status === "USED")
      .map((c) => c.couponGrantId),
  );
  const orphanedOrderAmounts = [...realized.coupon.entries()]
    .filter(([id]) => !knownIds.has(id))
    .map(([, amount]) => amount);
  const couponDiscountCents = sum([
    ...used.map((v) => v.known),
    ...orphanedOrderAmounts,
  ]);
  const volumeDiscountCents = sum([...realized.volume.values()]);
  const reservedLiabilityCents = sum(
    state.coupons.map((c) => liability(c, now)),
  );
  const unresolvedUsedFaceCents = sum(used.map((v) => v.unresolved));
  return {
    issued: state.coupons.length,
    available: state.coupons.filter(
      (c) => c.status === "AVAILABLE" && !expired(c, now),
    ).length,
    reserved: state.coupons.filter((c) => c.status === "RESERVED").length,
    used: state.coupons.filter((c) => c.status === "USED").length,
    expired: state.coupons.filter((c) => expired(c, now)).length,
    revoked: state.coupons.filter((c) => c.status === "REVOKED").length,
    frozen: state.coupons.filter((c) => c.riskHold === "FROZEN").length,
    reservedLiabilityCents,
    couponDiscountCents,
    volumeDiscountCents,
    realizedDiscountCents: sum([couponDiscountCents, volumeDiscountCents]),
    unresolvedUsedCount: used.filter((v) => v.unresolved > 0).length,
    unresolvedUsedFaceCents,
    newCustomers: state.accounts.length,
    verifiedCustomers: state.accounts.filter((a) => a.verified).length,
    campaigns: state.campaigns.map((c) => ({
      campaignId: c.id,
      issued: state.coupons.filter((x) => x.campaignId === c.id).length,
      budgetCents: c.budgetCents,
      committedCents: campaignBudget(state, c.id, now),
      remainingBudgetCents:
        c.budgetCents == null
          ? null
          : Math.max(0, c.budgetCents - campaignBudget(state, c.id, now)),
    })),
  };
}
