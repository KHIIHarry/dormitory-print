"""Extended finance exports: supplementary charges, movements, payroll.

Real PostgreSQL integration tests.  The supplementary ledger must stay a
separate stream from the immutable original order money: a flat FINANCE export
uses explicit row types, and the ORDER export only adds independent aggregate
columns.  Customer personal exports only contain the account's own orders and
the employee ledger linked through ``Staff.account_id`` (never name/phone
matching).
"""
from __future__ import annotations

import csv
import io
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import (
    Account,
    AuthSession,
    DeliverySlot,
    ExportJob,
    Location,
    Order,
    Preparation,
    Principal,
    Quote,
    Receipt,
    ReceiptAdjustment,
    Staff,
    SupplementaryCharge,
    SupplementaryDispute,
    SupplementaryMovement,
    WageEntry,
    utcnow,
)

APP_MODULES = ['auth', 'commerce', 'exports', 'maintenance']

FINANCE_FIELDS = [
    'orderId', 'rowType', 'isTrashed', 'payableCents', 'cashReceivedCents', 'refundCents',
    'settlementKind', 'createdAt', 'receiptStatus', 'paymentKind', 'receiptAmountCents',
    'adjustmentCents', 'refundRecordCents', 'chargeId', 'movementId', 'ledgerBucket',
    'purgedAt',
    'supplementaryPurpose', 'supplementaryAmountCents', 'includeInPayroll',
    'supplementaryCashReceivedCents', 'supplementaryRefundedCents',
    'supplementaryOutstandingCents', 'supplementaryStatus', 'supplementaryCreatedAt',
    'supplementaryMovementCents', 'supplementaryMovementReason',
    'unmatchedReceiptCents', 'unmatchedReceiptRefundCents',
]
ORDER_FIELDS = [
    'orderId', 'pickupCode', 'status', 'totalCents', 'payableCents', 'createdAt',
    'deliveredAt', 'settlementKind', 'cashReceivedCents', 'refundCents',
    'customerNameMasked', 'phoneMasked', 'locationName', 'slotName',
    'supplementaryChargeCount', 'supplementaryFeeCents', 'supplementaryCashReceivedCents',
    'supplementaryRefundCents', 'supplementaryNetReceivedCents',
    'supplementaryOutstandingCents', 'supplementaryPayrollFeeCents',
]
PAYROLL_FIELDS = [
    'entryId', 'staffId', 'staffName', 'period', 'kind', 'amountCents', 'orderId',
    'assignmentId', 'sourceKey', 'reason', 'createdAt', 'settlementId', 'settledAt',
]


# ---------------------------------------------------------------------------
# Local helpers (shared app/database fixtures only)
# ---------------------------------------------------------------------------


def make_order(db, *, principal=None, status='DELIVERED', total_cents=1000,
               cash_received_cents=None, refunded_cents=0, settlement_kind='CASH_MANUAL',
               nickname='张三', phone='13800001111', created_at=None, note=''):
    principal = principal or Principal()
    if principal.id is None:
        db.add(principal)
        db.flush()
    prep = Preparation(principal_id=principal.id, status='READY',
                       expires_at=utcnow() + timedelta(days=1), input={}, data={})
    db.add(prep)
    db.flush()
    quote = Quote(preparation_id=prep.id, price_version=1,
                  expires_at=utcnow() + timedelta(minutes=15),
                  subtotal_cents=total_cents, discount_cents=0, total_cents=total_cents,
                  data={'selectedPromotionType': 'NONE', 'lines': [], 'discounts': [],
                        'discountAllocations': [], 'checkoutAccountId': None})
    db.add(quote)
    db.flush()
    prep.quote_id = quote.id
    location = Location(name='一号楼', x=0.5, y=0.5)
    slot = DeliverySlot(date='2030-01-01', name='晚间', starts_at=utcnow() + timedelta(days=1),
                        ends_at=utcnow() + timedelta(days=1, hours=1), capacity=10)
    db.add_all([location, slot])
    db.flush()
    order = Order(
        principal_id=principal.id,
        checkout_account_id=principal.account_id,
        pickup_code='P' + secrets.token_hex(4).upper(),
        status=status,
        preparation_id=prep.id,
        quote_id=quote.id,
        total_cents=total_cents,
        cash_received_cents=(total_cents if cash_received_cents is None else cash_received_cents),
        refunded_cents=refunded_cents,
        settlement_kind=settlement_kind,
        nickname=nickname,
        phone=phone,
        customer_note=note,
        location_id=location.id,
        requested_slot_id=slot.id,
        close_at=utcnow() + timedelta(hours=1),
        data={'quote': {'totalCents': total_cents}, 'history': []},
    )
    if created_at is not None:
        order.created_at = created_at
    db.add(order)
    db.commit()
    return order


def make_charge(db, order, *, purpose='加急装订费', amount_cents=300,
                include_in_payroll=False, status='ACTIVE', cash_received_cents=0,
                refunded_cents=0, created_by='staff-test'):
    charge = SupplementaryCharge(order_id=order.id, purpose=purpose,
                                 amount_cents=amount_cents,
                                 include_in_payroll=include_in_payroll,
                                 status=status,
                                 cash_received_cents=cash_received_cents,
                                 refunded_cents=refunded_cents,
                                 created_by=created_by, data={})
    db.add(charge)
    db.flush()
    return charge


