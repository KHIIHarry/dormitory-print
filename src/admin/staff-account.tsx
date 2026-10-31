import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { Link, useNavigate } from "react-router-dom";
import { PencilLine, Mail, LogOut, ShieldCheck, UserPlus } from "lucide-react";
import {
  Button,
  Field,
  Modal,
  Notice,
  PageHeading,
  Panel,
} from "../components/ui";
import { ADMIN, DEMO } from "../lib/api";
import { useStore } from "../lib/store";
import type { Staff } from "../lib/types";
import { growthRequest, useGrowthResource } from "../growth/client";
import { useIdentity } from "../growth/identity";
import type { SessionDevice } from "../growth/types";
import {
  consumeFragment,
  readFragmentToken,
} from "../customer/account/security";
import {
  ConfirmAction,
  ResourceState,
  SubmitState,
  useSubmit,
} from "./growth/common";
import { localDate, mailStatus } from "./growth/logic";
import { AdminPermissionEditor } from "../v1/admin";
import { StaffPayrollPanel } from "../v1/payroll";
import {
  CAPABILITY_CATALOG,
  staffGrantableAccountsPath,
  staffGrantPayload,
  staffUpdatePayload,
  type StaffGrantableAccount,
} from "../v1/admin-api";
import "./growth.css";

type StaffRow = Staff & {
  capabilities?: string[];
  accountId?: string;
  isSuperAdmin?: boolean;
  discountLevel?: number | null;
};
type GrantDraft = {
  account: StaffGrantableAccount;
  name: string;
  username: string;
  phone: string;
  role: Staff["role"];
  discountLevel: string;
  capabilities: string[];
  isSuperAdmin: boolean;
};
type StaffDraft = {
  id: string;
  version?: number;
  accountId?: string;
  name: string;
  username: string;
  phone: string;
  role: Staff["role"];
  enabled: boolean;
  status: "PENDING_SETUP" | "ACTIVE" | "DISABLED";
  discountLevel: string;
  isSuperAdmin: boolean;
};
type MailResult = {
  status?: string;
  message?: string;
  retryAfterSeconds?: number;
};
/** A conservative local guard; the server must enforce this against its full staff set. */
export function staffChangeBlocker(
  rows: StaffRow[],
  currentId: string,
  target: StaffRow,
  next: Pick<StaffDraft, "enabled" | "role" | "status">,
): string {
  const losesAdmin =
    !next.enabled ||
    next.role !== "ADMIN" ||
    next.status === "DISABLED" ||
    next.status === "PENDING_SETUP";
  if (
    target.id === currentId &&
    (!next.enabled ||
      next.role !== target.role ||
      next.status === "DISABLED" ||
      next.status === "PENDING_SETUP")
  )
    return "不能停用自己的账号或修改自己的角色，请由其他有权限的管理员操作。";
  const full = (row: StaffRow) =>
    row.role === "ADMIN" &&
    row.enabled &&
    row.status !== "DISABLED" &&
    row.status !== "PENDING_SETUP" &&
    (!row.capabilities ||
      row.capabilities.includes("*") ||
      row.capabilities.includes("staff.manage"));
  if (full(target) && losesAdmin && rows.filter(full).length <= 1)
    return "不能停用或降级最后一个活跃的完整权限管理员。";
  return "";
}
function draftFrom(row: StaffRow): StaffDraft {
  return {
    id: row.id,
    version: row.version,
    accountId: row.accountId,
    name: row.name,
    username: row.username,
    phone: row.phone,
    role: row.role,
    enabled: row.enabled,
    status: row.status || (row.enabled ? "ACTIVE" : "DISABLED"),
    discountLevel:
      row.discountLevel === null || row.discountLevel === undefined
        ? ""
        : String(row.discountLevel),
    isSuperAdmin: !!row.isSuperAdmin,
  };
}
function staffPasswordError(password: string, confirmation: string) {
  return password.length < 8
    ? "新密码至少需要 8 位。"
    : password !== confirmation
      ? "两次输入的新密码不一致。"
      : "";
}

