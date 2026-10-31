"""Database-authoritative at-least-once workers. Redis is transport, never truth."""
import argparse
from datetime import timedelta
import logging
import secrets

from celery import Celery
from sqlalchemy import or_, select

from .db import SessionLocal
from .models import Asset, EmailJob, ExportJob, Job, Preparation, TrashEntry, utcnow
from .settings import get_settings

settings=get_settings()
celery_app=Celery('dormprint',broker=settings.redis_url)
celery_app.conf.update(task_default_queue='dormprint',task_acks_late=True,worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,task_serializer='json',accept_content=['json'],result_serializer='json',
    timezone='UTC',beat_schedule={'file-outbox':{'task':'dormprint.files','schedule':2.0,'options':{'queue':'files'}},
        'business-outbox':{'task':'dormprint.business','schedule':2.0,'options':{'queue':'business'}},
        'maintenance':{'task':'dormprint.maintenance','schedule':30.0,'options':{'queue':'business'}}})
logger=logging.getLogger('dormprint.jobs')


def _database():
    from . import db
    if db.engine is None:db.configure()


def claim_next_job(kinds=None):
    _database()
    with SessionLocal.begin() as session:
        now=utcnow()
        query=select(Job).where(or_(Job.status=='QUEUED',(Job.status=='PROCESSING') & (Job.lease_until<=now)))
        if kinds:query=query.where(Job.kind.in_(kinds))
        candidates=session.scalars(query.order_by(Job.created_at,Job.id).with_for_update(skip_locked=True).limit(20))
        for row in candidates:
            available=(row.data or {}).get('availableAt')
            if available:
                from datetime import datetime, timezone
                try:
                    value=datetime.fromisoformat(str(available).replace('Z','+00:00'))
                except (TypeError,ValueError):
                    logger.error('job_available_at_invalid job_id=%s value=%r',row.id,available)
                    continue
                if value.tzinfo is None:value=value.replace(tzinfo=timezone.utc)
                if value>now:continue
            token=secrets.token_urlsafe(24)
            row.status='PROCESSING';row.attempts+=1;row.lease_token=token
            row.lease_until=now+timedelta(seconds=settings.worker_lease_seconds)
            return row.id,token
    return None