def make_movement(db, charge, kind, amount_cents, *, reason='', actor_id='staff-test'):
    movement = SupplementaryMovement(charge_id=charge.id, kind=kind,
                                     amount_cents=amount_cents, reason=reason,
                                     actor_id=actor_id, data={})
    db.add(movement)
    db.flush()
    return movement


def make_dispute(db, charge, reason, *, principal_id=None, status='OPEN', resolution=''):
    dispute = SupplementaryDispute(charge_id=charge.id, principal_id=principal_id,
                                   reason=reason, status=status, resolution=resolution,
                                   actor_id='staff-test')
    db.add(dispute)
    db.flush()
    return dispute


def make_wage(db, staff, amount_cents, *, period='2030-01', kind='DELIVERY',
              order_id=None, reason='配送工资'):
    entry = WageEntry(staff_id=staff.id, order_id=order_id, assignment_id=None,
                      source_key='WAGE:' + secrets.token_hex(8), kind=kind,
                      amount_cents=amount_cents, period=period, snapshot={},
                      reason=reason, actor_id='staff-test')
    db.add(entry)
    db.flush()
    return entry


def process_job(job_id):
    from app.db import SessionLocal
    from app.exports import process_export_job

    session = SessionLocal()
    try:
        process_export_job(session, job_id)
        session.commit()
    finally:
        session.close()


def csv_rows(response):
    assert response.status_code == 200, response.text
    text = response.content.decode('utf-8-sig')
    return list(csv.reader(io.StringIO(text)))


def staff_session_client(client_factory, db, staff):
    """Log a linked Staff row in through the unified customer-account cookie."""

    from app.core import token_hash

    account = db.get(Account, staff.account_id)
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(kind='CUSTOMER', account_id=account.id,
                       principal_id=account.principal_id,
                       session_version=account.session_version,
                       token_hash=token_hash(token),
                       expires_at=utcnow() + timedelta(hours=1)))
    db.commit()
    client = client_factory()
    client.cookies.set('dormprint_account_session', token)
    client.staff = staff
    client.account = account
    return client


def customer_client_for(client_factory, db, account):
    from app.core import token_hash

    token = secrets.token_urlsafe(32)
    db.add(AuthSession(kind='CUSTOMER', account_id=account.id,
                       principal_id=account.principal_id,
                       session_version=account.session_version,
                       token_hash=token_hash(token),
                       expires_at=utcnow() + timedelta(hours=1)))
    db.commit()
    client = client_factory()
    client.cookies.set('dormprint_account_session', token)
    client.account = account
    return client


def create_export(api, client, *, kind, fields, filters=None, reason='财务导出'):
    response = api(client, '/admin/exports', {
        'kind': kind,
        'filters': filters or {},
        'fields': fields,
        'reason': reason,
    })
    assert response.status_code == 200, response.text
    return response.json()['data']


def download_rows(api, client, job_id, *, prefix='/admin/exports'):
    response = api(client, f'{prefix}/{job_id}/download', method='GET')
    return csv_rows(response)


# ---------------------------------------------------------------------------
# FINANCE: explicit row types, no double counting
# ---------------------------------------------------------------------------


