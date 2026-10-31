"""Controlled deletion, 24h trash, account deletion and lifecycle maintenance.

Only an explicit object registry is accepted, never a caller-provided table or
SQL fragment.  Targets without a ``deleted_at`` column are masked by the
``TrashEntry`` lifecycle and remain untouched until a real purge, so restore
never has to recreate business facts.  The purge processor unlinks private
files first, then deletes the database graph, and only reports ``PURGED`` after
both steps succeeded; failures leave a retryable ``PURGE_PENDING`` state.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, delete, func, or_, select, update

from .config import get_config
from .core import (
    Actor,
    DomainError,
    Envelope,
    actor_dep,
    audit,
    enqueue_job,
    lock,
    ok,
    require_admin,
    require_customer,
    touch,
)
from .data_ops_schemas import (
    AuditLogList,
    AuditLogView,
    DeletionApproveInput,
    DeletionRequestList,
    DeletionRequestView,
    RestorePreview,
    TrashActionInput,
    TrashConflict,
    TrashCreateInput,
    TrashItemView,
    TrashList,
    TrashPreviewInput,
)
from .db import get_db
from .exports import (
    _mask_email,
    purge_storage_key,
    storage_path,
)
from .storage_coordination import (
    purge_guard,
    purge_storage_key_locked,
    purge_storage_tree_locked,
    record_purged_files,
    recover_orphaned_staging,
    recover_purge_cleanup,
    storage_gc_guard,
)
from .models import (
    AccessGrant,
    Account,
    AfterSale,
    AnonymousLedger,
    Artifact,
    Asset,
    AuditLog,
    AuthSession,
    BindingTask,
    BusinessException,
    Campaign,
    Coupon,
    DeliveryAssignment,
    DeliveryBatch,
    DeliveryProof,
    DeletionRequest,
    EmailJob,
    ExportJob,
    Job,
    OneTimeToken,
    Order,
    OrderFile,
    OrderRevision,
    PackingTask,
    Payment,
    Preparation,
    Principal,
    PrintAttempt,
    PrintTask,
    PromotionEvent,
    Quote,
    Receipt,
    ReceiptAdjustment,
    Refund,
    Referral,
    Staff,
    SupplementaryCharge,
    SupplementaryDispute,
    SupplementaryMovement,
    TrashEntry,
    UploadSession,
    WageEntry,
    WageSettlementItem,
    utcnow,
)

router = APIRouter()

TERMINAL_ORDER_STATUSES = {'DELIVERED', 'COMPLETED', 'CLOSED', 'CANCELLED', 'CANCELED', 'REFUNDED'}
TERMINAL_AFTER_SALE_STATUSES = {'COMPLETED', 'CLOSED', 'REJECTED', 'CANCELLED', 'CANCELED', 'REFUNDED'}
ACTIVE_EXCEPTION_STATUSES = {'OPEN', 'IN_PROGRESS'}

TARGETS = {
    'ORDER': Order,
    'BATCH': DeliveryBatch,
    'ASSET': Asset,
    'ACCOUNT': Account,
    'PAYMENT': Payment,
    'RECEIPT': Receipt,
    'RECEIPT_ADJUSTMENT': ReceiptAdjustment,
    'REFUND': Refund,
    'AFTER_SALE': AfterSale,
    'COUPON': Coupon,
    'PROMOTION_EVENT': PromotionEvent,
    'CAMPAIGN': Campaign,
    'REFERRAL': Referral,
    'AUDIT_LOG': AuditLog,
    'EMAIL_JOB': EmailJob,
    'EXPORT_JOB': ExportJob,
    'PREPARATION': Preparation,
    'ARTIFACT': Artifact,
}
TARGET_ALIASES = {
    'CUSTOMER_ACCOUNT': 'ACCOUNT',
    'CUSTOMER': 'ACCOUNT',
    'DELIVERY_BATCH': 'BATCH',
    'PROMOTION': 'COUPON',
    'COUPON_GRANT': 'COUPON',
    'PROMOTION_RECORD': 'PROMOTION_EVENT',
    'PROMOTION_LEDGER': 'PROMOTION_EVENT',
    'CASH_RECEIPT': 'RECEIPT',
    'PAYMENT_RECORD': 'PAYMENT',
    'REFUND_RECORD': 'REFUND',
    'AFTER_SALE_CASE': 'AFTER_SALE',
    'MAIL_JOB': 'EMAIL_JOB',
    'AUDIT': 'AUDIT_LOG',
    'EXPORT': 'EXPORT_JOB',
}
TRASH_STATUSES = ('DELETED', 'PURGE_PENDING')
PUBLIC_PURPOSES = {'PUBLIC_WECHAT_QR', 'PUBLIC_MAP'}


def _now() -> datetime:
    return utcnow()


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _canonical_target(value: str) -> str:
    key = (value or '').strip().upper()
    key = TARGET_ALIASES.get(key, key)
    if key not in TARGETS:
        raise DomainError('UNKNOWN_TRASH_TARGET', '不支持的对象类型', 422,
                          {'targetType': value})
    return key


def _trash_hours(db) -> int:
    try:
        value = int(get_config(db, 'retention').data.get('trashHours', 24))
    except (TypeError, ValueError):
        value = 24
    return max(1, min(value, 24 * 30))


def _retention_days(db, key: str, fallback: int) -> int:
    try:
        value = int(get_config(db, 'retention').data.get(key, fallback))
    except (TypeError, ValueError):
        value = fallback
    return max(1, min(value, 3650))


# ---------------------------------------------------------------------------
# Views / target helpers
# ---------------------------------------------------------------------------


def _entry_view(entry: TrashEntry) -> TrashItemView:
    data = entry.data or {}
    return TrashItemView(
        id=entry.id,
        type=entry.target_type,
        name=data.get('name') or entry.target_id,
        deleted_at=entry.created_at,
        purge_at=entry.purge_at,
        reason=entry.reason,
        by=data.get('byName') or entry.actor_id,
        status=entry.status,
        version=entry.version,
    )


def _target_name(db, target_type: str, target) -> str:
    if target_type == 'ORDER':
        return f'订单 {target.pickup_code}'
    if target_type == 'BATCH':
        return target.name
    if target_type == 'ASSET':
        return target.name
    if target_type == 'ACCOUNT':
        return _mask_email(target.email)
    if target_type == 'COUPON':
        return f'优惠券 {target.id[:8]}'
    if target_type == 'PROMOTION_EVENT':
        return f"优惠记录 {target.kind}"
    if target_type == 'CAMPAIGN':
        return target.name
    if target_type == 'EMAIL_JOB':
        return f'邮件任务 {target.template_key}'
    if target_type == 'AUDIT_LOG':
        return f'审计记录 {target.action}'
    if target_type == 'EXPORT_JOB':
        return f'导出文件 {target.kind}'
    if target_type == 'PREPARATION':
        return f'草稿 {target.name if hasattr(target, "name") else target.id[:8]}'
    if target_type == 'ARTIFACT':
        return f'成品文件 {target.id[:8]}'
    if target_type == 'PAYMENT':
        return f'收款记录 {target.id[:8]}'
    if target_type == 'RECEIPT':
        return f'现金收款 {target.id[:8]}'
    if target_type == 'REFUND':
        return f'退款记录 {target.id[:8]}'
    if target_type == 'AFTER_SALE':
        return f'售后记录 {target.id[:8]}'
    return f'{target_type} {target.id[:8]}'


def _actor_name(actor: Actor) -> str:
    return f'staff:{actor.staff_id}' if actor.staff_id else actor.id


def _active_after_sales(db, order_ids) -> list[AfterSale]:
    if not order_ids:
        return []
    return list(db.scalars(select(AfterSale).where(
        AfterSale.order_id.in_(order_ids),
        AfterSale.status.notin_(TERMINAL_AFTER_SALE_STATUSES))))


def _open_exceptions(db, order_ids) -> list[BusinessException]:
    if not order_ids:
        return []
    return list(db.scalars(select(BusinessException).where(
        BusinessException.order_id.in_(order_ids),
        BusinessException.status.in_(ACTIVE_EXCEPTION_STATUSES))))


def _unmatched_receipt_activity(db, order_id: str) -> list[tuple[Receipt, int]]:
    """Return cash receipts that never became a settled Payment.

    An UNMATCHED receipt is cash physically held outside the order ledger.  A
    fully refunded receipt becomes REFUNDED but still has no Payment; retaining
    its gross/refund facts lets the anonymous ledger keep a separate audit
    trail instead of pretending the original Payment cash was received.
    """
    rows: list[tuple[Receipt, int]] = []
    for receipt in db.scalars(select(Receipt).where(
            Receipt.order_id == order_id,
            Receipt.status.in_(('UNMATCHED', 'REFUNDED'))
    ).order_by(Receipt.id)):
        if db.scalar(select(Payment.id).where(
                Payment.receipt_id == receipt.id).limit(1)):
            continue
        refunded = db.scalar(select(func.coalesce(func.sum(
            ReceiptAdjustment.amount_cents), 0)).where(
            ReceiptAdjustment.receipt_id == receipt.id)) or 0
        rows.append((receipt, int(refunded)))
    return rows


def _order_active_blockers(db, order: Order) -> list[str]:
    blockers: list[str] = []
    for receipt, refunded in _unmatched_receipt_activity(db, order.id):
        if receipt.amount_cents - refunded > 0:
            blockers.append(f'UNMATCHED_RECEIPT:{receipt.id}')
    if order.settlement_kind:
        if order.status not in TERMINAL_ORDER_STATUSES:
            blockers.append('ACTIVE_FULFILLMENT')
        elif order.status in ('DELIVERED', 'COMPLETED'):
            # ``DELIVERED`` is a fact only when the operational records agree;
            # a reopened print/binding/packing/delivery task is unfinished work.
            if db.scalar(select(DeliveryAssignment.id).where(
                    DeliveryAssignment.order_id == order.id,
                    DeliveryAssignment.active.is_(True),
                    DeliveryAssignment.status != 'DELIVERED').limit(1)):
                blockers.append('DELIVERY_NOT_COMPLETED')
            if db.scalar(select(PrintTask.id).where(
                    PrintTask.order_id == order.id, PrintTask.active.is_(True),
                    PrintTask.status != 'COMPLETED').limit(1)):
                blockers.append('PRINT_NOT_COMPLETED')
            if db.scalar(select(BindingTask.id).where(
                    BindingTask.order_id == order.id,
                    BindingTask.status.notin_(('COMPLETED', 'NOT_REQUIRED'))).limit(1)):
                blockers.append('BINDING_NOT_COMPLETED')
            if db.scalar(select(PackingTask.id).where(
                    PackingTask.order_id == order.id,
                    PackingTask.status != 'COMPLETED').limit(1)):
                blockers.append('PACKING_NOT_COMPLETED')
    elif order.status not in ('CANCELLED', 'CANCELED'):
        blockers.append('ACTIVE_ORDER')
    if order.refund_locked:
        blockers.append('REFUND_REVIEW_PENDING')
    if _active_after_sales(db, [order.id]):
        blockers.append('ACTIVE_AFTER_SALE')
    if _open_exceptions(db, [order.id]):
        blockers.append('OPEN_BUSINESS_EXCEPTION')
    assignment_ids = list(db.scalars(select(DeliveryAssignment.id).where(
        DeliveryAssignment.order_id == order.id)))
    blockers.extend(_unsettled_wage_blockers(
        db, order_ids=[order.id], assignment_ids=assignment_ids))
    # Supplementary fees are append-only financial facts.  An ACTIVE charge or
    # an open dispute is still live business and must not be cascaded away.
    for charge_id in db.scalars(select(SupplementaryCharge.id).where(
            SupplementaryCharge.order_id == order.id,
            SupplementaryCharge.status == 'ACTIVE').order_by(SupplementaryCharge.id)):
        blockers.append(f'SUPPLEMENTARY_ACTIVE:{charge_id}')
    for dispute_id in db.scalars(select(SupplementaryDispute.id).join(
            SupplementaryCharge, SupplementaryCharge.id == SupplementaryDispute.charge_id)
            .where(SupplementaryCharge.order_id == order.id,
                   SupplementaryDispute.status.in_(('OPEN', 'IN_PROGRESS')))
            .order_by(SupplementaryDispute.id)):
        blockers.append(f'SUPPLEMENTARY_DISPUTE_OPEN:{dispute_id}')
    return blockers


def _asset_referenced(db, asset_id: str) -> bool:
    checks = (
        select(OrderFile.id).where(OrderFile.asset_id == asset_id),
        select(Artifact.id).where(Artifact.asset_id == asset_id),
        select(DeliveryProof.id).where(DeliveryProof.photo_asset_id == asset_id),
        select(UploadSession.id).where(UploadSession.asset_id == asset_id),
        select(Asset.id).where(Asset.normalized_asset_id == asset_id),
    )
    return any(db.scalar(query.limit(1)) for query in checks)


def _account_principal_ids(db, account: Account) -> set[str]:
    ids = {account.principal_id}
    ids.update(db.scalars(select(Principal.id).where(Principal.account_id == account.id)))
    return {value for value in ids if value}


def _account_orders(db, account: Account) -> list[Order]:
    principal_ids = _account_principal_ids(db, account)
    conditions = [Order.checkout_account_id == account.id]
    if principal_ids:
        conditions.append(Order.principal_id.in_(principal_ids))
    return list(db.scalars(select(Order).where(or_(*conditions))))


def _linked_staff(db, account: Account) -> Staff | None:
    """Staff privileges now derive from the base customer account."""
    return db.scalar(select(Staff).where(Staff.account_id == account.id))


def _super_admins(db, exclude_staff_id: str | None = None) -> list[Staff]:
    """Return (and lock) every super admin, including disabled rows.

    R04 never permits deleting the last super-admin identity.  A disabled
    super-admin still owns that authority until another one is created, so it
    must not be anonymised away.  Locking all rows in primary-key order makes
    concurrent account deletions serialize without deadlocking on each other's
    linked Staff row.
    """
    query = select(Staff).where(
        Staff.is_super_admin.is_(True),
    ).order_by(Staff.id).with_for_update()
    rows = list(db.scalars(query))
    if exclude_staff_id:
        rows = [row for row in rows if row.id != exclude_staff_id]
    return rows


def _last_super_admin_blockers(db, staff: Staff | None) -> list[str]:
    if staff is None or not staff.is_super_admin:
        return []
    if _super_admins(db, exclude_staff_id=staff.id):
        return []
    return [f'LAST_SUPER_ADMIN:{staff.id}']


def _wage_entry_is_settled(db, entry_id: str) -> bool:
    return db.scalar(select(WageSettlementItem.id).where(
        WageSettlementItem.entry_id == entry_id).limit(1)) is not None


def _unsettled_wage_blockers(
    db, *, order_ids=(), assignment_ids=(), staff_id: str | None = None,
) -> list[str]:
    """Wage entries not covered by a settlement still owe money.

    Destructive cleanup must stop before touching payroll rows or the orders /
    assignments they reference.  Settled entries are handled by detaching their
    nullable FKs while keeping the immutable wage snapshot.
    """
    scope = []
    if order_ids:
        scope.append(WageEntry.order_id.in_(list(order_ids)))
    if assignment_ids:
        scope.append(WageEntry.assignment_id.in_(list(assignment_ids)))
    if staff_id:
        scope.append(WageEntry.staff_id == staff_id)
    if not scope:
        return []
    settled_entry_ids = select(WageSettlementItem.entry_id)
    rows = db.scalars(select(WageEntry.id).where(
        or_(*scope), WageEntry.id.notin_(settled_entry_ids)).order_by(WageEntry.id))
    return [f'WAGE_UNSETTLED:{entry_id}' for entry_id in rows]


def _account_deletion_blockers(db, account: Account) -> list[str]:
    blockers: list[str] = []
    staff = _linked_staff(db, account)
    blockers.extend(_last_super_admin_blockers(db, staff))
    if staff is not None and (staff.enabled or staff.status != 'DISABLED'):
        blockers.append(f'STAFF_ACTIVE:{staff.id}')
    orders = _account_orders(db, account)
    for order in orders:
        if order.deleted_at is None and order.status not in TERMINAL_ORDER_STATUSES:
            blockers.append(f'ORDER_ACTIVE:{order.id}')
        if order.refund_locked:
            blockers.append(f'REFUND_REVIEW:{order.id}')
    order_ids = [order.id for order in orders]
    assignment_ids = list(db.scalars(select(DeliveryAssignment.id).where(
        DeliveryAssignment.order_id.in_(order_ids)))) if order_ids else []
    blockers.extend(_unsettled_wage_blockers(
        db, order_ids=order_ids, assignment_ids=assignment_ids,
        staff_id=staff.id if staff is not None else None))
    for sale in _active_after_sales(db, order_ids):
        blockers.append(f'AFTER_SALE_ACTIVE:{sale.id}')
    for exception in _open_exceptions(db, order_ids):
        blockers.append(f'BUSINESS_EXCEPTION_OPEN:{exception.id}')
    return blockers


def _delete_blockers(db, target_type: str, target) -> list[str]:
    blockers: list[str] = []
    if target_type == 'ORDER':
        if target.deleted_at is not None:
            return ['ALREADY_DELETED']
        return _order_active_blockers(db, target)
    if target_type == 'BATCH':
        if target.deleted_at is not None:
            return ['ALREADY_DELETED']
        if target.status == 'DELIVERING':
            blockers.append('ACTIVE_DELIVERY')
        assignments = list(db.scalars(select(DeliveryAssignment).where(
            DeliveryAssignment.batch_id == target.id,
            DeliveryAssignment.active.is_(True))))
        if any(item.status == 'STARTED' for item in assignments):
            blockers.append('BATCH_HAS_STARTED_EXECUTIONS')
        assignment_ids = [item.id for item in assignments]
        if assignment_ids:
            blockers.extend(_unsettled_wage_blockers(db, assignment_ids=assignment_ids))
        return blockers
    if target_type == 'ASSET':
        if target.deleted_at is not None:
            return ['ALREADY_DELETED']
        if _asset_referenced(db, target.id):
            blockers.append('ASSET_IN_USE')
        return blockers
    if target_type == 'ACCOUNT':
        if target.deleted_at is not None:
            return ['ALREADY_DELETED']
        blockers.extend(_account_deletion_blockers(db, target))
        if db.scalar(select(DeletionRequest.id).where(
                DeletionRequest.account_id == target.id,
                DeletionRequest.status.in_(('REQUESTED', 'PENDING_REVIEW'))).limit(1)):
            blockers.append('DELETION_REQUEST_PENDING')
        return blockers
    if target_type == 'PAYMENT':
        order = db.get(Order, target.order_id)
        if order is not None and order.deleted_at is None:
            blockers.append('ORDER_REFERENCE')
    elif target_type == 'RECEIPT':
        if target.status != 'UNMATCHED':
            blockers.append('RECEIPT_ALLOCATED')
        if db.scalar(select(Payment.id).where(Payment.receipt_id == target.id).limit(1)):
            blockers.append('PAYMENT_REFERENCE')
    elif target_type == 'RECEIPT_ADJUSTMENT':
        blockers.append('RECEIPT_REFERENCE')
    elif target_type == 'REFUND':
        sale = db.get(AfterSale, target.after_sale_id)
        if sale is not None and sale.status not in TERMINAL_AFTER_SALE_STATUSES:
            blockers.append('AFTER_SALE_ACTIVE')
    elif target_type == 'AFTER_SALE':
        if target.status not in TERMINAL_AFTER_SALE_STATUSES:
            blockers.append('AFTER_SALE_ACTIVE')
        if db.scalar(select(Refund.id).where(Refund.after_sale_id == target.id).limit(1)):
            blockers.append('REFUND_REFERENCE')
    elif target_type == 'COUPON':
        if db.scalar(select(PromotionEvent.id).where(
                PromotionEvent.coupon_id == target.id).limit(1)):
            blockers.append('PROMOTION_EVENT_REFERENCE')
        for order_id in (target.reserved_order_id, target.used_order_id):
            order = db.get(Order, order_id) if order_id else None
            if order is not None and order.deleted_at is None:
                blockers.append('ORDER_REFERENCE')
    elif target_type == 'CAMPAIGN':
        if db.scalar(select(Coupon.id).where(Coupon.campaign_id == target.id).limit(1)) or \
                db.scalar(select(PromotionEvent.id).where(
                    PromotionEvent.campaign_id == target.id).limit(1)):
            blockers.append('PROMOTION_REFERENCE')
    elif target_type == 'REFERRAL':
        if target.qualified_order_id:
            order = db.get(Order, target.qualified_order_id)
            if order is not None and order.deleted_at is None:
                blockers.append('ORDER_REFERENCE')
    elif target_type == 'PREPARATION':
        if db.scalar(select(Order.id).where(Order.preparation_id == target.id).limit(1)) or \
                db.scalar(select(OrderRevision.id).where(
                    OrderRevision.preparation_id == target.id).limit(1)):
            blockers.append('ORDER_REFERENCE')
        if db.scalar(select(PrintAttempt.id).where(PrintAttempt.artifact_id.in_(
                select(Artifact.id).where(Artifact.preparation_id == target.id))).limit(1)):
            blockers.append('PRINT_ATTEMPT_REFERENCE')
    elif target_type == 'ARTIFACT':
        if db.scalar(select(PrintTask.id).where(PrintTask.artifact_id == target.id).limit(1)):
            blockers.append('PRINT_TASK_REFERENCE')
        if db.scalar(select(PrintAttempt.id).where(
                PrintAttempt.artifact_id == target.id).limit(1)):
            blockers.append('PRINT_ATTEMPT_REFERENCE')
    return blockers


def _soft_delete(db, target_type: str, target, now: datetime) -> None:
    if target_type == 'ORDER':
        target.deleted_at = now
        target.purge_at = now + timedelta(hours=_trash_hours(db))
        touch(target)
    elif target_type in ('BATCH', 'ASSET', 'ACCOUNT'):
        target.deleted_at = now
        touch(target)


# ---------------------------------------------------------------------------
# Trash routes
# ---------------------------------------------------------------------------


@router.get('/admin/trash', response_model=Envelope[TrashList])
def list_trash(cursor: str | None = None, limit: int = Query(default=50, ge=1, le=100),
               db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'data.manage')
    query = select(TrashEntry).where(TrashEntry.status.in_(TRASH_STATUSES))
    if cursor:
        anchor = db.get(TrashEntry, cursor)
        if anchor is not None:
            query = query.where(or_(
                TrashEntry.created_at < anchor.created_at,
                and_(TrashEntry.created_at == anchor.created_at, TrashEntry.id < anchor.id)))
    rows = list(db.scalars(query.order_by(
        TrashEntry.created_at.desc(), TrashEntry.id.desc()).limit(limit + 1)))
    items = [_entry_view(row) for row in rows[:limit]]
    return ok(TrashList(items=items, next_cursor=rows[limit - 1].id if len(rows) > limit else None))


def _release_batch_orders(db, batch: DeliveryBatch, now: datetime) -> list[str]:
    """Detach active assignments so a trashed batch no longer holds orders.

    Finished deliveries stay untouched: their proof is the payroll and audit
    evidence.  Everything else returns to the ready-for-delivery pool.
    """
    released: list[str] = []
    assignments = list(db.scalars(select(DeliveryAssignment).where(
        DeliveryAssignment.batch_id == batch.id,
        DeliveryAssignment.active.is_(True)).with_for_update()))
    for assignment in assignments:
        if assignment.status == 'DELIVERED':
            continue
        assignment.active = False
        assignment.status = 'CANCELLED'
        touch(assignment)
        released.append(assignment.order_id)
        order = db.get(Order, assignment.order_id)
        if order is not None and order.status in ('READY_FOR_DELIVERY', 'DELIVERY_EXCEPTION'):
            order.status = 'READY_FOR_DELIVERY'
            order.data = {**(order.data or {}),
                          'deliveryReleasedAt': now.isoformat(),
                          'lastBatchId': batch.id}
            touch(order)
    return released


def move_to_trash(db, actor: Actor, target_type: str, target, reason: str,
                  extra: dict | None = None) -> TrashEntry:
    """Soft delete a locked target and record a recoverable trash entry.

    Callers must already hold the row lock (``lock``/``_lock_batch_context``)
    and have checked the capability they need.  Only ``deleted_at`` changes on
    the target so restoring it is lossless.
    """
    target_type = _canonical_target(target_type)
    existing = db.scalar(select(TrashEntry).where(
        TrashEntry.target_type == target_type, TrashEntry.target_id == target.id,
        TrashEntry.status.in_(TRASH_STATUSES)))
    if existing is not None:
        raise DomainError('TRASH_ALREADY_EXISTS', '该对象已在回收站中', 409,
                          {'trashId': existing.id})
    blockers = _delete_blockers(db, target_type, target)
    if blockers:
        raise DomainError('TRASH_BLOCKED', '对象仍存在有效引用或未结业务，不能删除', 409,
                          {'blockers': blockers, 'targetType': target_type,
                           'targetId': target.id})
    name = _target_name(db, target_type, target)
    now = _now()
    data = {'name': name, 'byName': _actor_name(actor), 'actorId': actor.id,
            'targetVersion': target.version, 'restorable': True,
            'deletedAt': now.isoformat()}
    if extra:
        data.update(extra)
    if target_type == 'BATCH':
        data.setdefault('statusBefore', target.status)
        data['releasedOrderIds'] = _release_batch_orders(db, target, now)
    _soft_delete(db, target_type, target, now)
    entry = TrashEntry(
        target_type=target_type,
        target_id=target.id,
        actor_id=actor.id,
        reason=reason,
        status='DELETED',
        purge_at=now + timedelta(hours=_trash_hours(db)),
        data=data,
    )
    db.add(entry)
    db.flush()
    audit(db, actor, 'TRASH_CREATED', 'TRASH', entry.id,
          data={'targetType': target_type, 'targetId': target.id})
    return entry


@router.post('/admin/trash', response_model=Envelope[TrashItemView])
def create_trash(payload: TrashCreateInput, db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'data.manage')
    target_type = _canonical_target(payload.target_type)
    target = lock(db, TARGETS[target_type], payload.target_id, payload.expected_version)
    entry = move_to_trash(db, actor, target_type, target, payload.reason)
    return ok(_entry_view(entry))


def _restore_blockers(db, entry: TrashEntry) -> list[TrashConflict]:
    blockers: list[TrashConflict] = []
    now = _now()
    if _aware(entry.purge_at) is not None and _aware(entry.purge_at) <= now:
        blockers.append(TrashConflict(code='TRASH_EXPIRED', message='回收站保留期已结束，不能恢复'))
    if entry.status == 'PURGED':
        blockers.append(TrashConflict(code='TRASH_PURGED', message='对象已永久清理'))
    elif entry.status == 'RESTORED':
        blockers.append(TrashConflict(code='TRASH_ALREADY_RESTORED', message='对象已经恢复'))
    elif entry.status == 'PURGE_PENDING':
        blockers.append(TrashConflict(code='PURGE_PENDING', message='对象正在清理，不能恢复'))
    data = entry.data or {}
    if data.get('restorable') is False:
        blockers.append(TrashConflict(code='DELETION_FINAL', message='该对象属于客户注销清理，不能恢复'))
    try:
        target_type = _canonical_target(entry.target_type)
    except DomainError:
        blockers.append(TrashConflict(code='TARGET_UNKNOWN', message='对象类型不再受支持'))
        return blockers
    target = db.get(TARGETS[target_type], entry.target_id)
    if target is None:
        blockers.append(TrashConflict(code='TARGET_MISSING', message='对象已不在数据库中存在'))
        return blockers
    if target_type in ('ORDER', 'BATCH', 'ASSET', 'ACCOUNT'):
        if getattr(target, 'deleted_at', None) is None:
            blockers.append(TrashConflict(code='TARGET_NOT_TRASHED', message='对象状态已变化，请刷新'))
    if target_type == 'ASSET':
        try:
            path = storage_path(target.storage_key)
        except DomainError:
            path = None
        if path is None or not path.is_file():
            blockers.append(TrashConflict(code='STORAGE_MISSING', message='原文件已不可用，恢复后将缺少内容'))
    return blockers


def _restore_preview(db, actor: Actor, entry_id: str, expected_version: int | None):
    require_admin(actor, 'data.manage')
    entry = lock(db, TrashEntry, entry_id, expected_version)
    blockers = _restore_blockers(db, entry)
    return ok(RestorePreview(entry=_entry_view(entry), restorable=not blockers, blockers=blockers))


@router.post('/admin/trash/{entry_id}/restore-preview', response_model=Envelope[RestorePreview])
def restore_preview(entry_id: str, payload: TrashPreviewInput, db=Depends(get_db),
                    actor: Actor = Depends(actor_dep)):
    return _restore_preview(db, actor, entry_id, payload.expected_version)


@router.get('/admin/trash/{entry_id}/restore-preview', response_model=Envelope[RestorePreview])
def restore_preview_get(entry_id: str, expectedVersion: int | None = None,
                        db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    return _restore_preview(db, actor, entry_id, expectedVersion)


@router.post('/admin/trash/{entry_id}/restore', response_model=Envelope[TrashItemView])
def restore_trash(entry_id: str, payload: TrashActionInput, db=Depends(get_db),
                  actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'data.manage')
    entry = lock(db, TrashEntry, entry_id, payload.expected_version)
    if _aware(entry.purge_at) <= _now():
        raise DomainError('TRASH_EXPIRED', '回收站保留期已结束，不能恢复', 410,
                          {'trashId': entry.id})
    blockers = _restore_blockers(db, entry)
    if blockers:
        raise DomainError('RESTORE_CONFLICT', '恢复存在冲突，请先处理', 409,
                          {'blockers': [item.code for item in blockers],
                           'messages': {item.code: item.message for item in blockers}})
    target_type = _canonical_target(entry.target_type)
    target = db.get(TARGETS[target_type], entry.target_id)
    if target is None:
        raise DomainError('TARGET_MISSING', '对象已不存在，不能恢复', 404)
    if target_type in ('ORDER', 'BATCH', 'ASSET', 'ACCOUNT'):
        target.deleted_at = None
        if target_type == 'ORDER':
            target.purge_at = None
        if target_type == 'BATCH':
            status_before = (entry.data or {}).get('statusBefore')
            if status_before and target.status == 'CANCELLED':
                target.status = status_before
        touch(target)
    entry.status = 'RESTORED'
    data = dict(entry.data or {})
    data.update(restoredAt=_now().isoformat(), restoredBy=actor.id, restoreReason=payload.reason)
    entry.data = data
    touch(entry)
    audit(db, actor, 'TRASH_RESTORED', target_type, target.id, reason=payload.reason,
          data={'trashId': entry.id})
    return ok(_entry_view(entry))


@router.post('/admin/trash/{entry_id}/purge', response_model=Envelope[TrashItemView])
def purge_trash(entry_id: str, payload: TrashActionInput, db=Depends(get_db),
                actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'data.manage')
    # Read without a row lock first.  This is only an inexpensive early 404/
    # version check; the authoritative check and every mutation happen in
    # process_purge_job after the storage maintenance lock and the Trash row
    # lock are both held.  In particular this route must not mutate
    # entry.data or enqueue a Job here: the dirty Trash row/enqueue flush
    # would otherwise take the database write lock before the file lock.
    entry = db.get(TrashEntry, entry_id)
    if entry is None:
        raise DomainError('NOT_FOUND', '对象不存在或不可访问', 404)
    if payload.expected_version is not None and entry.version != payload.expected_version:
        raise DomainError('VERSION_CONFLICT', '数据已变化，请刷新后重试', 409,
                          {'currentVersion': entry.version})
    if entry.status == 'RESTORED':
        raise DomainError('TRASH_RESTORED', '对象已恢复，不能清理', 409)
    process_purge_job(
        db,
        entry.id,
        expected_version=payload.expected_version,
        reason=payload.reason,
    )
    # purge_guard stages regular files before the body reports PURGED.  Their
    # physical GC (and any downgrade to PURGE_PENDING) happens in the commit
    # callback, after this response is serialized.  Never tell the client
    # PURGED while owned staged files still exist; a reload after commit sees
    # the authoritative PURGED or PURGE_PENDING status.
    view = _entry_view(entry)
    data = entry.data or {}
    if data.get('cleanupPending') or data.get('stagedFiles'):
        view = view.model_copy(update={'status': 'PURGE_PENDING'})
    return ok(view)


# ---------------------------------------------------------------------------
# Audit / deletion request routes
# ---------------------------------------------------------------------------


SENSITIVE_KEYS = ('password', 'token', 'secret', 'cookie', 'authorization', 'payload',
                  'content', 'phone', 'email', 'contact', 'url')


def _redact(value):
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items()
                if not any(part in str(key).lower() for part in SENSITIVE_KEYS)}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _audit_actor_labels(db, rows):
    """Resolve safe display labels from real Staff/Account facts only.

    Never expose passwords, tokens, email, phone or document content.  Unknown
    actor ids get a neutral label while the immutable actorId stays present.
    """
    actor_ids = {str(row.actor_id) for row in rows if row.actor_id}
    staff_ids = set()
    account_ids = set()
    principal_ids = set()
    for actor_id in actor_ids:
        if actor_id.startswith('account:'):
            account_ids.add(actor_id.split(':', 1)[1])
        elif actor_id.startswith('staff:'):
            staff_ids.add(actor_id.split(':', 1)[1])
        elif actor_id == 'system' or actor_id.startswith('system:'):
            continue
        else:
            # Actor.id is either a Staff id or a Principal id.  A raw account
            # id is not used by core.audit; account ids are prefixed.
            staff_ids.add(actor_id)
            principal_ids.add(actor_id)
    staff = {row.id: row for row in db.scalars(
        select(Staff).where(Staff.id.in_(staff_ids)))} if staff_ids else {}
    principals = {row.id: row for row in db.scalars(
        select(Principal).where(Principal.id.in_(principal_ids)))} if principal_ids else {}
    principal_account_ids = {
        row.account_id for row in principals.values() if row.account_id}
    account_rows = list(db.scalars(select(Account).where(or_(
        Account.id.in_(account_ids | principal_account_ids),
        Account.principal_id.in_(principal_ids))))) if (
            account_ids or principal_account_ids or principal_ids) else []
    accounts = {row.id: row for row in account_rows}
    accounts_by_principal = {row.principal_id: row for row in account_rows}
    labels = {}
    for actor_id in actor_ids:
        member = staff.get(actor_id)
        if member is None and actor_id.startswith('staff:'):
            member = staff.get(actor_id.split(':', 1)[1])
        if member is not None:
            labels[actor_id] = (member.name or '员工', member.role)
            continue
        if actor_id.startswith('account:'):
            account = accounts.get(actor_id.split(':', 1)[1])
            if account is not None:
                labels[actor_id] = (account.display_name or '客户', 'CUSTOMER')
                continue
        principal = principals.get(actor_id)
        if principal is not None:
            account = (accounts_by_principal.get(principal.id)
                       or accounts.get(principal.account_id or ''))
            if account is not None:
                labels[actor_id] = (account.display_name or '客户', 'CUSTOMER')
            else:
                labels[actor_id] = ('客户', 'CUSTOMER')
            continue
        if actor_id == 'system' or actor_id.startswith('system:'):
            labels[actor_id] = ('系统', 'SYSTEM')
        else:
            labels[actor_id] = ('未知操作人', None)
    return labels


@router.get('/admin/audit-logs', response_model=Envelope[AuditLogList])
def list_audit_logs(action: str | None = None, targetType: str | None = None,
                    targetId: str | None = None, actorId: str | None = None,
                    createdFrom: datetime | None = None,
                    createdTo: datetime | None = None,
                    q: str | None = Query(default=None, max_length=200),
                    cursor: str | None = None,
                    limit: int = Query(default=50, ge=1, le=100),
                    db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'audit.view')
    createdFrom, createdTo = _aware(createdFrom), _aware(createdTo)
    if createdFrom is not None and createdTo is not None and createdFrom > createdTo:
        raise DomainError('DATE_RANGE_INVALID', '开始时间不能晚于结束时间', 422)
    query = select(AuditLog)
    hidden = select(TrashEntry.target_id).where(
        TrashEntry.target_type == 'AUDIT_LOG', TrashEntry.status.in_(TRASH_STATUSES))
    query = query.where(AuditLog.id.notin_(hidden))
    if action:
        query = query.where(AuditLog.action == action)
    if targetType:
        query = query.where(AuditLog.target_type == targetType)
    if targetId:
        query = query.where(AuditLog.target_id == targetId)
    if q:
        needle = q.strip()
        if needle:
            pattern = f'%{needle}%'
            query = query.where(or_(
                AuditLog.action.ilike(pattern),
                AuditLog.target_type.ilike(pattern),
                AuditLog.target_id.ilike(pattern),
                AuditLog.reason.ilike(pattern)))
    if actorId:
        query = query.where(AuditLog.actor_id == actorId)
    if createdFrom is not None:
        query = query.where(AuditLog.created_at >= _aware(createdFrom))
    if createdTo is not None:
        query = query.where(AuditLog.created_at <= _aware(createdTo))
    if cursor:
        anchor = db.get(AuditLog, cursor)
        if anchor is None:
            raise DomainError('CURSOR_INVALID', '审计游标无效', 422)
        query = query.where(or_(
            AuditLog.created_at < anchor.created_at,
            and_(AuditLog.created_at == anchor.created_at, AuditLog.id < anchor.id)))
    rows = list(db.scalars(query.order_by(
        AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit + 1)))
    visible = rows[:limit]
    labels = _audit_actor_labels(db, visible)
    items = []
    for row in visible:
        label, role = labels.get(row.actor_id, ('未知操作人', None))
        items.append(AuditLogView(
            id=row.id, actor_id=row.actor_id, actor_label=label, actor_role=role,
            action=row.action, target_type=row.target_type, target_id=row.target_id,
            reason=row.reason, request_id=row.request_id,
            created_at=row.created_at, data=_redact(row.data or {})))
    return ok(AuditLogList(items=items,
                           next_cursor=rows[limit - 1].id if len(rows) > limit else None))


def _requested_at(request: DeletionRequest) -> datetime | None:
    raw = (request.data or {}).get('requestedAt')
    if raw:
        try:
            value = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
            return _aware(value)
        except ValueError:
            return request.created_at
    return request.created_at


def _reviewed_at(request: DeletionRequest) -> datetime | None:
    raw = (request.data or {}).get('reviewedAt')
    if not raw:
        return None
    try:
        return _aware(datetime.fromisoformat(str(raw).replace('Z', '+00:00')))
    except ValueError:
        return None


def _recovery_until(request: DeletionRequest) -> datetime | None:
    raw = (request.data or {}).get('recoveryUntil')
    if not raw:
        return None
    try:
        return _aware(datetime.fromisoformat(str(raw).replace('Z', '+00:00')))
    except ValueError:
        return None


def _deletion_view(db, request: DeletionRequest) -> DeletionRequestView:
    account = db.get(Account, request.account_id)
    data = request.data or {}
    blockers: list[str] = []
    if request.status == 'BLOCKED':
        # The blocker codes are stored by the self-service lifecycle.  Keep
        # them machine-readable; the frontend owns their human labels.
        blockers = [str(value) for value in (data.get('blockers') or [])]
    elif request.status == 'RECOVERY':
        # A finalization attempt can be blocked by a reopened order or a new
        # financial duty; keep those durable facts visible while the window is
        # still recoverable.
        blockers = [str(value) for value in (data.get('finalizeBlockers') or [])]
    elif account is not None and request.status == 'REQUESTED' and account.deleted_at is None:
        blockers = _account_deletion_blockers(db, account)
    recovery_until = _recovery_until(request)
    return DeletionRequestView(
        id=request.id,
        version=request.version,
        status=request.status,
        account_masked=('账号 ' + request.account_id[:8]) if request.account_id else '—',
        email_masked=_mask_email(account.email) if account else '—',
        display_name=(account.display_name if account else '') or '',
        reason=data.get('reason', ''),
        created_at=request.created_at,
        requested_at=_requested_at(request),
        reviewed_at=_reviewed_at(request),
        blockers=blockers,
        recovery_until=recovery_until,
        can_recover=(request.status == 'RECOVERY' and recovery_until is not None
                     and recovery_until > _now()),
    )


@router.get('/admin/deletion-requests', response_model=Envelope[DeletionRequestList])
def admin_deletion_requests(cursor: str | None = None,
                            limit: int = Query(default=50, ge=1, le=100),
                            db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'customer.manage')
    query = select(DeletionRequest).order_by(
        DeletionRequest.created_at.desc(), DeletionRequest.id.desc())
    if cursor:
        anchor = db.get(DeletionRequest, cursor)
        if anchor is not None:
            query = query.where(or_(
                DeletionRequest.created_at < anchor.created_at,
                and_(DeletionRequest.created_at == anchor.created_at,
                     DeletionRequest.id < anchor.id)))
    rows = list(db.scalars(query.limit(limit + 1)))
    items = [_deletion_view(db, row) for row in rows[:limit]]
    return ok(DeletionRequestList(
        items=items, next_cursor=rows[limit - 1].id if len(rows) > limit else None))


@router.get('/customer/account/deletion-requests', response_model=Envelope[DeletionRequestList])
def customer_deletion_requests(cursor: str | None = None,
                               limit: int = Query(default=50, ge=1, le=100),
                               db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_customer(actor, verified=True)
    query = select(DeletionRequest).where(
        DeletionRequest.account_id == actor.account_id).order_by(
        DeletionRequest.created_at.desc(), DeletionRequest.id.desc())
    if cursor:
        anchor = db.get(DeletionRequest, cursor)
        if anchor is not None:
            query = query.where(or_(
                DeletionRequest.created_at < anchor.created_at,
                and_(DeletionRequest.created_at == anchor.created_at,
                     DeletionRequest.id < anchor.id)))
    rows = list(db.scalars(query.limit(limit + 1)))
    items = [_deletion_view(db, row) for row in rows[:limit]]
    return ok(DeletionRequestList(
        items=items, next_cursor=rows[limit - 1].id if len(rows) > limit else None))


@router.post('/admin/deletion-requests/{request_id}/approve',
             response_model=Envelope[DeletionRequestView])
def approve_deletion_request(request_id: str, payload: DeletionApproveInput,
                             db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    # Account-deletion review is a customer lifecycle action, not the sensitive
    # business-data cleanup capability.  The internal purge worker below is not
    # called through this route and intentionally does not require data.manage.
    require_admin(actor, 'customer.manage')
    request = lock(db, DeletionRequest, request_id, payload.expected_version)
    if request.status != 'REQUESTED':
        recovery_until = _recovery_until(request)
        raise DomainError('INVALID_TRANSITION', '该注销申请已处理', 409,
                          {'status': request.status,
                           'recoveryUntil': recovery_until.isoformat() if recovery_until else None})
    account = lock(db, Account, request.account_id)
    if account.status == 'DELETION_PENDING' or account.deleted_at is not None:
        # A self-service window is already running (or completed).  The old
        # administrator-review endpoint must never shorten or bypass it.
        recovery_until = _recovery_until(request)
        raise DomainError('INVALID_TRANSITION', '该账号正在自助注销恢复期，不能由管理员提前批准', 409,
                          {'status': request.status, 'accountStatus': account.status,
                           'recoveryUntil': recovery_until.isoformat() if recovery_until else None})
    blockers = _account_deletion_blockers(db, account)
    if blockers:
        raise DomainError(
            'DELETION_BLOCKED',
            '存在未结订单、工资、售后或退款业务，或该账号是最后一个超级管理员，暂不能注销',
            409, {'blockers': blockers})
    existing = db.scalar(select(TrashEntry).where(
        TrashEntry.target_type == 'ACCOUNT', TrashEntry.target_id == account.id,
        TrashEntry.status.in_(TRASH_STATUSES)))
    if existing is not None:
        raise DomainError('TRASH_ALREADY_EXISTS', '该账号已在清理流程中', 409,
                          {'trashId': existing.id})
    now = _now()
    name = _mask_email(account.email)
    linked_staff = _linked_staff(db, account)
    disabled_staff_id = _disable_linked_staff(db, account, now)
    # Export artefacts are normal Q29 GC, not a manual Trash purge.  Stage
    # them under the same write lock so a backup cannot capture the old row
    # after the file disappeared and a rollback can restore it.
    with storage_gc_guard(db):
        counts = _revoke_account_credentials(db, account, now)
    anonymised_staff_id = _anonymise_linked_staff(db, linked_staff)
    _anonymise_account(db, account, now)
    request.status = 'APPROVED'
    data = dict(request.data or {})
    data.update(reviewedAt=now.isoformat(), reviewedBy=actor.id,
                decisionReason=payload.reason, blockers=[])
    request.data = data
    touch(request)
    entry = TrashEntry(
        target_type='ACCOUNT',
        target_id=account.id,
        actor_id=actor.id,
        reason=payload.reason,
        status='DELETED',
        purge_at=now + timedelta(hours=_trash_hours(db)),
        data={'name': name, 'byName': _actor_name(actor), 'actorId': actor.id,
              'restorable': False, 'deletionRequestId': request.id,
              'deletedAt': now.isoformat()},
    )
    db.add(entry)
    db.flush()
    audit(db, actor, 'ACCOUNT_DELETION_APPROVED', 'ACCOUNT', account.id,
          data={'deletionRequestId': request.id, 'trashId': entry.id,
                'revokedSessions': counts['sessions'], 'revokedGrants': counts['grants'],
                'destroyedTokens': counts['tokens'],
                'disabledStaffId': disabled_staff_id,
                'anonymisedStaffId': anonymised_staff_id})
    return ok(_deletion_view(db, request))


# ---------------------------------------------------------------------------
# Account credential revocation / anonymisation
# ---------------------------------------------------------------------------


def _revoke_account_credentials(db, account: Account, now: datetime) -> dict:
    principal_ids = _account_principal_ids(db, account)
    staff = _linked_staff(db, account)
    order_ids = [order.id for order in _account_orders(db, account)]
    grant_query = select(AccessGrant)
    conditions = [AccessGrant.account_id == account.id]
    if order_ids:
        conditions.append(AccessGrant.order_id.in_(order_ids))
    grants = list(db.scalars(grant_query.where(or_(*conditions))))
    grant_ids = {grant.id for grant in grants}
    # Include independently stored derived grants (source_grant_id chain) even
    # when their account relation is indirect.
    changed = True
    while changed:
        changed = False
        children = list(db.scalars(select(AccessGrant).where(
            AccessGrant.source_grant_id.in_(grant_ids)))) if grant_ids else []
        for child in children:
            if child.id not in grant_ids:
                grant_ids.add(child.id)
                grants.append(child)
                changed = True
    for grant in grants:
        if grant.revoked_at is None:
            grant.revoked_at = now
            touch(grant)
    session_conditions = [AuthSession.account_id == account.id]
    if principal_ids:
        session_conditions.append(AuthSession.principal_id.in_(principal_ids))
    if grant_ids:
        session_conditions.append(AuthSession.grant_id.in_(grant_ids))
    if staff is not None:
        session_conditions.append(AuthSession.staff_id == staff.id)
    sessions = list(db.scalars(select(AuthSession).where(or_(*session_conditions))))
    revoked = 0
    for session in sessions:
        if session.revoked_at is None:
            session.revoked_at = now
            session.version = (session.version or 0) + 1
            revoked += 1
    for job in db.scalars(select(ExportJob).where(
            ExportJob.account_id == account.id,
            ExportJob.status.in_(('QUEUED', 'PROCESSING', 'READY')))):
        if job.storage_key:
            try:
                purge_storage_key_locked(job.storage_key)
            except OSError:
                job_data = dict(job.data or {})
                job_data['cleanupPending'] = True
                job.data = job_data
            else:
                job.storage_key = None
        job.status = 'EXPIRED'
        job_data = dict(job.data or {})
        job_data['expiredAt'] = now.isoformat()
        job.data = job_data
        touch(job)
    token_conditions = [OneTimeToken.account_id == account.id]
    if principal_ids:
        token_conditions.append(OneTimeToken.session_id.in_(
            [session.id for session in sessions]))
    if staff is not None:
        token_conditions.append(OneTimeToken.staff_id == staff.id)
    destroyed = db.execute(delete(OneTimeToken).where(or_(*token_conditions))).rowcount or 0
    # A deleted account never keeps a valid guest session from a merged identity.
    if principal_ids:
        db.execute(update(AuthSession).where(
            AuthSession.kind == 'GUEST', AuthSession.principal_id.in_(principal_ids),
            AuthSession.revoked_at.is_(None)).values(revoked_at=now))
    return {'sessions': revoked, 'grants': len(grant_ids), 'tokens': destroyed}


def _disable_linked_staff(db, account: Account, now: datetime) -> str | None:
    """Account deletion also disables its linked Staff row.

    ``account_id`` is unique, so one account can own at most one Staff row.
    The caller has already rejected deletion when this row is the last active
    super admin.
    """
    staff = _linked_staff(db, account)
    if staff is None:
        return None
    changed = False
    if staff.enabled:
        staff.enabled = False
        changed = True
    if staff.status != 'DISABLED':
        staff.status = 'DISABLED'
        changed = True
    if changed:
        staff.session_version = (staff.session_version or 1) + 1
        touch(staff)
    return staff.id


def _anonymise_linked_staff(db, staff: Staff | None) -> str | None:
    """Retire a linked employee without destroying settled payroll facts.

    The account relation, credentials, contact metadata and capabilities are
    removed.  Wage rows keep their immutable amounts and settlement items;
    only the free-form ``staffName`` snapshot value is pseudonymised so the
    payroll ledger remains reconcilable without retaining the person's name.
    """
    if staff is None:
        return None
    staff.account_id = None
    staff.username = f'deleted-staff-{staff.id}'
    staff.name = '已注销员工'
    staff.phone = ''
    staff.email = None
    staff.email_verified = False
    staff.password_hash = '!'
    staff.capabilities = []
    staff.must_change_password = True
    staff.temporary_expires_at = None
    staff.is_super_admin = False
    staff.discount_level = None
    staff.enabled = False
    staff.status = 'DISABLED'
    staff.session_version = (staff.session_version or 1) + 1
    touch(staff)
    for wage in db.scalars(select(WageEntry).where(
            WageEntry.staff_id == staff.id).with_for_update()):
        snapshot = dict(wage.snapshot or {})
        if 'staffName' in snapshot:
            snapshot['staffName'] = '已注销员工'
            wage.snapshot = snapshot
            touch(wage)
    return staff.id


def _anonymise_account(db, account: Account, now: datetime) -> None:
    if account.status == 'DELETED' and str(account.email).startswith('deleted+'):
        account.deleted_at = account.deleted_at or now
        return
    for order in _account_orders(db, account):
        order.nickname = '已注销用户'
        order.phone = ''
        order.customer_note = ''
        data = dict(order.data or {})
        data.pop('checkoutAuthKind', None)
        order.data = data
        touch(order)
    account.email = f'deleted+{account.id}@invalid.local'
    account.password_hash = '!'
    account.display_name = '已注销用户'
    account.preferences = {}
    account.status = 'DELETED'
    account.verified_at = None
    account.deleted_at = now
    account.session_version = (account.session_version or 1) + 1
    account.invite_code = 'X' + account.id[:31]
    touch(account)


# ---------------------------------------------------------------------------
# Purge processor and target graph deletion
# ---------------------------------------------------------------------------


def _ledger(db, entry: TrashEntry, totals: dict) -> str | None:
    if not totals:
        return None
    bucket = f'trash:{entry.id}'
    existing = db.scalar(select(AnonymousLedger).where(AnonymousLedger.bucket == bucket))
    if existing is None:
        db.add(AnonymousLedger(bucket=bucket, totals=totals))
        db.flush()
    return bucket


def _assets_for_order(db, order: Order) -> tuple[set[str], set[str], set[str], set[str], set[str], set[str], set[str]]:
    file_ids = set(db.scalars(select(OrderFile.id).where(OrderFile.order_id == order.id)))
    revision_rows = list(db.scalars(select(OrderRevision).where(OrderRevision.order_id == order.id)))
    prep_ids = {order.preparation_id} | {row.preparation_id for row in revision_rows}
    quote_ids = {order.quote_id} | {row.quote_id for row in revision_rows}
    task_rows = list(db.scalars(select(PrintTask).where(PrintTask.order_id == order.id)))
    task_ids = {row.id for row in task_rows}
    artifact_ids = {row.artifact_id for row in task_rows}
    artifact_rows = list(db.scalars(select(Artifact).where(or_(
        Artifact.preparation_id.in_(prep_ids), Artifact.id.in_(artifact_ids)))))
    artifact_ids.update(row.id for row in artifact_rows)
    proofs = list(db.scalars(select(DeliveryProof).where(DeliveryProof.order_id == order.id)))
    proof_ids = {row.id for row in proofs}
    asset_ids = set(db.scalars(select(OrderFile.asset_id).where(OrderFile.order_id == order.id)))
    asset_ids.update(row.asset_id for row in artifact_rows)
    asset_ids.update(row.photo_asset_id for row in proofs if row.photo_asset_id)
    # Normalized/intermediate PDFs are linked through preparation data rather
    # than a relational FK; they still belong to the order's retention graph.
    for asset in db.scalars(select(Asset).where(Asset.purpose == 'PRINT_NORMALIZED')):
        if (asset.data or {}).get('normalizedFrom') in prep_ids:
            asset_ids.add(asset.id)
    return file_ids, prep_ids, quote_ids, task_ids, artifact_ids, proof_ids, asset_ids


def _asset_referenced_outside(db, asset_id: str, *, ignore_order_id: str | None = None,
                              ignore_artifact_ids: set[str] | None = None,
                              ignore_proof_ids: set[str] | None = None) -> bool:
    ignore_artifact_ids = ignore_artifact_ids or set()
    ignore_proof_ids = ignore_proof_ids or set()
    file_query = select(OrderFile.id).where(OrderFile.asset_id == asset_id)
    if ignore_order_id:
        file_query = file_query.where(OrderFile.order_id != ignore_order_id)
    if db.scalar(file_query.limit(1)):
        return True
    artifact_query = select(Artifact.id).where(Artifact.asset_id == asset_id)
    if ignore_artifact_ids:
        artifact_query = artifact_query.where(Artifact.id.notin_(ignore_artifact_ids))
    if db.scalar(artifact_query.limit(1)):
        return True
    proof_query = select(DeliveryProof.id).where(DeliveryProof.photo_asset_id == asset_id)
    if ignore_proof_ids:
        proof_query = proof_query.where(DeliveryProof.id.notin_(ignore_proof_ids))
    if db.scalar(proof_query.limit(1)):
        return True
    if db.scalar(select(UploadSession.id).where(UploadSession.asset_id == asset_id).limit(1)):
        return True
    if db.scalar(select(Asset.id).where(Asset.normalized_asset_id == asset_id).limit(1)):
        return True
    return False


def _preparation_shared(db, preparation_id: str, *, ignore_order_id: str | None = None) -> bool:
    query = select(Order.id).where(Order.preparation_id == preparation_id)
    if ignore_order_id:
        query = query.where(Order.id != ignore_order_id)
    if db.scalar(query.limit(1)):
        return True
    revision = select(OrderRevision.id).where(OrderRevision.preparation_id == preparation_id)
    if ignore_order_id:
        revision = revision.where(OrderRevision.order_id != ignore_order_id)
    return bool(db.scalar(revision.limit(1)))


def _quote_shared(db, quote_id: str, *, ignore_order_id: str | None = None) -> bool:
    query = select(Order.id).where(Order.quote_id == quote_id)
    if ignore_order_id:
        query = query.where(Order.id != ignore_order_id)
    if db.scalar(query.limit(1)):
        return True
    revision = select(OrderRevision.id).where(OrderRevision.quote_id == quote_id)
    if ignore_order_id:
        revision = revision.where(OrderRevision.order_id != ignore_order_id)
    return bool(db.scalar(revision.limit(1)))


def _order_ledger(db, order: Order, quote: Quote | None) -> dict:
    """Mirror commerce.dashboard's aggregation exactly, once for a purged order."""
    # The anonymous ledger always carries the five stable financial keys, even
    # for an unpaid cancelled order; consumers must not see a sparse row.
    totals = {
        'orderCount': 1, 'subtotalCents': 0, 'payableCents': 0,
        'cashReceivedCents': 0, 'refundCents': 0,
        # Non-PII aggregate dates: the original finance export filters by
        # order-created day, while the anonymous row itself is written at purge
        # time.  Preserve both day-only values so all-time reconciliation can
        # still place the order in its original reporting period.
        'orderCreatedDate': order.created_at.date().isoformat(),
        'purgedDate': _now().date().isoformat(),
    }
    if order.status != 'CANCELLED' or order.settlement_kind:
        totals.update(
            subtotalCents=quote.subtotal_cents if quote else 0,
            payableCents=order.total_cents,
            cashReceivedCents=order.cash_received_cents,
            refundCents=order.refunded_cents,
        )
        selected = (quote.data or {}).get('selectedPromotionType') if quote else None
        discount = quote.discount_cents if quote else 0
        if selected == 'COUPON':
            totals['couponDiscountCents'] = discount
        if selected == 'VOLUME_TIER':
            totals['volumeDiscountCents'] = discount
        if selected == 'STAFF_GRADE':
            totals['staffDiscountCents'] = discount
        if order.settlement_kind == 'ZERO_PAYABLE':
            totals['zeroPayableCount'] = 1
    adjustment = 0
    for refund in db.scalars(select(Refund).where(Refund.order_id == order.id)):
        suggested = int((refund.data or {}).get('suggestedCents', refund.amount_cents))
        adjustment += refund.amount_cents - suggested
    if adjustment:
        totals['manualAdjustmentCents'] = adjustment
    # Supplementary fees are separate financial facts.  Mirror the dashboard
    # aggregation before the charge/movement rows are purged: cash and refunds
    # are always preserved, active-fee totals only for a still-open charge
    # (an active charge is rejected by the blockers before this point).
    totals.update(supplementaryFeesCents=0, supplementaryCashReceivedCents=0,
                  supplementaryRefundCents=0, supplementaryOutstandingCents=0)
    # Unmatched cash is a separately held liability.  Do not fold it into
    # cashReceivedCents/refundCents: those mirror the order's Payment ledger,
    # and a fully refunded unmatched receipt never became a Payment.
    unmatched = _unmatched_receipt_activity(db, order.id)
    if unmatched:
        totals['unmatchedReceiptCents'] = sum(
            receipt.amount_cents for receipt, _refunded in unmatched)
        totals['unmatchedReceiptRefundCents'] = sum(
            refunded for _receipt, refunded in unmatched)
    for charge in db.scalars(select(SupplementaryCharge).where(
            SupplementaryCharge.order_id == order.id)):
        totals['supplementaryCashReceivedCents'] += charge.cash_received_cents
        totals['supplementaryRefundCents'] += charge.refunded_cents
        if charge.status == 'ACTIVE':
            totals['supplementaryFeesCents'] += charge.amount_cents
            totals['supplementaryOutstandingCents'] += max(
                0, charge.amount_cents - charge.cash_received_cents)
    if order.status == 'DELIVERED':
        totals['deliveredCount'] = 1
    return totals


