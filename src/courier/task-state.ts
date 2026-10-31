import type { OrderStatus, Position, Proof, Role } from "../lib/types";

/** A device operation owns exactly one completion; cancelling invalidates late callbacks. */
export class DeviceTask {
  private sequence = 0;
  private active: number | undefined;
  start() {
    if (this.active !== undefined) return undefined;
    return (this.active = ++this.sequence);
  }
  current(token: number) {
    return this.active === token;
  }
  finish(token: number) {
    if (!this.current(token)) return false;
    this.active = undefined;
    return true;
  }
  cancel() {
    this.active = undefined;
    ++this.sequence;
  }
}
export function photoTransfer(loaded: number, total?: number) {
  const value =
    total && total > 0
      ? Math.min(100, Math.round((loaded / total) * 100))
      : undefined;
  return {
    value,
    label: value === 100 ? "照片已传输，等待服务端确认…" : "正在上传照片…",
  };
}
export function exportReady(status: string, url?: string | null) {
  return !!url && ["COMPLETED", "READY"].includes(status);
}

/** React uses this key to discard every private input and pending state on scope change. */
export function proofScope(
  orderId: string | undefined,
  generation: number,
  role: string | null,
) {
  return JSON.stringify([orderId, generation, role]);
}

/**
 * Fallback for the server `workflow` defaults. `GET /public/config` publishes
 * the live limits when available; the client passes them through and the
 * server remains authoritative.
 */
export const LOCATION_MAX_AGE_SECONDS = 300;
export const LOCATION_MAX_ACCURACY_METERS = 100;

export type LocationLimits = {
  maxAgeSeconds: number;
  maxAccuracyMeters: number;
};

/** Reads live public limits, falling back to the documented server defaults. */
export function locationLimits(config: unknown): LocationLimits {
  const row = (config || {}) as {
    locationMaxAgeSeconds?: unknown;
    locationMaxAccuracyMeters?: unknown;
  };
  const maxAge = Number(row.locationMaxAgeSeconds);
  const maxAccuracy = Number(row.locationMaxAccuracyMeters);
  return {
    maxAgeSeconds:
      Number.isFinite(maxAge) && maxAge > 0 ? maxAge : LOCATION_MAX_AGE_SECONDS,
    maxAccuracyMeters:
      Number.isFinite(maxAccuracy) && maxAccuracy > 0
        ? maxAccuracy
        : LOCATION_MAX_ACCURACY_METERS,
  };
}

export const LOCATION_FAILURE_CODES = [
  ["PERMISSION_DENIED", "定位权限被拒绝"],
  ["POSITION_UNAVAILABLE", "设备无法提供位置"],
  ["TIMEOUT", "定位超时"],
  ["UNSUPPORTED", "设备不支持定位"],
  ["DEVICE_UNAVAILABLE", "定位设备不可用"],
] as const;
export type LocationFailureCode = (typeof LOCATION_FAILURE_CODES)[number][0];
const FAILURE_CODE_SET = new Set<string>(
  LOCATION_FAILURE_CODES.map(([code]) => code),
);

/** Fields returned by the real courier delivery DTOs (not yet on shared `Order`). */
export type DeliveryAssignmentView = {
  id: string;
  orderId?: string;
  batchId?: string;
  status: string;
  version: number;
};
/** Server-approved location exception bound to one assignment + version. */
export type LocationExceptionApproval = {
  id: string;
  status: "APPROVED";
  assignmentId: string;
  assignmentVersion: number;
  reason?: string | null;
  failureCode?: string | null;
  approvedAt?: string | null;
};
export type DeliveryOrderView = {
  id: string;
  version: number;
  status: string;
  pickupCode?: string;
  nickname?: string;
  phone?: string;
  locationId?: string | null;
  locationName?: string | null;
  batchId?: string | null;
  deliveryAssignmentId?: string | null;
  deliveryStatus?: string | null;
  assignment?: DeliveryAssignmentView | null;
  /** Present only when the server approved the current assignment/version. */
  locationExceptionApproval?: LocationExceptionApproval | null;
  blockers?: { code: string; message: string }[];
  allowedActions?: string[];
};
export type DeliveryProofView = {
  id?: string;
  orderId?: string;
  assignmentId: string;
  photoAssetId?: string | null;
  skipPhotoReason?: string | null;
  position?: {
    latitude: number;
    longitude: number;
    accuracyMeters: number;
    capturedAt: string;
    coordinateSystem?: string;
  } | null;
  skipLocationReason?: string | null;
  locationExceptionId?: string | null;
  placementNote?: string | null;
  deliveredAt: string;
  operatorId?: string;
};
export type SingleProofResponse = {
  order: DeliveryOrderView;
  proof: DeliveryProofView;
};
/** Exact body documented for the batch-proof endpoint. */
export type BatchProofItem = {
  orderId: string;
  deliveryAssignmentId: string;
  expectedVersion: number;
  /** Required: guards the exact assignment version, not just the order. */
  expectedAssignmentVersion: number;
  position?: Position;
  locationFailureCode?: string;
  skipLocationReason?: string;
};
export type BatchProofPayload = {
  items: BatchProofItem[];
  photoAssetId: string | null;
  skipPhotoReason: string | null;
  placementNote: string;
};
export type CourierBatchView = {
  id: string;
  name: string;
  status: string;
  version: number;
  courierId?: string | null;
  orderIds: string[];
};