def test_finance_export_separates_original_and_supplementary_rows(admin_client, api, db):
    order = make_order(db, total_cents=1200, cash_received_cents=1200, refunded_cents=200)
    active = make_charge(db, order, purpose='加急装订', amount_cents=300,
                         include_in_payroll=True, cash_received_cents=200,
                         refunded_cents=50)
    receipt = make_movement(db, active, 'RECEIPT', 200, reason='微信收款')
    refund = make_movement(db, active, 'REFUND', 50, reason='少印一页')
    cancelled = make_charge(db, order, purpose='已取消费用', amount_cents=500,
                            status='CANCELLED', cash_received_cents=500,
                            refunded_cents=500)
    db.commit()

    created = create_export(api, admin_client, kind='FINANCE', fields=FINANCE_FIELDS)
    process_job(created['id'])
    rows = download_rows(api, admin_client, created['id'])

    assert rows[0] == FINANCE_FIELDS
    data = [dict(zip(rows[0], row)) for row in rows[1:]]

    original = [row for row in data if row['rowType'] == 'ORIGINAL_ORDER']
    assert len(original) == 1
    base = original[0]
    assert [base['payableCents'], base['cashReceivedCents'], base['refundCents'],
            base['settlementKind']] == ['1200', '1200', '200', 'CASH_MANUAL']
    assert base['isTrashed'] == 'false'
    for field in ('chargeId', 'movementId', 'ledgerBucket', 'purgedAt',
                  'supplementaryPurpose',
                  'supplementaryAmountCents', 'includeInPayroll',
                  'supplementaryCashReceivedCents', 'supplementaryRefundedCents',
                  'supplementaryOutstandingCents', 'supplementaryStatus',
                  'supplementaryMovementCents', 'supplementaryCreatedAt'):
        assert base[field] == '', field
    assert base['unmatchedReceiptCents'] == '0'
    assert base['unmatchedReceiptRefundCents'] == '0'

    charges = [row for row in data if row['rowType'] == 'SUPPLEMENTARY_CHARGE']
    assert {row['chargeId'] for row in charges} == {active.id, cancelled.id}
    held = next(row for row in charges if row['chargeId'] == active.id)
    assert held['supplementaryPurpose'] == '加急装订'
    assert held['supplementaryAmountCents'] == '300'
    assert held['includeInPayroll'] == 'true'
    assert held['supplementaryCashReceivedCents'] == '200'
    assert held['supplementaryRefundedCents'] == '50'
    assert held['supplementaryOutstandingCents'] == '100'
    assert held['supplementaryStatus'] == 'ACTIVE'
    assert held['supplementaryMovementCents'] == ''
    dropped = next(row for row in charges if row['chargeId'] == cancelled.id)
    assert dropped['supplementaryOutstandingCents'] == '0'
    assert dropped['includeInPayroll'] == 'false'
    assert dropped['supplementaryStatus'] == 'CANCELLED'

    receipts = [row for row in data if row['rowType'] == 'SUPPLEMENTARY_RECEIPT']
    refunds = [row for row in data if row['rowType'] == 'SUPPLEMENTARY_REFUND']
    assert [row['movementId'] for row in receipts] == [receipt.id]
    assert [row['movementId'] for row in refunds] == [refund.id]
    assert receipts[0]['supplementaryMovementCents'] == '200'
    assert refunds[0]['supplementaryMovementCents'] == '50'
    assert receipts[0]['supplementaryMovementReason'] == '微信收款'
    assert refunds[0]['supplementaryMovementReason'] == '少印一页'
    assert charges[0]['supplementaryMovementReason'] == ''
    assert receipts[0]['supplementaryAmountCents'] == ''
    assert receipts[0]['supplementaryPurpose'] == '加急装订'
    # Original-ledger columns never leak onto supplementary rows.
    assert receipts[0]['payableCents'] == ''
    assert receipts[0]['cashReceivedCents'] == ''

    # Summing a money column never mixes the original order and the extras.
    assert sum(int(row['payableCents'] or 0) for row in data) == 1200
    assert sum(int(row['supplementaryAmountCents'] or 0) for row in data) == 800
    net = sum(int(row['supplementaryMovementCents'])
              * (1 if row['rowType'] == 'SUPPLEMENTARY_RECEIPT' else -1)
              for row in data if row['rowType'].startswith('SUPPLEMENTARY_')
              and row['movementId'])
    assert net == 150
    assert len(data) == 5

    # The export reads the immutable original money; it never rewrites it.
    db.expire_all()
    fresh = db.get(Order, order.id)
    assert [fresh.total_cents, fresh.cash_received_cents, fresh.refunded_cents] == \
        [1200, 1200, 200]


def test_order_export_reports_separate_supplementary_totals(admin_client, api, db):
    order = make_order(db, total_cents=1000, cash_received_cents=1000, refunded_cents=0)
    make_charge(db, order, purpose='加急装订', amount_cents=400,
                include_in_payroll=True, cash_received_cents=100)
    make_charge(db, order, purpose='已取消费用', amount_cents=600, status='CANCELLED',
                cash_received_cents=600, refunded_cents=600)
    db.commit()

    created = create_export(api, admin_client, kind='ORDERS', fields=ORDER_FIELDS)
    process_job(created['id'])
    rows = download_rows(api, admin_client, created['id'])

    assert rows[0] == ORDER_FIELDS
    assert len(rows) == 2
    values = dict(zip(rows[0], rows[1]))
    assert values['totalCents'] == '1000'
    assert values['cashReceivedCents'] == '1000'
    assert values['refundCents'] == '0'
    assert values['supplementaryChargeCount'] == '2'
    assert values['supplementaryFeeCents'] == '400'
    assert values['supplementaryCashReceivedCents'] == '700'
    assert values['supplementaryRefundCents'] == '600'
    assert values['supplementaryNetReceivedCents'] == '100'
    assert values['supplementaryOutstandingCents'] == '300'
    assert values['supplementaryPayrollFeeCents'] == '400'


def test_supplementary_purpose_is_formula_escaped(admin_client, api, db):
    order = make_order(db)
    make_charge(db, order, purpose='=1+1', amount_cents=200)
    db.commit()

    created = create_export(api, admin_client, kind='FINANCE',
                            fields=['orderId', 'rowType', 'supplementaryPurpose'])
    process_job(created['id'])
    rows = download_rows(api, admin_client, created['id'])
    values = dict(zip(rows[0], rows[2]))
    assert values['supplementaryPurpose'] == "'=1+1"


# ---------------------------------------------------------------------------
# Whitelists stay closed
# ---------------------------------------------------------------------------


