import type { Batch, Building, Campus, Order } from "./types";

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
