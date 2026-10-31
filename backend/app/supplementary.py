"""Supplementary fees: itemised manual charges that are independent of the
original order, quote, payment and fulfilment ledgers.

F01: the administrator records the fee immediately; the customer sees the fee
and may dispute it.  F02A: the exact final fee is manual and is never changed by
coupons, volume tiers or employee discounts.  F03A: fees may be added after the
original order was settled or delivered; collecting/refunding the fee is an
independent manual cash ledger and never changes the original fulfilment.

The charge's amount, purpose and payroll flag are immutable.  Money movements
are append-only: a refund never edits an earlier receipt and never re-opens
money that was already collected (it becomes a customer credit instead).
"""
from __future__ import annotations

import importlib

from fastapi import APIRouter, Depends
from sqlalchemy import select

from .core import (
    Actor, DomainError, Envelope, actor_dep, audit, authorize_order, grant_scopes,
    lock, ok, owns_principal, require_admin, touch,
)
from .db import get_db
from .models import (
    Order, SupplementaryCharge, SupplementaryDispute, SupplementaryMovement, WageEntry,
    utcnow,
)
from .settings import get_settings
from .supplementary_schemas import (
    ChargeAction, ChargeInput, ChargeList, ChargeView, DisputeInput, DisputeResolveInput,
    DisputeView, MovementView, ReceiptInput, RefundInput, SupplementarySummaryDTO,
)

router = APIRouter()

ACTIVE = 'ACTIVE'
CANCELLED = 'CANCELLED'
OPEN_DISPUTE = 'OPEN'
CLOSED_ORDER_STATUSES = {'CANCELLED', 'CANCELED', 'CLOSED', 'COMPLETED', 'REFUNDED'}

SUPPLEMENTARY_EXPORT_FIELDS = (
    'orderId', 'chargeId', 'purpose', 'amountCents', 'includeInPayroll', 'status',
    'grossReceivedCents', 'refundedCents', 'netReceivedCents', 'outstandingCents',
    'createdAt',
)


def _net_received(charge: SupplementaryCharge) -> int:
    return max(0, int(charge.cash_received_cents or 0) - int(charge.refunded_cents or 0))


def _outstanding(charge: SupplementaryCharge) -> int:
    if charge.status != ACTIVE:
        return 0
    return max(0, int(charge.amount_cents) - int(charge.cash_received_cents or 0))


def _payroll_state(movement: SupplementaryMovement) -> dict:
    return dict((movement.data or {}).get('payroll') or {})


def _set_payroll_state(movement: SupplementaryMovement, state: dict) -> None:
    movement.data = {**(movement.data or {}), 'payroll': state}


def _payroll_result_state(entry: WageEntry | None) -> dict:
    """Record the exact outcome of a real payroll hook call.

    The payroll worker returns a freshly appended ``WageEntry`` when the
    movement changed wages, or ``None`` when no row was needed: the movement
    may already be folded into the delivery basis, the policy may be fixed
    wage, or there may be no delivery base.  ``None`` is not an error and is
    not ``SKIPPED`` (the hook did run); it must never claim a new wage row.
    """
    if entry is None:
        return {'status': 'HOOK_CALLED'}
    return {'status': 'APPLIED', 'entryId': entry.id}


def _after_financial_movement(db, order: Order, charge: SupplementaryCharge,
                              movement: SupplementaryMovement) -> None:
    """Notify payroll only for a real receipt/refund, never for fee creation.

    The source key is the append-only movement id, so payroll can key its wage
    adjustment uniquely.  The worker returns a ``WageEntry`` when it wrote a
    new row and ``None`` when the movement needs no new row.  A deployment
    without the payroll worker must not silently pretend the movement does not
    change wages: in production the request fails, while local/test runs record
    a visible pending state.
    """
    if not charge.include_in_payroll:
        _set_payroll_state(movement, {'status': 'SKIPPED', 'reason': 'NOT_INCLUDED'})
        return
    try:
        module = importlib.import_module('.payroll', __package__)
    except ModuleNotFoundError as error:
        if error.name not in ('app.payroll', f'{__package__}.payroll'):
            raise
        module = None
    handler = getattr(module, 'on_supplementary_change', None) if module else None
    if handler is None:
        if get_settings().environment == 'production':
            raise DomainError(
                'PAYROLL_HOOK_UNAVAILABLE',
                '工资模块不可用，补充费用不能假装无需更新工资',
                503,
            )
        _set_payroll_state(movement, {
            'status': 'PENDING', 'reason': 'PAYROLL_UNAVAILABLE',
        })
        return
    result = handler(db, order, movement.id)
    _set_payroll_state(movement, _payroll_result_state(result))


