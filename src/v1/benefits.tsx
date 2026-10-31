import { useEffect, useState } from "react";
import {
  Button,
  ErrorState,
  Field,
  Notice,
  PageHeading,
  Panel,
} from "../components/ui";
import { growthRequest, useGrowthResource } from "../growth/client";
import { useIdentity } from "../growth/identity";
import { ApiError } from "../lib/api";
import "./benefits.css";

const MAX_BPS = 10_000;
const GRADE_LEVELS = [1, 2, 3, 4, 5, 6] as const;
export const NO_EXPIRY_LABEL = "员工身份有效期间长期有效，无固定到期日";

/** The benefits endpoints return the policy version from the config row. */
export type BenefitsPolicy = {
  id?: string;
  version: number;
  description?: string;
  grades: (number | null)[];
  updatedAt?: string;
};

/**
 * Kept structural on purpose: the current account/session payload is shared
 * with auth, and the benefits screen must not gain access from an unrelated
 * capability or role.
 */
export type BenefitsStaffSession = {
  id?: string;
  accountId?: string | null;
  isSuperAdmin?: boolean;
  discountLevel?: number | null;
  role?: string;
  capabilities?: string[];
};

export type StaffBenefitAccount = {
  id?: string | null;
  verified?: boolean;
  status?: string | null;
};

/** Optional quote snapshot supplied by the account/quote screen. */
export type StaffBenefitCandidate = {
  source?: string | null;
  discountLevel?: number | null;
  reductionBps?: number | null;
  payRateBps?: number | null;
};

export type StaffBenefitView = {
  source: "SUPER_ADMIN" | "STAFF_GRADE";
  level: number | null;
  title: string;
  rateLabel: string;
  expiryLabel: string;
};

/** Only the explicit super-admin flag may unlock benefits management. */
export function canManageBenefits(
  session: BenefitsStaffSession | null | undefined,
): boolean {
  return session?.isSuperAdmin === true;
}

function validBps(value: number): boolean {
  return Number.isFinite(value) && value >= 0 && value <= MAX_BPS;
}

/** Format reduction basis points as a percent, without trailing zeroes. */
export function formatBenefitPercent(bps: number): string {
  if (!validBps(bps)) throw new Error("折扣比例必须在 0% 到 100% 之间");
  return (bps / 100).toFixed(2).replace(/\.?0+$/, "");
}

/** The grade input value: blank = not configured, "0" = explicitly zero. */
export function gradePercentText(bps: number | null | undefined): string {
  if (bps === null || bps === undefined) return "";
  return formatBenefitPercent(bps);
}

/** Parse the admin's reduction percent into integer basis points. */
export function parseGradePercentText(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const percent = Number(trimmed);
  if (!Number.isFinite(percent) || percent < 0 || percent > 100)
    throw new Error("折扣比例必须在 0% 到 100% 之间");
  return Math.round(percent * 100);
}

/** Unambiguous reduction/payable copy for one grade. */
export function gradePercentLabel(bps: number | null | undefined): string {
  if (bps === null || bps === undefined) return "未配置（不享受该等级折扣）";
  const reduction = formatBenefitPercent(bps);
  const payable = formatBenefitPercent(MAX_BPS - bps);
  return `减少 ${reduction}% / 实付 ${payable}%`;
}

function normalizeGradeBps(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  const number =
    typeof value === "string" ? Number(value.trim()) : Number(value);
  if (!Number.isFinite(number))
    throw new Error("折扣比例必须是 0 到 10000 之间的整数基点");
  const bps = Math.round(number);
  if (!validBps(bps))
    throw new Error("折扣比例必须在 0% 到 100% 之间（0—10000 基点）");
  return bps;
}

/** Preserve zero and null; pad unconfigured trailing grades to six slots. */
export function normalizeBenefitGrades(input: unknown): (number | null)[] {
  if (!Array.isArray(input)) throw new Error("折扣等级必须是数组");
  if (input.length > GRADE_LEVELS.length)
    throw new Error("员工折扣固定为 1—6 级");
  const grades = input.map(normalizeGradeBps);
  while (grades.length < GRADE_LEVELS.length) grades.push(null);
  return grades;
}

