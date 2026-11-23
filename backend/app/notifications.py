"""Admin inbox projected from business records. Only personal read receipts are stored."""
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import and_, case, func, literal, select, true, tuple_, union_all
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from .core import Actor, DomainError, DTO, Envelope, actor_dep, ok, require_admin
from .db import get_db
from .delivery import visible_admin_exception
from .models import (
    AfterSale, AuditLog, BindingTask, BusinessException, DeliveryBatch,
    NotificationRead, Order, OrderFile, PrintAttempt, PrintTask, utcnow,
)

from .production import POST_DELIVERY_STATUSES

router = APIRouter(prefix='/admin/notifications')
NotificationFilter = Literal['ALL', 'UNREAD', 'READ', 'PENDING', 'HANDLED']
NotificationKind = Literal[
    'PAYMENT_PENDING', 'PAYMENT_MISMATCH', 'PRINT_FAILED', 'BINDING_REOPENED',
    'DELIVERY_EXCEPTION', 'LOCATION_EXCEPTION', 'AFTER_SALE_REQUESTED',
    'REFUND_PENDING', 'BATCH_PENDING', 'BATCH_CHANGED',
]
TITLES = {
    'PAYMENT_PENDING': '订单待确认收款',
    'PAYMENT_MISMATCH': '实收金额待核对',
    'PRINT_FAILED': '打印任务失败',
    'BINDING_REOPENED': '装订任务已重新打开',
    'DELIVERY_EXCEPTION': '配送异常待处理',
    'LOCATION_EXCEPTION': '定位例外待审批',
    'AFTER_SALE_REQUESTED': '售后申请待审核',
    'REFUND_PENDING': '退款待确认',
    'BATCH_PENDING': '配送批次待开始',
    'BATCH_CHANGED': '配送计划已变更',
}
BATCH_CHANGES = (
    'BATCH_COURIER_ASSIGNED', 'BATCH_UPDATED', 'BATCH_PUBLICATION_WITHDRAWN',
    'BATCH_CANCELLED',
)


class Notification(DTO):
    id: str
    kind: NotificationKind
    title: str
    context: str
    created_at: datetime
    read: bool
    task_status: Literal['PENDING', 'HANDLED'] | None
    target_id: str


class NotificationUnread(DTO):
    unread_count: int


class NotificationPage(NotificationUnread):
    items: list[Notification]
    total: int
    page: int
    page_size: int


def _event(kind, event_key, target_id, context, created_at, pending=None):
    task_status = literal(None) if pending is None else case((pending, 'PENDING'), else_='HANDLED')
    return select(
        (literal(kind + ':') + event_key).label('id'),
        literal(kind).label('kind'),
        target_id.label('target_id'),
        context.label('context'),
        created_at.label('created_at'),
        task_status.label('task_status'),
    )


def _audit_for(action, target_type, target_id):
    return and_(AuditLog.action == action, AuditLog.target_type == target_type, AuditLog.target_id == target_id)


def _later_audit(*actions):
    later = aliased(AuditLog)
    return select(later.id).where(
        later.target_id == AuditLog.target_id,
        later.target_type == AuditLog.target_type,
        later.action.in_(actions),
        tuple_(later.created_at, later.id) > tuple_(AuditLog.created_at, AuditLog.id),
    ).exists()


def _exception(kind, target_id, pending_statuses=('OPEN', 'IN_PROGRESS')):
    return _event(
        kind, BusinessException.id, target_id, Order.pickup_code,
        BusinessException.created_at, BusinessException.status.in_(pending_statuses),
    ).select_from(BusinessException).join(Order, Order.id == BusinessException.order_id).where(
        BusinessException.type == kind, Order.deleted_at.is_(None), visible_admin_exception(),
    )


