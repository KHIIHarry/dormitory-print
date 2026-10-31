import {
  grantCampaignCoupons,
  qualifyReferral,
  promotionSummary,
  type PromotionCoupon,
  type PromotionReferral,
} from "./demo-promotions";
import { DEFAULT_TEMPLATE_HTML } from "./mailTemplates";
import type {
  AccessLink,
  Campaign,
  Coupon,
  CustomerAccount,
  CustomerRecord,
  CustomerSession,
  MailConfig,
  MailTemplate,
  OrderAccess,
  PromotionRecord,
  Quote,
  ReferralRecord,
  StaffSession,
  VolumePolicy,
} from "./types";
import type { Order } from "../lib/types";
import { ApiError } from "../lib/api";
import { buildQuote, validateVolumePolicy } from "./pricing";
const date = () => new Date().toISOString();
const uid = () => crypto.randomUUID();
export const defaultVolume: VolumePolicy = {
  id: "volume-policy",
  version: 1,
  enabled: false,
  effectiveAt: null,
  description: "未配置满量档位，暂未启用",
  tiers: [],
};
const account: CustomerAccount = {
  id: "demo-account",
  emailMasked: "s***@example.invalid",
  displayName: "演示同学",
  verified: true,
  status: "ACTIVE",
  version: 1,
  notificationPreferences: {
    orderCreated: false,
    orderStatusUpdated: false,
    referralReward: false,
  },
};
const templateDefinitions: Array<{
  key: string;
  name: string;
  subject: string;
  textBody: string;
  variables: string[];
  critical?: boolean;
}> = [
  {
    key: "CUSTOMER_VERIFY_EMAIL",
    name: "验证邮箱",
    subject: "请验证你的邮箱",
    textBody:
      "你好，{{displayName}}：\n欢迎使用{{brandName}}。请打开以下链接，并点击页面中的确认按钮验证邮箱：\n{{actionUrl}}\n链接在{{expiresMinutes}}分钟内有效。若非本人操作，请忽略。",
    variables: ["brandName", "displayName", "expiresMinutes", "actionUrl"],
    critical: true,
  },
  {
    key: "CUSTOMER_RESET_PASSWORD",
    name: "重置客户密码",
    subject: "重置你的登录密码",
    textBody:
      "你好，{{displayName}}：\n我们收到了你的密码重置申请。请打开{{actionUrl}}，在页面上明确确认并设置新密码。\n链接有效期{{expiresMinutes}}分钟。若非本人操作，请忽略。",
    variables: ["brandName", "displayName", "expiresMinutes", "actionUrl"],
    critical: true,
  },
  {
    key: "ADMIN_ORDER_CREATED",
    name: "新订单通知（管理员）",
    subject: "{{brandName}}｜新订单 {{orderCode}} 待处理",
    textBody:
      "{{brandName}} 收到一笔新订单 {{orderCode}}，金额 {{orderAmount}}。\n客户昵称：{{customerNickname}}\n联系电话：{{customerPhone}}\n请及时核对文件并安排打印，留意添加客户微信以便沟通。\n查看订单：{{orderLink}}",
    variables: [
      "brandName",
      "orderCode",
      "orderAmount",
      "customerNickname",
      "customerPhone",
      "orderLink",
    ],
  },
  {
    key: "COURIER_DISPATCH",
    name: "配送派单通知（配送员）",
    subject:
      "{{brandName}}｜配送任务 {{batchName}}（{{deliveryDate}} {{deliverySlot}}）",
    textBody:
      "{{courierName}}，你好！{{brandName}} 的配送任务已发布。\n批次「{{batchName}}」：{{deliveryDate}} {{deliverySlot}}，共 {{orderCount}} 单。\n请按页面指引领取并配送：{{courierLink}}\n出发前请核对订单数量与包装。",
    variables: [
      "brandName",
      "courierName",
      "batchName",
      "deliveryDate",
      "deliverySlot",
      "orderCount",
      "courierLink",
    ],
  },
  {
    key: "DELIVERY_UPDATED",
    name: "配送信息变更",
    subject: "{{brandName}}｜订单 {{orderCode}} 配送信息已更新",
    textBody:
      "您好！{{brandName}} 订单 {{orderCode}} 的配送信息已更新。\n配送楼栋：{{deliveryLocation}}\n配送时段：{{deliverySlot}}\n查看订单：{{orderLink}}",
    variables: [
      "brandName",
      "orderCode",
      "deliveryLocation",
      "deliverySlot",
      "orderLink",
    ],
  },
  {
    key: "PAYMENT_SUCCESS",
    name: "付款成功",
    subject: "{{brandName}}｜订单 {{orderCode}} 付款成功",
    textBody:
      "您好！{{brandName}} 已确认收到订单 {{orderCode}} 的付款 {{paidAmount}}。\n我们正在安排打印与配送，签收后会再发送确认邮件。\n查看订单：{{orderLink}}",
    variables: ["brandName", "orderCode", "paidAmount", "orderLink"],
  },
  {
    key: "ORDER_DELIVERED",
    name: "签收成功",
    subject: "{{brandName}}｜订单 {{orderCode}} 已签收",
    textBody:
      "您好！{{brandName}} 订单 {{orderCode}} 已于 {{deliveredAt}} 签收。\n配送楼栋：{{deliveryLocation}}\n查看订单：{{orderLink}}\n请及时取回资料并妥善保管。",
    variables: [
      "brandName",
      "orderCode",
      "deliveredAt",
      "deliveryLocation",
      "orderLink",
    ],
  },
  {
    key: "ACCOUNT_DELETED",
    name: "账号注销受理",
    subject: "{{brandName}}｜账号注销申请已受理",
    textBody:
      "你的 {{brandName}} 账号注销申请已受理。\n我们将在 {{deletionDeadline}} 前完成注销，并停止发送后续通知。\n在此之前登录账号可撤销申请。注销完成后订单、优惠券与历史记录将无法恢复。",
    variables: ["brandName", "deletionDeadline"],
  },
  {
    key: "ORDER_CREATED",
    name: "下单回执",
    subject: "订单{{orderCode}}已提交",
    textBody:
      "你好，{{displayName}}：\n你的订单{{orderCode}}已经提交，当前进度：{{orderStatus}}。请按订单页面说明联系管理员，核对线下结算。\n{{brandName}}",
    variables: ["brandName", "displayName", "orderCode", "orderStatus", "orderLink"],
  },
  {
    key: "ORDER_STATUS_UPDATED",
    name: "订单进度",
    subject: "订单{{orderCode}}进度更新",
    textBody:
      "你好，{{displayName}}：\n你的订单{{orderCode}}当前状态为{{orderStatus}}。请进入你保存的订单页面查看最新安排。\n{{brandName}}",
    variables: ["brandName", "displayName", "orderCode", "orderStatus", "orderLink"],
  },
  {
    key: "REFERRAL_REWARD_GRANTED",
    name: "邀请奖励到账",
    subject: "你的邀请奖励已更新",
    textBody:
      "你好，{{displayName}}：\n{{rewardText}}\n请登录{{brandName}}查看奖励详情及使用条件。",
    variables: ["brandName", "displayName", "rewardText"],
  },
];
const variableList = [
  { key: "brandName", description: "品牌名称" },
  { key: "logoUrl", description: "系统品牌 Logo（固定资源）" },
  { key: "displayName", description: "脱敏展示名" },
  { key: "expiresMinutes", description: "安全链接有效分钟数" },
  { key: "actionUrl", description: "一次性操作链接（测试仅非操作样例）" },
  { key: "orderCode", description: "订单短提取码" },
  { key: "orderStatus", description: "订单状态文字" },
  { key: "orderLink", description: "订单详情链接" },
  { key: "orderAmount", description: "订单金额（含币种）" },
  { key: "paidAmount", description: "实付金额（含币种）" },
  { key: "customerNickname", description: "客户昵称" },
  { key: "customerPhone", description: "客户联系电话" },
  { key: "courierName", description: "配送员称呼" },
  { key: "courierLink", description: "配送员任务入口" },
  { key: "batchName", description: "配送批次名称" },
  { key: "deliveryDate", description: "配送日期" },
  { key: "deliverySlot", description: "配送时段" },
  { key: "deliveryLocation", description: "配送楼栋或地点" },
  { key: "deliveredAt", description: "签收时间" },
  { key: "orderCount", description: "本批订单数量" },
  { key: "deletionDeadline", description: "注销完成期限" },
  { key: "rewardText", description: "奖励说明" },
];
const defaults = templateDefinitions.map(
  (definition): MailTemplate => ({
    key: definition.key,
    name: definition.name,
    subject: definition.subject,
    textBody: definition.textBody,
    htmlBody: DEFAULT_TEMPLATE_HTML[definition.key] ?? "",
    version: 1,
    enabled: true,
    critical: definition.critical === true,
    variables: variableList.filter(
      (v) => definition.variables.includes(v.key) || v.key === "logoUrl",
    ),
    updatedBy: "系统默认",
    updatedAt: date(),
  }),
);
const db = {
  customer: {
    role: "CUSTOMER",
    authKind: "ANONYMOUS", recovered: false,
    capabilities: [],
  } as CustomerSession,
  staff: null as StaffSession | null,
  guestPrincipalId: "guest-initial",
  account,
  accounts: [
    {
      ...account,
      createdAt: date(),
      orderCount: 0,
      couponCount: 0,
      inviteCode: "DEMO",
      referralCount: 0,
      allowedActions: [
        "suspend",
        "restore",
        "resend-verification",
        "send-recovery",
      ],
      orders: [],
    },
  ] as CustomerRecord[],
  coupons: [] as PromotionCoupon[],
  campaigns: [] as Campaign[],
  volume: structuredClone(defaultVolume),
  mail: {
    version: 1,
    enabled: false,
    host: "",
    port: 587,
    security: "STARTTLS",
    authEnabled: true,
    username: "",
    secretConfigured: false,
    fromName: "DormPrint",
    fromAddress: "",
    replyTo: "",
    timeoutSeconds: 15,
  } as MailConfig,
  templates: structuredClone(defaults),
  records: [] as PromotionRecord[],
  referrals: [] as PromotionReferral[],
  links: [] as AccessLink[],
  orders: new Map<string, Order>(),
  tokens: new Map<string, { orderId: string; grantId: string }>(),
  access: null as OrderAccess | null,
  preps: new Map<string, Record<string, unknown>>(),
  exports: new Map<
    string,
    { status: string; downloadUrl: string; expiresAt: string }
  >(),
  registrations: new Map<
    string,
    { email: string; token: string; inviteCode?: string }
  >(),
  imports: new Map<
    string,
    {
      id: string;
      accountId: string;
      orderId: string;
      sourceGrantId: string;
      scopes: string[];
      expiresAt: string | null;
    }
  >(),
  qualified: new Set<string>(),
};
export function setDemoStaff(staff: StaffSession | null) {
  db.staff = staff;
}
function validLink(id: string) {
  const link = db.links.find((l) => l.id === id);
  return link &&
    !link.revokedAt &&
    (!link.expiresAt || Date.parse(link.expiresAt) > Date.now())
    ? link
    : null;
}
function couponFor(order: Order) {
  const chosen = order.quote?.discounts[0];
  return chosen?.kind === "COUPON"
    ? db.coupons.find((c) => c.couponGrantId === chosen.couponGrantId)
    : undefined;
}
export function syncDemoOrders(orders: Order[]) {
  for (const projected of orders) {
    const previous = db.orders.get(projected.id);
    const restricted =
      projected.accessKind === "ORDER_GRANT" ||
      projected.accessKind === "IMPORTED_GRANT";
    const order =
      restricted && previous
        ? {
            ...projected,
            files: previous.files,
            proof: previous.proof,
            allowedActions: previous.allowedActions,
          }
        : projected;
    const coupon = couponFor(order);
    if (
      coupon &&
      coupon.reservedOrderId === order.id &&
      coupon.status === "RESERVED"
    ) {
      if (order.status === "CANCELLED") {
        coupon.status =
          coupon.validUntil && Date.parse(coupon.validUntil) <= Date.now()
            ? "EXPIRED"
            : "AVAILABLE";
        delete coupon.reservedOrderId;
        coupon.version++;
      } else if (order.settlement && !previous?.settlement) {
        coupon.status = "USED";
        coupon.realizedDiscountCents = order.quote?.discountCents ?? 0;
        coupon.version++;
        db.records.unshift({
          id: uid(),
          accountId: coupon.accountId,
          campaignId: coupon.campaignId,
          accountMasked: db.account.emailMasked,
          campaignName: coupon.name,
          couponGrantId: coupon.couponGrantId,
          type: "USED",
          status: coupon.status,
          riskHold: coupon.riskHold,
          amountCents: order.quote?.discountCents || 0,
          reason: "人工结算成功",
          time: date(),
          version: coupon.version,
        });
      }
    }
    db.orders.set(order.id, order);
    qualifyReferral(db, order);
  }
}
const readScopes = ["ORDER_VIEW", "FINAL_PDF_PREVIEW", "DELIVERY_PROOF_VIEW"];
function grantedSources(
  orderId: string,
  customer = db.customer,
  access = db.access,
) {
  const sources: AccessLink[] = [];
  if (access?.orderId === orderId) {
    const link = validLink(access.sourceGrantId);
    if (link) sources.push(link);
  }
  if (customer.authKind === "REGISTERED")
    for (const imported of db.imports.values())
      if (
        imported.accountId === customer.account?.id &&
        imported.orderId === orderId
      ) {
        const source = validLink(imported.sourceGrantId);
        if (source)
          sources.push({
            ...source,
            scopes: source.scopes.filter((scope) =>
              imported.scopes.includes(scope),
            ),
          });
      }
  return sources;
}
function grantOrderView(
  order: Order,
  scopes: string[],
  kind: Order["accessKind"],
): Order {
  return {
    ...order,
    accessKind: kind,
    allowedActions: [...(order.allowedActions || []), ...readScopes].filter(
      (action) => scopes.includes(action),
    ),
    proof: scopes.includes("DELIVERY_PROOF_VIEW") ? order.proof : undefined,
    files: (order.files || []).map((f) => ({
      ...f,
      url: "",
      pdfUrl: scopes.includes("FINAL_PDF_PREVIEW") ? f.pdfUrl : undefined,
      finalArtifacts: scopes.includes("FINAL_PDF_PREVIEW")
        ? f.finalArtifacts
        : undefined,
    })),
  };
}
export function visibleDemoOrders(
  view: string,
  customer: CustomerSession,
  access: OrderAccess | null,
) {
  const all = [...db.orders.values()];
  if (view !== "customer") return all;
  return all.flatMap((order) => {
    const owned =
      order.ownerPrincipalId === db.guestPrincipalId ||
      (customer.authKind === "REGISTERED" &&
        !!order.ownerAccountId &&
        order.ownerAccountId === customer.account?.id);
    if (owned)
      return [
        {
          ...order,
          accessKind:
            order.ownerPrincipalId === db.guestPrincipalId
              ? "GUEST_OWNER"
              : "OWNER",
        } as Order,
      ];
    const sources = grantedSources(order.id, customer, access);
    const scopes = [...new Set(sources.flatMap((source) => source.scopes))];
    if (!scopes.includes("ORDER_VIEW")) return [];
    const imported = [...db.imports.values()].some(
      (g) =>
        g.accountId === customer.account?.id &&
        g.orderId === order.id &&
        validLink(g.sourceGrantId),
    );
    return [
      grantOrderView(
        order,
        scopes,
        imported ? "IMPORTED_GRANT" : "ORDER_GRANT",
      ),
    ];
  });
}
export function validateDemoOrderAction(path: string, input: unknown) {
  const body = (input || {}) as Record<string, any>;
  const match = path.match(/^\/admin\/orders\/([^/]+)\/confirm-payment$/);
  if (!match) return;
  const order = db.orders.get(match[1]) || fail("订单不存在", "NOT_FOUND", 404);
  if (
    order.version !== body.expectedVersion ||
    order.contentRevision !== body.contentRevision ||
    order.currentQuoteId !== body.quoteId ||
    order.status !== "PENDING_CONFIRMATION"
  )
    fail("订单报价或状态已变化，请重新核对", "VERSION_CONFLICT", 409);
  if (order.blockers?.length)
    fail("订单有风控阻断，请先人工核对", "ORDER_BLOCKED", 409);
  if (
    !Number.isSafeInteger(body.receivedCents) ||
    body.receivedCents !== order.totalCents ||
    !(
      (body.receivedCents > 0 && body.settlementKind === "CASH_MANUAL") ||
      (body.receivedCents === 0 && body.settlementKind === "ZERO_PAYABLE")
    )
  )
    fail("结算类型或实收金额无效");
  const coupon = couponFor(order);
  if (
    coupon &&
    (coupon.status !== "RESERVED" ||
      coupon.reservedOrderId !== order.id ||
      coupon.riskHold === "FROZEN")
  )
    fail("预约优惠需人工核对", "COUPON_RISK_HOLD", 409);
}
export function getDemoGrowth() {
  return db;
}
const fail = (
  message: string,
  code = "VALIDATION_FAILED",
  status = 422,
): never => {
  throw new ApiError(code, message, status);
};
const requireAccount = () => {
  if (
    db.customer.authKind !== "REGISTERED" ||
    !db.customer.account?.verified ||
    db.customer.account.status !== "ACTIVE"
  )
    fail("请先登录已验证邮箱账号", "AUTH_REQUIRED", 401);
  return db.customer.account!;
};
const mailReady = () => {
  if (!db.mail.enabled)
    fail("邮箱服务暂不可用，仍可使用游客打印", "MAIL_UNAVAILABLE", 503);
};
const queued = () => ({
  message: "开发演示：仅模拟邮件入队，未发送真实邮件",
  status: "QUEUED",
  mailStatus: "QUEUED",
  deliveryStatus: "UNKNOWN",
  queuedAt: date(),
  retryAfterSeconds: 30,
});
const escape = (value: string) =>
  value.replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ]!,
  );
