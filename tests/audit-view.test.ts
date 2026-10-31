import { expect, it } from "vitest";
import { auditRow, auditTime } from "../src/admin/audit-view";
it("真实审计DTO不会再丢失操作时间、操作者、对象及请求ID", () => {
  const entry = auditRow({
    id: "log",
    action: "CUSTOMER_LOGIN",
    createdAt: "2026-10-31T01:00:00Z",
    actorId: "account:123",
    actorLabel: "测试客户",
    targetType: "account",
    targetId: "123",
    reason: "test",
    requestId: "request-1",
    data: { changedFields: ["displayName"] },
  });
  expect(entry.createdAt).toBe("2026-10-31T01:00:00Z");
  expect(entry.actorName).toBe("测试客户");
  expect(entry.targetId).toBe("123");
  expect(entry.requestId).toBe("request-1");
  expect(entry.data).toEqual({ changedFields: ["displayName"] });
});
it("兼容演示数据但不伪造缺失的审计事实", () => {
  expect(
    auditRow({ id: "x", time: "old", by: "管理员", target: "订单" }),
  ).toMatchObject({ createdAt: "old", actorName: "管理员", targetId: "订单" });
  expect(auditRow(null).reason).toBe("");
  expect(auditRow(null).data).toEqual({});
  expect(auditTime("bad")).toBe("未记录时间");
});
