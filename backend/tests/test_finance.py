"""Real PostgreSQL regressions for procurement-based cash balances."""
from datetime import datetime, timezone
import pytest
from sqlalchemy import func, select
from app.models import (AnonymousLedger, Payment, Receipt, Refund, Staff,
                        SupplementaryCharge, SupplementaryMovement, WageSettlement)
from test_delivery_closed_loop import APP_MODULES as DELIVERY_MODULES, make_world, submit
APP_MODULES = [*DELIVERY_MODULES, 'finance', 'maintenance']


def create(api, client, **changes):
    return api(client, '/admin/finance/expenses', {
        'category': 'PAPER', 'spentOn': '2026-10-15', 'amountCents': 2500,
        'paperSheets': 500, 'note': '一包纸', **changes})


def summary(api, client, month=None):
    r = api(client, '/admin/finance/summary' + (f'?month={month}' if month else ''), method='GET')
    assert r.status_code == 200, r.text
    return r.json()['data']


def test_procurement_and_void_are_idempotent_versioned_and_preserve_records(db, api, admin_client):
    body = {'category': 'PAPER', 'spentOn': '2026-10-15', 'amountCents': 2500, 'paperSheets': 500}
    first = api(admin_client, '/admin/finance/expenses', body, key='paper-once')
    assert first.status_code == 200, first.text
    entry = first.json()['data']
    replay = api(admin_client, '/admin/finance/expenses', body, key='paper-once')
    assert replay.status_code == 200 and replay.json()['data']['id'] == entry['id']
    assert create(api, admin_client, category='INK', amountCents=1200, paperSheets=None).status_code == 200
    stats = summary(api, admin_client, '2026-10')
    assert stats['paperPurchaseCents'] == 2500 and stats['inkPurchaseCents'] == 1200
    assert stats['paperSheets'] == 500 and stats['paperUnitYuan'] == '0.0500'
    assert stats['consumablesBalanceCents'] == stats['balanceCents'] == -3700
    body = {'expectedVersion': entry['version'], 'reason': '重复录入'}
    path = f'/admin/finance/expenses/{entry["id"]}/void'
    result = api(admin_client, path, body, key='void-once')
    assert result.status_code == 200, result.text
    assert result.json()['data']['status'] == 'VOIDED'
    assert api(admin_client, path, body, key='void-once').status_code == 200
    assert api(admin_client, path, body).status_code == 409
    stats = summary(api, admin_client, '2026-10')
    assert stats['paperSheets'] == 0 and stats['paperUnitYuan'] is None
    assert stats['balanceCents'] == -1200
    from app.models import OperatingExpense
    assert db.scalar(select(func.count()).select_from(OperatingExpense)) == 2


@pytest.mark.parametrize('changes', [
    {'amountCents': 0}, {'amountCents': -1}, {'amountCents': 1.5}, {'amountCents': True},
    {'amountCents': 2147483648}, {'paperSheets': None}, {'paperSheets': 0}, {'paperSheets': 1.5},
    {'category': 'INK', 'paperSheets': 10}, {'category': 'OTHER', 'paperSheets': 1},
    {'spentOn': '2099-01-01'}, {'spentOn': '2026-02-30'}, {'spentOn': 1792022400},
])
def test_bad_expenses_are_rejected(db, api, admin_client, changes):
    result = create(api, admin_client, **changes)
    assert result.status_code == 422, result.text


def test_finance_permissions_do_not_follow_order_or_payroll_access(db, api, admin_client, customer_client):
    for path in ('/admin/finance/summary', '/admin/finance/expenses'):
        assert api(customer_client, path, method='GET').status_code == 401
    staff = db.get(Staff, admin_client.staff.id)
    staff.capabilities = ['orders.manage', 'payroll.manage', 'payment.manage']
    staff.is_super_admin = False
    db.commit()
    assert api(admin_client, '/admin/finance/summary', method='GET').status_code == 403
    assert create(api, admin_client).status_code == 403
    staff.capabilities = ['finance.manage']
    db.commit()
    assert create(api, admin_client).status_code == 200


def cash_world(db, admin):
    order = submit(db, make_world(db))
    oct_end = datetime(2026, 10, 31, 15, 59, 59, tzinfo=timezone.utc)
    nov_start = datetime(2026, 10, 31, 16, 0, 0, tzinfo=timezone.utc)
    order.cash_received_cents = 980
    order.refunded_cents = 80
    order.settlement_kind = 'CASH_MANUAL'
    order.deleted_at = nov_start
    db.add_all([Payment(order_id=order.id, amount_cents=980, kind='CASH_MANUAL', staff_id=admin.staff.id, created_at=oct_end),
                Refund(order_id=order.id, amount_cents=80, staff_id=admin.staff.id, reason='退款', created_at=nov_start)])
    charge = SupplementaryCharge(order_id=order.id, purpose='补充费用', amount_cents=200,
        created_by=admin.staff.id, cash_received_cents=200, refunded_cents=20)
    db.add(charge); db.flush()
    db.add_all([
        SupplementaryMovement(charge_id=charge.id, kind='RECEIPT', amount_cents=200, reason='到账', actor_id=admin.staff.id, created_at=nov_start),
        SupplementaryMovement(charge_id=charge.id, kind='REFUND', amount_cents=20, reason='退回', actor_id=admin.staff.id, created_at=nov_start),
        WageSettlement(staff_id=admin.staff.id, period='2026-09', amount_cents=100, paid_at=nov_start, paid_by=admin.staff.id, reason='实际发放'),
        Receipt(order_id=order.id, amount_cents=9999, staff_id=admin.staff.id),
    ])
    db.commit()
    return order