/** Build the versioned PATCH body. Zero is data; it is never dropped. */
export function benefitsPatchPayload(
  version: number,
  grades: Array<number | null>,
  reason = "",
): { expectedVersion: number; grades: (number | null)[]; reason: string } {
  if (!Number.isInteger(version) || version < 1)
    throw new Error("缺少有效的配置版本，请刷新后重试");
  if (!Array.isArray(grades) || grades.length !== GRADE_LEVELS.length)
    throw new Error("员工折扣固定为 1—6 级");
  if (reason.length > 500) throw new Error("变更原因不能超过 500 个字符");
  return {
    expectedVersion: version,
    grades: grades.map(normalizeGradeBps),
    reason: reason.trim(),
  };
}

function candidateReduction(
  candidate: StaffBenefitCandidate | null | undefined,
  level: number,
): number | null {
  if (
    candidate?.source === "STAFF_GRADE" &&
    candidate.discountLevel === level &&
    typeof candidate.reductionBps === "number" &&
    validBps(candidate.reductionBps)
  )
    return candidate.reductionBps;
  return null;
}

/**
 * Pure summary model. A session only proves its own benefit; when an account is
 * supplied it must be the linked, verified customer account. Capabilities are
 * deliberately ignored, so a stale `["*"]` can never impersonate a super admin.
 */
export function staffBenefitView(
  session: BenefitsStaffSession | null | undefined,
  account?: StaffBenefitAccount | null,
  candidate?: StaffBenefitCandidate | null,
): StaffBenefitView | null {
  if (!session) return null;

  if (account !== undefined) {
    if (!account) return null;
    if (account.verified === false) return null;
    if (account.status && account.status !== "ACTIVE") return null;
    if (!session.accountId || !account.id) return null;
    if (session.accountId !== account.id) return null;
  }

  if (session.isSuperAdmin === true) {
    return {
      source: "SUPER_ADMIN",
      level: null,
      title: "超级管理员 · 打印免单",
      rateLabel: gradePercentLabel(MAX_BPS),
      expiryLabel: NO_EXPIRY_LABEL,
    };
  }

  const level = session.discountLevel;
  if (
    level === null ||
    level === undefined ||
    !Number.isInteger(level) ||
    level < 1 ||
    level > 6
  )
    return null;

  const reduction = candidateReduction(candidate, level);
  return {
    source: "STAFF_GRADE",
    level,
    title: `员工折扣 · 等级 ${level}`,
    rateLabel:
      reduction === null
        ? "报价时按当前等级配置自动计算减免"
        : gradePercentLabel(reduction),
    expiryLabel: NO_EXPIRY_LABEL,
  };
}

function gradePreview(text: string): string {
  try {
    return gradePercentLabel(parseGradePercentText(text));
  } catch {
    return "请输入 0—100 的比例";
  }
}

function useOnline() {
  const [online, setOnline] = useState(
    typeof navigator === "undefined" || navigator.onLine,
  );
  useEffect(() => {
    const sync = () => setOnline(navigator.onLine);
    window.addEventListener("online", sync);
    window.addEventListener("offline", sync);
    return () => {
      window.removeEventListener("online", sync);
      window.removeEventListener("offline", sync);
    };
  }, []);
  return online;
}

