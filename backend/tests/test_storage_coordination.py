from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.engine import make_url

from app.core import DomainError
from app.db import SessionLocal
from app.models import (
    Account,
    Asset,
    AnonymousLedger,
    AuditLog,
    AuthSession,
    AccessGrant,
    DeletionRequest,
    DeliverySlot,
    EmailJob,
    ExportJob,
    IdempotencyRecord,
    Job,
    Location,
    OneTimeToken,
    Order,
    Preparation,
    Principal,
    PromotionEvent,
    Quote,
    Staff,
    TrashEntry,
    WageEntry,
)
from app.maintenance import maintenance_sweep, process_purge_job
from app.settings import get_settings
from app.storage_coordination import (
    CHILD_FIRST_TABLES,
    maintenance_lock,
    purge_guard,
    purge_storage_key_locked,
    purge_storage_tree_locked,
    record_purged_files,
    recover_orphaned_staging,
    recover_purge_cleanup,
    storage_gc_guard,
)

APP_MODULES = ['auth', 'commerce', 'exports', 'maintenance']

REPO = Path(__file__).resolve().parents[2]
APPLY = REPO / 'deploy' / 'backup' / 'apply_tombstones.py'
POST_RESTORE = REPO / 'deploy' / 'backup' / 'post_restore.sql'


def _now():
    return datetime.now(timezone.utc)


def _account(db, suffix='a'):
    principal = Principal()
    db.add(principal)
    db.flush()
    account = Account(
        principal_id=principal.id,
        email=f'{suffix}-{uuid4().hex}@example.test',
        password_hash='hash',
    )
    db.add(account)
    db.flush()
    return principal, account


def _order_graph(db, principal, account, suffix='o'):
    preparation = Preparation(
        principal_id=principal.id,
        checkout_account_id=account.id,
        expires_at=_now() + timedelta(days=1),
        status='READY',
    )
    db.add(preparation)
    db.flush()
    quote = Quote(
        preparation_id=preparation.id,
        checkout_account_id=account.id,
        price_version=1,
        expires_at=_now() + timedelta(days=1),
        subtotal_cents=100,
        discount_cents=0,
        total_cents=100,
    )
    location = Location(name=f'Dorm-{suffix}')
    slot = DeliverySlot(
        date='2026-10-30',
        name=f'Slot-{suffix}',
        starts_at=_now() + timedelta(days=1),
        ends_at=_now() + timedelta(days=1, hours=1),
        capacity=10,
    )
    db.add_all([quote, location, slot])
    db.flush()
    order = Order(
        principal_id=principal.id,
        checkout_account_id=account.id,
        pickup_code=f'P-{uuid4().hex[:12]}',
        preparation_id=preparation.id,
        quote_id=quote.id,
        total_cents=100,
        nickname='Nick',
        phone='13800000000',
        location_id=location.id,
        requested_slot_id=slot.id,
        close_at=_now() + timedelta(days=2),
    )
    db.add(order)
    db.flush()
    return order


def _staff(db, suffix='s', super_admin=False):
    staff = Staff(
        username=f'staff-{suffix}-{uuid4().hex}',
        name='Staff Name',
        phone='13900000000',
        email=f'{suffix}-{uuid4().hex}@example.test',
        password_hash='hash',
        role='ADMIN',
        capabilities=['orders.read'],
        is_super_admin=super_admin,
    )
    db.add(staff)
    db.flush()
    return staff


def _trash(db, target_id='target-1', target_type='ORDER'):
    entry = TrashEntry(
        target_type=target_type,
        target_id=target_id,
        actor_id='system',
        reason='test purge',
        status='DELETED',
        purge_at=_now(),
        data={'name': 'should not survive', 'email': 'pii@example.test'},
    )
    db.add(entry)
    db.flush()
    return entry


def _url():
    return make_url(os.environ['DORMPRINT_DATABASE_URL'])


def _schema(db):
    value = db.execute(text('SHOW search_path')).scalar_one()
    return value.strip('"').split(',')[0]


def _psql_wrapper(tmp_path: Path) -> Path:
    docker = Path('/usr/local/bin/docker')
    if not docker.exists():
        docker = Path('/usr/bin/docker')
    container = os.environ.get('DORMPRINT_TEST_PG_CONTAINER', 'dormprint-test-postgres')
    if not docker.exists() or subprocess.run(
        [str(docker), 'inspect', container], capture_output=True
    ).returncode != 0:
        pytest.skip('real PostgreSQL container not available for psql subprocess replay')
    url = _url()
    wrapper = tmp_path / 'psql'
    wrapper.write_text(
        '#!/bin/sh\n'
        f'exec {shlex.quote(str(docker))} exec -i '
        f'-e PGPASSWORD={shlex.quote(url.password or "")} '
        f'-e PGOPTIONS="$PGOPTIONS" '
        f'{shlex.quote(container)} psql -U {shlex.quote(url.username or "dormprint")} '
        '-h 127.0.0.1 -p 5432 "$@"\n',
        encoding='utf-8',
    )
    wrapper.chmod(0o755)
    return wrapper


def _subprocess_env(db) -> dict:
    env = os.environ.copy()
    env['PGOPTIONS'] = f'-csearch_path={_schema(db)}'
    env['PGPASSWORD'] = _url().password or ''
    return env


def _write_journal(tmp_path: Path, entries: list[dict]) -> Path:
    path = tmp_path / 'tombstones.jsonl'
    path.write_text(
        ''.join(json.dumps(entry, ensure_ascii=False) + '\n' for entry in entries),
        encoding='utf-8',
    )
    return path


def _run_apply(db, tmp_path, entries, *, dry_run=False, env=None):
    journal = _write_journal(tmp_path, entries)
    wrapper = _psql_wrapper(tmp_path)
    args = [
        sys.executable,
        str(APPLY),
        'apply',
        '--journal',
        str(journal),
        '--database',
        _url().database,
        '--psql',
        str(wrapper),
        '--storage',
        str(get_settings().storage_root),
    ]
    if dry_run:
        args.append('--dry-run')
    return subprocess.run(
        args,
        cwd=REPO,
        env=env or _subprocess_env(db),
        capture_output=True,
        text=True,
        timeout=60,
    )


def _run_export(db, tmp_path, output: Path):
    wrapper = _psql_wrapper(tmp_path)
    return subprocess.run(
        [
            sys.executable,
            str(APPLY),
            'export',
            '--database',
            _url().database,
            '--psql',
            str(wrapper),
            '--output',
            str(output),
        ],
        cwd=REPO,
        env=_subprocess_env(db),
        capture_output=True,
        text=True,
        timeout=60,
    )


