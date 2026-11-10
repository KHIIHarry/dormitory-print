"""Opt-in real checkout acceptance: Chrome -> Vite -> FastAPI -> PostgreSQL -> real jobs.

The browser uploads an actual PDF, the real DB-backed worker converts it, and the
real review/contact/submit UI creates the order.  Nothing intercepts or mocks the
API, and no upload is pre-marked READY.
"""
from __future__ import annotations

from datetime import timedelta
import hashlib
import json
import os
import secrets
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

pytest.importorskip('pymupdf')
pytestmark = pytest.mark.skipif(
    os.environ.get('DORMPRINT_RUN_BROWSER_TESTS') != '1',
    reason='Opt in: DORMPRINT_RUN_BROWSER_TESTS=1; requires installed Chrome and node_modules',
)

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_v1_integration as base
from app.core import token_hash
from app.models import (
    Artifact,
    Asset,
    AuthSession,
    DeliveryAssignment,
    DeliveryBatch,
    DeliveryChange,
    DeliveryNotice,
    DeliverySlot,
    Job,
    Location,
    Order,
    OrderFile,
    Preparation,
    Quote,
    utcnow,
)
from app.settings import get_settings

# The in-process admin client must see the same routers as the real server.
APP_MODULES = [
    'auth', 'growth', 'mail', 'media', 'commerce', 'production',
    'delivery', 'after_sales', 'exports', 'maintenance',
]


def _account_client(client_factory, db, account):
    raw = secrets.token_urlsafe(32)
    db.add(AuthSession(
        kind='CUSTOMER', account_id=account.id,
        principal_id=account.principal_id,
        session_version=account.session_version,
        token_hash=token_hash(raw),
        expires_at=utcnow() + timedelta(days=2),
    ))
    db.commit()
    client = client_factory()
    client.cookies.set('dormprint_account_session', raw)
    return client


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_ready(process, url):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('Acceptance server exited before readiness')
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(.1)
    raise RuntimeError('Acceptance server readiness timed out: ' + url)


