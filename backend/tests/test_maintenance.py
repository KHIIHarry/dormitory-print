"""Data lifecycle: 24h trash, purge jobs, account deletion and retention sweep.

Real PostgreSQL integration tests; no SQLite substitute.
"""
from __future__ import annotations

import json
import secrets
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from app.models import (
    AccessGrant,
    Account,
    AfterSale,
    AnonymousLedger,
    Artifact,
    Asset,
    AuditLog,
    AuthSession,
    BusinessException,
    DeliveryAssignment,
    DeliveryBatch,
    DeliveryProof,
    DeliverySlot,
    DeletionRequest,
    EmailJob,
    ExportJob,
    Job,
    Location,
    OneTimeToken,
    Order,
    OrderFile,
    Payment,
    Preparation,
    Principal,
    PrintTask,
    Quote,
    Receipt,
    ReceiptAdjustment,
    Refund,
    Staff,
    TrashEntry,
    UploadSession,
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


def refresh(db):
    db.expunge_all()
    db.expire_all()
    return db


def storage_root():
    from app.settings import get_settings

    root = Path(get_settings().storage_root).expanduser()
    return root if root.is_absolute() else (Path.cwd() / root).resolve()


def write_file(key: str, content: bytes = b'%PDF-1.4 test') -> Path:
    path = storage_root() / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def make_order(db, *, principal=None, account_id=None, status='DELIVERED', total_cents=1000,
               cash_received_cents=None, refunded_cents=0, settlement_kind='CASH_MANUAL',
               nickname='张三', phone='13800001111', updated_at=None):
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
                        'discountAllocations': [], 'checkoutAccountId': account_id})
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
        checkout_account_id=account_id,
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
        customer_note='',
        location_id=location.id,
        requested_slot_id=slot.id,
        close_at=utcnow() + timedelta(hours=1),
        data={'quote': {'totalCents': total_cents}, 'history': []},
    )
    if updated_at is not None:
        order.updated_at = updated_at
    db.add(order)
    db.commit()
    return order


def link_asset(db, order, *, purpose='PRINT_SOURCE', key=None, sha=None):
    sha = sha or secrets.token_hex(32)
    key = key or f'assets/{sha[:2]}/{sha}'
    path = write_file(key)
    asset = Asset(principal_id=order.principal_id, purpose=purpose, name=f'{purpose.lower()}.pdf',
                  mime='application/pdf', size=path.stat().st_size, sha256=sha, storage_key=key,
                  status='READY', pages=1, expires_at=utcnow() + timedelta(days=30))
    db.add(asset)
    db.flush()
    db.add(OrderFile(order_id=order.id, revision=order.content_revision, client_file_id='f-' + asset.id[:6],
                     asset_id=asset.id, kind='DOCUMENT'))
    db.commit()
    return asset, path


def add_proof(db, order, *, days_ago=0):
    batch = DeliveryBatch(name='批次', starts_at=utcnow() - timedelta(days=days_ago + 1),
                          ends_at=utcnow() - timedelta(days=days_ago))
    db.add(batch)
    db.flush()
    assignment = DeliveryAssignment(order_id=order.id, batch_id=batch.id, status='DELIVERED', active=True)
    db.add(assignment)
    db.flush()
    proof = DeliveryProof(order_id=order.id, assignment_id=assignment.id, active=True,
                          data={'serverDeliveredAt': (utcnow() - timedelta(days=days_ago)).isoformat()})
    proof.created_at = utcnow() - timedelta(days=days_ago)
    db.add(proof)
    db.commit()
    return proof


def add_after_sale(db, order, *, status='COMPLETED', days_ago=1):
    row = AfterSale(order_id=order.id, source='CUSTOMER', actor_id='customer', responsibility='SELLER',
                    solution='REFUND', status=status, reason='售后', data={})
    row.updated_at = utcnow() - timedelta(days=days_ago)
    db.add(row)
    db.commit()
    return row


# ---------------------------------------------------------------------------
# Trash creation / guards
# ---------------------------------------------------------------------------