def _run_post_restore(db, tmp_path):
    wrapper = _psql_wrapper(tmp_path)
    return subprocess.run(
        [
            str(wrapper),
            '-X',
            '--no-password',
            '-v',
            'ON_ERROR_STOP=1',
            '-d',
            _url().database,
            '-f',
            '-',
        ],
        cwd=REPO,
        env=_subprocess_env(db),
        input=POST_RESTORE.read_text(encoding='utf-8'),
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_purge_guard_captures_exact_rows_files_and_private_lock(db):
    principal, account = _account(db, 'target')
    request = DeletionRequest(account_id=account.id, status='REQUESTED')
    other_principal, other_account = _account(db, 'other')
    other_request = DeletionRequest(account_id=other_account.id, status='REQUESTED')
    db.add_all([request, other_request])
    db.flush()
    entry = _trash(db, target_id=account.id, target_type='ACCOUNT')
    db.commit()

    with purge_guard(db, entry) as guarded:
        assert guarded is entry
        record_purged_files(entry, ['artifacts/aa/final.pdf', 'artifacts/aa/final.pdf'])
        db.execute(delete(DeletionRequest).where(DeletionRequest.id == request.id))
        db.execute(delete(Account).where(Account.id == account.id))

    db.commit()
    db.expire_all()
    stored = db.get(TrashEntry, entry.id)
    assert stored is not None
    rows = stored.data['purgedRows']
    assert rows == [
        {'table': 'deletion_requests', 'id': request.id},
        {'table': 'customer_accounts', 'id': account.id},
    ], rows
    assert stored.data['purgedFiles'] == ['artifacts/aa/final.pdf']
    assert stored.data['purgedAt'].endswith('Z')
    assert 'name' not in stored.data and 'email' not in stored.data
    assert db.get(Account, account.id) is None
    assert db.get(DeletionRequest, request.id) is None
    # No whole-database scan: an unrelated account and its child are untouched.
    assert db.get(Account, other_account.id) is not None
    assert db.get(DeletionRequest, other_request.id) is not None

    lock = Path(get_settings().storage_root) / '.coordination' / 'maintenance.lock'
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600
    assert stat.S_IMODE(lock.parent.stat().st_mode) == 0o700


def test_purge_guard_rejects_unbounded_bulk_delete(db):
    entry = _trash(db)
    db.commit()
    with pytest.raises(DomainError) as error:
        with purge_guard(db, entry):
            db.execute(delete(AuditLog))
    assert error.value.code == 'PURGE_CAPTURE_UNBOUNDED'
    db.rollback()


def test_purge_guard_captures_bulk_and_orm_fk_detaches(db):
    _, account = _account(db, 'detach')
    principal = db.get(Principal, account.principal_id)
    order = _order_graph(db, principal, account, 'detach')
    staff = _staff(db, 'detach')
    wage = WageEntry(
        staff_id=staff.id,
        order_id=order.id,
        source_key=f'wage-{uuid4().hex}',
        kind='ORDER',
        amount_cents=50,
        period='2026-10',
    )
    event = PromotionEvent(
        business_key=f'event-{uuid4().hex}',
        order_id=order.id,
        kind='ORDER',
        amount_cents=10,
    )
    db.add_all([wage, event])
    db.flush()
    entry = _trash(db, target_id=order.id)
    db.commit()

    with purge_guard(db, entry):
        wage.order_id = None
        db.execute(
            update(PromotionEvent)
            .where(PromotionEvent.id == event.id)
            .values(order_id=None)
        )

    db.commit()
    db.expire_all()
    stored = db.get(TrashEntry, entry.id)
    detaches = {
        (item['table'], item['column'], item['id'])
        for item in stored.data['purgedDetaches']
    }
    assert ('wage_entries', 'order_id', wage.id) in detaches
    assert ('promotion_events', 'order_id', event.id) in detaches
    assert db.get(WageEntry, wage.id).order_id is None
    assert db.get(PromotionEvent, event.id).order_id is None

    # Child-first ordering puts detaches for child tables before parent tables.
    tables = [item['table'] for item in stored.data['purgedDetaches']]
    indexes = [CHILD_FIRST_TABLES.index(name) for name in tables]
    assert indexes == sorted(indexes), tables


def _lock_probe(storage: Path, timeout: float = 0.3) -> subprocess.CompletedProcess:
    """Try to take the maintenance lock in a fresh process (not reentrant)."""
    script = (
        'from app.storage_coordination import maintenance_lock\n'
        f'with maintenance_lock(timeout={timeout!r}):\n'
        '    print("ACQUIRED", flush=True)\n'
    )
    env = os.environ.copy()
    env['PYTHONPATH'] = str(REPO / 'backend')
    env['DORMPRINT_STORAGE_ROOT'] = str(storage)
    return subprocess.run(
        [sys.executable, '-c', script],
        cwd=REPO / 'backend',
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_purge_guard_holds_lock_until_outer_commit(db):
    storage = Path(get_settings().storage_root)
    key = 'artifacts/aa/held-through-commit.pdf'
    target = storage / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'pdf')
    _, account = _account(db, 'lock-through')
    entry = _trash(db, target_id=account.id, target_type='ACCOUNT')
    db.commit()

    with purge_guard(db, entry, files=[key]):
        db.execute(delete(Account).where(Account.id == account.id))

    # The guard has exited, but the caller has NOT committed yet. A backup
    # process must still be blocked. The original path remains readable (the
    # quarantine is an extra hard link), so even a crashed lock holder cannot
    # leave the pre-commit database snapshot referencing a missing file.
    staging = storage / '.coordination' / 'staging' / entry.id
    assert target.exists()
    assert staging.exists()
    blocked = _lock_probe(storage)
    assert blocked.returncode != 0
    assert 'DomainError' in blocked.stderr and '备份或恢复' in blocked.stderr

    db.commit()

    acquired = _lock_probe(storage)
    assert acquired.returncode == 0 and 'ACQUIRED' in acquired.stdout
    db.expire_all()
    stored = db.get(TrashEntry, entry.id)
    assert stored.status == 'PURGED'
    assert 'cleanupPending' not in (stored.data or {})
    assert not target.exists()
    assert not staging.exists(), 'committed staging must be garbage-collected'


def test_purge_guard_rollback_restores_file_and_database_row(db):
    storage = Path(get_settings().storage_root)
    key = 'artifacts/bb/rollback.pdf'
    target = storage / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'keep-me')
    _, account = _account(db, 'rollback')
    request = DeletionRequest(account_id=account.id, status='REQUESTED')
    db.add(request)
    db.flush()
    entry = _trash(db, target_id=account.id, target_type='ACCOUNT')
    db.commit()

    with purge_guard(db, entry, files=[key]):
        record_purged_files(entry, [key])
        db.execute(delete(DeletionRequest).where(DeletionRequest.id == request.id))
        db.execute(delete(Account).where(Account.id == account.id))

    # The original is retained until commit; rollback only drops the staging
    # link and must leave the exact bytes in place.
    assert target.read_bytes() == b'keep-me'
    db.rollback()
    db.expire_all()

    assert target.read_bytes() == b'keep-me'
    assert not (storage / '.coordination' / 'staging' / entry.id).exists()
    assert db.get(Account, account.id) is not None
    assert db.get(DeletionRequest, request.id) is not None
    assert db.get(TrashEntry, entry.id).data.get('purgedRows') is None
    acquired = _lock_probe(storage)
    assert acquired.returncode == 0 and 'ACQUIRED' in acquired.stdout


def test_purge_guard_gc_failure_keeps_pending_and_recovery_retries(db, monkeypatch):
    from app import storage_coordination

    storage = Path(get_settings().storage_root)
    key = 'artifacts/cc/gc-failure.pdf'
    target = storage / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'garbage')
    _, account = _account(db, 'gc-failure')
    entry = _trash(db, target_id=account.id, target_type='ACCOUNT')
    db.commit()

    original_gc = storage_coordination._gc_staged
    calls = {'n': 0}

    def failing_gc(root, entry_dir, staged):
        calls['n'] += 1
        if calls['n'] == 1:
            return [item.get('key') for item in staged]
        return original_gc(root, entry_dir, staged)

    monkeypatch.setattr(storage_coordination, '_gc_staged', failing_gc)
    with purge_guard(db, entry, files=[key]):
        db.execute(delete(Account).where(Account.id == account.id))
    db.commit()
    db.expire_all()

    stored = db.get(TrashEntry, entry.id)
    assert stored.status == 'PURGE_PENDING'
    assert stored.data['cleanupPending'] is True
    assert stored.data['cleanupError'] == 'STAGING_GC_FAILED'
    assert stored.data['stagedFiles']
    assert target.exists(), 'failed GC must leave the original until retry'
    assert (storage / '.coordination' / 'staging' / entry.id).exists()

    monkeypatch.setattr(storage_coordination, '_gc_staged', original_gc)
    result = storage_coordination.recover_orphaned_staging(db)
    db.commit()
    db.expire_all()
    stored = db.get(TrashEntry, entry.id)
    assert result['completed'] == 1
    assert stored.status == 'PURGED'
    assert 'cleanupPending' not in (stored.data or {})
    assert not target.exists()
    assert not (storage / '.coordination' / 'staging' / entry.id).exists()
    assert db.get(Account, account.id) is None


