"""F01/F02A/F03A supplementary fees: real PostgreSQL + HTTP behaviour.

The fee ledger is independent from the original order/quote/payment ledger.  It
never rewrites the original quote, original cash counters or fulfilment status.
"""
from __future__ import annotations

import secrets
import sys
import types
from datetime import timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core import token_hash
from app.models import (
    AccessGrant,
    Account,
    AuthSession,
    Order,
    Payment,
    Quote,
    SupplementaryCharge,
    SupplementaryDispute,
    SupplementaryMovement,
    WageEntry,
    utcnow,
)
from auth_helpers import make_order

APP_MODULES = ['supplementary']


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _order(db, account, *, status='DELIVERED', settlement='CASH_MANUAL', total=5000,
           cash=0, deleted=False):
    order = make_order(db, account.principal_id, status=status, total_cents=total)
    order.status = status
    order.settlement_kind = settlement
    order.cash_received_cents = cash
    if deleted:
        order.deleted_at = utcnow()
    db.commit()
    db.refresh(order)
    return order


def _charge(api, client, order, **overrides):
    body = {
        'purpose': '排版与装订',
        'amountCents': 1000,
        'includeInPayroll': True,
        'expectedVersion': order.version,
    }
    body.update(overrides)
    response = api(client, f'/admin/orders/{order.id}/supplementary-charges', body)
    return response


def _created(api, client, order, **overrides):
    response = _charge(api, client, order, **overrides)
    assert response.status_code == 200, response.text
    return response.json()['data']


def _receipt(api, client, charge_id, amount, expected_version, reason='现金收款', key=None):
    return api(client, f'/admin/supplementary-charges/{charge_id}/receipts', {
        'amountCents': amount, 'reason': reason, 'expectedVersion': expected_version,
    }, key=key)


def _refund(api, client, charge_id, amount, expected_version, reason='现金退款', key=None):
    return api(client, f'/admin/supplementary-charges/{charge_id}/refunds', {
        'amountCents': amount, 'reason': reason, 'expectedVersion': expected_version,
    }, key=key)


def _cancel(api, client, charge_id, expected_version, reason='不再收取'):
    return api(client, f'/admin/supplementary-charges/{charge_id}/cancel', {
        'reason': reason, 'expectedVersion': expected_version,
    })


def _customer_client(client_factory, db, account):
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(kind='CUSTOMER', account_id=account.id, principal_id=account.principal_id,
                       token_hash=token_hash(token), expires_at=utcnow() + timedelta(days=1)))
    db.commit()
    client = client_factory()
    client.cookies.set('dormprint_account_session', token)
    client.account = account
    return client


def _staff_client(client_factory, db, staff):
    # Unified N05 identity: employee authority comes from a normal verified
    # customer-account session linked to the active Staff row.
    account = db.get(Account, staff.account_id)
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(kind='CUSTOMER', account_id=account.id, principal_id=account.principal_id,
                       session_version=account.session_version, token_hash=token_hash(token),
                       expires_at=utcnow() + timedelta(days=1)))
    db.commit()
    client = client_factory()
    client.cookies.set('dormprint_account_session', token)
    client.staff = staff
    client.account = account
    return client


def _refresh(db, order, charge):
    db.expire_all()
    return db.get(Order, order.id), db.get(SupplementaryCharge, charge['id'])


# ---------------------------------------------------------------------------
# Creation gate: settled/paid or delivered, never a closed/deleted order
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('status,settlement,cash,deleted,expected,code', [
    ('PENDING_PRINT', 'CASH_MANUAL', 0, False, 200, None),
    ('PENDING_PRINT', None, 500, False, 200, None),        # already paid
    ('DELIVERED', None, 0, False, 200, None),              # delivered is allowed
    ('PENDING_PRINT', None, 0, False, 409, 'SETTLEMENT_REQUIRED'),
    ('CANCELLED', 'CASH_MANUAL', 0, False, 409, 'ORDER_CLOSED'),
    ('CLOSED', 'CASH_MANUAL', 0, False, 409, 'ORDER_CLOSED'),
    ('COMPLETED', 'CASH_MANUAL', 0, False, 409, 'ORDER_CLOSED'),
    ('DELIVERED', None, 0, True, 404, 'NOT_FOUND'),
])
def test_creation_requires_original_settlement_or_delivery(
        admin_client, api, db, customer_client, status, settlement, cash, deleted, expected, code):
    order = _order(db, customer_client.account, status=status, settlement=settlement,
                   cash=cash, deleted=deleted)
    response = _charge(api, admin_client, order)
    assert response.status_code == expected, response.text
    if code:
        assert response.json()['error']['code'] == code
    else:
        assert response.json()['data']['status'] == 'ACTIVE'