def test_paid_order_trash_requires_finished_fulfilment_and_after_sales(admin_client, api, db):
    order = make_order(db, status='PENDING_PRINT')
    blocked = api(admin_client, '/admin/trash', {
        'targetType': 'ORDER', 'targetId': order.id, 'expectedVersion': order.version,
        'reason': '整理数据',
    })
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()['error']['code'] == 'TRASH_BLOCKED'
    assert 'ACTIVE_FULFILLMENT' in blocked.json()['error']['details']['blockers']
    refresh(db)
    order = db.get(Order, order.id)
    assert order.deleted_at is None

    order.status = 'DELIVERED'
    db.commit()
    sale = add_after_sale(db, order, status='REQUESTED')
    blocked = api(admin_client, '/admin/trash', {
        'targetType': 'ORDER', 'targetId': order.id, 'expectedVersion': order.version,
        'reason': '整理数据',
    })
    assert blocked.status_code == 409
    assert 'ACTIVE_AFTER_SALE' in blocked.json()['error']['details']['blockers']

    sale.status = 'COMPLETED'
    db.commit()
    created = api(admin_client, '/admin/trash', {
        'targetType': 'ORDER', 'targetId': order.id, 'expectedVersion': order.version,
        'reason': '履约和售后均已结束',
    })
    assert created.status_code == 200, created.text
    entry = created.json()['data']
    assert entry['id'] and entry['status'] == 'DELETED'
    assert entry['type'] == 'ORDER'
    assert entry['name']
    assert entry['reason'] == '履约和售后均已结束'
    assert entry['by']
    assert entry['deletedAt'] and entry['purgeAt']
    assert entry['version'] >= 1

    refresh(db)
    assert db.get(Order, order.id).deleted_at is not None
    listed = api(admin_client, '/admin/trash', method='GET')
    assert listed.status_code == 200, listed.text
    data = listed.json()['data']
    assert {'items', 'nextCursor'} <= set(data)
    assert any(item['id'] == entry['id'] for item in data['items'])


def test_trash_rejects_unknown_targets_and_sql_injection_names(admin_client, api):
    for target in ('sqlite_master', 'orders; DROP TABLE orders', 'users'):
        response = api(admin_client, '/admin/trash', {
            'targetType': target, 'targetId': 'x', 'expectedVersion': 1, 'reason': '探测',
        })
        assert response.status_code == 422, response.text
        assert response.json()['error']['code'] == 'UNKNOWN_TRASH_TARGET'


def test_asset_with_live_references_cannot_be_trashed(admin_client, api, db):
    order = make_order(db, status='PENDING_PRINT')
    asset, _ = link_asset(db, order)
    blocked = api(admin_client, '/admin/trash', {
        'targetType': 'ASSET', 'targetId': asset.id, 'expectedVersion': asset.version,
        'reason': '清理资产',
    })
    assert blocked.status_code == 409
    assert blocked.json()['error']['code'] == 'TRASH_BLOCKED'


def test_batch_with_planned_orders_enters_trash_and_releases_them(admin_client, api, db):
    order = make_order(db, status='READY_FOR_DELIVERY')
    batch = DeliveryBatch(name='待删批次', starts_at=utcnow(), ends_at=utcnow() + timedelta(hours=1))
    db.add(batch)
    db.flush()
    assignment = DeliveryAssignment(order_id=order.id, batch_id=batch.id, active=True,
                                    status='ASSIGNED')
    db.add(assignment)
    db.commit()
    assignment_id = assignment.id

    created = api(admin_client, '/admin/trash', {
        'targetType': 'BATCH', 'targetId': batch.id, 'expectedVersion': batch.version,
        'reason': '清理批次',
    })
    assert created.status_code == 200, created.text
    assert created.json()['data']['type'] == 'BATCH'

    refresh(db)
    assert db.get(DeliveryBatch, batch.id).deleted_at is not None
    stored = db.get(DeliveryAssignment, assignment_id)
    assert stored.active is False
    assert stored.status == 'CANCELLED'
    assert db.get(Order, order.id).status == 'READY_FOR_DELIVERY'


# ---------------------------------------------------------------------------
# Restore / expiry
# ---------------------------------------------------------------------------