def test_purge_guard_commit_is_not_hidden_by_missing_file(db):
    storage = Path(get_settings().storage_root)
    key = 'artifacts/dd/already-missing.pdf'
    _, account = _account(db, 'missing')
    entry = _trash(db, target_id=account.id, target_type='ACCOUNT')
    db.commit()

    with purge_guard(db, entry, files=[key]):
        db.execute(delete(Account).where(Account.id == account.id))
    db.commit()
    db.expire_all()
    stored = db.get(TrashEntry, entry.id)
    assert stored.status == 'PURGED'
    assert stored.data['purgedFiles'] == [key]
    assert not (storage / key).exists()


def test_purge_guard_finalize_error_does_not_raise_after_commit(db, monkeypatch):
    from app import storage_coordination

    storage = Path(get_settings().storage_root)
    key = 'artifacts/dd/finalize-error.pdf'
    target = storage / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'pdf')
    _, account = _account(db, 'finalize-error')
    entry = _trash(db, target_id=account.id, target_type='ACCOUNT')
    db.commit()

    original_gc = storage_coordination._gc_staged

    def exploding_gc(root, entry_dir, staged):
        raise OSError('simulated staging failure')

    monkeypatch.setattr(storage_coordination, '_gc_staged', exploding_gc)
    with purge_guard(db, entry, files=[key]):
        db.execute(delete(Account).where(Account.id == account.id))
    # The commit has already happened; a finalize failure must not surface to
    # the caller as an exception with unknown side effects.
    db.commit()
    db.expire_all()

    stored = db.get(TrashEntry, entry.id)
    assert stored.status == 'PURGE_PENDING'
    assert stored.data['cleanupError'] == 'STAGING_GC_FAILED'
    assert stored.data['stagedFiles']
    assert target.exists()

    monkeypatch.setattr(storage_coordination, '_gc_staged', original_gc)
    result = storage_coordination.recover_orphaned_staging(db)
    db.commit()
    db.expire_all()
    stored = db.get(TrashEntry, entry.id)
    assert result['completed'] == 1
    assert stored.status == 'PURGED'
    assert not target.exists()
    assert not (storage / '.coordination' / 'staging' / entry.id).exists()


def test_maintenance_lock_blocks_cross_process_and_unlink_times_out(db):
    storage = Path(get_settings().storage_root)
    storage.mkdir(parents=True, exist_ok=True)
    key = 'assets/aa/held.pdf'
    target = storage / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'pdf')

    script = (
        'import sys\n'
        'from app.storage_coordination import maintenance_lock\n'
        'with maintenance_lock(timeout=30):\n'
        '    print("LOCKED", flush=True)\n'
        '    sys.stdin.read()\n'
    )
    env = os.environ.copy()
    env['PYTHONPATH'] = str(REPO / 'backend')
    env['DORMPRINT_STORAGE_ROOT'] = str(storage)
    child = subprocess.Popen(
        [sys.executable, '-c', script],
        cwd=REPO / 'backend',
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == 'LOCKED'
        started = time.monotonic()
        with pytest.raises(DomainError) as error:
            with maintenance_lock(timeout=0.25):
                pass
        assert error.value.code == 'TRANSIENT_IO'
        assert error.value.status == 503
        assert time.monotonic() - started < 3

        with pytest.raises(DomainError) as unlink_error:
            purge_storage_key_locked(key, timeout=0.25)
        assert unlink_error.value.code == 'TRANSIENT_IO'
        assert target.exists(), 'unlink must not happen while backup holds the lock'
    finally:
        assert child.stdin is not None
        child.stdin.close()
        assert child.wait(timeout=10) == 0

    assert purge_storage_key_locked(key, timeout=5) is True
    assert not target.exists()


def test_maintenance_lock_rejects_symlinked_coordination(tmp_path, monkeypatch):
    root = tmp_path / 'storage'
    outside = tmp_path / 'outside'
    root.mkdir()
    outside.mkdir()
    (root / '.coordination').symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv('DORMPRINT_STORAGE_ROOT', str(root))
    get_settings.cache_clear()
    try:
        with pytest.raises(DomainError) as error:
            with maintenance_lock(timeout=0.1):
                pass
        assert error.value.code == 'STORAGE_PATH_INVALID'
    finally:
        get_settings.cache_clear()


def test_apply_replays_stale_snapshot_detaches_and_ledger(db, tmp_path):
    owner_principal, owner_account = _account(db, 'stale-owner')
    _, account = _account(db, 'stale')
    request = DeletionRequest(account_id=account.id, status='REQUESTED')
    _, other_account = _account(db, 'survivor')
    order = _order_graph(db, owner_principal, owner_account, 'stale')
    staff = _staff(db, 'stale')
    wage = WageEntry(
        staff_id=staff.id,
        order_id=order.id,
        source_key=f'wage-{uuid4().hex}',
        kind='ORDER',
        amount_cents=75,
        period='2026-10',
    )
    entry = _trash(db, target_id=account.id, target_type='ACCOUNT')
    db.add_all([request, wage])
    db.flush()
    file_key = 'artifacts/aa/stale.pdf'
    file_path = Path(get_settings().storage_root) / file_key
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b'old')
    db.commit()

    account_id, request_id = account.id, request.id
    other_id, wage_id, entry_id = other_account.id, wage.id, entry.id
    journal_entry = {
        'version': 2,
        'trashId': entry_id,
        'targetType': 'ACCOUNT',
        'targetId': account_id,
        'purgedAt': '2026-10-30T00:00:00Z',
        'updatedAt': '2026-10-30T00:00:00Z',
        'rows': [
            {'table': 'deletion_requests', 'id': request_id},
            {'table': 'customer_accounts', 'id': account_id},
        ],
        'detaches': [{'table': 'wage_entries', 'column': 'order_id', 'id': wage_id}],
        'files': [file_key],
        'ledger': {
            'bucket': f'trash:{entry_id}',
            'totals': {'orderCount': 1, 'payableCents': 75},
        },
    }
    result = _run_apply(db, tmp_path, [journal_entry])
    assert result.returncode == 0, result.stderr
    assert 'TOMBSTONE_OK' in result.stdout
    db.rollback()

    assert db.get(Account, account_id) is None
    assert db.get(DeletionRequest, request_id) is None
    assert db.get(Account, other_id) is not None
    assert db.get(WageEntry, wage_id).order_id is None
    assert not file_path.exists()
    stored = db.get(TrashEntry, entry_id)
    assert stored is not None
    assert stored.status == 'PURGED'
    assert stored.data['tombstoneReplay'] is True
    assert stored.data['rowCount'] == 2
    assert stored.data['fileCount'] == 1
    assert stored.data['purgedFiles'] == [file_key]
    ledger = db.scalar(select(AnonymousLedger).where(AnonymousLedger.bucket == f'trash:{entry_id}'))
    assert ledger is not None
    assert ledger.totals == {'orderCount': 1, 'payableCents': 75}
    audits = db.scalars(select(AuditLog).where(AuditLog.action == 'RESTORE_LEDGER_PRESERVED')).all()
    assert len(audits) == 1
    assert audits[0].data == {'bucket': f'trash:{entry.id}'}


@pytest.mark.parametrize(
    'table',
    [
        'wage_policies',
        'wage_entries',
        'wage_settlements',
        'wage_settlement_items',
        'supplementary_charges',
        'supplementary_movements',
        'supplementary_disputes',
    ],
)
def test_apply_whitelists_wage_and_supplementary_tables(db, tmp_path, table):
    entry = {
        'version': 2,
        'trashId': f'trash-{table}',
        'targetType': 'ORDER',
        'targetId': 'x',
        'rows': [{'table': table, 'id': 'missing-row'}],
        'files': [],
    }
    result = _run_apply(db, tmp_path, [entry], dry_run=True)
    assert result.returncode == 0, result.stderr
    assert 'TOMBSTONE_OK' in result.stdout


