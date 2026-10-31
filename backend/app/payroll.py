"""Delivery payroll: frozen policies, an append-only wage ledger, and manual payouts.

The module deliberately owns no shared entity models.  It consumes the root-owned
``WagePolicy``/``WageEntry``/``WageSettlement`` models and is designed to be called
from the delivery/after-sales transactions through ``on_delivery`` and ``on_refund``.

Money rules
-----------
* Percentage wages use basis points and floor the combined delivery basis once.
* Refund/fee adjustments store the difference between that single target and the
  component already recorded, so many small cash events cannot drift by cents.
* Adjustments are recorded in the Asia/Shanghai month of the cash event; the
  delivery month and frozen policy remain in the snapshot for audit.
* Every ledger fact is append-only.  A delivery owns one unique base entry; refunds
  and eligible supplementary fees append unique adjustments.  Settled rows are never
  edited.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from .core import (
    Actor,
    DomainError,
    Envelope,
    actor_dep,
    advisory_lock,
    audit,
    lock,
    ok,
    require_admin,
    require_staff,
    touch,
)
from .db import get_db
from .models import (
    Order,
    Staff,
    SupplementaryCharge,
    SupplementaryMovement,
    WageEntry,
    WagePolicy,
    WageSettlement,
    WageSettlementItem,
    utcnow,
)
from .payroll_schemas import (
    AdminLedgerDTO,
    ApplyPolicyInput,
    LedgerSummaryDTO,
    ManualEntryInput,
    NeedsConfigurationDTO,
    PayrollStaffDTO,
    PolicyCatalogDTO,
    PolicyDTO,
    PolicyInput,
    SettlementDTO,
    SettlementInput,
    SettlementItemDTO,
    StaffBriefDTO,
    StaffLedgerDTO,
    WageEntryDTO,
)

router = APIRouter()

SHANGHAI = ZoneInfo('Asia/Shanghai')
PERIOD_RE = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')

KIND_DELIVERY = 'DELIVERY'
KIND_NEEDS_CONFIGURATION = 'NEEDS_CONFIGURATION'
KIND_REFUND = 'REFUND_ADJUSTMENT'
KIND_SUPPLEMENTARY = 'SUPPLEMENTARY_ADJUSTMENT'
KIND_MANUAL = 'MANUAL'
SUPPLEMENTARY_SOURCE_AVAILABLE = True
SUPPLEMENTARY_SOURCE_PREFIX = 'supplementary-movement:'


# ---------------------------------------------------------------------------
# Pure calculation helpers
# ---------------------------------------------------------------------------

def _as_datetime(value: datetime | str | None) -> datetime:
    if value is None:
        return utcnow()
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value).strip()
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def period_for(moment: datetime | str | None) -> str:
    """Return the Chinese payroll month (Asia/Shanghai) for an instant."""
    return _as_datetime(moment).astimezone(SHANGHAI).strftime('%Y-%m')


def current_period() -> str:
    return period_for(utcnow())


def _cash_event_period(value) -> str:
    """Payroll month in which a cash event is recorded for payout.

    Adjustments are appended to the event month, not the month of the original
    delivery.  Otherwise a February refund against a January delivery would be
    stranded in the already-settled January ledger and could not offset a
    February earning.
    """
    return period_for(value)


def normalize_period(period: str | None) -> str:
    value = (period or '').strip() or current_period()
    if not PERIOD_RE.fullmatch(value):
        raise DomainError('PERIOD_INVALID', '工资月份格式应为 YYYY-MM', 422)
    return value


def wage_amount(mode: str, fixed_cents: int, rate_bps: int, basis_cents: int) -> int:
    """Compute a wage in whole cents.  Percentage results are floored."""
    if mode == 'FIXED':
        return int(fixed_cents)
    if mode == 'PERCENT':
        basis = max(0, int(basis_cents))
        return (max(0, int(rate_bps)) * basis) // 10000
    raise DomainError('POLICY_MODE_INVALID', '工资策略模式无效', 422)


def signed_rate_amount(rate_bps: int, cents: int) -> int:
    """Apply a rate to a signed eligible amount without rounding asymmetry."""
    amount = int(cents)
    if amount >= 0:
        return wage_amount('PERCENT', 0, rate_bps, amount)
    return -wage_amount('PERCENT', 0, rate_bps, -amount)


def extra_eligible_cents(settled_cents: int, refunded_cents: int, *,
                         include_in_payroll: bool = True) -> int:
    """Eligible supplementary cash: actually settled fees minus fee refunds.

    Kept pure so the same formula can be used by the supplementary ledger or a
    read-only eligibility view.  ``on_supplementary_change`` translates actual
    movements into signed deltas for the wage ledger.
    """
    if not include_in_payroll:
        return 0
    return max(0, int(settled_cents) - int(refunded_cents))


def _supplementary_source(source_key: str) -> tuple[str, str]:
    """Return the canonical source key and the append-only movement id.

    The supplementary service passes ``supplementary-movement:<id>``.  Raw ids
    remain accepted for older callers and direct tests, but the persisted wage
    source key is always canonical so a replay cannot create a second entry.
    """
    value = str(source_key or '').strip()
    if not value:
        raise DomainError('SUPPLEMENTARY_SOURCE_INVALID', '补充费用来源无效', 422)
    if value.startswith(SUPPLEMENTARY_SOURCE_PREFIX):
        movement_id = value[len(SUPPLEMENTARY_SOURCE_PREFIX):]
    elif ':' in value:
        raise DomainError('SUPPLEMENTARY_SOURCE_INVALID', '补充费用来源格式无效', 422)
    else:
        movement_id = value
    if not movement_id or ':' in movement_id:
        raise DomainError('SUPPLEMENTARY_SOURCE_INVALID', '补充费用来源格式无效', 422)
    return f'{SUPPLEMENTARY_SOURCE_PREFIX}{movement_id}', movement_id


def _included_supplementary_cents(db, order_id: str) -> int:
    """Aggregate actual collected eligible fees, net of fee refunds.

    Quoted but uncollected amounts never enter the wage basis.  ``CANCELLED``
    charges are included in the historical query because their receipts and
    refunds remain append-only; the cancellation invariant requires them to net
    to zero, so they do not add or remove wages.
    """
    charges = db.scalars(
        select(SupplementaryCharge).where(SupplementaryCharge.order_id == order_id)
    )
    return sum(
        max(0, int(charge.cash_received_cents or 0) - int(charge.refunded_cents or 0))
        for charge in charges
        if charge.include_in_payroll
    )


# ---------------------------------------------------------------------------
# Policy resolution
# ---------------------------------------------------------------------------

def resolve_policy(db, staff_id: str) -> tuple[WagePolicy | None, str]:
    """Resolve an enabled staff override, then the enabled default policy."""
    override = db.scalar(
        select(WagePolicy).where(
            WagePolicy.scope_key == f'STAFF:{staff_id}',
            WagePolicy.enabled.is_(True),
        )
    )
    if override is not None:
        return override, 'STAFF'
    default = db.scalar(
        select(WagePolicy).where(
            WagePolicy.scope_key == 'DEFAULT',
            WagePolicy.enabled.is_(True),
        )
    )
    if default is not None:
        return default, 'DEFAULT'
    return None, 'MISSING'


def _policy_snapshot(policy: WagePolicy, source: str) -> dict:
    return {
        'policyId': policy.id,
        'policyScopeKey': policy.scope_key,
        'policySource': source,
        'policyMode': policy.mode,
        'fixedCents': int(policy.fixed_cents or 0),
        'rateBps': int(policy.rate_bps or 0),
        'policyVersion': policy.version,
    }


def _policy_view(db, policy: WagePolicy | None) -> dict | None:
    if policy is None:
        return None
    staff = db.get(Staff, policy.staff_id) if policy.staff_id else None
    return {
        'id': policy.id,
        'version': policy.version,
        'scopeKey': policy.scope_key,
        'staffId': policy.staff_id,
        'staffName': staff.name if staff else '',
        'mode': policy.mode,
        'fixedCents': int(policy.fixed_cents or 0),
        'rateBps': int(policy.rate_bps or 0),
        'enabled': bool(policy.enabled),
        'updatedAt': _iso(policy.updated_at),
    }


# ---------------------------------------------------------------------------
# Delivery / refund hooks (called by the root-owned transactions)
# ---------------------------------------------------------------------------

def _delivery_instant(order: Order, proof) -> datetime:
    data = proof.data or {}
    raw = (data.get('serverDeliveredAt') or data.get('serverDeliveryTime')
           or data.get('deliveredAt'))
    if not raw:
        order_data = order.data or {}
        raw = order_data.get('deliveredAt')
    if not raw:
        return _as_datetime(proof.created_at)
    return _as_datetime(raw)


def _delivery_staff_id(order: Order, assignment, proof) -> str | None:
    if assignment is not None and assignment.courier_id:
        return assignment.courier_id
    proof_data = proof.data or {}
    ordered = [proof_data.get('staffId'), proof_data.get('operatorId')]
    order_data = order.data or {}
    delivery = order_data.get('delivery') if isinstance(order_data.get('delivery'), dict) else {}
    ordered.extend([order_data.get('deliveryCourierId'), delivery.get('courierId')])
    for value in ordered:
        if value:
            return str(value)
    return None


def on_delivery(db, order: Order, assignment, proof) -> WageEntry | None:
    """Create the single delivery wage fact for an order.

    This hook is intentionally non-blocking: if no enabled policy is configured it
    records a durable ``NEEDS_CONFIGURATION`` marker instead of inventing a wage or
    raising into the delivery transaction.  Replays and re-delivery of the same
    order return the original row.

    The caller must hold the Order row lock (the root delivery path does) for the
    fee aggregate and its cutoff to be serialized with supplementary receipts and
    refunds.  Calling this hook without that lock is not a supported transaction.
    """
    if order is None or assignment is None or proof is None:
        raise DomainError('WAGE_DELIVERY_INVALID', '交付工资缺少订单或交付凭据', 422)
    if order.status not in ('DELIVERED', 'COMPLETED'):
        return None

    source_key = f'DELIVERY:{order.id}'
    existing = db.scalar(select(WageEntry).where(WageEntry.source_key == source_key)
                         .with_for_update())
    if existing is not None:
        return existing

    advisory_lock(db, f'payroll:delivery:{order.id}')
    existing = db.scalar(select(WageEntry).where(WageEntry.source_key == source_key)
                         .with_for_update())
    if existing is not None:
        return existing

    staff_id = _delivery_staff_id(order, assignment, proof)
    if not staff_id:
        raise DomainError('WAGE_COURIER_REQUIRED', '交付记录缺少配送员，无法归属工资', 409)
    courier = db.get(Staff, staff_id)
    if courier is None:
        raise DomainError('WAGE_COURIER_REQUIRED', '交付记录中的配送员不存在', 409)

    delivered_at = _delivery_instant(order, proof)
    period = period_for(delivered_at)
    cash_received = max(0, int(order.cash_received_cents or 0))
    refunded_at_delivery = max(0, int(order.refunded_cents or 0))
    original_net = max(0, cash_received - refunded_at_delivery)
    # F03A: only fees actually collected (net of fee refunds) and explicitly
    # included may enlarge the percentage basis.  A quote or an uncollected fee
    # is never treated as earned cash.
    supplementary_net = _included_supplementary_cents(db, order.id)
    basis = original_net + supplementary_net
    recorded_at = utcnow().isoformat()

    policy, source = resolve_policy(db, staff_id)
    snapshot = {
        'deliveryTime': delivered_at.isoformat(),
        'orderId': order.id,
        'assignmentId': assignment.id,
        'batchId': assignment.batch_id,
        'staffId': staff_id,
        'staffName': courier.name,
        'originalBaseCashCents': cash_received,
        'refundedAtDeliveryCents': refunded_at_delivery,
        'baseCashCents': original_net,
        'extraEligibleCents': supplementary_net,
        'supplementaryNetCents': supplementary_net,
        'basisCents': basis,
        'totalCents': int(order.total_cents or 0),
        'recordedAt': recorded_at,
        # Movements at or before this instant were already folded into the
        # delivery aggregate; later movements append adjustments instead.
        'supplementaryRecordedAt': recorded_at,
    }

    if policy is None:
        kind = KIND_NEEDS_CONFIGURATION
        amount = 0
        snapshot.update({
            'needsConfiguration': True,
            'reasonCode': 'NEEDS_CONFIGURATION',
        })
    else:
        kind = KIND_DELIVERY
        amount = wage_amount(policy.mode, policy.fixed_cents or 0,
                             policy.rate_bps or 0, basis)
        snapshot.update(_policy_snapshot(policy, source))
        snapshot['computedAmountCents'] = amount

    entry = WageEntry(
        staff_id=staff_id,
        order_id=order.id,
        assignment_id=assignment.id,
        source_key=source_key,
        kind=kind,
        amount_cents=amount,
        period=period,
        snapshot=snapshot,
        reason='',
        actor_id='',
    )
    db.add(entry)
    db.flush()
    audit(db, 'system:delivery', 'payroll.wage.delivery', 'wage_entry', entry.id,
          reason='', data={'orderId': order.id, 'kind': kind, 'amountCents': amount,
                           'period': period})
    return entry


def _base_entry(db, order_id: str) -> WageEntry | None:
    return db.scalar(
        select(WageEntry).where(WageEntry.source_key == f'DELIVERY:{order_id}')
        .with_for_update()
    )


def _component_total(db, base: WageEntry, order: Order) -> int:
    """Sum the delivery component already recorded for one order/staff."""
    total = int(base.amount_cents)
    for row in db.scalars(
        select(WageEntry).where(
            WageEntry.order_id == order.id,
            WageEntry.staff_id == base.staff_id,
            WageEntry.kind.in_((KIND_REFUND, KIND_SUPPLEMENTARY)),
        )
    ):
        total += int(row.amount_cents)
    return total


def _component_target(db, base: WageEntry, order: Order) -> int | None:
    """Single-rounding target for the whole delivery component.

    Every change (order refund or supplementary fee receipt/refund) is valued by
    flooring the rate against the *combined* current basis once.  Appending the
    difference against the previously recorded component avoids the per-event
    rounding drift that would otherwise accumulate over many small adjustments.
    """
    snapshot = dict(base.snapshot or {})
    mode = snapshot.get('policyMode')
    if mode == 'PERCENT':
        original_cash = int(snapshot.get('originalBaseCashCents', 0))
        net_original = max(0, original_cash - max(0, int(order.refunded_cents or 0)))
        basis = net_original + _included_supplementary_cents(db, order.id)
        return wage_amount('PERCENT', 0, int(snapshot.get('rateBps', 0)), basis)
    if mode == 'FIXED':
        return int(snapshot.get('fixedCents', base.amount_cents))
    return None


def on_refund(db, order: Order, refund) -> WageEntry | None:
    """Append the refund adjustment in the refund's cash-event month.

    The policy, staff and original delivery month come from the base entry.  The
    adjustment itself belongs to the current month so a refund after a paid month
    remains payable/offsettable instead of being stranded in a closed ledger.
    """
    if order is None or refund is None:
        return None
    if not refund.id:
        db.flush()
    lock(db, Order, order.id)
    base = _base_entry(db, order.id)
    if base is None or base.kind != KIND_DELIVERY:
        return None

    source_key = f'REFUND:{refund.id}'
    existing = db.scalar(select(WageEntry).where(WageEntry.source_key == source_key)
                         .with_for_update())
    if existing is not None:
        return existing

    advisory_lock(db, f'payroll:refund:{refund.id}')
    existing = db.scalar(select(WageEntry).where(WageEntry.source_key == source_key)
                         .with_for_update())
    if existing is not None:
        return existing

    snapshot = dict(base.snapshot or {})
    expected = _component_target(db, base, order)
    if expected is None:
        # A marker without a policy cannot be recalculated; keep the refund visible
        # in the order but do not invent a wage policy.
        return None
    previous = _component_total(db, base, order)
    amount = expected - previous
    refunded_total = max(0, int(order.refunded_cents or 0))
    event_at = getattr(refund, 'created_at', None)
    adjustment_period = _cash_event_period(event_at)
    original_cash = int(snapshot.get('originalBaseCashCents', 0))
    net_base = max(0, original_cash - refunded_total)
    supplementary_net = _included_supplementary_cents(db, order.id)

    adjustment_snapshot = {
        **{key: snapshot.get(key) for key in
           ('policyId', 'policyScopeKey', 'policySource', 'policyMode', 'fixedCents', 'rateBps')},
        'adjustmentType': 'REFUND',
        'refundId': refund.id,
        'refundAmountCents': int(refund.amount_cents or 0),
        'refundedTotalCents': refunded_total,
        'originalBaseCashCents': original_cash,
        'netBaseCashCents': net_base,
        'supplementaryNetCents': supplementary_net,
        'basisCents': net_base + supplementary_net,
        'previousBaseComponentCents': previous,
        'recalculatedBaseComponentCents': expected,
        'deliveryTime': snapshot.get('deliveryTime'),
        'originalDeliveryPeriod': base.period,
        'basePeriod': base.period,
        'cashEventPeriod': adjustment_period,
        'adjustmentPeriod': adjustment_period,
        'staffId': base.staff_id,
        'orderId': order.id,
    }
    entry = WageEntry(
        staff_id=base.staff_id,
        order_id=order.id,
        assignment_id=base.assignment_id,
        source_key=source_key,
        kind=KIND_REFUND,
        amount_cents=amount,
        period=adjustment_period,
        snapshot=adjustment_snapshot,
        reason='退款调整',
        actor_id='system:after_sales',
    )
    db.add(entry)
    db.flush()
    audit(db, 'system:after_sales', 'payroll.wage.refund', 'wage_entry', entry.id,
          reason='退款调整', data={'orderId': order.id, 'refundId': refund.id,
                                   'amountCents': amount, 'refundedCents': refunded_total})
    return entry


# ---------------------------------------------------------------------------
# Supplementary fee adjustment (source models are root-owned)
# ---------------------------------------------------------------------------

def apply_supplementary_eligible(db, order: Order, source_key: str, eligible_cents: int, *,
                                 include_in_payroll: bool = True,
                                 amount_cents: int | None = None,
                                 period: str | None = None) -> WageEntry | None:
    """Append the percentage adjustment for one actual supplementary-fee event.

    ``eligible_cents`` is the signed change in eligible cash (settled fee positive,
    fee refund negative) and ``source_key`` must identify that event uniquely.
    ``period`` defaults to the delivery month for direct callers; the root hook
    passes the fee movement's cash-event month.
    """
    if not include_in_payroll or not eligible_cents:
        return None
    base = _base_entry(db, order.id)
    if base is None or base.kind != KIND_DELIVERY:
        return None
    snapshot = dict(base.snapshot or {})
    if snapshot.get('policyMode') != 'PERCENT':
        return None
    key = f'SUPPLEMENTARY:{order.id}:{source_key}'
    existing = db.scalar(select(WageEntry).where(WageEntry.source_key == key)
                         .with_for_update())
    if existing is not None:
        old_eligible = int((existing.snapshot or {}).get('eligibleCents', 0))
        if old_eligible != int(eligible_cents):
            raise DomainError('WAGE_SOURCE_CONFLICT', '同一补充费用来源的金额不一致', 409)
        return existing

    advisory_lock(db, f'payroll:supplementary:{key}')
    existing = db.scalar(select(WageEntry).where(WageEntry.source_key == key)
                         .with_for_update())
    if existing is not None:
        return existing

    amount = (int(amount_cents) if amount_cents is not None
              else signed_rate_amount(int(snapshot.get('rateBps', 0)), eligible_cents))
    adjustment_period = normalize_period(period) if period else base.period
    adjustment_snapshot = {
        **{name: snapshot.get(name) for name in
           ('policyId', 'policyScopeKey', 'policySource', 'policyMode', 'fixedCents', 'rateBps')},
        'adjustmentType': 'SUPPLEMENTARY',
        'supplementarySourceKey': source_key,
        'eligibleCents': int(eligible_cents),
        'includeInPayroll': True,
        'deliveryTime': snapshot.get('deliveryTime'),
        'staffId': base.staff_id,
        'orderId': order.id,
        'originalBaseCashCents': snapshot.get('originalBaseCashCents', 0),
        'extraEligibleCents': int(eligible_cents),
        'originalDeliveryPeriod': base.period,
        'basePeriod': base.period,
        'cashEventPeriod': adjustment_period,
        'adjustmentPeriod': adjustment_period,
    }
    entry = WageEntry(
        staff_id=base.staff_id,
        order_id=order.id,
        assignment_id=base.assignment_id,
        source_key=key,
        kind=KIND_SUPPLEMENTARY,
        amount_cents=amount,
        period=adjustment_period,
        snapshot=adjustment_snapshot,
        reason='补充费用计入工资',
        actor_id='system:supplementary',
    )
    db.add(entry)
    db.flush()
    audit(db, 'system:supplementary', 'payroll.wage.supplementary', 'wage_entry', entry.id,
          reason='补充费用计入工资', data={'orderId': order.id, 'sourceKey': source_key,
                                          'eligibleCents': int(eligible_cents),
                                          'amountCents': amount})
    return entry


def on_supplementary_change(db, order: Order, source_key: str) -> WageEntry | None:
    """Apply the wage effect of one real supplementary receipt/refund.

    ``source_key`` is ``supplementary-movement:<SupplementaryMovement.id>`` (a
    raw id is accepted for older callers).  A receipt adds its actual collected
    amount to the percentage basis; a refund removes it.  The movement id makes
    the adjustment idempotent, and the policy is always the one frozen on the
    original delivery entry.  Each adjustment is appended in the movement's
    cash-event month.  Movements already folded into the delivery aggregate are
    not paid twice.

    The root supplementary service calls this under the same Order row lock it
    takes before inserting the movement; that lock is what serializes the cutoff
    with ``on_delivery``.
    """
    if order is None or not source_key:
        raise DomainError('SUPPLEMENTARY_SOURCE_INVALID', '补充费用来源无效', 422)
    canonical, movement_id = _supplementary_source(source_key)
    movement = db.scalar(
        select(SupplementaryMovement).where(SupplementaryMovement.id == movement_id)
        .with_for_update()
    )
    if movement is None:
        raise DomainError('SUPPLEMENTARY_SOURCE_NOT_FOUND', '补充费用流水不存在', 404,
                          {'sourceKey': source_key})
    charge = db.get(SupplementaryCharge, movement.charge_id)
    if charge is None or charge.order_id != order.id:
        raise DomainError('SUPPLEMENTARY_SOURCE_MISMATCH', '补充费用不属于该订单', 409,
                          {'sourceKey': source_key})
    if not charge.include_in_payroll:
        return None
    if movement.kind == 'RECEIPT':
        eligible = int(movement.amount_cents)
    elif movement.kind == 'REFUND':
        eligible = -int(movement.amount_cents)
    else:
        raise DomainError('SUPPLEMENTARY_SOURCE_INVALID', '补充费用流水类型无效', 422)

    base = _base_entry(db, order.id)
    if base is not None and base.kind == KIND_DELIVERY:
        cutoff = (base.snapshot or {}).get('supplementaryRecordedAt')
        if cutoff and _as_datetime(movement.created_at) <= _as_datetime(cutoff):
            # The delivery aggregate already includes this movement.
            return None
        target = _component_target(db, base, order)
        if target is None:
            return None
        # The movement's cash delta is signed, but the wage amount is the
        # difference between the single-rounding combined target and everything
        # already recorded.  This avoids per-event rounding drift.
        amount = target - _component_total(db, base, order)
        movement_period = _cash_event_period(movement.created_at)
        return apply_supplementary_eligible(
            db, order, canonical, eligible, include_in_payroll=True,
            amount_cents=amount, period=movement_period,
        )
    return None


# ---------------------------------------------------------------------------
# Policy mutation & retroactive application
# ---------------------------------------------------------------------------

def _clean_reason(reason: str | None) -> str:
    value = (reason or '').strip()
    if not value:
        raise DomainError('REASON_REQUIRED', '请填写原因', 422)
    return value[:500]


def _staff_or_404(db, staff_id: str) -> Staff:
    staff = db.get(Staff, staff_id)
    if staff is None:
        raise DomainError('NOT_FOUND', '员工不存在', 404)
    return staff


def _save_policy(db, actor: Actor, staff_id: str | None, payload: PolicyInput) -> WagePolicy:
    require_admin(actor, 'payroll.manage')
    scope_key = f'STAFF:{staff_id}' if staff_id else 'DEFAULT'
    if staff_id:
        _staff_or_404(db, staff_id)
    row = db.scalar(select(WagePolicy).where(WagePolicy.scope_key == scope_key)
                    .with_for_update())
    if row is not None:
        if payload.expected_version is None:
            raise DomainError('VERSION_REQUIRED', '更新工资策略前请刷新并携带版本号', 409)
        if row.version != payload.expected_version:
            raise DomainError('VERSION_CONFLICT', '工资策略已变化，请刷新后重试', 409,
                              {'currentVersion': row.version})
    else:
        if payload.expected_version not in (None, 0):
            raise DomainError('VERSION_CONFLICT', '工资策略尚未创建，请刷新后重试', 409,
                              {'currentVersion': 0})
        row = WagePolicy(scope_key=scope_key, staff_id=staff_id, data={})
        db.add(row)
    row.mode = payload.mode
    row.fixed_cents = payload.fixed_cents
    row.rate_bps = payload.rate_bps
    row.enabled = payload.enabled
    row.data = {**(row.data or {}), 'updatedBy': actor.id, 'reason': payload.reason}
    touch(row)
    db.flush()
    audit(db, actor, 'payroll.policy.update', 'wage_policy', row.id,
          reason=payload.reason, data={'scopeKey': scope_key, 'mode': payload.mode,
                                       'fixedCents': payload.fixed_cents,
                                       'rateBps': payload.rate_bps,
                                       'enabled': payload.enabled})
    return row


def apply_retroactive_policy(db, actor: Actor, order: Order, reason: str,
                             policy_id: str | None = None) -> WageEntry:
    """Convert a NEEDS_CONFIGURATION marker using an explicitly selected policy."""
    require_admin(actor, 'payroll.manage')
    reason = _clean_reason(reason)
    if order is None or order.status not in ('DELIVERED', 'COMPLETED'):
        raise DomainError('ORDER_NOT_DELIVERED', '订单尚未完成交付，不能补录工资', 409)
    lock(db, Order, order.id)
    advisory_lock(db, f'payroll:apply:{order.id}')
    entry = _base_entry(db, order.id)
    if entry is None:
        raise DomainError('WAGE_ENTRY_NOT_FOUND', '该订单没有交付工资记录', 404)
    if entry.kind != KIND_NEEDS_CONFIGURATION:
        raise DomainError('WAGE_ALREADY_APPLIED', '该订单工资已配置，不能重复补录', 409)

    if policy_id:
        policy = db.get(WagePolicy, policy_id)
        if policy is None or not policy.enabled:
            raise DomainError('POLICY_UNAVAILABLE', '所选工资策略不存在或已停用', 422)
        if policy.staff_id and policy.staff_id != entry.staff_id:
            raise DomainError('POLICY_NOT_APPLICABLE', '所选策略不属于该配送员', 422)
        source = 'ADMIN_SELECTED'
    else:
        policy, source = resolve_policy(db, entry.staff_id)
        if policy is None:
            raise DomainError('NEEDS_CONFIGURATION', '尚未配置工资策略，请先设置默认或员工策略', 409)

    snapshot = dict(entry.snapshot or {})
    cash_received = int(snapshot.get('originalBaseCashCents', order.cash_received_cents or 0))
    refunded_total = max(0, int(order.refunded_cents or 0))
    original_net = max(0, cash_received - refunded_total)
    supplementary_net = int(snapshot.get('extraEligibleCents') or 0)
    basis = original_net + supplementary_net
    amount = wage_amount(policy.mode, policy.fixed_cents or 0,
                         policy.rate_bps or 0, basis)
    delivered_at = _as_datetime(snapshot.get('deliveryTime'))
    cutoff = _as_datetime(
        snapshot.get('supplementaryRecordedAt') or snapshot.get('recordedAt')
        or delivered_at
    )
    snapshot.update(_policy_snapshot(policy, source))
    snapshot.update({
        'originalBaseCashCents': cash_received,
        'refundedAtDeliveryCents': refunded_total,
        'baseCashCents': original_net,
        'extraEligibleCents': supplementary_net,
        'supplementaryNetCents': supplementary_net,
        'basisCents': basis,
        'computedAmountCents': amount,
        'deliveryTime': delivered_at.isoformat(),
        'appliedBy': actor.id,
        'appliedAt': utcnow().isoformat(),
        'reason': reason,
        'needsConfiguration': False,
    })
    entry.kind = KIND_DELIVERY
    entry.amount_cents = amount
    entry.period = period_for(delivered_at)
    entry.reason = reason
    entry.actor_id = actor.id
    entry.snapshot = snapshot
    touch(entry)
    audit(db, actor, 'payroll.policy.apply_retroactive', 'wage_entry', entry.id,
          reason=reason, data={'orderId': order.id, 'policyId': policy.id,
                               'amountCents': amount,
                               'policySource': source})
    db.flush()

    # Fees actually collected after the marker was written were skipped while
    # there was no policy.  Append them now under the selected policy instead
    # of folding later cash into the frozen delivery snapshot.
    movements = db.scalars(
        select(SupplementaryMovement)
        .join(SupplementaryCharge,
              SupplementaryMovement.charge_id == SupplementaryCharge.id)
        .where(
            SupplementaryCharge.order_id == order.id,
            SupplementaryCharge.include_in_payroll.is_(True),
            SupplementaryMovement.created_at > cutoff,
        )
        .order_by(SupplementaryMovement.created_at, SupplementaryMovement.id)
    )
    for movement in movements:
        if movement.kind == 'RECEIPT':
            eligible = int(movement.amount_cents)
        elif movement.kind == 'REFUND':
            eligible = -int(movement.amount_cents)
        else:
            continue
        apply_supplementary_eligible(
            db, order, f'{SUPPLEMENTARY_SOURCE_PREFIX}{movement.id}', eligible,
            include_in_payroll=True, period=current_period(),
        )
    return entry


# ---------------------------------------------------------------------------
# Manual adjustments and payouts
# ---------------------------------------------------------------------------

def create_manual_entry(db, actor: Actor, staff_id: str, amount_cents: int,
                        period: str | None, reason: str,
                        order_id: str | None = None) -> WageEntry:
    require_admin(actor, 'payroll.manage')
    reason = _clean_reason(reason)
    staff = _staff_or_404(db, staff_id)
    order = None
    base = None
    if order_id:
        order = db.get(Order, order_id)
        if order is None:
            raise DomainError('NOT_FOUND', '订单不存在', 404)
        base = db.scalar(select(WageEntry).where(
            WageEntry.source_key == f'DELIVERY:{order_id}'))
        if period is None and base is not None:
            period = base.period
    period = normalize_period(period)
    snapshot = {
        'manual': True,
        'createdBy': actor.id,
        'staffId': staff.id,
        'staffName': staff.name,
        'orderId': order.id if order else None,
        'period': period,
        'baseSourceKey': base.source_key if base else None,
        'originalStaffId': base.staff_id if base else None,
        'originalBaseCashCents': (base.snapshot or {}).get('originalBaseCashCents') if base else None,
        'originalAmountCents': base.amount_cents if base else None,
    }
    entry = WageEntry(
        staff_id=staff.id,
        order_id=order.id if order else None,
        assignment_id=base.assignment_id if base else None,
        source_key=f'MANUAL:{uuid4().hex}',
        kind=KIND_MANUAL,
        amount_cents=int(amount_cents),
        period=period,
        snapshot=snapshot,
        reason=reason,
        actor_id=actor.id,
    )
    db.add(entry)
    db.flush()
    audit(db, actor, 'payroll.entry.manual', 'wage_entry', entry.id,
          reason=reason, data={'staffId': staff.id, 'amountCents': entry.amount_cents,
                               'period': period, 'orderId': order.id if order else None})
    return entry


def record_settlement(db, actor: Actor, staff_id: str, period: str,
                      entry_ids: list[str] | None, reason: str) -> WageSettlement:
    require_admin(actor, 'payroll.manage')
    reason = _clean_reason(reason)
    period = normalize_period(period)
    _staff_or_404(db, staff_id)
    if entry_ids is not None:
        if not entry_ids:
            entry_ids = None
        elif len(set(entry_ids)) != len(entry_ids):
            raise DomainError('ENTRY_IDS_DUPLICATE', '工资条目不可重复选择', 422,
                              {'entryIds': entry_ids})
    advisory_lock(db, f'payroll:settlement:{staff_id}:{period}')

    if entry_ids:
        rows = list(db.scalars(
            select(WageEntry).where(WageEntry.id.in_(entry_ids)).with_for_update()
        ))
        found = {row.id for row in rows}
        missing = [entry_id for entry_id in entry_ids if entry_id not in found]
        if missing:
            raise DomainError('NOT_FOUND', '部分工资条目不存在', 404, {'entryIds': missing})
    else:
        # A normal monthly payout consumes every still-unsettled entry from the
        # target month and all earlier months, positive or negative.  The ledger
        # summary already reports those rows as carry-forward, so paying only the
        # old negatives would leave the available balance and the actual payout
        # disagreeing.  Future months are deliberately excluded.
        rows = list(db.scalars(
            select(WageEntry).where(
                WageEntry.staff_id == staff_id,
                WageEntry.kind != KIND_NEEDS_CONFIGURATION,
                WageEntry.id.not_in(select(WageSettlementItem.entry_id)),
                WageEntry.period <= period,
            ).order_by(WageEntry.period, WageEntry.created_at, WageEntry.id)
            .with_for_update()
        ))

    rows = [row for row in rows if row.kind != KIND_NEEDS_CONFIGURATION]
    if not rows:
        raise DomainError('SETTLEMENT_EMPTY', '没有可发放的工资条目', 409)

    # An explicit payout selection must not bypass an older negative adjustment.
    # Carry it into the selected set so a future positive is never paid in full
    # while the closed-month deduction remains stranded.
    selected_ids = {row.id for row in rows}
    carry_rows = list(db.scalars(
        select(WageEntry).where(
            WageEntry.staff_id == staff_id,
            WageEntry.period < period,
            WageEntry.amount_cents < 0,
            WageEntry.kind != KIND_NEEDS_CONFIGURATION,
            WageEntry.id.not_in(select(WageSettlementItem.entry_id)),
            WageEntry.id.not_in(selected_ids),
        ).order_by(WageEntry.period, WageEntry.created_at, WageEntry.id)
        .with_for_update()
    ))
    rows.extend(carry_rows)
    for row in rows:
        if row.staff_id != staff_id:
            raise DomainError('SETTLEMENT_STAFF_MISMATCH', '工资条目不属于所选员工', 409,
                              {'entryId': row.id})
        if row.period > period:
            raise DomainError('SETTLEMENT_PERIOD_INVALID', '不能提前发放未来月份的工资', 409,
                              {'entryId': row.id, 'entryPeriod': row.period})
    already = list(db.scalars(
        select(WageSettlementItem.entry_id).where(
            WageSettlementItem.entry_id.in_([row.id for row in rows])
        )
    ))
    if already:
        raise DomainError('WAGE_ALREADY_SETTLED', '所选工资条目已发放，不能重复发放', 409,
                          {'entryIds': already})

    net = sum(int(row.amount_cents) for row in rows)
    if net < 0:
        raise DomainError('WAGE_NET_NEGATIVE', '所选条目净额为负数，请与后续月份工资合并发放', 409,
                          {'netCents': net, 'entryIds': [row.id for row in rows]})

    settlement = WageSettlement(
        staff_id=staff_id,
        period=period,
        amount_cents=net,
        paid_at=utcnow(),
        paid_by=actor.id,
        reason=reason,
        data={
            'method': 'MANUAL_LEDGER',
            'entryIds': [row.id for row in rows],
            'entryPeriods': {row.id: row.period for row in rows},
            'positiveCents': sum(int(row.amount_cents) for row in rows
                                 if row.amount_cents > 0),
            'negativeCents': sum(int(row.amount_cents) for row in rows
                                 if row.amount_cents < 0),
            'carryForwardCents': sum(int(row.amount_cents) for row in rows
                                     if row.period < period),
        },
    )
    db.add(settlement)
    db.flush()
    for row in rows:
        db.add(WageSettlementItem(settlement_id=settlement.id, entry_id=row.id))
    db.flush()
    audit(db, actor, 'payroll.settlement.record', 'wage_settlement', settlement.id,
          reason=reason, data={'staffId': staff_id, 'period': period,
                               'amountCents': net,
                               'entryIds': [row.id for row in rows]})
    return settlement


# ---------------------------------------------------------------------------
# Read models
# ---------------------------------------------------------------------------

def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ''


def _settlement_index(db, entries: list[WageEntry]) -> dict[str, tuple[WageSettlement, WageSettlementItem]]:
    if not entries:
        return {}
    ids = [entry.id for entry in entries]
    items = list(db.scalars(select(WageSettlementItem).where(
        WageSettlementItem.entry_id.in_(ids))))
    settlement_ids = [item.settlement_id for item in items]
    settlements = list(db.scalars(select(WageSettlement).where(
        WageSettlement.id.in_(settlement_ids)))) if settlement_ids else []
    by_id = {settlement.id: settlement for settlement in settlements}
    return {item.entry_id: (by_id[item.settlement_id], item)
            for item in items if item.settlement_id in by_id}


def _entry_view(db, entry: WageEntry,
                index: dict[str, tuple[WageSettlement, WageSettlementItem]] | None = None) -> dict:
    index = index or {}
    settlement, _ = index.get(entry.id, (None, None))
    return {
        'id': entry.id,
        'version': entry.version,
        'staffId': entry.staff_id,
        'orderId': entry.order_id,
        'assignmentId': entry.assignment_id,
        'sourceKey': entry.source_key,
        'kind': entry.kind,
        'amountCents': int(entry.amount_cents),
        'period': entry.period,
        'reason': entry.reason or '',
        'actorId': entry.actor_id or '',
        'snapshot': entry.snapshot or {},
        'createdAt': _iso(entry.created_at),
        'settlementId': settlement.id if settlement else None,
        'settledAt': _iso(settlement.paid_at) if settlement else None,
        'needsConfiguration': entry.kind == KIND_NEEDS_CONFIGURATION,
    }


def _summary(db, staff: Staff, period: str,
             entries: list[WageEntry] | None = None) -> dict:
    if entries is None:
        entries = list(db.scalars(select(WageEntry).where(
            WageEntry.staff_id == staff.id, WageEntry.period == period)))
    index = _settlement_index(db, entries)
    real = [entry for entry in entries if entry.kind != KIND_NEEDS_CONFIGURATION]
    gross = sum(int(entry.amount_cents) for entry in real if entry.amount_cents > 0)
    deductions = sum(int(entry.amount_cents) for entry in real if entry.amount_cents < 0)
    paid = sum(int(entry.amount_cents) for entry in real if entry.id in index)
    unpaid = sum(int(entry.amount_cents) for entry in real if entry.id not in index)

    carry_rows = list(db.scalars(select(WageEntry).where(
        WageEntry.staff_id == staff.id,
        WageEntry.period < period,
        WageEntry.kind != KIND_NEEDS_CONFIGURATION,
    )))
    carry_index = _settlement_index(db, carry_rows)
    carry = sum(int(entry.amount_cents) for entry in carry_rows
                if entry.id not in carry_index)
    return {
        'staffId': staff.id,
        'staffName': staff.name,
        'entryCount': len(real),
        'grossCents': gross,
        'deductionsCents': deductions,
        'netCents': gross + deductions,
        'paidCents': paid,
        'unpaidCents': unpaid,
        'carryForwardCents': carry,
        'availableCents': unpaid + carry,
        'needsConfigurationCount': sum(
            1 for entry in entries if entry.kind == KIND_NEEDS_CONFIGURATION),
    }


def _settlement_view(db, settlement: WageSettlement,
                     staff_names: dict[str, str] | None = None) -> dict:
    staff_names = staff_names or {}
    items = list(db.scalars(select(WageSettlementItem).where(
        WageSettlementItem.settlement_id == settlement.id)))
    entry_ids = [item.entry_id for item in items]
    entries = list(db.scalars(select(WageEntry).where(WageEntry.id.in_(entry_ids)))) if entry_ids else []
    by_id = {entry.id: entry for entry in entries}
    staff = db.get(Staff, settlement.staff_id)
    paid_by = db.get(Staff, settlement.paid_by) if settlement.paid_by else None
    return {
        'id': settlement.id,
        'version': settlement.version,
        'staffId': settlement.staff_id,
        'staffName': staff.name if staff else staff_names.get(settlement.staff_id, ''),
        'period': settlement.period,
        'amountCents': int(settlement.amount_cents),
        'paidAt': _iso(settlement.paid_at),
        'paidBy': settlement.paid_by or '',
        'paidByName': paid_by.name if paid_by else '',
        'reason': settlement.reason or '',
        'createdAt': _iso(settlement.created_at),
        'items': [
            {
                'entryId': item.entry_id,
                'kind': by_id[item.entry_id].kind if item.entry_id in by_id else '',
                'amountCents': int(by_id[item.entry_id].amount_cents) if item.entry_id in by_id else 0,
                'sourceKey': by_id[item.entry_id].source_key if item.entry_id in by_id else '',
                'orderId': by_id[item.entry_id].order_id if item.entry_id in by_id else None,
                'period': by_id[item.entry_id].period if item.entry_id in by_id else settlement.period,
            }
            for item in items
        ],
    }


def staff_ledger(db, staff_id: str, period: str | None = None) -> dict:
    staff = _staff_or_404(db, staff_id)
    period = normalize_period(period)
    entries = list(db.scalars(select(WageEntry).where(
        WageEntry.staff_id == staff.id, WageEntry.period == period
    ).order_by(WageEntry.created_at, WageEntry.id)))
    index = _settlement_index(db, entries)
    carry_rows = list(db.scalars(select(WageEntry).where(
        WageEntry.staff_id == staff.id,
        WageEntry.period < period,
        WageEntry.kind != KIND_NEEDS_CONFIGURATION,
    ).order_by(WageEntry.period, WageEntry.created_at, WageEntry.id)))
    carry_index = _settlement_index(db, carry_rows)
    carry_rows = [row for row in carry_rows if row.id not in carry_index]
    settlements = list(db.scalars(select(WageSettlement).where(
        WageSettlement.staff_id == staff.id, WageSettlement.period == period
    ).order_by(WageSettlement.paid_at, WageSettlement.id)))
    return {
        'staff': {'id': staff.id, 'name': staff.name},
        'period': period,
        'summary': _summary(db, staff, period, entries),
        'entries': [_entry_view(db, entry, index) for entry in entries],
        'carryForward': [_entry_view(db, entry, carry_index) for entry in carry_rows],
        'settlements': [_settlement_view(db, settlement) for settlement in settlements],
    }


def admin_ledger(db, period: str | None = None, staff_id: str | None = None) -> dict:
    period = normalize_period(period)
    query = select(WageEntry).where(WageEntry.period == period)
    if staff_id:
        _staff_or_404(db, staff_id)
        query = query.where(WageEntry.staff_id == staff_id)
    entries = list(db.scalars(query.order_by(WageEntry.created_at, WageEntry.id)))
    index = _settlement_index(db, entries)

    staff_ids: list[str] = []
    for entry in entries:
        if entry.staff_id not in staff_ids:
            staff_ids.append(entry.staff_id)
    if staff_id and staff_id not in staff_ids:
        staff_ids.append(staff_id)
    summaries = []
    for sid in staff_ids:
        staff = db.get(Staff, sid)
        if staff is not None:
            summaries.append(_summary(db, staff, period))
    summaries.sort(key=lambda item: (item['staffName'], item['staffId']))

    carry_rows: list[WageEntry] = []
    if staff_id:
        carry_query = select(WageEntry).where(
            WageEntry.staff_id == staff_id,
            WageEntry.period < period,
            WageEntry.kind != KIND_NEEDS_CONFIGURATION,
        ).order_by(WageEntry.period, WageEntry.created_at, WageEntry.id)
        carry_rows = list(db.scalars(carry_query))
        carry_index = _settlement_index(db, carry_rows)
        carry_rows = [row for row in carry_rows if row.id not in carry_index]
    else:
        carry_index = {}

    names: dict[str, str] = {}
    for entry in entries:
        if entry.staff_id not in names:
            staff = db.get(Staff, entry.staff_id)
            names[entry.staff_id] = staff.name if staff else ''
    needs = [
        {
            'entryId': entry.id,
            'orderId': entry.order_id,
            'staffId': entry.staff_id,
            'staffName': names.get(entry.staff_id, ''),
            'assignmentId': entry.assignment_id,
            'deliveredAt': (entry.snapshot or {}).get('deliveryTime'),
            'period': entry.period,
            'createdAt': _iso(entry.created_at),
        }
        for entry in entries if entry.kind == KIND_NEEDS_CONFIGURATION
    ]
    return {
        'period': period,
        'summaries': summaries,
        'entries': [_entry_view(db, entry, index) for entry in entries],
        'carryForward': [_entry_view(db, entry, carry_index) for entry in carry_rows],
        'needsConfiguration': needs,
    }


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------

@router.get('/payroll/me', response_model=Envelope[StaffLedgerDTO])
def my_ledger(period: str | None = Query(default=None),
              db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    """The signed-in employee's own ledger.  Staff identity never comes from query."""
    require_staff(actor)
    return ok(staff_ledger(db, actor.staff_id, period))


