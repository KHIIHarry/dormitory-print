/**
 * Local DTOs and request builders for the V1 admin integration.
 *
 * This module is intentionally free of React and of the transport client so the
 * payload rules can be unit-tested in Node. Components call `growthRequest`
 * themselves and pass the resulting server views into these builders.
 */
export type ConfigSectionName =
  | "prices"
  | "services"
  | "photo"
  | "contact"
  | "announcement"
  | "workflow"
  | "retention";

export type SectionVersionMap = Record<string, { version: number }>;

export type AdminConfig = Record<string, any>;

export type PauseState = {
  newOrders: boolean;
  fileProcessing: boolean;
  production: boolean;
  delivery: boolean;
};

export const RETENTION_POLICY = {
  sourceDays: 7,
  finalDays: 30,
  proofDays: 30,
  draftHours: 24,
  exportHours: 24,
  trashHours: 24,
} as const;

const PRICE_KEYS = [
  "bwSingle",
  "bwDuplex",
  "colorSingle",
  "colorDuplex",
  "photo",
  "corner",
  "side",
] as const;

const SERVICE_KEYS = [
  "serviceEnabled",
  "maxFiles",
  "maxMb",
  "maxOrderMb",
  "maxPages",
  "maxCopies",
  "maxPixels",
  "maxExpandedPages",
  "maxFinalMb",
  "bindingLimits",
  "formats",
  "photoEnabled",
  "photoCalibrated",
] as const;

const CONTACT_KEYS = ["wechat", "phone", "qrUrl", "displayName", "guide"] as const;
const WORKFLOW_KEYS = [
  "unpaidTimeoutMinutes",
  "locationMaxAgeSeconds",
  "locationMaxAccuracyMeters",
  "shortCodeLength",
] as const;

function requiredVersion(version: number): number {
  if (!Number.isSafeInteger(version) || version < 1)
    throw new Error("配置版本无效，请刷新后重试");
  return version;
}

function present(source: Record<string, any>, keys: readonly string[]) {
  const result: Record<string, unknown> = {};
  for (const key of keys) if (source[key] !== undefined) result[key] = source[key];
  return result;
}

function numberOrNull(value: unknown): number | null {
  if (value === null || value === "" || value === undefined) return null;
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < 0)
    throw new Error("价目必须是非负整数分");
  return number;
}

function pauses(source: unknown): PauseState {
  const row = (source || {}) as Partial<PauseState>;
  return {
    newOrders: !!row.newOrders,
    fileProcessing: !!row.fileProcessing,
    production: !!row.production,
    delivery: !!row.delivery,
  };
}

/**
 * Build the exact PATCH body for one config section. Never sends the whole
 * flattened config and never reuses a hard-coded version.
 */
export function configSectionPayload(
  section: ConfigSectionName,
  config: AdminConfig,
  version: number,
): Record<string, unknown> {
  const expectedVersion = requiredVersion(version);
  if (section === "prices") {
    const prices = (config.prices || {}) as Record<string, unknown>;
    return {
      expectedVersion,
      ...Object.fromEntries(PRICE_KEYS.map((key) => [key, numberOrNull(prices[key])])),
    };
  }
  if (section === "services") {
    return {
      expectedVersion,
      ...present(config, SERVICE_KEYS),
    };
  }
  if (section === "photo") {
    return {
      expectedVersion,
      ...present(config, ["photoWidthMm", "photoHeightMm", "margins"]),
    };
  }
  if (section === "contact") {
    return {
      expectedVersion,
      ...present(config, CONTACT_KEYS),
    };
  }
  if (section === "announcement") {
    return {
      expectedVersion,
      ...present(config, ["announcement", "serviceEnabled"]),
    };
  }
  if (section === "workflow") {
    const workflow = present(config, WORKFLOW_KEYS);
    // The current backend PATCH drops explicit nulls for this section; omitting
    // a cleared field keeps the existing server value instead of faking a reset.
    for (const key of WORKFLOW_KEYS) if (workflow[key] === null) delete workflow[key];
    return {
      expectedVersion,
      ...workflow,
      pauses: pauses(config.pauses),
    };
  }
  if (section === "retention") {
    for (const [key, value] of Object.entries(RETENTION_POLICY)) {
      if (config[key] !== undefined && Number(config[key]) !== value)
        throw new Error("保留期限由已批准策略固定，不能在本页修改");
    }
    return { expectedVersion, ...RETENTION_POLICY };
  }
  throw new Error("未知配置分区");
}

