"""Independent customer, guest, employee and order-access identity module.

This module owns the public auth/customer/staff/access-link routes and the
exported helpers used by the rest of the backend. It never creates a second
account, order or authorization system: all facts live in the shared models and
every session/reservation change is committed by the request transaction.
"""
from __future__ import annotations

import importlib
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, or_, select

from .auth_schemas import (
    AccessLinkInput,
    AccessLinkIssued,
    AccessLinkList,
    AccessLinkView,
    ChangeEmailInput,
    ChangePasswordInput,
    ChangePendingEmailInput,
    ConfirmInput,
    CustomerAccountView,
    CustomerLoginInput,
    CustomerSessionView,
    DeletionRequestInput,
    DeletionRequestResult,
    EmailInput,
    ExchangeInput,
    ExportRequestInput,
    ExportRequestResult,
    GrantRevokeResult,
    GrantableAccountList,
    GrantableAccountView,
    ImportOrderInput,
    ImportOrderResult,
    LinkGuestResult,
    MailResult,
    NotificationInput,
    NotificationPreferences,
    OrderAccessExchange,
    OrderAccessView,
    PasswordChangedResult,
    ProfileInput,
    ReasonInput,
    RegisterInput,
    RegistrationResult,
    ResendVerificationInput,
    ResetPasswordInput,
    RevokeResult,
    SessionDeviceView,
    SessionList,
    SessionRevokeInput,
    StaffActionResult,
    StaffCreateInput,
    StaffList,
    StaffLoginInput,
    StaffSessionView,
    StaffUpdateInput,
    StaffView,
    TokenInput,
    _validate_password,
    mask_email,
    normalize_email,
)
from .core import (
    Actor,
    DomainError,
    Envelope,
    actor_dep,
    advisory_lock,
    audit,
    grant_scopes,
    lock,
    ok,
    require_admin,
    require_customer,
    require_staff,
    token_hash,
    touch,
)
from .db import get_db
from .models import (
    AccessGrant,
    Account,
    AfterSale,
    AuthSession,
    DeletionRequest,
    ExportJob,
    OneTimeToken,
    Order,
    Principal,
    Referral,
    Staff,
    utcnow,
)
from .settings import get_settings

router = APIRouter()

GUEST_COOKIE = os.environ.get('CUSTOMER_GUEST_COOKIE_NAME') or 'dormprint_guest_session'
ACCOUNT_COOKIE = os.environ.get('CUSTOMER_ACCOUNT_COOKIE_NAME') or 'dormprint_account_session'
ORDER_ACCESS_COOKIE = os.environ.get('ORDER_ACCESS_COOKIE_NAME') or 'dormprint_order_access_session'
STAFF_COOKIE = os.environ.get('STAFF_AUTH_COOKIE_NAME') or 'dormprint_staff_session'
PENDING_COOKIE = 'dormprint_pending_registration'
DEVICE_COOKIE = 'dormprint_device_id'

MAX_DEVICES = 2
CUSTOMER_SESSION_TTL = timedelta(days=30)
GUEST_SESSION_TTL = timedelta(days=30)
STAFF_SESSION_TTL = timedelta(days=7)
DELETION_PENDING = 'DELETION_PENDING'
ORDER_SESSION_TTL = timedelta(days=30)
SECURITY_TOKEN_TTL = timedelta(minutes=30)
TEMPORARY_PASSWORD_TTL = timedelta(hours=24)
ORDER_LINK_MAX_TTL = timedelta(days=30)
EXPORT_TTL = timedelta(hours=24)

LINK_SCOPES = (
    'ORDER_VIEW',
    'FINAL_PDF_PREVIEW',
    'DELIVERY_PROOF_VIEW',
    'ORDER_REVISE_PENDING',
    'ORDER_CANCEL_PENDING',
    'ORDER_CHANGE_LOCATION_PRE_DELIVERY',
    'ORDER_IMPORT',
)
READ_ONLY_SCOPES = ('ORDER_VIEW', 'FINAL_PDF_PREVIEW', 'DELIVERY_PROOF_VIEW')

TERMINAL_ORDER_STATUSES = {'COMPLETED', 'DELIVERED', 'CANCELLED', 'CANCELED', 'CLOSED', 'REFUNDED'}
TERMINAL_AFTER_SALE_STATUSES = {'COMPLETED', 'CLOSED', 'REJECTED', 'CANCELLED', 'CANCELED'}
LOCATION_CHANGE_BLOCKED_STATUSES = TERMINAL_ORDER_STATUSES | {'DELIVERING', 'OUT_FOR_DELIVERY'}

_hasher = PasswordHasher()
_DUMMY_HASH: str | None = None


def _now() -> datetime:
    return utcnow()


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError, ValueError):
        return False


def _dummy_verify(password: str) -> None:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_urlsafe(24))
    verify_password(_DUMMY_HASH, password)


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


def _set_cookie(response: Response, name: str, value: str, ttl: timedelta) -> None:
    response.set_cookie(name, value, max_age=int(ttl.total_seconds()), httponly=True,
                        secure=get_settings().cookie_secure, samesite='lax', path='/')


def _clear_cookie(response: Response, name: str) -> None:
    response.delete_cookie(name, path='/', secure=get_settings().cookie_secure, httponly=True,
                           samesite='lax')


def _device_id(request: Request, current: AuthSession | None = None) -> str:
    candidate = request.cookies.get(DEVICE_COOKIE) or (current.device_id if current else None)
    if candidate and re.fullmatch(r'[A-Za-z0-9_-]{16,64}', candidate):
        return candidate
    return secrets.token_urlsafe(24)


def _set_device(response: Response, request: Request, device_id: str) -> None:
    if request.cookies.get(DEVICE_COOKIE) != device_id:
        _set_cookie(response, DEVICE_COOKIE, device_id, timedelta(days=180))


def _load_session(db, request: Request, cookie_name: str, kind: str) -> AuthSession | None:
    raw = request.cookies.get(cookie_name)
    if not raw or len(raw) > 512:
        return None
    return db.scalar(select(AuthSession).where(
        AuthSession.kind == kind, AuthSession.token_hash == token_hash(raw)))


def _session_active(session: AuthSession | None, now: datetime | None = None) -> bool:
    now = now or _now()
    return bool(session and session.revoked_at is None and _aware(session.expires_at) > now)


def _active_session_count(db, kind: str, *, account_id: str | None = None,
                          staff_id: str | None = None) -> int:
    query = select(func.count()).select_from(AuthSession).where(
        AuthSession.kind == kind, AuthSession.revoked_at.is_(None),
        AuthSession.expires_at > _now())
    if account_id:
        query = query.where(AuthSession.account_id == account_id)
    if staff_id:
        query = query.where(AuthSession.staff_id == staff_id)
    return int(db.scalar(query) or 0)


def _issue_account_session(db, response: Response, request: Request, account: Account,
                           current: AuthSession | None = None) -> AuthSession:
    now = _now()
    device_id = _device_id(request, current)
    staff = _active_staff_for_account(db, account)
    ttl = STAFF_SESSION_TTL if staff is not None else CUSTOMER_SESSION_TTL
    candidate = None
    if current and current.kind == 'CUSTOMER' and current.account_id == account.id:
        candidate = current
    if candidate is None:
        candidate = db.scalar(select(AuthSession).where(
            AuthSession.kind == 'CUSTOMER', AuthSession.account_id == account.id,
            AuthSession.device_id == device_id, AuthSession.revoked_at.is_(None)
        ).order_by(AuthSession.created_at.desc()))
    raw = secrets.token_urlsafe(32)
    if (candidate is not None and candidate.revoked_at is None
            and _aware(candidate.expires_at) > now):
        candidate.token_hash = token_hash(raw)
        candidate.created_at = now
        candidate.expires_at = now + ttl
        candidate.last_seen_at = now
        candidate.principal_id = account.principal_id
        candidate.session_version = account.session_version
        session = candidate
    else:
        if _active_session_count(db, 'CUSTOMER', account_id=account.id) >= MAX_DEVICES:
            raise DomainError(
                'DEVICE_LIMIT_REACHED',
                '此账号最多同时登录 2 台设备，请先在已登录设备退出一个设备后重试',
                409,
            )
        session = AuthSession(kind='CUSTOMER', token_hash=token_hash(raw),
                              principal_id=account.principal_id, account_id=account.id,
                              device_id=device_id, device_label='浏览器',
                              session_version=account.session_version,
                              expires_at=now + ttl, last_seen_at=now)
        db.add(session)
    db.flush()
    _set_device(response, request, device_id)
    _set_cookie(response, ACCOUNT_COOKIE, raw, ttl)
    return session


def _issue_order_session(db, response: Response, request: Request, grant: AccessGrant) -> AuthSession:
    now = _now()
    raw = secrets.token_urlsafe(32)
    expiry = min(now + ORDER_SESSION_TTL, _aware(grant.expires_at))
    session = AuthSession(kind='ORDER_ACCESS', token_hash=token_hash(raw), grant_id=grant.id,
                          device_id=_device_id(request), device_label='订单链接',
                          expires_at=expiry, last_seen_at=now)
    db.add(session)
    db.flush()
    _set_cookie(response, ORDER_ACCESS_COOKIE, raw, max(expiry - now, timedelta(seconds=1)))
    return session


def _create_guest_session(db, response: Response, request: Request) -> tuple[Principal, AuthSession]:
    now = _now()
    principal = Principal()
    db.add(principal)
    db.flush()
    raw = secrets.token_urlsafe(32)
    session = AuthSession(kind='GUEST', token_hash=token_hash(raw), principal_id=principal.id,
                          device_id=_device_id(request), device_label='游客浏览器',
                          expires_at=now + GUEST_SESSION_TTL, last_seen_at=now)
    db.add(session)
    db.flush()
    _set_cookie(response, GUEST_COOKIE, raw, GUEST_SESSION_TTL)
    return principal, session


def _revoke_session(session: AuthSession, now: datetime | None = None) -> None:
    if session.revoked_at is None:
        session.revoked_at = now or _now()
        session.version = (session.version or 0) + 1


def _revoke_sessions(db, kind: str, *, account_id: str | None = None,
                     staff_id: str | None = None, except_id: str | None = None) -> int:
    query = select(AuthSession).where(AuthSession.kind == kind, AuthSession.revoked_at.is_(None))
    if account_id:
        query = query.where(AuthSession.account_id == account_id)
    if staff_id:
        query = query.where(AuthSession.staff_id == staff_id)
    count = 0
    for session in db.scalars(query):
        if except_id and session.id == except_id:
            continue
        _revoke_session(session)
        count += 1
    return count


def _revoke_sessions_for_grants(db, grant_ids: set[str]) -> int:
    if not grant_ids:
        return 0
    count = 0
    for session in db.scalars(select(AuthSession).where(
            AuthSession.grant_id.in_(grant_ids), AuthSession.revoked_at.is_(None))):
        _revoke_session(session)
        count += 1
    return count


# ---------------------------------------------------------------------------
# Actor resolution
# ---------------------------------------------------------------------------


def _valid_account_session(db, session: AuthSession | None) -> Account | None:
    if not _session_active(session) or not session.account_id:
        return None
    account = db.get(Account, session.account_id)
    if account is None or account.deleted_at is not None:
        return None
    if not account.verified_at or account.status != 'ACTIVE':
        return None
    if session.session_version != account.session_version:
        return None
    return account


def _valid_grant_session(db, session: AuthSession | None) -> AccessGrant | None:
    if not _session_active(session) or not session.grant_id:
        return None
    grant = db.get(AccessGrant, session.grant_id)
    if grant is None or grant.revoked_at is not None or _aware(grant.expires_at) <= _now():
        return None
    return grant


def _account_active(account: Account | None) -> bool:
    return bool(account is not None and account.deleted_at is None
                and account.verified_at and account.status == 'ACTIVE')


def _staff_active(staff: Staff | None) -> bool:
    # Legacy staff rows awaiting a password change belong to the retired separate
    # staff credential system; account-based grants always clear this flag.
    return bool(staff is not None and staff.enabled and staff.status == 'ACTIVE'
                and not staff.must_change_password)


def _linked_staff(db, account_id: str) -> Staff | None:
    return db.scalar(select(Staff).where(Staff.account_id == account_id))


def _active_staff_for_account(db, account: Account | None) -> Staff | None:
    """The staff grant that currently applies to a verified customer account.

    This is deliberately re-read from the database on every request: disabling
    or revoking the staff row takes effect immediately, not at session expiry.
    """
    if not _account_active(account):
        return None
    staff = _linked_staff(db, account.id)  # type: ignore[union-attr]
    return staff if _staff_active(staff) else None


