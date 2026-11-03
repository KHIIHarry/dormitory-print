from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.delivery_schemas import SlotInput
from app.delivery_plan import slot_acceptance
from app.models import DeliverySlot


def test_new_slot_requires_explicit_campus():
    with pytest.raises(ValidationError):
        SlotInput(date='2026-11-03', name='晚间', start='19:00', end='20:00', capacity=10)


def test_slot_campus_edit_checks_historical_change_snapshot_reference(db):
    from app.core import Actor, DomainError
    from app.delivery import admin_slot_update
    from app.delivery_schemas import SlotUpdate
    from app.models import Campus, DeliveryChange
    from test_delivery_closed_loop import configured_slot, make_world, submit

    world = make_world(db)
    order = submit(db, world)
    historical = configured_slot(db, campus=world.campus)
    west = Campus(name='西校区')
    db.add(west)
    db.flush()
    db.add(DeliveryChange(order_id=order.id, slot_id=world.slot.id, kind='ETA_UPDATED',
                          original={'slotId': historical.id, 'campusId': world.campus.id}, proposed={}))
    db.flush()
    with pytest.raises(DomainError) as error:
        admin_slot_update(historical.id, SlotUpdate(campusId=west.id, expectedVersion=historical.version),
                          db, Actor(staff_id='staff', staff_role='ADMIN', capabilities={'*'}))
    assert error.value.code == 'SLOT_CAMPUS_LOCKED'


def test_legacy_unscoped_slot_is_not_selectable(db):
    now = datetime.now(timezone.utc)
    slot = DeliverySlot(date='2026-11-03', name='旧时段', starts_at=now + timedelta(hours=2),
                        ends_at=now + timedelta(hours=3), open_at=now - timedelta(hours=1),
                        cutoff_at=now + timedelta(hours=1), capacity=10, occupied=0,
                        enabled=True, data={})
    db.add(slot)
    db.flush()
    assert slot_acceptance(db, slot, now, service_open=True)['status'] == 'CAMPUS_MISSING'


def test_manual_slot_batch_derives_campus_and_group(db):
    from app.core import Actor
    from app.delivery import admin_batch_create
    from app.delivery_schemas import BatchCreate
    from app.models import Campus, DeliveryBatch, DeliveryGroup

    now = datetime.now(timezone.utc)
    campus = Campus(name='东校区')
    db.add(campus)
    db.flush()
    slot = DeliverySlot(campus_id=campus.id, date='2026-11-03', name='晚间',
                        starts_at=now + timedelta(hours=2), ends_at=now + timedelta(hours=3),
                        open_at=now - timedelta(hours=1), cutoff_at=now + timedelta(hours=1),
                        capacity=10, occupied=0, enabled=True, data={})
    db.add(slot)
    db.flush()
    result = admin_batch_create(BatchCreate(name='配送', slotId=slot.id), db,
                                Actor(staff_id='staff', staff_role='ADMIN', capabilities={'*'}))
    batch = db.get(DeliveryBatch, result['data']['id'])
    group = db.get(DeliveryGroup, batch.group_id)
    assert batch.campus_id == campus.id
    assert group.slot_id == slot.id and group.campus_id == campus.id


def test_empty_batch_rejects_first_order_from_other_campus(db):
    from app.delivery import _campus_mismatch_blocker
    from app.models import Campus, DeliveryBatch, Location, Principal
    from tests.auth_helpers import make_order

    principal = Principal()
    db.add(principal)
    db.flush()
    order = make_order(db, principal.id)
    west = Campus(name='西校区')
    db.add(west)
    db.flush()
    now = datetime.now(timezone.utc)
    batch = DeliveryBatch(name='西校区空批次', campus_id=west.id,
                          starts_at=now, ends_at=now + timedelta(hours=1),
                          status='PLANNED', reason='', data={})
    db.add(batch)
    db.flush()
    assert db.get(Location, order.location_id).campus_id != west.id
    assert _campus_mismatch_blocker(db, batch, order)['code'] == 'CAMPUS_MISMATCH'