export type AfterSaleStatus =
  | "REQUESTED"
  | "REVIEW_PENDING"
  | "REFUND_APPROVED"
  | "REPRINTING"
  | "REPRINT_READY"
  | "REJECTED"
  | "REFUNDED"
  | "COMPLETED"
  | "CANCELLED"
  | string;

export type AfterSaleCase = {
  id: string;
  orderId: string;
  version: number;
  status: AfterSaleStatus;
  source: "CUSTOMER" | "ADMIN" | string;
  responsibility: "CUSTOMER" | "MERCHANT" | string;
  solution: "REFUND" | "REPRINT" | string;
  reason: string;
  suggestedCents: number;
  approvedCents: number | null;
  adjustmentBasis: string;
  data: Record<string, unknown> & {
    affectedItems?: { lineKey: string; quantity: number }[];
    refundRecordedAt?: string;
    refundAmountCents?: number;
    reviewReason?: string;
    reviewedAt?: string;
    completedAt?: string | null;
    reprintTaskIds?: string[];
    customerChoiceAt?: string | null;
  };
  createdAt: string;
  updatedAt?: string;
  orderCode?: string;
  customerLabel?: string;
  allowedActions?: string[];
  refund?: {
    amountCents: number;
    registeredAt?: string;
    registeredBy?: string;
  };
  refundedCents?: number;
  refundRecordedAt?: string | null;
};

export type AfterSaleList = {
  items: AfterSaleCase[];
  nextCursor?: string | null;
};

export type AfterSaleReviewInput = {
  decision: "APPROVE" | "REJECT";
  solution: "REFUND" | "REPRINT";
  approvedCents?: number;
  adjustmentBasis?: string;
  reason: string;
};

export function afterSaleReviewPayload(item: AfterSaleCase, input: AfterSaleReviewInput) {
  const reason = input.reason.trim();
  if (!reason) throw new Error("请填写审核原因");
  if (input.solution !== item.solution)
    throw new Error("处理方式须与客户已确认的选择一致；变更方式须先由客户确认");
  if (input.decision === "REJECT")
    return { expectedVersion: item.version, decision: "REJECT" as const, solution: input.solution, reason };
  if (input.solution === "REPRINT")
    return { expectedVersion: item.version, decision: "APPROVE" as const, solution: "REPRINT" as const, reason };
  const suggested = Math.max(0, Number(item.suggestedCents) || 0);
  const approved = input.approvedCents === undefined ? suggested : Number(input.approvedCents);
  if (!Number.isSafeInteger(approved) || approved < 0) throw new Error("批准金额必须是非负整数分");
  if (item.responsibility === "CUSTOMER" && approved !== suggested)
    throw new Error("客户自身原因退款按已批准规则全额退回实收，不能自定义金额");
  const basis = (input.adjustmentBasis || "").trim();
  if (approved !== suggested && !basis) throw new Error("调整系统建议金额必须填写依据");
  // The backend requires basis whenever approvedCents is present; omit it when
  // the system suggestion is kept so the review uses the stored suggestion.
  return {
    expectedVersion: item.version,
    decision: "APPROVE" as const,
    solution: "REFUND" as const,
    ...(approved === suggested
      ? {}
      : { approvedCents: approved, adjustmentBasis: basis }),
    reason,
  };
}

export type AfterSaleCaseAction = "reprint-ready" | "complete";

/**
 * The backend requires an explicit reason and the current case version for both
 * reprint-ready and complete. It also enforces the real guards (packed order,
 * completed reprint tasks, redelivery proof), so the UI must never pre-set them.
 */
