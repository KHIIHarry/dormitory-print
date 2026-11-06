export type SlotDatePreset = "today" | "week" | "month" | "all";
export type SlotDateRange = { start: string; end: string };

export function shanghaiToday(now = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}

const isoDate = (date: Date) => date.toISOString().slice(0, 10);

export function slotDateRange(
  preset: SlotDatePreset,
  today: string,
  dates: string[],
): SlotDateRange {
  if (preset === "all") {
    return dates.reduce(
      (range, date) => ({
        start: !range.start || date < range.start ? date : range.start,
        end: !range.end || date > range.end ? date : range.end,
      }),
      { start: "", end: "" },
    );
  }
  const date = new Date(`${today}T00:00:00Z`);
  if (preset === "week") {
    date.setUTCDate(date.getUTCDate() - ((date.getUTCDay() + 6) % 7));
    const start = isoDate(date);
    date.setUTCDate(date.getUTCDate() + 6);
    return { start, end: isoDate(date) };
  }
  if (preset === "month") {
    date.setUTCDate(1);
    const start = isoDate(date);
    date.setUTCMonth(date.getUTCMonth() + 1, 0);
    return { start, end: isoDate(date) };
  }
  return { start: today, end: today };
}

export function identifySlotDateRange(
  range: SlotDateRange,
  today: string,
  dates: string[],
): SlotDatePreset | "custom" {
  if (slotDateRangeError(range)) return "custom";
  for (const preset of ["today", "week", "month", "all"] as const) {
    const bounds = slotDateRange(preset, today, dates);
    if (range.start === bounds.start && range.end === bounds.end) return preset;
  }
  return "custom";
}

export function slotDateRangeError({ start, end }: SlotDateRange): string {
  if (!start) return "请选择开始日期";
  if (!end) return "请选择结束日期";
  for (const value of [start, end]) {
    const parsed = new Date(`${value}T00:00:00Z`);
    if (
      !/^\d{4}-\d{2}-\d{2}$/.test(value) ||
      !Number.isFinite(parsed.getTime()) ||
      isoDate(parsed) !== value
    )
      return "请选择有效日期";
  }
  return end < start ? "结束日期不能早于开始日期" : "";
}

export function formatSlotDate(date: string): string {
  const [year, month, day] = date.split("-");
  return `${year}年${month}月${day}日`;
}
