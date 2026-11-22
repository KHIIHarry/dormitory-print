import { useLayoutEffect, useRef, type ReactNode } from "react";
import { AlertCircle, CheckCircle2, Info, TriangleAlert } from "lucide-react";

export function Notice({
  children,
  type = "info",
  reveal,
  className = "",
  role,
}: {
  children: ReactNode;
  type?: string;
  reveal?: boolean;
  className?: string;
  role?: "alert" | "status";
}) {
  const ref = useRef<HTMLDivElement>(null);
  const lastText = useRef("");
  const Icon =
    type === "error"
      ? AlertCircle
      : type === "warning"
        ? TriangleAlert
        : type === "success"
          ? CheckCircle2
          : Info;
  useLayoutEffect(() => {
    const notice = ref.current;
    if (notice?.closest("[inert], [aria-hidden=true]")) return;
    if (!notice || !(reveal ?? (type === "error" && notice.closest(".modal"))))
      return;
    const message = notice.textContent ?? "";
    if (message === lastText.current) return;
    lastText.current = message;
    const bounds = notice.getBoundingClientRect();
    const modal = notice.closest(".modal")?.getBoundingClientRect();
    if (
      bounds.top < Math.max(modal?.top ?? 0, 0) + 12 ||
      bounds.bottom >
        Math.min(modal?.bottom ?? window.innerHeight, window.innerHeight) - 12
    ) {
      notice.scrollIntoView({
        block: "nearest",
        inline: "nearest",
        behavior: "instant",
      });
    }
  });
  return (
    <div
      ref={ref}
      className={`notice ${type} ${className}`.trim()}
      role={
        role ??
        (reveal && typeof children === "string" && type === "error"
          ? "alert"
          : undefined)
      }
    >
      <Icon size={16} aria-hidden="true" />
      <div>{children}</div>
    </div>
  );
}
