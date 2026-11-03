"""After-sale lifecycle through the shared recoverable trash (real PostgreSQL)."""
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app.core import Actor, DomainError, actor_dep
from app.models import AfterSale, Job, Order, Refund, TrashEntry, utcnow
from test_delivery_closed_loop import act_as
from test_maintenance import make_order

APP_MODULES = ['auth', 'commerce', 'after_sales', 'maintenance', 'growth', 'mail',
               'production', 'payroll', 'supplementary', 'exports']


def make_case(db, staff, status='REFUNDED', amount=200):
    zero_payable = status == 'REFUNDED' and amount == 0
    order = make_order(db, total_cents=0 if zero_payable else 1000,
                       settlement_kind='ZERO_PAYABLE' if zero_payable else 'CASH_MANUAL',
                       refunded_cents=amount if status == 'REFUNDED' else 0)
    case = AfterSale(order_id=order.id, source='CUSTOMER', actor_id=order.principal_id,
                     responsibility='MERCHANT', solution='REFUND', status=status,
                     reason='测试售后', suggested_cents=amount, approved_cents=amount,
                     adjustment_basis='', data={})
    db.add(case)
    db.flush()
    refund = None
    if status == 'REFUNDED':
        refund = Refund(order_id=order.id, after_sale_id=case.id, amount_cents=amount,
                        staff_id=staff.id, reason='已核实退款', data={'suggestedCents': amount})
        db.add(refund)
    db.commit()
    return order, case, refund


def trash(api, client, case, **values):
    return api(client, '/admin/trash', {
        'targetType': 'AFTER_SALE', 'targetId': case.id,
        'expectedVersion': case.version, 'reason': '清理测试售后', **values})


@pytest.mark.parametrize('status', ['REFUNDED', 'COMPLETED', 'REJECTED', 'CANCELLED', 'CLOSED'])
def test_terminal_case_trash_hides_reads_and_restores_original_facts(
        db, api, client, app, act_as, staff_factory, status):
    admin = act_as(staff_factory())
    order, case, refund = make_case(db, admin, status)
    before_version = case.version
    result = trash(api, client, case)
    assert result.status_code == 200, result.text
    entry = result.json()['data']
    db.refresh(case)
    assert case.deleted_at is not None
    assert case.version > before_version
    stored_entry = db.get(TrashEntry, entry['id'])
    assert timedelta(hours=23, minutes=59) < stored_entry.purge_at - stored_entry.created_at <= timedelta(hours=24)
    listed = api(client, '/admin/after-sales', method='GET')
    assert listed.json()['data']['items'] == []
    assert api(client, f'/admin/after-sales/{case.id}', method='GET').status_code == 404

    app.dependency_overrides[actor_dep] = lambda: Actor(principal_id=order.principal_id)
    assert api(client, f'/orders/{order.id}/after-sales', method='GET').json()['data']['items'] == []
    assert api(client, f'/after-sales/{case.id}', method='GET').status_code == 404
    act_as(admin)

    restored = api(client, f"/admin/trash/{entry['id']}/restore", {
        'expectedVersion': entry['version'], 'reason': '误删恢复'})
    assert restored.status_code == 200, restored.text
    db.refresh(case)
    assert case.deleted_at is None and case.status == status
    detail = api(client, f'/admin/after-sales/{case.id}', method='GET')
    assert detail.status_code == 200 and detail.json()['data']['reason'] == '测试售后'
    if refund:
        db.refresh(refund)
        assert refund.after_sale_id == case.id and refund.amount_cents == 200
    assert db.get(Order, order.id).refunded_cents == (200 if refund else 0)


@pytest.mark.parametrize('status', ['REQUESTED', 'REVIEW_PENDING', 'REFUND_APPROVED', 'REPRINTING', 'REPRINT_READY'])
def test_open_case_cannot_enter_trash(db, api, client, act_as, staff_factory, status):
    admin = act_as(staff_factory())
    _, case, _ = make_case(db, admin, status)
    result = trash(api, client, case)
    assert result.status_code == 409, result.text
    assert result.json()['error']['details']['blockers'] == ['AFTER_SALE_ACTIVE']
    assert db.scalar(select(TrashEntry).where(TrashEntry.target_id == case.id)) is None


