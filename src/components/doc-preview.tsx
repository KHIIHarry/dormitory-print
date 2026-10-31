import { DEMO } from "../lib/api";
import { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Expand, FileText } from "lucide-react";
import type { PrintFile } from "../lib/types";
import { Modal } from "./ui";
import { TaskStatus } from "./feedback";
import { getIdentityGeneration } from "../growth/client";
import { imageMime } from "../lib/image-bytes";
type PreviewProps = {
  file: PrintFile;
  compact?: boolean;
  pdfUrl?: string;
  expanded?: boolean;
  initialPage?: number;
  label?: string;
  onReadyChange?: (ready: boolean) => void;
};
export function DocPreview(props: PreviewProps) {
  const sourceKey = [
    props.file.id,
    props.file.assetId,
    props.file.uploadVersion,
    props.file.imagePrintVersion,
    props.pdfUrl,
    props.file.url,
  ].join(":");
  return <PreviewContent key={sourceKey} {...props} />;
}
function PreviewContent({
  file,
  compact = false,
  pdfUrl,
  expanded = false,
  initialPage = 1,
  label,
  onReadyChange,
}: PreviewProps) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [page, setPage] = useState(initialPage);
  const [displayedPage, setDisplayedPage] = useState(0);
  const generation = useRef(getIdentityGeneration());
  const renderAbort = useRef<AbortController | null>(null);
  const [invalidated, setInvalidated] = useState(false);
  const imageUrlRef = useRef<string | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  useEffect(() => {
    const invalidate = () => {
      renderAbort.current?.abort();
      if (canvas.current) canvas.current.width = canvas.current.width;
      if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current);
      imageUrlRef.current = null;
      setImageUrl(null);
      setInvalidated(true);
      setZoom(false);
      setBytes(null);
    };
    window.addEventListener("dormprint:identity", invalidate);
    return () => {
      window.removeEventListener("dormprint:identity", invalidate);
      if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current);
      imageUrlRef.current = null;
    };
  }, []);
  const [pages, setPages] = useState(file.pages || 1);
  const [zoom, setZoom] = useState(false);
  const [error, setError] = useState("");
  const [rendering, setRendering] = useState(false);
  const [bytes, setBytes] = useState<Uint8Array | null>(null);
  useEffect(() => {
    setPage(initialPage);
    setError("");
    setBytes(null);
    if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current);
    imageUrlRef.current = null;
    setImageUrl(null);
    if (!pdfUrl || invalidated) return;
    const abort = new AbortController();
    const url = new URL(pdfUrl, location.href);
    if (url.origin !== location.origin) {
      setError("预览地址无效，请重新获取受权文件");
      return;
    }
    fetch(pdfUrl, {
      credentials: "same-origin",
      cache: "no-store",
      signal: abort.signal,
    })
      .then((r) => {
        if (!r.ok) throw new Error("文件已过期或无权读取，请刷新任务");
        return r.arrayBuffer();
      })
      .then((buffer) => {
        if (
          !abort.signal.aborted &&
          generation.current === getIdentityGeneration()
        ) {
          const data = new Uint8Array(buffer);
          const mime = imageMime(data);
          if (mime) {
            if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current);
            const objectUrl = URL.createObjectURL(
              new Blob([data], { type: mime }),
            );
            imageUrlRef.current = objectUrl;
            setImageUrl(objectUrl);
            setPages(1);
            setDisplayedPage(1);
            setRendering(false);
          } else {
            setBytes(data);
          }
        }
      })
      .catch((e) => {
        if (
          !abort.signal.aborted &&
          generation.current === getIdentityGeneration()
        )
          setError(e.message);
      });
    return () => abort.abort();
  }, [pdfUrl, file.id, invalidated]);
  useEffect(() => {
    const source = pdfUrl
      ? bytes
      : file.localFile?.type === "application/pdf"
        ? file.localFile
        : null;
    if (!source || !canvas.current || invalidated) return;
    const abort = new AbortController();
    renderAbort.current = abort;
    const valid = () =>
      !abort.signal.aborted && generation.current === getIdentityGeneration();
    setRendering(true);
    setError("");
    import("../lib/pdf")
      .then((m) => {
        if (valid() && canvas.current)
          return m.renderPage(
            source instanceof Uint8Array ? source.slice() : source,
            canvas.current,
            page,
            abort.signal,
            expanded ? 2200 : 1440,
          );
      })
      .then((count) => {
        if (valid() && count) {
          setPages(count);
          setDisplayedPage(Math.min(page, count));
          setRendering(false);
        }
      })
      .catch((e) => {
        if (valid()) {
          setError((e as Error).message);
          setRendering(false);
        }
      });
    return () => {
      abort.abort();
    };
  }, [file.id, file.localFile, page, pdfUrl, bytes, invalidated, expanded]);
  const ready =
    !invalidated &&
    !error &&
    !rendering &&
    displayedPage === page &&
    (pdfUrl ? !!bytes || !!imageUrl : !!file.localFile);
  useEffect(() => {
    onReadyChange?.(ready);
  }, [ready, onReadyChange]);
  const sample = !invalidated && DEMO && !file.localFile && !pdfUrl;
  return (
    <div
      className={`document-preview ${compact ? "compact" : ""} ${expanded ? "expanded" : ""}`}
    >
      <div className="preview-toolbar">
        <span>
          <FileText size={14} />
          {label ||
            (pdfUrl ? (imageUrl ? "图片预览" : "最终 PDF") : "文件预览")}
        </span>
        {!expanded && (
          <button
            className="icon-btn"
            aria-label="放大预览"
            onClick={() => setZoom(true)}
          >
            <Expand size={15} />
          </button>
        )}
      </div>
      <div className="preview-canvas">
        {invalidated ? (
          <p className="form-hint">
            当前预览资源不可用，请刷新文件或重新获取受权 PDF。
          </p>
        ) : file.kind === "PHOTO" && file.url && !pdfUrl ? (
          <img src={file.url} alt={file.name} />
        ) : sample ? (
          <div className="sample-paper">
            <span>COLLEGE ENGLISH</span>
            <h3>英语期末复习资料</h3>
            <p>Final Examination · Review Notes</p>
            <hr />
            <h4>Unit {page} · Learning & Practice</h4>
            <p>知识梳理 / 重点语法 / 复习练习</p>
            {Array.from({ length: 8 }, (_, i) => (
              <div
                className="paper-line"
                style={{ width: `${i % 3 === 2 ? 65 : 100}%` }}
                key={i}
              />
            ))}
            <h4>阅读与理解</h4>
            {Array.from({ length: 7 }, (_, i) => (
              <div
                className="paper-line"
                style={{ width: `${i % 4 === 3 ? 50 : 100}%` }}
                key={i}
              />
            ))}
            <small>
              示例预览 · 第 {page} 页 / {file.pages} 页
            </small>
          </div>
        ) : imageUrl ? (
          <img src={imageUrl} alt={file.name} />
        ) : pdfUrl || file.localFile?.type === "application/pdf" ? (
          <canvas ref={canvas} />
        ) : (
          <p className="form-hint">
            当前预览资源不可用，请刷新文件或重新获取受权 PDF。
          </p>
        )}
      </div>
      {!invalidated &&
        (rendering || (pdfUrl && !bytes && !imageUrl && !error)) && (
          <div className="pdf-render-status">
            <TaskStatus label={`正在渲染第 ${page} 页…`} />
          </div>
        )}
      {error && (
        <p className="field-error pdf-render-status">预览失败：{error}</p>
      )}
      <div className="preview-pager">
        <button
          className="icon-btn"
          aria-label="上一页"
          disabled={invalidated || page <= 1 || rendering}
          onClick={() => setPage((p) => p - 1)}
        >
          <ChevronLeft size={16} />
        </button>
        <span>
          {sample ? page : displayedPage || "—"} / {sample ? file.pages : pages}
        </span>
        <button
          className="icon-btn"
          aria-label="下一页"
          disabled={
            invalidated || page >= (sample ? file.pages : pages) || rendering
          }
          onClick={() => setPage((p) => p + 1)}
        >
          <ChevronRight size={16} />
        </button>
      </div>
      {zoom && (
        <Modal title={file.name} fullscreen onClose={() => setZoom(false)}>
          <DocPreview
            file={file}
            pdfUrl={pdfUrl}
            initialPage={page}
            label={label}
            expanded
          />
        </Modal>
      )}
    </div>
  );
}