def test_sealing_one_campus_slot_does_not_seal_same_window_other_slot(db):
    from app.core import Actor
    from app.delivery_plan import apply_seal, seal_preview
    from app.models import Campus

    now = datetime.now(timezone.utc)
    east, west = Campus(name='东校区'), Campus(name='西校区')
    db.add_all([east, west])
    db.flush()
    def slot(campus):
        return DeliverySlot(campus_id=campus.id, date='2026-11-03', name='晚间',
                            starts_at=now + timedelta(hours=2), ends_at=now + timedelta(hours=3),
                            open_at=now - timedelta(hours=1), cutoff_at=now + timedelta(hours=1),
                            capacity=2, occupied=0, enabled=True, data={})
    first, second = slot(east), slot(west)
    db.add_all([first, second])
    db.flush()
    actor = Actor(staff_id='staff', staff_role='ADMIN', capabilities={'*'})
    preview = seal_preview(db, first, actor)
    assert preview['blockers'] == []
    assert preview['impact']['campuses'][0]['campusId'] == east.id
    apply_seal(db, first, actor, '测试')
    assert first.sealed_at is not None and second.sealed_at is None
    assert slot_acceptance(db, second, now, service_open=True)['status'] == 'OPEN'


def test_referenced_slot_campus_cannot_be_changed(db):
    from app.core import Actor, DomainError
    from app.delivery import admin_slot_update
    from app.delivery_schemas import SlotUpdate
    from app.models import Campus, Principal
    from tests.auth_helpers import make_order

    principal = Principal()
    db.add(principal)
    db.flush()
    order = make_order(db, principal.id)
    slot = db.get(DeliverySlot, order.requested_slot_id)
    west = Campus(name='西校区')
    db.add(west)
    db.flush()
    with pytest.raises(DomainError) as error:
        admin_slot_update(slot.id, SlotUpdate(campusId=west.id, expectedVersion=slot.version),
                          db, Actor(staff_id='staff', staff_role='ADMIN', capabilities={'*'}))
    assert error.value.code == 'SLOT_CAMPUS_LOCKED'


def test_seal_plan_ignores_foreign_slot_but_rejects_new_target_order(db, staff_factory):
    from app.core import Actor, DomainError
    from app.delivery import admin_slot_seal, admin_slot_seal_preview
    from app.delivery_schemas import SlotSealCommit, SlotSealPreview
    from app.models import Campus, Principal
    from tests.auth_helpers import make_order
    from tests.test_delivery_closed_loop import configured_slot

    staff = staff_factory(role='ADMIN')
    actor = Actor(staff_id=staff.id, staff_role='ADMIN', capabilities={'*'})
    east, west = Campus(name='东校区'), Campus(name='西校区')
    db.add_all([east, west])
    db.flush()
    target, foreign = configured_slot(db, campus=east), configured_slot(db, campus=west)
    plan = admin_slot_seal_preview(target.id, SlotSealPreview(expectedVersion=target.version), db, actor)['data']
    foreign.capacity += 1
    db.flush()
    admin_slot_seal(target.id, SlotSealCommit(planId=plan['planId'], expectedVersion=target.version,
                                             reason='测试'), db, actor)
    assert foreign.sealed_at is None

    target2 = configured_slot(db, campus=east)
    plan2 = admin_slot_seal_preview(target2.id, SlotSealPreview(expectedVersion=target2.version), db, actor)['data']
    principal = Principal()
    db.add(principal)
    db.flush()
    order = make_order(db, principal.id)
    order.requested_slot_id = target2.id
    db.flush()
    with pytest.raises(DomainError) as error:
        admin_slot_seal(target2.id, SlotSealCommit(planId=plan2['planId'], expectedVersion=target2.version,
                                                  reason='测试'), db, actor)
    assert error.value.code == 'PLAN_STALE'


def test_seal_includes_legacy_json_slot_batch(db):
    from app.core import Actor
    from app.delivery_plan import apply_seal
    from app.models import Campus, DeliveryBatch
    from tests.test_delivery_closed_loop import configured_slot

    campus = Campus(name='东校区')
    db.add(campus)
    db.flush()
    slot = configured_slot(db, campus=campus)
    batch = DeliveryBatch(name='旧批次', campus_id=campus.id, starts_at=slot.starts_at,
                          ends_at=slot.ends_at, status='PLANNED', reason='',
                          data={'slotId': slot.id})
    db.add(batch)
    db.flush()
    apply_seal(db, slot, Actor(staff_id='staff', staff_role='ADMIN', capabilities={'*'}), '测试')
    assert batch.sealed_at is not None


