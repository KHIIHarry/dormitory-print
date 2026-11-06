import { useEffect, useState } from "react";
import { Field } from "../components/ui";
import {
  formatSlotDate,
  identifySlotDateRange,
  shanghaiToday,
  slotDateRange,
  slotDateRangeError,
  type SlotDatePreset,
  type SlotDateRange,
} from "../lib/slot-date-range";

export function useSlotDateFilter(dates: string[]) {
  const [today, setToday] = useState(() => shanghaiToday());
  const [selection, setSelection] = useState<{
    preset: SlotDatePreset | "custom";
    range: SlotDateRange;
  }>(() => ({ preset: "week", range: slotDateRange("week", today, dates) }));

  useEffect(() => {
    const refreshToday = () => setToday(shanghaiToday());
    const timer = window.setInterval(refreshToday, 60_000);
    document.addEventListener("visibilitychange", refreshToday);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshToday);
    };
  }, []);

  const range =
    selection.preset === "custom"
      ? selection.range
      : slotDateRange(selection.preset, today, dates);
  const error =
    selection.preset === "all" && !dates.length
      ? ""
      : slotDateRangeError(range);

  return {
    range,
    preset: selection.preset,
    error,
    choosePreset(preset: SlotDatePreset | "custom") {
      setSelection({ preset, range });
    },
    changeDate(field: keyof SlotDateRange, value: string) {
      const next = { ...range, [field]: value };
      setSelection({
        preset: identifySlotDateRange(next, today, dates),
        range: next,
      });
    },
  };
}

export function SlotDateFilter({
  filter,
}: {
  filter: ReturnType<typeof useSlotDateFilter>;
}) {
  const { range, preset, error, choosePreset, changeDate } = filter;
  return (
    <>
      <Field label="日期范围">
        <select
          value={preset}
          onChange={(event) =>
            choosePreset(event.target.value as SlotDatePreset | "custom")
          }
        >
          <option value="week">本周</option>
          <option value="month">本月</option>
          <option value="today">当天</option>
          <option value="all">全部时段</option>
          <option value="custom">自定义</option>
        </select>
      </Field>
      <Field label="开始日期" error={!range.start && error ? error : undefined}>
        <input
          type="date"
          value={range.start}
          onChange={(event) => changeDate("start", event.target.value)}
        />
      </Field>
      <Field label="结束日期" error={range.start && error ? error : undefined}>
        <input
          type="date"
          value={range.end}
          onChange={(event) => changeDate("end", event.target.value)}
        />
      </Field>
    </>
  );
}

export function SlotDateSummary({
  range,
  count,
}: {
  range: SlotDateRange;
  count: number;
}) {
  return (
    <p className="slot-date-summary" role="status">
      {range.start && range.end
        ? `${formatSlotDate(range.start)} — ${formatSlotDate(range.end)}`
        : "全部时段"}
      <span>共 {count} 个时段</span>
    </p>
  );
}
