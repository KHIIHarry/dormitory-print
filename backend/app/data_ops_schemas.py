"""Strict request/response DTOs for controlled exports and data lifecycle routes.

Every response route declares ``Envelope[...]`` from this module.  The DTOs are
deliberately closed (``extra='forbid'``) and only expose fields the front end
actually needs; private storage keys, session tokens and raw file contents are
never part of an API model.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from .core import DTO


# ---------------------------------------------------------------------------
# Controlled exports
# ---------------------------------------------------------------------------


class OrderExportFilters(DTO):
    q: str | None = Field(default=None, max_length=120)
    status: str | None = Field(default=None, max_length=32)
    from_: str | None = Field(default=None, alias='from', max_length=40)
    to: str | None = Field(default=None, max_length=40)
    building: str | None = Field(default=None, max_length=64)
    slot: str | None = Field(default=None, max_length=64)
    batch: str | None = Field(default=None, max_length=64)
    # PAYROLL-only filters.  Other export kinds reject them in the service layer
    # instead of silently ignoring a request that would widen a salary export.
    period: str | None = Field(default=None, max_length=7)
    staff_id: str | None = Field(default=None, alias='staffId', max_length=64)


class PromotionExportFilters(DTO):
    q: str | None = Field(default=None, max_length=120)
    status: str | None = Field(default=None, max_length=32)
    risk_hold: str | None = Field(default=None, max_length=24)
    campaign_id: str | None = Field(default=None, max_length=64)
    account_id: str | None = Field(default=None, max_length=64)
    type_: str | None = Field(default=None, alias='type', max_length=48)
    from_: str | None = Field(default=None, alias='from', max_length=40)
    to: str | None = Field(default=None, max_length=40)


class ExportCreate(DTO):
    kind: Literal['ORDERS', 'FINANCE', 'PAYROLL']
    filters: OrderExportFilters = Field(default_factory=OrderExportFilters)
    fields: list[str] = Field(min_length=1, max_length=32)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator('fields')
    @classmethod
    def clean_fields(cls, value: list[str]) -> list[str]:
        cleaned = []
        for item in value:
            name = (item or '').strip()
            if not name:
                continue
            if len(name) > 64:
                raise ValueError('字段名过长')
            if name not in cleaned:
                cleaned.append(name)
        if not cleaned:
            raise ValueError('至少选择一个导出字段')
        return cleaned

    @field_validator('reason')
    @classmethod
    def clean_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('请填写导出原因')
        return value


class PromotionExportCreate(DTO):
    filters: PromotionExportFilters = Field(default_factory=PromotionExportFilters)
    fields: list[str] = Field(min_length=1, max_length=16)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator('fields')
    @classmethod
    def clean_fields(cls, value: list[str]) -> list[str]:
        cleaned = []
        for item in value:
            name = (item or '').strip()
            if not name:
                continue
            if len(name) > 64:
                raise ValueError('字段名过长')
            if name not in cleaned:
                cleaned.append(name)
        if not cleaned:
            raise ValueError('至少选择一个导出字段')
        return cleaned

    @field_validator('reason')
    @classmethod
    def clean_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('请填写导出原因')
        return value


class CustomerExportCreate(DTO):
    reason: str = Field(default='个人资料导出', max_length=500)

    @field_validator('reason')
    @classmethod
    def clean_reason(cls, value: str) -> str:
        return value.strip() or '个人资料导出'


class ExportJobView(DTO):
    id: str
    export_job_id: str
    kind: str
    status: str
    row_count: int | None = None
    truncated: bool = False
    created_at: datetime
    expires_at: datetime
    download_url: str | None = None
    error: str | None = None


class ExportCreated(DTO):
    export_job_id: str
    status: str
    expires_at: datetime


# ---------------------------------------------------------------------------
# Trash / restore
# ---------------------------------------------------------------------------


class TrashCreateInput(DTO):
    target_type: str = Field(min_length=1, max_length=48)
    target_id: str = Field(min_length=1, max_length=64)
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator('reason')
    @classmethod
    def clean_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('请填写操作原因')
        return value


class TrashPreviewInput(DTO):
    expected_version: int | None = Field(default=None, ge=1)
    reason: str = Field(default='', max_length=1000)


class TrashActionInput(DTO):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator('reason')
    @classmethod
    def clean_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('请填写操作原因')
        return value


class TrashItemView(DTO):
    id: str
    type: str
    name: str
    deleted_at: datetime
    purge_at: datetime
    reason: str
    by: str
    status: str
    version: int


class TrashList(DTO):
    items: list[TrashItemView]
    next_cursor: str | None = None


class TrashConflict(DTO):
    code: str
    message: str


class RestorePreview(DTO):
    entry: TrashItemView
    restorable: bool
    blockers: list[TrashConflict] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


class AuditLogView(DTO):
    id: str
    actor_id: str
    actor_label: str | None = None
    actor_role: str | None = None
    action: str
    target_type: str
    target_id: str
    reason: str
    request_id: str
    created_at: datetime
    data: dict


class AuditLogList(DTO):
    items: list[AuditLogView]
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# Account deletion requests
# ---------------------------------------------------------------------------


class DeletionRequestView(DTO):
    id: str
    version: int
    status: str
    account_masked: str
    email_masked: str
    display_name: str
    reason: str
    created_at: datetime
    requested_at: datetime | None = None
    reviewed_at: datetime | None = None
    blockers: list[str] = Field(default_factory=list)
    recovery_until: datetime | None = None
    can_recover: bool = False


class DeletionRequestList(DTO):
    items: list[DeletionRequestView]
    next_cursor: str | None = None


class DeletionApproveInput(DTO):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator('reason')
    @classmethod
    def clean_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('请填写审核原因')
        return value
