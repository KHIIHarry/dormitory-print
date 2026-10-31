from datetime import timedelta
import pytest

from app.models import (Artifact,Asset,ConfigSection,DeliverySlot,Location,Preparation,Principal,Quote,utcnow)
from app.core import Actor,DomainError


@pytest.fixture
def ready_preparation(db):
    from app.config import get_config
    principal=Principal()
    db.add(principal);db.flush()
    for section in ('services','announcement'):
        config=get_config(db,section)
        config.data={**config.data,'serviceEnabled':True}
    workflow=get_config(db,'workflow')
    workflow.data={**workflow.data,'unpaidTimeoutMinutes':60}
    prices=get_config(db,'prices')
    prices.data={**prices.data,'bwSingle':10}
    asset=Asset(principal_id=principal.id,purpose='PRINT_SOURCE',name='test.pdf',mime='application/pdf',size=100,
                sha256='a'*64,storage_key='fixtures/source.pdf',status='READY',pages=1)
    db.add(asset);db.flush()
    prep=Preparation(principal_id=principal.id,status='READY',expires_at=utcnow()+timedelta(hours=24),
        input={'files':[{'clientFileId':'file-one','assetId':asset.id,'kind':'DOCUMENT','segments':[{'clientSegmentId':'segment-one','ranges':[{'from':1,'to':1}],'colorMode':'BW','sides':'SINGLE','flip':'NONE','copies':1}],'excludedPages':[],'bindingType':'NONE'}]},data={})
    db.add(prep);db.flush()
    final=Asset(principal_id=principal.id,purpose='FINAL_PDF',name='final.pdf',mime='application/pdf',size=100,
                sha256='b'*64,storage_key='fixtures/final.pdf',status='READY',pages=1)
    db.add(final);db.flush()
    artifact=Artifact(preparation_id=prep.id,asset_id=final.id,client_file_id='file-one',client_segment_id='segment-one',
        sha256='b'*64,pages=1,sheets=1,settings={'range':'1','copies':1,'colorMode':'BW','sides':'SINGLE','flip':'NONE','blankBacks':0},page_map=[{'sourcePage':1,'copyIndex':1,'sheetIndex':1,'side':'FRONT'}])
    db.add(artifact);db.flush()
    quote=Quote(preparation_id=prep.id,price_version=prices.version,expires_at=utcnow()+timedelta(minutes=15),
                subtotal_cents=10,total_cents=10,discount_cents=0,data={'id':'seed','priceVersion':prices.version,'lines':[{'key':'a4-one','relatedId':artifact.id,'unit':'SHEET','quantity':1,'unitPriceCents':10,'amountCents':10,'service':'A4'}],'discounts':[],'discountAllocations':[],'selectionMode':'AUTO','selectedPromotionType':'NONE','deliveryFeeCents':0,'volumeDiscountCandidate':None,'selectedEligibleSubtotalCents':0,'checkoutAccountId':None})
    db.add(quote);db.flush()
    prep.quote_id=quote.id
    location=Location(name='一号楼',x=.5,y=.5)
    slot=DeliverySlot(date='2030-01-01',name='晚间',starts_at=utcnow()+timedelta(days=1),ends_at=utcnow()+timedelta(days=1,hours=1),capacity=1)
    db.add_all([location,slot]);db.flush()
    body={'preparationId':prep.id,'quoteId':quote.id,'previewAcks':[{'artifactId':artifact.id,'sha256':artifact.sha256}],
          'contact':{'wechatNickname':'测试用户','phone':'13800000000'},'locationId':location.id,'slotId':slot.id,'customerNote':'备注'}
    db.commit()
    return Actor(principal_id=principal.id,guest_principal_id=principal.id),prep,artifact,slot,body


def test_submit_requires_exact_artifact_hash(db,ready_preparation):
    from app.commerce import submit_order,OrderSubmit
    actor,prep,artifact,slot,body=ready_preparation
    for acknowledgements in ([],[{'artifactId':artifact.id,'sha256':'c'*64}],body['previewAcks']*2):
        with pytest.raises(DomainError) as error:
            submit_order(db,actor,OrderSubmit.model_validate({**body,'previewAcks':acknowledgements}))
        assert error.value.code=='PREVIEW_REQUIRED'


