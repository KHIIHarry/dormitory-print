"""Growth domain tests: quote selection, coupon ledger, refunds and referrals.

These tests exercise the real PostgreSQL schema; they never substitute SQLite.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import (
    Account,
    AfterSale,
    ConfigSection,
    Coupon,
    DeliverySlot,
    Location,
    Order,
    Preparation,
    Principal,
    PromotionEvent,
    Referral,
    Refund,
    Staff,
    Campaign,
    Quote,
    utcnow,
)


def uid(prefix='x'):
    return f'{prefix}-{uuid4().hex[:12]}'


def config(db, section, data, version=1):
    row = db.scalar(select(ConfigSection).where(ConfigSection.section == section))
    if row is None:
        row = ConfigSection(section=section, data=data, version=version)
        db.add(row)
    else:
        row.data = data
        row.version = version
    db.flush()
    return row


def base_config(db, volume=None):
    config(db, 'prices', {'bwSingle': 100, 'bwDuplex': 180, 'colorSingle': 200,
                          'colorDuplex': 360, 'photo': 500, 'corner': 0, 'side': 0})
    config(db, 'workflow', {'quoteMinutes': 15})
    if volume is not None:
        config(db, 'volume-discounts', volume)
    return db


def volume_policy(tiers, enabled=True, version=1):
    return {
        'id': 'volume-policy', 'version': version, 'enabled': enabled,
        'effectiveAt': None, 'description': '测试满量规则', 'tiers': tiers,
    }


def make_account(db, verified=True, status='ACTIVE'):
    principal = Principal()
    db.add(principal)
    db.flush()
    account = Account(
        principal_id=principal.id,
        email=f'{uuid4()}@example.invalid',
        password_hash='not-a-real-hash',
        status=status,
        verified_at=utcnow() if verified else None,
        display_name='测试同学',
        invite_code=uid('invite'),
    )
    db.add(account)
    db.flush()
    principal.account_id = account.id
    db.flush()
    return account


def make_prep(db, account=None, replaces_order_id=None):
    principal = Principal()
    db.add(principal)
    db.flush()
    prep = Preparation(
        principal_id=principal.id,
        checkout_account_id=account.id if account else None,
        status='READY',
        input={},
        data={},
        expires_at=utcnow() + timedelta(hours=1),
        replaces_order_id=replaces_order_id,
    )
    db.add(prep)
    db.flush()
    return prep


def line(key, related, service, quantity, unit_price, unit='SHEET'):
    return {
        'key': key, 'relatedId': related, 'unit': unit, 'quantity': quantity,
        'unitPriceCents': unit_price, 'amountCents': quantity * unit_price,
        'service': service,
    }


def make_campaign(db, kind='NEW_CUSTOMER', amount=500, budget=None,
                  enabled=True, min_spend=0, service='ALL', validity_days=30,
                  per_account=1, issuance=None, qualification=None):
    campaign = Campaign(
        name=uid('活动'), kind=kind, enabled=enabled, budget_cents=budget,
        reserved_liability_cents=0, realized_discount_cents=0,
        data={
            'amountCents': amount, 'minSpendCents': min_spend, 'service': service,
            'validityDays': validity_days, 'perAccountLimit': per_account,
            'issuanceLimit': issuance, 'startsAt': None, 'endsAt': None,
            'description': '测试活动', 'stackable': False,
        },
    )
    if qualification:
        campaign.data.update(qualification)
    db.add(campaign)
    db.flush()
    return campaign


def make_coupon(db, account, campaign=None, amount=500, min_spend=0, service='ALL',
                status='AVAILABLE', valid_days=30, risk_hold='NONE', source=None,
                used_order_id=None):
    coupon = Coupon(
        account_id=account.id, campaign_id=campaign.id if campaign else None,
        amount_cents=amount, status=status, risk_hold=risk_hold,
        source_key=source or uid('coupon'),
        valid_until=utcnow() + timedelta(days=valid_days),
        used_order_id=used_order_id,
        data={
            'terms': {
                'amountCents': amount, 'minSpendCents': min_spend,
                'service': service,
                'campaignId': campaign.id if campaign else None,
                'name': campaign.name if campaign else '补偿券',
                'campaignVersion': campaign.version if campaign else None,
            },
            'liabilityReserved': bool(campaign and campaign.budget_cents is not None),
        },
    )
    db.add(coupon)
    if campaign and campaign.budget_cents is not None:
        campaign.reserved_liability_cents += amount
    db.flush()
    return coupon



def make_staff(db):
    staff = Staff(username=f'admin-{uuid4().hex[:10]}', name='测试员工',
                  password_hash='x', role='ADMIN', status='ACTIVE')
    db.add(staff)
    db.flush()
    return staff


def make_refund(db, order, amount, reason='退款', affected=None):
    staff = make_staff(db)
    after = AfterSale(
        order_id=order.id, source='ADMIN', actor_id=staff.id,
        responsibility='CUSTOMER', solution='REFUND', status='COMPLETED',
        reason=reason, suggested_cents=amount, approved_cents=amount,
        adjustment_basis='测试',
    )
    db.add(after)
    db.flush()
    refund = Refund(order_id=order.id, after_sale_id=after.id,
                    amount_cents=amount, staff_id=staff.id, reason=reason,
                    data={'affectedItems': affected or []})
    db.add(refund)
    db.flush()
    return refund


def make_order(db, prep, quote, account=None, status='PENDING_CONFIRMATION',
               cash=0, refunded=0, settlement_kind=None):
    from app.growth import quote_view

    location = Location(name=uid('楼栋'), x=0.5, y=0.5)
    slot = DeliverySlot(
        date='2026-10-30', name='测试时段',
        starts_at=utcnow(), ends_at=utcnow() + timedelta(hours=1), capacity=10,
    )
    db.add_all([location, slot])
    db.flush()
    order = Order(
        principal_id=prep.principal_id,
        checkout_account_id=account.id if account else None,
        pickup_code=uid('pick'),
        status=status,
        preparation_id=prep.id,
        quote_id=quote.id,
        total_cents=quote.total_cents,
        cash_received_cents=cash,
        refunded_cents=refunded,
        settlement_kind=settlement_kind,
        nickname='测试同学',
        phone='13800000000',
        location_id=location.id,
        requested_slot_id=slot.id,
        close_at=utcnow() + timedelta(hours=2),
        data={'quote': quote_view(quote)},
    )
    db.add(order)
    db.flush()
    return order


def test_volume_discount_rounds_once_on_a4_subtotal_and_allocates_only_a4(db):
    from app.growth import build_quote, quote_view

    db = base_config(db, volume_policy([{'id': 't1', 'minSheets': 1, 'payRateBps': 9000}]))
    prep = make_prep(db)
    lines = [
        line('a4-1', 'file-1', 'A4', 1, 1001),
        line('photo-1', 'file-2', 'PHOTO', 1, 500),
        line('bind-1', 'file-3', 'BINDING', 1, 300, 'BOOKLET'),
    ]
    quote = build_quote(db, prep, lines, actor=None)
    view = quote_view(quote)
    assert view['subtotalCents'] == 1801
    assert view['discountCents'] == 100
    assert view['totalCents'] == 1701
    assert view['selectedPromotionType'] == 'VOLUME_TIER'
    assert view['volumeDiscountCandidate']['a4Sheets'] == 1
    assert view['volumeDiscountCandidate']['a4SubtotalCents'] == 1001
    assert view['discountAllocations'] == [
        {'lineKey': 'a4-1', 'relatedId': 'file-1', 'amountCents': 100}
    ]
    assert view['discounts'][0]['kind'] == 'VOLUME_TIER'
    assert view['discounts'][0]['amountCents'] == 100


def test_volume_counts_physical_sheets_across_multiple_a4_lines(db):
    from app.growth import build_quote, quote_view

    db = base_config(db, volume_policy([{'id': 't1', 'minSheets': 14, 'payRateBps': 8000}]))
    prep = make_prep(db)
    lines = [line('a', 'f1', 'A4', 8, 100), line('b', 'f2', 'A4', 6, 100)]
    view = quote_view(build_quote(db, prep, lines, actor=None))
    assert view['volumeDiscountCandidate']['a4Sheets'] == 14
    assert view['volumeDiscountCandidate']['a4SubtotalCents'] == 1400
    assert view['discountCents'] == 280
    assert sum(x['amountCents'] for x in view['discountAllocations']) == 280


def test_volume_tier_selection_uses_best_discount_then_higher_tier(db):
    from app.growth import build_quote, quote_view

    db = base_config(db, volume_policy([
        {'id': 't1', 'minSheets': 10, 'payRateBps': 9000},
        {'id': 't2', 'minSheets': 20, 'payRateBps': 9000},
        {'id': 't3', 'minSheets': 30, 'payRateBps': 5000},
    ], version=4))
    prep = make_prep(db)
    view = quote_view(build_quote(db, prep, [line('a', 'f1', 'A4', 30, 100)], actor=None))
    assert view['volumeDiscountCandidate']['tierId'] == 't3'
    assert view['discountCents'] == 1500
    assert view['promotionRuleVersion'] == 'volume-4'


def test_tie_prefers_volume_and_keeps_coupon_available(db):
    from app.growth import build_quote, quote_view, reserve_coupon

    db = base_config(db, volume_policy([{'id': 't1', 'minSheets': 1, 'payRateBps': 9000}]))
    account = make_account(db)
    campaign = make_campaign(db, amount=100, budget=1000)
    coupon = make_coupon(db, account, campaign, amount=100)
    prep = make_prep(db, account)
    view = quote_view(build_quote(db, prep, [line('a', 'f1', 'A4', 1, 1000)], actor=None))
    assert view['selectedPromotionType'] == 'VOLUME_TIER'
    assert view['discountCents'] == 100
    quote = db.get(Quote, view['id'])
    reserve_coupon(db, make_order(db, prep, quote, account), quote)
    assert db.get(Coupon, coupon.id).status == 'AVAILABLE'
    assert db.get(Coupon, coupon.id).reserved_order_id is None


def test_bigger_coupon_wins_and_reserve_settle_keeps_budget_consistent(db):
    from app.growth import build_quote, quote_view, reserve_coupon, settle_coupon

    db = base_config(db, volume_policy([{'id': 't1', 'minSheets': 1, 'payRateBps': 9000}]))
    account = make_account(db)
    campaign = make_campaign(db, amount=300, budget=1000)
    coupon = make_coupon(db, account, campaign, amount=300)
    prep = make_prep(db, account)
    quote = build_quote(db, prep, [line('a', 'f1', 'A4', 1, 1000)], actor=None)
    view = quote_view(quote)
    assert view['selectedPromotionType'] == 'COUPON'
    assert view['discountCents'] == 300
    order = make_order(db, prep, quote, account)
    reserve_coupon(db, order, quote)
    db.refresh(coupon)
    assert coupon.status == 'RESERVED'
    assert coupon.reserved_order_id == order.id
    assert campaign.reserved_liability_cents == 300
    settle_coupon(db, order)
    db.refresh(coupon)
    db.refresh(campaign)
    assert coupon.status == 'USED'
    assert coupon.used_order_id == order.id
    assert coupon.reserved_order_id is None
    assert campaign.reserved_liability_cents == 0
    assert campaign.realized_discount_cents == 300
    assert campaign.reserved_liability_cents + campaign.realized_discount_cents <= 1000


def test_partial_coupon_discount_returns_unused_face_to_budget(db):
    from app.growth import build_quote, reserve_coupon, settle_coupon

    db = base_config(db, volume_policy([]))
    account = make_account(db)
    campaign = make_campaign(db, amount=500, budget=1000)
    coupon = make_coupon(db, account, campaign, amount=500, min_spend=0)
    prep = make_prep(db, account)
    quote = build_quote(db, prep, [line('a', 'f1', 'A4', 1, 200)], actor=None)
    order = make_order(db, prep, quote, account)
    reserve_coupon(db, order, quote)
    settle_coupon(db, order)
    db.refresh(campaign)
    assert campaign.reserved_liability_cents == 0
    assert campaign.realized_discount_cents == 200
    assert campaign.reserved_liability_cents + campaign.realized_discount_cents == 200


def test_a4_coupon_never_discounts_photo_or_uses_photo_for_threshold(db):
    from app.growth import build_quote, quote_view

    db = base_config(db, volume_policy([]))
    account = make_account(db)
    campaign = make_campaign(db, amount=500, service='A4', min_spend=100)
    make_coupon(db, account, campaign, amount=500, service='A4', min_spend=100)
    prep = make_prep(db, account)
    view = quote_view(build_quote(db, prep, [line('p', 'f1', 'PHOTO', 1, 1000)], actor=None))
    assert view['selectedPromotionType'] == 'NONE'
    assert view['discountCents'] == 0
    assert view['totalCents'] == 1000


def test_no_coupon_still_applies_volume_and_specific_coupon_compares_with_volume(db):
    from app.growth import build_quote, quote_view, PromotionIntent

    db = base_config(db, volume_policy([{'id': 't1', 'minSheets': 1, 'payRateBps': 9000}]))
    account = make_account(db)
    campaign = make_campaign(db, amount=50, budget=1000)
    coupon = make_coupon(db, account, campaign, amount=50)
    prep = make_prep(db, account)
    lines = [line('a', 'f1', 'A4', 1, 1000)]
    no_coupon = quote_view(build_quote(db, prep, lines, actor=None,
                                       intent=PromotionIntent(selectionMode='NO_COUPON')))
    assert no_coupon['selectedPromotionType'] == 'VOLUME_TIER'
    specific = quote_view(build_quote(db, prep, lines, actor=None,
        intent=PromotionIntent(selectionMode='SPECIFIC_COUPON', couponGrantId=coupon.id)))
    assert specific['selectedPromotionType'] == 'VOLUME_TIER'
    assert specific['discountCents'] == 100


def test_same_order_reserved_coupon_keeps_terms_when_campaign_stops(db):
    from app.growth import build_quote, quote_view, reserve_coupon

    db = base_config(db, volume_policy([]))
    account = make_account(db)
    campaign = make_campaign(db, amount=300, budget=1000, service='A4', min_spend=100)
    coupon = make_coupon(db, account, campaign, amount=300, service='A4', min_spend=100)
    prep = make_prep(db, account)
    quote = build_quote(db, prep, [line('a', 'f1', 'A4', 1, 1000)], actor=None)
    order = make_order(db, prep, quote, account)
    reserve_coupon(db, order, quote)
    campaign.enabled = False
    db.flush()
    revision = make_prep(db, account, replaces_order_id=order.id)
    view = quote_view(build_quote(db, revision, [line('a', 'f1', 'A4', 1, 1000)], actor=None))
    assert view['selectedPromotionType'] == 'COUPON'
    assert view['discountCents'] == 300


def test_release_is_idempotent_and_keeps_liability(db):
    from app.growth import build_quote, reserve_coupon, release_coupon

    db = base_config(db, volume_policy([]))
    account = make_account(db)
    campaign = make_campaign(db, amount=300, budget=1000)
    coupon = make_coupon(db, account, campaign, amount=300)
    prep = make_prep(db, account)
    quote = build_quote(db, prep, [line('a', 'f1', 'A4', 1, 1000)], actor=None)
    order = make_order(db, prep, quote, account)
    reserve_coupon(db, order, quote)
    release_coupon(db, order)
    release_coupon(db, order)
    db.refresh(coupon)
    db.refresh(campaign)
    assert coupon.status == 'AVAILABLE'
    assert coupon.reserved_order_id is None
    assert campaign.reserved_liability_cents == 300
    assert db.scalar(select(PromotionEvent).where(
        PromotionEvent.business_key == f'coupon-release:{coupon.id}:{order.id}:1')) is not None


def test_concurrent_reservation_of_one_coupon_only_one_order_wins(db):
    from app.db import SessionLocal
    from app.growth import build_quote, reserve_coupon
    from app.core import DomainError

    db = base_config(db, volume_policy([]))
    account = make_account(db)
    campaign = make_campaign(db, amount=300, budget=1000)
    coupon = make_coupon(db, account, campaign, amount=300)
    prep = make_prep(db, account)
    quote = build_quote(db, prep, [line('a', 'f1', 'A4', 1, 1000)], actor=None)
    first = make_order(db, prep, quote, account)
    second = make_order(db, prep, quote, account)
    db.commit()
    try:
        other = SessionLocal()
        try:
            reserve_coupon(db, first, quote)
            db.commit()
            with pytest.raises(DomainError) as error:
                reserve_coupon(other, second, quote)
                other.commit()
            assert error.value.code in ('COUPON_ALREADY_RESERVED', 'COUPON_UNAVAILABLE')
        finally:
            other.rollback()
            other.close()
    finally:
        db.rollback()


def test_full_refund_creates_one_seven_day_compensation_coupon_with_original_terms(db):
    from app.growth import build_quote, reserve_coupon, settle_coupon, on_refund

    db = base_config(db, volume_policy([]))
    account = make_account(db)
    campaign = make_campaign(db, amount=500, budget=1000, service='A4', min_spend=200)
    coupon = make_coupon(db, account, campaign, amount=500, service='A4', min_spend=200)
    prep = make_prep(db, account)
    quote = build_quote(db, prep, [line('a', 'f1', 'A4', 1, 400)], actor=None)
    order = make_order(db, prep, quote, account, cash=400, settlement_kind='CASH_MANUAL')
    reserve_coupon(db, order, quote)
    settle_coupon(db, order)
    refund = make_refund(db, order, 400, '整单退款')
    order.refunded_cents = 400
    db.flush()
    on_refund(db, order, refund)
    compensation = db.scalar(select(Coupon).where(
        Coupon.source_key == f'REFUND_COMPENSATION:{order.id}'))
    assert compensation is not None
    assert compensation.status == 'AVAILABLE'
    assert compensation.amount_cents == 400
    assert compensation.campaign_id is None
    assert compensation.data['terms']['service'] == 'A4'
    assert compensation.data['terms']['minSpendCents'] == 200
    remaining = compensation.valid_until - utcnow()
    assert timedelta(days=6, hours=23) < remaining <= timedelta(days=7)
    db.refresh(coupon)
    assert coupon.status == 'USED'
    assert coupon.used_order_id == order.id
    on_refund(db, order, refund)
    assert db.scalar(select(Coupon).where(
        Coupon.source_key == f'REFUND_COMPENSATION:{order.id}')) is not None
    assert len(db.scalars(select(Coupon).where(
        Coupon.source_key.like(f'REFUND_COMPENSATION:{order.id}%'))).all()) == 1


def test_partial_refund_and_volume_only_refund_do_not_issue_compensation(db):
    from app.growth import build_quote, reserve_coupon, settle_coupon, on_refund

    db = base_config(db, volume_policy([{'id': 't1', 'minSheets': 1, 'payRateBps': 9000}]))
    account = make_account(db)
    campaign = make_campaign(db, amount=500, budget=1000)
    make_coupon(db, account, campaign, amount=500)
    prep = make_prep(db, account)
    quote = build_quote(db, prep, [line('a', 'f1', 'A4', 1, 1000)], actor=None)
    order = make_order(db, prep, quote, account, cash=900, settlement_kind='CASH_MANUAL')
    reserve_coupon(db, order, quote)
    settle_coupon(db, order)
    order.refunded_cents = 300
    refund = make_refund(db, order, 300)
    on_refund(db, order, refund)
    assert db.scalar(select(Coupon).where(
        Coupon.source_key == f'REFUND_COMPENSATION:{order.id}')) is None

    volume_prep = make_prep(db, account)
    volume_quote = build_quote(db, volume_prep,
                               [line('v', 'f2', 'A4', 1, 1000)], actor=None)
    volume_order = make_order(db, volume_prep, volume_quote, account, cash=900,
                              settlement_kind='CASH_MANUAL')
    volume_order.refunded_cents = 900
    volume_refund = make_refund(db, volume_order, 900)
    on_refund(db, volume_order, volume_refund)
    assert db.scalar(select(Coupon).where(
        Coupon.source_key == f'REFUND_COMPENSATION:{volume_order.id}')) is None


def test_registration_verification_grants_configured_coupons_once(db):
    from app.growth import on_registration_verified

    db = base_config(db, volume_policy([]))
    account = make_account(db)
    new_campaign = make_campaign(db, kind='NEW_CUSTOMER', amount=500, budget=1000)
    invitee_campaign = make_campaign(db, kind='INVITEE', amount=300, budget=1000)
    inviter = make_account(db)
    relation = Referral(inviter_id=inviter.id, invitee_id=account.id,
                        status='WAITING_FIRST_ORDER', data={})
    db.add(relation)
    db.flush()
    first = on_registration_verified(db, account)
    second = on_registration_verified(db, account)
    coupons = db.scalars(select(Coupon).where(Coupon.account_id == account.id)).all()
    assert len(coupons) == 2
    assert len(first) == 2
    assert second == []
    db.refresh(new_campaign)
    db.refresh(invitee_campaign)
    assert new_campaign.reserved_liability_cents == 500
    assert invitee_campaign.reserved_liability_cents == 300
    assert {c.campaign_id for c in coupons} == {new_campaign.id, invitee_campaign.id}
    terms = {c.campaign_id: c.data['terms'] for c in coupons}
    assert terms[new_campaign.id]['amountCents'] == 500


def test_registration_bonus_respects_budget_without_negative_liability(db):
    from app.growth import on_registration_verified

    db = base_config(db, volume_policy([]))
    account = make_account(db)
    campaign = make_campaign(db, kind='NEW_CUSTOMER', amount=500, budget=400)
    granted = on_registration_verified(db, account)
    assert granted == []
    db.refresh(campaign)
    assert campaign.reserved_liability_cents == 0
    assert db.scalar(select(Coupon).where(Coupon.account_id == account.id)) is None


def test_referral_qualification_locks_first_delivered_paid_order_only(db):
    from app.growth import build_quote, reserve_coupon, settle_coupon, qualify_referral

    db = base_config(db, volume_policy([]))
    inviter = make_account(db)
    invitee = make_account(db)
    reward_campaign = make_campaign(
        db, kind='REFERRER', amount=200, budget=1000,
        qualification={'qualificationMinSpendCents': 100, 'qualificationService': 'ALL'},
    )
    relation = Referral(
        inviter_id=inviter.id, invitee_id=invitee.id,
        status='WAITING_FIRST_ORDER',
        data={'rewardCampaigns': [campaign_snapshot(reward_campaign)],
              'qualificationMinSpendCents': 100,
              'qualificationService': 'ALL',
              'qualificationExpiresAt': None},
    )
    db.add(relation)
    db.flush()
    prep = make_prep(db, invitee)
    quote = build_quote(db, prep, [line('a', 'f1', 'A4', 1, 1000)], actor=None)
    order = make_order(db, prep, quote, invitee, status='DELIVERED', cash=1000,
                       settlement_kind='CASH_MANUAL')
    assert qualify_referral(db, order) is True
    db.refresh(relation)
    assert relation.qualified_order_id == order.id
    assert relation.status == 'GRANTED'
    reward = db.scalar(select(Coupon).where(
        Coupon.source_key == f'REFERRAL_REWARD:{relation.id}:{reward_campaign.id}'))
    assert reward is not None and reward.amount_cents == 200

    second_prep = make_prep(db, invitee)
    second_quote = build_quote(db, second_prep, [line('b', 'f2', 'A4', 1, 1200)], actor=None)
    second = make_order(db, second_prep, second_quote, invitee, status='DELIVERED',
                        cash=1200, settlement_kind='CASH_MANUAL')
    assert qualify_referral(db, second) is False
    db.refresh(relation)
    assert relation.qualified_order_id == order.id
    assert len(db.scalars(select(Coupon).where(
        Coupon.source_key.like(f'REFERRAL_REWARD:{relation.id}%'))).all()) == 1


def test_referral_zero_value_or_unpaid_order_never_qualifies(db):
    from app.growth import build_quote, qualify_referral

    db = base_config(db, volume_policy([]))
    inviter = make_account(db)
    invitee = make_account(db)
    relation = Referral(inviter_id=inviter.id, invitee_id=invitee.id,
                        status='WAITING_FIRST_ORDER', data={})
    db.add(relation)
    db.flush()
    prep = make_prep(db, invitee)
    quote = build_quote(db, prep, [line('a', 'f1', 'A4', 1, 1000)], actor=None)
    zero = make_order(db, prep, quote, invitee, status='DELIVERED', cash=0,
                      settlement_kind='ZERO_PAYABLE')
    assert qualify_referral(db, zero) is False
    db.refresh(relation)
    assert relation.qualified_order_id is None


def test_refund_recheck_revokes_unused_referral_reward_and_keeps_used_history(db):
    from app.growth import build_quote, reserve_coupon, settle_coupon, on_refund, \
        qualify_referral

    db = base_config(db, volume_policy([]))
    inviter = make_account(db)
    invitee = make_account(db)
    reward_campaign = make_campaign(db, kind='REFERRER', amount=200, budget=1000)
    relation = Referral(
        inviter_id=inviter.id, invitee_id=invitee.id,
        status='WAITING_FIRST_ORDER',
        data={'rewardCampaigns': [campaign_snapshot(reward_campaign)],
              'qualificationMinSpendCents': 500, 'qualificationService': 'ALL',
              'qualificationExpiresAt': None},
    )
    db.add(relation)
    db.flush()
    prep = make_prep(db, invitee)
    quote = build_quote(db, prep, [line('a', 'f1', 'A4', 1, 1000)], actor=None)
    order = make_order(db, prep, quote, invitee, status='DELIVERED', cash=1000,
                       settlement_kind='CASH_MANUAL')
    assert qualify_referral(db, order) is True
    reward = db.scalar(select(Coupon).where(
        Coupon.source_key == f'REFERRAL_REWARD:{relation.id}:{reward_campaign.id}'))
    assert reward.status == 'AVAILABLE'
    order.refunded_cents = 800
    refund = make_refund(db, order, 800)
    on_refund(db, order, refund)
    db.refresh(reward)
    db.refresh(relation)
    assert reward.status == 'REVOKED'
    assert relation.status == 'DISQUALIFIED'
    assert relation.qualified_order_id == order.id
    db.refresh(reward_campaign)
    assert reward_campaign.reserved_liability_cents == 0


def campaign_snapshot(campaign):
    return {
        'id': campaign.id, 'version': campaign.version, 'name': campaign.name,
        'kind': campaign.kind, 'enabled': campaign.enabled,
        **campaign.data,
    }


# ---------------------------------------------------------------------------
# HTTP routes: OpenAPI-shaped DTOs, permissions and versioning
# ---------------------------------------------------------------------------


def test_volume_policy_route_versions_and_rejects_duplicate_tiers(admin_client, api, db):
    response = api(admin_client, '/admin/config/volume-discounts', method='GET')
    assert response.status_code == 200, response.text
    policy = response.json()['data']
    assert policy['enabled'] is False
    payload = {
        'id': policy['id'], 'enabled': True, 'effectiveAt': None,
        'description': '测试满量', 'tiers': [
            {'id': 't10', 'minSheets': 10, 'payRateBps': 9000},
            {'id': 't20', 'minSheets': 20, 'payRateBps': 8000},
        ],
        'expectedVersion': policy['version'], 'reason': '配置测试',
    }
    from app.core import DomainError
    from app.growth import update_volume_policy
    from app.growth_schemas import VolumePolicyInput

    updated = update_volume_policy(db, VolumePolicyInput.model_validate(payload))
    assert updated['version'] == policy['version'] + 1
    assert [tier['minSheets'] for tier in updated['tiers']] == [10, 20]
    with pytest.raises(DomainError) as conflict:
        update_volume_policy(db, VolumePolicyInput.model_validate(
            {**payload, 'expectedVersion': policy['version']}))
    assert conflict.value.code == 'VERSION_CONFLICT'
    with pytest.raises(Exception):
        VolumePolicyInput.model_validate({
            **payload, 'expectedVersion': updated['version'],
            'tiers': [{'id': 'a', 'minSheets': 10, 'payRateBps': 9000},
                      {'id': 'b', 'minSheets': 10, 'payRateBps': 8000}],
        })


def test_campaign_routes_create_list_and_patch(admin_client, api):
    body = {
        'name': '新客测试券', 'kind': 'NEW_CUSTOMER', 'enabled': True,
        'amountCents': 500, 'minSpendCents': 0, 'service': 'ALL',
        'validityDays': 30, 'budgetCents': 10000, 'issuanceLimit': 100,
        'perAccountLimit': 1, 'startsAt': None, 'endsAt': None,
        'description': '测试', 'stackable': False, 'reason': '创建活动',
        'qualificationExpiresAt': None, 'qualificationMinSpendCents': 0,
        'qualificationService': 'ALL',
    }
    created = api(admin_client, '/admin/promotions/campaigns', body)
    assert created.status_code == 200, created.text
    campaign = created.json()['data']
    assert campaign['id'] and campaign['version'] == 1
    listing = api(admin_client, '/admin/promotions/campaigns', method='GET')
    assert listing.status_code == 200
    assert any(item['id'] == campaign['id'] for item in listing.json()['data']['items'])
    patched = api(admin_client, f"/admin/promotions/campaigns/{campaign['id']}",
                  {**body, 'amountCents': 600, 'expectedVersion': 1},
                  method='PATCH')
    assert patched.status_code == 200, patched.text
    assert patched.json()['data']['amountCents'] == 600
    assert patched.json()['data']['version'] == 2
    invalid = api(admin_client, '/admin/promotions/campaigns',
                  {**body, 'stackable': True})
    assert invalid.status_code == 422


def test_customer_coupon_route_only_returns_own_account(client_factory, db, api):
    from app.core import token_hash
    from app.models import Account, AuthSession, Coupon, Principal

    principal = Principal()
    db.add(principal)
    db.flush()
    account = Account(principal_id=principal.id, email=f'{uuid4().hex[:8]}@example.invalid',
                      password_hash='x', status='ACTIVE', verified_at=utcnow(),
                      invite_code=uid('i'))
    db.add(account)
    db.flush()
    principal.account_id = account.id
    token = uid('tok')
    db.add(AuthSession(kind='CUSTOMER', account_id=account.id,
                       principal_id=principal.id, token_hash=token_hash(token),
                       expires_at=utcnow() + timedelta(days=30)))
    coupon = Coupon(
        account_id=account.id, amount_cents=300,
        status='AVAILABLE', risk_hold='NONE', source_key=uid('http-coupon'),
        valid_until=utcnow() + timedelta(days=7),
        data={'terms': {'amountCents': 300, 'minSpendCents': 100,
                        'service': 'A4', 'name': '测试券', 'campaignId': None}},
    )
    db.add(coupon)
    db.commit()
    customer_client = client_factory()
    customer_client.cookies.set('dormprint_account_session', token)
    customer_client.account = account
    response = api(customer_client, '/customer/coupons', method='GET')
    assert response.status_code == 200, response.text
    items = response.json()['data']['items']
    assert [item['id'] for item in items] == [coupon.id]
    assert items[0]['couponGrantId'] == coupon.id
    assert items[0]['service'] == 'A4'
    assert items[0]['minSpendCents'] == 100


def test_admin_customer_list_is_masked_and_counts_business(admin_client, account_factory, api):
    account = account_factory(email='listed-customer@example.invalid', invite_code=uid('i'))
    response = api(admin_client, '/admin/customers?q=listed-customer', method='GET')
    assert response.status_code == 200, response.text
    items = response.json()['data']['items']
    assert len(items) == 1
    assert items[0]['id'] == account.id
    assert items[0]['emailMasked'] == 'l***@example.invalid'
    assert 'listed-customer@' not in response.text
    assert items[0]['status'] == 'ACTIVE'
    detail = api(admin_client, f'/admin/customers/{account.id}', method='GET')
    assert detail.status_code == 200
    assert detail.json()['data']['orderCount'] == 0


def test_admin_routes_require_admin_capability(client, api):
    response = api(client, '/admin/customers', method='GET')
    assert response.status_code in (401, 403)


def test_coupon_freeze_requires_reason_and_version(admin_client, db, api):
    from app.models import Coupon

    account = make_account(db)
    coupon = make_coupon(db, account, amount=300)
    db.commit()
    missing_reason = api(admin_client, f'/admin/promotions/coupons/{coupon.id}/freeze',
                         {'expectedVersion': coupon.version, 'reason': ''})
    assert missing_reason.status_code == 422
    response = api(admin_client, f'/admin/promotions/coupons/{coupon.id}/freeze',
                   {'expectedVersion': coupon.version, 'reason': '风险核对'})
    assert response.status_code == 200, response.text
    assert response.json()['data']['riskHold'] == 'FROZEN'
    conflict = api(admin_client, f'/admin/promotions/coupons/{coupon.id}/freeze',
                   {'expectedVersion': coupon.version, 'reason': '重复'})
    assert conflict.status_code == 409


def test_quote_snapshot_is_strict_for_commerce_order_adapter(db):
    from app.commerce import quote_view as commerce_quote_view
    from app.growth import build_quote, quote_view
    from app.growth_schemas import QuoteDTO

    db = base_config(db, volume_policy([{'id': 't1', 'minSheets': 1, 'payRateBps': 9000}]))
    prep = make_prep(db)
    quote = build_quote(db, prep, [line('a', 'f1', 'A4', 1, 1000)], actor=None)
    strict = commerce_quote_view(quote)
    QuoteDTO.model_validate(strict)
    assert strict['selectedPromotionType'] == 'VOLUME_TIER'
    assert strict['discountAllocations']
    aliases = {field.alias or name for name, field in QuoteDTO.model_fields.items()}
    assert set(strict) <= aliases
    assert quote_view(quote)['totalCents'] == 900


def test_expired_available_coupon_releases_campaign_liability(db):
    from app.growth import expire_due_coupons

    db = base_config(db, volume_policy([]))
    account = make_account(db)
    campaign = make_campaign(db, amount=300, budget=1000)
    coupon = make_coupon(db, account, campaign, amount=300)
    coupon.valid_until = utcnow() - timedelta(seconds=1)
    db.flush()
    assert expire_due_coupons(db) == 1
    db.refresh(coupon)
    db.refresh(campaign)
    assert coupon.status == 'EXPIRED'
    assert campaign.reserved_liability_cents == 0


def test_referral_reward_is_issued_to_inviter_not_invitee(db):
    from app.growth import build_quote, qualify_referral

    db = base_config(db, volume_policy([]))
    inviter = make_account(db)
    invitee = make_account(db)
    reward_campaign = make_campaign(db, kind='REFERRER', amount=200, budget=1000)
    relation = Referral(
        inviter_id=inviter.id, invitee_id=invitee.id,
        status='WAITING_FIRST_ORDER',
        data={'rewardCampaigns': [campaign_snapshot(reward_campaign)],
              'qualificationMinSpendCents': 0, 'qualificationService': 'ALL'},
    )
    db.add(relation)
    db.flush()
    prep = make_prep(db, invitee)
    quote = build_quote(db, prep, [line('a', 'f1', 'A4', 1, 1000)], actor=None)
    order = make_order(db, prep, quote, invitee, status='DELIVERED', cash=1000,
                       settlement_kind='CASH_MANUAL')
    assert qualify_referral(db, order) is True
    reward = db.scalar(select(Coupon).where(
        Coupon.source_key == f'REFERRAL_REWARD:{relation.id}:{reward_campaign.id}'))
    assert reward.account_id == inviter.id
    assert reward.account_id != invitee.id


def test_notification_helper_requires_explicit_subscription(db):
    from app.growth import enqueue_notification_email

    db = base_config(db, volume_policy([]))
    account = make_account(db)
    result = enqueue_notification_email(db, account, 'ORDER_CREATED', {
        'brandName': 'DormPrint', 'orderCode': 'ABC', 'orderStatus': '待确认',
        'orderLink': 'https://example.invalid/orders/1',
    }, 'notify-unsubscribed-1')
    assert result is None


def test_promotion_records_route_reports_server_summary(admin_client, db, api):
    from app.growth import build_quote, quote_view
    from app.models import Quote

    db = base_config(db, volume_policy([]))
    account = make_account(db)
    campaign = make_campaign(db, amount=300, budget=1000)
    coupon = make_coupon(db, account, campaign, amount=300, status='USED')
    prep = make_prep(db, account)
    quote = build_quote(db, prep, [line('a', 'f1', 'A4', 1, 1000)], actor=None)
    order = make_order(db, prep, quote, account, cash=700,
                       settlement_kind='CASH_MANUAL')
    from app.growth import _event

    _event(db, 'test-used-event', 'USED', account_id=account.id,
           campaign_id=campaign.id, coupon_id=coupon.id, order_id=order.id,
           amount_cents=300)
    db.commit()
    response = api(admin_client, '/admin/promotions/records', method='GET')
    assert response.status_code == 200, response.text
    body = response.json()['data']
    assert body['summary']['used'] >= 1
    assert body['summary']['couponDiscountCents'] >= 300
    assert any(item['couponGrantId'] == coupon.id for item in body['items'])


def test_quote_ignores_forged_client_sheet_and_discount_fields(db):
    from app.growth import build_quote, quote_view

    db = base_config(db, volume_policy([{'id': 't1', 'minSheets': 10, 'payRateBps': 9000}]))
    prep = make_prep(db)
    forged = {**line('a', 'f1', 'A4', 2, 100), 'a4Sheets': 999,
              'discountCents': 9999, 'payRateBps': 0}
    view = quote_view(build_quote(db, prep, [forged], actor=None))
    assert view['subtotalCents'] == 200
    assert view['discountCents'] == 0
    assert view['volumeDiscountCandidate'] is None


def test_zero_percent_tier_can_make_payable_zero_but_is_server_computed(db):
    from app.growth import build_quote, quote_view

    db = base_config(db, volume_policy([{'id': 'free', 'minSheets': 1, 'payRateBps': 0}]))
    prep = make_prep(db)
    view = quote_view(build_quote(db, prep, [line('a', 'f1', 'A4', 1, 500)], actor=None))
    assert view['discountCents'] == 500
    assert view['totalCents'] == 0
    assert view['selectedPromotionType'] == 'VOLUME_TIER'


def test_reserved_coupon_still_applies_after_natural_expiry(db):
    from app.growth import build_quote, quote_view, reserve_coupon

    db = base_config(db, volume_policy([]))
    account = make_account(db)
    campaign = make_campaign(db, amount=300, budget=1000)
    coupon = make_coupon(db, account, campaign, amount=300)
    prep = make_prep(db, account)
    quote = build_quote(db, prep, [line('a', 'f1', 'A4', 1, 1000)], actor=None)
    order = make_order(db, prep, quote, account)
    reserve_coupon(db, order, quote)
    coupon.valid_until = utcnow() - timedelta(seconds=1)
    db.flush()
    revision = make_prep(db, account, replaces_order_id=order.id)
    view = quote_view(build_quote(db, revision, [line('a', 'f1', 'A4', 1, 1000)], actor=None))
    assert view['selectedPromotionType'] == 'COUPON'
    assert view['discountCents'] == 300


def test_another_accounts_coupon_is_never_a_quote_candidate(db):
    from app.growth import build_quote, quote_view

    db = base_config(db, volume_policy([]))
    owner = make_account(db)
    other = make_account(db)
    campaign = make_campaign(db, amount=300, budget=1000)
    coupon = make_coupon(db, owner, campaign, amount=300)
    prep = make_prep(db, other)
    view = quote_view(build_quote(db, prep, [line('a', 'f1', 'A4', 1, 1000)], actor=None))
    assert view['selectedPromotionType'] == 'NONE'
    assert view['discountCents'] == 0


def test_issuance_limit_and_budget_never_oversubscribe(db):
    from app.growth import on_registration_verified

    db = base_config(db, volume_policy([]))
    limited = make_campaign(db, kind='NEW_CUSTOMER', amount=300, budget=300,
                            issuance=1)
    first = make_account(db)
    second = make_account(db)
    assert len(on_registration_verified(db, first)) == 1
    assert on_registration_verified(db, second) == []
    db.refresh(limited)
    assert limited.reserved_liability_cents == 300
    assert limited.reserved_liability_cents + limited.realized_discount_cents <= 300


def test_freezing_reserved_coupon_creates_order_blocker_and_unfreeze_resolves_it(
        admin_client, db, api):
    from app.models import BusinessException
    from app.growth import build_quote, reserve_coupon

    db = base_config(db, volume_policy([]))
    account = make_account(db)
    campaign = make_campaign(db, amount=300, budget=1000)
    coupon = make_coupon(db, account, campaign, amount=300)
    prep = make_prep(db, account)
    quote = build_quote(db, prep, [line('a', 'f1', 'A4', 1, 1000)], actor=None)
    order = make_order(db, prep, quote, account)
    reserve_coupon(db, order, quote)
    db.commit()
    frozen = api(admin_client, f'/admin/promotions/coupons/{coupon.id}/freeze',
                 {'expectedVersion': coupon.version, 'reason': '风控核对'})
    assert frozen.status_code == 200, frozen.text
    blocker = db.scalar(select(BusinessException).where(
        BusinessException.order_id == order.id, BusinessException.status == 'OPEN'))
    assert blocker is not None and blocker.blocks_production is True
    db.refresh(coupon)
    unfrozen = api(admin_client, f'/admin/promotions/coupons/{coupon.id}/unfreeze',
                   {'expectedVersion': coupon.version, 'reason': '核对完成'})
    assert unfrozen.status_code == 200, unfrozen.text
    db.refresh(blocker)
    assert blocker.status == 'RESOLVED'


def test_volume_policy_patch_route_is_reachable_and_versioned(admin_client, api):
    current = api(admin_client, '/admin/config/volume-discounts', method='GET').json()['data']
    payload = {
        'enabled': True, 'effectiveAt': None, 'description': '接口满量',
        'tiers': [{'id': 'route10', 'minSheets': 10, 'payRateBps': 9000}],
        'expectedVersion': current['version'], 'reason': '接口配置',
    }
    updated = api(admin_client, '/admin/config/volume-discounts', payload, method='PATCH')
    assert updated.status_code == 200, updated.text
    assert updated.json()['data']['version'] == current['version'] + 1
    conflict = api(admin_client, '/admin/config/volume-discounts',
                   {**payload, 'expectedVersion': current['version']}, method='PATCH')
    assert conflict.status_code == 409


def test_pending_order_revision_can_release_and_reserve_new_quote_atomically(db):
    from app.growth import (build_quote, release_coupon, reserve_coupon,
                            settle_coupon)

    db = base_config(db, volume_policy([]))
    account = make_account(db)
    campaign = make_campaign(db, amount=300, budget=1000)
    coupon = make_coupon(db, account, campaign, amount=300)
    prep = make_prep(db, account)
    quote = build_quote(db, prep, [line('a', 'f1', 'A4', 1, 1000)], actor=None)
    order = make_order(db, prep, quote, account)
    reserve_coupon(db, order, quote)
    revision = make_prep(db, account, replaces_order_id=order.id)
    new_quote = build_quote(db, revision,
                            [line('b', 'f2', 'A4', 2, 500)], actor=None)
    # Commerce order: release old reservation, reserve new quote, then switch.
    release_coupon(db, order)
    reserve_coupon(db, order, new_quote)
    db.refresh(coupon)
    assert coupon.status == 'RESERVED'
    assert coupon.reserved_order_id == order.id
    order.quote_id = new_quote.id
    order.total_cents = new_quote.total_cents
    settle_coupon(db, order)
    db.refresh(coupon)
    assert coupon.status == 'USED'
    assert coupon.used_order_id == order.id


def test_refund_compensation_does_not_consume_a_stopped_campaign_budget(db):
    from app.growth import build_quote, on_refund, reserve_coupon, settle_coupon

    db = base_config(db, volume_policy([]))
    account = make_account(db)
    campaign = make_campaign(db, amount=500, budget=1000, service='A4', min_spend=100)
    make_coupon(db, account, campaign, amount=500, service='A4', min_spend=100)
    prep = make_prep(db, account)
    quote = build_quote(db, prep, [line('a', 'f1', 'A4', 1, 400)], actor=None)
    order = make_order(db, prep, quote, account, cash=400,
                       settlement_kind='CASH_MANUAL')
    reserve_coupon(db, order, quote)
    settle_coupon(db, order)
    campaign.enabled = False
    db.flush()
    db.refresh(campaign)
    before = (campaign.reserved_liability_cents, campaign.realized_discount_cents)
    order.refunded_cents = 400
    refund = make_refund(db, order, 400, '整单退款')
    compensation = on_refund(db, order, refund)
    db.refresh(campaign)
    assert compensation is not None and compensation.campaign_id is None
    assert (campaign.reserved_liability_cents,
            campaign.realized_discount_cents) == before
    event = db.scalar(select(PromotionEvent).where(
        PromotionEvent.business_key == f'refund-compensation:{order.id}'))
    assert event is not None and event.kind == 'REFUND_COMPENSATION'
    assert event.amount_cents == 400


def test_legacy_explicit_null_coupon_maps_to_no_coupon_but_volume_stays(db):
    from app.growth import build_quote, quote_view
    from app.growth_schemas import PromotionIntent

    intent = PromotionIntent.model_validate({'couponGrantId': None})
    assert intent.selection_mode == 'NO_COUPON'
    db = base_config(db, volume_policy([{'id': 't1', 'minSheets': 1, 'payRateBps': 9000}]))
    account = make_account(db)
    campaign = make_campaign(db, amount=300, budget=1000)
    make_coupon(db, account, campaign, amount=300)
    prep = make_prep(db, account)
    view = quote_view(build_quote(db, prep, [line('a', 'f1', 'A4', 1, 1000)],
                                  actor=None, intent=intent))
    assert view['selectionMode'] == 'NO_COUPON'
    assert view['selectedPromotionType'] == 'VOLUME_TIER'


def test_unbudgeted_campaign_liability_releases_on_settlement(db):
    from app.growth import build_quote, reserve_coupon, settle_coupon

    db = base_config(db, volume_policy([]))
    account = make_account(db)
    campaign = make_campaign(db, amount=300, budget=None)
    make_coupon(db, account, campaign, amount=300)
    prep = make_prep(db, account)
    quote = build_quote(db, prep, [line('a', 'f1', 'A4', 1, 1000)], actor=None)
    order = make_order(db, prep, quote, account)
    reserve_coupon(db, order, quote)
    settle_coupon(db, order)
    db.refresh(campaign)
    assert campaign.reserved_liability_cents == 0
    assert campaign.realized_discount_cents == 300


def test_same_reserved_coupon_revision_keeps_locked_terms_and_updates_amount(db):
    from app.growth import (build_quote, release_coupon, reserve_coupon,
                            settle_coupon)

    db = base_config(db, volume_policy([]))
    account = make_account(db)
    campaign = make_campaign(db, amount=300, budget=1000, service='A4',
                             min_spend=100)
    coupon = make_coupon(db, account, campaign, amount=300, service='A4',
                         min_spend=100)
    prep = make_prep(db, account)
    quote = build_quote(db, prep, [line('a', 'f1', 'A4', 1, 1000)], actor=None)
    order = make_order(db, prep, quote, account)
    reserve_coupon(db, order, quote)
    old_terms = dict(coupon.data['terms'])
    revision = make_prep(db, account, replaces_order_id=order.id)
    # Codex sets order.quote_id before reserve; same coupon remains RESERVED.
    new_quote = build_quote(db, revision, [line('b', 'f2', 'A4', 1, 500)],
                            actor=None)
    order.quote_id = new_quote.id
    reserve_coupon(db, order, new_quote)
    db.refresh(coupon)
    assert coupon.status == 'RESERVED'
    assert coupon.data['terms'] == old_terms
    assert coupon.data['reservation']['quoteId'] == new_quote.id
    assert coupon.data['reservation']['amountCents'] == 300
    assert coupon.data['reservationHistory']
    settle_coupon(db, order)
    db.refresh(coupon)
    assert coupon.status == 'USED'
    assert coupon.data['settlement']['amountCents'] == 300


def test_revision_validates_new_quote_amount_even_when_coupon_already_reserved(db):
    from app.growth import build_quote, reserve_coupon

    db = base_config(db, volume_policy([]))
    account = make_account(db)
    campaign = make_campaign(db, amount=300, budget=1000)
    coupon = make_coupon(db, account, campaign, amount=300)
    prep = make_prep(db, account)
    quote = build_quote(db, prep, [line('a', 'f1', 'A4', 1, 1000)], actor=None)
    order = make_order(db, prep, quote, account)
    reserve_coupon(db, order, quote)
    revision = make_prep(db, account, replaces_order_id=order.id)
    new_quote = build_quote(db, revision, [line('b', 'f2', 'A4', 1, 1000)],
                            actor=None)
    view = dict(new_quote.data)
    view['selectedEligibleSubtotalCents'] = 999
    new_quote.data = view
    order.quote_id = new_quote.id
    with pytest.raises(Exception) as error:
        reserve_coupon(db, order, new_quote)
    assert getattr(error.value, 'code', '') == 'COUPON_REQUIRES_REQUOTE'


def test_frozen_same_order_reserved_coupon_blocks_revision_reserve(db):
    from app.growth import build_quote, reserve_coupon
    from app.core import DomainError

    db = base_config(db, volume_policy([]))
    account = make_account(db)
    campaign = make_campaign(db, amount=300, budget=1000)
    coupon = make_coupon(db, account, campaign, amount=300)
    prep = make_prep(db, account)
    quote = build_quote(db, prep, [line('a', 'f1', 'A4', 1, 1000)], actor=None)
    order = make_order(db, prep, quote, account)
    reserve_coupon(db, order, quote)
    revision = make_prep(db, account, replaces_order_id=order.id)
    new_quote = build_quote(db, revision, [line('b', 'f2', 'A4', 1, 1000)],
                            actor=None)
    coupon.risk_hold = 'FROZEN'
    db.flush()
    order.quote_id = new_quote.id
    with pytest.raises(DomainError) as error:
        reserve_coupon(db, order, new_quote)
    assert error.value.code == 'COUPON_RISK_HOLD'


def test_naturally_expired_same_order_reserved_coupon_revision_still_reserves(db):
    from app.growth import build_quote, reserve_coupon

    db = base_config(db, volume_policy([]))
    account = make_account(db)
    campaign = make_campaign(db, amount=300, budget=1000)
    coupon = make_coupon(db, account, campaign, amount=300)
    prep = make_prep(db, account)
    quote = build_quote(db, prep, [line('a', 'f1', 'A4', 1, 1000)], actor=None)
    order = make_order(db, prep, quote, account)
    reserve_coupon(db, order, quote)
    coupon.valid_until = utcnow() - timedelta(seconds=1)
    db.flush()
    revision = make_prep(db, account, replaces_order_id=order.id)
    new_quote = build_quote(db, revision, [line('b', 'f2', 'A4', 1, 1000)],
                            actor=None)
    order.quote_id = new_quote.id
    reserved = reserve_coupon(db, order, new_quote)
    assert reserved is not None and reserved.status == 'RESERVED'


def test_zero_payable_full_cancellation_still_issues_compensation_for_used_coupon(db):
    from app.growth import (build_quote, on_refund, reserve_coupon,
                            settle_coupon)

    db = base_config(db, volume_policy([]))
    account = make_account(db)
    campaign = make_campaign(db, amount=400, budget=1000, service='A4',
                             min_spend=100)
    coupon = make_coupon(db, account, campaign, amount=400, service='A4',
                         min_spend=100)
    prep = make_prep(db, account)
    quote = build_quote(db, prep, [line('a', 'f1', 'A4', 1, 400)], actor=None)
    assert quote.total_cents == 0
    order = make_order(db, prep, quote, account, cash=0,
                       settlement_kind='ZERO_PAYABLE')
    reserve_coupon(db, order, quote)
    settle_coupon(db, order)
    db.refresh(coupon)
    assert coupon.status == 'USED'
    # No cash is returned, but the full-order cancellation is still recorded.
    refund = make_refund(db, order, 0, '零元结算整单取消')
    compensation = on_refund(db, order, refund)
    assert compensation is not None
    assert compensation.amount_cents == 400
    assert compensation.campaign_id is None
    assert compensation.status == 'AVAILABLE'
    assert compensation.data['terms']['service'] == 'A4'
    assert compensation.data['terms']['minSpendCents'] == 100
    remaining = compensation.valid_until - utcnow()
    assert timedelta(days=6, hours=23) < remaining <= timedelta(days=7)
    db.refresh(coupon)
    assert coupon.status == 'USED'
    assert coupon.used_order_id == order.id
    assert order.refunded_cents == 0
    on_refund(db, order, refund)
    assert len(db.scalars(select(Coupon).where(
        Coupon.source_key.like(f'REFUND_COMPENSATION:{order.id}%'))).all()) == 1


def _referral_with_a4_reward(db, inviter, invitee, minimum=100):
    campaign = make_campaign(db, kind='REFERRER', amount=200, budget=1000,
                             qualification={'qualificationMinSpendCents': minimum,
                                            'qualificationService': 'A4'})
    relation = Referral(
        inviter_id=inviter.id, invitee_id=invitee.id,
        status='WAITING_FIRST_ORDER',
        data={'rewardCampaigns': [campaign_snapshot(campaign)],
              'qualificationMinSpendCents': minimum,
              'qualificationService': 'A4',
              'qualificationExpiresAt': None},
    )
    db.add(relation)
    db.flush()
    return relation, campaign


def test_refund_of_all_a4_does_not_keep_a4_referral_qualification(db):
    from app.growth import build_quote, on_refund, qualify_referral

    db = base_config(db, volume_policy([]))
    inviter = make_account(db)
    invitee = make_account(db)
    relation, campaign = _referral_with_a4_reward(db, inviter, invitee)
    prep = make_prep(db, invitee)
    quote = build_quote(db, prep, [
        line('a4', 'f1', 'A4', 1, 1000),
        line('photo', 'f2', 'PHOTO', 1, 2000),
    ], actor=None)
    order = make_order(db, prep, quote, invitee, status='DELIVERED', cash=3000,
                       settlement_kind='CASH_MANUAL')
    assert qualify_referral(db, order) is True
    reward = db.scalar(select(Coupon).where(
        Coupon.source_key == f'REFERRAL_REWARD:{relation.id}:{campaign.id}'))
    assert reward.status == 'AVAILABLE'
    order.refunded_cents = 1000
    refund = make_refund(db, order, 1000, 'A4 全退，照片保留',
                         affected=[{'lineKey': 'a4', 'quantity': 1}])
    on_refund(db, order, refund)
    db.refresh(relation)
    db.refresh(reward)
    assert relation.status == 'DISQUALIFIED'
    assert reward.status == 'REVOKED'
    assert relation.data['refundReview']['remainingEligibleCents'] == 0
    assert relation.data['refundReview']['netPaidCents'] == 2000
    assert relation.qualified_order_id == order.id


def test_refund_of_non_qualifying_service_keeps_a4_referral_qualification(db):
    from app.growth import build_quote, on_refund, qualify_referral

    db = base_config(db, volume_policy([]))
    inviter = make_account(db)
    invitee = make_account(db)
    relation, campaign = _referral_with_a4_reward(db, inviter, invitee)
    prep = make_prep(db, invitee)
    quote = build_quote(db, prep, [
        line('a4', 'f1', 'A4', 1, 1000),
        line('photo', 'f2', 'PHOTO', 1, 2000),
    ], actor=None)
    order = make_order(db, prep, quote, invitee, status='DELIVERED', cash=3000,
                       settlement_kind='CASH_MANUAL')
    assert qualify_referral(db, order) is True
    reward = db.scalar(select(Coupon).where(
        Coupon.source_key == f'REFERRAL_REWARD:{relation.id}:{campaign.id}'))
    order.refunded_cents = 500
    refund = make_refund(db, order, 500, '仅退照片',
                         affected=[{'lineKey': 'photo', 'quantity': 1}])
    on_refund(db, order, refund)
    db.refresh(relation)
    db.refresh(reward)
    assert relation.status == 'GRANTED'
    assert reward.status == 'AVAILABLE'
    assert relation.data['refundReview']['remainingEligibleCents'] == 1000


def test_used_referral_reward_on_another_paid_order_goes_to_manual_review(db):
    from app.growth import build_quote, on_refund, qualify_referral
    from app.models import BusinessException

    db = base_config(db, volume_policy([]))
    inviter = make_account(db)
    invitee = make_account(db)
    relation, campaign = _referral_with_a4_reward(db, inviter, invitee)
    prep = make_prep(db, invitee)
    quote = build_quote(db, prep, [line('a4', 'f1', 'A4', 1, 1000)], actor=None)
    order = make_order(db, prep, quote, invitee, status='DELIVERED', cash=1000,
                       settlement_kind='CASH_MANUAL')
    assert qualify_referral(db, order) is True
    reward = db.scalar(select(Coupon).where(
        Coupon.source_key == f'REFERRAL_REWARD:{relation.id}:{campaign.id}'))
    inviter_prep = make_prep(db, inviter)
    inviter_quote = build_quote(db, inviter_prep,
                                [line('b', 'f2', 'A4', 1, 2000)], actor=None)
    inviter_order = make_order(db, inviter_prep, inviter_quote, inviter,
                               cash=2000, settlement_kind='CASH_MANUAL')
    other_total = inviter_order.total_cents
    reward.status = 'USED'
    reward.used_order_id = inviter_order.id
    db.flush()
    order.refunded_cents = 1000
    refund = make_refund(db, order, 1000, '整单撤销',
                         affected=[{'lineKey': 'a4', 'quantity': 1}])
    on_refund(db, order, refund)
    db.refresh(relation)
    db.refresh(reward)
    db.refresh(inviter_order)
    assert relation.status == 'REVERSAL_REVIEW'
    assert reward.status == 'USED'
    assert reward.risk_hold == 'FROZEN'
    assert inviter_order.total_cents == other_total
    blocker = db.scalar(select(BusinessException).where(
        BusinessException.order_id == inviter_order.id,
        BusinessException.type == 'REFERRAL_REWARD_REVERSAL_REVIEW',
        BusinessException.status == 'OPEN'))
    assert blocker is not None
