"""FINAL N05: employee authority follows the normal verified customer account.

Every test here uses the real PostgreSQL schema/app and real HTTP routes, as
required by backend/AGENTS.md. There is no mock identity layer and no second
staff password system.
"""
from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models import Account, AuthSession, OneTimeToken, Staff, utcnow
from app.core import DomainError

APP_MODULES = ['config', 'auth', 'growth', 'mail']

from auth_helpers import (  # noqa: E402
    PASSWORD,
    api,
    grant_staff,
    login_customer,
    login_staff,
    make_account,
    make_admin,
    new_client,
)


@pytest.fixture
def db():
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def fresh_session(db):
    db.expire_all()
    return db


def grant(admin_client, account, **overrides):
    payload = {
        'accountId': account.id,
        'role': 'ADMIN',
        'capabilities': ['staff.manage'],
        'reason': '测试授权',
    }
    payload.update(overrides)
    return api(admin_client, '/admin/staff', payload)


def test_same_account_cookie_is_owner_and_employee_and_uses_one_device_quota(client, db):
    admin = make_admin(db)
    target = make_account(db)
    db.commit()

    admin_client = new_client(client.app)
    assert login_customer(admin_client, admin.email).status_code == 200
    me = api(admin_client, '/auth/me', method='GET')
    assert me.status_code == 200, me.text
    assert me.json()['data']['accountId'] == admin.account_id
    assert me.json()['data']['isSuperAdmin'] is True

    granted = grant(admin_client, target, discountLevel=2)
    assert granted.status_code == 200, granted.text
    row = next(item for item in granted.json()['data']['items']
               if item['accountId'] == target.id)
    assert row['isSuperAdmin'] is False
    assert row['discountLevel'] == 2

    one = new_client(client.app)
    login = login_customer(one, target.email)
    assert login.status_code == 200, login.text
    # Same cookie is simultaneously the normal owner identity and the employee identity.
    owner = api(one, '/customer/auth/me', method='GET').json()['data']
    assert owner['authKind'] == 'REGISTERED'
    assert owner['account']['id'] == target.id
    staff_me = api(one, '/auth/me', method='GET')
    assert staff_me.status_code == 200, staff_me.text
    assert staff_me.json()['data']['role'] == 'ADMIN'
    assert staff_me.json()['data']['capabilities'] == ['staff.manage']

    from starlette.requests import Request
    from app.auth import resolve_actor

    token = admin_client.cookies.get('dormprint_account_session')
    actor = resolve_actor(db, Request({
        'type': 'http',
        'headers': [(b'cookie', f'dormprint_account_session={token}'.encode())],
    }))
    assert actor.staff_id == admin.id
    assert actor.staff_session_id == actor.account_session_id

    two = new_client(client.app)
    assert login_customer(two, target.email).status_code == 200
    third = new_client(client.app)
    rejected = login_customer(third, target.email)
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()['error']['code'] == 'DEVICE_LIMIT_REACHED'

    fresh_session(db)
    sessions = list(db.scalars(select(AuthSession).where(
        AuthSession.account_id == target.id,
        AuthSession.kind == 'CUSTOMER', AuthSession.revoked_at.is_(None))))
    assert len(sessions) == 2
    assert db.scalar(select(func.count()).select_from(AuthSession).where(
        AuthSession.kind == 'STAFF', AuthSession.staff_id == row['id'])) == 0
    # Staff-account logins use the 7 day elevated window, not 30 days.
    for session in sessions:
        assert timedelta(days=6, hours=23) < session.expires_at - session.created_at \
            <= timedelta(days=7, seconds=1)


