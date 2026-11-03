"""Campus identity guards shared by checkout, settlement and dispatch.

Missing legacy identities are never an invitation to accept new orders from
every campus. The explicit legacy escape is only for already-existing delivery
executions; callers must establish that execution before opting in.
"""
from sqlalchemy import select

from .core import DomainError
from .models import Campus, Location


def order_campus_id(db, order) -> str | None:
    if not order.location_id:
        return None
    return db.scalar(select(Location.campus_id).where(Location.id == order.location_id))


def ensure_slot_campus(db, slot, campus_id: str | None, *, allow_legacy: bool = False) -> Campus:
    if not campus_id or (campus := db.get(Campus, campus_id)) is None:
        raise DomainError('CAMPUS_CONFIG_MISSING', '订单或批次缺少有效校区，请先核对配送归属', 409)
    if not slot.campus_id:
        if allow_legacy:
            return campus
        raise DomainError('SLOT_CAMPUS_MISSING', '原时段尚未确认校区，请先完成归属核对或协调新时段', 409,
                          {'slotId': slot.id, 'campusId': campus_id})
    if slot.campus_id != campus_id:
        raise DomainError('SLOT_CAMPUS_MISMATCH', '配送楼栋、时段和批次必须属于同一校区，请重新选择', 409,
                          {'slotId': slot.id, 'slotCampusId': slot.campus_id, 'campusId': campus_id})
    return campus


def slot_campus_snapshot(db, slot) -> dict:
    campus = db.get(Campus, slot.campus_id) if slot.campus_id else None
    return {'campusId': slot.campus_id, 'campusName': campus.name if campus else None}


def location_campus_snapshot(db, location) -> dict:
    campus = db.get(Campus, location.campus_id) if location.campus_id else None
    return {'id': location.id, 'name': location.name, 'mapVersion': location.map_version,
            'campusId': location.campus_id, 'campusName': campus.name if campus else None}
