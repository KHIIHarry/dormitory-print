"""Production module tests.

Two layers:
* DTO/domain rules that need no database (run first and drive the design).
* PostgreSQL integration tests for guards, projection and correction facts.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

import pytest
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# DTO / domain layer
# ---------------------------------------------------------------------------

from app.production_schemas import (  # noqa: E402
    ActualStartInput,
    BindingCompleteInput,
    CorrectionInput,
    PackCheck,
    PackInput,
    PrintAttemptInput,
    PrintResultInput,
    ReprintInput,
    aggregate_file_print_status,
    binding_status_for,
    checklist_errors,
    checklist_version_matches,
    correction_allowed,
    next_order_status,
    packing_status_for,
    sheet_catalog,
    validate_sheet_selection,
)


def _artifact(pages: int, sheets: int, *, copies: int, sides: str, page_map=None):
    from types import SimpleNamespace

    return SimpleNamespace(
        id='artifact-1',
        pages=pages,
        sheets=sheets,
        page_map=page_map or [],
        settings={'copies': copies, 'sides': sides, 'range': '1-7'},
        sha256='a' * 64,
    )


def test_print_attempt_input_matches_frontend_payload_and_rejects_extra():
    payload = PrintAttemptInput(expectedVersion=4, artifactId='artifact-1')
    assert payload.expected_version == 4
    assert payload.model_dump(by_alias=True) == {
        'expectedVersion': 4,
        'artifactId': 'artifact-1',
    }
    with pytest.raises(ValidationError):
        PrintAttemptInput(expectedVersion=4, artifactId='artifact-1', copies=3)
    with pytest.raises(ValidationError):
        PrintAttemptInput(expectedVersion=4)


def test_print_result_requires_reason_only_when_not_completed():
    assert PrintResultInput(expectedVersion=1, outcome='COMPLETED').reason == ''
    with pytest.raises(ValidationError):
        PrintResultInput(expectedVersion=1, outcome='FAILED', reason='   ')
    failed = PrintResultInput(expectedVersion=1, outcome='FAILED', reason='  卡纸  ')
    assert failed.reason == '卡纸'
    with pytest.raises(ValidationError):
        PrintResultInput(expectedVersion=1, outcome='DONE')


def test_reprint_requires_reason_and_sheet_ids_for_sheet_mode():
    whole = ReprintInput(expectedVersion=2, mode='WHOLE', reason='颜色错误')
    assert whole.sheet_ids == []
    with pytest.raises(ValidationError):
        ReprintInput(expectedVersion=2, mode='WHOLE', reason=' ')
    with pytest.raises(ValidationError):
        ReprintInput(expectedVersion=2, mode='SHEETS', reason='缺页')
    sheet = ReprintInput(
        expectedVersion=2,
        mode='SHEETS',
        sheetIds=['c1-s1', 'c1-s1', 'c2-s3'],
        reason='缺页',
    )
    # Duplicates are harmless; stable first-seen order is preserved.
    assert sheet.unique_sheet_ids() == ['c1-s1', 'c2-s3']


def test_pack_and_actual_start_and_correction_contracts():
    assert ActualStartInput(expectedVersion=1, reason='').expected_version == 1
    assert BindingCompleteInput(expectedVersion=1).expected_version == 1
    checks = [PackCheck(fileId='file-1', confirmed=True)]
    pack = PackInput(expectedVersion=3, checklistVersion=2, checks=checks)
    assert pack.checks[0].file_id == 'file-1'
    with pytest.raises(ValidationError):
        PackInput(expectedVersion=3, checklistVersion=2, checks=[])
    with pytest.raises(ValidationError):
        CorrectionInput(
            targetType='PRINT_TASK',
            targetId='task-1',
            expectedVersion=1,
            action='SET_STATUS_COMPLETED',
            reason='x',
        )
    with pytest.raises(ValidationError):
        CorrectionInput(
            targetType='PRINT_TASK',
            targetId='task-1',
            expectedVersion=1,
            action='UNPACK',
            reason='x',
        )
    correction = CorrectionInput(
        targetType='PRINT_TASK',
        targetId='task-1',
        expectedVersion=1,
        action='REOPEN_PRINT',
        reason='错页',
    )
    assert correction.reason == '错页'


def test_correction_whitelist_pairs():
    assert correction_allowed('PRINT_TASK', 'REOPEN_PRINT')
    assert correction_allowed('BINDING_TASK', 'REOPEN_BINDING')
    assert correction_allowed('PACKING_TASK', 'UNPACK')
    assert correction_allowed('ORDER', 'UNPACK')
    assert not correction_allowed('BINDING_TASK', 'REOPEN_PRINT')
    assert not correction_allowed('ORDER', 'REOPEN_PRINT')


def test_duplex_odd_copies_catalog_keeps_complete_front_back_pairs():
    catalog = sheet_catalog(_artifact(pages=24, sheets=12, copies=3, sides='DUPLEX'))
    assert len(catalog) == 12
    assert len({row['sheetId'] for row in catalog}) == 12
    by_id = {row['sheetId']: row for row in catalog}
    # 7 content pages -> 4 physical sheets per copy; last back is the system blank.
    for copy in (1, 2, 3):
        for sheet in (1, 2, 3):
            row = by_id[f'c{copy}-s{sheet}']
            assert row['frontPage'] == 2 * sheet - 1
            assert row['backPage'] == 2 * sheet
            assert row['blankBack'] is False
        last = by_id[f'c{copy}-s4']
        assert last['frontPage'] == 7
        assert last['backPage'] is None
        assert last['blankBack'] is True
    assert catalog[0]['copy'] == 1 and catalog[-1]['copy'] == 3


def test_single_sided_catalog_has_one_page_per_sheet():
    catalog = sheet_catalog(_artifact(pages=14, sheets=14, copies=2, sides='SINGLE'))
    assert len(catalog) == 14
    assert all(row['backPage'] is None for row in catalog)
    assert all(row['blankBack'] is False for row in catalog)
    assert [row['sheetId'] for row in catalog][:3] == ['c1-s1', 'c1-s2', 'c1-s3']
    assert catalog[7]['sheetId'] == 'c2-s1'


def test_catalog_prefers_immutable_artifact_page_map():
    page_map = [
        {'copy': 1, 'sheet': 1, 'side': 'FRONT', 'sourcePage': 3, 'pdfPage': 1},
        {'copy': 1, 'sheet': 1, 'side': 'BACK', 'sourcePage': 4, 'pdfPage': 2},
        {'copy': 1, 'sheet': 2, 'side': 'FRONT', 'sourcePage': 5, 'pdfPage': 3},
        {'copy': 2, 'sheet': 1, 'side': 'FRONT', 'sourcePage': 3, 'pdfPage': 4},
    ]
    catalog = sheet_catalog(
        _artifact(pages=4, sheets=3, copies=2, sides='DUPLEX', page_map=page_map)
    )
    assert [row['sheetId'] for row in catalog] == ['c1-s1', 'c1-s2', 'c2-s1']
    assert catalog[0]['frontPage'] == 3
    assert catalog[0]['backPage'] == 4
    assert catalog[1]['frontPage'] == 5
    assert catalog[1]['backPage'] is None
    assert catalog[1]['blankBack'] is True


def test_sheet_selection_rejects_unknown_and_requires_explicit_ids():
    catalog = sheet_catalog(_artifact(pages=24, sheets=12, copies=3, sides='DUPLEX'))
    selected, errors = validate_sheet_selection(catalog, [])
    assert errors == ['SHEET_SELECTION_REQUIRED'] and selected == []
    selected, errors = validate_sheet_selection(catalog, ['c1-s1', 'c9-s9'])
    assert errors == ['SHEET_NOT_IN_TASK']
    selected, errors = validate_sheet_selection(catalog, ['c3-s4', 'c1-s1', 'c1-s1'])
    assert errors == []
    # Output order follows the immutable artifact order, not request order.
    assert [row['sheetId'] for row in selected] == ['c1-s1', 'c3-s4']


def test_checklist_errors_require_exact_current_file_coverage():
    required = ['file-1', 'file-2']
    assert checklist_errors(required, [])[0] == 'CHECKLIST_FILE_MISSING'
    assert checklist_errors(
        required, [PackCheck(fileId='file-1', confirmed=True)]
    ) == ['CHECKLIST_FILE_MISSING']
    assert checklist_errors(
        required,
        [
            PackCheck(fileId='file-1', confirmed=True),
            PackCheck(fileId='file-1', confirmed=True),
        ],
    ) == ['CHECKLIST_DUPLICATE', 'CHECKLIST_FILE_MISSING']
    assert checklist_errors(
        required,
        [
            PackCheck(fileId='file-1', confirmed=True),
            PackCheck(fileId='file-2', confirmed=True),
            PackCheck(fileId='file-3', confirmed=True),
        ],
    ) == ['CHECKLIST_UNKNOWN_FILE']
    assert checklist_errors(
        required,
        [
            PackCheck(fileId='file-1', confirmed=True),
            PackCheck(fileId='file-2', confirmed=False),
        ],
    ) == ['CHECKLIST_NOT_CONFIRMED']
    assert checklist_errors(
        required,
        [
            PackCheck(fileId='file-1', confirmed=True),
            PackCheck(fileId='file-2', confirmed=True),
        ],
    ) == []


def test_checklist_uses_current_content_revision_as_server_version():
    assert checklist_version_matches(3, sent_version=3, packing_manifest_revision=3)
    assert not checklist_version_matches(3, sent_version=2, packing_manifest_revision=3)
    assert not checklist_version_matches(3, sent_version=3, packing_manifest_revision=2)


def test_dependency_status_rules_never_auto_complete_by_hand():
    assert aggregate_file_print_status(['COMPLETED', 'COMPLETED']) == 'COMPLETED'
    assert aggregate_file_print_status(['COMPLETED', 'ATTEMPTED']) == 'ATTEMPTED'
    assert aggregate_file_print_status(['COMPLETED', 'EXCEPTION']) == 'EXCEPTION'
    assert aggregate_file_print_status(['PENDING', 'EXCEPTION']) == 'EXCEPTION'
    assert aggregate_file_print_status([]) == 'PENDING'

    assert binding_status_for(has_binding=False, print_status='PENDING', current=None) == 'NOT_REQUIRED'
    assert binding_status_for(has_binding=True, print_status='PENDING', current=None) == 'WAITING_PRINT'
    assert binding_status_for(has_binding=True, print_status='ATTEMPTED', current=None) == 'WAITING_PRINT'
    assert binding_status_for(has_binding=True, print_status='COMPLETED', current='WAITING_PRINT') == 'READY'
    assert binding_status_for(has_binding=True, print_status='COMPLETED', current='COMPLETED') == 'COMPLETED'
    # Reopening a print invalidates the completed binding fact.
    assert binding_status_for(has_binding=True, print_status='ATTEMPTED', current='COMPLETED') == 'WAITING_PRINT'

    assert packing_status_for(
        paid=True, print_ok=True, binding_ok=True, open_attempts=0, blocking_exception=False
    ) == 'READY'
    assert packing_status_for(
        paid=True, print_ok=False, binding_ok=True, open_attempts=0, blocking_exception=False
    ) == 'BLOCKED'
    assert packing_status_for(
        paid=True, print_ok=True, binding_ok=True, open_attempts=1, blocking_exception=False
    ) == 'BLOCKED'
    assert packing_status_for(
        paid=True, print_ok=True, binding_ok=True, open_attempts=0, blocking_exception=True
    ) == 'BLOCKED'
    # READY never silently becomes PACKED.
    assert pack_ready_is_not_packed() == 'READY'

    assert next_order_status('PENDING_PRINT', 'READY') == 'PENDING_PRINT'
    assert next_order_status('PENDING_PRINT', 'BLOCKED') == 'PENDING_PRINT'
    # Only the explicit human pack action or a valid PACKED fact promotes the order.
    assert next_order_status('PENDING_PRINT', 'PACKED') == 'READY_FOR_DELIVERY'
    assert next_order_status('READY_FOR_DELIVERY', 'PACKED') == 'READY_FOR_DELIVERY'
    assert next_order_status('READY_FOR_DELIVERY', 'READY') == 'PENDING_PRINT'
    assert next_order_status('DELIVERING', 'READY') == 'DELIVERING'
    assert next_order_status('DELIVERED', 'BLOCKED') == 'DELIVERED'
    assert next_order_status('CANCELLED', 'READY') == 'CANCELLED'


def pack_ready_is_not_packed() -> str:
    return packing_status_for(
        paid=True, print_ok=True, binding_ok=True, open_attempts=0, blocking_exception=False
    )


# ---------------------------------------------------------------------------
# PostgreSQL integration layer
# ---------------------------------------------------------------------------

import math  # noqa: E402
import sys  # noqa: E402
import types  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app.core import Actor, DomainError  # noqa: E402
from app.models import (  # noqa: E402
    AfterSale,
    Artifact,
    Asset,
    BindingTask,
    BusinessException,
    Location,
    DeliverySlot,
    Order,
    OrderFile,
    PackingTask,
    PrintAttempt,
    PrintTask,
    Principal,
    Quote,
    Preparation,
    Staff,
    utcnow,
)
from app import production  # noqa: E402
from app.production_schemas import (  # noqa: E402
    ActualStartInput,
    BindingCompleteInput,
    CorrectionInput,
    PackCheck,
    PackInput,
    PrintAttemptInput,
    PrintResultInput,
    ReprintInput,
)


def _unique(prefix: str) -> str:
    return f'{prefix}-{uuid.uuid4().hex[:10]}'


def _seed(
    db,
    *,
    pages: int = 4,
    copies: int = 1,
    sides: str = 'SINGLE',
    binding: str = 'NONE',
    file_count: int = 1,
    status: str = 'PENDING_PRINT',
    settlement_kind: str | None = 'CASH_MANUAL',
    refund_locked: bool = False,
    packing: str = 'BLOCKED',
):
    """Seed shared models exactly as Codex commerce would after settlement."""
    now = datetime.now(timezone.utc)
    principal = Principal()
    staff = Staff(
        username=_unique('staff'),
        name='打印员',
        role='ADMIN',
        password_hash='x',
        enabled=True,
        status='ACTIVE',
        must_change_password=False,
        capabilities=['*'],
    )
    location = Location(name='1号宿舍楼', type='DORMITORY', x=0.5, y=0.5)
    slot = DeliverySlot(
        date='2026-10-31', name='上午', starts_at=now, ends_at=now + timedelta(hours=1), capacity=10
    )
    db.add_all([principal, staff, location, slot])
    db.flush()
    preparation = Preparation(
        principal_id=principal.id,
        status='READY',
        content_revision=1,
        expires_at=now + timedelta(hours=2),
    )
    db.add(preparation)
    db.flush()
    quote = Quote(
        preparation_id=preparation.id,
        price_version=1,
        expires_at=now + timedelta(minutes=15),
        subtotal_cents=1000,
        discount_cents=0,
        total_cents=1000,
    )
    db.add(quote)
    db.flush()
    order = Order(
        principal_id=principal.id,
        pickup_code=_unique('P').replace('-', '')[:10],
        status=status,
        content_revision=1,
        preparation_id=preparation.id,
        quote_id=quote.id,
        total_cents=1000,
        cash_received_cents=1000 if settlement_kind else 0,
        settlement_kind=settlement_kind,
        nickname='同学',
        phone='13800000000',
        location_id=location.id,
        requested_slot_id=slot.id,
        refund_locked=refund_locked,
        packing_status=packing,
        close_at=now + timedelta(days=1),
        data={},
    )
    db.add(order)
    db.flush()
    files, tasks, artifacts = [], [], []
    for index in range(file_count):
        asset = Asset(
            principal_id=principal.id,
            purpose='FINAL',
            name=f'file-{index}.pdf',
            mime='application/pdf',
            size=100,
            sha256=uuid.uuid4().hex + uuid.uuid4().hex,
            storage_key=f'private/{order.id}/{index}.pdf',
            status='READY',
            pages=pages,
        )
        db.add(asset)
        db.flush()
        file = OrderFile(
            order_id=order.id,
            revision=1,
            client_file_id=f'client-file-{index}',
            asset_id=asset.id,
            kind='DOCUMENT',
            binding_type=binding,
            data={'name': f'资料{index + 1}.pdf'},
        )
        db.add(file)
        db.flush()
        sheets_per_copy = math.ceil(pages / 2) if sides == 'DUPLEX' else pages
        pdf_pages = (2 * sheets_per_copy if sides == 'DUPLEX' else pages) * copies
        artifact = Artifact(
            preparation_id=preparation.id,
            asset_id=asset.id,
            client_file_id=f'client-file-{index}',
            client_segment_id=f'segment-{index}',
            sha256=asset.sha256,
            pages=pdf_pages,
            sheets=sheets_per_copy * copies,
            settings={
                'range': f'1-{pages}',
                'copies': copies,
                'sides': sides,
                'colorMode': 'BW',
                'flip': 'LONG_EDGE' if sides == 'DUPLEX' else 'NONE',
                'blankBacks': copies if sides == 'DUPLEX' and pages % 2 else 0,
                'contentRevision': 1,
            },
            page_map=[],
        )
        db.add(artifact)
        db.flush()
        task = PrintTask(
            order_id=order.id,
            file_id=file.id,
            artifact_id=artifact.id,
            active=True,
            status='PENDING',
            settings={'segmentId': f'segment-{index}', 'copies': copies, 'sides': sides},
        )
        db.add(task)
        if binding != 'NONE':
            db.add(BindingTask(order_id=order.id, file_id=file.id, status='WAITING_PRINT', data={}))
        db.flush()
        files.append(file)
        tasks.append(task)
        artifacts.append(artifact)
    db.add(PackingTask(order_id=order.id, status=packing, manifest_revision=1, data={}))
    # Full strict quote snapshot for commerce OrderView serialization.
    line_total = sum(artifact.sheets * 10 for artifact in artifacts)
    quote.subtotal_cents = line_total
    quote.total_cents = line_total
    order.total_cents = line_total
    order.cash_received_cents = line_total if settlement_kind else 0
    quote.data = {
        'id': quote.id,
        'priceVersion': 1,
        'lines': [
            {
                'key': f'a4-{index}',
                'relatedId': artifact.id,
                'unit': 'SHEET' if sides == 'SINGLE' else 'SHEET',
                'quantity': artifact.sheets,
                'unitPriceCents': 10,
                'amountCents': artifact.sheets * 10,
                'service': 'A4',
            }
            for index, artifact in enumerate(artifacts)
        ],
        'discounts': [],
        'discountAllocations': [],
        'selectionMode': 'AUTO',
        'selectedPromotionType': 'NONE',
        'deliveryFeeCents': 0,
        'volumeDiscountCandidate': None,
        'selectedEligibleSubtotalCents': 0,
        'checkoutAccountId': None,
    }
    db.commit()
    for row in (order, preparation, quote, *files, *tasks, *artifacts):
        db.refresh(row)
    return types.SimpleNamespace(
        order=order,
        staff=staff,
        preparation=preparation,
        quote=quote,
        files=files,
        tasks=tasks,
        artifacts=artifacts,
        actor=Actor(staff_id=staff.id, staff_role='ADMIN', capabilities={'*'}),
    )


def _fresh(db, order):
    # Each route call is its own committed request in production; tests mirror
    # that by committing before re-reading the projection.
    db.commit()
    db.expire_all()
    return db.get(Order, order.id)


def _attempt_and_complete(db, seed, task=None, *, start=True):
    task = task or seed.tasks[0]
    order = _fresh(db, seed.order)
    if start and order.actual_started_at is None:
        production.start_production(
            order.id, ActualStartInput(expectedVersion=order.version), db=db, actor=seed.actor
        )
        order = _fresh(db, seed.order)
    attempt = production.create_print_attempt(
        task.id,
        PrintAttemptInput(expectedVersion=order.version, artifactId=task.artifact_id),
        db=db,
        actor=seed.actor,
    )['data']
    order = _fresh(db, seed.order)
    production.record_print_result(
        attempt['attemptId'],
        PrintResultInput(expectedVersion=order.version, outcome='COMPLETED'),
        db=db,
        actor=seed.actor,
    )
    return attempt['attemptId']


def _pack(db, seed):
    order = _fresh(db, seed.order)
    return production.pack_order(
        order.id,
        PackInput(
            expectedVersion=order.version,
            checklistVersion=order.content_revision,
            checks=[PackCheck(fileId=file.id, confirmed=True) for file in seed.files],
        ),
        db=db,
        actor=seed.actor,
    )['data']


def test_unpaid_order_cannot_start_or_attempt(db):
    seed = _seed(db, settlement_kind=None, status='PENDING_CONFIRMATION')
    with pytest.raises(DomainError) as start_error:
        production.start_production(
            seed.order.id, ActualStartInput(expectedVersion=seed.order.version), db=db, actor=seed.actor
        )
    assert start_error.value.code == 'PAYMENT_REQUIRED'
    with pytest.raises(DomainError) as attempt_error:
        production.create_print_attempt(
            seed.tasks[0].id,
            PrintAttemptInput(expectedVersion=seed.order.version, artifactId=seed.tasks[0].artifact_id),
            db=db,
            actor=seed.actor,
        )
    assert attempt_error.value.code == 'PAYMENT_REQUIRED'


def test_refund_lock_blocks_start_attempt_and_pack(db):
    seed = _seed(db, refund_locked=True)
    with pytest.raises(DomainError) as start_error:
        production.start_production(
            seed.order.id, ActualStartInput(expectedVersion=seed.order.version), db=db, actor=seed.actor
        )
    assert start_error.value.code == 'REFUND_LOCKED'
    with pytest.raises(DomainError) as attempt_error:
        production.create_print_attempt(
            seed.tasks[0].id,
            PrintAttemptInput(expectedVersion=seed.order.version, artifactId=seed.tasks[0].artifact_id),
            db=db,
            actor=seed.actor,
        )
    assert attempt_error.value.code == 'REFUND_LOCKED'
    with pytest.raises(DomainError) as pack_error:
        production.pack_order(
            seed.order.id,
            PackInput(
                expectedVersion=seed.order.version,
                checklistVersion=1,
                checks=[PackCheck(fileId=seed.files[0].id, confirmed=True)],
            ),
            db=db,
            actor=seed.actor,
        )
    assert pack_error.value.code == 'REFUND_LOCKED'


def test_attempt_projection_and_completion_requires_actual_start(db):
    seed = _seed(db)
    before = seed.order.version
    attempt = production.create_print_attempt(
        seed.tasks[0].id,
        PrintAttemptInput(expectedVersion=before, artifactId=seed.tasks[0].artifact_id),
        db=db,
        actor=seed.actor,
    )['data']
    task = db.get(PrintTask, seed.tasks[0].id)
    assert task.status == 'ATTEMPTED'
    assert attempt['outcome'] is None
    order = _fresh(db, seed.order)
    assert order.status == 'PENDING_PRINT'
    assert order.packing_status == 'BLOCKED'
    assert order.version >= before

    with pytest.raises(DomainError) as start_error:
        production.record_print_result(
            attempt['attemptId'],
            PrintResultInput(expectedVersion=order.version, outcome='COMPLETED'),
            db=db,
            actor=seed.actor,
        )
    assert start_error.value.code == 'ACTUAL_START_REQUIRED'

    started = production.start_production(
        order.id,
        ActualStartInput(expectedVersion=order.version, reason='实体打印机已开始出纸'),
        db=db,
        actor=seed.actor,
    )['data']
    assert started['actualStartedAt']
    order = _fresh(db, seed.order)
    with pytest.raises(DomainError) as duplicate_start:
        production.start_production(
            order.id,
            ActualStartInput(expectedVersion=order.version),
            db=db,
            actor=seed.actor,
        )
    assert duplicate_start.value.code == 'ACTUAL_START_ALREADY_RECORDED'

    result = production.record_print_result(
        attempt['attemptId'],
        PrintResultInput(expectedVersion=order.version, outcome='COMPLETED'),
        db=db,
        actor=seed.actor,
    )['data']
    assert result['taskStatus'] == 'COMPLETED'
    assert result['orderStatus'] == 'PENDING_PRINT'
    order = _fresh(db, seed.order)
    assert order.status == 'PENDING_PRINT'
    assert order.packing_status == 'READY'
    task = db.get(PrintTask, seed.tasks[0].id)
    assert task.status == 'COMPLETED'
    attempt_row = db.get(PrintAttempt, attempt['attemptId'])
    assert attempt_row.outcome == 'COMPLETED' and attempt_row.completed_at is not None
    # Print completion never implies packed or delivered.
    packing = db.query(PackingTask).filter(PackingTask.order_id == order.id).one()
    assert packing.status == 'READY'
    assert order.status != 'READY_FOR_DELIVERY'


def test_open_attempt_blocks_second_attempt_and_pack(db):
    seed = _seed(db)
    first = production.create_print_attempt(
        seed.tasks[0].id,
        PrintAttemptInput(expectedVersion=seed.order.version, artifactId=seed.tasks[0].artifact_id),
        db=db,
        actor=seed.actor,
    )['data']
    order = _fresh(db, seed.order)
    with pytest.raises(DomainError) as attempt_error:
        production.create_print_attempt(
            seed.tasks[0].id,
            PrintAttemptInput(expectedVersion=order.version, artifactId=seed.tasks[0].artifact_id),
            db=db,
            actor=seed.actor,
        )
    assert attempt_error.value.code == 'OPEN_ATTEMPT'
    assert attempt_error.value.details['attemptId'] == first['attemptId']
    with pytest.raises(DomainError) as pack_error:
        _pack(db, seed)
    assert pack_error.value.code == 'PRINT_INCOMPLETE'


def test_completed_print_does_not_satisfy_binding_and_packing(db):
    seed = _seed(db, binding='CORNER')
    with pytest.raises(DomainError) as pack_error:
        _pack(db, seed)
    assert pack_error.value.code == 'PRINT_INCOMPLETE'
    _attempt_and_complete(db, seed)
    order = _fresh(db, seed.order)
    binding = db.query(BindingTask).filter(BindingTask.order_id == order.id).one()
    assert binding.status == 'READY'
    with pytest.raises(DomainError) as binding_missing:
        _pack(db, seed)
    assert binding_missing.value.code == 'BINDING_INCOMPLETE'
    production.complete_binding(
        binding.id, BindingCompleteInput(expectedVersion=order.version), db=db, actor=seed.actor
    )
    order = _fresh(db, seed.order)
    # Wrong checklist version must not pack.
    with pytest.raises(DomainError) as stale:
        production.pack_order(
            order.id,
            PackInput(
                expectedVersion=order.version,
                checklistVersion=order.content_revision + 1,
                checks=[PackCheck(fileId=seed.files[0].id, confirmed=True)],
            ),
            db=db,
            actor=seed.actor,
        )
    assert stale.value.code == 'CHECKLIST_STALE'
    # Missing/unconfirmed checklist entries are refused.
    with pytest.raises(DomainError) as incomplete:
        production.pack_order(
            order.id,
            PackInput(expectedVersion=order.version, checklistVersion=order.content_revision, checks=[PackCheck(fileId=seed.files[0].id, confirmed=False)]),
            db=db,
            actor=seed.actor,
        )
    assert incomplete.value.code == 'CHECKLIST_INCOMPLETE'
    packed = _pack(db, seed)
    assert packed['packingStatus'] == 'PACKED'
    assert packed['status'] == 'READY_FOR_DELIVERY'
    order = _fresh(db, seed.order)
    assert order.status == 'READY_FOR_DELIVERY'
    assert order.packing_status == 'PACKED'
    with pytest.raises(DomainError) as repeat:
        _pack(db, seed)
    assert repeat.value.code == 'PRODUCTION_COMPLETE'


def test_duplex_odd_sheet_listing_covers_every_copy_and_blank_back(db):
    seed = _seed(db, pages=7, copies=3, sides='DUPLEX')
    queue = production.list_print_tasks(
        order_id=seed.order.id, db=db, actor=seed.actor
    )['data']
    task = queue['tasks'][0]
    assert task['sheetCount'] == 12
    assert len(task['sheetCatalog']) == 12
    by_id = {row['sheetId']: row for row in task['sheetCatalog']}
    assert by_id['c1-s4']['blankBack'] is True
    assert by_id['c1-s4']['backPage'] is None
    assert by_id['c3-s4']['frontPage'] == 7


def test_reprint_whole_after_completion_invalidates_packing_and_preserves_attempts(db):
    seed = _seed(db)
    first_attempt = _attempt_and_complete(db, seed)
    order = _fresh(db, seed.order)
    packed = _pack(db, seed)
    assert packed['packingStatus'] == 'PACKED'

    order = _fresh(db, seed.order)
    reprint = production.create_reprint(
        seed.tasks[0].id,
        ReprintInput(expectedVersion=order.version, mode='WHOLE', reason='封面颜色错误'),
        db=db,
        actor=seed.actor,
    )['data']
    assert reprint['nonBillable'] is True
    assert reprint['mode'] == 'WHOLE'
    assert reprint['artifactId'] == seed.tasks[0].artifact_id
    assert reprint['attemptId'] != first_attempt
    order = _fresh(db, seed.order)
    assert order.status == 'PENDING_PRINT'
    assert order.packing_status == 'BLOCKED'
    exception = db.query(BusinessException).filter(BusinessException.order_id == order.id, BusinessException.type == 'REPRINT_AFTER_COMPLETION').one()
    assert exception.status == 'OPEN' and exception.blocks_delivery is True
    # All old facts are preserved.
    attempts = db.query(PrintAttempt).filter(PrintAttempt.task_id == seed.tasks[0].id).all()
    assert len(attempts) == 2
    assert {row.outcome for row in attempts} == {'COMPLETED', None}

    production.record_print_result(
        reprint['attemptId'],
        PrintResultInput(expectedVersion=order.version, outcome='COMPLETED'),
        db=db,
        actor=seed.actor,
    )
    order = _fresh(db, seed.order)
    db.refresh(exception)
    assert exception.status == 'RESOLVED'
    assert order.status == 'PENDING_PRINT'
    assert order.packing_status == 'READY'
    packed = _pack(db, seed)
    assert packed['packingStatus'] == 'PACKED'


def _fake_media(monkeypatch, *, present=True, artifact_factory=None):
    module = types.ModuleType('app.media')

    def create_reprint_artifact(db, task, sheet_ids, actor):
        if artifact_factory is not None:
            return artifact_factory(db, task, sheet_ids, actor)
        source = db.get(Artifact, task.artifact_id)
        asset = Asset(
            principal_id=None,
            purpose='REPRINT',
            name='reprint.pdf',
            mime='application/pdf',
            size=10,
            sha256=uuid.uuid4().hex + uuid.uuid4().hex,
            storage_key=f'private/reprint/{uuid.uuid4().hex}.pdf',
            status='READY',
            pages=len(sheet_ids),
        )
        db.add(asset)
        db.flush()
        artifact = Artifact(
            preparation_id=source.preparation_id,
            asset_id=asset.id,
            client_file_id=source.client_file_id,
            client_segment_id=source.client_segment_id,
            sha256=asset.sha256,
            pages=len(sheet_ids),
            sheets=len(sheet_ids),
            settings={'reprintOfTaskId': task.id, 'sheetIds': list(sheet_ids), 'nonBillable': True},
            page_map=[],
        )
        db.add(artifact)
        db.flush()
        return artifact

    if present:
        module.create_reprint_artifact = create_reprint_artifact
    import app as app_package

    monkeypatch.setitem(sys.modules, 'app.media', module)
    # ``from app import media`` resolves the package attribute first, so both
    # the module table and the package attribute must point at the seam.
    monkeypatch.setattr(app_package, 'media', module, raising=False)
    return module


def test_sheets_reprint_uses_media_helper_and_never_the_original_pdf(db, monkeypatch):
    _fake_media(monkeypatch)
    seed = _seed(db, pages=7, copies=2, sides='DUPLEX')
    _attempt_and_complete(db, seed)
    order = _fresh(db, seed.order)
    with pytest.raises(DomainError) as invalid:
        production.create_reprint(
            seed.tasks[0].id,
            ReprintInput(expectedVersion=order.version, mode='SHEETS', sheetIds=['c9-s9'], reason='缺页'),
            db=db,
            actor=seed.actor,
        )
    assert invalid.value.code == 'SHEET_SELECTION_INVALID'
    order = _fresh(db, seed.order)
    result = production.create_reprint(
        seed.tasks[0].id,
        ReprintInput(
            expectedVersion=order.version,
            mode='SHEETS',
            sheetIds=['c1-s4', 'c2-s1'],
            reason='缺页补打',
        ),
        db=db,
        actor=seed.actor,
    )['data']
    assert result['sheetIds'] == ['c1-s4', 'c2-s1']
    assert result['artifactId'] != seed.tasks[0].artifact_id
    attempt = db.get(PrintAttempt, result['attemptId'])
    assert attempt.artifact_id == result['artifactId'] != seed.tasks[0].artifact_id
    reprint_artifact = db.get(Artifact, result['artifactId'])
    assert reprint_artifact.settings['nonBillable'] is True
    assert reprint_artifact.settings['reprintOfTaskId'] == seed.tasks[0].id


def test_sheets_reprint_is_blocked_when_media_helper_is_unavailable(db, monkeypatch):
    _fake_media(monkeypatch, present=False)
    seed = _seed(db, pages=7, copies=1, sides='DUPLEX')
    _attempt_and_complete(db, seed)
    order = _fresh(db, seed.order)
    with pytest.raises(DomainError) as unavailable:
        production.create_reprint(
            seed.tasks[0].id,
            ReprintInput(expectedVersion=order.version, mode='SHEETS', sheetIds=['c1-s1'], reason='缺页'),
            db=db,
            actor=seed.actor,
        )
    assert unavailable.value.code == 'REPRINT_UNAVAILABLE'
    assert unavailable.value.status == 503
    # No attempt or fake artifact was recorded as a substitute.
    assert db.query(PrintAttempt).filter(PrintAttempt.task_id == seed.tasks[0].id).count() == 1


def test_reopen_print_blocks_dependent_binding_packing_and_delivery(db):
    seed = _seed(db, binding='SIDE')
    _attempt_and_complete(db, seed)
    order = _fresh(db, seed.order)
    binding = db.query(BindingTask).filter(BindingTask.order_id == order.id).one()
    production.complete_binding(
        binding.id, BindingCompleteInput(expectedVersion=order.version), db=db, actor=seed.actor
    )
    _pack(db, seed)
    order = _fresh(db, seed.order)
    assert order.status == 'READY_FOR_DELIVERY'
    corrected = production.apply_correction(
        CorrectionInput(
            targetType='PRINT_TASK',
            targetId=seed.tasks[0].id,
            expectedVersion=order.version,
            action='REOPEN_PRINT',
            reason='发现错页，需要重新打印',
        ),
        db=db,
        actor=seed.actor,
    )['data']
    assert corrected['deliveryBlocked'] is True
    order = _fresh(db, seed.order)
    assert order.status == 'PENDING_PRINT'
    assert order.packing_status != 'PACKED'
    task = db.get(PrintTask, seed.tasks[0].id)
    assert task.status == 'PENDING'
    db.refresh(binding)
    assert binding.status == 'WAITING_PRINT'
    assert task.settings['corrections'][-1]['action'] == 'REOPEN_PRINT'
    # Attempt history remains.
    assert db.query(PrintAttempt).filter(PrintAttempt.task_id == seed.tasks[0].id).count() == 1


def test_unpack_and_reopen_binding_are_whitelisted(db):
    seed = _seed(db, binding='CORNER')
    _attempt_and_complete(db, seed)
    order = _fresh(db, seed.order)
    binding = db.query(BindingTask).filter(BindingTask.order_id == order.id).one()
    production.complete_binding(
        binding.id, BindingCompleteInput(expectedVersion=order.version), db=db, actor=seed.actor
    )
    _pack(db, seed)
    order = _fresh(db, seed.order)
    reopened = production.apply_correction(
        CorrectionInput(
            targetType='BINDING_TASK',
            targetId=binding.id,
            expectedVersion=order.version,
            action='REOPEN_BINDING',
            reason='装订位置错误',
        ),
        db=db,
        actor=seed.actor,
    )['data']
    assert reopened['bindingStatus'] == 'READY'
    order = _fresh(db, seed.order)
    assert order.packing_status != 'PACKED'
    production.complete_binding(
        binding.id, BindingCompleteInput(expectedVersion=order.version), db=db, actor=seed.actor
    )
    _pack(db, seed)
    order = _fresh(db, seed.order)
    unpacked = production.apply_correction(
        CorrectionInput(
            targetType='ORDER',
            targetId=order.id,
            expectedVersion=order.version,
            action='UNPACK',
            reason='装袋清单有误',
        ),
        db=db,
        actor=seed.actor,
    )['data']
    assert unpacked['packingStatus'] == 'READY'
    order = _fresh(db, seed.order)
    assert order.status == 'PENDING_PRINT'


def test_delivery_started_correction_records_exception_and_keeps_facts(db):
    seed = _seed(db, status='DELIVERED', packing='PACKED')
    order = _fresh(db, seed.order)
    with pytest.raises(DomainError) as error:
        production.apply_correction(
            CorrectionInput(
                targetType='PRINT_TASK',
                targetId=seed.tasks[0].id,
                expectedVersion=order.version,
                action='REOPEN_PRINT',
                reason='送达后发现错页',
            ),
            db=db,
            actor=seed.actor,
        )
    assert error.value.code == 'HIGH_RISK_CORRECTION'
    assert error.value.status == 409
    assert error.value.commit is True
    db.commit()
    order = _fresh(db, seed.order)
    assert order.status == 'DELIVERED'
    packing = db.query(PackingTask).filter(PackingTask.order_id == order.id).one()
    assert packing.status == 'PACKED'
    task = db.get(PrintTask, seed.tasks[0].id)
    assert task.status == 'PENDING'  # seeded pending, not rewritten by the rejected correction
    exception = db.query(BusinessException).filter(BusinessException.order_id == order.id).one()
    assert exception.blocks_delivery is True
    assert exception.status == 'OPEN'
    assert exception.data['action'] == 'REOPEN_PRINT'


def test_refresh_is_idempotent_and_never_auto_completes(db):
    seed = _seed(db)
    order = _fresh(db, seed.order)
    production.refresh_production(db, order)
    db.commit()
    order = _fresh(db, seed.order)
    first_version = order.version
    checklist = order.data.get('productionChecklist')
    assert checklist['version'] == 1
    assert checklist['packingStatus'] == 'BLOCKED'
    production.refresh_production(db, order)
    db.commit()
    order = _fresh(db, seed.order)
    assert order.version == first_version
    assert order.status == 'PENDING_PRINT'
    assert order.packing_status == 'BLOCKED'
    task = db.get(PrintTask, seed.tasks[0].id)
    assert task.status == 'PENDING'


def test_stale_expected_version_is_rejected(db):
    seed = _seed(db)
    with pytest.raises(DomainError) as error:
        production.start_production(
            seed.order.id, ActualStartInput(expectedVersion=seed.order.version + 5), db=db, actor=seed.actor
        )
    assert error.value.code == 'VERSION_CONFLICT'


def test_admin_required_for_queue_and_start(db):
    seed = _seed(db)
    anonymous = Actor()
    with pytest.raises(DomainError) as queue_error:
        production.list_print_tasks(order_id=seed.order.id, db=db, actor=anonymous)
    assert queue_error.value.code == 'AUTH_REQUIRED'
    with pytest.raises(DomainError) as start_error:
        production.start_production(
            seed.order.id, ActualStartInput(expectedVersion=seed.order.version), db=db, actor=anonymous
        )
    assert start_error.value.code == 'AUTH_REQUIRED'


# ---------------------------------------------------------------------------
# HTTP envelope / idempotency / validation (auth override until app.auth lands)
# ---------------------------------------------------------------------------


def _override_actor(app, actor):
    app.dependency_overrides[production.actor_dep] = lambda: actor


def test_http_attempt_envelope_and_idempotent_replay(app, client, api, db):
    seed = _seed(db)
    _override_actor(app, seed.actor)
    start = api(
        client,
        f'/admin/orders/{seed.order.id}/start-production',
        {'expectedVersion': seed.order.version, 'reason': '已开印'},
        key='start-key-1',
    )
    assert start.status_code == 200, start.text
    assert start.json()['data']['actualStartedAt']
    assert start.json()['requestId']
    order = _fresh(db, seed.order)
    body = {'expectedVersion': order.version, 'artifactId': seed.tasks[0].artifact_id}
    first = api(client, f'/admin/print-tasks/{seed.tasks[0].id}/attempts', body, key='attempt-key-1')
    assert first.status_code == 200, first.text
    assert first.json()['data']['attemptId']
    replay = api(client, f'/admin/print-tasks/{seed.tasks[0].id}/attempts', body, key='attempt-key-1')
    assert replay.status_code == 200
    assert replay.json()['data'] == first.json()['data']
    assert db.query(PrintAttempt).filter(PrintAttempt.task_id == seed.tasks[0].id).count() == 1


def test_http_open_attempt_and_version_conflict_envelopes(app, client, api, db):
    seed = _seed(db)
    _override_actor(app, seed.actor)
    conflict = api(
        client,
        f'/admin/orders/{seed.order.id}/start-production',
        {'expectedVersion': seed.order.version + 9, 'reason': ''},
        key='start-conflict',
    )
    assert conflict.status_code == 409
    assert conflict.json()['error']['code'] == 'VERSION_CONFLICT'
    assert conflict.json()['requestId']
    assert conflict.headers['cache-control'] == 'no-store'

    order = _fresh(db, seed.order)
    first = api(
        client,
        f'/admin/print-tasks/{seed.tasks[0].id}/attempts',
        {'expectedVersion': order.version, 'artifactId': seed.tasks[0].artifact_id},
        key='attempt-key-2',
    )
    assert first.status_code == 200
    order = _fresh(db, seed.order)
    second = api(
        client,
        f'/admin/print-tasks/{seed.tasks[0].id}/attempts',
        {'expectedVersion': order.version, 'artifactId': seed.tasks[0].artifact_id},
        key='attempt-key-3',
    )
    assert second.status_code == 409
    assert second.json()['error']['code'] == 'OPEN_ATTEMPT'
    assert second.json()['error']['details']['attemptId'] == first.json()['data']['attemptId']


def test_http_strict_dto_and_route_authorization(app, client, api, db):
    seed = _seed(db)
    _override_actor(app, seed.actor)
    invalid = api(
        client,
        f'/admin/print-tasks/{seed.tasks[0].id}/attempts',
        {'expectedVersion': seed.order.version, 'artifactId': seed.tasks[0].artifact_id, 'copies': 2},
        key='attempt-invalid',
    )
    assert invalid.status_code == 422
    assert invalid.json()['error']['code'] == 'VALIDATION_ERROR'
    assert invalid.json()['error']['fieldErrors']

    _override_actor(app, Actor())
    unauthorized = api(client, '/admin/print-tasks', method='GET', key='queue-anon')
    assert unauthorized.status_code == 401
    assert unauthorized.json()['error']['code'] == 'AUTH_REQUIRED'


def test_catalog_accepts_media_page_map_with_page_keys():
    page_map = [
        {'copyIndex': 1, 'sheetIndex': 1, 'side': 'FRONT', 'page': 2},
        {'copyIndex': 1, 'sheetIndex': 1, 'side': 'BACK', 'page': 3, 'blank': False},
    ]
    catalog = sheet_catalog(_artifact(pages=4, sheets=1, copies=1, sides='DUPLEX', page_map=page_map))
    assert len(catalog) == 1
    assert catalog[0]['frontPage'] == 2 and catalog[0]['backPage'] == 3
    assert catalog[0]['blankBack'] is False


def test_result_can_resolve_open_attempt_during_refund_review(db):
    seed = _seed(db)
    attempt = production.create_print_attempt(
        seed.tasks[0].id,
        PrintAttemptInput(expectedVersion=seed.order.version, artifactId=seed.tasks[0].artifact_id),
        db=db,
        actor=seed.actor,
    )['data']
    order = _fresh(db, seed.order)
    order.refund_locked = True
    db.commit()
    order = _fresh(db, seed.order)
    # New production stays locked...
    with pytest.raises(DomainError) as start_error:
        production.start_production(
            order.id, ActualStartInput(expectedVersion=order.version), db=db, actor=seed.actor
        )
    assert start_error.value.code == 'REFUND_LOCKED'
    # ...but the open attempt can be verified (it is not new production).
    result = production.record_print_result(
        attempt['attemptId'],
        PrintResultInput(expectedVersion=order.version, outcome='FAILED', reason='退款核实：未出纸'),
        db=db,
        actor=seed.actor,
    )['data']
    assert result['taskStatus'] == 'EXCEPTION'
    attempt_row = db.get(PrintAttempt, attempt['attemptId'])
    assert attempt_row.outcome == 'FAILED'
    assert attempt_row.reason == '退款核实：未出纸'


def test_packing_requires_every_file_and_task(db):
    seed = _seed(db, file_count=2)
    _attempt_and_complete(db, seed, seed.tasks[0])
    with pytest.raises(DomainError) as incomplete:
        _pack(db, seed)
    assert incomplete.value.code == 'PRINT_INCOMPLETE'
    _attempt_and_complete(db, seed, seed.tasks[1])
    packed = _pack(db, seed)
    assert packed['packingStatus'] == 'PACKED'
    assert set(packed['checkedFileIds']) == {file.id for file in seed.files}


def test_courier_cannot_operate_production(db):
    seed = _seed(db)
    courier = Actor(staff_id=seed.staff.id, staff_role='COURIER')
    with pytest.raises(DomainError) as error:
        production.list_print_tasks(order_id=seed.order.id, db=db, actor=courier)
    assert error.value.code == 'FORBIDDEN'
    assert error.value.status == 403


def test_delivered_projection_is_frozen_and_does_not_claim_unpaid(db):
    seed = _seed(db, status='DELIVERED', packing='PACKED')
    view = production.order_production_view(db, seed.order)
    assert view['packingStatus'] == 'PACKED'
    assert view['blockers'] == []
    assert view['canPack'] is False
    production.refresh_production(db, seed.order)
    db.commit()
    order = _fresh(db, seed.order)
    assert order.status == 'DELIVERED'
    assert order.packing_status == 'PACKED'


def test_start_after_delivery_records_compensation_exception(db):
    seed = _seed(db, status='DELIVERED', packing='PACKED')
    with pytest.raises(DomainError) as error:
        production.start_production(
            seed.order.id,
            ActualStartInput(expectedVersion=seed.order.version, reason='送达后要求重开生产'),
            db=db,
            actor=seed.actor,
        )
    assert error.value.code == 'HIGH_RISK_CORRECTION'
    db.commit()
    order = _fresh(db, seed.order)
    assert order.status == 'DELIVERED'
    exception = db.query(BusinessException).filter(BusinessException.order_id == order.id).one()
    assert exception.blocks_delivery is True and exception.status == 'OPEN'


def test_catalog_matches_pdf_engine_page_map_shape():
    """The real pdf_engine page_map uses sourcePageIndex/pdfPageIndex/sheetId."""
    page_map = []
    pdf_page = 1
    for copy in (1, 2, 3):
        for position, source_page in enumerate(range(1, 8)):
            page_map.append(
                {
                    'pdfPageIndex': pdf_page,
                    'sourcePageIndex': source_page,
                    'copyIndex': copy,
                    'sheetIndex': position // 2 + 1,
                    'sheetOrdinal': pdf_page,
                    'sheetId': f'c{copy}-s{position // 2 + 1}',
                    'side': 'FRONT' if position % 2 == 0 else 'BACK',
                    'blank': False,
                }
            )
            pdf_page += 1
        page_map.append(
            {
                'pdfPageIndex': pdf_page,
                'sourcePageIndex': None,
                'copyIndex': copy,
                'sheetIndex': 4,
                'sheetOrdinal': pdf_page,
                'sheetId': f'c{copy}-s4',
                'side': 'BACK',
                'blank': True,
            }
        )
        pdf_page += 1
    catalog = sheet_catalog(_artifact(pages=24, sheets=12, copies=3, sides='DUPLEX', page_map=page_map))
    assert len(catalog) == 12
    by_id = {row['sheetId']: row for row in catalog}
    assert by_id['c1-s1'] == {
        'sheetId': 'c1-s1',
        'copy': 1,
        'sheet': 1,
        'frontPage': 1,
        'backPage': 2,
        'blankBack': False,
        'pdfFrontPage': 1,
        'pdfBackPage': 2,
    }
    assert by_id['c1-s4']['frontPage'] == 7
    assert by_id['c1-s4']['backPage'] is None
    assert by_id['c1-s4']['blankBack'] is True
    assert by_id['c3-s4']['pdfFrontPage'] == 23


def test_correction_accepts_target_version_and_rejects_unknown_version(db):
    seed = _seed(db)
    _attempt_and_complete(db, seed)
    task = db.get(PrintTask, seed.tasks[0].id)
    with pytest.raises(DomainError) as conflict:
        production.apply_correction(
            CorrectionInput(
                targetType='PRINT_TASK',
                targetId=task.id,
                expectedVersion=task.version + 7,
                action='REOPEN_PRINT',
                reason='版本校验',
            ),
            db=db,
            actor=seed.actor,
        )
    assert conflict.value.code == 'VERSION_CONFLICT'
    # The task row version is an accepted concurrency token for a correction.
    production.apply_correction(
        CorrectionInput(
            targetType='PRINT_TASK',
            targetId=task.id,
            expectedVersion=task.version,
            action='REOPEN_PRINT',
            reason='错页',
        ),
        db=db,
        actor=seed.actor,
    )
    db.commit()
    assert db.get(PrintTask, task.id).status == 'PENDING'


def test_admin_without_production_capability_is_forbidden(db):
    seed = _seed(db)
    limited = Actor(staff_id=seed.staff.id, staff_role='ADMIN', capabilities={'orders.view'})
    with pytest.raises(DomainError) as error:
        production.start_production(
            seed.order.id, ActualStartInput(expectedVersion=seed.order.version), db=db, actor=limited
        )
    assert error.value.code == 'FORBIDDEN'
    # The full/initial administrator ('*') keeps access.
    production.start_production(
        seed.order.id, ActualStartInput(expectedVersion=seed.order.version), db=db, actor=seed.actor
    )


# Mount commerce+auth only for the cross-module end-to-end route test.
APP_MODULES = ['commerce', 'production', 'after_sales', 'auth']


def _seed_checkout(db):
    """Reuse the commerce contract's preparation/artifact/quote snapshot."""
    from app.commerce import OrderSubmit, submit_order
    from app.config import get_config

    principal = Principal()
    db.add(principal)
    db.flush()
    for section in ('services', 'announcement'):
        config = get_config(db, section)
        config.data = {**config.data, 'serviceEnabled': True}
    workflow = get_config(db, 'workflow')
    workflow.data = {**workflow.data, 'unpaidTimeoutMinutes': 60}
    prices = get_config(db, 'prices')
    prices.data = {**prices.data, 'bwSingle': 10}
    source = Asset(
        principal_id=principal.id,
        purpose='PRINT_SOURCE',
        name='test.pdf',
        mime='application/pdf',
        size=100,
        sha256='a' * 64,
        storage_key='fixtures/source.pdf',
        status='READY',
        pages=1,
    )
    db.add(source)
    db.flush()
    preparation = Preparation(
        principal_id=principal.id,
        status='READY',
        expires_at=utcnow() + timedelta(hours=24),
        input={
            'files': [
                {
                    'clientFileId': 'file-one',
                    'assetId': source.id,
                    'kind': 'DOCUMENT',
                    'segments': [
                        {
                            'clientSegmentId': 'segment-one',
                            'ranges': [{'from': 1, 'to': 1}],
                            'colorMode': 'BW',
                            'sides': 'SINGLE',
                            'flip': 'NONE',
                            'copies': 1,
                        }
                    ],
                    'excludedPages': [],
                    'bindingType': 'NONE',
                }
            ]
        },
        data={},
    )
    db.add(preparation)
    db.flush()
    final = Asset(
        principal_id=principal.id,
        purpose='FINAL_PDF',
        name='final.pdf',
        mime='application/pdf',
        size=100,
        sha256='b' * 64,
        storage_key='fixtures/final.pdf',
        status='READY',
        pages=1,
    )
    db.add(final)
    db.flush()
    artifact = Artifact(
        preparation_id=preparation.id,
        asset_id=final.id,
        client_file_id='file-one',
        client_segment_id='segment-one',
        sha256='b' * 64,
        pages=1,
        sheets=1,
        settings={
            'range': '1',
            'copies': 1,
            'colorMode': 'BW',
            'sides': 'SINGLE',
            'flip': 'NONE',
            'blankBacks': 0,
        },
        page_map=[{'sourcePage': 1, 'copyIndex': 1, 'sheetIndex': 1, 'side': 'FRONT'}],
    )
    db.add(artifact)
    db.flush()
    quote = Quote(
        preparation_id=preparation.id,
        price_version=prices.version,
        expires_at=utcnow() + timedelta(minutes=15),
        subtotal_cents=10,
        total_cents=10,
        discount_cents=0,
        data={
            'id': 'seed',
            'priceVersion': prices.version,
            'lines': [
                {
                    'key': 'a4-one',
                    'relatedId': artifact.id,
                    'unit': 'SHEET',
                    'quantity': 1,
                    'unitPriceCents': 10,
                    'amountCents': 10,
                    'service': 'A4',
                }
            ],
            'discounts': [],
            'discountAllocations': [],
            'selectionMode': 'AUTO',
            'selectedPromotionType': 'NONE',
            'deliveryFeeCents': 0,
            'volumeDiscountCandidate': None,
            'selectedEligibleSubtotalCents': 0,
            'checkoutAccountId': None,
        },
    )
    db.add(quote)
    db.flush()
    preparation.quote_id = quote.id
    location = Location(name='一号楼', x=0.5, y=0.5)
    slot = DeliverySlot(
        date='2030-01-01',
        name='晚间',
        starts_at=utcnow() + timedelta(days=1),
        ends_at=utcnow() + timedelta(days=1, hours=1),
        capacity=1,
    )
    db.add_all([location, slot])
    db.flush()
    body = {
        'preparationId': preparation.id,
        'quoteId': quote.id,
        'previewAcks': [{'artifactId': artifact.id, 'sha256': artifact.sha256}],
        'contact': {'wechatNickname': '测试用户', 'phone': '13800000000'},
        'locationId': location.id,
        'slotId': slot.id,
        'customerNote': '备注',
    }
    order = submit_order(
        db,
        Actor(principal_id=principal.id, guest_principal_id=principal.id),
        OrderSubmit.model_validate(body),
    )
    db.commit()
    db.refresh(order)
    return order, artifact


