from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import Field, StrictInt, model_validator
from sqlalchemy import func, select

from .commerce import add_history, quote_view
from .core import Actor, DTO, DomainError, Envelope, actor_dep, audit, authorize_order, lock, ok, require_admin, touch
from .db import get_db
from .models import (AfterSale, BindingTask, BusinessException, DeliveryAssignment, DeliveryBatch, DeliveryProof,
    DeliverySlot, Order, PackingTask, PrintAttempt, PrintTask, Quote, Refund, utcnow)

router=APIRouter()
OPEN_STATUSES=('REQUESTED','REVIEW_PENDING','REFUND_APPROVED','REPRINTING','REPRINT_READY')


class AffectedItem(DTO):
    line_key:str
    quantity:StrictInt=Field(gt=0)


class CaseInput(DTO):
    responsibility:Literal['CUSTOMER','MERCHANT']
    solution:Literal['REFUND','REPRINT']
    reason:str=Field(min_length=1,max_length=2000)
    affected_items:list[AffectedItem]=Field(default_factory=list,max_length=200)
    expected_version:StrictInt|None=None


class ReviewInput(DTO):
    expected_version:StrictInt
    decision:Literal['APPROVE','REJECT']
    solution:Literal['REFUND','REPRINT']|None=None
    approved_cents:StrictInt|None=Field(default=None,ge=0)
    adjustment_basis:str=Field(default='',max_length=2000)
    reason:str=Field(min_length=1,max_length=2000)

    @model_validator(mode='after')
    def custom_amount_basis(self):
        if self.approved_cents is not None and not self.adjustment_basis.strip():
            raise ValueError('自定义退款金额必须填写依据；使用系统建议金额时省略approvedCents')
        return self


class PreviewInput(DTO):
    affected_items:list[AffectedItem]=Field(default_factory=list,max_length=200)


class RefundInput(DTO):
    expected_version:StrictInt
    amount_cents:StrictInt=Field(ge=0)
    reason:str=Field(min_length=1,max_length=2000)


class ChoiceInput(DTO):
    expected_version:StrictInt
    solution:Literal['REFUND','REPRINT']


class CaseAction(DTO):
    expected_version:StrictInt
    reason:str=Field(min_length=1,max_length=2000)


class CaseView(DTO):
    id:str
    order_id:str
    version:int
    status:str
    source:str
    responsibility:str
    solution:str
    reason:str
    suggested_cents:int
    approved_cents:int|None=None
    adjustment_basis:str
    data:dict
    created_at:str


class CaseList(DTO):
    items:list[CaseView]
    next_cursor:str|None=None


class RefundPreview(DTO):
    suggested_cents:int
    remaining_cash_cents:int
    affected_items:list[AffectedItem]


def case_view(case,admin=False):
    data=dict(case.data or {})
    if not admin:
        data={k:v for k,v in data.items() if k in ('affectedItems','reviewReason','reviewedAt','refundRecordedAt','refundAmountCents','customerChoiceAt','completedAt','reprintTaskIds')}
    return {'id':case.id,'orderId':case.order_id,'version':case.version,'status':case.status,
        'source':case.source,'responsibility':case.responsibility,'solution':case.solution,'reason':case.reason,
        'suggestedCents':case.suggested_cents,'approvedCents':case.approved_cents,
        'adjustmentBasis':case.adjustment_basis,'data':data,'createdAt':case.created_at.isoformat()}


def calculate_refund(quote,affected_items):
    lines={line['key']:line for line in quote.get('lines',[])}
    allocations={item['lineKey']:item['amountCents'] for item in quote.get('discountAllocations',[])}
    if not affected_items:
        affected_items=[{'lineKey':line['key'],'quantity':line['quantity']} for line in lines.values()]
    result=0;seen=set()
    for raw in affected_items:
        item=raw.model_dump(by_alias=True) if isinstance(raw,DTO) else raw
        key,quantity=item['lineKey'],item['quantity']
        if key in seen or key not in lines:
            raise DomainError('REFUND_ITEMS_INVALID','受影响项目重复或不属于原报价',422)
        seen.add(key);line=lines[key]
        total_quantity=line['quantity']
        if not isinstance(quantity,int) or isinstance(quantity,bool) or quantity<=0 or quantity>total_quantity:
            raise DomainError('REFUND_ITEMS_INVALID','受影响数量超出原报价',422)
        net=line['amountCents']-allocations.get(key,0)
        # Stable per-unit remainder assignment preserves exact original integer cents.
        unit,remainder=divmod(net,total_quantity)
        result+=unit*quantity+min(quantity,remainder)
    return result


