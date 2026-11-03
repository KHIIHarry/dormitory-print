"""Closed-loop delivery acceptance tests: windows, sealing and auto-batching."""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import (
    Artifact,
    Asset,
    Campus,
    DeliveryAssignment,
    DeliveryBatch,
    DeliveryGroup,
    DeliverySlot,
    Location,
    Order,
    Preparation,
    Principal,
    Quote,
    utcnow,
)

APP_MODULES = [
    'auth', 'commerce', 'delivery', 'growth', 'mail', 'media', 'production',
    'after_sales', 'supplementary', 'payroll', 'benefits',
]


@pytest.fixture
def act_as(app):
    from app.core import Actor, actor_dep

    def apply(staff):
        app.dependency_overrides[actor_dep] = lambda: Actor(
            staff_id=staff.id, staff_role=staff.role,
            capabilities=set(staff.capabilities or []))
        return staff

    yield apply
    app.dependency_overrides.pop(actor_dep, None)


def shanghai_day(value):
    from app.delivery_schemas import to_shanghai_date

    return to_shanghai_date(value)


def configured_slot(db, *, capacity=4, campus=None, offset_hours=30, open_offset=-1,
                    cutoff_offset=1, name='测试时段'):
    """A future slot with explicit order-acceptance instants."""
    from tests.auth_helpers import ensure_test_campus
    if campus is None:
        existing = list(db.scalars(select(Campus)))
        campus = existing[0] if len(existing) == 1 else ensure_test_campus(db)
    now = utcnow()
    starts_at = now + timedelta(hours=offset_hours)
    ends_at = starts_at + timedelta(hours=2)
    slot = DeliverySlot(
        campus_id=campus.id, date=shanghai_day(starts_at), name=name,
        starts_at=starts_at, ends_at=ends_at,
        open_at=now + timedelta(hours=open_offset),
        cutoff_at=now + timedelta(hours=cutoff_offset),
        capacity=capacity, occupied=0, enabled=True, data={},
    )
    db.add(slot)
    db.flush()
    return slot


def enable_service(db):
    from app.config import get_config

    for section in ('services', 'announcement'):
        config = get_config(db, section)
        config.data = {**config.data, 'serviceEnabled': True}


