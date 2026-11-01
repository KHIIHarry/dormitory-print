import { Component, type ErrorInfo, type ReactNode } from "react";
import {
  isChunkLoadError,
  reloadOnceForChunkError,
} from "../lib/chunk-recovery";
import "./error-boundary.css";

type Scope = "root" | "route";

function toError(error: unknown): Error {
  if (error instanceof Error) return error;
  if (typeof error === "string") return new Error(error);
  return new Error("页面渲染时遇到未知异常", { cause: error });
}

export function ErrorFallback({
  error,
  scope = "route",
  recovering = false,
  onRetry,
}: {
  error: Error | null;
  scope?: Scope;
  recovering?: boolean;
  onRetry?: () => void;
}) {
  const chunkError = isChunkLoadError(error);
  const Heading = scope === "root" ? "h1" : "h2";
  return (
    <section
      className={`app-error app-error--${scope}`}
      data-error-kind={chunkError ? "chunk" : "render"}
      role="alert"
    >
      <div className="app-error-card">
        <span className="app-error-brand">
          DormPrint<small>宿舍打印</small>
        </span>
        {recovering ? (
          <>
            <Heading className="app-error-title">正在更新到最新版本…</Heading>
            <p className="app-error-text">
              页面资源刚刚更新，正在自动刷新。若浏览器没有自动跳转，请点击下方按钮。
            </p>
            <div className="app-error-actions">
              <button
                type="button"
                className="btn"
                onClick={() => window.location.reload()}
              >
                立即刷新
              </button>
            </div>
          </>
        ) : chunkError ? (
          <>
            <Heading className="app-error-title">页面资源加载失败</Heading>
            <p className="app-error-text">
              可能是应用刚刚发布了新版本，或者网络连接中断。刷新页面即可继续；未提交的填写内容可能需要重新输入。
            </p>
            <div className="app-error-actions">
              <button
                type="button"
                className="btn"
                onClick={() => window.location.reload()}
              >
                刷新页面
              </button>
              <a className="btn secondary" href="/u/">
                返回首页
              </a>
            </div>
          </>
        ) : (
          <>
            <Heading className="app-error-title">页面暂时出了点问题</Heading>
            <p className="app-error-text">
              页面渲染时遇到异常，已停止加载以保护你的数据。可以重试；如果问题持续，请刷新页面或联系管理员。
            </p>
            <div className="app-error-actions">
              {onRetry && (
                <button type="button" className="btn" onClick={onRetry}>
                  重试
                </button>
              )}
              <button
                type="button"
                className={onRetry ? "btn secondary" : "btn"}
                onClick={() => window.location.reload()}
              >
                刷新页面
              </button>
              <a className="btn secondary" href="/u/">
                返回首页
              </a>
            </div>
            {error?.message && (
              <details className="app-error-detail">
                <summary>错误信息</summary>
                <code>{error.message}</code>
              </details>
            )}
          </>
        )}
      </div>
    </section>
  );
}

/**
 * Catches render/lazy-import failures so they never unmount the whole app
 * into a blank page. Route-level boundaries keep the surrounding navigation
 * usable; the root boundary is the final safety net for providers and shell
 * code.
 */
export class AppErrorBoundary extends Component<
  { children: ReactNode; scope?: Scope; resetKey?: string },
  { error: Error | null; recovering: boolean }
> {
  state = { error: null as Error | null, recovering: false };

  static getDerivedStateFromError(error: unknown) {
    return { error: toError(error), recovering: false };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error("[dormprint] 页面渲染失败", error, info.componentStack || "");
    if (isChunkLoadError(error) && reloadOnceForChunkError())
      this.setState({ recovering: true });
  }

  componentDidUpdate(previous: { resetKey?: string }) {
    if (previous.resetKey !== this.props.resetKey && this.state.error)
      this.setState({ error: null, recovering: false });
  }

  private reset = () => this.setState({ error: null, recovering: false });

  render() {
    if (this.state.error)
      return (
        <ErrorFallback
          error={this.state.error}
          scope={this.props.scope ?? "route"}
          recovering={this.state.recovering}
          onRetry={this.reset}
        />
      );
    return this.props.children;
  }
}
