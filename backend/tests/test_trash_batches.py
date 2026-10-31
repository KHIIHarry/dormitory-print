"""Batch deletion must go through the same 24h trash as orders (R02, D-05.3).

A completed batch used to be permanently un-deletable; the policy decision is
that an admin may delete it, it lands in the trash, and it can be restored
within the retention window.
"""
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core import Actor, actor_dep, utcnow
from app.delivery_schemas import to_shanghai_date
from app.models import (
    DeliveryAssignment, DeliveryBatch, Order, TrashEntry, WageEntry,
)
from tests.test_delivery import make_courier, make_ready_order

APP_MODULES = ['maintenance', 'delivery', 'commerce', 'production', 'growth',
               'mail', 'auth']


@pytest.fixture
def act_as(app):
    def apply(staff):
        app.dependency_overrides[actor_dep] = lambda: Actor(
            staff_id=staff.id, staff_role=staff.role,
            capabilities=set(staff.capabilities or []))
        return staff
    yield apply
    app.dependency_overrides.pop(actor_dep, None)


def create_batch(api, client, courier):
    starts_at = utcnow() + timedelta(hours=-1)
    ends_at = starts_at + timedelta(hours=3)
    response = api(client, '/admin/batches', {
        'name': f'回收站批次 {uuid4().hex[:6]}',
        'startsAt': starts_at.isoformat(),
        'endsAt': ends_at.isoformat(),
        'courierId': courier.id,
        'reason': '',
    })
    assert response.status_code == 200, response.text
    return response.json()['data']


def attach_order(api, client, batch, order):
    body = {
        'action': 'ADD', 'orderIds': [order.id], 'sourceBatchIds': [batch['id']],
        'versions': {batch['id']: batch['version']},
        'payload': {'reason': '测试排单'},
    }
    preview = api(client, '/admin/batch-operations/preview', body)
    assert preview.status_code == 200, preview.text
    plan = preview.json()['data']
    assert plan['blockers'] == [], plan['blockers']
    committed = api(client, '/admin/batch-operations/commit', {'planId': plan['planId']})
    assert committed.status_code == 200, committed.text
    return committed.json()['data']['batches'][0]


def complete_batch(db, batch_id):
    batch = db.get(DeliveryBatch, batch_id)
    batch.status = 'COMPLETED'
    for assignment in db.scalars(select(DeliveryAssignment).where(
            DeliveryAssignment.batch_id == batch_id, DeliveryAssignment.active.is_(True))):
        assignment.status = 'DELIVERED'
        order = db.get(Order, assignment.order_id)
        order.status = 'DELIVERED'
    db.commit()
    db.refresh(batch)
    return batch


def trash_entry(db, batch_id):
    db.expire_all()
    return db.scalar(select(TrashEntry).where(
        TrashEntry.target_type == 'BATCH', TrashEntry.target_id == batch_id))


def test_completed_batch_enters_trash_and_restores_within_window(
        db, api, client, act_as, staff_factory):
    act_as(staff_factory(role='ADMIN'))
    courier = make_courier(staff_factory)
    order = make_ready_order(db)
    batch = create_batch(api, client, courier)
    attach_order(api, client, batch, order)
    stored = complete_batch(db, batch['id'])
    version = stored.version

    deleted = api(client, f"/admin/batches/{batch['id']}", {
        'expectedVersion': version, 'reason': '录错了'}, method='DELETE')
    assert deleted.status_code == 200, deleted.text

    entry = trash_entry(db, batch['id'])
    assert entry is not None
    assert entry.status == 'DELETED'
    assert entry.reason == '录错了'
    db.expire_all()
    assert db.get(DeliveryBatch, batch['id']).deleted_at is not None
    assert db.get(DeliveryBatch, batch['id']).status == 'CANCELLED'

    listed = api(client, '/admin/trash', None, method='GET')
    assert listed.status_code == 200, listed.text
    items = listed.json()['data']['items']
    assert any(item['type'] == 'BATCH' and item['name'] for item in items)

    restored = api(client, f'/admin/trash/{entry.id}/restore', {
        'expectedVersion': entry.version, 'reason': '恢复批次'})
    assert restored.status_code == 200, restored.text
    db.expire_all()
    batch = db.get(DeliveryBatch, batch['id'])
    assert batch.deleted_at is None
    assert batch.status == 'COMPLETED'
    assert trash_entry(db, batch.id).status == 'RESTORED'


