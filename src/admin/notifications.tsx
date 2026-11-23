import { useQueryClient } from "@tanstack/react-query";
import {
  Bell,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  ClipboardList,
  Files,
  Printer,
  ReceiptText,
  RefreshCw,
  Truck,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Button,
  Empty,
  ErrorState,
  Loading,
  Notice,
  PageHeading,
  Panel,
} from "../components/ui";
import {
  getIdentityGeneration,
  growthRequest,
  useGrowthResource,
} from "../growth/client";
import { useIdentity } from "../growth/identity";
import { ADMIN, DEMO } from "../lib/api";
import type { components } from "../lib/api-schema";
import { formatDisplayTime } from "../lib/display-format";
import { useStore } from "../lib/store";
import {
  notificationsEndpoint,
  unreadNotificationsKey,
  useUnreadNotifications,
  type UnreadNotifications,
} from "./notification-unread";

type Notification = components["schemas"]["Notification"];
type NotificationPage = components["schemas"]["NotificationPage"];

const filters = [
  ["ALL", "全部"],
  ["UNREAD", "未读"],
  ["READ", "已读"],
  ["PENDING", "未处理"],
  ["HANDLED", "已处理"],
] as const;
type NotificationFilter = (typeof filters)[number][0];

const destinations = {
  PAYMENT_PENDING: { icon: ReceiptText, path: "orders/" },
  PAYMENT_MISMATCH: { icon: ReceiptText, path: "orders/" },
  PRINT_FAILED: { icon: Printer, path: "print/" },
  BINDING_REOPENED: { icon: Files, path: "binding?task=" },
  DELIVERY_EXCEPTION: { icon: CircleAlert, path: "exceptions?exception=" },
  LOCATION_EXCEPTION: { icon: CircleAlert, path: "exceptions?exception=" },
  AFTER_SALE_REQUESTED: { icon: ClipboardList, path: "after-sales/" },
  REFUND_PENDING: { icon: ReceiptText, path: "after-sales/" },
  BATCH_PENDING: { icon: Truck, path: "batches/" },
  BATCH_CHANGED: { icon: Truck, path: "batches/" },
} satisfies Record<Notification["kind"], { icon: typeof Bell; path: string }>;

export function AdminNotifications() {
  const { generation, staff } = useIdentity();
  return (
    <NotificationInbox
      key={`${generation}:${staff?.id}`}
      generation={generation}
      staffId={staff?.id}
    />
  );
}

