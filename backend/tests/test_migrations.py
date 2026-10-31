"""Actual PostgreSQL upgrades preserve existing employees and can be reversed."""
import os
from pathlib import Path
import secrets
import subprocess
import sys
from urllib.parse import quote

from sqlalchemy import create_engine, inspect, text


def test_upgrade_existing_staff_and_reverse_new_feature_schema():
    root_url=os.environ.get('DORMPRINT_TEST_DATABASE_URL','postgresql+psycopg://dormprint:local-test-only@127.0.0.1:55432/dormprint')
    engine=create_engine(root_url)
    schema='migration_'+secrets.token_hex(8)
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    url=root_url+('&' if '?' in root_url else '?')+'options='+quote('-csearch_path='+schema,safe='')
    env={**os.environ,'DORMPRINT_DATABASE_URL':url}
    directory=Path(__file__).resolve().parents[1]
    def migrate(target):
        result=subprocess.run([sys.executable,'-m','alembic',*target],cwd=directory,env=env,capture_output=True,text=True,timeout=60)
        assert result.returncode==0, result.stdout+'\n'+result.stderr
    db=create_engine(url)
    try:
        migrate(['upgrade','43091261e090'])
        with db.begin() as conn:
            conn.execute(text("INSERT INTO staff (id,version,created_at,updated_at,username,password_hash,name,phone,email_verified,role,enabled,status,capabilities,must_change_password,session_version) VALUES ('existing',1,NOW(),NOW(),'existing','','existing','',false,'ADMIN',true,'ACTIVE','[\"*\"]'::jsonb,false,1)"))
        migrate(['upgrade','head'])
        with db.begin() as conn:
            row=conn.execute(text("SELECT account_id,is_super_admin,discount_level FROM staff WHERE id='existing'")).one()
            assert tuple(row)==(None,False,None)  # No guessed account binding or automatic superadmin promotion.
        assert 'wage_entries' in inspect(db).get_table_names()
        assert 'supplementary_charges' in inspect(db).get_table_names()
        migrate(['check'])
        migrate(['downgrade','43091261e090'])
        assert 'wage_entries' not in inspect(db).get_table_names()
        with db.begin() as conn:
            assert conn.scalar(text("SELECT COUNT(*) FROM staff WHERE id='existing'"))==1
    finally:
        db.dispose()
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()