def test_extended_columns_are_whitelisted_per_kind(admin_client, api, db):
    response = api(admin_client, '/admin/exports', {
        'kind': 'FINANCE',
        'fields': ['orderId', 'supplementaryPasswordHash'],
        'reason': '审计',
    })
    assert response.status_code == 422
    error = response.json()['error']
    assert error['code'] == 'EXPORT_FIELD_DENIED'
    assert 'supplementaryPasswordHash' in error['details']['unknownFields']

    response = api(admin_client, '/admin/exports', {
        'kind': 'ORDERS', 'fields': ['orderId', 'rowType'], 'reason': '审计',
    })
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'EXPORT_FIELD_DENIED'

    response = api(admin_client, '/admin/exports', {
        'kind': 'FINANCE', 'filters': {'period': '2030-01'},
        'fields': ['orderId'], 'reason': '审计',
    })
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'EXPORT_FILTER_INVALID'

    response = api(admin_client, '/admin/exports', {
        'kind': 'PAYROLL', 'fields': ['staffId', 'passwordHash'], 'reason': '审计',
    })
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'EXPORT_FIELD_DENIED'


# ---------------------------------------------------------------------------
# Customer personal export
# ---------------------------------------------------------------------------


def test_personal_export_only_contains_own_supplementary_and_wage_records(
        api, db, account_factory, client_factory):
    mine = account_factory(email='mine@example.test')
    other = account_factory(email='other@example.test')
    mine_principal = db.get(Principal, mine.principal_id)
    other_principal = db.get(Principal, other.principal_id)
    mine_order = make_order(db, principal=mine_principal, total_cents=900)
    other_order = make_order(db, principal=other_principal, total_cents=800)
    mine_charge = make_charge(db, mine_order, purpose='加急装订费', amount_cents=300,
                              include_in_payroll=True, cash_received_cents=100)
    make_movement(db, mine_charge, 'RECEIPT', 100, reason='现金')
    make_dispute(db, mine_charge, '用途与约定不符', principal_id=mine.principal_id)
    make_dispute(db, mine_charge, '他人异议不可见', principal_id=other.principal_id)
    other_charge = make_charge(db, other_order, purpose='别人费用', amount_cents=200)
    make_dispute(db, other_charge, '别人的异议', principal_id=other.principal_id)

    mine_staff = Staff(account_id=mine.id, username='mine_staff_' + secrets.token_hex(3),
                       name='测试员工', role='ADMIN', password_hash='!', enabled=True,
                       status='ACTIVE', capabilities=[])
    unlinked = Staff(account_id=None, username='unlinked_' + secrets.token_hex(3),
                     name='测试员工', role='ADMIN', password_hash='!', enabled=True,
                     status='ACTIVE', capabilities=[])
    db.add_all([mine_staff, unlinked])
    db.flush()
    mine_entry = make_wage(db, mine_staff, 54321, period='2030-01', reason='配送工资')
    make_wage(db, unlinked, 99999, period='2030-01', reason='同名但不是本人')
    db.commit()

    client = customer_client_for(client_factory, db, mine)
    created = api(client, '/customer/account/exports', {'reason': '个人数据导出'})
    assert created.status_code == 200, created.text
    job_id = created.json()['data']['exportJobId']
    process_job(job_id)
    response = api(client, f'/customer/account/exports/{job_id}/download', method='GET')
    rows = csv_rows(response)
    text = response.content.decode('utf-8-sig')

    assert rows[0] == ['section', 'recordId', 'field', 'value']
    assert mine_order.id in text
    assert '加急装订费' in text
    assert '用途与约定不符' in text
    assert '54321' in text
    assert mine_entry.id in text
    wage_ids = {row[1] for row in rows if row[0] == 'WAGE_ENTRY'}
    assert wage_ids == {mine_entry.id}

    assert other_order.id not in text
    assert '别人费用' not in text
    assert '别人的异议' not in text
    assert '他人异议不可见' not in text
    assert '99999' not in text
    assert 'password' not in text.lower()
    assert 'token' not in text.lower()


# ---------------------------------------------------------------------------
# Dedicated PAYROLL export: bounded and double-capability gated
# ---------------------------------------------------------------------------


def test_payroll_export_requires_payroll_and_export_capabilities(
        api, db, staff_factory, client_factory):
    worker = staff_factory(capabilities=[])
    make_wage(db, worker, 8800, period='2030-01', reason='配送工资')
    make_wage(db, worker, 1200, period='2030-02', reason='补贴')
    db.commit()

    export_only = staff_session_client(
        client_factory, db, staff_factory(capabilities=['export.manage']))
    payroll_only = staff_session_client(
        client_factory, db, staff_factory(capabilities=['payroll.manage']))
    both = staff_session_client(
        client_factory, db, staff_factory(capabilities=['payroll.manage', 'export.manage']))

    payload = {
        'kind': 'PAYROLL',
        'filters': {'period': '2030-01'},
        'fields': PAYROLL_FIELDS,
        'reason': '工资核算',
    }
    denied = api(export_only, '/admin/exports', payload)
    assert denied.status_code == 403
    assert denied.json()['error']['code'] == 'FORBIDDEN'
    denied = api(payroll_only, '/admin/exports', payload)
    assert denied.status_code == 403
    assert denied.json()['error']['code'] == 'FORBIDDEN'

    allowed = api(both, '/admin/exports', payload)
    assert allowed.status_code == 200, allowed.text
    job_id = allowed.json()['data']['id']
    process_job(job_id)
    rows = download_rows(api, both, job_id)
    assert rows[0] == PAYROLL_FIELDS
    assert len(rows) == 2
    values = dict(zip(rows[0], rows[1]))
    assert values['staffId'] == worker.id
    assert values['staffName'] == '测试员工'
    assert values['period'] == '2030-01'
    assert values['amountCents'] == '8800'
    assert values['kind'] == 'DELIVERY'
    assert values['reason'] == '配送工资'
    assert values['settlementId'] == ''

    assert api(export_only, f'/admin/exports/{job_id}', method='GET').status_code == 403
    assert api(payroll_only, f'/admin/exports/{job_id}', method='GET').status_code == 403
    assert api(both, f'/admin/exports/{job_id}', method='GET').status_code == 200

    # Existing ORDERS exports keep their old (export.manage-only) rule.
    make_order(db)
    public = api(export_only, '/admin/exports', {
        'kind': 'ORDERS', 'filters': {}, 'fields': ['orderId'], 'reason': '订单核对',
    })
    assert public.status_code == 200, public.text


