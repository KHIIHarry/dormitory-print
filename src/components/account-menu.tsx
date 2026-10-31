import { useEffect, useId, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { ChevronDown, ClipboardList, ShieldCheck, User } from "lucide-react";
import { useIdentity } from "../growth/identity";
import { MOTION, usePresence } from "./presence";
import "./account-menu.css";

export function AccountMenu() {
  const { customer } = useIdentity();
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  const { present, phase } = usePresence(open, MOTION.local);
  useEffect(
    () => setOpen(false),
    [location.pathname, location.search, customer.authKind],
  );
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        trigger.current?.focus({ preventScroll: true });
      }
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  if (customer.authKind !== "REGISTERED")
    return (
      <Link className="header-profile" to="/u/account/login">
        <span className="avatar">
          <User size={17} />
        </span>
        <span>登录 / 注册</span>
      </Link>
    );
  return (
    <div
      className="account-menu"
      ref={root}
      onBlur={(event) => {
        if (
          event.relatedTarget &&
          !event.currentTarget.contains(event.relatedTarget as Node)
        )
          setOpen(false);
      }}
    >
      <button
        type="button"
        ref={trigger}
        id={`${id}-trigger`}
        className="header-profile account-menu-trigger"
        aria-label="我的账户"
        aria-expanded={open}
        aria-controls={`${id}-panel`}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="avatar">
          <User size={17} />
        </span>
        <span>我的账户</span>
        <ChevronDown size={14} />
      </button>
      {present && (
        <div
          id={`${id}-panel`}
          className="account-menu-panel"
          data-state={phase}
          aria-labelledby={`${id}-trigger`}
          inert={!open}
        >
          <Link to="/u/account">
            <User size={17} />
            我的账户
          </Link>
          <Link to="/u/account/security">
            <ShieldCheck size={17} />
            账号安全
          </Link>
          <Link to="/u/orders">
            <ClipboardList size={17} />
            我的订单
          </Link>
        </div>
      )}
    </div>
  );
}
