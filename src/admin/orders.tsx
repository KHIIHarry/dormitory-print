import { slotCurrentlySelectable } from "../customer/checkout";
import { growthRequest } from "../growth/client";
import { formatDisplayTime } from "../lib/display-format";
import {
  requestedSlotOf,
  confirmedSlotOf,
  slotBlockLabel,
} from "../v1/customer";
import { AdminSupplementaryPanel } from "../v1/supplementary";
import {
  assertQuote,
  settlementInput,
  settlementWithReceipt,
} from "../growth/pricing";
import { AdminAccessLinks } from "../growth/order-links";
import { useIdentity } from "../growth/identity";
import { useEffect, useState } from "react";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Clock,
  FileText,
  Filter,
  MapPin,
  MoreHorizontal,
  Plus,
  Receipt,
  Search,
  Star,
  Truck,
} from "lucide-react";
import { ADMIN, useStore } from "../lib/store";
import { money } from "../lib/api";
import { campusOfOrder, slotsForCampus } from "../lib/campus";
import { CampusFilter, CampusTag, orderCampusId } from "./campus-filter";
import {
  hasWageBlocker,
  trashBlockers,
  type TrashBlocker,
} from "../lib/trash-blockers";
import type { Order, Slot } from "../lib/types";
import {
  Badge,
  Button,
  Empty,
  Field,
  FileRow,
  Modal,
  Notice,
  PageHeading,
  Panel,
  PriceRows,
} from "../components/ui";
import { ResourceSkeleton, RefreshStatus } from "../components/feedback";
import { ErrorState } from "../components/ui";
import { DeliveryProofPanel } from "../components/delivery-proof";
export function AdminOrders() {
  const { data, resources, refresh } = useStore();
  const resource = resources.orders;
  const ready = !resource || resource.hasData;
  const [params, setParams] = useSearchParams();
  const [more, setMore] = useState(false);
  const q = params.get("q") || "";
  const status = params.get("status") || "";
  const campus = params.get("campus") || "";
  const building = params.get("building") || "";
  const patch = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  };
  const patchCampus = (value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set("campus", value);
    else next.delete("campus");
    next.delete("building");
    setParams(next);
    setPage(1);
  };
  const filtered = data.orders.filter(
    (o) =>
      (!status || o.status === status) &&
      (!campus ||
        campusOfOrder(data.campuses, data.buildings, o)?.id === campus) &&
      (!building || o.locationId === building) &&
      `${o.pickupCode} ${o.nickname} ${o.phone} ${o.files.map((f) => f.name).join(" ")}`
        .toLowerCase()
        .includes(q.trim().toLowerCase()),
  );
  const [page, setPage] = useState(1);
  return (
    <>
      <PageHeading
        title="订单管理"
        description="从付款确认到送达，查看每一份订单的完整进度。"
      />
      <Panel className="filter-panel">
        <div className="filter-row">
          <div className="search-input grow">
            <Search size={18} />
            <input
              placeholder="搜索提取码、昵称、手机号或文件名"
              value={q}
              onChange={(e) => {
                patch("q", e.target.value);
                setPage(1);
              }}
            />
          </div>
          <select
            aria-label="订单状态"
            value={status}
            onChange={(e) => {
              patch("status", e.target.value);
              setPage(1);
            }}
          >
            <option value="">全部状态</option>
            {[
              ["PENDING_CONFIRMATION", "待确认"],
              ["PENDING_PRINT", "待打印"],
              ["READY_FOR_DELIVERY", "待配送"],
              ["DELIVERING", "配送中"],
              ["DELIVERED", "已送达"],
              ["DELIVERY_EXCEPTION", "配送异常"],
              ["CANCELLED", "已撤销"],
            ].map(([v, l]) => (
              <option value={v} key={v}>
                {l}
              </option>
            ))}
          </select>
          <CampusFilter
            campuses={data.campuses}
            value={campus}
            onChange={patchCampus}
          />
          <select
            aria-label="楼栋筛选"
            value={building}
            onChange={(e) => patch("building", e.target.value)}
          >
            <option value="">全部楼栋</option>
            {data.buildings
              .filter((b) => !campus || b.campusId === campus)
              .map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
          </select>
          <Button variant="secondary" onClick={() => setMore(!more)}>
            <Filter size={16} />
            更多筛选
          </Button>
        </div>
        {more && (
          <div className="filter-extra">
            <Field label="下单日期">
              <input
                type="date"
                value={params.get("date") || ""}
                onChange={(e) => patch("date", e.target.value)}
              />
            </Field>
            <Field label="预约时段">
              <select
                value={params.get("slot") || ""}
                onChange={(e) => patch("slot", e.target.value)}
              >
                <option value="">全部时段</option>
                {data.slots.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.date} {s.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="配送批次">
              <select
                value={params.get("batch") || ""}
                onChange={(e) => patch("batch", e.target.value)}
              >
                <option value="">全部批次</option>
                {data.batches.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
            </Field>
            <Button variant="text" onClick={() => setParams({})}>
              重置筛选
            </Button>
          </div>
        )}
      </Panel>
      <Panel>
        <div className="panel-heading">
          <h2>
            全部订单{" "}
            <span className="muted">{ready ? filtered.length : "—"}</span>
          </h2>
          <span className="muted">按下单时间排序</span>
        </div>
        {ready && resource && (
          <RefreshStatus
            loading={resource.loading}
            error={resource.error || undefined}
            retry={refresh}
          />
        )}
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>提取码 / 下单时间</th>
                <th>联系人</th>
                <th>打印文件</th>
                <th>配送信息</th>
                <th>金额</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {!ready && (
                <tr>
                  <td colSpan={7}>
                    {resource?.error ? (
                      <ErrorState message={resource.error} retry={refresh} />
                    ) : (
                      <ResourceSkeleton variant="table" />
                    )}
                  </td>
                </tr>
              )}
              {ready &&
                filtered
                  .filter(
                    (o) =>
                      (!params.get("date") ||
                        o.createdAt.startsWith(params.get("date")!)) &&
                      (!params.get("slot") ||
                        o.slotId === params.get("slot")) &&
                      (!params.get("batch") ||
                        o.batchId === params.get("batch")),
                  )
                  .slice((page - 1) * 10, page * 10)
                  .map((o) => (
                    <tr key={o.id}>
                      <td>
                        <Link
                          className="table-code"
                          to={`${ADMIN}/orders/${o.id}`}
                        >
                          {o.pickupCode}
                          {o.priority && <Star size={12} fill="currentColor" />}
                        </Link>
                        <small>{formatDisplayTime(o.createdAt)}</small>
                      </td>
                      <td>
                        {o.nickname}
                        <small>{o.phone}</small>
                      </td>
                      <td>
                        <span className="table-filename">
                          {o.files[0]?.name}
                        </span>
                        <small>
                          {o.files.length} 个文件 /{" "}
                          {o.files.reduce(
                            (sum, f) => sum + f.segments.length,
                            0,
                          )}{" "}
                          个任务
                        </small>
                      </td>
                      <td>
                        <CampusTag
                          campus={campusOfOrder(
                            data.campuses,
                            data.buildings,
                            o,
                          )}
                        />
                        <small>
                          {
                            data.buildings.find((b) => b.id === o.locationId)
                              ?.name
                          }{" "}
                          · {data.slots.find((s) => s.id === o.slotId)?.name}{" "}
                          {o.addressReview && "· 地址待复核"}
                        </small>
                      </td>
                      <td className="numeric">{money(o.totalCents)}</td>
                      <td>
                        <Badge status={o.status} />
                      </td>
                      <td>
                        <Link
                          className="text-link"
                          to={`${ADMIN}/orders/${o.id}`}
                        >
                          {o.status === "PENDING_CONFIRMATION"
                            ? "核对收款"
                            : "查看详情"}
                          <ChevronRight size={14} />
                        </Link>
                      </td>
                    </tr>
                  ))}
            </tbody>
          </table>
        </div>
        {ready && !filtered.length && (
          <Empty title="没有符合条件的订单" text="调整筛选条件再试试。" />
        )}
        <div className="pagination">
          <span>共 {ready ? filtered.length : "—"} 条订单 · 每页 10 条</span>
          <div>
            <button
              className="icon-btn"
              disabled={!ready || page === 1}
              onClick={() => setPage((p) => p - 1)}
              aria-label="上一页"
            >
              <ChevronLeft size={16} />
            </button>
            <b>{page}</b>
            <button
              className="icon-btn"
              disabled={!ready || page * 10 >= filtered.length}
              onClick={() => setPage((p) => p + 1)}
              aria-label="下一页"
            >
              <ChevronRight size={16} />
            </button>
          </div>
        </div>
      </Panel>
    </>
  );
}
export function AdminOrderDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { data, act, updateOrder, toast, refresh, actionIssues } = useStore();
  const [payment, setPayment] = useState(false);
  const [voiding, setVoiding] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteBlockers, setDeleteBlockers] = useState<TrashBlocker[]>([]);
  const [reason, setReason] = useState("");
  const [received, setReceived] = useState("");
  const [zeroConfirmed, setZeroConfirmed] = useState(false);
  const [paymentDifferenceAccepted, setPaymentDifferenceAccepted] =
    useState(false);
  const [receiptId, setReceiptId] = useState("");
  const [receiptNotice, setReceiptNotice] = useState("");
  const [recordedReceiptOrder, setRecordedReceiptOrder] = useState<
    string | null
  >(null);
  const { staff, generation } = useIdentity();
  const [note, setNote] = useState("");
  const [internal, setInternal] = useState(false);
  const [busy, setBusy] = useState(false);
  const order = data.orders.find((o) => o.id === id);
  if (!order)
    return (
      <Empty
        title="订单不存在或无权访问"
        action={<Link to={`${ADMIN}/orders`}>返回订单列表</Link>}
      />
    );
  const paymentBlockers = (order.blockers || []).filter(
    (blocker) => blocker.code !== "PAYMENT_MISMATCH",
  );
  let paymentDifference: number | null = null;
  try {
    paymentDifference =
      settlementInput(order.totalCents, received, true, true).receivedCents -
      order.totalCents;
  } catch {
    // A difference is shown only for a valid cash amount; submit explains invalid input.
  }
  const confirm = async () => {
    let settlement: ReturnType<typeof settlementWithReceipt>;
    try {
      if (!order.quote) throw new Error("缺少完整报价快照，请刷新订单后核对");
      assertQuote(order.quote);
      if (
        order.quote.id !== order.currentQuoteId ||
        order.quote.totalCents !== order.totalCents
      )
        throw new Error("订单报价不一致，请重新加载");
      settlement = settlementWithReceipt(
        order.totalCents,
        received,
        zeroConfirmed,
        order.unmatchedReceipts || [],
        receiptId,
        recordedReceiptOrder === order.id,
        paymentDifferenceAccepted,
      );
      if (!order.currentQuoteId)
        throw new Error("缺少订单锁定报价，请刷新订单后核对");
      if (paymentBlockers.length)
        throw new Error("订单存在风控阻断，请先人工核对处理");
    } catch (e) {
      toast((e as Error).message);
      return;
    }
    setBusy(true);
    const ok = await act(
      `/admin/orders/${id}/confirm-payment`,
      {
        expectedVersion: order.version,
        contentRevision: order.contentRevision,
        quoteId: order.currentQuoteId,
        ...settlement,
        note,
      },
      (s) => ({
        ...s,
        orders: s.orders.map((o) =>
          o.id === id
            ? {
                ...o,
                status: "PENDING_PRINT",
                settlement: {
                  settlementKind: settlement.settlementKind,
                  cashReceivedCents: settlement.receivedCents,
                  discountCents: o.quote?.discountCents ?? 0,
                  confirmedAt: new Date().toISOString(),
                  confirmedBy: staff?.id || "admin",
                  payableCents: o.totalCents,
                  paymentDifferenceCents:
                    settlement.receivedCents - o.totalCents,
                  paymentDifferenceAccepted:
                    !!settlement.paymentDifferenceAccepted,
                  note,
                },
                version: o.version + 1,
                history: [
                  {
                    title:
                      settlement.settlementKind === "ZERO_PAYABLE"
                        ? "人工零元结算已确认"
                        : "线下付款已确认",
                    time: new Date().toLocaleString("zh-CN"),
                    description:
                      settlement.settlementKind === "ZERO_PAYABLE"
                        ? "优惠后应付0元，经人工确认进入生产"
                        : `实收 ${money(settlement.receivedCents)}`,
                  },
                  ...o.history,
                ],
              }
            : o,
        ),
      }),
      "POST",
      undefined,
      (error) => {
        const details = (error as { details?: { receiptId?: string } }).details;
        if (details?.receiptId) {
          setRecordedReceiptOrder(order.id);
          setReceiptNotice(
            "款项已登记为待核对实收。协调并接受有效新时段后，请选择该记录完成结算，无需再次付款。",
          );
          void refresh();
        }
      },
    );
    setBusy(false);
    if (ok) {
      setPayment(false);
      toast(
        settlement.settlementKind === "ZERO_PAYABLE"
          ? "零元结算已确认，订单进入待打印"
          : "收款已确认，订单进入待打印",
      );
    }
  };
  return (
    <>
      <Link className="back-link" to={`${ADMIN}/orders`}>
        <ArrowLeft size={16} />
        返回订单列表
      </Link>
      <PageHeading
        title={`订单 ${order.pickupCode}`}
        description={`下单 ${formatDisplayTime(order.createdAt)} · 内容版本 v${order.contentRevision}`}
        action={
          <div className="button-row">
            <Button
              variant="secondary"
              onClick={() => {
                setNote(order.note);
                setInternal(true);
              }}
            >
              内部备注
            </Button>
            <Button
              variant="secondary"
              onClick={() =>
                updateOrder(order.id, { priority: !order.priority }, "priority")
              }
            >
              <Star size={16} />
              {order.priority ? "取消优先" : "设为优先"}
            </Button>
            {order.status === "DELIVERING" && (
              <Link
                className="btn secondary"
                to={`/courier/orders/${order.id}/proof`}
              >
                <Truck size={16} />
                处理配送交付
              </Link>
            )}
            {order.status === "PENDING_CONFIRMATION" &&
              (staff?.capabilities?.includes("*") ||
                staff?.capabilities?.includes("payment.manage")) && (
                <Button
                  disabled={!!paymentBlockers.length || !order.currentQuoteId}
                  onClick={() => {
                    setZeroConfirmed(false);
                    setPaymentDifferenceAccepted(false);
                    setNote("");
                    setReceived("");
                    setReceiptId("");
                    setReceiptNotice("");
                    setPayment(true);
                  }}
                >
                  <Receipt size={17} />
                  {order.totalCents === 0 ? "确认零元结算" : "确认线下收款"}
                </Button>
              )}
          </div>
        }
      />
      <div className="admin-detail-banner">
        <Badge status={order.status} />
        <span>
          打印 {order.files.filter((f) => f.printStatus === "COMPLETED").length}
          /{order.files.length}
        </span>
        <span>
          装袋：{order.packingStatus === "PACKED" ? "已完成" : "待核对"}
        </span>
        {order.note && <b>备注：{order.note}</b>}
      </div>
      {!!order.blockers?.length && (
        <Notice type="warning">
          {order.blockers.map((b) => b.message).join("；")}
          {paymentBlockers.length
            ? "。当前保留锁定金额，解除阻断前不能结算或生产。"
            : "。请在核对收款中复用原实收记录，确认差额后完成结算。"}
        </Notice>
      )}
      <div className="detail-grid">
        <div>
          <Panel
            title="打印文件与任务"
            action={
              <Link className="text-link" to={`${ADMIN}/print?order=${id}`}>
                去打印中心
                <ArrowRight size={15} />
              </Link>
            }
          >
            {order.files.map((f) => (
              <div className="admin-file" key={f.id}>
                <FileRow file={f} action={<Badge status={f.printStatus} />} />
                {f.segments.map((s, i) => (
                  <div className="task-line" key={s.id}>
                    <span className="task-number">{i + 1}</span>
                    <div>
                      <b>
                        第 {s.range} 页 ·{" "}
                        {s.colorMode === "BW" ? "黑白" : "彩色"} ·{" "}
                        {s.sides === "DUPLEX" ? "双面" : "单面"} · {s.copies} 份
                      </b>
                      <small>
                        A4 /{" "}
                        {s.flip === "LONG_EDGE"
                          ? "长边翻转"
                          : s.flip === "SHORT_EDGE"
                            ? "短边翻转"
                            : "无翻转"}{" "}
                        / 内容版本 v{order.contentRevision}
                      </small>
                    </div>
                    <Link
                      className="text-link"
                      to={`${ADMIN}/print/${f.id}?segment=${s.id}`}
                    >
                      查看任务
                      <ChevronRight size={14} />
                    </Link>
                  </div>
                ))}
                <div className="task-binding">
                  装订：
                  {f.bindingType === "NONE"
                    ? "无需装订"
                    : f.bindingType === "CORNER"
                      ? "左上角单钉"
                      : "左侧双钉"}{" "}
                  ·{" "}
                  {f.bindingStatus === "COMPLETED"
                    ? "已完成"
                    : f.bindingType === "NONE"
                      ? "散页整理"
                      : "待生产完成"}
                </div>
              </div>
            ))}
          </Panel>
          <DeliveryProofPanel
            key={`${order.id}:${generation}:${order.proof?.deliveredAt || ""}`}
            proof={order.proof}
            delivered={order.status === "DELIVERED"}
          />
          <Panel title="订单动态与操作记录">
            <div className="history-list">
              {order.history.map((h, i) => (
                <div key={i}>
                  <i />
                  <div>
                    <strong>{h.title}</strong>
                    <span>{formatDisplayTime(h.time)}</span>
                    <p>{h.description}</p>
                  </div>
                </div>
              ))}
            </div>
          </Panel>
          <Panel title="受控操作">
            <Notice>
              纠错操作必须记录原因并重新检查生产依赖，不能直接改订单状态。
            </Notice>
            <div className="button-row">
              <Link className="btn secondary" to={`${ADMIN}/exceptions`}>
                记录生产问题
              </Link>
              <Button variant="secondary" disabled title="D-01 未批准">
                付款后替换内容 · 未批准
              </Button>
              <Button
                variant="secondary"
                disabled={[
                  "DELIVERING",
                  "DELIVERED",
                  "COMPLETED",
                  "CLOSED",
                  "CANCELLED",
                  "REFUNDED",
                ].includes(order.status)}
                title={
                  [
                    "DELIVERING",
                    "DELIVERED",
                    "COMPLETED",
                    "CLOSED",
                    "CANCELLED",
                    "REFUNDED",
                  ].includes(order.status)
                    ? "订单已进入配送或已经结束，不能再作废"
                    : "生产、配送、收款一旦开始就不能作废，只能走交付或售后"
                }
                onClick={() => {
                  setReason("");
                  setVoiding(true);
                }}
              >
                作废订单
              </Button>
              <Button
                variant="danger"
                disabled={
                  !["DELIVERED", "COMPLETED", "CANCELLED"].includes(
                    order.status,
                  )
                }
                title={
                  ["DELIVERED", "COMPLETED", "CANCELLED"].includes(order.status)
                    ? "删除后进入回收站，24 小时内可恢复"
                    : "仅已结束的订单可删除；履约未结束不能通过删除中断"
                }
                onClick={() => {
                  setReason("");
                  setDeleteBlockers([]);
                  setDeleting(true);
                }}
              >
                删除订单
              </Button>
            </div>
          </Panel>
        </div>
        <aside className="order-detail-sidebar">
          <div className="order-detail-column">
            <Panel title="联系与配送">
              <dl className="detail-dl vertical">
                <div>
                  <dt>微信昵称</dt>
                  <dd>{order.nickname}</dd>
                </div>
                {order.customerNote && (
                  <div>
                    <dt>订单备注</dt>
                    <dd className="order-note-text">{order.customerNote}</dd>
                  </div>
                )}
                <div>
                  <dt>手机号</dt>
                  <dd>
                    <a href={`tel:${order.phone}`}>{order.phone}</a>
                  </dd>
                </div>
                <div>
                  <dt>目标楼栋</dt>
                  <dd>
                    <CampusTag
                      campus={campusOfOrder(
                        data.campuses,
                        data.buildings,
                        order,
                      )}
                    />
                    <div>
                      {
                        data.buildings.find((b) => b.id === order.locationId)
                          ?.name
                      }
                    </div>
                  </dd>
                </div>
                <div>
                  <dt>原预约时段</dt>
                  <dd>
                    {slotBlockLabel(requestedSlotOf(order)) ||
                      data.slots.find((s) => s.id === order.slotId)?.name ||
                      "历史预约快照缺失"}
                  </dd>
                </div>
                <div>
                  <dt>确认时段</dt>
                  <dd>
                    {slotBlockLabel(confirmedSlotOf(order)) || "尚未确认"}
                  </dd>
                </div>
                <div>
                  <dt>当前预计安排</dt>
                  <dd>
                    {slotBlockLabel((order as any).arrangement?.effective) ||
                      "待协调"}
                  </dd>
                </div>
                <div>
                  <dt>最新批次</dt>
                  <dd>
                    {(order as any).arrangement?.batch?.name ||
                      data.batches.find((b) => b.id === order.batchId)?.name ||
                      "尚未安排"}
                  </dd>
                </div>
              </dl>
              {order.addressReview && (
                <>
                  <Notice>用户修改了楼栋，必须复核后才可配送。</Notice>
                  <Button
                    onClick={() =>
                      updateOrder(
                        order.id,
                        { addressReview: false },
                        "review-address",
                      )
                    }
                  >
                    确认配送地址已复核
                  </Button>
                </>
              )}
            </Panel>
            <AdminAccessLinks orderId={order.id} />
          </div>
          <div className="order-detail-column">
            <AdminSupplementaryPanel orderId={order.id} />
            {order.status === "PENDING_CONFIRMATION" &&
              (staff?.capabilities?.includes("*") ||
                staff?.capabilities?.includes("payment.manage")) && (
                <PendingSlotProposal
                  slots={data.slots}
                  campusId={orderCampusId(data.buildings || [], order as any)}
                  proposal={(order as any).slotProposal}
                  onSubmit={async (slotId, reason) => {
                    await growthRequest(
                      `/admin/orders/${encodeURIComponent(order.id)}/slot-proposals`,
                      {
                        method: "POST",
                        body: {
                          expectedVersion: order.version,
                          slotId,
                          reason,
                        },
                      },
                    );
                    await refresh();
                    toast("时段建议已提交，等待客户接受后再结算");
                  }}
                />
              )}
            {!!order.deliveryNotices?.length && (
              <DeliveryNoticePanel
                notices={order.deliveryNotices}
                canRetry={
                  !!(
                    staff?.capabilities?.includes("*") ||
                    staff?.capabilities?.includes("delivery.manage") ||
                    staff?.capabilities?.includes("mail.manage")
                  )
                }
                onRetry={async (notice, reason) => {
                  await growthRequest(
                    `/admin/delivery-notices/${encodeURIComponent(notice.id)}/retry`,
                    {
                      method: "POST",
                      body: { expectedVersion: notice.version, reason },
                    },
                  );
                  await refresh();
                  toast("通知已重新检查，发送状态以最新记录为准");
                }}
              />
            )}
            <Panel title="费用快照">
              <PriceRows total={order.totalCents} quote={order.quote} />
              <p className="form-hint">历史订单金额不会随新价目修改。</p>
              {order.settlement && (
                <dl className="detail-dl">
                  <div>
                    <dt>实际收款</dt>
                    <dd className="amount">
                      {money(order.settlement.cashReceivedCents)}
                    </dd>
                  </div>
                  <div>
                    <dt>差额</dt>
                    <dd>
                      <span
                        className={
                          order.settlement.cashReceivedCents > order.totalCents
                            ? "green-text"
                            : order.settlement.cashReceivedCents <
                                order.totalCents
                              ? "red-text"
                              : ""
                        }
                      >
                        {order.settlement.cashReceivedCents > order.totalCents
                          ? "+"
                          : order.settlement.cashReceivedCents <
                              order.totalCents
                            ? "-"
                            : ""}
                        {money(
                          Math.abs(
                            order.settlement.cashReceivedCents -
                              order.totalCents,
                          ),
                        )}
                      </span>
                    </dd>
                  </div>
                  <div>
                    <dt>收款确认时间</dt>
                    <dd>{formatDisplayTime(order.settlement.confirmedAt)}</dd>
                  </div>
                  {order.settlement.note && (
                    <div>
                      <dt>收款备注</dt>
                      <dd>{order.settlement.note}</dd>
                    </div>
                  )}
                </dl>
              )}
            </Panel>
          </div>
        </aside>
      </div>
      {payment && (
        <Modal
          title={`核对收款 · ${order.pickupCode}`}
          onClose={() => setPayment(false)}
        >
          <Notice>
            {order.totalCents === 0
              ? "本单优惠后应付0元，无需转账。请人工核对优惠快照并确认零元结算。"
              : "请在微信中核对实际收款。此操作不会发起任何线上支付。"}
          </Notice>
          <dl className="detail-dl">
            <div>
              <dt>当前内容版本</dt>
              <dd>v{order.contentRevision}</dd>
            </div>
            <div>
              <dt>微信昵称</dt>
              <dd>{order.nickname}</dd>
            </div>
            <div>
              <dt>应收金额</dt>
              <dd className="amount">{money(order.totalCents)}</dd>
            </div>
          </dl>
          {receiptNotice && <Notice>{receiptNotice}</Notice>}
          {actionIssues[`/admin/orders/${id}/confirm-payment`] && (
            <Notice type="error">
              {actionIssues[`/admin/orders/${id}/confirm-payment`]}
            </Notice>
          )}
          {!!order.unmatchedReceipts?.length && (
            <Field label="复用待核对实收（必选，不再登记新款）">
              <select
                value={receiptId}
                onChange={(event) => {
                  setReceiptId(event.target.value);
                  setPaymentDifferenceAccepted(false);
                  const receipt = order.unmatchedReceipts?.find(
                    (item) => item.id === event.target.value,
                  );
                  setReceived(
                    receipt
                      ? (
                          (receipt.refundableCents ??
                            receipt.amountCents -
                              (receipt.refundedCents || 0)) / 100
                        ).toFixed(2)
                      : "",
                  );
                }}
              >
                <option value="">请选择实收记录</option>
                {order.unmatchedReceipts?.map((receipt) => (
                  <option key={receipt.id} value={receipt.id}>
                    {formatDisplayTime(receipt.createdAt)} · 净额{" "}
                    {money(
                      receipt.refundableCents ??
                        receipt.amountCents - (receipt.refundedCents || 0),
                    )}{" "}
                    · {receipt.id.slice(0, 8)}
                  </option>
                ))}
              </select>
            </Field>
          )}
          <Field label="实际收到金额（元）">
            <input
              type="number"
              min="0"
              step="0.01"
              max="21474836.47"
              value={received}
              onChange={(e) => {
                setReceived(e.target.value);
                setPaymentDifferenceAccepted(false);
              }}
            />
          </Field>
          {paymentDifference !== null && paymentDifference !== 0 && (
            <>
              <p role="status">
                差额：
                <span
                  className={paymentDifference > 0 ? "green-text" : "red-text"}
                >
                  {paymentDifference > 0 ? "+" : "-"}
                  {money(Math.abs(paymentDifference))}
                </span>
              </p>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={paymentDifferenceAccepted}
                  onChange={(event) =>
                    setPaymentDifferenceAccepted(event.target.checked)
                  }
                />
                <span>接受差额并结算</span>
              </label>
            </>
          )}
          {order.totalCents === 0 && (
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={zeroConfirmed}
                onChange={(e) => setZeroConfirmed(e.target.checked)}
              />
              <span>
                我已核对本单应付为0，确认按零元结算进入打印；这不属于实付首单。
              </span>
            </label>
          )}
          <Field label="收款备注（选填）">
            <textarea value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>
          <Button
            className="full"
            busy={busy}
            disabled={
              busy ||
              (paymentDifference !== null &&
                paymentDifference !== 0 &&
                !paymentDifferenceAccepted)
            }
            onClick={confirm}
          >
            {busy
              ? "正在确认…"
              : order.totalCents === 0
                ? "已核对，确认零元结算"
                : "已核对，确认实际收款"}
          </Button>
        </Modal>
      )}
      {voiding && (
        <Modal
          title={`作废订单 · ${order.pickupCode}`}
          onClose={() => setVoiding(false)}
        >
          <Notice>
            作废只关闭尚未交付的订单，不删除数据。已收款、已开始生产或已开始配送的订单必须走交付或售后，不能作废。
          </Notice>
          <Field label="作废原因（必填，写入审计记录）">
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="例如：客户重复下单，本人已知悉"
            />
          </Field>
          <Button
            busy={busy}
            disabled={busy || !reason.trim()}
            onClick={async () => {
              setBusy(true);
              const ok = await act(
                `/admin/orders/${order.id}/void`,
                { expectedVersion: order.version, reason: reason.trim() },
                (s) => ({
                  ...s,
                  orders: s.orders.map((o) =>
                    o.id === order.id
                      ? {
                          ...o,
                          status: "CANCELLED",
                          version: o.version + 1,
                          history: [
                            {
                              title: "订单已作废",
                              time: new Date().toLocaleString("zh-CN"),
                              description: reason.trim(),
                            },
                            ...o.history,
                          ],
                        }
                      : o,
                  ),
                }),
              );
              setBusy(false);
              if (ok) {
                setVoiding(false);
                toast("订单已作废");
              }
            }}
          >
            确认作废
          </Button>
        </Modal>
      )}
      {deleting && (
        <Modal
          title={`删除订单 · ${order.pickupCode}`}
          onClose={() => {
            setDeleting(false);
            setDeleteBlockers([]);
          }}
        >
          <Notice>
            订单将移入回收站，24
            小时内可恢复；超过保留期会被永久清理。删除前必须确保履约、售后、退款和工资全部结清。
          </Notice>
          {!!order.blockers?.length && (
            <Notice>{order.blockers.map((b) => b.message).join("；")}</Notice>
          )}
          {!!deleteBlockers.length && (
            <Notice type="error">
              <div>
                删除被以下未结业务拦下：
                {deleteBlockers.map((blocker) => blocker.label).join("；")}
              </div>
              {hasWageBlocker(deleteBlockers) && (
                <div>
                  请先到{" "}
                  <Link className="text-link" to={`${ADMIN}/payroll`}>
                    工资结算
                  </Link>{" "}
                  为这单的配送工资记录发放（若仍待配置，先补录策略），再回来删除。
                </div>
              )}
            </Notice>
          )}
          <Field label="删除原因（必填，写入审计记录）">
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="例如：测试订单，已完成清理核对"
            />
          </Field>
          <Button
            variant="danger"
            busy={busy}
            disabled={busy || !reason.trim()}
            onClick={async () => {
              setBusy(true);
              const ok = await act(
                "/admin/trash",
                {
                  targetType: "ORDER",
                  targetId: order.id,
                  expectedVersion: order.version,
                  reason: reason.trim(),
                },
                (s) => ({
                  ...s,
                  orders: s.orders.filter((o) => o.id !== order.id),
                }),
                "POST",
                undefined,
                (e) => setDeleteBlockers(trashBlockers(e)),
              );
              setBusy(false);
              if (ok) {
                setDeleting(false);
                toast("订单已移入回收站，24 小时内可恢复");
                navigate(`${ADMIN}/orders`);
              }
            }}
          >
            确认删除
          </Button>
        </Modal>
      )}
      {internal && (
        <Modal title="内部备注" onClose={() => setInternal(false)}>
          <Field label="仅员工可见">
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="例如：资料请按原页序整理"
            />
          </Field>
          <Button
            onClick={async () => {
              if (await updateOrder(order.id, { note }, "note")) {
                setInternal(false);
                toast("备注已保存");
              }
            }}
          >
            保存备注
          </Button>
        </Modal>
      )}
    </>
  );
}

