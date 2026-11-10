import { formatDisplayTime } from "../lib/display-format";
import { slotCurrentlySelectable } from "../customer/checkout";
import { useEffect, useRef, useState } from "react";
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
import { NumberInput } from "../components/number-input";
import { campusOfOrder, campusesOfBatch, slotsForCampus } from "../lib/campus";
import { CampusFilter, CampusTag, orderCampusId } from "./campus-filter";
import {
  SlotDateFilter,
  SlotDateSummary,
  useSlotDateFilter,
} from "./slot-date-filter";
import { batchPrimaryAction, canStartBatch } from "../lib/domain";
import type { Batch, Campus, DataState, Slot } from "../lib/types";
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
  const [status, setStatus] = useState("");
  const [campus, setCampus] = useState("");
  const [createBatch, setCreateBatch] = useState(false);
  const batchCampuses = (batch: Batch) =>
    campusesOfBatch(data.campuses, data.buildings, data.orders, batch);
  const filterCampuses = [...(data.campuses || [])];
  for (const batch of data.batches) {
    if (
      batch.campusId &&
      !filterCampuses.some((c) => c.id === batch.campusId)
    ) {
      filterCampuses.push({
        id: batch.campusId,
        name: batch.campusName || batch.campusId,
        themeColor: "#888",
        selectionEnabled: false,
        sortOrder: 0,
        mapVersion: 0,
        items: [],
      });
    }
  }
  const memberCampusIds = (batch: Batch) =>
    new Set(
      data.orders
        .filter((o) => batch.orderIds.includes(o.id))
        .map((o) => orderCampusId(data.buildings || [], o as any))
        .filter(Boolean),
    );
  const hasMixedCampus = (batch: Batch) => {
    const ids = memberCampusIds(batch);
    return (
      ids.size > 1 ||
      (!!batch.campusId && [...ids].some((id) => id !== batch.campusId))
    );
  };
  const visibleBatches = data.batches.filter(
    (batch) =>
      (!status || batch.status === status) &&
      (!campus ||
        (batch.campusId
          ? batch.campusId === campus
          : batchCampuses(batch).some((item) => item.id === campus))),
  );
  return (
    <>
      <PageHeading
        title="配送调度"
        description="先规划，再核对；资料全部装袋后，才开始配送。"
        action={
          <div className="button-row">
            <Link className="text-button" to={`${ADMIN}/slots`}>
              <Clock size={16} />
              管理配送时段
            </Link>
            <Button onClick={() => setCreateBatch(true)}>
              <Plus size={17} />
              新建批次
            </Button>
          </div>
        }
      />
      <div className="production-stats">
        {[
          ["今日批次", visibleBatches.length],
          [
            "规划中",
            visibleBatches.filter((b) => b.status === "PLANNED").length,
          ],
          [
            "已延期",
            visibleBatches.filter((b) => b.status === "POSTPONED").length,
          ],
          [
            "配送中",
            visibleBatches.filter((b) => b.status === "DELIVERING").length,
          ],
          [
            "已完成",
            visibleBatches.filter((b) => b.status === "COMPLETED").length,
          ],
        ].map(([l, n]) => (
          <div key={l}>
            <span>{l}</span>
            <strong>{n}</strong>
          </div>
        ))}
      </div>
      {!!filterCampuses.length && (
        <Panel className="filter-panel">
          <div className="filter-row">
            <CampusFilter
              campuses={filterCampuses}
              value={campus}
              onChange={setCampus}
            />
            <span className="muted">
              配送批次只能安排同一校区的订单，跨校区订单不会被加入批次。
            </span>
          </div>
        </Panel>
      )}
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
        {visibleBatches.map((b) => {
          const extra = b as Batch & Record<string, any>;
          const ready =
            extra.preparedCount ??
            b.orderIds.filter(
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
                {extra.isRegular && <span className="badge">自动归集</span>}
                {extra.sealedAt && <span className="badge">已封批</span>}
              </div>
              <h2>{b.name}</h2>
              {b.status === "PLANNED" && b.autoPublishBlockedReason && (
                <p className="muted">
                  自动发布未完成。{b.autoPublishBlockedReason}
                </p>
              )}
              <div className="batch-campus">
                {batchCampuses(b).length ? (
                  batchCampuses(b).map((item) => (
                    <CampusTag key={item.id} campus={item} />
                  ))
                ) : extra.campusName ? (
                  <span>{extra.campusName}</span>
                ) : (
                  <span className="muted">校区待定</span>
                )}
                {hasMixedCampus(b) && (
                  <span className="red-text">跨校区，请拆分后再配送</span>
                )}
              </div>
              <p>
                <CalendarDays size={15} />
                {b.date} <span>{b.time}</span>
              </p>
              <p>
                <Users size={15} />
                {(b as Batch & { courierName?: string }).courierName ||
                  data.staff.find((s) => s.id === b.courierId)?.name ||
                  "未分配配送员（发布前指派）"}
              </p>
              <div className="batch-count">
                <div>
                  <strong>{b.orderIds.length}</strong>
                  <span>规划订单</span>
                </div>
                <div>
                  <strong>{ready}</strong>
                  <span>准备完成</span>
                </div>
                {typeof extra.unpreparedCount === "number" && (
                  <div>
                    <strong>{extra.unpreparedCount}</strong>
                    <span>仍待准备</span>
                  </div>
                )}
                <ArrowRight size={19} />
              </div>
            </Link>
          );
        })}
      </div>
      {createBatch && <BatchForm onClose={() => setCreateBatch(false)} />}
      {!visibleBatches.length && (
        <Panel>
          <Empty
            title={
              data.batches.length ? "该校区暂无配送批次" : "还没有配送批次"
            }
            text={
              data.batches.length
                ? "切换校区或清除筛选查看其他批次。"
                : "订单确认收款后会自动生成配送批次；特殊拆分或补送可从批次详情处理。"
            }
          />
        </Panel>
      )}
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
  const [batchCampus, setBatchCampus] = useState("");
  const [batchCampuses, setBatchCampuses] = useState<Campus[]>(
    data.campuses || [],
  );
  useEffect(() => {
    if (DEMO || batch) return;
    let active = true;
    growthRequest<{ items: Campus[] }>("/admin/campuses")
      .then((result) => {
        if (active) setBatchCampuses(result.items || []);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [batch]);
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
  const [reasonError, setReasonError] = useState("");
  const nameChanged = !!batch && name !== batch.name;
  const windowChanged = !!batch && (date !== batch.date || time !== batch.time);
  const canAssignFirst =
    batch &&
    !batch.courierId &&
    (batch.allowedActions?.includes("ASSIGN_COURIER") ||
      (DEMO && batch.status === "PLANNED"));
  const assignmentOnly = canAssignFirst && !nameChanged && !windowChanged;
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
        <Field label="配送校区">
          <CampusFilter
            campuses={batchCampuses}
            value={batchCampus}
            allLabel="请选择校区"
            ariaLabel="配送校区"
            onChange={(value) => {
              setBatchCampus(value);
              setSlotId("");
              setDate("");
              setTime("");
            }}
          />
        </Field>
      )}
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
            {slotsForCampus(availableSlots, batchCampus)
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
      {batch && !assignmentOnly && (
        <Field label="修改原因" error={reasonError}>
          <textarea
            value={reason}
            maxLength={2000}
            onChange={(e) => {
              setReason(e.target.value);
              setReasonError("");
            }}
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
            !courier
          ) {
            toast("请完整填写批次信息并选择配送员");
            return;
          }
          if (batch && !assignmentOnly && !reason.trim()) {
            setReasonError("请填写修改原因");
            return;
          }
          const id = batch?.id || crypto.randomUUID();
          if (
            await act(
              batch ? `/admin/batches/${id}` : "/admin/batches",
              {
                ...(!batch || nameChanged ? { name } : {}),
                ...(batch ? (windowChanged ? { date, time } : {}) : { slotId }),
                courierId: courier,
                expectedVersion: batch?.version,
                ...(!assignmentOnly ? { reason: reason.trim() } : {}),
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
                        campusId: batchCampus,
                        version: 1,
                      },
                    ],
              }),
              batch ? "PATCH" : "POST",
            )
          ) {
            toast(
              assignmentOnly
                ? "已指派配送员"
                : batch
                  ? "批次信息已更新"
                  : "配送批次已创建",
            );
            onClose();
          }
        }}
      >
        {assignmentOnly ? "指派配送员" : batch ? "保存批次信息" : "创建批次"}
      </Button>
    </Modal>
  );
}
function WithdrawPublication({
  batch,
  onClose,
}: {
  batch: Batch;
  onClose: () => void;
}) {
  const { act, toast, pendingActions } = useStore();
  const [confirmed, setConfirmed] = useState(false);
  const [reason, setReason] = useState("");
  const [issue, setIssue] = useState("");
  const started = batch.status === "DELIVERING";
  const path = `/admin/batches/${batch.id}/withdraw-publication`;
  return (
    <Modal title="撤回发布" onClose={onClose}>
      <Notice type="warning">
        撤回后，配送员需停止本批次配送。名单和配送员会保留，重新配送时请手动发布。
        {started && "请先联系配送员停止交付。"}
      </Notice>
      {started && (
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(event) => setConfirmed(event.target.checked)}
          />
          我已确认所有资料尚未交给客户
        </label>
      )}
      <Field label="撤回原因（选填）">
        <textarea
          value={reason}
          maxLength={300}
          onChange={(event) => setReason(event.target.value)}
        />
      </Field>
      {issue && (
        <Notice type="error">
          <span role="alert">{issue}</span>
        </Notice>
      )}
      <Button
        className="full"
        variant="danger"
        busy={pendingActions[path]}
        disabled={started && !confirmed}
        onClick={async () => {
          setIssue("");
          const ok = await act(
            path,
            {
              expectedVersion: batch.version,
              confirmNoHandover: confirmed,
              reason: reason.trim(),
            },
            (state) => state,
            "POST",
            undefined,
            (error) =>
              setIssue(
                error instanceof Error
                  ? error.message
                  : "撤回失败，请刷新后重试",
              ),
          );
          if (ok) {
            toast("已撤回发布");
            onClose();
          }
        }}
      >
        确认撤回
      </Button>
    </Modal>
  );
}

