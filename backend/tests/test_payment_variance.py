"""Actual cash settlement and acknowledged variance, on real PostgreSQL."""
import pytest
from sqlalchemy import func, select

from app.models import AuditLog, BusinessException, DeliverySlot, ExportJob, Order, Payment, Quote, Receipt
from test_delivery_closed_loop import APP_MODULES, make_world, submit


def payment_body(order, amount, **extra):
    return {
        'expectedVersion': order.version, 'contentRevision': order.content_revision,
        'quoteId': order.quote_id, 'receivedCents': amount,
        'settlementKind': 'CASH_MANUAL', **extra,
    }


@pytest.mark.parametrize('delta', [-1, 5])
def test_acknowledged_cash_is_settled_once_without_repricing(db, api, admin_client, delta):
    from app.delivery import _order_blockers
    from app.exports import _finance_context, _finance_value, _order_value

    world = make_world(db)
    order = submit(db, world)
    payable = order.total_cents
    actual = payable + delta
    path = f'/admin/orders/{order.id}/confirm-payment'
    body = payment_body(order, actual, paymentDifferenceAccepted=True, note='已核对并接受差额')
    response = api(admin_client, path, body, key='accepted-variance')
    assert response.status_code == 200, response.text
    result = response.json()['data']
    assert result['status'] == 'PENDING_PRINT'
    assert result['totalCents'] == payable
    assert result['settlement']['cashReceivedCents'] == actual
    assert result['settlement']['payableCents'] == payable
    assert result['settlement']['paymentDifferenceCents'] == delta
    assert result['settlement']['paymentDifferenceAccepted'] is True
    assert result['settlement']['note'] == body['note']
    db.expire_all()
    order = db.get(Order, order.id)
    assert db.get(Quote, order.quote_id).total_cents == payable
    assert order.cash_received_cents == actual
    receipt = db.scalar(select(Receipt).where(Receipt.order_id == order.id))
    payment = db.scalar(select(Payment).where(Payment.order_id == order.id))
    assert receipt.amount_cents == payment.amount_cents == actual
    assert receipt.status == 'ALLOCATED'
    assert payment.data['paymentDifferenceCents'] == delta
    assert db.get(DeliverySlot, world.slot.id).occupied == 1
    assert 'PAYMENT_REQUIRED' not in {item['code'] for item in _order_blockers(db, order)}
    audit = db.scalar(select(AuditLog).where(AuditLog.action == 'PAYMENT_CONFIRMED'))
    assert audit.data['paymentDifferenceAccepted'] is True
    assert audit.data['cashReceivedCents'] == actual
    context = _finance_context(db, [order])
    assert _finance_value(order, 'cashReceivedCents', context) == actual
    assert _finance_value(order, 'paymentDifferenceCents', context) == delta
    assert _finance_value(order, 'paymentNote', context) == body['note']
    assert _order_value(db, order, 'paymentDifferenceCents', None) == delta
    replay = api(admin_client, path, body, key='accepted-variance')
    assert replay.status_code == 200, replay.text
    duplicate = api(admin_client, path, {**body, 'expectedVersion': order.version})
    assert duplicate.status_code == 409, duplicate.text
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Payment)) == 1
    assert db.scalar(select(func.count()).select_from(Receipt)) == 1
    assert db.get(DeliverySlot, world.slot.id).occupied == 1


def test_difference_requires_explicit_acceptance_without_recording_cash(db, api, admin_client):
    order = submit(db, make_world(db))
    response = api(admin_client, f'/admin/orders/{order.id}/confirm-payment',
                   payment_body(order, order.total_cents + 1))
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'PAYMENT_DIFFERENCE_CONFIRMATION_REQUIRED'
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Receipt)) == 0
    assert db.get(Order, order.id).settlement_kind is None


def test_variance_reuses_existing_receipt_without_receiving_cash_again(db, api, admin_client):
    order = submit(db, make_world(db))
    actual = order.total_cents - 1
    receipt = Receipt(order_id=order.id, amount_cents=actual, staff_id=admin_client.staff.id)
    db.add(receipt)
    mismatch = BusinessException(order_id=order.id, type='PAYMENT_MISMATCH', status='IN_PROGRESS',
                                 reason='历史金额差异待核对', blocks_production=True)
    db.add(mismatch)
    db.commit()
    response = api(admin_client, f'/admin/orders/{order.id}/confirm-payment', payment_body(
        order, actual, receiptId=receipt.id, paymentDifferenceAccepted=True))
    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Receipt)) == 1
    assert db.get(Receipt, receipt.id).status == 'ALLOCATED'
    assert db.get(BusinessException, mismatch.id).status == 'RESOLVED'