def test_fee_creation_never_rewrites_original_quote_payment_or_cash(
        admin_client, api, db, customer_client):
    order = _order(db, customer_client.account, status='DELIVERED', settlement='CASH_MANUAL',
                   total=5000, cash=5000)
    quote = db.get(Quote, order.quote_id)
    original = (order.status, order.total_cents, order.cash_received_cents, order.refunded_cents,
                quote.version, quote.total_cents, dict(quote.data or {}))
    charge = _created(api, admin_client, order, amountCents=1234, includeInPayroll=False)
    assert charge['purpose'] == '排版与装订'
    assert charge['amountCents'] == 1234
    assert charge['includeInPayroll'] is False
    assert charge['grossReceivedCents'] == charge['refundedCents'] == 0
    assert charge['netReceivedCents'] == 0
    assert charge['outstandingCents'] == 1234

    db.expire_all()
    order = db.get(Order, order.id)
    quote = db.get(Quote, order.quote_id)
    assert (order.status, order.total_cents, order.cash_received_cents, order.refunded_cents,
            quote.version, quote.total_cents, dict(quote.data or {})) == original
    assert db.scalar(select(func.count()).select_from(Payment)) == 0


def test_original_refund_keeps_existing_fee_and_fee_refund_is_separate(
        admin_client, api, db, customer_client):
    order = _order(db, customer_client.account, status='DELIVERED',
                   settlement='CASH_MANUAL', total=5000, cash=5000)
    charge = _created(api, admin_client, order, amountCents=1000)
    paid = _receipt(api, admin_client, charge['id'], 200, charge['version'])
    assert paid.status_code == 200, paid.text

    # The original order may be refunded separately; that must not delete or
    # rewrite the supplementary fee, its receipt, or allow a second collection.
    order = db.get(Order, order.id)
    order.status = 'REFUNDED'
    order.refunded_cents = 5000
    db.commit()
    db.refresh(order)

    listed = api(admin_client, f'/admin/orders/{order.id}/supplementary-charges', method='GET')
    assert listed.status_code == 200, listed.text
    item = listed.json()['data']['items'][0]
    assert item['id'] == charge['id']
    assert item['grossReceivedCents'] == 200
    assert item['outstandingCents'] == 800
    blocked = _charge(api, admin_client, order, purpose='原单退款后新增',
                      expectedVersion=order.version)
    assert blocked.status_code == 409
    assert blocked.json()['error']['code'] == 'ORDER_CLOSED'

    # Fee refund remains an explicit, separate manual action.
    refund = _refund(api, admin_client, charge['id'], 200, item['version'])
    assert refund.status_code == 200, refund.text
    assert refund.json()['data']['refundedCents'] == 200


# ---------------------------------------------------------------------------
# Cash ledger: gross cap, refund credit, cancellation and audit trail
# ---------------------------------------------------------------------------