def _movement_view(movement: SupplementaryMovement, admin: bool) -> dict:
    payroll = _payroll_state(movement)
    return {
        'id': movement.id,
        'kind': movement.kind,
        'amountCents': movement.amount_cents,
        'reason': movement.reason,
        'createdAt': movement.created_at.isoformat(),
        'actorId': movement.actor_id if admin else None,
        'payrollStatus': payroll.get('status') if admin else None,
    }


def _dispute_view(dispute: SupplementaryDispute, admin: bool) -> dict:
    return {
        'id': dispute.id,
        'version': dispute.version,
        'principalId': dispute.principal_id if admin else None,
        'reason': dispute.reason,
        'status': dispute.status,
        'resolution': dispute.resolution,
        'createdAt': dispute.created_at.isoformat(),
        'resolvedAt': (dispute.updated_at.isoformat()
                       if dispute.status != OPEN_DISPUTE else None),
        'actorId': dispute.actor_id if admin else None,
    }


def charge_view(db, charge: SupplementaryCharge, admin: bool = False) -> dict:
    movements = list(db.scalars(
        select(SupplementaryMovement)
        .where(SupplementaryMovement.charge_id == charge.id)
        .order_by(SupplementaryMovement.created_at, SupplementaryMovement.id)
    ))
    disputes = list(db.scalars(
        select(SupplementaryDispute)
        .where(SupplementaryDispute.charge_id == charge.id)
        .order_by(SupplementaryDispute.created_at.desc(), SupplementaryDispute.id)
    ))
    return {
        'id': charge.id,
        'orderId': charge.order_id,
        'purpose': charge.purpose,
        'amountCents': charge.amount_cents,
        'includeInPayroll': bool(charge.include_in_payroll),
        'status': charge.status,
        'grossReceivedCents': int(charge.cash_received_cents or 0),
        'refundedCents': int(charge.refunded_cents or 0),
        'netReceivedCents': _net_received(charge),
        'outstandingCents': _outstanding(charge),
        'creditCents': int(charge.refunded_cents or 0),
        'createdBy': charge.created_by if admin else None,
        'createdAt': charge.created_at.isoformat(),
        'updatedAt': charge.updated_at.isoformat(),
        'version': charge.version,
        'movements': [_movement_view(row, admin) for row in movements],
        'disputes': [_dispute_view(row, admin) for row in disputes],
        'data': dict(charge.data or {}) if admin else {},
    }


def supplementary_summary(db, order: Order) -> dict:
    """Strict summary with the original order ledger and the extras separated.

    ``original`` repeats the order's original-only cash/total fields.  ``extras``
    is the supplementary aggregate that the order view/dashboard can add
    without folding fees into the original total.  Cancelled fees are never
    owed, while their historical receipts/refunds remain reported.
    """
    charges = list(db.scalars(
        select(SupplementaryCharge)
        .where(SupplementaryCharge.order_id == order.id)
        .order_by(SupplementaryCharge.created_at, SupplementaryCharge.id)
    ))
    active = [row for row in charges if row.status == ACTIVE]
    gross = sum(int(row.cash_received_cents or 0) for row in charges)
    refunded = sum(int(row.refunded_cents or 0) for row in charges)
    fee_total = sum(int(row.amount_cents) for row in active)
    outstanding = sum(_outstanding(row) for row in active)
    extras = {
        'chargeCount': len(charges),
        'activeChargeCount': len(active),
        'cancelledChargeCount': len(charges) - len(active),
        'feeTotalCents': fee_total,
        'grossReceivedCents': gross,
        'refundedCents': refunded,
        'netReceivedCents': max(0, gross - refunded),
        'outstandingCents': outstanding,
        'includeInPayrollFeeTotalCents': sum(
            int(row.amount_cents) for row in active if row.include_in_payroll
        ),
    }
    original = {
        'totalCents': int(order.total_cents),
        'cashReceivedCents': int(order.cash_received_cents or 0),
        'refundedCents': int(order.refunded_cents or 0),
        'settlementKind': order.settlement_kind,
        'status': order.status,
    }
    return {
        'orderId': order.id,
        'orderVersion': order.version,
        'original': original,
        'extras': extras,
        'combined': {
            'totalCents': original['totalCents'] + extras['feeTotalCents'],
            'grossReceivedCents': original['cashReceivedCents'] + extras['grossReceivedCents'],
            'refundedCents': original['refundedCents'] + extras['refundedCents'],
            'netReceivedCents': max(
                0, original['cashReceivedCents'] - original['refundedCents']
            ) + extras['netReceivedCents'],
            'outstandingCents': extras['outstandingCents'],
        },
    }


