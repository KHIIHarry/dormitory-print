"""Map, slot, batch scheduling and courier delivery execution.

Owns only delivery facts: active ``DeliveryAssignment`` rows are the execution
identity (never a batch id), capacity is provided by slots but consumed by
commerce settlement, and batch start is an all-or-nothing guarded transition.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .config import get_config
from .core import (
    Actor,
    DomainError,
    Envelope,
    actor_dep,
    audit,
    lock,
    ok,
    require_admin,
    require_staff,
    touch,
)
from .db import get_db
from .delivery_schemas import (
    AdminMapView,
    AddressReviewInput,
    BatchCreate,
    BatchDeliveryProofInput,
    BatchDeliveryProofResultView,
    BatchListView,
    BatchOperationCommit,
    BatchOperationPreview,
    BatchOperationResultView,
    BatchPlanView,
    BatchUpdate,
    BatchVersionInput,
    BatchView,
    CourierBatchDetailView,
    CourierExceptionInput,
    DeliveryExceptionListView,
    DeliveryExceptionView,
    DeliveryOrderView,
    DeliveryProofInput,
    DeliveryStaffListView,
    ExceptionDecisionInput,
    ExceptionResolveInput,
    LOCATION_FAILURE_CODES,
    LocationExceptionInput,
    LocationView,
    MapHotspotUpdate,
    MapPublishInput,
    PublicMapView,
    SlotDeleteInput,
    SlotInput,
    SlotListView,
    SlotUpdate,
    SlotView,
    batch_window,
    normalize_exception_type,
    position_errors,
    proof_shape_errors,
    slot_weekday,
    slot_window,
    to_shanghai_date,
    to_shanghai_hhmm,
    to_shanghai_window,
)
from .models import (
    Account,
    Asset,
    BatchPlan,
    BindingTask,
    BusinessException,
    DeliveryAssignment,
    DeliveryBatch,
    DeliveryProof,
    DeliverySlot,
    Location,
    Order,
    PackingTask,
    Payment,
    PrintTask,
    Staff,
    utcnow,
)

router = APIRouter()

PLAN_TTL = timedelta(minutes=10)
OPEN_EXCEPTION_STATUSES = ('OPEN', 'IN_PROGRESS')
# Deterministic lock order for every delivery transaction that touches more
# than one aggregate: batch rows (sorted by id) -> order rows (sorted by id)
# -> delivery assignments (sorted by id).  Address review only needs
# order -> assignment; map configuration locks section first, then locations.
# Capacity is supplied by DeliverySlot but is consumed by commerce settlement;
# batch operations never increment DeliverySlot.occupied (Q16).


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _staff_display_name(db, staff: Staff | None) -> str:
    """Public courier name from the linked customer nickname (FINAL N05)."""
    if staff is None:
        return ''
    if staff.account_id:
        account = db.get(Account, staff.account_id)
        if account is not None and account.deleted_at is None:
            nickname = (account.display_name or '').strip()
            if nickname:
                return nickname
    return (staff.name or '').strip()


def _is_admin(actor: Actor) -> bool:
    return actor.staff_role == 'ADMIN' and not actor.restricted


def _require_delivery_actor(actor: Actor) -> None:
    """Courier execution stays courier-only; an ADMIN needs delivery.manage.

    We deliberately do not widen this to a global staff permission: the
    courier object guards (current assignment / current batch courier) remain
    in force for COURIER actors, while ADMIN access requires the explicit
    scheduling capability instead of the old all-ADMIN bypass.
    """
    require_staff(actor)
    if actor.staff_role == 'ADMIN':
        require_admin(actor, 'delivery.manage')


def _blocker(code: str, message: str, related_id: str | None = None) -> dict:
    return {'code': code, 'message': message, 'relatedId': related_id}


def _location_snapshot(db, order: Order) -> dict:
    location = db.get(Location, order.location_id)
    if location is None:
        return {'locationId': order.location_id, 'name': '', 'type': 'DORMITORY',
                'x': 0.0, 'y': 0.0, 'mapVersion': 1}
    return {'locationId': location.id, 'name': location.name, 'type': location.type,
            'x': location.x, 'y': location.y, 'mapVersion': location.map_version}


def _slot_brief(db, slot_id: str | None) -> dict | None:
    if not slot_id:
        return None
    slot = db.get(DeliverySlot, slot_id)
    if slot is None:
        return None
    return {'id': slot.id, 'date': slot.date, 'weekday': slot_weekday(slot.date),
            'name': slot.name, 'start': to_shanghai_hhmm(slot.starts_at),
            'end': to_shanghai_hhmm(slot.ends_at), 'startsAt': slot.starts_at,
            'endsAt': slot.ends_at}


def _workflow_limits(db) -> tuple[int, float]:
    data = get_config(db, 'workflow').data
    max_age = int(data.get('locationMaxAgeSeconds') or 300)
    max_accuracy = float(data.get('locationMaxAccuracyMeters') or 100)
    return max_age, max_accuracy


def _location_view(location: Location, map_version: int) -> dict:
    return {'id': location.id, 'name': location.name, 'type': location.type,
            'x': round(location.x * 100, 4), 'y': round(location.y * 100, 4),
            'enabled': location.enabled, 'mapVersion': location.map_version,
            'version': location.version}


def _slot_archived(slot: DeliverySlot) -> bool:
    return bool((slot.data or {}).get('archivedAt'))


def _slot_snapshot(slot: DeliverySlot) -> dict:
    return {
        'id': slot.id, 'date': slot.date, 'weekday': slot_weekday(slot.date),
        'name': slot.name, 'start': to_shanghai_hhmm(slot.starts_at),
        'end': to_shanghai_hhmm(slot.ends_at),
        'startsAt': slot.starts_at.isoformat(), 'endsAt': slot.ends_at.isoformat(),
        'capacity': slot.capacity, 'version': slot.version,
    }


def _batch_slot_id(batch: DeliveryBatch) -> str | None:
    data = batch.data or {}
    return data.get('slotId') or data.get('slot_id') or (data.get('slotSnapshot') or {}).get('id')


def _order_effective_slot_id(order: Order) -> str | None:
    return order.confirmed_slot_id or order.requested_slot_id


def _slot_view(slot: DeliverySlot, now: datetime, *, public: bool = False) -> dict:
    remaining = max(0, slot.capacity - slot.occupied)
    archived = _slot_archived(slot)
    selectable = bool(slot.enabled and not archived and slot.ends_at > now and remaining > 0)
    return {
        'id': slot.id,
        'date': slot.date,
        'weekday': slot_weekday(slot.date),
        'name': slot.name,
        'start': to_shanghai_hhmm(slot.starts_at),
        'end': to_shanghai_hhmm(slot.ends_at),
        'startsAt': slot.starts_at,
        'endsAt': slot.ends_at,
        'capacity': slot.capacity,
        'occupied': slot.occupied,
        'remaining': remaining,
        'enabled': slot.enabled,
        'selectable': selectable,
        'archived': archived,
        'version': slot.version,
    }


def _batch_view(db, batch: DeliveryBatch) -> dict:
    assignments = _active_batch_assignments(db, batch.id)
    courier = db.get(Staff, batch.courier_id) if batch.courier_id else None
    published_at = (batch.data or {}).get('publishedAt')
    slot_id = _batch_slot_id(batch)
    slot_snapshot = (batch.data or {}).get('slotSnapshot') or {}
    slot = db.get(DeliverySlot, slot_id) if slot_id else None
    slot_name = slot_snapshot.get('name') or (slot.name if slot else None)
    return {
        'id': batch.id,
        'name': batch.name,
        'date': to_shanghai_date(batch.starts_at),
        'time': to_shanghai_window(batch.starts_at, batch.ends_at),
        'startsAt': batch.starts_at.isoformat(),
        'endsAt': batch.ends_at.isoformat(),
        'slotId': slot_id,
        'slotName': slot_name,
        'courierId': batch.courier_id,
        'courierName': _staff_display_name(db, courier) if courier else None,
        'status': batch.status,
        'orderIds': [assignment.order_id for assignment in assignments],
        'assignmentIds': [assignment.id for assignment in assignments],
        'reason': batch.reason,
        'version': batch.version,
        'publishedAt': published_at if isinstance(published_at, str) else None,
        'createdAt': batch.created_at.isoformat(),
        'updatedAt': batch.updated_at.isoformat(),
    }


def _active_batch_assignments(db, batch_id: str) -> list[DeliveryAssignment]:
    return list(db.scalars(select(DeliveryAssignment)
                           .where(DeliveryAssignment.batch_id == batch_id,
                                  DeliveryAssignment.active.is_(True))
                           .order_by(DeliveryAssignment.order_id, DeliveryAssignment.id)))


def refresh_batch(db, batch_id: str) -> DeliveryBatch | None:
    """Recompute a started batch's terminal projection from assignment facts.

    Exported for after-sales: merchant-responsibility rework deactivates the old
    executed assignment while preserving its DELIVERED status and proof.  Once no
    active execution remains, the old batch must not stay DELIVERING forever.
    This helper never deletes proofs/payments and never touches terminal batches.
    """
    batch = db.get(DeliveryBatch, batch_id)
    if batch is None or batch.status != 'DELIVERING':
        return batch
    unfinished = db.scalar(select(func.count(DeliveryAssignment.id))
                           .where(DeliveryAssignment.batch_id == batch.id,
                                  DeliveryAssignment.active.is_(True),
                                  DeliveryAssignment.status.in_(('PLANNED', 'STARTED', 'EXCEPTION'))))
    if unfinished:
        return batch
    delivered = db.scalar(select(func.count(DeliveryAssignment.id))
                          .where(DeliveryAssignment.batch_id == batch.id,
                                 DeliveryAssignment.status == 'DELIVERED'))
    batch.status = 'COMPLETED' if delivered else 'CANCELLED'
    batch.data = {**(batch.data or {}), 'endedAt': utcnow().isoformat(),
                  'endedReason': 'all delivery executions are terminal'}
    touch(batch)
    return batch


def _assignment_view(db, assignment: DeliveryAssignment) -> dict:
    batch = db.get(DeliveryBatch, assignment.batch_id)
    courier = db.get(Staff, assignment.courier_id) if assignment.courier_id else None
    data = assignment.data or {}
    return {
        'id': assignment.id,
        'orderId': assignment.order_id,
        'batchId': assignment.batch_id,
        'batchName': batch.name if batch else None,
        'courierId': assignment.courier_id,
        'courierName': _staff_display_name(db, courier) if courier else None,
        'status': assignment.status,
        'version': assignment.version,
        'addressReview': assignment.address_review,
        'locationSnapshot': data.get('locationSnapshot'),
        'startedAt': data.get('startedAt'),
        'deliveredAt': data.get('deliveredAt'),
    }


def _proof_view(db, proof: DeliveryProof) -> dict:
    data = proof.data or {}
    return {
        'id': proof.id,
        'orderId': proof.order_id,
        'assignmentId': proof.assignment_id,
        'photoAssetId': proof.photo_asset_id,
        'skipPhotoReason': data.get('skipPhotoReason'),
        'position': data.get('position'),
        'skipLocationReason': data.get('skipLocationReason'),
        'locationExceptionId': data.get('locationExceptionId'),
        'placementNote': data.get('placementNote'),
        'deliveredAt': data.get('serverDeliveredAt') or proof.created_at,
        'operatorId': data.get('operatorId') or '',
    }


def _active_assignment(db, order_id: str) -> DeliveryAssignment | None:
    return db.scalar(select(DeliveryAssignment)
                     .where(DeliveryAssignment.order_id == order_id,
                            DeliveryAssignment.active.is_(True)))


def _proof_for_assignment(db, assignment_id: str) -> DeliveryProof | None:
    return db.scalar(select(DeliveryProof)
                     .where(DeliveryProof.assignment_id == assignment_id,
                            DeliveryProof.active.is_(True))
                     .order_by(DeliveryProof.created_at.desc()))


def _payment_confirmed(order: Order) -> bool:
    return (order.settlement_kind in ('CASH_MANUAL', 'ZERO_PAYABLE')
            and order.cash_received_cents == order.total_cents)


def _order_blockers(db, order: Order, assignment: DeliveryAssignment | None = None) -> list[dict]:
    blockers: list[dict] = []
    if order.deleted_at:
        blockers.append(_blocker('ORDER_DELETED', '订单已删除', order.id))
        return blockers
    if order.status == 'CANCELLED':
        blockers.append(_blocker('ORDER_CANCELLED', '订单已撤销', order.id))
    if not _payment_confirmed(order):
        blockers.append(_blocker('PAYMENT_REQUIRED', '尚未完成人工收款或零元结算', order.id))
    if order.refund_locked:
        blockers.append(_blocker('REFUND_LOCKED', '退款审核期间不能开始配送', order.id))
    payment = db.scalar(select(Payment).where(Payment.order_id == order.id))
    if payment is not None and (payment.amount_cents != order.cash_received_cents
                                or payment.kind != order.settlement_kind):
        blockers.append(_blocker('SETTLEMENT_MISMATCH', '结算记录与订单实收不一致，请核对', order.id))
    packing = db.scalar(select(PackingTask).where(PackingTask.order_id == order.id))
    if (order.packing_status != 'PACKED' or packing is None
            or packing.status != 'PACKED'
            or packing.manifest_revision != order.content_revision):
        blockers.append(_blocker('PACKING_REQUIRED', '尚未完成人工装袋或装袋清单已失效', order.id))
    if order.address_review or (assignment is not None and assignment.address_review):
        blockers.append(_blocker('ADDRESS_REVIEW_REQUIRED', '地址修改后尚待管理员复核', order.id))
    active_tasks = list(db.scalars(select(PrintTask).where(PrintTask.order_id == order.id,
                                                           PrintTask.active.is_(True))))
    if not active_tasks:
        blockers.append(_blocker('PRINT_NOT_COMPLETE', '没有当前有效的打印任务', order.id))
    elif any(task.status != 'COMPLETED' for task in active_tasks):
        blockers.append(_blocker('PRINT_NOT_COMPLETE', '仍有打印任务未完成', order.id))
    bindings = list(db.scalars(select(BindingTask).where(BindingTask.order_id == order.id)))
    if any(task.status not in ('COMPLETED', 'NOT_REQUIRED') for task in bindings):
        blockers.append(_blocker('BINDING_NOT_COMPLETE', '仍有装订任务未完成', order.id))
    blocking = db.scalar(select(func.count(BusinessException.id))
                         .where(BusinessException.order_id == order.id,
                                BusinessException.status.in_(OPEN_EXCEPTION_STATUSES),
                                BusinessException.blocks_delivery.is_(True)))
    if blocking:
        blockers.append(_blocker('DELIVERY_BLOCKED', '存在未解决的阻断异常', order.id))
    return blockers


def _start_blockers(db, batch: DeliveryBatch, assignments: list[DeliveryAssignment],
                    orders_by_id: dict[str, Order]) -> list[dict]:
    blockers: list[dict] = []
    pauses = (get_config(db, 'workflow').data or {}).get('pauses') or {}
    if pauses.get('delivery'):
        blockers.append(_blocker('DELIVERY_PAUSED', '配送已暂停，不能开始新批次', batch.id))
    if batch.deleted_at:
        blockers.append(_blocker('BATCH_DELETED', '批次已删除', batch.id))
    if batch.status != 'PUBLISHED':
        blockers.append(_blocker('BATCH_NOT_PUBLISHED', '批次尚未发布，不能开始配送', batch.id))
    if _valid_courier(db, batch.courier_id) is None:
        blockers.append(_blocker('COURIER_REQUIRED', '批次未分配有效配送员', batch.id))
    if not assignments:
        blockers.append(_blocker('BATCH_EMPTY', '批次没有可配送订单', batch.id))
    for assignment in assignments:
        if assignment.batch_id != batch.id or not assignment.active:
            blockers.append(_blocker('ASSIGNMENT_INVALID', '配送执行关联无效', assignment.id))
            continue
        if assignment.courier_id != batch.courier_id:
            blockers.append(_blocker('ASSIGNMENT_COURIER_MISMATCH', '配送执行与批次配送员不一致', assignment.id))
        if assignment.status != 'PLANNED':
            blockers.append(_blocker('ASSIGNMENT_NOT_PLANNED', '配送执行状态不允许开始', assignment.id))
        order = orders_by_id.get(assignment.order_id)
        if order is None:
            blockers.append(_blocker('ORDER_NOT_FOUND', '订单不存在', assignment.order_id))
            continue
        if order.status != 'READY_FOR_DELIVERY':
            blockers.append(_blocker('ORDER_NOT_READY', '订单当前不在待配送状态', order.id))
        mismatch = _slot_mismatch_blocker(batch, order)
        if mismatch is not None:
            blockers.append(mismatch)
        blockers.extend(_order_blockers(db, order, assignment))
    return blockers


def _refresh_production(db, order: Order) -> None:
    """Refresh shared production projections inside the caller's transaction."""
    from .production import refresh_production

    refresh_production(db, order)


