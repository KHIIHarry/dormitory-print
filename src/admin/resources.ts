import { ADMIN } from "../lib/api";
export type AdminResource =
  "config" | "orders" | "batches" | "staff" | "exceptions" | "trash" | "audit";
/** Growth and account screens load their own scoped resources. Never prefetch unrelated private modules. */
export function adminResources(
  path: string,
  capabilities: readonly string[] = [],
): AdminResource[] {
  if (!adminNavAllowed(path, capabilities)) return [];
  const relative = path.slice(ADMIN.length).replace(/^\//, "");
  if (relative === "")
    return capabilities.includes("*") ||
      capabilities.includes("delivery.manage")
      ? ["orders", "batches", "exceptions"]
      : ["orders"];
  if (relative === "staff") return ["staff"];
  if (relative === "audit") return []; // AuditPage owns filtered, paginated requests.
  if (relative === "trash") return ["trash"];
  if (relative === "settings") return ["config"];
  if (relative === "exceptions") return ["exceptions", "orders"];
  if (relative === "batches" || relative.startsWith("batches/"))
    return capabilities.includes("*") || capabilities.includes("staff.manage")
      ? ["orders", "batches", "staff"]
      : ["orders", "batches"];
  if (
    relative === "orders" ||
    relative.startsWith("orders/") ||
    relative.startsWith("print/") ||
    ["print", "binding", "packing"].includes(relative)
  )
    return ["orders", "config"];
  return [];
}
export function adminNavAllowed(
  path: string,
  capabilities: readonly string[],
  isSuperAdmin = false,
) {
  const relative = path.replace(/\/+$/, "").slice(ADMIN.length);
  if (relative.startsWith("/settings/benefits")) return isSuperAdmin;
  if (capabilities.includes("*")) return true;
  const has = (...keys: string[]) =>
    keys.some((key) => capabilities.includes(key));
  if (relative === "/account" || relative === "/qr-tools") return true;
  if (relative.startsWith("/staff")) return has("staff.manage");
  if (relative.startsWith("/payroll")) return has("payroll.manage");
  if (relative.startsWith("/customers")) return has("customer.manage");
  if (relative.startsWith("/settings/mail")) return has("mail.manage");
  if (
    relative === "/settings/promotions" ||
    relative === "/settings/volume-discounts" ||
    relative.startsWith("/promotions/records")
  )
    return has("promotion.manage");
  if (relative === "/settings") return has("config.manage");
  if (relative.startsWith("/after-sales")) return has("payment.manage");
  if (
    relative.startsWith("/print") ||
    relative === "/binding" ||
    relative === "/packing"
  )
    return has("production.manage");
  if (
    relative.startsWith("/batches") ||
    relative === "/slots" ||
    relative === "/map" ||
    relative.startsWith("/exceptions")
  )
    return has("delivery.manage");
  if (relative === "/trash") return has("data.manage");
  if (relative === "/audit") return has("audit.view");
  if (relative.startsWith("/data-operations"))
    return has("export.manage", "customer.manage");
  if (relative === "" || relative.startsWith("/orders"))
    return has(
      "orders.view",
      "orders.manage",
      "payment.manage",
      "production.manage",
      "delivery.manage",
      "customer.manage",
    );
  return false;
}
