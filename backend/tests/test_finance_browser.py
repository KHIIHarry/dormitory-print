"""Live FastAPI/PostgreSQL/Vite acceptance, without API interception."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from test_real_browser import free_port, wait_ready

pytestmark = pytest.mark.skipif(os.environ.get('DORMPRINT_RUN_BROWSER_TESTS') != '1', reason='Requires local Chrome and Node')


def test_live_finance_mobile_and_desktop(db, account_factory, staff_factory, tmp_path):
    from app.models import OperatingExpense
    root = Path(__file__).resolve().parents[2]
    account = account_factory(email='finance-browser-admin@example.test')
    staff = staff_factory(account_id=account.id, capabilities=['finance.manage'])
    today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
    for index in range(23):
        db.add(OperatingExpense(category='PAPER', spent_on=today,
            amount_cents=2147483647 if index == 0 else 100, paper_sheets=500, recorded_by=staff.id,
            note=('超长备注用于验证移动端换行与字间距' * 10 + 'long-unbroken-reference-' * 8) if index == 22 else f'采购记录 {index + 1}'))
    db.commit()
    api_port, ui_port = free_port(), free_port()
    origin = f'http://127.0.0.1:{ui_port}'
    env = {**os.environ, 'DORMPRINT_PUBLIC_ORIGIN': origin,
        'DORMPRINT_DEV_API_TARGET': f'http://127.0.0.1:{api_port}', 'VITE_DEMO_MODE': 'false',
        'DORMPRINT_BROWSER_ORIGIN': origin, 'DORMPRINT_FINANCE_TODAY': today.isoformat()}
    processes, logs = [], []
    try:
        for label, args, cwd, url in (
            ('api', [sys.executable, '-m', 'uvicorn', 'app.main:create_app', '--factory', '--host', '127.0.0.1', '--port', str(api_port)], root / 'backend', f'http://127.0.0.1:{api_port}/api/v1/health/ready'),
            ('ui', [shutil.which('node'), 'node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', str(ui_port), '--strictPort'], root, origin + '/u/')):
            log = (tmp_path / (label + '.log')).open('w+')
            logs.append(log)
            process = subprocess.Popen(args, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
            processes.append(process)
            wait_ready(process, url)
        result = subprocess.run([shutil.which('node'), 'tests/real-finance-driver.mjs'], cwd=root, env=env,
            capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, result.stdout + '\n' + result.stderr
        assert 'REAL_FINANCE_PASS' in result.stdout
    finally:
        for process in reversed(processes):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
        for log in logs:
            log.close()
