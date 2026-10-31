"""Campus catalog, per-campus base maps and point-selection availability.

The original product shipped one implicit campus stored in the ``map`` config
section.  This module makes that campus explicit and lazily backfills it so
existing installations, fixtures and clients keep working unchanged.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from sqlalchemy import func, select, update

from .config import get_config
from .core import Actor, DomainError, audit, lock, touch
from .models import Asset, Campus, Location, Order, utcnow

DEFAULT_CAMPUS_NAME = '默认校区'
DEFAULT_THEME_COLOR = '#C67C4E'


def _campus_order():
    return (Campus.sort_order, Campus.created_at, Campus.id)


def _parse_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def ensure_default_campus(db) -> Campus:
    """Return the implicit first campus, creating/backfilling it if needed."""
    campus = db.scalar(select(Campus).order_by(*_campus_order()).limit(1))
    if campus is None:
        row = get_config(db, 'map')
        data = row.data or {}
        asset_id = data.get('assetId')
        if asset_id and db.get(Asset, asset_id) is None:
            asset_id = None
        campus = Campus(
            name=DEFAULT_CAMPUS_NAME,
            theme_color=DEFAULT_THEME_COLOR,
            selection_enabled=True,
            sort_order=0,
            map_asset_id=asset_id,
            map_url=data.get('mapUrl') or '',
            map_version=int(data.get('mapVersion') or 1),
            published_at=_parse_datetime(data.get('publishedAt')),
        )
        db.add(campus)
        db.flush()
        audit(db, 'SYSTEM', 'CAMPUS_BACKFILLED', 'CAMPUS', campus.id,
              data={'name': campus.name, 'mapVersion': campus.map_version})
    # Locations created before the campus table existed are members of the
    # first campus.  Keeping this idempotent also repairs rows written by old
    # clients between the migration and the first map request.
    db.execute(update(Location).where(Location.campus_id.is_(None))
               .values(campus_id=campus.id))
    db.flush()
    return campus


def is_default_campus(db, campus: Campus) -> bool:
    first_id = db.scalar(select(Campus.id).order_by(*_campus_order()).limit(1))
    return first_id == campus.id


def campus_is_open(db, location: Location | None) -> bool:
    """Whether a location belongs to a campus that currently accepts points."""
    if location is None:
        return False
    if not location.campus_id:
        # Pre-campus data is treated as the legacy, always-open campus.
        return True
    campus = db.get(Campus, location.campus_id)
    return bool(campus and campus.selection_enabled)


def location_view(location: Location, map_version: int | None = None) -> dict:
    return {
        'id': location.id,
        'name': location.name,
        'type': location.type,
        'x': round(location.x * 100, 4),
        'y': round(location.y * 100, 4),
        'enabled': location.enabled,
        'mapVersion': location.map_version if map_version is None else map_version,
        'version': location.version,
        'campusId': location.campus_id,
    }


def _map_url(campus: Campus) -> str | None:
    if campus.map_url:
        return campus.map_url
    if campus.map_asset_id:
        return f'/api/v1/assets/{campus.map_asset_id}/content'
    return None


def _campus_locations(db, campus: Campus, *, enabled_only: bool):
    query = select(Location).where(Location.campus_id == campus.id)
    if enabled_only:
        query = query.where(Location.enabled.is_(True))
    return list(db.scalars(query.order_by(Location.name, Location.id)))


def public_campus_view(db, campus: Campus) -> dict:
    items = [] if not campus.selection_enabled else [
        location_view(location) for location in _campus_locations(db, campus, enabled_only=True)]
    return {
        'id': campus.id,
        'name': campus.name,
        'themeColor': campus.theme_color,
        'selectionEnabled': campus.selection_enabled,
        'sortOrder': campus.sort_order,
        'mapVersion': campus.map_version,
        'mapAssetId': campus.map_asset_id,
        'mapUrl': _map_url(campus),
        'publishedAt': campus.published_at,
        'items': items,
    }


def admin_campus_view(db, campus: Campus) -> dict:
    hotspots = [location_view(location) for location in
                _campus_locations(db, campus, enabled_only=False)]
    return {
        'id': campus.id,
        'name': campus.name,
        'themeColor': campus.theme_color,
        'selectionEnabled': campus.selection_enabled,
        'sortOrder': campus.sort_order,
        'version': campus.version,
        'mapVersion': campus.map_version,
        'mapAssetId': campus.map_asset_id,
        'mapUrl': _map_url(campus),
        'publishedAt': campus.published_at,
        'hotspots': hotspots,
    }


def public_map_data(db) -> dict:
    default = ensure_default_campus(db)
    campuses = list(db.scalars(select(Campus).order_by(*_campus_order())))
    public = [public_campus_view(db, campus) for campus in campuses]
    default_public = next(item for item in public if item['id'] == default.id)
    return {
        # Legacy fields describe the first/legacy campus for old clients.
        'version': default.map_version,
        'mapAssetId': default.map_asset_id,
        'mapUrl': _map_url(default),
        'publishedAt': default.published_at,
        'items': default_public['items'],
        'campuses': public,
    }


def admin_map_data(db) -> dict:
    default = ensure_default_campus(db)
    hotspots = [location_view(location) for location in
                _campus_locations(db, default, enabled_only=False)]
    campuses = [public_campus_view(db, campus) for campus in
                db.scalars(select(Campus).order_by(*_campus_order()))]
    return {
        'version': default.map_version,
        'mapAssetId': default.map_asset_id,
        'mapUrl': _map_url(default),
        'publishedAt': default.published_at,
        'items': hotspots,
        'hotspots': hotspots,
        'campuses': campuses,
    }


def admin_campuses_data(db) -> dict:
    ensure_default_campus(db)
    campuses = list(db.scalars(select(Campus).order_by(*_campus_order())))
    return {'items': [admin_campus_view(db, campus) for campus in campuses]}


def check_public_map_asset(db, asset_id: str) -> Asset:
    asset = db.get(Asset, asset_id)
    if (asset is None or asset.deleted_at is not None or asset.status != 'READY'
            or asset.purpose != 'PUBLIC_MAP'):
        raise DomainError('INVALID_PUBLIC_ASSET', '请选择已上传并就绪的公开地图资产', 422)
    return asset


def sync_legacy_map_config(db, campus: Campus) -> None:
    """Mirror the first campus into the legacy ``map`` config section."""
    if not is_default_campus(db, campus):
        return
    row = get_config(db, 'map')
    data = deepcopy(row.data or {})
    data.update({
        'mapVersion': campus.map_version,
        'assetId': campus.map_asset_id,
        'mapUrl': campus.map_url or '',
    })
    if campus.published_at is not None:
        data['publishedAt'] = campus.published_at.isoformat()
    data['hotspotCount'] = db.scalar(select(func.count(Location.id)).where(
        Location.campus_id == campus.id)) or 0
    row.data = data
    touch(row)


def create_campus(db, payload, actor: Actor) -> dict:
    name = payload.name.strip()
    if db.scalar(select(Campus.id).where(Campus.name == name)):
        raise DomainError('CAMPUS_NAME_TAKEN', '校区名称已存在', 409)
    campus = Campus(
        name=name,
        theme_color=payload.theme_color,
        selection_enabled=payload.selection_enabled,
        sort_order=payload.sort_order,
        map_url='',
        map_version=1,
    )
    db.add(campus)
    db.flush()
    audit(db, actor, 'CAMPUS_CREATED', 'CAMPUS', campus.id, data={'name': name})
    sync_legacy_map_config(db, campus)
    db.flush()
    return admin_campus_view(db, campus)


def update_campus(db, campus_id: str, payload, actor: Actor) -> dict:
    campus = lock(db, Campus, campus_id, payload.expected_version)
    if payload.name is not None:
        name = payload.name.strip()
        duplicate = db.scalar(select(Campus.id).where(
            Campus.name == name, Campus.id != campus.id))
        if duplicate:
            raise DomainError('CAMPUS_NAME_TAKEN', '校区名称已存在', 409)
        campus.name = name
    if payload.theme_color is not None:
        campus.theme_color = payload.theme_color
    if payload.selection_enabled is not None:
        campus.selection_enabled = payload.selection_enabled
    if payload.sort_order is not None:
        campus.sort_order = payload.sort_order
    touch(campus)
    audit(db, actor, 'CAMPUS_UPDATED', 'CAMPUS', campus.id,
          data={'fields': sorted(payload.model_fields_set - {'expected_version'})})
    sync_legacy_map_config(db, campus)
    db.flush()
    return admin_campus_view(db, campus)


def delete_campus(db, campus_id: str, actor: Actor) -> dict:
    campus = lock(db, Campus, campus_id)
    if (db.scalar(select(func.count(Campus.id))) or 0) <= 1:
        raise DomainError('CAMPUS_LAST', '至少保留一个校区', 409)
    if db.scalar(select(func.count(Location.id)).where(Location.campus_id == campus.id)):
        raise DomainError('RESOURCE_REFERENCED', '校区下仍有点位，请先删除或转移点位', 409)
    db.delete(campus)
    audit(db, actor, 'CAMPUS_DELETED', 'CAMPUS', campus.id)
    db.flush()
    return {'id': campus.id, 'deleted': True}


def publish_campus(db, campus_id: str, payload, actor: Actor) -> dict:
    campus = lock(db, Campus, campus_id, payload.expected_version)
    current_version = campus.map_version
    current_asset = campus.map_asset_id
    if payload.version < current_version:
        raise DomainError('MAP_VERSION_CONFLICT', '地图版本不能倒退', 409,
                          {'currentVersion': current_version})

    changed = payload.map_asset_id not in (None, current_asset)
    if payload.map_asset_id and payload.map_asset_id != current_asset:
        if not payload.migration_confirmed:
            raise DomainError('MAP_MIGRATION_REQUIRED', '更换底图前必须确认热点已迁移', 422)
        asset = check_public_map_asset(db, payload.map_asset_id)
        campus.map_asset_id = asset.id
        campus.map_url = f'/api/v1/assets/{asset.id}/content'
    elif payload.map_asset_id:
        campus.map_asset_id = payload.map_asset_id

    existing = {location.id: location for location in _campus_locations(
        db, campus, enabled_only=False)}
    seen: set[str] = set()
    for hotspot in payload.hotspots:
        if hotspot.id and hotspot.id in seen:
            raise DomainError('MAP_HOTSPOT_DUPLICATE', '楼栋热点重复', 422,
                              {'id': hotspot.id})
        location = existing.get(hotspot.id) if hotspot.id else None
        if location is None:
            if hotspot.id:
                other = db.get(Location, hotspot.id)
                if other is not None:
                    raise DomainError('MAP_HOTSPOT_CAMPUS_CONFLICT',
                                      '该点位属于其他校区', 422, {'id': hotspot.id})
            location = Location(
                id=hotspot.id or None,
                campus_id=campus.id,
                name=hotspot.name,
                type=hotspot.type,
                x=hotspot.x / 100,
                y=hotspot.y / 100,
                enabled=hotspot.enabled,
                map_version=payload.version,
            )
            db.add(location)
            db.flush()
        else:
            if (location.name, location.type, round(location.x * 100, 4),
                    round(location.y * 100, 4), location.enabled) != (
                    hotspot.name, hotspot.type, round(hotspot.x, 4),
                    round(hotspot.y, 4), hotspot.enabled):
                changed = True
            location.name = hotspot.name
            location.type = hotspot.type
            location.x = hotspot.x / 100
            location.y = hotspot.y / 100
            location.enabled = hotspot.enabled
            location.map_version = payload.version
            touch(location)
        seen.add(location.id)

    for location_id, location in existing.items():
        if location_id in seen:
            continue
        referenced = db.scalar(select(func.count(Order.id)).where(
            Order.location_id == location.id))
        if referenced:
            raise DomainError('RESOURCE_REFERENCED', '楼栋仍被订单引用，请改为停止配送而不是删除',
                              409, {'locationId': location.id})
        db.delete(location)

    new_version = max(
        payload.version,
        current_version + 1 if changed and payload.version <= current_version else payload.version,
    )
    if new_version != payload.version:
        for location in _campus_locations(db, campus, enabled_only=False):
            if location.id in seen:
                location.map_version = new_version
                touch(location)
    campus.map_version = new_version
    campus.published_at = utcnow()
    touch(campus)
    audit(db, actor, 'CAMPUS_MAP_PUBLISHED', 'CAMPUS', campus.id,
          data={'version': new_version, 'hotspots': len(seen)})
    sync_legacy_map_config(db, campus)
    db.flush()
    return admin_campus_view(db, campus)
