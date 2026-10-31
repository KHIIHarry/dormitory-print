import { inviteRegistrationUrl, inviteShareText } from "../lib/invite-share";
import { ResourceSkeleton, RefreshStatus } from "../components/feedback";
import { getIdentityGeneration } from "../growth/client";
import { ApiError, isUncertainSubmission } from "../lib/api";
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  ArrowRight,
  CheckCircle2,
  Gift,
  LockKeyhole,
  LogOut,
  Mail,
  ShieldCheck,
  Ticket,
  UserRound,
} from "lucide-react";
import {
  Button,
  CopyButton,
  Empty,
  ErrorState,
  Field,
  Loading,
  Modal,
  Notice,
  PageHeading,
  Panel,
} from "../components/ui";
import { StaffAccessNotice, StaffPortalPanel } from "../components/role-switch";
import { LinkQrButton } from "../components/link-qr";
import { DEMO, useStore } from "../lib/store";
import { money } from "../lib/api";
import type { Order } from "../lib/types";
import { growthRequest, useGrowthResource } from "../growth/client";
import { useIdentity } from "../growth/identity";
import type {
  Coupon,
  CustomerAccount,
  OrderAccess,
  ReferralRecord,
  SessionDevice,
} from "../growth/types";
import {
  consumeFragment,
  readFragmentToken,
  couponTab,
  safeInviteeLabel,
  safeCustomerReturn,
  singleFlight,
} from "./account/security";
import { PersonalDataPanel } from "../v1/customer";
import "./account.css";

type AuthMode = "login" | "register" | "forgot" | "verify" | "reset";
type MailResult = {
  registrationChallengeId?: string;
  emailMasked?: string;
  mailStatus?: string;
  retryAfterSeconds?: number;
  message?: string;
};
let pendingRegistration: { id: string; emailMasked: string } | null = null;
const exchangeOnce = singleFlight<{ order: Order; access: OrderAccess }>();
const messageOf = (error: unknown) =>
  typeof error === "string"
    ? error
    : error instanceof Error
      ? error.message
      : "操作暂时未完成，请稍后重试。";
const dateLabel = (value?: string | null) =>
  value ? new Date(value).toLocaleString("zh-CN") : "未设置期限";
function useFragmentToken(enabled = true) {
  // React may call state initializers twice. Reading is pure; URL clearing happens
  // at commit before paint, so either initialization retains the same secret.
  const state = useState(() =>
    enabled ? readFragmentToken(window.location) : "",
  );
  useLayoutEffect(() => {
    if (enabled)
      consumeFragment(window.location, (url) =>
        window.history.replaceState(window.history.state, "", url),
      );
  }, [enabled]);
  return state;
}
function useAction() {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [success, setSuccess] = useState("");
  const [control, setControl] = useState<HTMLElement | null>(null);
  const mounted = useRef(true);
  const running = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const run = async (action: () => Promise<void>) => {
    if (running.current) return;
    running.current = true;
    const generation = getIdentityGeneration();
    const active = document.activeElement as HTMLElement | null;
    setControl(
      active?.closest("button") ||
        active
          ?.closest("form")
          ?.querySelector<HTMLButtonElement>(
            'button[type="submit"],button:not([type])',
          ) ||
        null,
    );
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      if (!navigator.onLine) throw new Error("当前离线，请联网后重试。");
      await action();
    } catch (e) {
      if (mounted.current && generation === getIdentityGeneration())
        setError(
          isUncertainSubmission(e)
            ? "提交结果待核实，请先查询实际状态，勿重复操作。"
            : messageOf(e),
        );
    } finally {
      running.current = false;
      if (mounted.current) setBusy(false);
    }
  };
  return { busy, control, error, success, setSuccess, run };
}
function Feedback({ action }: { action: ReturnType<typeof useAction> }) {
  return (
    <>
      {action.error && (
        <div role="alert">
          <Notice type="error">{action.error}</Notice>
        </div>
      )}
      {action.success && (
        <div role="status">
          <Notice type="success">{action.success}</Notice>
        </div>
      )}
    </>
  );
}
function PasswordInput({
  value,
  onChange,
  label = "密码",
  autoComplete = "current-password",
  minLength,
}: {
  value: string;
  onChange: (v: string) => void;
  label?: string;
  autoComplete?: string;
  minLength?: number;
}) {
  const [show, setShow] = useState(false);
  return (
    <label className="account-password">
      <span>{label}</span>
      <div>
        <input
          required
          aria-label={label}
          type={show ? "text" : "password"}
          autoComplete={autoComplete}
          minLength={minLength}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
        <button
          type="button"
          aria-label={`${show ? "隐藏" : "显示"}${label}`}
          aria-pressed={show}
          onClick={() => setShow(!show)}
        >
          {show ? "隐藏" : "显示"}
        </button>
      </div>
    </label>
  );
}
function MailStatus({ response }: { response: MailResult }) {
  return response.mailStatus === "QUEUED" ? (
    <Notice>
      邮件已进入发送队列，请查看收件箱与垃圾邮件。排队不代表已经送达。
    </Notice>
  ) : (
    <Notice type="warning">
      邮箱服务尚未确认可用，请稍后重试。你仍可游客下单。
    </Notice>
  );
}
function GuestLinkConfirm({ onDone }: { onDone: () => void }) {
  const action = useAction();
  const identity = useIdentity();
  const { refresh } = useStore();
  return (
    <div className="account-stack">
      <Notice>
        是否将这台设备当前游客身份下的订单关联到账号？确认后旧游客会话会撤销，本浏览器将轮换为全新的游客身份。单独的订单链接授权保持独立。
      </Notice>
      <Feedback action={action} />
      <div className="account-actions">
        <Button
          busy={action.busy}
          busyTarget={action.control}
          disabled={action.busy}
          onClick={() =>
            void action.run(async () => {
              const result = await growthRequest<{
                rotationCompleted: boolean;
              }>("/customer/account/link-guest", {
                method: "POST",
                body: { confirm: true },
              });
              if (!result.rotationCompleted)
                throw new Error("游客会话轮换未完成，请重新核验账号状态。");
              identity.invalidate();
              await identity.refreshCustomer();
              await refresh();
              onDone();
            })
          }
        >
          {action.busy ? "关联中…" : "确认关联本机游客订单"}
        </Button>
        <Button
          variant="secondary"
          busy={action.busy}
          busyTarget={action.control}
          disabled={action.busy}
          onClick={onDone}
        >
          暂不关联
        </Button>
      </div>
    </div>
  );
}

