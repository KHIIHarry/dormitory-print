"""Payroll DTOs and request validation.

The module is deliberately self-contained: shared entity models live in
``app.models`` and the HTTP envelope/actor helpers live in ``app.core``.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator

from .core import DTO

PERIOD_PATTERN = r'^\d{4}-(0[1-9]|1[0-2])$'
_PERIOD_RE = re.compile(PERIOD_PATTERN)


def _clean_reason(value: str) -> str:
    value = (value or '').strip()
    if not value:
        raise ValueError('请填写原因')
    return value


class PolicyInput(DTO):
    mode: Literal['FIXED', 'PERCENT']
    fixed_cents: int = Field(default=0, ge=0, le=100_000_000)
    rate_bps: int = Field(default=0, ge=0, le=10000)
    enabled: bool = True
    expected_version: int | None = Field(default=None, ge=0)
    reason: str = Field(default='', max_length=500)

    @field_validator('reason')
    @classmethod
    def trim_reason(cls, value: str) -> str:
        return (value or '').strip()


class ApplyPolicyInput(DTO):
    policy_id: str | None = Field(default=None, max_length=64)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator('reason')
    @classmethod
    def trim_reason(cls, value: str) -> str:
        return _clean_reason(value)


class ManualEntryInput(DTO):
    staff_id: str = Field(min_length=1, max_length=64)
    order_id: str | None = Field(default=None, max_length=64)
    amount_cents: int = Field(ge=-100_000_000, le=100_000_000)
    period: str | None = Field(default=None, pattern=PERIOD_PATTERN)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator('reason')
    @classmethod
    def trim_reason(cls, value: str) -> str:
        return _clean_reason(value)


class SettlementInput(DTO):
    staff_id: str = Field(min_length=1, max_length=64)
    period: str = Field(pattern=PERIOD_PATTERN)
    entry_ids: list[str] | None = Field(default=None, max_length=500)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator('reason')
    @classmethod
    def trim_reason(cls, value: str) -> str:
        return _clean_reason(value)

    @field_validator('entry_ids')
    @classmethod
    def unique_entry_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if not value:
            return None
        if len(set(value)) != len(value):
            raise ValueError('工资条目不可重复选择')
        return value


class StaffBriefDTO(DTO):
    id: str
    name: str


class PayrollStaffDTO(DTO):
    """Minimal authorized payroll picker row; never exposes staff PII."""

    id: str
    name: str
    role: str
    enabled: bool


class PolicyDTO(DTO):
    id: str
    version: int
    scope_key: str
    staff_id: str | None = None
    staff_name: str = ''
    mode: str
    fixed_cents: int
    rate_bps: int
    enabled: bool
    updated_at: str


class PolicyCatalogDTO(DTO):
    default_policy: PolicyDTO | None = None
    overrides: list[PolicyDTO] = Field(default_factory=list)


class WageEntryDTO(DTO):
    id: str
    version: int
    staff_id: str
    order_id: str | None = None
    assignment_id: str | None = None
    source_key: str
    kind: str
    amount_cents: int
    period: str
    reason: str = ''
    actor_id: str = ''
    snapshot: dict = Field(default_factory=dict)
    created_at: str
    settlement_id: str | None = None
    settled_at: str | None = None
    needs_configuration: bool = False


class LedgerSummaryDTO(DTO):
    staff_id: str
    staff_name: str
    entry_count: int = 0
    gross_cents: int = 0
    deductions_cents: int = 0
    net_cents: int = 0
    paid_cents: int = 0
    unpaid_cents: int = 0
    carry_forward_cents: int = 0
    available_cents: int = 0
    needs_configuration_count: int = 0


class NeedsConfigurationDTO(DTO):
    entry_id: str
    order_id: str | None = None
    staff_id: str
    staff_name: str
    assignment_id: str | None = None
    delivered_at: str | None = None
    period: str
    created_at: str


class SettlementItemDTO(DTO):
    entry_id: str
    kind: str
    amount_cents: int
    source_key: str
    order_id: str | None = None
    period: str


class SettlementDTO(DTO):
    id: str
    version: int
    staff_id: str
    staff_name: str
    period: str
    amount_cents: int
    paid_at: str
    paid_by: str
    paid_by_name: str = ''
    reason: str
    created_at: str
    items: list[SettlementItemDTO] = Field(default_factory=list)


class StaffLedgerDTO(DTO):
    staff: StaffBriefDTO
    period: str
    summary: LedgerSummaryDTO
    entries: list[WageEntryDTO] = Field(default_factory=list)
    carry_forward: list[WageEntryDTO] = Field(default_factory=list)
    settlements: list[SettlementDTO] = Field(default_factory=list)


class AdminLedgerDTO(DTO):
    period: str
    summaries: list[LedgerSummaryDTO] = Field(default_factory=list)
    entries: list[WageEntryDTO] = Field(default_factory=list)
    carry_forward: list[WageEntryDTO] = Field(default_factory=list)
    needs_configuration: list[NeedsConfigurationDTO] = Field(default_factory=list)