def _payment_events(now):
    open_payment = and_(Order.status == 'PENDING_CONFIRMATION', Order.close_at > now)
    reopened = select(AuditLog.id).where(_audit_for('ORDER_REOPENED', 'ORDER', Order.id)).exists()
    initial = _event(
        'PAYMENT_PENDING', Order.id, Order.id, Order.pickup_code, Order.created_at,
        and_(open_payment, ~reopened),
    ).where(Order.deleted_at.is_(None))
    reopened_event = _event(
        'PAYMENT_PENDING', literal('reopened:') + AuditLog.id, Order.id,
        Order.pickup_code, AuditLog.created_at,
        and_(open_payment, ~_later_audit('ORDER_REOPENED', 'ORDER_AUTO_CLOSED')),
    ).select_from(AuditLog).join(Order, Order.id == AuditLog.target_id).where(
        AuditLog.action == 'ORDER_REOPENED', AuditLog.target_type == 'ORDER', Order.deleted_at.is_(None),
    )
    sale_request = _event(
        'AFTER_SALE_REQUESTED', AfterSale.id, AfterSale.id, Order.pickup_code,
        AfterSale.created_at, AfterSale.status.in_(('REQUESTED', 'REVIEW_PENDING')),
    ).select_from(AfterSale).join(Order, Order.id == AfterSale.order_id).where(
        AfterSale.deleted_at.is_(None), Order.deleted_at.is_(None),
    )
    refund = _event(
        'REFUND_PENDING', AuditLog.id, AfterSale.id, Order.pickup_code,
        AuditLog.created_at, and_(AfterSale.status == 'REFUND_APPROVED', ~_later_audit('AFTER_SALE_APPROVED')),
    ).select_from(AfterSale).join(Order, Order.id == AfterSale.order_id).join(
        AuditLog, _audit_for('AFTER_SALE_APPROVED', 'AFTER_SALE', AfterSale.id),
    ).where(AfterSale.solution == 'REFUND', AfterSale.deleted_at.is_(None), Order.deleted_at.is_(None))
    return initial, reopened_event, _exception('PAYMENT_MISMATCH', Order.id), sale_request, refund


def _production_events():
    later = aliased(PrintAttempt)
    newer_attempt = select(later.id).where(
        later.task_id == PrintTask.id,
        tuple_(later.created_at, later.id) > tuple_(PrintAttempt.created_at, PrintAttempt.id),
    ).exists()
    failed = _event(
        'PRINT_FAILED', PrintAttempt.id, PrintTask.id, Order.pickup_code,
        func.coalesce(PrintAttempt.completed_at, PrintAttempt.created_at),
        and_(PrintTask.active, PrintTask.status == 'EXCEPTION', ~newer_attempt, Order.status != 'CANCELLED'),
    ).select_from(PrintAttempt).join(PrintTask, PrintTask.id == PrintAttempt.task_id).join(
        Order, Order.id == PrintTask.order_id,
    ).where(PrintAttempt.outcome == 'FAILED', Order.deleted_at.is_(None))
    binding = _event(
        'BINDING_REOPENED', AuditLog.id, BindingTask.id, Order.pickup_code, AuditLog.created_at,
        and_(
            BindingTask.status.in_(('WAITING_PRINT', 'READY')),
            OrderFile.revision == Order.content_revision,
            OrderFile.binding_type != 'NONE',
            Order.status.not_in(('CANCELLED',) + POST_DELIVERY_STATUSES),
            ~_later_audit('production.correction.reopen_binding', 'production.binding_complete'),
        ),
    ).select_from(AuditLog).join(BindingTask, BindingTask.id == AuditLog.target_id).join(
        Order, Order.id == BindingTask.order_id,
    ).join(OrderFile, OrderFile.id == BindingTask.file_id).where(
        AuditLog.action == 'production.correction.reopen_binding',
        AuditLog.target_type == 'BindingTask', Order.deleted_at.is_(None),
    )
    return failed, binding


def _delivery_events():
    pending = _event(
        'BATCH_PENDING', AuditLog.id, DeliveryBatch.id, DeliveryBatch.name, AuditLog.created_at,
        and_(DeliveryBatch.status == 'PUBLISHED',
             ~_later_audit('BATCH_PUBLISHED', 'BATCH_PUBLICATION_WITHDRAWN', 'BATCH_STARTED')),
    ).select_from(AuditLog).join(DeliveryBatch, DeliveryBatch.id == AuditLog.target_id).where(
        AuditLog.target_type == 'DELIVERY_BATCH', AuditLog.action == 'BATCH_PUBLISHED',
        DeliveryBatch.deleted_at.is_(None),
    )
    changed = _event(
        'BATCH_CHANGED', AuditLog.id, DeliveryBatch.id, DeliveryBatch.name, AuditLog.created_at,
    ).select_from(AuditLog).join(DeliveryBatch, DeliveryBatch.id == AuditLog.target_id).where(
        AuditLog.target_type == 'DELIVERY_BATCH', AuditLog.action.in_(BATCH_CHANGES),
        DeliveryBatch.deleted_at.is_(None),
    )
    affected = (func.jsonb_array_elements_text(AuditLog.data['batchIds'])
                .table_valued('value').render_derived().lateral('affected_batch'))
    operations = _event(
        'BATCH_CHANGED', AuditLog.id + literal(':') + DeliveryBatch.id, DeliveryBatch.id,
        DeliveryBatch.name, AuditLog.created_at,
    ).select_from(AuditLog).join(affected, true()).join(
        DeliveryBatch, DeliveryBatch.id == affected.c.value,
    ).where(
        AuditLog.target_type == 'BATCH_PLAN', AuditLog.action == 'BATCH_OPERATION_COMMITTED',
        DeliveryBatch.deleted_at.is_(None),
    )
    return (
        pending, changed, operations,
        _exception('DELIVERY_EXCEPTION', BusinessException.id),
        _exception('LOCATION_EXCEPTION', BusinessException.id, ('OPEN',)),
    )


