"""Manual coupon issuance: recipient lookup, limits, records and refund policy."""
from __future__ import annotations

from datetime import timedelta
from urllib.parse import quote

from sqlalchemy import func, select

from app.models import Coupon, PromotionEvent, utcnow

APP_MODULES = ['growth', 'mail', 'auth']


def body(email: str, **overrides):
    payload = {
        'email': email,
        'amountCents': 500,
        'minSpendCents': 0,
        'service': 'A4',
        'validUntil': (utcnow() + timedelta(days=7)).isoformat(),
        'name': '测试人工券',
        'reason': '客服补偿测试',
        'notify': False,
    }
    payload.update(overrides)
    return payload


def error_code(response):
    return response.json().get('error', {}).get('code')


def test_recipient_lookup_and_manual_issue(admin_client, account_factory, db, api):
    account = account_factory(display_name='收券同学')
    found = api(admin_client,
                f'/admin/promotions/coupons/recipient?email={quote(account.email)}',
                method='GET')
    assert found.status_code == 200, found.text
    recipient = found.json()['data']
    assert recipient['accountId'] == account.id
    assert recipient['eligible'] is True
    assert recipient['availableCouponCount'] == 0
    assert '***@' in recipient['emailMasked']

    response = api(admin_client, '/admin/promotions/coupons/manual', body(account.email))
    assert response.status_code == 200, response.text
    result = response.json()['data']
    coupon = result['coupon']
    assert result['replayed'] is False
    assert result['notified'] is False
    assert coupon['campaignId'] == 'MANUAL'
    assert coupon['status'] == 'AVAILABLE'
    assert coupon['amountCents'] == 500
    assert coupon['service'] == 'A4'

    db.expire_all()
    stored = db.get(Coupon, coupon['id'])
    assert stored.campaign_id is None
    assert stored.source_key.startswith('MANUAL_ISSUE:')
    assert stored.data['manualIssue'] is True
    assert stored.data['terms']['campaignId'] == 'MANUAL'
    assert stored.data['terms']['name'] == '测试人工券'
    assert stored.data['issuedByStaffId'] == admin_client.staff.id
    assert 'reason' not in stored.data
    event = db.scalar(select(PromotionEvent).where(
        PromotionEvent.coupon_id == stored.id))
    assert event.kind == 'MANUAL_ISSUE'
    assert event.data['staffId'] == admin_client.staff.id

    again = api(admin_client,
                f'/admin/promotions/coupons/recipient?email={quote(account.email)}',
                method='GET')
    assert again.json()['data']['availableCouponCount'] == 1


def test_manual_issue_is_idempotent(admin_client, account_factory, db, api):
    account = account_factory()
    key = 'manual-issue-replay-key'
    payload = body(account.email)
    first = api(admin_client, '/admin/promotions/coupons/manual', payload, key=key)
    second = api(admin_client, '/admin/promotions/coupons/manual', payload, key=key)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()['data']['coupon']['id'] == second.json()['data']['coupon']['id']
    db.expire_all()
    assert int(db.scalar(select(func.count()).select_from(Coupon).where(
        Coupon.account_id == account.id))) == 1
    assert int(db.scalar(select(func.count()).select_from(PromotionEvent).where(
        PromotionEvent.kind == 'MANUAL_ISSUE',
        PromotionEvent.account_id == account.id))) == 1


def test_manual_issue_rejects_ineligible_accounts(admin_client, account_factory, db, api):
    unverified = account_factory(verified=False)
    response = api(admin_client, '/admin/promotions/coupons/manual', body(unverified.email))
    assert response.status_code == 422
    assert error_code(response) == 'ACCOUNT_NOT_ELIGIBLE'

    suspended = account_factory()
    suspended.status = 'SUSPENDED'
    db.commit()
    response = api(admin_client, '/admin/promotions/coupons/manual', body(suspended.email))
    assert response.status_code == 422
    assert error_code(response) == 'ACCOUNT_NOT_ELIGIBLE'

    pending = account_factory()
    pending.status = 'DELETION_PENDING'
    pending.deleted_at = utcnow()
    db.commit()
    response = api(admin_client, '/admin/promotions/coupons/manual', body(pending.email))
    assert response.status_code == 422
    assert error_code(response) == 'ACCOUNT_NOT_ELIGIBLE'

    own = api(admin_client, '/admin/promotions/coupons/manual',
              body(admin_client.account.email))
    assert own.status_code == 403
    assert error_code(own) == 'SELF_ISSUE_DENIED'

    missing = api(admin_client,
                  '/admin/promotions/coupons/recipient?email=missing%40example.test',
                  method='GET')
    assert missing.status_code == 404
    assert error_code(missing) == 'ACCOUNT_NOT_FOUND'