def process_claim(job_id,lease_token):
    _database()
    try:
        with SessionLocal() as session:
            job=session.scalar(select(Job).where(Job.id==job_id).with_for_update())
            if not job or job.lease_token!=lease_token or job.status!='PROCESSING':return False
            kind,target_id=job.kind,job.target_id
            lifecycle_status=None
            # File processors publish PROCESSING and validated immutable outputs in
            # separate short transactions, permitting cancellation during conversion.
            # A begin() context would reject their explicit transaction boundaries.
            session.commit()
            if job.kind=='ASSET':
                from .media import process_asset
                process_asset(session,job.target_id)
                target=session.get(Asset,job.target_id)
            elif job.kind=='PREPARATION':
                from .media import process_preparation
                process_preparation(session,job.target_id)
                target=session.get(Preparation,job.target_id)
            elif job.kind=='EMAIL':
                from .mail import process_email_job
                process_email_job(session,job.target_id)
                target=None
            elif job.kind=='EXPORT':
                from .exports import process_export_job
                process_export_job(session,job.target_id)
                target=None
            elif job.kind=='ACCOUNT_DELETION_FINALIZE':
                from .account_lifecycle import finalize_deletion_request
                lifecycle_status=finalize_deletion_request(session,job.target_id)
                target=None
            elif job.kind=='PURGE':
                from .maintenance import process_purge_job
                try:
                    process_purge_job(session,job.target_id)
                except Exception as error:
                    # A pending-storage error may carry durable recovery facts.
                    # Respect the same explicit commit contract as HTTP mutations.
                    if getattr(error,'commit',False):session.commit()
                    raise
                # Physical cleanup is finalized after the graph transaction
                # commits. Only its persisted result can make the job READY.
                session.commit()
                target=None
            else:
                raise RuntimeError('UNKNOWN_JOB_KIND')
            job=session.scalar(select(Job).where(Job.id==job_id).with_for_update().execution_options(populate_existing=True))
            if not job or job.lease_token!=lease_token or job.status!='PROCESSING':
                session.rollback()
                return False
            if lifecycle_status is not None:
                if lifecycle_status=='NOT_DUE':
                    job.status='QUEUED'
                    job.data={**job.data,'availableAt':(utcnow()+timedelta(minutes=5)).isoformat()}
                elif lifecycle_status=='BLOCKED':
                    job.status='QUEUED'
                    job.data={**job.data,'availableAt':(utcnow()+timedelta(hours=1)).isoformat(),
                              'failureCode':'ACCOUNT_DELETION_BLOCKED'}
                else:
                    job.status='READY'
            elif kind=='EMAIL':
                email=session.get(EmailJob,target_id)
                if email and email.status in ('QUEUED','RETRY','RETRY_PENDING'):
                    job.status='QUEUED'
                    job.data={**job.data,'availableAt':email.available_at.isoformat()}
                elif email and email.status in ('FAILED','EXPIRED','CANCELLED'):job.status='FAILED'
                else:job.status='READY'
            elif kind=='EXPORT':
                export=session.get(ExportJob,target_id)
                job.status='READY' if export and export.status=='READY' else 'FAILED'
            elif kind=='PURGE':
                trash=session.get(TrashEntry,target_id,populate_existing=True)
                if trash and (trash.status=='PURGE_PENDING' or (trash.data or {}).get('cleanupPending')):
                    job.status='QUEUED'
                    job.data={**job.data,'availableAt':(utcnow()+timedelta(seconds=2)).isoformat(),'failureCode':'PURGE_STORAGE_PENDING'}
                else:job.status='READY' if trash and trash.status=='PURGED' else 'FAILED'
            elif target and target.status=='FAILED':
                job.status='FAILED'
            elif target and target.status=='CANCELLED':
                job.status='CANCELLED'
            elif kind in ('ASSET','PREPARATION') and (not target or getattr(target,'deleted_at',None) is not None):
                job.status='CANCELLED'
            elif kind in ('ASSET','PREPARATION') and target.status!='READY':
                # A superseded converter may return while the winning resource
                # attempt is still running. The durable job cannot claim READY.
                job.status='QUEUED'
                job.data={**job.data,'availableAt':(utcnow()+timedelta(seconds=2)).isoformat(),'waitingForTarget':target.status}
            else:job.status='READY'
            job.lease_until=None;job.lease_token=None
            session.commit()
            return True
    except Exception as error:
        code=getattr(error,'code','WORKER_PROCESSING_FAILED')
        if not isinstance(code,str) or len(code)>80:code='WORKER_PROCESSING_FAILED'
        with SessionLocal.begin() as session:
            job=session.scalar(select(Job).where(Job.id==job_id).with_for_update())
            if not job or job.lease_token!=lease_token:return False
            retryable=(code in ('CONVERSION_TIMEOUT','TRANSIENT_IO','WORKER_LOST') and job.attempts<3) or (code=='PURGE_STORAGE_PENDING' and job.attempts<10)
            job.status='QUEUED' if retryable else 'FAILED'
            job.data={**job.data,'failureCode':code,'availableAt':(utcnow()+timedelta(seconds=min(300,10**job.attempts))).isoformat()}
            job.lease_until=None;job.lease_token=None
            target=session.get(Asset,job.target_id) if job.kind=='ASSET' else session.get(Preparation,job.target_id) if job.kind=='PREPARATION' else None
            if target:
                target.status='QUEUED' if retryable else 'FAILED'
                target.data={**target.data,'failureCode':code,'retryable':retryable}
        logger.error('job_failed job_id=%s class=%s code=%s',job_id,type(error).__name__,code)
        return False


def run_one(kinds=None):
    claim=claim_next_job(kinds)
    return process_claim(*claim) if claim else None


@celery_app.task(name='dormprint.work')
def work():
    return run_one()


@celery_app.task(name='dormprint.files')
def file_work():
    return run_one(('ASSET','PREPARATION'))


@celery_app.task(name='dormprint.business')
def business_work():
    return run_one(('EMAIL','EXPORT','PURGE','ACCOUNT_DELETION_FINALIZE'))


@celery_app.task(name='dormprint.maintenance')
def maintenance():
    _database()
    from .maintenance import maintenance_sweep
    from .account_lifecycle import run_due_deletions
    with SessionLocal.begin() as session:
        result=maintenance_sweep(session)
        result['accountDeletions']=run_due_deletions(session)
        return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--maintenance',action='store_true')
    options=parser.parse_args()
    if options.maintenance:print(maintenance())
    elif options.once:print(run_one())
    else:parser.error('Use --once or --maintenance; continuous production uses Celery worker and beat')
