from datetime import timedelta
import secrets
import threading

import pytest

from app.core import Actor, DomainError
from app.models import (
    Artifact,
    Asset,
    DeliverySlot,
    Location,
    Preparation,
    Principal,
    Quote,
    utcnow,
)


@pytest.fixture
def submission_fixture(db):
    """A minimal READY preparation/quote for an actual submit_order call."""
    from app.config import get_config

    principal = Principal()
    db.add(principal)
    db.flush()
    for section in ('services', 'announcement'):
        config = get_config(db, section)
        config.data = {**config.data, 'serviceEnabled': True}
    workflow = get_config(db, 'workflow')
    workflow.data = {**workflow.data, 'unpaidTimeoutMinutes': 60}
    prices = get_config(db, 'prices')
    prices.data = {**prices.data, 'bwSingle': 10}

    source = Asset(
        principal_id=principal.id, purpose='PRINT_SOURCE', name='race.pdf',
        mime='application/pdf', size=100, sha256='a' * 64,
        storage_key='fixtures/race-source.pdf', status='READY', pages=1)
    db.add(source)
    db.flush()
    prep = Preparation(
        principal_id=principal.id, status='READY',
        expires_at=utcnow() + timedelta(hours=24),
        input={'files': [{
            'clientFileId': 'file-race',
            'assetId': source.id,
            'kind': 'DOCUMENT',
            'segments': [{
                'clientSegmentId': 'segment-race',
                'ranges': [{'from': 1, 'to': 1}],
                'colorMode': 'BW', 'sides': 'SINGLE', 'flip': 'NONE', 'copies': 1,
            }],
            'excludedPages': [], 'bindingType': 'NONE',
        }]},
        data={})
    db.add(prep)
    db.flush()
    final = Asset(
        principal_id=principal.id, purpose='FINAL_PDF', name='race-final.pdf',
        mime='application/pdf', size=100, sha256='b' * 64,
        storage_key='fixtures/race-final.pdf', status='READY', pages=1)
    db.add(final)
    db.flush()
    artifact = Artifact(
        preparation_id=prep.id, asset_id=final.id, client_file_id='file-race',
        client_segment_id='segment-race', sha256='b' * 64, pages=1, sheets=1,
        settings={'range': '1', 'copies': 1, 'colorMode': 'BW', 'sides': 'SINGLE',
                  'flip': 'NONE', 'blankBacks': 0},
        page_map=[{'sourcePage': 1, 'copyIndex': 1, 'sheetIndex': 1, 'side': 'FRONT'}])
    db.add(artifact)
    db.flush()
    quote = Quote(
        preparation_id=prep.id, price_version=prices.version,
        expires_at=utcnow() + timedelta(minutes=15),
        subtotal_cents=10, total_cents=10, discount_cents=0,
        data={'id': 'seed', 'priceVersion': prices.version,
              'lines': [{'key': 'a4-one', 'relatedId': artifact.id, 'unit': 'SHEET',
                         'quantity': 1, 'unitPriceCents': 10, 'amountCents': 10,
                         'service': 'A4'}],
              'discounts': [], 'discountAllocations': [], 'selectionMode': 'AUTO',
              'selectedPromotionType': 'NONE', 'deliveryFeeCents': 0,
              'volumeDiscountCandidate': None,
              'selectedEligibleSubtotalCents': 0, 'checkoutAccountId': None})
    db.add(quote)
    db.flush()
    prep.quote_id = quote.id
    location = Location(name='并发测试楼', x=0.5, y=0.5)
    slot = DeliverySlot(
        date=(utcnow() + timedelta(days=2)).date().isoformat(), name='并发时段',
        starts_at=utcnow() + timedelta(days=2),
        ends_at=utcnow() + timedelta(days=2, hours=1), capacity=1)
    db.add_all([location, slot])
    db.flush()
    body = {
        'preparationId': prep.id,
        'quoteId': quote.id,
        'previewAcks': [{'artifactId': artifact.id, 'sha256': artifact.sha256}],
        'contact': {'wechatNickname': '并发用户', 'phone': '13800000000'},
        'locationId': location.id,
        'slotId': slot.id,
        'customerNote': '',
    }
    db.commit()
    return Actor(principal_id=principal.id, guest_principal_id=principal.id), body, slot