def test_restore_preview_then_restore_within_24h(admin_client, api, db):
    order = make_order(db)
    created = api(admin_client, '/admin/trash', {
        'targetType': 'ORDER', 'targetId': order.id, 'expectedVersion': order.version,
        'reason': '误删恢复测试',
    })
    entry = created.json()['data']

    preview = api(admin_client, f"/admin/trash/{entry['id']}/restore-preview", {})
    assert preview.status_code == 200, preview.text
    assert preview.json()['data']['restorable'] is True
    assert preview.json()['data']['blockers'] == []

    restored = api(admin_client, f"/admin/trash/{entry['id']}/restore", {
        'expectedVersion': entry['version'], 'reason': '确认未产生冲突',
    })
    assert restored.status_code == 200, restored.text
    assert restored.json()['data']['status'] == 'RESTORED'
    refresh(db)
    assert db.get(Order, order.id).deleted_at is None
    assert db.get(TrashEntry, entry['id']).status == 'RESTORED'

    again = api(admin_client, f"/admin/trash/{entry['id']}/restore", {
        'expectedVersion': entry['version'], 'reason': '重复恢复',
    })
    assert again.status_code == 409


def test_expired_trash_cannot_restore_and_sweep_queues_real_purge_job(admin_client, api, db):
    order = make_order(db)
    created = api(admin_client, '/admin/trash', {
        'targetType': 'ORDER', 'targetId': order.id, 'expectedVersion': order.version,
        'reason': '到期不可恢复',
    })
    entry = created.json()['data']
    refresh(db)
    row = db.get(TrashEntry, entry['id'])
    row.purge_at = utcnow() - timedelta(minutes=1)
    db.commit()

    preview = api(admin_client, f"/admin/trash/{entry['id']}/restore-preview", {})
    assert preview.status_code == 200
    assert preview.json()['data']['restorable'] is False
    assert any(item['code'] == 'TRASH_EXPIRED' for item in preview.json()['data']['blockers'])

    denied = api(admin_client, f"/admin/trash/{entry['id']}/restore", {
        'expectedVersion': entry['version'], 'reason': '过期恢复',
    })
    assert denied.status_code == 410
    assert denied.json()['error']['code'] == 'TRASH_EXPIRED'

    from app.maintenance import maintenance_sweep
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        maintenance_sweep(session)
        session.commit()
    finally:
        session.close()
    refresh(db)
    job = db.scalar(select(Job).where(Job.business_key == f'purge:{entry["id"]}'))
    assert job is not None and job.kind == 'PURGE' and job.target_id == entry['id']
    assert db.get(TrashEntry, entry['id']).status != 'PURGED'


# ---------------------------------------------------------------------------
# Purge with anonymous ledger + minimal audit
# ---------------------------------------------------------------------------


def test_purge_deletes_graph_but_keeps_anonymous_ledger_and_minimal_audit(admin_client, api, db):
    from app.maintenance import process_purge_job
    from app.db import SessionLocal

    order = make_order(db, status='DELIVERED', total_cents=1000, cash_received_cents=1000,
                       refunded_cents=200)
    asset, path = link_asset(db, order)
    assert path.exists()
    sale = add_after_sale(db, order, status='COMPLETED')
    db.add(Refund(order_id=order.id, after_sale_id=sale.id, amount_cents=200,
                  staff_id=admin_client.staff.id, reason='部分退款',
                  data={'suggestedCents': 100}))
    receipt = Receipt(order_id=order.id, amount_cents=1000, status='ALLOCATED',
                      staff_id=admin_client.staff.id, data={})
    db.add(receipt)
    db.flush()
    db.add(Payment(order_id=order.id, receipt_id=receipt.id, amount_cents=1000,
                   kind='CASH_MANUAL', staff_id=admin_client.staff.id, data={}))
    db.commit()

    created = api(admin_client, '/admin/trash', {
        'targetType': 'ORDER', 'targetId': order.id, 'expectedVersion': order.version,
        'reason': '过期数据清理',
    })
    assert created.status_code == 200, created.text
    entry_id = created.json()['data']['id']

    session = SessionLocal()
    try:
        process_purge_job(session, entry_id)
        session.commit()
    finally:
        session.close()
    refresh(db)

    assert db.get(Order, order.id) is None
    assert db.get(Asset, asset.id) is None
    assert not path.exists()
    assert db.scalar(select(Payment).where(Payment.order_id == order.id)) is None
    assert db.scalar(select(Receipt).where(Receipt.order_id == order.id)) is None
    assert db.scalar(select(Refund).where(Refund.order_id == order.id)) is None
    assert db.scalar(select(AfterSale).where(AfterSale.order_id == order.id)) is None
    assert db.get(TrashEntry, entry_id).status == 'PURGED'

    ledger = db.scalar(select(AnonymousLedger).where(AnonymousLedger.bucket == f'trash:{entry_id}'))
    assert ledger is not None
    assert ledger.totals['orderCount'] == 1
    assert ledger.totals['payableCents'] == 1000
    assert ledger.totals['cashReceivedCents'] == 1000
    assert ledger.totals['refundCents'] == 200
    assert ledger.totals['subtotalCents'] == 1000
    assert ledger.totals['manualAdjustmentCents'] == 100

    purge_audit = db.scalar(select(AuditLog).where(AuditLog.action == 'TRASH_PURGED'))
    assert purge_audit is not None
    assert purge_audit.target_type == 'ORDER'
    serialized = json.dumps(purge_audit.data, ensure_ascii=False)
    for private in ('13800001111', '张三', '@example.test'):
        assert private not in serialized