def _purge_plan(db, entry: TrashEntry, target_type: str, target) -> dict:
    plan = {'files': [], 'asset_ids': set(), 'ledger': {}, 'ledger_bucket': None,
            'order': None, 'purchase_context': {}}
    if target_type == 'ORDER':
        order = target
        blockers = _order_active_blockers(db, order)
        if blockers:
            raise DomainError('PURGE_BLOCKED', '订单仍存在有效引用或未结业务', 409,
                              {'blockers': blockers})
        file_ids, prep_ids, quote_ids, task_ids, artifact_ids, proof_ids, asset_ids = \
            _assets_for_order(db, order)
        deletable_assets = set()
        for asset_id in asset_ids:
            if not _asset_referenced_outside(db, asset_id, ignore_order_id=order.id,
                                             ignore_artifact_ids=artifact_ids,
                                             ignore_proof_ids=proof_ids):
                deletable_assets.add(asset_id)
        files = [asset.storage_key for asset in db.scalars(
            select(Asset).where(Asset.id.in_(deletable_assets))) if asset.storage_key]
        quote = db.get(Quote, order.quote_id)
        plan.update(files=files, asset_ids=deletable_assets,
                    ledger=_order_ledger(db, order, quote),
                    order={
                        'order_id': order.id,
                        'file_ids': file_ids,
                        'prep_ids': prep_ids,
                        'quote_ids': quote_ids,
                        'task_ids': task_ids,
                        'artifact_ids': artifact_ids,
                        'proof_ids': proof_ids,
                    })
        return plan
    if target_type == 'ASSET':
        if _asset_referenced(db, target.id):
            raise DomainError('PURGE_BLOCKED', '资产仍被其他业务引用', 409,
                              {'blockers': ['ASSET_IN_USE']})
        plan['files'] = [target.storage_key] if target.storage_key else []
        plan['asset_ids'] = {target.id}
        return plan
    if target_type == 'BATCH':
        assignment_ids = list(db.scalars(select(DeliveryAssignment.id).where(
            DeliveryAssignment.batch_id == target.id)))
        if assignment_ids:
            blockers = _unsettled_wage_blockers(db, assignment_ids=assignment_ids)
        if blockers:
            raise DomainError('PURGE_BLOCKED', '批次仍存在未结工资，不能永久清理', 409,
                              {'blockers': blockers})
        plan['batch'] = {'assignment_ids': assignment_ids}
        return plan
    if target_type == 'ACCOUNT':
        plan['account_id'] = target.id
        return plan
    if target_type in ('PAYMENT', 'RECEIPT', 'RECEIPT_ADJUSTMENT', 'REFUND'):
        amount = target.amount_cents
        plan['ledger'] = {f'deleted{target_type.title().replace("_", "")}Cents': amount}
        if target_type == 'REFUND':
            suggested = int((target.data or {}).get('suggestedCents', amount))
            if amount - suggested:
                plan['ledger']['manualAdjustmentCents'] = amount - suggested
        return plan
    if target_type == 'COUPON':
        plan['ledger'] = {'deletedCouponCents': target.amount_cents}
        return plan
    if target_type == 'PROMOTION_EVENT':
        plan['ledger'] = {'deletedPromotionCents': target.amount_cents}
        return plan
    if target_type == 'EXPORT_JOB':
        plan['files'] = [target.storage_key] if target.storage_key else []
        return plan
    if target_type == 'PREPARATION':
        artifacts = list(db.scalars(select(Artifact).where(
            Artifact.preparation_id == target.id)))
        for artifact in artifacts:
            if db.scalar(select(PrintTask.id).where(
                    PrintTask.artifact_id == artifact.id).limit(1)):
                raise DomainError('PURGE_BLOCKED', '草稿成品仍被打印任务引用', 409,
                                  {'blockers': ['PRINT_TASK_REFERENCE']})
        asset_ids = {artifact.asset_id for artifact in artifacts}
        asset_ids.update(row.get('assetId') for row in
                         ((target.input or {}).get('files') or []) if row.get('assetId'))
        deletable = {asset_id for asset_id in asset_ids if not _asset_referenced_outside(
            db, asset_id, ignore_artifact_ids={row.id for row in artifacts})}
        files = [asset.storage_key for asset in db.scalars(
            select(Asset).where(Asset.id.in_(deletable))) if asset.storage_key]
        plan.update(files=files, asset_ids=deletable,
                    preparation={'artifact_ids': {row.id for row in artifacts},
                                 'quote_id': target.quote_id})
        return plan
    if target_type == 'ARTIFACT':
        if db.scalar(select(PrintTask.id).where(
                PrintTask.artifact_id == target.id).limit(1)):
            raise DomainError('PURGE_BLOCKED', '成品仍被打印任务引用', 409,
                              {'blockers': ['PRINT_TASK_REFERENCE']})
        if not _asset_referenced_outside(db, target.asset_id, ignore_artifact_ids={target.id}):
            plan['asset_ids'] = {target.asset_id}
            asset = db.get(Asset, target.asset_id)
            if asset and asset.storage_key:
                plan['files'] = [asset.storage_key]
        return plan
    return plan


