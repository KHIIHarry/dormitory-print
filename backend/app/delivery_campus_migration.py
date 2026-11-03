"""Explicitly reviewed conversion of shared legacy slots.

This is an operations tool, not an automatic repair on a read request. It never
collects another payment, creates an assignment or sends a customer notice.
"""
from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json

from sqlalchemy import select

from .core import DomainError, audit, lock, require_admin, touch
from .models import (BatchPlan, Campus, DeliveryAssignment, DeliveryBatch,
                     DeliveryChange, DeliveryGroup, DeliverySlot, Location, Order, utcnow)


def _batch_slot(batch):
    data = batch.data or {}
    return batch.slot_id or data.get('slotId') or data.get('slot_id') or (data.get('slotSnapshot') or {}).get('id')


def _context(db, slot_id):
    slot = db.get(DeliverySlot, slot_id)
    if slot is None:
        raise DomainError('NOT_FOUND', '配送时段不存在', 404)
    groups = list(db.scalars(select(DeliveryGroup).where(DeliveryGroup.slot_id == slot_id).order_by(DeliveryGroup.id)))
    group_ids = {row.id for row in groups}
    batches = [row for row in db.scalars(select(DeliveryBatch).order_by(DeliveryBatch.id))
               if _batch_slot(row) == slot_id or row.group_id in group_ids]
    batch_ids = [row.id for row in batches]
    assignments = list(db.scalars(select(DeliveryAssignment).where(
        DeliveryAssignment.batch_id.in_(batch_ids)).order_by(DeliveryAssignment.id)))
    member_ids = {row.order_id for row in assignments}
    changes = [row for row in db.scalars(select(DeliveryChange).order_by(DeliveryChange.id))
               if row.slot_id == slot_id or any(
                   snapshot.get('slotId') == slot_id or snapshot.get('id') == slot_id
                   for snapshot in (row.original or {}, row.proposed or {}))]
    member_ids.update(row.order_id for row in changes)
    orders = [row for row in db.scalars(select(Order).order_by(Order.id))
              if row.requested_slot_id == slot_id or row.confirmed_slot_id == slot_id
              or ((row.data or {}).get('slotProposal') or {}).get('slotId') == slot_id
              or row.id in member_ids]
    campus_by_order = {row.id: db.scalar(select(Location.campus_id).where(Location.id == row.location_id))
                       for row in orders}
    related_group_ids = {row.group_id for row in batches if row.group_id} - group_ids
    related_groups = list(db.scalars(select(DeliveryGroup).where(DeliveryGroup.id.in_(related_group_ids))
                                     .order_by(DeliveryGroup.id)))
    return slot, groups, batches, assignments, changes, orders, campus_by_order, related_groups


