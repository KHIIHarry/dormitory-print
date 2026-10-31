"""Independent-review regression tests (isolated real PostgreSQL).

P1: cash captured on a closed/expired order must not disappear when the order
is trashed/purged.  An UNMATCHED receipt with a positive net refund is a live
cash liability; once fully refunded its gross and refunds survive only as
separate anonymous aggregates (never as original Payment cash, which would
double-count).
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core import DomainError
from app.maintenance import (
    _close_expired_orders,
    _order_active_blockers,
    _order_ledger,
    _purge_plan,
    process_purge_job,
)
from app.models import AnonymousLedger, Quote, Receipt, ReceiptAdjustment, TrashEntry, utcnow

APP_MODULES = ['auth', 'commerce', 'exports', 'maintenance']


def _cancelled_order_with_receipt(db, staff_factory, *, refunded_cents: int):
    from test_maintenance import make_order

    order = make_order(db, status='CANCELLED', settlement_kind=None,
                       total_cents=1000, cash_received_cents=0)
    staff = staff_factory(role='ADMIN')
    receipt = Receipt(
        order_id=order.id, amount_cents=1000, status='UNMATCHED',
        staff_id=staff.id,
        data={'refundRequired': True, 'reasonCode': 'CLOSED_ORDER'},
    )
    db.add(receipt)
    db.flush()
    if refunded_cents:
        db.add(ReceiptAdjustment(receipt_id=receipt.id, amount_cents=refunded_cents,
                                 staff_id=staff.id, reason='线下退回'))
        if refunded_cents == receipt.amount_cents:
            receipt.status = 'REFUNDED'
    db.commit()
    return order, receipt


def test_cancelled_unsettled_order_with_outstanding_unmatched_cash_is_blocked(
        db, staff_factory):
    order, receipt = _cancelled_order_with_receipt(
        db, staff_factory, refunded_cents=400)

    assert _order_active_blockers(db, order) == [f'UNMATCHED_RECEIPT:{receipt.id}']
    ledger = _order_ledger(db, order, db.get(Quote, order.quote_id))
    assert ledger['cashReceivedCents'] == 0
    assert ledger['refundCents'] == 0
    assert ledger['unmatchedReceiptCents'] == 1000
    assert ledger['unmatchedReceiptRefundCents'] == 400

    with pytest.raises(DomainError) as blocked:
        _purge_plan(db, None, 'ORDER', order)
    assert blocked.value.code == 'PURGE_BLOCKED'
    assert f'UNMATCHED_RECEIPT:{receipt.id}' in blocked.value.details['blockers']
    assert db.scalar(select(Receipt.id).where(Receipt.order_id == order.id)) == receipt.id


def test_fully_refunded_unmatched_cash_is_preserved_as_separate_anonymous_totals(
        db, staff_factory):
    order, receipt = _cancelled_order_with_receipt(
        db, staff_factory, refunded_cents=1000)

    assert _order_active_blockers(db, order) == []
    ledger = _order_ledger(db, order, db.get(Quote, order.quote_id))
    assert ledger['cashReceivedCents'] == 0
    assert ledger['refundCents'] == 0
    assert ledger['unmatchedReceiptCents'] == 1000
    assert ledger['unmatchedReceiptRefundCents'] == 1000

    plan = _purge_plan(db, None, 'ORDER', order)
    assert plan['ledger']['unmatchedReceiptCents'] == 1000
    assert plan['ledger']['unmatchedReceiptRefundCents'] == 1000


def test_unmatched_cash_does_not_stop_auto_close_but_blocks_later_cleanup(
        db, staff_factory):
    """R06: expiry still closes the order; refund handling is a separate queue."""
    from test_maintenance import make_order

    order = make_order(db, status='PENDING_CONFIRMATION', settlement_kind=None,
                       total_cents=1000, cash_received_cents=0)
    order.close_at = utcnow() - timedelta(minutes=1)
    staff = staff_factory(role='ADMIN')
    receipt = Receipt(
        order_id=order.id, amount_cents=1000, status='UNMATCHED',
        staff_id=staff.id,
        data={'refundRequired': True, 'reasonCode': 'CLOSED_ORDER'},
    )
    db.add(receipt)
    db.commit()

    assert _close_expired_orders(db, utcnow()) == 1
    db.refresh(order)
    assert order.status == 'CANCELLED'
    assert receipt.status == 'UNMATCHED'
    assert f'UNMATCHED_RECEIPT:{receipt.id}' in _order_active_blockers(db, order)


def test_fully_refunded_receipt_purge_keeps_separate_anonymous_totals(
        db, api, admin_client, staff_factory):
    order, receipt = _cancelled_order_with_receipt(
        db, staff_factory, refunded_cents=1000)
    order_id = order.id
    receipt_id = receipt.id

    trashed = api(admin_client, '/admin/trash', {
        'targetType': 'ORDER', 'targetId': order_id,
        'expectedVersion': order.version, 'reason': 'fully refunded cash',
    })
    assert trashed.status_code == 200, trashed.text
    entry_id = trashed.json()['data']['id']
    process_purge_job(db, entry_id)
    db.commit()
    db.expire_all()

    assert db.get(TrashEntry, entry_id).status == 'PURGED'
    assert db.get(type(order), order_id) is None
    assert db.get(Receipt, receipt_id) is None
    ledger = db.scalar(select(AnonymousLedger).where(
        AnonymousLedger.bucket == f'trash:{entry_id}'))
    assert ledger is not None
    assert ledger.totals['cashReceivedCents'] == 0
    assert ledger.totals['refundCents'] == 0
    assert ledger.totals['unmatchedReceiptCents'] == 1000
    assert ledger.totals['unmatchedReceiptRefundCents'] == 1000
