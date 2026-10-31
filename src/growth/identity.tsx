import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { DEMO, request } from "../lib/api";
import { advanceIdentity, growthRequest } from "./client";
import type { CustomerSession, StaffSession, OrderAccess } from "./types";
const anonymous: CustomerSession = {
  role: "CUSTOMER",
  authKind: "ANONYMOUS",
  recovered: false,
  capabilities: [],
};
type Identity = {
  customer: CustomerSession;
  staff: StaffSession | null;
  orderAccess: OrderAccess | null;
  generation: number;
  staffReady: boolean;
  customerReady: boolean;
  setStaff: (staff: StaffSession | null) => void;
  setOrderAccess: (access: OrderAccess | null) => void;
  refreshCustomer: () => Promise<void>;
  refreshStaff: () => Promise<void>;
  loginCustomer: (email: string, password: string, replaceSessionId?: string) => Promise<boolean>;
  logoutCustomer: () => Promise<void>;
  clearCustomer: () => void;
  demoCustomer: () => Promise<void>;
  invalidate: () => void;
};
const Context = createContext<Identity>(null!);
export function IdentityProvider({ children }: { children: ReactNode }) {
  const [customer, setCustomer] = useState(anonymous);
  const [staff, setStaffState] = useState<StaffSession | null>(null);
  const [orderAccess, setOrderAccess] = useState<OrderAccess | null>(null);
  const [generation, setGeneration] = useState(0);
  const [staffReady, setStaffReady] = useState(DEMO);
  const [customerReady, setCustomerReady] = useState(DEMO);
  const query = useQueryClient();
  const revision = useRef(0);
  const customerRef = useRef(customer);
  const invalidate = () => {
    revision.current++;
    setGeneration(advanceIdentity());
    void query.cancelQueries();
    query.clear();
  };
  const refreshCustomer = async () => {
    const current = revision.current;
    try {
      const result = await growthRequest<CustomerSession>("/customer/auth/me");
      if (current === revision.current) {
        const previous = customerRef.current;
        if (
          [
            previous.authKind,
            previous.account?.id,
            previous.account?.verified,
            previous.account?.status,
          ].join(":") !==
          [
            result.authKind,
            result.account?.id,
            result.account?.verified,
            result.account?.status,
          ].join(":")
        )
          invalidate();
        customerRef.current = result;
        setCustomer(result);
      }
    } catch (e) {
      if (
        current === revision.current &&
        (e as { status?: number }).status === 401
      ) {
        if (customerRef.current.authKind !== "ANONYMOUS") invalidate();
        customerRef.current = anonymous;
        setCustomer(anonymous);
        void refreshStaff();
      } else if (!DEMO) throw e;
    } finally {
      setCustomerReady(true);
    }
  };
  const refreshStaff = async () => {
    const current = revision.current;
    try {
      const result = DEMO
        ? await growthRequest<StaffSession | null>("/auth/me")
        : await request<StaffSession>("/auth/me");
      if (current === revision.current) setStaffState(result);
    } catch (e) {
      if (
        current === revision.current &&
        [401, 403].includes((e as { status?: number }).status || 0)
      )
        setStaffState(null);
    } finally {
      if (current === revision.current) setStaffReady(true);
    }
  };
  const setStaff = (next: StaffSession | null) => {
    if (DEMO) void import("./demo").then((m) => m.setDemoStaff(next));
    invalidate();
    setStaffState(next);
  };
  const loginCustomer = async (
    email: string,
    password: string,
    replaceSessionId?: string,
  ) => {
    const result = await growthRequest<{ recovered?: boolean }>(
      "/customer/auth/login",
      {
        method: "POST",
        body: {
          email,
          password,
          ...(replaceSessionId ? { replaceSessionId } : {}),
        },
      },
    );
    invalidate();
    await refreshCustomer();
    await refreshStaff();
    return result.recovered === true;
  };
  const clearCustomer = () => {
    window.dispatchEvent(new Event("dormprint:customer-logout"));
    invalidate();
    customerRef.current = anonymous;
    setCustomer(anonymous);
    setCustomerReady(true);
    setStaffState(null);
    setStaffReady(true);
  };
  const logoutCustomer = async () => {
    await growthRequest("/customer/auth/logout", { method: "POST" });
    clearCustomer();
    if (DEMO) {
      const demo = await import("./demo");
      demo.setDemoStaff(null);
    }
    await Promise.allSettled([refreshCustomer(), refreshStaff()]);
  };
  const demoCustomer = async () => {
    if (!DEMO) throw new Error("演示身份仅在开发模式提供");
    await growthRequest("/__demo/customer", { method: "POST" });
    invalidate();
    await refreshCustomer();
  };
  useEffect(() => {
    void refreshCustomer().catch(() => {});
  }, []);
  // A customer transition may cancel the staff probe. Probe again in the new
  // generation; the effective staff role is derived from the one account session.
  useEffect(() => {
    if (!DEMO) void refreshStaff();
  }, [generation]);
  return (
    <Context.Provider
      value={{
        customer,
        staff,
        orderAccess,
        generation,
        staffReady,
        customerReady,
        setStaff,
        setOrderAccess: (access) => {
          if (access?.sourceGrantId !== orderAccess?.sourceGrantId)
            invalidate();
          setOrderAccess(access);
        },
        refreshCustomer,
        refreshStaff,
        loginCustomer,
        logoutCustomer,
        clearCustomer,
        demoCustomer,
        invalidate,
      }}
    >
      {children}
    </Context.Provider>
  );
}
export const useIdentity = () => useContext(Context);