def test_end_to_end_commerce_settlement_to_packed(app, client, admin_client, api, db):
    """Commerce-created tasks -> settlement refresh -> print -> pack -> delivery ready."""
    order, artifact = _seed_checkout(db)
    payment = api(
        admin_client,
        f'/admin/orders/{order.id}/confirm-payment',
        {
            'expectedVersion': order.version,
            'contentRevision': order.content_revision,
            'quoteId': order.quote_id,
            'receivedCents': order.total_cents,
            'settlementKind': 'CASH_MANUAL',
            'note': '',
        },
        key='e2e-payment',
    )
    assert payment.status_code == 200, payment.text
    view = payment.json()['data']
    assert view['status'] == 'PENDING_PRINT'
    file = view['files'][0]
    task_id = file['segments'][0]['id']
    assert file['printStatus'] == 'PENDING'
    assert file['bindingStatus'] == 'NOT_REQUIRED'
    assert view['packingStatus'] == 'BLOCKED'

    started = api(
        admin_client,
        f'/admin/orders/{order.id}/start-production',
        {'expectedVersion': view['version'], 'reason': '实体开印'},
        key='e2e-start',
    )
    assert started.status_code == 200, started.text
    started_version = started.json()['data']['version']
    attempt = api(
        admin_client,
        f'/admin/print-tasks/{task_id}/attempts',
        {'expectedVersion': started_version, 'artifactId': artifact.id},
        key='e2e-attempt',
    )
    assert attempt.status_code == 200, attempt.text
    attempt_id = attempt.json()['data']['attemptId']

    current_response = api(admin_client, f'/admin/orders/{order.id}', method='GET', key='e2e-view-1')
    assert current_response.status_code == 200, current_response.text
    current = current_response.json()['data']
    result = api(
        admin_client,
        f'/admin/print-attempts/{attempt_id}/result',
        {'expectedVersion': current['version'], 'outcome': 'COMPLETED', 'reason': ''},
        key='e2e-result',
    )
    assert result.status_code == 200, result.text

    current = api(admin_client, f'/admin/orders/{order.id}', method='GET', key='e2e-view-2').json()['data']
    assert current['files'][0]['printStatus'] == 'COMPLETED'
    assert current['packingStatus'] == 'READY'
    assert current['status'] == 'PENDING_PRINT'

    packed = api(
        admin_client,
        f'/admin/orders/{order.id}/pack',
        {
            'expectedVersion': current['version'],
            'checklistVersion': current['contentRevision'],
            'checks': [{'fileId': current['files'][0]['id'], 'confirmed': True}],
        },
        key='e2e-pack',
    )
    assert packed.status_code == 200, packed.text
    final = api(admin_client, f'/admin/orders/{order.id}', method='GET', key='e2e-view-3').json()['data']
    assert final['status'] == 'READY_FOR_DELIVERY'
    assert final['packingStatus'] == 'PACKED'
    assert final['actualStartedAt']
    assert final['checklistVersion'] if 'checklistVersion' in final else True


