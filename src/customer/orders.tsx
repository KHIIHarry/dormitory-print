import { formatDisplayTime } from "../lib/display-format";
import { CustomerSupplementaryPanel } from "../v1/supplementary";
import { useIdentity } from "../growth/identity";
import { canOrderAction, effectiveOrderAccess } from "../growth/order-security";
import { CustomerSavedLink } from "../growth/order-links";
import { OrderImportButton } from "./account";
import { DocPreview } from "../components/doc-preview";
import type { PrintFile } from "../lib/types";
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
  CalendarDays,
  Check,
  CheckCircle2,
  ChevronRight,
  Clock,
  Copy,
  Download,
  FileText,
  MapPin,
  MessageCircle,
  PackageCheck,
  Phone,
  Plus,
  Printer,
  RefreshCw,
  Search,
  ShieldCheck,
  Truck,
} from "lucide-react";
import { DEMO, useStore } from "../lib/store";
import { money, now } from "../lib/api";
import {
  Badge,
  Button,
  CopyButton,
  Empty,
  ErrorState,
  Field,
  FileRow,
  LinkButton,
  Modal,
  Notice,
  PageHeading,
  Panel,
  PriceRows,
  SectionLink,
} from "../components/ui";
import { RefreshStatus, ResourceSkeleton } from "../components/feedback";
import type { Order } from "../lib/types";
import {
  AfterSalesPanel,
  autoCloseNotice,
  confirmedSlotOf,
  requestedSlotOf,
  slotBlockLabel,
} from "../v1/customer";
const statusFilters = [
  ["全部订单", ""],
  ["待确认", "PENDING_CONFIRMATION"],
  ["待打印", "PENDING_PRINT"],
  ["待配送", "READY_FOR_DELIVERY"],
  ["配送中", "DELIVERING"],
  ["已送达", "DELIVERED"],
];
export function OrderList() {
  const { data, resources, refreshing, refreshError, refresh } = useStore();
  const ordersResource = resources.orders;
  const firstLoading =
    !DEMO && !ordersResource?.hasData && !ordersResource?.error;
  const firstError =
    !DEMO && !ordersResource?.hasData ? ordersResource?.error : "";
  const { customer, orderAccess } = useIdentity();
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get("q") || "");
  const status = params.get("status") || "";
  const orders = data.orders.filter(
    (o) =>
      (!status || o.status === status) &&
      (!search ||
        `${o.pickupCode} ${o.files.map((f) => f.name).join(" ")}`
          .toLowerCase()
          .includes(search.trim().toLowerCase())),
  );
  return (
    <main className="content-page">
      <PageHeading
        title="我的订单"
        description={
          customer.authKind === "REGISTERED"
            ? "显示归属账号、明确关联及通过链接导入的授权订单。"
            : "显示本机游客会话的订单；换设备可使用私下保存的专属链接。"
        }
        action={
          <LinkButton to="/u/new">
            <Plus size={17} />
            新建打印
          </LinkButton>
        }
      />
      <div className="order-list-toolbar">
        <div className="tabs">
          {statusFilters.map(([label, s]) => (
            <button
              key={s}
              className={s === status ? "active" : ""}
              onClick={() => setParams(s ? { status: s } : {})}
            >
              {label}
              {s === "" && (
                <b>
                  {!DEMO && !ordersResource?.hasData ? "—" : data.orders.length}
                </b>
              )}
            </button>
          ))}
        </div>
        <div className="search-input">
          <Search size={17} />
          <input
            placeholder="搜索提取码或文件名"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setParams({ ...Object.fromEntries(params), q: e.target.value });
            }}
          />
        </div>
      </div>
      <RefreshStatus
        loading={refreshing && !firstLoading}
        error={ordersResource?.hasData ? refreshError : undefined}
        retry={refresh}
      />
      <div className="customer-orders">
        {firstLoading ? (
          <ResourceSkeleton variant="cards" />
        ) : firstError ? (
          <ErrorState message={firstError} retry={refresh} />
        ) : orders.length ? (
          orders.map((o) => (
            <Link
              to={`/u/orders/${o.id}`}
              className="customer-order-card"
              key={o.id}
            >
              <div className="order-card-header">
                <div>
                  <span>提取码</span>
                  <strong>{o.pickupCode}</strong>
                </div>
                <Badge status={o.status} />
              </div>
              {o.files.slice(0, 2).map((f) => (
                <FileRow key={f.id} file={f} />
              ))}
              <div className="order-meta">
                <span>
                  <MapPin size={14} />
                  {data.buildings.find((b) => b.id === o.locationId)?.name}
                </span>
                <span>
                  <Clock size={14} />
                  {formatDisplayTime(o.createdAt)}
                </span>
              </div>
              <div className="order-card-footer">
                <span>
                  {o.files.length} 个文件 <i>·</i> 合计{" "}
                  <b>{money(o.totalCents)}</b>
                </span>
                <span>
                  查看订单
                  <ChevronRight size={16} />
                </span>
              </div>
            </Link>
          ))
        ) : (
          <Empty
            title="还没有这样的订单"
            text="试试其他筛选，或开始你的第一份打印。"
            action={
              <LinkButton to="/u/new">
                开始打印
                <ArrowRight size={16} />
              </LinkButton>
            }
          />
        )}
      </div>
      <Notice>
        {orderAccess
          ? "当前链接额外授权一笔订单；这不代表拥有下单游客的其他订单。"
          : "这里只显示当前授权范围内的订单。短提取码与手机号不能公开查询私有文件；链接丢失请联系管理员重新签发。"}
      </Notice>
    </main>
  );
}
export function Contact() {
  const { id } = useParams();
  const {
    data,
    refresh,
    refreshing,
    refreshError,
    toast,
    setDraft,
    setEditingOrderId,
    setCheckoutMemory,
  } = useStore();
  useEffect(() => {
    setDraft([]);
    setEditingOrderId(null);
    setCheckoutMemory({});
  }, []);
  const order = data.orders.find((o) => o.id === id);
  const [qr, setQr] = useState(false);
  if (!order)
    return (
      <main className="content-page">
        <Empty
          title="找不到这份订单"
          text="请使用当前会话或保存的专属链接。"
          action={<LinkButton to="/u/orders">返回订单</LinkButton>}
        />
      </main>
    );
  const text = `提取码 ${order.pickupCode}，${order.totalCents === 0 ? "本单优惠后应付¥0.00，请人工确认零元结算，无需转账" : `本次应付 ${money(order.totalCents)}，请核对线下付款`}，微信昵称 ${order.nickname}`;
  return (
    <main className="contact-page">
      <div className="success-heading">
        <div className="success-art">
          <div className="success-paper">
            <FileText size={47} />
          </div>
          <div className="success-box" />
          <span>
            <Check size={25} />
          </span>
          <i />
          <i />
          <i />
        </div>
        <h1>订单提交成功！</h1>
        <p>
          {order.settlement?.confirmedAt
            ? "结算已确认，请在订单详情中查看最新进度。"
            : order.totalCents === 0
              ? "请联系管理员确认零元结算，确认后安排打印。"
              : "请联系管理员核对线下付款，确认后安排打印。"}
        </p>
      </div>
      <div className="contact-grid">
        <Panel className="contact-order">
          <Badge status={order.status} />
          <p>你的订单提取码</p>
          <div className="pickup-code">
            {order.pickupCode}
            <CopyButton text={order.pickupCode} />
          </div>
          <div className="contact-amount">
            本次应付<strong>{money(order.totalCents)}</strong>
          </div>
          <Notice>
            {order.settlement?.confirmedAt
              ? "本单结算已确认，可通过订单详情查看后续进度。"
              : order.totalCents === 0
                ? "本单无需转账。请发送提取码，请管理员明确确认零元结算后安排打印。"
                : "复制提取码并发送给管理员，完成线下付款后，我们会核对收款并安排打印。"}
          </Notice>
          <div className="contact-text">
            <p>{text}</p>
            <CopyButton text={text} label="复制联系文案" />
          </div>
        </Panel>
        <Panel className="wechat-panel">
          <div className="wechat-title">
            <span>
              <MessageCircle size={24} />
            </span>
            <div>
              <h2>添加管理员微信</h2>
              <p>请使用微信扫描下方二维码</p>
            </div>
          </div>
          {data.config.qrUrl ? (
            <>
              <button className="qr-image" onClick={() => setQr(true)}>
                <img src={data.config.qrUrl} alt="管理员微信二维码" />
              </button>
              <a
                className="btn secondary full"
                href={data.config.qrUrl}
                download
              >
                保存二维码
                <Download size={16} />
              </a>
            </>
          ) : (
            <div className="qr-unconfigured">
              <MessageCircle size={38} />
              <strong>管理员二维码尚未配置</strong>
              <p>
                {DEMO
                  ? "当前为前端演示。正式接单前，需在系统配置中上传真实二维码。"
                  : "请联系管理员获取微信，暂时无法扫码添加。"}
              </p>
            </div>
          )}
          {data.config.wechat && (
            <div className="wechat-id">
              <span>微信号：{data.config.wechat}</span>
              <CopyButton text={data.config.wechat} />
            </div>
          )}
        </Panel>
      </div>
      <CustomerSavedLink orderId={order.id} />
      <div className="contact-actions">
        <LinkButton to={`/u/orders/${id}`}>
          查看订单详情
          <ArrowRight size={17} />
        </LinkButton>
        {order.settlement?.confirmedAt ? (
          <div className="settlement-confirmed" role="status">
            <CheckCircle2 size={18} />
            <span>{order.settlement.settlementKind === "ZERO_PAYABLE" ? "零元结算已确认，正在安排打印" : "收款已确认，正在安排打印"}</span>
          </div>
        ) : (
          <Button variant="secondary" busy={refreshing} onClick={async () => {
            await refresh();
            if (DEMO) toast("演示订单等待管理员确认收款");
          }}>
            <RefreshCw size={16} /> 查看是否已确认
          </Button>
        )}
      </div>
      <RefreshStatus loading={false} error={refreshError} retry={refresh} />
      <p className="save-link-note">
        <ShieldCheck size={15} />
        建议保存本订单专属访问链接。链接包含访问权限，请勿公开分享。
      </p>
      {qr && (
        <Modal title="长按保存二维码" onClose={() => setQr(false)}>
          <img
            className="qr-large"
            src={data.config.qrUrl}
            alt="管理员微信二维码"
          />
        </Modal>
      )}
    </main>
  );
}
export function OrderDetail() {
  const { id } = useParams();
  const {
    data,
    setDraft,
    setEditingOrderId,
    updateOrder,
    toast,
    refresh,
    refreshing,
    refreshError,
    pendingActions,
    actionIssues,
  } = useStore();
  const { orderAccess, customer } = useIdentity();
  const [pdf, setPdf] = useState<PrintFile | null>(null);
  const [edit, setEdit] = useState(false);
  const [cancel, setCancel] = useState(false);
  const [building, setBuilding] = useState("");
  const [reason, setReason] = useState("");
  const nav = useNavigate();
  const order = data.orders.find((o) => o.id === id);
  if (!order)
    return (
      <main className="content-page">
        <Empty
          title="这份订单暂不可访问"
          text="可能链接已失效，或当前会话没有权限。"
          action={<LinkButton to="/u/orders">返回订单</LinkButton>}
        />
      </main>
    );
  const access = effectiveOrderAccess(order, customer, orderAccess);
  const can = (action: string) => canOrderAction(order, action, access);
  const location = data.buildings.find((b) => b.id === order.locationId);
  const slot = data.slots.find((s) => s.id === order.slotId);
  const batch = data.batches.find((b) => b.id === order.batchId);
  const v1Order = order as Order & {
    requestedSlotId?: string | null;
    confirmedSlotId?: string | null;
  };
  const brief = (id?: string | null) => {
    const match = data.slots.find((s) => s.id === id);
    return match
      ? {
          id: match.id,
          name: match.name,
          date: match.date,
          weekday: match.weekday,
          start: match.start,
          end: match.end,
        }
      : null;
  };
  const requestedSlot =
    requestedSlotOf(order) ||
    brief(v1Order.requestedSlotId || order.slotId) ||
    (slot
      ? {
          id: slot.id,
          name: slot.name,
          date: slot.date,
          weekday: slot.weekday,
          start: slot.start,
          end: slot.end,
        }
      : null);
  const confirmedSlot =
    confirmedSlotOf(order) || brief(v1Order.confirmedSlotId);
  const unpaidTimeoutMinutes = (
    data.config as unknown as { unpaidTimeoutMinutes?: number | null }
  ).unpaidTimeoutMinutes;
  const closeNotice = autoCloseNotice(order, unpaidTimeoutMinutes);
  const timeline = ["订单提交", "付款确认", "打印整理", "开始配送", "送达完成"];
  const stage = {
    PENDING_CONFIRMATION: 0,
    PENDING_PRINT: 2,
    READY_FOR_DELIVERY: 2,
    DELIVERING: 3,
    DELIVERED: 4,
    CANCELLED: 0,
    DELIVERY_EXCEPTION: 3,
  }[order.status];
  return (
    <main className="content-page order-detail-page">
      <Link className="back-link" to="/u/orders">
        <ArrowLeft size={16} />
        返回我的订单
      </Link>
      <PageHeading
        title="订单详情"
        description={`下单时间 ${formatDisplayTime(order.createdAt)}`}
        action={
          <Button
            variant="secondary"
            busy={refreshing}
            onClick={() => void refresh()}
          >
            <RefreshCw size={16} />
            刷新状态
          </Button>
        }
      />
      <RefreshStatus loading={false} error={refreshError} retry={refresh} />
      <div className="detail-status">
        <div>
          <Badge status={order.status} />
          <h2>
            {order.status === "PENDING_CONFIRMATION"
              ? "等待管理员确认付款"
              : order.status === "PENDING_PRINT"
                ? "正在认真准备你的资料"
                : order.status === "DELIVERING"
                  ? "你的资料正在路上"
                  : order.status === "DELIVERED"
                    ? "资料已送达，请及时领取"
                    : order.status === "CANCELLED"
                      ? "订单已撤销"
                      : order.status === "DELIVERY_EXCEPTION"
                        ? "配送遇到一点问题"
                        : "资料已准备好，等待配送"}
          </h2>
          <p>
            {order.status === "PENDING_PRINT"
              ? order.packingStatus === "PACKED"
                ? "资料已装袋"
                : "打印、整理和装袋完成后安排配送"
              : batch?.reason || "最新进度会在这里同步，请留意更新。"}
          </p>
          {closeNotice && (
            <p className="v1-deadline" role="status">
              {closeNotice}
            </p>
          )}
        </div>
        <div className="detail-code">
          <span>订单提取码</span>
          <strong>
            {order.pickupCode}
            <CopyButton text={order.pickupCode} />
          </strong>
        </div>
      </div>
      <div className="order-timeline">
        {timeline.map((t, i) => (
          <div className={i <= stage ? "done" : ""} key={t}>
            <span>{i < stage ? <Check size={15} /> : i + 1}</span>
            <b>{t}</b>
          </div>
        ))}
      </div>
      <div className="detail-grid">
        <div>
          <Panel title="打印文件">
            {order.files.map((f) => (
              <div className="detail-file" key={f.id}>
                <FileRow file={f} />
                {f.pdfUrl &&
                  (!access || access.scopes.includes("FINAL_PDF_PREVIEW")) && (
                    <button className="text-button" onClick={() => setPdf(f)}>
                      查看最终 PDF
                    </button>
                  )}
                <div className="detail-file-settings">
                  {f.kind === "PHOTO" ? (
                    <span>5 寸照片 · {f.photo.copies} 张</span>
                  ) : (
                    f.segments.map((s) => (
                      <span key={s.id}>
                        第 {s.range} 页 ·{" "}
                        {s.colorMode === "BW" ? "黑白" : "彩色"} ·{" "}
                        {s.sides === "DUPLEX" ? "双面" : "单面"} · {s.copies} 份
                      </span>
                    ))
                  )}
                </div>
                <div className="production-chips">
                  <span>
                    <Printer size={14} />
                    {f.printStatus === "COMPLETED"
                      ? "打印已完成"
                      : f.printStatus === "ATTEMPTED"
                        ? "等待打印结果确认"
                        : "等待打印"}
                  </span>
                  <span>
                    {f.bindingType === "NONE"
                      ? "无需装订"
                      : f.bindingStatus === "COMPLETED"
                        ? "装订已完成"
                        : "等待装订"}
                  </span>
                  <span>
                    <PackageCheck size={14} />
                    {order.packingStatus === "PACKED" ? "已装袋" : "待装袋"}
                  </span>
                </div>
              </div>
            ))}
          </Panel>
          <Panel title="配送与联系信息">
            <dl className="detail-dl">
              <div>
                <dt>目标楼栋</dt>
                <dd>
                  {location?.name || "尚未设置"}
                  {can("ORDER_CHANGE_LOCATION_PRE_DELIVERY") && (
                    <button
                      className="text-button"
                      onClick={() => {
                        setBuilding(order.locationId);
                        setEdit(true);
                      }}
                    >
                      修改楼栋
                    </button>
                  )}
                </dd>
              </div>
              <div>
                <dt>申请时段</dt>
                <dd>
                  {slotBlockLabel(requestedSlot) || "暂无预约"}
                  <span className="v1-slot-state">申请，未保证名额</span>
                </dd>
              </div>
              <div>
                <dt>确认时段</dt>
                <dd>
                  {confirmedSlot
                    ? slotBlockLabel(confirmedSlot)
                    : "待人工确认收款后占用容量"}
                </dd>
              </div>
              <div>
                <dt>最新配送安排</dt>
                <dd>
                  {order.addressReview
                    ? "楼栋已修改，待管理员复核"
                    : batch
                      ? `${batch.date} ${batch.name} ${batch.time}`
                      : "待管理员安排"}
                </dd>
              </div>
              <div>
                <dt>微信昵称</dt>
                <dd>{order.nickname}</dd>
              </div>
              <div>
                <dt>联系电话</dt>
                <dd>{order.phone}</dd>
              </div>
              {order.customerNote && (
                <div>
                  <dt>订单备注</dt>
                  <dd className="order-note-text">{order.customerNote}</dd>
                </div>
              )}
            </dl>
          </Panel>
          {order.proof &&
            (!access || access.scopes.includes("DELIVERY_PROOF_VIEW")) && (
              <Panel title="交付凭证">
                {order.proof.photoUrl ? (
                  <img
                    className="proof-image"
                    src={order.proof.photoUrl}
                    alt="资料实际交付照片"
                  />
                ) : (
                  <Notice>免拍照说明：{order.proof.skipPhotoReason}</Notice>
                )}
                <dl className="detail-dl">
                  <div>
                    <dt>交付时间</dt>
                    <dd>{formatDisplayTime(order.proof.deliveredAt)}</dd>
                  </div>
                  <div>
                    <dt>实际位置</dt>
                    <dd>
                      {order.proof.position
                        ? `${order.proof.position.latitude.toFixed(6)}, ${order.proof.position.longitude.toFixed(6)}（WGS84，精度 ±${Math.round(order.proof.position.accuracyMeters)} m）`
                        : `定位例外：${order.proof.skipLocationReason}`}
                    </dd>
                  </div>
                  <div>
                    <dt>摆放说明</dt>
                    <dd>{order.proof.placementNote || "无补充说明"}</dd>
                  </div>
                </dl>
              </Panel>
            )}
          <Panel title="订单动态">
            <div className="history-list">
              {order.history.map((h, i) => (
                <div key={i}>
                  <i />
                  <div>
                    <strong>{h.title}</strong>
                    <span>{formatDisplayTime(h.time)}</span>
                    {h.description && <p>{h.description}</p>}
                  </div>
                </div>
              ))}
            </div>
          </Panel>
          <CustomerSupplementaryPanel orderId={order.id} canDispute={order.accessKind === "OWNER" || order.accessKind === "GUEST_OWNER" || order.allowedActions?.includes("ORDER_AFTER_SALES")} />
          <AfterSalesPanel orderId={order.id} order={order} />
        </div>
        <aside>
          <Panel title="订单费用">
            <PriceRows total={order.totalCents} quote={order.quote} />
            {order.status !== "PENDING_CONFIRMATION" && (
              <Notice>
                订单已确认收款或进入履约，原文件、打印参数和应付快照不会修改。如需调整内容，请申请售后核实或重新下单。
              </Notice>
            )}
            {order.status === "PENDING_CONFIRMATION" && (
              <>
                <LinkButton to={`/u/orders/${id}/contact`}>
                  {order.totalCents === 0
                    ? "联系管理员确认零元结算"
                    : "联系管理员付款"}
                  <ArrowRight size={16} />
                </LinkButton>
                {can("ORDER_REVISE_PENDING") && (
                  <Button
                    className="full"
                    variant="secondary"
                    onClick={() => {
                      setDraft(order.files);
                      setEditingOrderId(order.id);
                      toast("已载入现有文件，请修改并重新确认最终预览");
                      nav(`/u/new?revise=${id}`);
                    }}
                  >
                    修改文件与参数
                  </Button>
                )}
                {can("ORDER_CANCEL_PENDING") && (
                  <Button className="full text" onClick={() => setCancel(true)}>
                    撤销订单
                  </Button>
                )}
              </>
            )}
          </Panel>
          {access && (
            <Panel title="本单访问授权">
              <Notice>
                此链接仅包含当前订单权限，所有操作仍受订单状态限制。
              </Notice>
              <OrderImportButton orderId={order.id} />
            </Panel>
          )}
          <CustomerSavedLink orderId={order.id} />
          <Notice>
            如有疑问，请向管理员提供提取码 {order.pickupCode}
            。不要发送订单专属访问链接。
          </Notice>
        </aside>
      </div>
      {edit && (
        <Modal
          title="修改目标楼栋"
          onClose={() =>
            !pendingActions[`/orders/${order.id}/location`] && setEdit(false)
          }
        >
          <Field label="选择新的楼栋">
            <select
              value={building}
              onChange={(e) => setBuilding(e.target.value)}
            >
              {data.buildings
                .filter((b) => b.enabled)
                .map((b) => (
                  <option value={b.id} key={b.id}>
                    {b.name}
                  </option>
                ))}
            </select>
          </Field>
          {order.batchId && (
            <Notice>已分配批次，修改后需要管理员重新复核配送安排。</Notice>
          )}
          {actionIssues[`/orders/${order.id}/location`] && (
            <Notice type="error">
              {actionIssues[`/orders/${order.id}/location`]}
            </Notice>
          )}
          <Button
            busy={!!pendingActions[`/orders/${order.id}/location`]}
            onClick={async () => {
              if (
                await updateOrder(
                  order.id,
                  { locationId: building, addressReview: !!order.batchId },
                  "location",
                )
              ) {
                setEdit(false);
                toast("楼栋已修改");
              }
            }}
          >
            保存楼栋
          </Button>
        </Modal>
      )}
      {cancel && (
        <Modal
          title={`撤销订单 ${order.pickupCode}`}
          onClose={() =>
            !pendingActions[`/orders/${order.id}/cancel`] && setCancel(false)
          }
        >
          <p>撤销后不会继续打印。需要打印时可以重新下单。</p>
          <Field label="撤销原因（选填）">
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="告诉我们撤销的原因"
            />
          </Field>
          {actionIssues[`/orders/${order.id}/cancel`] && (
            <Notice type="error">
              {actionIssues[`/orders/${order.id}/cancel`]}
            </Notice>
          )}
          <Button
            variant="danger"
            busy={!!pendingActions[`/orders/${order.id}/cancel`]}
            onClick={async () => {
              if (
                await updateOrder(
                  order.id,
                  { status: "CANCELLED", note: reason },
                  "cancel",
                )
              ) {
                setCancel(false);
                toast("订单已撤销");
              }
            }}
          >
            确认撤销订单
          </Button>
        </Modal>
      )}
      {pdf && (
        <Modal title="订单最终 PDF" wide onClose={() => setPdf(null)}>
          <DocPreview file={pdf} pdfUrl={pdf.pdfUrl} />
        </Modal>
      )}
    </main>
  );
}
