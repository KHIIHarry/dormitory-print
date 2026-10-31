"""Multi-campus map configuration contract.

The public map keeps its legacy shape for old clients, but every campus now
owns its base map, publication version and hotspot collection.  These tests
exercise the HTTP contract and the closed-campus order guard.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import Actor, actor_dep
from app.models import Campus, Location

APP_MODULES = ['delivery']


@pytest.fixture
def act_as(app):
    def apply(staff):
        app.dependency_overrides[actor_dep] = lambda: Actor(
            staff_id=staff.id, staff_role=staff.role,
            capabilities=set(staff.capabilities or []))
        return staff
    yield apply
    app.dependency_overrides.pop(actor_dep, None)


def test_public_map_lazily_backfills_default_campus(client: TestClient, db):
    db.add(Location(name='一号楼', type='DORMITORY', x=0.2, y=0.3, enabled=True))
    db.commit()

    response = client.get('/api/v1/public/map')
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['campuses'], 'the legacy single map must surface as one campus'
    campus = data['campuses'][0]
    assert campus['name'] == '默认校区'
    assert campus['selectionEnabled'] is True
    assert len(campus['items']) == 1
    assert campus['items'][0]['name'] == '一号楼'
    assert campus['items'][0]['campusId'] == campus['id']
    # The legacy fields remain populated for older clients.
    assert data['items'][0]['campusId'] == campus['id']


def test_admin_campus_crud_and_selection_toggle(client: TestClient, db, staff_factory, act_as, api):
    act_as(staff_factory(role='ADMIN'))
    created = api(client, '/admin/campuses', {
        'name': '东校区', 'themeColor': '#B5654A', 'selectionEnabled': True, 'sortOrder': 2,
    })
    assert created.status_code == 200, created.text
    campus = created.json()['data']
    assert campus['name'] == '东校区'
    assert campus['themeColor'] == '#B5654A'
    assert campus['version'] == 1
    assert campus['hotspots'] == []

    listed = client.get('/api/v1/admin/campuses', headers={'Origin': 'http://testserver'})
    assert listed.status_code == 200, listed.text
    assert any(item['id'] == campus['id'] for item in listed.json()['data']['items'])

    toggled = api(client, f"/admin/campuses/{campus['id']}", {
        'expectedVersion': campus['version'], 'selectionEnabled': False,
    }, 'PATCH')
    assert toggled.status_code == 200, toggled.text
    assert toggled.json()['data']['selectionEnabled'] is False

    public = client.get('/api/v1/public/map')
    assert public.status_code == 200
    closed = next(item for item in public.json()['data']['campuses']
                  if item['id'] == campus['id'])
    assert closed['selectionEnabled'] is False
    assert closed['items'] == []


def test_campus_publish_keeps_hotspots_on_that_campus(client: TestClient, db, staff_factory, act_as, api):
    act_as(staff_factory(role='ADMIN'))
    campus = api(client, '/admin/campuses', {
        'name': '西校区', 'themeColor': '#6F8790', 'selectionEnabled': True,
    }).json()['data']

    published = api(client, f"/admin/campuses/{campus['id']}/map", {
        'version': 1,
        'expectedVersion': campus['version'],
        'migrationConfirmed': False,
        'hotspots': [
            {'name': '图书馆', 'type': 'TEACHING', 'x': 40, 'y': 50, 'enabled': True},
            {'name': '西区食堂', 'type': 'DORMITORY', 'x': 60, 'y': 55, 'enabled': True},
        ],
    })
    assert published.status_code == 200, published.text
    body = published.json()['data']
    assert body['mapVersion'] == 1
    assert len(body['hotspots']) == 2
    assert all(item['campusId'] == campus['id'] for item in body['hotspots'])

    public = client.get('/api/v1/public/map').json()['data']
    west = next(item for item in public['campuses'] if item['id'] == campus['id'])
    assert {item['name'] for item in west['items']} == {'图书馆', '西区食堂'}

    rows = db.query(Location).filter(Location.campus_id == campus['id']).all()
    assert len(rows) == 2
    assert all(0 <= row.x <= 1 and 0 <= row.y <= 1 for row in rows)


def test_closed_campus_is_not_open_for_new_locations(db):
    from app.campuses import campus_is_open
    campus = Campus(name='暂停校区', selection_enabled=False)
    db.add(campus)
    db.flush()
    location = Location(name='暂停楼', campus_id=campus.id)
    db.add(location)
    db.flush()
    assert campus_is_open(db, location) is False
    campus.selection_enabled = True
    db.flush()
    assert campus_is_open(db, location) is True