def _delete_order_graph(db, order: Order, context: dict) -> None:
    order_id = context['order_id']
    task_ids = context['task_ids']
    artifact_ids = context['artifact_ids']
    file_ids = context['file_ids']
    proof_ids = context['proof_ids']
    prep_ids = context['prep_ids']
    quote_ids = context['quote_ids']
    grant_ids = set(db.scalars(select(AccessGrant.id).where(AccessGrant.order_id == order_id)))
    if grant_ids:
        # The order -> preparation -> grant -> order chain is a real cycle in
        # the shared model.  Null every preparation link first, then remove the
        # grants before the order row itself.
        db.execute(update(Preparation).where(
            Preparation.source_grant_id.in_(grant_ids)).values(source_grant_id=None))
        db.execute(update(AccessGrant).where(
            AccessGrant.source_grant_id.in_(grant_ids)).values(source_grant_id=None))
        db.execute(delete(AccessGrant).where(AccessGrant.id.in_(grant_ids)))
    # Payroll rows are immutable business facts.  A settled entry may outlive
    # the order, but only after its nullable order/assignment FKs are detached
    # and the original identifiers are retained in the snapshot.  Unsettled
    # entries are rejected by the blockers before this point.
    assignment_ids = list(db.scalars(select(DeliveryAssignment.id).where(
        DeliveryAssignment.order_id == order_id)))
    wage_scope = [WageEntry.order_id == order_id]
    if assignment_ids:
        wage_scope.append(WageEntry.assignment_id.in_(assignment_ids))
    wage_entries = list(db.scalars(select(WageEntry).where(or_(*wage_scope))))
    detached_at = _now().isoformat()
    for wage in wage_entries:
        if not _wage_entry_is_settled(db, wage.id):
            raise DomainError('PURGE_BLOCKED', '工资条目尚未结算，不能清理关联订单', 409,
                              {'blockers': [f'WAGE_UNSETTLED:{wage.id}']})
        snapshot = dict(wage.snapshot or {})
        if wage.order_id == order_id:
            snapshot['purgedOrderId'] = wage.order_id
            wage.order_id = None
        if wage.assignment_id in assignment_ids:
            snapshot['purgedAssignmentId'] = wage.assignment_id
            wage.assignment_id = None
        snapshot['purgedAt'] = detached_at
        wage.snapshot = snapshot
        touch(wage)
    if wage_entries:
        db.flush()
    if task_ids or artifact_ids:
        db.execute(delete(PrintAttempt).where(or_(
            PrintAttempt.task_id.in_(task_ids) if task_ids else False,
            PrintAttempt.artifact_id.in_(artifact_ids) if artifact_ids else False)))
    db.execute(delete(PrintTask).where(PrintTask.order_id == order_id))
    db.execute(delete(BindingTask).where(BindingTask.order_id == order_id))
    db.execute(delete(PackingTask).where(PackingTask.order_id == order_id))
    db.execute(delete(DeliveryProof).where(DeliveryProof.order_id == order_id))
    db.execute(delete(DeliveryAssignment).where(DeliveryAssignment.order_id == order_id))
    db.execute(delete(OrderFile).where(OrderFile.order_id == order_id))
    if artifact_ids:
        db.execute(delete(Artifact).where(Artifact.id.in_(artifact_ids)))
    db.execute(delete(OrderRevision).where(OrderRevision.order_id == order_id))
    db.execute(update(PromotionEvent).where(
        PromotionEvent.order_id == order_id).values(order_id=None))
    db.execute(update(Coupon).where(
        Coupon.reserved_order_id == order_id).values(reserved_order_id=None))
    db.execute(update(Coupon).where(
        Coupon.used_order_id == order_id).values(used_order_id=None))
    db.execute(update(Referral).where(
        Referral.qualified_order_id == order_id).values(qualified_order_id=None))
    db.execute(delete(Refund).where(Refund.order_id == order_id))
    db.execute(delete(AfterSale).where(AfterSale.order_id == order_id))
    db.execute(delete(ReceiptAdjustment).where(ReceiptAdjustment.receipt_id.in_(
        select(Receipt.id).where(Receipt.order_id == order_id))))
    db.execute(delete(Payment).where(Payment.order_id == order_id))
    db.execute(delete(Receipt).where(Receipt.order_id == order_id))
    db.execute(delete(BusinessException).where(BusinessException.order_id == order_id))
    charge_ids = list(db.scalars(select(SupplementaryCharge.id).where(
        SupplementaryCharge.order_id == order_id)))
    if charge_ids:
        db.execute(delete(SupplementaryDispute).where(
            SupplementaryDispute.charge_id.in_(charge_ids)))
        db.execute(delete(SupplementaryMovement).where(
            SupplementaryMovement.charge_id.in_(charge_ids)))
        db.execute(delete(SupplementaryCharge).where(
            SupplementaryCharge.id.in_(charge_ids)))
    db.execute(delete(Order).where(Order.id == order_id))
    for quote_id in quote_ids:
        if not _quote_shared(db, quote_id):
            db.execute(delete(Quote).where(Quote.id == quote_id))
    for prep_id in prep_ids:
        if not _preparation_shared(db, prep_id):
            db.execute(delete(Preparation).where(Preparation.id == prep_id))