/** Never fall back to a batch id: the server rejects that as an assignment id. */
export function assignmentIdFor(
  order: Pick<DeliveryOrderView, "deliveryAssignmentId" | "assignment">,
): string {
  return (order.deliveryAssignmentId || order.assignment?.id || "").trim();
}

/**
 * Only a server-approved exception for the exact current assignment + version
 * counts. An old approval after reassignment/version change is ignored.
 */
export function approvedLocationException(
  order: Pick<
    DeliveryOrderView,
    "deliveryAssignmentId" | "assignment" | "locationExceptionApproval"
  >,
): LocationExceptionApproval | null {
  const approval = order.locationExceptionApproval;
  if (!approval || approval.status !== "APPROVED") return null;
  const assignmentId = assignmentIdFor(order);
  const assignmentVersion = order.assignment?.version;
  if (!assignmentId || approval.assignmentId !== assignmentId) return null;
  if (
    typeof assignmentVersion !== "number" ||
    approval.assignmentVersion !== assignmentVersion
  )
    return null;
  return approval;
}

/** ADMIN needs the explicit scheduling capability; role alone never bypasses it. */
export function canAdminDeliver(
  role: Role | null,
  capabilities?: readonly string[] | null,
) {
  return (
    role === "ADMIN" &&
    !!capabilities?.some(
      (capability) => capability === "*" || capability === "delivery.manage",
    )
  );
}

/** Client freshness mirror; the server validates again from `captured_at`. */
export function locationErrors(
  position: Position,
  nowMs = Date.now(),
  maxAgeSeconds = LOCATION_MAX_AGE_SECONDS,
  maxAccuracyMeters = LOCATION_MAX_ACCURACY_METERS,
) {
  const errors: string[] = [];
  if (!Number.isFinite(position.accuracyMeters) || position.accuracyMeters <= 0)
    errors.push("定位精度无效，请重新获取");
  else if (position.accuracyMeters > maxAccuracyMeters)
    errors.push(
      `定位精度 ±${Math.round(position.accuracyMeters)} m，超过 ${maxAccuracyMeters} m，请到开阔处重新获取`,
    );
  const captured = new Date(position.capturedAt).getTime();
  if (!Number.isFinite(captured)) errors.push("定位时间无效，请重新获取");
  else {
    const ageSeconds = (nowMs - captured) / 1000;
    if (ageSeconds < -60) errors.push("定位时间异常，请重新获取");
    else if (ageSeconds > maxAgeSeconds)
      errors.push(`定位已超过 ${maxAgeSeconds} 秒，请在放置资料的位置重新获取`);
  }
  return errors;
}

/** Why an order may not join a batch proof in this building. */
export function batchOrderBlockers(
  order: DeliveryOrderView,
  batchId: string,
  buildingId: string,
) {
  const blockers: string[] = [];
  if (order.batchId && order.batchId !== batchId)
    blockers.push("订单不属于当前批次，请刷新");
  if (order.status !== "DELIVERING") blockers.push("订单尚未开始配送");
  if (!order.locationId || order.locationId !== buildingId)
    blockers.push("订单不在当前楼栋");
  const assignmentId = assignmentIdFor(order);
  if (!assignmentId) blockers.push("缺少真实配送执行ID，请刷新");
  else if (assignmentId === batchId)
    blockers.push("不能使用批次ID作为配送执行ID");
  if (!Number.isSafeInteger(order.assignment?.version) || (order.assignment?.version ?? 0) < 1)
    blockers.push("缺少有效配送执行版本，请刷新");
  if (order.assignment?.status && order.assignment.status !== "STARTED")
    blockers.push("配送执行尚未开始");
  for (const blocker of order.blockers || [])
    if (blocker?.message) blockers.push(blocker.message);
  return Array.from(new Set(blockers));
}