def _elevated_staff(db, account: Account | None,
                    account_session: AuthSession | None) -> Staff | None:
    """Employee rights require a recent authentication of the same account.

    The account cookie may keep the ordinary customer session alive for 30 days,
    but ADMIN/COURIER authority is only derived within 7 days of a successful
    authentication in that session. There is no second staff password system.
    """
    staff = _active_staff_for_account(db, account)
    if staff is None or account_session is None:
        return None
    authenticated_at = _aware(account_session.created_at)
    if _now() - authenticated_at > STAFF_SESSION_TTL:
        return None
    return staff


def resolve_actor(db, request: Request) -> Actor:
    """Resolve browser identity only from the unified customer-account cookie.

    FINAL N05: employee authority is derived from the normal verified customer
    account cookie (role/capabilities are read fresh from the linked active Staff
    row). A legacy STAFF cookie is ignored entirely: it grants no identity, no
    device slot, and cannot elevate a logged-in account. Old staff sessions must
    reauthenticate through the same base customer account.
    """
    now = _now()
    actor = Actor()

    account_session = _load_session(db, request, ACCOUNT_COOKIE, 'CUSTOMER')
    account = _valid_account_session(db, account_session)
    # Order creation must serialize with self-service deletion.  The deletion
    # path locks the same account row; re-reading it after acquiring the lock
    # prevents a request that started before deletion from submitting an order
    # after the account entered its recovery window.
    if (account is not None and account_session is not None
            and request.scope.get('method') == 'POST'
            and request.url.path.rstrip('/') == '/api/v1/orders'):
        locked = db.scalar(select(Account).where(Account.id == account.id)
                           .with_for_update().execution_options(populate_existing=True))
        if (locked is None or not _account_active(locked)
                or locked.session_version != account_session.session_version):
            # Do not raise from the middleware-level resolve_actor call: the
            # route dependency will resolve the now-invalid cookie as anonymous
            # and return the normal AUTH_REQUIRED response.
            account = None
            account_session = None
        else:
            account = locked
    if account is not None and account_session is not None:
        actor.account_id = account.id
        actor.account_session_id = account_session.id
        actor.principal_id = account.principal_id
        account_session.last_seen_at = now

        staff = _elevated_staff(db, account, account_session)
        if staff is not None:
            actor.staff_id = staff.id
            actor.staff_role = staff.role
            # The unified effective identity is scoped by the customer account
            # session that authenticated it. This is what actor_key hashes for
            # idempotency, so a revoked grant cannot replay a staff request.
            actor.staff_session_id = account_session.id
            actor.capabilities = set(staff.capabilities or [])
            actor.restricted = False

    guest_session = _load_session(db, request, GUEST_COOKIE, 'GUEST')
    if _session_active(guest_session) and guest_session is not None and guest_session.principal_id:
        principal = db.get(Principal, guest_session.principal_id)
        if principal is not None and principal.account_id is None:
            actor.guest_principal_id = principal.id
            if actor.principal_id is None:
                actor.principal_id = principal.id
            guest_session.last_seen_at = now

    order_session = _load_session(db, request, ORDER_ACCESS_COOKIE, 'ORDER_ACCESS')
    grant = _valid_grant_session(db, order_session)
    if grant is not None:
        actor.grant_ids = [grant.id]
        order_session.last_seen_at = now

    return actor


# ---------------------------------------------------------------------------
# Small view builders
# ---------------------------------------------------------------------------


def _account_status(account: Account) -> str:
    if account.deleted_at is not None or account.status in ('SUSPENDED', 'DELETION_PENDING'):
        return 'SUSPENDED'
    if account.verified_at and account.status == 'ACTIVE':
        return 'ACTIVE'
    return 'PENDING_VERIFICATION'


def _preferences(account: Account) -> NotificationPreferences:
    values = account.preferences or {}
    return NotificationPreferences(
        order_created=bool(values.get('orderCreated', False)),
        order_status_updated=bool(values.get('orderStatusUpdated', False)),
        referral_reward=bool(values.get('referralReward', False)),
    )


def _account_view(account: Account) -> CustomerAccountView:
    return CustomerAccountView(
        id=account.id,
        email_masked=mask_email(account.email) or '',
        display_name=account.display_name or '',
        verified=bool(account.verified_at) and account.status == 'ACTIVE',
        status=_account_status(account),  # type: ignore[arg-type]
        version=account.version,
        notification_preferences=_preferences(account),
    )


def _customer_session(account: Account | None, *, recovered: bool = False,
                      recovery_until=None, message: str | None = None) -> CustomerSessionView:
    if account is None:
        return CustomerSessionView(auth_kind='ANONYMOUS', capabilities=[])
    return CustomerSessionView(
        auth_kind='REGISTERED',
        account=_account_view(account),
        capabilities=['customer.account', 'customer.orders', 'customer.coupons', 'customer.referrals'],
        recovered=recovered,
        recovery_until=recovery_until,
        message=message,
    )


def _staff_session_view(staff: Staff) -> StaffSessionView:
    return StaffSessionView(
        account_id=staff.account_id or '',
        is_super_admin=bool(staff.is_super_admin),
        discount_level=staff.discount_level,
        id=staff.id,
        role=staff.role,  # type: ignore[arg-type]
        name=staff.name,
        username=staff.username,
        email_masked=mask_email(staff.email),
        email_verified=bool(staff.email_verified),
        must_change_password=bool(staff.must_change_password),
        capabilities=list(staff.capabilities or []),
    )


def _staff_status(staff: Staff) -> str:
    if staff.status == 'DISABLED' or not staff.enabled:
        return 'DISABLED'
    if staff.status == 'PENDING_SETUP':
        return 'PENDING_SETUP'
    return 'ACTIVE'


def _staff_allowed_actions(actor: Actor, staff: Staff) -> list[str]:
    if actor.staff_role != 'ADMIN' or actor.restricted:
        return []
    capabilities = set(actor.capabilities or [])
    if '*' not in capabilities and 'staff.manage' not in capabilities:
        return []
    actions = ['edit', 'revoke-sessions']
    if staff.account_id:
        actions.insert(1, 'send-setup')
    return actions


def _staff_view(staff: Staff, last_login: datetime | None, actor: Actor) -> StaffView:
    return StaffView(
        id=staff.id,
        account_id=staff.account_id or '',
        is_super_admin=bool(staff.is_super_admin),
        discount_level=staff.discount_level,
        version=staff.version,
        name=staff.name,
        username=staff.username,
        phone=staff.phone or '',
        email_masked=mask_email(staff.email),
        email_verified=bool(staff.email_verified),
        role=staff.role,  # type: ignore[arg-type]
        enabled=bool(staff.enabled) and _staff_status(staff) != 'DISABLED',
        status=_staff_status(staff),  # type: ignore[arg-type]
        must_change_password=bool(staff.must_change_password),
        capabilities=list(staff.capabilities or []),
        last_login_at=last_login,
        allowed_actions=_staff_allowed_actions(actor, staff),
    )


def _staff_list(db, actor: Actor) -> StaffList:
    rows = list(db.scalars(select(Staff).order_by(Staff.created_at.asc())))
    # Historical standalone STAFF sessions are not an identity or a login source.
    account_ids = [row.account_id for row in rows if row.account_id]
    account_logins = dict(db.execute(
        select(AuthSession.account_id, func.max(AuthSession.created_at))
        .where(AuthSession.kind == 'CUSTOMER', AuthSession.account_id.in_(account_ids))
        .group_by(AuthSession.account_id)
    ).all()) if account_ids else {}
    views = [
        _staff_view(
            row,
            account_logins.get(row.account_id) if row.account_id else None,
            actor,
        )
        for row in rows
    ]
    return StaffList(items=views, staff=views)


def _session_device(session: AuthSession, current_id: str | None) -> SessionDeviceView:
    return SessionDeviceView(
        id=session.id,
        device=session.device_label or '浏览器',
        last_seen_at=session.last_seen_at,
        current=session.id == current_id,
        created_at=session.created_at,
        expires_at=session.expires_at,
    )


def _session_list(db, kind: str, account_id: str | None = None,
                  staff_id: str | None = None, current_id: str | None = None) -> SessionList:
    query = select(AuthSession).where(
        AuthSession.kind == kind, AuthSession.revoked_at.is_(None),
        AuthSession.expires_at > _now())
    if account_id:
        query = query.where(AuthSession.account_id == account_id)
    if staff_id:
        query = query.where(AuthSession.staff_id == staff_id)
    rows = list(db.scalars(query.order_by(AuthSession.last_seen_at.desc())))
    return SessionList(items=[_session_device(row, current_id) for row in rows])


# ---------------------------------------------------------------------------
# Mail / growth lazy integration
# ---------------------------------------------------------------------------


def _growth_module():
    try:
        return importlib.import_module('.growth', __package__)
    except ModuleNotFoundError:
        return None


def _mail_available(db) -> bool:
    module = _growth_module()
    function = getattr(module, 'mail_available', None) if module else None
    if function is None:
        return False
    try:
        return bool(function(db))
    except Exception:
        return False


def _enqueue_email(db, key: str, to: str, variables: dict, business_key: str):
    module = _growth_module()
    function = getattr(module, 'enqueue_email', None) if module else None
    if function is None:
        raise DomainError('EMAIL_SERVICE_UNAVAILABLE', '邮箱服务暂不可用，请稍后重试', 503)
    try:
        return function(db, key, to, variables, business_key)
    except DomainError:
        raise
    except Exception as exc:  # provider/outbox setup failure must not fake success
        raise DomainError('EMAIL_SERVICE_UNAVAILABLE', '邮箱服务暂不可用，请稍后重试', 503) from exc


def _on_registration_verified(db, account: Account) -> None:
    module = _growth_module()
    function = getattr(module, 'on_registration_verified', None) if module else None
    if function is not None:
        function(db, account)


def _base_url() -> str:
    return get_settings().public_origin.rstrip('/')


def _customer_action(path: str, raw: str, *, query: str = '') -> str:
    suffix = ('?' + query) if query else ''
    return f'{_base_url()}{path}{suffix}#token={quote(raw)}'


def _staff_action(staff: Staff, action: str, raw: str) -> str:
    prefix = '/courier' if staff.role == 'COURIER' else '/manage'
    return f'{_base_url()}{prefix}/account/{action}#token={quote(raw)}'


def _require_mail(db) -> None:
    if not _mail_available(db):
        raise DomainError('EMAIL_SERVICE_UNAVAILABLE', '邮箱服务暂不可用，新注册与找回暂不可用，请稍后重试', 503)


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def _create_token(db, kind: str, *, account_id: str | None = None, staff_id: str | None = None,
                  session_id: str | None = None, payload: dict | None = None,
                  ttl: timedelta = SECURITY_TOKEN_TTL) -> tuple[OneTimeToken, str]:
    raw = secrets.token_urlsafe(32)
    row = OneTimeToken(kind=kind, token_hash=token_hash(raw), account_id=account_id,
                       staff_id=staff_id, session_id=session_id, payload=payload or {},
                       expires_at=_now() + ttl)
    db.add(row)
    db.flush()
    return row, raw


def _invalidate_tokens(db, kind: str, *, account_id: str | None = None,
                       staff_id: str | None = None) -> None:
    now = _now()
    query = select(OneTimeToken).where(OneTimeToken.kind == kind, OneTimeToken.consumed_at.is_(None))
    if account_id:
        query = query.where(OneTimeToken.account_id == account_id)
    if staff_id:
        query = query.where(OneTimeToken.staff_id == staff_id)
    for row in db.scalars(query):
        row.consumed_at = now
        row.expires_at = min(_aware(row.expires_at), now)


def _consume_token(db, raw: str, kind: str) -> OneTimeToken:
    if not raw:
        raise DomainError('EMAIL_TOKEN_INVALID', '凭据无效或已使用', 410)
    row = db.scalar(select(OneTimeToken).where(
        OneTimeToken.kind == kind, OneTimeToken.token_hash == token_hash(raw)).with_for_update())
    if row is None or row.consumed_at is not None:
        raise DomainError('EMAIL_TOKEN_INVALID', '凭据无效或已使用', 410)
    if _aware(row.expires_at) <= _now():
        raise DomainError('EMAIL_TOKEN_EXPIRED', '凭据已过期，请重新申请', 410)
    row.consumed_at = _now()
    return row