def test_reprint_invalidates_completed_binding_dependency(db):
    seed = _seed(db, binding='CORNER')
    _attempt_and_complete(db, seed)
    order = _fresh(db, seed.order)
    binding = db.query(BindingTask).filter(BindingTask.order_id == order.id).one()
    production.complete_binding(
        binding.id, BindingCompleteInput(expectedVersion=order.version), db=db, actor=seed.actor
    )
    _pack(db, seed)
    order = _fresh(db, seed.order)
    reprint = production.create_reprint(
        seed.tasks[0].id,
        ReprintInput(expectedVersion=order.version, mode='WHOLE', reason='内页装订错位'),
        db=db,
        actor=seed.actor,
    )['data']
    order = _fresh(db, seed.order)
    db.refresh(binding)
    assert binding.status == 'WAITING_PRINT'
    assert order.status == 'PENDING_PRINT' and order.packing_status != 'PACKED'
    production.record_print_result(
        reprint['attemptId'],
        PrintResultInput(expectedVersion=order.version, outcome='COMPLETED'),
        db=db,
        actor=seed.actor,
    )
    order = _fresh(db, seed.order)
    db.refresh(binding)
    # The reprint restores printing but the binding must be checked again, so
    # packing stays blocked until the human re-completes binding.
    assert binding.status == 'READY'
    assert order.packing_status == 'BLOCKED'
    production.complete_binding(
        binding.id, BindingCompleteInput(expectedVersion=order.version), db=db, actor=seed.actor
    )
    order = _fresh(db, seed.order)
    assert order.packing_status == 'READY'


