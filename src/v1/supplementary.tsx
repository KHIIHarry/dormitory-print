import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  Banknote,
  ReceiptText,
  RotateCcw,
  XCircle,
} from "lucide-react";
import {
  Button,
  Empty,
  ErrorState,
  Field,
  Loading,
  Modal,
  Notice,
  Panel,
} from "../components/ui";
import { ConfirmAction } from "../admin/growth/common";
import { ADMIN, DEMO, isUncertainSubmission, money } from "../lib/api";
import {
  amountError,
  cancelCharge,
  chargeDraftError,
  createCharge,
  disputeCharge,
  isReceiptOvershoot,
  listAdminCharges,
  listCustomerCharges,
  reasonError,
  receiptOvershootRemainingCents,
  receiveCharge,
  refundCharge,
  resolveDispute,
  yuanToCents,
  type SupplementaryCharge,
  type SupplementaryDispute,
  type SupplementaryList,
} from "./supplementary-api";
import "./supplementary.css";

const timestamp = (value?: string | null) => {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" });
};

const writeError = (error: unknown) =>
  isUncertainSubmission(error)
    ? "提交结果待核实：请刷新记录确认，切勿重复提交。"
    : error instanceof Error
      ? error.message
      : "操作暂时未完成，请稍后重试。";

function statusLabel(status: string) {
  return status === "CANCELLED"
    ? "已取消"
    : status === "ACTIVE"
      ? "生效中"
      : status;
}

function useWrite() {
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState("");
  const run = async (
    key: string,
    action: () => Promise<unknown>,
    onError?: (cause: unknown) => boolean,
  ) => {
    if (pending) return false;
    setPending(key);
    setError("");
    try {
      await action();
      return true;
    } catch (cause) {
      if (!onError?.(cause)) setError(writeError(cause));
      return false;
    } finally {
      setPending(null);
    }
  };
  return { pending, error, setError, run };
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="supp-metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function MovementList({ charge }: { charge: SupplementaryCharge }) {
  if (!charge.movements.length)
    return <p className="supp-muted">还没有实际收款或退款记录。</p>;
  return (
    <ul className="supp-movements" aria-label="补充费用收退款记录">
      {charge.movements.map((movement) => (
        <li key={movement.id}>
          <span
            className={
              movement.kind === "REFUND" ? "supp-move refund" : "supp-move"
            }
          >
            {movement.kind === "REFUND" ? "退款" : "收款"}
          </span>
          <strong>{money(movement.amountCents)}</strong>
          <span>{movement.reason}</span>
          <small>{timestamp(movement.createdAt)}</small>
        </li>
      ))}
    </ul>
  );
}

function DisputeList({
  charge,
  admin,
  onResolve,
}: {
  charge: SupplementaryCharge;
  admin?: boolean;
  onResolve?: (dispute: SupplementaryDispute) => void;
}) {
  if (!charge.disputes.length)
    return <p className="supp-muted">客户暂未对该费用提出异议。</p>;
  return (
    <ul className="supp-disputes" aria-label="补充费用争议">
      {charge.disputes.map((dispute) => (
        <li key={dispute.id}>
          <div>
            <strong>{dispute.reason}</strong>
            <small>
              {timestamp(dispute.createdAt)} ·{" "}
              {dispute.status === "OPEN"
                ? "待处理"
                : dispute.status === "RESOLVED"
                  ? "已解决"
                  : "已驳回"}
            </small>
            {dispute.resolution && <p>处理结果：{dispute.resolution}</p>}
          </div>
          {admin && dispute.status === "OPEN" && (
            <Button variant="secondary" onClick={() => onResolve?.(dispute)}>
              处理异议
            </Button>
          )}
        </li>
      ))}
    </ul>
  );
}

/* ------------------------------------------------------------------------- */
/* Administrator                                                              */
/* ------------------------------------------------------------------------- */

type AdminAction =
  | { kind: "receipt" | "refund"; charge: SupplementaryCharge }
  | { kind: "cancel"; charge: SupplementaryCharge }
  | {
      kind: "resolve";
      charge: SupplementaryCharge;
      dispute: SupplementaryDispute;
    }
  | null;