export function afterSaleActionPayload(item: Pick<AfterSaleCase, "version">, reason: string) {
  const cleanReason = reason.trim();
  if (!cleanReason) throw new Error("请填写操作原因");
  if (!Number.isSafeInteger(item.version) || item.version < 1)
    throw new Error("售后单版本无效，请刷新后重试");
  return { expectedVersion: item.version, reason: cleanReason };
}

/** Admin detail route; staff capability is checked by the backend for payment/production managers. */
export function adminAfterSaleDetailPath(caseId: string): string {
  const id = caseId.trim();
  if (!id) throw new Error("售后单 ID 不能为空");
  return `/admin/after-sales/${encodeURIComponent(id)}`;
}

export type SheetView = {
  sheetId: string;
  copyIndex: number;
  sheet: number;
  frontPage: number | null;
  backPage: number | null;
  blankBack: boolean;
};

export type PrintAttemptView = {
  attemptId: string;
  taskId: string;
  artifactId: string;
  outcome: string | null;
  reason: string;
  reasonKind: string | null;
  staffId: string;
  createdAt: string;
  completedAt: string | null;
};

export type ReprintRecordView = {
  attemptId: string;
  mode: string;
  sheetIds: string[];
  reason: string;
  artifactId: string;
  createdAt: string;
  exceptionId: string | null;
};

export type PrintTaskView = {
  id: string;
  orderId: string;
  pickupCode: string;
  fileId: string;
  fileName: string;
  kind: "DOCUMENT" | "PHOTO" | string;
  bindingType: string;
  status: string;
  active: boolean;
  artifactId: string;
  artifactSha256: string;
  pages: number;
  sheetCount: number;
  copies: number;
  sides: string;
  colorMode: string;
  pageRange: string;
  flip: string;
  openAttemptId: string | null;
  lastAttempt: PrintAttemptView | null;
  reprints: ReprintRecordView[];
  orderStatus: string;
  orderVersion: number;
  sheetCatalog: SheetView[];
};

export type PrintQueueView = {
  tasks: PrintTaskView[];
  counts: Record<string, number>;
  nextTaskId: string | null;
};

export type BindingTaskView = {
  id: string;
  orderId: string;
  pickupCode: string;
  fileId: string;
  fileName: string;
  bindingType: string;
  status: string;
  printStatus: string;
  orderStatus: string;
  version: number;
};

export type CheckListItemView = {
  fileId: string;
  name: string;
  kind: string;
  bindingType: string;
  printStatus: string;
  bindingStatus: string;
  taskIds: string[];
  openAttemptIds: string[];
  complete: boolean;
};

export type PackingTaskView = {
  id: string;
  orderId: string;
  pickupCode: string;
  status: string;
  checklistVersion: number;
  orderStatus: string;
  checklist: {
    version: number;
    generatedAt: string;
    packingStatus: string;
    items: CheckListItemView[];
    blockers: { code: string; message: string; fileId?: string | null; taskId?: string | null }[];
  };
  version: number;
};

export type ReproMode = "WHOLE" | "SHEETS";

export function reprintPayload(
  task: Pick<PrintTaskView, "id" | "orderVersion" | "sheetCatalog">,
  mode: ReproMode,
  sheetIds: string[],
  reason: string,
) {
  const cleanReason = reason.trim();
  if (!cleanReason) throw new Error("请填写补打原因");
  const ids = Array.from(new Set(sheetIds.map((id) => id.trim()).filter(Boolean)));
  if (mode === "SHEETS") {
    if (!ids.length) throw new Error("指定纸张补打必须选择实体纸张");
    const catalog = new Set((task.sheetCatalog || []).map((row) => row.sheetId));
    if (catalog.size && ids.some((id) => !catalog.has(id)))
      throw new Error("补打纸张不属于当前打印任务");
  }
  return {
    expectedVersion: task.orderVersion,
    mode,
    sheetIds: mode === "SHEETS" ? ids : [],
    reason: cleanReason,
  };
}