function text(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

export type BatchProofInput = {
  batchId: string;
  buildingId: string;
  orders: DeliveryOrderView[];
  selectedIds: string[];
  photoAssetId?: string | null;
  skipPhotoReason?: string;
  placementNote?: string;
  position?: Position | null;
  /** Live public limits; defaults mirror the server workflow defaults. */
  maxAgeSeconds?: number;
  maxAccuracyMeters?: number;
  /** Admin-only per-order skip: reason + valid failure code. */
  adminSkip?: { reason: string; code: string } | null;
  nowMs?: number;
};

/**
 * Builds the single atomic batch request. It never loops per-order proof calls
 * and never substitutes a batch id for an assignment id.
 */
export function buildBatchProofPayload(input: BatchProofInput): {
  payload: BatchProofPayload | null;
  errors: string[];
} {
  const errors: string[] = [];
  const selected = input.orders.filter((order) =>
    input.selectedIds.includes(order.id),
  );
  const requested = new Set(input.selectedIds);
  if (!selected.length) errors.push("请至少选择一个待交付订单");
  if (selected.length !== requested.size)
    errors.push("部分订单已不可选，请刷新");

  const photoId = text(input.photoAssetId);
  const photoReason = text(input.skipPhotoReason);
  const placementNote = text(input.placementNote);
  if (photoId && photoReason) errors.push("交付照片与免拍照原因不能同时提交");
  else if (!photoId && !photoReason)
    errors.push("请上传一张共享交付照片，或填写明确的免拍照原因");
  if (photoReason.length > 1000) errors.push("免拍照原因不能超过 1000 字");
  if (placementNote.length > 500) errors.push("摆放说明不能超过 500 字");

  const maxAgeSeconds =
    Number.isFinite(input.maxAgeSeconds) && (input.maxAgeSeconds || 0) > 0
      ? (input.maxAgeSeconds as number)
      : LOCATION_MAX_AGE_SECONDS;
  const maxAccuracyMeters =
    Number.isFinite(input.maxAccuracyMeters) &&
    (input.maxAccuracyMeters || 0) > 0
      ? (input.maxAccuracyMeters as number)
      : LOCATION_MAX_ACCURACY_METERS;
  const assignmentIds = new Set<string>();
  const items: BatchProofItem[] = [];
  for (const order of selected) {
    const label = order.pickupCode || order.id;
    const assignmentId = assignmentIdFor(order);
    if (!assignmentId) {
      errors.push(`${label} 缺少真实配送执行ID，请刷新`);
      continue;
    }
    if (assignmentId === input.batchId) {
      errors.push(`${label} 不能使用批次ID作为配送执行ID`);
      continue;
    }
    if (assignmentIds.has(assignmentId)) {
      errors.push(`${label} 与其他订单使用了同一个配送执行ID，请刷新`);
      continue;
    }
    assignmentIds.add(assignmentId);
    const assignmentVersion = order.assignment?.version;
    if (typeof assignmentVersion !== "number" || !Number.isSafeInteger(assignmentVersion) || assignmentVersion < 1) {
      errors.push(`${label} 缺少有效配送执行版本，请刷新`);
      continue;
    }
    const item: BatchProofItem = {
      orderId: order.id,
      deliveryAssignmentId: assignmentId,
      expectedVersion: order.version,
      expectedAssignmentVersion: assignmentVersion,
    };
    if (input.position) {
      const positionIssues = locationErrors(
        input.position,
        input.nowMs,
        maxAgeSeconds,
        maxAccuracyMeters,
      );
      for (const issue of positionIssues) errors.push(`${label}：${issue}`);
      item.position = input.position;
    } else if (approvedLocationException(order)) {
      // Server-approved for this exact assignment + version; submit no
      // position and no self-declared skip reason.
    } else if (input.adminSkip) {
      const reason = text(input.adminSkip.reason);
      const code = text(input.adminSkip.code);
      if (!reason) errors.push(`${label}：管理员跳过定位必须填写原因`);
      if (!FAILURE_CODE_SET.has(code))
        errors.push(`${label}：管理员跳过定位必须选择有效的失败原因`);
      item.skipLocationReason = reason;
      item.locationFailureCode = code;
    } else {
      errors.push(
        `${label} 需要真实定位，或等待管理员批准当前配送执行的定位例外`,
      );
    }
    items.push(item);
  }

  return {
    payload: errors.length
      ? null
      : {
          items,
          photoAssetId: photoId || null,
          skipPhotoReason: photoReason || null,
          placementNote,
        },
    errors: Array.from(new Set(errors)),
  };
}

/** Client mirror of the server shape rules, including approved exceptions. */
export function singleProofErrors(
  role: Role,
  input: {
    photo: boolean;
    skipPhotoReason: string;
    position: Position | null;
    skipLocationReason: string;
    locationFailureCode?: string;
    /** True only when the account has the server `delivery.manage` capability. */
    canSkipLocation?: boolean;
    /** Actual server approval; never a user claim. */
    approvedLocation?: LocationExceptionApproval | null;
    assignmentId?: string;
    assignmentVersion?: number;
    nowMs?: number;
    maxAgeSeconds?: number;
    maxAccuracyMeters?: number;
  },
) {
  const errors: string[] = [];
  const photoReason = input.skipPhotoReason.trim();
  const locationReason = input.skipLocationReason.trim();
  const approval = input.approvedLocation;
  const approved =
    !!approval &&
    approval.status === "APPROVED" &&
    (!input.assignmentId || approval.assignmentId === input.assignmentId) &&
    (input.assignmentVersion === undefined ||
      approval.assignmentVersion === input.assignmentVersion);
  const adminSkip = role === "ADMIN" && !!input.canSkipLocation;
  const maxAgeSeconds =
    Number.isFinite(input.maxAgeSeconds) && (input.maxAgeSeconds || 0) > 0
      ? (input.maxAgeSeconds as number)
      : LOCATION_MAX_AGE_SECONDS;
  const maxAccuracyMeters =
    Number.isFinite(input.maxAccuracyMeters) &&
    (input.maxAccuracyMeters || 0) > 0
      ? (input.maxAccuracyMeters as number)
      : LOCATION_MAX_ACCURACY_METERS;
  if (input.photo && photoReason)
    errors.push("交付照片与免拍照原因不能同时提交");
  if (!input.photo && !photoReason)
    errors.push("请上传交付照片或填写有效的免拍照原因");
  if (input.position) {
    if (locationReason && !adminSkip)
      errors.push("已提供真实定位，不能再填写跳过定位原因");
    errors.push(
      ...locationErrors(
        input.position,
        input.nowMs ?? Date.now(),
        maxAgeSeconds,
        maxAccuracyMeters,
      ),
    );
  } else if (adminSkip) {
    if (!locationReason) errors.push("管理员跳过定位必须填写原因");
    if (!input.locationFailureCode)
      errors.push("管理员跳过定位必须选择失败原因代码");
    else if (!FAILURE_CODE_SET.has(input.locationFailureCode))
      errors.push("定位失败原因代码无效");
  } else if (approved) {
    if (locationReason)
      errors.push("已获定位例外批准的配送员不能填写跳过定位原因");
  } else if (role === "ADMIN") {
    errors.push("当前管理员账号没有跳过定位的权限，请获取真实定位");
  } else {
    errors.push("配送员必须获取真实定位，或先等待当前配送执行的定位例外获批");
  }
  if (photoReason.length > 1000) errors.push("免拍照原因不能超过 1000 字");
  return Array.from(new Set(errors));
}

export function parseProofResponse(value: unknown): SingleProofResponse {
  const row = value as { order?: unknown; proof?: unknown } | null;
  const order = row?.order as DeliveryOrderView | undefined;
  const proof = row?.proof as DeliveryProofView | undefined;
  if (
    !order ||
    typeof order !== "object" ||
    typeof order.id !== "string" ||
    typeof order.version !== "number" ||
    typeof order.status !== "string" ||
    !proof ||
    typeof proof !== "object" ||
    typeof proof.assignmentId !== "string" ||
    typeof proof.deliveredAt !== "string"
  )
    throw new Error("服务端交付回执不完整，请刷新后核对实际状态");
  return { order, proof };
}

export function formatServerTime(value: string) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour12: false,
  });
}