def _pending_attempts(db,order):
    return db.scalar(select(PrintAttempt.id).join(PrintTask,PrintTask.id==PrintAttempt.task_id)
        .where(PrintTask.order_id==order.id,PrintTask.active.is_(True),PrintAttempt.outcome.is_(None)).limit(1))


def _unlock(db,order):
    order.refund_locked=bool(db.scalar(select(AfterSale.id).where(AfterSale.order_id==order.id,
        AfterSale.status.in_(['REQUESTED','REVIEW_PENDING','REFUND_APPROVED']),AfterSale.solution=='REFUND').limit(1)))
    touch(order)


def create_case(db,actor,order,body,admin=False):
    if not admin:authorize_order(db,actor,order,'ORDER_AFTER_SALES')
    else:require_admin(actor,'payment.manage')
    if not order.settlement_kind:
        raise DomainError('SETTLEMENT_REQUIRED','尚未结算订单请直接撤销；实际转款可联系管理员核对',409)
    if order.deleted_at:
        raise DomainError('NOT_FOUND','订单不可访问',404)
    if body.responsibility=='CUSTOMER' and (order.actual_started_at or body.solution!='REFUND'):
        raise DomainError('PRODUCTION_ALREADY_STARTED','实际打印开始后不支持客户自身原因退款；新增内容请新下单',409)
    if db.scalar(select(AfterSale.id).where(AfterSale.order_id==order.id,AfterSale.status.in_(OPEN_STATUSES))):
        raise DomainError('AFTER_SALE_ALREADY_OPEN','已有未结束售后，请先处理当前申请',409)
    if order.cash_received_cents>0 and order.refunded_cents>=order.cash_received_cents:
        raise DomainError('ALREADY_FULLY_REFUNDED','该单已全额退款',409)
    quote=quote_view(db.get(Quote,order.quote_id))
    items=[item.model_dump(by_alias=True) for item in body.affected_items]
    suggested=order.cash_received_cents-order.refunded_cents if body.responsibility=='CUSTOMER' else min(calculate_refund(quote,items),order.cash_received_cents-order.refunded_cents)
    case=AfterSale(order_id=order.id,source='ADMIN' if admin else 'CUSTOMER',actor_id=actor.id,
        responsibility=body.responsibility,solution=body.solution,reason=body.reason.strip(),suggested_cents=suggested,
        data={'affectedItems':items,'orderVersionAtRequest':order.version,'quoteId':order.quote_id})
    db.add(case);db.flush()
    if body.solution=='REFUND':order.refund_locked=True
    touch(order);add_history(order,'售后申请已受理','等待管理员核实；申请不代表已批准或已退款')
    audit(db,actor,'AFTER_SALE_REQUESTED','AFTER_SALE',case.id,data={'source':case.source,'solution':case.solution})
    return case


