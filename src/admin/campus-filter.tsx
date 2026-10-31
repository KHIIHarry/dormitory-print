import type { Campus } from "../lib/types";

export function CampusFilter({
  campuses,
  value,
  onChange,
  ariaLabel = "校区筛选",
  allLabel = "全部校区",
}: {
  campuses?: Campus[];
  value: string;
  onChange: (value: string) => void;
  ariaLabel?: string;
  allLabel?: string;
}) {
  if (!campuses?.length) return null;
  return (
    <select
      aria-label={ariaLabel}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    >
      <option value="">{allLabel}</option>
      {campuses.map((campus) => (
        <option key={campus.id} value={campus.id}>
          {campus.name}
          {!campus.selectionEnabled ? "（已关闭）" : ""}
        </option>
      ))}
    </select>
  );
}

export function CampusTag({ campus }: { campus?: Campus }) {
  if (!campus) return <span className="campus-inline muted">未分校区</span>;
  return (
    <span className="campus-inline">
      <i className="campus-dot" style={{ background: campus.themeColor }} />
      {campus.name}
    </span>
  );
}
