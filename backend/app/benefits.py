"""N-06 staff whole-order percentage benefits.

The module owns the ``benefits`` config section and the immutable employee
discount snapshot embedded in quotes.  Eligibility is derived exclusively from
the preparation's trusted checkout account: staff claims on the request actor
are never consulted for pricing.

Discount rates are stored as basis points of *reduction*: a grade value of
2000 means the employee pays 80% of the order.  Superadmins always pay zero
while their linked staff identity is active and enabled.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .benefits_schemas import BenefitsPolicyDTO, BenefitsPolicyInput
from .core import Actor, DomainError, Envelope, actor_dep, audit, get_db, lock, ok, require_admin, touch
from .models import Account, ConfigSection, Order, Quote, Staff, new_id, utcnow

router = APIRouter()

SECTION = 'benefits'
DEFAULT_POLICY_ID = 'benefits-policy'
DEFAULT_DESCRIPTION = '员工打印折扣'
SUPERADMIN_REDUCTION_BPS = 10000


def _default_policy() -> dict:
    return {'id': DEFAULT_POLICY_ID, 'description': DEFAULT_DESCRIPTION,
            'grades': [None] * 6}


def _grade_value(item: Any) -> int | None:
    if isinstance(item, dict):
        item = item.get('reductionBps', item.get('reduction_bps'))
    if isinstance(item, bool) or item is None:
        return None
    try:
        value = int(item)
    except (TypeError, ValueError):
        return None
    if 0 <= value <= 10000:
        return value
    return None


def _grades(data: dict) -> list[int | None]:
    raw = (data or {}).get('grades')
    if isinstance(raw, dict):
        raw = [raw.get(str(level), raw.get(level)) for level in range(1, 7)]
    if not isinstance(raw, (list, tuple)):
        raw = []
    return [_grade_value(raw[index]) if index < len(raw) else None
            for index in range(6)]


def _policy_row(db) -> ConfigSection:
    row = db.scalar(select(ConfigSection).where(ConfigSection.section == SECTION))
    if row is not None:
        return row
    db.execute(
        pg_insert(ConfigSection)
        .values(id=new_id(), section=SECTION, version=1, data=_default_policy())
        .on_conflict_do_nothing(index_elements=[ConfigSection.section])
    )
    db.flush()
    row = db.scalar(select(ConfigSection).where(ConfigSection.section == SECTION))
    if row is None:  # pragma: no cover - only a broken database can get here
        raise DomainError('BENEFITS_POLICY_UNAVAILABLE', '员工折扣配置不可用', 500)
    return row


def _policy_payload(section: ConfigSection) -> dict:
    data = dict(section.data or {})
    return {
        'id': str(data.get('id') or DEFAULT_POLICY_ID),
        'version': section.version,
        'description': str(data.get('description') or DEFAULT_DESCRIPTION),
        'grades': _grades(data),
        'updatedAt': section.updated_at,
    }


def get_benefits_policy(db) -> dict:
    return _policy_payload(_policy_row(db))


def update_benefits_policy(db, payload: BenefitsPolicyInput,
                           actor: Actor | None = None) -> dict:
    section = _policy_row(db)
    lock(db, ConfigSection, section.id, payload.expected_version)
    current = dict(section.data or {})
    data = {
        **current,
        'id': str(current.get('id') or DEFAULT_POLICY_ID),
        'description': str(current.get('description') or DEFAULT_DESCRIPTION),
        'grades': [_grade_value(item) for item in payload.grades],
    }
    section.data = data
    touch(section)
    if actor is not None:
        audit(db, actor, 'BENEFITS_POLICY_UPDATED', 'CONFIG', SECTION,
              reason=payload.reason,
              data={'version': section.version, 'grades': data['grades']})
    db.flush()
    return _policy_payload(section)


def _require_superadmin(db, actor: Actor) -> Staff:
    require_admin(actor)
    staff = db.get(Staff, actor.staff_id) if actor.staff_id else None
    if (staff is None or not staff.enabled or staff.status != 'ACTIVE'
            or not staff.is_super_admin):
        raise DomainError('FORBIDDEN', '仅超级管理员可管理员工折扣', 403)
    return staff


@router.get('/admin/config/benefits', response_model=Envelope[BenefitsPolicyDTO])
def read_benefits_policy(db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    _require_superadmin(db, actor)
    return ok(get_benefits_policy(db))


@router.patch('/admin/config/benefits', response_model=Envelope[BenefitsPolicyDTO])
def patch_benefits_policy(payload: BenefitsPolicyInput, db=Depends(get_db),
                          actor: Actor = Depends(actor_dep)):
    _require_superadmin(db, actor)
    return ok(update_benefits_policy(db, payload, actor))


def _rate_text(bps: int) -> str:
    text = f'{bps / 1000:.3f}'.rstrip('0').rstrip('.')
    return text or '0'


def current_staff_candidate(db, checkout_account_id: str | None,
                            subtotal_cents: int,
                            now: datetime | None = None) -> dict | None:
    """Return the current employee candidate for a trusted checkout account.

    Returns ``None`` for guests, unlinked staff, disabled/revoked identities,
    unverified or deleted customer accounts, unconfigured grades and zero
    discounts.  This function never reads the request actor.
    """
    if not checkout_account_id or subtotal_cents <= 0:
        return None
    account = db.get(Account, checkout_account_id)
    if (account is None or account.status != 'ACTIVE'
            or account.deleted_at is not None or account.verified_at is None):
        return None
    staff = db.scalar(select(Staff).where(Staff.account_id == checkout_account_id))
    if staff is None or not staff.enabled or staff.status != 'ACTIVE':
        return None
    if staff.is_super_admin:
        # Superadmin pricing is unconditional: it must not depend on the
        # benefits grade row (or even on that config section existing).
        section = db.scalar(
            select(ConfigSection).where(ConfigSection.section == SECTION))
        data = dict(section.data or {}) if section is not None else {}
        policy_version = section.version if section is not None else 1
        source = 'SUPER_ADMIN'
        level = None
        reduction = SUPERADMIN_REDUCTION_BPS
    else:
        section = _policy_row(db)
        data = dict(section.data or {})
        policy_version = section.version
        source = 'STAFF_GRADE'
        level = staff.discount_level
        if level is None or not 1 <= level <= 6:
            return None
        reduction = _grades(data)[level - 1]
        if reduction is None or reduction <= 0:
            return None
    pay_rate = 10000 - int(reduction)
    payable = (subtotal_cents * pay_rate + 5000) // 10000
    discount = subtotal_cents - payable
    if discount <= 0:
        return None
    computed = now or utcnow()
    return {
        'source': source,
        'staffId': staff.id,
        'discountLevel': level,
        'reductionBps': int(reduction),
        'payRateBps': pay_rate,
        'discountCents': discount,
        'policyId': str(data.get('id') or DEFAULT_POLICY_ID),
        'policyVersion': policy_version,
        'computedAt': computed.isoformat(),
    }


def _quote_snapshot(quote: Quote | dict) -> dict:
    if isinstance(quote, dict):
        data = quote.get('data') if isinstance(quote.get('data'), dict) else quote
    else:
        data = getattr(quote, 'data', None)
    if not isinstance(data, dict):
        return {}
    nested = data.get('quote')
    return nested if isinstance(nested, dict) else data


def validate_quote_entitlement(db, preparation, quote: Quote | dict) -> None:
    """Expire an unsubmitted quote whose employee entitlement changed.

    Commerce calls this before accepting an order.  A submitted original quote
    is locked to its historical snapshot even if the employee is later revoked.
    """
    if preparation is None or quote is None:
        return None
    view = _quote_snapshot(quote)
    candidate = view.get('staffDiscountCandidate')
    quote_id = getattr(quote, 'id', None) or view.get('id')
    if quote_id and db.scalar(select(Order.id).where(Order.quote_id == quote_id)) is not None:
        return None
    subtotal = int(view.get('subtotalCents') or getattr(quote, 'subtotal_cents', 0) or 0)
    current = current_staff_candidate(db, preparation.checkout_account_id, subtotal)
    if current is None:
        if isinstance(candidate, dict):
            raise DomainError('QUOTE_EXPIRED', '员工优惠已变化，请重新报价确认', 409)
        return None
    if not isinstance(candidate, dict):
        # The quote was built before this account became an eligible employee
        # (or before the staff grant was activated).  Requote so the employee
        # benefit is applied instead of silently submitting the guest price.
        raise DomainError('QUOTE_EXPIRED', '员工优惠已变化，请重新报价确认', 409)
    keys = ['source', 'staffId', 'discountLevel', 'reductionBps', 'payRateBps',
            'discountCents']
    if candidate.get('source') != 'SUPER_ADMIN':
        keys += ['policyId', 'policyVersion']
    if any(candidate.get(key) != current.get(key) for key in keys):
        raise DomainError('QUOTE_EXPIRED', '员工优惠已变化，请重新报价确认', 409)
    return None
