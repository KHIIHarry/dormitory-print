"""Controlled, asynchronous CSV exports for promotions, orders, finance and
personal account data.

The module never fabricates a download URL: a job starts ``QUEUED``, a worker
calls :func:`process_export_job` to build a private CSV, and only a ``READY``
job with an existing file yields a download link.  Files expire after the
configured 24h retention window and all downloads are authenticated and
``no-store``.

FINANCE is the accounting ledger, not the operational order list.  It includes
soft-deleted orders (their original money remains financial truth during the
24h trash window) and marks them with ``isTrashed``.  Permanently purged orders
survive only as ``ANONYMOUS_SUMMARY`` rows built from ``AnonymousLedger``; those
rows expose ``ledgerBucket`` and aggregate original/supplementary totals, never
an order id or customer identity.  Date filters for order rows use
``created_at``.  Anonymous rows are scoped by the original order-created day
preserved in the ledger when available (falling back to the purge timestamp for
older ledgers); ``createdAt`` carries that reporting day and ``purgedAt`` the
purge day.  Order-scoped filters (q/status/building/slot/batch) exclude
anonymous rows because those attributes are intentionally gone.  The global
unfiltered export therefore reconciles the all-time dashboard.  ORDERS remains
the operational list and continues to exclude soft-deleted orders.

Cash receipts that never became a Payment are a third, separately held stream.
An ORIGINAL_ORDER row exposes their per-order totals in
``unmatchedReceiptCents`` / ``unmatchedReceiptRefundCents``; each underlying
receipt also appears as an ``UNMATCHED_RECEIPT`` row using the receipt gross in
``receiptAmountCents`` and its refunded amount in ``adjustmentCents``.  After
purge the same two totals come from ``ANONYMOUS_SUMMARY``.  These columns never
populate ``cashReceivedCents``/``refundCents``, so confirmed original cash is
never double counted.

Column naming stays explicit: the ORDER export's singular
``supplementaryFeeCents`` is the active fee total, while an ANONYMOUS_SUMMARY
row carries the dashboard's plural ``supplementaryFeesCents`` in
``supplementaryAmountCents`` alongside the separate cash/refund/outstanding
totals.  Net supplementary cash is derived (received minus refunded); no extra
dashboard keys are needed for that.
"""
from __future__ import annotations

import csv
import io
import os
import re
import secrets
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select

from .config import get_config
from .core import (
    Actor,
    DomainError,
    Envelope,
    actor_dep,
    advisory_lock,
    audit,
    enqueue_job,
    lock,
    ok,
    require_admin,
    require_customer,
    seal,
    touch,
    unseal,
)
from .data_ops_schemas import (
    CustomerExportCreate,
    ExportCreate,
    ExportCreated,
    ExportJobView,
    PromotionExportCreate,
)
from .db import get_db
from .models import (
    Account,
    AnonymousLedger,
    Campaign,
    Coupon,
    DeliveryAssignment,
    DeliveryProof,
    DeletionRequest,
    ExportJob,
    Location,
    Order,
    Payment,
    PromotionEvent,
    Receipt,
    ReceiptAdjustment,
    Refund,
    DeliverySlot,
    Referral,
    Staff,
    SupplementaryCharge,
    SupplementaryDispute,
    SupplementaryMovement,
    TrashEntry,
    WageEntry,
    WageSettlement,
    WageSettlementItem,
    utcnow,
)
from .settings import get_settings

router = APIRouter()

EXPORT_HOURS_DEFAULT = 24
STATUSES = {
    'PENDING_CONFIRMATION', 'PENDING_PRINT', 'READY_FOR_DELIVERY', 'DELIVERING',
    'DELIVERED', 'CANCELLED', 'CANCELED', 'CLOSED', 'COMPLETED', 'REFUNDED',
    'DELIVERY_EXCEPTION',
}
ROW_ORIGINAL = 'ORIGINAL_ORDER'
ROW_SUPPLEMENTARY_CHARGE = 'SUPPLEMENTARY_CHARGE'
ROW_SUPPLEMENTARY_RECEIPT = 'SUPPLEMENTARY_RECEIPT'
ROW_SUPPLEMENTARY_REFUND = 'SUPPLEMENTARY_REFUND'
ROW_ANONYMOUS_SUMMARY = 'ANONYMOUS_SUMMARY'
ROW_UNMATCHED_RECEIPT = 'UNMATCHED_RECEIPT'

ORDER_SUPPLEMENTARY_FIELDS = {
    'supplementaryChargeCount': 'chargeCount',
    'supplementaryFeeCents': 'feeTotalCents',
    'supplementaryCashReceivedCents': 'grossReceivedCents',
    'supplementaryRefundCents': 'refundedCents',
    'supplementaryNetReceivedCents': 'netReceivedCents',
    'supplementaryOutstandingCents': 'outstandingCents',
    'supplementaryPayrollFeeCents': 'payrollFeeCents',
}
ORDER_FIELDS = (
    'orderId', 'pickupCode', 'status', 'totalCents', 'payableCents', 'createdAt',
    'deliveredAt', 'settlementKind', 'cashReceivedCents', 'refundCents',
    'customerNameMasked', 'phoneMasked', 'locationName', 'slotName',
    *ORDER_SUPPLEMENTARY_FIELDS,
)
FINANCE_SUPPLEMENTARY_FIELDS = (
    'rowType', 'isTrashed', 'chargeId', 'movementId', 'ledgerBucket',
    'purgedAt', 'supplementaryPurpose',
    'supplementaryAmountCents', 'includeInPayroll', 'supplementaryCashReceivedCents',
    'supplementaryRefundedCents', 'supplementaryOutstandingCents',
    'supplementaryStatus', 'supplementaryCreatedAt', 'supplementaryMovementCents',
    'supplementaryMovementReason',
    # Cash physically received but never matched to a Payment.  Kept apart from
    # cashReceivedCents so the confirmed original ledger is never inflated.
    'unmatchedReceiptCents', 'unmatchedReceiptRefundCents',
)
FINANCE_FIELDS = (
    'orderId', 'payableCents', 'cashReceivedCents', 'refundCents', 'settlementKind',
    'createdAt', 'receiptStatus', 'paymentKind', 'receiptAmountCents',
    'adjustmentCents', 'refundRecordCents',
    *FINANCE_SUPPLEMENTARY_FIELDS,
)
PROMOTION_FIELDS = (
    'accountMasked', 'campaignName', 'type', 'status', 'amountCents', 'reason', 'time',
)
PAYROLL_FIELDS = (
    'entryId', 'staffId', 'staffName', 'period', 'kind', 'amountCents', 'orderId',
    'assignmentId', 'sourceKey', 'reason', 'createdAt', 'settlementId', 'settledAt',
)
PERSONAL_HEADERS = ('section', 'recordId', 'field', 'value')
PAYROLL_PERIOD_RE = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')
SHANGHAI = ZoneInfo('Asia/Shanghai')


# ---------------------------------------------------------------------------
# Private storage helpers (shared with maintenance.py)
# ---------------------------------------------------------------------------


