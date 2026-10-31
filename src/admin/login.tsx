import { Navigate, useLocation } from "react-router-dom";
import { ADMIN } from "../lib/store";
import {
  customerLoginPath,
  safeStaffReturn,
} from "../components/role-switch";

/**
 * Legacy staff login URLs now redirect to the one customer-base login. The
 * requested workspace is kept as a safe post-login return target; the server
 * still decides whether the account has the effective staff role.
 */
export function Login({ courier = false }: { courier?: boolean }) {
  const location = useLocation();
  const fallback = courier ? "/courier/" : `${ADMIN}/`;
  const state = location.state as { from?: unknown } | null;
  const target =
    safeStaffReturn(typeof state?.from === "string" ? state.from : null) ||
    fallback;
  return <Navigate to={customerLoginPath(target)} replace />;
}
