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
function taskDescription(f: PrintFile) {
  return f.kind === "PHOTO"
    ? `5 寸照片 · ${f.photo.copies} 张`
    : f.segments
        .map(
          (s) =>
            `第 ${s.range} 页 · ${s.colorMode === "BW" ? "黑白" : "彩色"} · ${s.sides === "DUPLEX" ? "双面" : "单面"} · ${s.copies} 份`,
        )
        .join(" / ");
}
function DemoPrintCenter() {
  const { data } = useStore();
  const [params, setParams] = useSearchParams();
  const view = params.get("view") || "order";
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const tasks = data.orders
    .filter(
      (o) =>
        ["PENDING_PRINT", "READY_FOR_DELIVERY"].includes(o.status) &&
        (!params.get("order") || o.id === params.get("order")),
    )
    .flatMap((o) => o.files.map((f) => ({ o, f })))
    .filter(
      ({ o, f }) =>
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
                {segment?.sides === "DUPLEX" ? "双面" : "单面"}
              </li>
              <li>
                <Check size={15} />
                {segment?.flip === "SHORT_EDGE"
                  ? "短边翻转"
                  : "长边翻转（双面时）"}
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
  const items = data.orders
    .filter((o) => o.status === "PENDING_PRINT")
    .flatMap((o) =>
      o.files.filter((f) => f.bindingType !== "NONE").map((f) => ({ o, f })),
    );
  return (
    <>
      <PageHeading
        title="装订中心"
        description="按原始文件分别整理，不同文件不合并成册。"
      />
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
  const orders = data.orders.filter((o) =>
    ["PENDING_PRINT", "READY_FOR_DELIVERY"].includes(o.status),
  );
  return (
    <>
      <PageHeading
        title="装袋中心"
        description="一单一码，一个塑封袋。核对全部资料后，才能出发。"
      />
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
  return `${task.copies} 份 · ${task.sides === "DUPLEX" ? "双面" : "单面"} · ${
    task.colorMode === "BW" ? "黑白" : "彩色"
  } · ${task.sheetCount} 张纸`;
}

export function PrintCenter() {
  // Invoke the demo component directly so the exported boundary keeps the same
  // hook order and remains inspectable by the existing render tests.
  return DEMO ? DemoPrintCenter() : <ServerPrintCenter />;
}

function ServerPrintCenter() {
  const { data: storeData } = useStore();
  const [status, setStatus] = useState("");
  const [kind, setKind] = useState("");
  const [search, setSearch] = useState("");
  const path = useMemo(() => {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (kind) params.set("kind", kind);
    const query = params.toString();
    return `/admin/print-tasks${query ? `?${query}` : ""}`;
  }, [status, kind]);
  const queue = useRemote<PrintQueueView>(path);
  const tasks = (queue.data?.tasks || []).filter((task) =>
    `${task.pickupCode} ${task.fileName}`.toLowerCase().includes(search.trim().toLowerCase()),
  );
  const next = queue.data?.nextTaskId
    ? queue.data.tasks.find((task) => task.id === queue.data?.nextTaskId)
    : undefined;
  const counts = queue.data?.counts || {};
  return (
    <>
      <PageHeading
        title="打印中心"
        description="服务端任务逐项打印；浏览器窗口事件不代表实际出纸，必须人工确认结果。"
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
        {[
          ["待打印", counts.PENDING || 0, "blue"],
          ["待结果确认", counts.ATTEMPTED || 0, "amber"],
          ["打印已完成", counts.COMPLETED || 0, "green"],
          ["打印异常", counts.EXCEPTION || 0, "red"],
        ].map(([label, count, color]) => (
          <div key={String(label)}>
            <i className={`dot ${color}`} />
            <span>{label}</span>
            <strong>{count}</strong>
          </div>
        ))}
      </div>
      <Panel className="filter-panel">
        <div className="filter-row">
          <div className="search-input grow">
            <Search size={17} />
            <input
              placeholder="搜索提取码或文件"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
          <select
            aria-label="任务状态"
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="">全部状态</option>
            <option value="PENDING">待打印</option>
            <option value="ATTEMPTED">待结果确认</option>
            <option value="COMPLETED">已完成</option>
            <option value="EXCEPTION">异常</option>
          </select>
          <select
            aria-label="文件类型"
            value={kind}
            onChange={(event) => setKind(event.target.value)}
          >
            <option value="">全部类型</option>
            <option value="DOCUMENT">文档</option>
            <option value="PHOTO">照片</option>
          </select>
        </div>
      </Panel>
      <Notice>
        打印前请登记“实际开印”；退款审核中的订单会锁住新生产。每个任务的份数已在最终 PDF
        展开，打印窗口只需 1 份。
      </Notice>
      <ServerQueueState
        loading={queue.loading}
        error={queue.error}
        empty={!queue.loading && !queue.error && !tasks.length}
        reload={queue.reload}
      />
      <div className="production-list">
        {tasks.map((task) => {
          const order = storeData.orders.find((row) => row.id === task.orderId);
          const locked = order?.blockers?.some(
            (blocker) => blocker.code === "REFUND_LOCKED",
          );
          return (
            <Panel key={task.id} className="print-task-card">
              <div className="print-task-top">
                <Link className="table-code" to={`${ADMIN}/orders/${task.orderId}`}>
                  {task.pickupCode}
                </Link>
                <span>{task.fileName}</span>
                <Badge status={task.status} />
                {task.openAttemptId && (
                  <span className="soft-tag">存在未确认尝试</span>
                )}
                {locked && <span className="soft-tag">退款审核锁定</span>}
              </div>
              <div className="print-task-details">
                <p>
                  {task.pageRange ? `第 ${task.pageRange} 页` : "整文件"} ·{" "}
                  {taskPaperLabel(task)}
                </p>
                <span>
                  任务 {task.id} · 内容版本 {task.orderVersion}
                </span>
                <span>
                  产物 {task.artifactId.slice(0, 8)} · SHA {task.artifactSha256.slice(0, 10)}
                </span>
                {task.bindingType !== "NONE" && <span>需装订</span>}
              </div>
              <div className="task-card-actions">
                <span className="muted">
                  {task.openAttemptId
                    ? "先核实未结尝试，再决定完成、失败或补打"
                    : "按服务端任务独立打印"}
                </span>
                <Link className="btn secondary" to={`${ADMIN}/print/${task.id}`}>
                  打开任务
                  <ArrowRight size={16} />
                </Link>
              </div>
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
  const { data: storeData, refresh } = useStore();
  const toast = useStore().toast;
  const taskState = useRemote<PrintTaskView>(
    id ? `/admin/print-tasks/${encodeURIComponent(id)}` : null,
  );
  const task = taskState.data;
  const order = task
    ? storeData.orders.find((row) => row.id === task.orderId)
    : undefined;
  // Production endpoints use the order optimistic version as the task lock.
  const orderVersion = task?.orderVersion ?? 0;
  const refundLocked = order?.blockers?.some(
    (blocker) => blocker.code === "REFUND_LOCKED",
  );
  const [startReason, setStartReason] = useState("");
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
  const [orderMeta, setOrderMeta] = useState<{
    status?: string;
    actualStartedAt?: string | null;
  } | null>(null);
  useEffect(() => {
    if (!task) return;
    let active = true;
    growthRequest<{ status?: string; actualStartedAt?: string | null }>(
      `/admin/orders/${encodeURIComponent(task.orderId)}`,
    )
      .then((value) => {
        if (active) setOrderMeta(value);
      })
      .catch(() => {
        if (active) setOrderMeta(null);
      });
    return () => {
      active = false;
    };
  }, [task?.orderId, task?.orderVersion]);
  useEffect(() => {
    setAttemptId(task?.openAttemptId || "");
  }, [task?.openAttemptId]);
  useEffect(() => {
    setReprintArtifactId("");
    setSheetIds([]);
    setReason("");
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
    return <Empty title="打印任务不存在或已失效" text="旧内容版本的任务不能继续打印。" />;
  const pdfUrl = printAssetUrl(reprintArtifactId || task.artifactId);
  const completed = task.status === "COMPLETED";
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
      setIssue(errorText(cause));
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
      else
        toast("打印窗口被拦截，请点击“打开最终 PDF”后手动打印并返回确认");
      await taskState.reload();
      await refresh();
    } catch (cause) {
      win?.close();
      setIssue(errorText(cause));
    } finally {
      setBusy(false);
    }
  };
  const confirmResult = () =>
    run(
      () =>
        growthRequest(`/admin/print-attempts/${encodeURIComponent(attemptId)}/result`, {
          method: "POST",
          body: {
            expectedVersion: orderVersion,
            outcome,
            reason: outcome === "COMPLETED" ? "" : reason,
          },
        }),
      "实际打印结果已记录",
    );
  const submitReprint = async () => {
    const win = window.open("about:blank", "_blank");
    if (win) win.opener = null;
    setBusy(true);
    setIssue("");
    try {
      const result = await growthRequest<{ artifactId: string; attemptId: string }>(
        `/admin/print-tasks/${encodeURIComponent(task.id)}/reprints`,
        {
          method: "POST",
          body: reprintPayload(task, reprintMode, sheetIds, reason),
        },
      );
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
      setIssue(errorText(cause));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <Link className="back-link" to={`${ADMIN}/print`}>
        <ArrowLeft size={16} />
        返回打印中心
      </Link>
      <PageHeading
        title={`打印任务 · ${task.pickupCode}`}
        description={task.fileName}
        action={<Badge status={task.status} />}
      />
      {refundLocked && (
        <Notice type="error">
          退款审核中，新生产已锁定；请先在售后流程完成未结打印尝试核实与审批。
        </Notice>
      )}
      {issue && (
        <Notice type="error">
          <span role="alert">{issue}</span>
        </Notice>
      )}
      <div className="print-view-layout">
        <Panel title="服务端任务">
          <div className="print-settings" aria-label="打印设置">
            {[`${task.copies} 份`, task.sides === "DUPLEX" ? "双面" : "单面", task.colorMode === "BW" ? "黑白" : "彩色", `${task.sheetCount} 张纸`, `打印页码 ${task.pageRange || "整文件"}`].map((value, index) => <span key={index}>{value}</span>)}
          </div>
          <dl className="courier-facts print-task-facts">
            <div>
              <dt>任务 ID</dt>
              <dd className="numeric">{task.id}</dd>
            </div>
            <div>
              <dt>订单版本</dt>
              <dd>{orderVersion}</dd>
            </div>
            <div>
              <dt>产物</dt>
              <dd className="numeric">{task.artifactId}</dd>
            </div>
            <div>
              <dt>SHA-256</dt>
              <dd className="numeric">{task.artifactSha256}</dd>
            </div>
          </dl>
          <DocPreview file={(order?.files.find((item) => item.id === task.fileId) || { id: task.fileId, assetId: task.artifactId, name: task.fileName, kind: "DOCUMENT", pages: task.pages }) as PrintFile} pdfUrl={pdfUrl} />
          <div className="button-row">
            <a className="btn secondary" href={pdfUrl} target="_blank" rel="noreferrer">
              {reprintArtifactId ? "打开补打 PDF" : "打开最终 PDF"}
            </a>
            <Button
              disabled={completed || !!task.openAttemptId || busy || refundLocked}
              onClick={startAttempt}
            >
              记录打印尝试并打开 PDF
            </Button>
          </div>
          {!completed && !task.openAttemptId && (
            <p className="muted">
              打开打印窗口只创建尝试。关闭窗口、afterprint 或失焦都不会自动算作完成。
            </p>
          )}
        </Panel>
        <Panel className="print-start-panel" title="实际开印">
          <p className="muted">
            实际开印必须由管理员明确登记；未登记前不能确认实体打印完成。后端只允许登记一次，重复提交会返回已登记错误。
          </p>
          <dl className="growth-facts v1-facts">
            <div>
              <dt>订单状态</dt>
              <dd><Badge status={orderMeta?.status || task.orderStatus} /></dd>
            </div>
            <div>
              <dt>实际开印</dt>
              <dd>
                {orderMeta?.actualStartedAt
                  ? new Date(orderMeta.actualStartedAt).toLocaleString("zh-CN")
                  : "尚未登记"}
              </dd>
            </div>
          </dl>
          <Field label="开印说明（选填）">
            <input
              value={startReason}
              onChange={(event) => setStartReason(event.target.value)}
              placeholder="例如：开始打印第一份任务"
            />
          </Field>
          <Button
            variant="secondary"
            busy={busy}
            disabled={
              !!orderMeta?.actualStartedAt ||
              (orderMeta?.status || task.orderStatus) !== "PENDING_PRINT"
            }
            onClick={async () => {
              let body: ReturnType<typeof actualStartPayload>;
              try {
                body = actualStartPayload(orderVersion, startReason);
              } catch (error) {
                setIssue(errorText(error));
                return;
              }
              const ok = await run(
                () =>
                  growthRequest(`/admin/orders/${encodeURIComponent(task.orderId)}/start-production`, {
                    method: "POST",
                    body,
                  }),
                "实际开印已登记",
              );
              if (ok)
                setOrderMeta((current) => ({
                  ...(current || {}),
                  status: "PENDING_PRINT",
                  actualStartedAt: new Date().toISOString(),
                }));
            }}
          >
            {orderMeta?.actualStartedAt ? "实际开印已登记" : "登记实际开印"}
          </Button>
        </Panel>
      </div>
      {attemptId && !completed && (
        <Panel className="print-result-panel" title="确认本次打印结果">
          <Notice>
            请核对实体纸张的颜色、单双面、翻转与页序。确认完成后才能进入装订/装袋。
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
              <textarea value={reason} onChange={(event) => setReason(event.target.value)} />
            </Field>
          )}
          <Button busy={busy} onClick={confirmResult}>
            确认记录当前尝试
          </Button>
        </Panel>
      )}
      <Panel className="reprint-panel" title="实体纸张与补打">
        <p className="muted">
          指定纸张补打按服务端页纸映射选择完整实体纸张；双面会自动包含正反面与空白背面，整任务补打不增加费用。
        </p>
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
        {reprintMode === "SHEETS" && (
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
                {sheet.frontPage ?? "—"} / 背面 {sheet.backPage ?? (sheet.blankBack ? "空白" : "—")}
              </label>
            ))}
          </div>
        )}
        <Field label="补打原因（必填）">
          <textarea value={reason} onChange={(event) => setReason(event.target.value)} />
        </Field>
        <Button
          busy={busy}
          disabled={!!task.openAttemptId}
          onClick={submitReprint}
        >
          建立补打并记录尝试
        </Button>
      </Panel>
    </>
  );
}

export function BindingCenter() {
  // Invoke the demo component directly so the exported boundary keeps the same
  // hook order and remains inspectable by the existing render tests.
  return DEMO ? DemoBindingCenter() : <ServerBindingCenter />;
}

function ServerBindingCenter() {
  const { refresh } = useStore();
  const queue = useRemote<{ tasks: BindingTaskView[] }>("/admin/binding-tasks");
  const [busyId, setBusyId] = useState("");
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
      {issue && (
        <Notice type="error">
          <span role="alert">{issue}</span>
        </Notice>
      )}
      <ServerQueueState
        loading={queue.loading}
        error={queue.error}
        empty={!queue.loading && !queue.error && !queue.data?.tasks.length}
        reload={queue.reload}
      />
      <div className="production-list">
        {queue.data?.tasks.map((task) => (
          <Panel key={task.id}>
            <div className="print-task-top">
              <Link className="table-code" to={`${ADMIN}/orders/${task.orderId}`}>
                {task.pickupCode}
              </Link>
              <span>{task.fileName}</span>
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
                  装订任务 {task.id} · 打印状态 {task.printStatus}
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
        ))}
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
  const { refresh } = useStore();
  const queue = useRemote<{ tasks: PackingTaskView[] }>("/admin/packing-tasks");
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
      <ServerQueueState
        loading={queue.loading}
        error={queue.error}
        empty={!queue.loading && !queue.error && !queue.data?.tasks.length}
        reload={queue.reload}
      />
      <div className="packing-grid">
        {queue.data?.tasks.map((task) => {
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
              <p className="muted">
                清单版本 {task.checklistVersion} · 装袋任务 {task.id}
              </p>
              <div className="packing-checks">
                {task.checklist.items.map((item) => (
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
                      <b>{item.name || item.fileId}</b>
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
