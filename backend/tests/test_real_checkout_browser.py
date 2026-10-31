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
from app.models import (
    Artifact,
    Asset,
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
    db, account_factory, tmp_path
):
    root = Path(__file__).resolve().parents[2]
    database_url = os.environ.get('DORMPRINT_DATABASE_URL')
    assert database_url, 'conftest must provide DORMPRINT_DATABASE_URL'
    assert 'test_' in database_url, 'refusing to run against a non-test schema'

    base.seed_config(db)
    account = account_factory(
        email='live-checkout@example.test', display_name='真实浏览器验收客户'
    )
    now = utcnow()
    location = Location(name='验收宿舍楼', x=0.4, y=0.6)
    slot = DeliverySlot(
        date=(now + timedelta(days=1)).strftime('%Y-%m-%d'),
        name='验收时段', starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=1, hours=1), capacity=2,
    )
    db.add_all([location, slot])
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