def _refresh_production_safely(db, order: Order) -> None:
    try:
        _refresh_production(db, order)
        db.refresh(order)
    except ImportError:
        # Production module is being integrated by Codex; its absence must be a
        # hard deployment error, but module-level delivery tests patch this hook.
        raise DomainError('PRODUCTION_UNAVAILABLE', '生产模块尚未就绪，不能开始配送', 503)


def _qualify_referral(db, order: Order) -> None:
    from .growth import qualify_referral

    qualify_referral(db, order)


def _audit_batch(db, actor: Actor, batch: DeliveryBatch, action: str, reason: str, data=None) -> None:
    audit(db, actor, action, 'DELIVERY_BATCH', batch.id, reason=reason, data=data or {})


def _active_exception(db, order_id: str, exception_type: str, statuses) -> BusinessException | None:
    return db.scalar(select(BusinessException)
                     .where(BusinessException.order_id == order_id,
                            BusinessException.type == exception_type,
                            BusinessException.status.in_(tuple(statuses)))
                     .order_by(BusinessException.created_at.desc()))


def _approved_location_exception(db, assignment: DeliveryAssignment) -> BusinessException | None:
    rows = db.scalars(select(BusinessException)
                      .where(BusinessException.order_id == assignment.order_id,
                             BusinessException.type == 'LOCATION_EXCEPTION',
                             BusinessException.status == 'APPROVED'))
    for exception in rows:
        data = exception.data or {}
        if data.get('assignmentId') == assignment.id and data.get('assignmentVersion') == assignment.version:
            return exception
    return None


def _location_exception_approval_view(exception: BusinessException) -> dict:
    data = exception.data or {}
    return {
        'id': exception.id,
        'status': 'APPROVED',
        'assignmentId': data.get('assignmentId'),
        'assignmentVersion': int(data.get('assignmentVersion') or 0),
        'reason': exception.reason,
        'failureCode': data.get('code'),
        'approvedAt': data.get('decidedAt') or exception.created_at,
    }


def _valid_courier(db, courier_id: str | None) -> Staff | None:
    """Return an assignable courier.

    COURIER is always eligible.  An unrestricted ADMIN explicitly holding
    ``delivery.manage`` (or ``*``) may also be assigned to physically deliver,
    because that capability already grants the courier-facing delivery entry.
    Other ADMINs (for example mail.manage-only) remain ineligible.
    """
    if not courier_id:
        return None
    courier = db.get(Staff, courier_id)
    if courier is None or not courier.enabled or courier.status != 'ACTIVE':
        return None
    if courier.role == 'COURIER':
        return courier
    if (courier.role == 'ADMIN' and not courier.must_change_password
            and ('*' in (courier.capabilities or [])
                 or 'delivery.manage' in (courier.capabilities or []))):
        return courier
    return None


# ---------------------------------------------------------------------------
# public map and slots
# ---------------------------------------------------------------------------


@router.get('/public/map', response_model=Envelope[PublicMapView])
def public_map(db=Depends(get_db)):
    row = get_config(db, 'map')
    data = row.data or {}
    version = int(data.get('mapVersion') or 1)
    items = [_location_view(location, version) for location in db.scalars(
        select(Location).where(Location.enabled.is_(True)).order_by(Location.name, Location.id))]
    published_at = data.get('publishedAt')
    return ok({
        'version': version,
        'mapAssetId': data.get('assetId'),
        'mapUrl': data.get('mapUrl') or None,
        'publishedAt': datetime.fromisoformat(published_at) if isinstance(published_at, str) else published_at,
        'items': items,
    })


@router.get('/public/slots', response_model=Envelope[SlotListView])
def public_slots(date: str | None = None, db=Depends(get_db)):
    now = utcnow()
    query = select(DeliverySlot).where(DeliverySlot.enabled.is_(True))
    if date:
        query = query.where(DeliverySlot.date == date)
    else:
        query = query.where(DeliverySlot.date >= to_shanghai_date(now))
    slots = list(db.scalars(query.order_by(DeliverySlot.starts_at, DeliverySlot.id)))
    return ok({'items': [_slot_view(slot, now, public=True) for slot in slots
                         if not _slot_archived(slot)]})


# ---------------------------------------------------------------------------
# admin map configuration
# ---------------------------------------------------------------------------


@router.get('/admin/maps', response_model=Envelope[AdminMapView])
def admin_map(db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'delivery.manage')
    row = get_config(db, 'map')
    data = row.data or {}
    version = int(data.get('mapVersion') or 1)
    locations = list(db.scalars(select(Location).order_by(Location.name, Location.id)))
    items = [_location_view(location, version) for location in locations]
    published_at = data.get('publishedAt')
    return ok({
        'version': version,
        'mapAssetId': data.get('assetId'),
        'mapUrl': data.get('mapUrl') or None,
        'publishedAt': datetime.fromisoformat(published_at) if isinstance(published_at, str) else published_at,
        'items': items,
        'hotspots': items,
    })


def _check_public_map_asset(db, asset_id: str) -> Asset:
    asset = db.get(Asset, asset_id)
    if (asset is None or asset.deleted_at is not None or asset.status != 'READY'
            or asset.purpose != 'PUBLIC_MAP'):
        raise DomainError('INVALID_PUBLIC_ASSET', '请选择已上传并就绪的公开地图资产', 422)
    return asset


@router.post('/admin/maps', response_model=Envelope[AdminMapView])
def admin_map_publish(payload: MapPublishInput, db=Depends(get_db),
                      actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'delivery.manage')
    row = get_config(db, 'map')
    lock(db, type(row), row.id, payload.expected_version)
    data = deepcopy(row.data or {})
    current_version = int(data.get('mapVersion') or 1)
    current_asset = data.get('assetId')
    if payload.map_asset_id and payload.map_asset_id != current_asset:
        if not payload.migration_confirmed:
            raise DomainError('MAP_MIGRATION_REQUIRED', '更换底图前必须确认热点已迁移', 422)
        asset = _check_public_map_asset(db, payload.map_asset_id)
        data['assetId'] = asset.id
        data['mapUrl'] = f'/api/v1/assets/{asset.id}/content'
    elif payload.map_asset_id:
        data['assetId'] = payload.map_asset_id
    if payload.version < current_version:
        raise DomainError('MAP_VERSION_CONFLICT', '地图版本不能倒退', 409, {'currentVersion': current_version})

    existing = {location.id: location for location in db.scalars(select(Location))}
    seen: set[str] = set()
    changed = payload.map_asset_id not in (None, current_asset)
    for hotspot in payload.hotspots:
        if hotspot.id and hotspot.id in seen:
            raise DomainError('MAP_HOTSPOT_DUPLICATE', '楼栋热点重复', 422, {'id': hotspot.id})
        location = existing.get(hotspot.id) if hotspot.id else None
        if location is None:
            location = Location(id=hotspot.id or None, name=hotspot.name, type=hotspot.type,
                                x=hotspot.x / 100, y=hotspot.y / 100,
                                enabled=hotspot.enabled, map_version=payload.version)
            db.add(location)
            db.flush()
        else:
            if (location.name, location.type, round(location.x * 100, 4),
                    round(location.y * 100, 4), location.enabled) != (
                    hotspot.name, hotspot.type, round(hotspot.x, 4),
                    round(hotspot.y, 4), hotspot.enabled):
                changed = True
            location.name = hotspot.name
            location.type = hotspot.type
            location.x = hotspot.x / 100
            location.y = hotspot.y / 100
            location.enabled = hotspot.enabled
            location.map_version = payload.version
            touch(location)
        seen.add(location.id)
    removed = [location for location_id, location in existing.items() if location_id not in seen]
    for location in removed:
        referenced = db.scalar(select(func.count(Order.id)).where(Order.location_id == location.id))
        if referenced:
            raise DomainError('RESOURCE_REFERENCED', '楼栋仍被订单引用，请改为停止配送而不是删除',
                              409, {'locationId': location.id})
        db.delete(location)
    new_version = max(payload.version, current_version + 1 if changed and payload.version <= current_version
                      else payload.version)
    if new_version != payload.version:
        for location in db.scalars(select(Location)):
            if location.id in seen:
                location.map_version = new_version
                touch(location)
    data.update({'mapVersion': new_version, 'publishedAt': utcnow().isoformat(),
                 'migrationConfirmed': payload.migration_confirmed,
                 'hotspotCount': len(seen), 'publishedBy': actor.id})
    row.data = data
    touch(row)
    audit(db, actor, 'MAP_PUBLISHED', 'CONFIG', row.id,
          data={'version': new_version, 'hotspots': len(seen)})
    db.flush()
    return admin_map(db=db, actor=actor)


@router.patch('/admin/maps/{location_id}', response_model=Envelope[LocationView])
def admin_map_update(location_id: str, payload: MapHotspotUpdate, db=Depends(get_db),
                     actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'delivery.manage')
    map_row = get_config(db, 'map')
    location = lock(db, Location, location_id, payload.expected_version)
    if payload.name is not None:
        location.name = payload.name
    if payload.type is not None:
        location.type = payload.type
    geometry_changed = payload.x is not None or payload.y is not None
    if payload.x is not None:
        location.x = payload.x / 100
    if payload.y is not None:
        location.y = payload.y / 100
    if payload.enabled is not None:
        location.enabled = payload.enabled
    map_version = int((map_row.data or {}).get('mapVersion') or location.map_version)
    if geometry_changed:
        map_version += 1
        map_data = deepcopy(map_row.data or {})
        map_data['mapVersion'] = map_version
        map_data['publishedAt'] = utcnow().isoformat()
        map_row.data = map_data
        touch(map_row)
        for other in db.scalars(select(Location)):
            if other.map_version != map_version:
                other.map_version = map_version
                touch(other)
    location.map_version = map_version
    touch(location)
    audit(db, actor, 'MAP_HOTSPOT_UPDATED', 'DELIVERY_LOCATION', location.id,
          reason=payload.reason,
          data={'fields': sorted(payload.model_fields_set - {'expected_version', 'reason'})})
    return ok(_location_view(location, map_version))


@router.delete('/admin/maps/{location_id}', response_model=Envelope[dict])
def admin_map_delete(location_id: str, db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'delivery.manage')
    location = lock(db, Location, location_id)
    referenced = db.scalar(select(func.count(Order.id)).where(Order.location_id == location.id))
    if referenced:
        raise DomainError('RESOURCE_REFERENCED', '楼栋仍被订单引用，请先停止配送', 409)
    db.delete(location)
    audit(db, actor, 'MAP_HOTSPOT_DELETED', 'DELIVERY_LOCATION', location_id)
    return ok({'id': location_id, 'deleted': True})


# ---------------------------------------------------------------------------
# admin slots
# ---------------------------------------------------------------------------


