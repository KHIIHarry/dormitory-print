"""Delivery facts feed the shared timeline and a recoverable mail outbox."""
from datetime import datetime, timedelta
from email import message_from_string
from email.policy import default
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import DeliveryNotice, DeliveryProof, EmailJob, utcnow
from test_batch_delivery import (
    _batch, _body, _customer_client, _proof_asset, _staff_client,
)
from test_delivery import make_courier, make_ready_order
from test_mail import seed_mail_config, settings_allow_local, smtp_server
from test_order_void import seed_quote

APP_MODULES = ['auth', 'commerce', 'delivery', 'production', 'growth', 'mail', 'media']


def test_delivered_status_alone_does_not_fabricate_timeline_events(db):
    from app.commerce import history
    from app.core import Actor

    order = make_ready_order(db, status='DELIVERED')
    result = history(order.id, db=db, actor=Actor(principal_id=order.principal_id))
    assert result['data'] == []


def complete_delivery(db, api, admin_client, client_factory, staff_factory,
                      account_factory, *, mode='batch'):
    account = account_factory(preferences={})
    courier = make_courier(staff_factory)
    order = seed_quote(db, make_ready_order(db))
    order.principal_id = account.principal_id
    order.checkout_account_id = account.id
    order.data = {**order.data, 'history': [{
        'title': '线下收款已人工确认', 'time': order.created_at.isoformat(),
        'description': '原收款记录',
    }]}
    db.commit()
    batch, assignments = _batch(db, api, admin_client, courier, [order])
    photo = _proof_asset(db, courier, assignments[0])
    client = _staff_client(client_factory, db, courier)
    body = _body([order], assignments, photo, placement_note='一楼收发台')
    if mode == 'single':
        item = body['items'][0]
        path = f'/courier/orders/{order.id}/proof'
        payload = {key: value for key, value in item.items()
                   if key not in ('orderId', 'expectedAssignmentVersion')}
        payload.update({key: value for key, value in body.items() if key != 'items'})
    else:
        path = f'/courier/batches/{batch["id"]}/delivery-proofs/batch'
        payload = body
    key = 'completion-' + uuid4().hex
    response = api(client, path, payload, key=key)
    assert response.status_code == 200, response.text
    replay = api(client, path, payload, key=key)
    assert replay.status_code == 200, replay.text
    db.expire_all()
    db.refresh(order)
    return order, account


@pytest.mark.parametrize('mode', ['single', 'batch'])
def test_delivery_timeline_is_shared_and_recovers_legacy_missing_history(
        db, api, admin_client, client_factory, staff_factory, account_factory, mode):
    order, account = complete_delivery(
        db, api, admin_client, client_factory, staff_factory, account_factory, mode=mode)
    # Existing deliveries have no delivery entries in JSON history. Read actual
    # assignment/proof timestamps; never fabricate events from status alone.
    order.data = {**order.data, 'history': [row for row in order.data['history']
                                          if row['title'] == '线下收款已人工确认']}
    db.commit()
    customer = _customer_client(client_factory, db, account)
    customer_response = api(customer, f'/orders/{order.id}', method='GET')
    admin_response = api(admin_client, f'/admin/orders/{order.id}', method='GET')
    history_response = api(customer, f'/orders/{order.id}/history', method='GET')
    for response in (customer_response, admin_response, history_response):
        assert response.status_code == 200, response.text
    history = customer_response.json()['data']['history']
    assert history == admin_response.json()['data']['history']
    assert history == history_response.json()['data']
    assert [row['title'] for row in history] == [
        '资料已送达', '配送已开始', '线下收款已人工确认']
    proof = db.scalar(select(DeliveryProof).where(DeliveryProof.order_id == order.id))
    assert datetime.fromisoformat(history[0]['time']) == datetime.fromisoformat(
        proof.data['serverDeliveredAt'])
    # ORDER_VIEW history does not bypass the separate proof-view scope.
    assert '一楼收发台' not in str(history)
    # Reading history must not rewrite business data or add duplicates.
    again = api(customer, f'/orders/{order.id}/history', method='GET')
    assert again.json()['data'] == history
    # A legacy row already describing the same fact must not appear twice.
    order.data = {**order.data, 'history': [*history, {
        'title': '内部之外的既有动态', 'time': order.created_at.isoformat(),
        'description': '保留既有记录',
    }]}
    db.commit()
    deduped = api(customer, f'/orders/{order.id}/history', method='GET').json()['data']
    assert [row['title'] for row in deduped].count('资料已送达') == 1
    assert [row['title'] for row in deduped].count('配送已开始') == 1


