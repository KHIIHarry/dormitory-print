import { it, expect } from "vitest";
import { photoCrop, paperLayout } from "../src/lib/photo-layout";
it("keeps COVER crop bounded and aware of source/paper aspect ratios", () => {
  const crop = photoCrop(4000, 3000, 110, 70, 1, 50, 50);
  expect(crop.x).toBeGreaterThanOrEqual(0);
  expect(crop.x + crop.width).toBeLessThanOrEqual(1);
  expect(crop.y + crop.height).toBeLessThanOrEqual(1);
  expect(crop.width / crop.height).toBeCloseTo(110 / 70 / (4000 / 3000));
});
it("uses all four calibrated margins independently", () => {
  expect(
    paperLayout(127, 89, { top: 3, right: 4, bottom: 5, left: 6 }),
  ).toEqual({
    width: 127,
    height: 89,
    printWidth: 117,
    printHeight: 81,
    left: 6,
    top: 3,
  });
});
