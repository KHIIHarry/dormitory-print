import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Field, Notice, Panel } from "../components/ui";
import { useIdentity } from "../growth/identity";
import { DEMO, isUncertainSubmission, mutation, request } from "../lib/api";
import type { Position, Role } from "../lib/types";
import {
  DeviceTask,
  LOCATION_FAILURE_CODES,
  approvedLocationException,
  assignmentIdFor,
  batchOrderBlockers,
  batchProofPath,
  buildBatchProofPayload,
  canAdminDeliver,
  photoTransfer,
  submissionErrorText,
  type CourierBatchView,
  type DeliveryOrderView,
  type LocationLimits,
} from "./task-state";
import "./batch-proof.css";

type BatchDetail = { batch: CourierBatchView; orders: DeliveryOrderView[] };

type BatchProofProps = {
  batchId: string;
  buildingId: string;
  buildingName: string;
  role: Role;
  limits: LocationLimits;
  onClose: () => void;
  onConfirmed: () => void | Promise<void>;
};

/**
 * One shared photo + one atomic batch request. The component never loops
 * single-order proof calls and never substitutes a batch id for an assignment.
 */
export function BatchProof({
  batchId,
  buildingId,
  buildingName,
  role,
  limits,
  onClose,
  onConfirmed,
}: BatchProofProps) {
  const [detail, setDetail] = useState<BatchDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [selected, setSelected] = useState<string[] | null>(null);
  const [position, setPosition] = useState<Position | null>(null);
  const [locating, setLocating] = useState(false);
  const [locationError, setLocationError] = useState("");
  const [photo, setPhoto] = useState<{ id: string; url: string } | null>(null);
  const [photoAnchor, setPhotoAnchor] = useState("");
  const [uploading, setUploading] = useState(false);
  const [transferLabel, setTransferLabel] = useState("");
  const [photoError, setPhotoError] = useState("");
  const [skipPhoto, setSkipPhoto] = useState(false);
  const [photoReason, setPhotoReason] = useState("");
  const [note, setNote] = useState("");
  const [adminCode, setAdminCode] = useState<string>(
    LOCATION_FAILURE_CODES[0][0],
  );
  const [adminReason, setAdminReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [success, setSuccess] = useState("");
  const { staff } = useIdentity();
  const adminCapable = canAdminDeliver(role, staff?.capabilities);
  const photoTask = useRef(new DeviceTask());
  const submitTask = useRef(new DeviceTask());
  const fileInput = useRef<HTMLInputElement>(null);
  const objectUrl = useRef<string | null>(null);

  const load = useCallback(async () => {
    const next = await request<BatchDetail>(`/courier/batches/${batchId}`);
    setDetail(next);
    return next;
  }, [batchId]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    load()
      .then(() => {
        if (alive) setLoadError("");
      })
      .catch((error: unknown) => {
        if (alive) setLoadError(submissionErrorText(error));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
      photoTask.current.cancel();
      submitTask.current.cancel();
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
    };
  }, [load]);

  const orders = (detail?.orders || []).filter(
    (order) => order.locationId === buildingId,
  );
  const blockersFor = (order: DeliveryOrderView) =>
    batchOrderBlockers(order, batchId, buildingId);
  const eligibleIds = orders
    .filter((order) => blockersFor(order).length === 0)
    .map((order) => order.id);
  const eligibleKey = eligibleIds.join("|");

  useEffect(() => {
    if (loading) return;
    setSelected((current) =>
      current === null
        ? eligibleIds
        : current.filter((id) => eligibleIds.includes(id)),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eligibleKey, loading]);

  const chosen = selected || [];
  const selectedOrders = orders.filter(
    (order) => chosen.includes(order.id) && !blockersFor(order).length,
  );
  const firstSelected = selectedOrders[0];
  const anchorAssignment = firstSelected ? assignmentIdFor(firstSelected) : "";

  // The shared photo is anchored to the first selected real assignment. Server
  // scope checks reject an anchor outside the submitted selection, so a
  // selection change that moves the anchor requires a fresh upload.
  useEffect(() => {
    if (
      !photo ||
      !photoAnchor ||
      !anchorAssignment ||
      photoAnchor === anchorAssignment
    )
      return;
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
    objectUrl.current = null;
    setPhoto(null);
    setPhotoAnchor("");
    setTransferLabel("");
    setPhotoError("共享照片按首个订单的配送执行登记；订单顺序变化后请重新上传");
  }, [anchorAssignment, photo, photoAnchor]);
  const approvedSelected = selectedOrders.filter(
    (order) => approvedLocationException(order) !== null,
  ).length;

  const toggle = (orderId: string, checked: boolean) =>
    setSelected((current) => {
      const base = current || [];
      return checked
        ? Array.from(new Set([...base, orderId]))
        : base.filter((id) => id !== orderId);
    });

  const locate = () => {
    if (!navigator.geolocation) {
      setLocationError("当前设备不支持定位，无法提交批量送达");
      return;
    }
    setLocating(true);
    setLocationError("");
    navigator.geolocation.getCurrentPosition(
      (result) => {
        setPosition({
          latitude: result.coords.latitude,
          longitude: result.coords.longitude,
          accuracyMeters: result.coords.accuracy,
          capturedAt: new Date(result.timestamp).toISOString(),
          coordinateSystem: "WGS84",
        });
        setLocating(false);
      },
      (error) => {
        setLocating(false);
        setLocationError(
          error.code === 1
            ? "定位权限被拒绝，请在浏览器设置中允许定位"
            : "定位失败，请重试；如设备无法定位，可申请管理员定位例外",
        );
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 },
    );
  };

  const pickPhoto = async (file: File | undefined) => {
    if (!file) return;
    setPhotoError("");
    if (!file.type.startsWith("image/")) {
      setPhotoError("请选择照片文件");
      return;
    }
    if (!firstSelected || !anchorAssignment) {
      setPhotoError("请先选择至少一个可交付订单，再上传共享照片");
      return;
    }
    if (anchorAssignment === batchId) {
      setPhotoError("缺少真实配送执行ID，请刷新后重试");
      return;
    }
    if (DEMO) {
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
      objectUrl.current = URL.createObjectURL(file);
      setPhoto({ id: `demo-asset-${file.name}`, url: objectUrl.current });
      setPhotoAnchor(anchorAssignment);
      return;
    }
    const token = photoTask.current.start();
    if (token === undefined) return;
    setUploading(true);
    setTransferLabel("正在上传照片…");
    const form = new FormData();
    form.append("file", file);
    form.append("purpose", "DELIVERY_PROOF");
    form.append("deliveryAssignmentId", anchorAssignment);
    try {
      const asset = await request<{ assetId: string; url?: string }>(
        "/assets",
        {
          method: "POST",
          body: form,
          onUploadProgress: (progress) => {
            if (!photoTask.current.current(token)) return;
            setTransferLabel(
              photoTransfer(progress.loaded, progress.total).label,
            );
          },
        },
      );
      if (!photoTask.current.current(token)) return;
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
      objectUrl.current = URL.createObjectURL(file);
      setPhoto({ id: asset.assetId, url: asset.url || objectUrl.current });
      setPhotoAnchor(anchorAssignment);
      setTransferLabel("照片已上传，提交后由服务端确认");
    } catch (error) {
      if (!photoTask.current.current(token)) return;
      setPhotoError(submissionErrorText(error));
    } finally {
      if (photoTask.current.finish(token)) setUploading(false);
    }
  };

  const removePhoto = () => {
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
    objectUrl.current = null;
    setPhoto(null);
    setPhotoAnchor("");
    setTransferLabel("");
    if (fileInput.current) fileInput.current.value = "";
  };

  const submit = async () => {
    if (busy) return;
    setErrors([]);
    setSuccess("");
    setBusy(true);
    let fresh: BatchDetail;
    try {
      fresh = await load();
    } catch (error) {
      setBusy(false);
      setErrors([submissionErrorText(error)]);
      return;
    }
    const freshOrders = fresh.orders.filter(
      (order) =>
        order.locationId === buildingId &&
        batchOrderBlockers(order, batchId, buildingId).length === 0,
    );
    const { payload, errors: validation } = buildBatchProofPayload({
      batchId,
      buildingId,
      orders: freshOrders,
      selectedIds: chosen,
      photoAssetId: photo?.id,
      skipPhotoReason: skipPhoto ? photoReason : "",
      placementNote: note,
      position,
      maxAgeSeconds: limits.maxAgeSeconds,
      maxAccuracyMeters: limits.maxAccuracyMeters,
      adminSkip:
        adminCapable && role === "ADMIN" && !position
          ? { reason: adminReason, code: adminCode }
          : null,
      nowMs: Date.now(),
    });
    if (!payload) {
      setBusy(false);
      setErrors(validation);
      return;
    }
    const token = submitTask.current.start();
    if (token === undefined) {
      setBusy(false);
      return;
    }
    try {
      await mutation(batchProofPath(batchId), payload);
      let refreshed = false;
      try {
        await load();
        refreshed = true;
      } catch {
        /* the 2xx response is authoritative; status will be re-fetched later */
      }
      if (!submitTask.current.current(token)) return;
      setSuccess(
        refreshed
          ? "服务端已确认本楼栋批量送达，订单状态已更新"
          : "服务端已确认批量送达，状态刷新失败，请返回后重新加载",
      );
      setSelected([]);
      removePhoto();
      setSkipPhoto(false);
      setPhotoReason("");
      setNote("");
      await onConfirmed();
    } catch (error) {
      if (!submitTask.current.current(token)) return;
      const uncertain = isUncertainSubmission(error);
      const issues = [submissionErrorText(error)];
      if (uncertain)
        issues.push("网络中断，提交结果未知；未重复提交，请核对订单实际状态");
      else issues.push("本次批量提交未生效，所有订单保持原状态");
      try {
        await load();
        issues.push("已重新获取订单状态，请核对后再决定是否提交");
      } catch {
        issues.push("状态刷新失败，请返回批次页重新加载");
      }
      setErrors(issues);
    } finally {
      submitTask.current.finish(token);
      setBusy(false);
    }
  };

  return (
    <Panel className="batch-proof" title={`${buildingName} · 批量送达`}>
      <p className="batch-proof-hint">
        同一进行中批次、同一楼栋：勾选订单，共用一张交付照片，一次提交由服务端逐单校验并整批原子确认。
      </p>
      {loading ? (
        <p className="batch-proof-muted">正在获取最新订单状态…</p>
      ) : null}
      {loadError ? (
        <Notice>
          <p>{loadError}</p>
          <Button
            variant="secondary"
            onClick={() => {
              setLoading(true);
              load()
                .then(() => setLoadError(""))
                .catch((error: unknown) =>
                  setLoadError(submissionErrorText(error)),
                )
                .finally(() => setLoading(false));
            }}
          >
            重新加载
          </Button>
        </Notice>
      ) : null}
      {!loading && !loadError && !orders.length ? (
        <p className="batch-proof-muted">本楼栋暂无可交付订单。</p>
      ) : null}
      {orders.length ? (
        <>
          <ul className="batch-proof-orders">
            {orders.map((order) => {
              const blockers = blockersFor(order);
              const checked = chosen.includes(order.id);
              const approval = approvedLocationException(order);
              return (
                <li key={order.id} className="batch-proof-order">
                  <label className="checkbox-label">
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={!!blockers.length}
                      onChange={(event) =>
                        toggle(order.id, event.target.checked)
                      }
                    />
                    <span>
                      <strong>{order.pickupCode || order.id}</strong>
                      <small>
                        {order.nickname || "未命名"} · 执行{" "}
                        {assignmentIdFor(order) || "缺失"} · 订单 v
                        {order.version} · 执行 v
                        {order.assignment?.version ?? "?"}
                      </small>
                    </span>
                  </label>
                  {blockers.length ? (
                    <ul className="batch-proof-blockers">
                      {blockers.map((blocker) => (
                        <li key={blocker}>{blocker}</li>
                      ))}
                    </ul>
                  ) : null}
                  {role === "COURIER" && approval ? (
                    <p className="batch-proof-approved">
                      服务端已批准定位例外 #{approval.id}
                      {approval.reason ? `：${approval.reason}` : ""}
                      {approval.failureCode
                        ? `（${approval.failureCode}）`
                        : ""}
                      。仅对当前配送执行与版本有效。
                    </p>
                  ) : checked && !blockers.length && role === "COURIER" ? (
                    <LocationExceptionRequest
                      orderId={order.id}
                      expectedVersion={order.version}
                      onRequested={() => void load().catch(() => undefined)}
                    />
                  ) : null}
                </li>
              );
            })}
          </ul>

          <section className="batch-proof-section">
            <h3>交付照片（共用一张）</h3>
            {photo ? (
              <figure className="batch-proof-photo">
                <img src={photo.url} alt="批量交付照片预览" />
                <figcaption>{transferLabel || "已上传"}</figcaption>
                <Button variant="text" onClick={removePhoto}>
                  移除照片
                </Button>
              </figure>
            ) : (
              <>
                <input
                  ref={fileInput}
                  className="batch-proof-file"
                  type="file"
                  accept="image/*"
                  capture="environment"
                  onChange={(event) => void pickPhoto(event.target.files?.[0])}
                />
                <Button
                  variant="secondary"
                  busy={uploading}
                  busyLabel="正在上传…"
                  onClick={() => fileInput.current?.click()}
                >
                  拍照或选择照片
                </Button>
                <p className="batch-proof-muted">
                  照片只存储一次，并以首个勾选订单的真实配送执行ID登记；服务端会校验其与批次的关系。
                </p>
              </>
            )}
            {uploading ? (
              <p className="batch-proof-muted">{transferLabel}</p>
            ) : null}
            {photoError ? <p className="field-error">{photoError}</p> : null}
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={skipPhoto}
                onChange={(event) => {
                  setSkipPhoto(event.target.checked);
                  if (event.target.checked) removePhoto();
                }}
              />
              <span>本次无法拍照，填写免拍照原因</span>
            </label>
            {skipPhoto ? (
              <Field label="免拍照原因（必填，客户可见）">
                <textarea
                  value={photoReason}
                  maxLength={1000}
                  onChange={(event) => setPhotoReason(event.target.value)}
                  placeholder="例如：客户要求放置在门口并已电话确认，无需拍照"
                />
              </Field>
            ) : null}
          </section>

          <section className="batch-proof-section">
            <h3>定位（逐单校验）</h3>
            <Button
              variant="secondary"
              busy={locating}
              busyLabel="正在定位…"
              onClick={locate}
            >
              获取当前位置
            </Button>
            {position ? (
              <div className="location-result">
                <strong>
                  {position.latitude.toFixed(5)},{" "}
                  {position.longitude.toFixed(5)}
                </strong>
                <p>
                  WGS84 · ±{Math.round(position.accuracyMeters)} m ·{" "}
                  {new Date(position.capturedAt).toLocaleTimeString("zh-CN", {
                    hour12: false,
                  })}
                </p>
              </div>
            ) : null}
            {selectedOrders.length ? (
              <p className="batch-proof-muted">
                已选 {selectedOrders.length} 单；服务端已批准定位例外{" "}
                {approvedSelected} 单。
                {position
                  ? " 本次批量提交将统一使用最新一次真实定位。"
                  : approvedSelected === selectedOrders.length
                    ? " 本次全部订单按各自当前配送执行的服务端批准提交。"
                    : " 未获批准且没有定位的订单会阻止整批提交。"}
              </p>
            ) : null}
            {locationError ? (
              <p className="field-error">{locationError}</p>
            ) : null}
            {role === "ADMIN" && !adminCapable && !position ? (
              <p className="field-error">
                当前管理员账号没有 delivery.manage 权限，不能跳过定位。
              </p>
            ) : null}
            {role === "ADMIN" && adminCapable && !position ? (
              <div className="batch-proof-admin-skip">
                <p className="batch-proof-muted">
                  管理员跳过定位时必须填写原因并选择失败代码。
                </p>
                <Field label="定位失败代码">
                  <select
                    value={adminCode}
                    onChange={(event) => setAdminCode(event.target.value)}
                  >
                    {LOCATION_FAILURE_CODES.map(([code, label]) => (
                      <option key={code} value={code}>
                        {label}（{code}）
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="跳过定位原因">
                  <textarea
                    value={adminReason}
                    maxLength={1000}
                    onChange={(event) => setAdminReason(event.target.value)}
                  />
                </Field>
              </div>
            ) : null}
          </section>

          <Field label="摆放说明（选填，全部订单共用；客户凭证可见）">
            <textarea
              value={note}
              maxLength={500}
              onChange={(event) => setNote(event.target.value)}
              placeholder="例如：已放入 1 栋一楼取件架第二层"
            />
          </Field>

          {errors.length ? (
            <Notice>
              <ul>
                {errors.map((error) => (
                  <li key={error}>{error}</li>
                ))}
              </ul>
            </Notice>
          ) : null}
          {success ? <Notice>{success}</Notice> : null}
          <div className="button-row batch-proof-actions">
            <Button
              onClick={() => void submit()}
              busy={busy || uploading}
              busyLabel="正在提交…"
              disabled={!selectedOrders.length}
            >
              确认批量送达（{selectedOrders.length} 单）
            </Button>
            <Button variant="text" onClick={onClose} disabled={busy}>
              取消
            </Button>
          </div>
        </>
      ) : null}
    </Panel>
  );
}

/** Explicit, per-assignment request; approval remains an admin/server decision. */
export function LocationExceptionRequest({
  orderId,
  expectedVersion,
  onRequested,
}: {
  orderId: string;
  expectedVersion: number;
  onRequested?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState<string>(LOCATION_FAILURE_CODES[0][0]);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const send = async () => {
    if (busy) return;
    if (!reason.trim()) {
      setError("请填写定位失败的具体原因");
      return;
    }
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await mutation(`/courier/orders/${orderId}/location-exception`, {
        expectedVersion,
        code,
        reason: reason.trim(),
      });
      setMessage("已提交，等待管理员审批；批准前不能跳过定位。");
      setReason("");
      onRequested?.();
    } catch (failure) {
      setError(submissionErrorText(failure));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="batch-proof-exception">
      <Button
        variant="text"
        type="button"
        onClick={() => setOpen((value) => !value)}
      >
        {open ? "收起定位例外申请" : "申请定位例外（需管理员审批）"}
      </Button>
      {open ? (
        <div className="batch-proof-exception-form">
          <Field label="定位失败代码">
            <select
              value={code}
              onChange={(event) => setCode(event.target.value)}
            >
              {LOCATION_FAILURE_CODES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}（{value}）
                </option>
              ))}
            </select>
          </Field>
          <Field label="原因">
            <textarea
              value={reason}
              maxLength={1000}
              onChange={(event) => setReason(event.target.value)}
              placeholder="说明为什么无法在该配送执行中获取真实定位"
            />
          </Field>
          <Button
            variant="secondary"
            type="button"
            busy={busy}
            busyLabel="正在提交…"
            onClick={() => void send()}
          >
            提交定位例外申请
          </Button>
          {message ? <p className="batch-proof-muted">{message}</p> : null}
          {error ? <p className="field-error">{error}</p> : null}
        </div>
      ) : null}
    </div>
  );
}