def _delete_batch_graph(db, batch, now: datetime) -> None:
    """Permanently remove a batch together with its execution facts.

    Delivered proofs and assignments belong to the batch; settled wage entries
    are immutable payroll facts, so their nullable assignment FK is detached
    and the original identifiers stay in the snapshot.
    """
    assignment_ids = list(db.scalars(select(DeliveryAssignment.id).where(
        DeliveryAssignment.batch_id == batch.id)))
    if assignment_ids:
        for wage in db.scalars(select(WageEntry).where(
                WageEntry.assignment_id.in_(assignment_ids))):
            if not _wage_entry_is_settled(db, wage.id):
                raise DomainError('PURGE_BLOCKED', '工资条目尚未结算，不能清理关联批次', 409,
                                  {'blockers': [f'WAGE_UNSETTLED:{wage.id}']})
            snapshot = dict(wage.snapshot or {})
            snapshot['purgedAssignmentId'] = wage.assignment_id
            snapshot['purgedBatchId'] = batch.id
            snapshot['purgedAt'] = now.isoformat()
            wage.assignment_id = None
            wage.snapshot = snapshot
            touch(wage)
        # The session runs with autoflush=False: flush the detached payroll FKs
        # before deleting the delivery rows they pointed at.
        db.flush()
        db.execute(delete(DeliveryProof).where(
            DeliveryProof.assignment_id.in_(assignment_ids)))
        db.execute(delete(DeliveryAssignment).where(
            DeliveryAssignment.batch_id == batch.id))
    db.delete(batch)


