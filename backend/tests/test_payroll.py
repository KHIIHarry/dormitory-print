"""Payroll module tests: pure rules first, then real PostgreSQL behaviour."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core import Actor, DomainError, actor_dep
from app.models import (
    DeliveryAssignment,
    DeliveryBatch,
    DeliveryProof,
    Order,
    Principal,
    Refund,
    Staff,
    SupplementaryCharge,
    SupplementaryMovement,
    WageEntry,
    WagePolicy,
    WageSettlement,
    WageSettlementItem,
    utcnow,
)
from app.supplementary import create_charge, record_receipt, record_refund
from app.supplementary_schemas import ChargeInput, ReceiptInput, RefundInput
from app.payroll import (
    SUPPLEMENTARY_SOURCE_AVAILABLE,
    SUPPLEMENTARY_SOURCE_PREFIX,
    apply_retroactive_policy,
    apply_supplementary_eligible,
    create_manual_entry,
    extra_eligible_cents,
    on_delivery,
    on_refund,
    on_supplementary_change,
    period_for,
    record_settlement,
    resolve_policy,
    signed_rate_amount,
    staff_ledger,
    wage_amount,
)
from app.payroll_schemas import (
    ManualEntryInput,
    PolicyInput,
    SettlementInput,
)

APP_MODULES = ['payroll']


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------

def test_period_uses_chinese_month_in_asia_shanghai():
    assert period_for(datetime(2026, 10, 31, 16, 30, tzinfo=timezone.utc)) == '2026-11'
    assert period_for(datetime(2026, 10, 31, 15, 59, tzinfo=timezone.utc)) == '2026-10'
    assert period_for(datetime(2026, 1, 1, 0, 0)) == '2026-01'


def test_percent_wage_floors_to_whole_cent():
    assert wage_amount('PERCENT', 0, 333, 1000) == 33
    assert wage_amount('PERCENT', 0, 333, 99) == 3
    assert wage_amount('PERCENT', 0, 10000, 1200) == 1200
    assert wage_amount('PERCENT', 0, 0, 1200) == 0
    assert wage_amount('FIXED', 250, 9999, 1200) == 250
    assert wage_amount('FIXED', 0, 0, 0) == 0


def test_signed_rate_amount_reverses_without_overpaying_rounding():
    assert signed_rate_amount(333, 1000) == 33
    assert signed_rate_amount(333, -1000) == -33
    assert signed_rate_amount(333, 0) == 0


def test_extra_eligible_cents_respects_include_and_never_negative():
    assert extra_eligible_cents(1200, 300) == 900
    assert extra_eligible_cents(1200, 300, include_in_payroll=False) == 0
    assert extra_eligible_cents(300, 1200) == 0
    assert extra_eligible_cents(0, 0) == 0


def test_policy_input_validates_mode_rate_and_fixed_cents():
    valid = PolicyInput(mode='PERCENT', fixedCents=0, rateBps=333, enabled=True)
    assert valid.rate_bps == 333
    with pytest.raises(ValidationError):
        PolicyInput(mode='PERCENT', fixedCents=0, rateBps=10001)
    with pytest.raises(ValidationError):
        PolicyInput(mode='FIXED', fixedCents=-1, rateBps=0)
    with pytest.raises(ValidationError):
        PolicyInput(mode='PIECE', fixedCents=0, rateBps=0)


def test_policy_input_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        PolicyInput(mode='FIXED', fixedCents=100, rateBps=0, enabled=True, scopeKey='X')


def test_manual_entry_requires_reason_and_period_shape():
    with pytest.raises(ValidationError):
        ManualEntryInput(staffId='s1', amountCents=100, reason='')
    with pytest.raises(ValidationError):
        ManualEntryInput(staffId='s1', amountCents=100, reason='   ')
    with pytest.raises(ValidationError):
        ManualEntryInput(staffId='s1', amountCents=100, period='2026-13', reason='补发')
    with pytest.raises(ValidationError):
        ManualEntryInput(staffId='s1', amountCents=100, reason='补发', extra=1)
    assert ManualEntryInput(staffId='s1', amountCents=100, reason='补发').reason == '补发'


def test_settlement_input_requires_reason_period_and_unique_entries():
    with pytest.raises(ValidationError):
        SettlementInput(staffId='s1', period='2026-10', reason='')
    with pytest.raises(ValidationError):
        SettlementInput(staffId='s1', period='2026-10-01', reason='发放')
    with pytest.raises(ValidationError):
        SettlementInput(staffId='s1', period='2026-10', entryIds=['e1', 'e1'], reason='发放')
    payload = SettlementInput(staffId='s1', period='2026-10', reason='发放')
    assert payload.entry_ids is None


# ---------------------------------------------------------------------------
# Fixtures/helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def act_as(app):
    def apply(staff, capabilities=None):
        app.dependency_overrides[actor_dep] = lambda: Actor(
            staff_id=staff.id,
            staff_role=staff.role,
            capabilities=set(capabilities if capabilities is not None else (staff.capabilities or [])),
        )
        return staff
    yield apply
    app.dependency_overrides.pop(actor_dep, None)


def add_policy(db, staff=None, *, mode='FIXED', fixed=0, rate=0, enabled=True):
    scope_key = f'STAFF:{staff.id}' if staff else 'DEFAULT'
    row = db.scalar(select(WagePolicy).where(WagePolicy.scope_key == scope_key))
    if row is None:
        row = WagePolicy(
            staff_id=staff.id if staff else None,
            scope_key=scope_key,
            mode=mode,
            fixed_cents=fixed,
            rate_bps=rate,
            enabled=enabled,
            data={},
        )
        db.add(row)
    else:
        row.mode = mode
        row.fixed_cents = fixed
        row.rate_bps = rate
        row.enabled = enabled
    db.commit()
    return row


def make_courier(staff_factory, name='配送员'):
    row = staff_factory(role='COURIER', capabilities=[],
                        username=f'courier-{uuid4().hex[:10]}')
    row.name = name
    return row


def make_delivered(db, staff_factory, courier=None, *, total=10000, refunded=0,
                   delivered_at=None, batch=None):
    from tests.auth_helpers import make_order

    courier = courier or make_courier(staff_factory)
    delivered_at = delivered_at or datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
    principal = Principal()
    db.add(principal)
    db.flush()
    order = make_order(db, principal.id, status='DELIVERED', total_cents=total)
    order.cash_received_cents = total
    order.refunded_cents = refunded
    if batch is None:
        batch = DeliveryBatch(
            name=f'批次-{uuid4().hex[:6]}',
            courier_id=courier.id,
            starts_at=delivered_at - timedelta(hours=2),
            ends_at=delivered_at + timedelta(hours=2),
            status='PUBLISHED',
            data={},
        )
        db.add(batch)
        db.flush()
    assignment = DeliveryAssignment(order_id=order.id, batch_id=batch.id, courier_id=courier.id,
                                    status='DELIVERED', active=True, data={})
    db.add(assignment)
    db.flush()
    proof = DeliveryProof(order_id=order.id, assignment_id=assignment.id, active=True,
                          data={'serverDeliveredAt': delivered_at.isoformat(),
                                'operatorId': courier.id})
    db.add(proof)
    db.flush()
    order.data = {'deliveryProofId': proof.id, 'deliveredAt': delivered_at.isoformat(),
                  'delivery': {'assignmentId': assignment.id, 'batchId': batch.id,
                               'courierId': courier.id, 'deliveredAt': delivered_at.isoformat()}}
    db.commit()
    return order, assignment, proof, batch


def admin_actor(staff):
    return Actor(staff_id=staff.id, staff_role='ADMIN', capabilities={'*'})


def entry_for(db, source_key):
    return db.scalar(select(WageEntry).where(WageEntry.source_key == source_key))


def entries_for(db, staff_id):
    return list(db.scalars(select(WageEntry).where(WageEntry.staff_id == staff_id)
                           .order_by(WageEntry.created_at, WageEntry.id)))


# ---------------------------------------------------------------------------
# Delivery attribution
# ---------------------------------------------------------------------------

def test_delivery_fixed_policy_creates_one_frozen_base_entry(db, staff_factory):
    courier = make_courier(staff_factory)
    add_policy(db, mode='FIXED', fixed=250)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier, total=10000)

    entry = on_delivery(db, order, assignment, proof)
    db.commit()

    assert entry.kind == 'DELIVERY'
    assert entry.staff_id == courier.id
    assert entry.order_id == order.id
    assert entry.assignment_id == assignment.id
    assert entry.amount_cents == 250
    assert entry.period == '2026-10'
    assert entry.snapshot['policyMode'] == 'FIXED'
    assert entry.snapshot['fixedCents'] == 250
    assert entry.snapshot['originalBaseCashCents'] == 10000
    assert entry.snapshot['extraEligibleCents'] == 0
    assert entry.snapshot['staffId'] == courier.id
    assert entry.snapshot['orderId'] == order.id

    again = on_delivery(db, order, assignment, proof)
    db.commit()
    assert again.id == entry.id
    assert db.scalar(select(func.count()).select_from(WageEntry)
                     .where(WageEntry.source_key == f'DELIVERY:{order.id}')) == 1


def test_delivery_percent_uses_net_collected_and_staff_override(db, staff_factory):
    courier = make_courier(staff_factory)
    add_policy(db, mode='FIXED', fixed=999)
    add_policy(db, courier, mode='PERCENT', rate=333)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier, total=10000, refunded=1000)

    entry = on_delivery(db, order, assignment, proof)
    db.commit()

    policy, source = resolve_policy(db, courier.id)
    assert source == 'STAFF'
    assert policy.mode == 'PERCENT'
    assert entry.amount_cents == 299  # floor(333 * 9,000 / 10,000)
    assert entry.snapshot['basisCents'] == 9000
    assert entry.snapshot['rateBps'] == 333


def test_delivery_without_policy_records_needs_configuration_without_blocking(db, staff_factory):
    courier = make_courier(staff_factory)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier)

    marker = on_delivery(db, order, assignment, proof)
    db.commit()

    assert marker.kind == 'NEEDS_CONFIGURATION'
    assert marker.amount_cents == 0
    assert marker.staff_id == courier.id
    assert marker.source_key == f'DELIVERY:{order.id}'
    assert marker.snapshot['originalBaseCashCents'] == 10000

    # A re-run before configuration returns the same durable marker.
    assert on_delivery(db, order, assignment, proof).id == marker.id


def test_delivery_period_uses_proof_server_time_in_shanghai(db, staff_factory):
    courier = make_courier(staff_factory)
    add_policy(db, mode='FIXED', fixed=100)
    order, assignment, proof, _ = make_delivered(
        db, staff_factory, courier, delivered_at=datetime(2026, 10, 31, 16, 30, tzinfo=timezone.utc))

    entry = on_delivery(db, order, assignment, proof)
    db.commit()
    assert entry.period == '2026-11'


def test_delivery_accepts_server_delivery_time_and_staff_id_alias(db, staff_factory):
    courier = make_courier(staff_factory)
    add_policy(db, mode='FIXED', fixed=100)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier)
    assignment.courier_id = None
    proof.data = {'serverDeliveryTime': '2026-10-31T16:30:00+00:00', 'staffId': courier.id}
    db.commit()

    entry = on_delivery(db, order, assignment, proof)
    db.commit()
    assert entry.staff_id == courier.id
    assert entry.period == '2026-11'


def test_rework_and_free_redelivery_do_not_create_a_second_base_wage(db, staff_factory):
    courier = make_courier(staff_factory)
    add_policy(db, mode='FIXED', fixed=250)
    order, assignment, proof, batch = make_delivered(db, staff_factory, courier)

    first = on_delivery(db, order, assignment, proof)
    rework = DeliveryAssignment(order_id=order.id, batch_id=batch.id, courier_id=courier.id,
                                status='DELIVERED', active=False, data={})
    db.add(rework)
    db.flush()
    rework_proof = DeliveryProof(order_id=order.id, assignment_id=rework.id, active=False,
                                 data={'serverDeliveredAt': utcnow().isoformat(), 'operatorId': courier.id})
    db.add(rework_proof)
    db.commit()

    second = on_delivery(db, order, rework, rework_proof)
    db.commit()
    assert second.id == first.id
    assert len(entries_for(db, courier.id)) == 1


def test_batch_with_two_orders_earns_one_entry_per_order(db, staff_factory):
    courier = make_courier(staff_factory)
    add_policy(db, mode='FIXED', fixed=100)
    first_order, first_assignment, first_proof, batch = make_delivered(db, staff_factory, courier)
    second_order, second_assignment, second_proof, _ = make_delivered(
        db, staff_factory, courier, batch=batch)

    on_delivery(db, first_order, first_assignment, first_proof)
    on_delivery(db, second_order, second_assignment, second_proof)
    db.commit()

    assert [e.order_id for e in entries_for(db, courier.id)] == [first_order.id, second_order.id]
    assert sum(e.amount_cents for e in entries_for(db, courier.id)) == 200


def test_delivery_hook_does_not_pay_an_undelivered_order(db, staff_factory):
    courier = make_courier(staff_factory)
    add_policy(db, mode='FIXED', fixed=100)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier)
    order.status = 'READY_FOR_DELIVERY'
    db.commit()

    assert on_delivery(db, order, assignment, proof) is None
    db.commit()
    assert entries_for(db, courier.id) == []


# ---------------------------------------------------------------------------
# Refund adjustments
# ---------------------------------------------------------------------------

def refund_for(db, order, amount, staff_id, *, created_at=None):
    from app.models import AfterSale

    after_sale = AfterSale(order_id=order.id, source='ADMIN', actor_id=staff_id,
                           responsibility='MERCHANT', solution='REFUND', status='APPROVED',
                           reason='测试退款', suggested_cents=amount,
                           approved_cents=amount, adjustment_basis='REFUND', data={})
    db.add(after_sale)
    db.flush()
    row = Refund(order_id=order.id, after_sale_id=after_sale.id, amount_cents=amount,
                 staff_id=staff_id, reason='测试退款', data={})
    if created_at is not None:
        row.created_at = created_at
    db.add(row)
    db.flush()
    return row


def test_percent_refund_uses_original_policy_and_append_only_adjustment(db, staff_factory):
    courier = make_courier(staff_factory)
    admin = staff_factory()
    add_policy(db, mode='PERCENT', rate=333)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier, total=10000)
    base = on_delivery(db, order, assignment, proof)
    db.commit()
    assert base.amount_cents == 333

    order.refunded_cents = 4000
    first = refund_for(db, order, 4000, admin.id)
    adjustment = on_refund(db, order, first)
    db.commit()
    assert adjustment.kind == 'REFUND_ADJUSTMENT'
    assert adjustment.source_key == f'REFUND:{first.id}'
    assert adjustment.amount_cents == -134  # expected 199 - existing 333
    assert adjustment.snapshot['policyMode'] == 'PERCENT'
    assert adjustment.snapshot['rateBps'] == 333

    # Current policy changes must not affect the frozen original policy.
    add_policy(db, mode='FIXED', fixed=999)
    order.refunded_cents = 5000
    second = refund_for(db, order, 1000, admin.id)
    second_adjustment = on_refund(db, order, second)
    db.commit()
    assert second_adjustment.amount_cents == -33  # expected 166 - current 199
    assert second_adjustment.snapshot['rateBps'] == 333

    assert base.amount_cents == 333
    assert [e.amount_cents for e in entries_for(db, courier.id)] == [333, -134, -33]
    assert sum(e.amount_cents for e in entries_for(db, courier.id)) == 166


def test_refund_adjustment_is_unique_per_refund(db, staff_factory):
    courier = make_courier(staff_factory)
    admin = staff_factory()
    add_policy(db, mode='PERCENT', rate=1000)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier, total=10000)
    on_delivery(db, order, assignment, proof)
    db.commit()
    order.refunded_cents = 2000
    refund = refund_for(db, order, 2000, admin.id)

    first = on_refund(db, order, refund)
    second = on_refund(db, order, refund)
    db.commit()
    assert first.id == second.id
    assert db.scalar(select(func.count()).select_from(WageEntry)
                     .where(WageEntry.source_key == f'REFUND:{refund.id}')) == 1


def test_refund_after_settlement_never_edits_paid_rows(db, staff_factory):
    courier = make_courier(staff_factory)
    admin = staff_factory()
    add_policy(db, mode='PERCENT', rate=333)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier, total=10000)
    base = on_delivery(db, order, assignment, proof)
    db.commit()
    admin_actor_value = admin_actor(admin)
    settlement = record_settlement(db, admin_actor_value, courier.id, '2026-10', [base.id], '月结发放')
    db.commit()
    assert settlement.amount_cents == 333

    order.refunded_cents = 4000
    refund = refund_for(db, order, 4000, admin.id)
    adjustment = on_refund(db, order, refund)
    db.commit()

    db.refresh(base)
    db.refresh(settlement)
    assert base.amount_cents == 333
    assert settlement.amount_cents == 333
    assert adjustment.amount_cents == -134
    assert adjustment.id not in {item.entry_id for item in db.scalars(select(WageSettlementItem))}
    assert db.scalar(select(func.count()).select_from(WageSettlement)) == 1

    # The carry-forward negative cannot be paid by itself.
    with pytest.raises(DomainError) as error:
        record_settlement(db, admin_actor_value, courier.id, '2026-10', [adjustment.id], '尝试单发负数')
    assert error.value.code == 'WAGE_NET_NEGATIVE'
    db.rollback()


def test_refund_after_paid_january_lands_in_february_and_offsets_february_payout(
        db, staff_factory):
    courier = make_courier(staff_factory)
    admin = staff_factory()
    actor = admin_actor(admin)
    add_policy(db, mode='PERCENT', rate=333)

    january_order, january_assignment, january_proof, _ = make_delivered(
        db, staff_factory, courier, total=10000,
        delivered_at=datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc))
    january_base = on_delivery(db, january_order, january_assignment, january_proof)
    db.commit()
    january_settlement = record_settlement(
        db, actor, courier.id, '2026-01', [january_base.id], '一月发放')
    db.commit()
    assert january_settlement.amount_cents == 333

    january_order.refunded_cents = 4000
    # Change the current policy before the refund: the correction must still use
    # the policy frozen on the January delivery.
    add_policy(db, mode='FIXED', fixed=999)
    refund = refund_for(
        db, january_order, 4000, admin.id,
        created_at=datetime(2026, 2, 10, 12, 0, tzinfo=timezone.utc))
    adjustment = on_refund(db, january_order, refund)
    db.commit()
    assert adjustment.period == '2026-02'
    assert adjustment.amount_cents == -134
    assert adjustment.snapshot['originalDeliveryPeriod'] == '2026-01'
    assert adjustment.snapshot['basePeriod'] == '2026-01'
    assert adjustment.snapshot['policyId'] == january_base.snapshot['policyId']
    assert adjustment.snapshot['rateBps'] == 333

    add_policy(db, mode='PERCENT', rate=333)
    february_order, february_assignment, february_proof, _ = make_delivered(
        db, staff_factory, courier, total=5000,
        delivered_at=datetime(2026, 2, 5, 12, 0, tzinfo=timezone.utc))
    february_base = on_delivery(db, february_order, february_assignment, february_proof)
    db.commit()
    assert february_base.amount_cents == 166

    ledger = staff_ledger(db, courier.id, '2026-02')
    assert ledger['summary']['netCents'] == 32
    assert {row['id'] for row in ledger['entries']} == {
        february_base.id, adjustment.id}
    assert ledger['summary']['carryForwardCents'] == 0

    settlement = record_settlement(db, actor, courier.id, '2026-02', None, '二月合并发放')
    db.commit()
    assert settlement.amount_cents == 32
    assert {item.entry_id for item in db.scalars(select(WageSettlementItem))} == {
        january_base.id, february_base.id, adjustment.id}
    db.refresh(january_settlement)
    assert january_settlement.amount_cents == 333


def test_default_settlement_consumes_prior_negative_adjustment_carry(
        db, staff_factory):
    courier = make_courier(staff_factory)
    admin = staff_factory()
    actor = admin_actor(admin)
    add_policy(db, mode='PERCENT', rate=333)

    january_order, january_assignment, january_proof, _ = make_delivered(
        db, staff_factory, courier, total=3000,
        delivered_at=datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc))
    january_base = on_delivery(db, january_order, january_assignment, january_proof)
    db.commit()
    record_settlement(db, actor, courier.id, '2026-01', [january_base.id], '一月发放')
    db.commit()

    # The refund happens after January was already paid, so the negative row
    # stays in January and must be carried into February by default.
    january_order.refunded_cents = 1500
    refund = refund_for(
        db, january_order, 1500, admin.id,
        created_at=datetime(2026, 1, 20, 12, 0, tzinfo=timezone.utc))
    adjustment = on_refund(db, january_order, refund)
    db.commit()
    assert adjustment.period == '2026-01'
    assert adjustment.amount_cents == -50

    february_order, february_assignment, february_proof, _ = make_delivered(
        db, staff_factory, courier, total=2000,
        delivered_at=datetime(2026, 2, 5, 12, 0, tzinfo=timezone.utc))
    february_base = on_delivery(db, february_order, february_assignment, february_proof)
    db.commit()

    ledger = staff_ledger(db, courier.id, '2026-02')
    assert ledger['summary']['carryForwardCents'] == -50
    assert ledger['summary']['availableCents'] == 16
    settlement = record_settlement(db, actor, courier.id, '2026-02', None, '二月合并发放')
    db.commit()
    assert settlement.amount_cents == 16
    assert {item.entry_id for item in db.scalars(select(WageSettlementItem))} == {
        january_base.id, february_base.id, adjustment.id}


def test_default_settlement_consumes_all_older_unsettled_entries(
        db, staff_factory):
    courier = make_courier(staff_factory)
    admin = staff_factory()
    actor = admin_actor(admin)
    add_policy(db, mode='PERCENT', rate=333)

    january_order, january_assignment, january_proof, _ = make_delivered(
        db, staff_factory, courier, total=3000,
        delivered_at=datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc))
    january_base = on_delivery(db, january_order, january_assignment, january_proof)
    db.commit()
    record_settlement(db, actor, courier.id, '2026-01', [january_base.id], '一月发放')
    db.commit()

    # A positive correction appended after January was paid is still payable.
    january_bonus = create_manual_entry(
        db, actor, courier.id, 100, '2026-01', '一月补发奖励')
    db.commit()

    january_order.refunded_cents = 1500
    refund = refund_for(
        db, january_order, 1500, admin.id,
        created_at=datetime(2026, 1, 20, 12, 0, tzinfo=timezone.utc))
    january_adjustment = on_refund(db, january_order, refund)
    db.commit()
    assert january_bonus.amount_cents == 100
    assert january_adjustment.amount_cents == -50

    february_order, february_assignment, february_proof, _ = make_delivered(
        db, staff_factory, courier, total=2000,
        delivered_at=datetime(2026, 2, 5, 12, 0, tzinfo=timezone.utc))
    february_base = on_delivery(db, february_order, february_assignment, february_proof)
    db.commit()

    march_future = create_manual_entry(
        db, actor, courier.id, 999, '2026-03', '三月奖励')
    db.commit()

    ledger = staff_ledger(db, courier.id, '2026-02')
    assert ledger['summary']['carryForwardCents'] == 50
    assert ledger['summary']['availableCents'] == 116
    assert {row['id'] for row in ledger['carryForward']} == {
        january_bonus.id, january_adjustment.id}

    settlement = record_settlement(db, actor, courier.id, '2026-02', None, '二月自动发放')
    db.commit()
    assert settlement.amount_cents == 116
    paid_ids = {item.entry_id for item in db.scalars(select(WageSettlementItem).where(
        WageSettlementItem.settlement_id == settlement.id))}
    assert paid_ids == {january_bonus.id, january_adjustment.id, february_base.id}
    assert march_future.id not in paid_ids


def test_concurrent_delivery_first_does_not_miss_fee_receipt(
        db, staff_factory):
    """Same-Order locks must serialize delivery and an actual fee receipt.

    This intentionally drives the real delivery lock path
    (``_lock_order_delivery``) and the real supplementary service
    (``record_receipt``), not a naked hook call.  Delivery commits first; the
    receipt then blocks until the delivery aggregate is durable and must append
    its own adjustment, so no collected fee disappears from wages.
    """
    from app import delivery as delivery_module
    from app.db import SessionLocal

    courier = make_courier(staff_factory)
    admin = staff_factory()
    add_policy(db, mode='PERCENT', rate=333)
    order, assignment, proof, _ = make_delivered(
        db, staff_factory, courier, total=10000)
    charge = create_charge(
        db, admin_actor(admin), order,
        ChargeInput(purpose='加急补充费', amount_cents=1000,
                    include_in_payroll=True, expected_version=order.version))
    db.commit()
    order_id, proof_id, charge_id = order.id, proof.id, charge.id

    errors = []
    locked = threading.Event()
    release = threading.Event()
    delivery_done = threading.Event()
    receipt_done = threading.Event()
    values = {}

    def delivery_worker():
        session = SessionLocal()
        try:
            locked_order, locked_assignment, _ = delivery_module._lock_order_delivery(
                session, order_id, None)
            locked.set()
            if not release.wait(10):
                raise RuntimeError('delivery release timed out')
            locked_proof = session.get(DeliveryProof, proof_id)
            entry = on_delivery(session, locked_order, locked_assignment, locked_proof)
            session.commit()
            values['base'] = entry.amount_cents
        except BaseException as exc:  # pragma: no cover - surfaced by main thread
            errors.append(exc)
        finally:
            session.close()
            delivery_done.set()

    def receipt_worker():
        session = SessionLocal()
        try:
            locked_charge = session.get(SupplementaryCharge, charge_id)
            record_receipt(
                session,
                Actor(staff_id=admin.id, staff_role='ADMIN',
                      capabilities={'payment.manage'}),
                locked_charge,
                ReceiptInput(amount_cents=1000, reason='补充费用实际收款',
                             expected_version=locked_charge.version))
            session.commit()
            values['receipt'] = 1000
        except BaseException as exc:  # pragma: no cover - surfaced by main thread
            errors.append(exc)
        finally:
            session.close()
            receipt_done.set()

    delivery_thread = threading.Thread(target=delivery_worker, daemon=True)
    receipt_thread = threading.Thread(target=receipt_worker, daemon=True)
    delivery_thread.start()
    assert locked.wait(10), 'delivery did not acquire the order lock'
    receipt_thread.start()
    time.sleep(0.4)
    assert not receipt_done.is_set(), 'receipt should block on the delivery order lock'
    release.set()
    assert delivery_done.wait(10), 'delivery did not finish'
    assert receipt_done.wait(10), 'receipt did not finish after delivery committed'
    delivery_thread.join(10)
    receipt_thread.join(10)
    assert not errors, errors

    check = SessionLocal()
    try:
        rows = list(check.scalars(select(WageEntry).where(
            WageEntry.order_id == order_id).order_by(WageEntry.created_at, WageEntry.id)))
        charge_row = check.get(SupplementaryCharge, charge_id)
        assert charge_row.cash_received_cents == 1000
    finally:
        check.close()

    assert values['base'] == 333
    assert [(row.kind, row.amount_cents) for row in rows] == [
        ('DELIVERY', 333), ('SUPPLEMENTARY_ADJUSTMENT', 33)]
    assert sum(row.amount_cents for row in rows) == 366


def test_concurrent_fee_receipt_first_is_included_in_delivery_basis(
        db, staff_factory):
    """If the fee receipt wins the race, delivery must see the collected cash."""
    from app import delivery as delivery_module
    from app.db import SessionLocal

    courier = make_courier(staff_factory)
    admin = staff_factory()
    add_policy(db, mode='PERCENT', rate=333)
    order, assignment, proof, _ = make_delivered(
        db, staff_factory, courier, total=10000)
    charge = create_charge(
        db, admin_actor(admin), order,
        ChargeInput(purpose='加急补充费', amount_cents=1000,
                    include_in_payroll=True, expected_version=order.version))
    db.commit()
    order_id, proof_id, charge_id = order.id, proof.id, charge.id

    errors = []
    receipt_locked = threading.Event()
    release = threading.Event()
    receipt_done = threading.Event()
    delivery_done = threading.Event()
    values = {}

    def receipt_worker():
        session = SessionLocal()
        try:
            session.execute(select(Order).where(Order.id == order_id).with_for_update())
            locked_charge = session.get(SupplementaryCharge, charge_id)
            receipt_locked.set()
            if not release.wait(10):
                raise RuntimeError('receipt release timed out')
            record_receipt(
                session,
                Actor(staff_id=admin.id, staff_role='ADMIN',
                      capabilities={'payment.manage'}),
                locked_charge,
                ReceiptInput(amount_cents=1000, reason='补充费用实际收款',
                             expected_version=locked_charge.version))
            session.commit()
            values['receipt'] = 1000
        except BaseException as exc:  # pragma: no cover - surfaced by main thread
            errors.append(exc)
        finally:
            session.close()
            receipt_done.set()

    def delivery_worker():
        session = SessionLocal()
        try:
            locked_order, locked_assignment, _ = delivery_module._lock_order_delivery(
                session, order_id, None)
            locked_proof = session.get(DeliveryProof, proof_id)
            entry = on_delivery(session, locked_order, locked_assignment, locked_proof)
            session.commit()
            values['base'] = entry.amount_cents
        except BaseException as exc:  # pragma: no cover - surfaced by main thread
            errors.append(exc)
        finally:
            session.close()
            delivery_done.set()

    receipt_thread = threading.Thread(target=receipt_worker, daemon=True)
    delivery_thread = threading.Thread(target=delivery_worker, daemon=True)
    receipt_thread.start()
    assert receipt_locked.wait(10), 'receipt did not acquire the order lock'
    delivery_thread.start()
    time.sleep(0.4)
    assert not delivery_done.is_set(), 'delivery should block on the receipt order lock'
    release.set()
    assert receipt_done.wait(10), 'receipt did not finish'
    assert delivery_done.wait(10), 'delivery did not finish after receipt committed'
    receipt_thread.join(10)
    delivery_thread.join(10)
    assert not errors, errors

    check = SessionLocal()
    try:
        rows = list(check.scalars(select(WageEntry).where(
            WageEntry.order_id == order_id).order_by(WageEntry.created_at, WageEntry.id)))
        charge_row = check.get(SupplementaryCharge, charge_id)
        assert charge_row.cash_received_cents == 1000
    finally:
        check.close()

    assert values['base'] == 366
    assert [(row.kind, row.amount_cents) for row in rows] == [('DELIVERY', 366)]
    assert sum(row.amount_cents for row in rows) == 366


def test_explicit_future_positive_still_consumes_prior_negative_carry(
        db, staff_factory):
    courier = make_courier(staff_factory)
    admin = staff_factory()
    actor = admin_actor(admin)
    add_policy(db, mode='PERCENT', rate=333)

    january_order, january_assignment, january_proof, _ = make_delivered(
        db, staff_factory, courier, total=3000,
        delivered_at=datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc))
    january_base = on_delivery(db, january_order, january_assignment, january_proof)
    db.commit()
    record_settlement(db, actor, courier.id, '2026-01', [january_base.id], '一月发放')
    db.commit()

    january_order.refunded_cents = 1500
    refund = refund_for(
        db, january_order, 1500, admin.id,
        created_at=datetime(2026, 1, 20, 12, 0, tzinfo=timezone.utc))
    adjustment = on_refund(db, january_order, refund)
    db.commit()
    assert adjustment.amount_cents == -50

    february_order, february_assignment, february_proof, _ = make_delivered(
        db, staff_factory, courier, total=2000,
        delivered_at=datetime(2026, 2, 5, 12, 0, tzinfo=timezone.utc))
    february_base = on_delivery(db, february_order, february_assignment, february_proof)
    db.commit()

    # Even an explicit selection of only the February positive must carry the
    # January negative adjustment rather than paying the positive in full.
    settlement = record_settlement(
        db, actor, courier.id, '2026-02', [february_base.id], '二月指定发放')
    db.commit()
    assert settlement.amount_cents == 16
    assert {item.entry_id for item in db.scalars(select(WageSettlementItem))} == {
        january_base.id, february_base.id, adjustment.id}


def test_supplementary_cross_month_uses_cash_event_period_not_delivery_period(
        db, staff_factory, monkeypatch):
    courier = make_courier(staff_factory)
    admin = staff_factory()
    actor = admin_actor(admin)
    add_policy(db, mode='PERCENT', rate=333)

    january_order, january_assignment, january_proof, _ = make_delivered(
        db, staff_factory, courier, total=10000,
        delivered_at=datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc))
    # Simulate the delivery hook running in January so the frozen cutoff is not
    # the wall-clock month of the test run.
    monkeypatch.setattr(
        'app.payroll.utcnow',
        lambda: datetime(2026, 1, 5, 12, 5, tzinfo=timezone.utc),
    )
    january_base = on_delivery(db, january_order, january_assignment, january_proof)
    db.commit()
    record_settlement(db, actor, courier.id, '2026-01', [january_base.id], '一月发放')
    db.commit()

    charge = _supplementary_charge(db, january_order, admin, amount=1000)
    receipt = _supplementary_movement(
        db, charge, admin, 'RECEIPT', 1000,
        created_at=datetime(2026, 2, 12, 12, 0, tzinfo=timezone.utc))
    db.commit()
    receipt_wage = on_supplementary_change(
        db, january_order, f'{SUPPLEMENTARY_SOURCE_PREFIX}{receipt.id}')
    db.commit()
    assert receipt_wage.period == '2026-02'
    assert receipt_wage.amount_cents == 33
    assert receipt_wage.snapshot['originalDeliveryPeriod'] == '2026-01'

    february_order, february_assignment, february_proof, _ = make_delivered(
        db, staff_factory, courier, total=3000,
        delivered_at=datetime(2026, 2, 6, 12, 0, tzinfo=timezone.utc))
    february_base = on_delivery(db, february_order, february_assignment, february_proof)
    db.commit()
    assert february_base.amount_cents == 99

    settlement = record_settlement(db, actor, courier.id, '2026-02', None, '二月合并发放')
    db.commit()
    assert settlement.amount_cents == 132
    assert {item.entry_id for item in db.scalars(select(WageSettlementItem))} == {
        january_base.id, february_base.id, receipt_wage.id}

    fee_refund = _supplementary_movement(
        db, charge, admin, 'REFUND', 400,
        created_at=datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc))
    db.commit()
    refund_wage = on_supplementary_change(
        db, january_order, f'{SUPPLEMENTARY_SOURCE_PREFIX}{fee_refund.id}')
    db.commit()
    assert refund_wage.period == '2026-03'
    assert refund_wage.amount_cents == -14
    assert refund_wage.snapshot['originalDeliveryPeriod'] == '2026-01'


def test_fixed_refund_records_zero_adjustment_for_audit(db, staff_factory):
    courier = make_courier(staff_factory)
    admin = staff_factory()
    add_policy(db, mode='FIXED', fixed=250)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier)
    on_delivery(db, order, assignment, proof)
    db.commit()
    order.refunded_cents = 3000
    refund = refund_for(db, order, 3000, admin.id)

    adjustment = on_refund(db, order, refund)
    db.commit()
    assert adjustment.amount_cents == 0
    assert adjustment.kind == 'REFUND_ADJUSTMENT'
    assert adjustment.snapshot['policyMode'] == 'FIXED'


def test_refund_before_delivery_creates_no_wage_adjustment(db, staff_factory):
    admin = staff_factory()
    courier = make_courier(staff_factory)
    add_policy(db, mode='PERCENT', rate=333)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier)
    order.status = 'READY_FOR_DELIVERY'
    db.commit()
    refund = refund_for(db, order, 1000, admin.id)

    assert on_refund(db, order, refund) is None
    db.commit()
    assert entries_for(db, courier.id) == []


# ---------------------------------------------------------------------------
# Manual adjustments & supplementary fees
# ---------------------------------------------------------------------------

def test_manual_entry_requires_reason_and_preserves_original_attribution(db, staff_factory):
    courier = make_courier(staff_factory)
    admin = staff_factory()
    add_policy(db, mode='FIXED', fixed=250)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier)
    base = on_delivery(db, order, assignment, proof)
    db.commit()

    entry = create_manual_entry(db, admin_actor(admin), courier.id, -50, '2026-10',
                                ' 少算补差  ', order_id=order.id)
    db.commit()

    assert entry.kind == 'MANUAL'
    assert entry.amount_cents == -50
    assert entry.reason == '少算补差'
    assert entry.snapshot['baseSourceKey'] == base.source_key
    assert entry.snapshot['originalStaffId'] == courier.id
    assert entry.snapshot['originalBaseCashCents'] == 10000
    db.refresh(base)
    assert base.amount_cents == 250
    assert base.version == 1

    with pytest.raises(DomainError) as error:
        create_manual_entry(db, admin_actor(admin), courier.id, 10, '2026-10', '   ')
    assert error.value.code == 'REASON_REQUIRED'
    db.rollback()


def test_supplementary_eligible_uses_original_percent_policy_and_include_flag(db, staff_factory):
    courier = make_courier(staff_factory)
    add_policy(db, mode='PERCENT', rate=333)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier, total=3000)
    base = on_delivery(db, order, assignment, proof)
    db.commit()
    assert base.amount_cents == 99

    paid = apply_supplementary_eligible(db, order, 'FEE:1:PAID', 1000, include_in_payroll=True)
    db.commit()
    assert paid.kind == 'SUPPLEMENTARY_ADJUSTMENT'
    assert paid.amount_cents == 33

    excluded = apply_supplementary_eligible(db, order, 'FEE:2:PAID', 5000, include_in_payroll=False)
    assert excluded is None

    refunded = apply_supplementary_eligible(db, order, 'FEE:1:REFUND', -1000, include_in_payroll=True)
    db.commit()
    assert refunded.amount_cents == -33
    assert sum(e.amount_cents for e in entries_for(db, courier.id)) == 99

    # Duplicate source is idempotent; a different amount for the same source conflicts.
    assert apply_supplementary_eligible(db, order, 'FEE:1:PAID', 1000).id == paid.id
    with pytest.raises(DomainError) as error:
        apply_supplementary_eligible(db, order, 'FEE:1:PAID', 2000)
    assert error.value.code == 'WAGE_SOURCE_CONFLICT'
    db.rollback()


def test_supplementary_fixed_policy_is_unchanged(db, staff_factory):
    courier = make_courier(staff_factory)
    add_policy(db, mode='FIXED', fixed=250)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier)
    on_delivery(db, order, assignment, proof)
    db.commit()

    assert apply_supplementary_eligible(db, order, 'FEE:1:PAID', 1000) is None
    db.commit()
    assert len(entries_for(db, courier.id)) == 1


def test_supplementary_hook_is_real_and_reports_unknown_source(db, staff_factory):
    assert SUPPLEMENTARY_SOURCE_AVAILABLE is True
    courier = make_courier(staff_factory)
    order, _, _, _ = make_delivered(db, staff_factory, courier)
    with pytest.raises(DomainError) as error:
        on_supplementary_change(db, order, 'supplementary-movement:missing')
    assert error.value.code == 'SUPPLEMENTARY_SOURCE_NOT_FOUND'


def test_delivery_percent_aggregates_only_collected_included_fees(db, staff_factory):
    courier = make_courier(staff_factory)
    admin = staff_factory()
    add_policy(db, mode='PERCENT', rate=333)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier, total=10000)

    included = _supplementary_charge(db, order, admin, amount=2000)
    _supplementary_movement(db, included, admin, 'RECEIPT', 2000)
    excluded = _supplementary_charge(db, order, admin, amount=5000, include=False)
    _supplementary_movement(db, excluded, admin, 'RECEIPT', 5000)
    _supplementary_charge(db, order, admin, amount=900)  # quoted but never collected
    cancelled = _supplementary_charge(db, order, admin, amount=700)
    _supplementary_movement(db, cancelled, admin, 'RECEIPT', 700)
    _supplementary_movement(db, cancelled, admin, 'REFUND', 700)
    cancelled.status = 'CANCELLED'

    base = on_delivery(db, order, assignment, proof)
    db.commit()

    # 333 bps of (10,000 original net + only 2,000 actually collected eligible fee)
    assert base.amount_cents == 399
    assert base.snapshot['originalBaseCashCents'] == 10000
    assert base.snapshot['extraEligibleCents'] == 2000
    assert base.snapshot['baseCashCents'] == 10000
    assert base.snapshot['basisCents'] == 12000
    assert db.scalar(select(func.count()).select_from(WageEntry)
                     .where(WageEntry.kind == 'SUPPLEMENTARY_ADJUSTMENT')) == 0


def test_supplementary_hook_accepts_prefixed_source_and_freezes_original_policy(db, staff_factory):
    courier = make_courier(staff_factory)
    admin = staff_factory()
    add_policy(db, mode='PERCENT', rate=333)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier, total=10000)
    base = on_delivery(db, order, assignment, proof)
    charge = _supplementary_charge(db, order, admin, amount=1000)
    receipt = _supplementary_movement(db, charge, admin, 'RECEIPT', 1000)

    wage = on_supplementary_change(db, order, f'{SUPPLEMENTARY_SOURCE_PREFIX}{receipt.id}')
    db.commit()
    assert wage.amount_cents == 33
    assert wage.source_key == f'SUPPLEMENTARY:{order.id}:{SUPPLEMENTARY_SOURCE_PREFIX}{receipt.id}'
    assert wage.snapshot['policyMode'] == 'PERCENT'
    assert wage.snapshot['rateBps'] == 333
    assert wage.period == base.period
    assert on_supplementary_change(
        db, order, f'{SUPPLEMENTARY_SOURCE_PREFIX}{receipt.id}').id == wage.id

    refund = _supplementary_movement(db, charge, admin, 'REFUND', 400)
    reversal = on_supplementary_change(
        db, order, f'{SUPPLEMENTARY_SOURCE_PREFIX}{refund.id}')
    db.commit()
    assert reversal.amount_cents == -14
    assert sum(entry.amount_cents for entry in entries_for(db, courier.id)) == 352


def test_combined_base_rounds_once_for_partial_fee_and_order_refunds(db, staff_factory):
    courier = make_courier(staff_factory)
    admin = staff_factory()
    add_policy(db, mode='PERCENT', rate=1000)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier, total=101)
    charge = _supplementary_charge(db, order, admin, amount=101)
    _supplementary_movement(db, charge, admin, 'RECEIPT', 101)

    base = on_delivery(db, order, assignment, proof)
    db.commit()
    # floor(10% * (101 original + 101 eligible fee)) = 20, not 10 + 10.
    assert base.amount_cents == 20
    assert base.snapshot['basisCents'] == 202

    fee_refund = _supplementary_movement(db, charge, admin, 'REFUND', 1)
    fee_adjustment = on_supplementary_change(
        db, order, f'{SUPPLEMENTARY_SOURCE_PREFIX}{fee_refund.id}')
    db.commit()
    # floor(10% * 201) is still 20; the combined target does not drift.
    assert fee_adjustment.amount_cents == 0
    assert sum(entry.amount_cents for entry in entries_for(db, courier.id)) == 20

    order.refunded_cents = 1
    order_refund = refund_for(db, order, 1, admin.id)
    order_adjustment = on_refund(db, order, order_refund)
    db.commit()
    assert order_adjustment.amount_cents == 0
    assert sum(entry.amount_cents for entry in entries_for(db, courier.id)) == 20

    order.refunded_cents = 101
    full_refund = refund_for(db, order, 100, admin.id)
    full_adjustment = on_refund(db, order, full_refund)
    db.commit()
    # Only the 101 eligible fee remains in the base: floor(10% * 101) = 10.
    assert full_adjustment.amount_cents == -10
    assert sum(entry.amount_cents for entry in entries_for(db, courier.id)) == 10


def test_zero_original_cash_still_pays_collected_eligible_fee_component(db, staff_factory):
    courier = make_courier(staff_factory)
    admin = staff_factory()
    add_policy(db, mode='PERCENT', rate=1000)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier, total=0)
    charge = _supplementary_charge(db, order, admin, amount=101)
    _supplementary_movement(db, charge, admin, 'RECEIPT', 101)

    base = on_delivery(db, order, assignment, proof)
    db.commit()
    assert base.amount_cents == 10
    assert base.snapshot['originalBaseCashCents'] == 0
    assert base.snapshot['extraEligibleCents'] == 101
    assert base.snapshot['basisCents'] == 101


def test_fee_refund_recalculates_combined_target_instead_of_adding_rounded_deltas(
        db, staff_factory):
    courier = make_courier(staff_factory)
    admin = staff_factory()
    add_policy(db, mode='PERCENT', rate=333)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier, total=1010)
    charge = _supplementary_charge(db, order, admin, amount=1010)
    _supplementary_movement(db, charge, admin, 'RECEIPT', 1010)

    base = on_delivery(db, order, assignment, proof)
    db.commit()
    # floor(3.33% * 2020) = 67, not floor(33.633) + floor(33.633) = 66.
    assert base.amount_cents == 67

    refund = _supplementary_movement(db, charge, admin, 'REFUND', 1010)
    adjustment = on_supplementary_change(
        db, order, f'{SUPPLEMENTARY_SOURCE_PREFIX}{refund.id}')
    db.commit()
    # floor(3.33% * 1010) = 33; 67 - 33 = 34, not a per-event -33 leaving 34.
    assert base.amount_cents == 67
    assert adjustment.amount_cents == -34
    assert sum(entry.amount_cents for entry in entries_for(db, courier.id)) == 33


def test_refund_of_pre_delivery_included_fee_reverses_its_base_component(db, staff_factory):
    courier = make_courier(staff_factory)
    admin = staff_factory()
    add_policy(db, mode='PERCENT', rate=333)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier, total=10000)
    charge = _supplementary_charge(db, order, admin, amount=1000)
    _supplementary_movement(db, charge, admin, 'RECEIPT', 1000)

    base = on_delivery(db, order, assignment, proof)
    db.commit()
    assert base.amount_cents == 366

    refund = _supplementary_movement(db, charge, admin, 'REFUND', 1000)
    adjustment = on_supplementary_change(
        db, order, f'{SUPPLEMENTARY_SOURCE_PREFIX}{refund.id}')
    db.commit()
    assert adjustment.amount_cents == -33
    assert sum(entry.amount_cents for entry in entries_for(db, courier.id)) == 333


def test_original_refund_keeps_pre_delivery_fee_in_the_percent_base(db, staff_factory):
    courier = make_courier(staff_factory)
    admin = staff_factory()
    add_policy(db, mode='PERCENT', rate=333)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier, total=10000)
    charge = _supplementary_charge(db, order, admin, amount=1000)
    _supplementary_movement(db, charge, admin, 'RECEIPT', 1000)
    base = on_delivery(db, order, assignment, proof)
    db.commit()
    assert base.amount_cents == 366

    order.refunded_cents = 4000
    refund = refund_for(db, order, 4000, admin.id)
    adjustment = on_refund(db, order, refund)
    db.commit()
    # floor(333 * (6,000 original net + 1,000 eligible fee) / 10,000) = 233
    assert adjustment.amount_cents == -133
    assert sum(entry.amount_cents for entry in entries_for(db, courier.id)) == 233


def test_retroactive_policy_includes_pre_delivery_fee_and_appends_post_delivery_fee(
        db, staff_factory):
    courier = make_courier(staff_factory)
    admin = staff_factory()
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier, total=10000)
    pre_charge = _supplementary_charge(db, order, admin, amount=1000)
    _supplementary_movement(db, pre_charge, admin, 'RECEIPT', 1000)
    marker = on_delivery(db, order, assignment, proof)
    db.commit()
    assert marker.kind == 'NEEDS_CONFIGURATION'
    assert marker.snapshot['extraEligibleCents'] == 1000

    post_charge = _supplementary_charge(db, order, admin, amount=500)
    post_receipt = _supplementary_movement(db, post_charge, admin, 'RECEIPT', 500)
    # The marker could not pay this fee; applying a policy retrospectively must.
    assert on_supplementary_change(
        db, order, f'{SUPPLEMENTARY_SOURCE_PREFIX}{post_receipt.id}') is None

    policy = add_policy(db, mode='PERCENT', rate=500)
    base = apply_retroactive_policy(db, admin_actor(admin), order, '补录交付工资',
                                    policy_id=policy.id)
    db.commit()
    assert base.amount_cents == 550  # floor(500 bps * 11,000 / 10,000)
    assert base.snapshot['extraEligibleCents'] == 1000
    adjustment = entry_for(
        db, f'SUPPLEMENTARY:{order.id}:{SUPPLEMENTARY_SOURCE_PREFIX}{post_receipt.id}')
    assert adjustment is not None
    assert adjustment.amount_cents == 25
    assert adjustment.snapshot['policyMode'] == 'PERCENT'
    assert adjustment.snapshot['rateBps'] == 500


# ---------------------------------------------------------------------------
# Retroactive application of a selected policy
# ---------------------------------------------------------------------------

def test_retroactive_policy_application_requires_reason_and_converts_marker(db, staff_factory):
    courier = make_courier(staff_factory)
    admin = staff_factory()
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier)
    marker = on_delivery(db, order, assignment, proof)
    db.commit()
    assert marker.kind == 'NEEDS_CONFIGURATION'

    with pytest.raises(DomainError) as missing:
        apply_retroactive_policy(db, admin_actor(admin), order, '   ')
    assert missing.value.code == 'REASON_REQUIRED'
    db.rollback()

    policy = add_policy(db, mode='PERCENT', rate=500)
    entry = apply_retroactive_policy(db, admin_actor(admin), order, '补录交付工资', policy_id=policy.id)
    db.commit()
    assert entry.id == marker.id
    assert entry.kind == 'DELIVERY'
    assert entry.amount_cents == 500
    assert entry.reason == '补录交付工资'
    assert entry.snapshot['appliedBy'] == admin.id
    assert entry.snapshot['policySource'] == 'ADMIN_SELECTED'


def test_retroactive_application_still_requires_a_configured_policy(db, staff_factory):
    courier = make_courier(staff_factory)
    admin = staff_factory()
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier)
    on_delivery(db, order, assignment, proof)
    db.commit()

    with pytest.raises(DomainError) as error:
        apply_retroactive_policy(db, admin_actor(admin), order, '补录交付工资')
    assert error.value.code == 'NEEDS_CONFIGURATION'
    db.rollback()


# ---------------------------------------------------------------------------
# HTTP surfaces
# ---------------------------------------------------------------------------

def test_admin_policy_route_versions_and_capability(admin_client, api, act_as, staff_factory):
    created = api(admin_client, '/admin/payroll/policies/default', {
        'mode': 'PERCENT', 'fixedCents': 0, 'rateBps': 333, 'enabled': True,
    }, method='PUT')
    assert created.status_code == 200, created.text
    policy = created.json()['data']
    assert policy['mode'] == 'PERCENT'
    assert policy['version'] == 1

    conflict = api(admin_client, '/admin/payroll/policies/default', {
        'mode': 'FIXED', 'fixedCents': 100, 'rateBps': 0, 'enabled': True,
        'expectedVersion': 99,
    }, method='PUT')
    assert conflict.status_code == 409, conflict.text

    updated = api(admin_client, '/admin/payroll/policies/default', {
        'mode': 'FIXED', 'fixedCents': 100, 'rateBps': 0, 'enabled': True,
        'expectedVersion': 1,
    }, method='PUT')
    assert updated.status_code == 200, updated.text
    assert updated.json()['data']['version'] == 2

    limited = staff_factory(capabilities=['delivery.manage'])
    act_as(limited)
    denied = api(admin_client, '/admin/payroll/policies/default', {
        'mode': 'FIXED', 'fixedCents': 1, 'rateBps': 0, 'enabled': True,
    }, method='PUT')
    assert denied.status_code == 403, denied.text


def test_employee_ledger_is_own_only_and_admin_routes_are_forbidden(
        client, api, act_as, staff_factory, db):
    courier = make_courier(staff_factory, name='甲')
    other = make_courier(staff_factory, name='乙')
    add_policy(db, mode='FIXED', fixed=100)
    first_order, first_assignment, first_proof, _ = make_delivered(
        db, staff_factory, courier, total=1000)
    second_order, second_assignment, second_proof, _ = make_delivered(
        db, staff_factory, other, total=2000)
    on_delivery(db, first_order, first_assignment, first_proof)
    on_delivery(db, second_order, second_assignment, second_proof)
    db.commit()

    act_as(courier)
    mine = api(client, f'/payroll/me?period=2026-10&staffId={other.id}', method='GET')
    assert mine.status_code == 200, mine.text
    data = mine.json()['data']
    assert data['staff']['id'] == courier.id
    assert data['staff']['name'] == '甲'
    assert {item['orderId'] for item in data['entries']} == {first_order.id}
    assert data['summary']['netCents'] == 100

    forbidden = api(client, '/admin/payroll/ledger?period=2026-10', method='GET')
    assert forbidden.status_code == 403, forbidden.text
    manual = api(client, '/admin/payroll/entries', {
        'staffId': courier.id, 'amountCents': 100, 'period': '2026-10', 'reason': '自己加钱',
    })
    assert manual.status_code == 403, manual.text


def test_payroll_staff_catalog_needs_only_payroll_manage_and_no_staff_pii(
        client, api, act_as, staff_factory, db):
    admin = staff_factory(role='ADMIN', capabilities=['payroll.manage'])
    courier = make_courier(staff_factory, name='工资员工')
    disabled = make_courier(staff_factory, name='停用员工')
    disabled.enabled = False
    db.commit()

    act_as(admin)
    response = api(client, '/admin/payroll/staff', method='GET')
    assert response.status_code == 200, response.text
    rows = response.json()['data']
    by_id = {row['id']: row for row in rows}
    assert by_id[courier.id] == {
        'id': courier.id, 'name': '工资员工', 'role': 'COURIER', 'enabled': True,
    }
    assert by_id[disabled.id]['enabled'] is False
    assert all(set(row) == {'id', 'name', 'role', 'enabled'} for row in rows)
    assert all('username' not in row and 'phone' not in row for row in rows)

    act_as(courier)
    denied = api(client, '/admin/payroll/staff', method='GET')
    assert denied.status_code == 403, denied.text


def test_admin_ledger_shows_unpaid_pending_manual_reason_and_needs_configuration(
        admin_client, api, staff_factory, db):
    courier = make_courier(staff_factory)
    admin = admin_client.staff
    unconfigured_courier = make_courier(staff_factory, name='未配置')
    pending_order, pending_assignment, pending_proof, _ = make_delivered(
        db, staff_factory, unconfigured_courier, total=1000)
    marker = on_delivery(db, pending_order, pending_assignment, pending_proof)
    add_policy(db, mode='FIXED', fixed=250)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier)
    base = on_delivery(db, order, assignment, proof)
    create_manual_entry(db, admin_actor(admin), courier.id, 50, '2026-10', '补发车费')
    db.commit()
    assert marker.kind == 'NEEDS_CONFIGURATION'

    response = api(admin_client, '/admin/payroll/ledger?period=2026-10', method='GET')
    assert response.status_code == 200, response.text
    data = response.json()['data']
    summary = next(item for item in data['summaries'] if item['staffId'] == courier.id)
    assert summary['netCents'] == 300
    assert summary['unpaidCents'] == 300
    assert summary['paidCents'] == 0
    assert any(item['kind'] == 'MANUAL' and item['reason'] == '补发车费' for item in data['entries'])
    assert any(item['orderId'] == pending_order.id for item in data['needsConfiguration'])
    assert all(item['entryId'] != base.id for item in data['needsConfiguration'])


def test_manual_entry_endpoint_requires_reason_and_replays_idempotently(
        admin_client, api, staff_factory, db):
    courier = make_courier(staff_factory)
    missing = api(admin_client, '/admin/payroll/entries', {
        'staffId': courier.id, 'amountCents': 100, 'period': '2026-10', 'reason': '',
    })
    assert missing.status_code == 422, missing.text

    body = {'staffId': courier.id, 'amountCents': -30, 'period': '2026-10', 'reason': '扣回'}
    first = api(admin_client, '/admin/payroll/entries', body, key='manual-key-1')
    second = api(admin_client, '/admin/payroll/entries', body, key='manual-key-1')
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()['data']['id'] == second.json()['data']['id']
    db.rollback()
    assert db.scalar(select(func.count()).select_from(WageEntry)
                     .where(WageEntry.kind == 'MANUAL')) == 1


def test_settlement_endpoint_records_unique_payout_and_rejects_double_pay(
        admin_client, api, staff_factory, db):
    courier = make_courier(staff_factory)
    add_policy(db, mode='FIXED', fixed=250)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier)
    base = on_delivery(db, order, assignment, proof)
    create_manual_entry(db, admin_actor(admin_client.staff), courier.id, -50, '2026-10', '扣回')
    db.commit()

    body = {'staffId': courier.id, 'period': '2026-10', 'entryIds': [base.id],
            'reason': '十月发放'}
    response = api(admin_client, '/admin/payroll/settlements', body)
    assert response.status_code == 200, response.text
    settlement = response.json()['data']
    assert settlement['amountCents'] == 250
    assert settlement['reason'] == '十月发放'
    assert [item['entryId'] for item in settlement['items']] == [base.id]

    again = api(admin_client, '/admin/payroll/settlements', body)
    assert again.status_code == 409, again.text

    db.rollback()
    assert db.scalar(select(func.count()).select_from(WageSettlement)) == 1
    db.refresh(base)
    assert base.amount_cents == 250


def test_settlement_with_negative_net_is_rejected_and_kept_for_future(db, staff_factory):
    courier = make_courier(staff_factory)
    admin = staff_factory()
    add_policy(db, mode='PERCENT', rate=333)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier, total=3000)
    base = on_delivery(db, order, assignment, proof)
    order.refunded_cents = 3000
    refund = refund_for(db, order, 3000, admin.id)
    adjustment = on_refund(db, order, refund)
    db.commit()
    assert adjustment.amount_cents == -99

    with pytest.raises(DomainError) as error:
        record_settlement(db, admin_actor(admin), courier.id, '2026-10',
                          [adjustment.id], '发放')
    assert error.value.code == 'WAGE_NET_NEGATIVE'
    db.rollback()
    assert db.scalar(select(func.count()).select_from(WageSettlement)) == 0


def test_negative_adjustment_offsets_a_later_month_when_selected(db, staff_factory):
    courier = make_courier(staff_factory)
    admin = staff_factory()
    actor = admin_actor(admin)
    add_policy(db, mode='PERCENT', rate=333)
    october_order, october_assignment, october_proof, _ = make_delivered(
        db, staff_factory, courier, total=3000,
        delivered_at=datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc))
    october_base = on_delivery(db, october_order, october_assignment, october_proof)
    db.commit()
    october_settlement = record_settlement(db, actor, courier.id, '2026-10',
                                           [october_base.id], '十月发放')
    db.commit()
    assert october_settlement.amount_cents == 99

    october_order.refunded_cents = 1500
    refund = refund_for(db, october_order, 1500, admin.id)
    october_adjustment = on_refund(db, october_order, refund)
    db.commit()
    assert october_adjustment.amount_cents == -50

    november_order, november_assignment, november_proof, _ = make_delivered(
        db, staff_factory, courier, total=2000,
        delivered_at=datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc))
    november_base = on_delivery(db, november_order, november_assignment, november_proof)
    db.commit()
    assert november_base.amount_cents == 66

    ledger = staff_ledger(db, courier.id, '2026-11')
    assert ledger['summary']['carryForwardCents'] == -50
    assert ledger['summary']['availableCents'] == 16
    assert [row['id'] for row in ledger['carryForward']] == [october_adjustment.id]

    settlement = record_settlement(db, actor, courier.id, '2026-11',
                                   [november_base.id, october_adjustment.id], '十一月合并结转')
    db.commit()
    assert settlement.amount_cents == 16
    assert {item.entry_id for item in db.scalars(select(WageSettlementItem))} == {
        october_base.id, november_base.id, october_adjustment.id}


def test_settlement_requires_payroll_manage_permission(client, api, act_as, staff_factory):
    courier = make_courier(staff_factory)
    act_as(courier)
    response = api(client, '/admin/payroll/settlements', {
        'staffId': courier.id, 'period': '2026-10', 'reason': '发放',
    })
    assert response.status_code == 403, response.text


def test_retroactive_route_requires_reason(admin_client, api, staff_factory, db):
    courier = make_courier(staff_factory)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier)
    on_delivery(db, order, assignment, proof)
    db.commit()
    add_policy(db, mode='FIXED', fixed=100)

    missing = api(admin_client, f'/admin/payroll/deliveries/{order.id}/apply-policy',
                  {'reason': ' '})
    assert missing.status_code == 422, missing.text

    applied = api(admin_client, f'/admin/payroll/deliveries/{order.id}/apply-policy',
                  {'reason': '补录交付工资'})
    assert applied.status_code == 200, applied.text
    assert applied.json()['data']['kind'] == 'DELIVERY'
    assert applied.json()['data']['amountCents'] == 100


def test_default_settlement_skips_rows_already_paid_and_picks_new_unpaid(db, staff_factory):
    courier = make_courier(staff_factory)
    admin = staff_factory()
    actor = admin_actor(admin)
    add_policy(db, mode='FIXED', fixed=100)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier)
    base = on_delivery(db, order, assignment, proof)
    db.commit()
    first = record_settlement(db, actor, courier.id, base.period, None, '首次月结')
    db.commit()
    assert first.amount_cents == 100

    manual = create_manual_entry(db, actor, courier.id, 60, base.period, '补发')
    db.commit()
    second = record_settlement(db, actor, courier.id, base.period, None, '补充发放')
    db.commit()
    assert second.amount_cents == 60
    assert {item.entry_id for item in db.scalars(
        select(WageSettlementItem).where(WageSettlementItem.settlement_id == second.id)
    )} == {manual.id}

    with pytest.raises(DomainError) as error:
        record_settlement(db, actor, courier.id, base.period, None, '重复月结')
    assert error.value.code == 'SETTLEMENT_EMPTY'


def _supplementary_charge(db, order, staff, amount=1000, include=True):
    charge = SupplementaryCharge(
        order_id=order.id,
        purpose='加急补充费',
        amount_cents=amount,
        include_in_payroll=include,
        status='ACTIVE',
        cash_received_cents=0,
        refunded_cents=0,
        created_by=staff.id,
        data={},
    )
    db.add(charge)
    db.flush()
    return charge


def _supplementary_movement(db, charge, staff, kind, amount, *, created_at=None):
    row = SupplementaryMovement(
        charge_id=charge.id,
        kind=kind,
        amount_cents=amount,
        reason='补充费用流水',
        actor_id=staff.id,
        data={},
    )
    if created_at is not None:
        row.created_at = created_at
    db.add(row)
    db.flush()
    if kind == 'RECEIPT':
        charge.cash_received_cents = int(charge.cash_received_cents or 0) + amount
    else:
        charge.refunded_cents = int(charge.refunded_cents or 0) + amount
    db.flush()
    return row


def test_supplementary_receipt_and_refund_follow_original_percent_policy(
        db, staff_factory):
    courier = make_courier(staff_factory)
    admin = staff_factory()
    add_policy(db, mode='PERCENT', rate=333)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier, total=10000)
    base = on_delivery(db, order, assignment, proof)
    assert base.amount_cents == 333
    charge = _supplementary_charge(db, order, admin)
    receipt = _supplementary_movement(db, charge, admin, 'RECEIPT', 1000)
    wage = on_supplementary_change(db, order, f'{SUPPLEMENTARY_SOURCE_PREFIX}{receipt.id}')
    assert wage.kind == 'SUPPLEMENTARY_ADJUSTMENT'
    assert wage.amount_cents == 33
    assert on_supplementary_change(db, order, f'{SUPPLEMENTARY_SOURCE_PREFIX}{receipt.id}').id == wage.id

    refund = _supplementary_movement(db, charge, admin, 'REFUND', 400)
    reversal = on_supplementary_change(db, order, f'{SUPPLEMENTARY_SOURCE_PREFIX}{refund.id}')
    assert reversal.amount_cents == -14
    db.commit()
    assert db.scalar(select(func.count()).select_from(WageEntry)
                     .where(WageEntry.kind == 'SUPPLEMENTARY_ADJUSTMENT')) == 2
    ledger = staff_ledger(db, courier.id, base.period)
    assert ledger['summary']['netCents'] == 352


def test_supplementary_fixed_or_excluded_fee_never_changes_wages(db, staff_factory):
    courier = make_courier(staff_factory)
    admin = staff_factory()
    add_policy(db, mode='FIXED', fixed=250)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier)
    on_delivery(db, order, assignment, proof)
    charge = _supplementary_charge(db, order, admin)
    receipt = _supplementary_movement(db, charge, admin, 'RECEIPT', 1000)
    assert on_supplementary_change(db, order, f'{SUPPLEMENTARY_SOURCE_PREFIX}{receipt.id}') is None

    excluded_order, excluded_assignment, excluded_proof, _ = make_delivered(
        db, staff_factory, courier)
    on_delivery(db, excluded_order, excluded_assignment, excluded_proof)
    excluded = _supplementary_charge(db, excluded_order, admin, include=False)
    excluded_movement = _supplementary_movement(db, excluded, admin, 'RECEIPT', 1000)
    assert on_supplementary_change(db, excluded_order, f'{SUPPLEMENTARY_SOURCE_PREFIX}{excluded_movement.id}') is None
    db.commit()
    assert db.scalar(select(func.count()).select_from(WageEntry)
                     .where(WageEntry.kind == 'SUPPLEMENTARY_ADJUSTMENT')) == 0


def test_supplementary_ledger_receipt_runs_real_payroll_hook(db, staff_factory):
    courier = make_courier(staff_factory)
    admin = staff_factory()
    add_policy(db, mode='PERCENT', rate=333)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier, total=10000)
    base = on_delivery(db, order, assignment, proof)
    charge = create_charge(
        db, admin_actor(admin), order,
        ChargeInput(purpose='补充加急费', amount_cents=1000,
                    include_in_payroll=True, expected_version=order.version),
    )
    record_receipt(
        db, admin_actor(admin), charge,
        ReceiptInput(amount_cents=1000, reason='补充费用实际收款',
                     expected_version=charge.version),
    )
    db.commit()
    movement = db.scalar(select(SupplementaryMovement).where(
        SupplementaryMovement.charge_id == charge.id))
    assert movement is not None
    entry = entry_for(db, f'SUPPLEMENTARY:{order.id}:{SUPPLEMENTARY_SOURCE_PREFIX}{movement.id}')
    assert entry is not None
    assert movement.data['payroll'] == {'status': 'APPLIED', 'entryId': entry.id}
    assert entry.amount_cents == 33
    assert entry.period == period_for(movement.created_at)
    assert entry.snapshot['originalDeliveryPeriod'] == base.period

    record_refund(
        db, admin_actor(admin), charge,
        RefundInput(amount_cents=400, reason='补充费用部分退款',
                    expected_version=charge.version),
    )
    db.commit()
    refund_movement = db.scalar(select(SupplementaryMovement).where(
        SupplementaryMovement.charge_id == charge.id,
        SupplementaryMovement.kind == 'REFUND'))
    reversal = entry_for(db, f'SUPPLEMENTARY:{order.id}:{SUPPLEMENTARY_SOURCE_PREFIX}{refund_movement.id}')
    assert reversal is not None and reversal.amount_cents == -14
    assert refund_movement.data['payroll'] == {'status': 'APPLIED', 'entryId': reversal.id}
    assert reversal.period == period_for(refund_movement.created_at)
