/**
 * Pure payroll DTOs, request builders and ledger rules for the V1 UI.
 *
 * This module is intentionally React-free and transport-free so the same rules
 * can be unit-tested in Node and reused by the admin/employee components.
 * The server remains authoritative for money, permissions and idempotency.
 */
export type PayrollPolicyMode = "FIXED" | "PERCENT";

export type PayrollPolicy = {
  id: string;
  version: number;
  scopeKey: string;
  staffId: string | null;
  staffName: string;
  mode: PayrollPolicyMode;
  fixedCents: number;
  rateBps: number;
  enabled: boolean;
  updatedAt: string;
};

export type PolicyCatalog = {
  defaultPolicy: PayrollPolicy | null;
  overrides: PayrollPolicy[];
};

export type WageEntry = {
  id: string;
  version: number;
  staffId: string;
  orderId: string | null;
  assignmentId: string | null;
  sourceKey: string;
  kind: string;
  amountCents: number;
  period: string;
  reason: string;
  actorId: string;
  snapshot: Record<string, unknown>;
  createdAt: string;
  settlementId: string | null;
  settledAt: string | null;
  needsConfiguration: boolean;
};

export type LedgerSummary = {
  staffId: string;
  staffName: string;
  entryCount: number;
  grossCents: number;
  deductionsCents: number;
  netCents: number;
  paidCents: number;
  unpaidCents: number;
  carryForwardCents: number;
  availableCents: number;
  needsConfigurationCount: number;
};

export type SettlementItem = {
  entryId: string;
  kind: string;
  amountCents: number;
  sourceKey: string;
  orderId: string | null;
  period: string;
};

export type Settlement = {
  id: string;
  version: number;
  staffId: string;
  staffName: string;
  period: string;
  amountCents: number;
  paidAt: string;
  paidBy: string;
  paidByName: string;
  reason: string;
  createdAt: string;
  items: SettlementItem[];
};

export type StaffLedger = {
  staff: { id: string; name: string };
  period: string;
  summary: LedgerSummary;
  entries: WageEntry[];
  carryForward: WageEntry[];
  settlements: Settlement[];
};

export type NeedsConfigurationRow = {
  entryId: string;
  orderId: string | null;
  staffId: string;
  staffName: string;
  assignmentId: string | null;
  deliveredAt: string | null;
  period: string;
  createdAt: string;
};

export type AdminLedger = {
  period: string;
  summaries: LedgerSummary[];
  entries: WageEntry[];
  carryForward: WageEntry[];
  needsConfiguration: NeedsConfigurationRow[];
};

export type PayrollStaffOption = {
  id: string;
  name: string;
  role?: string;
  enabled?: boolean;
};

export type PolicyDraft = {
  mode: PayrollPolicyMode;
  fixedCents: number | string;
  rateBps: number | string;
  enabled?: boolean;
  expectedVersion?: number | null;
  reason?: string;
};

export type ManualEntryDraft = {
  staffId: string;
  orderId?: string;
  amountCents: number | string;
  period?: string;
  reason: string;
};

export type SettlementDraft = {
  staffId: string;
  period: string;
  entryIds?: string[];
  reason: string;
};

export const PAYROLL_PERIOD_PATTERN = /^\d{4}-(0[1-9]|1[0-2])$/;

export function normalizePayrollPeriod(value: string): string {
  const period = (value || "").trim();
  if (!PAYROLL_PERIOD_PATTERN.test(period))
    throw new Error("工资月份格式应为 YYYY-MM");
  return period;
}

/** Chinese payroll month (Asia/Shanghai) for a client instant. */
export function shanghaiPeriod(
  value: Date | string | number = new Date(),
): string {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) throw new Error("工资月份时间无效");
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
  }).formatToParts(date);
  const year = parts.find((part) => part.type === "year")?.value;
  const month = parts.find((part) => part.type === "month")?.value;
  if (!year || !month) throw new Error("无法计算中国时区工资月份");
  return `${year}-${month}`;
}

