"""N-06 员工整单折扣：等级、资格、择优、快照和失效边界。"""

from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core import Actor, DomainError, token_hash
from app.models import (
    Account,
    AfterSale,
    AuthSession,
    Campaign,
    ConfigSection,
    Coupon,
    DeliverySlot,
    Location,
    Order,
    Preparation,
    Principal,
    Refund,
    Staff,
    utcnow,
)

APP_MODULES = ['benefits', 'growth', 'mail', 'auth']

GRADES = [2000, 1500, 100, 10000, 2500, 3333]
LINE_VALUES = (101, 700, 200)  # a4 / binding / photo，合计 1001


def uid(prefix):
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


def base_config(db, grades=None, volume=None):
    config(db, 'prices', {'bwSingle': 100, 'bwDuplex': 180, 'colorSingle': 200,
                          'colorDuplex': 360, 'photo': 500, 'corner': 0, 'side': 0})
    config(db, 'workflow', {'quoteMinutes': 15})
    if grades is not None:
        config(db, 'benefits', {'id': 'benefits-policy',
                                'description': '员工打印折扣', 'grades': grades})
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


def whole_order_lines(a4=101, binding=700, photo=200):
    return [
        line('a4', 'a4-related', 'A4', a4, 1),
        line('bind', 'bind-related', 'BINDING', binding, 1, 'BOOKLET'),
        line('photo', 'photo-related', 'PHOTO', photo, 1),
    ]


def make_campaign(db, amount=500, min_spend=0, service='ALL'):
    campaign = Campaign(
        name=uid('活动'), kind='NEW_CUSTOMER', enabled=True, budget_cents=None,
        reserved_liability_cents=0, realized_discount_cents=0,
        data={
            'amountCents': amount, 'minSpendCents': min_spend, 'service': service,
            'validityDays': 30, 'perAccountLimit': 1,
            'issuanceLimit': None, 'startsAt': None, 'endsAt': None,
            'description': '测试活动', 'stackable': False,
        },
    )
    db.add(campaign)
    db.flush()
    return campaign


def make_coupon(db, account, amount=500, min_spend=0, service='ALL', status='AVAILABLE'):
    campaign = make_campaign(db, amount=amount, min_spend=min_spend, service=service)
    coupon = Coupon(
        account_id=account.id, campaign_id=campaign.id,
        amount_cents=amount, status=status, risk_hold='NONE',
        source_key=uid('coupon'), valid_until=utcnow() + timedelta(days=30),
        data={
            'terms': {
                'amountCents': amount, 'minSpendCents': min_spend,
                'service': service, 'campaignId': campaign.id,
                'name': campaign.name, 'campaignVersion': campaign.version,
            },
            'liabilityReserved': False,
        },
    )
    db.add(coupon)
    db.flush()
    return coupon


def make_staff(db, account=None, level=None, superadmin=False, enabled=True,
               status='ACTIVE', capabilities=None):
    staff = Staff(
        username=f'staff-{uuid4().hex[:12]}', name='测试员工', password_hash='x',
        role='ADMIN', enabled=enabled, status=status,
        account_id=account.id if account else None,
        is_super_admin=superadmin, discount_level=level,
        capabilities=list(capabilities or []),
        must_change_password=False,
    )
    db.add(staff)
    db.flush()
    return staff


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
        pickup_code=uid('pick'), status=status, preparation_id=prep.id,
        quote_id=quote.id, total_cents=quote.total_cents,
        cash_received_cents=cash, refunded_cents=refunded,
        settlement_kind=settlement_kind, nickname='测试同学', phone='13800000000',
        location_id=location.id, requested_slot_id=slot.id,
        close_at=utcnow() + timedelta(hours=2),
        data={'quote': quote_view(quote)},
    )
    db.add(order)
    db.flush()
    return order


def make_refund(db, order, amount, reason='退款'):
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
                    data={'affectedItems': []})
    db.add(refund)
    db.flush()
    return refund


