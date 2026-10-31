"""Media module tests: strict DTOs first, real PostgreSQL/routes second.

The integration tests upload real PDF/Office/HEIC bytes, run the actual worker
processors against PostgreSQL and verify byte-level artifacts, paper maths,
quotes, cancellation and resumable upload semantics. They never accept a fake
"READY" or a mock conversion.
"""
from __future__ import annotations

import hashlib
import io
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

import pytest
from pydantic import ValidationError

pymupdf = pytest.importorskip('pymupdf')


# ---------------------------------------------------------------------------
# DTO / domain tests (no database)
# ---------------------------------------------------------------------------


def test_page_range_and_segment_dto_are_strict():
    from app.media_schemas import PageRange, PreparationInput, SegmentInput

    page = PageRange(**{'from': 2, 'to': 5})
    assert (page.from_page, page.to_page) == (2, 5)
    assert page.model_dump(by_alias=True) == {'from': 2, 'to': 5}

    with pytest.raises(ValidationError):
        PageRange(**{'from': 5, 'to': 2})
    base = dict(
        clientSegmentId='seg-1', ranges=[{'from': 1, 'to': 2}], colorMode='BW',
        sides='SINGLE', flip='NONE', copies=1,
    )
    SegmentInput.model_validate(base)
    for patch in (
        {'flip': 'LONG_EDGE'},   # single-sided cannot flip
        {'sides': 'DUPLEX'},     # duplex must declare a flip
        {'copies': 0},
        {'colorMode': 'GRAY'},
    ):
        with pytest.raises(ValidationError):
            SegmentInput.model_validate({**base, **patch})
    with pytest.raises(ValidationError):
        SegmentInput.model_validate({**base, 'extra': 1})


def test_preparation_input_photo_crop_and_binding_validation():
    from app.media_schemas import PreparationInput

    document = {
        'files': [{
            'clientFileId': 'f1', 'assetId': 'a1', 'kind': 'DOCUMENT',
            'segments': [
                {'clientSegmentId': 's1', 'ranges': [{'from': 1, 'to': 2}],
                 'colorMode': 'BW', 'sides': 'SINGLE', 'flip': 'NONE', 'copies': 2},
                {'clientSegmentId': 's2', 'ranges': [{'from': 3, 'to': 4}],
                 'colorMode': 'BW', 'sides': 'SINGLE', 'flip': 'NONE', 'copies': 1},
            ],
            'excludedPages': [], 'bindingType': 'CORNER',
        }],
    }
    # Q08: the same file's segments must share copies before it can be bound.
    with pytest.raises(ValidationError):
        PreparationInput.model_validate(document)
    document['files'][0]['bindingType'] = 'NONE'
    PreparationInput.model_validate(document)

    photo = {
        'clientFileId': 'p1', 'assetId': 'a2', 'kind': 'PHOTO', 'copies': 2,
        'orientation': 'LANDSCAPE', 'fit': 'COVER', 'rotationDegrees': 90,
        'crop': None,
    }
    with pytest.raises(ValidationError):
        PreparationInput.model_validate({'files': [photo]})
    photo['crop'] = {'x': 0.1, 'y': 0.1, 'width': 0.8, 'height': 0.8}
    PreparationInput.model_validate({'files': [photo]})
    photo['fit'] = 'CONTAIN'
    with pytest.raises(ValidationError):
        PreparationInput.model_validate({'files': [photo]})
    photo['crop'] = None
    PreparationInput.model_validate({'files': [photo]})


def test_upload_session_dto_rejects_unsafe_values():
    from app.media_schemas import UploadSessionComplete, UploadSessionCreate

    UploadSessionCreate(
        name='thesis.pdf', purpose='PRINT_SOURCE', totalBytes=1024,
        sha256='a' * 64,
    )
    for payload in (
        {'name': 'x', 'totalBytes': 0, 'sha256': 'a' * 64},
        {'name': 'x', 'totalBytes': 1024, 'sha256': 'A' * 64},
        {'name': 'x', 'totalBytes': 1024, 'sha256': 'a' * 63},
        {'name': 'x', 'totalBytes': 1024, 'sha256': 'a' * 64, 'purpose': 'PRIVATE_HACK'},
    ):
        with pytest.raises(ValidationError):
            UploadSessionCreate.model_validate(payload)
    UploadSessionComplete.model_validate({})
    with pytest.raises(ValidationError):
        UploadSessionComplete.model_validate({'sha256': 'nope'})


# ---------------------------------------------------------------------------
# Integration helpers
# ---------------------------------------------------------------------------


PRICES = {
    'bwSingle': 10, 'bwDuplex': 8, 'colorSingle': 20, 'colorDuplex': 16,
    'photo': 100, 'corner': 200, 'side': 300,
}
SERVICES = {
    'serviceEnabled': True, 'maxFiles': 10, 'maxMb': 20, 'maxOrderMb': 60,
    'maxPages': 100, 'maxCopies': 20, 'maxPixels': 40_000_000,
    'maxExpandedPages': 500, 'maxFinalMb': 60, 'bindingApproved': True,
    'bindingLimits': {'CORNER': None, 'SIDE': None},
    'formats': ['pdf', 'doc', 'docx', 'ppt', 'pptx', 'xls', 'xlsx', 'jpg', 'jpeg', 'png', 'webp', 'heic'],
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
    'unpaidTimeoutMinutes': None, 'quoteMinutes': 15, 'locationMaxAgeSeconds': 300,
    'locationMaxAccuracyMeters': 100, 'shortCodeLength': 5,
    'pauses': {'newOrders': False, 'fileProcessing': False, 'production': False, 'delivery': False},
}


def seed_config(db, *, prices=None, services=None, photo=None, retention=None, workflow=None):
    from app.models import ConfigSection

    db.add_all([
        ConfigSection(section='prices', data=dict(prices or PRICES)),
        ConfigSection(section='services', data=dict(services or SERVICES)),
        ConfigSection(section='photo', data=dict(photo or PHOTO)),
        ConfigSection(section='retention', data=dict(retention or RETENTION)),
        ConfigSection(section='workflow', data=dict(workflow or WORKFLOW)),
    ])
    db.commit()


def make_verified_account(db, email=None):
    from app.models import Account, Principal

    principal = Principal()
    db.add(principal)
    db.flush()
    account = Account(
        principal_id=principal.id,
        email=email or f'{secrets.token_hex(6)}@example.test',
        password_hash='testing-only',
        status='ACTIVE',
        verified_at=__import__('app.models', fromlist=['utcnow']).utcnow(),
        invite_code=secrets.token_hex(8),
    )
    db.add(account)
    db.flush()
    principal.account_id = account.id
    db.commit()
    return account


def login_account(client, db, account):
    from datetime import timedelta

    from app.core import token_hash
    from app.models import AuthSession, utcnow

    token = secrets.token_urlsafe(32)
    db.add(AuthSession(
        kind='CUSTOMER', account_id=account.id, principal_id=account.principal_id,
        token_hash=token_hash(token), expires_at=utcnow() + timedelta(days=30),
    ))
    db.commit()
    client.cookies.set('dormprint_account_session', token)
    return client


def make_pending_order(db, principal, *, checkout_account=None, auth_kind='ANONYMOUS'):
    from datetime import timedelta
    from types import SimpleNamespace

    from app.models import DeliverySlot, Location, Order, Preparation, Quote, utcnow

    now = utcnow()
    preparation = Preparation(principal_id=principal.id, status='READY',
                              checkout_account_id=checkout_account.id if checkout_account else None,
                              content_revision=1, expires_at=now + timedelta(hours=2), data={})
    location = Location(name='东区 1 栋')
    slot = DeliverySlot(date='2026-10-31', name='19:00', starts_at=now,
                        ends_at=now + timedelta(hours=1), capacity=10)
    db.add_all([preparation, location, slot])
    db.flush()
    quote = Quote(preparation_id=preparation.id, price_version=1,
                  expires_at=now + timedelta(minutes=15), subtotal_cents=0,
                  discount_cents=0, total_cents=0,
                  checkout_account_id=checkout_account.id if checkout_account else None)
    db.add(quote)
    db.flush()
    order = Order(
        principal_id=principal.id,
        checkout_account_id=checkout_account.id if checkout_account else None,
        pickup_code=secrets.token_hex(4).upper(), status='PENDING_CONFIRMATION',
        preparation_id=preparation.id, quote_id=quote.id, total_cents=0,
        nickname='同学', phone='13800000000', location_id=location.id,
        requested_slot_id=slot.id, close_at=now + timedelta(hours=24),
        data={'checkoutAuthKind': auth_kind},
    )
    db.add(order)
    db.commit()
    return SimpleNamespace(order=order, preparation=preparation, quote=quote,
                           location=location, slot=slot)


def guest(client):
    # First customer bootstrap may omit CSRF, but still needs an operation key.
    response = client.post(
        '/api/v1/customer/session',
        json={},
        headers={'Origin': 'http://testserver', 'Idempotency-Key': secrets.token_urlsafe(24)},
    )
    assert response.status_code == 200, response.text
    return client


def _headers(client, key=None):
    return {
        'Origin': 'http://testserver',
        'Idempotency-Key': key or secrets.token_urlsafe(24),
        'X-CSRF-Token': unquote(client.cookies.get('csrfToken', '')),
    }


def upload(client, payload: bytes, name='document.pdf', mime='application/pdf', purpose='PRINT_SOURCE', key=None, **extra):
    data = {'purpose': purpose, **{key_: value for key_, value in extra.items() if value is not None}}
    return client.post(
        '/api/v1/assets',
        files={'file': (name, payload, mime)},
        data=data,
        headers=_headers(client, key),
    )


