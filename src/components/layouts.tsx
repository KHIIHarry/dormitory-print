import { AccountMenu } from "./account-menu";
import { ResourceOutlet } from "./resource-outlet";
import { MOTION, usePresence } from "./presence";
import {
  adminAccount,
  adminHome,
  activeAdminGroup,
  activeAdminItem,
  visibleAdminGroups,
} from "../admin/navigation";
import "../admin/navigation.css";
import { useEffect, useRef, useState } from "react";
import {
  NavLink,
  Link,
  Navigate,
  Outlet,
  useLocation,
  useNavigate,
} from "react-router-dom";
import {
  ArrowUpRight,
  Bell,
  BookOpen,
  ChevronDown,
  ChevronRight,
  ClipboardList,
  Home,
  LifeBuoy,
  LogOut,
  Menu,
  ShieldCheck,
  ShoppingBag,
  Truck,
  User,
  Users,
  WifiOff,
  X,
} from "lucide-react";
import { adminNavAllowed } from "../admin/resources";
import { ADMIN, DEMO, useStore } from "../lib/store";
import { useIdentity } from "../growth/identity";
import { Button, Logo, Notice, Loading, ErrorState } from "./ui";
import {
  adminWorkspaceTarget,
  courierWorkspaceAllowed,
  customerLoginPath,
  ReturnToCustomerLink,
  WorkspaceSwitch,
} from "./role-switch";
function Network() {
  const [online, setOnline] = useState(navigator.onLine);
  useEffect(() => {
    const fn = () => setOnline(navigator.onLine);
    window.addEventListener("online", fn);
    window.addEventListener("offline", fn);
    return () => {
      window.removeEventListener("online", fn);
      window.removeEventListener("offline", fn);
    };
  }, []);
  return !online ? (
    <div className="offline-bar">
      <WifiOff size={16} />
      当前离线 · 暂停业务提交，联网后会刷新状态
    </div>
  ) : null;
}
function DemoStrip() {
  return DEMO ? (
    <div className="demo-strip">
      <span>前端演示</span>示例订单与价格仅用于交互预览，刷新后重置
    </div>
  ) : null;
}
export function CustomerLayout() {
  const { loading, error, refresh } = useStore();
  const { customer } = useIdentity();
  const loc = useLocation();
  const independent =
    loc.pathname.startsWith("/u/account") || loc.pathname === "/u/order-link";
  const home = loc.pathname === "/u/" || loc.pathname === "/u";
  const nav = [
    ["首页", "/u/", Home],
    [
      customer.authKind === "REGISTERED" ? "我的订单" : "本机订单",
      "/u/orders",
      ClipboardList,
    ],
    ["优惠券", "/u/rewards", ShoppingBag],
    ...(customer.authKind === "REGISTERED"
      ? [["邀请好友", "/u/invite", Users] as const]
      : []),
    ["帮助中心", "/u/help", LifeBuoy],
  ] as const;
  return (
    <div className={`customer-app ${home ? "is-home" : ""}`}>
      <Network />
      <header className="site-header">
        <div className="site-header-inner">
          <Link to="/u/" aria-label="宿舍打印首页">
            <Logo />
          </Link>
          <nav>
            {nav.map(([label, to, Icon]) => (
              <NavLink end={to === "/u/"} key={to} to={to}>
                <Icon size={17} />
                {label}
              </NavLink>
            ))}
          </nav>
          <AccountMenu />
        </div>
      </header>
      <DemoStrip />
      <ResourceOutlet />
      <footer className="site-footer">
        <Logo small />
        <span>从一份资料，到一次安心送达。</span>
        <span>用心打印，认真对待每一页</span>
      </footer>
      <nav className="bottom-nav">
        {[
          ["首页", "/u/", Home],
          ["订单", "/u/orders", ClipboardList],
          ["我的", "/u/account", User],
        ].map(([label, to, Icon]) => (
          <NavLink key={String(to)} end={to === "/u/"} to={String(to)}>
            {typeof Icon !== "string" && <Icon size={22} />}
            <span>{String(label)}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
export function StaffGuard({ courier = false }: { courier?: boolean }) {
  const { authReady } = useStore();
  const { staff, customer, customerReady } = useIdentity();
  const loc = useLocation();
  if (!authReady || !customerReady) return <Loading label="正在核实员工身份" />;
  if (!staff) {
    // A signed-in customer without an effective staff role is an authorization
    // failure, not an anonymous visitor. Sending them back to the login page
    // would loop through the safe-return target forever.
    if (customer.authKind === "REGISTERED" && customer.account?.verified)
      return (
        <Navigate to="/u/account" state={{ staffError: "forbidden" }} replace />
      );
    return (
      <Navigate
        to={customerLoginPath(`${loc.pathname}${loc.search}`)}
        replace
      />
    );
  }
  const allowed = courier
    ? courierWorkspaceAllowed(staff)
    : staff.role === "ADMIN";
  if (!allowed)
    return (
      <Navigate to="/u/account" state={{ staffError: "forbidden" }} replace />
    );
  if (
    staff.mustChangePassword &&
    loc.pathname !== `${courier ? "/courier" : ADMIN}/account`
  )
    return <Navigate to={`${courier ? "/courier" : ADMIN}/account`} replace />;
  if (
    !courier &&
    !adminNavAllowed(
      loc.pathname,
      staff.capabilities ?? [],
      staff.isSuperAdmin === true,
    )
  )
    return (
      <Navigate
        to={adminWorkspaceTarget(staff, ADMIN)}
        state={{ staffError: "forbidden" }}
        replace
      />
    );
  return <Outlet />;
}
export function AdminLayout() {
  const { role, toast, loading, error, refresh } = useStore();
  const { staff, logoutCustomer } = useIdentity();
  const loc = useLocation();
  const navigate = useNavigate();
  const [menu, setMenu] = useState(false);
  const drawerPresence = usePresence(menu, MOTION.expand);
  const drawerButton = useRef<HTMLButtonElement>(null);
  const drawerCloseButton = useRef<HTMLButtonElement>(null);
  const [isMobile, setIsMobile] = useState(
    () => window.matchMedia("(max-width: 767px)").matches,
  );
  useEffect(() => setMenu(false), [loc.pathname]);
  useEffect(() => {
    const mobile = window.matchMedia("(max-width: 767px)");
    const sync = () => {
      setIsMobile(mobile.matches);
      if (!mobile.matches) setMenu(false);
    };
    mobile.addEventListener("change", sync);
    return () => mobile.removeEventListener("change", sync);
  }, []);
  useEffect(() => {
    if (!drawerPresence.present) return;
    drawerCloseButton.current?.focus({ preventScroll: true });
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenu(false);
      if (event.key === "Tab") {
        const aside = document.getElementById("admin-navigation-sidebar");
        const controls = Array.from(
          aside?.querySelectorAll<HTMLElement>(
            "button:not(:disabled),a[href]",
          ) || [],
        ).filter(
          (el) => !el.closest("[inert]") && el.getClientRects().length > 0,
        );
        const first = controls[0],
          last = controls.at(-1);
        if (!menu) {
          event.preventDefault();
          return;
        }
        if (!aside?.contains(document.activeElement)) {
          event.preventDefault();
          (event.shiftKey ? last : first)?.focus();
        } else if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", onKey);
      drawerButton.current?.focus({ preventScroll: true });
    };
  }, [drawerPresence.present]);
  const currentGroup = activeAdminGroup(loc.pathname);
  const [openGroup, setOpenGroup] = useState<string | null>(
    () => currentGroup?.id || null,
  );
  useEffect(() => {
    setOpenGroup(currentGroup?.id || null);
  }, [loc.pathname, currentGroup?.id, staff?.id]);
  const groups = visibleAdminGroups(
    staff?.capabilities ?? [],
    staff?.mustChangePassword,
    staff?.isSuperAdmin === true,
  );
  const label = activeAdminItem(loc.pathname)?.label || "详情";
  const HomeIcon = adminHome.icon;
  const AccountIcon = adminAccount.icon;
  const logout = async () => {
    // One account session: signing out of a staff workspace must also clear the
    // customer identity instead of leaving a stale registered customer behind.
    await logoutCustomer().catch(() => {});
    navigate(`${ADMIN}/login`, { replace: true });
  };
  return (
    <div className="admin-app">
      <Network />
      <aside
        id="admin-navigation-sidebar"
        inert={isMobile && !menu}
        aria-hidden={isMobile && !menu}
        className={`admin-sidebar ${menu ? "open" : ""}`}
      >
        <button
          type="button"
          className="icon-btn mobile-only admin-sidebar-close"
          aria-label="关闭导航"
          ref={drawerCloseButton}
          onClick={() => setMenu(false)}
        >
          <X size={18} />
        </button>
        <Link to={staff ? adminWorkspaceTarget(staff, ADMIN) : "/u/account"}>
          <Logo small />
        </Link>
        <div className="workspace-label">
          管理工作空间<span>ADMIN</span>
        </div>
        <nav className="admin-nav" aria-label="管理导航">
          {!staff?.mustChangePassword &&
            adminNavAllowed(
              adminHome.to,
              staff?.capabilities ?? [],
              staff?.isSuperAdmin === true,
            ) && (
              <NavLink to={adminHome.to} end className="admin-nav-home">
                <HomeIcon size={18} />
                <span>{adminHome.label}</span>
              </NavLink>
            )}
          {groups.map((group) => {
            const expanded = openGroup === group.id;
            const Icon = group.icon;
            const current = currentGroup?.id === group.id;
            return (
              <div
                key={group.id}
                className={`admin-nav-group${expanded ? " is-open" : ""}${current ? " contains-current" : ""}`}
              >
                <button
                  type="button"
                  className="admin-nav-toggle"
                  aria-expanded={expanded}
                  aria-controls={`admin-nav-${group.id}`}
                  onClick={() => setOpenGroup(expanded ? null : group.id)}
                >
                  <Icon size={18} />
                  <span>{group.label}</span>
                  <ChevronDown
                    size={15}
                    className="admin-nav-chevron"
                    aria-hidden="true"
                  />
                </button>
                <div
                  id={`admin-nav-${group.id}`}
                  className="admin-nav-children"
                  aria-hidden={!expanded}
                  inert={!expanded}
                >
                  <div className="admin-nav-clip">
                    <ul className="admin-subnav">
                      {group.items.map((item) => (
                        <li key={item.to}>
                          <NavLink to={item.to} end={item.end}>
                            <span>{item.label}</span>
                          </NavLink>
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              </div>
            );
          })}
          <div className="admin-nav-utility">
            <NavLink to={adminAccount.to} end={adminAccount.end}>
              <AccountIcon size={18} />
              <span>{adminAccount.label}</span>
            </NavLink>
          </div>
        </nav>
        <div className="sidebar-footer">
          <ShieldCheck size={18} />
          <div>
            安全管理工作台<small>仅限授权员工访问</small>
          </div>
        </div>
      </aside>
      {drawerPresence.present && (
        <div
          className="sidebar-scrim"
          data-state={drawerPresence.phase}
          onClick={() => setMenu(false)}
        />
      )}
      <div className="admin-main" inert={isMobile && drawerPresence.present}>
        <header className="admin-header">
          <button
            className="icon-btn mobile-only"
            aria-label="展开导航"
            aria-expanded={menu}
            aria-controls="admin-navigation-sidebar"
            ref={drawerButton}
            onClick={() => setMenu(!menu)}
          >
            <Menu size={21} />
          </button>
          <div className="breadcrumbs">
            工作空间
            <ChevronRight size={14} />
            <strong>{label}</strong>
          </div>
          <div className="admin-header-right">
            <ReturnToCustomerLink />
            <WorkspaceSwitch current="admin" />
            <span className="today">
              {new Date().toLocaleDateString("zh-CN", {
                year: "numeric",
                month: "long",
                day: "numeric",
                weekday: "long",
              })}
            </span>
            {adminNavAllowed(
              `${ADMIN}/exceptions`,
              staff?.capabilities ?? [],
              staff?.isSuperAdmin === true,
            ) && (
              <button
                className="icon-btn"
                aria-label="查看异常提醒"
                onClick={() => navigate(`${ADMIN}/exceptions`)}
              >
                <Bell size={19} />
                <i className="notification-dot" />
              </button>
            )}
            <Link
              to={`${ADMIN}/account`}
              className="avatar admin-avatar"
              aria-label="个人账号"
            >
              {staff?.name?.slice(0, 1) || "管"}
            </Link>
            <div className="user-name">
              {staff?.name ||
                (staff?.isSuperAdmin
                  ? "超级管理员"
                  : role === "ADMIN"
                    ? "管理员"
                    : "员工")}
              <small>
                {staff?.isSuperAdmin
                  ? "超级管理员"
                  : role === "ADMIN"
                    ? "管理员"
                    : "员工"}{" "}
                · 校园打印工作室
              </small>
            </div>
            <button className="icon-btn" aria-label="退出登录" onClick={logout}>
              <LogOut size={18} />
            </button>
          </div>
        </header>
        <DemoStrip />
        <main className="admin-content">
          <ResourceOutlet />
        </main>
        <footer className="admin-footer">
          DormPrint 管理工作台<span>认真对待每一份托付</span>
        </footer>
      </div>
    </div>
  );
}
export function CourierLayout() {
  return (
    <div className="courier-app">
      <Network />
      <header className="site-header">
        <div className="site-header-inner">
          <Link to="/courier/">
            <Logo small />
          </Link>
          <span className="courier-label" aria-label="配送工作台">
            <Truck size={17} />
            配送工作台
          </span>
          <ReturnToCustomerLink />
          <WorkspaceSwitch current="courier" />
          <Link
            className="avatar"
            to="/courier/account"
            aria-label="配送员账户"
          >
            <User size={20} />
          </Link>
        </div>
      </header>
      <DemoStrip />
      <main className="courier-content">
        <ResourceOutlet />
      </main>
      <nav className="bottom-nav courier-nav">
        <NavLink to="/courier/" end>
          <Truck size={23} />
          <span>配送任务</span>
        </NavLink>
        <NavLink to="/courier/account">
          <User size={23} />
          <span>我的</span>
        </NavLink>
      </nav>
    </div>
  );
}
