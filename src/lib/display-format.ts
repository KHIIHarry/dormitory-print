/** Display known byte counts without rounding small real files down to zero. */
export function formatFileSize(size: number | null | undefined): string {
  if (typeof size !== "number" || !Number.isFinite(size) || size < 0) return "大小未知";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(size < 10240 ? 1 : 0)} KB`;
  return `${(size / (1024 * 1024)).toFixed(size < 10 * 1024 * 1024 ? 2 : 1)} MB`;
}
/** Server timestamps are displayed in the campus timezone; legacy labels stay readable. */
export function formatDisplayTime(value: string | null | undefined): string {
  if (!value) return "—";
  if (!/^\d{4}-\d\d-\d\dT/.test(value)) return value;
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "时间不可用";
  return new Intl.DateTimeFormat("zh-CN", {timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hourCycle:"h23"}).format(date);
}
