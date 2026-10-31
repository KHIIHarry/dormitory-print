import { planResources } from "./resources";
import {
  ActionLock,
  LatestTask,
  type ResourceSnapshot,
} from "./feedback-state";
import { isUncertainSubmission } from "./api";
import { usePresence, MOTION } from "../components/presence";
import {
  createContext,
  useContext,
  useEffect,
  useState,
  useRef,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { adminResources } from "../admin/resources";
import { useIdentity } from "../growth/identity";
import type { Quote } from "../growth/types";
import { getIdentityGeneration } from "../growth/client";
import { useLocation } from "react-router-dom";
import { DEMO, ADMIN, mutation, request, now } from "./api";
import type { DataState, Config, PrintFile, Role, Order } from "./types";
const emptyConfig: Config = {
  serviceEnabled: false,
  announcement: "",
  prices: {
    bwSingle: null,
    bwDuplex: null,
    colorSingle: null,
    colorDuplex: null,
    photo: null,
    corner: null,
    side: null,
  },
  wechat: "",
  phone: "",
  qrUrl: "",
  photoWidthMm: 127,
  photoHeightMm: 89,
  margins: { top: 3, bottom: 3, left: 3, right: 3 },
  maxFiles: 0,
  maxMb: 0,
  bindingApproved: false,
};
const initial: DataState = {
  orders: [],
  batches: [],
  buildings: [],
  slots: [],
  staff: [],
  exceptions: [],
  trash: [],
  audit: [],
  config: emptyConfig,
};
type Store = {
  savedAccessLinks: Record<string, string>;
  setSavedAccessLinks: React.Dispatch<
    React.SetStateAction<Record<string, string>>
  >;
  checkoutMemory: Record<string, unknown>;
  setCheckoutMemory: React.Dispatch<
    React.SetStateAction<Record<string, unknown>>
  >;
  editingOrderId: string | null;
  setEditingOrderId: React.Dispatch<React.SetStateAction<string | null>>;
  data: DataState;
  setData: React.Dispatch<React.SetStateAction<DataState>>;
  draft: PrintFile[];
  setDraft: React.Dispatch<React.SetStateAction<PrintFile[]>>;
  role: Role | null;
  setRole: React.Dispatch<React.SetStateAction<Role | null>>;
  resources: Partial<Record<keyof DataState, ResourceSnapshot>>;
  refreshing: boolean;
  refreshError: string;
  pendingActions: Record<string, boolean>;
  actionIssues: Record<string, string>;
  loading: boolean;
  authReady: boolean;
  error: string;
  toast: (text: string) => void;
  act: (
    path: string,
    body: unknown,
    apply: (s: DataState) => DataState,
    method?: string,
    onResponse?: (result: unknown) => void,
    onError?: (error: unknown) => void,
  ) => Promise<boolean>;
  updateOrder: (
    id: string,
    patch: Partial<Order>,
    action: string,
  ) => Promise<boolean>;
  refresh: () => Promise<void>;
};
const Context = createContext<Store>(null!);
export function Provider({ children }: { children: ReactNode }) {
  const [savedAccessLinks, setSavedAccessLinks] = useState<
    Record<string, string>
  >({});
  const [checkoutMemory, setCheckoutMemory] = useState<Record<string, unknown>>(
    {},
  );
  const [editingOrderId, setEditingOrderId] = useState<string | null>(null);
  const [data, setData] = useState(initial);
  const [draft, setDraft] = useState<PrintFile[]>([]);
  const identity = useIdentity();
  const role = identity.staff?.role || null;
  const setRole: React.Dispatch<React.SetStateAction<Role | null>> = (
    action,
  ) => {
    const next = typeof action === "function" ? action(role) : action;
    identity.setStaff(
      next === "ADMIN" || next === "COURIER"
        ? {
            id: next === "ADMIN" ? "staff-1" : "staff-2",
            role: next,
            name: next === "ADMIN" ? "管理员" : "李同学",
            username: next === "ADMIN" ? "admin" : "courier",
            capabilities: next === "ADMIN" ? ["*"] : [],
            accountId: identity.customer.account?.id ?? "demo-account",
            isSuperAdmin: next === "ADMIN",
            emailVerified: false,
            mustChangePassword: false,
          }
        : null,
    );
  };
  const [bootLoading, setLoading] = useState(true);
  const [resources, setResources] = useState<
    Partial<Record<keyof DataState, ResourceSnapshot>>
  >({});
  const resourceRef = useRef(resources);
  resourceRef.current = resources;
  const latest = useRef(new LatestTask());
  const running = useRef(
    new Map<
      string,
      {
        promise: Promise<void>;
        controller: AbortController;
        current: () => boolean;
      }
    >(),
  );
  const locks = useRef(new ActionLock());
  const [pendingActions, setPendingActions] = useState<Record<string, boolean>>(
    {},
  );
  const [actionIssues, setActionIssues] = useState<Record<string, string>>({});
  const [dataGeneration, setDataGeneration] = useState(getIdentityGeneration());
  const authReady = identity.staffReady;
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const location = useLocation();
  const queryClient = useQueryClient();
  const sessionReady = useRef(false);
  const sessionRequest = useRef<Promise<unknown> | null>(null);
  const messageRef = useRef(message);
  if (message) messageRef.current = message;
  const toastPresence = usePresence(!!message, MOTION.local);
  const toast = (text: string) => setMessage(text);
  useEffect(() => {
    if (message) {
      const t = setTimeout(() => setMessage(""), 4500);
      return () => clearTimeout(t);
    }
  }, [message]);
  const refresh = async () => {
    if (DEMO) {
      const generation = getIdentityGeneration();
      const module = await import("../growth/demo");
      if (generation !== getIdentityGeneration()) return;
      setData((s) => ({
        ...s,
        config: { ...s.config, volumeDiscounts: module.getDemoGrowth().volume },
        orders: module.visibleDemoOrders(
          location.pathname.startsWith(ADMIN)
            ? "admin"
            : location.pathname.startsWith("/courier")
              ? "courier"
              : "customer",
          identity.customer,
          identity.orderAccess,
        ),
      }));
      return;
    }
    const startGeneration = getIdentityGeneration();
    if (
      identity.staff?.mustChangePassword &&
      (location.pathname.startsWith(ADMIN) ||
        location.pathname.startsWith("/courier"))
    ) {
      setLoading(false);
      return;
    }
    const plan = planResources(
      location.pathname,
      location.search,
      role,
      identity.staff?.capabilities || [],
    );
    const realm = location.pathname.startsWith(ADMIN)
      ? "admin"
      : location.pathname.startsWith("/courier")
        ? "courier"
        : "customer";
    const mark = (name: keyof DataState, snapshot: ResourceSnapshot) => {
      resourceRef.current = { ...resourceRef.current, [name]: snapshot };
      setResources(resourceRef.current);
    };
    await Promise.all(
      plan.map((resource) => {
        const key = `${startGeneration}:${realm}:${resource.path}`;
        const inFlight = running.current.get(key);
        if (inFlight?.current()) return inFlight.promise;
        if (inFlight) {
          inFlight.controller.abort();
          running.current.delete(key);
        }
        const ownership = latest.current.begin(resource.name),
          controller = new AbortController();
        const previous = resourceRef.current[resource.name];
        mark(resource.name, {
          key,
          loading: true,
          hasData: previous?.key === key && previous.hasData,
          error: "",
        });
        const owns = () =>
          ownership.current() &&
          startGeneration === getIdentityGeneration() &&
          !controller.signal.aborted;
        const promise = (async () => {
          try {
            let result: unknown;
            if (resource.name === "orders" && realm === "customer") {
              if (!sessionReady.current) {
                sessionRequest.current ||= request("/customer/session", {
                  method: "POST",
                  body: {},
                });
                await sessionRequest.current;
                if (!owns()) return;
                sessionReady.current = true;
              }
              result = await request(resource.path, {
                signal: controller.signal,
              });
            } else if (resource.name === "orders" && realm === "courier") {
              const assigned = await request<{ items: DataState["batches"] }>(
                resource.path,
                { signal: controller.signal },
              );
              const details = await Promise.all(
                assigned.items.map((batch) =>
                  request<{
                    batch: DataState["batches"][number];
                    orders: Order[];
                  }>(`/courier/batches/${batch.id}`, {
                    signal: controller.signal,
                  }),
                ),
              );
              result = {
                items: details.flatMap((d) => d.orders),
                batches: details.map((d) => d.batch),
              };
            } else if (resource.path === "/public/config")
              result = await queryClient.fetchQuery({
                queryKey: ["public-config"],
                queryFn: ({ signal }) =>
                  request<Config>(resource.path, { signal }),
                staleTime: 5000,
              });
            else
              result = await request(resource.path, {
                signal: controller.signal,
              });
            if (!owns()) return;
            const payload = result as {
              items?: unknown[];
              batches?: DataState["batches"];
            };
            setData((state) => {
              if (!owns()) return state;
              let value: unknown =
                resource.name === "config" ? result : payload.items || result;
              if (resource.name === "orders" && !Array.isArray(value))
                value = [value];
              return {
                ...state,
                [resource.name]: value,
                ...(payload.batches ? { batches: payload.batches } : {}),
              };
            });
            setDataGeneration(startGeneration);
            mark(resource.name, {
              key,
              loading: false,
              hasData: true,
              error: "",
            });
          } catch (error) {
            if (!owns()) return;
            const item = error as { status?: number; message?: string };
            const forbidden = item.status === 401 || item.status === 403;
            if (forbidden) {
              setData((state) => ({
                ...state,
                [resource.name]: resource.name === "config" ? emptyConfig : [],
              }));
              queryClient.clear();
              if (item.status === 401 && realm !== "customer")
                identity.setStaff(null);
            }
            const old = resourceRef.current[resource.name];
            mark(resource.name, {
              key,
              loading: false,
              hasData: !forbidden && !!old?.hasData,
              error: item.message || "暂时无法读取当前内容",
            });
            if (
              resource.name === "orders" &&
              realm === "customer" &&
              !sessionReady.current
            )
              sessionRequest.current = null;
          } finally {
            if (running.current.get(key)?.controller === controller)
              running.current.delete(key);
          }
        })();
        running.current.set(key, { promise, controller, current: owns });
        return promise;
      }),
    );
    if (startGeneration === getIdentityGeneration()) setLoading(false);
  };

  useEffect(() => {
    if (DEMO) {
      import("./demo").then((m) => {
        const state = m.createDemo();
        state.staff = state.staff.map((s) => ({
          ...s,
          version: 1,
          status: "ACTIVE",
          mustChangePassword: false,
          emailVerified: false,
        }));
        import("../growth/pricing").then((p) =>
          import("../growth/demo").then((g) => {
            state.orders = state.orders.map((o) => ({
              ...o,
              currentQuoteId: `initial-${o.id}`,
              quote: {
                ...p.buildQuote(
                  [
                    {
                      key: "legacy",
                      relatedId: o.id,
                      unit: "SHEET",
                      quantity: 1,
                      unitPriceCents: o.totalCents,
                      amountCents: o.totalCents,
                      service: "A4",
                    },
                  ],
                  g.defaultVolume,
                  [],
                  {},
                  null,
                ),
                id: `initial-${o.id}`,
              },
              ownerPrincipalId: "guest-initial",
              checkoutAccountId: null,
              allowedActions:
                o.status === "PENDING_CONFIRMATION"
                  ? [
                      "ORDER_REVISE_PENDING",
                      "ORDER_CANCEL_PENDING",
                      "ORDER_CHANGE_LOCATION_PRE_DELIVERY",
                    ]
                  : ["READY_FOR_DELIVERY", "PENDING_PRINT"].includes(o.status)
                    ? ["ORDER_CHANGE_LOCATION_PRE_DELIVERY"]
                    : [],
            }));
            g.syncDemoOrders(state.orders);
            setData(state);
          }),
        );
        setLoading(false);
      });
    } else {
      void refresh();
    }
  }, []);
  useEffect(() => {
    void refresh();
  }, [role, location.pathname, location.search, identity.generation]);
  useEffect(() => {
    if (DEMO) return;
    const t = setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, 10000);
    const listener = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", listener);
    window.addEventListener("online", listener);
    return () => {
      clearInterval(t);
      document.removeEventListener("visibilitychange", listener);
      window.removeEventListener("online", listener);
    };
  }, [role, location.pathname, location.search, identity.generation]);
  useEffect(() => {
    if (DEMO)
      import("../growth/demo").then((m) => m.syncDemoOrders(data.orders));
  }, [data.orders]);
  useEffect(() => {
    const reset = () => {
      latest.current.invalidate();
      running.current.forEach((task) => task.controller.abort());
      running.current.clear();
      resourceRef.current = {};
      setResources({});
      locks.current.clear();
      setPendingActions({});
      setActionIssues({});
      sessionReady.current = false;
      sessionRequest.current = null;
      setDataGeneration(getIdentityGeneration());
      setData((state) => ({
        ...state,
        orders: [],
        batches: DEMO ? state.batches : [],
        staff: DEMO ? state.staff : [],
        exceptions: DEMO ? state.exceptions : [],
        trash: DEMO ? state.trash : [],
        audit: DEMO ? state.audit : [],
      }));
    };
    window.addEventListener("dormprint:identity", reset);
    return () => {
      window.removeEventListener("dormprint:identity", reset);
      latest.current.invalidate();
      running.current.forEach((task) => task.controller.abort());
      running.current.clear();
    };
  }, []);
  useEffect(() => {
    setData((s) => ({
      ...s,
      orders: [],
      batches: DEMO ? s.batches : [],
      staff: DEMO ? s.staff : [],
      exceptions: DEMO ? s.exceptions : [],
      audit: DEMO ? s.audit : [],
      trash: DEMO ? s.trash : [],
    }));
    void refresh();
  }, [identity.generation]);
  useEffect(() => {
    const clear = () => {
      const retained = new Set(draft.map((f) => f.url));
      for (const o of data.orders)
        for (const f of o.files) {
          for (const url of [
            f.url,
            f.pdfUrl,
            ...(f.finalArtifacts || []).map((a) => a.url),
          ])
            if (url?.startsWith("blob:") && !retained.has(url))
              URL.revokeObjectURL(url);
        }
      setSavedAccessLinks({});
      setEditingOrderId(null);
      setCheckoutMemory({});
      setDraft((s) => s.map((f) => ({ ...f, finalArtifacts: undefined })));
    };
    window.addEventListener("dormprint:customer-logout", clear);
    return () => window.removeEventListener("dormprint:customer-logout", clear);
  }, [draft, data.orders]);
  const act = async (
    path: string,
    body: unknown,
    apply: (s: DataState) => DataState,
    method = "POST",
    onResponse?: (result: unknown) => void,
    onError?: (error: unknown) => void,
  ) => {
    if (!navigator.onLine) {
      toast("当前离线，联网后再执行此操作");
      return false;
    }
    const release = locks.current.acquire(path);
    if (!release) return false;
    setPendingActions((old) => ({ ...old, [path]: true }));
    setActionIssues((old) => ({ ...old, [path]: "" }));
    const operationGeneration = getIdentityGeneration();
    try {
      if (!DEMO) {
        const response = await mutation(path, body, method);
        if (operationGeneration !== getIdentityGeneration()) return false;
        onResponse?.(response);
      }
      if (DEMO) {
        const m = await import("../growth/demo");
        m.validateDemoOrderAction(path, body);
      }
      if (operationGeneration !== getIdentityGeneration()) return false;
      setData((s) => {
        if (operationGeneration !== getIdentityGeneration()) return s;
        const next = apply(s);
        return {
          ...next,
          audit: [
            {
              id: crypto.randomUUID(),
              action: path.split("/").pop() || "操作",
              target: path,
              by: role === "COURIER" ? "李同学" : "管理员",
              time: now(),
              reason:
                typeof body === "object" && body && "reason" in body
                  ? String(body.reason)
                  : "业务操作",
            },
            ...next.audit,
          ],
        };
      });
      if (!DEMO) {
        await queryClient.invalidateQueries();
        await refresh();
      }
      return true;
    } catch (e) {
      if (operationGeneration !== getIdentityGeneration()) return false;
      onError?.(e);
      const issue = isUncertainSubmission(e)
        ? "提交结果待核实：请求响应未确认，请先刷新并核对实际业务状态，避免重复操作。"
        : (e as Error).message;
      setActionIssues((old) => ({ ...old, [path]: issue }));
      if ((e as { status: number }).status === 409) void refresh();
      return false;
    } finally {
      release();
      if (operationGeneration === getIdentityGeneration())
        setPendingActions((old) => ({ ...old, [path]: false }));
    }
  };
  const updateOrder = async (
    id: string,
    patch: Partial<Order>,
    action: string,
  ) => {
    const order = data.orders.find((o) => o.id === id);
    const payload: Record<string, unknown> = {
      expectedVersion: order?.version,
    };
    let path = `/orders/${id}/${action}`;
    let method = "POST";
    if (action === "location") {
      payload.locationId = patch.locationId;
      method = "PATCH";
    } else if (action === "cancel") {
      payload.reason = patch.note;
    } else if (["note", "priority", "review-address"].includes(action)) {
      path = `/admin/corrections`;
      Object.assign(payload, {
        targetType: "ORDER",
        targetId: id,
        action:
          action === "review-address"
            ? "REVIEW_ADDRESS"
            : action === "priority"
              ? "SET_PRIORITY"
              : "SET_NOTE",
        reason: patch.note || action,
        actionPayload: patch,
      });
    }
    return act(
      path,
      payload,
      (s) => ({
        ...s,
        orders: s.orders.map((o) =>
          o.id === id
            ? {
                ...o,
                ...patch,
                version: o.version + 1,
                history: [
                  {
                    title:
                      action === "cancel"
                        ? "订单已撤销"
                        : action === "location"
                          ? "楼栋已更新"
                          : action === "review-address"
                            ? "配送地址已复核"
                            : "订单记录已更新",
                    time: now(),
                    description: patch.note || "",
                  },
                  ...o.history,
                ],
              }
            : o,
        ),
      }),
      method,
    );
  };
  const realm = location.pathname.startsWith(ADMIN)
    ? "admin"
    : location.pathname.startsWith("/courier")
      ? "courier"
      : "customer";
  const currentPlan =
    identity.staff?.mustChangePassword && realm !== "customer"
      ? []
      : planResources(
          location.pathname,
          location.search,
          role,
          identity.staff?.capabilities || [],
        );
  const currentResources = { ...resources };
  for (const resource of currentPlan) {
    const key = `${getIdentityGeneration()}:${realm}:${resource.path}`;
    if (DEMO)
      currentResources[resource.name] = {
        key,
        loading: bootLoading,
        hasData: !bootLoading,
        error: "",
      };
    else if (currentResources[resource.name]?.key !== key)
      currentResources[resource.name] = {
        key,
        loading: true,
        hasData: false,
        error: "",
      };
  }
  const states = currentPlan.map((r) => currentResources[r.name]!);
  const loading = DEMO
    ? bootLoading
    : states.some((s) => s.loading && !s.hasData);
  const refreshing = !DEMO && states.some((s) => s.loading && s.hasData);
  const currentError = !DEMO
    ? states.find((s) => s.error && !s.hasData)?.error || ""
    : error;
  const refreshError = !DEMO
    ? states.find((s) => s.error && s.hasData)?.error || ""
    : "";
  const authorizedData =
    dataGeneration === getIdentityGeneration()
      ? data
      : {
          ...data,
          orders: [],
          batches: [],
          staff: [],
          exceptions: [],
          trash: [],
          audit: [],
        };
  const needsConfig = currentPlan.some(
    (resource) => resource.name === "config",
  );
  const visibleData =
    !DEMO && needsConfig && !currentResources.config?.hasData
      ? { ...authorizedData, config: emptyConfig }
      : authorizedData;
  return (
    <Context.Provider
      value={{
        savedAccessLinks,
        setSavedAccessLinks,
        checkoutMemory,
        setCheckoutMemory,
        editingOrderId,
        setEditingOrderId,
        data: visibleData,
        resources: currentResources,
        refreshing,
        refreshError,
        pendingActions,
        actionIssues,
        setData,
        draft,
        setDraft,
        role,
        setRole,
        loading,
        authReady,
        error: currentError,
        toast,
        act,
        updateOrder,
        refresh,
      }}
    >
      {children}
      {toastPresence.present && (
        <div className="toast" data-state={toastPresence.phase} role="status">
          {messageRef.current}
          <button aria-label="关闭提示" onClick={() => setMessage("")}>
            ×
          </button>
        </div>
      )}
    </Context.Provider>
  );
}
export const useStore = () => useContext(Context);
export { DEMO, ADMIN };
