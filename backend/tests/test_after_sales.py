from datetime import timedelta
import pytest

from app.core import Actor,DomainError
from app.models import AfterSale,Order,utcnow
from test_commerce import ready_preparation


def test_refund_suggestion_uses_original_net_line_allocation():
    from app.after_sales import calculate_refund
    quote={'lines':[{'key':'a','quantity':3,'amountCents':100},{'key':'b','quantity':1,'amountCents':40}],
           'discountAllocations':[{'lineKey':'a','amountCents':10},{'lineKey':'b','amountCents':0}]}
    assert calculate_refund(quote,[{'lineKey':'a','quantity':2}])==60
    assert calculate_refund(quote,[])==130


def test_custom_refund_requires_adjustment_basis():
    from app.after_sales import ReviewInput
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ReviewInput.model_validate({'expectedVersion':1,'decision':'APPROVE','approvedCents':50,'reason':'核实'})


def test_self_reason_refund_rejected_after_actual_start(db,ready_preparation):
    from app.after_sales import create_case,CaseInput
    from app.commerce import submit_order,OrderSubmit
    actor,prep,artifact,slot,body=ready_preparation
    order=submit_order(db,actor,OrderSubmit.model_validate(body))
    order.status='PENDING_PRINT';order.settlement_kind='CASH_MANUAL';order.cash_received_cents=10
    order.actual_started_at=utcnow()
    db.flush()
    with pytest.raises(DomainError) as error:
        create_case(db,actor,order,CaseInput(responsibility='CUSTOMER',solution='REFUND',reason='不需要了'))
    assert error.value.code=='PRODUCTION_ALREADY_STARTED'


APP_MODULES=['auth','commerce','after_sales','growth','mail','production','payroll','supplementary']


@pytest.mark.parametrize('admin', [False, True])
def test_creating_after_sale_queues_admin_mail_in_same_transaction(
        db, ready_preparation, staff_factory, admin):
    from sqlalchemy import select
    from app.after_sales import create_case, CaseInput
    from app.commerce import submit_order, OrderSubmit
    from app.core import unseal
    from app.models import EmailJob
    from test_mail import seed_mail_config

    actor, prep, artifact, slot, body = ready_preparation
    order = submit_order(db, actor, OrderSubmit.model_validate(body))
    order.status = 'PENDING_PRINT'
    order.settlement_kind = 'CASH_MANUAL'
    order.cash_received_cents = 10
    member = staff_factory(email='reviewer@example.invalid', email_verified=True,
                           capabilities=['payment.manage'])
    config = seed_mail_config(db, 12345)
    config.data = {**config.data, 'security': 'SSL_TLS'}
    db.commit()
    if admin:
        actor = Actor(staff_id=member.id, staff_role='ADMIN', capabilities={'payment.manage'})
    case = create_case(db, actor, order, CaseInput(
        responsibility='MERCHANT', solution='REFUND', reason='材料错误，请退款'), admin)
    jobs = list(db.scalars(select(EmailJob).where(
        EmailJob.template_key == 'ADMIN_AFTER_SALE_CREATED')))
    assert len(jobs) == 1
    payload = unseal(jobs[0].protected_payload)
    assert case.id in payload['textBody']
    assert '退款' in payload['textBody']
    assert ('管理员代建' if admin else '客户申请') in payload['textBody']
    db.rollback()
    assert list(db.scalars(select(EmailJob))) == []
    assert list(db.scalars(select(AfterSale))) == []


def test_employee_uses_customer_surface_for_own_after_sale(db,ready_preparation,customer_client,staff_factory):
    from app.after_sales import create_case,CaseInput
    from app.commerce import submit_order,OrderSubmit
    from app.models import Principal
    actor,prep,artifact,slot,body=ready_preparation
    order=submit_order(db,actor,OrderSubmit.model_validate(body))
    order.settlement_kind='CASH_MANUAL';order.cash_received_cents=10;order.status='PENDING_PRINT'
    case=create_case(db,actor,order,CaseInput(responsibility='MERCHANT',solution='REFUND',reason='错印'))
    db.get(Principal,order.principal_id).account_id=customer_client.account.id
    staff_factory(role='COURIER',account_id=customer_client.account.id)
    db.commit()
    response=customer_client.get('/api/v1/after-sales/'+case.id)
    assert response.status_code==200,response.text
    assert response.json()['data']['id']==case.id


