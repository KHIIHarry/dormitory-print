from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import select

from app.models import Asset, AuditLog, BusinessException, DeliveryAssignment, DeliveryBatch, DeliveryProof, EmailJob, Order, utcnow
from test_delivery import add_orders, create_batch, make_courier, make_ready_order, publish_batch, start_batch
from test_delivery_closed_loop import APP_MODULES, act_as, admin_client_actor, make_world, settle, submit


@pytest.mark.parametrize('scenario', ['assigned', 'unassigned', 'legacy_slot', 'expired', 'disabled', 'empty'])
def test_auto_publication_at_cutoff_is_once_and_withdrawn_batch_stays_manual(
        db, api, client, act_as, staff_factory, scenario):
    from app import jobs

    world = make_world(db)
    admin = act_as(staff_factory(role='ADMIN'))
    order = submit(db, world)
    settle(db, order, admin_client_actor(admin))
    slot = world.slot
    slot_path = f'/admin/slots/{slot.id}'
    batch = db.scalar(select(DeliveryBatch).where(DeliveryBatch.slot_id == slot.id))
    path = f'/admin/batches/{batch.id}'
    courier = make_courier(staff_factory)
    if scenario != 'unassigned':
        response = api(client, path, {'expectedVersion': batch.version, 'courierId': courier.id}, method='PATCH')
        assert response.status_code == 200, response.text
    if scenario != 'disabled':
        enabled = api(client, slot_path, {'expectedVersion': slot.version, 'autoPublish': True}, method='PATCH')
        assert enabled.status_code == 200 and enabled.json()['data']['autoPublish'] is True, enabled.text
    assert jobs.publish_due_delivery_batches.run()['published'] == 0
    db.refresh(slot)
    slot.open_at = utcnow() - timedelta(hours=1)
    slot.cutoff_at = utcnow() - timedelta(seconds=1)
    if scenario == 'expired':
        slot.open_at = utcnow() - timedelta(hours=4)
        slot.cutoff_at = utcnow() - timedelta(hours=3)
        slot.starts_at = utcnow() - timedelta(hours=2)
        slot.ends_at = utcnow() - timedelta(hours=1)
    if scenario == 'legacy_slot':
        db.refresh(batch)
        batch.slot_id = None
        batch.data = {key: value for key, value in batch.data.items() if key not in ('slotId', 'slot_id')}
        batch.data = {**batch.data, 'slotSnapshot': {'id': slot.id}}
    if scenario == 'empty':
        assignment = db.scalars(select(DeliveryAssignment).where(DeliveryAssignment.batch_id == batch.id)).one()
        assignment.active = False
    db.commit()

    result = jobs.publish_due_delivery_batches.run()
    if scenario == 'empty':
        assert result['published'] == result['blocked'] == 0
        current = api(client, path, method='GET').json()['data']
        assert current['status'] == 'PLANNED' and current['autoPublishBlockedReason'] is None
        return
    if scenario == 'disabled':
        assert result['published'] == result['blocked'] == 0
        rejected = api(client, slot_path, {'expectedVersion': slot.version, 'autoPublish': True}, method='PATCH')
        assert rejected.status_code == 422 and rejected.json()['error']['code'] == 'AUTO_PUBLISH_CUTOFF_PASSED'
        return
    assert result['blocked' if scenario in ('unassigned', 'expired') else 'published'] == 1
    assert jobs.publish_due_delivery_batches.run()['published'] == 0
    current = api(client, path, None, method='GET').json()['data']
    if scenario == 'expired':
        assert current['status'] == 'PLANNED' and '配送窗口已结束' in current['autoPublishBlockedReason']
        return
    if scenario == 'unassigned':
        assert current['status'] == 'PLANNED'
        assert '配送员' in current['autoPublishBlockedReason']
        assigned_late = api(client, path, {'expectedVersion': current['version'], 'courierId': courier.id}, method='PATCH')
        assert assigned_late.status_code == 200, assigned_late.text
        assert jobs.publish_due_delivery_batches.run()['published'] == 0
        assert publish_batch(api, client, batch.id, assigned_late.json()['data']['version'])['status'] == 'PUBLISHED'
        return

    assert current['status'] == 'PUBLISHED' and current['publishedAutomatically'] is True
    assert current['orderIds'] == [order.id] and 'WITHDRAW_PUBLICATION' in current['allowedActions']
    withdrawn = api(client, path + '/withdraw-publication', {'expectedVersion': current['version']})
    assert withdrawn.status_code == 200, withdrawn.text
    current = withdrawn.json()['data']
    assert current['status'] == 'PLANNED' and current['courierId'] == courier.id
    assert current['orderIds'] == [order.id] and current['publicationWithdrawnAt']
    assert jobs.publish_due_delivery_batches.run()['published'] == 0
    republished = publish_batch(api, client, batch.id, current['version'])
    assert republished['publishedAutomatically'] is False
    db.expire_all()
    logs = list(db.scalars(select(AuditLog).where(
        AuditLog.target_id == batch.id, AuditLog.action == 'BATCH_PUBLISHED')))
    assert len(logs) == 2


def _started_batch(db, api, client, act_as, staff_factory, *, extra_order=False):
    act_as(staff_factory(role='ADMIN'))
    courier = make_courier(staff_factory)
    order = make_ready_order(db)
    batch = create_batch(api, client, courier)
    order_ids = [order.id]
    if extra_order:
        order_ids.append(make_ready_order(db).id)
    add_orders(api, client, batch, order_ids)
    published = publish_batch(api, client, batch['id'])
    started = start_batch(api, client, batch['id'], published['version'])
    assert started.status_code == 200, started.text
    db.expire_all()
    assignment = db.scalar(select(DeliveryAssignment).where(DeliveryAssignment.order_id == order.id))
    return started.json()['data']['batches'][0], order, assignment