def test_supplementary_cash_never_enters_paid_original_order_or_payment(
        admin_client, api, db, customer_client):
    order = _order(db, customer_client.account, status='PENDING_PRINT',
                   settlement='CASH_MANUAL', total=5000, cash=5000)
    quote = db.get(Quote, order.quote_id)
    original = (order.status, order.total_cents, order.cash_received_cents,
                order.refunded_cents, quote.total_cents, quote.version)
    charge = _created(api, admin_client, order, amountCents=1000)
    received = _receipt(api, admin_client, charge['id'], 400, charge['version'])
    assert received.status_code == 200, received.text
    refunded = _refund(api, admin_client, charge['id'], 100,
                       received.json()['data']['version'])
    assert refunded.status_code == 200, refunded.text

    db.expire_all()
    order = db.get(Order, order.id)
    quote = db.get(Quote, order.quote_id)
    assert (order.status, order.total_cents, order.cash_received_cents,
            order.refunded_cents, quote.total_cents, quote.version) == original
    assert db.scalar(select(func.count()).select_from(Payment)) == 0


def test_receipts_cap_at_gross_and_refunds_credit_without_rewriting_cash(
        admin_client, api, db, customer_client):
    order = _order(db, customer_client.account)
    charge = _created(api, admin_client, order, amountCents=1000)

    first_receipt = _receipt(api, admin_client, charge['id'], 400, charge['version'])
    assert first_receipt.status_code == 200, first_receipt.text
    too_much = _receipt(api, admin_client, charge['id'], 700, charge['version'] + 1)
    assert too_much.status_code == 422
    assert too_much.json()['error']['code'] == 'RECEIPT_EXCEEDS_OUTSTANDING'
    second = _receipt(api, admin_client, charge['id'], 600, charge['version'] + 1)
    assert second.status_code == 200, second.text
    charge = second.json()['data']
    assert charge['grossReceivedCents'] == 1000
    assert charge['netReceivedCents'] == 1000
    assert charge['outstandingCents'] == 0

    refunded = _refund(api, admin_client, charge['id'], 300, charge['version'], reason='多收退回')
    assert refunded.status_code == 200, refunded.text
    charge = refunded.json()['data']
    assert charge['grossReceivedCents'] == 1000
    assert charge['refundedCents'] == 300
    assert charge['netReceivedCents'] == 700
    # A refund is a customer credit: it never re-opens already collected money.
    assert charge['outstandingCents'] == 0
    again = _receipt(api, admin_client, charge['id'], 1, charge['version'])
    assert again.status_code == 422
    over_refund = _refund(api, admin_client, charge['id'], 701, charge['version'])
    assert over_refund.status_code == 422
    assert over_refund.json()['error']['code'] == 'REFUND_EXCEEDS_COLLECTED'

    full = _refund(api, admin_client, charge['id'], 700, charge['version'], reason='全额退回')
    assert full.status_code == 200, full.text
    charge = full.json()['data']
    assert charge['netReceivedCents'] == 0
    assert charge['refundedCents'] == 1000

    db.expire_all()
    order = db.get(Order, order.id)
    assert order.cash_received_cents == 0
    assert order.refunded_cents == 0
    movements = list(db.scalars(select(SupplementaryMovement)
                               .where(SupplementaryMovement.charge_id == charge['id'])
                               .order_by(SupplementaryMovement.created_at, SupplementaryMovement.id)))
    assert [m.kind for m in movements] == ['RECEIPT', 'RECEIPT', 'REFUND', 'REFUND']
    assert [m.amount_cents for m in movements] == [400, 600, 300, 700]
    assert all(m.reason for m in movements)
    # The original receipt movements remain untouched after later refunds.
    assert movements[0].amount_cents == 400
    assert movements[1].amount_cents == 600


def test_cancel_requires_zero_net_received_and_keeps_original_movements(
        admin_client, api, db, customer_client):
    order = _order(db, customer_client.account)
    charge = _created(api, admin_client, order, amountCents=800)
    assert _cancel(api, admin_client, charge['id'], charge['version']).status_code == 200

    cash_charge = _created(api, admin_client, order, amountCents=800,
                           purpose='装订', expectedVersion=order.version + 1)
    assert _receipt(api, admin_client, cash_charge['id'], 300,
                    cash_charge['version']).status_code == 200
    blocked = _cancel(api, admin_client, cash_charge['id'], cash_charge['version'] + 1)
    assert blocked.status_code == 409
    assert blocked.json()['error']['code'] == 'CHARGE_NET_RECEIVED'

    refund = _refund(api, admin_client, cash_charge['id'], 300, cash_charge['version'] + 1)
    assert refund.status_code == 200, refund.text
    cancelled = _cancel(api, admin_client, cash_charge['id'], cash_charge['version'] + 2,
                        reason='已全额退回')
    assert cancelled.status_code == 200, cancelled.text
    data = cancelled.json()['data']
    assert data['status'] == 'CANCELLED'
    assert data['outstandingCents'] == 0
    assert data['refundedCents'] == 300
    assert len(data['movements']) == 2
    after = _receipt(api, admin_client, cash_charge['id'], 1, data['version'])
    assert after.status_code == 409
    assert after.json()['error']['code'] == 'CHARGE_NOT_ACTIVE'


