"""Single guest link lifecycle and per-order ownership against PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models import AccessGrant, AuthSession, Order, Principal
from auth_helpers import api, latest_session, login_customer, make_order, new_client

APP_MODULES = ['auth', 'commerce']


def guest_order(db, app):
    client = new_client(app)
    principal_id = latest_session(db, 'GUEST').principal_id
    order = make_order(db, principal_id)
    db.commit()
    return client, order


def reset(admin, order, version=1, **kwargs):
    return api(admin, f'/admin/orders/{order.id}/guest-access/reset', {
        'expectedVersion': version, 'reason': '人工核对后更新', **kwargs})


def open_token(admin, order):
    response = api(admin, f'/admin/orders/{order.id}/guest-access/open', method='GET')
    assert response.status_code == 200, response.text
    return response.json()['data']['accessToken']


def exchange(client, order, token, **kwargs):
    return api(client, '/order-access/exchange', {'orderId': order.id, 'accessToken': token}, **kwargs)


def test_guest_link_reset_close_and_stale_session_status(db, app, admin_client):
    guest, order = guest_order(db, app)
    initial = api(admin_client, f'/admin/orders/{order.id}/guest-access', method='GET')
    assert initial.status_code == 200, initial.text
    assert initial.json()['data']['status'] == 'NO_LINK'
    active = reset(admin_client, order)
    assert active.status_code == 200, active.text
    view = active.json()['data']
    assert view['status'] == 'ACTIVE'
    assert view['version'] == 2
    assert 'accessToken' not in view
    assert datetime.fromisoformat(view['expiresAt']) - datetime.fromisoformat(view['openedAt']) == timedelta(days=14)
    first_token = open_token(admin_client, order)
    assert open_token(admin_client, order) == first_token
    forwarded = new_client(app)
    result = exchange(forwarded, order, first_token)
    assert result.status_code == 200, result.text
    assert set(result.json()['data']['access']['scopes']) == {'ORDER_VIEW', 'FINAL_PDF_PREVIEW', 'DELIVERY_PROOF_VIEW'}
    assert api(forwarded, f'/customer/orders/{order.id}/guest-access', method='GET').json()['data']['canClaimOnDevice'] is False
    closed = api(admin_client, f'/admin/orders/{order.id}/guest-access/close', {
        'expectedVersion': 2, 'reason': '核实后关闭'})
    assert closed.status_code == 200, closed.text
    assert closed.json()['data']['status'] == 'CLOSED'
    assert exchange(forwarded, order, first_token).json()['error']['code'] == 'ORDER_GRANT_REVOKED'
    stale = api(forwarded, f'/customer/orders/{order.id}/guest-access', method='GET')
    assert stale.status_code == 200
    assert stale.json()['data']['status'] == 'CLOSED'
    assert 'order' not in stale.json()['data']
    assert reset(admin_client, order, version=2).status_code == 409
    assert reset(admin_client, order, version=3, days=1).status_code == 200
    assert open_token(admin_client, order) != first_token
    assert exchange(forwarded, order, first_token).status_code == 410
    assert api(guest, f'/customer/orders/{order.id}/guest-access', method='GET').json()['data']['status'] == 'ACTIVE'


@pytest.mark.parametrize('days', [None, True, False, '1', 1.0, 0, 15])
def test_reset_days_are_strict(db, app, admin_client, days):
    _, order = guest_order(db, app)
    assert reset(admin_client, order, days=days).status_code == 422


def test_exact_guest_link_expiry(db, app, admin_client, monkeypatch):
    import app.auth as auth
    import app.core as core
    instant = datetime(2026, 11, 17, tzinfo=timezone.utc)
    monkeypatch.setattr(auth, '_now', lambda: instant)
    monkeypatch.setattr(core, 'utcnow', lambda: instant)
    guest, order = guest_order(db, app)
    assert reset(admin_client, order).status_code == 200
    token = open_token(admin_client, order)
    instant += timedelta(days=14, microseconds=-1)
    assert exchange(guest, order, token).status_code == 200
    instant += timedelta(microseconds=1)
    expired = exchange(guest, order, token)
    assert expired.status_code == 410, expired.text
    assert expired.json()['error']['code'] == 'ORDER_GRANT_EXPIRED'
    view = api(guest, f'/customer/orders/{order.id}/guest-access', method='GET').json()['data']
    assert view['status'] == 'EXPIRED'
    assert view['canClaimOnDevice'] is False


def test_claim_keeps_other_guest_order_and_checkout_snapshot(db, app, admin_client, account_factory):
    guest, order = guest_order(db, app)
    other = make_order(db, order.principal_id)
    db.commit()
    assert reset(admin_client, order).status_code == 200
    assert reset(admin_client, other).status_code == 200
    first_token = open_token(admin_client, order)
    account = account_factory()
    assert login_customer(guest, account.email, 'Testing-only-Password-42!').status_code == 200
    view = api(guest, f'/customer/orders/{order.id}/guest-access', method='GET').json()['data']
    assert view['canClaimOnDevice'] is True
    claimed = api(guest, f'/customer/orders/{order.id}/claim', {'confirm': True})
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()['data'] == {'orderId': order.id, 'ownerAccountId': account.id}
    assert api(guest, f'/customer/orders/{order.id}/claim', {'confirm': True}).status_code == 200
    db.expire_all()
    assert db.get(Order, order.id).owner_account_id == account.id
    assert db.get(Order, order.id).checkout_account_id is None
    assert db.get(Order, other.id).owner_account_id is None
    assert db.get(Principal, order.principal_id).account_id is None
    assert api(guest, f'/customer/orders/{other.id}/guest-access', method='GET').json()['data']['status'] == 'ACTIVE'
    assert exchange(new_client(app), order, first_token).status_code == 410
    assert api(admin_client, f'/admin/orders/{order.id}/guest-access', method='GET').status_code == 409


def test_forwarded_link_cannot_claim_and_admin_can_bind_closed_order(db, app, admin_client, customer_client):
    _, order = guest_order(db, app)
    assert reset(admin_client, order).status_code == 200
    assert exchange(customer_client, order, open_token(admin_client, order)).status_code == 200
    assert api(customer_client, f'/customer/orders/{order.id}/claim', {'confirm': True}).status_code == 404
    assert api(admin_client, f'/admin/orders/{order.id}/guest-access/close', {
        'expectedVersion': 2, 'reason': '关闭'}).status_code == 200
    result = api(admin_client, f'/admin/orders/{order.id}/guest-owner', {
        'accountId': customer_client.account.id, 'expectedVersion': 3, 'reason': '已人工核实'})
    assert result.status_code == 200, result.text
    assert result.json()['data']['ownerAccountId'] == customer_client.account.id


def test_parallel_reset_has_one_winner_and_one_unrevoked_root(db, app, admin_client):
    _, order = guest_order(db, app)
    cookies = dict(admin_client.cookies)
    def attempt():
        from fastapi.testclient import TestClient
        with TestClient(app) as browser:
            browser.cookies.update(cookies)
            return reset(browser, order).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(results) == [200, 409]
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(AccessGrant).where(
        AccessGrant.order_id == order.id, AccessGrant.source_grant_id.is_(None),
        AccessGrant.token_hash.is_not(None), AccessGrant.revoked_at.is_(None))) == 1


def test_two_verified_accounts_cannot_claim_the_same_order(db, app, admin_client, account_factory):
    from threading import Barrier
    from fastapi.testclient import TestClient

    guest, order = guest_order(db, app)
    assert reset(admin_client, order).status_code == 200
    contenders = []
    for _ in range(2):
        account = account_factory()
        browser = TestClient(app)
        browser.cookies.update(dict(guest.cookies))
        assert login_customer(browser, account.email, 'Testing-only-Password-42!').status_code == 200
        contenders.append((browser, account.id))
    ready = Barrier(2)

    def attempt(contender):
        browser, account_id = contender
        ready.wait(timeout=10)
        response = api(browser, f'/customer/orders/{order.id}/claim', {'confirm': True})
        return account_id, response.status_code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, contenders))
    finally:
        for browser, _ in contenders:
            browser.close()
    assert sorted(status for _, status in results) == [200, 404]
    winner = next(account_id for account_id, status in results if status == 200)
    db.expire_all()
    assert db.get(Order, order.id).owner_account_id == winner
    assert db.get(Order, order.id).checkout_account_id is None
    assert db.get(Order, order.id).guest_access_version == 3


def test_claim_and_account_deletion_recheck_each_other(db, app, admin_client, account_factory):
    from threading import Barrier
    from fastapi.testclient import TestClient
    from app.models import Account

    guest, order = guest_order(db, app)
    assert reset(admin_client, order).status_code == 200
    account = account_factory()
    assert login_customer(guest, account.email, 'Testing-only-Password-42!').status_code == 200
    ready = Barrier(2)
    cookies = dict(guest.cookies)

    def attempt(action):
        with TestClient(app) as browser:
            browser.cookies.update(cookies)
            ready.wait(timeout=10)
            if action == 'claim':
                return api(browser, f'/customer/orders/{order.id}/claim', {'confirm': True})
            return api(browser, '/customer/account/deletion-request', {'reason': '并发注销核验'})

    with ThreadPoolExecutor(max_workers=2) as pool:
        claim, deletion = list(pool.map(attempt, ['claim', 'delete']))
    db.expire_all()
    current_account = db.get(Account, account.id)
    current_order = db.get(Order, order.id)
    if claim.status_code == 200:
        assert current_order.owner_account_id == account.id
        assert current_account.deleted_at is None
        assert deletion.status_code == 200, deletion.text
        assert deletion.json()['data']['status'] == 'BLOCKED'
        assert any(order.id in blocker for blocker in deletion.json()['data']['blockers'])
    else:
        assert claim.status_code in (401, 403, 409), claim.text
        assert deletion.status_code == 200, deletion.text
        assert deletion.json()['data']['status'] == 'RECOVERY'
        assert current_order.owner_account_id is None
        assert current_account.deleted_at is not None


def test_old_routes_are_removed(db, app, admin_client):
    _, order = guest_order(db, app)
    for path in ['/customer/account/link-guest', '/customer/account/import-order',
                 f'/admin/orders/{order.id}/access-links', f'/admin/orders/{order.id}/access-links/reissue']:
        assert api(admin_client, path, {'confirm': True}).status_code == 404


def test_legacy_hash_requires_reset_and_unrelated_device_has_no_status(db, app, admin_client):
    from app.core import token_hash
    from app.models import utcnow
    _, order = guest_order(db, app)
    legacy = AccessGrant(order_id=order.id, token_hash=token_hash('legacy-token'),
                         scopes=['ORDER_VIEW'], expires_at=utcnow() + timedelta(days=1))
    db.add(legacy)
    db.commit()
    view = api(admin_client, f'/admin/orders/{order.id}/guest-access', method='GET').json()['data']
    assert view['status'] == 'ACTIVE'
    assert view['canOpen'] is False
    response = api(admin_client, f'/admin/orders/{order.id}/guest-access/open', method='GET')
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'GUEST_LINK_RESET_REQUIRED'
    assert api(new_client(app), f'/customer/orders/{order.id}/guest-access', method='GET').status_code == 404
    assert reset(admin_client, order).status_code == 200
    assert open_token(admin_client, order)


@pytest.mark.parametrize('payload', [
    {'reason': '核对'}, {'expectedVersion': 1}, {'expectedVersion': 1, 'reason': '  '},
    {'expectedVersion': True, 'reason': '核对'},
])
def test_guest_access_mutation_requires_version_and_reason(db, app, admin_client, payload):
    _, order = guest_order(db, app)
    assert api(admin_client, f'/admin/orders/{order.id}/guest-access/reset', payload).status_code == 422


def test_link_admin_and_owner_binding_require_capabilities(db, app, customer_client, staff_factory):
    _, order = guest_order(db, app)
    staff = staff_factory(account_id=customer_client.account.id, capabilities=['order.access-link.manage'])
    assert reset(customer_client, order).status_code == 200
    assert api(customer_client, f'/admin/orders/{order.id}/guest-owner', {
        'accountId': customer_client.account.id, 'expectedVersion': 2, 'reason': '核对'}).status_code == 403
    staff.capabilities = ['customer.manage']
    db.commit()
    assert api(customer_client, f'/admin/orders/{order.id}/guest-access', method='GET').status_code == 403


def test_claim_requires_boolean_confirmation(db, app, admin_client, account_factory):
    guest, order = guest_order(db, app)
    assert reset(admin_client, order).status_code == 200
    account = account_factory()
    assert login_customer(guest, account.email, 'Testing-only-Password-42!').status_code == 200
    assert api(guest, f'/customer/orders/{order.id}/claim', {'confirm': 1}).status_code == 422


def test_reset_revokes_derived_grants_and_only_their_sessions(db, app, admin_client):
    _, order = guest_order(db, app)
    assert reset(admin_client, order).status_code == 200
    browser = new_client(app)
    assert exchange(browser, order, open_token(admin_client, order)).status_code == 200
    db.expire_all()
    root = db.scalar(select(AccessGrant).where(AccessGrant.order_id == order.id))
    derived = AccessGrant(order_id=order.id, source_grant_id=root.id, scopes=['ORDER_VIEW'], expires_at=root.expires_at)
    db.add(derived)
    db.flush()
    from app.core import token_hash
    db.add(AuthSession(kind='ORDER_ACCESS', token_hash=token_hash('derived-session'),
                       grant_id=derived.id, expires_at=root.expires_at))
    _, other = guest_order(db, app)
    assert reset(admin_client, other).status_code == 200
    other_browser = new_client(app)
    assert exchange(other_browser, other, open_token(admin_client, other)).status_code == 200
    assert reset(admin_client, order, version=2).status_code == 200
    db.expire_all()
    assert db.get(AccessGrant, derived.id).revoked_at is not None
    assert db.scalar(select(AuthSession).where(AuthSession.grant_id == derived.id)).revoked_at is not None
    other_state = api(other_browser, f'/customer/orders/{other.id}/guest-access', method='GET')
    assert other_state.json()['data']['status'] == 'ACTIVE'


@pytest.mark.parametrize('change', ['close', 'reset', 'claim', 'expire'])
def test_exchange_replay_never_restores_invalid_private_response(db, app, admin_client, account_factory, change):
    guest, order = guest_order(db, app)
    assert reset(admin_client, order).status_code == 200
    token = open_token(admin_client, order)
    forwarded = new_client(app)
    key = 'guest-exchange-replay'
    first = exchange(forwarded, order, token, key=key)
    assert first.status_code == 200, first.text
    repeated = exchange(forwarded, order, token, key=key)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()['data']['access'] == first.json()['data']['access']
    if change == 'claim':
        account = account_factory()
        assert login_customer(guest, account.email, 'Testing-only-Password-42!').status_code == 200
        assert api(guest, f'/customer/orders/{order.id}/claim', {'confirm': True}).status_code == 200
    elif change == 'expire':
        from app.models import utcnow
        root = db.scalar(select(AccessGrant).where(AccessGrant.order_id == order.id))
        root.expires_at = utcnow() - timedelta(microseconds=1)
        db.commit()
    else:
        response = api(admin_client, f'/admin/orders/{order.id}/guest-access/{change}', {
            'expectedVersion': 2, 'reason': '核验后停止旧入口'})
        assert response.status_code == 200, response.text
    stale = exchange(forwarded, order, token, key=key)
    assert stale.status_code == 410, stale.text
    assert 'data' not in stale.json()
    assert 'dormprint_order_access_session=' not in stale.headers.get('set-cookie', '')


def test_order_write_replay_rejects_prior_access_version_after_reset(db, app, admin_client):
    from test_order_void import seed_quote
    guest, order = guest_order(db, app)
    seed_quote(db, order)
    assert reset(admin_client, order).status_code == 200
    body = {'expectedVersion': order.version, 'reason': '客户明确撤销'}
    key = 'cancel-before-access-reset'
    first = api(guest, f'/orders/{order.id}/cancel', body, key=key)
    assert first.status_code == 200, first.text
    assert api(guest, f'/orders/{order.id}/cancel', body, key=key).status_code == 200
    assert reset(admin_client, order, version=2).status_code == 200
    stale = api(guest, f'/orders/{order.id}/cancel', body, key=key)
    assert stale.status_code == 409, stale.text
    assert 'data' not in stale.json()


def test_link_view_does_not_add_order_to_account_list(db, app, admin_client, customer_client):
    from test_order_void import seed_quote

    original, order = guest_order(db, app)
    seed_quote(db, order)
    # A separate, account-owned order still belongs in the customer's list.
    owned = make_order(db, customer_client.account.principal_id)
    owned.owner_account_id = customer_client.account.id
    seed_quote(db, owned)
    assert reset(admin_client, order).status_code == 200
    grant_count = db.scalar(select(func.count()).select_from(AccessGrant))
    assert exchange(customer_client, order, open_token(admin_client, order)).status_code == 200
    detail = customer_client.get(f'/api/v1/orders/{order.id}')
    assert detail.status_code == 200, detail.text
    assert detail.json()['data']['accessKind'] == 'ORDER_GRANT'
    listing = customer_client.get('/api/v1/customer/orders')
    assert [item['id'] for item in listing.json()['data']['items']] == [owned.id]
    listing = original.get('/api/v1/customer/orders')
    assert [item['id'] for item in listing.json()['data']['items']] == [order.id]
    assert db.scalar(select(func.count()).select_from(AccessGrant)) == grant_count