def staff_client(client_factory, db, staff):
    """Create the unified customer-account session that carries staff authority."""
    account = db.get(Account, staff.account_id) if staff.account_id else make_account(db)
    if staff.account_id is None:
        staff.account_id = account.id
    token = f'test-{uuid4().hex}'
    db.add(AuthSession(kind='CUSTOMER', account_id=account.id,
                       token_hash=token_hash(token),
                       expires_at=utcnow() + timedelta(days=7)))
    db.commit()
    client = client_factory()
    client.cookies.set('dormprint_account_session', token)
    client.staff = staff
    return client


@pytest.fixture
def superadmin_client(client_factory, staff_factory, db):
    return staff_client(client_factory, db, staff_factory(is_super_admin=True))


def quote_of(db, prep, lines=None, intent=None, actor=None):
    from app.growth import build_quote, quote_view

    quote = build_quote(db, prep, lines or whole_order_lines(), actor=actor, intent=intent)
    return quote, quote_view(quote)


def assert_no_staff_grade(view):
    assert view['selectedPromotionType'] == 'NONE'
    assert view['discountCents'] == 0
    assert 'staffDiscountCandidate' not in view


def test_all_six_grades_discount_whole_order_with_exact_allocations(db):
    base_config(db, grades=GRADES)
    cases = [
        (1, 2000, 200, [21, 140, 39]),
        (2, 1500, 150, [16, 105, 29]),
        (3, 100, 10, [2, 7, 1]),
        (4, 10000, 1001, [101, 700, 200]),
        (5, 2500, 250, [26, 175, 49]),
        (6, 3333, 334, [34, 234, 66]),
    ]
    for level, reduction, discount, allocations in cases:
        account = make_account(db)
        staff = make_staff(db, account=account, level=level)
        prep = make_prep(db, account)
        quote, view = quote_of(db, prep)

        assert view['selectedPromotionType'] == 'STAFF_GRADE'
        assert view['subtotalCents'] == 1001
        assert view['discountCents'] == discount
        assert view['totalCents'] == 1001 - discount
        assert view['selectedEligibleSubtotalCents'] == 1001
        assert len(view['discounts']) == 1
        applied = view['discounts'][0]
        assert applied['kind'] == 'STAFF_GRADE'
        assert applied['source'] == 'STAFF_GRADE'
        assert applied['discountLevel'] == level
        assert applied['amountCents'] == discount

        candidate = view['staffDiscountCandidate']
        assert candidate['source'] == 'STAFF_GRADE'
        assert candidate['staffId'] == staff.id
        assert candidate['discountLevel'] == level
        assert candidate['reductionBps'] == reduction
        assert candidate['payRateBps'] == 10000 - reduction
        assert candidate['discountCents'] == discount
        assert candidate['policyId'] == 'benefits-policy'
        assert candidate['policyVersion'] == 1
        assert candidate['computedAt']

        got = [item['amountCents'] for item in view['discountAllocations']]
        assert got == allocations
        assert sum(got) == discount
        assert [item['lineKey'] for item in view['discountAllocations']] == [
            'a4', 'bind', 'photo']

        from app.growth_schemas import QuoteDTO
        QuoteDTO.model_validate(view)
        assert quote.data['selectedPromotionType'] == 'STAFF_GRADE'
        raw = quote.data['staffDiscountCandidate']
        assert raw['staffId'] == candidate['staffId']
        assert raw['discountCents'] == candidate['discountCents']
        assert raw['policyVersion'] == candidate['policyVersion']


def test_zero_reduction_grade_is_not_a_benefit(db):
    base_config(db, grades=[0, None, None, None, None, None])
    account = make_account(db)
    make_staff(db, account=account, level=1)
    _, view = quote_of(db, make_prep(db, account))
    assert_no_staff_grade(view)


