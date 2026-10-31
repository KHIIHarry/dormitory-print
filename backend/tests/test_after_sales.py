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
