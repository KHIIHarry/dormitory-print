import { describe, expect, it } from "vitest";
import {
  CAPABILITY_CATALOG,
  actualStartPayload,
  allowedDataOperationTabs,
  eligibleCouriers,
  adminAfterSaleDetailPath,
  afterSaleActionPayload,
  afterSaleReviewPayload,
  afterSaleStatusLabel,
  configSectionPayload,
  mergeConfigSection,
  receiptRefundPayload,
  receiptRefundTotals,
  sectionVersions,
  slotProposalPayload,
  staffCapabilityPayload,
  staffGrantableAccountsPath,
  staffGrantPayload,
  staffUpdatePayload,
  type AdminConfig,
  type AfterSaleCase,
} from "../src/v1/admin-api";

const config = {
  prices: { bwSingle: 20, bwDuplex: 30 },
  maxFiles: 10,
  sections: {
    prices: { version: 4 },
    services: { version: 2 },
  },
} as unknown as AdminConfig;

describe("后台 V1 实接口对齐", () => {
  it("重做完成与重新交付确认必须携带案件版本和原因", () => {
    expect(afterSaleActionPayload({ version: 7 }, "重做已完成")).toEqual({
      expectedVersion: 7,
      reason: "重做已完成",
    });
    expect(() => afterSaleActionPayload({ version: 7 }, "  ")).toThrow(
      "请填写操作原因",
    );
    expect(() => afterSaleActionPayload({ version: 0 }, "ok")).toThrow(
      "售后单版本无效",
    );
  });

  it("管理员售后详情走独立后台路由并编码案件 ID", () => {
    expect(adminAfterSaleDetailPath(" case/1 ")).toBe(
      "/admin/after-sales/case%2F1",
    );
    expect(() => adminAfterSaleDetailPath("  ")).toThrow("售后单 ID");
  });

  it("售后审核仍要求处理方式与客户选择一致且客户退款不可改金额", () => {
    const item = {
      version: 3,
      solution: "REFUND",
      responsibility: "CUSTOMER",
      suggestedCents: 1200,
    } as AfterSaleCase;
    expect(() =>
      afterSaleReviewPayload(item, {
        decision: "APPROVE",
        solution: "REPRINT",
        reason: "改重做",
      }),
    ).toThrow("客户已确认");
    expect(() =>
      afterSaleReviewPayload(item, {
        decision: "APPROVE",
        solution: "REFUND",
        approvedCents: 1000,
        reason: "少退",
      }),
    ).toThrow("不能自定义金额");
    expect(
      afterSaleReviewPayload(item, {
        decision: "APPROVE",
        solution: "REFUND",
        reason: "按建议退款",
      }),
    ).not.toHaveProperty("approvedCents");
  });

  it("未匹配收款退款只接受未匹配记录、正数金额与当前版本", () => {
    const receipt = {
      id: "r1",
      status: "UNMATCHED",
      amountCents: 5000,
      version: 2,
    };
    expect(receiptRefundPayload(receipt, 5000, "客户取消")).toEqual({
      expectedVersion: 2,
      amountCents: 5000,
      reason: "客户取消",
    });
    expect(() => receiptRefundPayload(receipt, 0, "x")).toThrow("正整数分");
    expect(() => receiptRefundPayload(receipt, 5001, "x")).toThrow(
      "剩余可退",
    );
    expect(() =>
      receiptRefundPayload({ ...receipt, status: "MATCHED" }, 100, "x"),
    ).toThrow("已分配收款须走订单售后退款");
    expect(() => receiptRefundPayload(receipt, 100, "  ")).toThrow(
      "请填写退款原因",
    );
  });

  it("未匹配收款使用累计已退/剩余可退，旧数据按实收回退", () => {
    const partial = {
      id: "r2",
      status: "UNMATCHED",
      amountCents: 10000,
      refundedCents: 5000,
      refundableCents: 5000,
      version: 3,
    };
    expect(receiptRefundTotals(partial)).toEqual({
      refundedCents: 5000,
      refundableCents: 5000,
    });
    expect(receiptRefundPayload(partial, 5000, "退剩余")).toEqual({
      expectedVersion: 3,
      amountCents: 5000,
      reason: "退剩余",
    });
    expect(() => receiptRefundPayload(partial, 5001, "x")).toThrow(
      "剩余可退",
    );
    expect(
      receiptRefundTotals({
        amountCents: 10000,
        refundedCents: 5000,
        refundableCents: 0,
      }),
    ).toEqual({ refundedCents: 5000, refundableCents: 0 });
    expect(() =>
      receiptRefundPayload(
        { ...partial, refundableCents: 0, refundedCents: 10000 },
        100,
        "x",
      ),
    ).toThrow("已无可退");
    expect(receiptRefundTotals({ amountCents: 5000 })).toEqual({
      refundedCents: 0,
      refundableCents: 5000,
    });
  });

  it("时段建议只写建议，绝不替客户确认", () => {
    const order = { id: "o1", status: "PENDING_CONFIRMATION", version: 5 };
    const body = slotProposalPayload(order, "slot-1", "客户改期");
    expect(body).toEqual({
      expectedVersion: 5,
      slotId: "slot-1",
      reason: "客户改期",
    });
    expect(body).not.toHaveProperty("confirmed");
    expect(() =>
      slotProposalPayload({ ...order, status: "PAID" }, "slot-1", "x"),
    ).toThrow("仅待结算订单");
    expect(() => slotProposalPayload(order, " ", "x")).toThrow("请选择");
    expect(() => slotProposalPayload(order, "slot-1", " ")).toThrow(
      "请填写时段变更原因",
    );
  });

  it("实际开印使用真实订单版本，说明可空但版本不可缺", () => {
    expect(actualStartPayload(9, " 开始 ")).toEqual({
      expectedVersion: 9,
      reason: "开始",
    });
    expect(actualStartPayload(9, "")).toEqual({ expectedVersion: 9, reason: "" });
    expect(() => actualStartPayload(0, "")).toThrow("订单版本尚未加载");
  });

  it("能力目录只暴露后端真实校验的键", () => {
    const keys = CAPABILITY_CATALOG.map((item) => item.key);
    expect(keys).toEqual([
      "*",
      "config.manage",
      "staff.manage",
      "customer.manage",
      "mail.manage",
      "promotion.manage",
      "order.access-link.manage",
      "orders.manage",
      "orders.view",
      "payment.manage",
      "production.manage",
      "payroll.manage",
      "delivery.manage",
      "export.manage",
      "data.manage",
      "audit.view",
    ]);
    expect(keys).not.toContain("finance.manage");
    expect(
      CAPABILITY_CATALOG.find((item) => item.key === "delivery.manage")?.enforced,
    ).toBe(true);
    expect(
      staffCapabilityPayload(
        { id: "s1", version: 2, role: "ADMIN" },
        [
          "delivery.manage",
          "production.manage",
          "payroll.manage",
          "data.manage",
          "audit.view",
        ],
        "补配送、生产、清理与审计权限",
      ),
    ).toEqual({
      expectedVersion: 2,
      capabilities: [
        "delivery.manage",
        "production.manage",
        "payroll.manage",
        "data.manage",
        "audit.view",
      ],
      reason: "补配送、生产、清理与审计权限",
    });
    expect(() =>
      staffCapabilityPayload(
        { id: "s1", version: 2, role: "ADMIN" },
        ["finance.manage"],
        "x",
      ),
    ).toThrow("尚未执行");
    expect(
      CAPABILITY_CATALOG.find((item) => item.key === "customer.manage")
        ?.description,
    ).toContain("注销");
    expect(
      CAPABILITY_CATALOG.find((item) => item.key === "delivery.manage")
        ?.description,
    ).toContain("发车");
  });

  it("员工授权只绑定客户账号且不自动赠送全部管理员能力", () => {
    const account = { id: "acc-1", verified: true, status: "ACTIVE" };
    const body = staffGrantPayload({
      account,
      name: "前台",
      username: "frontdesk",
      phone: "13800000000",
      role: "ADMIN",
      capabilities: ["orders.manage", "payment.manage"],
      discountLevel: 3,
      reason: "新增前台管理员",
    });
    expect(body).toMatchObject({
      accountId: "acc-1",
      role: "ADMIN",
      capabilities: ["orders.manage", "payment.manage"],
      discountLevel: 3,
      reason: "新增前台管理员",
    });
    expect(body).not.toHaveProperty("email");
    expect(body).not.toHaveProperty("password");
    expect(body).not.toHaveProperty("status");
    expect(
      staffGrantPayload({ account, role: "ADMIN", reason: "普通管理员" })
        .capabilities,
    ).toEqual([]);
    expect(
      staffGrantPayload({
        account: {
          id: "acc-minimal",
          displayName: "最小候选",
          emailMasked: "m***@example.com",
          verified: true,
        },
        role: "ADMIN",
        reason: "最小候选授权",
      }),
    ).toMatchObject({ accountId: "acc-minimal", capabilities: [] });
    expect(
      staffGrantPayload({
        account,
        role: "ADMIN",
        isSuperAdmin: true,
        capabilities: ["orders.manage"],
        reason: "指定超级管理员",
      }).capabilities,
    ).toEqual(["*"]);
    expect(() =>
      staffGrantPayload({
        account: { ...account, verified: false },
        role: "COURIER",
        reason: "x",
      }),
    ).toThrow("已验证");
    expect(() =>
      staffGrantPayload({
        account,
        role: "COURIER",
        capabilities: ["orders.manage"],
        reason: "x",
      }),
    ).toThrow("配送员");
    expect(() =>
      staffGrantPayload({
        account,
        role: "COURIER",
        isSuperAdmin: true,
        reason: "x",
      }),
    ).toThrow("只有管理员");
    expect(() =>
      staffGrantPayload({ account, role: "ADMIN", discountLevel: 7, reason: "x" }),
    ).toThrow("1 到 6");
  });

  it("员工资料保存不会因兼容分支自动变成完整管理员", () => {
    const target = {
      id: "staff-2",
      version: 4,
      role: "ADMIN" as const,
      capabilities: ["orders.manage"],
      isSuperAdmin: false,
    };
    const body = staffUpdatePayload({
      actorId: "staff-1",
      target,
      name: "前台",
      username: "frontdesk",
      phone: "",
      role: "ADMIN",
      enabled: true,
      status: "ACTIVE",
      discountLevel: 2,
      isSuperAdmin: false,
      reason: "调整资料",
    });
    expect(body).toMatchObject({
      expectedVersion: 4,
      role: "ADMIN",
      capabilities: ["orders.manage"],
      discountLevel: 2,
      reason: "调整资料",
    });
    expect(
      staffUpdatePayload({
        actorId: "staff-1",
        target: { ...target, capabilities: [] },
        name: "前台",
        username: "frontdesk",
        phone: "",
        role: "ADMIN",
        enabled: true,
        status: "ACTIVE",
        isSuperAdmin: false,
        reason: "调整资料",
      }).capabilities,
    ).toEqual([]);
    const promoted = staffUpdatePayload({
      actorId: "staff-1",
      target: { ...target, role: "COURIER", capabilities: [] },
      name: "配送",
      username: "courier",
      phone: "",
      role: "ADMIN",
      enabled: true,
      status: "ACTIVE",
      isSuperAdmin: false,
      reason: "转为管理员",
    });
    expect(promoted.capabilities).toEqual([]);
    const self = staffUpdatePayload({
      actorId: "staff-2",
      target,
      name: "前台",
      username: "frontdesk",
      phone: "",
      role: "COURIER",
      enabled: true,
      status: "ACTIVE",
      reason: "自改资料",
    });
    expect(self).not.toHaveProperty("role");
    expect(self).not.toHaveProperty("capabilities");
    expect(self).not.toHaveProperty("discountLevel");
  });

  it("配置分区响应按真实结构合并，价格不会写回顶层", () => {
    const merged = mergeConfigSection(config, "prices", {
      version: 5,
      bwSingle: 25,
      bwDuplex: 35,
    });
    expect(merged).not.toHaveProperty("bwSingle");
    expect(merged.prices).toEqual({ bwSingle: 25, bwDuplex: 35 });
    expect(merged.maxFiles).toBe(10);
    expect((merged as Record<string, any>).version).toBeUndefined();
    const services = mergeConfigSection(config, "services", {
      version: 3,
      maxFiles: 20,
    });
    expect(services.maxFiles).toBe(20);
    expect(services.prices).toEqual(config.prices);
    expect(config.prices).toEqual({ bwSingle: 20, bwDuplex: 30 });
  });

  it("照片分区补丁同时携带 A4 最小留白与服务端版本", () => {
    const photo = configSectionPayload(
      "photo",
      {
        ...config,
        photoWidthMm: 127,
        photoHeightMm: 89,
        margins: { top: 3, right: 3, bottom: 3, left: 3 },
        a4Margins: { top: 4, right: 5, bottom: 6, left: 7 },
      } as unknown as AdminConfig,
      9,
    );
    expect(photo).toEqual({
      expectedVersion: 9,
      photoWidthMm: 127,
      photoHeightMm: 89,
      margins: { top: 3, right: 3, bottom: 3, left: 3 },
      a4Margins: { top: 4, right: 5, bottom: 6, left: 7 },
    });
  });

  it("版本表与状态文案使用后端真实状态", () => {
    expect(sectionVersions(config)).toEqual({
      prices: { version: 4 },
      services: { version: 2 },
    });
    expect(afterSaleStatusLabel("REPRINT_READY")).toContain("重新交付");
    expect(afterSaleStatusLabel("REFUND_APPROVED")).toContain("退款");
    expect(afterSaleStatusLabel("COMPLETED")).toBe("已完成");
  });
});

