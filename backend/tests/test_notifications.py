from datetime import timedelta

import pytest

from app.core import audit
from app.models import (
    AfterSale, Artifact, Asset, BindingTask, BusinessException, DeliveryAssignment, DeliveryBatch, OrderFile,
    PrintAttempt, PrintTask, utcnow,
)
from auth_helpers import make_order

APP_MODULES = ['auth', 'notifications', 'production']
CENTER = '/api/v1/admin/notifications'


@pytest.fixture
def sign_in(api, client_factory, staff_factory):
    def login(capabilities, role='ADMIN'):
        staff = staff_factory(role=role, capabilities=capabilities)
        client = client_factory()
        response = api(client, '/auth/login', {
            'username': staff.username, 'password': 'Testing-only-Password-42!',
        })
        assert response.status_code == 200, response.text
        return client, staff
    return login


def data(client, suffix='', **params):
    response = client.get(CENTER + suffix, params=params)
    assert response.status_code == 200, response.text
    return response.json()['data']


def test_manual_read_is_per_staff_and_business_completion_is_shared(db, account_factory, sign_in, api):
    first, _ = sign_in(['payment.manage'])
    second, _ = sign_in(['payment.manage'])
    customer = account_factory()
    order = make_order(db, customer.principal_id)
    db.commit()

    result = data(first)
    assert result['total'] == result['unreadCount'] == 1
    notification, = result['items']
    assert notification['kind'] == 'PAYMENT_PENDING'
    assert notification['targetId'] == order.id
    assert notification['context'] == order.pickup_code
    assert notification['read'] is False
    assert notification['taskStatus'] == 'PENDING'
    path = f"{CENTER}/{notification['id']}/read"
    for _ in range(2):
        read = api(first, path, {})
        assert read.status_code == 200, read.text
        assert read.json()['data'] == {'unreadCount': 0}
    read_item, = data(first, filter='READ')['items']
    assert read_item['read'] is True
    assert data(first, filter='PENDING')['items'] == [read_item]
    assert data(first, filter='UNREAD')['items'] == []
    assert data(second, '/unread') == {'unreadCount': 1}

    order.status = 'PENDING_PRINT'
    order.settlement_kind = 'CASH_MANUAL'
    db.commit()
    for client in (first, second):
        assert data(client, '/unread') == {'unreadCount': 0}
        assert data(client, filter='PENDING')['items'] == []
        handled, = data(client, filter='HANDLED')['items']
        assert handled == {**notification, 'taskStatus': 'HANDLED', 'read': True}
        assert data(client)['items'] == [handled]


def test_capabilities_limit_list_badge_and_read_target(db, account_factory, sign_in, api, client, customer_client):
    payment, staff = sign_in(['payment.manage'])
    delivery, _ = sign_in(['delivery.manage'])
    viewer, _ = sign_in([])
    courier, _ = sign_in([], 'COURIER')
    order = make_order(db, account_factory().principal_id)
    db.commit()

    assert client.get(CENTER).status_code == 401
    assert customer_client.get(CENTER + '/unread').status_code == 401
    assert courier.get(CENTER).status_code == 403
    item, = data(payment)['items']
    path = f"{CENTER}/{item['id']}/read"
    for limited in (delivery, viewer):
        assert data(limited)['items'] == []
        assert data(limited, '/unread') == {'unreadCount': 0}
        assert api(limited, path, {}).status_code == 404
    assert api(payment, CENTER + '/PAYMENT_PENDING:missing/read', {}).status_code == 404

    staff.capabilities = []
    db.commit()
    assert data(payment)['items'] == []
    assert api(payment, path, {}).status_code == 404


def test_read_all_covers_every_page_without_changing_other_staff_or_tasks(
        db, account_factory, sign_in, api):
    first, _ = sign_in(['payment.manage'])
    second, _ = sign_in(['payment.manage'])
    courier, _ = sign_in([], 'COURIER')
    principal_id = account_factory().principal_id
    make_order(db, principal_id)
    make_order(db, principal_id)
    db.commit()

    path = f'{CENTER}/read-all'
    assert api(courier, path, {}).status_code == 403
    assert data(first, filter='UNREAD', pageSize=1)['total'] == 2
    for _ in range(2):
        response = api(first, path, {})
        assert response.status_code == 200, response.text
        assert response.json()['data'] == {'unreadCount': 0}
    assert data(first, filter='UNREAD')['items'] == []
    pending = data(first, filter='PENDING')['items']
    assert len(pending) == 2 and all(item['read'] for item in pending)
    assert data(second, '/unread') == {'unreadCount': 2}

    make_order(db, principal_id)
    db.commit()
    assert data(first, '/unread') == {'unreadCount': 1}


