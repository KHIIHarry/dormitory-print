import type { ReactNode } from "react";
export function MotionFold({
  open,
  children,
}: {
  open: boolean;
  children: ReactNode;
}) {
  return (
    <div
      className="motion-fold"
      data-open={open}
      aria-hidden={!open}
      inert={!open}
    >
      <div>{children}</div>
    </div>
  );
}
