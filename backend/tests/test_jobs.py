from datetime import timedelta

from app.models import Job,utcnow


def test_claim_uses_persistent_lease_and_is_not_reclaimed_early(db):
    from app.jobs import claim_next_job
    first=Job(kind='ASSET',business_key='job-test',target_id='one')
    db.add(first);db.commit()
    claimed=claim_next_job()
    assert claimed and claimed[0]==first.id
    assert claim_next_job() is None
    db.expire_all()
    row=db.get(Job,first.id)
    assert row.status=='PROCESSING'
    assert row.lease_until>utcnow()


def test_expired_lease_can_be_recovered_once(db):
    from app.jobs import claim_next_job
    row=Job(kind='ASSET',business_key='job-recover',target_id='two',status='PROCESSING',
        lease_until=utcnow()-timedelta(seconds=1),lease_token='old-lease')
    db.add(row);db.commit()
    claimed=claim_next_job()
    assert claimed[0]==row.id and claimed[1]!='old-lease'
    assert claim_next_job() is None


def test_stale_file_processor_does_not_mark_job_ready_while_target_is_processing(db,monkeypatch):
    from app.models import Asset
    from app.jobs import claim_next_job,process_claim
    import app.media
    asset=Asset(purpose='PRINT_SOURCE',name='still-processing.pdf',mime='application/pdf',size=10,sha256='f'*64,storage_key='unused/source.pdf',status='PROCESSING')
    db.add(asset);db.flush()
    work=Job(kind='ASSET',business_key='job-stale-result',target_id=asset.id)
    db.add(work);db.commit()
    # A converter whose resource attempt was superseded returns without publishing.
    monkeypatch.setattr(app.media,'process_asset',lambda session,target_id:None)
    claim=claim_next_job(['ASSET'])
    assert process_claim(*claim)
    db.expire_all()
    assert db.get(Asset,asset.id).status=='PROCESSING'
    assert db.get(Job,work.id).status=='QUEUED'


def test_purge_job_waits_for_storage_finalization_after_commit(db,monkeypatch):
    from sqlalchemy import event
    from app.db import SessionLocal
    from app.models import TrashEntry
    from app.jobs import claim_next_job,process_claim
    import app.maintenance
    entry=TrashEntry(target_type='ASSET',target_id='already-deleted',actor_id='test',reason='清理',purge_at=utcnow())
    db.add(entry);db.flush()
    work=Job(kind='PURGE',business_key='pending-purge',target_id=entry.id)
    db.add(work);db.commit()
    def purge(session,target_id):
        session.get(TrashEntry,target_id).status='PURGED'
        # The file finalizer runs after database commit. A failed unlink writes
        # a durable pending status using its own short recovery transaction.
        def fail_cleanup(_):
            with SessionLocal.begin() as recovery:
                row=recovery.get(TrashEntry,target_id)
                row.status='PURGE_PENDING';row.data={'cleanupPending':True}
        event.listen(session,'after_commit',fail_cleanup,once=True)
    monkeypatch.setattr(app.maintenance,'process_purge_job',purge)
    assert process_claim(*claim_next_job(['PURGE']))
    db.expire_all()
    assert db.get(TrashEntry,entry.id).status=='PURGE_PENDING'
    assert db.get(Job,work.id).status=='QUEUED'


def test_purge_storage_pending_is_retryable_and_preserves_recovery_metadata(db,monkeypatch):
    from app.core import DomainError
    from app.models import TrashEntry
    from app.jobs import claim_next_job,process_claim
    import app.maintenance
    entry=TrashEntry(target_type='ASSET',target_id='pending-file',actor_id='test',reason='清理',purge_at=utcnow())
    db.add(entry);db.flush()
    work=Job(kind='PURGE',business_key='retry-purge',target_id=entry.id)
    db.add(work);db.commit()
    def purge(session,target_id):
        row=session.get(TrashEntry,target_id)
        row.status='PURGE_PENDING';row.data={'cleanupPending':True,'purgedFiles':['private/pending.pdf']}
        raise DomainError('PURGE_STORAGE_PENDING','等待存储重试',503,commit=True)
    monkeypatch.setattr(app.maintenance,'process_purge_job',purge)
    assert not process_claim(*claim_next_job(['PURGE']))
    db.expire_all()
    assert db.get(TrashEntry,entry.id).data['cleanupPending'] is True
    assert db.get(Job,work.id).status=='QUEUED'