def test_paginating_all_keeps_unread_first_across_pages(db, account_factory, sign_in, api):
    client, _ = sign_in(['payment.manage'])
    principal_id = account_factory().principal_id
    oldest = make_order(db, principal_id)
    middle = make_order(db, principal_id)
    newest = make_order(db, principal_id)
    db.commit()
    first, = data(client, pageSize=1)['items']
    assert first['targetId'] == newest.id
    assert api(client, f"{CENTER}/{first['id']}/read", {}).status_code == 200

    for page, order in enumerate((middle, oldest, newest), start=1):
        result = data(client, page=page, pageSize=1)
        assert (result['total'], result['unreadCount'], result['page'], result['pageSize']) == (3, 2, page, 1)
        assert [item['targetId'] for item in result['items']] == [order.id]
    assert data(client, page=4, pageSize=1) == {
        'items': [], 'total': 3, 'unreadCount': 2, 'page': 4, 'pageSize': 1,
    }
    assert data(client, filter='READ', pageSize=1)['unreadCount'] == 2
    assert client.get(CENTER, params={'filter': 'INVALID'}).status_code == 422
    assert client.get(CENTER, params={'page': 0}).status_code == 422


def test_reopened_payment_creates_new_unread_episode(db, account_factory, sign_in, api):
    client, staff = sign_in(['payment.manage'])
    order = make_order(db, account_factory().principal_id)
    db.commit()
    original, = data(client)['items']
    assert api(client, f"{CENTER}/{original['id']}/read", {}).status_code == 200

    audit(db, 'system', 'ORDER_AUTO_CLOSED', 'ORDER', order.id)
    db.flush()
    audit(db, staff.id, 'ORDER_REOPENED', 'ORDER', order.id)
    db.commit()

    current, = data(client, filter='PENDING')['items']
    assert current['id'] != original['id']
    assert current['read'] is False
    assert current['targetId'] == original['targetId']
    handled, = data(client, filter='HANDLED')['items']
    assert handled['id'] == original['id']


