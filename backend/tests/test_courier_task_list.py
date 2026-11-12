from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import DeliveryAssignment, DeliveryBatch, EmailJob
from test_delivery import (
    add_orders, courier_client, create_batch, create_batch_fixed, make_courier,
    make_ready_order, publish_batch,
)
from test_delivery_closed_loop import APP_MODULES


def items(api, client, path='/courier/batches'):
    response = api(client, path, method='GET')
    assert response.status_code == 200, response.text
    return response.json()['data']['items']


def test_empty_auto_placeholder_stays_reusable_and_manual_empty_batch_stays_in_planning(
        db, api, admin_client, client_factory, staff_factory):
    courier = make_courier(staff_factory)
    assigned = courier_client(client_factory, db, courier)
    order = make_ready_order(db)
    automatic = create_batch(api, admin_client, courier)
    manual = create_batch(api, admin_client, courier)
    stored = db.get(DeliveryBatch, automatic['id'])
    stored.source = 'AUTO'
    stored.is_regular = True
    db.commit()
    for viewer in (assigned, admin_client):
        assert items(api, viewer) == []
        for batch in (automatic, manual):
            assert api(viewer, f'/courier/batches/{batch["id"]}', method='GET').status_code == 404
    assert [row['id'] for row in items(api, admin_client, '/admin/batches')] == [manual['id']]
    assert api(admin_client, f'/admin/batches/{automatic["id"]}', method='GET').status_code == 200
    rejected = api(admin_client, f'/admin/batches/{manual["id"]}/publish', {
        'expectedVersion': manual['version'],
    })
    assert rejected.status_code == 409 and rejected.json()['error']['code'] == 'BATCH_EMPTY'
    assert db.scalars(select(EmailJob)).first() is None
    db.get(DeliveryBatch, manual['id']).status = 'PUBLISHED'
    db.commit()
    assert items(api, assigned) == []
    denied = api(assigned, f'/courier/batches/{manual["id"]}/start', {
        'expectedVersion': manual['version'],
    })
    assert denied.status_code == 409, denied.text
    assert 'BATCH_EMPTY' in {item['code'] for item in denied.json()['error']['details']['blockers']}

    add_orders(api, admin_client, automatic, [order.id])
    visible = items(api, assigned)[0]
    assert visible['id'] == automatic['id'] and visible['orderIds'] == [order.id]
    assert visible['status'] == 'PLANNED' and 'PUBLISH' not in visible['allowedActions']
    assert api(assigned, f'/courier/orders/{order.id}', method='GET').status_code == 200
    published = publish_batch(api, admin_client, automatic['id'])
    assert items(api, assigned)[0]['status'] == 'PUBLISHED'
    withdrawn = api(admin_client, f'/admin/batches/{automatic["id"]}/withdraw-publication', {
        'expectedVersion': published['version'],
    })
    assert withdrawn.status_code == 200, withdrawn.text
    visible = items(api, assigned)[0]
    assert visible['status'] == 'PLANNED' and visible['publicationWithdrawnAt']
    assert 'START' not in visible['allowedActions']


@pytest.mark.parametrize('condition', ['inactive', 'cancelled_assignment', 'foreign', 'cancelled_order', 'pending_order',
                                       'deleted_order', 'cancelled_batch', 'deleted_batch'])
def test_courier_read_surfaces_agree_on_unavailable_work(
        db, api, admin_client, client_factory, staff_factory, condition):
    courier = make_courier(staff_factory)
    foreign = make_courier(staff_factory)
    assigned = courier_client(client_factory, db, courier)
    order = make_ready_order(db)
    batch = create_batch(api, admin_client, courier)
    add_orders(api, admin_client, batch, [order.id])
    db.expire_all()
    stored = db.get(DeliveryBatch, batch['id'])
    stored.status = 'PUBLISHED'
    assignment = db.scalars(select(DeliveryAssignment).where(DeliveryAssignment.order_id == order.id)).one()
    if condition == 'inactive':
        assignment.active = False
    elif condition == 'cancelled_assignment':
        assignment.status = 'CANCELLED'
    elif condition == 'foreign':
        assignment.courier_id = foreign.id
    elif condition == 'cancelled_order':
        order.status = 'CANCELLED'
    elif condition == 'pending_order':
        order.status = 'PENDING_CONFIRMATION'
    elif condition == 'deleted_order':
        order.deleted_at = datetime.now(timezone.utc)
    elif condition == 'cancelled_batch':
        stored.status = 'CANCELLED'
    else:
        stored.deleted_at = datetime.now(timezone.utc)
    db.commit()
    assert items(api, assigned) == []
    assert api(assigned, f'/courier/batches/{batch["id"]}', method='GET').status_code == 404
    assert api(assigned, f'/courier/orders/{order.id}', method='GET').status_code == 404


