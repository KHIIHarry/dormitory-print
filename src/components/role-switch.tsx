import { Link, useLocation } from "react-router-dom";
import { LayoutDashboard, Truck, UserRound } from "lucide-react";
import { ADMIN } from "../lib/api";
import { normalizeAdminBase } from "../lib/pwa";
import { adminNavAllowed } from "../admin/resources";
import { adminHome, visibleAdminGroups } from "../admin/navigation";
import type { StaffSession } from "../growth/types";
import { useIdentity } from "../growth/identity";
import { Notice, Panel } from "./ui";

export type WorkspaceKey = "admin" | "courier";

export type WorkspaceEntry = {
  key: WorkspaceKey;
  label: string;
  to: string;
  description: string;
};

/**
 * Courier access is role based, and for admins it also requires the server
 * delivery capability. A mail-only admin must not see or enter the courier
 * workspace merely because their role is ADMIN.
 */
export function courierWorkspaceAllowed(
  staff: StaffSession | null | undefined,
): boolean {
  if (!staff) return false;
  if (staff.role === "COURIER") return true;
  if (staff.role !== "ADMIN") return false;
  const capabilities = staff.capabilities ?? [];
  return capabilities.includes("*") || capabilities.includes("delivery.manage");
}

/**
 * Pick the first page an admin may actually open. The dashboard is preferred
 * only when the server-issued capabilities allow it; otherwise the first
 * permitted operational item (for example mail management) or the always-safe
 * personal account page is used. This prevents a limited admin from landing on
 * a default-deny dashboard that would immediately show empty/unauthorized data.
 */
export function adminWorkspaceTarget(
  staff: StaffSession,
  adminRoot: string,
): string {
  if (staff.mustChangePassword) return `${adminRoot}/account`;
  const capabilities = staff.capabilities ?? [];
  const isSuperAdmin = staff.isSuperAdmin === true;
  if (adminNavAllowed(adminHome.to, capabilities, isSuperAdmin))
    return `${adminRoot}/`;
  const first = visibleAdminGroups(capabilities, false, isSuperAdmin)
    .flatMap((group) => group.items)
    .find((item) => {
      const relative = item.to.startsWith(ADMIN)
        ? item.to.slice(ADMIN.length)
        : item.to;
      // qr-tools is a utility open to every admin, not an operational landing.
      return relative !== "/qr-tools";
    });
  if (!first) return `${adminRoot}/account`;
  const suffix = first.to.startsWith(ADMIN) ? first.to.slice(ADMIN.length) : "";
  return `${adminRoot}${suffix}`;
}

/**
 * Workspace entry is derived from the effective staff session returned by the
 * server. A courier can never be shown the admin workspace, regardless of any
 * capability claims in the current view. Admin landing targets are capability
 * aware so a limited employee enters a page they can actually use.
 */
export function workspaceEntries(
  staff: StaffSession | null | undefined,
  adminBase: string = ADMIN,
): WorkspaceEntry[] {
  if (!staff) return [];
  const adminRoot = normalizeAdminBase(adminBase);
  const entries: WorkspaceEntry[] = [];
  if (staff.role === "ADMIN") {
    entries.push({
      key: "admin",
      label: "管理端",
      to: adminWorkspaceTarget(staff, adminRoot),
      description: "进入有权限的管理工作台",
    });
  }
  if (courierWorkspaceAllowed(staff)) {
    entries.push({
      key: "courier",
      label: "配送端",
      to: "/courier/",
      description: "查看配送任务与完成交付",
    });
  }
  return entries;
}

/**
 * Only same-origin workspace paths may be used as a post-login return target.
 * Login routes are rejected to prevent a redirect loop; customer, external,
 * protocol-relative and hash targets are rejected as well.
 */
