import { shanghaiToday, slotDateRangeError } from "./slot-date-range";
import type { Batch } from "./types";

export const courierDatePresets = [
  { key: "yesterday", label: "昨天", days: -1 },
  { key: "today", label: "今天", days: 0 },
  { key: "tomorrow", label: "明天", days: 1 },
] as const;

export const courierBatchStatuses = [
  { key: "PLANNED", label: "规划中" },
  { key: "POSTPONED", label: "已延期" },
  { key: "PUBLISHED", label: "已发布" },
  { key: "DELIVERING", label: "配送中" },
  { key: "COMPLETED", label: "已完成" },
] as const;

export function hasCourierTasks(batch: Batch) {
  return (
    batch.orderIds.length > 0 &&
    courierBatchStatuses.some(({ key }) => key === batch.status)
  );
}

export function courierBatchFilters(
  search: string | URLSearchParams,
  today = shanghaiToday(),
) {
  const params = new URLSearchParams(search);
  const customDate = params.get("date") || "";
  const preset =
    courierDatePresets.find(({ key }) => key === params.get("day")) ??
    courierDatePresets[1];
  const status =
    courierBatchStatuses.find(({ key }) => key === params.get("status"))?.key ||
    "";
  if (
    customDate &&
    !slotDateRangeError({ start: customDate, end: customDate })
  ) {
    return { date: customDate, day: null, status };
  }
  const date = new Date(`${today}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + preset.days);
  return { date: date.toISOString().slice(0, 10), day: preset.key, status };
}
