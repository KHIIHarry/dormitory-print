"""Checkout and money may never cross a delivery slot's explicit campus."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.core import Actor, DomainError
from app.models import Campus, DeliveryAssignment, DeliverySlot, Location, Order, Receipt, utcnow
from test_delivery_closed_loop import APP_MODULES, make_world, submit


@pytest.fixture
def world(db):
    value = make_world(db)
    value.slot.campus_id = value.campus.id
    db.commit()
    return value


def other_campus(db, world):
    campus = Campus(name='西校区', theme_color='#123456', selection_enabled=True)
    db.add(campus)
    db.flush()
    location = Location(name='西校区楼栋', campus_id=campus.id)
    slot = DeliverySlot(campus_id=campus.id, date=world.slot.date, name='西校区同时间',
                        starts_at=world.slot.starts_at, ends_at=world.slot.ends_at,
                        open_at=world.slot.open_at, cutoff_at=world.slot.cutoff_at,
                        capacity=2, occupied=0)
    db.add_all([location, slot])
    db.commit()
    return campus, location, slot


def test_submit_rejects_other_campus_slot_without_order_or_capacity(db, world):
    _, _, foreign = other_campus(db, world)
    with pytest.raises(DomainError) as error:
        submit(db, world, slot=foreign)
    assert error.value.code == 'SLOT_CAMPUS_MISMATCH'
    assert db.scalar(select(func.count(Order.id))) == 0
    assert foreign.occupied == world.slot.occupied == 0


def test_submit_captures_campus_identity_and_name(db, world):
    order = submit(db, world)
    assert order.data['locationSnapshot']['campusId'] == world.campus.id
    assert order.data['requestedSlotSnapshot']['campusId'] == world.campus.id
    assert order.data['requestedSlotSnapshot']['campusName'] == world.campus.name


def test_order_http_response_exposes_safe_historical_location_snapshot(db, world, app, client):
    from app.core import actor_dep
    order = submit(db, world)
    old_name = world.campus.name
    world.campus.name = '改名后的校区'
    world.campus.selection_enabled = False
    db.commit()
    app.dependency_overrides[actor_dep] = lambda: Actor(principal_id=world.principal.id,
                                                       guest_principal_id=world.principal.id)
    response = client.get(f'/api/v1/orders/{order.id}')
    assert response.status_code == 200, response.text
    snapshot = response.json()['data']['locationSnapshot']
    assert snapshot['campusId'] == world.campus.id and snapshot['campusName'] == old_name
    assert snapshot['name'] == world.location.name
    assert 'data' not in response.json()['data']


def test_customer_cannot_change_location_across_campuses(db, world):
    from app.commerce import LocationInput, change_location
    order = submit(db, world)
    _, foreign, _ = other_campus(db, world)
    actor = Actor(principal_id=world.principal.id, guest_principal_id=world.principal.id)
    with pytest.raises(DomainError) as error:
        change_location(order.id, LocationInput(expectedVersion=order.version, locationId=foreign.id),
                        db=db, actor=actor)
    assert error.value.code == 'CAMPUS_CHANGE_REQUIRES_RESCHEDULE'
    assert order.location_id == world.location.id


def test_same_campus_location_change_keeps_campus_snapshot(db, world):
    from app.commerce import LocationInput, change_location
    order = submit(db, world)
    next_location = Location(name='东区新楼栋', campus_id=world.campus.id)
    db.add(next_location)
    db.commit()
    actor = Actor(principal_id=world.principal.id, guest_principal_id=world.principal.id)
    change_location(order.id, LocationInput(expectedVersion=order.version, locationId=next_location.id),
                    db=db, actor=actor)
    assert order.location_id == next_location.id
    assert order.data['locationSnapshot']['campusId'] == world.campus.id


@pytest.mark.parametrize('operation', ['propose', 'agree'])
def test_unpaid_proposals_cannot_use_foreign_campus(db, world, staff_factory, operation):
    from app.commerce import SlotAgreement, SlotProposal, agree_slot, propose_slot
    order = submit(db, world)
    _, _, foreign = other_campus(db, world)
    admin = staff_factory()
    if operation == 'propose':
        actor = Actor(staff_id=admin.id, staff_role='ADMIN', capabilities={'*'})
        body = SlotProposal(expectedVersion=order.version, slotId=foreign.id, reason='调整')
        fn = propose_slot
    else:
        order.data = {**order.data, 'slotProposal': {'slotId': foreign.id, 'confirmed': False}}
        db.commit()
        actor = Actor(principal_id=world.principal.id, guest_principal_id=world.principal.id)
        body = SlotAgreement(expectedVersion=order.version, slotId=foreign.id, confirm=True)
        fn = agree_slot
    with pytest.raises(DomainError) as error:
        fn(order.id, body, db=db, actor=actor)
    assert error.value.code == 'SLOT_CAMPUS_MISMATCH'


@pytest.mark.parametrize('zero', [False, True])
def test_settlement_checks_campus_and_keeps_real_cash_unmatched(db, world, staff_factory, zero):
    from app.commerce import ConfirmPayment, settle_order
    order = submit(db, world)
    _, foreign_location, _ = other_campus(db, world)
    # Represents an imported legacy mismatch that escaped the old address API.
    order.location_id = foreign_location.id
    if zero:
        order.total_cents = 0
    db.commit()
    admin = staff_factory()
    actor = Actor(staff_id=admin.id, staff_role='ADMIN', capabilities={'*'})
    body = ConfirmPayment(expectedVersion=order.version, contentRevision=order.content_revision,
                          quoteId=order.quote_id, receivedCents=order.total_cents,
                          settlementKind='ZERO_PAYABLE' if zero else 'CASH_MANUAL')
    with pytest.raises(DomainError) as error:
        settle_order(db, actor, order, body)
    assert error.value.code == 'SLOT_CAMPUS_MISMATCH'
    db.commit() if error.value.commit else db.rollback()
    stored = db.get(Order, order.id)
    assert stored.status == 'PENDING_CONFIRMATION' and stored.confirmed_slot_id is None
    assert db.get(DeliverySlot, world.slot.id).occupied == 0
    assert db.scalar(select(func.count(DeliveryAssignment.id))) == 0
    receipts = list(db.scalars(select(Receipt)))
    assert len(receipts) == (0 if zero else 1)
    if receipts:
        assert receipts[0].status == 'UNMATCHED'
        assert receipts[0].amount_cents == 10


def test_revision_updates_effective_request_and_invalidates_old_proposal(db, world):
    from app.commerce import RevisionSubmit, revise_order
    order = submit(db, world)
    campus, location, slot = other_campus(db, world)
    world.prep.replaces_order_id = order.id
    world.prep.expected_order_version = order.version
    order.data = {**order.data, 'slotProposal': {'slotId': world.slot.id, 'confirmed': True}}
    db.commit()
    original_request = dict(order.data['requestedSlotSnapshot'])
    actor = Actor(principal_id=world.principal.id, guest_principal_id=world.principal.id)
    body = RevisionSubmit(expectedVersion=order.version, preparationId=world.prep.id,
                          quoteId=world.quote.id,
                          previewAcks=[{'artifactId': world.artifact.id, 'sha256': world.artifact.sha256}],
                          contact={'wechatNickname': '修订用户', 'phone': '13800000000'},
                          locationId=location.id, slotId=slot.id)
    revise_order(order.id, body, db=db, actor=actor)
    assert order.requested_slot_id == slot.id
    assert order.data['requestedSlotSnapshot']['campusId'] == campus.id
    assert order.data['locationSnapshot']['campusId'] == campus.id
    assert not order.data.get('slotProposal')
    assert order.data['previousRequestedSlots'][-1] == original_request