def test_ineligible_staff_never_gets_a_grade(db):
    base_config(db, grades=GRADES)

    account = make_account(db)
    make_staff(db, account=None, level=1)
    assert_no_staff_grade(quote_of(db, make_prep(db, account))[1])

    account = make_account(db)
    make_staff(db, account=account, level=1, enabled=False)
    assert_no_staff_grade(quote_of(db, make_prep(db, account))[1])

    account = make_account(db)
    make_staff(db, account=account, level=1, enabled=False, status='DISABLED')
    assert_no_staff_grade(quote_of(db, make_prep(db, account))[1])

    account = make_account(db)
    make_staff(db, account=account, level=1, status='PENDING_SETUP')
    assert_no_staff_grade(quote_of(db, make_prep(db, account))[1])

    account = make_account(db, status='SUSPENDED')
    make_staff(db, account=account, level=1)
    assert_no_staff_grade(quote_of(db, make_prep(db, account))[1])

    account = make_account(db)
    make_staff(db, account=account, level=1)
    account.deleted_at = utcnow()
    db.flush()
    assert_no_staff_grade(quote_of(db, make_prep(db, account))[1])

    account = make_account(db)
    make_staff(db, account=account, superadmin=True, enabled=False, status='DISABLED')
    assert_no_staff_grade(quote_of(db, make_prep(db, account))[1])


def test_lookup_uses_preparation_account_not_actor_staff_claims(db):
    base_config(db, grades=GRADES)
    staff_account = make_account(db)
    staff = make_staff(db, account=staff_account, level=1)
    other_account = make_account(db)

    _, view = quote_of(
        db, make_prep(db, other_account),
        actor=Actor(account_id=other_account.id, staff_id=staff.id,
                    staff_role='ADMIN', capabilities={'*'}),
    )
    assert_no_staff_grade(view)

    _, view = quote_of(
        db, make_prep(db, staff_account),
        actor=Actor(account_id=other_account.id),
    )
    assert view['selectedPromotionType'] == 'STAFF_GRADE'
    assert view['staffDiscountCandidate']['staffId'] == staff.id

    guest = make_prep(db, None)
    _, view = quote_of(
        db, guest,
        actor=Actor(staff_id=staff.id, staff_role='ADMIN', capabilities={'*'}),
    )
    assert_no_staff_grade(view)

    from app.growth_schemas import PromotionIntent
    with pytest.raises(ValidationError):
        PromotionIntent.model_validate({'selectionMode': 'AUTO', 'staffDiscountLevel': 6})


def test_superadmin_is_free_and_beats_equal_volume_and_coupon(db):
    base_config(db, grades=None,
                volume=volume_policy([{'id': 'v100', 'minSheets': 1, 'payRateBps': 0}]))
    account = make_account(db)
    staff = make_staff(db, account=account, superadmin=True)
    coupon = make_coupon(db, account, amount=10000, min_spend=0, service='ALL')
    lines = [line('a4', 'a4-related', 'A4', 1000, 1)]
    _, view = quote_of(db, make_prep(db, account), lines=lines)

    assert view['selectedPromotionType'] == 'STAFF_GRADE'
    assert view['discountCents'] == 1000
    assert view['totalCents'] == 0
    candidate = view['staffDiscountCandidate']
    assert candidate['source'] == 'SUPER_ADMIN'
    assert candidate['staffId'] == staff.id
    assert candidate['discountLevel'] is None
    assert candidate['reductionBps'] == 10000
    assert candidate['payRateBps'] == 0
    assert candidate['discountCents'] == 1000
    assert view['discounts'][0]['source'] == 'SUPER_ADMIN'
    assert view['volumeDiscountCandidate']['discountCents'] == 1000
    assert db.get(Coupon, coupon.id).status == 'AVAILABLE'
    db.refresh(coupon)
    assert coupon.status == 'AVAILABLE'


