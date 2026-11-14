import { formatDisplayTime } from "../lib/display-format";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  FileClock,
  RefreshCw,
  RotateCcw,
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

export type SlotBrief = {
  campusId?: string | null;
  campusName?: string | null;
  id?: string;
  name?: string;
  date?: string;
  weekday?: string;
  start?: string;
  end?: string;
  startsAt?: string;
  endsAt?: string;
};

type V1Order = Order & {
  actualStartedAt?: string | null;
  closeAt?: string | null;
  data?: Record<string, unknown>;
  requestedSlot?: SlotBrief | null;
  confirmedSlot?: SlotBrief | null;
  requestedSlotSnapshot?: SlotBrief | null;
  confirmedSlotSnapshot?: SlotBrief | null;
  slotProposal?: {
    confirmed?: boolean;
    rejected?: boolean;
    proposed?: SlotBrief;
  } | null;
  arrangement?: {
    status?: string;
    effective?: SlotBrief | null;
    confirmed?: SlotBrief | null;
    originalRequest?: SlotBrief | null;
    originalConfirmation?: SlotBrief | null;
    pendingProposal?: unknown;
  } | null;
  requestedSlotId?: string | null;
  confirmedSlotId?: string | null;
  slotStatus?: string | null;
  capabilities?: Record<string, unknown>;
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

function AccountDeletionForm() {
  const navigate = useNavigate();
  const identity = useIdentity();
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const action = useV1Action();

  const requestDeletion = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!confirmed || DEMO) return;
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
        return;
      }
      if (result.status !== "RECOVERY")
        throw new Error("注销状态需要核实，请稍后重试。");
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

  return (
    <Panel className="account-deletion-panel" title="申请注销账号">
      <p>
        所有订单已结束且没有待处理的售后、退款或账务事项时，可自助注销。注销后退出所有设备，7
        天内使用原账号密码登录即可恢复。期满后按数据保留规则处理。唯一超级管理员须先完成管理权限交接。
      </p>
      {DEMO ? (
        <Notice type="warning">正式环境满足条件后，提交即进入 7 天恢复期。</Notice>
      ) : (
        <>
          {action.uncertain ? (
            <Notice type="warning">
              <div className="account-deletion-uncertain">
                <span role="alert">
                  暂时无法确认注销结果。请刷新页面核实登录状态。若已退出，使用原账号登录即可恢复。
                </span>
                <Button variant="secondary" onClick={() => window.location.reload()}>
                  刷新页面
                </Button>
              </div>
            </Notice>
          ) : (
            <TaskFeedback state={action} label="正在提交注销申请" />
          )}
          <form className="v1-form" onSubmit={(event) => void requestDeletion(event)}>
            <Field label="注销原因（选填）">
              <textarea
                rows={2}
                maxLength={300}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="可说明注销原因"
              />
            </Field>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(event) => setConfirmed(event.target.checked)}
              />
              <span>
                我已了解注销后将退出所有设备，7
                天内登录可恢复，期满后无法通过登录找回。
              </span>
            </label>
            <div className="v1-actions">
              <Button
                type="submit"
                variant="danger"
                busy={action.pending === "deletion"}
                busyLabel="正在提交…"
                disabled={!!action.pending || action.uncertain || !confirmed}
              >
                <Trash2 size={16} />
                提交注销申请
              </Button>
            </div>
          </form>
        </>
      )}
    </Panel>
  );
}

function AccountDeletionGate({ children }: { children: React.ReactNode }) {
  const { customer } = useIdentity();
  return customer.authKind === "REGISTERED" && customer.account?.verified ? (
    <>{children}</>
  ) : (
    <Panel>
      <Empty
        title="登录已验证的账号"
        text="注销账号需要本人登录并验证邮箱。"
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

export function CustomerAccountDeletionPage() {
  return (
    <main className="content-page">
      <Link className="back-link" to="/u/account">
        <ArrowLeft size={16} />
        返回账号设置
      </Link>
      <PageHeading
        title="注销账号"
        description="完成所有订单并处理待办事项后，可自助注销。"
      />
      <AccountDeletionGate>
        <AccountDeletionForm />
      </AccountDeletionGate>
    </main>
  );
}

/* ------------------------------------------------------------------------- */
/* Order text helpers reused by src/customer/orders.tsx and review.tsx        */
/* ------------------------------------------------------------------------- */

export function requestedSlotOf(order: Order) {
  const value = order as V1Order;
  return (
    value.requestedSlot ||
    value.requestedSlotSnapshot ||
    (order as any).arrangement?.originalRequest ||
    null
  );
}

export function confirmedSlotOf(order: Order) {
  const value = order as V1Order;
  return (
    value.confirmedSlot ||
    value.confirmedSlotSnapshot ||
    (order as any).arrangement?.confirmed ||
    null
  );
}

function slotWindowKey(slot?: SlotBrief | null) {
  if (slot?.startsAt && slot.endsAt) {
    const start = Date.parse(slot.startsAt);
    const end = Date.parse(slot.endsAt);
    if (Number.isFinite(start) && Number.isFinite(end))
      return `${start}/${end}`;
  }
  if (slot?.date && slot.start && slot.end) {
    return slotWindowKey({
      startsAt: `${slot.date}T${slot.start}:00+08:00`,
      endsAt: `${slot.date}T${slot.end}:00+08:00`,
    });
  }
  return "";
}

export function deliveryWindowOf(order: Order, fallback?: SlotBrief) {
  const value = order as V1Order;
  const arrangement = value.arrangement;
  const unpaid = order.status === "PENDING_CONFIRMATION";
  const current = unpaid
    ? (value.slotProposal?.confirmed ? value.slotProposal.proposed : null) ||
      arrangement?.effective ||
      requestedSlotOf(order) ||
      fallback
    : arrangement?.effective || confirmedSlotOf(order) || fallback;
  const original = unpaid ? null : arrangement?.originalConfirmation;
  const currentKey = slotWindowKey(current);
  const originalKey = slotWindowKey(original);
  return {
    unpaid,
    current,
    previous:
      currentKey && originalKey && currentKey !== originalKey ? original : null,
    hasLegacyProposal: !!(
      arrangement?.pendingProposal ||
      (unpaid && value.slotProposal && !value.slotProposal.confirmed)
    ),
  };
}

const slotClock = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});
const slotDate = new Intl.DateTimeFormat("sv-SE", {
  timeZone: "Asia/Shanghai",
});

export function slotBlockLabel(slot?: SlotBrief | null) {
  if (!slot) return "";
  const start = new Date(slot.startsAt || "");
  const end = new Date(slot.endsAt || "");
  let date = slot.date;
  let time = slot.start && slot.end ? `${slot.start}–${slot.end}` : "";
  if (Number.isFinite(start.getTime()) && Number.isFinite(end.getTime())) {
    date = slotDate.format(start);
    const endDate = slotDate.format(end);
    time = `${slotClock.format(start)}–${endDate === date ? "" : endDate + " "}${slotClock.format(end)}`;
  }
  return [slot.campusName, date, slot.weekday, slot.name, time]
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