def test_apply_rejects_unknown_table(db, tmp_path):
    entry = {
        'version': 2,
        'trashId': 'trash-bad',
        'targetType': 'ORDER',
        'targetId': 'x',
        'rows': [{'table': 'pg_shadow', 'id': 'x'}],
        'files': [],
    }
    result = _run_apply(db, tmp_path, [entry], dry_run=True)
    assert result.returncode != 0
    assert 'TOMBSTONE_INVALID' in result.stderr


def test_apply_waits_for_the_same_maintenance_lock(db, tmp_path):
    env = _subprocess_env(db)
    env['DORMPRINT_MAINTENANCE_LOCK_TIMEOUT_SECONDS'] = '0.2'
    with maintenance_lock(timeout=5):
        result = _run_apply(db, tmp_path, [], env=env)
    assert result.returncode == 4
    assert 'maintenance lock busy' in result.stderr


def test_export_writes_authority_and_never_pii(db, tmp_path):
    _, account = _account(db, 'export')
    staff = _staff(db, 'export')
    entry = _trash(db, target_id='export-target', target_type='ORDER')
    entry.status = 'PURGED'
    entry.data = {
        'purgedAt': '2026-10-30T00:00:00Z',
        'purgedRows': [{'table': 'orders', 'id': 'order-export'}],
        'purgedFiles': ['artifacts/aa/export.pdf'],
        'purgedDetaches': [{'table': 'wage_entries', 'column': 'order_id', 'id': 'wage-1'}],
        'name': 'Must Not Leak',
        'email': 'leak@example.test',
    }
    db.commit()
    wrapper = _psql_wrapper(tmp_path)
    output = tmp_path / 'export.jsonl'
    cumulative = tmp_path / 'cumulative.jsonl'
    result = subprocess.run(
        [
            sys.executable,
            str(APPLY),
            'export',
            '--database',
            _url().database,
            '--psql',
            str(wrapper),
            '--output',
            str(output),
            '--cumulative',
            str(cumulative),
        ],
        cwd=REPO,
        env=_subprocess_env(db),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert 'TOMBSTONE_EXPORT_OK' in result.stdout
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    lines = [json.loads(line) for line in output.read_text().splitlines() if line.strip()]
    authority = [line for line in lines if line.get('kind') == 'AUTHORITY']
    assert len(authority) == 1
    facts = authority[0]['facts']
    assert facts['staff'][0]['id'] == staff.id
    assert facts['accounts'][0]['id'] == account.id
    encoded = json.dumps(facts, ensure_ascii=False).lower()
    for forbidden in ('"email"', '"phone"', '"password"', '"displayname"', '"name"'):
        assert forbidden not in encoded
    assert 'leak@example.test' not in encoded
    assert '13800000000' not in encoded
    deletion = [line for line in lines if line.get('trashId') == entry.id]
    assert len(deletion) == 1
    assert deletion[0]['rows'] == entry.data['purgedRows']
    assert deletion[0]['files'] == entry.data['purgedFiles']
    assert deletion[0]['detaches'] == entry.data['purgedDetaches']
    assert 'name' not in deletion[0] and 'email' not in deletion[0]


def test_export_includes_committed_gc_failed_pending_but_not_empty_pending(
        db, tmp_path, monkeypatch):
    from app import storage_coordination

    storage = Path(get_settings().storage_root)
    key = 'artifacts/export/pending.pdf'
    target = storage / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'pending-private-file')
    log = AuditLog(actor_id='system', action='PURGE_EXPORT_TEST',
                   target_type='ASSET', target_id='pending-asset')
    db.add(log)
    db.flush()
    entry = _trash(db, target_id=log.id, target_type='AUDIT_LOG')
    empty = _trash(db, target_id='never-executed', target_type='ASSET')
    empty.status = 'PURGE_PENDING'
    empty.data = {
        'purgedAt': '2026-10-30T00:00:00Z',
        'purgedRows': [],
        'purgedFiles': ['artifacts/export/empty.pdf'],
        'cleanupPending': True,
        'stagedFiles': [{'key': 'artifacts/export/empty.pdf',
                         'staged': 'deadbeef'}],
    }
    db.commit()

    entry_id, empty_id, log_id = entry.id, empty.id, log.id

    def fail_gc(root, entry_dir, staged):
        return [item.get('key') for item in staged]

    monkeypatch.setattr(storage_coordination, '_gc_staged', fail_gc)
    with purge_guard(db, entry, files=[key]):
        db.execute(delete(AuditLog).where(AuditLog.id == log_id))
    db.commit()
    db.expire_all()
    pending = db.get(TrashEntry, entry_id)
    assert pending.status == 'PURGE_PENDING'
    assert pending.data['cleanupPending'] is True
    assert pending.data['purgedRows'] == [{'table': 'audit_logs', 'id': log_id}]
    assert pending.data['purgedFiles'] == [key]

    output = tmp_path / 'fresh-export.jsonl'
    result = _run_export(db, tmp_path, output)
    assert result.returncode == 0, result.stderr
    lines = [json.loads(line) for line in output.read_text().splitlines() if line.strip()]
    exported = [line for line in lines if line.get('trashId') == entry_id]
    assert len(exported) == 1
    assert exported[0]['rows'] == [{'table': 'audit_logs', 'id': log_id}]
    assert exported[0]['files'] == [key]
    assert not [line for line in lines if line.get('trashId') == empty_id]
    assert target.exists(), 'GC failure must leave the original for retry'


def test_export_rejects_purged_tombstone_with_empty_rows(db, tmp_path):
    entry = _trash(db, target_id='purged-empty', target_type='ASSET')
    entry.status = 'PURGED'
    entry.data = {
        'purgedAt': '2026-10-30T00:00:00Z',
        'purgedRows': [],
        'purgedFiles': [],
    }
    db.commit()

    result = _run_export(db, tmp_path, tmp_path / 'invalid-export.jsonl')
    assert result.returncode == 3, result.stderr
    assert 'TOMBSTONE_INCOMPLETE' in result.stderr
    assert not (tmp_path / 'invalid-export.jsonl').exists()


def _authority_table(db, facts, latest=True):
    db.execute(
        text(
            'CREATE TABLE IF NOT EXISTS _dormprint_restore_authority ('
            'kind text NOT NULL, id text NOT NULL, facts jsonb NOT NULL, '
            'latest boolean NOT NULL DEFAULT false, PRIMARY KEY (kind, id))'
        )
    )
    db.execute(
        text(
            "INSERT INTO _dormprint_restore_authority (kind, id, facts, latest) "
            "VALUES ('AUTHORITY', '__authority__', CAST(:facts AS jsonb), :latest) "
            'ON CONFLICT (kind, id) DO UPDATE SET facts = EXCLUDED.facts, latest = EXCLUDED.latest'
        ),
        {'facts': json.dumps(facts), 'latest': latest},
    )