def test_plain_account_has_no_employee_identity_or_panel(client, db):
    account = make_account(db)
    db.commit()
    browser = new_client(client.app)
    assert login_customer(browser, account.email).status_code == 200

    assert api(browser, '/customer/auth/me', method='GET').status_code == 200
    assert api(browser, '/auth/me', method='GET').status_code == 401
    blocked = api(browser, '/admin/staff', method='GET')
    assert blocked.status_code in (401, 403)
    assert blocked.json()['error']['code'] == 'AUTH_REQUIRED'

    fresh_session(db)
    session = db.scalar(select(AuthSession).where(
        AuthSession.account_id == account.id, AuthSession.kind == 'CUSTOMER'))
    assert session is not None
    assert timedelta(days=29, hours=23) < session.expires_at - session.created_at \
        <= timedelta(days=30, seconds=1)


def test_courier_is_staff_but_never_admin(client, db):
    admin = make_admin(db)
    courier_account = make_account(db)
    db.commit()
    admin_client = new_client(client.app)
    assert login_customer(admin_client, admin.email).status_code == 200
    assert grant(admin_client, courier_account, role='COURIER', capabilities=[]).status_code == 200

    courier = new_client(client.app)
    assert login_customer(courier, courier_account.email).status_code == 200
    me = api(courier, '/auth/me', method='GET').json()['data']
    assert me['role'] == 'COURIER'
    assert me['capabilities'] == []
    assert api(courier, '/auth/sessions', method='GET').status_code == 200
    denied = api(courier, '/admin/staff', method='GET')
    assert denied.status_code in (401, 403)
    assert denied.json()['error']['code'] in ('AUTH_REQUIRED', 'FORBIDDEN')


def test_disable_and_revoke_take_effect_immediately_and_reenable_restores(client, db):
    admin = make_admin(db)
    target = make_account(db)
    db.commit()
    admin_client = new_client(client.app)
    assert login_customer(admin_client, admin.email).status_code == 200
    granted = grant(admin_client, target)
    assert granted.status_code == 200, granted.text
    row = next(item for item in granted.json()['data']['items'] if item['accountId'] == target.id)

    browser = new_client(client.app)
    assert login_customer(browser, target.email).status_code == 200
    assert api(browser, '/auth/me', method='GET').status_code == 200

    disabled = api(admin_client, f"/admin/staff/{row['id']}", {
        'enabled': False, 'status': 'DISABLED', 'reason': '停用测试',
        'expectedVersion': row['version'],
    }, method='PATCH')
    assert disabled.status_code == 200, disabled.text
    disabled_row = next(item for item in disabled.json()['data']['items']
                        if item['id'] == row['id'])
    assert disabled_row['enabled'] is False

    # Elevated identity is gone but the same normal customer login still works.
    assert api(browser, '/auth/me', method='GET').status_code == 401
    customer = api(browser, '/customer/auth/me', method='GET')
    assert customer.status_code == 200
    assert customer.json()['data']['authKind'] == 'REGISTERED'
    assert customer.json()['data']['account']['id'] == target.id
    assert api(browser, '/admin/staff', method='GET').status_code in (401, 403)

    enabled = api(admin_client, f"/admin/staff/{row['id']}", {
        'enabled': True, 'status': 'ACTIVE', 'reason': '恢复测试',
        'expectedVersion': disabled_row['version'],
    }, method='PATCH')
    assert enabled.status_code == 200, enabled.text
    # The same customer session regains the employee identity immediately.
    assert api(browser, '/auth/me', method='GET').status_code == 200


def test_account_status_change_immediately_removes_customer_and_staff_access(client, db):
    admin = make_admin(db)
    target = make_account(db)
    db.commit()
    admin_client = new_client(client.app)
    assert login_customer(admin_client, admin.email).status_code == 200
    assert grant(admin_client, target).status_code == 200

    browser = new_client(client.app)
    assert login_customer(browser, target.email).status_code == 200
    assert api(browser, '/auth/me', method='GET').status_code == 200

    fresh_session(db)
    account = db.get(Account, target.id)
    account.status = 'SUSPENDED'
    db.commit()

    customer_me = api(browser, '/customer/auth/me', method='GET')
    assert customer_me.status_code == 401 or customer_me.json()['data']['authKind'] == 'ANONYMOUS'
    assert api(browser, '/auth/me', method='GET').status_code == 401