@router.get('/admin/delivery-staff', response_model=Envelope[DeliveryStaffListView])
@router.get('/admin/couriers', response_model=Envelope[DeliveryStaffListView])
def admin_delivery_staff(db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    """Minimal eligible-courier catalog for dispatchers without staff.manage.

    Deliberately excludes the private staff directory (username, contact data,
    password/session metadata and unrelated capability grants).  Only role,
    active state and the delivery-relevant capability are exposed.
    """
    require_admin(actor, 'delivery.manage')
    rows = db.scalars(select(Staff)
                      .where(Staff.enabled.is_(True), Staff.status == 'ACTIVE')
                      .order_by(Staff.name, Staff.id))
    items = []
    for row in rows:
        if _valid_courier(db, row.id) is None:
            continue
        capabilities = [cap for cap in (row.capabilities or [])
                        if cap in ('*', 'delivery.manage')]
        items.append({'id': row.id, 'name': _staff_display_name(db, row), 'role': row.role,
                      'enabled': row.enabled, 'status': row.status,
                      'capabilities': capabilities})
    return ok({'items': items})


@router.get('/admin/slots', response_model=Envelope[SlotListView])
def admin_slots(date: str | None = None, include_archived: bool = False,
                db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'delivery.manage')
    query = select(DeliverySlot)
    if date:
        query = query.where(DeliverySlot.date == date)
    now = utcnow()
    slots = list(db.scalars(query.order_by(DeliverySlot.starts_at, DeliverySlot.id)))
    items = [_slot_view(slot, now) for slot in slots
             if include_archived or not _slot_archived(slot)]
    return ok({'items': items})


def _validate_slot_capacity(slot: DeliverySlot, capacity: int) -> None:
    if capacity < slot.occupied:
        raise DomainError('SLOT_CAPACITY_BELOW_OCCUPIED', '容量不能低于已占用数量', 409,
                          {'occupied': slot.occupied})


@router.post('/admin/slots', response_model=Envelope[SlotView])
def admin_slot_create(payload: SlotInput, db=Depends(get_db),
                      actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'delivery.manage')
    starts_at, ends_at = payload.window()
    slot = DeliverySlot(date=payload.date, name=payload.name, starts_at=starts_at,
                        ends_at=ends_at, capacity=payload.capacity,
                        occupied=0, enabled=payload.enabled,
                        data={'createdBy': actor.id,
                              'history': []})
    db.add(slot)
    db.flush()
    audit(db, actor, 'DELIVERY_SLOT_CREATED', 'DELIVERY_SLOT', slot.id,
          reason=payload.reason or '', data={'date': slot.date, 'capacity': slot.capacity})
    return ok(_slot_view(slot, utcnow()))


@router.patch('/admin/slots/{slot_id}', response_model=Envelope[SlotView])
def admin_slot_update(slot_id: str, payload: SlotUpdate, db=Depends(get_db),
                      actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'delivery.manage')
    slot = lock(db, DeliverySlot, slot_id, payload.expected_version)
    if _slot_archived(slot):
        raise DomainError('SLOT_ARCHIVED', '时段已归档，不能继续编辑', 409,
                          {'slotId': slot.id})
    history = list((slot.data or {}).get('history') or [])
    before = {'date': slot.date, 'name': slot.name, 'start': to_shanghai_hhmm(slot.starts_at),
              'end': to_shanghai_hhmm(slot.ends_at), 'capacity': slot.capacity,
              'enabled': slot.enabled, 'version': slot.version}
    if (payload.date is not None or payload.start is not None or payload.end is not None):
        if payload.date is None or payload.start is None or payload.end is None:
            raise DomainError('SLOT_WINDOW_INCOMPLETE', '调整时段必须同时提供日期、开始与结束时间', 422)
        starts_at, ends_at = payload.window()
        slot.date = payload.date
        slot.starts_at = starts_at
        slot.ends_at = ends_at
    if payload.name is not None:
        slot.name = payload.name
    if payload.capacity is not None:
        _validate_slot_capacity(slot, payload.capacity)
        slot.capacity = payload.capacity
    if payload.enabled is not None:
        slot.enabled = payload.enabled
    changed = before != {'date': slot.date, 'name': slot.name,
                         'start': to_shanghai_hhmm(slot.starts_at),
                         'end': to_shanghai_hhmm(slot.ends_at),
                         'capacity': slot.capacity, 'enabled': slot.enabled,
                         'version': slot.version}
    if changed:
        history.append({'before': before, 'at': utcnow().isoformat(), 'by': actor.id,
                        'reason': payload.reason or ''})
    data = deepcopy(slot.data or {})
    data['history'] = history[-50:]
    slot.data = data
    touch(slot)
    audit(db, actor, 'DELIVERY_SLOT_UPDATED', 'DELIVERY_SLOT', slot.id,
          reason=payload.reason or '', data={'changed': changed})
    return ok(_slot_view(slot, utcnow()))


@router.delete('/admin/slots/{slot_id}', response_model=Envelope[dict])
def admin_slot_delete(slot_id: str, payload: SlotDeleteInput | None = None,
                      db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'delivery.manage')
    slot = lock(db, DeliverySlot, slot_id,
                payload.expected_version if payload else None)
    if _slot_archived(slot):
        return ok({'id': slot_id, 'deleted': True, 'archived': True})
    live_orders = list(db.scalars(select(Order).where(
        (Order.requested_slot_id == slot.id) | (Order.confirmed_slot_id == slot.id),
        Order.deleted_at.is_(None),
        Order.status.notin_(('CANCELLED', 'DELIVERED')))))
    live_batches = [batch for batch in db.scalars(select(DeliveryBatch).where(
        DeliveryBatch.deleted_at.is_(None),
        DeliveryBatch.status.notin_(('CANCELLED', 'COMPLETED'))))
        if _batch_slot_id(batch) == slot.id]
    if live_orders or live_batches:
        raise DomainError('SLOT_IN_USE', '时段仍被进行中订单或批次引用，不能删除',
                          409, {'slotId': slot.id,
                                'orderIds': sorted(order.id for order in live_orders),
                                'batchIds': sorted(batch.id for batch in live_batches)})
    reason = (payload.reason if payload else None) or '管理员移除配送时段'
    data = deepcopy(slot.data or {})
    history = list(data.get('history') or [])
    history.append({'action': 'ARCHIVE', 'before': _slot_snapshot(slot),
                    'at': utcnow().isoformat(), 'by': actor.id, 'reason': reason})
    data.update({'archivedAt': utcnow().isoformat(), 'archivedBy': actor.id,
                 'archiveReason': reason, 'history': history[-50:]})
    slot.data = data
    slot.enabled = False
    touch(slot)
    audit(db, actor, 'DELIVERY_SLOT_ARCHIVED', 'DELIVERY_SLOT', slot.id,
          reason=reason, data={'orderIds': [], 'batchIds': []})
    return ok({'id': slot_id, 'deleted': True, 'archived': True})


# ---------------------------------------------------------------------------
# batch CRUD and lifecycle
# ---------------------------------------------------------------------------


@router.get('/admin/batches', response_model=Envelope[BatchListView])
def admin_batches(status: str | None = None, courier_id: str | None = None,
                  date: str | None = None, db=Depends(get_db),
                  actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'delivery.manage')
    query = select(DeliveryBatch).where(DeliveryBatch.deleted_at.is_(None))
    if status:
        query = query.where(DeliveryBatch.status == status)
    if courier_id:
        query = query.where(DeliveryBatch.courier_id == courier_id)
    batches = list(db.scalars(query.order_by(DeliveryBatch.starts_at, DeliveryBatch.id)))
    if date:
        batches = [batch for batch in batches if to_shanghai_date(batch.starts_at) == date]
    return ok({'items': [_batch_view(db, batch) for batch in batches]})


@router.get('/admin/batches/{batch_id}', response_model=Envelope[BatchView])
def admin_batch(batch_id: str, db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'delivery.manage')
    batch = db.get(DeliveryBatch, batch_id)
    if batch is None or batch.deleted_at is not None:
        raise DomainError('NOT_FOUND', '批次不存在或不可访问', 404)
    return ok(_batch_view(db, batch))


@router.post('/admin/batches', response_model=Envelope[BatchView])
def admin_batch_create(payload: BatchCreate, db=Depends(get_db),
                       actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'delivery.manage')
    slot = None
    if payload.slot_id:
        slot = db.get(DeliverySlot, payload.slot_id, with_for_update=True)
        if slot is None:
            raise DomainError('SLOT_NOT_FOUND', '配送时段不存在', 404,
                              {'slotId': payload.slot_id})
        if _slot_archived(slot):
            raise DomainError('SLOT_UNAVAILABLE', '配送时段已移除，不能创建批次', 409,
                              {'slotId': slot.id})
        if not slot.enabled:
            raise DomainError('SLOT_UNAVAILABLE', '配送时段已关闭，不能创建批次', 409,
                              {'slotId': slot.id})
        starts_at, ends_at = slot.starts_at, slot.ends_at
    else:
        try:
            starts_at, ends_at = payload.window()
        except ValueError as error:
            raise DomainError('INVALID_WINDOW', str(error), 422) from error
    courier_id = payload.courier_id or None
    if courier_id and _valid_courier(db, courier_id) is None:
        raise DomainError('COURIER_INVALID', '配送员不可用', 422)
    data = {'createdBy': actor.id, 'history': []}
    if slot is not None:
        data.update({'slotId': slot.id, 'slotSnapshot': _slot_snapshot(slot)})
    batch = DeliveryBatch(name=payload.name, courier_id=courier_id, starts_at=starts_at,
                          ends_at=ends_at, status='PLANNED', reason=payload.reason,
                          data=data)
    db.add(batch)
    db.flush()
    _audit_batch(db, actor, batch, 'BATCH_CREATED', payload.reason,
                 {'startsAt': starts_at.isoformat(), 'endsAt': ends_at.isoformat(),
                  'slotId': slot.id if slot is not None else None})
    return ok(_batch_view(db, batch))


def _payload_window(payload: dict) -> tuple[datetime, datetime]:
    starts_at = payload.get('startsAt') or payload.get('starts_at')
    ends_at = payload.get('endsAt') or payload.get('ends_at')
    if starts_at or ends_at:
        try:
            return batch_window(None, None, datetime.fromisoformat(starts_at),
                                datetime.fromisoformat(ends_at))
        except (TypeError, ValueError) as error:
            raise DomainError('INVALID_WINDOW', str(error), 422) from error
    try:
        return batch_window(payload.get('date'), payload.get('time'))
    except ValueError as error:
        raise DomainError('INVALID_WINDOW', str(error), 422) from error


@router.patch('/admin/batches/{batch_id}', response_model=Envelope[BatchView])
def admin_batch_update(batch_id: str, payload: BatchUpdate, db=Depends(get_db),
                       actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'delivery.manage')
    batch, _orders, assignments = _lock_batch_context(
        db, batch_id, payload.expected_version, active_only=True)
    if batch.status in ('DELIVERING', 'COMPLETED', 'CANCELLED'):
        raise DomainError('INVALID_TRANSITION', '当前批次状态不能编辑', 409, {'status': batch.status})
    before = _batch_view(db, batch)
    if payload.name is not None:
        batch.name = payload.name
    window_fields = {'date', 'time', 'starts_at', 'ends_at'} & payload.model_fields_set
    if window_fields:
        if {'starts_at', 'ends_at'} & window_fields:
            starts_at, ends_at = batch_window(None, None, payload.starts_at, payload.ends_at)
        else:
            starts_at, ends_at = batch_window(payload.date, payload.time)
        batch.starts_at, batch.ends_at = starts_at, ends_at
        if batch.status == 'PUBLISHED':
            batch.status = 'PLANNED'
    if 'courier_id' in payload.model_fields_set:
        if payload.courier_id and _valid_courier(db, payload.courier_id) is None:
            raise DomainError('COURIER_INVALID', '配送员不可用', 422)
        batch.courier_id = payload.courier_id or None
        for assignment in assignments:
            if assignment.status == 'DELIVERED':
                continue
            assignment.courier_id = batch.courier_id
            assignment.data = {**(assignment.data or {}),
                               'reassignedAt': utcnow().isoformat(),
                               'reassignedBy': actor.id,
                               'reassignmentReason': payload.reason}
            touch(assignment)
    batch.reason = payload.reason
    data = deepcopy(batch.data or {})
    if window_fields and data.get('slotId'):
        data['detachedSlotId'] = data.pop('slotId')
        data.pop('slotSnapshot', None)
        data['slotDetachedAt'] = utcnow().isoformat()
    history = list(data.get('history') or [])
    history.append({'action': 'EDIT', 'before': before, 'at': utcnow().isoformat(),
                    'by': actor.id, 'reason': payload.reason})
    data['history'] = history[-50:]
    batch.data = data
    touch(batch)
    _audit_batch(db, actor, batch, 'BATCH_UPDATED', payload.reason,
                 {'fields': sorted(payload.model_fields_set - {'expected_version', 'reason'})})
    return ok(_batch_view(db, batch))


def _release_assignments(db, batch: DeliveryBatch, actor: Actor, reason: str,
                         status: str = 'CANCELLED') -> None:
    for assignment in _active_batch_assignments(db, batch.id):
        if assignment.status == 'DELIVERED':
            continue
        assignment.active = False
        assignment.status = status
        assignment.data = {**(assignment.data or {}),
                           'releasedAt': utcnow().isoformat(),
                           'releasedBy': actor.id, 'releaseReason': reason}
        touch(assignment)
        order = db.get(Order, assignment.order_id)
        if order is not None and order.status in ('READY_FOR_DELIVERY', 'DELIVERY_EXCEPTION'):
            order.status = 'READY_FOR_DELIVERY'
            order.data = {**(order.data or {}),
                          'deliveryReleasedAt': utcnow().isoformat(),
                          'deliveryReleaseReason': reason,
                          'lastBatchId': batch.id}
            touch(order)
    db.flush()


def _batch_delete_or_cancel(db, actor: Actor, batch: DeliveryBatch, reason: str,
                            *, delete: bool) -> None:
    if batch.status in ('DELIVERING', 'COMPLETED'):
        raise DomainError('INVALID_TRANSITION', '配送中或已完成批次不能取消或删除', 409,
                          {'status': batch.status})
    assignments = _active_batch_assignments(db, batch.id)
    if any(assignment.status in ('STARTED', 'DELIVERED') for assignment in assignments):
        raise DomainError('BATCH_HAS_STARTED_EXECUTIONS',
                          '请先处理已开始的配送执行和已送达凭证', 409)
    snapshot = _batch_view(db, batch)
    _release_assignments(db, batch, actor, reason)
    history = list((batch.data or {}).get('history') or [])
    history.append({'action': 'DELETE' if delete else 'CANCEL', 'before': snapshot,
                    'at': utcnow().isoformat(), 'by': actor.id, 'reason': reason})
    batch.status = 'CANCELLED'
    batch.reason = reason
    batch.data = {**(batch.data or {}), 'history': history[-50:],
                  'snapshot': snapshot, 'deletedBy': actor.id}
    if delete:
        batch.deleted_at = utcnow()
    touch(batch)
    db.flush()
    _audit_batch(db, actor, batch, 'BATCH_DELETED' if delete else 'BATCH_CANCELLED', reason,
                 {'snapshotId': batch.id})


def _batch_to_trash(db, actor: Actor, batch: DeliveryBatch, reason: str):
    """Delete a batch through the recoverable trash instead of hiding it silently."""
    if batch.deleted_at is not None:
        raise DomainError('INVALID_TRANSITION', '批次已删除', 409, {'status': batch.status})
    if batch.status == 'DELIVERING':
        raise DomainError('INVALID_TRANSITION', '配送中批次不能删除，请先处理实际配送执行', 409,
                          {'status': batch.status})
    assignments = _active_batch_assignments(db, batch.id)
    if any(assignment.status == 'STARTED' for assignment in assignments):
        raise DomainError('BATCH_HAS_STARTED_EXECUTIONS',
                          '请先处理已开始的配送执行和已送达凭证', 409)
    from .maintenance import move_to_trash
    snapshot = _batch_view(db, batch)
    entry = move_to_trash(db, actor, 'BATCH', batch, reason,
                          {'statusBefore': batch.status})
    history = list((batch.data or {}).get('history') or [])
    history.append({'action': 'DELETE', 'before': snapshot,
                    'at': utcnow().isoformat(), 'by': actor.id, 'reason': reason})
    batch.status = 'CANCELLED'
    batch.reason = reason
    batch.data = {**(batch.data or {}), 'history': history[-50:], 'snapshot': snapshot,
                  'deletedBy': actor.id, 'trashId': entry.id}
    db.flush()
    _audit_batch(db, actor, batch, 'BATCH_DELETED', reason, {'snapshotId': batch.id})
    return entry


@router.delete('/admin/batches/{batch_id}', response_model=Envelope[BatchView])
def admin_batch_delete(batch_id: str, payload: BatchVersionInput | None = None,
                       db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'delivery.manage')
    batch, _orders, _assignments = _lock_batch_context(
        db, batch_id, payload.expected_version if payload else None, active_only=True)
    reason = (payload.reason if payload else None) or '管理员删除配送批次'
    _batch_to_trash(db, actor, batch, reason)
    return ok(_batch_view(db, batch))


@router.post('/admin/batches/{batch_id}/publish', response_model=Envelope[BatchView])
def admin_batch_publish(batch_id: str, payload: BatchVersionInput, db=Depends(get_db),
                        actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'delivery.manage')
    batch = lock(db, DeliveryBatch, batch_id, payload.expected_version)
    if batch.deleted_at or batch.status not in ('PLANNED', 'POSTPONED'):
        raise DomainError('INVALID_TRANSITION', '只有规划中或已延期的批次可以发布', 409,
                          {'status': batch.status})
    if _valid_courier(db, batch.courier_id) is None:
        raise DomainError('COURIER_REQUIRED', '发布前必须分配有效配送员', 422)
    batch.status = 'PUBLISHED'
    data = deepcopy(batch.data or {})
    data['publishedAt'] = utcnow().isoformat()
    data['publishedBy'] = actor.id
    if payload.reason:
        data['publishReason'] = payload.reason
    batch.data = data
    batch.reason = payload.reason or batch.reason
    touch(batch)
    _audit_batch(db, actor, batch, 'BATCH_PUBLISHED', payload.reason or '')
    return ok(_batch_view(db, batch))


def _lock_batches(db, ids) -> dict[str, DeliveryBatch]:
    result = {}
    for batch_id in sorted(set(ids)):
        result[batch_id] = lock(db, DeliveryBatch, batch_id)
    return result


def _lock_orders(db, ids) -> dict[str, Order]:
    result = {}
    for order_id in sorted(set(ids)):
        result[order_id] = lock(db, Order, order_id)
    return result


def _lock_assignments(db, order_ids) -> list[DeliveryAssignment]:
    if not order_ids:
        return []
    return list(db.scalars(select(DeliveryAssignment)
                           .where(DeliveryAssignment.order_id.in_(sorted(set(order_ids))))
                           .order_by(DeliveryAssignment.id)
                           .with_for_update()))


def _lock_batch_context(db, batch_id: str, expected_version: int | None = None,
                        *, active_only: bool = True):
    """Lock a batch's work in the global order: orders, assignments, then batch.

    Batch metadata is read unlocked first only to discover the order ids.  A
    concurrent membership change is detected after the batch lock and aborted,
    so callers never write a stale projection.  The batch row must be last:
    after_sales holds the order/assignment locks before refreshing its batch,
    and taking the batch first here would deadlock against that path.
    """
    batch = db.get(DeliveryBatch, batch_id)
    if batch is None or batch.deleted_at:
        raise DomainError('NOT_FOUND', '批次不存在或不可访问', 404)
    statement = select(DeliveryAssignment).where(
        DeliveryAssignment.batch_id == batch_id)
    if active_only:
        statement = statement.where(DeliveryAssignment.active.is_(True))
    statement = statement.order_by(DeliveryAssignment.id)
    initial = list(db.scalars(statement))
    order_ids = sorted({assignment.order_id for assignment in initial})
    orders = _lock_orders(db, order_ids)
    _lock_assignments(db, order_ids)
    locked_batch = lock(db, DeliveryBatch, batch_id, expected_version)
    current = list(db.scalars(statement))
    if [assignment.id for assignment in current] != [assignment.id for assignment in initial]:
        raise DomainError('BATCH_CHANGED', '批次内容已变化，请刷新后重试', 409)
    return locked_batch, orders, current


@router.post('/admin/batches/{batch_id}/start', response_model=Envelope[BatchOperationResultView])
@router.post('/courier/batches/{batch_id}/start', response_model=Envelope[BatchOperationResultView])
def batch_start(batch_id: str, payload: BatchVersionInput, db=Depends(get_db),
                actor: Actor = Depends(actor_dep)):
    require_staff(actor)
    if _is_admin(actor):
        # The shared admin/courier start endpoint used to let any ADMIN start
        # any batch through the _is_admin bypass.  Scheduling/starting is a
        # delivery.manage action; assigned COURIERs keep their own path below.
        require_admin(actor, 'delivery.manage')
    batch, orders, assignments = _lock_batch_context(
        db, batch_id, payload.expected_version, active_only=True)
    if not _is_admin(actor) and (
            batch.courier_id != actor.staff_id
            or _valid_courier(db, actor.staff_id) is None):
        raise DomainError('FORBIDDEN', '只能开始分配给自己的配送批次', 403)
    for order in orders.values():
        _refresh_production_safely(db, order)
    blockers = _start_blockers(db, batch, assignments, orders)
    if blockers:
        raise DomainError('BATCH_NOT_READY', '批次仍有未满足条件的订单，整批未开始配送',
                          409, {'blockers': blockers})
    now = utcnow()
    for assignment in assignments:
        assignment.status = 'STARTED'
        assignment.data = {**(assignment.data or {}),
                           'startedAt': now.isoformat(), 'startedBy': actor.id}
        touch(assignment)
    for order in orders.values():
        order.status = 'DELIVERING'
        order.data = {**(order.data or {}), 'delivery': {
            'batchId': batch.id, 'assignmentId': next(
                assignment.id for assignment in assignments if assignment.order_id == order.id),
            'courierId': batch.courier_id, 'startedAt': now.isoformat(),
            'window': {'startsAt': batch.starts_at.isoformat(), 'endsAt': batch.ends_at.isoformat()},
        }}
        touch(order)
        audit(db, actor, 'DELIVERY_STARTED', 'ORDER', order.id,
              data={'batchId': batch.id, 'courierId': batch.courier_id})
    batch.status = 'DELIVERING'
    batch.data = {**(batch.data or {}), 'startedAt': now.isoformat(), 'startedBy': actor.id}
    touch(batch)
    _audit_batch(db, actor, batch, 'BATCH_STARTED', payload.reason or '',
                 {'assignmentIds': [assignment.id for assignment in assignments]})
    db.flush()
    # One status notification per order for this batch transition.  The
    # versioned business key deduplicates an idempotent replay.
    from .growth import notify_order_event
    for order in orders.values():
        notify_order_event(db, order, 'STATUS_UPDATED')
    return ok({'action': 'START', 'batches': [_batch_view(db, batch)],
               'orders': [_delivery_order_view(db, order, actor) for order in orders.values()]})


# ---------------------------------------------------------------------------
# batch operations: preview -> commit
# ---------------------------------------------------------------------------


def _operation_order_ids(db, payload: BatchOperationPreview) -> list[str]:
    ids = set(payload.order_ids)
    if payload.action in ('REMOVE', 'TRANSFER', 'SPLIT', 'RESCHEDULE', 'CANCEL', 'DELETE',
                          'MERGE', 'PUBLISH', 'START') and not ids:
        for batch_id in payload.source_batch_ids:
            ids.update(assignment.order_id for assignment in _active_batch_assignments(db, batch_id))
    return sorted(ids)


def _source_assignments(db, source_ids) -> list[DeliveryAssignment]:
    if not source_ids:
        return []
    return list(db.scalars(select(DeliveryAssignment)
                           .where(DeliveryAssignment.batch_id.in_(sorted(set(source_ids))),
                                  DeliveryAssignment.active.is_(True))
                           .order_by(DeliveryAssignment.id)))


def _slot_mismatch_blocker(target: DeliveryBatch, order: Order) -> dict | None:
    slot_id = _batch_slot_id(target)
    order_slot_id = _order_effective_slot_id(order)
    if slot_id and order_slot_id and slot_id != order_slot_id:
        return _blocker('ORDER_SLOT_MISMATCH',
                        '订单所选时段与批次时段不一致，请先由客户确认新时段', order.id)
    return None


def _preview_operation(db, actor: Actor, payload: BatchOperationPreview) -> dict:
    blockers: list[dict] = []
    impacts: list[dict] = []
    source_batches = []
    for batch_id in sorted(set(payload.source_batch_ids)):
        batch = db.get(DeliveryBatch, batch_id)
        if batch is None or batch.deleted_at:
            raise DomainError('NOT_FOUND', '来源批次不存在或不可访问', 404, {'batchId': batch_id})
        if batch_id not in payload.versions:
            blockers.append(_blocker('VERSION_REQUIRED', '缺少来源批次版本', batch_id))
        elif payload.versions[batch_id] != batch.version:
            blockers.append(_blocker('VERSION_CONFLICT', '批次数据已变化，请刷新后重试', batch_id))
        source_batches.append(batch)
    target_batch = None
    if payload.target_batch_id:
        target_batch = db.get(DeliveryBatch, payload.target_batch_id)
        if target_batch is None or target_batch.deleted_at:
            raise DomainError('NOT_FOUND', '目标批次不存在或不可访问', 404)
        if (payload.target_batch_id in payload.versions
                and payload.versions[payload.target_batch_id] != target_batch.version):
            blockers.append(_blocker('VERSION_CONFLICT', '目标批次数据已变化', target_batch.id))
    order_ids = _operation_order_ids(db, payload)
    orders = {}
    for order_id in order_ids:
        order = db.get(Order, order_id)
        if order is None or order.deleted_at:
            blockers.append(_blocker('ORDER_NOT_FOUND', '订单不存在或已删除', order_id))
        else:
            orders[order_id] = order

    action = payload.action
    if action in ('ADD',):
        if not target_batch and len(source_batches) == 1:
            target_batch = source_batches[0]
        if target_batch is None:
            blockers.append(_blocker('TARGET_BATCH_REQUIRED', '请选择目标批次'))
        elif target_batch.status in ('DELIVERING', 'COMPLETED', 'CANCELLED'):
            blockers.append(_blocker('BATCH_NOT_EDITABLE', '目标批次不能增加订单', target_batch.id))
        else:
            for order in orders.values():
                if order.status in ('CANCELLED', 'DELIVERED', 'DELIVERING'):
                    blockers.append(_blocker('ORDER_NOT_AVAILABLE', '订单状态不允许加入批次', order.id))
                existing = _active_assignment(db, order.id)
                if existing is not None:
                    blockers.append(_blocker('ORDER_ALREADY_ASSIGNED',
                                             '订单已有有效配送执行', order.id))
                else:
                    mismatch = _slot_mismatch_blocker(target_batch, order)
                    if mismatch is not None:
                        blockers.append(mismatch)
                    impacts.append({'batchId': target_batch.id, 'orderId': order.id,
                                    'pickupCode': order.pickup_code, 'message': '将加入批次'})
    elif action in ('REMOVE', 'TRANSFER', 'SPLIT', 'MERGE'):
        source_assignments = _source_assignments(db, payload.source_batch_ids)
        assignment_by_order = {assignment.order_id: assignment for assignment in source_assignments}
        if not payload.source_batch_ids:
            blockers.append(_blocker('SOURCE_BATCH_REQUIRED', '请选择来源批次'))
        selected = order_ids if action != 'MERGE' else list(assignment_by_order)
        if not selected:
            blockers.append(_blocker('ORDER_REQUIRED', '请选择订单'))
        if action in ('TRANSFER', 'MERGE'):
            if target_batch is None:
                blockers.append(_blocker('TARGET_BATCH_REQUIRED', '请选择目标批次'))
            elif target_batch.status in ('DELIVERING', 'COMPLETED', 'CANCELLED'):
                blockers.append(_blocker('BATCH_NOT_EDITABLE', '目标批次不能接收订单', target_batch.id))
            elif _valid_courier(db, target_batch.courier_id) is None:
                blockers.append(_blocker('COURIER_REQUIRED', '目标批次必须分配有效配送员', target_batch.id))
            if target_batch and target_batch.id in payload.source_batch_ids:
                blockers.append(_blocker('INVALID_TARGET', '目标批次不能与来源批次相同', target_batch.id))
        for order_id in selected:
            assignment = assignment_by_order.get(order_id)
            if assignment is None:
                blockers.append(_blocker('ORDER_NOT_IN_SOURCE', '订单不在来源批次', order_id))
                continue
            if assignment.status in ('STARTED', 'DELIVERED'):
                blockers.append(_blocker('ASSIGNMENT_STARTED', '已经开始配送的执行不能转移', assignment.id))
            order = orders.get(order_id)
            if order is not None:
                if target_batch is not None and action in ('TRANSFER', 'MERGE'):
                    mismatch = _slot_mismatch_blocker(target_batch, order)
                    if mismatch is not None:
                        blockers.append(mismatch)
                impacts.append({'batchId': target_batch.id if target_batch else None,
                                'orderId': order.id, 'pickupCode': order.pickup_code,
                                'message': {'REMOVE': '将移出批次', 'TRANSFER': '将转移到目标批次',
                                            'SPLIT': '将拆分为新批次', 'MERGE': '将合并到目标批次'}[action]})
    elif action == 'RESCHEDULE':
        try:
            starts_at, ends_at = _payload_window(payload.payload)
        except DomainError as error:
            blockers.append(_blocker('INVALID_WINDOW', error.message))
        else:
            for batch in source_batches:
                if batch.status in ('DELIVERING', 'COMPLETED', 'CANCELLED'):
                    blockers.append(_blocker('BATCH_NOT_EDITABLE', '当前批次不能改期', batch.id))
                else:
                    impacts.append({'batchId': batch.id,
                                    'message': f'将改期为 {to_shanghai_date(starts_at)} '
                                               f'{to_shanghai_window(starts_at, ends_at)}'})
    elif action in ('CANCEL', 'DELETE'):
        if not payload.payload.get('reason'):
            blockers.append(_blocker('REASON_REQUIRED', '取消或删除批次必须填写原因'))
        for batch in source_batches:
            if batch.status == 'DELIVERING':
                blockers.append(_blocker('BATCH_NOT_EDITABLE', '配送中批次不能取消或删除',
                                         batch.id))
            elif action == 'CANCEL' and batch.status == 'COMPLETED':
                blockers.append(_blocker('BATCH_NOT_EDITABLE', '已完成批次不能取消，只能删除',
                                         batch.id))
            for assignment in _source_assignments(db, [batch.id]):
                if assignment.status == 'STARTED':
                    blockers.append(_blocker('BATCH_HAS_STARTED_EXECUTIONS',
                                             '存在已开始的配送执行', assignment.id))
                elif action == 'CANCEL' and assignment.status == 'DELIVERED':
                    blockers.append(_blocker('BATCH_HAS_DELIVERED',
                                             '已完成配送的批次不能取消，只能删除',
                                             assignment.id))
            impacts.append({'batchId': batch.id,
                            'message': '将删除批次并移入回收站，24 小时内可恢复'
                                       if action == 'DELETE'
                                       else '将取消批次并释放未送达订单'})
    elif action == 'PUBLISH':
        for batch in source_batches:
            if batch.status not in ('PLANNED', 'POSTPONED'):
                blockers.append(_blocker('INVALID_TRANSITION', '只有规划中或已延期批次可以发布', batch.id))
            if _valid_courier(db, batch.courier_id) is None:
                blockers.append(_blocker('COURIER_REQUIRED', '发布前必须分配有效配送员', batch.id))
            impacts.append({'batchId': batch.id, 'message': '将发布配送计划，不标记订单配送中'})
    elif action == 'START':
        if not source_batches:
            blockers.append(_blocker('SOURCE_BATCH_REQUIRED', '请选择批次'))
        for batch in source_batches:
            assignments = _active_batch_assignments(db, batch.id)
            batch_orders = {assignment.order_id: orders.get(assignment.order_id)
                            for assignment in assignments}
            blockers.extend(_start_blockers(db, batch, assignments,
                                            {key: value for key, value in batch_orders.items()
                                             if value is not None}))
            impacts.append({'batchId': batch.id, 'message': '将整批开始配送'})
    else:
        blockers.append(_blocker('UNKNOWN_ACTION', '不支持的批量操作'))
    for batch in source_batches:
        if batch.status == 'DELIVERING' and action in ('ADD', 'REMOVE', 'TRANSFER', 'SPLIT', 'MERGE'):
            blockers.append(_blocker('BATCH_DELIVERING', '配送中批次不能调整订单', batch.id))
    return {'blockers': blockers, 'impacts': impacts}


def _store_plan(db, actor: Actor, payload: BatchOperationPreview, preview: dict) -> BatchPlan:
    plan = BatchPlan(staff_id=actor.staff_id, expires_at=utcnow() + PLAN_TTL, data={
        'action': payload.action,
        'orderIds': payload.order_ids,
        'sourceBatchIds': payload.source_batch_ids,
        'targetBatchId': payload.target_batch_id,
        'versions': payload.versions,
        'payload': payload.payload,
        'blockers': preview['blockers'],
        'impacts': preview['impacts'],
    })
    db.add(plan)
    db.flush()
    return plan


@router.post('/admin/batch-operations/preview', response_model=Envelope[BatchPlanView])
def batch_operation_preview(payload: BatchOperationPreview, db=Depends(get_db),
                            actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'delivery.manage')
    preview = _preview_operation(db, actor, payload)
    plan = _store_plan(db, actor, payload, preview)
    return ok({'planId': plan.id, 'action': payload.action,
               'blockers': preview['blockers'], 'impacts': preview['impacts'],
               'expiresAt': plan.expires_at})


def _assign_order(db, batch: DeliveryBatch, order: Order, actor: Actor,
                  reason: str) -> DeliveryAssignment:
    existing = db.scalar(select(DeliveryAssignment)
                         .where(DeliveryAssignment.order_id == order.id,
                                DeliveryAssignment.active.is_(True))
                         .with_for_update())
    if existing is not None:
        if existing.batch_id == batch.id:
            return existing
        raise DomainError('BATCH_CONFLICT', '订单已有其他有效配送批次', 409,
                          {'orderId': order.id, 'batchId': existing.batch_id})
    mismatch = _slot_mismatch_blocker(batch, order)
    if mismatch is not None:
        raise DomainError('ORDER_SLOT_MISMATCH', mismatch['message'], 409,
                          {'orderId': order.id, 'slotId': _batch_slot_id(batch)})
    assignment = DeliveryAssignment(
        order_id=order.id, batch_id=batch.id, courier_id=batch.courier_id,
        status='PLANNED', active=True, address_review=bool(order.address_review),
        data={'assignedAt': utcnow().isoformat(), 'assignedBy': actor.id,
              'reason': reason, 'locationSnapshot': _location_snapshot(db, order)})
    db.add(assignment)
    try:
        db.flush()
    except IntegrityError as error:
        raise DomainError('BATCH_CONFLICT', '订单已有其他有效配送批次', 409,
                          {'orderId': order.id}) from error
    # A merchant-rework order may still point at an old DELIVERING batch whose
    # only executed assignment was deactivated by after_sales.  Creating the new
    # assignment is the point where that old projection can be closed, so the
    # historical batch never remains DELIVERING forever.
    previous_batch_ids = sorted(set(db.scalars(
        select(DeliveryAssignment.batch_id).where(DeliveryAssignment.order_id == order.id))))
    for previous_batch_id in previous_batch_ids:
        if previous_batch_id != batch.id:
            refresh_batch(db, previous_batch_id)
    return assignment


def _deactivate_assignment(db, assignment: DeliveryAssignment, actor: Actor, reason: str,
                           status='CANCELLED') -> None:
    assignment.active = False
    assignment.status = status
    assignment.data = {**(assignment.data or {}), 'releasedAt': utcnow().isoformat(),
                       'releasedBy': actor.id, 'releaseReason': reason}
    touch(assignment)
    db.flush()


def _execute_operation(db, actor: Actor, action: str, payload: BatchOperationPreview,
                       source_batches: dict[str, DeliveryBatch],
                       target_batch: DeliveryBatch | None,
                       orders: dict[str, Order], reason: str) -> list[DeliveryBatch]:
    affected: list[DeliveryBatch] = []
    if action == 'ADD':
        batch = target_batch or next(iter(source_batches.values()))
        for order in orders.values():
            _assign_order(db, batch, order, actor, reason)
        affected.append(batch)
    elif action in ('REMOVE', 'TRANSFER', 'SPLIT'):
        targets = [target_batch] if target_batch else []
        source_assignments = _source_assignments(db, list(source_batches))
        by_order = {assignment.order_id: assignment for assignment in source_assignments}
        if action == 'SPLIT':
            source = next(iter(source_batches.values()))
            source_data = source.data or {}
            split_data = {'splitFrom': source.id, 'createdBy': actor.id}
            if source_data.get('slotId'):
                split_data.update({'slotId': source_data['slotId'],
                                   'slotSnapshot': source_data.get('slotSnapshot')})
            split = DeliveryBatch(name=f'{source.name} · 拆分', courier_id=source.courier_id,
                                  starts_at=source.starts_at, ends_at=source.ends_at,
                                  status='PLANNED', reason=reason, data=split_data)
            db.add(split)
            db.flush()
            target_batch = split
            targets = [split]
            affected.append(split)
        for order_id in sorted(orders):
            assignment = by_order.get(order_id)
            if assignment is None:
                continue
            _deactivate_assignment(db, assignment, actor, reason)
            if action != 'REMOVE':
                _assign_order(db, target_batch, orders[order_id], actor, reason)
        affected.extend(source_batches.values())
    elif action == 'MERGE':
        if target_batch is None:
            raise DomainError('TARGET_BATCH_REQUIRED', '请选择目标批次', 422)
        for assignment in _source_assignments(db, list(source_batches)):
            if assignment.status in ('DELIVERED', 'STARTED'):
                continue
            order = db.get(Order, assignment.order_id)
            _deactivate_assignment(db, assignment, actor, reason)
            if order is not None:
                _assign_order(db, target_batch, order, actor, reason)
        for source in source_batches.values():
            if source.id == target_batch.id:
                continue
            source.status = 'CANCELLED'
            source.reason = reason
            source.data = {**(source.data or {}), 'mergedInto': target_batch.id,
                           'mergedAt': utcnow().isoformat(), 'mergedBy': actor.id}
            touch(source)
        affected.append(target_batch)
        affected.extend(batch for batch in source_batches.values() if batch.id != target_batch.id)
    elif action == 'RESCHEDULE':
        starts_at, ends_at = _payload_window(payload.payload)
        for batch in source_batches.values():
            before = _batch_view(db, batch)
            batch.starts_at, batch.ends_at = starts_at, ends_at
            batch.status = 'POSTPONED'
            batch.reason = reason
            data = deepcopy(batch.data or {})
            if data.get('slotId'):
                data['detachedSlotId'] = data.pop('slotId')
                data.pop('slotSnapshot', None)
                data['slotDetachedAt'] = utcnow().isoformat()
            history = list(data.get('history') or [])
            history.append({'action': 'RESCHEDULE', 'before': before,
                            'at': utcnow().isoformat(), 'by': actor.id, 'reason': reason})
            data['history'] = history[-50:]
            batch.data = data
            touch(batch)
            affected.append(batch)
    elif action in ('CANCEL', 'DELETE'):
        for batch in source_batches.values():
            if action == 'DELETE':
                _batch_to_trash(db, actor, batch, reason)
            else:
                _batch_delete_or_cancel(db, actor, batch, reason, delete=False)
            affected.append(batch)
    return affected


@router.post('/admin/batch-operations/commit', response_model=Envelope[BatchOperationResultView])
def batch_operation_commit(payload: BatchOperationCommit, db=Depends(get_db),
                           actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'delivery.manage')
    plan = lock(db, BatchPlan, payload.plan_id)
    if plan.committed_at is not None:
        raise DomainError('PLAN_ALREADY_COMMITTED', '该操作计划已执行', 409)
    if plan.expires_at <= utcnow():
        raise DomainError('PLAN_EXPIRED', '操作计划已过期，请重新预览', 409)
    if plan.staff_id != actor.staff_id:
        raise DomainError('NOT_FOUND', '操作计划不存在或不可访问', 404)
    data = plan.data or {}
    operation = BatchOperationPreview(**{
        'action': data.get('action'), 'orderIds': data.get('orderIds') or [],
        'sourceBatchIds': data.get('sourceBatchIds') or [],
        'targetBatchId': data.get('targetBatchId'),
        'versions': data.get('versions') or {}, 'payload': data.get('payload') or {},
    })
    if operation.action in ('PUBLISH', 'START'):
        raise DomainError('USE_DEDICATED_ENDPOINT', '发布和开始配送使用专用接口', 422)
    if not operation.source_batch_ids:
        raise DomainError('SOURCE_BATCH_REQUIRED', '来源批次缺失', 422)
    source_ids = list(operation.source_batch_ids)
    target_id = operation.target_batch_id
    all_batch_ids = source_ids + ([target_id] if target_id else [])
    order_ids = _operation_order_ids(db, operation)
    locked_orders = _lock_orders(db, order_ids)
    locked_assignments = _lock_assignments(db, order_ids)
    # Re-check discovered membership before taking the batch rows.  The plan
    # may have been previewed against a stale assignment set; abort rather than
    # execute a partial operation on an order we do not hold.
    current_order_ids = set(_operation_order_ids(db, operation))
    current_assignment_ids = set(db.scalars(
        select(DeliveryAssignment.id).where(
            DeliveryAssignment.order_id.in_(sorted(current_order_ids)))))
    if (not current_order_ids.issubset(locked_orders)
            or current_assignment_ids != {assignment.id for assignment in locked_assignments}):
        raise DomainError('BATCH_CHANGED', '批次内容已变化，请重新预览', 409)
    batches = _lock_batches(db, all_batch_ids)
    preview = _preview_operation(db, actor, operation)
    if preview['blockers']:
        raise DomainError('BATCH_OPERATION_BLOCKED', '批次操作存在阻断项，未执行',
                          409, {'blockers': preview['blockers']})
    source_batches = {batch_id: batches[batch_id] for batch_id in source_ids}
    target_batch = batches.get(target_id) if target_id else None
    reason = (operation.payload or {}).get('reason') or operation.action
    affected = _execute_operation(db, actor, operation.action, operation, source_batches,
                                  target_batch, locked_orders, reason)
    plan.committed_at = utcnow()
    plan.data = {**data, 'committedBy': actor.id,
                 'committedAt': plan.committed_at.isoformat()}
    touch(plan)
    audit(db, actor, 'BATCH_OPERATION_COMMITTED', 'BATCH_PLAN', plan.id,
          reason=reason, data={'action': operation.action,
                               'batchIds': sorted(batches)})
    unique: list[DeliveryBatch] = []
    seen = set()
    for batch in affected:
        if batch.id not in seen:
            seen.add(batch.id)
            unique.append(batch)
    result_orders = [_delivery_order_view(db, order, actor)
                     for order in sorted(locked_orders.values(), key=lambda item: item.id)]
    return ok({'action': operation.action, 'planId': plan.id,
               'batches': [_batch_view(db, batch) for batch in unique],
               'orders': result_orders})


# ---------------------------------------------------------------------------
# address review (commerce location change marks the current assignment)
# ---------------------------------------------------------------------------


def mark_address_review(db, order: Order, *, reason: str = '') -> DeliveryAssignment | None:
    """Mark the current assignment/order for manual address review.

    Called by commerce inside its own locked location-change transaction.  This
    function never commits and preserves the immutable location snapshot.
    """
    if order.status in ('DELIVERING', 'DELIVERED', 'CANCELLED') or order.deleted_at:
        raise DomainError('INVALID_TRANSITION', '配送开始后不能修改楼栋', 409,
                          {'status': order.status})
    assignment = db.scalar(select(DeliveryAssignment)
                           .where(DeliveryAssignment.order_id == order.id,
                                  DeliveryAssignment.active.is_(True))
                           .with_for_update())
    snapshot = _location_snapshot(db, order)
    if assignment is not None:
        assignment.address_review = True
        assignment.data = {**(assignment.data or {}),
                           'addressReviewRequiredAt': utcnow().isoformat(),
                           'addressReviewReason': reason,
                           'locationSnapshot': snapshot}
        touch(assignment)
    order.address_review = True
    order.data = {**(order.data or {}), 'locationSnapshot': snapshot}
    touch(order)
    return assignment


@router.post('/admin/assignments/{assignment_id}/address-review',
             response_model=Envelope[DeliveryOrderView], response_model_exclude_none=True)
def admin_address_review(assignment_id: str, payload: AddressReviewInput,
                         db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'delivery.manage')
    assignment = db.get(DeliveryAssignment, assignment_id)
    if assignment is None:
        raise DomainError('NOT_FOUND', '配送执行不存在或不可访问', 404)
    order = lock(db, Order, assignment.order_id)
    assignment = lock(db, DeliveryAssignment, assignment_id, payload.expected_version)
    if not assignment.active:
        raise DomainError('ASSIGNMENT_INACTIVE', '配送执行已失效', 409)
    if order.status in ('DELIVERING', 'DELIVERED'):
        raise DomainError('INVALID_TRANSITION', '配送已开始，不能再复核地址', 409)
    if assignment.address_review is False and order.address_review is False:
        raise DomainError('NO_ADDRESS_REVIEW', '当前没有待复核的地址修改', 409)
    now = utcnow().isoformat()
    history = list((assignment.data or {}).get('addressReviewHistory') or [])
    history.append({'decision': payload.decision, 'at': now, 'by': actor.id,
                    'reason': payload.reason})
    if payload.decision == 'APPROVED':
        assignment.address_review = False
        order.address_review = False
        snapshot = _location_snapshot(db, order)
        assignment.data = {**(assignment.data or {}), 'locationSnapshot': snapshot}
        order.data = {**(order.data or {}), 'locationSnapshot': snapshot,
                      'locationReviewedAt': now, 'locationReviewedBy': actor.id}
        touch(order)
    assignment.data = {**(assignment.data or {}), 'addressReviewHistory': history[-30:]}
    touch(assignment)
    audit(db, actor, 'ADDRESS_REVIEWED', 'DELIVERY_ASSIGNMENT', assignment.id,
          reason=payload.reason, data={'decision': payload.decision, 'orderId': order.id})
    return ok(_delivery_order_view(db, order, actor))


# ---------------------------------------------------------------------------
# courier list / detail / proof / exceptions
# ---------------------------------------------------------------------------


def _courier_batches(db, actor: Actor) -> list[DeliveryBatch]:
    query = select(DeliveryBatch).where(DeliveryBatch.deleted_at.is_(None))
    if not _is_admin(actor):
        query = query.where(DeliveryBatch.courier_id == actor.staff_id)
    return list(db.scalars(query.order_by(DeliveryBatch.starts_at, DeliveryBatch.id)))


@router.get('/courier/batches', response_model=Envelope[BatchListView])
def courier_batches(db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    _require_delivery_actor(actor)
    return ok({'items': [_batch_view(db, batch) for batch in _courier_batches(db, actor)]})


@router.get('/courier/batches/{batch_id}', response_model=Envelope[CourierBatchDetailView],
             response_model_exclude_none=True)
def courier_batch(batch_id: str, db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    _require_delivery_actor(actor)
    batch = db.get(DeliveryBatch, batch_id)
    if batch is None or batch.deleted_at:
        raise DomainError('NOT_FOUND', '批次不存在或不可访问', 404)
    if not _is_admin(actor) and (batch.courier_id != actor.staff_id
                                 or actor.staff_role != 'COURIER'):
        raise DomainError('NOT_FOUND', '批次不存在或不可访问', 404)
    assignments = _active_batch_assignments(db, batch.id)
    if not _is_admin(actor):
        assignments = [assignment for assignment in assignments
                       if assignment.courier_id == actor.staff_id]
    orders = []
    for assignment in assignments:
        order = db.get(Order, assignment.order_id)
        if order is not None and not order.deleted_at:
            orders.append(_delivery_order_view(db, order, actor))
    return ok({'batch': _batch_view(db, batch), 'orders': orders})


@router.get('/courier/orders/{order_id}', response_model=Envelope[DeliveryOrderView],
             response_model_exclude_none=True)
def courier_order(order_id: str, db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    _require_delivery_actor(actor)
    order = db.get(Order, order_id)
    if order is None or order.deleted_at:
        raise DomainError('NOT_FOUND', '订单不存在或不可访问', 404)
    assignment = _active_assignment(db, order.id)
    if not _is_admin(actor):
        if (assignment is None or assignment.courier_id != actor.staff_id
                or actor.staff_role != 'COURIER'):
            raise DomainError('NOT_FOUND', '订单不存在或不可访问', 404)
        batch = db.get(DeliveryBatch, assignment.batch_id)
        if batch is None or batch.deleted_at or batch.courier_id != actor.staff_id:
            raise DomainError('NOT_FOUND', '订单不存在或不可访问', 404)
    return ok(_delivery_order_view(db, order, actor))


def _lock_order_delivery(db, order_id: str, expected_version: int | None = None) -> tuple[Order, DeliveryAssignment, DeliveryBatch]:
    order = db.get(Order, order_id)
    if order is None or order.deleted_at:
        raise DomainError('NOT_FOUND', '订单不存在或不可访问', 404)
    assignment = _active_assignment(db, order.id)
    if assignment is None:
        raise DomainError('ASSIGNMENT_REQUIRED', '订单没有有效配送执行', 409)
    order = lock(db, Order, order_id, expected_version)
    assignment = lock(db, DeliveryAssignment, assignment.id)
    if not assignment.active:
        raise DomainError('ASSIGNMENT_INACTIVE', '配送执行已失效', 409)
    batch = lock(db, DeliveryBatch, assignment.batch_id)
    if batch.deleted_at:
        raise DomainError('BATCH_DELETED', '批次已删除', 409)
    return order, assignment, batch


def _require_current_courier(actor: Actor, assignment: DeliveryAssignment,
                             batch: DeliveryBatch | None = None) -> None:
    if _is_admin(actor):
        # Never an implicit all-ADMIN assignment bypass: administrative access
        # to another courier's assignment needs the delivery capability.
        require_admin(actor, 'delivery.manage')
        return
    if actor.staff_role != 'COURIER' or assignment.courier_id != actor.staff_id:
        raise DomainError('FORBIDDEN', '配送任务已改派或不属于当前账号', 403)
    if batch is not None and batch.courier_id != actor.staff_id:
        raise DomainError('FORBIDDEN', '批次已改派，不能继续旧任务', 403)


def _record_delivery_wage(db, order: Order, assignment: DeliveryAssignment,
                          proof: DeliveryProof) -> None:
    """Single hook shared by normal and batch proof commits.

    Payroll owns policy and idempotency.  Keeping the import local avoids a module
    cycle while preserving the one-transaction delivery+wage guarantee.
    """
    from . import payroll

    payroll.on_delivery(db, order, assignment, proof)


def _record_delivery_proof(
    db,
    *,
    order: Order,
    assignment: DeliveryAssignment,
    batch: DeliveryBatch,
    actor: Actor,
    photo_asset_id: str | None,
    photo_reason: str,
    placement_note: str,
    position,
    location_reason: str,
    location_failure_code: str | None,
    approved: BusinessException | None,
) -> tuple[DeliveryProof, datetime]:
    """Write the proof, delivery projections, wage and referral facts atomically.

    Callers must have completed every validation and hold the order/assignment/batch
    locks.  No HTTP or partial-fact work happens here, so batch commits can reuse it
    without nested requests.
    """
    now = utcnow()
    position_data = (position.model_dump(by_alias=True, mode='json')
                     if position is not None else None)
    proof = DeliveryProof(
        order_id=order.id, assignment_id=assignment.id,
        photo_asset_id=photo_asset_id, active=True,
        data={
            'photoAssetId': photo_asset_id,
            'skipPhotoReason': photo_reason or None,
            'position': position_data,
            'locationFailureCode': (location_failure_code or None),
            'skipLocationReason': location_reason or None,
            'locationExceptionId': approved.id if approved else None,
            'placementNote': placement_note or None,
            'operatorId': actor.id,
            # The wage owner is the assignment's courier, not whoever pressed
            # submit on their behalf.  An ADMIN assigned to itself is both.
            'courierId': assignment.courier_id,
            'serverDeliveredAt': now.isoformat(),
            'coordinateSystem': 'WGS84',
        })
    db.add(proof)
    db.flush()

    assignment.status = 'DELIVERED'
    assignment.data = {**(assignment.data or {}), 'deliveredAt': now.isoformat(),
                       'proofId': proof.id, 'deliveredBy': actor.id}
    touch(assignment)
    previous_delivery = (order.data or {}).get('delivery')
    if not isinstance(previous_delivery, dict):
        previous_delivery = {}
    order.status = 'DELIVERED'
    order.data = {**(order.data or {}), 'deliveryProofId': proof.id,
                  'deliveredAt': now.isoformat(),
                  'delivery': {**previous_delivery,
                               'assignmentId': assignment.id, 'batchId': batch.id,
                               'courierId': assignment.courier_id,
                               'deliveredAt': now.isoformat()}}
    touch(order)
    _record_delivery_wage(db, order, assignment, proof)
    # Qualification must be atomic with the delivery fact and never re-issue on
    # a retried/idempotent proof request.
    _qualify_referral(db, order)
    db.flush()
    # Optional outbox write: a failed notification must never roll back the
    # delivery fact.  The helper's nested savepoint contains mail failures.
    from .growth import notify_order_event
    notify_order_event(db, order, 'STATUS_UPDATED')
    return proof, now


@router.post('/courier/orders/{order_id}/proof', response_model=Envelope[dict])
def courier_proof(order_id: str, payload: DeliveryProofInput, db=Depends(get_db),
                  actor: Actor = Depends(actor_dep)):
    _require_delivery_actor(actor)
    target = db.get(DeliveryAssignment, payload.delivery_assignment_id)
    if target is None:
        if db.get(DeliveryBatch, payload.delivery_assignment_id) is not None:
            raise DomainError('ASSIGNMENT_REQUIRED',
                              '必须使用真实配送执行ID，不能使用批次ID', 422)
        raise DomainError('ASSIGNMENT_NOT_FOUND', '配送执行不存在', 404)
    if target.order_id != order_id:
        raise DomainError('ASSIGNMENT_MISMATCH', '配送执行与订单不匹配', 404)
    order, assignment, batch = _lock_order_delivery(db, order_id, payload.expected_version)
    if assignment.id != payload.delivery_assignment_id:
        raise DomainError('ASSIGNMENT_CHANGED', '配送执行已变更，请刷新后重试', 409)
    _require_current_courier(actor, assignment, batch)
    if order.status != 'DELIVERING' or assignment.status != 'STARTED':
        raise DomainError('INVALID_TRANSITION', '只有配送中的当前执行可以提交交付', 409,
                          {'orderStatus': order.status, 'assignmentStatus': assignment.status})
    guard_blockers = _order_blockers(db, order, assignment)
    if guard_blockers:
        raise DomainError('DELIVERY_GUARD_FAILED', '付款、打印、装订、装袋或异常条件已失效',
                          409, {'blockers': guard_blockers})
    existing = _proof_for_assignment(db, assignment.id)
    if existing is not None:
        raise DomainError('PROOF_ALREADY_SUBMITTED', '该配送执行已有有效交付凭证', 409,
                          {'proofId': existing.id})

    photo_reason = (payload.skip_photo_reason or '').strip()
    location_reason = (payload.skip_location_reason or '').strip()
    approved = _approved_location_exception(db, assignment)
    shape_errors = proof_shape_errors(
        role='ADMIN' if _is_admin(actor) else 'COURIER',
        has_photo=bool(payload.photo_asset_id),
        skip_photo_reason=photo_reason,
        has_position=payload.position is not None,
        skip_location_reason=location_reason,
        location_approved=approved is not None,
        location_failure_code=payload.location_failure_code,
    )
    if shape_errors:
        raise DomainError('PROOF_INVALID', '交付凭证不符合要求', 422,
                          {'blockers': shape_errors})
    if payload.photo_asset_id:
        asset = db.scalar(select(Asset).where(Asset.id == payload.photo_asset_id).with_for_update())
        if (asset is None or asset.deleted_at is not None or asset.status != 'READY'
                or not (asset.mime or '').startswith('image/')):
            raise DomainError('PHOTO_INVALID', '交付照片尚未就绪或不是图片', 422)
        if asset.purpose != 'DELIVERY_PROOF':
            raise DomainError('PHOTO_INVALID', '资产用途不是交付照片', 422)
        if asset.assignment_id:
            if asset.assignment_id != assignment.id:
                raise DomainError('PHOTO_NOT_IN_SCOPE', '交付照片不属于当前配送执行', 403)
        elif asset.staff_id != actor.staff_id:
            raise DomainError('PHOTO_NOT_IN_SCOPE', '交付照片不属于当前账号', 403)
        if asset.expires_at is not None and asset.expires_at <= utcnow():
            raise DomainError('PHOTO_INVALID', '交付照片已过期', 422)
    if payload.position is not None:
        max_age, max_accuracy = _workflow_limits(db)
        errors = position_errors(payload.position, now=utcnow(), max_age_seconds=max_age,
                                 max_accuracy_meters=max_accuracy)
        if errors:
            raise DomainError('LOCATION_INVALID', '设备定位不符合新鲜度或精度要求', 422,
                              {'blockers': errors})
    proof, now = _record_delivery_proof(
        db, order=order, assignment=assignment, batch=batch, actor=actor,
        photo_asset_id=payload.photo_asset_id, photo_reason=photo_reason,
        placement_note=(payload.placement_note or '').strip(),
        position=payload.position, location_reason=location_reason,
        location_failure_code=payload.location_failure_code, approved=approved)

    related_batches = sorted(set(db.scalars(
        select(DeliveryAssignment.batch_id).where(DeliveryAssignment.order_id == order.id))))
    for related_batch_id in related_batches:
        refreshed = refresh_batch(db, related_batch_id)
        if (refreshed is not None and refreshed.id == batch.id
                and refreshed.status == 'COMPLETED'):
            refreshed.data = {**(refreshed.data or {}), 'completedAt': now.isoformat()}
            touch(refreshed)
    audit(db, actor, 'DELIVERY_PROOF_SUBMITTED', 'ORDER', order.id,
          data={'assignmentId': assignment.id, 'proofId': proof.id,
                'hasPhoto': bool(payload.photo_asset_id),
                'hasPosition': payload.position is not None,
                'locationExceptionId': approved.id if approved else None})
    return ok({'order': _delivery_order_view(db, order, actor),
               'proof': _proof_view(db, proof)})


def _batch_building_key(assignment: DeliveryAssignment) -> str:
    snapshot = (assignment.data or {}).get('locationSnapshot')
    if isinstance(snapshot, dict):
        return str(snapshot.get('name') or snapshot.get('locationId')
                   or assignment.order_id)
    return assignment.order_id


def _validate_batch_photo(db, actor: Actor, batch_id: str, photo_asset_id: str | None,
                          assignments: list[DeliveryAssignment]) -> Asset | None:
    """Authorize one shared photo against the selected assignment group.

    A courier may only share a photo they own and that is scoped to this batch or
    to one of the selected assignments.  Group hints in ``Asset.data`` are additive:
    neither an arbitrary asset nor another courier's photo can be borrowed.
    """
    if not photo_asset_id:
        return None
    asset = db.scalar(select(Asset).where(Asset.id == photo_asset_id).with_for_update())
    now = utcnow()
    if (asset is None or asset.deleted_at is not None or asset.status != 'READY'
            or not (asset.mime or '').startswith('image/')
            or asset.purpose != 'DELIVERY_PROOF'):
        raise DomainError('PHOTO_INVALID', '交付照片尚未就绪、已过期或用途不符', 422)
    if asset.expires_at is not None and asset.expires_at <= now:
        raise DomainError('PHOTO_INVALID', '交付照片已过期', 422)

    selected = {assignment.id for assignment in assignments}
    data = asset.data or {}
    assignment_ids = data.get('assignmentIds')
    markers: set[str] = set()
    if asset.assignment_id:
        markers.add(asset.assignment_id)
    if isinstance(assignment_ids, list):
        markers.update(str(value) for value in assignment_ids if value)
    scoped_batch = data.get('batchId') or data.get('deliveryBatchId')
    if scoped_batch and scoped_batch != batch_id:
        raise DomainError('PHOTO_NOT_IN_SCOPE', '交付照片属于其他配送批次', 403)
    if markers and not markers.issubset(selected):
        raise DomainError('PHOTO_NOT_IN_SCOPE', '交付照片包含本次选择之外的配送执行', 403)
    if not markers and not scoped_batch:
        raise DomainError('PHOTO_NOT_IN_SCOPE', '交付照片未绑定本次配送范围', 403)

    selected_couriers = {assignment.courier_id for assignment in assignments}
    admin_own = _is_admin(actor) and asset.staff_id == actor.staff_id
    if asset.staff_id not in selected_couriers and not admin_own:
        raise DomainError('PHOTO_NOT_IN_SCOPE', '交付照片不属于当前配送员', 403)
    if asset.assignment_id:
        anchor = next((assignment for assignment in assignments
                       if assignment.id == asset.assignment_id), None)
        if anchor is not None and asset.staff_id != anchor.courier_id and not admin_own:
            raise DomainError('PHOTO_NOT_IN_SCOPE', '交付照片不属于该配送执行', 403)
    return asset


@router.post('/courier/batches/{batch_id}/delivery-proofs/batch',
             response_model=Envelope[BatchDeliveryProofResultView])
def courier_batch_proofs(batch_id: str, payload: BatchDeliveryProofInput,
                         db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    """Submit one shared photo for many assignments in one all-or-nothing commit.

    The lock order matches every other delivery write path: orders sorted by
    id, assignments sorted by id, then the batch row.  Every input is validated
    before the first proof fact is emitted; one stale version, guard, photo or
    location therefore rolls the whole selection back.
    """
    _require_delivery_actor(actor)
    batch_stub = db.get(DeliveryBatch, batch_id)
    if batch_stub is None or batch_stub.deleted_at:
        raise DomainError('NOT_FOUND', '批次不存在或不可访问', 404)

    order_ids = sorted({item.order_id for item in payload.items})
    assignment_ids = sorted({item.delivery_assignment_id for item in payload.items})
    orders = {order.id: order for order in db.scalars(
        select(Order).where(Order.id.in_(order_ids)).order_by(Order.id).with_for_update())}
    if len(orders) != len(order_ids):
        raise DomainError('ORDER_NOT_FOUND', '选择的订单不存在', 404)
    assignments = list(db.scalars(
        select(DeliveryAssignment).where(DeliveryAssignment.id.in_(assignment_ids))
        .order_by(DeliveryAssignment.id).with_for_update()))
    if len(assignments) != len(assignment_ids):
        raise DomainError('ASSIGNMENT_NOT_FOUND', '选择的配送执行不存在', 404)
    by_assignment = {assignment.id: assignment for assignment in assignments}
    batch = lock(db, DeliveryBatch, batch_id)
    if batch.deleted_at:
        raise DomainError('NOT_FOUND', '批次不存在或不可访问', 404)
    if batch.status != 'DELIVERING':
        raise DomainError('INVALID_TRANSITION', '只有配送中的批次可以批量提交交付凭证', 409,
                          {'batchStatus': batch.status})

    selected: list[tuple[Order, DeliveryAssignment]] = []
    building_keys = set()
    for item in payload.items:
        order = orders[item.order_id]
        assignment = by_assignment[item.delivery_assignment_id]
        if assignment.batch_id != batch.id:
            raise DomainError('BATCH_MISMATCH', '配送执行不属于当前批次', 409,
                              {'orderId': order.id, 'assignmentId': assignment.id})
        if assignment.order_id != order.id:
            raise DomainError('ASSIGNMENT_MISMATCH', '配送执行与订单不匹配', 409,
                              {'orderId': order.id, 'assignmentId': assignment.id})
        if not assignment.active:
            raise DomainError('ASSIGNMENT_INACTIVE', '配送执行已失效', 409,
                              {'orderId': order.id, 'assignmentId': assignment.id})
        _require_current_courier(actor, assignment, batch)
        if order.deleted_at:
            raise DomainError('ORDER_NOT_FOUND', '订单不存在或不可访问', 404,
                              {'orderId': order.id})
        if order.version != item.expected_version:
            raise DomainError('VERSION_CONFLICT', '订单已被更新，请刷新后重试', 409,
                              {'orderId': order.id, 'expectedVersion': item.expected_version,
                               'currentVersion': order.version})
        if assignment.version != item.expected_assignment_version:
            raise DomainError('VERSION_CONFLICT', '配送执行已被更新，请刷新后重试', 409,
                              {'assignmentId': assignment.id,
                               'expectedVersion': item.expected_assignment_version,
                               'currentVersion': assignment.version})
        if order.status != 'DELIVERING' or assignment.status != 'STARTED':
            raise DomainError('INVALID_TRANSITION', '只有配送中的当前执行可以提交交付', 409,
                              {'orderId': order.id, 'orderStatus': order.status,
                               'assignmentStatus': assignment.status})
        guard_blockers = _order_blockers(db, order, assignment)
        if guard_blockers:
            raise DomainError('DELIVERY_GUARD_FAILED', '付款、打印、装订、装袋或异常条件已失效',
                              409, {'orderId': order.id, 'blockers': guard_blockers})
        existing = _proof_for_assignment(db, assignment.id)
        if existing is not None:
            raise DomainError('PROOF_ALREADY_SUBMITTED', '该配送执行已有有效交付凭证', 409,
                              {'orderId': order.id, 'proofId': existing.id})
        selected.append((order, assignment))
        building_keys.add(_batch_building_key(assignment))
    if len(building_keys) != 1:
        raise DomainError('BUILDING_MISMATCH', '一次批量交付只能选择同一楼栋的订单', 409,
                          {'buildings': sorted(building_keys)})

    photo_reason = (payload.skip_photo_reason or '').strip()
    placement_note = (payload.placement_note or '').strip()
    max_age, max_accuracy = _workflow_limits(db)
    now = utcnow()
    approved_by_order: dict[str, BusinessException | None] = {}
    for item, (order, assignment) in zip(payload.items, selected):
        approved = _approved_location_exception(db, assignment)
        approved_by_order[order.id] = approved
        shape_errors = proof_shape_errors(
            role='ADMIN' if _is_admin(actor) else 'COURIER',
            has_photo=bool(payload.photo_asset_id),
            skip_photo_reason=photo_reason,
            has_position=item.position is not None,
            skip_location_reason=(item.skip_location_reason or '').strip(),
            location_approved=approved is not None,
            location_failure_code=item.location_failure_code,
        )
        if shape_errors:
            raise DomainError('PROOF_INVALID', '交付凭证不符合要求', 422,
                              {'orderId': order.id, 'blockers': shape_errors})
        if item.position is not None:
            errors = position_errors(item.position, now=now, max_age_seconds=max_age,
                                     max_accuracy_meters=max_accuracy)
            if errors:
                raise DomainError('LOCATION_INVALID', '设备定位不符合新鲜度或精度要求', 422,
                                  {'orderId': order.id, 'blockers': errors})

    _validate_batch_photo(db, actor, batch.id, payload.photo_asset_id,
                          [assignment for _, assignment in selected])

    proofs: list[DeliveryProof] = []
    delivered_at = now
    for item, (order, assignment) in zip(payload.items, selected):
        proof, delivered_at = _record_delivery_proof(
            db, order=order, assignment=assignment, batch=batch, actor=actor,
            photo_asset_id=payload.photo_asset_id, photo_reason=photo_reason,
            placement_note=placement_note, position=item.position,
            location_reason=(item.skip_location_reason or '').strip(),
            location_failure_code=item.location_failure_code,
            approved=approved_by_order[order.id])
        proofs.append(proof)

    refreshed = refresh_batch(db, batch.id)
    if refreshed is not None and refreshed.status == 'COMPLETED':
        refreshed.data = {**(refreshed.data or {}), 'completedAt': delivered_at.isoformat()}
        touch(refreshed)
    for order, assignment, proof in zip([target[0] for target in selected],
                                        [target[1] for target in selected], proofs):
        audit(db, actor, 'DELIVERY_PROOF_SUBMITTED', 'ORDER', order.id,
              data={'assignmentId': assignment.id, 'proofId': proof.id,
                    'batchId': batch.id, 'batchProof': True,
                    'hasPhoto': bool(payload.photo_asset_id),
                    'hasPosition': any(item.position is not None
                                       for item in payload.items
                                       if item.order_id == order.id),
                    'locationExceptionId': approved_by_order[order.id].id
                    if approved_by_order[order.id] else None})
    return ok({
        'batchId': batch.id,
        'proofs': [_proof_view(db, proof) for proof in proofs],
        'orders': [_delivery_order_view(db, order, actor)
                   for order, _ in selected],
    })


@router.post('/courier/orders/{order_id}/exception', response_model=Envelope[dict])
def courier_exception(order_id: str, payload: CourierExceptionInput, db=Depends(get_db),
                      actor: Actor = Depends(actor_dep)):
    _require_delivery_actor(actor)
    try:
        exception_type = normalize_exception_type(payload.type)
    except ValueError as error:
        raise DomainError('EXCEPTION_TYPE_INVALID', str(error), 422) from error
    order, assignment, batch = _lock_order_delivery(db, order_id, payload.expected_version)
    _require_current_courier(actor, assignment, batch)
    if order.status != 'DELIVERING' or assignment.status != 'STARTED':
        raise DomainError('INVALID_TRANSITION', '只有配送中的当前执行可以登记异常', 409)
    damaged = exception_type == 'DAMAGED_OR_MISSING'
    exception = BusinessException(
        order_id=order.id, type='DELIVERY_EXCEPTION', status='OPEN',
        reason=payload.reason, blocks_production=damaged, blocks_delivery=True,
        data={'code': exception_type, 'label': payload.type,
              'assignmentId': assignment.id, 'assignmentVersion': assignment.version,
              'reportedBy': actor.id, 'reportedAt': utcnow().isoformat(),
              'batchId': batch.id})
    db.add(exception)
    db.flush()
    assignment.status = 'EXCEPTION'
    assignment.data = {**(assignment.data or {}), 'exceptionId': exception.id}
    touch(assignment)
    order.status = 'DELIVERY_EXCEPTION'
    order.data = {**(order.data or {}), 'deliveryException': {
        'id': exception.id, 'type': exception_type, 'reason': payload.reason,
        'assignmentId': assignment.id, 'reportedAt': utcnow().isoformat()}}
    touch(order)
    audit(db, actor, 'DELIVERY_EXCEPTION_REPORTED', 'ORDER', order.id,
          reason=payload.reason, data={'exceptionId': exception.id, 'type': exception_type})
    return ok({'exception': _exception_view(exception),
               'order': _delivery_order_view(db, order, actor)})


@router.post('/courier/orders/{order_id}/location-exception', response_model=Envelope[dict])
def courier_location_exception(order_id: str, payload: LocationExceptionInput,
                               db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    _require_delivery_actor(actor)
    if _is_admin(actor) or actor.staff_role != 'COURIER':
        raise DomainError('COURIER_REQUIRED', '定位例外由配送员申请、管理员审批', 403)
    if payload.code not in LOCATION_FAILURE_CODES:
        raise DomainError('LOCATION_FAILURE_CODE_INVALID', '定位失败原因代码无效', 422)
    order, assignment, batch = _lock_order_delivery(db, order_id, payload.expected_version)
    _require_current_courier(actor, assignment, batch)
    if order.status != 'DELIVERING' or assignment.status != 'STARTED':
        raise DomainError('INVALID_TRANSITION', '只有配送中的当前执行可以申请定位例外', 409)
    existing = _active_exception(db, order.id, 'LOCATION_EXCEPTION',
                                 ('OPEN', 'APPROVED'))
    if existing is not None:
        data = existing.data or {}
        if data.get('assignmentId') == assignment.id and data.get('assignmentVersion') == assignment.version:
            raise DomainError('LOCATION_EXCEPTION_EXISTS', '当前执行已有待审批或已批准的定位例外', 409)
    exception = BusinessException(
        order_id=order.id, type='LOCATION_EXCEPTION', status='OPEN',
        reason=payload.reason, blocks_production=False, blocks_delivery=False,
        data={'code': payload.code, 'assignmentId': assignment.id,
              'assignmentVersion': assignment.version, 'requestedBy': actor.id,
              'requestedAt': utcnow().isoformat(), 'batchId': batch.id})
    db.add(exception)
    db.flush()
    audit(db, actor, 'LOCATION_EXCEPTION_REQUESTED', 'DELIVERY_ASSIGNMENT', assignment.id,
          reason=payload.reason, data={'exceptionId': exception.id, 'code': payload.code})
    return ok({'exception': _exception_view(exception),
               'order': _delivery_order_view(db, order, actor)})


# ---------------------------------------------------------------------------
# admin exception handling
# ---------------------------------------------------------------------------


def _exception_view(exception: BusinessException) -> dict:
    return {'id': exception.id, 'orderId': exception.order_id, 'type': exception.type,
            'status': exception.status, 'reason': exception.reason,
            'blocksProduction': exception.blocks_production,
            'blocksDelivery': exception.blocks_delivery, 'version': exception.version,
            'createdAt': exception.created_at, 'data': exception.data or {}}


@router.get('/admin/exceptions', response_model=Envelope[DeliveryExceptionListView])
def admin_exceptions(status: str | None = None, order_id: str | None = None,
                     type: str | None = None, db=Depends(get_db),
                     actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'delivery.manage')
    query = select(BusinessException)
    if status:
        query = query.where(BusinessException.status == status)
    if order_id:
        query = query.where(BusinessException.order_id == order_id)
    if type:
        query = query.where(BusinessException.type == type)
    rows = list(db.scalars(query.order_by(BusinessException.created_at.desc())))
    return ok({'items': [_exception_view(row) for row in rows]})


@router.post('/admin/exceptions/{exception_id}/decision', response_model=Envelope[DeliveryExceptionView])
def admin_location_exception_decision(exception_id: str, payload: ExceptionDecisionInput,
                                      db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'delivery.manage')
    exception = lock(db, BusinessException, exception_id, payload.expected_version)
    if exception.type != 'LOCATION_EXCEPTION':
        raise DomainError('EXCEPTION_TYPE_MISMATCH', '该异常不是定位例外申请', 422)
    if exception.status != 'OPEN':
        raise DomainError('INVALID_TRANSITION', '该定位例外已处理', 409,
                          {'status': exception.status})
    assignment_id = (exception.data or {}).get('assignmentId')
    assignment = db.get(DeliveryAssignment, assignment_id) if assignment_id else None
    if assignment is None:
        raise DomainError('ASSIGNMENT_NOT_FOUND', '配送执行不存在', 409)
    order = lock(db, Order, assignment.order_id)
    assignment = lock(db, DeliveryAssignment, assignment.id)
    if (not assignment.active or assignment.id != assignment_id
            or (exception.data or {}).get('assignmentVersion') != assignment.version):
        raise DomainError('LOCATION_APPROVAL_STALE',
                          '审批绑定当前订单与配送执行版本，该申请已失效', 409)
    if order.status != 'DELIVERING' or assignment.status != 'STARTED':
        raise DomainError('INVALID_TRANSITION', '当前执行已不在配送中', 409)
    exception.status = payload.decision
    exception.data = {**(exception.data or {}), 'decision': payload.decision,
                      'decidedBy': actor.id, 'decidedAt': utcnow().isoformat(),
                      'decisionReason': payload.reason}
    touch(exception)
    audit(db, actor, 'LOCATION_EXCEPTION_DECIDED', 'DELIVERY_ASSIGNMENT', assignment.id,
          reason=payload.reason, data={'exceptionId': exception.id,
                                       'decision': payload.decision})
    return ok(_exception_view(exception))


@router.post('/admin/exceptions/{exception_id}/resolve', response_model=Envelope[DeliveryExceptionView])
def admin_delivery_exception_resolve(exception_id: str, payload: ExceptionResolveInput,
                                     db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'delivery.manage')
    exception = lock(db, BusinessException, exception_id, payload.expected_version)
    if exception.type != 'DELIVERY_EXCEPTION':
        raise DomainError('EXCEPTION_TYPE_MISMATCH', '该异常不是配送异常', 422)
    if exception.status not in OPEN_EXCEPTION_STATUSES:
        raise DomainError('INVALID_TRANSITION', '该配送异常已处理', 409)
    if exception.blocks_production:
        raise DomainError('PRODUCTION_CORRECTION_REQUIRED',
                          '资料损坏或缺失必须先完成生产纠错，不能仅重新安排配送', 409)
    assignment_id = (exception.data or {}).get('assignmentId')
    assignment = db.get(DeliveryAssignment, assignment_id) if assignment_id else None
    if assignment is None:
        raise DomainError('ASSIGNMENT_NOT_FOUND', '配送执行不存在', 409)
    order = lock(db, Order, assignment.order_id)
    assignment = lock(db, DeliveryAssignment, assignment.id)
    batch = lock(db, DeliveryBatch, assignment.batch_id)
    current = _active_assignment(db, order.id)
    if current is None or current.id != assignment.id:
        raise DomainError('ASSIGNMENT_CHANGED', '配送任务已重新安排，请刷新后重试', 409)
    if not _payment_confirmed(order) or order.packing_status != 'PACKED':
        raise DomainError('DELIVERY_GUARD_FAILED', '付款或装袋条件已失效，不能重新安排配送', 409)
    remaining = db.scalar(select(func.count(BusinessException.id))
                          .where(BusinessException.order_id == order.id,
                                 BusinessException.id != exception.id,
                                 BusinessException.status.in_(OPEN_EXCEPTION_STATUSES),
                                 BusinessException.blocks_delivery.is_(True)))
    if remaining:
        raise DomainError('DELIVERY_BLOCKED', '仍有其他阻断异常未解决', 409)
    if order.refund_locked:
        raise DomainError('REFUND_LOCKED', '退款审核期间不能重新安排配送', 409)
    assignment.active = False
    assignment.status = 'CANCELLED'
    assignment.data = {**(assignment.data or {}), 'releasedAt': utcnow().isoformat(),
                       'releasedBy': actor.id, 'releaseReason': payload.reason}
    touch(assignment)
    order.status = 'READY_FOR_DELIVERY'
    order.data = {**(order.data or {}), 'deliveryResolvedAt': utcnow().isoformat(),
                  'deliveryResolvedBy': actor.id, 'deliveryResolution': payload.reason}
    touch(order)
    exception.status = 'RESOLVED'
    exception.data = {**(exception.data or {}), 'resolution': payload.resolution,
                      'resolvedBy': actor.id, 'resolvedAt': utcnow().isoformat(),
                      'resolutionReason': payload.reason}
    touch(exception)
    db.flush()
    unfinished = db.scalar(select(func.count(DeliveryAssignment.id))
                           .where(DeliveryAssignment.batch_id == batch.id,
                                  DeliveryAssignment.active.is_(True),
                                  DeliveryAssignment.status.in_(('PLANNED', 'STARTED', 'EXCEPTION'))))
    if not unfinished and batch.status == 'DELIVERING':
        delivered = db.scalar(select(func.count(DeliveryAssignment.id))
                              .where(DeliveryAssignment.batch_id == batch.id,
                                     DeliveryAssignment.status == 'DELIVERED'))
        batch.status = 'COMPLETED' if delivered else 'CANCELLED'
        batch.data = {**(batch.data or {}),
                      'endedAt': utcnow().isoformat(), 'endedBy': actor.id}
        touch(batch)
    audit(db, actor, 'DELIVERY_EXCEPTION_RESOLVED', 'ORDER', order.id,
          reason=payload.reason, data={'exceptionId': exception.id})
    # The order re-entered READY_FOR_DELIVERY; notify after all state changes.
    from .growth import notify_order_event
    notify_order_event(db, order, 'STATUS_UPDATED')
    return ok(_exception_view(exception))


# ---------------------------------------------------------------------------
# order view (shared with commerce)
# ---------------------------------------------------------------------------


def delivery_order_view(db, order: Order, actor: Actor) -> dict:
    """Role-cropped delivery view; never exposes printable assets or management money."""
    admin = _is_admin(actor)
    assignment = _active_assignment(db, order.id)
    proof = _proof_for_assignment(db, assignment.id) if assignment else None
    approval = _approved_location_exception(db, assignment) if assignment else None
    blockers = _order_blockers(db, order, assignment)
    data = order.data or {}
    delivery = data.get('delivery') if isinstance(data.get('delivery'), dict) else {}
    packing = db.scalar(select(PackingTask).where(PackingTask.order_id == order.id))
    assignment_id = assignment.id if assignment else None
    batch_id = assignment.batch_id if assignment else None
    allowed: list[str] = []
    if assignment is not None:
        if admin:
            allowed.append('REVIEW_ADDRESS')
            if assignment.address_review or order.address_review:
                allowed.append('APPROVE_ADDRESS')
        if order.status == 'DELIVERING' and assignment.status == 'STARTED':
            allowed.extend(['SUBMIT_PROOF', 'REPORT_EXCEPTION', 'REQUEST_LOCATION_EXCEPTION'])
        if order.status == 'DELIVERED':
            allowed.append('VIEW_PROOF')
    view = {
        'id': order.id,
        'version': order.version,
        'pickupCode': order.pickup_code,
        'status': order.status,
        'nickname': order.nickname,
        'phone': order.phone,
        'customerNote': order.customer_note or None,
        'locationId': order.location_id,
        'locationName': (assignment.data or {}).get('locationSnapshot', {}).get('name')
                        if assignment and (assignment.data or {}).get('locationSnapshot')
                        else _location_snapshot(db, order).get('name'),
        'locationSnapshot': (assignment.data or {}).get('locationSnapshot')
                            if assignment and (assignment.data or {}).get('locationSnapshot')
                            else _location_snapshot(db, order),
        'packingStatus': order.packing_status,
        'packingVersion': packing.version if packing else None,
        'packingChecklistVersion': order.content_revision,
        'addressReview': bool(order.address_review or (assignment and assignment.address_review)),
        'deliveryAssignmentId': assignment_id,
        'batchId': batch_id,
        'requestedSlot': _slot_brief(db, order.requested_slot_id),
        'confirmedSlot': _slot_brief(db, order.confirmed_slot_id),
        'assignment': _assignment_view(db, assignment) if assignment else None,
        'proof': _proof_view(db, proof) if proof else None,
        'locationExceptionApproval': (
            _location_exception_approval_view(approval) if approval else None),
        'deliveryStatus': assignment.status if assignment else None,
        'allowedActions': allowed,
        'blockers': blockers,
    }
    if admin:
        view.update({
            'contentRevision': order.content_revision,
            'currentQuoteId': order.quote_id,
            'actualStartedAt': order.actual_started_at,
            'settlement': data.get('settlement'),
            'totalCents': order.total_cents,
            'cashReceivedCents': order.cash_received_cents,
            'settlementKind': order.settlement_kind,
            'productionSummary': {
                'packingStatus': order.packing_status,
                'addressReview': bool(order.address_review),
                'refundLocked': order.refund_locked,
                'deliveryStartedAt': delivery.get('startedAt'),
            },
        })
    return view


def _delivery_order_view(db, order: Order, actor: Actor) -> dict:
    return delivery_order_view(db, order, actor)
