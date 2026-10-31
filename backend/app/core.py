from __future__ import annotations

import base64
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import timedelta
import hashlib
import hmac
import json
import secrets
from typing import Generic, TypeVar

from cryptography.fernet import Fernet
from fastapi import Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text
from sqlalchemy.orm.attributes import flag_modified

from .db import get_db
from .models import AccessGrant, AuditLog, IdempotencyRecord, Job, Principal, utcnow
from .settings import get_settings

request_id_var = ContextVar('request_id', default='')
T = TypeVar('T')


def camel(value):
    parts = value.split('_')
    return parts[0] + ''.join(part.title() for part in parts[1:])


class DTO(BaseModel):
    model_config = ConfigDict(alias_generator=camel, populate_by_name=True, extra='forbid')


class Envelope(DTO, Generic[T]):
    data: T
    request_id: str


class DomainError(Exception):
    def __init__(self, code, message, status=422, details=None, field_errors=None, commit=False):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
        self.details, self.field_errors, self.commit = details, field_errors, commit


@dataclass
class Actor:
    principal_id: str | None = None
    guest_principal_id: str | None = None
    account_id: str | None = None
    staff_id: str | None = None
    staff_role: str | None = None
    account_session_id: str | None = None
    staff_session_id: str | None = None
    capabilities: set[str] = field(default_factory=set)
    grant_ids: list[str] = field(default_factory=list)
    restricted: bool = False

    @property
    def id(self):
        return self.staff_id or self.principal_id or 'anonymous'

    @property
    def role(self):
        return self.staff_role or 'CUSTOMER'


def actor_dep(request: Request, db=Depends(get_db)) -> Actor:
    from .auth import resolve_actor
    actor=resolve_actor(db, request)
    path=request.url.path.removeprefix('/api/v1')
    if path.startswith(('/customer/','/orders','/preparations','/after-sales/')):
        # Coexisting staff cookies never elevate a request on the customer surface.
        actor=replace(actor,staff_id=None,staff_role=None,staff_session_id=None,capabilities=set(),restricted=False)
    return actor


def require_admin(actor, capability=None):
    if not actor.staff_id:
        raise DomainError('AUTH_REQUIRED', '请先登录员工账号', 401)
    if actor.staff_role != 'ADMIN' or actor.restricted:
        raise DomainError('FORBIDDEN', '无权执行此操作', 403)
    if capability and '*' not in actor.capabilities and capability not in actor.capabilities:
        raise DomainError('FORBIDDEN', '账号未获此项管理权限', 403)


def require_staff(actor):
    if not actor.staff_id:
        raise DomainError('AUTH_REQUIRED', '请先登录员工账号', 401)
    if actor.staff_role not in ('ADMIN', 'COURIER') or actor.restricted:
        raise DomainError('PASSWORD_CHANGE_REQUIRED', '请先完成账号安全设置', 403)


def require_customer(actor, verified=False):
    if not actor.principal_id:
        raise DomainError('AUTH_REQUIRED', '请先建立客户会话', 401)
    if verified and not actor.account_id:
        raise DomainError('ACCOUNT_VERIFICATION_REQUIRED', '请登录已验证账号', 403)


def owns_principal(db, actor, principal_id):
    if not principal_id:
        return False
    if principal_id in (actor.principal_id, actor.guest_principal_id):
        return True
    return bool(actor.account_id and db.scalar(select(Principal.id).where(Principal.id == principal_id, Principal.account_id == actor.account_id)))


def grant_scopes(db, actor, order_id):
    result = set()
    candidates = list(db.scalars(select(AccessGrant).where(AccessGrant.order_id == order_id)))
    by_id = {grant.id: grant for grant in candidates}
    for grant in candidates:
        if not ((actor.account_id and grant.account_id == actor.account_id) or grant.id in actor.grant_ids):
            continue
        chain, seen, scopes = grant, set(), set(grant.scopes)
        valid = True
        while chain:
            if chain.id in seen or chain.revoked_at or chain.expires_at <= utcnow():
                valid = False
                break
            seen.add(chain.id)
            scopes.intersection_update(chain.scopes)
            if not chain.source_grant_id:
                break
            chain = by_id.get(chain.source_grant_id)
            if chain is None:
                valid = False
                break
        if valid:
            result.update(scopes)
    return result


