import { expect, it, vi } from "vitest";
const context = vi.hoisted(() => ({ id: "a", generation: 1, role: "COURIER" }));
vi.mock("../src/lib/store", () => ({
  DEMO: true,
  useStore: () => ({ role: context.role }),
}));
vi.mock("../src/growth/identity", () => ({
  useIdentity: () => ({ generation: context.generation }),
}));
vi.mock("react-router-dom", async (original) => ({
  ...(await original<typeof import("react-router-dom")>()),
  useParams: () => ({ id: context.id }),
}));
import { DeliveryProof } from "../src/courier/pages";
it("remounts all proof inputs and pending operations on route or identity change, but keeps the same form during ordinary renders", () => {
  const original = DeliveryProof();
  expect(DeliveryProof().key).toBe(original.key);
  context.id = "b";
  expect(DeliveryProof().key).not.toBe(original.key);
  context.id = "a";
  context.generation++;
  expect(DeliveryProof().key).not.toBe(original.key);
  context.generation = 1;
  context.role = "ADMIN";
  expect(DeliveryProof().key).not.toBe(original.key);
});
import {
  LOCATION_MAX_AGE_SECONDS,
  applyConfirmedOrder,
  approvedLocationException,
  assignmentIdFor,
  batchOrderBlockers,
  buildBatchProofPayload,
  canAdminDeliver,
  locationErrors,
  locationLimits,
  parseProofResponse,
  singleProofErrors,
  submissionErrorText,
  type DeliveryOrderView,
  type LocationExceptionApproval,
} from "../src/courier/task-state";
const nowMs = Date.parse("2026-10-30T12:00:00.000Z");
const freshPosition = {
  latitude: 31.2304,
  longitude: 121.4737,
  accuracyMeters: 12,
  capturedAt: new Date(nowMs - 10_000).toISOString(),
  coordinateSystem: "WGS84" as const,
};
function order(
  id: string,
  assignmentId: string | null,
  overrides: Partial<DeliveryOrderView> = {},
): DeliveryOrderView {
  return {
    id,
    version: 4,
    status: "DELIVERING",
    pickupCode: id.toUpperCase(),
    nickname: "验收同学",
    locationId: "building-1",
    batchId: "batch-1",
    deliveryAssignmentId: assignmentId,
    assignment: assignmentId
      ? { id: assignmentId, status: "STARTED", version: 1, batchId: "batch-1" }
      : null,
    blockers: [],
    allowedActions: ["SUBMIT_PROOF"],
    ...overrides,
  };
}
it("never uses a batch id as the delivery assignment id", () => {
  expect(assignmentIdFor(order("o-1", "asg-1"))).toBe("asg-1");
  expect(assignmentIdFor(order("o-1", null))).toBe("");
  expect(assignmentIdFor(order("o-1", "batch-1"))).toBe("batch-1");
});
it("builds one atomic batch payload with per-order real assignment ids and one shared photo", () => {
  const { payload, errors } = buildBatchProofPayload({
    batchId: "batch-1",
    buildingId: "building-1",
    orders: [order("o-1", "asg-1"), order("o-2", "asg-2")],
    selectedIds: ["o-1", "o-2"],
    photoAssetId: "asset-1",
    placementNote: "一楼收发台",
    position: freshPosition,
    nowMs,
  });
  expect(errors).toEqual([]);
  expect(payload?.items.map((item) => item.deliveryAssignmentId)).toEqual([
    "asg-1",
    "asg-2",
  ]);
  expect(payload?.items.map((item) => item.orderId)).toEqual(["o-1", "o-2"]);
  expect(payload?.items.every((item) => item.position === freshPosition)).toBe(
    true,
  );
  expect(payload?.items.map((item) => item.expectedAssignmentVersion)).toEqual([
    1, 1,
  ]);
  expect(payload?.photoAssetId).toBe("asset-1");
  expect(payload?.placementNote).toBe("一楼收发台");
});
it("blocks a partial delivery view without its current assignment version", () => {
  const partial = order("o-1", "asg-1", { assignment: null });
  expect(batchOrderBlockers(partial, "batch-1", "building-1").join("；")).toContain("配送执行版本");
  const result = buildBatchProofPayload({
    batchId: "batch-1", buildingId: "building-1", orders: [partial], selectedIds: [partial.id],
    photoAssetId: "asset-1", position: freshPosition, nowMs,
  });
  expect(result.payload).toBeNull();
  expect(result.errors.join("；")).toContain("配送执行版本");
});
function approval(
  assignmentId: string,
  assignmentVersion = 1,
): LocationExceptionApproval {
  return {
    id: `approval-${assignmentId}`,
    status: "APPROVED",
    assignmentId,
    assignmentVersion,
    reason: "设备无法定位",
    failureCode: "DEVICE_UNAVAILABLE",
    approvedAt: "2026-10-30T11:00:00.000Z",
  };
}
it("uses only the server approval bound to the current assignment for a batch", () => {
  const approvedOrder = order("o-1", "asg-1", {
    locationExceptionApproval: approval("asg-1"),
  });
  const { payload, errors } = buildBatchProofPayload({
    batchId: "batch-1",
    buildingId: "building-1",
    orders: [approvedOrder],
    selectedIds: ["o-1"],
    photoAssetId: "asset-1",
    nowMs,
  });
  expect(errors).toEqual([]);
  expect(payload?.items[0].position).toBeUndefined();
  expect(payload?.items[0].skipLocationReason).toBeUndefined();
  expect(payload?.items[0].locationFailureCode).toBeUndefined();

  const unapproved = buildBatchProofPayload({
    batchId: "batch-1",
    buildingId: "building-1",
    orders: [approvedOrder, order("o-2", "asg-2")],
    selectedIds: ["o-1", "o-2"],
    photoAssetId: "asset-1",
    nowMs,
  });
  expect(unapproved.payload).toBeNull();
  expect(unapproved.errors.join(" ")).toContain("需要真实定位");

  const stale = buildBatchProofPayload({
    batchId: "batch-1",
    buildingId: "building-1",
    orders: [
      order("o-1", "asg-1", {
        locationExceptionApproval: approval("asg-1", 0),
      }),
    ],
    selectedIds: ["o-1"],
    photoAssetId: "asset-1",
    nowMs,
  });
  expect(stale.payload).toBeNull();
});

