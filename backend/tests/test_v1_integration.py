"""V1 end-to-end acceptance through the real HTTP API and PostgreSQL.

These tests deliberately avoid mocks and seeded business outcomes:

* a real multi-page PDF is uploaded through ``POST /assets``;
* the real outbox worker (``jobs.claim_next_job``/``process_claim``) converts it
  and renders the server final PDF;
* the quote is produced by the preparation worker and locked into the order;
* settlement, actual print, binding, packing, batch delivery and merchant
  rework all go through the public/admin/courier HTTP routes.

Only test configuration (prices, workflow, one location/slot) is seeded, and it
is seeded inside the per-process isolated PostgreSQL schema from conftest.
"""
from __future__ import annotations

import hashlib
import io
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

import pytest

pymupdf = pytest.importorskip('pymupdf')
PIL = pytest.importorskip('PIL')

from app import jobs  # noqa: E402
from app.core import token_hash, utcnow  # noqa: E402
from app.models import (  # noqa: E402
    Asset,
    AuthSession,
    ConfigSection,
    DeliveryAssignment,
    DeliveryProof,
    DeliverySlot,
    Location,
    Order,
    Payment,
    Staff,
)
from sqlalchemy import func, select


# All V1 routers, plus the config router that conftest always adds.  This keeps
# the acceptance path on the same application composition as production.
APP_MODULES = [
    'auth',
    'growth',
    'mail',
    'media',
    'commerce',
    'production',
    'delivery',
    'after_sales',
    'exports',
    'maintenance',
]


PRICES = {
    'bwSingle': 10, 'bwDuplex': 8, 'colorSingle': 20, 'colorDuplex': 16,
    'photo': 100, 'corner': 200, 'side': 300,
}
SERVICES = {
    'serviceEnabled': True, 'maxFiles': 10, 'maxMb': 20, 'maxOrderMb': 60,
    'maxPages': 100, 'maxCopies': 20, 'maxPixels': 40_000_000,
    'maxExpandedPages': 500, 'maxFinalMb': 60, 'bindingApproved': True,
    'bindingLimits': {'CORNER': None, 'SIDE': None},
    'formats': ['pdf', 'doc', 'docx', 'ppt', 'pptx', 'xls', 'xlsx',
                'jpg', 'jpeg', 'png', 'webp', 'heic'],
    'photoEnabled': True, 'photoCalibrated': True,
}
PHOTO = {
    'photoWidthMm': 127, 'photoHeightMm': 89,
    'margins': {'top': 3, 'bottom': 3, 'left': 3, 'right': 3},
}
RETENTION = {
    'sourceDays': 7, 'finalDays': 30, 'proofDays': 30, 'draftHours': 24,
    'exportHours': 24, 'trashHours': 24,
}
WORKFLOW = {
    'unpaidTimeoutMinutes': 60, 'quoteMinutes': 15,
    'locationMaxAgeSeconds': 300, 'locationMaxAccuracyMeters': 100,
    'shortCodeLength': 5,
    'pauses': {'newOrders': False, 'fileProcessing': False,
               'production': False, 'delivery': False},
}


# ---------------------------------------------------------------------------
# Helpers: real bytes, real HTTP, real workers
# ---------------------------------------------------------------------------


def seed_config(db):
    for section, data in (
        ('prices', PRICES),
        ('services', SERVICES),
        ('photo', PHOTO),
        ('retention', RETENTION),
        ('workflow', WORKFLOW),
        # An explicit announcement row is required because the public config
        # merges this section over services; leaving it empty would clear
        # serviceEnabled and reject checkout.
        ('announcement', {'announcement': '', 'serviceEnabled': True}),
    ):
        db.add(ConfigSection(section=section, data=data))
    db.commit()


def make_pdf(pages=3):
    document = pymupdf.open()
    for index in range(1, pages + 1):
        page = document.new_page(width=595, height=842)
        page.insert_text((72, 96), f'V1 integration page {index}', fontsize=18)
        page.draw_rect(pymupdf.Rect(72, 120, 523, 300), color=(0, 0, 0), width=1)
    payload = document.tobytes()
    document.close()
    return payload


def make_proof_png():
    image = PIL.Image.new('RGB', (120, 90), (235, 235, 235))
    for x in range(20, 100, 10):
        for y in range(20, 70, 10):
            image.putpixel((x, y), (20, 120, 200))
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def guest(client):
    response = client.post(
        '/api/v1/customer/session', json={},
        headers={'Origin': 'http://testserver',
                 'Idempotency-Key': 'v1-guest-' + secrets.token_hex(8)},
    )
    assert response.status_code == 200, response.text
    return response.json()['data']


def write_headers(client, key=None):
    from urllib.parse import unquote

    return {
        'Origin': 'http://testserver',
        'Idempotency-Key': key or 'v1-' + secrets.token_hex(12),
        'X-CSRF-Token': unquote(client.cookies.get('csrfToken', '')),
    }


def upload(client, payload, *, name, mime, purpose, extra=None):
    data = {'purpose': purpose, **(extra or {})}
    response = client.post(
        '/api/v1/assets',
        files={'file': (name, payload, mime)},
        data=data,
        headers=write_headers(client),
    )
    assert response.status_code == 200, response.text
    return response.json()['data']


