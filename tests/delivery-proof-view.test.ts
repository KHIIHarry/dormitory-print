import { expect, it } from "vitest";
import { normalizeProof } from "../src/courier/task-state";

it("keeps the server photo asset when delivery confirmation has no upload preview URL", () => {
  const proof = normalizeProof({
    assignmentId: "assignment-1",
    photoAssetId: "photo-1",
    deliveredAt: "2026-11-05T05:10:38Z",
    placementNote: "一楼收发台",
  });
  expect(proof).toMatchObject({
    photoAssetId: "photo-1",
    placementNote: "一楼收发台",
  });
});