describe("配送候选目录", () => {
  it("包含在职配送员与持配送权限的管理员，排除不可用账号", () => {
    const base = {
      name: "候选",
      enabled: true,
      status: "ACTIVE",
      mustChangePassword: false,
    };
    const rows = [
      { ...base, id: "courier", role: "COURIER", capabilities: [] },
      {
        ...base,
        id: "admin-delivery",
        role: "ADMIN",
        capabilities: ["delivery.manage"],
      },
      { ...base, id: "admin-all", role: "ADMIN", capabilities: ["*"] },
      { ...base, id: "admin-none", role: "ADMIN", capabilities: [] },
      {
        ...base,
        id: "admin-disabled",
        role: "ADMIN",
        capabilities: ["delivery.manage"],
        enabled: false,
      },
      {
        ...base,
        id: "admin-inactive",
        role: "ADMIN",
        capabilities: ["delivery.manage"],
        status: "DISABLED",
      },
      {
        ...base,
        id: "admin-must-change",
        role: "ADMIN",
        capabilities: ["delivery.manage"],
        mustChangePassword: true,
      },
      { ...base, id: "manager", role: "MANAGER", capabilities: ["*"] },
    ];
    expect(eligibleCouriers(rows).map((row) => row.id)).toEqual([
      "courier",
      "admin-delivery",
      "admin-all",
    ]);
  });
});

