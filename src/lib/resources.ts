import { ADMIN } from "./api";
import { adminResources, adminNavAllowed } from "../admin/resources";
import type { DataState } from "./types";
export type ResourcePlan = { name: keyof DataState; path: string };
export function planResources(
  path: string,
  search: string,
  role: string | null,
  capabilities: readonly string[],
): ResourcePlan[] {
  if (path.startsWith(ADMIN)) {
    if (role !== "ADMIN" || !adminNavAllowed(path, capabilities)) return [];
    const names = adminResources(path, capabilities);
    const paths: Record<string, string> = {
      config: "/admin/config",
      orders: "/admin/orders" + (path === `${ADMIN}/orders` ? search : ""),
      batches: "/admin/batches",
      staff: "/admin/staff",
      exceptions: "/admin/exceptions",
      trash: "/admin/trash",
      audit: "/admin/audit-logs",
    };
    const result: ResourcePlan[] = names.map((name) => ({
      name,
      path: paths[name],
    }));
    if (path === `${ADMIN}/map`)
      result.push({ name: "config", path: "/public/config" });
    if (path === `${ADMIN}/map` || names.includes("orders"))
      result.push({ name: "buildings", path: "/public/map" });
    if (
      path === `${ADMIN}/slots` ||
      path === `${ADMIN}/orders` ||
      path.startsWith(`${ADMIN}/batches`)
    )
      result.push({ name: "slots", path: "/public/slots" });
    return result;
  }
  if (path.startsWith("/courier")) {
    if (path.includes("/account") || path.endsWith("/login")) return [];
    if (role !== "COURIER" && role !== "ADMIN") return [];
    // Mirrors StaffGuard's courier rule: ADMIN needs the delivery capability;
    // a mail-only admin must not prefetch delivery resources from a deep link.
    if (
      role === "ADMIN" &&
      !capabilities.includes("*") &&
      !capabilities.includes("delivery.manage")
    )
      return [];
    return [
      { name: "orders", path: "/courier/batches" },
      { name: "buildings", path: "/public/map" },
      { name: "slots", path: "/public/slots" },
    ];
  }
  if (!path.startsWith("/u")) return [];
  const result: ResourcePlan[] = [{ name: "config", path: "/public/config" }];
  if (path.startsWith("/u/orders")) {
    const id = path.match(/^\/u\/orders\/([^/]+)/)?.[1];
    result.push({
      name: "orders",
      path: id
        ? `/orders/${encodeURIComponent(decodeURIComponent(id))}`
        : "/customer/orders",
    });
  }
  if (path === "/u/orders")
    result.push({ name: "buildings", path: "/public/map" });
  if (path === "/u/review" || path.startsWith("/u/orders/"))
    result.push(
      { name: "buildings", path: "/public/map" },
      { name: "slots", path: "/public/slots" },
    );
  return result;
}
export async function loadIndependently<T>(
  plan: ResourcePlan[],
  load: (resource: ResourcePlan) => Promise<T>,
  success: (resource: ResourcePlan, data: T) => void,
  failure: (resource: ResourcePlan, error: unknown) => void,
) {
  await Promise.all(
    plan.map(async (resource) => {
      try {
        success(resource, await load(resource));
      } catch (error) {
        failure(resource, error);
      }
    }),
  );
}
