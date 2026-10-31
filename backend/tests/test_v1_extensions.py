"""Real N/F integration: employee benefits, payroll, supplementary fees.

This module deliberately exercises the actual HTTP routes and real workers.
It seeds only configuration, accounts, permissions and policies; every source
PDF and delivery photo is uploaded through ``POST /api/v1/assets`` and every
business transition goes through a route.  The old V1 suite remains in
``test_v1_integration.py`` and is not replaced by these focused extensions.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import sys

import pytest

pytest.importorskip('pymupdf')
pytest.importorskip('PIL')

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_v1_integration as base
from auth_helpers import make_account, make_admin, login_customer
from app.models import (
    Account,
    Asset,
    DeliveryAssignment,
    DeliveryBatch,
    DeliveryProof,
    DeliverySlot,
    Location,
    Order,
    Payment,
    Staff,
    SupplementaryCharge,
    SupplementaryMovement,
    WageEntry,
    utcnow,
)


APP_MODULES = [
    'auth', 'growth', 'mail', 'media', 'commerce', 'production', 'delivery',
    'after_sales', 'exports', 'maintenance', 'benefits', 'payroll',
    'supplementary',
]

BENEFITS = '扩展验收楼'
PHONE = '13800138000'


# ---------------------------------------------------------------------------
# Real account and HTTP helpers
# ---------------------------------------------------------------------------


def _account_client(client_factory, account):
    from sqlalchemy.orm import object_session

    # Routes use their own SessionLocal; make newly seeded accounts visible.
    session = object_session(account)
    if session is not None:
        session.commit()
    client = client_factory()
    response = login_customer(client, account.email)
    assert response.status_code == 200, response.text
    client.account = account
    return client


def _superadmin(db):
    staff = make_admin(db)
    db.commit()
    account = db.get(Account, staff.account_id)
    assert account is not None
    return staff, account


def _grant(api, admin_client, db, account, *, role='ADMIN',
           capabilities=('orders.view',), discount_level=2):
    response = api(admin_client, '/admin/staff', {
        'accountId': account.id,
        'role': role,
        'capabilities': list(capabilities),
        'discountLevel': discount_level,
        'reason': 'N/F 集成验收授权',
    }, key='grant-' + uuid4().hex)
    assert response.status_code == 200, response.text
    db.expire_all()
    staff = db.scalar(select(Staff).where(Staff.account_id == account.id))
    assert staff is not None
    return staff


def _location_slot(db, *, capacity=5):
    now = utcnow()
    location = Location(name=f'{BENEFITS}-{uuid4().hex[:6]}', x=0.4, y=0.6)
    slot = DeliverySlot(
        date=(now + timedelta(days=1)).strftime('%Y-%m-%d'),
        name='扩展验收时段', starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=1, hours=1), capacity=capacity,
    )
    db.add_all([location, slot])
    db.commit()
    return location, slot


def _quote_and_submit(client, api, db, location, slot, prefix, *,
                      pages=3, binding='CORNER', allow_zero=False):
    pipeline = base.start_pipeline(
        client, api, db, location, slot, prefix, pages=pages, binding=binding,
        allow_zero=allow_zero,
    )
    response = api(client, '/orders', base.submit_body(pipeline),
                   key=f'{prefix}-order')
    assert response.status_code == 200, response.text
    data = response.json()['data']
    return SimpleNamespace(
        pipeline=pipeline, order=data['order'], order_key=f'{prefix}-order',
        access_token=data['accessToken'], client=client,
    )


def _set_benefits(admin_client, api, grades):
    current = api(admin_client, '/admin/config/benefits', method='GET')
    assert current.status_code == 200, current.text
    version = current.json()['data']['version']
    response = api(admin_client, '/admin/config/benefits', {
        'expectedVersion': version,
        'grades': grades,
        'reason': 'N/F 员工等级验收',
    }, method='PATCH', key='benefits-' + uuid4().hex)
    assert response.status_code == 200, response.text
    return response.json()['data']


def _set_volume(admin_client, api, *, pay_rate_bps=9000):
    current = api(admin_client, '/admin/config/volume-discounts', method='GET')
    assert current.status_code == 200, current.text
    view = current.json()['data']
    response = api(admin_client, '/admin/config/volume-discounts', {
        'id': view['id'],
        'version': view['version'],
        'enabled': True,
        'effectiveAt': view.get('effectiveAt'),
        'description': 'N/F 阶梯验收',
        'tiers': [{'id': 'tier-1', 'minSheets': 1,
                   'payRateBps': pay_rate_bps}],
        'expectedVersion': view['version'],
        'reason': 'N/F 阶梯验收',
    }, method='PATCH', key='volume-' + uuid4().hex)
    assert response.status_code == 200, response.text
    return response.json()['data']


def _set_payroll(admin_client, api, *, mode='PERCENT', rate_bps=1000,
                 fixed_cents=0):
    current = api(admin_client, '/admin/payroll/policies', method='GET')
    assert current.status_code == 200, current.text
    default = current.json()['data'].get('defaultPolicy')
    body = {
        'mode': mode,
        'fixedCents': fixed_cents,
        'rateBps': rate_bps,
        'enabled': True,
        'expectedVersion': int(default['version']) if default else 0,
        'reason': 'N/F 默认工资策略',
    }
    response = api(admin_client, '/admin/payroll/policies/default', body,
                   method='PUT', key='payroll-' + uuid4().hex)
    assert response.status_code == 200, response.text
    return response.json()['data']


def _settle(admin_client, api, order, *, key, kind='CASH_MANUAL'):
    response = api(admin_client, f'/admin/orders/{order["id"]}/confirm-payment', {
        'expectedVersion': order['version'],
        'contentRevision': order['contentRevision'],
        'quoteId': order['currentQuoteId'],
        'receivedCents': order['totalCents'],
        'settlementKind': kind,
        'note': 'N/F 实际人工收款',
    }, key=key)
    return response


def _order_hydrated(api, admin_client, order_id):
    return base.admin_order(api, admin_client, order_id)


def _deliver_one(api, admin_client, db, order, courier_client,
                 courier_staff, key):
    """Packed order -> batch -> one real proof; returns the delivered view."""
    batch = base.create_batch(api, admin_client, courier_staff,
                              f'N/F 单件批次 {uuid4().hex[:6]}')
    base.add_order_to_batch(api, admin_client, batch, order['id'],
                            f'{key}-add')
    base.publish_and_start(api, admin_client, db, batch['id'], key)
    assignment = base.active_assignment(db, order['id'])
    assert assignment is not None
    photo = base.upload_proof(courier_client, assignment)
    base.submit_proof(api, courier_client, order['id'], assignment,
                      photo['assetId'], f'{key}-proof')
    return base.admin_order(api, admin_client, order['id'])


def _two_packed_orders(api, admin_client, db, client, location, slot, key):
    orders = []
    for index in range(2):
        seed = _quote_and_submit(
            client, api, db, location, slot, f'{key}-{index}',
        )
        settled = _settle(admin_client, api, seed.order,
                          key=f'{key}-settle-{index}')
        assert settled.status_code == 200, settled.text
        view = _order_hydrated(api, admin_client, seed.order['id'])
        base.run_print_to_packed(api, admin_client, view)
        orders.append(view)
    return orders


def _proof_photo_for_batch(courier_client, assignment):
    return base.upload_proof(courier_client, assignment)


def _bulk_body(api, admin_client, db, orders, assignments, photo_asset_id):
    views = [base.admin_order(api, admin_client, order['id'])
             for order in orders]
    items = []
    for view, assignment in zip(views, assignments):
        items.append({
            'orderId': view['id'],
            'deliveryAssignmentId': assignment.id,
            'expectedAssignmentVersion': assignment.version,
            'expectedVersion': view['version'],
            'position': {
                'latitude': 31.2304,
                'longitude': 121.4737,
                'accuracyMeters': 12,
                'capturedAt': (utcnow() - timedelta(seconds=10)).isoformat(),
                'coordinateSystem': 'WGS84',
            },
            'locationFailureCode': None,
            'skipLocationReason': None,
        })
    return {
        'photoAssetId': photo_asset_id,
        'skipPhotoReason': None,
        'placementNote': 'N/F 同楼批量交付',
        'items': items,
    }


def _wage_rows(db, order_id):
    db.expire_all()
    return list(db.scalars(select(WageEntry).where(
        WageEntry.order_id == order_id).order_by(WageEntry.created_at)))


# ---------------------------------------------------------------------------
# N: employee grade / benefits entitlement
# ---------------------------------------------------------------------------


def test_employee_grade_two_applies_to_whole_order_without_stacking_and_locks_submitted_quote(
        db, api, client_factory):
    base.seed_config(db)
    _, super_account = _superadmin(db)
    admin_client = _account_client(client_factory, super_account)
    _set_benefits(admin_client, api, [None, 5000, None, None, None, None])
    _set_volume(admin_client, api)

    employee = make_account(db, display_name='二级员工')
    employee_client = _account_client(client_factory, employee)
    grant = _grant(api, admin_client, db, employee, discount_level=2)
    assert grant.discount_level == 2

    location, slot = _location_slot(db)
    pipeline = base.start_pipeline(employee_client, api, db, location, slot,
                                   'grade')
    quote = pipeline.quote
    assert quote['selectedPromotionType'] == 'STAFF_GRADE', quote
    staff_discount = next(
        item for item in quote['discounts']
        if item['kind'] == 'STAFF_GRADE'
    )
    assert staff_discount['discountLevel'] == 2
    assert staff_discount['reductionBps'] == 5000
    binding_lines = [line for line in quote['lines']
                     if line['service'] == 'BINDING']
    assert binding_lines and binding_lines[0]['amountCents'] > 0
    assert quote['discountCents'] == staff_discount['amountCents']
    assert quote['totalCents'] == quote['subtotalCents'] - quote['discountCents']
    # The 50% staff grade beats the configured 10% volume tier and is applied
    # once to the full subtotal, including the binding line; it is not stacked.
    assert quote['totalCents'] > 0
    assert quote['discountCents'] > quote['subtotalCents'] * 1000 // 10000
    assert quote['discountAllocations'], quote

    # Revoking the grade invalidates an unsubmitted quote at the real submit
    # route, rather than silently accepting a now-ineligible price.
    _set_benefits(admin_client, api, [None] * 6)
    rejected = api(employee_client, '/orders', base.submit_body(pipeline),
                   key='grade-revoked')
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()['error']['code'] == 'QUOTE_EXPIRED'

    # Restore the grade, obtain a fresh quote and submit it.  Revoking the grade
    # after submission must not alter the locked historical quote or payment.
    _set_benefits(admin_client, api, [None, 5000, None, None, None, None])
    seed = _quote_and_submit(employee_client, api, db, location, slot, 'grade2')
    order = seed.order
    submitted_quote_id = order['currentQuoteId']
    submitted_total = order['totalCents']
    assert submitted_total == seed.pipeline.quote['totalCents']
    _set_benefits(admin_client, api, [None] * 6)

    paid = _settle(admin_client, api, order, key='grade-pay')
    assert paid.status_code == 200, paid.text
    locked = _order_hydrated(api, admin_client, order['id'])
    assert locked['status'] == 'PENDING_PRINT'
    assert locked['currentQuoteId'] == submitted_quote_id
    assert locked['totalCents'] == submitted_total
    db.expire_all()
    assert db.get(Order, order['id']).settlement_kind == 'CASH_MANUAL'
    assert db.scalar(select(func.count(Payment.id)).where(
        Payment.order_id == order['id'])) == 1
    assert db.get(DeliverySlot, slot.id).occupied == 1


def test_superadmin_free_quote_uses_zero_settlement_and_keeps_zero_total_locked(
        db, api, client_factory):
    base.seed_config(db)
    _, super_account = _superadmin(db)
    admin_client = _account_client(client_factory, super_account)
    location, slot = _location_slot(db)
    seed = _quote_and_submit(admin_client, api, db, location, slot, 'free',
                             allow_zero=True)
    order = seed.order
    assert order['totalCents'] == 0, order
    assert seed.pipeline.quote['selectedPromotionType'] == 'STAFF_GRADE'
    assert seed.pipeline.quote['discounts'][0]['source'] == 'SUPER_ADMIN'

    paid = _settle(admin_client, api, order, key='free-pay',
                   kind='ZERO_PAYABLE')
    assert paid.status_code == 200, paid.text
    locked = _order_hydrated(api, admin_client, order['id'])
    assert locked['status'] == 'PENDING_PRINT'
    assert locked['totalCents'] == 0
    db.expire_all()
    assert db.get(Order, order['id']).settlement_kind == 'ZERO_PAYABLE'
    assert db.get(Order, order['id']).total_cents == 0
    assert db.get(DeliverySlot, slot.id).occupied == 1


# ---------------------------------------------------------------------------
# F: payroll default, shared batch proof and supplementary fees
# ---------------------------------------------------------------------------


def test_default_percent_payroll_and_one_shared_proof_for_two_orders(
        db, api, client_factory):
    base.seed_config(db)
    _, super_account = _superadmin(db)
    admin_client = _account_client(client_factory, super_account)
    policy = _set_payroll(admin_client, api)
    assert policy['mode'] == 'PERCENT' and policy['rateBps'] == 1000

    courier_account = make_account(db, display_name='批量配送员')
    courier_client = _account_client(client_factory, courier_account)
    courier = _grant(api, admin_client, db, courier_account, role='COURIER',
                     capabilities=(), discount_level=None)

    location, slot = _location_slot(db)
    guest = client_factory()
    base.guest(guest)
    orders = _two_packed_orders(api, admin_client, db, guest, location, slot,
                                'batch')
    batch = base.create_batch(api, admin_client, courier,
                              f'N/F 批量批次 {uuid4().hex[:6]}')
    for index, order in enumerate(orders):
        added = base.add_order_to_batch(
            api, admin_client, batch, order['id'], f'batch-add-{index}')
        batch = added['batches'][0]
    base.publish_and_start(api, admin_client, db, batch['id'], 'batch')

    db.expire_all()
    assignments = []
    for order in orders:
        assignment = db.scalar(select(DeliveryAssignment).where(
            DeliveryAssignment.order_id == order['id'],
            DeliveryAssignment.active.is_(True)))
        assert assignment is not None
        assignments.append(assignment)
    photo = _proof_photo_for_batch(courier_client, assignments[0])
    body = _bulk_body(api, admin_client, db, orders, assignments,
                      photo['assetId'])
    key = 'bulk-proof-' + uuid4().hex
    response = api(courier_client,
                   f'/courier/batches/{batch["id"]}/delivery-proofs/batch',
                   body, key=key)
    assert response.status_code == 200, response.text
    delivered = response.json()['data']
    assert delivered['batchId'] == batch['id']
    assert len(delivered['proofs']) == 2
    assert {proof['photoAssetId'] for proof in delivered['proofs']} == {
        photo['assetId']}
    assert {proof['orderId'] for proof in delivered['proofs']} == {
        order['id'] for order in orders}
    assert {item['status'] for item in delivered['orders']} == {'DELIVERED'}
    db.expire_all()
    assert db.get(DeliveryBatch, batch['id']).status == 'COMPLETED'
    assert db.scalar(select(func.count(DeliveryProof.id)).where(
        DeliveryProof.photo_asset_id == photo['assetId'])) == 2
    for order in orders:
        rows = _wage_rows(db, order['id'])
        delivery = [row for row in rows if row.kind == 'DELIVERY']
        assert len(delivery) == 1, rows
        assert delivery[0].amount_cents == order['totalCents'] * 1000 // 10000
        assert delivery[0].source_key == f'DELIVERY:{order["id"]}'

    # Same idempotency key replays the proofs without a second wage/referral.
    replay = api(courier_client,
                 f'/courier/batches/{batch["id"]}/delivery-proofs/batch',
                 body, key=key)
    assert replay.status_code == 200, replay.text
    assert {proof['id'] for proof in replay.json()['data']['proofs']} == {
        proof['id'] for proof in delivered['proofs']}
    assert db.scalar(select(func.count(DeliveryProof.id)).where(
        DeliveryProof.photo_asset_id == photo['assetId'])) == 2
    assert db.scalar(select(func.count(WageEntry.id)).where(
        WageEntry.source_key.in_(
            [f'DELIVERY:{order["id"]}' for order in orders]))) == 2


def test_supplementary_charges_adjust_real_payroll_only_when_flagged(
        db, api, client_factory):
    base.seed_config(db)
    _, super_account = _superadmin(db)
    admin_client = _account_client(client_factory, super_account)
    policy = _set_payroll(admin_client, api)
    assert policy['rateBps'] == 1000
    courier_account = make_account(db, display_name='补充费配送员')
    courier_client = _account_client(client_factory, courier_account)
    courier = _grant(api, admin_client, db, courier_account, role='COURIER',
                     capabilities=(), discount_level=None)

    location, slot = _location_slot(db)
    guest = client_factory()
    base.guest(guest)
    seed = _quote_and_submit(guest, api, db, location, slot, 'supp')
    order = seed.order
    assert _settle(admin_client, api, order, key='supp-pay').status_code == 200
    current = _order_hydrated(api, admin_client, order['id'])

    # Mandatory purpose and an exact strict boolean are enforced by the route.
    missing_purpose = api(
        admin_client, f'/admin/orders/{order["id"]}/supplementary-charges',
        {'amountCents': 500, 'includeInPayroll': True,
         'expectedVersion': current['version']},
        key='supp-missing-purpose')
    assert missing_purpose.status_code == 422, missing_purpose.text
    missing_flag = api(
        admin_client, f'/admin/orders/{order["id"]}/supplementary-charges',
        {'purpose': '装订补收', 'amountCents': 500,
         'expectedVersion': current['version']},
        key='supp-missing-flag')
    assert missing_flag.status_code == 422, missing_flag.text

    created = api(
        admin_client, f'/admin/orders/{order["id"]}/supplementary-charges',
        {'purpose': '装订补收', 'amountCents': 500,
         'includeInPayroll': True, 'expectedVersion': current['version']},
        key='supp-create')
    assert created.status_code == 200, created.text
    charge = created.json()['data']
    assert charge['purpose'] == '装订补收'
    assert charge['includeInPayroll'] is True
    receipt = api(
        admin_client, f'/admin/supplementary-charges/{charge["id"]}/receipts',
        {'amountCents': 500, 'reason': '实际补收现金',
         'expectedVersion': charge['version']}, key='supp-receipt')
    assert receipt.status_code == 200, receipt.text

    # The pre-delivery fee is part of the frozen delivery wage basis.
    base.run_print_to_packed(api, admin_client, current)
    delivered = _deliver_one(api, admin_client, db, current, courier_client,
                             courier, 'supp-delivery')
    assert delivered['status'] == 'DELIVERED'
    rows = _wage_rows(db, order['id'])
    delivery = [row for row in rows if row.kind == 'DELIVERY']
    assert len(delivery) == 1, rows
    expected_base = (order['totalCents'] + 500) * 1000 // 10000
    assert delivery[0].amount_cents == expected_base, delivery[0].amount_cents

    # A post-delivery receipt is a percentage adjustment using the same frozen
    # policy; a later fee refund creates the opposite signed adjustment.
    after = _order_hydrated(api, admin_client, order['id'])
    extra = api(
        admin_client, f'/admin/orders/{order["id"]}/supplementary-charges',
        {'purpose': '加急补收', 'amountCents': 300,
         'includeInPayroll': True, 'expectedVersion': after['version']},
        key='supp-after')
    assert extra.status_code == 200, extra.text
    extra_charge = extra.json()['data']
    extra_receipt = api(
        admin_client,
        f'/admin/supplementary-charges/{extra_charge["id"]}/receipts',
        {'amountCents': 300, 'reason': '加急现金',
         'expectedVersion': extra_charge['version']}, key='supp-after-receipt')
    assert extra_receipt.status_code == 200, extra_receipt.text
    db.expire_all()
    supplementary = [row for row in _wage_rows(db, order['id'])
                     if row.kind == 'SUPPLEMENTARY_ADJUSTMENT']
    assert len(supplementary) == 1, supplementary
    assert supplementary[0].amount_cents == 30
    refunded = api(
        admin_client,
        f'/admin/supplementary-charges/{extra_charge["id"]}/refunds',
        {'amountCents': 100, 'reason': '加急部分退费',
         'expectedVersion': extra_receipt.json()['data']['version']},
        key='supp-after-refund')
    assert refunded.status_code == 200, refunded.text
    db.expire_all()
    adjustments = [row for row in _wage_rows(db, order['id'])
                   if row.kind == 'SUPPLEMENTARY_ADJUSTMENT']
    assert len(adjustments) == 2, adjustments
    assert adjustments[-1].amount_cents == -10, adjustments[-1].amount_cents
    view = _order_hydrated(api, admin_client, order['id'])
    assert view['supplementary']['original']['totalCents'] == order['totalCents']
    assert view['supplementary']['extras']['grossReceivedCents'] == 800
    assert view['supplementary']['extras']['refundedCents'] == 100

    # includeInPayroll=false must not create any wage row and must be visible as
    # a skipped payroll decision in the append-only movement.
    unchecked = api(
        admin_client, f'/admin/orders/{order["id"]}/supplementary-charges',
        {'purpose': '不计工资补收', 'amountCents': 200,
         'includeInPayroll': False, 'expectedVersion': view['version']},
        key='supp-unchecked')
    assert unchecked.status_code == 200, unchecked.text
    unchecked_charge = unchecked.json()['data']
    unchecked_receipt = api(
        admin_client,
        f'/admin/supplementary-charges/{unchecked_charge["id"]}/receipts',
        {'amountCents': 200, 'reason': '不计工资现金',
         'expectedVersion': unchecked_charge['version']},
        key='supp-unchecked-receipt')
    assert unchecked_receipt.status_code == 200, unchecked_receipt.text
    db.expire_all()
    skipped = db.scalar(select(SupplementaryMovement).where(
        SupplementaryMovement.charge_id == unchecked_charge['id']))
    assert skipped.data['payroll']['status'] == 'SKIPPED'
    assert len([row for row in _wage_rows(db, order['id'])
                if row.kind == 'SUPPLEMENTARY_ADJUSTMENT']) == 2

    # FIXED policies never receive percentage supplementary adjustments.
    _set_payroll(admin_client, api, mode='FIXED', fixed_cents=500,
                 rate_bps=0)
    second_guest = client_factory()
    base.guest(second_guest)
    second = _quote_and_submit(second_guest, api, db, location, slot, 'fixed')
    assert _settle(admin_client, api, second.order,
                   key='fixed-pay').status_code == 200
    fixed_view = _order_hydrated(api, admin_client, second.order['id'])
    base.run_print_to_packed(api, admin_client, fixed_view)
    fixed_delivered = _deliver_one(
        api, admin_client, db, fixed_view, courier_client, courier, 'fixed-del')
    assert fixed_delivered['status'] == 'DELIVERED'
    fixed_charge = api(
        admin_client, f'/admin/orders/{second.order["id"]}/supplementary-charges',
        {'purpose': '固定工资补收', 'amountCents': 300,
         'includeInPayroll': True,
         'expectedVersion': fixed_delivered['version']},
        key='fixed-supp')
    assert fixed_charge.status_code == 200, fixed_charge.text
    fixed_charge_view = fixed_charge.json()['data']
    fixed_receipt = api(
        admin_client,
        f'/admin/supplementary-charges/{fixed_charge_view["id"]}/receipts',
        {'amountCents': 300, 'reason': '固定工资现金',
         'expectedVersion': fixed_charge_view['version']},
        key='fixed-supp-receipt')
    assert fixed_receipt.status_code == 200, fixed_receipt.text
    fixed_rows = _wage_rows(db, second.order['id'])
    assert [row.amount_cents for row in fixed_rows
            if row.kind == 'DELIVERY'] == [500]
    assert not [row for row in fixed_rows
                if row.kind == 'SUPPLEMENTARY_ADJUSTMENT']


# ---------------------------------------------------------------------------
# Original after-sales refund + payroll hook, original quote immutable
# ---------------------------------------------------------------------------


def test_delivered_order_after_sales_refund_uses_payroll_hook_and_keeps_payment(
        db, api, client_factory):
    base.seed_config(db)
    _, super_account = _superadmin(db)
    admin_client = _account_client(client_factory, super_account)
    _set_payroll(admin_client, api)
    courier_account = make_account(db, display_name='售后配送员')
    courier_client = _account_client(client_factory, courier_account)
    courier = _grant(api, admin_client, db, courier_account, role='COURIER',
                     capabilities=(), discount_level=None)

    location, slot = _location_slot(db)
    guest = client_factory()
    base.guest(guest)
    seed = _quote_and_submit(guest, api, db, location, slot, 'aftersale')
    order = seed.order
    original_total = order['totalCents']
    original_quote = order['currentQuoteId']
    assert _settle(admin_client, api, order,
                   key='aftersale-pay').status_code == 200
    paid = _order_hydrated(api, admin_client, order['id'])
    base.run_print_to_packed(api, admin_client, paid)
    delivered = _deliver_one(api, admin_client, db, paid, courier_client,
                             courier, 'aftersale-del')
    assert delivered['status'] == 'DELIVERED'
    db.expire_all()
    base_wage = db.scalar(select(WageEntry).where(
        WageEntry.order_id == order['id'],
        WageEntry.kind == 'DELIVERY'))
    assert base_wage is not None
    base_amount = base_wage.amount_cents
    assert base_amount == original_total * 1000 // 10000

    created = api(admin_client,
                  f'/admin/orders/{order["id"]}/after-sales', {
                      'responsibility': 'MERCHANT',
                      'solution': 'REFUND',
                      'reason': '商家承担整单退款验收',
                      'affectedItems': [],
                      'expectedVersion': delivered['version'],
                  }, key='aftersale-case')
    assert created.status_code == 200, created.text
    case = created.json()['data']
    assert case['suggestedCents'] == original_total
    reviewed = api(admin_client,
                   f'/admin/after-sales/{case["id"]}/review', {
                       'expectedVersion': case['version'],
                       'decision': 'APPROVE',
                       'solution': 'REFUND',
                       'approvedCents': case['suggestedCents'],
                       'adjustmentBasis': '商家责任整单退款',
                       'reason': '审批通过',
                   }, key='aftersale-review')
    assert reviewed.status_code == 200, reviewed.text
    case = reviewed.json()['data']
    assert case['status'] == 'REFUND_APPROVED'
    recorded = api(admin_client,
                   f'/admin/after-sales/{case["id"]}/record-refund', {
                       'expectedVersion': case['version'],
                       'amountCents': case['approvedCents'],
                       'reason': '线下原路退款',
                   }, key='aftersale-refund')
    assert recorded.status_code == 200, recorded.text

    db.expire_all()
    refund_row = db.scalar(select(WageEntry).where(
        WageEntry.order_id == order['id'],
        WageEntry.kind == 'REFUND_ADJUSTMENT'))
    assert refund_row is not None
    assert refund_row.amount_cents == -base_amount
    locked = _order_hydrated(api, admin_client, order['id'])
    assert locked['status'] == 'DELIVERED'
    assert locked['totalCents'] == original_total
    assert locked['currentQuoteId'] == original_quote
    db.expire_all()
    assert db.get(Order, order['id']).refunded_cents == original_total
    assert db.get(Order, order['id']).total_cents == original_total
    assert db.scalar(select(func.count(Payment.id)).where(
        Payment.order_id == order['id'])) == 1


# ---------------------------------------------------------------------------
# Unified account other-device + cross-account asset/contact isolation
# ---------------------------------------------------------------------------


def test_second_device_and_cross_account_asset_isolation(
        db, api, client_factory):
    base.seed_config(db)
    owner = make_account(db, display_name='资产所有者')
    stranger = make_account(db, display_name='无关客户')
    owner_client = _account_client(client_factory, owner)
    stranger_client = _account_client(client_factory, stranger)

    uploaded = base.upload(owner_client, base.make_pdf(2),
                           name='owner-only.pdf', mime='application/pdf',
                           purpose='PRINT_SOURCE')
    assert uploaded['status'] == 'QUEUED'
    base.drain_job(db, ['ASSET'])
    asset_id = uploaded['assetId']
    denied = api(stranger_client, f'/assets/{asset_id}', method='GET')
    assert denied.status_code == 404, denied.text
    denied_bytes = stranger_client.get(f'/api/v1/assets/{asset_id}/content')
    assert denied_bytes.status_code == 404, denied_bytes.text
    bad_prep = api(stranger_client, '/preparations', {
        'files': [{
            'clientFileId': 'steal-1', 'assetId': asset_id,
            'kind': 'DOCUMENT',
            'segments': [{
                'clientSegmentId': 'steal-seg',
                'ranges': [{'from': 1, 'to': 2}],
                'colorMode': 'BW', 'sides': 'SINGLE', 'flip': 'NONE',
                'copies': 1,
            }], 'excludedPages': [], 'bindingType': 'NONE',
        }],
        'promotionIntent': {'selectionMode': 'AUTO'},
    }, key='steal-prep')
    assert bad_prep.status_code == 404, bad_prep.text

    location, slot = _location_slot(db)
    seed = _quote_and_submit(owner_client, api, db, location, slot, 'owner')
    order_id = seed.order['id']
    second_device = _account_client(client_factory, owner)
    visible = api(second_device, f'/orders/{order_id}', method='GET')
    assert visible.status_code == 200, visible.text
    hidden = api(stranger_client, f'/orders/{order_id}', method='GET')
    assert hidden.status_code == 404, hidden.text
    listing = api(stranger_client, '/customer/orders', method='GET')
    assert listing.status_code == 200, listing.text
    assert listing.json()['data']['items'] == []
    assert PHONE not in listing.text