def test_purge_keeps_records_and_never_claims_purged_when_unlink_fails(admin_client, api, db):
    order = make_order(db)
    sha = secrets.token_hex(32)
    key = f'assets/{sha[:2]}/{sha}'
    directory = storage_root() / key
    directory.mkdir(parents=True, exist_ok=True)  # unlink() will fail with IsADirectoryError
    asset = Asset(principal_id=order.principal_id, purpose='PRINT_SOURCE', name='broken.pdf',
                  mime='application/pdf', size=1, sha256=sha, storage_key=key, status='READY',
                  pages=1, expires_at=utcnow() + timedelta(days=30))
    db.add(asset)
    db.commit()

    created = api(admin_client, '/admin/trash', {
        'targetType': 'ASSET', 'targetId': asset.id, 'expectedVersion': asset.version,
        'reason': '孤立资产清理',
    })
    assert created.status_code == 200, created.text
    entry = created.json()['data']

    purged = api(admin_client, f"/admin/trash/{entry['id']}/purge", {
        'expectedVersion': entry['version'], 'reason': '永久清理',
    })
    assert purged.status_code == 503, purged.text
    assert purged.json()['error']['code'] == 'PURGE_STORAGE_PENDING'
    refresh(db)
    assert db.get(Asset, asset.id) is not None
    assert db.get(TrashEntry, entry['id']).status == 'PURGE_PENDING'
    directory.rmdir()


# ---------------------------------------------------------------------------
# Account deletion requests
# ---------------------------------------------------------------------------


def _deletion_request(db, account, reason='用户申请注销'):
    row = DeletionRequest(account_id=account.id, status='REQUESTED',
                          data={'reason': reason, 'requestedAt': utcnow().isoformat(), 'blockers': []})
    db.add(row)
    db.commit()
    return row


def test_account_deletion_approval_blocks_open_business(admin_client, api, db, account_factory):
    account = account_factory(email='blocked@example.test')
    make_order(db, principal=db.get(Principal, account.principal_id), account_id=account.id,
               status='PENDING_PRINT')
    request = _deletion_request(db, account)
    response = api(admin_client, f'/admin/deletion-requests/{request.id}/approve', {
        'expectedVersion': request.version, 'reason': '审核注销',
    })
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'DELETION_BLOCKED'
    assert any(item.startswith('ORDER_ACTIVE:') for item in response.json()['error']['details']['blockers'])
    refresh(db)
    assert db.get(DeletionRequest, request.id).status == 'REQUESTED'
    assert db.get(Account, account.id).deleted_at is None