def test_date_and_status_follow_shanghai_batch_start_and_scope_order_counts(
        db, api, admin_client, client_factory, staff_factory):
    courier = make_courier(staff_factory)
    foreign = make_courier(staff_factory)
    assigned = courier_client(client_factory, db, courier)
    midnight = datetime(2026, 11, 11, 16, tzinfo=timezone.utc)
    finished = make_ready_order(db, delivery_starts_at=midnight - timedelta(minutes=1))
    pending = make_ready_order(db, delivery_starts_at=midnight)
    invalid = make_ready_order(db, delivery_starts_at=midnight)
    previous = create_batch_fixed(api, admin_client, courier, midnight - timedelta(minutes=1))
    current = create_batch_fixed(api, admin_client, courier, midnight)
    add_orders(api, admin_client, previous, [finished.id])
    add_orders(api, admin_client, current, [pending.id, invalid.id])
    db.expire_all()
    db.get(DeliveryBatch, previous['id']).status = 'COMPLETED'
    db.get(DeliveryBatch, current['id']).status = 'PUBLISHED'
    finished.status = 'DELIVERED'
    db.scalars(select(DeliveryAssignment).where(DeliveryAssignment.order_id == finished.id)).one().status = 'DELIVERED'
    db.scalars(select(DeliveryAssignment).where(DeliveryAssignment.order_id == invalid.id)).one().courier_id = foreign.id
    db.commit()

    yesterday = items(api, assigned, '/courier/batches?date=2026-11-11&status=COMPLETED')
    assert [(row['id'], row['date'], row['deliveredCount']) for row in yesterday] == [(previous['id'], '2026-11-11', 1)]
    assert items(api, assigned, '/courier/batches?date=2026-11-12&status=COMPLETED') == []
    today = items(api, assigned, '/courier/batches?date=2026-11-12')
    assert [(row['id'], row['orderIds'], row['paidCount']) for row in today] == [(current['id'], [pending.id], 1)]
    detail = api(assigned, f'/courier/batches/{current["id"]}', method='GET').json()['data']
    assert detail['batch']['orderIds'] == [pending.id]
    assert [order['id'] for order in detail['orders']] == [pending.id]
    assert api(assigned, '/courier/batches?date=2026-02-30', method='GET').status_code == 422


def test_unpublished_batch_cannot_execute_even_if_legacy_order_and_assignment_still_look_started(
        db, api, admin_client, client_factory, staff_factory):
    courier = make_courier(staff_factory)
    assigned = courier_client(client_factory, db, courier)
    order = make_ready_order(db)
    batch = create_batch(api, admin_client, courier)
    add_orders(api, admin_client, batch, [order.id])
    db.expire_all()
    assignment = db.scalars(select(DeliveryAssignment).where(DeliveryAssignment.order_id == order.id)).one()
    order.status = 'DELIVERING'
    assignment.status = 'STARTED'
    db.commit()
    path = f'/courier/orders/{order.id}'
    view = api(assigned, path, method='GET').json()['data']
    assert not {'SUBMIT_PROOF', 'REPORT_EXCEPTION', 'REQUEST_LOCATION_EXCEPTION'}.intersection(view['allowedActions'])
    for suffix, fields in (
        ('proof', {'deliveryAssignmentId': assignment.id, 'skipPhotoReason': '客户要求免拍'}),
        ('exception', {'type': '无法联系收件人', 'reason': '暂时联系不上收件人'}),
        ('location-exception', {'code': 'POSITION_UNAVAILABLE', 'reason': '无法获取定位'}),
    ):
        response = api(assigned, f'{path}/{suffix}', {'expectedVersion': order.version, **fields})
        assert response.status_code == 409, response.text
        assert response.json()['error']['code'] == 'INVALID_TRANSITION'
