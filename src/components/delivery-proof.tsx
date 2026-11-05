import { useState } from "react";
import { ImageOff, Maximize2, RefreshCw } from "lucide-react";
import { formatDisplayTime } from "../lib/display-format";
import type { Proof } from "../lib/types";
import { Button, Modal, Panel } from "./ui";
import "./delivery-proof.css";

function ProofPhoto({ src }: { src: string }) {
  const [status, setStatus] = useState<"loading" | "ready" | "failed">(
    "loading",
  );
  const [attempt, setAttempt] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const source = attempt
    ? `${src}${src.includes("?") ? "&" : "?"}proofRetry=${attempt}`
    : src;
  return (
    <div className="delivery-proof-photo">
      <div
        className="delivery-proof-photo-stage"
        data-state={status}
        aria-busy={status === "loading"}
      >
        <button
          type="button"
          className="delivery-proof-photo-button"
          aria-label="查看交付照片大图"
          disabled={status !== "ready"}
          hidden={status === "failed"}
          onClick={(event) => {
            // WebKit pointer activation does not focus buttons automatically.
            // Give the shared modal a concrete trigger to restore on close.
            event.currentTarget.focus({ preventScroll: true });
            setExpanded(true);
          }}
        >
          <img
            src={source}
            alt="资料实际交付照片"
            referrerPolicy="no-referrer"
            onLoad={() => setStatus("ready")}
            onError={() => {
              setStatus("failed");
              setExpanded(false);
            }}
          />
          {status === "ready" && (
            <span className="delivery-proof-photo-action">
              <Maximize2 size={16} aria-hidden="true" />
              查看大图
            </span>
          )}
        </button>
        {status === "loading" && (
          <div className="delivery-proof-photo-status" role="status">
            正在读取交付照片…
          </div>
        )}
        {status === "failed" && (
          <div className="delivery-proof-photo-status" role="status">
            <ImageOff size={28} aria-hidden="true" />
            <strong>交付照片暂时无法查看</strong>
            <p>
              照片可能已过期或当前账号无权查看，请重新加载或刷新订单后再试。
            </p>
            <Button
              variant="secondary"
              onClick={() => {
                setStatus("loading");
                setAttempt((value) => value + 1);
              }}
            >
              <RefreshCw size={16} aria-hidden="true" />
              重新加载照片
            </Button>
          </div>
        )}
      </div>
      {expanded && (
        <Modal title="交付照片" wide onClose={() => setExpanded(false)}>
          <div className="delivery-proof-enlarged">
            <img
              src={source}
              alt="资料实际交付照片大图"
              referrerPolicy="no-referrer"
              onError={() => {
                setStatus("failed");
                setExpanded(false);
              }}
            />
          </div>
        </Modal>
      )}
    </div>
  );
}

/** Read-only view of server facts, shared by the existing staff order details. */
export function DeliveryProofPanel({
  proof,
  delivered = false,
}: {
  proof?: Proof;
  delivered?: boolean;
}) {
  if (!proof && !delivered) return null;
  const src =
    proof?.photoUrl ||
    (proof?.photoAssetId
      ? `${(import.meta.env.VITE_API_BASE_URL || "/api/v1").replace(/\/$/, "")}/assets/${encodeURIComponent(proof.photoAssetId)}/content`
      : "");
  const position = proof?.position;
  const hasPosition =
    position &&
    Number.isFinite(position.latitude) &&
    Number.isFinite(position.longitude);
  return (
    <Panel title="交付凭证" className="delivery-proof-panel">
      {!proof ? (
        <div className="delivery-proof-empty" role="status">
          <strong>暂无交付凭证</strong>
          <p>订单已送达，当前未返回交付凭证，请刷新订单后核对。</p>
        </div>
      ) : (
        <div className="delivery-proof-layout">
          {src ? (
            <ProofPhoto key={src} src={src} />
          ) : (
            <div className="delivery-proof-photo-exception">
              <ImageOff size={24} aria-hidden="true" />
              <strong>
                {proof.skipPhotoReason ? "免拍照说明" : "未记录交付照片"}
              </strong>
              <p>
                {proof.skipPhotoReason ||
                  "当前凭证没有照片或免拍照说明，请核对交付记录。"}
              </p>
            </div>
          )}
          <dl className="delivery-proof-facts">
            <div>
              <dt>交付时间</dt>
              <dd>
                <time
                  dateTime={
                    /^\d{4}-\d{2}-\d{2}T/.test(proof.deliveredAt)
                      ? proof.deliveredAt
                      : undefined
                  }
                >
                  {formatDisplayTime(proof.deliveredAt)}
                </time>
              </dd>
            </div>
            {hasPosition ? (
              <>
                <div>
                  <dt>实际位置</dt>
                  <dd>
                    <span className="delivery-proof-coordinates">
                      {position.latitude.toFixed(6)},{" "}
                      {position.longitude.toFixed(6)}
                    </span>
                    <span className="delivery-proof-meta">
                      WGS84 · 精度 ±{Math.round(position.accuracyMeters)} m
                    </span>
                  </dd>
                </div>
                <div>
                  <dt>定位采集时间</dt>
                  <dd>{formatDisplayTime(position.capturedAt)}</dd>
                </div>
              </>
            ) : (
              <div>
                <dt>{proof.skipLocationReason ? "定位例外" : "实际位置"}</dt>
                <dd>{proof.skipLocationReason || "未记录定位或例外说明"}</dd>
              </div>
            )}
            <div>
              <dt>摆放说明</dt>
              <dd className="delivery-proof-note">
                {proof.placementNote?.trim() || "无补充说明"}
              </dd>
            </div>
          </dl>
        </div>
      )}
    </Panel>
  );
}
