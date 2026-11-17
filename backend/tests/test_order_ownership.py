from datetime import timedelta

import pytest

from app.core import Actor, DomainError, authorize_order
from app.models import AccessGrant, utcnow
from tests.auth_helpers import make_account, make_order


def test_order_owner_is_independent_of_guest_principal(db):
    account = make_account(db)
    order = make_order(db, account.principal_id)
    order.owner_account_id = account.id
    db.flush()
    authorize_order(db, Actor(account_id=account.id), order)
    with pytest.raises(DomainError):
        authorize_order(db, Actor(principal_id=account.principal_id), order)


def test_guest_owner_also_obeys_link_deadline(db):
    account = make_account(db)
    order = make_order(db, account.principal_id)
    grant = AccessGrant(order_id=order.id, token_hash='a' * 64,
                        scopes=['ORDER_VIEW'], expires_at=utcnow() - timedelta(seconds=1))
    db.add(grant)
    db.flush()
    with pytest.raises(DomainError):
        authorize_order(db, Actor(guest_principal_id=order.principal_id), order)


def test_links_never_derive_account_or_editing_permissions(db):
    account = make_account(db)
    order = make_order(db, account.principal_id)
    order.owner_account_id = None
    grant = AccessGrant(order_id=order.id, token_hash='b' * 64,
                        account_id=account.id, scopes=['ORDER_VIEW', 'ORDER_REVISE_PENDING'],
                        expires_at=utcnow() + timedelta(days=14))
    db.add(grant)
    db.flush()
    with pytest.raises(DomainError):
        authorize_order(db, Actor(account_id=account.id), order)
    with pytest.raises(DomainError):
        authorize_order(db, Actor(grant_ids=[grant.id]), order, 'ORDER_REVISE_PENDING')


def test_identification_progress_only_exists_for_enabled_order(db):
    from app.order_identification import identification_progress
    from app.models import Job
    account = make_account(db)
    order = make_order(db, account.principal_id)
    assert identification_progress(db, order) is None
    db.add(Job(kind='ORDER_IDENTIFICATION', target_id=order.id,
               business_key=f'order-identification:{order.id}', status='QUEUED', data={}))
    db.flush()
    assert identification_progress(db, order) == {
        'enabled': True, 'completed': 0, 'total': 0, 'status': 'QUEUED'}