export function BatchDetail() {
  const { id } = useParams();
  const { data, act, toast, pendingActions } = useStore();
  const batch = data.batches.find((b) => b.id === id);
  const [edit, setEdit] = useState(false);
  const [withdrawal, setWithdrawal] = useState<Batch | null>(null);
  const [mode, setMode] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [target, setTarget] = useState("");
  const [candidateCampus, setCandidateCampus] = useState("");
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");
  const [reason, setReason] = useState("");
  const [preview, setPreview] = useState<{
    id: string;
    blockers: string[];
  } | null>(null);
  const [commitBlockers, setCommitBlockers] = useState<TrashBlocker[]>([]);
  const scopeSignature = JSON.stringify({
    mode,
    id,
    selected,
    target,
    campus: batch?.campusId,
    members: batch?.orderIds,
    version: batch?.version,
    targetBatch: data.batches.find((item) => item.id === target),
  });
  const scopeRef = useRef(scopeSignature);
  scopeRef.current = scopeSignature;
  useEffect(() => {
    setPreview(null);
    setCommitBlockers([]);
  }, [scopeSignature]);
  useEffect(() => {
    if (!target) return;
    const source = data.batches.find((item) => item.id === id);
    const destination = data.batches.find((item) => item.id === target);
    if (
      !source?.campusId ||
      destination?.campusId !== source.campusId ||
      ["DELIVERING", "COMPLETED", "CANCELLED"].includes(destination.status)
    )
      setTarget("");
  }, [data.batches, id, target]);
  useEffect(() => {
    const source = data.batches.find((item) => item.id === id);
    if (!source) return;
    if (mode === "MERGE") {
      setSelected((current) =>
        JSON.stringify(current) === JSON.stringify(source.orderIds)
          ? current
          : [...source.orderIds],
      );
      return;
    }
    if (!["ADD", "REMOVE", "TRANSFER", "SPLIT"].includes(mode)) return;
    setSelected((current) => {
      const next = current.filter((orderId) => {
        if (mode !== "ADD") return source.orderIds.includes(orderId);
        const order = data.orders.find((item) => item.id === orderId);
        return (
          !!source.campusId &&
          !!order &&
          !order.batchId &&
          !["CANCELLED", "DELIVERED", "DELIVERING"].includes(order.status) &&
          orderCampusId(data.buildings || [], order as any) === source.campusId
        );
      });
      return next.length === current.length ? current : next;
    });
  }, [data.batches, data.orders, data.buildings, id, mode]);
  if (!batch) return <Empty title="配送批次不存在" />;
  const orders = data.orders.filter((o) => batch.orderIds.includes(o.id));
  const batchCampusList = campusesOfBatch(
    data.campuses,
    data.buildings,
    data.orders,
    batch,
  );
  const batchCampusId = batch.campusId || "";
  const memberCampusIds = new Set(
    orders
      .map((order) => orderCampusId(data.buildings || [], order as any))
      .filter(Boolean),
  );
  const mixedCampus =
    memberCampusIds.size > 1 ||
    (!!batchCampusId &&
      [...memberCampusIds].some((value) => value !== batchCampusId));
  const canStart = canStartBatch(orders) && !mixedCampus;
  const candidates =
    mode === "ADD"
      ? data.orders.filter((o) => {
          if (o.batchId) return false;
          if (["CANCELLED", "DELIVERED", "DELIVERING"].includes(o.status)) {
            return false;
          }
          const orderCampus = orderCampusId(data.buildings || [], o as any);
          if (!batchCampusId || orderCampus !== batchCampusId) return false;
          if (candidateCampus && orderCampus !== candidateCampus) return false;
          return true;
        })
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
    setCandidateCampus(m === "ADD" ? batchCampusId : "");
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
    if (mode === "ADD" && selected.length) {
      const selectedCampuses = new Set(
        selected
          .map((oid) => {
            const order = data.orders.find((row) => row.id === oid);
            return campusOfOrder(data.campuses, data.buildings, order)?.id;
          })
          .filter(Boolean),
      );
      if (selectedCampuses.size > 1) {
        toast("一个批次只能包含同一校区的订单，请先按校区分批");
        return;
      }
      if (
        batchCampusId &&
        selectedCampuses.size === 1 &&
        !selectedCampuses.has(batchCampusId)
      ) {
        toast("所选订单不属于本批次校区，请切换到对应校区");
        return;
      }
    }
    if (mode === "START" && mixedCampus) {
      blockers.push("批次包含多个校区的订单，请先拆分为单校区批次再开始配送");
    }
    if (mode === "START" && !canStart)
      blockers = blockers.concat(
        orders
          .filter((o) => o.packingStatus !== "PACKED" || o.addressReview)
          .map(
            (o) =>
              `${o.pickupCode}：${o.packingStatus !== "PACKED" ? "尚未完成装袋" : "地址尚待复核"}`,
          ),
      );
    if (mode === "DELETE" && batch.status === "DELIVERING")
      blockers.push("配送中批次不能直接删除，请先处理实际配送执行");
    if (!DEMO) {
      try {
        const requestedScope = scopeRef.current;
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
        if (scopeRef.current !== requestedScope) {
          toast("批次范围已变化，请重新预览");
          return;
        }
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
            {batch.allowedActions?.includes("WITHDRAW_PUBLICATION") && (
              <Button
                variant="danger-outline"
                onClick={() => setWithdrawal(batch)}
              >
                撤回发布
              </Button>
            )}
          </div>
        }
      />
      <div className="batch-detail-summary">
        <Badge status={batch.status} />
        {batch.publishedAutomatically && (
          <span className="badge">自动发布</span>
        )}
        {(batch as Record<string, any>).isRegular && (
          <span className="badge">自动归集</span>
        )}
        {(batch as Record<string, any>).sealedAt && (
          <span className="badge">已封批</span>
        )}
        {batchCampusList.map((item) => (
          <CampusTag key={item.id} campus={item} />
        ))}
        {!batchCampusList.length && (
          <span className={batch.campusName ? "" : "muted"}>
            {batch.campusName || batch.campusId || "校区待定"}
          </span>
        )}
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
      {batch.status === "PLANNED" && batch.publicationWithdrawnAt && (
        <Notice>本批次已撤回，恢复配送时请手动发布。</Notice>
      )}
      {batch.status === "PLANNED" && batch.autoPublishBlockedReason && (
        <Notice type="warning">
          自动发布未完成。{batch.autoPublishBlockedReason}
        </Notice>
      )}
      {(batch as Record<string, any>).isRegular &&
        batch.status === "PLANNED" &&
        !(batch as Record<string, any>).sealedAt && (
          <Notice>
            该批次由确认收款自动归集，接单截止前名单仍会更新；发布前需先结束接单并封批。
          </Notice>
        )}
      {batch.status === "PLANNED" &&
        !(batch as Record<string, any>).sealedAt &&
        !!(batch as Record<string, any>).slotId && (
          <Notice>
            接单截止或提前封批后才能发布批次；封批会冻结名单，不再自动加入新订单。
          </Notice>
        )}
      {mixedCampus && (
        <Notice type="error">
          批次包含多个校区的订单，跨校区无法出发。请先使用“拆分批次”按校区拆开，再安排配送。
        </Notice>
      )}
      {!canStart && !mixedCampus && (
        <Notice>
          批次允许提前规划，但有订单未装袋或地址待复核。开始配送会整批检查，不会静默出发一部分。
        </Notice>
      )}
      <Panel
        title="批次订单"
        action={
          <Button
            variant="secondary"
            disabled={
              ["DELIVERING", "COMPLETED", "CANCELLED"].includes(batch.status) ||
              !!(batch as Record<string, any>).sealedAt
            }
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
                    <CampusTag
                      campus={campusOfOrder(data.campuses, data.buildings, o)}
                    />
                    <div>
                      {data.buildings.find((b) => b.id === o.locationId)?.name}
                    </div>
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
      {withdrawal && (
        <WithdrawPublication
          batch={withdrawal}
          onClose={() => setWithdrawal(null)}
        />
      )}
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
              {mode === "ADD" && !batchCampusId && (
                <Notice type="error">
                  该批次缺少明确校区，无法添加订单，请先核对旧数据。
                </Notice>
              )}
              {mode === "ADD" && batchCampusId && (
                <p className="form-hint">
                  本批次已锁定校区：
                  {batchCampusList.map((item) => item.name).join(" / ") ||
                    batch.campusName ||
                    batchCampusId}
                </p>
              )}
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
                      .filter((b) => {
                        if (
                          b.id === id ||
                          ["DELIVERING", "COMPLETED", "CANCELLED"].includes(
                            b.status,
                          )
                        ) {
                          return false;
                        }
                        return !!batchCampusId && b.campusId === batchCampusId;
                      })
                      .map((b) => {
                        const targetCampuses = campusesOfBatch(
                          data.campuses,
                          data.buildings,
                          data.orders,
                          b,
                        );
                        return (
                          <option key={b.id} value={b.id}>
                            {targetCampuses.length
                              ? `${targetCampuses.map((item) => item.name).join(" / ")} · `
                              : "校区待定 · "}
                            {b.name}
                          </option>
                        );
                      })}
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
const SLOT_STATUS_LABELS: Record<string, string> = {
  OPEN: "接单中",
  NOT_OPEN: "未到开放",
  CUTOFF: "已截单",
  SEALED: "提前封批",
  FULL: "已满",
  DISABLED: "人工停用",
  CONFIG_MISSING: "待补接单规则",
  ARCHIVED: "已归档",
  SERVICE_CLOSED: "服务暂停",
};

function toShanghaiInput(value?: string | null) {
  if (!value) return "";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "";
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  })
    .format(date)
    .replace(" ", "T");
}