def json_write(client, path, body, method='POST', key=None):
    return client.request(
        method,
        path,
        json=body,
        headers=_headers(client, key),
    )


def make_pdf(pages=2):
    doc = pymupdf.open()
    for index in range(pages):
        page = doc.new_page(width=595.276, height=841.89)
        page.insert_text((60, 80), f'Integration page {index + 1}', fontsize=18)
        page.draw_rect(pymupdf.Rect(40, 40, 555, 800), color=(0.8, 0.8, 0.8), width=1)
    payload = doc.tobytes()
    doc.close()
    return payload


def process_asset(db, asset_id):
    from app import media

    media.process_asset(db, asset_id)
    # The worker owns a separate committed transaction (CONTRACT.md).
    db.expire_all()


def process_preparation(db, preparation_id):
    from app import media

    media.process_preparation(db, preparation_id)
    db.expire_all()


def preparation_payload(asset_id, pages, *, segments=None, binding='NONE', excluded=None):
    segments = segments or [{
        'clientSegmentId': 'seg-1',
        'ranges': [{'from': 1, 'to': pages}],
        'colorMode': 'BW', 'sides': 'DUPLEX', 'flip': 'LONG_EDGE', 'copies': 1,
    }]
    return {
        'files': [{
            'clientFileId': 'file-1', 'assetId': asset_id, 'kind': 'DOCUMENT',
            'segments': segments, 'excludedPages': excluded or [], 'bindingType': binding,
        }],
        'promotionIntent': {'selectionMode': 'AUTO'},
    }


# ---------------------------------------------------------------------------
# Asset lifecycle / range / privacy
# ---------------------------------------------------------------------------


def test_asset_upload_worker_status_range_and_private_isolation(client, client_factory, db, admin_client):
    seed_config(db)
    guest(client)
    payload = make_pdf(2)
    response = upload(client, payload, 'private.pdf')
    assert response.status_code == 200, response.text
    result = response.json()['data']
    asset_id = result['assetId']
    assert result['status'] == 'QUEUED' and result['pages'] is None

    detail = json_write(client, f'/api/v1/assets/{asset_id}', None, method='GET')
    assert detail.status_code == 200
    assert detail.json()['data']['status'] == 'QUEUED'

    process_asset(db, asset_id)
    detail = json_write(client, f'/api/v1/assets/{asset_id}', None, method='GET').json()['data']
    assert detail['status'] == 'READY'
    assert detail['pages'] == 2
    assert detail['kind'] == 'PDF'
    assert detail['normalizedAssetId']
    assert detail['sha256'] == hashlib.sha256(payload).hexdigest()

    content = client.get(f'/api/v1/assets/{asset_id}/content')
    assert content.status_code == 200
    assert content.content == payload
    assert content.headers['accept-ranges'] == 'bytes'
    assert content.headers['content-type'].startswith('application/pdf')

    ranged = client.get(f'/api/v1/assets/{asset_id}/content', headers={'Range': 'bytes=10-109'})
    assert ranged.status_code == 206
    assert ranged.content == payload[10:110]
    assert ranged.headers['content-range'] == f'bytes 10-109/{len(payload)}'
    assert ranged.headers['content-length'] == '100'

    suffix = client.get(f'/api/v1/assets/{asset_id}/content', headers={'Range': 'bytes=-20'})
    assert suffix.status_code == 206 and suffix.content == payload[-20:]
    unsatisfiable = client.get(
        f'/api/v1/assets/{asset_id}/content', headers={'Range': f'bytes={len(payload)}-'}
    )
    assert unsatisfiable.status_code == 416

    retry = json_write(client, f'/api/v1/assets/{asset_id}/retry', {})
    assert retry.status_code == 409

    other = guest(client_factory())
    assert json_write(other, f'/api/v1/assets/{asset_id}', None, method='GET').status_code == 404
    assert other.get(f'/api/v1/assets/{asset_id}/content').status_code == 404

    # Admin may review a private asset, but a guest cannot mint a public QR.
    assert json_write(admin_client, f'/api/v1/assets/{asset_id}', None, method='GET').status_code == 200
    png = io.BytesIO()
    from PIL import Image

    Image.new('RGB', (24, 24), 'white').save(png, format='PNG')
    forbidden = upload(client, png.getvalue(), 'qr.png', 'image/png', purpose='PUBLIC_WECHAT_QR')
    assert forbidden.status_code in (401, 403)
    public = upload(admin_client, png.getvalue(), 'qr.png', 'image/png', purpose='PUBLIC_WECHAT_QR')
    assert public.status_code == 200, public.text
    public_id = public.json()['data']['assetId']
    process_asset(db, public_id)
    # Public QR content is readable by any visitor, but private content is not.
    assert other.get(f'/api/v1/assets/{public_id}/content').status_code == 200

    drafts = json_write(client, '/api/v1/assets', None, method='GET').json()['data']
    assert any(item['assetId'] == asset_id for item in drafts['items'])
    assert all(item['assetId'] != public_id for item in drafts['items'])


def test_multipart_idempotency_hashes_semantic_file_not_boundary(client, db):
    seed_config(db)
    guest(client)
    payload = make_pdf(1)
    key = 'multipart-semantic-key-1'
    first = upload(client, payload, 'same.pdf', key=key)
    assert first.status_code == 200, first.text
    first_id = first.json()['data']['assetId']
    # requests rebuilds a different multipart boundary every time; replay must
    # still recognize the same semantic file and key.
    second = upload(client, payload, 'same.pdf', key=key)
    assert second.status_code == 200
    assert second.json()['data']['assetId'] == first_id
    from app.models import Asset
    from sqlalchemy import select

    assert len(list(db.scalars(select(Asset).where(Asset.purpose == 'PRINT_SOURCE')))) == 1

    changed = upload(client, make_pdf(1) + b'%changed', 'same.pdf', key=key)
    assert changed.status_code == 409
    assert changed.json()['error']['code'] == 'IDEMPOTENCY_CONFLICT'


def test_resumable_upload_chunks_hash_merge_and_idempotent_completion(
    client, client_factory, db
):
    seed_config(db)
    guest(client)
    payload = make_pdf(2)
    digest = hashlib.sha256(payload).hexdigest()
    split = len(payload) // 2
    first_chunk, second_chunk = payload[:split], payload[split:]
    create = json_write(
        client,
        '/api/v1/upload-sessions',
        {'name': 'resume.pdf', 'purpose': 'PRINT_SOURCE', 'totalBytes': len(payload), 'sha256': digest},
    )
    assert create.status_code == 200, create.text
    session = create.json()['data']
    session_id = session['id']
    assert session['receivedBytes'] == 0 and session['nextOffset'] == 0

    def put_chunk(chunk, offset, key=None, target=None):
        return (target or client).request(
            'PUT',
            f'/api/v1/upload-sessions/{session_id}/chunks',
            params={'offset': offset},
            content=chunk,
            headers={**_headers(client), 'Content-Type': 'application/octet-stream',
                     'X-Chunk-Sha256': hashlib.sha256(chunk).hexdigest()},
        )

    assert put_chunk(first_chunk, 0).status_code == 200
    replay = put_chunk(first_chunk, 0)
    assert replay.status_code == 200
    assert replay.json()['data']['receivedBytes'] == split
    gap = put_chunk(second_chunk, split + 1)
    assert gap.status_code == 409
    assert gap.json()['error']['code'] == 'UPLOAD_OFFSET_GAP'

    other = guest(client_factory())
    assert other.get(f'/api/v1/upload-sessions/{session_id}').status_code == 404
    other_response = other.request(
        'PUT', f'/api/v1/upload-sessions/{session_id}/chunks',
        params={'offset': split}, content=second_chunk,
        headers={**_headers(other), 'Content-Type': 'application/octet-stream'},
    )
    assert other_response.status_code == 404, other_response.text

    assert put_chunk(second_chunk, split).status_code == 200
    view = client.get(f'/api/v1/upload-sessions/{session_id}').json()['data']
    assert view['receivedBytes'] == len(payload)
    assert view['ranges'] == [{'start': 0, 'end': len(payload) - 1}]

    complete = json_write(client, f'/api/v1/upload-sessions/{session_id}/complete', {'sha256': digest})
    assert complete.status_code == 200, complete.text
    asset_id = complete.json()['data']['assetId']
    replay_complete = json_write(client, f'/api/v1/upload-sessions/{session_id}/complete', {})
    assert replay_complete.status_code == 200
    assert replay_complete.json()['data']['assetId'] == asset_id
    from app.models import Asset
    from sqlalchemy import func, select

    assert db.scalar(select(func.count()).select_from(Asset).where(Asset.sha256 == digest)) == 1
    process_asset(db, asset_id)
    assert client.get(f'/api/v1/assets/{asset_id}/content').content == payload

    broken = json_write(
        client,
        '/api/v1/upload-sessions',
        {'name': 'broken.pdf', 'purpose': 'PRINT_SOURCE',
         'totalBytes': len(payload), 'sha256': 'b' * 64},
    ).json()['data']
    broken_id = broken['id']
    client.request(
        'PUT', f'/api/v1/upload-sessions/{broken_id}/chunks', params={'offset': 0},
        content=payload,
        headers={**_headers(client), 'Content-Type': 'application/octet-stream'},
    )
    mismatch = json_write(client, f'/api/v1/upload-sessions/{broken_id}/complete', {})
    assert mismatch.status_code == 409
    assert mismatch.json()['error']['code'] == 'UPLOAD_HASH_MISMATCH'

    cancel = json_write(client, f'/api/v1/upload-sessions/{session_id}', {}, method='DELETE')
    assert cancel.status_code == 200
    assert cancel.json()['data']['status'] == 'COMPLETED'  # real final state, not faked cancel
    fresh = json_write(
        client, '/api/v1/upload-sessions',
        {'name': 'cancel.pdf', 'purpose': 'PRINT_SOURCE', 'totalBytes': 10, 'sha256': 'c' * 64},
    ).json()['data']
    cancelled = json_write(client, f"/api/v1/upload-sessions/{fresh['id']}", {}, method='DELETE')
    assert cancelled.status_code == 200
    assert cancelled.json()['data']['status'] == 'CANCELLED'


