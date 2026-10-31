import { describe, expect, it } from "vitest";
import { mapPublishHotspots, mapPublishError } from "../src/admin/map-publish";

describe("map publishing", () => {
  it("submits only writable hotspot fields even when the server returns metadata", () => {
    const hotspot = { id: "building-1", name: "教学楼", type: "教学楼", x: 32.5, y: 40, enabled: true, version: 4, mapVersion: 9 };
    expect(mapPublishHotspots([hotspot])).toEqual([{ id: "building-1", name: "教学楼", type: "教学楼", x: 32.5, y: 40, enabled: true }]);
  });
  it("identifies the affected hotspot when the server rejects a field", () => {
    expect(mapPublishError({ message: "输入内容不符合要求", details: { fieldErrors: { "hotspots.1.name": "String should have at least 1 character" } } }, [{ name: "宿舍" }, { name: "教学楼" }])).toContain("教学楼");
    expect(mapPublishError({ message: "没有权限" }, [])).toBe("没有权限");
  });
});