def test_existing_execution_rejects_group_campus_drift(db):
    from app.delivery import _campus_mismatch_blocker
    from app.models import Campus, DeliveryBatch, DeliveryGroup, Principal
    from tests.auth_helpers import make_order

    principal = Principal()
    db.add(principal)
    db.flush()
    order = make_order(db, principal.id)
    slot = db.get(DeliverySlot, order.requested_slot_id)
    other = Campus(name='其他校区')
    db.add(other)
    db.flush()
    group = DeliveryGroup(slot_id=slot.id, campus_id=other.id, data={})
    db.add(group)
    db.flush()
    batch = DeliveryBatch(name='漂移批次', campus_id=slot.campus_id, slot_id=slot.id,
                          group_id=group.id, starts_at=slot.starts_at, ends_at=slot.ends_at,
                          status='PLANNED', reason='', data={})
    db.add(batch)
    db.flush()
    assert _campus_mismatch_blocker(db, batch, order, existing=True)['code'] == 'SLOT_CAMPUS_MISMATCH'


def test_public_slot_filter_hides_disabled_and_unscoped_campuses(db):
    from app.delivery import public_slots
    from app.models import Campus
    from tests.test_delivery_closed_loop import configured_slot, enable_service

    enable_service(db)
    east = Campus(name='东校区')
    west = Campus(name='西校区', selection_enabled=False)
    db.add_all([east, west])
    db.flush()
    open_slot = configured_slot(db, campus=east)
    disabled = configured_slot(db, campus=west)
    legacy = configured_slot(db, campus=east)
    legacy.campus_id = None
    db.flush()
    rows = public_slots(date=open_slot.date, campus_id=east.id, db=db)['data']['items']
    assert {row['id'] for row in rows} == {open_slot.id}
    all_rows = public_slots(date=open_slot.date, campus_id=None, db=db)['data']['items']
    assert {row['id'] for row in all_rows} == {open_slot.id}
    assert disabled.id not in {row['id'] for row in all_rows}


def test_execution_and_proof_guards_reject_drifted_group(db, staff_factory):
    from app.core import Actor, DomainError
    from app.delivery import _order_blockers, _start_blockers, admin_batch_publish
    from app.delivery_schemas import BatchVersionInput
    from app.models import Campus, DeliveryAssignment, DeliveryBatch, DeliveryGroup
    from tests.test_delivery import make_ready_order

    staff = staff_factory(role='ADMIN')
    courier = staff_factory(role='COURIER')
    order = make_ready_order(db)
    slot = db.get(DeliverySlot, order.confirmed_slot_id)
    other = Campus(name='其他校区')
    db.add(other)
    db.flush()
    group = DeliveryGroup(slot_id=slot.id, campus_id=other.id, data={})
    db.add(group)
    db.flush()
    batch = DeliveryBatch(name='漂移批次', campus_id=slot.campus_id, slot_id=slot.id,
                          group_id=group.id, courier_id=courier.id, starts_at=slot.starts_at,
                          ends_at=slot.ends_at, status='PLANNED', reason='', data={})
    db.add(batch)
    db.flush()
    assignment = DeliveryAssignment(order_id=order.id, batch_id=batch.id, courier_id=courier.id,
                                    status='PLANNED', active=True, data={})
    db.add(assignment)
    db.commit()
    actor = Actor(staff_id=staff.id, staff_role='ADMIN', capabilities={'*'})
    with pytest.raises(DomainError) as error:
        admin_batch_publish(batch.id, BatchVersionInput(expectedVersion=batch.version), db, actor)
    assert error.value.code == 'SLOT_CAMPUS_MISMATCH'
    batch.status = 'PUBLISHED'
    assert 'SLOT_CAMPUS_MISMATCH' in {item['code'] for item in _start_blockers(db, batch, [assignment], {order.id: order})}
    assert 'SLOT_CAMPUS_MISMATCH' in {item['code'] for item in _order_blockers(db, order, assignment)}


def test_automatic_seal_rejects_cross_campus_legacy_json_batch(db):
    from app.core import Actor, DomainError
    from app.delivery_plan import persist_seal_facts
    from app.models import Campus, DeliveryBatch
    from tests.test_delivery_closed_loop import configured_slot

    east, west = Campus(name='东校区'), Campus(name='西校区')
    db.add_all([east, west])
    db.flush()
    slot = configured_slot(db, campus=east)
    batch = DeliveryBatch(name='旧跨校区批次', campus_id=west.id, starts_at=slot.starts_at,
                          ends_at=slot.ends_at, status='PLANNED', reason='', data={'slotId': slot.id})
    db.add(batch)
    db.flush()
    with pytest.raises(DomainError) as error:
        persist_seal_facts(db, slot, Actor(staff_id='staff', staff_role='ADMIN', capabilities={'*'}), '测试')
    assert error.value.code == 'SLOT_CAMPUS_MISMATCH'
    assert batch.sealed_at is None


