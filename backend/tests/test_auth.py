"""Auth, account, staff and order-access module tests (real PostgreSQL)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models import (
    AccessGrant,
    Account,
    AuthSession,
    DeletionRequest,
    ExportJob,
    OneTimeToken,
    Order,
    Principal,
    Referral,
    Staff,
    utcnow,
)
APP_MODULES = ['config', 'auth', 'growth', 'mail']


from auth_helpers import (
    PASSWORD,
    api,
    db_session,
    latest_session,
    login_customer,
    login_staff,
    make_account,
    make_admin,
    make_order,
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


def refresh(db):
    db.expire_all()
    return db


def token_of(url: str) -> str:
    fragment = urlparse(url).fragment
    values = parse_qs(fragment)
    assert 'token' in values, url
    return values['token'][0]


# ---------------------------------------------------------------------------
# Guest / customer / staff session separation
# ---------------------------------------------------------------------------


def test_guest_customer_and_staff_sessions_are_independent(client):
    anonymous = api(client, '/customer/session', {})
    assert anonymous.status_code == 200, anonymous.text
    assert anonymous.json()['data']['authKind'] == 'ANONYMOUS'

    me = api(client, '/customer/auth/me', method='GET')
    assert me.status_code == 200
    assert me.json()['data']['role'] == 'CUSTOMER'

    # A customer cookie must never authenticate an employee endpoint.
    assert api(client, '/auth/me', method='GET').status_code == 401


def test_customer_registration_requires_real_mail_and_stores_only_token_hash(client, db, monkeypatch):
    import app.auth as auth

    email = f'{uuid4().hex}@example.invalid'
    monkeypatch.setattr(auth, '_mail_available', lambda database: False)
    unavailable = api(client, '/customer/auth/register', {'email': email, 'password': PASSWORD,
                                                          'acceptedTerms': True})
    assert unavailable.status_code == 503
    assert unavailable.json()['error']['code'] == 'EMAIL_SERVICE_UNAVAILABLE'
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Account).where(Account.email == auth.normalize_email(email))) == 0

    sent = []

    def enqueue(database, key, to, variables, business_key):
        sent.append({'key': key, 'to': to, 'variables': variables, 'business_key': business_key})

    inviter = make_account(db)
    inviter.invite_code = 'FRIEND-1'
    db.commit()

    monkeypatch.setattr(auth, '_mail_available', lambda database: True)
    monkeypatch.setattr(auth, '_enqueue_email', enqueue)
    created = api(client, '/customer/auth/register', {'email': email, 'password': PASSWORD,
                                                      'acceptedTerms': True, 'inviteCode': 'FRIEND-1'})
    assert created.status_code == 200, created.text
    data = created.json()['data']
    assert data['registrationChallengeId']
    assert data['mailStatus'] == 'QUEUED'
    assert sent and sent[0]['key'] == 'CUSTOMER_VERIFY_EMAIL'
    assert sent[0]['to'] == auth.normalize_email(email)

    db.expire_all()
    account = db.scalar(select(Account).where(Account.email == auth.normalize_email(email)))
    assert account is not None and account.status == 'PENDING_EMAIL'
    challenge = db.get(OneTimeToken, data['registrationChallengeId'])
    assert challenge is not None
    assert len(challenge.token_hash) == 64
    assert challenge.payload.get('inviteCode') == 'FRIEND-1'
    raw = token_of(sent[0]['variables']['actionUrl'])
    assert challenge.token_hash != raw
    relation = db.scalar(select(Referral).where(Referral.invitee_id == account.id))
    assert relation is not None and relation.inviter_id == inviter.id

    resent = api(client, '/customer/auth/resend-verification', {'email': email})
    assert resent.status_code == 200
    assert resent.json()['data']['registrationChallengeId'] is None
    assert len(sent) == 2


def test_registration_rejects_an_unknown_invite_code(client, db, monkeypatch):
    import app.auth as auth

    monkeypatch.setattr(auth, '_mail_available', lambda database: True)
    monkeypatch.setattr(auth, '_enqueue_email',
                        lambda database, key, to, variables, business_key: None)
    email = f'{uuid4().hex}@example.invalid'
    rejected = api(client, '/customer/auth/register', {
        'email': email, 'password': PASSWORD, 'acceptedTerms': True,
        'inviteCode': 'NOT-A-REAL-INVITE',
    })
    assert rejected.status_code == 422
    assert rejected.json()['error']['code'] == 'REFERRAL_INVALID'
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Account).where(
        Account.email == auth.normalize_email(email))) == 0


def test_registration_writes_a_real_mail_outbox_row(client, db, monkeypatch):
    from app.core import seal
    from app.mail import mail_available
    from app.models import ConfigSection, EmailJob, MailTemplate
    from app.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, 'allow_insecure_smtp', True, raising=False)
    monkeypatch.setattr(settings, 'allow_internal_smtp', True, raising=False)
    monkeypatch.setattr(settings, 'mail_allowed_hosts', '127.0.0.1', raising=False)
    db.add(ConfigSection(section='mail', version=1, data={
        'version': 1, 'enabled': True, 'host': '127.0.0.1', 'port': 25,
        'security': 'NONE', 'authEnabled': False, 'username': '',
        'fromName': 'DormPrint 测试', 'fromAddress': 'mailer@example.invalid',
        'replyTo': '', 'timeoutSeconds': 5, 'allowInsecure': True,
        'secretCiphertext': seal({'secret': 'unused'}),
        'lastTest': {'status': 'PASS', 'time': utcnow().isoformat(), 'message': ''},
    }))
    db.commit()
    assert mail_available(db) is True

    email = f'{uuid4().hex}@example.invalid'
    response = api(client, '/customer/auth/register', {
        'email': email, 'password': PASSWORD, 'acceptedTerms': True,
    })
    assert response.status_code == 200, response.text
    assert response.json()['data']['mailStatus'] == 'QUEUED'
    db.expire_all()
    job = db.scalar(select(EmailJob).where(EmailJob.business_key.like('customer-verify:%')))
    assert job is not None
    assert job.template_key == 'CUSTOMER_VERIFY_EMAIL'
    assert job.status == 'QUEUED'
    assert job.protected_payload and 'actionUrl' not in job.protected_payload
    template = db.scalar(select(MailTemplate).where(MailTemplate.key == 'CUSTOMER_VERIFY_EMAIL'))
    assert template is not None and template.enabled is True


def test_customer_login_verification_and_session_isolation(client, db):
    unverified = make_account(db, verified=False)
    db.commit()
    pending = login_customer(client, unverified.email)
    assert pending.status_code == 403
    assert pending.json()['error']['code'] == 'EMAIL_NOT_VERIFIED'

    account = make_account(db)
    db.commit()
    wrong = login_customer(client, account.email, 'WrongPass123!')
    assert wrong.status_code == 401
    assert wrong.json()['error']['code'] == 'AUTH_INVALID_CREDENTIALS'
    missing = login_customer(client, f'{uuid4().hex}@example.invalid')
    assert missing.status_code == 401
    assert missing.json()['error']['code'] == 'AUTH_INVALID_CREDENTIALS'

    ok = login_customer(client, account.email)
    assert ok.status_code == 200, ok.text
    session = ok.json()['data']
    assert session['authKind'] == 'REGISTERED'
    assert session['account']['verified'] is True
    assert '***' in session['account']['emailMasked']

    me = api(client, '/customer/auth/me', method='GET')
    assert me.json()['data']['account']['id'] == account.id
    # The customer cookie is not an employee credential.
    assert api(client, '/auth/me', method='GET').status_code == 401
    # A guest cookie has no account privileges.
    other = new_client(client.app)
    assert api(other, '/customer/account', method='GET').status_code in (401, 403)


def test_email_verification_token_is_consumed_once_and_activates_account(client, db, monkeypatch):
    import app.auth as auth

    calls = []
    monkeypatch.setattr(auth, '_on_registration_verified',
                        lambda database, verified_account: calls.append(verified_account.id))
    account = make_account(db, verified=False)
    raw = f'verify-{uuid4().hex}'
    from app.core import token_hash

    challenge = OneTimeToken(kind='REGISTRATION', token_hash=token_hash(raw), account_id=account.id,
                             payload={'inviteCode': None}, expires_at=utcnow() + timedelta(minutes=30))
    db.add(challenge)
    db.commit()

    verified = api(client, '/customer/auth/verify-email', {'token': raw})
    assert verified.status_code == 200, verified.text
    assert verified.json()['data']['authKind'] == 'REGISTERED'
    db.expire_all()
    assert db.get(Account, account.id).status == 'ACTIVE'
    assert db.get(Account, account.id).verified_at is not None
    assert calls == [account.id]

    replay = api(new_client(client.app), '/customer/auth/verify-email', {'token': raw})
    assert replay.status_code == 410
    assert replay.json()['error']['code'] == 'EMAIL_TOKEN_INVALID'


def test_third_device_is_rejected_and_revocation_releases_slot(client, db):
    account = make_account(db)
    db.commit()
    clients = [new_client(client.app) for _ in range(3)]
    first = login_customer(clients[0], account.email)
    # Same browser/device re-login rotates the session but does not consume another slot.
    again = login_customer(clients[0], account.email)
    assert again.status_code == 200
    second = login_customer(clients[1], account.email)
    third = login_customer(clients[2], account.email)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert third.status_code == 409
    assert third.json()['error']['code'] == 'DEVICE_LIMIT_REACHED'
    assert '2' in third.json()['error']['message']

    db.expire_all()
    active = db.scalar(select(func.count()).select_from(AuthSession).where(
        AuthSession.kind == 'CUSTOMER', AuthSession.account_id == account.id,
        AuthSession.revoked_at.is_(None), AuthSession.expires_at > utcnow()))
    assert active == 2

    # Existing devices stay online; the rejected request did not evict anyone.
    assert api(clients[0], '/customer/account', method='GET').status_code == 200
    assert api(clients[1], '/customer/account', method='GET').status_code == 200

    sessions = api(clients[0], '/customer/account/sessions', method='GET')
    assert sessions.status_code == 200
    items = sessions.json()['data']['items']
    assert len(items) == 2
    assert sum(1 for item in items if item['current']) == 1
    current = next(item for item in items if item['current'])
    revoked = api(clients[0], '/customer/account/sessions/revoke', {'sessionId': current['id']})
    assert revoked.status_code == 200
    assert revoked.json()['data']['revoked'] is True

    retry = login_customer(clients[2], account.email)
    assert retry.status_code == 200, retry.text
    db.expire_all()
    active = db.scalar(select(func.count()).select_from(AuthSession).where(
        AuthSession.kind == 'CUSTOMER', AuthSession.account_id == account.id,
        AuthSession.revoked_at.is_(None), AuthSession.expires_at > utcnow()))
    assert active <= 2


def test_third_device_can_replace_a_selected_session(client, db):
    account = make_account(db)
    db.commit()
    clients = [new_client(client.app) for _ in range(3)]
    assert login_customer(clients[0], account.email).status_code == 200
    assert login_customer(clients[1], account.email).status_code == 200
    rejected = login_customer(clients[2], account.email)
    assert rejected.status_code == 409
    assert rejected.json()['error']['code'] == 'DEVICE_LIMIT_REACHED'
    sessions = rejected.json()['error']['details']['sessions']
    assert len(sessions) == 2
    assert all(item['id'] for item in sessions)

    replaced = api(clients[2], '/customer/auth/login', {
        'email': account.email,
        'password': PASSWORD,
        'replaceSessionId': sessions[0]['id'],
    })
    assert replaced.status_code == 200, replaced.text
    assert api(clients[2], '/customer/account/sessions', method='GET').status_code == 200
    remaining = [api(item, '/customer/account/sessions', method='GET').status_code for item in clients[:2]]
    assert sorted(remaining) == [200, 403], remaining

    db.expire_all()
    active = db.scalar(select(func.count()).select_from(AuthSession).where(
        AuthSession.kind == 'CUSTOMER', AuthSession.account_id == account.id,
        AuthSession.revoked_at.is_(None), AuthSession.expires_at > utcnow()))
    assert active == 2


def test_device_replacement_rejects_unknown_or_revoked_session(client, db):
    account = make_account(db)
    db.commit()
    clients = [new_client(client.app) for _ in range(4)]
    assert login_customer(clients[0], account.email).status_code == 200
    assert login_customer(clients[1], account.email).status_code == 200

    unknown = login_customer(clients[2], account.email)
    invalid = api(clients[2], '/customer/auth/login', {
        'email': account.email, 'password': PASSWORD,
        'replaceSessionId': 'missing-session',
    })
    assert invalid.status_code == 409
    assert invalid.json()['error']['code'] == 'DEVICE_REPLACEMENT_INVALID'
    versions = unknown.json()['error']['details']['sessions']
    assert len(versions) == 2

    target = versions[0]['id']
    assert api(clients[2], '/customer/auth/login', {
        'email': account.email, 'password': PASSWORD,
        'replaceSessionId': target,
    }).status_code == 200
    fourth = login_customer(clients[3], account.email)
    assert fourth.status_code == 409
    revoked = api(clients[3], '/customer/auth/login', {
        'email': account.email, 'password': PASSWORD,
        'replaceSessionId': target,
    })
    assert revoked.status_code == 409
    assert revoked.json()['error']['code'] == 'DEVICE_REPLACEMENT_INVALID'


def test_concurrent_third_device_never_oversubscribes(client, db):
    account = make_account(db)
    db.commit()
    clients = [new_client(client.app) for _ in range(3)]
    barrier = __import__('threading').Barrier(3)

    def attempt(item):
        barrier.wait(timeout=10)
        return login_customer(item, account.email).status_code

    with ThreadPoolExecutor(max_workers=3) as pool:
        statuses = list(pool.map(attempt, clients))
    assert sorted(statuses) == [200, 200, 409], statuses
    db.expire_all()
    active = db.scalar(select(func.count()).select_from(AuthSession).where(
        AuthSession.kind == 'CUSTOMER', AuthSession.account_id == account.id,
        AuthSession.revoked_at.is_(None), AuthSession.expires_at > utcnow()))
    assert active == 2


def test_staff_account_uses_the_unified_customer_two_device_limit(client, db):
    admin = make_admin(db)
    db.commit()
    clients = [new_client(client.app) for _ in range(3)]
    assert login_staff(clients[0], admin.username).status_code == 200
    assert login_staff(clients[1], admin.username).status_code == 200
    third = login_staff(clients[2], admin.username)
    assert third.status_code == 409
    assert third.json()['error']['code'] == 'DEVICE_LIMIT_REACHED'
    db.expire_all()
    for browser in clients[:2]:
        assert browser.cookies.get('dormprint_account_session')
        assert not browser.cookies.get('dormprint_staff_session')
    active = db.scalar(select(func.count()).select_from(AuthSession).where(
        AuthSession.kind == 'CUSTOMER', AuthSession.account_id == admin.account_id,
        AuthSession.revoked_at.is_(None), AuthSession.expires_at > utcnow()))
    assert active == 2
    assert db.scalar(select(func.count()).select_from(AuthSession).where(
        AuthSession.kind == 'STAFF', AuthSession.staff_id == admin.id)) == 0


def test_password_reset_consumes_token_and_revokes_sessions(client, db):
    account = make_account(db)
    db.commit()
    one = new_client(client.app)
    two = new_client(client.app)
    assert login_customer(one, account.email).status_code == 200
    assert login_customer(two, account.email).status_code == 200

    raw = f'reset-{uuid4().hex}'
    from app.core import token_hash

    db.add(OneTimeToken(kind='CUSTOMER_RESET', token_hash=token_hash(raw), account_id=account.id,
                        payload={}, expires_at=utcnow() + timedelta(minutes=30)))
    db.commit()
    reset = api(new_client(client.app), '/customer/auth/reset-password',
                {'token': raw, 'newPassword': 'NewStrongPass456!'})
    assert reset.status_code == 200, reset.text
    assert api(one, '/customer/account', method='GET').status_code in (401, 403)
    assert api(two, '/customer/account', method='GET').status_code in (401, 403)

    relogin = new_client(client.app)
    assert login_customer(relogin, account.email, 'NewStrongPass456!').status_code == 200
    replay = api(new_client(client.app), '/customer/auth/reset-password',
                 {'token': raw, 'newPassword': 'AnotherStrong789!'})
    assert replay.status_code == 410


# ---------------------------------------------------------------------------
# Explicit guest linkage and single-order import
# ---------------------------------------------------------------------------


def test_link_guest_is_explicit_rotates_guest_and_keeps_order_owner(client, db):
    guest_client = new_client(client.app)
    db.expire_all()
    guest_session = latest_session(db, 'GUEST')
    assert guest_session is not None
    guest_principal = guest_session.principal_id
    order = make_order(db, guest_principal)
    account = make_account(db)
    db.commit()

    assert login_customer(guest_client, account.email).status_code == 200
    old_guest_cookie = guest_client.cookies.get('dormprint_guest_session')
    assert old_guest_cookie
    linked = api(guest_client, '/customer/account/link-guest', {'confirm': True})
    assert linked.status_code == 200, linked.text
    data = linked.json()['data']
    assert data['rotationCompleted'] is True
    assert data['customerPrincipalId'] == account.principal_id

    db.expire_all()
    assert db.get(Principal, guest_principal).account_id == account.id
    assert db.get(Order, order.id).principal_id == guest_principal
    assert db.get(Account, account.id).status == 'ACTIVE'
    assert guest_client.cookies.get('dormprint_guest_session') != old_guest_cookie

    stale = new_client(client.app)
    stale.cookies.set('dormprint_guest_session', old_guest_cookie)
    stale_me = api(stale, '/customer/auth/me', method='GET')
    assert stale_me.json()['data']['authKind'] == 'ANONYMOUS'


def test_import_order_derives_limited_grant_and_reissue_revokes_derived_only(client, db):
    owner_client = new_client(client.app)
    db.expire_all()
    owner_principal = latest_session(db, 'GUEST').principal_id
    order = make_order(db, owner_principal)
    admin = make_admin(db)
    account = make_account(db)
    db.commit()

    admin_client = new_client(client.app)
    assert login_staff(admin_client, admin.username).status_code == 200
    issued = api(admin_client, f'/admin/orders/{order.id}/access-links', {'reason': '人工核验后发送'})
    assert issued.status_code == 200, issued.text
    issued_data = issued.json()['data']
    assert set(issued_data['grant']['scopes']) == {'ORDER_VIEW', 'FINAL_PDF_PREVIEW', 'DELIVERY_PROOF_VIEW'}
    first_token = issued_data['accessToken']
    first_grant_id = issued_data['grant']['id']
    assert 'accessToken' not in api(admin_client, f'/admin/orders/{order.id}/access-links', method='GET').text

    exchanged_client = new_client(client.app)
    exchanged = api(exchanged_client, '/order-access/exchange', {'orderId': order.id, 'accessToken': first_token})
    assert exchanged.status_code == 200, exchanged.text
    assert exchanged.json()['data']['access']['orderId'] == order.id
    assert set(exchanged.json()['data']['access']['scopes']) == {
        'ORDER_VIEW', 'FINAL_PDF_PREVIEW', 'DELIVERY_PROOF_VIEW'}
    assert login_customer(exchanged_client, account.email).status_code == 200

    too_limited = api(exchanged_client, '/customer/account/import-order', {'orderId': order.id, 'confirm': True})
    assert too_limited.status_code == 403
    assert too_limited.json()['error']['code'] == 'ORDER_GRANT_SCOPE_DENIED'

    reissued = api(admin_client, f'/admin/orders/{order.id}/access-links', {
        'reason': '凭据丢失补发',
        'reissue': True,
        'scopes': ['ORDER_VIEW', 'FINAL_PDF_PREVIEW', 'DELIVERY_PROOF_VIEW', 'ORDER_IMPORT'],
    })
    assert reissued.status_code == 200, reissued.text
    reissued_data = reissued.json()['data']
    assert first_grant_id in reissued_data['revokedGrantIds']
    second_token = reissued_data['accessToken']
    second_grant_id = reissued_data['grant']['id']

    stale = api(new_client(client.app), '/order-access/exchange', {'orderId': order.id, 'accessToken': first_token})
    assert stale.status_code == 410
    assert stale.json()['error']['code'] == 'ORDER_GRANT_REVOKED'

    import_client = new_client(client.app)
    assert api(import_client, '/order-access/exchange',
               {'orderId': order.id, 'accessToken': second_token}).status_code == 200
    assert login_customer(import_client, account.email).status_code == 200
    imported = api(import_client, '/customer/account/import-order', {'orderId': order.id, 'confirm': True})
    assert imported.status_code == 200, imported.text
    imported_data = imported.json()['data']
    assert imported_data['sourceGrantId'] == second_grant_id
    assert set(imported_data['scopes']) == set(reissued_data['grant']['scopes'])
    assert imported_data['expiresAt'] == reissued_data['grant']['expiresAt']

    db.expire_all()
    derived = db.get(AccessGrant, imported_data['accountOrderGrantId'])
    source = db.get(AccessGrant, second_grant_id)
    assert derived.account_id == account.id
    assert derived.source_grant_id == source.id
    assert derived.expires_at <= source.expires_at
    assert set(derived.scopes).issubset(set(source.scopes))
    assert db.get(Order, order.id).principal_id == owner_principal

    third = api(admin_client, f'/admin/orders/{order.id}/access-links', {
        'reason': '再次补发', 'reissue': True,
        'scopes': ['ORDER_VIEW', 'ORDER_IMPORT'],
    })
    assert third.status_code == 200, third.text
    db.expire_all()
    assert db.get(AccessGrant, second_grant_id).revoked_at is not None
    assert db.get(AccessGrant, derived.id).revoked_at is not None
    assert db.get(Order, order.id).principal_id == owner_principal


def test_order_access_exchange_rejects_tampered_or_expired_grants(client, db):
    guest = new_client(client.app)
    db.expire_all()
    principal = latest_session(db, 'GUEST').principal_id
    order = make_order(db, principal)
    admin = make_admin(db)
    db.commit()
    admin_client = new_client(client.app)
    assert login_staff(admin_client, admin.username).status_code == 200
    issued = api(admin_client, f'/admin/orders/{order.id}/access-links', {'reason': '测试'})
    token = issued.json()['data']['accessToken']
    other_order = make_order(db, principal)
    db.commit()
    mismatch = api(new_client(client.app), '/order-access/exchange',
                   {'orderId': other_order.id, 'accessToken': token})
    assert mismatch.status_code == 401
    forged = api(new_client(client.app), '/order-access/exchange',
                 {'orderId': order.id, 'accessToken': 'not-a-real-token'})
    assert forged.status_code == 401


# ---------------------------------------------------------------------------
# Staff management, temporary credentials and last-admin protection
# ---------------------------------------------------------------------------


def test_courier_grant_is_account_based_and_has_no_staff_password(client, db):
    admin = make_admin(db)
    courier_account = make_account(db, display_name='配送员甲')
    db.commit()
    admin_client = new_client(client.app)
    login = login_staff(admin_client, admin.username)
    assert login.status_code == 200, login.text
    assert login.json()['data']['role'] == 'ADMIN'
    assert login.json()['data']['isSuperAdmin'] is True
    assert api(admin_client, '/auth/me', method='GET').json()['data']['id'] == admin.id

    created = api(admin_client, '/admin/staff', {
        'accountId': courier_account.id, 'role': 'COURIER', 'reason': '新增配送员',
    })
    assert created.status_code == 200, created.text
    courier_row = next(row for row in created.json()['data']['staff']
                       if row['accountId'] == courier_account.id)
    assert courier_row['role'] == 'COURIER'
    assert courier_row['isSuperAdmin'] is False
    assert 'password' not in courier_row and 'passwordHash' not in courier_row

    courier_client = new_client(client.app)
    courier_login = login_staff(courier_client, courier_account.email)
    assert courier_login.status_code == 200, courier_login.text
    assert courier_login.json()['data']['mustChangePassword'] is False
    assert courier_login.json()['data']['accountId'] == courier_account.id
    assert api(courier_client, '/auth/me', method='GET').status_code == 200
    assert api(courier_client, '/admin/staff', method='GET').status_code in (401, 403)

    retired = api(courier_client, '/auth/change-password',
                  {'currentPassword': PASSWORD, 'newPassword': 'ChangedPass456!'})
    assert retired.status_code == 410
    assert retired.json()['error']['code'] == 'STAFF_AUTH_RETIRED'

    temporary = api(admin_client, f"/admin/staff/{courier_row['id']}/issue-temporary-password",
                    {'reason': '不再支持独立临时密码', 'expectedVersion': courier_row['version']})
    assert temporary.status_code == 410
    assert temporary.json()['error']['code'] == 'STAFF_AUTH_RETIRED'

    db.expire_all()
    stored = db.get(Staff, courier_row['id'])
    assert stored.account_id == courier_account.id
    assert stored.password_hash == ''
    assert stored.must_change_password is False


def test_limited_admin_cannot_manage_superadmin_or_escalate(client, db):
    first = make_admin(db, name='主管理员')
    limited_account = make_account(db)
    courier_account = make_account(db)
    db.commit()
    first_client = new_client(client.app)
    assert login_staff(first_client, first.username).status_code == 200

    self_disable = api(first_client, f'/admin/staff/{first.id}', {
        'enabled': False, 'reason': '尝试停用自己', 'expectedVersion': first.version,
    }, method='PATCH')
    assert self_disable.status_code in (403, 409)
    assert self_disable.json()['error']['code'] in ('SELF_ACCOUNT_PROTECTION', 'LAST_ADMIN_PROTECTED')

    limited = api(first_client, '/admin/staff', {
        'accountId': limited_account.id, 'role': 'ADMIN',
        'capabilities': ['staff.manage'], 'reason': '细分员工管理',
    })
    assert limited.status_code == 200, limited.text
    limited_row = next(row for row in limited.json()['data']['staff']
                       if row['accountId'] == limited_account.id)
    limited_client = new_client(client.app)
    assert login_staff(limited_client, limited_account.email).status_code == 200

    rows = api(limited_client, '/admin/staff', method='GET').json()['data']['staff']
    first_row = next(row for row in rows if row['id'] == first.id)
    demote_super = api(limited_client, f'/admin/staff/{first.id}', {
        'role': 'COURIER', 'reason': '尝试降级最后超级管理员',
        'expectedVersion': first_row['version'],
    }, method='PATCH')
    assert demote_super.status_code == 403
    assert demote_super.json()['error']['code'] == 'SUPER_ADMIN_PROTECTED'

    self_escalation = api(limited_client, f"/admin/staff/{limited_row['id']}", {
        'capabilities': ['*'], 'reason': '尝试自我提权',
        'expectedVersion': limited_row['version'],
    }, method='PATCH')
    assert self_escalation.status_code == 403
    assert self_escalation.json()['error']['code'] == 'SELF_ACCOUNT_PROTECTION'

    grant_full = api(limited_client, '/admin/staff', {
        'accountId': courier_account.id, 'role': 'ADMIN',
        'capabilities': ['*'], 'reason': '尝试授予超出本人权限的能力',
    })
    assert grant_full.status_code == 403
    assert grant_full.json()['error']['code'] == 'CAPABILITY_ESCALATION'

    courier_management = api(first_client, '/admin/staff', {
        'accountId': courier_account.id, 'role': 'COURIER',
        'capabilities': ['*'], 'reason': '不应允许',
    })
    assert courier_management.status_code == 422
    assert courier_management.json()['error']['code'] == 'COURIER_CAPABILITY_DENIED'


def test_staff_setup_uses_the_customer_account_reset_flow(client, db, monkeypatch):
    import app.auth as auth

    admin = make_admin(db)
    target = make_account(db)
    db.commit()
    admin_client = new_client(client.app)
    assert login_staff(admin_client, admin.username).status_code == 200
    created = api(admin_client, '/admin/staff', {
        'accountId': target.id, 'role': 'ADMIN',
        'capabilities': ['staff.manage'], 'reason': '授予员工权限',
    })
    assert created.status_code == 200, created.text
    row = next(item for item in created.json()['data']['staff']
               if item['accountId'] == target.id)

    sent = []
    monkeypatch.setattr(auth, '_mail_available', lambda database: True)
    monkeypatch.setattr(auth, '_enqueue_email',
                        lambda database, key, to, variables, business_key: sent.append(
                            {'key': key, 'to': to, 'variables': variables,
                             'business_key': business_key}))
    invited = api(admin_client, f"/admin/staff/{row['id']}/send-setup",
                  {'reason': '发送账号重置', 'expectedVersion': row['version']})
    assert invited.status_code == 200, invited.text
    assert sent and sent[-1]['key'] == 'CUSTOMER_RESET_PASSWORD'
    assert sent[-1]['to'] == target.email
    raw = token_of(sent[-1]['variables']['actionUrl'])

    reset = api(new_client(client.app), '/customer/auth/reset-password',
                {'token': raw, 'newPassword': 'SetupStrong789!'})
    assert reset.status_code == 200, reset.text
    db.expire_all()
    stored = db.get(Staff, row['id'])
    assert stored.password_hash == ''
    assert stored.must_change_password is False
    login = login_customer(new_client(client.app), target.email, 'SetupStrong789!')
    assert login.status_code == 200, login.text
    browser = new_client(client.app)
    assert login_customer(browser, target.email, 'SetupStrong789!').status_code == 200
    assert api(browser, '/auth/me', method='GET').status_code == 200


def test_staff_forgot_password_is_retired_in_favor_of_customer_flow(client, db, monkeypatch):
    import app.auth as auth

    account = make_account(db)
    db.commit()
    sent = []
    monkeypatch.setattr(auth, '_mail_available', lambda database: True)
    monkeypatch.setattr(auth, '_enqueue_email',
                        lambda database, key, to, variables, business_key: sent.append(
                            {'key': key, 'to': to, 'business_key': business_key}))
    retired = api(new_client(client.app), '/auth/forgot-password', {'email': account.email})
    assert retired.status_code == 410
    assert retired.json()['error']['code'] == 'STAFF_AUTH_RETIRED'
    assert sent == []

    requested = api(new_client(client.app), '/customer/auth/forgot-password',
                    {'email': account.email})
    assert requested.status_code == 200
    assert len(sent) == 1 and sent[0]['key'] == 'CUSTOMER_RESET_PASSWORD'


# ---------------------------------------------------------------------------
# Profile, sessions, personal export and deletion application
# ---------------------------------------------------------------------------


def test_profile_versioning_and_all_other_session_revocation(client, db):
    account = make_account(db)
    db.commit()
    first = new_client(client.app)
    second = new_client(client.app)
    assert login_customer(first, account.email).status_code == 200
    assert login_customer(second, account.email).status_code == 200

    profile = api(first, '/customer/account', method='GET')
    assert profile.status_code == 200
    data = profile.json()['data']
    assert data['emailMasked'] and '***' in data['emailMasked']
    assert data['notificationPreferences'] == {
        'orderCreated': False, 'orderStatusUpdated': False, 'referralReward': False}

    updated = api(first, '/customer/account', {'displayName': '新昵称', 'expectedVersion': data['version']},
                  method='PATCH')
    assert updated.status_code == 200, updated.text
    assert updated.json()['data']['displayName'] == '新昵称'
    assert updated.json()['data']['version'] == data['version'] + 1
    stale = api(first, '/customer/account', {'displayName': '旧版本', 'expectedVersion': data['version']},
                method='PATCH')
    assert stale.status_code == 409
    assert stale.json()['error']['code'] == 'VERSION_CONFLICT'

    sessions = api(first, '/customer/account/sessions', method='GET').json()['data']['items']
    assert len(sessions) == 2
    revoked = api(first, '/customer/account/sessions/revoke', {'sessionId': 'ALL_OTHERS'})
    assert revoked.status_code == 200
    assert api(first, '/customer/account', method='GET').status_code == 200
    assert api(second, '/customer/account', method='GET').status_code in (401, 403)

    # A customer cannot revoke a session id that is not theirs.
    foreign = api(second, '/customer/account/sessions/revoke', {'sessionId': sessions[0]['id']})
    assert foreign.status_code in (401, 403)


def test_customer_nickname_is_the_staff_display_name(client, db):
    """Changing 我的资料 → 显示昵称 must update the employee's public name.

    FINAL N05 links every employee to one customer account.  The account
    nickname is the display name shown in 员工入口 / 管理端 / 配送端; Staff.name
    stays as the internal employee name for administration and payroll.
    """
    staff = make_admin(db, name='管理员')
    db.commit()
    login = login_staff(client, staff.username)
    assert login.status_code == 200, login.text
    assert login.json()['data']['name'] == '管理员'

    profile = api(client, '/customer/account', method='GET')
    assert profile.status_code == 200, profile.text
    updated = api(client, '/customer/account', {
        'displayName': 'Harry',
        'expectedVersion': profile.json()['data']['version'],
    }, method='PATCH')
    assert updated.status_code == 200, updated.text
    assert updated.json()['data']['displayName'] == 'Harry'

    me = api(client, '/auth/me', method='GET')
    assert me.status_code == 200, me.text
    assert me.json()['data']['name'] == 'Harry'

    db.expire_all()
    assert db.get(Staff, staff.id).name == '管理员'


def test_personal_export_request_and_deletion_application_do_not_execute_deletion(client, db):
    account = make_account(db)
    db.commit()
    client = new_client(client.app)
    assert login_customer(client, account.email).status_code == 200

    export = api(client, '/customer/account/export-request', {'reason': '个人资料导出'})
    assert export.status_code == 200, export.text
    export_data = export.json()['data']
    assert export_data['status'] == 'QUEUED'
    assert export_data['expiresAt']
    db.expire_all()
    job = db.get(ExportJob, export_data['exportJobId'])
    assert job is not None and job.account_id == account.id and job.kind == 'PERSONAL_DATA'

    order = make_order(db, account.principal_id)
    db.commit()
    deletion = api(client, '/customer/account/deletion-request', {'reason': '不再使用'})
    assert deletion.status_code == 200, deletion.text
    deletion_data = deletion.json()['data']
    assert deletion_data['status'] == 'BLOCKED'
    assert any(order.id in blocker for blocker in deletion_data['blockers'])
    db.expire_all()
    request = db.get(DeletionRequest, deletion_data['deletionRequestId'])
    assert request is not None and request.account_id == account.id
    assert request.status == 'BLOCKED'
    assert db.get(Account, account.id).deleted_at is None
    assert db.get(Order, order.id) is not None


def test_create_initial_admin_binds_verified_account_and_requires_email(db):
    import inspect

    from app.auth import create_initial_admin, verify_password
    from app.core import DomainError

    email_param = inspect.signature(create_initial_admin).parameters['email']
    assert email_param.default is None
    assert email_param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    with pytest.raises(DomainError) as missing:
        create_initial_admin(db, f'root-{uuid4().hex[:8]}', PASSWORD, '主管理员')
    assert missing.value.code == 'INITIAL_ADMIN_EMAIL_REQUIRED'

    account = make_account(db)
    db.commit()
    staff = create_initial_admin(db, f'root-{uuid4().hex[:8]}', PASSWORD, '主管理员',
                                 account.email)
    db.flush()
    assert staff.role == 'ADMIN' and staff.enabled and staff.status == 'ACTIVE'
    assert staff.is_super_admin is True
    assert staff.account_id == account.id
    assert staff.must_change_password is False
    assert '*' in staff.capabilities
    assert staff.password_hash == ''
    assert verify_password(account.password_hash, PASSWORD)
    assert not verify_password(account.password_hash, 'WrongPass123!')
    with pytest.raises(DomainError) as duplicate:
        create_initial_admin(db, f'root-{uuid4().hex[:8]}', PASSWORD, '主管理员',
                             email=account.email)
    assert duplicate.value.code == 'SUPER_ADMIN_ALREADY_INITIALIZED'

    legacy_account = make_account(db)
    db.commit()
    with pytest.raises(DomainError) as initialized:
        create_initial_admin(
            db, f'root-{uuid4().hex[:8]}', PASSWORD, '兼容管理员', legacy_account.email)
    assert initialized.value.code == 'SUPER_ADMIN_ALREADY_INITIALIZED'


def test_initial_admin_bootstrap_replaces_only_disabled_superadmin(db):
    from app.auth import create_initial_admin

    disabled = make_admin(db, username='restore-former-root')
    disabled.enabled = False
    disabled.status = 'DISABLED'
    db.flush()
    new_account = make_account(db)
    db.commit()

    staff = create_initial_admin(
        db, 'restore-new-root', PASSWORD, '恢复管理员', email=new_account.email)
    db.flush()
    assert staff.is_super_admin is True
    assert staff.enabled is True and staff.status == 'ACTIVE'
    db.refresh(disabled)
    assert disabled.is_super_admin is False
    assert disabled.enabled is False and disabled.status == 'DISABLED'
