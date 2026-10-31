import { expect, it } from "vitest";
import { payrollHundredths } from "../src/v1/payroll-api";
it("工资金额元与百分比精确转换为分和bps", () => {
  expect(payrollHundredths("0.29", "金额")).toBe(29);
  expect(payrollHundredths("1.2", "金额")).toBe(120);
  expect(payrollHundredths("12.34", "比例", 10000)).toBe(1234);
  expect(payrollHundredths("100", "比例", 10000)).toBe(10000);
});
it("拒绝空值、负数、超精度及超过100%的比例", () => {
  for (const value of ["", "-1", "1.111", "NaN", "1e5", "100.01"])
    expect(() => payrollHundredths(value, "比例", 10000)).toThrow();
});