def staff_client(client_factory, db, staff):
    """Build a unified customer-account session for an exact Staff.account_id.

    FINAL N05 has no standalone staff credential: a raw STAFF AuthSession must
    not authenticate.  Tests use a normal CUSTOMER session bound to the same
    account, which is exactly what /customer/auth/login issues.
    """
    from app.models import Account

    assert staff.account_id, (
        'staff_factory/admin_client must link a verified customer account; '
        'standalone staff credentials were removed by FINAL N05'
    )
    account = db.get(Account, staff.account_id)
    assert account is not None and account.verified_at is not None
    raw = secrets.token_urlsafe(32)
    db.add(AuthSession(
        kind='CUSTOMER', account_id=account.id,
        principal_id=account.principal_id,
        session_version=account.session_version,
        token_hash=token_hash(raw),
        expires_at=utcnow() + timedelta(days=30),
    ))
    db.commit()
    client = client_factory()
    client.cookies.set('dormprint_account_session', raw)
    client.staff = staff
    client.account = account
    return client


def drain_job(db, kinds, *, allow_zero=False):
    processed = 0
    while True:
        claim = jobs.claim_next_job(kinds=kinds)
        if claim is None:
            break
        job_id, token = claim
        assert jobs.process_claim(job_id, token) is True
        processed += 1
    assert processed or allow_zero, f'expected a queued {kinds} job'
    db.expire_all()


def start_pipeline(client, api, db, location, slot, key_prefix,
                   pages=3, binding='CORNER', allow_zero=False):
    """Upload a real PDF, run both workers and return the READY preparation."""
    payload = make_pdf(pages)
    uploaded = upload(
        client, payload, name='v1-acceptance.pdf',
        mime='application/pdf', purpose='PRINT_SOURCE',
    )
    asset_id = uploaded['assetId']
    assert uploaded['status'] == 'QUEUED'

    drain_job(db, ['ASSET'])
    asset = db.get(Asset, asset_id)
    assert asset is not None and asset.status == 'READY'
    assert asset.pages == pages

    body = {
        'files': [{
            'clientFileId': 'v1-file-1',
            'assetId': asset_id,
            'kind': 'DOCUMENT',
            'segments': [{
                'clientSegmentId': 'v1-segment-1',
                'ranges': [{'from': 1, 'to': pages}],
                'colorMode': 'BW',
                'sides': 'SINGLE',
                'flip': 'NONE',
                'copies': 1,
            }],
            'excludedPages': [],
            'bindingType': binding,
        }],
        'promotionIntent': {'selectionMode': 'AUTO'},
    }
    created = api(client, '/preparations', body, key=f'{key_prefix}-prep')
    assert created.status_code == 200, created.text
    preparation_id = created.json()['data']['id']
    assert created.json()['data']['status'] == 'QUEUED'

    drain_job(db, ['PREPARATION'])
    view_response = api(client, f'/preparations/{preparation_id}', method='GET')
    assert view_response.status_code == 200, view_response.text
    view = view_response.json()['data']
    assert view['status'] == 'READY', view
    assert view['validation']['ok'] is True
    assert len(view['artifacts']) == 1
    artifact = view['artifacts'][0]
    assert artifact['pages'] == pages
    assert artifact['sheets'] == pages
    assert artifact['sides'] == 'SINGLE'
    assert len(artifact['sha256']) == 64
    assert view['quote'] is not None
    assert view['quote']['totalCents'] > 0 or (
        allow_zero and view['quote']['totalCents'] == 0)

    content = client.get(artifact['url'])
    assert content.status_code == 200, content.text
    assert hashlib.sha256(content.content).hexdigest() == artifact['sha256']
    rendered = pymupdf.open(stream=content.content, filetype='pdf')
    try:
        assert rendered.page_count == pages
    finally:
        rendered.close()

    return SimpleNamespace(
        preparation_id=preparation_id,
        preparation_version=view['version'],
        artifact=artifact,
        quote=view['quote'],
        location=location,
        slot=slot,
    )


def submit_body(pipeline):
    return {
        'preparationId': pipeline.preparation_id,
        'quoteId': pipeline.quote['id'],
        'previewAcks': [{
            'artifactId': pipeline.artifact['id'],
            'sha256': pipeline.artifact['sha256'],
        }],
        'contact': {'wechatNickname': '验收同学', 'phone': '13800138000'},
        'locationId': pipeline.location.id,
        'slotId': pipeline.slot.id,
        'customerNote': 'V1 集成验收',
    }


def prepare_order(client, api, db, client_factory, *, key_prefix):
    seed_config(db)
    now = utcnow()
    location = Location(name='验收宿舍楼', x=0.4, y=0.6)
    slot = DeliverySlot(
        date=(now + timedelta(days=1)).strftime('%Y-%m-%d'),
        name='验收时段', starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=1, hours=1), capacity=2,
    )
    db.add_all([location, slot])
    db.commit()

    # A second guest makes the owner-isolation check independent of the order
    # creator's session.
    guest(client_factory())
    guest(client)
    pipeline = start_pipeline(client, api, db, location, slot, key_prefix)

    bad_body = submit_body(pipeline)
    bad_body['previewAcks'] = [{
        'artifactId': pipeline.artifact['id'], 'sha256': '0' * 64,
    }]
    bad = api(client, '/orders', bad_body, key=f'{key_prefix}-bad-ack')
    assert bad.status_code == 422, bad.text
    assert bad.json()['error']['code'] == 'PREVIEW_REQUIRED'

    # Contact validation happens before any order row is created; a nickname
    # made only of whitespace must not be normalized into a blank customer.
    blank_body = submit_body(pipeline)
    blank_body['contact']['wechatNickname'] = '   '
    blank = api(client, '/orders', blank_body, key=f'{key_prefix}-blank-nick')
    assert blank.status_code == 422, blank.text
    assert blank.json()['error']['code'] == 'VALIDATION_ERROR'
    assert any('wechat' in key.lower()
               for key in blank.json()['error'].get('fieldErrors', {})), blank.text

    body = submit_body(pipeline)
    order_key = f'{key_prefix}-order'
    response = api(client, '/orders', body, key=order_key)
    assert response.status_code == 200, response.text
    data = response.json()['data']
    order = data['order']
    assert order['status'] == 'PENDING_CONFIRMATION'
    assert order['confirmedSlotId'] is None
    assert order['slotStatus'] == 'REQUESTED'
    assert order['totalCents'] == pipeline.quote['totalCents']
    assert order['files'][0]['bindingType'] == 'CORNER'
    assert order['files'][0]['printStatus'] == 'PENDING'
    assert order['files'][0]['finalArtifacts'][0]['sha256'] == pipeline.artifact['sha256']
    assert data['accessToken']

    db.expire_all()
    persisted = db.get(Order, order['id'])
    assert persisted is not None
    assert db.get(DeliverySlot, slot.id).occupied == 0
    assert persisted.total_cents == pipeline.quote['totalCents']

    return SimpleNamespace(
        pipeline=pipeline, order=order, body=body, order_key=order_key,
        access_token=data['accessToken'], client=client,
    )