# ---------------------------------------------------------------------------
# Mandatory text, strict DTOs and version conflicts
# ---------------------------------------------------------------------------

def test_purpose_amount_reason_and_payroll_flag_are_mandatory(
        admin_client, api, db, customer_client):
    order = _order(db, customer_client.account)
    blank = _charge(api, admin_client, order, purpose='   ')
    assert blank.status_code == 422
    assert blank.json()['error']['code'] == 'VALIDATION_ERROR'
    zero = _charge(api, admin_client, order, amountCents=0)
    assert zero.status_code == 422
    missing = api(admin_client, f'/admin/orders/{order.id}/supplementary-charges', {
        'purpose': '整理', 'amountCents': 100, 'expectedVersion': order.version,
    })
    assert missing.status_code == 422

    charge = _created(api, admin_client, order)
    for response in (
        _receipt(api, admin_client, charge['id'], 100, charge['version'], reason='  '),
        _refund(api, admin_client, charge['id'], 0, charge['version']),
    ):
        assert response.status_code == 422
        assert response.json()['error']['code'] == 'VALIDATION_ERROR'


def test_every_mutation_checks_expected_version(admin_client, api, db, customer_client):
    order = _order(db, customer_client.account)
    charge = _created(api, admin_client, order)
    stale_order = _charge(api, admin_client, order, purpose='第二次')
    assert stale_order.status_code == 409
    assert stale_order.json()['error']['code'] == 'VERSION_CONFLICT'

    receipt = _receipt(api, admin_client, charge['id'], 100, charge['version'])
    assert receipt.status_code == 200
    stale_charge = _receipt(api, admin_client, charge['id'], 100, charge['version'])
    assert stale_charge.status_code == 409
    assert stale_charge.json()['error']['details']['currentVersion'] == charge['version'] + 1

    dispute = api(customer_client, f"/orders/{order.id}/supplementary-charges/{charge['id']}/disputes", {
        'reason': '金额有疑问', 'expectedVersion': charge['version'] + 1,
    })
    assert dispute.status_code == 200, dispute.text
    case = dispute.json()['data']
    resolved = api(admin_client, f"/admin/supplementary-charges/{charge['id']}/disputes/{case['id']}/resolve", {
        'resolution': '已核对', 'expectedVersion': case['version'],
    })
    assert resolved.status_code == 200, resolved.text
    stale = api(admin_client, f"/admin/supplementary-charges/{charge['id']}/disputes/{case['id']}/resolve", {
        'resolution': '重复处理', 'expectedVersion': case['version'],
    })
    assert stale.status_code == 409


