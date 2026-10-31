"""Strict DTOs and pure dependency rules for the production module.

The rules here are deliberately free of database/session access: routes and
``refresh_production`` call them so the same decisions apply from HTTP, batch
delivery guards and tests.  No pricing, payment or order-view DTO lives here;
Codex commerce remains the single order data source.
"""
from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import AliasChoices, Field, field_validator, model_validator

from app.core import DTO

TASK_STATUSES = ('PENDING', 'ATTEMPTED', 'COMPLETED', 'EXCEPTION', 'VOIDED')
OPEN_TASK_STATUSES = ('PENDING', 'ATTEMPTED', 'EXCEPTION')
BINDING_STATUSES = ('NOT_REQUIRED', 'WAITING_PRINT', 'READY', 'COMPLETED')
PACKING_STATUSES = ('BLOCKED', 'READY', 'PACKED')
ATTEMPT_OUTCOMES = ('COMPLETED', 'FAILED', 'CANCELLED')
REPRINT_MODES = ('WHOLE', 'SHEETS')
CORRECTION_ACTIONS = ('REOPEN_PRINT', 'REOPEN_BINDING', 'UNPACK')
CORRECTION_TARGETS = ('PRINT_TASK', 'BINDING_TASK', 'PACKING_TASK', 'ORDER')

# Explicit whitelist: action -> accepted target types.  There is no generic
# status setter and no action may be sent with an arbitrary status payload.
CORRECTION_WHITELIST: dict[str, tuple[str, ...]] = {
    'REOPEN_PRINT': ('PRINT_TASK',),
    'REOPEN_BINDING': ('BINDING_TASK',),
    'UNPACK': ('PACKING_TASK', 'ORDER'),
}

_SHEET_ID = re.compile(r'^c(\d+)-s(\d+)$')


def _clean_reason(value: str) -> str:
    value = (value or '').strip()
    if not value:
        raise ValueError('请填写原因')
    return value


def _range_page_count(value, default: int) -> int:
    """Count selected source pages in a stored range string/list."""
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        return len(value) or default
    text = str(value).strip()
    if not text:
        return default
    total = 0
    for part in text.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            start, _, end = part.partition('-')
            try:
                start, end = int(start), int(end)
            except ValueError:
                return default
            if end < start:
                return default
            total += end - start + 1
        else:
            try:
                int(part)
            except ValueError:
                return default
            total += 1
    return total or default


def _int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _page(value):
    if value is None or value == '':
        return None
    try:
        page = int(value)
    except (TypeError, ValueError):
        return None
    return page if page > 0 else None


