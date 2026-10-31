"""Self-service account deletion, recovery and superadmin singleton guards.

These are real-PostgreSQL HTTP regressions: account status, session revocation
and the single-superadmin invariant must survive the request boundary.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import threading
import time

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.account_lifecycle import request_self_deletion
from app.commerce import require_order_account
from app.core import Actor, DomainError
from app.db import SessionLocal
from app.models import Account, AuthSession, DeletionRequest, Job, Staff, TrashEntry, utcnow
from tests.auth_helpers import (
    PASSWORD,
    api,
    grant_staff,
    login_customer,
    make_account,
    make_admin,
    make_order,
    new_client,
)

APP_MODULES = ['auth', 'maintenance']


def _client(app):
    client = new_client(app)
    return client


def _request_deletion(client):
    response = api(client, '/customer/account/deletion-request', {'reason': '我要注销'})
    return response


def test_active_order_blocks_without_a_recovery_window(app, db):
    account = make_account(db, email='blocked@example.test')
    make_order(db, principal_id=account.principal_id, status='PENDING_CONFIRMATION')
    db.commit()
    client = _client(app)
    login_customer(client, account.email)

    response = _request_deletion(client)

    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['status'] == 'BLOCKED'
    assert any(item.startswith('ORDER_ACTIVE:') for item in data['blockers'])
    assert data['recoveryUntil'] is None
    db.expire_all()
    stored = db.get(Account, account.id)
    assert stored.status == 'ACTIVE'
    assert stored.deleted_at is None
    # A blocked request must not revoke the ordinary customer session.
    assert api(client, '/customer/auth/me', method='GET').status_code == 200
    request = db.scalar(select(DeletionRequest).where(DeletionRequest.account_id == account.id))
    assert request is not None and request.status == 'BLOCKED'
    listed = api(client, '/customer/account/deletion-requests', method='GET')
    assert listed.status_code == 200, listed.text
    item = listed.json()['data']['items'][0]
    assert item['status'] == 'BLOCKED'
    assert item['recoveryUntil'] is None
    assert item['canRecover'] is False
    assert any(value.startswith('ORDER_ACTIVE:') for value in item['blockers'])


def test_request_enters_seven_day_recovery_and_password_login_restores(app, db):
    account = make_account(db, email='recover@example.test')
    db.commit()
    client = _client(app)
    login_customer(client, account.email)

    response = _request_deletion(client)

    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['status'] == 'RECOVERY'
    assert data['recoveryUntil']
    assert '7' in data['message'] or '七天' in data['message']
    db.expire_all()
    stored = db.get(Account, account.id)
    assert stored.status == 'DELETION_PENDING'
    assert stored.deleted_at is not None
    request = db.scalar(select(DeletionRequest).where(DeletionRequest.account_id == account.id))
    assert request.status == 'RECOVERY'
    job = db.scalar(select(Job).where(
        Job.business_key == f'account-deletion:{request.id}'))
    assert job is not None and job.kind == 'ACCOUNT_DELETION_FINALIZE'
    assert job.target_id == request.id
    assert job.data.get('availableAt') == request.data['recoveryUntil']

    reviewer = make_admin(db, email='list-reviewer@example.test')
    db.commit()
    reviewer_client = _client(app)
    login_customer(reviewer_client, reviewer.email)
    listed = api(reviewer_client, '/admin/deletion-requests', method='GET')
    assert listed.status_code == 200, listed.text
    item = next(row for row in listed.json()['data']['items'] if row['id'] == request.id)
    assert item['status'] == 'RECOVERY'
    assert item['recoveryUntil'] == data['recoveryUntil']
    assert item['canRecover'] is True
    assert item['blockers'] == []
    # The response has already revoked the normal browser session server-side.
    me = api(client, '/customer/auth/me', method='GET')
    assert me.status_code == 200 and me.json()['data']['authKind'] == 'ANONYMOUS'

    wrong = api(client, '/customer/auth/login', {'email': account.email, 'password': 'WrongPass123!'})
    assert wrong.status_code == 401
    assert wrong.json()['error']['code'] == 'AUTH_INVALID_CREDENTIALS'

    restored = api(client, '/customer/auth/login', {'email': account.email, 'password': PASSWORD})
    assert restored.status_code == 200, restored.text
    session = restored.json()['data']
    assert session['authKind'] == 'REGISTERED'
    assert session['account']['status'] == 'ACTIVE'
    assert session['recovered'] is True
    assert session['message']
    db.expire_all()
    stored = db.get(Account, account.id)
    assert stored.status == 'ACTIVE'
    assert stored.deleted_at is None
    assert db.get(DeletionRequest, request.id).status == 'RECOVERED'


def test_expired_window_blocks_login_and_finalizer_uses_retention(app, db):
    account = make_account(db, email='expired@example.test')
    staff = grant_staff(db, account, role='COURIER')
    db.commit()
    client = _client(app)
    login_customer(client, account.email)
    assert _request_deletion(client).status_code == 200
    request = db.scalar(select(DeletionRequest).where(DeletionRequest.account_id == account.id))
    data = dict(request.data or {})
    data['recoveryUntil'] = (utcnow() - timedelta(minutes=1)).isoformat()
    request.data = data
    db.commit()

    response = api(client, '/customer/auth/login', {'email': account.email, 'password': PASSWORD})
    assert response.status_code == 410
    assert response.json()['error']['code'] == 'RECOVERY_WINDOW_EXPIRED'

    from app.account_lifecycle import finalize_deletion_request
    assert finalize_deletion_request(db, request.id) == 'FINALIZED'
    db.commit()
    db.expire_all()
    stored = db.get(Account, account.id)
    assert stored.status == 'DELETED'
    assert stored.email.startswith('deleted+')
    assert stored.deleted_at is not None
    assert db.get(DeletionRequest, request.id).status == 'FINALIZED'
    linked = db.get(Staff, staff.id)
    assert linked.account_id is None
    assert linked.enabled is False
    trash = db.scalar(select(TrashEntry).where(
        TrashEntry.target_type == 'ACCOUNT', TrashEntry.target_id == account.id))
    assert trash is not None and trash.status == 'DELETED'


def test_order_guard_serializes_with_self_deletion_and_rejects_direct_submission(db):
    account = make_account(db, email='order-race@example.test')
    db.commit()
    actor = Actor(principal_id=account.principal_id, account_id=account.id)
    errors: list[DomainError] = []
    observed_states: list[str] = []
    started = threading.Event()

    def worker():
        with SessionLocal() as reader:
            # Load the active row before the concurrent deletion commits.  The
            # submitting Session now holds a stale ACTIVE Account in its
            # identity map; require_order_account must refresh it after the
            # lock wait instead of trusting the cached object.
            stale = reader.get(Account, account.id)
            assert stale is not None and stale.status == 'ACTIVE'
            started.set()
            try:
                require_order_account(reader, actor)
            except DomainError as error:
                errors.append(error)
                observed_states.append(stale.status)

    writer = SessionLocal()
    try:
        locked = writer.scalar(
            select(Account).where(Account.id == account.id).with_for_update())
        request_self_deletion(writer, locked, reason='并发注销', actor=actor)
        assert locked.status == 'DELETION_PENDING'
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(worker)
            assert started.wait(timeout=2)
            time.sleep(0.3)
            assert not future.done(), 'order guard did not wait for the account lock'
            writer.commit()
            future.result(timeout=5)
    finally:
        writer.close()

    assert errors and errors[0].code == 'AUTH_REQUIRED'
    assert observed_states == ['DELETION_PENDING']


def test_last_superadmin_self_deletion_is_blocked_for_ui(app, db):
    admin = make_admin(db, email='only-super@example.test')
    account = db.get(Account, admin.account_id)
    db.commit()
    client = _client(app)
    login_customer(client, account.email)

    response = _request_deletion(client)

    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['status'] == 'BLOCKED'
    assert 'LAST_SUPER_ADMIN' in data['blockers']


def test_recovery_requests_cannot_be_approved_by_the_old_admin_route(app, db):
    account = make_account(db, email='noadmin@example.test')
    db.commit()
    client = _client(app)
    login_customer(client, account.email)
    created = _request_deletion(client).json()['data']
    admin = make_admin(db, email='approver@example.test')
    db.commit()
    super_client = _client(app)
    login_customer(super_client, admin.email)

    response = api(super_client, f"/admin/deletion-requests/{created['deletionRequestId']}/approve",
                   {'expectedVersion': 1, 'reason': '提前批准'})

    assert response.status_code == 409
    assert response.json()['error']['code'] == 'INVALID_TRANSITION'
    db.expire_all()
    assert db.get(Account, account.id).deleted_at is not None
    assert db.get(DeletionRequest, created['deletionRequestId']).status == 'RECOVERY'


def test_due_sweep_finalizes_even_when_the_job_is_still_scheduled_later(app, db):
    account = make_account(db, email='sweep@example.test')
    db.commit()
    client = _client(app)
    login_customer(client, account.email)
    assert _request_deletion(client).status_code == 200
    request = db.scalar(select(DeletionRequest).where(DeletionRequest.account_id == account.id))
    data = dict(request.data or {})
    data['recoveryUntil'] = (utcnow() - timedelta(minutes=1)).isoformat()
    request.data = data
    db.commit()

    from app.account_lifecycle import run_due_deletions
    result = run_due_deletions(db)
    db.commit()
    assert result['processed'] == 1 and result['finalized'] == 1
    db.expire_all()
    assert db.get(DeletionRequest, request.id).status == 'FINALIZED'
    assert db.get(Account, account.id).status == 'DELETED'


def test_due_finalizer_job_is_claimable_after_json_available_at(app, db):
    account = make_account(db, email='due-job@example.test')
    db.commit()
    client = _client(app)
    login_customer(client, account.email)
    assert _request_deletion(client).status_code == 200
    request = db.scalar(select(DeletionRequest).where(DeletionRequest.account_id == account.id))
    job = db.scalar(select(Job).where(Job.business_key == f'account-deletion:{request.id}'))
    assert job is not None
    db.commit()

    from app.jobs import claim_next_job, process_claim
    # The future JSON availableAt is honored; the worker does not finalize early.
    assert claim_next_job(['ACCOUNT_DELETION_FINALIZE']) is None

    data = dict(request.data or {})
    data['recoveryUntil'] = (utcnow() - timedelta(minutes=1)).isoformat()
    request.data = data
    job.data = {**job.data, 'availableAt': (utcnow() - timedelta(seconds=1)).isoformat()}
    db.commit()

    claim = claim_next_job(['ACCOUNT_DELETION_FINALIZE'])
    assert claim is not None and claim[0] == job.id
    assert process_claim(*claim)
    db.expire_all()
    assert db.get(DeletionRequest, request.id).status == 'FINALIZED'
    assert db.get(Job, job.id).status == 'READY'
    assert db.get(Account, account.id).status == 'DELETED'


def _super_actor(incumbent):
    return Actor(principal_id=incumbent.account_id, account_id=incumbent.account_id,
                 staff_id=incumbent.id, staff_role='ADMIN', staff_session_id=None,
                 capabilities={'*'})


@pytest.mark.parametrize(('enabled', 'status'), [(False, 'ACTIVE'), (True, 'PENDING_SETUP'), (True, 'DISABLED')])
def test_singleton_transfer_rejects_inactive_new_super(db, enabled, status):
    from app.auth import active_super_admin_count, admin_staff_create
    from app.auth_schemas import StaffCreateInput

    incumbent = make_admin(db, username='super-incumbent', email='incumbent@example.test')
    target_account = make_account(db, email=f'inactive-{enabled}-{status}@example.test')
    db.commit()

    with pytest.raises(DomainError) as error:
        admin_staff_create(StaffCreateInput(
            accountId=target_account.id, role='ADMIN', enabled=enabled, status=status,
            isSuperAdmin=True, reason='must not transfer to an unusable super',
        ), db=db, actor=_super_actor(incumbent))

    assert error.value.code == 'SUPER_ADMIN_MUST_BE_ACTIVE'
    assert error.value.status == 422
    db.rollback()
    assert db.get(Staff, incumbent.id).is_super_admin is True
    assert active_super_admin_count(db) == 1
    assert db.scalar(select(Staff).where(Staff.account_id == target_account.id)) is None


@pytest.mark.parametrize(('enabled', 'status'), [(False, 'DISABLED'), (True, 'PENDING_SETUP')])
def test_singleton_transfer_rejects_inactive_existing_staff(db, enabled, status):
    from app.auth import active_super_admin_count, admin_staff_update
    from app.auth_schemas import StaffUpdateInput

    incumbent = make_admin(db, username='super-incumbent', email='incumbent@example.test')
    target_account = make_account(db, email=f'inactive-existing-{enabled}-{status}@example.test')
    target = grant_staff(db, target_account, is_super_admin=False,
                         enabled=enabled, status=status)
    db.commit()

    with pytest.raises(DomainError) as error:
        admin_staff_update(target.id, StaffUpdateInput(
            expectedVersion=target.version,
            isSuperAdmin=True,
            reason='must not transfer to an unusable super',
        ), db=db, actor=_super_actor(incumbent))

    assert error.value.code == 'SUPER_ADMIN_MUST_BE_ACTIVE'
    assert error.value.status == 422
    db.rollback()
    db.expire_all()
    assert db.get(Staff, incumbent.id).is_super_admin is True
    assert active_super_admin_count(db) == 1
    current = db.get(Staff, target.id)
    assert current.is_super_admin is False
    assert current.enabled is enabled
    assert current.status == status


def test_singleton_bootstrap_and_superadmin_transfer(db):
    first = make_admin(db, username='super-one', email='super-one@example.test')
    other = make_account(db, email='super-two@example.test')
    db.commit()

    with pytest.raises(DomainError) as error:
        from app.auth import create_initial_admin
        create_initial_admin(db, 'super-two', PASSWORD, '新超管', email=other.email)
    assert error.value.code == 'SUPER_ADMIN_ALREADY_INITIALIZED'
    db.rollback()

    from app.auth import admin_staff_create
    from app.auth_schemas import StaffCreateInput
    from app.core import Actor

    # A real login is the only source of an actor in production; use the
    # superadmin's ordinary account session to perform the transfer.
    actor = Actor(principal_id=first.account_id, account_id=first.account_id,
                  staff_id=first.id, staff_role='ADMIN', staff_session_id=None,
                  capabilities={'*'})
    result = admin_staff_create(StaffCreateInput(
        accountId=other.id, role='ADMIN', isSuperAdmin=True, reason='转交超级管理员',
    ), db=db, actor=actor)
    db.commit()
    db.expire_all()
    old = db.get(Staff, first.id)
    new = db.scalar(select(Staff).where(Staff.account_id == other.id))
    assert old.is_super_admin is False
    assert new is not None and new.is_super_admin is True
    assert db.scalar(select(func.count()).select_from(Staff).where(Staff.is_super_admin.is_(True))) == 1

    # Database guard: a direct second true flag must not be possible.
    third = make_account(db, email='super-three@example.test')
    db.add(Staff(account_id=third.id, is_super_admin=True, username='super-three',
                 name='第三人', role='ADMIN', enabled=True, status='ACTIVE',
                 capabilities=['*'], password_hash=''))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