def _visible_events(actor: Actor):
    caps = actor.capabilities
    sources = []
    if '*' in caps or 'payment.manage' in caps:
        sources.extend(_payment_events(utcnow()))
    if '*' in caps or 'production.manage' in caps:
        sources.extend(_production_events())
    if '*' in caps or 'delivery.manage' in caps:
        sources.extend(_delivery_events())
    if not sources:
        return None
    events = union_all(*sources).subquery()
    return select(
        *events.c,
        ((events.c.task_status.is_not_distinct_from('HANDLED'))
         | NotificationRead.staff_id.is_not(None)).label('read'),
    ).outerjoin(
        NotificationRead,
        and_(NotificationRead.staff_id == actor.staff_id, NotificationRead.notification_id == events.c.id),
    )


def _unread_count(db, actor):
    visible = _visible_events(actor)
    if visible is None:
        return 0
    events = visible.subquery()
    return db.scalar(select(func.count()).select_from(events).where(events.c.read.is_(False)))


@router.get('', response_model=Envelope[NotificationPage])
def list_notifications(
    filter: NotificationFilter = 'ALL',
    page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
    page_size: Annotated[int, Query(alias='pageSize', ge=1, le=100)] = 20,
    db=Depends(get_db), actor: Actor = Depends(actor_dep),
):
    require_admin(actor)
    visible = _visible_events(actor)
    result = {'items': [], 'total': 0, 'unreadCount': 0, 'page': page, 'pageSize': page_size}
    if visible is None:
        return ok(result)
    events = visible.cte('visible_notifications')
    criteria = {
        'ALL': true(), 'UNREAD': events.c.read.is_(False), 'READ': events.c.read.is_(True),
        'PENDING': events.c.task_status == 'PENDING', 'HANDLED': events.c.task_status == 'HANDLED',
    }[filter]
    filtered = select(events).where(criteria).cte('filtered_notifications')
    paged = select(filtered).order_by(
        filtered.c.read, filtered.c.created_at.desc(), filtered.c.id.desc(),
    ).offset((page - 1) * page_size).limit(page_size).subquery()
    counts = select(
        select(func.count()).select_from(filtered).scalar_subquery().label('total'),
        select(func.count()).select_from(events).where(events.c.read.is_(False))
        .scalar_subquery().label('unread_count'),
    ).subquery()
    rows = db.execute(select(counts, paged).outerjoin(paged, true()).order_by(
        paged.c.read, paged.c.created_at.desc(), paged.c.id.desc(),
    )).mappings().all()
    result.update(total=rows[0]['total'], unreadCount=rows[0]['unread_count'], items=[
        Notification(id=row['id'], kind=row['kind'], title=TITLES[row['kind']],
                     context=row['context'], created_at=row['created_at'], read=row['read'],
                     task_status=row['task_status'], target_id=row['target_id'])
        for row in rows if row['id'] is not None
    ])
    return ok(result)


@router.get('/unread', response_model=Envelope[NotificationUnread])
def unread_notifications(db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    require_admin(actor)
    return ok({'unreadCount': _unread_count(db, actor)})


@router.post('/{notification_id}/read', response_model=Envelope[NotificationUnread])
def read_notification(
    notification_id: Annotated[str, Path(min_length=1, max_length=160)],
    db=Depends(get_db), actor: Actor = Depends(actor_dep),
):
    require_admin(actor)
    visible = _visible_events(actor)
    if visible is not None:
        events = visible.subquery()
        found = db.execute(select(events.c.read).where(events.c.id == notification_id)).first()
        if found is not None:
            # Completion only contributes to the derived read status. An explicit
            # read persists even when the business record is still pending.
            if not found.read:
                db.execute(insert(NotificationRead).values(
                    staff_id=actor.staff_id, notification_id=notification_id, read_at=utcnow(),
                ).on_conflict_do_nothing())
            return ok({'unreadCount': _unread_count(db, actor)})
    raise DomainError('NOT_FOUND', '通知不存在或不可访问', 404)
