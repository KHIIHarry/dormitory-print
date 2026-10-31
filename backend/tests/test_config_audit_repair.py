"""Focused config read and audit query/DTO regressions."""
from __future__ import annotations

from datetime import timedelta

from app.models import AuditLog, ConfigSection, TrashEntry, utcnow

APP_MODULES = ['auth', 'maintenance', 'mail']


def test_superadmin_can_read_authoritative_admin_config(db, api, admin_client):
    response = api(admin_client, '/admin/config', method='GET')
    assert response.status_code == 200, response.text
    data = response.json()['data']

    assert data['version'] >= 1
    assert set(data['versions']) == {
        'prices', 'services', 'photo', 'contact', 'announcement',
        'workflow', 'map',
    }
    assert data['sections']['prices']['version'] == data['versions']['prices']
    assert data['sections']['services']['version'] == data['versions']['services']
    assert data['sections']['workflow']['version'] == data['versions']['workflow']
    assert data['sections']['prices']['bwSingle'] is None
    assert 'host' not in data['sections']


def test_audit_list_exposes_stable_fields_and_safe_actor_label(db, api, admin_client):
    actor_staff = admin_client.staff
    row = AuditLog(actor_id=actor_staff.id, action='CONFIG_UPDATED',
                   target_type='CONFIG', target_id='prices',
                   reason='调整价格', request_id='req-config-1',
                   data={'version': 2, 'fields': ['bwSingle'],
                         'phone': '13800000000',
                         'nested': {'email': 'x@example.com', 'keep': 'ok'}},
                   created_at=utcnow() - timedelta(minutes=1))
    db.add(row)
    db.commit()

    response = api(admin_client, '/admin/audit-logs', method='GET')
    assert response.status_code == 200, response.text
    data = response.json()['data']
    item = next(value for value in data['items'] if value['id'] == row.id)

    assert item['createdAt']
    assert item['actorId'] == actor_staff.id
    assert item['targetType'] == 'CONFIG'
    assert item['targetId'] == 'prices'
    assert item['reason'] == '调整价格'
    assert item['requestId'] == 'req-config-1'
    assert item['data']['fields'] == ['bwSingle']
    assert 'phone' not in item['data']
    assert 'email' not in item['data']['nested']
    assert item['data']['nested']['keep'] == 'ok'
    assert item['actorLabel'] == actor_staff.name
    assert item['actorRole'] == 'ADMIN'


def test_audit_actor_date_target_filters_and_cursor(
        db, api, admin_client, staff_factory):
    actor = admin_client.staff
    other = staff_factory(role='COURIER')
    other.name = '配送员甲'
    db.commit()
    base = utcnow() - timedelta(minutes=10)
    older = AuditLog(actor_id=actor.id, action='CONFIG_UPDATED',
                     target_type='CONFIG', target_id='prices',
                     reason='', request_id='req-old', data={},
                     created_at=base)
    newer = AuditLog(actor_id=actor.id, action='CONFIG_UPDATED',
                     target_type='CONFIG', target_id='services',
                     reason='', request_id='req-new', data={},
                     created_at=base + timedelta(minutes=2))
    foreign = AuditLog(actor_id=other.id, action='ORDER_CANCELLED',
                       target_type='ORDER', target_id='order-x',
                       reason='', request_id='req-foreign', data={},
                       created_at=base + timedelta(minutes=1))
    db.add_all([older, newer, foreign])
    db.commit()

    response = api(admin_client, '/admin/audit-logs', method='GET', params={
        'actorId': actor.id, 'targetType': 'CONFIG', 'targetId': 'prices',
        'createdFrom': (base - timedelta(seconds=1)).isoformat(),
        'createdTo': (base + timedelta(seconds=1)).isoformat(),
    })
    assert response.status_code == 200, response.text
    rows = response.json()['data']['items']
    assert [row['id'] for row in rows] == [older.id]
    assert rows[0]['actorLabel'] == actor.name
    assert rows[0]['action'] == 'CONFIG_UPDATED'
    assert rows[0]['targetId'] == 'prices'

    page1 = api(admin_client, '/admin/audit-logs', method='GET', params={
        'actorId': actor.id, 'limit': 1,
    })
    assert page1.status_code == 200, page1.text
    first_page = page1.json()['data']
    assert [row['id'] for row in first_page['items']] == [newer.id]
    assert first_page['nextCursor'] == newer.id

    page2 = api(admin_client, '/admin/audit-logs', method='GET', params={
        'actorId': actor.id, 'limit': 1, 'cursor': first_page['nextCursor'],
    })
    assert page2.status_code == 200, page2.text
    assert [row['id'] for row in page2.json()['data']['items']] == [older.id]

    invalid = api(admin_client, '/admin/audit-logs', method='GET', params={
        'cursor': 'missing-audit-cursor',
    })
    assert invalid.status_code == 422, invalid.text
    assert invalid.json()['error']['code'] == 'CURSOR_INVALID'


