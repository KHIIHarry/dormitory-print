import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  Download,
  ExternalLink,
  FileWarning,
  KeyRound,
  RefreshCw,
  ShieldCheck,
  Trash2,
  UserX,
} from "lucide-react";
import {
  Badge,
  Button,
  Empty,
  Field,
  Modal,
  Notice,
  PageHeading,
  Panel,
} from "../components/ui";
import { ResourceSkeleton, RefreshStatus } from "../components/feedback";
import { growthRequest, useGrowthResource } from "../growth/client";
import type { StaffSession } from "../growth/types";
import { money, ADMIN } from "../lib/api";
import {
  AccessState,
  ConfirmAction,
  ResourceState,
  errorText,
  useSubmit,
} from "../admin/growth/common";
import {
  CAPABILITY_CATALOG,
  adminAfterSaleDetailPath,
  allowedDataOperationTabs,
  afterSaleActionPayload,
  afterSaleReviewPayload,
  afterSaleStatusLabel,
  exportPayload,
  receiptRefundPayload,
  receiptRefundTotals,
  staffCapabilityPayload,
  type AdminDataOperationTab,
  type AfterSaleCase,
  type AfterSaleList,
  type ExportJobView,
  type ReceiptView,
  type StaffCapabilityRow,
} from "./admin-api";
import "./admin.css";

function useAdminOnly() {
  const session = useGrowthResource<StaffSession>("/auth/me");
  return {
    allowed: session.data?.role === "ADMIN",
    capabilities: session.data?.capabilities || [],
    loading: session.loading,
    error: session.error,
    reload: session.reload,
  };
}

function apiBase() {
  return (import.meta.env.VITE_API_BASE_URL || "/api/v1").replace(/\/$/, "");
}