export function StaffAccountPage({ courier = false }: { courier?: boolean }) {
  const identity = useIdentity(),
    staff = identity.staff,
    submit = useSubmit(),
    navigate = useNavigate();
  const restricted = !!staff?.mustChangePassword;
  const sessions = useGrowthResource<{ items: SessionDevice[] }>(
    staff && !restricted ? "/auth/sessions" : null,
  );
  const [current, setCurrent] = useState(""),
    [password, setPassword] = useState(""),
    [confirmation, setConfirmation] = useState(""),
    [email, setEmail] = useState(""),
    [mail, setMail] = useState<MailResult | null>(null),
    [revoke, setRevoke] = useState<string | null>(null),
    [cooldown, setCooldown] = useState(0);
  useEffect(() => {
    if (!cooldown) return;
    const timer = window.setTimeout(
      () => setCooldown((v) => Math.max(0, v - 1)),
      1000,
    );
    return () => window.clearTimeout(timer);
  }, [cooldown]);
  useEffect(() => {
    setCurrent("");
    setPassword("");
    setConfirmation("");
    setEmail("");
    setMail(null);
    setRevoke(null);
  }, [staff?.id]);
  const changePassword = async (e: FormEvent) => {
    e.preventDefault();
    const validation = staffPasswordError(password, confirmation);
    if (validation) {
      submit.setError(validation);
      return;
    }
    const result = await submit.run(
      () =>
        growthRequest("/auth/change-password", {
          method: "POST",
          body: { currentPassword: current, newPassword: password },
        }),
      "密码已修改，请核对当前会话状态",
    );
    if (result.ok) {
      setCurrent("");
      setPassword("");
      setConfirmation("");
      identity.invalidate();
      await identity.refreshStaff();
      sessions.reload();
    }
  };
  const logout = async () => {
    const result = await submit.run(() =>
      growthRequest("/auth/logout", { method: "POST" }),
    );
    if (result.ok) {
      identity.setStaff(null);
      navigate(courier ? "/courier/login" : `${ADMIN}/login`, {
        replace: true,
      });
    }
  };
  const disabled = submit.pending || !submit.online;
  return (
    <div className="admin-growth">
      <PageHeading
        title="个人账号"
        description={
          courier
            ? "配送工作账号、安全设置和登录设备。"
            : "管理员工作账号、安全设置和登录设备。"
        }
      />
      <SubmitState state={submit} />
      {!identity.staffReady ? (
        <Notice>正在读取员工身份…</Notice>
      ) : !staff ? (
        <Notice type="warning">
          员工会话已失效，请重新
          <Link to={courier ? "/courier/login" : `${ADMIN}/login`}>
            登录工作台
          </Link>
          。
        </Notice>
      ) : (
        <>
          <Panel title="工作账号">
            <dl className="growth-facts">
              <div>
                <dt>展示名 / 工作账号</dt>
                <dd>
                  {staff.name} / {staff.username}
                </dd>
              </div>
              <div>
                <dt>角色</dt>
                <dd>
                  {staff.isSuperAdmin
                    ? "超级管理员"
                    : staff.role === "ADMIN"
                      ? "管理员"
                      : "配送员"}
                </dd>
              </div>
              <div>
                <dt>邮箱</dt>
                <dd>
                  {staff.emailMasked || "未设置"} ·{" "}
                  {staff.emailVerified ? "已验证" : "未验证"}
                </dd>
              </div>
            </dl>
            {restricted && (
              <Notice type="warning">
                此账号必须先修改密码。当前仅允许改密或退出登录，其他工作与安全操作暂不可用。
              </Notice>
            )}
            <Button variant="secondary" disabled={disabled} onClick={logout}>
              退出当前登录
            </Button>
          </Panel>
          {!restricted && <StaffPayrollPanel />}
          <Panel title={restricted ? "完成首次密码修改" : "修改密码"}>
            <form className="growth-stack" onSubmit={changePassword}>
              <Field label={restricted ? "当前临时密码" : "当前密码"}>
                <input
                  required
                  type="password"
                  autoComplete="current-password"
                  value={current}
                  onChange={(e) => setCurrent(e.target.value)}
                />
              </Field>
              <Field label="新密码（至少 8 位）">
                <input
                  required
                  type="password"
                  autoComplete="new-password"
                  minLength={8}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </Field>
              <Field label="再次输入新密码">
                <input
                  required
                  type="password"
                  autoComplete="new-password"
                  minLength={8}
                  value={confirmation}
                  onChange={(e) => setConfirmation(e.target.value)}
                />
              </Field>
              <Button disabled={disabled}>修改密码</Button>
            </form>
          </Panel>
          <Panel title="员工邮箱验证">
            <form
              className="growth-stack"
              onSubmit={async (e) => {
                e.preventDefault();
                if (restricted) return;
                const result = await submit.run(() =>
                  growthRequest<MailResult>(
                    "/auth/email/request-verification",
                    {
                      method: "POST",
                      body: { email: email.trim() },
                    },
                  ),
                );
                if (result.ok) {
                  setMail(result.data);
                  setCooldown(Math.max(0, result.data.retryAfterSeconds ?? 0));
                  setEmail("");
                }
              }}
            >
              <Notice>
                验证邮箱后才能用于员工密码找回。初次设置邀请无需预先验证，收到邀请的员工自行完成邮箱验证与设密。
              </Notice>
              <Field label="本人邮箱">
                <input
                  type="email"
                  required
                  value={email}
                  disabled={restricted}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </Field>
              <Button disabled={disabled || restricted || cooldown > 0}>
                {cooldown ? `请等待 ${cooldown} 秒` : "发送邮箱验证邮件"}
              </Button>
              {mail && (
                <Notice>
                  {mailStatus(mail.status)}
                  {mail.message && <p>{mail.message}</p>}
                </Notice>
              )}
            </form>
          </Panel>
          <Panel
            title="登录设备"
            action={
              <Button
                variant="secondary"
                disabled={disabled || restricted}
                onClick={() => setRevoke("ALL_OTHERS")}
              >
                退出其他所有设备
              </Button>
            }
          >
            {restricted ? (
              <Notice>完成密码修改后才能检查或撤销设备会话。</Notice>
            ) : (
              <>
                <ResourceState
                  {...sessions}
                  empty={sessions.data?.items.length === 0}
                />
                {sessions.data?.items.map((device) => (
                  <div className="file-row" key={device.id}>
                    <div className="file-info">
                      <b>
                        {device.device}
                        {device.current ? " · 当前设备" : ""}
                      </b>
                      <small>最近活动：{localDate(device.lastSeenAt)}</small>
                    </div>
                    <button
                      type="button"
                      className="icon-btn session-action"
                      aria-label={
                        device.current
                          ? "退出当前设备"
                          : `撤销设备会话：${device.device}`
                      }
                      title={device.current ? "退出当前设备" : "撤销设备会话"}
                      disabled={disabled}
                      onClick={() => setRevoke(device.id)}
                    >
                      <LogOut size={18} />
                    </button>
                  </div>
                ))}
              </>
            )}
          </Panel>
        </>
      )}
      {revoke && staff && !restricted && (
        <ConfirmAction
          title="撤销员工登录会话"
          pending={submit.pending}
          error={submit.error}
          onClose={() => setRevoke(null)}
          onConfirm={async (reason) => {
            const currentDevice = sessions.data?.items.some(
              (s) => s.id === revoke && s.current,
            );
            const result = await submit.run(
              () =>
                growthRequest("/auth/sessions/revoke", {
                  method: "POST",
                  body: { sessionId: revoke, reason },
                }),
              "所选员工会话已撤销",
            );
            if (result.ok) {
              if (currentDevice) {
                identity.setStaff(null);
                navigate(courier ? "/courier/login" : `${ADMIN}/login`, {
                  replace: true,
                });
              } else {
                identity.invalidate();
                await identity.refreshStaff();
                sessions.reload();
              }
            }
            return result.ok;
          }}
        >
          将撤销{revoke === "ALL_OTHERS" ? "其他所有设备的" : "所选设备的"}
          登录权限，员工需要重新登录。
        </ConfirmAction>
      )}
    </div>
  );
}