def authorize_order(db, actor, order, scope='ORDER_VIEW'):
    if not order or order.deleted_at:
        raise DomainError('NOT_FOUND', '订单不存在或不可访问', 404)
    if actor.staff_role == 'ADMIN' and not actor.restricted:
        return
    if owns_principal(db, actor, order.principal_id):
        return
    if scope in grant_scopes(db, actor, order.id):
        return
    raise DomainError('NOT_FOUND', '订单不存在或不可访问', 404)


def lock(db, model, object_id, expected_version=None):
    existing = db.identity_map.get(db.identity_key(model, (object_id,)))
    # An earlier unlocked read must not mask a concurrent committed change.
    # Reentrant locks within this transaction retain deliberate unflushed work.
    refresh = existing is None or existing not in db.dirty
    row = db.scalar(select(model).where(model.id == object_id).with_for_update()
                    .execution_options(populate_existing=refresh))
    if row is None:
        raise DomainError('NOT_FOUND', '对象不存在或不可访问', 404)
    if expected_version is not None and row.version != expected_version:
        raise DomainError('VERSION_CONFLICT', '数据已变化，请刷新后重试', 409, {'currentVersion': row.version})
    return row


def touch(row):
    row.version = (row.version or 0) + 1
    row.updated_at = utcnow()
    for name in ('data', 'input', 'settings', 'snapshot', 'preferences', 'scopes', 'capabilities', 'page_map', 'totals'):
        if name in row.__mapper__.columns and name in row.__dict__ and row.__dict__[name] is not None:
            flag_modified(row, name)


def ok(data):
    if isinstance(data, BaseModel):
        data = data.model_dump(by_alias=True, mode='json')
    return {'data': data, 'requestId': request_id_var.get() or secrets.token_hex(12)}


def audit(db, actor, action, target_type, target_id, reason='', data=None):
    record = AuditLog(actor_id=actor.id if isinstance(actor, Actor) else str(actor), action=action,
                      target_type=target_type, target_id=str(target_id), reason=reason,
                      request_id=request_id_var.get(), data=data or {})
    db.add(record)
    return record


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _fernet():
    key = hashlib.sha256(get_settings().master_secret().encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def seal(value):
    return _fernet().encrypt(json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=str).encode()).decode()


def unseal(value):
    return json.loads(_fernet().decrypt(value.encode()))


def canonical_hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str).encode()).hexdigest()


def advisory_lock(db, value, wait=True):
    if db.bind.dialect.name == 'postgresql':
        namespace=db.info.get('lock_namespace')
        if namespace is None:
            namespace=db.scalar(text('SELECT current_schema()'))
            db.info['lock_namespace']=namespace
        digest = int.from_bytes(hashlib.sha256((namespace+':'+value).encode()).digest()[:8], 'big', signed=True)
        function='pg_advisory_xact_lock' if wait else 'pg_try_advisory_xact_lock'
        result=db.scalar(text(f'SELECT {function}(:key)'), {'key': digest})
        return True if wait else bool(result)
    return True


def actor_key(actor, request=None, body=None, route_path=None):
    path = route_path if route_path is not None else (request.url.path if request else '')
    if path.endswith('/login') or path.endswith('/register'):
        identifier = (body or {}).get('email') or (body or {}).get('username') or ''
        if not isinstance(identifier,str):
            identifier=''
        return 'credential:' + token_hash(path + ':' + identifier.strip().lower())
    if '/admin/' in path or path.startswith('/api/v1/auth/') or '/courier/' in path:
        authority=canonical_hash({'role':actor.staff_role,'capabilities':sorted(actor.capabilities),'restricted':actor.restricted})
        return 'staff:' + (actor.staff_id or 'anonymous') + ':' + (actor.staff_session_id or '') + ':' + authority
    if actor.account_id:
        return 'customer:' + actor.account_id + ':' + (actor.account_session_id or '')
    if actor.principal_id:
        return 'guest:' + actor.principal_id
    if actor.grant_ids:
        return 'grant:' + canonical_hash(sorted(actor.grant_ids))
    return 'anonymous:' + token_hash(request.cookies.get('csrfToken', '') if request else '')