def test_account_deletion_approval_revokes_credentials_and_anonymises(
        admin_client, api, db, account_factory, customer_client):
    from app.core import token_hash

    account = account_factory(email='leave@example.test', display_name='离开同学')
    order = make_order(db, principal=db.get(Principal, account.principal_id),
                       account_id=account.id, status='DELIVERED')
    session = AuthSession(kind='CUSTOMER', account_id=account.id, principal_id=account.principal_id,
                          token_hash=token_hash(secrets.token_urlsafe(24)),
                          expires_at=utcnow() + timedelta(days=1))
    token = OneTimeToken(kind='PASSWORD_RESET', account_id=account.id,
                         token_hash=token_hash(secrets.token_urlsafe(24)),
                         payload={'secret': 'ephemeral'}, expires_at=utcnow() + timedelta(minutes=30))
    grant = AccessGrant(order_id=order.id, account_id=account.id, scopes=['ORDER_VIEW'],
                        expires_at=utcnow() + timedelta(days=1), created_by='customer')
    db.add_all([session, token, grant])
    db.commit()
    request = _deletion_request(db, account)
    original_email = account.email
    original_hash = account.password_hash
    original_version = account.session_version

    response = api(admin_client, f'/admin/deletion-requests/{request.id}/approve', {
        'expectedVersion': request.version, 'reason': '审核通过并清理',
    })
    assert response.status_code == 200, response.text
    assert response.json()['data']['status'] == 'APPROVED'
    refresh(db)

    account = db.get(Account, account.id)
    assert account.deleted_at is not None
    assert account.status == 'DELETED'
    assert account.email != original_email and original_email not in account.email
    assert account.display_name == '已注销用户'
    assert account.password_hash != original_hash
    assert account.session_version == original_version + 1
    assert db.get(AuthSession, session.id).revoked_at is not None
    assert db.get(AccessGrant, grant.id).revoked_at is not None
    stored_token = db.get(OneTimeToken, token.id)
    assert stored_token is None or stored_token.payload == {}
    assert db.get(DeletionRequest, request.id).status == 'APPROVED'
    trash = db.scalar(select(TrashEntry).where(TrashEntry.target_type == 'ACCOUNT',
                                               TrashEntry.target_id == account.id))
    assert trash is not None and trash.data.get('restorable') is False

    audit = db.scalar(select(AuditLog).where(AuditLog.action == 'ACCOUNT_DELETION_APPROVED'))
    assert audit is not None
    assert original_email not in json.dumps(audit.data, ensure_ascii=False)

    # A customer can query only their own requests.
    own = _deletion_request(db, customer_client.account)
    listed = api(customer_client, '/customer/account/deletion-requests', method='GET')
    assert listed.status_code == 200, listed.text
    ids = {item['id'] for item in listed.json()['data']['items']}
    assert own.id in ids and request.id not in ids


def test_admin_can_list_deletion_requests_with_live_blockers(admin_client, api, db, account_factory):
    account = account_factory(email='review@example.test')
    order = make_order(db, principal=db.get(Principal, account.principal_id), account_id=account.id,
                       status='PENDING_PRINT')
    request = _deletion_request(db, account)
    response = api(admin_client, '/admin/deletion-requests', method='GET')
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert {'items', 'nextCursor'} <= set(data)
    row = next(item for item in data['items'] if item['id'] == request.id)
    assert row['version'] == request.version
    assert row['status'] == 'REQUESTED'
    assert row['emailMasked'] and row['emailMasked'] != account.email
    assert any(item.startswith('ORDER_ACTIVE:') for item in row['blockers'])


# ---------------------------------------------------------------------------
# maintenance_sweep
# ---------------------------------------------------------------------------


def test_sweep_closes_expired_orders_and_expires_transient_secrets(db):
    from app.db import SessionLocal
    from app.maintenance import maintenance_sweep

    order = make_order(db, status='PENDING_CONFIRMATION', settlement_kind=None,
                       cash_received_cents=0)
    order.close_at = utcnow() - timedelta(minutes=5)
    db.commit()

    export = ExportJob(staff_id=None, account_id=None, kind='PERSONAL_DATA', status='READY',
                       storage_key='exports/old.csv', expires_at=utcnow() - timedelta(hours=1),
                       data={'audience': 'CUSTOMER'})
    db.add(export)
    path = write_file('exports/old.csv')
    token = OneTimeToken(kind='VERIFY_EMAIL', token_hash=secrets.token_hex(32),
                         payload={'secret': 'old'}, expires_at=utcnow() - timedelta(minutes=1))
    email = EmailJob(business_key='sweep-mail-' + secrets.token_hex(4), template_key='WELCOME',
                     status='QUEUED', protected_payload='ciphertext', attempts=0,
                     expires_at=utcnow() - timedelta(minutes=1), data={})
    upload = UploadSession(principal_id=None, purpose='PRINT_SOURCE', name='a.pdf', total_bytes=10,
                           sha256=secrets.token_hex(32), status='UPLOADING',
                           data={'chunks': [{'offset': 0}], 'receivedBytes': 10},
                           expires_at=utcnow() - timedelta(hours=1))
    db.add_all([export, token, email, upload])
    db.flush()
    upload_directory = storage_root() / f'sessions/{upload.id}'
    upload_directory.mkdir(parents=True, exist_ok=True)
    (upload_directory / '000000.part').write_bytes(b'x')

    normal = make_order(db, status='DELIVERED')
    audit = AuditLog(actor_id='admin', action='NORMAL', target_type='ORDER', target_id=normal.id)
    db.add(audit)
    db.commit()

    session = SessionLocal()
    try:
        result = maintenance_sweep(session)
        session.commit()
    finally:
        session.close()
    refresh(db)

    assert db.get(Order, order.id).status == 'CANCELLED'
    assert db.get(Order, normal.id).status == 'DELIVERED'
    assert db.get(ExportJob, export.id).status == 'EXPIRED'
    assert not path.exists()
    assert db.get(OneTimeToken, token.id) is None
    assert db.get(EmailJob, email.id).protected_payload is None
    assert db.get(UploadSession, upload.id).status == 'EXPIRED'
    assert not upload_directory.exists()
    assert db.get(AuditLog, audit.id) is not None
    assert isinstance(result, dict) and result