@pytest.mark.parametrize('solution',['REPRINT','REFUND'])
def test_after_sale_closes_batch_with_no_remaining_delivery(db,ready_preparation,staff_factory,solution):
    from app.after_sales import create_case,CaseInput,ReviewInput,RefundInput,review_case,record_refund
    from app.commerce import submit_order,OrderSubmit
    from app.models import DeliveryBatch,DeliveryAssignment
    actor,prep,artifact,slot,body=ready_preparation
    order=submit_order(db,actor,OrderSubmit.model_validate(body))
    order.status='DELIVERING';order.settlement_kind='CASH_MANUAL';order.cash_received_cents=10
    admin=staff_factory()
    operator=Actor(staff_id=admin.id,staff_role='ADMIN',capabilities={'*'})
    batch=DeliveryBatch(name='原交付批次',courier_id=admin.id,starts_at=utcnow(),ends_at=utcnow()+timedelta(hours=1),status='DELIVERING')
    db.add(batch);db.flush()
    assignment=DeliveryAssignment(order_id=order.id,batch_id=batch.id,courier_id=admin.id,status='STARTED',active=True)
    db.add(assignment);db.flush()
    case=create_case(db,operator,order,CaseInput(responsibility='MERCHANT',solution=solution,reason='商家核实错印'),True)
    review_case(db,operator,case,ReviewInput(expectedVersion=case.version,decision='APPROVE',reason='核实处理'))
    if solution=='REFUND':
        record_refund(db,operator,case,RefundInput(expectedVersion=case.version,amountCents=10,reason='现金退回'))
    db.flush()
    assert assignment.active is False
    assert batch.status=='CANCELLED'


def test_settled_order_with_open_print_attempt_cannot_cancel_or_approve_customer_refund(
    db, ready_preparation, staff_factory,
):
    from sqlalchemy import select

    from app.after_sales import CaseInput, ReviewInput, create_case, review_case
    from app.commerce import OrderSubmit, cancel_pending, submit_order
    from app.models import PrintTask
    from app.production import create_print_attempt, start_production
    from app.production_schemas import ActualStartInput, PrintAttemptInput

    customer, _, _, _, body = ready_preparation
    order = submit_order(db, customer, OrderSubmit.model_validate(body))
    order.status = 'PENDING_PRINT'
    order.settlement_kind = 'CASH_MANUAL'
    order.cash_received_cents = order.total_cents
    staff = staff_factory()
    admin = Actor(staff_id=staff.id, staff_role='ADMIN', capabilities={'*'})
    db.flush()
    task = db.scalar(select(PrintTask).where(PrintTask.order_id == order.id))

    with pytest.raises(DomainError, match='不能直接撤销'):
        cancel_pending(db, order, '取消订单', customer)
    attempt = create_print_attempt(
        task.id, PrintAttemptInput(expectedVersion=order.version, artifactId=task.artifact_id),
        db=db, actor=admin,
    )
    assert order.actual_started_at is None
    case = create_case(db, customer, order, CaseInput(
        responsibility='CUSTOMER', solution='REFUND', reason='暂时不需要',
    ))
    with pytest.raises(DomainError) as failure:
        review_case(db, admin, case, ReviewInput(
            expectedVersion=case.version, decision='APPROVE', reason='核对打印状态',
        ))
    assert failure.value.code == 'PRINT_ATTEMPT_UNRESOLVED'
    with pytest.raises(DomainError) as invalid_attempt:
        start_production(order.id, ActualStartInput(
            expectedVersion=order.version, attemptId='unknown',
        ), db=db, actor=admin)
    assert invalid_attempt.value.code == 'OPEN_DOCUMENT_ATTEMPT_REQUIRED'
    with pytest.raises(DomainError) as locked:
        create_print_attempt(
            task.id, PrintAttemptInput(expectedVersion=order.version, artifactId=task.artifact_id),
            db=db, actor=admin,
        )
    assert locked.value.code == 'REFUND_LOCKED'
    start_production(order.id, ActualStartInput(
        expectedVersion=order.version, attemptId=attempt['data']['attemptId'],
    ), db=db, actor=admin)
    assert order.actual_started_at is not None
    with pytest.raises(DomainError) as duplicate:
        start_production(order.id, ActualStartInput(
            expectedVersion=order.version, attemptId=attempt['data']['attemptId'],
        ), db=db, actor=admin)
    assert duplicate.value.code == 'ACTUAL_START_ALREADY_RECORDED'
    with pytest.raises(DomainError) as after_start:
        review_case(db, admin, case, ReviewInput(
            expectedVersion=case.version, decision='APPROVE', reason='核对开印记录',
        ))
    assert after_start.value.code == 'PRODUCTION_ALREADY_STARTED'
    assert order.status == 'PENDING_PRINT'
    assert order.refunded_cents == 0
