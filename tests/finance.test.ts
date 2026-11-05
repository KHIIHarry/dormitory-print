import { describe, expect, it } from "vitest";
import {
  expensePayload,
  parseExpenseCents,
  shanghaiToday,
} from "../src/admin/finance-api";

describe("finance expense input", () => {
  it("converts decimal yuan to exact positive integer cents", () => {
    expect(parseExpenseCents("12.30")).toBe(1230);
    expect(parseExpenseCents("0.01")).toBe(1);
    for (const input of [
      "",
      "0",
      "-1",
      "1.001",
      "1e2",
      "NaN",
      "1,000",
      "9007199254740991",
    ])
      expect(() => parseExpenseCents(input)).toThrow();
  });
  it("requires whole positive sheets for paper and omits them for other categories", () => {
    const input = {
      category: "PAPER" as const,
      spentOn: "2026-11-05",
      amount: "23.45",
      paperSheets: "500",
      note: "  采购  ",
    };
    expect(expensePayload(input, "2026-11-05")).toEqual({
      category: "PAPER",
      spentOn: "2026-11-05",
      amountCents: 2345,
      paperSheets: 500,
      note: "采购",
    });
    for (const paperSheets of ["", "0", "-1", "1.5", "1e3"])
      expect(() =>
        expensePayload({ ...input, paperSheets }, "2026-11-05"),
      ).toThrow();
    expect(
      expensePayload({ ...input, category: "INK" }, "2026-11-05"),
    ).not.toHaveProperty("paperSheets");
  });
  it("matches server amount, sheet and note limits", () => {
    expect(parseExpenseCents("21474836.47")).toBe(2147483647);
    expect(() => parseExpenseCents("21474836.48")).toThrow();
    const input = {
      category: "PAPER" as const,
      spentOn: "2026-11-05",
      amount: "1",
      paperSheets: "2147483648",
      note: "",
    };
    expect(() => expensePayload(input, "2026-11-05")).toThrow();
    expect(() =>
      expensePayload(
        { ...input, paperSheets: "1", note: "字".repeat(501) },
        "2026-11-05",
      ),
    ).toThrow();
  });
  it("rejects impossible dates and Shanghai future dates", () => {
    const input = {
      category: "OTHER" as const,
      spentOn: "2026-11-06",
      amount: "1",
      paperSheets: "",
      note: "",
    };
    for (const spentOn of ["2026-11-06", "2026-02-30", "", "2026-1-1"])
      expect(() =>
        expensePayload({ ...input, spentOn }, "2026-11-05"),
      ).toThrow();
    expect(shanghaiToday(new Date("2026-11-04T16:01:00Z"))).toBe("2026-11-05");
  });
});
