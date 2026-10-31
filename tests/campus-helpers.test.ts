import { describe, expect, it } from "vitest";
import type { Building, Campus } from "../src/lib/types";
import {
  campusOfLocation,
  campusOfOrder,
  campusesOfBatch,
} from "../src/lib/campus";

const campuses: Campus[] = [
  {
    id: "east",
    name: "东校区",
    themeColor: "#C67C4E",
    selectionEnabled: true,
    sortOrder: 0,
    mapVersion: 1,
    items: [],
  },
  {
    id: "west",
    name: "西校区",
    themeColor: "#7E8A63",
    selectionEnabled: true,
    sortOrder: 1,
    mapVersion: 1,
    items: [],
  },
];

const buildings: Building[] = [
  { id: "b1", name: "东一栋", type: "宿舍楼", x: 20, y: 20, enabled: true, campusId: "east" },
  { id: "b2", name: "西一栋", type: "宿舍楼", x: 60, y: 60, enabled: true, campusId: "west" },
];

describe("campus helpers", () => {
  it("resolves a building's campus and tolerates missing data", () => {
    expect(campusOfLocation(campuses, buildings, "b2")?.id).toBe("west");
    expect(campusOfLocation(campuses, buildings, "missing")).toBeUndefined();
    expect(campusOfLocation(undefined, undefined, "b2")).toBeUndefined();
  });

  it("derives an order campus from its delivery location", () => {
    expect(campusOfOrder(campuses, buildings, { locationId: "b1" })?.name).toBe("东校区");
    expect(campusOfOrder(campuses, buildings, null)).toBeUndefined();
  });

  it("deduplicates batch campuses and keeps configured order", () => {
    const result = campusesOfBatch(
      campuses,
      buildings,
      [
        { id: "o1", locationId: "b1" },
        { id: "o2", locationId: "b2" },
      ] as never,
      { orderIds: ["o2", "o1", "o2"] },
    );
    expect(result.map((campus) => campus.id)).toEqual(["east", "west"]);
    expect(
      campusesOfBatch(undefined, undefined, [], { orderIds: ["o1"] }),
    ).toEqual([]);
  });
});