def test_idempotency_key_replays_once_for_fee_receipt_and_dispute(
        admin_client, api, db, customer_client):
    order = _order(db, customer_client.account)
    same = api(admin_client, f'/admin/orders/{order.id}/supplementary-charges', {
        'purpose': '唯一费用', 'amountCents': 1000, 'includeInPayroll': True,
        'expectedVersion': order.version,
    }, key='fee-key')
    same_again = api(admin_client, f'/admin/orders/{order.id}/supplementary-charges', {
        'purpose': '唯一费用', 'amountCents': 1000, 'includeInPayroll': True,
        'expectedVersion': order.version,
    }, key='fee-key')
    assert same.status_code == same_again.status_code == 200
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(SupplementaryCharge)
                     .where(SupplementaryCharge.purpose == '唯一费用')) == 1

    db.refresh(order)
    charge_one = _created(api, admin_client, order, purpose='正常费用')
    receipt_key = 'receipt-key'
    one = _receipt(api, admin_client, charge_one['id'], 100, charge_one['version'], key=receipt_key)
    two = _receipt(api, admin_client, charge_one['id'], 100, charge_one['version'], key=receipt_key)
    assert one.status_code == two.status_code == 200
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(SupplementaryMovement)
                     .where(SupplementaryMovement.charge_id == charge_one['id'])) == 1

    dispute_key = 'dispute-key'
    d1 = api(customer_client, f"/orders/{order.id}/supplementary-charges/{charge_one['id']}/disputes",
             {'reason': '重复提交', 'expectedVersion': charge_one['version'] + 1}, key=dispute_key)
    d2 = api(customer_client, f"/orders/{order.id}/supplementary-charges/{charge_one['id']}/disputes",
             {'reason': '重复提交', 'expectedVersion': charge_one['version'] + 1}, key=dispute_key)
    assert d1.status_code == d2.status_code == 200
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(SupplementaryDispute)
                     .where(SupplementaryDispute.charge_id == charge_one['id'])) == 1


# ---------------------------------------------------------------------------
# Customer visibility, dispute authorization and admin capabilities
# ---------------------------------------------------------------------------

def test_customer_list_is_order_view_but_disputes_need_owner_or_after_sales_grant(
        admin_client, api, db, customer_client, account_factory, client_factory):
    order = _order(db, customer_client.account, status='DELIVERED')
    charge = _created(api, admin_client, order)
    own_list = api(customer_client, f"/orders/{order.id}/supplementary-charges", method='GET')
    assert own_list.status_code == 200, own_list.text
    assert own_list.json()['data']['items'][0]['purpose'] == '排版与装订'
    assert own_list.json()['data']['items'][0]['outstandingCents'] == 1000

    other = account_factory()
    other_client = _customer_client(client_factory, db, other)
    assert api(other_client, f"/orders/{order.id}/supplementary-charges",
               method='GET').status_code == 404
    assert api(other_client, f"/orders/{order.id}/supplementary-charges/{charge['id']}/disputes", {
        'reason': '不是我的订单', 'expectedVersion': charge['version'],
    }).status_code == 404

    readonly = account_factory()
    db.add(AccessGrant(order_id=order.id, account_id=readonly.id, scopes=['ORDER_VIEW'],
                       expires_at=utcnow() + timedelta(days=1), created_by='staff'))
    db.commit()
    readonly_client = _customer_client(client_factory, db, readonly)
    assert api(readonly_client, f"/orders/{order.id}/supplementary-charges",
               method='GET').status_code == 200
    denied = api(readonly_client, f"/orders/{order.id}/supplementary-charges/{charge['id']}/disputes", {
        'reason': '只读链接不能发起', 'expectedVersion': charge['version'],
    })
    assert denied.status_code == 403
    assert denied.json()['error']['code'] == 'FORBIDDEN'

    after_sales = account_factory()
    db.add(AccessGrant(order_id=order.id, account_id=after_sales.id,
                       scopes=['ORDER_VIEW', 'ORDER_AFTER_SALES'],
                       expires_at=utcnow() + timedelta(days=1), created_by='staff'))
    db.commit()
    after_client = _customer_client(client_factory, db, after_sales)
    allowed = api(after_client, f"/orders/{order.id}/supplementary-charges/{charge['id']}/disputes", {
        'reason': '授权售后可提交', 'expectedVersion': charge['version'],
    })
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()['data']['status'] == 'OPEN'


