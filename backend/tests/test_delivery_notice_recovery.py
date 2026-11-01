"""Durable delivery notices recover against the isolated PostgreSQL schema."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.models import Account, DeliveryNotice, EmailJob, Job, utcnow
from test_delivery_closed_loop import make_world, submit
from test_mail import settings_allow_local, seed_mail_config, smtp_server

APP_MODULES = ['auth', 'commerce', 'delivery', 'growth', 'mail', 'media']


def delivery_event(db):
    from app.delivery_plan import record_change
    world = make_world(db)
    order = submit(db, world)
    account = Account(principal_id=world.principal.id, email='student@example.invalid',
                      password_hash='unused', status='ACTIVE', verified_at=utcnow())
    db.add(account)
    db.flush()
    order.checkout_account_id = account.id
    change = record_change(db, order, kind='ETA_UPDATED',
                           original={'startsAt': world.slot.starts_at.isoformat(),
                                     'endsAt': world.slot.ends_at.isoformat()},
                           proposed={'startsAt': (world.slot.starts_at + timedelta(hours=1)).isoformat(),
                                     'endsAt': (world.slot.ends_at + timedelta(hours=1)).isoformat()},
                           reason='道路施工', actor_id=None)
    return order, change


@pytest.mark.parametrize('failure', ['smtp', 'template', 'queue'])
def test_unqueued_delivery_notice_recovers_after_configuration_or_queue_fix(
        db, smtp_server, settings_allow_local, monkeypatch, failure):
    from app import mail
    from app.growth import notify_delivery_change
    from app.maintenance import maintenance_sweep

    order, change = delivery_event(db)
    _, port = smtp_server
    if failure != 'smtp':
        seed_mail_config(db, port)
    template = mail.ensure_template(db, 'DELIVERY_UPDATED')
    if failure == 'template':
        template.enabled = False
    original_enqueue = mail.enqueue_job
    if failure == 'queue':
        def fail_queue(*args, **kwargs):
            raise RuntimeError('simulated queue write failure')
        monkeypatch.setattr(mail, 'enqueue_job', fail_queue)
    notice = notify_delivery_change(db, order, change)
    db.commit()
    assert notice.email_job_id is None
    assert notice.last_error == {
        'smtp': 'EMAIL_SERVICE_UNAVAILABLE',
        'template': 'MAIL_TEMPLATE_INVALID',
        'queue': 'MAIL_QUEUE_FAILED',
    }[failure]

    seed_mail_config(db, port)
    template.enabled = True
    monkeypatch.setattr(mail, 'enqueue_job', original_enqueue)
    db.commit()
    maintenance_sweep(db)
    db.commit()
    db.refresh(notice)
    assert notice.email_job_id is not None
    maintenance_sweep(db)
    db.commit()
    assert db.scalar(select(func.count(EmailJob.id)).where(
        EmailJob.id == notice.email_job_id)) == 1
    assert db.scalar(select(func.count(Job.id)).where(
        Job.kind == 'EMAIL', Job.target_id == notice.email_job_id)) == 1


def test_expired_delivery_mail_updates_notice(
        db, smtp_server, settings_allow_local):
    from app.growth import notify_delivery_change
    from app.mail import purge_expired_payloads

    order, change = delivery_event(db)
    seed_mail_config(db, smtp_server[1])
    notice = notify_delivery_change(db, order, change)
    job = db.get(EmailJob, notice.email_job_id)
    job.expires_at = utcnow() - timedelta(seconds=1)
    db.flush()
    purge_expired_payloads(db)
    assert notice.status == 'FAILED'
    assert notice.last_error == 'MAIL_PAYLOAD_EXPIRED'


def test_accepted_delivery_event_replay_does_not_reset_notice(db, smtp_server, settings_allow_local):
    from app.growth import notify_delivery_change
    from app.mail import process_email_job

    order, change = delivery_event(db)
    seed_mail_config(db, smtp_server[1])
    notice = notify_delivery_change(db, order, change)
    process_email_job(db, notice.email_job_id)
    assert notice.status == 'ACCEPTED'
    notify_delivery_change(db, order, change)
    assert notice.status == 'ACCEPTED'
    assert len(smtp_server[0].messages) == 1


def test_address_update_mail_uses_current_eta_instead_of_original_slot(db):
    from app.growth import _order_slot_label, _window_label
    order, change = delivery_event(db)
    expected = _window_label(change.proposed['startsAt'], change.proposed['endsAt'])
    assert _order_slot_label(db, order) == expected


def test_delivery_notice_retry_requires_capability_and_blocks_unknown_result(
        db, admin_client, smtp_server, settings_allow_local, api):
    from app.core import Actor, actor_dep
    from app.growth import notify_delivery_change

    order, change = delivery_event(db)
    notice = notify_delivery_change(db, order, change)
    db.commit()
    app = admin_client._transport.app
    path = f'/admin/delivery-notices/{notice.id}/retry'
    app.dependency_overrides[actor_dep] = lambda: Actor(
        staff_id=admin_client.staff.id, staff_role='ADMIN', capabilities={'payment.manage'})
    response = api(admin_client, path, {'expectedVersion': notice.version, 'reason': '恢复通知'})
    assert response.status_code == 403, response.text
    seed_mail_config(db, smtp_server[1])
    db.commit()
    app.dependency_overrides[actor_dep] = lambda: Actor(
        staff_id=admin_client.staff.id, staff_role='ADMIN', capabilities={'delivery.manage'})
    response = api(admin_client, path, {'expectedVersion': notice.version, 'reason': '恢复通知'})
    assert response.status_code == 200, response.text
    db.refresh(notice)
    assert notice.email_job_id
    notice.status = 'UNKNOWN'
    db.commit()
    response = api(admin_client, path, {'expectedVersion': notice.version, 'reason': '恢复通知'})
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'MAIL_RESULT_UNKNOWN'
    app.dependency_overrides.pop(actor_dep, None)


def test_expired_notice_explicit_retry_reuses_same_jobs(db, smtp_server, settings_allow_local):
    from app.growth import notify_delivery_change, retry_delivery_notice
    from app.mail import purge_expired_payloads, process_email_job
    order, change = delivery_event(db)
    seed_mail_config(db, smtp_server[1])
    notice = notify_delivery_change(db, order, change)
    job_id = notice.email_job_id
    db.get(EmailJob, job_id).expires_at = utcnow() - timedelta(seconds=1)
    db.flush()
    purge_expired_payloads(db)
    retry_delivery_notice(db, notice)
    assert notice.email_job_id == job_id
    process_email_job(db, job_id)
    assert notice.status == 'ACCEPTED'
    assert db.scalar(select(func.count(EmailJob.id))) == 1
    assert db.scalar(select(func.count(Job.id)).where(Job.kind == 'EMAIL')) == 1


def test_smtp_disconnect_after_data_is_unknown_and_not_retried(db, smtp_server, settings_allow_local, monkeypatch):
    import smtplib
    from app.growth import notify_delivery_change, retry_delivery_notice
    from app import mail
    order, change = delivery_event(db)
    seed_mail_config(db, smtp_server[1])
    notice = notify_delivery_change(db, order, change)
    class DisconnectedClient:
        def send_message(self, message):
            raise smtplib.SMTPServerDisconnected('lost final DATA response')
        def quit(self):
            pass
    monkeypatch.setattr(mail, '_smtp_session', lambda values: DisconnectedClient())
    mail.process_email_job(db, notice.email_job_id)
    assert notice.status == 'UNKNOWN'
    assert db.get(EmailJob, notice.email_job_id).status == 'UNKNOWN'
    with pytest.raises(Exception) as result:
        retry_delivery_notice(db, notice)
    assert getattr(result.value, 'code', None) == 'MAIL_RESULT_UNKNOWN'


@pytest.mark.parametrize('expired', [False, True])
def test_legacy_ambiguous_send_failure_cannot_be_retried_even_after_payload_expiry(
        db, smtp_server, settings_allow_local, expired):
    from app.growth import notify_delivery_change, retry_delivery_notice
    from app.mail import purge_expired_payloads
    order, change = delivery_event(db)
    seed_mail_config(db, smtp_server[1])
    notice = notify_delivery_change(db, order, change)
    job = db.get(EmailJob, notice.email_job_id)
    job.status = 'FAILED'
    job.data = {**job.data, 'lastErrorClass': 'MAIL_SEND_FAILED'}
    notice.status = 'FAILED'
    if expired:
        job.expires_at = utcnow() - timedelta(seconds=1)
        db.flush()
        purge_expired_payloads(db)
    with pytest.raises(Exception) as result:
        retry_delivery_notice(db, notice)
    assert getattr(result.value, 'code', None) == 'MAIL_RESULT_UNKNOWN'


def test_explicit_smtp_rejection_remains_a_known_retryable_failure(
        db, smtp_server, settings_allow_local, monkeypatch):
    import smtplib
    from app.growth import notify_delivery_change
    from app import mail
    order, change = delivery_event(db)
    seed_mail_config(db, smtp_server[1])
    notice = notify_delivery_change(db, order, change)
    class RejectedClient:
        def send_message(self, message):
            raise smtplib.SMTPDataError(550, b'not accepted')
        def quit(self):
            pass
    monkeypatch.setattr(mail, '_smtp_session', lambda values: RejectedClient())
    mail.process_email_job(db, notice.email_job_id)
    assert notice.status == 'PENDING'
    assert notice.last_error == 'MAIL_SEND_REJECTED'