function integer(
  value: number | string,
  label: string,
  minimum = -100_000_000,
  maximum = 100_000_000,
): number {
  const parsed =
    typeof value === "number"
      ? value
      : value === ""
        ? Number.NaN
        : Number(value);
  if (!Number.isSafeInteger(parsed)) throw new Error(`${label}必须是整数`);
  if (parsed < minimum || parsed > maximum)
    throw new Error(`${label}超出允许范围`);
  return parsed;
}

function text(value: string | undefined, label: string): string {
  const cleaned = (value || "").trim();
  if (!cleaned) throw new Error(label);
  return cleaned;
}

function optionalText(value: string | undefined): string | undefined {
  const cleaned = (value || "").trim();
  return cleaned || undefined;
}

export function policyPayload(input: PolicyDraft): Record<string, unknown> {
  if (input.mode !== "FIXED" && input.mode !== "PERCENT")
    throw new Error("工资策略模式无效");
  const fixedCents = integer(input.fixedCents, "固定工资", 0);
  const rateBps = integer(input.rateBps, "工资比例", 0, 10000);
  const body: Record<string, unknown> = {
    mode: input.mode,
    fixedCents,
    rateBps,
    enabled: input.enabled !== false,
  };
  if (input.expectedVersion !== undefined && input.expectedVersion !== null) {
    body.expectedVersion = integer(
      input.expectedVersion,
      "工资策略版本",
      0,
      2_147_483_647,
    );
  }
  const reason = optionalText(input.reason);
  if (reason) body.reason = reason;
  return body;
}

export function applyPolicyPayload(
  policyId: string | null | undefined,
  reason: string,
): Record<string, unknown> {
  const body: Record<string, unknown> = {
    reason: text(reason, "请填写补录工资的原因"),
  };
  const id = optionalText(policyId || undefined);
  if (id) body.policyId = id;
  return body;
}

export function manualEntryPayload(
  input: ManualEntryDraft,
): Record<string, unknown> {
  const staffId = text(input.staffId, "请选择员工");
  const reason = text(input.reason, "请填写人工调整原因");
  const amountCents = integer(
    input.amountCents,
    "工资金额",
    -100_000_000,
    100_000_000,
  );
  const body: Record<string, unknown> = { staffId, amountCents, reason };
  const orderId = optionalText(input.orderId);
  if (orderId) body.orderId = orderId;
  const period = optionalText(input.period);
  if (period) body.period = normalizePayrollPeriod(period);
  return body;
}

export function settlementPayload(
  input: SettlementDraft,
): Record<string, unknown> {
  const staffId = text(input.staffId, "请选择员工");
  const period = normalizePayrollPeriod(input.period);
  const reason = text(input.reason, "请填写发放原因");
  const body: Record<string, unknown> = { staffId, period, reason };
  if (input.entryIds && input.entryIds.length) {
    const ids = input.entryIds.map((id) => id.trim());
    if (ids.some((id) => !id)) throw new Error("工资条目 ID 不能为空");
    if (new Set(ids).size !== ids.length)
      throw new Error("工资条目不可重复选择");
    body.entryIds = ids;
  }
  return body;
}

/**
 * Validate a manual payout selection before the server sees it.
 * The server repeats these checks; this only makes the UI's disabled state honest.
 */
export function settlementSelection(
  entries: WageEntry[],
  period: string,
): { netCents: number; error: string } {
  if (!entries.length) return { netCents: 0, error: "请选择要发放的工资条目" };
  const target = normalizePayrollPeriod(period);
  const seen = new Set<string>();
  let netCents = 0;
  let staffId = "";
  for (const entry of entries) {
    if (seen.has(entry.id)) return { netCents, error: "工资条目重复选择" };
    seen.add(entry.id);
    if (entry.needsConfiguration || entry.kind === "NEEDS_CONFIGURATION")
      return { netCents, error: "待配置工资不能发放" };
    if (entry.settlementId)
      return { netCents, error: "所选条目中包含已发放工资" };
    if (entry.period > target)
      return { netCents, error: "不能提前发放未来月份的工资" };
    if (!staffId) staffId = entry.staffId;
    else if (staffId !== entry.staffId)
      return { netCents, error: "一次发放只能选择一个员工" };
    netCents += entry.amountCents;
  }
  if (netCents < 0)
    return { netCents, error: "净额为负数，请与后续月份工资合并发放" };
  return { netCents, error: "" };
}

