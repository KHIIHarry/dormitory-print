import { useState, useRef, useEffect } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  FileText,
  Image,
  Layers,
  Minus,
  Plus,
  RotateCw,
  Settings2,
  Trash2,
} from "lucide-react";
import { useStore } from "../lib/store";
import type { PrintFile, Segment } from "../lib/types";
import {
  fileErrors,
  pricingErrors,
  filePrice,
  expandRanges,
  productionCount,
} from "../lib/domain";
import { DEMO, money } from "../lib/api";
import {
  Button,
  Field,
  FileIcon,
  Notice,
  PageHeading,
  Panel,
  Steps,
} from "../components/ui";
import { DocPreview } from "../components/doc-preview";
import { getAsset, isSpreadsheetUpload } from "../v1/upload-client";
function OriginalDocumentPreview({ file }: { file: PrintFile }) {
  const [source, setSource] = useState<{ id: string; url?: string; error?: string }>({ id: "" });
  useEffect(() => {
    if (DEMO || !file.assetId || file.kind === "PHOTO") return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const asset = await getAsset(file.assetId, controller.signal);
        if (controller.signal.aborted) return;
        if (asset.status === "READY") {
          setSource({ id: file.assetId, url: asset.previewUrl || asset.normalizedUrl || undefined, error: !asset.previewUrl && !asset.normalizedUrl ? "预览暂不可用，请稍后重试。" : undefined });
        } else if (asset.status === "FAILED" || asset.status === "DELETED" || asset.status === "PURGED") {
          setSource({ id: file.assetId, error: asset.error || "文件处理失败或已过期，请重新上传。" });
        } else timer = setTimeout(load, 1200);
      } catch {
        if (!controller.signal.aborted) setSource({ id: file.assetId, error: "预览加载失败，请检查网络后重新打开。" });
      }
    };
    void load();
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [file.assetId, file.kind]);
  if (!DEMO && file.assetId && file.kind !== "PHOTO" && source.id !== file.assetId)
    return <div className="document-preview"><p className="pdf-render-status" role="status">正在准备文件预览…</p></div>;
  if (source.id === file.assetId && source.error) return <p className="field-error" role="alert">{source.error}</p>;
  return <DocPreview file={file} label="文件预览" pdfUrl={source.id === file.assetId ? source.url : undefined} />;
}
export function BindingCard({
  type,
  label,
  selected,
  onClick,
  disabled = false,
  price,
}: {
  type: string;
  label: string;
  selected: boolean;
  onClick: () => void;
  disabled?: boolean;
  price?: number | null;
}) {
  return (
    <button
      className={`binding-card ${selected ? "selected" : ""}`}
      disabled={disabled}
      onClick={onClick}
    >
      <div className={`binding-paper ${type.toLowerCase()}`}>
        <i />
        <i />
        <i />
        {type === "CORNER" ? (
          <b />
        ) : type === "SIDE" ? (
          <>
            <b />
            <b />
          </>
        ) : null}
      </div>
      <strong>{label}</strong>
      <small>
        {type === "NONE"
          ? "保持散页"
          : price !== null && price !== undefined
            ? `${money(price)} / 册`
            : "服务尚未配置"}
      </small>
      {selected && (
        <span className="selection-tick">
          <Check size={12} />
        </span>
      )}
    </button>
  );
}
function SegmentEditor({
  segment,
  total,
  index,
  update,
  remove,
  advanced,
}: {
  segment: Segment;
  total: number;
  index: number;
  update: (p: Partial<Segment>) => void;
  remove?: () => void;
  advanced: boolean;
}) {
  return (
    <div className="segment-editor">
      {advanced && (
        <div className="segment-header">
          <b>分段 {index + 1}</b>
          {remove && (
            <button className="icon-btn" onClick={remove} aria-label="删除分段">
              <Trash2 size={15} />
            </button>
          )}
        </div>
      )}
      <div className="form-grid">
        {advanced && (
          <Field label="打印页码">
            <input
              value={segment.range}
              onChange={(e) => update({ range: e.target.value })}
              placeholder={`例如 1-4,7（共 ${total} 页）`}
            />
          </Field>
        )}
        <Field label="打印颜色">
          <div className="segmented-control">
            <button
              className={segment.colorMode === "BW" ? "selected" : ""}
              onClick={() => update({ colorMode: "BW" })}
            >
              黑白
            </button>
            <button
              className={segment.colorMode === "COLOR" ? "selected" : ""}
              onClick={() => update({ colorMode: "COLOR" })}
            >
              彩色
            </button>
          </div>
        </Field>
        <Field label="打印面数">
          <div className="segmented-control">
            <button
              className={segment.sides === "SINGLE" ? "selected" : ""}
              onClick={() => update({ sides: "SINGLE", flip: "NONE" })}
            >
              单面
            </button>
            <button
              className={segment.sides === "DUPLEX" ? "selected" : ""}
              onClick={() => update({ sides: "DUPLEX", flip: "LONG_EDGE" })}
            >
              双面
            </button>
          </div>
        </Field>
        {segment.sides === "DUPLEX" && (
          <Field label="双面翻转">
            <select
              value={segment.flip}
              onChange={(e) =>
                update({ flip: e.target.value as Segment["flip"] })
              }
            >
              <option value="LONG_EDGE">长边翻转（像书本）</option>
              <option value="SHORT_EDGE">短边翻转（像日历）</option>
            </select>
          </Field>
        )}
        <Field label="打印份数">
          <div className="stepper">
            <button
              aria-label="减少份数"
              disabled={segment.copies <= 1}
              onClick={() => update({ copies: segment.copies - 1 })}
            >
              <Minus size={16} />
            </button>
            <input
              aria-label="份数"
              type="number"
              min="1"
              max="999"
              value={segment.copies}
              onChange={(e) => update({ copies: Number(e.target.value) })}
            />
            <button
              aria-label="增加份数"
              onClick={() => update({ copies: segment.copies + 1 })}
            >
              <Plus size={16} />
            </button>
          </div>
        </Field>
      </div>
    </div>
  );
}
export function SettingsPage() {
  const { draft, setDraft, data } = useStore();
  const [active, setActive] = useState(draft[0]?.id);
  const [advanced, setAdvanced] = useState(false);
  const nav = useNavigate();
  if (!draft.length || draft.some((f) => f.imagePrintMedium === "PENDING"))
    return <Navigate to="/u/new" replace />;
  const file = draft.find((f) => f.id === active) || draft[0];
  const patch = (p: Partial<PrintFile>) =>
    setDraft((s) =>
      s.map((f) =>
        f.id === file.id
          ? { ...f, ...p, artifactId: undefined, sha256: undefined }
          : f,
      ),
    );
  const errors = [...fileErrors(file), ...pricingErrors(file, data.config)];
  const total = draft.reduce((sum, f) => sum + filePrice(f, data.config), 0);
  const updateSegment = (id: string, p: Partial<Segment>) =>
    patch({
      segments: file.segments.map((s) => (s.id === id ? { ...s, ...p } : s)),
    });
  return (
    <main className="flow-page">
      <Link to="/u/new" className="back-link">
        <ArrowLeft size={17} />
        返回上传
      </Link>
      <PageHeading
        title="设置你的打印方式"
        description="每份文件独立设置，让每一页都恰到好处。"
      />
      <Steps step={1} />
      <div className="settings-layout">
        <aside className="file-sidebar">
          <span className="subtle-label">本单文件 · {draft.length}</span>
          {draft.map((f) => (
            <button
              className={`file-tab ${file.id === f.id ? "active" : ""}`}
              onClick={() => setActive(f.id)}
              key={f.id}
            >
              <FileIcon file={f} />
              <span>
                <strong>{f.name}</strong>
                <small>
                  {f.kind === "PHOTO" ? "5 寸照片" : `A4 · ${f.pages} 页`}
                </small>
              </span>
              {file.id === f.id && <i />}
            </button>
          ))}
          <Link to="/u/new" className="text-link">
            <Plus size={15} />
            继续添加文件
          </Link>
        </aside>
        <div className="settings-main">
          <Panel>
            <div className="panel-heading">
              <h2>
                {file.kind === "PHOTO"
                  ? "照片打印设置"
                  : file.imageSource
                    ? "A4 图片打印设置"
                    : "文档打印设置"}
              </h2>
              <span className="soft-tag">
                {file.kind === "PHOTO" ? "5 寸照片" : "A4 · 每面一页"}
              </span>
            </div>
            {file.kind === "PHOTO" ? (
              <>
                <div className="photo-mini">
                  {file.url ? (
                    <img src={file.url} alt={file.name} />
                  ) : (
                    <Image size={70} />
                  )}
                  <div>
                    <h3>{file.name}</h3>
                    <p>方向、裁剪与留白，都可以调整。</p>
                    <Link
                      className="btn secondary"
                      to={`/u/new/photo/${file.id}`}
                    >
                      <Settings2 size={16} />
                      编辑照片
                    </Link>
                  </div>
                </div>
                <Notice>
                  照片按后台校准尺寸输出，四边留白当前均为 3 mm。请在最终 PDF
                  中确认效果。
                </Notice>
              </>
            ) : (
              <>
                {isSpreadsheetUpload({ name: file.name }) && (
                  <Notice>
                    Excel 已按上传时设置的打印区域、方向与缩放转换为服务端 PDF（本文件{" "}
                    {file.pages} 页）。请在本页右侧及下一步最终 PDF 中逐页核对分页；如需修改打印区域，请返回上传重新选择该文件并使用新的打印区域上传。
                  </Notice>
                )}
                <div className="mode-row">
                  <span>打印参数</span>
                  <div className="segmented-control small">
                    <button
                      className={!advanced ? "selected" : ""}
                      onClick={() => setAdvanced(false)}
                    >
                      普通模式
                    </button>
                    <button
                      className={advanced ? "selected" : ""}
                      onClick={() => setAdvanced(true)}
                    >
                      高级分段
                    </button>
                  </div>
                </div>
                {!advanced && file.segments.length > 1 && (
                  <Notice>
                    此文件已有多个分段，切换高级模式查看全部设置。
                  </Notice>
                )}
                {file.segments.map((s, i) => (
                  <SegmentEditor
                    key={s.id}
                    segment={s}
                    total={file.pages}
                    index={i}
                    advanced={advanced || file.segments.length > 1}
                    update={(p) => updateSegment(s.id, p)}
                    remove={
                      file.segments.length > 1
                        ? () =>
                            patch({
                              segments: file.segments.filter(
                                (x) => x.id !== s.id,
                              ),
                            })
                        : undefined
                    }
                  />
                ))}
                {advanced && (
                  <>
                    <Button
                      variant="dashed"
                      onClick={() =>
                        patch({
                          segments: [
                            ...file.segments,
                            {
                              id: crypto.randomUUID(),
                              range: "",
                              colorMode: "BW",
                              sides: "SINGLE",
                              flip: "NONE",
                              copies: 1,
                            },
                          ],
                        })
                      }
                    >
                      <Plus size={16} />
                      添加页码分段
                    </Button>
                    <Field
                      label="明确跳过的页码"
                      hint="没有打印的页面必须在此明确列出，不会自动补打。"
                    >
                      <input
                        placeholder="例如 5,8（可留空）"
                        value={file.excludedPages.join(",")}
                        onChange={(e) => {
                          const value = e.target.value;
                          patch({
                            excludedPages: value
                              ? value.split(/[,，]/).map(Number)
                              : [],
                          });
                        }}
                      />
                    </Field>
                  </>
                )}
                <div className="allocation-summary">
                  <span>
                    <Layers size={16} />
                    页面分配摘要
                  </span>
                  {errors.length ? (
                    <ul>
                      {errors.map((e) => (
                        <li className="field-error" key={e}>
                          {e}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p>
                      <Check size={14} />共 {file.pages} 页，
                      {file.excludedPages.length
                        ? `明确跳过 ${file.excludedPages.join(",")} 页`
                        : "全部已分配"}
                      ；各分段分别生成 PDF
                    </p>
                  )}
                </div>
                <div className="binding-section">
                  <h3>
                    装订方式 <small>每个文件独立整理</small>
                  </h3>
                  <div className="binding-options">
                    {[
                      ["NONE", "无需装订"],
                      ["CORNER", "左上角单钉"],
                      ["SIDE", "左侧双钉"],
                    ].map(([type, label]) => (
                      <BindingCard
                        key={type}
                        type={type}
                        label={label}
                        selected={file.bindingType === type}
                        disabled={
                          type !== "NONE" && !data.config.bindingApproved
                        }
                        price={
                          type === "CORNER"
                            ? data.config.prices.corner
                            : data.config.prices.side
                        }
                        onClick={() =>
                          patch({
                            bindingType: type as PrintFile["bindingType"],
                          })
                        }
                      />
                    ))}
                  </div>
                  {!data.config.bindingApproved && (
                    <p className="form-hint">
                      装订收费规则尚未配置，暂不开放选择。
                    </p>
                  )}
                </div>
              </>
            )}
          </Panel>
          <div className="flow-bottom settings-bottom">
            <div>
              <span>预计合计</span>
              <strong>{money(total)}</strong>
              <small>最终费用以结算报价为准</small>
            </div>
            <Button
              disabled={draft.some(
                (f) =>
                  fileErrors(f).length > 0 ||
                  pricingErrors(f, data.config).length > 0,
              )}
              onClick={() => nav("/u/review")}
            >
              生成最终预览
              <ArrowRight size={17} />
            </Button>
          </div>
        </div>
        <aside className="preview-aside">
          <OriginalDocumentPreview file={file} />
          <Notice>
            {file.imageSource && file.kind === "DOCUMENT"
              ? "当前预览为图片在 A4 纸上的完整排版。下一步将按份数和单双面设置生成最终 PDF。"
              : "当前为原文件预览。下一步将生成包含分段、份数与空白背面的最终 PDF。"}
          </Notice>
        </aside>
      </div>
    </main>
  );
}
export function PhotoPage() {
  const { id } = useParams();
  const { draft, setDraft, data } = useStore();
  const nav = useNavigate();
  const file = draft.find((f) => f.id === id);
  if (!file || file.imagePrintMedium === "PENDING")
    return <Navigate to="/u/new" replace />;
  if (file.kind !== "PHOTO") return <Navigate to="/u/new/settings" replace />;
  const photo = file.photo;
  const patch = (p: Partial<PrintFile["photo"]>) =>
    setDraft((s) =>
      s.map((f) =>
        f.id === id
          ? {
              ...f,
              photo: { ...f.photo, ...p },
              artifactId: undefined,
              sha256: undefined,
            }
          : f,
      ),
    );
  return (
    <main className="flow-page">
      <Link className="back-link" to="/u/new/settings">
        <ArrowLeft size={17} />
        返回打印设置
      </Link>
      <PageHeading
        title="把美好，留在相纸上"
        description="调整画面构图，确认真实留白。"
      />
      <div className="photo-layout">
        <Panel className="photo-editor">
          <div className="photo-editor-tools">
            <span>
              5 寸相纸 · {data.config.photoWidthMm} ×{" "}
              {data.config.photoHeightMm} mm
            </span>
            <button
              className="text-button"
              onClick={() =>
                patch({
                  rotationDegrees: ((photo.rotationDegrees + 90) % 360) as
                    0 | 90 | 180 | 270,
                })
              }
            >
              <RotateCw size={17} />
              旋转 90°
            </button>
          </div>
          <div
            className={`photo-paper ${photo.orientation.toLowerCase()}`}
            style={{
              aspectRatio:
                photo.orientation === "LANDSCAPE"
                  ? `${data.config.photoWidthMm}/${data.config.photoHeightMm}`
                  : `${data.config.photoHeightMm}/${data.config.photoWidthMm}`,
              padding: 0,
            }}
          >
            <div
              className="photo-print-area"
              onPointerDown={(e) => {
                if (photo.fit !== "COVER") return;
                e.currentTarget.setPointerCapture(e.pointerId);
              }}
              onPointerMove={(e) => {
                if (e.buttons !== 1 || photo.fit !== "COVER") return;
                patch({
                  x: Math.max(0, Math.min(100, photo.x - e.movementX / 2)),
                  y: Math.max(0, Math.min(100, photo.y - e.movementY / 2)),
                });
              }}
            >
              <PhotoCanvas file={file} config={data.config} />
            </div>
          </div>
          <div className="photo-margin-caption">
            四边留白：上 {data.config.margins.top} / 右{" "}
            {data.config.margins.right} / 下 {data.config.margins.bottom} / 左{" "}
            {data.config.margins.left} mm
          </div>
        </Panel>
        <Panel title="画面设置">
          <Field label="纸张方向">
            <div className="segmented-control">
              <button
                className={photo.orientation === "LANDSCAPE" ? "selected" : ""}
                onClick={() => patch({ orientation: "LANDSCAPE" })}
              >
                横向
              </button>
              <button
                className={photo.orientation === "PORTRAIT" ? "selected" : ""}
                onClick={() => patch({ orientation: "PORTRAIT" })}
              >
                竖向
              </button>
            </div>
          </Field>
          <Field label="照片适配方式">
            <div className="choice-stack">
              <button
                className={photo.fit === "COVER" ? "selected" : ""}
                onClick={() => patch({ fit: "COVER" })}
              >
                <b>铺满可打印区域</b>
                <small>画面可能裁剪，拖动调整构图</small>
              </button>
              <button
                className={photo.fit === "CONTAIN" ? "selected" : ""}
                onClick={() => patch({ fit: "CONTAIN", zoom: 1 })}
              >
                <b>完整保留</b>
                <small>不裁剪，内部可能额外留白</small>
              </button>
            </div>
          </Field>
          {photo.fit === "COVER" && (
            <>
              <Field label="缩放">
                <input
                  type="range"
                  min="1"
                  max="3"
                  step="0.01"
                  value={photo.zoom}
                  onChange={(e) => patch({ zoom: Number(e.target.value) })}
                />
              </Field>
              <Field label="水平位置">
                <input
                  type="range"
                  min="0"
                  max="100"
                  value={photo.x}
                  onChange={(e) => patch({ x: Number(e.target.value) })}
                />
              </Field>
              <Field label="垂直位置">
                <input
                  type="range"
                  min="0"
                  max="100"
                  value={photo.y}
                  onChange={(e) => patch({ y: Number(e.target.value) })}
                />
              </Field>
            </>
          )}
          <Field label="打印张数">
            <input
              type="number"
              min="1"
              max="999"
              value={photo.copies}
              onChange={(e) =>
                patch({ copies: Math.max(1, Number(e.target.value)) })
              }
            />
          </Field>
          <Notice>此处为编辑预览；下一步以生成的最终 PDF 为准。</Notice>
          <Button className="full" onClick={() => nav("/u/new/settings")}>
            保存照片设置
            <Check size={16} />
          </Button>
        </Panel>
      </div>
    </main>
  );
}

function PhotoCanvas({
  file,
  config,
}: {
  file: PrintFile;
  config: import("../lib/types").Config;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    let active = true;
    import("../lib/photo-layout")
      .then((m) => {
        if (active && canvas.current)
          return m.drawPhoto(file, config, canvas.current);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [file, config]);
  return (
    <canvas
      ref={canvas}
      aria-label="照片实际纸张与留白预览"
      style={{ width: "100%", height: "100%", display: "block" }}
    />
  );
}