def test_legacy_staff_cookie_is_ignored_and_confers_no_identity(
        client, client_factory, staff_factory, db):
    from app.core import token_hash

    legacy = staff_factory()
    token = f'legacy-{uuid4().hex}'
    db.add(AuthSession(kind='STAFF', staff_id=legacy.id, token_hash=token_hash(token),
                       expires_at=utcnow() + timedelta(days=7)))
    db.commit()
    other = make_account(db)
    db.commit()

    browser = new_client(client.app)
    browser.cookies.set('dormprint_staff_session', token, domain='testserver.local')
    # A standalone staff cookie is not an identity at all.
    assert api(browser, '/auth/me', method='GET').status_code == 401
    assert api(browser, '/admin/staff', method='GET').status_code in (401, 403)
    assert api(browser, '/auth/sessions', method='GET').status_code == 401

    assert login_customer(browser, other.email).status_code == 200
    assert not browser.cookies.get('dormprint_staff_session')
    assert api(browser, '/auth/me', method='GET').status_code == 401
    assert api(browser, '/admin/staff', method='GET').status_code in (401, 403)
    customer = api(browser, '/customer/auth/me', method='GET')
    assert customer.status_code == 200
    assert customer.json()['data']['account']['id'] == other.id


def test_elevated_rights_need_authentication_within_seven_days_but_customer_continues(
        client, client_factory, db):
    from app.core import token_hash
    from auth_helpers import grant_staff

    account = make_account(db)
    staff = grant_staff(db, account)
    db.commit()

    def session_client(created_ago: timedelta):
        token = f'session-{uuid4().hex}'
        db.add(AuthSession(kind='CUSTOMER', account_id=account.id,
                           principal_id=account.principal_id,
                           token_hash=token_hash(token),
                           created_at=utcnow() - created_ago,
                           expires_at=utcnow() + timedelta(days=20)))
        db.commit()
        browser = client_factory()
        browser.cookies.set('dormprint_account_session', token)
        return browser

    old = session_client(timedelta(days=8))
    assert api(old, '/customer/auth/me', method='GET').status_code == 200
    assert api(old, '/auth/me', method='GET').status_code == 401

    recent = session_client(timedelta(days=2))
    assert api(recent, '/auth/me', method='GET').status_code == 200
    assert api(recent, '/auth/me', method='GET').json()['data']['id'] == staff.id


def test_legacy_staff_login_is_account_login_and_never_raw_staff_password(client, db):
    from app.auth import hash_password

    admin = make_admin(db)
    db.commit()
    # A stale/legacy staff password on the linked row must be irrelevant.
    admin.password_hash = hash_password('OnlyStaffKnowsThis-42!')
    db.commit()

    browser = new_client(client.app)
    login = login_staff(browser, admin.username, PASSWORD)
    assert login.status_code == 200, login.text
    assert browser.cookies.get('dormprint_account_session')
    assert not browser.cookies.get('dormprint_staff_session')
    assert api(browser, '/customer/auth/me', method='GET').status_code == 200

    fresh_session(db)
    assert db.scalar(select(func.count()).select_from(AuthSession).where(
        AuthSession.kind == 'STAFF', AuthSession.staff_id == admin.id)) == 0

    assert login_staff(new_client(client.app), admin.username,
                       'OnlyStaffKnowsThis-42!').status_code == 401

    plain = make_account(db)
    db.commit()
    denied = login_staff(new_client(client.app), plain.email, PASSWORD)
    assert denied.status_code == 403
    assert denied.json()['error']['code'] == 'STAFF_ACCESS_DENIED'


