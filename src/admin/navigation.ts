import { matchPath } from "react-router-dom";
import {
  CalendarDays,
  ChartNoAxesCombined,
  ClipboardList,
  FileCheck2,
  Files,
  Layers,
  Map,
  Package,
  Printer,
  ScrollText,
  Settings,
  ShoppingBag,
  Trash2,
  Truck,
  User,
  Users,
  AlertTriangle,
  type LucideIcon,
} from "lucide-react";
import { ADMIN } from "../lib/api";
import { adminNavAllowed } from "./resources";
export type AdminNavItem = {
  label: string;
  to: string;
  icon: LucideIcon;
  end: boolean;
};
export type AdminNavGroup = {
  id: string;
  label: string;
  icon: LucideIcon;
  items: readonly AdminNavItem[];
};
const item = (
  label: string,
  path: string,
  icon: LucideIcon,
  end = false,
): AdminNavItem => ({
  label,
  to: path ? `${ADMIN}/${path}` : ADMIN,
  icon,
  end,
});
export const adminHome = item("工作台", "", ChartNoAxesCombined, true);
export const adminAccount = item("个人账号", "account", User);
export const adminNavGroups: readonly AdminNavGroup[] = [
  {
    id: "orders",
    label: "订单与生产",
    icon: ClipboardList,
    items: [
      item("订单管理", "orders", ClipboardList),
      item("售后与退款", "after-sales", ClipboardList),
      item("打印中心", "print", Printer),
      item("装订中心", "binding", Files),
      item("装袋中心", "packing", Package),
      item("异常中心", "exceptions", AlertTriangle),
    ],
  },
  {
    id: "delivery",
    label: "配送管理",
    icon: Truck,
    items: [
      item("配送调度", "batches", Truck),
      item("配送时段", "slots", CalendarDays),
      item("校园地图", "map", Map),
    ],
  },
  {
    id: "customers",
    label: "客户与优惠",
    icon: Users,
    items: [
      item("客户管理", "customers", Users),
      item("优惠活动", "settings/promotions", ShoppingBag),
      item("满量折扣", "settings/volume-discounts", Layers),
      item("优惠记录", "promotions/records", ScrollText),
    ],
  },
  {
    id: "system",
    label: "系统与团队",
    icon: Settings,
    items: [
      item("系统配置", "settings", Settings, true),
      item("员工管理", "staff", Users),
      item("员工工资", "payroll", ScrollText),
      item("员工折扣", "settings/benefits", ShoppingBag),
      item("链接二维码", "qr-tools", Files),
      item("邮件配置", "settings/mail", Settings),
      item("邮件模板", "settings/mail-templates", FileCheck2),
      item("操作审计", "audit", ScrollText),
      item("数据与导出", "data-operations", Files),
      item("回收站", "trash", Trash2),
    ],
  },
];
export const adminNavigationItems = [
  adminHome,
  ...adminNavGroups.flatMap((group) => group.items),
  adminAccount,
];
export function activeAdminItem(pathname: string) {
  return adminNavigationItems
    .filter((item) => matchPath({ path: item.to, end: item.end }, pathname))
    .sort((a, b) => b.to.length - a.to.length)[0];
}
export function activeAdminGroup(pathname: string) {
  const current = activeAdminItem(pathname);
  return adminNavGroups.find((group) =>
    group.items.some((item) => item.to === current?.to),
  );
}
export function visibleAdminGroups(
  capabilities: readonly string[],
  mustChangePassword = false,
  isSuperAdmin = false,
) {
  if (mustChangePassword) return [];
  return adminNavGroups
    .map((group) => ({
      ...group,
      items: group.items.filter((item) =>
        adminNavAllowed(item.to, capabilities, isSuperAdmin),
      ),
    }))
    .filter((group) => group.items.length > 0);
}