const hash = async (value: string) =>
  Array.from(
    new Uint8Array(
      await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)),
    ),
    (b) => b.toString(16).padStart(2, "0"),
  ).join("");
function ownsOrder(order: Order) {
  return (
    order.ownerPrincipalId === db.guestPrincipalId ||
    (db.customer.authKind === "REGISTERED" &&
      !!order.ownerAccountId &&
      order.ownerAccountId === db.customer.account?.id)
  );
}
function protectDelegatedQuote(original: Order | undefined, quote: Quote) {
  if (!original || ownsOrder(original)) return;
  const reserved = couponFor(original);
  if (
    reserved &&
    !(
      quote.discounts[0]?.kind === "COUPON" &&
      quote.discounts[0].couponGrantId === reserved.couponGrantId
    )
  )
    fail(
      "原预约优惠不再适用，请由订单持有人重新选择并确认；受托修订未激活",
      "OWNER_REQUOTE_REQUIRED",
      409,
    );
}
function requirePrepPrincipal(prep: Record<string, unknown>) {
  if (
    !prep.replacesOrderId &&
    prep.guestPrincipalId !== db.guestPrincipalId &&
    !(
      prep.linkedAccountId &&
      db.customer.authKind === "REGISTERED" &&
      prep.linkedAccountId === db.customer.account?.id
    )
  )
    fail("原游客会话已轮换，请重新准备文件并报价", "AUTH_CONTEXT_CHANGED", 409);
}
const TEMPLATE_TAGS = new Set([
  "p", "br", "strong", "b", "em", "i", "h1", "h2", "h3", "h4",
  "ul", "ol", "li", "blockquote", "pre", "span", "div", "hr", "a",
  "table", "thead", "tbody", "tfoot", "tr", "td", "th", "img",
]);
const TEMPLATE_ATTRIBUTES = new Set([
  "style", "href", "role", "width", "height", "align", "valign", "bgcolor",
  "cellpadding", "cellspacing", "border", "colspan", "rowspan",
  "src", "alt",
]);
const BLOCKED_TEMPLATE_TOKENS = [
  "<script", "<iframe", "<object", "<embed", "<link", "<meta",
  "javascript:", "vbscript:", "data:text/html", "expression(", "url(",
  "@import", "-moz-binding",
];
const BLOCKED_TEMPLATE_STYLES = [
  "url(", "expression(", "@import", "javascript:", "vbscript:",
  "behavior:", "-moz-binding", "position:fixed", "position:absolute",
];
function safeTemplateHtml(content: string) {
  // Mirrors backend/app/mail.py: a bounded marketing-email subset with inline styles.
  const lowered = content.toLowerCase();
  for (const token of BLOCKED_TEMPLATE_TOKENS)
    if (lowered.includes(token))
      fail("HTML 模板包含不允许的标签、属性或远程资源");
  const tagPattern = /<\s*\/?\s*([a-zA-Z][\w-]*)((?:\s[^<>]*?)?)\s*\/?>/g;
  let cursor = 0;
  let tag: RegExpExecArray | null;
  while ((tag = tagPattern.exec(content))) {
    if (content.slice(cursor, tag.index).includes("<"))
      fail("HTML 模板包含不允许的标签、属性或远程资源");
    cursor = tagPattern.lastIndex;
    if (!TEMPLATE_TAGS.has(tag[1].toLowerCase()))
      fail("HTML 模板包含不允许的标签、属性或远程资源");
    const attributes = tag[2] || "";
    const attrPattern = /([a-zA-Z-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?/g;
    let consumed = 0;
    let attr: RegExpExecArray | null;
    while ((attr = attrPattern.exec(attributes))) {
      if (attributes.slice(consumed, attr.index).trim())
        fail("HTML 模板包含不允许的标签、属性或远程资源");
      consumed = attrPattern.lastIndex;
      const name = attr[1].toLowerCase();
      const value = (attr[2] ?? attr[3] ?? attr[4] ?? "").trim();
      if (!TEMPLATE_ATTRIBUTES.has(name) || name.startsWith("on"))
        fail("HTML 模板包含不允许的标签、属性或远程资源");
      if (name === "src" && tag[1].toLowerCase() !== "img")
        fail("HTML 模板只允许品牌 Logo 使用图片地址");
      if (
        tag[1].toLowerCase() === "img" &&
        name === "src" &&
        value !== "{{logoUrl}}"
      )
        fail("HTML 模板的图片只允许使用系统品牌 Logo");
      if (
        name === "href" &&
        !/^(https?:|mailto:)/i.test(value) &&
        !/^\{\{\s*[A-Za-z][A-Za-z0-9_]*\s*\}\}$/.test(value)
      )
        fail("HTML 模板包含不允许的链接协议");
      if (name === "style")
        for (const blocked of BLOCKED_TEMPLATE_STYLES)
          if (value.toLowerCase().includes(blocked))
            fail("HTML 模板包含不允许的样式");
    }
    if (attributes.slice(consumed).trim())
      fail("HTML 模板包含不允许的标签、属性或远程资源");
  }
  if (content.slice(cursor).includes("<"))
    fail("HTML 模板包含不允许的标签、属性或远程资源");
  return content;
}
function filteredRecords(filters: URLSearchParams) {
  return db.records.filter((r) => {
    for (const field of [
      "status",
      "riskHold",
      "type",
      "campaignId",
      "accountId",
    ] as const) {
      const value = filters.get(field);
      if (
        value &&
        String((r as unknown as Record<string, unknown>)[field] ?? "") !== value
      )
        return false;
    }
    const search = (filters.get("q") || "").toLowerCase();
    if (
      search &&
      ![r.id, r.accountMasked, r.campaignName, r.reason]
        .join(" ")
        .toLowerCase()
        .includes(search)
    )
      return false;
    if (
      filters.get("from") &&
      Date.parse(r.time) < Date.parse(filters.get("from")!)
    )
      return false;
    if (
      filters.get("to") &&
      Date.parse(r.time) > Date.parse(filters.get("to")!)
    )
      return false;
    return true;
  });
}
function quoteCoupons(accountId: string | null, original?: Order) {
  const owned =
    original &&
    (original.ownerPrincipalId === db.guestPrincipalId ||
      (original.ownerAccountId === db.customer.account?.id &&
        db.customer.authKind === "REGISTERED"));
  const old = couponFor(original || ({} as Order));
  if (old?.riskHold === "FROZEN")
    fail(
      "原预约优惠已冻结，请管理员先人工核对，不会静默改价",
      "COUPON_RISK_HOLD",
      409,
    );
  return db.coupons.filter(
    (c) =>
      (!c.accountId || c.accountId === accountId) &&
      (!original || owned || c.couponGrantId === old?.couponGrantId),
  );
}
function grantCampaigns() {
  grantCampaignCoupons(db, db.customer.account!.id, "NEW_CUSTOMER");
}
export async function demoRequest(
  rawPath: string,
  options: { method?: string; body?: unknown } = {},
) {
  const [path, queryString = ""] = rawPath.split("?");
  const query = new URLSearchParams(queryString);
  const method = options.method || "GET";
  const body = (options.body || {}) as Record<string, any>;
  const seg = path.split("/").filter(Boolean);
  const write = method !== "GET";
  if (path === "/__demo/staff") {
    db.staff = body as StaffSession;
    return db.staff;
  }
  if (path === "/__demo/customer") {
    db.customer = {
      role: "CUSTOMER",
      authKind: "REGISTERED", recovered: false,
      account: db.account,
      capabilities: ["ORDER_IMPORT", "COUPONS", "REFERRALS", "LINK_GUEST"],
    };
    grantCampaigns();
    return db.customer;
  }
  if (path === "/customer/auth/me") return db.customer;
  if (path === "/auth/me") return db.staff;
  if (path === "/auth/logout") {
    db.staff = null;
    return { success: true };
  }
  if (path === "/customer/auth/logout") {
    db.customer = { role: "CUSTOMER", authKind: "ANONYMOUS", recovered: false, capabilities: [] };
    return { success: true };
  }
  if (path === "/customer/auth/login") {
    if (!String(body.email).includes("@") || !body.password)
      fail("邮箱或密码不正确", "INVALID_CREDENTIALS", 401);
    if (db.account.status === "SUSPENDED")
      fail("账号已停用，请联系管理员", "ACCOUNT_SUSPENDED", 403);
    db.customer = {
      role: "CUSTOMER",
      authKind: "REGISTERED", recovered: false,
      account: db.account,
      capabilities: ["ORDER_IMPORT", "COUPONS", "REFERRALS", "LINK_GUEST"],
    };
    grantCampaigns();
    return db.customer;
  }
  if (path === "/customer/auth/register") {
    mailReady();
    const id = uid();
    db.registrations.set(id, {
      email: body.email,
      token: uid(),
      inviteCode: body.inviteCode,
    });
    return {
      ...queued(),
      registrationChallengeId: id,
      emailMasked: String(body.email).replace(/^(.).*(@.*)$/, "$1***$2"),
    };
  }
  if (path === "/customer/auth/change-pending-email") {
    mailReady();
    const pending = db.registrations.get(body.registrationChallengeId);
    if (!pending)
      fail("需要有效的待验证会话", "PENDING_REGISTRATION_REQUIRED", 403);
    pending!.email = body.newEmail;
    pending!.token = uid();
    return {
      ...queued(),
      registrationChallengeId: body.registrationChallengeId,
      emailMasked: String(body.newEmail).replace(/^(.).*(@.*)$/, "$1***$2"),
    };
  }
  if (path === "/customer/auth/verify-email") {
    const pending = [...db.registrations.entries()].find(
      ([, r]) => r.token === body.token,
    );
    if (!pending) fail("验证链接已失效，请重新申请", "TOKEN_EXPIRED", 410);
    const registration = pending![1];
    const next: CustomerAccount = {
      ...structuredClone(account),
      id: uid(),
      emailMasked: registration.email.replace(/^(.).*(@.*)$/, "$1***$2"),
      displayName: "新同学",
      version: 1,
    };
    db.account = next;
    const record = {
      ...next,
      createdAt: date(),
      orderCount: 0,
      couponCount: 0,
      inviteCode: uid().slice(0, 8).toUpperCase(),
      referralCount: 0,
      allowedActions: [
        "suspend",
        "restore",
        "resend-verification",
        "send-recovery",
      ],
      orders: [],
    };
    db.accounts.push(record);
    const referrer = db.accounts.find(
      (a) => a.inviteCode === registration.inviteCode && a.id !== next.id,
    );
    if (referrer) {
      const now = Date.now();
      const snapshot = db.campaigns.filter(
        (c) =>
          c.kind === "REFERRER" &&
          c.enabled &&
          (!c.startsAt || Date.parse(c.startsAt) <= now) &&
          (!c.endsAt || Date.parse(c.endsAt) > now),
      );
      db.referrals.push({
        id: uid(),
        inviteeAccountId: next.id,
        referrerAccountId: referrer.id,
        inviteeLabel: next.emailMasked,
        status: "WAITING_FIRST_ORDER",
        createdAt: date(),
        rewardText: "等待首笔符合规则的实付订单送达",
        rewardCampaigns: structuredClone(snapshot),
      });
      grantCampaignCoupons(db, next.id, "INVITEE");
    }
    db.registrations.delete(pending![0]);
    db.customer = {
      role: "CUSTOMER",
      authKind: "REGISTERED", recovered: false,
      account: next,
      capabilities: ["ORDER_IMPORT", "COUPONS", "REFERRALS", "LINK_GUEST"],
    };
    grantCampaigns();
    return db.customer;
  }
  if (
    path.startsWith("/customer/auth/") &&
    ["resend-verification", "forgot-password"].includes(seg.at(-1)!)
  ) {
    mailReady();
    return queued();
  }
  if (path === "/customer/auth/reset-password") {
    fail(
      "演示中不生成可操作的密码重置凭据，请申请真实邮件",
      "TOKEN_EXPIRED",
      410,
    );
  }
  if (path === "/customer/account/link-guest") {
    const current = requireAccount();
    if (body.confirm !== true) fail("请明确确认关联");
    for (const order of db.orders.values())
      if (order.ownerPrincipalId === db.guestPrincipalId) {
        order.ownerAccountId = current.id;
        order.ownerPrincipalId = `account-${current.id}`;
      }
    for (const prep of db.preps.values())
      if (
        prep.guestPrincipalId === db.guestPrincipalId &&
        !prep.replacesOrderId
      ) {
        prep.linkedAccountId = current.id;
        prep.checkoutAccountId = current.id;
        prep.version = Number(prep.version) + 1;
        prep.quote = buildQuote(
          prep.lines as Quote["lines"],
          db.volume,
          quoteCoupons(current.id),
          { selectionMode: "AUTO" },
          current.id,
        );
      }
    db.guestPrincipalId = uid();
    return {
      rotationCompleted: true,
      customerPrincipalId: `account-${current.id}`,
    };
  }
  if (path === "/customer/account/import-order") {
    requireAccount();
    if (
      !db.access ||
      db.access.orderId !== body.orderId ||
      !db.access.scopes.includes("ORDER_IMPORT")
    )
      fail("当前单订单授权不包含导入权限", "ORDER_IMPORT_SCOPE_DENIED", 403);
    const source = db.links.find((l) => l.id === db.access!.sourceGrantId);
    if (body.confirm !== true) fail("请明确确认单单导入");
    if (!source || !validLink(source.id))
      fail("来源授权已失效", "ORDER_GRANT_REVOKED", 403);
    const a = requireAccount();
    const key = `${a.id}:${body.orderId}:${source!.id}`;
    const grant = db.imports.get(key) || {
      id: uid(),
      accountId: a.id,
      orderId: body.orderId,
      sourceGrantId: source!.id,
      scopes: [...source!.scopes],
      expiresAt: source!.expiresAt ?? null,
    };
    db.imports.set(key, grant);
    return { accountOrderGrantId: grant.id, ...grant };
  }
  if (path === "/customer/account" && !write) return requireAccount();
  if (path === "/customer/account" && write) {
    const a = requireAccount();
    if (body.expectedVersion !== a.version)
      fail("账号资料已变化，请刷新", "VERSION_CONFLICT", 409);
    if (body.displayName) a.displayName = body.displayName;
    a.version++;
    return a;
  }
  if (path === "/customer/account/notification-preferences") {
    const a = requireAccount();
    Object.assign(
      a.notificationPreferences,
      body.notificationPreferences || body,
    );
    delete (a.notificationPreferences as any).expectedVersion;
    a.version++;
    return a;
  }
  if (path.includes("/customer/account/") && path.endsWith("sessions")) {
    requireAccount();
    return {
      items: [
        {
          id: "current",
          device: "当前浏览器 · 开发演示",
          current: true,
          lastSeenAt: date(),
        },
      ],
    };
  }
  if (path.includes("/customer/account/") && path.endsWith("revoke")) {
    requireAccount();
    return { success: true };
  }
  if (path === "/customer/account/change-password") {
    requireAccount();
    if (!body.currentPassword || String(body.newPassword).length < 8)
      fail("请填写当前密码及至少8位新密码");
    return { success: true };
  }
  if (path === "/customer/account/request-email-change") {
    requireAccount();
    mailReady();
    return queued();
  }
  if (path === "/customer/account/confirm-email-change")
    fail("链接已失效，请重新申请", "TOKEN_EXPIRED", 410);
  if (path === "/customer/coupons") {
    const a = requireAccount();
    return {
      items: db.coupons.filter((c) => !c.accountId || c.accountId === a.id),
    };
  }
  if (path === "/customer/referrals/code") {
    const a = requireAccount();
    const code = db.accounts.find((x) => x.id === a.id)?.inviteCode || "";
    return {
      code,
      shareText: `欢迎体验宿舍打印。注册时可填写邀请码 ${code}，实际权益以已启用活动为准。`,
      rules:
        db.campaigns
          .filter((c) => c.enabled)
          .map((c) => c.description)
          .join("\n") || "当前没有启用邀请奖励活动，不承诺奖励金额。",
    };
  }
  if (path === "/customer/referrals") {
    const a = requireAccount();
    const items = db.referrals.filter((r) => r.referrerAccountId === a.id);
    return {
      items,
      total: items.length,
      qualified: items.filter((r) =>
        ["QUALIFIED", "PENDING_BUDGET", "GRANTED"].includes(r.status),
      ).length,
      granted: items.filter((r) => r.status === "GRANTED").length,
    };
  }
  if (path === "/preparations" && write) {
    const id = body.id || uid();
    const original = body.replacesOrderId
      ? db.orders.get(body.replacesOrderId)
      : undefined;
    if (original && original.status !== "PENDING_CONFIRMATION")
      fail("订单已经结算，不能改版", "ORDER_STATUS_CONFLICT", 409);
    const accountId = original
      ? original.checkoutAccountId || null
      : body.checkoutAccountId || null;
    if (accountId && !original && accountId !== requireAccount().id)
      fail("结算身份不匹配", "AUTH_CONTEXT_CHANGED", 409);
    const quote = buildQuote(
      body.lines,
      db.volume,
      quoteCoupons(accountId, original),
      body.promotionIntent,
      accountId,
      original?.id,
    );
    protectDelegatedQuote(original, quote);
    const prep = {
      ...body,
      checkoutAccountId: accountId,
      guestPrincipalId: db.guestPrincipalId,
      sourceGrantIds:
        original && !ownsOrder(original)
          ? grantedSources(original.id)
              .filter((source) =>
                source.scopes.includes("ORDER_REVISE_PENDING"),
              )
              .map((source) => source.id)
          : undefined,
      id,
      version: 1,
      status: "READY",
      contentRevision: (original?.contentRevision || 0) + 1,
      quote,
    };
    db.preps.set(id, prep);
    return prep;
  }
  if (seg[0] === "preparations") {
    const prep = db.preps.get(seg[1]);
    if (!prep)
      fail("结算准备记录已失效，请重新准备", "PREPARATION_NOT_FOUND", 404);
    requirePrepPrincipal(prep!);
    if (seg[2] === "withdraw-checkout" && prep!.linkedAccountId)
      fail(
        "当前主体已明确关联账号，不能撤回为旧游客身份，请继续核验账号报价",
        "CHECKOUT_OWNER_LINKED",
        409,
      );
    if (["adopt-checkout", "withdraw-checkout"].includes(seg[2])) {
      requireAccount();
      if (prep!.replacesOrderId)
        fail("委托修订不可改变结算主体", "ORDER_SCOPE_DENIED", 403);
      if (body.expectedVersion !== prep!.version)
        fail("准备记录已变化", "VERSION_CONFLICT", 409);
      if (body.confirm !== true) fail("请明确确认仅关联本次结算");
      if (
        seg[2] === "withdraw-checkout" &&
        prep!.checkoutAccountId !== db.customer.account!.id
      )
        fail("结算授权不属于当前账号", "ORDER_SCOPE_DENIED", 403);
      prep!.checkoutAccountId =
        seg[2] === "withdraw-checkout" ? null : db.customer.account!.id;
      prep!.version = Number(prep!.version) + 1;
    }
    if (["requote", "adopt-checkout", "withdraw-checkout"].includes(seg[2])) {
      const old = prep!.quote as Quote;
      prep!.quote = buildQuote(
        prep!.lines as Quote["lines"],
        db.volume,
        quoteCoupons(
          (prep!.checkoutAccountId as string) || null,
          prep!.replacesOrderId
            ? db.orders.get(String(prep!.replacesOrderId))
            : undefined,
        ),
        (seg[2] === "withdraw-checkout"
          ? { selectionMode: "AUTO" as const }
          : body.promotionIntent) || {
          selectionMode: old.selectionMode,
          couponGrantId:
            old.discounts[0]?.kind === "COUPON"
              ? old.discounts[0].couponGrantId
              : undefined,
        },
        (prep!.checkoutAccountId as string) || null,
        prep!.replacesOrderId as string | undefined,
      );
      protectDelegatedQuote(
        prep!.replacesOrderId
          ? db.orders.get(String(prep!.replacesOrderId))
          : undefined,
        prep!.quote as Quote,
      );
      return prep!.quote;
    }
    return prep;
  }
  if (path === "/__demo/reserve") {
    const prep = db.preps.get(body.preparationId);
    if (!prep) fail("准备记录已失效", "PREPARATION_NOT_FOUND", 404);
    requirePrepPrincipal(prep!);
    const quote = prep!.quote as Quote;
    if (
      body.expectedVersion !== undefined &&
      body.expectedVersion !== prep!.version
    )
      fail("准备单已更新", "VERSION_CONFLICT", 409);
    if (body.quoteId !== quote.id || Date.parse(quote.expiresAt) <= Date.now())
      fail("报价已失效，请重新获取", "QUOTE_EXPIRED", 409);
    if (
      quote.checkoutAccountId &&
      !prep!.replacesOrderId &&
      quote.checkoutAccountId !== requireAccount().id
    )
      fail("结算身份已变化", "AUTH_CONTEXT_CHANGED", 409);
    const original = prep!.replacesOrderId
      ? db.orders.get(String(prep!.replacesOrderId))
      : undefined;
    if (
      original &&
      (original.status !== "PENDING_CONFIRMATION" ||
        original.version !== prep!.expectedOrderVersion)
    )
      fail("原订单已变化", "VERSION_CONFLICT", 409);
    if (
      original &&
      !ownsOrder(original) &&
      !(prep!.sourceGrantIds as string[] | undefined)?.some((id) =>
        validLink(id)?.scopes.includes("ORDER_REVISE_PENDING"),
      )
    )
      fail("委托来源已失效或不允许修订", "ORDER_GRANT_REVOKED", 403);
    protectDelegatedQuote(original, quote);
    const chosen = quote.discounts[0];
    let coupon: Coupon | undefined;
    if (chosen?.kind === "COUPON") {
      coupon = db.coupons.find((c) => c.couponGrantId === chosen.couponGrantId);
      if (
        !coupon ||
        coupon.riskHold === "FROZEN" ||
        !(
          (coupon.status === "AVAILABLE" &&
            (!coupon.validUntil ||
              Date.parse(coupon.validUntil) > Date.now())) ||
          (coupon.status === "RESERVED" &&
            coupon.reservedOrderId === body.orderId)
        )
      )
        fail("优惠券已不可用，请重新报价", "COUPON_REQUIRES_REQUOTE", 409);
    }
    const old = original && couponFor(original);
    if (
      old &&
      old.couponGrantId !== coupon?.couponGrantId &&
      old.status === "RESERVED" &&
      old.reservedOrderId === body.orderId
    ) {
      old.status =
        old.validUntil && Date.parse(old.validUntil) <= Date.now()
          ? "EXPIRED"
          : "AVAILABLE";
      delete old.reservedOrderId;
      old.version++;
    }
    if (coupon) {
      coupon.status = "RESERVED";
      coupon.reservedOrderId = body.orderId;
      coupon.version++;
    }
    let accessToken: string | undefined;
    if (!original) {
      accessToken = uid().replaceAll("-", "") + uid().replaceAll("-", "");
      const grant: AccessLink = {
        id: uid(),
        orderId: body.orderId,
        version: 1,
        scopes: [
          ...readScopes,
          "ORDER_REVISE_PENDING",
          "ORDER_CANCEL_PENDING",
          "ORDER_CHANGE_LOCATION_PRE_DELIVERY",
          "ORDER_IMPORT",
        ],
        createdAt: date(),
        createdBy: "下单会话",
        expiresAt: null,
        revokedAt: null,
      };
      db.links.push(grant);
      db.tokens.set(await hash(accessToken), {
        orderId: body.orderId,
        grantId: grant.id,
      });
    }
    return {
      accessToken,
      ownerPrincipalId:
        original?.ownerPrincipalId ||
        (quote.checkoutAccountId
          ? `account-${quote.checkoutAccountId}`
          : db.guestPrincipalId),
    };
  }
  if (path === "/order-access/exchange") {
    const found = db.tokens.get(await hash(body.accessToken || ""));
    const link = found && db.links.find((l) => l.id === found.grantId);
    if (
      !link ||
      link.orderId !== body.orderId ||
      link.revokedAt ||
      (link.expiresAt && Date.parse(link.expiresAt) <= Date.now())
    )
      fail("链接已失效，请联系打印员重新发送", "ORDER_LINK_EXPIRED", 410);
    const order = db.orders.get(body.orderId);
    if (!order) fail("订单不存在", "NOT_FOUND", 404);
    db.access = {
      orderId: body.orderId,
      sourceGrantId: link!.id,
      scopes: link!.scopes,
      allowedActions: order!.allowedActions,
      expiresAt: link!.expiresAt,
    };
    const serialized = ownsOrder(order!)
      ? ({
          ...order!,
          accessKind:
            order!.ownerPrincipalId === db.guestPrincipalId
              ? "GUEST_OWNER"
              : "OWNER",
        } as Order)
      : grantOrderView(order!, link!.scopes, "ORDER_GRANT");
    return { order: serialized, access: db.access };
  }
  if (seg[0] === "admin" && seg[1] === "orders" && seg[3] === "access-links") {
    if (!write) return { items: db.links.filter((l) => l.orderId === seg[2]) };
    if (seg[5] === "revoke") {
      const link = db.links.find((l) => l.id === seg[4]);
      if (link) link.revokedAt = date();
      return { success: true };
    }
    const token = uid().replaceAll("-", "") + uid().replaceAll("-", "");
    const link: AccessLink = {
      id: uid(),
      orderId: seg[2],
      version: 1,
      scopes: [
        "ORDER_VIEW",
        "FINAL_PDF_PREVIEW",
        "DELIVERY_PROOF_VIEW",
        "ORDER_REVISE_PENDING",
        "ORDER_CANCEL_PENDING",
        "ORDER_CHANGE_LOCATION_PRE_DELIVERY",
        "ORDER_IMPORT",
      ],
      createdAt: date(),
      createdBy: "演示管理员",
      expiresAt: body.expiresAt || null,
      revokedAt: null,
    };
    db.links.push(link);
    db.tokens.set(await hash(token), {
      orderId: link.orderId,
      grantId: link.id,
    });
    return { ...link, grant: link, accessToken: token };
  }
  if (path === "/admin/config/volume-discounts") {
    if (!write) return db.volume;
    const errors = validateVolumePolicy(body as VolumePolicy);
    if (errors.length) fail(errors.join("；"));
    if (body.expectedVersion !== db.volume.version)
      fail("规则版本已变化", "VERSION_CONFLICT", 409);
    db.volume = {
      ...body,
      id: db.volume.id,
      version: db.volume.version + 1,
    } as VolumePolicy;
    return db.volume;
  }
  if (path === "/admin/config/mail") {
    if (!write) return db.mail;
    if (body.expectedVersion !== db.mail.version)
      fail("邮件配置已变化", "VERSION_CONFLICT", 409);
    const { secret, password, clearSecret, expectedVersion, ...safe } = body;
    db.mail = {
      ...db.mail,
      ...safe,
      secretConfigured: clearSecret
        ? false
        : secret || password
          ? true
          : db.mail.secretConfigured,
      version: db.mail.version + 1,
    };
    return db.mail;
  }
  if (path === "/admin/mail/test-connection") {
    mailReady();
    return {
      status: "SIMULATED",
      message: "开发演示：仅校验表单结构，未连接真实 SMTP",
      testedAt: date(),
      deliveryStatus: "UNKNOWN",
    };
  }
  if (path === "/admin/mail/test-send") {
    mailReady();
    return queued();
  }
  if (path === "/admin/mail/templates") return { items: db.templates };
  if (seg[0] === "admin" && seg[1] === "mail" && seg[2] === "templates") {
    const item = db.templates.find((t) => t.key === seg[3]);
    if (!item) fail("模板不存在", "NOT_FOUND", 404);
    if (!write) return item;
    if (seg[4] === "restore-default") {
      if (body.expectedVersion !== item!.version)
        fail("模板版本冲突", "VERSION_CONFLICT", 409);
      const original = defaults.find((t) => t.key === item!.key)!;
      Object.assign(item!, structuredClone(original), {
        version: item!.version + 1,
        updatedAt: date(),
      });
      return item;
    }
    if (seg[4] === "test-send") {
      mailReady();
      return queued();
    }
    if (seg[4] === "preview") {
      const subject = body.subject ?? item!.subject;
      const text = body.textBody ?? item!.textBody;
      const sample = {
        brandName: "DormPrint",
        logoUrl: "/assets/icon-192.png",
        displayName: "示例同学",
        expiresMinutes: "示例有效期",
        actionUrl: "[不可操作的安全样例链接]",
        orderCode: "DEMO",
        orderStatus: "待人工核对",
        orderLink: "[不可操作的订单样例链接]",
        orderAmount: "¥36.00",
        paidAmount: "¥36.00",
        customerNickname: "示例同学",
        customerPhone: "138****0000",
        courierName: "示例配送员",
        courierLink: "[不可操作的配送样例链接]",
        batchName: "10月31日 晚班",
        deliveryDate: "2026-10-31",
        deliverySlot: "19:00–20:00",
        deliveryLocation: "示例公寓 3 栋",
        deliveredAt: "2026-10-31 20:12",
        orderCount: "6",
        deletionDeadline: "2026-11-14",
        rewardText: "奖励以真实活动规则为准",
      };
      const render = (value: string) =>
        value.replace(/\{\{\s*(\w+)\s*\}\}/g, (_, key) => {
          if (!(key in sample) || !item!.variables.some((v) => v.key === key))
            fail("模板存在未授权变量");
          return sample[key as keyof typeof sample];
        });
      return {
        subject: render(subject),
        textBody: render(text),
        htmlBody:
          body.htmlBody || item!.htmlBody
            ? render(safeTemplateHtml(body.htmlBody ?? item!.htmlBody))
            : `<pre>${escape(render(text))}</pre>`,
      };
    }
    if (body.expectedVersion !== item!.version)
      fail("模板版本冲突", "VERSION_CONFLICT", 409);
    if (item!.critical && body.enabled === false) fail("关键安全邮件不能关闭");
    for (const content of [body.subject, body.textBody, body.htmlBody])
      for (const match of String(content || "").matchAll(
        /\{\{\s*(\w+)\s*\}\}/g,
      ))
        if (!item!.variables.some((v) => v.key === match[1]))
          fail("模板存在未授权变量");
    const content = safeTemplateHtml(String(body.htmlBody || ""));
    if (/<script|on\w+\s*=|javascript:|<iframe/i.test(content))
      fail("HTML 模板包含不允许的脚本或远程资源");
    Object.assign(item!, body, {
      version: item!.version + 1,
      updatedBy: "演示管理员",
      updatedAt: date(),
    });
    return item;
  }
  if (path === "/admin/promotions/campaigns") {
    if (!write) return { items: db.campaigns };
    const item = {
      ...body,
      id: uid(),
      version: 1,
      stackable: false,
    } as Campaign;
    db.campaigns.push(item);
    return item;
  }
  if (
    seg[0] === "admin" &&
    seg[1] === "promotions" &&
    seg[2] === "campaigns" &&
    write
  ) {
    const c = db.campaigns.find((c) => c.id === seg[3]);
    if (!c) fail("活动不存在", "NOT_FOUND", 404);
    if (body.expectedVersion !== c!.version)
      fail("活动版本冲突", "VERSION_CONFLICT", 409);
    Object.assign(c!, body, { version: c!.version + 1, stackable: false });
    return c;
  }
  if (path === "/admin/promotions/records")
    return { items: filteredRecords(query), summary: promotionSummary(db) };
  if (
    seg[0] === "admin" &&
    seg[1] === "promotions" &&
    seg[2] === "coupons" &&
    write
  ) {
    const c = db.coupons.find(
      (c) => c.couponGrantId === seg[3] || c.id === seg[3],
    );
    if (!c) fail("优惠券不存在", "NOT_FOUND", 404);
    if (!body.reason?.trim()) fail("请填写处理原因");
    if (body.expectedVersion !== c!.version)
      fail("券状态已变化", "VERSION_CONFLICT", 409);
    if (seg[4] === "freeze") c!.riskHold = "FROZEN";
    if (seg[4] === "unfreeze") c!.riskHold = "NONE";
    if (seg[4] === "revoke") {
      if (c!.status === "AVAILABLE") c!.status = "REVOKED";
      else if (c!.status === "RESERVED" || c!.status === "USED")
        c!.riskHold = "FROZEN";
    }
    c!.version++;
    if (c!.status === "RESERVED" && c!.reservedOrderId) {
      const order = db.orders.get(c!.reservedOrderId);
      if (order)
        db.orders.set(order.id, {
          ...order,
          blockers: ["freeze", "revoke"].includes(seg[4])
            ? [
                ...(order.blockers || []).filter(
                  (b) => b.code !== "COUPON_FROZEN",
                ),
                {
                  code:
                    seg[4] === "revoke" ? "COUPON_REVOKED" : "COUPON_FROZEN",
                  message:
                    seg[4] === "revoke"
                      ? "预约优惠已申请撤销，保留锁价，等待人工核对"
                      : "预约优惠已风控冻结，请管理员人工核对",
                },
              ]
            : seg[4] === "unfreeze"
              ? (order.blockers || []).filter((b) => b.code !== "COUPON_FROZEN")
              : order.blockers,
        });
    }
    db.records.unshift({
      id: uid(),
      accountId: c!.accountId,
      campaignId: c!.campaignId,
      accountMasked:
        db.accounts.find((a) => a.id === c!.accountId)?.emailMasked ||
        account.emailMasked,
      campaignName: c!.name,
      couponGrantId: c!.couponGrantId,
      type: seg[4] === "revoke" ? "REVOKED" : seg[4].toUpperCase(),
      status: c!.status,
      riskHold: c!.riskHold,
      amountCents: c!.amountCents,
      reason: body.reason,
      time: date(),
      version: c!.version,
    });
    return c;
  }
  if (path === "/admin/promotions/records/export") {
    const id = uid();
    const permitted = [
      "id",
      "accountMasked",
      "campaignName",
      "type",
      "status",
      "riskHold",
      "amountCents",
      "reason",
      "time",
    ];
    const fields: string[] = (body.fields || permitted).filter((f: string) =>
      permitted.includes(f),
    );
    if (!fields.length) fail("请选择有效的导出字段");
    const cell = (value: unknown) =>
      '"' +
      String(value ?? "")
        .replace(/^[=+@-]/, "'$&")
        .replaceAll('"', '""') +
      '"';
    const rows = filteredRecords(new URLSearchParams(body.filters || {}));
    const csv =
      fields.join(",") +
      "\n" +
      rows
        .map((r) =>
          fields
            .map((f) => cell((r as unknown as Record<string, unknown>)[f]))
            .join(","),
        )
        .join("\n");
    const downloadUrl = URL.createObjectURL(
      new Blob(["\ufeff" + csv], { type: "text/csv" }),
    );
    db.exports.set(id, {
      status: "READY",
      downloadUrl,
      expiresAt: new Date(Date.now() + 600000).toISOString(),
    });
    return { exportJobId: id };
  }
  if (seg[2] === "exports")
    return db.exports.get(seg[3]) || fail("导出已过期", "NOT_FOUND", 404);
  if (path === "/admin/customers") {
    const items = db.accounts
      .map((a) => ({
        ...a,
        orderCount: [...db.orders.values()].filter(
          (o) => o.ownerAccountId === a.id,
        ).length,
        couponCount: db.coupons.filter((c) => c.accountId === a.id).length,
        referralCount: db.referrals.filter((r) => r.referrerAccountId === a.id)
          .length,
      }))
      .filter(
        (a) =>
          (!query.get("status") || a.status === query.get("status")) &&
          (!query.get("q") ||
            [a.id, a.displayName, a.emailMasked, a.inviteCode]
              .join(" ")
              .toLowerCase()
              .includes(query.get("q")!.toLowerCase())) &&
          (!query.get("verified") ||
            String(a.verified) === query.get("verified")) &&
          (!query.get("inviteCode") ||
            a.inviteCode === query.get("inviteCode")) &&
          (!query.get("createdFrom") ||
            Date.parse(a.createdAt) >= Date.parse(query.get("createdFrom")!)) &&
          (!query.get("createdTo") ||
            Date.parse(a.createdAt) <
              Date.parse(query.get("createdTo")!) + 86400000) &&
          (!query.get("minOrders") ||
            a.orderCount >= Number(query.get("minOrders"))) &&
          (!query.get("minCoupons") ||
            a.couponCount >= Number(query.get("minCoupons"))),
      );
    return { items };
  }
  if (seg[0] === "admin" && seg[1] === "customers") {
    const a = db.accounts.find((a) => a.id === seg[2]);
    if (!a) fail("客户不存在", "NOT_FOUND", 404);
    if (!write)
      return {
        ...a,
        orders: [...db.orders.values()]
          .filter(
            (o) => o.ownerAccountId === a!.id || o.checkoutAccountId === a!.id,
          )
          .map((o) => ({
            id: o.id,
            pickupCode: o.pickupCode,
            totalCents: o.totalCents,
            status: o.status,
          })),
      };
    if (!body.reason?.trim()) fail("请填写操作原因");
    if (seg[3] === "suspend") a!.status = "SUSPENDED";
    if (seg[3] === "restore") a!.status = "ACTIVE";
    if (["resend-verification", "send-recovery"].includes(seg[3])) {
      mailReady();
      return queued();
    }
    a!.version++;
    if (db.customer.account?.id === a!.id)
      Object.assign(db.customer.account, a);
    return a;
  }
  if (path === "/auth/sessions")
    return {
      items: [
        {
          id: "current",
          device: "当前浏览器",
          current: true,
          lastSeenAt: date(),
        },
      ],
    };
  if (path === "/auth/change-password") {
    if (String(body.newPassword).length < 8 || !body.currentPassword)
      fail("请输入当前密码及至少8位新密码");
    if (db.staff) db.staff.mustChangePassword = false;
    return { success: true };
  }
  if (path === "/auth/sessions/revoke") return { success: true };
  if (
    path === "/auth/email/request-verification" ||
    path === "/auth/forgot-password"
  ) {
    mailReady();
    return queued();
  }
  if (
    [
      "/auth/setup-password",
      "/auth/reset-password",
      "/auth/email/verify",
    ].includes(path)
  )
    fail("链接已失效，请重新申请", "TOKEN_EXPIRED", 410);
  if (seg[0] === "admin" && seg[1] === "staff" && write) {
    if (seg[3] === "issue-temporary-password")
      return {
        temporaryPassword: "Demo-" + uid().slice(0, 12),
        mustChangePassword: true,
      };
    if (seg[3] === "send-setup") {
      mailReady();
      return queued();
    }
    return { success: true };
  }
  fail("此能力尚待真实后端联调", "SERVICE_UNAVAILABLE", 503);
}