def test_multiple_segment_tasks_for_one_file_aggregate_print_status(db):
    seed = _seed(db, binding='NONE')
    same_file = seed.files[0]
    extra_asset = Asset(
        principal_id=None,
        purpose='FINAL',
        name='segment-2.pdf',
        mime='application/pdf',
        size=100,
        sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        storage_key=f'private/{seed.order.id}/segment-2.pdf',
        status='READY',
        pages=2,
    )
    db.add(extra_asset)
    db.flush()
    extra_artifact = Artifact(
        preparation_id=seed.preparation.id,
        asset_id=extra_asset.id,
        client_file_id=same_file.client_file_id,
        client_segment_id='segment-extra',
        sha256=extra_asset.sha256,
        pages=2,
        sheets=2,
        settings={'range': '2', 'rangeModel': [2], 'copies': 1, 'colorMode': 'BW', 'sides': 'SINGLE', 'flip': 'NONE'},
        page_map=[{'sourcePageIndex': 2, 'copyIndex': 1, 'sheetIndex': 1, 'side': 'FRONT', 'sheetId': 'c1-s1'}],
    )
    db.add(extra_artifact)
    db.flush()
    extra_task = PrintTask(
        order_id=seed.order.id,
        file_id=same_file.id,
        artifact_id=extra_artifact.id,
        active=True,
        status='PENDING',
        settings={'segmentId': 'segment-extra', 'copies': 1, 'sides': 'SINGLE'},
    )
    db.add(extra_task)
    db.commit()
    _attempt_and_complete(db, seed, seed.tasks[0])
    order = _fresh(db, seed.order)
    view = production.order_production_view(db, order)
    assert view['files'][0]['printStatus'] == 'PENDING'
    with pytest.raises(DomainError) as incomplete:
        _pack(db, seed)
    assert incomplete.value.code == 'PRINT_INCOMPLETE'
    _attempt_and_complete(db, seed, extra_task)
    order = _fresh(db, seed.order)
    view = production.order_production_view(db, order)
    assert view['files'][0]['printStatus'] == 'COMPLETED'
    packed = _pack(db, seed)
    assert packed['packingStatus'] == 'PACKED'