def test_sweep_removes_cancelled_expired_drafts(db):
    from app.db import SessionLocal
    from app.maintenance import maintenance_sweep

    principal = Principal()
    db.add(principal)
    db.flush()
    sha = secrets.token_hex(32)
    key = f'assets/{sha[:2]}/{sha}'
    path = write_file(key)
    asset = Asset(principal_id=principal.id, purpose='PRINT_SOURCE', name='draft.pdf',
                  mime='application/pdf', size=path.stat().st_size, sha256=sha,
                  storage_key=key, status='READY', pages=1,
                  expires_at=utcnow() + timedelta(days=7))
    db.add(asset)
    db.flush()
    preparation = Preparation(
        principal_id=principal.id, status='CANCELLED',
        expires_at=utcnow() - timedelta(hours=1),
        input={'files': [{'assetId': asset.id, 'clientFileId': 'f1'}]},
        data={'cancelledAt': utcnow().isoformat()},
    )
    db.add(preparation)
    db.flush()
    artifact = Artifact(preparation_id=preparation.id, asset_id=asset.id,
                        client_file_id='f1', sha256=sha, pages=1, sheets=1,
                        settings={}, page_map=[])
    db.add(artifact)
    db.commit()

    session = SessionLocal()
    try:
        maintenance_sweep(session)
        session.commit()
    finally:
        session.close()
    refresh(db)

    assert db.get(Preparation, preparation.id) is None
    assert db.get(Artifact, artifact.id) is None
    assert db.get(Asset, asset.id) is None
    assert not path.exists()


def test_sweep_file_retention_uses_q29_and_protects_unfinished_or_disputed(db):
    from app.db import SessionLocal
    from app.maintenance import maintenance_sweep

    old = utcnow() - timedelta(days=10)
    completed = make_order(db, status='DELIVERED', updated_at=old)
    add_proof(db, completed, days_ago=10)
    source, source_path = link_asset(db, completed)
    final_sha = secrets.token_hex(32)
    final_key = f'artifacts/{final_sha[:2]}/{final_sha}.pdf'
    final_path = write_file(final_key)
    final_asset = Asset(principal_id=completed.principal_id, purpose='PRINT_FINAL', name='final.pdf',
                        mime='application/pdf', size=final_path.stat().st_size, sha256=final_sha,
                        storage_key=final_key, status='READY', pages=1,
                        expires_at=utcnow() + timedelta(days=30))
    db.add(final_asset)
    db.flush()
    from app.models import Artifact
    artifact = Artifact(preparation_id=completed.preparation_id, asset_id=final_asset.id,
                        client_file_id='f1', sha256=final_sha, pages=1, sheets=1,
                        settings={}, page_map=[])
    db.add(artifact)
    db.flush()
    db.add(PrintTask(order_id=completed.id, file_id=db.scalar(select(OrderFile.id).where(
        OrderFile.order_id == completed.id)), artifact_id=artifact.id, active=True, status='COMPLETED',
        settings={}))
    db.commit()

    active = make_order(db, status='PENDING_PRINT', updated_at=old)
    active_source, active_path = link_asset(db, active)

    disputed = make_order(db, status='DELIVERED', updated_at=old)
    add_proof(db, disputed, days_ago=10)
    disputed_source, disputed_path = link_asset(db, disputed)
    db.add(BusinessException(order_id=disputed.id, type='DAMAGE', status='OPEN', reason='争议',
                             blocks_delivery=True))
    db.commit()

    session = SessionLocal()
    try:
        maintenance_sweep(session)
        session.commit()
    finally:
        session.close()
    refresh(db)

    assert db.get(Asset, source.id).deleted_at is not None
    assert not source_path.exists()
    assert db.get(Asset, final_asset.id).deleted_at is None
    assert final_path.exists()
    assert db.get(Asset, active_source.id).deleted_at is None
    assert active_path.exists()
    assert db.get(Asset, disputed_source.id).deleted_at is None
    assert disputed_path.exists()


