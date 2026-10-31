import type { VolumePolicy } from "../../growth/types";
export function parseCents(input: string): number {
  if (!/^\d+(?:\.\d{1,2})?$/.test(input.trim()))
    throw new Error("金额请填写非负数字，最多两位小数");
  const [whole, fraction = ""] = input.trim().split(".");
  const value = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  if (!Number.isSafeInteger(value)) throw new Error("金额超出支持范围");
  return value;
}
export function parseRate(input: string): number {
  if (!/^\d+(?:\.\d{1,3})?$/.test(input.trim()))
    throw new Error("折数请填写 0—10，最多三位小数");
  const [whole, fraction = ""] = input.trim().split(".");
  const value = Number(whole) * 1000 + Number(fraction.padEnd(3, "0"));
  if (!Number.isSafeInteger(value) || value > 10000)
    throw new Error("折数必须在 0—10 之间");
  return value;
}
export type Tier = VolumePolicy["tiers"][number];
export function positiveInteger(value: string, label: string): number {
  if (
    !/^\d+$/.test(value.trim()) ||
    !Number.isSafeInteger(Number(value)) ||
    Number(value) < 1
  )
    throw new Error(`${label}必须为正整数`);
  return Number(value);
}
export function validateTiers(tiers: Tier[]): Tier[] {
  const ordered = [...tiers].sort((a, b) => a.minSheets - b.minSheets);
  const ids = new Set<string>();
  ordered.forEach((tier, index) => {
    if (!Number.isSafeInteger(tier.minSheets) || tier.minSheets < 1)
      throw new Error("张数门槛必须为正整数");
    if (
      !Number.isInteger(tier.payRateBps) ||
      tier.payRateBps < 0 ||
      tier.payRateBps > 10000
    )
      throw new Error("折数必须在 0—10 之间");
    if (!tier.id || ids.has(tier.id)) throw new Error("档位标识不能重复");
    ids.add(tier.id);
    if (index && ordered[index - 1].minSheets === tier.minSheets)
      throw new Error("张数门槛不能重复");
    if (index && ordered[index - 1].payRateBps < tier.payRateBps)
      throw new Error("更高门槛不能使用更差折数");
  });
  return ordered;
}
export function previewVolume(
  tiers: Tier[],
  sheets: number,
  subtotal: number,
  enabled: boolean,
) {
  if (!enabled) return null;
  const candidates = validateTiers(tiers)
    .filter((t) => t.minSheets <= sheets)
    .map((t) => {
      const payableCents = Number(
        (BigInt(subtotal) * BigInt(t.payRateBps) + 5000n) / 10000n,
      );
      return { ...t, payableCents, discountCents: subtotal - payableCents };
    })
    .filter((t) => t.discountCents > 0)
    .sort(
      (a, b) =>
        b.discountCents - a.discountCents ||
        b.minSheets - a.minSheets ||
        a.id.localeCompare(b.id),
    );
  const winner = candidates[0];
  return winner
    ? {
        tierId: winner.id,
        discountCents: winner.discountCents,
        payableCents: winner.payableCents,
      }
    : null;
}
export function safeExportUrl(
  raw: string | undefined,
  origin: string,
  allowDemoBlob = false,
): string | null {
  if (!raw) return null;
  try {
    const url = new URL(raw, origin);
    if (allowDemoBlob && url.protocol === "blob:" && url.origin === origin)
      return url.href;
    return url.origin === origin &&
      /^\/api\/v1\/admin\/promotions\/exports\/[^/]+\/download$/.test(
        url.pathname,
      ) &&
      !url.username &&
      !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}
export function mailStatus(status?: string): string {
  if (status === "QUEUED" || status === "PENDING")
    return "已入队，等待发送；收件箱投递状态未知";
  if (status === "SMTP_ACCEPTED" || status === "ACCEPTED")
    return "SMTP 服务器已接受，不代表已送达收件箱";
  if (status === "FAILED") return "发送失败，请检查配置和错误类别后重试";
  if (status === "CONNECTED" || status === "SUCCESS")
    return "连接与身份认证检查成功；不代表邮件已送达";
  return status ? `测试状态：${status}；收件箱投递状态未知` : "尚无测试结果";
}
export const yuan = (cents: number) => `¥${(cents / 100).toFixed(2)}`;
export function localDate(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString("zh-CN") : "—";
}
export function verificationCountdown(
  deadline: string | null | undefined,
  now = Date.now(),
) {
  if (!deadline) return null;
  const target = Date.parse(deadline);
  if (!Number.isFinite(target)) return null;
  const remaining = target - now;
  if (remaining <= 0) return "已到期，等待系统清理";
  const minutes = Math.floor(remaining / 60000);
  if (minutes < 1) return "不足 1 分钟";
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return hours ? `${hours} 小时 ${rest} 分` : `${rest} 分`;
}
export function dateInput(value: string | null | undefined) {
  if (!value) return "";
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}
export const isoDate = (value: string) =>
  value ? new Date(value).toISOString() : null;

export function buildMailPatch(
  config: import("../../growth/types").MailConfig,
  secret: string,
  reason: string,
  clear = false,
) {
  if (!reason.trim()) throw new Error("请填写配置修改原因");
  if (clear)
    return {
      expectedVersion: config.version,
      clearSecret: true,
      reason: reason.trim(),
    };
  if (config.security === "NONE" && !config.allowInsecure)
    throw new Error("当前部署未批准无加密 SMTP 连接");
  const { version, secretConfigured, lastTest, allowInsecure, ...fields } =
    config;
  void secretConfigured;
  void lastTest;
  void allowInsecure;
  return {
    ...fields,
    expectedVersion: version,
    reason: reason.trim(),
    ...(secret ? { secret } : {}),
  };
}

export function buildTemplatePreview(
  template: Pick<
    import("../../growth/types").MailTemplate,
    "subject" | "textBody" | "htmlBody"
  >,
) {
  return {
    subject: template.subject,
    textBody: template.textBody,
    htmlBody: template.htmlBody,
  };
}
export function buildTestMailBody(to: string) {
  return { to: to.trim() };
}
export function buildVolumePatch(
  policy: VolumePolicy,
  changes: Pick<
    VolumePolicy,
    "enabled" | "effectiveAt" | "description" | "tiers"
  >,
  reason: string,
) {
  if (!reason.trim()) throw new Error("请填写规则修改原因");
  return {
    id: policy.id,
    expectedVersion: policy.version,
    ...changes,
    tiers: validateTiers(changes.tiers),
    reason: reason.trim(),
  };
}