def sheet_catalog(artifact, task_settings: dict | None = None) -> list[dict]:
    """Physical sheet manifest for a print task.

    Prefers the immutable ``Artifact.page_map`` written by media.  A stored
    page map is the only authority for copy/sheet/source-page pairing; the
    fallback derives the same 1-based ``c{copy}-s{sheet}`` identities from the
    artifact's immutable page/sheet counts and stored print settings.
    """
    settings = dict(task_settings or {})
    artifact_settings = dict(getattr(artifact, 'settings', None) or {})
    settings = {**artifact_settings, **settings}
    sides = str(settings.get('sides') or 'SINGLE').upper()
    copies = max(1, _int(settings.get('copies'), 1))

    page_map = getattr(artifact, 'page_map', None) or []
    grouped: dict[tuple[int, int], dict] = {}
    if isinstance(page_map, list) and any(isinstance(row, dict) for row in page_map):
        for row in page_map:
            if not isinstance(row, dict):
                continue
            copy = max(1, _int(row.get('copy', row.get('copyIndex', row.get('copy_index'))), 1))
            sheet = _int(
                row.get('sheet', row.get('sheetIndex', row.get('sheet_index'))),
                0,
            )
            if sheet < 1:
                continue
            side = str(row.get('side') or row.get('face') or '').upper()
            page = _page(
                next(
                    (
                        row[key]
                        for key in (
                            'sourcePageIndex',
                            'sourcePage',
                            'source_page',
                            'source',
                            'contentPage',
                            'page',
                            'sourceIndex',
                        )
                        if row.get(key) is not None
                    ),
                    None,
                )
            )
            pdf_page = _page(
                next(
                    (
                        row[key]
                        for key in ('pdfPageIndex', 'pdfPage', 'pdf_page', 'index', 'pdfIndex')
                        if row.get(key) is not None
                    ),
                    None,
                )
            )
            entry = grouped.setdefault(
                (copy, sheet),
                {'frontPage': None, 'backPage': None, 'pdfFrontPage': None, 'pdfBackPage': None},
            )
            if side in ('FRONT', 'F', 'RECTO') or (not side and entry['frontPage'] is None):
                entry['frontPage'] = page
                entry['pdfFrontPage'] = pdf_page
            elif side in ('BACK', 'B', 'VERSO'):
                entry['backPage'] = page
                entry['pdfBackPage'] = pdf_page
        catalog = []
        for (copy, sheet), entry in sorted(grouped.items()):
            front, back = entry['frontPage'], entry['backPage']
            catalog.append(
                {
                    'sheetId': f'c{copy}-s{sheet}',
                    'copy': copy,
                    'sheet': sheet,
                    'frontPage': front,
                    'backPage': back,
                    'blankBack': sides == 'DUPLEX' and back is None and front is not None,
                    'pdfFrontPage': entry.get('pdfFrontPage'),
                    'pdfBackPage': entry.get('pdfBackPage'),
                }
            )
        if catalog:
            return catalog

    declared_sheets = _int(getattr(artifact, 'sheets', 0), 0)
    declared_pages = _int(getattr(artifact, 'pages', 0), 0)
    per_copy_pdf = max(1, math.ceil(declared_pages / copies)) if declared_pages else 0
    explicit_pages = (
        settings.get('rangeModel')
        or settings.get('sourcePages')
        or settings.get('pageCount', settings.get('pages'))
    )
    if explicit_pages is None:
        explicit_pages = settings.get('range')
    content_pages = _range_page_count(explicit_pages, per_copy_pdf)
    content_pages = max(1, content_pages) if per_copy_pdf else 0
    if sides == 'DUPLEX':
        derived_sheets = math.ceil(content_pages / 2) if content_pages else 0
    else:
        derived_sheets = content_pages
    if declared_sheets > 0 and copies > 0 and declared_sheets % copies == 0:
        sheets_per_copy = declared_sheets // copies
    else:
        sheets_per_copy = derived_sheets
    catalog = []
    for copy in range(1, copies + 1):
        for sheet in range(1, max(0, sheets_per_copy) + 1):
            if sides == 'DUPLEX':
                front = 2 * sheet - 1
                back = 2 * sheet
                front = front if front <= content_pages else None
                back = back if back <= content_pages else None
                blank = back is None and front is not None
            else:
                front = sheet if sheet <= content_pages else None
                back = None
                blank = False
            catalog.append(
                {
                    'sheetId': f'c{copy}-s{sheet}',
                    'copy': copy,
                    'sheet': sheet,
                    'frontPage': front,
                    'backPage': back,
                    'blankBack': blank,
                    'pdfFrontPage': None,
                    'pdfBackPage': None,
                }
            )
    return catalog


def validate_sheet_selection(catalog: list[dict], sheet_ids: list[str]):
    """Return (ordered sheet rows, error codes) for a SHEETS reprint request."""
    if not sheet_ids:
        return [], ['SHEET_SELECTION_REQUIRED']
    by_id = {row['sheetId']: row for row in catalog}
    wanted = list(dict.fromkeys(sheet_ids))
    if any(sheet_id not in by_id for sheet_id in wanted):
        return [], ['SHEET_NOT_IN_TASK']
    selected = [row for row in catalog if row['sheetId'] in wanted]
    return selected, []


def aggregate_file_print_status(statuses: list[str]) -> str:
    """File-level print projection matching the existing frontend aggregation."""
    if not statuses:
        return 'PENDING'
    if all(status == 'COMPLETED' for status in statuses):
        return 'COMPLETED'
    if 'ATTEMPTED' in statuses:
        return 'ATTEMPTED'
    if 'EXCEPTION' in statuses:
        return 'EXCEPTION'
    if 'VOIDED' in statuses and all(status == 'VOIDED' for status in statuses):
        return 'VOIDED'
    return 'PENDING'


def binding_status_for(*, has_binding: bool, print_status: str, current: str | None) -> str:
    if not has_binding:
        return 'NOT_REQUIRED'
    if print_status == 'COMPLETED':
        return 'COMPLETED' if current == 'COMPLETED' else 'READY'
    return 'WAITING_PRINT'


def packing_status_for(
    *,
    paid: bool,
    print_ok: bool,
    binding_ok: bool,
    open_attempts: int,
    blocking_exception: bool,
) -> str:
    if not paid:
        return 'BLOCKED'
    if blocking_exception or open_attempts > 0 or not print_ok or not binding_ok:
        return 'BLOCKED'
    # READY means "waiting for a human to check and pack".  It never equals
    # PACKED: only the explicit pack action may record the packed fact.
    return 'READY'