def storage_root() -> Path:
    root = Path(get_settings().storage_root).expanduser()
    if not root.is_absolute():
        root = (Path.cwd() / root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def storage_path(key: str, *, create_parent: bool = False) -> Path:
    if not key or not isinstance(key, str):
        raise DomainError('STORAGE_KEY_INVALID', '文件存储路径无效', 500)
    root = storage_root()
    candidate = (root / key).resolve()
    if candidate == root or root not in candidate.parents:
        raise DomainError('STORAGE_KEY_INVALID', '文件存储路径无效', 500)
    if create_parent:
        candidate.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return candidate


def purge_storage_key(key: str | None) -> bool:
    """Remove one private file. Missing files count as already cleaned; an
    unlink failure propagates so callers never claim a physical purge."""
    if not key:
        return True
    path = storage_path(key)
    if not path.exists():
        return True
    path.unlink()
    return True


def purge_storage_tree(key: str | None) -> bool:
    if not key:
        return True
    path = storage_path(key)
    if not path.exists():
        return True
    shutil.rmtree(path)
    return True


# ---------------------------------------------------------------------------
# Small serialisation helpers
# ---------------------------------------------------------------------------


def _iso(value: datetime | None) -> str:
    if value is None:
        return ''
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _mask_email(value: str | None) -> str:
    if not value:
        return ''
    local, separator, domain = value.partition('@')
    if not separator:
        return value[:1] + '***'
    return (local[:1] + '***@' + domain) if local else '***@' + domain


def _mask_phone(value: str | None) -> str:
    if not value:
        return ''
    digits = ''.join(ch for ch in value if ch.isdigit())
    if len(digits) < 7:
        return '***'
    return digits[:3] + '****' + digits[-2:]


def _mask_name(value: str | None) -> str:
    if not value:
        return ''
    return value.strip()[:1] + '***'


def _csv_cell(value) -> str:
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'true' if value else 'false'
    # Server-validated integers are typed financial amounts (including negative
    # payroll adjustments); str(-50) can never be a formula.  Only free-form
    # strings below are untrusted and need the spreadsheet-injection escape.
    if isinstance(value, int):
        return str(value)
    text = str(value).replace('\x00', '')
    stripped = text.lstrip()
    if stripped[:1] in ('=', '+', '-', '@', '\t', '\r'):
        text = "'" + text
    return text


def _csv_bytes(headers, rows) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\r\n')
    writer.writerow([_csv_cell(value) for value in headers])
    for row in rows:
        writer.writerow([_csv_cell(value) for value in row])
    # A UTF-8 BOM lets spreadsheet software open Chinese headers correctly.
    return ('\ufeff' + buffer.getvalue()).encode('utf-8')


# ---------------------------------------------------------------------------
# Permissions and job metadata
# ---------------------------------------------------------------------------


def _has_export_capability(actor: Actor, extras=()) -> bool:
    if '*' in actor.capabilities:
        return True
    return bool(actor.capabilities.intersection({'export.manage', *extras}))


def _require_exporter(actor: Actor, extras=()) -> None:
    require_admin(actor)
    if not _has_export_capability(actor, extras):
        raise DomainError('FORBIDDEN', '账号未获受控导出权限', 403)


def _require_payroll_exporter(actor: Actor) -> None:
    """Salary exports need *both* payroll and export management.

    ``_has_export_capability`` uses OR semantics for promotion exports; payroll
    data is employee-private, so an ordinary export manager must not obtain it
    through ``export.manage`` alone.
    """
    require_admin(actor)
    capabilities = set(actor.capabilities or [])
    if '*' in capabilities:
        return
    if not {'payroll.manage', 'export.manage'}.issubset(capabilities):
        raise DomainError('FORBIDDEN', '工资导出需要同时具备工资管理与导出权限', 403)


def _require_job_admin(actor: Actor, job: ExportJob, extras=()) -> None:
    require_admin(actor)
    # A job's creator gets no standing exemption: export.manage (or the
    # promotion-specific capability) must still be granted now.  Revocation
    # applies to existing status and download requests immediately.
    if not _has_export_capability(actor, extras):
        raise DomainError('FORBIDDEN', '当前账号无权访问该导出任务', 403)


def _audience(job: ExportJob) -> str:
    stored = (job.data or {}).get('audience')
    if stored in ('ADMIN', 'PROMOTION', 'CUSTOMER'):
        return stored
    if job.kind == 'PROMOTION_RECORDS':
        return 'PROMOTION'
    if job.kind == 'PERSONAL_DATA':
        return 'CUSTOMER'
    return 'ADMIN'


def _assert_audience(job: ExportJob, audience: str) -> None:
    allowed = {
        'ADMIN': {'ORDERS', 'FINANCE', 'PAYROLL'},
        'PROMOTION': {'PROMOTION_RECORDS'},
        'CUSTOMER': {'PERSONAL_DATA'},
    }[audience]
    if job.kind not in allowed:
        raise DomainError('NOT_FOUND', '导出任务不存在或不可访问', 404)


def _download_url(job: ExportJob) -> str | None:
    audience = _audience(job)
    if audience == 'PROMOTION':
        return f'/api/v1/admin/promotions/exports/{job.id}/download'
    if audience == 'CUSTOMER':
        return f'/api/v1/customer/account/exports/{job.id}/download'
    return f'/api/v1/admin/exports/{job.id}/download'


def _effective_status(job: ExportJob, now: datetime | None = None) -> str:
    now = now or utcnow()
    if job.status in ('READY', 'QUEUED', 'PROCESSING') and job.expires_at <= now:
        return 'EXPIRED'
    return job.status


def _job_view(job: ExportJob) -> ExportJobView:
    now = utcnow()
    status = _effective_status(job, now)
    data = job.data or {}
    ready = status == 'READY' and bool(job.storage_key)
    return ExportJobView(
        id=job.id,
        export_job_id=job.id,
        kind=job.kind,
        status=status,
        row_count=data.get('rowCount'),
        truncated=bool(data.get('truncated')),
        created_at=job.created_at,
        expires_at=job.expires_at,
        download_url=_download_url(job) if ready else None,
        error=data.get('error'),
    )


def _enqueue_export(db, job: ExportJob) -> None:
    enqueue_job(db, 'EXPORT', job.id, f'export:{job.id}', {'kind': job.kind})


def _row_limit() -> int:
    try:
        value = int(get_settings().export_max_rows)
    except (TypeError, ValueError):
        value = 10000
    return max(1, min(value, 100000))


def _export_hours(db) -> int:
    try:
        value = int(get_config(db, 'retention').data.get('exportHours', EXPORT_HOURS_DEFAULT))
    except (TypeError, ValueError):
        value = EXPORT_HOURS_DEFAULT
    return max(1, min(value, 24 * 30))


def _job_filters(job: ExportJob) -> dict:
    data = job.data or {}
    sealed = data.get('sealedFilters')
    if sealed:
        try:
            value = unseal(sealed)
            return value if isinstance(value, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


# ---------------------------------------------------------------------------
# Filter validation / row builders
# ---------------------------------------------------------------------------


def _parse_day(value: str | None, *, end: bool = False) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    try:
        if len(text) == 10:
            parsed = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
            return parsed + timedelta(days=1) if end else parsed
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        raise DomainError('EXPORT_FILTER_INVALID', '日期筛选格式无效', 422, {'value': value})
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _validate_filters(filters: dict, *, promotion: bool = False,
                      payroll: bool = False) -> dict:
    if not isinstance(filters, dict):
        raise DomainError('EXPORT_FILTER_INVALID', '筛选条件格式无效', 422)
    if promotion:
        allowed = {'q', 'status', 'riskHold', 'campaignId', 'accountId', 'type', 'from', 'to'}
    elif payroll:
        allowed = {'period', 'staffId'}
    else:
        allowed = {'q', 'status', 'from', 'to', 'building', 'slot', 'batch'}
    unknown = sorted(set(filters) - allowed)
    if unknown:
        raise DomainError('EXPORT_FILTER_INVALID', '包含未知的筛选条件', 422,
                          {'unknownFilters': unknown})
    clean = {key: value for key, value in filters.items() if value not in (None, '')}
    if payroll:
        if (period := clean.get('period')) and not PAYROLL_PERIOD_RE.match(str(period)):
            raise DomainError('EXPORT_FILTER_INVALID', '工资周期格式无效', 422,
                              {'field': 'period'})
        return clean
    if not promotion and clean.get('status') and clean['status'] not in STATUSES:
        raise DomainError('EXPORT_FILTER_INVALID', '订单状态筛选无效', 422,
                          {'field': 'status'})
    return clean


def _validate_fields(kind: str, fields: list[str]) -> None:
    allowed = {
        'ORDERS': set(ORDER_FIELDS),
        'FINANCE': set(FINANCE_FIELDS),
        'PAYROLL': set(PAYROLL_FIELDS),
        'PROMOTION_RECORDS': set(PROMOTION_FIELDS),
    }.get(kind, set())
    unknown = sorted(set(fields) - allowed)
    if unknown:
        raise DomainError('EXPORT_FIELD_DENIED', '导出字段不在允许范围', 422,
                          {'unknownFields': unknown})


def _orders_query(db, filters: dict, *, include_deleted: bool = False):
    query = select(Order)
    if not include_deleted:
        query = query.where(Order.deleted_at.is_(None))
    if status := filters.get('status'):
        query = query.where(Order.status == status)
    if q := filters.get('q'):
        like = '%' + q + '%'
        query = query.where(or_(Order.pickup_code.ilike(like), Order.nickname.ilike(like),
                                Order.phone.ilike(like)))
    if start := _parse_day(filters.get('from')):
        query = query.where(Order.created_at >= start)
    if end := _parse_day(filters.get('to'), end=True):
        query = query.where(Order.created_at < end)
    if building := filters.get('building'):
        query = query.where(Order.location_id == building)
    if slot := filters.get('slot'):
        query = query.where(or_(Order.requested_slot_id == slot, Order.confirmed_slot_id == slot))
    if batch := filters.get('batch'):
        query = query.where(Order.id.in_(select(DeliveryAssignment.order_id).where(
            DeliveryAssignment.batch_id == batch, DeliveryAssignment.active.is_(True))))
    return query.order_by(Order.created_at.desc(), Order.id.desc())


def _order_rows(db, filters: dict, limit: int, *,
                include_deleted: bool = False) -> list[Order]:
    return list(db.scalars(
        _orders_query(db, filters, include_deleted=include_deleted).limit(limit)))


def _proof_map(db, order_ids):
    if not order_ids:
        return {}
    rows = db.scalars(select(DeliveryProof).where(
        DeliveryProof.order_id.in_(order_ids), DeliveryProof.active.is_(True)))
    return {row.order_id: row for row in rows}


def _delivered_at(proof: DeliveryProof | None) -> str:
    if proof is None:
        return ''
    data = proof.data or {}
    return data.get('serverDeliveredAt') or data.get('deliveredAt') or _iso(proof.created_at)


def _supplementary_extras(db, order_ids) -> dict:
    """Per-order supplementary aggregates, separate from the original money."""
    extras = {}
    if not order_ids:
        return extras
    for charge in db.scalars(select(SupplementaryCharge).where(
            SupplementaryCharge.order_id.in_(order_ids)).order_by(
            SupplementaryCharge.created_at, SupplementaryCharge.id)):
        item = extras.setdefault(charge.order_id, {
            'chargeCount': 0, 'activeChargeCount': 0, 'cancelledChargeCount': 0,
            'feeTotalCents': 0, 'grossReceivedCents': 0, 'refundedCents': 0,
            'netReceivedCents': 0, 'outstandingCents': 0, 'payrollFeeCents': 0,
        })
        active = charge.status == 'ACTIVE'
        item['chargeCount'] += 1
        item['activeChargeCount' if active else 'cancelledChargeCount'] += 1
        item['grossReceivedCents'] += charge.cash_received_cents
        item['refundedCents'] += charge.refunded_cents
        if active:
            item['feeTotalCents'] += charge.amount_cents
            item['outstandingCents'] += max(
                0, charge.amount_cents - charge.cash_received_cents)
            if charge.include_in_payroll:
                item['payrollFeeCents'] += charge.amount_cents
    for item in extras.values():
        item['netReceivedCents'] = max(
            0, item['grossReceivedCents'] - item['refundedCents'])
    return extras


def _supplementary_context(db, order_ids):
    """Charges and their append-only movements for the selected orders."""
    if not order_ids:
        return [], {}
    charges = list(db.scalars(select(SupplementaryCharge).where(
        SupplementaryCharge.order_id.in_(order_ids)).order_by(
        SupplementaryCharge.created_at, SupplementaryCharge.id)))
    charge_ids = [charge.id for charge in charges]
    movements = {}
    if charge_ids:
        for movement in db.scalars(select(SupplementaryMovement).where(
                SupplementaryMovement.charge_id.in_(charge_ids)).order_by(
                SupplementaryMovement.created_at, SupplementaryMovement.id)):
            movements.setdefault(movement.charge_id, []).append(movement)
    return charges, movements


def _finance_supplementary_value(charge: SupplementaryCharge,
                                 movement: SupplementaryMovement | None,
                                 field: str, *, trashed: bool = False) -> str | int | bool:
    if field == 'rowType':
        if movement is None:
            return ROW_SUPPLEMENTARY_CHARGE
        return (ROW_SUPPLEMENTARY_RECEIPT if movement.kind == 'RECEIPT'
                else ROW_SUPPLEMENTARY_REFUND)
    if field == 'orderId':
        return charge.order_id
    if field == 'isTrashed':
        return trashed
    if field == 'ledgerBucket':
        return ''
    if field == 'chargeId':
        return charge.id
    if field == 'movementId':
        return movement.id if movement else ''
    if field == 'supplementaryPurpose':
        return charge.purpose
    if field == 'includeInPayroll':
        return charge.include_in_payroll
    if field == 'supplementaryStatus':
        return charge.status
    if field == 'supplementaryCreatedAt':
        return _iso(movement.created_at if movement else charge.created_at)
    if field == 'supplementaryMovementCents':
        return movement.amount_cents if movement else ''
    if field == 'supplementaryMovementReason':
        return movement.reason if movement else ''
    if field == 'supplementaryAmountCents':
        return charge.amount_cents if movement is None else ''
    if field == 'supplementaryCashReceivedCents':
        return charge.cash_received_cents if movement is None else ''
    if field == 'supplementaryRefundedCents':
        return charge.refunded_cents if movement is None else ''
    if field == 'supplementaryOutstandingCents':
        if movement is not None:
            return ''
        if charge.status != 'ACTIVE':
            return 0
        return max(0, charge.amount_cents - charge.cash_received_cents)
    if field in FINANCE_FIELDS:
        # Original-ledger columns stay exclusively on the ORIGINAL_ORDER row.
        return ''
    raise DomainError('EXPORT_FIELD_DENIED', '导出字段不在允许范围', 422, {'field': field})


# Attributes that only exist on the order row.  A purged order intentionally no
# longer has them, so an order-scoped filter cannot select anonymous summaries.
_ANONYMOUS_BLOCKING_FILTERS = ('q', 'status', 'building', 'slot', 'batch')


def _anonymous_summary_value(ledger: AnonymousLedger, field: str) -> str | int:
    totals = ledger.totals or {}
    if field == 'rowType':
        return ROW_ANONYMOUS_SUMMARY
    if field == 'ledgerBucket':
        return ledger.bucket
    if field == 'createdAt':
        # The ledger preserves the original order-created day (non-PII) so the
        # anonymous row stays in its original reporting period.  If an older
        # ledger has no such key, fall back to the purge timestamp.
        day = totals.get('orderCreatedDate')
        return _iso(_parse_day(day)) if day else _iso(ledger.created_at)
    if field == 'purgedAt':
        day = totals.get('purgedDate')
        return _iso(_parse_day(day)) if day else _iso(ledger.created_at)
    if field == 'payableCents':
        return int(totals.get('payableCents') or 0)
    if field == 'cashReceivedCents':
        return int(totals.get('cashReceivedCents') or 0)
    if field == 'refundCents':
        return int(totals.get('refundCents') or 0)
    if field == 'supplementaryAmountCents':
        # Singular export column, dashboard's plural supplementaryFeesCents.
        return int(totals.get('supplementaryFeesCents') or 0)
    if field == 'supplementaryCashReceivedCents':
        return int(totals.get('supplementaryCashReceivedCents') or 0)
    if field == 'supplementaryRefundedCents':
        return int(totals.get('supplementaryRefundCents') or 0)
    if field == 'supplementaryOutstandingCents':
        return int(totals.get('supplementaryOutstandingCents') or 0)
    if field == 'unmatchedReceiptCents':
        return int(totals.get('unmatchedReceiptCents') or 0)
    if field == 'unmatchedReceiptRefundCents':
        return int(totals.get('unmatchedReceiptRefundCents') or 0)
    if field in FINANCE_FIELDS:
        return ''
    raise DomainError('EXPORT_FIELD_DENIED', '导出字段不在允许范围', 422, {'field': field})


def _anonymous_summary_rows(db, filters: dict) -> list[AnonymousLedger]:
    if any(filters.get(key) for key in _ANONYMOUS_BLOCKING_FILTERS):
        return []
    start = _parse_day(filters.get('from'))
    end = _parse_day(filters.get('to'), end=True)

    def reporting_day(ledger: AnonymousLedger) -> datetime:
        totals = ledger.totals or {}
        if day := totals.get('orderCreatedDate'):
            return _parse_day(day)
        return ledger.created_at

    query = select(AnonymousLedger).order_by(
        AnonymousLedger.created_at, AnonymousLedger.id)
    rows = []
    for ledger in db.scalars(query):
        # Coupon/account deletion ledgers are not order money and carry no
        # payable/cash totals; only order ledgers belong in the finance export.
        if 'orderCount' not in (ledger.totals or {}):
            continue
        day = reporting_day(ledger)
        if start and day < start:
            continue
        if end and day >= end:
            continue
        rows.append(ledger)
    return rows


def _order_value(db, order: Order, field: str, proof: DeliveryProof | None,
                 supplementary: dict | None = None) -> str | int:
    if field in ORDER_SUPPLEMENTARY_FIELDS:
        return (supplementary or {}).get(ORDER_SUPPLEMENTARY_FIELDS[field], 0)
    if field == 'orderId':
        return order.id
    if field == 'pickupCode':
        return order.pickup_code
    if field == 'status':
        return order.status
    if field in ('totalCents', 'payableCents'):
        return order.total_cents
    if field == 'createdAt':
        return _iso(order.created_at)
    if field == 'deliveredAt':
        return _delivered_at(proof)
    if field == 'settlementKind':
        return order.settlement_kind or ''
    if field == 'cashReceivedCents':
        return order.cash_received_cents
    if field == 'refundCents':
        return order.refunded_cents
    if field == 'customerNameMasked':
        return _mask_name(order.nickname)
    if field == 'phoneMasked':
        return _mask_phone(order.phone)
    if field == 'locationName':
        location = db.get(Location, order.location_id)
        snapshot = (order.data or {}).get('locationSnapshot') or {}
        return snapshot.get('name') or (location.name if location else '')
    if field == 'slotName':
        slot = db.get(DeliverySlot, order.confirmed_slot_id or order.requested_slot_id)
        snapshot = (order.data or {}).get('confirmedSlotSnapshot') or {}
        return snapshot.get('name') or (slot.name if slot else '')
    raise DomainError('EXPORT_FIELD_DENIED', '导出字段不在允许范围', 422, {'field': field})


def _unmatched_receipts(db, order_ids: list[str]) -> dict:
    """Receipts that never became a Payment, with their refunded amounts."""
    if not order_ids:
        return {}
    candidates = list(db.scalars(select(Receipt).where(
        Receipt.order_id.in_(order_ids),
        Receipt.status.in_(('UNMATCHED', 'REFUNDED')),
    ).order_by(Receipt.created_at, Receipt.id)))
    if not candidates:
        return {}
    receipt_ids = [receipt.id for receipt in candidates]
    matched = set(db.scalars(select(Payment.receipt_id).where(
        Payment.receipt_id.in_(receipt_ids))))
    unmatched = [receipt for receipt in candidates if receipt.id not in matched]
    if not unmatched:
        return {}
    refunded_by_receipt = {receipt_id: int(amount or 0) for receipt_id, amount in db.execute(
        select(ReceiptAdjustment.receipt_id,
               func.coalesce(func.sum(ReceiptAdjustment.amount_cents), 0))
        .where(ReceiptAdjustment.receipt_id.in_([row.id for row in unmatched]))
        .group_by(ReceiptAdjustment.receipt_id))}
    grouped: dict = {}
    for receipt in unmatched:
        grouped.setdefault(receipt.order_id, []).append(
            (receipt, refunded_by_receipt.get(receipt.id, 0)))
    return grouped


def _finance_context(db, orders: list[Order]):
    ids = [order.id for order in orders]
    payments, receipts, adjustments, refunds = {}, {}, {}, {}
    if ids:
        payments = {row.order_id: row for row in db.scalars(
            select(Payment).where(Payment.order_id.in_(ids)))}
        receipt_ids = [row.receipt_id for row in payments.values() if row.receipt_id]
        if receipt_ids:
            receipts = {row.id: row for row in db.scalars(
                select(Receipt).where(Receipt.id.in_(receipt_ids)))}
            for receipt_id, amount in db.execute(
                    select(ReceiptAdjustment.receipt_id, func.coalesce(func.sum(ReceiptAdjustment.amount_cents), 0))
                    .where(ReceiptAdjustment.receipt_id.in_(receipt_ids))
                    .group_by(ReceiptAdjustment.receipt_id)):
                adjustments[receipt_id] = int(amount or 0)
        for order_id, amount in db.execute(
                select(Refund.order_id, func.coalesce(func.sum(Refund.amount_cents), 0))
                .where(Refund.order_id.in_(ids)).group_by(Refund.order_id)):
            refunds[order_id] = int(amount or 0)
    return payments, receipts, adjustments, refunds, _unmatched_receipts(db, ids)


def _finance_value(order: Order, field: str, context) -> str | int | bool:
    payments, receipts, adjustments, refunds, unmatched = context
    payment = payments.get(order.id)
    receipt = receipts.get(payment.receipt_id) if payment and payment.receipt_id else None
    if field == 'rowType':
        return ROW_ORIGINAL
    if field == 'isTrashed':
        return order.deleted_at is not None
    if field == 'unmatchedReceiptCents':
        return sum(row.amount_cents for row, _refunded in unmatched.get(order.id, []))
    if field == 'unmatchedReceiptRefundCents':
        return sum(refunded for _row, refunded in unmatched.get(order.id, []))
    if field in FINANCE_SUPPLEMENTARY_FIELDS:
        return ''
    if field == 'orderId':
        return order.id
    if field == 'payableCents':
        return order.total_cents
    if field == 'cashReceivedCents':
        return order.cash_received_cents
    if field == 'refundCents':
        return order.refunded_cents
    if field == 'settlementKind':
        return order.settlement_kind or ''
    if field == 'createdAt':
        return _iso(order.created_at)
    if field == 'receiptStatus':
        return receipt.status if receipt else ''
    if field == 'paymentKind':
        return payment.kind if payment else ''
    if field == 'receiptAmountCents':
        return receipt.amount_cents if receipt else 0
    if field == 'adjustmentCents':
        return adjustments.get(receipt.id, 0) if receipt else 0
    if field == 'refundRecordCents':
        return refunds.get(order.id, 0)
    raise DomainError('EXPORT_FIELD_DENIED', '导出字段不在允许范围', 422, {'field': field})


def _finance_unmatched_value(order: Order, receipt: Receipt, refunded: int,
                             field: str) -> str | int | bool:
    if field == 'rowType':
        return ROW_UNMATCHED_RECEIPT
    if field == 'orderId':
        return order.id
    if field == 'isTrashed':
        return order.deleted_at is not None
    if field == 'createdAt':
        return _iso(receipt.created_at)
    if field == 'receiptStatus':
        return receipt.status
    if field == 'receiptAmountCents':
        return receipt.amount_cents
    if field == 'adjustmentCents':
        return refunded
    if field in FINANCE_FIELDS:
        # The per-order unmatched summary lives on ORIGINAL_ORDER and the
        # anonymous ledger row.  This receipt-level row shows the underlying
        # receipt gross/refund fields only, so summing the two unmatched
        # columns never double counts an individual receipt.
        return ''
    raise DomainError('EXPORT_FIELD_DENIED', '导出字段不在允许范围', 422, {'field': field})


def _promotion_rows(db, filters: dict, limit: int):
    query = select(PromotionEvent).order_by(PromotionEvent.created_at.desc(), PromotionEvent.id.desc())
    if campaign := filters.get('campaignId'):
        query = query.where(PromotionEvent.campaign_id == campaign)
    if account := filters.get('accountId'):
        query = query.where(PromotionEvent.account_id == account)
    if type_ := filters.get('type'):
        query = query.where(PromotionEvent.kind == type_)
    if start := _parse_day(filters.get('from')):
        query = query.where(PromotionEvent.created_at >= start)
    if end := _parse_day(filters.get('to'), end=True):
        query = query.where(PromotionEvent.created_at < end)
    # A text query applies to masked identity / campaign / key and therefore is
    # evaluated after loading the already narrow candidate set.
    scan = limit * 8 if filters.get('q') else limit
    events = list(db.scalars(query.limit(scan)))
    # Trash-masked virtual targets must not leak back through an export.
    trashed_events = set(db.scalars(select(TrashEntry.target_id).where(
        TrashEntry.target_type == 'PROMOTION_EVENT',
        TrashEntry.status.in_(('DELETED', 'PURGE_PENDING')))))
    trashed_coupons = set(db.scalars(select(TrashEntry.target_id).where(
        TrashEntry.target_type == 'COUPON',
        TrashEntry.status.in_(('DELETED', 'PURGE_PENDING')))))
    events = [row for row in events
              if row.id not in trashed_events and row.coupon_id not in trashed_coupons]
    account_ids = {row.account_id for row in events if row.account_id}
    campaign_ids = {row.campaign_id for row in events if row.campaign_id}
    coupon_ids = {row.coupon_id for row in events if row.coupon_id}
    accounts = {row.id: row for row in db.scalars(select(Account).where(Account.id.in_(account_ids)))} if account_ids else {}
    campaigns = {row.id: row for row in db.scalars(select(Campaign).where(Campaign.id.in_(campaign_ids)))} if campaign_ids else {}
    coupons = {row.id: row for row in db.scalars(select(Coupon).where(Coupon.id.in_(coupon_ids)))} if coupon_ids else {}
    rows = []
    needle = (filters.get('q') or '').lower()
    for event in events:
        account = accounts.get(event.account_id)
        campaign = campaigns.get(event.campaign_id)
        coupon = coupons.get(event.coupon_id)
        state = coupon.status if coupon else event.kind
        risk_hold = coupon.risk_hold if coupon else 'NONE'
        if filters.get('status') and state != filters['status']:
            continue
        if filters.get('riskHold') and risk_hold != filters['riskHold']:
            continue
        masked = _mask_email(account.email) if account else '—'
        names = (masked, campaign.name if campaign else '',
                 event.business_key, event.id)
        if needle and needle not in ' '.join(names).lower():
            continue
        rows.append({
            'accountMasked': masked,
            'campaignName': campaign.name if campaign else ('退款补偿' if event.kind == 'REFUND_COMPENSATION' else '—'),
            'type': event.kind,
            'status': state,
            'amountCents': event.amount_cents,
            'reason': (event.data or {}).get('reason', ''),
            'time': _iso(event.created_at),
        })
        if len(rows) >= limit:
            break
    return rows


def _settlement_index(db, entry_ids) -> dict:
    """Map wage entry id -> (WageSettlement | None, WageSettlementItem)."""
    if not entry_ids:
        return {}
    items = list(db.scalars(select(WageSettlementItem).where(
        WageSettlementItem.entry_id.in_(entry_ids))))
    settlement_ids = [item.settlement_id for item in items]
    settlements = ({row.id: row for row in db.scalars(select(WageSettlement).where(
        WageSettlement.id.in_(settlement_ids)))} if settlement_ids else {})
    return {item.entry_id: (settlements.get(item.settlement_id), item)
            for item in items}


def _payroll_rows(db, filters: dict, limit: int) -> list[dict]:
    """Bounded payroll ledger rows for one period and optionally one staff."""
    period = filters.get('period') or datetime.now(SHANGHAI).strftime('%Y-%m')
    staff_id = filters.get('staffId')
    if staff_id and db.get(Staff, staff_id) is None:
        raise DomainError('NOT_FOUND', '员工不存在', 404)
    query = select(WageEntry).where(WageEntry.period == period)
    if staff_id:
        query = query.where(WageEntry.staff_id == staff_id)
    entries = list(db.scalars(query.order_by(
        WageEntry.created_at, WageEntry.id).limit(limit)))
    if not entries:
        return []
    staff = {row.id: row for row in db.scalars(select(Staff).where(
        Staff.id.in_({entry.staff_id for entry in entries})))}
    settlements = _settlement_index(db, [entry.id for entry in entries])
    rows = []
    for entry in entries:
        settlement, _item = settlements.get(entry.id, (None, None))
        rows.append({
            'entryId': entry.id,
            'staffId': entry.staff_id,
            'staffName': staff[entry.staff_id].name if entry.staff_id in staff else '',
            'period': entry.period,
            'kind': entry.kind,
            'amountCents': entry.amount_cents,
            'orderId': entry.order_id or '',
            'assignmentId': entry.assignment_id or '',
            'sourceKey': entry.source_key,
            'reason': entry.reason,
            'createdAt': _iso(entry.created_at),
            'settlementId': settlement.id if settlement else '',
            'settledAt': _iso(settlement.paid_at) if settlement else '',
        })
    return rows


def _personal_rows(db, account_id: str, limit: int):
    account = db.get(Account, account_id)
    if account is None:
        raise DomainError('EXPORT_ACCOUNT_MISSING', '账号不存在或已删除', 410)
    rows = [
        ['PROFILE', '', 'email', account.email],
        ['PROFILE', '', 'displayName', account.display_name],
        ['PROFILE', '', 'status', account.status],
        ['PROFILE', '', 'verifiedAt', _iso(account.verified_at)],
        ['PROFILE', '', 'createdAt', _iso(account.created_at)],
    ]
    from .models import Principal
    owner_principals = select(Principal.id).where(or_(
        Principal.id == account.principal_id, Principal.account_id == account_id))
    orders = list(db.scalars(select(Order).where(
        Order.deleted_at.is_(None),
        or_(Order.checkout_account_id == account_id,
            Order.principal_id.in_(owner_principals))).order_by(
        Order.created_at.desc()).limit(limit)))
    for order in orders:
        rows.append(['ORDER', order.id, 'pickupCode', order.pickup_code])
        rows.append(['ORDER', order.id, 'status', order.status])
        rows.append(['ORDER', order.id, 'totalCents', order.total_cents])
        rows.append(['ORDER', order.id, 'refundedCents', order.refunded_cents])
        rows.append(['ORDER', order.id, 'nickname', order.nickname])
        rows.append(['ORDER', order.id, 'phone', order.phone])
        rows.append(['ORDER', order.id, 'createdAt', _iso(order.created_at)])
    order_ids = [order.id for order in orders]
    if order_ids:
        charges = list(db.scalars(select(SupplementaryCharge).where(
            SupplementaryCharge.order_id.in_(order_ids)).order_by(
            SupplementaryCharge.created_at.desc()).limit(limit)))
        charge_map = {charge.id: charge for charge in charges}
        for charge in charges:
            active = charge.status == 'ACTIVE'
            net_received = max(0, charge.cash_received_cents - charge.refunded_cents)
            outstanding = (max(0, charge.amount_cents - charge.cash_received_cents)
                           if active else 0)
            rows += [
                ['SUPPLEMENTARY_CHARGE', charge.id, 'orderId', charge.order_id],
                ['SUPPLEMENTARY_CHARGE', charge.id, 'purpose', charge.purpose],
                ['SUPPLEMENTARY_CHARGE', charge.id, 'amountCents', charge.amount_cents],
                ['SUPPLEMENTARY_CHARGE', charge.id, 'includeInPayroll',
                 'true' if charge.include_in_payroll else 'false'],
                ['SUPPLEMENTARY_CHARGE', charge.id, 'status', charge.status],
                ['SUPPLEMENTARY_CHARGE', charge.id, 'grossReceivedCents',
                 charge.cash_received_cents],
                ['SUPPLEMENTARY_CHARGE', charge.id, 'refundedCents', charge.refunded_cents],
                ['SUPPLEMENTARY_CHARGE', charge.id, 'netReceivedCents', net_received],
                ['SUPPLEMENTARY_CHARGE', charge.id, 'outstandingCents', outstanding],
                ['SUPPLEMENTARY_CHARGE', charge.id, 'createdAt', _iso(charge.created_at)],
            ]
        charge_ids = list(charge_map)
        if charge_ids:
            for movement in db.scalars(select(SupplementaryMovement).where(
                    SupplementaryMovement.charge_id.in_(charge_ids)).order_by(
                    SupplementaryMovement.created_at.desc()).limit(limit)):
                rows += [
                    ['SUPPLEMENTARY_MOVEMENT', movement.id, 'chargeId', movement.charge_id],
                    ['SUPPLEMENTARY_MOVEMENT', movement.id, 'orderId',
                     charge_map[movement.charge_id].order_id],
                    ['SUPPLEMENTARY_MOVEMENT', movement.id, 'kind', movement.kind],
                    ['SUPPLEMENTARY_MOVEMENT', movement.id, 'amountCents',
                     movement.amount_cents],
                    ['SUPPLEMENTARY_MOVEMENT', movement.id, 'reason', movement.reason],
                    ['SUPPLEMENTARY_MOVEMENT', movement.id, 'createdAt',
                     _iso(movement.created_at)],
                ]
            disputes = db.scalars(select(SupplementaryDispute).where(
                SupplementaryDispute.charge_id.in_(charge_ids),
                or_(SupplementaryDispute.principal_id.is_(None),
                    SupplementaryDispute.principal_id == account.principal_id),
            ).order_by(SupplementaryDispute.created_at.desc()).limit(limit))
            for dispute in disputes:
                rows += [
                    ['SUPPLEMENTARY_DISPUTE', dispute.id, 'chargeId', dispute.charge_id],
                    ['SUPPLEMENTARY_DISPUTE', dispute.id, 'orderId',
                     charge_map[dispute.charge_id].order_id],
                    ['SUPPLEMENTARY_DISPUTE', dispute.id, 'reason', dispute.reason],
                    ['SUPPLEMENTARY_DISPUTE', dispute.id, 'status', dispute.status],
                    ['SUPPLEMENTARY_DISPUTE', dispute.id, 'resolution', dispute.resolution],
                    ['SUPPLEMENTARY_DISPUTE', dispute.id, 'createdAt',
                     _iso(dispute.created_at)],
                ]
    # Only the Staff row linked to this exact account may disclose wage entries;
    # names, phones and e-mail addresses are never used to infer an employee.
    staff = db.scalar(select(Staff).where(Staff.account_id == account_id))
    if staff is not None:
        entries = list(db.scalars(select(WageEntry).where(
            WageEntry.staff_id == staff.id).order_by(
            WageEntry.created_at.desc()).limit(limit)))
        settlements = _settlement_index(db, [entry.id for entry in entries])
        for entry in entries:
            settlement, _item = settlements.get(entry.id, (None, None))
            rows += [
                ['WAGE_ENTRY', entry.id, 'period', entry.period],
                ['WAGE_ENTRY', entry.id, 'kind', entry.kind],
                ['WAGE_ENTRY', entry.id, 'amountCents', entry.amount_cents],
                ['WAGE_ENTRY', entry.id, 'orderId', entry.order_id or ''],
                ['WAGE_ENTRY', entry.id, 'reason', entry.reason],
                ['WAGE_ENTRY', entry.id, 'createdAt', _iso(entry.created_at)],
                ['WAGE_ENTRY', entry.id, 'settlementId', settlement.id if settlement else ''],
                ['WAGE_ENTRY', entry.id, 'settledAt',
                 _iso(settlement.paid_at) if settlement else ''],
            ]
    for coupon in db.scalars(select(Coupon).where(Coupon.account_id == account_id).order_by(
            Coupon.created_at.desc()).limit(limit)):
        rows.append(['COUPON', coupon.id, 'status', coupon.status])
        rows.append(['COUPON', coupon.id, 'amountCents', coupon.amount_cents])
        rows.append(['COUPON', coupon.id, 'validUntil', _iso(coupon.valid_until)])
    for referral in db.scalars(select(Referral).where(or_(
            Referral.inviter_id == account_id, Referral.invitee_id == account_id)).limit(limit)):
        rows.append(['REFERRAL', referral.id, 'status', referral.status])
        rows.append(['REFERRAL', referral.id, 'qualifiedAt', _iso(referral.qualified_at)])
    for request in db.scalars(select(DeletionRequest).where(
            DeletionRequest.account_id == account_id).order_by(DeletionRequest.created_at.desc()).limit(limit)):
        rows.append(['DELETION_REQUEST', request.id, 'status', request.status])
        rows.append(['DELETION_REQUEST', request.id, 'createdAt', _iso(request.created_at)])
    truncated = len(rows) > limit
    return PERSONAL_HEADERS, rows[:limit], truncated


def _build_export(db, job: ExportJob, limit: int):
    kind = job.kind
    filters = _validate_filters(
        _job_filters(job),
        promotion=kind == 'PROMOTION_RECORDS',
        payroll=kind == 'PAYROLL',
    )
    data = job.data or {}
    if kind in ('ORDERS', 'FINANCE'):
        fields = list(data.get('fields') or [])
        _validate_fields(kind, fields)
        orders = _order_rows(db, filters, limit + 1,
                             include_deleted=kind == 'FINANCE')
        truncated = len(orders) > limit
        orders = orders[:limit]
        proofs = _proof_map(db, [order.id for order in orders])
        if kind == 'ORDERS':
            extras = _supplementary_extras(db, [order.id for order in orders])
            rows = [[_order_value(db, order, field, proofs.get(order.id),
                                  extras.get(order.id)) for field in fields]
                    for order in orders]
        else:
            context = _finance_context(db, orders)
            charges, movements = _supplementary_context(
                db, [order.id for order in orders])
            rows = []
            for order in orders:
                trashed = order.deleted_at is not None
                rows.append([_finance_value(order, field, context) for field in fields])
                for charge in charges:
                    if charge.order_id != order.id:
                        continue
                    rows.append([_finance_supplementary_value(
                        charge, None, field, trashed=trashed) for field in fields])
                    for movement in movements.get(charge.id, []):
                        rows.append([_finance_supplementary_value(
                            charge, movement, field, trashed=trashed) for field in fields])
                for receipt, refunded in context[4].get(order.id, []):
                    rows.append([_finance_unmatched_value(
                        order, receipt, refunded, field) for field in fields])
            # Purged orders live on only in the anonymous ledger.  These rows
            # carry aggregate original/supplementary totals, never order or
            # customer identifiers, so reconciliation survives the purge.
            rows.extend([_anonymous_summary_value(ledger, field)
                         for field in fields]
                        for ledger in _anonymous_summary_rows(db, filters))
            truncated = truncated or len(rows) > limit
        return fields, rows, truncated
    if kind == 'PROMOTION_RECORDS':
        fields = list(data.get('fields') or [])
        _validate_fields(kind, fields)
        items = _promotion_rows(db, filters, limit + 1)
        truncated = len(items) > limit
        items = items[:limit]
        return fields, [[item.get(field, '') for field in fields] for item in items], truncated
    if kind == 'PAYROLL':
        fields = list(data.get('fields') or [])
        _validate_fields(kind, fields)
        items = _payroll_rows(db, filters, limit + 1)
        truncated = len(items) > limit
        items = items[:limit]
        return fields, [[item.get(field, '') for field in fields] for item in items], truncated
    if kind == 'PERSONAL_DATA':
        return _personal_rows(db, job.account_id, limit + 1)
    raise DomainError('EXPORT_KIND_UNSUPPORTED', '不支持的导出类型', 422)


# ---------------------------------------------------------------------------
# Job creation / processing
# ---------------------------------------------------------------------------


def _create_job(db, actor: Actor, *, kind: str, fields: list[str], filters: dict,
                reason: str, audience: str, account_id: str | None = None) -> ExportJob:
    if kind in ('ORDERS', 'FINANCE', 'PROMOTION_RECORDS', 'PAYROLL'):
        _validate_fields(kind, fields)
    clean_filters = _validate_filters(
        filters, promotion=kind == 'PROMOTION_RECORDS', payroll=kind == 'PAYROLL')
    now = utcnow()
    job = ExportJob(
        staff_id=actor.staff_id if audience != 'CUSTOMER' else None,
        account_id=account_id,
        kind=kind,
        status='QUEUED',
        expires_at=now + timedelta(hours=_export_hours(db)),
        data={
            'fields': list(fields),
            'sealedFilters': seal(clean_filters),
            'reason': reason,
            'audience': audience,
            'requestedBy': actor.id,
            'requestedAt': now.isoformat(),
        },
    )
    db.add(job)
    db.flush()
    # Dry-run the real row builder so the row limit is enforced before a job is
    # advertised to the user (and before any file is produced).
    limit = _row_limit()
    _, rows, _ = _build_export(db, job, limit + 1)
    if len(rows) > limit:
        raise DomainError('EXPORT_ROW_LIMIT_EXCEEDED', f'导出最多 {limit} 行，请缩小筛选范围',
                          422, {'limit': limit})
    _enqueue_export(db, job)
    audit(db, actor, 'EXPORT_REQUESTED', 'EXPORT_JOB', job.id, reason=reason,
          data={'kind': kind, 'rowCount': len(rows), 'fieldCount': len(fields)})
    return job


def _fail(db, job: ExportJob, code: str) -> None:
    data = dict(job.data or {})
    data['error'] = code
    data['failedAt'] = utcnow().isoformat()
    job.data = data
    job.status = 'FAILED'
    touch(job)
    db.flush()


def _expire(db, job: ExportJob) -> None:
    data = dict(job.data or {})
    if job.storage_key:
        try:
            purge_storage_key(job.storage_key)
        except OSError:
            data['cleanupPending'] = True
        else:
            data.pop('cleanupPending', None)
            job.storage_key = None
    data['expiredAt'] = utcnow().isoformat()
    job.data = data
    job.status = 'EXPIRED'
    touch(job)
    db.flush()


def process_export_job(db, job_id: str) -> None:
    """Build one private CSV. Transaction ownership stays with the worker."""
    job = lock(db, ExportJob, job_id)
    if job.status in ('READY', 'EXPIRED', 'FAILED'):
        return
    now = utcnow()
    if job.expires_at <= now:
        _expire(db, job)
        return
    job.status = 'PROCESSING'
    touch(job)
    db.flush()
    try:
        limit = _row_limit()
        headers, rows, truncated = _build_export(db, job, limit)
        if len(rows) > limit:
            rows = rows[:limit]
            truncated = True
        content = _csv_bytes(headers, rows)
        key = f'exports/{job.id}.csv'
        destination = storage_path(key, create_parent=True)
        temporary = destination.with_name(f'.{job.id}.{secrets.token_hex(6)}.tmp')
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)
        job.storage_key = key
        job.status = 'READY'
        data = dict(job.data or {})
        data.update(rowCount=len(rows), truncated=bool(truncated),
                    generatedAt=utcnow().isoformat(), error=None)
        job.data = data
        touch(job)
        db.flush()
    except DomainError as error:
        _fail(db, job, error.code)
    except OSError:
        _fail(db, job, 'EXPORT_STORAGE_FAILED')
    except (ValueError, TypeError):
        _fail(db, job, 'EXPORT_DATA_INVALID')


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _load_job(db, job_id: str) -> ExportJob:
    job = db.get(ExportJob, job_id)
    if job is None:
        raise DomainError('NOT_FOUND', '导出任务不存在或不可访问', 404)
    return job


@router.post('/admin/promotions/records/export', response_model=Envelope[ExportCreated])
def promotion_records_export(payload: PromotionExportCreate, db=Depends(get_db),
                             actor: Actor = Depends(actor_dep)):
    _require_exporter(actor, {'promotion.manage'})
    job = _create_job(db, actor, kind='PROMOTION_RECORDS', fields=payload.fields,
                      filters=payload.filters.model_dump(by_alias=True, exclude_none=True),
                      reason=payload.reason, audience='PROMOTION')
    return ok(ExportCreated(export_job_id=job.id, status=job.status, expires_at=job.expires_at))


def _promotion_job(db, job_id: str, actor: Actor) -> ExportJob:
    job = _load_job(db, job_id)
    _assert_audience(job, 'PROMOTION')
    _require_job_admin(actor, job, {'promotion.manage'})
    return job


@router.get('/admin/promotions/exports/{job_id}', response_model=Envelope[ExportJobView])
def promotion_export_status(job_id: str, db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    job = _promotion_job(db, job_id, actor)
    if job.status == 'QUEUED':
        _enqueue_export(db, job)
    return ok(_job_view(job))


@router.get('/admin/promotions/exports/{job_id}/download')
def promotion_export_download(job_id: str, db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    job = _promotion_job(db, job_id, actor)
    return _download(db, job, actor)


@router.post('/admin/exports', response_model=Envelope[ExportJobView])
def admin_export_create(payload: ExportCreate, db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    if payload.kind == 'PAYROLL':
        _require_payroll_exporter(actor)
    else:
        _require_exporter(actor)
    job = _create_job(db, actor, kind=payload.kind, fields=payload.fields,
                      filters=payload.filters.model_dump(by_alias=True, exclude_none=True),
                      reason=payload.reason, audience='ADMIN')
    return ok(_job_view(job))


def _admin_job(db, job_id: str, actor: Actor) -> ExportJob:
    job = _load_job(db, job_id)
    _assert_audience(job, 'ADMIN')
    if job.kind == 'PAYROLL':
        _require_payroll_exporter(actor)
    else:
        _require_job_admin(actor, job)
    return job


@router.get('/admin/exports/{job_id}', response_model=Envelope[ExportJobView])
def admin_export_status(job_id: str, db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    job = _admin_job(db, job_id, actor)
    if job.status == 'QUEUED':
        _enqueue_export(db, job)
    return ok(_job_view(job))


@router.get('/admin/exports/{job_id}/download')
def admin_export_download(job_id: str, db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    job = _admin_job(db, job_id, actor)
    return _download(db, job, actor)


@router.post('/customer/account/exports', response_model=Envelope[ExportCreated])
def customer_export_create(payload: CustomerExportCreate, db=Depends(get_db),
                           actor: Actor = Depends(actor_dep)):
    require_customer(actor, verified=True)
    advisory_lock(db, 'customer-export:' + (actor.account_id or ''))
    existing = db.scalar(select(ExportJob).where(
        ExportJob.account_id == actor.account_id, ExportJob.kind == 'PERSONAL_DATA',
        ExportJob.status.in_(('QUEUED', 'PROCESSING')), ExportJob.expires_at > utcnow()))
    if existing is not None:
        # The legacy auth route persists the same ExportJob model but cannot
        # enqueue the worker job itself; unify it here on the shared outbox.
        _enqueue_export(db, existing)
        return ok(ExportCreated(export_job_id=existing.id, status=existing.status,
                                expires_at=existing.expires_at))
    job = _create_job(db, actor, kind='PERSONAL_DATA', fields=[], filters={},
                      reason=payload.reason, audience='CUSTOMER', account_id=actor.account_id)
    return ok(ExportCreated(export_job_id=job.id, status=job.status, expires_at=job.expires_at))


def _customer_job(db, job_id: str, actor: Actor) -> ExportJob:
    require_customer(actor, verified=True)
    job = _load_job(db, job_id)
    _assert_audience(job, 'CUSTOMER')
    if job.account_id != actor.account_id:
        raise DomainError('NOT_FOUND', '导出任务不存在或不可访问', 404)
    return job


@router.get('/customer/account/exports/{job_id}', response_model=Envelope[ExportJobView])
def customer_export_status(job_id: str, db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    job = _customer_job(db, job_id, actor)
    if job.status == 'QUEUED':
        _enqueue_export(db, job)
    return ok(_job_view(job))


@router.get('/customer/account/exports/{job_id}/download')
def customer_export_download(job_id: str, db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    job = _customer_job(db, job_id, actor)
    return _download(db, job, actor)


def _download(db, job: ExportJob, actor: Actor) -> FileResponse:
    now = utcnow()
    if job.status == 'EXPIRED' or job.expires_at <= now:
        raise DomainError('EXPORT_EXPIRED', '导出文件已到期，请重新申请', 410)
    if job.status != 'READY':
        raise DomainError('EXPORT_NOT_READY', '导出文件尚未生成完成', 409)
    if not job.storage_key:
        raise DomainError('EXPORT_FILE_MISSING', '导出文件已不可用，请重新申请', 410)
    path = storage_path(job.storage_key)
    if not path.is_file():
        raise DomainError('EXPORT_FILE_MISSING', '导出文件已不可用，请重新申请', 410)
    audit(db, actor, 'EXPORT_DOWNLOADED', 'EXPORT_JOB', job.id,
          data={'kind': job.kind, 'audience': _audience(job)})
    return FileResponse(
        path,
        media_type='text/csv; charset=utf-8',
        filename=f'dormprint-{job.kind.lower()}-{job.id[:8]}.csv',
        headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                 'Referrer-Policy': 'no-referrer'},
    )
