import importlib
import importlib.util
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from functools import partial
import json
import logging
import secrets
from urllib.parse import unquote

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException

from . import db as database
from .core import (Actor, DomainError, _result_reference, actor_key, canonical_hash, csrf_value,
                   lookup_idempotency, ok, request_id_var, save_idempotency, unseal, valid_csrf)
from .db import SessionLocal
from .settings import get_settings

logger=logging.getLogger('dormprint')

# Advisory-lock waits must never block the ASGI event loop. Keep transaction
# completion on a separate bounded pool so queued duplicate requests cannot
# consume every worker needed by the original request to commit and unlock.
_security_pool=ThreadPoolExecutor(max_workers=8,thread_name_prefix='dormprint-security')
_completion_pool=ThreadPoolExecutor(max_workers=4,thread_name_prefix='dormprint-transaction')


async def _database_call(pool,function,*args):
    context=copy_context()
    future=asyncio.get_running_loop().run_in_executor(pool,partial(context.run,function,*args))
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        # Cancelling the await does not stop a running DB thread. Wait for it
        # before middleware's finally block closes the same Session.
        try:await future
        except Exception:pass
        raise


def error_response(code,message,status,request_id,details=None,field_errors=None):
    error={'code':code,'message':message}
    if details is not None:
        error['details']=details
    if field_errors:
        error['fieldErrors']=field_errors
    return JSONResponse({'error':error,'requestId':request_id},status_code=status)