def admin_order(api, admin_client, order_id):
    response = api(admin_client, f'/admin/orders/{order_id}', method='GET')
    assert response.status_code == 200, response.text
    return response.json()['data']


def settle(api, admin_client, order, key):
    return api(admin_client, f'/admin/orders/{order["id"]}/confirm-payment', {
        'expectedVersion': order['version'],
        'contentRevision': order['contentRevision'],
        'quoteId': order['currentQuoteId'],
        'receivedCents': order['totalCents'],
        'settlementKind': 'CASH_MANUAL',
        'note': 'V1 验收人工收款',
    }, key=key)


def create_batch(api, admin_client, courier, name):
    now = utcnow()
    response = api(admin_client, '/admin/batches', {
        'name': name,
        'startsAt': (now - timedelta(minutes=30)).isoformat(),
        'endsAt': (now + timedelta(hours=2)).isoformat(),
        'courierId': courier.id,
        'reason': 'V1 验收配送批次',
    })
    assert response.status_code == 200, response.text
    return response.json()['data']


def add_order_to_batch(api, admin_client, batch, order_id, key):
    preview = api(admin_client, '/admin/batch-operations/preview', {
        'action': 'ADD',
        'orderIds': [order_id],
        'sourceBatchIds': [batch['id']],
        'targetBatchId': None,
        'versions': {batch['id']: batch['version']},
        'payload': {'reason': 'V1 验收加入批次'},
    }, key=f'{key}-preview')
    assert preview.status_code == 200, preview.text
    plan = preview.json()['data']
    assert plan['blockers'] == [], plan
    commit = api(admin_client, '/admin/batch-operations/commit',
                 {'planId': plan['planId']}, key=f'{key}-commit')
    assert commit.status_code == 200, commit.text
    return commit.json()['data']


def publish_and_start(api, admin_client, db, batch_id, key):
    from app.models import DeliveryBatch

    published = api(admin_client, f'/admin/batches/{batch_id}/publish',
                    {'expectedVersion': db.get(DeliveryBatch, batch_id).version},
                    key=f'{key}-publish')
    assert published.status_code == 200, published.text
    started = api(admin_client, f'/admin/batches/{batch_id}/start',
                  {'expectedVersion': published.json()['data']['version']},
                  key=f'{key}-start')
    assert started.status_code == 200, started.text
    db.expire_all()
    return started.json()['data']


def upload_proof(client, assignment):
    payload = make_proof_png()
    uploaded = upload(
        client, payload, name='v1-proof.png', mime='image/png',
        purpose='DELIVERY_PROOF', extra={'deliveryAssignmentId': assignment.id},
    )
    assert uploaded['status'] == 'READY'
    return uploaded


def submit_proof(api, client, order_id, assignment, photo_asset_id, key):
    detail = api(client, f'/courier/orders/{order_id}', method='GET')
    assert detail.status_code == 200, detail.text
    body = {
        'expectedVersion': detail.json()['data']['version'],
        'deliveryAssignmentId': assignment.id,
        'photoAssetId': photo_asset_id,
        'skipPhotoReason': None,
        'position': {
            'latitude': 31.2304, 'longitude': 121.4737,
            'accuracyMeters': 12,
            'capturedAt': (utcnow() - timedelta(seconds=10)).isoformat(),
            'coordinateSystem': 'WGS84',
        },
        'locationFailureCode': None,
        'skipLocationReason': None,
        'placementNote': 'V1 验收交付至一楼收发台',
    }
    response = api(client, f'/courier/orders/{order_id}/proof', body, key=key)
    assert response.status_code == 200, response.text
    return response.json()['data']


def active_assignment(db, order_id):
    from sqlalchemy import select

    db.expire_all()
    return db.scalar(select(DeliveryAssignment).where(
        DeliveryAssignment.order_id == order_id,
        DeliveryAssignment.active.is_(True),
    ))


