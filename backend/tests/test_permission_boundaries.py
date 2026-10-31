"""Fine-grained capability boundaries for delivery, media and maintenance.

These are real HTTP + real PostgreSQL integration tests.  Each test uses the
isolated schema from ``tests/conftest.py`` and deliberately creates a limited
ADMIN (for example ``mail.manage`` only) instead of the full ``'*'`` admin.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.models import (
    AccessGrant,
    Account,
    Asset,
    AuthSession,
    DeliveryAssignment,
    DeliveryBatch,
    DeliveryProof,
    DeletionRequest,
    Order,
    OrderFile,
    Principal,
    Quote,
    Staff,
    SupplementaryCharge,
    SupplementaryDispute,
    TrashEntry,
    AnonymousLedger,
    WageEntry,
    WageSettlement,
    WageSettlementItem,
    utcnow,
)
from app.core import token_hash
from tests.auth_helpers import make_order

APP_MODULES = [
    'auth',
    'delivery',
    'production',
    'media',
    'maintenance',
    'commerce',
    'exports',
    'growth',
    'mail',
]

SHANGHAI = ZoneInfo('Asia/Shanghai')
PASSWORD_HASH = None


def _password_hash() -> str:
    global PASSWORD_HASH
    if PASSWORD_HASH is None:
        from argon2 import PasswordHasher

        PASSWORD_HASH = PasswordHasher().hash('Testing-only-Password-42!')
    return PASSWORD_HASH


def _staff_account(db, staff):
    """FINAL N05: staff privilege is derived from the linked customer account."""
    if staff.account_id:
        return db.get(Account, staff.account_id)
    principal = Principal()
    db.add(principal)
    db.flush()
    account = Account(
        principal_id=principal.id,
        email='staff_' + secrets.token_hex(8) + '@example.test',
        password_hash=_password_hash(),
        status='ACTIVE',
        verified_at=utcnow(),
        invite_code=secrets.token_hex(16),
    )
    db.add(account)
    db.flush()
    principal.account_id = account.id
    staff.account_id = account.id
    db.flush()
    return account


def _account_client(client_factory, db, account):
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(kind='CUSTOMER', account_id=account.id,
                       principal_id=account.principal_id,
                       session_version=account.session_version,
                       token_hash=token_hash(token),
                       expires_at=utcnow() + timedelta(days=7)))
    db.commit()
    client = client_factory()
    client.cookies.set('dormprint_account_session', token)
    client.account = account
    return client


def staff_session(client_factory, db, capabilities, *, must_change_password=False,
                  role='ADMIN', name='权限测试员工'):
    """Create an active staff account and a separate authenticated HTTP client."""
    staff = Staff(
        username='staff_' + secrets.token_hex(6),
        name=name,
        role=role,
        password_hash=_password_hash(),
        enabled=True,
        status='ACTIVE',
        must_change_password=must_change_password,
        capabilities=list(capabilities),
    )
    db.add(staff)
    db.flush()
    account = _staff_account(db, staff)
    db.commit()
    client = _account_client(client_factory, db, account)
    client.staff = staff
    return client


def staff_client_for(client_factory, db, staff):
    account = _staff_account(db, staff)
    db.commit()
    client = _account_client(client_factory, db, account)
    client.staff = staff
    return client


def customer_session(client_factory, db, account):
    client = _account_client(client_factory, db, account)
    client.account = account
    return client


def storage_root() -> Path:
    from app.settings import get_settings

    root = Path(get_settings().storage_root).expanduser()
    return root if root.is_absolute() else (Path.cwd() / root).resolve()


@pytest.fixture
def account_factory(db):
    """Local account fixture with a 32-char invite code (shared fixture bug)."""
    from argon2 import PasswordHasher

    from app.models import Account, Principal

    def factory(email=None, verified=True, display_name='权限测试同学', **kwargs):
        invite_code = kwargs.pop('invite_code', None) or secrets.token_hex(16)
        principal = Principal()
        db.add(principal)
        db.flush()
        account = Account(
            principal_id=principal.id,
            email=email or secrets.token_hex(6) + '@example.test',
            password_hash=PasswordHasher().hash('Testing-only-Password-42!'),
            status='ACTIVE' if verified else 'PENDING_EMAIL',
            verified_at=utcnow() if verified else None,
            display_name=display_name,
            invite_code=invite_code,
            **kwargs,
        )
        db.add(account)
        db.flush()
        principal.account_id = account.id
        db.commit()
        return account

    return factory


def make_staff(db, account=None, *, is_super_admin=False, role='ADMIN',
               capabilities=None, name='账号员工'):
    """Create a Staff row, optionally linked to a base customer account."""
    staff = Staff(
        account_id=account.id if account is not None else None,
        username='staff_' + secrets.token_hex(6),
        name=name,
        role=role,
        password_hash=_password_hash(),
        enabled=True,
        status='ACTIVE',
        must_change_password=False,
        capabilities=(['*'] if role == 'ADMIN' else []) if capabilities is None
        else list(capabilities),
        is_super_admin=is_super_admin,
    )
    db.add(staff)
    db.commit()
    return staff


def make_asset(db, purpose, *, principal_id=None, staff_id=None, assignment_id=None,
               status='READY', name='private.pdf', payload=b'private-file-bytes'):
    key = f'test/{secrets.token_hex(8)}.bin'
    path = storage_root() / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    asset = Asset(
        principal_id=principal_id,
        staff_id=staff_id,
        assignment_id=assignment_id,
        purpose=purpose,
        name=name,
        mime='application/pdf',
        size=len(payload),
        sha256=secrets.token_hex(32),
        storage_key=key,
        status=status,
        data={},
    )
    db.add(asset)
    db.commit()
    return asset


def multipart_headers(client):
    return {
        'Origin': 'http://testserver',
        'Idempotency-Key': secrets.token_urlsafe(24),
        'X-CSRF-Token': unquote(client.cookies.get('csrfToken', '')),
    }


# ---------------------------------------------------------------------------
# Delivery scheduling / execution
# ---------------------------------------------------------------------------


def test_standalone_staff_cookie_grants_no_staff_authority(
    api, client_factory, db, staff_factory
):
    """FINAL N05: the removed legacy STAFF cookie cannot authenticate alone."""
    staff = staff_factory(role='ADMIN', capabilities=['data.manage'])
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(kind='STAFF', staff_id=staff.id,
                       token_hash=token_hash(token),
                       expires_at=utcnow() + timedelta(days=7)))
    db.commit()
    client = client_factory()
    client.cookies.set('dormprint_staff_session', token)
    response = api(client, '/admin/trash', method='GET')
    assert response.status_code == 401, response.text
    assert response.json()['error']['code'] == 'AUTH_REQUIRED'


def test_capability_hash_prevents_cached_replay_after_revocation(
    api, client_factory, db
):
    client = staff_session(client_factory, db, ['delivery.manage'])
    starts_at = utcnow() - timedelta(hours=1)
    body = {
        'name': '幂等撤回测试',
        'startsAt': starts_at.isoformat(),
        'endsAt': (starts_at + timedelta(hours=2)).isoformat(),
        'reason': '',
    }
    key = 'capability-revocation-idempotency'
    first = api(client, '/admin/batches', body, key=key)
    assert first.status_code == 200, first.text
    client.staff.capabilities = ['mail.manage']
    db.commit()
    second = api(client, '/admin/batches', body, key=key)
    assert second.status_code == 403, second.text
    assert second.json()['error']['code'] == 'FORBIDDEN'


def test_limited_admin_cannot_schedule_deliveries_but_delivery_manage_can(
    api, client_factory, db, staff_factory
):
    mail_admin = staff_session(client_factory, db, ['mail.manage'])
    delivery_admin = staff_session(client_factory, db, ['delivery.manage'])
    courier = staff_factory(role='COURIER', capabilities=[])
    day = (datetime.now(SHANGHAI) + timedelta(days=3)).date().isoformat()
    body = {
        'name': '权限边界批次',
        'date': day,
        'time': '10:00-12:00',
        'courierId': courier.id,
        'reason': 'permission boundary test',
    }

    assert api(mail_admin, '/admin/batches', body).status_code == 403
    assert api(mail_admin, '/admin/batches', method='GET').status_code == 403
    assert api(mail_admin, '/admin/batch-operations/preview', {
        'action': 'DELETE', 'sourceBatchIds': ['missing'], 'versions': {},
    }).status_code == 403

    created = api(delivery_admin, '/admin/batches', body)
    assert created.status_code == 200, created.text
    batch = created.json()['data']
    assert api(delivery_admin, f"/admin/batches/{batch['id']}", method='GET').status_code == 200
    assert api(delivery_admin, '/admin/batches', method='GET').status_code == 200

    # A capability only for mail does not inherit scheduling or ad-hoc
    # administrative action on a concrete batch.
    assert api(mail_admin, f"/admin/batches/{batch['id']}", method='GET').status_code == 403
    assert api(mail_admin, f"/admin/batches/{batch['id']}", {
        'expectedVersion': batch['version'], 'name': '不应被改名', 'reason': 'should fail',
    }, method='PATCH').status_code == 403
    assert api(delivery_admin, f"/admin/batches/{batch['id']}", {
        'expectedVersion': batch['version'], 'name': '允许改名', 'reason': 'delivery manage',
    }, method='PATCH').status_code == 200
    assert api(mail_admin, f"/admin/batches/{batch['id']}/publish", {
        'expectedVersion': batch['version'] + 1, 'reason': 'should fail',
    }).status_code == 403


def test_limited_admin_cannot_use_the_courier_admin_bypass(
    api, client_factory, db, staff_factory
):
    mail_admin = staff_session(client_factory, db, ['mail.manage'])
    delivery_admin = staff_session(client_factory, db, ['delivery.manage'])
    courier = staff_factory(role='COURIER', capabilities=[])
    day = (datetime.now(SHANGHAI) + timedelta(days=3)).date().isoformat()
    batch = api(delivery_admin, '/admin/batches', {
        'name': '配送员旁路批次',
        'date': day,
        'time': '13:00-15:00',
        'courierId': courier.id,
        'reason': 'permission boundary test',
    }).json()['data']

    # Admin routes and courier routes both reject an unrelated capability,
    # including the historical _is_admin bypass.
    assert api(mail_admin, f"/admin/batches/{batch['id']}/start", {
        'expectedVersion': batch['version'],
    }).status_code == 403
    assert api(mail_admin, f"/courier/batches/{batch['id']}/start", {
        'expectedVersion': batch['version'],
    }).status_code == 403
    assert api(mail_admin, '/courier/batches', method='GET').status_code == 403
    assert api(mail_admin, f"/courier/batches/{batch['id']}", method='GET').status_code == 403

    assert api(delivery_admin, '/courier/batches', method='GET').status_code == 200


# ---------------------------------------------------------------------------
# Private media
# ---------------------------------------------------------------------------


def test_media_admin_capabilities_are_scope_bound(
    api, client_factory, db, account_factory, staff_factory
):
    mail_admin = staff_session(client_factory, db, ['mail.manage'])
    production_admin = staff_session(client_factory, db, ['production.manage'])
    delivery_admin = staff_session(client_factory, db, ['delivery.manage'])
    owner = account_factory()
    other = account_factory()
    courier = staff_factory(role='COURIER', capabilities=[])
    order = make_order(db, owner.principal_id, status='DELIVERING')
    day = (datetime.now(SHANGHAI) + timedelta(days=2)).date().isoformat()
    batch = api(delivery_admin, '/admin/batches', {
        'name': '凭证批次', 'date': day, 'time': '09:00-11:00',
        'courierId': courier.id, 'reason': 'fixture',
    }).json()['data']
    assignment = DeliveryAssignment(
        order_id=order.id,
        batch_id=batch['id'],
        courier_id=courier.id,
        status='STARTED',
        active=True,
    )
    db.add(assignment)
    db.commit()

    print_asset = make_asset(db, 'PRINT_SOURCE', principal_id=owner.principal_id)
    final_asset = make_asset(db, 'PRINT_FINAL', principal_id=owner.principal_id,
                             name='final.pdf')
    proof_asset = make_asset(db, 'DELIVERY_PROOF', staff_id=courier.id,
                             assignment_id=assignment.id, name='proof.jpg')
    db.add(DeliveryProof(order_id=order.id, assignment_id=assignment.id,
                         photo_asset_id=proof_asset.id, active=True))
    db.commit()
    owner_client = customer_session(client_factory, db, owner)
    other_client = customer_session(client_factory, db, other)
    courier_client = staff_client_for(client_factory, db, courier)

    # production.manage sees production artifacts, not delivery proofs.
    assert api(production_admin, f'/assets/{print_asset.id}', method='GET').status_code == 200
    assert api(production_admin, f'/assets/{proof_asset.id}', method='GET').status_code == 403
    # delivery.manage sees proof assets, not arbitrary print sources.
    assert api(delivery_admin, f'/assets/{proof_asset.id}', method='GET').status_code == 200
    assert api(delivery_admin, f'/assets/{print_asset.id}', method='GET').status_code == 403
    assert api(delivery_admin, f'/assets/{final_asset.id}', method='GET').status_code == 403
    assert api(production_admin, f'/assets/{final_asset.id}', method='GET').status_code == 200
    # mail-only admin cannot read either private class.
    assert api(mail_admin, f'/assets/{print_asset.id}', method='GET').status_code == 403
    assert api(mail_admin, f'/assets/{proof_asset.id}', method='GET').status_code == 403
    # The customer who owns the order can read its proof; others cannot.
    assert api(owner_client, f'/assets/{proof_asset.id}', method='GET').status_code == 200
    assert api(other_client, f'/assets/{proof_asset.id}', method='GET').status_code == 404
    # The currently assigned courier keeps the proof asset but never print files.
    assert api(courier_client, f'/assets/{proof_asset.id}', method='GET').status_code == 200
    assert api(courier_client, f'/assets/{print_asset.id}', method='GET').status_code == 404
    assert api(courier_client, f'/assets/{final_asset.id}', method='GET').status_code == 404
    # Ordinary customer source access is unchanged.
    assert api(owner_client, f'/assets/{print_asset.id}', method='GET').status_code == 200
    assert api(other_client, f'/assets/{print_asset.id}', method='GET').status_code == 404


def test_order_links_scope_final_preview_but_never_originals(
    api, client_factory, db, account_factory
):
    """ORDER_VIEW is an order link, not permission to download print masters."""
    owner = account_factory()
    viewer = account_factory()
    order = make_order(db, owner.principal_id, status='DELIVERING')
    source = make_asset(db, 'PRINT_SOURCE', principal_id=owner.principal_id,
                        name='source.pdf')
    final = make_asset(db, 'PRINT_FINAL', principal_id=owner.principal_id,
                       name='final.pdf', payload=b'%PDF-1.7 final')
    db.add_all([
        OrderFile(order_id=order.id, revision=1, client_file_id='source-file',
                  asset_id=source.id, kind='PRINT_SOURCE'),
        OrderFile(order_id=order.id, revision=1, client_file_id='final-file',
                  asset_id=final.id, kind='FINAL_PDF'),
    ])
    grant = AccessGrant(order_id=order.id, account_id=viewer.id,
                        scopes=['ORDER_VIEW'], expires_at=utcnow() + timedelta(days=1))
    db.add(grant)
    db.commit()
    viewer_client = customer_session(client_factory, db, viewer)

    # A read-only order link must not expose the source or the final master.
    assert api(viewer_client, f'/assets/{source.id}', method='GET').status_code == 404
    assert api(viewer_client, f'/assets/{final.id}', method='GET').status_code == 404

    # FINAL_PDF_PREVIEW opens exactly the final preview, never the source master.
    grant.scopes = ['ORDER_VIEW', 'FINAL_PDF_PREVIEW']
    db.commit()
    assert api(viewer_client, f'/assets/{final.id}', method='GET').status_code == 200
    assert api(viewer_client, f'/assets/{source.id}', method='GET').status_code == 404

    # The preview scope by itself is sufficient for the final, still not source.
    grant.scopes = ['FINAL_PDF_PREVIEW']
    db.commit()
    assert api(viewer_client, f'/assets/{final.id}', method='GET').status_code == 200
    assert api(viewer_client, f'/assets/{source.id}', method='GET').status_code == 404


def test_delivery_proof_view_only_reaches_the_associated_proof(
    api, client_factory, db, account_factory, staff_factory
):
    owner = account_factory()
    viewer = account_factory()
    courier = staff_factory(role='COURIER', capabilities=[])
    first = make_order(db, owner.principal_id, status='DELIVERING')
    second = make_order(db, owner.principal_id, status='DELIVERING')
    batch = DeliveryBatch(name='proof-scope', courier_id=courier.id,
                          starts_at=utcnow() + timedelta(hours=1),
                          ends_at=utcnow() + timedelta(hours=3), status='DELIVERING')
    db.add(batch)
    db.flush()
    assignment_a = DeliveryAssignment(order_id=first.id, batch_id=batch.id,
                                      courier_id=courier.id, status='STARTED', active=True)
    assignment_b = DeliveryAssignment(order_id=second.id, batch_id=batch.id,
                                      courier_id=courier.id, status='STARTED', active=True)
    db.add_all([assignment_a, assignment_b])
    db.flush()
    photo_a = make_asset(db, 'DELIVERY_PROOF', staff_id=courier.id,
                         assignment_id=assignment_a.id, name='a.jpg',
                         payload=b'\xff\xd8\xff\xd9 a')
    photo_b = make_asset(db, 'DELIVERY_PROOF', staff_id=courier.id,
                         assignment_id=assignment_b.id, name='b.jpg',
                         payload=b'\xff\xd8\xff\xd9 b')
    db.add_all([
        DeliveryProof(order_id=first.id, assignment_id=assignment_a.id,
                      photo_asset_id=photo_a.id, active=True),
        DeliveryProof(order_id=second.id, assignment_id=assignment_b.id,
                      photo_asset_id=photo_b.id, active=True),
    ])
    grant = AccessGrant(order_id=first.id, account_id=viewer.id,
                        scopes=['ORDER_VIEW', 'DELIVERY_PROOF_VIEW'],
                        expires_at=utcnow() + timedelta(days=1))
    db.add(grant)
    db.commit()
    viewer_client = customer_session(client_factory, db, viewer)

    assert api(viewer_client, f'/assets/{photo_a.id}', method='GET').status_code == 200
    # Holding a proof grant for one order must not leak another order's photo.
    assert api(viewer_client, f'/assets/{photo_b.id}', method='GET').status_code == 404

    grant.scopes = ['ORDER_VIEW']
    db.commit()
    assert api(viewer_client, f'/assets/{photo_a.id}', method='GET').status_code == 404


def test_order_purge_blocks_live_supplementary_and_keeps_five_ledger_keys(
    api, db, admin_client, account_factory
):
    from app.maintenance import process_purge_job

    owner = account_factory()
    order = make_order(db, owner.principal_id, status='CANCELLED')
    quote = db.get(Quote, order.quote_id)
    quote.data = {**(quote.data or {}), 'selectedPromotionType': 'STAFF_GRADE'}
    quote.discount_cents = 100
    quote.total_cents = quote.subtotal_cents - 100
    order.total_cents = quote.total_cents
    order.settlement_kind = 'CASH_MANUAL'
    order.cash_received_cents = order.total_cents
    charge = SupplementaryCharge(order_id=order.id, purpose='TEST', amount_cents=100,
                                 include_in_payroll=False, created_by='test')
    db.add(charge)
    db.commit()
    order_version = order.version
    order_created_date = order.created_at.date().isoformat()

    blocked = api(admin_client, '/admin/trash', {
        'targetType': 'ORDER', 'targetId': order.id,
        'expectedVersion': order_version, 'reason': 'supplementary check',
    })
    assert blocked.status_code == 409, blocked.text
    assert any(item.startswith('SUPPLEMENTARY_ACTIVE:')
               for item in blocked.json()['error']['details']['blockers'])

    charge.status = 'CANCELLED'
    charge.cash_received_cents = 100
    dispute = SupplementaryDispute(charge_id=charge.id, reason='still open',
                                   status='OPEN', actor_id='test')
    db.add(dispute)
    db.commit()
    dispute_blocked = api(admin_client, '/admin/trash', {
        'targetType': 'ORDER', 'targetId': order.id,
        'expectedVersion': order_version, 'reason': 'open dispute check',
    })
    assert dispute_blocked.status_code == 409, dispute_blocked.text
    assert f'SUPPLEMENTARY_DISPUTE_OPEN:{dispute.id}' in \
        dispute_blocked.json()['error']['details']['blockers']
    dispute.status = 'RESOLVED'
    db.commit()
    response = api(admin_client, '/admin/trash', {
        'targetType': 'ORDER', 'targetId': order.id,
        'expectedVersion': order_version, 'reason': 'supplementary settled',
    })
    assert response.status_code == 200, response.text
    entry_id = response.json()['data']['id']
    process_purge_job(db, entry_id)
    db.commit()
    db.expire_all()

    assert db.get(SupplementaryCharge, charge.id) is None
    ledger = db.scalar(select(AnonymousLedger).where(
        AnonymousLedger.bucket == f'trash:{entry_id}'))
    assert ledger is not None
    for key in ('orderCount', 'subtotalCents', 'payableCents',
                'cashReceivedCents', 'refundCents'):
        assert key in ledger.totals
    assert ledger.totals['staffDiscountCents'] == 100
    assert 'couponDiscountCents' not in ledger.totals
    assert 'volumeDiscountCents' not in ledger.totals
    assert ledger.totals['supplementaryCashReceivedCents'] == 100
    assert ledger.totals['supplementaryFeesCents'] == 0
    assert ledger.totals['orderCreatedDate'] == order_created_date
    assert ledger.totals['purgedDate']
    # Only day-level dates survive; no identifiers, contact data or timestamps.
    assert 'orderId' not in ledger.totals
    assert 'createdAt' not in ledger.totals
    assert 'customerName' not in ledger.totals
    assert 'phone' not in ledger.totals
    # purge_guard snapshots the anonymous ledger into the durable tombstone
    tombstone = db.get(TrashEntry, entry_id)
    assert tombstone is not None
    assert tombstone.data['ledgerTotals']['orderCreatedDate'] == order_created_date
    assert tombstone.data['ledgerTotals']['payableCents'] == ledger.totals['payableCents']
    assert tombstone.data['purgedRows']


def test_needs_configuration_payroll_marker_also_blocks_order_cleanup(
    api, client_factory, db, account_factory, staff_factory
):
    data_admin = staff_session(client_factory, db, ['data.manage'])
    account = account_factory()
    order = make_order(db, account.principal_id, status='CANCELLED')
    courier = staff_factory(role='COURIER', capabilities=[])
    wage = WageEntry(
        staff_id=courier.id, order_id=order.id,
        source_key='needs-config-' + secrets.token_hex(8),
        kind='NEEDS_CONFIGURATION', amount_cents=0, period='2026-10',
        snapshot={'reason': 'missing wage policy'}, reason='', actor_id='payroll',
    )
    db.add(wage)
    db.commit()
    response = api(data_admin, '/admin/trash', {
        'targetType': 'ORDER', 'targetId': order.id,
        'expectedVersion': order.version, 'reason': 'needs-config check',
    })
    assert response.status_code == 409, response.text
    assert f'WAGE_UNSETTLED:{wage.id}' in response.json()['error']['details']['blockers']


def test_staff_awaiting_password_change_has_no_private_asset_bypass(
    api, client_factory, db, account_factory
):
    """FINAL N05 retires must-change staff privilege entirely."""
    owner = account_factory()
    restricted = staff_session(client_factory, db, ['*'], must_change_password=True)
    foreign = make_asset(db, 'PRINT_SOURCE', principal_id=owner.principal_id)
    own_staff_asset = make_asset(db, 'PRINT_FINAL', staff_id=restricted.staff.id)
    public_asset = make_asset(db, 'PUBLIC_MAP', name='map.png')
    owner_client = customer_session(client_factory, db, owner)

    assert api(restricted, f'/assets/{foreign.id}', method='GET').status_code == 404
    assert api(restricted, f'/assets/{own_staff_asset.id}', method='GET').status_code == 404
    # Public reads stay public for an ordinary verified customer.
    assert api(restricted, f'/assets/{public_asset.id}', method='GET').status_code == 200
    # The customer identity itself remains independent of the staff restriction.
    assert api(owner_client, f'/assets/{foreign.id}', method='GET').status_code == 200

    response = restricted.post(
        '/api/v1/assets',
        data={'purpose': 'PUBLIC_MAP'},
        files={'file': ('map.png', b'\x89PNG\r\n\x1a\nnot-a-real-png', 'image/png')},
        headers=multipart_headers(restricted),
    )
    assert response.status_code == 401


def test_public_upload_requires_config_manage(
    api, client_factory, db
):
    mail_admin = staff_session(client_factory, db, ['mail.manage'])
    config_admin = staff_session(client_factory, db, ['config.manage'])

    def upload(client):
        return client.post(
            '/api/v1/assets',
            data={'purpose': 'PUBLIC_MAP'},
            files={'file': ('map.png', b'\x89PNG\r\n\x1a\nnot-a-real-png', 'image/png')},
            headers=multipart_headers(client),
        )

    assert upload(mail_admin).status_code == 403
    # config.manage passes the authorization gate; invalid image content is a
    # separate 4xx validation failure, proving the capability itself is valid.
    response = upload(config_admin)
    assert response.status_code != 403, response.text


# ---------------------------------------------------------------------------
# Maintenance / trash / audit / account deletion
# ---------------------------------------------------------------------------


def test_maintenance_admin_capabilities_are_separated(
    api, client_factory, db, account_factory
):
    mail_admin = staff_session(client_factory, db, ['mail.manage'])
    data_admin = staff_session(client_factory, db, ['data.manage'])
    audit_admin = staff_session(client_factory, db, ['audit.view'])
    customer_admin = staff_session(client_factory, db, ['customer.manage'])
    account = account_factory()
    request = DeletionRequest(account_id=account.id, status='REQUESTED',
                              data={'reason': 'customer request'})
    db.add(request)
    db.commit()
    target = make_asset(db, 'PRINT_SOURCE', principal_id=account.principal_id)

    assert api(mail_admin, '/admin/trash', method='GET').status_code == 403
    assert api(data_admin, '/admin/trash', method='GET').status_code == 200
    assert api(mail_admin, '/admin/audit-logs', method='GET').status_code == 403
    assert api(data_admin, '/admin/audit-logs', method='GET').status_code == 403
    assert api(audit_admin, '/admin/audit-logs', method='GET').status_code == 200
    assert api(mail_admin, '/admin/deletion-requests', method='GET').status_code == 403
    assert api(data_admin, '/admin/deletion-requests', method='GET').status_code == 403
    assert api(customer_admin, '/admin/deletion-requests', method='GET').status_code == 200

    # Trash creation is behind data.manage, not mail.manage.
    payload = {
        'targetType': 'ASSET',
        'targetId': target.id,
        'expectedVersion': target.version,
        'reason': 'permission boundary test',
    }
    assert api(mail_admin, '/admin/trash', payload).status_code == 403
    assert api(data_admin, '/admin/trash', payload).status_code == 200


def test_trash_restore_and_purge_require_data_manage(
    api, client_factory, db, account_factory
):
    mail_admin = staff_session(client_factory, db, ['mail.manage'])
    data_admin = staff_session(client_factory, db, ['data.manage'])
    owner = account_factory()
    target = make_asset(db, 'PRINT_SOURCE', principal_id=owner.principal_id)
    storage_key = target.storage_key

    created = api(data_admin, '/admin/trash', {
        'targetType': 'ASSET', 'targetId': target.id,
        'expectedVersion': target.version, 'reason': 'trash boundary',
    })
    assert created.status_code == 200, created.text
    entry = created.json()['data']

    preview = {'expectedVersion': entry['version'], 'reason': 'preview'}
    assert api(mail_admin, f"/admin/trash/{entry['id']}/restore-preview",
               preview).status_code == 403
    assert api(data_admin, f"/admin/trash/{entry['id']}/restore-preview",
               preview).status_code == 200
    restore = {'expectedVersion': entry['version'], 'reason': 'restore boundary'}
    assert api(mail_admin, f"/admin/trash/{entry['id']}/restore",
               restore).status_code == 403
    restored = api(data_admin, f"/admin/trash/{entry['id']}/restore", restore)
    assert restored.status_code == 200, restored.text

    # Trash the restored asset again so the purge guard can be exercised.
    db.refresh(target)
    trashed_again = api(data_admin, '/admin/trash', {
        'targetType': 'ASSET', 'targetId': target.id,
        'expectedVersion': target.version, 'reason': 'purge boundary',
    })
    assert trashed_again.status_code == 200, trashed_again.text
    second = trashed_again.json()['data']
    purge = {'expectedVersion': second['version'], 'reason': 'purge boundary'}
    assert api(mail_admin, f"/admin/trash/{second['id']}/purge",
               purge).status_code == 403
    purged = api(data_admin, f"/admin/trash/{second['id']}/purge", purge)
    assert purged.status_code == 200, purged.text
    # The response is serialized before commit-time physical cleanup. It may
    # truthfully report pending; the persisted state after commit is final.
    assert purged.json()['data']['status'] == 'PURGE_PENDING'
    db.expire_all()
    entry_row = db.get(TrashEntry, second['id'])
    assert entry_row is not None and entry_row.status == 'PURGED'
    assert not (storage_root() / storage_key).exists()


def test_account_deletion_review_requires_customer_manage(
    api, client_factory, db, account_factory
):
    mail_admin = staff_session(client_factory, db, ['mail.manage'])
    data_admin = staff_session(client_factory, db, ['data.manage'])
    customer_admin = staff_session(client_factory, db, ['customer.manage'])
    account = account_factory()
    request = DeletionRequest(account_id=account.id, status='REQUESTED',
                              data={'reason': 'please delete'})
    db.add(request)
    db.commit()
    body = {'expectedVersion': request.version, 'reason': 'review'}
    assert api(mail_admin, f'/admin/deletion-requests/{request.id}/approve',
               body).status_code == 403
    assert api(data_admin, f'/admin/deletion-requests/{request.id}/approve',
               body).status_code == 403
    approved = api(customer_admin, f'/admin/deletion-requests/{request.id}/approve', body)
    assert approved.status_code == 200, approved.text
    assert approved.json()['data']['status'] == 'APPROVED'
    db.expire_all()
    row = db.get(DeletionRequest, request.id)
    assert row is not None and row.status == 'APPROVED'


def test_staff_awaiting_password_change_cannot_manage_maintenance(
    api, client_factory, db
):
    restricted = staff_session(client_factory, db, ['*'], must_change_password=True)
    trash = api(restricted, '/admin/trash', method='GET')
    assert trash.status_code == 401, trash.text
    assert api(restricted, '/admin/audit-logs', method='GET').status_code == 401
    assert api(restricted, '/admin/deletion-requests', method='GET').status_code == 401


# ---------------------------------------------------------------------------
# N05 linked Staff / N06 payroll retention boundaries
# ---------------------------------------------------------------------------


def test_last_active_super_admin_account_cannot_be_deleted(
    api, client_factory, db, account_factory
):
    data_admin = staff_session(client_factory, db, ['data.manage'])
    customer_admin = staff_session(client_factory, db, ['customer.manage'])
    account = account_factory()
    super_admin = make_staff(db, account, is_super_admin=True)

    blocked = api(data_admin, '/admin/trash', {
        'targetType': 'ACCOUNT',
        'targetId': account.id,
        'expectedVersion': account.version,
        'reason': 'would orphan the last super admin',
    })
    assert blocked.status_code == 409, blocked.text
    error = blocked.json()['error']
    assert error['code'] == 'TRASH_BLOCKED'
    assert f'LAST_SUPER_ADMIN:{super_admin.id}' in error['details']['blockers']

    request = DeletionRequest(account_id=account.id, status='REQUESTED',
                              data={'reason': 'delete my account'})
    db.add(request)
    db.commit()
    review = api(customer_admin, f'/admin/deletion-requests/{request.id}/approve', {
        'expectedVersion': request.version,
        'reason': 'would orphan the last super admin',
    })
    assert review.status_code == 409, review.text
    assert review.json()['error']['code'] == 'DELETION_BLOCKED'
    assert f'LAST_SUPER_ADMIN:{super_admin.id}' in \
        review.json()['error']['details']['blockers']
    db.refresh(super_admin)
    assert super_admin.enabled is True
    assert super_admin.status == 'ACTIVE'


def test_super_admin_transfer_allows_previous_account_deletion_without_orphaning(
    api, client_factory, db, account_factory
):
    """Unique superadmin transfer, then ordinary deletion of the old account.

    The invariant is deliberately preserved: there is always exactly one
    active superadmin.  The old holder is demoted by a real HTTP transfer,
    disabled, and only then can their account be deleted.
    """
    customer_admin = staff_session(client_factory, db, ['customer.manage'])
    doomed_account = account_factory()
    survivor_account = account_factory()
    doomed = make_staff(db, doomed_account, is_super_admin=True)
    survivor = make_staff(db, survivor_account, is_super_admin=False)
    doomed_client = staff_client_for(client_factory, db, doomed)

    promoted = api(doomed_client, f'/admin/staff/{survivor.id}', {
        'expectedVersion': survivor.version,
        'reason': 'transfer superadmin before deleting the old account',
        'isSuperAdmin': True,
    }, method='PATCH')
    assert promoted.status_code == 200, promoted.text

    db.expire_all()
    doomed_row = db.get(Staff, doomed.id)
    survivor_row = db.get(Staff, survivor.id)
    assert survivor_row.is_super_admin is True
    assert doomed_row.is_super_admin is False
    assert db.scalar(select(func.count()).select_from(Staff).where(
        Staff.is_super_admin.is_(True))) == 1

    doomed_row.enabled = False
    doomed_row.status = 'DISABLED'
    db.commit()

    request = DeletionRequest(account_id=doomed_account.id, status='REQUESTED',
                              data={'reason': 'delete old admin account'})
    db.add(request)
    db.commit()
    approved = api(customer_admin, f'/admin/deletion-requests/{request.id}/approve', {
        'expectedVersion': request.version,
        'reason': 'another active superadmin remains',
    })
    assert approved.status_code == 200, approved.text

    db.expire_all()
    doomed_row = db.get(Staff, doomed.id)
    survivor_row = db.get(Staff, survivor.id)
    assert doomed_row is not None and doomed_row.enabled is False
    assert doomed_row.status == 'DISABLED'
    assert survivor_row is not None and survivor_row.enabled is True
    assert survivor_row.status == 'ACTIVE'
    assert survivor_row.is_super_admin is True
    session = db.scalar(select(AuthSession).where(AuthSession.account_id == doomed_account.id))
    assert session is not None and session.revoked_at is not None


def test_account_purge_disables_linked_staff_and_revokes_session(
    api, client_factory, db, account_factory
):
    data_admin = staff_session(client_factory, db, ['data.manage'])
    account = account_factory()
    staff = make_staff(db, account)
    staff_client_for(client_factory, db, staff)

    blocked = api(data_admin, '/admin/trash', {
        'targetType': 'ACCOUNT',
        'targetId': account.id,
        'expectedVersion': account.version,
        'reason': 'active employee must block account deletion',
    })
    assert blocked.status_code == 409, blocked.text
    assert f'STAFF_ACTIVE:{staff.id}' in \
        blocked.json()['error']['details']['blockers']

    staff.enabled = False
    staff.status = 'DISABLED'
    db.commit()
    account = db.get(Account, account.id)

    created = api(data_admin, '/admin/trash', {
        'targetType': 'ACCOUNT',
        'targetId': account.id,
        'expectedVersion': account.version,
        'reason': 'trash disabled employee account',
    })
    assert created.status_code == 200, created.text
    entry = created.json()['data']
    db.refresh(staff)
    assert staff.enabled is False and staff.status == 'DISABLED'

    purged = api(data_admin, f"/admin/trash/{entry['id']}/purge", {
        'expectedVersion': entry['version'],
        'reason': 'purge account with linked staff',
    })
    assert purged.status_code == 200, purged.text
    assert purged.json()['data']['status'] == 'PURGED'
    db.expire_all()
    staff_row = db.get(Staff, staff.id)
    assert staff_row is not None and staff_row.enabled is False
    assert staff_row.status == 'DISABLED'
    session = db.scalar(select(AuthSession).where(AuthSession.account_id == account.id))
    assert session is not None and session.revoked_at is not None


def test_order_cleanup_blocks_unsettled_wage_then_detaches_settled_fact(
    api, client_factory, db, account_factory, staff_factory
):
    data_admin = staff_session(client_factory, db, ['data.manage'])
    account = account_factory()
    order = make_order(db, account.principal_id, status='CANCELLED')
    order_id = order.id
    courier = staff_factory(role='COURIER', capabilities=[])
    wage = WageEntry(
        staff_id=courier.id,
        order_id=order_id,
        source_key='wage-' + secrets.token_hex(8),
        kind='DELIVERY',
        amount_cents=500,
        period='2026-10',
        snapshot={'orderTotalCents': order.total_cents},
        reason='delivery wage',
        actor_id='test',
    )
    db.add(wage)
    db.commit()

    blocked = api(data_admin, '/admin/trash', {
        'targetType': 'ORDER',
        'targetId': order_id,
        'expectedVersion': order.version,
        'reason': 'unsettled payroll must block cleanup',
    })
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()['error']['code'] == 'TRASH_BLOCKED'
    assert f'WAGE_UNSETTLED:{wage.id}' in \
        blocked.json()['error']['details']['blockers']

    settlement = WageSettlement(
        staff_id=courier.id,
        period='2026-10',
        amount_cents=500,
        paid_by='test',
        reason='settled before cleanup',
    )
    db.add(settlement)
    db.flush()
    settlement_id = settlement.id
    item = WageSettlementItem(settlement_id=settlement_id, entry_id=wage.id)
    db.add(item)
    db.commit()

    created = api(data_admin, '/admin/trash', {
        'targetType': 'ORDER',
        'targetId': order_id,
        'expectedVersion': order.version,
        'reason': 'settled payroll may detach order reference',
    })
    assert created.status_code == 200, created.text
    entry = created.json()['data']
    purged = api(data_admin, f"/admin/trash/{entry['id']}/purge", {
        'expectedVersion': entry['version'],
        'reason': 'purge settled order',
    })
    assert purged.status_code == 200, purged.text
    assert purged.json()['data']['status'] == 'PURGED'

    db.expire_all()
    assert db.get(Order, order_id) is None
    wage_row = db.get(WageEntry, wage.id)
    assert wage_row is not None
    assert wage_row.order_id is None
    assert wage_row.assignment_id is None
    assert wage_row.amount_cents == 500
    assert wage_row.snapshot['purgedOrderId'] == order_id
    assert 'purgedAt' in wage_row.snapshot
    assert db.get(WageSettlementItem, item.id) is not None
    assert db.get(WageSettlement, settlement_id) is not None


def test_account_cleanup_blocks_unsettled_linked_staff_wage(
    api, client_factory, db, account_factory
):
    data_admin = staff_session(client_factory, db, ['data.manage'])
    account = account_factory()
    staff = make_staff(db, account)
    wage = WageEntry(
        staff_id=staff.id,
        order_id=None,
        source_key='wage-' + secrets.token_hex(8),
        kind='MONTHLY',
        amount_cents=900,
        period='2026-10',
        snapshot={'note': 'pending payroll'},
        reason='pending wage',
        actor_id='test',
    )
    db.add(wage)
    db.commit()

    blocked = api(data_admin, '/admin/trash', {
        'targetType': 'ACCOUNT',
        'targetId': account.id,
        'expectedVersion': account.version,
        'reason': 'pending staff payroll must block account cleanup',
    })
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()['error']['code'] == 'TRASH_BLOCKED'
    assert f'WAGE_UNSETTLED:{wage.id}' in \
        blocked.json()['error']['details']['blockers']
    db.refresh(account)
    assert account.deleted_at is None
    db.refresh(staff)
    assert staff.enabled is True


def test_disabled_employee_with_settled_payroll_is_anonymised_on_account_deletion(
    api, client_factory, db, account_factory
):
    from app.models import WageEntry, WageSettlement, WageSettlementItem

    customer_admin = staff_session(client_factory, db, ['customer.manage'])
    account = account_factory()
    staff = make_staff(db, account, name='真实员工')
    staff.phone = '13800000000'
    staff.email = 'real-staff@example.test'
    staff_client_for(client_factory, db, staff)
    staff.enabled = False
    staff.status = 'DISABLED'
    wage = WageEntry(
        staff_id=staff.id, order_id=None,
        source_key='wage-' + secrets.token_hex(8), kind='MONTHLY',
        amount_cents=700, period='2026-10',
        snapshot={'staffName': '真实员工', 'note': 'settled payroll'},
        reason='settled wage', actor_id='test',
    )
    db.add(wage)
    db.flush()
    settlement = WageSettlement(
        staff_id=staff.id, period='2026-10', amount_cents=700,
        paid_by='test', reason='paid',
    )
    db.add(settlement)
    db.flush()
    item = WageSettlementItem(settlement_id=settlement.id, entry_id=wage.id)
    db.add(item)
    request = DeletionRequest(account_id=account.id, status='REQUESTED',
                              data={'reason': 'delete my account'})
    db.add(request)
    db.commit()

    approved = api(customer_admin, f'/admin/deletion-requests/{request.id}/approve', {
        'expectedVersion': request.version,
        'reason': 'employee explicitly disabled and payroll settled',
    })
    assert approved.status_code == 200, approved.text

    db.expire_all()
    retired = db.get(Staff, staff.id)
    assert retired is not None
    assert retired.enabled is False and retired.status == 'DISABLED'
    assert retired.account_id is None
    assert retired.name == '已注销员工'
    assert retired.phone == '' and retired.email is None
    assert retired.username.startswith('deleted-staff-')
    assert retired.capabilities == []
    assert retired.is_super_admin is False

    stored_wage = db.get(WageEntry, wage.id)
    assert stored_wage is not None and stored_wage.amount_cents == 700
    assert stored_wage.snapshot['staffName'] == '已注销员工'
    assert stored_wage.snapshot['note'] == 'settled payroll'
    assert db.get(WageSettlementItem, item.id) is not None
    assert db.get(WageSettlement, settlement.id) is not None
    session = db.scalar(select(AuthSession).where(AuthSession.account_id == account.id))
    assert session is not None and session.revoked_at is not None


def test_last_disabled_super_admin_still_cannot_be_deleted(
    api, client_factory, db, account_factory
):
    customer_admin = staff_session(client_factory, db, ['customer.manage'])
    account = account_factory()
    super_admin = make_staff(db, account, is_super_admin=True)
    super_admin.enabled = False
    super_admin.status = 'DISABLED'
    request = DeletionRequest(account_id=account.id, status='REQUESTED',
                              data={'reason': 'delete my account'})
    db.add(request)
    db.commit()

    blocked = api(customer_admin, f'/admin/deletion-requests/{request.id}/approve', {
        'expectedVersion': request.version,
        'reason': 'never remove the last super admin',
    })
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()['error']['code'] == 'DELETION_BLOCKED'
    assert f'LAST_SUPER_ADMIN:{super_admin.id}' in \
        blocked.json()['error']['details']['blockers']


def test_delivery_staff_catalog_requires_delivery_manage_and_is_minimal(
    api, client_factory, db
):
    dispatcher = staff_session(client_factory, db, ['delivery.manage'],
                               name='配送调度员')
    courier = staff_session(client_factory, db, [], role='COURIER', name='配送员甲')
    mail_admin = staff_session(client_factory, db, ['mail.manage'],
                               name='邮件管理员')
    restricted = staff_session(client_factory, db, ['delivery.manage'],
                               must_change_password=True, name='临时管理员')
    staff_admin = staff_session(client_factory, db, ['staff.manage'],
                                name='员工管理员')

    response = api(dispatcher, '/admin/delivery-staff', method='GET')
    assert response.status_code == 200, response.text
    items = response.json()['data']['items']
    ids = {row['id'] for row in items}
    assert courier.staff.id in ids and dispatcher.staff.id in ids
    assert mail_admin.staff.id not in ids
    assert restricted.staff.id not in ids
    assert staff_admin.staff.id not in ids

    row = next(item for item in items if item['id'] == dispatcher.staff.id)
    assert row == {
        'id': dispatcher.staff.id,
        'name': '配送调度员',
        'role': 'ADMIN',
        'enabled': True,
        'status': 'ACTIVE',
        'capabilities': ['delivery.manage'],
    }
    assert 'username' not in row and 'phone' not in row and 'email' not in row

    alias = api(dispatcher, '/admin/couriers', method='GET')
    assert alias.status_code == 200, alias.text
    assert {row['id'] for row in alias.json()['data']['items']} == ids

    denied = api(mail_admin, '/admin/delivery-staff', method='GET')
    assert denied.status_code == 403, denied.text
    denied = api(staff_admin, '/admin/delivery-staff', method='GET')
    assert denied.status_code == 403, denied.text


def test_delivery_staff_catalog_uses_linked_account_nickname(
    api, client_factory, db, account_factory
):
    dispatcher = staff_session(client_factory, db, ['delivery.manage'],
                               name='配送调度员')
    courier_account = account_factory(display_name='客户新昵称')
    courier = make_staff(db, courier_account, role='COURIER', name='内部员工姓名',
                         capabilities=[])

    response = api(dispatcher, '/admin/delivery-staff', method='GET')
    assert response.status_code == 200, response.text
    row = next(item for item in response.json()['data']['items']
               if item['id'] == courier.id)
    assert row['name'] == '客户新昵称'


def test_purge_tombstone_records_actual_rows_and_files(api, client_factory, db, account_factory):
    data_admin = staff_session(client_factory, db, ['data.manage'])
    owner = account_factory()
    asset = make_asset(db, 'PRINT_SOURCE', principal_id=owner.principal_id)
    asset_id, storage_key = asset.id, asset.storage_key
    created = api(data_admin, '/admin/trash', {
        'targetType': 'ASSET', 'targetId': asset.id,
        'expectedVersion': asset.version, 'reason': 'tombstone capture',
    })
    assert created.status_code == 200, created.text
    entry = created.json()['data']
    purged = api(data_admin, f"/admin/trash/{entry['id']}/purge", {
        'expectedVersion': entry['version'], 'reason': 'tombstone capture',
    })
    assert purged.status_code == 200, purged.text
    db.expire_all()
    row = db.get(TrashEntry, entry['id'])
    assert row is not None and row.status == 'PURGED'
    assert row.data['purgedAt']
    assert {'table': 'assets', 'id': asset_id} in row.data['purgedRows']
    assert storage_key in row.data['purgedFiles']
    assert 'purgedRows' in row.data and 'purgedFiles' in row.data
