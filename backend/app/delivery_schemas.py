"""Strict DTOs and pure validation rules for the delivery module.

Field aliases follow the existing frontend/OpenAPI camelCase contract.  Business
dates and times are entered in Asia/Shanghai (the frontend only submits a local
``date`` plus a human ``time`` window); persisted instants are UTC.
"""
from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator

from .core import DTO

SHANGHAI = ZoneInfo('Asia/Shanghai')
WEEKDAYS = ('星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日')

BATCH_ACTIONS = (
    'ADD',
    'REMOVE',
    'TRANSFER',
    'SPLIT',
    'MERGE',
    'RESCHEDULE',
    'CANCEL',
    'DELETE',
    'PUBLISH',
    'START',
)
SCHEDULE_ACTIONS = frozenset({'ADD', 'REMOVE', 'TRANSFER', 'SPLIT', 'MERGE', 'RESCHEDULE', 'CANCEL', 'DELETE'})
COURIER_EXCEPTION_TYPES = {
    'CANNOT_CONTACT': '无法联系收件人',
    'LOCATION_UNAVAILABLE': '无法获取设备定位',
    'PHOTO_UPLOAD_FAILED': '照片上传失败',
    'DAMAGED_OR_MISSING': '资料损坏或缺失',
    'BUILDING_INACCESSIBLE': '楼栋暂时无法进入',
    'OTHER': '其他配送问题',
}
_LEGACY_EXCEPTION_LABELS = {label: code for code, label in COURIER_EXCEPTION_TYPES.items()}
LOCATION_FAILURE_CODES = {'PERMISSION_DENIED', 'POSITION_UNAVAILABLE', 'TIMEOUT', 'UNSUPPORTED', 'DEVICE_UNAVAILABLE'}


def _parse_business_date(value: str) -> date_type:
    if not isinstance(value, str):
        raise ValueError('业务日期格式必须为 YYYY-MM-DD')
    try:
        return date_type.fromisoformat(value)
    except ValueError as exc:
        raise ValueError('业务日期格式必须为 YYYY-MM-DD') from exc