def test_delivering_batch_still_cannot_be_trashed(db, api, client, act_as, staff_factory):
    act_as(staff_factory(role='ADMIN'))
    courier = make_courier(staff_factory)
    order = make_ready_order(db)
    batch = create_batch(api, client, courier)
    attach_order(api, client, batch, order)
    stored = db.get(DeliveryBatch, batch['id'])
    stored.status = 'DELIVERING'
    db.commit()

    response = api(client, f"/admin/batches/{batch['id']}", {
        'expectedVersion': stored.version, 'reason': '删掉'}, method='DELETE')
    assert response.status_code == 409, response.text
    db.expire_all()
    assert db.get(DeliveryBatch, batch['id']).deleted_at is None
    assert trash_entry(db, batch['id']) is None


def test_batch_with_unsettled_wage_cannot_be_trashed(
        db, api, client, act_as, staff_factory):
    act_as(staff_factory(role='ADMIN'))
    courier = make_courier(staff_factory)
    order = make_ready_order(db)
    batch = create_batch(api, client, courier)
    attach_order(api, client, batch, order)
    complete_batch(db, batch['id'])
    assignment = db.scalar(select(DeliveryAssignment).where(
        DeliveryAssignment.batch_id == batch['id']))
    db.add(WageEntry(staff_id=courier.id, assignment_id=assignment.id,
                     source_key=f'wage-{uuid4()}', kind='DELIVERY',
                     amount_cents=500, period='2030-01', snapshot={}))
    db.commit()

    response = api(client, f"/admin/batches/{batch['id']}", {
        'expectedVersion': db.get(DeliveryBatch, batch['id']).version,
        'reason': '删掉'}, method='DELETE')
    assert response.status_code == 409, response.text
    assert 'WAGE_UNSETTLED' in str(response.json()['error']['details'])
    db.expire_all()
    assert db.get(DeliveryBatch, batch['id']).deleted_at is None


def test_purged_batch_removes_executions_and_detaches_settled_wages(
        db, api, client, act_as, staff_factory):
    from app.models import WageSettlement, WageSettlementItem

    act_as(staff_factory(role='ADMIN'))
    courier = make_courier(staff_factory)
    order = make_ready_order(db)
    batch = create_batch(api, client, courier)
    attach_order(api, client, batch, order)
    complete_batch(db, batch['id'])
    assignment = db.scalar(select(DeliveryAssignment).where(
        DeliveryAssignment.batch_id == batch['id']))
    assignment_id = assignment.id
    db.expunge(assignment)
    wage = WageEntry(staff_id=courier.id, assignment_id=assignment_id,
                     source_key=f'wage-{uuid4()}', kind='DELIVERY',
                     amount_cents=500, period='2030-01', snapshot={})
    db.add(wage)
    db.flush()
    settlement = WageSettlement(staff_id=courier.id, period='2030-01',
                                amount_cents=500, paid_by='admin',
                                reason='测试结算', data={})
    db.add(settlement)
    db.flush()
    db.add(WageSettlementItem(settlement_id=settlement.id, entry_id=wage.id))
    db.commit()

    deleted = api(client, f"/admin/batches/{batch['id']}", {
        'expectedVersion': db.get(DeliveryBatch, batch['id']).version,
        'reason': '测试清理'}, method='DELETE')
    assert deleted.status_code == 200, deleted.text
    entry = trash_entry(db, batch['id'])
    purged = api(client, f'/admin/trash/{entry.id}/purge', {
        'expectedVersion': entry.version, 'reason': '确认清理'})
    assert purged.status_code == 200, purged.text

    db.expire_all()
    assert db.get(DeliveryBatch, batch['id']) is None
    assert db.scalar(select(DeliveryAssignment).where(
        DeliveryAssignment.batch_id == batch['id'])) is None
    retained = db.get(WageEntry, wage.id)
    assert retained is not None
    assert retained.assignment_id is None
    assert retained.snapshot['purgedAssignmentId'] == assignment_id