export function normalizeProof(
  view: DeliveryProofView,
  photoUrl?: string,
): Proof {
  const position = view.position;
  return {
    photoUrl,
    skipPhotoReason: view.skipPhotoReason || undefined,
    position:
      position && Number.isFinite(position.latitude)
        ? {
            latitude: position.latitude,
            longitude: position.longitude,
            accuracyMeters: position.accuracyMeters,
            capturedAt: position.capturedAt,
            coordinateSystem: "WGS84",
          }
        : undefined,
    skipLocationReason: view.skipLocationReason || undefined,
    placementNote: view.placementNote || "",
    deliveredAt: formatServerTime(view.deliveredAt),
  };
}

/** Applies the server DTO, never a client-synthesized "delivered" state. */
export function applyConfirmedOrder<
  T extends {
    status: OrderStatus;
    version: number;
    proof?: Proof;
    history: { title: string; time: string; description: string }[];
    allowedActions?: string[];
    blockers?: { code: string; message: string }[];
  },
>(
  order: T,
  response: SingleProofResponse,
  photoUrl?: string,
): T & {
  status: OrderStatus;
  version: number;
  proof: Proof;
  allowedActions: string[];
  blockers: { code: string; message: string }[];
  history: { title: string; time: string; description: string }[];
} {
  const proof = normalizeProof(response.proof, photoUrl);
  return {
    ...order,
    status: response.order.status as OrderStatus,
    version: response.order.version,
    proof,
    allowedActions: response.order.allowedActions || order.allowedActions || [],
    blockers: response.order.blockers || [],
    history: [
      {
        title: "资料已送达",
        time: proof.deliveredAt,
        description: proof.placementNote,
      },
      ...order.history,
    ],
  };
}

