import { describe, expect, it } from "vitest";
import { batchPrimaryAction } from "../src/lib/domain";
describe("delivery batch actions", () => {
  it("requires planned and postponed batches to be published before starting", () => {
    expect(batchPrimaryAction("PLANNED")).toBe("PUBLISH");
    expect(batchPrimaryAction("POSTPONED")).toBe("PUBLISH");
    expect(batchPrimaryAction("PUBLISHED")).toBe("START");
    for (const status of ["DELIVERING", "COMPLETED", "CANCELLED", "UNKNOWN"]) expect(batchPrimaryAction(status)).toBeNull();
  });
});
