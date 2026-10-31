import React, {
  Suspense,
  lazy,
  useEffect,
  useLayoutEffect,
  useRef,
} from "react";
import ReactDOM from "react-dom/client";
import {
  BrowserRouter,
  Link,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useNavigationType,
} from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@fontsource/sora/400.css";
import "@fontsource/sora/500.css";
import "@fontsource/sora/600.css";
import "@fontsource/sora/700.css";
import "./styles.css";
import { IdentityProvider, useIdentity } from "./growth/identity";
import { PreviewHub } from "./preview";
import { DEMO } from "./lib/api";
import { bootstrapUnifiedPwa } from "./lib/pwa";
import { safeStaffReturn } from "./components/role-switch";
import { Provider, ADMIN } from "./lib/store";
import {
  CustomerLayout,
  AdminLayout,
  CourierLayout,
  StaffGuard,
} from "./components/layouts";
import { Empty, Loading } from "./components/ui";
import { Home } from "./customer/home";
import { Login } from "./admin/login";
const AdminBenefits = lazy(() => import("./v1/benefits").then((m) => ({ default: m.AdminBenefitsPage })));
const AdminQrTools = lazy(() => import("./v1/qr-tools").then((m) => ({ default: m.AdminQRCodeToolsPage })));
const AdminPayroll = lazy(() => import("./v1/payroll").then((m) => ({ default: m.AdminPayrollPage })));
const Upload = lazy(() =>
  import("./customer/upload").then((m) => ({ default: m.UploadPage })),
);
const Settings = lazy(() =>
  import("./customer/settings").then((m) => ({ default: m.SettingsPage })),
);
const Photo = lazy(() =>
  import("./customer/settings").then((m) => ({ default: m.PhotoPage })),
);
const Review = lazy(() =>
  import("./customer/review").then((m) => ({ default: m.Review })),
);
const OrderList = lazy(() =>
  import("./customer/orders").then((m) => ({ default: m.OrderList })),
);
const OrderDetail = lazy(() =>
  import("./customer/orders").then((m) => ({ default: m.OrderDetail })),
);
const Contact = lazy(() =>
  import("./customer/orders").then((m) => ({ default: m.Contact })),
);
const Help = lazy(() =>
  import("./customer/help").then((m) => ({ default: m.Help })),
);
const Dashboard = lazy(() =>
  import("./admin/dashboard").then((m) => ({ default: m.Dashboard })),
);
const AdminOrders = lazy(() =>
  import("./admin/orders").then((m) => ({ default: m.AdminOrders })),
);
const AdminOrder = lazy(() =>
  import("./admin/orders").then((m) => ({ default: m.AdminOrderDetail })),
);
const Print = lazy(() =>
  import("./admin/production").then((m) => ({ default: m.PrintCenter })),
);
const PrintTask = lazy(() =>
  import("./admin/production").then((m) => ({ default: m.PrintTask })),
);
const Binding = lazy(() =>
  import("./admin/production").then((m) => ({ default: m.BindingCenter })),
);
const Packing = lazy(() =>
  import("./admin/production").then((m) => ({ default: m.PackingCenter })),
);
const Batches = lazy(() =>
  import("./admin/logistics").then((m) => ({ default: m.Batches })),
);
const Batch = lazy(() =>
  import("./admin/logistics").then((m) => ({ default: m.BatchDetail })),
);
const Slots = lazy(() =>
  import("./admin/logistics").then((m) => ({ default: m.Slots })),
);
const MapPage = lazy(() =>
  import("./admin/operations").then((m) => ({ default: m.MapEditor })),
);
const ConfigPage = lazy(() =>
  import("./admin/operations").then((m) => ({ default: m.Settings })),
);
const Exceptions = lazy(() =>
  import("./admin/operations").then((m) => ({ default: m.Exceptions })),
);
const Trash = lazy(() =>
  import("./admin/operations").then((m) => ({ default: m.Trash })),
);
const Audit = lazy(() =>
  import("./admin/operations").then((m) => ({ default: m.Audit })),
);
const CourierHome = lazy(() =>
  import("./courier/pages").then((m) => ({ default: m.CourierHome })),
);
const CourierBatch = lazy(() =>
  import("./courier/pages").then((m) => ({ default: m.CourierBatch })),
);
const CourierOrder = lazy(() =>
  import("./courier/pages").then((m) => ({ default: m.CourierOrder })),
);
const Proof = lazy(() =>
  import("./courier/pages").then((m) => ({ default: m.DeliveryProof })),
);
const Exception = lazy(() =>
  import("./courier/pages").then((m) => ({ default: m.CourierException })),
);
const AccountLogin = lazy(() =>
  import("./customer/account").then((m) => ({ default: m.AccountLoginPage })),
);
const AccountRegister = lazy(() =>
  import("./customer/account").then((m) => ({
    default: m.AccountRegisterPage,
  })),
);
const AccountVerify = lazy(() =>
  import("./customer/account").then((m) => ({ default: m.AccountVerifyPage })),
);
const AccountForgot = lazy(() =>
  import("./customer/account").then((m) => ({ default: m.AccountForgotPage })),
);
const AccountReset = lazy(() =>
  import("./customer/account").then((m) => ({ default: m.AccountResetPage })),
);
const Account = lazy(() =>
  import("./customer/account").then((m) => ({ default: m.AccountPage })),
);
const AccountSecurity = lazy(() =>
  import("./customer/account").then((m) => ({
    default: m.AccountSecurityPage,
  })),
);
const Rewards = lazy(() =>
  import("./customer/account").then((m) => ({ default: m.RewardsPage })),
);
const Invite = lazy(() =>
  import("./customer/account").then((m) => ({ default: m.InvitePage })),
);
const OrderLink = lazy(() =>
  import("./customer/account").then((m) => ({ default: m.OrderLinkPage })),
);
const Customers = lazy(() =>
  import("./admin/growth").then((m) => ({ default: m.CustomersPage })),
);
const CustomerDetail = lazy(() =>
  import("./admin/growth").then((m) => ({ default: m.CustomerDetailPage })),
);
const MailSettings = lazy(() =>
  import("./admin/growth").then((m) => ({ default: m.MailSettingsPage })),
);
const MailTemplates = lazy(() =>
  import("./admin/growth").then((m) => ({ default: m.MailTemplatesPage })),
);
const Promotions = lazy(() =>
  import("./admin/growth").then((m) => ({ default: m.PromotionSettingsPage })),
);
const PromotionRecords = lazy(() =>
  import("./admin/growth").then((m) => ({ default: m.PromotionRecordsPage })),
);
const VolumeDiscounts = lazy(() =>
  import("./admin/growth").then((m) => ({ default: m.VolumeDiscountsPage })),
);
const StaffAccount = lazy(() =>
  import("./admin/staff-account").then((m) => ({
    default: m.StaffAccountPage,
  })),
);
const Staff = lazy(() =>
  import("./admin/staff-account").then((m) => ({
    default: m.StaffManagementPage,
  })),
);
const AdminAfterSales = lazy(() =>
  import("./v1/admin").then((m) => ({ default: m.AdminAfterSalesPage })),
);
const AdminAfterSaleDetail = lazy(() =>
  import("./v1/admin").then((m) => ({ default: m.AdminAfterSaleDetail })),
);
const AdminDataOperations = lazy(() =>
  import("./v1/admin").then((m) => ({ default: m.AdminDataOperationsPage })),
);
const CustomerAfterSales = lazy(() =>
  import("./v1/customer").then((m) => ({ default: m.CustomerAfterSalesPage })),
);
const CustomerAccountData = lazy(() =>
  import("./v1/customer").then((m) => ({ default: m.CustomerAccountDataPage })),
);
import { createRouteScrollMemory } from "./lib/route-scroll";
function AppMeta() {
  const { pathname, key } = useLocation();
  const navigationType = useNavigationType();
  const positions = useRef(createRouteScrollMemory());
  const previousPath = useRef(pathname);
  useEffect(() => {
    const previous = history.scrollRestoration;
    history.scrollRestoration = "manual";
    return () => {
      history.scrollRestoration = previous;
    };
  }, []);
  useLayoutEffect(() => {
    const changedPage = previousPath.current !== pathname;
    previousPath.current = pathname;
    const y =
      navigationType === "POP"
        ? positions.current.position(key, navigationType)
        : changedPage
          ? 0
          : window.scrollY;
    let pendingRestore = navigationType === "POP" && y > 0;
    let focusAllowed = true;
    const restore = () => {
      if (!pendingRestore) return;
      window.scrollTo({ top: y, left: 0, behavior: "instant" });
      if (Math.abs(window.scrollY - y) < 1) pendingRestore = false;
    };
    window.scrollTo({ top: y, left: 0, behavior: "instant" });
    restore();
    // A lazy page or its first resource result may be taller than its fallback.
    const sizeObserver = new ResizeObserver(restore);
    sizeObserver.observe(document.getElementById("root")!);
    const stopRestore = () => {
      pendingRestore = false;
      focusAllowed = false;
      sizeObserver.disconnect();
    };
    window.addEventListener("wheel", stopRestore, { passive: true });
    window.addEventListener("touchstart", stopRestore, { passive: true });
    window.addEventListener("keydown", stopRestore);
    window.addEventListener("pointerdown", stopRestore, true);
    const save = () => {
      if (!pendingRestore) positions.current.save(key, window.scrollY);
    };
    window.addEventListener("scroll", save, { passive: true });
    // Suspended pages may mount their title after navigation commits.
    let observer: MutationObserver | undefined;
    const focusTitle = () => {
      if (!focusAllowed) {
        observer?.disconnect();
        return true;
      }
      const title = document.querySelector<HTMLElement>(
        "main h1, .page-heading h1",
      );
      if (!title) return false;
      const previous = title.getAttribute("tabindex");
      title.setAttribute("tabindex", "-1");
      title.focus({ preventScroll: true });
      if (previous === null) title.removeAttribute("tabindex");
      else title.setAttribute("tabindex", previous);
      observer?.disconnect();
      return true;
    };
    const frame = changedPage
      ? requestAnimationFrame(() => {
          if (!focusTitle()) {
            observer = new MutationObserver(() => {
              focusTitle();
            });
            observer.observe(document.getElementById("root")!, {
              childList: true,
              subtree: true,
            });
          }
        })
      : 0;
    return () => {
      window.removeEventListener("scroll", save);
      window.removeEventListener("wheel", stopRestore);
      window.removeEventListener("touchstart", stopRestore);
      window.removeEventListener("keydown", stopRestore);
      window.removeEventListener("pointerdown", stopRestore, true);
      sizeObserver.disconnect();
      cancelAnimationFrame(frame);
      observer?.disconnect();
    };
  }, [key, pathname, navigationType]);
  useEffect(() => {
    const base = pathname.startsWith(ADMIN)
      ? ADMIN
      : pathname.startsWith("/courier")
        ? "/courier"
        : "/u";
    document.title =
      base === ADMIN
        ? "DormPrint 管理工作台"
        : base === "/courier"
          ? "DormPrint 校园配送"
          : "DormPrint 宿舍打印";
  }, [pathname]);
  useEffect(() => {
    void bootstrapUnifiedPwa({
      production: import.meta.env.PROD,
      adminBase: ADMIN,
    });
  }, []);
  return null;
}
const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: true } },
});
function CustomerLoginRoute() {
  const { customer } = useIdentity();
  const location = useLocation();
  const navigate = useNavigate();
  const target = safeStaffReturn(
    new URLSearchParams(location.search).get("returnTo"),
  );
  useEffect(() => {
    if (
      target &&
      customer.authKind === "REGISTERED" &&
      customer.account?.verified
    )
      navigate(target, { replace: true });
  }, [target, customer.authKind, customer.account?.verified, navigate]);
  return (
    <Suspense fallback={<Loading />}>
      <AccountLogin />
    </Suspense>
  );
}
// N05 retired staff passwords. Keep old links out of the 410 staff forms and
// hand them to the base customer account flow; preserve query/hash tokens.
const legacyCredentialRoutes = {
  setup: "/u/account/register",
  forgot: "/u/account/forgot",
  reset: "/u/account/reset",
  verify: "/u/account/verify",
} as const;