@pytest.mark.parametrize('failure', ['smtp', 'template', 'queue'])
@pytest.mark.parametrize('mode', ['single', 'batch'])
def test_delivery_mail_survives_enqueue_failure_and_sends_once_after_recovery(
        db, api, admin_client, client_factory, staff_factory, account_factory,
        smtp_server, settings_allow_local, monkeypatch, failure, mode):
    from app import mail
    from app.growth import recover_delivery_notices
    from app.jobs import claim_next_job, process_claim

    state, port = smtp_server
    if failure != 'smtp':
        seed_mail_config(db, port)
    template = mail.ensure_template(db, 'ORDER_DELIVERED')
    if failure == 'template':
        template.enabled = False
    original_enqueue = mail.enqueue_job
    if failure == 'queue':
        def fail_queue(*args, **kwargs):
            raise RuntimeError('simulated queue write failure')
        monkeypatch.setattr(mail, 'enqueue_job', fail_queue)
    db.commit()

    order, account = complete_delivery(
        db, api, admin_client, client_factory, staff_factory, account_factory, mode=mode)
    assert order.status == 'DELIVERED'
    notice = db.scalar(select(DeliveryNotice).where(
        DeliveryNotice.order_id == order.id, DeliveryNotice.kind == 'ORDER_DELIVERED'))
    assert notice is not None, 'A failed enqueue must retain a delivery notification intent'
    assert notice.email_job_id is None
    assert notice.last_error == {
        'smtp': 'EMAIL_SERVICE_UNAVAILABLE', 'template': 'MAIL_TEMPLATE_INVALID',
        'queue': 'MAIL_QUEUE_FAILED',
    }[failure]
    assert list(db.scalars(select(EmailJob))) == []
    # The retry must use the frozen event time even if unrelated order fields change.
    delivered_at = notice.data['variables']['deliveredAt']
    order.version += 1
    seed_mail_config(db, port)
    template = mail.ensure_template(db, 'ORDER_DELIVERED')
    template.enabled = True
    monkeypatch.setattr(mail, 'enqueue_job', original_enqueue)
    db.commit()
    assert recover_delivery_notices(db) == 1
    db.commit()
    assert recover_delivery_notices(db) == 0
    db.commit()

    claim = claim_next_job(['EMAIL'])
    assert claim is not None
    assert process_claim(*claim)
    db.expire_all()
    assert db.get(DeliveryNotice, notice.id).status == 'ACCEPTED'
    assert len(state.messages) == 1
    assert account.email in state.messages[0]['to']
    message = message_from_string(state.messages[0]['data'], policy=default)
    plain = message.get_body(preferencelist=('plain',)).get_content()
    assert order.pickup_code in plain
    assert delivered_at in plain
    assert '已签收' in message['Subject']
    jobs = list(db.scalars(select(EmailJob)))
    assert len(jobs) == 1 and jobs[0].status == 'SMTP_ACCEPTED'
    from app.growth import notify_order_delivered
    assert notify_order_delivered(db, order).id == jobs[0].id
    assert db.get(DeliveryNotice, notice.id).status == 'ACCEPTED'
    assert recover_delivery_notices(db) == 0


def test_unknown_delivery_mail_cannot_be_replayed_or_retried(
        db, api, admin_client, client_factory, staff_factory, account_factory,
        smtp_server, settings_allow_local):
    from app.core import DomainError
    from app.growth import notify_order_delivered, recover_delivery_notices, retry_delivery_notice

    seed_mail_config(db, smtp_server[1])
    db.commit()
    order, _ = complete_delivery(
        db, api, admin_client, client_factory, staff_factory, account_factory)
    notice = db.scalar(select(DeliveryNotice).where(
        DeliveryNotice.order_id == order.id, DeliveryNotice.kind == 'ORDER_DELIVERED'))
    job = db.get(EmailJob, notice.email_job_id)
    notice.status = job.status = 'UNKNOWN'
    db.commit()
    assert notify_order_delivered(db, order).id == job.id
    assert notice.status == 'UNKNOWN'
    assert recover_delivery_notices(db) == 0
    with pytest.raises(DomainError) as error:
        retry_delivery_notice(db, notice)
    assert error.value.code == 'MAIL_RESULT_UNKNOWN'
    assert len(list(db.scalars(select(EmailJob)))) == 1
    assert smtp_server[0].messages == []


def test_expired_unsent_delivery_mail_can_be_explicitly_retried(
        db, api, admin_client, client_factory, staff_factory, account_factory,
        smtp_server, settings_allow_local):
    from app.growth import retry_delivery_notice
    from app.mail import process_email_job, purge_expired_payloads

    seed_mail_config(db, smtp_server[1])
    db.commit()
    order, _ = complete_delivery(
        db, api, admin_client, client_factory, staff_factory, account_factory)
    notice = db.scalar(select(DeliveryNotice).where(
        DeliveryNotice.order_id == order.id, DeliveryNotice.kind == 'ORDER_DELIVERED'))
    job = db.get(EmailJob, notice.email_job_id)
    job.expires_at = utcnow() - timedelta(seconds=1)
    db.flush()
    purge_expired_payloads(db)
    assert job.status == 'EXPIRED' and notice.status == 'FAILED'
    assert job.protected_payload is None
    retry_delivery_notice(db, notice)
    assert job.status == 'QUEUED' and notice.status == 'PENDING'
    process_email_job(db, job.id)
    assert notice.status == 'ACCEPTED'
    assert len(smtp_server[0].messages) == 1
    assert len(list(db.scalars(select(EmailJob)))) == 1