def test_publish_empty_batch_checks_explicit_slot_campus(db, staff_factory):
    from app.core import Actor, DomainError
    from app.delivery import admin_batch_publish
    from app.delivery_schemas import BatchVersionInput
    from app.models import Campus, DeliveryBatch
    from tests.test_delivery_closed_loop import configured_slot

    admin, courier = staff_factory(role='ADMIN'), staff_factory(role='COURIER')
    east, west = Campus(name='东校区'), Campus(name='西校区')
    db.add_all([east, west])
    db.flush()
    slot = configured_slot(db, campus=east)
    slot.sealed_at = datetime.now(timezone.utc)
    batch = DeliveryBatch(name='错误归属空批次', campus_id=west.id, slot_id=slot.id,
                          courier_id=courier.id, starts_at=slot.starts_at, ends_at=slot.ends_at,
                          status='PLANNED', reason='', data={})
    db.add(batch)
    db.commit()
    with pytest.raises(DomainError) as error:
        admin_batch_publish(batch.id, BatchVersionInput(expectedVersion=batch.version), db,
                            Actor(staff_id=admin.id, staff_role='ADMIN', capabilities={'*'}))
    assert error.value.code == 'SLOT_CAMPUS_MISMATCH'


def test_legacy_null_batch_campus_allows_only_existing_single_campus_execution(db):
    from app.delivery import _batch_scope_blocker, _campus_mismatch_blocker
    from app.models import Campus, DeliveryAssignment, DeliveryBatch, Location, Principal
    from tests.auth_helpers import make_order

    principal = Principal()
    db.add(principal)
    db.flush()
    order = make_order(db, principal.id)
    slot = db.get(DeliverySlot, order.requested_slot_id)
    batch = DeliveryBatch(name='旧批次', campus_id=None, slot_id=slot.id,
                          starts_at=slot.starts_at, ends_at=slot.ends_at,
                          status='PLANNED', reason='', data={})
    db.add(batch)
    db.flush()
    db.add(DeliveryAssignment(order_id=order.id, batch_id=batch.id, status='PLANNED', active=True, data={}))
    db.flush()
    assert _batch_scope_blocker(db, batch, existing=True) is None
    assert _campus_mismatch_blocker(db, batch, order, existing=True) is None
    assert _campus_mismatch_blocker(db, batch, order, existing=False)['code'] == 'CAMPUS_CONFIG_MISSING'
    west = Campus(name='西校区')
    db.add(west)
    db.flush()
    db.get(Location, order.location_id).campus_id = west.id
    db.flush()
    assert _batch_scope_blocker(db, batch, existing=True)['code'] == 'SLOT_CAMPUS_MISMATCH'


def test_legacy_custom_batch_requires_all_members_to_have_one_campus(db):
    from app.delivery import _batch_scope_blocker
    from app.models import Campus, DeliveryAssignment, DeliveryBatch, Location, Principal
    from tests.auth_helpers import make_order

    principal = Principal()
    db.add(principal)
    db.flush()
    first, second = make_order(db, principal.id), make_order(db, principal.id)
    slot = db.get(DeliverySlot, first.requested_slot_id)
    batch = DeliveryBatch(name='旧自定义批次', starts_at=slot.starts_at, ends_at=slot.ends_at,
                          status='PLANNED', reason='', data={})
    db.add(batch)
    db.flush()
    for order in (first, second):
        db.add(DeliveryAssignment(order_id=order.id, batch_id=batch.id, status='PLANNED', active=True, data={}))
    db.flush()
    assert _batch_scope_blocker(db, batch, existing=True) is None
    other = Campus(name='西校区')
    db.add(other)
    db.flush()
    db.get(Location, second.location_id).campus_id = other.id
    db.flush()
    assert _batch_scope_blocker(db, batch, existing=True)['code'] == 'CAMPUS_CONFIG_MISSING'
    db.get(Location, second.location_id).campus_id = None
    db.flush()
    assert _batch_scope_blocker(db, batch, existing=True)['code'] == 'CAMPUS_CONFIG_MISSING'


@pytest.mark.parametrize('corruption,code', [('source_campus', 'SLOT_CAMPUS_MISMATCH'),
                                             ('zero_occupancy', 'OCCUPANCY_REVIEW_REQUIRED'),
                                             ('batch_campus', 'CAMPUS_MISMATCH')])