def test_real_checkout_upload_ready_and_submit(
    db, account_factory, staff_factory, client_factory, api, admin_client, tmp_path
):
    root = Path(__file__).resolve().parents[2]
    database_url = os.environ.get('DORMPRINT_DATABASE_URL')
    assert database_url, 'conftest must provide DORMPRINT_DATABASE_URL'
    assert 'test_' in database_url, 'refusing to run against a non-test schema'

    base.seed_config(db)
    account = account_factory(
        email='live-checkout@example.test', display_name='真实浏览器验收客户'
    )
    campus = base.ensure_test_campus(db)
    now = utcnow()
    location = Location(name='验收宿舍楼', x=0.4, y=0.6, campus_id=campus.id)
    slot = DeliverySlot(
        campus_id=campus.id,
        date=(now + timedelta(days=1)).strftime('%Y-%m-%d'),
        name='验收时段', starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=1, hours=1), capacity=2,
        open_at=now - timedelta(hours=1),
        cutoff_at=now + timedelta(hours=2),
    )
    slot2 = DeliverySlot(
        campus_id=campus.id,
        date=(now + timedelta(days=1)).strftime('%Y-%m-%d'),
        name='验收改期时段', starts_at=now + timedelta(days=1, hours=3),
        ends_at=now + timedelta(days=1, hours=4), capacity=2,
        open_at=now - timedelta(hours=1),
        cutoff_at=now + timedelta(hours=2),
    )
    db.add_all([location, slot, slot2])
    db.commit()

    pdf_path = tmp_path / 'checkout.pdf'
    pdf_path.write_bytes(base.make_pdf(2))
    assert pdf_path.stat().st_size > 0

    api_port, ui_port = free_port(), free_port()
    origin = f'http://127.0.0.1:{ui_port}'
    env = {
        **os.environ,
        'DORMPRINT_PUBLIC_ORIGIN': origin,
        'DORMPRINT_DEV_API_TARGET': f'http://127.0.0.1:{api_port}',
        'VITE_DEMO_MODE': 'false',
        'DORMPRINT_BROWSER_ORIGIN': origin,
        'DORMPRINT_BROWSER_EMAIL': account.email,
        'DORMPRINT_BROWSER_PDF': str(pdf_path),
        'DORMPRINT_BROWSER_BUILDING': location.name,
        'DORMPRINT_BROWSER_SLOT': slot.name,
        'DORMPRINT_BROWSER_CAMPUS': campus.name,
        'PYTHONUNBUFFERED': '1',
    }
    processes, logs = [], []
    try:
        api_log = (tmp_path / 'api.log').open('w+')
        logs.append(api_log)
        processes.append(subprocess.Popen(
            [sys.executable, '-m', 'uvicorn', 'app.main:create_app', '--factory',
             '--host', '127.0.0.1', '--port', str(api_port)],
            cwd=root / 'backend', env=env, stdout=api_log,
            stderr=subprocess.STDOUT,
        ))
        wait_ready(processes[-1], f'http://127.0.0.1:{api_port}/api/v1/health/ready')

        ui_log = (tmp_path / 'ui.log').open('w+')
        logs.append(ui_log)
        processes.append(subprocess.Popen(
            [shutil.which('node'), 'node_modules/vite/bin/vite.js',
             '--host', '127.0.0.1', '--port', str(ui_port), '--strictPort'],
            cwd=root, env=env, stdout=ui_log, stderr=subprocess.STDOUT,
        ))
        wait_ready(processes[-1], origin + '/u/')

        worker_script = (
            'import time, traceback\n'
            'from app.jobs import run_one\n'
            'while True:\n'
            '    try:\n'
            "        run_one(('ASSET','PREPARATION'))\n"
            '    except Exception:\n'
            '        traceback.print_exc()\n'
            '    time.sleep(0.1)\n'
        )
        worker_log = (tmp_path / 'worker.log').open('w+')
        logs.append(worker_log)
        processes.append(subprocess.Popen(
            [sys.executable, '-u', '-c', worker_script],
            cwd=root / 'backend', env=env, stdout=worker_log,
            stderr=subprocess.STDOUT,
        ))
        time.sleep(.5)
        assert processes[-1].poll() is None, 'real job worker exited early'

        result = subprocess.run(
            [shutil.which('node'), 'tests/real-checkout-driver.mjs'],
            cwd=root, env=env, capture_output=True, text=True, timeout=240,
        )
        assert result.returncode == 0, result.stdout + '\n' + result.stderr
        pass_lines = [
            line for line in result.stdout.splitlines()
            if line.startswith('REAL_CHECKOUT_PASS ')
        ]
        assert pass_lines, result.stdout + '\n' + result.stderr
        evidence = json.loads(pass_lines[-1][len('REAL_CHECKOUT_PASS '):])
        print('REAL_CHECKOUT_EVIDENCE ' + json.dumps(evidence, ensure_ascii=False), flush=True)

        db.expire_all()
        order = db.get(Order, evidence['orderId'])
        assert order is not None, evidence
        assert order.checkout_account_id == account.id
        assert order.principal_id == account.principal_id
        assert order.status == 'PENDING_CONFIRMATION'
        assert order.cash_received_cents == 0
        assert order.settlement_kind is None
        assert order.confirmed_slot_id is None
        assert order.total_cents > 0
        assert order.total_cents == evidence['totalCents']

        quote = db.get(Quote, order.quote_id)
        assert quote is not None
        assert quote.id == evidence['quoteId']
        assert quote.total_cents == order.total_cents
        assert quote.checkout_account_id == account.id

        requested_slot = db.get(DeliverySlot, order.requested_slot_id)
        assert requested_slot is not None and requested_slot.id == slot.id
        assert requested_slot.occupied == 0

        source_file = db.scalar(select(OrderFile).where(OrderFile.order_id == order.id))
        assert source_file is not None
        source = db.get(Asset, source_file.asset_id)
        assert source is not None and source.status == 'READY' and source.pages == 2

        preparation = db.get(Preparation, order.preparation_id)
        assert preparation is not None and preparation.status == 'READY'
        artifacts = db.scalars(
            select(Artifact).where(Artifact.preparation_id == preparation.id)
        ).all()
        assert artifacts, 'no immutable final artifact was created'
        storage_root = Path(get_settings().storage_root)
        for artifact in artifacts:
            final_asset = db.get(Asset, artifact.asset_id)
            assert final_asset is not None
            assert final_asset.purpose == 'PRINT_FINAL'
            assert final_asset.status == 'READY'
            path = storage_root / final_asset.storage_key
            assert path.is_file(), path
            assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact.sha256

        ready_jobs = db.scalar(
            select(func.count()).select_from(Job).where(
                Job.kind.in_(('ASSET', 'PREPARATION')), Job.status == 'READY'
            )
        )
        assert ready_jobs and ready_jobs >= 2

        for name in ('checkout-contact.png', 'checkout-success.png'):
            screenshot = root / 'docs/qa/live-checkout' / name
            assert screenshot.is_file() and screenshot.stat().st_size > 1000, screenshot

        # ---------- real settlement -> auto batch -> reschedule -> delivery ----------
        view = api(admin_client, f'/admin/orders/{order.id}', method='GET')
        assert view.status_code == 200, view.text
        settled = base.settle(api, admin_client, view.json()['data'],
                              'real-delivery-settle')
        assert settled.status_code == 200, settled.text
        paid = settled.json()['data']
        assert paid['status'] == 'PENDING_PRINT', paid
        db.expire_all()
        auto_assignment = db.scalar(select(DeliveryAssignment).where(
            DeliveryAssignment.order_id == order.id,
            DeliveryAssignment.active.is_(True)))
        assert auto_assignment is not None, 'settlement did not create an assignment'
        auto_batch = db.get(DeliveryBatch, auto_assignment.batch_id)
        assert auto_batch is not None and auto_batch.status == 'PLANNED'
        assert auto_batch.campus_id == campus.id
        assert db.get(DeliverySlot, slot.id).occupied == 1

        packed = base.run_print_to_packed(api, admin_client, paid)
        assert packed['status'] == 'READY_FOR_DELIVERY', packed

        changed = api(
            admin_client, f'/admin/orders/{order.id}/slot-change',
            {'expectedVersion': packed['version'], 'slotId': slot2.id,
             'reason': '真实链路验收：微信确认改到次日时段'},
            key='real-delivery-slot-change')
        assert changed.status_code == 200, changed.text

        customer = _account_client(client_factory, db, account)
        customer_view = api(customer, f'/orders/{order.id}', method='GET')
        assert customer_view.status_code == 200, customer_view.text
        arrangement = customer_view.json()['data']['arrangement']
        assert arrangement['effective']['id'] == slot2.id
        assert arrangement['originalConfirmation']['id'] == slot.id
        assert arrangement['pendingProposal'] is None
        db.expire_all()
        moved = db.get(Order, order.id)
        assert moved.confirmed_slot_id == slot2.id, moved.confirmed_slot_id
        moved_assignment = db.scalar(select(DeliveryAssignment).where(
            DeliveryAssignment.order_id == order.id,
            DeliveryAssignment.active.is_(True)))
        assert moved_assignment is not None
        moved_batch = db.get(DeliveryBatch, moved_assignment.batch_id)
        assert moved_batch.id != auto_batch.id, 'reschedule did not move the batch'

        courier = staff_factory(role='COURIER', capabilities=[])
        moved_batch.courier_id = courier.id
        moved_assignment.courier_id = courier.id
        db.commit()

        slot2_row = db.get(DeliverySlot, slot2.id)
        db.refresh(slot2_row)
        seal_preview = api(
            admin_client, f'/admin/slots/{slot2.id}/seal/preview',
            {'expectedVersion': slot2_row.version}, key='real-delivery-seal-preview')
        assert seal_preview.status_code == 200, seal_preview.text
        plan = seal_preview.json()['data']
        assert plan['blockers'] == [], plan
        sealed = api(
            admin_client, f'/admin/slots/{slot2.id}/seal',
            {'expectedVersion': slot2_row.version, 'planId': plan['planId'],
             'reason': '真实链路验收封批'},
            key='real-delivery-seal')
        assert sealed.status_code == 200, sealed.text

        db.expire_all()
        moved_batch = db.get(DeliveryBatch, moved_batch.id)
        published = api(
            admin_client, f'/admin/batches/{moved_batch.id}/publish',
            {'expectedVersion': moved_batch.version, 'reason': '真实链路验收发布'},
            key='real-delivery-publish')
        assert published.status_code == 200, published.text
        started = api(
            admin_client, f'/admin/batches/{moved_batch.id}/start',
            {'expectedVersion': published.json()['data']['version'],
             'reason': '真实链路验收出发'},
            key='real-delivery-start')
        assert started.status_code == 200, started.text

        db.expire_all()
        moved_assignment = db.get(DeliveryAssignment, moved_assignment.id)
        courier_client = base.staff_client(client_factory, db, courier)
        proof_photo = base.upload_proof(courier_client, moved_assignment)
        delivered = base.submit_proof(
            api, courier_client, order.id, moved_assignment,
            proof_photo['assetId'], key='real-delivery-proof')
        assert delivered['order']['status'] == 'DELIVERED', delivered

        db.expire_all()
        final = db.get(Order, order.id)
        assert final.status == 'DELIVERED'
        kinds = set(db.scalars(select(DeliveryChange.kind).where(
            DeliveryChange.order_id == order.id)))
        assert 'RESCHEDULE_APPLIED' in kinds, kinds
        assert not ({'RESCHEDULE_PROPOSED', 'RESCHEDULE_CONFIRMED'} & kinds), kinds
        change_id = db.scalar(select(DeliveryChange.id).where(
            DeliveryChange.order_id == order.id, DeliveryChange.kind == 'RESCHEDULE_APPLIED'))
        notice = db.scalar(select(DeliveryNotice).where(DeliveryNotice.change_id == change_id))
        assert notice is not None, 'reschedule did not leave a delivery notice'
        print('REAL_DELIVERY_EVIDENCE ' + json.dumps({
            'orderId': order.id,
            'autoBatchId': auto_batch.id,
            'rescheduledBatchId': moved_batch.id,
            'changes': sorted(kinds),
            'noticeId': notice.id,
            'noticeStatus': notice.status,
        }, ensure_ascii=False), flush=True)
    finally:
        for process in reversed(processes):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for log in logs:
            log.close()