def _proof_payload(order, assignment):
    return {'expectedVersion': order.version, 'deliveryAssignmentId': assignment.id,
            'skipPhotoReason': '测试免拍', 'skipLocationReason': '测试免定位',
            'locationFailureCode': 'POSITION_UNAVAILABLE'}


def test_withdraw_started_requires_confirmation_and_blocks_stale_proof(db, api, client, act_as, staff_factory):
    batch, order, assignment = _started_batch(db, api, client, act_as, staff_factory)
    path = f"/admin/batches/{batch['id']}/withdraw-publication"
    payload = {'expectedVersion': batch['version']}
    old_proof = _proof_payload(order, assignment)
    assert api(client, path, payload).status_code == 422
    queued = EmailJob(template_key='COURIER_DISPATCH', business_key=f"courier-dispatch:{batch['id']}:2",
                      status='QUEUED', protected_payload='test-payload', expires_at=utcnow() + timedelta(hours=1))
    sent = EmailJob(template_key='COURIER_DISPATCH', business_key=f"courier-dispatch:{batch['id']}:1",
                    status='SMTP_ACCEPTED', expires_at=utcnow() + timedelta(hours=1))
    old_photo = Asset(purpose='DELIVERY_PROOF', assignment_id=assignment.id, staff_id=batch['courierId'],
                      name='old.jpg', mime='image/jpeg', size=10, sha256='a' * 64,
                      storage_key=f'delivery/{assignment.id}.jpg', status='READY')
    location_request = BusinessException(order_id=order.id, type='LOCATION_EXCEPTION', status='OPEN',
                                         reason='设备定位不可用', blocks_production=False, blocks_delivery=False,
                                         data={'assignmentId': assignment.id, 'assignmentVersion': assignment.version})
    db.add_all([queued, sent, old_photo, location_request])
    db.commit()
    withdrawn = api(client, path, {**payload, 'confirmNoHandover': True})
    assert withdrawn.status_code == 200, withdrawn.text
    db.expire_all()
    assert db.get(Order, order.id).status == 'READY_FOR_DELIVERY'
    assert queued.status == 'CANCELLED' and queued.protected_payload is None
    assert sent.status == 'SMTP_ACCEPTED'
    assert api(client, '/admin/exceptions?status=OPEN', None, method='GET').json()['data']['items'] == []
    assert api(client, f'/courier/orders/{order.id}/proof', old_proof).status_code == 409
    republished = publish_batch(api, client, batch['id'], withdrawn.json()['data']['version'])
    assert start_batch(api, client, batch['id'], republished['version']).status_code == 200
    db.expire_all()
    new_assignment = db.scalar(select(DeliveryAssignment).where(
        DeliveryAssignment.order_id == order.id, DeliveryAssignment.active.is_(True)))
    reused_photo = {**_proof_payload(order, new_assignment), 'photoAssetId': old_photo.id, 'skipPhotoReason': None}
    rejected = api(client, f'/courier/orders/{order.id}/proof', reused_photo)
    assert rejected.status_code == 403 and rejected.json()['error']['code'] == 'PHOTO_NOT_IN_SCOPE'
    assert not assignment.active and assignment.status == 'CANCELLED'
    assert new_assignment.courier_id == assignment.courier_id


def test_one_successful_order_prevents_withdrawal_even_with_more_orders_pending(db, api, client, act_as, staff_factory):
    batch, order, assignment = _started_batch(db, api, client, act_as, staff_factory, extra_order=True)
    proof = api(client, f'/courier/orders/{order.id}/proof', _proof_payload(order, assignment))
    assert proof.status_code == 200, proof.text
    path = f"/admin/batches/{batch['id']}"
    current = api(client, path, None, method='GET').json()['data']
    assert current['status'] == 'DELIVERING' and 'WITHDRAW_PUBLICATION' not in current['allowedActions']
    rejected = api(client, path + '/withdraw-publication',
                   {'expectedVersion': current['version'], 'confirmNoHandover': True})
    assert rejected.status_code == 409 and rejected.json()['error']['code'] == 'BATCH_HAS_DELIVERED'


def test_withdraw_and_successful_proof_cannot_both_commit(db, api, client, act_as, staff_factory):
    batch, order, assignment = _started_batch(db, api, client, act_as, staff_factory)
    barrier = Barrier(2)

    def post(path, body):
        barrier.wait(timeout=5)
        return api(client, path, body)

    with ThreadPoolExecutor(max_workers=2) as pool:
        withdraw = pool.submit(post, f"/admin/batches/{batch['id']}/withdraw-publication",
                               {'expectedVersion': batch['version'], 'confirmNoHandover': True})
        proof = pool.submit(post, f'/courier/orders/{order.id}/proof', _proof_payload(order, assignment))
        withdrawal, completion = withdraw.result(timeout=15), proof.result(timeout=15)
    assert sorted([withdrawal.status_code, completion.status_code]) == [200, 409], (
        withdrawal.text, completion.text)
    db.expire_all()
    proof_row = db.scalar(select(DeliveryProof).where(DeliveryProof.assignment_id == assignment.id))
    if completion.status_code == 200:
        assert proof_row is not None and order.status == 'DELIVERED'
        assert db.get(DeliveryBatch, batch['id']).status == 'COMPLETED'
    else:
        assert proof_row is None and order.status == 'READY_FOR_DELIVERY'
        assert db.get(DeliveryBatch, batch['id']).status == 'PLANNED'