const BLOCKER_LABELS: Record<string, string> = {
  PHOTO_OR_REASON_REQUIRED: "需要交付照片或免拍照原因",
  PHOTO_CONFLICT: "照片与免拍照原因不能同时提交",
  LOCATION_REQUIRED: "需要真实定位或管理员定位例外批准",
  LOCATION_CONFLICT: "定位与跳过原因不能同时提交",
  LOCATION_SKIP_FORBIDDEN: "配送员不能自行跳过定位",
  LOCATION_STALE: "定位已过期",
  LOCATION_ACCURACY: "定位精度不足",
  LOCATION_TIME_INVALID: "定位时间异常",
  PACKING_REQUIRED: "资料尚未装袋",
  ADDRESS_REVIEW_REQUIRED: "收货地址待核对",
  PAYMENT_REQUIRED: "订单尚未完成付款",
  PROOF_ALREADY_SUBMITTED: "该订单已提交过交付凭证",
  ASSIGNMENT_CHANGED: "配送执行已变更，请刷新",
  ASSIGNMENT_MISMATCH: "配送执行与订单不匹配",
  ASSIGNMENT_REQUIRED: "必须使用真实配送执行ID",
  ASSIGNMENT_INACTIVE: "配送执行已失效，请刷新",
  ASSIGNMENT_NOT_FOUND: "配送执行不存在，请刷新",
  BATCH_MISMATCH: "配送执行不属于当前批次，请刷新",
  BUILDING_MISMATCH: "一次批量交付只能选择同一楼栋的订单",
  PHOTO_NOT_IN_SCOPE: "交付照片不属于本次配送执行，请重新上传",
  PROOF_INVALID: "交付凭证不符合要求",
  LOCATION_INVALID: "定位不符合新鲜度或精度要求",
  INVALID_TRANSITION: "订单或配送执行状态已变化",
  DELIVERY_GUARD_FAILED: "交付前置条件已失效",
  VERSION_CONFLICT: "数据版本已变化，请刷新",
  BATCH_NOT_READY: "批次仍有未满足条件的订单",
  LOCATION_APPROVAL_STALE: "定位例外批准已过期，请重新申请",
};

/** Reads DomainError.details.blockers without assuming a shape beyond strings. */
export function submissionErrorText(error: unknown) {
  const row = error as {
    message?: unknown;
    code?: unknown;
    details?: { blockers?: unknown };
  };
  const message =
    typeof row?.message === "string" && row.message
      ? row.message
      : "提交失败，请刷新后重试";
  const raw = row?.details?.blockers;
  const labels = (Array.isArray(raw) ? raw : [])
    .map((item) => (typeof item === "string" ? item : ""))
    .filter(Boolean)
    .map((code) => BLOCKER_LABELS[code] || code);
  const unique = Array.from(new Set(labels));
  return unique.length ? `${message}（${unique.join("；")}）` : message;
}

export function batchProofPath(batchId: string) {
  return `/courier/batches/${batchId}/delivery-proofs/batch`;
}
