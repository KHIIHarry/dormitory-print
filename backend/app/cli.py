import argparse
import getpass
from pathlib import Path
import shutil
import subprocess
import sys

from sqlalchemy import text
from . import db
from .db import SessionLocal
from .settings import get_settings


def migrate():
    from alembic import command
    from alembic.config import Config
    cfg=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    command.upgrade(cfg,'head')


def initialize_admin(username,password,email):
    from .auth import create_initial_admin
    with SessionLocal.begin() as session:
        staff=create_initial_admin(session,username,password,email=email)
        from .config import DEFAULTS,get_config
        for section in DEFAULTS:get_config(session,section)
        from .mail import seed_templates
        seed_templates(session)
        return staff.id


def doctor():
    settings=get_settings()
    result={}
    try:
        with SessionLocal() as session:
            session.execute(text('SELECT 1'))
            result['database']='READY'
            result['migration']=session.scalar(text('SELECT version_num FROM alembic_version'))
    except Exception as error:result['database']=type(error).__name__
    settings.storage_root.mkdir(parents=True,exist_ok=True)
    result['storage']='READY' if settings.storage_root.is_dir() else 'UNAVAILABLE'
    for label,program in [('office',settings.soffice_path),('gray',settings.ghostscript_path)]:
        result[label]='AVAILABLE' if shutil.which(program) else 'UNAVAILABLE'
    try:
        from redis import Redis
        result['redis']='READY' if Redis.from_url(settings.redis_url,socket_connect_timeout=2).ping() else 'UNAVAILABLE'
    except Exception as error:result['redis']=type(error).__name__
    import json
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if all(result.get(key)=='READY' for key in ('database','storage','redis')) and result.get('office')=='AVAILABLE' else 1


def main():
    parser=argparse.ArgumentParser(description='DormPrint deployment and operations')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('migrate')
    init=sub.add_parser('init-admin')
    init.add_argument('--username',required=True)
    init.add_argument('--email',required=True,help='超级管理员基础客户账号邮箱，须由部署负责人核验')
    init.add_argument('--password-stdin',action='store_true')
    sub.add_parser('doctor')
    sub.add_parser('maintenance')
    sub.add_parser('work-once')
    args=parser.parse_args()
    db.configure()
    if args.command=='migrate':migrate()
    elif args.command=='init-admin':
        password=sys.stdin.readline().rstrip('\r\n') if args.password_stdin else getpass.getpass('管理员初始密码: ')
        if not args.password_stdin and password!=getpass.getpass('再次输入密码: '):
            parser.error('密码不一致')
        print('Created administrator:',initialize_admin(args.username,password,args.email))
    elif args.command=='doctor':raise SystemExit(doctor())
    elif args.command=='maintenance':
        from .jobs import maintenance
        print(maintenance())
    elif args.command=='work-once':
        from .jobs import run_one
        print(run_one())


if __name__=='__main__':main()