def run_print_to_packed(api, admin_client, order):
    """Actual start -> attempt -> result -> binding -> manual pack."""
    current = admin_order(api, admin_client, order['id'])
    task_id = current['files'][0]['segments'][0]['id']
    artifact_id = current['files'][0]['artifactId']
    binding_id = current['files'][0]['bindingTaskId']

    started = api(admin_client, f'/admin/orders/{order["id"]}/start-production', {
        'expectedVersion': current['version'], 'reason': 'V1 验收实际开印',
    })
    assert started.status_code == 200, started.text

    current = admin_order(api, admin_client, order['id'])
    assert current['actualStartedAt'] is not None
    attempt = api(admin_client, f'/admin/print-tasks/{task_id}/attempts', {
        'expectedVersion': current['version'], 'artifactId': artifact_id,
    })
    assert attempt.status_code == 200, attempt.text
    attempt_id = attempt.json()['data']['attemptId']

    current = admin_order(api, admin_client, order['id'])
    result = api(admin_client, f'/admin/print-attempts/{attempt_id}/result', {
        'expectedVersion': current['version'], 'outcome': 'COMPLETED',
        'reason': '',
    })
    assert result.status_code == 200, result.text

    current = admin_order(api, admin_client, order['id'])
    assert current['files'][0]['printStatus'] == 'COMPLETED'
    if binding_id:
        binding = api(admin_client, f'/admin/binding-tasks/{binding_id}/complete',
                      {'expectedVersion': current['version']})
        assert binding.status_code == 200, binding.text

    current = admin_order(api, admin_client, order['id'])
    packed = api(admin_client, f'/admin/orders/{order["id"]}/pack', {
        'expectedVersion': current['version'],
        'checklistVersion': current['packingChecklistVersion'],
        'checks': [{'fileId': current['files'][0]['id'], 'confirmed': True}],
    })
    assert packed.status_code == 200, packed.text
    data = packed.json()['data']
    assert data['status'] == 'READY_FOR_DELIVERY'
    assert data['packingStatus'] == 'PACKED'
    return data


# ---------------------------------------------------------------------------
# Acceptance: intake, quote lock, owner/staff isolation, operations, settlement
# ---------------------------------------------------------------------------