def test_best_of_staff_coupon_and_volume(db):
    base_config(db, grades=[3000, 2000, 1000, 10000, 2500, 3333])
    account = make_account(db)
    make_staff(db, account=account, level=1)
    lines = whole_order_lines(a4=1000, binding=100, photo=400)
    coupon = make_coupon(db, account, amount=300)

    _, view = quote_of(db, make_prep(db, account), lines=lines)
    assert view['selectedPromotionType'] == 'STAFF_GRADE'
    assert view['discountCents'] == 450
    assert view['staffDiscountCandidate']['discountLevel'] == 1

    coupon.amount_cents = 500
    coupon.data['terms']['amountCents'] = 500
    db.flush()
    _, view = quote_of(db, make_prep(db, account), lines=lines)
    assert view['selectedPromotionType'] == 'COUPON'
    assert view['discountCents'] == 500
    assert view['staffDiscountCandidate']['discountCents'] == 450


def test_ties_prefer_staff_over_coupon_and_volume_over_ordinary_staff(db):
    base_config(db, grades=[2000, 1000, None, None, None, None])
    account = make_account(db)
    make_staff(db, account=account, level=1)
    lines = whole_order_lines(a4=1000, binding=100, photo=400)

    coupon = make_coupon(db, account, amount=300)
    _, view = quote_of(db, make_prep(db, account), lines=lines)
    assert view['selectedPromotionType'] == 'STAFF_GRADE'
    assert view['discountCents'] == 300
    coupon.status = 'USED'
    db.flush()

    config(db, 'benefits', {'id': 'benefits-policy', 'description': '员工打印折扣',
                            'grades': [1000, 2000, None, None, None, None]}, version=2)
    config(db, 'volume-discounts',
           volume_policy([{'id': 'v15', 'minSheets': 1, 'payRateBps': 8500}]))
    _, view = quote_of(db, make_prep(db, account), lines=lines)
    assert view['discountCents'] == 150
    assert view['selectedPromotionType'] == 'VOLUME_TIER'
    assert view['staffDiscountCandidate']['discountCents'] == 150


def test_no_coupon_and_specific_coupon_still_compare_all_sources(db):
    base_config(db, grades=[3000, None, None, None, None, None])
    account = make_account(db)
    make_staff(db, account=account, level=1)
    lines = whole_order_lines(a4=1000, binding=100, photo=400)
    coupon = make_coupon(db, account, amount=300)

    from app.growth_schemas import PromotionIntent
    _, view = quote_of(
        db, make_prep(db, account), lines=lines,
        intent=PromotionIntent(selection_mode='NO_COUPON'),
    )
    assert view['selectedPromotionType'] == 'STAFF_GRADE'
    assert view['discountCents'] == 450

    _, view = quote_of(
        db, make_prep(db, account), lines=lines,
        intent=PromotionIntent(selection_mode='SPECIFIC_COUPON',
                               coupon_grant_id=coupon.id),
    )
    assert view['selectedPromotionType'] == 'STAFF_GRADE'
    assert view['discountCents'] == 450
    assert db.get(Coupon, coupon.id).status == 'AVAILABLE'


def test_quote_without_staff_keeps_legacy_payload_shape(db):
    base_config(db, volume=volume_policy([
        {'id': 't1', 'minSheets': 10, 'payRateBps': 9000}]))
    account = make_account(db)
    make_staff(db, account=None, level=1)
    prep = make_prep(db, account)
    quote, view = quote_of(
        db, prep, lines=[line('a4', 'a4-related', 'A4', 10, 100)])

    expected = {
        'id', 'priceVersion', 'expiresAt', 'lines', 'subtotalCents',
        'discountCents', 'totalCents', 'deliveryFeeCents', 'checkoutAccountId',
        'selectedEligibleSubtotalCents', 'promotionRuleVersion', 'selectionMode',
        'selectedPromotionType', 'volumeDiscountCandidate', 'discounts',
        'discountAllocations',
    }
    assert set(view) == expected
    assert 'staffDiscountCandidate' not in view
    assert view['selectedPromotionType'] == 'VOLUME_TIER'
    assert view['promotionRuleVersion'] == 'volume-1'
    assert view['discountCents'] == 100

    from app.growth import quote_view
    from app.growth_schemas import QuoteDTO
    QuoteDTO.model_validate(quote_view(quote))
    quote.data.pop('staffDiscountCandidate', None)
    assert 'staffDiscountCandidate' not in quote_view(quote)