def test_audit_q_searches_action_target_and_reason_case_insensitively(
        db, api, admin_client):
    actor = admin_client.staff
    base = utcnow() - timedelta(minutes=5)
    action_row = AuditLog(
        actor_id=actor.id, action='ORDER_REFUNDED', target_type='ORDER',
        target_id='ord-a', reason='普通操作', request_id='req-q-action',
        data={}, created_at=base)
    target_row = AuditLog(
        actor_id=actor.id, action='NOTE_ADDED', target_type='CUSTOMER_ACCOUNT',
        target_id='acct-Q-9', reason='普通操作', request_id='req-q-target',
        data={}, created_at=base + timedelta(seconds=1))
    reason_row = AuditLog(
        actor_id=actor.id, action='LOCATION_EXCEPTION_DECIDED',
        target_type='DELIVERY_ASSIGNMENT', target_id='asn-r',
        reason='定位偏差已批准', request_id='req-q-reason',
        data={}, created_at=base + timedelta(seconds=2))
    data_row = AuditLog(
        actor_id=actor.id, action='NOISE_ONLY', target_type='OTHER',
        target_id='other', reason='nothing', request_id='req-q-data',
        data={'secret': 'needle-only-in-data'}, created_at=base + timedelta(seconds=3))
    trashed = AuditLog(
        actor_id=actor.id, action='TRASHED_ONLY', target_type='ORDER',
        target_id='ord-trashed', reason='trashed-needle', request_id='req-q-trashed',
        data={}, created_at=base + timedelta(seconds=4))
    db.add_all([action_row, target_row, reason_row, data_row, trashed])
    db.flush()
    db.add(TrashEntry(
        target_type='AUDIT_LOG', target_id=trashed.id, actor_id=actor.id,
        reason='hidden', status='DELETED', data={},
        purge_at=utcnow() + timedelta(days=1)))
    db.commit()

    def search(term, **filters):
        response = api(admin_client, '/admin/audit-logs', method='GET',
                       params={'q': term, **filters})
        assert response.status_code == 200, response.text
        return {item['id'] for item in response.json()['data']['items']}

    assert search('refunded') == {action_row.id}
    assert search('acct-q') == {target_row.id}
    assert search('定位') == {reason_row.id}
    assert search('needle-only-in-data') == set()
    assert search('refunded', targetType='CUSTOMER_ACCOUNT') == set()
    assert search('trashed-needle') == set()

    too_long = api(admin_client, '/admin/audit-logs', method='GET',
                   params={'q': 'x' * 201})
    assert too_long.status_code == 422, too_long.text