def create_app(modules=None):
    settings=get_settings()
    if database.engine is None:
        database.configure(settings.database_url)
    app=FastAPI(title='DormPrint V1',version='1.0.0',openapi_url='/api/v1/openapi.json',docs_url=None,redoc_url=None)
    app.state.settings=settings

    @app.exception_handler(DomainError)
    async def domain_error(request:Request,error:DomainError):
        request.state.commit_error=error.commit
        return error_response(error.code,error.message,error.status,request.state.request_id,error.details,error.field_errors)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request,error):
        fields={'.'.join(str(v) for v in item['loc'] if v!='body'):item['msg'] for item in error.errors()}
        return error_response('VALIDATION_ERROR','输入内容不符合要求',422,request.state.request_id,field_errors=fields)

    @app.exception_handler(ValidationError)
    async def model_error(request,error):
        fields={'.'.join(map(str,item['loc'])):item['msg'] for item in error.errors()}
        return error_response('VALIDATION_ERROR','输入内容不符合要求',422,request.state.request_id,field_errors=fields)

    @app.exception_handler(HTTPException)
    async def http_error(request,error):
        return error_response('NOT_FOUND' if error.status_code==404 else 'REQUEST_REJECTED','接口不存在' if error.status_code==404 else '请求无法处理',error.status_code,request.state.request_id)

    @app.middleware('http')
    async def transaction_security(request:Request,call_next):
        request_id=secrets.token_hex(12)
        request.state.request_id=request_id
        request.state.commit_error=False
        context_token=request_id_var.set(request_id)
        session=SessionLocal()
        request.state.db=session
        response=None
        try:
            is_write=request.method not in ('GET','HEAD','OPTIONS')
            if is_write:
                origin=request.headers.get('origin')
                expected_origins={settings.public_origin}
                if settings.environment in ('development','test'):
                    expected_origins.update(('http://testserver','http://localhost:5173','http://localhost:8000','http://127.0.0.1:5173','http://127.0.0.1:8000'))
                if origin and origin not in expected_origins:
                    raise DomainError('ORIGIN_REJECTED','请求来源不受信任',403)
                bootstrap=request.url.path=='/api/v1/customer/session'
                cookie=unquote(request.cookies.get('csrfToken',''))
                header=request.headers.get('x-csrf-token','')
                if not bootstrap and (not valid_csrf(cookie) or not secrets.compare_digest(cookie,header)):
                    raise DomainError('CSRF_REQUIRED','请求校验已失效，请刷新后重试',403)
                size=request.headers.get('content-length')
                if size:
                    try:declared_size=int(size)
                    except ValueError:raise DomainError('INVALID_CONTENT_LENGTH','请求长度格式无效',400)
                    if declared_size<0:raise DomainError('INVALID_CONTENT_LENGTH','请求长度格式无效',400)
                    if declared_size>settings.max_request_bytes:
                        raise DomainError('UPLOAD_TOO_LARGE','请求文件超过允许大小',413)
                if not request.headers.get('idempotency-key'):
                    raise DomainError('IDEMPOTENCY_KEY_REQUIRED','缺少操作标识，请刷新重试',422)
            cached=None
            scope=route=key=digest=None
            # Multipart and raw chunks use semantic hashes in their module handlers.
            if is_write and 'multipart/form-data' not in request.headers.get('content-type','') and 'application/octet-stream' not in request.headers.get('content-type',''):
                raw=await request.body()
                if len(raw)>1024*1024:
                    raise DomainError('UPLOAD_TOO_LARGE','请求超过允许大小',413)
                try:
                    payload=json.loads(raw) if raw else {}
                except ValueError:
                    raise DomainError('INVALID_JSON','请求JSON格式无效',422)
                actor=Actor()
                if importlib.util.find_spec('app.auth'):
                    from .auth import resolve_actor
                    actor=await _database_call(_security_pool,resolve_actor,session,request)
                scope=actor_key(actor,request,payload)
                route=request.method+' '+request.url.path
                key=request.headers['idempotency-key']
                digest=canonical_hash({'body':payload,'query':sorted(request.query_params.multi_items())})
                deadline=asyncio.get_running_loop().time()+5
                while True:
                    try:
                        cached=await _database_call(_security_pool,lookup_idempotency,session,scope,route,key,digest,False)
                        break
                    except DomainError as error:
                        if error.code!='IDEMPOTENCY_IN_PROGRESS':raise
                        # Waiting duplicates release their DB connection and
                        # thread between attempts, allowing unrelated writes to
                        # proceed even when the original operation is slow.
                        await _database_call(_completion_pool,session.rollback)
                        if asyncio.get_running_loop().time()>=deadline:raise
                        await asyncio.sleep(.025)
                        actor=await _database_call(_security_pool,resolve_actor,session,request)
                        scope=actor_key(actor,request,payload)
            if cached:
                data=unseal(cached.encrypted_response)
                response=JSONResponse(data['body'],status_code=cached.status_code)
                for name,value in data.get('headers',[]):
                    if name.lower()=='set-cookie':
                        response.headers.append(name,value)
            else:
                response=await call_next(request)
                if (scope is not None and (response.status_code<400 or request.state.commit_error)
                    and 'application/json' in response.headers.get('content-type','')):
                    parts=[]
                    async for part in response.body_iterator:
                        parts.append(part.encode() if isinstance(part,str) else part)
                    raw=b''.join(parts)
                    data=json.loads(raw)
                    headers=[(name.decode(),value.decode()) for name,value in response.raw_headers if name.lower()==b'set-cookie']
                    await _database_call(_completion_pool,save_idempotency,session,scope,route,key,digest,response.status_code,{'body':data,'headers':headers},_result_reference(data))
                    original_headers=list(response.raw_headers)
                    response=JSONResponse(data,status_code=response.status_code)
                    response.raw_headers=original_headers
            if response.status_code<400 or request.state.commit_error:
                await _database_call(_completion_pool,session.commit)
            else:
                await _database_call(_completion_pool,session.rollback)
        except DomainError as error:
            await _database_call(_completion_pool,session.rollback)
            response=error_response(error.code,error.message,error.status,request_id,error.details,error.field_errors)
        except IntegrityError:
            await _database_call(_completion_pool,session.rollback)
            response=error_response('RESOURCE_CONFLICT','数据发生并发冲突，请刷新后核实',409,request_id)
        except Exception as error:
            await _database_call(_completion_pool,session.rollback)
            logger.error('request_failed class=%s request_id=%s',type(error).__name__,request_id)
            if settings.environment=='test':
                raise
            response=error_response('INTERNAL_ERROR','服务暂时无法完成操作，请核实结果后重试',500,request_id)
        finally:
            await _database_call(_completion_pool,session.close)
            request_id_var.reset(context_token)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['X-Request-ID']=request_id
        if not valid_csrf(unquote(request.cookies.get('csrfToken',''))):
            response.set_cookie('csrfToken',csrf_value(),secure=settings.cookie_secure,httponly=False,samesite='lax',path='/')
        return response

    @app.get('/api/v1/health/ready')
    def ready(request:Request):
        request.state.db.execute(text('SELECT 1'))
        return ok({'database':'ready'})

    @app.get('/api/v1/health/live')
    def live():
        return ok({'status':'live'})

    @app.get('/api/v1/operations/{key}')
    def operation_result(key:str,request:Request,path:str):
        from .auth import resolve_actor
        from .models import IdempotencyRecord
        from sqlalchemy import select
        actor=resolve_actor(request.state.db,request)
        method, separator, route_path = path.partition(' ')
        if (not separator or method not in ('POST','PUT','PATCH','DELETE')
                or not route_path.startswith('/api/v1/') or '?' in route_path
                or '#' in route_path or route_path.endswith(('/login','/register'))):
            raise DomainError('NOT_FOUND','未找到当前身份的操作结果',404)
        scope=actor_key(actor,request,route_path=route_path)
        record=request.state.db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.actor_key==scope,
            IdempotencyRecord.route==path,IdempotencyRecord.key==key))
        if not record:
            raise DomainError('NOT_FOUND','未找到当前身份的操作结果',404)
        return ok({'status':'COMPLETED','statusCode':record.status_code,'result':record.result_ref})

    names=modules if modules is not None else ['config','auth','growth','mail','media','commerce','production','delivery','after_sales','exports','maintenance','benefits','payroll','supplementary']
    for name in names:
        if importlib.util.find_spec('app.'+name):
            module=importlib.import_module('app.'+name)
            app.include_router(module.router,prefix='/api/v1')
        elif settings.environment=='production':
            raise RuntimeError('Required backend module missing: '+name)
    # Concrete /config/mail and /config/volume-discounts must precede /config/{section}.
    # Otherwise the generic configuration route shadows module-specific DTO handlers.
    app.router.routes.sort(key=lambda route: ('{' in getattr(route,'path',''),))
    return app
