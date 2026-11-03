"""Shared delivery planning facts: acceptance windows, sealing and auto batches.

This module owns the single authoritative computation of a slot's order
acceptance state and of an order's current effective delivery arrangement.  It
is imported by both commerce (settlement) and delivery (admin/courier views) so
the public list, order submission, settlement and every read surface agree.

Lock order (see delivery.py module docstring for the full graph):
    settlement: order -> slot -> group -> batch -> assignment
    sealing:    slot -> groups -> batches (sorted by id)
    batch ops:  orders -> assignments -> batch (last)
A settling order has no active batch, so settlement cannot form a cycle with
batch operations.  Every path re-checks time, version and permission after a
wait.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import object_session

from .config import get_config
from .core import DomainError, lock, touch, utcnow
from .models import (
    Campus,
    DeliveryAssignment,
    DeliveryBatch,
    DeliveryChange,
    DeliveryGroup,
    DeliveryNotice,
    DeliverySlot,
    Location,
    Order,
    PackingTask,
    utcnow as _utcnow,
)

CAMPUS_MISSING = 'CAMPUS_MISSING'
CAMPUS_DISABLED = 'CAMPUS_DISABLED'
OPEN = 'OPEN'
ARCHIVED = 'ARCHIVED'
CONFIG_MISSING = 'CONFIG_MISSING'
DISABLED = 'DISABLED'
NOT_OPEN = 'NOT_OPEN'
CUTOFF = 'CUTOFF'
SEALED = 'SEALED'
FULL = 'FULL'
SERVICE_CLOSED = 'SERVICE_CLOSED'

ACCEPTANCE_LABELS = {
    CAMPUS_MISSING: '缺少校区',
    CAMPUS_DISABLED: '校区不可预约',
    OPEN: '接单中',
    ARCHIVED: '已归档',
    CONFIG_MISSING: '待补配置',
    DISABLED: '人工停用',
    NOT_OPEN: '未到开放',
    CUTOFF: '已截单',
    SEALED: '提前封批',
    FULL: '已满',
    SERVICE_CLOSED: '服务未开放',
}

SLOT_ERRORS = {
    CAMPUS_MISSING: ('SLOT_CAMPUS_MISSING', '配送时段缺少校区'),
    CAMPUS_DISABLED: ('CAMPUS_DISABLED', '校区不可新预约'),
    ARCHIVED: ('SLOT_ARCHIVED', '该配送时段已归档'),
    CONFIG_MISSING: ('SLOT_CONFIG_MISSING', '该配送时段尚未补齐开放下单与截单规则'),
    DISABLED: ('SLOT_DISABLED', '该配送时段已停止接单'),
    NOT_OPEN: ('SLOT_NOT_OPEN', '尚未到开放下单时间'),
    CUTOFF: ('SLOT_CUTOFF', '该配送时段已超过截单时间'),
    SEALED: ('SLOT_SEALED', '该配送时段已提前封批'),
    FULL: ('SLOT_FULL', '该配送时段名额已满'),
    SERVICE_CLOSED: ('SERVICE_CLOSED', '服务暂未开放'),
}

BLOCKER_MESSAGES = {
    CAMPUS_MISSING: '配送时段缺少校区',
    CAMPUS_DISABLED: '校区不可新预约',
    ARCHIVED: '时段已归档',
    CONFIG_MISSING: '缺少开放下单时间或截单时间',
    DISABLED: '时段已停止接单',
    NOT_OPEN: '尚未到开放下单时间',
    CUTOFF: '已过截单时间',
    SEALED: '已提前封批',
    FULL: '名额已满',
    SERVICE_CLOSED: '服务未开放',
}


def service_accepting(db) -> bool:
    announcement = get_config(db, 'announcement').data or {}
    workflow = get_config(db, 'workflow').data or {}
    pauses = workflow.get('pauses') or {}
    return bool(announcement.get('serviceEnabled', False)) and not bool(pauses.get('newOrders'))


def slot_acceptance(db, slot: DeliverySlot, now: datetime | None = None,
                    service_open: bool | None = None) -> dict:
    """Return the one authoritative acceptance state of a delivery slot."""
    now = now or _utcnow()
    data = slot.data or {}
    if data.get('archivedAt'):
        status = ARCHIVED
    elif not slot.campus_id or db.get(Campus, slot.campus_id) is None:
        status = CAMPUS_MISSING
    elif not db.get(Campus, slot.campus_id).selection_enabled:
        status = CAMPUS_DISABLED
    elif slot.open_at is None or slot.cutoff_at is None:
        status = CONFIG_MISSING
    elif slot.sealed_at is not None:
        status = SEALED
    elif not slot.enabled:
        status = DISABLED
    elif now < slot.open_at:
        status = NOT_OPEN
    elif now >= slot.cutoff_at:
        status = CUTOFF
    elif slot.occupied >= slot.capacity:
        status = FULL
    elif not (service_accepting(db) if service_open is None else service_open):
        status = SERVICE_CLOSED
    else:
        status = OPEN
    blockers = []
    if status != OPEN:
        blockers.append({
            'code': status,
            'message': BLOCKER_MESSAGES[status],
            'relatedId': slot.id,
        })
    return {'status': status, 'selectable': status == OPEN, 'blockers': blockers}


def ensure_slot_accepting(db, slot: DeliverySlot, now: datetime | None = None) -> None:
    state = slot_acceptance(db, slot, now)
    if state['status'] == OPEN:
        return
    code, message = SLOT_ERRORS[state['status']]
    raise DomainError(code, message, 409, {
        'slotId': slot.id,
        'slotStatus': state['status'],
        'openAt': slot.open_at.isoformat() if slot.open_at else None,
        'cutoffAt': slot.cutoff_at.isoformat() if slot.cutoff_at else None,
    })


def slot_snapshot(slot: DeliverySlot, *, public: bool = True) -> dict:
    snapshot = {
        'id': slot.id,
        'campusId': slot.campus_id,
        'campusName': (object_session(slot).get(Campus, slot.campus_id).name
                       if object_session(slot) and slot.campus_id and object_session(slot).get(Campus, slot.campus_id) else None),
        'date': slot.date,
        'name': slot.name,
        'startsAt': slot.starts_at.isoformat(),
        'endsAt': slot.ends_at.isoformat(),
        'capacity': slot.capacity,
        'version': slot.version,
    }
    if slot.open_at is not None:
        snapshot['openAt'] = slot.open_at.isoformat()
    if slot.cutoff_at is not None:
        snapshot['cutoffAt'] = slot.cutoff_at.isoformat()
    if not public:
        snapshot['occupied'] = slot.occupied
        snapshot['enabled'] = slot.enabled
        snapshot['sealedAt'] = slot.sealed_at.isoformat() if slot.sealed_at else None
        snapshot['sealReason'] = slot.seal_reason
    return snapshot


def batch_effective_sealed(slot: DeliverySlot | None, now: datetime | None = None) -> bool:
    if slot is None:
        return True
    now = now or _utcnow()
    if slot.sealed_at is not None:
        return True
    return slot.cutoff_at is not None and now >= slot.cutoff_at


# ---------------------------------------------------------------------------
# Auto collection groups and regular batches


def campus_for_order(db, order: Order) -> Campus | None:
    campus_id = db.scalar(select(Location.campus_id).where(Location.id == order.location_id))
    if not campus_id:
        return None
    return db.get(Campus, campus_id)


def group_for_slot_campus(db, slot: DeliverySlot, campus: Campus) -> DeliveryGroup:
    from .delivery_scope import ensure_slot_campus
    ensure_slot_campus(db, slot, campus.id)
    group = db.scalar(select(DeliveryGroup).where(
        DeliveryGroup.slot_id == slot.id, DeliveryGroup.campus_id == campus.id))
    if group is not None:
        return group
    try:
        with db.begin_nested():
            group = DeliveryGroup(
                slot_id=slot.id, campus_id=campus.id,
                data={'createdBy': 'settlement', 'regularBatchId': None})
            db.add(group)
            db.flush()
    except IntegrityError:
        group = db.scalar(select(DeliveryGroup).where(
            DeliveryGroup.slot_id == slot.id,
            DeliveryGroup.campus_id == campus.id))
        if group is None:
            raise
    return group


def regular_batch_for_group(db, group: DeliveryGroup, slot: DeliverySlot,
                            campus: Campus) -> DeliveryBatch:
    batch = db.scalar(select(DeliveryBatch).where(
        DeliveryBatch.group_id == group.id,
        DeliveryBatch.is_regular.is_(True),
        DeliveryBatch.deleted_at.is_(None)))
    if batch is not None:
        if batch.slot_id != slot.id or batch.campus_id != campus.id or batch.group_id != group.id:
            raise DomainError('SLOT_CAMPUS_MISMATCH', '批次校区与时段不一致', 409)
        if batch.status in ('CANCELLED', 'COMPLETED'):
            active = db.scalar(select(DeliveryAssignment.id).where(
                DeliveryAssignment.batch_id == batch.id,
                DeliveryAssignment.active.is_(True)).limit(1))
            if active is not None or batch_effective_sealed(slot):
                raise DomainError('BATCH_NOT_ACCEPTING', '原批次不能继续接单，请协调配送安排', 409)
            batch.is_regular = False
            touch(batch)
            db.flush()
        else:
            return batch
    batch = DeliveryBatch(
        name=f'{slot.date} {slot.name} · {campus.name}',
        courier_id=None,
        starts_at=slot.starts_at,
        ends_at=slot.ends_at,
        status='PLANNED',
        reason='确认收款后自动归集',
        slot_id=slot.id,
        campus_id=campus.id,
        group_id=group.id,
        source='AUTO',
        is_regular=True,
        data={'createdBy': 'settlement', 'autoGrouped': True},
    )
    try:
        with db.begin_nested():
            db.add(batch)
            db.flush()
    except IntegrityError:
        batch = db.scalar(select(DeliveryBatch).where(
            DeliveryBatch.group_id == group.id,
            DeliveryBatch.is_regular.is_(True),
            DeliveryBatch.deleted_at.is_(None)))
        if batch is None:
            raise
        return batch
    group.data = {**(group.data or {}), 'regularBatchId': batch.id}
    touch(group)
    return batch


def add_to_auto_batch(db, actor, order: Order, slot: DeliverySlot,
                      campus: Campus) -> tuple[DeliveryBatch, DeliveryAssignment]:
    """Find or create the (slot, campus) regular batch and its assignment.

    Called while the slot row lock is held; the group unique key and the one
    active assignment index remain the last line of defence.
    """
    from .delivery_scope import ensure_slot_campus
    ensure_slot_campus(db, slot, campus.id)
    group = group_for_slot_campus(db, slot, campus)
    batch = regular_batch_for_group(db, group, slot, campus)
    if batch.status not in ('PLANNED', 'POSTPONED') or batch.deleted_at is not None:
        raise DomainError('BATCH_NOT_ACCEPTING', '该配送批次已进入执行阶段，不能自动归集', 409,
                          {'batchId': batch.id, 'status': batch.status})
    existing = db.scalar(select(DeliveryAssignment).where(
        DeliveryAssignment.order_id == order.id,
        DeliveryAssignment.active.is_(True)))
    if existing is not None:
        if existing.batch_id != batch.id:
            raise DomainError('BATCH_CONFLICT', '订单已有其他有效配送执行，请先处理原分配', 409)
        return batch, existing
    assignment = DeliveryAssignment(
        order_id=order.id, batch_id=batch.id, courier_id=batch.courier_id,
        status='PLANNED', active=True, address_review=order.address_review,
        data={
            'assignedAt': _utcnow().isoformat(),
            'assignedBy': actor.staff_id if actor else None,
            'reason': '确认收款后自动归集',
            'source': 'AUTO',
            'locationSnapshot': (order.data or {}).get('locationSnapshot'),
        })
    try:
        with db.begin_nested():
            db.add(assignment)
            db.flush()
    except IntegrityError:
        existing = db.scalar(select(DeliveryAssignment).where(
            DeliveryAssignment.order_id == order.id,
            DeliveryAssignment.active.is_(True)))
        if existing is None:
            raise
        if existing.batch_id != batch.id:
            raise DomainError('BATCH_CONFLICT', '订单已有其他有效配送执行', 409)
        return batch, existing
    return batch, assignment


# ---------------------------------------------------------------------------
# Effective arrangement


def _assignment_for_order(db, order: Order) -> DeliveryAssignment | None:
    return db.scalar(select(DeliveryAssignment).where(
        DeliveryAssignment.order_id == order.id,
        DeliveryAssignment.active.is_(True)))


def _latest_changes(db, order: Order, limit: int = 20) -> list[DeliveryChange]:
    return list(db.scalars(select(DeliveryChange).where(
        DeliveryChange.order_id == order.id).order_by(
        DeliveryChange.created_at.desc(), DeliveryChange.id.desc()).limit(limit)))


def arrangement_for_order(db, order: Order) -> dict:
    """Compute the one authoritative arrangement shown to every surface."""
    data = order.data or {}
    assignment = _assignment_for_order(db, order)
    batch = db.get(DeliveryBatch, assignment.batch_id) if assignment else None
    changes = _latest_changes(db, order)
    latest = changes[0] if changes else None
    pending = db.scalar(select(DeliveryChange).where(
        DeliveryChange.order_id == order.id, DeliveryChange.status == 'PENDING')
        .order_by(DeliveryChange.created_at.desc(), DeliveryChange.id.desc()).limit(1))
    applied = db.scalar(select(DeliveryChange).where(
        DeliveryChange.order_id == order.id, DeliveryChange.status == 'APPLIED')
        .order_by(DeliveryChange.created_at.desc(), DeliveryChange.id.desc()).limit(1))
    requested = data.get('requestedSlotSnapshot')
    confirmed = data.get('confirmedSlotSnapshot')
    current = confirmed
    if applied is not None and applied.proposed:
        current = applied.proposed
    elif pending is not None and pending.original:
        current = pending.original
    if current is None and batch is not None:
        current = {'startsAt': batch.starts_at.isoformat(), 'endsAt': batch.ends_at.isoformat(),
                   'name': batch.name, 'source': 'CURRENT_BATCH'}
    if current is None and order.confirmed_slot_id:
        fallback = db.get(DeliverySlot, order.confirmed_slot_id)
        if fallback is not None:
            current = {**slot_snapshot(fallback), 'source': 'CURRENT_SLOT'}
    if current is None:
        current = requested
    # Keep immutable historical snapshots intact while presenting the current
    # migrated slot identity. Applied ETA windows remain authoritative.
    migration = data.get('campusMigration') or {}
    if isinstance(current, dict) and isinstance(migration, dict):
        historical = confirmed or requested or {}
        old_id = current.get('slotId') or current.get('id') or historical.get('slotId') or historical.get('id')
        new_id = migration.get(old_id)
        effective_id = order.confirmed_slot_id or order.requested_slot_id
        if isinstance(new_id, str) and new_id == effective_id:
            migrated = db.get(DeliverySlot, new_id)
            if migrated:
                campus = db.get(Campus, migrated.campus_id) if migrated.campus_id else None
                current = {**current, 'id': migrated.id, 'name': migrated.name,
                           'campusId': migrated.campus_id,
                           'campusName': campus.name if campus else None}
                if 'slotId' in current:
                    current['slotId'] = migrated.id
    if order.status == 'CANCELLED':
        state = 'CANCELLED'
    elif order.status == 'DELIVERED':
        state = 'DELIVERED'
    elif pending is not None:
        state = 'WAITING_CUSTOMER'
    elif ((latest is not None and latest.status == 'REJECTED')
          or (data.get('slotProposal') or {}).get('rejected')):
        state = 'NEEDS_COORDINATION'
    elif order.confirmed_slot_id is None:
        state = 'REQUESTED'
    elif assignment is None:
        state = 'CONFIRMED_WAITING_ASSIGNMENT'
    elif batch is not None and batch.status == 'PLANNED':
        state = 'ASSIGNED_NOT_DISPATCHED'
    elif batch is not None and batch.status in ('PUBLISHED', 'POSTPONED'):
        state = 'READY_TO_DEPART'
    elif batch is not None and batch.status == 'DELIVERING':
        state = 'DELIVERING'
    else:
        state = 'CONFIRMED'
    confirmed_slot = db.get(DeliverySlot, order.confirmed_slot_id) if order.confirmed_slot_id else None
    sealed = bool(batch and (batch.sealed_at or batch_effective_sealed(confirmed_slot)))
    return {
        'version': order.arrangement_version or 0,
        'status': state,
        'updatedAt': (order.arrangement_updated_at or order.updated_at).isoformat(),
        'effective': current,
        'originalRequest': requested,
        'confirmed': confirmed,
        'originalConfirmation': data.get('settlementSlotSnapshot') or confirmed,
        'reason': latest.reason if latest else '',
        'sealed': sealed,
        'batch': ({
            'id': batch.id, 'name': batch.name, 'status': batch.status,
            'source': batch.source, 'isRegular': batch.is_regular,
            'sealedAt': batch.sealed_at.isoformat() if batch.sealed_at else None,
        } if batch else None),
        'assignment': ({
            'id': assignment.id, 'status': assignment.status,
            'version': assignment.version,
        } if assignment else None),
        'pendingProposal': ({
            'changeId': pending.id, 'reason': pending.reason,
            'original': pending.original, 'proposed': pending.proposed,
            'createdAt': pending.created_at.isoformat(),
        } if pending else None),
        'latestChange': ({
            'changeId': latest.id, 'kind': latest.kind, 'status': latest.status,
            'reason': latest.reason, 'original': latest.original,
            'proposed': latest.proposed,
            'confirmationRequired': latest.confirmation_required,
            'createdAt': latest.created_at.isoformat(),
        } if latest else None),
    }


def record_change(db, order: Order, *, kind: str, original: dict, proposed: dict,
                  reason: str, actor_id: str | None, batch_id: str | None = None,
                  slot_id: str | None = None, status: str = 'APPLIED',
                  confirmation_required: bool = False,
                  internal_note: str = '', plan_id: str | None = None,
                  data: dict | None = None) -> DeliveryChange:
    reason = (reason or '').strip()
    if not reason or len(reason) > 300:
        raise DomainError('INVALID_CHANGE_REASON', '配送变更原因须为 1 到 300 个字符', 422,
                          field_errors={'reason': ['请填写 1 到 300 个字符的原因']})
    change = DeliveryChange(
        order_id=order.id, batch_id=batch_id, slot_id=slot_id, kind=kind,
        status=status, confirmation_required=confirmation_required,
        original=original, proposed=proposed, reason=reason,
        internal_note=internal_note, actor_id=actor_id, plan_id=plan_id,
        data=data or {})
    db.add(change)
    order.arrangement_version = int(order.arrangement_version or 0) + 1
    order.arrangement_updated_at = _utcnow()
    touch(order)
    db.flush()
    return change


# ---------------------------------------------------------------------------
# Seal


def seal_preview(db, slot: DeliverySlot, actor) -> dict:
    now = _utcnow()
    groups = list(db.scalars(select(DeliveryGroup).where(DeliveryGroup.slot_id == slot.id)))
    batches = [batch for batch in db.scalars(select(DeliveryBatch).where(
        DeliveryBatch.deleted_at.is_(None)))
        if batch.slot_id == slot.id or batch.group_id in [g.id for g in groups]
        or (batch.slot_id is None and ((batch.data or {}).get('slotId') == slot.id
            or ((batch.data or {}).get('slotSnapshot') or {}).get('id') == slot.id))]
    blockers = []
    if not slot.campus_id or db.get(Campus, slot.campus_id) is None:
        blockers.append({'code': 'SLOT_CAMPUS_MISSING', 'message': '时段缺少校区', 'relatedId': slot.id})
    for group in groups:
        if group.campus_id != slot.campus_id:
            blockers.append({'code': 'SLOT_CAMPUS_MISMATCH', 'message': '配送组校区与时段不一致', 'relatedId': group.id})
    for batch in batches:
        if batch.campus_id != slot.campus_id:
            blockers.append({'code': 'SLOT_CAMPUS_MISMATCH', 'message': '批次校区与时段不一致', 'relatedId': batch.id})
        for assignment in db.scalars(select(DeliveryAssignment).where(DeliveryAssignment.batch_id == batch.id, DeliveryAssignment.active.is_(True))):
            order = db.get(Order, assignment.order_id)
            campus = campus_for_order(db, order) if order else None
            if campus is None or campus.id != slot.campus_id:
                blockers.append({'code': 'SLOT_CAMPUS_MISMATCH', 'message': '订单校区与时段不一致', 'relatedId': assignment.order_id})
    if (slot.data or {}).get('archivedAt'):
        blockers.append({'code': 'SLOT_ARCHIVED', 'message': '已归档时段不能封批', 'relatedId': slot.id})
    if slot.sealed_at is not None:
        blockers.append({'code': 'SLOT_ALREADY_SEALED', 'message': '该时段已封批', 'relatedId': slot.id})
    if slot.open_at is None or slot.cutoff_at is None:
        blockers.append({'code': 'SLOT_CONFIG_MISSING', 'message': '请先补齐接单规则', 'relatedId': slot.id})
    for batch in batches:
        if batch.status in ('PUBLISHED', 'DELIVERING', 'COMPLETED'):
            blockers.append({'code': 'BATCH_ALREADY_PUBLISHED',
                             'message': f'批次 {batch.name} 已发布或执行，不能提前封批',
                             'relatedId': batch.id})
    pending = list(db.scalars(select(Order).where(
        Order.requested_slot_id == slot.id,
        Order.confirmed_slot_id.is_(None),
        Order.status == 'PENDING_CONFIRMATION',
        Order.deleted_at.is_(None))))
    for order in pending:
        campus = campus_for_order(db, order)
        if campus is None or campus.id != slot.campus_id:
            blockers.append({'code': 'SLOT_CAMPUS_MISMATCH', 'message': '待确认订单校区与时段不一致', 'relatedId': order.id})
    campuses = []
    for group in groups:
        campus = db.get(Campus, group.campus_id)
        campus_orders = [order for order in pending
                         if (db.get(Location, order.location_id).campus_id if order.location_id else None) == group.campus_id]
        campuses.append({
            'campusId': group.campus_id,
            'campusName': campus.name if campus else '',
            'sealedAt': group.sealed_at.isoformat() if group.sealed_at else None,
            'batchCount': sum(1 for batch in batches if batch.group_id == group.id),
            'orderCount': db.scalar(select(func.count(DeliveryAssignment.id)).where(
                DeliveryAssignment.batch_id.in_([b.id for b in batches if b.group_id == group.id]),
                DeliveryAssignment.active.is_(True))) or 0,
            'pendingApplications': len(campus_orders),
        })
    if not groups and slot.campus_id:
        campus = db.get(Campus, slot.campus_id)
        campuses.append({'campusId': slot.campus_id, 'campusName': campus.name if campus else '',
                         'sealedAt': None, 'batchCount': len(batches), 'orderCount': 0,
                         'pendingApplications': sum(1 for order in pending if campus_for_order(db, order) and campus_for_order(db, order).id == slot.campus_id)})
    pending_by_campus = {}
    for order in pending:
        location = db.get(Location, order.location_id)
        pending_by_campus[location.campus_id if location else None] = pending_by_campus.get(location.campus_id if location else None, 0) + 1
    return {
        'slot': slot_snapshot(slot, public=False),
        'blockers': blockers,
        'impact': {
            'campuses': campuses,
            'pendingApplications': len(pending),
            'pendingByCampus': [
                {'campusId': campus_id, 'pendingApplications': count}
                for campus_id, count in pending_by_campus.items()
            ],
            'batchCount': len(batches),
            'orderCount': db.scalar(select(func.count(DeliveryAssignment.id)).where(
                DeliveryAssignment.batch_id.in_([b.id for b in batches]),
                DeliveryAssignment.active.is_(True))) if batches else 0,
        },
    }


def apply_seal(db, slot: DeliverySlot, actor, reason: str, plan_id: str | None = None) -> dict:
    preview = seal_preview(db, slot, actor)
    if preview['blockers']:
        raise DomainError('SEAL_BLOCKED', '当前存在阻断，不能封批', 409, {'blockers': preview['blockers']})
    now = _utcnow()
    slot.sealed_at = now
    slot.sealed_by = actor.staff_id
    slot.seal_reason = reason
    touch(slot)
    groups = list(db.scalars(select(DeliveryGroup).where(DeliveryGroup.slot_id == slot.id)
                            .order_by(DeliveryGroup.id).with_for_update()))
    for group in groups:
        group.sealed_at = now
        group.sealed_by = actor.staff_id
        group.seal_reason = reason
        touch(group)
    batch_ids = [batch.id for batch in db.scalars(select(DeliveryBatch).where(
        DeliveryBatch.deleted_at.is_(None)))
        if batch.slot_id == slot.id or batch.group_id in [g.id for g in groups]
        or (batch.slot_id is None and ((batch.data or {}).get('slotId') == slot.id
            or ((batch.data or {}).get('slotSnapshot') or {}).get('id') == slot.id))]
    batches = list(db.scalars(select(DeliveryBatch).where(DeliveryBatch.id.in_(batch_ids))
                              .order_by(DeliveryBatch.id).with_for_update()))
    for batch in batches:
        if batch.sealed_at is None:
            batch.sealed_at = now
            batch.sealed_by = actor.staff_id
            batch.seal_reason = reason
            touch(batch)
    return {'slot': slot, 'groups': groups, 'batches': batches}


def persist_seal_facts(db, slot: DeliverySlot, actor, reason: str) -> None:
    """Seal every batch that belongs to the slot without touching order state."""
    now = _utcnow()
    groups = list(db.scalars(select(DeliveryGroup).where(DeliveryGroup.slot_id == slot.id)))
    if not slot.campus_id or any(group.campus_id != slot.campus_id for group in groups):
        raise DomainError('SLOT_CAMPUS_MISMATCH', '配送组校区与时段不一致，不能封批', 409)
    group_ids = {group.id for group in groups}
    batch_ids = [batch.id for batch in db.scalars(select(DeliveryBatch).where(
        DeliveryBatch.deleted_at.is_(None)))
        if batch.slot_id == slot.id or batch.group_id in group_ids
        or (batch.slot_id is None and ((batch.data or {}).get('slotId') == slot.id
            or ((batch.data or {}).get('slotSnapshot') or {}).get('id') == slot.id))]
    batches = list(db.scalars(select(DeliveryBatch).where(DeliveryBatch.id.in_(batch_ids))
                              .order_by(DeliveryBatch.id).with_for_update()))
    for batch in batches:
        if batch.campus_id != slot.campus_id:
            raise DomainError('SLOT_CAMPUS_MISMATCH', '批次校区与时段不一致，不能封批', 409)
        for assignment in db.scalars(select(DeliveryAssignment).where(
                DeliveryAssignment.batch_id == batch.id, DeliveryAssignment.active.is_(True))):
            order = db.get(Order, assignment.order_id)
            campus = campus_for_order(db, order) if order else None
            if campus is None or campus.id != slot.campus_id:
                raise DomainError('SLOT_CAMPUS_MISMATCH', '订单校区与时段不一致，不能封批', 409)
        if batch.sealed_at is None:
            batch.sealed_at = now
            batch.sealed_by = actor.staff_id
            batch.seal_reason = reason
            touch(batch)


# ---------------------------------------------------------------------------
# paid-order arrangement proposals (plan §8.2 B)
# ---------------------------------------------------------------------------


def _target_slot_problem(db, order, slot, now=None) -> tuple[str, str] | None:
    """Return (code, message) when *slot* cannot receive this paid order."""
    now = now or utcnow()
    if slot is None or slot.id is None:
        return 'SLOT_NOT_FOUND', '配送时段不存在'
    if (slot.data or {}).get('archivedAt'):
        return 'SLOT_ARCHIVED', '配送时段已归档'
    if slot.open_at is None or slot.cutoff_at is None:
        return 'SLOT_CONFIG_MISSING', '配送时段尚未补齐接单规则'
    if slot.sealed_at is not None:
        return 'SLOT_SEALED', '配送时段已封批'
    if not slot.enabled:
        return 'SLOT_DISABLED', '配送时段已停用'
    if now >= slot.cutoff_at:
        return 'SLOT_CUTOFF', '配送时段已截单'
    if now >= slot.starts_at:
        return 'SLOT_DEPARTED', '配送时段已开始'
    if slot.occupied >= slot.capacity:
        return 'SLOT_FULL', '配送时段已满'
    campus = campus_for_order(db, order)
    if campus is None:
        return 'CAMPUS_CONFIG_MISSING', '订单缺少校区信息'
    if not slot.campus_id:
        return 'SLOT_CAMPUS_MISSING', '配送时段缺少校区'
    if slot.campus_id != campus.id:
        return 'SLOT_CAMPUS_MISMATCH', '订单与时段校区不一致'
    if slot.id == order.confirmed_slot_id:
        return 'SLOT_UNCHANGED', '目标时段与原安排相同'
    state = slot_acceptance(db, slot, now)
    if state['status'] != OPEN:
        return SLOT_ERRORS[state['status']]
    return None


def _ensure_reschedulable(db, order: Order) -> None:
    if (order.status in ('DELIVERING', 'DELIVERED', 'CANCELLED')
            or order.deleted_at or order.refund_locked):
        raise DomainError('INVALID_TRANSITION', '配送已开始、订单已结束或退款处理中，不能直接改订', 409)
    assignment = _assignment_for_order(db, order)
    if assignment is not None and assignment.status in ('STARTED', 'DELIVERED'):
        raise DomainError('ASSIGNMENT_STARTED', '当前配送执行已开始，不能直接改订', 409)


def propose_reschedule(db, actor, order: Order, slot, reason: str) -> DeliveryChange:
    _ensure_reschedulable(db, order)
    problem = _target_slot_problem(db, order, slot)
    if problem is not None:
        code, message = problem
        raise DomainError(code, message, 409,
                          {'orderId': order.id, 'slotId': slot.id if slot else None})
    if order.status in ('DELIVERED', 'CANCELLED') or order.deleted_at:
        raise DomainError('INVALID_TRANSITION', '当前订单不能调整配送安排', 409,
                          {'status': order.status})
    if not order.confirmed_slot_id or not order.settlement_kind:
        raise DomainError('ARRANGEMENT_NOT_CONFIRMED', '订单尚未确认配送时段', 409)
    current = arrangement_for_order(db, order)
    original = current.get('effective') or current.get('confirmed') or {}
    for old in db.scalars(select(DeliveryChange).where(
            DeliveryChange.order_id == order.id, DeliveryChange.status == 'PENDING')):
        old.status = 'SUPERSEDED'
        touch(old)
    proposed = {**slot_snapshot(slot, public=False), 'slotId': slot.id}
    change = record_change(
        db, order, kind='RESCHEDULE_PROPOSED', original=original, proposed=proposed,
        reason=reason, actor_id=actor.id if actor else None, slot_id=slot.id,
        status='PENDING', confirmation_required=True)
    data = dict(order.data or {})
    data['slotProposal'] = {'slotId': slot.id, 'reason': reason, 'confirmed': False,
                            'changeId': change.id,
                            'requestedAt': utcnow().isoformat()}
    order.data = data
    touch(order)
    from .growth import notify_delivery_change
    notify_delivery_change(db, order, change)
    return change


def reject_reschedule(db, actor, order: Order, reason: str) -> DeliveryChange:
    change = db.scalar(
        select(DeliveryChange)
        .where(DeliveryChange.order_id == order.id,
               DeliveryChange.kind == 'RESCHEDULE_PROPOSED',
               DeliveryChange.status == 'PENDING')
        .order_by(DeliveryChange.created_at.desc()))
    if change is None:
        raise DomainError('NO_PENDING_ARRANGEMENT', '当前没有待确认的配送调整', 409,
                          {'orderId': order.id})
    change.status = 'REJECTED'
    change.reason = reason or change.reason
    change.data = {**(change.data or {}), 'rejectedAt': utcnow().isoformat(),
                   'rejectedBy': actor.id if actor else None}
    touch(change)
    data = dict(order.data or {})
    data['slotProposal'] = {**(data.get('slotProposal') or {}),
                            'rejected': True, 'confirmed': False}
    order.data = data
    order.arrangement_version = int(order.arrangement_version or 0) + 1
    order.arrangement_updated_at = utcnow()
    touch(order)
    return change


def confirm_reschedule(db, actor, order: Order, slot_id: str) -> DeliveryChange:
    from .models import DeliveryAssignment

    _ensure_reschedulable(db, order)
    data = dict(order.data or {})
    proposal = data.get('slotProposal') or {}
    if not proposal or proposal.get('slotId') != slot_id:
        raise DomainError('SLOT_PROPOSAL_MISMATCH', '没有匹配的配送调整待确认', 409,
                          {'slotId': slot_id})
    change = db.get(DeliveryChange, proposal.get('changeId')) if proposal.get('changeId') else None
    if change is None or change.status != 'PENDING':
        raise DomainError('NO_PENDING_ARRANGEMENT', '配送调整已失效，请刷新后重试', 409,
                          {'orderId': order.id})
    source_id = order.confirmed_slot_id
    locked_slots = {sid: lock(db, DeliverySlot, sid)
                    for sid in sorted({slot_id, source_id} - {None})}
    target = locked_slots[slot_id]
    source = locked_slots.get(source_id) if source_id and source_id != slot_id else None
    problem = _target_slot_problem(db, order, target)
    if problem is not None:
        code, message = problem
        # The target may have been reserved after proposal; keep the pending
        # change visible so the admin can propose another slot.
        raise DomainError(code, message, 409,
                          {'orderId': order.id, 'slotId': slot_id,
                           'changeId': change.id})
    campus = campus_for_order(db, order)
    assignment = db.scalar(select(DeliveryAssignment).where(
        DeliveryAssignment.order_id == order.id,
        DeliveryAssignment.active.is_(True)).with_for_update().execution_options(populate_existing=True))
    if assignment is not None and assignment.status in ('STARTED', 'DELIVERED'):
        raise DomainError('ASSIGNMENT_STARTED', '当前配送执行已开始，不能直接改订', 409)
    if source is None:
        raise DomainError('ARRANGEMENT_NOT_CONFIRMED', '订单缺少有效原配送时段', 409)
    if int(source.occupied or 0) < 1:
        raise DomainError('OCCUPANCY_REVIEW_REQUIRED', '原时段占用数异常，请先核对', 409,
                          {'slotId': source.id, 'orderId': order.id})
    old_batch_id = assignment.batch_id if assignment else None
    old_batch = lock(db, DeliveryBatch, old_batch_id) if old_batch_id else None
    from .delivery_scope import ensure_slot_campus
    ensure_slot_campus(db, source, campus.id if campus else None, allow_legacy=assignment is not None)
    if assignment is not None:
        from .delivery import _batch_slot_id, _campus_mismatch_blocker
        if old_batch is None or (_batch_slot_id(old_batch) and _batch_slot_id(old_batch) != source.id):
            raise DomainError('ORDER_SLOT_MISMATCH', '原批次与订单时段不一致，请先核对', 409)
        mismatch = _campus_mismatch_blocker(db, old_batch, order, existing=True)
        if mismatch:
            raise DomainError(mismatch['code'], mismatch['message'], 409)
    target_batch = None
    try:
        with db.begin_nested():
            group = group_for_slot_campus(db, target, campus)
            target_batch = regular_batch_for_group(db, group, target, campus)
            if target_batch.status not in ('PLANNED', 'POSTPONED'):
                raise DomainError('BATCH_NOT_ACCEPTING', '目标批次已发布或出发，不能加入', 409)
            if assignment is not None:
                assignment.active = False
                assignment.status = 'CANCELLED'
                assignment.data = {**(assignment.data or {}),
                                   'releasedAt': utcnow().isoformat(),
                                   'releasedBy': actor.id if actor else None,
                                   'releaseReason': '客户确认新的配送时段'}
                touch(assignment)
            db.flush()
            db.add(DeliveryAssignment(
                order_id=order.id, batch_id=target_batch.id, courier_id=target_batch.courier_id,
                status='PLANNED', active=True, address_review=order.address_review,
                data={'assignedAt': utcnow().isoformat(),
                      'assignedBy': actor.id if actor else None,
                      'reason': '客户确认配送调整',
                      'source': 'RESCHEDULE',
                      'locationSnapshot': (assignment.data or {}).get('locationSnapshot')
                      if assignment is not None else None}))
            db.flush()
    except DomainError:
        raise
    except Exception as error:  # pragma: no cover - defensive transaction guard
        raise DomainError('RESCHEDULE_FAILED', '配送调整未能完成，请稍后重试', 409,
                          {'orderId': order.id, 'slotId': slot_id}) from error
    if source is not None:
        source.occupied = int(source.occupied) - 1
        touch(source)
    target.occupied = int(target.occupied or 0) + 1
    order.confirmed_slot_id = target.id
    data = dict(order.data or {})
    data.pop('slotProposal', None)
    data.setdefault('settlementSlotSnapshot', data.get('confirmedSlotSnapshot'))
    data['confirmedSlotSnapshot'] = slot_snapshot(target, public=False)
    data['lastArrangementChange'] = {
        'changeId': change.id, 'kind': change.kind, 'reason': change.reason,
        'at': utcnow().isoformat(), 'batchId': target_batch.id,
        'batchName': target_batch.name}
    order.data = data
    order.arrangement_version = max(1, int(order.arrangement_version or 0)) + 1
    order.arrangement_updated_at = utcnow()
    change.status = 'APPLIED'
    change.confirmation_required = False
    change.data = {**(change.data or {}), 'confirmedAt': utcnow().isoformat(),
                   'confirmedBy': actor.id if actor else None,
                   'fromSlotId': source_id, 'toSlotId': target.id,
                   'batchId': target_batch.id, 'oldBatchId': old_batch_id}
    touch(change)
    touch(order)
    if old_batch_id:
        from .delivery import refresh_batch
        refresh_batch(db, old_batch_id)
    applied = record_change(
        db, order, kind='RESCHEDULE_CONFIRMED', original=change.original,
        proposed=slot_snapshot(target), reason=change.reason,
        actor_id=actor.id if actor else None, batch_id=target_batch.id,
        slot_id=target.id, data={'proposalId': change.id})
    from .growth import notify_delivery_change
    notify_delivery_change(db, order, applied)
    return change
