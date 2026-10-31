"""Human production: print queue, actual start, attempts/results, reprints,
binding, packing, dependency projection and whitelisted corrections.

Ownership: this module only uses shared models and helpers.  Order views,
initial OrderFile/PrintTask/BindingTask/PackingTask creation and payment
settlement belong to Codex commerce; the production snapshot helper below is
the single source of production facts for ``commerce.order_view``.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.core import (
    Actor,
    DomainError,
    Envelope,
    actor_dep,
    audit,
    lock,
    ok,
    require_admin,
    touch,
)
from app.db import get_db
from app.models import (
    Artifact,
    BindingTask,
    BusinessException,
    Order,
    OrderFile,
    PackingTask,
    PrintAttempt,
    PrintTask,
    utcnow,
)
from app.production_schemas import (
    ActualStartInput,
    AttemptResultDTO,
    BindingCompleteInput,
    BindingQueueDTO,
    BindingTaskDTO,
    ChecklistItemDTO,
    CorrectionInput,
    CorrectionResultDTO,
    PackInput,
    PackResultDTO,
    PackingQueueDTO,
    PackingTaskDTO,
    PrintAttemptDTO,
    PrintAttemptInput,
    PrintQueueDTO,
    PrintResultInput,
    PrintTaskDTO,
    ProductionBlockerDTO,
    ProductionChecklistDTO,
    ProductionStartDTO,
    ReprintInput,
    ReprintRecordDTO,
    ReprintResultDTO,
    SheetDTO,
    aggregate_file_print_status,
    binding_status_for,
    checklist_errors,
    checklist_version_matches,
    next_order_status,
    sheet_catalog,
    validate_sheet_selection,
)

router = APIRouter()

DELIVERY_STARTED_STATUSES = ('DELIVERING', 'DELIVERED')
POST_DELIVERY_STATUSES = ('DELIVERING', 'DELIVERED', 'DELIVERY_EXCEPTION')
PRODUCTION_STATUSES = ('PENDING_PRINT', 'READY_FOR_DELIVERY')


# ---------------------------------------------------------------------------
# Small serialization / lookup helpers
# ---------------------------------------------------------------------------


def _iso(value):
    return value.isoformat() if value else None


def _staff(actor: Actor) -> str:
    if not actor.staff_id:
        raise DomainError('AUTH_REQUIRED', '请先登录员工账号', 401)
    return actor.staff_id


def _json_dict(value) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def _task_key(task: PrintTask) -> str:
    settings = _json_dict(task.settings)
    return (
        settings.get('segmentId')
        or settings.get('clientSegmentId')
        or task.id
    )


def _task_reprints(task: PrintTask) -> list[dict]:
    records = _json_dict(task.settings).get('reprints')
    return [dict(row) for row in records if isinstance(row, dict)] if isinstance(records, list) else []


def _task_corrections(task: PrintTask) -> list[dict]:
    records = _json_dict(task.settings).get('corrections')
    return [dict(row) for row in records if isinstance(row, dict)] if isinstance(records, list) else []


def _artifact_settings(artifact: Artifact | None) -> dict:
    return _json_dict(getattr(artifact, 'settings', None))


def _order_settings(order: Order) -> dict:
    return _json_dict(order.data)


def _resolve_task(db, ident: str) -> PrintTask:
    """Accept a PrintTask id, the client segment id, or an unambiguous file id.

    The existing frontend routes attempts/reprints with the client segment id
    in the path, so the server resolves it against the immutable task settings
    instead of trusting a client-supplied task id.
    """
    task = db.get(PrintTask, ident)
    if task is not None:
        return task
    for candidate in db.scalars(select(PrintTask).where(PrintTask.active.is_(True))):
        settings = _json_dict(candidate.settings)
        if ident in (
            settings.get('segmentId'),
            settings.get('clientSegmentId'),
            settings.get('clientSegment'),
        ):
            return candidate
    client_files = list(db.scalars(select(OrderFile).where(OrderFile.client_file_id == ident)))
    if len(client_files) == 1:
        rows = list(
            db.scalars(
                select(PrintTask).where(
                    PrintTask.file_id == client_files[0].id, PrintTask.active.is_(True)
                )
            )
        )
        if len(rows) == 1:
            return rows[0]
    rows = list(db.scalars(select(PrintTask).where(PrintTask.file_id == ident, PrintTask.active.is_(True))))
    if len(rows) == 1:
        return rows[0]
    if len(rows) > 1:
        raise DomainError('AMBIGUOUS_PRINT_TASK', '该文件包含多个打印任务，请指定任务编号', 409)
    raise DomainError('NOT_FOUND', '打印任务不存在或已失效', 404)


def _resolve_binding(db, ident: str) -> BindingTask:
    task = db.get(BindingTask, ident)
    if task is not None:
        return task
    task = db.scalar(select(BindingTask).where(BindingTask.file_id == ident))
    if task is not None:
        return task
    client_files = list(db.scalars(select(OrderFile).where(OrderFile.client_file_id == ident)))
    if len(client_files) == 1:
        task = db.scalar(select(BindingTask).where(BindingTask.file_id == client_files[0].id))
        if task is not None:
            return task
    raise DomainError('NOT_FOUND', '装订任务不存在或已失效', 404)


def _order_of_task(db, task: PrintTask) -> Order:
    order = db.get(Order, task.order_id)
    if order is None or order.deleted_at:
        raise DomainError('NOT_FOUND', '订单不存在或不可访问', 404)
    return order


def _current_files(db, order: Order) -> list[OrderFile]:
    return list(
        db.scalars(
            select(OrderFile)
            .where(OrderFile.order_id == order.id, OrderFile.revision == order.content_revision)
            .order_by(OrderFile.created_at, OrderFile.id)
        )
    )


def _all_print_tasks(db, order: Order) -> list[PrintTask]:
    return list(
        db.scalars(
            select(PrintTask).where(PrintTask.order_id == order.id).order_by(PrintTask.created_at, PrintTask.id)
        )
    )


def _file_is_current(file: OrderFile | None, order: Order) -> bool:
    return bool(file and file.order_id == order.id and file.revision == order.content_revision)


def _task_is_current(db, order: Order, task: PrintTask) -> bool:
    return task.active and _file_is_current(db.get(OrderFile, task.file_id), order)


def _open_attempts_for(db, task: PrintTask) -> list[PrintAttempt]:
    return list(
        db.scalars(
            select(PrintAttempt)
            .where(PrintAttempt.task_id == task.id, PrintAttempt.outcome.is_(None))
            .order_by(PrintAttempt.created_at)
        )
    )


def _open_attempt(db, task: PrintTask) -> PrintAttempt | None:
    return db.scalar(
        select(PrintAttempt)
        .where(PrintAttempt.task_id == task.id, PrintAttempt.outcome.is_(None))
        .order_by(PrintAttempt.created_at.desc())
    )


def _attempts_for(db, task: PrintTask) -> list[PrintAttempt]:
    return list(
        db.scalars(
            select(PrintAttempt)
            .where(PrintAttempt.task_id == task.id)
            .order_by(PrintAttempt.created_at.desc())
        )
    )


def _attempt_dto(attempt: PrintAttempt) -> PrintAttemptDTO:
    return PrintAttemptDTO(
        attempt_id=attempt.id,
        task_id=attempt.task_id,
        artifact_id=attempt.artifact_id,
        outcome=attempt.outcome,
        reason=attempt.reason or '',
        staff_id=attempt.staff_id or '',
        created_at=_iso(attempt.created_at),
        completed_at=_iso(attempt.completed_at),
    )


def _artifact_current(db, order: Order, artifact_id: str) -> Artifact:
    artifact = db.get(Artifact, artifact_id)
    if artifact is None:
        raise DomainError('STALE_PRINT_ARTIFACT', '打印产物不存在或已失效', 409)
    if artifact.preparation_id != order.preparation_id:
        raise DomainError('STALE_PRINT_ARTIFACT', '打印产物与当前订单内容版本不一致', 409)
    return artifact


def _delivery_started(db, order: Order) -> bool:
    if order.status in POST_DELIVERY_STATUSES:
        return True
    from app.models import DeliveryAssignment

    return (
        db.scalar(
            select(DeliveryAssignment.id).where(
                DeliveryAssignment.order_id == order.id,
                DeliveryAssignment.active.is_(True),
                DeliveryAssignment.status.in_(DELIVERY_STARTED_STATUSES),
            )
        )
        is not None
    )


def _require_expected_version(expected: int, primary, *others) -> None:
    """Corrections may be versioned by the owning order or by the target row."""
    rows = (primary, *others)
    if any(row is not None and row.version == expected for row in rows):
        return
    current = primary.version if primary is not None else 0
    raise DomainError('VERSION_CONFLICT', '数据已变化，请刷新后重试', 409, {'currentVersion': current})


def _record_high_risk(
    db, actor: Actor, order: Order, *, action: str, target_type: str, target_id: str, reason: str
) -> None:
    """Persist a compensation fact, then reject the direct status change."""
    exception = BusinessException(
        order_id=order.id,
        type='POST_DELIVERY_PRODUCTION_CORRECTION',
        status='OPEN',
        reason=reason,
        blocks_production=False,
        blocks_delivery=True,
        data={
            'action': action,
            'targetType': target_type,
            'targetId': target_id,
            'actorId': actor.id,
            'orderStatus': order.status,
            'recordedAt': _iso(utcnow()),
        },
    )
    db.add(exception)
    db.flush()
    audit(
        db,
        actor,
        'production.high_risk_correction',
        target_type,
        target_id,
        reason,
        {'orderId': order.id, 'action': action, 'exceptionId': exception.id, 'orderStatus': order.status},
    )
    raise DomainError(
        'HIGH_RISK_CORRECTION',
        '订单已进入配送流程，生产纠错不能直接改状态，已登记异常待补偿处理',
        409,
        {'exceptionId': exception.id, 'orderId': order.id, 'action': action, 'orderStatus': order.status},
        commit=True,
    )


# ---------------------------------------------------------------------------
# Production preconditions
# ---------------------------------------------------------------------------


def _require_order_active(order: Order) -> None:
    if order.deleted_at:
        raise DomainError('NOT_FOUND', '订单不存在或不可访问', 404)
    if order.status == 'CANCELLED':
        raise DomainError('ORDER_CLOSED', '订单已关闭，不能开展生产', 409)


def _refund_locked(order: Order) -> None:
    if order.refund_locked:
        raise DomainError('REFUND_LOCKED', '退款审核期间已锁定新的生产操作，请先完成退款核实', 409)


def _blocking_exceptions(db, order: Order) -> list[BusinessException]:
    return list(
        db.scalars(
            select(BusinessException).where(
                BusinessException.order_id == order.id,
                BusinessException.status == 'OPEN',
                BusinessException.blocks_production.is_(True),
            )
        )
    )


def _raise_for_blocking_exception(db, order: Order) -> None:
    rows = _blocking_exceptions(db, order)
    if rows:
        raise DomainError(
            'PRODUCTION_BLOCKED',
            '存在未解决的生产异常，解除前不能开展生产',
            409,
            {'exceptions': [{'id': row.id, 'type': row.type, 'reason': row.reason} for row in rows]},
        )


def _require_paid(order: Order) -> None:
    """Payment is proven by the human settlement projection, never by a client flag."""
    if order.status == 'PENDING_CONFIRMATION':
        raise DomainError('PAYMENT_REQUIRED', '订单尚未人工确认收款，不能开始生产', 422)
    if order.status not in PRODUCTION_STATUSES:
        raise DomainError('INVALID_ORDER_STATE', '当前订单状态不能开展生产', 409, {'status': order.status})
    if not order.settlement_kind:
        raise DomainError('PAYMENT_REQUIRED', '订单缺少人工结算事实，不能开始生产', 422)


def _require_production_open(db, order: Order) -> None:
    _require_order_active(order)
    _require_paid(order)
    _refund_locked(order)
    _raise_for_blocking_exception(db, order)


def _raise_if_delivery_started(db, actor: Actor, order: Order, *, action: str, target_type: str, target_id: str, reason: str):
    if _delivery_started(db, order):
        _record_high_risk(
            db, actor, order, action=action, target_type=target_type, target_id=target_id, reason=reason
        )


# ---------------------------------------------------------------------------
# Projection: the single dependency/dependency-recompute implementation
# ---------------------------------------------------------------------------


def _ensure_tasks(db, order: Order, files: list[OrderFile]) -> None:
    """Commerce owns initial creation; this only heals a missing projection row."""
    existing_bindings = {
        row.file_id for row in db.scalars(select(BindingTask).where(BindingTask.order_id == order.id))
    }
    for file in files:
        if file.binding_type != 'NONE' and file.id not in existing_bindings:
            task = BindingTask(order_id=order.id, file_id=file.id, status='WAITING_PRINT', data={})
            db.add(task)
            db.flush()
    packing = db.scalar(select(PackingTask).where(PackingTask.order_id == order.id))
    if packing is None:
        db.add(
            PackingTask(
                order_id=order.id,
                status='BLOCKED',
                manifest_revision=order.content_revision,
                data={},
            )
        )
        db.flush()


def _state(db, order: Order) -> dict:
    files = _current_files(db, order)
    all_tasks = _all_print_tasks(db, order)
    current_tasks: list[PrintTask] = []
    stale_tasks: list[PrintTask] = []
    for task in all_tasks:
        if not task.active:
            continue
        if _task_is_current(db, order, task):
            current_tasks.append(task)
        else:
            stale_tasks.append(task)
    tasks_by_file: dict[str, list[PrintTask]] = {file.id: [] for file in files}
    for task in current_tasks:
        tasks_by_file.setdefault(task.file_id, []).append(task)
    open_by_task = {task.id: _open_attempts_for(db, task) for task in current_tasks}
    open_attempts = sum(len(rows) for rows in open_by_task.values())
    binding_by_file = {
        row.file_id: row for row in db.scalars(select(BindingTask).where(BindingTask.order_id == order.id))
    }
    packing = db.scalar(select(PackingTask).where(PackingTask.order_id == order.id))
    open_exception_rows = list(
        db.scalars(
            select(BusinessException).where(
                BusinessException.order_id == order.id,
                BusinessException.status == 'OPEN',
            )
        )
    )
    blocking_rows = [row for row in open_exception_rows if row.blocks_production]
    delivery_block = any(row.blocks_delivery for row in open_exception_rows)
    blocking = bool(blocking_rows)

    file_status: dict[str, str] = {}
    artifacts_ok = True
    for file in files:
        rows = tasks_by_file.get(file.id, [])
        file_status[file.id] = aggregate_file_print_status([task.status for task in rows])
        for task in rows:
            artifact = db.get(Artifact, task.artifact_id)
            if artifact is None or artifact.preparation_id != order.preparation_id:
                artifacts_ok = False

    settled = bool(order.settlement_kind) and order.status not in ('PENDING_CONFIRMATION', 'CANCELLED')
    paid_projection = order.status in PRODUCTION_STATUSES and bool(order.settlement_kind)
    print_ok = bool(files) and artifacts_ok and all(
        file_status.get(file.id) == 'COMPLETED' and tasks_by_file.get(file.id) for file in files
    )
    binding_ok = True
    for file in files:
        task = binding_by_file.get(file.id)
        if file.binding_type == 'NONE':
            continue
        if task is None or task.status != 'COMPLETED':
            binding_ok = False

    desired_binding: dict[str, str] = {}
    for file in files:
        task = binding_by_file.get(file.id)
        desired_binding[file.id] = binding_status_for(
            has_binding=file.binding_type != 'NONE',
            print_status=file_status.get(file.id, 'PENDING'),
            current=task.status if task else None,
        )

    manifest_matches = bool(
        packing
        and checklist_version_matches(
            order.content_revision,
            sent_version=order.content_revision,
            packing_manifest_revision=packing.manifest_revision,
        )
    )
    if order.status in POST_DELIVERY_STATUSES or order.status == 'CANCELLED':
        # Post-delivery/closed facts are frozen; only the explicit compensating
        # correction flow may add an exception, never a projection rewrite.
        desired_packing = order.packing_status or packing.status if packing else order.packing_status
    elif not settled:
        desired_packing = 'BLOCKED'
    elif (
        blocking
        or open_attempts
        or not print_ok
        or not binding_ok
        or not artifacts_ok
    ):
        desired_packing = 'BLOCKED'
    elif packing is not None and packing.status == 'PACKED' and manifest_matches:
        # A packed fact survives projections until an explicit correction or a
        # content revision invalidates it.
        desired_packing = 'PACKED'
    else:
        desired_packing = 'READY'
    desired_order_status = next_order_status(order.status, desired_packing)

    blockers = _build_blockers(
        order,
        files,
        tasks_by_file,
        binding_by_file,
        open_by_task,
        open_exception_rows,
        artifacts_ok,
    )
    checklist = _checklist_payload(
        order, files, tasks_by_file, binding_by_file, open_by_task, desired_packing, blockers
    )
    return {
        'files': files,
        'all_tasks': all_tasks,
        'current_tasks': current_tasks,
        'stale_tasks': stale_tasks,
        'tasks_by_file': tasks_by_file,
        'binding_by_file': binding_by_file,
        'packing': packing,
        'file_status': file_status,
        'open_by_task': open_by_task,
        'open_attempts': open_attempts,
        'blocking_rows': blocking_rows,
        'open_exception_rows': open_exception_rows,
        'delivery_block': delivery_block,
        'paid': paid_projection,
        'print_ok': print_ok,
        'binding_ok': binding_ok,
        'artifacts_ok': artifacts_ok,
        'desired_binding': desired_binding,
        'desired_packing': desired_packing,
        'desired_order_status': desired_order_status,
        'manifest_matches': manifest_matches,
        'checklist': checklist,
        'blockers': blockers,
    }


def _build_blockers(
    order: Order,
    files: list[OrderFile],
    tasks_by_file: dict[str, list[PrintTask]],
    binding_by_file: dict[str, BindingTask],
    open_by_task: dict[str, list[PrintAttempt]],
    open_exception_rows: list[BusinessException],
    artifacts_ok: bool,
) -> list[dict]:
    blockers: list[dict] = []
    if order.deleted_at:
        blockers.append({'code': 'ORDER_DELETED', 'message': '订单已删除'})
    if order.status in POST_DELIVERY_STATUSES:
        # In-flight/completed delivery facts are frozen; production blockers are
        # handled by the compensating exception flow, not re-invented here.
        return blockers
    if order.status == 'CANCELLED':
        blockers.append({'code': 'ORDER_CLOSED', 'message': '订单已关闭'})
    if order.status == 'PENDING_CONFIRMATION':
        blockers.append({'code': 'PAYMENT_REQUIRED', 'message': '订单尚未人工确认收款'})
    if order.refund_locked:
        blockers.append({'code': 'REFUND_LOCKED', 'message': '退款审核中，新生产已锁定'})
    for row in open_exception_rows:
        # Delivery-only blocks (e.g. AFTER_SALE_REPRINT) intentionally do not
        # block packing: after_sales/reprint-ready requires a PACKED order before
        # it releases that delivery block.
        if row.blocks_production:
            blockers.append(
                {'code': 'PRODUCTION_EXCEPTION', 'message': f'存在未解决的生产异常：{row.reason or row.type}'}
            )
    if not files:
        blockers.append({'code': 'NO_CURRENT_FILES', 'message': '订单没有当前有效的文件'})
    for file in files:
        tasks = tasks_by_file.get(file.id, [])
        if not tasks:
            blockers.append(
                {'code': 'NO_PRINT_TASKS', 'message': f'文件没有有效的打印任务：{file.client_file_id}', 'fileId': file.id}
            )
        for task in tasks:
            if open_by_task.get(task.id):
                blockers.append(
                    {
                        'code': 'OPEN_ATTEMPT',
                        'message': '存在未确认的打印尝试，请先核实实际结果',
                        'fileId': file.id,
                        'taskId': task.id,
                    }
                )
        status = aggregate_file_print_status([task.status for task in tasks])
        if tasks and status != 'COMPLETED':
            blockers.append(
                {'code': 'PRINT_INCOMPLETE', 'message': f'文件尚未完成打印：{file.client_file_id}', 'fileId': file.id}
            )
        binding = binding_by_file.get(file.id)
        if file.binding_type != 'NONE' and (binding is None or binding.status != 'COMPLETED'):
            blockers.append(
                {'code': 'BINDING_INCOMPLETE', 'message': f'文件尚未完成装订：{file.client_file_id}', 'fileId': file.id}
            )
    if not artifacts_ok:
        blockers.append({'code': 'STALE_PRINT_ARTIFACT', 'message': '打印产物与当前订单内容版本不一致'})
    return blockers


def _checklist_payload(
    order: Order,
    files: list[OrderFile],
    tasks_by_file: dict[str, list[PrintTask]],
    binding_by_file: dict[str, BindingTask],
    open_by_task: dict[str, list[PrintAttempt]],
    packing_status: str,
    blockers: list[dict],
) -> dict:
    items = []
    for file in files:
        tasks = tasks_by_file.get(file.id, [])
        print_status = aggregate_file_print_status([task.status for task in tasks])
        binding = binding_by_file.get(file.id)
        binding_status = (
            'NOT_REQUIRED' if file.binding_type == 'NONE' else (binding.status if binding else 'WAITING_PRINT')
        )
        open_ids = [attempt.id for task in tasks for attempt in open_by_task.get(task.id, [])]
        complete = print_status == 'COMPLETED' and binding_status in ('COMPLETED', 'NOT_REQUIRED')
        items.append(
            {
                'fileId': file.id,
                'clientFileId': file.client_file_id,
                'kind': file.kind,
                'bindingType': file.binding_type,
                'printStatus': print_status,
                'bindingStatus': binding_status,
                'taskIds': [task.id for task in tasks],
                'taskKeys': [_task_key(task) for task in tasks],
                'openAttemptIds': open_ids,
                'complete': complete,
            }
        )
    return {
        'version': order.content_revision,
        'packingStatus': packing_status,
        'items': items,
        'blockers': blockers,
    }


def refresh_production(db, order: Order) -> None:
    """Recompute print/binding/packing/order projections from persisted facts.

    Must be called inside the caller's locked transaction.  It never completes
    print, binding or packing by itself; it only invalidates stale dependent
    facts and keeps the order status consistent with the packed fact.
    """
    if order is None or order.deleted_at:
        return
    if order.status in ('CANCELLED',) + POST_DELIVERY_STATUSES:
        # Post-delivery facts are immutable outside the compensating correction
        # flow; a projection call must not rewrite them.
        return
    # The shared session has autoflush disabled, so persist the action's facts
    # before the projection queries them (otherwise a just-resolved attempt
    # still looks open).
    db.flush()
    files = _current_files(db, order)
    _ensure_tasks(db, order, files)
    state = _state(db, order)
    packing = state['packing']

    for task in state['stale_tasks']:
        task.active = False
        touch(task)

    for file in files:
        binding = state['binding_by_file'].get(file.id)
        desired = state['desired_binding'][file.id]
        if binding is not None and binding.status != desired:
            before = binding.status
            binding.status = desired
            data = _json_dict(binding.data)
            history = list(data.get('history') or [])
            history.append(
                {
                    'from': before,
                    'to': desired,
                    'at': _iso(utcnow()),
                    'source': 'refresh_production',
                }
            )
            data['history'] = history[-50:]
            binding.data = data
            touch(binding)

    if packing is not None:
        desired_packing = state['desired_packing']
        changed = False
        if packing.manifest_revision != order.content_revision:
            packing.manifest_revision = order.content_revision
            changed = True
        if packing.status != desired_packing:
            before = packing.status
            packing.status = desired_packing
            data = _json_dict(packing.data)
            history = list(data.get('history') or [])
            history.append(
                {
                    'from': before,
                    'to': desired_packing,
                    'at': _iso(utcnow()),
                    'source': 'refresh_production',
                }
            )
            data['history'] = history[-50:]
            packing.data = data
            changed = True
        if changed:
            touch(packing)

    order_changed = False
    if order.packing_status != state['desired_packing']:
        order.packing_status = state['desired_packing']
        order_changed = True
    status_changed = order.status != state['desired_order_status']
    if order.status != state['desired_order_status']:
        order.status = state['desired_order_status']
        order_changed = True
    data = _order_settings(order)
    if data.get('productionChecklist') != state['checklist']:
        data['productionChecklist'] = state['checklist']
        order.data = data
        order_changed = True
    if order_changed:
        touch(order)
    if status_changed:
        from .growth import notify_order_event
        notify_order_event(db,order,'STATUS_UPDATED')


def order_production_view(db, order: Order) -> dict:
    """Read-only production facts for Codex ``commerce.order_view``.

    Returns the current server checklist and per-file merged fields so the
    customer/admin order views never have to recompute print/binding rules.
    """
    db.flush()
    state = _state(db, order)
    files = []
    for file in state['files']:
        tasks = state['tasks_by_file'].get(file.id, [])
        binding = state['binding_by_file'].get(file.id)
        task_results = {}
        attempt_ids = {}
        for task in tasks:
            artifact = db.get(Artifact, task.artifact_id)
            keys = {_task_key(task), task.id}
            if artifact is not None and artifact.client_segment_id:
                keys.add(artifact.client_segment_id)
            for key in keys:
                task_results[key] = task.status
                open_attempt = state['open_by_task'].get(task.id) or []
                if open_attempt:
                    attempt_ids[key] = open_attempt[-1].id
        files.append(
            {
                'fileId': file.id,
                'printStatus': state['file_status'].get(file.id, 'PENDING'),
                'bindingType': file.binding_type,
                'bindingStatus': (
                    'NOT_REQUIRED'
                    if file.binding_type == 'NONE'
                    else (binding.status if binding else 'WAITING_PRINT')
                ),
                'taskResults': task_results,
                'attemptIds': attempt_ids,
                'printTaskIds': [task.id for task in tasks],
            }
        )
    checklist = _checklist_dto(state)
    return {
        'actualStartedAt': _iso(order.actual_started_at),
        'deliveryBlocked': bool(state['delivery_block']),
        'refundLocked': bool(order.refund_locked),
        'packingStatus': state['desired_packing'],
        'checklistVersion': order.content_revision,
        'checklist': checklist.model_dump(by_alias=True, mode='json'),
        'blockers': [dict(row) for row in state['blockers']],
        'files': files,
        'canPack': _can_pack(order, state),
        'taskCount': len(state['current_tasks']),
        'openAttemptCount': state['open_attempts'],
    }


def refund_production_facts(db, order: Order) -> dict:
    """Facts after-sales needs before approving a customer-caused refund (Q04).

    Verification only: no state is changed.  A refund must not be approved
    while any print attempt is unresolved, and an order whose physical start was
    recorded is not an "unstarted" cancellation.
    """
    db.flush()
    open_attempts = list(
        db.scalars(
            select(PrintAttempt)
            .join(PrintTask, PrintAttempt.task_id == PrintTask.id)
            .where(PrintTask.order_id == order.id, PrintAttempt.outcome.is_(None))
            .order_by(PrintAttempt.created_at)
        )
    )
    completed_tasks = db.scalar(
        select(func.count())
        .select_from(PrintTask)
        .where(PrintTask.order_id == order.id, PrintTask.active.is_(True), PrintTask.status == 'COMPLETED')
    )
    return {
        'orderId': order.id,
        'actualStartedAt': _iso(order.actual_started_at),
        'productionStarted': order.actual_started_at is not None,
        'openAttempts': [
            {
                'attemptId': attempt.id,
                'taskId': attempt.task_id,
                'artifactId': attempt.artifact_id,
                'createdAt': _iso(attempt.created_at),
            }
            for attempt in open_attempts
        ],
        'openAttemptCount': len(open_attempts),
        'completedPrintTaskCount': int(completed_tasks or 0),
        'refundLocked': bool(order.refund_locked),
    }


def _can_pack(order: Order, state: dict) -> bool:
    return bool(
        order.status == 'PENDING_PRINT'
        and not order.refund_locked
        and state['paid']
        and state['print_ok']
        and state['binding_ok']
        and state['artifacts_ok']
        and state['open_attempts'] == 0
        and not state['blocking_rows']
        and state['desired_packing'] == 'READY'
    )


def _checklist_dto(state: dict) -> ProductionChecklistDTO:
    payload = state['checklist']
    return ProductionChecklistDTO(
        version=payload['version'],
        generated_at=_iso(utcnow()),
        packing_status=payload['packingStatus'],
        items=[
            ChecklistItemDTO(
                file_id=item['fileId'],
                name=item.get('clientFileId', ''),
                kind=item.get('kind', 'DOCUMENT'),
                binding_type=item.get('bindingType', 'NONE'),
                print_status=item.get('printStatus', 'PENDING'),
                binding_status=item.get('bindingStatus', 'NOT_REQUIRED'),
                task_ids=list(item.get('taskIds') or []),
                open_attempt_ids=list(item.get('openAttemptIds') or []),
                complete=bool(item.get('complete')),
            )
            for item in payload['items']
        ],
        blockers=[
            ProductionBlockerDTO(
                code=row['code'],
                message=row['message'],
                file_id=row.get('fileId'),
                task_id=row.get('taskId'),
            )
            for row in payload['blockers']
        ],
    )


# ---------------------------------------------------------------------------
# Task/queue DTOs
# ---------------------------------------------------------------------------


def _task_dto(db, task: PrintTask, order: Order, *, with_attempts: bool = False) -> PrintTaskDTO:
    artifact = db.get(Artifact, task.artifact_id)
    artifact_settings = _artifact_settings(artifact)
    settings = {**artifact_settings, **_json_dict(task.settings)}
    file = db.get(OrderFile, task.file_id)
    catalog = sheet_catalog(artifact, settings) if artifact is not None else []
    open_attempt = _open_attempt(db, task)
    last_attempt = None
    if with_attempts:
        attempts = _attempts_for(db, task)
        last_attempt = _attempt_dto(attempts[0]) if attempts else None
    reprints = [
        ReprintRecordDTO(
            attempt_id=row.get('attemptId', ''),
            mode=row.get('mode', 'WHOLE'),
            sheet_ids=list(row.get('sheetIds') or []),
            reason=row.get('reason', ''),
            artifact_id=row.get('artifactId', ''),
            created_at=row.get('createdAt', ''),
            exception_id=row.get('exceptionId'),
        )
        for row in _task_reprints(task)
    ]
    return PrintTaskDTO(
        id=task.id,
        order_id=task.order_id,
        pickup_code=order.pickup_code if order else '',
        file_id=task.file_id,
        file_name=(_json_dict(file.data).get('name') if file else '') or (file.client_file_id if file else ''),
        kind=file.kind if file else 'DOCUMENT',
        binding_type=file.binding_type if file else 'NONE',
        status=task.status,
        active=bool(task.active),
        artifact_id=task.artifact_id,
        artifact_sha256=artifact.sha256 if artifact else '',
        pages=artifact.pages if artifact else 0,
        sheet_count=len(catalog),
        copies=max(1, int(settings.get('copies') or 1)),
        sides=str(settings.get('sides') or 'SINGLE').upper(),
        color_mode=str(settings.get('colorMode') or settings.get('color_mode') or 'BW').upper(),
        page_range=str(settings.get('range') or settings.get('pageRange') or ''),
        flip=str(settings.get('flip') or 'NONE').upper(),
        open_attempt_id=open_attempt.id if open_attempt else None,
        last_attempt=last_attempt,
        reprints=reprints,
        order_status=order.status if order else '',
        order_version=order.version if order else 0,
        sheet_catalog=[
            SheetDTO(
                sheet_id=row['sheetId'],
                copy_index=row['copy'],
                sheet=row['sheet'],
                front_page=row['frontPage'],
                back_page=row['backPage'],
                blank_back=row['blankBack'],
            )
            for row in catalog
        ],
    )


def _binding_dto(db, task: BindingTask) -> BindingTaskDTO:
    order = db.get(Order, task.order_id)
    file = db.get(OrderFile, task.file_id)
    tasks = [
        row
        for row in db.scalars(
            select(PrintTask).where(
                PrintTask.file_id == task.file_id,
                PrintTask.order_id == task.order_id,
                PrintTask.active.is_(True),
            )
        )
    ]
    return BindingTaskDTO(
        id=task.id,
        order_id=task.order_id,
        pickup_code=order.pickup_code if order else '',
        file_id=task.file_id,
        file_name=(_json_dict(file.data).get('name') if file else '') or (file.client_file_id if file else ''),
        binding_type=file.binding_type if file else 'NONE',
        status=task.status,
        print_status=aggregate_file_print_status([row.status for row in tasks]),
        order_status=order.status if order else '',
        version=task.version,
    )


def _packing_dto(db, order: Order, packing: PackingTask) -> PackingTaskDTO:
    state = _state(db, order)
    return PackingTaskDTO(
        id=packing.id,
        order_id=order.id,
        pickup_code=order.pickup_code,
        status=packing.status,
        checklist_version=order.content_revision,
        order_status=order.status,
        checklist=_checklist_dto(state),
        version=packing.version,
    )


# ---------------------------------------------------------------------------
# Reprints
# ---------------------------------------------------------------------------


def _create_reprint_artifact(db, order: Order, task: PrintTask, sheet_ids: list[str], actor: Actor) -> Artifact:
    """Use the media-owned helper; never substitute the original whole PDF."""
    try:
        from app import media
    except Exception:  # pragma: no cover - import guarded for deployment ordering
        raise DomainError('REPRINT_UNAVAILABLE', '指定纸张补打暂不可用，请稍后重试', 503)
    helper = getattr(media, 'create_reprint_artifact', None)
    if helper is None:
        raise DomainError('REPRINT_UNAVAILABLE', '指定纸张补打尚未就绪，不能使用原整份 PDF 代替', 503)
    try:
        artifact = helper(db, task, sheet_ids, actor)
    except DomainError:
        raise
    except (NotImplementedError, AttributeError):
        raise DomainError('REPRINT_UNAVAILABLE', '指定纸张补打尚未就绪，不能使用原整份 PDF 代替', 503)
    if artifact is None or not getattr(artifact, 'id', None):
        raise DomainError('REPRINT_ARTIFACT_INVALID', '补打产物生成失败，请稍后重试', 503)
    if artifact.id == task.artifact_id:
        raise DomainError('REPRINT_ARTIFACT_INVALID', '补打必须使用独立的纸张产物，不能复用原整份 PDF', 503)
    if artifact.preparation_id != order.preparation_id:
        raise DomainError('REPRINT_ARTIFACT_INVALID', '补打产物与当前订单内容版本不一致', 409)
    db.add(artifact)
    db.flush()
    return artifact


# ---------------------------------------------------------------------------
# Routes: print queue / actual start / attempts / reprints
# ---------------------------------------------------------------------------


@router.get('/admin/print-tasks', response_model=Envelope[PrintQueueDTO])
def list_print_tasks(
    status: Annotated[str | None, Query()] = None,
    order_id: Annotated[str | None, Query(alias='orderId')] = None,
    kind: Annotated[str | None, Query()] = None,
    include_inactive: Annotated[bool, Query(alias='includeInactive')] = False,
    db=Depends(get_db),
    actor: Actor = Depends(actor_dep),
):
    require_admin(actor, 'production.manage')
    query = select(PrintTask).order_by(PrintTask.created_at, PrintTask.id)
    if order_id:
        query = query.where(PrintTask.order_id == order_id)
    if not include_inactive:
        query = query.where(PrintTask.active.is_(True))
    tasks = []
    counts = {name: 0 for name in ('PENDING', 'ATTEMPTED', 'COMPLETED', 'EXCEPTION')}
    for task in db.scalars(query):
        order = db.get(Order, task.order_id)
        if order is None or order.deleted_at:
            continue
        if order.status in ('CANCELLED',) + POST_DELIVERY_STATUSES:
            continue
        file = db.get(OrderFile, task.file_id)
        if file is not None and kind and file.kind != kind:
            continue
        if status and task.status != status:
            continue
        if task.status in counts:
            counts[task.status] += 1
        tasks.append(_task_dto(db, task, order))
    next_task = next(
        (task.id for task in tasks if task.status in ('PENDING', 'ATTEMPTED', 'EXCEPTION')),
        None,
    )
    return ok(PrintQueueDTO(tasks=tasks, counts=counts, next_task_id=next_task))


@router.get('/admin/print-tasks/{task_id}', response_model=Envelope[PrintTaskDTO])
def get_print_task(
    task_id: str,
    db=Depends(get_db),
    actor: Actor = Depends(actor_dep),
):
    require_admin(actor, 'production.manage')
    task = _resolve_task(db, task_id)
    order = _order_of_task(db, task)
    return ok(_task_dto(db, task, order, with_attempts=True))


@router.post('/admin/orders/{order_id}/start-production', response_model=Envelope[ProductionStartDTO])
def start_production(
    order_id: str,
    payload: ActualStartInput,
    db=Depends(get_db),
    actor: Actor = Depends(actor_dep),
):
    """Record the explicit human fact that physical printing has started."""
    require_admin(actor, 'production.manage')
    _staff(actor)
    order = lock(db, Order, order_id, payload.expected_version)
    _raise_if_delivery_started(
        db,
        actor,
        order,
        action='START_PRODUCTION',
        target_type='Order',
        target_id=order.id,
        reason=payload.reason or '实际开印登记发生在订单进入配送之后',
    )
    _require_production_open(db, order)
    if order.status != 'PENDING_PRINT':
        raise DomainError('INVALID_ORDER_STATE', '当前订单状态不能登记实际开印', 409, {'status': order.status})
    if order.actual_started_at is not None:
        raise DomainError(
            'ACTUAL_START_ALREADY_RECORDED',
            '实际开印已经登记，不能重复覆盖原始事实',
            409,
            {'actualStartedAt': _iso(order.actual_started_at)},
        )
    order.actual_started_at = utcnow()
    touch(order)
    refresh_production(db, order)
    audit(
        db,
        actor,
        'production.actual_start',
        'Order',
        order.id,
        payload.reason,
        {'orderId': order.id, 'actualStartedAt': _iso(order.actual_started_at)},
    )
    return ok(
        ProductionStartDTO(
            order_id=order.id,
            actual_started_at=_iso(order.actual_started_at),
            order_status=order.status,
            version=order.version,
        )
    )


@router.post('/admin/print-tasks/{task_id}/attempts', response_model=Envelope[PrintAttemptDTO])
def create_print_attempt(
    task_id: str,
    payload: PrintAttemptInput,
    db=Depends(get_db),
    actor: Actor = Depends(actor_dep),
):
    require_admin(actor, 'production.manage')
    staff_id = _staff(actor)
    task = _resolve_task(db, task_id)
    order = lock(db, Order, task.order_id, payload.expected_version)
    _raise_if_delivery_started(
        db,
        actor,
        order,
        action='PRINT_ATTEMPT',
        target_type='PrintTask',
        target_id=task.id,
        reason='打印尝试发生在订单进入配送之后',
    )
    _require_production_open(db, order)
    if order.status != 'PENDING_PRINT':
        raise DomainError('PRODUCTION_COMPLETE', '订单已完成装袋，请先撤回装袋再重新打印', 409, {'status': order.status})
    if not _task_is_current(db, order, task):
        raise DomainError('STALE_PRINT_TASK', '打印任务属于旧内容版本或已失效', 409)
    if task.status == 'VOIDED':
        raise DomainError('STALE_PRINT_TASK', '打印任务已作废，不能继续执行', 409)
    if task.status == 'COMPLETED':
        raise DomainError('TASK_COMPLETED', '任务已完成，如需重打请登记补打原因', 409)
    existing = _open_attempt(db, task)
    if existing is not None:
        raise DomainError(
            'OPEN_ATTEMPT',
            '存在未确认的打印尝试，请先核实实际结果',
            409,
            {'attemptId': existing.id, 'taskId': task.id},
        )
    if payload.artifact_id != task.artifact_id:
        raise DomainError('STALE_PRINT_ARTIFACT', '提交的打印文件与当前任务产物不一致', 409)
    _artifact_current(db, order, payload.artifact_id)
    attempt = PrintAttempt(
        task_id=task.id,
        artifact_id=task.artifact_id,
        staff_id=staff_id,
        outcome=None,
        reason='',
    )
    db.add(attempt)
    db.flush()
    task.status = 'ATTEMPTED'
    touch(task)
    refresh_production(db, order)
    audit(
        db,
        actor,
        'production.print_attempt',
        'PrintTask',
        task.id,
        '',
        {'orderId': order.id, 'attemptId': attempt.id, 'artifactId': task.artifact_id},
    )
    return ok(_attempt_dto(attempt))


@router.post('/admin/print-attempts/{attempt_id}/result', response_model=Envelope[AttemptResultDTO])
def record_print_result(
    attempt_id: str,
    payload: PrintResultInput,
    db=Depends(get_db),
    actor: Actor = Depends(actor_dep),
):
    require_admin(actor, 'production.manage')
    _staff(actor)
    attempt = lock(db, PrintAttempt, attempt_id)
    task = lock(db, PrintTask, attempt.task_id)
    order = lock(db, Order, task.order_id, payload.expected_version)
    _raise_if_delivery_started(
        db,
        actor,
        order,
        action='PRINT_RESULT',
        target_type='PrintAttempt',
        target_id=attempt.id,
        reason=payload.reason or '打印结果确认发生在订单进入配送之后',
    )
    # Confirming an existing attempt is verification, not new production: the
    # refund-review lock must not prevent staff from resolving the open attempt
    # that the refund decision depends on.
    _require_order_active(order)
    _require_paid(order)
    if order.status != 'PENDING_PRINT':
        raise DomainError('PRODUCTION_COMPLETE', '订单已完成装袋，请先撤回装袋再记录打印结果', 409)
    if not _task_is_current(db, order, task):
        raise DomainError('STALE_PRINT_TASK', '打印任务属于旧内容版本或已失效', 409)
    if attempt.outcome is not None:
        raise DomainError(
            'ATTEMPT_ALREADY_RESOLVED',
            '该打印尝试已有不可覆盖的结果',
            409,
            {'attemptId': attempt.id, 'outcome': attempt.outcome},
        )
    reprint = next((row for row in _task_reprints(task) if row.get('attemptId') == attempt.id), None)
    if attempt.artifact_id == task.artifact_id:
        _artifact_current(db, order, attempt.artifact_id)
    elif reprint and reprint.get('artifactId') == attempt.artifact_id:
        _artifact_current(db, order, attempt.artifact_id)
    else:
        raise DomainError('STALE_PRINT_ATTEMPT', '该尝试对应的打印产物已失效', 409)
    if payload.outcome == 'COMPLETED' and order.actual_started_at is None:
        raise DomainError(
            'ACTUAL_START_REQUIRED',
            '请先登记实际开印，才能把实体打印确认完成',
            422,
            {'orderId': order.id},
        )
    attempt.outcome = payload.outcome
    if payload.reason:
        attempt.reason = payload.reason
    attempt.completed_at = utcnow()
    touch(attempt)
    task.status = {'COMPLETED': 'COMPLETED', 'FAILED': 'EXCEPTION', 'CANCELLED': 'PENDING'}[payload.outcome]
    touch(task)
    if payload.outcome == 'COMPLETED':
        open_reprint_exceptions = list(
            db.scalars(
                select(BusinessException).where(
                    BusinessException.order_id == order.id,
                    BusinessException.type == 'REPRINT_AFTER_COMPLETION',
                    BusinessException.status == 'OPEN',
                )
            )
        )
        for exception in open_reprint_exceptions:
            exception_task = _json_dict(exception.data).get('taskId')
            if exception_task not in (None, task.id):
                continue
            exception.status = 'RESOLVED'
            data = _json_dict(exception.data)
            data['resolvedAt'] = _iso(utcnow())
            data['resolvedBy'] = actor.id
            data['resolution'] = 'REPRINT_COMPLETED'
            exception.data = data
            touch(exception)
    refresh_production(db, order)
    audit(
        db,
        actor,
        'production.print_result',
        'PrintAttempt',
        attempt.id,
        payload.reason,
        {'orderId': order.id, 'taskId': task.id, 'outcome': payload.outcome},
    )
    return ok(
        AttemptResultDTO(
            attempt_id=attempt.id,
            task_id=task.id,
            outcome=payload.outcome,
            task_status=task.status,
            order_status=order.status,
            packing_status=order.packing_status,
            checklist_version=order.content_revision,
            order_version=order.version,
        )
    )


@router.post('/admin/print-tasks/{task_id}/reprints', response_model=Envelope[ReprintResultDTO])
def create_reprint(
    task_id: str,
    payload: ReprintInput,
    db=Depends(get_db),
    actor: Actor = Depends(actor_dep),
):
    require_admin(actor, 'production.manage')
    staff_id = _staff(actor)
    task = _resolve_task(db, task_id)
    order = lock(db, Order, task.order_id, payload.expected_version)
    _require_order_active(order)
    _raise_if_delivery_started(
        db,
        actor,
        order,
        action='REPRINT',
        target_type='PrintTask',
        target_id=task.id,
        reason=payload.reason,
    )
    _require_production_open(db, order)
    if order.status not in PRODUCTION_STATUSES:
        raise DomainError('INVALID_ORDER_STATE', '当前订单状态不能补打', 409, {'status': order.status})
    if not _task_is_current(db, order, task):
        raise DomainError('STALE_PRINT_TASK', '打印任务属于旧内容版本或已失效', 409)
    existing = _open_attempt(db, task)
    if existing is not None:
        raise DomainError(
            'OPEN_ATTEMPT',
            '存在未确认的打印尝试，请先核实实际结果再补打',
            409,
            {'attemptId': existing.id, 'taskId': task.id},
        )
    if task.status == 'ATTEMPTED':
        raise DomainError('OPEN_ATTEMPT', '任务已有未确认的打印尝试', 409)
    artifact = db.get(Artifact, task.artifact_id)
    if artifact is None or artifact.preparation_id != order.preparation_id:
        raise DomainError('STALE_PRINT_ARTIFACT', '打印产物与当前订单内容版本不一致', 409)

    sheet_ids: list[str] = []
    artifact_id = task.artifact_id
    if payload.mode == 'SHEETS':
        selected, errors = validate_sheet_selection(sheet_catalog(artifact, task.settings), payload.unique_sheet_ids())
        if errors:
            raise DomainError(
                'SHEET_SELECTION_INVALID',
                '补打纸张选择无效，请重新选择完整实体纸张',
                422,
                {'errors': errors},
            )
        sheet_ids = [row['sheetId'] for row in selected]
        reprint_artifact = _create_reprint_artifact(db, order, task, sheet_ids, actor)
        artifact_id = reprint_artifact.id

    was_completed = task.status == 'COMPLETED'
    exception_id = None
    if was_completed:
        # A missing/damaged sheet after completion must open a production
        # exception and invalidate the packed fact before delivery departs.
        # Reuse the existing open exception for this task on repeated attempts.
        exception = next(
            (
                row
                for row in db.scalars(
                    select(BusinessException).where(
                        BusinessException.order_id == order.id,
                        BusinessException.type == 'REPRINT_AFTER_COMPLETION',
                        BusinessException.status == 'OPEN',
                    )
                )
                if _json_dict(row.data).get('taskId') == task.id
            ),
            None,
        )
        if exception is None:
            exception = BusinessException(
                order_id=order.id,
                type='REPRINT_AFTER_COMPLETION',
                status='OPEN',
                reason=payload.reason,
                blocks_production=False,
                blocks_delivery=True,
                data={
                    'taskId': task.id,
                    'mode': payload.mode,
                    'sheetIds': sheet_ids,
                    'actorId': actor.id,
                    'recordedAt': _iso(utcnow()),
                },
            )
            db.add(exception)
        else:
            exception.data = {
                **_json_dict(exception.data),
                'mode': payload.mode,
                'sheetIds': sheet_ids,
                'lastReason': payload.reason,
                'lastActorId': actor.id,
                'lastRecordedAt': _iso(utcnow()),
            }
            touch(exception)
        db.flush()
        exception_id = exception.id

    attempt = PrintAttempt(
        task_id=task.id,
        artifact_id=artifact_id,
        staff_id=staff_id,
        outcome=None,
        reason=payload.reason,
    )
    db.add(attempt)
    db.flush()
    record = {
        'attemptId': attempt.id,
        'mode': payload.mode,
        'sheetIds': sheet_ids,
        'reason': payload.reason,
        'artifactId': artifact_id,
        'createdAt': _iso(utcnow()),
    }
    if exception_id:
        record['exceptionId'] = exception_id
    settings = _json_dict(task.settings)
    settings['reprints'] = [*_task_reprints(task), record][-100:]
    task.settings = settings
    task.status = 'ATTEMPTED'
    touch(task)
    refresh_production(db, order)
    audit(
        db,
        actor,
        'production.reprint',
        'PrintTask',
        task.id,
        payload.reason,
        {
            'orderId': order.id,
            'attemptId': attempt.id,
            'mode': payload.mode,
            'sheetIds': sheet_ids,
            'artifactId': artifact_id,
            'nonBillable': True,
            'exceptionId': exception_id,
        },
    )
    return ok(
        ReprintResultDTO(
            task_id=task.id,
            mode=payload.mode,
            attempt_id=attempt.id,
            artifact_id=artifact_id,
            sheet_ids=sheet_ids,
            task_status=task.status,
            non_billable=True,
            order_version=order.version,
        )
    )


# ---------------------------------------------------------------------------
# Routes: binding / packing
# ---------------------------------------------------------------------------


@router.get('/admin/binding-tasks', response_model=Envelope[BindingQueueDTO])
def list_binding_tasks(
    order_id: Annotated[str | None, Query(alias='orderId')] = None,
    db=Depends(get_db),
    actor: Actor = Depends(actor_dep),
):
    require_admin(actor, 'production.manage')
    query = select(BindingTask).order_by(BindingTask.created_at, BindingTask.id)
    if order_id:
        query = query.where(BindingTask.order_id == order_id)
    tasks = []
    for task in db.scalars(query):
        order = db.get(Order, task.order_id)
        if order is None or order.deleted_at:
            continue
        file = db.get(OrderFile, task.file_id)
        if not _file_is_current(file, order) or file.binding_type == 'NONE':
            continue
        if order.status in ('CANCELLED',) + POST_DELIVERY_STATUSES:
            continue
        tasks.append(_binding_dto(db, task))
    return ok(BindingQueueDTO(tasks=tasks))


@router.post('/admin/binding-tasks/{task_id}/complete', response_model=Envelope[BindingTaskDTO])
def complete_binding(
    task_id: str,
    payload: BindingCompleteInput,
    db=Depends(get_db),
    actor: Actor = Depends(actor_dep),
):
    require_admin(actor, 'production.manage')
    _staff(actor)
    task = _resolve_binding(db, task_id)
    order = lock(db, Order, task.order_id, payload.expected_version)
    _raise_if_delivery_started(
        db,
        actor,
        order,
        action='BINDING_COMPLETE',
        target_type='BindingTask',
        target_id=task.id,
        reason='装订完成请求发生在配送开始之后',
    )
    _require_order_active(order)
    _require_paid(order)
    _refund_locked(order)
    _raise_for_blocking_exception(db, order)
    if order.status != 'PENDING_PRINT':
        raise DomainError('PRODUCTION_COMPLETE', '订单已完成装袋，请先撤回装袋再修改装订', 409)
    file = db.get(OrderFile, task.file_id)
    if not _file_is_current(file, order):
        raise DomainError('STALE_BINDING_TASK', '装订任务属于旧内容版本或已失效', 409)
    if file.binding_type == 'NONE':
        raise DomainError('BINDING_NOT_REQUIRED', '该文件不需要装订', 409)
    if task.status == 'COMPLETED':
        raise DomainError('BINDING_ALREADY_COMPLETED', '装订已完成，如需更正请使用纠错流程', 409)
    tasks = list(
        db.scalars(
            select(PrintTask).where(
                PrintTask.file_id == file.id,
                PrintTask.order_id == order.id,
                PrintTask.active.is_(True),
            )
        )
    )
    if not tasks:
        raise DomainError('NO_PRINT_TASKS', '文件没有有效的打印任务，不能确认装订', 409)
    if any(row.status != 'COMPLETED' for row in tasks):
        raise DomainError('PRINT_INCOMPLETE', '文件尚未完成全部打印，不能确认装订', 409)
    for row in tasks:
        if _open_attempt(db, row) is not None:
            raise DomainError('OPEN_ATTEMPT', '存在未确认的打印尝试，请先核实实际结果', 409)
        _artifact_current(db, order, row.artifact_id)
    task.status = 'COMPLETED'
    data = _json_dict(task.data)
    history = list(data.get('history') or [])
    history.append(
        {
            'from': 'READY',
            'to': 'COMPLETED',
            'at': _iso(utcnow()),
            'staffId': actor.staff_id,
            'fileRevision': order.content_revision,
            'printTaskIds': [row.id for row in tasks],
        }
    )
    data['history'] = history[-50:]
    data['completedAt'] = _iso(utcnow())
    data['completedBy'] = actor.staff_id
    task.data = data
    touch(task)
    refresh_production(db, order)
    audit(
        db,
        actor,
        'production.binding_complete',
        'BindingTask',
        task.id,
        '',
        {'orderId': order.id, 'fileId': file.id, 'fileRevision': order.content_revision},
    )
    return ok(_binding_dto(db, task))


@router.get('/admin/packing-tasks', response_model=Envelope[PackingQueueDTO])
def list_packing_tasks(
    order_id: Annotated[str | None, Query(alias='orderId')] = None,
    db=Depends(get_db),
    actor: Actor = Depends(actor_dep),
):
    require_admin(actor, 'production.manage')
    query = select(PackingTask).order_by(PackingTask.created_at, PackingTask.id)
    if order_id:
        query = query.where(PackingTask.order_id == order_id)
    tasks = []
    for packing in db.scalars(query):
        order = db.get(Order, packing.order_id)
        if order is None or order.deleted_at:
            continue
        if order.status not in PRODUCTION_STATUSES:
            continue
        tasks.append(_packing_dto(db, order, packing))
    return ok(PackingQueueDTO(tasks=tasks))


@router.post('/admin/orders/{order_id}/pack', response_model=Envelope[PackResultDTO])
def pack_order(
    order_id: str,
    payload: PackInput,
    db=Depends(get_db),
    actor: Actor = Depends(actor_dep),
):
    require_admin(actor, 'production.manage')
    _staff(actor)
    order = lock(db, Order, order_id, payload.expected_version)
    _raise_if_delivery_started(
        db,
        actor,
        order,
        action='PACK',
        target_type='Order',
        target_id=order.id,
        reason='装袋请求发生在配送开始之后',
    )
    _require_order_active(order)
    _require_paid(order)
    _refund_locked(order)
    _raise_for_blocking_exception(db, order)
    if order.status != 'PENDING_PRINT':
        raise DomainError('PRODUCTION_COMPLETE', '订单已完成装袋，不能重复装袋', 409, {'status': order.status})
    files = _current_files(db, order)
    if not files:
        raise DomainError('NO_CURRENT_FILES', '订单没有当前有效的文件，不能装袋', 409)
    _ensure_tasks(db, order, files)
    state = _state(db, order)
    packing = state['packing']
    if payload.checklist_version != order.content_revision:
        raise DomainError(
            'CHECKLIST_STALE',
            '装袋清单已变化，请刷新后重新核对',
            409,
            {'currentVersion': order.content_revision},
        )
    required_ids = [file.id for file in files]
    client_aliases = {file.client_file_id: file.id for file in files}
    checks = [
        check.model_copy(update={'file_id': client_aliases.get(check.file_id, check.file_id)})
        if check.file_id not in required_ids
        else check
        for check in payload.checks
    ]
    errors = checklist_errors(required_ids, checks)
    if errors:
        raise DomainError(
            'CHECKLIST_INCOMPLETE',
            '装袋清单核对未通过，请逐项确认当前全部文件',
            422,
            {'errors': errors},
        )
    if not state['print_ok']:
        raise DomainError('PRINT_INCOMPLETE', '仍有文件未完成打印，不能装袋', 409)
    if not state['binding_ok']:
        raise DomainError('BINDING_INCOMPLETE', '仍有文件未完成装订，不能装袋', 409)
    if state['open_attempts']:
        raise DomainError('OPEN_ATTEMPT', '存在未确认的打印尝试，不能装袋', 409)
    if not state['artifacts_ok']:
        raise DomainError('STALE_PRINT_ARTIFACT', '打印产物与当前内容版本不一致', 409)
    open_blocks = [row for row in state['open_exception_rows'] if row.blocks_production]
    if open_blocks:
        raise DomainError(
            'PRODUCTION_BLOCKED',
            '存在未解决的生产异常，解除前不能装袋',
            409,
            {'exceptions': [{'id': row.id, 'type': row.type, 'reason': row.reason} for row in open_blocks]},
        )
    if state['desired_packing'] != 'READY':
        raise DomainError('PACKING_BLOCKED', '生产尚未全部完成，不能装袋', 409)
    if packing is None:  # defensive; _ensure_tasks creates it
        raise DomainError('PACKING_TASK_MISSING', '装袋任务缺失，请刷新后重试', 409)
    if packing.status == 'PACKED':
        raise DomainError('ALREADY_PACKED', '订单已装袋，不能重复确认', 409)
    packed_at = utcnow()
    packing.status = 'PACKED'
    packing.manifest_revision = order.content_revision
    data = _json_dict(packing.data)
    history = list(data.get('history') or [])
    history.append(
        {
            'from': 'READY',
            'to': 'PACKED',
            'at': _iso(packed_at),
            'staffId': actor.staff_id,
            'checklistVersion': order.content_revision,
        }
    )
    data['history'] = history[-20:]
    data['packedAt'] = _iso(packed_at)
    data['packedBy'] = actor.staff_id
    data['checklistVersion'] = order.content_revision
    data['checks'] = [{'fileId': check.file_id, 'confirmed': True} for check in checks]
    data['fileIds'] = required_ids
    packing.data = data
    touch(packing)
    order.packing_status = 'PACKED'
    order.status = 'READY_FOR_DELIVERY'
    touch(order)
    refresh_production(db, order)
    audit(
        db,
        actor,
        'production.pack',
        'Order',
        order.id,
        '',
        {
            'orderId': order.id,
            'checklistVersion': order.content_revision,
            'fileIds': required_ids,
            'packingTaskId': packing.id,
        },
    )
    return ok(
        PackResultDTO(
            order_id=order.id,
            packing_status=order.packing_status,
            status=order.status,
            version=order.version,
            checklist_version=order.content_revision,
            packed_at=_iso(packed_at),
            checked_file_ids=required_ids,
        )
    )


# ---------------------------------------------------------------------------
# Routes: explicit correction whitelist
# ---------------------------------------------------------------------------


@router.post('/admin/corrections', response_model=Envelope[CorrectionResultDTO])
def apply_correction(
    payload: CorrectionInput,
    db=Depends(get_db),
    actor: Actor = Depends(actor_dep),
):
    require_admin(actor, 'production.manage')
    _staff(actor)
    if payload.action == 'REOPEN_PRINT':
        task = _resolve_task(db, payload.target_id)
        order = lock(db, Order, task.order_id)
        _require_expected_version(payload.expected_version, order, task)
        _raise_if_delivery_started(
            db,
            actor,
            order,
            action=payload.action,
            target_type=payload.target_type,
            target_id=task.id,
            reason=payload.reason,
        )
        _require_production_open(db, order)
        if order.status not in PRODUCTION_STATUSES:
            raise DomainError('INVALID_ORDER_STATE', '当前订单状态不能重开打印', 409)
        if not _task_is_current(db, order, task):
            raise DomainError('STALE_PRINT_TASK', '打印任务属于旧内容版本或已失效', 409)
        if task.status not in ('COMPLETED', 'EXCEPTION'):
            raise DomainError(
                'INVALID_CORRECTION_STATE',
                '只有已完成或异常的打印任务才能重开',
                409,
                {'taskStatus': task.status},
            )
        if _open_attempt(db, task) is not None:
            raise DomainError('OPEN_ATTEMPT', '存在未确认的打印尝试，请先核实实际结果', 409)
        before = task.status
        task.status = 'PENDING'
        settings = _json_dict(task.settings)
        corrections = [*_task_corrections(task)]
        corrections.append(
            {
                'action': payload.action,
                'from': before,
                'to': 'PENDING',
                'reason': payload.reason,
                'actorId': actor.id,
                'at': _iso(utcnow()),
            }
        )
        settings['corrections'] = corrections[-50:]
        task.settings = settings
        touch(task)
        refresh_production(db, order)
        audit(
            db,
            actor,
            'production.correction.reopen_print',
            'PrintTask',
            task.id,
            payload.reason,
            {'orderId': order.id, 'from': before, 'to': 'PENDING', 'fileRevision': order.content_revision},
        )
        return ok(
            CorrectionResultDTO(
                action=payload.action,
                target_type=payload.target_type,
                target_id=task.id,
                order_id=order.id,
                task_status=task.status,
                packing_status=order.packing_status,
                order_status=order.status,
                version=order.version,
                delivery_blocked=order.packing_status != 'PACKED',
            )
        )

    if payload.action == 'REOPEN_BINDING':
        task = _resolve_binding(db, payload.target_id)
        order = lock(db, Order, task.order_id)
        _require_expected_version(payload.expected_version, order, task)
        _raise_if_delivery_started(
            db,
            actor,
            order,
            action=payload.action,
            target_type=payload.target_type,
            target_id=task.id,
            reason=payload.reason,
        )
        _require_production_open(db, order)
        if order.status not in PRODUCTION_STATUSES:
            raise DomainError('INVALID_ORDER_STATE', '当前订单状态不能重开装订', 409)
        file = db.get(OrderFile, task.file_id)
        if not _file_is_current(file, order):
            raise DomainError('STALE_BINDING_TASK', '装订任务属于旧内容版本或已失效', 409)
        if task.status != 'COMPLETED':
            raise DomainError(
                'INVALID_CORRECTION_STATE',
                '只有已完成的装订才能重开',
                409,
                {'bindingStatus': task.status},
            )
        task.status = 'READY'
        data = _json_dict(task.data)
        history = list(data.get('history') or [])
        history.append(
            {
                'from': 'COMPLETED',
                'to': 'READY',
                'reason': payload.reason,
                'actorId': actor.id,
                'at': _iso(utcnow()),
            }
        )
        data['history'] = history[-50:]
        task.data = data
        touch(task)
        refresh_production(db, order)
        audit(
            db,
            actor,
            'production.correction.reopen_binding',
            'BindingTask',
            task.id,
            payload.reason,
            {'orderId': order.id, 'from': 'COMPLETED', 'to': 'READY'},
        )
        return ok(
            CorrectionResultDTO(
                action=payload.action,
                target_type=payload.target_type,
                target_id=task.id,
                order_id=order.id,
                binding_status=task.status,
                packing_status=order.packing_status,
                order_status=order.status,
                version=order.version,
                delivery_blocked=order.packing_status != 'PACKED',
            )
        )

    # UNPACK
    if payload.target_type == 'PACKING_TASK':
        packing = lock(db, PackingTask, payload.target_id)
        order = lock(db, Order, packing.order_id)
        _require_expected_version(payload.expected_version, order, packing)
    else:
        order = lock(db, Order, payload.target_id)
        _require_expected_version(payload.expected_version, order)
        packing = db.scalar(select(PackingTask).where(PackingTask.order_id == order.id))
    _raise_if_delivery_started(
        db,
        actor,
        order,
        action=payload.action,
        target_type=payload.target_type,
        target_id=payload.target_id,
        reason=payload.reason,
    )
    _require_production_open(db, order)
    if packing is None:
        raise DomainError('PACKING_TASK_MISSING', '装袋任务不存在', 404)
    if packing.status != 'PACKED' and order.packing_status != 'PACKED':
        raise DomainError('INVALID_CORRECTION_STATE', '订单尚未装袋，不能撤回装袋', 409)
    packing.status = 'READY'
    data = _json_dict(packing.data)
    history = list(data.get('history') or [])
    history.append(
        {
            'from': 'PACKED',
            'to': 'READY',
            'reason': payload.reason,
            'actorId': actor.id,
            'at': _iso(utcnow()),
        }
    )
    data['history'] = history[-50:]
    packing.data = data
    touch(packing)
    order.packing_status = 'READY'
    if order.status == 'READY_FOR_DELIVERY':
        order.status = 'PENDING_PRINT'
    touch(order)
    refresh_production(db, order)
    audit(
        db,
        actor,
        'production.correction.unpack',
        'PackingTask',
        packing.id,
        payload.reason,
        {'orderId': order.id, 'from': 'PACKED', 'to': packing.status},
    )
    return ok(
        CorrectionResultDTO(
            action=payload.action,
            target_type=payload.target_type,
            target_id=packing.id,
            order_id=order.id,
            packing_status=order.packing_status,
            order_status=order.status,
            version=order.version,
            delivery_blocked=True,
        )
    )
