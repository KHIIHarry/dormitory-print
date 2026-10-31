"""Self-service account lifecycle: recovery window and retention-safe finalization.

The normal customer account is the only credential.  A self-service deletion is
not an administrator review: once the account has no unfinished financial or
order duties it immediately enters a seven-day recovery window, normal sessions
are revoked, and only a correct password login can restore it.  After the
deadline the existing maintenance retention/ledger path performs the final
anonymisation and a TrashEntry keeps the account restorable for the configured
retention period.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from .core import DomainError, audit, enqueue_job, touch, advisory_lock
from .models import (
    AccessGrant,
    Account,
    DeletionRequest,
    Order,
    Principal,
    Staff,
    TrashEntry,
    utcnow,
)

logger = logging.getLogger('dormprint.account_lifecycle')

RECOVERY_DAYS = 7
RECOVERY_DELTA = timedelta(days=RECOVERY_DAYS)
UNVERIFIED_ACCOUNT_TTL = timedelta(hours=24)
STATUS_BLOCKED = 'BLOCKED'
STATUS_RECOVERY = 'RECOVERY'
STATUS_RECOVERED = 'RECOVERED'
STATUS_FINALIZED = 'FINALIZED'
ACCOUNT_PENDING_DELETION = 'DELETION_PENDING'
TERMINAL_ORDER_STATUSES = {
    'DELIVERED', 'COMPLETED', 'CLOSED', 'CANCELLED', 'CANCELED', 'REFUNDED',
}


def _now() -> datetime:
    return utcnow()


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _parse_datetime(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _aware(value)
    try:
        return _aware(datetime.fromisoformat(str(value)))
    except ValueError:
        return None


def recovery_deadline(request: DeletionRequest) -> datetime | None:
    data = request.data or {}
    return _parse_datetime(data.get('recoveryUntil'))


def verification_deadline(account: Account) -> datetime | None:
    """Return the only deadline that can auto-deregister an unverified account.

    A verified, suspended or already-deleted account never gets a deadline.
    The timestamp is based on the original registration, not on a resend or an
    email change, so repeatedly requesting a new code cannot extend it.
    """
    if (account.verified_at is not None or account.deleted_at is not None
            or account.status not in ('PENDING_EMAIL', 'PENDING_VERIFICATION')):
        return None
    created = _aware(account.created_at)
    return created + UNVERIFIED_ACCOUNT_TTL if created is not None else None


def _principal_ids(db, account: Account) -> set[str]:
    ids = {account.principal_id}
    ids.update(db.scalars(select(Principal.id).where(Principal.account_id == account.id)))
    return {value for value in ids if value}


def _imported_order_ids(db, account: Account) -> set[str]:
    return set(db.scalars(select(AccessGrant.order_id).where(
        AccessGrant.account_id == account.id,
        AccessGrant.source_grant_id.is_not(None),
        AccessGrant.revoked_at.is_(None),
        AccessGrant.expires_at > _now())))


def deletion_blockers(db, account: Account) -> list[str]:
    """Return blockers for *this* account, with account-active super semantics.

    The maintenance helper is the canonical financial/security checklist.  It
    predates unified accounts, so its two staff-only blockers are replaced by
    the auth module's account-aware last-superadmin helper.  Orders and
    after-sales are additionally locked while they are inspected.
    """
    from .maintenance import _account_deletion_blockers

    raw = [value for value in _account_deletion_blockers(db, account)
           if not value.startswith('STAFF_ACTIVE:')
           and not value.startswith('LAST_SUPER_ADMIN')]
    blockers: list[str] = []

    from .auth import last_super_admin_blocker
    if last_super_admin_blocker(db, account_id=account.id):
        blockers.append('LAST_SUPER_ADMIN')

    principal_ids = _principal_ids(db, account)
    imported_ids = _imported_order_ids(db, account)
    order_filter = [Order.checkout_account_id == account.id]
    if principal_ids:
        order_filter.append(Order.principal_id.in_(principal_ids))
    if imported_ids:
        order_filter.append(Order.id.in_(imported_ids))
    # Lock active candidate orders so a concurrent order mutation cannot make
    # the request observe a terminal order while a non-terminal row is created
    # in the same account.
    active_order_ids = list(db.scalars(select(Order.id).where(
        or_(*order_filter), Order.deleted_at.is_(None),
        Order.status.notin_(TERMINAL_ORDER_STATUSES)).order_by(Order.id).with_for_update()))
    for order_id in active_order_ids:
        blockers.append(f'ORDER_ACTIVE:{order_id}')

    blockers.extend(raw)
    return list(dict.fromkeys(blockers))


def _request_view(request: DeletionRequest, blockers: list[str]) -> dict:
    data = request.data or {}
    deadline = recovery_deadline(request)
    if request.status == STATUS_RECOVERY:
        message = ('注销申请已进入 7 天恢复期；此期间使用账号密码登录可恢复账号。'
                   '期满后将按数据保留与账务规则完成注销，不会立即不可逆删除。')
    elif request.status == STATUS_BLOCKED:
        message = '账号仍有未完成的订单、售后、退款或账务事项，暂时不能注销；请处理后再试。'
    elif request.status == STATUS_RECOVERED:
        message = '账号已恢复。'
    else:
        message = '注销申请已受理。'
    return {
        'id': request.id,
        'status': request.status,
        'requestedAt': data.get('requestedAt') or request.created_at.isoformat(),
        'blockers': blockers,
        'message': message,
        'recoveryUntil': deadline,
        'canRecover': request.status == STATUS_RECOVERY and deadline is not None and deadline > _now(),
        'recovered': request.status == STATUS_RECOVERED,
    }


def request_self_deletion(db, account: Account, *, reason: str, actor) -> dict:
    """Start or refresh a self-service deletion; no administrator approval."""
    advisory_lock(db, 'customer-deletion:' + account.id)
    account = db.scalar(select(Account).where(Account.id == account.id).with_for_update())
    if account is None or account.deleted_at is not None:
        raise DomainError('AUTH_REQUIRED', '请先登录已验证账号', 401)
    existing = db.scalar(select(DeletionRequest).where(
        DeletionRequest.account_id == account.id,
        DeletionRequest.status.in_(('REQUESTED', STATUS_BLOCKED, STATUS_RECOVERY)),
    ).order_by(DeletionRequest.created_at.desc(), DeletionRequest.id.desc()).with_for_update())

    blockers = deletion_blockers(db, account)
    if blockers:
        now = _now()
        if existing is None:
            existing = DeletionRequest(account_id=account.id)
            db.add(existing)
        # An existing RECOVERY window is never downgraded by a stale retry.
        if existing.status != STATUS_RECOVERY:
            existing.status = STATUS_BLOCKED
            data = dict(existing.data or {})
            data.update(reason=reason, requestedAt=data.get('requestedAt') or now.isoformat(),
                        blockers=blockers, blockedAt=now.isoformat())
            existing.data = data
            touch(existing)
            audit(db, actor, 'CUSTOMER_DELETION_BLOCKED', 'deletion_request', existing.id,
                  reason=reason, data={'blockers': blockers})
        db.flush()
        return _request_view(existing, blockers)

    now = _now()
    deadline = now + RECOVERY_DELTA
    if existing is None:
        existing = DeletionRequest(account_id=account.id)
        db.add(existing)
    if existing.status == STATUS_RECOVERY:
        # Already pending; return the same window (idempotent request retry).
        return _request_view(existing, [])
    existing.status = STATUS_RECOVERY
    data = dict(existing.data or {})
    data.update(reason=reason, requestedAt=data.get('requestedAt') or now.isoformat(),
                recoveryUntil=deadline.isoformat(), blockers=[])
    existing.data = data
    touch(existing)
    account.status = ACCOUNT_PENDING_DELETION
    account.deleted_at = now
    account.session_version = (account.session_version or 1) + 1
    touch(account)
    from .maintenance import _revoke_account_credentials
    counts = _revoke_account_credentials(db, account, now)
    data.update(revokedSessions=counts['sessions'], revokedGrants=counts['grants'],
                destroyedTokens=counts['tokens'], deletedAt=now.isoformat())
    existing.data = data
    db.flush()
    enqueue_job(db, 'ACCOUNT_DELETION_FINALIZE', existing.id,
                f'account-deletion:{existing.id}',
                {'availableAt': deadline.isoformat(), 'accountId': account.id,
                 'requestId': existing.id})
    db.flush()
    audit(db, actor, 'CUSTOMER_DELETION_RECOVERY_STARTED', 'deletion_request', existing.id,
          reason=reason, data={'accountId': account.id, 'recoveryUntil': deadline.isoformat(),
                               'revokedSessions': counts['sessions']})
    return _request_view(existing, [])


def recover_account(db, account: Account, *, now: datetime | None = None) -> DeletionRequest:
    """Restore a pending account from a correct-password login within the window."""
    now = now or _now()
    request = db.scalar(select(DeletionRequest).where(
        DeletionRequest.account_id == account.id,
        DeletionRequest.status == STATUS_RECOVERY,
    ).order_by(DeletionRequest.created_at.desc(), DeletionRequest.id.desc()).with_for_update())
    if request is None:
        raise DomainError('AUTH_ACCOUNT_SUSPENDED', '该账号当前不可用', 403)
    deadline = recovery_deadline(request) or (_aware(request.created_at) + RECOVERY_DELTA)
    if now > deadline:
        raise DomainError(
            'RECOVERY_WINDOW_EXPIRED',
            '注销恢复期已结束，账号不能通过登录恢复；注销将按数据保留规则完成', 410,
            {'recoveryUntil': deadline.isoformat()})
    account.status = 'ACTIVE'
    account.deleted_at = None
    account.session_version = (account.session_version or 1) + 1
    touch(account)
    request.status = STATUS_RECOVERED
    data = dict(request.data or {})
    data['recoveredAt'] = now.isoformat()
    request.data = data
    touch(request)
    from .maintenance import _revoke_account_credentials
    _revoke_account_credentials(db, account, now)
    db.flush()
    audit(db, 'account:' + account.id, 'CUSTOMER_DELETION_RECOVERED', 'account', account.id,
          data={'deletionRequestId': request.id, 'recoveryUntil': deadline.isoformat()})
    return request


def finalize_deletion_request(db, request_id: str, *, now: datetime | None = None) -> str:
    """Finalize one due recovery request using the existing retention path.

    Lock order is account then request, matching login/recovery and request
    creation.  A concurrent recovery cannot deadlock the worker, and the
    request status is re-checked after both locks are held.
    """
    now = now or _now()
    request = db.scalar(select(DeletionRequest).where(DeletionRequest.id == request_id))
    if request is None:
        return 'SKIPPED'
    account = db.scalar(select(Account).where(
        Account.id == request.account_id).with_for_update())
    request = db.scalar(select(DeletionRequest).where(
        DeletionRequest.id == request_id).with_for_update())
    if request is None:
        return 'SKIPPED'
    if request.status == STATUS_FINALIZED:
        return 'ALREADY_FINALIZED'
    if request.status != STATUS_RECOVERY:
        return 'SKIPPED'
    deadline = recovery_deadline(request) or (_aware(request.created_at) + RECOVERY_DELTA)
    if now < deadline:
        return 'NOT_DUE'
    if account is None:
        request.status = 'CANCELLED'
        touch(request)
        return 'CANCELLED'
    blockers = deletion_blockers(db, account)
    if account.deleted_at is None and account.status != ACCOUNT_PENDING_DELETION:
        blockers.append('ACCOUNT_NOT_PENDING_DELETION')
    if blockers:
        data = dict(request.data or {})
        data['finalizeBlockers'] = blockers
        data['finalizeAttemptAt'] = now.isoformat()
        request.data = data
        touch(request)
        return 'BLOCKED'

    from .maintenance import (
        _anonymise_account,
        _anonymise_linked_staff,
        _disable_linked_staff,
        _linked_staff,
        _revoke_account_credentials,
        _trash_hours,
        storage_gc_guard,
    )
    existing = db.scalar(select(TrashEntry).where(
        TrashEntry.target_type == 'ACCOUNT', TrashEntry.target_id == account.id,
        TrashEntry.status.in_(('DELETED', 'PURGE_PENDING'))))
    name = ''
    try:
        from .auth_schemas import mask_email
        name = mask_email(account.email)
    except Exception:
        name = ''
    linked = _linked_staff(db, account)
    with storage_gc_guard(db):
        counts = _revoke_account_credentials(db, account, now)
    disabled_staff_id = _disable_linked_staff(db, account, now)
    anonymised_staff_id = _anonymise_linked_staff(db, linked)
    _anonymise_account(db, account, now)
    request.status = STATUS_FINALIZED
    data = dict(request.data or {})
    data.update(finalizedAt=now.isoformat(), blockers=[], finalizeBlockers=[],
                revokedSessions=counts['sessions'], revokedGrants=counts['grants'],
                destroyedTokens=counts['tokens'],
                disabledStaffId=disabled_staff_id, anonymisedStaffId=anonymised_staff_id)
    request.data = data
    touch(request)
    entry = existing
    if entry is None:
        entry = TrashEntry(
            target_type='ACCOUNT', target_id=account.id, actor_id='system:account-lifecycle',
            reason=(request.data or {}).get('reason') or '用户自助注销期满', status='DELETED',
            purge_at=now + timedelta(hours=_trash_hours(db)),
            data={'name': name, 'actorId': 'system:account-lifecycle',
                  'deletionRequestId': request.id, 'restorable': False,
                  'deletedAt': now.isoformat(), 'selfService': True},
        )
        db.add(entry)
        db.flush()
    data['trashId'] = entry.id
    request.data = data
    db.flush()
    audit(db, 'system:account-lifecycle', 'ACCOUNT_DELETION_FINALIZED', 'ACCOUNT', account.id,
          data={'deletionRequestId': request.id, 'trashId': entry.id,
                'revokedSessions': counts['sessions'], 'revokedGrants': counts['grants'],
                'destroyedTokens': counts['tokens'], 'disabledStaffId': disabled_staff_id,
                'anonymisedStaffId': anonymised_staff_id})
    return STATUS_FINALIZED


def run_due_deletions(db, *, now: datetime | None = None) -> dict:
    now = now or _now()
    ids = list(db.scalars(select(DeletionRequest.id).where(
        DeletionRequest.status == STATUS_RECOVERY).order_by(DeletionRequest.id)))
    result = {'processed': 0, 'finalized': 0, 'pending': 0, 'blocked': 0, 'skipped': 0}
    for request_id in ids:
        try:
            with db.begin_nested():
                outcome = finalize_deletion_request(db, request_id, now=now)
                db.flush()
        except Exception:
            logger.exception('account_deletion_finalize_failed request_id=%s', request_id)
            result['pending'] += 1
            continue
        result['processed'] += 1
        if outcome in (STATUS_FINALIZED, 'ALREADY_FINALIZED'):
            result['finalized'] += 1
        elif outcome == 'NOT_DUE':
            result['pending'] += 1
        elif outcome == 'BLOCKED':
            result['blocked'] += 1
        else:
            result['skipped'] += 1
    return result