function LegacyCredentialRedirect({ to }: { to: string }) {
  const location = useLocation();
  return (
    <Navigate
      replace
      to={{ pathname: to, search: location.search, hash: location.hash }}
    />
  );
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <IdentityProvider>
          <Provider>
            <AppMeta />
            <Routes>
              {DEMO && <Route path="/preview" element={<PreviewHub />} />}
              <Route path="/" element={<Navigate to="/u/" replace />} />
              <Route path="/u" element={<CustomerLayout />}>
                <Route index element={<Home />} />
                <Route path="new" element={<Upload />} />
                <Route path="new/settings" element={<Settings />} />
                <Route path="new/photo/:id" element={<Photo />} />
                <Route path="review" element={<Review />} />
                <Route path="orders" element={<OrderList />} />
                <Route path="orders/:id" element={<OrderDetail />} />
                <Route path="orders/:id/contact" element={<Contact />} />
                <Route path="orders/:id/after-sales" element={<CustomerAfterSales />} />
                <Route path="help" element={<Help />} />
                <Route
                  path="profile"
                  element={<Navigate to="/u/account" replace />}
                />
                <Route path="account/login" element={<CustomerLoginRoute />} />
                <Route path="account/register" element={<AccountRegister />} />
                <Route path="account/verify" element={<AccountVerify />} />
                <Route path="account/forgot" element={<AccountForgot />} />
                <Route path="account/reset" element={<AccountReset />} />
                <Route path="account" element={<Account />} />
                <Route path="account/security" element={<AccountSecurity />} />
                <Route path="account/data" element={<CustomerAccountData />} />
                <Route path="rewards" element={<Rewards />} />
                <Route path="invite" element={<Invite />} />
                <Route path="order-link" element={<OrderLink />} />
              </Route>
              <Route path={`${ADMIN}/login`} element={<Login />} />
              {(["setup", "forgot", "reset", "verify"] as const).map((mode) => (
                <Route
                  key={mode}
                  path={`${ADMIN}/account/${mode}`}
                  element={
                    <LegacyCredentialRedirect
                      to={legacyCredentialRoutes[mode]}
                    />
                  }
                />
              ))}
              <Route element={<StaffGuard />}>
                <Route path={ADMIN} element={<AdminLayout />}>
                  <Route index element={<Dashboard />} />
                  <Route path="orders" element={<AdminOrders />} />
                  <Route path="orders/:id" element={<AdminOrder />} />
                  <Route path="after-sales" element={<AdminAfterSales />} />
                  <Route path="after-sales/:id" element={<AdminAfterSaleDetail />} />
                  <Route path="data-operations" element={<AdminDataOperations />} />
                  <Route path="payroll" element={<AdminPayroll />} />
                  <Route path="settings/benefits" element={<AdminBenefits />} />
                  <Route path="qr-tools" element={<AdminQrTools />} />
                  <Route path="print" element={<Print />} />
                  <Route path="print/:id" element={<PrintTask />} />
                  <Route path="binding" element={<Binding />} />
                  <Route path="packing" element={<Packing />} />
                  <Route path="batches" element={<Batches />} />
                  <Route path="batches/:id" element={<Batch />} />
                  <Route path="slots" element={<Slots />} />
                  <Route path="map" element={<MapPage />} />
                  <Route path="settings" element={<ConfigPage />} />
                  <Route path="settings/mail" element={<MailSettings />} />
                  <Route
                    path="settings/mail-templates"
                    element={<MailTemplates />}
                  />
                  <Route path="settings/promotions" element={<Promotions />} />
                  <Route
                    path="settings/volume-discounts"
                    element={<VolumeDiscounts />}
                  />
                  <Route
                    path="promotions/records"
                    element={<PromotionRecords />}
                  />
                  <Route path="customers" element={<Customers />} />
                  <Route path="customers/:id" element={<CustomerDetail />} />
                  <Route path="account" element={<StaffAccount />} />
                  <Route path="staff" element={<Staff />} />
                  <Route path="exceptions" element={<Exceptions />} />
                  <Route path="trash" element={<Trash />} />
                  <Route path="audit" element={<Audit />} />
                </Route>
              </Route>
              <Route path="/courier/login" element={<Login courier />} />
              {(["setup", "forgot", "reset", "verify"] as const).map((mode) => (
                <Route
                  key={mode}
                  path={`/courier/account/${mode}`}
                  element={
                    <LegacyCredentialRedirect
                      to={legacyCredentialRoutes[mode]}
                    />
                  }
                />
              ))}
              <Route element={<StaffGuard courier />}>
                <Route path="/courier" element={<CourierLayout />}>
                  <Route index element={<CourierHome />} />
                  <Route path="batches/:id" element={<CourierBatch />} />
                  <Route path="orders/:id" element={<CourierOrder />} />
                  <Route path="orders/:id/proof" element={<Proof />} />
                  <Route path="orders/:id/exception" element={<Exception />} />
                  <Route
                    path="profile"
                    element={<Navigate to="/courier/account" replace />}
                  />
                  <Route path="account" element={<StaffAccount courier />} />
                </Route>
              </Route>
              <Route
                path="*"
                element={
                  <Empty
                    title="这一页暂时找不到"
                    text="地址可能已变更，请返回首页。"
                    action={
                      <Link className="btn" to="/u/">
                        返回首页
                      </Link>
                    }
                  />
                }
              />
            </Routes>
          </Provider>
        </IdentityProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
const rootHost = window as typeof window & {
  __dormprintRoot?: ReturnType<typeof ReactDOM.createRoot>;
};
(rootHost.__dormprintRoot ??= ReactDOM.createRoot(
  document.getElementById("root")!,
)).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
