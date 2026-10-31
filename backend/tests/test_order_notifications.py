"""Subscribed order notifications traverse the actual outbox and local SMTP."""
from sqlalchemy import select
from app.core import Actor
from app.models import EmailJob,Principal,Quote
from test_commerce import ready_preparation
from test_mail import smtp_server,settings_allow_local,seed_mail_config


def test_subscribed_order_creation_queues_and_sends_real_local_smtp(db,ready_preparation,account_factory,smtp_server,settings_allow_local):
    from app.commerce import submit_order,OrderSubmit
    from app.jobs import claim_next_job,process_claim
    account=account_factory(preferences={'orderCreated':True})
    actor,prep,artifact,slot,body=ready_preparation
    prep.checkout_account_id=account.id
    db.get(Principal,prep.principal_id).account_id=account.id
    quote=db.get(Quote,prep.quote_id)
    quote.checkout_account_id=account.id
    quote.data={**quote.data,'checkoutAccountId':account.id}
    state,port=smtp_server
    seed_mail_config(db,port)
    order=submit_order(db,Actor(principal_id=prep.principal_id,account_id=account.id),OrderSubmit.model_validate(body))
    db.commit()
    job=db.scalar(select(EmailJob).where(EmailJob.template_key=='ORDER_CREATED'))
    assert job is not None
    claim=claim_next_job(['EMAIL'])
    assert claim is not None
    assert process_claim(*claim)
    db.expire_all()
    assert db.get(EmailJob,job.id).status=='SMTP_ACCEPTED'
    assert len(state.messages)==1
    from email import message_from_string
    from email.policy import default
    message=message_from_string(state.messages[0]['data'],policy=default)
    assert order.pickup_code in message.get_body(preferencelist=('plain',)).get_content()