def supplementary_export_facts(db, order: Order) -> list[dict]:
    """Export rows for finance/reporting; deliberately contains no customer PII."""
    charges = list(db.scalars(
        select(SupplementaryCharge)
        .where(SupplementaryCharge.order_id == order.id)
        .order_by(SupplementaryCharge.created_at, SupplementaryCharge.id)
    ))
    facts = []
    for charge in charges:
        facts.append({
            'orderId': order.id,
            'chargeId': charge.id,
            'purpose': charge.purpose,
            'amountCents': int(charge.amount_cents),
            'includeInPayroll': bool(charge.include_in_payroll),
            'status': charge.status,
            'grossReceivedCents': int(charge.cash_received_cents or 0),
            'refundedCents': int(charge.refunded_cents or 0),
            'netReceivedCents': _net_received(charge),
            'outstandingCents': _outstanding(charge),
            'createdAt': charge.created_at.isoformat(),
        })
    return facts


def create_charge(db, actor: Actor, order: Order, body: ChargeInput) -> SupplementaryCharge:
    """Add an immutable, exact fee; never touch the original quote/order money."""
    if order.deleted_at:
        raise DomainError('NOT_FOUND', '订单不存在或不可访问', 404)
    if order.status in CLOSED_ORDER_STATUSES:
        raise DomainError('ORDER_CLOSED', '已关闭或撤销的订单不能新增补充费用', 409)
    if not order.settlement_kind and not order.cash_received_cents and order.status != 'DELIVERED':
        raise DomainError('SETTLEMENT_REQUIRED', '原订单结算或交付后才能新增补充费用', 409)
    charge = SupplementaryCharge(
        order_id=order.id,
        purpose=body.purpose.strip(),
        amount_cents=body.amount_cents,
        include_in_payroll=body.include_in_payroll,
        status=ACTIVE,
        cash_received_cents=0,
        refunded_cents=0,
        created_by=actor.id,
        data={'createdByRole': actor.role},
    )
    db.add(charge)
    db.flush()
    # Import lazily so root commerce can import this module for OrderView
    # without a module-import cycle (commerce also owns add_history).
    from .commerce import add_history

    add_history(order, '补充费用已新增', f'新增补充费用 {charge.amount_cents} 分：{charge.purpose}')
    touch(order)
    audit(db, actor, 'SUPPLEMENTARY_CHARGE_CREATED', 'SUPPLEMENTARY_CHARGE', charge.id,
          charge.purpose, {'amountCents': charge.amount_cents,
                           'includeInPayroll': bool(charge.include_in_payroll)})
    return charge


def _lock_charge(db, charge: SupplementaryCharge, expected_version: int) -> tuple[Order, SupplementaryCharge]:
    # Consistent lock order for every money mutation: order first, then charge.
    order = lock(db, Order, charge.order_id)
    return order, lock(db, SupplementaryCharge, charge.id, expected_version)


def _record_movement(db, actor: Actor, order: Order, charge: SupplementaryCharge,
                     kind: str, amount_cents: int, reason: str) -> SupplementaryCharge:
    movement = SupplementaryMovement(
        charge_id=charge.id,
        kind=kind,
        amount_cents=amount_cents,
        reason=reason.strip(),
        actor_id=actor.id,
        data={},
    )
    db.add(movement)
    db.flush()
    if kind == 'RECEIPT':
        charge.cash_received_cents = int(charge.cash_received_cents or 0) + amount_cents
    else:
        charge.refunded_cents = int(charge.refunded_cents or 0) + amount_cents
    touch(charge)
    db.flush()
    _after_financial_movement(db, order, charge, movement)
    audit(
        db, actor,
        'SUPPLEMENTARY_RECEIPT' if kind == 'RECEIPT' else 'SUPPLEMENTARY_REFUND',
        'SUPPLEMENTARY_MOVEMENT', movement.id, movement.reason,
        {'chargeId': charge.id, 'amountCents': amount_cents},
    )
    return charge


