export type AuditRow = {
  id: string;
  action: string;
  actorId: string;
  actorName: string;
  createdAt: string;
  targetType: string;
  targetId: string;
  reason: string;
  requestId: string;
  data: Record<string, unknown>;
};
const value = (input: unknown) => (typeof input === "string" ? input : "");
export function auditRow(input: unknown): AuditRow {
  const row = (input && typeof input === "object" ? input : {}) as Record<
    string,
    unknown
  >;
  return {
    id: value(row.id),
    action: value(row.action),
    actorId: value(row.actorId) || value(row.by),
    actorName:
      value(row.actorLabel) ||
      value(row.actorName) ||
      value(row.actorDisplayName) ||
      value(row.by) ||
      value(row.actorId),
    createdAt: value(row.createdAt) || value(row.time),
    targetType: value(row.targetType),
    targetId: value(row.targetId) || value(row.target),
    reason: value(row.reason),
    requestId: value(row.requestId),
    data:
      row.data && typeof row.data === "object" && !Array.isArray(row.data)
        ? (row.data as Record<string, unknown>)
        : {},
  };
}
export const auditActionNames: Record<string, string> = {
  CUSTOMER_LOGIN: "客户登录",
  CUSTOMER_LOGOUT: "客户退出",
  CUSTOMER_REGISTER: "客户注册",
  CUSTOMER_PROFILE_UPDATED: "修改客户资料",
  CUSTOMER_PASSWORD_CHANGED: "修改账号密码",
  CUSTOMER_DELETION_REQUESTED: "申请注销账号",
  CUSTOMER_DELETION_RECOVERED: "恢复注销账号",
  INITIAL_ADMIN_CREATED: "初始化超级管理员",
  STAFF_CREATED: "授予员工权限",
  STAFF_UPDATED: "修改员工权限",
  CONFIG_UPDATED: "更新系统配置",
  ORDER_CREATED: "创建订单",
  ORDER_CANCELLED: "撤销订单",
  PAYMENT_CONFIRMED: "确认收款",
  ORDER_DELIVERED: "完成交付",
  STAFF_LOGIN: "员工进入工作台",
};
export function auditTime(time: string) {
  const date = new Date(time);
  return Number.isFinite(date.getTime())
    ? date.toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false })
    : "未记录时间";
}