it("ignores an approval for another assignment or version", () => {
  const base = order("o-1", "asg-1");
  expect(approvedLocationException(base)).toBeNull();
  expect(
    approvedLocationException({
      ...base,
      locationExceptionApproval: approval("asg-1"),
    })?.id,
  ).toBe("approval-asg-1");
  expect(
    approvedLocationException({
      ...base,
      locationExceptionApproval: approval("asg-other"),
    }),
  ).toBeNull();
  expect(
    approvedLocationException({
      ...base,
      locationExceptionApproval: approval("asg-1", 2),
    }),
  ).toBeNull();
});

it("reads location limits from public config and falls back to server defaults", () => {
  expect(locationLimits({})).toEqual({
    maxAgeSeconds: LOCATION_MAX_AGE_SECONDS,
    maxAccuracyMeters: 100,
  });
  expect(
    locationLimits({
      locationMaxAgeSeconds: 45,
      locationMaxAccuracyMeters: 25,
    }),
  ).toEqual({ maxAgeSeconds: 45, maxAccuracyMeters: 25 });
  const limited = buildBatchProofPayload({
    batchId: "batch-1",
    buildingId: "building-1",
    orders: [order("o-1", "asg-1")],
    selectedIds: ["o-1"],
    photoAssetId: "asset-1",
    position: { ...freshPosition, accuracyMeters: 30 },
    maxAgeSeconds: 45,
    maxAccuracyMeters: 25,
    nowMs,
  });
  expect(limited.payload).toBeNull();
  expect(limited.errors.join(" ")).toContain("超过 25 m");
});

