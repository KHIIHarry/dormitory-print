"""Private asset storage, resumable uploads, final PDFs and prepared drafts.

The module owns the asset/preparation lifecycle. Route handlers only validate
input, enforce object-level authorization and write outbox rows; every byte of
real conversion happens in ``process_asset`` / ``process_preparation`` which a
leased worker calls. No handler fabricates READY, page counts, prices or
progress.

Storage is content-addressed and immutable; a worker may run twice, lose its
lease or be cancelled, but it can only publish a result after re-locking the
row and proving its attempt token is still current.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import secrets
import shutil
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import and_, func, or_, select

from . import pdf_engine
from .config import get_config
from .core import (
    Actor,
    DomainError,
    Envelope,
    actor_dep,
    audit,
    authorize_order,
    canonical_hash,
    enqueue_job,
    grant_scopes,
    idempotent_call,
    lock,
    ok,
    owns_principal,
    require_admin,
    require_staff,
    touch,
)
from .db import get_db
from .models import (
    AccessGrant,
    Account,
    Artifact,
    Asset,
    ConfigSection,
    DeliveryAssignment,
    DeliveryProof,
    Order,
    OrderFile,
    Preparation,
    Principal,
    PrintTask,
    Quote,
    UploadSession,
    new_id,
    utcnow,
)
from .growth_schemas import QuoteDTO, RequoteInput
from .media_schemas import (
    AssetList,
    AssetUploadResult,
    AssetView,
    ConfirmCheckoutInput,
    PreparationInput,
    PreparationList,
    PreparationView,
    UploadSessionComplete,
    UploadSessionCreate,
    UploadSessionView,
)
from .settings import get_settings

router = APIRouter()

BYTES_PER_MB = 1024 * 1024
DEFAULT_CHUNK_BYTES = 8 * BYTES_PER_MB
MAX_CHUNK_BYTES = 32 * BYTES_PER_MB
PUBLIC_PURPOSES = frozenset({'PUBLIC_WECHAT_QR', 'PUBLIC_MAP'})
CUSTOMER_PURPOSES = frozenset({'PRINT_SOURCE'})
STAFF_PURPOSES = frozenset({'DELIVERY_PROOF'})
INTERNAL_PURPOSES = frozenset({'PRINT_NORMALIZED', 'PRINT_FINAL', 'PRINT_REPRINT'})
ALL_PURPOSES = PUBLIC_PURPOSES | CUSTOMER_PURPOSES | STAFF_PURPOSES | INTERNAL_PURPOSES
# Explicit ADMIN capability scopes. There is deliberately no mapping for a
# generic staff/admin bypass: a missing mapping means an ADMIN gets no private
# object access at all.
PRINT_PURPOSES = CUSTOMER_PURPOSES | INTERNAL_PURPOSES
SOURCE_PURPOSES = frozenset({'PRINT_SOURCE', 'PRINT_NORMALIZED', 'SOURCE'})
FINAL_PURPOSES = frozenset({'PRINT_FINAL', 'PRINT_REPRINT', 'FINAL', 'FINAL_PDF'})
ADMIN_PURPOSE_CAPABILITIES = {
    **{purpose: 'production.manage' for purpose in PRINT_PURPOSES},
    'FINAL_PDF': 'production.manage',
    'DELIVERY_PROOF': 'delivery.manage',
    **{purpose: 'config.manage' for purpose in PUBLIC_PURPOSES},
}
DOCUMENT_KINDS = frozenset({'PDF', 'DOC', 'DOCX', 'PPT', 'PPTX', 'XLS', 'XLSX'})
IMAGE_KINDS = frozenset({'JPEG', 'PNG', 'WEBP', 'HEIC'})
KIND_EXTENSIONS = {
    'PDF': {'pdf'}, 'DOC': {'doc'}, 'DOCX': {'docx'}, 'PPT': {'ppt'}, 'PPTX': {'pptx'},
    'XLS': {'xls'}, 'XLSX': {'xlsx'}, 'JPEG': {'jpg', 'jpeg'}, 'PNG': {'png'},
    'WEBP': {'webp'}, 'HEIC': {'heic', 'heif'},
}
FAILURE_MESSAGES = {
    'CANCELED': '任务已取消',
    'EXPIRED': '文件已过期，请重新上传',
    'SOURCE_CORRUPT': '原文件校验失败，请重新上传',
    'OFFICE_CONVERTER_MISSING': '服务器暂未提供 Office 转换能力',
    'PRICE_NOT_CONFIGURED': '当前服务价格尚未配置，暂时无法报价',
}


# ---------------------------------------------------------------------------
# Storage and configuration helpers
# ---------------------------------------------------------------------------


def _root() -> Path:
    root = Path(get_settings().storage_root).expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ('assets', 'normalized', 'artifacts', 'previews', 'sessions', 'tmp'):
        (root / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(root, 0o700)
    except OSError:  # pragma: no cover - deployment volume may enforce its own mode
        pass
    return root


def _storage_path(key: str) -> Path:
    root = _root()
    candidate = (root / key).resolve()
    if candidate != root and root not in candidate.parents:
        raise DomainError('STORAGE_KEY_INVALID', '文件存储路径无效', 500)
    return candidate


# Every Asset row owns a distinct blob path. Content hashes are verified on
# publish, but blobs are never shared between rows: the shared maintenance
# module purges a single row's storage_key and must not delete another owner's
# file.
def _source_key(asset_id: str, sha: str) -> str:
    return f'assets/{asset_id}/{sha}'


def _normalized_key(asset_id: str, sha: str) -> str:
    return f'normalized/{asset_id}/{sha}.pdf'


def _artifact_key(asset_id: str, sha: str) -> str:
    return f'artifacts/{asset_id}/{sha}.pdf'


def _preview_key(asset_id: str, sha: str) -> str:
    return f'previews/{asset_id}/{sha}'


def _config(db, section: str) -> dict:
    return dict(get_config(db, section).data or {})


def _services(db) -> dict:
    section = get_config(db, 'services')
    data = dict(section.data or {})
    data['_version'] = section.version
    return data


def _require_file_processing_open(db) -> dict:
    services = _services(db)
    workflow = _config(db, 'workflow')
    pauses = workflow.get('pauses') or {}
    if not services.get('serviceEnabled'):
        raise DomainError('FILE_PROCESSING_PAUSED', '文件处理服务已暂停，请稍后再试', 503)
    if pauses.get('fileProcessing'):
        raise DomainError('FILE_PROCESSING_PAUSED', '管理员已暂停文件处理，请稍后再试', 503)
    return services


def _max_file_bytes(services: dict) -> int:
    value = services.get('maxMb')
    if not isinstance(value, int) or value <= 0:
        raise DomainError('SERVICE_LIMIT_UNAVAILABLE', '服务器尚未配置上传大小限制', 503)
    return value * BYTES_PER_MB


def _max_upload_bytes(services: dict) -> int:
    # A single multipart request additionally respects the reverse-proxy/API cap.
    return min(_max_file_bytes(services), get_settings().max_request_bytes)


def _expiry_for(purpose: str, db) -> datetime | None:
    retention = _config(db, 'retention')
    if purpose in PUBLIC_PURPOSES:
        return None
    days = retention.get('proofDays') if purpose == 'DELIVERY_PROOF' else (
        retention.get('sourceDays') if purpose == 'PRINT_SOURCE' else retention.get('finalDays')
    )
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 7 if purpose == 'PRINT_SOURCE' else 30
    return utcnow() + timedelta(days=max(1, days))


def _draft_expiry(db) -> datetime:
    hours = _config(db, 'retention').get('draftHours')
    try:
        hours = int(hours)
    except (TypeError, ValueError):
        hours = 24
    return utcnow() + timedelta(hours=max(1, min(hours, 720)))


def _expansion_limit(services: dict) -> int:
    try:
        return int(services.get('maxExpandedPages') or 10_000)
    except (TypeError, ValueError):
        return 10_000


def _max_copies(services: dict) -> int:
    try:
        return int(services.get('maxCopies') or 999)
    except (TypeError, ValueError):
        return 999


def _max_pages(services: dict) -> int:
    try:
        return int(services.get('maxPages') or 1000)
    except (TypeError, ValueError):
        return 1000


def _format_allowed(services: dict, kind: str) -> None:
    raw = services.get('formats')
    if raw is None:
        return
    allowed = {str(item).lower().lstrip('.') for item in raw}
    if not KIND_EXTENSIONS.get(kind, set()) & allowed:
        raise DomainError('FORMAT_DISABLED', '该文件格式未开放上传', 415, {'kind': kind})


def _kind_from_filename(name: str) -> str | None:
    suffix = Path(name or '').suffix.lower().lstrip('.')
    for kind, extensions in KIND_EXTENSIONS.items():
        if suffix in extensions:
            return kind
    return None


def _engine_error(error: pdf_engine.EngineError) -> DomainError:
    status = 422
    if error.code in ('UNSUPPORTED_FORMAT', 'MIME_MISMATCH', 'FORMAT_DISABLED'):
        status = 415
    elif error.code in ('UPLOAD_TOO_LARGE', 'IMAGE_TOO_LARGE'):
        status = 413
    elif error.code in ('OFFICE_CONVERTER_MISSING', 'CONVERTER_TIMEOUT', 'OFFICE_CONVERT_FAILED', 'GRAYSCALE_CONVERSION_FAILED'):
        status = 503
    elif error.code in ('ARTIFACT_VERIFY_FAILED',):
        status = 500
    return DomainError(error.code, error.message, status, error.details or None)


def _write_image_preview(source: Path, workdir: Path, kind: str | None = None) -> tuple[Path, str, int, int]:
    """Create a browser-displayable, EXIF-corrected preview of a READY image.

    Browsers cannot reliably decode HEIC (and draft recovery must not depend on
    the original local File), so every image asset gets a real normalized
    server-side derivative.
    """
    from PIL import Image, ImageOps

    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened)
        has_alpha = image.mode in ('RGBA', 'LA') or (
            image.mode == 'P' and 'transparency' in image.info
        )
        # HEIC must surface as a real PNG for the customer UI; browsers do not
        # decode HEIC and the client explicitly asks for a PNG preview.
        if has_alpha or kind == 'HEIC':
            image = image.convert('RGBA' if has_alpha else 'RGB')
            mime, suffix = 'image/png', '.png'
        else:
            image = image.convert('RGB')
            mime, suffix = 'image/jpeg', '.jpg'
        image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
        target = workdir / f'normalized-preview{suffix}'
        if mime == 'image/png':
            image.save(target, format='PNG', optimize=True)
        else:
            image.save(target, format='JPEG', quality=92, optimize=True)
        width, height = image.size
        image.close()
    return target, mime, width, height


def _clean_name(name: str, fallback: str = 'file') -> str:
    value = Path(name or fallback).name
    value = ''.join(character for character in value if ord(character) >= 32).strip()
    if not value:
        value = fallback
    return value[:255]


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _is_expired(value: datetime | None) -> bool:
    if value is None:
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value <= utcnow()


def _principal_key(actor: Actor) -> str | None:
    return actor.principal_id or actor.guest_principal_id


def _require_customer_principal(actor: Actor) -> str:
    principal = _principal_key(actor)
    if not principal:
        raise DomainError('AUTH_REQUIRED', '请先建立客户会话', 401)
    return principal


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


def _asset_kind(asset: Asset) -> str | None:
    data = asset.data or {}
    return data.get('kind')


def _asset_view(asset: Asset, *, include_url: bool = True) -> dict:
    data = asset.data or {}
    ready = asset.status == 'READY'
    return {
        'id': asset.id,
        'assetId': asset.id,
        'status': asset.status,
        'purpose': asset.purpose,
        'name': asset.name,
        'mime': asset.mime,
        'size': asset.size,
        'sha256': asset.sha256,
        'pages': asset.pages,
        'kind': data.get('kind'),
        'error': data.get('failureMessage'),
        'failureCode': data.get('failureCode'),
        'retryable': bool(data.get('retryable')),
        'dimensions': data.get('image'),
        'url': f'/api/v1/assets/{asset.id}/content' if include_url and ready else None,
        'normalizedUrl': (
            f'/api/v1/assets/{asset.normalized_asset_id}/content'
            if asset.normalized_asset_id and ready else None
        ),
        'previewUrl': (
            f'/api/v1/assets/{asset.normalized_asset_id}/content'
            if asset.normalized_asset_id and ready else None
        ),
        'cancelRequested': bool(data.get('cancelRequested')),
        'normalizedAssetId': asset.normalized_asset_id,
        'createdAt': asset.created_at,
        'expiresAt': asset.expires_at,
    }


def _range_label(pages: Iterable[int]) -> str:
    ordered = sorted(set(int(page) for page in pages))
    if not ordered:
        return ''
    parts: list[str] = []
    start = previous = ordered[0]
    for page in ordered[1:]:
        if page == previous + 1:
            previous = page
            continue
        parts.append(str(start) if start == previous else f'{start}-{previous}')
        start = previous = page
    parts.append(str(start) if start == previous else f'{start}-{previous}')
    return ','.join(parts)


def artifact_view(artifact: Artifact) -> dict:
    settings = dict(artifact.settings or {})
    page_map = list(artifact.page_map or [])
    source_pages = settings.get('sourcePages')
    if not source_pages:
        source_pages = sorted(
            {int(row['sourcePageIndex']) for row in page_map if row.get('sourcePageIndex')}
        )
    return {
        'id': artifact.id,
        'fileId': artifact.client_file_id,
        'segmentId': artifact.client_segment_id or 'photo',
        'sha256': artifact.sha256,
        'url': f'/api/v1/assets/{artifact.asset_id}/content',
        'pages': artifact.pages,
        'sheets': artifact.sheets,
        'blankBacks': int(settings.get('blankBacks') or 0),
        'range': settings.get('range') or _range_label(source_pages),
        'copies': int(settings.get('copies') or 1),
        'color': settings.get('colorMode') or 'BW',
        'sides': settings.get('sides') or 'SINGLE',
        'flip': settings.get('flip') or 'NONE',
        'contentRevision': int(settings.get('contentRevision') or 1),
        'settingsHash': settings.get('settingsHash') or canonical_hash(settings),
        'inputSha256': settings.get('inputSha256'),
        'toolchain': dict(settings.get('toolchain') or {}),
        'warnings': list(settings.get('warnings') or []),
        'pageMap': page_map,
        'sourcePages': [int(page) for page in source_pages],
        'nonBillable': bool(settings.get('nonBillable')),
        'reprintOfArtifactId': settings.get('reprintOfArtifactId'),
        'reprintOfTaskId': settings.get('reprintOfTaskId'),
        'createdAt': artifact.created_at,
    }


def _quote_or_none(db, quote_id: str | None):
    if not quote_id:
        return None
    quote = db.get(Quote, quote_id)
    if quote is None:
        return None
    try:
        from .growth import quote_view

        return quote_view(quote)
    except DomainError:
        return None


def preparation_view(db, preparation: Preparation) -> dict:
    artifacts = list(
        db.scalars(select(Artifact).where(Artifact.preparation_id == preparation.id))
    )
    artifacts.sort(key=lambda row: int((row.settings or {}).get('sequence') or 0))
    data = dict(preparation.data or {})
    specs = data.get('files') or []
    files = []
    for spec in specs:
        asset = db.get(Asset, spec.get('assetId')) if spec.get('assetId') else None
        files.append(
            {
                'clientFileId': spec.get('clientFileId'),
                'assetId': spec.get('assetId'),
                'kind': spec.get('kind'),
                'name': spec.get('name') or (asset.name if asset else ''),
                'status': asset.status if asset else 'MISSING',
                'pages': spec.get('pages'),
                'excludedPages': list(spec.get('excludedPages') or []),
                'bindingType': spec.get('bindingType') or 'NONE',
                'segments': list(spec.get('segments') or []),
                'copies': spec.get('copies'),
                'orientation': spec.get('orientation'),
                'fit': spec.get('fit'),
                'rotationDegrees': spec.get('rotationDegrees'),
                'crop': spec.get('crop'),
            }
        )
    warnings = list(data.get('warnings') or [])
    errors: list[dict] = []
    if preparation.status == 'FAILED':
        errors.append(
            {
                'code': data.get('failureCode') or 'PREPARATION_FAILED',
                'message': data.get('failureMessage') or '最终 PDF 生成失败',
            }
        )
    if preparation.status == 'READY' and not artifacts:
        errors.append({'code': 'NO_ARTIFACTS', 'message': '准备单尚无可确认的最终 PDF'})
    quote = _quote_or_none(db, preparation.quote_id)
    can_adopt = (
        preparation.status == 'READY'
        and preparation.checkout_account_id is None
        and preparation.replaces_order_id is None
    )
    return {
        'id': preparation.id,
        'version': preparation.version,
        'status': preparation.status,
        'contentRevision': preparation.content_revision,
        'artifacts': [artifact_view(row) for row in artifacts],
        'validation': {'ok': preparation.status == 'READY' and bool(artifacts) and not errors,
                       'errors': errors, 'warnings': warnings},
        'quote': quote,
        'files': files,
        'expiresAt': preparation.expires_at,
        'cancelRequested': bool(preparation.cancel_requested),
        'checkoutAccountId': preparation.checkout_account_id,
        'checkoutAuthKind': (preparation.data or {}).get('checkoutAuthKind'),
        'sourceGrantId': preparation.source_grant_id,
        'canAdopt': can_adopt,
        'failureCode': data.get('failureCode'),
        'failureMessage': data.get('failureMessage'),
        'replacesOrderId': preparation.replaces_order_id,
        'expectedOrderVersion': preparation.expected_order_version,
        'createdAt': preparation.created_at,
        'updatedAt': preparation.updated_at,
    }


# ---------------------------------------------------------------------------
# Authorization helpers
# ---------------------------------------------------------------------------


def _owner_ids(db, actor: Actor) -> set[str]:
    values = {value for value in (actor.principal_id, actor.guest_principal_id) if value}
    if actor.account_id:
        values.update(
            db.scalars(select(Principal.id).where(Principal.account_id == actor.account_id))
        )
    return values


def _verified_account_id(db, actor: Actor) -> str | None:
    if not actor.account_id:
        return None
    account = db.get(Account, actor.account_id)
    if (
        account is None
        or account.deleted_at is not None
        or account.status != 'ACTIVE'
        or account.verified_at is None
    ):
        return None
    return account.id


def _source_grant_for(db, actor: Actor, order: Order, scope: str) -> str | None:
    """Find the concrete active grant that authorizes this order action."""
    candidates: list[str] = []
    for grant_id in actor.grant_ids or []:
        if grant_id not in candidates:
            candidates.append(grant_id)
    if actor.account_id:
        for grant_id in db.scalars(
            select(AccessGrant.id).where(
                AccessGrant.order_id == order.id,
                AccessGrant.account_id == actor.account_id,
                AccessGrant.revoked_at.is_(None),
                AccessGrant.expires_at > utcnow(),
            )
        ):
            if grant_id not in candidates:
                candidates.append(grant_id)
    for grant_id in candidates:
        if scope in grant_scopes(db, Actor(grant_ids=[grant_id]), order.id):
            return grant_id
    return None


def _source_grant_valid(db, preparation: Preparation) -> bool:
    if not preparation.source_grant_id:
        return True
    if not preparation.replaces_order_id:
        return False
    order = db.get(Order, preparation.replaces_order_id)
    if order is None or order.deleted_at is not None:
        return False
    return 'ORDER_REVISE_PENDING' in grant_scopes(
        db, Actor(grant_ids=[preparation.source_grant_id]), order.id
    )


def _assert_source_grant(db, preparation: Preparation) -> None:
    if not _source_grant_valid(db, preparation):
        raise DomainError(
            'SOURCE_GRANT_REVOKED',
            '原订单委托授权已撤销或过期，请重新取得授权',
            403,
        )


def _can_read_preparation_assets(db, actor: Actor, preparation: Preparation) -> bool:
    if _is_expired(preparation.expires_at):
        return False
    if owns_principal(db, actor, preparation.principal_id):
        return True
    if preparation.source_grant_id and preparation.replaces_order_id:
        order = db.get(Order, preparation.replaces_order_id)
        if order is None or order.deleted_at is not None:
            return False
        return (
            'ORDER_REVISE_PENDING' in grant_scopes(db, actor, order.id)
            and _source_grant_valid(db, preparation)
        )
    return False


def _readable_preparations(db, actor: Actor):
    seen: set[str] = set()
    if actor.principal_id:
        for preparation in db.scalars(
            select(Preparation).where(Preparation.principal_id == actor.principal_id)
        ):
            if preparation.id not in seen:
                seen.add(preparation.id)
                yield preparation
    grant_ids = set(actor.grant_ids or [])
    if actor.account_id:
        grant_ids.update(
            db.scalars(
                select(AccessGrant.id).where(
                    AccessGrant.account_id == actor.account_id,
                    AccessGrant.revoked_at.is_(None),
                    AccessGrant.expires_at > utcnow(),
                )
            )
        )
    if grant_ids:
        for preparation in db.scalars(
            select(Preparation).where(Preparation.source_grant_id.in_(grant_ids))
        ):
            if preparation.id not in seen:
                seen.add(preparation.id)
                yield preparation


def _asset_via_owned_preparation(db, actor: Actor, asset: Asset) -> bool:
    """Allow owners and source-grant editors to read exactly draft assets."""
    artifact = db.scalar(select(Artifact).where(Artifact.asset_id == asset.id))
    if artifact is not None:
        preparation = db.get(Preparation, artifact.preparation_id)
        if preparation is not None and _can_read_preparation_assets(db, actor, preparation):
            return True
    target_ids = {asset.id}
    normalized_from = (asset.data or {}).get('normalizedFrom')
    if normalized_from:
        target_ids.add(normalized_from)
    for preparation in _readable_preparations(db, actor):
        if not _can_read_preparation_assets(db, actor, preparation):
            continue
        for spec in ((preparation.data or {}).get('files') or []):
            if spec.get('assetId') in target_ids or spec.get('normalizedAssetId') in target_ids:
                return True
    return False


def _asset_proof_orders(db, asset_id: str) -> list[Order]:
    """Every order whose active proof uses this shared delivery photo.

    A batch photo is one physical asset shared by several proofs.  Access must be
    evaluated against each proof's own order instead of returning one arbitrary
    order, otherwise only the first customer in the batch could read it.
    """
    return list(db.scalars(
        select(Order).join(DeliveryProof, DeliveryProof.order_id == Order.id)
        .where(DeliveryProof.photo_asset_id == asset_id,
               DeliveryProof.active.is_(True))
        .order_by(DeliveryProof.created_at, DeliveryProof.id)))


def _asset_order(db, asset_id: str) -> Order | None:
    file = db.scalar(select(OrderFile).where(OrderFile.asset_id == asset_id))
    if file is not None:
        return db.get(Order, file.order_id)
    artifact = db.scalar(select(Artifact).where(Artifact.asset_id == asset_id))
    if artifact is not None:
        preparation = db.get(Preparation, artifact.preparation_id)
        if preparation is not None:
            order = db.scalar(select(Order).where(Order.preparation_id == preparation.id))
            if order is not None:
                return order
            # A delegated revision builds a replacement preparation before the
            # order is rewritten. Its grant still names the replaces order.
            if preparation.replaces_order_id:
                return db.get(Order, preparation.replaces_order_id)
    return None


def _courier_assignment(db, actor: Actor, asset: Asset) -> bool:
    if (actor.restricted or actor.staff_role != 'COURIER' or not actor.staff_id
            or asset.purpose != 'DELIVERY_PROOF' or not asset.assignment_id):
        return False
    assignment = db.get(DeliveryAssignment, asset.assignment_id)
    return bool(
        assignment
        and assignment.active
        and assignment.courier_id == actor.staff_id
    )


def _resolve_asset_identifier(db, identifier: str) -> str:
    """Accept an asset id, or an artifact id used by the production UI."""
    if db.get(Asset, identifier) is not None:
        return identifier
    artifact = db.get(Artifact, identifier)
    return artifact.asset_id if artifact is not None else identifier


def _has_capability(actor: Actor, capability: str) -> bool:
    return (actor.staff_id is not None and not actor.restricted
            and ('*' in actor.capabilities or capability in actor.capabilities))


def _require_admin_purpose(actor: Actor, purpose: str) -> bool:
    """Authorize an ADMIN by the concrete private-asset purpose.

    Returns ``False`` when the purpose has no ADMIN scope (the caller then
    keeps the normal object-level 404). A matching purpose always goes through
    ``require_admin(actor, capability)`` so restricted/must-change accounts and
    capability-less ADMINs fail closed.
    """
    if not actor.staff_id or actor.staff_role != 'ADMIN':
        return False
    capability = ADMIN_PURPOSE_CAPABILITIES.get(purpose)
    if capability is None:
        return False
    require_admin(actor, capability)
    return True


def _customer_scope_actor(actor: Actor) -> Actor:
    """Strip the staff identity before calling the shared order guard.

    ``core.authorize_order`` has an ADMIN bypass. Media must not inherit it
    implicitly: an ADMIN only reaches this path after the explicit purpose
    capability check, while owners/grantees are evaluated as pure customers.
    """
    if not actor.staff_id:
        return actor
    return replace(
        actor,
        staff_id=None,
        staff_role=None,
        staff_session_id=None,
        capabilities=set(),
        restricted=False,
    )


def _asset_order_scope(db, actor: Actor, asset: Asset, scope: str) -> bool:
    """True when a valid grant for a related order carries ``scope``.

    The actor is stripped of staff identity so ``core.authorize_order`` cannot
    apply its ADMIN bypass; an ADMIN only reaches private assets through the
    concrete purpose capability check.
    """
    customer = _customer_scope_actor(actor)
    orders = _asset_proof_orders(db, asset.id)
    file_order = _asset_order(db, asset.id)
    if file_order is not None and all(order.id != file_order.id for order in orders):
        orders.append(file_order)
    for order in orders:
        try:
            authorize_order(db, customer, order, scope)
            return True
        except DomainError:
            continue
    return False


def _asset_for_read(db, actor: Actor, asset_id: str) -> Asset:
    asset_id = _resolve_asset_identifier(db, asset_id)
    asset = db.get(Asset, asset_id)
    if asset is None or asset.deleted_at is not None:
        raise DomainError('NOT_FOUND', '文件不存在或不可访问', 404)
    if asset.purpose in PUBLIC_PURPOSES:
        return asset
    # Print masters are never reachable through an ORDER_VIEW order link. The
    # owner/account may read its own file; staff need the production capability
    # and the actual artifact workflow.
    if asset.purpose in SOURCE_PURPOSES:
        if asset.principal_id and owns_principal(db, actor, asset.principal_id):
            return asset
        if _require_admin_purpose(actor, asset.purpose):
            return asset
        raise DomainError('NOT_FOUND', '文件不存在或不可访问', 404)
    if asset.purpose in FINAL_PURPOSES:
        if asset.principal_id and owns_principal(db, actor, asset.principal_id):
            return asset
        if _asset_order_scope(db, actor, asset, 'FINAL_PDF_PREVIEW'):
            return asset
        if _require_admin_purpose(actor, asset.purpose):
            return asset
        raise DomainError('NOT_FOUND', '文件不存在或不可访问', 404)
    if asset.purpose == 'DELIVERY_PROOF':
        # A proof grant is tied to exactly its own order; the current assigned
        # courier keeps operational access, and ADMIN needs delivery.manage.
        if _asset_order_scope(db, actor, asset, 'DELIVERY_PROOF_VIEW'):
            return asset
        if _courier_assignment(db, actor, asset):
            return asset
        if _require_admin_purpose(actor, asset.purpose):
            return asset
        raise DomainError('NOT_FOUND', '文件不存在或不可访问', 404)
    if asset.principal_id and owns_principal(db, actor, asset.principal_id):
        return asset
    if _asset_via_owned_preparation(db, actor, asset):
        return asset
    if _require_admin_purpose(actor, asset.purpose):
        return asset
    raise DomainError('NOT_FOUND', '文件不存在或不可访问', 404)


def _asset_for_write(db, actor: Actor, asset_id: str) -> Asset:
    asset = db.get(Asset, asset_id)
    if asset is None or asset.deleted_at is not None:
        raise DomainError('NOT_FOUND', '文件不存在或不可访问', 404)
    if asset.principal_id and owns_principal(db, actor, asset.principal_id):
        return asset
    if _require_admin_purpose(actor, asset.purpose):
        return asset
    raise DomainError('NOT_FOUND', '文件不存在或不可访问', 404)


def _preparation_for(db, actor: Actor, preparation_id: str) -> Preparation:
    preparation = db.get(Preparation, preparation_id)
    if preparation is None:
        raise DomainError('NOT_FOUND', '准备任务不存在或不可访问', 404)
    if owns_principal(db, actor, preparation.principal_id):
        return preparation
    if preparation.source_grant_id and preparation.replaces_order_id:
        order = db.get(Order, preparation.replaces_order_id)
        if (
            order is not None
            and order.deleted_at is None
            and 'ORDER_REVISE_PENDING' in grant_scopes(db, actor, order.id)
            and _source_grant_valid(db, preparation)
        ):
            return preparation
    if actor.staff_id and actor.staff_role == 'ADMIN':
        # Preparations are customer-owned private drafts.  An ADMIN only sees
        # them through the production scope, never through staff identity.
        require_admin(actor, 'production.manage')
        return preparation
    raise DomainError('NOT_FOUND', '准备任务不存在或不可访问', 404)


def _upload_session_for(db, actor: Actor, session_id: str) -> UploadSession:
    session = db.get(UploadSession, session_id)
    if session is None:
        raise DomainError('NOT_FOUND', '上传会话不存在或不可访问', 404)
    if session.principal_id and owns_principal(db, actor, session.principal_id):
        return session
    if actor.staff_id and actor.staff_role == 'ADMIN':
        require_admin(actor, ADMIN_PURPOSE_CAPABILITIES.get(
            session.purpose, 'production.manage'
        ))
        return session
    raise DomainError('NOT_FOUND', '上传会话不存在或不可访问', 404)


# ---------------------------------------------------------------------------
# Upload: single multipart asset
# ---------------------------------------------------------------------------


def _purpose_target(db, actor: Actor, purpose: str, assignment_id: str | None):
    if purpose not in ALL_PURPOSES:
        raise DomainError('INVALID_PURPOSE', '文件用途无效', 422)
    if purpose in INTERNAL_PURPOSES:
        raise DomainError('INVALID_PURPOSE', '该文件用途只能由服务器生成', 422)
    if purpose in PUBLIC_PURPOSES:
        require_admin(actor, 'config.manage')
        return None, actor.staff_id, None
    if purpose in STAFF_PURPOSES:
        require_staff(actor)
        if actor.staff_role == 'ADMIN':
            # Uploading a delivery proof is an administrative delivery action.
            require_admin(actor, 'delivery.manage')
        if not assignment_id:
            raise DomainError('ASSIGNMENT_REQUIRED', '交付凭证必须关联当前配送执行', 422)
        assignment = db.get(DeliveryAssignment, assignment_id)
        if assignment is None or not assignment.active:
            raise DomainError('ASSIGNMENT_NOT_FOUND', '配送执行不存在或已失效', 404)
        if actor.staff_role != 'ADMIN' and assignment.courier_id != actor.staff_id:
            raise DomainError('FORBIDDEN', '无权为其他配送员上传凭证', 403)
        return None, actor.staff_id, assignment.id
    principal = _require_customer_principal(actor)
    return principal, None, None


async def _spool_upload(upload: UploadFile, destination: Path, limit: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(destination, 'wb') as handle:
            os.chmod(destination, 0o600)
            while True:
                chunk = await upload.read(BYTES_PER_MB)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise DomainError(
                        'UPLOAD_TOO_LARGE', '文件超过允许大小', 413, {'maxBytes': limit}
                    )
                handle.write(chunk)
                digest.update(chunk)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return total, digest.hexdigest()


def _parse_excel_form(value: str | None):
    if value is None or value == '':
        return None
    try:
        parsed = json.loads(value)
    except ValueError as error:
        raise DomainError('EXCEL_OPTIONS_INVALID', 'Excel 打印选项不是合法 JSON', 422) from error
    if not isinstance(parsed, dict):
        raise DomainError('EXCEL_OPTIONS_INVALID', 'Excel 打印选项必须为对象', 422)
    try:
        return pdf_engine.validate_excel_options(parsed).as_manifest()
    except pdf_engine.EngineError as error:
        raise _engine_error(error) from error


@router.post('/assets', response_model=Envelope[AssetUploadResult])
async def create_asset(
    request: Request,
    file: UploadFile = File(...),
    purpose: str = Form(...),
    assignmentId: str | None = Form(default=None),
    deliveryAssignmentId: str | None = Form(default=None),
    excelOptions: str | None = Form(default=None),
    db=Depends(get_db),
    actor: Actor = Depends(actor_dep),
):
    key = request.headers.get('idempotency-key')
    if not key:
        raise DomainError('IDEMPOTENCY_KEY_REQUIRED', '缺少操作标识，请刷新重试', 422)
    principal_id, staff_id, resolved_assignment = _purpose_target(
        db, actor, purpose, assignmentId or deliveryAssignmentId
    )
    services = _services(db)
    if purpose == 'PRINT_SOURCE':
        services = _require_file_processing_open(db)
    limit = _max_upload_bytes(services)
    name = _clean_name(file.filename or 'upload')
    excel = _parse_excel_form(excelOptions)
    if excel is not None and _kind_from_filename(name) not in ('XLS', 'XLSX'):
        raise DomainError('EXCEL_OPTIONS_INVALID', '只有 Excel 文件可以设置打印区域', 422)

    temporary = _root() / 'tmp' / f'upload-{uuid.uuid4().hex}.part'
    try:
        size, sha = await _spool_upload(file, temporary, limit)
        await file.close()
        if size <= 0:
            raise DomainError('EMPTY_FILE', '上传文件为空', 422)
        try:
            info = pdf_engine.inspect_upload(temporary, name, file.content_type or '')
            if info.kind in IMAGE_KINDS:
                pdf_engine.validate_image(
                    temporary, info.kind, max_pixels=int(services.get('maxPixels') or 40_000_000)
                )
            _format_allowed(services, info.kind)
        except pdf_engine.EngineError as error:
            raise _engine_error(error) from error

        def create() -> dict:
            asset_id = new_id()
            final_key = _source_key(asset_id, sha)
            pdf_engine.publish_file(temporary, _storage_path(final_key), sha)
            # Delivery proofs and admin public images need no derived artifact:
            # they are fully validated at intake and must be usable immediately
            # by the proof/config transaction. Print sources still need the
            # asynchronous normalized preview.
            immediately_ready = (
                info.kind in IMAGE_KINDS
                and purpose in (PUBLIC_PURPOSES | STAFF_PURPOSES)
            )
            asset = Asset(
                id=asset_id,
                principal_id=principal_id,
                staff_id=staff_id,
                assignment_id=resolved_assignment,
                purpose=purpose,
                name=name,
                mime=info.mime,
                size=size,
                sha256=sha,
                storage_key=final_key,
                status='READY' if immediately_ready else 'QUEUED',
                pages=1 if immediately_ready else None,
                data={
                    'kind': info.kind,
                    'originalFilename': name,
                    'declaredMime': file.content_type or '',
                    'excelOptions': excel,
                    'attempts': 0,
                    'retryable': True,
                    'intakeValidated': True,
                    'uploadedAt': utcnow().isoformat(),
                    **({'processedAt': utcnow().isoformat()} if immediately_ready else {}),
                },
                expires_at=_expiry_for(purpose, db),
            )
            db.add(asset)
            db.flush()
            enqueue_job(db, 'ASSET', asset.id, f'asset:{asset.id}', {'sha256': sha})
            audit(db, actor, 'ASSET_UPLOADED', 'ASSET', asset.id, data={'purpose': purpose, 'kind': info.kind})
            return {
                'assetId': asset.id,
                'status': asset.status,
                'kind': info.kind,
                'name': name,
                'size': size,
                'sha256': sha,
                'pages': None,
                'duplicate': False,
                'url': f'/api/v1/assets/{asset.id}/content',
                'normalizedUrl': None,
            }

        payload = {
            'purpose': purpose,
            'name': name,
            'size': size,
            'sha256': sha,
            'mime': file.content_type or '',
            'assignmentId': resolved_assignment,
            'excelOptions': excel,
        }
        result = idempotent_call(db, actor, request, key, payload, create)
        return result if isinstance(result, dict) and 'data' in result else ok(result)
    finally:
        temporary.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Upload: resumable chunk sessions (Q28)
# ---------------------------------------------------------------------------


def _session_chunks(session: UploadSession) -> list[dict]:
    data = session.data or {}
    rows = data.get('chunks')
    return [dict(row) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _session_received(session: UploadSession) -> int:
    try:
        return int((session.data or {}).get('receivedBytes') or 0)
    except (TypeError, ValueError):
        return 0


def _merge_byte_ranges(chunks: list[dict]) -> list[dict]:
    ranges: list[dict] = []
    for row in sorted(chunks, key=lambda item: int(item.get('offset') or 0)):
        offset = int(row.get('offset') or 0)
        length = int(row.get('length') or 0)
        if length <= 0:
            continue
        if ranges and offset == ranges[-1]['end'] + 1:
            ranges[-1]['end'] = offset + length - 1
        else:
            ranges.append({'start': offset, 'end': offset + length - 1})
    return ranges


def _session_status(session: UploadSession) -> str:
    if session.status == 'UPLOADING' and _is_expired(session.expires_at):
        return 'EXPIRED'
    return session.status


def _session_view(session: UploadSession) -> dict:
    data = session.data or {}
    chunks = _session_chunks(session)
    received = _session_received(session)
    return {
        'id': session.id,
        'status': _session_status(session),
        'purpose': session.purpose,
        'name': session.name,
        'totalBytes': session.total_bytes,
        'receivedBytes': received,
        'nextOffset': received if session.status == 'UPLOADING' else session.total_bytes,
        'chunkSize': DEFAULT_CHUNK_BYTES,
        'ranges': _merge_byte_ranges(chunks),
        'sha256': session.sha256,
        'mime': data.get('mime'),
        'expiresAt': session.expires_at,
        'assetId': session.asset_id,
        'cancelRequested': bool(data.get('cancelRequested')),
    }


@router.post('/upload-sessions', response_model=Envelope[UploadSessionView])
def create_upload_session(
    payload: UploadSessionCreate,
    request: Request,
    db=Depends(get_db),
    actor: Actor = Depends(actor_dep),
):
    body = payload
    if body.purpose != 'PRINT_SOURCE':
        raise DomainError('INVALID_PURPOSE', '分片上传仅用于打印源文件', 422)
    principal = _require_customer_principal(actor)
    services = _require_file_processing_open(db)
    limit = _max_file_bytes(services)
    if body.total_bytes > limit:
        raise DomainError('UPLOAD_TOO_LARGE', '文件超过允许大小', 413, {'maxBytes': limit})
    kind = _kind_from_filename(body.name)
    if kind is None:
        raise DomainError('UNSUPPORTED_FORMAT', '不支持该文件格式', 415)
    _format_allowed(services, kind)
    excel = None
    if body.excel_options is not None:
        if kind not in ('XLS', 'XLSX'):
            raise DomainError('EXCEL_OPTIONS_INVALID', '只有 Excel 文件可以设置打印区域', 422)
        try:
            excel = pdf_engine.validate_excel_options(body.excel_options).as_manifest()
        except pdf_engine.EngineError as error:
            raise _engine_error(error) from error
    session = UploadSession(
        principal_id=principal,
        purpose=body.purpose,
        name=_clean_name(body.name),
        total_bytes=body.total_bytes,
        sha256=body.sha256.lower(),
        status='UPLOADING',
        data={
            'mime': body.mime,
            'kind': kind,
            'excelOptions': excel,
            'chunks': [],
            'receivedBytes': 0,
        },
        expires_at=_draft_expiry(db),
    )
    db.add(session)
    db.flush()
    audit(db, actor, 'UPLOAD_SESSION_CREATED', 'UPLOAD_SESSION', session.id,
          data={'totalBytes': body.total_bytes, 'name': session.name})
    return ok(_session_view(session))


@router.get('/upload-sessions/{session_id}', response_model=Envelope[UploadSessionView])
def get_upload_session(session_id: str, db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    session = _upload_session_for(db, actor, session_id)
    return ok(_session_view(session))


@router.put('/upload-sessions/{session_id}/chunks', response_model=Envelope[UploadSessionView])
async def put_upload_chunk(
    session_id: str,
    request: Request,
    offset: int = Query(ge=0),
    db=Depends(get_db),
    actor: Actor = Depends(actor_dep),
):
    session = lock(db, UploadSession, session_id)
    _upload_session_for(db, actor, session_id)
    status = _session_status(session)
    if status == 'COMPLETED':
        raise DomainError('UPLOAD_ALREADY_COMPLETED', '上传已完成', 409,
                          {'assetId': session.asset_id})
    if status in ('CANCELLED', 'EXPIRED'):
        raise DomainError('UPLOAD_SESSION_CLOSED', '上传会话已结束，请重新开始', 409)
    body = await request.body()
    if not body:
        raise DomainError('EMPTY_CHUNK', '分片内容为空', 422)
    if len(body) > MAX_CHUNK_BYTES:
        raise DomainError('CHUNK_TOO_LARGE', '单个分片超过允许大小', 413)
    received = _session_received(session)
    if received + len(body) > session.total_bytes:
        raise DomainError('UPLOAD_TOO_LARGE', '分片超过上传会话总大小', 413,
                          {'totalBytes': session.total_bytes})
    chunk_sha = hashlib.sha256(body).hexdigest()
    supplied = (request.headers.get('x-chunk-sha256') or '').lower()
    if supplied and supplied != chunk_sha:
        raise DomainError('CHUNK_HASH_MISMATCH', '分片校验失败，请重新上传该分片', 422)
    data = dict(session.data or {})
    chunks = _session_chunks(session)
    for row in chunks:
        if int(row.get('offset') or 0) == offset:
            if int(row.get('length') or 0) == len(body) and row.get('sha256') == chunk_sha:
                return ok(_session_view(session))
            raise DomainError('CHUNK_CONFLICT', '该位置已有不同内容，请重新开始上传', 409)
    if offset > received:
        raise DomainError('UPLOAD_OFFSET_GAP', '分片位置不连续，请从已接收位置继续', 409,
                          {'expectedOffset': received})
    if offset < received:
        raise DomainError('CHUNK_CONFLICT', '分片与已接收内容重叠', 409,
                          {'expectedOffset': received})
    index = len(chunks)
    directory = _storage_path(f'sessions/{session.id}')
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f'{index:06d}.part'
    temporary = directory / f'{index:06d}.part.tmp'
    temporary.write_bytes(body)
    os.chmod(temporary, 0o600)
    with open(temporary, 'rb') as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, destination)
    chunks.append({'offset': offset, 'length': len(body), 'sha256': chunk_sha,
                   'index': index, 'storedAt': utcnow().isoformat()})
    data['chunks'] = chunks
    data['receivedBytes'] = received + len(body)
    session.data = data
    touch(session)
    return ok(_session_view(session))


def _assemble_chunks(session: UploadSession, destination: Path) -> str:
    chunks = sorted(_session_chunks(session), key=lambda row: int(row.get('offset') or 0))
    received = 0
    digest = hashlib.sha256()
    with open(destination, 'wb') as target:
        os.chmod(destination, 0o600)
        for row in chunks:
            offset = int(row.get('offset') or 0)
            if offset != received:
                raise DomainError('UPLOAD_INCOMPLETE', '上传分片不连续，请重新上传', 409)
            path = _storage_path(f"sessions/{session.id}/{int(row.get('index') or 0):06d}.part")
            if not path.is_file():
                raise DomainError('UPLOAD_INCOMPLETE', '上传分片已丢失，请重新上传', 409)
            with open(path, 'rb') as source:
                while True:
                    block = source.read(BYTES_PER_MB)
                    if not block:
                        break
                    target.write(block)
                    digest.update(block)
            received += int(row.get('length') or 0)
        target.flush()
        os.fsync(target.fileno())
    if received != session.total_bytes:
        raise DomainError('UPLOAD_INCOMPLETE', '上传尚未完成', 409,
                          {'receivedBytes': received, 'totalBytes': session.total_bytes})
    return digest.hexdigest()


@router.post('/upload-sessions/{session_id}/complete', response_model=Envelope[AssetUploadResult])
def complete_upload_session(
    session_id: str,
    payload: UploadSessionComplete,
    request: Request,
    db=Depends(get_db),
    actor: Actor = Depends(actor_dep),
):
    body = payload
    session = lock(db, UploadSession, session_id)
    _upload_session_for(db, actor, session_id)
    if session.status == 'COMPLETED':
        asset = db.get(Asset, session.asset_id) if session.asset_id else None
        if asset is None:
            raise DomainError('UPLOAD_STATE_INVALID', '上传结果异常，请联系管理员', 500)
        return ok(
            {
                'assetId': asset.id, 'status': asset.status,
                'kind': (asset.data or {}).get('kind'), 'name': asset.name,
                'size': asset.size, 'sha256': asset.sha256,
                'pages': asset.pages, 'duplicate': False,
                'url': f'/api/v1/assets/{asset.id}/content',
                'normalizedUrl': None,
            }
        )
    if _session_status(session) == 'EXPIRED':
        raise DomainError('UPLOAD_SESSION_EXPIRED', '上传会话已过期，请重新开始', 410)
    if session.status == 'CANCELLED':
        raise DomainError('UPLOAD_SESSION_CLOSED', '上传会话已取消', 409)
    if _session_received(session) != session.total_bytes:
        raise DomainError('UPLOAD_INCOMPLETE', '上传尚未完成', 409,
                          {'receivedBytes': _session_received(session),
                           'totalBytes': session.total_bytes})
    expected = session.sha256
    if body.sha256 and body.sha256.lower() != expected:
        raise DomainError('UPLOAD_HASH_MISMATCH', '声明的文件摘要与上传会话不一致', 409)
    temporary = _root() / 'tmp' / f'session-{session.id}-{uuid.uuid4().hex}.part'
    try:
        actual = _assemble_chunks(session, temporary)
        if actual != expected:
            raise DomainError('UPLOAD_HASH_MISMATCH', '合并后的文件摘要不一致，请重新上传', 409)
        data = dict(session.data or {})
        kind = data.get('kind') or _kind_from_filename(session.name)
        try:
            info = pdf_engine.inspect_upload(temporary, session.name, data.get('mime') or '')
            if info.kind in IMAGE_KINDS:
                services = _services(db)
                pdf_engine.validate_image(
                    temporary, info.kind, max_pixels=int(services.get('maxPixels') or 40_000_000)
                )
        except pdf_engine.EngineError as error:
            raise _engine_error(error) from error
        if kind and info.kind != kind:
            raise DomainError('MIME_MISMATCH', '文件内容与扩展名不一致', 415)
        asset_id = new_id()
        final_key = _source_key(asset_id, actual)
        pdf_engine.publish_file(temporary, _storage_path(final_key), actual)
        asset = Asset(
            id=asset_id,
            principal_id=session.principal_id,
            staff_id=session.staff_id,
            purpose=session.purpose,
            name=session.name,
            mime=info.mime,
            size=session.total_bytes,
            sha256=actual,
            storage_key=final_key,
            status='QUEUED',
            data={
                'kind': info.kind,
                'originalFilename': session.name,
                'declaredMime': data.get('mime') or '',
                'excelOptions': data.get('excelOptions'),
                'attempts': 0,
                'retryable': True,
                'intakeValidated': True,
                'resumedUpload': True,
                'uploadSessionId': session.id,
                'uploadedAt': utcnow().isoformat(),
            },
            expires_at=_expiry_for(session.purpose, db),
        )
        db.add(asset)
        db.flush()
        session.status = 'COMPLETED'
        session.asset_id = asset.id
        # Chunks are merged into immutable Asset storage; let the maintenance
        # sweep remove the now-useless session tree promptly.
        session.expires_at = utcnow()
        data['receivedBytes'] = session.total_bytes
        data['completedAt'] = utcnow().isoformat()
        data['cleanupPending'] = True
        session.data = data
        touch(session)
        enqueue_job(db, 'ASSET', asset.id, f'asset:{asset.id}', {'sha256': actual})
        audit(db, actor, 'UPLOAD_SESSION_COMPLETED', 'UPLOAD_SESSION', session.id,
              data={'assetId': asset.id, 'sha256': actual})
        return ok(
            {
                'assetId': asset.id, 'status': asset.status, 'kind': info.kind,
                'name': asset.name, 'size': asset.size, 'sha256': asset.sha256,
                'pages': asset.pages, 'duplicate': False,
                'url': f'/api/v1/assets/{asset.id}/content',
                'normalizedUrl': None,
            }
        )
    finally:
        temporary.unlink(missing_ok=True)


@router.delete('/upload-sessions/{session_id}', response_model=Envelope[UploadSessionView])
def cancel_upload_session(session_id: str, db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    session = lock(db, UploadSession, session_id)
    _upload_session_for(db, actor, session_id)
    if session.status == 'COMPLETED':
        # A completed upload is a real final fact; never pretend it was cancelled.
        return ok(_session_view(session))
    if session.status == 'CANCELLED':
        return ok(_session_view(session))
    data = dict(session.data or {})
    session.status = 'CANCELLED'
    session.expires_at = utcnow()
    data['cancelRequested'] = True
    data['cancelledAt'] = utcnow().isoformat()
    data['cleanupPending'] = True
    session.data = data
    touch(session)
    audit(db, actor, 'UPLOAD_SESSION_CANCELLED', 'UPLOAD_SESSION', session.id)
    return ok(_session_view(session))


# ---------------------------------------------------------------------------
# Asset status / content / retry / cancel
# ---------------------------------------------------------------------------


@router.get('/assets', response_model=Envelope[AssetList])
def list_assets(
    purpose: str | None = Query(default=None),
    db=Depends(get_db),
    actor: Actor = Depends(actor_dep),
):
    statement = select(Asset).where(Asset.deleted_at.is_(None))
    owners = _owner_ids(db, actor)
    conditions = []
    if owners:
        conditions.append(Asset.principal_id.in_(owners))
    if actor.staff_id and actor.staff_role == 'ADMIN':
        # Listing is scoped exactly like reading: an ADMIN sees only the
        # purposes the validated capability covers. Restricted/must-change
        # accounts get no staff-based listing condition at all.
        scoped_purposes = set()
        for capability, purposes in (
            ('production.manage', PRINT_PURPOSES),
            ('delivery.manage', {'DELIVERY_PROOF'}),
            ('config.manage', PUBLIC_PURPOSES),
        ):
            if _has_capability(actor, capability):
                scoped_purposes.update(purposes)
        if scoped_purposes and not actor.restricted:
            conditions.append(and_(
                Asset.staff_id == actor.staff_id,
                Asset.purpose.in_(scoped_purposes),
            ))
    if not conditions:
        raise DomainError('AUTH_REQUIRED', '请先建立客户会话', 401)
    statement = statement.where(or_(*conditions))
    if purpose:
        statement = statement.where(Asset.purpose == purpose)
    elif not actor.staff_id:
        # Customer draft recovery only needs printable sources; internal
        # normalized/final derivatives are not drafts and must not be listed.
        statement = statement.where(Asset.purpose == 'PRINT_SOURCE')
    rows = list(db.scalars(statement.order_by(Asset.created_at.desc()).limit(50)))
    return ok({'items': [_asset_view(row) for row in rows], 'total': len(rows)})


@router.get('/assets/{asset_id}', response_model=Envelope[AssetView])
def get_asset(asset_id: str, db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    asset = _asset_for_read(db, actor, _resolve_asset_identifier(db, asset_id))
    return ok(_asset_view(asset))


def _content_response(asset: Asset, range_header: str | None) -> Response:
    data = asset.data or {}
    proof_photo_pending = (
        asset.purpose == 'DELIVERY_PROOF'
        and data.get('kind') in IMAGE_KINDS
        and data.get('intakeValidated')
        and asset.status in ('QUEUED', 'PROCESSING')
    )
    if asset.status != 'READY' and not proof_photo_pending:
        raise DomainError('ASSET_NOT_READY', '文件尚未处理完成', 409, {'status': asset.status})
    if _is_expired(asset.expires_at):
        raise DomainError('ASSET_EXPIRED', '文件已过期，请重新上传', 410)
    path = _storage_path(asset.storage_key)
    if not path.is_file():
        raise DomainError('ASSET_BLOB_MISSING', '文件内容不可用，请重新上传', 410)
    size = path.stat().st_size
    start, end, partial = 0, size - 1, False
    if range_header:
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header.strip())
        if not match or (match.group(1) == '' and match.group(2) == ''):
            raise DomainError('RANGE_NOT_SATISFIABLE', '请求的文件范围无效', 416, {'size': size})
        first, last = match.groups()
        if first == '':
            length = int(last)
            if length <= 0:
                raise DomainError('RANGE_NOT_SATISFIABLE', '请求的文件范围无效', 416, {'size': size})
            start, end = max(0, size - length), size - 1
        else:
            start = int(first)
            end = int(last) if last else size - 1
            if start >= size or end < start:
                raise DomainError('RANGE_NOT_SATISFIABLE', '请求的文件范围超出文件大小', 416, {'size': size})
            end = min(end, size - 1)
        partial = True
    length = end - start + 1

    def stream():
        with open(path, 'rb') as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                block = handle.read(min(256 * 1024, remaining))
                if not block:
                    break
                remaining -= len(block)
                yield block

    headers = {
        'Accept-Ranges': 'bytes',
        'Content-Length': str(length),
        'Content-Disposition': 'inline',
        'ETag': f'"{asset.sha256}"',
        'X-Content-Type-Options': 'nosniff',
    }
    if partial:
        headers['Content-Range'] = f'bytes {start}-{end}/{size}'
    status = 206 if partial else 200
    return StreamingResponse(stream(), status_code=status, media_type=asset.mime, headers=headers)


@router.get('/assets/{asset_id}/content')
def get_asset_content(
    asset_id: str, request: Request, db=Depends(get_db), actor: Actor = Depends(actor_dep)
):
    asset = _asset_for_read(db, actor, _resolve_asset_identifier(db, asset_id))
    return _content_response(asset, request.headers.get('range'))


@router.head('/assets/{asset_id}/content')
def head_asset_content(
    asset_id: str, request: Request, db=Depends(get_db), actor: Actor = Depends(actor_dep)
):
    asset = _asset_for_read(db, actor, _resolve_asset_identifier(db, asset_id))
    response = _content_response(asset, request.headers.get('range'))
    headers = {key: value for key, value in response.headers.items() if key.lower() != 'content-length'}
    headers['Content-Length'] = response.headers.get('content-length', '0')
    return Response(status_code=response.status_code, headers=headers, media_type=asset.mime)


@router.post('/assets/{asset_id}/retry', response_model=Envelope[AssetView])
def retry_asset(asset_id: str, db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    asset = _asset_for_write(db, actor, asset_id)
    data = dict(asset.data or {})
    if asset.status == 'READY':
        raise DomainError('ALREADY_COMPLETED', '文件已处理完成，无需重试', 409)
    if data.get('cancelRequested'):
        raise DomainError('TASK_CANCELLED', '任务已取消，不能重试', 409)
    if asset.status != 'FAILED' or not data.get('retryable'):
        raise DomainError('NOT_RETRYABLE', '当前任务不可重试', 409, {'status': asset.status})
    if _is_expired(asset.expires_at):
        raise DomainError('ASSET_EXPIRED', '文件已过期，请重新上传', 410)
    attempts = int(data.get('attempts') or 0) + 1
    data.update(
        attempts=attempts,
        failureCode=None,
        failureMessage=None,
        failureDetail=None,
        retryable=True,
        attemptToken=None,
        lastRetryAt=utcnow().isoformat(),
    )
    asset.data = data
    asset.status = 'QUEUED'
    touch(asset)
    enqueue_job(db, 'ASSET', asset.id, f'asset:{asset.id}:retry:{attempts}', {'sha256': asset.sha256})
    audit(db, actor, 'ASSET_RETRY_REQUESTED', 'ASSET', asset.id, data={'attempt': attempts})
    return ok(_asset_view(asset))


@router.post('/assets/{asset_id}/cancel', response_model=Envelope[AssetView])
def cancel_asset(asset_id: str, db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    asset = _asset_for_write(db, actor, asset_id)
    data = dict(asset.data or {})
    if asset.status == 'READY':
        raise DomainError('ALREADY_COMPLETED', '文件已处理完成，不能取消', 409, {'status': 'READY'})
    if asset.status == 'FAILED':
        if data.get('failureCode') == 'CANCELED':
            return ok(_asset_view(asset))
        raise DomainError('ALREADY_COMPLETED', '任务已结束，不能取消', 409, {'status': 'FAILED'})
    previous_status = asset.status
    data['cancelRequested'] = True
    data['cancelledAt'] = utcnow().isoformat()
    if asset.status == 'QUEUED':
        asset.status = 'FAILED'
        data.update(failureCode='CANCELED', failureMessage=FAILURE_MESSAGES['CANCELED'],
                    retryable=False)
    asset.data = data
    touch(asset)
    audit(db, actor, 'ASSET_CANCEL_REQUESTED', 'ASSET', asset.id,
          data={'fromStatus': previous_status})
    return ok(_asset_view(asset))


# ---------------------------------------------------------------------------
# Asset processing (real worker; owns its transaction)
# ---------------------------------------------------------------------------


def _fail_asset(db, asset_id: str, token: str | None, code: str, message: str, retryable: bool) -> None:
    try:
        db.rollback()
        asset = lock(db, Asset, asset_id)
    except DomainError:
        return
    data = dict(asset.data or {})
    if token is not None and data.get('attemptToken') != token:
        return
    if asset.status == 'READY':
        return
    asset.status = 'FAILED'
    data.update(
        failureCode=code,
        failureMessage=message,
        retryable=bool(retryable) and not data.get('cancelRequested'),
        attemptToken=None,
        failedAt=utcnow().isoformat(),
    )
    asset.data = data
    touch(asset)
    db.commit()


def process_asset(db, asset_id: str) -> None:
    """Convert one uploaded asset in a worker-owned transaction.

    CONTRACT.md: workers obtain their own ``SessionLocal`` and own the
    transaction. The HTTP/outbox caller may be inside its own transaction, so a
    fresh session is used and committed step by step; the passed session is only
    kept for signature compatibility.
    """
    from .db import SessionLocal

    session = SessionLocal()
    try:
        _process_asset(session, asset_id)
    finally:
        session.close()


def _process_asset(db, asset_id: str) -> None:
    """Worker implementation; owns commits on the supplied session."""
    asset = lock(db, Asset, asset_id)
    if asset.deleted_at is not None:
        db.rollback()
        return
    data = dict(asset.data or {})
    if asset.status == 'READY':
        db.rollback()
        return
    if data.get('cancelRequested'):
        asset.status = 'FAILED'
        data.update(failureCode='CANCELED', failureMessage=FAILURE_MESSAGES['CANCELED'],
                    retryable=False, attemptToken=None)
        asset.data = data
        touch(asset)
        audit(db, 'system', 'ASSET_CANCELLED_BEFORE_START', 'ASSET', asset.id)
        db.commit()
        return
    if _is_expired(asset.expires_at):
        asset.status = 'FAILED'
        data.update(failureCode='EXPIRED', failureMessage=FAILURE_MESSAGES['EXPIRED'],
                    retryable=False, attemptToken=None)
        asset.data = data
        touch(asset)
        db.commit()
        return
    token = uuid.uuid4().hex
    attempts = int(data.get('attempts') or 0) + 1
    data.update(attemptToken=token, attempts=attempts, startedAt=utcnow().isoformat(),
                failureCode=None, failureMessage=None)
    asset.data = data
    asset.status = 'PROCESSING'
    touch(asset)
    db.commit()

    workdir = _root() / 'tmp' / f'asset-{asset.id}-{uuid.uuid4().hex}'
    workdir.mkdir(parents=True, exist_ok=True)
    normalized_asset_id = None
    try:
        source = _storage_path(asset.storage_key)
        if not source.is_file():
            raise pdf_engine.EngineError('SOURCE_CORRUPT', '原文件已丢失，请重新上传', retryable=True)
        if pdf_engine.sha256_file(source) != asset.sha256:
            raise pdf_engine.EngineError('SOURCE_CORRUPT', '原文件校验失败，请重新上传', retryable=True)
        kind = (asset.data or {}).get('kind') or ''
        if kind in IMAGE_KINDS:
            probe = pdf_engine.validate_image(
                source, kind, max_pixels=int(_services(db).get('maxPixels') or 40_000_000)
            )
            from PIL import Image, ImageOps

            with Image.open(source) as opened:
                fixed = ImageOps.exif_transpose(opened)
                width, height = fixed.size
                fixed.close()
            preview_path, preview_mime, preview_width, preview_height = _write_image_preview(
                source, workdir, kind
            )
            preview_sha = pdf_engine.sha256_file(preview_path)
            preview_key = _preview_key(asset.id, preview_sha)
            pdf_engine.publish_file(preview_path, _storage_path(preview_key), preview_sha)
            asset.pages = 1
            result = {
                'kind': kind,
                'image': {'width': width, 'height': height,
                          'exifOrientation': probe.exif_orientation},
                'normalizedSha256': preview_sha,
                'normalizedStorageKey': preview_key,
                'normalizedMime': preview_mime,
                'normalizedWidth': preview_width,
                'normalizedHeight': preview_height,
                'processedAt': utcnow().isoformat(),
            }
            normalized_meta = None
        elif kind in DOCUMENT_KINDS:
            services = _services(db)
            settings = get_settings()
            normalized, normalized_meta = pdf_engine.source_to_normalized_pdf(
                source,
                kind,
                workdir,
                max_pages=_max_pages(services),
                soffice_path=settings.soffice_path,
                timeout=settings.conversion_timeout_seconds,
                excel_options=(asset.data or {}).get('excelOptions'),
            )
            normalized_sha = pdf_engine.sha256_file(normalized)
            normalized_id = new_id()
            key = _normalized_key(normalized_id, normalized_sha)
            pdf_engine.publish_file(normalized, _storage_path(key), normalized_sha)
            result = {
                'kind': kind,
                'normalizedSha256': normalized_sha,
                'normalizedStorageKey': key,
                'normalizedAssetId': normalized_id,
                'pageGeometry': normalized_meta.get('pageGeometry'),
                'adaptedPages': normalized_meta.get('adaptedPages'),
                'converter': normalized_meta.get('converter'),
                'excel': normalized_meta.get('excel'),
                'toolchain': normalized_meta.get('toolchain'),
                'normalizedPages': normalized_meta.get('normalizedPages'),
                'processedAt': utcnow().isoformat(),
            }
        else:
            raise pdf_engine.EngineError('UNSUPPORTED_FORMAT', '不支持的文件类型', retryable=False)

        # Re-lock and prove this attempt is still the current, uncancelled one.
        db.rollback()
        current = lock(db, Asset, asset_id)
        current_data = dict(current.data or {})
        if (
            current_data.get('attemptToken') != token
            or current.status == 'READY'
            or current_data.get('cancelRequested')
            or current.deleted_at is not None
        ):
            db.rollback()
            return
        if _is_expired(current.expires_at):
            current.status = 'FAILED'
            current_data.update(failureCode='EXPIRED',
                                failureMessage=FAILURE_MESSAGES['EXPIRED'],
                                retryable=False, attemptToken=None)
            current.data = current_data
            touch(current)
            db.commit()
            return
        if normalized_meta is not None:
            normalized_asset = Asset(
                id=result.get('normalizedAssetId') or new_id(),
                principal_id=current.principal_id,
                staff_id=current.staff_id,
                purpose='PRINT_NORMALIZED',
                name=f'normalized-{current.id}.pdf',
                mime='application/pdf',
                size=(_storage_path(result['normalizedStorageKey'])).stat().st_size,
                sha256=result['normalizedSha256'],
                storage_key=result['normalizedStorageKey'],
                status='READY',
                pages=result['normalizedPages'],
                data={
                    'kind': 'PDF',
                    'normalizedFrom': current.id,
                    'pageGeometry': result.get('pageGeometry'),
                    'adaptedPages': result.get('adaptedPages'),
                    'converter': result.get('converter'),
                    'excel': result.get('excel'),
                    'toolchain': result.get('toolchain'),
                },
                expires_at=current.expires_at,
            )
            db.add(normalized_asset)
            db.flush()
            normalized_asset_id = normalized_asset.id
            current.pages = result['normalizedPages']
        elif result.get('normalizedStorageKey'):
            preview_asset = Asset(
                id=new_id(),
                principal_id=current.principal_id,
                staff_id=current.staff_id,
                purpose='PRINT_NORMALIZED',
                name=f'preview-{current.id}',
                mime=result['normalizedMime'],
                size=_storage_path(result['normalizedStorageKey']).stat().st_size,
                sha256=result['normalizedSha256'],
                storage_key=result['normalizedStorageKey'],
                status='READY',
                pages=1,
                data={
                    'kind': 'IMAGE',
                    'normalizedFrom': current.id,
                    'width': result.get('normalizedWidth'),
                    'height': result.get('normalizedHeight'),
                },
                expires_at=current.expires_at,
            )
            db.add(preview_asset)
            db.flush()
            normalized_asset_id = preview_asset.id
            current.pages = 1
        else:
            current.pages = 1
        current.normalized_asset_id = normalized_asset_id
        current_data = dict(current.data or {})
        current_data.update(result)
        current_data.update(failureCode=None, failureMessage=None, retryable=True,
                            attemptToken=None, processedAt=utcnow().isoformat(),
                            normalizedAssetId=normalized_asset_id)
        current.data = current_data
        current.status = 'READY'
        touch(current)
        db.commit()
    except pdf_engine.EngineError as error:
        _fail_asset(db, asset_id, token, error.code, error.message, error.retryable)
    except DomainError as error:
        _fail_asset(db, asset_id, token, error.code, error.message, False)
    except Exception:
        _fail_asset(db, asset_id, token, 'PROCESSING_FAILED',
                    '文件处理失败，请稍后重试', True)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Preparation creation
# ---------------------------------------------------------------------------


def _expand_ranges(segments, pages: int) -> tuple[list[list[int]], list[dict]]:
    expanded: list[list[int]] = []
    payload_segments: list[dict] = []
    for segment in segments:
        pages_in_segment: list[int] = []
        ranges: list[dict] = []
        for page_range in segment.ranges:
            start, end = page_range.from_page, page_range.to_page
            if end > pages:
                raise DomainError('PAGE_RANGE_INVALID', f'页码超出 1–{pages} 范围', 422)
            ranges.append({'from': start, 'to': end})
            pages_in_segment.extend(range(start, end + 1))
        if pages_in_segment != sorted(pages_in_segment):
            raise DomainError('PAGE_ORDER_INVALID', '分段页码必须按升序填写', 422)
        expanded.append(pages_in_segment)
        payload_segments.append(
            {
                'clientSegmentId': segment.client_segment_id,
                'ranges': ranges,
                'colorMode': segment.color_mode,
                'sides': segment.sides,
                'flip': segment.flip,
                'copies': segment.copies,
            }
        )
    return expanded, payload_segments


def _paper_for(photo_config: dict, orientation: str) -> pdf_engine.PaperSpec:
    try:
        width = float(photo_config.get('photoWidthMm'))
        height = float(photo_config.get('photoHeightMm'))
        margins = photo_config.get('margins') or {}
        margins = {
            key: float(margins.get(key, 0) or 0)
            for key in ('top', 'right', 'bottom', 'left')
        }
    except (TypeError, ValueError) as error:
        raise DomainError('PHOTO_NOT_CALIBRATED', '照片纸张尚未校准', 422) from error
    long_side, short_side = max(width, height), min(width, height)
    if orientation == 'LANDSCAPE':
        paper = pdf_engine.PaperSpec(long_side, short_side, margins)
    else:
        paper = pdf_engine.PaperSpec(short_side, long_side, margins)
    try:
        paper.validate()
    except pdf_engine.EngineError as error:
        raise _engine_error(error) from error
    return paper


def _resolve_asset_for_preparation(db, actor: Actor, asset_id: str) -> Asset:
    asset = db.get(Asset, asset_id)
    if asset is None or asset.deleted_at is not None:
        raise DomainError('ASSET_NOT_FOUND', '文件不存在或不可访问', 404)
    if not owns_principal(db, actor, asset.principal_id):
        raise DomainError('ASSET_NOT_FOUND', '文件不存在或不可访问', 404)
    if asset.purpose != 'PRINT_SOURCE':
        raise DomainError('ASSET_NOT_PRINTABLE', '该文件不能用于打印准备', 422)
    if _is_expired(asset.expires_at):
        raise DomainError('ASSET_EXPIRED', '文件已过期，请重新上传', 410)
    if asset.status != 'READY':
        raise DomainError('ASSET_NOT_READY', '文件尚未处理完成', 409, {'status': asset.status})
    return asset


def _document_spec(db, actor: Actor, item, services: dict, budget: dict) -> dict:
    asset = _resolve_asset_for_preparation(db, actor, item.asset_id)
    kind = (asset.data or {}).get('kind')
    if kind not in DOCUMENT_KINDS:
        raise DomainError('ASSET_KIND_MISMATCH', '该文件不是可打印文档', 422)
    if not asset.normalized_asset_id:
        raise DomainError('ASSET_NOT_READY', '文档尚未标准化完成', 409)
    normalized = db.get(Asset, asset.normalized_asset_id)
    if normalized is None or normalized.status != 'READY':
        raise DomainError('ASSET_NOT_READY', '标准化 PDF 不可用', 409)
    pages = int(normalized.pages or asset.pages or 0)
    if pages <= 0:
        raise DomainError('ASSET_NOT_READY', '文档页数无效', 409)
    max_pages = _max_pages(services)
    if pages > max_pages:
        raise DomainError('PAGE_LIMIT_EXCEEDED', f'文档页数超过上限 {max_pages}', 422)
    expanded, segment_payload = _expand_ranges(item.segments, pages)
    try:
        pdf_engine.validate_page_allocation(pages, expanded, item.excluded_pages)
    except pdf_engine.EngineError as error:
        raise _engine_error(error) from error
    copies_limit = _max_copies(services)
    segments = []
    expanded_total = 0
    binding_thickness = 0
    for raw, page_list in zip(segment_payload, expanded):
        if raw['copies'] > copies_limit:
            raise DomainError('COPY_LIMIT_EXCEEDED', f'份数不能超过 {copies_limit}', 422)
        pdf_pages, sheets, blank_backs = pdf_engine.production_count(
            len(page_list), raw['copies'], raw['sides']
        )
        expanded_total += pdf_pages
        segments.append({**raw, 'pages': page_list, 'pdfPages': pdf_pages,
                         'sheets': sheets, 'blankBacks': blank_backs})
        binding_thickness += pdf_engine.production_count(
            len(page_list), 1, raw['sides']
        )[1]
    if expanded_total > _expansion_limit(services):
        raise DomainError('EXPANSION_LIMIT_EXCEEDED', '展开后的页数超过上限', 422)
    if item.binding_type != 'NONE':
        if len({segment['copies'] for segment in segment_payload}) > 1:
            raise DomainError('BINDING_COPIES_MISMATCH',
                              '同一文件各分段份数不一致，不能装订；请先统一份数', 422)
        if not services.get('bindingApproved'):
            raise DomainError('BINDING_NOT_APPROVED', '装订服务尚未开放', 422)
        limits = services.get('bindingLimits') or {}
        limit = limits.get(item.binding_type)
        if limit is not None and binding_thickness > int(limit):
            raise DomainError('BINDING_TOO_THICK',
                              f'每册 {binding_thickness} 张超过 {item.binding_type} 装订上限 {limit} 张', 422)
    budget['bytes'] += asset.size
    budget['expanded'] += expanded_total
    budget['pages'] = max(budget['pages'], pages)
    return {
        'clientFileId': item.client_file_id,
        'assetId': asset.id,
        'normalizedAssetId': normalized.id,
        'kind': 'DOCUMENT',
        'sourceKind': kind,
        'name': asset.name,
        'pages': pages,
        'excludedPages': list(item.excluded_pages),
        'bindingType': item.binding_type,
        'bindingThickness': binding_thickness,
        'segments': segments,
    }


def _photo_spec(db, actor: Actor, item, services: dict, photo_config: dict, budget: dict) -> dict:
    asset = _resolve_asset_for_preparation(db, actor, item.asset_id)
    kind = (asset.data or {}).get('kind')
    if kind not in IMAGE_KINDS:
        raise DomainError('ASSET_KIND_MISMATCH', '照片任务必须使用原始图片', 422)
    if not services.get('photoEnabled') or not services.get('photoCalibrated'):
        raise DomainError('PHOTO_NOT_CALIBRATED', '照片打印尚未校准开放', 422)
    copies_limit = _max_copies(services)
    if item.copies > copies_limit:
        raise DomainError('COPY_LIMIT_EXCEEDED', f'份数不能超过 {copies_limit}', 422)
    paper = _paper_for(photo_config, item.orientation)
    image = (asset.data or {}).get('image') or {}
    width, height = int(image.get('width') or 0), int(image.get('height') or 0)
    if width <= 0 or height <= 0:
        raise DomainError('ASSET_NOT_READY', '图片尺寸不可用', 409)
    if item.rotation_degrees % 180:
        width, height = height, width
    if item.fit == 'COVER':
        try:
            pdf_engine.validate_photo_crop(
                width,
                height,
                paper.print_width_mm,
                paper.print_height_mm,
                item.crop.model_dump() if item.crop else None,
            )
        except pdf_engine.EngineError as error:
            raise _engine_error(error) from error
    expanded_total = item.copies
    if expanded_total > _expansion_limit(services):
        raise DomainError('EXPANSION_LIMIT_EXCEEDED', '展开后的页数超过上限', 422)
    budget['bytes'] += asset.size
    budget['expanded'] += expanded_total
    budget['pages'] = max(budget['pages'], 1)
    return {
        'clientFileId': item.client_file_id,
        'assetId': asset.id,
        'kind': 'PHOTO',
        'sourceKind': kind,
        'normalizedAssetId': asset.normalized_asset_id,
        'name': asset.name,
        'pages': 1,
        'copies': item.copies,
        'orientation': item.orientation,
        'fit': item.fit,
        'rotationDegrees': item.rotation_degrees,
        'crop': item.crop.model_dump() if item.crop else None,
        'paper': {
            'widthMm': paper.width_mm,
            'heightMm': paper.height_mm,
            'printWidthMm': paper.print_width_mm,
            'printHeightMm': paper.print_height_mm,
            'marginsMm': dict(paper.margins_mm),
        },
        'excludedPages': [],
        'bindingType': 'NONE',
        'segments': [],
    }


@router.post('/preparations', response_model=Envelope[PreparationView])
def create_preparation(
    payload: PreparationInput,
    request: Request,
    db=Depends(get_db),
    actor: Actor = Depends(actor_dep),
):
    body = payload
    principal = _require_customer_principal(actor)
    services = _require_file_processing_open(db)
    if len(body.files) > int(services.get('maxFiles') or 10):
        raise DomainError('FILE_LIMIT_EXCEEDED', '文件数量超过上限', 422)
    replaces_order = None
    checkout_account_id = None
    checkout_auth_kind = 'ANONYMOUS'
    source_grant_id = None
    if body.replaces_order_id:
        replaces_order = db.get(Order, body.replaces_order_id)
        if replaces_order is None:
            raise DomainError('ORDER_NOT_FOUND', '订单不存在或不可访问', 404)
        authorize_order(db, actor, replaces_order, 'ORDER_REVISE_PENDING')
        if replaces_order.status != 'PENDING_CONFIRMATION':
            raise DomainError('ORDER_NOT_EDITABLE', '订单已确认付款，不能修改内容', 409)
        if body.expected_order_version is None or body.expected_order_version != replaces_order.version:
            raise DomainError('VERSION_CONFLICT', '订单已变化，请刷新后重试', 409,
                              {'currentVersion': replaces_order.version})
        # A revision must retain the original checkout identity and auth kind;
        # neither may be changed by the client or downgraded to guest. The quote
        # is built for that account so its reserved coupon terms survive.
        checkout_account_id = replaces_order.checkout_account_id
        replaces_data = dict(replaces_order.data or {})
        checkout_auth_kind = (
            replaces_data.get('checkoutAuthKind')
            or ('REGISTERED' if checkout_account_id else 'ANONYMOUS')
        )
        # A revision always belongs to the original order owner; a delegated
        # editor acts through the recorded source grant, never as the owner.
        principal = replaces_order.principal_id
        if not owns_principal(db, actor, replaces_order.principal_id):
            # Single-order delegation: record the concrete source grant and
            # re-check it during generation and order activation. Staff cookies
            # are stripped on this surface, so the grant is the only authority.
            source_grant_id = _source_grant_for(db, actor, replaces_order, 'ORDER_REVISE_PENDING')
            if not source_grant_id:
                raise DomainError(
                    'ORDER_GRANT_SCOPE_DENIED',
                    '当前链接未授予待确认订单编辑权限',
                    403,
                )
    elif body.expected_order_version is not None:
        raise DomainError('ORDER_VERSION_WITHOUT_ORDER', '未提供要修改的订单', 422)
    photo_config = _config(db, 'photo')
    budget = {'bytes': 0, 'expanded': 0, 'pages': 0}
    specs = []
    for item in body.files:
        if item.kind == 'DOCUMENT':
            specs.append(_document_spec(db, actor, item, services, budget))
        else:
            specs.append(_photo_spec(db, actor, item, services, photo_config, budget))
    max_order = int(services.get('maxOrderMb') or 200) * BYTES_PER_MB
    if budget['bytes'] > max_order:
        raise DomainError('ORDER_SIZE_EXCEEDED', '本单文件总大小超过上限', 422)
    if budget['expanded'] > _expansion_limit(services):
        raise DomainError('EXPANSION_LIMIT_EXCEEDED', '展开后的总页数超过上限', 422)
    if replaces_order is None:
        # A verified ACTIVE account that uploaded with its own primary principal
        # settles directly with that account; no fake guest + adopt round trip.
        account_id = _verified_account_id(db, actor)
        if account_id:
            account = db.get(Account, account_id)
            asset_principals = {
                db.get(Asset, spec['assetId']).principal_id for spec in specs
            }
            if asset_principals == {account.principal_id}:
                checkout_account_id = account.id
                checkout_auth_kind = 'REGISTERED'
    input_hash = canonical_hash(
        {
            'input': body.model_dump(by_alias=True),
            'assets': [
                {'assetId': spec['assetId'], 'sha256': db.get(Asset, spec['assetId']).sha256}
                for spec in specs
            ],
            'config': {
                'services': (get_config(db, 'services')).version,
                'photo': (get_config(db, 'photo')).version,
                'prices': (get_config(db, 'prices')).version,
            },
        }
    )
    preparation = Preparation(
        principal_id=principal,
        checkout_account_id=checkout_account_id,
        source_grant_id=source_grant_id,
        replaces_order_id=body.replaces_order_id,
        expected_order_version=body.expected_order_version,
        status='QUEUED',
        content_revision=1,
        input=body.model_dump(by_alias=True),
        data={
            'files': specs,
            'promotionIntent': body.promotion_intent.model_dump(by_alias=True)
            if body.promotion_intent else {'selectionMode': 'AUTO'},
            'inputSha256': input_hash,
            'limits': {
                'maxPages': _max_pages(services),
                'maxCopies': _max_copies(services),
                'maxExpandedPages': _expansion_limit(services),
                'maxFinalMb': int(services.get('maxFinalMb') or 500),
            },
            'checkoutAuthKind': checkout_auth_kind,
            'sourceGrantId': source_grant_id,
            'attempts': 0,
            'retryable': True,
            'warnings': [],
        },
        expires_at=_draft_expiry(db),
    )
    db.add(preparation)
    db.flush()
    enqueue_job(db, 'PREPARATION', preparation.id,
                f'preparation:{preparation.id}:1',
                {'contentRevision': preparation.content_revision})
    audit(db, actor, 'PREPARATION_CREATED', 'PREPARATION', preparation.id,
          data={
              'files': len(specs),
              'replacesOrderId': body.replaces_order_id,
              'checkoutAuthKind': checkout_auth_kind,
              'sourceGrantId': source_grant_id,
          })
    return ok(preparation_view(db, preparation))


# ---------------------------------------------------------------------------
# Preparation reads / retry / requote / adopt / withdraw / cancel / drafts
# ---------------------------------------------------------------------------


@router.get('/preparations', response_model=Envelope[PreparationList])
def list_preparations(db=Depends(get_db), actor: Actor = Depends(actor_dep)):
    owners = _owner_ids(db, actor)
    grant_ids = set(actor.grant_ids or [])
    if actor.account_id:
        grant_ids.update(
            db.scalars(
                select(AccessGrant.id).where(
                    AccessGrant.account_id == actor.account_id,
                    AccessGrant.revoked_at.is_(None),
                    AccessGrant.expires_at > utcnow(),
                )
            )
        )
    if not owners and not grant_ids:
        raise DomainError('AUTH_REQUIRED', '请先建立客户会话或打开订单链接', 401)
    statement = select(Preparation)
    conditions = []
    if owners:
        conditions.append(Preparation.principal_id.in_(owners))
    if grant_ids:
        conditions.append(Preparation.source_grant_id.in_(grant_ids))
    rows = list(
        db.scalars(
            statement.where(or_(*conditions))
            .order_by(Preparation.created_at.desc())
            .limit(20)
        )
    )
    items = []
    for row in rows:
        if _is_expired(row.expires_at):
            continue
        if not owns_principal(db, actor, row.principal_id) and not _source_grant_valid(db, row):
            continue
        items.append(preparation_view(db, row))
    return ok({'items': items, 'total': len(items)})


@router.get('/preparations/{preparation_id}', response_model=Envelope[PreparationView])
def get_preparation(
    preparation_id: str, db=Depends(get_db), actor: Actor = Depends(actor_dep)
):
    preparation = _preparation_for(db, actor, preparation_id)
    if _is_expired(preparation.expires_at):
        raise DomainError('PREPARATION_EXPIRED', '准备草稿已过期，请重新准备', 410)
    return ok(preparation_view(db, preparation))


def _preparation_lines(db, preparation: Preparation) -> list[dict]:
    prices_section = get_config(db, 'prices')
    prices = dict(prices_section.data or {})
    data = preparation.data or {}
    specs = data.get('files') or []
    lines: list[dict] = []
    for spec in specs:
        if spec.get('kind') == 'DOCUMENT':
            for segment in spec.get('segments') or []:
                color = segment.get('colorMode')
                sides = segment.get('sides')
                key = (
                    'bwSingle' if color == 'BW' and sides == 'SINGLE' else
                    'bwDuplex' if color == 'BW' else
                    'colorSingle' if sides == 'SINGLE' else
                    'colorDuplex'
                )
                price = prices.get(key)
                if price is None:
                    raise DomainError('PRICE_NOT_CONFIGURED', '当前服务价格尚未配置，暂时无法报价', 503)
                quantity = int(segment.get('sheets') or 0)
                line_key = f"{spec['clientFileId']}:{segment['clientSegmentId']}"
                lines.append({
                    'key': line_key,
                    'relatedId': segment['clientSegmentId'],
                    'unit': 'SHEET',
                    'quantity': quantity,
                    'unitPriceCents': int(price),
                    'amountCents': quantity * int(price),
                    'service': 'A4',
                    'a4Sheets': quantity,
                    'colorMode': color,
                    'sides': sides,
                    'copies': segment.get('copies'),
                    'pages': len(segment.get('pages') or []),
                })
            if spec.get('bindingType') not in (None, 'NONE'):
                price = prices.get('corner' if spec['bindingType'] == 'CORNER' else 'side')
                if price is None:
                    raise DomainError('PRICE_NOT_CONFIGURED', '装订价格尚未配置，暂时无法报价', 503)
                copies = int((spec.get('segments') or [{}])[0].get('copies') or 1)
                line_key = f"{spec['clientFileId']}:binding"
                lines.append({
                    'key': line_key,
                    'relatedId': spec['clientFileId'],
                    'unit': 'BOOKLET',
                    'quantity': copies,
                    'unitPriceCents': int(price),
                    'amountCents': copies * int(price),
                    'service': 'BINDING',
                    'bindingType': spec['bindingType'],
                })
        else:
            price = prices.get('photo')
            if price is None:
                raise DomainError('PRICE_NOT_CONFIGURED', '照片价格尚未配置，暂时无法报价', 503)
            copies = int(spec.get('copies') or 1)
            line_key = f"{spec['clientFileId']}:photo"
            lines.append({
                'key': line_key,
                'relatedId': spec['clientFileId'],
                'unit': 'SHEET',
                'quantity': copies,
                'unitPriceCents': int(price),
                'amountCents': copies * int(price),
                'service': 'PHOTO',
                'copies': copies,
            })
    if not lines:
        raise DomainError('QUOTE_LINES_INVALID', '没有可报价的打印任务', 422)
    return lines


def _prep_actor(db, preparation: Preparation) -> Actor:
    account_id = preparation.checkout_account_id
    principal_id = preparation.principal_id
    return Actor(principal_id=principal_id, guest_principal_id=principal_id,
                 account_id=account_id)


def _fail_preparation(
    db, preparation_id: str, token: str | None, code: str, message: str, retryable: bool
) -> None:
    try:
        db.rollback()
        preparation = lock(db, Preparation, preparation_id)
    except DomainError:
        return
    data = dict(preparation.data or {})
    if token is not None and data.get('attemptToken') != token:
        return
    if preparation.status in ('READY', 'CANCELLED'):
        return
    preparation.status = 'FAILED'
    data.update(
        failureCode=code,
        failureMessage=message,
        retryable=bool(retryable) and not preparation.cancel_requested,
        attemptToken=None,
        failedAt=utcnow().isoformat(),
    )
    preparation.data = data
    touch(preparation)
    db.commit()


@router.post('/preparations/{preparation_id}/retry', response_model=Envelope[PreparationView])
def retry_preparation(
    preparation_id: str, db=Depends(get_db), actor: Actor = Depends(actor_dep)
):
    preparation = lock(db, Preparation, preparation_id)
    _preparation_for(db, actor, preparation_id)
    data = dict(preparation.data or {})
    if preparation.status != 'FAILED':
        raise DomainError('NOT_RETRYABLE', '只有失败的准备任务可以重试', 409,
                          {'status': preparation.status})
    if preparation.cancel_requested or data.get('cancelRequested'):
        raise DomainError('TASK_CANCELLED', '任务已取消，不能重试', 409)
    if not data.get('retryable'):
        raise DomainError('NOT_RETRYABLE', '该失败不可自动重试', 409)
    if _is_expired(preparation.expires_at):
        raise DomainError('PREPARATION_EXPIRED', '准备草稿已过期，请重新准备', 410)
    if db.scalar(select(Order.id).where(Order.preparation_id == preparation.id)):
        raise DomainError('PREPARATION_IN_USE', '准备任务已生成订单，不能重试', 409)
    attempts = int(data.get('attempts') or 0) + 1
    data.update(attempts=attempts, failureCode=None, failureMessage=None,
                failureDetail=None, retryable=True, attemptToken=None)
    preparation.data = data
    preparation.status = 'QUEUED'
    touch(preparation)
    enqueue_job(db, 'PREPARATION', preparation.id,
                f'preparation:{preparation.id}:retry:{attempts}',
                {'contentRevision': preparation.content_revision})
    audit(db, actor, 'PREPARATION_RETRY_REQUESTED', 'PREPARATION', preparation.id,
          data={'attempt': attempts})
    return ok(preparation_view(db, preparation))


@router.post('/preparations/{preparation_id}/cancel', response_model=Envelope[PreparationView])
def cancel_preparation(
    preparation_id: str, db=Depends(get_db), actor: Actor = Depends(actor_dep)
):
    preparation = lock(db, Preparation, preparation_id)
    _preparation_for(db, actor, preparation_id)
    data = dict(preparation.data or {})
    if preparation.status == 'READY':
        if db.scalar(select(Order.id).where(Order.preparation_id == preparation.id)):
            raise DomainError('PREPARATION_IN_USE', '准备任务已生成订单，不能取消', 409)
        preparation.status = 'CANCELLED'
        data['cancelledAt'] = utcnow().isoformat()
        preparation.cancel_requested = True
        preparation.data = data
        touch(preparation)
        audit(db, actor, 'PREPARATION_CANCELLED', 'PREPARATION', preparation.id,
              data={'fromStatus': 'READY'})
        return ok(preparation_view(db, preparation))
    if preparation.status == 'CANCELLED':
        return ok(preparation_view(db, preparation))
    if preparation.status == 'FAILED':
        raise DomainError('ALREADY_COMPLETED', '准备任务已结束，不能取消', 409)
    previous_status = preparation.status
    preparation.cancel_requested = True
    data['cancelRequested'] = True
    data['cancelledAt'] = utcnow().isoformat()
    preparation.data = data
    if preparation.status == 'QUEUED':
        preparation.status = 'CANCELLED'
    touch(preparation)
    audit(db, actor, 'PREPARATION_CANCEL_REQUESTED', 'PREPARATION', preparation.id,
          data={'fromStatus': previous_status})
    return ok(preparation_view(db, preparation))


@router.post('/preparations/{preparation_id}/requote', response_model=Envelope[QuoteDTO])
def requote_preparation(
    preparation_id: str,
    payload: RequoteInput,
    db=Depends(get_db),
    actor: Actor = Depends(actor_dep),
):
    from .growth import build_quote, quote_view

    body = payload
    preparation = lock(db, Preparation, preparation_id)
    _preparation_for(db, actor, preparation_id)
    if preparation.status != 'READY':
        raise DomainError('PREPARATION_NOT_READY', '最终 PDF 尚未生成完成', 409,
                          {'status': preparation.status})
    if _is_expired(preparation.expires_at):
        raise DomainError('PREPARATION_EXPIRED', '准备草稿已过期，请重新准备', 410)
    intent = body.promotion_intent
    if intent is None:
        stored = (preparation.data or {}).get('promotionIntent')
        intent = stored if isinstance(stored, dict) else {'selectionMode': 'AUTO'}
    lines = _preparation_lines(db, preparation)
    quote = build_quote(db, preparation, lines, _prep_actor(db, preparation), intent)
    preparation.quote_id = quote.id
    data = dict(preparation.data or {})
    data['promotionIntent'] = (
        intent.model_dump(by_alias=True) if hasattr(intent, 'model_dump') else dict(intent)
    )
    preparation.data = data
    touch(preparation)
    audit(db, actor, 'PREPARATION_REQUOTED', 'PREPARATION', preparation.id,
          data={'quoteId': quote.id})
    return ok(quote_view(quote))


@router.post('/preparations/{preparation_id}/adopt-checkout', response_model=Envelope[PreparationView])
def adopt_checkout(
    preparation_id: str,
    payload: ConfirmCheckoutInput,
    db=Depends(get_db),
    actor: Actor = Depends(actor_dep),
):
    from .growth import build_quote

    body = payload
    preparation = lock(db, Preparation, preparation_id, body.expected_version)
    _preparation_for(db, actor, preparation_id)
    if preparation.status != 'READY':
        raise DomainError('PREPARATION_NOT_READY', '最终 PDF 尚未生成完成', 409,
                          {'status': preparation.status})
    if _is_expired(preparation.expires_at):
        raise DomainError('PREPARATION_EXPIRED', '准备草稿已过期，请重新准备', 410)
    if preparation.replaces_order_id:
        raise DomainError('CHECKOUT_ADOPT_NOT_APPLICABLE', '修改既有订单时不能改变结算身份', 409)
    if preparation.checkout_account_id:
        raise DomainError('CHECKOUT_ALREADY_ADOPTED', '该准备单已绑定账号', 409)
    if not actor.account_id:
        raise DomainError('ACCOUNT_VERIFICATION_REQUIRED', '请先登录已验证账号', 403)
    from .models import Account

    account = db.get(Account, actor.account_id)
    if account is None or account.status != 'ACTIVE' or account.verified_at is None or account.deleted_at:
        raise DomainError('ACCOUNT_VERIFICATION_REQUIRED', '请先登录已验证账号', 403)
    principal = db.get(Principal, preparation.principal_id)
    if principal is not None and principal.account_id and principal.account_id != account.id:
        raise DomainError('OWNERSHIP_CONFLICT', '该草稿属于其他账号，不能接管', 409)
    data = dict(preparation.data or {})
    original_principal_id = preparation.principal_id
    data['checkoutGrant'] = {
        'accountId': account.id,
        'principalId': original_principal_id,
        'assetPrincipalId': original_principal_id,
        'preparationId': preparation.id,
        'contentHash': data.get('inputSha256'),
        'createdAt': utcnow().isoformat(),
        'expiresAt': preparation.expires_at.isoformat(),
        'scope': 'CHECKOUT_USE_ONLY',
    }
    data['checkoutAuthKind'] = 'ADOPTED'
    # The order belongs to the account principal, but the already-uploaded
    # assets keep their original guest owner (Q20). No asset is re-owned.
    if original_principal_id != account.principal_id:
        data['originalPrincipalId'] = original_principal_id
        preparation.principal_id = account.principal_id
    preparation.data = data
    preparation.checkout_account_id = account.id
    db.flush()
    intent = data.get('promotionIntent') or {'selectionMode': 'AUTO'}
    lines = _preparation_lines(db, preparation)
    quote = build_quote(db, preparation, lines, _prep_actor(db, preparation), intent)
    preparation.quote_id = quote.id
    touch(preparation)
    audit(db, actor, 'PREPARATION_CHECKOUT_ADOPTED', 'PREPARATION', preparation.id,
          data={'accountId': account.id, 'quoteId': quote.id})
    return ok(preparation_view(db, preparation))


@router.post('/preparations/{preparation_id}/withdraw-checkout', response_model=Envelope[PreparationView])
def withdraw_checkout(
    preparation_id: str,
    payload: ConfirmCheckoutInput,
    db=Depends(get_db),
    actor: Actor = Depends(actor_dep),
):
    from .growth import build_quote

    body = payload
    preparation = lock(db, Preparation, preparation_id, body.expected_version)
    _preparation_for(db, actor, preparation_id)
    if _is_expired(preparation.expires_at):
        raise DomainError('PREPARATION_EXPIRED', '准备草稿已过期，请重新准备', 410)
    if preparation.replaces_order_id:
        raise DomainError('CHECKOUT_WITHDRAW_NOT_APPLICABLE', '修改既有订单时不能撤回结算身份', 409)
    if not preparation.checkout_account_id:
        raise DomainError('CHECKOUT_NOT_ADOPTED', '该准备单尚未绑定账号', 409)
    if not (preparation.data or {}).get('checkoutGrant'):
        raise DomainError(
            'CHECKOUT_NOT_ADOPTED',
            '该准备单直接以账号结算，不能撤回为游客结算',
            409,
        )
    if actor.account_id != preparation.checkout_account_id:
        raise DomainError('FORBIDDEN', '只有绑定账号本人可以撤回', 403)
    if db.scalar(select(Order.id).where(Order.preparation_id == preparation.id)):
        raise DomainError('ORDER_EXISTS', '订单已创建，不能撤回结算身份', 409)
    data = dict(preparation.data or {})
    original_principal_id = data.get('originalPrincipalId') or preparation.principal_id
    original_principal = db.get(Principal, original_principal_id)
    if original_principal is not None and original_principal.account_id:
        raise DomainError('OWNERSHIP_FIXED', '游客文件已并入账号，不能撤销为游客结算', 409)
    if actor.guest_principal_id != original_principal_id and actor.principal_id != original_principal_id:
        raise DomainError('GUEST_SESSION_REQUIRED', '撤回账号结算需同时持有原游客会话', 403)
    data.pop('checkoutGrant', None)
    data.pop('originalPrincipalId', None)
    data['checkoutAuthKind'] = 'GUEST'
    preparation.data = data
    preparation.principal_id = original_principal_id
    preparation.checkout_account_id = None
    db.flush()
    intent = data.get('promotionIntent') or {'selectionMode': 'AUTO'}
    lines = _preparation_lines(db, preparation)
    quote = build_quote(db, preparation, lines, _prep_actor(db, preparation), intent)
    preparation.quote_id = quote.id
    touch(preparation)
    audit(db, actor, 'PREPARATION_CHECKOUT_WITHDRAWN', 'PREPARATION', preparation.id,
          data={'quoteId': quote.id})
    return ok(preparation_view(db, preparation))


# ---------------------------------------------------------------------------
# Preparation processing (real worker; owns its transaction)
# ---------------------------------------------------------------------------


def _artifact_settings(
    db,
    preparation: Preparation,
    spec: dict,
    *,
    sequence: int,
    segment: dict | None,
    pages: list[int],
    pages_count: int,
    sheets: int,
    blank_backs: int,
    copies: int,
    color_mode: str,
    sides: str,
    flip: str,
    extra: dict | None = None,
) -> dict:
    settings = get_settings()
    toolchain = pdf_engine.toolchain_versions(settings.soffice_path, settings.ghostscript_path)
    base = {
        'clientFileId': spec['clientFileId'],
        'segmentId': segment['clientSegmentId'] if segment else 'photo',
        'range': _range_label(pages) if segment else '照片',
        'rangeModel': pages if segment else [1],
        'copies': copies,
        'colorMode': color_mode,
        'sides': sides,
        'flip': flip,
        'blankBacks': blank_backs,
        'contentRevision': preparation.content_revision,
        'sourcePages': pages,
        'inputSha256': (preparation.data or {}).get('inputSha256'),
        'toolchain': toolchain,
        'sequence': sequence,
        'isReprint': False,
        'nonBillable': False,
        'createdAt': utcnow().isoformat(),
    }
    if extra:
        base.update(extra)
    hash_payload = {key: value for key, value in base.items() if key != 'createdAt'}
    base['settingsHash'] = canonical_hash(hash_payload)
    return base


def _build_artifact_plan(db, preparation: Preparation, workdir: Path) -> list[dict]:
    settings = get_settings()
    specs = (preparation.data or {}).get('files') or []
    plans: list[dict] = []
    sequence = 0

    def store(path: Path, settings_payload: dict, page_map, pages_count, sheets, blank_backs):
        sha = pdf_engine.sha256_file(path)
        settings_payload = dict(settings_payload)
        settings_payload['artifactSha256'] = sha
        settings_payload['settingsHash'] = canonical_hash(
            {key: value for key, value in settings_payload.items()
             if key not in ('artifactSha256', 'settingsHash', 'createdAt')}
        )
        return {
            'path': path,
            'sha256': sha,
            'settings': settings_payload,
            'pageMap': page_map,
            'pages': pages_count,
            'sheets': sheets,
            'blankBacks': blank_backs,
        }

    for spec in specs:
        if spec.get('kind') == 'DOCUMENT':
            normalized = db.get(Asset, spec.get('normalizedAssetId'))
            if normalized is None or normalized.status != 'READY':
                raise pdf_engine.EngineError('NORMALIZED_ASSET_MISSING',
                                             '标准化 PDF 不可用，请重新上传', retryable=True)
            source = _storage_path(normalized.storage_key)
            for segment in spec.get('segments') or []:
                sequence += 1
                target = workdir / f'artifact-{sequence}.pdf'
                build = pdf_engine.build_document_artifact(
                    source,
                    target,
                    segment_id=segment['clientSegmentId'],
                    pages=segment['pages'],
                    sides=segment['sides'],
                    copies=int(segment['copies']),
                    color_mode=segment['colorMode'],
                    flip=segment['flip'],
                )
                warnings: list[dict] = []
                final_path = build.path
                if segment['colorMode'] == 'BW':
                    gray_path = workdir / f'artifact-{sequence}-gray.pdf'
                    gray = pdf_engine.convert_pdf_to_grayscale(
                        final_path, gray_path,
                        gs_path=settings.ghostscript_path,
                        timeout=settings.conversion_timeout_seconds,
                    )
                    final_path = gray.path
                    if gray.warning:
                        warnings.append(gray.warning)
                payload = _artifact_settings(
                    db, preparation, spec,
                    sequence=sequence, segment=segment, pages=segment['pages'],
                    pages_count=build.pages, sheets=build.sheets,
                    blank_backs=build.blank_backs, copies=int(segment['copies']),
                    color_mode=segment['colorMode'], sides=segment['sides'],
                    flip=segment['flip'],
                    extra={'warnings': warnings, 'rangeModel': segment['pages']},
                )
                plan = store(final_path, payload, build.page_map, build.pages,
                             build.sheets, build.blank_backs)
                plans.append(plan)
        else:
            asset = db.get(Asset, spec['assetId'])
            if asset is None:
                raise pdf_engine.EngineError('ASSET_NOT_FOUND', '照片源文件不可用', retryable=True)
            sequence += 1
            target = workdir / f'artifact-{sequence}.pdf'
            paper = pdf_engine.PaperSpec(
                float(spec['paper']['widthMm']),
                float(spec['paper']['heightMm']),
                dict(spec['paper']['marginsMm']),
            )
            render = pdf_engine.render_photo_pdf(
                _storage_path(asset.storage_key),
                target,
                copies=int(spec['copies']),
                orientation=spec['orientation'],
                fit=spec['fit'],
                rotation_degrees=int(spec['rotationDegrees']),
                crop=spec.get('crop'),
                paper=paper,
                dpi=300,
            )
            page_map = tuple(
                {
                    'pdfPageIndex': copy,
                    'pdfPage': copy,
                    'sourcePageIndex': 1,
                    'sourcePage': 1,
                    'copyIndex': copy,
                    'sheetIndex': copy,
                    'sheetOrdinal': copy,
                    'sheetId': f'c{copy}-s{copy}',
                    'side': 'FRONT',
                    'blank': False,
                }
                for copy in range(1, render.copies + 1)
            )
            payload = _artifact_settings(
                db, preparation, spec,
                sequence=sequence, segment=None, pages=[1],
                pages_count=render.pages, sheets=render.copies, blank_backs=0,
                copies=render.copies, color_mode='COLOR', sides='SINGLE',
                flip='NONE',
                extra={
                    'orientation': render.orientation,
                    'fit': render.fit,
                    'rotationDegrees': render.rotation_degrees,
                    'crop': render.crop,
                    'cropAdjusted': render.crop_adjusted,
                    'paperWidthMm': render.width_mm,
                    'paperHeightMm': render.height_mm,
                    'printWidthMm': render.print_width_mm,
                    'printHeightMm': render.print_height_mm,
                    'marginsMm': render.margins_mm,
                    'sourceWidth': render.source_width,
                    'sourceHeight': render.source_height,
                    'exifOrientation': render.exif_orientation,
                    'dpi': render.dpi,
                    'rangeModel': [1],
                    'warnings': [],
                },
            )
            plans.append(store(render.path, payload, page_map, render.pages,
                               render.copies, 0))
    return plans


def process_preparation(db, preparation_id: str) -> None:
    """Generate every final artifact and the authoritative quote.

    Like :func:`process_asset` this owns a separate worker transaction so a
    cancel request stays visible while a long conversion runs.
    """
    from .db import SessionLocal

    session = SessionLocal()
    try:
        _process_preparation(session, preparation_id)
    finally:
        session.close()


def _process_preparation(db, preparation_id: str) -> None:
    """Worker implementation; owns commits on the supplied session."""
    preparation = lock(db, Preparation, preparation_id)
    data = dict(preparation.data or {})
    if preparation.status in ('READY', 'CANCELLED'):
        db.rollback()
        return
    if preparation.cancel_requested or data.get('cancelRequested'):
        preparation.status = 'CANCELLED'
        data.update(cancelledAt=utcnow().isoformat(), attemptToken=None)
        preparation.data = data
        touch(preparation)
        db.commit()
        return
    if _is_expired(preparation.expires_at):
        preparation.status = 'FAILED'
        data.update(failureCode='EXPIRED', failureMessage='准备草稿已过期，请重新准备',
                    retryable=False, attemptToken=None)
        preparation.data = data
        touch(preparation)
        db.commit()
        return
    if not _source_grant_valid(db, preparation):
        preparation.status = 'FAILED'
        data.update(
            failureCode='SOURCE_GRANT_REVOKED',
            failureMessage='原订单委托授权已撤销或过期，请重新取得授权',
            retryable=False, attemptToken=None, failedAt=utcnow().isoformat(),
        )
        preparation.data = data
        touch(preparation)
        db.commit()
        return
    token = uuid.uuid4().hex
    attempts = int(data.get('attempts') or 0) + 1
    data.update(attemptToken=token, attempts=attempts, startedAt=utcnow().isoformat(),
                failureCode=None, failureMessage=None)
    preparation.data = data
    preparation.status = 'PROCESSING'
    touch(preparation)
    db.commit()

    workdir = _root() / 'tmp' / f'prep-{preparation.id}-{uuid.uuid4().hex}'
    workdir.mkdir(parents=True, exist_ok=True)
    published: list[tuple[str, str]] = []
    try:
        plans = _build_artifact_plan(db, preparation, workdir)
        max_final = int((data.get('limits') or {}).get('maxFinalMb') or 500) * BYTES_PER_MB
        for plan in plans:
            if plan['path'].stat().st_size > max_final:
                raise pdf_engine.EngineError('FINAL_PDF_TOO_LARGE', '最终 PDF 超过大小上限',
                                             retryable=False)
        # Verify all files before any metadata becomes visible.
        for plan in plans:
            if pdf_engine.sha256_file(plan['path']) != plan['sha256']:
                raise pdf_engine.EngineError('ARTIFACT_VERIFY_FAILED', '最终 PDF 校验失败',
                                             retryable=True)

        db.rollback()
        current = lock(db, Preparation, preparation_id)
        current_data = dict(current.data or {})
        if current_data.get('attemptToken') != token or current.status == 'READY':
            db.rollback()
            return
        if current.cancel_requested:
            current.status = 'CANCELLED'
            current_data.update(cancelledAt=utcnow().isoformat(), attemptToken=None)
            current.data = current_data
            touch(current)
            db.commit()
            return
        if _is_expired(current.expires_at):
            current.status = 'FAILED'
            current_data.update(
                failureCode='EXPIRED', failureMessage='准备草稿已过期，请重新准备',
                retryable=False, attemptToken=None, failedAt=utcnow().isoformat(),
            )
            current.data = current_data
            touch(current)
            db.commit()
            return
        if not _source_grant_valid(db, current):
            current.status = 'FAILED'
            current_data.update(
                failureCode='SOURCE_GRANT_REVOKED',
                failureMessage='原订单委托授权已撤销或过期，请重新取得授权',
                retryable=False, attemptToken=None, failedAt=utcnow().isoformat(),
            )
            current.data = current_data
            touch(current)
            db.commit()
            return
        final_days = _expiry_for('PRINT_FINAL', db)
        for plan in plans:
            sha = plan['sha256']
            asset_id = new_id()
            key = _artifact_key(asset_id, sha)
            pdf_engine.publish_file(plan['path'], _storage_path(key), sha)
            published.append((plan['path'].as_posix(), key))
            asset = Asset(
                id=asset_id,
                principal_id=current.principal_id,
                purpose='PRINT_FINAL',
                name=f"final-{plan['settings']['clientFileId']}-{plan['settings']['segmentId']}.pdf",
                mime='application/pdf',
                size=_storage_path(key).stat().st_size,
                sha256=sha,
                storage_key=key,
                status='READY',
                pages=plan['pages'],
                data={
                    'kind': 'PDF',
                    'artifact': True,
                    'settingsHash': plan['settings']['settingsHash'],
                    'toolchain': plan['settings'].get('toolchain'),
                },
                expires_at=final_days,
            )
            db.add(asset)
            db.flush()
            artifact = Artifact(
                preparation_id=current.id,
                asset_id=asset.id,
                client_file_id=plan['settings']['clientFileId'],
                client_segment_id=plan['settings']['segmentId'],
                sha256=sha,
                pages=plan['pages'],
                sheets=plan['sheets'],
                settings=plan['settings'],
                page_map=list(plan['pageMap']),
            )
            db.add(artifact)
            db.flush()
        lines = _preparation_lines(db, current)
        from .growth import build_quote

        quote = build_quote(db, current, lines, _prep_actor(db, current),
                            current_data.get('promotionIntent') or {'selectionMode': 'AUTO'})
        current_data = dict(current.data or {})
        warnings = [
            warning
            for plan in plans
            for warning in (plan['settings'].get('warnings') or [])
        ]
        current_data.update(
            lines=lines,
            warnings=warnings,
            attemptToken=None,
            preparedAt=utcnow().isoformat(),
            manifest=[
                {
                    'artifactId': None,
                    'sha256': plan['sha256'],
                    'settingsHash': plan['settings']['settingsHash'],
                    'pages': plan['pages'],
                    'sheets': plan['sheets'],
                }
                for plan in plans
            ],
        )
        current.quote_id = quote.id
        current.status = 'READY'
        current.data = current_data
        touch(current)
        db.commit()
    except pdf_engine.EngineError as error:
        _fail_preparation(db, preparation_id, token, error.code, error.message, error.retryable)
    except DomainError as error:
        retryable = error.code in (
            'PRICE_NOT_CONFIGURED',
            'FILE_PROCESSING_PAUSED',
            'SERVICE_LIMIT_UNAVAILABLE',
            'OFFICE_CONVERTER_MISSING',
        )
        _fail_preparation(db, preparation_id, token, error.code, error.message, retryable)
    except Exception:
        _fail_preparation(db, preparation_id, token, 'PREPARATION_FAILED',
                          '最终 PDF 生成失败，请稍后重试', True)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Reprint artifacts (called by production inside its locked transaction)
# ---------------------------------------------------------------------------


def create_reprint_artifact(db, task: PrintTask, sheet_ids, actor: Actor) -> Artifact:
    """Build a non-billable PDF containing complete selected physical sheets."""
    source_artifact = db.get(Artifact, task.artifact_id)
    if source_artifact is None:
        raise DomainError('STALE_PRINT_ARTIFACT', '打印产物不存在或已失效', 409)
    source_asset = db.get(Asset, source_artifact.asset_id)
    if source_asset is None or source_asset.status != 'READY':
        raise DomainError('STALE_PRINT_ARTIFACT', '打印产物文件不可用', 409)
    selected = list(dict.fromkeys(str(item) for item in (sheet_ids or [])))
    if not selected:
        raise DomainError('REPRINT_SHEETS_REQUIRED', '请选择需要补打的实体纸张', 422)
    workdir = _root() / 'tmp' / f'reprint-{task.id}-{uuid.uuid4().hex}'
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        target = workdir / 'reprint.pdf'
        source_path = _storage_path(source_asset.storage_key)
        try:
            built = pdf_engine.build_sheet_reprint_pdf(
                source_path, source_artifact.page_map or [], selected, target
            )
        except pdf_engine.EngineError as error:
            raise _engine_error(error) from error
        reprint_asset_id = new_id()
        key = _artifact_key(reprint_asset_id, built.sha256)
        pdf_engine.publish_file(built.path, _storage_path(key), built.sha256)
        selected_rows = []
        for entry in source_artifact.page_map or []:
            if str(entry.get('sheetId')) in selected:
                selected_rows.append(dict(entry))
        page_map = []
        for index, row in enumerate(selected_rows, start=1):
            row['pdfPageIndex'] = index
            row['pdfPage'] = index
            page_map.append(row)
        source_settings = dict(source_artifact.settings or {})
        settings = {
            **source_settings,
            'isReprint': True,
            'nonBillable': True,
            'reprintOfTaskId': task.id,
            'reprintOfArtifactId': source_artifact.id,
            'selectedSheets': selected,
            'blankBacks': sum(1 for row in page_map if row.get('blank')),
            'contentRevision': source_settings.get('contentRevision') or 1,
            'createdAt': utcnow().isoformat(),
        }
        settings['settingsHash'] = canonical_hash(
            {key: value for key, value in settings.items() if key != 'createdAt'}
        )
        asset = Asset(
            id=reprint_asset_id,
            principal_id=source_asset.principal_id,
            staff_id=actor.staff_id if isinstance(actor, Actor) else None,
            purpose='PRINT_REPRINT',
            name=f'reprint-{task.id}.pdf',
            mime='application/pdf',
            size=_storage_path(key).stat().st_size,
            sha256=built.sha256,
            storage_key=key,
            status='READY',
            pages=built.pages,
            data={
                'kind': 'PDF',
                'reprint': True,
                'sourceArtifactId': source_artifact.id,
                'sheetIds': selected,
                'settingsHash': settings['settingsHash'],
            },
            expires_at=_expiry_for('PRINT_REPRINT', db),
        )
        db.add(asset)
        db.flush()
        artifact = Artifact(
            preparation_id=source_artifact.preparation_id,
            asset_id=asset.id,
            client_file_id=source_artifact.client_file_id,
            client_segment_id=source_artifact.client_segment_id,
            sha256=built.sha256,
            pages=built.pages,
            sheets=built.sheets,
            settings=settings,
            page_map=page_map,
        )
        db.add(artifact)
        db.flush()
        audit(db, actor, 'REPRINT_ARTIFACT_CREATED', 'ARTIFACT', artifact.id,
              data={'taskId': task.id, 'sourceArtifactId': source_artifact.id,
                    'sheetIds': selected})
        return artifact
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
