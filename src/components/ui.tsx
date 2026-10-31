import { formatFileSize } from "../lib/display-format";
import { getIdentityGeneration } from "../growth/client";
import { MOTION, usePresence } from "./presence";
import { exitOverlay, registerOverlay } from "./overlay-stack";
import { TaskStatus, useDelayedBusy } from "./feedback";
import "./motion.css";
import { assertQuote } from "../growth/pricing";
import type { Quote } from "../growth/types";
import {
  Children,
  useEffect,
  useLayoutEffect,
  useState,
  useId,
  useRef,
  isValidElement,
  cloneElement,
  type ReactNode,
} from "react";
import { Link } from "react-router-dom";
import { createPortal } from "react-dom";
import {
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronRight,
  Copy,
  FileText,
  Image,
  Info,
  Loader2,
  PackageOpen,
  Printer,
  RefreshCw,
  X,
} from "lucide-react";
import { useStore } from "../lib/store";
import { money } from "../lib/api";
import type { OrderStatus, PrintFile } from "../lib/types";
export function Logo({ small = false }: { small?: boolean }) {
  return (
    <span className={`brand ${small ? "small" : ""}`}>
      <span className="brand-icon">
        <Printer size={small ? 20 : 23} />
      </span>
      <span>
        Dorm<span className="brand-accent">Print</span>
        <small>宿舍打印</small>
      </span>
    </span>
  );
}
export function Button({
  children,
  variant = "",
  className = "",
  busy = false,
  busyLabel,
  busyTarget,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: string;
  busy?: boolean;
  busyLabel?: string;
  busyTarget?: HTMLElement | null;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  const targeted = busy && (!busyTarget || busyTarget === ref.current);
  const show = useDelayedBusy(targeted);
  const locked = useRef(false);
  const idleChildren = useRef(children);
  if (!busy) idleChildren.current = children;
  useLayoutEffect(() => {
    if (!ref.current) return;
    if (!busy) return;
    const element = ref.current;
    const width = element.getBoundingClientRect().width;
    const previous = element.style.minWidth;
    element.style.minWidth = `min(${width}px, 100%)`;
    return () => {
      element.style.minWidth = previous;
    };
  }, [busy]);
  // Keep text/geometry; replace the existing action icon rather than adding a second one.
  const content = targeted ? ReactChildren(children) : children;
  return (
    <button
      ref={ref}
      className={`btn ${variant} ${className}${targeted ? " busy" : ""}`}
      {...props}
      disabled={props.disabled || busy}
      aria-busy={targeted || undefined}
      onClick={(event) => {
        if (busy || locked.current) {
          event.preventDefault();
          return;
        }
        locked.current = true;
        try {
          props.onClick?.(event);
        } finally {
          queueMicrotask(() => {
            locked.current = false;
          });
        }
      }}
    >
      {targeted ? (
        <>
          <span className="button-original" aria-hidden="true">
            {idleChildren.current}
          </span>
          <span className={`button-feedback ${show ? "is-visible" : ""}`}>
            {show && <Loader2 className="spin" size={18} aria-hidden="true" />}
            {busyLabel || content}
          </span>
        </>
      ) : (
        content
      )}
    </button>
  );
}
function ReactChildren(children: ReactNode): ReactNode {
  // Lucide icons are elements; retain text and arbitrary non-icon child content.
  return Children.toArray(children).filter(
    (child) =>
      !(
        isValidElement(child) &&
        ((child.props as Record<string, unknown>).size ||
          (child.props as Record<string, unknown>)["aria-hidden"])
      ),
  );
}
export function LinkButton({
  to,
  children,
  variant = "",
}: {
  to: string;
  children: ReactNode;
  variant?: string;
}) {
  return (
    <Link className={`btn ${variant}`} to={to}>
      {children}
    </Link>
  );
}
export function Badge({ status }: { status: OrderStatus | string }) {
  const map: Record<string, [string, string]> = {
    PENDING_CONFIRMATION: ["待确认", "amber"],
    PENDING_PRINT: ["待打印", "blue"],
    READY_FOR_DELIVERY: ["待配送", "purple"],
    DELIVERING: ["配送中", "cyan"],
    DELIVERED: ["已送达", "green"],
    CANCELLED: ["已撤销", "gray"],
    DELIVERY_EXCEPTION: ["配送异常", "red"],
    PENDING: ["待打印", "blue"],
    ATTEMPTED: ["待结果确认", "amber"],
    COMPLETED: ["已完成", "green"],
    EXCEPTION: ["异常", "red"],
    PLANNED: ["规划中", "gray"],
    PUBLISHED: ["已发布", "purple"],
    POSTPONED: ["已延期", "amber"],
    OPEN: ["待处理", "red"],
    IN_PROGRESS: ["处理中", "amber"],
    RESOLVED: ["已解决", "green"],
    READY: ["待装袋", "purple"],
    PACKED: ["已装袋", "green"],
    BLOCKED: ["待生产完成", "amber"],
  };
  const [label, color] = map[status] || [status, "gray"];
  return (
    <span className={`badge ${color}`}>
      <i />
      {label}
    </span>
  );
}
export function PageHeading({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {action}
    </div>
  );
}
export function Panel({
  children,
  className = "",
  title,
  action,
}: {
  children: ReactNode;
  className?: string;
  title?: string;
  action?: ReactNode;
}) {
  return (
    <section className={`panel ${className}`}>
      {title && (
        <div className="panel-heading">
          <h2>{title}</h2>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}
export function Notice({
  children,
  type = "info",
}: {
  children: ReactNode;
  type?: string;
}) {
  return (
    <div className={`notice ${type}`}>
      <Info size={16} />
      <div>{children}</div>
    </div>
  );
}
export function Empty({
  title = "这里还没有内容",
  text = "新的记录会出现在这里。",
  action,
}: {
  title?: string;
  text?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <span>
        <PackageOpen size={38} />
      </span>
      <h3>{title}</h3>
      <p>{text}</p>
      {action}
    </div>
  );
}
export function Loading({
  label = "正在读取当前内容",
}: { label?: string } = {}) {
  return (
    <div className="empty">
      <TaskStatus label={label} />
    </div>
  );
}
export function ErrorState({
  message,
  retry,
}: {
  message: string;
  retry?: () => void;
}) {
  return (
    <div className="empty">
      <Info size={32} />
      <h3>暂时无法加载</h3>
      <p>{message}</p>
      {retry && (
        <Button variant="secondary" onClick={retry}>
          <RefreshCw size={16} />
          重新加载
        </Button>
      )}
    </div>
  );
}
export function Modal({
  title,
  children,
  onClose,
  wide = false,
  fullscreen = false,
  open = true,
  focusKey,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
  fullscreen?: boolean;
  open?: boolean;
  focusKey?: string;
}) {
  const modalRef = useRef<HTMLElement>(null),
    backdropRef = useRef<HTMLDivElement>(null);
  const [identityValid, setIdentityValid] = useState(true);
  useLayoutEffect(() => {
    const generation = getIdentityGeneration();
    const invalidate = () => {
      if (generation !== getIdentityGeneration()) setIdentityValid(false);
    };
    window.addEventListener("dormprint:identity", invalidate);
    return () => window.removeEventListener("dormprint:identity", invalidate);
  }, []);
  open = open && identityValid;
  const closeRef = useRef(onClose),
    openRef = useRef(open),
    contentRef = useRef(children);
  closeRef.current = onClose;
  openRef.current = open;
  if (open) contentRef.current = children;
  const { present, phase, reduced } = usePresence(
    open,
    identityValid ? MOTION.modalOut : 0,
  );
  const epoch = useRef(0);
  useLayoutEffect(() => {
    if (!present || !backdropRef.current) return;
    const node = backdropRef.current;
    const cycle = ++epoch.current;
    const registration = registerOverlay(
      node,
      () => closeRef.current(),
      () => openRef.current,
    );
    return () => {
      const clone = node.cloneNode(true) as HTMLDivElement;
      const sourceWindow = node.querySelector<HTMLElement>(".modal");
      const clonedWindow = clone.querySelector<HTMLElement>(".modal");
      if (sourceWindow && clonedWindow)
        clonedWindow.style.opacity = getComputedStyle(sourceWindow).opacity;
      clone.dataset.exitSnapshot = "true";
      clone.style.setProperty(
        "--exit-shade-opacity",
        getComputedStyle(node, "::before").opacity,
      );
      const sourceCanvases = node.querySelectorAll("canvas");
      clone.querySelectorAll("canvas").forEach((canvas, index) => {
        const original = sourceCanvases[index];
        if (!original) return;
        canvas.width = original.width;
        canvas.height = original.height;
        canvas.getContext("2d")?.drawImage(original, 0, 0);
      });
      // React removes conditional windows immediately. Retain only their inert exit snapshot.
      queueMicrotask(() => {
        if (
          epoch.current !== cycle ||
          node.isConnected ||
          !openRef.current ||
          reduced
        ) {
          registration.release();
          return;
        }
        clone.setAttribute("data-dp-layer", "");
        clone.setAttribute("aria-hidden", "true");
        document.body.append(clone);
        registration.replace(clone);
        void clone.offsetWidth;
        requestAnimationFrame(() =>
          exitOverlay(clone, registration.release, MOTION.modalOut),
        );
      });
    };
  }, [present]);
  useLayoutEffect(() => {
    if (!present || !open) return;
    modalRef.current
      ?.querySelector<HTMLElement>(
        "button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href]",
      )
      ?.focus({ preventScroll: true });
  }, [present, open, focusKey]);
  if (!present) return null;
  return createPortal(
    <div
      ref={backdropRef}
      className="modal-backdrop"
      data-state={phase}
      data-dp-layer=""
      style={identityValid ? undefined : { visibility: "hidden" }}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && open) closeRef.current();
      }}
    >
      <section
        ref={modalRef}
        className={`modal ${wide ? "wide" : ""} ${fullscreen ? "fullscreen" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        inert={!open}
      >
        <header>
          <h2>{title}</h2>
          <button
            className="icon-btn"
            onClick={() => closeRef.current()}
            aria-label="关闭"
          >
            <X size={20} />
          </button>
        </header>
        {contentRef.current}
      </section>
    </div>,
    document.body,
  );
}
export function Field({
  label,
  children,
  error,
  hint,
}: {
  label: string;
  children: ReactNode;
  error?: string;
  hint?: string;
}) {
  const id = useId();
  const simple =
    isValidElement(children) &&
    typeof children.type === "string" &&
    ["input", "select", "textarea"].includes(children.type);
  const control = simple
    ? cloneElement(children as React.ReactElement<Record<string, unknown>>, {
        id,
        "aria-invalid": !!error,
        "aria-describedby": error
          ? `${id}-error`
          : hint
            ? `${id}-hint`
            : undefined,
      })
    : children;
  return (
    <div className="field">
      {simple ? (
        <label htmlFor={id}>{label}</label>
      ) : (
        <span id={id}>{label}</span>
      )}
      {control}
      {hint && <small id={`${id}-hint`}>{hint}</small>}
      {error && (
        <small id={`${id}-error`} className="field-error" role="alert">
          {error}
        </small>
      )}
    </div>
  );
}
export function CopyButton({
  text,
  label = "复制",
  className = "",
}: {
  text: string;
  label?: string;
  className?: string;
}) {
  const { toast } = useStore();
  const [manual, setManual] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      toast("已复制到剪贴板");
    } catch {
      setManual(true);
    }
  };
  return (
    <>
      <button
        className={`copy-btn ${className}`}
        onClick={copy}
        title={label}
        aria-label={label}
      >
        <Copy size={16} />
        {label !== "复制" && label}
      </button>
      {manual && (
        <Modal title="长按或选择下方文字复制" onClose={() => setManual(false)}>
          <textarea readOnly value={text} onFocus={(e) => e.target.select()} />
        </Modal>
      )}
    </>
  );
}
export function Steps({ step }: { step: number }) {
  return (
    <div className="steps">
      {["上传文件", "打印设置", "确认订单"].map((s, i) => (
        <div key={s} className={step === i ? "active" : step > i ? "done" : ""}>
          <span>{step > i ? <Check size={14} /> : i + 1}</span>
          <b>{s}</b>
        </div>
      ))}
    </div>
  );
}
export function FileIcon({
  file,
}: {
  file: Pick<PrintFile, "kind" | "name" | "imageSource">;
}) {
  return (
    <span
      className={`file-icon ${file.kind === "PHOTO" || !!file.imageSource ? "image" : ""}`}
    >
      {file.kind === "PHOTO" || !!file.imageSource ? (
        <Image size={23} />
      ) : (
        <FileText size={23} />
      )}
      <small>
        {file.name.split(".").pop()?.toUpperCase() ||
          (file.kind === "PHOTO" ? "IMG" : "PDF")}
      </small>
    </span>
  );
}
export function FileRow({
  file,
  action,
  indicator,
}: {
  file: PrintFile;
  action?: ReactNode;
  indicator?: ReactNode;
}) {
  return (
    <div className="file-row">
      {indicator ? (
        <span className="file-progress-visual">
          <FileIcon file={file} />
          <span className="file-progress-overlay">{indicator}</span>
        </span>
      ) : (
        <FileIcon file={file} />
      )}
      <div className="file-info">
        <strong>{file.name}</strong>
        <small>
          {formatFileSize(file.size)} <i>·</i>{" "}
          {file.imagePrintMedium === "PENDING"
            ? "图片 · 待选择纸张"
            : file.kind === "PHOTO"
              ? "5 寸照片纸"
              : file.imageSource
                ? `A4 图片 · ${file.pages} 页`
                : `${file.pages} 页`}
        </small>
      </div>
      {action}
    </div>
  );
}
export function Stat({
  label,
  value,
  icon,
  caption,
  href,
}: {
  label: string;
  value: string | number;
  icon: ReactNode;
  caption?: string;
  href?: string;
}) {
  const content = (
    <>
      <div className="stat-top">
        <span>{label}</span>
        <i>{icon}</i>
      </div>
      <strong>{value}</strong>
      {caption && <small>{caption}</small>}
    </>
  );
  return href ? (
    <Link to={href} className="stat">
      {content}
    </Link>
  ) : (
    <div className="stat">{content}</div>
  );
}
export function PriceRows({
  total,
  print,
  binding = 0,
  quote,
}: {
  quote?: Quote;
  total: number;
  print?: number;
  binding?: number;
}) {
  if (quote) {
    try {
      assertQuote(quote);
    } catch (e) {
      return <Notice type="error">{(e as Error).message}</Notice>;
    }
  }
  if (quote)
    return (
      <div className="price-rows">
        {quote.lines.map((line) => (
          <div key={line.key}>
            <span>
              {line.service === "A4"
                ? "A4打印"
                : line.service === "PHOTO"
                  ? "照片打印"
                  : "装订"}{" "}
              · {line.quantity}
              {line.unit === "SHEET" ? "张" : "本"}
            </span>
            <b>{money(line.amountCents)}</b>
          </div>
        ))}
        <div>
          <span>费用小计</span>
          <b>{money(quote.subtotalCents)}</b>
        </div>
        <div>
          <span>
            {quote.selectedPromotionType === "COUPON"
              ? "优惠券抵扣"
              : quote.selectedPromotionType === "VOLUME_TIER"
                ? "满量优惠"
                : quote.selectedPromotionType === "STAFF_GRADE"
                  ? quote.staffDiscountCandidate?.source === "SUPER_ADMIN"
                    ? "超级管理员免单"
                    : "员工优惠"
                  : "优惠抵扣"}
          </span>
          <b>
            {quote.discountCents > 0 ? "−" : ""}
            {money(quote.discountCents)}
          </b>
        </div>
        <div>
          <span>校园配送</span>
          <b className="green-text">免费</b>
        </div>
        <div className="price-total">
          <span>合计应付</span>
          <strong>{money(quote.totalCents)}</strong>
        </div>
      </div>
    );
  return (
    <div className="price-rows">
      <div>
        <span>打印费用</span>
        <b>{money(print ?? total - binding)}</b>
      </div>
      <div>
        <span>装订费用</span>
        <b>{money(binding)}</b>
      </div>
      <div>
        <span>校园配送</span>
        <b className="green-text">免费</b>
      </div>
      <div className="price-total">
        <span>合计应付</span>
        <strong>{money(total)}</strong>
      </div>
    </div>
  );
}
export function SectionLink({
  to,
  children,
}: {
  to: string;
  children: ReactNode;
}) {
  return (
    <Link className="text-link" to={to}>
      {children}
      <ChevronRight size={15} />
    </Link>
  );
}
export { ArrowRight, CheckCircle2 };