def test_admin_financial_access_requires_payment_manage_not_courier(
        admin_client, api, db, customer_client, staff_factory, client_factory):
    order = _order(db, customer_client.account)
    charge = _created(api, admin_client, order)
    assert api(admin_client, f"/admin/orders/{order.id}/supplementary-charges",
               method='GET').status_code == 200

    no_cap = _staff_client(client_factory, db, staff_factory(role='ADMIN', capabilities=[]))
    for response in (
        api(no_cap, f"/admin/orders/{order.id}/supplementary-charges", method='GET'),
        api(no_cap, f"/admin/supplementary-charges/{charge['id']}/receipts", {
            'amountCents': 100, 'reason': '无权收款', 'expectedVersion': charge['version'],
        }),
    ):
        assert response.status_code == 403

    courier = _staff_client(client_factory, db, staff_factory(role='COURIER', capabilities=[]))
    assert api(courier, f"/admin/orders/{order.id}/supplementary-charges",
               method='GET').status_code == 403
    assert api(courier, f"/admin/orders/{order.id}/supplementary-charges", {
        'purpose': '快递员新增', 'amountCents': 100, 'includeInPayroll': False,
        'expectedVersion': order.version,
    }).status_code == 403

    dispute = api(customer_client, f"/orders/{order.id}/supplementary-charges/{charge['id']}/disputes", {
        'reason': '客户争议', 'expectedVersion': charge['version'],
    })
    assert dispute.status_code == 200
    case = dispute.json()['data']
    assert api(no_cap, f"/admin/supplementary-charges/{charge['id']}/disputes/{case['id']}/resolve", {
        'resolution': '无权处理', 'expectedVersion': case['version'],
    }).status_code == 403
    resolved = api(admin_client, f"/admin/supplementary-charges/{charge['id']}/disputes/{case['id']}/resolve", {
        'resolution': '已核对并说明', 'expectedVersion': case['version'],
    })
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()['data']['status'] == 'RESOLVED'


# ---------------------------------------------------------------------------
# Payroll hook: only actual movements, source key = movement.id, no pretending
# ---------------------------------------------------------------------------

def test_payroll_hook_runs_only_after_actual_receipt_or_refund(
        admin_client, api, db, customer_client, monkeypatch):
    calls = []
    fake = types.ModuleType('app.payroll')
    def handler(db, order, source_key):
        assert db.get(SupplementaryMovement, source_key) is not None
        calls.append((order.id, source_key))
        return WageEntry(id=f'wage-{len(calls)}')
    fake.on_supplementary_change = handler
    monkeypatch.setitem(sys.modules, 'app.payroll', fake)

    order = _order(db, customer_client.account)
    included = _created(api, admin_client, order, includeInPayroll=True)
    excluded = _created(api, admin_client, order, includeInPayroll=False,
                        purpose='固定工资不受影响', expectedVersion=order.version + 1)
    assert calls == []  # fee creation must not touch payroll

    receipt = _receipt(api, admin_client, included['id'], 400, included['version'])
    assert receipt.status_code == 200, receipt.text
    assert len(calls) == 1
    movement = receipt.json()['data']['movements'][0]
    assert calls[0] == (order.id, movement['id'])
    assert movement['payrollStatus'] == 'APPLIED'
    db.expire_all()
    ledger = db.get(SupplementaryMovement, movement['id'])
    assert ledger.data['payroll'] == {'status': 'APPLIED', 'entryId': 'wage-1'}

    refund = _refund(api, admin_client, included['id'], 100, receipt.json()['data']['version'])
    assert refund.status_code == 200, refund.text
    assert len(calls) == 2
    assert calls[1][1] == refund.json()['data']['movements'][-1]['id']
    assert refund.json()['data']['movements'][-1]['payrollStatus'] == 'APPLIED'

    ignored = _receipt(api, admin_client, excluded['id'], 100, excluded['version'])
    assert ignored.status_code == 200
    assert len(calls) == 2
    assert ignored.json()['data']['movements'][0]['payrollStatus'] == 'SKIPPED'


