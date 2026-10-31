import { it, expect } from "vitest";
import { taskStatus, aggregatePrintStatus } from "../src/lib/print-state";
it("unattempted segments do not inherit a sibling attempt", () => {
  expect(
    taskStatus(
      { printStatus: "ATTEMPTED", taskResults: { a: "ATTEMPTED" } },
      "b",
    ),
  ).toBe("PENDING");
});
it("all document segments must finish independently", () => {
  expect(aggregatePrintStatus(["a", "b"], { a: "COMPLETED" })).toBe("PENDING");
  expect(
    aggregatePrintStatus(["a", "b"], { a: "COMPLETED", b: "COMPLETED" }),
  ).toBe("COMPLETED");
});
it("photo tasks have a real completion condition", () => {
  expect(aggregatePrintStatus(["photo"], { photo: "ATTEMPTED" })).toBe(
    "ATTEMPTED",
  );
  expect(aggregatePrintStatus(["photo"], { photo: "COMPLETED" })).toBe(
    "COMPLETED",
  );
});