@pytest.mark.parametrize('amount,kind', [(0, 'CASH_MANUAL'), (0, 'ZERO_PAYABLE')])
def test_variance_cannot_waive_a_positive_payable(db, api, admin_client, amount, kind):
    order = submit(db, make_world(db))
    response = api(admin_client, f'/admin/orders/{order.id}/confirm-payment', payment_body(
        order, amount, settlementKind=kind, paymentDifferenceAccepted=True))
    assert response.status_code == 409, response.text
    db.expire_all()
    assert db.get(Order, order.id).settlement_kind is None


def test_explicit_waiver_preserves_payable_and_releases_order_for_fulfillment(db, api, admin_client):
    from app.delivery import _order_blockers

    world = make_world(db)
    order = submit(db, world)
    payable = order.total_cents
    path = f'/admin/orders/{order.id}/confirm-payment'
    body = payment_body(order, 0, settlementKind='WAIVED_MANUAL', note='管理员确认免单')
    unconfirmed = api(admin_client, path, body)
    assert unconfirmed.status_code == 409, unconfirmed.text
    assert unconfirmed.json()['error']['code'] == 'PAYMENT_DIFFERENCE_CONFIRMATION_REQUIRED'

    body['paymentDifferenceAccepted'] = True
    response = api(admin_client, path, body, key='confirmed-waiver')
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['status'] == 'PENDING_PRINT'
    assert data['totalCents'] == payable
    assert data['settlement']['settlementKind'] == 'WAIVED_MANUAL'
    assert data['settlement']['cashReceivedCents'] == 0
    assert data['settlement']['paymentDifferenceCents'] == -payable
    assert data['settlement']['paymentDifferenceAccepted'] is True
    assert data['settlement']['note'] == body['note']
    replay = api(admin_client, path, body, key='confirmed-waiver')
    assert replay.status_code == 200, replay.text

    db.expire_all()
    order = db.get(Order, order.id)
    assert db.get(Quote, order.quote_id).total_cents == payable
    assert db.scalar(select(func.count()).select_from(Receipt)) == 0
    payment = db.scalars(select(Payment)).one()
    assert payment.kind == 'WAIVED_MANUAL' and payment.amount_cents == 0
    assert db.get(DeliverySlot, world.slot.id).occupied == 1
    assert 'PAYMENT_REQUIRED' not in {item['code'] for item in _order_blockers(db, order)}
    audit = db.scalar(select(AuditLog).where(AuditLog.action == 'PAYMENT_CONFIRMED'))
    assert audit.data['paymentDifferenceCents'] == -payable
    assert audit.actor_id == admin_client.staff.id


def test_waiver_requires_reconciling_any_unmatched_receipt(db, api, admin_client):
    order = submit(db, make_world(db))
    receipt = Receipt(order_id=order.id, amount_cents=order.total_cents, staff_id=admin_client.staff.id)
    db.add(receipt)
    db.commit()
    response = api(admin_client, f'/admin/orders/{order.id}/confirm-payment', payment_body(
        order, 0, settlementKind='WAIVED_MANUAL', paymentDifferenceAccepted=True))
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'RECEIPT_REVIEW_REQUIRED'
    db.expire_all()
    assert db.get(Receipt, receipt.id).status == 'UNMATCHED'
    assert db.get(Order, order.id).settlement_kind is None


def test_delivery_does_not_trust_an_unacknowledged_or_inconsistent_difference(db, api, admin_client):
    from app.delivery import _payment_confirmed
    order = submit(db, make_world(db))
    order.settlement_kind = 'CASH_MANUAL'
    order.cash_received_cents = order.total_cents - 1
    assert not _payment_confirmed(order)
    order.data = {**order.data, 'settlement': {
        'settlementKind': 'CASH_MANUAL', 'payableCents': order.total_cents,
        'cashReceivedCents': order.cash_received_cents, 'paymentDifferenceCents': -1,
        'paymentDifferenceAccepted': True,
    }}
    assert _payment_confirmed(order)
    order.data = {**order.data, 'settlement': {**order.data['settlement'], 'cashReceivedCents': 999}}
    assert not _payment_confirmed(order)


