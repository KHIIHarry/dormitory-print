import { useQuery } from "@tanstack/react-query";
import { Bell } from "lucide-react";
import { NavLink } from "react-router-dom";
import { growthRequest } from "../growth/client";
import { useIdentity } from "../growth/identity";
import { ADMIN, DEMO } from "../lib/api";
import "./notifications.css";

export const notificationsEndpoint = "/admin/notifications";
export const notificationsPath = `${ADMIN}/notifications`;
export type UnreadNotifications = { unreadCount: number };

export function unreadNotificationsKey(generation: number, staffId?: string) {
  return ["admin-notification-unread", generation, staffId] as const;
}

export function AdminNotificationBell() {
  const { generation, staff } = useIdentity();
  const allowed = staff?.role === "ADMIN" && !staff.mustChangePassword;
  const unread = useQuery({
    queryKey: unreadNotificationsKey(generation, staff?.id),
    queryFn: ({ signal }) =>
      growthRequest<UnreadNotifications>(`${notificationsEndpoint}/unread`, {
        signal,
      }),
    enabled: allowed && !DEMO,
    refetchInterval: 30_000,
    retry: false,
  });
  if (!allowed) return null;
  const count = unread.data?.unreadCount ?? 0;
  return (
    <NavLink
      to={notificationsPath}
      className="icon-btn admin-notification-bell"
      aria-label={count ? `通知中心，${count} 条未读` : "通知中心"}
      title="通知中心"
    >
      <Bell size={20} aria-hidden="true" />
      {count > 0 && (
        <span className="notification-count" aria-hidden="true">
          {count > 9 ? "9+" : count}
        </span>
      )}
    </NavLink>
  );
}