export function packPayload(packing: PackingTaskView, orderVersion: number, confirmedFileIds: string[]) {
  const required = packing.checklist.items.map((item) => item.fileId);
  const sent = new Set(confirmedFileIds);
  if (required.some((fileId) => !sent.has(fileId)))
    throw new Error("请逐项核对当前清单的全部文件");
  if (confirmedFileIds.some((fileId) => !required.includes(fileId)))
    throw new Error("装袋清单包含不属于当前版本的文件");
  return {
    expectedVersion: orderVersion,
    checklistVersion: packing.checklistVersion,
    checks: required.map((fileId) => ({ fileId, confirmed: true })),
  };
}

export type ReceiptStatus = "UNMATCHED" | "MATCHED" | "REFUNDED" | string;

/** One row of GET /admin/receipts?status=UNMATCHED (payment.manage). */
export type ReceiptView = {
  id: string;
  orderId: string;
  amountCents: number;
  status: ReceiptStatus;
  version: number;
  createdAt: string;
  /** Cumulative amount already refunded; optional for old fixtures. */
  refundedCents?: number;
  /** Remaining refundable amount; optional for old fixtures. */
  refundableCents?: number;
};

/**
 * Normalize the cumulative receipt fields.
 *
 * The new backend DTO reports both values. Old fixtures only have the gross
 * amount, so we fall back to gross-minus-refunded and never trust a negative
 * or fractional value.
 */
export function receiptRefundTotals(
  receipt: Pick<
    ReceiptView,
    "amountCents" | "refundedCents" | "refundableCents"
  >,
): { refundedCents: number; refundableCents: number } {
  const gross = Number.isSafeInteger(receipt.amountCents)
    ? Math.max(0, receipt.amountCents)
    : 0;
  const refundedRaw = receipt.refundedCents;
  const refundableRaw = receipt.refundableCents;
  const reportedRefunded =
    Number.isSafeInteger(refundedRaw) && (refundedRaw as number) >= 0
      ? (refundedRaw as number)
      : null;
  const reportedRefundable =
    Number.isSafeInteger(refundableRaw) && (refundableRaw as number) >= 0
      ? (refundableRaw as number)
      : null;
  const refundedCents =
    reportedRefunded ?? (reportedRefundable === null ? 0 : Math.max(0, gross - reportedRefundable));
  const calculated = Math.max(0, gross - refundedCents);
  const refundableCents =
    reportedRefundable === null
      ? calculated
      : Math.max(0, Math.min(reportedRefundable, calculated));
  return { refundedCents, refundableCents };
}

/**
 * Unmatched receipts may only be refunded through this dedicated endpoint.
 * Once the receipt is assigned to an order it must use the order after-sales
 * flow instead ("已分配收款须走订单售后退款"). Both the default UI amount and
 * this guard use the remaining refundable amount, not the gross receipt.
 */
export function receiptRefundPayload(
  receipt: Pick<
    ReceiptView,
    "version" | "status" | "amountCents" | "refundedCents" | "refundableCents"
  >,
  amountCents: number,
  reason: string,
) {
  if (receipt.status !== "UNMATCHED")
    throw new Error("仅未匹配收款可直接退回；已分配收款须走订单售后退款");
  if (!Number.isSafeInteger(amountCents) || amountCents <= 0)
    throw new Error("退款金额必须是正整数分");
  const { refundableCents } = receiptRefundTotals(receipt);
  if (amountCents > refundableCents)
    throw new Error(
      refundableCents > 0
        ? "单次退款不得超过剩余可退金额"
        : "该笔收款已无可退金额",
    );
  const cleanReason = reason.trim();
  if (!cleanReason) throw new Error("请填写退款原因");
  if (!Number.isSafeInteger(receipt.version) || receipt.version < 1)
    throw new Error("收款记录版本无效，请刷新后重试");
  return { expectedVersion: receipt.version, amountCents, reason: cleanReason };
}