def test_validate_quote_entitlement_expires_unsubmitted_quote_on_changes(db):
    from app.benefits import validate_quote_entitlement

    base_config(db, grades=GRADES)
    account = make_account(db)
    staff = make_staff(db, account=account, level=1)
    prep = make_prep(db, account)
    quote, view = quote_of(db, prep)
    assert view['selectedPromotionType'] == 'STAFF_GRADE'
    assert validate_quote_entitlement(db, prep, quote) is None

    same = list(GRADES)
    config(db, 'benefits', {'id': 'benefits-policy', 'description': '员工打印折扣',
                            'grades': same}, version=2)
    with pytest.raises(DomainError) as caught:
        validate_quote_entitlement(db, prep, quote)
    assert caught.value.code == 'QUOTE_EXPIRED'
    assert caught.value.status == 409

    quote, _ = quote_of(db, prep)
    changed = list(GRADES)
    changed[0] = 2500
    config(db, 'benefits', {'id': 'benefits-policy', 'description': '员工打印折扣',
                            'grades': changed}, version=3)
    with pytest.raises(DomainError):
        validate_quote_entitlement(db, prep, quote)

    config(db, 'benefits', {'id': 'benefits-policy', 'description': '员工打印折扣',
                            'grades': GRADES}, version=4)
    quote, _ = quote_of(db, prep)
    staff.discount_level = 2
    db.flush()
    with pytest.raises(DomainError):
        validate_quote_entitlement(db, prep, quote)

    staff.discount_level = 1
    db.flush()
    quote, _ = quote_of(db, prep)
    staff.enabled = False
    db.flush()
    with pytest.raises(DomainError):
        validate_quote_entitlement(db, prep, quote)

    staff.enabled = True
    db.flush()
    quote, _ = quote_of(db, prep)
    staff.account_id = None
    db.flush()
    with pytest.raises(DomainError):
        validate_quote_entitlement(db, prep, quote)

    staff.account_id = account.id
    db.flush()
    quote, _ = quote_of(db, prep)
    account.status = 'SUSPENDED'
    db.flush()
    with pytest.raises(DomainError):
        validate_quote_entitlement(db, prep, quote)


def test_submitted_original_quote_stays_locked_after_staff_revocation(db):
    from app.benefits import validate_quote_entitlement

    base_config(db, grades=GRADES)
    account = make_account(db)
    staff = make_staff(db, account=account, level=1)
    prep = make_prep(db, account)
    quote, view = quote_of(db, prep)
    order = make_order(db, prep, quote, account=account,
                       settlement_kind='ZERO_PAYABLE')
    snapshot = dict(quote.data)

    staff.enabled = False
    staff.status = 'DISABLED'
    db.flush()
    config(db, 'benefits', {'id': 'benefits-policy', 'description': '员工打印折扣',
                            'grades': [2500] + GRADES[1:]}, version=2)

    assert validate_quote_entitlement(db, prep, quote) is None
    assert quote.data == snapshot
    assert quote.data['selectedPromotionType'] == 'STAFF_GRADE'
    assert order.quote_id == quote.id


def test_full_refund_of_staff_quote_does_not_issue_employee_voucher(db):
    from app.growth import on_refund

    base_config(db, grades=[10000, None, None, None, None, None])
    account = make_account(db)
    make_staff(db, account=account, level=1)
    prep = make_prep(db, account)
    quote, view = quote_of(db, prep)
    assert view['totalCents'] == 0
    order = make_order(db, prep, quote, account=account, status='PENDING_CONFIRMATION',
                       cash=0, refunded=0, settlement_kind='ZERO_PAYABLE')
    refund = make_refund(db, order, 0)
    assert on_refund(db, order, refund) is None
    assert db.scalar(select(Coupon).where(Coupon.account_id == account.id)) is None