# ---------------------------------------------------------------------------
# Preparation / artifacts / quote / requote
# ---------------------------------------------------------------------------


def test_preparation_generates_real_artifacts_quote_and_requote(client, db):
    from app.models import Artifact, Quote, Preparation

    seed_config(db)
    guest(client)
    asset_id = upload(client, make_pdf(7), 'seven.pdf').json()['data']['assetId']
    process_asset(db, asset_id)
    body = {
        'files': [{
            'clientFileId': 'file-1', 'assetId': asset_id, 'kind': 'DOCUMENT',
            'segments': [
                {'clientSegmentId': 'seg-bw', 'ranges': [{'from': 1, 'to': 3}],
                 'colorMode': 'BW', 'sides': 'DUPLEX', 'flip': 'LONG_EDGE', 'copies': 2},
                {'clientSegmentId': 'seg-color', 'ranges': [{'from': 4, 'to': 7}],
                 'colorMode': 'COLOR', 'sides': 'SINGLE', 'flip': 'NONE', 'copies': 1},
            ],
            'excludedPages': [], 'bindingType': 'NONE',
        }],
        'promotionIntent': {'selectionMode': 'AUTO'},
    }
    created = json_write(client, '/api/v1/preparations', body)
    assert created.status_code == 200, created.text
    prep = created.json()['data']
    assert prep['status'] == 'QUEUED' and prep['artifacts'] == [] and prep['quote'] is None
    prep_id = prep['id']

    process_preparation(db, prep_id)
    view = json_write(client, f'/api/v1/preparations/{prep_id}', None, method='GET').json()['data']
    assert view['status'] == 'READY', view
    assert len(view['artifacts']) == 2
    by_segment = {artifact['segmentId']: artifact for artifact in view['artifacts']}
    bw, color = by_segment['seg-bw'], by_segment['seg-color']
    assert (bw['pages'], bw['sheets'], bw['blankBacks']) == (8, 4, 2)
    assert (color['pages'], color['sheets'], color['blankBacks']) == (4, 4, 0)
    assert bw['copies'] == 2 and bw['color'] == 'BW'
    assert bw['sides'] == 'DUPLEX' and bw['flip'] == 'LONG_EDGE'
    assert len(bw['sha256']) == 64 and len(bw['settingsHash']) == 64
    assert bw['url'].startswith('/api/v1/assets/') and bw['url'].endswith('/content')

    # Real bytes behind the preview URL, with the declared page/sheet counts.
    for artifact in (bw, color):
        content = client.get(artifact['url'])
        assert content.status_code == 200
        document = pymupdf.open(stream=content.content, filetype='pdf')
        assert document.page_count == artifact['pages']
        assert hashlib.sha256(content.content).hexdigest() == artifact['sha256']
        assert document[0].rect.width == pytest.approx(595.276, abs=0.5)
    # The production UI addresses final files by artifact id; the content route
    # must resolve that id without weakening authorization.
    by_artifact = client.get(f"/api/v1/assets/{bw['id']}/content")
    assert by_artifact.status_code == 200
    assert hashlib.sha256(by_artifact.content).hexdigest() == bw['sha256']
    bw_document = pymupdf.open(stream=client.get(bw['url']).content, filetype='pdf')
    assert bw_document[3].get_text().strip() == ''   # real blank back
    assert 'Integration page 1' in bw_document[0].get_text()
    assert 'Integration page 3' in bw_document[2].get_text()
    color_document = pymupdf.open(stream=client.get(color['url']).content, filetype='pdf')
    assert 'Integration page 4' in color_document[0].get_text()

    quote = view['quote']
    assert quote['subtotalCents'] == 8 * 4 + 20 * 4
    assert quote['totalCents'] == quote['subtotalCents'] - quote['discountCents']
    assert {line['service'] for line in quote['lines']} == {'A4'}
    assert view['validation']['ok'] is True
    assert view['files'][0]['clientFileId'] == 'file-1'

    drafts = json_write(client, '/api/v1/preparations', None, method='GET').json()['data']
    assert any(item['id'] == prep_id for item in drafts['items'])

    retry = json_write(client, f'/api/v1/preparations/{prep_id}/retry', {})
    assert retry.status_code == 409

    # Requote after a price version change: same immutable PDF, new quote.
    from app.models import ConfigSection
    from sqlalchemy import select as sql_select

    prices = db.scalar(sql_select(ConfigSection).where(ConfigSection.section == 'prices'))
    prices.data = {**prices.data, 'bwDuplex': 12}
    prices.version += 1
    db.commit()
    old_quote_id = view['quote']['id']
    requoted = json_write(
        client, f'/api/v1/preparations/{prep_id}/requote',
        {'promotionIntent': {'selectionMode': 'NO_COUPON'}},
    )
    assert requoted.status_code == 200, requoted.text
    new_quote = requoted.json()['data']
    assert new_quote['id'] != old_quote_id
    assert new_quote['subtotalCents'] == 12 * 4 + 20 * 4
    fresh = json_write(client, f'/api/v1/preparations/{prep_id}', None, method='GET').json()['data']
    assert [artifact['id'] for artifact in fresh['artifacts']] == [
        artifact['id'] for artifact in view['artifacts']
    ]
    assert [artifact['sha256'] for artifact in fresh['artifacts']] == [
        artifact['sha256'] for artifact in view['artifacts']
    ]
    assert len(list(db.scalars(sql_select(Quote).where(Quote.preparation_id == prep_id)))) == 2


def test_photo_preparation_uses_calibrated_millimetres_and_exif(client, db):
    from PIL import Image

    seed_config(db)
    guest(client)
    image = Image.new('RGB', (400, 300), (210, 40, 40))
    exif = image.getexif()
    exif[274] = 6
    buffer = io.BytesIO()
    image.save(buffer, format='JPEG', exif=exif)
    asset_id = upload(client, buffer.getvalue(), 'photo.jpg', 'image/jpeg').json()['data']['assetId']
    process_asset(db, asset_id)
    detail = json_write(client, f'/api/v1/assets/{asset_id}', None, method='GET').json()['data']
    assert detail['status'] == 'READY' and detail['pages'] == 1
    assert detail['dimensions'] == {'width': 300, 'height': 400, 'exifOrientation': 6}
    # Draft recovery needs a browser-displayable derivative, not the raw JPEG.
    assert detail['normalizedUrl'] == f"/api/v1/assets/{detail['normalizedAssetId']}/content"
    preview = client.get(detail['normalizedUrl'])
    assert preview.status_code == 200
    assert preview.headers['content-type'].startswith(('image/png', 'image/jpeg'))
    from PIL import Image

    with Image.open(io.BytesIO(preview.content)) as normalized:
        assert normalized.size == (300, 400)

    body = {
        'files': [{
            'clientFileId': 'photo-1', 'assetId': asset_id, 'kind': 'PHOTO',
            'copies': 2, 'orientation': 'LANDSCAPE', 'fit': 'CONTAIN',
            'rotationDegrees': 0, 'crop': None,
        }],
        'promotionIntent': {'selectionMode': 'AUTO'},
    }
    prep_id = json_write(client, '/api/v1/preparations', body).json()['data']['id']
    process_preparation(db, prep_id)
    view = json_write(client, f'/api/v1/preparations/{prep_id}', None, method='GET').json()['data']
    assert view['status'] == 'READY'
    artifact = view['artifacts'][0]
    assert (artifact['pages'], artifact['sheets'], artifact['blankBacks']) == (2, 2, 0)
    assert artifact['color'] == 'COLOR' and artifact['sides'] == 'SINGLE'
    assert artifact['copies'] == 2
    assert view['quote']['lines'][0]['service'] == 'PHOTO'
    assert view['quote']['totalCents'] == 200
    content = client.get(artifact['url']).content
    document = pymupdf.open(stream=content, filetype='pdf')
    assert document.page_count == 2
    assert document[0].rect.width == pytest.approx(127 / 25.4 * 72, abs=0.4)
    assert document[0].rect.height == pytest.approx(89 / 25.4 * 72, abs=0.4)
    settings = artifact['pageMap'][0]
    assert settings['copyIndex'] == 1 and settings['side'] == 'FRONT'