/** Current-month unsettled entries plus every unpaid prior adjustment. */
export function suggestedSettlementEntries(
  ledger: Pick<StaffLedger, "entries" | "carryForward">,
): WageEntry[] {
  const current = ledger.entries.filter(
    (entry) => !entry.settlementId && !entry.needsConfiguration,
  );
  const carry = ledger.carryForward.filter(
    (entry) => !entry.settlementId && !entry.needsConfiguration,
  );
  const seen = new Set<string>();
  return [...current, ...carry].filter((entry) => {
    if (seen.has(entry.id)) return false;
    seen.add(entry.id);
    return true;
  });
}

export function wageKindLabel(kind: string): string {
  if (kind === "DELIVERY") return "交付计件";
  if (kind === "NEEDS_CONFIGURATION") return "待配置工资";
  if (kind === "REFUND_ADJUSTMENT") return "退款调整";
  if (kind === "SUPPLEMENTARY_ADJUSTMENT") return "补充费用调整";
  if (kind === "MANUAL") return "人工调整";
  return kind || "工资条目";
}

export function policyModeLabel(mode: PayrollPolicyMode): string {
  return mode === "PERCENT" ? "按净收款比例" : "每单固定金额";
}

/** All visible state markers for one ledger row. */
export function entryFlags(entry: WageEntry): string[] {
  const flags: string[] = [wageKindLabel(entry.kind)];
  if (entry.needsConfiguration) flags.push("待配置");
  else if (entry.settlementId) flags.push("已发放");
  else flags.push("未发放");
  if (entry.amountCents < 0) flags.push("冲减");
  if (entry.kind === "REFUND_ADJUSTMENT") flags.push("退款后追加");
  if (entry.kind === "SUPPLEMENTARY_ADJUSTMENT") flags.push("补费后追加");
  return Array.from(new Set(flags));
}

/** Delivery month for an adjustment recorded in a later cash-event month. */
export function entryOriginalDeliveryPeriod(
  entry: Pick<WageEntry, "period" | "snapshot">,
): string | null {
  const value = entry.snapshot?.originalDeliveryPeriod;
  if (typeof value !== "string" || !value) return null;
  return value;
}

export function payrollPaths() {
  const query = (params: Record<string, string | undefined>) => {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params))
      if (value) search.set(key, value);
    const suffix = search.toString();
    return suffix ? `?${suffix}` : "";
  };
  return {
    me: (period?: string) => `/payroll/me${query({ period })}`,
    staff: "/admin/payroll/staff",
    policies: "/admin/payroll/policies",
    defaultPolicy: "/admin/payroll/policies/default",
    staffPolicy: (staffId: string) =>
      `/admin/payroll/policies/staff/${encodeURIComponent(staffId)}`,
    applyPolicy: (orderId: string) =>
      `/admin/payroll/deliveries/${encodeURIComponent(orderId)}/apply-policy`,
    ledger: (options: { period?: string; staffId?: string } = {}) =>
      `/admin/payroll/ledger${query(options)}`,
    entries: "/admin/payroll/entries",
    settlements: (options: { period?: string; staffId?: string } = {}) =>
      `/admin/payroll/settlements${query(options)}`,
  };
}

export const PAYROLL_PATHS = payrollPaths();

/** Convert visible yuan/percent inputs to integer cents/basis points without float rounding. */
export function payrollHundredths(
  value: string,
  label: string,
  maximum = Number.MAX_SAFE_INTEGER,
): number {
  const text = value.trim();
  if (!/^\d+(?:\.\d{0,2})?$/.test(text))
    throw new Error(`${label}请填写非负数字，最多两位小数`);
  const [whole, fraction = ""] = text.split(".");
  const result = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  if (!Number.isSafeInteger(result) || result > maximum)
    throw new Error(`${label}超出允许范围`);
  return result;
}