@pytest.mark.parametrize('amount', [0, 200])
def test_expired_case_purges_without_erasing_refund_or_order_ledger(
        db, api, client, act_as, staff_factory, amount):
    from app.maintenance import maintenance_sweep, process_purge_job

    admin = act_as(staff_factory())
    order, case, refund = make_case(db, admin, amount=amount)
    case_id, refund_id, order_id = case.id, refund.id, order.id
    result = trash(api, client, case)
    assert result.status_code == 200, result.text
    entry = db.get(TrashEntry, result.json()['data']['id'])
    entry.purge_at = utcnow() - timedelta(seconds=1)
    db.commit()
    denied = api(client, f'/admin/trash/{entry.id}/restore', {
        'expectedVersion': entry.version, 'reason': '到期后恢复'})
    assert denied.status_code == 410
    maintenance_sweep(db)
    db.commit()
    assert db.scalar(select(Job).where(Job.business_key == f'purge:{entry.id}')) is not None
    process_purge_job(db, entry.id)
    db.commit()
    db.expire_all()
    assert db.get(AfterSale, case_id) is None
    retained = db.get(Refund, refund_id)
    assert retained.after_sale_id is None
    assert retained.order_id == order_id and retained.amount_cents == amount
    assert retained.reason == '已核实退款'
    assert db.get(Order, order_id).refunded_cents == amount
    assert db.get(Order, order_id).cash_received_cents == (0 if amount == 0 else 1000)
    purged = db.get(TrashEntry, entry.id)
    assert purged.status == 'PURGED'
    assert {'table': 'refund_records', 'column': 'after_sale_id', 'id': refund_id} in purged.data['purgedDetaches']
    process_purge_job(db, entry.id)
    db.commit()
    assert db.get(Refund, refund_id).amount_cents == amount


@pytest.mark.parametrize('action,body', [
    ('refund-preview', {'affectedItems': []}),
    ('review', {'decision': 'REJECT', 'reason': '不能处理已删除售后'}),
    ('record-refund', {'amountCents': 0, 'reason': '不能处理已删除售后'}),
    ('reprint-ready', {'reason': '不能处理已删除售后'}),
    ('complete', {'reason': '不能处理已删除售后'}),
])
def test_trashed_case_rejects_admin_actions(db, api, client, act_as, staff_factory, action, body):
    admin = act_as(staff_factory())
    _, case, _ = make_case(db, admin, 'REJECTED')
    result = trash(api, client, case)
    assert result.status_code == 200, result.text
    db.refresh(case)
    response = api(client, f'/admin/after-sales/{case.id}/{action}', {
        'expectedVersion': case.version, **body} if action != 'refund-preview' else body)
    assert response.status_code == 404, response.text


def test_trash_case_requires_data_permission_and_latest_version(db, api, client, act_as, staff_factory):
    admin = staff_factory()
    _, case, _ = make_case(db, admin, 'REJECTED')
    act_as(staff_factory(capabilities=['payment.manage']))
    assert trash(api, client, case).status_code == 403
    act_as(admin)
    stale = trash(api, client, case, expectedVersion=case.version + 1)
    assert stale.status_code == 409 and stale.json()['error']['code'] == 'VERSION_CONFLICT'


def test_case_purge_rechecks_unfinished_business(db, api, client, act_as, staff_factory):
    from app.maintenance import process_purge_job

    admin = act_as(staff_factory())
    _, case, _ = make_case(db, admin, 'REJECTED')
    result = trash(api, client, case)
    assert result.status_code == 200, result.text
    db.refresh(case)
    case.status = 'REVIEW_PENDING'
    db.commit()
    with pytest.raises(DomainError) as caught:
        process_purge_job(db, result.json()['data']['id'])
    assert caught.value.code == 'PURGE_BLOCKED'
    assert caught.value.details['blockers'] == ['AFTER_SALE_ACTIVE']


def test_case_restore_requires_restoring_trashed_parent_first(db, api, client, act_as, staff_factory):
    admin = act_as(staff_factory())
    order, case, _ = make_case(db, admin, 'REJECTED')
    child = trash(api, client, case).json()['data']
    parent = api(client, '/admin/trash', {
        'targetType': 'ORDER', 'targetId': order.id,
        'expectedVersion': order.version, 'reason': '清理测试订单'})
    assert parent.status_code == 200, parent.text
    preview = api(client, f"/admin/trash/{child['id']}/restore-preview", {})
    assert preview.json()['data']['restorable'] is False
    assert 'PARENT_ORDER_TRASHED' in {item['code'] for item in preview.json()['data']['blockers']}
    blocked = api(client, f"/admin/trash/{child['id']}/restore", {
        'expectedVersion': child['version'], 'reason': '恢复售后'})
    assert blocked.status_code == 409 and blocked.json()['error']['code'] == 'RESTORE_CONFLICT'
    parent = parent.json()['data']
    assert api(client, f"/admin/trash/{parent['id']}/restore", {
        'expectedVersion': parent['version'], 'reason': '先恢复订单'}).status_code == 200
    restored = api(client, f"/admin/trash/{child['id']}/restore", {
        'expectedVersion': child['version'], 'reason': '再恢复售后'})
    assert restored.status_code == 200, restored.text