def test_heic_source_is_really_decoded_for_photo_printing(client, client_factory, db):
    from PIL import Image
    from pillow_heif import register_heif_opener

    register_heif_opener()
    seed_config(db)
    guest(client)
    buffer = io.BytesIO()
    Image.new('RGB', (120, 90), (20, 90, 220)).save(buffer, format='HEIF')
    heic = buffer.getvalue()
    created = upload(client, heic, 'camera.heic', 'image/heic')
    assert created.status_code == 200, created.text
    assert created.json()['data']['kind'] == 'HEIC'
    asset_id = created.json()['data']['assetId']
    process_asset(db, asset_id)
    detail = json_write(client, f'/api/v1/assets/{asset_id}', None, method='GET').json()['data']
    assert detail['status'] == 'READY'
    # Browsers cannot decode HEIC; the server must expose a real authorized PNG.
    assert detail['normalizedUrl'] == f"/api/v1/assets/{detail['normalizedAssetId']}/content"
    assert detail['previewUrl'] == detail['normalizedUrl']
    preview = client.get(detail['normalizedUrl'])
    assert preview.status_code == 200
    assert preview.headers['content-type'] == 'image/png'
    from PIL import Image

    with Image.open(io.BytesIO(preview.content)) as normalized:
        assert normalized.size == (120, 90)
        assert normalized.format == 'PNG'
    other = guest(client_factory())
    assert other.get(detail['normalizedUrl']).status_code == 404
    body = {
        'files': [{
            'clientFileId': 'heic-1', 'assetId': asset_id, 'kind': 'PHOTO',
            'copies': 1, 'orientation': 'PORTRAIT', 'fit': 'CONTAIN',
            'rotationDegrees': 0, 'crop': None,
        }],
    }
    prep_id = json_write(client, '/api/v1/preparations', body).json()['data']['id']
    process_preparation(db, prep_id)
    view = json_write(client, f'/api/v1/preparations/{prep_id}', None, method='GET').json()['data']
    assert view['status'] == 'READY'
    artifact = view['artifacts'][0]
    # The photo pipeline still uses the original HEIC asset, not the PNG preview.
    assert view['files'][0]['assetId'] == asset_id
    document = pymupdf.open(stream=client.get(artifact['url']).content, filetype='pdf')
    assert document.page_count == 1
    assert document[0].rect.width == pytest.approx(89 / 25.4 * 72, abs=0.4)


def test_excel_print_area_options_are_applied_and_validated(client, db, tmp_path):
    import openpyxl

    seed_config(db)
    guest(client)
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = '区域'
    for row in range(1, 21):
        for column in range(1, 6):
            sheet.cell(row=row, column=column, value=f'{column}-{row}')
    buffer = io.BytesIO()
    workbook.save(buffer)
    xlsx = buffer.getvalue()
    options = json.dumps({
        'sheetName': '区域', 'printArea': 'A1:E10', 'orientation': 'LANDSCAPE',
        'fitToWidth': 1, 'fitToHeight': 0, 'gridlines': True,
    })
    created = upload(
        client, xlsx, 'grades.xlsx',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        excelOptions=options,
    )
    assert created.status_code == 200, created.text
    asset_id = created.json()['data']['assetId']
    process_asset(db, asset_id)
    detail = json_write(client, f'/api/v1/assets/{asset_id}', None, method='GET').json()['data']
    assert detail['status'] == 'READY', (detail.get('failureCode'), detail.get('error'))
    assert detail['pages'] == 1
    assert detail['kind'] == 'XLSX'

    # Real legacy .xls through the API: OLE is detected from content even though
    # storage keys have no filename extension.
    from app.pdf_engine import resolve_soffice
    import subprocess

    soffice = resolve_soffice()
    if soffice:
        (tmp_path / 'grades.xlsx').write_bytes(xlsx)
        subprocess.run(
            [soffice, '--headless', '--norestore',
             f'-env:UserInstallation=file://{Path("/tmp") / ("lo-xls-" + secrets.token_hex(4))}',
             '--convert-to', 'xls', '--outdir', str(tmp_path), str(tmp_path / 'grades.xlsx')],
            check=True, capture_output=True, timeout=120,
        )
        xls_path = tmp_path / 'grades.xls'
        assert xls_path.is_file(), 'LibreOffice must produce a real XLS fixture'
        if xls_path.exists():
            legacy = upload(
                client, xls_path.read_bytes(), 'grades.xls', 'application/vnd.ms-excel',
                excelOptions=json.dumps({'printArea': 'A1:E10'}),
            )
            assert legacy.status_code == 200, legacy.text
            legacy_id = legacy.json()['data']['assetId']
            process_asset(db, legacy_id)
            legacy_detail = json_write(
                client, f'/api/v1/assets/{legacy_id}', None, method='GET'
            ).json()['data']
            assert legacy_detail['status'] == 'READY', (
                legacy_detail.get('failureCode'), legacy_detail.get('error')
            )
            assert legacy_detail['kind'] == 'XLS'
            assert legacy_detail['pages'] == 1

    invalid = upload(
        client, xlsx, 'bad.xlsx',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        excelOptions=json.dumps({'printArea': 'A1:ZZ99999'}),
    )
    assert invalid.status_code == 422
    assert invalid.json()['error']['code'] == 'EXCEL_OPTIONS_INVALID'


def test_cancel_prevents_asset_and_preparation_publish(client, db):
    from app.models import Asset, Preparation
    from sqlalchemy import select

    seed_config(db)
    guest(client)
    asset_id = upload(client, make_pdf(2), 'cancel.pdf').json()['data']['assetId']
    cancelled = json_write(client, f'/api/v1/assets/{asset_id}/cancel', {})
    assert cancelled.status_code == 200
    assert cancelled.json()['data']['status'] in ('FAILED', 'CANCELED')
    assert cancelled.json()['data']['cancelRequested'] is True
    db.expire_all()
    process_asset(db, asset_id)
    row = db.get(Asset, asset_id)
    db.refresh(row)
    assert row.status == 'FAILED'
    assert row.data['failureCode'] == 'CANCELED'
    assert row.normalized_asset_id is None
    assert client.get(f'/api/v1/assets/{asset_id}/content').status_code in (409, 410)
    retry = json_write(client, f'/api/v1/assets/{asset_id}/retry', {})
    assert retry.status_code == 409

    # Preparation cancellation before the worker runs.
    second_id = upload(client, make_pdf(2), 'cancel-prep.pdf').json()['data']['assetId']
    process_asset(db, second_id)
    prep_id = json_write(
        client, '/api/v1/preparations', preparation_payload(second_id, 2)
    ).json()['data']['id']
    assert json_write(client, f'/api/v1/preparations/{prep_id}/cancel', {}).status_code == 200
    process_preparation(db, prep_id)
    view = json_write(client, f'/api/v1/preparations/{prep_id}', None, method='GET').json()['data']
    assert view['status'] == 'CANCELLED'
    assert view['artifacts'] == []
    row = db.get(Preparation, prep_id)
    db.refresh(row)
    assert row.status == 'CANCELLED'
    assert db.scalar(select(Asset).where(Asset.purpose == 'PRINT_FINAL')) is None

    # Cancelling an already-ready asset reports the real final state.
    ready_id = upload(client, make_pdf(1), 'ready.pdf').json()['data']['assetId']
    process_asset(db, ready_id)
    late = json_write(client, f'/api/v1/assets/{ready_id}/cancel', {})
    assert late.status_code == 409
    assert late.json()['error']['code'] == 'ALREADY_COMPLETED'


def test_adopt_and_withdraw_checkout_dual_session(client_factory, db):
    from datetime import timedelta

    from app.core import token_hash
    from app.models import Account, AuthSession, Order, Principal, utcnow

    seed_config(db)
    client = guest(client_factory())
    asset_id = upload(client, make_pdf(2), 'adopt.pdf').json()['data']['assetId']
    process_asset(db, asset_id)
    prep_id = json_write(
        client, '/api/v1/preparations', preparation_payload(asset_id, 2)
    ).json()['data']['id']
    process_preparation(db, prep_id)
    view = json_write(client, f'/api/v1/preparations/{prep_id}', None, method='GET').json()['data']
    assert view['checkoutAccountId'] is None

    # Pure guest cannot adopt an account checkout.
    denied = json_write(
        client, f'/api/v1/preparations/{prep_id}/adopt-checkout',
        {'expectedVersion': view['version'], 'confirm': True},
    )
    assert denied.status_code in (401, 403)

    # Build the account directly: the shared account_factory still uses a
    # 36-char UUID invite_code against String(32), a shared-model defect that is
    # outside this module's ownership.
    principal = Principal()
    db.add(principal)
    db.flush()
    account = Account(
        principal_id=principal.id,
        email=f'{secrets.token_hex(6)}@example.test',
        password_hash='not-used-in-this-test',
        status='ACTIVE',
        verified_at=utcnow(),
        invite_code=secrets.token_hex(8),
    )
    db.add(account)
    db.flush()
    principal.account_id = account.id
    db.commit()
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(
        kind='CUSTOMER', account_id=account.id, principal_id=account.principal_id,
        token_hash=token_hash(token), expires_at=utcnow() + timedelta(days=30),
    ))
    db.commit()
    # Same browser: guest cookie + account session are both present.
    client.cookies.set('dormprint_account_session', token)
    from app.models import Preparation as Prep

    original_principal_id = db.get(Prep, prep_id).principal_id
    assert original_principal_id != account.principal_id
    adopted = json_write(
        client, f'/api/v1/preparations/{prep_id}/adopt-checkout',
        {'expectedVersion': view['version'], 'confirm': True},
    )
    assert adopted.status_code == 200, adopted.text
    adopted_view = adopted.json()['data']
    assert adopted_view['checkoutAccountId'] == account.id
    assert adopted_view['quote']['checkoutAccountId'] == account.id
    adopted_row = db.get(Prep, prep_id)
    db.refresh(adopted_row)
    assert adopted_row.principal_id == account.principal_id
    assert adopted_row.data['originalPrincipalId'] == original_principal_id
    # Preview keeps working for the adopted account before any order exists.
    preview_after_adopt = client.get(adopted_view['artifacts'][0]['url'])
    assert preview_after_adopt.status_code == 200
    assert hashlib.sha256(preview_after_adopt.content).hexdigest() == adopted_view['artifacts'][0]['sha256']
    assert [artifact['sha256'] for artifact in adopted_view['artifacts']] == [
        artifact['sha256'] for artifact in view['artifacts']
    ]
    assert adopted_view['version'] == view['version'] + 1

    withdrawn = json_write(
        client, f'/api/v1/preparations/{prep_id}/withdraw-checkout',
        {'expectedVersion': adopted_view['version'], 'confirm': True},
    )
    assert withdrawn.status_code == 200, withdrawn.text
    withdrawn_view = withdrawn.json()['data']
    assert withdrawn_view['checkoutAccountId'] is None
    assert withdrawn_view['quote']['checkoutAccountId'] is None
    assert withdrawn_view['quote']['id'] != adopted_view['quote']['id']
    assert [artifact['id'] for artifact in withdrawn_view['artifacts']] == [
        artifact['id'] for artifact in view['artifacts']
    ]
    withdrawn_row = db.get(Prep, prep_id)
    db.refresh(withdrawn_row)
    assert withdrawn_row.principal_id == original_principal_id
    assert withdrawn_row.data.get('originalPrincipalId') is None

    # Once an order references this preparation checkout can no longer be undone.
    adopted_again = json_write(
        client, f'/api/v1/preparations/{prep_id}/adopt-checkout',
        {'expectedVersion': withdrawn_view['version'], 'confirm': True},
    ).json()['data']
    from app.models import DeliverySlot, Location, Quote

    quote = db.get(Quote, adopted_again['quote']['id'])
    location = Location(name='东区 1 栋')
    slot = DeliverySlot(date='2026-10-31', name='19:00', starts_at=utcnow(),
                        ends_at=utcnow() + timedelta(hours=1), capacity=5)
    db.add_all([location, slot])
    db.flush()
    order = Order(
        principal_id=account.principal_id, pickup_code='TEST1', status='PENDING_CONFIRMATION',
        preparation_id=prep_id, quote_id=quote.id, total_cents=quote.total_cents,
        nickname='测试', phone='13800000000', location_id=location.id,
        requested_slot_id=slot.id, close_at=utcnow() + timedelta(hours=24), data={},
    )
    db.add(order)
    db.commit()
    blocked = json_write(
        client, f'/api/v1/preparations/{prep_id}/withdraw-checkout',
        {'expectedVersion': adopted_again['version'], 'confirm': True},
    )
    assert blocked.status_code == 409