function NotificationInbox({
  generation,
  staffId,
}: {
  generation: number;
  staffId?: string;
}) {
  const [position, setPosition] = useState<{
    filter: NotificationFilter;
    page: number;
  }>({
    filter: "ALL",
    page: 1,
  });
  const [seen, setSeen] = useState<ReadonlySet<string>>(new Set());
  const activeReads = useRef(new Set<string>());
  const readSequence = useRef(0);
  const latestConfirmedRead = useRef(0);
  const [pending, setPending] = useState<ReadonlySet<string>>(new Set());
  const [issue, setIssue] = useState<{
    id: string | null;
    message: string;
  } | null>(null);
  const queryClient = useQueryClient();
  const { toast } = useStore();
  const unreadKey = unreadNotificationsKey(generation, staffId);
  const unread = useUnreadNotifications(generation, staffId, !!staffId);
  const { data, error, loading, refreshing, reload } =
    useGrowthResource<NotificationPage>(
      DEMO
        ? null
        : `${notificationsEndpoint}?filter=${position.filter}&page=${position.page}`,
    );
  const pageCount = Math.max(
    1,
    Math.ceil((data?.total ?? 0) / (data?.pageSize ?? 20)),
  );
  useEffect(() => {
    if (data && position.page > pageCount)
      setPosition((current) => ({ ...current, page: pageCount }));
  }, [data, pageCount, position.page]);

  const refresh = async () => {
    await reload();
    await queryClient.invalidateQueries({ queryKey: unreadKey, exact: true });
  };
  const markRead = async (
    id: string | null,
    showErrorAfterNavigation = false,
  ) => {
    const action = id ? `${encodeURIComponent(id)}/read` : "read-all";
    if (
      activeReads.current.has(action) ||
      (!id && activeReads.current.size > 0)
    )
      return;
    activeReads.current.add(action);
    setPending(new Set(activeReads.current));
    setIssue(null);
    const requestGeneration = getIdentityGeneration();
    const sequence = ++readSequence.current;
    try {
      const result = await growthRequest<UnreadNotifications>(
        `${notificationsEndpoint}/${action}`,
        { method: "POST" },
      );
      await queryClient.cancelQueries({ queryKey: unreadKey, exact: true });
      if (getIdentityGeneration() !== requestGeneration) return;
      if (sequence > latestConfirmedRead.current) {
        latestConfirmedRead.current = sequence;
        queryClient.setQueryData(unreadKey, result);
      }
      const readIds = id ? [id] : (data?.items ?? []).map((item) => item.id);
      setSeen((previous) => new Set([...previous, ...readIds]));
      const updateUnread = queryClient.invalidateQueries({
        queryKey: unreadKey,
        exact: true,
      });
      if (!id || position.filter === "UNREAD")
        await Promise.all([updateUnread, reload()]);
      else await updateUnread;
    } catch (cause) {
      if (getIdentityGeneration() !== requestGeneration) return;
      const message =
        cause instanceof Error ? cause.message : "标记已读失败，请重试";
      if (showErrorAfterNavigation) toast(message, "warning");
      else setIssue({ id, message });
    } finally {
      activeReads.current.delete(action);
      setPending(new Set(activeReads.current));
    }
  };
  const unreadCount = unread.data?.unreadCount ?? data?.unreadCount ?? 0;
  const rows = (data?.items ?? []).filter(
    (item) => position.filter !== "UNREAD" || !seen.has(item.id),
  );

  return (
    <>
      <PageHeading
        title="通知中心"
        action={
          !DEMO && (
            <div className="notification-actions">
              <Button
                variant="secondary"
                className="notification-read-all"
                busy={pending.has("read-all")}
                disabled={
                  loading || refreshing || pending.size > 0 || unreadCount === 0
                }
                onClick={() => void markRead(null)}
              >
                一键已读
              </Button>
              <Button
                variant="secondary"
                className="notification-refresh"
                aria-label="刷新通知"
                title="刷新通知"
                busy={refreshing}
                disabled={loading || pending.size > 0}
                onClick={() => void refresh()}
              >
                <RefreshCw size={18} aria-hidden="true" />
              </Button>
            </div>
          )
        }
      />
      <Panel className="notification-center">
        <div
          className="notification-filters"
          role="group"
          aria-label="通知筛选"
        >
          {filters.map(([key, label]) => (
            <button
              key={key}
              type="button"
              disabled={pending.size > 0}
              aria-pressed={position.filter === key}
              onClick={() => {
                setIssue(null);
                setPosition({ filter: key, page: 1 });
              }}
            >
              {label}
            </button>
          ))}
        </div>
        {loading && <Loading label="正在读取通知" />}
        {issue?.id === null && (
          <Notice type="error">
            <span role="alert">{issue.message}</span>
          </Notice>
        )}
        {error &&
          (data ? (
            <Notice type="error">
              <span role="alert">{error}</span>
            </Notice>
          ) : (
            <ErrorState message={error} retry={refresh} />
          ))}
        {!loading && !error && rows.length === 0 && (
          <Empty
            title={
              position.filter === "UNREAD"
                ? "没有未读通知"
                : position.filter === "PENDING"
                  ? "没有待处理通知"
                  : "暂无通知"
            }
            text=""
          />
        )}
        <ul className="notification-list" aria-label="通知列表">
          {rows.map((item) => {
            const read = item.read || seen.has(item.id);
            const marking = pending.has(`${encodeURIComponent(item.id)}/read`);
            const { icon: Icon, path } = destinations[item.kind];
            return (
              <li key={item.id} className="notification-row" data-read={read}>
                {!read && (
                  <button
                    type="button"
                    className="notification-mark"
                    aria-label={`标记「${item.title}」为已读`}
                    aria-busy={marking}
                    disabled={marking || pending.has("read-all")}
                    onClick={() => void markRead(item.id)}
                  />
                )}
                <span className="notification-kind-icon">
                  <Icon size={20} aria-hidden="true" />
                </span>
                <div className="notification-content">
                  <div className="notification-summary">
                    <h2>{item.title}</h2>
                    <span className="notification-read-state">
                      {read ? "已读" : "未读"}
                    </span>
                    {item.taskStatus && (
                      <span
                        className="notification-task-state"
                        data-pending={item.taskStatus === "PENDING"}
                      >
                        {item.taskStatus === "PENDING" ? "待处理" : "已处理"}
                      </span>
                    )}
                  </div>
                  <div className="notification-meta">
                    <span>{item.context}</span>
                    <time dateTime={item.createdAt}>
                      {formatDisplayTime(item.createdAt)}
                    </time>
                  </div>
                  {issue?.id === item.id && (
                    <p className="notification-error" role="alert">
                      {issue.message}
                    </p>
                  )}
                </div>
                <Link
                  className="notification-detail"
                  to={`${ADMIN}/${path}${encodeURIComponent(item.targetId)}`}
                  onClick={() => {
                    if (!read) void markRead(item.id, true);
                  }}
                  onAuxClick={(event) => {
                    if (event.button === 1 && !read)
                      void markRead(item.id, true);
                  }}
                >
                  查看详情
                  <ChevronRight size={15} aria-hidden="true" />
                </Link>
              </li>
            );
          })}
        </ul>
        {pageCount > 1 && (
          <nav className="notification-pagination" aria-label="通知分页">
            <button
              type="button"
              className="icon-btn"
              aria-label="上一页"
              disabled={loading || pending.size > 0 || position.page <= 1}
              onClick={() =>
                setPosition((current) => ({
                  ...current,
                  page: current.page - 1,
                }))
              }
            >
              <ChevronLeft size={18} aria-hidden="true" />
            </button>
            <span>
              {position.page} / {pageCount}
            </span>
            <button
              type="button"
              className="icon-btn"
              aria-label="下一页"
              disabled={
                loading || pending.size > 0 || position.page >= pageCount
              }
              onClick={() =>
                setPosition((current) => ({
                  ...current,
                  page: current.page + 1,
                }))
              }
            >
              <ChevronRight size={18} aria-hidden="true" />
            </button>
          </nav>
        )}
      </Panel>
    </>
  );
}
