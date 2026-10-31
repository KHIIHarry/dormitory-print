"""Real ASGI event loop regression; separate TestClient loops hide this race."""
import asyncio
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import httpx
from sqlalchemy import select


def test_cancelled_database_wait_finishes_before_session_can_be_closed():
    from app.main import _database_call
    started,release,finished=threading.Event(),threading.Event(),threading.Event()
    pool=ThreadPoolExecutor(max_workers=1)
    def operation():
        started.set()
        release.wait(2)
        finished.set()
    async def run():
        task=asyncio.create_task(_database_call(pool,operation))
        assert await asyncio.to_thread(started.wait,1)
        task.cancel()
        asyncio.get_running_loop().call_later(.05,release.set)
        with pytest.raises(asyncio.CancelledError):await task
        assert finished.is_set(), 'Session finalization must not race a still-running database call'
    try:asyncio.run(run())
    finally:
        release.set()
        pool.shutdown(wait=True)


@pytest.mark.parametrize('slow',[False,True])
def test_concurrent_idempotent_http_requests_do_not_block_the_event_loop(db,tmp_path,slow):
    module=tmp_path/'race_server.py'
    module.write_text('''
import time
from fastapi import Request
from app.main import create_app
from app.core import ok
from app.models import ConfigSection
app=create_app(modules=[])
@app.post('/api/v1/race')
def mutate(request:Request):
    time.sleep(1.5 if request.query_params.get('slow') else .25)
    row=ConfigSection(section='http-race'+request.query_params.get('tag',''),data={'count':1})
    request.state.db.add(row)
    request.state.db.flush()
    return ok({'id':row.id,'count':1})
''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    origin=f'http://127.0.0.1:{port}'
    env={**os.environ,'DORMPRINT_PUBLIC_ORIGIN':origin,
         'PYTHONPATH':str(tmp_path)+os.pathsep+str(Path(__file__).resolve().parents[1])}
    with (tmp_path/'server.log').open('w+') as log:
        server=subprocess.Popen([sys.executable,'-m','uvicorn','race_server:app','--host','127.0.0.1','--port',str(port)],env=env,stdout=log,stderr=subprocess.STDOUT)
        try:
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                try:
                    if httpx.get(origin+'/api/v1/health/live',timeout=.5).status_code==200:break
                except httpx.HTTPError:time.sleep(.1)
            else:raise AssertionError('ASGI server did not start')
            async def run():
                async with httpx.AsyncClient(base_url=origin,timeout=5) as client:
                    await client.get('/api/v1/health/live')
                    headers={'Origin':origin,'X-CSRF-Token':client.cookies['csrfToken'],'Idempotency-Key':'concurrent-same-request'}
                    path='/api/v1/race'+('?slow=1' if slow else '')
                    first=asyncio.create_task(client.post(path,json={'value':1},headers=headers))
                    await asyncio.sleep(.05)
                    others=[asyncio.create_task(client.post(path,json={'value':1},headers=headers)) for _ in range(16)]
                    if slow:
                        await asyncio.sleep(.05)
                        unrelated=await asyncio.wait_for(client.post('/api/v1/race?tag=-unrelated',json={'value':2},headers={**headers,'Idempotency-Key':'unrelated-write'}),timeout=.9)
                        assert unrelated.status_code==200
                        assert not first.done(), 'Unrelated work must complete while duplicates still wait'
                    return await asyncio.gather(first,*others)
            responses=asyncio.run(run())
            assert all(response.status_code==200 for response in responses),[response.text for response in responses]
            assert len({response.json()['data']['id'] for response in responses})==1
            from app.models import ConfigSection
            rows=list(db.scalars(select(ConfigSection).where(ConfigSection.section=='http-race')))
            assert len(rows)==1 and rows[0].data=={'count':1}
        finally:
            server.terminate()
            try:server.wait(timeout=2)
            except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)