function localInputLabel(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(date);
}

export function Slots() {
  const { data, act, toast, pendingActions } = useStore();
  const [edit, setEdit] = useState<
    (Partial<Slot> & Record<string, any>) | null
  >(null);
  const [slots, setSlots] = useState<Slot[]>(data.slots);
  const [slotError, setSlotError] = useState("");
  const [campuses, setCampuses] = useState<Campus[]>(data.campuses || []);
  const [campusFilter, setCampusFilter] = useState("");
  useEffect(() => {
    if (DEMO) {
      setCampuses(data.campuses || []);
      return;
    }
    let active = true;
    growthRequest<{ items: Campus[] }>("/admin/campuses")
      .then((result) => {
        if (active) setCampuses(result.items || []);
      })
      .catch(() => {
        if (active) setCampuses(data.campuses || []);
      });
    return () => {
      active = false;
    };
  }, [data.campuses]);
  const campusName = (slot: Slot) =>
    slot.campusName ||
    campuses.find((c) => c.id === slot.campusId)?.name ||
    "校区待定";
  const campusSlots = slots.filter(
    (slot) => !campusFilter || slot.campusId === campusFilter,
  );
  const dateFilter = useSlotDateFilter(campusSlots.map((slot) => slot.date));
  const visibleSlots = campusSlots.filter(
    (slot) =>
      !dateFilter.error &&
      slot.date >= dateFilter.range.start &&
      slot.date <= dateFilter.range.end,
  );
  const [formErrors, setFormErrors] = useState<Record<string, string>>({});
  const [removeSlot, setRemoveSlot] = useState<Slot | null>(null);
  const [removeReason, setRemoveReason] = useState("");
  const [sealSlot, setSealSlot] = useState<Slot | null>(null);
  const [sealPreview, setSealPreview] = useState<any>(null);
  const [sealReason, setSealReason] = useState("");
  const [sealBusy, setSealBusy] = useState(false);
  const [sealError, setSealError] = useState("");

  const reload = async () => {
    if (DEMO) return;
    const result = await growthRequest<{ items: Slot[] }>("/admin/slots");
    setSlots(result.items || []);
  };
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

  const openEditor = (slot?: Slot) => {
    setFormErrors({});
    if (!slot) {
      setEdit({
        enabled: true,
        capacity: 30,
        campusId:
          campuses.filter((c) => c.selectionEnabled).length === 1
            ? campuses.find((c) => c.selectionEnabled)?.id
            : "",
      });
      return;
    }
    setEdit({
      ...slot,
      openAt: toShanghaiInput((slot as any).openAt),
      cutoffAt: toShanghaiInput((slot as any).cutoffAt),
    });
  };

  const save = async () => {
    if (!edit) return;
    const errors: Record<string, string> = {};
    if (!edit.campusId) errors.campusId = "请选择配送校区";
    if (!edit.date) errors.date = "请选择配送日期";
    if (!edit.name?.trim()) errors.name = "请填写时段名称";
    if (!edit.start) errors.start = "请选择预计配送开始时间";
    if (!edit.end) errors.end = "请选择预计配送结束时间";
    if (edit.start && edit.end && edit.start >= edit.end)
      errors.end = "预计配送结束时间必须晚于开始时间";
    if (!edit.openAt) errors.openAt = "请选择开放下单时间";
    if (!edit.cutoffAt) errors.cutoffAt = "请选择截单时间";
    if (edit.date && edit.start && edit.openAt) {
      const open = new Date(edit.openAt);
      const start = new Date(`${edit.date}T${edit.start}:00+08:00`);
      if (
        Number.isFinite(open.getTime()) &&
        Number.isFinite(start.getTime()) &&
        open >= start
      )
        errors.openAt = "开放下单时间必须早于预计配送开始时间";
    }
    if (edit.cutoffAt) {
      const cutoff = new Date(edit.cutoffAt);
      const open = edit.openAt ? new Date(edit.openAt) : null;
      const start =
        edit.date && edit.start
          ? new Date(`${edit.date}T${edit.start}:00+08:00`)
          : null;
      if (
        open &&
        Number.isFinite(cutoff.getTime()) &&
        Number.isFinite(open.getTime()) &&
        cutoff <= open
      )
        errors.cutoffAt = "截单时间必须晚于开放下单时间";
      if (
        start &&
        Number.isFinite(cutoff.getTime()) &&
        Number.isFinite(start.getTime()) &&
        cutoff >= start
      )
        errors.cutoffAt = "截单时间必须早于预计配送开始时间";
    }
    if (
      edit.capacity == null ||
      !Number.isSafeInteger(edit.capacity) ||
      edit.capacity < 1 ||
      edit.capacity > 100000
    )
      errors.capacity = "预约容量必须是 1 到 100000 之间的整数";
    else if (edit.capacity < Number((edit as any).occupied || 0))
      errors.capacity = "容量不能小于已占用数量";
    setFormErrors(errors);
    const first = Object.keys(errors)[0];
    if (first) {
      const target = document.querySelector(`[data-slot-field="${first}"]`);
      (target as HTMLElement | null)?.focus();
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
      autoPublish: edit.autoPublish ?? false,
      capacity: edit.capacity,
    } as Slot;
    const payload: Record<string, unknown> = {
      campusId: edit.campusId,
      date: edit.date,
      name: edit.name,
      start: edit.start,
      end: edit.end,
      openAt: edit.openAt,
      cutoffAt: edit.cutoffAt,
      capacity: edit.capacity,
      enabled: edit.enabled ?? true,
      autoPublish: edit.autoPublish ?? false,
      reason: (edit as any).reason || "",
    };
    if (edit.id) payload.expectedVersion = (edit as any).version;
    if (
      await act(
        edit.id ? `/admin/slots/${id}` : "/admin/slots",
        payload,
        (state) => ({
          ...state,
          slots: edit.id
            ? state.slots.map((x) => (x.id === id ? slot : x))
            : [...state.slots, slot],
        }),
        edit.id ? "PATCH" : "POST",
        undefined,
        (error) =>
          setFormErrors((current) => ({
            ...current,
            submit:
              error instanceof Error ? error.message : "保存失败，请刷新后重试",
          })),
      )
    ) {
      setEdit(null);
      toast("配送时段已保存");
      if (!DEMO) await reload();
    }
  };

  const openSeal = async (slot: Slot) => {
    setSealSlot(slot);
    setSealPreview(null);
    setSealReason("");
    setSealError("");
    setSealBusy(true);
    try {
      const preview = await growthRequest(
        `/admin/slots/${slot.id}/seal/preview`,
        { method: "POST", body: { expectedVersion: (slot as any).version } },
      );
      setSealPreview(preview);
    } catch (error) {
      setSealError((error as Error).message);
    } finally {
      setSealBusy(false);
    }
  };

  const commitSeal = async () => {
    if (!sealSlot || !sealPreview) return;
    if (!sealReason.trim()) {
      setSealError("请填写封批原因");
      return;
    }
    setSealBusy(true);
    setSealError("");
    try {
      await growthRequest(`/admin/slots/${sealSlot.id}/seal`, {
        method: "POST",
        body: {
          expectedVersion:
            (sealPreview.slot as any)?.version ?? (sealSlot as any).version,
          planId: sealPreview.planId,
          reason: sealReason.trim(),
        },
      });
      toast("已结束接单并封批，名单已冻结");
      setSealSlot(null);
      await reload();
    } catch (error) {
      setSealError((error as Error).message);
    } finally {
      setSealBusy(false);
    }
  };

  return (
    <>
      <PageHeading
        title="配送时段"
        description="按具体日期开放预约；开放与截单时间决定申请资格，提前封批会冻结名单。"
        action={
          <Button onClick={() => openEditor()}>
            <Plus size={17} />
            新建时段
          </Button>
        }
      />
      <Notice>
        截单由服务器时间即时生效，不依赖定时任务；停止新预约不会取消已有配送。
      </Notice>
      {slotError && (
        <Notice type="error">
          <span role="alert">{slotError}</span>
        </Notice>
      )}
      <Panel>
        <div className="slot-filter-toolbar">
          {!!campuses.length && (
            <Field label="校区筛选">
              <CampusFilter
                campuses={campuses}
                value={campusFilter}
                onChange={setCampusFilter}
              />
            </Field>
          )}
          <SlotDateFilter filter={dateFilter} />
        </div>
        {!dateFilter.error && (
          <SlotDateSummary
            range={dateFilter.range}
            count={visibleSlots.length}
          />
        )}
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>校区</th>
                <th>日期 / 名称</th>
                <th>预计配送</th>
                <th>接单窗口</th>
                <th>容量 / 已占 / 剩余</th>
                <th>接单状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {visibleSlots.map((s: any) => {
                const state =
                  s.acceptanceStatus ||
                  (s.sealedAt ? "SEALED" : s.enabled ? "OPEN" : "DISABLED");
                return (
                  <tr key={s.id}>
                    <td>{campusName(s)}</td>
                    <td>
                      {s.date}
                      <small>
                        {s.weekday} · {s.name}
                      </small>
                    </td>
                    <td className="numeric">
                      {s.start} – {s.end}
                    </td>
                    <td className="numeric">
                      {localInputLabel(s.openAt)} 开放
                      <small>{localInputLabel(s.cutoffAt)} 截单</small>
                    </td>
                    <td>
                      {s.capacity} 单
                      <small>
                        已占 {s.occupied ?? 0} · 剩余{" "}
                        {s.remaining ?? s.capacity}
                      </small>
                    </td>
                    <td>
                      <span
                        className={`status ${state === "OPEN" ? "green" : ""}`}
                      >
                        {SLOT_STATUS_LABELS[state] || state}
                      </span>
                      <small>
                        {s.batchCount ?? 0} 个批次
                        {s.pendingApplicationCount
                          ? ` · ${s.pendingApplicationCount} 份待确认申请`
                          : ""}
                      </small>
                      {s.sealReason && <small>原因：{s.sealReason}</small>}
                      {s.autoPublish && <small>截单后自动发布</small>}
                    </td>
                    <td>
                      <div className="button-row slot-actions">
                        <button
                          className="text-button"
                          onClick={() => openEditor(s)}
                        >
                          编辑
                        </button>
                        {!s.sealedAt && (
                          <button
                            className="text-button"
                            onClick={() => void openSeal(s)}
                          >
                            提前封批
                          </button>
                        )}
                        <button
                          className="text-button"
                          onClick={() => {
                            setRemoveSlot(s);
                            setRemoveReason("");
                          }}
                        >
                          移除
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {!visibleSlots.length && (
          <Empty
            title={
              dateFilter.error
                ? "请调整日期范围"
                : !slots.length
                  ? "尚未配置配送时段"
                  : !campusSlots.length
                    ? "该校区暂无配送时段"
                    : "所选日期范围内暂无配送时段"
            }
            text={
              dateFilter.error
                ? "请检查上方开始日期和结束日期。"
                : slots.length && campusSlots.length
                  ? "可切换日期范围或选择“全部时段”查看。"
                  : "新建时段后会显示在这里。"
            }
          />
        )}
      </Panel>
      {!DEMO && <SlotProposals slots={slots} buildings={data.buildings} />}
      {!DEMO && (
        <PaidArrangementProposals slots={slots} buildings={data.buildings} />
      )}
      {!DEMO && <DeliveryRepairPanel />}
      {removeSlot && (
        <Modal title="移除配送时段" onClose={() => setRemoveSlot(null)}>
          <Notice>
            将归档「{campusName(removeSlot)} · {removeSlot.name}
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
      {sealSlot && (
        <Modal title="提前封批" onClose={() => setSealSlot(null)}>
          <Notice>
            封批会立即结束「{campusName(sealSlot)} · {sealSlot.date} ·{" "}
            {sealSlot.name}
            」的新申请并冻结该时段名单； 不可撤销，需要继续接单请新建时段。
          </Notice>
          {sealError && <Notice type="error">{sealError}</Notice>}
          {!sealPreview && sealBusy && <p>正在检查影响范围…</p>}
          {sealPreview && (
            <>
              {sealPreview.blockers?.length ? (
                <Notice type="error">
                  {sealPreview.blockers
                    .map((b: any) => b.message || b.code)
                    .join("；")}
                </Notice>
              ) : (
                <div className="form-hint">
                  将影响 {sealPreview.impact?.pendingApplications ?? 0}{" "}
                  份待确认申请， 涉及 {sealPreview.impact?.batchCount ?? 0}{" "}
                  个批次。
                  {sealPreview.impact?.campuses?.map((c: any) => (
                    <div key={c.campusId}>
                      {c.campusName}：{c.pendingApplications} 份待确认申请，
                      {c.batchCount} 个批次
                    </div>
                  ))}
                </div>
              )}
              <Field label="封批原因（必填）">
                <textarea
                  value={sealReason}
                  onChange={(event) => setSealReason(event.target.value)}
                  placeholder="例如：配送运力已满，提前结束接单"
                />
              </Field>
              <Button
                busy={sealBusy}
                disabled={!!sealPreview.blockers?.length}
                onClick={commitSeal}
              >
                结束接单并封批
              </Button>
            </>
          )}
        </Modal>
      )}
      {edit && (
        <Modal
          title={edit.id ? "编辑配送时段" : "新建配送时段"}
          onClose={() => setEdit(null)}
        >
          <div className="slot-editor-form">
            {formErrors.submit && (
              <Notice type="error">
                <span role="alert">{formErrors.submit}</span>
              </Notice>
            )}
            <Field
              label="配送校区"
              error={formErrors.campusId}
              hint={
                edit.id
                  ? "已有业务引用的时段不能更换校区，保存时由服务器校验。"
                  : "每个时段只属于一个校区。"
              }
            >
              <select
                data-slot-field="campusId"
                value={edit.campusId || ""}
                disabled={
                  !!edit.id &&
                  ((edit.occupied || 0) > 0 ||
                    (edit.batchCount || 0) > 0 ||
                    (edit.pendingApplicationCount || 0) > 0)
                }
                onChange={(event) =>
                  setEdit({ ...edit, campusId: event.target.value })
                }
              >
                <option value="">请选择配送校区</option>
                {campuses
                  .filter((c) => c.selectionEnabled || c.id === edit.campusId)
                  .map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                      {!c.selectionEnabled ? "（已关闭）" : ""}
                    </option>
                  ))}
              </select>
            </Field>
            {((edit as any).occupied > 0 || (edit as any).batchCount > 0) && (
              <Notice>
                该时段已有订单或批次。修改预计配送时间会影响现有预约，需要调整配送安排；
                截单后不能通过普通编辑延长接单。
              </Notice>
            )}
            <Field label="配送日期" error={formErrors.date}>
              <input
                data-slot-field="date"
                type="date"
                value={edit.date || ""}
                onChange={(e) => setEdit({ ...edit, date: e.target.value })}
              />
            </Field>
            <Field label="时段名称" error={formErrors.name}>
              <input
                data-slot-field="name"
                value={edit.name || ""}
                onChange={(e) => setEdit({ ...edit, name: e.target.value })}
                placeholder="例如 晚间配送"
              />
            </Field>
            <div className="form-grid">
              <Field label="预计配送开始" error={formErrors.start}>
                <input
                  data-slot-field="start"
                  type="time"
                  value={edit.start || ""}
                  onChange={(e) => setEdit({ ...edit, start: e.target.value })}
                />
              </Field>
              <Field label="预计配送结束" error={formErrors.end}>
                <input
                  data-slot-field="end"
                  type="time"
                  value={edit.end || ""}
                  onChange={(e) => setEdit({ ...edit, end: e.target.value })}
                />
              </Field>
            </div>
            <div className="form-grid slot-booking-window">
              <Field
                label="开放下单时间"
                error={formErrors.openAt}
                hint="按北京时间填写，可跨天"
              >
                <input
                  data-slot-field="openAt"
                  type="datetime-local"
                  value={edit.openAt || ""}
                  onChange={(e) => setEdit({ ...edit, openAt: e.target.value })}
                />
              </Field>
              <Field
                label="截单时间"
                error={formErrors.cutoffAt}
                hint="截单后立即停止新申请与自动归批"
              >
                <input
                  data-slot-field="cutoffAt"
                  type="datetime-local"
                  value={edit.cutoffAt || ""}
                  onChange={(e) =>
                    setEdit({ ...edit, cutoffAt: e.target.value })
                  }
                />
              </Field>
            </div>
            <Field
              label="预约容量（单）"
              error={formErrors.capacity}
              hint="容量仅适用于该校区时段；拆分批次不会重复占用"
            >
              <NumberInput
                min={1}
                max={100000}
                value={edit.capacity}
                onValueChange={(capacity) =>
                  setEdit({ ...edit, capacity: capacity ?? undefined })
                }
              />
            </Field>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={edit.enabled ?? true}
                onChange={(e) =>
                  setEdit({ ...edit, enabled: e.target.checked })
                }
              />
              人工开启接单（关闭后停止该校区时段的新申请，不取消已有配送）
            </label>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={edit.autoPublish ?? false}
                onChange={(event) =>
                  setEdit({ ...edit, autoPublish: event.target.checked })
                }
              />
              截单后自动发布
            </label>
            <p className="form-hint">
              请在截单前开启并指派配送员。发布后仍由配送员确认开始。
            </p>
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
          </div>
        </Modal>
      )}
    </>
  );
}

export function PaidArrangementProposals({
  slots,
  buildings = [],
}: {
  slots: Slot[];
  buildings?: DataState["buildings"];
}) {
  const [orders, setOrders] = useState<AdminOrderView[]>([]);
  const [target, setTarget] = useState<AdminOrderView | null>(null);
  const [slotId, setSlotId] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const result = await growthRequest<{ items: AdminOrderView[] }>(
        "/admin/orders?limit=100",
      );
      setOrders(
        (result.items || []).filter(
          (order) =>
            !!order.confirmedSlotId &&
            !["DELIVERED", "CANCELLED"].includes(order.status),
        ),
      );
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void load();
  }, []);
  const available = slotsForCampus(
    slots,
    orderCampusId(buildings, target as any),
  ).filter((slot) => slotCurrentlySelectable(slot));
  useEffect(() => {
    if (slotId && !available.some((slot) => slot.id === slotId)) setSlotId("");
  }, [slotId, available]);
  const submit = async () => {
    if (!target) return;
    if (
      !available.some(
        (slot) => slot.id === slotId && slotCurrentlySelectable(slot),
      )
    ) {
      setError("请选择该订单校区可预约的新时段");
      return;
    }
    if (!reason.trim()) {
      setError("请填写调整原因");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await growthRequest(
        `/admin/orders/${encodeURIComponent(target.id)}/arrangement-proposals`,
        {
          method: "POST",
          body: {
            expectedVersion: target.version,
            slotId: slotId.trim(),
            reason: reason.trim(),
          },
        },
      );
      setTarget(null);
      await load();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel title="已结算订单的配送安排">
      <p className="muted">
        已结算订单改到其他时段需要客户在订单页明确接受；预览与实际生效分离，
        不会重复扣款或重新报价。
      </p>
      {error && <Notice type="error">{error}</Notice>}
      <div className="button-row">
        <Button
          variant="secondary"
          busy={loading}
          disabled={busy}
          onClick={() => void load()}
        >
          刷新已结算订单
        </Button>
      </div>
      {!orders.length && !loading && !error && (
        <Empty title="暂无需要协调的已结算订单" />
      )}
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>取件码</th>
              <th>状态</th>
              <th>当前确认时段</th>
              <th>待确认提案</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {orders.map((order) => {
              const confirmed = slots.find(
                (s) => s.id === order.confirmedSlotId,
              );
              const pending = order.arrangement?.pendingProposal;
              return (
                <tr key={order.id}>
                  <td>{order.pickupCode || order.id.slice(0, 8)}</td>
                  <td>{order.status}</td>
                  <td>
                    {confirmed
                      ? `${confirmed.date} ${confirmed.name} ${confirmed.start}–${confirmed.end}`
                      : order.confirmedSlotId}
                  </td>
                  <td>
                    {pending
                      ? `${pending.proposed?.date || ""} ${pending.proposed?.name || ""} 待客户确认`
                      : "—"}
                  </td>
                  <td>
                    <button
                      className="text-button"
                      onClick={() => {
                        setTarget(order);
                        setSlotId("");
                        setReason("");
                        setError("");
                      }}
                    >
                      调整配送安排
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {target && (
        <Modal title="调整配送安排" onClose={() => setTarget(null)}>
          <Notice>
            订单 {target.pickupCode || target.id.slice(0, 8)}{" "}
            已结算。选择其他时段后， 客户需在订单页确认；接受前原安排仍然有效。
          </Notice>
          <Field label="新的配送时段">
            <select value={slotId} onChange={(e) => setSlotId(e.target.value)}>
              <option value="">请选择时段</option>
              {available.map((slot) => (
                <option key={slot.id} value={slot.id}>
                  {slot.date} · {slot.name} · {slot.start}–{slot.end}
                </option>
              ))}
            </select>
          </Field>
          <Field label="调整原因（会展示给客户）">
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="例如：原时段运力不足，建议调整到次日中午"
            />
          </Field>
          <Button busy={busy} onClick={submit}>
            提交待客户确认的安排
          </Button>
        </Modal>
      )}
    </Panel>
  );
}

export function SlotProposals({
  slots,
  buildings = [],
}: {
  slots: Slot[];
  buildings?: DataState["buildings"];
}) {
  const [orders, setOrders] = useState<AdminOrderView[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [target, setTarget] = useState<AdminOrderView | null>(null);
  const [slotId, setSlotId] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const available = slotsForCampus(
    slots,
    orderCampusId(buildings, target as any),
  ).filter((slot) => slotCurrentlySelectable(slot));
  useEffect(() => {
    if (slotId && !available.some((slot) => slot.id === slotId)) setSlotId("");
  }, [slotId, available]);
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
    if (
      !available.some(
        (slot) => slot.id === slotId && slotCurrentlySelectable(slot),
      )
    ) {
      setError("请选择该订单校区可预约的时段");
      return;
    }
    try {
      body = slotProposalPayload(target, slotId, reason);
    } catch (cause) {
      setError((cause as Error).message);
      return;
    }
    setBusy(true);
    setError("");
    try {
      const updated = await growthRequest<AdminOrderView>(
        `/admin/orders/${encodeURIComponent(target.id)}/slot-proposals`,
        { method: "POST", body },
      );
      setOrders((current) =>
        current.map((order) => (order.id === target.id ? updated : order)),
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

type DeliveryRepairRow = {
  slotId?: string | null;
  orderId?: string;
  pickupCode?: string;
  batchId?: string;
  batchIds?: string[];
  campusId?: string | null;
  campusIds?: string[];
  references?: {
    type: string;
    id: string;
    campusId?: string | null;
    deleted?: boolean;
  }[];
  name?: string;
  date?: string;
  status?: string;
  matchStatus?: string;
  candidateBatchIds?: string[];
  reasons?: string[];
  blockers?: string[];
  nextAction?: string;
};
type DeliveryRepairPreview = {
  readOnly: boolean;
  generatedAt: string;
  counts: Record<string, number>;
  campusScopeIssues?: DeliveryRepairRow[];
  unconfiguredSlots: DeliveryRepairRow[];
  paidUnassignedOrders: DeliveryRepairRow[];
  ambiguousGroups: DeliveryRepairRow[];
  detachedBatches: DeliveryRepairRow[];
  historicalCounts: Record<string, number>;
  nextSteps: string[];
};

export function DeliveryRepairPanel() {
  const [preview, setPreview] = useState<DeliveryRepairPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await growthRequest<DeliveryRepairPreview>(
        "/admin/delivery-repair/preview",
      );
      if (result.readOnly !== true)
        throw new Error("检查结果缺少只读标识，请联系管理员核对接口");
      setPreview(result);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const sections = [
    ["campusScopeIssues", "校区归属需核对的时段"],
    ["unconfiguredSlots", "待补接单规则的时段"],
    ["paidUnassignedOrders", "已结算但缺少有效分配的订单"],
    ["ambiguousGroups", "需核对的同范围旧批次"],
    ["detachedBatches", "关联信息需核对的批次"],
  ] as const;
  const historyLabels: Record<string, string> = {
    deliveredOrders: "已送达订单",
    cancelledOrders: "已取消订单",
    refundedOrders: "有退款订单",
    trashedOrders: "订单回收站",
    trashedBatches: "批次回收站",
    openAfterSaleOrders: "处理中售后订单",
  };
  return (
    <Panel title="旧数据履约检查">
      <p className="form-hint">
        按需读取旧时段、已结算订单和历史批次的关联情况。检查为只读，后续请使用时段配置与配送调度的明确操作。
      </p>
      <Button variant="secondary" busy={busy} onClick={() => void load()}>
        {preview ? "重新检查旧数据" : "查看旧数据检查"}
      </Button>
      {error && <Notice type="error">{error}</Notice>}
      {preview && (
        <>
          <p className="muted">
            检查时间 {formatDisplayTime(preview.generatedAt)} · 只读预览
          </p>
          {sections.map(([key, label]) => (
            <details key={key}>
              <summary>
                {label}：{preview.counts[key] ?? (preview[key] || []).length}
              </summary>
              {!(preview[key] || []).length ? (
                <p className="muted">未发现此类记录</p>
              ) : (
                <ul>
                  {(preview[key] || []).map((row, index) => (
                    <li
                      key={
                        row.orderId ||
                        row.batchId ||
                        `${row.slotId}-${row.campusId}-${index}`
                      }
                    >
                      <p>
                        {row.name ||
                          row.pickupCode ||
                          row.orderId ||
                          row.batchId ||
                          row.slotId}{" "}
                        {row.date || ""} {row.status || ""}
                      </p>
                      {row.orderId && (
                        <p className="form-hint">订单 ID：{row.orderId}</p>
                      )}
                      {row.batchId && (
                        <p className="form-hint">批次 ID：{row.batchId}</p>
                      )}
                      {row.slotId && (
                        <p className="form-hint">时段 ID：{row.slotId}</p>
                      )}
                      {row.campusId && (
                        <p className="form-hint">校区 ID：{row.campusId}</p>
                      )}
                      {!!row.campusIds?.length && (
                        <p className="form-hint">
                          涉及校区 ID：{row.campusIds.join("、")}
                        </p>
                      )}
                      {!!row.references?.length && (
                        <details>
                          <summary>关联记录：{row.references.length}</summary>
                          <ul>
                            {row.references.map((ref, refIndex) => (
                              <li key={`${ref.type}-${ref.id}-${refIndex}`}>
                                {ref.type} · {ref.id} · 校区{" "}
                                {ref.campusId || "待核对"}
                                {ref.deleted ? " · 已删除" : ""}
                              </li>
                            ))}
                          </ul>
                        </details>
                      )}
                      {!!row.batchIds?.length && (
                        <p className="form-hint">
                          批次：{row.batchIds.join("、")}
                        </p>
                      )}
                      {!!row.candidateBatchIds?.length && (
                        <p className="form-hint">
                          候选批次（需人工核对）：
                          {row.candidateBatchIds.join("、")}
                        </p>
                      )}
                      {!!row.reasons?.length && (
                        <p className="form-hint">
                          待核对原因：{row.reasons.join("、")}
                        </p>
                      )}
                      {!!row.blockers?.length && (
                        <p className="form-hint">
                          阻断项：{row.blockers.join("、")}
                        </p>
                      )}
                      {row.nextAction && <p>{row.nextAction}</p>}
                    </li>
                  ))}
                </ul>
              )}
            </details>
          ))}
          <details>
            <summary>历史分类数量（保留原事实）</summary>
            <ul>
              {Object.entries(preview.historicalCounts).map(([key, count]) => (
                <li key={key}>
                  {historyLabels[key] || key}：{count}
                </li>
              ))}
            </ul>
          </details>
          <ul>
            {preview.nextSteps.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ul>
        </>
      )}
    </Panel>
  );
}
