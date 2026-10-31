import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { ApiError } from "../src/lib/api";
import {
  adminChargesPath,
  amountError,
  cancelPath,
  chargeDraftError,
  createCharge,
  customerChargesPath,
  customerDisputePath,
  isReceiptOvershoot,
  reasonError,
  receiptOvershootRemainingCents,
  receiptPath,
  refundPath,
  resolveDisputePath,
  yuanToCents,
} from "../src/v1/supplementary-api";

const component = readFileSync("src/v1/supplementary.tsx", "utf8");
const css = readFileSync("src/v1/supplementary.css", "utf8");

describe("补充费用前端接口", () => {
  it("使用订单详情与独立收款/退款/争议路径，并编码标识", () => {
    expect(adminChargesPath("o 1")).toBe(
      "/admin/orders/o%201/supplementary-charges",
    );
    expect(customerChargesPath("o 1")).toBe(
      "/orders/o%201/supplementary-charges",
    );
    expect(receiptPath("c/1")).toBe(
      "/admin/supplementary-charges/c%2F1/receipts",
    );
    expect(refundPath("c1")).toBe("/admin/supplementary-charges/c1/refunds");
    expect(cancelPath("c1")).toBe("/admin/supplementary-charges/c1/cancel");
    expect(customerDisputePath("o1", "c1")).toBe(
      "/orders/o1/supplementary-charges/c1/disputes",
    );
    expect(resolveDisputePath("c1", "d/1")).toBe(
      "/admin/supplementary-charges/c1/disputes/d%2F1/resolve",
    );
    expect(typeof createCharge).toBe("function");
  });

  it("按分解析元金额，拒绝超过两位小数、负数与空值", () => {
    expect(yuanToCents("12.34")).toBe(1234);
    expect(yuanToCents("0.1")).toBe(10);
    expect(yuanToCents("10")).toBe(1000);
    expect(yuanToCents("0")).toBe(0);
    expect(yuanToCents("")).toBeNull();
    expect(yuanToCents("-1")).toBeNull();
    expect(yuanToCents("12.345")).toBeNull();
    expect(yuanToCents("1,000")).toBeNull();
  });

  it("强制填写用途、正金额和原因，并限制退款不超过可退金额", () => {
    expect(chargeDraftError({ purpose: "  ", amount: "10" })).toContain("用途");
    expect(chargeDraftError({ purpose: "装订", amount: "0" })).toContain(
      "大于 0",
    );
    expect(chargeDraftError({ purpose: "装订", amount: "12.345" })).toContain(
      "大于 0",
    );
    expect(reasonError("  ")).toContain("原因");
    expect(amountError("30", 2000, "退款金额")).toContain("不能超过 20.00");
    expect(amountError("20", 2000, "退款金额")).toBe("");
  });

  it("只把服务端明确超收的 422 识别为未登记，并安全读取剩余应收", () => {
    const overshoot = new ApiError(
      "RECEIPT_EXCEEDS_OUTSTANDING",
      "收款金额超过剩余应收金额",
      422,
      { remainingCents: 700 },
    );
    expect(isReceiptOvershoot(overshoot)).toBe(true);
    expect(receiptOvershootRemainingCents(overshoot)).toBe(700);
    expect(isReceiptOvershoot(new ApiError("OTHER", "其他错误", 422))).toBe(
      false,
    );
    expect(isReceiptOvershoot(new Error("network"))).toBe(false);
    for (const details of [
      null,
      { remainingCents: -1 },
      { remainingCents: 7.5 },
      { remainingCents: "700" },
      { other: 1 },
    ]) {
      expect(
        receiptOvershootRemainingCents(
          new ApiError("RECEIPT_EXCEEDS_OUTSTANDING", "超收", 422, details),
        ),
      ).toBeNull();
    }
  });
});

describe("补充费用组件", () => {
  it("导出管理员与客户面板，并保留管理员固定工资提示", () => {
    expect(component).toMatch(
      /export function AdminSupplementaryPanel\(\{\s*orderId\s*\}/,
    );
    expect(component).toMatch(
      /export function CustomerSupplementaryPanel\(\{\s*orderId,\s*canDispute = false,\s*\}/,
    );
    for (const text of [
      "费用用途（客户可见）",
      "金额（元）",
      "固定工资不受影响",
      "计入百分比工资",
      "登记收款",
      "登记退款",
      "取消费用",
      "处理异议",
    ])
      expect(component).toContain(text);
  });

  it("管理员展示费用、实收、退款、净收与待收的独立聚合", () => {
    for (const text of ["最终费用", "累计已收", "累计已退", "净收", "待收"])
      expect(component).toContain(text);
    for (const className of [
      ".supp-summary",
      ".supp-metrics",
      ".supp-movements",
      ".supp-disputes",
    ])
      expect(css).toContain(className);
  });

  it("客户可以查看费用并提交异议，但不暴露工资与经办人内部字段", () => {
    const customer = component.slice(
      component.indexOf("export function CustomerSupplementaryPanel"),
    );
    expect(customer).toContain("对费用有异议");
    expect(customer).toContain("异议原因");
    expect(customer).toContain("待支付");
    expect(customer).not.toContain("includeInPayroll");
    expect(customer).not.toContain("createdBy");
    expect(customer).not.toContain("固定工资");
  });

  it("客户异议写入口默认关闭，只有显式 canDispute 才显示", () => {
    const customer = component.slice(
      component.indexOf("export function CustomerSupplementaryPanel"),
    );
    expect(customer).toContain("canDispute = false");
    expect(customer).toContain("canDispute?: boolean");
    expect(customer).toContain(
      '{canDispute && charge.status === "ACTIVE" && (',
    );
    expect(customer).toContain("{canDispute && disputeFor && (");
    expect(customer).toContain("if (!canDispute) return;");
    expect(customer).toContain("if (!canDispute || !disputeFor) return;");
    expect(customer).not.toContain('{charge.status === "ACTIVE" && (');
  });

  it("补充费用收款超额明确未入账，并提供未匹配收款入口", () => {
    expect(component).toContain("这笔补充费用收款未登记");
    expect(component).toContain("本次补充费用入账为 ¥0.00");
    expect(component).toContain("不会记入补充费用");
    expect(component).toContain("打开该订单的未匹配收款");
    expect(component).toContain(
      "${ADMIN}/after-sales?tab=receipts&orderId=${encodeURIComponent(orderId)}",
    );
    expect(css).toContain(".supp-overshoot");
  });

  it("写操作都携带 expectedVersion，创建金额与工资标记只提交一次", () => {
    expect(component).toContain("expectedVersion:");
    expect(component).toContain("includeInPayroll,");
    expect(component).not.toMatch(/updateCharge|editCharge|changeAmount/);
  });
});
