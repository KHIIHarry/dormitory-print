"""Reviewed legacy identity conversion preserves money and execution IDs."""
from copy import deepcopy

import pytest
from sqlalchemy import select

from app.core import Actor, DomainError
from app.models import Campus, DeliveryAssignment, DeliveryBatch, DeliveryGroup, DeliverySlot, Order
from test_delivery_closed_loop import make_world, settle, submit


def shared_world(db, staff_factory):
    first = make_world(db, capacity=6)
    first.campus.name = '东校区'
    first.slot.campus_id = first.campus.id
    db.commit()
    east = submit(db, first)
    staff = staff_factory()
    actor = Actor(staff_id=staff.id, staff_role='ADMIN', capabilities={'*'})
    settle(db, east, actor)
    second = make_world(db, capacity=3)
    second.campus.name = '西校区'
    second.slot.campus_id = second.campus.id
    db.commit()
    west = submit(db, second)
    settle(db, west, actor)
    # Construct exactly the shared identity layout used before this feature.
    western_batch = db.scalar(select(DeliveryBatch).where(DeliveryBatch.slot_id == second.slot.id))
    western_group = db.get(DeliveryGroup, western_batch.group_id)
    western_batch.slot_id = western_group.slot_id = first.slot.id
    west.requested_slot_id = west.confirmed_slot_id = first.slot.id
    first.slot.campus_id = None
    first.slot.occupied = 2
    second.slot.occupied = 0
    db.commit()
    return first, second, east, west, actor


def test_preview_does_not_write_and_split_preserves_execution_and_receipts(db, staff_factory):
    from app.delivery_campus_migration import apply_campus_migration, preview_campus_migration
    first, second, east, west, actor = shared_world(db, staff_factory)
    original_data = deepcopy(east.data)
    assignments = {row.order_id: row.id for row in db.scalars(select(DeliveryAssignment))}
    preview = preview_campus_migration(db, first.slot.id)
    assert preview['blockers'] == []
    assert first.slot.campus_id is None and first.slot.occupied == 2
    result = apply_campus_migration(db, {**preview['manifest'], 'reason': '核对旧共享时段',
        'capacities': {first.campus.id: 4, second.campus.id: 2}}, actor)
    db.flush()
    assert east.confirmed_slot_id != west.confirmed_slot_id
    for order, campus, capacity in ((east, first.campus, 4), (west, second.campus, 2)):
        slot = db.get(DeliverySlot, order.confirmed_slot_id)
        assert slot.campus_id == campus.id and slot.occupied == 1 and slot.capacity == capacity
        assignment = db.get(DeliveryAssignment, assignments[order.id])
        batch = db.get(DeliveryBatch, assignment.batch_id)
        assert batch.slot_id == slot.id and batch.campus_id == campus.id
        assert db.get(DeliveryGroup, batch.group_id).slot_id == slot.id
        assert order.cash_received_cents == 10 and order.status == 'PENDING_PRINT'
    assert east.data['settlementSlotSnapshot'] == original_data['settlementSlotSnapshot']
    assert east.data['requestedSlotSnapshot'] == original_data['requestedSlotSnapshot']
    assert first.slot.occupied == 0 and not first.slot.enabled
    assert result['originalSlotId'] == first.slot.id


def test_migration_rejects_silent_capacity_expansion_and_stale_plan(db, staff_factory):
    from app.delivery_campus_migration import apply_campus_migration, preview_campus_migration
    first, second, east, west, actor = shared_world(db, staff_factory)
    preview = preview_campus_migration(db, first.slot.id)
    with pytest.raises(DomainError) as error:
        apply_campus_migration(db, {**preview['manifest'], 'reason': '核对',
            'capacities': {first.campus.id: 6, second.campus.id: 6}}, actor)
    assert error.value.code == 'MIGRATION_CAPACITY_MISMATCH'
    assert first.slot.campus_id is None
    west.version += 1
    db.flush()
    with pytest.raises(DomainError) as error:
        apply_campus_migration(db, {**preview['manifest'], 'reason': '核对',
            'capacities': {first.campus.id: 4, second.campus.id: 2}}, actor)
    assert error.value.code == 'PLAN_STALE'


def test_migration_preserves_sealed_state_and_does_not_accept_old_proposal(db, staff_factory):
    from app.delivery_campus_migration import apply_campus_migration, preview_campus_migration
    from app.models import utcnow
    first, second, east, west, actor = shared_world(db, staff_factory)
    first.slot.sealed_at = utcnow()
    first.slot.seal_reason = '原已封批'
    west.data = {**west.data, 'slotProposal': {'slotId': first.slot.id, 'confirmed': False}}
    db.commit()
    preview = preview_campus_migration(db, first.slot.id)
    result = apply_campus_migration(db, {**preview['manifest'], 'reason': '核对',
        'capacities': {first.campus.id: 4, second.campus.id: 2}}, actor)
    for slot_id in result['slotIdsByCampus'].values():
        slot = db.get(DeliverySlot, slot_id)
        assert slot.sealed_at == first.slot.sealed_at and slot.seal_reason == '原已封批'
    assert west.data['slotProposal']['slotId'] == west.confirmed_slot_id
    assert west.data['slotProposal']['confirmed'] is False


