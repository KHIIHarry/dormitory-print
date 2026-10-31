"""Controlled CSV exports: async jobs, whitelists, masking, expiry and download.

These are real PostgreSQL integration tests using the shared fixture schema.
"""
from __future__ import annotations

import csv
import io
import secrets
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import (
    Account,
    AuthSession,
    DeliverySlot,
    ExportJob,
    Job,
    Location,
    Order,
    OrderFile,
    Principal,
    PromotionEvent,
    Quote,
    Preparation,
    Staff,
    utcnow,
)

APP_MODULES = ['auth', 'commerce', 'exports', 'maintenance']


@pytest.fixture
def account_factory(db):
    """Local account fixture with a valid 32-char invite code.

    The shared fixture relies on Account.invite_code default=new_id(), a 36-char
    UUID that exceeds the shared varchar(32) column; that schema mismatch is
    reported to Codex instead of being patched here.
    """
    from argon2 import PasswordHasher
    from app.models import Account, Principal, utcnow

    def factory(email=None, verified=True, display_name='测试同学', **kwargs):
        invite_code = kwargs.pop('invite_code', None) or secrets.token_hex(16)
        principal = Principal()
        db.add(principal)
        db.flush()
        account = Account(principal_id=principal.id,
                          email=email or secrets.token_hex(6) + '@example.test',
                          password_hash=PasswordHasher().hash('Testing-only-Password-42!'),
                          status='ACTIVE' if verified else 'PENDING_EMAIL',
                          verified_at=utcnow() if verified else None,
                          display_name=display_name, invite_code=invite_code, **kwargs)
        db.add(account)
        db.flush()
        principal.account_id = account.id
        db.commit()
        return account

    return factory


@pytest.fixture
def customer_client(client_factory, account_factory, db):
    from app.core import token_hash
    from app.models import AuthSession, utcnow

    account = account_factory()
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(kind='CUSTOMER', account_id=account.id, principal_id=account.principal_id,
                       token_hash=token_hash(token), expires_at=utcnow() + timedelta(days=30)))
    db.commit()
    client = client_factory()
    client.cookies.set('dormprint_account_session', token)
    client.account = account
    return client


# ---------------------------------------------------------------------------
# Local helpers (no second database or app)
# ---------------------------------------------------------------------------


def refresh(db):
    db.expunge_all()
    db.expire_all()
    return db


def make_order(db, *, principal=None, status='DELIVERED', total_cents=1000,
               cash_received_cents=None, refunded_cents=0, settlement_kind='CASH_MANUAL',
               nickname='张三', phone='13800001111', created_at=None, note=''):
    principal = principal or Principal()
    if principal.id is None:
        db.add(principal)
        db.flush()
    prep = Preparation(principal_id=principal.id, status='READY', expires_at=utcnow() + timedelta(days=1),
                       input={}, data={})
    db.add(prep)
    db.flush()
    quote = Quote(preparation_id=prep.id, price_version=1, expires_at=utcnow() + timedelta(minutes=15),
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


def staff_client(client_factory, db, account_factory, capabilities):
    from app.core import token_hash

    account = account_factory(display_name='受限员工')
    staff = Staff(account_id=account.id, username='limited_' + secrets.token_hex(4),
                  name='受限员工', role='ADMIN', password_hash='!', enabled=True,
                  status='ACTIVE', must_change_password=False,
                  capabilities=list(capabilities))
    db.add(staff)
    db.flush()
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(kind='CUSTOMER', account_id=account.id,
                       principal_id=account.principal_id,
                       session_version=account.session_version,
                       token_hash=token_hash(token),
                       expires_at=utcnow() + timedelta(days=1)))
    db.commit()
    client = client_factory()
    client.cookies.set('dormprint_account_session', token)
    client.staff = staff
    client.account = account
    return client