def _delete_simple_target(db, entry: TrashEntry, target_type: str, target, plan: dict) -> None:
    now = _now()
    if target_type == 'ASSET':
        db.delete(target)
    elif target_type == 'BATCH':
        _delete_batch_graph(db, target, now)
    elif target_type == 'ACCOUNT':
        staff = _linked_staff(db, target)
        _disable_linked_staff(db, target, now)
        with storage_gc_guard(db):
            _revoke_account_credentials(db, target, now)
        _anonymise_linked_staff(db, staff)
        _anonymise_account(db, target, now)
    elif target_type == 'PAYMENT':
        db.delete(target)
    elif target_type == 'RECEIPT':
        db.execute(delete(ReceiptAdjustment).where(
            ReceiptAdjustment.receipt_id == target.id))
        db.delete(target)
    elif target_type == 'RECEIPT_ADJUSTMENT':
        db.delete(target)
    elif target_type == 'REFUND':
        db.delete(target)
    elif target_type == 'AFTER_SALE':
        db.delete(target)
    elif target_type == 'COUPON':
        db.delete(target)
    elif target_type == 'PROMOTION_EVENT':
        db.delete(target)
    elif target_type == 'CAMPAIGN':
        db.delete(target)
    elif target_type == 'REFERRAL':
        db.delete(target)
    elif target_type == 'AUDIT_LOG':
        db.delete(target)
    elif target_type == 'EMAIL_JOB':
        db.delete(target)
    elif target_type == 'EXPORT_JOB':
        db.delete(target)
    elif target_type == 'PREPARATION':
        artifact_ids = plan.get('preparation', {}).get('artifact_ids', set())
        if artifact_ids:
            db.execute(delete(Artifact).where(Artifact.id.in_(artifact_ids)))
        quote_id = plan.get('preparation', {}).get('quote_id')
        if quote_id and not _quote_shared(db, quote_id):
            db.execute(delete(Quote).where(Quote.id == quote_id))
        db.delete(target)
    elif target_type == 'ARTIFACT':
        db.delete(target)
    else:
        raise DomainError('PURGE_UNSUPPORTED', '该对象类型不支持永久清理', 422)