def test_audit_actor_labels_resolve_account_principal_system_and_staff_prefix(
        db, api, client, admin_client, account_factory):
    account = account_factory(display_name='测试客户')
    login = api(client, '/customer/auth/login', {
        'email': account.email,
        'password': 'Testing-only-Password-42!',
    })
    assert login.status_code == 200, login.text

    principal_row = AuditLog(
        actor_id=account.principal_id, action='CUSTOMER_PROFILE_UPDATED',
        target_type='account', target_id=account.id, reason='',
        request_id='req-principal', data={}, created_at=utcnow())
    system_row = AuditLog(
        actor_id='system:account-lifecycle', action='ACCOUNT_DELETION_FINALIZED',
        target_type='ACCOUNT', target_id=account.id, reason='',
        request_id='req-system', data={}, created_at=utcnow())
    prefixed_staff_row = AuditLog(
        actor_id='staff:' + admin_client.staff.id, action='CONFIG_UPDATED',
        target_type='CONFIG', target_id='prices', reason='',
        request_id='req-staff-prefix', data={}, created_at=utcnow())
    unknown_row = AuditLog(
        actor_id='unknown-actor', action='UNKNOWN_ACTION',
        target_type='UNKNOWN', target_id='unknown', reason='',
        request_id='req-unknown', data={}, created_at=utcnow())
    db.add_all([principal_row, system_row, prefixed_staff_row, unknown_row])
    db.commit()

    response = api(admin_client, '/admin/audit-logs', method='GET',
                   params={'limit': 100})
    assert response.status_code == 200, response.text
    items = {item['id']: item for item in response.json()['data']['items']}

    login_item = next(
        item for item in items.values()
        if item['action'] == 'CUSTOMER_LOGIN'
        and item['actorId'] == 'account:' + account.id)
    assert login_item['actorLabel'] == '测试客户'
    assert login_item['actorRole'] == 'CUSTOMER'

    assert items[principal_row.id]['actorId'] == account.principal_id
    assert items[principal_row.id]['actorLabel'] == '测试客户'
    assert items[principal_row.id]['actorRole'] == 'CUSTOMER'

    assert items[system_row.id]['actorId'] == 'system:account-lifecycle'
    assert items[system_row.id]['actorLabel'] == '系统'
    assert items[system_row.id]['actorRole'] == 'SYSTEM'

    assert items[prefixed_staff_row.id]['actorLabel'] == admin_client.staff.name
    assert items[prefixed_staff_row.id]['actorRole'] == 'ADMIN'

    assert items[unknown_row.id]['actorId'] == 'unknown-actor'
    assert items[unknown_row.id]['actorLabel'] == '未知操作人'
    assert items[unknown_row.id]['actorRole'] is None


def test_photo_config_merges_and_persists_a4_margins(db, api, admin_client):
    from app.config import get_config

    # Simulate a row written before A4 calibration existed.
    row = get_config(db, 'photo')
    row.version = 11
    row.data = {
        'photoWidthMm': 127,
        'photoHeightMm': 89,
        'margins': {'top': 3, 'bottom': 3, 'left': 3, 'right': 3},
    }
    db.commit()

    read = api(admin_client, '/admin/config', method='GET')
    assert read.status_code == 200, read.text
    data = read.json()['data']
    assert data['a4Margins'] == {'top': 3, 'bottom': 3, 'left': 3, 'right': 3}
    assert data['sections']['photo']['a4Margins'] == data['a4Margins']
    assert data['sections']['photo']['version'] == 11

    patched = api(admin_client, '/admin/config/photo', {
        'expectedVersion': 11,
        'a4Margins': {'top': 5, 'right': 6, 'bottom': 7, 'left': 8},
    }, method='PATCH')
    assert patched.status_code == 200, patched.text
    body = patched.json()['data']
    assert body['version'] == 12
    assert body['a4Margins'] == {'top': 5, 'right': 6, 'bottom': 7, 'left': 8}
    assert body['photoWidthMm'] == 127
    db.refresh(row)
    assert row.data['a4Margins'] == {'top': 5, 'right': 6, 'bottom': 7, 'left': 8}

    # A later photo patch that does not mention A4 margins keeps them.
    photo_only = api(admin_client, '/admin/config/photo', {
        'expectedVersion': 12,
        'photoWidthMm': 128,
    }, method='PATCH')
    assert photo_only.status_code == 200, photo_only.text
    assert photo_only.json()['data']['a4Margins'] == {
        'top': 5, 'right': 6, 'bottom': 7, 'left': 8,
    }

    invalid = api(admin_client, '/admin/config/photo', {
        'expectedVersion': 13,
        'a4Margins': {'top': -1, 'right': 3, 'bottom': 3, 'left': 3},
    }, method='PATCH')
    assert invalid.status_code == 422, invalid.text