def test_csv_cell_keeps_integers_numeric_and_escapes_only_strings():
    from app.exports import _csv_cell

    assert _csv_cell(-50) == '-50'
    assert _csv_cell(0) == '0'
    assert _csv_cell(True) == 'true'
    assert _csv_cell(False) == 'false'
    assert _csv_cell('-50') == "'-50"
    assert _csv_cell('=1+1') == "'=1+1"
    assert _csv_cell('@cmd') == "'@cmd"


def test_payroll_negative_adjustment_exports_numeric_csv_cell(
        api, db, staff_factory, client_factory):
    """N06 paid-refund adjustment must sum in a spreadsheet, not be text."""

    worker = staff_factory(capabilities=[])
    make_wage(db, worker, -50, period='2030-01', kind='ADJUSTMENT',
              reason='退款调整')
    make_wage(db, worker, 8800, period='2030-01', reason='配送工资')
    make_wage(db, worker, 100, period='2030-01', reason='-备注不是数字')
    db.commit()
    both = staff_session_client(
        client_factory, db,
        staff_factory(capabilities=['payroll.manage', 'export.manage']))

    created = api(both, '/admin/exports', {
        'kind': 'PAYROLL', 'filters': {'period': '2030-01'},
        'fields': ['entryId', 'amountCents', 'reason'], 'reason': '负向工资调整',
    })
    assert created.status_code == 200, created.text
    job_id = created.json()['data']['id']
    process_job(job_id)
    rows = download_rows(api, both, job_id)

    assert rows[0] == ['entryId', 'amountCents', 'reason']
    amounts = [row[1] for row in rows[1:]]
    # A negative integer is a typed financial value, never a formula payload.
    assert '-50' in amounts
    assert "'-50" not in amounts
    assert sum(int(amount) for amount in amounts) == 8850

    reasons = {row[1]: row[2] for row in rows[1:]}
    assert reasons['-50'] == '退款调整'
    # Untrusted free text keeps the spreadsheet-injection escape.
    assert reasons['100'] == "'-备注不是数字"


def test_payroll_export_row_limit_is_checked_before_queueing(
        api, db, staff_factory, client_factory, monkeypatch):
    from app.settings import get_settings

    worker = staff_factory(capabilities=[])
    make_wage(db, worker, 8800)
    make_wage(db, worker, 1200)
    db.commit()
    both = staff_session_client(
        client_factory, db, staff_factory(capabilities=['payroll.manage', 'export.manage']))
    monkeypatch.setattr(get_settings(), 'export_max_rows', 1)

    response = api(both, '/admin/exports', {
        'kind': 'PAYROLL', 'filters': {'period': '2030-01'},
        'fields': ['entryId', 'amountCents'], 'reason': '超限',
    })
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'EXPORT_ROW_LIMIT_EXCEEDED'
    db.expire_all()
    assert db.scalar(select(ExportJob).where(ExportJob.kind == 'PAYROLL')) is None


# ---------------------------------------------------------------------------
# Financial truth survives soft delete and permanent purge
# ---------------------------------------------------------------------------


def test_finance_includes_soft_deleted_orders_but_orders_export_does_not(
        admin_client, api, db):
    live = make_order(db, total_cents=1000, cash_received_cents=1000)
    trashed = make_order(db, total_cents=700, cash_received_cents=700)
    trashed_charge = make_charge(db, trashed, amount_cents=100, status='CANCELLED')
    db.commit()
    response = api(admin_client, '/admin/trash', {
        'targetType': 'ORDER', 'targetId': trashed.id,
        'expectedVersion': trashed.version, 'reason': '测试软删除',
    })
    assert response.status_code == 200, response.text

    created = create_export(
        api, admin_client, kind='FINANCE',
        fields=['orderId', 'rowType', 'isTrashed', 'chargeId', 'payableCents',
                'cashReceivedCents'],
    )
    process_job(created['id'])
    rows = download_rows(api, admin_client, created['id'])
    records = [dict(zip(rows[0], row)) for row in rows[1:]]
    original = {row['orderId']: row for row in records
                if row['rowType'] == 'ORIGINAL_ORDER'}
    assert live.id in original
    assert trashed.id in original
    assert original[trashed.id]['isTrashed'] == 'true'
    assert original[trashed.id]['payableCents'] == '700'
    charge_rows = [row for row in records
                   if row['rowType'] == 'SUPPLEMENTARY_CHARGE']
    held = next(row for row in charge_rows if row['chargeId'] == trashed_charge.id)
    assert held['isTrashed'] == 'true'

    public = create_export(api, admin_client, kind='ORDERS', fields=['orderId'])
    process_job(public['id'])
    order_rows = download_rows(api, admin_client, public['id'])
    ids = {row[0] for row in order_rows[1:]}
    assert live.id in ids
    assert trashed.id not in ids