def _purge_recheck(db, target_type: str, target) -> None:
    blockers: list[str] = []
    if target_type == 'ORDER':
        blockers = _order_active_blockers(db, target)
    elif target_type == 'BATCH':
        assignment_ids = list(db.scalars(select(DeliveryAssignment.id).where(
            DeliveryAssignment.batch_id == target.id)))
        if assignment_ids:
            blockers = _unsettled_wage_blockers(db, assignment_ids=assignment_ids)
    elif target_type == 'ASSET':
        if _asset_referenced(db, target.id):
            blockers = ['ASSET_IN_USE']
    elif target_type == 'ACCOUNT':
        blockers = _account_deletion_blockers(db, target)
    elif target_type == 'COUPON':
        if db.scalar(select(PromotionEvent.id).where(
                PromotionEvent.coupon_id == target.id).limit(1)):
            blockers.append('PROMOTION_EVENT_REFERENCE')
        for order_id in (target.reserved_order_id, target.used_order_id):
            order = db.get(Order, order_id) if order_id else None
            if order is not None and order.deleted_at is None:
                blockers.append('ORDER_REFERENCE')
    elif target_type == 'CAMPAIGN':
        if db.scalar(select(Coupon.id).where(
                Coupon.campaign_id == target.id).limit(1)) or db.scalar(
                select(PromotionEvent.id).where(
                    PromotionEvent.campaign_id == target.id).limit(1)):
            blockers.append('PROMOTION_REFERENCE')
    elif target_type == 'RECEIPT':
        if db.scalar(select(Payment.id).where(Payment.receipt_id == target.id).limit(1)):
            blockers.append('PAYMENT_REFERENCE')
    elif target_type == 'AFTER_SALE':
        if db.scalar(select(Refund.id).where(Refund.after_sale_id == target.id).limit(1)):
            blockers.append('REFUND_REFERENCE')
    elif target_type == 'REFUND':
        sale = db.get(AfterSale, target.after_sale_id)
        if sale is not None and sale.status not in TERMINAL_AFTER_SALE_STATUSES:
            blockers.append('AFTER_SALE_ACTIVE')
    elif target_type == 'PREPARATION':
        if _preparation_referenced(db, target.id):
            blockers.append('ORDER_REFERENCE')
        artifact_ids = select(Artifact.id).where(Artifact.preparation_id == target.id)
        if db.scalar(select(PrintTask.id).where(
                PrintTask.artifact_id.in_(artifact_ids)).limit(1)):
            blockers.append('PRINT_TASK_REFERENCE')
        if db.scalar(select(PrintAttempt.id).where(
                PrintAttempt.artifact_id.in_(artifact_ids)).limit(1)):
            blockers.append('PRINT_ATTEMPT_REFERENCE')
    elif target_type == 'ARTIFACT':
        if db.scalar(select(PrintTask.id).where(
                PrintTask.artifact_id == target.id).limit(1)):
            blockers.append('PRINT_TASK_REFERENCE')
        if db.scalar(select(PrintAttempt.id).where(
                PrintAttempt.artifact_id == target.id).limit(1)):
            blockers.append('PRINT_ATTEMPT_REFERENCE')
    if blockers:
        raise DomainError('PURGE_BLOCKED', '对象出现了新的有效引用或未结业务，不能清理', 409,
                          {'blockers': blockers, 'targetType': target_type,
                           'targetId': target.id})


