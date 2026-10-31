import { Suspense, useLayoutEffect, useRef } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { useStore, ADMIN, DEMO } from "../lib/store";
import { ErrorState, Notice } from "./ui";
import {
  RefreshStatus,
  ResourceSkeleton,
  TaskStatus,
  type SkeletonVariant,
} from "./feedback";
import { useReducedMotion } from "./presence";
function variantFor(path: string): SkeletonVariant {
  if (path.includes("/account")) return "profile";
  if (path.endsWith("/rewards")) return "coupons";
  if (path.includes("/print/")) return "paper";
  if (/\/(orders|customers|batches)\/[^/]+/.test(path)) return "detail";
  if (path === ADMIN) return "stats";
  return path.startsWith(ADMIN) ? "table" : "cards";
}
export function PageSkeleton() {
  const { pathname } = useLocation();
  return (
    <div className={pathname.startsWith("/u") ? "content-page" : ""}>
      <ResourceSkeleton variant={variantFor(pathname)} />
    </div>
  );
}
export function ResourceOutlet() {
  const {
    loading,
    error,
    refreshing,
    refreshError,
    refresh,
    actionIssues,
    clearActionIssues,
    resources,
  } = useStore();
  const { pathname } = useLocation();
  // Action errors describe the page they failed on; do not carry them (or
  // duplicate banners from an earlier attempt) into the next page.
  useLayoutEffect(() => {
    clearActionIssues();
  }, [pathname, clearActionIssues]);
  const reduced = useReducedMotion();
  const anchor = useRef<HTMLSpanElement>(null);
  const publicOrLocal =
    pathname.startsWith("/u") && !pathname.startsWith("/u/orders");
  const independent =
    publicOrLocal ||
    pathname === "/u/orders" ||
    pathname === `${ADMIN}/orders` ||
    /\/(account|customers|promotions\/records|settings\/(mail|mail-templates|promotions|volume-discounts))/.test(
      pathname,
    ) ||
    pathname.replace(/\/+$/, "") === ADMIN;
  useLayoutEffect(() => {
    if (reduced || loading || pathname.startsWith(ADMIN)) return;
    const parent = anchor.current?.parentElement;
    const target = parent?.querySelector<HTMLElement>(
      "main.content-page,.account-page,.courier-content>.panel,.courier-content>.page-heading",
    );
    const animation = target?.animate([{ opacity: 0 }, { opacity: 1 }], {
      duration: 150,
      easing: "cubic-bezier(.16,1,.3,1)",
    });
    return () => animation?.cancel();
  }, [pathname, reduced]);
  const issues = Object.entries(actionIssues).filter(
    ([path, text]) =>
      text &&
      (pathname.startsWith("/courier")
        ? path.startsWith("/courier")
        : pathname.startsWith(ADMIN)
          ? !path.startsWith("/courier")
          : !path.startsWith("/admin") && !path.startsWith("/courier")),
  );
  const config = resources.config;
  const needsConfig = /^\/u\/(new|review)(\/|$)/.test(pathname);
  return (
    <>
      <span ref={anchor} hidden />
      {!DEMO && needsConfig && config && !config.hasData && (
        <div className="content-page resource-config-status">
          {config.error ? (
            <Notice type="error">
              <span role="alert">{config.error}</span>{" "}
              <button className="text-link" onClick={() => void refresh()}>
                重新读取配置
              </button>
            </Notice>
          ) : (
            <TaskStatus label="正在读取服务配置，暂时不能上传或提交" />
          )}
        </div>
      )}
      {issues.map(([path, text]) => (
        <Notice key={path} type="error">
          <span role="alert">{text}</span>{" "}
          <button className="text-link" onClick={() => void refresh()}>
            刷新并核对状态
          </button>
        </Notice>
      ))}
      {!independent && !DEMO && loading ? (
        <PageSkeleton />
      ) : !independent && error ? (
        <ErrorState message={error} retry={refresh} />
      ) : (
        <>
          {!independent && !/^\/u\/orders\//.test(pathname) && (
            <RefreshStatus
              loading={refreshing && !/^\/u\/orders\//.test(pathname)}
              error={refreshError}
              retry={refresh}
            />
          )}
          <Suspense fallback={<PageSkeleton />}>
            <Outlet />
          </Suspense>
        </>
      )}
    </>
  );
}
