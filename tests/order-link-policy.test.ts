import { expect, it } from "vitest";
import { READ_ONLY_LINK_SCOPES } from "../src/growth/link-policy";
it("guest links expose exactly three read-only permissions", () => {
  expect(READ_ONLY_LINK_SCOPES).toEqual([
    "ORDER_VIEW",
    "FINAL_PDF_PREVIEW",
    "DELIVERY_PROOF_VIEW",
  ]);
});