@router.post('/orders/{order_id}/after-sales',response_model=Envelope[CaseView])
def customer_create(order_id:str,body:CaseInput,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    order=lock(db,Order,order_id,body.expected_version)
    return ok(case_view(create_case(db,actor,order,body)))


@router.post('/admin/orders/{order_id}/after-sales',response_model=Envelope[CaseView])
def admin_create(order_id:str,body:CaseInput,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    require_admin(actor,'payment.manage');order=lock(db,Order,order_id,body.expected_version)
    return ok(case_view(create_case(db,actor,order,body,True),True))


@router.get('/orders/{order_id}/after-sales',response_model=Envelope[CaseList])
def customer_list(order_id:str,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    order=db.get(Order,order_id);authorize_order(db,actor,order)
    return ok({'items':[case_view(row) for row in db.scalars(select(AfterSale).where(AfterSale.order_id==order.id).order_by(AfterSale.created_at.desc()))],'nextCursor':None})


@router.get('/admin/after-sales',response_model=Envelope[CaseList])
def admin_list(status:str|None=None,limit:int=Query(default=50,ge=1,le=100),db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    require_admin(actor,'payment.manage')
    query=select(AfterSale)
    if status:query=query.where(AfterSale.status==status)
    return ok({'items':[case_view(row,True) for row in db.scalars(query.order_by(AfterSale.created_at.desc()).limit(limit))],'nextCursor':None})


@router.get('/after-sales/{case_id}',response_model=Envelope[CaseView])
def get_case(case_id:str,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    case=db.get(AfterSale,case_id)
    if not case:raise DomainError('NOT_FOUND','售后申请不存在',404)
    authorize_order(db,actor,db.get(Order,case.order_id))
    return ok(case_view(case,False))


@router.get('/admin/after-sales/{case_id}',response_model=Envelope[CaseView])
def admin_get_case(case_id:str,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    require_admin(actor)
    if '*' not in actor.capabilities and not actor.capabilities.intersection({'payment.manage','production.manage'}):
        raise DomainError('FORBIDDEN','账号未获售后查看权限',403)
    case=db.get(AfterSale,case_id)
    if not case:raise DomainError('NOT_FOUND','售后申请不存在',404)
    return ok(case_view(case,True))


@router.post('/orders/{order_id}/after-sales/{case_id}/choice',response_model=Envelope[CaseView])
def change_choice(order_id:str,case_id:str,body:ChoiceInput,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    order=lock(db,Order,order_id);authorize_order(db,actor,order,'ORDER_AFTER_SALES')
    case=lock(db,AfterSale,case_id,body.expected_version)
    if case.order_id!=order.id or case.status not in ('REQUESTED','REVIEW_PENDING'):
        raise DomainError('INVALID_TRANSITION','当前售后不能更改处理选择',409)
    if case.responsibility=='CUSTOMER' and body.solution!='REFUND':
        raise DomainError('INVALID_SOLUTION','客户新增内容须新下单',422)
    case.solution=body.solution;case.data={**case.data,'customerChoiceAt':utcnow().isoformat()};touch(case)
    db.flush();_unlock(db,order)
    return ok(case_view(case))


def _lock_case_order(db,case_id,expected_version=None):
    # Match customer mutations: parent order first, then the case. Reading only
    # the FK before locking avoids holding a case lock while waiting for Order.
    order_id=db.scalar(select(AfterSale.order_id).where(AfterSale.id==case_id))
    if order_id is None:raise DomainError('NOT_FOUND','售后申请不存在',404)
    order=lock(db,Order,order_id)
    case=lock(db,AfterSale,case_id,expected_version)
    return case,order


def _refresh_affected_batches(db,batch_ids):
    from .delivery import refresh_batch
    # Session autoflush is disabled: projections must see deactivations first.
    db.flush()
    for batch_id in sorted(batch_ids):
        lock(db,DeliveryBatch,batch_id)
        refresh_batch(db,batch_id)


@router.post('/admin/after-sales/{case_id}/refund-preview',response_model=Envelope[RefundPreview])
def preview_refund(case_id:str,body:PreviewInput,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    require_admin(actor,'payment.manage');case,order=_lock_case_order(db,case_id)
    items=[item.model_dump(by_alias=True) for item in body.affected_items] if body.affected_items else case.data.get('affectedItems',[])
    amount=calculate_refund(quote_view(db.get(Quote,order.quote_id)),items)
    return ok({'suggestedCents':min(amount,order.cash_received_cents-order.refunded_cents),
        'remainingCashCents':order.cash_received_cents-order.refunded_cents,'affectedItems':items})


def review_case(db,actor,case,body):
    require_admin(actor,'payment.manage')
    order=lock(db,Order,case.order_id)
    if case.status not in ('REQUESTED','REVIEW_PENDING'):
        raise DomainError('INVALID_TRANSITION','该售后已审核，请核实已有结果',409)
    if body.decision=='REJECT':
        case.status='REJECTED';case.data={**case.data,'reviewReason':body.reason,'reviewedAt':utcnow().isoformat()}
        touch(case);db.flush();_unlock(db,order)
        audit(db,actor,'AFTER_SALE_REJECTED','AFTER_SALE',case.id,body.reason)
        return case
    if body.solution and body.solution!=case.solution:
        raise DomainError('CUSTOMER_CHOICE_REQUIRED','处理方式须与客户选择一致；请先由客户确认',409)
    if case.responsibility=='CUSTOMER':
        if order.actual_started_at:
            raise DomainError('PRODUCTION_ALREADY_STARTED','实际开印后不支持客户自身原因退款',409)
        if _pending_attempts(db,order):
            raise DomainError('PRINT_ATTEMPT_UNRESOLVED','有未结打印尝试，请先核实实体开印情况',409)
    if case.solution=='REFUND':
        amount=case.suggested_cents if body.approved_cents is None else body.approved_cents
        if amount>order.cash_received_cents-order.refunded_cents:
            raise DomainError('REFUND_EXCEEDS_CASH','累计现金退款不能超过实收',422)
        if case.responsibility=='CUSTOMER' and amount!=order.cash_received_cents-order.refunded_cents:
            raise DomainError('FULL_REFUND_REQUIRED','未开印的自身原因取消按已批准规则全额退款',422)
        case.approved_cents=amount;case.adjustment_basis=body.adjustment_basis.strip();case.status='REFUND_APPROVED'
        order.refund_locked=True
    else:
        if case.responsibility!='MERCHANT':raise DomainError('INVALID_SOLUTION','免费重做须核实商家责任',422)
        if _pending_attempts(db,order):raise DomainError('PRINT_ATTEMPT_UNRESOLVED','请先核实未结打印尝试',409)
        # Rework preserves original artifacts and all old attempts/proofs, starting a new execution.
        task_ids=[]
        for task in db.scalars(select(PrintTask).where(PrintTask.order_id==order.id,PrintTask.active.is_(True)).with_for_update()):
            task.status='EXCEPTION';task.settings={**task.settings,'afterSaleId':case.id};touch(task);task_ids.append(task.id)
        for binding in db.scalars(select(BindingTask).where(BindingTask.order_id==order.id)):
            binding.status='WAITING_PRINT';touch(binding)
        packing=db.scalar(select(PackingTask).where(PackingTask.order_id==order.id))
        if packing:packing.status='BLOCKED';touch(packing)
        affected_batches=set()
        for assignment in db.scalars(select(DeliveryAssignment).where(DeliveryAssignment.order_id==order.id,DeliveryAssignment.active.is_(True)).order_by(DeliveryAssignment.id).with_for_update()):
            affected_batches.add(assignment.batch_id)
            assignment.active=False
            if assignment.status not in ('DELIVERED','CANCELLED'):assignment.status='EXCEPTION'
            touch(assignment)
        _refresh_affected_batches(db,affected_batches)
        order.status='PENDING_PRINT';order.packing_status='BLOCKED';order.refund_locked=False
        case.status='REPRINTING';case.data={**case.data,'reprintTaskIds':task_ids}
        db.add(BusinessException(order_id=order.id,type='AFTER_SALE_REPRINT',reason='商家责任售后重做，完成核对后重新安排交付',blocks_delivery=True,data={'afterSaleId':case.id}))
    case.data={**case.data,'reviewReason':body.reason,'reviewedAt':utcnow().isoformat(),'reviewedBy':actor.staff_id}
    touch(case);touch(order)
    add_history(order,'售后已审核','请按客户确认的方式处理；退款批准不代表已完成线下退款')
    audit(db,actor,'AFTER_SALE_APPROVED','AFTER_SALE',case.id,body.reason,
        {'suggestedCents':case.suggested_cents,'approvedCents':case.approved_cents,'adjusted':body.approved_cents is not None})
    return case


@router.post('/admin/after-sales/{case_id}/review',response_model=Envelope[CaseView])
def review(case_id:str,body:ReviewInput,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    require_admin(actor,'payment.manage');case,order=_lock_case_order(db,case_id,body.expected_version)
    return ok(case_view(review_case(db,actor,case,body),True))


def record_refund(db,actor,case,body):
    require_admin(actor,'payment.manage');order=lock(db,Order,case.order_id)
    if case.status!='REFUND_APPROVED' or body.amount_cents!=case.approved_cents:
        raise DomainError('REFUND_APPROVAL_MISMATCH','实际退款须与本次批准金额一致',409)
    if order.refunded_cents+body.amount_cents>order.cash_received_cents:
        raise DomainError('REFUND_EXCEEDS_CASH','累计现金退款不能超过实收',422)
    if case.responsibility=='CUSTOMER' and (order.actual_started_at or _pending_attempts(db,order)):
        raise DomainError('PRODUCTION_REVIEW_REQUIRED','请先核实是否已实际开印',409)
    row=Refund(order_id=order.id,after_sale_id=case.id,amount_cents=body.amount_cents,staff_id=actor.staff_id,reason=body.reason,
        data={'affectedItems':case.data.get('affectedItems',[]),'suggestedCents':case.suggested_cents,'adjustmentBasis':case.adjustment_basis})
    db.add(row);db.flush()
    order.refunded_cents+=body.amount_cents
    full=order.refunded_cents==order.cash_received_cents
    if full and order.status!='DELIVERED':
        order.status='CANCELLED'
        affected_batches=set()
        for assignment in db.scalars(select(DeliveryAssignment).where(DeliveryAssignment.order_id==order.id,DeliveryAssignment.active.is_(True)).order_by(DeliveryAssignment.id).with_for_update()):
            affected_batches.add(assignment.batch_id)
            assignment.active=False
            if assignment.status!='DELIVERED':assignment.status='CANCELLED'
            touch(assignment)
        _refresh_affected_batches(db,affected_batches)
        if order.confirmed_slot_id:
            slot=lock(db,DeliverySlot,order.confirmed_slot_id)
            if slot.occupied>0:slot.occupied-=1;touch(slot)
        for task in db.scalars(select(PrintTask).where(PrintTask.order_id==order.id,PrintTask.active.is_(True))):
            task.active=False
            if task.status!='COMPLETED':task.status='VOIDED'
            touch(task)
    case.status='REFUNDED'
    case.data={**case.data,'refundRecordedAt':utcnow().isoformat(),'refundAmountCents':body.amount_cents,'refundId':row.id}
    touch(case);db.flush();_unlock(db,order)
    from .growth import on_refund
    on_refund(db,order,row)
    from .payroll import on_refund as update_wages_for_refund
    update_wages_for_refund(db,order,row)
    from .growth import notify_order_event
    notify_order_event(db,order,'STATUS_UPDATED')
    add_history(order,'零元结算已取消' if order.cash_received_cents==0 else '管理员已登记线下退款',
        '保留原报价、结算和处理历史；补偿券及邀请奖励按规则处理')
    audit(db,actor,'REFUND_RECORDED','REFUND',row.id,body.reason,{'amountCents':row.amount_cents})
    return case


@router.post('/admin/after-sales/{case_id}/record-refund',response_model=Envelope[CaseView])
def record(case_id:str,body:RefundInput,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    require_admin(actor,'payment.manage');case,order=_lock_case_order(db,case_id,body.expected_version)
    return ok(case_view(record_refund(db,actor,case,body),True))


@router.post('/admin/after-sales/{case_id}/reprint-ready',response_model=Envelope[CaseView])
def reprint_ready(case_id:str,body:CaseAction,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    require_admin(actor,'production.manage');case,order=_lock_case_order(db,case_id,body.expected_version)
    if case.status!='REPRINTING' or order.packing_status!='PACKED':
        raise DomainError('PACKING_REQUIRED','重做须全部生产及人工装袋完成',409)
    tasks=list(db.scalars(select(PrintTask).where(PrintTask.id.in_(case.data.get('reprintTaskIds',[])))))
    if not tasks or any(task.status!='COMPLETED' for task in tasks):raise DomainError('PRODUCTION_INCOMPLETE','重做任务未完成',409)
    for exception in db.scalars(select(BusinessException).where(BusinessException.order_id==order.id,BusinessException.type=='AFTER_SALE_REPRINT',BusinessException.status=='OPEN')):
        if exception.data.get('afterSaleId')==case.id:exception.status='RESOLVED';touch(exception)
    case.status='REPRINT_READY';touch(case)
    audit(db,actor,'REPRINT_READY','AFTER_SALE',case.id,body.reason)
    return ok(case_view(case,True))


@router.post('/admin/after-sales/{case_id}/complete',response_model=Envelope[CaseView])
def complete_case(case_id:str,body:CaseAction,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    require_admin(actor,'production.manage');case,order=_lock_case_order(db,case_id,body.expected_version)
    proof=db.scalar(select(DeliveryProof).where(DeliveryProof.order_id==order.id,DeliveryProof.created_at>=case.created_at).order_by(DeliveryProof.created_at.desc()).limit(1))
    if case.status!='REPRINT_READY' or order.status!='DELIVERED' or not proof:
        raise DomainError('REDELIVERY_REQUIRED','免费重做尚未完成重新交付',409)
    case.status='COMPLETED';case.data={**case.data,'completedAt':utcnow().isoformat()};touch(case)
    audit(db,actor,'AFTER_SALE_COMPLETED','AFTER_SALE',case.id,body.reason)
    return ok(case_view(case,True))


@router.post('/orders/{order_id}/after-sales/{case_id}/withdraw',response_model=Envelope[CaseView])
def withdraw_case(order_id:str,case_id:str,body:CaseAction,db=Depends(get_db),actor:Actor=Depends(actor_dep)):
    order=lock(db,Order,order_id);authorize_order(db,actor,order,'ORDER_AFTER_SALES');case=lock(db,AfterSale,case_id,body.expected_version)
    if case.order_id!=order.id or case.status not in ('REQUESTED','REVIEW_PENDING'):
        raise DomainError('INVALID_TRANSITION','该申请当前不能撤回',409)
    case.status='CANCELLED';touch(case);db.flush();_unlock(db,order)
    audit(db,actor,'AFTER_SALE_WITHDRAWN','AFTER_SALE',case.id,body.reason)
    return ok(case_view(case))
