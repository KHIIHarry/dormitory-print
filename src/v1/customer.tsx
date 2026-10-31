import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  Download,
  FileClock,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  Trash2,
  Undo2,
} from "lucide-react";
import {
  Button,
  Empty,
  ErrorState,
  Field,
  LinkButton,
  Loading,
  Notice,
  PageHeading,
  Panel,
} from "../components/ui";
import { TaskStatus } from "../components/feedback";
import { canOrderAction, effectiveOrderAccess } from "../growth/order-security";
import { useIdentity } from "../growth/identity";
import {
  ApiError,
  isUncertainSubmission,
  money,
  mutation,
  request,
} from "../lib/api";
import { DEMO, useStore } from "../lib/store";
import type { Order } from "../lib/types";
import "./customer.css";

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "/api/v1").replace(
  /\/$/,
  "",
);

/* ------------------------------------------------------------------------- */
/* Local V1 DTOs. Shared src/lib/types.ts is owned by Codex; these interfaces  */
/* mirror the approved OpenAPI contract until Codex exports the shared types. */
/* ------------------------------------------------------------------------- */

export type AfterSaleSource = "CUSTOMER" | "ADMIN";
export type AfterSaleResponsibility = "CUSTOMER" | "MERCHANT";
export type AfterSaleSolution = "REFUND" | "REPRINT";

export type AfterSaleCase = {
  id: string;
  orderId: string;
  version: number;
  status: string;
  source: AfterSaleSource;
  responsibility: AfterSaleResponsibility;
  solution: AfterSaleSolution;
  reason: string;
  suggestedCents: number;
  approvedCents: number | null;
  adjustmentBasis: string;
  data: Record<string, unknown>;
  createdAt: string;
  /** Optional extensions requested from Codex's after-sales module. */
  allowedActions?: string[];
  refund?: {
    amountCents: number;
    registeredAt?: string;
    registeredBy?: string;
  };
};

export type AfterSaleList = {
  items: AfterSaleCase[];
  nextCursor?: string | null;
};

type SlotBrief = {
  id?: string;
  name?: string;
  date?: string;
  weekday?: string;
  start?: string;
  end?: string;
};

type V1Order = Order & {
  actualStartedAt?: string | null;
  closeAt?: string | null;
  data?: Record<string, unknown>;
  requestedSlot?: SlotBrief | null;
  confirmedSlot?: SlotBrief | null;
  requestedSlotSnapshot?: SlotBrief | null;
  confirmedSlotSnapshot?: SlotBrief | null;
  slotProposal?: SlotBrief | null;
  requestedSlotId?: string | null;
  confirmedSlotId?: string | null;
  slotStatus?: string | null;
  capabilities?: Record<string, unknown>;
};

type ExportJob = {
  id: string;
  status: string;
  kind?: string;
  createdAt?: string;
  expiresAt?: string | null;
  error?: string | null;
  downloadUrl?: string | null;
  rowCount?: number | null;
  truncated?: boolean;
};

function deletionBlockerLabel(blocker: string) {
  if (blocker.startsWith("LAST_SUPER_ADMIN"))
    return "唯一超级管理员须先完成权限交接";
  if (blocker.startsWith("ORDER") || blocker.startsWith("ACTIVE_ORDER"))
    return "仍有未完成的订单";
  if (blocker.includes("REFUND")) return "仍有待处理退款";
  if (blocker.includes("AFTER")) return "仍有未结束的售后";
  if (blocker.includes("WAGE") || blocker.includes("PAYROLL"))
    return "仍有待结算工资";
  return "仍有待处理的业务或账务事项";
}

type DeletionRequestView = {
  id: string;
  status: string;
  createdAt?: string;
  requestedAt?: string;
  reason?: string;
  blockers?: string[];
  reviewedAt?: string | null;
  recoveryUntil?: string | null;
};

type DeletionRequestList = {
  items: DeletionRequestView[];
  nextCursor?: string | null;
};

const orderActionCandidates = [
  // Canonical action from app/commerce.py order_view.
  "ORDER_AFTER_SALES",
  // Accepted aliases while Codex finalises the shared order DTO.
  "AFTER_SALE_REQUEST",
  "AFTER_SALES_REQUEST",
];

const terminalAfterSaleStatuses = new Set([
  "COMPLETED",
  "CLOSED",
  "REJECTED",
  "CANCELLED",
  "CANCELED",
  "REFUNDED",
]);

const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : "操作暂时未完成，请稍后重试。";

const formatTime = (value?: string | null) => {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" });
};

const sourceLabel = (source: string) =>
  source === "CUSTOMER"
    ? "客户申请"
    : source === "ADMIN"
      ? "管理员代建"
      : source;

const responsibilityLabel = (value: string) =>
  value === "CUSTOMER"
    ? "客户自身原因"
    : value === "MERCHANT"
      ? "商家责任"
      : value;