def test_grant_requires_superadmin_for_super_flag_and_cannot_exceed_own_caps(client, db):
    first = make_admin(db)
    ordinary_account = make_account(db)
    target = make_account(db)
    unverified = make_account(db, verified=False)
    db.commit()
    first_client = new_client(client.app)
    assert login_customer(first_client, first.email).status_code == 200
    ordinary = grant(first_client, ordinary_account, capabilities=['staff.manage'])
    assert ordinary.status_code == 200, ordinary.text
    ordinary_row = next(item for item in ordinary.json()['data']['items']
                        if item['accountId'] == ordinary_account.id)

    admin_client = new_client(client.app)
    assert login_customer(admin_client, ordinary_account.email).status_code == 200
    assert api(admin_client, '/auth/me', method='GET').status_code == 200

    escalated = grant(admin_client, target, isSuperAdmin=True)
    assert escalated.status_code == 403, escalated.text
    assert escalated.json()['error']['code'] == 'SUPER_ADMIN_REQUIRED'

    over_caps = grant(admin_client, target, capabilities=['payment.manage'])
    assert over_caps.status_code == 403
    assert over_caps.json()['error']['code'] == 'CAPABILITY_ESCALATION'

    missing_account = api(admin_client, '/admin/staff', {
        'role': 'ADMIN', 'capabilities': ['staff.manage'], 'reason': '缺少账号',
    })
    assert missing_account.status_code == 422

    not_verified = grant(admin_client, unverified)
    assert not_verified.status_code == 409
    assert not_verified.json()['error']['code'] == 'ACCOUNT_NOT_VERIFIED'

    duplicate = grant(admin_client, ordinary_account)
    assert duplicate.status_code == 409
    assert duplicate.json()['error']['code'] == 'STAFF_ACCOUNT_ALREADY_GRANTED'

    swapped = api(admin_client, f"/admin/staff/{ordinary_row['id']}", {
        'accountId': target.id, 'reason': '换绑', 'expectedVersion': ordinary_row['version'],
    }, method='PATCH')
    assert swapped.status_code == 422
    assert swapped.json()['error']['code'] == 'ACCOUNT_SWAP_FORBIDDEN'

    # SUPER_ADMIN grant is allowed only for an existing active superadmin.
    second = make_account(db)
    db.commit()
    promoted = grant(first_client, second, isSuperAdmin=True)
    assert promoted.status_code == 200, promoted.text
    promoted_row = next(item for item in promoted.json()['data']['items']
                        if item['accountId'] == second.id)
    assert promoted_row['isSuperAdmin'] is True
    assert promoted_row['capabilities'] == ['*']

    protected = api(admin_client, f"/admin/staff/{promoted_row['id']}", {
        'role': 'COURIER', 'reason': '降级超管',
        'expectedVersion': promoted_row['version'],
    }, method='PATCH')
    assert protected.status_code == 403
    assert protected.json()['error']['code'] == 'SUPER_ADMIN_PROTECTED'


def test_last_superadmin_blocks_account_deletion_request(client, db):
    from app.auth import active_super_admin_count, last_super_admin_blocker

    admin = make_admin(db)
    db.commit()
    assert active_super_admin_count(db) == 1
    assert last_super_admin_blocker(db, account_id=admin.account_id) == 'LAST_SUPER_ADMIN'

    browser = new_client(client.app)
    assert login_customer(browser, admin.email).status_code == 200
    requested = api(browser, '/customer/account/deletion-request', {'reason': '测试注销'})
    assert requested.status_code == 200, requested.text
    assert 'LAST_SUPER_ADMIN' in requested.json()['data']['blockers']
    stored = db.scalar(select(AuthSession).where(AuthSession.account_id == admin.account_id))
    assert stored is not None
    assert db.scalar(select(func.count()).select_from(Staff)) == 1