def test_benefits_config_route_is_superadmin_only_and_records_six_values(
        db, superadmin_client, client_factory, api):
    non_super = make_staff(db, capabilities=['*'])
    admin_client = staff_client(client_factory, db, non_super)
    response = api(superadmin_client, '/admin/config/benefits', method='GET')
    assert response.status_code == 200
    data = response.json()['data']
    assert data['version'] == 1
    assert data['grades'] == [None, None, None, None, None, None]

    response = api(
        superadmin_client, '/admin/config/benefits',
        {'expectedVersion': 1, 'grades': GRADES, 'reason': '配置六档'},
        method='PATCH',
    )
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['version'] == 2
    assert data['grades'] == GRADES

    db.expire_all()
    row = db.scalar(select(ConfigSection).where(ConfigSection.section == 'benefits'))
    assert row.data['grades'] == GRADES

    assert api(admin_client, '/admin/config/benefits', method='GET').status_code == 403
    assert api(admin_client, '/admin/config/benefits',
               {'expectedVersion': 2, 'grades': GRADES}, method='PATCH').status_code == 403

    for bad in ([2000] * 5, [10001] + GRADES[1:], [-1] + GRADES[1:]):
        response = api(superadmin_client, '/admin/config/benefits',
                       {'expectedVersion': 2, 'grades': bad}, method='PATCH')
        assert response.status_code == 422

    response = api(superadmin_client, '/admin/config/benefits',
                   {'expectedVersion': 1, 'grades': GRADES}, method='PATCH')
    assert response.status_code == 409


def test_disabled_superadmin_cannot_manage_benefits(db, client_factory, api):
    base_config(db)
    account = make_account(db)
    staff = make_staff(db, account=account, superadmin=True, enabled=False,
                       status='DISABLED')
    client = staff_client(client_factory, db, staff)
    response = api(client, '/admin/config/benefits', method='GET')
    assert response.status_code in (401, 403)


def test_revision_quote_keeps_checkout_identity_and_staff_snapshot(db):
    base_config(db, grades=GRADES)
    account = make_account(db)
    staff = make_staff(db, account=account, level=1)
    first_prep = make_prep(db, account)
    first_quote, first_view = quote_of(db, first_prep)
    order = make_order(db, first_prep, first_quote, account=account)

    revision = make_prep(db, account, replaces_order_id=order.id)
    revision_quote, revision_view = quote_of(db, revision)
    assert revision_view['checkoutAccountId'] == account.id
    assert revision_view['staffDiscountCandidate']['staffId'] == staff.id
    assert revision_view['staffDiscountCandidate']['discountLevel'] == 1
    assert revision_view['discountCents'] == first_view['discountCents']


@pytest.mark.parametrize('capabilities', [['customer.manage'], ['*']])
def test_customer_suspend_cannot_freeze_last_linked_super_admin(
        db, client_factory, api, capabilities):
    """The customer admin route must not bypass the auth last-superadmin guard."""
    account = make_account(db)
    superadmin = make_staff(db, account=account, superadmin=True)
    operator = make_staff(db, capabilities=capabilities)
    client = staff_client(client_factory, db, operator)

    response = api(client, f'/admin/customers/{account.id}/suspend', {
        'expectedVersion': account.version,
        'reason': 'would remove the only active super admin',
    })
    assert response.status_code == 409, response.text
    error = response.json()['error']
    assert error['code'] == 'LAST_SUPER_ADMIN_PROTECTED'
    assert error['details']['staffId'] == superadmin.id

    db.expire_all()
    assert db.get(Account, account.id).status == 'ACTIVE'
    assert db.get(Staff, superadmin.id).enabled is True
    assert db.get(Staff, superadmin.id).status == 'ACTIVE'


