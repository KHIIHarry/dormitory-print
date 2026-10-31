"""24h unverified-account auto-deregistration and physical cleanup.

The account is not a business tombstone here: a customer who never verifies an
email address has no order, payment or staff history worth retaining.  The
sweep therefore removes the credential, the identity graph and every private
file it owns in the same transaction that records the audit event.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core import token_hash
from app.exports import storage_root
from app.maintenance import maintenance_sweep, process_purge_job, _asset_referenced
from app.models import (
    AccessGrant,
    Account,
    Artifact,
    Asset,
    AuditLog,
    AuthSession,
    DeletionRequest,
    EmailJob,
    ExportJob,
    IdempotencyRecord,
    Job,
    OneTimeToken,
    Preparation,
    Principal,
    Referral,
    TrashEntry,
    UploadSession,
    utcnow,
)
from tests.auth_helpers import api, login_customer, make_account, make_admin, make_order, new_client

APP_MODULES = ['auth', 'growth', 'maintenance', 'exports']


def _write(key: str, content: bytes = b'%PDF-1.4 stale') -> None:
    path = storage_root() / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _age(account: Account, hours: int = 25) -> None:
    Account.__table__  # keep the model import obviously used for type checkers
    account.created_at = utcnow() - timedelta(hours=hours)
    account.updated_at = account.created_at


def _owned_graph(db, account: Account, *, suffix: str = 'stale'):
    """Create every kind of temporary object a pending account can own."""
    _write(f'assets/{suffix}/source.pdf')
    _write(f'exports/{suffix}.csv')

    asset = Asset(
        principal_id=account.principal_id,
        purpose='PRINT_SOURCE',
        name='source.pdf',
        mime='application/pdf',
        size=15,
        sha256=secrets.token_hex(32),
        storage_key=f'assets/{suffix}/source.pdf',
        status='READY',
        pages=1,
        expires_at=utcnow() + timedelta(days=1),
    )
    db.add(asset)
    db.flush()

    preparation = Preparation(
        principal_id=account.principal_id,
        checkout_account_id=account.id,
        status='READY',
        input={'files': [{'assetId': asset.id}]},
        data={},
        expires_at=utcnow() + timedelta(hours=1),
    )
    db.add(preparation)
    db.flush()
    artifact = Artifact(
        preparation_id=preparation.id,
        asset_id=asset.id,
        client_file_id='file-1',
        sha256=asset.sha256,
        pages=1,
        sheets=1,
        settings={},
        page_map=[],
    )
    db.add(artifact)
    db.flush()

    upload = UploadSession(
        principal_id=account.principal_id,
        purpose='PRINT_SOURCE',
        name='chunk.pdf',
        total_bytes=5,
        sha256=secrets.token_hex(32),
        status='UPLOADING',
        data={},
        expires_at=utcnow() + timedelta(hours=1),
    )
    db.add(upload)
    db.flush()
    _write(f'sessions/{upload.id}/chunk-0', b'chunk')

    export = ExportJob(
        account_id=account.id,
        kind='PERSONAL_DATA',
        status='READY',
        storage_key=f'exports/{suffix}.csv',
        expires_at=utcnow() + timedelta(hours=1),
        data={},
    )
    db.add(export)
    db.flush()

    session = AuthSession(
        kind='PENDING',
        token_hash=token_hash(f'{suffix}-session'),
        account_id=account.id,
        expires_at=utcnow() + timedelta(days=1),
    )
    db.add(session)
    db.flush()
    token = OneTimeToken(
        kind='REGISTRATION',
        token_hash=token_hash(f'{suffix}-token'),
        account_id=account.id,
        session_id=session.id,
        payload={'secret': 'registration-secret'},
        expires_at=utcnow() + timedelta(hours=1),
    )
    db.add(token)
    db.flush()
    email = EmailJob(
        business_key=f'customer-verify:{token.id}',
        template_key='CUSTOMER_VERIFY_EMAIL',
        status='QUEUED',
        protected_payload='sealed-email-payload',
        expires_at=utcnow() + timedelta(hours=1),
        data={'recipientMasked': 's***@example.test', 'accountId': account.id},
    )
    db.add(email)
    db.flush()
    stale_email = EmailJob(
        business_key=f'customer-verify:stale-{suffix}',
        template_key='CUSTOMER_VERIFY_EMAIL',
        status='QUEUED',
        protected_payload='sealed-stale-payload',
        expires_at=utcnow() + timedelta(hours=1),
        data={'recipientMasked': 's***@example.test', 'accountId': account.id},
    )
    db.add(stale_email)
    db.flush()
    email_job = Job(
        kind='EMAIL',
        business_key=f'email:{email.business_key}',
        target_id=email.id,
        status='QUEUED',
        data={},
    )
    db.add(email_job)
    asset_job = Job(
        kind='ASSET',
        business_key=f'asset:{asset.id}',
        target_id=asset.id,
        status='QUEUED',
        data={},
    )
    db.add(asset_job)
    preparation_job = Job(
        kind='PREPARATION',
        business_key=f'preparation:{preparation.id}',
        target_id=preparation.id,
        status='QUEUED',
        data={},
    )
    db.add(preparation_job)
    request = DeletionRequest(account_id=account.id, status='FINALIZED', data={})
    db.add(request)
    db.flush()
    db.add(IdempotencyRecord(
        actor_key=f'customer:{account.id}:register',
        route='/api/v1/customer/auth/register',
        key='idem-1',
        request_hash='a' * 64,
        status_code=200,
        encrypted_response='sealed-response',
        result_ref={},
        expires_at=utcnow() + timedelta(days=1),
    ))
    return {
        'asset': asset,
        'preparation': preparation,
        'artifact': artifact,
        'upload': upload,
        'export': export,
        'session': session,
        'token': token,
        'email': email,
        'staleEmail': stale_email,
        'emailJob': email_job,
        'assetJob': asset_job,
        'preparationJob': preparation_job,
        'request': request,
    }


def test_expired_unverified_account_is_physically_erased_with_private_files(app, db):
    account = make_account(db, email='never-verified@example.test', verified=False)
    db.flush()
    _age(account)
    graph = _owned_graph(db, account)
    ids = {key: value.id for key, value in graph.items()}
    principal_id = account.principal_id
    account_id = account.id
    db.commit()

    result = maintenance_sweep(db)
    db.commit()

    assert result['unverifiedAccountsPurged'] == 1
    assert result['unverifiedAccountsSkipped'] == 0
    assert result['unverifiedFilesDeleted'] >= 3

    db.expire_all()
    assert db.get(Account, account_id) is None
    assert db.get(Principal, principal_id) is None
    for key in ('asset', 'preparation', 'artifact', 'upload', 'export', 'session', 'token',
                'email', 'staleEmail'):
        assert db.get(type(graph[key]), ids[key]) is None, key
    assert db.scalar(select(Job).where(Job.id == ids['emailJob'])) is None
    assert db.scalar(select(Job).where(Job.id == ids['assetJob'])) is None
    assert db.scalar(select(Job).where(Job.id == ids['preparationJob'])) is None
    assert db.scalar(select(DeletionRequest).where(DeletionRequest.id == ids['request'])) is None
    assert db.scalar(select(IdempotencyRecord).where(
        IdempotencyRecord.actor_key == f'customer:{account_id}:register')) is None
    assert not (storage_root() / 'assets/stale/source.pdf').exists()
    assert not (storage_root() / 'exports/stale.csv').exists()
    assert not (storage_root() / f"sessions/{ids['upload']}").exists()

    audit_row = db.scalar(select(AuditLog).where(
        AuditLog.action == 'UNVERIFIED_ACCOUNT_AUTO_PURGED'))
    assert audit_row is not None
    assert audit_row.target_type == 'ACCOUNT'
    assert audit_row.target_id == account_id
    assert 'never-verified' not in str(audit_row.data)


def test_account_grant_source_is_deleted_after_its_preparation(app, db):
    account = make_account(db, email='granted@example.test', verified=False)
    other = make_account(db, email='other@example.test', verified=True)
    db.flush()
    _age(account)
    graph = _owned_graph(db, account, suffix='granted')
    other_order = make_order(db, other.principal_id)
    grant = AccessGrant(
        order_id=other_order.id,
        token_hash=token_hash('granted-source'),
        account_id=account.id,
        scopes=[],
        expires_at=utcnow() + timedelta(hours=1),
        created_by='test',
    )
    db.add(grant)
    db.flush()
    graph['preparation'].source_grant_id = grant.id
    grant_id = grant.id
    account_id = account.id
    db.commit()

    result = maintenance_sweep(db)
    db.commit()

    assert result['unverifiedAccountsPurged'] == 1
    db.expire_all()
    assert db.get(Account, account_id) is None
    assert db.get(AccessGrant, grant_id) is None
    assert db.get(type(other_order), other_order.id) is not None


def test_recent_pending_and_long_lived_verified_accounts_are_untouched(db):
    recent = make_account(db, email='recent@example.test', verified=False)
    verified = make_account(db, email='verified@example.test', verified=True)
    db.flush()
    _age(recent, hours=23)
    _age(verified, hours=24 * 30)
    db.commit()

    result = maintenance_sweep(db)
    db.commit()

    assert result['unverifiedAccountsPurged'] == 0
    assert db.get(Account, recent.id) is not None
    assert db.get(Account, verified.id) is not None


def test_account_with_any_order_is_blocked_and_files_are_kept(db):
    account = make_account(db, email='ordered@example.test', verified=False)
    db.flush()
    _age(account)
    make_order(db, principal_id=account.principal_id, status='DELIVERED')
    key = 'assets/ordered/source.pdf'
    _write(key)
    asset = Asset(
        principal_id=account.principal_id,
        purpose='PRINT_SOURCE',
        name='ordered.pdf',
        mime='application/pdf',
        size=15,
        sha256=secrets.token_hex(32),
        storage_key=key,
        status='READY',
        pages=1,
        expires_at=utcnow() + timedelta(days=1),
    )
    db.add(asset)
    db.commit()

    result = maintenance_sweep(db)
    db.commit()

    assert result['unverifiedAccountsPurged'] == 0
    assert result['unverifiedAccountsSkipped'] == 1
    assert db.get(Account, account.id) is not None
    assert db.get(Asset, asset.id) is not None
    assert (storage_root() / key).exists()


def test_customer_record_exposes_verification_deadline_only_for_pending(app, db):
    pending = make_account(db, email='pending-list@example.test', verified=False)
    verified = make_account(db, email='verified-list@example.test', verified=True)
    db.flush()
    pending.created_at = utcnow() - timedelta(hours=2)
    db.flush()
    admin = make_admin(db, email='admin-list@example.test')
    db.commit()

    client = new_client(app)
    login_customer(client, admin.email)
    response = api(client, '/admin/customers', method='GET')
    assert response.status_code == 200, response.text
    items = {item['id']: item for item in response.json()['data']['items']}
    pending_view = items[pending.id]
    verified_view = items[verified.id]
    assert pending_view['verified'] is False
    assert pending_view['verificationDeadline']
    deadline = datetime.fromisoformat(pending_view['verificationDeadline'].replace('Z', '+00:00'))
    expected = pending.created_at.replace(tzinfo=timezone.utc) + timedelta(hours=24)
    assert abs((deadline - expected).total_seconds()) < 1
    assert verified_view['verificationDeadline'] is None


def test_staging_failure_restores_files_and_keeps_account(db, monkeypatch):
    """A savepoint rollback must also roll back files staged on the GC guard."""
    account = make_account(db, email='staging-failure@example.test', verified=False)
    db.flush()
    _age(account)
    graph = _owned_graph(db, account)
    db.commit()
    asset_id = graph['asset'].id
    upload_id = graph['upload'].id

    from app import maintenance

    def boom(*args, **kwargs):
        raise OSError('simulated staging failure')

    monkeypatch.setattr(maintenance, '_delete_access_grants', boom)

    result = maintenance_sweep(db)
    db.commit()

    assert result['unverifiedAccountsFailed'] == 1
    assert result['unverifiedAccountsPurged'] == 0
    db.expire_all()
    assert db.get(Account, account.id) is not None
    assert db.get(Asset, asset_id) is not None
    assert db.get(UploadSession, upload_id) is not None
    assert (storage_root() / 'assets/stale/source.pdf').exists()
    assert (storage_root() / 'exports/stale.csv').exists()
    assert (storage_root() / f'sessions/{upload_id}').exists()


def test_one_failed_account_does_not_discard_another_accounts_staged_files(db, monkeypatch):
    older = make_account(db, email='staged-first@example.test', verified=False)
    newer = make_account(db, email='staged-second@example.test', verified=False)
    db.flush()
    _age(older, hours=26)
    _age(newer, hours=25)
    _owned_graph(db, older, suffix='first')
    _owned_graph(db, newer, suffix='second')
    db.commit()
    older_id = older.id
    newer_id = newer.id

    from app import maintenance

    original = maintenance._purge_unverified_account
    calls = {'count': 0}

    def flaky(db_session, account, now):
        files = original(db_session, account, now)
        calls['count'] += 1
        if calls['count'] == 2:
            raise OSError('simulated second-account failure')
        return files

    monkeypatch.setattr(maintenance, '_purge_unverified_account', flaky)

    result = maintenance_sweep(db)
    db.commit()

    assert result['unverifiedAccountsPurged'] == 1
    assert result['unverifiedAccountsFailed'] == 1
    db.expire_all()
    assert db.get(Account, older_id) is None
    assert db.get(Account, newer_id) is not None
    assert not (storage_root() / 'assets/first/source.pdf').exists()
    assert not (storage_root() / 'exports/first.csv').exists()
    assert (storage_root() / 'assets/second/source.pdf').exists()
    assert (storage_root() / 'exports/second.csv').exists()


def test_normal_account_purge_removes_owned_orphan_assets_and_files(db):
    account = make_account(db, email='normal-purge@example.test', verified=True)
    key = 'assets/normal/source.pdf'
    _write(key)
    asset = Asset(
        principal_id=account.principal_id,
        purpose='PRINT_SOURCE',
        name='source.pdf',
        mime='application/pdf',
        size=15,
        sha256=secrets.token_hex(32),
        storage_key=key,
        status='READY',
        pages=1,
        expires_at=utcnow() + timedelta(days=1),
    )
    db.add(asset)
    db.flush()
    upload = UploadSession(
        principal_id=account.principal_id,
        asset_id=asset.id,
        purpose='PRINT_SOURCE',
        name='chunk.pdf',
        total_bytes=5,
        sha256=secrets.token_hex(32),
        status='UPLOADING',
        data={},
        expires_at=utcnow() + timedelta(hours=1),
    )
    db.add(upload)
    db.flush()
    preparation = Preparation(
        principal_id=account.principal_id,
        checkout_account_id=account.id,
        status='READY',
        input={'files': [{'assetId': asset.id}]},
        data={},
        expires_at=utcnow() + timedelta(hours=1),
    )
    db.add(preparation)
    db.flush()
    artifact = Artifact(
        preparation_id=preparation.id,
        asset_id=asset.id,
        client_file_id='normal-file',
        sha256=asset.sha256,
        pages=1,
        sheets=1,
    )
    db.add(artifact)
    db.flush()
    _write(f'sessions/{upload.id}/chunk-0', b'chunk')
    entry = TrashEntry(
        target_type='ACCOUNT',
        target_id=account.id,
        actor_id='system',
        reason='account deletion',
        status='DELETED',
        purge_at=utcnow() - timedelta(minutes=1),
        data={},
    )
    db.add(entry)
    db.commit()
    account_id = account.id
    asset_id = asset.id
    upload_id = upload.id
    preparation_id = preparation.id
    artifact_id = artifact.id

    process_purge_job(db, entry.id)
    db.commit()
    db.expire_all()

    assert not (storage_root() / key).exists()
    assert not (storage_root() / f'sessions/{upload_id}').exists()
    assert db.get(Asset, asset_id) is None
    assert db.get(UploadSession, upload_id) is None
    assert db.get(Preparation, preparation_id) is None
    assert db.get(Artifact, artifact_id) is None
    purged = db.get(Account, account_id)
    assert purged is not None
    assert purged.status == 'DELETED'
    assert purged.email.startswith('deleted+')
