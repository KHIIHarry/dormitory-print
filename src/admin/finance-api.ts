export type ExpenseCategory = "PAPER" | "INK" | "OTHER";
export interface FinanceSummary {
  month: string | null;
  confirmedReceivedCents: number;
  refundCents: number;
  netReceivedCents: number;
  paperPurchaseCents: number;
  inkPurchaseCents: number;
  otherExpenseCents: number;
  consumablesBalanceCents: number;
  paidWagesCents: number;
  balanceCents: number;
  paperSheets: number;
  paperUnitYuan: string | null;
  legacyArchiveCount: number;
}
export interface Expense {
  id: string;
  version: number;
  category: ExpenseCategory;
  spentOn: string;
  amountCents: number;
  paperSheets: number | null;
  note: string;
  status: "ACTIVE" | "VOIDED";
  voidReason: string | null;
  createdAt: string;
}
export interface ExpenseList {
  items: Expense[];
  total: number;
  page: number;
  pageSize: number;
}
export interface ExpenseForm {
  category: ExpenseCategory;
  spentOn: string;
  amount: string;
  paperSheets: string;
  note: string;
}
export function shanghaiToday(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  return ["year", "month", "day"]
    .map((key) => parts.find((part) => part.type === key)!.value)
    .join("-");
}
export function parseExpenseCents(value: string) {
  const text = value.trim();
  if (!/^\d+(\.\d{1,2})?$/.test(text))
    throw new Error("金额须为正数，最多两位小数");
  const [yuan, fraction = ""] = text.split(".");
  const cents = Number(yuan) * 100 + Number(fraction.padEnd(2, "0"));
  if (!Number.isSafeInteger(cents) || cents <= 0 || cents > 2147483647)
    throw new Error("请输入有效的正数金额");
  return cents;
}
export function expensePayload(form: ExpenseForm, today = shanghaiToday()) {
  const date = new Date(`${form.spentOn}T00:00:00Z`);
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(form.spentOn) ||
    !Number.isFinite(date.getTime()) ||
    date.toISOString().slice(0, 10) !== form.spentOn ||
    form.spentOn > today
  )
    throw new Error("请选择有效日期，不能晚于今天");
  const amountCents = parseExpenseCents(form.amount);
  let paperSheets: number | undefined;
  if (form.category === "PAPER") {
    paperSheets = Number(form.paperSheets);
    if (
      !/^\d+$/.test(form.paperSheets) ||
      !Number.isSafeInteger(paperSheets) ||
      paperSheets <= 0 ||
      paperSheets > 2147483647
    )
      throw new Error("纸张张数须为正整数");
  }
  if (form.note.trim().length > 500) throw new Error("备注最多500字");
  return {
    category: form.category,
    spentOn: form.spentOn,
    amountCents,
    ...(paperSheets === undefined ? {} : { paperSheets }),
    ...(form.note.trim() ? { note: form.note.trim() } : {}),
  };
}
