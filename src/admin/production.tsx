import { taskStatus, aggregatePrintStatus } from "../lib/print-state";
import { useEffect, useMemo, useState } from "react";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronRight,
  FileText,
  Layers,
  Package,
  Printer,
  Search,
  ShieldCheck,
  TriangleAlert,
  X,
} from "lucide-react";
import { ADMIN, DEMO, useStore } from "../lib/store";
import { ApiError } from "../lib/api";
import { expandRanges, productionCount, canPack } from "../lib/domain";
import type { PrintFile, Order } from "../lib/types";
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
} from "../components/ui";
import { DocPreview } from "../components/doc-preview";
import { BindingCard } from "../customer/settings";
import { growthRequest } from "../growth/client";
import { errorText } from "./growth/common";
import { campusOfOrder } from "../lib/campus";
import { CampusFilter, CampusTag } from "./campus-filter";
import {
  actualStartPayload,
  packPayload,
  printAssetUrl,
  reprintPayload,
  type BindingTaskView,
  type PackingTaskView,
  type PrintQueueView,
  type PrintTaskView,
  type ReproMode,
  type SheetView,
} from "../v1/admin-api";

function printSidesLabel({ sides, flip }: Pick<PrintTaskView, "sides" | "flip">) {
  if (sides !== "DUPLEX") return "单面";
  if (flip === "LONG_EDGE") return "双面（长边翻转）";
  if (flip === "SHORT_EDGE") return "双面（短边翻转）";
  return "双面（翻转方向未记录）";
}