def test_dashboard_totals_follow_trash_restore_and_purge(admin_client, api, db):
    from app.db import SessionLocal
    from app.maintenance import process_purge_job

    order = make_order(db, status='DELIVERED', total_cents=1000, cash_received_cents=1000,
                       refunded_cents=0)
    before = api(admin_client, '/admin/dashboard', method='GET')
    assert before.status_code == 200, before.text
    before_data = before.json()['data']

    created = api(admin_client, '/admin/trash', {
        'targetType': 'ORDER', 'targetId': order.id, 'expectedVersion': order.version,
        'reason': '统计保持测试',
    })
    assert created.status_code == 200, created.text
    entry = created.json()['data']
    during = api(admin_client, '/admin/dashboard', method='GET').json()['data']
    assert during['activeOrderCount'] == 0
    for key in ('orderCount', 'payableCents', 'cashReceivedCents', 'subtotalCents'):
        assert during[key] == 0, key

    restored = api(admin_client, f"/admin/trash/{entry['id']}/restore", {
        'expectedVersion': entry['version'], 'reason': '恢复后统计不重复',
    })
    assert restored.status_code == 200, restored.text
    after_restore = api(admin_client, '/admin/dashboard', method='GET').json()['data']
    assert after_restore['activeOrderCount'] == 1
    for key in ('orderCount', 'payableCents', 'cashReceivedCents', 'subtotalCents'):
        assert after_restore[key] == before_data[key], key

    refresh(db)
    order = db.get(Order, order.id)
    created = api(admin_client, '/admin/trash', {
        'targetType': 'ORDER', 'targetId': order.id, 'expectedVersion': order.version,
        'reason': '最终清理',
    })
    entry = created.json()['data']
    session = SessionLocal()
    try:
        process_purge_job(session, entry['id'])
        session.commit()
    finally:
        session.close()
    after_purge = api(admin_client, '/admin/dashboard', method='GET').json()['data']
    assert after_purge['activeOrderCount'] == 0
    for key in ('orderCount', 'payableCents', 'cashReceivedCents', 'subtotalCents'):
        assert after_purge[key] == 0, key


def test_purge_rechecks_new_references_before_deleting(admin_client, api, db):
    from app.maintenance import process_purge_job
    from app.core import DomainError
    from app.db import SessionLocal

    order = make_order(db, status='PENDING_PRINT')
    asset, _ = link_asset(db, order, key=f'assets/{secrets.token_hex(2)}/{secrets.token_hex(32)}')
    # Make the asset orphan again, trash it, then recreate a business reference.
    db.execute(delete(OrderFile).where(OrderFile.asset_id == asset.id))
    db.commit()
    created = api(admin_client, '/admin/trash', {
        'targetType': 'ASSET', 'targetId': asset.id, 'expectedVersion': asset.version,
        'reason': '孤立资产',
    })
    assert created.status_code == 200, created.text
    entry = created.json()['data']
    db.add(OrderFile(order_id=order.id, revision=order.content_revision,
                     client_file_id='new-ref', asset_id=asset.id, kind='DOCUMENT'))
    db.commit()

    session = SessionLocal()
    try:
        with pytest.raises(DomainError) as error:
            process_purge_job(session, entry['id'])
        assert error.value.code == 'PURGE_BLOCKED'
        session.rollback()
    finally:
        session.close()
    refresh(db)
    assert db.get(Asset, asset.id) is not None
    assert db.get(TrashEntry, entry['id']).status == 'DELETED'