def test_attempt_rejects_artifact_not_belonging_to_current_task(db):
    seed = _seed(db)
    with pytest.raises(DomainError) as error:
        production.create_print_attempt(
            seed.tasks[0].id,
            PrintAttemptInput(expectedVersion=seed.order.version, artifactId='not-the-task-artifact'),
            db=db,
            actor=seed.actor,
        )
    assert error.value.code == 'STALE_PRINT_ARTIFACT'


def test_result_rejects_attempt_whose_artifact_was_replaced(db):
    seed = _seed(db)
    attempt = production.create_print_attempt(
        seed.tasks[0].id,
        PrintAttemptInput(expectedVersion=seed.order.version, artifactId=seed.tasks[0].artifact_id),
        db=db,
        actor=seed.actor,
    )['data']
    replacement_asset = Asset(
        principal_id=None,
        purpose='FINAL',
        name='replacement.pdf',
        mime='application/pdf',
        size=10,
        sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        storage_key=f'private/{seed.order.id}/replacement.pdf',
        status='READY',
        pages=1,
    )
    db.add(replacement_asset)
    db.flush()
    replacement = Artifact(
        preparation_id=seed.preparation.id,
        asset_id=replacement_asset.id,
        client_file_id=seed.files[0].client_file_id,
        sha256=replacement_asset.sha256,
        pages=1,
        sheets=1,
        settings=dict(seed.artifacts[0].settings),
        page_map=[],
    )
    db.add(replacement)
    db.flush()
    task = db.get(PrintTask, seed.tasks[0].id)
    task.artifact_id = replacement.id
    db.commit()
    order = _fresh(db, seed.order)
    with pytest.raises(DomainError) as error:
        production.record_print_result(
            attempt['attemptId'],
            PrintResultInput(expectedVersion=order.version, outcome='COMPLETED'),
            db=db,
            actor=seed.actor,
        )
    assert error.value.code == 'STALE_PRINT_ATTEMPT'
    assert db.get(PrintAttempt, attempt['attemptId']).outcome is None


