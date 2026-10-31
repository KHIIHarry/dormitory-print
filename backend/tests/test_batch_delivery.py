"""Atomic batch delivery proofs over one shared, courier-owned photo."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select, text

from app.core import Actor, DomainError, token_hash, touch
from app.db import SessionLocal
from app.delivery import _lock_order_delivery, batch_start, courier_proof, refresh_batch
from app.delivery_schemas import BatchVersionInput, DeliveryProofInput, PositionInput
from app.models import (
    Account,
    Asset,
    AuthSession,
    BusinessException,
    ConfigSection,
    DeliveryAssignment,
    DeliveryBatch,
    DeliveryProof,
    EmailJob,
    Location,
    Order,
    Referral,
    Staff,
    WageEntry,
    utcnow,
)
from tests.test_delivery import (
    add_orders,
    create_batch,
    make_courier,
    make_ready_order,
    publish_batch,
    start_batch,
)

APP_MODULES = ['delivery', 'production', 'auth', 'growth', 'mail', 'media', 'maintenance']


def _staff_client(client_factory, db, staff):
    """Authenticate through the unified customer-account cookie (FINAL N05)."""
    account = db.get(Account, staff.account_id)
    token = uuid4().hex
    db.add(AuthSession(kind='CUSTOMER', account_id=account.id,
                       principal_id=account.principal_id, session_version=account.session_version,
                       token_hash=token_hash(token),
                       expires_at=utcnow() + timedelta(days=7)))
    db.commit()
    client = client_factory()
    client.cookies.set('dormprint_account_session', token)
    client.staff = staff
    client.account = account
    return client


def _customer_client(client_factory, db, account):
    token = uuid4().hex
    db.add(AuthSession(kind='CUSTOMER', account_id=account.id,
                       principal_id=account.principal_id, token_hash=token_hash(token),
                       expires_at=utcnow() + timedelta(days=30)))
    db.commit()
    client = client_factory()
    client.cookies.set('dormprint_account_session', token)
    client.account = account
    return client


def _proof_asset(db, courier, assignment, *, purpose='DELIVERY_PROOF', owner=None,
                 anchor=True, key=None):
    from app.media import _root

    key = key or f'proof/{uuid4().hex}.jpg'
    path = _root() / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'\xff\xd8\xff\xe0' + b'0' * 64)
    asset = Asset(purpose=purpose, assignment_id=assignment.id if anchor else None,
                  staff_id=owner or courier.id, name=f'proof-{uuid4().hex}.jpg',
                  mime='image/jpeg', size=path.stat().st_size, sha256='9' * 64,
                  storage_key=key, status='READY',
                  data={'kind': 'JPEG', 'intakeValidated': True,
                        'uploadedAt': utcnow().isoformat()})
    db.add(asset)
    db.commit()
    return asset


def _batch(db, api, admin_client, courier, orders, *, building_name='一号楼'):
    if building_name is not None and orders:
        location = Location(name=building_name, type='DORMITORY', x=0.4, y=0.5,
                            enabled=True, map_version=1)
        db.add(location)
        db.flush()
        for order in orders:
            order.location_id = location.id
        db.commit()
    # Use explicit instants so the window never straddles Shanghai midnight.
    starts_at = utcnow() - timedelta(hours=1)
    created = api(admin_client, '/admin/batches', {
        'name': f'批量凭证批次 {uuid4().hex[:6]}',
        'startsAt': starts_at.isoformat(),
        'endsAt': (starts_at + timedelta(hours=3)).isoformat(),
        'courierId': courier.id,
        'reason': '',
    })
    assert created.status_code == 200, created.text
    batch = created.json()['data']
    add_orders(api, admin_client, batch, [order.id for order in orders])
    published = publish_batch(api, admin_client, batch['id'])
    assert start_batch(api, admin_client, batch['id'], published['version']).status_code == 200
    assignments = []
    for order in orders:
        db.refresh(order)
        assignment = db.scalar(select(DeliveryAssignment).where(
            DeliveryAssignment.order_id == order.id,
            DeliveryAssignment.active.is_(True)))
        assert assignment is not None
        assignments.append(assignment)
    return batch, assignments


def _body(orders, assignments, photo, *, versions=None, positions=None,
          skip_photo_reason=None, placement_note='一楼收发台'):
    items = []
    for index, (order, assignment) in enumerate(zip(orders, assignments)):
        item = {
            'orderId': order.id,
            'deliveryAssignmentId': assignment.id,
            'expectedVersion': (versions or {}).get(order.id, order.version),
            'expectedAssignmentVersion': assignment.version,
            'position': (positions or {}).get(order.id, {
                'latitude': 31.2304,
                'longitude': 121.4737,
                'accuracyMeters': 12,
                'capturedAt': (utcnow() - timedelta(seconds=15)).isoformat(),
                'coordinateSystem': 'WGS84',
            }),
            'locationFailureCode': None,
            'skipLocationReason': None,
        }
        items.append(item)
    return {
        'photoAssetId': photo.id if photo is not None else None,
        'skipPhotoReason': skip_photo_reason,
        'placementNote': placement_note,
        'items': items,
    }


def _post(api, client, batch, body, key=None):
    return api(client, f'/courier/batches/{batch["id"]}/delivery-proofs/batch',
               body, key=key)


def test_batch_proof_is_atomic_idempotent_and_keeps_one_wage_and_referral(
        db, api, admin_client, client_factory, account_factory, staff_factory):
    courier = make_courier(staff_factory)
    first = make_ready_order(db)
    second = make_ready_order(db)
    batch, assignments = _batch(db, api, admin_client, courier, [first, second])
    photo = _proof_asset(db, courier, assignments[0])

    invitee = account_factory()
    inviter = account_factory()
    first.principal_id = invitee.principal_id
    first.checkout_account_id = invitee.id
    first.data = {**(first.data or {}), 'quote': {'lines': [
        {'service': 'PRINT', 'amountCents': first.total_cents}]}}
    relation = Referral(inviter_id=inviter.id, invitee_id=invitee.id,
                        status='WAITING_FIRST_ORDER',
                        data={'qualificationMinSpendCents': 100,
                              'qualificationService': 'ALL',
                              'qualificationExpiresAt': None})
    db.add(relation)
    db.commit()
    db.refresh(first)

    client = _staff_client(client_factory, db, courier)
    body = _body([first, second], assignments, photo)
    key = 'batch-proof-' + uuid4().hex
    response = _post(api, client, batch, body, key=key)
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['batchId'] == batch['id']
    assert len(data['proofs']) == 2
    assert {proof['photoAssetId'] for proof in data['proofs']} == {photo.id}
    assert {proof['orderId'] for proof in data['proofs']} == {first.id, second.id}
    assert all(proof['placementNote'] == '一楼收发台' for proof in data['proofs'])
    assert {order['status'] for order in data['orders']} == {'DELIVERED'}

    db.expire_all()
    assert db.get(DeliveryBatch, batch['id']).status == 'COMPLETED'
    assert db.scalar(select(func.count(DeliveryProof.id)).where(
        DeliveryProof.photo_asset_id == photo.id)) == 2
    assert db.scalar(select(func.count(WageEntry.id)).where(
        WageEntry.source_key.in_([f'DELIVERY:{first.id}', f'DELIVERY:{second.id}']))) == 2
    db.refresh(relation)
    assert relation.qualified_order_id == first.id

    replay = _post(api, client, batch, body, key=key)
    assert replay.status_code == 200, replay.text
    assert replay.json()['data']['proofs'] == data['proofs']
    db.expire_all()
    assert db.scalar(select(func.count(DeliveryProof.id)).where(
        DeliveryProof.photo_asset_id == photo.id)) == 2
    assert db.scalar(select(func.count(WageEntry.id)).where(
        WageEntry.order_id.in_([first.id, second.id]))) == 2
    assert db.scalar(select(func.count(Referral.id)).where(
        Referral.invitee_id == invitee.id, Referral.qualified_order_id.is_not(None))) == 1


def test_batch_proof_rolls_back_every_order_on_one_stale_version(
        db, api, admin_client, client_factory, staff_factory):
    courier = make_courier(staff_factory)
    first = make_ready_order(db)
    second = make_ready_order(db)
    batch, assignments = _batch(db, api, admin_client, courier, [first, second])
    photo = _proof_asset(db, courier, assignments[0])
    client = _staff_client(client_factory, db, courier)

    body = _body([first, second], assignments, photo,
                 versions={second.id: second.version + 1})
    response = _post(api, client, batch, body)
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'VERSION_CONFLICT'
    db.expire_all()
    assert db.scalar(select(func.count(DeliveryProof.id))) == 0
    assert db.get(type(first), first.id).status == 'DELIVERING'
    assert db.get(type(first), second.id).status == 'DELIVERING'
    assert db.scalar(select(func.count(WageEntry.id))) == 0
    assert db.get(DeliveryBatch, batch['id']).status == 'DELIVERING'


def test_batch_proof_photo_scope_shared_read_and_cross_courier(
        db, api, admin_client, client_factory, staff_factory, account_factory):
    courier = make_courier(staff_factory)
    foreign = make_courier(staff_factory)
    first = make_ready_order(db)
    second = make_ready_order(db)
    third = make_ready_order(db)
    batch, assignments = _batch(db, api, admin_client, courier, [first, second, third])
    photo = _proof_asset(db, courier, assignments[0])

    owner_a = account_factory()
    owner_b = account_factory()
    owner_c = account_factory()
    first.principal_id = owner_a.principal_id
    second.principal_id = owner_b.principal_id
    third.principal_id = owner_c.principal_id
    db.commit()
    db.refresh(first)
    db.refresh(second)
    db.refresh(third)

    client = _staff_client(client_factory, db, courier)
    body = _body([first, second], assignments[:2], photo)
    response = _post(api, client, batch, body)
    assert response.status_code == 200, response.text

    # Shared photo is readable by each selected owner through its own proof link.
    for account in (owner_a, owner_b):
        reader = _customer_client(client_factory, db, account)
        asset = reader.get(f'/api/v1/assets/{photo.id}')
        assert asset.status_code == 200, asset.text
        content = reader.get(f'/api/v1/assets/{photo.id}/content')
        assert content.status_code == 200, content.text
    stranger = _customer_client(client_factory, db, owner_c)
    assert stranger.get(f'/api/v1/assets/{photo.id}').status_code == 404

    # Negative photo-scope checks need undelivered orders (the first selection is
    # already delivered, so reuse would correctly fail the transition guard first).
    neg_first = make_ready_order(db)
    neg_second = make_ready_order(db)
    neg_third = make_ready_order(db)
    neg_batch, neg_assignments = _batch(
        db, api, admin_client, courier, [neg_first, neg_second, neg_third])

    # A photo anchored to an assignment outside the selected batch selection is rejected.
    other = _proof_asset(db, courier, neg_assignments[2])
    outside = _body([neg_first, neg_second], neg_assignments[:2], other)
    denied = _post(api, client, neg_batch, outside)
    assert denied.status_code == 403, denied.text
    assert denied.json()['error']['code'] == 'PHOTO_NOT_IN_SCOPE'

    # A photo owned by another courier cannot be borrowed even inside the batch.
    foreign_photo = _proof_asset(db, courier, neg_assignments[0], owner=foreign.id)
    foreign_attempt = _body([neg_first, neg_second], neg_assignments[:2], foreign_photo)
    denied = _post(api, client, neg_batch, foreign_attempt)
    assert denied.status_code == 403, denied.text
    assert denied.json()['error']['code'] == 'PHOTO_NOT_IN_SCOPE'
    db.expire_all()
    assert db.scalar(select(func.count(DeliveryProof.id)).where(
        DeliveryProof.photo_asset_id.in_([other.id, foreign_photo.id]))) == 0


def test_batch_proof_rejects_mixed_building_and_mixed_batch(
        db, api, admin_client, client_factory, staff_factory):
    courier = make_courier(staff_factory)
    first = make_ready_order(db)
    second = make_ready_order(db)
    batch, assignments = _batch(db, api, admin_client, courier, [first, second])
    # Move one order to a different building before proof; the assignment snapshot
    # from batch start still names the original building, so update both snapshots.
    other = Location(name='二号楼', type='DORMITORY', x=0.8, y=0.2,
                     enabled=True, map_version=1)
    db.add(other)
    db.flush()
    second.location_id = other.id
    assignments[1].data = {**(assignments[1].data or {}),
                           'locationSnapshot': {'locationId': other.id,
                                                'name': other.name}}
    db.commit()
    photo = _proof_asset(db, courier, assignments[0])
    client = _staff_client(client_factory, db, courier)
    body = _body([first, second], assignments, photo)
    response = _post(api, client, batch, body)
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'BUILDING_MISMATCH'

    # Restore the same building and prove a foreign batch id is rejected.
    assignments[1].data['locationSnapshot']['locationId'] = assignments[0].data['locationSnapshot']['locationId']
    assignments[1].data['locationSnapshot']['name'] = assignments[0].data['locationSnapshot']['name']
    db.commit()
    other_batch, other_assignments = _batch(
        db, api, admin_client, courier, [make_ready_order(db)])
    mismatch = _body([first, second], assignments, photo)
    mismatch['items'][1]['deliveryAssignmentId'] = other_assignments[0].id
    response = _post(api, client, batch, mismatch)
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] in ('BATCH_MISMATCH', 'ASSIGNMENT_MISMATCH')
    db.expire_all()
    assert db.scalar(select(func.count(DeliveryProof.id))) == 0


def test_batch_proof_requires_position_or_approved_exception_for_each_order(
        db, api, admin_client, client_factory, staff_factory):
    courier = make_courier(staff_factory)
    first = make_ready_order(db)
    second = make_ready_order(db)
    batch, assignments = _batch(db, api, admin_client, courier, [first, second])
    photo = _proof_asset(db, courier, assignments[0])
    db.add(BusinessException(
        order_id=first.id, type='LOCATION_EXCEPTION', status='APPROVED',
        reason='设备定位不可用', data={'assignmentId': assignments[0].id,
                                      'assignmentVersion': assignments[0].version}))
    db.commit()
    client = _staff_client(client_factory, db, courier)
    body = _body([first, second], assignments, photo)
    body['items'][0].pop('position')
    body['items'][1].pop('position')
    response = _post(api, client, batch, body)
    assert response.status_code == 422, response.text
    details = response.json()['error']['details']
    assert details['orderId'] == second.id
    assert 'LOCATION_REQUIRED' in details['blockers']

    # The per-order approved exception satisfies the other item; position satisfies this one.
    body['items'][1]['position'] = {
        'latitude': 31.2304, 'longitude': 121.4737, 'accuracyMeters': 10,
        'capturedAt': (utcnow() - timedelta(seconds=10)).isoformat(),
        'coordinateSystem': 'WGS84',
    }
    response = _post(api, client, batch, body)
    assert response.status_code == 200, response.text
    proofs = {proof['orderId']: proof for proof in response.json()['data']['proofs']}
    assert proofs[first.id]['locationExceptionId'] is not None
    assert proofs[second.id]['position'] is not None


def test_shared_proof_asset_stays_in_trash_guard_until_all_proof_refs_close(
        db, api, admin_client, client_factory, staff_factory):
    courier = make_courier(staff_factory)
    first = make_ready_order(db)
    second = make_ready_order(db)
    batch, assignments = _batch(db, api, admin_client, courier, [first, second])
    photo = _proof_asset(db, courier, assignments[0])
    client = _staff_client(client_factory, db, courier)
    assert _post(api, client, batch, _body([first, second], assignments, photo)).status_code == 200

    denied = api(admin_client, '/admin/trash', {
        'targetType': 'ASSET', 'targetId': photo.id,
        'expectedVersion': photo.version, 'reason': '清理测试照片',
    })
    assert denied.status_code == 409, denied.text
    assert 'ASSET_IN_USE' in denied.json()['error']['details']['blockers']

    for proof in db.scalars(select(DeliveryProof).where(
            DeliveryProof.photo_asset_id == photo.id)):
        db.delete(proof)
    db.commit()
    allowed = api(admin_client, '/admin/trash', {
        'targetType': 'ASSET', 'targetId': photo.id,
        'expectedVersion': photo.version, 'reason': '引用已关闭',
    })
    assert allowed.status_code == 200, allowed.text


def test_eligible_admin_can_be_assigned_as_courier_and_earns_own_wage(
        db, api, client_factory, staff_factory):
    admin = staff_factory(role='ADMIN', capabilities=['delivery.manage'])
    admin_client = _staff_client(client_factory, db, admin)
    order = make_ready_order(db)
    batch, assignments = _batch(db, api, admin_client, admin, [order])
    photo = _proof_asset(db, admin, assignments[0])
    response = _post(api, admin_client, batch,
                     _body([order], assignments, photo))
    assert response.status_code == 200, response.text
    wage = db.scalar(select(WageEntry).where(WageEntry.order_id == order.id))
    assert wage is not None and wage.staff_id == admin.id
    proof = db.scalar(select(DeliveryProof).where(DeliveryProof.order_id == order.id))
    assert proof is not None
    assert proof.data['courierId'] == admin.id
    assert proof.data['operatorId'] == admin.id


def test_admin_on_behalf_proof_keeps_wage_with_assigned_courier(
        db, api, admin_client, client_factory, staff_factory):
    courier = make_courier(staff_factory)
    order = make_ready_order(db)
    batch, assignments = _batch(db, api, admin_client, courier, [order])
    photo = _proof_asset(db, courier, assignments[0])
    response = _post(api, admin_client, batch,
                     _body([order], assignments, photo))
    assert response.status_code == 200, response.text
    wage = db.scalar(select(WageEntry).where(WageEntry.order_id == order.id))
    assert wage is not None and wage.staff_id == courier.id
    proof = db.scalar(select(DeliveryProof).where(DeliveryProof.order_id == order.id))
    assert proof is not None
    assert proof.data['courierId'] == courier.id
    assert proof.data['operatorId'] == admin_client.staff.id


def test_mail_only_admin_cannot_be_assigned_as_courier(
        api, db, admin_client, staff_factory):
    mail_admin = staff_factory(role='ADMIN', capabilities=['mail.manage'])
    starts_at = utcnow() - timedelta(hours=1)
    response = api(admin_client, '/admin/batches', {
        'name': '不合资格配送员',
        'startsAt': starts_at.isoformat(),
        'endsAt': (starts_at + timedelta(hours=2)).isoformat(),
        'courierId': mail_admin.id,
        'reason': '',
    })
    assert response.status_code == 422, response.text
    assert response.json()['error']['code'] == 'COURIER_INVALID'


def test_order_and_batch_views_expose_only_exact_approved_location_exception(
        db, api, admin_client, client_factory, staff_factory):
    courier = make_courier(staff_factory)
    courier_client = _staff_client(client_factory, db, courier)
    first = make_ready_order(db)
    second = make_ready_order(db)
    batch, assignments = _batch(db, api, admin_client, courier, [first, second])
    first_assignment, second_assignment = assignments

    approved = BusinessException(
        order_id=first.id, type='LOCATION_EXCEPTION', status='APPROVED',
        reason='楼内无信号，管理员已核对',
        data={'code': 'LOCATION_UNAVAILABLE',
              'assignmentId': first_assignment.id,
              'assignmentVersion': first_assignment.version,
              'decidedAt': utcnow().isoformat()},
    )
    stale = BusinessException(
        order_id=second.id, type='LOCATION_EXCEPTION', status='APPROVED',
        reason='旧版本审批不应生效',
        data={'code': 'LOCATION_UNAVAILABLE',
              'assignmentId': second_assignment.id,
              'assignmentVersion': second_assignment.version + 1,
              'decidedAt': utcnow().isoformat()},
    )
    db.add_all([approved, stale])
    db.commit()

    order_response = api(courier_client, f'/courier/orders/{first.id}', method='GET')
    assert order_response.status_code == 200, order_response.text
    approval = order_response.json()['data']['locationExceptionApproval']
    assert approval == {
        'id': approved.id,
        'status': 'APPROVED',
        'assignmentId': first_assignment.id,
        'assignmentVersion': first_assignment.version,
        'reason': '楼内无信号，管理员已核对',
        'failureCode': 'LOCATION_UNAVAILABLE',
        'approvedAt': approved.data['decidedAt'].replace('+00:00', 'Z'),
    }

    other_response = api(courier_client, f'/courier/orders/{second.id}', method='GET')
    assert other_response.status_code == 200, other_response.text
    assert other_response.json()['data'].get('locationExceptionApproval') is None

    batch_response = api(courier_client, f'/courier/batches/{batch["id"]}', method='GET')
    assert batch_response.status_code == 200, batch_response.text
    rows = {row['id']: row for row in batch_response.json()['data']['orders']}
    assert rows[first.id]['locationExceptionApproval']['id'] == approved.id
    assert rows[second.id].get('locationExceptionApproval') is None
    assert stale.id not in str(batch_response.json()['data'])

    # A later assignment version invalidates the old approval.
    first_assignment.version += 1
    db.commit()
    stale_version = api(courier_client, f'/courier/orders/{first.id}', method='GET')
    assert stale_version.status_code == 200, stale_version.text
    assert stale_version.json()['data'].get('locationExceptionApproval') is None



def _enable_order_notifications(db, account):
    from app.models import utcnow

    account.preferences = {**(account.preferences or {}),
                           'orderStatusUpdated': True}
    row = db.scalar(select(ConfigSection).where(ConfigSection.section == 'mail'))
    data = {
        'version': 1, 'enabled': True, 'host': '127.0.0.1', 'port': 2525,
        'security': 'STARTTLS', 'authEnabled': False, 'username': '',
        'fromName': 'DormPrint 测试', 'fromAddress': 'mailer@example.invalid',
        'replyTo': '', 'timeoutSeconds': 5, 'allowInsecure': True,
        'secretCiphertext': None,
        'lastTest': {'status': 'PASS', 'time': utcnow().isoformat(), 'message': ''},
    }
    if row is None:
        db.add(ConfigSection(section='mail', data=data, version=1))
    else:
        row.data = data
        row.version = 1
    db.commit()


def test_start_and_proof_queue_one_status_notification_per_order(
        db, api, admin_client, client_factory, staff_factory, account_factory):
    courier = make_courier(staff_factory)
    account = account_factory()
    _enable_order_notifications(db, account)
    order = make_ready_order(db)
    order.checkout_account_id = account.id
    order.principal_id = account.principal_id
    db.commit()

    batch, assignments = _batch(db, api, admin_client, courier, [order])
    db.refresh(order)
    jobs = list(db.scalars(select(EmailJob).order_by(EmailJob.created_at)))
    assert len(jobs) == 1
    assert jobs[0].business_key ==         f'order-email:ORDER_STATUS_UPDATED:{order.id}:status:{order.version}'

    photo = _proof_asset(db, courier, assignments[0])
    courier_client = _staff_client(client_factory, db, courier)
    body = _body([order], assignments, photo)
    key = 'notify-proof-' + uuid4().hex
    response = _post(api, courier_client, batch, body, key=key)
    assert response.status_code == 200, response.text
    db.expire_all()
    jobs = list(db.scalars(select(EmailJob).order_by(EmailJob.created_at)))
    assert len(jobs) == 2
    delivered = db.get(type(order), order.id)
    assert jobs[-1].business_key ==         f'order-email:ORDER_STATUS_UPDATED:{order.id}:status:{delivered.version}'

    replay = _post(api, courier_client, batch, body, key=key)
    assert replay.status_code == 200, replay.text
    assert len(list(db.scalars(select(EmailJob)))) == 2


def test_admin_exception_resolve_queues_status_notification(
        db, api, admin_client, client_factory, staff_factory, account_factory):
    courier = make_courier(staff_factory)
    account = account_factory()
    _enable_order_notifications(db, account)
    order = make_ready_order(db)
    order.checkout_account_id = account.id
    order.principal_id = account.principal_id
    db.commit()

    batch, assignments = _batch(db, api, admin_client, courier, [order])
    db.refresh(order)
    courier_client = _staff_client(client_factory, db, courier)
    reported = api(courier_client, f'/courier/orders/{order.id}/exception', {
        'expectedVersion': order.version,
        'type': 'CANNOT_CONTACT',
        'reason': '电话无人接听',
    })
    assert reported.status_code == 200, reported.text
    exception = reported.json()['data']['exception']

    resolved = api(admin_client, f'/admin/exceptions/{exception["id"]}/resolve', {
        'expectedVersion': exception['version'],
        'resolution': 'REDELIVER',
        'reason': '联系上收件人，重新安排配送',
    })
    assert resolved.status_code == 200, resolved.text
    db.expire_all()
    rows = list(db.scalars(select(EmailJob).order_by(EmailJob.created_at)))
    assert len(rows) == 2
    current = db.get(type(order), order.id)
    assert current.status == 'READY_FOR_DELIVERY'
    assert rows[-1].business_key ==         f'order-email:ORDER_STATUS_UPDATED:{order.id}:status:{current.version}'


def test_auto_unpaid_timeout_close_queues_status_notification(
        db, account_factory):
    from app.maintenance import _close_expired_orders

    account = account_factory()
    _enable_order_notifications(db, account)
    order = make_ready_order(db)
    order.checkout_account_id = account.id
    order.principal_id = account.principal_id
    order.status = 'PENDING_CONFIRMATION'
    order.settlement_kind = None
    order.cash_received_cents = 0
    order.close_at = utcnow() - timedelta(minutes=1)
    db.commit()

    assert _close_expired_orders(db, utcnow()) == 1
    db.expire_all()
    current = db.get(type(order), order.id)
    assert current.status == 'CANCELLED'
    rows = list(db.scalars(select(EmailJob)))
    assert len(rows) == 1
    assert rows[0].business_key ==         f'order-email:ORDER_STATUS_UPDATED:{order.id}:status:{current.version}'



def test_batch_proof_requires_and_enforces_assignment_version(
        db, api, admin_client, client_factory, staff_factory):
    courier = make_courier(staff_factory)
    first = make_ready_order(db)
    second = make_ready_order(db)
    batch, assignments = _batch(db, api, admin_client, courier, [first, second])
    photo = _proof_asset(db, courier, assignments[0])
    client = _staff_client(client_factory, db, courier)

    body = _body([first, second], assignments, photo)
    body['items'][0].pop('expectedAssignmentVersion')
    missing = _post(api, client, batch, body)
    assert missing.status_code == 422, missing.text

    body = _body([first, second], assignments, photo)
    body['items'][1]['expectedAssignmentVersion'] = assignments[1].version + 1
    stale = _post(api, client, batch, body)
    assert stale.status_code == 409, stale.text
    assert stale.json()['error']['code'] == 'VERSION_CONFLICT'
    db.expire_all()
    assert db.scalar(select(func.count(DeliveryProof.id))) == 0
    assert all(db.get(type(order), order.id).status == 'DELIVERING'
               for order in (first, second))


def test_single_delivery_lock_refreshes_stale_identity_map_version(
        db, api, admin_client, staff_factory):
    """A read-before-lock ORM object must not hide a committed version bump."""
    courier = make_courier(staff_factory)
    order = make_ready_order(db)
    _batch(db, api, admin_client, courier, [order])

    stale = SessionLocal()
    updater = SessionLocal()
    try:
        stale_order = stale.get(Order, order.id)
        assert stale_order is not None
        expected = stale_order.version

        current = updater.get(Order, order.id)
        touch(current)
        updater.commit()

        try:
            _lock_order_delivery(stale, order.id, expected_version=expected)
        except DomainError as error:
            assert error.code == 'VERSION_CONFLICT', error.code
        else:
            raise AssertionError('stale identity-map version was accepted')
    finally:
        stale.rollback()
        stale.close()
        updater.rollback()
        updater.close()


def _admin_actor(admin):
    return Actor(staff_id=admin.id, staff_role='ADMIN',
                 capabilities=set(admin.capabilities))


def _courier_actor(courier):
    return Actor(staff_id=courier.id, staff_role='COURIER',
                 capabilities=set(courier.capabilities))


def _hold_order_then_refresh_batch(db, order_id, batch_id, order_locked,
                                   contender_started, errors):
    try:
        db.execute(text("SET LOCAL lock_timeout = '3s'"))
        db.execute(select(Order).where(Order.id == order_id).with_for_update())
        order_locked.set()
        assert contender_started.wait(5)
        time.sleep(0.2)
        # Root after_sales now locks the order, then active assignments
        # (sorted id), then the batch.  Mirror it so this test covers the
        # actual production lock graph.
        db.execute(select(DeliveryAssignment).where(
            DeliveryAssignment.order_id == order_id,
            DeliveryAssignment.active.is_(True)).order_by(
                DeliveryAssignment.id).with_for_update())
        db.execute(select(DeliveryBatch).where(
            DeliveryBatch.id == batch_id).with_for_update())
        refresh_batch(db, batch_id)
        db.commit()
    except Exception as error:  # pragma: no cover - failure is asserted below
        errors.append(error)
    finally:
        db.close()


def test_single_proof_and_after_sales_order_first_do_not_deadlock(
        db, api, admin_client, client_factory, staff_factory):
    """Proof must wait on the order lock, not hold the batch first."""
    courier = make_courier(staff_factory)
    first = make_ready_order(db)
    second = make_ready_order(db)
    batch, assignments = _batch(db, api, admin_client, courier, [first, second])
    db.expire_all()
    first = db.get(type(first), first.id)
    assignment = db.get(DeliveryAssignment, assignments[0].id)
    photo = _proof_asset(db, courier, assignment)
    payload = DeliveryProofInput(
        expected_version=first.version,
        delivery_assignment_id=assignment.id,
        photo_asset_id=photo.id,
        skip_photo_reason='',
        placement_note='并发测试',
        position=PositionInput(latitude=31.2304, longitude=121.4737,
                               accuracy_meters=5, captured_at=utcnow(),
                               coordinate_system='WGS84'),
        skip_location_reason='',
        location_failure_code=None,
    )
    errors = []
    order_locked = threading.Event()
    proof_started = threading.Event()

    def proof_worker():
        session = SessionLocal()
        try:
            session.execute(text("SET LOCAL lock_timeout = '3s'"))
            assert order_locked.wait(5)
            proof_started.set()
            courier_proof(first.id, payload, db=session,
                          actor=_courier_actor(courier))
            session.commit()
        except Exception as error:  # pragma: no cover - failure is asserted below
            errors.append(error)
        finally:
            session.close()

    def after_sales_worker():
        _hold_order_then_refresh_batch(
            SessionLocal(), first.id, batch['id'], order_locked, proof_started, errors)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(proof_worker), pool.submit(after_sales_worker)]
        for future in futures:
            future.result(timeout=15)

    assert not errors, [repr(error) for error in errors]
    db.expire_all()
    assert db.scalar(select(func.count(DeliveryProof.id))) == 1
    assert db.get(type(first), first.id).status == 'DELIVERED'


def test_batch_start_and_after_sales_order_first_do_not_deadlock(
        db, api, admin_client, staff_factory):
    """Batch start must not hold the batch while waiting for an order lock."""
    courier = make_courier(staff_factory)
    order = make_ready_order(db)
    batch = create_batch(api, admin_client, courier)
    add_orders(api, admin_client, batch, [order.id])
    published = publish_batch(api, admin_client, batch['id'])
    errors = []
    order_locked = threading.Event()
    start_called = threading.Event()

    def start_worker():
        session = SessionLocal()
        try:
            session.execute(text("SET LOCAL lock_timeout = '3s'"))
            assert order_locked.wait(5)
            start_called.set()
            batch_start(batch['id'], BatchVersionInput(expected_version=published['version']),
                        db=session, actor=_admin_actor(admin_client.staff))
            session.commit()
        except Exception as error:  # pragma: no cover - failure is asserted below
            errors.append(error)
        finally:
            session.close()

    def after_sales_worker():
        _hold_order_then_refresh_batch(
            SessionLocal(), order.id, batch['id'], order_locked, start_called, errors)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(start_worker), pool.submit(after_sales_worker)]
        for future in futures:
            future.result(timeout=15)

    assert not errors, [repr(error) for error in errors]
    db.expire_all()
    assert db.get(type(order), order.id).status == 'DELIVERING'
    assert db.get(DeliveryBatch, batch['id']).status == 'DELIVERING'