const solutionLabel = (value: string) =>
  value === "REFUND" ? "退款" : value === "REPRINT" ? "免费重做 / 补打" : value;

export function afterSaleStatusLabel(status: string) {
  // Exact statuses from backend/app/after_sales.py.
  const labels: Record<string, string> = {
    REQUESTED: "已提交，等待管理员核实",
    REVIEW_PENDING: "管理员核实中",
    REFUND_APPROVED: "已批准退款，等待管理员线下登记",
    REFUNDED: "管理员已登记退款",
    REPRINTING: "免费重做进行中",
    REPRINT_READY: "重做完成，等待重新交付",
    COMPLETED: "已完成",
    REJECTED: "未通过核实",
    CANCELLED: "已取消",
    CANCELED: "已取消",
    CLOSED: "已关闭",
  };
  return labels[status] || `处理中（${status}）`;
}

function caseAllowedActions(item: AfterSaleCase) {
  if (Array.isArray(item.allowedActions)) return item.allowedActions;
  const fromData = item.data?.allowedActions;
  return Array.isArray(fromData)
    ? fromData.filter((value): value is string => typeof value === "string")
    : null;
}

/* ------------------------------------------------------------------------- */
/* Small shared request state: every write exposes pending, error, uncertain    */
/* and an idempotent replay/retry. Reads expose error + retry.                  */
/* ------------------------------------------------------------------------- */

function useV1Action() {
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [uncertain, setUncertain] = useState(false);
  const [success, setSuccess] = useState("");
  const last = useRef<{ key: string; action: () => Promise<void> } | null>(
    null,
  );
  const run = useCallback(
    async (key: string, action: () => Promise<void>) => {
      if (pending) return false;
      if (!navigator.onLine) {
        setError("当前离线，联网后再执行此操作。");
        return false;
      }
      last.current = { key, action };
      setPending(key);
      setError("");
      setUncertain(false);
      setSuccess("");
      try {
        await action();
        return true;
      } catch (e) {
        if (isUncertainSubmission(e)) {
          setUncertain(true);
          setError(
            "提交结果待核实：请求响应未确认。请先查询实际状态，勿重复提交；重试会安全复用同一操作标识。",
          );
        } else setError(errorMessage(e));
        return false;
      } finally {
        setPending(null);
      }
    },
    [pending],
  );
  const retry = useCallback(() => {
    const operation = last.current;
    if (operation) void run(operation.key, operation.action);
  }, [run]);
  return {
    pending,
    error,
    uncertain,
    success,
    setSuccess,
    setError,
    run,
    retry,
    clear: () => {
      setError("");
      setUncertain(false);
      setSuccess("");
    },
  };
}

function TaskFeedback({
  state,
  label,
}: {
  state: ReturnType<typeof useV1Action>;
  label: string;
}) {
  return (
    <>
      {state.pending && <TaskStatus label={`${label}…`} />}
      {state.error && (
        <div role="alert" className="v1-feedback">
          <Notice type={state.uncertain ? "warning" : "error"}>
            {state.error}
            {state.uncertain && (
              <>
                {" "}
                <button className="text-button" onClick={state.retry}>
                  安全重试
                </button>
              </>
            )}
          </Notice>
        </div>
      )}
      {state.success && (
        <div role="status" className="v1-feedback">
          <Notice type="success">{state.success}</Notice>
        </div>
      )}
    </>
  );
}

/* ------------------------------------------------------------------------- */
/* After-sales                                                                */
/* ------------------------------------------------------------------------- */