def _fingerprint(context):
    def record(row):
        return {column.name: getattr(row, column.name) for column in row.__table__.columns}
    slot, groups, batches, assignments, changes, orders, campus_by_order, related_groups = context
    data = [[record(row) for row in rows] for rows in ([slot], groups, batches, assignments, changes, orders, related_groups)]
    # Address ownership can change without touching an order version.
    data.append(campus_by_order)
    return sha256(json.dumps(data, default=str, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def preview_campus_migration(db, slot_id: str) -> dict:
    with db.no_autoflush:
        context = _context(db, slot_id)
        slot, groups, batches, assignments, changes, orders, campus_by_order, related_groups = context
        blockers = []
        if slot.campus_id:
            blockers.append({'code': 'SLOT_ALREADY_SCOPED', 'relatedId': slot.id})
        if (slot.data or {}).get('campusMigration'):
            blockers.append({'code': 'SLOT_ALREADY_MIGRATED', 'relatedId': slot.id})
        campuses = {row.campus_id for row in groups}
        campuses.update(value for value in campus_by_order.values() if value)
        batch_campuses = {}
        for change in changes:
            for snapshot in (change.original or {}, change.proposed or {}):
                if snapshot.get('slotId') == slot.id or snapshot.get('id') == slot.id:
                    historical_campus = snapshot.get('campusId')
                    if not historical_campus or historical_campus != campus_by_order.get(change.order_id):
                        blockers.append({'code': 'HISTORICAL_SCOPE_REVIEW_REQUIRED', 'relatedId': change.id})
                        break
        for batch in batches:
            group = next((row for row in groups + related_groups if row.id == batch.group_id), None)
            members = [row for row in assignments if row.batch_id == batch.id]
            member_orders = {row.id: row for row in orders}
            if ((batch.slot_id and batch.slot_id != slot.id)
                    or (batch.group_id and (group is None or group.slot_id != slot.id))
                    or any(member_orders.get(row.order_id) is None
                           or (row.active and member_orders[row.order_id].confirmed_slot_id != slot.id)
                           for row in members)):
                blockers.append({'code': 'BATCH_SCOPE_CONFLICT', 'relatedId': batch.id})
            ids = {campus_by_order.get(row.order_id) for row in assignments if row.batch_id == batch.id}
            if batch.campus_id:
                ids.add(batch.campus_id)
            if group:
                ids.add(group.campus_id)
            if None in ids or len(ids) != 1:
                blockers.append({'code': 'BATCH_CAMPUS_REVIEW_REQUIRED', 'relatedId': batch.id})
            else:
                batch_campuses[batch.id] = next(iter(ids))
                campuses.update(ids)
        for order in orders:
            if not campus_by_order[order.id]:
                blockers.append({'code': 'ORDER_CAMPUS_MISSING', 'relatedId': order.id})
        occupied = Counter(campus_by_order[row.id] for row in orders
                           if row.confirmed_slot_id == slot.id and row.settlement_kind
                           and row.status != 'CANCELLED')
        if sum(occupied.values()) != slot.occupied:
            blockers.append({'code': 'OCCUPANCY_REVIEW_REQUIRED', 'relatedId': slot.id})
        campus_views = []
        for campus_id in sorted(campuses):
            campus = db.get(Campus, campus_id)
            if not campus:
                blockers.append({'code': 'CAMPUS_MISSING', 'relatedId': campus_id})
            campus_views.append({'campusId': campus_id, 'campusName': campus.name if campus else '',
                                 'occupied': occupied[campus_id]})
        return {'readOnly': True, 'slotId': slot.id, 'capacity': slot.capacity,
                'occupied': slot.occupied, 'campuses': campus_views, 'blockers': blockers,
                'batchCampuses': batch_campuses,
                'manifest': {'slotId': slot.id, 'expectedVersion': slot.version,
                             'fingerprint': _fingerprint(context)}}


def apply_campus_migration(db, manifest: dict, actor) -> dict:
    require_admin(actor, 'delivery.manage')
    reason = manifest.get('reason')
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 300:
        raise DomainError('MIGRATION_REASON_REQUIRED', '请填写 1 到 300 字的迁移依据', 422)
    slot_id = manifest.get('slotId')
    context = _context(db, slot_id)
    # Match settlement's order -> slot -> group -> batch -> assignment graph.
    # Every existing batch member is included, including finished/trash rows.
    for row in context[5]:
        lock(db, Order, row.id)
    lock(db, DeliverySlot, slot_id, manifest.get('expectedVersion'))
    for model, rows in ((DeliveryGroup, sorted(context[1] + context[7], key=lambda row: row.id)), (DeliveryBatch, context[2]),
                        (DeliveryAssignment, context[3]), (DeliveryChange, context[4])):
        for row in rows:
            lock(db, model, row.id)
    db.expire_all()
    context = _context(db, slot_id)
    preview = preview_campus_migration(db, slot_id)
    if not manifest.get('fingerprint') or preview['manifest']['fingerprint'] != manifest['fingerprint']:
        raise DomainError('PLAN_STALE', '迁移对象或归属已变化，请重新生成预检', 409)
    if preview['blockers']:
        raise DomainError('MIGRATION_BLOCKED', '存量归属或容量仍有冲突，请先核对', 409,
                          {'blockers': preview['blockers']})
    slot, groups, batches, assignments, changes, orders, campus_by_order, related_groups = context
    capacities = manifest.get('capacities')
    if not isinstance(capacities, dict) or not capacities or any(
            not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 100000
            for value in capacities.values()):
        raise DomainError('MIGRATION_CAPACITY_REQUIRED', '请明确填写每个校区的容量（1 到 100000）', 422)
    expected_campuses = {item['campusId'] for item in preview['campuses']}
    if expected_campuses and set(capacities) != expected_campuses:
        raise DomainError('MIGRATION_CAMPUS_MISMATCH', '容量分配须完整匹配预检中的校区', 409)
    if not expected_campuses and len(capacities) != 1:
        raise DomainError('MIGRATION_CAMPUS_MISMATCH', '无引用旧时段须明确指定唯一校区', 409)
    if sum(capacities.values()) != slot.capacity or any(
            capacities[item['campusId']] < item['occupied'] for item in preview['campuses']):
        raise DomainError('MIGRATION_CAPACITY_MISMATCH', '各校区容量之和须等于原总容量，且不得小于已占用数', 409)
    for campus_id in capacities:
        if not db.get(Campus, campus_id):
            raise DomainError('CAMPUS_CONFIG_MISSING', '目标校区不存在', 409)

    from .delivery_plan import slot_snapshot
    occupied = {item['campusId']: item['occupied'] for item in preview['campuses']}
    targets = {}
    for campus_id in sorted(capacities):
        target = DeliverySlot(campus_id=campus_id, date=slot.date, name=slot.name,
                              starts_at=slot.starts_at, ends_at=slot.ends_at,
                              open_at=slot.open_at, cutoff_at=slot.cutoff_at,
                              sealed_at=slot.sealed_at, sealed_by=slot.sealed_by, seal_reason=slot.seal_reason,
                              capacity=capacities[campus_id], occupied=occupied.get(campus_id, 0),
                              enabled=slot.enabled, data={**deepcopy(slot.data or {}),
                                  'campusScopeStatus': 'SCOPED', 'migratedFromSlotId': slot.id})
        db.add(target)
        db.flush()
        targets[campus_id] = target
    mapping = {key: value.id for key, value in targets.items()}
    for group in groups:
        group.slot_id = mapping[group.campus_id]
        touch(group)
    for batch in batches:
        batch.campus_id = preview['batchCampuses'][batch.id]
        batch.slot_id = mapping[batch.campus_id]
        batch.data = {**deepcopy(batch.data or {}), 'slotId': batch.slot_id,
                      'legacySlotSnapshot': (batch.data or {}).get('slotSnapshot'),
                      'slotSnapshot': slot_snapshot(targets[batch.campus_id]), 'migratedFromSlotId': slot.id}
        touch(batch)
    for order in orders:
        new_slot = mapping[campus_by_order[order.id]]
        if order.requested_slot_id == slot.id:
            order.requested_slot_id = new_slot
        if order.confirmed_slot_id == slot.id:
            order.confirmed_slot_id = new_slot
        data = deepcopy(order.data or {})
        proposal = data.get('slotProposal') or {}
        if proposal.get('slotId') == slot.id:
            data['slotProposal'] = {**proposal, 'slotId': new_slot}
        # Original request and settlement snapshots are immutable history.
        data['campusMigration'] = {**(data.get('campusMigration') or {}), slot.id: new_slot}
        order.data = data
        touch(order)
    for change in changes:
        if (change.status == 'PENDING' and
                ((change.proposed or {}).get('slotId') == slot.id
                 or (change.proposed or {}).get('id') == slot.id)):
            target = targets[campus_by_order[change.order_id]]
            change.data = {**deepcopy(change.data or {}), 'campusMigrationOriginalProposed': change.proposed}
            change.proposed = {**change.proposed, **slot_snapshot(target), 'slotId': target.id}
            change.slot_id = target.id
            touch(change)
    affected = {slot.id, *[row.id for row in batches]}
    def includes_identity(value):
        if isinstance(value, dict):
            return any(includes_identity(item) for item in value.values())
        if isinstance(value, list):
            return any(includes_identity(item) for item in value)
        return isinstance(value, str) and value in affected
    for plan in db.scalars(select(BatchPlan).where(BatchPlan.committed_at.is_(None))):
        if includes_identity(plan.data):
            plan.expires_at = utcnow()
            touch(plan)
    slot.occupied = 0
    slot.enabled = False
    slot.data = {**deepcopy(slot.data or {}), 'archivedAt': utcnow().isoformat(),
                 'archiveReason': '旧共享时段已按校区迁移', 'campusMigration': mapping,
                 'campusScopeStatus': 'MIGRATED'}
    touch(slot)
    audit(db, actor, 'DELIVERY_CAMPUS_MIGRATED', 'DELIVERY_SLOT', slot.id, reason=reason.strip(),
          data={'slotIdsByCampus': mapping, 'capacityByCampus': capacities,
                'occupiedByCampus': occupied, 'fingerprint': manifest['fingerprint']})
    db.flush()
    return {'originalSlotId': slot.id, 'slotIdsByCampus': mapping}