def process_purge_job(db, trash_id: str, expected_version: int | None = None,
                      reason: str | None = None) -> None:
    """Execute one PURGE job. Transaction ownership stays with the caller.

    The storage maintenance lock is acquired before the ``TrashEntry`` row
    lock.  Private files are staged by ``purge_guard`` and are not unlinked
    until the caller commits; a staging/GC failure leaves a truthful
    ``PURGE_PENDING`` tombstone instead of reporting ``PURGED`` with missing
    files.

    HTTP callers pass ``reason``.  The reason mutation and Job enqueue happen
    only after both locks are held: doing either before ``purge_guard`` would
    flush the dirty Trash row and take the database write lock before the
    file lock, recreating the backup inversion this guard exists to prevent.
    """
    entry = db.get(TrashEntry, trash_id)
    if entry is None:
        # Preserve the public 404 shape without taking a row lock first.
        entry = lock(db, TrashEntry, trash_id)
    with purge_guard(db, entry):
        entry = lock(db, TrashEntry, trash_id, expected_version)
        if entry.status == 'RESTORED':
            raise DomainError('TRASH_RESTORED', '对象已恢复，不能清理', 409)

        if reason is not None:
            data = dict(entry.data or {})
            data['purgeReason'] = reason
            entry.data = data
            touch(entry)
            enqueue_job(db, 'PURGE', entry.id, f'purge:{entry.id}',
                        {'targetType': entry.target_type})

        target_type = _canonical_target(entry.target_type)
        target = db.get(TARGETS[target_type], entry.target_id)
        data = dict(entry.data or {})
        pending_cleanup = bool(data.get('cleanupPending'))
        if entry.status == 'PURGED' or pending_cleanup:
            try:
                if recover_purge_cleanup(db, entry):
                    return
            except DomainError as error:
                if error.code != 'PURGE_STORAGE_PENDING':
                    raise
                # A missing target has nothing left to purge.  Keep the
                # existing pending tombstone/metadata for the next retry or
                # operator recovery instead of overwriting it as PURGED.
                if target is None and entry.status != 'PURGED':
                    db.flush()
                    return
                raise

        if target is None:
            if data.get('storageCleanup'):
                raise DomainError(
                    'PURGE_STORAGE_PENDING',
                    '存储清理状态仍未完成，保留待重试墓碑',
                    503,
                    {'reason': 'STORAGE_CLEANUP_PENDING'},
                    commit=True,
                )
            entry.status = 'PURGED'
            touch(entry)
            db.flush()
            return

        # The backup lock, recheck, plan and every row/file mutation share one
        # critical section.  purge_guard records the exact deleted rows,
        # detached foreign keys and relative files in the same transaction.
        _purge_recheck(db, target_type, target)
        plan = _purge_plan(db, entry, target_type, target)
        files = list(plan['files'])

        # A purge must never report success after unlinking something it
        # cannot restore on rollback.  Regular files are staged by the guard;
        # directories and symlinks are rejected before any mutation and left
        # in a visible retryable state.
        nonregular: list[dict] = []
        for key in files:
            try:
                path = storage_path(key)
            except DomainError as error:
                nonregular.append({'key': key, 'reason': error.code})
                continue
            if path.is_dir() or path.is_symlink():
                nonregular.append({'key': key, 'reason': 'NONREGULAR'})
        if nonregular:
            data = dict(entry.data or {})
            data['storageCleanup'] = {'pending': nonregular,
                                      'attemptedAt': _now().isoformat()}
            entry.data = data
            entry.status = 'PURGE_PENDING'
            touch(entry)
            db.flush()
            raise DomainError('PURGE_STORAGE_PENDING',
                              '存储路径不是可安全暂存的普通文件',
                              503, {'pending': len(nonregular)},
                              commit=True)

        try:
            record_purged_files(entry, files)
        except (DomainError, OSError) as error:
            data = dict(entry.data or {})
            data['storageCleanup'] = {
                'pending': [{'key': key, 'reason': type(error).__name__}
                            for key in files],
                'attemptedAt': _now().isoformat(),
            }
            entry.data = data
            entry.status = 'PURGE_PENDING'
            touch(entry)
            db.flush()
            raise DomainError('PURGE_STORAGE_PENDING',
                              '存储文件尚未安全暂存，将自动重试',
                              503, {'pending': len(files)},
                              commit=True) from error

        bucket = _ledger(db, entry, plan.get('ledger') or {})
        if target_type == 'ORDER':
            _delete_order_graph(db, target, plan['order'])
        else:
            _delete_simple_target(db, entry, target_type, target, plan)
        if plan.get('asset_ids'):
            db.execute(delete(Asset).where(Asset.id.in_(plan['asset_ids'])))
        entry.status = 'PURGED'
        data = dict(entry.data or {})
        data.pop('storageCleanup', None)
        entry.data = data
        touch(entry)
        audit(db, 'system', 'TRASH_PURGED', target_type, target.id,
              data={'trashId': entry.id, 'ledgerBucket': bucket})
    db.flush()


# ---------------------------------------------------------------------------
# Scheduled maintenance sweep
# ---------------------------------------------------------------------------


