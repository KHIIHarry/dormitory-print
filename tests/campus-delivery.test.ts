import { describe, expect, it } from "vitest";
import { campusesOfBatch, slotsForCampus } from "../src/lib/campus";
import type { Campus, Slot } from "../src/lib/types";

const campuses = [{ id: "east", name: "East" }, { id: "west", name: "West" }] as Campus[];
const slots = [{ id: "e", campusId: "east" }, { id: "w", campusId: "west" }, { id: "legacy" }] as Slot[];

describe("campus delivery scope", () => {
  it("only returns slots explicitly assigned to the selected campus", () => {
    expect(slotsForCampus(slots, "east").map(s => s.id)).toEqual(["e"]);
    expect(slotsForCampus(slots, undefined)).toEqual([]);
  });
  it("uses a batch's explicit campus even with no orders or conflicting members", () => {
    const buildings = [{ id: "b", campusId: "west" }] as never;
    const orders = [{ id: "o", locationId: "b" }] as never;
    expect(campusesOfBatch(campuses, buildings, orders, { campusId: "east", orderIds: ["o"] }).map(c => c.id)).toEqual(["east"]);
    expect(campusesOfBatch(campuses, buildings, orders, { campusId: "east", orderIds: [] }).map(c => c.id)).toEqual(["east"]);
  });
});

it("only permits enabled buildings with explicit matching ownership", async () => {
  const { selectableBuildingsForCampus } = await import("../src/lib/campus");
  const buildings = [
    { id: "valid", campusId: "east", enabled: true },
    { id: "disabled", campusId: "east", enabled: false },
    { id: "moved", campusId: "west", enabled: true },
    { id: "unowned", enabled: true },
  ];
  expect(selectableBuildingsForCampus(buildings, "east").map(b => b.id)).toEqual(["valid"]);
  expect(selectableBuildingsForCampus(buildings, undefined)).toEqual([]);
});

it("keeps member-campus diagnostics separate from explicit batch ownership", async () => {
  const { memberCampusesOfBatch } = await import("../src/lib/campus");
  const buildings = [{ id: "b", campusId: "west" }] as never;
  const orders = [{ id: "o", locationId: "b" }] as never;
  expect(memberCampusesOfBatch(campuses, buildings, orders, { orderIds: ["o"] }).map(c => c.id)).toEqual(["west"]);
});

it("uses historical location snapshots when the location is absent", async () => {
  const { orderLocationSnapshot } = await import("../src/lib/campus");
  expect(orderLocationSnapshot({ locationSnapshot: { name: "旧楼", campusId: "east" } })).toEqual({ name: "旧楼", campusId: "east" });
  expect(orderLocationSnapshot({ data: { locationSnapshot: { name: "历史楼", campusId: "west" } } })).toEqual({ name: "历史楼", campusId: "west" });
});
