import type { Batch, Building, Campus, Order, Slot } from "./types";

/** Resolve the campus that owns a delivery location (building). */
export function campusOfLocation(
  campuses: Campus[] | undefined,
  buildings: Building[] | undefined,
  locationId: string | null | undefined,
): Campus | undefined {
  if (!locationId) return undefined;
  const building = (buildings || []).find((item) => item.id === locationId);
  if (!building?.campusId) return undefined;
  return (campuses || []).find((campus) => campus.id === building.campusId);
}

export function campusOfOrder(
  campuses: Campus[] | undefined,
  buildings: Building[] | undefined,
  order: Pick<Order, "locationId"> | null | undefined,
): Campus | undefined {
  return order ? campusOfLocation(campuses, buildings, order.locationId) : undefined;
}

/** All campuses represented by a batch's current orders, in campus sort order. */
export function campusesOfBatch(
  campuses: Campus[] | undefined,
  buildings: Building[] | undefined,
  orders: Order[],
  batch: Pick<Batch, "orderIds" | "campusId">,
): Campus[] {
  if (batch.campusId) return (campuses || []).filter((campus) => campus.id === batch.campusId);
  return memberCampusesOfBatch(campuses, buildings, orders, batch);
}

/** Member campuses diagnose inconsistent legacy assignments independently of ownership. */
export function memberCampusesOfBatch(
  campuses: Campus[] | undefined,
  buildings: Building[] | undefined,
  orders: Order[],
  batch: Pick<Batch, "orderIds">,
): Campus[] {
  const orderById = new Map(orders.map((order) => [order.id, order]));
  const ids = new Set<string>();
  for (const orderId of batch.orderIds) {
    const campus = campusOfOrder(campuses, buildings, orderById.get(orderId));
    if (campus) ids.add(campus.id);
  }
  return (campuses || []).filter((campus) => ids.has(campus.id));
}

/** Slots without an explicit campus are legacy records, never available for selection. */
export function slotsForCampus(slots: Slot[], campusId: string | null | undefined): Slot[] {
  return campusId ? slots.filter((slot) => slot.campusId === campusId) : [];
}

/** Only enabled locations with definite matching ownership are selectable. */
export function selectableBuildingsForCampus<T extends { campusId?: string | null; enabled: boolean }>(buildings: T[], campusId: string | null | undefined): T[] {
  return campusId ? buildings.filter((building) => building.enabled && building.campusId === campusId) : [];
}

/** Historical delivery location supplied either at the top level or in order data. */
export function orderLocationSnapshot(order: unknown): { name?: string; campusId?: string | null } | undefined {
  if (!order || typeof order !== "object") return undefined;
  const value = order as { locationSnapshot?: unknown; data?: { locationSnapshot?: unknown } };
  const snapshot = value.locationSnapshot ?? value.data?.locationSnapshot;
  if (!snapshot || typeof snapshot !== "object") return undefined;
  return snapshot as { name?: string; campusId?: string | null };
}
