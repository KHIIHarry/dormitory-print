import { describe, expect, it } from "vitest";
import { makeFile, createDemo } from "../src/lib/demo";
import { fileErrors, filePrice } from "../src/lib/domain";
import {
  normalizePrintFile,
  pageAssignments,
  updatePrintSegment,
  pageSelectionLabel,
} from "../src/lib/print-settings";

describe("打印页码设置", () => {
  it("跳过页摘要排序去重并合并连续页码", () => {
    expect(pageSelectionLabel([7, 2, 3, 3, 5, 6])).toBe("2–3、5–7");
    expect(pageSelectionLabel([])).toBe("");
  });
  it.each(["1", "5", "5-5"])("单页 %s 自动单面并按单面计价", (range) => {
    const file = makeFile("seven.pdf", 7);
    file.segments[0].range = range;
    file.segments[0].copies = 3;
    file.artifactId = "old-artifact";
    const next = normalizePrintFile(file);
    expect(next.segments[0]).toMatchObject({
      sides: "SINGLE",
      flip: "NONE",
      copies: 3,
    });
    expect(next.artifactId).toBeUndefined();
    const config = createDemo().config;
    expect(filePrice(next, config)).toBe(config.prices.bwSingle! * 3);
    expect(file.segments[0].sides).toBe("DUPLEX");
  });

  it("多页双面和未完成的页码输入保持不变", () => {
    const file = makeFile("seven.pdf", 7);
    expect(normalizePrintFile(file)).toBe(file);
    file.segments[0].range = "1-";
    expect(normalizePrintFile(file)).toBe(file);
  });

  it("各分段分别判断，单页多份仍只允许单面", () => {
    const file = makeFile("seven.pdf", 7);
    file.segments.push({
      ...file.segments[0],
      id: "single",
      range: "7",
      copies: 5,
    });
    file.segments[0].range = "1-6";
    const next = normalizePrintFile(file);
    expect(next.segments.map((s) => s.sides)).toEqual(["DUPLEX", "SINGLE"]);
    expect(fileErrors(next)).toEqual([]);
  });

  it("页面归属保留分段编号，忽略正在编辑的无效分段", () => {
    const file = makeFile("seven.pdf", 7);
    file.segments[0].range = "1,3";
    file.segments.push({ ...file.segments[0], id: "second", range: "5-7" });
    file.segments.push({ ...file.segments[0], id: "editing", range: "2-" });
    expect([...pageAssignments(file)]).toEqual([
      [1, [1]],
      [3, [1]],
      [5, [2]],
      [6, [2]],
      [7, [2]],
    ]);
  });

  it("修改分段为单页时立即校正，重新加入打印的页面取消跳过", () => {
    const file = makeFile("seven.pdf", 7);
    file.segments[0].range = "1-3";
    file.excludedPages = [4, 5, 6, 7];
    const next = updatePrintSegment(file, file.segments[0].id, { range: "5" });
    expect(next.segments[0]).toMatchObject({
      range: "5",
      sides: "SINGLE",
      flip: "NONE",
    });
    expect(next.excludedPages).toEqual([4, 6, 7]);
    const multi = updatePrintSegment(next, next.segments[0].id, {
      range: "1-3",
    });
    expect(multi.segments[0].sides).toBe("SINGLE");
    expect(fileErrors(multi)).toContain(
      "第 5 页未分配，请明确跳过或添加到分段",
    );
  });
});
