"""GET map hotspot views are richer than the strict publish input.

The admin map response intentionally reuses LocationView for both ``items`` and
``hotspots``.  LocationView carries optimistic-concurrency metadata
(``version``) and map attribution (``mapVersion``), while MapHotspotInput uses
``extra='forbid'``.  Posting a GET response straight back therefore fails at
the nested hotspot fields; clients must project only the publishable fields.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.delivery_schemas import MapHotspotInput, MapPublishInput

APP_MODULES = ['delivery', 'auth']


def _publishable_hotspot() -> dict:
    return {
        'id': None,
        'name': '契约测试楼',
        'type': 'DORMITORY',
        'x': 40.0,
        'y': 50.0,
        'enabled': True,
    }


def test_admin_map_get_hotspots_are_not_a_direct_publish_payload(admin_client, api):
    created = api(admin_client, '/admin/maps', {
        'version': 2,
        'hotspots': [_publishable_hotspot()],
        'migrationConfirmed': True,
    })
    assert created.status_code == 200, created.text

    fetched = api(admin_client, '/admin/maps', None, method='GET')
    assert fetched.status_code == 200, fetched.text
    payload = fetched.json()['data']
    assert payload['hotspots']
    assert payload['hotspots'] == payload['items']

    hotspot = payload['hotspots'][0]
    assert {'id', 'name', 'type', 'x', 'y', 'enabled',
            'mapVersion', 'version'} <= set(hotspot)
    assert isinstance(hotspot['version'], int)
    assert isinstance(hotspot['mapVersion'], int)

    # Re-post the response verbatim.  The top-level fields are compatible, but
    # each LocationView still has two fields MapHotspotInput forbids.
    reposted = api(admin_client, '/admin/maps', {
        'version': payload['version'],
        'hotspots': payload['hotspots'],
        'migrationConfirmed': True,
    })
    assert reposted.status_code == 422, reposted.text
    error = reposted.json()['error']
    assert error['code'] == 'VALIDATION_ERROR'
    assert error['message'] == '输入内容不符合要求'
    fields = error['fieldErrors']
    assert fields['hotspots.0.version'] == 'Extra inputs are not permitted'
    assert fields['hotspots.0.mapVersion'] == 'Extra inputs are not permitted'


def test_map_hotspot_input_constraints_and_publish_aliases():
    """Document the schema constraints and aliases clients must honor."""
    for invalid in (
        {**_publishable_hotspot(), 'x': 100.1},
        {**_publishable_hotspot(), 'y': -0.1},
        {**_publishable_hotspot(), 'name': ''},
        {**_publishable_hotspot(), 'id': 123},
        {**_publishable_hotspot(), 'version': 1},
        {**_publishable_hotspot(), 'mapVersion': 1},
    ):
        with pytest.raises(ValidationError):
            MapHotspotInput.model_validate(invalid)

    with pytest.raises(ValidationError):
        MapPublishInput.model_validate({'version': 0, 'hotspots': [_publishable_hotspot()]})
    with pytest.raises(ValidationError):
        MapPublishInput.model_validate({'version': 1, 'hotspots': []})

    parsed = MapPublishInput.model_validate({
        'version': 2,
        'hotspots': [_publishable_hotspot()],
        'migrationConfirmed': True,
        'mapAssetId': 'asset-id',
        'expectedVersion': 1,
    })
    dumped = parsed.model_dump(by_alias=True)
    assert dumped['migrationConfirmed'] is True
    assert dumped['mapAssetId'] == 'asset-id'
    assert dumped['expectedVersion'] == 1

    # Known schema gap: type has only max_length, so an empty type is accepted.
    assert MapHotspotInput.model_validate({**_publishable_hotspot(), 'type': ''}).type == ''