def test_create_reprint_artifact_builds_only_selected_physical_sheets(client, db):
    from app import media
    from app.core import Actor
    from app.models import (
        Artifact, DeliverySlot, Location, Order, OrderFile, PrintTask, Quote, Staff, utcnow,
    )

    seed_config(db)
    guest(client)
    asset_id = upload(client, make_pdf(4), 'reprint.pdf').json()['data']['assetId']
    process_asset(db, asset_id)
    body = {
        'files': [{
            'clientFileId': 'file-r', 'assetId': asset_id, 'kind': 'DOCUMENT',
            'segments': [{'clientSegmentId': 'seg-r', 'ranges': [{'from': 1, 'to': 4}],
                          'colorMode': 'COLOR', 'sides': 'DUPLEX',
                          'flip': 'LONG_EDGE', 'copies': 1}],
            'excludedPages': [], 'bindingType': 'NONE',
        }],
    }
    prep_id = json_write(client, '/api/v1/preparations', body).json()['data']['id']
    process_preparation(db, prep_id)
    view = json_write(client, f'/api/v1/preparations/{prep_id}', None, method='GET').json()['data']
    source_artifact = db.get(Artifact, view['artifacts'][0]['id'])
    from app.models import Preparation

    preparation = db.get(Preparation, prep_id)
    quote = db.get(Quote, view['quote']['id'])
    location = Location(name='东区 2 栋')
    slot = DeliverySlot(date='2026-10-31', name='20:00', starts_at=utcnow(),
                        ends_at=utcnow() + timedelta(hours=1), capacity=5)
    staff = Staff(username='printer', name='打印员', role='ADMIN', password_hash='x', status='ACTIVE')
    db.add_all([location, slot, staff])
    db.flush()
    order = Order(
        principal_id=preparation.principal_id,
        pickup_code='RPRNT', status='PENDING_PRINT', preparation_id=prep_id,
        quote_id=quote.id, total_cents=quote.total_cents, nickname='测试',
        phone='13800000000', location_id=location.id, requested_slot_id=slot.id,
        close_at=utcnow() + timedelta(hours=24), data={},
    )
    db.add(order)
    db.flush()
    order_file = OrderFile(
        order_id=order.id, revision=1, client_file_id='file-r',
        asset_id=asset_id, kind='DOCUMENT', data={},
    )
    db.add(order_file)
    db.flush()
    task = PrintTask(
        order_id=order.id, file_id=order_file.id, artifact_id=source_artifact.id,
        active=True, status='ATTEMPTED',
        settings={'segmentId': 'seg-r', 'clientSegmentId': 'seg-r', 'copies': 1,
                  'sides': 'DUPLEX', 'range': '1-4'},
    )
    db.add(task)
    db.commit()
    actor = Actor(staff_id=staff.id, staff_role='ADMIN')
    reprint = media.create_reprint_artifact(db, task, ['c1-s2'], actor)
    db.commit()
    assert reprint.id != source_artifact.id
    assert reprint.preparation_id == source_artifact.preparation_id
    assert (reprint.pages, reprint.sheets) == (2, 1)
    assert reprint.settings['nonBillable'] is True
    assert reprint.settings['reprintOfArtifactId'] == source_artifact.id
    new_asset = db.get(__import__('app.models', fromlist=['Asset']).Asset, reprint.asset_id)
    assert new_asset.status == 'READY' and new_asset.purpose == 'PRINT_REPRINT'
    from app.settings import get_settings

    path = get_settings().storage_root / new_asset.storage_key
    document = pymupdf.open(path)
    assert document.page_count == 2
    assert 'Integration page 3' in document[0].get_text()
    assert 'Integration page 4' in document[1].get_text()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == reprint.sha256
    with pytest.raises(Exception):
        media.create_reprint_artifact(db, task, ['c1-s99'], actor)

    # The production module's own catalog must read this page map and its
    # helper path must accept a real media reprint artifact.
    from app.production import _create_reprint_artifact
    from app.production_schemas import sheet_catalog

    catalog = sheet_catalog(source_artifact, task.settings)
    assert [row['sheetId'] for row in catalog] == ['c1-s1', 'c1-s2']
    assert catalog[0]['frontPage'] == 1 and catalog[0]['backPage'] == 2
    assert catalog[0]['pdfFrontPage'] == 1 and catalog[0]['pdfBackPage'] == 2
    assert catalog[1]['blankBack'] is False
    production_reprint = _create_reprint_artifact(db, order, task, ['c1-s1'], actor)
    db.commit()
    assert production_reprint.id not in (source_artifact.id, reprint.id)
    assert production_reprint.preparation_id == source_artifact.preparation_id
    assert production_reprint.pages == 2


def test_openapi_media_contract_is_typed(app):
    schema = app.openapi()
    paths = schema['paths']
    expected = [
        '/api/v1/assets',
        '/api/v1/assets/{asset_id}',
        '/api/v1/assets/{asset_id}/content',
        '/api/v1/assets/{asset_id}/retry',
        '/api/v1/assets/{asset_id}/cancel',
        '/api/v1/upload-sessions',
        '/api/v1/upload-sessions/{session_id}',
        '/api/v1/upload-sessions/{session_id}/chunks',
        '/api/v1/upload-sessions/{session_id}/complete',
        '/api/v1/preparations',
        '/api/v1/preparations/{preparation_id}',
        '/api/v1/preparations/{preparation_id}/retry',
        '/api/v1/preparations/{preparation_id}/requote',
        '/api/v1/preparations/{preparation_id}/adopt-checkout',
        '/api/v1/preparations/{preparation_id}/withdraw-checkout',
        '/api/v1/preparations/{preparation_id}/cancel',
    ]
    for path in expected:
        assert path in paths, path
    upload_schema = paths['/api/v1/assets']['post']['responses']['200']['content'][
        'application/json'
    ]['schema']
    assert '$ref' in upload_schema
    prepared = paths['/api/v1/preparations']['post']['requestBody']['content'][
        'application/json'
    ]['schema']
    assert '$ref' in prepared


def test_transient_asset_and_preparation_failures_are_retryable(client, db, monkeypatch):
    from app import media, pdf_engine

    seed_config(db)
    guest(client)
    asset_id = upload(client, make_pdf(2), 'retry.pdf').json()['data']['assetId']
    original_convert = pdf_engine.source_to_normalized_pdf

    def broken(*args, **kwargs):
        raise pdf_engine.EngineError('CONVERTER_TIMEOUT', '文档转换超时，请稍后重试', retryable=True)

    monkeypatch.setattr(pdf_engine, 'source_to_normalized_pdf', broken)
    process_asset(db, asset_id)
    detail = json_write(client, f'/api/v1/assets/{asset_id}', None, method='GET').json()['data']
    assert detail['status'] == 'FAILED'
    assert detail['failureCode'] == 'CONVERTER_TIMEOUT'
    assert detail['retryable'] is True
    retry = json_write(client, f'/api/v1/assets/{asset_id}/retry', {})
    assert retry.status_code == 200
    assert retry.json()['data']['status'] == 'QUEUED'
    monkeypatch.setattr(pdf_engine, 'source_to_normalized_pdf', original_convert)
    process_asset(db, asset_id)
    assert json_write(
        client, f'/api/v1/assets/{asset_id}', None, method='GET'
    ).json()['data']['status'] == 'READY'

    prep_id = json_write(
        client, '/api/v1/preparations', preparation_payload(asset_id, 2)
    ).json()['data']['id']
    original_plan = media._build_artifact_plan

    def broken_plan(*args, **kwargs):
        raise pdf_engine.EngineError('ARTIFACT_VERIFY_FAILED', '最终 PDF 校验失败', retryable=True)

    monkeypatch.setattr(media, '_build_artifact_plan', broken_plan)
    process_preparation(db, prep_id)
    view = json_write(client, f'/api/v1/preparations/{prep_id}', None, method='GET').json()['data']
    assert view['status'] == 'FAILED'
    assert view['failureCode'] == 'ARTIFACT_VERIFY_FAILED'
    retry = json_write(client, f'/api/v1/preparations/{prep_id}/retry', {})
    assert retry.status_code == 200
    assert retry.json()['data']['status'] == 'QUEUED'
    monkeypatch.setattr(media, '_build_artifact_plan', original_plan)
    process_preparation(db, prep_id)
    view = json_write(client, f'/api/v1/preparations/{prep_id}', None, method='GET').json()['data']
    assert view['status'] == 'READY' and view['quote'] and len(view['artifacts']) == 1