def test_retroactive_policy_still_needs_settlement_before_order_trash(
    api, admin_client, db, staff_factory
):
    """补录只把 0 元标记换成正式工资条目；发放结清前订单仍不可删。"""
    from app import payroll
    from tests.test_payroll import add_policy, admin_actor, make_delivered

    courier = make_courier(staff_factory)
    order, assignment, proof, _ = make_delivered(db, staff_factory, courier)
    order.settlement_kind = 'CASH'
    entry = payroll.on_delivery(db, order, assignment, proof)
    db.commit()

    def trash():
        return api(admin_client, '/admin/trash', {
            'targetType': 'ORDER', 'targetId': order.id,
            'expectedVersion': db.get(Order, order.id).version,
            'reason': '流程回归',
        })

    blocked = trash()
    assert blocked.status_code == 409, blocked.text
    assert f'WAGE_UNSETTLED:{entry.id}' in blocked.json()['error']['details']['blockers']

    policy = add_policy(db, mode='FIXED', fixed=500)
    payroll.apply_retroactive_policy(db, admin_actor(admin_client.staff), order, '补录', policy.id)
    db.commit()
    still_blocked = trash()
    assert still_blocked.status_code == 409, still_blocked.text
    assert any('WAGE_UNSETTLED' in code for code in
               still_blocked.json()['error']['details']['blockers'])

    converted = db.get(WageEntry, entry.id)
    payroll.record_settlement(db, admin_actor(admin_client.staff), courier.id,
                              converted.period, [converted.id], '发放后清理')
    db.commit()
    deleted = trash()
    assert deleted.status_code == 200, deleted.text


def test_packed_delivered_order_can_be_trashed(db, api, client, act_as, staff_factory):
    """装袋完成用的是 PACKED，不是 COMPLETED；已交付订单不应被装袋守卫挡住。"""
    act_as(staff_factory(role='ADMIN'))
    order = make_ready_order(db, status='DELIVERED')

    response = api(client, '/admin/trash', {
        'targetType': 'ORDER', 'targetId': order.id,
        'expectedVersion': order.version, 'reason': '整理数据',
    })
    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.get(Order, order.id).deleted_at is not None


def test_unpacked_order_still_blocked_by_packing(db, api, client, act_as, staff_factory):
    act_as(staff_factory(role='ADMIN'))
    order = make_ready_order(db, status='DELIVERED', packed=False)

    response = api(client, '/admin/trash', {
        'targetType': 'ORDER', 'targetId': order.id,
        'expectedVersion': order.version, 'reason': '整理数据',
    })
    assert response.status_code == 409, response.text
    assert 'PACKING_NOT_COMPLETED' in response.json()['error']['details']['blockers']


def test_delete_batch_when_member_order_was_already_deleted(
        db, api, client, act_as, staff_factory):
    act_as(staff_factory(role='ADMIN'))
    courier = make_courier(staff_factory)
    order = make_ready_order(db)
    starts_at = utcnow() + timedelta(days=1)
    created = api(client, '/admin/batches', {
        'name': f'订单删除批次 {uuid4().hex[:6]}',
        'date': to_shanghai_date(starts_at),
        'time': '10:00 – 12:00',
        'courierId': courier.id,
        'reason': '',
    })
    assert created.status_code == 200, created.text
    batch = created.json()['data']
    attach_order(api, client, batch, order)
    complete_batch(db, batch['id'])
    order = db.get(Order, order.id)
    order.deleted_at = utcnow()
    db.commit()

    stored = db.get(DeliveryBatch, batch['id'])
    preview = api(client, '/admin/batch-operations/preview', {
        'action': 'DELETE', 'orderIds': [], 'sourceBatchIds': [batch['id']],
        'versions': {batch['id']: stored.version},
        'payload': {'reason': '订单已删除'}})
    assert preview.status_code == 200, preview.text
    assert preview.json()['data']['blockers'] == [], preview.json()['data']['blockers']
    committed = api(client, '/admin/batch-operations/commit',
                    {'planId': preview.json()['data']['planId']})
    assert committed.status_code == 200, committed.text
    db.expire_all()
    assert db.get(DeliveryBatch, batch['id']).deleted_at is not None
    assert trash_entry(db, batch['id']) is not None
    listed = api(client, '/admin/batches', None, method='GET')
    assert listed.status_code == 200, listed.text
    assert all(item['id'] != batch['id'] for item in listed.json()['data']['items'])