def next_order_status(current: str, packing: str) -> str:
    if current not in ('PENDING_PRINT', 'READY_FOR_DELIVERY'):
        # DELIVERING/DELIVERED/CANCELLED are never rewritten by a projection.
        return current
    return 'READY_FOR_DELIVERY' if packing == 'PACKED' else 'PENDING_PRINT'


def checklist_version_matches(
    current_content_revision: int,
    *,
    sent_version: int,
    packing_manifest_revision: int,
) -> bool:
    return sent_version == current_content_revision == packing_manifest_revision


def checklist_errors(required_file_ids: list[str], checks: list['PackCheck']) -> list[str]:
    required = list(dict.fromkeys(required_file_ids))
    seen_order: list[str] = []
    unknown = False
    duplicate = False
    for check in checks:
        if check.file_id not in required:
            unknown = True
        if check.file_id in seen_order:
            duplicate = True
        else:
            seen_order.append(check.file_id)
    errors = []
    if unknown:
        errors.append('CHECKLIST_UNKNOWN_FILE')
    if duplicate:
        errors.append('CHECKLIST_DUPLICATE')
    if any(file_id not in seen_order for file_id in required):
        errors.append('CHECKLIST_FILE_MISSING')
    if any(not check.confirmed for check in checks if check.file_id in required):
        errors.append('CHECKLIST_NOT_CONFIRMED')
    return errors


def correction_allowed(target_type: str, action: str) -> bool:
    return target_type in CORRECTION_WHITELIST.get(action, ())


# ---------------------------------------------------------------------------
# Request DTOs (strict, camelCase, extra fields rejected)
# ---------------------------------------------------------------------------


class PrintAttemptInput(DTO):
    expected_version: int
    artifact_id: str = Field(min_length=1, max_length=64)


class PrintResultInput(DTO):
    expected_version: int
    outcome: Literal['COMPLETED', 'FAILED', 'CANCELLED']
    reason: str = Field(default='', max_length=1000)

    @model_validator(mode='after')
    def _reason_required(self):
        self.reason = (self.reason or '').strip()
        if self.outcome != 'COMPLETED' and not self.reason:
            raise ValueError('请填写失败或取消原因')
        return self


class ReprintInput(DTO):
    expected_version: int
    mode: Literal['WHOLE', 'SHEETS']
    sheet_ids: list[str] = Field(default_factory=list, max_length=2000)
    reason: str = Field(max_length=1000)

    @field_validator('reason')
    @classmethod
    def _reason(cls, value: str) -> str:
        return _clean_reason(value)

    @field_validator('sheet_ids')
    @classmethod
    def _sheet_ids(cls, value: list[str]) -> list[str]:
        return [str(item).strip() for item in value if str(item).strip()]

    @model_validator(mode='after')
    def _sheets_required(self):
        if self.mode == 'SHEETS' and not self.sheet_ids:
            raise ValueError('指定纸张补打必须选择实体纸张')
        return self

    def unique_sheet_ids(self) -> list[str]:
        return list(dict.fromkeys(self.sheet_ids))


class ActualStartInput(DTO):
    expected_version: int
    reason: str = Field(default='', max_length=1000)

    @field_validator('reason')
    @classmethod
    def _reason(cls, value: str) -> str:
        return (value or '').strip()


class BindingCompleteInput(DTO):
    expected_version: int


class PackCheck(DTO):
    file_id: str = Field(min_length=1)
    confirmed: bool


class PackInput(DTO):
    expected_version: int
    checklist_version: int
    checks: list[PackCheck] = Field(min_length=1, max_length=500)


class CorrectionInput(DTO):
    target_type: Literal['PRINT_TASK', 'BINDING_TASK', 'PACKING_TASK', 'ORDER']
    target_id: str = Field(min_length=1)
    expected_version: int
    action: Literal['REOPEN_PRINT', 'REOPEN_BINDING', 'UNPACK']
    reason: str = Field(max_length=1000)
    # The documented correction contract uses actionPayload; earlier callers
    # used payload.  No action may apply an arbitrary status from either field.
    action_payload: dict | None = Field(
        default=None,
        validation_alias=AliasChoices('actionPayload', 'payload'),
        serialization_alias='actionPayload',
    )

    @field_validator('reason')
    @classmethod
    def _reason(cls, value: str) -> str:
        return _clean_reason(value)

    @model_validator(mode='after')
    def _whitelist(self):
        if not correction_allowed(self.target_type, self.action):
            raise ValueError('该纠错动作不接受此对象类型')
        return self


# ---------------------------------------------------------------------------
# Response DTOs
# ---------------------------------------------------------------------------


