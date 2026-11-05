"""Configured delivery fees belong to a quote, independent of product discounts."""
import pytest
from sqlalchemy import select

from app.config import get_config
from app.core import DomainError
from app.growth import build_quote
from app.growth_schemas import QuoteDTO
from app.models import AuditLog, Payment
from test_growth import base_config, line, make_prep, volume_policy

APP_MODULES = ['auth', 'commerce', 'growth', 'mail', 'media', 'production', 'after_sales', 'supplementary', 'payroll', 'benefits']


def test_delivery_config_is_public_versioned_and_audited(db, admin_client, api):
    for delivery in (250, 0, None):
        current = admin_client.get('/api/v1/admin/config').json()['data']
        result = api(admin_client, '/admin/config/prices', {
            'expectedVersion': current['sections']['prices']['version'],
            'delivery': delivery,
        }, method='PATCH')
        assert result.status_code == 200, result.text
        assert result.json()['data']['delivery'] == delivery
        public = admin_client.get('/api/v1/public/config').json()['data']
        assert public['prices']['delivery'] == delivery
    logs = db.scalars(select(AuditLog).where(
        AuditLog.action == 'CONFIG_UPDATED', AuditLog.target_id == 'prices')).all()
    assert len(logs) == 3
    assert all('delivery' in log.data['fields'] for log in logs)
    current = get_config(db, 'prices')
    for invalid in (-1, 0.5, '2'):
        result = api(admin_client, '/admin/config/prices', {
            'expectedVersion': current.version, 'delivery': invalid,
        }, method='PATCH')
        assert result.status_code == 422


def test_delivery_is_once_per_quote_and_excluded_from_product_discounts(db):
    base_config(db, volume_policy([{'id': 'free', 'minSheets': 1, 'payRateBps': 0}]))
    prices = get_config(db, 'prices')
    prices.data = {**prices.data, 'delivery': 250}
    prep = make_prep(db)
    lines = [line('one', 'file-one', 'A4', 10, 100),
             line('two', 'file-two', 'A4', 5, 100)]
    quote = build_quote(db, prep, lines)
    assert quote.subtotal_cents == 1500
    assert quote.discount_cents == 1500
    assert quote.total_cents == 250
    assert quote.data['deliveryFeeCents'] == 250
    QuoteDTO.model_validate(quote.data)
    prices.data = {**prices.data, 'delivery': 500}
    next_quote = build_quote(db, prep, lines)
    assert next_quote.total_cents == 500
    assert quote.data['deliveryFeeCents'] == 250
    assert quote.total_cents == 250


def test_legacy_delivery_defaults_free_but_explicit_blank_blocks_quote(db):
    base_config(db)
    prep = make_prep(db)
    lines = [line('one', 'file-one', 'A4', 1, 100)]
    quote = build_quote(db, prep, lines)
    assert quote.data['deliveryFeeCents'] == 0
    assert quote.total_cents == 100
    prices = get_config(db, 'prices')
    prices.data = {**prices.data, 'delivery': None}
    with pytest.raises(DomainError) as error:
        build_quote(db, prep, lines)
    assert error.value.code == 'PRICE_UNCONFIGURED'


def test_paid_delivery_survives_order_submission_payment_and_config_changes(db, admin_client):
    from app.core import Actor
    from test_delivery_closed_loop import make_world, submit, settle

    world = make_world(db)
    prices = get_config(db, 'prices')
    prices.data = {**prices.data, 'delivery': 250}
    world.quote = build_quote(db, world.prep, world.quote.data['lines'])
    world.prep.quote_id = world.quote.id
    db.commit()
    order = submit(db, world)
    assert order.total_cents == 260
    prices.data = {**prices.data, 'delivery': 500}
    db.commit()
    view = admin_client.get(f'/api/v1/admin/orders/{order.id}')
    assert view.status_code == 200, view.text
    assert view.json()['data']['quote']['deliveryFeeCents'] == 250
    assert view.json()['data']['totalCents'] == 260
    actor = Actor(staff_id=admin_client.staff.id, staff_role='ADMIN', capabilities={'*'})
    settle(db, order, actor)
    assert order.status == 'PENDING_PRINT'
    payment = db.scalar(select(Payment).where(Payment.order_id == order.id))
    assert payment.amount_cents == 260