def test_customer_suspend_allows_ordinary_staff_and_restore_keeps_row(
        db, client_factory, api):
    account = make_account(db)
    linked = make_staff(db, account=account, superadmin=False,
                        capabilities=['*'])
    operator = make_staff(db, capabilities=['customer.manage'])
    client = staff_client(client_factory, db, operator)

    before = (linked.enabled, linked.status, linked.is_super_admin,
              list(linked.capabilities))
    suspended = api(client, f'/admin/customers/{account.id}/suspend', {
        'expectedVersion': account.version, 'reason': 'temporary suspension',
    })
    assert suspended.status_code == 200, suspended.text
    assert suspended.json()['data']['status'] == 'SUSPENDED'

    db.expire_all()
    assert db.get(Account, account.id).status == 'SUSPENDED'
    row = db.get(Staff, linked.id)
    assert (row.enabled, row.status, row.is_super_admin,
            list(row.capabilities)) == before

    restored = api(client, f'/admin/customers/{account.id}/restore', {
        'expectedVersion': suspended.json()['data']['version'],
        'reason': 'restore the same existing grant',
    })
    assert restored.status_code == 200, restored.text
    assert restored.json()['data']['status'] == 'ACTIVE'

    db.expire_all()
    row = db.get(Staff, linked.id)
    assert (row.enabled, row.status, row.is_super_admin,
            list(row.capabilities)) == before
    assert row.account_id == account.id


def test_unsubmitted_quote_expires_when_superadmin_entitlement_appears(db):
    from app.benefits import validate_quote_entitlement

    base_config(db, grades=GRADES)
    account = make_account(db)
    staff = make_staff(db, account=None, level=None, superadmin=False)
    prep = make_prep(db, account)
    quote, view = quote_of(db, prep)
    assert_no_staff_grade(view)

    staff.account_id = account.id
    staff.is_super_admin = True
    db.flush()

    with pytest.raises(DomainError) as caught:
        validate_quote_entitlement(db, prep, quote)
    assert caught.value.code == 'QUOTE_EXPIRED'
    assert caught.value.status == 409

    fresh, fresh_view = quote_of(db, prep)
    assert fresh_view['selectedPromotionType'] == 'STAFF_GRADE'
    assert fresh_view['staffDiscountCandidate']['source'] == 'SUPER_ADMIN'
    assert validate_quote_entitlement(db, prep, fresh) is None


def test_superadmin_free_quote_needs_no_benefits_policy_row(db):
    base_config(db)
    account = make_account(db)
    make_staff(db, account=account, superadmin=True)
    prep = make_prep(db, account)

    _, view = quote_of(db, prep)
    assert view['selectedPromotionType'] == 'STAFF_GRADE'
    assert view['totalCents'] == 0
    assert view['staffDiscountCandidate']['source'] == 'SUPER_ADMIN'
    assert view['staffDiscountCandidate']['reductionBps'] == 10000
    assert view['staffDiscountCandidate']['policyVersion'] == 1
    assert db.scalar(select(ConfigSection).where(
        ConfigSection.section == 'benefits')) is None


def test_submitted_quote_without_candidate_stays_locked_after_grant(db):
    from app.benefits import validate_quote_entitlement

    base_config(db, grades=GRADES)
    account = make_account(db)
    staff = make_staff(db, account=None, level=None, superadmin=False)
    prep = make_prep(db, account)
    quote, view = quote_of(db, prep)
    assert_no_staff_grade(view)
    order = make_order(db, prep, quote, account=account)
    snapshot = dict(quote.data)

    staff.account_id = account.id
    staff.is_super_admin = True
    db.flush()

    assert validate_quote_entitlement(db, prep, quote) is None
    assert quote.data == snapshot
    assert order.quote_id == quote.id