function taskDescription(f: PrintFile) {
  return f.kind === "PHOTO"
    ? `5 寸照片 · ${f.photo.copies} 张`
    : f.segments
        .map(
          (s) =>
            `第 ${s.range} 页 · ${s.colorMode === "BW" ? "黑白" : "彩色"} · ${printSidesLabel(s)} · ${s.copies} 份`,
        )
        .join(" / ");
}
function DemoPrintCenter() {
  const { data } = useStore();
  const [params, setParams] = useSearchParams();
  const view = params.get("view") || "order";
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [campus, setCampus] = useState("");
  const tasks = data.orders
    .filter(
      (o) =>
        ["PENDING_PRINT", "READY_FOR_DELIVERY"].includes(o.status) &&
        (!params.get("order") || o.id === params.get("order")),
    )
    .flatMap((o) => o.files.map((f) => ({ o, f })))
    .filter(
      ({ o, f }) =>
        (!campus ||
          campusOfOrder(data.campuses, data.buildings, o)?.id === campus) &&
        (!status || f.printStatus === status) &&
        `${o.pickupCode} ${f.name}`
          .toLowerCase()
          .includes(search.toLowerCase()),
    );
  return (
    <>
      <PageHeading
        title="打印中心"
        description="每个任务独立打印，实际出纸后再确认结果。"
      />
      <div className="production-stats">
        {[
          [
            "待打印",
            tasks.filter((t) => t.f.printStatus === "PENDING").length,
            "blue",
          ],
          [
            "待结果确认",
            tasks.filter((t) => t.f.printStatus === "ATTEMPTED").length,
            "amber",
          ],
          [
            "打印已完成",
            tasks.filter((t) => t.f.printStatus === "COMPLETED").length,
            "green",
          ],
          [
            "打印异常",
            tasks.filter((t) => t.f.printStatus === "EXCEPTION").length,
            "red",
          ],
        ].map(([label, count, color]) => (
          <div key={label}>
            <i className={`dot ${color}`} />
            <span>{label}</span>
            <strong>{count}</strong>
          </div>
        ))}
      </div>
      <Panel className="filter-panel">
        <div className="filter-row">
          <div className="tabs compact-tabs">
            {[
              ["order", "按订单"],
              ["type", "按打印类型"],
              ["batch", "按配送批次"],
            ].map(([v, l]) => (
              <button
                key={v}
                className={view === v ? "active" : ""}
                onClick={() => setParams({ view: v })}
              >
                {l}
              </button>
            ))}
          </div>
          <div className="search-input">
            <Search size={17} />
            <input
              placeholder="搜索提取码或文件"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <CampusFilter
            campuses={data.campuses}
            value={campus}
            onChange={setCampus}
          />
          <select
            value={status}
            aria-label="任务状态"
            onChange={(e) => setStatus(e.target.value)}
          >
            <option value="">全部状态</option>
            <option value="PENDING">待打印</option>
            <option value="ATTEMPTED">待结果确认</option>
            <option value="COMPLETED">已完成</option>
            <option value="EXCEPTION">异常</option>
          </select>
        </div>
      </Panel>
      <Notice>
        浏览器无法判断是否实际出纸。请检查打印窗口设为 <b>1 份</b>
        ，文件已展开份数，关闭对话框后请返回确认结果。
      </Notice>
      <div className="production-list">
        {(view === "type"
          ? [...tasks].sort((a, b) =>
              (a.f.segments[0]?.colorMode || "").localeCompare(
                b.f.segments[0]?.colorMode || "",
              ),
            )
          : tasks
        ).map(({ o, f }) => (
          <Panel key={f.id} className="print-task-card">
            <div className="print-task-top">
              <Link className="table-code" to={`${ADMIN}/orders/${o.id}`}>
                {o.pickupCode}
              </Link>
              <span>
                {o.nickname} ·{" "}
                {data.buildings.find((b) => b.id === o.locationId)?.name}
              </span>
              <CampusTag
                campus={campusOfOrder(data.campuses, data.buildings, o)}
              />
              {view === "batch" && (
                <span className="soft-tag">
                  {data.batches.find((b) => b.id === o.batchId)?.name ||
                    "未分配批次"}
                </span>
              )}
              <Badge status={f.printStatus} />
            </div>
            <FileRow file={f} />
            <div className="print-task-details">
              <p>{taskDescription(f)}</p>
              <span>当前 PDF · v{o.contentRevision}</span>
              <span>各分段独立打印 · 不缩印</span>
              <span>
                {f.segments.reduce((n, s) => {
                  try {
                    return (
                      n +
                      productionCount(
                        expandRanges(s.range, f.pages).length,
                        s.copies,
                        s.sides,
                      ).sheets
                    );
                  } catch {
                    return n;
                  }
                }, 0)}{" "}
                张纸
              </span>
            </div>
            <div className="task-card-actions">
              <span className="muted">
                {f.printStatus === "ATTEMPTED"
                  ? "存在未确认的打印尝试，注意避免重复打印"
                  : "请逐项核对任务设置"}
              </span>
              <Link className="btn secondary" to={`${ADMIN}/print/${f.id}`}>
                {f.printStatus === "ATTEMPTED"
                  ? "确认实际打印结果"
                  : "打开打印任务"}
                <ArrowRight size={16} />
              </Link>
            </div>
          </Panel>
        ))}
      </div>
      {!tasks.length && (
        <Panel>
          <Empty
            title="当前没有打印任务"
            text="管理员确认收款后，任务会出现在这里。"
          />
        </Panel>
      )}
    </>
  );
}
function DemoPrintTask() {
  const { id } = useParams();
  const { data, act, toast, pendingActions } = useStore();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const order = data.orders.find((o) => o.files.some((f) => f.id === id));
  const file = order?.files.find((f) => f.id === id);
  const [segmentId, setSegmentId] = useState(
    params.get("segment") || file?.segments[0]?.id || "",
  );
  const [result, setResult] = useState("");
  const [reason, setReason] = useState("");

  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!file) return;
    const ids = file.segments.map((s) => s.id);
    if (file.kind === "DOCUMENT" && !ids.includes(segmentId))
      setSegmentId(
        ids.includes(params.get("segment") || "")
          ? params.get("segment")!
          : ids[0] || "",
      );
    setResult("");
    setReason("");
  }, [file?.id, file?.segments]);
  const segment = file?.segments.find((s) => s.id === segmentId);
  const taskKey = file?.kind === "PHOTO" ? "photo" : segmentId;
  const status = file ? taskStatus(file, taskKey) : "PENDING";
  const attemptId = file?.attemptIds?.[taskKey];
  const finalArtifact = file?.finalArtifacts?.find(
    (a) => a.segmentId === segmentId,
  );
  const pdfUrl = finalArtifact?.url || file?.pdfUrl;
  if (!file || !order) return <Empty title="打印任务不存在" />;
  const update =
    (status: string, createdAttempt?: string) => (s: typeof data) => ({
      ...s,
      orders: s.orders.map((o) =>
        o.id === order.id
          ? {
              ...o,
              files: o.files.map((f) =>
                f.id === file.id
                  ? (() => {
                      const taskResults = {
                        ...f.taskResults,
                        [taskKey]: status,
                      };
                      return {
                        ...f,
                        taskResults,
                        attemptIds: createdAttempt
                          ? { ...f.attemptIds, [taskKey]: createdAttempt }
                          : f.attemptIds,
                        printStatus: aggregatePrintStatus(
                          f.kind === "PHOTO"
                            ? ["photo"]
                            : f.segments.map((s) => s.id),
                          taskResults,
                        ),
                      };
                    })()
                  : f,
              ),
            }
          : o,
      ),
    });
  const attempt = async () => {
    if (status === "COMPLETED" && !reason.trim()) {
      setResult("REPRINT");
      return;
    }
    setBusy(true);
    let createdAttempt = DEMO ? crypto.randomUUID() : "";
    const printWindow = pdfUrl ? window.open("about:blank", "_blank") : null;
    if (printWindow) printWindow.opener = null;
    const ok = await act(
      `/admin/print-tasks/${segmentId || id}/attempts`,
      {
        expectedVersion: order.version,
        artifactId: finalArtifact?.id || file.artifactId || `demo-${file.id}`,
      },
      (state) => update("ATTEMPTED", createdAttempt)(state),
      "POST",
      (r) => {
        createdAttempt = (r as { attemptId: string }).attemptId;
      },
    );
    if (ok) {
      toast("已记录打印尝试。请打开最终 PDF 手动打印，之后确认实际结果。");
      if (printWindow && pdfUrl) printWindow.location.href = pdfUrl;
      else toast("打印窗口被拦截，请点击上方“打开 PDF”，然后返回确认结果。");
    }
    if (!ok) printWindow?.close();
    setBusy(false);
  };
  const confirmResult = async () => {
    if (result !== "COMPLETED" && !reason.trim()) {
      toast("请填写失败或取消原因");
      return;
    }
    if (!attemptId) {
      toast("缺少当前任务的打印尝试记录，请重新加载任务后核对");
      return;
    }
    setBusy(true);
    if (
      await act(
        `/admin/print-attempts/${attemptId}/result`,
        { expectedVersion: order.version, outcome: result, reason },
        update(
          result === "COMPLETED"
            ? "COMPLETED"
            : result === "FAILED"
              ? "EXCEPTION"
              : "PENDING",
        ),
      )
    ) {
      setResult("");
      setReason("");
      toast("实际打印结果已记录");
    }
    setBusy(false);
  };
  let count = {
    sheets: file.photo.copies,
    pdfPages: file.photo.copies,
    blankBacks: 0,
  };
  try {
    if (segment)
      count = productionCount(
        expandRanges(segment.range, file.pages).length,
        segment.copies,
        segment.sides,
      );
  } catch {}
  return (
    <>
      <Link className="back-link" to={`${ADMIN}/print`}>
        <ArrowLeft size={16} />
        返回打印中心
      </Link>
      <PageHeading
        title={`打印任务 · ${order.pickupCode}`}
        description={file.name}
        action={<Badge status={status || "PENDING"} />}
      />
      <div className="print-view-layout">
        <Panel>
          <div className="panel-heading">
            <h2>最终打印文件</h2>
            {pdfUrl && (
              <a
                className="text-link"
                href={pdfUrl}
                target="_blank"
                rel="noreferrer"
              >
                打开 PDF
                <ArrowRight size={15} />
              </a>
            )}
          </div>
          <DocPreview file={file} pdfUrl={pdfUrl} />
          {!pdfUrl && (
            <Notice>
              {DEMO
                ? "此种子任务没有真实最终 PDF。可从用户端新建订单生成演示 PDF，再体验完整打印流程。"
                : "最终文件暂不可用，请重新获取受权资产。"}
            </Notice>
          )}
        </Panel>
        <aside>
          <Panel title="打印前核对">
            <div className="print-check-hero">
              <Printer size={26} />
              <span>打印窗口只设</span>
              <strong>1 份</strong>
            </div>
            <ul className="print-checklist">
              <li>
                <Check size={15} />
                份数已在 PDF 中展开
              </li>
              <li>
                <Check size={15} />
                {segment?.colorMode === "COLOR" ? "彩色" : "黑白"} /{" "}
                {segment ? printSidesLabel(segment) : "单面"}
              </li>
              <li>
                <Check size={15} />
                {file.kind === "PHOTO"
                  ? "校准实际照片尺寸"
                  : "A4 原尺寸，每面一页"}
              </li>
              <li>
                <Check size={15} />
                不缩印，不添加标签或广告
              </li>
            </ul>
            {file.segments.length > 1 && (
              <Field label="选择独立分段">
                <select
                  value={segmentId}
                  onChange={(e) => {
                    setSegmentId(e.target.value);
                    setResult("");
                    setReason("");
                  }}
                >
                  {file.segments.map((s, i) => (
                    <option key={s.id} value={s.id}>
                      任务 {i + 1} · {s.range} 页
                    </option>
                  ))}
                </select>
              </Field>
            )}
            <div className="task-counts">
              <div>
                <span>最终 PDF</span>
                <strong>{count.pdfPages} 页</strong>
              </div>
              <div>
                <span>实体用纸</span>
                <strong>{count.sheets} 张</strong>
              </div>
            </div>
            {count.blankBacks > 0 && (
              <p className="form-hint">
                每份末尾含必要空白背面，共 {count.blankBacks} 页。
              </p>
            )}
            <Button
              className="full"
              busy={busy}
              disabled={busy || !pdfUrl}
              onClick={attempt}
            >
              <Printer size={17} />
              {status === "COMPLETED" ? "填写原因后重打" : "记录尝试并打开打印"}
            </Button>
          </Panel>
          <Panel title="实际打印结果">
            <p className="muted">
              关闭打印窗口不表示已经出纸，请人工检查后确认。
            </p>
            <Button
              className="full"
              disabled={status !== "ATTEMPTED"}
              onClick={() => setResult("COMPLETED")}
            >
              <CheckCircle2 size={17} />
              确认实际打印完成
            </Button>
            <div className="button-row">
              <Button
                variant="secondary"
                disabled={status !== "ATTEMPTED"}
                onClick={() => setResult("FAILED")}
              >
                打印失败
              </Button>
              <Button
                variant="secondary"
                disabled={status !== "ATTEMPTED"}
                onClick={() => setResult("CANCELLED")}
              >
                已取消打印
              </Button>
            </div>
            <Button className="full text" disabled>
              指定纸张补打 · D-02 未批准
            </Button>
          </Panel>
        </aside>
      </div>
      {result && (
        <Modal
          title={
            result === "COMPLETED"
              ? "确认实际打印完成"
              : result === "REPRINT"
                ? "整任务重打"
                : "记录打印结果"
          }
          onClose={() => setResult("")}
        >
          {result === "COMPLETED" ? (
            <Notice>
              请确认实体纸张完整、颜色、单双面和翻转正确。此操作将记录为实际完成。
            </Notice>
          ) : (
            <Field label="原因（必填）">
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="记录重打、失败或取消的具体原因"
              />
            </Field>
          )}
          <Button
            busy={
              busy ||
              !!pendingActions[`/admin/print-tasks/${segmentId}/reprints`]
            }
            disabled={busy}
            onClick={
              result === "REPRINT"
                ? async () => {
                    if (!reason.trim()) {
                      toast("请填写重打原因");
                      return;
                    }
                    setResult("");
                    await act(
                      `/admin/print-tasks/${segmentId}/reprints`,
                      { expectedVersion: order.version, mode: "WHOLE", reason },
                      update("PENDING"),
                    );
                    toast("整任务重打已建立，请再次打印");
                  }
                : confirmResult
            }
          >
            确认记录
          </Button>
        </Modal>
      )}
    </>
  );
}
function DemoBindingCenter() {
  const { data, act, toast, pendingActions } = useStore();
  const [selected, setSelected] = useState<{ o: Order; f: PrintFile } | null>(
    null,
  );
  const [campus, setCampus] = useState("");
  const items = data.orders
    .filter(
      (o) =>
        o.status === "PENDING_PRINT" &&
        (!campus ||
          campusOfOrder(data.campuses, data.buildings, o)?.id === campus),
    )
    .flatMap((o) =>
      o.files.filter((f) => f.bindingType !== "NONE").map((f) => ({ o, f })),
    );
  return (
    <>
      <PageHeading
        title="装订中心"
        description="按原始文件分别整理，不同文件不合并成册。"
      />
      {!!(data.campuses?.length ?? 0) && (
        <Panel className="filter-panel">
          <div className="filter-row">
            <CampusFilter
              campuses={data.campuses}
              value={campus}
              onChange={setCampus}
            />
          </div>
        </Panel>
      )}
      <Notice>
        同文件的多个打印分段完成后，按原始页序整理，再独立装订。未完成全部打印时不能确认装订。
      </Notice>
      <div className="production-list">
        {items.map(({ o, f }) => (
          <Panel key={f.id}>
            <div className="print-task-top">
              <Link className="table-code" to={`${ADMIN}/orders/${o.id}`}>
                {o.pickupCode}
              </Link>
              <span>{o.nickname}</span>
              <CampusTag
                campus={campusOfOrder(data.campuses, data.buildings, o)}
              />
              <Badge
                status={
                  f.bindingStatus === "COMPLETED"
                    ? "COMPLETED"
                    : f.printStatus === "COMPLETED"
                      ? "READY"
                      : "BLOCKED"
                }
              />
            </div>
            <div className="binding-task-body">
              <BindingCard
                type={f.bindingType}
                label={f.bindingType === "CORNER" ? "左上角单钉" : "左侧双钉"}
                selected
                onClick={() => {}}
              />
              <div>
                <FileRow file={f} />
                <p>{taskDescription(f)}</p>
                <small className="muted">
                  各分段打印：
                  {f.printStatus === "COMPLETED" ? "全部完成" : "尚未完成"} ·
                  按原始页序整理
                </small>
              </div>
              <Button
                disabled={
                  f.printStatus !== "COMPLETED" ||
                  f.bindingStatus === "COMPLETED"
                }
                onClick={() => setSelected({ o, f })}
              >
                已整理且装订完成
              </Button>
            </div>
          </Panel>
        ))}
      </div>
      {!items.length && (
        <Panel>
          <Empty
            title="没有待装订文件"
            text="选择了装订服务的已付款文件，会在这里按原文件列出。无需装订的文件不会生成任务。"
          />
        </Panel>
      )}
      {selected && (
        <Modal title="确认整理与装订完成" onClose={() => setSelected(null)}>
          <p>请核对文件 {selected.f.name} 的原始页序、份数和装订位置。</p>
          <Button
            busy={
              !!pendingActions[`/admin/binding-tasks/${selected.f.id}/complete`]
            }
            onClick={async () => {
              if (
                await act(
                  `/admin/binding-tasks/${selected.f.id}/complete`,
                  { expectedVersion: selected.o.version },
                  (s) => ({
                    ...s,
                    orders: s.orders.map((o) =>
                      o.id === selected.o.id
                        ? {
                            ...o,
                            files: o.files.map((f) =>
                              f.id === selected.f.id
                                ? { ...f, bindingStatus: "COMPLETED" }
                                : f,
                            ),
                          }
                        : o,
                    ),
                  }),
                )
              ) {
                setSelected(null);
                toast("装订已确认完成");
              }
            }}
          >
            确认装订完成
          </Button>
        </Modal>
      )}
    </>
  );
}
function DemoPackingCenter() {
  const { data, act, toast, pendingActions } = useStore();
  const [checked, setChecked] = useState<string[]>([]);
  const [campus, setCampus] = useState("");
  const orders = data.orders.filter(
    (o) =>
      ["PENDING_PRINT", "READY_FOR_DELIVERY"].includes(o.status) &&
      (!campus ||
        campusOfOrder(data.campuses, data.buildings, o)?.id === campus),
  );
  return (
    <>
      <PageHeading
        title="装袋中心"
        description="一单一码，一个塑封袋。核对全部资料后，才能出发。"
      />
      {(data.campuses?.length ?? 0) > 0 && (
        <Panel className="filter-panel">
          <div className="filter-row">
            <CampusFilter
              campuses={data.campuses}
              value={campus}
              onChange={setCampus}
            />
          </div>
        </Panel>
      )}
      <Notice>
        打印完成、装订完成、装袋完成分别记录。装袋必须人工核对，不能自动跳过。
      </Notice>
      <div className="packing-grid">
        {orders.map((o) => {
          const ready = canPack(o.files);
          const allChecked = o.files.every((f) =>
            checked.includes(`${o.id}-${f.id}`),
          );
          return (
            <Panel key={o.id} className="packing-card">
              <div className="packing-code">
                <div>
                  <small>提取码</small>
                  <strong>{o.pickupCode}</strong>
                </div>
                <Badge
                  status={
                    o.packingStatus === "PACKED"
                      ? "PACKED"
                      : ready
                        ? "READY"
                        : "BLOCKED"
                  }
                />
              </div>
              <p className="packing-location">
                <MapPinIcon />
                {data.buildings.find((b) => b.id === o.locationId)?.name}
                <CampusTag
                  campus={campusOfOrder(data.campuses, data.buildings, o)}
                />
                <span>{o.nickname}</span>
              </p>
              <div className="packing-checks">
                {o.files.map((f) => (
                  <label key={f.id} className="packing-check">
                    <input
                      type="checkbox"
                      disabled={!ready || o.packingStatus === "PACKED"}
                      checked={
                        o.packingStatus === "PACKED" ||
                        checked.includes(`${o.id}-${f.id}`)
                      }
                      onChange={(e) =>
                        setChecked((s) =>
                          e.target.checked
                            ? [...s, `${o.id}-${f.id}`]
                            : s.filter((k) => k !== `${o.id}-${f.id}`),
                        )
                      }
                    />
                    <div>
                      <b>{f.name}</b>
                      <small>{taskDescription(f)}</small>
                      <span>
                        {f.printStatus === "COMPLETED"
                          ? "打印已完成"
                          : "等待打印"}{" "}
                        ·{" "}
                        {f.bindingType === "NONE"
                          ? "无需装订"
                          : f.bindingStatus === "COMPLETED"
                            ? "装订已完成"
                            : "等待装订"}
                      </span>
                    </div>
                  </label>
                ))}
              </div>
              {!ready && (
                <Notice>
                  生产尚未完成。
                  <Link to={`${ADMIN}/print?order=${o.id}`}>查看阻断任务</Link>
                </Notice>
              )}
              <Button
                className="full"
                busy={!!pendingActions[`/admin/orders/${o.id}/pack`]}
                disabled={!ready || !allChecked || o.packingStatus === "PACKED"}
                onClick={async () => {
                  if (
                    await act(
                      `/admin/orders/${o.id}/pack`,
                      {
                        expectedVersion: o.version,
                        checklistVersion: o.contentRevision,
                        checks: o.files.map((f) => ({
                          fileId: f.id,
                          confirmed: true,
                        })),
                      },
                      (s) => ({
                        ...s,
                        orders: s.orders.map((x) =>
                          x.id === o.id
                            ? {
                                ...x,
                                packingStatus: "PACKED",
                                status: "READY_FOR_DELIVERY",
                                version: x.version + 1,
                              }
                            : x,
                        ),
                      }),
                    )
                  )
                    toast("已确认资料齐全并装袋");
                }}
              >
                <Package size={17} />
                {o.packingStatus === "PACKED"
                  ? "资料已装袋"
                  : "确认资料齐全并已装袋"}
              </Button>
            </Panel>
          );
        })}
      </div>
      {!orders.length && (
        <Panel>
          <Empty title="没有待装袋订单" />
        </Panel>
      )}
    </>
  );
}
function MapPinIcon() {
  return <Package size={16} />;
}