def _restore_fixture(db):
    _, account = _account(db, 'restore')
    normal = _staff(db, 'restore-normal')
    super_admin = _staff(db, 'restore-super', super_admin=True)
    order = _order_graph(db, db.get(Principal, account.principal_id), account, 'restore')
    session = AuthSession(
        kind='ACCOUNT',
        token_hash=uuid4().hex + uuid4().hex,
        principal_id=account.principal_id,
        account_id=account.id,
        expires_at=_now() + timedelta(days=1),
    )
    grant = AccessGrant(
        order_id=order.id,
        account_id=account.id,
        token_hash=uuid4().hex + uuid4().hex,
        expires_at=_now() + timedelta(days=1),
    )
    token = OneTimeToken(
        kind='VERIFY_EMAIL',
        token_hash=uuid4().hex + uuid4().hex,
        account_id=account.id,
        expires_at=_now() + timedelta(days=1),
    )
    idem = IdempotencyRecord(
        actor_key='actor',
        route='/route',
        key=uuid4().hex,
        request_hash='hash',
        status_code=200,
        expires_at=_now() + timedelta(hours=1),
    )
    job = Job(
        kind='PURGE',
        business_key=f'job-{uuid4().hex}',
        target_id='x',
        status='PROCESSING',
        lease_until=_now() + timedelta(minutes=5),
        lease_token='lease',
    )
    email = EmailJob(
        business_key=f'email-{uuid4().hex}',
        template_key='verify',
        status='QUEUED',
        protected_payload='encrypted-secret',
        expires_at=_now() + timedelta(days=1),
        data={'email': 'leak@example.test', 'phone': '13800000000', 'name': 'Leak', 'keep': 1},
    )
    db.add_all([session, grant, token, idem, job, email])
    db.flush()
    return account, normal, super_admin, session, grant, token, idem, job, email


def test_post_restore_fails_closed_without_latest_authority(db, tmp_path):
    account, normal, super_admin, session, grant, token, idem, job, email = _restore_fixture(db)
    normal_id, super_id = normal.id, super_admin.id
    session_id, grant_id, token_id = session.id, grant.id, token.id
    idem_id, job_id, email_id = idem.id, job.id, email.id
    db.commit()
    result = _run_post_restore(db, tmp_path)
    assert result.returncode == 0, result.stderr
    db.rollback()
    assert db.get(Staff, normal_id).enabled is False
    assert db.get(Staff, super_id).enabled is False
    assert db.get(AuthSession, session_id).revoked_at is not None
    assert db.get(AccessGrant, grant_id).revoked_at is not None
    assert db.get(OneTimeToken, token_id) is None
    assert db.get(IdempotencyRecord, idem_id) is None
    assert db.get(Job, job_id).status == 'QUEUED'
    assert db.get(Job, job_id).lease_token is None
    assert db.get(EmailJob, email_id).protected_payload is None
    assert db.get(EmailJob, email_id).data == {'keep': 1}


def test_post_restore_overlays_latest_authority_but_never_super_admin(db, tmp_path):
    account, normal, super_admin, session, grant, token, idem, job, email = _restore_fixture(db)
    _authority_table(
        db,
        {
            'staff': [
                {
                    'id': normal.id,
                    'enabled': True,
                    'status': 'ACTIVE',
                    'role': 'ADMIN',
                    'isSuperAdmin': False,
                    'capabilities': ['orders.read'],
                    'sessionVersion': 4,
                },
                {
                    'id': super_admin.id,
                    'enabled': True,
                    'status': 'ACTIVE',
                    'role': 'ADMIN',
                    'isSuperAdmin': True,
                    'capabilities': ['*'],
                    'sessionVersion': 9,
                },
            ],
            'accounts': [
                {
                    'id': account.id,
                    'status': 'ACTIVE',
                    'sessionVersion': 7,
                    'deletedAt': None,
                }
            ],
        },
        latest=True,
    )
    db.commit()
    result = _run_post_restore(db, tmp_path)
    assert result.returncode == 0, result.stderr
    db.rollback()
    normal_after = db.get(Staff, normal.id)
    assert normal_after.enabled is True
    assert normal_after.status == 'ACTIVE'
    assert normal_after.capabilities == ['orders.read']
    super_after = db.get(Staff, super_admin.id)
    assert super_after.enabled is False, 'super admin must require explicit bootstrap review'
    account_after = db.get(Account, account.id)
    assert account_after.status == 'ACTIVE'
    assert account_after.session_version >= 7
    assert db.get(AuthSession, session.id).revoked_at is not None
    assert db.get(EmailJob, email.id).protected_payload is None
    assert db.get(EmailJob, email.id).data == {'keep': 1}


def _asset(db, key: str, suffix='gc'):
    asset = Asset(
        purpose='PRINT_SOURCE',
        name=f'{suffix}.pdf',
        mime='application/pdf',
        size=3,
        sha256='a' * 64,
        storage_key=key,
    )
    db.add(asset)
    db.flush()
    return asset


def test_storage_gc_guard_stages_asset_until_outer_commit(db):
    storage = Path(get_settings().storage_root)
    key = 'artifacts/gc/expired.pdf'
    target = storage / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'pdf')
    asset = _asset(db, key)
    db.commit()

    with storage_gc_guard(db):
        assert purge_storage_key_locked(key) is True
        asset.deleted_at = _now()
        db.flush()

    gc_root = storage / '.coordination' / 'gc'
    manifests = [path for path in gc_root.iterdir() if path.is_dir()]
    assert len(manifests) == 1
    manifest_id = manifests[0].name
    assert target.exists(), 'old DB snapshot must keep resolving before commit'
    blocked = _lock_probe(storage)
    assert blocked.returncode != 0
    assert 'DomainError' in blocked.stderr and '备份或恢复' in blocked.stderr

    db.commit()
    db.expire_all()

    assert db.get(Asset, asset.id).deleted_at is not None
    assert not target.exists()
    assert not manifests[0].exists(), 'committed GC manifest must be removed'
    marker = db.scalar(select(AuditLog).where(
        AuditLog.action == 'PRIVATE_FILE_GC_COMMITTED',
        AuditLog.target_id == manifest_id,
    ))
    assert marker is not None
    assert marker.data == {'manifestId': manifest_id}
    acquired = _lock_probe(storage)
    assert acquired.returncode == 0 and 'ACQUIRED' in acquired.stdout


def test_storage_gc_guard_rollback_restores_asset_file_and_flag(db):
    storage = Path(get_settings().storage_root)
    key = 'artifacts/gc/rollback.pdf'
    target = storage / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'keep')
    asset = _asset(db, key)
    db.commit()

    with storage_gc_guard(db):
        purge_storage_key_locked(key)
        asset.deleted_at = _now()
        db.flush()

    assert target.read_bytes() == b'keep'
    db.rollback()
    db.expire_all()

    assert target.read_bytes() == b'keep'
    assert db.get(Asset, asset.id).deleted_at is None
    gc_root = storage / '.coordination' / 'gc'
    assert not any(path.is_dir() for path in gc_root.iterdir())
    acquired = _lock_probe(storage)
    assert acquired.returncode == 0 and 'ACQUIRED' in acquired.stdout


def test_storage_gc_guard_recovery_uses_committed_marker_truth(db, monkeypatch):
    from app import storage_coordination

    storage = Path(get_settings().storage_root)
    committed_key = 'artifacts/gc/committed.pdf'
    rolled_key = 'artifacts/gc/rolled.pdf'
    for key, body in ((committed_key, b'committed'), (rolled_key, b'rolled')):
        target = storage / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    committed_asset = _asset(db, committed_key, 'committed')
    rolled_asset = _asset(db, rolled_key, 'rolled')
    db.commit()

    original_gc = storage_coordination._gc_staged
    calls = {'n': 0}

    def failing_gc(root, entry_dir, staged):
        calls['n'] += 1
        if calls['n'] == 1:
            return [item.get('key') for item in staged]
        return original_gc(root, entry_dir, staged)

    monkeypatch.setattr(storage_coordination, '_gc_staged', failing_gc)
    with storage_gc_guard(db):
        purge_storage_key_locked(committed_key)
        committed_asset.deleted_at = _now()
        db.flush()
    db.commit()
    db.expire_all()
    gc_root = storage / '.coordination' / 'gc'
    committed_manifest = next(path for path in gc_root.iterdir() if path.is_dir())
    assert (storage / committed_key).exists()
    assert db.get(Asset, committed_asset.id).deleted_at is not None

    original_restore = storage_coordination._restore_staged
    monkeypatch.setattr(storage_coordination, '_restore_staged', lambda *args: ['simulated'])
    with storage_gc_guard(db):
        purge_storage_key_locked(rolled_key)
        rolled_asset.deleted_at = _now()
        db.flush()
    db.rollback()
    db.expire_all()
    rolled_manifest = next(
        path for path in gc_root.iterdir() if path.is_dir() and path != committed_manifest
    )
    assert (storage / rolled_key).exists()
    assert db.get(Asset, rolled_asset.id).deleted_at is None

    monkeypatch.setattr(storage_coordination, '_gc_staged', original_gc)
    monkeypatch.setattr(storage_coordination, '_restore_staged', original_restore)
    result = storage_coordination.recover_orphaned_staging(db)
    assert result['gcCompleted'] == 1
    assert result['gcRestored'] == 1
    assert not (storage / committed_key).exists()
    assert (storage / rolled_key).read_bytes() == b'rolled'
    assert not committed_manifest.exists()
    assert not rolled_manifest.exists()