function useList<T>(path: string | null) {
  const [items, setItems] = useState<T[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(!!path);
  const [error, setError] = useState("");
  const [sequence, setSequence] = useState(0);
  useEffect(() => {
    if (!path) {
      setItems([]);
      setLoading(false);
      return;
    }
    let active = true;
    setLoading(true);
    setError("");
    growthRequest<{ items: T[]; nextCursor?: string | null }>(path)
      .then((result) => {
        if (!active) return;
        setItems(result.items || []);
        setNextCursor(result.nextCursor || null);
      })
      .catch((cause) => {
        if (active) setError(errorText(cause));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [path, sequence]);
  const more = async () => {
    if (!path || !nextCursor) return;
    setLoading(true);
    try {
      const glue = path.includes("?") ? "&" : "?";
      const result = await growthRequest<{ items: T[]; nextCursor?: string | null }>(
        `${path}${glue}cursor=${encodeURIComponent(nextCursor)}`,
      );
      setItems((current) => [...current, ...(result.items || [])]);
      setNextCursor(result.nextCursor || null);
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setLoading(false);
    }
  };
  return {
    items,
    nextCursor,
    loading,
    error,
    more,
    reload: () => setSequence((value) => value + 1),
  };
}

function sourceLabel(source: string) {
  return source === "CUSTOMER" ? "客户申请" : "管理员代建";
}

function responsibilityLabel(value: string) {
  return value === "MERCHANT" ? "商家责任" : "客户自身原因";
}

function solutionLabel(value: string) {
  return value === "REPRINT" ? "免费补打/重做" : "退款";
}

const AFTER_SALE_STATUSES = [
  ["", "全部"],
  ["REQUESTED", "待审核"],
  ["REVIEW_PENDING", "审核中"],
  ["REFUND_APPROVED", "已批准待退款"],
  ["REPRINTING", "重做中"],
  ["REPRINT_READY", "重做待交付"],
  ["REFUNDED", "已退款"],
  ["COMPLETED", "已完成"],
  ["REJECTED", "已拒绝"],
  ["CANCELLED", "已撤回"],
] as const;

export function AdminAfterSalesPage() {
  const access = useAdminOnly();
  const [params, setParams] = useSearchParams();
  const tab: "cases" | "receipts" =
    params.get("tab") === "receipts" ? "receipts" : "cases";
  const orderFilter = (params.get("orderId") || "").trim();
  const selectTab = (next: "cases" | "receipts") => {
    const nextParams = new URLSearchParams(params);
    if (next === "receipts") {
      nextParams.set("tab", "receipts");
    } else {
      nextParams.delete("tab");
      nextParams.delete("orderId");
    }
    setParams(nextParams, { replace: true });
  };
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [draft, setDraft] = useState(false);
  const path = useMemo(() => {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    return `/admin/after-sales${params.size ? `?${params}` : ""}`;
  }, [status]);
  const list = useList<AfterSaleCase>(path);
  const items = q.trim()
    ? list.items.filter((item) =>
        `${item.orderCode || ""} ${item.orderId} ${item.reason} ${item.id}`
          .toLowerCase()
          .includes(q.trim().toLowerCase()),
      )
    : list.items;
  return (
    <div className="admin-growth v1-admin">
      <PageHeading
        title="售后与退款"
        description="客户申请与管理员代建统一审核；建议金额与人工批准分开记录，批准不等于已退款。"
        action={
          tab === "cases" ? (
            <Button onClick={() => setDraft(true)}>
              <FileWarning size={16} />
              代建售后
            </Button>
          ) : undefined
        }
      />
      <AccessState access={access} />
      <div className="v1-tabs" role="tablist" aria-label="售后与未匹配收款">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "cases"}
          className={tab === "cases" ? "active" : ""}
          onClick={() => selectTab("cases")}
        >
          售后单
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "receipts"}
          className={tab === "receipts" ? "active" : ""}
          onClick={() => selectTab("receipts")}
        >
          未匹配收款
        </button>
      </div>
      {tab === "cases" && (
        <>
      <Panel className="filter-panel">
        <div className="filter-row">
          <Field label="搜索">
            <input
              value={q}
              onChange={(event) => setQ(event.target.value)}
              placeholder="订单号、客户或原因"
            />
          </Field>
          <Field label="状态">
            <select value={status} onChange={(event) => setStatus(event.target.value)}>
              {AFTER_SALE_STATUSES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </Panel>
      <ResourceState
        loading={list.loading && !list.items.length}
        error={list.error}
        empty={!list.loading && !list.error && !items.length}
        hasData={!!items.length}
        reload={list.reload}
      />
      {items.length > 0 && (
        <Panel>
          <div className="growth-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>订单 / 来源</th>
                  <th>责任 / 方案</th>
                  <th>建议 / 批准</th>
                  <th>状态</th>
                  <th>申请时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <b>{item.orderCode || item.orderId}</b>
                      <small>
                        {sourceLabel(item.source)} · {item.customerLabel || "客户"}
                      </small>
                    </td>
                    <td>
                      {responsibilityLabel(item.responsibility)}
                      <small>{solutionLabel(item.solution)}</small>
                    </td>
                    <td className="numeric">
                      {money(item.suggestedCents || 0)}
                      <small>
                        {item.approvedCents === null
                          ? "未批准"
                          : `批准 ${money(item.approvedCents)}`}
                      </small>
                    </td>
                    <td>
                      <span className="soft-tag">{afterSaleStatusLabel(item.status)}</span>
                    </td>
                    <td className="numeric">
                      {new Date(item.createdAt).toLocaleString("zh-CN")}
                    </td>
                    <td>
                      <Link className="text-link" to={`${ADMIN}/after-sales/${item.id}`}>
                        查看与审核
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {list.nextCursor && (
            <Button variant="secondary" busy={list.loading} onClick={list.more}>
              加载更多
            </Button>
          )}
        </Panel>
      )}
      {draft && tab === "cases" && (
        <CreateAfterSale
          onClose={() => setDraft(false)}
          onCreated={() => {
            setDraft(false);
            list.reload();
          }}
        />
      )}
        </>
      )}
      {tab === "receipts" && <UnmatchedReceipts orderId={orderFilter} />}
    </div>
  );
}

function UnmatchedReceipts({ orderId }: { orderId?: string }) {
  const list = useList<ReceiptView>("/admin/receipts?status=UNMATCHED");
  const visible = orderId
    ? list.items.filter((row) => row.orderId === orderId)
    : list.items;
  const [target, setTarget] = useState<ReceiptView | null>(null);
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const submit = useSubmit();
  const targetTotals = target ? receiptRefundTotals(target) : null;

  useEffect(() => {
    if (!target) return;
    setAmount((receiptRefundTotals(target).refundableCents / 100).toFixed(2));
    setReason("");
  }, [target?.id]);

  const refund = async (event: FormEvent) => {
    event.preventDefault();
    if (!target) return;
    let body: ReturnType<typeof receiptRefundPayload>;
    try {
      const cents = Math.round(Number(amount) * 100);
      body = receiptRefundPayload(target, cents, reason);
    } catch (error) {
      submit.setError(errorText(error));
      return;
    }
    const result = await submit.run(
      () =>
        growthRequest(`/admin/receipts/${target.id}/refund`, {
          method: "POST",
          body,
        }),
      "未匹配收款退款已登记",
    );
    if (result.ok) {
      setTarget(null);
      list.reload();
    }
  };

  return (
    <Panel>
      <div className="panel-head">
        <div>
          <h3>未匹配收款</h3>
          <p>
            仅未分配到订单的收款可直接退回；已分配收款必须走订单售后。后端按累计金额校验，退回全部实收后状态变为已退款。
          </p>
        </div>
        <Button variant="secondary" busy={list.loading} onClick={list.reload}>
          <RefreshCw size={16} />
          刷新
        </Button>
      </div>
      {orderId && (
        <Notice type="info">
          仅显示原订单 <code>{orderId}</code> 的未匹配收款。
          <Link to={`${ADMIN}/after-sales?tab=receipts`}>查看全部</Link>
        </Notice>
      )}
      <ResourceState
        loading={list.loading && !list.items.length}
        error={list.error}
        empty={!list.loading && !list.error && !visible.length}
        hasData={!!visible.length}
        reload={list.reload}
      />
      {visible.length > 0 && (
        <div className="growth-table-wrap">
          <table>
            <thead>
              <tr>
                <th>收款记录</th>
                <th>订单</th>
                <th>金额</th>
                <th>状态</th>
                <th>登记时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((row) => {
                const totals = receiptRefundTotals(row);
                return (
                  <tr key={row.id}>
                    <td>
                      <b>{row.id}</b>
                    </td>
                    <td>{row.orderId || "未关联"}</td>
                    <td className="numeric">
                      {money(row.amountCents)}
                      <small>
                        已退 {money(totals.refundedCents)} · 可退{" "}
                        {money(totals.refundableCents)}
                      </small>
                    </td>
                    <td>
                      <span className="soft-tag">
                        {row.status === "UNMATCHED" ? "未匹配" : row.status}
                      </span>
                    </td>
                    <td className="numeric">
                      {new Date(row.createdAt).toLocaleString("zh-CN")}
                    </td>
                    <td>
                      <Button
                        variant="secondary"
                        disabled={totals.refundableCents <= 0}
                        onClick={() => {
                          submit.setError("");
                          setTarget(row);
                        }}
                      >
                        退款
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {target && (
        <Modal
          title={`退回未匹配收款（可退 ${money(targetTotals?.refundableCents || 0)}）`}
          onClose={() => setTarget(null)}
        >
          <form className="form-stack" onSubmit={refund}>
            <div className="growth-facts">
              <div>
                <dt>实收金额</dt>
                <dd>{money(target.amountCents)}</dd>
              </div>
              <div>
                <dt>已退金额</dt>
                <dd>{money(targetTotals?.refundedCents || 0)}</dd>
              </div>
              <div>
                <dt>剩余可退</dt>
                <dd>{money(targetTotals?.refundableCents || 0)}</dd>
              </div>
            </div>
            <Notice type="info">
              默认填入剩余可退金额；本次退款不能超过剩余可退，累计退款上限由后端再次校验。
            </Notice>
            <Field label="退款金额（元）">
              <input
                inputMode="decimal"
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
                required
              />
            </Field>
            <Field label="退款原因">
              <textarea
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                rows={3}
                required
              />
            </Field>
            <Notice type="warning">
              该操作只登记未匹配收款退款；不会替客户确认任何时段或售后方案。
            </Notice>
            {submit.error && <Notice type="error">{submit.error}</Notice>}
            <div className="form-actions">
              <Button variant="secondary" type="button" onClick={() => setTarget(null)}>
                取消
              </Button>
              <Button
                type="submit"
                busy={submit.pending}
                disabled={!submit.online || (targetTotals?.refundableCents || 0) <= 0}
              >
                确认退回
              </Button>
            </div>
          </form>
        </Modal>
      )}
    </Panel>
  );
}

function CreateAfterSale({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const submit = useSubmit();
  const [orderId, setOrderId] = useState("");
  const [order, setOrder] = useState<{
    id: string;
    version: number;
    status: string;
    settlement?: { settlementKind?: string } | null;
  } | null>(null);
  const [loadingOrder, setLoadingOrder] = useState(false);
  const [responsibility, setResponsibility] = useState<"CUSTOMER" | "MERCHANT">(
    "MERCHANT",
  );
  const [solution, setSolution] = useState<"REFUND" | "REPRINT">("REFUND");
  const [reason, setReason] = useState("");
  const [items, setItems] = useState<{ lineKey: string; quantity: string }[]>([]);
  const loadOrder = async () => {
    const id = orderId.trim();
    if (!id) {
      submit.setError("请填写订单 ID");
      return;
    }
    setLoadingOrder(true);
    submit.setError("");
    setOrder(null);
    try {
      const found = await growthRequest<{
        id: string;
        version: number;
        status: string;
        settlement?: { settlementKind?: string } | null;
      }>(`/admin/orders/${encodeURIComponent(id)}`);
      setOrder(found);
    } catch (error) {
      submit.setError(errorText(error));
    } finally {
      setLoadingOrder(false);
    }
  };
  const create = async (auditReason: string) => {
    if (!order || order.id !== orderId.trim()) {
      submit.setError("请先读取订单，售后代建必须携带订单当前版本");
      return false;
    }
    const result = await submit.run(
      () =>
        growthRequest(`/admin/orders/${encodeURIComponent(order.id)}/after-sales`, {
          method: "POST",
          body: {
            expectedVersion: order.version,
            responsibility,
            solution,
            reason: reason.trim() || auditReason,
            affectedItems: items
              .filter((item) => item.lineKey.trim())
              .map((item) => ({
                lineKey: item.lineKey.trim(),
                quantity: Math.max(1, Number(item.quantity) || 1),
              })),
          },
        }),
      "售后案件已建立",
    );
    if (result.ok) onCreated();
    return result.ok;
  };
  return (
    <ConfirmAction
      title="管理员代建售后"
      pending={submit.pending}
      error={submit.error}
      onClose={onClose}
      onConfirm={async (auditReason) => {
        if (!orderId.trim()) {
          submit.setError("请填写订单 ID");
          return false;
        }
        if (!reason.trim()) {
          submit.setError("请填写客户诉求或现场原因");
          return false;
        }
        return create(auditReason);
      }}
    >
      <Field label="订单 ID">
        <div className="v1-inline-row">
          <input
            value={orderId}
            onChange={(event) => {
              setOrderId(event.target.value);
              setOrder(null);
            }}
          />
          <Button variant="secondary" type="button" busy={loadingOrder} onClick={loadOrder}>
            读取订单
          </Button>
        </div>
      </Field>
      {order && (
        <Notice type={order.settlement?.settlementKind ? "info" : "warning"}>
          订单状态 {order.status} · 版本 {order.version} ·{" "}
          {order.settlement?.settlementKind
            ? `结算方式 ${order.settlement.settlementKind}`
            : "尚未结算，后端会拒绝代建；请先完成收款或零应付结算"}
        </Notice>
      )}
      <div className="v1-two-col">
        <Field label="责任">
          <select
            value={responsibility}
            onChange={(event) => {
              const next = event.target.value as "CUSTOMER" | "MERCHANT";
              setResponsibility(next);
              if (next === "CUSTOMER") setSolution("REFUND");
            }}
          >
            <option value="MERCHANT">商家责任（错印/漏印/损坏）</option>
            <option value="CUSTOMER">客户自身原因</option>
          </select>
        </Field>
        <Field label="客户选择">
          <select
            value={solution}
            onChange={(event) => setSolution(event.target.value as "REFUND" | "REPRINT")}
          >
            <option value="REFUND">退款</option>
            <option value="REPRINT" disabled={responsibility === "CUSTOMER"}>
              免费补打/重做
            </option>
          </select>
        </Field>
      </div>
      <Field label="诉求与原因">
        <textarea value={reason} onChange={(event) => setReason(event.target.value)} />
      </Field>
      <Field label="受影响项目（可选，按报价行）">
        <div className="v1-inline-rows">
          {items.map((item, index) => (
            <div className="v1-inline-row" key={index}>
              <input
                placeholder="lineKey"
                value={item.lineKey}
                onChange={(event) =>
                  setItems((current) =>
                    current.map((row, rowIndex) =>
                      rowIndex === index ? { ...row, lineKey: event.target.value } : row,
                    ),
                  )
                }
              />
              <input
                type="number"
                min="1"
                placeholder="数量"
                value={item.quantity}
                onChange={(event) =>
                  setItems((current) =>
                    current.map((row, rowIndex) =>
                      rowIndex === index ? { ...row, quantity: event.target.value } : row,
                    ),
                  )
                }
              />
              <Button
                variant="secondary"
                onClick={() =>
                  setItems((current) => current.filter((_, rowIndex) => rowIndex !== index))
                }
              >
                移除
              </Button>
            </div>
          ))}
          <Button
            variant="secondary"
            onClick={() => setItems((current) => [...current, { lineKey: "", quantity: "1" }])}
          >
            添加受影响行
          </Button>
        </div>
      </Field>
      <Notice>
        客户自身原因仅在未登记实际开印时可退款；系统会按原折扣分摊给出建议金额，管理员可填写依据后覆写。
      </Notice>
    </ConfirmAction>
  );
}

export function AdminAfterSaleDetail() {
  const { id } = useParams();
  const access = useAdminOnly();
  const canProduction =
    access.capabilities.includes("*") ||
    access.capabilities.includes("production.manage");
  const submit = useSubmit();
  const [item, setItem] = useState<AfterSaleCase | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [sequence, setSequence] = useState(0);
  const [review, setReview] = useState(false);
  const [refund, setRefund] = useState(false);
  const [preview, setPreview] = useState<any | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [action, setAction] = useState<"reprint-ready" | "complete" | null>(null);
  const [orderInfo, setOrderInfo] = useState<{
    status?: string;
    packingStatus?: string;
    actualStartedAt?: string | null;
  } | null>(null);
  const [orderError, setOrderError] = useState("");
  useEffect(() => {
    if (!id) return;
    let active = true;
    setLoading(true);
    setError("");
    growthRequest<AfterSaleCase>(adminAfterSaleDetailPath(id))
      .then((value) => {
        if (active) setItem(value);
      })
      .catch((cause) => {
        if (active) setError(errorText(cause));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [id, sequence]);
  useEffect(() => {
    if (!item) return;
    let active = true;
    setOrderError("");
    setOrderInfo(null);
    growthRequest<any>(`/admin/orders/${encodeURIComponent(item.orderId)}`)
      .then((value) => {
        if (active) setOrderInfo(value);
      })
      .catch((cause) => {
        if (active) setOrderError(errorText(cause));
      });
    return () => {
      active = false;
    };
  }, [item?.orderId, sequence]);
  const reload = () => setSequence((value) => value + 1);
  const loadPreview = async () => {
    if (!item) return;
    setPreviewError("");
    try {
      const affected = Array.isArray(item.data?.affectedItems)
        ? item.data.affectedItems
        : [];
      const result = await growthRequest<any>(
        `/admin/after-sales/${encodeURIComponent(item.id)}/refund-preview`,
        { method: "POST", body: { affectedItems: affected } },
      );
      setPreview(result);
    } catch (cause) {
      setPreviewError(errorText(cause));
    }
  };
  if (loading)
    return (
      <div className="admin-growth v1-admin">
        <PageHeading title="售后详情" />
        <ResourceSkeleton variant="detail" />
      </div>
    );
  if (error || !item)
    return (
      <div className="admin-growth v1-admin">
        <PageHeading title="售后详情" />
        <ResourceState loading={false} error={error || "案件不存在"} reload={reload} />
      </div>
    );
  return (
    <div className="admin-growth v1-admin">
      <PageHeading
        title={`售后案件 · ${item.orderCode || item.orderId}`}
        description={`${sourceLabel(item.source)} · ${responsibilityLabel(item.responsibility)} · ${solutionLabel(item.solution)}`}
      />
      <AccessState access={access} />
      <ResourceState
        loading={false}
        error={error || submit.error}
        hasData
        reload={reload}
      />
      <Panel title="案件事实">
        <dl className="growth-facts v1-facts">
          <div>
            <dt>案件 ID</dt>
            <dd className="numeric">{item.id}</dd>
          </div>
          <div>
            <dt>状态</dt>
            <dd>
              <span className="soft-tag">{afterSaleStatusLabel(item.status)}</span>
            </dd>
          </div>
          <div>
            <dt>建议金额</dt>
            <dd className="numeric">{money(item.suggestedCents || 0)}</dd>
          </div>
          <div>
            <dt>批准金额</dt>
            <dd className="numeric">
              {item.approvedCents === null ? "尚未批准" : money(item.approvedCents)}
            </dd>
          </div>
          <div>
            <dt>调整依据</dt>
            <dd>{item.adjustmentBasis || "—"}</dd>
          </div>
          <div>
            <dt>原因</dt>
            <dd>{item.reason}</dd>
          </div>
          <div>
            <dt>退款登记</dt>
            <dd>
              {item.data?.refundRecordedAt || item.refund?.registeredAt
                ? `${new Date(
                    item.data?.refundRecordedAt || item.refund!.registeredAt!,
                  ).toLocaleString("zh-CN")} · ${money(
                    item.data?.refundAmountCents ??
                      item.refund?.amountCents ??
                      0,
                  )}`
                : "尚未登记线下退款"}
            </dd>
          </div>
        </dl>
      </Panel>
      <Panel title="重做与重新交付">
        {orderError && (
          <Notice type="error">
            <span role="alert">{orderError}</span>
          </Notice>
        )}
        <dl className="growth-facts v1-facts">
          <div>
            <dt>订单状态</dt>
            <dd>{orderInfo?.status || "加载中"}</dd>
          </div>
          <div>
            <dt>装袋状态</dt>
            <dd>{orderInfo?.packingStatus || "未装袋"}</dd>
          </div>
          <div>
            <dt>实际开印</dt>
            <dd>
              {orderInfo?.actualStartedAt
                ? new Date(orderInfo.actualStartedAt).toLocaleString("zh-CN")
                : "尚未记录"}
            </dd>
          </div>
          <div>
            <dt>重做任务</dt>
            <dd>
              {Array.isArray(item.data?.reprintTaskIds) && item.data.reprintTaskIds.length
                ? `${item.data.reprintTaskIds.length} 个（须全部打印完成）`
                : "无"}
            </dd>
          </div>
        </dl>
        <div className="button-row">
          <Button
            variant="secondary"
            onClick={() => setAction("reprint-ready")}
            disabled={!canProduction || item.status !== "REPRINTING"}
          >
            <RefreshCw size={15} />
            重做完成，等待重新交付
          </Button>
          <Button
            onClick={() => setAction("complete")}
            disabled={!canProduction || item.status !== "REPRINT_READY"}
          >
            <ShieldCheck size={15} />
            确认重新交付完成
          </Button>
        </div>
        <Notice type={canProduction ? "info" : "warning"}>
          {canProduction
            ? "重做完成要求订单已装袋且全部重做任务打印完成；重新交付完成要求订单已送达，并存在本案件创建之后的送达凭证。后端会逐项校验，未满足时会返回具体原因。"
            : "当前会话缺少生产管理能力，不能确认重做完成或重新交付；请联系主管理员授予 production.manage。"}
        </Notice>
      </Panel>
      <Panel title="审核动作">
        <div className="button-row">
          <Button variant="secondary" onClick={loadPreview}>
            <RefreshCw size={15} />
            计算受影响行与建议金额
          </Button>
          <Button
            onClick={() => setReview(true)}
            disabled={!["REQUESTED", "REVIEW_PENDING"].includes(item.status)}
          >
            <ShieldCheck size={15} />
            审核
          </Button>
          <Button
            variant="secondary"
            onClick={() => setRefund(true)}
            disabled={item.status !== "REFUND_APPROVED"}
          >
            <Download size={15} />
            登记线下退款
          </Button>
        </div>
        <Notice>
          批准表示同意方案，不代表已退款。必须线下完成退款后另行登记金额与原因；累计现金退款不得超过该单实收。
        </Notice>
        {previewError && (
          <Notice type="error">
            <span role="alert">{previewError}</span>
          </Notice>
        )}
        {preview && (
          <div className="v1-refund-preview">
            <h3>受影响行</h3>
            {(preview.affectedItems || []).map((row: any, index: number) => (
              <div className="v1-refund-row" key={`${row.lineKey || index}`}>
                <span>{row.label || row.lineKey || "报价行"}</span>
                <span>数量 {row.quantity ?? "—"}</span>
                <span className="muted">按原折扣分摊重算</span>
              </div>
            ))}
            <p>
              建议金额：
              <b>{money(preview.suggestedCents ?? 0)}</b>
              {preview.remainingCashCents !== undefined &&
                ` · 仍可退 ${money(preview.remainingCashCents)}`}
            </p>
          </div>
        )}
      </Panel>
      {review && (
        <ReviewAfterSale
          item={item}
          onClose={() => setReview(false)}
          onSaved={() => {
            setReview(false);
            reload();
          }}
        />
      )}
      {refund && (
        <RecordRefund
          item={item}
          defaultAmount={item.approvedCents ?? item.suggestedCents}
          onClose={() => setRefund(false)}
          onSaved={() => {
            setRefund(false);
            reload();
          }}
        />
      )}
      {action && (
        <AfterSaleActionModal
          item={item}
          action={action}
          onClose={() => setAction(null)}
          onSaved={() => {
            setAction(null);
            reload();
          }}
        />
      )}
    </div>
  );
}

function AfterSaleActionModal({
  item,
  action,
  onClose,
  onSaved,
}: {
  item: AfterSaleCase;
  action: "reprint-ready" | "complete";
  onClose: () => void;
  onSaved: () => void;
}) {
  const submit = useSubmit();
  const [reason, setReason] = useState("");
  const title = action === "reprint-ready" ? "重做完成" : "确认重新交付完成";
  const save = async () => {
    let body: ReturnType<typeof afterSaleActionPayload>;
    try {
      body = afterSaleActionPayload(item, reason);
    } catch (error) {
      submit.setError(errorText(error));
      return;
    }
    const result = await submit.run(
      () =>
        growthRequest(`/admin/after-sales/${encodeURIComponent(item.id)}/${action}`, {
          method: "POST",
          body,
        }),
      action === "reprint-ready" ? "已标记重做完成" : "已确认重新交付完成",
    );
    if (result.ok) onSaved();
  };
  return (
    <Modal title={title} onClose={onClose}>
      <div className="form-stack">
        <Notice type="warning">
          {action === "reprint-ready"
            ? "仅当订单已装袋且全部重做任务打印完成时才能确认；确认后案件进入等待重新交付。"
            : "仅当订单已送达且存在案件创建后的送达凭证时才能确认；确认后案件完成。"}
        </Notice>
        <Field label="操作原因">
          <textarea
            rows={3}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            required
          />
        </Field>
        {submit.error && <Notice type="error">{submit.error}</Notice>}
        <div className="form-actions">
          <Button variant="secondary" type="button" onClick={onClose}>
            取消
          </Button>
          <Button busy={submit.pending} disabled={!submit.online} onClick={save}>
            确认
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function ReviewAfterSale({
  item,
  onClose,
  onSaved,
}: {
  item: AfterSaleCase;
  onClose: () => void;
  onSaved: () => void;
}) {
  const submit = useSubmit();
  const [decision, setDecision] = useState<"APPROVE" | "REJECT">("APPROVE");
  const solution = item.solution === "REPRINT" ? "REPRINT" : "REFUND";
  const customerReason = item.responsibility === "CUSTOMER";
  const [approved, setApproved] = useState(String((item.suggestedCents || 0) / 100));
  const [basis, setBasis] = useState("");
  const [reason, setReason] = useState("");
  const save = async (auditReason: string) => {
    const result = await submit.run(
      () =>
        growthRequest(`/admin/after-sales/${encodeURIComponent(item.id)}/review`, {
          method: "POST",
          body: afterSaleReviewPayload(item, {
            decision,
            solution,
            approvedCents:
              decision === "APPROVE" && solution === "REFUND"
                ? Math.round(Number(approved || 0) * 100)
                : undefined,
            adjustmentBasis: basis,
            reason: reason.trim() || auditReason,
          }),
        }),
      "售后审核已记录",
    );
    if (result.ok) onSaved();
    return result.ok;
  };
  return (
    <ConfirmAction
      title="审核售后方案"
      pending={submit.pending}
      error={submit.error}
      onClose={onClose}
      onConfirm={async (auditReason) => {
        if (!reason.trim()) {
          submit.setError("请填写审核原因");
          return false;
        }
        return save(auditReason);
      }}
    >
      <div className="v1-two-col">
        <Field label="决定">
          <select
            value={decision}
            onChange={(event) => setDecision(event.target.value as "APPROVE" | "REJECT")}
          >
            <option value="APPROVE">批准</option>
            <option value="REJECT">拒绝</option>
          </select>
        </Field>
        <Field label="客户已确认方案">
          <input
            readOnly
            value={solution === "REPRINT" ? "免费补打 / 重做" : "退款"}
          />
        </Field>
      </div>
      <Notice>
        退款方案由客户确认；管理员不能直接改方案。实际退款前仍须核对是否已实际开印及未结打印尝试。
      </Notice>
      {decision === "APPROVE" && solution === "REFUND" && (
        <>
          <Field
            label={
              customerReason
                ? "批准金额（客户自身原因按规则全额退回实收）"
                : "批准金额（元）"
            }
          >
            <input
              type="number"
              min="0"
              step="0.01"
              readOnly={customerReason}
              value={approved}
              onChange={(event) => setApproved(event.target.value)}
            />
          </Field>
          {!customerReason && (
            <Field label="与建议金额不同的依据">
              <textarea value={basis} onChange={(event) => setBasis(event.target.value)} />
            </Field>
          )}
        </>
      )}
      <Field label="审核原因（必填）">
        <textarea value={reason} onChange={(event) => setReason(event.target.value)} />
      </Field>
    </ConfirmAction>
  );
}

function RecordRefund({
  item,
  defaultAmount,
  onClose,
  onSaved,
}: {
  item: AfterSaleCase;
  defaultAmount: number;
  onClose: () => void;
  onSaved: () => void;
}) {
  const submit = useSubmit();
  const [amount, setAmount] = useState(String(defaultAmount / 100));
  const [reason, setReason] = useState("");
  const save = async (auditReason: string) => {
    const cents = Math.round(Number(amount || 0) * 100);
    if (item.approvedCents === null || cents !== item.approvedCents) {
      submit.setError("实际退款金额必须与本次批准金额一致；金额不符请重新审核");
      return false;
    }
    const result = await submit.run(
      () =>
        growthRequest(`/admin/after-sales/${encodeURIComponent(item.id)}/record-refund`, {
          method: "POST",
          body: {
            expectedVersion: item.version,
            amountCents: cents,
            reason: reason.trim() || auditReason,
          },
        }),
      "线下退款已登记",
    );
    if (result.ok) onSaved();
    return result.ok;
  };
  return (
    <ConfirmAction
      title="登记线下退款"
      pending={submit.pending}
      error={submit.error}
      onClose={onClose}
      onConfirm={async (auditReason) => {
        if (!reason.trim()) {
          submit.setError("请填写退款方式与凭证说明");
          return false;
        }
        return save(auditReason);
      }}
    >
      <Field label="实际退款金额（元）">
        <input
          type="number"
          min="0"
          step="0.01"
          readOnly
          value={amount}
          onChange={(event) => setAmount(event.target.value)}
        />
      </Field>
      <Field label="线下退款说明（必填）">
        <textarea value={reason} onChange={(event) => setReason(event.target.value)} />
      </Field>
      <Notice>该操作只登记已发生的线下退款，不调用支付平台，也不自动修改原订单报价。</Notice>
    </ConfirmAction>
  );
}

// ---------------------------------------------------------------------------
// Deletion requests / trash / exports
// ---------------------------------------------------------------------------

type DeletionRow = {
  id: string;
  version: number;
  status: string;
  accountMasked?: string;
  emailMasked?: string;
  displayName?: string;
  reason?: string;
  createdAt?: string;
  blockers?: string[];
};

type TrashRow = {
  id: string;
  version?: number;
  targetType?: string;
  targetId?: string;
  type?: string;
  name?: string;
  reason?: string;
  deletedAt?: string;
  purgeAt?: string;
  status?: string;
  by?: string;
  [key: string]: any;
};

export function AdminDataOperationsPage() {
  const access = useAdminOnly();
  const available = allowedDataOperationTabs(access.capabilities);
  const [tab, setTab] = useState<AdminDataOperationTab>("deletion");
  const active = available.includes(tab) ? tab : available[0];
  useEffect(() => {
    if (active && active !== tab) setTab(active);
  }, [active, tab]);
  const tabs: {
    value: AdminDataOperationTab;
    label: string;
    Icon: typeof UserX;
  }[] = [
    { value: "deletion", label: "注销记录", Icon: UserX },
    { value: "trash", label: "删除与恢复", Icon: Trash2 },
    { value: "exports", label: "受控导出", Icon: Download },
  ];
  return (
    <div className="admin-growth v1-admin">
      <PageHeading
        title="数据与合规操作"
        description="注销、回收站与受控导出各自校验独立能力；只加载当前账号获准的标签页。"
      />
      <AccessState access={access} />
      {!access.loading && !access.error && access.allowed && (
        available.length ? (
          <>
            <div className="v1-tabs" role="tablist">
              {tabs
                .filter(({ value }) => available.includes(value))
                .map(({ value, label, Icon }) => (
                  <button
                    key={value}
                    role="tab"
                    aria-selected={active === value}
                    className={active === value ? "active" : ""}
                    onClick={() => setTab(value)}
                  >
                    <Icon size={16} />
                    {label}
                  </button>
                ))}
            </div>
            {active === "deletion" && <DeletionRequests />}
            {active === "trash" && <TrashOperations />}
            {active === "exports" && <ExportOperations />}
          </>
        ) : (
          <Notice type="warning">
            当前账号没有数据与合规操作权限。注销审核需要 customer.manage，
            回收站需要 data.manage，受控导出需要 export.manage。
          </Notice>
        )
      )}
    </div>
  );
}

function DeletionRequests() {
  const list = useList<DeletionRow>("/admin/deletion-requests");
  const submit = useSubmit();
  const [target, setTarget] = useState<DeletionRow | null>(null);
  return (
    <>
      <ResourceState
        loading={list.loading && !list.items.length}
        error={list.error}
        empty={!list.loading && !list.error && !list.items.length}
        hasData={!!list.items.length}
        reload={list.reload}
      />
      {list.items.length > 0 && (
        <Panel>
          <div className="growth-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>账号</th>
                  <th>状态</th>
                  <th>阻断项</th>
                  <th>申请时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {list.items.map((row) => (
                  <tr key={row.id}>
                    <td>
                      <b>{row.displayName || row.accountMasked || row.emailMasked || row.id}</b>
                      <small>{row.emailMasked || row.accountMasked || "—"}</small>
                    </td>
                    <td>{row.status}</td>
                    <td>
                      {row.blockers?.length ? (
                        <span className="red-text">{row.blockers.join("、")}</span>
                      ) : (
                        "无未结履约"
                      )}
                    </td>
                    <td className="numeric">
                      {row.createdAt ? new Date(row.createdAt).toLocaleString("zh-CN") : "—"}
                    </td>
                    <td>
                      {row.status === "REQUESTED" ? <Button variant="secondary" onClick={() => setTarget(row)}>
                        处理历史申请
                      </Button> : <span className="muted">{row.status === "RECOVERY" ? "恢复期内，用户可登录找回" : "无需管理员审核"}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {list.nextCursor && (
            <Button variant="secondary" busy={list.loading} onClick={list.more}>
              加载更多
            </Button>
          )}
        </Panel>
      )}
      {target && (
        <ConfirmAction
          title={`通过注销申请 · ${target.displayName || target.id}`}
          pending={submit.pending}
          error={submit.error}
          onClose={() => setTarget(null)}
          onConfirm={async (reason) => {
            const result = await submit.run(
              () =>
                growthRequest(
                  `/admin/deletion-requests/${encodeURIComponent(target.id)}/approve`,
                  {
                    method: "POST",
                    body: { expectedVersion: target.version, reason },
                  },
                ),
              "注销申请已通过并进入清理流程",
            );
            if (result.ok) {
              setTarget(null);
              list.reload();
            }
            return result.ok;
          }}
        >
          <Notice>
            通过前服务端会重新检查未结订单、售后与有效引用。清理保留匿名对账汇总与最小删除审计，不授予客户直接永久删除权。
          </Notice>
          {target.blockers?.length ? (
            <Notice type="error">
              <span role="alert">存在阻断项：{target.blockers.join("、")}</span>
            </Notice>
          ) : null}
        </ConfirmAction>
      )}
    </>
  );
}

function TrashOperations() {
  const list = useList<TrashRow>("/admin/trash");
  const submit = useSubmit();
  const [mode, setMode] = useState<"create" | "restore" | "purge" | null>(null);
  const [row, setRow] = useState<TrashRow | null>(null);
  const [reason, setReason] = useState("");
  const [preview, setPreview] = useState<any | null>(null);
  const [create, setCreate] = useState({
    targetType: "ORDER",
    targetId: "",
    expectedVersion: "",
    reason: "",
  });
  const [issue, setIssue] = useState("");
  const restorePreview = async (entry: TrashRow) => {
    setIssue("");
    try {
      const result = await growthRequest<any>(
        `/admin/trash/${encodeURIComponent(entry.id)}/restore-preview`,
        { method: "POST", body: {} },
      );
      setPreview(result);
      setRow(entry);
      setMode("restore");
    } catch (cause) {
      setIssue(errorText(cause));
    }
  };
  const submitEntry = async (auditReason: string) => {
    if (!row && mode !== "create") return false;
    const result = await submit.run(
      () =>
        mode === "create"
          ? growthRequest("/admin/trash", {
              method: "POST",
              body: {
                targetType: create.targetType,
                targetId: create.targetId.trim(),
                expectedVersion: Number(create.expectedVersion),
                reason: create.reason.trim() || auditReason,
              },
            })
          : growthRequest(`/admin/trash/${encodeURIComponent(row!.id)}/${mode}`, {
              method: "POST",
              body: {
                reason: reason.trim() || auditReason,
                expectedVersion: row!.version ?? 1,
              },
            }),
      mode === "create" ? "删除已进入 24 小时回收站" : mode === "restore" ? "对象已恢复" : "清理请求已登记，请刷新核实最终状态",
    );
    if (result.ok) {
      setMode(null);
      setRow(null);
      setPreview(null);
      list.reload();
    }
    return result.ok;
  };
  return (
    <>
      <div className="button-row v1-toolbar">
        <Button onClick={() => setMode("create")}>
          <Trash2 size={16} />
          发起受控删除
        </Button>
        <span className="muted">回收站内对象保留 24 小时；过期不可恢复。</span>
      </div>
      {issue && (
        <Notice type="error">
          <span role="alert">{issue}</span>
        </Notice>
      )}
      <ResourceState
        loading={list.loading && !list.items.length}
        error={list.error}
        empty={!list.loading && !list.error && !list.items.length}
        hasData={!!list.items.length}
        reload={list.reload}
      />
      {list.items.length > 0 && (
        <Panel>
          <div className="growth-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>对象</th>
                  <th>删除原因</th>
                  <th>删除时间</th>
                  <th>剩余恢复</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {list.items.map((entry) => {
                  const deleted = entry.deletedAt ? Date.parse(entry.deletedAt) : Date.now();
                  const purge = entry.purgeAt
                    ? Date.parse(entry.purgeAt)
                    : deleted + 24 * 3600 * 1000;
                  const minutes = Math.max(0, Math.floor((purge - Date.now()) / 60000));
                  return (
                    <tr key={entry.id}>
                      <td>
                        <b>{entry.name || entry.targetId || entry.id}</b>
                        <small>{entry.targetType || entry.type || "对象"}</small>
                      </td>
                      <td>{entry.reason || "—"}<small>{entry.status === "PURGE_PENDING" ? "文件清理待完成，将自动重试" : entry.status === "PURGED" ? "已永久清理" : entry.status === "RESTORED" ? "已恢复" : "回收站中"}</small></td>
                      <td className="numeric">
                        {entry.deletedAt
                          ? new Date(entry.deletedAt).toLocaleString("zh-CN")
                          : "—"}
                      </td>
                      <td>
                        {minutes > 0 ? `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分` : "已过期"}
                      </td>
                      <td>
                        <div className="button-row">
                          <Button
                            variant="secondary"
                            disabled={minutes <= 0}
                            onClick={() => restorePreview(entry)}
                          >
                            恢复预览
                          </Button>
                          <Button
                            variant="danger-outline"
                            onClick={() => {
                              setRow(entry);
                              setReason("");
                              setMode("purge");
                            }}
                          >
                            永久清理
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {list.nextCursor && (
            <Button variant="secondary" busy={list.loading} onClick={list.more}>
              加载更多
            </Button>
          )}
        </Panel>
      )}
      {mode && (
        <ConfirmAction
          title={mode === "create" ? "发起受控删除" : mode === "restore" ? "确认恢复对象" : "永久清理对象"}
          pending={submit.pending}
          error={submit.error}
          onClose={() => {
            setMode(null);
            setPreview(null);
          }}
          onConfirm={async (auditReason) => {
            if (mode === "create" && (!create.targetId.trim() || !create.expectedVersion)) {
              submit.setError("请填写对象 ID 与当前版本");
              return false;
            }
            if (mode !== "create" && !reason.trim()) {
              submit.setError("请填写操作原因");
              return false;
            }
            return submitEntry(auditReason);
          }}
        >
          {mode === "create" ? (
            <>
              <Field label="对象类型">
                <select
                  value={create.targetType}
                  onChange={(event) => setCreate((current) => ({ ...current, targetType: event.target.value }))}
                >
                  <option value="ORDER">已付款订单</option>
                  <option value="BATCH">配送批次</option>
                  <option value="ASSET">文件资产</option>
                  <option value="CUSTOMER_ACCOUNT">客户账号</option>
                </select>
              </Field>
              <Field label="对象 ID">
                <input
                  value={create.targetId}
                  onChange={(event) => setCreate((current) => ({ ...current, targetId: event.target.value }))}
                />
              </Field>
              <Field label="当前版本">
                <input
                  type="number"
                  min="1"
                  value={create.expectedVersion}
                  onChange={(event) =>
                    setCreate((current) => ({ ...current, expectedVersion: event.target.value }))
                  }
                />
              </Field>
              <Field label="删除原因">
                <textarea
                  value={create.reason}
                  onChange={(event) => setCreate((current) => ({ ...current, reason: event.target.value }))}
                />
              </Field>
              <Notice>
                已付款订单仅在履约与售后全部结束后可删除；未结生产、配送或退款不能通过删除中断。删除进入 24 小时回收站。
              </Notice>
            </>
          ) : (
            <>
              {mode === "restore" && (
                <Notice>
                  {preview?.blockers?.length
                    ? `存在恢复冲突：${preview.blockers
                        .map((blocker: any) => blocker.message || blocker)
                        .join("；")}`
                    : "恢复预览未报告新冲突；恢复后仍需满足当前领域守卫，不会自动恢复装袋、收款或配送分配。"}
                </Notice>
              )}
              {mode === "purge" && (
                <Notice type="error">永久清理不可恢复；服务端会重新检查有效引用。</Notice>
              )}
              <Field label="操作原因">
                <textarea value={reason} onChange={(event) => setReason(event.target.value)} />
              </Field>
            </>
          )}
        </ConfirmAction>
      )}
    </>
  );
}

function ExportOperations() {
  const submit = useSubmit();
  const [kind, setKind] = useState<"ORDERS" | "FINANCE">("ORDERS");
  const [status, setStatus] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [q, setQ] = useState("");
  const [fields, setFields] = useState<string[]>(["orderId", "pickupCode", "status"]);
  const [reason, setReason] = useState("");
  const [job, setJob] = useState<ExportJobView | null>(null);
  const [error, setError] = useState("");
  const fieldOptions =
    kind === "ORDERS"
      ? ["orderId", "pickupCode", "status", "totalCents", "createdAt", "deliveredAt"]
      : ["orderId", "payableCents", "cashReceivedCents", "refundCents", "settlementKind", "createdAt"];
  const create = async (auditReason: string) => {
    setError("");
    const result = await submit.run(
      () =>
        growthRequest<ExportJobView>("/admin/exports", {
          method: "POST",
          body: exportPayload(
            kind,
            Object.fromEntries(
              Object.entries({ q, status, from, to }).filter(([, value]) => value.trim()),
            ),
            fields,
            reason.trim() || auditReason,
          ),
        }),
      "导出任务已受理",
    );
    if (result.ok) setJob(result.data);
    return result.ok;
  };
  useEffect(() => {
    if (!job || !["QUEUED", "PROCESSING"].includes(job.status)) return;
    const timer = window.setTimeout(async () => {
      try {
        const next = await growthRequest<ExportJobView>(
          `/admin/exports/${encodeURIComponent(job.id)}`,
        );
        setJob(next);
      } catch (cause) {
        setError(errorText(cause));
      }
    }, 2500);
    return () => window.clearTimeout(timer);
  }, [job]);
  return (
    <>
      <Panel title="新建受控导出">
        <div className="v1-two-col">
          <Field label="导出类型">
            <select value={kind} onChange={(event) => setKind(event.target.value as "ORDERS" | "FINANCE")}>
              <option value="ORDERS">订单</option>
              <option value="FINANCE">收退款对账</option>
            </select>
          </Field>
          <Field label="订单状态筛选">
            <input value={status} onChange={(event) => setStatus(event.target.value)} placeholder="如 DELIVERED" />
          </Field>
          <Field label="开始日期">
            <input type="date" value={from} onChange={(event) => setFrom(event.target.value)} />
          </Field>
          <Field label="结束日期">
            <input type="date" value={to} onChange={(event) => setTo(event.target.value)} />
          </Field>
        </div>
        <Field label="搜索">
          <input value={q} onChange={(event) => setQ(event.target.value)} />
        </Field>
        <Field label="字段白名单">
          <div className="v1-permission-grid">
            {fieldOptions.map((field) => (
              <label className="checkbox-label" key={field}>
                <input
                  type="checkbox"
                  checked={fields.includes(field)}
                  onChange={(event) =>
                    setFields((current) =>
                      event.target.checked
                        ? [...current, field]
                        : current.filter((value) => value !== field),
                    )
                  }
                />
                {field}
              </label>
            ))}
          </div>
        </Field>
        <Field label="导出原因">
          <textarea value={reason} onChange={(event) => setReason(event.target.value)} />
        </Field>
        <Button
          busy={submit.pending}
          onClick={() => (reason.trim() ? create(reason) : submit.setError("请填写导出原因"))}
        >
          申请导出
        </Button>
        {submit.error && (
          <Notice type="error">
            <span role="alert">{submit.error}</span>
          </Notice>
        )}
        <Notice>
          导出文件 24 小时后失效；仅返回白名单字段，不包含密码、令牌、SMTP 密钥或原打印文件。
        </Notice>
      </Panel>
      {error && (
        <Notice type="error">
          <span role="alert">{error}</span>
        </Notice>
      )}
      {job && (
        <Panel title={`导出任务 · ${job.id}`}>
          <p>
            状态：<span className="soft-tag">{job.status}</span>
            {job.rowCount !== undefined && ` · ${job.rowCount} 行`}
          </p>
          {job.expiresAt && (
            <p className="muted">到期：{new Date(job.expiresAt).toLocaleString("zh-CN")}</p>
          )}
          {job.error && <Notice type="error">{job.error}</Notice>}
          {job.status === "READY" && (
            <a
              className="btn secondary"
              href={`${apiBase()}/admin/exports/${encodeURIComponent(job.id)}/download`}
              rel="noreferrer"
            >
              <Download size={16} />
              下载受控导出
            </a>
          )}
          {["QUEUED", "PROCESSING"].includes(job.status) && (
            <p className="muted">
              <RefreshCw size={14} /> 正在等待服务端生成；受理不等于可下载。
            </p>
          )}
        </Panel>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Permission editor for the existing staff page
// ---------------------------------------------------------------------------

export function AdminPermissionEditor({
  staff,
  open,
  onClose,
  onSaved,
}: {
  staff: StaffCapabilityRow & { name?: string; capabilities?: string[] };
  open: boolean;
  onClose: () => void;
  onSaved?: () => void;
}) {
  const submit = useSubmit();
  const [selected, setSelected] = useState<string[]>(staff.capabilities || []);
  const [reason, setReason] = useState("");
  useEffect(() => {
    if (open) {
      setSelected(staff.capabilities || []);
      setReason("");
    }
  }, [open, staff.id, staff.version]);
  if (!open) return null;
  const save = async (auditReason: string) => {
    const result = await submit.run(
      () =>
        growthRequest(`/admin/staff/${encodeURIComponent(staff.id)}`, {
          method: "PATCH",
          body: staffCapabilityPayload(staff, selected, reason.trim() || auditReason),
        }),
      "员工权限已更新",
    );
    if (result.ok) {
      onSaved?.();
      onClose();
    }
    return result.ok;
  };
  return (
    <Modal title={`权限配置 · ${staff.name || staff.id}`} onClose={onClose}>
      <Notice>
        仅主管理员可授予能力；最后一名完整管理员不能被降级。配送员不能获得管理能力，服务端会再次校验。
      </Notice>
      <div className="v1-permission-grid">
        {CAPABILITY_CATALOG.map((capability) => (
          <label
            className={`checkbox-label ${capability.enforced ? "" : "disabled"}`}
            key={capability.key}
          >
            <input
              type="checkbox"
              disabled={!capability.enforced || staff.role === "COURIER"}
              checked={selected.includes(capability.key)}
              onChange={(event) =>
                setSelected((current) =>
                  event.target.checked
                    ? capability.key === "*"
                      ? ["*"]
                      : [...current.filter((item) => item !== "*"), capability.key]
                    : current.filter((item) => item !== capability.key),
                )
              }
            />
            <span>
              <b>{capability.label}</b>
              <small>{capability.description}</small>
            </span>
          </label>
        ))}
      </div>
      <Field label="变更原因">
        <textarea value={reason} onChange={(event) => setReason(event.target.value)} />
      </Field>
      {submit.error && (
        <Notice type="error">
          <span role="alert">{submit.error}</span>
        </Notice>
      )}
      <div className="button-row">
        <Button variant="secondary" onClick={onClose}>
          取消
        </Button>
        <Button
          busy={submit.pending}
          onClick={() => (reason.trim() ? save(reason) : submit.setError("请填写权限变更原因"))}
        >
          <KeyRound size={16} />
          保存权限
        </Button>
      </div>
    </Modal>
  );
}