def customer_client_for(client_factory, db, account):
    from app.core import token_hash

    token = secrets.token_urlsafe(32)
    db.add(AuthSession(kind='CUSTOMER', account_id=account.id, principal_id=account.principal_id,
                       token_hash=token_hash(token), expires_at=utcnow() + timedelta(days=1)))
    db.commit()
    client = client_factory()
    client.cookies.set('dormprint_account_session', token)
    client.account = account
    return client


# ---------------------------------------------------------------------------
# Promotion records export (legacy frontend paths)
# ---------------------------------------------------------------------------


def test_promotion_export_is_a_real_async_job_with_masked_safe_csv(admin_client, api, db, account_factory):
    account = account_factory(email='target@example.test', display_name='王同学')
    db.add(PromotionEvent(business_key='evt-' + secrets.token_hex(6), account_id=account.id,
                          kind='USED', amount_cents=500,
                          data={'reason': '=1+1'}))
    db.commit()

    created = api(admin_client, '/admin/promotions/records/export', {
        'filters': {},
        'fields': ['accountMasked', 'campaignName', 'type', 'status', 'amountCents', 'reason', 'time'],
        'reason': '审计抽查',
    })
    assert created.status_code == 200, created.text
    body = created.json()['data']
    assert body['exportJobId']
    job_id = body['exportJobId']
    assert body['status'] == 'QUEUED'

    refresh(db)
    job = db.get(ExportJob, job_id)
    assert job is not None and job.kind == 'PROMOTION_RECORDS'
    assert job.staff_id == admin_client.staff.id
    queued = db.scalar(select(Job).where(Job.business_key == f'export:{job_id}'))
    assert queued is not None and queued.kind == 'EXPORT' and queued.target_id == job_id

    status = api(admin_client, f'/admin/promotions/exports/{job_id}', method='GET')
    assert status.status_code == 200, status.text
    view = status.json()['data']
    assert view['status'] == 'QUEUED'
    assert view['downloadUrl'] is None

    process_job(job_id)

    status = api(admin_client, f'/admin/promotions/exports/{job_id}', method='GET')
    view = status.json()['data']
    assert view['status'] == 'READY'
    assert view['downloadUrl'].endswith(f'/admin/promotions/exports/{job_id}/download')
    assert view['rowCount'] == 1

    download = api(admin_client, f'/admin/promotions/exports/{job_id}/download', method='GET')
    assert download.status_code == 200, download.text
    assert download.headers['cache-control'] == 'no-store'
    assert 'attachment' in download.headers['content-disposition']
    rows = csv_rows(download)
    assert rows[0] == ['accountMasked', 'campaignName', 'type', 'status', 'amountCents', 'reason', 'time']
    flat = download.content.decode('utf-8-sig')
    assert 'target@example.test' not in flat
    assert 't***@example.test' in flat
    # Formula injection must be neutralised, not executed by a spreadsheet.
    assert "'=1+1" in flat


# ---------------------------------------------------------------------------
# Admin orders / finance exports
# ---------------------------------------------------------------------------


def test_orders_export_respects_fields_filters_and_has_no_private_columns(admin_client, api, db):
    delivered = make_order(db, status='DELIVERED', nickname='李四', phone='13900002222')
    pending = make_order(db, status='PENDING_CONFIRMATION', settlement_kind=None,
                         cash_received_cents=0, nickname='王五', phone='13700003333')

    created = api(admin_client, '/admin/exports', {
        'kind': 'ORDERS',
        'filters': {'status': 'DELIVERED'},
        'fields': ['orderId', 'status', 'totalCents', 'createdAt', 'customerNameMasked'],
        'reason': '对账抽样',
    })
    assert created.status_code == 200, created.text
    job_id = created.json()['data']['id']
    process_job(job_id)

    download = api(admin_client, f'/admin/exports/{job_id}/download', method='GET')
    rows = csv_rows(download)
    assert rows[0] == ['orderId', 'status', 'totalCents', 'createdAt', 'customerNameMasked']
    assert len(rows) == 2
    assert rows[1][0] == delivered.id
    assert all(row[0] != pending.id for row in rows[1:])
    assert '13900002222' not in download.content.decode('utf-8-sig')
    assert '李***' in download.content.decode('utf-8-sig')