def test_v1_intake_quote_lock_owner_staff_isolation_and_settlement(
        client, api, db, admin_client, client_factory, staff_factory):
    seed = prepare_order(client, api, db, client_factory, key_prefix='v1-intake')
    order = seed.order

    # Owner sees the order; anonymous, unrelated accounts and low-privilege
    # staff cannot.
    anonymous = client_factory()
    anonymous_hidden = anonymous.get(f'/api/v1/orders/{order["id"]}')
    assert anonymous_hidden.status_code == 404, anonymous_hidden.text

    other_guest = client_factory()
    guest(other_guest)
    hidden = api(other_guest, f'/orders/{order["id"]}', method='GET')
    assert hidden.status_code == 404, hidden.text
    assert hidden.json()['error']['code'] == 'NOT_FOUND'

    limited = staff_client(client_factory, db,
                           staff_factory(role='COURIER', capabilities=[]))
    assert api(limited, f'/admin/orders/{order["id"]}', method='GET').status_code == 403

    order_viewer = staff_client(
        client_factory, db,
        staff_factory(role='ADMIN', capabilities=['orders.view']),
    )
    viewer_response = api(order_viewer, f'/admin/orders/{order["id"]}', method='GET')
    assert viewer_response.status_code == 200, viewer_response.text
    denied_settlement = api(
        order_viewer, f'/admin/orders/{order["id"]}/confirm-payment', {
            'expectedVersion': viewer_response.json()['data']['version'],
            'contentRevision': viewer_response.json()['data']['contentRevision'],
            'quoteId': viewer_response.json()['data']['currentQuoteId'],
            'receivedCents': viewer_response.json()['data']['totalCents'],
            'settlementKind': 'CASH_MANUAL', 'note': '',
        })
    assert denied_settlement.status_code == 403, denied_settlement.text
    assert denied_settlement.json()['error']['code'] == 'FORBIDDEN'
    # A rejected permission check must not consume capacity or leave a payment
    # row behind in the request transaction.
    db.expire_all()
    assert db.get(DeliverySlot, seed.pipeline.slot.id).occupied == 0
    assert db.scalar(select(func.count()).select_from(Payment)) == 0

    # A read-only order grant is enough to view the locked final PDF, but it
    # does not silently obtain pending-order mutation actions.
    exchanger = client_factory()
    exchange = api(exchanger, '/order-access/exchange', {
        'orderId': order['id'], 'accessToken': seed.access_token,
    })
    assert exchange.status_code == 200, exchange.text
    grant_view = api(exchanger, f'/orders/{order["id"]}', method='GET')
    assert grant_view.status_code == 200, grant_view.text
    granted = grant_view.json()['data']
    assert granted['accessKind'] == 'ORDER_GRANT'
    assert 'ORDER_VIEW' in granted['allowedActions']
    assert 'ORDER_CANCEL_PENDING' not in granted['allowedActions']
    assert granted['files'][0]['finalArtifacts'][0]['sha256'] == seed.pipeline.artifact['sha256']

    # Unknown operation references never leak another actor's result, and the
    # known idempotency key resolves through the real operations endpoint.
    unknown = api(seed.client, '/operations/v1-does-not-exist', method='GET',
                  params={'path': 'POST /api/v1/orders'})
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()['error']['code'] == 'NOT_FOUND'
    known = api(seed.client, f'/operations/{seed.order_key}', method='GET',
                params={'path': 'POST /api/v1/orders'})
    assert known.status_code == 200, known.text
    known_data = known.json()['data']
    assert known_data['status'] == 'COMPLETED'
    assert known_data['result']['orderId'] == order['id']
    assert known_data['result']['status'] == 'PENDING_CONFIRMATION'
    # The lookup result is a safe reference, never the credential-bearing
    # cached response returned by the original submit.
    assert set(known_data['result']) == {'orderId', 'status'}
    assert 'accessToken' not in known.text

    # Operation results are scoped by the identity and session that created
    # them.  A different guest and a staff account must not read the key even
    # when they know the exact original route.
    for reader in (other_guest, order_viewer):
        foreign_ops = api(reader, f'/operations/{seed.order_key}', method='GET',
                          params={'path': 'POST /api/v1/orders'})
        assert foreign_ops.status_code == 404, foreign_ops.text
        assert foreign_ops.json()['error']['code'] == 'NOT_FOUND'

    # The same key under a different route is not the same operation.
    wrong_route = api(seed.client, f'/operations/{seed.order_key}', method='GET',
                      params={'path': 'POST /api/v1/preparations'})
    assert wrong_route.status_code == 404, wrong_route.text

    # Malformed/unsafe lookup paths are rejected as not found, not resolved
    # against the idempotency table.
    for bad_path in ('GET /api/v1/orders', '/api/v1/orders',
                     'POST /api/v1/orders?x=1', 'POST /api/v1/orders#x',
                     'POST /api/v1/auth/login'):
        invalid = api(seed.client, f'/operations/{seed.order_key}', method='GET',
                      params={'path': bad_path})
        assert invalid.status_code == 404, (bad_path, invalid.text)

    # Idempotent replay returns the original order instead of charging twice.
    replay = api(seed.client, '/orders', seed.body, key=seed.order_key)
    assert replay.status_code == 200, replay.text
    assert replay.json()['data']['order']['id'] == order['id']

    # Reusing the key with a different payload is a conflict, not a second
    # order and not a silent replay of the first payload.
    conflict_body = dict(seed.body)
    conflict_body['customerNote'] = 'changed payload'
    conflict = api(seed.client, '/orders', conflict_body, key=seed.order_key)
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()['error']['code'] == 'IDEMPOTENCY_CONFLICT'
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Order)) == 1

    # Payment confirmation locks the quote/content revision: a stale revision
    # is rejected, and a successful manual settlement occupies capacity.
    current = admin_order(api, admin_client, order['id'])
    stale = api(admin_client, f'/admin/orders/{order["id"]}/confirm-payment', {
        'expectedVersion': current['version'],
        'contentRevision': current['contentRevision'] + 99,
        'quoteId': current['currentQuoteId'],
        'receivedCents': current['totalCents'],
        'settlementKind': 'CASH_MANUAL', 'note': '',
    })
    assert stale.status_code == 409, stale.text
    assert stale.json()['error']['code'] == 'VERSION_CONFLICT'
    db.expire_all()
    assert db.get(DeliverySlot, seed.pipeline.slot.id).occupied == 0
    assert db.scalar(select(func.count()).select_from(Payment)) == 0

    settle_body = {
        'expectedVersion': current['version'],
        'contentRevision': current['contentRevision'],
        'quoteId': current['currentQuoteId'],
        'receivedCents': current['totalCents'],
        'settlementKind': 'CASH_MANUAL',
        'note': 'V1 验收人工收款',
    }
    settled = api(admin_client,
                  f'/admin/orders/{order["id"]}/confirm-payment',
                  settle_body, key='v1-intake-settle')
    assert settled.status_code == 200, settled.text
    paid = settled.json()['data']
    assert paid['status'] == 'PENDING_PRINT'
    assert paid['confirmedSlotId'] == seed.pipeline.slot.id
    assert paid['slotStatus'] == 'CONFIRMED'
    assert paid['actualStartedAt'] is None

    db.expire_all()
    slot = db.get(DeliverySlot, seed.pipeline.slot.id)
    assert slot.occupied == 1
    persisted = db.get(Order, order['id'])
    assert persisted.settlement_kind == 'CASH_MANUAL'
    assert persisted.cash_received_cents == paid['totalCents']

    # Settlement is idempotent: the same key/payload replays the original
    # result without a second payment row or a second capacity increment.
    payment_url = f'/admin/orders/{order["id"]}/confirm-payment'
    replay_payment = api(admin_client, payment_url, settle_body,
                         key='v1-intake-settle')
    assert replay_payment.status_code == 200, replay_payment.text
    assert replay_payment.json()['data']['id'] == paid['id']
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Payment)) == 1
    assert db.get(DeliverySlot, seed.pipeline.slot.id).occupied == 1

    # Same key with a different amount is a conflict, not a second charge.
    changed = dict(settle_body)
    changed['receivedCents'] = settle_body['receivedCents'] + 1
    changed_payment = api(admin_client, payment_url, changed,
                          key='v1-intake-settle')
    assert changed_payment.status_code == 409, changed_payment.text
    assert changed_payment.json()['error']['code'] == 'IDEMPOTENCY_CONFLICT'

    # A fresh key cannot settle an already-settled order; the unique payment
    # constraint and status check keep one payment for one order.
    duplicate = api(admin_client, payment_url, {
        'expectedVersion': paid['version'],
        'contentRevision': paid['contentRevision'],
        'quoteId': paid['currentQuoteId'],
        'receivedCents': paid['totalCents'],
        'settlementKind': 'CASH_MANUAL',
        'note': '',
    }, key='v1-intake-settle-duplicate')
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()['error']['code'] == 'INVALID_TRANSITION'
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Payment)) == 1
    assert db.get(DeliverySlot, seed.pipeline.slot.id).occupied == 1

    # Staff operation results are bound to the original staff session.  The
    # settling admin can replay the reference; another admin cannot read it.
    settle_ops_path = f'POST /api/v1/admin/orders/{order["id"]}/confirm-payment'
    own_ops = api(admin_client, '/operations/v1-intake-settle', method='GET',
                  params={'path': settle_ops_path})
    assert own_ops.status_code == 200, own_ops.text
    # confirm-payment returns an OrderView, so the generic safe reference
    # carries the order id under ``id`` (not ``orderId`` like create-order).
    assert own_ops.json()['data']['result'] == {
        'id': order['id'], 'status': 'PENDING_PRINT',
    }
    other_admin = staff_client(
        client_factory, db,
        staff_factory(role='ADMIN',
                      capabilities=['orders.view', 'payment.manage']),
    )
    foreign_ops = api(other_admin, '/operations/v1-intake-settle', method='GET',
                      params={'path': settle_ops_path})
    assert foreign_ops.status_code == 404, foreign_ops.text
    assert foreign_ops.json()['error']['code'] == 'NOT_FOUND'


