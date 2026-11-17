"""Private media follows the current order owner and guest link lifecycle."""
import hashlib
import secrets
from datetime import timedelta

import pytest
from app.models import Artifact, Asset, AuthSession, OrderFile, Preparation, PrintTask, UploadSession, utcnow
from auth_helpers import api, latest_session, make_order, new_client

APP_MODULES = ['auth', 'media', 'supplementary']


def make_asset(db, principal_id, purpose, *, data=None):
    from app.media import _storage_path
    payload = b'%PDF-1.7\nprivate-order-media\n'
    key = 'assets/guest-resource-' + secrets.token_hex(8) + '.pdf'
    path = _storage_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    asset = Asset(principal_id=principal_id, purpose=purpose, name='private.pdf',
                  mime='application/pdf', size=len(payload), sha256=hashlib.sha256(payload).hexdigest(),
                  storage_key=key, status='READY', data=data or {})
    db.add(asset)
    db.flush()
    return asset


@pytest.fixture
def resource_order(db, app):
    from app.auth import issue_order_grant
    from app.core import Actor
    guest = new_client(app)
    principal = latest_session(db, 'GUEST').principal_id
    order = make_order(db, principal)
    grant, token = issue_order_grant(db, order, Actor(principal_id=principal))
    source = make_asset(db, principal, 'PRINT_SOURCE', data={'kind': 'DOCUMENT'})
    normalized = make_asset(db, principal, 'PRINT_NORMALIZED', data={'normalizedFrom': source.id})
    final = make_asset(db, principal, 'PRINT_FINAL')
    draft = make_asset(db, principal, 'PRINT_SOURCE', data={'kind': 'DOCUMENT'})
    for asset in (source, draft):
        db.add(UploadSession(principal_id=principal, purpose=asset.purpose, name=asset.name,
                             total_bytes=asset.size, sha256=asset.sha256, status='COMPLETED',
                             asset_id=asset.id, expires_at=utcnow() + timedelta(days=1)))
    file = OrderFile(order_id=order.id, revision=1, client_file_id='source',
                     asset_id=source.id, kind='DOCUMENT')
    db.add(file)
    db.flush()
    artifact = Artifact(preparation_id=order.preparation_id, asset_id=final.id,
                        client_file_id='source', sha256=final.sha256, pages=1, sheets=1)
    db.add(artifact)
    db.flush()
    db.add(PrintTask(order_id=order.id, file_id=file.id, artifact_id=artifact.id))
    db.commit()
    forwarded = new_client(app)
    response = api(forwarded, '/order-access/exchange', {'orderId': order.id, 'accessToken': token})
    assert response.status_code == 200, response.text
    return guest, forwarded, order, grant, [source, normalized, final], draft


@pytest.mark.parametrize('stop', ['close', 'reset', 'expire'])
def test_stopped_guest_entry_blocks_source_and_final_bytes(db, resource_order, admin_client, stop):
    guest, forwarded, order, grant, assets, draft = resource_order
    for asset in assets:
        content = api(guest, f'/assets/{asset.id}/content', method='GET')
        assert content.status_code == 200
        assert content.content == b'%PDF-1.7\nprivate-order-media\n'
    assert api(forwarded, f'/assets/{assets[0].id}/content', method='GET').status_code == 404
    assert api(forwarded, f'/assets/{assets[-1].id}/content', method='GET').status_code == 200
    if stop == 'expire':
        grant.expires_at = utcnow() - timedelta(microseconds=1)
        db.commit()
    else:
        response = api(admin_client, f'/admin/orders/{order.id}/guest-access/{stop}', {
            'expectedVersion': 1, 'reason': '核实后结束旧访问'})
        assert response.status_code == 200, response.text
    for browser in [guest, forwarded]:
        for asset in assets:
            expected = 200 if stop == 'reset' and browser is guest else 404
            assert api(browser, f'/assets/{asset.id}/content', method='GET').status_code == expected
            assert api(browser, f'/assets/{asset.id}', method='GET').status_code == expected
    assert api(guest, f'/assets/{draft.id}/content', method='GET').status_code == 200
    for asset in assets:
        listed = api(guest, f'/assets?purpose={asset.purpose}', method='GET')
        assert listed.status_code == 200, listed.text
        ids = {item['assetId'] for item in listed.json()['data']['items']}
        assert (asset.id in ids) is (stop == 'reset')
    drafts = api(guest, '/assets', method='GET').json()['data']['items']
    assert draft.id in {item['assetId'] for item in drafts}
    from sqlalchemy import select
    for asset in (assets[0], draft):
        upload = db.scalar(select(UploadSession).where(UploadSession.asset_id == asset.id))
        expected = 200 if asset is draft or stop == 'reset' else 404
        assert api(guest, f'/upload-sessions/{upload.id}', method='GET').status_code == expected
        assert api(guest, f'/upload-sessions/{upload.id}/complete', {}).status_code == expected