export function AdminBenefitsPage() {
  const sessionResource = useGrowthResource<BenefitsStaffSession>("/auth/me");
  const allowed = canManageBenefits(sessionResource.data);
  const policyResource = useGrowthResource<BenefitsPolicy>(
    allowed ? "/admin/config/benefits" : null,
  );
  const online = useOnline();
  const [version, setVersion] = useState<number | null>(null);
  const [drafts, setDrafts] = useState<string[]>(GRADE_LEVELS.map(() => ""));
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    const policy = policyResource.data;
    if (!policy) return;
    setVersion(policy.version);
    setDrafts(
      GRADE_LEVELS.map((_, index) =>
        gradePercentText(policy.grades?.[index] ?? null),
      ),
    );
    setMessage("");
  }, [policyResource.data]);

  const updateDraft = (index: number, value: string) => {
    setDrafts((current) =>
      current.map((item, position) => (position === index ? value : item)),
    );
    setMessage("");
  };

  const save = async () => {
    if (!allowed || version === null) return;
    setError("");
    setMessage("");
    let payload: ReturnType<typeof benefitsPatchPayload>;
    try {
      payload = benefitsPatchPayload(
        version,
        drafts.map(parseGradePercentText),
        reason,
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "配置校验失败");
      return;
    }
    setSaving(true);
    try {
      const saved = await growthRequest<BenefitsPolicy>(
        "/admin/config/benefits",
        { method: "PATCH", body: payload },
      );
      setVersion(saved.version);
      setDrafts(
        GRADE_LEVELS.map((_, index) =>
          gradePercentText(saved.grades?.[index] ?? null),
        ),
      );
      setMessage(
        `已保存。新报价立即使用该配置，已提交订单保持原报价快照。`,
      );
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        setError(
          "配置版本已变化（409）。请点击重新加载，确认其他管理员的最新配置后再修改。",
        );
      } else {
        setError(
          caught instanceof Error ? caught.message : "保存失败，请稍后重试",
        );
      }
    } finally {
      setSaving(false);
    }
  };

  if (sessionResource.loading)
    return (
      <div className="v1-benefits">
        <PageHeading
          title="员工打印折扣"
          description="按员工等级设置整单减免比例；员工身份有效期间长期有效，不设固定到期日。"
        />
        <Notice>
          <span role="status">正在确认员工折扣管理权限</span>
        </Notice>
      </div>
    );
  if (sessionResource.error)
    return (
      <ErrorState
        message="无法确认当前账号的超级管理员身份。"
        retry={() => void sessionResource.reload()}
      />
    );

  return (
    <div className="v1-benefits">
      <PageHeading
        title="员工打印折扣"
        description="按员工等级设置整单减免比例；员工身份有效期间长期有效，不设固定到期日。"
      />

      {!allowed && (
        <Notice type="warning">
          仅超级管理员可以配置员工折扣。当前账号不是明确启用的超级管理员，角色或“*”权限不会替代该身份标记。
        </Notice>
      )}

      {allowed && policyResource.loading && !policyResource.hasData && (
        <Notice>
          <span role="status">正在读取员工折扣配置</span>
        </Notice>
      )}

      {allowed && policyResource.error && !policyResource.hasData && (
        <ErrorState
          message={policyResource.error}
          retry={() => void policyResource.reload()}
        />
      )}

      {allowed && version !== null && (
        <Panel title="等级减免设置">
          <form
            className="v1-benefits-form"
            onSubmit={(event) => {
              event.preventDefault();
              void save();
            }}
          >
            <Notice>
              输入的是<strong>减少比例</strong>：20 表示减少 20%、客户实付 80%。
              留空表示该等级尚未配置；输入 0 表示该等级明确不减免（实付
              100%），两种状态不会混淆。
            </Notice>

            <div className="v1-benefit-grade-list">
              {GRADE_LEVELS.map((level, index) => (
                <div className="v1-benefit-grade-row" key={level}>
                  <div className="v1-benefit-grade-title">
                    <strong>等级 {level}</strong>
                    <span>{gradePreview(drafts[index])}</span>
                  </div>
                  <Field
                    label={`等级 ${level} 减少比例（%）`}
                    hint="0—100，最多两位小数；留空为未配置，0 为明确不减免。"
                  >
                    <input
                      type="number"
                      min="0"
                      max="100"
                      step="0.01"
                      inputMode="decimal"
                      value={drafts[index]}
                      placeholder="未配置"
                      onChange={(event) =>
                        updateDraft(index, event.target.value)
                      }
                    />
                  </Field>
                  <div className="v1-benefit-grade-preview">
                    {gradePreview(drafts[index])}
                  </div>
                </div>
              ))}
            </div>

            <Field
              label="变更原因（写入审计日志，可选）"
              hint="最多 500 个字符，便于回溯此次等级调整。"
            >
              <input
                value={reason}
                maxLength={500}
                onChange={(event) => setReason(event.target.value)}
                placeholder="例如：2026 秋季员工折扣调整"
              />
            </Field>

            <Notice type="warning">
              折扣按订单原始金额计算，覆盖
              A4、照片、装订等全部打印项目，并按整数分四舍五入分摊。
              员工折扣与优惠券、满量折扣自动择优且不叠加；同额时优先满量折扣，超级管理员免单优先于其他任何优惠。
              实付为 0 的订单仍需完成人工结算确认。
            </Notice>

            {error && (
              <Notice type="error">
                <span role="alert">{error}</span>
              </Notice>
            )}
            {message && <Notice type="success">{message}</Notice>}
            {!online && (
              <Notice type="warning">当前离线，保存暂不可用。</Notice>
            )}

            <div className="v1-benefits-actions">
              <Button busy={saving} disabled={saving || !online} type="submit">
                保存等级配置
              </Button>
              <Button
                type="button"
                variant="secondary"
                disabled={saving}
                onClick={() => {
                  setError("");
                  setMessage("");
                  void policyResource.reload();
                }}
              >
                重新加载最新配置
              </Button>
            </div>
          </form>
        </Panel>
      )}

      {allowed && version === null && !policyResource.loading && (
        <Notice type="warning">
          尚未读取到员工折扣配置版本。请重新加载后再修改。
        </Notice>
      )}

      <Panel title="规则说明">
        <ul className="v1-benefits-notes">
          <li>
            普通员工等级在员工身份有效、账号已绑定且启用期间长期有效，不设置到期日。
          </li>
          <li>
            超级管理员打印订单 100%
            免单，只要超级管理员身份处于启用状态即持续有效。
          </li>
          <li>
            报价由服务端按当前员工身份计算；客户端提交的等级、角色或权限不会被采信。
          </li>
          <li>
            配置保存后即生效。未提交报价在员工身份或等级变化后需要重新报价；已提交订单保留原报价快照。
          </li>
        </ul>
      </Panel>
    </div>
  );
}