// ---------------------------------------------------------------------------
// V1 server-backed production views
// ---------------------------------------------------------------------------

function useRemote<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(!!path);
  const [error, setError] = useState("");
  const [sequence, setSequence] = useState(0);
  useEffect(() => {
    if (!path) {
      setData(null);
      setLoading(false);
      return;
    }
    let active = true;
    setLoading(true);
    setError("");
    growthRequest<T>(path)
      .then((value) => {
        if (active) setData(value);
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
  return {
    data,
    loading,
    error,
    reload: () => setSequence((value) => value + 1),
  };
}

function ServerQueueState({
  loading,
  error,
  empty,
  reload,
}: {
  loading: boolean;
  error: string;
  empty: boolean;
  reload: () => void;
}) {
  if (loading)
    return (
      <Panel>
        <p className="muted">正在读取服务端任务…</p>
      </Panel>
    );
  if (error)
    return (
      <Notice type="error">
        <span role="alert">{error}</span>
        <Button variant="secondary" onClick={reload}>
          重新加载
        </Button>
      </Notice>
    );
  if (empty)
    return (
      <Panel>
        <Empty
          title="当前没有服务端任务"
          text="只有人工确认收款并生成当前有效任务的订单会出现在此处。"
        />
      </Panel>
    );
  return null;
}

function taskPaperLabel(task: PrintTaskView) {
  return `${task.copies} 份 · ${printSidesLabel(task)} · ${
    task.colorMode === "BW" ? "黑白" : "彩色"
  } · ${task.sheetCount} 张纸`;
}

export function PrintCenter() {
  // Invoke the demo component directly so the exported boundary keeps the same
  // hook order and remains inspectable by the existing render tests.
  return DEMO ? DemoPrintCenter() : <ServerPrintCenter />;
}

const orderPrintStates = [
  ["PENDING", "待打印", "blue"],
  ["PRINTING", "已开印，待完成", "amber"],
  ["COMPLETED", "打印已完成", "green"],
] as const;

function ServerPrintCenter() {
  const { data, toast } = useStore();
  const [params] = useSearchParams();
  const selectedOrderId = params.get("order");
  const [opened, setOpened] = useState(
    () => new Set(selectedOrderId ? [selectedOrderId] : []),
  );
  const [currentOrderId, setCurrentOrderId] = useState(selectedOrderId);
  const [status, setStatus] = useState("");
  const [kind, setKind] = useState("");
  const [search, setSearch] = useState("");
  const [campus, setCampus] = useState("");
  const [retrying, setRetrying] = useState("");
  const queue = useRemote<PrintQueueView>("/admin/print-tasks");
  // The order detail links here with an order ID. Closed orders' identification
  // records are fetched on demand and remain outside the live print queue.
  const historicalOrderId =
    selectedOrderId &&
    queue.data &&
    !queue.data.tasks.some((task) => task.orderId === selectedOrderId)
      ? selectedOrderId
      : null;
  const historicalQueue = useRemote<PrintQueueView>(
    historicalOrderId
      ? `/admin/print-tasks?orderId=${encodeURIComponent(historicalOrderId)}`
      : null,
  );
  const pendingJobs = queue.data?.identificationJobs;
  const refreshQueue = queue.reload;
  useEffect(() => {
    if (
      queue.loading ||
      queue.error ||
      !Object.values(pendingJobs ?? {}).some(
        (value) => value === "QUEUED" || value === "PROCESSING",
      )
    )
      return;
    const timer = window.setTimeout(refreshQueue, 2200);
    return () => window.clearTimeout(timer);
  }, [pendingJobs, queue.loading, queue.error, refreshQueue]);

  const orders = useMemo(() => {
    const tasksByOrder = new Map<string, PrintTaskView[]>();
    const orderById = new Map(data.orders.map((order) => [order.id, order]));
    for (const task of [
      ...(historicalQueue.data?.tasks.filter(
        (task) => task.orderId === historicalOrderId,
      ) ?? []),
      ...(queue.data?.tasks ?? []),
    ]) {
      if (task.orderStatus === "PENDING_CONFIRMATION") continue;
      const tasks = tasksByOrder.get(task.orderId);
      if (tasks) tasks.push(task);
      else tasksByOrder.set(task.orderId, [task]);
    }
    return Array.from(tasksByOrder, ([id, tasks]) => {
      const order = orderById.get(id);
      const taskCampus = campusOfOrder(data.campuses, data.buildings, order);
      const sorted = [...tasks].sort(
        (a, b) =>
          Number(b.kind === "IDENTIFICATION") -
          Number(a.kind === "IDENTIFICATION"),
      );
      const job = sorted.some((task) => task.kind === "IDENTIFICATION")
        ? undefined
        : pendingJobs?.[id];
      const documentTasks = sorted.filter(
        (task) => task.kind !== "IDENTIFICATION",
      );
      const complete = documentTasks.filter(
        (task) => task.status === "COMPLETED",
      ).length;
      const total = documentTasks.length;
      const attempted = documentTasks.some(
        (task) => task.status === "ATTEMPTED" || task.openAttemptId,
      );
      const exception = documentTasks.some(
        (task) => task.status === "EXCEPTION",
      );
      const progress =
        complete === total
          ? "COMPLETED"
          : sorted[0].actualStartedAt
            ? "PRINTING"
            : "PENDING";
      return {
        id,
        historical: id === historicalOrderId,
        tasks: sorted,
        documentTasks,
        code: sorted[0].pickupCode,
        campus: taskCampus,
        locked: sorted[0].refundLocked,
        job,
        complete,
        total,
        attempted,
        exception,
        progress,
      };
    }).filter((order) => !campus || order.campus?.id === campus);
  }, [
    campus,
    data.buildings,
    data.campuses,
    data.orders,
    historicalOrderId,
    historicalQueue.data?.tasks,
    pendingJobs,
    queue.data?.tasks,
  ]);

  const query = search.trim().toLowerCase();
  const visibleOrders = orders.filter(
    (order) =>
      (!status ||
        (status === "EXCEPTION"
          ? order.exception
          : order.progress === status)) &&
      (!kind ||
        order.tasks.some((task) => task.kind === kind) ||
        (kind === "IDENTIFICATION" && !!order.job)) &&
      (!query ||
        order.code.toLowerCase().includes(query) ||
        order.tasks.some((task) =>
          task.fileName.toLowerCase().includes(query),
        ) ||
        (!!order.job && "订单识别页.pdf".includes(query))),
  );
  const nextOrders = [
    ...visibleOrders.filter((order) => order.id === currentOrderId),
    ...visibleOrders.filter((order) => order.id !== currentOrderId),
  ];
  const next = nextOrders
    .filter((order) => !order.locked && !order.historical)
    .flatMap((order) => order.documentTasks)
    .find((task) =>
      ["PENDING", "ATTEMPTED", "EXCEPTION"].includes(task.status),
    );

  async function retryIdentification(orderId: string) {
    setRetrying(orderId);
    try {
      await growthRequest(
        `/admin/orders/${encodeURIComponent(orderId)}/identification/retry`,
        {
          method: "POST",
          body: {},
        },
      );
      refreshQueue();
    } catch (error) {
      toast(errorText(error));
    } finally {
      setRetrying("");
    }
  }

  return (
    <>
      <PageHeading
        title="打印中心"
        description="按订单逐项打印，实际出纸后确认结果。"
        action={
          next && (
            <Link className="btn" to={`${ADMIN}/print/${next.id}`}>
              <ArrowRight size={16} />
              下一项 · {next.pickupCode}
            </Link>
          )
        }
      />
      <div className="production-stats">
        {[...orderPrintStates, ["EXCEPTION", "打印异常", "red"] as const].map(
          ([value, label, color]) => (
            <div key={value}>
              <i className={`dot ${color}`} />
              <span>{label}</span>
              <strong>
                {
                  orders.filter(
                    (order) =>
                      !order.historical &&
                      (value === "EXCEPTION"
                        ? order.exception
                        : order.progress === value),
                  ).length
                }
              </strong>
            </div>
          ),
        )}
      </div>
      <Panel className="filter-panel">
        <div className="filter-row">
          <div className="search-input grow">
            <Search size={17} />
            <input
              aria-label="搜索提取码或文件"
              placeholder="搜索提取码或文件"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
          <CampusFilter
            campuses={data.campuses}
            value={campus}
            onChange={setCampus}
          />
          <select
            aria-label="订单打印状态"
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="">全部状态</option>
            {orderPrintStates.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
            <option value="EXCEPTION">打印异常</option>
          </select>
          <select
            aria-label="文件类型"
            value={kind}
            onChange={(event) => setKind(event.target.value)}
          >
            <option value="">全部类型</option>
            <option value="DOCUMENT">文档</option>
            <option value="PHOTO">照片</option>
            <option value="IDENTIFICATION">订单识别页</option>
          </select>
        </div>
      </Panel>
      <Notice>
        机器开始打印首份正文时，请回到任务确认开印。打印窗口统一选 1 份，单双面和翻转按任务设置。
      </Notice>
      <ServerQueueState
        loading={
          queue.loading ||
          (historicalOrderId !== null && historicalQueue.loading)
        }
        error={queue.error || historicalQueue.error}
        empty={
          !queue.loading &&
          !historicalQueue.loading &&
          !queue.error &&
          !historicalQueue.error &&
          !visibleOrders.length
        }
        reload={historicalQueue.error ? historicalQueue.reload : refreshQueue}
      />
      <div className="production-list print-orders">
        {visibleOrders.map((order) => {
          const expanded = opened.has(order.id);
          const [, label, color] = orderPrintStates.find(
            ([value]) => value === order.progress,
          )!;
          return (
            <Panel key={order.id} className="print-order-card">
              <button
                type="button"
                className="print-order-toggle"
                aria-expanded={expanded}
                onClick={() => {
                  setOpened((current) => {
                    const next = new Set(current);
                    if (next.has(order.id)) next.delete(order.id);
                    else next.add(order.id);
                    return next;
                  });
                  setCurrentOrderId(expanded ? null : order.id);
                }}
              >
                <span className="print-order-heading">
                  <ChevronRight
                    size={18}
                    className="print-order-chevron"
                    aria-hidden="true"
                  />
                  <strong>{order.code}</strong>
                  <CampusTag campus={order.campus} />
                </span>
                <span className="print-order-progress">
                  <span
                    className={`badge ${order.historical ? "gray" : color}`}
                  >
                    {order.historical ? "识别页记录" : label}
                  </span>
                  {order.total > 0 && (
                    <span className="muted">
                      {order.complete}/{order.total} 项完成
                    </span>
                  )}
                  {order.attempted && (
                    <span className="soft-tag">待结果确认</span>
                  )}
                  {order.exception && (
                    <span className="badge red">打印异常</span>
                  )}
                  {order.locked && (
                    <span className="soft-tag">退款审核锁定</span>
                  )}
                </span>
              </button>
              {expanded && (
                <div className="print-order-tasks">
                  {order.job && (
                    <div className="print-identification-pending">
                      <strong>订单识别页.pdf</strong>
                      <span className="muted">可选</span>
                      {order.job === "FAILED" ? (
                        <>
                          <span className="badge red">生成失败</span>
                          <Button
                            variant="secondary"
                            busy={retrying === order.id}
                            onClick={() => retryIdentification(order.id)}
                          >
                            重新生成
                          </Button>
                        </>
                      ) : (
                        <span className="muted" role="status">
                          正在生成识别页…
                        </span>
                      )}
                    </div>
                  )}
                  {order.tasks.map((task) => (
                    <section key={task.id} className="print-task-card">
                      <div className="print-task-content">
                        <div className="print-task-top">
                          <strong className="production-file-name">
                            {task.fileName?.trim() || "文件名未记录"}
                          </strong>
                        </div>
                        <div className="print-task-details">
                          <p>
                            {task.pageRange
                              ? `第 ${task.pageRange} 页`
                              : "整文件"}{" "}
                            · {taskPaperLabel(task)}
                            {task.kind === "IDENTIFICATION" && " · 可选"}
                          </p>
                          {task.bindingType !== "NONE" && <span>需装订</span>}
                          {task.openAttemptId && (
                            <span>请先核实本次打印结果</span>
                          )}
                        </div>
                      </div>
                      <div className="task-card-actions">
                        <Badge status={task.status} />
                        <Link
                          className="btn secondary"
                          to={`${ADMIN}/print/${task.id}`}
                        >
                          打开任务 <ArrowRight size={16} />
                        </Link>
                      </div>
                    </section>
                  ))}
                  <Link
                    className="text-link print-order-detail"
                    to={`${ADMIN}/orders/${order.id}`}
                  >
                    查看订单
                  </Link>
                </div>
              )}
            </Panel>
          );
        })}
      </div>
    </>
  );
}

export function PrintTask() {
  // Invoke the demo component directly so the exported boundary keeps the same
  // hook order and remains inspectable by the existing render tests.
  return DEMO ? DemoPrintTask() : <ServerPrintTask />;
}

function ServerPrintTask() {
  const { id } = useParams();
  const { data: storeData, refresh, toast } = useStore();
  const taskState = useRemote<PrintTaskView>(
    id ? `/admin/print-tasks/${encodeURIComponent(id)}` : null,
  );
  const task = taskState.data;
  const order = task
    ? storeData.orders.find((row) => row.id === task.orderId)
    : undefined;
  // Production endpoints use the order optimistic version as the task lock.
  const orderVersion = task?.orderVersion ?? 0;
  const refundLocked =
    order && order.version >= orderVersion
      ? (order.refundLocked ?? task?.refundLocked)
      : task?.refundLocked;
  const [startReason, setStartReason] = useState("");
  const [startDialog, setStartDialog] = useState(false);
  const [startIssue, setStartIssue] = useState("");
  const [reason, setReason] = useState("");
  const [outcome, setOutcome] = useState<"COMPLETED" | "FAILED" | "CANCELLED">(
    "COMPLETED",
  );
  const [attemptId, setAttemptId] = useState("");
  const [reprintArtifactId, setReprintArtifactId] = useState("");
  const [reprintMode, setReprintMode] = useState<ReproMode>("WHOLE");
  const [sheetIds, setSheetIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [issue, setIssue] = useState("");
  useEffect(() => {
    setAttemptId(task?.openAttemptId || "");
  }, [task?.id, task?.openAttemptId]);
  useEffect(() => {
    setReprintArtifactId("");
    setSheetIds([]);
    setReason("");
    setStartDialog(false);
    setStartIssue("");
    setStartReason("");
  }, [task?.id]);
  if (!id) return <Empty title="缺少打印任务" />;
  if (taskState.loading)
    return (
      <Panel>
        <p className="muted">正在读取服务端打印任务…</p>
      </Panel>
    );
  if (taskState.error)
    return (
      <Notice type="error">
        <span role="alert">{taskState.error}</span>
        <Button variant="secondary" onClick={taskState.reload}>
          重新加载
        </Button>
      </Notice>
    );
  if (!task)
    return (
      <Empty
        title="打印任务不存在或已失效"
        text="旧内容版本的任务不能继续打印。"
      />
    );
  const fileName = task.fileName?.trim() || "文件名未记录";
  const orderFile = order?.files.find((item) => item.id === task.fileId);
  const pdfUrl = printAssetUrl(reprintArtifactId || task.artifactId);
  const completed = task.status === "COMPLETED";
  const identification = task.kind === "IDENTIFICATION";
  const needsStart = !identification && !task.actualStartedAt;
  const canConfirmStart =
    needsStart && !!task.openAttemptId && task.orderStatus === "PENDING_PRINT";
  const identificationCanPrint =
    !identification ||
    ["PENDING_PRINT", "READY_FOR_DELIVERY"].includes(task.orderStatus);
  const reportIssue = (cause: unknown) => {
    setIssue(errorText(cause));
    if (cause instanceof ApiError && cause.code === "VERSION_CONFLICT")
      taskState.reload();
  };
  const run = async (work: () => Promise<unknown>, success: string) => {
    setBusy(true);
    setIssue("");
    try {
      await work();
      await taskState.reload();
      await refresh();
      toast(success);
      return true;
    } catch (cause) {
      reportIssue(cause);
      return false;
    } finally {
      setBusy(false);
    }
  };
  const startAttempt = async () => {
    const win = window.open("about:blank", "_blank");
    if (win) win.opener = null;
    setBusy(true);
    setIssue("");
    try {
      const attempt = await growthRequest<{ attemptId: string }>(
        `/admin/print-tasks/${encodeURIComponent(task.id)}/attempts`,
        {
          method: "POST",
          body: { expectedVersion: orderVersion, artifactId: task.artifactId },
        },
      );
      setAttemptId(attempt.attemptId);
      if (win) win.location.href = pdfUrl;
      else toast("打印窗口被拦截，请点击“打开最终 PDF”后手动打印并返回确认");
      await taskState.reload();
      await refresh();
    } catch (cause) {
      win?.close();
      reportIssue(cause);
    } finally {
      setBusy(false);
    }
  };
  const confirmStart = async () => {
    setBusy(true);
    setStartIssue("");
    setIssue("");
    try {
      const latest = await growthRequest<PrintTaskView>(
        `/admin/print-tasks/${encodeURIComponent(task.id)}`,
      );
      if (latest.actualStartedAt) {
        toast("此订单已确认开印");
      } else {
        if (!latest.openAttemptId || latest.openAttemptId !== task.openAttemptId)
          throw new ApiError(
            "OPEN_DOCUMENT_ATTEMPT_REQUIRED",
            "当前打印尝试已变化，请重新核对",
            409,
          );
        await growthRequest(
          `/admin/orders/${encodeURIComponent(task.orderId)}/start-production`,
          {
            method: "POST",
            body: actualStartPayload(
              latest.orderVersion,
              startReason,
              latest.openAttemptId,
            ),
          },
        );
        toast("已确认开印");
      }
      setStartDialog(false);
      taskState.reload();
      void refresh();
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) {
        setStartDialog(false);
        setIssue(errorText(cause));
        taskState.reload();
      } else {
        setStartIssue(errorText(cause));
      }
    } finally {
      setBusy(false);
    }
  };
  const confirmResult = () =>
    run(
      () =>
        growthRequest(
          `/admin/print-attempts/${encodeURIComponent(attemptId)}/result`,
          {
            method: "POST",
            body: {
              expectedVersion: orderVersion,
              outcome,
              reason: outcome === "COMPLETED" ? "" : reason,
            },
          },
        ),
      "实际打印结果已记录",
    );
  const submitReprint = async () => {
    const win = window.open("about:blank", "_blank");
    if (win) win.opener = null;
    setBusy(true);
    setIssue("");
    try {
      const result = await growthRequest<{
        artifactId: string;
        attemptId: string;
      }>(`/admin/print-tasks/${encodeURIComponent(task.id)}/reprints`, {
        method: "POST",
        body: reprintPayload(
          task,
          identification ? "WHOLE" : reprintMode,
          sheetIds,
          reason,
        ),
      });
      setReprintArtifactId(result.artifactId);
      setAttemptId(result.attemptId);
      const url = printAssetUrl(result.artifactId);
      if (win) win.location.href = url;
      else toast("补打窗口被拦截，请点击上方补打 PDF 手动打印");
      await taskState.reload();
      await refresh();
      toast("补打已建立，请打印补打 PDF 后确认结果");
    } catch (cause) {
      win?.close();
      reportIssue(cause);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <Link
        className="back-link"
        to={`${ADMIN}/print?order=${encodeURIComponent(task.orderId)}`}
      >
        <ArrowLeft size={16} />
        返回打印中心
      </Link>
      <div className="production-file-name">
        <PageHeading
          title={`打印任务 · ${task.pickupCode}`}
          description={fileName}
          action={<Badge status={task.status} />}
        />
      </div>
      {refundLocked && (
        <Notice type="error">
          退款审核期间暂停新的打印和补打。已有尝试仍可核实开印和打印结果。
        </Notice>
      )}
      {issue && (
        <Notice type="error">
          <span role="alert">{issue}</span>
        </Notice>
      )}
      <div className="print-view-layout print-task-full-width">
        <Panel title="服务端任务">
          <div className="print-settings" aria-label="打印设置">
            {[
              `${task.copies} 份`,
              printSidesLabel(task),
              task.colorMode === "BW" ? "黑白" : "彩色",
              `${task.sheetCount} 张纸`,
              `打印页码 ${task.pageRange || "整文件"}`,
            ].map((value) => (
              <span key={value}>{value}</span>
            ))}
          </div>
          <details className="print-task-metadata">
            <summary>任务技术信息</summary>
            <dl className="courier-facts print-task-facts">
              <div>
                <dt>任务编号</dt>
                <dd className="numeric">{task.id}</dd>
              </div>
              <div>
                <dt>订单版本</dt>
                <dd className="numeric">{orderVersion}</dd>
              </div>
              <div>
                <dt>产物编号</dt>
                <dd className="numeric">{task.artifactId}</dd>
              </div>
              <div>
                <dt>SHA-256</dt>
                <dd className="numeric">{task.artifactSha256}</dd>
              </div>
            </dl>
          </details>
          <DocPreview
            file={
              orderFile
                ? { ...orderFile, name: fileName }
                : ({
                    id: task.fileId,
                    assetId: task.artifactId,
                    name: fileName,
                    kind: "DOCUMENT",
                    pages: task.pages,
                  } as PrintFile)
            }
            pdfUrl={pdfUrl}
          />
          <div className="print-pdf-tools">
            <div className="button-row print-pdf-controls">
              <a
                className="btn secondary"
                href={pdfUrl}
                target="_blank"
                rel="noreferrer"
              >
                {reprintArtifactId ? "打开补打 PDF" : "打开最终 PDF"}
              </a>
              {!task.openAttemptId && !attemptId && (
                <Button
                  disabled={completed || busy || refundLocked || !identificationCanPrint}
                  onClick={startAttempt}
                >
                  记录打印尝试并打开 PDF
                </Button>
              )}
              {canConfirmStart && (
                <Button
                  disabled={busy}
                  onClick={() => {
                    setStartIssue("");
                    setStartDialog(true);
                  }}
                >
                  确认已开印
                </Button>
              )}
            </div>
            {!identification && task.actualStartedAt && (
              <p className="print-pdf-hint">
                <strong>已开印</strong>{" "}
                <time dateTime={task.actualStartedAt}>
                  {new Date(task.actualStartedAt).toLocaleString("zh-CN", {
                    timeZone: "Asia/Shanghai",
                  })}
                </time>
              </p>
            )}
            {!completed && identificationCanPrint && (
              <p className="muted print-pdf-hint">
                {canConfirmStart
                  ? "确认机器已开始打印正文后，点击「确认已开印」。打印结束后回到本页确认结果。"
                  : task.openAttemptId
                    ? "打印结束后，请在下方确认本次结果。"
                    : "打印前请先记录尝试，打印结束后回到本页确认结果。"}
              </p>
            )}
          </div>
        </Panel>
      </div>
      <Modal
        title="确认已开印"
        open={startDialog && canConfirmStart}
        onClose={() => {
          if (!busy) setStartDialog(false);
        }}
      >
        <p className="print-start-confirmation">
          请确认机器已开始打印本订单的正文。确认后，用户端将显示「打印中」，用户原因退款将按已开印处理。
        </p>
        <Field label="开印说明（选填）">
          <input
            value={startReason}
            maxLength={1000}
            onChange={(event) => setStartReason(event.target.value)}
          />
        </Field>
        {startIssue && (
          <Notice type="error">
            <span role="alert">{startIssue}</span>
          </Notice>
        )}
        <footer className="button-row print-start-actions">
          <Button
            variant="secondary"
            disabled={busy}
            onClick={() => setStartDialog(false)}
          >
            返回
          </Button>
          <Button busy={busy} disabled={busy} onClick={confirmStart}>
            确认开印
          </Button>
        </footer>
      </Modal>
      {attemptId && !completed && (
        <Panel className="print-result-panel" title="确认本次打印结果">
          <Notice>
            {identification
              ? "请核对提取码和页数。实际出纸后确认本次结果。"
              : needsStart
                ? "如果机器已开始打印，即使中途停止，也请先确认开印，再记录本次结果。"
                : "请核对实体纸张的颜色、单双面、翻转与页序。确认完成后才能进入装订/装袋。"}
          </Notice>
          <div className="button-row">
            {(["COMPLETED", "FAILED", "CANCELLED"] as const).map((value) => (
              <Button
                key={value}
                variant={outcome === value ? "" : "secondary"}
                onClick={() => setOutcome(value)}
              >
                {value === "COMPLETED"
                  ? "实际完成"
                  : value === "FAILED"
                    ? "失败"
                    : "取消"}
              </Button>
            ))}
          </div>
          {outcome !== "COMPLETED" && (
            <Field label="失败/取消原因（必填）">
              <textarea
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
            </Field>
          )}
          <Button
            busy={busy}
            disabled={busy || (needsStart && outcome === "COMPLETED")}
            onClick={confirmResult}
          >
            确认记录当前尝试
          </Button>
        </Panel>
      )}
      {identification && task.generatedAt && (
        <Panel title="识别页记录">
          <p className="muted">
            生成于{" "}
            {new Date(task.generatedAt).toLocaleString("zh-CN", {
              timeZone: "Asia/Shanghai",
            })}
          </p>
          {task.attempts?.length ? (
            <ul className="identification-attempts">
              {task.attempts.map((attempt) => (
                <li key={attempt.attemptId}>
                  <span>
                    {new Date(attempt.createdAt).toLocaleString("zh-CN", {
                      timeZone: "Asia/Shanghai",
                    })}
                  </span>
                  <Badge
                    status={
                      attempt.outcome === "FAILED"
                        ? "EXCEPTION"
                        : attempt.outcome || "ATTEMPTED"
                    }
                  />
                  {attempt.reason && (
                    <span className="identification-attempt-reason">
                      {attempt.reason}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">暂无打印记录</p>
          )}
        </Panel>
      )}
      {identificationCanPrint && (
        <Panel className="reprint-panel" title="实体纸张与补打">
          <p className="muted">
            {identification
              ? "按原识别页整份补打，打印窗口选 1 份。"
              : "指定纸张补打按服务端页纸映射选择完整实体纸张；双面会自动包含正反面与空白背面，整任务补打不增加费用。"}
          </p>
          {!identification && (
            <div className="button-row">
              {(["WHOLE", "SHEETS"] as const).map((mode) => (
                <Button
                  key={mode}
                  variant={reprintMode === mode ? "" : "secondary"}
                  onClick={() => setReprintMode(mode)}
                >
                  {mode === "WHOLE" ? "整任务重打" : "指定实体纸张"}
                </Button>
              ))}
            </div>
          )}
          {!identification && reprintMode === "SHEETS" && (
            <div className="sheet-selector">
              {task.sheetCatalog.map((sheet) => (
                <label className="checkbox-label" key={sheet.sheetId}>
                  <input
                    type="checkbox"
                    checked={sheetIds.includes(sheet.sheetId)}
                    onChange={(event) =>
                      setSheetIds((current) =>
                        event.target.checked
                          ? [...current, sheet.sheetId]
                          : current.filter((value) => value !== sheet.sheetId),
                      )
                    }
                  />
                  第 {sheet.copyIndex} 份 · 第 {sheet.sheet} 张 · 正面{" "}
                  {sheet.frontPage ?? "—"} / 背面{" "}
                  {sheet.backPage ?? (sheet.blankBack ? "空白" : "—")}
                </label>
              ))}
            </div>
          )}
          <Field label="补打原因（必填）">
            <textarea
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </Field>
          <Button
            busy={busy}
            disabled={busy || refundLocked || !!task.openAttemptId}
            onClick={submitReprint}
          >
            建立补打并记录尝试
          </Button>
        </Panel>
      )}
    </>
  );
}

export function BindingCenter() {
  // Invoke the demo component directly so the exported boundary keeps the same
  // hook order and remains inspectable by the existing render tests.
  return DEMO ? DemoBindingCenter() : <ServerBindingCenter />;
}

const bindingPrintLabels: Record<string, string> = {
  PENDING: "等待打印",
  ATTEMPTED: "待确认打印结果",
  COMPLETED: "打印已完成",
  EXCEPTION: "打印异常",
  VOIDED: "打印任务已作废",
};

function ServerBindingCenter() {
  const { data: storeData, refresh } = useStore();
  const queue = useRemote<{ tasks: BindingTaskView[] }>("/admin/binding-tasks");
  const [busyId, setBusyId] = useState("");
  const [campus, setCampus] = useState("");
  const [issue, setIssue] = useState("");
  const complete = async (task: BindingTaskView) => {
    setBusyId(task.id);
    setIssue("");
    try {
      const order = await growthRequest<{ version: number }>(
        `/admin/orders/${encodeURIComponent(task.orderId)}`,
      );
      await growthRequest(`/admin/binding-tasks/${encodeURIComponent(task.id)}/complete`, {
        method: "POST",
        body: { expectedVersion: order.version },
      });
      await refresh();
      queue.reload();
    } catch (cause) {
      setIssue(errorText(cause));
    } finally {
      setBusyId("");
    }
  };
  return (
    <>
      <PageHeading
        title="装订中心"
        description="按完整册装订，不同文件不合册；全部有效打印完成后才能确认。"
      />
      {!!(storeData.campuses?.length ?? 0) && (
        <Panel className="filter-panel">
          <div className="filter-row">
            <CampusFilter
              campuses={storeData.campuses}
              value={campus}
              onChange={setCampus}
            />
          </div>
        </Panel>
      )}
      {issue && (
        <Notice type="error">
          <span role="alert">{issue}</span>
        </Notice>
      )}
      <ServerQueueState
        loading={queue.loading}
        error={queue.error}
        empty={
          !queue.loading &&
          !queue.error &&
          !(queue.data?.tasks || []).some((task) => {
            if (!campus) return true;
            const order = storeData.orders.find(
              (row) => row.id === task.orderId,
            );
            return (
              campusOfOrder(storeData.campuses, storeData.buildings, order)
                ?.id === campus
            );
          })
        }
        reload={queue.reload}
      />
      <div className="production-list">
        {queue.data?.tasks
          .filter((task) => {
            if (!campus) return true;
            const order = storeData.orders.find(
              (row) => row.id === task.orderId,
            );
            return (
              campusOfOrder(storeData.campuses, storeData.buildings, order)
                ?.id === campus
            );
          })
          .map((task) => {
            const order = storeData.orders.find(
              (row) => row.id === task.orderId,
            );
            const taskCampus = campusOfOrder(
              storeData.campuses,
              storeData.buildings,
              order,
            );
            return (
          <Panel key={task.id}>
            <div className="print-task-top">
              <Link className="table-code" to={`${ADMIN}/orders/${task.orderId}`}>
                {task.pickupCode}
              </Link>
              <strong className="production-file-name">
                {task.fileName?.trim() || "文件名未记录"}
              </strong>
              <CampusTag campus={taskCampus} />
              <Badge
                status={
                  task.status === "COMPLETED"
                    ? "COMPLETED"
                    : task.printStatus === "COMPLETED"
                      ? "READY"
                      : "BLOCKED"
                }
              />
            </div>
            <div className="binding-task-body">
              <BindingCard
                type={task.bindingType}
                label={task.bindingType === "CORNER" ? "左上角单钉" : "左侧双钉"}
                selected
                onClick={() => {}}
              />
              <div>
                <p>
                  {bindingPrintLabels[task.printStatus] || "打印尚未完成"}
                </p>
                <small className="muted">
                  同文件各分段份数必须一致；超过每册张数上限时由服务端拒绝。
                </small>
              </div>
              <Button
                busy={busyId === task.id}
                disabled={task.printStatus !== "COMPLETED" || task.status === "COMPLETED"}
                onClick={() => complete(task)}
              >
                已整理且装订完成
              </Button>
            </div>
          </Panel>
            );
          })}
      </div>
    </>
  );
}

export function PackingCenter() {
  // Invoke the demo component directly so the exported boundary keeps the same
  // hook order and remains inspectable by the existing render tests.
  return DEMO ? DemoPackingCenter() : <ServerPackingCenter />;
}

function ServerPackingCenter() {
  const { refresh, data } = useStore();
  const queue = useRemote<{ tasks: PackingTaskView[] }>("/admin/packing-tasks");
  const [campus, setCampus] = useState("");
  const [checked, setChecked] = useState<Record<string, string[]>>({});
  const [busyId, setBusyId] = useState("");
  const [issue, setIssue] = useState("");
  const pack = async (task: PackingTaskView) => {
    setBusyId(task.id);
    setIssue("");
    try {
      const order = await growthRequest<{ version: number }>(
        `/admin/orders/${encodeURIComponent(task.orderId)}`,
      );
      await growthRequest(`/admin/orders/${encodeURIComponent(task.orderId)}/pack`, {
        method: "POST",
        body: packPayload(task, order.version, checked[task.id] || []),
      });
      await refresh();
      queue.reload();
    } catch (cause) {
      setIssue(errorText(cause));
    } finally {
      setBusyId("");
    }
  };
  const visibleTasks = (queue.data?.tasks ?? []).filter((task) => {
    if (!campus) return true;
    const order = data.orders.find((item) => item.id === task.orderId);
    return campusOfOrder(data.campuses, data.buildings, order)?.id === campus;
  });
  return (
    <>
      <PageHeading
        title="装袋中心"
        description="服务端清单逐项核对；必须全部打印与装订完成，人工确认后才能出发。"
      />
      {issue && (
        <Notice type="error">
          <span role="alert">{issue}</span>
        </Notice>
      )}
      {(data.campuses?.length ?? 0) > 0 && (
        <Panel className="filter-panel">
          <div className="filter-row">
            <CampusFilter
              campuses={data.campuses}
              value={campus}
              onChange={setCampus}
            />
          </div>
        </Panel>
      )}
      <ServerQueueState
        loading={queue.loading}
        error={queue.error}
        empty={!queue.loading && !queue.error && !visibleTasks.length}
        reload={queue.reload}
      />
      <div className="packing-grid">
        {visibleTasks.map((task) => {
          const selected = checked[task.id] || [];
          const allChecked = task.checklist.items.every((item) =>
            selected.includes(item.fileId),
          );
          const blocked = task.checklist.blockers.length > 0;
          return (
            <Panel key={task.id} className="packing-card">
              <div className="packing-code">
                <div>
                  <small>提取码</small>
                  <strong>{task.pickupCode}</strong>
                </div>
                <Badge status={task.status === "PACKED" ? "PACKED" : blocked ? "BLOCKED" : "READY"} />
              </div>
              <p className="packing-location">
                <CampusTag
                  campus={campusOfOrder(
                    data.campuses,
                    data.buildings,
                    data.orders.find((item) => item.id === task.orderId),
                  )}
                />
                <span>
                  {data.buildings.find(
                    (building) =>
                      building.id ===
                      data.orders.find((item) => item.id === task.orderId)
                        ?.locationId,
                  )?.name || "未分校区"}
                </span>
              </p>
              <p className="muted">
                共 {task.checklist.items.length} 个文件
              </p>
              <div className="packing-checks">
                {task.checklist.items.map((item, index) => (
                  <label className="packing-check" key={item.fileId}>
                    <input
                      type="checkbox"
                      disabled={task.status === "PACKED" || blocked}
                      checked={task.status === "PACKED" || selected.includes(item.fileId)}
                      onChange={(event) =>
                        setChecked((current) => {
                          const ids = current[task.id] || [];
                          return {
                            ...current,
                            [task.id]: event.target.checked
                              ? [...ids, item.fileId]
                              : ids.filter((value) => value !== item.fileId),
                          };
                        })
                      }
                    />
                    <div>
                      <b>{item.name?.trim() || `文件 ${index + 1}`}</b>
                      <small>
                        {item.printStatus === "COMPLETED" ? "打印已完成" : "等待打印"} ·{" "}
                        {item.bindingStatus === "NOT_REQUIRED"
                          ? "无需装订"
                          : item.bindingStatus === "COMPLETED"
                            ? "装订已完成"
                            : "等待装订"}
                      </small>
                    </div>
                  </label>
                ))}
              </div>
              {blocked && (
                <Notice type="warning">
                  {task.checklist.blockers.map((blocker) => (
                    <p key={`${blocker.code}-${blocker.taskId || blocker.fileId || ""}`}>
                      {blocker.message}
                    </p>
                  ))}
                </Notice>
              )}
              <Button
                className="full"
                busy={busyId === task.id}
                disabled={task.status === "PACKED" || blocked || !allChecked}
                onClick={() => pack(task)}
              >
                确认资料齐全并已装袋
              </Button>
            </Panel>
          );
        })}
      </div>
    </>
  );
}
