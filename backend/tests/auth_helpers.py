"""Helpers for the auth/account module tests.

These helpers use the shared PostgreSQL session configured by tests/conftest.py.
They deliberately do not create a second database, app instance or test schema.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import (
    Account,
    DeliverySlot,
    Location,
    Order,
    Preparation,
    Principal,
    Quote,
    utcnow,
)

PASSWORD = 'StrongPass123!'


@contextmanager
def db_session():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    finally:
        session.rollback()
        session.close()


def new_client(app) -> TestClient:
    """A separate cookie jar, i.e. a separate browser/device."""
    client = TestClient(app)
    response = client.post(
        '/api/v1/customer/session',
        json={},
        headers={'Origin': 'http://testserver', 'Idempotency-Key': uuid4().hex},
    )
    assert response.status_code == 200, response.text
    return client


def bootstrap(client: TestClient) -> TestClient:
    if not client.cookies.get('csrfToken'):
        response = client.get('/api/v1/customer/auth/me')
        assert response.status_code == 200, response.text
    return client


def api(client: TestClient, path: str, body=None, method: str = 'POST', key: str | None = None,
        headers: dict | None = None):
    bootstrap(client)
    merged = {'Origin': 'http://testserver'}
    csrf = client.cookies.get('csrfToken')
    if csrf:
        merged['X-CSRF-Token'] = csrf
    if method not in ('GET', 'HEAD'):
        merged['Idempotency-Key'] = key or uuid4().hex
    if headers:
        merged.update(headers)
    return client.request(method, '/api/v1' + path, json=body, headers=merged)


def make_account(db, email: str | None = None, password: str = PASSWORD, verified: bool = True,
                 display_name: str = '测试同学', status: str | None = None):
    from app.auth import hash_password, normalize_email

    email = normalize_email(email or f'{uuid4()}@example.invalid')
    principal = Principal()
    db.add(principal)
    db.flush()
    account = Account(
        principal_id=principal.id,
        email=email,
        password_hash=hash_password(password),
        status=status or ('ACTIVE' if verified else 'PENDING_EMAIL'),
        verified_at=utcnow() if verified else None,
        display_name=display_name,
        invite_code=f'inv-{uuid4().hex[:24]}',
        preferences={'orderCreated': False, 'orderStatusUpdated': False, 'referralReward': False},
    )
    db.add(account)
    db.flush()
    principal.account_id = account.id
    db.flush()
    return account


def make_admin(db, username: str | None = None, password: str = PASSWORD, name: str = '管理员',
               email: str | None = None):
    """Create a verified customer account plus its superadmin grant.

    FINAL N05 has no standalone staff credential, so every admin helper starts
    from a normal registered account. Callers can use ``admin.email`` or
    ``admin.username`` with ``login_staff``; both resolve the same account.
    """
    from app.auth import create_initial_admin

    account = make_account(db, email=email, password=password, display_name=name)
    staff = create_initial_admin(db, username or f'admin-{uuid4().hex[:12]}', password, name,
                                 email=account.email)
    db.flush()
    return staff


def grant_staff(db, account, role: str = 'ADMIN', capabilities: list[str] | None = None,
                is_super_admin: bool = False, discount_level: int | None = None,
                username: str | None = None, enabled: bool = True, status: str = 'ACTIVE'):
    """Module-local fixture for direct DB setup; HTTP grant tests use /admin/staff."""
    from app.models import Staff

    staff = Staff(
        account_id=account.id,
        is_super_admin=is_super_admin,
        discount_level=discount_level,
        username=username or f'staff-{uuid4().hex[:12]}',
        name=account.display_name or '员工',
        phone='',
        email=account.email,
        email_verified=True,
        role=role,
        enabled=enabled,
        status=status,
        capabilities=['*'] if capabilities is None and role == 'ADMIN' else (capabilities or []),
        must_change_password=False,
        password_hash='',
    )
    db.add(staff)
    db.flush()
    return staff


def login_customer(client: TestClient, email: str, password: str = PASSWORD):
    return api(client, '/customer/auth/login', {'email': email, 'password': password})


def login_staff(client: TestClient, username: str, password: str = PASSWORD):
    return api(client, '/auth/login', {'username': username, 'password': password})


def make_order(db, principal_id: str, status: str = 'PENDING_CONFIRMATION', total_cents: int = 1200):
    location = Location(name='一号楼', type='DORMITORY', x=0.4, y=0.5, enabled=True, map_version=1)
    db.add(location)
    db.flush()
    starts = utcnow() + timedelta(hours=2)
    slot = DeliverySlot(date=starts.date().isoformat(), name='下午', starts_at=starts,
                        ends_at=starts + timedelta(hours=2), capacity=20)
    db.add(slot)
    db.flush()
    preparation = Preparation(principal_id=principal_id, status='READY', input={}, data={},
                              expires_at=utcnow() + timedelta(hours=2))
    db.add(preparation)
    db.flush()
    quote = Quote(preparation_id=preparation.id, price_version=1,
                  expires_at=utcnow() + timedelta(minutes=15), subtotal_cents=total_cents,
                  discount_cents=0, total_cents=total_cents, data={})
    db.add(quote)
    db.flush()
    order = Order(
        principal_id=principal_id,
        pickup_code=uuid4().hex[:8].upper(),
        status=status,
        preparation_id=preparation.id,
        quote_id=quote.id,
        total_cents=total_cents,
        nickname='测试',
        phone='13800000000',
        location_id=location.id,
        requested_slot_id=slot.id,
        close_at=utcnow() + timedelta(hours=24),
        data={},
    )
    db.add(order)
    db.flush()
    return order


def latest_session(db, kind: str, **filters):
    from sqlalchemy import select

    from app.models import AuthSession

    query = select(AuthSession).where(AuthSession.kind == kind)
    for name, value in filters.items():
        query = query.where(getattr(AuthSession, name) == value)
    return db.scalar(query.order_by(AuthSession.created_at.desc()))
