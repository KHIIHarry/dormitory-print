from datetime import timedelta

import pytest

from app.core import Actor, DomainError
from app.models import Campus, DeliveryBatch, DeliveryGroup, TrashEntry, utcnow
from test_delivery_closed_loop import make_world, submit


def test_delete_campus_rejects_empty_slot_reference(db):
    from app.campuses import delete_campus
    world = make_world(db)
    other = Campus(name='Other campus')
    db.add(other)
    db.flush()
    world.location.campus_id = other.id
    world.slot.campus_id = world.campus.id
    db.flush()
    with pytest.raises(DomainError) as exc:
        delete_campus(db, world.campus.id, Actor())
    assert exc.value.code == 'RESOURCE_REFERENCED'


def test_repair_reports_unscoped_and_cross_campus_slot_facts(db):
    from app.delivery_repair import delivery_repair_preview
    world = make_world(db)
    other = Campus(name='Other campus')
    db.add(other)
    db.flush()
    world.slot.campus_id = None
    db.add_all([DeliveryGroup(slot_id=world.slot.id, campus_id=world.campus.id),
                DeliveryGroup(slot_id=world.slot.id, campus_id=other.id)])
    db.flush()
    result = delivery_repair_preview(db)
    row = next(item for item in result['campusScopeIssues'] if item['slotId'] == world.slot.id)
    assert 'SLOT_CAMPUS_MISSING' in row['reasons']
    assert 'SHARED_ACROSS_CAMPUSES' in row['reasons']
    assert set(row['campusIds']) == {world.campus.id, other.id}


def test_restore_preview_blocks_batch_scope_mismatch(db):
    from app.maintenance import _restore_blockers
    world = make_world(db)
    other = Campus(name='Other campus')
    db.add(other)
    db.flush()
    world.slot.campus_id = world.campus.id
    batch = DeliveryBatch(name='Archived', starts_at=world.slot.starts_at,
                          ends_at=world.slot.ends_at, slot_id=world.slot.id,
                          campus_id=other.id, deleted_at=utcnow(), data={})
    db.add(batch)
    db.flush()
    entry = TrashEntry(target_type='BATCH', target_id=batch.id, actor_id='staff',
                       reason='test', purge_at=utcnow()+timedelta(days=1), data={})
    db.add(entry)
    db.flush()
    assert 'BATCH_SCOPE_MISMATCH' in {item.code for item in _restore_blockers(db, entry)}


def test_order_export_uses_captured_campus_identity(db):
    from app.exports import ORDER_FIELDS, _order_value
    world = make_world(db)
    world.slot.campus_id = world.campus.id
    db.flush()
    order = submit(db, world)
    order.data = {**(order.data or {}), 'locationSnapshot': {
        'campusId': world.campus.id, 'campusName': 'Original campus'}}
    world.campus.name = 'Renamed campus'
    assert {'campusId', 'campusName'} <= set(ORDER_FIELDS)
    assert _order_value(db, order, 'campusId', None) == world.campus.id
    assert _order_value(db, order, 'campusName', None) == 'Original campus'


def test_delivery_change_notice_uses_captured_campus_name(db):
    from app.growth import _delivery_change_variables
    from app.models import DeliveryChange
    world = make_world(db)
    world.slot.campus_id = world.campus.id
    db.flush()
    order = submit(db, world)
    order.data = {**(order.data or {}), 'locationSnapshot': {
        'name': 'Dormitory', 'campusId': world.campus.id, 'campusName': 'Original campus'}}
    world.campus.name = 'Renamed campus'
    change = DeliveryChange(order_id=order.id, kind='RESCHEDULE_PROPOSED',
                            original={}, proposed={'campusName': 'Original campus'}, data={})
    variables = _delivery_change_variables(db, order, change)
    assert 'Original campus' in variables['deliveryLocation']
    assert 'Renamed campus' not in variables['deliveryLocation']


def test_repair_excludes_cross_campus_batch_from_candidates(db):
    from app.delivery_repair import delivery_repair_preview
    world = make_world(db)
    other = Campus(name='Other campus')
    db.add(other)
    db.flush()
    world.slot.campus_id = world.campus.id
    batch = DeliveryBatch(name='Wrong campus', starts_at=world.slot.starts_at,
                          ends_at=world.slot.ends_at, slot_id=world.slot.id,
                          campus_id=other.id, data={})
    db.add(batch)
    db.flush()
    result = delivery_repair_preview(db)
    detached = next(row for row in result['detachedBatches'] if row['batchId'] == batch.id)
    assert 'SLOT_CAMPUS_MISMATCH' in detached['reasons']


def test_restore_rejects_missing_identity_for_scoped_slot_but_allows_legacy(db):
    from app.maintenance import _restore_blockers
    world = make_world(db)
    world.slot.campus_id = world.campus.id
    batch = DeliveryBatch(name='Archived', starts_at=world.slot.starts_at,
                          ends_at=world.slot.ends_at, slot_id=world.slot.id,
                          deleted_at=utcnow(), data={})
    db.add(batch)
    db.flush()
    entry = TrashEntry(target_type='BATCH', target_id=batch.id, actor_id='staff',
                       reason='test', purge_at=utcnow()+timedelta(days=1), data={})
    db.add(entry)
    db.flush()
    assert 'BATCH_SCOPE_MISSING' in {item.code for item in _restore_blockers(db, entry)}
    world.slot.campus_id = None
    db.flush()
    assert not _restore_blockers(db, entry)
