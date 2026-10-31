"""Strict DTOs for the growth/promotion/customer surface.

Field names and aliases follow docs/openapi.yaml exactly; unexpected client
fields are rejected by the shared DTO base.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import Field as PField, model_validator

from .core import DTO

SelectionMode = Literal['AUTO', 'NO_COUPON', 'SPECIFIC_COUPON']
PromotionType = Literal['NONE', 'COUPON', 'VOLUME_TIER', 'STAFF_GRADE']
Service = Literal['A4', 'PHOTO', 'BINDING', 'ALL']
CatalogService = Literal['A4', 'PHOTO', 'ALL']


class PromotionIntent(DTO):
    selection_mode: SelectionMode | None = None
    coupon_grant_id: str | None = None

    @model_validator(mode='after')
    def normalize_mode(self):
        if self.selection_mode is None:
            if self.coupon_grant_id is not None:
                self.selection_mode = 'SPECIFIC_COUPON'
            elif 'coupon_grant_id' in self.model_fields_set:
                self.selection_mode = 'NO_COUPON'
        if self.selection_mode == 'SPECIFIC_COUPON' and not self.coupon_grant_id:
            raise ValueError('指定用券时必须提供 couponGrantId')
        if self.selection_mode == 'NO_COUPON':
            self.coupon_grant_id = None
        return self


class RequoteInput(DTO):
    promotion_intent: PromotionIntent | None = None


class PriceLineDTO(DTO):
    key: str = PField(min_length=1, max_length=120)
    related_id: str = PField(min_length=1, max_length=120)
    unit: Literal['SHEET', 'BOOKLET']
    quantity: int = PField(ge=0, le=10_000_000)
    unit_price_cents: int = PField(ge=0, le=10_000_000_000)
    amount_cents: int = PField(ge=0, le=10_000_000_000)
    service: Service

    @model_validator(mode='after')
    def amount_matches_quantity(self):
        if self.amount_cents != self.quantity * self.unit_price_cents:
            raise ValueError('金额必须等于数量乘以单价')
        return self


class DiscountAllocation(DTO):
    line_key: str
    related_id: str
    amount_cents: int = PField(ge=0)


class VolumeCandidateDTO(DTO):
    policy_id: str
    policy_version: int = PField(ge=0)
    tier_id: str
    min_sheets: int = PField(ge=0)
    a4_sheets: int = PField(ge=0)
    a4_subtotal_cents: int = PField(ge=0)
    pay_rate_bps: int = PField(ge=0, le=10000)
    discount_cents: int = PField(ge=0)


class CouponDiscountDTO(DTO):
    kind: Literal['COUPON']
    coupon_grant_id: str
    campaign_id: str
    amount_cents: int = PField(ge=0)
    description: str


class VolumeDiscountDTO(DTO):
    kind: Literal['VOLUME_TIER']
    policy_id: str
    policy_version: int = PField(ge=0)
    tier_id: str
    a4_sheets: int = PField(ge=0)
    pay_rate_bps: int = PField(ge=0)
    amount_cents: int = PField(ge=0)
    description: str


class StaffDiscountCandidateDTO(DTO):
    source: Literal['STAFF_GRADE', 'SUPER_ADMIN']
    staff_id: str
    discount_level: int | None
    reduction_bps: int = PField(ge=0, le=10000)
    pay_rate_bps: int = PField(ge=0, le=10000)
    discount_cents: int = PField(ge=0)
    policy_id: str
    policy_version: int = PField(ge=0)
    computed_at: datetime


class StaffGradeDiscountDTO(DTO):
    kind: Literal['STAFF_GRADE']
    source: Literal['STAFF_GRADE', 'SUPER_ADMIN']
    staff_id: str
    discount_level: int | None
    reduction_bps: int = PField(ge=0, le=10000)
    pay_rate_bps: int = PField(ge=0, le=10000)
    policy_id: str
    policy_version: int = PField(ge=0)
    amount_cents: int = PField(ge=0)
    description: str


DiscountApplied = Annotated[
    Union[CouponDiscountDTO, VolumeDiscountDTO, StaffGradeDiscountDTO],
    PField(discriminator='kind'),
]


class QuoteDTO(DTO):
    id: str
    price_version: int = PField(ge=0)
    expires_at: datetime
    lines: list[PriceLineDTO]
    subtotal_cents: int = PField(ge=0)
    discount_cents: int = PField(ge=0)
    total_cents: int = PField(ge=0)
    delivery_fee_cents: Literal[0] = 0
    checkout_account_id: str | None
    selected_eligible_subtotal_cents: int = PField(ge=0)
    promotion_rule_version: str
    selection_mode: SelectionMode
    selected_promotion_type: PromotionType
    volume_discount_candidate: VolumeCandidateDTO | None
    discounts: list[DiscountApplied] = PField(max_length=1)
    discount_allocations: list[DiscountAllocation]
    staff_discount_candidate: StaffDiscountCandidateDTO | None = None

    @model_validator(mode='after')
    def totals_are_consistent(self):
        subtotal = sum(line.amount_cents for line in self.lines)
        if subtotal != self.subtotal_cents:
            raise ValueError('商品小计必须等于各行金额之和')
        if self.total_cents != self.subtotal_cents - self.discount_cents:
            raise ValueError('应付必须等于小计减折扣')
        if not 0 <= self.discount_cents <= self.selected_eligible_subtotal_cents:
            raise ValueError('折扣不能超过适用商品小计')
        if self.selected_eligible_subtotal_cents > self.subtotal_cents:
            raise ValueError('适用商品小计不能大于商品小计')
        if sum(item.amount_cents for item in self.discount_allocations) != self.discount_cents:
            raise ValueError('行分摊合计必须等于折扣金额')
        if self.discount_cents == 0 and self.discounts:
            raise ValueError('无折扣时不得返回折扣明细')
        if self.selected_promotion_type == 'NONE':
            if self.discounts or self.discount_cents:
                raise ValueError('未采用优惠时折扣必须为零')
        elif len(self.discounts) != 1 or self.discounts[0].kind != self.selected_promotion_type:
            raise ValueError('折扣来源与优惠类型不一致')
        if self.selected_promotion_type == 'COUPON' and not self.checkout_account_id:
            raise ValueError('账号券必须有结算账号')
        if self.selected_promotion_type == 'STAFF_GRADE':
            if not self.checkout_account_id:
                raise ValueError('员工折扣必须有结算账号')
            if self.staff_discount_candidate is None:
                raise ValueError('员工折扣缺少快照候选')
            applied = self.discounts[0]
            candidate = self.staff_discount_candidate
            if (applied.source != candidate.source
                    or applied.staff_id != candidate.staff_id
                    or applied.amount_cents != candidate.discount_cents):
                raise ValueError('员工折扣与快照候选不一致')
        return self


class CouponDTO(DTO):
    id: str
    coupon_grant_id: str
    campaign_id: str
    name: str
    amount_cents: int = PField(ge=0)
    min_spend_cents: int = PField(ge=0)
    service: CatalogService
    status: Literal['AVAILABLE', 'RESERVED', 'USED', 'EXPIRED', 'REVOKED']
    risk_hold: Literal['NONE', 'FROZEN']
    valid_until: datetime | None
    reserved_order_id: str | None = None
    reason: str | None = None
    version: int = PField(ge=0)


class CouponList(DTO):
    items: list[CouponDTO]


class VolumeTierDTO(DTO):
    id: str
    min_sheets: int = PField(ge=1)
    pay_rate_bps: int = PField(ge=0, le=10000)


class VolumePolicyDTO(DTO):
    id: str
    version: int = PField(ge=0)
    enabled: bool
    effective_at: datetime | None
    description: str
    tiers: list[VolumeTierDTO]

    @model_validator(mode='after')
    def tiers_are_sane(self):
        seen = set()
        ordered = sorted(self.tiers, key=lambda item: item.min_sheets)
        for index, tier in enumerate(ordered):
            if not tier.id:
                raise ValueError('档位标识不能为空')
            if tier.id in seen:
                raise ValueError('档位标识不能重复')
            seen.add(tier.id)
            if index and ordered[index - 1].min_sheets == tier.min_sheets:
                raise ValueError('张数门槛不能重复')
            if index and ordered[index - 1].pay_rate_bps < tier.pay_rate_bps:
                raise ValueError('更高门槛不能设置更差折扣')
        return self


class VolumePolicyInput(DTO):
    id: str | None = None
    version: int | None = PField(default=None, ge=0)
    enabled: bool
    effective_at: datetime | None
    description: str
    tiers: list[VolumeTierDTO]
    expected_version: int = PField(ge=0)
    reason: str = PField(min_length=1, max_length=500)

    @model_validator(mode='after')
    def tiers_are_sane(self):
        VolumePolicyDTO(
            id=self.id or 'volume-policy', version=self.version or 0,
            enabled=self.enabled, effective_at=self.effective_at,
            description=self.description, tiers=self.tiers,
        )
        return self


class CampaignDTO(DTO):
    id: str
    version: int = PField(ge=0)
    name: str
    kind: Literal['NEW_CUSTOMER', 'INVITEE', 'REFERRER']
    enabled: bool
    amount_cents: int = PField(ge=0)
    min_spend_cents: int = PField(ge=0)
    service: CatalogService
    validity_days: int | None = PField(default=None, ge=1)
    budget_cents: int | None = PField(default=None, ge=0)
    issuance_limit: int | None = PField(default=None, ge=0)
    per_account_limit: int = PField(ge=1)
    starts_at: datetime | None
    ends_at: datetime | None
    description: str
    stackable: Literal[False] = False
    qualification_expires_at: datetime | None
    qualification_min_spend_cents: int = PField(ge=0)
    qualification_service: CatalogService


class CampaignInput(DTO):
    name: str = PField(min_length=1, max_length=120)
    kind: Literal['NEW_CUSTOMER', 'INVITEE', 'REFERRER']
    enabled: bool
    amount_cents: int = PField(ge=1)
    min_spend_cents: int = PField(ge=0)
    service: CatalogService
    validity_days: int | None = PField(default=None, ge=1)
    budget_cents: int | None = PField(default=None, ge=0)
    issuance_limit: int | None = PField(default=None, ge=0)
    per_account_limit: int = PField(ge=1)
    starts_at: datetime | None
    ends_at: datetime | None
    description: str
    stackable: Literal[False] = False
    reason: str = PField(min_length=1, max_length=500)
    qualification_expires_at: datetime | None
    qualification_min_spend_cents: int = PField(ge=0)
    qualification_service: CatalogService

    @model_validator(mode='after')
    def dates(self):
        if self.starts_at and self.ends_at and self.starts_at >= self.ends_at:
            raise ValueError('结束时间必须晚于开始时间')
        return self


class CampaignUpdateInput(CampaignInput):
    expected_version: int = PField(ge=0)


class CampaignList(DTO):
    items: list[CampaignDTO]
    stats: dict | None = None


class CampaignStats(DTO):
    issued_count: int
    reserved_count: int
    used_count: int
    expired_count: int
    coupon_discount_cents: int
    new_customers: int
    verified_customers: int
    volume_discount_cents: int


class PromotionRecord(DTO):
    id: str
    account_id: str | None = None
    campaign_id: str | None = None
    account_masked: str
    campaign_name: str
    coupon_grant_id: str | None = None
    type: str
    status: str
    risk_hold: str | None = None
    amount_cents: int = 0
    reason: str = ''
    time: datetime
    version: int = 0


class PromotionSummary(DTO):
    issued: int
    available: int
    reserved: int
    used: int
    expired: int
    revoked: int
    frozen: int
    reserved_liability_cents: int
    coupon_discount_cents: int
    volume_discount_cents: int
    realized_discount_cents: int
    unresolved_used_count: int
    unresolved_used_face_cents: int
    refund_compensation_cents: int = 0
    new_customers: int
    verified_customers: int
    campaigns: list[dict]


class PromotionRecords(DTO):
    items: list[PromotionRecord]
    summary: PromotionSummary


class ReasonInput(DTO):
    expected_version: int = PField(ge=0)
    reason: str = PField(min_length=1, max_length=500)


class CustomerNotificationPreferences(DTO):
    order_created: bool = False
    order_status_updated: bool = False
    referral_reward: bool = False


class CustomerAccountDTO(DTO):
    id: str
    email_masked: str
    display_name: str
    verified: bool
    status: Literal['PENDING_VERIFICATION', 'ACTIVE', 'SUSPENDED']
    version: int = PField(ge=0)
    notification_preferences: CustomerNotificationPreferences


class CustomerOrderSummary(DTO):
    id: str
    pickup_code: str
    total_cents: int
    status: str


class CustomerRecord(CustomerAccountDTO):
    created_at: datetime
    order_count: int
    coupon_count: int
    invite_code: str
    referral_count: int
    allowed_actions: list[str]
    orders: list[CustomerOrderSummary] | None = None


class CustomerList(DTO):
    items: list[CustomerRecord]