def record_receipt(db, actor: Actor, charge: SupplementaryCharge, body: ReceiptInput) -> SupplementaryCharge:
    require_admin(actor, 'payment.manage')
    order, charge = _lock_charge(db, charge, body.expected_version)
    if charge.status != ACTIVE:
        raise DomainError('CHARGE_NOT_ACTIVE', '已取消的补充费用不能再收款', 409)
    remaining = int(charge.amount_cents) - int(charge.cash_received_cents or 0)
    if body.amount_cents > remaining:
        raise DomainError('RECEIPT_EXCEEDS_OUTSTANDING', '收款金额超过剩余应收金额', 422,
                          {'remainingCents': remaining})
    return _record_movement(db, actor, order, charge, 'RECEIPT', body.amount_cents, body.reason)


def record_refund(db, actor: Actor, charge: SupplementaryCharge, body: RefundInput) -> SupplementaryCharge:
    require_admin(actor, 'payment.manage')
    order, charge = _lock_charge(db, charge, body.expected_version)
    if charge.status != ACTIVE:
        raise DomainError('CHARGE_NOT_ACTIVE', '已取消的补充费用不能再退款', 409)
    refundable = int(charge.cash_received_cents or 0) - int(charge.refunded_cents or 0)
    if body.amount_cents > refundable:
        raise DomainError('REFUND_EXCEEDS_COLLECTED', '退款金额超过尚未退回的实收金额', 422,
                          {'refundableCents': max(0, refundable)})
    return _record_movement(db, actor, order, charge, 'REFUND', body.amount_cents, body.reason)


def cancel_charge(db, actor: Actor, charge: SupplementaryCharge, body: ChargeAction) -> SupplementaryCharge:
    require_admin(actor, 'payment.manage')
    order, charge = _lock_charge(db, charge, body.expected_version)
    if charge.status != ACTIVE:
        raise DomainError('CHARGE_NOT_ACTIVE', '补充费用已经取消', 409)
    if _net_received(charge) != 0:
        raise DomainError('CHARGE_NET_RECEIVED', '尚有未退回的实收金额，请先全额退款再取消', 409,
                          {'netReceivedCents': _net_received(charge)})
    charge.status = CANCELLED
    charge.data = {
        **(charge.data or {}),
        'cancelReason': body.reason.strip(),
        'cancelledAt': utcnow().isoformat(),
        'cancelledBy': actor.id,
    }
    touch(charge)
    db.flush()
    audit(db, actor, 'SUPPLEMENTARY_CHARGE_CANCELLED', 'SUPPLEMENTARY_CHARGE', charge.id,
          body.reason, {'outstandingCents': 0})
    return charge


def create_dispute(db, actor: Actor, charge: SupplementaryCharge, body: DisputeInput) -> SupplementaryDispute:
    if charge.status != ACTIVE:
        raise DomainError('CHARGE_NOT_ACTIVE', '已取消的补充费用不能提交争议', 409)
    dispute = SupplementaryDispute(
        charge_id=charge.id,
        principal_id=actor.principal_id,
        reason=body.reason.strip(),
        status=OPEN_DISPUTE,
        resolution='',
        actor_id=actor.id,
    )
    db.add(dispute)
    db.flush()
    touch(charge)
    audit(db, actor, 'SUPPLEMENTARY_DISPUTE_OPENED', 'SUPPLEMENTARY_DISPUTE', dispute.id,
          dispute.reason, {'chargeId': charge.id})
    return dispute


def resolve_dispute(db, actor: Actor, dispute: SupplementaryDispute,
                    body: DisputeResolveInput) -> SupplementaryDispute:
    require_admin(actor, 'payment.manage')
    # Read the charge only to establish the order, then lock order/charge/dispute
    # in the documented order.
    charge_ref = db.scalar(
        select(SupplementaryCharge.order_id).where(SupplementaryCharge.id == dispute.charge_id)
    )
    if not charge_ref:
        raise DomainError('NOT_FOUND', '补充费用不存在', 404)
    lock(db, Order, charge_ref)
    lock(db, SupplementaryCharge, dispute.charge_id)
    dispute = lock(db, SupplementaryDispute, dispute.id, body.expected_version)
    if dispute.status != OPEN_DISPUTE:
        raise DomainError('DISPUTE_NOT_OPEN', '该争议已经处理', 409)
    dispute.status = body.status
    dispute.resolution = body.resolution.strip()
    touch(dispute)
    db.flush()
    audit(db, actor, 'SUPPLEMENTARY_DISPUTE_RESOLVED', 'SUPPLEMENTARY_DISPUTE', dispute.id,
          dispute.resolution, {'status': dispute.status})
    return dispute


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------

