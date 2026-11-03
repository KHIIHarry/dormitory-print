"""Upgrade a real pre-scope PostgreSQL schema containing ambiguous history."""
from datetime import timedelta
import os
from pathlib import Path
import secrets
import subprocess
import sys
from urllib.parse import quote

from sqlalchemy import create_engine, text

from app.models import (Campus, DeliveryBatch, DeliveryChange, DeliveryGroup, DeliverySlot,
                        Location, Order, Preparation, Principal, Quote, utcnow)


def test_schema_upgrade_only_binds_provable_campuses_and_keeps_shared_capacity():
    root_url = os.environ.get('DORMPRINT_TEST_DATABASE_URL',
        'postgresql+psycopg://dormprint:local-test-only@127.0.0.1:55432/dormprint')
    schema = 'campus_migration_' + secrets.token_hex(8)
    root = create_engine(root_url)
    with root.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = root_url + ('&' if '?' in root_url else '?') + 'options=' + quote('-csearch_path=' + schema, safe='')
    engine = create_engine(url)
    directory = Path(__file__).resolve().parents[1]
    def migrate(*args):
        result = subprocess.run([sys.executable, '-m', 'alembic', *args], cwd=directory,
            env={**os.environ, 'DORMPRINT_DATABASE_URL': url}, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
    try:
        migrate('upgrade', 'd2e4f6a8b010')
        now = utcnow()
        with engine.begin() as connection:
            connection.execute(Campus.__table__.insert(), [{'id': 'east', 'name': '东校区'}, {'id': 'west', 'name': '西校区'}])
            for slot_id in ('unique', 'shared', 'empty', 'snapshot-only', 'history-contradiction', 'empty-unowned', 'deleted-campus-history'):
                # Explicit columns omit campus_id, which does not exist yet.
                connection.execute(DeliverySlot.__table__.insert().values(id=slot_id,
                    date='2030-01-01', name=slot_id, starts_at=now+timedelta(days=1),
                    ends_at=now+timedelta(days=1, hours=1), capacity=6, occupied=0,
                    sealed_at=now, seal_reason='旧封批事实'))
            connection.execute(DeliveryGroup.__table__.insert(), [
                {'slot_id': 'unique', 'campus_id': 'east'},
                {'slot_id': 'shared', 'campus_id': 'east'},
                {'slot_id': 'shared', 'campus_id': 'west'},
                {'slot_id': 'history-contradiction', 'campus_id': 'east'},
                {'slot_id': 'empty-unowned', 'campus_id': 'east'}])
            connection.execute(DeliveryBatch.__table__.insert().values(name='旧快照批次',
                campus_id='west', starts_at=now, ends_at=now+timedelta(hours=1),
                data={'slotSnapshot': {'id': 'snapshot-only'}}))
            connection.execute(DeliveryBatch.__table__.insert().values(name='空的无归属旧批次',
                slot_id='empty-unowned', starts_at=now, ends_at=now+timedelta(hours=1)))
            connection.execute(Principal.__table__.insert().values(id='principal'))
            connection.execute(Location.__table__.insert().values(id='location', name='东区楼栋', campus_id='east'))
            connection.execute(Preparation.__table__.insert().values(id='preparation', principal_id='principal',
                expires_at=now+timedelta(days=1)))
            connection.execute(Quote.__table__.insert().values(id='quote', preparation_id='preparation',
                price_version=1, expires_at=now+timedelta(days=1), subtotal_cents=10, total_cents=10))
            connection.execute(Order.__table__.insert().values(id='order', principal_id='principal',
                pickup_code='HISTORY', preparation_id='preparation', quote_id='quote', total_cents=10,
                nickname='测试', phone='13800000000', location_id='location', requested_slot_id='unique',
                close_at=now+timedelta(days=1)))
            connection.execute(DeliveryChange.__table__.insert().values(order_id='order', slot_id='unique',
                kind='ETA_UPDATED', original={'slotId': 'history-contradiction', 'campusId': 'west'},
                proposed={'slotId': 'unique', 'campusId': 'east'}))
            connection.execute(DeliveryChange.__table__.insert().values(order_id='order', slot_id='unique',
                kind='ETA_UPDATED', original={'slotId': 'deleted-campus-history', 'campusId': 'deleted-campus'},
                proposed={'slotId': 'unique', 'campusId': 'east'}))
        migrate('upgrade', 'head')
        with engine.begin() as connection:
            rows = {row.id: row for row in connection.execute(text(
                'SELECT id,campus_id,capacity,occupied,sealed_at,seal_reason,data FROM delivery_slots'))}
            assert rows['unique'].campus_id == 'east'
            assert rows['snapshot-only'].campus_id == 'west'
            assert rows['shared'].campus_id is None
            assert rows['shared'].data['campusScopeStatus'] == 'SHARED_LEGACY'
            assert rows['empty'].campus_id is None
            assert rows['empty'].data['campusScopeStatus'] == 'UNRESOLVED'
            assert rows['history-contradiction'].campus_id is None
            assert rows['empty-unowned'].campus_id is None
            assert rows['deleted-campus-history'].campus_id is None
            assert rows['deleted-campus-history'].data['campusScopeStatus'] == 'UNRESOLVED'
            assert all(row.capacity == 6 and row.occupied == 0 for row in rows.values())
            assert all(row.seal_reason == '旧封批事实' and row.sealed_at == now for row in rows.values())
        migrate('check')
    finally:
        engine.dispose()
        with root.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        root.dispose()
