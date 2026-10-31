import { useEffect, useRef, useState, type ReactNode } from "react";
import { Button, Empty, Modal, Notice } from "../../components/ui";
import { useStore } from "../../lib/store";
import { growthRequest, useGrowthResource } from "../../growth/client";
import { isUncertainSubmission } from "../../lib/api";
import { ResourceSkeleton, RefreshStatus } from "../../components/feedback";
import { DeviceTask } from "../../courier/task-state";
import { useIdentity } from "../../growth/identity";
import type { StaffSession } from "../../growth/types";
export function errorText(error: unknown): string {
  if (!error) return "";
  const item = error as { status?: number; code?: string; message?: string };
  if (item.status === 409)
    return "配置已被其他管理员修改（409）。请重新加载最新版本后再提交。";
  if (item.status === 403) return "权限不足（403），请联系有相应权限的管理员。";
  if (item.status === 429)
    return "操作过于频繁（429），请按服务端冷却时间稍后重试。";
  return typeof error === "string"
    ? error
    : item.message || "请求失败，请检查网络后重试";
}
export function ResourceState({
  loading,
  error,
  empty = false,
  reload,
  hasData = false,
  refreshing = false,
  variant = "table",
}: {
  hasData?: boolean;
  refreshing?: boolean;
  variant?:
    | "cards"
    | "table"
    | "detail"
    | "timeline"
    | "stats"
    | "profile"
    | "coupons"
    | "paper";
  loading: boolean;
  error: unknown;
  empty?: boolean;
  reload: () => unknown;
}) {
  if (hasData)
    return (
      <>
        <RefreshStatus
          loading={refreshing || loading}
          error={error ? errorText(error) : undefined}
          retry={reload}
        />
        {empty && <Empty title="暂无记录" text="调整筛选条件或等待业务记录产生。" />}
      </>
    );
  if (loading) return <ResourceSkeleton variant={variant} />;
  if (error)
    return (
      <Notice type="error">
        <span role="alert">{errorText(error)}</span>{" "}
        <Button variant="secondary" onClick={() => reload()}>
          重新加载
        </Button>
      </Notice>
    );
  if (empty)
    return <Empty title="暂无记录" text="调整筛选条件或等待业务记录产生。" />;
  return null;
}
export function useAdminAccess(capability: string) {
  const session = useGrowthResource<StaffSession>("/auth/me");
  const allowed =
    session.data?.role === "ADMIN" &&
    ((session.data.capabilities ?? []).includes(capability) ||
      (session.data.capabilities ?? []).includes("*"));
  return {
    allowed,
    loading: session.loading,
    error: session.error,
    reload: session.reload,
  };
}
export function AccessState({
  access,
}: {
  access: ReturnType<typeof useAdminAccess>;
}) {
  if (access.loading || access.error) return <ResourceState {...access} />;
  return access.allowed ? null : (
    <Notice type="warning">当前管理员没有此模块的管理权限。</Notice>
  );
}
export function useSubmit() {
  const { toast } = useStore();
  const [pending, setPending] = useState(false),
    [error, setError] = useState("");
  const task = useRef(new DeviceTask());
  const { generation } = useIdentity();
  useEffect(() => () => task.current.cancel(), [generation]);
  const [online, setOnline] = useState(
    typeof navigator === "undefined" || navigator.onLine,
  );
  useEffect(() => {
    const sync = () => setOnline(navigator.onLine);
    window.addEventListener("online", sync);
    window.addEventListener("offline", sync);
    return () => {
      window.removeEventListener("online", sync);
      window.removeEventListener("offline", sync);
    };
  }, []);
  const run = async <T,>(
    action: () => Promise<T>,
    success?: string,
  ): Promise<{ ok: true; data: T } | { ok: false }> => {
    if (!navigator.onLine) {
      setError("当前离线，请连接网络后重新提交。");
      return { ok: false };
    }
    const token = task.current.start();
    if (token === undefined) return { ok: false };
    setError("");
    setPending(true);
    try {
      const data = await action();
      if (!task.current.current(token)) return { ok: false };
      if (success) toast(success);
      return { ok: true, data };
    } catch (e) {
      if (!task.current.current(token)) return { ok: false };
      setError(
        isUncertainSubmission(e)
          ? "提交结果待核实，请刷新记录确认，勿重复提交。"
          : errorText(e),
      );
      return { ok: false };
    } finally {
      if (task.current.finish(token)) setPending(false);
    }
  };
  return { run, pending, error, setError, online };
}
export function SubmitState({
  state,
}: {
  state: ReturnType<typeof useSubmit>;
}) {
  return (
    <>
      {!state.online && (
        <Notice type="warning">当前离线，保存和测试暂不可用。</Notice>
      )}
      {state.error && (
        <Notice type="error">
          <span role="alert">{state.error}</span>
        </Notice>
      )}
    </>
  );
}
export function ConfirmAction({
  title,
  children,
  onClose,
  onConfirm,
  pending,
  error,
  requireReason = true,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  onConfirm: (reason: string) => Promise<boolean>;
  pending: boolean;
  error?: string;
  requireReason?: boolean;
}) {
  const [reason, setReason] = useState("");
  return (
    <Modal title={title} onClose={() => !pending && onClose()}>
      <div className="growth-stack">
        {error && (
          <Notice type="error">
            <span role="alert">{error}</span>
          </Notice>
        )}
        <Notice type="warning">{children}</Notice>
        {requireReason && (
          <label className="growth-reason">
            操作原因
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              maxLength={500}
              required
              placeholder="填写业务原因，将写入操作审计"
            />
          </label>
        )}
        <div className="button-row">
          <Button variant="secondary" disabled={pending} onClick={onClose}>
            取消
          </Button>
          <Button
            busy={pending}
            disabled={pending || (requireReason && !reason.trim())}
            onClick={async () => {
              if (await onConfirm(reason.trim())) onClose();
            }}
          >
            {pending ? "正在提交…" : "确认操作"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
export async function mutate<T>(path: string, body: unknown, method = "POST") {
  return growthRequest<T>(path, { method, body });
}
