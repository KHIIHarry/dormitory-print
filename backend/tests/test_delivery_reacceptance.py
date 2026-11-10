"""Independent acceptance regressions for the settlement/delivery handoff."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.core import Actor, DomainError
from app.models import (BusinessException, DeliveryAssignment, DeliveryBatch,
                        DeliveryChange, DeliverySlot, Order, Receipt, utcnow)
from test_delivery_closed_loop import (APP_MODULES, admin_client_actor, configured_slot,
                                      make_world, settle, submit)


def paid_order(db, admin_client):
    world = make_world(db)
    order = submit(db, world)
    actor = admin_client_actor(admin_client.staff)
    settle(db, order, actor)
    return world, order, actor


def assignment_for(db, order):
    return db.scalar(select(DeliveryAssignment).where(
        DeliveryAssignment.order_id == order.id, DeliveryAssignment.active.is_(True)))


def test_rejected_proposal_does_not_erase_previously_applied_eta(db, admin_client):
    from app.delivery_plan import (arrangement_for_order, propose_reschedule,
                                   record_change, reject_reschedule)
    world, order, actor = paid_order(db, admin_client)
    effective = {**order.data['confirmedSlotSnapshot'],
                 'startsAt': (world.slot.starts_at + timedelta(minutes=20)).isoformat()}
    record_change(db, order, kind='ETA_UPDATED', original=order.data['confirmedSlotSnapshot'],
                  proposed=effective, reason='原配送延误', actor_id=actor.id)
    target = configured_slot(db, offset_hours=40)
    propose_reschedule(db, actor, order, target, '建议调整')
    reject_reschedule(db, Actor(principal_id=order.principal_id), order, '不同意')
    db.flush()
    current = arrangement_for_order(db, order)
    assert current['effective']['startsAt'] == effective['startsAt']
    assert current['status'] == 'NEEDS_COORDINATION'


def test_replacing_proposal_does_not_resurrect_older_pending_suggestion(db, admin_client):
    from app.delivery_plan import (arrangement_for_order, propose_reschedule,
                                   reject_reschedule)
    _, order, actor = paid_order(db, admin_client)
    first = configured_slot(db, offset_hours=40)
    second = configured_slot(db, offset_hours=50)
    original = propose_reschedule(db, actor, order, first, '第一个方案')
    propose_reschedule(db, actor, order, second, '替代方案')
    reject_reschedule(db, Actor(principal_id=order.principal_id), order, '不同意')
    db.flush()
    assert original.status == 'SUPERSEDED'
    assert arrangement_for_order(db, order)['pendingProposal'] is None


@pytest.mark.parametrize('stage', ['DELIVERING', 'DELIVERED', 'CANCELLED'])
def test_acceptance_cannot_cancel_a_started_or_terminal_delivery(db, admin_client, stage):
    from app.delivery_plan import confirm_reschedule, propose_reschedule
    world, order, actor = paid_order(db, admin_client)
    target = configured_slot(db, offset_hours=40)
    propose_reschedule(db, actor, order, target, '配送调整')
    original_assignment = assignment_for(db, order)
    original_assignment.status = 'STARTED' if stage == 'DELIVERING' else stage
    order.status = stage
    with pytest.raises(DomainError):
        confirm_reschedule(db, Actor(principal_id=order.principal_id), order, target.id)
    assert original_assignment.active
    assert order.confirmed_slot_id == world.slot.id


def test_target_assignment_inherits_the_already_assigned_courier(db, admin_client, staff_factory):
    from app.delivery_plan import (confirm_reschedule, propose_reschedule,
                                   group_for_slot_campus, regular_batch_for_group)
    world, order, actor = paid_order(db, admin_client)
    target = configured_slot(db, offset_hours=40)
    group = group_for_slot_campus(db, target, world.campus)
    target_batch = regular_batch_for_group(db, group, target, world.campus)
    courier = staff_factory(role='COURIER')
    target_batch.courier_id = courier.id
    propose_reschedule(db, actor, order, target, '配送调整')
    confirm_reschedule(db, Actor(principal_id=order.principal_id), order, target.id)
    assert assignment_for(db, order).courier_id == courier.id


def test_confirmation_is_a_separate_event_and_preserves_original_payment_window(db, admin_client):
    from app.delivery_plan import confirm_reschedule, propose_reschedule
    _, order, actor = paid_order(db, admin_client)
    initial = dict(order.data['confirmedSlotSnapshot'])
    target = configured_slot(db, offset_hours=40)
    proposal = propose_reschedule(db, actor, order, target, '配送调整')
    confirm_reschedule(db, Actor(principal_id=order.principal_id), order, target.id)
    applied = db.scalar(select(DeliveryChange).where(
        DeliveryChange.order_id == order.id,
        DeliveryChange.kind == 'RESCHEDULE_CONFIRMED'))
    assert applied is not None and applied.id != proposal.id
    assert order.data['settlementSlotSnapshot'] == initial


def test_unopened_target_cannot_receive_a_paid_reschedule(db, admin_client):
    from app.delivery_plan import propose_reschedule
    _, order, actor = paid_order(db, admin_client)
    target = configured_slot(db, offset_hours=40, open_offset=1, cutoff_offset=2)
    with pytest.raises(DomainError) as error:
        propose_reschedule(db, actor, order, target, '尚未开放')
    assert error.value.code == 'SLOT_NOT_OPEN'


def test_auto_batch_rejects_an_assignment_to_another_batch(db, admin_client):
    from app.delivery_plan import add_to_auto_batch
    world, order, actor = paid_order(db, admin_client)
    assignment = assignment_for(db, order)
    other = DeliveryBatch(name='其他手工批次', status='PLANNED',
                          starts_at=world.slot.starts_at, ends_at=world.slot.ends_at)
    db.add(other)
    db.flush()
    assignment.batch_id = other.id
    db.flush()
    with pytest.raises(DomainError) as error:
        add_to_auto_batch(db, actor, order, world.slot, world.campus)
    assert error.value.code == 'BATCH_CONFLICT'


def test_repeated_failed_confirmation_does_not_record_the_same_cash_twice(db, admin_client, api):
    world = make_world(db)
    order = submit(db, world)
    world.slot.cutoff_at = utcnow() - timedelta(seconds=1)
    db.commit()
    body = {'expectedVersion': order.version, 'contentRevision': order.content_revision,
            'quoteId': order.quote_id, 'receivedCents': order.total_cents,
            'settlementKind': 'CASH_MANUAL', 'note': '确已收款'}
    first = api(admin_client, f'/admin/orders/{order.id}/confirm-payment', body)
    assert first.status_code == 409, first.text
    repeated = api(admin_client, f'/admin/orders/{order.id}/confirm-payment', body)
    assert repeated.status_code == 409, repeated.text
    db.expire_all()
    assert db.scalar(select(func.count(Receipt.id)).where(Receipt.order_id == order.id)) == 1
    detail = api(admin_client, f'/admin/orders/{order.id}', method='GET')
    assert len(detail.json()['data']['unmatchedReceipts']) == 1


def test_auto_batch_failure_can_recover_without_permanent_delivery_block(
        db, admin_client, api, monkeypatch):
    import app.delivery_plan as planning
    world = make_world(db)
    order = submit(db, world)
    body = {'expectedVersion': order.version, 'contentRevision': order.content_revision,
            'quoteId': order.quote_id, 'receivedCents': order.total_cents,
            'settlementKind': 'CASH_MANUAL', 'note': '确已收款'}
    actual = planning.add_to_auto_batch
    monkeypatch.setattr(planning, 'add_to_auto_batch', lambda *args: (_ for _ in ()).throw(RuntimeError('fixture')))
    failed = api(admin_client, f'/admin/orders/{order.id}/confirm-payment', body)
    assert failed.status_code == 409, failed.text
    receipt_id = failed.json()['error']['details']['receiptId']
    monkeypatch.setattr(planning, 'add_to_auto_batch', actual)
    result = api(admin_client, f'/admin/orders/{order.id}/confirm-payment',
                 {**body, 'receiptId': receipt_id})
    assert result.status_code == 200, result.text
    db.expire_all()
    assert db.scalar(select(func.count(BusinessException.id)).where(
        BusinessException.order_id == order.id,
        BusinessException.type == 'AUTO_BATCH_FAILED',
        BusinessException.status.in_(('OPEN', 'IN_PROGRESS')))) == 0


def test_unpaid_proposal_must_target_an_accepting_slot_and_support_rejection(db, admin_client, api):
    world = make_world(db)
    order = submit(db, world)
    target = configured_slot(db, offset_hours=40)
    db.commit()
    proposed = api(admin_client, f'/admin/orders/{order.id}/slot-proposals', {
        'expectedVersion': order.version, 'slotId': target.id, 'reason': '协调新时段'})
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()['data']['slotProposal']['proposed']['startsAt']
    from app.commerce import reject_slot, VersionReason
    from app.core import lock
    rejected = reject_slot(order.id, VersionReason(expected_version=proposed.json()['data']['version'],
                          reason='不能接受'), db=db,
                           actor=Actor(principal_id=world.principal.id))
    assert rejected['data']['slotProposal']['rejected'] is True


def test_coupon_failure_preserves_cash_without_partial_auto_assignment(db, admin_client, api, monkeypatch):
    import app.growth as growth
    world = make_world(db)
    order = submit(db, world)
    def frozen(*args):
        raise DomainError('COUPON_FROZEN', '优惠冻结，请先核对', 409)
    monkeypatch.setattr(growth, 'settle_coupon', frozen)
    result = api(admin_client, f'/admin/orders/{order.id}/confirm-payment', {
        'expectedVersion': order.version, 'contentRevision': order.content_revision,
        'quoteId': order.quote_id, 'receivedCents': order.total_cents,
        'settlementKind': 'CASH_MANUAL', 'note': '实际已收款'})
    assert result.status_code == 409, result.text
    db.expire_all()
    assert db.scalar(select(func.count(Receipt.id)).where(Receipt.order_id==order.id)) == 1
    assert assignment_for(db, order) is None
    assert db.get(DeliverySlot, world.slot.id).occupied == 0


def test_natural_cutoff_is_reported_as_sealed_without_a_scheduler(db, admin_client):
    from app.delivery_plan import arrangement_for_order
    world, order, _ = paid_order(db, admin_client)
    world.slot.cutoff_at = utcnow() - timedelta(seconds=1)
    assert arrangement_for_order(db, order)['sealed'] is True


def test_legacy_order_without_snapshots_shows_current_batch_without_inventing_history(db, admin_client):
    from app.delivery_plan import arrangement_for_order
    _, order, _ = paid_order(db, admin_client)
    order.data = {key:value for key,value in order.data.items()
                  if key not in ('requestedSlotSnapshot','confirmedSlotSnapshot','settlementSlotSnapshot')}
    batch=db.get(DeliveryBatch,assignment_for(db,order).batch_id)
    view=arrangement_for_order(db,order)
    assert view['effective']['startsAt']==batch.starts_at.isoformat()
    assert view['effective']['source']=='CURRENT_BATCH'
    assert view['originalRequest'] is None and view['originalConfirmation'] is None


def test_terminal_or_closed_regular_batch_does_not_trap_future_paid_orders(db, admin_client):
    from app.delivery_plan import add_to_auto_batch
    world, order, actor = paid_order(db, admin_client)
    old_assignment = assignment_for(db, order)
    old_batch = db.get(DeliveryBatch, old_assignment.batch_id)
    old_assignment.active=False
    old_assignment.status='CANCELLED'
    old_batch.status='CANCELLED'
    db.flush()
    batch, assignment = add_to_auto_batch(db, actor, order, world.slot, world.campus)
    assert batch.id != old_batch.id and assignment.batch_id == batch.id
    assert not old_batch.is_regular and batch.is_regular


def test_admission_follows_the_actual_public_service_switch(db):
    from app.config import get_config, public_config
    from app.delivery_plan import slot_acceptance
    world=make_world(db)
    announcement=get_config(db,'announcement')
    announcement.data={**announcement.data,'serviceEnabled':False}
    assert public_config(db)['serviceEnabled'] is False
    assert slot_acceptance(db,world.slot)['selectable'] is False


def pending_copy(db, order, suffix):
    second=Order(principal_id=order.principal_id,pickup_code='AUDIT-'+suffix,
        status='PENDING_CONFIRMATION',preparation_id=order.preparation_id,
        quote_id=order.quote_id,total_cents=order.total_cents,
        nickname='并发验收',phone='13800000001',location_id=order.location_id,
        requested_slot_id=order.requested_slot_id,close_at=utcnow()+timedelta(hours=2),data={})
    db.add(second);db.commit()
    return second


def test_later_paid_order_inherits_changed_batch_eta(db, admin_client):
    from app.delivery_plan import arrangement_for_order
    world, first, actor=paid_order(db, admin_client)
    batch=db.get(DeliveryBatch, assignment_for(db, first).batch_id)
    batch.starts_at += timedelta(minutes=20)
    batch.ends_at += timedelta(minutes=20)
    batch.reason='配送时间调整'
    second=pending_copy(db, first, 'ETA')
    settle(db, second, actor)
    assert arrangement_for_order(db, second)['effective']['startsAt'] == batch.starts_at.isoformat()


def run_settlement(session, order_id, staff_id):
    from app.commerce import ConfirmPayment, settle_order
    from app.core import lock
    locked=lock(session,Order,order_id)
    try:
        settle_order(session,Actor(staff_id=staff_id,staff_role='ADMIN',capabilities={'payment.manage'}),
            locked,ConfirmPayment(expected_version=locked.version,content_revision=locked.content_revision,
            quote_id=locked.quote_id,received_cents=locked.total_cents,settlement_kind='CASH_MANUAL'))
        session.commit()
        return 'OK'
    except DomainError as error:
        session.commit() if error.commit else session.rollback()
        return error.code


def test_concurrent_last_capacity_preserves_the_losing_cash(db,admin_client):
    import threading
    from sqlalchemy import text
    from app.db import SessionLocal
    world=make_world(db,capacity=1)
    first=submit(db,world)
    second=pending_copy(db,first,'LAST')
    staff_id=admin_client.staff.id
    barrier=threading.Barrier(2)
    results=[]
    def worker(order_id):
        with SessionLocal() as session:
            session.execute(text("SET LOCAL lock_timeout='4s'"))
            barrier.wait(timeout=5)
            results.append(run_settlement(session,order_id,staff_id))
    threads=[threading.Thread(target=worker,args=(oid,),daemon=True) for oid in (first.id,second.id)]
    for thread in threads:thread.start()
    for thread in threads:thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(results)==['OK','SLOT_CAPACITY_REVIEW_REQUIRED']
    db.expire_all()
    assert db.get(DeliverySlot,world.slot.id).occupied==1
    assert db.scalar(select(func.count(Receipt.id)).where(Receipt.order_id.in_((first.id,second.id))))==2
    assert db.scalar(select(func.count(Receipt.id)).where(Receipt.order_id.in_((first.id,second.id)),Receipt.status=='UNMATCHED'))==1


def test_waiting_for_slot_lock_does_not_keep_a_stale_admission_time(db,admin_client,monkeypatch):
    import threading
    from sqlalchemy import text
    import app.commerce as commerce
    from app.core import lock
    from app.db import SessionLocal
    world=make_world(db)
    order=submit(db,world)
    order_id,slot_id,staff_id=order.id,world.slot.id,admin_client.staff.id
    waiting=threading.Event()
    clock={'now':utcnow()}
    actual_lock=commerce.lock
    def observed_lock(session,model,object_id,*args):
        if model is DeliverySlot:waiting.set()
        return actual_lock(session,model,object_id,*args)
    monkeypatch.setattr(commerce,'lock',observed_lock)
    monkeypatch.setattr(commerce,'utcnow',lambda:clock['now'])
    result=[]
    with SessionLocal() as holder:
        lock(holder,DeliverySlot,slot_id)
        def worker():
            with SessionLocal() as session:
                session.execute(text("SET LOCAL lock_timeout='4s'"))
                result.append(run_settlement(session,order_id,staff_id))
        thread=threading.Thread(target=worker,daemon=True)
        thread.start()
        assert waiting.wait(timeout=5)
        clock['now']=world.slot.cutoff_at+timedelta(seconds=1)
        holder.commit()
        thread.join(timeout=10)
    assert not thread.is_alive()
    assert result==['SLOT_CUTOFF']
    db.expire_all()
    assert db.get(DeliverySlot,slot_id).occupied==0


def test_sealing_and_settlement_have_one_serial_order(db,admin_client):
    import threading
    from sqlalchemy import text
    from app.core import lock
    from app.db import SessionLocal
    from app.delivery_plan import apply_seal
    world=make_world(db)
    order=submit(db,world)
    order_id,slot_id,staff_id=order.id,world.slot.id,admin_client.staff.id
    barrier=threading.Barrier(2)
    result=[]
    def pay():
        with SessionLocal() as session:
            session.execute(text("SET LOCAL lock_timeout='4s'"))
            barrier.wait(timeout=5)
            result.append(run_settlement(session,order_id,staff_id))
    def seal():
        with SessionLocal() as session:
            session.execute(text("SET LOCAL lock_timeout='4s'"))
            barrier.wait(timeout=5)
            slot=lock(session,DeliverySlot,slot_id)
            apply_seal(session,slot,Actor(staff_id=staff_id,staff_role='ADMIN'), '并发封批验收')
            session.commit()
    threads=[threading.Thread(target=fn,daemon=True) for fn in (pay,seal)]
    for thread in threads:thread.start()
    for thread in threads:thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert result in (['OK'],['SLOT_SEALED'])
    db.expire_all()
    assert db.get(DeliverySlot,slot_id).sealed_at is not None
    assignment=assignment_for(db,db.get(Order,order_id))
    if assignment:
        assert db.get(DeliveryBatch,assignment.batch_id).sealed_at is not None
    assert db.get(DeliverySlot,slot_id).occupied==int(result==['OK'])



@pytest.mark.parametrize('settled', [False, True])
def test_admin_changes_slot_directly_and_releases_old_slot(db, admin_client, api, settled):
    from app.commerce import settle_order, ConfirmPayment
    from app.delivery import admin_slot_update, admin_slot_delete
    from app.delivery_schemas import SlotUpdate, to_shanghai_date
    from app.delivery_plan import propose_reschedule

    world = make_world(db)
    order = submit(db, world)
    actor = admin_client_actor(admin_client.staff)
    source = world.slot
    target = configured_slot(db, offset_hours=44, campus=world.campus)
    if settled:
        settle(db, order, actor)
        previous_assignment = assignment_for(db, order)
        original = dict(order.data['confirmedSlotSnapshot'])
        legacy = configured_slot(db, offset_hours=48, campus=world.campus)
        pending = propose_reschedule(db, actor, order, legacy, '旧建议')
    total = order.total_cents
    db.commit()
    result = api(admin_client, f'/admin/orders/{order.id}/slot-change', {
        'expectedVersion': order.version, 'slotId': target.id, 'reason': '已在微信协调'})
    assert result.status_code == 200, result.text
    db.expire_all()
    updated = result.json()['data']
    assert updated['arrangement']['effective']['id'] == target.id
    assert updated['arrangement']['pendingProposal'] is None
    assert updated['slotProposal'] is None
    assert order.total_cents == total
    assert source.occupied == 0
    if settled:
        assert target.occupied == 1
        assert updated['arrangement']['originalConfirmation'] == original
        assert not previous_assignment.active
        assert pending.status == 'SUPERSEDED'
        assert db.get(DeliveryBatch, assignment_for(db, order).batch_id).slot_id == target.id
    else:
        assert target.occupied == 0
        assert order.requested_slot_id == target.id
        settle_order(db, actor, order, ConfirmPayment(
            expected_version=order.version, content_revision=order.content_revision,
            quote_id=order.quote_id, received_cents=total, settlement_kind='CASH_MANUAL'))
        assert order.confirmed_slot_id == target.id
    cutoff = source.cutoff_at - timedelta(minutes=1)
    new_date = to_shanghai_date(source.starts_at + timedelta(days=1))
    edited = admin_slot_update(source.id, SlotUpdate(expected_version=source.version,
        date=new_date, start='15:00', end='16:00',
        open_at=source.open_at, cutoff_at=cutoff), db=db, actor=actor)
    assert edited['data']['pendingApplicationCount'] == 0
    assert edited['data']['batchCount'] == 0
    if settled:
        vacated_batch = db.get(DeliveryBatch, previous_assignment.batch_id)
        assert vacated_batch.starts_at == source.starts_at
        assert vacated_batch.name.startswith(new_date)
    assert admin_slot_delete(source.id, db=db, actor=actor)['data']['archived']


def test_direct_change_uses_target_batch_eta_and_cancels_empty_published_source(
        db, admin_client, api):
    from app.delivery_plan import group_for_slot_campus, regular_batch_for_group
    from app.models import EmailJob

    world, order, _ = paid_order(db, admin_client)
    source = db.get(DeliveryBatch, assignment_for(db, order).batch_id)
    source.status = 'PUBLISHED'
    email = EmailJob(template_key='COURIER_DISPATCH', status='QUEUED',
                     business_key=f'courier-dispatch:{source.id}:1',
                     protected_payload='queued', expires_at=utcnow() + timedelta(hours=1))
    target = configured_slot(db, offset_hours=44, campus=world.campus)
    group = group_for_slot_campus(db, target, world.campus)
    batch = regular_batch_for_group(db, group, target, world.campus)
    batch.starts_at += timedelta(minutes=15)
    batch.ends_at += timedelta(minutes=15)
    db.add(email)
    db.commit()
    response = api(admin_client, f'/admin/orders/{order.id}/slot-change', {
        'expectedVersion': order.version, 'slotId': target.id, 'reason': '已协调更改时间'})
    assert response.status_code == 200, response.text
    db.expire_all()
    assert source.status == 'CANCELLED'
    assert email.status == 'CANCELLED'
    assert assignment_for(db, order).batch_id == batch.id
    arrangement = response.json()['data']['arrangement']
    assert arrangement['effective']['startsAt'] == batch.starts_at.isoformat()
    assert arrangement['confirmed']['startsAt'] == target.starts_at.isoformat()


def test_direct_change_rejects_open_delivery_exception_without_moving_facts(db, admin_client, api):
    world, order, _ = paid_order(db, admin_client)
    target = configured_slot(db, offset_hours=44, campus=world.campus)
    assignment = assignment_for(db, order)
    assignment.status = 'EXCEPTION'
    order.status = 'DELIVERY_EXCEPTION'
    db.commit()
    response = api(admin_client, f'/admin/orders/{order.id}/slot-change', {
        'expectedVersion': order.version, 'slotId': target.id, 'reason': '已协调更改时间'})
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'DELIVERY_EXCEPTION_OPEN'
    db.expire_all()
    assert assignment.active and assignment.status == 'EXCEPTION'
    assert order.confirmed_slot_id == world.slot.id
    assert world.slot.occupied == 1 and target.occupied == 0


def test_admin_can_keep_current_request_and_discard_legacy_proposal(db, admin_client, api):
    world = make_world(db)
    order = submit(db, world)
    target = configured_slot(db, offset_hours=44, campus=world.campus)
    order.data = {**order.data, 'slotProposal': {'slotId': target.id, 'confirmed': False}}
    db.commit()
    result = api(admin_client, f'/admin/orders/{order.id}/slot-change', {
        'expectedVersion': order.version, 'slotId': world.slot.id, 'reason': '微信确认保留原时段'})
    assert result.status_code == 200, result.text
    assert result.json()['data']['slotProposal'] is None
    db.refresh(order)
    settle(db, order, admin_client_actor(admin_client.staff))
    assert order.confirmed_slot_id == world.slot.id
    assert world.slot.occupied == 1 and target.occupied == 0