def test_storage_gc_guard_wraps_maintenance_sweep_expiry(db):
    storage = Path(get_settings().storage_root)
    key = 'artifacts/gc/sweep-expired.pdf'
    target = storage / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'sweep')
    asset = _asset(db, key, 'sweep')
    asset.expires_at = _now() - timedelta(hours=1)
    db.commit()

    result = maintenance_sweep(db)
    assert result['deletedFiles'] >= 1
    assert asset.deleted_at is not None
    assert target.exists(), 'maintenance sweep must not unlink before outer commit'
    blocked = _lock_probe(storage)
    assert blocked.returncode != 0

    db.commit()
    db.expire_all()
    assert target.exists() is False
    assert db.get(Asset, asset.id).deleted_at is not None
    assert db.scalar(select(TrashEntry).where(
        TrashEntry.target_id == asset.id,
        TrashEntry.target_type == 'ASSET',
    )) is None, 'Q29 auto-expiry must not create a fake Trash entry'


def test_maintenance_sweep_rollback_restores_expiry_file_and_flag(db):
    storage = Path(get_settings().storage_root)
    key = 'artifacts/gc/sweep-rollback.pdf'
    target = storage / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'sweep-rollback')
    asset = _asset(db, key, 'sweep-rollback')
    asset.expires_at = _now() - timedelta(hours=1)
    db.commit()
    asset_id = asset.id

    result = maintenance_sweep(db)
    assert result['deletedFiles'] >= 1
    assert target.exists(), 'sweep must not unlink before the caller commits'
    assert db.get(Asset, asset_id).deleted_at is not None

    db.rollback()
    db.expire_all()
    fresh = db.get(Asset, asset_id)
    assert fresh.deleted_at is None
    assert target.read_bytes() == b'sweep-rollback'
    assert _lock_probe(storage).returncode == 0, 'rollback must release the file lock'


def test_storage_gc_guard_worker_thread_commit_main_thread_releases_lock(db):
    storage = Path(get_settings().storage_root)
    key = 'artifacts/gc/cross-thread.pdf'
    target = storage / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'pdf')
    asset = _asset(db, key)
    db.commit()

    result = {}

    def worker():
        session = SessionLocal()
        try:
            row = session.get(Asset, asset.id)
            with storage_gc_guard(session):
                assert purge_storage_key_locked(key) is True
                row.deleted_at = _now()
                session.flush()
            assert target.exists(), 'pre-commit snapshot must keep resolving'
            blocked = _lock_probe(storage)
            assert blocked.returncode != 0
            result['session'] = session
        except BaseException as exc:  # noqa: BLE001 - re-raised in the test thread
            session.rollback()
            session.close()
            result['error'] = exc

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=20)
    assert not thread.is_alive()
    assert 'error' not in result, result.get('error')

    # The session is used sequentially: created and guarded in the worker
    # thread, then committed by the middleware-equivalent thread.
    session = result['session']
    session.commit()
    session.close()

    db.expire_all()
    assert db.get(Asset, asset.id).deleted_at is not None
    assert not target.exists()
    acquired = _lock_probe(storage)
    assert acquired.returncode == 0, acquired.stderr
    assert 'ACQUIRED' in acquired.stdout

    # A later request that happens to reuse the same worker thread must create
    # a fresh real OS lock, not reuse the closed FD from the previous request.
    reused = {}

    def next_worker():
        next_session = SessionLocal()
        try:
            with maintenance_lock(timeout=0.3) as lock:
                assert lock.fd >= 0
                blocked = _lock_probe(storage)
            reused['blocked'] = blocked
            reused['acquired'] = _lock_probe(storage)
        finally:
            next_session.close()

    thread = threading.Thread(target=next_worker)
    thread.start()
    thread.join(timeout=20)
    assert not thread.is_alive()
    assert reused['blocked'].returncode != 0, 'held lock must block a fresh process'
    assert reused['acquired'].returncode == 0, 'lock must be released on context exit'


def test_manual_purge_http_route_releases_lock_across_worker_and_event_loop(db, admin_client, api):
    storage = Path(get_settings().storage_root)
    key = 'artifacts/gc/http-route.pdf'
    target = storage / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'pdf')
    asset = _asset(db, key)
    db.commit()

    created = api(admin_client, '/admin/trash', {
        'targetType': 'ASSET', 'targetId': asset.id, 'expectedVersion': asset.version,
        'reason': '跨线程永久清理',
    })
    assert created.status_code == 200, created.text
    entry = created.json()['data']

    purged = api(admin_client, f"/admin/trash/{entry['id']}/purge", {
        'expectedVersion': entry['version'], 'reason': '永久清理',
    })
    assert purged.status_code == 200, purged.text
    assert purged.json()['data']['status'] == 'PURGE_PENDING'
    assert not target.exists()

    # The sync route ran in a worker thread and the middleware committed the
    # session on the event-loop thread. The lock must already be free here.
    acquired = _lock_probe(storage)
    assert acquired.returncode == 0, acquired.stderr
    assert 'ACQUIRED' in acquired.stdout

    next_response = api(admin_client, '/admin/trash', method='GET')
    assert next_response.status_code == 200, next_response.text
    # PURGED tombstones are intentionally hidden from the active trash list;
    # after the middleware commit the authoritative DB row is PURGED.
    db.expire_all()
    stored = db.get(TrashEntry, entry['id'])
    assert stored.status == 'PURGED'
    job = db.scalar(select(Job).where(Job.business_key == f"purge:{entry['id']}"))
    assert job is not None
    assert job.target_id == entry['id']


def test_process_purge_rejects_concurrent_stale_expected_version(db):
    asset = _asset(db, 'artifacts/gc/stale-version.pdf')
    entry = _trash(db, target_id=asset.id, target_type='ASSET')
    db.commit()
    entry_id = entry.id
    stale_version = entry.version

    # A concurrent transaction commits a newer TrashEntry version after this
    # Session already holds the row.  core.lock must refresh the non-dirty
    # target and compare expectedVersion against the committed row.
    with SessionLocal() as other:
        row = other.get(TrashEntry, entry_id)
        row.version += 1
        row.data = {**(row.data or {}), 'concurrent': 'update'}
        other.commit()

    try:
        with pytest.raises(DomainError) as exc:
            process_purge_job(db, entry_id, expected_version=stale_version)
        assert exc.value.code == 'VERSION_CONFLICT'
    finally:
        db.rollback()


