"""Opt-in Chrome + live FastAPI/PostgreSQL/Vite acceptance; no API interception."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

pytestmark=pytest.mark.skipif(os.environ.get('DORMPRINT_RUN_BROWSER_TESTS')!='1',reason='Opt in: DORMPRINT_RUN_BROWSER_TESTS=1; requires installed Chrome and node_modules')


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


def wait_ready(process,url):
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        if process.poll() is not None:
            raise RuntimeError('Acceptance server exited before readiness')
        try:
            with urlopen(url,timeout=1) as response:
                if response.status==200:return
        except Exception:
            time.sleep(.1)
    raise RuntimeError('Acceptance server readiness timed out')


def test_live_browser_account_grants_and_revocation(db,account_factory,staff_factory,tmp_path):
    root=Path(__file__).resolve().parents[2]
    account=account_factory(email='browser-user@example.test',display_name='浏览器验收客户')
    admin=account_factory(email='browser-admin@example.test',display_name='浏览器验收管理员')
    staff_factory(account_id=admin.id,is_super_admin=True)
    db.commit()
    api_port,ui_port=free_port(),free_port()
    origin=f'http://127.0.0.1:{ui_port}'
    env={**os.environ,'DORMPRINT_PUBLIC_ORIGIN':origin,'DORMPRINT_DEV_API_TARGET':f'http://127.0.0.1:{api_port}','VITE_DEMO_MODE':'false','DORMPRINT_BROWSER_ORIGIN':origin,'DORMPRINT_BROWSER_ACCOUNT_ID':account.id}
    processes=[]
    logs=[]
    try:
        for label,args,cwd,url in (
            ('api',[sys.executable,'-m','uvicorn','app.main:create_app','--factory','--host','127.0.0.1','--port',str(api_port)],root/'backend',f'http://127.0.0.1:{api_port}/api/v1/health/ready'),
            ('ui',[shutil.which('node'),'node_modules/vite/bin/vite.js','--host','127.0.0.1','--port',str(ui_port),'--strictPort'],root,origin+'/u/')):
            log=(tmp_path/(label+'.log')).open('w+')
            logs.append(log)
            process=subprocess.Popen(args,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT)
            processes.append(process)
            wait_ready(process,url)
        result=subprocess.run([shutil.which('node'),'tests/real-browser-driver.mjs'],cwd=root,env=env,capture_output=True,text=True,timeout=150)
        assert result.returncode==0,result.stdout+'\n'+result.stderr
        assert 'REAL_BROWSER_PASS' in result.stdout
    finally:
        for process in reversed(processes):
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        for log in logs:log.close()


def test_live_guest_order_close_reset_and_claim(db, account_factory, staff_factory, tmp_path):
    from datetime import timedelta
    import secrets
    from app.core import Actor, token_hash
    from app.auth import issue_order_grant
    from app.models import AuthSession, Order, Principal, utcnow
    from auth_helpers import make_order
    from test_order_void import seed_quote

    root = Path(__file__).resolve().parents[2]
    account = account_factory(email='guest-browser-user@example.test', display_name='游客订单浏览器客户')
    admin = account_factory(email='guest-browser-admin@example.test', display_name='游客订单浏览器管理员')
    staff_factory(account_id=admin.id, is_super_admin=True)
    principal = Principal()
    db.add(principal)
    db.flush()
    order = seed_quote(db, make_order(db, principal.id))
    expired_order = seed_quote(db, make_order(db, principal.id))
    expired_grant, expired_token = issue_order_grant(db, expired_order, Actor(principal_id=principal.id))
    expired_grant.expires_at = utcnow() - timedelta(seconds=1)
    guest_token = secrets.token_urlsafe(32)
    db.add(AuthSession(kind='GUEST', principal_id=principal.id, token_hash=token_hash(guest_token),
                       expires_at=utcnow() + timedelta(days=30)))
    db.commit()
    api_port, ui_port = free_port(), free_port()
    origin = f'http://127.0.0.1:{ui_port}'
    env = {
        **os.environ, 'DORMPRINT_PUBLIC_ORIGIN': origin,
        'DORMPRINT_DEV_API_TARGET': f'http://127.0.0.1:{api_port}', 'VITE_DEMO_MODE': 'false',
        'DORMPRINT_BROWSER_ORIGIN': origin, 'DORMPRINT_BROWSER_ORDER_ID': order.id,
        'DORMPRINT_BROWSER_PICKUP_CODE': order.pickup_code, 'DORMPRINT_BROWSER_GUEST_COOKIE': guest_token,
        'DORMPRINT_BROWSER_EXPIRED_ORDER_ID': expired_order.id,
        'DORMPRINT_BROWSER_EXPIRED_TOKEN': expired_token,
        'DORMPRINT_BROWSER_OWNER_ID': account.id, 'DORMPRINT_BROWSER_QA_DIR': str(tmp_path),
    }
    processes, logs = [], []
    try:
        for label, args, cwd, url in (
            ('api', [sys.executable, '-m', 'uvicorn', 'app.main:create_app', '--factory', '--host',
                     '127.0.0.1', '--port', str(api_port)], root / 'backend',
             f'http://127.0.0.1:{api_port}/api/v1/health/ready'),
            ('ui', [shutil.which('node'), 'node_modules/vite/bin/vite.js', '--host',
                    '127.0.0.1', '--port', str(ui_port), '--strictPort'], root, origin + '/u/'),
        ):
            log = (tmp_path / (label + '.log')).open('w+')
            logs.append(log)
            process = subprocess.Popen(args, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
            processes.append(process)
            wait_ready(process, url)
        result = subprocess.run([shutil.which('node'), 'tests/real-guest-order-browser-driver.mjs'],
                                cwd=root, env=env, capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, result.stdout + '\n' + result.stderr
        assert 'REAL_GUEST_BROWSER_PASS' in result.stdout
        db.expire_all()
        current = db.get(Order, order.id)
        assert current.owner_account_id == account.id
        assert current.checkout_account_id is None
        assert current.guest_access_version == 5
        assert current.status == 'PENDING_CONFIRMATION'
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