def test_purged_order_anonymous_summary_stays_in_exports_but_not_dashboard(
        admin_client, api, db):
    live = make_order(db, total_cents=1000, cash_received_cents=1000, refunded_cents=0)
    active = make_charge(db, live, purpose='加急装订', amount_cents=300,
                         include_in_payroll=True, cash_received_cents=200,
                         refunded_cents=50)
    make_movement(db, active, 'RECEIPT', 200, reason='微信收款')
    make_movement(db, active, 'REFUND', 50, reason='少印一页')

    trashed = make_order(db, total_cents=700, cash_received_cents=700)
    response = api(admin_client, '/admin/trash', {
        'targetType': 'ORDER', 'targetId': trashed.id,
        'expectedVersion': trashed.version, 'reason': '测试软删除',
    })
    assert response.status_code == 200, response.text

    purged = make_order(db, total_cents=900, cash_received_cents=900,
                        refunded_cents=100,
                        created_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
    cancelled = make_charge(db, purged, purpose='已取消', amount_cents=500,
                            status='CANCELLED', cash_received_cents=500,
                            refunded_cents=500)
    make_movement(db, cancelled, 'RECEIPT', 500, reason='现金')
    make_movement(db, cancelled, 'REFUND', 500, reason='退费')
    db.commit()
    response = api(admin_client, '/admin/trash', {
        'targetType': 'ORDER', 'targetId': purged.id,
        'expectedVersion': purged.version, 'reason': '测试永久清理',
    })
    assert response.status_code == 200, response.text
    entry = response.json()['data']
    response = api(admin_client, f"/admin/trash/{entry['id']}/purge", {
        'expectedVersion': entry['version'], 'reason': '测试永久清理',
    })
    assert response.status_code == 200, response.text
    assert response.json()['data']['status'] == 'PURGED'

    dashboard = api(admin_client, '/admin/dashboard', method='GET').json()['data']
    created = create_export(api, admin_client, kind='FINANCE', fields=FINANCE_FIELDS)
    process_job(created['id'])
    rows = download_rows(api, admin_client, created['id'])
    text = '\n'.join(','.join(row) for row in rows)
    data = [dict(zip(rows[0], row)) for row in rows[1:]]

    original = [row for row in data if row['rowType'] == 'ORIGINAL_ORDER']
    anonymous = [row for row in data if row['rowType'] == 'ANONYMOUS_SUMMARY']
    charges = [row for row in data if row['rowType'] == 'SUPPLEMENTARY_CHARGE']
    movements = [row for row in data if row['rowType'].startswith('SUPPLEMENTARY_')
                 and row['movementId']]
    assert len(original) == 2
    assert len(anonymous) == 1
    assert len(charges) == 1
    assert len(movements) == 2
    assert purged.id not in text
    assert anonymous[0]['ledgerBucket'].startswith('trash:')
    assert anonymous[0]['orderId'] == ''
    assert anonymous[0]['createdAt'].startswith('2020-01-01')
    assert anonymous[0]['purgedAt'].startswith(utcnow().date().isoformat())

    def total(column, kinds):
        return sum(int(row[column] or 0) for row in data if row['rowType'] in kinds)

    original_kinds = ('ORIGINAL_ORDER', 'ANONYMOUS_SUMMARY')
    # The export remains the all-time ledger and keeps the purged order's
    # anonymous summary. The dashboard deliberately follows only live orders.
    assert total('payableCents', original_kinds) == 2600
    assert total('cashReceivedCents', original_kinds) == 2600
    assert total('refundCents', original_kinds) == 100
    assert dashboard['payableCents'] == 1000
    assert dashboard['cashReceivedCents'] == 1000
    assert dashboard['refundCents'] == 0

    charge_kinds = ('SUPPLEMENTARY_CHARGE', 'ANONYMOUS_SUMMARY')
    assert total('supplementaryCashReceivedCents', charge_kinds) == 700
    assert total('supplementaryRefundedCents', charge_kinds) == 550
    assert total('supplementaryOutstandingCents', charge_kinds) == 100
    assert dashboard['supplementaryCashReceivedCents'] == 200
    assert dashboard['supplementaryRefundCents'] == 50
    assert dashboard['supplementaryOutstandingCents'] == 100
    fee_total = sum(int(row['supplementaryAmountCents'] or 0) for row in data
                    if row['rowType'] == 'ANONYMOUS_SUMMARY'
                    or (row['rowType'] == 'SUPPLEMENTARY_CHARGE'
                        and row['supplementaryStatus'] == 'ACTIVE'))
    assert fee_total == 300
    assert dashboard['supplementaryFeesCents'] == 300
    assert dashboard['orderCount'] == 1
    assert dashboard['activeOrderCount'] == 1

    # Movement rows are events, not another copy of the charge totals.
    for row in movements:
        assert row['cashReceivedCents'] == ''
        assert row['supplementaryCashReceivedCents'] == ''
        assert row['supplementaryAmountCents'] == ''
    # Purged movements are reduced to ledger totals, so only live events remain.
    assert sum(int(row['supplementaryMovementCents'] or 0) for row in movements) == 250

    # Date scope is explicit: the ledger's preserved original order day wins.
    dated = create_export(api, admin_client, kind='FINANCE',
                          fields=['rowType', 'ledgerBucket'],
                          filters={'from': '2019-12-31'})
    process_job(dated['id'])
    dated_rows = download_rows(api, admin_client, dated['id'])
    assert 'ANONYMOUS_SUMMARY' in {row[0] for row in dated_rows[1:]}

    recent = (utcnow() - timedelta(days=1)).date().isoformat()
    recent_export = create_export(api, admin_client, kind='FINANCE',
                                  fields=['rowType'], filters={'from': recent})
    process_job(recent_export['id'])
    recent_rows = download_rows(api, admin_client, recent_export['id'])
    assert 'ANONYMOUS_SUMMARY' not in {row[0] for row in recent_rows[1:]}

    # Order-scoped filters cannot select a purged order's anonymous totals.
    scoped = create_export(api, admin_client, kind='FINANCE',
                           fields=['rowType'], filters={'status': 'DELIVERED'})
    process_job(scoped['id'])
    scoped_rows = download_rows(api, admin_client, scoped['id'])
    assert 'ANONYMOUS_SUMMARY' not in {row[0] for row in scoped_rows[1:]}


def test_finance_live_unmatched_receipt_stays_separate_from_order_cash(
        admin_client, api, db):
    """Unmatched cash was never a Payment, so it must not inflate order cash."""
    order = make_order(db, status='CANCELLED', settlement_kind=None,
                       total_cents=1000, cash_received_cents=0)
    receipt = Receipt(order_id=order.id, amount_cents=1000, status='UNMATCHED',
                      staff_id=admin_client.staff.id, data={})
    db.add(receipt)
    db.flush()
    db.add(ReceiptAdjustment(receipt_id=receipt.id, amount_cents=400,
                             staff_id=admin_client.staff.id, reason='部分退回'))
    db.commit()

    created = create_export(api, admin_client, kind='FINANCE', fields=FINANCE_FIELDS)
    process_job(created['id'])
    rows = download_rows(api, admin_client, created['id'])
    data = [dict(zip(rows[0], row)) for row in rows[1:]]

    unmatched = [row for row in data if row['rowType'] == 'UNMATCHED_RECEIPT']
    assert len(unmatched) == 1
    row = unmatched[0]
    assert row['orderId'] == order.id
    assert row['receiptStatus'] == 'UNMATCHED'
    assert row['receiptAmountCents'] == '1000'
    assert row['adjustmentCents'] == '400'
    # The unmatched stream owns only receipt-level cash columns.  It must not
    # repeat confirmed order cash or supplementary money.
    assert row['unmatchedReceiptCents'] == ''
    assert row['unmatchedReceiptRefundCents'] == ''
    assert row['cashReceivedCents'] == ''
    assert row['refundCents'] == ''
    assert row['payableCents'] == ''
    assert row['supplementaryCashReceivedCents'] == ''

    original = next(row for row in data if row['rowType'] == 'ORIGINAL_ORDER')
    assert original['cashReceivedCents'] == '0'
    assert original['unmatchedReceiptCents'] == '1000'
    assert original['unmatchedReceiptRefundCents'] == '400'
    assert sum(int(record['cashReceivedCents'] or 0) for record in data) == 0
    assert sum(int(record['unmatchedReceiptCents'] or 0) for record in data) == 1000

    dashboard = api(admin_client, '/admin/dashboard', method='GET').json()['data']
    assert dashboard['unmatchedReceiptCents'] == 1000
    assert dashboard['unmatchedReceiptRefundCents'] == 400
    assert dashboard['cashReceivedCents'] == 0


def test_purged_fully_refunded_unmatched_cash_maps_to_anonymous_summary(
        admin_client, api, db):
    """After purge the receipt is gone, but its gross/refund facts must survive."""
    live = make_order(db, total_cents=500, cash_received_cents=500)
    live_receipt = Receipt(order_id=live.id, amount_cents=300, status='UNMATCHED',
                           staff_id=admin_client.staff.id, data={})
    db.add(live_receipt)
    db.flush()
    db.add(ReceiptAdjustment(receipt_id=live_receipt.id, amount_cents=100,
                             staff_id=admin_client.staff.id, reason='部分退回'))
    cancelled = make_order(db, status='CANCELLED', settlement_kind=None,
                           total_cents=1000, cash_received_cents=0)
    receipt = Receipt(order_id=cancelled.id, amount_cents=1000, status='UNMATCHED',
                      staff_id=admin_client.staff.id, data={})
    db.add(receipt)
    db.flush()
    db.add(ReceiptAdjustment(receipt_id=receipt.id, amount_cents=1000,
                             staff_id=admin_client.staff.id, reason='原路退回'))
    receipt.status = 'REFUNDED'
    db.commit()

    response = api(admin_client, '/admin/trash', {
        'targetType': 'ORDER', 'targetId': cancelled.id,
        'expectedVersion': cancelled.version, 'reason': '已全额退回现金',
    })
    assert response.status_code == 200, response.text
    entry = response.json()['data']
    response = api(admin_client, f"/admin/trash/{entry['id']}/purge", {
        'expectedVersion': entry['version'], 'reason': '已全额退回现金',
    })
    assert response.status_code == 200, response.text
    assert response.json()['data']['status'] == 'PURGED'

    dashboard = api(admin_client, '/admin/dashboard', method='GET').json()['data']
    created = create_export(api, admin_client, kind='FINANCE', fields=FINANCE_FIELDS)
    process_job(created['id'])
    rows = download_rows(api, admin_client, created['id'])
    data = [dict(zip(rows[0], row)) for row in rows[1:]]

    anonymous = [row for row in data if row['rowType'] == 'ANONYMOUS_SUMMARY']
    assert len(anonymous) == 1
    summary = anonymous[0]
    assert summary['orderId'] == ''
    assert summary['ledgerBucket'].startswith('trash:')
    assert summary['unmatchedReceiptCents'] == '1000'
    assert summary['unmatchedReceiptRefundCents'] == '1000'
    assert summary['cashReceivedCents'] == '0'
    assert summary['refundCents'] == '0'
    # The purged receipt is represented only by the anonymous summary; the live
    # unmatched receipt remains a detail row.  Together they cover both sides.
    assert all(row['rowType'] != 'UNMATCHED_RECEIPT' or row['orderId'] != cancelled.id
               for row in data)
    detail = [row for row in data if row['rowType'] == 'UNMATCHED_RECEIPT']
    assert len(detail) == 1
    assert detail[0]['orderId'] == live.id
    assert detail[0]['receiptAmountCents'] == '300'
    assert detail[0]['adjustmentCents'] == '100'
    assert detail[0]['unmatchedReceiptCents'] == ''
    original_live = next(row for row in data
                         if row['rowType'] == 'ORIGINAL_ORDER'
                         and row['orderId'] == live.id)
    assert original_live['unmatchedReceiptCents'] == '300'
    assert original_live['unmatchedReceiptRefundCents'] == '100'

    # Deleted orders disappear from the dashboard, so only the live receipt
    # remains there; the export still carries the purged anonymous summary.
    assert dashboard['unmatchedReceiptCents'] == 300
    assert dashboard['unmatchedReceiptRefundCents'] == 100
    assert dashboard['cashReceivedCents'] == 500
    assert sum(int(row['unmatchedReceiptCents'] or 0) for row in data) == 1300
    assert sum(int(row['unmatchedReceiptRefundCents'] or 0) for row in data) == 1100
    assert sum(int(row['unmatchedReceiptCents'] or 0) for row in data
               if row['rowType'] == 'ORIGINAL_ORDER') == \
        dashboard['unmatchedReceiptCents']
    assert sum(int(row['unmatchedReceiptRefundCents'] or 0) for row in data
               if row['rowType'] == 'ORIGINAL_ORDER') == \
        dashboard['unmatchedReceiptRefundCents']
    # Original confirmed cash reconciles exactly once and is never replaced by
    # the unmatched columns.
    assert sum(int(row['cashReceivedCents'] or 0) for row in data
               if row['rowType'] in ('ORIGINAL_ORDER', 'ANONYMOUS_SUMMARY')) == \
        dashboard['cashReceivedCents']


def test_finance_row_cap_sets_visible_truncated_flag(
        admin_client, api, db, monkeypatch):
    from app.settings import get_settings

    order = make_order(db)
    charge = make_charge(db, order, amount_cents=300)
    make_movement(db, charge, 'RECEIPT', 100)
    db.commit()
    monkeypatch.setattr(get_settings(), 'export_max_rows', 100)
    created = create_export(api, admin_client, kind='FINANCE',
                            fields=['orderId', 'rowType'])
    monkeypatch.setattr(get_settings(), 'export_max_rows', 2)
    process_job(created['id'])

    view = api(admin_client, f"/admin/exports/{created['id']}", method='GET')
    assert view.status_code == 200, view.text
    assert view.json()['data']['truncated'] is True
    rows = download_rows(api, admin_client, created['id'])
    assert len(rows) == 3


# ---------------------------------------------------------------------------
# Role isolation: couriers and customers never reach the admin ledger
# ---------------------------------------------------------------------------


def test_courier_and_customer_cannot_use_admin_finance_export(
        api, db, staff_factory, client_factory, account_factory):
    courier = staff_factory(role='COURIER', capabilities=['*'])
    courier_client = staff_session_client(client_factory, db, courier)
    payload = {'kind': 'FINANCE', 'fields': ['orderId'], 'reason': '越权'}
    response = api(courier_client, '/admin/exports', payload)
    assert response.status_code == 403
    assert response.json()['error']['code'] == 'FORBIDDEN'

    client = customer_client_for(client_factory, db, account_factory())
    response = api(client, '/admin/exports', payload)
    assert response.status_code == 401