def test_manual_purge_http_gc_failure_serializes_pending_and_truthful_get(
        db, admin_client, api, monkeypatch):
    from app import storage_coordination

    storage = Path(get_settings().storage_root)
    key = 'artifacts/gc/http-pending.pdf'
    target = storage / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'pending-pdf')
    asset = _asset(db, key)
    asset_id = asset.id
    db.commit()

    created = api(admin_client, '/admin/trash', {
        'targetType': 'ASSET', 'targetId': asset_id, 'expectedVersion': asset.version,
        'reason': 'GC 失败回归',
    })
    assert created.status_code == 200, created.text
    entry = created.json()['data']

    original_gc = storage_coordination._gc_staged

    def fail_gc(root, entry_dir, staged):
        return [item.get('key') for item in staged]

    monkeypatch.setattr(storage_coordination, '_gc_staged', fail_gc)
    purge_key = f'purge-gc-failure-{uuid4().hex}'
    purged = api(admin_client, f"/admin/trash/{entry['id']}/purge", {
        'expectedVersion': entry['version'], 'reason': 'GC 失败回归',
    }, key=purge_key)
    assert purged.status_code == 200, purged.text
    assert purged.json()['data']['status'] == 'PURGE_PENDING'
    assert target.exists(), 'failed GC must not unlink the source'

    # The middleware stores the idempotent body before committing. A GC
    # failure is only known in the commit callback, so the stored body must
    # already be the conservative PENDING status, never PURGED.
    replay = api(admin_client, f"/admin/trash/{entry['id']}/purge", {
        'expectedVersion': entry['version'], 'reason': 'GC 失败回归',
    }, key=purge_key)
    assert replay.status_code == 200, replay.text
    assert replay.json()['data']['status'] == 'PURGE_PENDING'

    db.expire_all()
    stored = db.get(TrashEntry, entry['id'])
    assert stored.status == 'PURGE_PENDING'
    assert stored.data['cleanupPending'] is True
    assert stored.data['cleanupError'] == 'STAGING_GC_FAILED'

    next_response = api(admin_client, '/admin/trash', method='GET')
    assert next_response.status_code == 200, next_response.text
    items = next_response.json()['data']['items']
    listed = next(item for item in items if item['id'] == entry['id'])
    assert listed['status'] == 'PURGE_PENDING'
    assert db.get(Asset, asset_id) is None, 'committed purge graph must be gone'

    # The first response was serialized before commit; its PENDING status is
    # the truthful accepted/request state even though the graph is committed.
    # A worker retry recovers the quarantine and finishes the tombstone.
    monkeypatch.setattr(storage_coordination, '_gc_staged', original_gc)
    retry = api(admin_client, f"/admin/trash/{entry['id']}/purge", {
        'expectedVersion': stored.version, 'reason': 'GC 失败恢复',
    }, key=f'purge-gc-retry-{uuid4().hex}')
    assert retry.status_code == 200, retry.text
    assert retry.json()['data']['status'] == 'PURGED'
    assert not target.exists(), 'retried GC must collect the staged source'

    db.expire_all()
    recovered = db.get(TrashEntry, entry['id'])
    assert recovered.status == 'PURGED'
    assert recovered.data.get('cleanupPending') is None
    assert recovered.data.get('stagedFiles') is None
    assert recovered.data['purgedRows'] == [{'table': 'assets', 'id': asset_id}]
    assert _lock_probe(storage).returncode == 0

    already = api(admin_client, f"/admin/trash/{entry['id']}/purge", {
        'expectedVersion': recovered.version, 'reason': '已清理重试',
    }, key=f'purge-gc-already-{uuid4().hex}')
    assert already.status_code == 200, already.text
    assert already.json()['data']['status'] == 'PURGED'


def test_account_deletion_http_stages_export_file_until_commit(db, admin_client, api):
    storage = Path(get_settings().storage_root)
    key = 'exports/account-deletion.csv'
    target = storage / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'private-export')
    _, account = _account(db, 'delete-export')
    export = ExportJob(
        staff_id=None,
        account_id=account.id,
        kind='PERSONAL_DATA',
        status='READY',
        storage_key=key,
        expires_at=_now() + timedelta(hours=1),
    )
    request = DeletionRequest(
        account_id=account.id,
        status='REQUESTED',
        data={'reason': '测试注销导出文件', 'requestedAt': _now().isoformat(),
              'blockers': []},
    )
    db.add_all([export, request])
    db.commit()

    response = api(admin_client, f'/admin/deletion-requests/{request.id}/approve', {
        'expectedVersion': request.version, 'reason': '审核注销',
    })
    assert response.status_code == 200, response.text
    db.expire_all()
    assert not target.exists(), 'committed account deletion must collect the export file'
    assert db.get(ExportJob, export.id).storage_key is None
    assert _lock_probe(storage).returncode == 0


def test_purge_guard_worker_thread_commit_main_thread_finalizes_files_and_lock(db):
    storage = Path(get_settings().storage_root)
    key = 'artifacts/gc/cross-thread-purge.pdf'
    target = storage / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'purge')
    log = AuditLog(actor_id='system', action='TEST_PURGE_TARGET', target_type='TEST', target_id='x')
    db.add(log)
    db.flush()
    entry = _trash(db, target_id=log.id, target_type='AUDIT_LOG')
    db.commit()
    log_id = log.id
    entry_id = entry.id

    result = {}

    def worker():
        session = SessionLocal()
        try:
            row = session.get(TrashEntry, entry_id)
            with purge_guard(session, row, files=[key]):
                session.execute(delete(AuditLog).where(AuditLog.id == log.id))
            assert target.exists(), 'pre-commit snapshot must keep resolving'
            blocked = _lock_probe(storage)
            assert blocked.returncode != 0
            result['session'] = session
        except BaseException as exc:  # noqa: BLE001
            session.rollback()
            session.close()
            result['error'] = exc

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=20)
    assert not thread.is_alive()
    assert 'error' not in result, result.get('error')

    session = result['session']
    session.commit()
    session.close()

    db.expire_all()
    assert db.get(AuditLog, log_id) is None
    assert db.get(TrashEntry, entry_id).status == 'PURGED'
    assert not target.exists()
    acquired = _lock_probe(storage)
    assert acquired.returncode == 0, acquired.stderr
    assert 'ACQUIRED' in acquired.stdout


def test_storage_gc_guard_stages_tree_without_following_symlinks(db):
    storage = Path(get_settings().storage_root)
    tree = storage / 'sessions' / 'cross-thread-tree'
    tree.mkdir(parents=True)
    (tree / 'part.bin').write_bytes(b'tree-part')
    outside = storage / 'outside-secret.bin'
    outside.write_bytes(b'do-not-copy-through-link')
    link = tree / 'external-link'
    os.symlink(outside, link)

    with storage_gc_guard(db):
        assert purge_storage_tree_locked('sessions/cross-thread-tree')
        assert tree.exists()
        assert link.is_symlink()
        assert link.read_bytes() == b'do-not-copy-through-link'
        assert _lock_probe(storage).returncode != 0

    db.commit()
    assert not tree.exists()
    assert outside.read_bytes() == b'do-not-copy-through-link'
    assert _lock_probe(storage).returncode == 0


def test_purge_guard_install_failure_releases_lock(db, monkeypatch):
    storage = Path(get_settings().storage_root)
    entry = _trash(db)
    db.commit()

    def broken_install(self):
        raise RuntimeError('install failed')

    monkeypatch.setattr('app.storage_coordination._PurgeCapture.install', broken_install)
    with pytest.raises(RuntimeError):
        with purge_guard(db, entry):
            pass
    db.rollback()
    probe = _lock_probe(storage)
    assert probe.returncode == 0, probe.stderr


def test_purge_guard_retry_preserves_original_tombstone(db):
    entry = _trash(db, target_id='missing-order', target_type='ORDER')
    old_at = '2026-01-02T03:04:05Z'
    entry.data = {
        'name': 'must be dropped',
        'email': 'must-not-survive@example.test',
        'purgedAt': old_at,
        'purgedRows': [{'table': 'customer_accounts', 'id': 'old-account'}],
        'purgedDetaches': [
            {'table': 'wage_entries', 'column': 'order_id', 'id': 'old-wage'}
        ],
        'purgedFiles': ['artifacts/old/final.pdf'],
        'ledgerBucket': f'trash:{entry.id}',
        'ledgerTotals': {'deletedOrderCents': 123},
    }
    db.flush()

    with purge_guard(db, entry):
        entry.status = 'PURGED'
    db.commit()
    db.expire_all()
    fresh = db.get(TrashEntry, entry.id)

    assert fresh.status == 'PURGED'
    assert fresh.data['purgedAt'] == old_at
    assert fresh.data['purgedRows'] == [
        {'table': 'customer_accounts', 'id': 'old-account'}
    ]
    assert fresh.data['purgedDetaches'] == [
        {'table': 'wage_entries', 'column': 'order_id', 'id': 'old-wage'}
    ]
    assert fresh.data['purgedFiles'] == ['artifacts/old/final.pdf']
    assert fresh.data['ledgerTotals'] == {'deletedOrderCents': 123}
    assert 'name' not in fresh.data
    assert 'email' not in fresh.data


