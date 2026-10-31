import { useEffect, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";

function useDocumentVisible() {
  const [visible, setVisible] = useState(
    typeof document === "undefined" || document.visibilityState !== "hidden",
  );
  useEffect(() => {
    const sync = () => setVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", sync);
    return () => document.removeEventListener("visibilitychange", sync);
  }, []);
  return visible;
}
/** Visual delay never delays the operation or its synchronous duplicate lock. */
export function useDelayedBusy(active: boolean, delay = 200) {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!active) {
      setVisible(false);
      return;
    }
    const timer = window.setTimeout(() => setVisible(true), delay);
    return () => window.clearTimeout(timer);
  }, [active, delay]);
  return active && visible;
}
export function TaskStatus({
  label,
  long = true,
}: {
  label: string;
  long?: boolean;
}) {
  const visible = useDelayedBusy(true);
  const onScreen = useDocumentVisible();
  const lengthy = useDelayedBusy(long, 10000);
  const [online, setOnline] = useState(navigator.onLine);
  useEffect(() => {
    const sync = () => setOnline(navigator.onLine);
    window.addEventListener("online", sync);
    window.addEventListener("offline", sync);
    return () => {
      window.removeEventListener("online", sync);
      window.removeEventListener("offline", sync);
    };
  }, []);
  return (
    <div className="task-status" role="status" aria-live="polite">
      {online && visible && onScreen && (
        <Loader2 className="spin" size={24} aria-hidden="true" />
      )}
      <span>
        {online ? label : "当前离线，请联网后核实任务状态"}
        {online && lengthy && (
          <small>耗时比平常稍长，请稍后查看任务状态。</small>
        )}
      </span>
    </div>
  );
}
export type SkeletonVariant =
  | "cards"
  | "table"
  | "detail"
  | "timeline"
  | "stats"
  | "profile"
  | "coupons"
  | "paper";
const Block = ({
  width = "100%",
  height = 12,
}: {
  width?: string;
  height?: number;
}) => <span className="skeleton-block" style={{ width, height }} />;
export function ResourceSkeleton({
  variant = "cards",
}: {
  variant?: SkeletonVariant;
}) {
  const onScreen = useDocumentVisible();
  let content;
  if (variant === "paper")
    content = (
      <div className="skeleton-paper">
        <Block width="70%" height={18} />
        {Array.from({ length: 8 }, (_, i) => (
          <Block key={i} width={i % 3 === 2 ? "65%" : "100%"} />
        ))}
      </div>
    );
  else if (variant === "stats")
    content = (
      <div className="stats-grid">
        {Array.from({ length: 4 }, (_, i) => (
          <div className="stat" key={i}>
            <Block width="60%" />
            <Block width="35%" height={28} />
            <Block width="75%" />
          </div>
        ))}
      </div>
    );
  else if (variant === "profile")
    content = (
      <div className="skeleton-profile">
        <Block width="48px" height={48} />
        <div>
          <Block width="45%" />
          <Block width="75%" />
        </div>
      </div>
    );
  else if (variant === "coupons")
    content = (
      <div className="skeleton-coupons">
        {Array.from({ length: 3 }, (_, i) => (
          <div className="skeleton-coupon" key={i}>
            <Block width="32%" height={28} />
            <Block width="70%" />
            <Block width="50%" />
          </div>
        ))}
      </div>
    );
  else if (variant === "detail")
    content = (
      <div className="skeleton-detail">
        <Block width="40%" height={22} />
        <Block width="70%" />
        <Block width="55%" />
        <Block height={68} />
      </div>
    );
  else
    content = Array.from({ length: 3 }, (_, i) => (
      <div className={`skeleton-row skeleton-${variant}`} key={i}>
        <Block
          width={variant === "timeline" ? "12px" : "40px"}
          height={variant === "timeline" ? 12 : 40}
        />
        <div>
          <Block width={i % 2 ? "65%" : "80%"} />
          <Block width="45%" />
        </div>
        {variant === "table" && <Block width="54px" />}
      </div>
    ));
  return (
    <div
      className={`resource-skeleton resource-skeleton-${variant}`}
      data-paused={!onScreen}
      role="status"
      aria-label="正在读取当前内容"
      aria-busy="true"
    >
      <div aria-hidden="true">{content}</div>
    </div>
  );
}
export function RefreshStatus({
  loading,
  error,
  label = "正在更新",
  retry,
}: {
  loading: boolean;
  error?: string;
  label?: string;
  retry?: () => unknown;
}) {
  const visible = useDelayedBusy(loading, 600);
  // Background reads must not insert a row into the page on every poll.
  // Keep a delayed accessible status; only failures need visible feedback.
  if (!error)
    return visible ? (
      <span className="resource-refresh-pending" role="status" aria-live="polite">
        {label}
      </span>
    ) : null;
  return (
    <div
      className={`resource-refresh ${error ? "has-error" : ""}`}
      role={error ? "alert" : "status"}
    >
      {visible && <Loader2 className="spin" size={16} aria-hidden="true" />}
      <span>{error ? `更新失败，当前为上次数据：${error}` : label}</span>
      {error && retry && (
        <button
          type="button"
          className="text-link"
          onClick={() => void retry()}
        >
          <RefreshCw size={14} />
          重试
        </button>
      )}
    </div>
  );
}
export function Progress({
  value,
  label,
  ring = false,
}: {
  value?: number;
  label: string;
  ring?: boolean;
}) {
  const onScreen = useDocumentVisible();
  const current =
    value !== undefined && Number.isFinite(value)
      ? Math.max(0, Math.min(100, value))
      : undefined;
  return (
    <span
      className={`task-progress ${ring ? "task-progress-ring" : ""}`}
      data-paused={!onScreen}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={current}
      aria-valuetext={current === undefined ? label : undefined}
    >
      {ring ? (
        <svg viewBox="0 0 36 36" aria-hidden="true">
          <circle className="progress-track" cx="18" cy="18" r="14" />
          <circle
            className={
              current === undefined ? "progress-unknown" : "progress-value"
            }
            cx="18"
            cy="18"
            r="14"
            pathLength="100"
            strokeDasharray={current === undefined ? "25 75" : `${current} 100`}
          />
        </svg>
      ) : (
        <span
          className={
            current === undefined ? "progress-unknown" : "progress-value"
          }
          style={
            current === undefined
              ? undefined
              : { transform: `scaleX(${current / 100})` }
          }
        />
      )}
    </span>
  );
}