def test_case_restore_serializes_with_parent_purge(
        db, api, client, act_as, staff_factory, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event
    from app import maintenance
    from app.db import SessionLocal

    admin = act_as(staff_factory())
    order, case, _ = make_case(db, admin, 'REJECTED')
    child = trash(api, client, case).json()['data']
    parent = api(client, '/admin/trash', {
        'targetType': 'ORDER', 'targetId': order.id,
        'expectedVersion': order.version, 'reason': '清理订单'}).json()['data']
    reached_graph, release = Event(), Event()
    delete_graph = maintenance._delete_order_graph

    def pause_graph(session, target, context):
        reached_graph.set()
        assert release.wait(5)
        return delete_graph(session, target, context)

    monkeypatch.setattr(maintenance, '_delete_order_graph', pause_graph)

    def purge_parent():
        with SessionLocal() as session:
            maintenance.process_purge_job(session, parent['id'])
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        purge = pool.submit(purge_parent)
        try:
            assert reached_graph.wait(5)
            restored = pool.submit(api, client, f"/admin/trash/{child['id']}/restore", {
                'expectedVersion': child['version'], 'reason': '并发恢复售后'})
            with pytest.raises(TimeoutError):
                restored.result(timeout=0.2)
        finally:
            release.set()
        purge.result(timeout=5)
        response = restored.result(timeout=5)
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'RESTORE_CONFLICT'


@pytest.mark.parametrize('action,body', [
    ('choice', {'solution': 'REFUND'}),
    ('withdraw', {'reason': '不能撤回已删除售后'}),
])
def test_trashed_case_rejects_customer_actions(db, api, client, app, act_as, staff_factory, action, body):
    admin = act_as(staff_factory())
    order, case, _ = make_case(db, admin, 'CANCELLED')
    result = trash(api, client, case)
    assert result.status_code == 200, result.text
    db.refresh(case)
    app.dependency_overrides[actor_dep] = lambda: Actor(principal_id=order.principal_id)
    response = api(client, f'/orders/{order.id}/after-sales/{case.id}/{action}', {
        'expectedVersion': case.version, **body})
    assert response.status_code == 404, response.text


@pytest.mark.parametrize('legacy_schema', [False, True])
def test_backup_replay_preserves_refund_while_removing_purged_case(
        db, api, client, act_as, staff_factory, tmp_path, legacy_schema):
    from app.maintenance import process_purge_job
    from test_storage_coordination import _run_apply

    admin = act_as(staff_factory())
    order, case, refund = make_case(db, admin)
    snapshot = {column.name: getattr(case, column.name) for column in AfterSale.__table__.columns}
    case_id, refund_id, order_id = case.id, refund.id, order.id
    result = trash(api, client, case)
    assert result.status_code == 200, result.text
    entry_id = result.json()['data']['id']
    process_purge_job(db, entry_id)
    db.commit()
    entry = db.get(TrashEntry, entry_id)
    journal = {
        'version': 2, 'trashId': entry.id, 'targetType': 'AFTER_SALE',
        'targetId': case_id, 'purgedAt': utcnow().isoformat(),
        'rows': entry.data['purgedRows'], 'detaches': entry.data['purgedDetaches'],
        'files': [],
    }
    # Simulate restoring an older database snapshot before applying its journal.
    db.add(AfterSale(**snapshot))
    db.flush()
    db.get(Refund, refund_id).after_sale_id = case_id
    db.commit()
    if legacy_schema:
        db.execute(text('ALTER TABLE refund_records ALTER COLUMN after_sale_id SET NOT NULL'))
        db.commit()
    replayed = _run_apply(db, tmp_path, [journal])
    assert replayed.returncode == 0, replayed.stdout + replayed.stderr
    db.expire_all()
    assert db.get(AfterSale, case_id) is None
    assert db.get(Refund, refund_id).after_sale_id is None
    assert db.get(Refund, refund_id).amount_cents == 200
    assert db.get(Order, order_id).refunded_cents == 200
