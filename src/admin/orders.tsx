import { formatDisplayTime } from "../lib/display-format";
import { AdminSupplementaryPanel } from "../v1/supplementary";
import { assertQuote, settlementInput } from "../growth/pricing";
import { AdminAccessLinks } from "../growth/order-links";
import { useIdentity } from "../growth/identity";
import { useState } from "react";
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
import { campusOfOrder } from "../lib/campus";
import { CampusFilter, CampusTag } from "./campus-filter";
import {
  hasWageBlocker,
  trashBlockers,
  type TrashBlocker,
} from "../lib/trash-blockers";
import type { Order } from "../lib/types";
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
      (!campus || campusOfOrder(data.campuses, data.buildings, o)?.id === campus) &&
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
                          campus={campusOfOrder(data.campuses, data.buildings, o)}
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
  const { data, act, updateOrder, toast } = useStore();
  const [payment, setPayment] = useState(false);
  const [voiding, setVoiding] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteBlockers, setDeleteBlockers] = useState<TrashBlocker[]>([]);
  const [reason, setReason] = useState("");
  const [received, setReceived] = useState("");
  const [zeroConfirmed, setZeroConfirmed] = useState(false);
  const { staff } = useIdentity();
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
  const confirm = async () => {
    let settlement: ReturnType<typeof settlementInput>;
    try {
      if (!order.quote) throw new Error("缺少完整报价快照，请刷新订单后核对");
      assertQuote(order.quote);
      if (
        order.quote.id !== order.currentQuoteId ||
        order.quote.totalCents !== order.totalCents
      )
        throw new Error("订单报价不一致，请重新加载");
      settlement = settlementInput(order.totalCents, received, zeroConfirmed);
      if (!order.currentQuoteId)
        throw new Error("缺少订单锁定报价，请刷新订单后核对");
      if (order.blockers?.length)
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
            {order.status === "PENDING_CONFIRMATION" && (
              <Button
                disabled={!!order.blockers?.length || !order.currentQuoteId}
                onClick={() => {
                  setZeroConfirmed(false);
                  setReceived("");
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
          。当前保留锁定金额，解除阻断前不能结算或生产。
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
                  <dd>{data.slots.find((s) => s.id === order.slotId)?.name}</dd>
                </div>
                <div>
                  <dt>最新批次</dt>
                  <dd>
                    {data.batches.find((b) => b.id === order.batchId)?.name ||
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
            <Panel title="费用快照">
              <PriceRows total={order.totalCents} quote={order.quote} />
              <p className="form-hint">历史订单金额不会随新价目修改。</p>
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
          <Field label="实际收到金额（元）">
            <input
              type="number"
              min="0"
              step="0.01"
              value={received}
              onChange={(e) => setReceived(e.target.value)}
            />
          </Field>
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
            disabled={busy}
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
