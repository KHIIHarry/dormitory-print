"""Regression coverage for scheduling entry points sharing closed-loop facts."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.delivery_schemas import to_shanghai_date
from app.models import DeliveryAssignment, DeliveryBatch, DeliveryChange, Order, utcnow
from test_delivery_closed_loop import (
    APP_MODULES, act_as, admin_client_actor, configured_slot, make_world, settle, submit,
)


def paid_batch(db, admin):
    world = make_world(db)
    order = submit(db, world)
    settle(db, order, admin_client_actor(admin))
    batch = db.scalar(select(DeliveryBatch).where(DeliveryBatch.slot_id == world.slot.id))
    return world, order, batch


def preview(api, client, batch, action, **values):
    return api(client, '/admin/batch-operations/preview', {
        'action': action, 'sourceBatchIds': [batch.id],
        'versions': {batch.id: batch.version}, **values,
    })


def another_order(db, world, **values):
    row = Order(principal_id=world.principal.id, pickup_code='AUDIT-SECOND',
                preparation_id=world.prep.id, quote_id=world.quote.id,
                total_cents=10, nickname='第二单', phone='13800000001',
                location_id=world.location.id, requested_slot_id=world.slot.id,
                close_at=utcnow() + timedelta(hours=1), data={}, **values)
    db.add(row)
    db.flush()
    return row


def test_manual_add_rejects_unsettled_order(db, api, client, act_as, staff_factory):
    act_as(staff_factory())
    world = make_world(db)
    order = submit(db, world)
    batch = DeliveryBatch(name='人工批次', campus_id=world.campus.id, starts_at=world.slot.starts_at,
                          ends_at=world.slot.ends_at, status='PLANNED', data={})
    db.add(batch)
    db.commit()
    result = preview(api, client, batch, 'ADD', orderIds=[order.id])
    assert result.status_code == 200, result.text
    assert 'PAYMENT_REQUIRED' in {b['code'] for b in result.json()['data']['blockers']}


@pytest.mark.parametrize('reason', ['调整安排', ''])
def test_cross_day_eta_requires_customer_proposal(db, api, client, act_as, staff_factory, reason):
    admin = act_as(staff_factory())
    _, _, batch = paid_batch(db, admin)
    result = preview(api, client, batch, 'RESCHEDULE', payload={
        'startsAt': (batch.starts_at + timedelta(days=1)).isoformat(),
        'endsAt': (batch.ends_at + timedelta(days=1)).isoformat(), 'reason': reason})
    assert result.status_code == 200, result.text
    assert 'CUSTOMER_CONFIRMATION_REQUIRED' in {
        b['code'] for b in result.json()['data']['blockers']}


def shanghai_2300(value):
    date = to_shanghai_date(value)
    local = datetime.fromisoformat(f'{date}T23:00:00').replace(tzinfo=ZoneInfo('Asia/Shanghai'))
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def test_midnight_spanning_eta_only_requires_reason(db, api, client, act_as, staff_factory):
    admin = act_as(staff_factory())
    _, _, batch = paid_batch(db, admin)
    starts = shanghai_2300(batch.starts_at)
    ends = starts + timedelta(hours=3)
    assert to_shanghai_date(starts) == to_shanghai_date(batch.starts_at)
    result = preview(api, client, batch, 'RESCHEDULE', payload={
        'startsAt': starts.isoformat() + '+00:00',
        'endsAt': ends.isoformat() + '+00:00',
        'reason': '夜间配送，窗口跨零点但仍是同一天'})
    assert result.status_code == 200, result.text
    assert result.json()['data']['blockers'] == []


def test_transfer_into_midnight_spanning_batch_needs_reason_not_confirmation(
        db, api, client, act_as, staff_factory):
    admin = act_as(staff_factory())
    _, order, source = paid_batch(db, admin)
    courier = staff_factory(role='COURIER')
    starts = shanghai_2300(source.starts_at)
    target = DeliveryBatch(name='跨零点人工批次', courier_id=courier.id, campus_id=source.campus_id,
                           starts_at=starts, ends_at=starts + timedelta(hours=3),
                           status='PLANNED', data={})
    db.add(target)
    db.commit()
    result = preview(api, client, source, 'TRANSFER', targetBatchId=target.id,
                     orderIds=[order.id], payload={'reason': '夜间改派'})
    assert result.status_code == 200, result.text
    assert result.json()['data']['blockers'] == []


def test_eta_requires_public_reason(db, api, client, act_as, staff_factory):
    admin = act_as(staff_factory())
    _, _, batch = paid_batch(db, admin)
    result = preview(api, client, batch, 'RESCHEDULE', payload={
        'startsAt': batch.starts_at.isoformat(), 'endsAt': batch.ends_at.isoformat()})
    assert 'REASON_REQUIRED' in {b['code'] for b in result.json()['data']['blockers']}


def test_running_same_day_eta_only_updates_undelivered_order(db, api, client, act_as, staff_factory):
    admin = act_as(staff_factory())
    world, order, batch = paid_batch(db, admin)
    batch.starts_at = batch.starts_at.replace(hour=3, minute=0, second=0, microsecond=0)
    batch.ends_at = batch.starts_at + timedelta(hours=1)
    batch.status = 'DELIVERING'
    order.status = 'DELIVERING'
    assignment = db.scalar(select(DeliveryAssignment).where(DeliveryAssignment.order_id == order.id))
    assignment.status = 'STARTED'
    delivered = another_order(db, world, status='DELIVERED',
                              confirmed_slot_id=world.slot.id, settlement_kind='CASH_MANUAL',
                              cash_received_cents=10)
    delivered.data = {'confirmedSlotSnapshot': {'startsAt': batch.starts_at.isoformat(),
                                               'endsAt': batch.ends_at.isoformat()}}
    db.add(DeliveryAssignment(order_id=delivered.id, batch_id=batch.id,
                              active=True, status='DELIVERED', data={}))
    db.commit()
    original_delivered = dict(delivered.data['confirmedSlotSnapshot'])
    result = preview(api, client, batch, 'RESCHEDULE', payload={
        'startsAt': (batch.starts_at + timedelta(minutes=30)).isoformat(),
        'endsAt': (batch.ends_at + timedelta(minutes=30)).isoformat(), 'reason': '交通延误'})
    assert result.json()['data']['blockers'] == [], result.text
    committed = api(client, '/admin/batch-operations/commit', {'planId': result.json()['data']['planId']})
    assert committed.status_code == 200, committed.text
    db.expire_all()
    assert db.get(DeliveryBatch, batch.id).status == 'DELIVERING'
    assert db.scalar(select(DeliveryChange).where(DeliveryChange.order_id == order.id)) is not None
    assert db.scalar(select(DeliveryChange).where(DeliveryChange.order_id == delivered.id)) is None
    from app.delivery_plan import arrangement_for_order
    assert arrangement_for_order(db, db.get(Order, delivered.id))['effective'] == original_delivered


def test_eta_plan_rejects_changed_membership_without_batch_version_change(
        db, api, client, act_as, staff_factory):
    admin = act_as(staff_factory())
    world, _, batch = paid_batch(db, admin)
    result = preview(api, client, batch, 'RESCHEDULE', payload={
        'startsAt': batch.starts_at.isoformat(), 'endsAt': batch.ends_at.isoformat(), 'reason': '延误'})
    new_order = another_order(db, world, status='READY_FOR_DELIVERY',
                              confirmed_slot_id=world.slot.id, settlement_kind='CASH_MANUAL',
                              cash_received_cents=10)
    db.add(DeliveryAssignment(order_id=new_order.id, batch_id=batch.id,
                              status='PLANNED', active=True, data={}))
    db.commit()
    committed = api(client, '/admin/batch-operations/commit', {'planId': result.json()['data']['planId']})
    assert committed.status_code == 409, committed.text
    assert committed.json()['error']['code'] == 'PLAN_STALE'


def test_slot_seal_requires_preview_and_version(db, api, client, act_as, staff_factory):
    act_as(staff_factory())
    slot = configured_slot(db)
    db.commit()
    result = api(client, f'/admin/slots/{slot.id}/seal', {'reason': '提前结束'})
    assert result.status_code == 422, result.text


def test_slot_seal_rejects_unpreviewed_pending_application(db, api, client, act_as, staff_factory):
    act_as(staff_factory())
    world = make_world(db)
    result = api(client, f'/admin/slots/{world.slot.id}/seal/preview', {})
    submit(db, world)
    committed = api(client, f'/admin/slots/{world.slot.id}/seal', {
        'expectedVersion': world.slot.version, 'planId': result.json()['data']['planId'],
        'reason': '提前结束'})
    assert committed.status_code == 409, committed.text
    assert committed.json()['error']['code'] == 'PLAN_STALE'


def test_slot_edit_accepts_unchanged_time_fields_after_order_exists(db, api, client, act_as, staff_factory):
    act_as(staff_factory())
    world = make_world(db)
    submit(db, world)
    from app.delivery_schemas import to_shanghai_hhmm
    result = api(client, f'/admin/slots/{world.slot.id}', {
        'expectedVersion': world.slot.version, 'date': world.slot.date,
        'start': to_shanghai_hhmm(world.slot.starts_at), 'end': to_shanghai_hhmm(world.slot.ends_at),
        'openAt': world.slot.open_at.isoformat(), 'cutoffAt': world.slot.cutoff_at.isoformat(),
        'name': '改名', 'capacity': 6, 'enabled': True, 'reason': '调整容量'}, method='PATCH')
    assert result.status_code == 200, result.text
    assert result.json()['data']['name'] == '改名'


def test_legacy_referenced_slot_can_fill_missing_acceptance_rules(db, api, client, act_as, staff_factory):
    act_as(staff_factory())
    world = make_world(db)
    submit(db, world)
    opens, cutoff = world.slot.open_at, world.slot.cutoff_at
    world.slot.open_at = world.slot.cutoff_at = None
    db.commit()
    result = api(client, f'/admin/slots/{world.slot.id}', {
        'expectedVersion': world.slot.version, 'openAt': opens.isoformat(),
        'cutoffAt': cutoff.isoformat(), 'reason': '补齐接单规则'}, method='PATCH')
    assert result.status_code == 200, result.text


def test_transfer_cannot_silently_change_delivery_window(db, api, client, act_as, staff_factory):
    admin = act_as(staff_factory())
    world, order, source = paid_batch(db, admin)
    courier = staff_factory(role='COURIER')
    target = DeliveryBatch(name='另一天人工批次', courier_id=courier.id, campus_id=source.campus_id,
                           starts_at=source.starts_at + timedelta(days=1),
                           ends_at=source.ends_at + timedelta(days=1), status='PLANNED', data={})
    db.add(target)
    db.commit()
    result = preview(api, client, source, 'TRANSFER', targetBatchId=target.id,
                     orderIds=[order.id], payload={'reason': '改派'})
    assert 'CUSTOMER_CONFIRMATION_REQUIRED' in {b['code'] for b in result.json()['data']['blockers']}


def test_cancel_requires_explicit_destination_for_paid_orders(db, api, client, act_as, staff_factory):
    admin = act_as(staff_factory())
    _, _, batch = paid_batch(db, admin)
    result = preview(api, client, batch, 'CANCEL', payload={'reason': '取消安排'})
    assert 'DESTINATION_REQUIRED' in {b['code'] for b in result.json()['data']['blockers']}


def test_start_rechecks_sealing_for_legacy_published_batch(db, staff_factory):
    from app.delivery import _active_batch_assignments, _start_blockers
    admin = staff_factory()
    _, order, batch = paid_batch(db, admin)
    batch.courier_id = staff_factory(role='COURIER').id
    batch.status = 'PUBLISHED'
    db.commit()
    blockers = _start_blockers(db, batch, _active_batch_assignments(db, batch.id), {order.id: order})
    assert 'BATCH_NOT_SEALED' in {item['code'] for item in blockers}


def test_manual_batch_publish_cannot_bypass_member_slot_sealing(db, api, client, act_as, staff_factory):
    admin = act_as(staff_factory())
    _, order, batch = paid_batch(db, admin)
    batch.slot_id = batch.group_id = None
    batch.data = {}
    batch.courier_id = staff_factory(role='COURIER').id
    db.commit()
    result = api(client, f'/admin/batches/{batch.id}/publish', {'expectedVersion': batch.version})
    assert result.status_code == 409, result.text
    assert result.json()['error']['code'] == 'BATCH_NOT_SEALED'


def test_publish_checks_requested_slot_for_legacy_paid_member(db, api, client, act_as, staff_factory):
    admin = act_as(staff_factory())
    _, order, batch = paid_batch(db, admin)
    batch.slot_id = batch.group_id = None
    batch.data = {}
    batch.courier_id = staff_factory(role='COURIER').id
    order.confirmed_slot_id = None
    db.commit()
    result = api(client, f'/admin/batches/{batch.id}/publish', {'expectedVersion': batch.version})
    assert result.status_code == 409, result.text
    assert result.json()['error']['code'] == 'BATCH_NOT_SEALED'


def test_batch_edit_accepts_reposted_unchanged_window(db, api, client, act_as, staff_factory):
    admin = act_as(staff_factory())
    _, _, batch = paid_batch(db, admin)
    result = api(client, f'/admin/batches/{batch.id}', {
        'expectedVersion': batch.version, 'name': '改名后的批次',
        'startsAt': batch.starts_at.isoformat(), 'endsAt': batch.ends_at.isoformat(),
        'reason': '改名'}, method='PATCH')
    assert result.status_code == 200, result.text
    assert result.json()['data']['name'] == '改名后的批次'


def test_assignment_lock_refreshes_previously_read_execution(db, staff_factory):
    from app.delivery import _lock_assignments
    from sqlalchemy import update
    admin = staff_factory()
    _, order, _ = paid_batch(db, admin)
    assignment = db.scalar(select(DeliveryAssignment).where(DeliveryAssignment.order_id == order.id))
    db.commit()
    from app.db import SessionLocal
    with SessionLocal() as other:
        other.execute(update(DeliveryAssignment).where(DeliveryAssignment.id == assignment.id)
                      .values(status='STARTED', version=assignment.version + 1))
        other.commit()
    rows = _lock_assignments(db, [order.id])
    assert rows[0].status == 'STARTED'


@pytest.mark.parametrize('capabilities,expected', [(['delivery.manage'], 200), (['payment.manage'], 403)])
def test_delivery_repair_preview_requires_delivery_capability(
        api, client, act_as, staff_factory, capabilities, expected):
    act_as(staff_factory(capabilities=capabilities))
    result = api(client, '/admin/delivery-repair/preview', None, method='GET')
    assert result.status_code == expected, result.text


def test_customer_cannot_read_delivery_repair_preview(api, customer_client):
    result = api(customer_client, '/admin/delivery-repair/preview', None, method='GET')
    assert result.status_code == 401, result.text