class SheetDTO(DTO):
    sheet_id: str
    copy_index: int
    sheet: int
    front_page: int | None = None
    back_page: int | None = None
    blank_back: bool = False


class PrintAttemptDTO(DTO):
    attempt_id: str
    task_id: str
    artifact_id: str
    outcome: str | None = None
    reason: str = ''
    reason_kind: str | None = None
    staff_id: str = ''
    created_at: str
    completed_at: str | None = None


class ReprintRecordDTO(DTO):
    attempt_id: str
    mode: str
    sheet_ids: list[str] = Field(default_factory=list)
    reason: str = ''
    artifact_id: str = ''
    created_at: str = ''
    exception_id: str | None = None


class PrintTaskDTO(DTO):
    id: str
    order_id: str
    pickup_code: str = ''
    file_id: str
    file_name: str = ''
    kind: str = 'DOCUMENT'
    binding_type: str = 'NONE'
    status: str
    active: bool
    artifact_id: str
    artifact_sha256: str = ''
    pages: int = 0
    sheet_count: int = 0
    copies: int = 1
    sides: str = 'SINGLE'
    color_mode: str = 'BW'
    page_range: str = ''
    flip: str = 'NONE'
    open_attempt_id: str | None = None
    last_attempt: PrintAttemptDTO | None = None
    reprints: list[ReprintRecordDTO] = Field(default_factory=list)
    order_status: str = ''
    order_version: int = 0
    sheet_catalog: list[SheetDTO] = Field(default_factory=list)


class PrintQueueDTO(DTO):
    tasks: list[PrintTaskDTO]
    counts: dict[str, int] = Field(default_factory=dict)
    next_task_id: str | None = None


class AttemptResultDTO(DTO):
    attempt_id: str
    task_id: str
    outcome: str
    task_status: str
    order_status: str
    packing_status: str
    checklist_version: int
    order_version: int


class ReprintResultDTO(DTO):
    task_id: str
    mode: str
    attempt_id: str
    artifact_id: str
    sheet_ids: list[str] = Field(default_factory=list)
    task_status: str
    non_billable: bool = True
    order_version: int


class ProductionStartDTO(DTO):
    order_id: str
    actual_started_at: str
    order_status: str
    version: int


class BindingTaskDTO(DTO):
    id: str
    order_id: str
    pickup_code: str = ''
    file_id: str
    file_name: str = ''
    binding_type: str = 'NONE'
    status: str
    print_status: str = 'PENDING'
    order_status: str = ''
    version: int = 1


class BindingQueueDTO(DTO):
    tasks: list[BindingTaskDTO]


class ChecklistItemDTO(DTO):
    file_id: str
    name: str = ''
    kind: str = 'DOCUMENT'
    binding_type: str = 'NONE'
    print_status: str = 'PENDING'
    binding_status: str = 'NOT_REQUIRED'
    task_ids: list[str] = Field(default_factory=list)
    open_attempt_ids: list[str] = Field(default_factory=list)
    complete: bool = False


class ProductionBlockerDTO(DTO):
    code: str
    message: str
    file_id: str | None = None
    task_id: str | None = None


class ProductionChecklistDTO(DTO):
    version: int
    generated_at: str = ''
    packing_status: str = 'BLOCKED'
    items: list[ChecklistItemDTO] = Field(default_factory=list)
    blockers: list[ProductionBlockerDTO] = Field(default_factory=list)


class PackingTaskDTO(DTO):
    id: str
    order_id: str
    pickup_code: str = ''
    status: str
    checklist_version: int = 0
    order_status: str = ''
    checklist: ProductionChecklistDTO
    version: int = 1


class PackingQueueDTO(DTO):
    tasks: list[PackingTaskDTO]


class PackResultDTO(DTO):
    order_id: str
    packing_status: str
    status: str
    version: int
    checklist_version: int
    packed_at: str
    checked_file_ids: list[str] = Field(default_factory=list)


class CorrectionResultDTO(DTO):
    action: str
    target_type: str
    target_id: str
    order_id: str
    task_status: str | None = None
    binding_status: str | None = None
    packing_status: str = ''
    order_status: str = ''
    version: int = 1
    delivery_blocked: bool = False


class ProductionSnapshotDTO(DTO):
    """Server truth merged into Codex commerce ``order_view``."""

    actual_started_at: str | None = None
    refund_locked: bool = False
    delivery_blocked: bool = False
    packing_status: str = 'BLOCKED'
    checklist_version: int = 1
    checklist: ProductionChecklistDTO
    files: list[dict] = Field(default_factory=list)
    tasks: list[PrintTaskDTO] = Field(default_factory=list)
    can_pack: bool = False