def test_duplicate_worker_is_idempotent_and_stale_lease_cannot_publish(client, db, monkeypatch):
    from app import media, pdf_engine
    from app.db import SessionLocal
    from app.models import Asset
    from sqlalchemy import func, select

    seed_config(db)
    guest(client)
    asset_id = upload(client, make_pdf(2), 'lease.pdf').json()['data']['assetId']
    process_asset(db, asset_id)
    process_asset(db, asset_id)  # duplicate delivery of the same job
    assert db.scalar(
        select(func.count()).select_from(Asset).where(Asset.purpose == 'PRINT_NORMALIZED')
    ) == 1

    raced_id = upload(client, make_pdf(2), 'race.pdf').json()['data']['assetId']
    original = pdf_engine.source_to_normalized_pdf

    def racing(*args, **kwargs):
        output = original(*args, **kwargs)
        session = SessionLocal()
        try:
            row = session.get(Asset, raced_id)
            data = dict(row.data or {})
            data['attemptToken'] = 'newer-worker-owns-this-row'
            row.data = data
            session.commit()
        finally:
            session.close()
        return output

    monkeypatch.setattr(pdf_engine, 'source_to_normalized_pdf', racing)
    media.process_asset(db, raced_id)
    row = db.get(Asset, raced_id)
    db.refresh(row)
    assert row.normalized_asset_id is None
    assert row.status == 'PROCESSING'
    assert db.scalar(
        select(func.count()).select_from(Asset).where(Asset.purpose == 'PRINT_NORMALIZED')
    ) == 1


def test_missing_price_fails_without_a_fake_quote_then_retry_after_configuration(client, db):
    from app.models import ConfigSection
    from sqlalchemy import select

    seed_config(db, prices={key: None for key in PRICES})
    guest(client)
    asset_id = upload(client, make_pdf(2), 'priced.pdf').json()['data']['assetId']
    process_asset(db, asset_id)
    prep_id = json_write(
        client, '/api/v1/preparations', preparation_payload(asset_id, 2)
    ).json()['data']['id']
    process_preparation(db, prep_id)
    view = json_write(client, f'/api/v1/preparations/{prep_id}', None, method='GET').json()['data']
    assert view['status'] == 'FAILED'
    assert view['failureCode'] == 'PRICE_NOT_CONFIGURED'
    assert view['quote'] is None
    assert view['artifacts'] == []
    assert view['validation']['ok'] is False
    assert json_write(client, f'/api/v1/preparations/{prep_id}/retry', {}).status_code == 200

    row = db.scalar(select(ConfigSection).where(ConfigSection.section == 'prices'))
    row.data = dict(PRICES)
    row.version += 1
    db.commit()
    process_preparation(db, prep_id)
    view = json_write(client, f'/api/v1/preparations/{prep_id}', None, method='GET').json()['data']
    assert view['status'] == 'READY'
    assert view['quote']['totalCents'] == 8 * 1  # 2 pages duplex = 1 sheet x 1 copy
    assert len(view['artifacts']) == 1


def test_binding_thickness_limit_is_enforced_server_side(client, db):
    services = dict(SERVICES)
    services['bindingLimits'] = {'CORNER': 2, 'SIDE': None}
    seed_config(db, services=services)
    guest(client)
    asset_id = upload(client, make_pdf(5), 'thick.pdf').json()['data']['assetId']
    process_asset(db, asset_id)
    body = preparation_payload(asset_id, 5, binding='CORNER')
    body['files'][0]['segments'][0]['copies'] = 1
    response = json_write(client, '/api/v1/preparations', body)
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'BINDING_TOO_THICK'


def test_cover_photo_crop_is_applied_through_the_api(client, db):
    from PIL import Image

    from app.pdf_engine import photo_crop

    seed_config(db)
    guest(client)
    buffer = io.BytesIO()
    Image.new('RGB', (400, 300), (30, 170, 30)).save(buffer, format='JPEG')
    asset_id = upload(client, buffer.getvalue(), 'cover.jpg', 'image/jpeg').json()['data']['assetId']
    process_asset(db, asset_id)
    crop = photo_crop(400, 300, 127 - 6, 89 - 6, 1.2, 45, 55)
    body = {
        'files': [{
            'clientFileId': 'cover-1', 'assetId': asset_id, 'kind': 'PHOTO',
            'copies': 1, 'orientation': 'LANDSCAPE', 'fit': 'COVER',
            'rotationDegrees': 0, 'crop': crop,
        }],
    }
    prep_id = json_write(client, '/api/v1/preparations', body).json()['data']['id']
    process_preparation(db, prep_id)
    view = json_write(client, f'/api/v1/preparations/{prep_id}', None, method='GET').json()['data']
    assert view['status'] == 'READY'
    artifact = view['artifacts'][0]
    assert artifact['pages'] == 1 and artifact['sides'] == 'SINGLE'
    content = client.get(artifact['url']).content
    document = pymupdf.open(stream=content, filetype='pdf')
    assert document.page_count == 1
    # Crop preserves the print-area aspect and fills it in millimetres.
    assert crop['width'] * 400 / (crop['height'] * 300) == pytest.approx(
        (127 - 6) / (89 - 6), rel=0.02
    )


def test_cancel_requested_while_processing_never_publishes(client, db, monkeypatch):
    from app import media
    from app.db import SessionLocal
    from app.models import Asset, Preparation
    from sqlalchemy import func, select

    seed_config(db)
    guest(client)
    asset_id = upload(client, make_pdf(2), 'inflight.pdf').json()['data']['assetId']
    row = db.get(Asset, asset_id)
    data = dict(row.data or {})
    data['attemptToken'] = 'running-attempt'
    row.data = data
    row.status = 'PROCESSING'
    db.commit()
    cancelled = json_write(client, f'/api/v1/assets/{asset_id}/cancel', {})
    assert cancelled.status_code == 200
    assert cancelled.json()['data']['status'] == 'PROCESSING'
    assert cancelled.json()['data']['cancelRequested'] is True
    db.expire_all()
    process_asset(db, asset_id)
    row = db.get(Asset, asset_id)
    db.refresh(row)
    assert row.status == 'FAILED' and row.data['failureCode'] == 'CANCELED'
    assert row.normalized_asset_id is None

    # A preparation cancelled mid-generation must not publish old artifacts.
    source_id = upload(client, make_pdf(2), 'inflight-prep.pdf').json()['data']['assetId']
    process_asset(db, source_id)
    prep_id = json_write(
        client, '/api/v1/preparations', preparation_payload(source_id, 2)
    ).json()['data']['id']
    original_plan = media._build_artifact_plan

    def cancelling(*args, **kwargs):
        plans = original_plan(*args, **kwargs)
        session = SessionLocal()
        try:
            preparation = session.get(Preparation, prep_id)
            preparation.cancel_requested = True
            session.commit()
        finally:
            session.close()
        return plans

    monkeypatch.setattr(media, '_build_artifact_plan', cancelling)
    process_preparation(db, prep_id)
    view = json_write(client, f'/api/v1/preparations/{prep_id}', None, method='GET').json()['data']
    assert view['status'] == 'CANCELLED'
    assert view['artifacts'] == []
    assert db.scalar(
        select(func.count()).select_from(Asset).where(Asset.purpose == 'PRINT_FINAL')
    ) == 0


def test_bw_raster_fallback_is_explicit_in_the_artifact_manifest(client, db, monkeypatch):
    from app import pdf_engine

    seed_config(db)
    guest(client)
    asset_id = upload(client, make_pdf(2), 'gray.pdf').json()['data']['assetId']
    process_asset(db, asset_id)
    prep_id = json_write(
        client, '/api/v1/preparations', preparation_payload(asset_id, 2)
    ).json()['data']['id']
    monkeypatch.setattr(pdf_engine, 'resolve_ghostscript', lambda configured=None: None)
    process_preparation(db, prep_id)
    view = json_write(client, f'/api/v1/preparations/{prep_id}', None, method='GET').json()['data']
    assert view['status'] == 'READY'
    warnings = view['artifacts'][0]['warnings']
    assert warnings and warnings[0]['code'] == 'BW_GRAYSCALE_RASTERIZED'
    assert warnings[0]['dpi'] >= 72
    # Pages/sheets remain authoritative even when the BW file was rasterised.
    assert view['artifacts'][0]['pages'] == 2
    assert view['artifacts'][0]['sheets'] == 1