export type SlotProposal = {
  slotId: string;
  reason: string;
  confirmed: boolean;
};

export type AdminOrderView = {
  id: string;
  version: number;
  status: string;
  pickupCode?: string | null;
  totalCents?: number;
  slotProposal?: SlotProposal | null;
  slotId?: string | null;
  requestedSlotId?: string | null;
  confirmedSlotId?: string | null;
};

/**
 * Administrators may only propose a slot. The customer has to confirm it with
 * POST /orders/{id}/slot-agreement; no admin code path may set confirmed=true.
 */
export function slotProposalPayload(
  order: Pick<AdminOrderView, "version" | "status">,
  slotId: string,
  reason: string,
) {
  if (order.status !== "PENDING_CONFIRMATION")
    throw new Error("仅待结算订单可申请确认新时段");
  const cleanSlotId = slotId.trim();
  if (!cleanSlotId) throw new Error("请选择要建议的时段");
  const cleanReason = reason.trim();
  if (!cleanReason) throw new Error("请填写时段变更原因");
  if (!Number.isSafeInteger(order.version) || order.version < 1)
    throw new Error("订单版本无效，请刷新订单后重试");
  return { expectedVersion: order.version, slotId: cleanSlotId, reason: cleanReason };
}

/** Actual start can be recorded once; reason is optional in the backend contract. */
export function actualStartPayload(orderVersion: number, reason: string) {
  if (!Number.isSafeInteger(orderVersion) || orderVersion < 1)
    throw new Error("订单版本尚未加载，请刷新订单后重试");
  return { expectedVersion: orderVersion, reason: reason.trim() };
}

export type ExportKind = "ORDERS" | "FINANCE";

export type ExportJobView = {
  id: string;
  kind: ExportKind | string;
  status: string;
  rowCount?: number;
  createdAt?: string;
  expiresAt?: string | null;
  downloadUrl?: string | null;
  error?: string | null;
};

export function exportPayload(
  kind: ExportKind,
  filters: Record<string, unknown>,
  fields: string[],
  reason: string,
) {
  if (kind !== "ORDERS" && kind !== "FINANCE") throw new Error("仅支持订单或收退款导出");
  const cleanReason = reason.trim();
  if (!cleanReason) throw new Error("请填写导出原因");
  const cleanFields = Array.from(new Set(fields.map((field) => field.trim()).filter(Boolean)));
  if (!cleanFields.length) throw new Error("请选择导出字段");
  return { kind, filters, fields: cleanFields, reason: cleanReason };
}

export type StaffCapabilityRow = {
  id: string;
  version: number;
  role?: "ADMIN" | "COURIER" | string;
  name?: string;
};

/**
 * Minimal delivery directory row. Dispatch only needs identity, role and
 * capability/status fields; it must not depend on the private staff store.
 */
export type CourierDirectoryRow = {
  id: string;
  name: string;
  role: "ADMIN" | "COURIER" | string;
  enabled: boolean;
  status?: string | null;
  mustChangePassword?: boolean | null;
  capabilities?: string[] | null;
};

/** Target contract: active ADMIN with delivery.manage (or *) is also a courier; current backend `_valid_courier` still accepts COURIER only. */
export function isEligibleCourier(row: CourierDirectoryRow): boolean {
  if (!row.enabled || row.status !== "ACTIVE" || row.mustChangePassword)
    return false;
  if (row.role === "COURIER") return true;
  if (row.role !== "ADMIN") return false;
  const capabilities = row.capabilities || [];
  return capabilities.includes("*") || capabilities.includes("delivery.manage");
}

export function eligibleCouriers(
  rows: readonly CourierDirectoryRow[],
): CourierDirectoryRow[] {
  return rows.filter(isEligibleCourier);
}

