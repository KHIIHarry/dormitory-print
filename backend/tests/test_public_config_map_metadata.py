"""Public config must never expose private map publication metadata."""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from app.config import PUBLIC_FIELD_NAMES, PublicConfigView
from app.models import Asset, ConfigSection, utcnow

APP_MODULES = ['auth', 'delivery', 'mail']


def test_public_allowlist_is_derived_from_public_config_view():
    expected = set(PublicConfigView.model_fields)
    expected.update(
        field.alias for field in PublicConfigView.model_fields.values()
        if field.alias
    )
    assert PUBLIC_FIELD_NAMES == frozenset(expected)
    for private in ('publishedAt', 'publishedBy', 'hotspotCount', 'migrationConfirmed'):
        assert private not in PUBLIC_FIELD_NAMES
    assert 'mapUrl' in PUBLIC_FIELD_NAMES
    assert 'map_url' in PUBLIC_FIELD_NAMES


def _assert_public_map(data, *, asset_id, map_version):
    assert data['mapUrl'] == f'/api/v1/assets/{asset_id}/content'
    assert data['assetId'] == asset_id
    assert data['mapVersion'] == map_version
    for key in ('publishedAt', 'publishedBy', 'hotspotCount', 'migrationConfirmed'):
        assert key not in data, f'private map metadata leaked: {key}'


def test_public_config_filters_stored_map_metadata(db, api, client):
    db.add(ConfigSection(section='map', version=7, data={
        'mapUrl': '/api/v1/assets/public-map/content',
        'mapVersion': 5,
        'assetId': 'public-map',
        'publishedAt': utcnow().isoformat(),
        'publishedBy': 'staff-private',
        'hotspotCount': 9,
        'migrationConfirmed': True,
    }))
    db.commit()

    response = api(client, '/public/config', None, method='GET')
    assert response.status_code == 200, response.text
    data = response.json()['data']
    _assert_public_map(data, asset_id='public-map', map_version=5)
    # Legitimate public fields from the other flattened sections survive.
    assert data['maxFiles'] == 10
    assert data['formats']
    assert data['displayName'] == 'DormPrint'
    assert set(data['prices']) == {
        'bwSingle', 'bwDuplex', 'colorSingle', 'colorDuplex',
        'photo', 'corner', 'side',
    }
    # Nested DTO semantics are unchanged and the response round-trips through
    # the strict public model without dropping/adding a field.
    assert set(data['margins']) == {'top', 'bottom', 'left', 'right'}
    assert set(data['pauses']) == {'newOrders', 'fileProcessing', 'production', 'delivery'}
    assert PublicConfigView.model_validate(data).model_dump(by_alias=True) == data


def test_actual_map_publication_exposes_metadata_only_internal(db, api, admin_client):
    asset = Asset(
        purpose='PUBLIC_MAP', name='map.png', mime='image/png', size=10,
        sha256='a' * 64, storage_key=f'map/{uuid4()}', status='READY')
    db.add(asset)
    db.commit()

    published = api(admin_client, '/admin/maps', {
        'version': 2,
        'hotspots': [
            {'id': None, 'name': '测试楼', 'type': 'DORMITORY',
             'x': 40.0, 'y': 50.0, 'enabled': True},
        ],
        'migrationConfirmed': True,
        'mapAssetId': asset.id,
    })
    assert published.status_code == 200, published.text

    stored = db.scalar(select(ConfigSection).where(ConfigSection.section == 'map'))
    assert stored.data['publishedAt']
    assert stored.data['publishedBy']
    assert stored.data['hotspotCount'] == 1
    assert stored.data['migrationConfirmed'] is True

    response = api(admin_client, '/public/config', None, method='GET')
    assert response.status_code == 200, response.text
    data = response.json()['data']
    _assert_public_map(data, asset_id=asset.id, map_version=2)
