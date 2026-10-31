def test_health_uses_real_database(client):
    response = client.get('/api/v1/health/ready')
    assert response.status_code == 200
    assert response.json()['data']['database'] == 'ready'
    assert response.headers['cache-control'] == 'no-store'


def test_error_envelope_and_csrf(client):
    response = client.post('/api/v1/core/probe', json={})
    assert response.status_code in (403, 404)
    assert 'error' in response.json()
    assert response.json()['requestId']


def test_core_app_exists():
    from app.main import create_app
    assert callable(create_app)


def test_cross_origin_write_rejected(client):
    response=client.post('/api/v1/customer/session',headers={'Origin':'https://foreign.example','Idempotency-Key':'safe-key'},json={})
    assert response.status_code==403
    assert response.json()['error']['code']=='ORIGIN_REJECTED'


def test_idempotency_transaction_replay_before_version(client,api):
    from fastapi import Request
    from app.config import get_config
    from app.core import lock,ok,touch
    from app.models import ConfigSection

    @client.app.post('/api/v1/core/probe')
    def probe(body:dict,request:Request):
        db=request.state.db
        row=get_config(db,'announcement')
        lock(db,ConfigSection,row.id,body['expectedVersion'])
        row.data={'announcement':body['announcement']}
        touch(row)
        return ok({'version':row.version,'announcement':row.data['announcement']})

    body={'expectedVersion':1,'announcement':'营业'}
    first=api(client,'/core/probe',body,key='same-operation')
    assert first.status_code==200,first.text
    replay=api(client,'/core/probe',body,key='same-operation')
    assert replay.status_code==200,replay.text
    assert replay.json()['data']==first.json()['data']
    conflict=api(client,'/core/probe',{**body,'announcement':'暂停'},key='same-operation')
    assert conflict.status_code==409
    assert conflict.json()['error']['code']=='IDEMPOTENCY_CONFLICT'
    stale=api(client,'/core/probe',body,key='other-operation')
    assert stale.status_code==409
    assert stale.json()['error']['code']=='VERSION_CONFLICT'


def test_public_config_does_not_leak_private_settings(client):
    response=client.get('/api/v1/public/config')
    assert response.status_code==200,response.text
    data=response.json()['data']
    assert data['prices']['bwSingle'] is None
    assert data['bindingApproved'] is True
    assert not data['serviceEnabled']
    assert not any(key in data for key in ('mail','storageRoot','secretKey','databaseUrl'))


def test_authenticated_operation_payload_crypto():
    from app.core import seal,unseal
    encrypted=seal({'accessToken':'sensitive-value'})
    assert 'sensitive-value' not in encrypted
    assert unseal(encrypted)=={'accessToken':'sensitive-value'}



def test_nested_operation_reference_never_contains_private_payload():
    from app.core import _result_reference
    result=_result_reference({'data':{'order':{'id':'order-1','status':'PENDING_CONFIRMATION','phone':'private'},'accessToken':'secret','accessLink':'https://private/#token'}})
    assert result=={'orderId':'order-1','status':'PENDING_CONFIRMATION'}


def test_staff_operation_lookup_uses_original_route_identity(admin_client,api):
    from fastapi import Request
    from app.core import ok
    @admin_client.app.post('/api/v1/admin/core/probe')
    def probe(request:Request):
        return ok({'id':'receipt-1','status':'UNMATCHED','privateNote':'hidden'})
    first=api(admin_client,'/admin/core/probe',{},key='staff-operation')
    assert first.status_code==200,first.text
    lookup=admin_client.get('/api/v1/operations/staff-operation',params={'path':'POST /api/v1/admin/core/probe'})
    assert lookup.status_code==200,lookup.text
    assert lookup.json()['data']['result']=={'id':'receipt-1','status':'UNMATCHED'}


def test_operation_lookup_cannot_read_other_staff_operation(admin_client,client,api):
    from fastapi import Request
    from app.core import ok
    @admin_client.app.post('/api/v1/admin/core/probe')
    def probe(request:Request):
        return ok({'id':'receipt-1'})
    assert api(admin_client,'/admin/core/probe',{},key='private-operation').status_code==200
    denied=client.get('/api/v1/operations/private-operation',params={'path':'POST /api/v1/admin/core/probe'})
    assert denied.status_code==404


def test_account_default_invite_code_fits_persistent_column(account_factory):
    account=account_factory()
    assert 16 <= len(account.invite_code) <= 32


def test_idempotency_scope_changes_when_staff_authority_is_revoked():
    from app.core import Actor,actor_key
    class Request:
        url=type('URL',(),{'path':'/api/v1/admin/orders'})()
    privileged=Actor(staff_id='s',staff_role='ADMIN',staff_session_id='session',capabilities={'payment.manage'})
    revoked=Actor(staff_id='s',staff_role='ADMIN',staff_session_id='session',capabilities={'mail.manage'})
    assert actor_key(privileged,Request()) != actor_key(revoked,Request())


def test_delivery_location_limits_publish_actual_config_for_client_guard(db,client):
    from app.config import get_config
    row=get_config(db,'workflow')
    row.data={**row.data,'locationMaxAgeSeconds':180,'locationMaxAccuracyMeters':75}
    db.commit()
    data=client.get('/api/v1/public/config').json()['data']
    assert data['locationMaxAgeSeconds']==180
    assert data['locationMaxAccuracyMeters']==75
def test_row_lock_checks_current_database_version_after_prior_read(db):
    import pytest
    from app.core import DomainError,lock,touch
    from app.models import ConfigSection
    from app.db import SessionLocal
    row=ConfigSection(section='lock-freshness',data={'value':'before'})
    db.add(row);db.commit()
    stale_version=row.version
    with SessionLocal.begin() as other:
        current=other.get(ConfigSection,row.id)
        current.data={'value':'after'};touch(current)
    with pytest.raises(DomainError) as error:
        lock(db,ConfigSection,row.id,stale_version)
    assert error.value.code=='VERSION_CONFLICT'


def test_reentrant_row_lock_preserves_pending_mutation(db):
    from app.core import lock
    from app.models import ConfigSection
    row=ConfigSection(section='lock-reentrant',data={'value':'before'})
    db.add(row);db.commit()
    current=lock(db,ConfigSection,row.id)
    current.data={'value':'pending'}
    assert lock(db,ConfigSection,row.id).data=={'value':'pending'}
