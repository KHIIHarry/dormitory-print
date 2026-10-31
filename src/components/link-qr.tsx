import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { Check, Copy, Download, QrCode, ShieldCheck } from "lucide-react";
import { getIdentityGeneration } from "../growth/client";
import { QrLinkError, createQrDataUrl, validateQrLink } from "../lib/link-qr";
import { Button, Modal, Notice } from "./ui";
import "./link-qr.css";

export interface LinkQrButtonProps {
  /** The exact authorized URL. It is not rendered or encoded until clicked. */
  value: string;
  label?: string;
  /** Adds a stronger warning for order-exclusive credential links. */
  sensitive?: boolean;
}

function validationOptions() {
  const browser = typeof window !== "undefined";
  return {
    kind: "order" as const,
    currentOrigin: browser ? window.location.origin : undefined,
    allowLocalHttp:
      browser && import.meta.env.DEV && window.location.protocol === "http:",
  };
}

function messageOf(cause: unknown) {
  return cause instanceof QrLinkError
    ? cause.message
    : "二维码生成失败，请稍后重试";
}

function dataUrlToBlob(dataUrl: string) {
  const comma = dataUrl.indexOf(",");
  const meta = dataUrl.slice(5, comma);
  const mime = meta.split(";")[0] || "image/png";
  const binary = atob(dataUrl.slice(comma + 1).replace(/\s/g, ""));
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return new Blob([bytes], { type: mime });
}

/**
 * Explicit, local-only QR preview for authorized links.
 *
 * The URL never enters the DOM before the user opens the dialog. QR images are
 * generated in-browser, scoped to the current identity generation, and purged
 * when the dialog closes, the value changes, or the account identity changes.
 */