export function CustomerAuthForm({
  mode: initialMode,
  embedded = false,
  onSuccess,
}: {
  mode: AuthMode;
  embedded?: boolean;
  onSuccess?: () => void;
}) {
  const identity = useIdentity(),
    navigate = useNavigate(),
    action = useAction();
  const { toast } = useStore();
  const location = useLocation();
  const deletionRecovery = (
    location.state as { deletionRecovery?: string } | null
  )?.deletionRecovery;
  const [mode, setMode] = useState<AuthMode>(initialMode);
  const [email, setEmail] = useState(""),
    [password, setPassword] = useState(""),
    [confirm, setConfirm] = useState(""),
    [inviteCode, setInviteCode] = useState(
      () => new URLSearchParams(window.location.search).get("invite") || "",
    ),
    [accepted, setAccepted] = useState(false);
  const [token, setToken] = useFragmentToken(
    mode === "verify" || mode === "reset",
  );
  const [mail, setMail] = useState<MailResult | null>(null),
    [challenge, setChallenge] = useState(pendingRegistration),
    [cooldown, setCooldown] = useState(0),
    [registered, setRegistered] = useState(false),
    [completed, setCompleted] = useState(false),
    [linkPrompt, setLinkPrompt] = useState(false);
  const [deviceOptions, setDeviceOptions] = useState<
    Array<{
      id: string;
      device: string;
      lastSeenAt?: string;
      current?: boolean;
    }> | null
  >(null);
  const changingEmail =
    mode === "verify" &&
    ["emailchange", "email-change"].includes(
      new URLSearchParams(window.location.search).get("mode") || "",
    );
  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = window.setTimeout(() => setCooldown(cooldown - 1), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);
  const done = () => {
    setLinkPrompt(false);
    onSuccess
      ? onSuccess()
      : navigate(
          safeCustomerReturn(
            new URLSearchParams(window.location.search).get("returnTo") ||
              new URLSearchParams(window.location.search).get("next"),
          ),
          { replace: true },
        );
  };
  const receivedMail = (response: MailResult) => {
    setMail(response);
    setCooldown(Math.max(0, response.retryAfterSeconds ?? 60));
    if (response.registrationChallengeId) {
      pendingRegistration = {
        id: response.registrationChallengeId,
        emailMasked: response.emailMasked || "待验证邮箱",
      };
      setChallenge(pendingRegistration);
    }
  };
  const login = async (replaceSessionId?: string) => {
    try {
      const recovered = await identity.loginCustomer(
        email.trim(),
        password,
        replaceSessionId,
      );
      setDeviceOptions(null);
      if (recovered) toast("账号已恢复，已取消本次注销。");
      setPassword("");
      if (embedded) onSuccess?.();
      else setLinkPrompt(true);
    } catch (error) {
      if (error instanceof ApiError && error.code === "DEVICE_LIMIT_REACHED") {
        const details = error.details as
          | { sessions?: Array<{ id: string; device: string; lastSeenAt?: string; current?: boolean }> }
          | undefined;
        setDeviceOptions(details?.sessions || []);
        return;
      }
      throw error;
    }
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    void action.run(async () => {
      if (mode === "login") {
        await login();
        return;
      }
      if (mode === "register") {
        if (!accepted) throw new Error("请先确认账号使用与隐私提示。");
        if (password !== confirm) throw new Error("两次密码不一致。");
        receivedMail(
          await growthRequest<MailResult>("/customer/auth/register", {
            method: "POST",
            body: {
              email: email.trim(),
              password,
              inviteCode: inviteCode.trim() || undefined,
              acceptedTerms: true,
            },
          }),
        );
        setPassword("");
        setConfirm("");
        setRegistered(true);
        return;
      }
      if (mode === "forgot") {
        receivedMail(
          await growthRequest<MailResult>("/customer/auth/forgot-password", {
            method: "POST",
            body: { email: email.trim() },
          }),
        );
        return;
      }
      if (!token) throw new Error("此链接缺少有效凭据，请重新申请邮件。");
      if (mode === "reset") {
        if (password !== confirm) throw new Error("两次密码不一致。");
        await growthRequest("/customer/auth/reset-password", {
          method: "POST",
          body: { token, newPassword: password },
        });
        setPassword("");
        setConfirm("");
        setToken("");
        setCompleted(true);
        action.setSuccess("密码已重置，请使用新密码登录。");
        return;
      }
      await growthRequest(
        changingEmail
          ? "/customer/account/confirm-email-change"
          : "/customer/auth/verify-email",
        { method: "POST", body: { token } },
      );
      setToken("");
      pendingRegistration = null;
      setChallenge(null);
      await identity.refreshCustomer();
      setCompleted(true);
      action.setSuccess(
        changingEmail
          ? "新邮箱已确认。"
          : "邮箱已验证。你可以自主选择是否关联本机游客订单。",
      );
      if (!changingEmail) {
        if (embedded) onSuccess?.();
        else setLinkPrompt(true);
      } else onSuccess?.();
    });
  };
  const verifyControls =
    ((mode === "verify" && !changingEmail) || registered) && !completed;
  return (
    <div className={`account-auth ${embedded ? "embedded" : ""}`}>
      {mode === "login" && deletionRecovery && (
        <Notice type="success">{deletionRecovery}</Notice>
      )}
      {linkPrompt ? (
        <>
          <div className="account-success">
            <CheckCircle2 size={26} />
            <h2>账号已就绪</h2>
          </div>
          <GuestLinkConfirm onDone={done} />
        </>
      ) : (
        <>
          <Feedback action={action} />
          {mail && <MailStatus response={mail} />}
          {registered && (
            <Notice>
              请验证邮箱{challenge ? `（${challenge.emailMasked}）` : ""}
              。验证前不会启用正式账号或发放账号权益。
              {embedded ? (
                <button
                  className="text-button"
                  type="button"
                  onClick={() => setMode("verify")}
                >
                  前往验证页
                </button>
              ) : (
                <Link to="/u/account/verify">前往验证页</Link>
              )}
            </Notice>
          )}
          {!completed && !registered && (
            <form className="account-stack" onSubmit={submit}>
              {["login", "register", "forgot"].includes(mode) && (
                <Field label="邮箱">
                  <input
                    required
                    type="email"
                    autoComplete="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="name@example.com"
                  />
                </Field>
              )}
              {["login", "register", "reset"].includes(mode) && (
                <PasswordInput
                  value={password}
                  onChange={setPassword}
                  autoComplete={
                    mode === "login" ? "current-password" : "new-password"
                  }
                  minLength={mode === "login" ? undefined : 8}
                />
              )}
              {["register", "reset"].includes(mode) && (
                <PasswordInput
                  value={confirm}
                  onChange={setConfirm}
                  label="确认密码"
                  autoComplete="new-password"
                  minLength={8}
                />
              )}
              {mode === "register" && (
                <>
                  <Field
                    label="邀请码（可选）"
                    hint="奖励资格与金额以当前活动规则为准。"
                  >
                    <input
                      value={inviteCode}
                      maxLength={64}
                      onChange={(e) => setInviteCode(e.target.value)}
                    />
                  </Field>
                  <label className="account-check">
                    <input
                      type="checkbox"
                      checked={accepted}
                      onChange={(e) => setAccepted(e.target.checked)}
                      required
                    />
                    <span>
                      我同意使用邮箱建立账号；邮箱用于验证、安全通知和我主动订阅的邮件。订单信息按授权范围展示。
                      <Link to="/u/help">查看使用与隐私说明</Link>
                    </span>
                  </label>
                </>
              )}
              {mode === "verify" && (
                <Notice>
                  {token
                    ? changingEmail
                      ? "确认后才会完成邮箱换绑；此前原邮箱继续有效。"
                      : "点击下方按钮确认邮箱，页面打开本身不会使用验证凭据。"
                    : "请从验证邮件中的链接打开本页；链接失效时可重新发送。"}
                </Notice>
              )}
              {mode === "reset" && !token && (
                <Notice type="warning">
                  重置链接缺失或已使用，请重新申请找回邮件。
                </Notice>
              )}
              {mode === "login" && deviceOptions ? (
                <div className="account-device-picker">
                  <strong>登录设备已满</strong>
                  <p>
                    此账号最多同时登录 2 台设备。选择一台设备下线，当前设备即可登录。
                  </p>
                  {deviceOptions.length ? (
                    deviceOptions.map((session) => (
                      <button
                        key={session.id}
                        type="button"
                        className="account-device-option"
                        disabled={action.busy}
                        onClick={() => void action.run(() => login(session.id))}
                      >
                        <span>
                          <b>{session.device}</b>
                          <small>
                            {session.current
                              ? "当前设备"
                              : session.lastSeenAt
                                ? `最近使用 ${new Date(session.lastSeenAt).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}`
                                : "登录设备"}
                          </small>
                        </span>
                        <em>下线并登录</em>
                      </button>
                    ))
                  ) : (
                    <p>设备列表暂不可用，请稍后重试。</p>
                  )}
                  <button
                    type="button"
                    className="text-button"
                    disabled={action.busy}
                    onClick={() => setDeviceOptions(null)}
                  >
                    返回重新输入
                  </button>
                </div>
              ) : (
                <Button
                  busy={action.busy}
                  busyTarget={action.control}
                  disabled={
                    action.busy ||
                    ((mode === "verify" || mode === "reset") && !token)
                  }
                >
                  {action.busy
                    ? "正在提交…"
                    : {
                        login: "登录",
                        register: "注册并申请验证邮件",
                        forgot: "申请重置邮件",
                        verify: changingEmail ? "确认换绑新邮箱" : "确认验证邮箱",
                        reset: "设置新密码",
                      }[mode]}
                  <ArrowRight size={16} />
                </Button>
              )}
            </form>
          )}
          {verifyControls && (
            <section className="account-stack account-verification">
              <h3>没有收到验证邮件？</h3>
              <p>先检查垃圾邮件。只有本次注册的受限挑战可以重发或更正地址。</p>
              <Button
                variant="secondary"
                disabled={!challenge || action.busy || cooldown > 0}
                onClick={() =>
                  void action.run(async () => {
                    if (!challenge) return;
                    receivedMail(
                      await growthRequest<MailResult>(
                        "/customer/auth/resend-verification",
                        {
                          method: "POST",
                          body: { registrationChallengeId: challenge.id },
                        },
                      ),
                    );
                  })
                }
              >
                {cooldown ? `${cooldown} 秒后可重发` : "重新申请验证邮件"}
              </Button>
              {challenge ? (
                <form
                  className="account-stack"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void action.run(async () => {
                      receivedMail(
                        await growthRequest<MailResult>(
                          "/customer/auth/change-pending-email",
                          {
                            method: "POST",
                            body: {
                              registrationChallengeId: challenge.id,
                              newEmail: email.trim(),
                            },
                          },
                        ),
                      );
                    });
                  }}
                >
                  <Field label="更正待验证邮箱">
                    <input
                      type="email"
                      required
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                    />
                  </Field>
                  <Button
                    variant="secondary"
                    busy={action.busy}
                    busyTarget={action.control}
                    disabled={action.busy}
                  >
                    更正邮箱并重新申请验证
                  </Button>
                </form>
              ) : (
                <Notice>
                  本次注册挑战已不在内存中，请重新注册申请验证邮件。已验证账号请到账号设置更换邮箱。
                </Notice>
              )}
            </section>
          )}
          <div className="account-auth-links">
            {mode !== "login" &&
              (embedded ? (
                <button
                  type="button"
                  className="text-button"
                  onClick={() => {
                    setMode("login");
                    setRegistered(false);
                    setCompleted(false);
                    setMail(null);
                  }}
                >
                  返回登录
                </button>
              ) : (
                <Link to="/u/account/login">返回登录</Link>
              ))}
            {mode === "login" &&
              (embedded ? (
                <>
                  <button
                    type="button"
                    className="text-button"
                    onClick={() => setMode("register")}
                  >
                    注册账号
                  </button>
                  <button
                    type="button"
                    className="text-button"
                    onClick={() => setMode("forgot")}
                  >
                    忘记密码
                  </button>
                </>
              ) : (
                <>
                  <Link to="/u/account/register">注册账号</Link>
                  <Link to="/u/account/forgot">忘记密码</Link>
                </>
              ))}
            {(mode === "reset" || mode === "verify") && !embedded && (
              <Link
                to={
                  mode === "reset" ? "/u/account/forgot" : "/u/account/register"
                }
              >
                重新申请
              </Link>
            )}
            {embedded ? (
              <button type="button" className="text-button" onClick={onSuccess}>
                继续游客打印
              </button>
            ) : (
              <Link to="/u/">继续游客打印</Link>
            )}
          </div>
          {DEMO && mode === "login" && (
            <Button
              variant="secondary"
              busy={action.busy}
              busyTarget={action.control}
              disabled={action.busy}
              onClick={() =>
                void action.run(async () => {
                  await identity.demoCustomer();
                  if (embedded) onSuccess?.();
                  else setLinkPrompt(true);
                })
              }
            >
              体验演示账号
            </Button>
          )}
        </>
      )}
    </div>
  );
}
function AuthPage({
  mode,
  title,
  description,
}: {
  mode: AuthMode;
  title: string;
  description: string;
}) {
  return (
    <div className="account-page account-auth-page">
      <PageHeading title={title} description={description} />
      <Panel>
        <div className="account-mark">
          <Mail size={24} />
        </div>
        <CustomerAuthForm mode={mode} />
      </Panel>
    </div>
  );
}
export function AccountLoginPage() {
  return (
    <AuthPage
      mode="login"
      title="欢迎回来"
      description="登录后查看账号订单与优惠，游客仍可随时打印。"
    />
  );
}
export function AccountRegisterPage() {
  return (
    <AuthPage
      mode="register"
      title="建立你的打印账号"
      description="验证邮箱后启用账号，当前打印流程继续保留。"
    />
  );
}
export function AccountVerifyPage() {
  return (
    <AuthPage
      mode="verify"
      title="确认邮箱"
      description="请通过邮件链接，并主动确认完成验证。"
    />
  );
}
export function AccountForgotPage() {
  return (
    <AuthPage
      mode="forgot"
      title="找回密码"
      description="提交邮箱后按邮件提示操作，回复不会透露账号是否存在。"
    />
  );
}
export function AccountResetPage() {
  return (
    <AuthPage
      mode="reset"
      title="设置新密码"
      description="重置凭据只在本页内存中暂存，完成后请重新登录。"
    />
  );
}
function AccountGate({ children }: { children: ReactNode }) {
  const { customer } = useIdentity();
  return customer.authKind === "REGISTERED" && customer.account?.verified ? (
    <>{children}</>
  ) : (
    <div className="account-page">
      <StaffAccessNotice />
      <StaffPortalPanel />
      <Panel>
        <Empty
          title="登录已验证的账号"
          text="账号资料、优惠和邀请记录需要邮箱验证。你仍可继续游客打印。"
          action={
            <div className="account-actions">
              <Link className="btn" to="/u/account/login">
                登录 / 注册
              </Link>
              <Link className="btn secondary" to="/u/">
                继续打印
              </Link>
            </div>
          }
        />
      </Panel>
    </div>
  );
}
export function AccountPage() {
  return (
    <AccountGate>
      <AccountContent />
    </AccountGate>
  );
}
function AccountContent() {
  const identity = useIdentity(),
    { toast, refresh } = useStore(),
    action = useAction(),
    navigate = useNavigate();
  const resource = useGrowthResource<CustomerAccount>("/customer/account");
  const [name, setName] = useState(""),
    [currentPassword, setCurrentPassword] = useState(""),
    [newPassword, setNewPassword] = useState(""),
    [confirmPassword, setConfirmPassword] = useState(""),
    [newEmail, setNewEmail] = useState(""),
    [emailPassword, setEmailPassword] = useState(""),
    [linking, setLinking] = useState(false),
    [mail, setMail] = useState<MailResult | null>(null);
  const [preferences, setPreferences] = useState<
    CustomerAccount["notificationPreferences"]
  >({ orderCreated: false, orderStatusUpdated: false, referralReward: false });
  const hydratedGeneration = useRef<number | null>(null);
  useEffect(() => {
    if (resource.data && hydratedGeneration.current !== identity.generation) {
      hydratedGeneration.current = identity.generation;
      setName(resource.data.displayName);
      setPreferences(resource.data.notificationPreferences);
    }
  }, [resource.data, identity.generation]);
  if (resource.loading) return <ResourceSkeleton variant="profile" />;
  if (resource.error && !resource.hasData)
    return (
      <ErrorState message={messageOf(resource.error)} retry={resource.reload} />
    );
  if (!resource.data)
    return <ErrorState message="账号资料暂不可用。" retry={resource.reload} />;
  const account = resource.data;
  const update = async (path: string, body: unknown, method = "POST") => {
    await growthRequest(path, { method, body });
    await identity.refreshCustomer();
    // The customer nickname is also the employee display name.  Refresh the
    // staff probe as well so the 员工入口 card and workspace headers update
    // immediately without a full page reload.
    await identity.refreshStaff();
    resource.reload();
  };
  return (
    <div className="account-page">
      <PageHeading
        title="账号设置"
        description="管理资料、安全与本机游客订单关联。"
        action={
          <Link className="btn secondary" to="/u/account/security">
            <ShieldCheck size={16} />
            账号安全
          </Link>
        }
      />
      <Feedback action={action} />
      <RefreshStatus
        loading={resource.refreshing}
        error={resource.hasData ? resource.error : undefined}
        retry={resource.reload}
      />
      <StaffAccessNotice />
      <StaffPortalPanel />
      <div className="account-grid">
        <Panel title="我的资料">
          <form
            className="account-stack"
            onSubmit={(e) => {
              e.preventDefault();
              void action.run(async () => {
                await update(
                  "/customer/account",
                  {
                    displayName: name.trim(),
                    expectedVersion: account.version,
                  },
                  "PATCH",
                );
                action.setSuccess("资料已更新。");
              });
            }}
          >
            <div className="account-profile">
              <span className="account-mark">
                <UserRound />
              </span>
              <div>
                <strong>{account.emailMasked}</strong>
                <small>
                  邮箱已验证 ·{" "}
                  {account.status === "ACTIVE"
                    ? "账号正常"
                    : account.status === "SUSPENDED"
                      ? "账号已停用"
                      : "待验证"}
                </small>
              </div>
            </div>
            <Field label="显示昵称">
              <input
                required
                maxLength={40}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </Field>
            <Button
              busy={action.busy}
              busyTarget={action.control}
              disabled={action.busy}
            >
              保存资料
            </Button>
          </form>
        </Panel>
        <Panel title="邮件通知偏好">
          <form
            className="account-stack"
            onSubmit={(e) => {
              e.preventDefault();
              void action.run(async () => {
                await update(
                  "/customer/account/notification-preferences",
                  { ...preferences, expectedVersion: account.version },
                  "PATCH",
                );
                action.setSuccess("通知偏好已保存。");
              });
            }}
          >
            {(
              [["referralReward", "邀请奖励到账通知"]] as const
            ).map(([key, label]) => (
              <label key={key} className="account-check">
                <input
                  type="checkbox"
                  checked={preferences[key]}
                  onChange={(e) =>
                    setPreferences((p) => ({ ...p, [key]: e.target.checked }))
                  }
                />
                {label}
              </label>
            ))}
            <small>只有邀请奖励等运营邮件受此开关控制；付款成功、签收成功、账号安全与注销通知属于事务性邮件，会直接发送，不受开关影响。</small>
            <Button
              busy={action.busy}
              busyTarget={action.control}
              disabled={action.busy}
            >
              保存通知偏好
            </Button>
          </form>
        </Panel>
        <Panel title="修改密码">
          <form
            className="account-stack"
            onSubmit={(e) => {
              e.preventDefault();
              void action.run(async () => {
                if (newPassword !== confirmPassword)
                  throw new Error("两次新密码不一致。");
                await update("/customer/account/change-password", {
                  currentPassword,
                  newPassword,
                });
                setCurrentPassword("");
                setNewPassword("");
                setConfirmPassword("");
                action.setSuccess("密码已修改，请检查账号安全中的会话设备。");
              });
            }}
          >
            <PasswordInput
              label="当前密码"
              value={currentPassword}
              onChange={setCurrentPassword}
            />
            <PasswordInput
              label="新密码"
              autoComplete="new-password"
              minLength={8}
              value={newPassword}
              onChange={setNewPassword}
            />
            <PasswordInput
              label="确认新密码"
              autoComplete="new-password"
              minLength={8}
              value={confirmPassword}
              onChange={setConfirmPassword}
            />
            <Button
              busy={action.busy}
              busyTarget={action.control}
              disabled={action.busy}
            >
              更新密码
            </Button>
          </form>
        </Panel>
        <Panel title="更换邮箱">
          <form
            className="account-stack"
            onSubmit={(e) => {
              e.preventDefault();
              void action.run(async () => {
                setMail(
                  await growthRequest<MailResult>(
                    "/customer/account/request-email-change",
                    {
                      method: "POST",
                      body: {
                        newEmail: newEmail.trim(),
                        currentPassword: emailPassword,
                        expectedVersion: account.version,
                      },
                    },
                  ),
                );
                setEmailPassword("");
              });
            }}
          >
            <Notice>
              当前邮箱 {account.emailMasked}{" "}
              在新邮箱验证完成前继续有效。可重新提交以重发，最终结果由邮件中的确认按钮完成。
            </Notice>
            {mail && <MailStatus response={mail} />}
            <Field label="新邮箱">
              <input
                required
                type="email"
                autoComplete="email"
                value={newEmail}
                onChange={(e) => setNewEmail(e.target.value)}
              />
            </Field>
            <PasswordInput
              label="确认当前密码"
              value={emailPassword}
              onChange={setEmailPassword}
            />
            <Button
              busy={action.busy}
              busyTarget={action.control}
              disabled={action.busy}
            >
              申请新邮箱验证邮件
            </Button>
          </form>
        </Panel>
      </div>
      <Panel title="订单与身份">
        <div className="account-stack">
          <p>
            登录不会自动认领游客历史。你可明确关联这台设备当前游客身份下的订单；已授权的单个订单请在订单详情中选择“添加这一单到账号”。
          </p>
          <div className="account-actions">
            <Button variant="secondary" onClick={() => setLinking(true)}>
              关联本机游客订单
            </Button>
            <Link className="btn secondary" to="/u/orders">
              查看我的订单
            </Link>
            <Link className="btn secondary" to="/u/rewards">
              我的优惠
            </Link>
            <Link className="btn secondary" to="/u/invite">
              邀请好友
            </Link>
            <Link className="btn secondary" to="/u/account/data">
              资料导出与注销
            </Link>
          </div>
          <Button
            variant="danger"
            busy={action.busy}
            busyTarget={action.control}
            disabled={action.busy}
            onClick={() =>
              void action.run(async () => {
                await identity.logoutCustomer();
                await refresh();
                toast("客户账号已退出");
                navigate("/u/", { replace: true });
              })
            }
          >
            退出客户账号
          </Button>
        </div>
      </Panel>
      <PersonalDataPanel />
      {linking && (
        <Modal title="确认关联本机游客订单" onClose={() => setLinking(false)}>
          <GuestLinkConfirm
            onDone={() => {
              setLinking(false);
              toast("已更新订单关联选择");
            }}
          />
        </Modal>
      )}
    </div>
  );
}
export function AccountSecurityPage() {
  return (
    <AccountGate>
      <SecurityContent />
    </AccountGate>
  );
}
function SecurityContent() {
  const identity = useIdentity(),
    action = useAction();
  const resource = useGrowthResource<{ items: SessionDevice[] }>(
    "/customer/account/sessions",
  );
  const [selected, setSelected] = useState<SessionDevice | "others" | null>(
    null,
  );
  return (
    <div className="account-page">
      <PageHeading
        title="账号安全"
        description="检查登录设备，及时撤销不再使用的会话。"
        action={
          <Link to="/u/account" className="btn secondary">
            返回账号
          </Link>
        }
      />
      <Notice>
        邮箱已验证。设备信息由服务器记录；撤销后相关设备需要重新登录。
      </Notice>
      <Feedback action={action} />
      <Panel
        title="登录设备"
        action={
          <Button
            variant="secondary"
            busy={action.busy}
            busyTarget={action.control}
            disabled={
              action.busy || !resource.data?.items.some((s) => !s.current)
            }
            onClick={() => setSelected("others")}
          >
            退出其他设备
          </Button>
        }
      >
        <RefreshStatus
          loading={resource.refreshing}
          error={resource.hasData ? resource.error : undefined}
          retry={resource.reload}
        />
        {resource.loading ? (
          <ResourceSkeleton variant="profile" />
        ) : resource.error && !resource.hasData ? (
          <ErrorState
            message={messageOf(resource.error)}
            retry={resource.reload}
          />
        ) : !resource.data?.items.length ? (
          <Empty title="暂无设备记录" text="服务器尚未提供可展示的登录设备。" />
        ) : (
          <div className="account-stack">
            {resource.data.items.map((session) => (
              <div className="account-row" key={session.id}>
                <LockKeyhole size={20} />
                <div>
                  <strong>{session.device}</strong>
                  <small>
                    最近活动：{dateLabel(session.lastSeenAt)}
                    {session.current && " · 当前设备"}
                  </small>
                </div>
                <Button
                  variant="secondary"
                  className="session-action"
                  aria-label={
                    session.current
                      ? "退出当前设备"
                      : `撤销设备会话：${session.device}`
                  }
                  title={session.current ? "退出当前设备" : "撤销设备会话"}
                  busy={action.busy}
                  busyTarget={action.control}
                  disabled={action.busy}
                  onClick={() => setSelected(session)}
                >
                  <LogOut size={18} />
                </Button>
              </div>
            ))}
          </div>
        )}
      </Panel>
      {selected && (
        <Modal title="确认撤销会话" onClose={() => setSelected(null)}>
          <div className="account-stack">
            <p>
              {selected === "others"
                ? "其他设备将退出账号。"
                : selected.current
                  ? "当前设备将退出账号，需要重新登录。"
                  : `撤销 ${selected.device} 的账号访问权限？`}
            </p>
            <Feedback action={action} />
            <Button
              busy={action.busy}
              busyTarget={action.control}
              disabled={action.busy}
              onClick={() =>
                void action.run(async () => {
                  await growthRequest("/customer/account/sessions/revoke", {
                    method: "POST",
                    body:
                      selected === "others"
                        ? { sessionId: "ALL_OTHERS" }
                        : { sessionId: selected.id },
                  });
                  const current = selected !== "others" && selected.current;
                  setSelected(null);
                  if (current) await identity.refreshCustomer();
                  else resource.reload();
                  action.setSuccess("会话已撤销。");
                })
              }
            >
              确认撤销
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}
export function RewardsPage() {
  return (
    <AccountGate>
      <RewardsContent />
    </AccountGate>
  );
}
function RewardsContent() {
  const resource = useGrowthResource<{ items: Coupon[] }>("/customer/coupons");
  const [tab, setTab] = useState("AVAILABLE");
  const tabs = [
    ["AVAILABLE", "可使用"],
    ["RESERVED", "已预约"],
    ["USED", "已使用"],
    ["INACTIVE", "已失效"],
  ];
  const items =
    resource.data?.items.filter((coupon) => couponTab(coupon.status) === tab) ||
    [];
  return (
    <div className="account-page">
      <PageHeading
        title="我的优惠"
        description="优惠资格以结算时报价为准，每单最多使用一张优惠券。"
        action={
          <Link to="/u/invite" className="btn secondary">
            <Gift size={16} />
            邀请好友
          </Link>
        }
      />
      <Notice>
        券有独立的预约、使用与失效状态。风控冻结会单独标注，冻结期间不可使用；打印满量优惠与券会按服务端规则择优。
      </Notice>
      <div className="account-tabs" role="tablist" aria-label="优惠券状态">
        {tabs.map(([key, label]) => (
          <button
            key={key}
            role="tab"
            aria-selected={key === tab}
            onClick={() => setTab(key)}
          >
            {label}
            <small>
              {resource.data?.items.filter((c) => couponTab(c.status) === key)
                .length || 0}
            </small>
          </button>
        ))}
      </div>
      <RefreshStatus
        loading={resource.refreshing}
        error={resource.hasData ? resource.error : undefined}
        retry={resource.reload}
      />
      {resource.loading ? (
        <ResourceSkeleton variant="coupons" />
      ) : resource.error && !resource.hasData ? (
        <ErrorState
          message={messageOf(resource.error)}
          retry={resource.reload}
        />
      ) : !items.length ? (
        <Panel>
          <Empty
            title="暂无这类优惠券"
            text="活动未开启或暂不满足资格时不会发放；请以实际活动规则为准。"
          />
        </Panel>
      ) : (
        <div className="account-coupon-grid">
          {items.map((coupon) => (
            <Panel
              className={`account-coupon ${coupon.riskHold === "FROZEN" ? "frozen" : ""}`}
              key={coupon.couponGrantId}
            >
              <div className="account-coupon-top">
                <Ticket size={21} />
                <strong>{money(coupon.amountCents)}</strong>
                <span>{coupon.name}</span>
              </div>
              <p>
                {coupon.minSpendCents
                  ? `符合范围的金额满 ${money(coupon.minSpendCents)} 可用`
                  : "符合适用服务即可使用"}
              </p>
              <dl>
                <div>
                  <dt>适用服务</dt>
                  <dd>
                    {
                      {
                        A4: "A4 文档打印",
                        PHOTO: "照片打印",
                        ALL: "全部已配置服务",
                      }[coupon.service]
                    }
                  </dd>
                </div>
                <div>
                  <dt>有效期至</dt>
                  <dd>{dateLabel(coupon.validUntil)}</dd>
                </div>
                <div>
                  <dt>当前状态</dt>
                  <dd>
                    {
                      tabs.find(
                        ([key]) => key === couponTab(coupon.status),
                      )?.[1]
                    }
                    {coupon.status === "REVOKED" && "（已撤销）"}
                  </dd>
                </div>
              </dl>
              {coupon.riskHold === "FROZEN" && (
                <Notice type="warning">风险审核冻结，暂不可使用。</Notice>
              )}
              {coupon.reason && <Notice>{coupon.reason}</Notice>}
              {coupon.reservedOrderId && (
                <Link
                  to={`/u/orders/${encodeURIComponent(coupon.reservedOrderId)}`}
                  className="text-link"
                >
                  查看预约订单
                </Link>
              )}
            </Panel>
          ))}
        </div>
      )}
    </div>
  );
}
export function InvitePage() {
  return (
    <AccountGate>
      <InviteContent />
    </AccountGate>
  );
}
function InviteContent() {
  const referrals = useGrowthResource<{
    items: ReferralRecord[];
    total: number;
    qualified: number;
    granted: number;
  }>("/customer/referrals");
  const code = useGrowthResource<{
    code: string;
    shareText: string;
    rules: string | string[];
  }>("/customer/referrals/code");
  const inviteUrl =
    typeof window !== "undefined" && code.data?.code
      ? inviteRegistrationUrl(window.location.origin, code.data.code)
      : "";
  const labels: Record<ReferralRecord["status"], string> = {
    WAITING_FIRST_ORDER: "待合格首单",
    QUALIFIED: "已达标",
    PENDING_BUDGET: "待奖励额度",
    GRANTED: "奖励已发",
    DISQUALIFIED: "不合格",
    REVERSAL_REVIEW: "纠错审核中",
  };
  return (
    <div className="account-page">
      <PageHeading
        title="邀请好友"
        description="分享你的邀请码，奖励按照当前已启用的活动规则认定。"
      />
      <Panel title="我的邀请码">
        <RefreshStatus
          loading={code.refreshing}
          error={code.hasData ? code.error : undefined}
          retry={code.reload}
        />
        {code.loading ? (
          <ResourceSkeleton variant="detail" />
        ) : code.error && !code.hasData ? (
          <ErrorState message={messageOf(code.error)} retry={code.reload} />
        ) : code.data?.code ? (
          <div className="account-stack">
            <div className="account-invite-code">
              <strong>{code.data.code}</strong>
              <div className="account-actions">
                <CopyButton text={code.data.code} label="复制邀请码" />
                <LinkQrButton value={inviteUrl} label="邀请二维码" />
              </div>
            </div>
            <p className="invite-link-description">
              好友打开分享链接后，会直接进入注册页并自动填写你的邀请码。
            </p>
            <CopyButton
              text={inviteShareText(
                window.location.origin,
                code.data.code,
                code.data.shareText,
              )}
              label="复制邀请文案与链接"
            />
            <Notice>
              {Array.isArray(code.data.rules)
                ? code.data.rules.join("；")
                : code.data.rules ||
                  "当前没有可承诺的奖励活动，请以实际启用规则为准。"}
            </Notice>
          </div>
        ) : (
          <Empty
            title="邀请码暂未开放"
            text="当前没有可分享的邀请码或奖励配置。"
          />
        )}
      </Panel>
      <Notice>
        邀请资格在受邀人完成第一笔成功送达、实付大于 0
        的合格订单后认定。仅注册、待送达或零元订单不会触发邀请奖励；具体金额、额度及有效期以当前规则为准。
      </Notice>
      <Panel title="邀请记录">
        <RefreshStatus
          loading={referrals.refreshing}
          error={referrals.hasData ? referrals.error : undefined}
          retry={referrals.reload}
        />
        {referrals.loading ? (
          <ResourceSkeleton variant="cards" />
        ) : referrals.error && !referrals.hasData ? (
          <ErrorState
            message={messageOf(referrals.error)}
            retry={referrals.reload}
          />
        ) : (
          <>
            <div className="account-stat-grid">
              {[
                ["邀请总数", referrals.data?.total || 0],
                ["已达标", referrals.data?.qualified || 0],
                ["奖励已发", referrals.data?.granted || 0],
              ].map(([label, value]) => (
                <div key={label}>
                  <strong>{value}</strong>
                  <small>{label}</small>
                </div>
              ))}
            </div>
            {!referrals.data?.items.length ? (
              <Empty
                title="还没有邀请记录"
                text="好友验证账号后，邀请进展会显示在这里。"
              />
            ) : (
              <div className="account-stack">
                {referrals.data.items.map((record) => (
                  <div className="account-referral" key={record.id}>
                    <div>
                      <strong>{safeInviteeLabel(record.inviteeLabel)}</strong>
                      <span className="badge gray">
                        {labels[record.status]}
                      </span>
                    </div>
                    <small>{dateLabel(record.createdAt)}</small>
                    <p>{record.rewardText || "按实际活动规则核验奖励"}</p>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </Panel>
    </div>
  );
}

export function OrderLinkPage() {
  const identity = useIdentity(),
    store = useStore(),
    navigate = useNavigate(),
    action = useAction();
  const [token, setToken] = useFragmentToken(),
    [orderId] = useState(
      () => new URLSearchParams(window.location.search).get("orderId") || "",
    ),
    [order, setOrder] = useState<Order | null>(null),
    [confirmImport, setConfirmImport] = useState(false);
  const started = useRef(false),
    generation = useRef(identity.generation);
  generation.current = identity.generation;
  const open = () =>
    navigate(`/u/orders/${encodeURIComponent(orderId)}`, { replace: true });
  const exchange = async () => {
    if (!orderId) throw new Error("链接缺少订单信息，请联系打印员重新发送。");
    if (!token) {
      if (identity.orderAccess?.orderId === orderId) {
        open();
        return;
      }
      throw new Error("链接缺少有效访问凭据，请联系打印员重新发送。");
    }
    const expectedGeneration = identity.generation;
    const result = await exchangeOnce(token, () =>
      growthRequest<{ order: Order; access: OrderAccess }>(
        "/order-access/exchange",
        { method: "POST", body: { orderId, accessToken: token } },
      ),
    );
    if (generation.current !== expectedGeneration)
      throw new Error("身份已变更，请重新打开订单链接。");
    if (result.order.id !== orderId || result.access.orderId !== orderId)
      throw new Error("订单授权与链接不符，请重新获取链接。");
    setToken("");
    identity.setOrderAccess(result.access);
    setOrder(result.order);
    store.setData((s) => ({
      ...s,
      orders: [...s.orders.filter((o) => o.id !== orderId), result.order],
    }));
    if (!(
      identity.customer.authKind === "REGISTERED" &&
      identity.customer.account?.verified &&
      result.access.scopes.includes("ORDER_IMPORT")
    ))
      open();
  };
  useEffect(() => {
    if (!started.current) {
      started.current = true;
      void action.run(exchange);
    }
  }, []);
  return (
    <div className="account-page account-auth-page">
      <PageHeading
        title="打开专属订单"
        description="此链接仅授权这一单，请妥善保管访问凭据。"
      />
      <Panel>
        {action.busy && !order ? (
          <ResourceSkeleton variant="profile" />
        ) : (
          <>
            <Feedback action={action} />
            {order ? (
              <div className="account-stack">
                <div className="account-success">
                  <ShieldCheck size={30} />
                  <h2>这一单已授权访问</h2>
                </div>
                <p>
                  你可直接查看订单，也可明确将这一单添加到已验证的账号。添加只保留源链接允许的权限和有效期，不关联其他游客订单。
                </p>
                <Button onClick={open}>
                  查看这一单
                  <ArrowRight size={16} />
                </Button>
                {identity.customer.account?.verified &&
                  identity.orderAccess?.orderId === orderId &&
                  identity.orderAccess.scopes.includes("ORDER_IMPORT") && (
                    <Button
                      variant="secondary"
                      onClick={() => setConfirmImport(true)}
                    >
                      添加这一单到账号
                    </Button>
                  )}
              </div>
            ) : (
              <div className="account-stack">
                <Notice type="warning">
                  链接已失效、缺少凭据或暂时无法兑换时，请联系打印员重发专属链接。手机号、昵称和提取码不能用于查询私有订单。
                </Notice>
                {token && (
                  <Button
                    variant="secondary"
                    busy={action.busy}
                    busyTarget={action.control}
                    disabled={action.busy}
                    onClick={() => void action.run(exchange)}
                  >
                    重新兑换
                  </Button>
                )}
                <Link to="/u/" className="btn secondary">
                  回到打印首页
                </Link>
              </div>
            )}
          </>
        )}
      </Panel>
      {confirmImport && (
        <Modal title="确认添加这一单" onClose={() => setConfirmImport(false)}>
          <div className="account-stack">
            <Notice>
              仅添加当前授权的订单，不转移订单所有权；源链接撤销或到期后，该派生授权随之失效。
            </Notice>
            <Feedback action={action} />
            <Button
              busy={action.busy}
              busyTarget={action.control}
              disabled={action.busy}
              onClick={() =>
                void action.run(async () => {
                  if (
                    !identity.customer.account?.verified ||
                    identity.orderAccess?.orderId !== orderId ||
                    !identity.orderAccess.scopes.includes("ORDER_IMPORT")
                  )
                    throw new Error("需要已验证账号和有效的单订单导入授权。");
                  await growthRequest("/customer/account/import-order", {
                    method: "POST",
                    body: { orderId, confirm: true },
                  });
                  setConfirmImport(false);
                  store.toast("这一单已添加到账号");
                  open();
                })
              }
            >
              确认添加这一单
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}

/** Detail-page affordance: no automatic guest merge or order ownership change. */
export function OrderImportButton({
  orderId,
  onSuccess,
}: {
  orderId: string;
  onSuccess?: () => void;
}) {
  const identity = useIdentity(),
    { toast } = useStore(),
    action = useAction();
  const [confirm, setConfirm] = useState(false);
  if (
    !identity.customer.account?.verified ||
    identity.orderAccess?.orderId !== orderId ||
    !identity.orderAccess.scopes.includes("ORDER_IMPORT")
  )
    return null;
  return (
    <>
      <Button variant="secondary" onClick={() => setConfirm(true)}>
        添加这一单到账号
      </Button>
      {confirm && (
        <Modal title="确认添加这一单" onClose={() => setConfirm(false)}>
          <div className="account-stack">
            <Notice>
              仅添加本订单的有限授权，不转移所有权，也不关联其他游客订单。源链接撤销或到期后，派生授权同步失效。
            </Notice>
            <Feedback action={action} />
            <Button
              busy={action.busy}
              busyTarget={action.control}
              disabled={action.busy}
              onClick={() =>
                void action.run(async () => {
                  await growthRequest("/customer/account/import-order", {
                    method: "POST",
                    body: { orderId, confirm: true },
                  });
                  setConfirm(false);
                  toast("这一单已添加到账号");
                  onSuccess?.();
                })
              }
            >
              确认添加这一单
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}
