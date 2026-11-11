"""Timed-out order recovery through the real API and PostgreSQL."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.maintenance import _close_expired_orders
from app.models import Asset, AuditLog, DeliveryAssignment, DeliverySlot, Order, Payment, Receipt, utcnow
from test_delivery_closed_loop import APP_MODULES, make_world, submit
from test_growth import line, make_campaign, make_coupon
from test_v1_integration import staff_client


def expire(db, order):
    order.close_at = utcnow() - timedelta(minutes=1)
    db.commit()
    assert _close_expired_orders(db, utcnow()) == 1
    db.commit()
    assert order.status == 'CANCELLED'


def reopen(api, client, order, **kwargs):
    return api(client, f'/admin/orders/{order.id}/reopen',
               {'expectedVersion': order.version, 'confirmed': True}, **kwargs)


@pytest.mark.parametrize('legacy', [False, True])
def test_reopen_then_confirm_original_cash_once(db, api, admin_client, legacy):
    world = make_world(db)
    order = submit(db, world)
    world.quote.expires_at = utcnow() - timedelta(minutes=1)
    receipt = Receipt(order_id=order.id, amount_cents=order.total_cents,
                      staff_id=admin_client.staff.id,
                      data={'reasonCode': 'CLOSED_ORDER', 'refundRequired': True})
    db.add(receipt)
    expire(db, order)
    if legacy:
        order.data = {k: v for k, v in order.data.items() if k != 'closeReasonCode'}
        db.commit()
    detail = api(admin_client, f'/admin/orders/{order.id}', method='GET').json()['data']
    assert 'ADMIN_REOPEN_TIMED_OUT_ORDER' in detail['allowedActions']
    path = f'/admin/orders/{order.id}/reopen'
    body = {'expectedVersion': order.version, 'confirmed': True}
    for value in (False, 1):
        assert api(admin_client, path, {**body, 'confirmed': value}).status_code == 422
    response = api(admin_client, path, body, key='reopen-order')
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['status'] == 'PENDING_CONFIRMATION'
    assert data['currentQuoteId'] == order.quote_id and data['contentRevision'] == order.content_revision
    assert data['totalCents'] == order.total_cents and data['settlement'] is None
    assert data['unmatchedReceipts'][0]['id'] == receipt.id
    assert data['history'][0]['title'] == '超时订单已恢复'
    assert 'ADMIN_REOPEN_TIMED_OUT_ORDER' not in data['allowedActions']
    assert api(admin_client, path, body, key='reopen-order').status_code == 200
    assert api(admin_client, path, body).status_code == 409
    db.expire_all()
    order = db.get(Order, order.id)
    assert order.close_at > utcnow() + timedelta(minutes=59)
    assert _close_expired_orders(db, utcnow()) == 0
    assert db.get(DeliverySlot, world.slot.id).occupied == 0
    assert db.scalar(select(func.count()).select_from(DeliveryAssignment)) == 0
    assert db.scalar(select(func.count()).select_from(Payment)) == 0
    audit = db.scalars(select(AuditLog).where(AuditLog.action == 'ORDER_REOPENED')).one()
    assert audit.actor_id == admin_client.staff.id
    db.commit()

    settled = api(admin_client, f'/admin/orders/{order.id}/confirm-payment', {
        'expectedVersion': order.version, 'contentRevision': order.content_revision,
        'quoteId': order.quote_id, 'receivedCents': order.total_cents,
        'settlementKind': 'CASH_MANUAL', 'receiptId': receipt.id,
    })
    assert settled.status_code == 200, settled.text
    assert settled.json()['data']['status'] == 'PENDING_PRINT'
    db.expire_all()
    allocated_receipt = db.scalars(select(Receipt)).one()
    assert allocated_receipt.status == 'ALLOCATED'
    assert allocated_receipt.data['refundRequired'] is False
    assert db.scalars(select(Payment)).one().receipt_id == receipt.id
    assert db.get(DeliverySlot, world.slot.id).occupied == 1


def test_reopen_rejects_legacy_customer_cancellation_even_with_matching_text(db, api, admin_client):
    from app.commerce import cancel_pending
    from app.core import Actor

    order = submit(db, make_world(db))
    cancel_pending(db, order, '超过待确认期限自动关闭', Actor(principal_id=order.principal_id))
    order.data = {k: v for k, v in order.data.items() if k != 'closeReasonCode'}
    db.commit()
    response = reopen(api, admin_client, order)
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'ORDER_REOPEN_UNAVAILABLE'


def test_reopen_requires_payment_permission(db, api, client_factory, staff_factory):
    order = submit(db, make_world(db))
    expire(db, order)
    viewer = staff_client(client_factory, db, staff_factory(capabilities=['orders.manage']))
    assert reopen(api, viewer, order).status_code == 403
    detail = api(viewer, f'/admin/orders/{order.id}', method='GET').json()['data']
    assert 'ADMIN_REOPEN_TIMED_OUT_ORDER' not in detail['allowedActions']


def test_reopen_does_not_reactivate_an_order_during_account_deletion(db, api, admin_client, account_factory):
    world = make_world(db)
    order = submit(db, world)
    expire(db, order)
    account = account_factory()
    world.principal.account_id = account.id
    account.status = 'DELETION_PENDING'
    db.commit()
    response = reopen(api, admin_client, order)
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'ORDER_REOPEN_UNAVAILABLE'
    db.refresh(order)
    assert order.status == 'CANCELLED'


def test_expired_slot_can_be_changed_after_reopen_before_cash_is_allocated(db, api, admin_client):
    from test_delivery_closed_loop import configured_slot

    world = make_world(db)
    order = submit(db, world)
    world.slot.cutoff_at = utcnow() - timedelta(minutes=1)
    new_slot = configured_slot(db, campus=world.campus, offset_hours=48, name='备用时段')
    expire(db, order)
    response = reopen(api, admin_client, order)
    assert response.status_code == 200, response.text
    body = {'expectedVersion': response.json()['data']['version'],
            'contentRevision': order.content_revision, 'quoteId': order.quote_id,
            'receivedCents': order.total_cents, 'settlementKind': 'CASH_MANUAL'}
    path = f'/admin/orders/{order.id}'
    failed = api(admin_client, path + '/confirm-payment', body)
    assert failed.status_code == 409, failed.text
    assert failed.json()['error']['code'] == 'SLOT_CUTOFF'
    receipt_id = failed.json()['error']['details']['receiptId']
    changed = api(admin_client, path + '/slot-change', {
        'expectedVersion': body['expectedVersion'], 'slotId': new_slot.id, 'reason': '原时段已截单',
    })
    assert changed.status_code == 200, changed.text
    result = api(admin_client, path + '/confirm-payment', {
        **body, 'expectedVersion': changed.json()['data']['version'], 'receiptId': receipt_id,
    })
    assert result.status_code == 200, result.text
    db.expire_all()
    assert db.get(DeliverySlot, world.slot.id).occupied == 0
    assert db.get(DeliverySlot, new_slot.id).occupied == 1
    assert db.scalars(select(Payment)).one().receipt_id == receipt_id


def test_reopen_rejects_cleaned_final_pdf(db, api, admin_client):
    world = make_world(db)
    order = submit(db, world)
    expire(db, order)
    db.get(Asset, world.artifact.asset_id).deleted_at = utcnow()
    db.commit()
    response = reopen(api, admin_client, order)
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'FILE_NOT_READY'
    db.refresh(order)
    assert order.status == 'CANCELLED'


def test_retention_skips_a_locked_order_then_preserves_reopened_files(db, api, admin_client):
    from sqlalchemy.orm import Session
    from app.maintenance import _retention_files

    world = make_world(db)
    order = submit(db, world)
    expire(db, order)
    future = utcnow() + timedelta(days=40)
    with Session(db.get_bind()) as other:
        other.scalar(select(Order).where(Order.id == order.id).with_for_update())
        assert _retention_files(db, future) == 0
        db.commit()
    response = reopen(api, admin_client, order)
    assert response.status_code == 200, response.text
    db.expire_all()
    assert _retention_files(db, future) == 0
    assert db.get(Asset, world.artifact.asset_id).deleted_at is None


@pytest.mark.parametrize('reused', [False, True])
def test_reopen_reserves_original_coupon_or_leaves_order_cancelled(db, api, admin_client, account_factory, reused):
    from app.commerce import OrderSubmit, submit_order
    from app.core import Actor
    from app.growth import build_quote, reserve_coupon
    from test_growth import make_order

    world = make_world(db)
    account = account_factory()
    actor = Actor(account_id=account.id, principal_id=world.principal.id,
                  guest_principal_id=world.principal.id)
    campaign = make_campaign(db, amount=5, budget=10)
    coupon = make_coupon(db, account, campaign, amount=5)
    world.prep.checkout_account_id = account.id
    quote = build_quote(db, world.prep, [line('a4-one', world.artifact.id, 'A4', 1, 10)], actor)
    world.prep.quote_id = quote.id
    order = submit_order(db, actor, OrderSubmit.model_validate({
        'preparationId': world.prep.id, 'quoteId': quote.id,
        'previewAcks': [{'artifactId': world.artifact.id, 'sha256': world.artifact.sha256}],
        'contact': {'wechatNickname': '测试', 'phone': '13800000000'},
        'locationId': world.location.id, 'slotId': world.slot.id,
    }))
    assert coupon.status == 'RESERVED'
    expire(db, order)
    assert coupon.status == 'AVAILABLE'
    if reused:
        from test_growth import make_prep
        other_prep = make_prep(db, account)
        other_quote = build_quote(db, other_prep, [line('other', 'file', 'A4', 1, 10)], actor)
        other = make_order(db, other_prep, other_quote, account)
        reserve_coupon(db, other, other_quote)
        db.commit()

    response = reopen(api, admin_client, order)
    db.refresh(order)
    db.refresh(coupon)
    db.refresh(campaign)
    assert campaign.reserved_liability_cents == 5 and campaign.realized_discount_cents == 0
    if reused:
        assert response.status_code == 409, response.text
        assert response.json()['error']['code'] == 'COUPON_ALREADY_RESERVED'
        assert order.status == 'CANCELLED' and coupon.reserved_order_id == other.id
    else:
        assert response.status_code == 200, response.text
        assert order.status == 'PENDING_CONFIRMATION' and coupon.reserved_order_id == order.id