export function LinkQrButton({
  value,
  label = "预览链接二维码",
  sensitive = false,
}: LinkQrButtonProps) {
  const [open, setOpen] = useState(false);
  const [displayValue, setDisplayValue] = useState("");
  const [image, setImage] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "manual">(
    "idle",
  );
  const run = useRef(0);
  const openRef = useRef(open);
  const valueRef = useRef(value);
  const blobUrl = useRef<string | null>(null);
  const generation = useRef(getIdentityGeneration());
  const ownerId = useId();
  openRef.current = open;
  valueRef.current = value;

  // Modal keeps its last content for the 180ms exit animation. Remove this
  // instance's private image and URL text before paint so credentials cannot
  // linger in the document after close or an identity switch.
  useLayoutEffect(() => {
    if (open) return;
    const root = Array.from(
      document.querySelectorAll<HTMLElement>("[data-link-qr-owner]"),
    ).find((node) => node.dataset.linkQrOwner === ownerId);
    if (!root) return;
    root
      .querySelectorAll<HTMLElement>(
        '[data-testid="link-qr-image"], .link-qr-value-row, .link-qr-fallback',
      )
      .forEach((node) => {
        if (node instanceof HTMLImageElement) node.removeAttribute("src");
        node.remove();
      });
  }, [open, ownerId]);

  const releaseBlob = useCallback(() => {
    if (blobUrl.current) {
      URL.revokeObjectURL(blobUrl.current);
      blobUrl.current = null;
    }
  }, []);

  const close = useCallback(() => {
    run.current += 1;
    setOpen(false);
    setDisplayValue("");
    setImage(null);
    setError("");
    setLoading(false);
    setCopyState("idle");
    releaseBlob();
  }, [releaseBlob]);

  useEffect(() => {
    const onIdentity = () => {
      if (getIdentityGeneration() !== generation.current) {
        generation.current = getIdentityGeneration();
        close();
      }
    };
    window.addEventListener("dormprint:identity", onIdentity);
    return () => window.removeEventListener("dormprint:identity", onIdentity);
  }, [close]);

  useEffect(() => {
    if (valueRef.current !== value && openRef.current) close();
    valueRef.current = value;
  }, [value, close]);

  useEffect(
    () => () => {
      run.current += 1;
      releaseBlob();
    },
    [releaseBlob],
  );

  const openPreview = () => {
    if (openRef.current) return;
    const runId = ++run.current;
    const activeGeneration = getIdentityGeneration();
    generation.current = activeGeneration;
    setOpen(true);
    openRef.current = true;
    setDisplayValue("");
    setImage(null);
    setError("");
    setCopyState("idle");
    setLoading(true);

    let validated: string;
    try {
      validated = validateQrLink(value, validationOptions());
    } catch (cause) {
      if (runId !== run.current) return;
      setLoading(false);
      setError(messageOf(cause));
      return;
    }
    setDisplayValue(validated);

    createQrDataUrl(validated)
      .then((dataUrl) => {
        if (
          runId !== run.current ||
          activeGeneration !== getIdentityGeneration()
        )
          return;
        setImage(dataUrl);
        setLoading(false);
      })
      .catch((cause) => {
        if (
          runId !== run.current ||
          activeGeneration !== getIdentityGeneration()
        )
          return;
        setLoading(false);
        setError(messageOf(cause));
      });
  };

  const download = () => {
    if (!image) return;
    releaseBlob();
    const url = URL.createObjectURL(dataUrlToBlob(image));
    blobUrl.current = url;
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "dormprint-link-qr.png";
    anchor.rel = "noopener";
    anchor.style.display = "none";
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => {
      if (blobUrl.current === url) releaseBlob();
    }, 1000);
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(displayValue);
      setCopyState("copied");
    } catch {
      setCopyState("manual");
    }
  };

  return (
    <>
      <Button
        variant="secondary"
        className="link-qr-trigger"
        onClick={openPreview}
        aria-haspopup="dialog"
        aria-expanded={open}
        data-testid="link-qr-trigger"
      >
        <QrCode size={16} />
        {label}
      </Button>
      <Modal title="链接二维码预览" open={open} onClose={close}>
        <div
          className="link-qr-dialog"
          data-testid="link-qr-dialog"
          data-link-qr-owner={ownerId}
        >
          <Notice>
            二维码只编码此链接，不会签发、扩大或恢复访问权限。链接被撤销或过期后，扫码仍会失败。
          </Notice>
          <p className="link-qr-privacy">
            <ShieldCheck size={16} aria-hidden="true" />
            <span>
              这是链接二维码，不是微信好友／群二维码或收款码。
              {sensitive
                ? "此链接含订单专属访问凭据，请仅私下发送给本人。"
                : "请确认接收对象后再转发。"}
            </span>
          </p>

          {loading && (
            <p className="link-qr-status" role="status">
              正在浏览器本地生成二维码…
            </p>
          )}
          {error && (
            <p className="field-error" role="alert">
              {error}
            </p>
          )}

          {image && (
            <div className="link-qr-preview">
              <img
                src={image}
                width={512}
                height={512}
                alt="链接二维码预览，扫描后打开当前链接"
                data-testid="link-qr-image"
              />
            </div>
          )}

          {displayValue && (
            <div className="link-qr-value-row">
              <code className="link-qr-value" data-testid="link-qr-value">
                {displayValue}
              </code>
              <button
                type="button"
                className="copy-btn link-qr-copy"
                onClick={copy}
                aria-label="复制链接"
                title="复制链接"
                data-testid="link-qr-copy"
              >
                {copyState === "copied" ? (
                  <Check size={16} />
                ) : (
                  <Copy size={16} />
                )}
                {copyState === "copied" ? "已复制" : "复制链接"}
              </button>
              <span className="link-qr-copy-status" role="status">
                {copyState === "copied" ? "链接已复制到剪贴板" : ""}
              </span>
            </div>
          )}

          {copyState === "manual" && (
            <div className="link-qr-fallback">
              <label htmlFor={`link-qr-manual-${ownerId}`}>
                自动复制不可用，请手动选择并复制
              </label>
              <textarea
                id={`link-qr-manual-${ownerId}`}
                readOnly
                value={displayValue}
                onFocus={(event) => event.currentTarget.select()}
              />
            </div>
          )}

          <div className="button-row link-qr-actions">
            <Button
              variant="secondary"
              onClick={download}
              disabled={!image}
              data-testid="link-qr-download"
            >
              <Download size={16} />
              下载 PNG
            </Button>
            <Button variant="text" onClick={close}>
              关闭
            </Button>
          </div>
          <p className="form-hint">
            二维码在浏览器本地生成，图片不会上传到服务器或第三方服务。
          </p>
        </div>
      </Modal>
    </>
  );
}
