from app.core import Actor
from app import production
from app.after_sales import case_view
from app.models import AfterSale
from test_production import _seed


def test_production_queues_and_detail_include_admin_pickup(db):
    seed = _seed(db, binding='CORNER')
    calls = [
        production.list_print_tasks(db=db, actor=seed.actor),
        production.list_binding_tasks(db=db, actor=seed.actor),
        production.list_packing_tasks(db=db, actor=seed.actor),
    ]
    for response in calls:
        task = response['data']['tasks'][0]
        assert task['adminPickup'] == {
            'pickupCode': seed.order.pickup_code,
            'placedWithAccount': False,
            'customerDetailId': None,
        }
    detail = production.get_print_task(seed.tasks[0].id, db=db, actor=seed.actor)['data']
    assert detail['adminPickup']['pickupCode'] == seed.order.pickup_code


def test_after_sale_customer_view_omits_admin_pickup(db):
    seed = _seed(db)
    case = AfterSale(order_id=seed.order.id, source='CUSTOMER', actor_id=seed.order.principal_id,
        responsibility='MERCHANT', solution='REFUND', reason='错印', suggested_cents=100, data={})
    db.add(case)
    db.flush()
    customer = case_view(case)
    assert 'adminPickup' not in customer
    admin = case_view(case, True, db=db, actor=seed.actor)
    assert admin['adminPickup']['pickupCode'] == seed.order.pickup_code


def test_registered_order_pickup_respects_customer_management_capability(db):
    from app.models import Account, Principal, utcnow
    seed = _seed(db, binding='CORNER')
    principal = Principal()
    db.add(principal)
    db.flush()
    account = Account(principal_id=principal.id, email='pickup@example.invalid',
        password_hash='x', status='ACTIVE', verified_at=utcnow())
    db.add(account)
    db.flush()
    seed.order.checkout_account_id = account.id
    seed.order.owner_account_id = account.id
    db.flush()
    restricted = Actor(staff_id=seed.staff.id, staff_role='ADMIN', capabilities={'production.manage'})
    for actor, expected in ((seed.actor, account.id), (restricted, None)):
        tasks = production.list_print_tasks(db=db, actor=actor)['data']['tasks']
        assert tasks[0]['adminPickup'] == {
            'pickupCode': seed.order.pickup_code,
            'placedWithAccount': True,
            'customerDetailId': expected,
        }
    # Account association updates the shared icon without changing the checkout source.
    seed.order.checkout_account_id = None
    db.flush()
    task = production.list_print_tasks(db=db, actor=seed.actor)['data']['tasks'][0]
    assert task['adminPickup']['placedWithAccount'] is True
    assert task['adminPickup']['customerDetailId'] == account.id


def test_customer_management_order_summary_contains_pickup_origin(db):
    from app.growth import _customer_view
    from app.models import Account, Principal, utcnow
    seed = _seed(db)
    principal = Principal()
    db.add(principal)
    db.flush()
    account = Account(principal_id=principal.id, email='summary@example.invalid',
        password_hash='x', status='ACTIVE', verified_at=utcnow())
    db.add(account)
    db.flush()
    seed.order.checkout_account_id = account.id
    seed.order.owner_account_id = account.id
    db.flush()
    summary = _customer_view(db, account, actor=seed.actor).model_dump(by_alias=True)
    assert summary['orders'][0]['adminPickup'] == {
        'pickupCode': seed.order.pickup_code,
        'placedWithAccount': True,
        'customerDetailId': account.id,
    }
    private = _customer_view(db, account).model_dump(by_alias=True)
    assert private['orders'][0]['adminPickup'] is None