def test_reschedule_rejects_corrupt_source_without_mutating_facts(db, admin_client, corruption, code):
    from sqlalchemy import select
    from app.core import Actor, DomainError
    from app.delivery_plan import confirm_reschedule, propose_reschedule
    from app.models import Campus, DeliveryAssignment, DeliveryBatch, Order
    from tests.test_delivery_closed_loop import admin_client_actor, configured_slot, make_world, settle, submit

    world = make_world(db)
    order = submit(db, world)
    settle(db, order, admin_client_actor(admin_client.staff))
    target = configured_slot(db, campus=world.campus, offset_hours=44)
    change = propose_reschedule(db, admin_client_actor(admin_client.staff), order, target, '调整')
    assignment = db.scalar(select(DeliveryAssignment).where(DeliveryAssignment.order_id == order.id,
                                                               DeliveryAssignment.active.is_(True)))
    batch = db.get(DeliveryBatch, assignment.batch_id)
    if corruption in ('source_campus', 'batch_campus'):
        west = Campus(name='西校区')
        db.add(west)
        db.flush()
        if corruption == 'source_campus':
            world.slot.campus_id = west.id
        else:
            batch.campus_id = west.id
    else:
        world.slot.occupied = 0
    db.flush()
    source_occupied = world.slot.occupied
    with pytest.raises(DomainError) as error:
        confirm_reschedule(db, Actor(principal_id=world.principal.id), order, target.id)
    assert error.value.code == code
    assert order.confirmed_slot_id == world.slot.id
    assert world.slot.occupied == source_occupied and target.occupied == 0
    assert assignment.active and change.status == 'PENDING'
    assert db.scalar(select(DeliveryAssignment).where(DeliveryAssignment.order_id == order.id,
                                                       DeliveryAssignment.active.is_(True))).id == assignment.id


def test_reschedule_existing_legacy_source_with_matching_members(db, admin_client):
    from sqlalchemy import select
    from app.core import Actor
    from app.delivery_plan import confirm_reschedule, propose_reschedule
    from app.models import DeliveryAssignment, DeliveryBatch
    from tests.test_delivery_closed_loop import admin_client_actor, configured_slot, make_world, settle, submit

    world = make_world(db)
    order = submit(db, world)
    settle(db, order, admin_client_actor(admin_client.staff))
    target = configured_slot(db, campus=world.campus, offset_hours=44)
    change = propose_reschedule(db, admin_client_actor(admin_client.staff), order, target, '调整')
    assignment = db.scalar(select(DeliveryAssignment).where(DeliveryAssignment.order_id == order.id,
                                                               DeliveryAssignment.active.is_(True)))
    batch = db.get(DeliveryBatch, assignment.batch_id)
    world.slot.campus_id = None
    batch.campus_id = None
    db.flush()
    confirm_reschedule(db, Actor(principal_id=world.principal.id), order, target.id)
    assert order.confirmed_slot_id == target.id and change.status == 'APPLIED'
    assert world.slot.occupied == 0 and target.occupied == 1
    assert not assignment.active


def test_migrated_arrangement_uses_current_slot_identity_keeps_historical_windows(db):
    from app.delivery_plan import arrangement_for_order
    from app.models import DeliveryChange, Principal
    from tests.auth_helpers import make_order
    from tests.test_delivery_closed_loop import configured_slot

    principal = Principal()
    db.add(principal)
    db.flush()
    order = make_order(db, principal.id)
    old = db.get(DeliverySlot, order.requested_slot_id)
    campus = db.get(__import__('app.models', fromlist=['Campus']).Campus, old.campus_id)
    new = configured_slot(db, campus=campus)
    original = {'id': old.id, 'name': old.name, 'startsAt': old.starts_at.isoformat(),
                'endsAt': old.ends_at.isoformat()}
    order.requested_slot_id = order.confirmed_slot_id = new.id
    order.data = {'requestedSlotSnapshot': original, 'confirmedSlotSnapshot': original,
                  'settlementSlotSnapshot': original, 'campusMigration': {old.id: new.id}}
    eta = {'id': old.id, 'startsAt': '2030-01-01T03:30:00+00:00',
           'endsAt': '2030-01-01T04:30:00+00:00'}
    db.add(DeliveryChange(order_id=order.id, kind='ETA_UPDATED', status='APPLIED',
                          original=original, proposed=eta, reason='调整', data={}))
    db.flush()
    view = arrangement_for_order(db, order)
    assert view['effective']['id'] == new.id
    assert view['effective']['campusId'] == campus.id
    assert view['effective']['startsAt'] == eta['startsAt']
    assert view['originalRequest'] == original and view['originalConfirmation'] == original
    assert order.data['confirmedSlotSnapshot'] == original
