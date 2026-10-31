import { ApiError } from "./api";

/**
 * Human labels for the machine codes the trash guard returns in
 * ``error.details.blockers`` (see backend/app/maintenance.py).
 */
const BLOCKER_LABELS: Record<string, string> = {
  ALREADY_DELETED: "对象已经被删除",
  UNMATCHED_RECEIPT: "存在未匹配的收款",
  ACTIVE_FULFILLMENT: "订单履约尚未完成",
  DELIVERY_NOT_COMPLETED: "配送尚未完成",
  PRINT_NOT_COMPLETED: "打印尚未完成",
  BINDING_NOT_COMPLETED: "装订尚未完成",
  PACKING_NOT_COMPLETED: "装袋尚未完成",
  ACTIVE_ORDER: "订单状态尚未终结",
  REFUND_REVIEW_PENDING: "退款仍待审核",
  ACTIVE_AFTER_SALE: "售后仍在处理中",
  OPEN_BUSINESS_EXCEPTION: "存在未关闭的业务异常",
  WAGE_UNSETTLED: "配送工资未发放（补录策略后还需记录发放）",
  SUPPLEMENTARY_ACTIVE: "补收款尚未结清",
  SUPPLEMENTARY_DISPUTE_OPEN: "补收款争议尚未解决",
  ACTIVE_DELIVERY: "批次仍处于配送中",
  BATCH_HAS_STARTED_EXECUTIONS: "批次内已有开始的配送执行",
};

export type TrashBlocker = { code: string; label: string };

/** Split ``WAGE_UNSETTLED:<entry id>`` into its stable code prefix. */
export function blockerCode(raw: string): string {
  const index = raw.indexOf(":");
  return index === -1 ? raw : raw.slice(0, index);
}

export function blockerLabel(raw: string): string {
  const code = blockerCode(raw);
  return BLOCKER_LABELS[code] || `未结业务（${raw}）`;
}

/**
 * Extract the deletion blockers carried by a 409 ``TRASH_BLOCKED`` response.
 *
 * The generic banner only shows "对象仍存在有效引用或未结业务", which does not
 * tell the admin what to finish first; the delete dialogs use this helper to
 * list the concrete reasons next to the action that failed.
 */
export function trashBlockers(error: unknown): TrashBlocker[] {
  if (!(error instanceof ApiError) || error.code !== "TRASH_BLOCKED") return [];
  const details = error.details as { blockers?: unknown } | null | undefined;
  const raw = Array.isArray(details?.blockers)
    ? details!.blockers.filter(
        (item): item is string => typeof item === "string",
      )
    : [];
  const seen = new Set<string>();
  const result: TrashBlocker[] = [];
  for (const item of raw) {
    const code = blockerCode(item);
    if (seen.has(code)) continue;
    seen.add(code);
    result.push({ code, label: blockerLabel(item) });
  }
  return result;
}

export function hasWageBlocker(blockers: TrashBlocker[]): boolean {
  return blockers.some((blocker) => blocker.code === "WAGE_UNSETTLED");
}