# ---------------------------------------------------------------------------
# Acceptance: paid fulfilment, delivery proof, merchant rework + redelivery
# ---------------------------------------------------------------------------


def test_v1_paid_fulfilment_delivery_and_merchant_rework_redelivery(
        client, api, db, admin_client, staff_factory, client_factory):
    seed = prepare_order(client, api, db, client_factory, key_prefix='v1-flow')
    order = seed.order

    initial = admin_order(api, admin_client, order['id'])
    settled = settle(api, admin_client, initial, key='v1-flow-settle')
    assert settled.status_code == 200, settled.text
    paid = settled.json()['data']
    assert paid['status'] == 'PENDING_PRINT'
    assert paid['packingStatus'] == 'BLOCKED'

    run_print_to_packed(api, admin_client, paid)

    courier = staff_factory(role='COURIER', capabilities=[])
    courier_client = staff_client(client_factory, db, courier)

    batch = create_batch(api, admin_client, courier, 'V1 首轮配送')
    add_order_to_batch(api, admin_client, batch, order['id'], key='v1-flow-first')
    publish_and_start(api, admin_client, db, batch['id'], key='v1-flow-first')

    db.expire_all()
    assert db.get(Order, order['id']).status == 'DELIVERING'
    assignment = active_assignment(db, order['id'])
    assert assignment is not None and assignment.status == 'STARTED'

    proof_photo = upload_proof(courier_client, assignment)
    delivered = submit_proof(
        api, courier_client, order['id'], assignment,
        proof_photo['assetId'], key='v1-flow-first-proof',
    )
    assert delivered['order']['status'] == 'DELIVERED'
    assert delivered['proof']['assignmentId'] == assignment.id

    first_proof_id = delivered['proof']['id']
    db.expire_all()
    first_assignment_id = assignment.id
    assert db.get(DeliveryProof, first_proof_id) is not None

    # Merchant-responsibility reprint: admin records the case, approves it and
    # the real reprint worker path has to run again before re-packing.
    current = admin_order(api, admin_client, order['id'])
    case_response = api(
        admin_client, f'/admin/orders/{order["id"]}/after-sales', {
            'responsibility': 'MERCHANT',
            'solution': 'REPRINT',
            'reason': '验收：商家责任重做',
            'affectedItems': [],
            'expectedVersion': current['version'],
        }, key='v1-flow-case')
    assert case_response.status_code == 200, case_response.text
    case = case_response.json()['data']
    assert case['status'] == 'REQUESTED'

    # Customer detail strips staff identity and is owner/grant-only; an admin
    # must use the explicit admin route instead.
    customer_surface = api(admin_client, f'/after-sales/{case["id"]}', method='GET')
    assert customer_surface.status_code == 404, customer_surface.text
    admin_detail = api(admin_client, f'/admin/after-sales/{case["id"]}', method='GET')
    assert admin_detail.status_code == 200, admin_detail.text
    assert admin_detail.json()['data']['orderId'] == order['id']

    review = api(admin_client, f'/admin/after-sales/{case["id"]}/review', {
        'expectedVersion': case['version'],
        'decision': 'APPROVE',
        'solution': 'REPRINT',
        'adjustmentBasis': '商家责任重做',
        'reason': '验收通过重做申请',
    }, key='v1-flow-review')
    assert review.status_code == 200, review.text
    reviewed = review.json()['data']
    assert reviewed['status'] == 'REPRINTING'
    reprint_task_ids = reviewed['data']['reprintTaskIds']
    assert reprint_task_ids

    db.expire_all()
    rework_order = db.get(Order, order['id'])
    assert rework_order.status == 'PENDING_PRINT'
    assert rework_order.packing_status == 'BLOCKED'
    assert active_assignment(db, order['id']) is None

    reprint = api(admin_client, f'/admin/print-tasks/{reprint_task_ids[0]}/reprints', {
        'expectedVersion': admin_order(api, admin_client, order['id'])['version'],
        'mode': 'WHOLE',
        'reason': '验收：整单重做',
    }, key='v1-flow-reprint')
    assert reprint.status_code == 200, reprint.text
    reprint_attempt_id = reprint.json()['data']['attemptId']

    result = api(admin_client, f'/admin/print-attempts/{reprint_attempt_id}/result', {
        'expectedVersion': admin_order(api, admin_client, order['id'])['version'],
        'outcome': 'COMPLETED', 'reason': '',
    }, key='v1-flow-reprint-result')
    assert result.status_code == 200, result.text

    rebind = api(admin_client,
                 f'/admin/binding-tasks/{admin_order(api, admin_client, order["id"])["files"][0]["bindingTaskId"]}/complete',
                 {'expectedVersion': admin_order(api, admin_client, order['id'])['version']},
                 key='v1-flow-rebind')
    assert rebind.status_code == 200, rebind.text

    rework_current = admin_order(api, admin_client, order['id'])
    repacked = api(admin_client, f'/admin/orders/{order["id"]}/pack', {
        'expectedVersion': rework_current['version'],
        'checklistVersion': rework_current['packingChecklistVersion'],
        'checks': [{'fileId': rework_current['files'][0]['id'], 'confirmed': True}],
    }, key='v1-flow-repack')
    assert repacked.status_code == 200, repacked.text
    assert repacked.json()['data']['status'] == 'READY_FOR_DELIVERY'
    assert repacked.json()['data']['packingStatus'] == 'PACKED'

    case_current = api(admin_client, f'/admin/after-sales/{case["id"]}', method='GET')
    assert case_current.status_code == 200, case_current.text
    ready = api(admin_client, f'/admin/after-sales/{case["id"]}/reprint-ready', {
        'expectedVersion': case_current.json()['data']['version'],
        'reason': '验收：重做打包完成',
    }, key='v1-flow-ready')
    assert ready.status_code == 200, ready.text
    assert ready.json()['data']['status'] == 'REPRINT_READY'

    # A new batch and a new assignment are required; the first proof stays.
    second_batch = create_batch(api, admin_client, courier, 'V1 重做配送')
    add_order_to_batch(api, admin_client, second_batch, order['id'], key='v1-flow-second')
    publish_and_start(api, admin_client, db, second_batch['id'], key='v1-flow-second')
    second_assignment = active_assignment(db, order['id'])
    assert second_assignment is not None
    assert second_assignment.id != first_assignment_id

    second_photo = upload_proof(courier_client, second_assignment)
    redelivered = submit_proof(
        api, courier_client, order['id'], second_assignment,
        second_photo['assetId'], key='v1-flow-second-proof',
    )
    assert redelivered['order']['status'] == 'DELIVERED'

    db.expire_all()
    assert db.get(DeliveryProof, first_proof_id) is not None
    assert db.get(DeliveryAssignment, first_assignment_id).active is False
    assert db.get(DeliveryAssignment, second_assignment.id).status == 'DELIVERED'

    final_case = api(admin_client, f'/admin/after-sales/{case["id"]}', method='GET')
    completed = api(admin_client, f'/admin/after-sales/{case["id"]}/complete', {
        'expectedVersion': final_case.json()['data']['version'],
        'reason': '验收：重做订单二次交付完成',
    }, key='v1-flow-complete')
    assert completed.status_code == 200, completed.text
    assert completed.json()['data']['status'] == 'COMPLETED'

    final_order = admin_order(api, admin_client, order['id'])
    assert final_order['status'] == 'DELIVERED'
    assert final_order['proof'] is not None