export const CAPABILITY_CATALOG: {
  key: string;
  label: string;
  description: string;
  enforced: boolean;
}[] = [
  { key: "*", label: "完整管理员", description: "包含全部已实现管理能力；仍受最后管理员保护", enforced: true },
  { key: "config.manage", label: "系统配置", description: "价目、服务、照片、联系、营业、工作流与保留策略", enforced: true },
  { key: "staff.manage", label: "员工管理", description: "授予客户账号员工权限、改角色、超级管理员与撤销会话", enforced: true },
  { key: "customer.manage", label: "客户管理", description: "客户检索、冻结、解冻、注销审批与安全邮件", enforced: true },
  { key: "mail.manage", label: "邮件与模板", description: "SMTP 配置、连接测试与七类模板", enforced: true },
  { key: "promotion.manage", label: "营销与满量", description: "活动、券、邀请奖励、记录与满量规则", enforced: true },
  { key: "order.access-link.manage", label: "订单链接", description: "签发、撤销与丢失补发", enforced: true },
  { key: "orders.manage", label: "订单管理", description: "订单查询、笔记、优先级与运营操作", enforced: true },
  { key: "orders.view", label: "订单只读", description: "只读查看订单、看板与详情，不含运营操作", enforced: true },
  { key: "payment.manage", label: "收款与售后", description: "人工收款、时段建议、售后审核与退款登记", enforced: true },
  {
    key: "production.manage",
    label: "生产管理",
    description: "实际开印、打印尝试、补打、装订与装袋",
    enforced: true,
  },
  {
    key: "payroll.manage",
    label: "工资管理",
    description: "全员工资台账、工资策略与人工发放记录",
    enforced: true,
  },
  {
    key: "delivery.manage",
    label: "配送调度",
    description: "配送时段、批次、发车、分配与异常处理",
    enforced: true,
  },
  { key: "export.manage", label: "受控导出", description: "订单、收退款与营销记录导出及下载", enforced: true },
  { key: "data.manage", label: "数据清理", description: "回收站、恢复预览、恢复与彻底清理", enforced: true },
  { key: "audit.view", label: "审计日志", description: "查看后台操作审计记录", enforced: true },
];

export type StaffGrantAccount = {
  id: string;
  verified?: boolean;
  status?: string;
};

/** Minimal grant-target row from GET /admin/staff/grantable-accounts. */
export type StaffGrantableAccount = {
  id: string;
  displayName: string;
  emailMasked: string;
  verified: true;
};

/**
 * Search existing verified base accounts as grant targets.
 *
 * This is deliberately narrower than /admin/customers: one search term, at
 * least three characters, and at most twenty rows. The backend also accepts a
 * complete email address, but the UI always enforces the minimum length so a
 * short fragment never becomes a broad customer directory query.
 */
export function staffGrantableAccountsPath(
  query: string,
  limit = 20,
): string {
  const term = query.trim();
  if (term.length < 3) throw new Error("搜索关键词至少 3 个字符");
  if (!Number.isInteger(limit) || limit < 1 || limit > 20)
    throw new Error("搜索数量必须在 1 到 20 之间");
  return `/admin/staff/grantable-accounts?q=${encodeURIComponent(term)}&limit=${limit}`;
}

export type StaffGrantInput = {
  account: StaffGrantAccount;
  name?: string;
  username?: string;
  phone?: string;
  role: "ADMIN" | "COURIER";
  capabilities?: string[];
  isSuperAdmin?: boolean;
  discountLevel?: number | null;
  reason: string;
};

/**
 * Grant employee rights to an existing verified customer account.
 *
 * The backend identifies the account only by `accountId`. Name, username and
 * phone are display metadata; email and passwords are never accepted here
 * because the customer account owns the credentials.
 */
export type StaffUpdateTarget = {
  id: string;
  version: number;
  role: "ADMIN" | "COURIER";
  capabilities?: string[];
  isSuperAdmin?: boolean;
};

export type StaffUpdateInput = {
  actorId: string;
  target: StaffUpdateTarget;
  name: string;
  username: string;
  phone: string;
  role: "ADMIN" | "COURIER";
  enabled: boolean;
  status: "PENDING_SETUP" | "ACTIVE" | "DISABLED";
  discountLevel?: number | null;
  isSuperAdmin?: boolean;
  reason: string;
};