def test_migration_blocks_unknown_or_inconsistent_occupied_facts(db, staff_factory):
    from app.delivery_campus_migration import preview_campus_migration
    first, _, _, _, _ = shared_world(db, staff_factory)
    first.slot.occupied = 3
    db.flush()
    preview = preview_campus_migration(db, first.slot.id)
    assert 'OCCUPANCY_REVIEW_REQUIRED' in {item['code'] for item in preview['blockers']}


def test_historical_snapshot_reference_requires_scope_review_and_changes_fingerprint(db, staff_factory):
    from app.delivery_campus_migration import preview_campus_migration
    from app.models import DeliveryChange
    first, second, east, west, _ = shared_world(db, staff_factory)
    initial = preview_campus_migration(db, first.slot.id)
    change = DeliveryChange(order_id=east.id, slot_id=second.slot.id, kind='RESCHEDULED',
                            status='APPLIED', original={'slotId': first.slot.id,
                                                        'campusId': second.campus.id},
                            proposed={'id': second.slot.id}, data={})
    db.add(change)
    db.flush()
    updated = preview_campus_migration(db, first.slot.id)
    assert updated['manifest']['fingerprint'] != initial['manifest']['fingerprint']
    assert any(item['code'] == 'HISTORICAL_SCOPE_REVIEW_REQUIRED' and item['relatedId'] == change.id
               for item in updated['blockers'])


def test_snapshot_with_unknown_campus_requires_review(db, staff_factory):
    from app.delivery_campus_migration import preview_campus_migration
    from app.models import DeliveryChange
    first, second, east, _, _ = shared_world(db, staff_factory)
    change = DeliveryChange(order_id=east.id, slot_id=second.slot.id, kind='RESCHEDULED',
                            original={'id': first.slot.id}, proposed={}, data={})
    db.add(change)
    db.flush()
    preview = preview_campus_migration(db, first.slot.id)
    assert any(item['code'] == 'HISTORICAL_SCOPE_REVIEW_REQUIRED' and item['relatedId'] == change.id
               for item in preview['blockers'])


@pytest.mark.parametrize('conflict', ['group_source', 'batch_source', 'active_member', 'active_member_missing'])
def test_migration_blocks_disagreeing_batch_source_graph(db, staff_factory, conflict):
    from app.delivery_campus_migration import preview_campus_migration
    first, second, east, _, _ = shared_world(db, staff_factory)
    batch = db.scalar(select(DeliveryBatch).where(DeliveryBatch.slot_id == first.slot.id,
                                                   DeliveryBatch.campus_id == first.campus.id))
    if conflict == 'group_source':
        batch.slot_id = second.slot.id
    elif conflict == 'batch_source':
        group = db.get(DeliveryGroup, batch.group_id)
        group.slot_id = second.slot.id
    else:
        assignment = db.scalar(select(DeliveryAssignment).where(DeliveryAssignment.order_id == east.id))
        east.confirmed_slot_id = None if conflict == 'active_member_missing' else second.slot.id
    db.flush()
    preview = preview_campus_migration(db, first.slot.id)
    assert any(item['code'] == 'BATCH_SCOPE_CONFLICT' and item['relatedId'] == batch.id
               for item in preview['blockers'])


def test_pending_change_with_historical_source_preserves_other_target(db, staff_factory):
    from app.delivery_campus_migration import apply_campus_migration, preview_campus_migration
    from app.models import DeliveryChange
    first, second, east, _, actor = shared_world(db, staff_factory)
    proposed = {'slotId': second.slot.id, 'campusId': first.campus.id}
    change = DeliveryChange(order_id=east.id, slot_id=first.slot.id, kind='RESCHEDULE_PROPOSED',
                            status='PENDING', original={'slotId': first.slot.id,
                                                       'campusId': first.campus.id},
                            proposed=proposed, data={})
    db.add(change)
    db.flush()
    preview = preview_campus_migration(db, first.slot.id)
    assert not preview['blockers']
    apply_campus_migration(db, {**preview['manifest'], 'reason': '核对',
        'capacities': {first.campus.id: 4, second.campus.id: 2}}, actor)
    assert change.proposed == proposed
    assert change.slot_id == first.slot.id


def test_inactive_historical_assignment_allows_rescheduled_order(db, staff_factory):
    from app.delivery_campus_migration import apply_campus_migration, preview_campus_migration
    first, second, east, _, actor = shared_world(db, staff_factory)
    assignment = db.scalar(select(DeliveryAssignment).where(DeliveryAssignment.order_id == east.id))
    assignment.active = False
    east.confirmed_slot_id = second.slot.id
    # A historical assignment no longer consumes the old slot's capacity.
    first.slot.occupied = 1
    db.flush()
    preview = preview_campus_migration(db, first.slot.id)
    assert not preview['blockers']
    apply_campus_migration(db, {**preview['manifest'], 'reason': '核对',
        'capacities': {first.campus.id: 4, second.campus.id: 2}}, actor)
    assert east.confirmed_slot_id == second.slot.id
    assert not assignment.active
