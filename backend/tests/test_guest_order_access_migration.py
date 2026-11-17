"""Execute the ownership/link migration on isolated legacy PostgreSQL tables."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
import secrets

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def legacy_connection(database_schema):
    with database_schema.connect() as connection:
        transaction = connection.begin()
        schema = 'guest_migration_' + secrets.token_hex(6)
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        statements = [
            'CREATE TABLE customer_accounts (id varchar(64) PRIMARY KEY)',
            'CREATE TABLE principals (id varchar(64) PRIMARY KEY, account_id varchar(64))',
            '''CREATE TABLE orders (id varchar(64) PRIMARY KEY, principal_id varchar(64),
               checkout_account_id varchar(64))''',
            '''CREATE TABLE order_access_grants (id varchar(64) PRIMARY KEY,
               order_id varchar(64), token_hash varchar(64), account_id varchar(64),
               source_grant_id varchar(64), created_at timestamptz, expires_at timestamptz,
               revoked_at timestamptz, version integer DEFAULT 1, scopes jsonb)''',
            '''CREATE TABLE auth_sessions (id varchar(64) PRIMARY KEY, kind varchar(32),
               grant_id varchar(64), expires_at timestamptz, revoked_at timestamptz)''',
            '''CREATE TABLE idempotency_records (id varchar(64) PRIMARY KEY, route text)''',
        ]
        for statement in statements:
            connection.execute(text(statement))
        connection.execute(text("INSERT INTO customer_accounts VALUES ('account-a'), ('account-b')"))
        connection.execute(text("INSERT INTO principals VALUES ('guest', NULL), ('member', 'account-a')"))
        yield connection
        transaction.rollback()


def migration():
    path = Path(__file__).resolve().parents[1] / 'alembic/versions/ce72aa117014_guest_order_access.py'
    spec = importlib.util.spec_from_file_location('guest_access_migration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade(connection):
    with Operations.context(MigrationContext.configure(connection)):
        migration().upgrade()


def add_order(connection, order_id, principal='guest', checkout=None):
    connection.execute(text('INSERT INTO orders VALUES (:id, :principal, :checkout)'),
                       {'id': order_id, 'principal': principal, 'checkout': checkout})


def add_grant(connection, grant_id, order_id, *, created, expires=None, revoked=None, source=None):
    connection.execute(text('''
        INSERT INTO order_access_grants
            (id, order_id, token_hash, source_grant_id, created_at, expires_at, revoked_at, scopes)
        VALUES (:id, :order_id, :hash, :source, :created, :expires, :revoked,
                '["ORDER_VIEW","ORDER_IMPORT","ORDER_REVISE_PENDING"]'::jsonb)
    '''), {'id': grant_id, 'order_id': order_id, 'hash': None if source else grant_id,
           'source': source, 'created': created, 'expires': expires or created + timedelta(days=30),
           'revoked': revoked})
    connection.execute(text('''
        INSERT INTO auth_sessions (id, kind, grant_id, expires_at)
        VALUES (:id, 'ORDER_ACCESS', :grant, :expires)
    '''), {'id': grant_id + '-session', 'grant': grant_id,
           'expires': created + timedelta(days=30)})


def test_migration_rejects_conflicting_reliable_owners(legacy_connection):
    connection = legacy_connection
    add_order(connection, 'conflict', principal='member', checkout='account-b')
    with pytest.raises(RuntimeError, match='conflict'):
        upgrade(connection)
    assert 'owner_account_id' not in {column['name'] for column in inspect(connection).get_columns('orders')}


def test_migration_backfills_owners_closes_links_and_clears_private_replays(legacy_connection):
    connection = legacy_connection
    now = datetime.now(timezone.utc)
    add_order(connection, 'principal-owner', principal='member')
    add_order(connection, 'checkout-owner', checkout='account-b')
    add_order(connection, 'unowned')
    for order_id in ['principal-owner', 'checkout-owner']:
        add_grant(connection, order_id + '-root', order_id, created=now)
        add_grant(connection, order_id + '-derived', order_id, created=now,
                  source=order_id + '-root')
    paths = ['/orders', '/orders/id/cancel', '/order-access/exchange',
             '/admin/orders/id/access-links', '/customer/account/link-guest',
             '/customer/account/import-order', '/after-sales/id', '/config/public']
    for index, path in enumerate(paths):
        connection.execute(text('INSERT INTO idempotency_records VALUES (:id, :route)'),
                           {'id': str(index), 'route': 'POST /api/v1' + path})
    upgrade(connection)
    rows = dict(connection.execute(text('SELECT id, owner_account_id FROM orders')).all())
    assert rows == {'principal-owner': 'account-a', 'checkout-owner': 'account-b', 'unowned': None}
    assert connection.execute(text('SELECT DISTINCT guest_access_version FROM orders')).scalars().all() == [1]
    assert connection.execute(text('SELECT count(*) FROM order_access_grants WHERE revoked_at IS NULL')).scalar() == 0
    assert connection.execute(text('SELECT count(*) FROM auth_sessions WHERE revoked_at IS NULL')).scalar() == 0
    assert connection.execute(text('SELECT route FROM idempotency_records')).scalars().all() == ['POST /api/v1/config/public']
    assert any(index['name'] == 'ix_orders_owner_account_id' for index in inspect(connection).get_indexes('orders'))


def test_migration_keeps_latest_valid_root_clamps_ttl_and_revokes_other_access(legacy_connection):
    connection = legacy_connection
    now = datetime.now(timezone.utc)
    add_order(connection, 'guest-order')
    add_grant(connection, 'older-valid', 'guest-order', created=now - timedelta(days=2))
    add_grant(connection, 'newest-valid', 'guest-order', created=now - timedelta(days=1))
    add_grant(connection, 'newer-closed', 'guest-order', created=now, revoked=now)
    add_grant(connection, 'derived', 'guest-order', created=now, source='newest-valid')
    upgrade(connection)
    roots = connection.execute(text('SELECT * FROM order_access_grants WHERE revoked_at IS NULL')).mappings().all()
    assert len(roots) == 1
    root = roots[0]
    assert root['id'] == 'newest-valid'
    assert root['expires_at'] == root['created_at'] + timedelta(days=14)
    assert set(root['scopes']) == {'ORDER_VIEW', 'FINAL_PDF_PREVIEW', 'DELIVERY_PROOF_VIEW'}
    assert root['encrypted_token'] is None
    sessions = connection.execute(text('SELECT * FROM auth_sessions WHERE revoked_at IS NULL')).mappings().all()
    assert len(sessions) == 1
    assert sessions[0]['grant_id'] == root['id']
    assert sessions[0]['expires_at'] == root['expires_at']
    with pytest.raises(IntegrityError), connection.begin_nested():
        add_grant(connection, 'duplicate-root', 'guest-order', created=now)


@pytest.mark.parametrize('latest_revoked', [False, True])
def test_migration_preserves_expired_or_closed_latest_state(legacy_connection, latest_revoked):
    connection = legacy_connection
    now = datetime.now(timezone.utc)
    add_order(connection, 'expired-order')
    add_grant(connection, 'older-expired', 'expired-order', created=now - timedelta(days=20))
    add_grant(connection, 'latest-expired', 'expired-order', created=now - timedelta(days=18),
              revoked=now if latest_revoked else None)
    upgrade(connection)
    active = connection.execute(text('SELECT * FROM order_access_grants WHERE revoked_at IS NULL')).mappings().all()
    if latest_revoked:
        assert active == []
    else:
        assert len(active) == 1
        assert active[0]['id'] == 'latest-expired'
        assert active[0]['expires_at'] < now
        with pytest.raises(IntegrityError), connection.begin_nested():
            add_grant(connection, 'duplicate-expired-root', 'expired-order', created=now)
    assert connection.execute(text('SELECT count(*) FROM auth_sessions WHERE revoked_at IS NULL')).scalar() == 0


def test_downgrade_preserves_new_per_order_account_ownership(legacy_connection):
    connection = legacy_connection
    add_order(connection, 'claimed-order')
    upgrade(connection)
    connection.execute(text("UPDATE orders SET owner_account_id = 'account-a' WHERE id = 'claimed-order'"))
    with Operations.context(MigrationContext.configure(connection)):
        with pytest.raises(RuntimeError, match='claimed-order'):
            migration().downgrade()
    assert connection.execute(text("SELECT owner_account_id FROM orders WHERE id = 'claimed-order'")).scalar() == 'account-a'