/**
 * Build a staff PATCH body without letting the backend's "role=ADMIN and no
 * capabilities means *" compatibility branch grant full admin by accident.
 *
 * Self edits deliberately omit role/capabilities: the backend forbids changing
 * them for one's own account, and omitting them also avoids the auto-* branch.
 */
export function staffUpdatePayload(input: StaffUpdateInput) {
  const reason = input.reason.trim();
  if (!reason) throw new Error("请填写变更原因");
  if (!Number.isSafeInteger(input.target.version) || input.target.version < 1)
    throw new Error("员工记录版本无效，请刷新后重试");
  const name = input.name.trim();
  const username = input.username.trim();
  if (!name) throw new Error("请填写姓名");
  if (!username) throw new Error("请填写工作账号");
  const body: Record<string, unknown> = {
    name,
    username,
    phone: input.phone.trim(),
    enabled: input.enabled,
    status: input.enabled ? input.status : "DISABLED",
    reason,
    expectedVersion: input.target.version,
  };
  if (input.actorId === input.target.id) return body;
  const role = input.role;
  const isSuperAdmin = !!input.isSuperAdmin;
  body.role = role;
  body.capabilities =
    role === "COURIER"
      ? []
      : isSuperAdmin
        ? ["*"]
        : input.target.role === "ADMIN"
          ? [...(input.target.capabilities || [])]
          : [];
  body.discountLevel = input.discountLevel ?? null;
  if (
    input.isSuperAdmin !== undefined &&
    input.isSuperAdmin !== !!input.target.isSuperAdmin
  )
    body.isSuperAdmin = input.isSuperAdmin;
  if (role === "COURIER" && input.target.isSuperAdmin)
    body.isSuperAdmin = false;
  return body;
}

export function staffGrantPayload(input: StaffGrantInput) {
  const accountId = input.account.id?.trim();
  if (!accountId) throw new Error("请选择客户账号");
  if (input.account.verified === false || (input.account.status && input.account.status !== "ACTIVE"))
    throw new Error("只能授予已验证且正常使用的客户账号");
  const cleanReason = input.reason.trim();
  if (!cleanReason) throw new Error("请填写授权原因");
  const role = input.role;
  if (role !== "ADMIN" && role !== "COURIER") throw new Error("员工角色无效");
  const isSuperAdmin = !!input.isSuperAdmin;
  if (isSuperAdmin && role !== "ADMIN")
    throw new Error("只有管理员可以成为超级管理员");
  let capabilities = Array.from(
    new Set((input.capabilities || []).map((item) => item.trim()).filter(Boolean)),
  );
  if (role === "COURIER" && capabilities.length)
    throw new Error("配送员不能获得管理能力");
  if (isSuperAdmin) capabilities = ["*"];
  const known = new Set(CAPABILITY_CATALOG.filter((item) => item.enforced).map((item) => item.key));
  const unknown = capabilities.filter((item) => !known.has(item));
  if (unknown.length) throw new Error(`包含后端尚未执行的能力：${unknown.join("、")}`);
  const discountLevel = input.discountLevel;
  if (
    discountLevel !== undefined &&
    discountLevel !== null &&
    (!Number.isInteger(discountLevel) || discountLevel < 1 || discountLevel > 6)
  )
    throw new Error("员工折扣等级只能是 1 到 6");
  const name = input.name?.trim();
  const username = input.username?.trim();
  const phone = (input.phone || "").trim();
  return {
    accountId,
    ...(name ? { name } : {}),
    ...(username ? { username } : {}),
    phone,
    role,
    capabilities,
    isSuperAdmin,
    ...(discountLevel !== undefined && discountLevel !== null
      ? { discountLevel }
      : {}),
    reason: cleanReason,
  };
}

