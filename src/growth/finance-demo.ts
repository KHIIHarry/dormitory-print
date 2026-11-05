/** Explicit demo expense ledger; real mode always uses the finance API. */
import { ApiError } from "../lib/api";
import {
  expensePayload,
  shanghaiToday,
  type Expense,
  type FinanceSummary,
} from "../admin/finance-api";
import type { Order } from "../lib/types";
import type { StaffSession } from "./types";
const entries: Expense[] = [];
export function financeDemoRequest(
  path: string,
  query: URLSearchParams,
  method: string,
  body: Record<string, any>,
  staff: StaffSession | null,
  orders: Order[],
) {
  if (
    staff?.role !== "ADMIN" ||
    !(staff.capabilities || []).some((c) => c === "*" || c === "finance.manage")
  )
    throw new ApiError("FORBIDDEN", "当前账号没有收支管理权限", 403);
  const month = query.get("month");
  if (month && !/^\d{4}-(0[1-9]|1[0-2])$/.test(month))
    throw new ApiError("VALIDATION_ERROR", "请选择有效月份", 422);
  if (path === "/admin/finance/summary" && method === "GET") {
    const active = entries.filter(
      (e) => e.status === "ACTIVE" && (!month || e.spentOn.startsWith(month)),
    );
    const paper = active.filter((e) => e.category === "PAPER"),
      ink = active.filter((e) => e.category === "INK");
    const sum = (rows: Expense[]) =>
      rows.reduce((s, e) => s + e.amountCents, 0);
    const paperSheets = paper.reduce((s, e) => s + (e.paperSheets || 0), 0);
    const paid = orders.filter(
      (o) =>
        o.settlement &&
        (!month ||
          shanghaiToday(new Date(o.settlement.confirmedAt)).startsWith(month)),
    );
    const received = paid.reduce(
      (s, o) => s + (o.settlement?.cashReceivedCents || 0),
      0,
    );
    // Demo records have no dated refund ledger; never invent a refund date.
    const refund = month
      ? 0
      : orders.reduce(
          (s, o) =>
            s + ((o as Order & { refundedCents?: number }).refundedCents || 0),
          0,
        );
    const net = received - refund,
      supplies = sum(paper) + sum(ink);
    return {
      month,
      confirmedReceivedCents: received,
      refundCents: refund,
      netReceivedCents: net,
      paperPurchaseCents: sum(paper),
      inkPurchaseCents: sum(ink),
      otherExpenseCents: sum(active.filter((e) => e.category === "OTHER")),
      consumablesBalanceCents: net - supplies,
      paidWagesCents: 0,
      balanceCents:
        net - supplies - sum(active.filter((e) => e.category === "OTHER")),
      paperSheets,
      paperUnitYuan: paperSheets
        ? (sum(paper) / 100 / paperSheets).toFixed(4)
        : null,
      legacyArchiveCount:
        month &&
        orders.some(
          (o) => (o as Order & { refundedCents?: number }).refundedCents,
        )
          ? 1
          : 0,
    } satisfies FinanceSummary;
  }
  if (path === "/admin/finance/expenses") {
    if (method === "GET") {
      const category = query.get("category");
      const page = Math.max(1, Number(query.get("page") || 1)),
        pageSize = Math.min(
          50,
          Math.max(1, Number(query.get("pageSize") || 20)),
        );
      const rows = entries
        .filter(
          (e) =>
            (!month || e.spentOn.startsWith(month)) &&
            (!category || e.category === category),
        )
        .sort(
          (a, b) =>
            b.spentOn.localeCompare(a.spentOn) ||
            b.createdAt.localeCompare(a.createdAt) ||
            b.id.localeCompare(a.id),
        );
      return {
        items: rows
          .slice((page - 1) * pageSize, page * pageSize)
          .map((e) => ({ ...e })),
        total: rows.length,
        page,
        pageSize,
      };
    }
    if (method === "POST") {
      if (
        !["PAPER", "INK", "OTHER"].includes(body.category) ||
        !Number.isInteger(body.amountCents)
      )
        throw new ApiError("VALIDATION_ERROR", "请填写有效支出", 422);
      expensePayload({
        category: body.category,
        spentOn: body.spentOn,
        amount: (body.amountCents / 100).toFixed(2),
        paperSheets:
          body.paperSheets === undefined ? "" : String(body.paperSheets),
        note: body.note || "",
      });
      if (body.category !== "PAPER" && body.paperSheets != null)
        throw new ApiError("VALIDATION_ERROR", "此类别不填写纸张张数", 422);
      const row: Expense = {
        id: crypto.randomUUID(),
        version: 1,
        category: body.category,
        spentOn: body.spentOn,
        amountCents: body.amountCents,
        paperSheets: body.paperSheets ?? null,
        note: body.note?.trim() || "",
        status: "ACTIVE",
        voidReason: "",
        createdAt: new Date().toISOString(),
      };
      entries.push(row);
      return { ...row };
    }
  }
  const match = path.match(/^\/admin\/finance\/expenses\/([^/]+)\/void$/);
  if (match && method === "POST") {
    const row = entries.find((e) => e.id === match[1]);
    if (!row) throw new ApiError("NOT_FOUND", "支出记录不存在", 404);
    if (row.version !== body.expectedVersion || row.status !== "ACTIVE")
      throw new ApiError("VERSION_CONFLICT", "记录已变化，请刷新后核对", 409);
    if (!String(body.reason || "").trim() || String(body.reason).length > 500)
      throw new ApiError("VALIDATION_ERROR", "请填写作废原因", 422);
    row.status = "VOIDED";
    row.voidReason = body.reason.trim();
    row.version++;
    return { ...row };
  }
  throw new ApiError("NOT_FOUND", "接口不存在", 404);
}