type DeliveryNoticeView = NonNullable<Order["deliveryNotices"]>[number];
export function DeliveryNoticePanel({
  notices,
  canRetry,
  onRetry,
}: {
  notices: DeliveryNoticeView[];
  canRetry: boolean;
  onRetry: (notice: DeliveryNoticeView, reason: string) => Promise<void>;
}) {
  const [target, setTarget] = useState<DeliveryNoticeView | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const labels: Record<string, string> = {
    PENDING: "等待发送",
    FAILED: "发送失败",
    UNKNOWN: "发送结果未知，请人工核实",
    ACCEPTED: "SMTP 已接受",
    NO_EMAIL: "无有效邮箱，需人工联系",
    EXPIRED: "发送任务已过期",
  };
  const retry = async () => {
    if (!target || busy) return;
    if (!reason.trim()) {
      setError("请填写重试原因");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await onRetry(target, reason.trim());
      setTarget(null);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel title="配送变更通知">
      <p className="form-hint">
        SMTP 接受表示邮件服务器已接收，实际收件情况需另行核实。
      </p>
      {notices.map((notice) => (
        <div key={notice.id} className="detail-dl vertical">
          <p>
            <strong>{labels[notice.status] || notice.status}</strong> ·{" "}
            {formatDisplayTime(notice.createdAt)}
          </p>
          <p className="muted">
            尝试 {notice.attempts} 次
            {notice.lastError ? ` · 原因：${notice.lastError}` : ""}
          </p>
          {canRetry &&
            ["FAILED", "NO_EMAIL", "EXPIRED"].includes(notice.status) && (
              <Button
                variant="secondary"
                onClick={() => {
                  setTarget(notice);
                  setReason("");
                  setError("");
                }}
              >
                重试通知
              </Button>
            )}
        </div>
      ))}
      {target && (
        <Modal
          title="重试配送通知"
          onClose={() => {
            if (!busy) setTarget(null);
          }}
        >
          <Notice>
            重试会复用原变更通知记录；结果未知的邮件需先人工核实。
          </Notice>
          {error && <Notice type="error">{error}</Notice>}
          <Field label="重试原因">
            <textarea
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </Field>
          <Button busy={busy} onClick={() => void retry()}>
            确认重试
          </Button>
        </Modal>
      )}
    </Panel>
  );
}

export function PendingSlotProposal({
  slots,
  campusId,
  proposal,
  onSubmit,
}: {
  slots: Slot[];
  campusId?: string;
  proposal?: {
    slotId: string;
    reason?: string;
    confirmed?: boolean;
    rejected?: boolean;
    proposed?: any;
  } | null;
  onSubmit: (slotId: string, reason: string) => Promise<void>;
}) {
  const [slotId, setSlotId] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const available = slotsForCampus(slots, campusId).filter((slot) =>
    slotCurrentlySelectable(slot),
  );
  useEffect(() => {
    if (slotId && !available.some((slot) => slot.id === slotId)) setSlotId("");
  }, [slotId, available]);
  const submit = async () => {
    if (busy) return;
    if (
      !available.some(
        (slot) => slot.id === slotId && slotCurrentlySelectable(slot),
      )
    ) {
      setError("请选择当前可预约的新时段");
      return;
    }
    if (!reason.trim()) {
      setError("请填写客户可见的调整原因");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await onSubmit(slotId, reason.trim());
      setSlotId("");
      setReason("");
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel title="协调申请时段">
      <p className="form-hint">
        新建议需由客户接受，接受不占名额。已有待核对实收可在确认后复用，无需再次付款。
      </p>
      {proposal && (
        <Notice>
          {proposal.rejected
            ? "客户已拒绝，需重新协调"
            : proposal.confirmed
              ? "客户已接受，待人工确认结算"
              : "已有新时段建议待客户确认"}
          {proposal.proposed && (
            <span> · {slotBlockLabel(proposal.proposed)}</span>
          )}
          {proposal.reason && <span> · 原因：{proposal.reason}</span>}
        </Notice>
      )}
      {error && <Notice type="error">{error}</Notice>}
      <Field label="建议其他时段">
        <select
          value={slotId}
          onChange={(event) => setSlotId(event.target.value)}
        >
          <option value="">请选择可预约时段</option>
          {available.map((slot) => (
            <option key={slot.id} value={slot.id}>
              {slot.date} · {slot.name} · {slot.start}–{slot.end}
            </option>
          ))}
        </select>
      </Field>
      {!available.length && (
        <Notice>当前暂无可建议的开放时段，请联系调度管理员。</Notice>
      )}
      <Field label="调整原因（客户可见）">
        <textarea
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
      </Field>
      <Button
        busy={busy}
        disabled={!available.length}
        onClick={() => void submit()}
      >
        提出时段建议
      </Button>
    </Panel>
  );
}