def test_v1_concurrent_settlement_same_key_creates_one_payment(
        client, api, db, admin_client, client_factory):
    """Two simultaneous confirm-payment calls with one key settle only once.

    This exercises the real transaction boundary: the idempotency advisory lock
    must make the second request replay the committed result instead of
    creating a second payment row or consuming slot capacity twice.
    """
    seed = prepare_order(client, api, db, client_factory, key_prefix='v1-race')
    order = seed.order
    current = admin_order(api, admin_client, order['id'])
    body = {
        'expectedVersion': current['version'],
        'contentRevision': current['contentRevision'],
        'quoteId': current['currentQuoteId'],
        'receivedCents': current['totalCents'],
        'settlementKind': 'CASH_MANUAL',
        'note': 'V1 并发幂等验收',
    }

    token = admin_client.cookies.get('dormprint_account_session')
    assert token
    racers = []
    for _ in range(2):
        racer = client_factory()
        racer.cookies.set('dormprint_account_session', token)
        # A read response issues the CSRF cookie used by the write headers.
        racer.get('/api/v1/health/live')
        racers.append(racer)

    barrier = threading.Barrier(2)
    url = f'/admin/orders/{order["id"]}/confirm-payment'

    def settle_once(racer):
        barrier.wait(timeout=10)
        return api(racer, url, body, key='v1-race-settle')

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(settle_once, racers))

    assert [response.status_code for response in responses] == [200, 200], [
        response.text for response in responses
    ]
    assert {response.json()['data']['id'] for response in responses} == {order['id']}

    db.expire_all()
    from app.models import Order, Payment
    from sqlalchemy import func, select
    assert db.scalar(select(func.count()).select_from(Payment)) == 1
    assert db.get(DeliverySlot, seed.pipeline.slot.id).occupied == 1
    assert db.get(Order, order['id']).settlement_kind == 'CASH_MANUAL'