def test_jobs_are_real_outbox_rows_and_drafts_survive_a_fresh_browser(client_factory, db):
    from app.models import Job
    from sqlalchemy import select

    seed_config(db)
    first = guest(client_factory())
    asset_id = upload(first, make_pdf(3), 'draft.pdf').json()['data']['assetId']
    jobs = list(db.scalars(select(Job).where(Job.kind == 'ASSET', Job.target_id == asset_id)))
    assert jobs and jobs[0].status == 'QUEUED'
    process_asset(db, asset_id)
    prep_id = json_write(
        first, '/api/v1/preparations', preparation_payload(asset_id, 3)
    ).json()['data']['id']
    prep_jobs = list(db.scalars(select(Job).where(Job.kind == 'PREPARATION', Job.target_id == prep_id)))
    assert prep_jobs and prep_jobs[0].status == 'QUEUED'

    # A browser refresh is a new client with the same authenticated cookies;
    # the uploaded asset, settings and preparation must still be recoverable.
    refreshed = client_factory()
    for name, value in first.cookies.items():
        refreshed.cookies.set(name, value)
    assets = refreshed.get('/api/v1/assets').json()['data']
    assert any(item['assetId'] == asset_id for item in assets['items'])
    drafts = refreshed.get('/api/v1/preparations').json()['data']
    assert any(item['id'] == prep_id for item in drafts['items'])
    recovered = refreshed.get(f'/api/v1/preparations/{prep_id}').json()['data']
    assert recovered['id'] == prep_id
    assert recovered['files'][0]['assetId'] == asset_id
    assert recovered['files'][0]['pages'] == 3


def test_delivery_proof_upload_uses_frontend_field_and_serves_until_worker(
    client_factory, db, staff_factory
):
    from datetime import timedelta

    from app.core import token_hash
    from app.models import (
        Account, AuthSession, DeliveryAssignment, DeliveryBatch, DeliverySlot, Location, Order,
        Preparation, Principal, Quote, utcnow,
    )

    seed_config(db)
    now = utcnow()
    principal = Principal()
    location = Location(name='东区 3 栋')
    slot = DeliverySlot(date='2026-10-31', name='21:00', starts_at=now,
                        ends_at=now + timedelta(hours=1), capacity=5)
    db.add_all([principal, location, slot])
    db.flush()
    preparation = Preparation(principal_id=principal.id, status='READY', content_revision=1,
                              expires_at=now + timedelta(hours=2))
    db.add(preparation)
    db.flush()
    quote = Quote(preparation_id=preparation.id, price_version=1, expires_at=now + timedelta(minutes=15),
                  subtotal_cents=0, discount_cents=0, total_cents=0)
    db.add(quote)
    db.flush()
    order = Order(
        principal_id=principal.id, pickup_code='PROOF', status='DELIVERING',
        preparation_id=preparation.id, quote_id=quote.id, total_cents=0,
        nickname='同学', phone='13800000000', location_id=location.id,
        requested_slot_id=slot.id, close_at=now + timedelta(hours=2), data={},
    )
    db.add(order)
    db.flush()
    staff = staff_factory(role='COURIER')
    batch = DeliveryBatch(name='晚班', courier_id=staff.id, starts_at=now,
                          ends_at=now + timedelta(hours=2), status='DELIVERING')
    db.add(batch)
    db.flush()
    assignment = DeliveryAssignment(order_id=order.id, batch_id=batch.id,
                                    courier_id=staff.id, status='STARTED', active=True, data={})
    db.add(assignment)
    assert staff.account_id, 'staff_factory must link a verified customer account'
    account = db.get(Account, staff.account_id)
    assert account is not None and account.verified_at is not None and account.status == 'ACTIVE'
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(kind='CUSTOMER', account_id=account.id,
                       principal_id=account.principal_id,
                       session_version=account.session_version,
                       token_hash=token_hash(token),
                       expires_at=now + timedelta(days=7)))
    db.commit()
    client = client_factory()
    client.cookies.set('dormprint_account_session', token)
    client.staff = staff
    client.account = account

    from PIL import Image

    buffer = io.BytesIO()
    Image.new('RGB', (80, 60), 'white').save(buffer, format='PNG')
    response = client.post(
        '/api/v1/assets',
        files={'file': ('proof.png', buffer.getvalue(), 'image/png')},
        data={'purpose': 'DELIVERY_PROOF', 'deliveryAssignmentId': assignment.id},
        headers=_headers(client),
    )
    assert response.status_code == 200, response.text
    result = response.json()['data']
    assert result['url'] == f"/api/v1/assets/{result['assetId']}/content"
    # The proof photo is fully validated at intake and ready for the proof
    # transaction; the courier must not wait for a metadata worker.
    assert result['status'] == 'READY'
    early = client.get(result['url'])
    assert early.status_code == 200 and early.content == buffer.getvalue()
    process_asset(db, result['assetId'])
    detail = json_write(client, f"/api/v1/assets/{result['assetId']}", None, method='GET').json()['data']
    assert detail['status'] == 'READY'
    assert detail['purpose'] == 'DELIVERY_PROOF'

    guest_client = guest(client_factory())
    denied = upload(guest_client, buffer.getvalue(), 'proof.png', 'image/png', purpose='DELIVERY_PROOF')
    assert denied.status_code in (401, 403)


def test_outbox_worker_claim_and_dispatch_runs_real_processors(client, db):
    from app import jobs
    from app.models import Job
    from sqlalchemy import select

    seed_config(db)
    guest(client)
    asset_id = upload(client, make_pdf(3), 'worker.pdf').json()['data']['assetId']
    claim = jobs.claim_next_job(kinds=['ASSET'])
    assert claim is not None
    job_id, token = claim
    assert jobs.process_claim(job_id, token) is True
    job = db.get(Job, job_id)
    db.refresh(job)
    assert job.status == 'READY' and job.lease_token is None
    from app.models import Asset

    asset = db.get(Asset, asset_id)
    db.refresh(asset)
    assert asset.status == 'READY' and asset.pages == 3

    prep_id = json_write(
        client, '/api/v1/preparations', preparation_payload(asset_id, 3)
    ).json()['data']['id']
    claim = jobs.claim_next_job(kinds=['PREPARATION'])
    assert claim is not None
    job_id, token = claim
    assert jobs.process_claim(job_id, token) is True
    job = db.get(Job, job_id)
    db.refresh(job)
    assert job.status == 'READY' and job.lease_token is None
    view = json_write(client, f'/api/v1/preparations/{prep_id}', None, method='GET').json()['data']
    assert view['status'] == 'READY' and view['quote'] and view['artifacts']


def test_preparation_quote_delegates_to_growth_volume_discount(client, db):
    from app.models import ConfigSection

    seed_config(db)
    db.add(ConfigSection(section='volume-discounts', data={
        'id': 'volume-main', 'enabled': True, 'description': '测试满量',
        'effectiveAt': None, 'tiers': [{'id': 'tier-1', 'minSheets': 8, 'payRateBps': 8000}],
        'stackable': False,
    }))
    db.commit()
    guest(client)
    asset_id = upload(client, make_pdf(7), 'volume.pdf').json()['data']['assetId']
    process_asset(db, asset_id)
    body = {
        'files': [{
            'clientFileId': 'vol-1', 'assetId': asset_id, 'kind': 'DOCUMENT',
            'segments': [{'clientSegmentId': 'vol-s1', 'ranges': [{'from': 1, 'to': 7}],
                          'colorMode': 'BW', 'sides': 'DUPLEX',
                          'flip': 'LONG_EDGE', 'copies': 2}],
            'excludedPages': [], 'bindingType': 'NONE',
        }],
        'promotionIntent': {'selectionMode': 'AUTO'},
    }
    prep_id = json_write(client, '/api/v1/preparations', body).json()['data']['id']
    process_preparation(db, prep_id)
    view = json_write(client, f'/api/v1/preparations/{prep_id}', None, method='GET').json()['data']
    assert view['status'] == 'READY'
    quote = view['quote']
    # 7 pages duplex x 2 copies = 8 sheets, so the 8-sheet 80% tier applies.
    assert quote['selectedPromotionType'] == 'VOLUME_TIER'
    assert quote['subtotalCents'] == 8 * 8
    assert quote['discountCents'] > 0
    assert quote['totalCents'] == quote['subtotalCents'] - quote['discountCents']
    assert sum(row['amountCents'] for row in quote['discountAllocations']) == quote['discountCents']


def test_expired_server_draft_is_not_recoverable(client, db):
    from datetime import timedelta

    from app.models import Preparation, utcnow

    seed_config(db)
    guest(client)
    asset_id = upload(client, make_pdf(2), 'expired-draft.pdf').json()['data']['assetId']
    process_asset(db, asset_id)
    prep_id = json_write(
        client, '/api/v1/preparations', preparation_payload(asset_id, 2)
    ).json()['data']['id']
    process_preparation(db, prep_id)
    row = db.get(Preparation, prep_id)
    row.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    assert json_write(client, f'/api/v1/preparations/{prep_id}', None, method='GET').status_code == 410
    items = json_write(client, '/api/v1/preparations', None, method='GET').json()['data']['items']
    assert all(item['id'] != prep_id for item in items)
    adopt = json_write(
        client, f'/api/v1/preparations/{prep_id}/adopt-checkout',
        {'expectedVersion': row.version, 'confirm': True},
    )
    assert adopt.status_code == 410