def make_world(db, *, capacity=4, campus=True, offset_hours=30):
    from app.config import get_config

    enable_service(db)
    workflow = get_config(db, 'workflow')
    workflow.data = {**workflow.data, 'unpaidTimeoutMinutes': 60}
    prices = get_config(db, 'prices')
    prices.data = {**prices.data, 'bwSingle': 10}

    site = None
    if campus:
        site = Campus(name='东区', theme_color='#C67C4E', selection_enabled=True, sort_order=1)
        db.add(site)
        db.flush()
    location = Location(name='一号楼', x=0.4, y=0.5, enabled=True, map_version=1,
                        campus_id=site.id if site else None)
    db.add(location)
    db.flush()
    slot = configured_slot(db, capacity=capacity, campus=site, offset_hours=offset_hours)

    principal = Principal()
    db.add(principal)
    db.flush()
    source = Asset(principal_id=principal.id, purpose='PRINT_SOURCE', name='loop.pdf',
                   mime='application/pdf', size=100, sha256='a' * 64,
                   storage_key='fixtures/loop-source.pdf', status='READY', pages=1)
    db.add(source)
    db.flush()
    prep = Preparation(
        principal_id=principal.id, status='READY',
        expires_at=utcnow() + timedelta(hours=24),
        input={'files': [{
            'clientFileId': 'file-loop', 'assetId': source.id, 'kind': 'DOCUMENT',
            'segments': [{
                'clientSegmentId': 'segment-loop',
                'ranges': [{'from': 1, 'to': 1}], 'colorMode': 'BW',
                'sides': 'SINGLE', 'flip': 'NONE', 'copies': 1,
            }],
            'excludedPages': [], 'bindingType': 'NONE',
        }]},
        data={})
    db.add(prep)
    db.flush()
    final = Asset(principal_id=principal.id, purpose='FINAL_PDF', name='loop-final.pdf',
                  mime='application/pdf', size=100, sha256='b' * 64,
                  storage_key='fixtures/loop-final.pdf', status='READY', pages=1)
    db.add(final)
    db.flush()
    artifact = Artifact(
        preparation_id=prep.id, asset_id=final.id, client_file_id='file-loop',
        client_segment_id='segment-loop', sha256='b' * 64, pages=1, sheets=1,
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
    db.commit()
    return SimpleNamespace(
        principal=principal, location=location, slot=slot, campus=site,
        prep=prep, artifact=artifact, quote=quote,
    )


def submit(db, world, *, slot=None):
    from app.commerce import OrderSubmit, submit_order
    from app.core import Actor

    slot = slot or world.slot
    actor = Actor(principal_id=world.principal.id, guest_principal_id=world.principal.id)
    body = OrderSubmit.model_validate({
        'preparationId': world.prep.id, 'quoteId': world.quote.id,
        'previewAcks': [{'artifactId': world.artifact.id, 'sha256': world.artifact.sha256}],
        'contact': {'wechatNickname': '闭环用户', 'phone': '13800000000'},
        'locationId': world.location.id, 'slotId': slot.id, 'customerNote': '',
    })
    order = submit_order(db, actor, body)
    db.commit()
    return order


def settle(db, order, actor):
    from app.commerce import ConfirmPayment, settle_order
    from app.core import lock

    locked = lock(db, Order, order.id, order.version)
    result = settle_order(db, actor, locked, ConfirmPayment(
        expected_version=locked.version, content_revision=locked.content_revision,
        quote_id=locked.quote_id, received_cents=locked.total_cents,
        settlement_kind='CASH_MANUAL', note='测试收款'))
    db.commit()
    return result


def test_slot_create_requires_order_acceptance_window(db, api, client, act_as, staff_factory):
    act_as(staff_factory(role='ADMIN'))
    from tests.auth_helpers import ensure_test_campus
    campus = ensure_test_campus(db)
    db.commit()
    starts = utcnow() + timedelta(days=2)
    response = api(client, '/admin/slots', {
        'campusId': campus.id,
        'date': shanghai_day(starts), 'name': '缺失规则时段',
        'start': '10:00', 'end': '12:00', 'capacity': 4, 'enabled': True,
    })
    assert response.status_code == 422, response.text
    assert response.json()['error']['code'] == 'SLOT_CONFIG_REQUIRED'


def test_slot_view_reports_open_cutoff_and_acceptance_status(db, api, client, act_as, staff_factory):
    act_as(staff_factory(role='ADMIN'))
    slot = configured_slot(db, capacity=3)
    db.commit()
    listed = api(client, '/admin/slots', None, method='GET')
    assert listed.status_code == 200, listed.text
    row = next(item for item in listed.json()['data']['items'] if item['id'] == slot.id)
    assert row['openAt'] and row['cutoffAt']
    assert row['acceptanceStatus'] == 'OPEN'
    assert row['selectable'] is True
    assert row['remaining'] == 3
    assert row['openAt'] < row['cutoffAt'] < row['startsAt'] < row['endsAt']


def test_public_slots_expose_only_open_configured_slots(db, api, client, admin_client):
    enable_service(db)
    open_slot = configured_slot(db, name='可预约')
    cutoff_slot = configured_slot(db, name='已截单', open_offset=-3, cutoff_offset=-1,
                                  offset_hours=30)
    missing = DeliverySlot(date=shanghai_day(utcnow() + timedelta(days=2)), name='待补配置',
                           starts_at=utcnow() + timedelta(days=2),
                           ends_at=utcnow() + timedelta(days=2, hours=2),
                           capacity=3, occupied=0, enabled=True, data={})
    db.add(missing)
    db.commit()
    response = api(client, '/public/slots', None, method='GET')
    assert response.status_code == 200, response.text
    ids = [item['id'] for item in response.json()['data']['items']]
    assert open_slot.id in ids
    assert cutoff_slot.id not in ids
    assert missing.id not in ids
    assert all(item['selectable'] for item in response.json()['data']['items'])


def test_submit_rejects_slot_after_cutoff_and_reserves_no_seat(db):
    world = make_world(db)
    slot = world.slot
    slot.cutoff_at = utcnow() - timedelta(minutes=1)
    db.commit()
    from app.commerce import OrderSubmit, submit_order
    from app.core import Actor, DomainError

    actor = Actor(principal_id=world.principal.id, guest_principal_id=world.principal.id)
    body = OrderSubmit.model_validate({
        'preparationId': world.prep.id, 'quoteId': world.quote.id,
        'previewAcks': [{'artifactId': world.artifact.id, 'sha256': world.artifact.sha256}],
        'contact': {'wechatNickname': '闭环用户', 'phone': '13800000000'},
        'locationId': world.location.id, 'slotId': slot.id, 'customerNote': '',
    })
    with pytest.raises(DomainError) as error:
        submit_order(db, actor, body)
    assert error.value.code == 'SLOT_CUTOFF'
    db.rollback()
    assert db.scalar(select(Order.id).where(Order.principal_id == world.principal.id)) is None
    assert db.get(DeliverySlot, slot.id).occupied == 0


def test_settlement_creates_one_auto_group_batch_and_active_assignment(
        db, admin_client):
    admin = admin_client.staff
    world = make_world(db)
    order = submit(db, world)
    settle(db, order, admin_client_actor(admin))

    group = db.scalar(select(DeliveryGroup).where(DeliveryGroup.slot_id == world.slot.id))
    assert group is not None and group.campus_id == world.campus.id
    batch = db.scalar(select(DeliveryBatch).where(DeliveryBatch.group_id == group.id))
    assert batch is not None
    assert batch.is_regular is True
    assert batch.source == 'AUTO'
    assert batch.status == 'PLANNED'
    assert batch.courier_id is None
    assert batch.slot_id == world.slot.id and batch.campus_id == world.campus.id
    assert world.slot.name in batch.name and world.campus.name in batch.name
    assignment = db.scalar(select(DeliveryAssignment).where(
        DeliveryAssignment.order_id == order.id, DeliveryAssignment.active.is_(True)))
    assert assignment is not None and assignment.batch_id == batch.id
    db.refresh(order)
    assert order.confirmed_slot_id == world.slot.id


def test_same_group_joins_and_other_campus_gets_its_own_batch(db, admin_client):
    admin = admin_client.staff
    world = make_world(db, capacity=4)
    first = submit(db, world)
    settle(db, first, admin_client_actor(admin))

    other_campus = Campus(name='西区', theme_color='#C67C4E', selection_enabled=True, sort_order=2)
    db.add(other_campus)
    db.flush()
    west = Location(name='二号楼', x=0.6, y=0.5, enabled=True, map_version=1,
                    campus_id=other_campus.id)
    db.add(west)
    db.flush()
    west_slot = configured_slot(db, campus=other_campus)
    west_slot.starts_at, west_slot.ends_at = world.slot.starts_at, world.slot.ends_at
    west_slot.open_at, west_slot.cutoff_at = world.slot.open_at, world.slot.cutoff_at
    west_order = Order(
        principal_id=world.principal.id, pickup_code='WEST01', status='PENDING_CONFIRMATION',
        preparation_id=world.prep.id, quote_id=world.quote.id, total_cents=10,
        nickname='西区用户', phone='13800000001', location_id=west.id,
        requested_slot_id=west_slot.id, close_at=utcnow() + timedelta(hours=24), data={})
    db.add(west_order)
    db.commit()
    settle(db, west_order, admin_client_actor(admin))

    groups = list(db.scalars(select(DeliveryGroup).where(DeliveryGroup.slot_id == world.slot.id)))
    assert {group.campus_id for group in groups} == {world.campus.id}
    assert db.scalar(select(DeliveryGroup).where(DeliveryGroup.slot_id == west_slot.id)).campus_id == other_campus.id
    assert world.slot.occupied == west_slot.occupied == 1
    batches = list(db.scalars(select(DeliveryBatch).where(DeliveryBatch.slot_id.in_([world.slot.id, west_slot.id]))))
    assert len(batches) == 2
    assert all(batch.is_regular for batch in batches)

    second = Order(
        principal_id=world.principal.id, pickup_code='EAST02', status='PENDING_CONFIRMATION',
        preparation_id=world.prep.id, quote_id=world.quote.id, total_cents=10,
        nickname='东区用户二', phone='13800000003', location_id=world.location.id,
        requested_slot_id=world.slot.id, close_at=utcnow() + timedelta(hours=24), data={})
    db.add(second)
    db.commit()
    settle(db, second, admin_client_actor(admin))
    east_group = next(group for group in groups if group.campus_id == world.campus.id)
    east_batch = db.scalar(select(DeliveryBatch).where(DeliveryBatch.group_id == east_group.id))
    assert db.scalar(select(DeliveryAssignment).where(
        DeliveryAssignment.order_id == second.id)).batch_id == east_batch.id


def test_legacy_unconfigured_slot_requires_reconciliation_before_settlement(db, admin_client):
    world = make_world(db)
    slot = world.slot
    slot.open_at = None
    slot.cutoff_at = None
    db.commit()
    # Legacy orders were created before the acceptance rules existed.
    order = Order(
        principal_id=world.principal.id, pickup_code='LEGACY1',
        status='PENDING_CONFIRMATION', preparation_id=world.prep.id,
        quote_id=world.quote.id, total_cents=10, nickname='历史用户',
        phone='13800000002', location_id=world.location.id,
        requested_slot_id=slot.id, close_at=utcnow() + timedelta(hours=24), data={})
    db.add(order)
    db.commit()
    from app.core import DomainError
    with pytest.raises(DomainError) as error:
        settle(db, order, admin_client_actor(admin_client.staff))
    assert error.value.code == 'SLOT_CONFIG_MISSING'
    assert db.scalar(select(DeliveryBatch).where(DeliveryBatch.slot_id == slot.id)) is None


def test_slot_seal_preview_and_commit_blocks_new_orders_and_publish(db, api, client,
                                                                    act_as, staff_factory):
    act_as(staff_factory(role='ADMIN'))
    slot = configured_slot(db, capacity=2)
    db.commit()
    preview = api(client, f'/admin/slots/{slot.id}/seal/preview',
                  {'expectedVersion': slot.version})
    assert preview.status_code == 200, preview.text
    plan = preview.json()['data']
    assert plan['impact']['pendingApplications'] == 0
    assert plan['blockers'] == []
    commit = api(client, f'/admin/slots/{slot.id}/seal',
                 {'expectedVersion': slot.version, 'planId': plan['planId'],
                  'reason': '提前结束接单'})
    assert commit.status_code == 200, commit.text
    db.expire_all()
    stored = db.get(DeliverySlot, slot.id)
    assert stored.sealed_at is not None and stored.seal_reason == '提前结束接单'
    public = api(client, '/public/slots', None, method='GET')
    assert all(item['id'] != slot.id for item in public.json()['data']['items'])
    again = api(client, f'/admin/slots/{slot.id}/seal',
                {'expectedVersion': stored.version, 'planId': plan['planId'],
                 'reason': '重复封批'})
    assert again.status_code == 409, again.text


def admin_client_actor(staff):
    from app.core import Actor

    return Actor(staff_id=staff.id, staff_role='ADMIN', capabilities={'*'})


def test_paid_order_reschedule_needs_customer_confirmation_and_moves_capacity(
        db, admin_client):
    from app.core import Actor
    from app.delivery_plan import (confirm_reschedule, propose_reschedule,
                                   reject_reschedule)
    from app.models import DeliveryChange, DeliveryNotice

    admin = admin_client.staff
    world = make_world(db, capacity=4)
    order = submit(db, world)
    settle(db, order, admin_client_actor(admin))
    target = configured_slot(db, capacity=3, campus=world.campus,
                             offset_hours=44, name='调整后时段')
    db.commit()

    actor = admin_client_actor(admin)
    change = propose_reschedule(db, actor, order, target, '配送线路调整')
    db.commit()
    assert change.status == 'PENDING'
    stored = db.get(Order, order.id)
    assert stored.confirmed_slot_id == world.slot.id
    assert stored.data['slotProposal']['slotId'] == target.id
    notice = db.scalar(select(DeliveryNotice).where(DeliveryNotice.change_id == change.id))
    assert notice is not None and notice.status == 'NO_EMAIL'

    with pytest.raises(Exception) as premature:
        confirm_reschedule(db, Actor(principal_id=world.principal.id), stored,
                           world.slot.id)
    assert getattr(premature.value, 'code', '') == 'SLOT_PROPOSAL_MISMATCH'
    db.rollback()

    confirm_reschedule(db, Actor(principal_id=world.principal.id),
                       db.get(Order, order.id), target.id)
    db.commit()
    stored = db.get(Order, order.id)
    assert stored.confirmed_slot_id == target.id
    assert stored.data.get('slotProposal') is None
    assert db.get(DeliverySlot, world.slot.id).occupied == 0
    assert db.get(DeliverySlot, target.id).occupied == 1
    assignment = db.scalar(select(DeliveryAssignment).where(
        DeliveryAssignment.order_id == order.id, DeliveryAssignment.active.is_(True)))
    target_batch = db.scalar(select(DeliveryBatch).where(
        DeliveryBatch.slot_id == target.id,
        DeliveryBatch.campus_id == world.campus.id))
    assert assignment.batch_id == target_batch.id
    change = db.get(DeliveryChange, change.id)
    assert change.status == 'APPLIED' and change.confirmation_required is False

    # A rejection never moves the confirmed arrangement or capacity.
    second = configured_slot(db, capacity=2, campus=world.campus,
                             offset_hours=50, name='再次调整')
    db.commit()
    propose_reschedule(db, actor, stored, second, '再次调整')
    db.commit()
    reject_reschedule(db, Actor(principal_id=world.principal.id), stored, '保持原安排')
    db.commit()
    assert db.get(Order, order.id).confirmed_slot_id == target.id
    assert db.get(DeliverySlot, second.id).occupied == 0


def test_eta_reschedule_updates_undelivered_assignment_and_keeps_batch_status(
        db, admin_client, api, client, act_as):
    admin = admin_client.staff
    act_as(admin)
    world = make_world(db)
    order = submit(db, world)
    settle(db, order, admin_client_actor(admin))
    batch = db.scalar(select(DeliveryBatch).where(DeliveryBatch.slot_id == world.slot.id))
    new_start = batch.starts_at + timedelta(minutes=30)
    preview = api(client, '/admin/batch-operations/preview', {
        'action': 'RESCHEDULE', 'sourceBatchIds': [batch.id],
        'versions': {batch.id: batch.version},
        'payload': {'startsAt': new_start.isoformat(),
                    'endsAt': (new_start + timedelta(hours=2)).isoformat(),
                    'reason': '前序配送延迟'},
    })
    assert preview.status_code == 200, preview.text
    plan = preview.json()['data']
    assert plan['blockers'] == []
    result = api(client, '/admin/batch-operations/commit', {'planId': plan['planId']})
    assert result.status_code == 200, result.text
    db.expire_all()
    stored = db.get(DeliveryBatch, batch.id)
    assert stored.status == 'PLANNED'
    assert stored.starts_at == new_start.replace(microsecond=0) or stored.starts_at == new_start
    from app.models import DeliveryChange
    change = db.scalar(select(DeliveryChange).where(
        DeliveryChange.order_id == order.id,
        DeliveryChange.kind == 'ETA_UPDATED'))
    assert change is not None and change.status == 'APPLIED'


def test_batch_publish_requires_seal_for_slot_linked_auto_batch(db, api, client, act_as, staff_factory):
    world = make_world(db, capacity=4)
    admin = staff_factory(role='ADMIN', capabilities=['payment.manage', 'delivery.manage'])
    order = submit(db, world)
    settle(db, order, admin_client_actor(admin))
    act_as(admin)
    listed = api(client, '/admin/batches', None, method='GET')
    assert listed.status_code == 200, listed.text
    items = listed.json()['data']['items']
    batch = next(item for item in items if item['slotId'] == world.slot.id)
    courier = staff_factory(role='COURIER')
    assigned = api(client, f"/admin/batches/{batch['id']}", {
        'expectedVersion': batch['version'], 'courierId': courier.id,
        'reason': '指派配送员'}, method='PATCH')
    assert assigned.status_code == 200, assigned.text
    batch = assigned.json()['data']
    blocked = api(client, f"/admin/batches/{batch['id']}/publish", {
        'expectedVersion': batch['version'], 'reason': '准备发布'})
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()['error']['code'] == 'BATCH_NOT_SEALED'

    preview = api(client, f'/admin/slots/{world.slot.id}/seal/preview', {})
    assert preview.status_code == 200, preview.text
    plan = preview.json()['data']
    sealed = api(client, f'/admin/slots/{world.slot.id}/seal', {
        'expectedVersion': plan['slot']['version'], 'planId': plan['planId'],
        'reason': '运力已满，提前封批'})
    assert sealed.status_code == 200, sealed.text
    current = api(client, f"/admin/batches/{batch['id']}", None, method='GET')
    assert current.status_code == 200, current.text
    batch = current.json()['data']
    published = api(client, f"/admin/batches/{batch['id']}/publish", {
        'expectedVersion': batch['version'], 'reason': '封批后发布'})
    assert published.status_code == 200, published.text


def test_slot_window_edit_is_locked_after_an_order_exists(db, api, client, act_as, staff_factory):
    world = make_world(db, capacity=4)
    order = submit(db, world)
    assert order.status == 'PENDING_CONFIRMATION'
    admin = staff_factory(role='ADMIN', capabilities=['delivery.manage'])
    act_as(admin)
    response = api(client, f'/admin/slots/{world.slot.id}', {
        'expectedVersion': world.slot.version,
        'date': world.slot.date, 'start': '21:00', 'end': '22:00',
        'reason': '测试改期'}, method='PATCH')
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'SLOT_TIME_LOCKED'


def test_concurrent_settlements_share_one_group_and_one_regular_batch(db, staff_factory):
    import threading

    from app.commerce import ConfirmPayment, settle_order
    from app.core import Actor, lock
    from app.db import SessionLocal

    world = make_world(db, capacity=4)
    admin = staff_factory(role='ADMIN', capabilities=['payment.manage'])
    first = submit(db, world)
    second = Order(
        principal_id=first.principal_id, checkout_account_id=None,
        pickup_code='CONCURRENT-2', status='PENDING_CONFIRMATION',
        nickname='并发用户', phone='13800000001', note='',
        content_revision=0, preparation_id=first.preparation_id,
        quote_id=first.quote_id, total_cents=first.total_cents,
        location_id=first.location_id, requested_slot_id=first.requested_slot_id,
        close_at=utcnow() + timedelta(minutes=60),
        data={'selectedPromotionType': 'NONE'},
    )
    db.add(second)
    db.commit()
    order_ids = [first.id, second.id]
    barrier = threading.Barrier(2)
    outcomes = []

    def worker(order_id):
        session = SessionLocal()
        try:
            barrier.wait(timeout=10)
            locked = lock(session, Order, order_id)
            actor = Actor(staff_id=admin.id, staff_role='ADMIN',
                          capabilities={'payment.manage'})
            settle_order(session, actor, locked, ConfirmPayment(
                expected_version=locked.version,
                content_revision=locked.content_revision,
                quote_id=locked.quote_id,
                received_cents=locked.total_cents,
                settlement_kind='CASH_MANUAL', note='并发测试'))
            session.commit()
            outcomes.append('OK')
        except Exception as exc:  # pragma: no cover - surfaced below
            session.rollback()
            outcomes.append(type(exc).__name__)
        finally:
            session.close()

    threads = [threading.Thread(target=worker, args=(order_id,)) for order_id in order_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(outcomes) == ['OK', 'OK'], outcomes
    db.expire_all()
    group = db.scalar(select(DeliveryGroup).where(
        DeliveryGroup.slot_id == world.slot.id))
    assert group is not None
    batches = list(db.scalars(select(DeliveryBatch).where(
        DeliveryBatch.group_id == group.id,
        DeliveryBatch.is_regular.is_(True))))
    assert len(batches) == 1, [batch.name for batch in batches]
    assignments = list(db.scalars(select(DeliveryAssignment).where(
        DeliveryAssignment.batch_id == batches[0].id,
        DeliveryAssignment.active.is_(True))))
    assert len(assignments) == 2
    assert db.get(DeliverySlot, world.slot.id).occupied == 2
