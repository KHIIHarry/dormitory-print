"""Procurement spending and confirmed cash balances, never inventory profit."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import re
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from pydantic import Field, StrictInt, field_validator, model_validator
from sqlalchemy import BigInteger, String, cast, column, func, select, true
from sqlalchemy.dialects.postgresql import JSONB

from .core import DTO, DomainError, Envelope, actor_dep, audit, lock, ok, require_admin, touch
from .db import get_db
from .models import (AnonymousLedger, OperatingExpense, Payment, Refund,
                     SupplementaryCharge, SupplementaryMovement, WageSettlement, utcnow)

router = APIRouter()
SHANGHAI = ZoneInfo('Asia/Shanghai')
Category = Literal['PAPER', 'INK', 'OTHER']


class ExpenseInput(DTO):
    category: Category
    spent_on: date
    amount_cents: StrictInt = Field(gt=0, le=2147483647)
    paper_sheets: StrictInt | None = Field(default=None, gt=0, le=2147483647)
    note: str = Field(default='', max_length=500)

    @field_validator('spent_on', mode='before')
    @classmethod
    def date_text(cls, value):
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError('请选择有效的支出日期')
        return value

    @model_validator(mode='after')
    def cash_facts(self):
        if self.spent_on > utcnow().astimezone(SHANGHAI).date():
            raise ValueError('支出日期不能晚于今天')
        if (self.category == 'PAPER') != (self.paper_sheets is not None):
            raise ValueError('纸张采购须填写正整数张数；其他支出不填张数')
        self.note = self.note.strip()
        return self


class VoidInput(DTO):
    expected_version: StrictInt = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator('reason')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('请填写作废原因')
        return value.strip()


class ExpenseView(DTO):
    id: str
    version: int
    category: Category
    spent_on: date
    amount_cents: int
    paper_sheets: int | None
    note: str
    status: Literal['ACTIVE', 'VOIDED']
    void_reason: str
    created_at: datetime


class ExpenseList(DTO):
    items: list[ExpenseView]
    total: int
    page: int
    page_size: int


class FinanceSummary(DTO):
    month: str | None
    confirmed_received_cents: int
    refund_cents: int
    net_received_cents: int
    paper_purchase_cents: int
    ink_purchase_cents: int
    other_expense_cents: int
    consumables_balance_cents: int
    paid_wages_cents: int
    balance_cents: int
    paper_sheets: int
    paper_unit_yuan: str | None
    legacy_archive_count: int


def expense_view(row):
    return ExpenseView(id=row.id, version=row.version, category=row.category, spent_on=row.spent_on,
        amount_cents=row.amount_cents, paper_sheets=row.paper_sheets, note=row.note,
        status=row.status, void_reason=row.void_reason, created_at=row.created_at)


def month_bounds(month):
    if month is None:
        return None, None
    try:
        if not re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])', month):
            raise ValueError()
        start = date.fromisoformat(month + '-01')
        end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    except ValueError:
        raise DomainError('FINANCE_MONTH_INVALID', '请选择有效月份', 422)
    return start, end


def cash_sum(model, amount_column, time_column, bounds, *conditions):
    start, end = bounds
    statement = select(func.coalesce(func.sum(amount_column), 0)).select_from(model).where(*conditions)
    if start:
        statement = statement.where(time_column >= datetime.combine(start, datetime.min.time(), SHANGHAI),
                                    time_column < datetime.combine(end, datetime.min.time(), SHANGHAI))
    return statement.scalar_subquery()


def order_cash_days(db, order_id):
    """Only anonymous date/amount facts survive order purge, no identifiers."""
    days = {}
    def add(instant, amount, key):
        day = instant.astimezone(SHANGHAI).date().isoformat()
        days.setdefault(day, {'receivedCents': 0, 'refundCents': 0})[key] += int(amount)
    for model, key in ((Payment, 'receivedCents'), (Refund, 'refundCents')):
        for row in db.scalars(select(model).where(model.order_id == order_id)):
            add(row.created_at, row.amount_cents, key)
    charges = select(SupplementaryCharge.id).where(SupplementaryCharge.order_id == order_id)
    for row in db.scalars(select(SupplementaryMovement).where(SupplementaryMovement.charge_id.in_(charges))):
        add(row.created_at, row.amount_cents, 'receivedCents' if row.kind == 'RECEIPT' else 'refundCents')
    return days


@router.get('/admin/finance/summary', response_model=Envelope[FinanceSummary])
def summary(month: str | None = None, db=Depends(get_db), actor=Depends(actor_dep)):
    require_admin(actor, 'finance.manage')
    bounds = month_bounds(month)
    received = cash_sum(Payment, Payment.amount_cents, Payment.created_at, bounds)
    received += cash_sum(SupplementaryMovement, SupplementaryMovement.amount_cents,
                         SupplementaryMovement.created_at, bounds, SupplementaryMovement.kind == 'RECEIPT')
    refunded = cash_sum(Refund, Refund.amount_cents, Refund.created_at, bounds)
    refunded += cash_sum(SupplementaryMovement, SupplementaryMovement.amount_cents,
                         SupplementaryMovement.created_at, bounds, SupplementaryMovement.kind == 'REFUND')
    totals = AnonymousLedger.totals
    is_order = totals['orderCount'].as_integer().is_not(None)
    legacy_count = select(func.coalesce(func.sum(totals['orderCount'].as_integer()), 0)).where(
        is_order, totals['cashFlowByDay'].as_string().is_(None)).scalar_subquery() if month else 0
    if month is None:
        archive_received = select(func.coalesce(func.sum(
            func.coalesce(totals['cashReceivedCents'].as_integer(), 0) +
            func.coalesce(totals['supplementaryCashReceivedCents'].as_integer(), 0)), 0)).where(is_order).scalar_subquery()
        archive_refunded = select(func.coalesce(func.sum(
            func.coalesce(totals['refundCents'].as_integer(), 0) +
            func.coalesce(totals['supplementaryRefundCents'].as_integer(), 0)), 0)).where(is_order).scalar_subquery()
    else:
        days = func.jsonb_each(totals['cashFlowByDay']).table_valued(
            column('key', String), column('value', JSONB)).lateral('cash_days')
        def archive_sum(key):
            amount = cast(days.c.value[key].astext, BigInteger)
            return select(func.coalesce(func.sum(amount), 0)).select_from(
                AnonymousLedger.__table__.join(days, true())).where(
                    is_order, days.c.key.like(month + '-%')).scalar_subquery()
        archive_received = archive_sum('receivedCents')
        archive_refunded = archive_sum('refundCents')
    start, end = bounds
    expense_filters = [OperatingExpense.status == 'ACTIVE']
    if start:
        expense_filters.extend((OperatingExpense.spent_on >= start, OperatingExpense.spent_on < end))
    def expense_sum(key, amount=OperatingExpense.amount_cents):
        return select(func.coalesce(func.sum(amount), 0)).where(
            *expense_filters, OperatingExpense.category == key).scalar_subquery()
    # Every live/archive/spending/payout aggregate shares ONE PostgreSQL
    # statement snapshot. A simultaneous purge or void cannot double-count cash.
    result = db.execute(select(
        (received + archive_received).label('received'),
        (refunded + archive_refunded).label('refunded'),
        expense_sum('PAPER').label('paper'), expense_sum('INK').label('ink'),
        expense_sum('OTHER').label('other'),
        expense_sum('PAPER', OperatingExpense.paper_sheets).label('sheets'),
        cash_sum(WageSettlement, WageSettlement.amount_cents, WageSettlement.paid_at, bounds).label('wages'),
        (legacy_count if month else cast(0, BigInteger)).label('legacy'),
    )).one()
    received, refunded, paper, ink, other, sheets, wages, legacy_count = map(int, result)
    net = received - refunded
    supplies_balance = net - paper - ink
    unit = str((Decimal(paper) / Decimal(100) / Decimal(sheets)).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)) if sheets else None
    return ok(FinanceSummary(month=month, confirmed_received_cents=received, refund_cents=refunded,
        net_received_cents=net, paper_purchase_cents=paper, ink_purchase_cents=ink, other_expense_cents=other,
        consumables_balance_cents=supplies_balance, paid_wages_cents=wages, balance_cents=supplies_balance-wages-other,
        paper_sheets=sheets, paper_unit_yuan=unit, legacy_archive_count=legacy_count))


@router.get('/admin/finance/expenses', response_model=Envelope[ExpenseList])
def expenses(month: str | None = None, category: Category | None = None,
             page: int = Query(default=1, ge=1, le=1000000),
             page_size: int = Query(default=20, ge=1, le=50, alias='pageSize'),
             db=Depends(get_db), actor=Depends(actor_dep)):
    require_admin(actor, 'finance.manage')
    start, end = month_bounds(month)
    filters = []
    if start:
        filters.extend((OperatingExpense.spent_on >= start, OperatingExpense.spent_on < end))
    if category:
        filters.append(OperatingExpense.category == category)
    total = db.scalar(select(func.count()).select_from(OperatingExpense).where(*filters))
    rows = db.scalars(select(OperatingExpense).where(*filters).order_by(OperatingExpense.spent_on.desc(),
        OperatingExpense.created_at.desc(), OperatingExpense.id.desc()).offset((page-1)*page_size).limit(page_size))
    return ok(ExpenseList(items=[expense_view(row) for row in rows], total=total, page=page, page_size=page_size))


@router.post('/admin/finance/expenses', response_model=Envelope[ExpenseView])
def create_expense(body: ExpenseInput, db=Depends(get_db), actor=Depends(actor_dep)):
    require_admin(actor, 'finance.manage')
    row = OperatingExpense(category=body.category, spent_on=body.spent_on, amount_cents=body.amount_cents,
        paper_sheets=body.paper_sheets, note=body.note, recorded_by=actor.id)
    db.add(row); db.flush()
    audit(db, actor, 'EXPENSE_RECORDED', 'OPERATING_EXPENSE', row.id,
          data={'category': row.category, 'amountCents': row.amount_cents, 'spentOn': row.spent_on.isoformat()})
    return ok(expense_view(row))


@router.post('/admin/finance/expenses/{expense_id}/void', response_model=Envelope[ExpenseView])
def void_expense(expense_id: str, body: VoidInput, db=Depends(get_db), actor=Depends(actor_dep)):
    require_admin(actor, 'finance.manage')
    row = lock(db, OperatingExpense, expense_id, body.expected_version)
    if row.status != 'ACTIVE':
        raise DomainError('EXPENSE_ALREADY_VOIDED', '记录已作废，请刷新后核对', 409)
    row.status = 'VOIDED'; row.void_reason = body.reason
    row.voided_by = actor.id; row.voided_at = utcnow(); touch(row)
    audit(db, actor, 'EXPENSE_VOIDED', 'OPERATING_EXPENSE', row.id, body.reason,
          {'amountCents': row.amount_cents, 'category': row.category})
    db.flush()
    return ok(expense_view(row))