def _load_pending_account(db, request: Request) -> Account | None:
    session = _load_session(db, request, PENDING_COOKIE, 'PENDING')
    if not _session_active(session) or session is None or not session.account_id:
        return None
    account = db.get(Account, session.account_id)
    if (account is None or account.deleted_at is not None
            or account.status not in ('PENDING_EMAIL', 'PENDING_VERIFICATION')):
        return None
    return account


def _create_pending_session(db, response: Response, request: Request, account: Account) -> None:
    _revoke_sessions(db, 'PENDING', account_id=account.id)
    raw = secrets.token_urlsafe(32)
    session = AuthSession(kind='PENDING', token_hash=token_hash(raw), account_id=account.id,
                          device_id=_device_id(request), device_label='待验证注册',
                          expires_at=_now() + SECURITY_TOKEN_TTL, last_seen_at=_now())
    db.add(session)
    db.flush()
    _set_cookie(response, PENDING_COOKIE, raw, SECURITY_TOKEN_TTL)


def _issue_registration_mail(db, account: Account, challenge: OneTimeToken, raw: str) -> RegistrationResult:
    _enqueue_email(
        db,
        'CUSTOMER_VERIFY_EMAIL',
        account.email,
        {
            'brandName': 'DormPrint',
            'actionUrl': _customer_action('/u/account/verify', raw),
            'expiresMinutes': int(SECURITY_TOKEN_TTL.total_seconds() // 60),
        },
        f'customer-verify:{challenge.id}',
    )
    return RegistrationResult(
        registration_challenge_id=challenge.id,
        id=challenge.id,
        email_masked=mask_email(account.email) or '',
        mail_status='QUEUED',
        retry_after_seconds=60,
        message='验证邮件已进入发送队列；排队不代表已经送达，请检查收件箱与垃圾邮件。',
    )


# ---------------------------------------------------------------------------
# Customer auth routes
# ---------------------------------------------------------------------------


@router.post('/customer/session', response_model=Envelope[CustomerSessionView])
def customer_session(request: Request, response: Response, db=Depends(get_db)):
    actor = actor_dep(request, db)
    if actor.account_id:
        account = db.get(Account, actor.account_id)
        return ok(_customer_session(account))
    session = _load_session(db, request, GUEST_COOKIE, 'GUEST')
    if _session_active(session) and session is not None and session.principal_id:
        principal = db.get(Principal, session.principal_id)
        if principal is not None and principal.account_id is None:
            session.last_seen_at = _now()
            return ok(_customer_session(None))
    _create_guest_session(db, response, request)
    return ok(_customer_session(None))


@router.get('/customer/auth/me', response_model=Envelope[CustomerSessionView])
def customer_me(request: Request, db=Depends(get_db)):
    actor = actor_dep(request, db)
    if actor.account_id:
        account = db.get(Account, actor.account_id)
        if account is not None:
            return ok(_customer_session(account))
    return ok(_customer_session(None))


@router.post('/customer/auth/register', response_model=Envelope[RegistrationResult])
def customer_register(payload: RegisterInput, request: Request, response: Response, db=Depends(get_db)):
    _require_mail(db)
    email = normalize_email(payload.email)
    advisory_lock(db, 'customer-register:' + email)
    account = db.scalar(select(Account).where(func.lower(Account.email) == email.lower()).with_for_update())
    if account is not None:
        if account.deleted_at is not None or account.status == 'SUSPENDED':
            raise DomainError('AUTH_ACCOUNT_SUSPENDED', '该账号当前不可用', 403)
        if account.verified_at is not None or account.status == 'ACTIVE':
            raise DomainError('EMAIL_ALREADY_REGISTERED', '该邮箱已注册，请直接登录或找回密码', 409)
        account.password_hash = hash_password(payload.password)
    else:
        principal = Principal()
        db.add(principal)
        db.flush()
        account = Account(
            principal_id=principal.id,
            email=email,
            password_hash=hash_password(payload.password),
            status='PENDING_EMAIL',
            display_name='',
            invite_code=secrets.token_urlsafe(16),
            preferences={'orderCreated': False, 'orderStatusUpdated': False, 'referralReward': False},
        )
        db.add(account)
        db.flush()
        principal.account_id = account.id
        db.flush()

    if payload.invite_code:
        invite_code = payload.invite_code.strip()
        inviter = db.scalar(select(Account).where(Account.invite_code == invite_code))
        if (inviter is None or inviter.id == account.id or inviter.status != 'ACTIVE'
                or not inviter.verified_at or inviter.deleted_at is not None):
            raise DomainError('REFERRAL_INVALID', '邀请码无效或邀请人当前不可用', 422)
        relation = db.scalar(select(Referral).where(Referral.invitee_id == account.id))
        if relation is None:
            db.add(Referral(inviter_id=inviter.id, invitee_id=account.id,
                            status='WAITING_FIRST_ORDER',
                            data={'inviteCode': invite_code, 'registeredAt': _now().isoformat()}))
        elif relation.inviter_id != inviter.id:
            raise DomainError('REFERRAL_ALREADY_ASSIGNED', '该账号已登记其他邀请关系', 409)
    _invalidate_tokens(db, 'REGISTRATION', account_id=account.id)
    challenge, raw = _create_token(
        db, 'REGISTRATION', account_id=account.id,
        payload={'inviteCode': payload.invite_code, 'email': email},
    )
    _create_pending_session(db, response, request, account)
    result = _issue_registration_mail(db, account, challenge, raw)
    audit(db, 'account:' + account.id, 'CUSTOMER_REGISTER', 'account', account.id)
    return ok(result)


@router.post('/customer/auth/resend-verification', response_model=Envelope[RegistrationResult])
def customer_resend_verification(payload: ResendVerificationInput, request: Request,
                                 response: Response, db=Depends(get_db)):
    _require_mail(db)
    if payload.registration_challenge_id:
        account = _load_pending_account(db, request)
        challenge = db.get(OneTimeToken, payload.registration_challenge_id)
        if (account is None or challenge is None or challenge.kind != 'REGISTRATION'
                or challenge.account_id != account.id):
            raise DomainError('PENDING_REGISTRATION_REQUIRED', '请从本次注册的待验证会话重新申请', 401)
        _invalidate_tokens(db, 'REGISTRATION', account_id=account.id)
        new_challenge, raw = _create_token(
            db, 'REGISTRATION', account_id=account.id,
            payload={'inviteCode': (challenge.payload or {}).get('inviteCode'), 'email': account.email},
        )
        _create_pending_session(db, response, request, account)
        return ok(_issue_registration_mail(db, account, new_challenge, raw))
    # Email-only requests stay anti-enumerating and also send a fresh token
    # for a real pending registration. Different shape (challenge id present)
    # is reserved for the browser that still holds the pending session.
    email = normalize_email(payload.email)
    account = db.scalar(select(Account).where(func.lower(Account.email) == email.lower()))
    if (account is not None and account.status in ('PENDING_EMAIL', 'PENDING_VERIFICATION')
            and account.deleted_at is None and not account.verified_at):
        token, raw = _create_token(
            db, 'REGISTRATION', account_id=account.id,
            payload={'inviteCode': None, 'email': account.email},
        )
        _issue_registration_mail(db, account, token, raw)
    return ok(RegistrationResult(
        registration_challenge_id=None,
        email_masked=mask_email(email) or '',
        mail_status='QUEUED',
        message='如该邮箱存在待验证注册，验证邮件将按安全策略发送；本响应不确认邮箱是否存在。',
    ))


@router.post('/customer/auth/change-pending-email', response_model=Envelope[RegistrationResult])
def customer_change_pending_email(payload: ChangePendingEmailInput, request: Request,
                                  response: Response, db=Depends(get_db)):
    _require_mail(db)
    account = _load_pending_account(db, request)
    if account is None:
        raise DomainError('PENDING_REGISTRATION_REQUIRED', '待验证注册会话已失效，请重新注册', 401)
    challenge = db.get(OneTimeToken, payload.registration_challenge_id)
    if challenge is None or challenge.kind != 'REGISTRATION' or challenge.account_id != account.id:
        raise DomainError('PENDING_REGISTRATION_REQUIRED', '注册挑战无效，请重新申请验证邮件', 401)
    email = normalize_email(payload.new_email)
    advisory_lock(db, 'customer-register:' + email)
    conflict = db.scalar(select(Account).where(
        func.lower(Account.email) == email.lower(), Account.id != account.id))
    if conflict is not None:
        raise DomainError('EMAIL_ALREADY_REGISTERED', '该邮箱已注册，请直接登录或找回密码', 409)
    locked = lock(db, Account, account.id)
    locked.email = email
    touch(locked)
    _invalidate_tokens(db, 'REGISTRATION', account_id=account.id)
    new_challenge, raw = _create_token(
        db, 'REGISTRATION', account_id=account.id,
        payload={'inviteCode': (challenge.payload or {}).get('inviteCode'), 'email': email},
    )
    _create_pending_session(db, response, request, account)
    audit(db, 'account:' + account.id, 'CUSTOMER_PENDING_EMAIL_CHANGED', 'account', account.id)
    return ok(_issue_registration_mail(db, locked, new_challenge, raw))


@router.post('/customer/auth/verify-email', response_model=Envelope[CustomerSessionView])
def customer_verify_email(payload: TokenInput, request: Request, response: Response, db=Depends(get_db)):
    challenge = _consume_token(db, payload.token, 'REGISTRATION')
    if not challenge.account_id:
        raise DomainError('EMAIL_TOKEN_INVALID', '凭据无效或已使用', 410)
    account = lock(db, Account, challenge.account_id)
    if account.deleted_at is not None or account.status == 'SUSPENDED':
        raise DomainError('AUTH_ACCOUNT_SUSPENDED', '该账号当前不可用', 403)
    if account.verified_at is not None and account.status == 'ACTIVE':
        raise DomainError('EMAIL_TOKEN_INVALID', '凭据无效或已使用', 410)
    if _active_session_count(db, 'CUSTOMER', account_id=account.id) >= MAX_DEVICES:
        raise DomainError(
            'DEVICE_LIMIT_REACHED',
            '此账号最多同时登录 2 台设备，请先在已登录设备退出一个设备后重试',
            409,
        )
    account.status = 'ACTIVE'
    account.verified_at = _now()
    touch(account)
    _revoke_sessions(db, 'PENDING', account_id=account.id)
    _on_registration_verified(db, account)
    current = _load_session(db, request, ACCOUNT_COOKIE, 'CUSTOMER')
    _issue_account_session(db, response, request, account, current)
    audit(db, 'account:' + account.id, 'CUSTOMER_EMAIL_VERIFIED', 'account', account.id)
    return ok(_customer_session(account))


@router.post('/customer/auth/login', response_model=Envelope[CustomerSessionView])
def customer_login(payload: CustomerLoginInput, request: Request, response: Response, db=Depends(get_db)):
    email = normalize_email(payload.email)
    account = db.scalar(select(Account).where(
        func.lower(Account.email) == email.lower()).with_for_update())
    if account is None:
        _dummy_verify(payload.password)
        raise DomainError('AUTH_INVALID_CREDENTIALS', '邮箱或密码不正确', 401)
    if not verify_password(account.password_hash, payload.password):
        raise DomainError('AUTH_INVALID_CREDENTIALS', '邮箱或密码不正确', 401)
    if account.status == DELETION_PENDING:
        from .account_lifecycle import recover_account, recovery_deadline
        request_row = recover_account(db, account)
        deadline = recovery_deadline(request_row)
        current = _load_session(db, request, ACCOUNT_COOKIE, 'CUSTOMER')
        _issue_account_session(db, response, request, account, current)
        _clear_cookie(response, STAFF_COOKIE)
        audit(db, 'account:' + account.id, 'CUSTOMER_LOGIN', 'account', account.id)
        return ok(_customer_session(
            account, recovered=True, recovery_until=deadline,
            message='账号已在注销恢复期内恢复；此前的会话不会被重新启用。'))
    if account.deleted_at is not None or account.status == 'SUSPENDED':
        raise DomainError('AUTH_ACCOUNT_SUSPENDED', '该账号当前不可用', 403)
    if not account.verified_at or account.status != 'ACTIVE':
        raise DomainError('EMAIL_NOT_VERIFIED', '请先完成邮箱验证', 403)
    current = _load_session(db, request, ACCOUNT_COOKIE, 'CUSTOMER')
    _issue_account_session(db, response, request, account, current)
    # Pre-unification staff cookies confer no identity; remove them on login.
    _clear_cookie(response, STAFF_COOKIE)
    audit(db, 'account:' + account.id, 'CUSTOMER_LOGIN', 'account', account.id)
    return ok(_customer_session(account))


@router.post('/customer/auth/logout', response_model=Envelope[RevokeResult])
def customer_logout(request: Request, response: Response, db=Depends(get_db)):
    session = _load_session(db, request, ACCOUNT_COOKIE, 'CUSTOMER')
    revoked = 0
    if _session_active(session) and session is not None:
        _revoke_session(session)
        revoked = 1
        audit(db, 'account:' + (session.account_id or ''), 'CUSTOMER_LOGOUT', 'auth_session', session.id)
    _clear_cookie(response, ACCOUNT_COOKIE)
    _clear_cookie(response, STAFF_COOKIE)
    return ok(RevokeResult(revoked=True, revoked_count=revoked, current_revoked=True))


@router.post('/customer/auth/forgot-password', response_model=Envelope[MailResult])
def customer_forgot_password(payload: EmailInput, request: Request, db=Depends(get_db)):
    _require_mail(db)
    email = normalize_email(payload.email)
    account = db.scalar(select(Account).where(func.lower(Account.email) == email.lower()))
    if account is not None and account.verified_at and account.status == 'ACTIVE' and account.deleted_at is None:
        _invalidate_tokens(db, 'CUSTOMER_RESET', account_id=account.id)
        token, raw = _create_token(db, 'CUSTOMER_RESET', account_id=account.id, payload={})
        _enqueue_email(
            db,
            'CUSTOMER_RESET_PASSWORD',
            account.email,
            {
                'brandName': 'DormPrint',
                'actionUrl': _customer_action('/u/account/reset', raw),
                'expiresMinutes': int(SECURITY_TOKEN_TTL.total_seconds() // 60),
            },
            f'customer-reset:{token.id}',
        )
        audit(db, 'account:' + account.id, 'CUSTOMER_PASSWORD_RESET_REQUESTED', 'account', account.id)
    return ok(MailResult(
        status='QUEUED',
        mail_status='QUEUED',
        retry_after_seconds=60,
        message='如果该邮箱对应账号，重置邮件将按安全策略发送；本响应不确认账号是否存在。',
    ))


@router.post('/customer/auth/reset-password', response_model=Envelope[PasswordChangedResult])
def customer_reset_password(payload: ResetPasswordInput, request: Request, response: Response,
                            db=Depends(get_db)):
    token = _consume_token(db, payload.token, 'CUSTOMER_RESET')
    if not token.account_id:
        raise DomainError('EMAIL_TOKEN_INVALID', '凭据无效或已使用', 410)
    account = lock(db, Account, token.account_id)
    if account.deleted_at is not None or account.status == 'SUSPENDED':
        raise DomainError('AUTH_ACCOUNT_SUSPENDED', '该账号当前不可用', 403)
    account.password_hash = hash_password(payload.new_password)
    account.session_version = (account.session_version or 1) + 1
    touch(account)
    revoked = _revoke_sessions(db, 'CUSTOMER', account_id=account.id)
    _invalidate_tokens(db, 'CUSTOMER_RESET', account_id=account.id)
    _invalidate_tokens(db, 'EMAIL_CHANGE', account_id=account.id)
    _clear_cookie(response, ACCOUNT_COOKIE)
    audit(db, 'account:' + account.id, 'CUSTOMER_PASSWORD_RESET', 'account', account.id)
    return ok(PasswordChangedResult(password_changed=True, sessions_revoked=revoked))


# ---------------------------------------------------------------------------
# Customer account routes
# ---------------------------------------------------------------------------


@router.get('/customer/account', response_model=Envelope[CustomerAccountView])
def customer_account(request: Request, db=Depends(get_db), actor=Depends(actor_dep)):
    require_customer(actor, verified=True)
    account = db.get(Account, actor.account_id)
    if account is None or account.deleted_at is not None:
        raise DomainError('AUTH_REQUIRED', '请先登录已验证账号', 401)
    return ok(_account_view(account))


@router.patch('/customer/account', response_model=Envelope[CustomerAccountView])
def customer_update_account(payload: ProfileInput, db=Depends(get_db), actor=Depends(actor_dep)):
    require_customer(actor, verified=True)
    account = lock(db, Account, actor.account_id, payload.expected_version)
    account.display_name = payload.display_name
    touch(account)
    audit(db, actor, 'CUSTOMER_PROFILE_UPDATED', 'account', account.id)
    return ok(_account_view(account))


@router.patch('/customer/account/notification-preferences', response_model=Envelope[CustomerAccountView])
def customer_notification_preferences(payload: NotificationInput, db=Depends(get_db),
                                      actor=Depends(actor_dep)):
    require_customer(actor, verified=True)
    account = lock(db, Account, actor.account_id, payload.expected_version)
    account.preferences = {
        'orderCreated': payload.order_created,
        'orderStatusUpdated': payload.order_status_updated,
        'referralReward': payload.referral_reward,
    }
    touch(account)
    audit(db, actor, 'CUSTOMER_NOTIFICATION_PREFERENCES_UPDATED', 'account', account.id)
    return ok(_account_view(account))


@router.post('/customer/account/change-password', response_model=Envelope[PasswordChangedResult])
def customer_change_password(payload: ChangePasswordInput, request: Request, db=Depends(get_db),
                             actor=Depends(actor_dep)):
    require_customer(actor, verified=True)
    account = lock(db, Account, actor.account_id)
    if not verify_password(account.password_hash, payload.current_password):
        raise DomainError('AUTH_REAUTH_REQUIRED', '当前密码不正确，请重新核验身份', 401)
    account.password_hash = hash_password(payload.new_password)
    account.session_version = (account.session_version or 1) + 1
    touch(account)
    current = db.get(AuthSession, actor.account_session_id) if actor.account_session_id else None
    if current is not None:
        current.session_version = account.session_version
    revoked = _revoke_sessions(db, 'CUSTOMER', account_id=account.id, except_id=current.id if current else None)
    _invalidate_tokens(db, 'CUSTOMER_RESET', account_id=account.id)
    _invalidate_tokens(db, 'EMAIL_CHANGE', account_id=account.id)
    audit(db, actor, 'CUSTOMER_PASSWORD_CHANGED', 'account', account.id)
    return ok(PasswordChangedResult(password_changed=True, sessions_revoked=revoked))


@router.post('/customer/account/request-email-change', response_model=Envelope[MailResult])
def customer_request_email_change(payload: ChangeEmailInput, request: Request, db=Depends(get_db),
                                  actor=Depends(actor_dep)):
    require_customer(actor, verified=True)
    _require_mail(db)
    account = lock(db, Account, actor.account_id, payload.expected_version)
    if not verify_password(account.password_hash, payload.current_password):
        raise DomainError('AUTH_REAUTH_REQUIRED', '当前密码不正确，请重新核验身份', 401)
    new_email = normalize_email(payload.new_email)
    if new_email.lower() == account.email.lower():
        raise DomainError('EMAIL_UNCHANGED', '新邮箱与当前邮箱相同', 422)
    advisory_lock(db, 'customer-register:' + new_email)
    conflict = db.scalar(select(Account).where(
        func.lower(Account.email) == new_email.lower(), Account.id != account.id))
    if conflict is not None:
        raise DomainError('EMAIL_ALREADY_REGISTERED', '该邮箱已注册', 409)
    _invalidate_tokens(db, 'EMAIL_CHANGE', account_id=account.id)
    token, raw = _create_token(db, 'EMAIL_CHANGE', account_id=account.id,
                               payload={'newEmail': new_email})
    _enqueue_email(
        db,
        'CUSTOMER_VERIFY_EMAIL',
        new_email,
        {
            'brandName': 'DormPrint',
            'actionUrl': _customer_action('/u/account/verify', raw, query='mode=email-change'),
            'expiresMinutes': int(SECURITY_TOKEN_TTL.total_seconds() // 60),
        },
        f'customer-email-change:{token.id}',
    )
    audit(db, actor, 'CUSTOMER_EMAIL_CHANGE_REQUESTED', 'account', account.id)
    return ok(MailResult(
        status='QUEUED', mail_status='QUEUED', retry_after_seconds=60,
        message='确认邮件已进入发送队列；新邮箱验证前原邮箱继续有效。',
    ))


@router.post('/customer/account/confirm-email-change', response_model=Envelope[CustomerAccountView])
def customer_confirm_email_change(payload: TokenInput, db=Depends(get_db), actor=Depends(actor_dep)):
    token = _consume_token(db, payload.token, 'EMAIL_CHANGE')
    if not token.account_id:
        raise DomainError('EMAIL_TOKEN_INVALID', '凭据无效或已使用', 410)
    new_email = normalize_email((token.payload or {}).get('newEmail', ''))
    advisory_lock(db, 'customer-register:' + new_email)
    conflict = db.scalar(select(Account).where(
        func.lower(Account.email) == new_email.lower(), Account.id != token.account_id))
    if conflict is not None:
        raise DomainError('EMAIL_ALREADY_REGISTERED', '该邮箱已注册', 409)
    account = lock(db, Account, token.account_id)
    if account.deleted_at is not None or account.status == 'SUSPENDED':
        raise DomainError('AUTH_ACCOUNT_SUSPENDED', '该账号当前不可用', 403)
    account.email = new_email
    account.verified_at = _now()
    touch(account)
    audit(db, 'account:' + account.id, 'CUSTOMER_EMAIL_CHANGED', 'account', account.id)
    return ok(_account_view(account))


@router.get('/customer/account/sessions', response_model=Envelope[SessionList])
def customer_sessions(db=Depends(get_db), actor=Depends(actor_dep)):
    require_customer(actor, verified=True)
    return ok(_session_list(db, 'CUSTOMER', account_id=actor.account_id,
                            current_id=actor.account_session_id))


@router.post('/customer/account/sessions/revoke', response_model=Envelope[RevokeResult])
def customer_revoke_session(payload: SessionRevokeInput, response: Response, db=Depends(get_db),
                            actor=Depends(actor_dep)):
    require_customer(actor, verified=True)
    if payload.session_id == 'ALL_OTHERS':
        count = _revoke_sessions(db, 'CUSTOMER', account_id=actor.account_id,
                                 except_id=actor.account_session_id)
        audit(db, actor, 'CUSTOMER_SESSIONS_REVOKED', 'account', actor.account_id or '',
              reason=payload.reason, data={'scope': 'ALL_OTHERS', 'revoked': count})
        return ok(RevokeResult(revoked=True, revoked_count=count))
    session = db.get(AuthSession, payload.session_id)
    if (session is None or session.kind != 'CUSTOMER'
            or session.account_id != actor.account_id or not _session_active(session)):
        raise DomainError('NOT_FOUND', '会话不存在或不可访问', 404)
    current = session.id == actor.account_session_id
    _revoke_session(session)
    if current:
        _clear_cookie(response, ACCOUNT_COOKIE)
    audit(db, actor, 'CUSTOMER_SESSION_REVOKED', 'auth_session', session.id, reason=payload.reason)
    return ok(RevokeResult(revoked=True, revoked_count=1, current_revoked=current))


@router.post('/customer/account/link-guest', response_model=Envelope[LinkGuestResult])
def customer_link_guest(payload: ConfirmInput, request: Request, response: Response,
                        db=Depends(get_db), actor=Depends(actor_dep)):
    require_customer(actor, verified=True)
    guest_session = _load_session(db, request, GUEST_COOKIE, 'GUEST')
    if not _session_active(guest_session) or guest_session is None or not guest_session.principal_id:
        raise DomainError('GUEST_SESSION_REQUIRED', '需要当前浏览器仍有效的游客会话才能显式关联', 409)
    principal = db.scalar(select(Principal).where(Principal.id == guest_session.principal_id).with_for_update())
    if principal is None:
        raise DomainError('GUEST_SESSION_REQUIRED', '当前游客身份已失效', 409)
    account = lock(db, Account, actor.account_id)
    if principal.account_id is not None and principal.account_id != account.id:
        raise DomainError('ACCOUNT_LINK_CONFLICT', '该游客身份已关联其他账号，不能重复认领', 409)
    principal.account_id = account.id
    touch(principal)
    for session in db.scalars(select(AuthSession).where(
            AuthSession.kind == 'GUEST', AuthSession.principal_id == principal.id,
            AuthSession.revoked_at.is_(None))):
        _revoke_session(session)
    new_principal, _ = _create_guest_session(db, response, request)
    audit(db, actor, 'CUSTOMER_GUEST_LINKED', 'principal', principal.id,
          data={'guestPrincipalId': principal.id, 'newGuestPrincipalId': new_principal.id})
    return ok(LinkGuestResult(
        rotation_completed=True,
        customer_principal_id=account.principal_id,
        guest_principal_id=new_principal.id,
        id=account.principal_id,
    ))


@router.post('/customer/account/import-order', response_model=Envelope[ImportOrderResult])
def customer_import_order(payload: ImportOrderInput, request: Request, db=Depends(get_db),
                          actor=Depends(actor_dep)):
    require_customer(actor, verified=True)
    order_session = _load_session(db, request, ORDER_ACCESS_COOKIE, 'ORDER_ACCESS')
    grant = _valid_grant_session(db, order_session)
    if grant is None or grant.order_id != payload.order_id:
        raise DomainError('ORDER_GRANT_SCOPE_DENIED', '需要该订单当前有效的访问授权', 403)
    grant = db.scalar(select(AccessGrant).where(AccessGrant.id == grant.id).with_for_update())
    if not _grant_active(grant):
        raise DomainError('ORDER_GRANT_REVOKED', '订单访问授权已撤销或过期', 410)
    order = db.get(Order, payload.order_id)
    if order is None or order.deleted_at is not None:
        raise DomainError('NOT_FOUND', '订单不存在或不可访问', 404)
    effective = grant_scopes(db, Actor(grant_ids=[grant.id]), order.id)
    if 'ORDER_IMPORT' not in effective:
        raise DomainError('ORDER_GRANT_SCOPE_DENIED', '当前链接未授予单单导入权限', 403)
    advisory_lock(db, f'order-import:{actor.account_id}:{order.id}:{grant.id}')
    existing = db.scalar(select(AccessGrant).where(
        AccessGrant.order_id == order.id,
        AccessGrant.account_id == actor.account_id,
        AccessGrant.source_grant_id == grant.id,
        AccessGrant.revoked_at.is_(None),
        AccessGrant.expires_at > _now(),
    ))
    if existing is not None:
        return ok(ImportOrderResult(
            account_order_grant_id=existing.id,
            source_grant_id=grant.id,
            scopes=list(existing.scopes or []),
            expires_at=existing.expires_at,
            id=existing.id,
            order_id=order.id,
        ))
    scopes = [scope for scope in (grant.scopes or []) if scope in effective]
    derived = AccessGrant(
        order_id=order.id,
        account_id=actor.account_id,
        source_grant_id=grant.id,
        scopes=scopes,
        expires_at=min(_aware(grant.expires_at), _now() + ORDER_LINK_MAX_TTL),
        created_by=actor.id,
    )
    db.add(derived)
    db.flush()
    audit(db, actor, 'ORDER_IMPORTED_TO_ACCOUNT', 'order', order.id,
          data={'sourceGrantId': grant.id, 'accountOrderGrantId': derived.id})
    return ok(ImportOrderResult(
        account_order_grant_id=derived.id,
        source_grant_id=grant.id,
        scopes=scopes,
        expires_at=derived.expires_at,
        id=derived.id,
        order_id=order.id,
    ))


@router.post('/customer/account/export-request', response_model=Envelope[ExportRequestResult])
def customer_export_request(payload: ExportRequestInput, db=Depends(get_db), actor=Depends(actor_dep)):
    require_customer(actor, verified=True)
    advisory_lock(db, 'customer-export:' + (actor.account_id or ''))
    existing = db.scalar(select(ExportJob).where(
        ExportJob.account_id == actor.account_id, ExportJob.kind == 'PERSONAL_DATA',
        ExportJob.status.in_(('QUEUED', 'PROCESSING')), ExportJob.expires_at > _now()))
    if existing is not None:
        return ok(ExportRequestResult(export_job_id=existing.id, status=existing.status,
                                      expires_at=existing.expires_at,
                                      message='已有个人资料导出申请在处理中'))
    row = ExportJob(account_id=actor.account_id, kind='PERSONAL_DATA', status='QUEUED',
                    expires_at=_now() + EXPORT_TTL,
                    data={'requestedAt': _now().isoformat(), 'reason': payload.reason})
    db.add(row)
    db.flush()
    audit(db, actor, 'CUSTOMER_EXPORT_REQUESTED', 'export_job', row.id)
    return ok(ExportRequestResult(export_job_id=row.id, status=row.status, expires_at=row.expires_at))


@router.post('/customer/account/deletion-request', response_model=Envelope[DeletionRequestResult])
def customer_deletion_request(payload: DeletionRequestInput, response: Response,
                              db=Depends(get_db), actor=Depends(actor_dep)):
    require_customer(actor, verified=True)
    from .account_lifecycle import request_self_deletion
    result = request_self_deletion(db, db.get(Account, actor.account_id),
                                   reason=payload.reason, actor=actor)
    if result['status'] == 'RECOVERY':
        # The recovery window already revoked every normal session; remove the
        # stale browser cookie as well so it cannot be replayed.
        _clear_cookie(response, ACCOUNT_COOKIE)
    requested_at = result['requestedAt']
    if isinstance(requested_at, str):
        from datetime import datetime
        requested_at = datetime.fromisoformat(requested_at)
    return ok(DeletionRequestResult(
        deletion_request_id=result['id'],
        status=result['status'],
        requested_at=requested_at,
        blockers=result['blockers'],
        message=result['message'],
        id=result['id'],
        recovery_until=result['recoveryUntil'],
        can_recover=result['canRecover'],
        recovered=result['recovered'],
    ))


# ---------------------------------------------------------------------------
# Order access links
# ---------------------------------------------------------------------------


def _validate_scopes(scopes: list[str] | None) -> list[str]:
    values = list(scopes) if scopes is not None else list(READ_ONLY_SCOPES)
    if not values:
        raise DomainError('ORDER_GRANT_SCOPE_INVALID', '至少需要一个订单访问权限', 422)
    unknown = sorted({value for value in values if value not in LINK_SCOPES})
    if unknown:
        raise DomainError('ORDER_GRANT_SCOPE_DENIED', '包含未知的订单访问权限', 422,
                          {'unknownScopes': unknown})
    return [scope for scope in LINK_SCOPES if scope in set(values)]


def _grant_active(grant: AccessGrant | None) -> bool:
    return bool(grant and grant.revoked_at is None and _aware(grant.expires_at) > _now())


def _link_view(grant: AccessGrant) -> AccessLinkView:
    return AccessLinkView(
        id=grant.id,
        order_id=grant.order_id,
        version=grant.version,
        scopes=list(grant.scopes or []),
        created_at=grant.created_at,
        created_by=grant.created_by or '',
        expires_at=grant.expires_at,
        revoked_at=grant.revoked_at,
        last_access_at=grant.last_used_at,
    )


def _order_access_view(grant: AccessGrant, scopes: list[str], order: Order) -> dict:
    # The frontend compares allowedActions against the same scope constants
    # (`canOrderAction`), so this list must contain scope names, not prose.
    allowed = [scope for scope in scopes
               if scope in ('ORDER_VIEW', 'FINAL_PDF_PREVIEW', 'DELIVERY_PROOF_VIEW')]
    if order.status == 'PENDING_CONFIRMATION':
        if 'ORDER_REVISE_PENDING' in scopes:
            allowed.append('ORDER_REVISE_PENDING')
        if 'ORDER_CANCEL_PENDING' in scopes:
            allowed.append('ORDER_CANCEL_PENDING')
    if (order.status not in LOCATION_CHANGE_BLOCKED_STATUSES
            and 'ORDER_CHANGE_LOCATION_PRE_DELIVERY' in scopes):
        allowed.append('ORDER_CHANGE_LOCATION_PRE_DELIVERY')
    if 'ORDER_IMPORT' in scopes:
        allowed.append('ORDER_IMPORT')
    return {
        'orderId': order.id,
        'sourceGrantId': grant.id,
        'scopes': scopes,
        'allowedActions': allowed,
        'expiresAt': grant.expires_at,
    }


def _order_view(db, order: Order, actor: Actor) -> dict:
    module = None
    try:
        module = importlib.import_module('.commerce', __package__)
    except ModuleNotFoundError:
        module = None
    function = getattr(module, 'order_view', None) if module else None
    if function is not None:
        return function(db, order, actor)
    # Module-local fallback for auth tests and bootstrap. It is a read-only
    # projection of the real order and never invents business state.
    data = order.data or {}
    return {
        'id': order.id,
        'pickupCode': order.pickup_code,
        'status': order.status,
        'totalCents': order.total_cents,
        'contentRevision': order.content_revision,
        'createdAt': order.created_at,
        'updatedAt': order.updated_at,
        'quote': data.get('quote'),
        'settlement': data.get('settlement'),
        'allowedActions': [],
        'blockers': [],
    }


def issue_order_grant(db, order: Order, actor, scopes: list[str] | None = None,
                      expires_at: datetime | None = None) -> tuple[AccessGrant, str]:
    """Issue a single-order link. The raw token is returned exactly once."""
    validated = _validate_scopes(scopes)
    now = _now()
    expiry = _aware(expires_at) if expires_at is not None else now + ORDER_LINK_MAX_TTL
    if expiry <= now:
        raise DomainError('ORDER_LINK_EXPIRES_INVALID', '链接到期时间必须晚于当前时间', 422)
    if expiry > now + ORDER_LINK_MAX_TTL:
        raise DomainError('ORDER_LINK_EXPIRES_INVALID', '订单链接有效期不能超过 30 天', 422)
    raw = secrets.token_urlsafe(32)
    grant = AccessGrant(
        order_id=order.id,
        token_hash=token_hash(raw),
        scopes=validated,
        expires_at=expiry,
        created_by=actor.id if isinstance(actor, Actor) else str(actor),
    )
    db.add(grant)
    db.flush()
    return grant, raw


def _revoke_grant_tree(db, order_id: str, root_ids: list[str]) -> list[str]:
    rows = list(db.scalars(select(AccessGrant).where(
        AccessGrant.order_id == order_id).with_for_update()))
    children: dict[str | None, list[AccessGrant]] = {}
    for row in rows:
        children.setdefault(row.source_grant_id, []).append(row)
    targets: set[str] = set()
    stack = list(root_ids)
    while stack:
        current = stack.pop()
        if current in targets:
            continue
        targets.add(current)
        for child in children.get(current, []):
            stack.append(child.id)
    now = _now()
    affected: list[str] = []
    for row in rows:
        if row.id in targets and row.revoked_at is None:
            row.revoked_at = now
            row.version = (row.version or 0) + 1
            affected.append(row.id)
    _revoke_sessions_for_grants(db, targets)
    return affected


def _require_link_admin(actor: Actor) -> None:
    require_admin(actor, 'order.access-link.manage')


@router.get('/admin/orders/{order_id}/access-links', response_model=Envelope[AccessLinkList])
def admin_access_links(order_id: str, db=Depends(get_db), actor=Depends(actor_dep)):
    _require_link_admin(actor)
    order = db.get(Order, order_id)
    if order is None or order.deleted_at is not None:
        raise DomainError('NOT_FOUND', '订单不存在或不可访问', 404)
    rows = db.scalars(select(AccessGrant).where(
        AccessGrant.order_id == order.id, AccessGrant.token_hash.is_not(None)
    ).order_by(AccessGrant.created_at.desc()))
    return ok(AccessLinkList(items=[_link_view(row) for row in rows]))


@router.post('/admin/orders/{order_id}/access-links', response_model=Envelope[AccessLinkIssued])
def admin_issue_access_link(order_id: str, payload: AccessLinkInput, db=Depends(get_db),
                            actor=Depends(actor_dep)):
    _require_link_admin(actor)
    order = lock(db, Order, order_id)
    if order.deleted_at is not None:
        raise DomainError('NOT_FOUND', '订单不存在或不可访问', 404)
    revoked: list[str] = []
    reissue = payload.reissue or payload.revoke_previous
    if reissue:
        active_roots = list(db.scalars(select(AccessGrant).where(
            AccessGrant.order_id == order.id, AccessGrant.token_hash.is_not(None),
            AccessGrant.revoked_at.is_(None), AccessGrant.expires_at > _now())))
        revoked = _revoke_grant_tree(db, order.id, [row.id for row in active_roots])
    requested_scopes = payload.scopes if payload.scopes is not None else payload.scope
    grant, raw = issue_order_grant(db, order, actor, requested_scopes, payload.expires_at)
    audit(db, actor, 'ORDER_ACCESS_LINK_REISSUED' if reissue else 'ORDER_ACCESS_LINK_ISSUED',
          'order', order.id, reason=payload.reason,
          data={'grantId': grant.id, 'scopes': grant.scopes, 'revokedGrantIds': revoked})
    return ok(AccessLinkIssued(grant=_link_view(grant), access_token=raw,
                               revoked_grant_ids=revoked, id=grant.id, order_id=order.id))


@router.post('/admin/orders/{order_id}/access-links/reissue',
             response_model=Envelope[AccessLinkIssued])
def admin_reissue_access_link(order_id: str, payload: AccessLinkInput, db=Depends(get_db),
                              actor=Depends(actor_dep)):
    """Explicit lost-credential reissue: revoke the order's prior links first."""
    payload.reissue = True
    return admin_issue_access_link(order_id, payload, db, actor)


@router.post('/admin/orders/{order_id}/access-links/{grant_id}/revoke',
             response_model=Envelope[GrantRevokeResult])
def admin_revoke_access_link(order_id: str, grant_id: str, payload: ReasonInput,
                             db=Depends(get_db), actor=Depends(actor_dep)):
    _require_link_admin(actor)
    order = lock(db, Order, order_id)
    if order.deleted_at is not None:
        raise DomainError('NOT_FOUND', '订单不存在或不可访问', 404)
    grant = lock(db, AccessGrant, grant_id, payload.expected_version)
    if grant.order_id != order.id or grant.token_hash is None:
        raise DomainError('NOT_FOUND', '授权不存在或不可访问', 404)
    affected = _revoke_grant_tree(db, order.id, [grant.id])
    if grant.id not in affected:
        affected.append(grant.id)
    audit(db, actor, 'ORDER_ACCESS_LINK_REVOKED', 'order', order.id, reason=payload.reason,
          data={'grantId': grant.id, 'affectedGrantIds': affected})
    return ok(GrantRevokeResult(revoked=True, grant_id=grant.id,
                                affected_grant_ids=affected, owner_preserved=True,
                                id=grant.id, order_id=order.id))


@router.post('/order-access/exchange', response_model=Envelope[OrderAccessExchange])
def order_access_exchange(payload: ExchangeInput, request: Request, response: Response,
                          db=Depends(get_db)):
    grant = db.scalar(select(AccessGrant).where(
        AccessGrant.token_hash == token_hash(payload.access_token)).with_for_update())
    if grant is None or grant.token_hash is None:
        raise DomainError('ORDER_GRANT_INVALID', '订单访问凭据无效', 401)
    if grant.order_id != payload.order_id:
        raise DomainError('ORDER_GRANT_INVALID', '订单访问凭据无效', 401)
    if grant.revoked_at is not None:
        raise DomainError('ORDER_GRANT_REVOKED', '订单访问授权已撤销，请联系管理员重新签发', 410)
    if _aware(grant.expires_at) <= _now():
        raise DomainError('ORDER_GRANT_EXPIRED', '订单访问授权已过期，请联系管理员重新签发', 410)
    order = db.get(Order, grant.order_id)
    if order is None or order.deleted_at is not None:
        raise DomainError('NOT_FOUND', '订单不存在或不可访问', 404)
    effective = grant_scopes(db, Actor(grant_ids=[grant.id]), order.id)
    if not effective:
        raise DomainError('ORDER_GRANT_REVOKED', '订单访问授权链已失效', 410)
    grant.last_used_at = _now()
    _issue_order_session(db, response, request, grant)
    access = _order_access_view(grant, [scope for scope in (grant.scopes or []) if scope in effective], order)
    view = _order_view(db, order, Actor(grant_ids=[grant.id]))
    return ok(OrderAccessExchange(order=view, access=OrderAccessView(**access),
                                  id=order.id, order_id=order.id))


# ---------------------------------------------------------------------------
# Employee auth and security
# ---------------------------------------------------------------------------


def _require_staff_actor(actor: Actor) -> None:
    if not actor.staff_id:
        raise DomainError('AUTH_REQUIRED', '请先登录员工账号', 401)


@router.post('/auth/login', response_model=Envelope[StaffSessionView])
def staff_login(payload: StaffLoginInput, request: Request, response: Response, db=Depends(get_db)):
    """Compatibility login for an existing unified customer account.

    FINAL N05 has no standalone staff password. This endpoint verifies the normal
    registered account credential and issues the ordinary customer account cookie,
    from which employee rights are derived. It never reads ``Staff.password_hash``.
    """
    identifier = payload.username.strip()
    account: Account | None = None
    if '@' in identifier:
        try:
            email = normalize_email(identifier)
        except ValueError:
            email = ''
        if email:
            account = db.scalar(select(Account).where(
                func.lower(Account.email) == email.lower()).with_for_update())
    if account is None:
        legacy = db.scalar(select(Staff).where(
            Staff.username == identifier).with_for_update())
        if legacy is not None and legacy.account_id:
            account = db.scalar(select(Account).where(
                Account.id == legacy.account_id).with_for_update())
    if account is None:
        _dummy_verify(payload.password)
        raise DomainError('AUTH_INVALID_CREDENTIALS', '账号或密码不正确', 401)
    if not verify_password(account.password_hash or '', payload.password):
        raise DomainError('AUTH_INVALID_CREDENTIALS', '账号或密码不正确', 401)
    if account.deleted_at is not None or account.status == 'SUSPENDED':
        raise DomainError('AUTH_ACCOUNT_SUSPENDED', '该账号已停用', 403)
    if not account.verified_at or account.status != 'ACTIVE':
        raise DomainError('EMAIL_NOT_VERIFIED', '请先完成邮箱验证', 403)
    staff = _active_staff_for_account(db, account)
    if staff is None:
        raise DomainError('STAFF_ACCESS_DENIED', '该账号尚未获得员工权限', 403)
    current = _load_session(db, request, ACCOUNT_COOKIE, 'CUSTOMER')
    _issue_account_session(db, response, request, account, current)
    _clear_cookie(response, STAFF_COOKIE)
    audit(db, 'account:' + account.id, 'STAFF_LOGIN', 'staff', staff.id)
    return ok(_staff_session_view(staff))


@router.get('/auth/me', response_model=Envelope[StaffSessionView])
def staff_me(db=Depends(get_db), actor=Depends(actor_dep)):
    _require_staff_actor(actor)
    staff = db.get(Staff, actor.staff_id)
    if staff is None or not _staff_active(staff):
        raise DomainError('AUTH_REQUIRED', '员工权限已失效，请重新登录客户账号', 401)
    return ok(_staff_session_view(staff))


@router.post('/auth/logout', response_model=Envelope[RevokeResult])
def staff_logout(request: Request, response: Response, db=Depends(get_db), actor=Depends(actor_dep)):
    _require_staff_actor(actor)
    count = 0
    session = db.get(AuthSession, actor.account_session_id)
    if _session_active(session) and session is not None:
        _revoke_session(session)
        count = 1
    _clear_cookie(response, ACCOUNT_COOKIE)
    # Clear any leftover pre-unification cookie; it never granted authority here.
    _clear_cookie(response, STAFF_COOKIE)
    audit(db, actor, 'STAFF_LOGOUT', 'staff', actor.staff_id or '')
    return ok(RevokeResult(revoked=True, revoked_count=count, current_revoked=True))


@router.get('/auth/sessions', response_model=Envelope[SessionList])
def staff_sessions(db=Depends(get_db), actor=Depends(actor_dep)):
    require_staff(actor)
    return ok(_session_list(db, 'CUSTOMER', account_id=actor.account_id,
                            current_id=actor.account_session_id))


@router.post('/auth/sessions/revoke', response_model=Envelope[RevokeResult])
def staff_revoke_session(payload: SessionRevokeInput, response: Response, db=Depends(get_db),
                         actor=Depends(actor_dep)):
    require_staff(actor)
    current_id = actor.account_session_id
    if payload.session_id == 'ALL_OTHERS':
        count = _revoke_sessions(db, 'CUSTOMER', account_id=actor.account_id,
                                 except_id=current_id)
        audit(db, actor, 'STAFF_SESSIONS_REVOKED', 'staff', actor.staff_id or '',
              reason=payload.reason, data={'scope': 'ALL_OTHERS', 'revoked': count})
        return ok(RevokeResult(revoked=True, revoked_count=count))
    session = db.get(AuthSession, payload.session_id)
    if (session is None or session.kind != 'CUSTOMER' or not _session_active(session)
            or session.account_id != actor.account_id):
        raise DomainError('NOT_FOUND', '会话不存在或不可访问', 404)
    current = session.id == current_id
    _revoke_session(session)
    if current:
        _clear_cookie(response, ACCOUNT_COOKIE)
    audit(db, actor, 'STAFF_SESSION_REVOKED', 'auth_session', session.id, reason=payload.reason)
    return ok(RevokeResult(revoked=True, revoked_count=1, current_revoked=current))


def _staff_auth_retired() -> None:
    raise DomainError(
        'STAFF_AUTH_RETIRED',
        '员工身份已统一为客户账号；请在“我的-账号安全”中使用客户账号密码流程',
        410,
    )


@router.post('/auth/change-password', response_model=Envelope[PasswordChangedResult])
def staff_change_password(payload: ChangePasswordInput, db=Depends(get_db), actor=Depends(actor_dep)):
    _staff_auth_retired()


@router.post('/auth/setup-password', response_model=Envelope[PasswordChangedResult])
def staff_setup_password(payload: ResetPasswordInput, db=Depends(get_db)):
    _staff_auth_retired()


@router.post('/auth/reset-password', response_model=Envelope[PasswordChangedResult])
def staff_reset_password(payload: ResetPasswordInput, db=Depends(get_db)):
    _staff_auth_retired()


@router.post('/auth/forgot-password', response_model=Envelope[MailResult])
def staff_forgot_password(payload: EmailInput, request: Request, db=Depends(get_db)):
    _staff_auth_retired()


@router.post('/auth/email/request-verification', response_model=Envelope[MailResult])
def staff_request_email_verification(payload: EmailInput, db=Depends(get_db),
                                     actor=Depends(actor_dep)):
    _staff_auth_retired()


@router.post('/auth/email/verify', response_model=Envelope[StaffActionResult])
def staff_verify_email(payload: TokenInput, db=Depends(get_db)):
    _staff_auth_retired()


# ---------------------------------------------------------------------------
# Admin staff management
# ---------------------------------------------------------------------------


def _require_staff_manage(actor: Actor) -> None:
    require_admin(actor, 'staff.manage')


def _normalize_capabilities(capabilities: list[str] | None, role: str) -> list[str]:
    if role == 'COURIER':
        if capabilities:
            raise DomainError('COURIER_CAPABILITY_DENIED', '配送员不能获得管理能力', 422)
        return []
    if capabilities is None:
        return ['*']
    cleaned: list[str] = []
    for capability in capabilities:
        if capability == '*':
            cleaned.append('*')
            continue
        if not re.fullmatch(r'[a-z][a-z0-9]*(?:[.-][a-z0-9]+){0,4}', capability or ''):
            raise DomainError('CAPABILITY_INVALID', '包含未知的管理能力', 422, {'capability': capability})
        if capability not in cleaned:
            cleaned.append(capability)
    return cleaned


def _authorize_capability_grant(actor: Actor, capabilities: list[str]) -> None:
    owned = set(actor.capabilities or [])
    if '*' in owned:
        return
    for capability in capabilities:
        if capability == '*' or capability not in owned:
            raise DomainError('CAPABILITY_ESCALATION', '不能授予超出本人权限的管理能力', 403)


def _is_full_admin(staff: Staff) -> bool:
    return (staff.role == 'ADMIN' and staff.enabled and staff.status == 'ACTIVE'
            and not staff.must_change_password and '*' in set(staff.capabilities or []))


def _is_active_super_admin(db, staff: Staff | None) -> bool:
    if staff is None or not staff.is_super_admin:
        return False
    if staff.role != 'ADMIN' or not staff.enabled or staff.status != 'ACTIVE':
        return False
    account = db.get(Account, staff.account_id) if staff.account_id else None
    return _account_active(account)


def active_super_admin_count(db, *, exclude_staff_id: str | None = None) -> int:
    """Number of superadmins whose linked verified account is currently usable.

    Public helper for the account-deletion worker: a deletion request must be
    blocked while it is the last active superadmin, and the worker re-checks at
    execution time because rights may change after the request is filed.
    """
    count = 0
    for row in db.scalars(select(Staff).where(Staff.is_super_admin.is_(True))):
        if exclude_staff_id and row.id == exclude_staff_id:
            continue
        if _is_active_super_admin(db, row):
            count += 1
    return count


def last_super_admin_blocker(db, *, staff_id: str | None = None,
                             account_id: str | None = None) -> str | None:
    """Return a stable blocker code when removing this grant/account is unsafe."""
    staff = None
    if staff_id:
        staff = db.get(Staff, staff_id)
    elif account_id:
        staff = _linked_staff(db, account_id)
    if staff is None or not _is_active_super_admin(db, staff):
        return None
    if active_super_admin_count(db, exclude_staff_id=staff.id) == 0:
        return 'LAST_SUPER_ADMIN'
    return None


def _require_super_admin_actor(db, actor: Actor) -> None:
    staff = db.get(Staff, actor.staff_id) if actor.staff_id else None
    if not _is_active_super_admin(db, staff):
        raise DomainError('SUPER_ADMIN_REQUIRED', '只有现任超级管理员可以授予或撤销超级管理员', 403)


def _locked_super_admins(db) -> list[Staff]:
    return list(db.scalars(select(Staff).where(
        Staff.is_super_admin.is_(True)).order_by(Staff.id).with_for_update()))


def _demote_super_admins(db, *, keep_staff_id: str | None = None, actor=None,
                         reason: str = '') -> list[str]:
    """Atomically clear every true flag except ``keep_staff_id``.

    The partial unique index permits at most one true row.  Callers demote the
    old holder and flush before promoting a new holder, so a transfer never
    attempts to persist two true flags at once.
    """
    demoted: list[str] = []
    for row in _locked_super_admins(db):
        if keep_staff_id and row.id == keep_staff_id:
            continue
        if row.is_super_admin:
            row.is_super_admin = False
            row.session_version = (row.session_version or 1) + 1
            touch(row)
            demoted.append(row.id)
    if demoted:
        db.flush()
        audit(db, actor or 'system:superadmin-transfer', 'SUPER_ADMIN_TRANSFERRED_OUT',
              'staff', demoted[0], reason=reason,
              data={'demotedStaffIds': demoted, 'keptStaffId': keep_staff_id})
    return demoted


def _grantable_account(db, account_id: str) -> Account:
    account = db.scalar(select(Account).where(Account.id == account_id).with_for_update())
    if account is None:
        raise DomainError('ACCOUNT_NOT_FOUND', '客户账号不存在', 404)
    if account.deleted_at is not None or account.status == 'SUSPENDED':
        raise DomainError('ACCOUNT_NOT_ACTIVE', '该客户账号已停用或注销', 409)
    if not account.verified_at or account.status != 'ACTIVE':
        raise DomainError('ACCOUNT_NOT_VERIFIED', '只能授予已验证且正常使用的客户账号', 409)
    return account


def _staff_by_username(db, username: str, exclude_id: str | None = None):
    query = select(Staff).where(Staff.username == username)
    if exclude_id:
        query = query.where(Staff.id != exclude_id)
    return db.scalar(query)


def _staff_by_email(db, email: str, exclude_id: str | None = None):
    query = select(Staff).where(func.lower(Staff.email) == email.lower())
    if exclude_id:
        query = query.where(Staff.id != exclude_id)
    return db.scalar(query)


@router.get('/admin/staff/grantable-accounts',
            response_model=Envelope[GrantableAccountList])
def admin_staff_grantable_accounts(
    q: str = Query(default='', max_length=120),
    limit: int = Query(default=20, ge=1, le=20),
    db=Depends(get_db),
    actor=Depends(actor_dep),
):
    """Find an existing verified base account to grant employee rights to.

    This is deliberately narrower than the customer-admin search: it requires
    only ``staff.manage`` and returns just id/display name/masked email/verified.
    It never exposes contacts, principals, orders, passwords or full profiles.
    Accounts that already have a Staff row are excluded, so every returned id
    is grantable through ``POST /admin/staff`` with that exact ``accountId``.
    """
    _require_staff_manage(actor)
    term = q.strip()
    if not term:
        raise DomainError('SEARCH_QUERY_REQUIRED', '请输入至少 3 个字符的姓名或完整邮箱', 422)

    try:
        exact_email = normalize_email(term)
    except ValueError:
        exact_email = None
    if exact_email is None and len(term) < 3:
        raise DomainError('SEARCH_QUERY_TOO_SHORT',
                          '搜索关键词至少 3 个字符，或输入完整邮箱', 422)

    query = (
        select(Account)
        .outerjoin(Staff, Staff.account_id == Account.id)
        .where(
            Account.deleted_at.is_(None),
            Account.verified_at.is_not(None),
            Account.status == 'ACTIVE',
            Staff.id.is_(None),
        )
    )
    if exact_email is not None:
        query = query.where(func.lower(Account.email) == exact_email.lower())
    else:
        pattern = term.lower()
        query = query.where(or_(
            func.lower(func.coalesce(Account.display_name, '')).contains(
                pattern, autoescape=True),
            func.lower(Account.email).contains(pattern, autoescape=True),
        ))
    accounts = db.scalars(
        query.order_by(Account.display_name, Account.email, Account.id).limit(limit)
    ).all()
    return ok(GrantableAccountList(items=[
        GrantableAccountView(
            id=account.id,
            display_name=account.display_name or '',
            email_masked=mask_email(account.email) or '',
            verified=True,
        )
        for account in accounts
    ]))


@router.get('/admin/staff', response_model=Envelope[StaffList])
def admin_staff_list(db=Depends(get_db), actor=Depends(actor_dep)):
    _require_staff_manage(actor)
    return ok(_staff_list(db, actor))


@router.post('/admin/staff', response_model=Envelope[StaffList])
def admin_staff_create(payload: StaffCreateInput, db=Depends(get_db), actor=Depends(actor_dep)):
    """Grant employee rights to one existing verified customer account.

    FINAL N05: the only account selector is ``accountId``. Name/phone/email are
    display metadata and are never used for matching. No standalone staff row is
    created, and this route never issues a session.
    """
    _require_staff_manage(actor)
    if payload.password:
        raise DomainError('STAFF_PASSWORD_NOT_ALLOWED',
                          '员工统一使用客户账号密码，不再设置独立员工密码', 422)
    account = _grantable_account(db, payload.account_id)
    advisory_lock(db, 'staff-account:' + account.id)
    if _linked_staff(db, account.id) is not None:
        raise DomainError('STAFF_ACCOUNT_ALREADY_GRANTED', '该客户账号已经拥有员工权限', 409)
    if payload.email and normalize_email(payload.email).lower() != account.email.lower():
        raise DomainError('STAFF_EMAIL_MANAGED_BY_ACCOUNT',
                          '员工邮箱由客户账号统一管理，不能单独修改', 422)

    is_super_admin = bool(payload.is_super_admin)
    if is_super_admin:
        _require_super_admin_actor(db, actor)
    role = payload.role
    capabilities = _normalize_capabilities(payload.capabilities, role)
    if is_super_admin:
        capabilities = ['*']
    _authorize_capability_grant(actor, capabilities)

    username = payload.username.strip() if payload.username else f'staff-{account.id}'
    advisory_lock(db, 'staff-username:' + username)
    if _staff_by_username(db, username) is not None:
        raise DomainError('USERNAME_TAKEN', '该员工标识已存在，请更换用户名', 409)
    email = account.email
    if _staff_by_email(db, email) is not None:
        raise DomainError('STAFF_EMAIL_CONFLICT', '该账号邮箱已经关联其他员工记录', 409)

    enabled = payload.enabled
    status = payload.status
    if not enabled:
        status = 'DISABLED'
    if status == 'DISABLED':
        enabled = False
    if is_super_admin and (not enabled or status != 'ACTIVE'):
        raise DomainError('SUPER_ADMIN_MUST_BE_ACTIVE',
                          '超级管理员必须是已启用且状态正常的员工', 422)
    demoted_super_ids: list[str] = []
    if is_super_admin:
        advisory_lock(db, 'staff-super-admin-singleton')
        demoted_super_ids = _demote_super_admins(
            db, keep_staff_id=None, actor=actor, reason=payload.reason)
    name = payload.name or account.display_name or email.split('@')[0]
    staff = Staff(
        account_id=account.id,
        is_super_admin=is_super_admin,
        discount_level=payload.discount_level,
        username=username,
        name=name,
        phone=payload.phone,
        email=email,
        email_verified=True,
        role=role,
        enabled=enabled,
        status=status,
        capabilities=capabilities,
        must_change_password=False,
        password_hash='',
    )
    db.add(staff)
    db.flush()
    if is_super_admin and demoted_super_ids:
        audit(db, actor, 'SUPER_ADMIN_TRANSFERRED', 'staff', staff.id, reason=payload.reason,
              data={'fromStaffIds': demoted_super_ids, 'toStaffId': staff.id,
                    'accountId': account.id})
    audit(db, actor, 'STAFF_GRANTED', 'staff', staff.id, reason=payload.reason,
          data={'accountId': account.id, 'role': role, 'status': status,
                'isSuperAdmin': is_super_admin, 'discountLevel': staff.discount_level})
    return ok(_staff_list(db, actor))


@router.patch('/admin/staff/{staff_id}', response_model=Envelope[StaffList])
def admin_staff_update(staff_id: str, payload: StaffUpdateInput, db=Depends(get_db),
                       actor=Depends(actor_dep)):
    _require_staff_manage(actor)
    advisory_lock(db, 'staff-full-admin-guard')
    staff = lock(db, Staff, staff_id, payload.expected_version)
    acting_staff = db.get(Staff, actor.staff_id) if actor.staff_id else None
    actor_is_super = _is_active_super_admin(db, acting_staff)
    if payload.password:
        raise DomainError('STAFF_PASSWORD_NOT_ALLOWED',
                          '员工统一使用客户账号密码，不再设置独立员工密码', 422)
    if payload.account_id is not None and payload.account_id != staff.account_id:
        raise DomainError('ACCOUNT_SWAP_FORBIDDEN', '不能把员工权限转移到其他客户账号', 422)
    if staff.is_super_admin and not actor_is_super:
        raise DomainError('SUPER_ADMIN_PROTECTED', '只有现任超级管理员可以修改超级管理员', 403)
    if payload.is_super_admin is not None and payload.is_super_admin != staff.is_super_admin:
        _require_super_admin_actor(db, actor)

    account = db.get(Account, staff.account_id) if staff.account_id else None
    if payload.email is not None:
        email = normalize_email(payload.email) if payload.email else None
        if account is not None and (email or '').lower() != account.email.lower():
            raise DomainError('STAFF_EMAIL_MANAGED_BY_ACCOUNT',
                              '员工邮箱由客户账号统一管理，不能单独修改', 422)
        if account is None and email != (staff.email or None):
            if email:
                advisory_lock(db, 'staff-email:' + email)
            if email and _staff_by_email(db, email, exclude_id=staff.id) is not None:
                raise DomainError('STAFF_EMAIL_CONFLICT', '该员工邮箱已被使用', 409)
            staff.email = email
            staff.email_verified = False
            _invalidate_tokens(db, 'STAFF_EMAIL_VERIFY', staff_id=staff.id)
            _invalidate_tokens(db, 'STAFF_SETUP', staff_id=staff.id)

    if staff.id == actor.staff_id:
        if payload.enabled is False:
            raise DomainError('SELF_ACCOUNT_PROTECTION', '不能停用自己的账号，请由其他管理员操作', 403)
        if payload.role is not None and payload.role != staff.role:
            raise DomainError('SELF_ACCOUNT_PROTECTION', '不能修改自己的员工角色', 403)
        if payload.status is not None and payload.status != 'ACTIVE':
            raise DomainError('SELF_ACCOUNT_PROTECTION', '不能停用自己的账号，请由其他管理员操作', 403)
        if payload.capabilities is not None:
            raise DomainError('SELF_ACCOUNT_PROTECTION', '不能自行修改自己的管理权限', 403)
        if payload.is_super_admin is not None and payload.is_super_admin != staff.is_super_admin:
            raise DomainError('SELF_ACCOUNT_PROTECTION', '不能自行修改自己的超级管理员标记', 403)
        if 'discount_level' in payload.model_fields_set:
            raise DomainError('SELF_ACCOUNT_PROTECTION', '不能自行修改自己的员工折扣等级', 403)

    proposed_role = payload.role or staff.role
    proposed_enabled = staff.enabled if payload.enabled is None else payload.enabled
    proposed_status = payload.status or staff.status
    if payload.enabled is False:
        proposed_status = 'DISABLED'
    if proposed_status == 'DISABLED':
        proposed_enabled = False
    if payload.enabled is True and proposed_status == 'DISABLED':
        raise DomainError('STAFF_STATUS_INVALID', '启用账号时不能保持停用状态', 422)
    proposed_is_super = (staff.is_super_admin if payload.is_super_admin is None
                         else payload.is_super_admin)
    if staff.is_super_admin and not proposed_is_super:
        raise DomainError('LAST_SUPER_ADMIN_PROTECTED',
                          '超级管理员只能通过把标记转交给另一位管理员来交接，不能直接撤销', 409)
    if proposed_is_super and (account is None or not _account_active(account)):
        raise DomainError('SUPER_ADMIN_ACCOUNT_INACTIVE',
                          '超级管理员必须绑定当前正常使用的已验证客户账号', 409)
    if proposed_is_super and proposed_role != 'ADMIN':
        raise DomainError('SUPER_ADMIN_IS_ADMIN', '只有管理员可以成为超级管理员', 422)
    if proposed_role == 'COURIER':
        if payload.capabilities:
            raise DomainError('COURIER_CAPABILITY_DENIED', '配送员不能获得管理能力', 422)
        proposed_capabilities: list[str] = []
    elif proposed_is_super:
        proposed_capabilities = ['*']
    elif payload.capabilities is not None:
        proposed_capabilities = _normalize_capabilities(payload.capabilities, proposed_role)
    else:
        proposed_capabilities = list(staff.capabilities or [])
        if proposed_role == 'ADMIN' and payload.role == 'ADMIN' and not proposed_capabilities:
            proposed_capabilities = ['*']

    if proposed_capabilities != list(staff.capabilities or []):
        _authorize_capability_grant(actor, proposed_capabilities)
    was_active_super = _is_active_super_admin(db, staff)
    will_be_active_super = (proposed_is_super and proposed_role == 'ADMIN'
                            and proposed_enabled and proposed_status == 'ACTIVE'
                            and _account_active(account))
    if was_active_super and not will_be_active_super:
        if active_super_admin_count(db, exclude_staff_id=staff.id) == 0:
            raise DomainError('LAST_SUPER_ADMIN_PROTECTED',
                              '不能停用、降级、撤销或删除最后一个在职超级管理员', 409)
    was_full = _is_full_admin(staff)
    will_be_full = (proposed_role == 'ADMIN' and proposed_enabled and proposed_status == 'ACTIVE'
                    and '*' in set(proposed_capabilities))
    if was_full and not will_be_full:
        others = [row for row in db.scalars(select(Staff).where(Staff.id != staff.id))
                  if _is_full_admin(row)]
        if not others:
            raise DomainError('LAST_ADMIN_PROTECTED', '不能停用、降级或撤权最后一个完整管理员', 409)

    demoted_super_ids: list[str] = []
    if proposed_is_super and not staff.is_super_admin:
        if not proposed_enabled or proposed_status != 'ACTIVE':
            raise DomainError('SUPER_ADMIN_MUST_BE_ACTIVE',
                              '超级管理员必须是已启用且状态正常的员工', 422)
        advisory_lock(db, 'staff-super-admin-singleton')
        demoted_super_ids = _demote_super_admins(
            db, keep_staff_id=staff.id, actor=actor, reason=payload.reason)

    sensitive = False
    if payload.username is not None and payload.username.strip() != staff.username:
        username = payload.username.strip()
        advisory_lock(db, 'staff-username:' + username)
        if _staff_by_username(db, username, exclude_id=staff.id) is not None:
            raise DomainError('USERNAME_TAKEN', '该工作账号已存在', 409)
        staff.username = username
        sensitive = True
    if payload.name is not None:
        staff.name = payload.name
    if payload.phone is not None:
        staff.phone = payload.phone
    if payload.role is not None and payload.role != staff.role:
        staff.role = payload.role
        sensitive = True
    if proposed_enabled != staff.enabled:
        staff.enabled = proposed_enabled
        sensitive = True
    if proposed_status != staff.status:
        staff.status = proposed_status
        sensitive = True
    if proposed_capabilities != list(staff.capabilities or []):
        staff.capabilities = proposed_capabilities
        sensitive = True
    if proposed_is_super != bool(staff.is_super_admin):
        staff.is_super_admin = proposed_is_super
        sensitive = True
    if 'discount_level' in payload.model_fields_set:
        staff.discount_level = payload.discount_level
    if staff.must_change_password:
        staff.must_change_password = False
        sensitive = True
    if sensitive:
        staff.session_version = (staff.session_version or 1) + 1
        _revoke_sessions(db, 'STAFF', staff_id=staff.id)
        # Do not revoke the unified customer session: rights are re-derived from
        # this row on every request, so disabling or changing a grant is already
        # immediate while the ordinary customer login remains usable.
    touch(staff)
    audit(db, actor, 'STAFF_UPDATED', 'staff', staff.id, reason=payload.reason,
          data={'accountId': staff.account_id, 'role': staff.role,
                'enabled': staff.enabled, 'status': staff.status,
                'isSuperAdmin': bool(staff.is_super_admin),
                'discountLevel': staff.discount_level,
                'capabilities': list(staff.capabilities or [])})
    if demoted_super_ids:
        audit(db, actor, 'SUPER_ADMIN_TRANSFERRED', 'staff', staff.id, reason=payload.reason,
              data={'fromStaffIds': demoted_super_ids, 'toStaffId': staff.id,
                    'accountId': staff.account_id})
    return ok(_staff_list(db, actor))


@router.post('/admin/staff/{staff_id}/send-setup', response_model=Envelope[StaffActionResult])
def admin_staff_send_setup(staff_id: str, payload: ReasonInput, db=Depends(get_db),
                           actor=Depends(actor_dep)):
    """Send the unified customer-account password-reset flow, not a staff password."""
    _require_staff_manage(actor)
    _require_mail(db)
    staff = lock(db, Staff, staff_id, payload.expected_version)
    if not staff.enabled or staff.status == 'DISABLED':
        raise DomainError('AUTH_ACCOUNT_SUSPENDED', '该员工账号已停用', 403)
    account = db.get(Account, staff.account_id) if staff.account_id else None
    if account is None:
        raise DomainError('STAFF_LEGACY_MIGRATION_REQUIRED',
                          '该员工记录尚未绑定客户账号，请先完成迁移', 409)
    if not _account_active(account):
        raise DomainError('ACCOUNT_NOT_ACTIVE', '该客户账号已停用或未验证', 409)
    _invalidate_tokens(db, 'CUSTOMER_RESET', account_id=account.id)
    token, raw = _create_token(db, 'CUSTOMER_RESET', account_id=account.id, payload={})
    _enqueue_email(
        db,
        'CUSTOMER_RESET_PASSWORD',
        account.email,
        {
            'brandName': 'DormPrint',
            'actionUrl': _customer_action('/u/account/reset', raw),
            'expiresMinutes': int(SECURITY_TOKEN_TTL.total_seconds() // 60),
        },
        f'customer-reset:{token.id}',
    )
    audit(db, actor, 'STAFF_ACCOUNT_RESET_SENT', 'staff', staff.id, reason=payload.reason,
          data={'accountId': account.id})
    return ok(StaffActionResult(
        status='QUEUED', mail_status='QUEUED', retry_after_seconds=60,
        message='客户账号密码重置邮件已进入发送队列；排队不代表已经送达。'))


@router.post('/admin/staff/{staff_id}/issue-temporary-password',
             response_model=Envelope[StaffActionResult])
def admin_staff_temporary_password(staff_id: str, payload: ReasonInput, db=Depends(get_db),
                                   actor=Depends(actor_dep)):
    _require_staff_manage(actor)
    raise DomainError(
        'STAFF_AUTH_RETIRED',
        '员工统一使用客户账号密码，不再签发独立临时密码；请发送客户账号密码重置邮件',
        410,
    )


@router.post('/admin/staff/{staff_id}/revoke-sessions', response_model=Envelope[StaffActionResult])
def admin_staff_revoke_sessions(staff_id: str, payload: ReasonInput, db=Depends(get_db),
                                actor=Depends(actor_dep)):
    _require_staff_manage(actor)
    staff = lock(db, Staff, staff_id, payload.expected_version)
    acting_staff = db.get(Staff, actor.staff_id) if actor.staff_id else None
    if staff.is_super_admin and not _is_active_super_admin(db, acting_staff):
        raise DomainError('SUPER_ADMIN_PROTECTED', '只有现任超级管理员可以撤销超级管理员会话', 403)
    count = _revoke_sessions(db, 'STAFF', staff_id=staff.id)
    if staff.account_id:
        count += _revoke_sessions(db, 'CUSTOMER', account_id=staff.account_id)
    staff.session_version = (staff.session_version or 1) + 1
    touch(staff)
    audit(db, actor, 'STAFF_SESSIONS_REVOKED_BY_ADMIN', 'staff', staff.id,
          reason=payload.reason, data={'revoked': count, 'accountId': staff.account_id})
    return ok(StaffActionResult(status='REVOKED', revoked=True,
                                message=f'已撤销 {count} 个统一账号会话'))


# ---------------------------------------------------------------------------
# Exported bootstrap helpers
# ---------------------------------------------------------------------------


def create_initial_admin(db, username: str, password: str, name: str = '管理员',
                         email: str | None = None) -> Staff:
    """Create/bind the explicit superadmin bootstrap account.

    FINAL N05 requires the initial administrator to be a normal verified customer
    account with an explicit ``is_super_admin`` staff grant. The operator must
    pass the account email; an existing account is reused only when the supplied
    password matches it, otherwise the caller is expected to create the account
    through the normal verified registration flow first. No staff password and no
    implicit account created from an unverified email are supported.

    ``email`` is optional in the signature for internal/legacy compatibility
    (callers may pass it positionally or as a keyword); omitting it raises
    ``INITIAL_ADMIN_EMAIL_REQUIRED`` rather than creating a privileged standalone
    staff row. The production CLI always passes it.
    """
    username = (username or '').strip()
    if len(username) < 1 or len(username) > 120:
        raise DomainError('USERNAME_INVALID', '工作账号长度需为 3-120 位', 422)
    _validate_password(password)
    if not email:
        raise DomainError(
            'INITIAL_ADMIN_EMAIL_REQUIRED',
            '生产初始化必须提供客户账号邮箱，由该账号持有超级管理员权限',
            422,
        )
    email = normalize_email(email)
    advisory_lock(db, 'customer-register:' + email)
    advisory_lock(db, 'staff-username:' + username)
    advisory_lock(db, 'staff-super-admin-singleton')
    if db.scalar(select(Staff.id).where(Staff.is_super_admin.is_(True)).limit(1)):
        raise DomainError(
            'SUPER_ADMIN_ALREADY_INITIALIZED',
            '系统已经有超级管理员；超级管理员只能通过现任超级管理员转交，不能重复初始化',
            409,
        )
    if _staff_by_username(db, username) is not None:
        raise DomainError('USERNAME_TAKEN', '该工作账号已存在', 409)
    account = db.scalar(select(Account).where(
        func.lower(Account.email) == email.lower()).with_for_update())
    if account is not None:
        if account.deleted_at is not None or not account.verified_at or account.status != 'ACTIVE':
            raise DomainError(
                'INITIAL_ADMIN_ACCOUNT_NOT_VERIFIED',
                '初始管理员必须绑定已验证且正常使用的客户账号；请先完成正常注册验证',
                409,
            )
        if not verify_password(account.password_hash or '', password):
            raise DomainError(
                'INITIAL_ADMIN_CREDENTIAL_MISMATCH',
                '该客户账号已存在，但密码不匹配；请使用该账号密码或先走客户账号找回流程',
                409,
            )
        if _linked_staff(db, account.id) is not None:
            raise DomainError('STAFF_ACCOUNT_ALREADY_GRANTED',
                              '该客户账号已经拥有员工记录，请使用员工管理授权', 409)
    else:
        principal = Principal()
        db.add(principal)
        db.flush()
        account = Account(
            principal_id=principal.id,
            email=email,
            password_hash=hash_password(password),
            status='ACTIVE',
            verified_at=_now(),
            display_name=name or '管理员',
        )
        db.add(account)
        db.flush()
        principal.account_id = account.id
    staff = Staff(
        account_id=account.id,
        is_super_admin=True,
        discount_level=None,
        username=username,
        name=name or account.display_name or '管理员',
        phone='',
        email=account.email,
        email_verified=True,
        role='ADMIN',
        enabled=True,
        status='ACTIVE',
        capabilities=['*'],
        must_change_password=False,
        password_hash='',
    )
    db.add(staff)
    db.flush()
    audit(db, 'system', 'INITIAL_ADMIN_CREATED', 'staff', staff.id,
          data={'accountId': account.id, 'isSuperAdmin': True})
    return staff