export function AfterSalesPanel({
  orderId,
  order,
}: {
  orderId: string;
  order?: Order;
}) {
  const { data } = useStore();
  const { customer, orderAccess } = useIdentity();
  const stored = data.orders.find((item) => item.id === orderId);
  const currentOrder = (order || stored) as V1Order | undefined;
  const [cases, setCases] = useState<AfterSaleCase[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [responsibility, setResponsibility] =
    useState<AfterSaleResponsibility>("CUSTOMER");
  const [solution, setSolution] = useState<AfterSaleSolution>("REFUND");
  const [reason, setReason] = useState("");
  const [affected, setAffected] = useState<Record<string, number>>({});
  const [choiceFailed, setChoiceFailed] = useState<Record<string, string>>({});
  const action = useV1Action();
  const sequence = useRef(0);

  const load = useCallback(
    async (append?: string) => {
      const seq = ++sequence.current;
      setLoading(true);
      setLoadError("");
      try {
        const result = await request<AfterSaleList>(
          `/orders/${encodeURIComponent(orderId)}/after-sales${append ? `?cursor=${encodeURIComponent(append)}` : ""}`,
        );
        if (seq !== sequence.current) return;
        setCases((old) => (append ? [...old, ...result.items] : result.items));
        setCursor(result.nextCursor || null);
      } catch (e) {
        if (seq !== sequence.current) return;
        setLoadError(errorMessage(e));
      } finally {
        if (seq === sequence.current) setLoading(false);
      }
    },
    [orderId],
  );

  useEffect(() => {
    setFormOpen(false);
    setCases([]);
    setCursor(null);
    setReason("");
    setAffected({});
    setChoiceFailed({});
    action.clear();
    if (DEMO) {
      setLoading(false);
      setLoadError("");
      return;
    }
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderId]);

  const access = currentOrder
    ? effectiveOrderAccess(currentOrder, customer, orderAccess)
    : null;
  const allowedAction = currentOrder
    ? orderActionCandidates.find((name) =>
        canOrderAction(currentOrder, name, access),
      )
    : undefined;
  const canRequest = !!allowedAction;
  const actualStarted = !!(
    currentOrder?.actualStartedAt || currentOrder?.data?.actualStartedAt
  );
  const lines = currentOrder?.quote?.lines || [];

  const openForm = () => {
    action.clear();
    setResponsibility("CUSTOMER");
    setSolution("REFUND");
    setReason("");
    setAffected({});
    setFormOpen(true);
  };

  const submit = async () => {
    const trimmed = reason.trim();
    if (trimmed.length < 2) {
      action.setError("请填写至少 2 个字的原因，便于管理员核实。");
      return;
    }
    if (responsibility === "CUSTOMER" && actualStarted) {
      action.setError(
        "订单已登记实际开印，自身原因不支持退款。如属商家错印、漏印、丢失或损坏，请选择商家责任。",
      );
      return;
    }
    const affectedItems = Object.entries(affected)
      .filter(([, quantity]) => quantity > 0)
      .map(([lineKey, quantity]) => ({ lineKey, quantity }));
    await action.run("create", async () => {
      await mutation<AfterSaleCase>(
        `/orders/${encodeURIComponent(orderId)}/after-sales`,
        {
          expectedVersion: currentOrder?.version,
          responsibility,
          solution,
          reason: trimmed,
          ...(affectedItems.length ? { affectedItems } : {}),
        },
      );
      setFormOpen(false);
      action.setSuccess(
        "售后申请已提交。是否批准、退款金额与线下退款登记由管理员核实后处理。",
      );
      await load();
    });
  };

  const choose = async (item: AfterSaleCase, next: AfterSaleSolution) => {
    setChoiceFailed((old) => ({ ...old, [item.id]: "" }));
    const ok = await action.run(`choice:${item.id}`, async () => {
      await mutation<AfterSaleCase>(
        `/orders/${encodeURIComponent(orderId)}/after-sales/${encodeURIComponent(item.id)}/choice`,
        { expectedVersion: item.version, solution: next },
      );
      action.setSuccess(
        next === "REFUND"
          ? "已选择退款，等待管理员线下登记实际退款。"
          : "已选择免费重做 / 补打，等待管理员安排。",
      );
      await load();
    });
    if (!ok && action.error)
      setChoiceFailed((old) => ({ ...old, [item.id]: action.error }));
  };

  const chooseAction = (item: AfterSaleCase) => {
    const actions = caseAllowedActions(item);
    if (actions)
      return actions.some((name) =>
        ["CHOOSE_SOLUTION", "SELECT_SOLUTION", "CUSTOMER_CHOICE"].includes(
          name,
        ),
      );
    // backend/app/after_sales.py accepts a customer choice while the case is
    // REQUESTED or REVIEW_PENDING and the responsibility is MERCHANT. A
    // server-provided allowedActions array always wins.
    return (
      item.responsibility === "MERCHANT" &&
      ["REQUESTED", "REVIEW_PENDING"].includes(item.status)
    );
  };

  return (
    <Panel
      title="售后与退款"
      action={
        canRequest ? (
          <button
            className="text-button"
            onClick={() => (formOpen ? setFormOpen(false) : openForm())}
          >
            {formOpen ? "收起申请" : "申请售后 / 取消退款"}
          </button>
        ) : undefined
      }
    >
      {DEMO ? (
        <Notice type="warning">
          当前为演示模式。售后申请、审核与退款登记必须连接真实
          API；演示环境不会伪造提交成功。
        </Notice>
      ) : (
        <>
          <Notice>
            已提交、已批准与管理员已登记退款是不同阶段：提交后需管理员核实；批准金额可能按受影响项目调整并必须填写依据；实际线下退款由管理员单独登记。已付款订单的原文件、打印参数和应付快照不会被修改。
          </Notice>
          {currentOrder && !canRequest && (
            <Notice>
              {!currentOrder.settlement
                ? "未结算订单请直接在订单费用中撤销，无需申请售后；如已实际转款但后台未确认，请联系管理员核对收款。"
                : access
                  ? "当前订单链接没有售后申请权限（默认只读）。请联系管理员补发授权，或使用拥有该订单的账号操作。"
                  : "当前订单状态或服务端 allowedActions 未开放售后申请；如已完成履约或已开印，请直接联系管理员核实商家责任。"}
            </Notice>
          )}
          {actualStarted && (
            <Notice type="warning">
              服务端已登记实际开印：自身原因不支持退款；商家错印、漏印、丢失或损坏仍可申请核实。
            </Notice>
          )}
          {formOpen && canRequest && (
            <div className="v1-form">
              <Field label="申请类型">
                <div className="segmented-control">
                  <button
                    type="button"
                    className={responsibility === "CUSTOMER" ? "selected" : ""}
                    onClick={() => {
                      setResponsibility("CUSTOMER");
                      setSolution("REFUND");
                    }}
                  >
                    自身原因 · 取消 / 退款
                  </button>
                  <button
                    type="button"
                    className={responsibility === "MERCHANT" ? "selected" : ""}
                    onClick={() => setResponsibility("MERCHANT")}
                  >
                    商家责任 · 补打 / 退款
                  </button>
                </div>
              </Field>
              <Field
                label="期望处理方式"
                hint={
                  responsibility === "CUSTOMER"
                    ? "自身原因仅支持申请取消并退款；是否可退以管理员核实实际开印为准。"
                    : "商家核实责任后，由你选择免费重做 / 补打，或退受影响部分。"
                }
              >
                <div className="segmented-control">
                  <button
                    type="button"
                    className={solution === "REFUND" ? "selected" : ""}
                    disabled={responsibility === "CUSTOMER" && actualStarted}
                    onClick={() => setSolution("REFUND")}
                  >
                    退款
                  </button>
                  <button
                    type="button"
                    className={solution === "REPRINT" ? "selected" : ""}
                    disabled={responsibility === "CUSTOMER"}
                    onClick={() => setSolution("REPRINT")}
                  >
                    免费重做 / 补打
                  </button>
                </div>
              </Field>
              <Field
                label="申请原因 *"
                hint="说明具体问题，如错印、漏印、缺页、损坏或取消原因。"
              >
                <textarea
                  rows={3}
                  maxLength={500}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="请描述实际问题，方便管理员核实"
                />
              </Field>
              {responsibility === "MERCHANT" && lines.length > 0 && (
                <Field
                  label="受影响项目（选填）"
                  hint="按报价行选择受影响数量；不选则由管理员核实全部或部分项目。"
                >
                  <div className="v1-affected">
                    {lines.map((line) => (
                      <div className="v1-affected-row" key={line.key}>
                        <label className="checkbox-label">
                          <input
                            type="checkbox"
                            checked={line.key in affected}
                            onChange={(e) =>
                              setAffected((old) => {
                                const next = { ...old };
                                if (e.target.checked) next[line.key] = 1;
                                else delete next[line.key];
                                return next;
                              })
                            }
                          />
                          <span>
                            {line.service === "A4"
                              ? "A4 打印"
                              : line.service === "PHOTO"
                                ? "照片"
                                : "装订"}{" "}
                            · {line.quantity} 张/册 · {money(line.amountCents)}
                          </span>
                        </label>
                        {line.key in affected && (
                          <input
                            aria-label={`受影响数量 ${line.key}`}
                            type="number"
                            min={1}
                            max={line.quantity}
                            value={affected[line.key]}
                            onChange={(e) =>
                              setAffected((old) => ({
                                ...old,
                                [line.key]: Math.max(
                                  1,
                                  Math.min(
                                    line.quantity,
                                    Number(e.target.value) || 1,
                                  ),
                                ),
                              }))
                            }
                          />
                        )}
                      </div>
                    ))}
                  </div>
                </Field>
              )}
              <TaskFeedback state={action} label="正在提交售后申请" />
              <div className="v1-actions">
                <Button
                  busy={action.pending === "create"}
                  busyLabel="正在提交…"
                  disabled={!!action.pending}
                  onClick={() => void submit()}
                >
                  提交售后申请
                </Button>
                <Button
                  variant="secondary"
                  disabled={!!action.pending}
                  onClick={() => setFormOpen(false)}
                >
                  取消
                </Button>
              </div>
            </div>
          )}
          {loading && cases.length === 0 ? (
            <Loading label="正在读取售后记录…" />
          ) : loadError ? (
            <ErrorState message={loadError} retry={() => void load()} />
          ) : cases.length === 0 ? (
            <Empty
              title="还没有售后记录"
              text="如资料有错印、漏印、丢失或损坏，可在订单允许时提交售后申请。"
            />
          ) : (
            <div className="v1-cases">
              {cases.map((item) => {
                const refund = item.refund;
                const refundCents =
                  refund?.amountCents ??
                  (typeof item.data?.refundAmountCents === "number"
                    ? (item.data.refundAmountCents as number)
                    : null);
                const refundRecordedAt =
                  refund?.registeredAt ||
                  (typeof item.data?.refundRecordedAt === "string"
                    ? (item.data.refundRecordedAt as string)
                    : undefined);
                const terminal = terminalAfterSaleStatuses.has(item.status);
                return (
                  <article className="v1-case" key={item.id}>
                    <header>
                      <div>
                        <strong>{solutionLabel(item.solution)}</strong>
                        <span className="v1-case-meta">
                          {sourceLabel(item.source)} ·{" "}
                          {responsibilityLabel(item.responsibility)} ·{" "}
                          {formatTime(item.createdAt)}
                        </span>
                      </div>
                      <span
                        className={`v1-status ${terminal ? "terminal" : ""}`}
                        data-status={item.status}
                      >
                        {afterSaleStatusLabel(item.status)}
                      </span>
                    </header>
                    <p className="v1-reason">{item.reason}</p>
                    <dl className="v1-amounts">
                      <div>
                        <dt>系统建议金额</dt>
                        <dd>{money(item.suggestedCents)}</dd>
                      </div>
                      <div>
                        <dt>管理员批准金额</dt>
                        <dd>
                          {item.approvedCents === null
                            ? "尚未批准"
                            : money(item.approvedCents)}
                        </dd>
                      </div>
                      <div>
                        <dt>线下退款登记</dt>
                        <dd>
                          {refundCents === null
                            ? "尚未登记"
                            : `${money(refundCents)}${
                                refundRecordedAt
                                  ? ` · ${formatTime(refundRecordedAt)}`
                                  : ""
                              }`}
                        </dd>
                      </div>
                    </dl>
                    {item.adjustmentBasis && (
                      <p className="v1-basis">
                        调整依据：{item.adjustmentBasis}
                      </p>
                    )}
                    {item.approvedCents !== null &&
                      item.approvedCents !== item.suggestedCents && (
                        <p className="v1-basis">
                          批准金额与系统建议相差{" "}
                          {money(
                            Math.abs(item.approvedCents - item.suggestedCents),
                          )}
                          ，以管理员填写依据为准。
                        </p>
                      )}
                    {chooseAction(item) && (
                      <div className="v1-choice">
                        <p>管理员已核实商家责任，请选择处理方式：</p>
                        <div className="v1-actions">
                          <Button
                            busy={action.pending === `choice:${item.id}`}
                            busyLabel="正在提交…"
                            disabled={!!action.pending}
                            onClick={() => void choose(item, "REPRINT")}
                          >
                            <RotateCcw size={16} />
                            免费重做 / 补打
                          </Button>
                          <Button
                            variant="secondary"
                            busy={action.pending === `choice:${item.id}`}
                            disabled={!!action.pending}
                            onClick={() => void choose(item, "REFUND")}
                          >
                            <Undo2 size={16} />
                            退受影响部分
                          </Button>
                        </div>
                        {choiceFailed[item.id] && (
                          <Notice type="error">{choiceFailed[item.id]}</Notice>
                        )}
                      </div>
                    )}
                  </article>
                );
              })}
              {cursor && (
                <Button
                  variant="secondary"
                  disabled={loading}
                  onClick={() => void load(cursor || undefined)}
                >
                  {loading ? "正在加载…" : "加载更多记录"}
                </Button>
              )}
            </div>
          )}
        </>
      )}
    </Panel>
  );
}

export function CustomerAfterSalesPage() {
  const { id } = useParams();
  const { data, resources, refresh } = useStore();
  const order = data.orders.find((item) => item.id === id);
  const loading = resources.orders?.loading && !resources.orders?.hasData;
  return (
    <main className="content-page">
      <Link className="back-link" to={id ? `/u/orders/${id}` : "/u/orders"}>
        <ArrowLeft size={16} />
        返回订单详情
      </Link>
      <PageHeading
        title="售后与退款"
        description="提交申请，查看核实、批准与管理员线下退款登记的真实状态。"
      />
      {!id ? (
        <Empty title="缺少订单" text="请从订单详情进入售后申请。" />
      ) : loading && !order ? (
        <Loading label="正在读取订单…" />
      ) : !order ? (
        <ErrorState
          message={
            resources.orders?.error || "这份订单暂不可访问，或链接已失效。"
          }
          retry={refresh}
        />
      ) : (
        <AfterSalesPanel orderId={order.id} order={order} />
      )}
    </main>
  );
}

/* ------------------------------------------------------------------------- */
/* Personal data export and account deletion request                          */
/* ------------------------------------------------------------------------- */

export function PersonalDataPanel() {
  const navigate = useNavigate();
  const identity = useIdentity();
  const verified =
    identity.customer.authKind === "REGISTERED" &&
    !!identity.customer.account?.verified;
  const [job, setJob] = useState<ExportJob | null>(null);
  const [deletions, setDeletions] = useState<DeletionRequestView[]>([]);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [recoveryMessage, setRecoveryMessage] = useState("");
  const action = useV1Action();
  const sequence = useRef(0);

  const loadDeletions = useCallback(async () => {
    const seq = ++sequence.current;
    setListLoading(true);
    setListError("");
    try {
      const result = await request<DeletionRequestList | DeletionRequestView[]>(
        "/customer/account/deletion-requests",
      );
      if (seq !== sequence.current) return;
      const items = Array.isArray(result) ? result : result.items;
      setDeletions(Array.isArray(items) ? items : []);
    } catch (e) {
      if (seq !== sequence.current) return;
      setListError(errorMessage(e));
    } finally {
      if (seq === sequence.current) setListLoading(false);
    }
  }, []);

  useEffect(() => {
    if (DEMO || !verified) {
      setListLoading(false);
      return;
    }
    void loadDeletions();
  }, [DEMO, verified, loadDeletions]);

  useEffect(() => {
    if (!job || !["QUEUED", "PROCESSING"].includes(job.status)) return;
    let active = true;
    const poll = async () => {
      if (!active || document.hidden || !navigator.onLine) return;
      try {
        const next = await request<ExportJob>(
          `/customer/account/exports/${encodeURIComponent(job.id)}`,
        );
        if (active) setJob(next);
      } catch {
        // The job remains in its last confirmed state; the next tick retries.
      }
    };
    const timer = window.setInterval(poll, 3000);
    const onVisible = () => void poll();
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("online", onVisible);
    return () => {
      active = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("online", onVisible);
    };
  }, [job]);

  const requestExport = async () => {
    await action.run("export", async () => {
      const created = await mutation<{ exportJobId: string }>(
        "/customer/account/exports",
        {},
      );
      setJob({ id: created.exportJobId, status: "QUEUED" });
      try {
        const next = await request<ExportJob>(
          `/customer/account/exports/${encodeURIComponent(created.exportJobId)}`,
        );
        setJob(next);
      } catch {
        // The creation result is authoritative; the status poll will retry.
      }
      action.setSuccess(
        "导出申请已提交，正在服务端生成。生成完成后可下载，文件到期会自动失效。",
      );
    });
  };

  const requestDeletion = async () => {
    if (!confirmed) {
      action.setError("请先确认已了解注销与 7 天恢复期规则。");
      return;
    }
    await action.run("deletion", async () => {
      const result = await mutation<{
        status: string;
        blockers: string[];
        message: string;
        recoveryUntil?: string | null;
      }>("/customer/account/deletion-request", {
        reason: reason.trim() || "用户自助申请注销",
      });
      if (result.status === "BLOCKED") {
        const reasons = (result.blockers || []).map(deletionBlockerLabel);
        action.setError(
          `暂不能注销：${[...new Set(reasons)].join("；") || result.message}`,
        );
        await loadDeletions();
        return;
      }
      if (result.status !== "RECOVERY")
        throw new Error("注销状态需要核实，请刷新记录后再操作。");
      setReason("");
      setConfirmed(false);
      setRecoveryMessage(
        `${result.message}${result.recoveryUntil ? ` 恢复截止：${formatTime(result.recoveryUntil)}。` : ""}`,
      );
      setDeletions([]);
      identity.clearCustomer();
      navigate("/u/account/login", {
        replace: true,
        state: {
          deletionRecovery:
            result.message +
            (result.recoveryUntil
              ? ` 恢复截止：${formatTime(result.recoveryUntil)}。`
              : ""),
        },
      });
    });
  };

  const pendingDeletion = deletions.find((item) =>
    ["RECOVERY"].includes(item.status),
  );
  const downloadUrl = job?.downloadUrl
    ? job.downloadUrl
    : job
      ? `${API_BASE}/customer/account/exports/${encodeURIComponent(job.id)}/download`
      : "";
  const exportStatus =
    job?.status === "READY"
      ? "已就绪，可下载"
      : job?.status === "PROCESSING"
        ? "生成中"
        : job?.status === "QUEUED"
          ? "排队中"
          : job?.status === "FAILED"
            ? "生成失败"
            : job?.status === "EXPIRED"
              ? "已过期"
              : job
                ? `状态 ${job.status}`
                : "";

  return (
    <div className="v1-account-data">
      <Panel title="导出我的个人资料">
        <p>
          由服务端生成受控导出，仅包含账号资料与业务记录白名单字段；不包含密码、会话凭据、原始打印文件或他人资料。导出文件有有效期，请及时下载。
        </p>
        {DEMO ? (
          <Notice type="warning">
            演示模式不提供真实个人资料导出。连接真实服务后才能申请、查询和下载。
          </Notice>
        ) : !verified ? (
          <Notice>登录并验证邮箱后才能申请导出本人资料。</Notice>
        ) : (
          <>
            <TaskFeedback state={action} label="正在申请资料导出" />
            <div className="v1-actions">
              <Button
                busy={action.pending === "export"}
                busyLabel="正在申请…"
                disabled={!!action.pending || !!job}
                onClick={() => void requestExport()}
              >
                <Download size={16} />
                申请导出我的资料
              </Button>
              {job && (
                <Button
                  variant="secondary"
                  disabled={!!action.pending}
                  onClick={() => {
                    setJob(null);
                    action.clear();
                  }}
                >
                  重新申请
                </Button>
              )}
            </div>
            {job && (
              <div className="v1-job" aria-live="polite">
                <div>
                  <FileClock size={18} />
                  <span>
                    <strong>{exportStatus}</strong>
                    <small>导出任务 {job.id}</small>
                    {job.expiresAt && (
                      <small>有效期至 {formatTime(job.expiresAt)}</small>
                    )}
                    {typeof job.rowCount === "number" && (
                      <small>
                        记录 {job.rowCount} 行
                        {job.truncated ? "（已达导出上限，内容被截断）" : ""}
                      </small>
                    )}
                    {job.error && <small role="alert">{job.error}</small>}
                  </span>
                </div>
                {job.status === "READY" && (
                  <a className="btn secondary" href={downloadUrl} download>
                    <Download size={16} />
                    下载资料
                  </a>
                )}
                {job.status === "FAILED" && (
                  <Button
                    variant="secondary"
                    disabled={!!action.pending}
                    onClick={() => void requestExport()}
                  >
                    <RefreshCw size={15} />
                    重试导出
                  </Button>
                )}
              </div>
            )}
          </>
        )}
      </Panel>

      <Panel title="申请注销账号">
        <p>
          无需管理员审核。所有订单已结束且没有待处理的售后、退款或账务事项时，可自助注销。注销后退出所有设备；7
          天内使用原账号密码登录即可恢复，期满后按数据保留规则处理。唯一超级管理员须先完成管理权限交接。
        </p>
        {recoveryMessage && (
          <Notice type="success">
            {recoveryMessage} <Link to="/u/account/login">前往登录恢复</Link>
          </Notice>
        )}
        {DEMO ? (
          <Notice type="warning">
            演示模式不执行真实注销。正式环境满足条件后立即进入 7 天恢复期。
          </Notice>
        ) : !verified ? (
          <Notice>登录并验证邮箱后才能提交注销申请。</Notice>
        ) : (
          <>
            <TaskFeedback state={action} label="正在提交注销申请" />
            {pendingDeletion ? (
              <div className="v1-job" role="status">
                <div>
                  <ShieldAlert size={18} />
                  <span>
                    <strong>账号处于 7 天恢复期</strong>
                    <small>
                      提交于{" "}
                      {formatTime(
                        pendingDeletion.createdAt ||
                          pendingDeletion.requestedAt,
                      )}
                    </small>
                    {(pendingDeletion.blockers || []).length > 0 && (
                      <small>
                        待处理事项：
                        {pendingDeletion.blockers
                          ?.map(deletionBlockerLabel)
                          .join("、")}
                      </small>
                    )}
                  </span>
                </div>
              </div>
            ) : (
              <div className="v1-form">
                <Field label="注销原因（选填）">
                  <textarea
                    rows={2}
                    maxLength={300}
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    placeholder="可说明注销原因"
                  />
                </Field>
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={confirmed}
                    onChange={(e) => setConfirmed(e.target.checked)}
                  />
                  <span>
                    我已了解注销后将退出所有设备，7
                    天内登录可恢复，期满后无法通过登录找回。
                  </span>
                </label>
                <div className="v1-actions">
                  <Button
                    variant="danger"
                    busy={action.pending === "deletion"}
                    busyLabel="正在提交…"
                    disabled={!!action.pending || !confirmed}
                    onClick={() => void requestDeletion()}
                  >
                    <Trash2 size={16} />
                    提交注销申请
                  </Button>
                </div>
              </div>
            )}
          </>
        )}
      </Panel>

      <Panel
        title="我的注销申请"
        action={
          !DEMO && verified ? (
            <button
              className="text-button"
              disabled={listLoading}
              onClick={() => void loadDeletions()}
            >
              <RefreshCw size={14} />
              刷新
            </button>
          ) : undefined
        }
      >
        {DEMO ? (
          <Notice>演示模式不读取真实注销申请。</Notice>
        ) : !verified ? (
          <Notice>登录后查看本人注销申请。</Notice>
        ) : listLoading && deletions.length === 0 ? (
          <Loading label="正在读取注销申请…" />
        ) : listError ? (
          <ErrorState message={listError} retry={() => void loadDeletions()} />
        ) : deletions.length === 0 ? (
          <Empty
            title="还没有注销申请"
            text="如确需注销，可在上方提交；提交后不会立即删除。"
          />
        ) : (
          <ul className="v1-deletions">
            {deletions.map((item) => (
              <li key={item.id}>
                <span
                  className={`v1-status ${
                    ["COMPLETED", "DELETED"].includes(item.status)
                      ? "terminal"
                      : ""
                  }`}
                >
                  {item.status === "RECOVERY"
                    ? "7 天恢复期"
                    : item.status === "RECOVERED"
                      ? "已登录恢复"
                      : item.status === "BLOCKED"
                        ? "存在未完成事项"
                        : item.status === "FINALIZED"
                          ? "恢复期已结束"
                          : item.status === "REQUESTED" ||
                              item.status === "PENDING_REVIEW"
                            ? "等待管理员审核"
                            : item.status === "UNDER_REVIEW"
                              ? "管理员审核中"
                              : item.status === "APPROVED"
                                ? "审核通过，等待执行"
                                : item.status === "COMPLETED" ||
                                    item.status === "DELETED"
                                  ? "已按保留规则处理"
                                  : item.status === "REJECTED"
                                    ? "未通过审核"
                                    : item.status === "CANCELLED"
                                      ? "已撤回"
                                      : item.status}
                </span>
                <span>
                  提交于 {formatTime(item.createdAt || item.requestedAt)}
                  {item.reason ? ` · ${item.reason}` : ""}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}

function AccountDataGate({ children }: { children: React.ReactNode }) {
  const { customer } = useIdentity();
  return customer.authKind === "REGISTERED" && customer.account?.verified ? (
    <>{children}</>
  ) : (
    <Panel>
      <Empty
        title="登录已验证的账号"
        text="个人资料导出与注销申请需要邮箱验证的本人账号。"
        action={
          <div className="account-actions">
            <Link className="btn" to="/u/account/login">
              登录 / 注册
            </Link>
            <LinkButton to="/u/">继续打印</LinkButton>
          </div>
        }
      />
    </Panel>
  );
}

export function CustomerAccountDataPage() {
  return (
    <main className="content-page">
      <Link className="back-link" to="/u/account">
        <ArrowLeft size={16} />
        返回账号设置
      </Link>
      <PageHeading
        title="个人资料与账号数据"
        description="导出个人资料，或在完成所有订单后自助注销账号。"
      />
      <AccountDataGate>
        <PersonalDataPanel />
      </AccountDataGate>
    </main>
  );
}

/* ------------------------------------------------------------------------- */
/* Order text helpers reused by src/customer/orders.tsx and review.tsx        */
/* ------------------------------------------------------------------------- */

export function requestedSlotOf(order: Order) {
  const value = order as V1Order;
  return value.requestedSlot || value.requestedSlotSnapshot || null;
}

export function confirmedSlotOf(order: Order) {
  const value = order as V1Order;
  return value.confirmedSlot || value.confirmedSlotSnapshot || null;
}

export function slotBlockLabel(
  slot: SlotBrief | null | undefined,
  fallback?: string,
) {
  if (!slot && !fallback) return "";
  if (!slot) return fallback || "";
  const date = slot.date || "";
  return [
    date,
    slot.weekday,
    slot.name,
    slot.start && slot.end ? `${slot.start}–${slot.end}` : "",
  ]
    .filter(Boolean)
    .join(" ");
}

export function closeDeadlineOf(
  order: Order,
  unpaidTimeoutMinutes?: number | null,
) {
  const value = order as V1Order;
  const raw = value.closeAt || value.data?.closeAt;
  if (typeof raw === "string" && raw) return raw;
  if (
    order.status === "PENDING_CONFIRMATION" &&
    typeof unpaidTimeoutMinutes === "number" &&
    unpaidTimeoutMinutes > 0
  )
    return new Date(
      Date.parse(order.createdAt) + unpaidTimeoutMinutes * 60_000,
    ).toISOString();
  return null;
}

export function autoCloseNotice(
  order: Order,
  unpaidTimeoutMinutes?: number | null,
) {
  if (order.status !== "PENDING_CONFIRMATION") return null;
  const deadline = closeDeadlineOf(order, unpaidTimeoutMinutes);
  if (deadline)
    return `若 ${formatTime(deadline)} 前仍未人工确认收款，订单将按配置期限自动关闭（以服务端关闭结果为准）。`;
  return "未配置未付款自动关闭期限；如长期未确认收款，请联系管理员。";
}
