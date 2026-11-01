import { useState, type FormEvent } from "react";
import { Button, Field, Modal, Notice } from "../../components/ui";
import { growthRequest } from "../../growth/client";
import type {
  ManualCouponIssueResult,
  ManualCouponRecipient,
} from "../../growth/types";
import { mutate, SubmitState, useSubmit } from "./common";
import { parseCents, yuan } from "./logic";

const services = [
  { value: "ALL", label: "全部可优惠商品" },
  { value: "A4", label: "A4 打印" },
  { value: "PHOTO", label: "照片打印" },
] as const;

const statusLabels: Record<string, string> = {
  ACTIVE: "账号正常",
  PENDING_EMAIL: "邮箱未验证",
  SUSPENDED: "账号已停用",
  DELETION_PENDING: "账号注销中",
};

function dayInShanghai(offsetDays = 0) {
  const value = new Date(Date.now() + offsetDays * 86_400_000);
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(value);
}

function endOfShanghaiDay(date: string) {
  return new Date(`${date}T23:59:59.999+08:00`).toISOString();
}

function shortEmail(value: string) {
  const limit = 26;
  if (value.length <= limit) return value;
  const at = value.indexOf("@");
  if (at <= 0) return `${value.slice(0, limit - 1)}…`;
  const local = value.slice(0, at);
  const room = limit - local.length - 2;
  if (room < 6) return `${value.slice(0, limit - 1)}…`;
  const domain = value.slice(at + 1, at + 1 + room).replace(/[.-]+$/, "");
  return `${local}@${domain}…`;
}

