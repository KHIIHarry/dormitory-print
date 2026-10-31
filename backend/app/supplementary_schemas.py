"""Strict request/response DTOs for the supplementary fee ledger."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, StrictBool, StrictInt, field_validator

from .core import DTO


def _clean(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError('内容不能为空')
    return value


class ChargeInput(DTO):
    purpose: str = Field(min_length=1, max_length=2000)
    amount_cents: StrictInt = Field(gt=0)
    include_in_payroll: StrictBool
    expected_version: StrictInt = Field(ge=1)

    @field_validator('purpose')
    @classmethod
    def purpose_not_blank(cls, value: str) -> str:
        return _clean(value)


class ChargeAction(DTO):
    reason: str = Field(min_length=1, max_length=2000)
    expected_version: StrictInt = Field(ge=1)

    @field_validator('reason')
    @classmethod
    def reason_not_blank(cls, value: str) -> str:
        return _clean(value)


class ReceiptInput(ChargeAction):
    amount_cents: StrictInt = Field(gt=0)


class RefundInput(ChargeAction):
    amount_cents: StrictInt = Field(gt=0)


class DisputeInput(DTO):
    reason: str = Field(min_length=1, max_length=2000)
    expected_version: StrictInt = Field(ge=1)

    @field_validator('reason')
    @classmethod
    def reason_not_blank(cls, value: str) -> str:
        return _clean(value)


class DisputeResolveInput(DTO):
    resolution: str = Field(min_length=1, max_length=2000)
    status: Literal['RESOLVED', 'REJECTED'] = 'RESOLVED'
    expected_version: StrictInt = Field(ge=1)

    @field_validator('resolution')
    @classmethod
    def resolution_not_blank(cls, value: str) -> str:
        return _clean(value)


class SupplementaryOriginalDTO(DTO):
    """The original order ledger, repeated unchanged for root consumers."""

    total_cents: int
    cash_received_cents: int
    refunded_cents: int
    settlement_kind: str | None
    status: str


class SupplementaryExtrasDTO(DTO):
    """The supplementary fee aggregate, kept separate from the original order."""

    charge_count: int
    active_charge_count: int
    cancelled_charge_count: int
    fee_total_cents: int
    gross_received_cents: int
    refunded_cents: int
    net_received_cents: int
    outstanding_cents: int
    include_in_payroll_fee_total_cents: int


class SupplementaryCombinedDTO(DTO):
    """Explicit display-only totals; never written back to the original order."""

    total_cents: int
    gross_received_cents: int
    refunded_cents: int
    net_received_cents: int
    outstanding_cents: int


class SupplementarySummaryDTO(DTO):
    """Strict summary embedded by root commerce as ``orderView.supplementary``."""

    order_id: str
    order_version: int
    original: SupplementaryOriginalDTO
    extras: SupplementaryExtrasDTO
    combined: SupplementaryCombinedDTO


class MovementView(DTO):
    id: str
    kind: str
    amount_cents: int
    reason: str
    created_at: str
    actor_id: str | None = None
    payroll_status: str | None = None


class DisputeView(DTO):
    id: str
    version: int
    principal_id: str | None = None
    reason: str
    status: str
    resolution: str
    created_at: str
    resolved_at: str | None = None
    actor_id: str | None = None


class ChargeView(DTO):
    id: str
    order_id: str
    purpose: str
    amount_cents: int
    include_in_payroll: bool
    status: str
    gross_received_cents: int
    refunded_cents: int
    net_received_cents: int
    outstanding_cents: int
    credit_cents: int
    created_by: str | None = None
    created_at: str
    updated_at: str
    version: int
    movements: list[MovementView] = Field(default_factory=list)
    disputes: list[DisputeView] = Field(default_factory=list)
    data: dict = Field(default_factory=dict)


class ChargeList(DTO):
    items: list[ChargeView]
    summary: SupplementarySummaryDTO