export function StaffCredentialPage({
  mode = "setup",
}: {
  mode?: "setup" | "forgot" | "reset" | "verify";
}) {
  const identity = useIdentity(),
    submit = useSubmit();
  const [token, setToken] = useState(() =>
      mode === "forgot" ? "" : readFragmentToken(window.location),
    ),
    [email, setEmail] = useState(""),
    [password, setPassword] = useState(""),
    [confirmation, setConfirmation] = useState(""),
    [done, setDone] = useState(false),
    [mail, setMail] = useState<MailResult | null>(null),
    [cooldown, setCooldown] = useState(0);
  const activeMode = useRef(mode);
  useLayoutEffect(() => {
    if (activeMode.current !== mode) {
      setToken(mode === "forgot" ? "" : readFragmentToken(window.location));
      setDone(false);
      setPassword("");
      setConfirmation("");
      setMail(null);
      activeMode.current = mode;
    }
    consumeFragment(window.location, (url) =>
      window.history.replaceState(window.history.state, "", url),
    );
  }, [mode]);
  useEffect(() => {
    if (!cooldown) return;
    const timer = window.setTimeout(
      () => setCooldown((v) => Math.max(0, v - 1)),
      1000,
    );
    return () => window.clearTimeout(timer);
  }, [cooldown]);
  const titles = {
    setup: "员工首次设置密码",
    forgot: "找回员工密码",
    reset: "重置员工密码",
    verify: "验证员工邮箱",
  };
  const post = async (e: FormEvent) => {
    e.preventDefault();
    if (mode !== "forgot" && !token) {
      submit.setError("链接缺少凭据，请重新申请邮件。");
      return;
    }
    if (mode === "setup" || mode === "reset") {
      const error = staffPasswordError(password, confirmation);
      if (error) {
        submit.setError(error);
        return;
      }
    }
    const path =
      mode === "setup"
        ? "/auth/setup-password"
        : mode === "reset"
          ? "/auth/reset-password"
          : mode === "verify"
            ? "/auth/email/verify"
            : "/auth/forgot-password";
    const result = await submit.run(() =>
      growthRequest<MailResult>(path, {
        method: "POST",
        body:
          mode === "forgot"
            ? { email: email.trim() }
            : {
                token,
                ...(mode === "verify" ? {} : { newPassword: password }),
              },
      }),
    );
    if (result.ok) {
      setPassword("");
      setConfirmation("");
      if (mode === "forgot") {
        setMail(result.data);
        setCooldown(Math.max(0, result.data.retryAfterSeconds ?? 0));
      } else {
        setToken("");
        setDone(true);
        identity.invalidate();
        await identity.refreshStaff();
      }
    }
  };
  return (
    <div className="admin-growth">
      <PageHeading
        title={titles[mode]}
        description="员工凭据与客户账号独立。打开页面不会自动使用邮件凭据。"
      />
      <Panel>
        <form className="growth-stack" onSubmit={post}>
          <SubmitState state={submit} />
          {done ? (
            <Notice>操作已完成。请返回员工工作台使用正式凭据登录。</Notice>
          ) : (
            <>
              {mode === "forgot" ? (
                <>
                  <Notice>
                    如该员工账号已验证邮箱且允许找回，系统会按安全策略发送邮件；此页面不确认账号是否存在。
                  </Notice>
                  <Field label="已验证的员工邮箱">
                    <input
                      required
                      type="email"
                      autoComplete="email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                    />
                  </Field>
                </>
              ) : (
                <>
                  <Notice>
                    请确认要
                    {mode === "verify"
                      ? "验证此员工邮箱"
                      : mode === "setup"
                        ? "验证邀请邮箱并完成首次设密"
                        : "重置此员工账号的密码"}
                    。链接凭据仅保存在当前页面内存中。
                  </Notice>
                  {!token && (
                    <Notice type="warning">
                      链接缺少凭据或已使用。请联系管理员重新发送安全邮件。
                    </Notice>
                  )}
                  {mode !== "verify" && (
                    <>
                      <Field label="新密码（至少 8 位）">
                        <input
                          required
                          type="password"
                          autoComplete="new-password"
                          minLength={8}
                          value={password}
                          onChange={(e) => setPassword(e.target.value)}
                        />
                      </Field>
                      <Field label="再次输入新密码">
                        <input
                          required
                          type="password"
                          autoComplete="new-password"
                          minLength={8}
                          value={confirmation}
                          onChange={(e) => setConfirmation(e.target.value)}
                        />
                      </Field>
                    </>
                  )}
                </>
              )}
              <Button
                disabled={
                  submit.pending ||
                  !submit.online ||
                  cooldown > 0 ||
                  (mode !== "forgot" && !token)
                }
              >
                {submit.pending
                  ? "正在提交…"
                  : cooldown
                    ? `请等待 ${cooldown} 秒`
                    : mode === "forgot"
                      ? "申请找回邮件"
                      : "确认并提交"}
              </Button>
            </>
          )}
          {mail && (
            <Notice>
              请求已受理。如符合条件，将按安全策略发送邮件。
              <p>{mailStatus(mail.status)}</p>
              {mail.message && <p>{mail.message}</p>}
            </Notice>
          )}
          <div className="button-row">
            <Link className="text-link" to={`${ADMIN}/login`}>
              管理员登录
            </Link>
            <Link className="text-link" to="/courier/login">
              配送员登录
            </Link>
          </div>
        </form>
      </Panel>
    </div>
  );
}

