import { useState } from "react";
import { Field } from "../components/ui";
import { DeliveryDateField } from "../components/delivery-date-field";
import { useShanghaiToday } from "../lib/use-shanghai-today";
import {
  formatSlotDate,
  identifySlotDateRange,
  slotDateRange,
  slotDateRangeError,
  type SlotDatePreset,
  type SlotDateRange,
} from "../lib/slot-date-range";

export function useSlotDateFilter(dates: string[]) {
  const today = useShanghaiToday();
  const [selection, setSelection] = useState<{
    preset: SlotDatePreset | "custom";
    range: SlotDateRange;
  }>(() => ({ preset: "week", range: slotDateRange("week", today, dates) }));

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
      <DeliveryDateField
        label="开始日期"
        value={range.start}
        error={!range.start && error ? error : undefined}
        onChange={(value) => changeDate("start", value)}
      />
      <DeliveryDateField
        label="结束日期"
        value={range.end}
        error={range.start && error ? error : undefined}
        onChange={(value) => changeDate("end", value)}
      />
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