def test_v1_unified_account_grant_effective_staff_and_legacy_cookie_isolation(
        client, api, db, client_factory, staff_factory):
    """FINAL N05: one customer account, effectiveStaff from its own cookie.

    A superadmin grants an ordinary verified account admin capabilities.  The
    very same account session, without re-login, must then act as effective
    staff.  Raw STAFF AuthSession cookies were retired by FINAL N05: they must
    grant no identity, and must not elevate a different logged-in account.
    """
    from auth_helpers import PASSWORD, login_customer, make_account, make_admin

    seed = prepare_order(client, api, db, client_factory, key_prefix='v1-n05')
    order = seed.order

    account_a = make_account(db, email='n05-effective@example.invalid',
                             display_name='N05 普通账号')
    account_b = make_account(db, email='n05-customer@example.invalid',
                             display_name='N05 客户账号')
    super_email = 'n05-super@example.invalid'
    super_staff = make_admin(db, email=super_email, name='N05 超级管理员')
    db.commit()
    assert super_staff.account_id and super_staff.is_super_admin is True

    # FINAL N05 removed raw STAFF-session authentication.  Even a valid legacy
    # STAFF row/session must grant nothing unless presented through its linked
    # customer account cookie.
    raw_staff = staff_factory(role='ADMIN', capabilities=['orders.view'])
    raw_token = secrets.token_urlsafe(32)
    db.add(AuthSession(
        kind='STAFF', staff_id=raw_staff.id, token_hash=token_hash(raw_token),
        expires_at=utcnow() + timedelta(days=7),
    ))
    db.commit()
    raw_client = client_factory()
    raw_client.cookies.set('dormprint_staff_session', raw_token)
    assert api(raw_client, '/auth/me', method='GET').status_code == 401
    assert api(raw_client, '/admin/orders', method='GET').status_code == 401

    # The superadmin logs in through the normal customer-account login route.
    super_client = client_factory()
    assert login_customer(super_client, super_email).status_code == 200
    super_me = api(super_client, '/auth/me', method='GET')
    assert super_me.status_code == 200, super_me.text
    super_data = super_me.json()['data']
    assert super_data['accountId'] == super_staff.account_id
    assert super_data['role'] == 'ADMIN'
    assert super_data['isSuperAdmin'] is True

    # Before the grant A is a plain customer and has no effective staff power.
    a_client = client_factory()
    assert login_customer(a_client, account_a.email).status_code == 200
    customer_me = api(a_client, '/customer/auth/me', method='GET')
    assert customer_me.status_code == 200, customer_me.text
    customer_data = customer_me.json()['data']
    assert customer_data['role'] == 'CUSTOMER'
    assert customer_data['authKind'] == 'REGISTERED'
    assert customer_data['account']['id'] == account_a.id
    assert api(a_client, '/auth/me', method='GET').status_code == 401
    assert api(a_client, f'/admin/orders/{order["id"]}',
               method='GET').status_code == 401

    # Staff grants never create a separate employee password.
    password_grant = api(super_client, '/admin/staff', {
        'accountId': account_b.id,
        'role': 'ADMIN',
        'capabilities': ['orders.view'],
        'password': PASSWORD,
        'reason': 'N05 不应设置员工独立密码',
    }, key='v1-n05-password-grant')
    assert password_grant.status_code == 422, password_grant.text
    assert password_grant.json()['error']['code'] == 'STAFF_PASSWORD_NOT_ALLOWED'

    # The only superadmin grants A's capabilities on A's existing account.
    granted = api(super_client, '/admin/staff', {
        'accountId': account_a.id,
        'role': 'ADMIN',
        'capabilities': ['orders.view', 'payment.manage', 'production.manage'],
        'reason': 'N05 统一账号员工授权验收',
    }, key='v1-n05-grant')
    assert granted.status_code == 200, granted.text
    granted_item = next(
        item for item in granted.json()['data']['items']
        if item['accountId'] == account_a.id
    )
    assert granted_item['role'] == 'ADMIN'
    assert granted_item['isSuperAdmin'] is False
    assert 'payment.manage' in granted_item['capabilities']

    db.expire_all()
    staff_a = db.scalar(select(Staff).where(Staff.account_id == account_a.id))
    assert staff_a is not None
    assert staff_a.password_hash == ''
    assert staff_a.is_super_admin is False
    assert db.scalar(select(func.count()).select_from(Staff).where(
        Staff.account_id == account_b.id)) == 0

    # Account A did not re-login: the existing account cookie now carries the
    # effective staff identity because authority is derived from the account.
    me_a = api(a_client, '/auth/me', method='GET')
    assert me_a.status_code == 200, me_a.text
    me_a_data = me_a.json()['data']
    assert me_a_data['id'] == staff_a.id
    assert me_a_data['accountId'] == account_a.id
    assert me_a_data['role'] == 'ADMIN'
    assert me_a_data['isSuperAdmin'] is False
    assert 'payment.manage' in me_a_data['capabilities']
    still_customer = api(a_client, '/customer/auth/me', method='GET').json()['data']
    assert still_customer['role'] == 'CUSTOMER'
    assert still_customer['account']['id'] == account_a.id

    # Effective staff can actually settle the guest order through admin routes.
    current = admin_order(api, a_client, order['id'])
    settled = settle(api, a_client, current, 'v1-n05-settle')
    assert settled.status_code == 200, settled.text
    assert settled.json()['data']['status'] == 'PENDING_PRINT'
    db.expire_all()
    assert db.get(Order, order['id']).status == 'PENDING_PRINT'
    assert db.get(DeliverySlot, seed.pipeline.slot.id).occupied == 1
    assert db.scalar(select(func.count()).select_from(Payment)) == 1

    # Account uniqueness is enforced by the account_id grant boundary.
    duplicate = api(super_client, '/admin/staff', {
        'accountId': account_a.id,
        'role': 'ADMIN',
        'capabilities': ['orders.view'],
        'reason': 'N05 重复授权拒绝',
    }, key='v1-n05-duplicate')
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()['error']['code'] == 'STAFF_ACCOUNT_ALREADY_GRANTED'

    # A non-superadmin administrator cannot grant staff permissions.
    denied = api(a_client, '/admin/staff', {
        'accountId': account_b.id,
        'role': 'ADMIN',
        'capabilities': ['orders.view'],
        'reason': 'N05 普通管理员越权',
    }, key='v1-n05-denied-grant')
    assert denied.status_code == 403, denied.text
    assert denied.json()['error']['code'] == 'FORBIDDEN'

    # A verified account without a staff grant remains a base customer.
    b_client = client_factory()
    assert login_customer(b_client, account_b.email).status_code == 200
    assert api(b_client, '/auth/me', method='GET').status_code == 401
    assert api(b_client, '/admin/orders', method='GET').status_code == 401
    assert api(b_client, '/customer/orders', method='GET').status_code == 200

    # Even if an old standalone STAFF-session cookie is present, it is ignored:
    # B must not inherit that unrelated staff's authority.
    mixed = client_factory()
    assert login_customer(mixed, account_b.email).status_code == 200
    mixed.cookies.set('dormprint_staff_session', raw_token)
    assert api(mixed, '/auth/me', method='GET').status_code == 401
    assert api(mixed, '/admin/orders', method='GET').status_code == 401