def _parse_hhmm(value: str) -> time:
    if not isinstance(value, str):
        raise ValueError('时间格式必须为 HH:MM')
    pieces = value.strip().split(':')
    if len(pieces) != 2 or not pieces[0].isdigit() or not pieces[1].isdigit():
        raise ValueError('时间格式必须为 HH:MM')
    hour, minute = int(pieces[0]), int(pieces[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError('时间超出合法范围')
    return time(hour, minute)


def _shanghai_instant(day: date_type, value: time) -> datetime:
    return datetime.combine(day, value, tzinfo=SHANGHAI).astimezone(timezone.utc)


def _split_window(value: str) -> tuple[time, time]:
    text = value.strip()
    for separator in ('–', '—', '~', '至'):
        if separator in text:
            left, right = text.split(separator, 1)
            return _parse_hhmm(left), _parse_hhmm(right)
    if '-' in text:
        left, right = text.split('-', 1)
        return _parse_hhmm(left), _parse_hhmm(right)
    raise ValueError('配送时间必须填写开始和结束时间，例如 19:00 – 20:00')


def _aware_utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f'{label} 必须包含时区')
    return value.astimezone(timezone.utc)


def batch_window(
    date: str | None,
    window: str | None,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Resolve a batch window from frontend ``date``+``time`` or explicit instants."""
    if starts_at is not None or ends_at is not None:
        if starts_at is None or ends_at is None:
            raise ValueError('startsAt 与 endsAt 必须同时提供')
        if date or window:
            raise ValueError('不能同时提交 date/time 与 startsAt/endsAt')
        first = _aware_utc(starts_at, 'startsAt')
        last = _aware_utc(ends_at, 'endsAt')
    else:
        if not date or not window:
            raise ValueError('请填写配送日期与时间段')
        day = _parse_business_date(date)
        start_time, end_time = _split_window(window)
        first = _shanghai_instant(day, start_time)
        last = _shanghai_instant(day, end_time)
    if last <= first:
        raise ValueError('结束时间必须晚于开始时间')
    return first, last


def slot_window(date: str, start: str, end: str) -> tuple[datetime, datetime]:
    day = _parse_business_date(date)
    first = _shanghai_instant(day, _parse_hhmm(start))
    last = _shanghai_instant(day, _parse_hhmm(end))
    if last <= first:
        raise ValueError('结束时间必须晚于开始时间')
    return first, last


def slot_weekday(date: str) -> str:
    return WEEKDAYS[_parse_business_date(date).weekday()]


def to_shanghai_date(value: datetime) -> str:
    return value.astimezone(SHANGHAI).date().isoformat()


def to_shanghai_hhmm(value: datetime) -> str:
    return value.astimezone(SHANGHAI).strftime('%H:%M')


def to_shanghai_window(starts_at: datetime, ends_at: datetime) -> str:
    return f'{to_shanghai_hhmm(starts_at)} – {to_shanghai_hhmm(ends_at)}'


def position_errors(
    position: PositionInput,
    *,
    now: datetime,
    max_age_seconds: int,
    max_accuracy_meters: float,
) -> list[str]:
    """Validate freshness/accuracy of a reported device position (WGS84)."""
    if position.captured_at is None or position.captured_at.tzinfo is None:
        return ['LOCATION_TIME_INVALID']
    captured_at = position.captured_at.astimezone(timezone.utc)
    if captured_at > now + timedelta(seconds=60):
        return ['LOCATION_TIME_INVALID']
    if now - captured_at > timedelta(seconds=max_age_seconds):
        return ['LOCATION_STALE']
    if position.accuracy_meters <= 0 or position.accuracy_meters > max_accuracy_meters:
        return ['LOCATION_ACCURACY']
    return []


def proof_shape_errors(
    *,
    role: str,
    has_photo: bool,
    skip_photo_reason: str | None,
    has_position: bool,
    skip_location_reason: str | None,
    location_approved: bool,
    location_failure_code: str | None = None,
) -> list[str]:
    """Independent photo/location requirements; never treats a reason as success."""
    photo_reason = (skip_photo_reason or '').strip()
    location_reason = (skip_location_reason or '').strip()
    errors: list[str] = []
    if has_position and location_reason:
        errors.append('LOCATION_CONFLICT')
    if has_photo and photo_reason:
        errors.append('PHOTO_CONFLICT')
    if not has_photo and not photo_reason:
        errors.append('PHOTO_OR_REASON_REQUIRED')
    if not has_position:
        if role != 'ADMIN':
            if location_reason:
                errors.append('LOCATION_SKIP_FORBIDDEN')
            elif not location_approved:
                errors.append('LOCATION_REQUIRED')
        else:
            if not location_reason:
                errors.append('LOCATION_REASON_REQUIRED')
            elif not location_failure_code:
                errors.append('LOCATION_FAILURE_CODE_REQUIRED')
            elif location_failure_code not in LOCATION_FAILURE_CODES:
                errors.append('LOCATION_FAILURE_CODE_INVALID')
    return errors


def normalize_exception_type(value: str) -> str:
    """Accept the frontend labels and stable codes, persisting a stable code."""
    text = (value or '').strip()
    if text in COURIER_EXCEPTION_TYPES:
        return text
    if text in _LEGACY_EXCEPTION_LABELS:
        return _LEGACY_EXCEPTION_LABELS[text]
    raise ValueError('不支持的配送异常类型')


class PositionInput(DTO):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_meters: float = Field(gt=0, le=100000)
    captured_at: datetime
    coordinate_system: Literal['WGS84'] = 'WGS84'


class BatchCreate(DTO):
    name: str = Field(min_length=1, max_length=120)
    slot_id: str | None = None
    date: str | None = None
    time: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    courier_id: str | None = None
    reason: str = Field(default='', max_length=2000)

    @field_validator('name')
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('批次名称不能为空')
        return value

    @model_validator(mode='after')
    def _slot_or_window(self):
        if self.slot_id and (self.date is not None or self.time is not None
                             or self.starts_at is not None or self.ends_at is not None):
            raise ValueError('选择预置配送时段后不能同时提交日期或时间')
        return self

    def window(self) -> tuple[datetime, datetime]:
        if self.slot_id:
            raise ValueError('选择预置配送时段时由服务端填充日期与时间')
        return batch_window(self.date, self.time, self.starts_at, self.ends_at)


class BatchUpdate(DTO):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    date: str | None = None
    time: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    courier_id: str | None = None
    reason: str = Field(min_length=1, max_length=2000)


class BatchVersionInput(DTO):
    expected_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=2000)


class SlotDeleteInput(DTO):
    expected_version: int | None = Field(default=None, ge=1)
    reason: str = Field(default='管理员移除配送时段', max_length=2000)


class BatchOperationPreview(DTO):
    action: Literal[
        'ADD',
        'REMOVE',
        'TRANSFER',
        'SPLIT',
        'MERGE',
        'RESCHEDULE',
        'CANCEL',
        'DELETE',
        'PUBLISH',
        'START',
    ]
    order_ids: list[str] = Field(default_factory=list)
    source_batch_ids: list[str] = Field(default_factory=list)
    target_batch_id: str | None = None
    versions: dict[str, int] = Field(default_factory=dict)
    payload: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def _source_present(self):
        if self.action in SCHEDULE_ACTIONS and not self.source_batch_ids:
            raise ValueError('批量操作必须提供来源批次')
        return self


class BatchOperationCommit(DTO):
    plan_id: str = Field(min_length=1, max_length=64)


class SlotInput(DTO):
    # The current frontend reposts the whole slot object; id/weekday are derived
    # server-side and accepted only so the strict DTO matches that call shape.
    id: str | None = None
    weekday: str | None = None
    date: str
    name: str = Field(min_length=1, max_length=120)
    start: str
    end: str
    capacity: int = Field(ge=1, le=100000)
    enabled: bool = True
    reason: str | None = Field(default=None, max_length=2000)

    @field_validator('date')
    @classmethod
    def _date(cls, value: str) -> str:
        _parse_business_date(value)
        return value

    @model_validator(mode='after')
    def _valid_window(self):
        slot_window(self.date, self.start, self.end)
        return self

    def window(self) -> tuple[datetime, datetime]:
        return slot_window(self.date, self.start, self.end)


class SlotUpdate(DTO):
    id: str | None = None
    weekday: str | None = None
    expected_version: int | None = Field(default=None, ge=1)
    date: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    start: str | None = None
    end: str | None = None
    capacity: int | None = Field(default=None, ge=1, le=100000)
    enabled: bool | None = None
    reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode='after')
    def _valid_supplied_window(self):
        if self.date is not None and self.start is not None and self.end is not None:
            slot_window(self.date, self.start, self.end)
        return self

    def window(self) -> tuple[datetime, datetime] | None:
        if self.date is None or self.start is None or self.end is None:
            return None
        return slot_window(self.date, self.start, self.end)


class MapHotspotInput(DTO):
    id: str | None = None
    name: str = Field(min_length=1, max_length=120)
    type: str = Field(default='DORMITORY', max_length=32)
    # Percent of the private map image, matching CampusMap's 0-100 contract.
    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=100)
    enabled: bool = True


class MapPublishInput(DTO):
    version: int = Field(ge=1)
    hotspots: list[MapHotspotInput] = Field(min_length=1)
    migration_confirmed: bool = False
    map_asset_id: str | None = None
    expected_version: int | None = Field(default=None, ge=1)


class MapHotspotUpdate(DTO):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    type: str | None = Field(default=None, max_length=32)
    x: float | None = Field(default=None, ge=0, le=100)
    y: float | None = Field(default=None, ge=0, le=100)
    enabled: bool | None = None
    reason: str = Field(min_length=1, max_length=2000)


class DeliveryProofInput(DTO):
    expected_version: int = Field(ge=1)
    delivery_assignment_id: str = Field(min_length=1, max_length=64)
    photo_asset_id: str | None = None
    skip_photo_reason: str | None = Field(default=None, max_length=1000)
    position: PositionInput | None = None
    location_failure_code: str | None = Field(default=None, max_length=48)
    skip_location_reason: str | None = Field(default=None, max_length=1000)
    placement_note: str | None = Field(default=None, max_length=500)


class BatchDeliveryProofItem(DTO):
    order_id: str = Field(min_length=1, max_length=64)
    delivery_assignment_id: str = Field(min_length=1, max_length=64)
    expected_version: int = Field(ge=1)
    expected_assignment_version: int = Field(ge=1)
    position: PositionInput | None = None
    location_failure_code: str | None = Field(default=None, max_length=48)
    skip_location_reason: str | None = Field(default=None, max_length=1000)


class BatchDeliveryProofInput(DTO):
    """One courier-owned photo and placement note shared by selected assignments."""

    photo_asset_id: str | None = None
    skip_photo_reason: str | None = Field(default=None, max_length=1000)
    placement_note: str | None = Field(default=None, max_length=500)
    items: list[BatchDeliveryProofItem] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def _unique_targets(self):
        order_ids = [item.order_id for item in self.items]
        assignment_ids = [item.delivery_assignment_id for item in self.items]
        if len(order_ids) != len(set(order_ids)):
            raise ValueError('items 中的订单不能重复')
        if len(assignment_ids) != len(set(assignment_ids)):
            raise ValueError('items 中的配送执行不能重复')
        return self


class CourierExceptionInput(DTO):
    expected_version: int = Field(ge=1)
    type: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator('reason')
    @classmethod
    def _reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('异常原因不能为空')
        return value


class LocationExceptionInput(DTO):
    expected_version: int = Field(ge=1)
    code: str = Field(min_length=1, max_length=48)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator('reason')
    @classmethod
    def _reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('定位例外原因不能为空')
        return value


class ExceptionDecisionInput(DTO):
    expected_version: int = Field(ge=1)
    decision: Literal['APPROVED', 'REJECTED']
    reason: str = Field(min_length=1, max_length=1000)


class ExceptionResolveInput(DTO):
    expected_version: int = Field(ge=1)
    resolution: Literal['REDELIVER']
    reason: str = Field(min_length=1, max_length=1000)


class AddressReviewInput(DTO):
    expected_version: int = Field(ge=1)
    decision: Literal['APPROVED', 'REJECTED']
    reason: str = Field(min_length=1, max_length=1000)


# ---- response views -------------------------------------------------------


class BlockerView(DTO):
    code: str
    message: str
    related_id: str | None = None


class LocationView(DTO):
    id: str
    name: str
    type: str
    x: float
    y: float
    enabled: bool
    map_version: int
    version: int


class PublicMapView(DTO):
    version: int
    map_asset_id: str | None = None
    map_url: str | None = None
    published_at: datetime | None = None
    items: list[LocationView]


class AdminMapView(PublicMapView):
    hotspots: list[LocationView]


class SlotView(DTO):
    id: str
    date: str
    weekday: str
    name: str
    start: str
    end: str
    starts_at: datetime
    ends_at: datetime
    capacity: int
    occupied: int
    remaining: int
    enabled: bool
    selectable: bool
    archived: bool = False
    version: int


class SlotListView(DTO):
    items: list[SlotView]


class BatchView(DTO):
    id: str
    name: str
    date: str
    time: str
    starts_at: datetime
    ends_at: datetime
    slot_id: str | None = None
    slot_name: str | None = None
    courier_id: str | None = None
    courier_name: str | None = None
    status: str
    order_ids: list[str]
    assignment_ids: list[str]
    reason: str
    version: int
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class BatchListView(DTO):
    items: list[BatchView]


class ImpactView(DTO):
    batch_id: str | None = None
    order_id: str | None = None
    pickup_code: str | None = None
    message: str


class BatchPlanView(DTO):
    plan_id: str
    action: str
    blockers: list[BlockerView]
    impacts: list[ImpactView]
    expires_at: datetime


class BatchOperationResultView(DTO):
    action: str
    batches: list[BatchView]
    orders: list[dict]
    plan_id: str | None = None


class ProofView(DTO):
    id: str
    order_id: str
    assignment_id: str
    photo_asset_id: str | None = None
    skip_photo_reason: str | None = None
    position: dict | None = None
    skip_location_reason: str | None = None
    location_exception_id: str | None = None
    placement_note: str | None = None
    delivered_at: datetime
    operator_id: str


class DeliveryAssignmentView(DTO):
    id: str
    order_id: str
    batch_id: str
    batch_name: str | None = None
    courier_id: str | None = None
    courier_name: str | None = None
    status: str
    version: int
    address_review: bool
    location_snapshot: dict | None = None
    started_at: datetime | None = None
    delivered_at: datetime | None = None


class LocationExceptionApprovalView(DTO):
    """Server-approved location exception for one exact assignment version."""

    id: str
    status: Literal['APPROVED']
    assignment_id: str
    assignment_version: int
    reason: str | None = None
    failure_code: str | None = None
    approved_at: datetime | None = None


class DeliveryOrderView(DTO):
    id: str
    version: int
    pickup_code: str
    status: str
    nickname: str | None = None
    phone: str | None = None
    customer_note: str | None = None
    location_id: str | None = None
    location_name: str | None = None
    location_snapshot: dict | None = None
    packing_status: str | None = None
    packing_version: int | None = None
    packing_checklist_version: int | None = None
    address_review: bool = False
    delivery_assignment_id: str | None = None
    batch_id: str | None = None
    requested_slot: dict | None = None
    confirmed_slot: dict | None = None
    assignment: DeliveryAssignmentView | None = None
    proof: ProofView | None = None
    location_exception_approval: LocationExceptionApprovalView | None = None
    delivery_status: str | None = None
    # Management-only fields; omitted for COURIER by response_model_exclude_none.
    content_revision: int | None = None
    current_quote_id: str | None = None
    actual_started_at: datetime | None = None
    settlement: dict | None = None
    total_cents: int | None = None
    cash_received_cents: int | None = None
    settlement_kind: str | None = None
    production_summary: dict | None = None
    allowed_actions: list[str] = Field(default_factory=list)
    blockers: list[BlockerView] = Field(default_factory=list)


class DeliveryStaffView(DTO):
    """Minimal courier-picker row; never exposes the private staff directory."""

    id: str
    name: str
    role: str
    enabled: bool
    status: str
    capabilities: list[str] = Field(default_factory=list)


class DeliveryStaffListView(DTO):
    items: list[DeliveryStaffView]


class BatchDeliveryProofResultView(DTO):
    batch_id: str
    proofs: list[ProofView]
    orders: list[DeliveryOrderView]


class CourierBatchDetailView(DTO):
    batch: BatchView
    orders: list[DeliveryOrderView]


class CourierOrderListView(DTO):
    items: list[DeliveryOrderView]


class DeliveryExceptionView(DTO):
    id: str
    order_id: str | None = None
    type: str
    status: str
    reason: str
    blocks_production: bool
    blocks_delivery: bool
    version: int
    created_at: datetime
    data: dict


class DeliveryExceptionListView(DTO):
    items: list[DeliveryExceptionView]
