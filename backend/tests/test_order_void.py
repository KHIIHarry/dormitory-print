"""Admin void (作废) for orders that must not be produced or delivered.

Deleting an order through the trash is reserved for fully finished orders
(D-05.3).  During development and after a customer mistake an admin still
needs a way to close an unpaid / not-yet-produced order without corrupting
production or delivery state.
"""
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core import Actor, actor_dep
from app.models import DeliveryAssignment, Order, PrintTask, TrashEntry
from tests.auth_helpers import make_order
from tests.test_delivery import make_courier, make_ready_order

APP_MODULES = ['maintenance', 'delivery', 'commerce', 'production', 'growth',
               'mail', 'auth']


@pytest.fixture
def act_as(app):
    def apply(staff):
        app.dependency_overrides[actor_dep] = lambda: Actor(
            staff_id=staff.id, staff_role=staff.role,
            capabilities=set(staff.capabilities or []))
        return staff
    yield apply
    app.dependency_overrides.pop(actor_dep, None)


def seed_quote(db, order):
    """Give the order a quote snapshot that satisfies the OrderView response."""
    from app.models import Quote
    quote = db.get(Quote, order.quote_id)
    quote.data = {
        'id': quote.id, 'priceVersion': 1,
        'lines': [{'key': 'a4', 'relatedId': order.preparation_id, 'unit': 'SHEET',
                   'quantity': 1, 'unitPriceCents': order.total_cents,
                   'amountCents': order.total_cents, 'service': 'A4'}],
        'discounts': [], 'discountAllocations': [], 'selectionMode': 'AUTO',
        'selectedPromotionType': 'NONE', 'deliveryFeeCents': 0,
        'volumeDiscountCandidate': None, 'selectedEligibleSubtotalCents': 0,
        'checkoutAccountId': None, 'promotionRuleVersion': 'NONE'}
    db.commit()
    db.refresh(order)
    return order


def make_unpaid_order(db, status='PENDING_CONFIRMATION'):
    from app.models import Principal
    principal = Principal()
    db.add(principal)
    db.flush()
    order = make_order(db, principal.id, status=status)
    return seed_quote(db, order)


def void(api, client, order, reason='客户下错了'):
    return api(client, f'/admin/orders/{order.id}/void', {
        'expectedVersion': order.version, 'reason': reason})


def test_admin_voids_unpaid_order_and_keeps_audit_trail(
        db, api, client, act_as, staff_factory):
    staff = act_as(staff_factory(role='ADMIN'))
    order = make_unpaid_order(db)
    initial_version = order.version

    response = void(api, client, order)
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['status'] == 'CANCELLED'
    assert data['allowedActions'] is not None

    db.expire_all()
    stored = db.get(Order, order.id)
    assert stored.status == 'CANCELLED'
    assert stored.version > initial_version
    history = stored.data.get('history') or []
    assert any('作废' in item.get('title', '') for item in history)


def test_void_requires_a_reason(db, api, client, act_as, staff_factory):
    act_as(staff_factory(role='ADMIN'))
    order = make_unpaid_order(db)

    response = api(client, f'/admin/orders/{order.id}/void', {
        'expectedVersion': order.version, 'reason': '   '})
    assert response.status_code == 422, response.text
    assert response.json()['error']['code'] == 'REASON_REQUIRED'
    db.expire_all()
    assert db.get(Order, order.id).status == 'PENDING_CONFIRMATION'


def test_void_rejects_orders_that_already_received_money(
        db, api, client, act_as, staff_factory):
    act_as(staff_factory(role='ADMIN'))
    order = make_ready_order(db, status='PENDING_PRINT', total=1200, paid=True)

    response = void(api, client, order)
    assert response.status_code == 409, response.text
    assert response.json()['error']['details']['blockers'] == ['CASH_RECEIVED']
    db.expire_all()
    stored = db.get(Order, order.id)
    assert stored.status == 'PENDING_PRINT'
    assert stored.status != 'CANCELLED'


def test_void_rejects_order_already_in_production(
        db, api, client, act_as, staff_factory):
    act_as(staff_factory(role='ADMIN'))
    order = make_unpaid_order(db, status='PENDING_PRINT')
    from app.models import Artifact, Asset, OrderFile

    asset = Asset(purpose='FINAL', name='f.pdf', mime='application/pdf', size=1,
                  sha256=uuid4().hex, storage_key=f'fin/{uuid4()}', status='READY')
    db.add(asset)
    db.flush()
    file = OrderFile(order_id=order.id, revision=order.content_revision,
                     client_file_id='f1', asset_id=asset.id, kind='DOCUMENT',
                     binding_type='NONE', data={})
    db.add(file)
    db.flush()
    artifact = Artifact(preparation_id=order.preparation_id, asset_id=asset.id,
                        client_file_id='f1', sha256=uuid4().hex, pages=1, sheets=1,
                        settings={}, page_map=[])
    db.add(artifact)
    db.flush()
    db.add(PrintTask(order_id=order.id, file_id=file.id, artifact_id=artifact.id,
                     active=True, status='PRINTING', settings={}))
    db.commit()

    response = void(api, client, order)
    assert response.status_code == 409, response.text
    assert 'PRINT_STARTED' in response.json()['error']['details']['blockers']


def test_voided_order_can_then_be_soft_deleted_into_trash(
        db, api, client, act_as, staff_factory):
    act_as(staff_factory(role='ADMIN'))
    order = make_unpaid_order(db)
    assert void(api, client, order).status_code == 200

    db.expire_all()
    stored = db.get(Order, order.id)
    response = api(client, '/admin/trash', {
        'targetType': 'ORDER', 'targetId': stored.id,
        'expectedVersion': stored.version, 'reason': '清理测试数据'})
    assert response.status_code == 200, response.text
    entry = db.scalar(select(TrashEntry).where(TrashEntry.target_id == order.id))
    assert entry is not None


def test_void_releases_pending_delivery_assignment(
        db, api, client, act_as, staff_factory):
    from tests.test_trash_batches import attach_order, create_batch

    act_as(staff_factory(role='ADMIN'))
    courier = make_courier(staff_factory)
    order = make_ready_order(db, status='READY_FOR_DELIVERY', total=0)
    order.settlement_kind = 'ZERO_PAYABLE'
    db.commit()
    seed_quote(db, order)
    batch = create_batch(api, client, courier)
    attach_order(api, client, batch, order)
    db.expire_all()
    order = db.get(Order, order.id)

    response = void(api, client, order)
    assert response.status_code == 200, response.text
    db.expire_all()
    assignment = db.scalar(select(DeliveryAssignment).where(
        DeliveryAssignment.order_id == order.id))
    assert assignment.active is False
    assert assignment.status == 'CANCELLED'