def test_pack_accepts_client_file_id_alias_and_rejects_stale_order_version(db):
    seed = _seed(db)
    _attempt_and_complete(db, seed)
    order = _fresh(db, seed.order)
    with pytest.raises(DomainError) as conflict:
        production.pack_order(
            order.id,
            PackInput(
                expectedVersion=order.version + 1,
                checklistVersion=order.content_revision,
                checks=[PackCheck(fileId=seed.files[0].client_file_id, confirmed=True)],
            ),
            db=db,
            actor=seed.actor,
        )
    assert conflict.value.code == 'VERSION_CONFLICT'
    packed = production.pack_order(
        order.id,
        PackInput(
            expectedVersion=order.version,
            checklistVersion=order.content_revision,
            checks=[PackCheck(fileId=seed.files[0].client_file_id, confirmed=True)],
        ),
        db=db,
        actor=seed.actor,
    )['data']
    assert packed['packingStatus'] == 'PACKED'
    assert packed['checkedFileIds'] == [seed.files[0].id]


def test_http_queues_binding_pack_and_correction_envelopes(app, client, admin_client, api, db):
    seed = _seed(db, binding='CORNER')
    _attempt_and_complete(db, seed)

    order = _fresh(db, seed.order)
    queue = api(
        admin_client,
        f'/admin/print-tasks?orderId={order.id}',
        method='GET',
        key='queue-print',
    )
    assert queue.status_code == 200, queue.text
    assert queue.json()['data']['tasks'][0]['sheetCount'] == 4
    assert queue.json()['data']['counts']['COMPLETED'] == 1

    binding_queue = api(admin_client, '/admin/binding-tasks', method='GET', key='queue-binding')
    assert binding_queue.status_code == 200, binding_queue.text
    binding_id = binding_queue.json()['data']['tasks'][0]['id']

    completed = api(
        admin_client,
        f'/admin/binding-tasks/{binding_id}/complete',
        {'expectedVersion': order.version},
        key='binding-complete',
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()['data']['status'] == 'COMPLETED'

    order = _fresh(db, seed.order)
    packing_queue = api(admin_client, '/admin/packing-tasks', method='GET', key='queue-packing')
    assert packing_queue.status_code == 200, packing_queue.text
    packing = packing_queue.json()['data']['tasks'][0]
    assert packing['checklistVersion'] == order.content_revision
    assert packing['checklist']['items'][0]['bindingStatus'] == 'COMPLETED'

    packed = api(
        admin_client,
        f'/admin/orders/{order.id}/pack',
        {
            'expectedVersion': order.version,
            'checklistVersion': order.content_revision,
            'checks': [{'fileId': seed.files[0].id, 'confirmed': True}],
        },
        key='pack-final',
    )
    assert packed.status_code == 200, packed.text

    order = _fresh(db, seed.order)
    unpacked = api(
        admin_client,
        '/admin/corrections',
        {
            'targetType': 'ORDER',
            'targetId': order.id,
            'expectedVersion': order.version,
            'action': 'UNPACK',
            'reason': '装袋清单需要重核',
        },
        key='unpack-final',
    )
    assert unpacked.status_code == 200, unpacked.text
    assert unpacked.json()['data']['packingStatus'] == 'READY'
    assert unpacked.json()['data']['deliveryBlocked'] is True


def test_http_whole_reprint_envelope_and_nonbillable(app, client, admin_client, api, db):
    seed = _seed(db)
    _attempt_and_complete(db, seed)
    order = _fresh(db, seed.order)
    reprint = api(
        admin_client,
        f'/admin/print-tasks/{seed.tasks[0].id}/reprints',
        {'expectedVersion': order.version, 'mode': 'WHOLE', 'reason': '实际出纸缺页'},
        key='reprint-whole',
    )
    assert reprint.status_code == 200, reprint.text
    data = reprint.json()['data']
    assert data['mode'] == 'WHOLE'
    assert data['nonBillable'] is True
    assert data['taskStatus'] == 'ATTEMPTED'
    assert data['attemptId']
    # Read the task projection rather than the full commerce OrderView (this
    # fixture only seeds production facts, not a complete strict quote snapshot).
    current = api(
        admin_client, f'/admin/print-tasks/{seed.tasks[0].id}', method='GET', key='reprint-view'
    )
    assert current.status_code == 200, current.text
    current_data = current.json()['data']
    assert current_data['status'] == 'ATTEMPTED'
    assert current_data['openAttemptId'] == data['attemptId']
    result = api(
        admin_client,
        f"/admin/print-attempts/{data['attemptId']}/result",
        {'expectedVersion': current_data['orderVersion'], 'outcome': 'COMPLETED', 'reason': ''},
        key='reprint-result',
    )
    assert result.status_code == 200, result.text
    assert result.json()['data']['taskStatus'] == 'COMPLETED'


def test_refund_production_facts_expose_start_and_open_attempts(db):
    seed = _seed(db)
    order = _fresh(db, seed.order)
    before = production.refund_production_facts(db, order)
    assert before['productionStarted'] is False
    assert before['openAttemptCount'] == 0
    attempt = production.create_print_attempt(
        seed.tasks[0].id,
        PrintAttemptInput(expectedVersion=order.version, artifactId=seed.tasks[0].artifact_id),
        db=db,
        actor=seed.actor,
    )['data']
    order = _fresh(db, seed.order)
    open_state = production.refund_production_facts(db, order)
    assert open_state['productionStarted'] is False
    assert open_state['openAttempts'][0]['attemptId'] == attempt['attemptId']
    production.start_production(
        order.id, ActualStartInput(expectedVersion=order.version), db=db, actor=seed.actor
    )
    order = _fresh(db, seed.order)
    started = production.refund_production_facts(db, order)
    assert started['productionStarted'] is True
    assert started['actualStartedAt']


def test_http_admin_order_view_and_customer_surface_isolation(app, client, admin_client, db):
    seed = _seed(db)
    response = admin_client.get(f'/api/v1/admin/orders/{seed.order.id}')
    assert response.status_code == 200, response.text
    assert response.json()['data']['id'] == seed.order.id
    # A staff cookie never elevates the customer surface; admins use /admin.
    customer_surface = admin_client.get(f'/api/v1/orders/{seed.order.id}')
    assert customer_surface.status_code == 404


def test_acceptance_database_is_postgresql(database_schema):
    # Locking/projection acceptance must run on the shared PostgreSQL test DB.
    assert database_schema.dialect.name == 'postgresql'


def test_repeated_reprints_reuse_and_then_resolve_the_open_exception(db):
    seed = _seed(db)
    _attempt_and_complete(db, seed)
    order = _fresh(db, seed.order)
    first = production.create_reprint(
        seed.tasks[0].id,
        ReprintInput(expectedVersion=order.version, mode='WHOLE', reason='缺页'),
        db=db,
        actor=seed.actor,
    )['data']
    order = _fresh(db, seed.order)
    production.record_print_result(
        first['attemptId'],
        PrintResultInput(expectedVersion=order.version, outcome='FAILED', reason='补打仍缺页'),
        db=db,
        actor=seed.actor,
    )
    order = _fresh(db, seed.order)
    second = production.create_reprint(
        seed.tasks[0].id,
        ReprintInput(expectedVersion=order.version, mode='WHOLE', reason='再次补打'),
        db=db,
        actor=seed.actor,
    )['data']
    exceptions = db.query(BusinessException).filter(
        BusinessException.order_id == seed.order.id,
        BusinessException.type == 'REPRINT_AFTER_COMPLETION',
    ).all()
    assert len(exceptions) == 1
    assert exceptions[0].status == 'OPEN'
    order = _fresh(db, seed.order)
    production.record_print_result(
        second['attemptId'],
        PrintResultInput(expectedVersion=order.version, outcome='COMPLETED'),
        db=db,
        actor=seed.actor,
    )
    db.commit()
    assert exceptions[0].status == 'RESOLVED'
    assert db.query(BusinessException).filter(
        BusinessException.order_id == seed.order.id,
        BusinessException.type == 'REPRINT_AFTER_COMPLETION',
    ).count() == 1


def test_correction_accepts_documented_action_payload_alias():
    documented = CorrectionInput(
        targetType='ORDER',
        targetId='order-1',
        expectedVersion=1,
        action='UNPACK',
        reason='核对',
        actionPayload={'ignored': True},
    )
    assert documented.action_payload == {'ignored': True}
    assert documented.model_dump(by_alias=True)['actionPayload'] == {'ignored': True}
    legacy = CorrectionInput(
        targetType='ORDER',
        targetId='order-1',
        expectedVersion=1,
        action='UNPACK',
        reason='核对',
        payload={'ignored': True},
    )
    assert legacy.action_payload == {'ignored': True}


# ---------------------------------------------------------------------------
# Targeted integration with the real commerce activation and after-sales flows
# ---------------------------------------------------------------------------


def _real_artifact_build(tmp_path, *, pages: int, copies: int, sides: str, segment_id: str):
    """Use pdf_engine itself so page_map/settings are production-shaped."""
    import pymupdf
    from app.pdf_engine import build_document_artifact

    source = tmp_path / f'source-{pages}-{copies}-{sides}.pdf'
    document = pymupdf.open()
    for _ in range(pages):
        document.new_page(width=595.276, height=841.89).insert_text((60, 60), 'DormPrint')
    document.save(source)
    document.close()
    build = build_document_artifact(
        source,
        tmp_path / f'artifact-{pages}-{copies}-{sides}.pdf',
        segment_id=segment_id,
        pages=list(range(1, pages + 1)),
        copies=copies,
        sides=sides,
        color_mode='BW',
        flip='LONG_EDGE' if sides == 'DUPLEX' else 'NONE',
    )
    return build


def _settings_for_build(build, *, copies: int, sides: str, pages: int, segment_id: str) -> dict:
    return {
        'clientFileId': 'real-file',
        'segmentId': segment_id,
        'range': '1' if pages == 1 else f'1-{pages}',
        'rangeModel': list(range(1, pages + 1)),
        'copies': copies,
        'colorMode': 'BW',
        'sides': sides,
        'flip': 'LONG_EDGE' if sides == 'DUPLEX' else 'NONE',
        'blankBacks': build.blank_backs,
        'contentRevision': 1,
        'sourcePages': list(range(1, pages + 1)),
        'settingsHash': 'h' * 64,
        'toolchain': {'engine': 'pdf_engine'},
    }


def _commerce_real(db, tmp_path, *, pages=3, copies=1, sides='SINGLE', binding='NONE'):
    """Create a checkout whose order is activated by the real commerce code."""
    from app.commerce import OrderSubmit, submit_order
    from app.config import get_config

    now = datetime.now(timezone.utc)
    principal = Principal()
    admin_staff = Staff(
        username='real-admin-' + uuid.uuid4().hex[:8],
        name='真实管理员',
        role='ADMIN',
        password_hash='x',
        enabled=True,
        status='ACTIVE',
        must_change_password=False,
        capabilities=['*'],
    )
    db.add_all([principal, admin_staff])
    db.flush()
    for section in ('services', 'announcement'):
        config = get_config(db, section)
        config.data = {**config.data, 'serviceEnabled': True}
    workflow = get_config(db, 'workflow')
    workflow.data = {**workflow.data, 'unpaidTimeoutMinutes': 60}
    prices = get_config(db, 'prices')
    prices.data = {**prices.data, 'bwSingle': 10, 'bwDuplex': 12}
    location = Location(name='二号楼', x=0.4, y=0.6)
    slot = DeliverySlot(
        date='2030-02-02',
        name='午后',
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=1, hours=1),
        capacity=2,
    )
    db.add_all([location, slot])
    db.flush()
    source_asset = Asset(
        principal_id=principal.id,
        purpose='PRINT_SOURCE',
        name='source.pdf',
        mime='application/pdf',
        size=1024,
        sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        storage_key='fixtures/real-source.pdf',
        status='READY',
        pages=pages,
    )
    db.add(source_asset)
    db.flush()
    build = _real_artifact_build(tmp_path, pages=pages, copies=copies, sides=sides, segment_id='real-segment')
    final_asset = Asset(
        principal_id=principal.id,
        purpose='FINAL_PDF',
        name='final.pdf',
        mime='application/pdf',
        size=2048,
        sha256=build.sha256,
        storage_key='fixtures/real-final.pdf',
        status='READY',
        pages=build.pages,
    )
    db.add(final_asset)
    db.flush()
    preparation = Preparation(
        principal_id=principal.id,
        status='READY',
        expires_at=now + timedelta(hours=24),
        input={'files': []},
        data={},
    )
    db.add(preparation)
    db.flush()
    artifact = Artifact(
        preparation_id=preparation.id,
        asset_id=final_asset.id,
        client_file_id='real-file',
        client_segment_id='real-segment',
        sha256=build.sha256,
        pages=build.pages,
        sheets=build.sheets,
        settings=_settings_for_build(
            build, copies=copies, sides=sides, pages=pages, segment_id='real-segment'
        ),
        page_map=list(build.page_map),
    )
    db.add(artifact)
    db.flush()
    quantity = build.sheets
    quote = Quote(
        preparation_id=preparation.id,
        price_version=prices.version,
        expires_at=now + timedelta(minutes=15),
        subtotal_cents=quantity * 10,
        discount_cents=0,
        total_cents=quantity * 10,
        data={
            'id': 'real-quote',
            'priceVersion': prices.version,
            'lines': [
                {
                    'key': 'a4-real',
                    'relatedId': artifact.id,
                    'unit': 'SHEET',
                    'quantity': quantity,
                    'unitPriceCents': 10,
                    'amountCents': quantity * 10,
                    'service': 'A4',
                }
            ],
            'discounts': [],
            'discountAllocations': [],
            'selectionMode': 'AUTO',
            'selectedPromotionType': 'NONE',
            'deliveryFeeCents': 0,
            'volumeDiscountCandidate': None,
            'selectedEligibleSubtotalCents': 0,
            'checkoutAccountId': None,
        },
    )
    db.add(quote)
    db.flush()
    file_input = {
        'clientFileId': 'real-file',
        'assetId': source_asset.id,
        'kind': 'DOCUMENT',
        'segments': [
            {
                'clientSegmentId': 'real-segment',
                'ranges': [{'from': 1, 'to': pages}],
                'colorMode': 'BW',
                'sides': sides,
                'flip': 'LONG_EDGE' if sides == 'DUPLEX' else 'NONE',
                'copies': copies,
            }
        ],
        'excludedPages': [],
        'bindingType': binding,
    }
    preparation.input = {'files': [file_input]}
    preparation.quote_id = quote.id
    order = submit_order(
        db,
        Actor(principal_id=principal.id, guest_principal_id=principal.id),
        OrderSubmit.model_validate(
            {
                'preparationId': preparation.id,
                'quoteId': quote.id,
                'previewAcks': [{'artifactId': artifact.id, 'sha256': artifact.sha256}],
                'contact': {'wechatNickname': '真实链路', 'phone': '13900000000'},
                'locationId': location.id,
                'slotId': slot.id,
                'customerNote': '',
            }
        ),
    )
    db.commit()
    db.expire_all()
    order = db.get(Order, order.id)
    file = db.scalar(select(OrderFile).where(OrderFile.order_id == order.id, OrderFile.revision == order.content_revision))
    task = db.scalar(select(PrintTask).where(PrintTask.file_id == file.id, PrintTask.active.is_(True)))
    binding_task = db.scalar(select(BindingTask).where(BindingTask.file_id == file.id))
    packing = db.scalar(select(PackingTask).where(PackingTask.order_id == order.id))
    return types.SimpleNamespace(
        order=order,
        preparation=preparation,
        artifact=artifact,
        file=file,
        task=task,
        binding=binding_task,
        packing=packing,
        build=build,
        admin=Actor(
            staff_id=admin_staff.id,
            staff_role='ADMIN',
            capabilities={'*'},
        ),
    )


