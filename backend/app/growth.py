"""Pricing/promotion ledger, referrals, customer administration and campaign routes.

The quote is the single authority for discounts: it is recomputed on the server
from trusted line items, never from client supplied discount fields, and it is
stored as an immutable V1.3 snapshot with integer-cent allocations.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote as url_quote
from uuid import uuid4

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from .account_lifecycle import verification_deadline
from .config import get_config
from .core import (
    Actor, DomainError, Envelope, actor_dep, advisory_lock, audit, lock, ok,
    require_admin, require_customer, touch,
)
from .db import get_db
from .growth_schemas import (
    CampaignDTO, CampaignInput, CampaignList, CampaignUpdateInput, CouponDTO,
    CouponList, CustomerList, CustomerRecord,
    PromotionIntent, PromotionRecords, QuoteDTO, ReasonInput,
    VolumePolicyDTO, VolumePolicyInput,
)
from .settings import get_settings
from .benefits import current_staff_candidate
from .models import (
    Account, BusinessException, Campaign, ConfigSection, Coupon,
    DeliveryAssignment, DeliveryBatch, DeliverySlot, Location, Order,
    Preparation, PromotionEvent, Quote, Referral, Refund, Staff, utcnow,
)

router = APIRouter()

CASH_KINDS = ('CASH_MANUAL',)
SELECTION_MODES = ('AUTO', 'NO_COUPON', 'SPECIFIC_COUPON')
CATALOG_SERVICES = ('A4', 'PHOTO', 'ALL')


def _as_utc(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value) -> str | None:
    parsed = _as_utc(value)
    return parsed.astimezone(timezone.utc).isoformat() if parsed else None


def _rate_text(bps: int) -> str:
    value = bps / 1000
    text = f'{value:.3f}'.rstrip('0').rstrip('.')
    return text or '0'


def _campaign_data(campaign: Campaign) -> dict:
    return dict(campaign.data or {})


def _campaign_window(campaign: Campaign, now: datetime) -> bool:
    data = _campaign_data(campaign)
    starts = _as_utc(data.get('startsAt'))
    ends = _as_utc(data.get('endsAt'))
    return (starts is None or starts <= now) and (ends is None or now < ends)


def _campaign_values(campaign: Campaign) -> dict:
    data = _campaign_data(campaign)
    return {
        'name': campaign.name,
        'kind': campaign.kind,
        'enabled': bool(campaign.enabled),
        'amountCents': int(data.get('amountCents') or 0),
        'minSpendCents': int(data.get('minSpendCents') or 0),
        'service': data.get('service') if data.get('service') in CATALOG_SERVICES else 'ALL',
        'validityDays': data.get('validityDays'),
        'budgetCents': campaign.budget_cents,
        'issuanceLimit': data.get('issuanceLimit'),
        'perAccountLimit': int(data.get('perAccountLimit') or 1),
        'startsAt': data.get('startsAt'),
        'endsAt': data.get('endsAt'),
        'description': data.get('description') or '',
        'qualificationExpiresAt': data.get('qualificationExpiresAt'),
        'qualificationMinSpendCents': int(data.get('qualificationMinSpendCents') or 0),
        'qualificationService': data.get('qualificationService')
        if data.get('qualificationService') in CATALOG_SERVICES else 'ALL',
    }


def _campaign_snapshot(campaign: Campaign) -> dict:
    return {'id': campaign.id, 'version': campaign.version, **_campaign_values(campaign)}


def _campaign_dto(campaign: Campaign) -> CampaignDTO:
    values = _campaign_values(campaign)
    return CampaignDTO(
        id=campaign.id, version=campaign.version, name=values['name'],
        kind=values['kind'], enabled=values['enabled'],
        amountCents=values['amountCents'], minSpendCents=values['minSpendCents'],
        service=values['service'],
        validityDays=values['validityDays'], budgetCents=values['budgetCents'],
        issuanceLimit=values['issuanceLimit'],
        perAccountLimit=values['perAccountLimit'],
        startsAt=_as_utc(values['startsAt']), endsAt=_as_utc(values['endsAt']),
        description=values['description'], stackable=False,
        qualificationExpiresAt=_as_utc(values['qualificationExpiresAt']),
        qualificationMinSpendCents=values['qualificationMinSpendCents'],
        qualificationService=values['qualificationService'],
    )


def _coupon_terms(coupon: Coupon) -> dict:
    data = dict(coupon.data or {})
    terms = data.get('terms') if isinstance(data.get('terms'), dict) else {}
    return {
        'amountCents': int(terms.get('amountCents') or coupon.amount_cents),
        'minSpendCents': int(terms.get('minSpendCents') or 0),
        'service': terms.get('service') if terms.get('service') in CATALOG_SERVICES else 'ALL',
        'campaignId': terms.get('campaignId') or coupon.campaign_id,
        'name': terms.get('name') or data.get('name') or '优惠券',
        'campaignVersion': terms.get('campaignVersion'),
    }


def _coupon_view(coupon: Coupon) -> CouponDTO:
    terms = _coupon_terms(coupon)
    data = dict(coupon.data or {})
    status = coupon.status
    if status == 'AVAILABLE' and coupon.valid_until and coupon.valid_until <= utcnow():
        status = 'EXPIRED'
    return CouponDTO(
        id=coupon.id, couponGrantId=coupon.id,
        campaignId=str(terms['campaignId'] or data.get('originalCampaignId') or 'REFUND_COMPENSATION'),
        name=terms['name'], amountCents=coupon.amount_cents,
        minSpendCents=terms['minSpendCents'], service=terms['service'],
        status=status, riskHold=coupon.risk_hold,
        validUntil=coupon.valid_until,
        reservedOrderId=coupon.reserved_order_id,
        reason=data.get('reason'), version=coupon.version,
    )


def _event(db, business_key: str, kind: str, *, account_id=None, campaign_id=None,
           coupon_id=None, order_id=None, amount_cents=0, data=None) -> PromotionEvent:
    existing = db.scalar(select(PromotionEvent).where(
        PromotionEvent.business_key == business_key))
    if existing:
        return existing
    event = PromotionEvent(
        business_key=business_key, kind=kind, account_id=account_id,
        campaign_id=campaign_id, coupon_id=coupon_id, order_id=order_id,
        amount_cents=amount_cents, data=data or {},
    )
    db.add(event)
    db.flush()
    return event


def _price_lines(lines) -> tuple[list, list[dict]]:
    if not isinstance(lines, (list, tuple)) or not lines:
        raise DomainError('QUOTE_LINES_INVALID', '报价明细不能为空')
    allowed = {'key', 'relatedId', 'unit', 'quantity', 'unitPriceCents',
               'amountCents', 'service'}
    checked = []
    keys = set()
    for raw in lines:
        if isinstance(raw, dict):
            payload = {key: raw[key] for key in allowed if key in raw}
        else:
            payload = raw.model_dump(by_alias=True)
        from .growth_schemas import PriceLineDTO

        try:
            item = PriceLineDTO.model_validate(payload)
        except Exception as error:
            raise DomainError('QUOTE_LINES_INVALID', '报价明细格式无效',
                              field_errors={'lines': [str(error)[:300]]}) from error
        if item.key in keys:
            raise DomainError('QUOTE_LINES_INVALID', '报价明细行标识重复')
        keys.add(item.key)
        if item.service not in ('A4', 'PHOTO', 'BINDING'):
            raise DomainError('QUOTE_LINES_INVALID', '报价行服务类型无效')
        checked.append(item)
    return checked, [item.model_dump(by_alias=True) for item in checked]


def _resolve_intent(preparation: Preparation, intent) -> PromotionIntent:
    if intent is None:
        stored = (preparation.data or {}).get('promotionIntent')
        if isinstance(stored, dict):
            intent = stored
        elif isinstance(stored, PromotionIntent):
            intent = stored
        else:
            return PromotionIntent(selectionMode='AUTO')
    if isinstance(intent, PromotionIntent):
        return intent
    if isinstance(intent, dict):
        return PromotionIntent.model_validate(intent)
    raise DomainError('PROMOTION_INTENT_INVALID', '优惠选择参数无效')


def _coupon_candidates(db, account, mode: str, coupon_grant_id: str | None,
                       same_order_id: str | None, lines, now: datetime):
    if mode == 'NO_COUPON':
        return []
    if not account or account.status != 'ACTIVE' or not account.verified_at:
        return []
    rows = db.scalars(select(Coupon).where(
        Coupon.account_id == account.id,
        Coupon.status.in_(('AVAILABLE', 'RESERVED')),
    )).all()
    candidates = []
    for coupon in rows:
        if coupon.risk_hold != 'NONE':
            continue
        terms = _coupon_terms(coupon)
        if coupon.status == 'RESERVED':
            if not same_order_id or coupon.reserved_order_id != same_order_id:
                continue
        else:
            if coupon.valid_until and coupon.valid_until <= now:
                continue
            if coupon.campaign_id:
                campaign = db.get(Campaign, coupon.campaign_id)
                if not campaign or not campaign.enabled or not _campaign_window(campaign, now):
                    continue
        if mode == 'SPECIFIC_COUPON' and coupon.id != coupon_grant_id:
            continue
        eligible = [line for line in lines
                    if terms['service'] == 'ALL' or line.service == terms['service']]
        eligible_subtotal = sum(line.amount_cents for line in eligible)
        discount = 0
        if eligible_subtotal > 0 and eligible_subtotal >= terms['minSpendCents']:
            discount = min(terms['amountCents'], eligible_subtotal)
        candidates.append({
            'coupon': coupon, 'terms': terms, 'eligible': eligible,
            'eligibleSubtotal': eligible_subtotal, 'discount': discount,
        })
    candidates.sort(key=lambda item: (
        -item['discount'],
        item['coupon'].valid_until or datetime.max.replace(tzinfo=timezone.utc),
        item['coupon'].id,
    ))
    return candidates


def _volume_candidate(db, a4_sheets: int, a4_subtotal: int, now: datetime):
    section = get_config(db, 'volume-discounts')
    data = dict(section.data or {})
    enabled = bool(data.get('enabled'))
    effective = _as_utc(data.get('effectiveAt'))
    policy_id = str(data.get('id') or 'volume-main')
    try:
        version = int(data.get('version') or section.version)
    except (TypeError, ValueError):
        version = section.version
    if not enabled or (effective and effective > now):
        return None, {'id': policy_id, 'version': version, 'enabled': enabled,
                      'effectiveAt': data.get('effectiveAt'),
                      'description': data.get('description') or '',
                      'tiers': data.get('tiers') or []}
    tiers = data.get('tiers') or []
    hits = []
    for tier in tiers:
        try:
            min_sheets = int(tier['minSheets'])
            pay_rate = int(tier['payRateBps'])
            tier_id = str(tier['id'])
        except (KeyError, TypeError, ValueError):
            continue
        if min_sheets < 1 or not 0 <= pay_rate <= 10000 or a4_sheets < min_sheets:
            continue
        payable = (a4_subtotal * pay_rate + 5000) // 10000
        discount = a4_subtotal - payable
        if discount <= 0:
            continue
        hits.append({
            'policyId': policy_id, 'policyVersion': version, 'tierId': tier_id,
            'minSheets': min_sheets, 'a4Sheets': a4_sheets,
            'a4SubtotalCents': a4_subtotal, 'payRateBps': pay_rate,
            'discountCents': discount,
        })
    hits.sort(key=lambda item: (-item['discountCents'], -item['minSheets'],
                                item['tierId']))
    policy = {'id': policy_id, 'version': version, 'enabled': enabled,
              'effectiveAt': data.get('effectiveAt'),
              'description': data.get('description') or '',
              'tiers': tiers}
    return (hits[0] if hits else None), policy


def _allocations(discount: int, eligible, eligible_subtotal: int, service=None):
    if discount <= 0:
        return []
    if eligible_subtotal <= 0:
        raise DomainError('QUOTE_DISCOUNT_INVALID', '优惠分摊缺少适用金额')
    ordered = sorted(eligible, key=lambda line: line.key)
    result = []
    allocated = 0
    for line in ordered:
        amount = (discount * line.amount_cents) // eligible_subtotal
        amount = min(amount, line.amount_cents)
        result.append({'lineKey': line.key, 'relatedId': line.related_id,
                       'amountCents': amount})
        allocated += amount
    remaining = discount - allocated
    while remaining > 0:
        progressed = False
        for index, line in enumerate(ordered):
            if remaining == 0:
                break
            if result[index]['amountCents'] < line.amount_cents:
                result[index]['amountCents'] += 1
                remaining -= 1
                progressed = True
        if not progressed:
            raise DomainError('QUOTE_DISCOUNT_INVALID', '优惠分摊超过适用行金额')
    return result


def expire_due_coupons(db, limit: int = 500) -> int:
    """Release liability for AVAILABLE coupons whose validity has elapsed.

    Called by quote construction and read paths; maintenance may call it too.
    RESERVED coupons keep their liability because their locked order survives
    natural expiry.
    """
    now = utcnow()
    rows = db.scalars(select(Coupon).where(
        Coupon.status == 'AVAILABLE',
        Coupon.valid_until <= now,
    ).order_by(Coupon.valid_until).limit(limit).with_for_update(skip_locked=True)).all()
    for coupon in rows:
        _expire_coupon(db, coupon, '已过有效期')
    return len(rows)


def build_quote(db, preparation: Preparation, lines, actor: Actor | None = None,
                intent=None) -> Quote:
    if preparation is None:
        raise DomainError('PREPARATION_NOT_FOUND', '准备任务不存在', 404)
    now = utcnow()
    if preparation.expires_at and preparation.expires_at <= now:
        raise DomainError('QUOTE_EXPIRED', '报价已过期，请重新准备', 410)
    expire_due_coupons(db)
    checked, line_payload = _price_lines(lines)
    subtotal = sum(line.amount_cents for line in checked)
    a4_lines = [line for line in checked if line.service == 'A4']
    a4_sheets = sum(line.quantity for line in a4_lines)
    a4_subtotal = sum(line.amount_cents for line in a4_lines)
    parsed_intent = _resolve_intent(preparation, intent)
    mode = parsed_intent.selection_mode or 'AUTO'
    if mode not in SELECTION_MODES:
        raise DomainError('PROMOTION_INTENT_INVALID', '优惠选择模式无效')
    account = db.get(Account, preparation.checkout_account_id) \
        if preparation.checkout_account_id else None
    same_order = None
    if preparation.replaces_order_id:
        same_order = preparation.replaces_order_id
    else:
        same_order = db.scalar(select(Order.id).where(
            Order.preparation_id == preparation.id, Order.deleted_at.is_(None)))
    candidates = _coupon_candidates(db, account, mode, parsed_intent.coupon_grant_id,
                                    same_order, checked, now)
    best = candidates[0] if candidates else None
    volume, policy = _volume_candidate(db, a4_sheets, a4_subtotal, now)
    volume_discount = int(volume['discountCents']) if volume else 0
    coupon_discount = int(best['discount']) if best else 0
    staff_candidate = current_staff_candidate(
        db, preparation.checkout_account_id, subtotal, now)
    staff_discount = int(staff_candidate['discountCents']) if staff_candidate else 0
    # Ranked, never stacked.  Amount decides first; the rank only breaks ties.
    # Volume keeps priority over coupons exactly as before, ordinary staff beat
    # an equal coupon, and a superadmin's free order beats every equal source.
    options = []
    if volume_discount > 0:
        options.append((volume_discount, 3, 'VOLUME_TIER'))
    if coupon_discount > 0:
        options.append((coupon_discount, 1, 'COUPON'))
    if staff_candidate is not None and staff_discount > 0:
        rank = 4 if staff_candidate['source'] == 'SUPER_ADMIN' else 2
        options.append((staff_discount, rank, 'STAFF_GRADE'))
    discount, _, selected_type = max(options, key=lambda item: (item[0], item[1])) \
        if options else (0, 0, 'NONE')
    use_volume = selected_type == 'VOLUME_TIER'
    use_coupon = selected_type == 'COUPON'
    use_staff = selected_type == 'STAFF_GRADE'
    eligible_lines = (a4_lines if use_volume
                      else best['eligible'] if use_coupon
                      else checked if use_staff else [])
    eligible_subtotal = sum(line.amount_cents for line in eligible_lines)
    discounts = []
    if use_volume:
        discounts = [{
            'kind': 'VOLUME_TIER', 'policyId': volume['policyId'],
            'policyVersion': volume['policyVersion'], 'tierId': volume['tierId'],
            'a4Sheets': volume['a4Sheets'], 'payRateBps': volume['payRateBps'],
            'amountCents': discount,
            'description': f"满 {volume['minSheets']} 张享 {_rate_text(volume['payRateBps'])} 折",
        }]
    elif use_coupon:
        discounts = [{
            'kind': 'COUPON', 'couponGrantId': best['coupon'].id,
            'campaignId': str(best['terms']['campaignId'] or 'REFUND_COMPENSATION'),
            'amountCents': discount, 'description': best['terms']['name'],
        }]
    elif use_staff:
        if staff_candidate['source'] == 'SUPER_ADMIN':
            description = '超级管理员员工免单'
        else:
            description = (f"员工 {staff_candidate['discountLevel']} 级享 "
                           f"{_rate_text(staff_candidate['payRateBps'])} 折")
        discounts = [{
            'kind': 'STAFF_GRADE', 'source': staff_candidate['source'],
            'staffId': staff_candidate['staffId'],
            'discountLevel': staff_candidate['discountLevel'],
            'reductionBps': staff_candidate['reductionBps'],
            'payRateBps': staff_candidate['payRateBps'],
            'policyId': staff_candidate['policyId'],
            'policyVersion': staff_candidate['policyVersion'],
            'amountCents': discount, 'description': description,
        }]
    allocations = _allocations(discount, eligible_lines, eligible_subtotal)
    quote_id = str(uuid4())
    workflow = get_config(db, 'workflow')
    try:
        quote_minutes = max(1, min(int((workflow.data or {}).get('quoteMinutes') or 15), 1440))
    except (TypeError, ValueError):
        quote_minutes = 15
    expires_at = now + timedelta(minutes=quote_minutes)
    price_version = get_config(db, 'prices').version
    view = {
        'id': quote_id,
        'priceVersion': price_version,
        'expiresAt': expires_at.isoformat(),
        'lines': line_payload,
        'subtotalCents': subtotal,
        'discountCents': discount,
        'totalCents': subtotal - discount,
        'deliveryFeeCents': 0,
        'checkoutAccountId': account.id if account else None,
        'selectedEligibleSubtotalCents': eligible_subtotal,
        'promotionRuleVersion': f"volume-{policy['version']}",
        'selectionMode': mode,
        'selectedPromotionType': selected_type,
        'volumeDiscountCandidate': volume,
        'discounts': discounts,
        'discountAllocations': allocations,
    }
    if staff_candidate is not None:
        view['staffDiscountCandidate'] = staff_candidate
        view['promotionRuleVersion'] = (
            f"volume-{policy['version']};benefits-{staff_candidate['policyVersion']}")
    QuoteDTO.model_validate(view)
    quote = Quote(
        id=quote_id,
        preparation_id=preparation.id,
        checkout_account_id=account.id if account else None,
        price_version=price_version,
        expires_at=expires_at,
        subtotal_cents=subtotal,
        discount_cents=discount,
        total_cents=subtotal - discount,
        selected_coupon_id=best['coupon'].id if use_coupon else None,
        # Quote.data is exactly a strict Quote snapshot. Commerce's order
        # adapter copies this dict into OrderView.quote, so extra metadata
        # (intent, candidate ids, actor) must never leak into it.
        data={**view},
    )
    db.add(quote)
    db.flush()
    return quote


def quote_view(quote: Quote) -> dict:
    data = dict(quote.data or {})
    view = data.get('quote') if isinstance(data.get('quote'), dict) else data
    if not isinstance(view, dict):
        raise DomainError('QUOTE_SNAPSHOT_INVALID', '报价快照损坏', 500)
    validated = QuoteDTO.model_validate(view)
    dumped = validated.model_dump(by_alias=True, mode='json')
    if validated.staff_discount_candidate is None:
        # Quotes without an employee remain byte-compatible with the V1.3
        # snapshot: the new optional field is omitted rather than null.
        dumped.pop('staffDiscountCandidate', None)
    return dumped


def _quote_selected(quote: Quote) -> dict:
    data = quote.data if quote and quote.data else None
    if not isinstance(data, dict):
        return {}
    view = data.get('quote') if isinstance(data.get('quote'), dict) else data
    return view if isinstance(view, dict) else {}


def _release_liability(db, coupon: Coupon, reason: str, event_kind: str = 'RELEASED'):
    if not coupon.campaign_id:
        return
    campaign = db.get(Campaign, coupon.campaign_id)
    if not campaign:
        return
    campaign = lock(db, Campaign, campaign.id)
    data = dict(coupon.data or {})
    if not data.get('liabilityReserved'):
        return
    face = coupon.amount_cents
    campaign.reserved_liability_cents = max(0, campaign.reserved_liability_cents - face)
    data['liabilityReserved'] = False
    coupon.data = data
    touch(campaign)


def _validated_quote_coupon_amount(coupon: Coupon, quote: Quote,
                                   order: Order) -> int:
    """Revalidate the new quote against the locked coupon terms.

    A same-order RESERVED coupon keeps its original face/terms and natural
    expiry protection, but a pending-order revision must still prove that the
    new quote still applies to the coupon's service and threshold and that the
    discount equals the server-recomputed eligible amount.
    """
    if not order.checkout_account_id or coupon.account_id != order.checkout_account_id:
        raise DomainError('COUPON_UNAVAILABLE', '优惠券不属于当前结算账号', 409)
    if quote.checkout_account_id != order.checkout_account_id:
        raise DomainError('COUPON_UNAVAILABLE', '新报价的结算账号与原订单不一致', 409)
    try:
        view = QuoteDTO.model_validate(_quote_selected(quote))
    except DomainError:
        raise
    except Exception as error:
        raise DomainError('QUOTE_SNAPSHOT_INVALID', '新报价快照无效，请重新确认',
                          409) from error
    if view.selection_mode == 'NO_COUPON':
        raise DomainError('COUPON_REQUIRES_REQUOTE', '新报价已排除该优惠券，请重新确认', 409)
    discounts = view.discounts
    if (view.selected_promotion_type != 'COUPON' or len(discounts) != 1
            or discounts[0].kind != 'COUPON'
            or discounts[0].coupon_grant_id != coupon.id):
        raise DomainError('COUPON_REQUIRES_REQUOTE', '新报价未包含该已预约优惠券，请重新确认', 409)
    terms = _coupon_terms(coupon)
    a4_lines = [line for line in view.lines
                if terms['service'] == 'ALL' or line.service == terms['service']]
    eligible = sum(line.amount_cents for line in a4_lines)
    if eligible <= 0 or eligible < terms['minSpendCents']:
        raise DomainError('COUPON_REQUIRES_REQUOTE',
                          '新报价不再满足优惠券适用服务或使用门槛，请重新确认', 409)
    expected = min(coupon.amount_cents, terms['amountCents'], eligible)
    if view.discount_cents != expected or view.selected_eligible_subtotal_cents != eligible:
        raise DomainError('COUPON_REQUIRES_REQUOTE', '新报价的券抵扣金额与适用金额不一致', 409)
    if not 0 < view.discount_cents <= coupon.amount_cents:
        raise DomainError('COUPON_UNAVAILABLE', '券面额与报价不符', 409)
    return view.discount_cents


def reserve_coupon(db, order: Order, quote: Quote) -> Coupon | None:
    selected = _quote_selected(quote)
    if selected.get('selectedPromotionType') != 'COUPON':
        return None
    if order.quote_id != quote.id:
        # A pending order revision replaces its quote inside one transaction;
        # commerce reserves before assigning order.quote_id. Only allow that
        # path when the new quote comes from this order's revision preparation.
        revision = db.get(Preparation, quote.preparation_id)
        if (order.status != 'PENDING_CONFIRMATION'
                or revision is None
                or revision.replaces_order_id != order.id
                or quote.checkout_account_id != order.checkout_account_id):
            raise DomainError('QUOTE_EXPIRED', '订单报价引用已失效，请重新确认', 409)
    discount = selected.get('discounts') or []
    coupon_id = discount[0].get('couponGrantId') if discount else None
    if not coupon_id:
        raise DomainError('COUPON_UNAVAILABLE', '报价未包含有效优惠券', 409)
    coupon = lock(db, Coupon, coupon_id)
    if coupon.risk_hold != 'NONE':
        raise DomainError('COUPON_RISK_HOLD', '优惠券处于风控冻结状态，请人工核对', 409)
    same_order_reserved = (coupon.status == 'RESERVED'
                           and coupon.reserved_order_id == order.id)
    if coupon.status == 'RESERVED' and not same_order_reserved:
        raise DomainError('COUPON_ALREADY_RESERVED', '该优惠券已被其他订单预约', 409)
    if not same_order_reserved and coupon.status != 'AVAILABLE':
        raise DomainError('COUPON_UNAVAILABLE', '优惠券当前不可用', 409)
    if not order.checkout_account_id or coupon.account_id != order.checkout_account_id:
        raise DomainError('COUPON_UNAVAILABLE', '优惠券不属于当前结算账号', 409)
    amount = _validated_quote_coupon_amount(coupon, quote, order)
    now = utcnow()
    if same_order_reserved:
        # Keep the original face/rules and do not reject solely because the
        # coupon naturally expired or its campaign stopped after reservation.
        data = dict(coupon.data or {})
        reservation = dict(data.get('reservation') or {})
        if (reservation.get('quoteId') != quote.id
                or reservation.get('amountCents') != amount):
            history = list(data.get('reservationHistory') or [])
            history.append({'quoteId': quote.id, 'amountCents': amount,
                            'at': now.isoformat()})
            data['reservationHistory'] = history[-10:]
            data['reservation'] = {
                'orderId': order.id, 'quoteId': quote.id, 'amountCents': amount,
                'terms': _coupon_terms(coupon), 'reservedAt': now.isoformat(),
            }
            coupon.data = data
            touch(coupon)
            db.flush()
        return coupon
    if coupon.valid_until <= now:
        _expire_coupon(db, coupon, '预约时已过期')
        raise DomainError('COUPON_UNAVAILABLE', '优惠券已过期', 410)
    if coupon.campaign_id:
        campaign = lock(db, Campaign, coupon.campaign_id)
        if not campaign.enabled or not _campaign_window(campaign, now):
            raise DomainError('PROMOTION_NOT_ELIGIBLE', '活动当前不可用', 422)
        data = dict(coupon.data or {})
        if not data.get('liabilityReserved'):
            if campaign.budget_cents is not None and (
                    campaign.reserved_liability_cents + campaign.realized_discount_cents
                    + coupon.amount_cents > campaign.budget_cents):
                raise DomainError('PROMOTION_BUDGET_EXCEEDED', '活动预算不足', 422)
            campaign.reserved_liability_cents += coupon.amount_cents
            data['liabilityReserved'] = True
            coupon.data = data
    data = dict(coupon.data or {})
    data['reservation'] = {
        'orderId': order.id, 'quoteId': quote.id, 'amountCents': amount,
        'terms': _coupon_terms(coupon), 'reservedAt': now.isoformat(),
    }
    coupon.status = 'RESERVED'
    coupon.reserved_order_id = order.id
    coupon.data = data
    touch(coupon)
    _event(db, f'coupon-reserve:{coupon.id}:{order.id}', 'RESERVED',
           account_id=coupon.account_id, campaign_id=coupon.campaign_id,
           coupon_id=coupon.id, order_id=order.id, amount_cents=amount,
           data={'quoteId': quote.id})
    db.flush()
    return coupon


def settle_coupon(db, order: Order, quote: Quote | None = None) -> Coupon | None:
    if quote is None:
        quote = db.get(Quote, order.quote_id)
    selected = _quote_selected(quote)
    if selected.get('selectedPromotionType') != 'COUPON':
        return None
    discount = selected.get('discounts') or []
    coupon_id = discount[0].get('couponGrantId') if discount else None
    coupon = lock(db, Coupon, coupon_id)
    if coupon.status == 'USED':
        if coupon.used_order_id == order.id:
            return coupon
        raise DomainError('COUPON_UNAVAILABLE', '优惠券已用于其他订单', 409)
    if coupon.status != 'RESERVED' or coupon.reserved_order_id != order.id:
        raise DomainError('COUPON_UNAVAILABLE', '优惠券预约与当前订单不一致', 409)
    if coupon.risk_hold != 'NONE':
        raise DomainError('COUPON_RISK_HOLD', '优惠券已被风控冻结，请人工核对', 409)
    actual = int(selected.get('discountCents') or 0)
    if not 0 < actual <= coupon.amount_cents:
        raise DomainError('COUPON_UNAVAILABLE', '核销金额超出券面额', 409)
    if coupon.campaign_id:
        campaign = lock(db, Campaign, coupon.campaign_id)
        data = dict(coupon.data or {})
        if data.get('liabilityReserved'):
            campaign.reserved_liability_cents = max(
                0, campaign.reserved_liability_cents - coupon.amount_cents)
            campaign.realized_discount_cents += actual
        data['liabilityReserved'] = False
        coupon.data = data
        touch(campaign)
    now = utcnow()
    data = dict(coupon.data or {})
    data['settlement'] = {
        'orderId': order.id, 'quoteId': quote.id, 'amountCents': actual,
        'faceCents': coupon.amount_cents, 'settledAt': now.isoformat(),
    }
    coupon.status = 'USED'
    coupon.used_order_id = order.id
    coupon.reserved_order_id = None
    coupon.data = data
    touch(coupon)
    _event(db, f'coupon-settle:{coupon.id}:{order.id}', 'USED',
           account_id=coupon.account_id, campaign_id=coupon.campaign_id,
           coupon_id=coupon.id, order_id=order.id, amount_cents=actual)
    db.flush()
    return coupon


def release_coupon(db, order: Order, quote: Quote | None = None) -> Coupon | None:
    if quote is None:
        quote = db.get(Quote, order.quote_id)
    selected = _quote_selected(quote)
    if selected.get('selectedPromotionType') != 'COUPON':
        return None
    discount = selected.get('discounts') or []
    coupon_id = discount[0].get('couponGrantId') if discount else None
    if not coupon_id:
        return None
    coupon = lock(db, Coupon, coupon_id)
    if coupon.status != 'RESERVED' or coupon.reserved_order_id != order.id:
        return coupon
    data = dict(coupon.data or {})
    count = int(data.get('releaseCount') or 0) + 1
    now = utcnow()
    if coupon.valid_until and coupon.valid_until <= now:
        if coupon.campaign_id:
            campaign = lock(db, Campaign, coupon.campaign_id)
            if data.get('liabilityReserved'):
                campaign.reserved_liability_cents = max(0, campaign.reserved_liability_cents - coupon.amount_cents)
                data['liabilityReserved'] = False
                touch(campaign)
        coupon.status = 'EXPIRED'
        outcome = 'EXPIRED'
    else:
        coupon.status = 'AVAILABLE'
        outcome = 'AVAILABLE'
    coupon.reserved_order_id = None
    data['releaseCount'] = count
    history = list(data.get('releaseHistory') or [])
    history.append({'orderId': order.id, 'outcome': outcome, 'at': now.isoformat()})
    data['releaseHistory'] = history[-10:]
    data['reservation'] = None
    coupon.data = data
    touch(coupon)
    _event(db, f'coupon-release:{coupon.id}:{order.id}:{count}', 'RELEASED',
           account_id=coupon.account_id, campaign_id=coupon.campaign_id,
           coupon_id=coupon.id, order_id=order.id,
           amount_cents=coupon.amount_cents, data={'outcome': outcome})
    db.flush()
    return coupon


def _expire_coupon(db, coupon: Coupon, reason: str):
    data = dict(coupon.data or {})
    coupon.status = 'EXPIRED'
    data['reason'] = reason
    coupon.data = data
    _release_liability(db, coupon, reason, 'EXPIRED')
    touch(coupon)
    _event(db, f'coupon-expired:{coupon.id}', 'EXPIRED',
           account_id=coupon.account_id, campaign_id=coupon.campaign_id,
           coupon_id=coupon.id, amount_cents=coupon.amount_cents)


def _issue_coupon(db, account: Account, campaign: Campaign, source_key: str,
                  *, promised: bool = False, requirements: dict | None = None):
    advisory_lock(db, 'coupon-source:' + source_key)
    existing = db.scalar(select(Coupon).where(Coupon.source_key == source_key))
    if existing:
        return existing, 'ALREADY_GRANTED'
    now = utcnow()
    values = _campaign_values(campaign)
    amount = int((requirements or {}).get('amountCents') or values['amountCents'])
    if amount <= 0:
        return None, 'INVALID_RULES'
    if not promised and (not values['enabled'] or not _campaign_window(campaign, now)):
        return None, 'DISABLED'
    if account.status != 'ACTIVE' or not account.verified_at:
        return None, 'ACCOUNT_NOT_ELIGIBLE'
    campaign = lock(db, Campaign, campaign.id)
    values = _campaign_values(campaign)
    amount = int((requirements or {}).get('amountCents') or values['amountCents'])
    if not promised and (not values['enabled'] or not _campaign_window(campaign, now)):
        return None, 'DISABLED'
    per_account = int(values['perAccountLimit'] or 0)
    issued_for_account = db.scalar(select(func.count()).select_from(Coupon).where(
        Coupon.campaign_id == campaign.id, Coupon.account_id == account.id)) or 0
    if per_account and issued_for_account >= per_account:
        return None, 'PER_ACCOUNT_LIMIT'
    issuance_limit = values.get('issuanceLimit')
    issued = db.scalar(select(func.count()).select_from(Coupon).where(
        Coupon.campaign_id == campaign.id)) or 0
    if issuance_limit is not None and issued >= int(issuance_limit):
        return None, 'ISSUANCE_LIMIT'
    if campaign.budget_cents is not None and (
            campaign.reserved_liability_cents + campaign.realized_discount_cents
            + amount > campaign.budget_cents):
        return None, 'BUDGET_EXHAUSTED'
    validity_days = (requirements or {}).get('validityDays', values.get('validityDays'))
    try:
        days = int(validity_days) if validity_days is not None else 36500
    except (TypeError, ValueError):
        days = 36500
    terms = {
        'amountCents': amount,
        'minSpendCents': int((requirements or {}).get('minSpendCents', values['minSpendCents'])),
        'service': (requirements or {}).get('service') or values['service'],
        'campaignId': campaign.id,
        'name': (requirements or {}).get('name') or values['name'],
        'campaignVersion': campaign.version,
        'kind': campaign.kind,
        'qualificationExpiresAt': values['qualificationExpiresAt'],
        'qualificationMinSpendCents': values['qualificationMinSpendCents'],
        'qualificationService': values['qualificationService'],
    }
    coupon = Coupon(
        account_id=account.id, campaign_id=campaign.id, amount_cents=amount,
        status='AVAILABLE', risk_hold='NONE', source_key=source_key,
        valid_until=now + timedelta(days=days),
        data={'terms': terms, 'name': values['name'],
              'liabilityReserved': True,
              'issuedAt': now.isoformat(), 'issuedBy': campaign.kind},
    )
    db.add(coupon)
    campaign.reserved_liability_cents += amount
    touch(campaign)
    db.flush()
    _event(db, f'coupon-issued:{source_key}', 'ISSUED',
           account_id=account.id, campaign_id=campaign.id, coupon_id=coupon.id,
           amount_cents=amount, data={'sourceKey': source_key})
    return coupon, 'GRANTED'


def on_registration_verified(db, account: Account) -> list[Coupon]:
    if account is None or account.status != 'ACTIVE' or not account.verified_at:
        return []
    now = utcnow()
    relation = db.scalar(select(Referral).where(Referral.invitee_id == account.id))
    if relation:
        data = dict(relation.data or {})
        if 'rewardCampaigns' not in data:
            promised = db.scalars(select(Campaign).where(
                Campaign.kind == 'REFERRER', Campaign.enabled.is_(True))).all()
            data['rewardCampaigns'] = [_campaign_snapshot(item) for item in promised
                                       if _campaign_window(item, now)]
            if data['rewardCampaigns']:
                first = data['rewardCampaigns'][0]
                data.setdefault('qualificationExpiresAt', first.get('qualificationExpiresAt'))
                data.setdefault('qualificationMinSpendCents',
                                first.get('qualificationMinSpendCents', 0))
                data.setdefault('qualificationService',
                                first.get('qualificationService', 'ALL'))
        data.setdefault('registeredAt', now.isoformat())
        if relation.status not in ('QUALIFIED', 'GRANTED', 'PENDING_BUDGET',
                                   'DISQUALIFIED', 'REVERSAL_REVIEW'):
            relation.status = 'WAITING_FIRST_ORDER'
        relation.data = data
        touch(relation)
    granted = []
    campaigns = db.scalars(select(Campaign).where(
        Campaign.kind == 'NEW_CUSTOMER')).all()
    for campaign in sorted(campaigns, key=lambda item: item.id):
        coupon, reason = _issue_coupon(
            db, account, campaign,
            f'REGISTRATION_BONUS:{campaign.id}:{account.id}')
        if coupon and reason == 'GRANTED':
            granted.append(coupon)
    if relation:
        invitee_campaigns = db.scalars(select(Campaign).where(
            Campaign.kind == 'INVITEE')).all()
        for campaign in sorted(invitee_campaigns, key=lambda item: item.id):
            coupon, reason = _issue_coupon(
                db, account, campaign,
                f'INVITEE_BONUS:{campaign.id}:{account.id}')
            if coupon and reason == 'GRANTED':
                granted.append(coupon)
    db.flush()
    return granted


def _order_quote_lines(db, order: Order) -> list[dict]:
    data = dict(order.data or {})
    view = data.get('quote') if isinstance(data.get('quote'), dict) else {}
    lines = view.get('lines')
    if isinstance(lines, list) and lines:
        return lines
    quote = db.get(Quote, order.quote_id)
    if quote:
        return list((quote.data or {}).get('lines') or [])
    return []


def _eligible_amount(lines: list[dict], service: str) -> int:
    return sum(int(line.get('amountCents') or 0) for line in lines
               if service == 'ALL' or line.get('service') == service)


def qualify_referral(db, order: Order, *, db_reward: bool = True) -> bool:
    if order is None or order.status != 'DELIVERED':
        return False
    if not order.checkout_account_id:
        return False
    if order.settlement_kind not in CASH_KINDS or (order.cash_received_cents or 0) <= 0:
        return False
    account = db.get(Account, order.checkout_account_id)
    if not account or account.status != 'ACTIVE' or not account.verified_at:
        return False
    relation = db.scalar(select(Referral).where(
        Referral.invitee_id == account.id).with_for_update())
    if relation is None:
        return False
    now = utcnow()
    data = dict(relation.data or {})
    if relation.qualified_order_id:
        if relation.qualified_order_id != order.id:
            return False
        if relation.status in ('DISQUALIFIED', 'REVERSAL_REVIEW'):
            return False
    else:
        deadline = _as_utc(data.get('qualificationExpiresAt'))
        if deadline and now > deadline:
            return False
        minimum = int(data.get('qualificationMinSpendCents') or 0)
        service = data.get('qualificationService') or 'ALL'
        lines = _order_quote_lines(db, order)
        eligible = _eligible_amount(lines, service)
        if eligible <= 0 or eligible < minimum:
            return False
        relation.qualified_order_id = order.id
        relation.qualified_at = now
        relation.status = 'QUALIFIED'
        data['qualificationSequence'] = int(data.get('qualificationSequence') or 0) + 1
        data['qualifiedNetPaidCents'] = int(order.cash_received_cents or 0)
        data['qualifiedAt'] = now.isoformat()
        relation.data = data
        touch(relation)
        _event(db, f'referral-qualified:{relation.id}', 'REFERRAL_QUALIFIED',
               account_id=relation.inviter_id, order_id=order.id,
               amount_cents=int(order.cash_received_cents or 0))
    snapshots = data.get('rewardCampaigns')
    if snapshots is None:
        campaigns = db.scalars(select(Campaign).where(
            Campaign.kind == 'REFERRER', Campaign.enabled.is_(True))).all()
        snapshots = [_campaign_snapshot(item) for item in campaigns
                     if _campaign_window(item, now)]
    inviter = db.get(Account, relation.inviter_id)
    if snapshots:
        lines = _order_quote_lines(db, order)
        qualified_snapshots = []
        for snapshot in snapshots:
            deadline = _as_utc(snapshot.get('qualificationExpiresAt'))
            if deadline and now > deadline:
                continue
            service = snapshot.get('qualificationService') or 'ALL'
            amount = _eligible_amount(lines, service)
            if amount <= 0 or amount < int(snapshot.get('qualificationMinSpendCents') or 0):
                continue
            qualified_snapshots.append(snapshot)
        snapshots = qualified_snapshots
    if not snapshots:
        relation.status = 'DISQUALIFIED'
        data['rewardReason'] = 'NO_PROMISED_REWARD'
        relation.data = data
        touch(relation)
        db.flush()
        return True
    if inviter is None or inviter.status != 'ACTIVE' or not inviter.verified_at:
        relation.status = 'PENDING_BUDGET'
        data['rewardReason'] = 'INVITER_NOT_ELIGIBLE'
        relation.data = data
        touch(relation)
        db.flush()
        return True
    granted = 0
    pending = 0
    for snapshot in snapshots:
        campaign = db.get(Campaign, snapshot.get('id'))
        if campaign is None:
            continue
        coupon, reason = _issue_coupon(
            db, inviter, campaign, f'REFERRAL_REWARD:{relation.id}:{campaign.id}',
            promised=True, requirements=snapshot)
        if coupon:
            granted += 1
        elif reason in ('BUDGET_EXHAUSTED', 'ISSUANCE_LIMIT'):
            pending += 1
        _event(db, f'referral-reward:{relation.id}:{campaign.id}', 'REFERRAL_REWARD',
               account_id=relation.inviter_id, campaign_id=campaign.id,
               coupon_id=coupon.id if coupon else None, order_id=order.id,
               amount_cents=coupon.amount_cents if coupon else 0,
               data={'outcome': reason})
    if granted == len(snapshots):
        relation.status = 'GRANTED'
    elif pending:
        relation.status = 'PENDING_BUDGET'
        data['rewardReason'] = 'PENDING_BUDGET'
    else:
        relation.status = 'DISQUALIFIED'
        data['rewardReason'] = 'REWARD_NOT_GRANTED'
    relation.data = data
    touch(relation)
    if relation.status in ('GRANTED', 'PENDING_BUDGET'):
        _notify_referral_reward(db, inviter, relation)
    db.flush()
    return True


def _notify_referral_reward(db, inviter: Account, relation: Referral):
    prefs = dict(inviter.preferences or {})
    if not prefs.get('referralReward'):
        return
    try:
        from .mail import enqueue_email
        enqueue_email(
            db, 'REFERRAL_REWARD_GRANTED', inviter.email,
            {'brandName': 'DormPrint',
             'rewardText': '邀请奖励已发放，请登录「我的优惠券」查看使用条件与有效期。'},
            f'referral-reward-email:{relation.id}',
        )
    except Exception:
        # Mail failure must never change the already-committed ledger fact.
        return


def _referral_review_exception(db, target_order_id, relation, coupon, net, reason):
    if not target_order_id:
        return
    existing = db.scalar(select(BusinessException).where(
        BusinessException.order_id == target_order_id,
        BusinessException.type == 'REFERRAL_REWARD_REVERSAL_REVIEW',
        BusinessException.status.in_(('OPEN', 'IN_PROGRESS'))))
    if existing is None:
        db.add(BusinessException(
            order_id=target_order_id, type='REFERRAL_REWARD_REVERSAL_REVIEW',
            status='OPEN', reason=reason, blocks_production=True,
            blocks_delivery=True,
            data={'referralId': relation.id, 'couponId': coupon.id,
                  'netPaidCents': net}))


def _remaining_qualified_service_cents(db, order: Order, service: str,
                                       refunded_total: int) -> int:
    """Remaining net value of the originally qualified service after refunds.

    Refund records carry the affected quote lines/quantities. If an order is
    fully refunded, every line is consumed. Otherwise only explicitly affected
    quantities are removed; a partial custom refund without item attribution
    is left unattributed rather than silently keeping a fully-refunded service.
    """
    quote = db.get(Quote, order.quote_id)
    view = _quote_selected(quote) if quote else {}
    if not view:
        view = (order.data or {}).get('quote') or {}
    lines = list(view.get('lines') or [])
    allocations = {item.get('lineKey'): int(item.get('amountCents') or 0)
                   for item in (view.get('discountAllocations') or [])}
    cash = int(order.cash_received_cents or 0)
    settled = order.settlement_kind in ('CASH_MANUAL', 'ZERO_PAYABLE')
    if settled and refunded_total >= cash and (cash > 0 or refunded_total == 0):
        return 0
    remaining = {line.get('key'): int(line.get('quantity') or 0) for line in lines}
    for refund in db.scalars(select(Refund).where(Refund.order_id == order.id)).all():
        affected = (refund.data or {}).get('affectedItems') or []
        for item in affected:
            key = item.get('lineKey')
            try:
                quantity = int(item.get('quantity') or 0)
            except (TypeError, ValueError):
                continue
            if key in remaining and quantity > 0:
                remaining[key] = max(0, remaining[key] - quantity)
    total = 0
    for line in lines:
        if service != 'ALL' and line.get('service') != service:
            continue
        key = line.get('key')
        quantity = int(line.get('quantity') or 0)
        if quantity <= 0:
            continue
        net = max(0, int(line.get('amountCents') or 0) - allocations.get(key, 0))
        unit, remainder = divmod(net, quantity)
        count = remaining.get(key, 0)
        total += unit * count + min(count, remainder)
    return total


def _referral_recheck(db, order: Order, refunded_total: int) -> None:
    relation = db.scalar(select(Referral).where(
        Referral.qualified_order_id == order.id).with_for_update())
    if relation is None:
        return
    data = dict(relation.data or {})
    net = max(0, int(order.cash_received_cents or 0) - int(refunded_total))
    minimum = int(data.get('qualificationMinSpendCents') or 0)
    service = data.get('qualificationService') or 'ALL'
    remaining_service = _remaining_qualified_service_cents(
        db, order, service, refunded_total)
    if net > 0 and net >= minimum and remaining_service > 0 and remaining_service >= minimum:
        data['refundReview'] = {
            'netPaidCents': net, 'refundedCents': refunded_total,
            'qualificationService': service,
            'remainingEligibleCents': remaining_service,
            'outcomes': ['RETAINED'], 'at': utcnow().isoformat(),
        }
        relation.data = data
        touch(relation)
        db.flush()
        return
    reasons = []
    review = False
    coupons = db.scalars(select(Coupon).where(
        Coupon.source_key.like(f'REFERRAL_REWARD:{relation.id}:%'))).all()
    for coupon in coupons:
        cdata = dict(coupon.data or {})
        if coupon.status in ('AVAILABLE', 'EXPIRED'):
            coupon.status = 'REVOKED'
            cdata['reason'] = '退款后净实付不满足邀请奖励条件'
            coupon.data = cdata
            _release_liability(db, coupon, '退款复核撤销', 'REWARD_REVOKED')
            touch(coupon)
            _event(db, f'referral-reward-revoked:{coupon.id}', 'REWARD_REVOKED',
                   account_id=relation.inviter_id, campaign_id=coupon.campaign_id,
                   coupon_id=coupon.id, order_id=order.id,
                   amount_cents=coupon.amount_cents, data={'reason': 'NET_PAID'})
            reasons.append('REVOKED')
        elif coupon.status == 'RESERVED':
            coupon.risk_hold = 'FROZEN'
            touch(coupon)
            review = True
            reasons.append('RESERVED_REVIEW')
            _referral_review_exception(
                db, coupon.reserved_order_id, relation, coupon, net,
                '退款后邀请奖励仍被其他订单预约，需人工复核')
        elif coupon.status == 'USED':
            if coupon.used_order_id == order.id:
                reasons.append('USED_ON_REFUNDED_ORDER')
            else:
                coupon.risk_hold = 'FROZEN'
                touch(coupon)
                review = True
                reasons.append('USED_ELSEWHERE')
                _referral_review_exception(
                    db, coupon.used_order_id, relation, coupon, net,
                    '退款后邀请奖励已在另一笔订单使用，需人工复核')
    if review:
        relation.status = 'REVERSAL_REVIEW'
    else:
        relation.status = 'DISQUALIFIED'
    data['refundReview'] = {
        'netPaidCents': net, 'refundedCents': refunded_total,
        'qualificationService': service,
        'remainingEligibleCents': remaining_service,
        'outcomes': reasons, 'at': utcnow().isoformat(),
    }
    relation.data = data
    touch(relation)
    _event(db, f'referral-refund-recheck:{relation.id}:{order.id}:{refunded_total}',
           'REFERRAL_REFUND_RECHECK', account_id=relation.inviter_id,
           order_id=order.id, amount_cents=refunded_total,
           data={'status': relation.status, 'outcomes': reasons})


def on_refund(db, order: Order, refund: Refund) -> Coupon | None:
    """Full refund compensation and referral net-paid recheck.

    The original quote and coupon history are never rewritten. Compensation is
    tracked as its own account liability and does not consume a stopped
    campaign's issuance budget.
    """
    now = utcnow()
    summed = db.scalar(select(func.coalesce(func.sum(Refund.amount_cents), 0)).where(
        Refund.order_id == order.id)) or 0
    refunded_total = max(int(order.refunded_cents or 0), int(summed))
    cash = int(order.cash_received_cents or 0)
    settled = order.settlement_kind in ('CASH_MANUAL', 'ZERO_PAYABLE')
    # A zero-payable order has no cash to return, but a recorded full-order
    # cancellation still triggers the Q01 compensation for the used coupon.
    full = settled and (refunded_total >= cash if cash > 0 else True)
    # Fixed lock order: invitation first, then any reward campaign liability.
    _referral_recheck(db, order, refunded_total)
    compensation = None
    if full:
        quote = db.get(Quote, order.quote_id)
        selected = _quote_selected(quote)
        discounts = selected.get('discounts') or []
        if discounts and discounts[0].get('kind') == 'COUPON':
            actual = int(selected.get('discountCents') or 0)
            original = db.get(Coupon, discounts[0].get('couponGrantId'))
            if (actual > 0 and order.checkout_account_id and original
                    and original.status == 'USED'
                    and original.used_order_id == order.id):
                source_key = f'REFUND_COMPENSATION:{order.id}'
                advisory_lock(db, 'coupon-source:' + source_key)
                compensation = db.scalar(select(Coupon).where(
                    Coupon.source_key == source_key))
                if compensation is None:
                    terms = _coupon_terms(original) if original else {
                        'amountCents': actual, 'minSpendCents': 0,
                        'service': 'ALL', 'name': '退款补偿券',
                        'campaignId': None,
                    }
                    compensation = Coupon(
                        account_id=order.checkout_account_id,
                        campaign_id=None,
                        amount_cents=actual,
                        status='AVAILABLE', risk_hold='NONE',
                        source_key=source_key,
                        valid_until=now + timedelta(days=7),
                        data={
                            'terms': {
                                'amountCents': actual,
                                'minSpendCents': terms['minSpendCents'],
                                'service': terms['service'],
                                'campaignId': None,
                                'name': '退款补偿券',
                                'originalCampaignId': terms['campaignId'],
                            },
                            'name': '退款补偿券',
                            'compensation': True,
                            'independentLiability': True,
                            'liabilityReserved': False,
                            'originalCouponId': original.id if original else None,
                            'originalCampaignId': terms['campaignId'],
                            'originalOrderId': order.id,
                            'refundId': refund.id,
                            'issuedAt': now.isoformat(),
                        },
                    )
                    db.add(compensation)
                    db.flush()
                    _event(db, f'refund-compensation:{order.id}', 'REFUND_COMPENSATION',
                           account_id=order.checkout_account_id,
                           coupon_id=compensation.id, order_id=order.id,
                           amount_cents=actual,
                           data={'refundId': refund.id, 'independentLiability': True})
    db.flush()
    return compensation


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _mask_email(value: str) -> str:
    local, _, domain = (value or '').partition('@')
    return (local[:1] + '***@' + domain) if local else 'unknown'


def _account_status(account: Account) -> str:
    if account.status == 'PENDING_EMAIL' or (not account.verified_at and account.status != 'SUSPENDED'):
        return 'PENDING_VERIFICATION'
    if account.status == 'SUSPENDED':
        return 'SUSPENDED'
    return 'ACTIVE'


def _customer_view(db, account: Account) -> CustomerRecord:
    orders = db.scalars(select(Order).where(
        Order.checkout_account_id == account.id,
        Order.deleted_at.is_(None)).order_by(Order.created_at.desc())).all()
    coupons = db.scalar(select(func.count()).select_from(Coupon).where(
        Coupon.account_id == account.id)) or 0
    referrals = db.scalar(select(func.count()).select_from(Referral).where(
        Referral.inviter_id == account.id)) or 0
    status = _account_status(account)
    actions = []
    if status == 'ACTIVE':
        actions = ['suspend', 'send-recovery']
    elif status == 'SUSPENDED':
        actions = ['restore']
    else:
        actions = ['resend-verification', 'suspend']
    data = dict(account.preferences or {})
    notification = {
        'orderCreated': bool(data.get('orderCreated')),
        'orderStatusUpdated': bool(data.get('orderStatusUpdated')),
        'referralReward': bool(data.get('referralReward')),
    }
    return CustomerRecord(
        id=account.id, emailMasked=_mask_email(account.email),
        displayName=account.display_name or '未命名客户', verified=bool(account.verified_at),
        status=status, version=account.version,
        notificationPreferences=notification,
        verificationDeadline=verification_deadline(account),
        createdAt=account.created_at, orderCount=len(orders), couponCount=int(coupons),
        inviteCode=account.invite_code, referralCount=int(referrals),
        allowedActions=actions,
        orders=[{'id': order.id, 'pickupCode': order.pickup_code,
                 'totalCents': order.total_cents, 'status': order.status}
                for order in orders[:100]],
    )


def _promotion_summary(db) -> dict:
    now = utcnow()
    coupons = db.scalars(select(Coupon)).all()
    used_discount = db.scalar(select(func.coalesce(func.sum(PromotionEvent.amount_cents), 0))
                              .where(PromotionEvent.kind == 'USED')) or 0
    volume_discount = 0
    for order in db.scalars(select(Order).where(
            Order.settlement_kind.is_not(None))).all():
        view = (order.data or {}).get('quote')
        if not isinstance(view, dict):
            quote = db.get(Quote, order.quote_id)
            view = _quote_selected(quote) if quote else {}
        for discount in (view.get('discounts') or []):
            if discount.get('kind') == 'VOLUME_TIER':
                volume_discount += int(discount.get('amountCents') or 0)
    compensation_cents = db.scalar(
        select(func.coalesce(func.sum(PromotionEvent.amount_cents), 0)).where(
            PromotionEvent.kind == 'REFUND_COMPENSATION')) or 0
    accounts = db.scalars(select(Account)).all()
    campaigns = []
    for campaign in db.scalars(select(Campaign)).all():
        issued = db.scalar(select(func.count()).select_from(Coupon).where(
            Coupon.campaign_id == campaign.id)) or 0
        committed = campaign.reserved_liability_cents + campaign.realized_discount_cents
        campaigns.append({
            'campaignId': campaign.id, 'issued': int(issued),
            'budgetCents': campaign.budget_cents, 'committedCents': committed,
            'remainingBudgetCents': None if campaign.budget_cents is None
            else max(0, campaign.budget_cents - committed),
        })
    return {
        'issued': len(coupons),
        'available': sum(1 for c in coupons if c.status == 'AVAILABLE'
                         and (not c.valid_until or c.valid_until > now)),
        'reserved': sum(1 for c in coupons if c.status == 'RESERVED'),
        'used': sum(1 for c in coupons if c.status == 'USED'),
        'expired': sum(1 for c in coupons if c.status == 'EXPIRED'
                       or (c.status == 'AVAILABLE' and c.valid_until and c.valid_until <= now)),
        'revoked': sum(1 for c in coupons if c.status == 'REVOKED'),
        'frozen': sum(1 for c in coupons if c.risk_hold == 'FROZEN'),
        'reservedLiabilityCents': sum(
            c.amount_cents for c in coupons if c.status in ('AVAILABLE', 'RESERVED')),
        'couponDiscountCents': int(used_discount),
        'volumeDiscountCents': int(volume_discount),
        'realizedDiscountCents': int(used_discount) + int(volume_discount),
        'unresolvedUsedCount': sum(1 for c in coupons if c.status == 'USED'),
        'unresolvedUsedFaceCents': 0,
        'refundCompensationCents': int(compensation_cents),
        'newCustomers': len(accounts),
        'verifiedCustomers': sum(1 for a in accounts if a.verified_at),
        'campaigns': campaigns,
    }


@router.get('/customer/coupons', response_model=Envelope[CouponList])
def customer_coupons(db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_customer(actor, verified=True)
    expire_due_coupons(db)
    account = db.get(Account, actor.account_id)
    if not account or account.status != 'ACTIVE' or not account.verified_at:
        raise DomainError('ACCOUNT_VERIFICATION_REQUIRED', '请登录已验证账号', 403)
    rows = db.scalars(select(Coupon).where(
        Coupon.account_id == account.id).order_by(Coupon.created_at.desc())).all()
    return ok({'items': [_coupon_view(item) for item in rows]})


@router.get('/customer/referrals', response_model=Envelope[dict])
def customer_referrals(db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_customer(actor, verified=True)
    account = db.get(Account, actor.account_id)
    if not account or account.status != 'ACTIVE' or not account.verified_at:
        raise DomainError('ACCOUNT_VERIFICATION_REQUIRED', '请登录已验证账号', 403)
    rows = db.scalars(select(Referral).where(
        Referral.inviter_id == actor.account_id).order_by(
        Referral.created_at.desc())).all()
    items = []
    for relation in rows:
        invitee = db.get(Account, relation.invitee_id)
        data = dict(relation.data or {})
        items.append({
            'id': relation.id,
            'inviteeLabel': _mask_email(invitee.email) if invitee else '已注销客户',
            'status': relation.status,
            'createdAt': relation.created_at.isoformat(),
            'qualifiedAt': relation.qualified_at.isoformat() if relation.qualified_at else None,
            'rewardText': data.get('rewardText') or '',
        })
    qualified = sum(1 for item in items if item['status'] in
                    ('QUALIFIED', 'GRANTED', 'PENDING_BUDGET'))
    return ok({'items': items, 'summary': {
        'invited': len(items), 'qualified': qualified,
        'granted': sum(1 for item in items if item['status'] == 'GRANTED'),
    }})


@router.get('/customer/referrals/code', response_model=Envelope[dict])
def customer_referral_code(db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_customer(actor, verified=True)
    account = db.get(Account, actor.account_id)
    if not account or account.status != 'ACTIVE' or not account.verified_at:
        raise DomainError('ACCOUNT_VERIFICATION_REQUIRED', '请登录已验证账号', 403)
    return ok({
        'code': account.invite_code,
        'shareText': '邀请你使用 DormPrint 打印，注册时填写邀请码即可参与活动。',
    })


@router.get('/admin/config/volume-discounts', response_model=Envelope[VolumePolicyDTO])
def get_volume_policy(db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'promotion.manage')
    section = get_config(db, 'volume-discounts')
    data = dict(section.data or {})
    return ok({
        'id': str(data.get('id') or 'volume-main'), 'version': section.version,
        'enabled': bool(data.get('enabled')),
        'effectiveAt': data.get('effectiveAt'),
        'description': data.get('description') or '',
        'tiers': data.get('tiers') or [],
    })


def update_volume_policy(db, payload: VolumePolicyInput,
                         actor: Actor | None = None) -> dict:
    section = get_config(db, 'volume-discounts')
    lock(db, ConfigSection, section.id, payload.expected_version)
    data = {
        'id': payload.id or (section.data or {}).get('id') or 'volume-main',
        'enabled': payload.enabled,
        'effectiveAt': payload.effective_at.isoformat() if payload.effective_at else None,
        'description': payload.description,
        'tiers': [tier.model_dump(by_alias=True) for tier in
                  sorted(payload.tiers, key=lambda item: item.min_sheets)],
        'stackable': False,
    }
    section.data = data
    touch(section)
    if actor is not None:
        audit(db, actor, 'VOLUME_POLICY_UPDATED', 'CONFIG', 'volume-discounts',
              reason=payload.reason,
              data={'version': section.version, 'enabled': data['enabled'],
                    'tiers': len(data['tiers'])})
    db.flush()
    return {
        'id': data['id'], 'version': section.version, 'enabled': data['enabled'],
        'effectiveAt': data['effectiveAt'], 'description': data['description'],
        'tiers': data['tiers'],
    }


@router.patch('/admin/config/volume-discounts', response_model=Envelope[VolumePolicyDTO])
def patch_volume_policy(payload: VolumePolicyInput, db=Depends(get_db),
                        actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'promotion.manage')
    return ok(update_volume_policy(db, payload, actor))


@router.get('/admin/promotions/campaigns', response_model=Envelope[CampaignList])
def list_campaigns(db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'promotion.manage')
    expire_due_coupons(db)
    rows = db.scalars(select(Campaign).order_by(Campaign.created_at.desc())).all()
    summary = _promotion_summary(db)
    stats = {
        'issuedCount': summary['issued'], 'reservedCount': summary['reserved'],
        'usedCount': summary['used'], 'expiredCount': summary['expired'],
        'couponDiscountCents': summary['couponDiscountCents'],
        'newCustomers': summary['newCustomers'],
        'verifiedCustomers': summary['verifiedCustomers'],
        'volumeDiscountCents': summary['volumeDiscountCents'],
    }
    return ok({'items': [_campaign_dto(item) for item in rows], 'stats': stats})


@router.post('/admin/promotions/campaigns', response_model=Envelope[CampaignDTO])
def create_campaign(payload: CampaignInput, db=Depends(get_db),
                    actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'promotion.manage')
    campaign = Campaign(
        name=payload.name, kind=payload.kind, enabled=payload.enabled,
        budget_cents=payload.budget_cents, reserved_liability_cents=0,
        realized_discount_cents=0,
        data={
            'amountCents': payload.amount_cents,
            'minSpendCents': payload.min_spend_cents,
            'service': payload.service,
            'validityDays': payload.validity_days,
            'issuanceLimit': payload.issuance_limit,
            'perAccountLimit': payload.per_account_limit,
            'startsAt': payload.starts_at.isoformat() if payload.starts_at else None,
            'endsAt': payload.ends_at.isoformat() if payload.ends_at else None,
            'description': payload.description,
            'stackable': False,
            'qualificationExpiresAt': payload.qualification_expires_at.isoformat()
            if payload.qualification_expires_at else None,
            'qualificationMinSpendCents': payload.qualification_min_spend_cents,
            'qualificationService': payload.qualification_service,
            'createdBy': actor.id,
        },
    )
    db.add(campaign)
    db.flush()
    audit(db, actor, 'CAMPAIGN_CREATED', 'CAMPAIGN', campaign.id,
          reason=payload.reason, data={'kind': campaign.kind})
    return ok(_campaign_dto(campaign))


@router.patch('/admin/promotions/campaigns/{id}',
              response_model=Envelope[CampaignDTO])
def patch_campaign(id: str, payload: CampaignUpdateInput,
                   db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'promotion.manage')
    campaign = lock(db, Campaign, id, payload.expected_version)
    data = dict(campaign.data or {})
    committed = campaign.reserved_liability_cents + campaign.realized_discount_cents
    if payload.budget_cents is not None and payload.budget_cents < committed:
        raise DomainError('PROMOTION_BUDGET_EXCEEDED',
                          '已承诺的优惠负债超过新预算，不能抹掉既有义务', 422)
    campaign.name = payload.name
    campaign.kind = payload.kind
    campaign.enabled = payload.enabled
    campaign.budget_cents = payload.budget_cents
    data.update({
        'amountCents': payload.amount_cents,
        'minSpendCents': payload.min_spend_cents,
        'service': payload.service,
        'validityDays': payload.validity_days,
        'issuanceLimit': payload.issuance_limit,
        'perAccountLimit': payload.per_account_limit,
        'startsAt': payload.starts_at.isoformat() if payload.starts_at else None,
        'endsAt': payload.ends_at.isoformat() if payload.ends_at else None,
        'description': payload.description,
        'stackable': False,
        'qualificationExpiresAt': payload.qualification_expires_at.isoformat()
        if payload.qualification_expires_at else None,
        'qualificationMinSpendCents': payload.qualification_min_spend_cents,
        'qualificationService': payload.qualification_service,
    })
    campaign.data = data
    touch(campaign)
    audit(db, actor, 'CAMPAIGN_UPDATED', 'CAMPAIGN', campaign.id,
          reason=payload.reason, data={'version': campaign.version})
    return ok(_campaign_dto(campaign))


def _records(db, *, campaign_id=None, account_id=None, status=None, risk_hold=None,
             q='', type_=None, start=None, end=None):
    from .models import Coupon as CouponModel
    query = select(PromotionEvent).order_by(PromotionEvent.created_at.desc())
    if campaign_id:
        query = query.where(PromotionEvent.campaign_id == campaign_id)
    if account_id:
        query = query.where(PromotionEvent.account_id == account_id)
    if type_:
        query = query.where(PromotionEvent.kind == type_)
    if start:
        query = query.where(PromotionEvent.created_at >= _as_utc(start))
    if end:
        query = query.where(PromotionEvent.created_at <= _as_utc(end))
    rows = db.scalars(query.limit(1000)).all()
    items = []
    for event in rows:
        coupon = db.get(CouponModel, event.coupon_id) if event.coupon_id else None
        campaign = db.get(Campaign, event.campaign_id) if event.campaign_id else None
        account = db.get(Account, event.account_id) if event.account_id else None
        state = coupon.status if coupon else event.kind
        if status and state != status:
            continue
        if risk_hold and (coupon.risk_hold if coupon else 'NONE') != risk_hold:
            continue
        masked = _mask_email(account.email) if account else '—'
        name = campaign.name if campaign else ('退款补偿' if event.kind == 'REFUND_COMPENSATION' else '—')
        if q and q.lower() not in ' '.join(
                [masked, name, event.business_key, event.id]).lower():
            continue
        items.append({
            'id': event.id, 'accountId': event.account_id,
            'campaignId': event.campaign_id, 'accountMasked': masked,
            'campaignName': name,
            'couponGrantId': event.coupon_id,
            'type': event.kind, 'status': state,
            'riskHold': coupon.risk_hold if coupon else None,
            'amountCents': event.amount_cents,
            'reason': (event.data or {}).get('reason', ''),
            'time': event.created_at.isoformat(),
            'version': coupon.version if coupon else 0,
        })
    return items


@router.get('/admin/promotions/records', response_model=Envelope[PromotionRecords])
def promotion_records(
    campaignId: str | None = Query(default=None), accountId: str | None = Query(default=None),
    status: str | None = Query(default=None), riskHold: str | None = Query(default=None),
    q: str = Query(default=''), type: str | None = Query(default=None),
    from_date: str | None = Query(default=None, alias='from'),
    to_date: str | None = Query(default=None, alias='to'),
    db=Depends(get_db), actor: Actor = Depends(actor_dep),
):
    require_admin(actor, 'promotion.manage')
    end = _as_utc(to_date)
    if end is not None and len(to_date or '') == 10:
        end = end + timedelta(days=1)
    items = _records(db, campaign_id=campaignId, account_id=accountId,
                     status=status, risk_hold=riskHold, q=q, type_=type,
                     start=from_date, end=end)
    return ok({'items': items, 'summary': _promotion_summary(db)})


@router.post('/admin/promotions/coupons/{id}/freeze', response_model=Envelope[dict])
def freeze_coupon(id: str, payload: ReasonInput, db=Depends(get_db),
                  actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'promotion.manage')
    coupon = lock(db, Coupon, id, payload.expected_version)
    coupon.risk_hold = 'FROZEN'
    data = dict(coupon.data or {})
    data['reason'] = payload.reason
    coupon.data = data
    touch(coupon)
    _add_coupon_blocker(db, coupon, 'COUPON_FROZEN', '预约优惠已被风控冻结，请人工核对',
                        blocks_delivery=True)
    _event(db, f'coupon-frozen:{coupon.id}:{coupon.version}', 'FREEZE',
           account_id=coupon.account_id, campaign_id=coupon.campaign_id,
           coupon_id=coupon.id, amount_cents=coupon.amount_cents,
           data={'reason': payload.reason})
    audit(db, actor, 'COUPON_FROZEN', 'COUPON', coupon.id, reason=payload.reason)
    return ok(_coupon_view(coupon).model_dump(by_alias=True, mode='json'))


@router.post('/admin/promotions/coupons/{id}/unfreeze', response_model=Envelope[dict])
def unfreeze_coupon(id: str, payload: ReasonInput, db=Depends(get_db),
                    actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'promotion.manage')
    coupon = lock(db, Coupon, id, payload.expected_version)
    coupon.risk_hold = 'NONE'
    data = dict(coupon.data or {})
    data['reason'] = payload.reason
    coupon.data = data
    touch(coupon)
    _remove_coupon_blocker(db, coupon, 'COUPON_FROZEN')
    _event(db, f'coupon-unfrozen:{coupon.id}:{coupon.version}', 'UNFREEZE',
           account_id=coupon.account_id, campaign_id=coupon.campaign_id,
           coupon_id=coupon.id, amount_cents=coupon.amount_cents,
           data={'reason': payload.reason})
    audit(db, actor, 'COUPON_UNFROZEN', 'COUPON', coupon.id, reason=payload.reason)
    return ok(_coupon_view(coupon).model_dump(by_alias=True, mode='json'))


@router.post('/admin/promotions/coupons/{id}/revoke', response_model=Envelope[dict])
def revoke_coupon(id: str, payload: ReasonInput, db=Depends(get_db),
                  actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'promotion.manage')
    coupon = lock(db, Coupon, id, payload.expected_version)
    data = dict(coupon.data or {})
    data['reason'] = payload.reason
    if coupon.status == 'AVAILABLE':
        coupon.status = 'REVOKED'
        _release_liability(db, coupon, payload.reason)
    elif coupon.status == 'EXPIRED':
        coupon.status = 'REVOKED'
    else:
        coupon.risk_hold = 'FROZEN'
        _add_coupon_blocker(db, coupon, 'COUPON_REVOKED',
                            '预约或已核销优惠被撤销，请人工核对', blocks_delivery=True)
    coupon.data = data
    touch(coupon)
    _event(db, f'coupon-revoked:{coupon.id}:{coupon.version}', 'REVOKED',
           account_id=coupon.account_id, campaign_id=coupon.campaign_id,
           coupon_id=coupon.id, amount_cents=coupon.amount_cents,
           data={'reason': payload.reason})
    audit(db, actor, 'COUPON_REVOKED', 'COUPON', coupon.id, reason=payload.reason)
    return ok(_coupon_view(coupon).model_dump(by_alias=True, mode='json'))


def _add_coupon_blocker(db, coupon, code, message, blocks_delivery=False):
    """Record a relational blocker; commerce composes blockers from these rows.

    We intentionally do not lock/rewrite Order rows here: coupon operations
    take the coupon lock before any order-facing work, while order submission
    takes the order lock before the coupon lock. Keeping this row-level
    exception table as the only blocker store avoids that lock inversion.
    """
    if not coupon.reserved_order_id:
        return
    existing = db.scalar(select(BusinessException).where(
        BusinessException.order_id == coupon.reserved_order_id,
        BusinessException.type == code,
        BusinessException.status.in_(('OPEN', 'IN_PROGRESS'))))
    if existing is None:
        db.add(BusinessException(
            order_id=coupon.reserved_order_id, type=code, status='OPEN',
            reason=message, blocks_production=True,
            blocks_delivery=blocks_delivery, data={'couponId': coupon.id}))


def _remove_coupon_blocker(db, coupon, code):
    if not coupon.reserved_order_id:
        return
    for exception in db.scalars(select(BusinessException).where(
            BusinessException.order_id == coupon.reserved_order_id,
            BusinessException.type == code,
            BusinessException.status.in_(('OPEN', 'IN_PROGRESS')))).all():
        exception.status = 'RESOLVED'
        exception.reason = (exception.reason or '') + '；已由管理员解除'
        touch(exception)


@router.get('/admin/customers', response_model=Envelope[CustomerList])
def list_customers(
    q: str = Query(default=''), status: str | None = Query(default=None),
    verified: bool | None = Query(default=None), inviteCode: str | None = Query(default=None),
    createdFrom: str | None = Query(default=None), createdTo: str | None = Query(default=None),
    minOrders: int | None = Query(default=None, ge=0),
    minCoupons: int | None = Query(default=None, ge=0),
    db=Depends(get_db), actor: Actor = Depends(actor_dep),
):
    require_admin(actor, 'customer.manage')
    rows = db.scalars(select(Account).order_by(Account.created_at.desc())).all()
    items = []
    for account in rows:
        if status and _account_status(account) != status:
            continue
        if verified is not None and bool(account.verified_at) != verified:
            continue
        if inviteCode and account.invite_code != inviteCode:
            continue
        if q and q.lower() not in ' '.join(
                [account.id, account.display_name or '', account.email,
                 account.invite_code]).lower():
            continue
        start = _as_utc(createdFrom)
        end = _as_utc(createdTo)
        if start and account.created_at < start:
            continue
        if end and account.created_at > end + timedelta(days=1):
            continue
        view = _customer_view(db, account)
        if minOrders is not None and view.order_count < minOrders:
            continue
        if minCoupons is not None and view.coupon_count < minCoupons:
            continue
        items.append(view)
    return ok({'items': items})


@router.get('/admin/customers/{id}', response_model=Envelope[CustomerRecord])
def get_customer(id: str, db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor, 'customer.manage')
    account = db.get(Account, id)
    if not account:
        raise DomainError('NOT_FOUND', '客户不存在', 404)
    return ok(_customer_view(db, account))


@router.post('/admin/customers/{id}/suspend', response_model=Envelope[dict])
def suspend_customer(id: str, payload: ReasonInput, db=Depends(get_db),
                     actor: Actor = Depends(actor_dep)):
    return _customer_action(db, actor, id, payload, 'suspend')


@router.post('/admin/customers/{id}/restore', response_model=Envelope[dict])
def restore_customer(id: str, payload: ReasonInput, db=Depends(get_db),
                     actor: Actor = Depends(actor_dep)):
    return _customer_action(db, actor, id, payload, 'restore')


@router.post('/admin/customers/{id}/resend-verification',
             response_model=Envelope[dict])
def resend_customer_verification(id: str, payload: ReasonInput,
                                 db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    return _customer_action(db, actor, id, payload, 'resend-verification')


@router.post('/admin/customers/{id}/send-recovery', response_model=Envelope[dict])
def send_customer_recovery(id: str, payload: ReasonInput, db=Depends(get_db),
                           actor: Actor = Depends(actor_dep)):
    return _customer_action(db, actor, id, payload, 'send-recovery')


def _customer_action(db, actor, account_id, payload, action):
    require_admin(actor, 'customer.manage')
    # The unified auth staff guard uses the same advisory key.  Account and
    # staff rows are then locked together so a customer suspension cannot race
    # another suspension or a staff update and leave no active superadmin.
    advisory_lock(db, 'staff-full-admin-guard')
    account = lock(db, Account, account_id, payload.expected_version)
    linked_staff = db.scalar(select(Staff).where(
        Staff.account_id == account.id).with_for_update())
    if action == 'suspend':
        if linked_staff is not None:
            from .auth import last_super_admin_blocker
            if last_super_admin_blocker(db, staff_id=linked_staff.id):
                raise DomainError(
                    'LAST_SUPER_ADMIN_PROTECTED',
                    '不能冻结最后一个在职超级管理员的客户账号',
                    409, {'staffId': linked_staff.id},
                )
        account.status = 'SUSPENDED'
        account.session_version = (account.session_version or 1) + 1
        touch(account)
        audit(db, actor, 'CUSTOMER_SUSPENDED', 'ACCOUNT', account.id,
              reason=payload.reason)
        return ok({'id': account.id, 'status': _account_status(account),
                   'version': account.version})
    if action == 'restore':
        account.status = 'ACTIVE' if account.verified_at else 'PENDING_EMAIL'
        touch(account)
        audit(db, actor, 'CUSTOMER_RESTORED', 'ACCOUNT', account.id,
              reason=payload.reason)
        return ok({'id': account.id, 'status': _account_status(account),
                   'version': account.version})
    if action in ('resend-verification', 'send-recovery'):
        job = _admin_account_email(db, account, action, payload.reason)
        audit(db, actor, 'CUSTOMER_EMAIL_REQUESTED', 'ACCOUNT', account.id,
              reason=payload.reason, data={'action': action,
                                           'emailJobId': getattr(job, 'id', None)})
        return ok({'id': account.id, 'status': 'QUEUED',
                   'emailJobId': getattr(job, 'id', None)})
    raise DomainError('NOT_FOUND', '未知客户操作', 404)


def _admin_account_email(db, account: Account, action: str, reason: str):
    """Reuse auth-owned one-time-token/email helpers; never create a second flow."""
    if action == 'resend-verification':
        if account.verified_at or account.status == 'ACTIVE':
            raise DomainError('EMAIL_ALREADY_VERIFIED', '该账号邮箱已验证，无需重发验证邮件', 409)
    elif not account.verified_at or account.status != 'ACTIVE':
        raise DomainError('EMAIL_NOT_VERIFIED', '该账号尚未验证邮箱，不能发起密码找回', 409)
    if not mail_available_safe(db):
        raise DomainError('EMAIL_SERVICE_UNAVAILABLE', '邮件服务暂不可用，无法发送安全邮件', 503)
    try:
        from .auth import (_create_token, _customer_action, _enqueue_email,
                           _invalidate_tokens)
    except ImportError as error:  # pragma: no cover - shared auth is required in V1
        raise DomainError('SERVICE_UNAVAILABLE', '账号安全邮件能力尚未接入', 503) from error
    if action == 'resend-verification':
        _invalidate_tokens(db, 'REGISTRATION', account_id=account.id)
        token, raw = _create_token(
            db, 'REGISTRATION', account_id=account.id,
            payload={'email': account.email, 'inviteCode': None})
        return _enqueue_email(
            db, 'CUSTOMER_VERIFY_EMAIL', account.email,
            {'brandName': 'DormPrint',
             'actionUrl': _customer_action('/u/account/verify', raw),
             'expiresMinutes': 30},
            f'admin-customer-verify:{token.id}:{account.id}')
    _invalidate_tokens(db, 'CUSTOMER_RESET', account_id=account.id)
    token, raw = _create_token(db, 'CUSTOMER_RESET', account_id=account.id, payload={})
    return _enqueue_email(
        db, 'CUSTOMER_RESET_PASSWORD', account.email,
        {'brandName': 'DormPrint',
         'actionUrl': _customer_action('/u/account/reset', raw),
         'expiresMinutes': 30},
        f'admin-customer-reset:{token.id}:{account.id}')


def mail_available_safe(db) -> bool:
    try:
        from .mail import mail_available
        return mail_available(db)
    except Exception:
        return False


# Auth imports these two helpers from ``growth`` (CONTRACT module entry points);
# the implementation lives in mail.py so the SMTP module keeps a single owner.
def mail_available(db) -> bool:
    from .mail import mail_available as _mail_available

    return _mail_available(db)


def enqueue_email(db, key: str, to: str, variables: dict, business_key: str):
    from .mail import enqueue_email as _enqueue_email

    return _enqueue_email(db, key, to, variables, business_key)


def enqueue_notification_email(db, account: Account, key: str, variables: dict,
                               business_key: str):
    """Send an optional order/referral notification only with explicit consent."""
    mapping = {
        'ORDER_CREATED': 'orderCreated',
        'ORDER_STATUS_UPDATED': 'orderStatusUpdated',
        'REFERRAL_REWARD_GRANTED': 'referralReward',
    }
    preference = mapping.get(key)
    if preference is None:
        raise DomainError('MAIL_TEMPLATE_INVALID', '该模板不是可选通知模板')
    if not account or account.status != 'ACTIVE' or not account.verified_at:
        return None
    if not dict(account.preferences or {}).get(preference):
        return None
    from .mail import enqueue_email

    try:
        return enqueue_email(db, key, account.email, variables, business_key)
    except DomainError:
        # Optional notifications must never change the underlying business fact.
        return None


_ORDER_STATUS_LABELS = {
    'PENDING_CONFIRMATION': '待确认',
    'PENDING_PRINT': '待打印',
    'PRINTING': '打印中',
    'BINDING': '装订中',
    'PACKING': '装袋中',
    'READY_FOR_DELIVERY': '待配送',
    'DELIVERING': '配送中',
    'DELIVERED': '已送达',
    'COMPLETED': '已完成',
    'CANCELLED': '已取消',
    'DELIVERY_EXCEPTION': '配送异常',
}


_LOCAL_TZ = timezone(timedelta(hours=8))


def _mail_brand(db) -> str:
    section = db.scalar(select(ConfigSection).where(
        ConfigSection.section == 'contact'))
    contact = dict(section.data or {}) if section else {}
    return str(contact.get('displayName') or 'DormPrint')


def _order_link(order) -> str:
    origin = get_settings().public_origin.rstrip('/')
    return f'{origin}/u/orders/{url_quote(str(order.id))}'


def _admin_order_link(order) -> str:
    origin = get_settings().public_origin.rstrip('/')
    return f'{origin}/manage/orders/{url_quote(str(order.id))}'


def _mask_phone(value) -> str:
    digits = str(value or '').strip()
    if len(digits) >= 7:
        return f'{digits[:3]}****{digits[-4:]}'
    return digits


def _money(cents) -> str:
    return f'¥{int(cents or 0) / 100:.2f}'


def _local_label(value, fmt: str = '%Y-%m-%d %H:%M') -> str:
    moment = _as_utc(value)
    return moment.astimezone(_LOCAL_TZ).strftime(fmt) if moment else ''


def _order_slot_label(db, order) -> str:
    slot_id = order.confirmed_slot_id or order.requested_slot_id
    slot = db.get(DeliverySlot, slot_id) if slot_id else None
    if slot is None:
        return ''
    start = _local_label(slot.starts_at, '%H:%M')
    end = _local_label(slot.ends_at, '%H:%M')
    return f'{slot.date} {slot.name} {start}–{end}'.strip()


def _order_location_label(db, order) -> str:
    location = db.get(Location, order.location_id) if order.location_id else None
    return location.name if location else ''


def _active_customer(db, order):
    account_id = getattr(order, 'checkout_account_id', None)
    account = db.get(Account, account_id) if account_id else None
    if not account or account.status != 'ACTIVE' or not account.verified_at:
        return None
    return account


def _queue(db, key: str, to: str, variables: dict, business_key: str):
    if not to:
        return None
    try:
        with db.begin_nested():
            return enqueue_email(db, key, to, variables, business_key)
    except Exception:
        # Transactional facts must never be rolled back by a mail failure.
        return None


def notify_admin_order_created(db, order):
    """Tell active staff with order/production permissions about a new order."""
    variables = {
        'brandName': _mail_brand(db),
        'orderCode': str(getattr(order, 'pickup_code', '') or ''),
        'orderAmount': _money(getattr(order, 'total_cents', 0)),
        'customerNickname': str(getattr(order, 'nickname', '') or ''),
        'customerPhone': _mask_phone(getattr(order, 'phone', '')),
        'orderLink': _admin_order_link(order),
    }
    jobs = []
    recipients = db.scalars(select(Staff).where(
        Staff.enabled.is_(True), Staff.status == 'ACTIVE'))
    for member in recipients:
        capabilities = set(member.capabilities or [])
        allowed = member.is_super_admin or bool(
            capabilities & {'orders.manage', 'production.manage', '*'})
        if not allowed or not member.email or not member.email_verified:
            continue
        job = _queue(db, 'ADMIN_ORDER_CREATED', member.email, variables,
                     f'admin-order-email:{order.id}:{member.id}')
        if job is not None:
            jobs.append(job)
    return jobs


def notify_payment_success(db, order):
    account = _active_customer(db, order)
    if account is None:
        return None
    variables = {
        'brandName': _mail_brand(db),
        'orderCode': str(getattr(order, 'pickup_code', '') or ''),
        'paidAmount': _money(getattr(order, 'cash_received_cents', 0)
                             or getattr(order, 'total_cents', 0)),
        'orderLink': _order_link(order),
    }
    return _queue(db, 'PAYMENT_SUCCESS', account.email, variables,
                  f'order-email:PAYMENT_SUCCESS:{order.id}:{order.version}')


def notify_order_delivered(db, order, delivered_at=None):
    account = _active_customer(db, order)
    if account is None:
        return None
    stamp = delivered_at or (order.data or {}).get('deliveredAt') or utcnow()
    variables = {
        'brandName': _mail_brand(db),
        'orderCode': str(getattr(order, 'pickup_code', '') or ''),
        'deliveredAt': _local_label(stamp),
        'deliveryLocation': _order_location_label(db, order),
        'orderLink': _order_link(order),
    }
    return _queue(db, 'ORDER_DELIVERED', account.email, variables,
                  f'order-email:ORDER_DELIVERED:{order.id}:{order.version}')


def notify_delivery_updated(db, order):
    account = _active_customer(db, order)
    if account is None:
        return None
    variables = {
        'brandName': _mail_brand(db),
        'orderCode': str(getattr(order, 'pickup_code', '') or ''),
        'deliveryLocation': _order_location_label(db, order),
        'deliverySlot': _order_slot_label(db, order),
        'orderLink': _order_link(order),
    }
    return _queue(db, 'DELIVERY_UPDATED', account.email, variables,
                  f'order-email:DELIVERY_UPDATED:{order.id}:{order.version}')


def notify_courier_dispatch(db, batch):
    """Send the published batch to its assigned courier only."""
    courier = db.get(Staff, batch.courier_id) if batch.courier_id else None
    if (courier is None or not courier.email or not courier.email_verified
            or not courier.enabled or courier.status != 'ACTIVE'):
        return None
    order_count = db.scalar(select(func.count(DeliveryAssignment.id)).where(
        DeliveryAssignment.batch_id == batch.id,
        DeliveryAssignment.active.is_(True))) or 0
    origin = get_settings().public_origin.rstrip('/')
    variables = {
        'brandName': _mail_brand(db),
        'courierName': courier.name or '配送员',
        'batchName': batch.name,
        'deliveryDate': _local_label(batch.starts_at, '%Y-%m-%d'),
        'deliverySlot': (
            f'{_local_label(batch.starts_at, "%H:%M")}–'
            f'{_local_label(batch.ends_at, "%H:%M")}'),
        'orderCount': str(order_count),
        'courierLink': f'{origin}/courier/batches/{url_quote(str(batch.id))}',
    }
    return _queue(db, 'COURIER_DISPATCH', courier.email, variables,
                  f'courier-dispatch:{batch.id}:{batch.version}')


def notify_account_deleted(db, account, deadline=None):
    if account is None or not account.email:
        return None
    variables = {
        'brandName': _mail_brand(db),
        'deletionDeadline': _local_label(deadline, '%Y-%m-%d') or '14 天内',
    }
    return _queue(db, 'ACCOUNT_DELETED', account.email, variables,
                  f'account-deleted:{account.id}')


def notify_order_event(db, order, event):
    """Route order lifecycle notifications.

    New orders notify active staff with order/production permissions. Customer
    order-created and order-status emails are intentionally retired to keep
    mail volume low; payment and delivery steps call their dedicated helpers.
    """
    event_key = str(event or '').strip().upper()
    if event_key in ('CREATED', 'ORDER_CREATED'):
        return notify_admin_order_created(db, order)
    return None
