import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  AdminBenefitsPage,
  StaffBenefitSummary,
  benefitsPatchPayload,
  canManageBenefits,
  formatBenefitPercent,
  gradePercentLabel,
  gradePercentText,
  normalizeBenefitGrades,
  parseGradePercentText,
  staffBenefitView,
} from "../src/v1/benefits";

const account = { id: "acct-1", verified: true, status: "ACTIVE" };
const staffSession = {
  id: "staff-1",
  accountId: "acct-1",
  discountLevel: 3,
  isSuperAdmin: false,
  role: "ADMIN",
  capabilities: ["*"],
};

describe("benefits access", () => {
  it("requires the explicit super-admin flag, not ADMIN or a wildcard capability", () => {
    expect(
      canManageBenefits({
        isSuperAdmin: true,
        role: "ADMIN",
        capabilities: ["*"],
      }),
    ).toBe(true);
    expect(canManageBenefits({ role: "ADMIN", capabilities: ["*"] })).toBe(
      false,
    );
    expect(
      canManageBenefits({
        isSuperAdmin: false,
        role: "ADMIN",
        capabilities: ["*"],
      }),
    ).toBe(false);
    expect(canManageBenefits(null)).toBe(false);
  });
});

describe("benefits grade percents", () => {
  it("keeps the difference between an unconfigured grade and an explicit zero", () => {
    expect(parseGradePercentText("")).toBeNull();
    expect(parseGradePercentText("   ")).toBeNull();
    expect(parseGradePercentText("0")).toBe(0);
    expect(parseGradePercentText("20")).toBe(2000);
    expect(parseGradePercentText("33.33")).toBe(3333);
    expect(gradePercentText(null)).toBe("");
    expect(gradePercentText(0)).toBe("0");
    expect(normalizeBenefitGrades([2000, null, 0, 10000])).toEqual([
      2000,
      null,
      0,
      10000,
      null,
      null,
    ]);
    expect(normalizeBenefitGrades([0])).toEqual([
      0,
      null,
      null,
      null,
      null,
      null,
    ]);
  });

  it("rejects values outside the percent/basis-point range", () => {
    expect(() => parseGradePercentText("-1")).toThrow();
    expect(() => parseGradePercentText("100.01")).toThrow();
    expect(() => normalizeBenefitGrades([1, 2, 3, 4, 5, 6, 7])).toThrow();
    expect(() => normalizeBenefitGrades([10001])).toThrow();
  });

  it("spells out the reduction and the payable percentage", () => {
    expect(formatBenefitPercent(2000)).toBe("20");
    expect(gradePercentLabel(2000)).toBe("减少 20% / 实付 80%");
    expect(gradePercentLabel(10000)).toBe("减少 100% / 实付 0%");
    expect(gradePercentLabel(3333)).toBe("减少 33.33% / 实付 66.67%");
    expect(gradePercentLabel(0)).toBe("减少 0% / 实付 100%");
    expect(gradePercentLabel(null)).toContain("未配置");
  });
});

describe("benefits patch", () => {
  it("uses the loaded policy version and preserves configured zeroes", () => {
    expect(
      benefitsPatchPayload(
        7,
        [2000, 0, null, 10000, 1500, 3333],
        "  调整等级  ",
      ),
    ).toEqual({
      expectedVersion: 7,
      grades: [2000, 0, null, 10000, 1500, 3333],
      reason: "调整等级",
    });
  });

  it("rejects missing versions and wrong grade counts", () => {
    expect(() =>
      benefitsPatchPayload(0, [null, null, null, null, null, null]),
    ).toThrow();
    expect(() =>
      benefitsPatchPayload(3, [null, null, null, null, null]),
    ).toThrow();
    expect(() =>
      benefitsPatchPayload(3, [null, null, null, null, null, null, null]),
    ).toThrow();
  });
});

describe("StaffBenefitSummary", () => {
  it("shows the linked employee's own level and no expiry, never capabilities", () => {
    const view = staffBenefitView(staffSession, account, {
      source: "STAFF_GRADE",
      discountLevel: 3,
      reductionBps: 2000,
    });
    expect(view).toMatchObject({
      source: "STAFF_GRADE",
      level: 3,
      title: "员工折扣 · 等级 3",
      rateLabel: "减少 20% / 实付 80%",
    });
    expect(view?.expiryLabel).toContain("无固定到期日");

    const html = renderToStaticMarkup(
      createElement(StaffBenefitSummary, {
        account,
        session: staffSession,
        candidate: {
          source: "STAFF_GRADE",
          discountLevel: 3,
          reductionBps: 2000,
        },
      }),
    );
    expect(html).toContain("员工折扣 · 等级 3");
    expect(html).toContain("减少 20% / 实付 80%");
    expect(html).toContain("无固定到期日");
    expect(html).toContain('data-source="STAFF_GRADE"');
    expect(html).toContain('data-level="3"');
  });

  it("does not display a staff benefit for a guest or a mismatched account", () => {
    expect(staffBenefitView(null, account)).toBeNull();
    expect(staffBenefitView(staffSession, null)).toBeNull();
    expect(
      staffBenefitView(staffSession, {
        id: "someone-else",
        verified: true,
        status: "ACTIVE",
      }),
    ).toBeNull();
    expect(
      staffBenefitView(staffSession, { id: "acct-1", verified: false }),
    ).toBeNull();
    expect(
      staffBenefitView(staffSession, {
        id: "acct-1",
        verified: true,
        status: "SUSPENDED",
      }),
    ).toBeNull();

    const guest = renderToStaticMarkup(
      createElement(StaffBenefitSummary, { account: null, session: null }),
    );
    expect(guest).toBe("");
  });

  it("shows the super-admin benefit only from the exact flag", () => {
    const view = staffBenefitView(
      { accountId: "acct-1", isSuperAdmin: true, capabilities: [] },
      account,
    );
    expect(view).toMatchObject({
      source: "SUPER_ADMIN",
      level: null,
      title: "超级管理员 · 打印免单",
      rateLabel: "减少 100% / 实付 0%",
    });
    const html = renderToStaticMarkup(
      createElement(StaffBenefitSummary, {
        account,
        session: { accountId: "acct-1", isSuperAdmin: true },
      }),
    );
    expect(html).toContain("超级管理员 · 打印免单");
    expect(html).toContain("减少 100% / 实付 0%");
    expect(html).toContain('data-source="SUPER_ADMIN"');
  });

  it("treats a stale session without the new fields as having no benefit", () => {
    expect(
      staffBenefitView({ role: "ADMIN", capabilities: ["*"] }, undefined),
    ).toBeNull();
    expect(
      staffBenefitView(
        { accountId: "acct-1", role: "ADMIN", capabilities: ["*"] },
        account,
      ),
    ).toBeNull();
  });
});

describe("AdminBenefitsPage", () => {
  it("is exported for the root router", () => {
    expect(typeof AdminBenefitsPage).toBe("function");
  });

  it("renders the permission check while auth is pending", () => {
    const html = renderToStaticMarkup(createElement(AdminBenefitsPage));
    expect(html).toContain("员工打印折扣");
    expect(html).toContain("正在确认员工折扣管理权限");
  });
});