def test_submitted_order_does_not_reserve_capacity(db,ready_preparation):
    from app.commerce import submit_order,OrderSubmit
    actor,prep,artifact,slot,body=ready_preparation
    order=submit_order(db,actor,OrderSubmit.model_validate(body))
    db.flush()
    assert order.total_cents==10
    assert order.status=='PENDING_CONFIRMATION'
    assert order.confirmed_slot_id is None
    assert db.get(DeliverySlot,slot.id).occupied==0


def test_customer_note_over_thirty_characters_rejected():
    from app.commerce import OrderSubmit
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        OrderSubmit.model_validate({'preparationId':'p','quoteId':'q','previewAcks':[],
            'contact':{'wechatNickname':'测试','phone':'13800000000'},'locationId':'l','slotId':'s','customerNote':'字'*31})


def test_contact_rejects_blank_nickname():
    from pydantic import ValidationError
    from app.commerce import Contact
    with pytest.raises(ValidationError):
        Contact(wechatNickname='   ',phone='13800000000')


APP_MODULES=['auth','commerce','growth','mail','media','production','after_sales','supplementary','payroll','benefits']


def test_unmatched_receipt_lists_remaining_cash_after_partial_refund(db,ready_preparation,admin_client,api):
    from app.commerce import submit_order,OrderSubmit
    actor,prep,artifact,slot,body=ready_preparation
    order=submit_order(db,actor,OrderSubmit.model_validate(body))
    db.commit()
    response=api(admin_client,f'/admin/orders/{order.id}/receipts',{'amountCents':100,'reason':'到期后收到现金'})
    assert response.status_code==200,response.text
    receipt=response.json()['data']
    refund=api(admin_client,f'/admin/receipts/{receipt["id"]}/refund',{'expectedVersion':receipt['version'],'amountCents':40,'reason':'先退回部分现金'})
    assert refund.status_code==200,refund.text
    items=admin_client.get('/api/v1/admin/receipts?status=UNMATCHED').json()['data']['items']
    current=next(row for row in items if row['id']==receipt['id'])
    assert current['refundedCents']==40
    assert current['refundableCents']==60
    summary=admin_client.get('/api/v1/admin/dashboard').json()['data']
    assert summary['unmatchedReceiptCents']==100
    assert summary['unmatchedReceiptRefundCents']==40
    assert summary['cashReceivedCents']==0
    final=api(admin_client,f'/admin/receipts/{receipt["id"]}/refund',{'expectedVersion':current['version'],'amountCents':60,'reason':'退清剩余现金'})
    assert final.status_code==200,final.text
    assert final.json()['data']['status']=='REFUNDED'


def test_dashboard_keeps_supplementary_cash_separate_from_original(db,ready_preparation,admin_client):
    from app.commerce import submit_order,OrderSubmit
    from app.models import SupplementaryCharge
    actor,prep,artifact,slot,body=ready_preparation
    order=submit_order(db,actor,OrderSubmit.model_validate(body))
    order.cash_received_cents=10;order.settlement_kind='CASH_MANUAL';order.status='DELIVERED'
    db.add(SupplementaryCharge(order_id=order.id,purpose='排版服务',amount_cents=90,include_in_payroll=True,cash_received_cents=90,refunded_cents=20,created_by=admin_client.staff.id))
    db.commit()
    response=admin_client.get('/api/v1/admin/dashboard')
    assert response.status_code==200,response.text
    summary=response.json()['data']
    assert summary['cashReceivedCents']==10
    assert summary['payableCents']==10
    assert summary['supplementaryFeesCents']==90
    assert summary['supplementaryCashReceivedCents']==90
    assert summary['supplementaryRefundCents']==20
    assert summary['supplementaryOutstandingCents']==0
