"""Read-only legacy delivery inspection; never assigns, seals or sends mail.

The caller must require ``delivery.manage``. A unique candidate is only a
matching hint, never authorization to bypass the existing scheduling guards.
All apply work remains an explicit, reviewed configuration/scheduling action.
"""
from collections import defaultdict

from sqlalchemy import select

from .models import (
    AfterSale, BusinessException, DeliveryAssignment, DeliveryBatch,
    DeliverySlot, Location, Order, utcnow,
)


def delivery_repair_preview(db) -> dict:
    """Return current facts and candidate matches without flushing pending work."""
    with db.no_autoflush:
        slots = {row.id: row for row in db.scalars(select(DeliverySlot).order_by(DeliverySlot.id))}
        orders = {row.id: row for row in db.scalars(select(Order).order_by(Order.id))}
        locations = {row.id: row for row in db.scalars(select(Location))}
        batches = {row.id: row for row in db.scalars(select(DeliveryBatch).order_by(DeliveryBatch.id))}
        assignments = list(db.scalars(select(DeliveryAssignment).where(
            DeliveryAssignment.active.is_(True)).order_by(DeliveryAssignment.id)))
        after_sales = list(db.scalars(select(AfterSale).where(AfterSale.status.in_(
            ('REQUESTED', 'REVIEW_PENDING', 'REFUND_APPROVED', 'REPRINTING', 'REPRINT_READY')))))
        blocking = list(db.scalars(select(BusinessException).where(
            BusinessException.status == 'OPEN', BusinessException.blocks_delivery.is_(True))))

    by_order, by_batch = defaultdict(list), defaultdict(list)
    for assignment in assignments:
        by_order[assignment.order_id].append(assignment)
        by_batch[assignment.batch_id].append(assignment)
    after_sale_orders = {row.order_id for row in after_sales}
    blocked_orders = {row.order_id for row in blocking}

    def order_campus(order):
        location = locations.get(order.location_id)
        return location.campus_id if location else None

    def batch_identity(batch):
        explicit = batch.slot_id
        legacy = (batch.data or {}).get('slotId')
        slot_id = explicit or (legacy if legacy in slots else None)
        campus_ids = {order_campus(orders[row.order_id]) for row in by_batch[batch.id]
                      if row.order_id in orders}
        campus_ids.discard(None)
        campus_id = batch.campus_id or (next(iter(campus_ids)) if len(campus_ids) == 1 else None)
        return slot_id, campus_id

    identities = {row.id: batch_identity(row) for row in batches.values()}
    candidate_groups = defaultdict(list)
    live_statuses = {'PLANNED', 'PUBLISHED', 'POSTPONED'}
    detached = []
    for batch in batches.values():
        slot_id, campus_id = identities[batch.id]
        active_orders = [orders[row.order_id] for row in by_batch[batch.id] if row.order_id in orders]
        reasons = []
        if batch.slot_id is None:
            reasons.append('EXPLICIT_SLOT_MISSING')
        if slot_id is None:
            reasons.append('ORIGINAL_SLOT_UNRESOLVED')
        if campus_id is None:
            reasons.append('CAMPUS_UNRESOLVED')
        confirmed_ids = {row.confirmed_slot_id for row in active_orders if row.confirmed_slot_id}
        if len(confirmed_ids) > 1 or (slot_id and any(value != slot_id for value in confirmed_ids)):
            reasons.append('ORDER_SLOT_MISMATCH')
        actual_campuses = {order_campus(row) for row in active_orders}
        if len(actual_campuses) > 1 or (campus_id and any(value != campus_id for value in actual_campuses)):
            reasons.append('ORDER_CAMPUS_MISMATCH')
        if reasons:
            detached.append({'batchId': batch.id, 'name': batch.name, 'status': batch.status,
                             'version': batch.version, 'deleted': batch.deleted_at is not None,
                             'slotId': slot_id, 'campusId': campus_id,
                             'activeAssignmentIds': [row.id for row in by_batch[batch.id]],
                             'confirmedSlotIds': sorted(confirmed_ids), 'reasons': reasons,
                             'nextAction': '人工核对原时段、校区和现有执行；保留原分配，使用受控调度预览处理，历史与垃圾桶不自动恢复。'})
        if (slot_id and campus_id and batch.deleted_at is None and batch.status in live_statuses
                and not any(reason in reasons for reason in ('ORDER_SLOT_MISMATCH', 'ORDER_CAMPUS_MISMATCH'))):
            candidate_groups[(slot_id, campus_id)].append(batch)

    ambiguous = []
    for (slot_id, campus_id), members in sorted(candidate_groups.items()):
        # Explicit groups may contain legitimate split batches. Only ungrouped
        # legacy membership is ambiguous; do not label every split as corrupt.
        if len(members) > 1 and any(row.group_id is None for row in members):
            ambiguous.append({'slotId': slot_id, 'campusId': campus_id,
                              'batchIds': [row.id for row in members],
                              'groupIds': sorted({row.group_id for row in members if row.group_id}),
                              'nextAction': '核对这些批次是否为合法拆分；不得自动合并或选择常规入口，保留已有有效配送执行。'})

    paid_unassigned = []
    for order in orders.values():
        if (order.settlement_kind not in ('CASH_MANUAL', 'ZERO_PAYABLE')
                or order.deleted_at is not None or order.status in ('DELIVERED', 'CANCELLED')):
            continue
        current = by_order[order.id]
        valid = [row for row in current if row.status != 'CANCELLED'
                 and row.batch_id in batches and batches[row.batch_id].deleted_at is None
                 and batches[row.batch_id].status not in ('CANCELLED', 'COMPLETED')]
        if valid:
            continue
        slot_id = order.confirmed_slot_id
        campus_id = order_campus(order)
        candidates = candidate_groups.get((slot_id, campus_id), []) if slot_id and campus_id else []
        blockers = []
        if slot_id is None:
            blockers.append('CONFIRMED_SLOT_MISSING')
        if campus_id is None:
            blockers.append('CAMPUS_MISSING')
        if current:
            blockers.append('EXISTING_ASSIGNMENT_REQUIRES_REVIEW')
        if order.refund_locked or order.refunded_cents:
            blockers.append('REFUND_REQUIRES_REVIEW')
        if order.address_review:
            blockers.append('ADDRESS_REVIEW')
        if order.id in after_sale_orders:
            blockers.append('AFTER_SALE_REQUIRES_REVIEW')
        if order.id in blocked_orders:
            blockers.append('DELIVERY_BLOCKED')
        paid_unassigned.append({'orderId': order.id, 'status': order.status,
                                'version': order.version, 'slotId': slot_id, 'campusId': campus_id,
                                'settlementKind': order.settlement_kind,
                                'existingAssignmentIds': [row.id for row in current],
                                'candidateBatchIds': [row.id for row in candidates],
                                'matchStatus': 'UNIQUE_CANDIDATE' if len(candidates) == 1 and not blockers else 'NEEDS_REVIEW',
                                'blockers': blockers,
                                'nextAction': '核对收款、原确认时段、校区及名单；匹配提示仅供检查，经现有调度预览和生产守卫后显式安排，不重新收款或重复占容量。'})

    unconfigured = [
        {'slotId': row.id, 'name': row.name, 'date': row.date, 'version': row.version,
         'enabled': row.enabled, 'archived': bool((row.data or {}).get('archivedAt')),
         'openAt': row.open_at.isoformat() if row.open_at else None,
         'cutoffAt': row.cutoff_at.isoformat() if row.cutoff_at else None,
         'nextAction': '由管理员核实并补齐真实接单规则；补齐前停止新申请和自动归批，已有履约继续按原有效安排执行。'}
        for row in slots.values() if row.open_at is None or row.cutoff_at is None
    ]
    result = {'readOnly': True, 'generatedAt': utcnow().isoformat(),
              'unconfiguredSlots': unconfigured, 'paidUnassignedOrders': paid_unassigned,
              'ambiguousGroups': ambiguous, 'detachedBatches': detached,
              'historicalCounts': {
                  'deliveredOrders': sum(row.status == 'DELIVERED' for row in orders.values()),
                  'cancelledOrders': sum(row.status == 'CANCELLED' for row in orders.values()),
                  'refundedOrders': sum(row.refunded_cents > 0 for row in orders.values()),
                  'trashedOrders': sum(row.deleted_at is not None for row in orders.values()),
                  'trashedBatches': sum(row.deleted_at is not None for row in batches.values()),
                  'openAfterSaleOrders': len(after_sale_orders),
              },
              'nextSteps': [
                  '本接口只读，不提供批量 apply、自动补时间、建组、归批、合并、恢复或通知。',
                  '唯一候选仅表示时段和校区匹配，管理员仍需核对名单及真实履约状态。',
                  '具体修复使用现有配置补齐、客户确认改期及受控配送调度；这些写操作各自执行版本、容量、封批、生产和权限校验。',
                  '已送达、取消、退款、售后和垃圾桶保留各自历史，不能全部重新归批。',
              ]}
    result['counts'] = {key: len(result[key]) for key in (
        'unconfiguredSlots', 'paidUnassignedOrders', 'ambiguousGroups', 'detachedBatches')}
    return result
