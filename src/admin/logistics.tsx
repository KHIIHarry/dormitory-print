import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  CalendarDays,
  Check,
  ChevronRight,
  Clock,
  MapPin,
  Plus,
  Search,
  Send,
  Truck,
  Users,
} from "lucide-react";
import { ADMIN, DEMO, useStore } from "../lib/store";
import { mutation } from "../lib/api";
import {
  hasWageBlocker,
  trashBlockers,
  type TrashBlocker,
} from "../lib/trash-blockers";
import { growthRequest } from "../growth/client";
import { batchPrimaryAction, canStartBatch } from "../lib/domain";
import type { Batch, DataState, Slot } from "../lib/types";
import {
  eligibleCouriers,
  slotProposalPayload,
  type AdminOrderView,
  type CourierDirectoryRow,
} from "../v1/admin-api";
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
type CourierCatalogResult = {
  items?: CourierDirectoryRow[];
  staff?: CourierDirectoryRow[];
};

/**
 * Dispatch only needs the minimal delivery-specific catalog.  Reading the
 * private `/admin/staff` directory here both required staff.manage and leaked
 * internal employee names; the public catalog resolves the customer nickname
 * instead.
 */
const COURIER_CATALOG_PATH = "/admin/couriers";

function useCourierCatalog() {
  const [rows, setRows] = useState<CourierDirectoryRow[]>([]);
  const [loading, setLoading] = useState(!DEMO);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    if (DEMO) {
      setRows([]);
      setError("");
      setLoading(false);
      return () => {
        active = false;
      };
    }
    setLoading(true);
    setError("");
    growthRequest<CourierCatalogResult>(COURIER_CATALOG_PATH)
      .then((result) => {
        if (active) setRows(result.items || result.staff || []);
      })
      .catch((cause) => {
        if (!active) return;
        const status = (cause as { status?: number }).status;
        setError(
          status === 403
            ? "当前账号无权读取配送员目录，请联系权限管理员。"
            : (cause as { message?: string }).message ||
                "配送员目录加载失败，请重试。",
        );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);
  return { options: eligibleCouriers(rows), loading, error };
}

export function Batches() {
  const { data } = useStore();
  const [create, setCreate] = useState(false);
  const [status, setStatus] = useState("");
  return (
    <>
      <PageHeading
        title="配送调度"
        description="先规划，再核对；资料全部装袋后，才开始配送。"
        action={
          <Button onClick={() => setCreate(true)}>
            <Plus size={17} />
            新建配送批次
          </Button>
        }
      />
      <div className="production-stats">
        {[
          ["今日批次", data.batches.length],
          ["规划中", data.batches.filter((b) => b.status === "PLANNED").length],
          [
            "已延期",
            data.batches.filter((b) => b.status === "POSTPONED").length,
          ],
          [
            "配送中",
            data.batches.filter((b) => b.status === "DELIVERING").length,
          ],
          [
            "已完成",
            data.batches.filter((b) => b.status === "COMPLETED").length,
          ],
        ].map(([l, n]) => (
          <div key={l}>
            <span>{l}</span>
            <strong>{n}</strong>
          </div>
        ))}
      </div>
      <div className="tabs">
        {[
          ["", "全部批次"],
          ["PLANNED", "规划中"],
          ["PUBLISHED", "已发布"],
          ["POSTPONED", "已延期"],
          ["DELIVERING", "配送中"],
          ["COMPLETED", "已完成"],
          ["CANCELLED", "已取消"],
        ].map(([v, l]) => (
          <button
            key={v}
            className={status === v ? "active" : ""}
            onClick={() => setStatus(v)}
          >
            {l}
          </button>
        ))}
      </div>
      <div className="batch-grid">
        {data.batches
          .filter((b) => !status || b.status === status)
          .map((b) => {
            const ready = b.orderIds.filter(
              (id) =>
                data.orders.find((o) => o.id === id)?.packingStatus ===
                "PACKED",
            ).length;
            return (
              <Link
                className="panel batch-card"
                to={`${ADMIN}/batches/${b.id}`}
                key={b.id}
              >
                <div className="batch-card-top">
                  <span className="batch-icon">
                    <Truck size={24} />
                  </span>
                  <Badge status={b.status} />
                </div>
                <h2>{b.name}</h2>
                <p>
                  <CalendarDays size={15} />
                  {b.date} <span>{b.time}</span>
                </p>
                <p>
                  <Users size={15} />
                  {(b as Batch & { courierName?: string }).courierName ||
                    data.staff.find((s) => s.id === b.courierId)?.name ||
                    "尚未分配配送员"}
                </p>
                <div className="batch-count">
                  <div>
                    <strong>{b.orderIds.length}</strong>
                    <span>规划订单</span>
                  </div>
                  <div>
                    <strong>{ready}</strong>
                    <span>可实际配送</span>
                  </div>
                  <ArrowRight size={19} />
                </div>
              </Link>
            );
          })}
      </div>
      {!data.batches.length && (
        <Panel>
          <Empty
            title="还没有配送批次"
            text="选择订单与配送时间，创建第一个配送计划。"
          />
        </Panel>
      )}
      {create && <BatchForm onClose={() => setCreate(false)} />}
    </>
  );
}
function BatchForm({ onClose, batch }: { onClose: () => void; batch?: Batch }) {
  const { data, act, toast, pendingActions } = useStore();
  const catalog = useCourierCatalog();
  const courierOptions = DEMO
    ? data.staff.filter((s) => s.enabled && s.role === "COURIER")
    : catalog.options;
  const [name, setName] = useState(batch?.name || "");
  const [slotId, setSlotId] = useState("");
  const [availableSlots, setAvailableSlots] = useState<Slot[]>(
    DEMO ? data.slots : [],
  );
  const [slotsError, setSlotsError] = useState("");
  useEffect(() => {
    if (DEMO || batch) return;
    const controller = new AbortController();
    growthRequest<{ items: Slot[] }>("/admin/slots", {
      signal: controller.signal,
    })
      .then((result) => setAvailableSlots(result.items || []))
      .catch((error) => {
        if (!controller.signal.aborted) setSlotsError((error as Error).message);
      });
    return () => controller.abort();
  }, [batch]);
  const [date, setDate] = useState(batch?.date || "");
  const [time, setTime] = useState(batch?.time || "");
  const [courier, setCourier] = useState(batch?.courierId || "");
  const [reason, setReason] = useState("");
  return (
    <Modal title={batch ? "编辑配送批次" : "新建配送批次"} onClose={onClose}>
      <Field label="批次名称">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="例如 晚间配送 · 东区"
        />
      </Field>
      {!batch && (
        <Field
          label="配送时段"
          hint="选择已配置的预计配送时段；配送可根据实际情况提前开始。"
        >
          <select
            value={slotId}
            onChange={(event) => {
              const selected = availableSlots.find(
                (item) => item.id === event.target.value,
              );
              setSlotId(event.target.value);
              setDate(selected?.date || "");
              setTime(selected ? `${selected.start} – ${selected.end}` : "");
            }}
          >
            <option value="">请选择配送时段</option>
            {availableSlots
              .filter((item) => item.enabled)
              .map((item) => (
                <option key={item.id} value={item.id}>
                  {item.date} · {item.name} · {item.start}–{item.end}
                </option>
              ))}
          </select>
        </Field>
      )}
      {!batch && slotsError && (
        <Notice type="error">配送时段加载失败：{slotsError}</Notice>
      )}
      {batch && (
        <div className="form-grid">
          <Field label="配送日期">
            <input
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
            />
          </Field>
          <Field label="配送时间">
            <input
              value={time}
              onChange={(e) => setTime(e.target.value)}
              placeholder="例如 19:00 – 20:00"
            />
          </Field>
        </div>
      )}
      <Field label="配送员">
        <select
          value={courier}
          onChange={(e) => setCourier(e.target.value)}
          disabled={catalog.loading}
        >
          <option value="">
            {catalog.loading ? "正在加载配送员..." : "请选择配送员"}
          </option>
          {courierOptions.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}（{s.role === "ADMIN" ? "管理员" : "配送员"}）
            </option>
          ))}
        </select>
      </Field>
      {!DEMO && catalog.error && <Notice type="error">{catalog.error}</Notice>}
      {!DEMO &&
        !catalog.loading &&
        !catalog.error &&
        !catalog.options.length && (
          <Notice>
            没有可指派的配送员：需为启用、正常状态且未强制改密的配送员，
            或具备配送权限的管理员。
          </Notice>
        )}
      {batch && (
        <Field label="修改原因">
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </Field>
      )}
      <Button
        className="full"
        busy={
          pendingActions[
            batch ? `/admin/batches/${batch.id}` : "/admin/batches"
          ]
        }
        onClick={async () => {
          if (
            !name.trim() ||
            (!batch && !slotId) ||
            !date ||
            !time ||
            !courier ||
            (batch && !reason.trim())
          ) {
            toast("请完整填写批次信息和修改原因");
            return;
          }
          const id = batch?.id || crypto.randomUUID();
          if (
            await act(
              batch ? `/admin/batches/${id}` : "/admin/batches",
              {
                name,
                ...(batch
                  ? date !== batch.date || time !== batch.time
                    ? { date, time }
                    : {}
                  : { slotId }),
                courierId: courier,
                expectedVersion: batch?.version,
                reason,
              },
              (s) => ({
                ...s,
                batches: batch
                  ? s.batches.map((b) =>
                      b.id === id
                        ? {
                            ...b,
                            name,
                            date,
                            time,
                            courierId: courier,
                            version: b.version + 1,
                          }
                        : b,
                    )
                  : [
                      ...s.batches,
                      {
                        id,
                        name,
                        date,
                        time,
                        courierId: courier,
                        status: "PLANNED",
                        orderIds: [],
                        version: 1,
                      },
                    ],
              }),
              batch ? "PATCH" : "POST",
            )
          ) {
            toast(batch ? "批次信息已更新" : "配送批次已创建");
            onClose();
          }
        }}
      >
        {batch ? "保存批次信息" : "创建批次"}
      </Button>
    </Modal>
  );
}
export function BatchDetail() {
  const { id } = useParams();
  const { data, act, toast, pendingActions } = useStore();
  const batch = data.batches.find((b) => b.id === id);
  const [edit, setEdit] = useState(false);
  const [mode, setMode] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [target, setTarget] = useState("");
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");
  const [reason, setReason] = useState("");
  const [preview, setPreview] = useState<{
    id: string;
    blockers: string[];
  } | null>(null);
  const [commitBlockers, setCommitBlockers] = useState<TrashBlocker[]>([]);
  if (!batch) return <Empty title="配送批次不存在" />;
  const orders = data.orders.filter((o) => batch.orderIds.includes(o.id));
  const canStart = canStartBatch(orders);
  const candidates =
    mode === "ADD"
      ? data.orders.filter(
          (o) =>
            !o.batchId &&
            !["CANCELLED", "DELIVERED", "DELIVERING"].includes(o.status),
        )
      : orders;
  const labels: Record<string, string> = {
    ADD: "添加订单",
    REMOVE: "移出订单",
    TRANSFER: "转移订单",
    SPLIT: "拆分批次",
    MERGE: "合并批次",
    RESCHEDULE: "调整配送时间",
    CANCEL: "取消本次配送",
    DELETE: "删除批次",
    PUBLISH: "发布配送计划",
    START: "开始配送",
  };
  const begin = (m: string) => {
    setMode(m);
    setSelected(m === "MERGE" ? batch.orderIds : []);
    setPreview(null);
    setCommitBlockers([]);
    setReason("");
    setTarget("");
  };
  const getPreview = async () => {
    if (
      ["ADD", "REMOVE", "TRANSFER", "SPLIT"].includes(mode) &&
      !selected.length
    ) {
      toast("请先选择订单");
      return;
    }
    if (["TRANSFER", "MERGE"].includes(mode) && !target) {
      toast("请选择目标批次");
      return;
    }
    if (
      ["RESCHEDULE", "CANCEL", "DELETE", "TRANSFER", "SPLIT", "MERGE"].includes(
        mode,
      ) &&
      !reason.trim()
    ) {
      toast("请填写操作原因");
      return;
    }
    if (mode === "RESCHEDULE" && (!date || !time)) {
      toast("请填写新的日期与时间");
      return;
    }
    let blockers: string[] = [];
    if (mode === "START" && !canStart)
      blockers = orders
        .filter((o) => o.packingStatus !== "PACKED" || o.addressReview)
        .map(
          (o) =>
            `${o.pickupCode}：${o.packingStatus !== "PACKED" ? "尚未完成装袋" : "地址尚待复核"}`,
        );
    if (mode === "DELETE" && batch.status === "DELIVERING")
      blockers.push("配送中批次不能直接删除，请先处理实际配送执行");
    if (!DEMO) {
      try {
        const plan = await mutation<{
          planId: string;
          blockers: { message: string }[];
        }>("/admin/batch-operations/preview", {
          action: mode,
          orderIds: selected,
          sourceBatchIds: [id],
          targetBatchId: target || undefined,
          versions: { [id!]: batch.version },
          payload: { date, time, reason },
        });
        setPreview({
          id: plan.planId,
          blockers: plan.blockers.map((b) => b.message),
        });
        return;
      } catch (e) {
        toast((e as Error).message);
        return;
      }
    }
    setPreview({ id: crypto.randomUUID(), blockers });
  };
  const apply = (s: DataState): DataState => {
    let batches = s.batches.map((b) => ({ ...b, orderIds: [...b.orderIds] }));
    let nextOrders = s.orders.map((o) => ({ ...o }));
    const source = batches.find((b) => b.id === id)!;
    if (mode === "ADD") {
      source.orderIds.push(...selected);
      nextOrders = nextOrders.map((o) =>
        selected.includes(o.id) ? { ...o, batchId: id } : o,
      );
    }
    if (["REMOVE", "TRANSFER", "SPLIT"].includes(mode)) {
      source.orderIds = source.orderIds.filter((x) => !selected.includes(x));
      if (mode === "REMOVE")
        nextOrders = nextOrders.map((o) =>
          selected.includes(o.id) ? { ...o, batchId: undefined } : o,
        );
      else {
        const targetId = mode === "SPLIT" ? crypto.randomUUID() : target;
        if (mode === "SPLIT")
          batches.push({
            ...source,
            id: targetId,
            name: `${source.name} · 拆分`,
            orderIds: selected,
            version: 1,
          });
        else
          batches = batches.map((b) =>
            b.id === targetId
              ? { ...b, orderIds: [...b.orderIds, ...selected] }
              : b,
          );
        nextOrders = nextOrders.map((o) =>
          selected.includes(o.id) ? { ...o, batchId: targetId } : o,
        );
      }
    }
    if (mode === "MERGE") {
      batches = batches.map((b) =>
        b.id === target
          ? { ...b, orderIds: [...b.orderIds, ...source.orderIds] }
          : b,
      );
      nextOrders = nextOrders.map((o) =>
        source.orderIds.includes(o.id) ? { ...o, batchId: target } : o,
      );
      source.orderIds = [];
      source.status = "CANCELLED";
    }
    if (mode === "RESCHEDULE") {
      source.date = date;
      source.time = time;
      source.reason = reason;
      source.status = "POSTPONED";
    }
    if (mode === "CANCEL") {
      source.status = "CANCELLED";
      source.reason = reason;
    }
    if (mode === "PUBLISH") source.status = "PUBLISHED";
    if (mode === "START") {
      source.status = "DELIVERING";
      nextOrders = nextOrders.map((o) =>
        source.orderIds.includes(o.id) ? { ...o, status: "DELIVERING" } : o,
      );
    }
    if (mode === "DELETE") {
      batches = batches.filter((b) => b.id !== id);
      nextOrders = nextOrders.map((o) =>
        o.batchId === id ? { ...o, batchId: undefined } : o,
      );
      return {
        ...s,
        batches,
        orders: nextOrders,
        trash: [
          ...s.trash,
          {
            id: source.id,
            batchSnapshot: { ...source },
            name: source.name,
            type: "配送批次",
            deletedAt: new Date().toISOString(),
            reason,
            by: "管理员",
          },
        ],
      };
    }
    return { ...s, batches, orders: nextOrders };
  };
  return (
    <>
      <Link className="back-link" to={`${ADMIN}/batches`}>
        <ArrowLeft size={16} />
        返回配送调度
      </Link>
      <PageHeading
        title={batch.name}
        description={`${batch.date} · ${batch.time} · 配送员 ${
          (batch as Record<string, any>).courierName ||
          data.staff.find((s) => s.id === batch.courierId)?.name ||
          "待指派"
        }`}
        action={
          <div className="button-row">
            <Button variant="secondary" onClick={() => setEdit(true)}>
              编辑批次 / 指派配送员
            </Button>
            {batchPrimaryAction(batch.status) && (
              <Button
                onClick={() => {
                  const action = batchPrimaryAction(batch.status);
                  if (action) begin(action);
                }}
              >
                <Send size={16} />
                {batchPrimaryAction(batch.status) === "PUBLISH"
                  ? "发布计划"
                  : "开始配送"}
              </Button>
            )}
          </div>
        }
      />
      <div className="batch-detail-summary">
        <Badge status={batch.status} />
        <span>
          规划订单 <b>{orders.length}</b>
        </span>
        <span>
          可配送订单{" "}
          <b>
            {
              orders.filter(
                (o) => o.packingStatus === "PACKED" && !o.addressReview,
              ).length
            }
          </b>
        </span>
      </div>
      {!canStart && (
        <Notice>
          批次允许提前规划，但有订单未装袋或地址待复核。开始配送会整批检查，不会静默出发一部分。
        </Notice>
      )}
      <Panel
        title="批次订单"
        action={
          <Button
            variant="secondary"
            disabled={["DELIVERING", "COMPLETED", "CANCELLED"].includes(
              batch.status,
            )}
            onClick={() => begin("ADD")}
          >
            <Plus size={15} />
            添加订单
          </Button>
        }
      >
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>提取码</th>
                <th>同学 / 电话</th>
                <th>目标楼栋</th>
                <th>申请 / 确认时段</th>
                <th>生产状态</th>
                <th>地址复核</th>
                <th>配送执行</th>
                <th>订单状态</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.id}>
                  <td>
                    <Link className="table-code" to={`${ADMIN}/orders/${o.id}`}>
                      {o.pickupCode}
                    </Link>
                  </td>
                  <td>
                    {o.nickname}
                    <small>{o.phone}</small>
                  </td>
                  <td>
                    {data.buildings.find((b) => b.id === o.locationId)?.name}
                  </td>
                  <td>
                    {(() => {
                      const requested =
                        (o as Record<string, any>).requestedSlotSnapshot ||
                        data.slots.find((slot) => slot.id === o.slotId);
                      const confirmed =
                        (o as Record<string, any>).confirmedSlotSnapshot ||
                        data.slots.find(
                          (slot) =>
                            slot.id ===
                            (o as Record<string, any>).confirmedSlotId,
                        );
                      return (
                        <>
                          {requested?.name || "未记录申请"}
                          <small>
                            {confirmed
                              ? `已确认：${confirmed.name}`
                              : "未确认名额"}
                          </small>
                        </>
                      );
                    })()}
                  </td>
                  <td>
                    <Badge
                      status={
                        o.packingStatus === "PACKED" ? "PACKED" : "BLOCKED"
                      }
                    />
                  </td>
                  <td>
                    {o.addressReview ? (
                      <span className="red-text">待复核</span>
                    ) : (
                      "无需复核"
                    )}
                  </td>
                  <td>
                    {(batch as Record<string, any>).assignmentIds?.[
                      batch.orderIds.indexOf(o.id)
                    ] ? (
                      <span className="numeric" title="真实配送执行 ID">
                        {String(
                          (batch as Record<string, any>).assignmentIds[
                            batch.orderIds.indexOf(o.id)
                          ],
                        ).slice(0, 8)}
                      </span>
                    ) : (
                      "待分配"
                    )}
                  </td>
                  <td>
                    <Badge status={o.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!orders.length && (
          <Empty
            title="批次还没有订单"
            text="添加订单后，可以规划配送与检查生产进度。"
          />
        )}
      </Panel>
      <Panel title="批次操作">
        <div className="batch-actions">
          {[
            "REMOVE",
            "TRANSFER",
            "SPLIT",
            "MERGE",
            "RESCHEDULE",
            "CANCEL",
            "DELETE",
          ].map((m) => (
            <Button
              key={m}
              variant={m === "DELETE" ? "danger-outline" : "secondary"}
              disabled={
                m === "DELETE"
                  ? batch.status === "DELIVERING"
                  : ["DELIVERING", "COMPLETED"].includes(batch.status)
              }
              title={
                m === "DELETE" && batch.status === "DELIVERING"
                  ? "配送中批次不能删除，请先处理实际配送执行"
                  : undefined
              }
              onClick={() => begin(m)}
            >
              {labels[m]}
            </Button>
          ))}
        </div>
        <p className="form-hint">
          批量操作先预览影响，再确认执行。改期或取消会同步显示到用户订单中。
        </p>
      </Panel>
      {edit && <BatchForm batch={batch} onClose={() => setEdit(false)} />}{" "}
      {mode && (
        <Modal
          title={preview ? `确认影响 · ${labels[mode]}` : labels[mode]}
          onClose={() => {
            setMode("");
            setCommitBlockers([]);
          }}
          wide
        >
          {preview ? (
            <>
              <Notice>
                {labels[mode]}将影响 {selected.length || batch.orderIds.length}{" "}
                个订单。{reason && `原因：${reason}`}
              </Notice>
              {preview.blockers.length ? (
                <div className="blocker-list">
                  {preview.blockers.map((b) => (
                    <p key={b}>{b}</p>
                  ))}
                </div>
              ) : (
                <div className="impact-list">
                  {(selected.length ? selected : batch.orderIds).map((oid) => (
                    <span key={oid}>
                      {data.orders.find((o) => o.id === oid)?.pickupCode}
                    </span>
                  ))}
                  {mode === "RESCHEDULE" && (
                    <p>
                      新安排：{date} {time}
                    </p>
                  )}
                </div>
              )}
              {!!commitBlockers.length && (
                <Notice type="error">
                  <div>
                    操作被以下未结业务拦下：
                    {commitBlockers.map((blocker) => blocker.label).join("；")}
                  </div>
                  {hasWageBlocker(commitBlockers) && (
                    <div>
                      请先到{" "}
                      <Link className="text-link" to={`${ADMIN}/payroll`}>
                        工资结算
                      </Link>{" "}
                      为批次内的配送工资记录发放（若仍待配置，先补录策略），再回来删除。
                    </div>
                  )}
                </Notice>
              )}
              <div className="button-row">
                <Button variant="secondary" onClick={() => setPreview(null)}>
                  返回调整
                </Button>
                <Button
                  busy={
                    pendingActions[
                      mode === "START"
                        ? `/admin/batches/${id}/start`
                        : mode === "PUBLISH"
                          ? `/admin/batches/${id}/publish`
                          : "/admin/batch-operations/commit"
                    ]
                  }
                  disabled={!!preview.blockers.length}
                  onClick={async () => {
                    const path =
                      mode === "START"
                        ? `/admin/batches/${id}/start`
                        : mode === "PUBLISH"
                          ? `/admin/batches/${id}/publish`
                          : "/admin/batch-operations/commit";
                    if (
                      await act(
                        path,
                        mode === "START" || mode === "PUBLISH"
                          ? { expectedVersion: batch.version }
                          : { planId: preview.id },
                        apply,
                        "POST",
                        undefined,
                        (e) => setCommitBlockers(trashBlockers(e)),
                      )
                    ) {
                      setMode("");
                      setCommitBlockers([]);
                      toast("批次操作已完成");
                    }
                  }}
                >
                  确认执行{labels[mode]}
                </Button>
              </div>
            </>
          ) : (
            <>
              {["ADD", "REMOVE", "TRANSFER", "SPLIT"].includes(mode) && (
                <div className="select-orders">
                  {candidates.map((o) => (
                    <label className="checkbox-label" key={o.id}>
                      <input
                        type="checkbox"
                        checked={selected.includes(o.id)}
                        onChange={(e) =>
                          setSelected((s) =>
                            e.target.checked
                              ? [...s, o.id]
                              : s.filter((x) => x !== o.id),
                          )
                        }
                      />
                      <b>{o.pickupCode}</b>
                      <span>
                        {o.nickname} ·{" "}
                        {
                          data.buildings.find((b) => b.id === o.locationId)
                            ?.name
                        }
                      </span>
                      <Badge
                        status={
                          o.packingStatus === "PACKED" ? "PACKED" : "BLOCKED"
                        }
                      />
                    </label>
                  ))}
                  {!candidates.length && <Empty title="没有可选择的订单" />}
                </div>
              )}
              {["TRANSFER", "MERGE"].includes(mode) && (
                <Field label="目标批次">
                  <select
                    value={target}
                    onChange={(e) => setTarget(e.target.value)}
                  >
                    <option value="">请选择目标批次</option>
                    {data.batches
                      .filter(
                        (b) =>
                          b.id !== id &&
                          !["DELIVERING", "COMPLETED", "CANCELLED"].includes(
                            b.status,
                          ),
                      )
                      .map((b) => (
                        <option key={b.id} value={b.id}>
                          {b.name}
                        </option>
                      ))}
                  </select>
                </Field>
              )}
              {mode === "RESCHEDULE" && (
                <div className="form-grid">
                  <Field label="新日期">
                    <input
                      type="date"
                      value={date}
                      onChange={(e) => setDate(e.target.value)}
                    />
                  </Field>
                  <Field label="新时段">
                    <input
                      value={time}
                      onChange={(e) => setTime(e.target.value)}
                      placeholder="例如 19:00 – 20:00"
                    />
                  </Field>
                </div>
              )}
              {!["ADD", "PUBLISH", "START"].includes(mode) && (
                <Field label="操作原因（必填）">
                  <textarea
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    placeholder="说明影响与原因，会保留操作记录"
                  />
                </Field>
              )}
              {mode === "START" && (
                <Notice>开始配送前会重新核对全部订单的装袋与地址状态。</Notice>
              )}
              <Button onClick={getPreview}>
                预览操作影响
                <ArrowRight size={16} />
              </Button>
            </>
          )}
        </Modal>
      )}
    </>
  );
}
export function Slots() {
  const { data, act, toast, pendingActions } = useStore();
  const [edit, setEdit] = useState<Partial<Slot> | null>(null);
  const [slots, setSlots] = useState<Slot[]>(data.slots);
  const [slotError, setSlotError] = useState("");
  const [removeSlot, setRemoveSlot] = useState<Slot | null>(null);
  const [removeReason, setRemoveReason] = useState("");
  useEffect(() => setSlots(data.slots), [data.slots]);
  useEffect(() => {
    if (DEMO) return;
    let active = true;
    growthRequest<{ items: Slot[] }>("/admin/slots")
      .then((result) => {
        if (active) setSlots(result.items || []);
      })
      .catch((error) => {
        if (active) setSlotError((error as Error).message);
      });
    return () => {
      active = false;
    };
  }, []);
  const save = async () => {
    if (!edit?.date || !edit.name?.trim() || !edit.start || !edit.end) {
      toast("请完整填写日期、名称与开始结束时间");
      return;
    }
    if (edit.start >= edit.end) {
      toast("结束时间必须晚于开始时间");
      return;
    }
    const id = edit.id || crypto.randomUUID();
    const slot = {
      ...edit,
      id,
      weekday: new Intl.DateTimeFormat("zh-CN", {
        weekday: "long",
        timeZone: "Asia/Shanghai",
      }).format(new Date(edit.date + "T12:00:00+08:00")),
      enabled: edit.enabled ?? true,
      capacity: edit.capacity || 30,
    } as Slot;
    if (
      await act(
        edit.id ? `/admin/slots/${id}` : "/admin/slots",
        {
          ...slot,
          ...(edit.id
            ? { expectedVersion: (edit as Record<string, any>).version }
            : {}),
        },
        (s) => ({
          ...s,
          slots: edit.id
            ? s.slots.map((x) => (x.id === id ? slot : x))
            : [...s.slots, slot],
        }),
        edit.id ? "PATCH" : "POST",
      )
    ) {
      setEdit(null);
      toast("配送时段已保存");
      if (!DEMO) {
        const result = await growthRequest<{ items: Slot[] }>("/admin/slots");
        setSlots(result.items || []);
      }
    }
  };
  return (
    <>
      <PageHeading
        title="配送时段"
        description="按具体日期开放预约，不会自动生成虚构的可选时段。"
        action={
          <Button onClick={() => setEdit({ enabled: true, capacity: 30 })}>
            <Plus size={17} />
            新建时段
          </Button>
        }
      />
      <Notice>关闭时段将停止新预约，不会删除已有订单的预约记录。</Notice>
      {slotError && (
        <Notice type="error">
          <span role="alert">{slotError}</span>
        </Notice>
      )}
      <Panel>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>日期</th>
                <th>时段名称</th>
                <th>配送时间</th>
                <th>容量 / 已占用</th>
                <th>开放状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {slots.map((s) => (
                <tr key={s.id}>
                  <td>
                    {s.date}
                    <small>{s.weekday}</small>
                  </td>
                  <td>{s.name}</td>
                  <td className="numeric">
                    {s.start} – {s.end}
                  </td>
                  <td>
                    {s.capacity} 单
                    <small>
                      已占 {(s as Record<string, any>).occupied ?? 0} · 剩余{" "}
                      {(s as Record<string, any>).remaining ?? s.capacity}
                      {(s as Record<string, any>).selectable === false &&
                        " · 不可选"}
                    </small>
                  </td>
                  <td>
                    <button
                      className={`toggle ${s.enabled ? "on" : ""}`}
                      aria-label={`${s.enabled ? "关闭" : "开放"}${s.name}`}
                      onClick={async () => {
                        const ok = await act(
                          `/admin/slots/${s.id}`,
                          {
                            enabled: !s.enabled,
                            expectedVersion: (s as Record<string, any>).version,
                          },
                          (d) => ({
                            ...d,
                            slots: d.slots.map((x) =>
                              x.id === s.id ? { ...x, enabled: !x.enabled } : x,
                            ),
                          }),
                          "PATCH",
                        );
                        if (!ok) return;
                        if (DEMO)
                          setSlots((current) =>
                            current.map((x) =>
                              x.id === s.id ? { ...x, enabled: !x.enabled } : x,
                            ),
                          );
                        else {
                          const result = await growthRequest<{ items: Slot[] }>(
                            "/admin/slots",
                          );
                          setSlots(result.items || []);
                        }
                      }}
                    >
                      <i />
                    </button>
                  </td>
                  <td>
                    <div className="button-row">
                      <button
                        className="text-button"
                        onClick={() => setEdit(s)}
                      >
                        编辑时段
                      </button>
                      <button
                        className="text-button"
                        onClick={() => {
                          setRemoveSlot(s);
                          setRemoveReason("");
                        }}
                      >
                        移除时段
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!slots.length && <Empty title="尚未配置配送时段" />}
      </Panel>
      {!DEMO && <SlotProposals slots={slots} />}
      {removeSlot && (
        <Modal title="移除配送时段" onClose={() => setRemoveSlot(null)}>
          <Notice>
            将归档「{removeSlot.name}
            」，保留历史记录；有关联中的订单或批次时无法移除。
          </Notice>
          <Field label="移除原因（选填）">
            <input
              value={removeReason}
              onChange={(event) => setRemoveReason(event.target.value)}
            />
          </Field>
          <Button
            busy={pendingActions[`/admin/slots/${removeSlot.id}`]}
            onClick={async () => {
              const selected = removeSlot;
              const ok = await act(
                `/admin/slots/${selected.id}`,
                {
                  expectedVersion: (selected as Record<string, any>).version,
                  reason: removeReason,
                },
                (current) => ({
                  ...current,
                  slots: current.slots.filter(
                    (item) => item.id !== selected.id,
                  ),
                }),
                "DELETE",
              );
              if (!ok) return;
              setSlots((current) =>
                current.filter((item) => item.id !== selected.id),
              );
              setRemoveSlot(null);
              toast("配送时段已移除，历史记录保留");
            }}
          >
            确认移除
          </Button>
        </Modal>
      )}
      {edit && (
        <Modal
          title={edit.id ? "编辑配送时段" : "新建配送时段"}
          onClose={() => setEdit(null)}
        >
          <Field label="配送日期">
            <input
              type="date"
              value={edit.date || ""}
              onChange={(e) => setEdit({ ...edit, date: e.target.value })}
            />
          </Field>
          <Field label="时段名称">
            <input
              value={edit.name || ""}
              onChange={(e) => setEdit({ ...edit, name: e.target.value })}
              placeholder="例如 晚间配送"
            />
          </Field>
          <div className="form-grid">
            <Field label="开始时间">
              <input
                type="time"
                value={edit.start || ""}
                onChange={(e) => setEdit({ ...edit, start: e.target.value })}
              />
            </Field>
            <Field label="结束时间">
              <input
                type="time"
                value={edit.end || ""}
                onChange={(e) => setEdit({ ...edit, end: e.target.value })}
              />
            </Field>
          </div>
          <Field label="预约容量（单）">
            <input
              type="number"
              min="1"
              value={edit.capacity || 30}
              onChange={(e) =>
                setEdit({ ...edit, capacity: Number(e.target.value) })
              }
            />
          </Field>
          <Button
            busy={
              pendingActions[
                edit?.id ? `/admin/slots/${edit.id}` : "/admin/slots"
              ]
            }
            onClick={save}
          >
            保存时段
          </Button>
        </Modal>
      )}
    </>
  );
}

function SlotProposals({ slots }: { slots: Slot[] }) {
  const [orders, setOrders] = useState<AdminOrderView[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [target, setTarget] = useState<AdminOrderView | null>(null);
  const [slotId, setSlotId] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const available = slots.filter(
    (slot) =>
      slot.enabled &&
      Number((slot as Record<string, any>).remaining ?? slot.capacity) > 0,
  );
  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const result = await growthRequest<{ items: AdminOrderView[] }>(
        "/admin/orders?status=PENDING_CONFIRMATION&limit=100",
      );
      setOrders(result.items || []);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void load();
  }, []);
  const propose = async () => {
    if (!target) return;
    let body: ReturnType<typeof slotProposalPayload>;
    try {
      body = slotProposalPayload(target, slotId, reason);
    } catch (cause) {
      setError((cause as Error).message);
      return;
    }
    setBusy(true);
    setError("");
    try {
      await growthRequest(
        `/admin/orders/${encodeURIComponent(target.id)}/slot-proposals`,
        { method: "POST", body },
      );
      setOrders((current) =>
        current.map((order) =>
          order.id === target.id
            ? { ...order, slotProposal: { slotId, reason, confirmed: false } }
            : order,
        ),
      );
      setTarget(null);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const slotLabel = (id?: string | null) => {
    const slot = slots.find((row) => row.id === id);
    return slot
      ? `${slot.date} ${slot.start}–${slot.end} ${slot.name}`
      : id || "—";
  };
  return (
    <Panel title="待结算订单时段建议">
      <p className="muted">
        管理员只能提出时段变更建议；必须由客户在订单页确认后才会生效。本页不会替客户同意，也不会改变订单的已确认时段。
      </p>
      {error && (
        <Notice type="error">
          <span role="alert">{error}</span>
        </Notice>
      )}
      <div className="button-row">
        <Button variant="secondary" busy={loading} onClick={load}>
          刷新待结算订单
        </Button>
      </div>
      {!loading && !orders.length ? (
        <Empty title="暂无待结算订单" text="客户确认价格后才会出现在这里。" />
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>订单</th>
                <th>当前建议</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((order) => (
                <tr key={order.id}>
                  <td>
                    <b>{order.pickupCode || order.id}</b>
                    <small>版本 {order.version}</small>
                  </td>
                  <td>
                    {order.slotProposal
                      ? `${slotLabel(order.slotProposal.slotId)} · 待客户确认`
                      : "尚未提出"}
                  </td>
                  <td>
                    <Button
                      variant="secondary"
                      onClick={() => {
                        setTarget(order);
                        setSlotId("");
                        setReason("");
                        setError("");
                      }}
                    >
                      建议变更时段
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {target && (
        <Modal
          title={`建议时段 · ${target.pickupCode || target.id}`}
          onClose={() => setTarget(null)}
        >
          <div className="form-stack">
            <Field label="建议时段">
              <select
                value={slotId}
                onChange={(event) => setSlotId(event.target.value)}
              >
                <option value="">请选择可预约时段</option>
                {available.map((slot) => (
                  <option key={slot.id} value={slot.id}>
                    {slot.date} {slot.start}–{slot.end} {slot.name}（剩余{" "}
                    {Number(
                      (slot as Record<string, any>).remaining ?? slot.capacity,
                    )}
                    ）
                  </option>
                ))}
              </select>
            </Field>
            <Field label="变更原因">
              <textarea
                rows={3}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                required
              />
            </Field>
            <Notice type="warning">
              提交后仅写入建议，订单仍为待结算。客户确认前不得安排配送；客户拒绝后需重新建议。
            </Notice>
            <div className="form-actions">
              <Button
                variant="secondary"
                type="button"
                onClick={() => setTarget(null)}
              >
                取消
              </Button>
              <Button
                busy={busy}
                disabled={!available.length}
                onClick={propose}
              >
                提交建议
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </Panel>
  );
}