def _order_admin_view(client_api, admin_client, order_id, key):
    response = client_api(
        admin_client, f'/admin/orders/{order_id}', method='GET', key=key
    )
    assert response.status_code == 200, response.text
    return response.json()['data']


def _settle_real(api, admin_client, order, key='real-settle'):
    response = api(
        admin_client,
        f'/admin/orders/{order.id}/confirm-payment',
        {
            'expectedVersion': order.version,
            'contentRevision': order.content_revision,
            'quoteId': order.quote_id,
            'receivedCents': order.total_cents,
            'settlementKind': 'CASH_MANUAL',
            'note': '',
        },
        key=key,
    )
    assert response.status_code == 200, response.text
    return response.json()['data']


def _run_real_to_packed(api, admin_client, db, seed, key_prefix):
    db.expire_all()
    order = db.get(Order, seed.order.id)
    view = _order_admin_view(api, admin_client, order.id, key_prefix + '-view0')
    start = api(
        admin_client,
        f'/admin/orders/{order.id}/start-production',
        {'expectedVersion': view['version'], 'reason': '真实链路开印'},
        key=key_prefix + '-start',
    )
    assert start.status_code == 200, start.text
    view = _order_admin_view(api, admin_client, order.id, key_prefix + '-view1')
    attempt = api(
        admin_client,
        f"/admin/print-tasks/{seed.task.id}/attempts",
        {'expectedVersion': view['version'], 'artifactId': seed.artifact.id},
        key=key_prefix + '-attempt',
    )
    assert attempt.status_code == 200, attempt.text
    attempt_id = attempt.json()['data']['attemptId']
    view = _order_admin_view(api, admin_client, order.id, key_prefix + '-view2')
    result = api(
        admin_client,
        f'/admin/print-attempts/{attempt_id}/result',
        {'expectedVersion': view['version'], 'outcome': 'COMPLETED', 'reason': ''},
        key=key_prefix + '-result',
    )
    assert result.status_code == 200, result.text
    if seed.binding is not None:
        view = _order_admin_view(api, admin_client, order.id, key_prefix + '-view3')
        binding = api(
            admin_client,
            f'/admin/binding-tasks/{seed.binding.id}/complete',
            {'expectedVersion': view['version']},
            key=key_prefix + '-binding',
        )
        assert binding.status_code == 200, binding.text
    view = _order_admin_view(api, admin_client, order.id, key_prefix + '-view4')
    packed = api(
        admin_client,
        f'/admin/orders/{order.id}/pack',
        {
            'expectedVersion': view['version'],
            'checklistVersion': view['packingChecklistVersion'],
            'checks': [{'fileId': seed.file.id, 'confirmed': True}],
        },
        key=key_prefix + '-pack',
    )
    assert packed.status_code == 200, packed.text
    return _order_admin_view(api, admin_client, seed.order.id, key_prefix + '-view5')


def test_real_commerce_activation_production_flow_and_version_parity(
    app, client, admin_client, api, db, tmp_path
):
    seed = _commerce_real(
        db, tmp_path, pages=3, copies=2, sides='DUPLEX', binding='CORNER'
    )
    # Commerce activation really produced these rows; production must not need
    # fixture-only settings keys.
    assert seed.task.settings == seed.artifact.settings
    assert seed.task.settings['segmentId'] == 'real-segment'
    assert seed.file.data['segments'][0]['copies'] == 2
    assert seed.file.data['bindingType'] == 'CORNER'
    assert len(seed.artifact.page_map) == seed.artifact.pages
    assert seed.artifact.sheets == seed.build.sheets == 4
    # Both the current production task id and the legacy file id resolve to the
    # same binding row used by the old frontend.
    from app.production import _resolve_binding, _resolve_task

    assert _resolve_task(db, seed.file.client_file_id).id == seed.task.id
    assert _resolve_binding(db, seed.file.id).id == seed.binding.id
    assert _resolve_binding(db, seed.file.client_file_id).id == seed.binding.id

    view = _settle_real(api, admin_client, seed.order)
    assert view['status'] == 'PENDING_PRINT'
    db.expire_all()
    assert view['version'] == db.get(Order, seed.order.id).version
    assert view['packingStatus'] == 'BLOCKED'
    file_view = view['files'][0]
    segment = file_view['segments'][0]
    assert segment['id'] == seed.task.id
    assert segment['version'] == seed.task.version
    assert file_view['bindingTaskId'] == seed.binding.id
    assert file_view['bindingStatus'] == 'WAITING_PRINT'
    assert view['packingChecklistVersion'] == seed.order.content_revision

    queue = api(
        admin_client,
        f'/admin/print-tasks?orderId={seed.order.id}',
        method='GET',
        key='real-queue',
    )
    assert queue.status_code == 200, queue.text
    task_view = queue.json()['data']['tasks'][0]
    assert task_view['id'] == seed.task.id
    assert task_view['orderVersion'] == view['version']
    assert task_view['sheetCount'] == seed.artifact.sheets
    assert len(task_view['sheetCatalog']) == seed.artifact.sheets
    assert sum(row['blankBack'] for row in task_view['sheetCatalog']) == seed.build.blank_backs == 2

    started = api(
        admin_client,
        f'/admin/orders/{seed.order.id}/start-production',
        {'expectedVersion': view['version'], 'reason': '真实 commerce 链路开印'},
        key='real-start',
    )
    assert started.status_code == 200, started.text
    started_at = started.json()['data']['actualStartedAt']
    view = _order_admin_view(api, admin_client, seed.order.id, 'real-after-start')
    assert view['actualStartedAt'] == started_at
    db.expire_all()
    assert view['version'] == db.get(Order, seed.order.id).version
    db.expire_all()
    assert db.get(Order, seed.order.id).actual_started_at.isoformat() == started_at

    attempt = api(
        admin_client,
        f'/admin/print-tasks/{seed.task.id}/attempts',
        {'expectedVersion': view['version'], 'artifactId': seed.artifact.id},
        key='real-attempt',
    )
    assert attempt.status_code == 200, attempt.text
    attempt_id = attempt.json()['data']['attemptId']
    view = _order_admin_view(api, admin_client, seed.order.id, 'real-after-attempt')
    assert view['files'][0]['attemptIds'][seed.task.id] == attempt_id
    db.expire_all()
    attempt_row = db.get(PrintAttempt, attempt_id)
    assert view['files'][0]['attemptVersions'][seed.task.id] == attempt_row.version
    db.expire_all()
    assert view['files'][0]['taskVersions'][seed.task.id] == db.get(PrintTask, seed.task.id).version
    task_view = api(
        admin_client, f'/admin/print-tasks/{seed.task.id}', method='GET', key='real-task'
    ).json()['data']
    assert task_view['orderVersion'] == view['version']
    assert task_view['status'] == 'ATTEMPTED'
    assert task_view['openAttemptId'] == attempt_id

    result = api(
        admin_client,
        f'/admin/print-attempts/{attempt_id}/result',
        {'expectedVersion': view['version'], 'outcome': 'COMPLETED', 'reason': ''},
        key='real-result',
    )
    assert result.status_code == 200, result.text
    view = _order_admin_view(api, admin_client, seed.order.id, 'real-after-result')
    db.expire_all()
    assert view['version'] == db.get(Order, seed.order.id).version
    assert view['files'][0]['printStatus'] == 'COMPLETED'
    assert view['files'][0]['taskVersions'][seed.task.id] == db.get(PrintTask, seed.task.id).version
    assert view['files'][0]['bindingStatus'] == 'READY'
    assert view['packingStatus'] == 'BLOCKED'

    binding = api(
        admin_client,
        f'/admin/binding-tasks/{seed.binding.id}/complete',
        {'expectedVersion': view['version']},
        key='real-binding',
    )
    assert binding.status_code == 200, binding.text
    view = _order_admin_view(api, admin_client, seed.order.id, 'real-after-binding')
    db.expire_all()
    assert view['version'] == db.get(Order, seed.order.id).version
    assert view['files'][0]['bindingStatus'] == 'COMPLETED'
    db.expire_all()
    assert view['files'][0]['bindingVersion'] == db.get(BindingTask, seed.binding.id).version
    assert view['packingStatus'] == 'READY'

    packed = api(
        admin_client,
        f'/admin/orders/{seed.order.id}/pack',
        {
            'expectedVersion': view['version'],
            'checklistVersion': view['packingChecklistVersion'],
            'checks': [{'fileId': seed.file.id, 'confirmed': True}],
        },
        key='real-pack',
    )
    assert packed.status_code == 200, packed.text
    view = _order_admin_view(api, admin_client, seed.order.id, 'real-after-pack')
    db.expire_all()
    assert view['version'] == db.get(Order, seed.order.id).version
    assert view['status'] == 'READY_FOR_DELIVERY'
    assert view['packingStatus'] == 'PACKED'
    db.expire_all()
    packing = db.get(PackingTask, seed.packing.id)
    assert view['packingVersion'] == packing.version
    assert view['packingChecklistVersion'] == packing.manifest_revision == view['contentRevision']