def lookup_idempotency(db, scope, route, key, payload_hash, wait=True):
    if not key or len(key) > 160 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise DomainError('IDEMPOTENCY_KEY_REQUIRED', '请提供合法的操作幂等标识', 422)
    if not advisory_lock(db, scope + route + key,wait=wait):
        raise DomainError('IDEMPOTENCY_IN_PROGRESS','相同操作仍在处理中，请稍后核实结果',503)
    record = db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.actor_key == scope,
                       IdempotencyRecord.route == route, IdempotencyRecord.key == key))
    if record:
        if record.request_hash != payload_hash:
            raise DomainError('IDEMPOTENCY_CONFLICT', '同一操作标识不能用于不同请求', 409)
        if record.expires_at <= utcnow() or not record.encrypted_response:
            raise DomainError('IDEMPOTENCY_EXPIRED', '该操作重放窗口已结束，请先核实结果', 409, record.result_ref)
    return record


def save_idempotency(db, scope, route, key, payload_hash, status_code, response_data, result_ref=None):
    record = IdempotencyRecord(actor_key=scope, route=route, key=key, request_hash=payload_hash,
        status_code=status_code, encrypted_response=seal(response_data), result_ref=result_ref or {},
        expires_at=utcnow() + timedelta(hours=get_settings().idempotency_hours))
    db.add(record)
    return record


def idempotent_call(db, actor, request, key, payload, function):
    scope, route = actor_key(actor, request, payload), request.method + ' ' + request.url.path
    digest = canonical_hash(payload)
    existing = lookup_idempotency(db, scope, route, key, digest)
    if existing:
        value = unseal(existing.encrypted_response)
        return value.get('body', value)
    result = function()
    if not isinstance(result, dict) or 'data' not in result:
        result = ok(result)
    save_idempotency(db, scope, route, key, digest, 200, {'body': result}, result_ref=_result_reference(result))
    return result


def _result_reference(body):
    data = body.get('data') if isinstance(body, dict) else None
    if not isinstance(data, dict):
        return {}
    # Operation lookup never returns a credential-bearing cached response.
    reference = {key: data[key] for key in ('id', 'orderId', 'assetId', 'preparationId', 'status', 'exportJobId') if key in data and isinstance(data[key], (str, int))}
    order = data.get('order')
    if isinstance(order, dict) and isinstance(order.get('id'), str):
        reference['orderId'] = order['id']
        if isinstance(order.get('status'), str):
            reference['status'] = order['status']
    return reference


def enqueue_job(db, kind, target_id, business_key, data=None):
    advisory_lock(db, 'job:' + business_key)
    job = db.scalar(select(Job).where(Job.business_key == business_key))
    if job:
        return job
    job = Job(kind=kind, target_id=target_id, business_key=business_key, data=data or {})
    db.add(job)
    db.flush()
    return job


def csrf_value():
    nonce = secrets.token_urlsafe(32)
    signature = hmac.new(get_settings().master_secret().encode(), nonce.encode(), hashlib.sha256).hexdigest()
    return nonce + '.' + signature


def valid_csrf(value):
    try:
        nonce, signature = value.rsplit('.', 1)
        expected = hmac.new(get_settings().master_secret().encode(), nonce.encode(), hashlib.sha256).hexdigest()
        return len(nonce) >= 32 and hmac.compare_digest(signature, expected)
    except (ValueError, AttributeError):
        return False
