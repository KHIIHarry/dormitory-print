import type { components } from "../lib/api-schema";
export type PromotionIntent = components["schemas"]["PromotionIntent"];
export type Coupon = components["schemas"]["Coupon"];
export type VolumePolicy = components["schemas"]["VolumePolicy"];
export type PriceLine = components["schemas"]["PriceLine"];
export type VolumeCandidate = {
  policyId: string;
  policyVersion: number;
  tierId: string;
  minSheets: number;
  a4Sheets: number;
  a4SubtotalCents: number;
  payRateBps: number;
  discountCents: number;
};
export type Discount =
  | {
      kind: "COUPON";
      couponGrantId: string;
      campaignId: string;
      amountCents: number;
      description: string;
    }
  | {
      kind: "VOLUME_TIER";
      policyId: string;
      policyVersion: number;
      tierId: string;
      a4Sheets: number;
      payRateBps: number;
      amountCents: number;
      description: string;
    };
export type Quote = components["schemas"]["Quote"];
export type CustomerAccount = components["schemas"]["CustomerAccount"];
export type CustomerSession = components["schemas"]["CustomerSession"];
export type StaffSession = components["schemas"]["StaffSession"];
export type OrderAccess = components["schemas"]["OrderAccess"];
export type AccessLink = components["schemas"]["AccessLink"];
export type MailConfig = components["schemas"]["MailConfig"];
export type MailTemplate = components["schemas"]["MailTemplate"];
export type Campaign = components["schemas"]["Campaign"];
export type ReferralRecord = {
  id: string;
  inviteeLabel: string;
  status:
    | "WAITING_FIRST_ORDER"
    | "QUALIFIED"
    | "PENDING_BUDGET"
    | "GRANTED"
    | "DISQUALIFIED"
    | "REVERSAL_REVIEW";
  createdAt: string;
  qualifiedAt?: string;
  rewardText: string;
};
export type SessionDevice = {
  id: string;
  device: string;
  lastSeenAt: string;
  current: boolean;
};
export type CustomerRecord = CustomerAccount & {
  createdAt: string;
  orderCount: number;
  couponCount: number;
  inviteCode: string;
  referralCount: number;
  allowedActions: string[];
  orders: {
    id: string;
    pickupCode: string;
    totalCents: number;
    status: string;
  }[];
};
export type PromotionRecord = {
  campaignId?: string;
  accountId?: string;
  id: string;
  accountMasked: string;
  campaignName: string;
  couponGrantId?: string;
  type: string;
  status: string;
  riskHold?: string;
  amountCents: number;
  reason: string;
  time: string;
  version: number;
};