def test_audit_logs_are_redacted_and_trash_masked(admin_client, api, db):
    from app.db import SessionLocal
    from app.maintenance import process_purge_job

    audit = AuditLog(actor_id='admin', action='SENSITIVE_TEST', target_type='ORDER',
                     target_id='order-x', reason='', request_id='req',
                     data={'safe': 1, 'email': 'person@example.test', 'phone': '13800001111',
                           'password': 'never'})
    db.add(audit)
    db.commit()

    listed = api(admin_client, '/admin/audit-logs', method='GET')
    assert listed.status_code == 200, listed.text
    row = next(item for item in listed.json()['data']['items'] if item['id'] == audit.id)
    assert row['data'] == {'safe': 1}

    created = api(admin_client, '/admin/trash', {
        'targetType': 'AUDIT_LOG', 'targetId': audit.id, 'expectedVersion': audit.version,
        'reason': '清理过期审计记录',
    })
    assert created.status_code == 200, created.text
    entry = created.json()['data']
    listed = api(admin_client, '/admin/audit-logs', method='GET')
    assert all(item['id'] != audit.id for item in listed.json()['data']['items'])

    session = SessionLocal()
    try:
        process_purge_job(session, entry['id'])
        session.commit()
    finally:
        session.close()
    refresh(db)
    assert db.get(AuditLog, audit.id) is None
    listed = api(admin_client, '/admin/audit-logs', method='GET')
    actions = {item['action'] for item in listed.json()['data']['items']}
    assert 'TRASH_PURGED' in actions


def test_restore_preview_accepts_get_compatibility_route(admin_client, api, db):
    order = make_order(db)
    created = api(admin_client, '/admin/trash', {
        'targetType': 'ORDER', 'targetId': order.id, 'expectedVersion': order.version,
        'reason': 'GET 预览兼容',
    })
    entry = created.json()['data']
    preview = api(admin_client, f"/admin/trash/{entry['id']}/restore-preview", method='GET')
    assert preview.status_code == 200, preview.text
    assert preview.json()['data']['restorable'] is True


def test_purging_unpaid_cancelled_order_does_not_invent_money_in_dashboard(admin_client, api, db):
    from app.db import SessionLocal
    from app.maintenance import process_purge_job

    order = make_order(db, status='CANCELLED', settlement_kind=None, cash_received_cents=0,
                       total_cents=1000)
    before = api(admin_client, '/admin/dashboard', method='GET').json()['data']
    assert before['payableCents'] == 0 and before['cashReceivedCents'] == 0

    created = api(admin_client, '/admin/trash', {
        'targetType': 'ORDER', 'targetId': order.id, 'expectedVersion': order.version,
        'reason': '未付款已关闭订单',
    })
    assert created.status_code == 200, created.text
    entry = created.json()['data']
    session = SessionLocal()
    try:
        process_purge_job(session, entry['id'])
        session.commit()
    finally:
        session.close()
    after = api(admin_client, '/admin/dashboard', method='GET').json()['data']
    assert after['orderCount'] == before['orderCount'] - 1
    assert after['activeOrderCount'] == 0
    assert after['payableCents'] == before['payableCents'] == 0
    assert after['cashReceivedCents'] == before['cashReceivedCents'] == 0


def test_purge_job_is_processed_by_the_shared_root_jobs_worker(admin_client, api, db):
    from app import jobs
    from app.maintenance import maintenance_sweep
    from app.db import SessionLocal

    order = make_order(db)
    created = api(admin_client, '/admin/trash', {
        'targetType': 'ORDER', 'targetId': order.id, 'expectedVersion': order.version,
        'reason': '根作业清理验证',
    })
    assert created.status_code == 200, created.text
    entry = created.json()['data']
    refresh(db)
    row = db.get(TrashEntry, entry['id'])
    row.purge_at = utcnow() - timedelta(minutes=1)
    db.commit()

    session = SessionLocal()
    try:
        maintenance_sweep(session)
        session.commit()
    finally:
        session.close()
    assert jobs.run_one(('PURGE',)) is True
    refresh(db)
    assert db.get(TrashEntry, entry['id']).status == 'PURGED'
    assert db.get(Order, order.id) is None
