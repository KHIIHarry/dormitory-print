import type { Building } from "../lib/types";

/** API responses carry read-only version fields; publish accepts only these values. */
export function mapPublishHotspots(buildings: Building[]) {
  return buildings.map(({ id, name, type, x, y, enabled }) => ({
    id, name, type, x, y, enabled,
  }));
}

/** Convert a structured validation failure into a useful, non-technical hint. */
export function mapPublishError(error: unknown, buildings: Pick<Building, "name">[]) {
  const value = error as { message?: string; details?: { fieldErrors?: Record<string, string> } };
  if (value.message !== "输入内容不符合要求") return value.message || "地图发布失败，请重试";
  const fields = value.details?.fieldErrors || {};
  const first = Object.keys(fields)[0];
  const match = first?.match(/^hotspots\.(\d+)\.(.+)$/);
  if (match) {
    const index = Number(match[1]);
    const label = buildings[index]?.name || `第 ${index + 1} 个热点`;
    const field = ({ name: "名称", type: "类型", x: "横向坐标", y: "纵向坐标", enabled: "配送状态" } as Record<string, string>)[match[2]];
    return field ? `楼栋“${label}”的${field}不符合要求，请检查后重试。` : `楼栋“${label}”的数据不符合要求，请刷新后重新检查。`;
  }
  return "地图数据未通过校验，请检查楼栋名称、类型与坐标后重试。";
}
