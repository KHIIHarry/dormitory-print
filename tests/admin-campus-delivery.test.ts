import { describe, expect, it } from "vitest";
import { orderCampusId } from "../src/admin/campus-filter";
import { slotsForCampus } from "../src/lib/campus";

describe("admin delivery campus scope", () => {
  it("never offers unassigned or other-campus slots", () => {
    const base = {
      enabled: true,
      date: "2030-01-01",
      weekday: "",
      start: "12:00",
      end: "13:00",
      name: "slot",
      capacity: 10,
    };
    const slots = [
      { ...base, id: "east", campusId: "e" },
      { ...base, id: "west", campusId: "w" },
      { ...base, id: "legacy" },
    ];
    expect(slotsForCampus(slots, "e").map((slot) => slot.id)).toEqual(["east"]);
    expect(slotsForCampus(slots, "")).toEqual([]);
  });
  it("resolves an order from its building or historical snapshot", () => {
    expect(
      orderCampusId([{ id: "b", campusId: "e" }], { locationId: "b" }),
    ).toBe("e");
    expect(
      orderCampusId([], {
        locationId: "missing",
        locationSnapshot: { campusId: "w" },
      }),
    ).toBe("w");
  });
});

it("shows only the order campus in the proposal form", async () => {
  const { createElement } = await import("react");
  const { renderToStaticMarkup } = await import("react-dom/server");
  const { PendingSlotProposal } = await import("../src/admin/orders");
  const base = {
    enabled: true,
    selectable: true,
    date: "2030-11-01",
    start: "12:00",
    end: "13:00",
    weekday: "",
    capacity: 10,
  };
  const html = renderToStaticMarkup(
    createElement(PendingSlotProposal, {
      campusId: "e",
      slots: [
        { ...base, id: "e1", name: "东校区时段", campusId: "e" },
        { ...base, id: "w1", name: "西校区时段", campusId: "w" },
      ],
      onSubmit: async () => {},
    }),
  );
  expect(html).toContain("东校区时段");
  expect(html).not.toContain("西校区时段");
});

it("does not offer a slot whose cutoff has elapsed", async () => {
  const { createElement } = await import("react");
  const { renderToStaticMarkup } = await import("react-dom/server");
  const { PendingSlotProposal } = await import("../src/admin/orders");
  const html = renderToStaticMarkup(
    createElement(PendingSlotProposal, {
      campusId: "e",
      slots: [
        {
          id: "expired",
          name: "过期时段",
          campusId: "e",
          enabled: true,
          cutoffAt: "2020-01-01T00:00:00Z",
          date: "2020-01-01",
          weekday: "",
          start: "12:00",
          end: "13:00",
          capacity: 10,
        },
      ],
      onSubmit: async () => {},
    }),
  );
  expect(html).not.toContain("过期时段");
});