describe("数据与合规操作能力拆分", () => {
  it("只开放当前能力对应的标签页，不越权加载", () => {
    expect(allowedDataOperationTabs([])).toEqual([]);
    expect(allowedDataOperationTabs(["customer.manage"])).toEqual(["deletion"]);
    expect(allowedDataOperationTabs(["export.manage"])).toEqual(["exports"]);
    expect(allowedDataOperationTabs(["data.manage"])).toEqual(["trash"]);
    expect(
      allowedDataOperationTabs(["customer.manage", "export.manage"]),
    ).toEqual(["deletion", "exports"]);
    expect(allowedDataOperationTabs(["*"])).toEqual([
      "deletion",
      "trash",
      "exports",
    ]);
  });
});

describe("员工授权候选账号最小检索", () => {
  it("使用 staff.manage 端点并限制关键词与数量", () => {
    expect(staffGrantableAccountsPath("  alice@example.com  ")).toBe(
      "/admin/staff/grantable-accounts?q=alice%40example.com&limit=20",
    );
    expect(staffGrantableAccountsPath("张三四", 5)).toBe(
      "/admin/staff/grantable-accounts?q=%E5%BC%A0%E4%B8%89%E5%9B%9B&limit=5",
    );
    expect(() => staffGrantableAccountsPath("ab")).toThrow("至少 3 个字符");
    expect(() => staffGrantableAccountsPath("abc", 0)).toThrow("1 到 20");
    expect(() => staffGrantableAccountsPath("abc", 21)).toThrow("1 到 20");
  });
});
