import { describe, it, expect } from "vitest";
import {
  parseRanges,
  productionCount,
  validateAllocation,
  canPack,
  canStartBatch,
  validateProof,
} from "../src/lib/domain";
describe("DORMPRINT-CONTRACT-1", () => {
  it("rejects malformed, overlapping and out-of-range page selections", () => {
    expect(parseRanges("1-4,7,9-12", 12)).toEqual([
      { from: 1, to: 4 },
      { from: 7, to: 7 },
      { from: 9, to: 12 },
    ]);
    for (const value of ["", "0", "-1", "2.5", "4-2", "1-4,3", "13", "1,,2"])
      expect(() => parseRanges(value, 12)).toThrow();
  });
  it("expands every odd duplex copy with its own blank back", () => {
    expect(productionCount(7, 3, "DUPLEX")).toEqual({
      sheets: 12,
      pdfPages: 24,
      blankBacks: 3,
    });
    expect(
      productionCount(4, 2, "SINGLE").sheets +
        productionCount(6, 2, "DUPLEX").sheets,
    ).toBe(14);
  });
  it("requires intentional exclusions and no duplicate segments", () => {
    expect(
      validateAllocation(
        5,
        [
          [1, 2],
          [3, 4],
        ],
        [5],
      ),
    ).toEqual([]);
    expect(
      validateAllocation(
        5,
        [
          [1, 2],
          [2, 3],
        ],
        [],
      ).length,
    ).toBeGreaterThan(0);
    expect(validateAllocation(5, [[1, 2, 3, 4]], [])).toContain(
      "第 5 页未分配，请明确跳过或添加到分段",
    );
  });
  it("blocks packing and dispatch until all production is confirmed", () => {
    expect(
      canPack([{ printStatus: "COMPLETED", bindingStatus: "COMPLETED" }]),
    ).toBe(true);
    expect(
      canPack([{ printStatus: "ATTEMPTED", bindingStatus: "NOT_REQUIRED" }]),
    ).toBe(false);
    expect(
      canStartBatch([{ packingStatus: "READY", addressReview: false }]),
    ).toBe(false);
    expect(
      canStartBatch([{ packingStatus: "PACKED", addressReview: true }]),
    ).toBe(false);
  });
  it("requires real position for couriers and independent photo evidence", () => {
    expect(
      validateProof("COURIER", {
        photo: true,
        skipPhotoReason: "",
        position: false,
        skipLocationReason: "定位失败",
      }),
    ).toContain("配送员必须获取真实定位");
    expect(
      validateProof("ADMIN", {
        photo: false,
        skipPhotoReason: "  ",
        position: false,
        skipLocationReason: "设备定位不可用",
      }),
    ).toContain("请上传交付照片或填写有效的免拍照原因");
    expect(
      validateProof("COURIER", {
        photo: true,
        skipPhotoReason: "",
        position: true,
        skipLocationReason: "",
      }),
    ).toEqual([]);
  });
});
