import { describe, expect, it } from "vitest";
import {
  shanghaiToday,
  slotDateRange,
  identifySlotDateRange,
  slotDateRangeError,
  formatSlotDate,
} from "../src/lib/slot-date-range";

describe("slot date ranges", () => {
  it("uses the Shanghai calendar date across UTC midnight", () => {
    expect(shanghaiToday(new Date("2026-11-01T16:30:00Z"))).toBe("2026-11-02");
  });
  it.each([
    ["2026-11-06", "2026-11-02", "2026-11-08"],
    ["2026-11-08", "2026-11-02", "2026-11-08"],
    ["2027-01-01", "2026-12-28", "2027-01-03"],
  ])("uses Monday through Sunday for %s", (today, start, end) => {
    expect(slotDateRange("week", today, [])).toEqual({ start, end });
  });
  it("supports leap months and same-day bounds", () => {
    expect(slotDateRange("month", "2028-02-10", [])).toEqual({
      start: "2028-02-01",
      end: "2028-02-29",
    });
    expect(slotDateRange("today", "2026-11-06", [])).toEqual({
      start: "2026-11-06",
      end: "2026-11-06",
    });
  });
  it("derives all bounds from unsorted dates, without inventing dates for an empty list", () => {
    expect(
      slotDateRange("all", "2026-11-06", [
        "2027-01-01",
        "2025-12-31",
        "2026-11-06",
      ]),
    ).toEqual({ start: "2025-12-31", end: "2027-01-01" });
    expect(slotDateRange("all", "2026-11-06", [])).toEqual({
      start: "",
      end: "",
    });
  });
  it.each(["today", "week", "month", "all"] as const)(
    "recognizes a manually selected %s range",
    (preset) => {
      const dates = ["2025-01-01", "2027-12-31"];
      expect(
        identifySlotDateRange(
          slotDateRange(preset, "2026-11-06", dates),
          "2026-11-06",
          dates,
        ),
      ).toBe(preset);
    },
  );
  it("identifies custom and incomplete dates", () => {
    expect(
      identifySlotDateRange(
        { start: "2026-11-03", end: "2026-11-05" },
        "2026-11-06",
        [],
      ),
    ).toBe("custom");
    expect(
      identifySlotDateRange({ start: "", end: "" }, "2026-11-06", []),
    ).toBe("custom");
  });
  it("validates both boundaries, including invalid and reversed dates", () => {
    expect(slotDateRangeError({ start: "", end: "2026-11-06" })).toBe(
      "请选择开始日期",
    );
    expect(slotDateRangeError({ start: "2026-11-06", end: "" })).toBe(
      "请选择结束日期",
    );
    expect(slotDateRangeError({ start: "2026-11-07", end: "2026-11-06" })).toBe(
      "结束日期不能早于开始日期",
    );
    expect(slotDateRangeError({ start: "2026-02-30", end: "2026-11-06" })).toBe(
      "请选择有效日期",
    );
    expect(slotDateRangeError({ start: "2026-11-06", end: "2026-11-06" })).toBe(
      "",
    );
  });
  it("formats a complete Chinese date", () => {
    expect(formatSlotDate("2026-11-06")).toBe("2026年11月06日");
  });
});