export function staffCapabilityPayload(
  staff: Pick<StaffCapabilityRow, "id" | "version" | "role">,
  capabilities: string[],
  reason: string,
) {
  const cleanReason = reason.trim();
  if (!cleanReason) throw new Error("请填写权限变更原因");
  if (!Number.isSafeInteger(staff.version) || staff.version < 1)
    throw new Error("员工记录缺少版本，请刷新后重试");
  const cleaned = Array.from(new Set(capabilities.map((item) => item.trim()).filter(Boolean)));
  if (staff.role === "COURIER" && cleaned.length)
    throw new Error("配送员不能获得管理能力");
  const known = new Set(CAPABILITY_CATALOG.filter((item) => item.enforced).map((item) => item.key));
  const unknown = cleaned.filter((item) => !known.has(item));
  if (unknown.length) throw new Error(`包含后端尚未执行的能力：${unknown.join("、")}`);
  return { expectedVersion: staff.version, capabilities: cleaned, reason: cleanReason };
}

/** A data-operations tab is never rendered without its own capability. */
export type AdminDataOperationTab = "deletion" | "trash" | "exports";

const DATA_OPERATION_CAPABILITY: Record<AdminDataOperationTab, string> = {
  deletion: "customer.manage",
  trash: "data.manage",
  exports: "export.manage",
};

export function allowedDataOperationTabs(
  capabilities: readonly string[],
): AdminDataOperationTab[] {
  const all = Object.keys(DATA_OPERATION_CAPABILITY) as AdminDataOperationTab[];
  if (capabilities.includes("*")) return all;
  return all.filter((tab) =>
    capabilities.includes(DATA_OPERATION_CAPABILITY[tab]),
  );
}

/** Read `sections` from GET /admin/config, tolerating older demo payloads. */
export function sectionVersions(config: AdminConfig | undefined): SectionVersionMap {
  const raw = config?.sections;
  if (!raw || typeof raw !== "object") return {};
  const result: SectionVersionMap = {};
  for (const [key, value] of Object.entries(raw as Record<string, any>)) {
    const version = Number(value?.version);
    if (Number.isSafeInteger(version) && version >= 1) result[key] = { version };
  }
  return result;
}

/**
 * Merge a PATCH /admin/config/{section} response back into the public config.
 * The backend returns `{version, ...row.data}` flat. Prices live under
 * `config.prices` in GET /admin/config, so a flat merge would leave the price
 * form reading stale values.
 */
export function mergeConfigSection(
  config: AdminConfig,
  section: ConfigSectionName,
  saved: Record<string, unknown>,
): AdminConfig {
  const next = { ...saved };
  delete next.version;
  if (section === "prices")
    return {
      ...config,
      prices: {
        ...((config as any).prices || {}),
        ...(next as unknown as Partial<AdminConfig["prices"]>),
      },
    };
  return { ...config, ...next };
}

/** BatchView keeps orderIds and assignmentIds index-aligned. Never use batch.id as deliveryAssignmentId. */export function assignmentIdForOrder(
  batch: { orderIds?: string[]; assignmentIds?: string[] } | null | undefined,
  orderId: string,
): string | null {
  if (!batch?.orderIds || !batch.assignmentIds) return null;
  const index = batch.orderIds.indexOf(orderId);
  return index >= 0 ? batch.assignmentIds[index] || null : null;
}

export function assetContentPath(assetId: string): string {
  return `/api/v1/assets/${encodeURIComponent(assetId)}/content`;
}

export function printAssetUrl(artifactId: string): string {
  const base = (import.meta.env?.VITE_API_BASE_URL || "/api/v1").replace(/\/$/, "");
  return `${base}/assets/${encodeURIComponent(artifactId)}/content`;
}

export function afterSaleStatusLabel(status: string): string {
  return (
    {
      REQUESTED: "待审核",
      REVIEW_PENDING: "审核中",
      REFUND_APPROVED: "已批准待退款",
      REPRINTING: "重做中",
      REPRINT_READY: "重做待重新交付",
      REJECTED: "已拒绝",
      REFUNDED: "已登记退款",
      COMPLETED: "已完成",
      CANCELLED: "已撤回",
    } as Record<string, string>
  )[status] || status;
}