def test_payroll_hook_none_records_called_not_applied(
        admin_client, api, db, customer_client, monkeypatch):
    """None means the payroll worker was called but inserted no new wage row."""
    fake = types.ModuleType('app.payroll')
    def handler(db, order, source_key):
        assert db.get(SupplementaryMovement, source_key) is not None
        return None
    fake.on_supplementary_change = handler
    monkeypatch.setitem(sys.modules, 'app.payroll', fake)

    order = _order(db, customer_client.account)
    charge = _created(api, admin_client, order, includeInPayroll=True)
    receipt = _receipt(api, admin_client, charge['id'], 100, charge['version'])
    assert receipt.status_code == 200, receipt.text
    movement = receipt.json()['data']['movements'][0]
    assert movement['payrollStatus'] == 'HOOK_CALLED'
    db.expire_all()
    ledger = db.get(SupplementaryMovement, movement['id'])
    assert ledger.data['payroll'] == {'status': 'HOOK_CALLED'}
    assert 'entryId' not in ledger.data['payroll']


def test_missing_payroll_module_is_pending_in_test_but_fails_in_production(
        admin_client, api, db, customer_client, monkeypatch):
    from app import supplementary

    real_import = supplementary.importlib.import_module

    def no_payroll(name, package=None):
        if name == '.payroll':
            raise ModuleNotFoundError("No module named 'app.payroll'", name='app.payroll')
        return real_import(name, package)

    monkeypatch.setattr(supplementary, 'importlib',
                        SimpleNamespace(import_module=no_payroll))
    order = _order(db, customer_client.account)
    charge = _created(api, admin_client, order, includeInPayroll=True)
    receipt = _receipt(api, admin_client, charge['id'], 100, charge['version'])
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()['data']['movements'][0]['payrollStatus'] == 'PENDING'
    db.expire_all()
    movement = db.get(SupplementaryMovement, receipt.json()['data']['movements'][0]['id'])
    assert movement.data['payroll']['reason'] == 'PAYROLL_UNAVAILABLE'

    monkeypatch.setattr(supplementary, 'get_settings',
                        lambda: SimpleNamespace(environment='production'))
    failed = _receipt(api, admin_client, charge['id'], 100, charge['version'] + 1)
    assert failed.status_code == 503
    assert failed.json()['error']['code'] == 'PAYROLL_HOOK_UNAVAILABLE'
    db.expire_all()
    refreshed = db.get(SupplementaryCharge, charge['id'])
    assert refreshed.cash_received_cents == 100
    assert db.scalar(select(func.count()).select_from(SupplementaryMovement)
                     .where(SupplementaryMovement.charge_id == charge['id'])) == 1


# ---------------------------------------------------------------------------
# Summary / export facts: original and extras reported separately
# ---------------------------------------------------------------------------

def test_summary_and_export_facts_keep_original_and_extras_separate(
        admin_client, api, db, customer_client):
    order = _order(db, customer_client.account, status='DELIVERED', total=5000, cash=4000)
    first = _created(api, admin_client, order, amountCents=1000)
    second = _created(api, admin_client, order, amountCents=500, purpose='文档整理',
                      includeInPayroll=False, expectedVersion=order.version + 1)
    cancelled = _created(api, admin_client, order, amountCents=700, purpose='已取消费用',
                         expectedVersion=order.version + 2)
    assert _cancel(api, admin_client, cancelled['id'], cancelled['version']).status_code == 200
    receipt = _receipt(api, admin_client, first['id'], 400, first['version'])
    assert receipt.status_code == 200
    assert _refund(api, admin_client, first['id'], 100,
                   receipt.json()['data']['version']).status_code == 200

    from app.commerce import order_view
    from app.supplementary import supplementary_export_facts, supplementary_summary

    db.expire_all()
    order = db.get(Order, order.id)
    summary = supplementary_summary(db, order)
    assert summary['original'] == {
        'totalCents': 5000, 'cashReceivedCents': 4000, 'refundedCents': 0,
        'settlementKind': 'CASH_MANUAL', 'status': 'DELIVERED',
    }
    extras = summary['extras']
    assert extras['chargeCount'] == 3
    assert extras['activeChargeCount'] == 2
    assert extras['cancelledChargeCount'] == 1
    assert extras['feeTotalCents'] == 1500          # cancelled fee is not owed
    assert extras['grossReceivedCents'] == 400
    assert extras['refundedCents'] == 100
    assert extras['netReceivedCents'] == 300
    assert extras['outstandingCents'] == 1100
    assert extras['includeInPayrollFeeTotalCents'] == 1000
    facts = supplementary_export_facts(db, order)
    assert len(facts) == 3
    assert {fact['chargeId'] for fact in facts} == {first['id'], second['id'], cancelled['id']}
    assert facts[0]['orderId'] == order.id
    assert 'purpose' in facts[0] and 'outstandingCents' in facts[0]
    assert 'customerName' not in facts[0] and 'quote' not in facts[0]

    view = order_view(db, order, __import__('app.core', fromlist=['Actor']).Actor(staff_id='admin', staff_role='ADMIN'))
    assert view['totalCents'] == 5000
    assert view['quote']['totalCents'] == 5000
    assert view['refundedCents'] == 0