export function safeStaffReturn(
  value: string | null | undefined,
  adminBase: string = ADMIN,
): string | null {
  if (!value || !value.startsWith("/") || value.startsWith("//")) return null;
  if (/[\\\r\n#]/.test(value)) return null;
  const adminRoot = normalizeAdminBase(adminBase);
  const path = value.split("?")[0];
  let decodedPath = path;
  try {
    decodedPath = decodeURIComponent(path);
  } catch {
    return null;
  }
  if (/[\\\r\n#]/.test(decodedPath)) return null;
  if (
    decodedPath
      .split("/")
      .some((segment) => segment === ".." || segment === ".")
  )
    return null;
  if (path === `${adminRoot}/login` || path === "/courier/login") return null;
  if (
    path === adminRoot ||
    path.startsWith(`${adminRoot}/`) ||
    path === "/courier" ||
    path.startsWith("/courier/")
  )
    return value;
  return null;
}

export function customerLoginPath(
  returnTo?: string | null,
  adminBase: string = ADMIN,
): string {
  const safe = safeStaffReturn(returnTo, adminBase);
  return safe
    ? `/u/account/login?returnTo=${encodeURIComponent(safe)}`
    : "/u/account/login";
}

function WorkspaceIcon({ entry }: { entry: WorkspaceEntry }) {
  return entry.key === "admin" ? (
    <LayoutDashboard size={16} aria-hidden="true" />
  ) : (
    <Truck size={16} aria-hidden="true" />
  );
}

/**
 * Personal-page staff section. It is absent for ordinary customers and for
 * staff without a workspace role: there is no standalone employee login here.
 */
export function StaffPortalPanel() {
  const { staff } = useIdentity();
  const entries = workspaceEntries(staff);
  if (!staff || !entries.length) return null;

  return (
    <Panel
      className="staff-portal"
      title="员工入口"
      action={<span className="staff-portal-status">员工身份</span>}
    >
      <div className="staff-portal-identity">
        <span className="staff-portal-avatar" aria-hidden="true">
          {staff.name.slice(0, 1)}
        </span>
        <div>
          <strong>{staff.name}</strong>
          <small>
            <span>
              {staff.isSuperAdmin
                ? "超级管理员"
                : staff.role === "ADMIN"
                  ? "管理员"
                  : "配送员"}
            </span>
            <span aria-hidden="true">·</span>
            <span className="staff-portal-username">{staff.username}</span>
          </small>
        </div>
      </div>
      <p className="staff-portal-note">
        工作台权限由当前账号在服务端生效的员工角色决定；进入工作台不会退出客户账号。
      </p>
      <div className="staff-portal-actions">
        {entries.map((entry) => (
          <Link className="btn secondary" key={entry.key} to={entry.to}>
            <WorkspaceIcon entry={entry} />
            进入{entry.label}
          </Link>
        ))}
      </div>
    </Panel>
  );
}

/**
 * Rendered on the personal page when the signed-in account cannot enter the
 * requested workspace. This is a safe dead end, not a second employee login.
 */
export function StaffAccessNotice() {
  const location = useLocation();
  const state = location.state as { staffError?: unknown } | null;
  if (state?.staffError !== "forbidden") return null;
  return (
    <Notice type="warning">
      当前账号没有访问该工作台的权限。你仍可使用客户账号继续打印或查看订单。
    </Notice>
  );
}

/** Cross-workspace link shown only when the authenticated role permits it. */
export function WorkspaceSwitch({
  current,
  className = "",
}: {
  current: WorkspaceKey;
  className?: string;
}) {
  const { staff } = useIdentity();
  const visible = workspaceEntries(staff).filter(
    (entry) => entry.key !== current,
  );
  if (!visible.length) return null;
  return (
    <nav
      className={`workspace-switch ${className}`.trim()}
      aria-label="切换工作台"
    >
      {visible.map((entry) => (
        <Link
          className="workspace-switch-link"
          key={entry.key}
          to={entry.to}
          aria-label={entry.label}
          title={entry.description}
        >
          <WorkspaceIcon entry={entry} />
          <span>{entry.label}</span>
        </Link>
      ))}
    </nav>
  );
}

/** Shared shell affordance on every staff workspace. */
export function ReturnToCustomerLink({
  className = "",
}: {
  className?: string;
}) {
  return (
    <Link
      className={`return-customer ${className}`.trim()}
      to="/u/account"
      aria-label="返回用户端"
    >
      <UserRound size={15} aria-hidden="true" />
      <span>返回用户端</span>
    </Link>
  );
}
