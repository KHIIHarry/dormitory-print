import { describe, it, expect } from "vitest";
import { ADMIN } from "../src/lib/api";
import {
  adminNavigationItems,
  adminNavGroups,
  activeAdminItem,
  activeAdminGroup,
  visibleAdminGroups,
} from "../src/admin/navigation";
describe("后台分级导航", () => {
  it("保留原有入口并纳入售后与数据导出，各功能只出现一次", () => {
    expect(adminNavigationItems).toHaveLength(25);
    expect(new Set(adminNavigationItems.map((item) => item.to)).size).toBe(25);
    expect(adminNavGroups.map((group) => group.label)).toEqual([
      "订单与生产",
      "配送管理",
      "客户与优惠",
      "系统与团队",
    ]);
    expect(adminNavGroups[0].items.map((item) => item.label)).toEqual([
      "订单管理",
      "售后与退款",
      "打印中心",
      "装订中心",
      "装袋中心",
      "异常中心",
    ]);
  });
  it.each([
    ["settings/mail", "邮件配置", "system"],
    ["settings/mail-templates", "邮件模板", "system"],
    ["settings/promotions", "优惠活动", "customers"],
    ["settings/volume-discounts", "满量折扣", "customers"],
    ["settings", "系统配置", "system"],
    ["orders/o-1", "订单管理", "orders"],
    ["print/task-1", "打印中心", "orders"],
    ["batches/b-1", "配送调度", "delivery"],
    ["customers/c-1", "客户管理", "customers"],
  ])("%s 只属于正确二级菜单和分组", (path, label, group) => {
    expect(activeAdminItem(`${ADMIN}/${path}`)?.label).toBe(label);
    expect(activeAdminGroup(`${ADMIN}/${path}`)?.id).toBe(group);
  });
  it("工作台和配置采用精确匹配，详情不抢占其他同名前缀", () => {
    expect(activeAdminItem(ADMIN)?.label).toBe("工作台");
    expect(activeAdminGroup(ADMIN)).toBeUndefined();
    expect(activeAdminItem(`${ADMIN}/settings/`)?.label).toBe("系统配置");
    expect(activeAdminItem(`${ADMIN}/orders-archive`)).toBeUndefined();
    expect(activeAdminItem(`${ADMIN}/settings/unknown`)).toBeUndefined();
  });
  it("隐藏无权限功能和空组，首次改密限制不渲染业务分组", () => {
    const groups = visibleAdminGroups(["mail.manage"]);
    expect(groups.some((group) => group.id === "customers")).toBe(false);
    const system = groups.find((group) => group.id === "system")!;
    expect(system.items.some((item) => item.label === "邮件配置")).toBe(true);
    expect(system.items.some((item) => item.label === "员工管理")).toBe(false);
    expect(visibleAdminGroups(["*"], true)).toEqual([]);
  });
});

it("细分能力仅显示获准管理工作区，邮件管理员看不到生产和调度", () => {
  const mail=visibleAdminGroups(["mail.manage"]).flatMap(group=>group.items.map(item=>item.label));
  expect(mail).toEqual(["链接二维码", "邮件配置", "邮件模板"]);
  const production=visibleAdminGroups(["production.manage"]).flatMap(group=>group.items.map(item=>item.label));
  expect(production).toContain("打印中心");
  expect(production).not.toContain("配送调度");
  expect(production).not.toContain("回收站");
});

it("后台根路径的尾斜线不改变授权，邮件管理员不会误进工作台", async () => {
  const {adminNavAllowed}=await import('../src/admin/resources');
  expect(adminNavAllowed(`${ADMIN}/`,['orders.manage'])).toBe(true);
  expect(adminNavAllowed(`${ADMIN}/`,['mail.manage'])).toBe(false);
});