@router.get('/admin/payroll/staff', response_model=Envelope[list[PayrollStaffDTO]])
def payroll_staff(db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    """Minimal staff catalog for payroll pickers, gated by payroll.manage only."""
    require_admin(actor, 'payroll.manage')
    rows = db.scalars(select(Staff).order_by(Staff.name, Staff.id))
    return ok([
        {'id': row.id, 'name': row.name, 'role': row.role,
         'enabled': bool(row.enabled)}
        for row in rows
    ])


@router.get('/admin/payroll/policies', response_model=Envelope[PolicyCatalogDTO])
def policy_catalog(db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'payroll.manage')
    default = db.scalar(select(WagePolicy).where(WagePolicy.scope_key == 'DEFAULT'))
    overrides = list(db.scalars(select(WagePolicy).where(
        WagePolicy.scope_key.like('STAFF:%')).order_by(WagePolicy.created_at)))
    return ok({
        'defaultPolicy': _policy_view(db, default),
        'overrides': [_policy_view(db, row) for row in overrides if row is not None],
    })


@router.put('/admin/payroll/policies/default', response_model=Envelope[PolicyDTO])
def update_default_policy(payload: PolicyInput, db=Depends(get_db),
                          actor: Actor = Depends(actor_dep)):
    row = _save_policy(db, actor, None, payload)
    return ok(_policy_view(db, row))


@router.put('/admin/payroll/policies/staff/{staff_id}', response_model=Envelope[PolicyDTO])
def update_staff_policy(staff_id: str, payload: PolicyInput, db=Depends(get_db),
                        actor: Actor = Depends(actor_dep)):
    row = _save_policy(db, actor, staff_id, payload)
    return ok(_policy_view(db, row))


@router.post('/admin/payroll/deliveries/{order_id}/apply-policy',
             response_model=Envelope[WageEntryDTO])
def apply_policy(order_id: str, payload: ApplyPolicyInput, db=Depends(get_db),
                 actor: Actor = Depends(actor_dep)):
    order = db.get(Order, order_id)
    if order is None:
        raise DomainError('NOT_FOUND', '订单不存在', 404)
    entry = apply_retroactive_policy(db, actor, order, payload.reason,
                                     payload.policy_id)
    return ok(_entry_view(db, entry))


@router.get('/admin/payroll/ledger', response_model=Envelope[AdminLedgerDTO])
def ledger(period: str | None = Query(default=None),
           staff_id: str | None = Query(default=None, alias='staffId'),
           db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'payroll.manage')
    return ok(admin_ledger(db, period, staff_id))


@router.post('/admin/payroll/entries', response_model=Envelope[WageEntryDTO])
def add_manual_entry(payload: ManualEntryInput, db=Depends(get_db),
                     actor: Actor = Depends(actor_dep)):
    entry = create_manual_entry(db, actor, payload.staff_id, payload.amount_cents,
                                payload.period, payload.reason, payload.order_id)
    return ok(_entry_view(db, entry))


@router.get('/admin/payroll/settlements', response_model=Envelope[list[SettlementDTO]])
def settlements(period: str | None = Query(default=None),
                staff_id: str | None = Query(default=None, alias='staffId'),
                db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'payroll.manage')
    period = normalize_period(period)
    query = select(WageSettlement).where(WageSettlement.period == period)
    if staff_id:
        _staff_or_404(db, staff_id)
        query = query.where(WageSettlement.staff_id == staff_id)
    rows = list(db.scalars(query.order_by(WageSettlement.paid_at.desc())))
    return ok([_settlement_view(db, row) for row in rows])


@router.post('/admin/payroll/settlements', response_model=Envelope[SettlementDTO])
def create_settlement(payload: SettlementInput, db=Depends(get_db),
                      actor: Actor = Depends(actor_dep)):
    row = record_settlement(db, actor, payload.staff_id, payload.period,
                            payload.entry_ids, payload.reason)
    return ok(_settlement_view(db, row))