def test_claim_moves_private_media_to_account_and_preserves_other_drafts(
        db, resource_order, customer_client, client_factory):
    from app.core import token_hash
    guest, forwarded, order, _, assets, draft = resource_order
    account = customer_client.account
    raw = secrets.token_urlsafe(32)
    db.add(AuthSession(kind='CUSTOMER', token_hash=token_hash(raw), account_id=account.id,
                       principal_id=account.principal_id, session_version=account.session_version,
                       expires_at=utcnow() + timedelta(days=1)))
    db.commit()
    old_guest = client_factory()
    old_guest.cookies.clear()
    old_guest.cookies.update(dict(guest.cookies))
    guest.cookies.set('dormprint_account_session', raw)
    claimed = api(guest, f'/customer/orders/{order.id}/claim', {'confirm': True})
    assert claimed.status_code == 200, claimed.text
    for asset in assets:
        assert api(customer_client, f'/assets/{asset.id}/content', method='GET').status_code == 200
        assert api(old_guest, f'/assets/{asset.id}/content', method='GET').status_code == 404
        assert api(forwarded, f'/assets/{asset.id}/content', method='GET').status_code == 404
    assert api(old_guest, f'/assets/{draft.id}/content', method='GET').status_code == 200
    drafts = api(old_guest, '/assets', method='GET').json()['data']['items']
    assert {item['assetId'] for item in drafts} == {draft.id}
    from sqlalchemy import select
    upload = db.scalar(select(UploadSession).where(UploadSession.asset_id == assets[0].id))
    assert api(old_guest, f'/upload-sessions/{upload.id}', method='GET').status_code == 404
    assert api(old_guest, f'/upload-sessions/{upload.id}/complete', {}).status_code == 404


@pytest.mark.parametrize('replacement', [False, True])
def test_old_preparation_final_without_current_print_task_obeys_order_expiry(db, resource_order, replacement):
    guest, _, order, grant, _, _ = resource_order
    historical_final = make_asset(db, order.principal_id, 'PRINT_FINAL')
    preparation_id = order.preparation_id
    if replacement:
        preparation = Preparation(principal_id=order.principal_id, replaces_order_id=order.id,
                                  expires_at=utcnow() + timedelta(days=1))
        db.add(preparation)
        db.flush()
        preparation_id = preparation.id
    db.add(Artifact(preparation_id=preparation_id, asset_id=historical_final.id,
                    client_file_id='old-final', sha256=historical_final.sha256, pages=1, sheets=1))
    grant.expires_at = utcnow() - timedelta(microseconds=1)
    db.commit()
    assert api(guest, f'/assets/{historical_final.id}/content', method='GET').status_code == 404


def test_dispute_rechecks_guest_access_after_acquiring_order_lock(
        db, resource_order, admin_client, app, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from app import supplementary
    from app.models import Order, SupplementaryDispute

    guest, _, order, _, _, _ = resource_order
    order.status = 'DELIVERED'
    order.settlement_kind = 'CASH_MANUAL'
    order.cash_received_cents = order.total_cents
    db.commit()
    created = api(admin_client, f'/admin/orders/{order.id}/supplementary-charges', {
        'purpose': '装订费用', 'amountCents': 100, 'includeInPayroll': False,
        'expectedVersion': order.version,
    })
    assert created.status_code == 200, created.text
    charge = created.json()['data']
    original_lock = supplementary.lock
    interleaved = False

    def close_before_order_lock(session, model, target_id, *args, **kwargs):
        nonlocal interleaved
        if model is Order and target_id == order.id and not interleaved:
            interleaved = True
            with TestClient(app) as other_admin:
                other_admin.cookies.update(dict(admin_client.cookies))
                closed = api(other_admin, f'/admin/orders/{order.id}/guest-access/close', {
                    'expectedVersion': 1, 'reason': '核验后关闭访问',
                })
                assert closed.status_code == 200, closed.text
        return original_lock(session, model, target_id, *args, **kwargs)

    monkeypatch.setattr(supplementary, 'lock', close_before_order_lock)
    response = api(guest, f"/orders/{order.id}/supplementary-charges/{charge['id']}/disputes", {
        'reason': '关闭入口后的旧请求', 'expectedVersion': charge['version'],
    })
    assert interleaved
    assert response.status_code == 404, response.text
    assert db.scalar(select(SupplementaryDispute.id).where(
        SupplementaryDispute.charge_id == charge['id'])) is None