def test_manual_issue_rejects_ambiguous_email(admin_client, account_factory, api):
    account_factory(email='Case.Test@example.test')
    account_factory(email='case.test@example.test')
    response = api(admin_client,
                   '/admin/promotions/coupons/recipient?email=case.test%40example.test',
                   method='GET')
    assert response.status_code == 409
    assert error_code(response) == 'MULTIPLE_ACCOUNTS'


def test_manual_issue_validates_amount_and_expiry(admin_client, account_factory, api):
    account = account_factory()
    too_large = api(admin_client, '/admin/promotions/coupons/manual',
                    body(account.email, amountCents=20_001))
    assert too_large.status_code == 422

    too_soon = api(admin_client, '/admin/promotions/coupons/manual',
                   body(account.email, validUntil=(utcnow() + timedelta(minutes=1)).isoformat()))
    assert too_soon.status_code == 422
    assert error_code(too_soon) == 'INVALID_VALID_UNTIL'

    too_far = api(admin_client, '/admin/promotions/coupons/manual',
                  body(account.email, validUntil=(utcnow() + timedelta(days=366)).isoformat()))
    assert too_far.status_code == 422
    assert error_code(too_far) == 'INVALID_VALID_UNTIL'


def test_manual_issue_enforces_outstanding_limit(admin_client, account_factory, api):
    account = account_factory()
    for _ in range(3):
        response = api(admin_client, '/admin/promotions/coupons/manual',
                       body(account.email, amountCents=100))
        assert response.status_code == 200, response.text
    fourth = api(admin_client, '/admin/promotions/coupons/manual',
                 body(account.email, amountCents=100))
    assert fourth.status_code == 422
    assert error_code(fourth) == 'MANUAL_OUTSTANDING_LIMIT'


def test_manual_issue_enforces_daily_limit(admin_client, account_factory, api):
    for _ in range(5):
        recipient = account_factory()
        response = api(admin_client, '/admin/promotions/coupons/manual',
                       body(recipient.email, amountCents=20_000))
        assert response.status_code == 200, response.text
    over = api(admin_client, '/admin/promotions/coupons/manual',
               body(account_factory().email, amountCents=1))
    assert over.status_code == 422
    assert error_code(over) == 'MANUAL_DAILY_LIMIT'


def test_manual_coupon_records_show_manual_name(admin_client, account_factory, api):
    account = account_factory()
    response = api(admin_client, '/admin/promotions/coupons/manual', body(account.email))
    assert response.status_code == 200, response.text
    coupon_id = response.json()['data']['coupon']['id']

    records = api(admin_client, '/admin/promotions/records?type=MANUAL_ISSUE', method='GET')
    assert records.status_code == 200, records.text
    rows = [item for item in records.json()['data']['items']
            if item['couponGrantId'] == coupon_id]
    assert rows
    assert rows[0]['campaignName'] == '人工发放'
    assert rows[0]['type'] == 'MANUAL_ISSUE'


def test_manual_coupon_does_not_earn_refund_compensation(db):
    from app.growth import build_quote, on_refund, reserve_coupon, settle_coupon
    from tests.test_growth import base_config, line, make_account, make_order, make_prep, make_refund

    db = base_config(db, None)
    account = make_account(db)
    coupon = Coupon(
        account_id=account.id, campaign_id=None, amount_cents=500,
        status='AVAILABLE', risk_hold='NONE', source_key='MANUAL_ISSUE:refund-test',
        valid_until=utcnow() + timedelta(days=7),
        data={
            'terms': {'amountCents': 500, 'minSpendCents': 0, 'service': 'A4',
                      'campaignId': 'MANUAL', 'name': '人工券',
                      'campaignVersion': None, 'kind': 'MANUAL'},
            'manualIssue': True, 'independentLiability': True,
            'liabilityReserved': False,
        },
    )
    db.add(coupon)
    db.flush()
    prep = make_prep(db, account)
    quote = build_quote(db, prep, [line('a', 'f1', 'A4', 1, 1000)], actor=None)
    order = make_order(db, prep, quote, account, cash=500,
                       settlement_kind='CASH_MANUAL')
    reserve_coupon(db, order, quote)
    settle_coupon(db, order)
    refund = make_refund(db, order, 500, '整单退款')
    order.refunded_cents = 500
    db.flush()
    on_refund(db, order, refund)
    assert db.scalar(select(Coupon).where(
        Coupon.source_key == f'REFUND_COMPENSATION:{order.id}')) is None