it("allows admin skip controls only with the delivery capability", () => {
  expect(canAdminDeliver("ADMIN", [])).toBe(false);
  expect(canAdminDeliver("ADMIN", ["delivery.manage"])).toBe(true);
  expect(canAdminDeliver("ADMIN", ["*"])).toBe(true);
  expect(canAdminDeliver("COURIER", ["delivery.manage"])).toBe(false);
});
it("refuses to build a partial batch when any selected order is invalid", () => {
  const missing = buildBatchProofPayload({
    batchId: "batch-1",
    buildingId: "building-1",
    orders: [order("o-1", "asg-1"), order("o-2", null)],
    selectedIds: ["o-1", "o-2"],
    photoAssetId: "asset-1",
    position: freshPosition,
    nowMs,
  });
  expect(missing.payload).toBeNull();
  expect(missing.errors.join(" ")).toContain("缺少真实配送执行ID");
  const stale = buildBatchProofPayload({
    batchId: "batch-1",
    buildingId: "building-1",
    orders: [order("o-1", "asg-1"), order("o-2", "asg-2")],
    selectedIds: ["o-1", "o-2"],
    photoAssetId: "asset-1",
    position: {
      ...freshPosition,
      capturedAt: new Date(nowMs - 400_000).toISOString(),
    },
    nowMs,
  });
  expect(stale.payload).toBeNull();
  expect(stale.errors.join(" ")).toContain(
    `定位已超过 ${LOCATION_MAX_AGE_SECONDS} 秒`,
  );
  const noPhoto = buildBatchProofPayload({
    batchId: "batch-1",
    buildingId: "building-1",
    orders: [order("o-1", "asg-1")],
    selectedIds: ["o-1"],
    position: freshPosition,
    nowMs,
  });
  expect(noPhoto.payload).toBeNull();
  expect(noPhoto.errors.join(" ")).toContain("共享交付照片");
  const mixed = buildBatchProofPayload({
    batchId: "batch-1",
    buildingId: "building-1",
    orders: [order("o-1", "asg-1"), order("o-2", "asg-2")],
    selectedIds: ["o-1", "o-2"],
    photoAssetId: "asset-1",
    skipPhotoReason: "同时填写原因",
    position: freshPosition,
    nowMs,
  });
  expect(mixed.payload).toBeNull();
  expect(mixed.errors.join(" ")).toContain("不能同时提交");
});
it("requires admin reason and code before skipping batch location", () => {
  const missing = buildBatchProofPayload({
    batchId: "batch-1",
    buildingId: "building-1",
    orders: [order("o-1", "asg-1")],
    selectedIds: ["o-1"],
    photoAssetId: "asset-1",
    adminSkip: { reason: "", code: "" },
    nowMs,
  });
  expect(missing.payload).toBeNull();
  expect(missing.errors.join(" ")).toContain("管理员跳过定位必须填写原因");
  const valid = buildBatchProofPayload({
    batchId: "batch-1",
    buildingId: "building-1",
    orders: [order("o-1", "asg-1")],
    selectedIds: ["o-1"],
    photoAssetId: "asset-1",
    adminSkip: { reason: "设备无法定位", code: "DEVICE_UNAVAILABLE" },
    nowMs,
  });
  expect(valid.errors).toEqual([]);
  expect(valid.payload?.items[0]).toMatchObject({
    skipLocationReason: "设备无法定位",
    locationFailureCode: "DEVICE_UNAVAILABLE",
  });
});
it("mirrors the single-proof guards, including approved exceptions and admin skips", () => {
  expect(
    singleProofErrors("COURIER", {
      photo: true,
      skipPhotoReason: "",
      position: null,
      skipLocationReason: "",
      nowMs,
    }),
  ).toContain("配送员必须获取真实定位，或先等待当前配送执行的定位例外获批");
  expect(
    singleProofErrors("COURIER", {
      photo: true,
      skipPhotoReason: "",
      position: null,
      skipLocationReason: "",
      approvedLocation: approval("asg-1"),
      assignmentId: "asg-1",
      assignmentVersion: 1,
      nowMs,
    }),
  ).toEqual([]);
  expect(
    singleProofErrors("COURIER", {
      photo: true,
      skipPhotoReason: "",
      position: null,
      skipLocationReason: "",
      approvedLocation: approval("asg-1", 2),
      assignmentId: "asg-1",
      assignmentVersion: 1,
      nowMs,
    }),
  ).not.toEqual([]);
  expect(
    singleProofErrors("COURIER", {
      photo: true,
      skipPhotoReason: "",
      position: null,
      skipLocationReason: "自行跳过",
      nowMs,
    }),
  ).toContain("配送员必须获取真实定位，或先等待当前配送执行的定位例外获批");
  expect(
    singleProofErrors("ADMIN", {
      photo: true,
      skipPhotoReason: "",
      position: null,
      skipLocationReason: "设备无法定位",
      locationFailureCode: "DEVICE_UNAVAILABLE",
      canSkipLocation: true,
      nowMs,
    }),
  ).toEqual([]);
  expect(
    singleProofErrors("ADMIN", {
      photo: true,
      skipPhotoReason: "",
      position: null,
      skipLocationReason: "设备无法定位",
      canSkipLocation: true,
      nowMs,
    }),
  ).toContain("管理员跳过定位必须选择失败原因代码");
  expect(
    singleProofErrors("ADMIN", {
      photo: true,
      skipPhotoReason: "",
      position: null,
      skipLocationReason: "",
      nowMs,
    }),
  ).toContain("当前管理员账号没有跳过定位的权限，请获取真实定位");
  expect(
    singleProofErrors("COURIER", {
      photo: true,
      skipPhotoReason: "同时填写",
      position: freshPosition,
      skipLocationReason: "",
      nowMs,
    }),
  ).toContain("交付照片与免拍照原因不能同时提交");
});
it("refuses incomplete server proof receipts and applies only the server DTO", () => {
  expect(() => parseProofResponse({})).toThrow("服务端交付回执不完整");
  expect(() => parseProofResponse({ order: { id: "o-1" }, proof: {} })).toThrow(
    "服务端交付回执不完整",
  );
  const response = parseProofResponse({
    order: {
      id: "o-1",
      version: 9,
      status: "DELIVERED",
      allowedActions: [],
      blockers: [],
    },
    proof: {
      assignmentId: "asg-1",
      deliveredAt: "2026-10-30T12:00:00.000Z",
      placementNote: "一楼收发台",
      skipPhotoReason: null,
    },
  });
  const applied = applyConfirmedOrder(
    {
      status: "DELIVERING",
      version: 8,
      history: [
        { title: "开始配送", time: "2026-10-30 18:00", description: "" },
      ],
      allowedActions: ["SUBMIT_PROOF"],
      blockers: [{ code: "PACKING_REQUIRED", message: "资料尚未装袋" }],
    },
    response,
    "https://example.test/photo.jpg",
  );
  expect(applied.status).toBe("DELIVERED");
  expect(applied.version).toBe(9);
  expect(applied.proof?.photoUrl).toBe("https://example.test/photo.jpg");
  expect(applied.proof?.placementNote).toBe("一楼收发台");
  expect(applied.blockers).toEqual([]);
  expect(applied.allowedActions).toEqual([]);
  expect(applied.history[0].title).toBe("资料已送达");
});
it("reports server blockers and keeps unknown codes visible", () => {
  const text = submissionErrorText({
    message: "订单状态已变化",
    code: "VERSION_CONFLICT",
    details: {
      blockers: ["VERSION_CONFLICT", "FUTURE_CODE", "VERSION_CONFLICT"],
    },
  });
  expect(text).toContain("订单状态已变化");
  expect(text).toContain("数据版本已变化");
  expect(text).toContain("FUTURE_CODE");
  expect(text.match(/数据版本已变化/g)).toHaveLength(1);
});
it("blocks cross-building and wrong-batch orders from a batch proof", () => {
  expect(
    batchOrderBlockers(
      order("o-1", "asg-1", { locationId: "building-2" }),
      "batch-1",
      "building-1",
    ),
  ).toContain("订单不在当前楼栋");
  expect(
    batchOrderBlockers(
      order("o-1", "asg-1", { status: "DELIVERED" }),
      "batch-1",
      "building-1",
    ),
  ).toContain("订单尚未开始配送");
  expect(
    batchOrderBlockers(order("o-1", "batch-1"), "batch-1", "building-1"),
  ).toContain("不能使用批次ID作为配送执行ID");
  expect(
    locationErrors({ ...freshPosition, accuracyMeters: 140 }, nowMs),
  ).toEqual(["定位精度 ±140 m，超过 100 m，请到开阔处重新获取"]);
});