def test_purge_guard_retry_keeps_pending_cleanup_visible(db):
    entry = _trash(db, target_id='missing-asset', target_type='ASSET')
    entry.status = 'PURGE_PENDING'
    entry.data = {
        'purgedAt': '2026-02-03T04:05:06Z',
        'purgedRows': [{'table': 'assets', 'id': 'old-asset'}],
        'purgedDetaches': [
            {'table': 'wage_entries', 'column': 'assignment_id', 'id': 'old-wage'}
        ],
        'ledgerTotals': {'assetCount': 1},
        'cleanupPending': True,
        'cleanupError': 'STAGING_GC_FAILED',
    }
    db.flush()

    with purge_guard(db, entry):
        entry.status = 'PURGED'
    db.commit()
    db.expire_all()
    fresh = db.get(TrashEntry, entry.id)

    assert fresh.status == 'PURGE_PENDING'
    assert fresh.data['cleanupPending'] is True
    assert fresh.data['cleanupError'] == 'STAGING_GC_FAILED'
    assert fresh.data['purgedRows'] == [{'table': 'assets', 'id': 'old-asset'}]
    assert fresh.data['purgedDetaches'] == [
        {'table': 'wage_entries', 'column': 'assignment_id', 'id': 'old-wage'}
    ]
    assert fresh.data['ledgerTotals'] == {'assetCount': 1}


def test_recover_purge_cleanup_completes_staged_pending_without_wipe(db, monkeypatch):
    storage = Path(get_settings().storage_root)
    key = 'artifacts/retry/keep.pdf'
    target = storage / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'retry-private-file')

    entry = _trash(db, target_id='missing-asset', target_type='ASSET')
    entry.status = 'PURGE_PENDING'
    entry.data = {
        'purgedAt': '2026-03-04T05:06:07Z',
        'purgedRows': [{'table': 'assets', 'id': 'old-asset'}],
        'purgedFiles': [key],
    }
    db.add(entry)
    db.add(AnonymousLedger(bucket=f'trash:{entry.id}', totals={'assetCount': 1}))
    db.flush()

    def fail_gc(root, entry_dir, staged):
        return [item['key'] for item in staged]

    monkeypatch.setattr('app.storage_coordination._gc_staged', fail_gc)
    with purge_guard(db, entry, files=[key]):
        entry.status = 'PURGED'
    db.commit()
    db.expire_all()

    pending = db.get(TrashEntry, entry.id)
    assert pending.status == 'PURGE_PENDING'
    assert pending.data['cleanupPending'] is True
    assert target.read_bytes() == b'retry-private-file'
    assert (storage / '.coordination' / 'staging' / entry.id).is_dir()

    monkeypatch.undo()
    assert recover_purge_cleanup(db, pending) is True
    db.commit()
    db.expire_all()
    final = db.get(TrashEntry, entry.id)

    assert final.status == 'PURGED'
    assert final.data['purgedAt'] == '2026-03-04T05:06:07Z'
    assert final.data['purgedRows'] == [{'table': 'assets', 'id': 'old-asset'}]
    assert final.data['purgedFiles'] == [key]
    assert final.data['ledgerTotals'] == {'assetCount': 1}
    assert 'cleanupPending' not in final.data
    assert not target.exists()


def test_recover_purge_cleanup_fails_closed_without_staging(db):
    entry = _trash(db, target_id='missing-asset', target_type='ASSET')
    entry.status = 'PURGE_PENDING'
    entry.data = {
        'purgedAt': '2026-04-05T06:07:08Z',
        'purgedRows': [{'table': 'assets', 'id': 'old-asset'}],
        'cleanupPending': True,
    }
    db.flush()

    with pytest.raises(DomainError) as exc:
        recover_purge_cleanup(db, entry)
    assert exc.value.code == 'PURGE_STORAGE_PENDING'
    assert exc.value.details == {'reason': 'STAGING_MISSING'}

    db.expire_all()
    fresh = db.get(TrashEntry, entry.id)
    assert fresh.status == 'PURGE_PENDING'
    assert fresh.data['cleanupPending'] is True
    assert fresh.data['purgedRows'] == [{'table': 'assets', 'id': 'old-asset'}]


def test_recover_orphaned_staging_keeps_unmanifested_pending(db):
    storage = Path(get_settings().storage_root)
    entry = _trash(db, target_id='missing-asset', target_type='ASSET')
    entry.status = 'PURGE_PENDING'
    entry.data = {
        'purgedRows': [{'table': 'assets', 'id': 'old-asset'}],
        'cleanupPending': True,
    }
    db.flush()
    entry_dir = storage / '.coordination' / 'staging' / entry.id
    entry_dir.mkdir(parents=True, exist_ok=True)
    garbage = entry_dir / 'unmanifested.bin'
    garbage.write_bytes(b'owned-garbage')

    result = recover_orphaned_staging(db)
    db.flush()
    assert result['pending'] == 1
    assert result['completed'] == 0
    assert garbage.read_bytes() == b'owned-garbage'

    db.expire_all()
    fresh = db.get(TrashEntry, entry.id)
    assert fresh.status == 'PURGE_PENDING'
    assert fresh.data['cleanupError'] == 'STAGING_MANIFEST_MISSING'
    assert fresh.data['purgedRows'] == [{'table': 'assets', 'id': 'old-asset'}]


def test_process_purge_job_target_missing_keeps_pending_tombstone(db):
    entry = _trash(db, target_id='missing-asset', target_type='ASSET')
    entry.status = 'PURGE_PENDING'
    entry.data = {
        'purgedAt': '2026-05-06T07:08:09Z',
        'purgedRows': [{'table': 'assets', 'id': 'old-asset'}],
        'purgedDetaches': [
            {'table': 'wage_entries', 'column': 'order_id', 'id': 'old-wage'}
        ],
        'ledgerTotals': {'assetCount': 1},
        'cleanupPending': True,
    }
    db.flush()

    process_purge_job(db, entry.id)
    db.commit()
    db.expire_all()
    fresh = db.get(TrashEntry, entry.id)

    assert fresh.status == 'PURGE_PENDING'
    assert fresh.data['cleanupPending'] is True
    assert fresh.data['purgedAt'] == '2026-05-06T07:08:09Z'
    assert fresh.data['purgedRows'] == [{'table': 'assets', 'id': 'old-asset'}]
    assert fresh.data['purgedDetaches'] == [
        {'table': 'wage_entries', 'column': 'order_id', 'id': 'old-wage'}
    ]
    assert fresh.data['ledgerTotals'] == {'assetCount': 1}


def test_recover_purge_cleanup_rejects_purged_with_pending_garbage(db):
    entry = _trash(db, target_id='missing-asset', target_type='ASSET')
    entry.status = 'PURGED'
    entry.data = {
        'purgedRows': [{'table': 'assets', 'id': 'old-asset'}],
        'cleanupPending': True,
    }
    db.flush()

    with pytest.raises(DomainError) as exc:
        recover_purge_cleanup(db, entry)
    assert exc.value.code == 'PURGE_STORAGE_PENDING'
    assert exc.value.details == {'reason': 'STAGING_MISSING'}