def test_export_rejects_unknown_fields_and_unauthorized_capability(
        admin_client, api, client_factory, db, account_factory):
    denied = api(admin_client, '/admin/exports', {
        'kind': 'ORDERS', 'filters': {}, 'fields': ['passwordHash'], 'reason': '恶意字段',
    })
    assert denied.status_code == 422
    assert denied.json()['error']['code'] == 'EXPORT_FIELD_DENIED'

    limited = staff_client(client_factory, db, account_factory, ['orders.view'])
    from conftest import api_request
    forbidden = api_request(limited, '/admin/exports', {
        'kind': 'ORDERS', 'filters': {}, 'fields': ['orderId'], 'reason': '越权导出',
    })
    assert forbidden.status_code == 403
    assert forbidden.json()['error']['code'] == 'FORBIDDEN'


@pytest.mark.parametrize(('kind', 'fields'), [
    ('ORDERS', ['orderId']),
    ('FINANCE', ['orderId', 'payableCents']),
])
def test_export_owner_must_keep_export_capability(
        api, db, client_factory, account_factory, kind, fields):
    """Revoking export.manage must also block the job's creator immediately."""

    make_order(db)
    owner = staff_client(client_factory, db, account_factory, ['export.manage'])
    created = api(owner, '/admin/exports', {
        'kind': kind, 'filters': {}, 'fields': fields, 'reason': '权限回收验证',
    })
    assert created.status_code == 200, created.text
    job_id = created.json()['data']['id']
    process_job(job_id)

    # The creator can access the job while the capability is current.
    assert api(owner, f'/admin/exports/{job_id}', method='GET').status_code == 200
    assert api(owner, f'/admin/exports/{job_id}/download', method='GET').status_code == 200

    staff = db.get(Staff, owner.staff.id)
    staff.capabilities = []
    db.commit()

    status = api(owner, f'/admin/exports/{job_id}', method='GET')
    assert status.status_code == 403, status.text
    assert status.json()['error']['code'] == 'FORBIDDEN'

    download = api(owner, f'/admin/exports/{job_id}/download', method='GET')
    assert download.status_code == 403, download.text
    assert download.json()['error']['code'] == 'FORBIDDEN'


def test_finance_export_reconciles_cash_and_refunds(admin_client, api, db):
    make_order(db, status='DELIVERED', total_cents=1200, cash_received_cents=1200,
               refunded_cents=200)

    created = api(admin_client, '/admin/exports', {
        'kind': 'FINANCE', 'filters': {},
        'fields': ['orderId', 'payableCents', 'cashReceivedCents', 'refundCents', 'settlementKind'],
        'reason': '收退款对账',
    })
    assert created.status_code == 200, created.text
    job_id = created.json()['data']['id']
    process_job(job_id)
    rows = csv_rows(api(admin_client, f'/admin/exports/{job_id}/download', method='GET'))
    assert rows[1][1:] == ['1200', '1200', '200', 'CASH_MANUAL']


def test_export_row_limit_is_enforced_before_queueing(admin_client, api, db, monkeypatch):
    from app.settings import get_settings

    make_order(db)
    make_order(db)
    monkeypatch.setattr(get_settings(), 'export_max_rows', 1)
    response = api(admin_client, '/admin/exports', {
        'kind': 'ORDERS', 'filters': {}, 'fields': ['orderId'], 'reason': '超限',
    })
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'EXPORT_ROW_LIMIT_EXCEEDED'
    refresh(db)
    assert db.scalar(select(ExportJob)) is None


# ---------------------------------------------------------------------------
# Customer personal data export (single ExportJob model)
# ---------------------------------------------------------------------------


