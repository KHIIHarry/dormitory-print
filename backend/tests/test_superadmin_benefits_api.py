"""Real media/API regression: a bound singleton superadmin gets a free quote."""
from uuid import uuid4

from app.models import Staff

APP_MODULES = ['media', 'benefits', 'growth', 'mail', 'auth']


def make_staff(db, account):
    staff = Staff(
        username=f'staff-{uuid4().hex[:12]}', name='超级管理员', password_hash='x',
        role='ADMIN', enabled=True, status='ACTIVE', account_id=account.id,
        is_super_admin=True, discount_level=None, capabilities=['*'],
        must_change_password=False,
    )
    db.add(staff)
    db.commit()
    return staff


def test_superadmin_media_quote_is_free(client_factory, db):
    from test_media import (
        guest, json_write, login_account, make_pdf, make_verified_account,
        preparation_payload, process_asset, process_preparation, seed_config, upload,
    )

    seed_config(db)
    account = make_verified_account(db)
    staff = make_staff(db, account)
    assert staff.is_super_admin is True
    client = guest(client_factory())
    login_account(client, db, account)
    asset_id = upload(client, make_pdf(2), 'superadmin.pdf').json()['data']['assetId']
    process_asset(db, asset_id)
    response = json_write(client, '/api/v1/preparations', preparation_payload(asset_id, 2))
    assert response.status_code == 200, response.text
    prep_id = response.json()['data']['id']
    process_preparation(db, prep_id)
    view = json_write(client, f'/api/v1/preparations/{prep_id}', None, method='GET')
    assert view.status_code == 200, view.text
    data = view.json()['data']
    assert data['checkoutAccountId'] == account.id
    quote = data['quote']
    assert quote['selectedPromotionType'] == 'STAFF_GRADE'
    assert quote['totalCents'] == 0
    assert quote['discountCents'] == quote['subtotalCents']
    candidate = quote['staffDiscountCandidate']
    assert candidate['source'] == 'SUPER_ADMIN'
    assert candidate['staffId'] == staff.id
    assert candidate['discountLevel'] is None
    assert candidate['reductionBps'] == 10000
    assert candidate['payRateBps'] == 0