def test_initial_admin_binds_verified_account_and_refuses_unverified_or_standalone(db):
    from app.auth import create_initial_admin

    with pytest.raises(DomainError) as missing:
        create_initial_admin(db, 'bootstrap', PASSWORD)
    assert missing.value.code == 'INITIAL_ADMIN_EMAIL_REQUIRED'

    unverified = make_account(db, verified=False)
    db.commit()
    with pytest.raises(DomainError) as rejected:
        create_initial_admin(db, 'bootstrap', PASSWORD, email=unverified.email)
    assert rejected.value.code == 'INITIAL_ADMIN_ACCOUNT_NOT_VERIFIED'

    verified = make_account(db)
    db.commit()
    with pytest.raises(DomainError) as mismatch:
        create_initial_admin(db, 'bootstrap', 'DifferentPass-42!', email=verified.email)
    assert mismatch.value.code == 'INITIAL_ADMIN_CREDENTIAL_MISMATCH'

    staff = create_initial_admin(db, 'bootstrap', PASSWORD, email=verified.email)
    db.flush()
    assert staff.account_id == verified.id
    assert staff.is_super_admin is True
    assert staff.role == 'ADMIN'
    assert staff.password_hash == ''
    assert staff.email == verified.email
    assert staff.email_verified is True

    # Exactly one superadmin: a second bootstrap is refused rather than
    # silently creating a second privileged account.
    with pytest.raises(DomainError) as second:
        create_initial_admin(db, 'bootstrap-two', PASSWORD,
                             email=f'bootstrap-{uuid4().hex[:8]}@example.invalid')
    assert second.value.code == 'SUPER_ADMIN_ALREADY_INITIALIZED'


def test_staff_dto_exposes_account_super_flag_and_discount(client, db):
    admin = make_admin(db)
    target = make_account(db)
    db.commit()
    admin_client = new_client(client.app)
    assert login_customer(admin_client, admin.email).status_code == 200

    created = grant(admin_client, target, discountLevel=4)
    assert created.status_code == 200, created.text
    row = next(item for item in created.json()['data']['items'] if item['accountId'] == target.id)
    assert row['isSuperAdmin'] is False
    assert row['discountLevel'] == 4

    changed = api(admin_client, f"/admin/staff/{row['id']}", {
        'discountLevel': 6, 'reason': '六折', 'expectedVersion': row['version'],
    }, method='PATCH')
    assert changed.status_code == 200, changed.text
    changed_row = next(item for item in changed.json()['data']['items'] if item['id'] == row['id'])
    assert changed_row['discountLevel'] == 6

    cleared = api(admin_client, f"/admin/staff/{row['id']}", {
        'discountLevel': None, 'reason': '取消折扣',
        'expectedVersion': changed_row['version'],
    }, method='PATCH')
    assert cleared.status_code == 200, cleared.text
    assert next(item for item in cleared.json()['data']['items']
                if item['id'] == row['id'])['discountLevel'] is None

    invalid = api(admin_client, f"/admin/staff/{row['id']}", {
        'discountLevel': 7, 'reason': '非法', 'expectedVersion': changed_row['version'],
    }, method='PATCH')
    assert invalid.status_code == 422


def test_grantable_accounts_search_is_minimal_and_staff_manage_scoped(client, db):
    admin = make_admin(db)
    target = make_account(db, display_name='Grantable Target')
    db.commit()

    admin_client = new_client(client.app)
    assert login_staff(admin_client, admin.email).status_code == 200

    found = api(admin_client, '/admin/staff/grantable-accounts?q=Grantable', method='GET')
    assert found.status_code == 200, found.text
    items = found.json()['data']['items']
    item = next(row for row in items if row['id'] == target.id)
    assert set(item) == {'id', 'displayName', 'emailMasked', 'verified'}
    assert item['displayName'] == 'Grantable Target'
    assert item['verified'] is True
    assert item['emailMasked'] != target.email
    assert target.email not in found.text
    assert 'phone' not in found.text
    assert 'principalId' not in found.text

    exact = api(admin_client, f'/admin/staff/grantable-accounts?q={target.email}', method='GET')
    assert exact.status_code == 200, exact.text
    assert [row['id'] for row in exact.json()['data']['items']] == [target.id]

    unknown = api(admin_client, '/admin/staff/grantable-accounts?q=no-such-person', method='GET')
    assert unknown.status_code == 200, unknown.text
    assert unknown.json()['data']['items'] == []

    short = api(admin_client, '/admin/staff/grantable-accounts?q=ab', method='GET')
    assert short.status_code == 422, short.text
    assert short.json()['error']['code'] == 'SEARCH_QUERY_TOO_SHORT'

    oversized = api(admin_client, '/admin/staff/grantable-accounts?q=Grantable&limit=21',
                    method='GET')
    assert oversized.status_code == 422, oversized.text