export function ManualCouponModal({
  onClose,
  onIssued,
}: {
  onClose: () => void;
  onIssued?: () => void;
}) {
  const submit = useSubmit();
  const [step, setStep] = useState<"lookup" | "form" | "done">("lookup");
  const [email, setEmail] = useState("");
  const [recipient, setRecipient] = useState<ManualCouponRecipient | null>(null);
  const [amount, setAmount] = useState("20.00");
  const [minSpend, setMinSpend] = useState("0");
  const [service, setService] = useState("ALL");
  const [validDate, setValidDate] = useState(dayInShanghai(7));
  const [name, setName] = useState("专享优惠券");
  const [reason, setReason] = useState("");
  const [notify, setNotify] = useState(true);
  const [result, setResult] = useState<ManualCouponIssueResult | null>(null);
  const disabled = submit.pending || !submit.online;

  const lookup = async (event: FormEvent) => {
    event.preventDefault();
    const address = email.trim();
    if (!address) {
      submit.setError("请输入客户邮箱");
      return;
    }
    const outcome = await submit.run(() =>
      growthRequest<ManualCouponRecipient>(
        `/admin/promotions/coupons/recipient?email=${encodeURIComponent(address)}`,
      ),
    );
    if (outcome.ok) {
      setRecipient(outcome.data);
      if (outcome.data.eligible) setStep("form");
    }
  };

  const issue = async (event: FormEvent) => {
    event.preventDefault();
    if (!recipient) return;
    let amountCents: number, minSpendCents: number;
    try {
      amountCents = parseCents(amount);
      minSpendCents = parseCents(minSpend);
    } catch (error) {
      submit.setError(error instanceof Error ? error.message : "金额格式不正确");
      return;
    }
    if (amountCents < 1 || amountCents > 20_000) {
      submit.setError("单张优惠券面额需在 ¥0.01 至 ¥200.00 之间");
      return;
    }
    if (!validDate) {
      submit.setError("请选择到期日期");
      return;
    }
    const validUntil = endOfShanghaiDay(validDate);
    const expiry = new Date(validUntil).getTime();
    if (!Number.isFinite(expiry) || expiry <= Date.now() + 5 * 60_000) {
      submit.setError("到期时间需晚于当前时间 5 分钟");
      return;
    }
    if (expiry > Date.now() + 365 * 86_400_000) {
      submit.setError("到期时间不能超过 365 天");
      return;
    }
    const internalReason = reason.trim();
    if (internalReason.length < 2) {
      submit.setError("请填写至少 2 个字符的发放原因，便于后续审计");
      return;
    }
    const outcome = await submit.run(
      () =>
        mutate<ManualCouponIssueResult>("/admin/promotions/coupons/manual", {
          email: email.trim(),
          amountCents,
          minSpendCents,
          service,
          validUntil,
          name: name.trim() || undefined,
          reason: internalReason,
          notify,
        }),
      "优惠券已发放",
    );
    if (outcome.ok) {
      setResult(outcome.data);
      setStep("done");
      onIssued?.();
    }
  };

  const reset = () => {
    setStep("lookup");
    setRecipient(null);
    setResult(null);
    setReason("");
    setEmail("");
    submit.setError("");
  };

  return (
    <Modal
      title="人工发券"
      wide
      onClose={() => !submit.pending && onClose()}
    >
      <div className="manual-coupon">
        <SubmitState state={submit} />
        {step === "lookup" && (
          <>
            <p className="manual-coupon__hint">
              按客户注册邮箱精确查找账号。未验证、已停用或注销中的账号不能收券。
            </p>
            <form className="growth-stack" onSubmit={lookup}>
              <Field label="客户邮箱" hint="请输入完整邮箱地址，不区分大小写">
                <input
                  type="email"
                  inputMode="email"
                  autoComplete="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="customer@example.com"
                  autoFocus
                />
              </Field>
              <div className="manual-coupon__actions">
                <Button type="button" variant="secondary" disabled={submit.pending} onClick={onClose}>
                  取消
                </Button>
                <Button busy={submit.pending} disabled={disabled || !email.trim()}>
                  查找客户
                </Button>
              </div>
            </form>
            {recipient && <RecipientCard recipient={recipient} />}
            {recipient?.eligible && (
              <Notice type="success">
                已找到可发放账号。下一步填写券面额、使用门槛与到期时间。
                <div className="manual-coupon__inline-action">
                  <Button onClick={() => setStep("form")}>填写发放信息</Button>
                </div>
              </Notice>
            )}
          </>
        )}

        {step === "form" && recipient && (
          <>
            <RecipientCard recipient={recipient} compact />
            <form className="growth-stack" onSubmit={issue}>
              <div className="growth-form-grid">
                <Field label="券面额（元）" hint="单张不超过 ¥200">
                  <input
                    inputMode="decimal"
                    value={amount}
                    onChange={(event) => setAmount(event.target.value)}
                  />
                </Field>
                <Field label="使用门槛（元）" hint="0 表示无门槛">
                  <input
                    inputMode="decimal"
                    value={minSpend}
                    onChange={(event) => setMinSpend(event.target.value)}
                  />
                </Field>
                <Field label="适用服务">
                  <select
                    value={service}
                    onChange={(event) => setService(event.target.value)}
                  >
                    {services.map((item) => (
                      <option key={item.value} value={item.value}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="到期日期" hint="按北京时间当日 23:59 失效">
                  <input
                    type="date"
                    min={dayInShanghai()}
                    value={validDate}
                    onChange={(event) => setValidDate(event.target.value)}
                  />
                </Field>
                <Field label="券名称" hint="展示在客户券包中">
                  <input
                    value={name}
                    maxLength={80}
                    onChange={(event) => setName(event.target.value)}
                  />
                </Field>
                <Field label="发放原因" hint="仅管理员可见，写入审计记录">
                  <input
                    value={reason}
                    maxLength={500}
                    onChange={(event) => setReason(event.target.value)}
                    placeholder="例如：客服补偿、活动补发"
                  />
                </Field>
              </div>
              <label className="growth-check">
                <input
                  type="checkbox"
                  checked={notify}
                  onChange={(event) => setNotify(event.target.checked)}
                />
                发放后发送“优惠券已到账”邮件
              </label>
              <Notice>
                客户将收到一张与系统发放完全一致的优惠券：可与其他优惠择优使用，
                单笔订单限用一张；发放后不可修改面额或有效期。
              </Notice>
              <div className="manual-coupon__actions">
                <Button
                  type="button"
                  variant="secondary"
                  disabled={submit.pending}
                  onClick={() => setStep("lookup")}
                >
                  返回查找
                </Button>
                <Button busy={submit.pending} disabled={disabled}>
                  确认发放
                </Button>
              </div>
            </form>
          </>
        )}

        {step === "done" && result && (
          <div className="manual-coupon__done">
            <Notice type="success">
              已向 {shortEmail(result.accountMasked)}
              {result.displayName ? `（${result.displayName}）` : ""} 发放优惠券。
              {result.replayed ? " 本次为重复请求，返回的是同一张券。" : ""}
            </Notice>
            <dl className="growth-facts manual-coupon__summary">
              <div>
                <dt>券面额</dt>
                <dd>{yuan(result.coupon.amountCents)}</dd>
              </div>
              <div>
                <dt>使用门槛</dt>
                <dd>
                  {result.coupon.minSpendCents
                    ? `满 ${yuan(result.coupon.minSpendCents)}`
                    : "无门槛"}
                </dd>
              </div>
              <div>
                <dt>适用服务</dt>
                <dd>
                  {services.find((item) => item.value === result.coupon.service)
                    ?.label ?? result.coupon.service}
                </dd>
              </div>
              <div>
                <dt>有效期至</dt>
                <dd>{new Date(result.coupon.validUntil ?? "").toLocaleString("zh-CN")}</dd>
              </div>
              <div>
                <dt>到账邮件</dt>
                <dd>{result.notified ? "已发送" : "未发送（不影响发券）"}</dd>
              </div>
            </dl>
            <div className="manual-coupon__actions">
              <Button variant="secondary" onClick={reset}>
                继续发放
              </Button>
              <Button onClick={onClose}>完成</Button>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}

function RecipientCard({
  recipient,
  compact = false,
}: {
  recipient: ManualCouponRecipient;
  compact?: boolean;
}) {
  return (
    <div
      className={`manual-coupon__recipient${compact ? " is-compact" : ""}`}
      data-eligible={recipient.eligible}
    >
      <div className="manual-coupon__person">
        <strong>{recipient.displayName || recipient.emailMasked}</strong>
        <span title={recipient.emailMasked}>
          {shortEmail(recipient.emailMasked)}
        </span>
      </div>
      <dl className="growth-facts manual-coupon__facts">
        <div>
          <dt>账号状态</dt>
          <dd>{statusLabels[recipient.status] ?? recipient.status}</dd>
        </div>
        <div>
          <dt>可用优惠券</dt>
          <dd>{recipient.availableCouponCount} 张</dd>
        </div>
        <div>
          <dt>内部员工</dt>
          <dd>{recipient.isStaff ? "是" : "否"}</dd>
        </div>
      </dl>
      {!recipient.eligible && (
        <Notice type="warning">
          {recipient.ineligibleReason || "该账号暂不符合收券条件"}
        </Notice>
      )}
    </div>
  );
}