def _close_expired_orders(db, now: datetime) -> int:
    from .commerce import cancel_pending

    rows = list(db.scalars(select(Order).where(
        Order.status == 'PENDING_CONFIRMATION',
        Order.settlement_kind.is_(None),
        Order.deleted_at.is_(None),
        Order.close_at <= now).with_for_update(skip_locked=True)))
    closed = 0
    for order in rows:
        try:
            cancel_pending(db, order, '超过待确认期限自动关闭')
        except DomainError:
            continue
        audit(db, 'system', 'ORDER_AUTO_CLOSED', 'ORDER', order.id,
              data={'reason': 'PAYMENT_TIMEOUT'})
        closed += 1
    if closed:
        db.flush()
    return closed


def _queue_pending_exports(db, now: datetime) -> int:
    queued = 0
    for job in db.scalars(select(ExportJob).where(
            ExportJob.status == 'QUEUED', ExportJob.expires_at > now)):
        business_key = f'export:{job.id}'
        if db.scalar(select(Job.id).where(Job.business_key == business_key)):
            continue
        enqueue_job(db, 'EXPORT', job.id, business_key, {'kind': job.kind})
        queued += 1
    return queued


def _expire_exports(db, now: datetime) -> tuple[int, int]:
    jobs = list(db.scalars(select(ExportJob).where(or_(
        and_(ExportJob.expires_at <= now, ExportJob.status != 'EXPIRED'),
        and_(ExportJob.status == 'EXPIRED', ExportJob.storage_key.isnot(None))),
    ).with_for_update(skip_locked=True)))
    expired = 0
    files = 0
    for job in jobs:
        if job.storage_key:
            try:
                purge_storage_key_locked(job.storage_key)
            except OSError:
                data = dict(job.data or {})
                data['cleanupPending'] = True
                job.data = data
                touch(job)
                continue
            job.storage_key = None
            files += 1
        if job.status != 'EXPIRED':
            data = dict(job.data or {})
            data['expiredAt'] = now.isoformat()
            job.data = data
            job.status = 'EXPIRED'
            touch(job)
            expired += 1
    db.flush()
    return expired, files


def _expire_uploads(db, now: datetime) -> int:
    rows = list(db.scalars(select(UploadSession).where(
        UploadSession.expires_at <= now,
        UploadSession.status.in_(('UPLOADING', 'COMPLETED', 'CANCELLED', 'EXPIRED'))
    ).with_for_update(skip_locked=True)))
    cleaned = 0
    for session in rows:
        try:
            purge_storage_tree_locked(f'sessions/{session.id}')
        except OSError:
            data = dict(session.data or {})
            data['cleanupPending'] = True
            session.data = data
            touch(session)
            continue
        data = dict(session.data or {})
        data.update(chunks=[], receivedBytes=0, cleanupPending=False,
                    expiredAt=now.isoformat())
        session.data = data
        if session.status == 'UPLOADING':
            session.status = 'EXPIRED'
        touch(session)
        cleaned += 1
    if rows:
        db.flush()
    return cleaned


def _preparation_referenced(db, preparation_id: str) -> bool:
    if db.scalar(select(Order.id).where(Order.preparation_id == preparation_id).limit(1)):
        return True
    if db.scalar(select(OrderRevision.id).where(
            OrderRevision.preparation_id == preparation_id).limit(1)):
        return True
    return False


def _cleanup_drafts(db, now: datetime) -> int:
    rows = list(db.scalars(select(Preparation).where(
        Preparation.expires_at <= now,
        Preparation.status != 'CANCELLED').with_for_update(skip_locked=True)))
    removed = 0
    for preparation in rows:
        if _preparation_referenced(db, preparation.id):
            continue
        artifacts = list(db.scalars(select(Artifact).where(
            Artifact.preparation_id == preparation.id)))
        if any(db.scalar(select(PrintTask.id).where(
                PrintTask.artifact_id == artifact.id).limit(1)) for artifact in artifacts):
            continue
        if any(db.scalar(select(PrintAttempt.id).where(
                PrintAttempt.artifact_id == artifact.id).limit(1)) for artifact in artifacts):
            continue
        asset_ids = {artifact.asset_id for artifact in artifacts}
        asset_ids.update(row.get('assetId') for row in
                         ((preparation.input or {}).get('files') or []) if row.get('assetId'))
        deletable = {asset_id for asset_id in asset_ids
                     if not _asset_referenced_outside(db, asset_id,
                                                      ignore_artifact_ids={a.id for a in artifacts})}
        failed = False
        for asset in db.scalars(select(Asset).where(Asset.id.in_(deletable))):
            if asset.storage_key:
                try:
                    purge_storage_key_locked(asset.storage_key)
                except OSError:
                    failed = True
                    break
        if failed:
            continue
        for artifact in artifacts:
            db.delete(artifact)
        if preparation.quote_id and not _quote_shared(db, preparation.quote_id):
            db.execute(delete(Quote).where(Quote.id == preparation.quote_id))
        db.execute(delete(Preparation).where(Preparation.id == preparation.id))
        if deletable:
            db.execute(delete(Asset).where(Asset.id.in_(deletable)))
        removed += 1
    if removed:
        db.flush()
    return removed


def _purge_tokens(db, now: datetime) -> int:
    result = db.execute(delete(OneTimeToken).where(or_(
        OneTimeToken.expires_at <= now, OneTimeToken.consumed_at.isnot(None))))
    db.flush()
    return result.rowcount or 0


def _purge_email_payloads(db, now: datetime) -> int:
    rows = list(db.scalars(select(EmailJob).where(
        EmailJob.expires_at <= now, EmailJob.protected_payload.isnot(None))))
    for job in rows:
        job.protected_payload = None
        data = dict(job.data or {})
        data['purgedAt'] = now.isoformat()
        job.data = data
        if job.status in ('QUEUED', 'PROCESSING'):
            job.status = 'EXPIRED'
        touch(job)
    if rows:
        db.flush()
    return len(rows)


def _queue_due_trash(db, now: datetime) -> int:
    rows = list(db.scalars(select(TrashEntry).where(
        or_(and_(TrashEntry.status == 'DELETED', TrashEntry.purge_at <= now),
            TrashEntry.status == 'PURGE_PENDING')).with_for_update(skip_locked=True)))
    queued = 0
    for entry in rows:
        business_key = f'purge:{entry.id}'
        job = db.scalar(select(Job).where(Job.business_key == business_key).with_for_update())
        failure_code = (job.data or {}).get('failureCode') if job is not None else None
        if job is None:
            enqueue_job(db, 'PURGE', entry.id, business_key,
                        {'targetType': entry.target_type})
            queued += 1
            continue
        # A failed storage cleanup is a retryable PURGE stage.  Domain blocks
        # (new reference/restored entry) are not retried forever.
        if (job.status in ('FAILED', 'READY')
                and failure_code not in ('PURGE_BLOCKED', 'TRASH_RESTORED')
                and job.attempts < 10):
            job.status = 'QUEUED'
            job.lease_token = None
            job.lease_until = None
            data = dict(job.data or {})
            data.pop('availableAt', None)
            data.pop('failureCode', None)
            job.data = data
            touch(job)
            queued += 1
    if queued:
        db.flush()
    return queued


def _order_completed_at(db, order: Order) -> datetime | None:
    times: list[datetime] = []
    proof = db.scalar(select(DeliveryProof).where(
        DeliveryProof.order_id == order.id).order_by(
        DeliveryProof.created_at.desc()).limit(1))
    if proof is not None:
        raw = (proof.data or {}).get('serverDeliveredAt') or (proof.data or {}).get('deliveredAt')
        if raw:
            try:
                times.append(_aware(datetime.fromisoformat(str(raw).replace('Z', '+00:00'))))
            except ValueError:
                times.append(_aware(proof.created_at))
        else:
            times.append(_aware(proof.created_at))
    for sale in db.scalars(select(AfterSale).where(AfterSale.order_id == order.id)):
        if sale.status in TERMINAL_AFTER_SALE_STATUSES:
            times.append(_aware(sale.updated_at))
    if order.status in TERMINAL_ORDER_STATUSES and not times:
        times.append(_aware(order.updated_at))
    return max(item for item in times if item) if times else None


def _file_retention_protected(db, order: Order) -> bool:
    if order.status not in TERMINAL_ORDER_STATUSES:
        return True
    if order.refund_locked:
        return True
    if _active_after_sales(db, [order.id]) or _open_exceptions(db, [order.id]):
        return True
    return False


def _retention_files(db, now: datetime) -> int:
    source_days = _retention_days(db, 'sourceDays', 7)
    final_days = _retention_days(db, 'finalDays', 30)
    proof_days = _retention_days(db, 'proofDays', 30)
    deleted = 0
    orders = list(db.scalars(select(Order).where(Order.deleted_at.is_(None))))
    for order in orders:
        if _file_retention_protected(db, order):
            continue
        completed = _order_completed_at(db, order)
        if completed is None:
            continue
        context = _assets_for_order(db, order)
        file_ids, prep_ids, quote_ids, task_ids, artifact_ids, proof_ids, asset_ids = context
        for asset in db.scalars(select(Asset).where(
                Asset.id.in_(asset_ids), Asset.deleted_at.is_(None))):
            purpose = asset.purpose
            if purpose in ('PRINT_SOURCE', 'PRINT_NORMALIZED'):
                days = source_days
            elif purpose == 'DELIVERY_PROOF':
                days = proof_days
            else:
                days = final_days
            if now < completed + timedelta(days=days):
                continue
            if _asset_referenced_outside(db, asset.id, ignore_order_id=order.id,
                                         ignore_artifact_ids=artifact_ids,
                                         ignore_proof_ids=proof_ids):
                continue
            if asset.storage_key:
                try:
                    purge_storage_key_locked(asset.storage_key)
                except OSError:
                    continue
            asset.deleted_at = now
            touch(asset)
            deleted += 1
    db.flush()
    return deleted


def _cleanup_orphan_assets(db, now: datetime) -> int:
    removed = 0
    for asset in db.scalars(select(Asset).where(
            Asset.deleted_at.is_(None),
            Asset.expires_at.isnot(None),
            Asset.expires_at <= now,
            Asset.purpose.notin_(PUBLIC_PURPOSES))):
        if _asset_referenced(db, asset.id):
            continue
        if asset.storage_key:
            try:
                purge_storage_key_locked(asset.storage_key)
            except OSError:
                continue
        asset.deleted_at = now
        touch(asset)
        removed += 1
    db.flush()
    return removed


def maintenance_sweep(db) -> dict:
    """Run all due lifecycle work in the caller's transaction.

    Never age-deletes ordinary orders, accounting, audit, accounts or email
    records; only explicit temporary artefacts and 24h trash purges are due.

    The whole sweep runs inside ``storage_gc_guard`` so every Q29 unlink is
    staged until the caller commits.  A backup therefore either sees the old
    committed database plus the original files, or the new database plus
    collected files; a rollback restores the staged files.
    """
    with storage_gc_guard(db):
        recover_orphaned_staging(db)
        now = _now()
        closed = _close_expired_orders(db, now)
        queued_exports = _queue_pending_exports(db, now)
        expired_exports, deleted_export_files = _expire_exports(db, now)
        expired_uploads = _expire_uploads(db, now)
        removed_drafts = _cleanup_drafts(db, now)
        purged_tokens = _purge_tokens(db, now)
        purged_emails = _purge_email_payloads(db, now)
        queued_purges = _queue_due_trash(db, now)
        deleted_files = _retention_files(db, now)
        deleted_files += _cleanup_orphan_assets(db, now)
        return {
            'closedOrders': closed,
            'queuedExports': queued_exports,
            'expiredExports': expired_exports,
            'expiredUploads': expired_uploads,
            'removedDrafts': removed_drafts,
            'purgedTokens': purged_tokens,
            'purgedEmailPayloads': purged_emails,
            'purgeJobsQueued': queued_purges,
            'deletedFiles': deleted_files,
            'deletedExportFiles': deleted_export_files,
        }