def test_customer_personal_export_only_contains_own_redacted_data(
        customer_client, api, db, account_factory, client_factory):
    mine = make_order(db, principal=db.get(Principal, customer_client.account.principal_id),
                      status='DELIVERED', nickname='我的订单', phone='13611112222')
    other_account = account_factory(email='other@example.test')
    other = make_order(db, principal=db.get(Principal, other_account.principal_id),
                       status='DELIVERED', nickname='别人订单', phone='13699998888')

    created = api(customer_client, '/customer/account/exports', {})
    assert created.status_code == 200, created.text
    job_id = created.json()['data']['exportJobId']
    process_job(job_id)

    download = api(customer_client, f'/customer/account/exports/{job_id}/download', method='GET')
    assert download.status_code == 200, download.text
    text = download.content.decode('utf-8-sig')
    assert mine.id in text
    assert other.id not in text
    assert 'other@example.test' not in text
    assert '13699998888' not in text
    assert 'password' not in text.lower()
    assert 'token' not in text.lower()
    assert '13611112222' in text

    other_client = customer_client_for(client_factory, db, other_account)
    from conftest import api_request
    denied = api_request(other_client, f'/customer/account/exports/{job_id}', method='GET')
    assert denied.status_code == 404


def test_legacy_auth_export_request_reuses_the_same_export_job_model(customer_client, api, db):
    created = api(customer_client, '/customer/account/export-request', {'reason': '旧入口'})
    assert created.status_code == 200, created.text
    job_id = created.json()['data']['exportJobId']

    # The legacy auth route only persisted the row; our status route must enqueue
    # the same shared ExportJob instead of creating a second export system.
    status = api(customer_client, f'/customer/account/exports/{job_id}', method='GET')
    assert status.status_code == 200, status.text
    assert status.json()['data']['id'] == job_id
    refresh(db)
    job = db.get(ExportJob, job_id)
    assert job.kind == 'PERSONAL_DATA' and job.account_id == customer_client.account.id
    queued = db.scalar(select(Job).where(Job.business_key == f'export:{job_id}'))
    assert queued is not None and queued.kind == 'EXPORT'

    process_job(job_id)
    status = api(customer_client, f'/customer/account/exports/{job_id}', method='GET')
    assert status.json()['data']['status'] == 'READY'


def test_expired_or_other_export_download_is_rejected(admin_client, api, db):
    created = api(admin_client, '/admin/exports', {
        'kind': 'ORDERS', 'filters': {}, 'fields': ['orderId'], 'reason': '到期测试',
    })
    job_id = created.json()['data']['id']
    refresh(db)
    job = db.get(ExportJob, job_id)
    job.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()

    status = api(admin_client, f'/admin/exports/{job_id}', method='GET')
    assert status.json()['data']['status'] == 'EXPIRED'
    assert status.json()['data']['downloadUrl'] is None

    denied = api(admin_client, f'/admin/exports/{job_id}/download', method='GET')
    assert denied.status_code == 410
    assert denied.json()['error']['code'] == 'EXPORT_EXPIRED'


def test_export_is_processed_by_the_shared_root_jobs_worker(admin_client, api, db):
    from app import jobs

    delivered = make_order(db, status='DELIVERED')
    created = api(admin_client, '/admin/exports', {
        'kind': 'ORDERS', 'filters': {}, 'fields': ['orderId'], 'reason': '根作业验证',
    })
    assert created.status_code == 200, created.text
    job_id = created.json()['data']['id']
    assert jobs.run_one(('EXPORT',)) is True
    refresh(db)
    job = db.get(ExportJob, job_id)
    assert job.status == 'READY' and job.storage_key
    queued = db.scalar(select(Job).where(Job.business_key == f'export:{job_id}'))
    assert queued.status == 'READY'
    download = api(admin_client, f'/admin/exports/{job_id}/download', method='GET')
    assert download.status_code == 200
    assert delivered.id in download.content.decode('utf-8-sig')
