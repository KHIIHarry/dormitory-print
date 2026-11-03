"""Deleted reservations must not keep a delivery slot's edit guard locked."""
from datetime import timedelta

import pytest

from app.delivery_schemas import to_shanghai_date, to_shanghai_hhmm
from app.models import DeliveryBatch, DeliverySlot, utcnow
from test_delivery_closed_loop import (
    APP_MODULES as DELIVERY_MODULES, act_as, make_world, submit,
)

APP_MODULES = [*DELIVERY_MODULES, 'maintenance']


@pytest.mark.parametrize('reference', ['requested_order', 'confirmed_order', 'batch', 'legacy_batch'])
@pytest.mark.parametrize('change', ['window', 'acceptance'])
@pytest.mark.parametrize('deleted', [False, True])
def test_slot_edit_ignores_only_trashed_references(
        db, api, client, act_as, staff_factory, reference, change, deleted):
    act_as(staff_factory())
    world = make_world(db)
    slot = world.slot
    slot.starts_at = (utcnow() + timedelta(days=3)).replace(
        hour=4, minute=0, second=0, microsecond=0)
    slot.ends_at = slot.starts_at + timedelta(hours=2)
    slot.date = to_shanghai_date(slot.starts_at)
    db.commit()
    if reference.endswith('order'):
        target = submit(db, world)
        if reference == 'confirmed_order':
            other = DeliverySlot(
                date=slot.date, name='原预约时段', starts_at=slot.starts_at,
                ends_at=slot.ends_at, open_at=slot.open_at, cutoff_at=slot.cutoff_at,
                capacity=4, occupied=0, enabled=True, data={})
            db.add(other)
            db.flush()
            target.requested_slot_id = other.id
            target.confirmed_slot_id = slot.id
        if deleted:
            target.status = 'CANCELLED'
        target_type = 'ORDER'
    else:
        target = DeliveryBatch(
            name='测试批次', starts_at=slot.starts_at, ends_at=slot.ends_at,
            status='PLANNED', slot_id=slot.id if reference == 'batch' else None,
            data={'slotSnapshot': {'id': slot.id}} if reference == 'legacy_batch' else {})
        db.add(target)
        target_type = 'BATCH'
    db.commit()

    if deleted:
        trashed = api(client, '/admin/trash', {
            'targetType': target_type, 'targetId': target.id,
            'expectedVersion': target.version, 'reason': '删除测试数据'})
        assert trashed.status_code == 200, trashed.text
        db.refresh(target)
        assert target.deleted_at is not None

    payload = {'expectedVersion': slot.version, 'reason': '调整空闲时段'}
    if change == 'window':
        payload.update(
            date=slot.date, start=to_shanghai_hhmm(slot.starts_at + timedelta(minutes=15)),
            end=to_shanghai_hhmm(slot.ends_at + timedelta(minutes=15)))
    else:
        payload.update(openAt=(slot.open_at - timedelta(hours=1)).isoformat(),
                       cutoffAt=slot.cutoff_at.isoformat())
    result = api(client, f'/admin/slots/{slot.id}', payload, method='PATCH')
    if deleted:
        assert result.status_code == 200, result.text
    else:
        assert result.status_code == 409, result.text
        assert result.json()['error']['code'] == 'SLOT_TIME_LOCKED'