def test_workflow_sources_use_safe_context_and_exact_target(db, account_factory, sign_in):
    client, staff = sign_in(['*'])
    order = make_order(db, account_factory().principal_id, status='PENDING_PRINT')
    order.settlement_kind = 'CASH_MANUAL'
    order.note = 'private-order-note'
    asset = Asset(principal_id=order.principal_id, purpose='ORDER', name='private-document.pdf',
                  mime='application/pdf', size=1, sha256='a' * 64, storage_key='private/path')
    db.add(asset)
    db.flush()
    artifact = Artifact(preparation_id=order.preparation_id, asset_id=asset.id, client_file_id='fixture',
                        sha256=asset.sha256, pages=1, sheets=1)
    order_file = OrderFile(order_id=order.id, asset_id=asset.id, revision=1, client_file_id='fixture',
                           kind='A4', binding_type='CORNER')
    db.add_all([artifact, order_file])
    db.flush()
    task = PrintTask(order_id=order.id, file_id=order_file.id, artifact_id=artifact.id, status='EXCEPTION')
    binding = BindingTask(order_id=order.id, file_id=order_file.id, status='READY')
    db.add_all([task, binding])
    db.flush()
    failed = PrintAttempt(task_id=task.id, artifact_id=artifact.id, staff_id=staff.id, outcome='FAILED',
                          reason='private-print-note', completed_at=utcnow())
    audit(db, staff.id, 'production.correction.reopen_binding', 'BindingTask', binding.id, 'private-audit-note')
    mismatch = BusinessException(order_id=order.id, type='PAYMENT_MISMATCH', status='OPEN', reason='private-cash-note')
    delivery = BusinessException(order_id=order.id, type='DELIVERY_EXCEPTION', status='OPEN', reason='private-delivery-note')
    location = BusinessException(order_id=order.id, type='LOCATION_EXCEPTION', status='OPEN', reason='private-location-note')
    requested = AfterSale(order_id=order.id, source='CUSTOMER', actor_id=order.principal_id,
                          responsibility='MERCHANT', solution='REPRINT', status='REQUESTED', reason='private-case-note')
    refund = AfterSale(order_id=order.id, source='ADMIN', actor_id=staff.id,
                       responsibility='MERCHANT', solution='REFUND', status='REFUND_APPROVED',
                       approved_cents=100, reason='private-refund-note')
    batch = DeliveryBatch(name='周一配送', status='PUBLISHED', starts_at=utcnow(),
                          ends_at=utcnow() + timedelta(hours=1))
    db.add_all([failed, mismatch, delivery, location, requested, refund, batch])
    db.flush()
    audit(db, staff.id, 'AFTER_SALE_APPROVED', 'AFTER_SALE', refund.id,
          'private-review-note', {'approvedCents': 100})
    audit(db, staff.id, 'BATCH_PUBLISHED', 'DELIVERY_BATCH', batch.id)
    audit(db, staff.id, 'BATCH_COURIER_ASSIGNED', 'DELIVERY_BATCH', batch.id, 'private-batch-note')
    db.commit()

    items = data(client, filter='UNREAD')['items']
    by_kind = {item['kind']: item for item in items}
    expected = {
        'PAYMENT_MISMATCH': order.id, 'PRINT_FAILED': task.id, 'BINDING_REOPENED': binding.id,
        'DELIVERY_EXCEPTION': delivery.id, 'LOCATION_EXCEPTION': location.id,
        'AFTER_SALE_REQUESTED': requested.id, 'REFUND_PENDING': refund.id,
        'BATCH_PENDING': batch.id, 'BATCH_CHANGED': batch.id,
    }
    assert {kind: item['targetId'] for kind, item in by_kind.items()} == expected
    for kind, item in by_kind.items():
        assert item['context'] == (batch.name if kind.startswith('BATCH_') else order.pickup_code)
        assert item['taskStatus'] == (None if kind == 'BATCH_CHANGED' else 'PENDING')
        assert 'private-' not in str(item)
    assert by_kind['BATCH_CHANGED']['id'] not in {item['id'] for item in data(client, filter='PENDING')['items']}

    mismatch.status = 'RESOLVED'
    delivery.status = 'RESOLVED'
    location.status = 'APPROVED'
    requested.status = 'REPRINTING'
    refund.status = 'REFUNDED'
    binding.status = 'COMPLETED'
    task.status = 'COMPLETED'
    batch.status = 'DELIVERING'
    db.add(PrintAttempt(task_id=task.id, artifact_id=artifact.id, staff_id=staff.id,
                        outcome='COMPLETED', completed_at=utcnow()))
    audit(db, staff.id, 'production.binding_complete', 'BindingTask', binding.id)
    audit(db, staff.id, 'BATCH_STARTED', 'DELIVERY_BATCH', batch.id)
    db.commit()
    all_items = {item['id']: item for item in data(client)['items']}
    for kind, item in by_kind.items():
        if kind != 'BATCH_CHANGED':
            assert all_items[item['id']] == {**item, 'taskStatus': 'HANDLED', 'read': True}
    assert data(client, '/unread') == {'unreadCount': 1}

    # The previously resolved production and dispatch episodes stay handled after rollback/reopen.
    binding.status = 'READY'
    task.status = 'EXCEPTION'
    batch.status = 'PUBLISHED'
    audit(db, staff.id, 'production.correction.reopen_binding', 'BindingTask', binding.id)
    audit(db, staff.id, 'BATCH_PUBLICATION_WITHDRAWN', 'DELIVERY_BATCH', batch.id)
    audit(db, staff.id, 'BATCH_PUBLISHED', 'DELIVERY_BATCH', batch.id)
    db.add(PrintAttempt(task_id=task.id, artifact_id=artifact.id, staff_id=staff.id,
                        outcome='FAILED', completed_at=utcnow()))
    db.commit()
    current_items = data(client, filter='PENDING')['items']
    assert {item['kind'] for item in current_items} == {'PRINT_FAILED', 'BINDING_REOPENED', 'BATCH_PENDING'}
    assert {item['id'] for item in current_items}.isdisjoint(item['id'] for item in by_kind.values())

    binding_url = '/api/v1/admin/binding-tasks'
    assert client.get(binding_url, params={'taskId': binding.id}).json()['data']['tasks'][0]['readOnly'] is False
    order.status = 'DELIVERED'
    db.commit()
    assert client.get(binding_url).json()['data']['tasks'] == []
    archived, = client.get(binding_url, params={'taskId': binding.id}).json()['data']['tasks']
    assert archived['id'] == binding.id
    assert archived['readOnly'] is True
    assert all(item['kind'] != 'BINDING_REOPENED' for item in data(client, filter='PENDING')['items'])


def test_dispatch_operation_links_each_batch_and_ignores_withdrawn_location_requests(
        db, account_factory, sign_in):
    client, staff = sign_in(['delivery.manage'])
    order = make_order(db, account_factory().principal_id)
    now = utcnow()
    source = DeliveryBatch(name='原配送批次', starts_at=now, ends_at=now + timedelta(hours=1))
    target = DeliveryBatch(name='目标配送批次', starts_at=now, ends_at=now + timedelta(hours=1))
    db.add_all([source, target])
    db.flush()
    assignment = DeliveryAssignment(
        order_id=order.id, batch_id=source.id, active=False,
        data={'publicationWithdrawnAt': now.isoformat()},
    )
    db.add(assignment)
    db.flush()
    db.add(BusinessException(
        order_id=order.id, type='LOCATION_EXCEPTION', status='OPEN',
        reason='撤回前的定位例外', data={'assignmentId': assignment.id},
    ))
    audit(db, staff.id, 'BATCH_OPERATION_COMMITTED', 'BATCH_PLAN', 'test-operation',
          'private-operation-note', {'action': 'TRANSFER', 'batchIds': [source.id, target.id]})
    db.commit()

    messages = data(client, filter='UNREAD')['items']
    assert {row['targetId'] for row in messages} == {source.id, target.id}
    assert len({row['id'] for row in messages}) == 2
    assert all(row['kind'] == 'BATCH_CHANGED' and row['taskStatus'] is None for row in messages)
    assert 'private-operation-note' not in str(messages)