export function AdminSupplementaryPanel({ orderId }: { orderId: string }) {
  const [data, setData] = useState<SupplementaryList | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [purpose, setPurpose] = useState("");
  const [amount, setAmount] = useState("");
  const [includeInPayroll, setIncludeInPayroll] = useState(false);
  const [formError, setFormError] = useState("");
  const [action, setAction] = useState<AdminAction>(null);
  const [moveAmount, setMoveAmount] = useState("");
  const [moveReason, setMoveReason] = useState("");
  const [moveError, setMoveError] = useState("");
  const [receiptOvershoot, setReceiptOvershoot] = useState<{
    enteredCents: number;
    remainingCents: number;
  } | null>(null);
  const [resolution, setResolution] = useState("");
  const [disputeStatus, setDisputeStatus] = useState<"RESOLVED" | "REJECTED">(
    "RESOLVED",
  );
  const [notice, setNotice] = useState("");
  const sequence = useRef(0);
  const write = useWrite();

  const load = useCallback(async () => {
    const seq = ++sequence.current;
    setLoading(true);
    setLoadError("");
    try {
      const response = await listAdminCharges(orderId);
      if (seq !== sequence.current) return;
      setData(response);
    } catch (cause) {
      if (seq !== sequence.current) return;
      setLoadError(
        cause instanceof Error ? cause.message : "暂时无法读取补充费用",
      );
    } finally {
      if (seq === sequence.current) setLoading(false);
    }
  }, [orderId]);

  useEffect(() => {
    setData(null);
    setCreateOpen(false);
    setAction(null);
    setNotice("");
    setReceiptOvershoot(null);
    write.setError("");
    if (DEMO) {
      setLoading(false);
      return;
    }
    void load();
    return () => {
      sequence.current++;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderId]);

  const summary = data?.summary;
  const items = data?.items || [];

  const openCreate = () => {
    setPurpose("");
    setAmount("");
    setIncludeInPayroll(false);
    setFormError("");
    write.setError("");
    setCreateOpen(true);
  };

  const submitCreate = async () => {
    const invalid = chargeDraftError({ purpose, amount });
    if (invalid) {
      setFormError(invalid);
      return;
    }
    if (!summary) {
      setFormError("订单版本尚未加载，请刷新后重试。");
      return;
    }
    const cents = yuanToCents(amount);
    if (cents === null) {
      setFormError("请输入大于 0 的准确金额");
      return;
    }
    const ok = await write.run("create", () =>
      createCharge(orderId, {
        purpose: purpose.trim(),
        amountCents: cents,
        includeInPayroll,
        expectedVersion: summary.orderVersion,
      }),
    );
    if (!ok) return;
    setCreateOpen(false);
    setNotice(
      "补充费用已新增。该金额是最终费用，不参与优惠券、满量或员工折扣。",
    );
    await load();
  };

  const openMovement = (
    kind: "receipt" | "refund",
    charge: SupplementaryCharge,
  ) => {
    setMoveAmount("");
    setMoveReason("");
    setMoveError("");
    setReceiptOvershoot(null);
    write.setError("");
    setAction({ kind, charge });
  };

  const submitMovement = async () => {
    if (!action || (action.kind !== "receipt" && action.kind !== "refund"))
      return;
    setMoveError("");
    setReceiptOvershoot(null);
    const maximum =
      action.kind === "receipt"
        ? action.charge.outstandingCents
        : action.charge.grossReceivedCents - action.charge.refundedCents;
    const cents = yuanToCents(moveAmount);
    if (cents === null || cents <= 0) {
      setMoveError("请输入大于 0 的准确金额");
      return;
    }
    if (action.kind === "receipt" && cents > maximum) {
      setReceiptOvershoot({ enteredCents: cents, remainingCents: maximum });
      setMoveError(`收款金额超过待收 ${money(maximum)}，本笔未登记`);
      return;
    }
    const reasonInvalid = reasonError(moveReason);
    if (reasonInvalid) {
      setMoveError(reasonInvalid);
      return;
    }
    if (action.kind === "refund") {
      const invalid = amountError(moveAmount, maximum, "退款金额");
      if (invalid) {
        setMoveError(invalid);
        return;
      }
    }
    const ok = await write.run(
      action.kind,
      () =>
        action.kind === "receipt"
          ? receiveCharge(action.charge.id, {
              amountCents: cents,
              reason: moveReason.trim(),
              expectedVersion: action.charge.version,
            })
          : refundCharge(action.charge.id, {
              amountCents: cents,
              reason: moveReason.trim(),
              expectedVersion: action.charge.version,
            }),
      (cause) => {
        if (action.kind !== "receipt" || !isReceiptOvershoot(cause))
          return false;
        const remaining = receiptOvershootRemainingCents(cause) ?? maximum;
        setReceiptOvershoot({
          enteredCents: cents,
          remainingCents: remaining,
        });
        setMoveError(`收款金额超过待收 ${money(remaining)}，本笔未登记`);
        return true;
      },
    );
    if (!ok) return;
    setAction(null);
    setNotice(
      action.kind === "receipt"
        ? "已登记补充费用收款。"
        : "已登记补充费用退款。",
    );
    await load();
  };

  const submitCancel = async (reason: string) => {
    if (!action || action.kind !== "cancel") return false;
    const ok = await write.run("cancel", () =>
      cancelCharge(action.charge.id, {
        reason,
        expectedVersion: action.charge.version,
      }),
    );
    if (ok) {
      setNotice("补充费用已取消，历史收退款记录保留。");
      await load();
    }
    return ok;
  };

  const openResolve = (
    charge: SupplementaryCharge,
    dispute: SupplementaryDispute,
  ) => {
    setResolution("");
    setDisputeStatus("RESOLVED");
    write.setError("");
    setAction({ kind: "resolve", charge, dispute });
  };

  const submitResolve = async () => {
    if (!action || action.kind !== "resolve") return;
    const invalid = resolution.trim() ? "" : "请填写处理结果";
    if (invalid) {
      write.setError(invalid);
      return;
    }
    const ok = await write.run("resolve", () =>
      resolveDispute(action.charge.id, action.dispute.id, {
        resolution: resolution.trim(),
        status: disputeStatus,
        expectedVersion: action.dispute.version,
      }),
    );
    if (!ok) return;
    setAction(null);
    setNotice("争议已处理。");
    await load();
  };

  return (
    <Panel
      title="补充费用"
      action={
        <Button onClick={openCreate} disabled={DEMO}>
          新增补充费用
        </Button>
      }
    >
      <p className="supp-intro">
        补充费用独立于原订单报价、收款与履约状态，不会改写原订单金额或交付结果。
        每笔费用创建后金额、用途与工资计入标记不可修改；收款和退款分别留痕。
      </p>
      {DEMO && <Notice type="info">演示模式不写入补充费用。</Notice>}
      {loading ? (
        <Loading label="正在读取补充费用" />
      ) : loadError ? (
        <ErrorState message={loadError} retry={() => void load()} />
      ) : (
        <>
          {summary && (
            <div className="supp-summary">
              <Metric
                label="原订单合计"
                value={money(summary.original.totalCents)}
              />
              <Metric
                label="补充费用合计"
                value={money(summary.extras.feeTotalCents)}
              />
              <Metric
                label="补充费用已收"
                value={money(summary.extras.grossReceivedCents)}
              />
              <Metric
                label="补充费用已退"
                value={money(summary.extras.refundedCents)}
              />
              <Metric
                label="补充费用待收"
                value={money(summary.extras.outstandingCents)}
              />
            </div>
          )}
          {notice && <Notice type="success">{notice}</Notice>}
          {write.error && (
            <Notice type="error">
              <span role="alert">{write.error}</span>
            </Notice>
          )}
          {!items.length ? (
            <Empty
              title="暂无补充费用"
              text="排版、装订或文档整理等额外工作可按固定金额逐笔登记。"
              action={
                DEMO ? undefined : (
                  <Button onClick={openCreate}>登记第一笔</Button>
                )
              }
            />
          ) : (
            <div className="supp-list">
              {items.map((charge) => {
                const refundable =
                  charge.grossReceivedCents - charge.refundedCents;
                return (
                  <article className="supp-charge" key={charge.id}>
                    <header>
                      <div>
                        <h3>{charge.purpose}</h3>
                        <small>
                          {timestamp(charge.createdAt)}
                          {charge.createdBy
                            ? ` · 经办 ${charge.createdBy}`
                            : ""}
                        </small>
                      </div>
                      <span
                        className={`supp-status ${charge.status.toLowerCase()}`}
                      >
                        {statusLabel(charge.status)}
                      </span>
                    </header>
                    <div className="supp-metrics">
                      <Metric
                        label="最终费用"
                        value={money(charge.amountCents)}
                      />
                      <Metric
                        label="累计已收"
                        value={money(charge.grossReceivedCents)}
                      />
                      <Metric
                        label="累计已退"
                        value={money(charge.refundedCents)}
                      />
                      <Metric
                        label="净收"
                        value={money(charge.netReceivedCents)}
                      />
                      <Metric
                        label="待收"
                        value={money(charge.outstandingCents)}
                      />
                    </div>
                    <div className="supp-tags">
                      <span>
                        工资：
                        {charge.includeInPayroll
                          ? "计入百分比工资"
                          : "不计入工资"}
                      </span>
                      {charge.creditCents > 0 && (
                        <span>客户退款余额 {money(charge.creditCents)}</span>
                      )}
                    </div>
                    <div className="supp-actions">
                      <Button
                        variant="secondary"
                        disabled={
                          charge.status !== "ACTIVE" ||
                          charge.outstandingCents <= 0
                        }
                        onClick={() => openMovement("receipt", charge)}
                      >
                        <Banknote size={16} />
                        登记收款
                      </Button>
                      <Button
                        variant="secondary"
                        disabled={charge.status !== "ACTIVE" || refundable <= 0}
                        onClick={() => openMovement("refund", charge)}
                      >
                        <RotateCcw size={16} />
                        登记退款
                      </Button>
                      <Button
                        variant="danger"
                        disabled={
                          charge.status !== "ACTIVE" ||
                          charge.netReceivedCents !== 0
                        }
                        onClick={() => setAction({ kind: "cancel", charge })}
                      >
                        <XCircle size={16} />
                        取消费用
                      </Button>
                    </div>
                    <div className="supp-detail-grid">
                      <div>
                        <h4>
                          <ReceiptText size={15} /> 收款与退款
                        </h4>
                        <MovementList charge={charge} />
                      </div>
                      <div>
                        <h4>
                          <AlertTriangle size={15} /> 客户异议
                        </h4>
                        <DisputeList
                          charge={charge}
                          admin
                          onResolve={(dispute) => openResolve(charge, dispute)}
                        />
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </>
      )}

      {createOpen && (
        <Modal
          title="新增补充费用"
          onClose={() => !write.pending && setCreateOpen(false)}
        >
          <div className="supp-form">
            <Notice type="info">
              这是最终固定金额，不参与优惠券、满量折扣或员工折扣；创建后金额与用途不可修改。
            </Notice>
            <Field
              label="费用用途（客户可见）"
              error={formError && !purpose.trim() ? formError : ""}
            >
              <textarea
                value={purpose}
                maxLength={2000}
                onChange={(event) => setPurpose(event.target.value)}
                placeholder="例如：论文封面排版与胶装"
              />
            </Field>
            <Field
              label="金额（元）"
              error={formError && purpose.trim() ? formError : ""}
              hint="按最终商定金额填写，精确到分。"
            >
              <input
                inputMode="decimal"
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
                placeholder="0.00"
              />
            </Field>
            <Field
              label="计入工资"
              hint="只影响百分比工资；固定工资不受影响。创建后不可修改。"
            >
              <label className="supp-check">
                <input
                  type="checkbox"
                  checked={includeInPayroll}
                  onChange={(event) =>
                    setIncludeInPayroll(event.target.checked)
                  }
                />
                计入百分比工资
              </label>
            </Field>
            {write.error && (
              <Notice type="error">
                <span role="alert">{write.error}</span>
              </Notice>
            )}
            <div className="button-row">
              <Button variant="secondary" onClick={() => setCreateOpen(false)}>
                取消
              </Button>
              <Button
                busy={write.pending === "create"}
                onClick={() => void submitCreate()}
              >
                保存费用
              </Button>
            </div>
          </div>
        </Modal>
      )}

      {action && (action.kind === "receipt" || action.kind === "refund") && (
        <Modal
          title={
            action.kind === "receipt" ? "登记补充费用收款" : "登记补充费用退款"
          }
          onClose={() => !write.pending && setAction(null)}
        >
          <div className="supp-form">
            <Notice type={action.kind === "refund" ? "warning" : "info"}>
              {action.kind === "receipt"
                ? `剩余应收 ${money(action.charge.outstandingCents)}。收款只记录补充费用，不改动原订单收款。`
                : `最多可退 ${money(action.charge.grossReceivedCents - action.charge.refundedCents)}。退款形成客户余额，不会重新打开已收款金额。`}
            </Notice>
            {action.kind === "receipt" && receiptOvershoot && (
              <Notice type="error">
                <div className="supp-overshoot" role="alert">
                  <strong>
                    这笔补充费用收款未登记，本次补充费用入账为 ¥0.00。
                  </strong>
                  <p>
                    输入 {money(receiptOvershoot.enteredCents)}，当前待收{" "}
                    {money(receiptOvershoot.remainingCents)}
                    。超出部分不会记入补充费用，也不会显示为已支付。
                  </p>
                  <p>
                    如果多出的现金实际已经到账，请到原订单的未匹配收款登记实际收款，不要重复登记补充费用。
                  </p>
                  <Link
                    to={`${ADMIN}/after-sales?tab=receipts&orderId=${encodeURIComponent(orderId)}`}
                  >
                    打开该订单的未匹配收款
                  </Link>
                </div>
              </Notice>
            )}
            <Field
              label="金额（元）"
              error={receiptOvershoot ? undefined : moveError}
            >
              <input
                inputMode="decimal"
                value={moveAmount}
                onChange={(event) => {
                  setMoveAmount(event.target.value);
                  setReceiptOvershoot(null);
                  setMoveError("");
                }}
                placeholder="0.00"
              />
            </Field>
            <Field label="原因">
              <textarea
                value={moveReason}
                maxLength={2000}
                onChange={(event) => setMoveReason(event.target.value)}
                placeholder="填写收款或退款原因"
              />
            </Field>
            {write.error && (
              <Notice type="error">
                <span role="alert">{write.error}</span>
              </Notice>
            )}
            <div className="button-row">
              <Button variant="secondary" onClick={() => setAction(null)}>
                取消
              </Button>
              <Button
                busy={write.pending === action.kind}
                onClick={() => void submitMovement()}
              >
                确认登记
              </Button>
            </div>
          </div>
        </Modal>
      )}

      {action?.kind === "cancel" && (
        <ConfirmAction
          title="取消补充费用"
          pending={write.pending === "cancel"}
          error={write.error}
          onClose={() => setAction(null)}
          onConfirm={submitCancel}
        >
          只有净收为 0
          时才能取消；如已收款，请先全额退款。取消后历史收退款记录保留，待收金额归零。
        </ConfirmAction>
      )}

      {action?.kind === "resolve" && (
        <Modal
          title="处理客户异议"
          onClose={() => !write.pending && setAction(null)}
        >
          <div className="supp-form">
            <Notice type="info">客户异议：{action.dispute.reason}</Notice>
            <Field label="处理结果">
              <select
                value={disputeStatus}
                onChange={(event) =>
                  setDisputeStatus(
                    event.target.value as "RESOLVED" | "REJECTED",
                  )
                }
              >
                <option value="RESOLVED">已解决</option>
                <option value="REJECTED">驳回异议</option>
              </select>
            </Field>
            <Field label="处理说明">
              <textarea
                value={resolution}
                maxLength={2000}
                onChange={(event) => setResolution(event.target.value)}
                placeholder="说明核对结果或处理方式"
              />
            </Field>
            {write.error && (
              <Notice type="error">
                <span role="alert">{write.error}</span>
              </Notice>
            )}
            <div className="button-row">
              <Button variant="secondary" onClick={() => setAction(null)}>
                取消
              </Button>
              <Button
                busy={write.pending === "resolve"}
                onClick={() => void submitResolve()}
              >
                保存处理结果
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </Panel>
  );
}

/* ------------------------------------------------------------------------- */
/* Customer                                                                   */
/* ------------------------------------------------------------------------- */

export function CustomerSupplementaryPanel({
  orderId,
  canDispute = false,
}: {
  orderId: string;
  canDispute?: boolean;
}) {
  const [data, setData] = useState<SupplementaryList | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [disputeFor, setDisputeFor] = useState<SupplementaryCharge | null>(
    null,
  );
  const [reason, setReason] = useState("");
  const [formError, setFormError] = useState("");
  const [notice, setNotice] = useState("");
  const sequence = useRef(0);
  const write = useWrite();

  const load = useCallback(async () => {
    const seq = ++sequence.current;
    setLoading(true);
    setLoadError("");
    try {
      const response = await listCustomerCharges(orderId);
      if (seq !== sequence.current) return;
      setData(response);
    } catch (cause) {
      if (seq !== sequence.current) return;
      setLoadError(
        cause instanceof Error ? cause.message : "暂时无法读取补充费用",
      );
    } finally {
      if (seq === sequence.current) setLoading(false);
    }
  }, [orderId]);

  useEffect(() => {
    setData(null);
    setDisputeFor(null);
    setNotice("");
    write.setError("");
    if (DEMO) {
      setLoading(false);
      return;
    }
    void load();
    return () => {
      sequence.current++;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderId]);

  useEffect(() => {
    if (!canDispute) setDisputeFor(null);
  }, [canDispute]);

  const openDispute = (charge: SupplementaryCharge) => {
    if (!canDispute) return;
    setReason("");
    setFormError("");
    write.setError("");
    setDisputeFor(charge);
  };

  const submitDispute = async () => {
    if (!canDispute || !disputeFor) return;
    const invalid = reasonError(reason);
    if (invalid) {
      setFormError(invalid);
      return;
    }
    const ok = await write.run("dispute", () =>
      disputeCharge(orderId, disputeFor.id, {
        reason: reason.trim(),
        expectedVersion: disputeFor.version,
      }),
    );
    if (!ok) return;
    setDisputeFor(null);
    setNotice("异议已提交，管理员会核实处理。");
    await load();
  };

  const items = data?.items || [];
  const summary = data?.summary;

  return (
    <Panel title="补充费用">
      <p className="supp-intro">
        这些费用是原订单之外的最终固定金额，用于排版、装订或文档整理等额外工作；
        不使用优惠券、满量或员工折扣，也不会改变原订单报价与交付状态。
      </p>
      {DEMO && <Notice type="info">演示模式不显示补充费用。</Notice>}
      {loading ? (
        <Loading label="正在读取补充费用" />
      ) : loadError ? (
        <ErrorState message={loadError} retry={() => void load()} />
      ) : (
        <>
          {summary && summary.extras.chargeCount > 0 && (
            <div className="supp-summary">
              <Metric
                label="补充费用合计"
                value={money(summary.extras.feeTotalCents)}
              />
              <Metric
                label="已支付"
                value={money(summary.extras.grossReceivedCents)}
              />
              <Metric
                label="已退回"
                value={money(summary.extras.refundedCents)}
              />
              <Metric
                label="待支付"
                value={money(summary.extras.outstandingCents)}
              />
            </div>
          )}
          {notice && <Notice type="success">{notice}</Notice>}
          {write.error && (
            <Notice type="error">
              <span role="alert">{write.error}</span>
            </Notice>
          )}
          {!items.length ? (
            <Empty
              title="暂无补充费用"
              text="如管理员登记了额外排版、装订或文档整理费用，会显示在这里。"
            />
          ) : (
            <div className="supp-list">
              {items.map((charge) => (
                <article className="supp-charge" key={charge.id}>
                  <header>
                    <div>
                      <h3>{charge.purpose}</h3>
                      <small>{timestamp(charge.createdAt)}</small>
                    </div>
                    <span
                      className={`supp-status ${charge.status.toLowerCase()}`}
                    >
                      {statusLabel(charge.status)}
                    </span>
                  </header>
                  <div className="supp-metrics">
                    <Metric
                      label="最终费用"
                      value={money(charge.amountCents)}
                    />
                    <Metric
                      label="已支付"
                      value={money(charge.grossReceivedCents)}
                    />
                    <Metric
                      label="已退回"
                      value={money(charge.refundedCents)}
                    />
                    <Metric
                      label="待支付"
                      value={money(charge.outstandingCents)}
                    />
                  </div>
                  {charge.creditCents > 0 && (
                    <Notice type="info">
                      已退回 {money(charge.creditCents)}
                      ，可作为后续费用余额，不会重复收取。
                    </Notice>
                  )}
                  <MovementList charge={charge} />
                  <DisputeList charge={charge} />
                  {canDispute && charge.status === "ACTIVE" && (
                    <div className="supp-actions">
                      <Button
                        variant="secondary"
                        onClick={() => openDispute(charge)}
                      >
                        <AlertTriangle size={16} />
                        对费用有异议
                      </Button>
                    </div>
                  )}
                </article>
              ))}
            </div>
          )}
        </>
      )}

      {canDispute && disputeFor && (
        <Modal
          title="对补充费用提出异议"
          onClose={() => !write.pending && setDisputeFor(null)}
        >
          <div className="supp-form">
            <Notice type="info">
              费用：{disputeFor.purpose} · {money(disputeFor.amountCents)}
            </Notice>
            <Field label="异议原因" error={formError}>
              <textarea
                value={reason}
                maxLength={2000}
                onChange={(event) => setReason(event.target.value)}
                placeholder="说明对金额、用途或收款的疑问"
              />
            </Field>
            {write.error && (
              <Notice type="error">
                <span role="alert">{write.error}</span>
              </Notice>
            )}
            <div className="button-row">
              <Button variant="secondary" onClick={() => setDisputeFor(null)}>
                取消
              </Button>
              <Button
                busy={write.pending === "dispute"}
                onClick={() => void submitDispute()}
              >
                提交异议
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </Panel>
  );
}