def test_grantable_accounts_excludes_unverified_suspended_deleted_and_granted(client, db):
    admin = make_admin(db)
    unverified = make_account(db, verified=False, display_name='Unverified U')
    suspended = make_account(db, status='SUSPENDED', display_name='Suspended S')
    deleted = make_account(db, display_name='Deleted D')
    deleted.deleted_at = utcnow()
    granted = make_account(db, display_name='Granted G')
    grant_staff(db, granted, role='COURIER')
    eligible = make_account(db, display_name='Eligible E')
    db.commit()

    admin_client = new_client(client.app)
    assert login_staff(admin_client, admin.email).status_code == 200

    for account in (unverified, suspended, deleted, granted):
        response = api(admin_client, f'/admin/staff/grantable-accounts?q={account.email}',
                       method='GET')
        assert response.status_code == 200, response.text
        assert response.json()['data']['items'] == [], account.id

    response = api(admin_client, f'/admin/staff/grantable-accounts?q={eligible.email}',
                   method='GET')
    assert response.status_code == 200, response.text
    assert [row['id'] for row in response.json()['data']['items']] == [eligible.id]


def test_grantable_accounts_permission_matrix_requires_staff_manage(client, db):
    eligible = make_account(db, display_name='Permission Eligible')
    customer = make_account(db)
    customer_admin = make_account(db, display_name='Customer Manager')
    grant_staff(db, customer_admin, role='ADMIN', capabilities=['customer.manage'])
    staff_manager = make_account(db, display_name='Staff Manager')
    grant_staff(db, staff_manager, role='ADMIN', capabilities=['staff.manage'])
    db.commit()

    anonymous = new_client(client.app)
    denied = api(anonymous, '/admin/staff/grantable-accounts?q=Eligible', method='GET')
    assert denied.status_code == 401, denied.text

    customer_client = new_client(client.app)
    assert login_customer(customer_client, customer.email).status_code == 200
    denied = api(customer_client, '/admin/staff/grantable-accounts?q=Eligible', method='GET')
    assert denied.status_code in (401, 403), denied.text

    customer_manager_client = new_client(client.app)
    assert login_staff(customer_manager_client, customer_admin.email).status_code == 200
    denied = api(customer_manager_client, '/admin/staff/grantable-accounts?q=Eligible',
                 method='GET')
    assert denied.status_code == 403, denied.text
    assert denied.json()['error']['code'] == 'FORBIDDEN'

    staff_manager_client = new_client(client.app)
    assert login_staff(staff_manager_client, staff_manager.email).status_code == 200
    allowed = api(staff_manager_client, '/admin/staff/grantable-accounts?q=Eligible',
                  method='GET')
    assert allowed.status_code == 200, allowed.text
    assert [row['id'] for row in allowed.json()['data']['items']] == [eligible.id]

    granted = api(staff_manager_client, '/admin/staff', {
        'accountId': eligible.id,
        'role': 'COURIER',
        'reason': 'staff.manage 直接授权',
    })
    assert granted.status_code == 200, granted.text
    assert next(row for row in granted.json()['data']['items']
                if row['accountId'] == eligible.id)['role'] == 'COURIER'

    # The static search excludes the just-granted account, so a repeated
    # selection cannot create a duplicate Staff row.
    after = api(staff_manager_client, '/admin/staff/grantable-accounts?q=Eligible',
                method='GET')
    assert after.status_code == 200, after.text
    assert after.json()['data']['items'] == []
