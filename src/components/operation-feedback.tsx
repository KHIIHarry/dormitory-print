import { useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { Notice } from "./notice";
import { getFeedbackLayer, subscribeToOverlays } from "./overlay-stack";
import "./operation-feedback.css";

export type OperationIssue = {
  message: string;
  tone: "error" | "warning";
  layer: HTMLElement | null;
  busy?: boolean;
  checkStatus?: () => void;
};

export function OperationFeedback({
  issue,
  toast,
  dismiss,
}: {
  issue?: OperationIssue;
  toast?: { message: string; tone: "success" | "info" };
  dismiss: () => void;
}) {
  const layer = useSyncExternalStore(
    subscribeToOverlays,
    getFeedbackLayer,
    () => null,
  );
  // A failure belongs to the page or dialog that initiated the operation.
  const visibleIssue = issue?.layer === layer ? issue : undefined;
  if ((!visibleIssue && !toast) || typeof document === "undefined") return null;
  return createPortal(
    <div
      data-dp-layer=""
      data-operation-feedback=""
      className={
        visibleIssue
          ? "operation-feedback operation-feedback-alert"
          : "operation-feedback operation-feedback-toast"
      }
    >
      {visibleIssue ? (
        <Notice type={visibleIssue.tone} reveal={false}>
          <div className="operation-feedback-body">
            <div role="alert" aria-atomic="true">
              <strong>
                {visibleIssue.tone === "warning" ? "请核对状态" : "操作失败"}
              </strong>
              <span>{visibleIssue.message}</span>
            </div>
            {visibleIssue.checkStatus && (
              <button
                type="button"
                className="text-link"
                disabled={visibleIssue.busy}
                onClick={visibleIssue.checkStatus}
              >
                {visibleIssue.busy ? "正在核对…" : "刷新并核对状态"}
              </button>
            )}
          </div>
          <button
            type="button"
            className="operation-feedback-close"
            aria-label="关闭提示"
            onClick={dismiss}
          >
            <X size={17} aria-hidden="true" />
          </button>
        </Notice>
      ) : (
        <div className="feedback-toast" role="status" data-tone={toast?.tone}>
          <span aria-hidden="true" />
          {toast?.message}
        </div>
      )}
    </div>,
    layer ?? document.body,
  );
}