@pytest.mark.parametrize('delta', [-1, 5])
def test_refunds_and_exports_use_actual_cash_and_hide_internal_notes_from_customer(db, api, admin_client, delta):
    from app.after_sales import CaseInput, ReviewInput, create_case, review_case
    from app.commerce import order_view
    from app.core import Actor, DomainError
    from app.exports import _build_export
    from test_delivery_closed_loop import admin_client_actor

    order = submit(db, make_world(db))
    actual = order.total_cents + delta
    response = api(admin_client, f'/admin/orders/{order.id}/confirm-payment',
                   payment_body(order, actual, paymentDifferenceAccepted=True, note='内部核对备注'))
    assert response.status_code == 200, response.text
    db.expire_all()
    order = db.get(Order, order.id)
    customer_view = order_view(db, order, Actor(principal_id=order.principal_id))
    assert not customer_view['settlement'].get('note')
    fields = ['orderId', 'payableCents', 'cashReceivedCents', 'paymentDifferenceCents', 'paymentDifferenceAccepted', 'paymentNote']
    for kind in ('ORDERS', 'FINANCE'):
        job = ExportJob(kind=kind, status='QUEUED', data={'fields': fields, 'filters': {}})
        headers, rows, truncated = _build_export(db, job, 100)
        assert headers == fields
        assert rows == [[order.id, order.total_cents, actual, delta, True, '内部核对备注']]
        assert not truncated
    actor = admin_client_actor(admin_client.staff)
    case = create_case(db, actor, order, CaseInput(
        responsibility='CUSTOMER', solution='REFUND', reason='未开印取消'), admin=True)
    assert case.suggested_cents == actual
    with pytest.raises(DomainError) as error:
        review_case(db, actor, case, ReviewInput(expected_version=case.version,
            decision='APPROVE', approved_cents=actual + 1, adjustment_basis='验证实收上限', reason='审核'))
    assert error.value.code == 'REFUND_EXCEEDS_CASH'
    review_case(db, actor, case, ReviewInput(expected_version=case.version, decision='APPROVE', reason='全额退实收'))
    assert case.approved_cents == actual


@pytest.mark.parametrize('mode,actual,expected', [('PERCENT', 980, 98), ('PERCENT', 1050, 105), ('FIXED', 980, 200)])
def test_delivery_wages_use_net_cash_instead_of_original_quote(db, staff_factory, mode, actual, expected):
    from app.payroll import on_delivery
    from test_payroll import add_policy, make_courier, make_delivered
    courier = make_courier(staff_factory)
    add_policy(db, courier, mode=mode, fixed=200, rate=1000)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier, total=1000)
    order.cash_received_cents = actual
    entry = on_delivery(db, order, assignment, proof)
    assert entry.amount_cents == expected
    assert entry.snapshot['basisCents'] == actual


def test_failed_slot_keeps_actual_receipt_and_retries_without_double_cash(db, api, admin_client):
    world = make_world(db)
    order = submit(db, world)
    world.slot.occupied = world.slot.capacity
    db.commit()
    actual = order.total_cents + 5
    body = payment_body(order, actual, paymentDifferenceAccepted=True)
    path = f'/admin/orders/{order.id}/confirm-payment'
    failed = api(admin_client, path, body)
    assert failed.status_code == 409, failed.text
    receipt_id = failed.json()['error']['details']['receiptId']
    db.expire_all()
    assert db.get(Receipt, receipt_id).amount_cents == actual
    slot = db.get(DeliverySlot, world.slot.id)
    slot.occupied = 0
    db.commit()
    response = api(admin_client, path, {**body, 'receiptId': receipt_id})
    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Receipt)) == 1
    assert db.get(Order, order.id).cash_received_cents == actual


@pytest.mark.parametrize('accepted', ['true', 1, None])
def test_difference_acceptance_requires_a_boolean(db, api, admin_client, accepted):
    order = submit(db, make_world(db))
    response = api(admin_client, f'/admin/orders/{order.id}/confirm-payment',
                   payment_body(order, order.total_cents + 1, paymentDifferenceAccepted=accepted))
    assert response.status_code == 422, response.text