def test_refund_review_lock_blocks_new_production_but_not_existing_attempt_result(
    app, client, admin_client, api, db, tmp_path
):
    from app.after_sales import CaseInput, ReviewInput, create_case, review_case

    seed = _commerce_real(db, tmp_path, pages=2, copies=1, sides='SINGLE', binding='CORNER')
    view = _settle_real(api, admin_client, seed.order)
    attempt = api(
        admin_client,
        f'/admin/print-tasks/{seed.task.id}/attempts',
        {'expectedVersion': view['version'], 'artifactId': seed.artifact.id},
        key='lock-attempt',
    )
    assert attempt.status_code == 200, attempt.text
    attempt_id = attempt.json()['data']['attemptId']
    # Opening a print attempt never writes the physical-start fact.
    db.expire_all()
    assert db.get(Order, seed.order.id).actual_started_at is None

    order = db.get(Order, seed.order.id)
    case = create_case(
        db,
        seed.admin,
        order,
        CaseInput(
            responsibility='CUSTOMER',
            solution='REFUND',
            reason='尚未实际开印，申请取消',
            expected_version=order.version,
        ),
        admin=True,
    )
    db.commit()
    db.expire_all()
    order = db.get(Order, seed.order.id)
    assert order.refund_locked is True

    view = _order_admin_view(api, admin_client, seed.order.id, 'lock-view')
    start = api(
        admin_client,
        f'/admin/orders/{seed.order.id}/start-production',
        {'expectedVersion': view['version'], 'reason': '退款审核中'},
        key='lock-start',
    )
    assert start.status_code == 409 and start.json()['error']['code'] == 'REFUND_LOCKED'
    retry = api(
        admin_client,
        f'/admin/print-tasks/{seed.task.id}/attempts',
        {'expectedVersion': view['version'], 'artifactId': seed.artifact.id},
        key='lock-retry',
    )
    assert retry.status_code == 409 and retry.json()['error']['code'] == 'REFUND_LOCKED'
    binding_locked = api(
        admin_client,
        f'/admin/binding-tasks/{seed.binding.id}/complete',
        {'expectedVersion': view['version']},
        key='lock-binding',
    )
    assert binding_locked.status_code == 409
    assert binding_locked.json()['error']['code'] == 'REFUND_LOCKED'
    reprint_locked = api(
        admin_client,
        f'/admin/print-tasks/{seed.task.id}/reprints',
        {'expectedVersion': view['version'], 'mode': 'WHOLE', 'reason': '退款审核中补打'},
        key='lock-reprint',
    )
    assert reprint_locked.status_code == 409
    assert reprint_locked.json()['error']['code'] == 'REFUND_LOCKED'
    pack = api(
        admin_client,
        f'/admin/orders/{seed.order.id}/pack',
        {
            'expectedVersion': view['version'],
            'checklistVersion': view['packingChecklistVersion'],
            'checks': [{'fileId': seed.file.id, 'confirmed': True}],
        },
        key='lock-pack',
    )
    assert pack.status_code == 409 and pack.json()['error']['code'] == 'REFUND_LOCKED'

    # Q04: the pre-existing attempt must still be resolvable so the refund
    # decision can be evidence-based; resolving it is not new production.
    resolved = api(
        admin_client,
        f'/admin/print-attempts/{attempt_id}/result',
        {'expectedVersion': view['version'], 'outcome': 'FAILED', 'reason': '核实：未实际出纸'},
        key='lock-result',
    )
    assert resolved.status_code == 200, resolved.text
    db.expire_all()
    assert db.get(PrintAttempt, attempt_id).outcome == 'FAILED'
    assert db.get(Order, seed.order.id).actual_started_at is None

    order = db.get(Order, seed.order.id)
    case = db.get(AfterSale, case.id)
    reviewed = review_case(
        db,
        seed.admin,
        case,
        ReviewInput(
            expected_version=case.version,
            decision='APPROVE',
            solution='REFUND',
            reason='已核实无实际开印',
        ),
    )
    db.commit()
    assert reviewed.status == 'REFUND_APPROVED'
    db.expire_all()
    assert db.get(Order, seed.order.id).refund_locked is True


def test_after_sales_merchant_rework_pack_then_reprint_ready_releases_delivery_block(
    app, client, admin_client, api, db, tmp_path
):
    from app.after_sales import (
        CaseAction,
        CaseInput,
        ReviewInput,
        create_case,
        review_case,
        reprint_ready,
    )

    seed = _commerce_real(
        db, tmp_path, pages=2, copies=1, sides='SINGLE', binding='SIDE'
    )
    view = _settle_real(api, admin_client, seed.order)
    view = _run_real_to_packed(api, admin_client, db, seed, 'rework')
    assert view['packingStatus'] == 'PACKED'

    db.expire_all()
    order = db.get(Order, seed.order.id)
    case = create_case(
        db,
        seed.admin,
        order,
        CaseInput(
            responsibility='MERCHANT',
            solution='REPRINT',
            reason='商家责任重做',
            expected_version=order.version,
        ),
        admin=True,
    )
    case = review_case(
        db,
        seed.admin,
        case,
        ReviewInput(
            expected_version=case.version,
            decision='APPROVE',
            solution='REPRINT',
            reason='核实为重做',
        ),
    )
    db.commit()
    db.expire_all()
    order = db.get(Order, seed.order.id)
    task = db.get(PrintTask, seed.task.id)
    binding = db.get(BindingTask, seed.binding.id)
    packing = db.get(PackingTask, seed.packing.id)
    assert case.status == 'REPRINTING'
    assert task.status == 'EXCEPTION'
    assert binding.status == 'WAITING_PRINT'
    assert packing.status == 'BLOCKED'
    assert order.status == 'PENDING_PRINT'
    assert order.packing_status == 'BLOCKED'
    exception = db.scalar(
        select(BusinessException).where(
            BusinessException.order_id == order.id,
            BusinessException.type == 'AFTER_SALE_REPRINT',
            BusinessException.status == 'OPEN',
        )
    )
    assert exception is not None
    assert exception.blocks_delivery is True
    assert exception.blocks_production is False

    view = _order_admin_view(api, admin_client, seed.order.id, 'rework-view')
    reprint = api(
        admin_client,
        f'/admin/print-tasks/{seed.task.id}/reprints',
        {'expectedVersion': view['version'], 'mode': 'WHOLE', 'reason': '商家责任重做'},
        key='rework-reprint',
    )
    assert reprint.status_code == 200, reprint.text
    assert reprint.json()['data']['artifactId'] == seed.artifact.id
    reprint_id = reprint.json()['data']['attemptId']
    db.expire_all()
    assert db.get(PrintTask, seed.task.id).artifact_id == seed.artifact.id
    view = _order_admin_view(api, admin_client, seed.order.id, 'rework-after-reprint')
    result = api(
        admin_client,
        f'/admin/print-attempts/{reprint_id}/result',
        {'expectedVersion': view['version'], 'outcome': 'COMPLETED', 'reason': ''},
        key='rework-result',
    )
    assert result.status_code == 200, result.text
    view = _order_admin_view(api, admin_client, seed.order.id, 'rework-after-result')
    binding_done = api(
        admin_client,
        f'/admin/binding-tasks/{seed.binding.id}/complete',
        {'expectedVersion': view['version']},
        key='rework-binding-2',
    )
    assert binding_done.status_code == 200, binding_done.text
    view = _order_admin_view(api, admin_client, seed.order.id, 'rework-after-binding')
    packing_queue = api(admin_client, '/admin/packing-tasks', method='GET', key='rework-pack-queue')
    assert packing_queue.status_code == 200, packing_queue.text
    packing_task = next(
        row for row in packing_queue.json()['data']['tasks'] if row['orderId'] == seed.order.id
    )
    # The frontend disables packing when checklist.blockers is non-empty; a
    # delivery-only AFTER_SALE_REPRINT block must not leak into that list.
    assert packing_task['checklist']['blockers'] == []
    db.expire_all()
    assert production.order_production_view(db, db.get(Order, seed.order.id))['deliveryBlocked'] is True
    # Packing must be possible while the AFTER_SALE_REPRINT exception is open:
    # reprint-ready is the explicit step that releases the delivery block.
    packed = api(
        admin_client,
        f'/admin/orders/{seed.order.id}/pack',
        {
            'expectedVersion': view['version'],
            'checklistVersion': view['packingChecklistVersion'],
            'checks': [{'fileId': seed.file.id, 'confirmed': True}],
        },
        key='rework-pack-2',
    )
    assert packed.status_code == 200, packed.text
    db.expire_all()
    exception = db.get(BusinessException, exception.id)
    assert exception.status == 'OPEN'
    assert db.get(Order, seed.order.id).status == 'READY_FOR_DELIVERY'
    open_delivery_blocks = db.query(BusinessException).filter(
        BusinessException.order_id == seed.order.id,
        BusinessException.status == 'OPEN',
        BusinessException.blocks_delivery.is_(True),
    ).count()
    assert open_delivery_blocks == 1

    case = db.get(AfterSale, case.id)
    ready = reprint_ready(
        case.id,
        CaseAction(expected_version=case.version, reason='重做已装袋，可重新配送'),
        db=db,
        actor=seed.admin,
    )
    db.commit()
    db.expire_all()
    assert db.get(AfterSale, case.id).status == 'REPRINT_READY'
    assert db.get(BusinessException, exception.id).status == 'RESOLVED'
    order = db.get(Order, seed.order.id)
    assert order.status == 'READY_FOR_DELIVERY'
    assert order.packing_status == 'PACKED'
    open_delivery_blocks = db.query(BusinessException).filter(
        BusinessException.order_id == order.id,
        BusinessException.status == 'OPEN',
        BusinessException.blocks_delivery.is_(True),
    ).count()
    assert open_delivery_blocks == 0


def test_refund_lock_also_allows_existing_attempt_completion_when_start_was_recorded(
    app, client, admin_client, api, db, tmp_path
):
    from app.after_sales import CaseInput, create_case

    seed = _commerce_real(db, tmp_path, pages=2, copies=1, sides='SINGLE')
    view = _settle_real(api, admin_client, seed.order)
    start = api(
        admin_client,
        f'/admin/orders/{seed.order.id}/start-production',
        {'expectedVersion': view['version'], 'reason': '已实际出纸'},
        key='completed-lock-start',
    )
    assert start.status_code == 200, start.text
    view = _order_admin_view(api, admin_client, seed.order.id, 'completed-lock-view')
    attempt = api(
        admin_client,
        f'/admin/print-tasks/{seed.task.id}/attempts',
        {'expectedVersion': view['version'], 'artifactId': seed.artifact.id},
        key='completed-lock-attempt',
    )
    assert attempt.status_code == 200, attempt.text
    attempt_id = attempt.json()['data']['attemptId']

    db.expire_all()
    order = db.get(Order, seed.order.id)
    case = create_case(
        db,
        seed.admin,
        order,
        CaseInput(
            responsibility='MERCHANT',
            solution='REFUND',
            reason='商家责任退款核实',
            expected_version=order.version,
        ),
        admin=True,
    )
    db.commit()
    db.expire_all()
    assert db.get(Order, seed.order.id).refund_locked is True

    view = _order_admin_view(api, admin_client, seed.order.id, 'completed-lock-view2')
    resolved = api(
        admin_client,
        f'/admin/print-attempts/{attempt_id}/result',
        {'expectedVersion': view['version'], 'outcome': 'COMPLETED', 'reason': ''},
        key='completed-lock-result',
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()['data']['taskStatus'] == 'COMPLETED'
    db.expire_all()
    assert db.get(PrintAttempt, attempt_id).outcome == 'COMPLETED'
    assert db.get(Order, seed.order.id).actual_started_at is not None
    assert db.get(AfterSale, case.id).status == 'REQUESTED'