def test_summary_is_a_strict_typed_dto_for_root_order_view(
        admin_client, api, db, customer_client):
    order = _order(db, customer_client.account, status='DELIVERED',
                   settlement='CASH_MANUAL', total=5000, cash=1000)
    charge = _created(api, admin_client, order, amountCents=1200, includeInPayroll=True)
    received = _receipt(api, admin_client, charge['id'], 300, charge['version'])
    assert received.status_code == 200, received.text

    from app.supplementary import SupplementarySummaryDTO, supplementary_summary
    from app.supplementary_schemas import ChargeList

    db.expire_all()
    order = db.get(Order, order.id)
    summary = supplementary_summary(db, order)
    dto = SupplementarySummaryDTO.model_validate(summary)
    assert dto.order_id == order.id
    assert dto.order_version == order.version
    assert dto.original.total_cents == 5000
    assert dto.original.cash_received_cents == 1000
    assert dto.original.refunded_cents == 0
    assert dto.original.settlement_kind == 'CASH_MANUAL'
    assert dto.original.status == 'DELIVERED'
    assert dto.extras.charge_count == 1
    assert dto.extras.active_charge_count == 1
    assert dto.extras.cancelled_charge_count == 0
    assert dto.extras.fee_total_cents == 1200
    assert dto.extras.gross_received_cents == 300
    assert dto.extras.refunded_cents == 0
    assert dto.extras.net_received_cents == 300
    assert dto.extras.outstanding_cents == 900
    assert dto.extras.include_in_payroll_fee_total_cents == 1200
    assert dto.combined.total_cents == 6200
    assert dto.combined.gross_received_cents == 1300
    assert dto.combined.refunded_cents == 0
    assert dto.combined.net_received_cents == 1300
    assert dto.combined.outstanding_cents == 900
    assert dto.model_dump(by_alias=True, mode='json') == summary
    nullable = SupplementarySummaryDTO.model_validate({
        **summary,
        'original': {**summary['original'], 'settlementKind': None},
    })
    assert nullable.original.settlement_kind is None
    assert ChargeList.model_fields['summary'].annotation is SupplementarySummaryDTO

    listing = ChargeList.model_validate({'items': [], 'summary': summary})
    payload = listing.model_dump(by_alias=True, mode='json')
    assert payload['summary'] == summary
    assert set(payload['summary']) == {
        'orderId', 'orderVersion', 'original', 'extras', 'combined',
    }
    assert set(payload['summary']['extras']) == {
        'chargeCount', 'activeChargeCount', 'cancelledChargeCount', 'feeTotalCents',
        'grossReceivedCents', 'refundedCents', 'netReceivedCents', 'outstandingCents',
        'includeInPayrollFeeTotalCents',
    }
    with pytest.raises(ValidationError):
        SupplementarySummaryDTO.model_validate({**summary, 'unexpected': 1})
    with pytest.raises(ValidationError):
        SupplementarySummaryDTO.model_validate({
            **summary,
            'extras': {**summary['extras'], 'unexpected': 1},
        })

    listed = api(admin_client, f'/admin/orders/{order.id}/supplementary-charges',
                 method='GET')
    assert listed.status_code == 200, listed.text
    assert listed.json()['data']['summary'] == summary
