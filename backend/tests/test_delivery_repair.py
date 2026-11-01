"""Legacy delivery repair preview must inspect facts without mutating them."""
from datetime import timedelta

from sqlalchemy import event

from app.models import DeliveryAssignment, DeliveryBatch, utcnow
from test_delivery_closed_loop import make_world, submit


def test_legacy_repair_preview_reports_ambiguity_and_emits_no_writes(db):
    from app.delivery_repair import delivery_repair_preview

    world = make_world(db)
    order = submit(db, world)
    order.status = 'IN_PRODUCTION'
    order.settlement_kind = 'CASH_MANUAL'
    order.cash_received_cents = order.total_cents
    order.confirmed_slot_id = world.slot.id
    world.slot.open_at = None
    world.slot.cutoff_at = None
    batches = [DeliveryBatch(name=f'旧批次 {i}', starts_at=world.slot.starts_at,
                             ends_at=world.slot.ends_at, status='PLANNED',
                             slot_id=world.slot.id, campus_id=world.campus.id, data={})
               for i in range(2)]
    detached = DeliveryBatch(name='已脱离原时段', starts_at=world.slot.starts_at,
                             ends_at=world.slot.ends_at, status='PLANNED', data={})
    db.add_all([*batches, detached])
    db.commit()
    statements = []
    def inspect_sql(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.lstrip().split(None, 1)[0].upper())
    engine = db.get_bind()
    event.listen(engine, 'before_cursor_execute', inspect_sql)
    try:
        result = delivery_repair_preview(db)
    finally:
        event.remove(engine, 'before_cursor_execute', inspect_sql)
    assert all(verb == 'SELECT' for verb in statements)
    assert result['readOnly'] is True
    assert result['unconfiguredSlots'][0]['slotId'] == world.slot.id
    orphan = next(row for row in result['paidUnassignedOrders'] if row['orderId'] == order.id)
    assert orphan['matchStatus'] == 'NEEDS_REVIEW'
    assert set(orphan['candidateBatchIds']) == {batch.id for batch in batches}
    assert set(result['ambiguousGroups'][0]['batchIds']) == {batch.id for batch in batches}
    assert any(row['batchId'] == detached.id for row in result['detachedBatches'])
    assert not db.new and not db.dirty and not db.deleted


def test_repair_preview_preserves_valid_assignments_and_historical_orders(db):
    from app.delivery_repair import delivery_repair_preview
    world = make_world(db)
    order = submit(db, world)
    order.status = 'DELIVERED'
    order.settlement_kind = 'CASH_MANUAL'
    order.cash_received_cents = order.total_cents
    order.confirmed_slot_id = world.slot.id
    batch = DeliveryBatch(name='历史执行', starts_at=world.slot.starts_at,
                          ends_at=world.slot.ends_at, status='COMPLETED',
                          slot_id=world.slot.id, campus_id=world.campus.id, data={})
    db.add(batch)
    db.flush()
    assignment = DeliveryAssignment(order_id=order.id, batch_id=batch.id,
                                    active=True, status='DELIVERED', data={})
    db.add(assignment)
    db.commit()
    result = delivery_repair_preview(db)
    assert not result['paidUnassignedOrders']
    assert result['historicalCounts']['deliveredOrders'] == 1
    assert db.get(DeliveryAssignment, assignment.id).active is True
    assert db.get(DeliveryBatch, batch.id).status == 'COMPLETED'
