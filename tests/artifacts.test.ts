import { describe, it, expect } from "vitest";
import { PDFDocument } from "pdf-lib";
import { generateDemoArtifacts } from "../src/lib/artifacts";
import { createDemo, makeFile } from "../src/lib/demo";
describe("generated immutable demo PDF", () => {
  it("actually emits 24 PDF pages and per-copy blank backs for 7 duplex pages x3", async () => {
    const file = makeFile("seven.pdf", 7);
    file.segments[0].copies = 3;
    const [a] = await generateDemoArtifacts([file], createDemo().config);
    const pdf = await PDFDocument.load(
      await (await fetch(a.url)).arrayBuffer(),
    );
    expect(pdf.getPageCount()).toBe(24);
    expect(a.sheets).toBe(12);
    expect(a.blankBacks).toBe(3);
    expect(a.sha256).toMatch(/^[a-f0-9]{64}$/);
    URL.revokeObjectURL(a.url);
  });
  it("keeps mixed single/duplex segments in separate artifacts and totals 14 sheets", async () => {
    const file = makeFile("mixed.pdf", 10);
    file.segments = [
      {
        id: "one",
        range: "1-4",
        sides: "SINGLE",
        colorMode: "BW",
        flip: "NONE",
        copies: 2,
      },
      {
        id: "two",
        range: "5-10",
        sides: "DUPLEX",
        colorMode: "BW",
        flip: "LONG_EDGE",
        copies: 2,
      },
    ];
    const artifacts = await generateDemoArtifacts([file], createDemo().config);
    expect(artifacts).toHaveLength(2);
    expect(artifacts.reduce((s, a) => s + a.sheets, 0)).toBe(14);
    expect(artifacts.map((a) => a.pages)).toEqual([8, 12]);
    artifacts.forEach((a) => URL.revokeObjectURL(a.url));
  });
});
