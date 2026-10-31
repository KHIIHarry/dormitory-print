import { describe, expect, it } from "vitest";
import {
  demoPriceLines,
  sameArtifactAcks,
  quoteIsCurrent,
} from "../src/customer/checkout";
import { createDemo } from "../src/lib/demo";

describe("checkout quote and PDF boundaries", () => {
  it("counts duplex paper per task and keeps binding outside A4 discount base", () => {
    const config = createDemo().config;
    config.bindingApproved = true;
    config.prices = {
      bwSingle: 10,
      bwDuplex: 8,
      colorSingle: 100,
      colorDuplex: 80,
      photo: 150,
      corner: 50,
      side: 100,
    };
    const file = {
      id: "doc",
      kind: "DOCUMENT",
      pages: 7,
      segments: [
        { id: "s", range: "1-7", colorMode: "BW", sides: "DUPLEX", copies: 3 },
      ],
      bindingType: "CORNER",
      photo: { copies: 1 },
      excludedPages: [],
    } as any;
    const lines = demoPriceLines([file], config);
    expect(lines.find((l) => l.service === "A4")).toMatchObject({
      quantity: 12,
      unitPriceCents: 8,
      amountCents: 96,
    });
    expect(lines.find((l) => l.service === "BINDING")).toMatchObject({
      quantity: 3,
      amountCents: 150,
    });
    expect(lines.every((l) => l.amountCents >= 0)).toBe(true);
  });
  it("retains PDF acknowledgement only when the artifact id and hash match", () => {
    expect(
      sameArtifactAcks(
        [
          { id: "a", sha256: "same" },
          { id: "b", sha256: "old" },
        ] as any,
        [
          { id: "a", sha256: "same" },
          { id: "b", sha256: "new" },
        ] as any,
        ["a", "b"],
      ),
    ).toEqual(["a"]);
  });
  it("requires a fresh quote even when the payable amount is zero", () => {
    expect(
      quoteIsCurrent(
        { expiresAt: "2030-01-01T00:00:00Z", totalCents: 0 } as any,
        Date.parse("2029-01-01"),
      ),
    ).toBe(true);
    expect(
      quoteIsCurrent(
        { expiresAt: "2020-01-01T00:00:00Z", totalCents: 0 } as any,
        Date.parse("2029-01-01"),
      ),
    ).toBe(false);
    expect(quoteIsCurrent(null)).toBe(false);
  });
});