export function StaffManagementPage() {
  const identity = useIdentity(),
    store = useStore(),
    submit = useSubmit();
  const staff = identity.staff,
    rows = store.data.staff as StaffRow[];
  const actor = staff as StaffRow | null;
  const actorSuper = !!(
    actor?.isSuperAdmin ||
    rows.find((row) => row.id === staff?.id)?.isSuperAdmin
  );
  const actorCapabilities = actor?.capabilities || [];
  const canGrantCapability = (key: string) =>
    actorCapabilities.includes("*") || actorCapabilities.includes(key);
  const allowed =
    staff?.role === "ADMIN" &&
    !staff.mustChangePassword &&
    (actorCapabilities.includes("*") ||
      actorCapabilities.includes("staff.manage"));
  const [draft, setDraft] = useState<StaffDraft | null>(null),
    [action, setAction] = useState<{
      row: StaffRow;
      kind: "send-setup" | "revoke-sessions";
    } | null>(null),
    [save, setSave] = useState(false),
    [permissionTarget, setPermissionTarget] = useState<StaffRow | null>(null),
    [grantOpen, setGrantOpen] = useState(false),
    [grant, setGrant] = useState<GrantDraft | null>(null),
    [grantSave, setGrantSave] = useState(false),
    [customerQuery, setCustomerQuery] = useState(""),
    [customerTerm, setCustomerTerm] = useState("");
  useEffect(() => {
    if (!grantOpen) return;
    const timer = window.setTimeout(
      () => setCustomerTerm(customerQuery.trim()),
      300,
    );
    return () => window.clearTimeout(timer);
  }, [customerQuery, grantOpen]);
  const grantable = useGrowthResource<{ items: StaffGrantableAccount[] }>(
    grantOpen && !DEMO && customerTerm.length >= 3
      ? staffGrantableAccountsPath(customerTerm)
      : null,
  );
  useEffect(() => {
    setAction(null);
    setDraft(null);
    setSave(false);
    setPermissionTarget(null);
    setGrantOpen(false);
    setGrant(null);
    setGrantSave(false);
  }, [identity.generation]);
  const disabled = !allowed || submit.pending || !submit.online;
  const permission = (row: StaffRow, action: string) =>
    !row.allowedActions || row.allowedActions.includes(action);
  const saveStaff = async (reason: string) => {
    if (!draft || !staff || !allowed) return false;
    const original = rows.find((row) => row.id === draft.id);
    if (!original) {
      submit.setError("员工记录已变化，请刷新后重试。");
      return false;
    }
    if (draft.version === undefined) {
      submit.setError("员工记录缺少版本，请重新加载后编辑。");
      return false;
    }
    const blocker = staffChangeBlocker(rows, staff.id, original, draft);
    if (blocker) {
      submit.setError(blocker);
      return false;
    }
    let body: ReturnType<typeof staffUpdatePayload>;
    try {
      body = staffUpdatePayload({
        actorId: staff.id,
        target: {
          id: original.id,
          version: draft.version,
          role: original.role,
          capabilities: original.capabilities,
          isSuperAdmin: original.isSuperAdmin,
        },
        name: draft.name,
        username: draft.username,
        phone: draft.phone,
        role: draft.role,
        enabled: draft.enabled,
        status: draft.status,
        discountLevel: draft.discountLevel ? Number(draft.discountLevel) : null,
        isSuperAdmin: draft.isSuperAdmin,
        reason,
      });
    } catch (error) {
      submit.setError((error as Error).message);
      return false;
    }
    const result = await submit.run(async () => {
      const ok = await store.act(
        `/admin/staff/${encodeURIComponent(draft.id)}`,
        body,
        (data) => ({
          ...data,
          staff: data.staff.map((row) =>
            row.id === draft.id
              ? {
                  ...row,
                  ...body,
                  version: (row.version ?? 0) + 1,
                }
              : row,
          ),
        }),
        "PATCH",
      );
      if (!ok)
        throw new Error("员工资料未保存，请核对权限、版本或服务端错误后重试。");
      return true;
    }, "员工资料已保存");
    if (result.ok) {
      setDraft(null);
      await store.refresh();
      if (
        original.id === staff.id ||
        (draft.isSuperAdmin && !original.isSuperAdmin)
      ) {
        identity.invalidate();
        await identity.refreshStaff();
      }
    }
    return result.ok;
  };
  const openGrant = () => {
    submit.setError("");
    setCustomerQuery("");
    setCustomerTerm("");
    setGrant(null);
    setGrantSave(false);
    setGrantOpen(true);
  };
  const chooseAccount = (account: StaffGrantableAccount) => {
    setGrant({
      account,
      name: "",
      username: "",
      phone: "",
      role: "COURIER",
      discountLevel: "",
      capabilities: [],
      isSuperAdmin: false,
    });
  };
  const grantEmployee = async (reason: string) => {
    if (!grant) return false;
    if (DEMO) {
      submit.setError("演示模式没有真实客户账号，无法授予员工权限。");
      return false;
    }
    let body: ReturnType<typeof staffGrantPayload>;
    try {
      body = staffGrantPayload({
        account: grant.account,
        name: grant.name,
        username: grant.username,
        phone: grant.phone,
        role: grant.role,
        capabilities: grant.role === "ADMIN" ? grant.capabilities : [],
        isSuperAdmin: grant.isSuperAdmin,
        discountLevel: grant.discountLevel
          ? Number(grant.discountLevel)
          : undefined,
        reason,
      });
    } catch (error) {
      submit.setError((error as Error).message);
      return false;
    }
    const result = await submit.run(async () => {
      const ok = await store.act("/admin/staff", body, (data) => data, "POST");
      if (!ok)
        throw new Error(
          "员工权限未授予，请核对客户账号状态、能力范围或服务端错误后重试。",
        );
      return true;
    }, "员工权限已授予");
    if (result.ok) {
      setGrantSave(false);
      setGrant(null);
      setGrantOpen(false);
      await store.refresh();
    }
    return result.ok;
  };
  const actionIcons = {
    "send-setup": Mail,
    "revoke-sessions": LogOut,
  };
  const names = {
    "send-setup": "发送客户账号密码重置邮件",
    "revoke-sessions": "撤销员工全部会话",
  };
  return (
    <div className="admin-growth">
      <PageHeading
        title="员工账号管理"
        description="员工权限绑定现有已验证客户账号，使用客户账号密码；不创建独立员工账号。"
        action={
          <Button
            disabled={disabled}
            title={DEMO ? "演示模式无法检索真实客户账号" : undefined}
            onClick={openGrant}
          >
            <UserPlus size={16} />
            授予员工权限
          </Button>
        }
      />
      {!allowed && (
        <Notice type="warning">
          当前会话没有员工管理权限，或必须先修改密码。
        </Notice>
      )}
      <SubmitState state={submit} />
      <ResourceState
        loading={store.loading}
        error={store.error}
        reload={store.refresh}
        empty={!store.loading && !store.error && rows.length === 0}
      />
      <Notice>
        员工与客户共用同一个已验证账号。授权只增加员工角色与能力，不创建第二套邮箱、密码或独立登录入口；停用员工权限不会注销客户账号。
      </Notice>
      {rows.length > 0 && (
        <Panel>
          <div className="growth-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>员工 / 工作账号</th>
                  <th>客户账号 / 状态</th>
                  <th>角色 / 等级 / 最近登录</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id}>
                    <td>
                      <b>{row.name}</b>
                      <small>{row.username}</small>
                    </td>
                    <td>
                      {row.accountId
                        ? `客户账号 ${row.accountId}`
                        : "历史记录未关联客户账号"}
                      <small>
                        {row.emailMasked || "未显示邮箱"} ·{" "}
                        {row.emailVerified ? "已验证" : "未验证"}
                      </small>
                      <small>
                        {row.status === "PENDING_SETUP"
                          ? "待首次设置"
                          : row.enabled
                            ? "已启用"
                            : "已停用"}
                        {row.isSuperAdmin ? " · 超级管理员" : ""}
                      </small>
                    </td>
                    <td>
                      {row.isSuperAdmin
                        ? "超级管理员"
                        : row.role === "ADMIN"
                          ? "管理员"
                          : "配送员"}
                      {row.discountLevel
                        ? ` · 折扣等级 ${row.discountLevel}`
                        : " · 无员工折扣等级"}
                      <small>{localDate(row.lastLoginAt)}</small>
                    </td>
                    <td>
                      <div className="admin-row-actions">
                        <button
                          type="button"
                          className="icon-btn table-action"
                          aria-label={`配置员工权限：${row.name}`}
                          title="管理能力"
                          disabled={
                            disabled ||
                            !permission(row, "edit") ||
                            row.role !== "ADMIN" ||
                            row.version === undefined
                          }
                          onClick={() => {
                            submit.setError("");
                            setPermissionTarget(row);
                          }}
                        >
                          <ShieldCheck size={18} />
                        </button>
                        <button
                          type="button"
                          className="icon-btn table-action"
                          aria-label={`编辑员工账号：${row.name}`}
                          title="编辑资料 / 角色 / 等级 / 启停"
                          disabled={disabled || !permission(row, "edit")}
                          onClick={() => {
                            submit.setError("");
                            setDraft(draftFrom(row));
                          }}
                        >
                          <PencilLine size={18} />
                        </button>
                        {Object.entries(names).map(([kind, label]) => {
                          const Icon =
                            actionIcons[kind as keyof typeof actionIcons];
                          return (
                            <button
                              type="button"
                              key={kind}
                              className="icon-btn table-action"
                              aria-label={`${label}：${row.name}`}
                              title={label}
                              disabled={
                                disabled ||
                                !permission(row, kind) ||
                                (kind === "send-setup" &&
                                  !row.emailMasked &&
                                  !row.email)
                              }
                              onClick={() => {
                                submit.setError("");
                                setAction({
                                  row,
                                  kind: kind as keyof typeof names,
                                });
                              }}
                            >
                              <Icon size={18} />
                            </button>
                          );
                        })}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
      {draft && (
        <Modal
          title={`编辑员工权限 · ${draft.name}`}
          onClose={() => !submit.pending && setDraft(null)}
        >
          <form
            className="growth-stack"
            onSubmit={(e) => {
              e.preventDefault();
              if (!draft.name.trim() || !draft.username.trim()) {
                submit.setError("请填写姓名和工作账号。");
                return;
              }
              if (draft.enabled && draft.status === "DISABLED") {
                submit.setError("启用账号时请选择正常或停用状态。");
                return;
              }
              submit.setError("");
              setSave(true);
            }}
          >
            <SubmitState state={submit} />
            <Field label="客户账号">
              <input
                readOnly
                value={
                  draft.accountId
                    ? `客户账号 ${draft.accountId}`
                    : "历史记录未关联客户账号"
                }
              />
            </Field>
            <Field
              label="员工姓名"
              hint="用于员工管理与工资记录；工作台展示名统一使用客户昵称"
            >
              <input
                required
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
            </Field>
            <Field label="工作账号">
              <input
                required
                autoComplete="off"
                value={draft.username}
                onChange={(e) =>
                  setDraft({ ...draft, username: e.target.value })
                }
              />
            </Field>
            <Field label="联系电话">
              <input
                type="tel"
                value={draft.phone}
                onChange={(e) => setDraft({ ...draft, phone: e.target.value })}
              />
            </Field>
            <div className="v1-two-col">
              <Field label="角色">
                <select
                  disabled={draft.id === staff?.id}
                  value={draft.role}
                  onChange={(e) => {
                    const role = e.target.value as Staff["role"];
                    setDraft({
                      ...draft,
                      role,
                      isSuperAdmin:
                        role === "ADMIN" ? draft.isSuperAdmin : false,
                    });
                  }}
                >
                  <option value="COURIER">配送员</option>
                  <option value="ADMIN">管理员</option>
                </select>
              </Field>
              <Field label="员工折扣等级" hint="1–6 级，留空表示不设置">
                <select
                  value={draft.discountLevel}
                  disabled={draft.id === staff?.id}
                  onChange={(e) =>
                    setDraft({ ...draft, discountLevel: e.target.value })
                  }
                >
                  <option value="">不设置</option>
                  {[1, 2, 3, 4, 5, 6].map((level) => (
                    <option value={level} key={level}>
                      等级 {level}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <Field label="账号状态">
              <select
                disabled={draft.id === staff?.id}
                value={draft.status}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    status: e.target.value as StaffDraft["status"],
                    enabled: e.target.value !== "DISABLED",
                  })
                }
              >
                <option value="ACTIVE">正常</option>
                {draft.status === "PENDING_SETUP" && (
                  <option value="PENDING_SETUP">待首次设置</option>
                )}
                <option value="DISABLED">已停用</option>
              </select>
            </Field>
            {actorSuper && draft.role === "ADMIN" && draft.id !== staff?.id && (
              <label className="growth-check">
                <input
                  type="checkbox"
                  checked={draft.isSuperAdmin}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      isSuperAdmin: e.target.checked,
                      ...(e.target.checked ? { capabilities: ["*"] } : {}),
                    })
                  }
                />
                超级管理员身份（系统仅一位，授予时将移交此身份）
              </label>
            )}
            <Notice>
              员工使用统一客户账号密码，保存资料不会创建独立密码或邀请。角色改为管理员也不会自动获得全部能力；请用盾牌按钮逐项授予。
            </Notice>
            <Button disabled={disabled}>检查并保存</Button>
          </form>
        </Modal>
      )}
      {save && draft && (
        <ConfirmAction
          title="确认员工权限变更"
          pending={submit.pending}
          error={submit.error}
          onClose={() => setSave(false)}
          onConfirm={saveStaff}
        >
          {draft.isSuperAdmin &&
            !rows.find((row) => row.id === draft.id)?.isSuperAdmin && (
              <Notice type="warning">
                将把唯一超级管理员身份移交给此账号，原超级管理员会降为普通管理员，并失去超级管理员免单和专属配置权限。
              </Notice>
            )}
          将保存 {draft.name}{" "}
          的角色、等级与账号状态。服务器会再次校验权限、版本、最后管理员、超级管理员及自身账号保护。
        </ConfirmAction>
      )}
      {action && (
        <ConfirmAction
          title={names[action.kind]}
          pending={submit.pending}
          error={submit.error}
          onClose={() => setAction(null)}
          onConfirm={async (reason) => {
            if (!allowed) return false;
            const result = await submit.run(() =>
              growthRequest<{
                status?: string;
                message?: string;
              }>(
                `/admin/staff/${encodeURIComponent(action.row.id)}/${action.kind}`,
                {
                  method: "POST",
                  body: {
                    reason,
                    ...(action.row.version !== undefined
                      ? { expectedVersion: action.row.version }
                      : {}),
                  },
                },
              ),
            );
            if (!result.ok) return false;
            if (action.kind === "send-setup") {
              store.toast(
                `${mailStatus(result.data.status)}${result.data.message ? ` · ${result.data.message}` : ""}`,
              );
            } else store.toast("员工会话撤销请求已完成");
            await store.refresh();
            if (
              action.kind === "revoke-sessions" &&
              action.row.id === staff?.id
            ) {
              identity.setStaff(null);
            }
            return true;
          }}
        >
          {action.kind === "send-setup"
            ? "将向该员工绑定的客户账号邮箱发送密码重置邮件；SMTP 接受不等于收件箱送达。不会创建独立员工密码。"
            : "将撤销该员工的全部登录会话；若为当前账号，你也需要重新登录。"}
          <p>
            员工：{action.row.name}（{action.row.username}）
          </p>
        </ConfirmAction>
      )}
      {grantOpen && (
        <Modal
          title="授予客户账号员工权限"
          onClose={() => !submit.pending && setGrantOpen(false)}
        >
          {!grant ? (
            <div className="growth-stack">
              <SubmitState state={submit} />
              <Notice>
                只能选择已验证且状态正常的客户账号。系统按客户账号 ID
                绑定，不使用邮箱、姓名或手机号自动匹配。
              </Notice>
              {DEMO && (
                <Notice type="warning">
                  演示模式没有真实客户账号，无法完成授权；请在真实后台操作。
                </Notice>
              )}
              <Field label="搜索客户账号">
                <input
                  autoFocus
                  disabled={DEMO}
                  placeholder="完整邮箱或姓名（至少 3 个字符）"
                  value={customerQuery}
                  onChange={(e) => setCustomerQuery(e.target.value)}
                />
              </Field>
              {!DEMO && customerTerm.length < 3 && (
                <p className="muted">输入至少 3 个字符开始搜索。</p>
              )}
              {!DEMO && customerTerm.length >= 3 && (
                <>
                  <ResourceState
                    loading={grantable.loading}
                    error={grantable.error}
                    reload={grantable.reload}
                    hasData={!!grantable.data?.items.length}
                    empty={
                      !grantable.loading &&
                      !grantable.error &&
                      !grantable.data?.items.length
                    }
                  />
                  {!!grantable.data?.items.length && (
                    <div className="growth-table-wrap">
                      <table>
                        <thead>
                          <tr>
                            <th>客户</th>
                            <th>账号状态</th>
                            <th>操作</th>
                          </tr>
                        </thead>
                        <tbody>
                          {grantable.data.items.map((account) => (
                            <tr key={account.id}>
                              <td>
                                <b>{account.displayName}</b>
                                <small>
                                  {account.emailMasked} · {account.id}
                                </small>
                              </td>
                              <td>
                                已验证
                                <small>可授予员工权限</small>
                              </td>
                              <td>
                                <Button
                                  variant="secondary"
                                  onClick={() => chooseAccount(account)}
                                >
                                  选择
                                </Button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </>
              )}
            </div>
          ) : (
            <form
              className="growth-stack"
              onSubmit={(e) => {
                e.preventDefault();
                submit.setError("");
                setGrantSave(true);
              }}
            >
              <SubmitState state={submit} />
              <Field label="已选择的客户账号">
                <input
                  readOnly
                  value={`${grant.account.displayName} · ${grant.account.emailMasked} · ${grant.account.id}`}
                />
              </Field>
              <Field
                label="员工姓名"
                hint="可选，仅用于员工管理与工资记录；工作台展示名统一使用客户昵称"
              >
                <input
                  value={grant.name}
                  placeholder={grant.account.displayName}
                  onChange={(e) => setGrant({ ...grant, name: e.target.value })}
                />
              </Field>
              <Field
                label="工作账号"
                hint="可选，默认由客户账号生成；用于后台显示与登录标识"
              >
                <input
                  autoComplete="off"
                  value={grant.username}
                  onChange={(e) =>
                    setGrant({ ...grant, username: e.target.value })
                  }
                />
              </Field>
              <Field label="联系电话">
                <input
                  type="tel"
                  value={grant.phone}
                  onChange={(e) =>
                    setGrant({ ...grant, phone: e.target.value })
                  }
                />
              </Field>
              <div className="v1-two-col">
                <Field label="角色">
                  <select
                    value={grant.role}
                    onChange={(e) => {
                      const role = e.target.value as Staff["role"];
                      setGrant({
                        ...grant,
                        role,
                        isSuperAdmin:
                          role === "ADMIN" ? grant.isSuperAdmin : false,
                        capabilities:
                          role === "ADMIN" ? grant.capabilities : [],
                      });
                    }}
                  >
                    <option value="COURIER">配送员</option>
                    <option value="ADMIN">管理员</option>
                  </select>
                </Field>
                <Field label="员工折扣等级" hint="1–6 级，可留空">
                  <select
                    value={grant.discountLevel}
                    onChange={(e) =>
                      setGrant({ ...grant, discountLevel: e.target.value })
                    }
                  >
                    <option value="">不设置</option>
                    {[1, 2, 3, 4, 5, 6].map((level) => (
                      <option value={level} key={level}>
                        等级 {level}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>
              {grant.role === "ADMIN" && (
                <Field
                  label="管理能力"
                  hint="显式勾选；不会因为角色是管理员就自动获得全部能力"
                >
                  <div className="v1-permission-grid">
                    {CAPABILITY_CATALOG.filter((item) => item.enforced).map(
                      (capability) => (
                        <label
                          className={`checkbox-label ${grant.isSuperAdmin || !canGrantCapability(capability.key) ? "disabled" : ""}`}
                          key={capability.key}
                        >
                          <input
                            type="checkbox"
                            disabled={
                              grant.isSuperAdmin ||
                              !canGrantCapability(capability.key)
                            }
                            checked={
                              grant.isSuperAdmin ||
                              grant.capabilities.includes(capability.key)
                            }
                            onChange={(e) =>
                              setGrant({
                                ...grant,
                                capabilities: e.target.checked
                                  ? capability.key === "*"
                                    ? ["*"]
                                    : [
                                        ...grant.capabilities.filter(
                                          (item) => item !== "*",
                                        ),
                                        capability.key,
                                      ]
                                  : grant.capabilities.filter(
                                      (item) => item !== capability.key,
                                    ),
                              })
                            }
                          />
                          <span>
                            <b>{capability.label}</b>
                            <small>{capability.description}</small>
                          </span>
                        </label>
                      ),
                    )}
                  </div>
                </Field>
              )}
              {actorSuper && grant.role === "ADMIN" && (
                <label className="growth-check">
                  <input
                    type="checkbox"
                    checked={grant.isSuperAdmin}
                    onChange={(e) =>
                      setGrant({
                        ...grant,
                        isSuperAdmin: e.target.checked,
                        capabilities: e.target.checked ? ["*"] : [],
                      })
                    }
                  />
                  设为超级管理员（仅现任超级管理员可授予；将拥有全部能力）
                </label>
              )}
              {!actorSuper && grant.role === "ADMIN" && (
                <Notice>
                  超级管理员标记只能由现任超级管理员授予；当前只能授予普通管理员。
                </Notice>
              )}
              <div className="button-row">
                <Button
                  variant="secondary"
                  type="button"
                  onClick={() => setGrant(null)}
                >
                  重新选择客户账号
                </Button>
                <Button>检查并授予</Button>
              </div>
            </form>
          )}
        </Modal>
      )}
      {grantSave && grant && (
        <ConfirmAction
          title="确认授予员工权限"
          pending={submit.pending}
          error={submit.error}
          onClose={() => setGrantSave(false)}
          onConfirm={grantEmployee}
        >
          将为客户账号 {grant.account.displayName}（{grant.account.emailMasked}
          ） 授予{grant.role === "ADMIN" ? "管理员" : "配送员"}权限
          {grant.discountLevel ? `，员工折扣等级 ${grant.discountLevel}` : ""}
          {grant.isSuperAdmin ? "，并设为超级管理员" : ""}。
          绑定固定使用客户账号 ID，不使用邮箱或姓名匹配。
        </ConfirmAction>
      )}
      {permissionTarget && permissionTarget.version !== undefined && (
        <AdminPermissionEditor
          staff={{
            id: permissionTarget.id,
            version: permissionTarget.version,
            role: permissionTarget.role,
            name: permissionTarget.name,
            capabilities: permissionTarget.capabilities,
          }}
          open
          onClose={() => setPermissionTarget(null)}
          onSaved={async () => {
            await store.refresh();
            if (permissionTarget.id === staff?.id)
              await identity.refreshStaff();
            setPermissionTarget(null);
          }}
        />
      )}
    </div>
  );
}