def test_shanghai_cash_dates_and_pending_cash_not_double_counted(db, api, admin_client):
    cash_world(db, admin_client)
    assert create(api, admin_client, category='OTHER', amountCents=50, paperSheets=None, spentOn='2026-11-01').status_code == 200
    oct_stats = summary(api, admin_client, '2026-10')
    assert oct_stats['confirmedReceivedCents'] == oct_stats['balanceCents'] == 980
    assert oct_stats['refundCents'] == 0
    nov = summary(api, admin_client, '2026-11')
    assert nov['confirmedReceivedCents'] == 200 and nov['refundCents'] == 100
    assert nov['netReceivedCents'] == 100 and nov['paidWagesCents'] == 100
    assert nov['otherExpenseCents'] == 50 and nov['balanceCents'] == -50
    assert summary(api, admin_client)['netReceivedCents'] == 1080
    assert summary(api, admin_client)['balanceCents'] == 930


def test_archival_cash_months_and_legacy_facts(db, api, admin_client):
    from app.maintenance import _order_ledger
    from app.models import Quote
    order = cash_world(db, admin_client)
    ledger = _order_ledger(db, order, db.get(Quote, order.quote_id))
    assert ledger['cashFlowByDay'] == {
        '2026-10-31': {'receivedCents': 980, 'refundCents': 0},
        '2026-11-01': {'receivedCents': 200, 'refundCents': 100}}
    db.add_all([
        AnonymousLedger(bucket='other-purged-order', totals=ledger),
        AnonymousLedger(bucket='legacy-order', totals={'orderCount': 1, 'cashReceivedCents': 300, 'refundCents': 30}),
        AnonymousLedger(bucket='deleted-refund', totals={'deletedRefundCents': 9999}),
    ]); db.commit()
    assert summary(api, admin_client, '2026-10')['confirmedReceivedCents'] == 1960
    nov = summary(api, admin_client, '2026-11')
    assert nov['netReceivedCents'] == 200 and nov['legacyArchiveCount'] == 1
    assert summary(api, admin_client)['netReceivedCents'] == 2430


def test_pagination_filters_and_invalid_month(db, api, admin_client):
    for day in ('2026-10-01', '2026-10-02', '2026-10-03'):
        assert create(api, admin_client, spentOn=day).status_code == 200
    assert create(api, admin_client, category='INK', paperSheets=None).status_code == 200
    r = api(admin_client, '/admin/finance/expenses?month=2026-10&category=PAPER&pageSize=2', method='GET')
    assert r.status_code == 200, r.text
    data = r.json()['data']
    assert data['total'] == 3 and len(data['items']) == 2
    assert data['items'][0]['spentOn'] == '2026-10-03'
    r = api(admin_client, '/admin/finance/expenses?month=2026-10&category=PAPER&pageSize=2&page=2', method='GET')
    assert r.status_code == 200 and len(r.json()['data']['items']) == 1
    assert api(admin_client, '/admin/finance/summary?month=2026-13', method='GET').status_code == 422


def test_finance_migration_on_a_fresh_dedicated_schema(database_schema):
    import os
    from pathlib import Path
    import secrets
    import subprocess
    import sys
    from sqlalchemy import create_engine, inspect, text
    schema = 'migration_finance_' + secrets.token_hex(6)
    with database_schema.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = database_schema.url.update_query_dict({'options': '-csearch_path=' + schema})
    engine = create_engine(url)
    try:
        env = {**os.environ, 'DORMPRINT_DATABASE_URL': url.render_as_string(hide_password=False)}
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=root, env=env,
                                capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'operating_expenses' in inspect(engine).get_table_names()
        checks = {item['name'] for item in inspect(engine).get_check_constraints('operating_expenses')}
        assert checks == {'ck_expense_category', 'ck_expense_amount', 'ck_expense_sheets', 'ck_expense_status', 'ck_expense_void'}
        result = subprocess.run([sys.executable, '-m', 'alembic', 'check'], cwd=root, env=env,
                                capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        engine.dispose()
        with database_schema.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def test_cash_summary_is_consistent_when_order_is_archived_mid_read(db, database_schema, api, admin_client):
    from sqlalchemy import delete, event, insert
    order = submit(db, make_world(db))
    payment = Payment(order_id=order.id, amount_cents=980, kind='CASH_MANUAL', staff_id=admin_client.staff.id)
    db.add(payment); db.commit()
    payment_id = payment.id
    moved = False
    def archive_after_live_sum(connection, cursor, statement, parameters, context, executemany):
        nonlocal moved
        if moved or not statement.lstrip().upper().startswith('SELECT') or 'payment_records' not in statement or 'sum(' not in statement:
            return
        moved = True
        # A separate committed purge moves the same cash from live to archive.
        with database_schema.begin() as other:
            other.execute(insert(AnonymousLedger).values(bucket='concurrent-purge', totals={
                'orderCount': 1, 'cashReceivedCents': 980, 'refundCents': 0}))
            other.execute(delete(Payment).where(Payment.id == payment_id))
    event.listen(database_schema, 'after_cursor_execute', archive_after_live_sum)
    try:
        result = summary(api, admin_client)
        assert moved
        assert result['confirmedReceivedCents'] == 980
    finally:
        event.remove(database_schema, 'after_cursor_execute', archive_after_live_sum)
