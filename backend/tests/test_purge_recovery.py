"""PostgreSQL regressions for failed purge transactions and operator recovery."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models import DeliveryChange, DeliveryNotice, Job, Order, TrashEntry, utcnow
from tests.test_maintenance import make_order, link_asset

APP_MODULES = ['auth', 'commerce', 'exports', 'maintenance']


def trash_order(db, api, admin_client):
    order = make_order(db)
    response = api(admin_client, '/admin/trash', {
        'targetType': 'ORDER', 'targetId': order.id,
        'expectedVersion': order.version, 'reason': '清理回归测试',
    })
    assert response.status_code == 200, response.text
    return order.id, response.json()['data']['id']


def test_order_purge_removes_delivery_history_and_notifications(db, api, admin_client, tmp_path):
    from app.maintenance import process_purge_job
    from tests.test_storage_coordination import _run_apply

    order_id, entry_id = trash_order(db, api, admin_client)
    change = DeliveryChange(order_id=order_id, kind='ETA_UPDATED')
    db.add(change)
    db.flush()
    notice = DeliveryNotice(order_id=order_id, change_id=change.id,
                            dedupe_key='purge-delivery-notice')
    db.add(notice)
    db.commit()
    change_id, notice_id = change.id, notice.id

    process_purge_job(db, entry_id)
    assert db.get(Order, order_id) is None
    assert db.get(DeliveryChange, change_id) is None
    assert db.get(DeliveryNotice, notice_id) is None
    entry = db.get(TrashEntry, entry_id)
    assert entry.status == 'PURGED'
    assert {'table': 'delivery_changes', 'id': change_id} in entry.data['purgedRows']
    assert {'table': 'delivery_notices', 'id': notice_id} in entry.data['purgedRows']
    journal = {'version': 2, 'trashId': entry_id, 'targetType': 'ORDER',
               'targetId': order_id, 'purgedAt': entry.data['purgedAt'],
               'rows': entry.data['purgedRows'], 'detaches': entry.data.get('purgedDetaches', []),
               'files': []}
    # Restore the pre-purge graph, then replay the journal against real FKs.
    db.rollback()
    assert db.get(DeliveryNotice, notice_id) is not None
    db.rollback()
    replay = _run_apply(db, tmp_path, [journal])
    assert replay.returncode == 0, replay.stdout + replay.stderr
    db.expire_all()
    assert db.get(Order, order_id) is None
    assert db.get(DeliveryChange, change_id) is None
    assert db.get(DeliveryNotice, notice_id) is None
    assert db.get(TrashEntry, entry_id).status == 'PURGED'


def test_purge_flush_failure_preserves_files_and_stable_error_then_retries(
        db, api, admin_client, monkeypatch):
    from app import jobs, maintenance

    order_id, entry_id = trash_order(db, api, admin_client)
    asset, path = link_asset(db, db.get(Order, order_id))
    asset_id = asset.id
    work = Job(kind='PURGE', target_id=entry_id, business_key=f'purge:{entry_id}')
    db.add(work)
    db.commit()
    work_id = work.id
    original_delete = maintenance._delete_order_graph

    def fail_flush(session, order, context):
        # Real PostgreSQL uniqueness failure puts the Session into rollback-only.
        session.add(Job(kind='PURGE', target_id=entry_id,
                        business_key=f'purge:{entry_id}'))
        session.flush()

    with monkeypatch.context() as patch:
        patch.setattr(maintenance, '_delete_order_graph', fail_flush)
        assert jobs.run_one(('PURGE',)) is False

    db.expire_all()
    failed = db.get(Job, work_id)
    assert failed.data['failureCode'] == 'PURGE_DATABASE_FAILED'
    assert failed.status == 'QUEUED'
    assert db.get(Order, order_id) is not None
    assert db.get(TrashEntry, entry_id).status == 'DELETED'
    assert db.get(type(asset), asset_id) is not None
    assert path.exists()
    from tests.test_maintenance import storage_root
    assert not (storage_root() / '.coordination' / 'staging' / entry_id).exists()
    # Make the persisted backoff due and use the actual processor on retry.
    failed.data = {**failed.data, 'availableAt': (utcnow() - timedelta(seconds=1)).isoformat()}
    db.commit()
    assert maintenance._delete_order_graph is original_delete
    assert jobs.run_one(('PURGE',)) is True
    db.expire_all()
    assert db.get(Job, work_id).status == 'READY'
    assert db.get(TrashEntry, entry_id).status == 'PURGED'
    assert db.get(Order, order_id) is None
    assert not path.exists()


def test_commit_error_contract_never_commits_a_failed_purge_session(
        db, api, admin_client, monkeypatch):
    from app import jobs, maintenance
    from app.core import DomainError
    from sqlalchemy.exc import IntegrityError

    order_id, entry_id = trash_order(db, api, admin_client)
    work = Job(kind='PURGE', target_id=entry_id, business_key=f'purge:{entry_id}')
    db.add(work)
    db.commit()
    work_id = work.id

    def invalid_recovery(session, order, context):
        session.add(Job(kind='PURGE', target_id=entry_id, business_key=f'purge:{entry_id}'))
        try:
            session.flush()
        except IntegrityError as error:
            raise DomainError('PURGE_STORAGE_PENDING', '清理待恢复', 503, commit=True) from error

    monkeypatch.setattr(maintenance, '_delete_order_graph', invalid_recovery)
    assert jobs.run_one(('PURGE',)) is False
    db.expire_all()
    assert db.get(Job, work_id).data['failureCode'] == 'PURGE_STORAGE_PENDING'
    assert db.get(Job, work_id).status == 'QUEUED'
    assert db.get(Order, order_id) is not None


def test_batch_purge_captures_delivery_change_detach_for_restore(db, api, admin_client, tmp_path):
    from app.maintenance import process_purge_job
    from app.models import DeliveryBatch
    from tests.test_storage_coordination import _run_apply

    order = make_order(db)
    batch = DeliveryBatch(name='历史批次', status='COMPLETED',
                          starts_at=utcnow() - timedelta(hours=2), ends_at=utcnow())
    db.add(batch)
    db.flush()
    change = DeliveryChange(order_id=order.id, batch_id=batch.id, kind='ETA_UPDATED')
    db.add(change)
    db.commit()
    response = api(admin_client, '/admin/trash', {
        'targetType': 'BATCH', 'targetId': batch.id,
        'expectedVersion': batch.version, 'reason': '历史批次清理',
    })
    assert response.status_code == 200, response.text
    entry_id, change_id = response.json()['data']['id'], change.id
    batch_id, order_id = batch.id, order.id
    process_purge_job(db, entry_id)
    entry = db.get(TrashEntry, entry_id)
    assert entry.status == 'PURGED'
    assert db.get(DeliveryChange, change_id).batch_id is None
    assert {'table': 'delivery_changes', 'column': 'batch_id', 'id': change_id} in entry.data['purgedDetaches']
    journal = {'version': 2, 'trashId': entry_id, 'targetType': 'BATCH',
               'targetId': batch_id, 'purgedAt': entry.data['purgedAt'],
               'rows': entry.data['purgedRows'], 'detaches': entry.data['purgedDetaches'],
               'files': []}
    db.rollback()
    assert db.get(DeliveryChange, change_id).batch_id == batch_id
    db.rollback()
    replay = _run_apply(db, tmp_path, [journal])
    assert replay.returncode == 0, replay.stdout + replay.stderr
    db.expire_all()
    assert db.get(DeliveryBatch, batch_id) is None
    assert db.get(DeliveryChange, change_id).batch_id is None
    assert db.get(Order, order_id) is not None


@pytest.mark.parametrize('failure_code', ['gkpj', 'WORKER_PROCESSING_FAILED', 'PURGE_STORAGE_PENDING'])
def test_manual_purge_rearms_exhausted_job_before_pending_storage_retry(
        db, api, admin_client, monkeypatch, failure_code):
    from app import maintenance
    from app.core import DomainError

    order_id, entry_id = trash_order(db, api, admin_client)
    work = Job(kind='PURGE', target_id=entry_id, business_key=f'purge:{entry_id}',
               status='FAILED', attempts=10, lease_token='stale', lease_until=utcnow(),
               data={'failureCode': failure_code,
                     'availableAt': (utcnow() + timedelta(hours=1)).isoformat()})
    db.add(work)
    db.commit()
    work_id = work.id

    def pending_storage(entry, files):
        raise DomainError('TRANSIENT_IO', '暂存失败', 503)

    monkeypatch.setattr(maintenance, 'record_purged_files', pending_storage)
    response = api(admin_client, f'/admin/trash/{entry_id}/purge', {
        'expectedVersion': db.get(TrashEntry, entry_id).version, 'reason': '管理员重试',
    })
    assert response.status_code == 503, response.text
    db.expire_all()
    job = db.get(Job, work_id)
    assert job.status == 'QUEUED'
    assert job.attempts == 0
    assert job.lease_token is None and job.lease_until is None
    assert 'failureCode' not in job.data and 'availableAt' not in job.data
    assert len(list(db.scalars(select(Job).where(Job.business_key == f'purge:{entry_id}')))) == 1
    assert db.get(Order, order_id) is not None


def test_manual_success_reconciles_previously_failed_job(db, api, admin_client):
    from app.jobs import run_one

    _, entry_id = trash_order(db, api, admin_client)
    work = Job(kind='PURGE', target_id=entry_id, business_key=f'purge:{entry_id}',
               status='FAILED', attempts=10, data={'failureCode': 'gkpj'})
    db.add(work)
    db.commit()
    work_id = work.id
    response = api(admin_client, f'/admin/trash/{entry_id}/purge', {
        'expectedVersion': db.get(TrashEntry, entry_id).version, 'reason': '修复后重试',
    })
    assert response.status_code == 200, response.text
    assert run_one(('PURGE',)) is True
    db.expire_all()
    assert db.get(Job, work_id).status == 'READY'
    assert db.get(TrashEntry, entry_id).status == 'PURGED'


def test_manual_purge_preserves_active_worker_lease(db, api, admin_client):
    from app.jobs import process_claim

    _, entry_id = trash_order(db, api, admin_client)
    work = Job(kind='PURGE', target_id=entry_id, business_key=f'purge:{entry_id}',
               status='PROCESSING', attempts=2, lease_token='active-worker',
               lease_until=utcnow() + timedelta(minutes=5))
    db.add(work)
    db.commit()
    work_id = work.id
    response = api(admin_client, f'/admin/trash/{entry_id}/purge', {
        'expectedVersion': db.get(TrashEntry, entry_id).version, 'reason': '手工清理',
    })
    assert response.status_code == 200, response.text
    db.expire_all()
    job = db.get(Job, work_id)
    assert job.status == 'PROCESSING' and job.attempts == 2
    assert job.lease_token == 'active-worker'
    assert process_claim(work_id, 'active-worker') is True
    db.expire_all()
    assert db.get(Job, work_id).status == 'READY'


def test_manual_retry_keeps_domain_blockers_and_version_guard(db, api, admin_client):
    from app.models import AfterSale

    order_id, entry_id = trash_order(db, api, admin_client)
    work = Job(kind='PURGE', target_id=entry_id, business_key=f'purge:{entry_id}',
               status='FAILED', attempts=10, data={'failureCode': 'PURGE_BLOCKED'})
    db.add(work)
    db.commit()
    work_id = work.id
    version = db.get(TrashEntry, entry_id).version
    stale = api(admin_client, f'/admin/trash/{entry_id}/purge', {
        'expectedVersion': version + 1, 'reason': '过期版本',
    })
    assert stale.status_code == 409
    assert stale.json()['error']['code'] == 'VERSION_CONFLICT'
    case = AfterSale(order_id=order_id, source='ADMIN', actor_id='test',
                     responsibility='SELLER', solution='REFUND', status='REQUESTED', reason='售后')
    db.add(case)
    db.commit()
    case_id = case.id
    blocked = api(admin_client, f'/admin/trash/{entry_id}/purge', {
        'expectedVersion': version, 'reason': '尚有未结业务',
    })
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()['error']['code'] == 'PURGE_BLOCKED'
    db.expire_all()
    job = db.get(Job, work_id)
    assert job.status == 'FAILED' and job.attempts == 10
    assert job.data == {'failureCode': 'PURGE_BLOCKED'}
    db.get(AfterSale, case_id).status = 'COMPLETED'
    db.commit()
    retried = api(admin_client, f'/admin/trash/{entry_id}/purge', {
        'expectedVersion': db.get(TrashEntry, entry_id).version, 'reason': '售后结束后重试',
    })
    assert retried.status_code == 200, retried.text
    db.expire_all()
    assert db.get(Job, work_id).status == 'QUEUED'
    assert db.get(Job, work_id).attempts == 0


@pytest.mark.parametrize('failure_code,attempts,expected', [
    ('gkpj', 1, 1), ('WORKER_PROCESSING_FAILED', 1, 1),
    ('PURGE_DATABASE_FAILED', 10, 0), ('PURGE_STORAGE_PENDING', 10, 0),
    ('PURGE_BLOCKED', 1, 0), ('TRASH_RESTORED', 1, 0),
])
def test_automatic_sweep_recovers_legacy_failures_but_respects_limits(
        db, api, admin_client, failure_code, attempts, expected):
    from app.maintenance import _queue_due_trash

    _, entry_id = trash_order(db, api, admin_client)
    db.get(TrashEntry, entry_id).purge_at = utcnow() - timedelta(seconds=1)
    work = Job(kind='PURGE', target_id=entry_id, business_key=f'purge:{entry_id}',
               status='FAILED', attempts=attempts, data={'failureCode': failure_code})
    db.add(work)
    db.commit()
    assert _queue_due_trash(db, utcnow()) == expected
    db.commit()
    assert work.status == ('QUEUED' if expected else 'FAILED')
    assert work.attempts == attempts


def test_purge_database_failures_have_bounded_worker_retries(db, api, admin_client, monkeypatch):
    from app import jobs, maintenance

    _, entry_id = trash_order(db, api, admin_client)
    work = Job(kind='PURGE', target_id=entry_id, business_key=f'purge:{entry_id}', attempts=2)
    db.add(work)
    db.commit()
    work_id = work.id

    def invalid_graph(session, order, context):
        session.add(Job(kind='PURGE', target_id=entry_id, business_key=f'purge:{entry_id}'))
        session.flush()

    monkeypatch.setattr(maintenance, '_delete_order_graph', invalid_graph)
    assert jobs.run_one(('PURGE',)) is False
    db.expire_all()
    job = db.get(Job, work_id)
    assert job.status == 'FAILED' and job.attempts == 3
    assert job.data['failureCode'] == 'PURGE_DATABASE_FAILED'
    assert jobs.run_one(('PURGE',)) is None


def test_post_commit_storage_failures_stop_at_retry_limit(db, api, admin_client, monkeypatch):
    from app import jobs, storage_coordination
    from app.maintenance import _queue_due_trash

    order_id, entry_id = trash_order(db, api, admin_client)
    _, path = link_asset(db, db.get(Order, order_id))
    work = Job(kind='PURGE', target_id=entry_id, business_key=f'purge:{entry_id}', attempts=9)
    db.add(work)
    db.commit()
    work_id = work.id
    with monkeypatch.context() as patch:
        patch.setattr(storage_coordination, '_gc_staged',
                      lambda root, entry_dir, staged: [item['key'] for item in staged])
        assert jobs.run_one(('PURGE',)) is True
    db.expire_all()
    job = db.get(Job, work_id)
    assert job.status == 'FAILED' and job.attempts == 10
    assert job.data['failureCode'] == 'PURGE_STORAGE_PENDING'
    assert db.get(TrashEntry, entry_id).status == 'PURGE_PENDING'
    assert path.exists()
    assert _queue_due_trash(db, utcnow()) == 0
    db.commit()
    # An explicit retry restores the budget and completes committed file GC.
    response = api(admin_client, f'/admin/trash/{entry_id}/purge', {
        'expectedVersion': db.get(TrashEntry, entry_id).version, 'reason': '存储恢复后重试',
    })
    assert response.status_code == 200, response.text
    assert jobs.run_one(('PURGE',)) is True
    db.expire_all()
    assert db.get(Job, work_id).status == 'READY'
    assert db.get(TrashEntry, entry_id).status == 'PURGED'
    assert not path.exists()


@pytest.mark.parametrize('with_revision', [False, True])
def test_order_purge_collects_all_historical_preparation_quotes(
        db, api, admin_client, with_revision):
    from app.maintenance import process_purge_job
    from app.models import OrderRevision, Preparation, Quote

    order_id, entry_id = trash_order(db, api, admin_client)
    order = db.get(Order, order_id)
    prep_ids = {order.preparation_id}
    if with_revision:
        old = Preparation(principal_id=order.principal_id, status='READY',
                          expires_at=utcnow() - timedelta(days=2))
        db.add(old)
        db.flush()
        old_quote = Quote(preparation_id=old.id, price_version=1,
                          expires_at=utcnow() - timedelta(days=2),
                          subtotal_cents=1000, total_cents=1000)
        db.add(old_quote)
        db.flush()
        db.add(OrderRevision(order_id=order_id, number=1, preparation_id=old.id,
                             quote_id=old_quote.id, snapshot={}))
        prep_ids.add(old.id)
    for prep_id in prep_ids:
        for _ in range(5):
            db.add(Quote(preparation_id=prep_id, price_version=1,
                         expires_at=utcnow() - timedelta(days=1),
                         subtotal_cents=1000, total_cents=1000))
    db.commit()
    quote_ids = set(db.scalars(select(Quote.id).where(Quote.preparation_id.in_(prep_ids))))

    process_purge_job(db, entry_id)
    db.commit()
    db.expire_all()
    assert db.get(Order, order_id) is None
    assert not list(db.scalars(select(Preparation.id).where(Preparation.id.in_(prep_ids))))
    assert not list(db.scalars(select(Quote.id).where(Quote.id.in_(quote_ids))))
    rows = db.get(TrashEntry, entry_id).data['purgedRows']
    assert {row['id'] for row in rows if row['table'] == 'quotes'} == quote_ids


@pytest.mark.parametrize('reference', ['ORDER', 'REVISION'])
def test_order_purge_preserves_historical_quotes_of_shared_preparation(
        db, api, admin_client, reference):
    from app.maintenance import process_purge_job
    from app.models import OrderRevision, Preparation, Quote

    order_id, entry_id = trash_order(db, api, admin_client)
    order = db.get(Order, order_id)
    prep_id = order.preparation_id
    sibling = Quote(preparation_id=prep_id, price_version=1,
                    expires_at=utcnow() + timedelta(minutes=15),
                    subtotal_cents=1000, total_cents=1000)
    db.add(sibling)
    db.commit()
    sibling_id = sibling.id
    other = make_order(db)
    other_id = other.id
    if reference == 'ORDER':
        other.preparation_id, other.quote_id = prep_id, sibling_id
    else:
        db.add(OrderRevision(order_id=other_id, number=1, preparation_id=prep_id,
                             quote_id=sibling_id, snapshot={}))
    db.commit()

    process_purge_job(db, entry_id)
    db.commit()
    db.expire_all()
    assert db.get(Order, order_id) is None
    assert db.get(Order, other_id) is not None
    assert db.get(Preparation, prep_id) is not None
    assert db.get(Quote, sibling_id) is not None


def test_purged_order_ledger_exports_and_replays_without_losing_cash_dates(
        db, api, admin_client, tmp_path):
    import json
    from datetime import datetime, timezone
    from sqlalchemy import delete
    from app.maintenance import process_purge_job
    from app.models import AnonymousLedger, Payment, Refund
    from tests.test_storage_coordination import _run_export, _run_apply

    order_id, entry_id = trash_order(db, api, admin_client)
    db.get(Order, order_id).refunded_cents = 100
    db.add_all([
        Payment(order_id=order_id, amount_cents=1000, kind='CASH_MANUAL',
                staff_id=admin_client.staff.id,
                created_at=datetime(2026, 10, 31, 15, 59, tzinfo=timezone.utc)),
        Refund(order_id=order_id, amount_cents=100, staff_id=admin_client.staff.id,
               reason='已结束退款', created_at=datetime(2026, 11, 1, tzinfo=timezone.utc)),
    ])
    db.commit()
    process_purge_job(db, entry_id)
    db.commit()
    db.expire_all()
    entry = db.get(TrashEntry, entry_id)
    totals = entry.data['ledgerTotals']
    assert totals['cashFlowByDay'] == {
        '2026-10-31': {'receivedCents': 1000, 'refundCents': 0},
        '2026-11-01': {'receivedCents': 0, 'refundCents': 100},
    }
    output = tmp_path / 'order-ledger.jsonl'
    exported = _run_export(db, tmp_path, output)
    assert exported.returncode == 0, exported.stdout + exported.stderr
    journal = [json.loads(line) for line in output.read_text().splitlines()]
    deletion = next(row for row in journal if row.get('trashId') == entry_id)
    assert deletion['ledger']['totals'] == totals
    # A restore must reconstruct the ledger, rather than need a live row.
    db.execute(delete(AnonymousLedger).where(AnonymousLedger.bucket == f'trash:{entry_id}'))
    db.commit()
    for _ in range(2):
        replayed = _run_apply(db, tmp_path, journal)
        assert replayed.returncode == 0, replayed.stdout + replayed.stderr
    db.expire_all()
    ledger = db.scalar(select(AnonymousLedger).where(AnonymousLedger.bucket == f'trash:{entry_id}'))
    assert ledger.totals == totals


def test_standalone_preparation_purge_includes_sibling_quotes(db, api, admin_client):
    from sqlalchemy import delete
    from app.maintenance import process_purge_job
    from app.models import Preparation, Quote

    order = make_order(db)
    prep_id = order.preparation_id
    for _ in range(5):
        db.add(Quote(preparation_id=prep_id, price_version=1,
                     expires_at=utcnow() - timedelta(days=1),
                     subtotal_cents=1000, total_cents=1000))
    db.execute(delete(Order).where(Order.id == order.id))
    db.commit()
    prep = db.get(Preparation, prep_id)
    response = api(admin_client, '/admin/trash', {
        'targetType': 'PREPARATION', 'targetId': prep_id,
        'expectedVersion': prep.version, 'reason': '清理无订单草稿',
    })
    assert response.status_code == 200, response.text
    process_purge_job(db, response.json()['data']['id'])
    db.commit()
    db.expire_all()
    assert db.get(Preparation, prep_id) is None
    assert not list(db.scalars(select(Quote.id).where(Quote.preparation_id == prep_id)))
