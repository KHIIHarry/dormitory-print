import os
from pathlib import Path
import secrets
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text


@pytest.fixture(scope='session',autouse=True)
def database_schema():
    url=os.environ.get('DORMPRINT_TEST_DATABASE_URL','postgresql+psycopg://dormprint:local-test-only@127.0.0.1:55432/dormprint')
    if 'dormprint' not in url:
        raise RuntimeError('Tests require an explicitly dedicated DormPrint database')
    schema='test_'+str(os.getpid())+'_'+secrets.token_hex(4)
    root=create_engine(url)
    with root.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    configured=url+('&' if '?' in url else '?')+'options='+quote('-csearch_path='+schema,safe='')
    old={key:os.environ.get(key) for key in ('DORMPRINT_DATABASE_URL','DORMPRINT_ENVIRONMENT','DORMPRINT_SECRET_KEY','DORMPRINT_STORAGE_ROOT')}
    os.environ['DORMPRINT_DATABASE_URL']=configured
    os.environ['DORMPRINT_ENVIRONMENT']='test'
    os.environ['DORMPRINT_SECRET_KEY']=secrets.token_urlsafe(48)
    storage=Path('/tmp')/('dormprint_'+schema)
    storage.mkdir(mode=0o700)
    os.environ['DORMPRINT_STORAGE_ROOT']=str(storage)
    from app.settings import get_settings
    get_settings.cache_clear()
    from app import db
    from app.models import Base
    db.configure(configured)
    Base.metadata.create_all(db.engine)
    yield db.engine
    db.engine.dispose()
    with root.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    root.dispose()
    import shutil
    shutil.rmtree(storage,ignore_errors=True)
    for key,value in old.items():
        if value is None:
            os.environ.pop(key,None)
        else:
            os.environ[key]=value
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def reset_database(database_schema):
    from app.models import Base
    tables=','.join('"'+table.name+'"' for table in Base.metadata.sorted_tables)
    with database_schema.begin() as connection:
        connection.execute(text('TRUNCATE TABLE '+tables+' RESTART IDENTITY CASCADE'))


@pytest.fixture
def db(reset_database):
    from app.db import SessionLocal
    session=SessionLocal()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def db_session(db):
    return db


@pytest.fixture
def app(request,reset_database):
    from app.main import create_app
    module_name=request.module.__name__.split('.')[-1]
    mapping={'test_auth':['auth'],'test_growth':['growth','mail','auth'],'test_mail':['mail','auth'],
        'test_media':['media','growth','mail','auth'],'test_pdf_engine':[],
        'test_production':['production','auth'],'test_delivery':['delivery','production','auth','growth','mail']}
    modules=getattr(request.module,'APP_MODULES',mapping.get(module_name,[]))
    return create_app(modules=['config',*modules])


@pytest.fixture
def client(app):
    with TestClient(app) as value:
        value.get('/api/v1/health/live')
        yield value


@pytest.fixture
def client_factory(app):
    clients=[]
    def factory():
        value=TestClient(app)
        value.get('/api/v1/health/live')
        clients.append(value)
        return value
    yield factory
    for value in clients:
        value.close()


def api_request(client,path,body=None,method='POST',key=None,**kwargs):
    from urllib.parse import unquote
    headers={'Origin':'http://testserver','Idempotency-Key':key or secrets.token_urlsafe(24),
             'X-CSRF-Token':unquote(client.cookies.get('csrfToken',''))}
    headers.update(kwargs.pop('headers',{}))
    return client.request(method,'/api/v1'+path if not path.startswith('/api/v1') else path,
                          json=body if body is not None else {},headers=headers,**kwargs)


@pytest.fixture
def api():
    return api_request


@pytest.fixture
def staff_factory(db,account_factory):
    def factory(role='ADMIN',username=None,capabilities=None,**kwargs):
        from argon2 import PasswordHasher
        from app.models import Staff
        if 'account_id' not in kwargs:
            kwargs['account_id']=account_factory().id
        row=Staff(username=username or 'staff_'+secrets.token_hex(4),name='测试员工',role=role,
            password_hash=PasswordHasher().hash('Testing-only-Password-42!'),enabled=True,status='ACTIVE',
            must_change_password=False,capabilities=capabilities if capabilities is not None else (['*'] if role=='ADMIN' else []),**kwargs)
        db.add(row)
        db.commit()
        return row
    return factory


@pytest.fixture
def account_factory(db):
    def factory(email=None,verified=True,**kwargs):
        from argon2 import PasswordHasher
        from app.models import Account,Principal,utcnow
        principal=Principal()
        db.add(principal)
        db.flush()
        account=Account(principal_id=principal.id,email=email or secrets.token_hex(6)+'@example.test',
            password_hash=PasswordHasher().hash('Testing-only-Password-42!'),status='ACTIVE' if verified else 'PENDING_EMAIL',
            verified_at=utcnow() if verified else None,**kwargs)
        db.add(account)
        db.flush()
        principal.account_id=account.id
        db.commit()
        return account
    return factory


@pytest.fixture
def admin_client(client_factory,staff_factory,db):
    from datetime import timedelta
    from app.models import AuthSession,utcnow
    from app.core import token_hash
    staff=staff_factory(is_super_admin=True)
    from app.models import Account
    account=db.get(Account,staff.account_id)
    token=secrets.token_urlsafe(32)
    db.add(AuthSession(kind='CUSTOMER',account_id=account.id,principal_id=account.principal_id,session_version=account.session_version,token_hash=token_hash(token),expires_at=utcnow()+timedelta(days=7)))
    db.commit()
    client=client_factory()
    client.cookies.set('dormprint_account_session',token)
    client.staff=staff
    client.account=account
    return client


@pytest.fixture
def customer_client(client_factory,account_factory,db):
    from datetime import timedelta
    from app.models import AuthSession,utcnow
    from app.core import token_hash
    account=account_factory()
    token=secrets.token_urlsafe(32)
    db.add(AuthSession(kind='CUSTOMER',account_id=account.id,principal_id=account.principal_id,
        token_hash=token_hash(token),expires_at=utcnow()+timedelta(days=30)))
    db.commit()
    client=client_factory()
    client.cookies.set('dormprint_account_session',token)
    client.account=account
    return client