@router.post('/admin/orders/{order_id}/supplementary-charges', response_model=Envelope[ChargeView])
def admin_create_charge(order_id: str, body: ChargeInput, db=Depends(get_db),
                        actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'payment.manage')
    order = lock(db, Order, order_id, body.expected_version)
    return ok(charge_view(db, create_charge(db, actor, order, body), True))


@router.get('/admin/orders/{order_id}/supplementary-charges', response_model=Envelope[ChargeList])
def admin_list_charges(order_id: str, db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'payment.manage')
    order = db.get(Order, order_id)
    if order is None:
        raise DomainError('NOT_FOUND', '订单不存在或不可访问', 404)
    charges = list(db.scalars(
        select(SupplementaryCharge)
        .where(SupplementaryCharge.order_id == order.id)
        .order_by(SupplementaryCharge.created_at, SupplementaryCharge.id)
    ))
    return ok({
        'items': [charge_view(db, charge, True) for charge in charges],
        'summary': supplementary_summary(db, order),
    })


@router.get('/orders/{order_id}/supplementary-charges', response_model=Envelope[ChargeList])
def customer_list_charges(order_id: str, db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    order = db.get(Order, order_id)
    authorize_order(db, actor, order)
    charges = list(db.scalars(
        select(SupplementaryCharge)
        .where(SupplementaryCharge.order_id == order.id)
        .order_by(SupplementaryCharge.created_at, SupplementaryCharge.id)
    ))
    return ok({
        'items': [charge_view(db, charge, False) for charge in charges],
        'summary': supplementary_summary(db, order),
    })


@router.post('/admin/supplementary-charges/{charge_id}/receipts', response_model=Envelope[ChargeView])
def admin_receipt(charge_id: str, body: ReceiptInput, db=Depends(get_db),
                  actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'payment.manage')
    charge = db.get(SupplementaryCharge, charge_id)
    if charge is None:
        raise DomainError('NOT_FOUND', '补充费用不存在', 404)
    return ok(charge_view(db, record_receipt(db, actor, charge, body), True))


@router.post('/admin/supplementary-charges/{charge_id}/refunds', response_model=Envelope[ChargeView])
def admin_refund(charge_id: str, body: RefundInput, db=Depends(get_db),
                 actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'payment.manage')
    charge = db.get(SupplementaryCharge, charge_id)
    if charge is None:
        raise DomainError('NOT_FOUND', '补充费用不存在', 404)
    return ok(charge_view(db, record_refund(db, actor, charge, body), True))


@router.post('/admin/supplementary-charges/{charge_id}/cancel', response_model=Envelope[ChargeView])
def admin_cancel(charge_id: str, body: ChargeAction, db=Depends(get_db),
                 actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'payment.manage')
    charge = db.get(SupplementaryCharge, charge_id)
    if charge is None:
        raise DomainError('NOT_FOUND', '补充费用不存在', 404)
    return ok(charge_view(db, cancel_charge(db, actor, charge, body), True))


@router.post('/orders/{order_id}/supplementary-charges/{charge_id}/disputes',
             response_model=Envelope[DisputeView])
def customer_dispute(order_id: str, charge_id: str, body: DisputeInput, db=Depends(get_db),
                     actor: Actor = Depends(actor_dep)):
    order = db.get(Order, order_id)
    # ORDER_VIEW is enough to see the fee, but a readonly link cannot write.
    authorize_order(db, actor, order)
    if not owns_principal(db, actor, order.principal_id) and \
            'ORDER_AFTER_SALES' not in grant_scopes(db, actor, order.id):
        raise DomainError('FORBIDDEN', '只读授权不能发起补充费用争议', 403)
    order = lock(db, Order, order.id)
    charge = lock(db, SupplementaryCharge, charge_id, body.expected_version)
    if charge.order_id != order.id:
        raise DomainError('NOT_FOUND', '补充费用不存在', 404)
    case = create_dispute(db, actor, charge, body)
    return ok(_dispute_view(case, False))


@router.post('/admin/supplementary-charges/{charge_id}/disputes/{dispute_id}/resolve',
             response_model=Envelope[DisputeView])
def admin_resolve_dispute(charge_id: str, dispute_id: str, body: DisputeResolveInput,
                          db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'payment.manage')
    dispute = db.get(SupplementaryDispute, dispute_id)
    if dispute is None or dispute.charge_id != charge_id:
        raise DomainError('NOT_FOUND', '争议不存在', 404)
    return ok(_dispute_view(resolve_dispute(db, actor, dispute, body), True))