def test_verified_account_upload_prep_settles_with_account_without_adopt(client, db):
    from app.models import Preparation

    seed_config(db)
    account = make_verified_account(db)
    login_account(client, db, account)
    asset_id = upload(client, make_pdf(2), 'account-prep.pdf').json()['data']['assetId']
    process_asset(db, asset_id)
    prep_id = json_write(
        client, '/api/v1/preparations', preparation_payload(asset_id, 2)
    ).json()['data']['id']
    process_preparation(db, prep_id)
    view = json_write(client, f'/api/v1/preparations/{prep_id}', None, method='GET').json()['data']
    assert view['status'] == 'READY'
    assert view['checkoutAccountId'] == account.id
    assert view['checkoutAuthKind'] == 'REGISTERED'
    assert view['quote']['checkoutAccountId'] == account.id
    assert view['canAdopt'] is False
    row = db.get(Preparation, prep_id)
    db.refresh(row)
    assert row.checkout_account_id == account.id
    assert row.principal_id == account.principal_id
    # A direct account checkout is not an adopted guest grant and must not be
    # silently downgraded back to guest by a stale UI action.
    assert json_write(
        client, f'/api/v1/preparations/{prep_id}/withdraw-checkout',
        {'expectedVersion': view['version'], 'confirm': True},
    ).status_code == 409
    assert json_write(
        client, f'/api/v1/preparations/{prep_id}/adopt-checkout',
        {'expectedVersion': view['version'], 'confirm': True},
    ).status_code == 409


def test_delegated_revision_inherits_checkout_and_rechecks_source_grant(client_factory, db):
    from datetime import timedelta

    from sqlalchemy import select

    from app.core import token_hash
    from app.models import (
        AccessGrant, Artifact, AuthSession, Order, Preparation, Principal, utcnow,
    )

    seed_config(db)
    owner = make_verified_account(db)
    owner_principal = db.get(Principal, owner.principal_id)
    order_graph = make_pending_order(
        db, owner_principal, checkout_account=owner, auth_kind='REGISTERED'
    )
    order = order_graph.order
    grant = AccessGrant(
        order_id=order.id, token_hash=token_hash(secrets.token_urlsafe(32)),
        scopes=['ORDER_VIEW', 'FINAL_PDF_PREVIEW', 'ORDER_REVISE_PENDING'],
        expires_at=utcnow() + timedelta(days=1), created_by='staff-test',
    )
    db.add(grant)
    db.flush()
    session_token = secrets.token_urlsafe(32)
    db.add(AuthSession(
        kind='ORDER_ACCESS', grant_id=grant.id,
        token_hash=token_hash(session_token),
        expires_at=utcnow() + timedelta(hours=12),
    ))
    db.commit()

    editor = guest(client_factory())
    editor.cookies.set('dormprint_order_access_session', session_token)
    asset_id = upload(editor, make_pdf(2), 'delegated.pdf').json()['data']['assetId']
    process_asset(db, asset_id)
    body = preparation_payload(asset_id, 2)
    body['replacesOrderId'] = order.id
    body['expectedOrderVersion'] = order.version
    created = json_write(editor, '/api/v1/preparations', body)
    assert created.status_code == 200, created.text
    prep = created.json()['data']
    assert prep['sourceGrantId'] == grant.id
    assert prep['checkoutAccountId'] == owner.id
    assert prep['checkoutAuthKind'] == 'REGISTERED'
    prep_id = prep['id']
    stored_prep = db.get(Preparation, prep_id)
    db.refresh(stored_prep)
    assert stored_prep.principal_id == order.principal_id
    assert stored_prep.source_grant_id == grant.id
    assert json_write(editor, f'/api/v1/preparations/{prep_id}', None, method='GET').status_code == 200
    listed = json_write(editor, '/api/v1/preparations', None, method='GET').json()['data']['items']
    assert any(item['id'] == prep_id for item in listed)

    process_preparation(db, prep_id)
    view = json_write(editor, f'/api/v1/preparations/{prep_id}', None, method='GET').json()['data']
    assert view['status'] == 'READY'
    assert view['quote']['checkoutAccountId'] == owner.id
    assert len(view['artifacts']) == 1
    # The source grant also authorizes the editor to preview the final artifact
    # before the order revision is submitted.
    artifact_url = view['artifacts'][0]['url']
    assert editor.get(artifact_url).status_code == 200
    row = db.get(Preparation, prep_id)
    db.refresh(row)
    assert row.source_grant_id == grant.id
    assert row.checkout_account_id == owner.id

    # A second delegated draft created before revocation must not publish after
    # the source grant is revoked mid-generation.
    second_asset = upload(editor, make_pdf(2), 'delegated-2.pdf').json()['data']['assetId']
    process_asset(db, second_asset)
    body2 = preparation_payload(second_asset, 2)
    body2['replacesOrderId'] = order.id
    body2['expectedOrderVersion'] = order.version
    second = json_write(editor, '/api/v1/preparations', body2).json()['data']
    assert second['sourceGrantId'] == grant.id
    grant_row = db.get(AccessGrant, grant.id)
    grant_row.revoked_at = utcnow()
    db.commit()
    process_preparation(db, second['id'])
    stale = json_write(editor, f"/api/v1/preparations/{second['id']}", None, method='GET')
    assert stale.status_code == 404  # revoked source grant removes delegated access
    stored = db.get(Preparation, second['id'])
    db.refresh(stored)
    assert stored.status == 'FAILED'
    assert stored.data['failureCode'] == 'SOURCE_GRANT_REVOKED'
    assert db.scalar(select(Artifact.id).where(Artifact.preparation_id == stored.id)) is None
    # Revoking the source grant also removes delegated access to earlier final
    # artifacts of this order revision.
    assert editor.get(artifact_url).status_code == 404


def test_job_claim_rejects_wrong_lease_token_without_changing_state(client, db):
    from app import jobs
    from app.models import Asset, Job

    seed_config(db)
    guest(client)
    asset_id = upload(client, make_pdf(2), 'lease-token.pdf').json()['data']['assetId']
    claim = jobs.claim_next_job(kinds=['ASSET'])
    assert claim is not None
    job_id, token = claim
    assert jobs.process_claim(job_id, 'not-the-lease-token') is False
    job = db.get(Job, job_id)
    db.refresh(job)
    assert job.status == 'PROCESSING'
    assert job.lease_token == token
    asset = db.get(Asset, asset_id)
    db.refresh(asset)
    assert asset.status == 'QUEUED'
    assert jobs.process_claim(job_id, token) is True
    job = db.get(Job, job_id)
    db.refresh(job)
    assert job.status == 'READY' and job.lease_token is None
    asset = db.get(Asset, asset_id)
    db.refresh(asset)
    assert asset.status == 'READY'


def test_a4_image_fixture_goes_through_server_normalization(client, db, tmp_path):
    from PIL import Image

    from app.pdf_engine import A4_HEIGHT_PT, A4_WIDTH_PT

    seed_config(db)
    guest(client)
    image = Image.new('RGB', (640, 480), (200, 120, 40))
    image_path = tmp_path / 'a4-source.png'
    image.save(image_path)
    # This mirrors the existing frontend protocol: an image is laid out as a
    # one-page A4 PDF with 10mm margins, then uploaded as PRINT_SOURCE.
    doc = pymupdf.open()
    page = doc.new_page(width=A4_WIDTH_PT, height=A4_HEIGHT_PT)
    margin = 10 / 25.4 * 72
    page.insert_image(
        pymupdf.Rect(margin, margin, A4_WIDTH_PT - margin, A4_HEIGHT_PT - margin),
        filename=str(image_path),
    )
    a4_pdf = doc.tobytes()
    doc.close()
    asset_id = upload(client, a4_pdf, 'a4-image.pdf').json()['data']['assetId']
    process_asset(db, asset_id)
    prep_id = json_write(
        client, '/api/v1/preparations',
        preparation_payload(
            asset_id, 1,
            segments=[{'clientSegmentId': 'a4-img', 'ranges': [{'from': 1, 'to': 1}],
                       'colorMode': 'COLOR', 'sides': 'SINGLE',
                       'flip': 'NONE', 'copies': 1}],
        ),
    ).json()['data']['id']
    process_preparation(db, prep_id)
    view = json_write(client, f'/api/v1/preparations/{prep_id}', None, method='GET').json()['data']
    assert view['status'] == 'READY'
    artifact = view['artifacts'][0]
    assert artifact['pages'] == 1 and artifact['sheets'] == 1
    document = pymupdf.open(stream=client.get(artifact['url']).content, filetype='pdf')
    assert document[0].rect.width == pytest.approx(A4_WIDTH_PT, abs=0.5)
    assert document[0].rect.height == pytest.approx(A4_HEIGHT_PT, abs=0.5)


def test_guest_order_revision_is_not_silently_upgraded_to_account(client, db):
    from sqlalchemy import select

    from app.models import Principal

    seed_config(db)
    guest(client)
    guest_principal = db.scalar(select(Principal).where(Principal.account_id.is_(None)))
    assert guest_principal is not None
    order_graph = make_pending_order(
        db, guest_principal, checkout_account=None, auth_kind='ANONYMOUS'
    )
    account = make_verified_account(db)
    login_account(client, db, account)
    asset_id = upload(client, make_pdf(2), 'guest-revision.pdf').json()['data']['assetId']
    process_asset(db, asset_id)
    body = preparation_payload(asset_id, 2)
    body['replacesOrderId'] = order_graph.order.id
    body['expectedOrderVersion'] = order_graph.order.version
    prep_id = json_write(client, '/api/v1/preparations', body).json()['data']['id']
    process_preparation(db, prep_id)
    view = json_write(client, f'/api/v1/preparations/{prep_id}', None, method='GET').json()['data']
    assert view['status'] == 'READY'
    assert view['checkoutAccountId'] is None
    assert view['checkoutAuthKind'] == 'ANONYMOUS'
    assert view['quote']['checkoutAccountId'] is None
    assert view['canAdopt'] is False