export function StaffBenefitSummary({
  account,
  currentAccount,
  session: sessionOverride,
  candidate,
}: {
  account?: StaffBenefitAccount | null;
  currentAccount?: StaffBenefitAccount | null;
  session?: BenefitsStaffSession | null;
  candidate?: StaffBenefitCandidate | null;
}) {
  const identity = useIdentity() as unknown as {
    staff?: BenefitsStaffSession | null;
  } | null;
  const session =
    sessionOverride !== undefined ? sessionOverride : (identity?.staff ?? null);
  const scopedAccount = account !== undefined ? account : currentAccount;
  const view = staffBenefitView(session, scopedAccount, candidate);
  if (!view) return null;

  return (
    <section
      className="v1-benefit-summary"
      aria-label="员工打印折扣"
      data-source={view.source}
      data-level={view.level ?? undefined}
      data-expiry="none"
    >
      <div className="v1-benefit-summary-head">
        <span className="v1-benefit-kicker">员工权益</span>
        <strong>{view.title}</strong>
      </div>
      <div className="v1-benefit-summary-rate">{view.rateLabel}</div>
      <div className="v1-benefit-summary-expiry">{view.expiryLabel}</div>
      <p>
        报价时由服务端在员工等级、优惠券与满量折扣之间自动择优，不叠加；免单订单仍需完成人工结算确认。
      </p>
    </section>
  );
}

export default AdminBenefitsPage;