def test_slot_archive_waits_for_concurrent_submit_and_returns_slot_in_use(
        db, monkeypatch, submission_fixture, staff_factory):
    """Without a slot lock in submit_order this archives an order's slot.

    submit_order reads the slot, then generates the pickup code before the
    order insert. Pausing at that pre-insert window lets the archive commit
    first unless submit_order holds the slot row lock. With the lock, the
    archive waits, observes the committed PENDING_CONFIRMATION order and
    refuses with SLOT_IN_USE instead of writing archivedAt.
    """
    import app.commerce as commerce
    from app.db import SessionLocal
    from app.delivery import admin_slot_delete
    from app.delivery_schemas import SlotDeleteInput

    actor, body, slot = submission_fixture
    admin = staff_factory(role='ADMIN')
    admin_actor = Actor(staff_id=admin.id, staff_role='ADMIN', capabilities={'*'})

    entered = threading.Event()
    release = threading.Event()
    original_choice = secrets.choice

    def paused_choice(sequence):
        entered.set()
        if not release.wait(10):
            raise AssertionError('test did not release submit_order')
        return original_choice(sequence)

    monkeypatch.setattr(commerce.secrets, 'choice', paused_choice)
    submitted = {}
    archived = {}
    archive_done = threading.Event()

    def submit():
        session = SessionLocal()
        try:
            order = commerce.submit_order(
                session, actor, commerce.OrderSubmit.model_validate(body))
            session.commit()
            submitted['orderId'] = order.id
        except BaseException as error:  # noqa: BLE001 - re-raised by assertion below
            submitted['error'] = error
            session.rollback()
        finally:
            session.close()

    def archive():
        session = SessionLocal()
        try:
            admin_slot_delete(
                slot.id, SlotDeleteInput(expected_version=slot.version, reason='并发归档'),
                db=session, actor=admin_actor)
            session.commit()
            archived['status'] = 200
        except DomainError as error:
            session.rollback()
            archived['code'] = error.code
        except BaseException as error:  # noqa: BLE001 - re-raised by assertion below
            session.rollback()
            archived['error'] = error
        finally:
            archive_done.set()
            session.close()

    submit_thread = threading.Thread(target=submit, name='slot-submit')
    archive_thread = threading.Thread(target=archive, name='slot-archive')
    submit_thread.start()
    try:
        assert entered.wait(5), 'submit_order did not reach its paused hook'
        archive_thread.start()
        blocked = not archive_done.wait(0.5)
        release.set()
    finally:
        release.set()
    submit_thread.join(10)
    archive_thread.join(10)

    assert not submit_thread.is_alive()
    assert not archive_thread.is_alive()
    assert 'error' not in submitted, submitted.get('error')
    assert 'error' not in archived, archived.get('error')
    assert blocked, 'slot archive did not wait for the submit_order slot lock'
    assert archived.get('code') == 'SLOT_IN_USE', archived

    db.expire_all()
    stored = db.get(DeliverySlot, slot.id)
    db.refresh(stored)
    assert stored is not None
    assert not (stored.data or {}).get('archivedAt')
    order = db.get(commerce.Order, submitted['orderId'])
    assert order is not None and order.requested_slot_id == slot.id


def test_slot_archive_waits_for_concurrent_revision_of_new_slot(
        db, monkeypatch, submission_fixture, staff_factory):
    """Revision must lock the new slot before updating the order FK."""
    import app.commerce as commerce
    from app.db import SessionLocal
    from app.delivery import admin_slot_delete
    from app.delivery_schemas import SlotDeleteInput

    actor, body, original_slot = submission_fixture
    admin = staff_factory(role='ADMIN')
    admin_actor = Actor(staff_id=admin.id, staff_role='ADMIN', capabilities={'*'})

    session = SessionLocal()
    try:
        order = commerce.submit_order(
            session, actor, commerce.OrderSubmit.model_validate(body))
        session.commit()
        order_id = order.id
    finally:
        session.close()
    db.expire_all()
    revision_source = db.get(commerce.Order, order_id)
    assert revision_source is not None
    original_version = revision_source.version

    new_slot = DeliverySlot(
        date=(utcnow() + timedelta(days=3)).date().isoformat(), name='修订新时段',
        starts_at=utcnow() + timedelta(days=3),
        ends_at=utcnow() + timedelta(days=3, hours=1), capacity=1)
    db.add(new_slot)
    db.commit()
    db.refresh(new_slot)
    revision_prep = db.get(Preparation, body['preparationId'])
    assert revision_prep is not None
    revision_prep.replaces_order_id = order_id
    revision_prep.expected_order_version = original_version
    db.commit()
    revision_body = {
        **body,
        'slotId': new_slot.id,
        'expectedVersion': original_version,
    }

    entered = threading.Event()
    release = threading.Event()
    original_activate = commerce._activate

    def paused_activate(db, order, prep, quote, artifacts):
        entered.set()
        if not release.wait(10):
            raise AssertionError('test did not release revise_order')
        return original_activate(db, order, prep, quote, artifacts)

    monkeypatch.setattr(commerce, '_activate', paused_activate)
    revised = {}
    archived = {}
    archive_done = threading.Event()

    def revise():
        session = SessionLocal()
        try:
            commerce.revise_order(
                order_id,
                commerce.RevisionSubmit.model_validate(revision_body),
                db=session, actor=actor)
            session.commit()
            revised['ok'] = True
        except BaseException as error:  # noqa: BLE001 - re-raised by assertion below
            session.rollback()
            revised['error'] = error
        finally:
            session.close()

    def archive():
        session = SessionLocal()
        try:
            admin_slot_delete(
                new_slot.id,
                SlotDeleteInput(expected_version=new_slot.version, reason='并发归档'),
                db=session, actor=admin_actor)
            session.commit()
            archived['status'] = 200
        except DomainError as error:
            session.rollback()
            archived['code'] = error.code
        except BaseException as error:  # noqa: BLE001 - re-raised by assertion below
            session.rollback()
            archived['error'] = error
        finally:
            archive_done.set()
            session.close()

    revise_thread = threading.Thread(target=revise, name='slot-revise')
    archive_thread = threading.Thread(target=archive, name='slot-archive')
    revise_thread.start()
    try:
        ready = entered.wait(5)
        if not ready:
            revise_thread.join(10)
            assert False, f'revise_order did not reach its paused hook: {revised!r}'
        archive_thread.start()
        blocked = not archive_done.wait(0.5)
        release.set()
    finally:
        release.set()
    revise_thread.join(10)
    archive_thread.join(10)

    assert not revise_thread.is_alive()
    assert not archive_thread.is_alive()
    assert 'error' not in revised, revised.get('error')
    assert 'error' not in archived, archived.get('error')
    assert blocked, 'slot archive did not wait for the revise_order slot lock'
    assert archived.get('code') == 'SLOT_IN_USE', archived

    db.expire_all()
    stored = db.get(DeliverySlot, new_slot.id)
    assert stored is not None
    assert not (stored.data or {}).get('archivedAt')
    updated = db.get(commerce.Order, order_id)
    assert updated is not None and updated.requested_slot_id == new_slot.id
