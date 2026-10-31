import { describe, expect, it } from "vitest";
import { ApiError } from "../src/lib/api";
import {
  blockerCode,
  hasWageBlocker,
  trashBlockers,
} from "../src/lib/trash-blockers";

describe("trash blockers", () => {
  it("turns TRASH_BLOCKED codes into readable reasons without duplicates", () => {
    const error = new ApiError(
      "TRASH_BLOCKED",
      "对象仍存在有效引用或未结业务，不能删除",
      409,
      {
        blockers: [
          "WAGE_UNSETTLED:wage-1",
          "WAGE_UNSETTLED:wage-2",
          "DELIVERY_NOT_COMPLETED",
        ],
      },
    );
    const blockers = trashBlockers(error);
    expect(blockers.map((item) => item.code)).toEqual([
      "WAGE_UNSETTLED",
      "DELIVERY_NOT_COMPLETED",
    ]);
    expect(blockers[0].label).toContain("工资");
    expect(blockers[1].label).toContain("配送");
    expect(hasWageBlocker(blockers)).toBe(true);
  });

  it("keeps unknown codes visible instead of hiding the reason", () => {
    const error = new ApiError("TRASH_BLOCKED", "不能删除", 409, {
      blockers: ["SOMETHING_NEW:42"],
    });
    expect(trashBlockers(error)).toEqual([
      { code: "SOMETHING_NEW", label: "未结业务（SOMETHING_NEW:42）" },
    ]);
    expect(blockerCode("SOMETHING_NEW:42")).toBe("SOMETHING_NEW");
  });

  it("returns nothing for unrelated failures", () => {
    expect(trashBlockers(new ApiError("NOT_FOUND", "不存在", 404))).toEqual([]);
    expect(trashBlockers(new Error("boom"))).toEqual([]);
    expect(trashBlockers(null)).toEqual([]);
  });
});
