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
