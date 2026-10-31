import { formatDisplayTime } from "../lib/display-format";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  Camera,
  Check,
  CheckCircle2,
  ChevronRight,
  Clock,
  Image,
  LocateFixed,
  LogOut,
  MapPin,
  Navigation,
  PackageCheck,
  Phone,
  RefreshCw,
  ShieldCheck,
  TriangleAlert,
  Truck,
  Upload,
  User,
  X,
} from "lucide-react";
import { DEMO, useStore } from "../lib/store";
import { isUncertainSubmission, mutation, now, request } from "../lib/api";
import type { Order, Position, Proof } from "../lib/types";
import {
  Badge,
  Button,
  Empty,
  Field,
  LinkButton,
  Notice,
  PageHeading,
  Panel,
} from "../components/ui";
import { Progress } from "../components/feedback";
import { useIdentity } from "../growth/identity";
import {
  DeviceTask,
  LOCATION_FAILURE_CODES,
  applyConfirmedOrder,
  approvedLocationException,
  canAdminDeliver,
  locationLimits,
  parseProofResponse,
  photoTransfer,
  proofScope,
  singleProofErrors,
  submissionErrorText,
} from "./task-state";
import { BatchProof, LocationExceptionRequest } from "./batch-proof";
import "./batch-proof.css";
export function CourierHome() {
  const { data, role } = useStore();
  const { staff } = useIdentity();
  const batches = data.batches.filter(
    (b) => role === "ADMIN" || !DEMO || b.courierId === "staff-2",
  );
  const orders = data.orders.filter((o) =>
    batches.some((b) => b.orderIds.includes(o.id)),
  );
  return (
    <>
      <div className="courier-greeting">
        <div>
          <p>你好，{staff?.name || "配送同事"}</p>
          <h1>
            每一份托付，
            <br />
            都好好送达。
          </h1>
        </div>
        <span className="courier-hero-icon">
          <Truck size={43} />
        </span>
      </div>
      <div className="courier-stat-row">
        <div>
          <strong>
            {orders.filter((o) => o.status !== "DELIVERED").length}
          </strong>
          <span>待送订单</span>
        </div>
        <div>
          <strong>
            {orders.filter((o) => o.status === "DELIVERED").length}
          </strong>
          <span>已送达</span>
        </div>
        <div>
          <strong>{batches.length}</strong>
          <span>我的批次</span>
        </div>
      </div>
      <div className="section-heading">
        <h2>我的配送批次</h2>
        <span className="muted">仅显示已分配任务</span>
      </div>
      {batches.map((b) => (
        <Link
          className="panel courier-batch"
          to={`/courier/batches/${b.id}`}
          key={b.id}
        >
          <div className="courier-batch-heading">
            <Badge status={b.status} />
            <ChevronRight size={20} />
          </div>
          <h2>{b.name}</h2>
          <p>
            <Clock size={15} />
            {b.date} · {b.time}
          </p>
          <div className="courier-batch-footer">
            <span>
              <PackageCheck size={17} />
              {b.orderIds.length} 份资料
            </span>
            <span>
              查看任务
              <ArrowRight size={16} />
            </span>
          </div>
        </Link>
      ))}
      {!batches.length && (
        <Panel>
          <Empty
            title="暂时没有配送任务"
            text="管理员分配批次后，会显示在这里。"
          />
        </Panel>
      )}
      <Notice>
        按最新安排配送。定位或交付遇到问题时，请记录异常并联系管理员。
      </Notice>
    </>
  );
}
export function CourierBatch() {
  const { id } = useParams();
  const { data, role, refresh, toast } = useStore();
  const { staff } = useIdentity();
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState("");
  const [proofBuilding, setProofBuilding] = useState<string | null>(null);
  const batch = data.batches.find(
    (b) =>
      b.id === id && (role === "ADMIN" || !DEMO || b.courierId === "staff-2"),
  );
  if (!batch)
    return (
      <Empty
        title="该批次未分配给你"
        text="分配可能已变更，请返回工作台刷新。"
      />
    );
  const adminCapable = canAdminDeliver(role, staff?.capabilities);
  const mayDeliver = role !== "ADMIN" || adminCapable;
  const orders = data.orders.filter((o) => batch.orderIds.includes(o.id));
  const buildings = Array.from(new Set(orders.map((o) => o.locationId)));
  const buildingName = (buildingId: string) =>
    data.buildings.find((b) => b.id === buildingId)?.name || buildingId;
  const start = async () => {
    if (starting) return;
    setStarting(true);
    setStartError("");
    try {
      await mutation(`/courier/batches/${batch.id}/start`, {
        expectedVersion: batch.version,
      });
      toast("批次已开始配送");
      await refresh();
    } catch (error) {
      setStartError(submissionErrorText(error));
      if (
        isUncertainSubmission(error) ||
        (error as { status?: number }).status === 409
      )
        await refresh();
    } finally {
      setStarting(false);
    }
  };
  return (
    <>
      <Link className="back-link" to="/courier/">
        <ArrowLeft size={16} />
        返回配送任务
      </Link>
      <PageHeading
        title={batch.name}
        description={`${batch.date} · ${batch.time}`}
      />
      <Notice>
        {batch.status === "DELIVERING"
          ? "批次已开始配送，请按楼栋完成交付。"
          : batch.status === "PUBLISHED"
            ? "批次已发布，确认后整批开始配送；开始后才能提交交付。"
            : batch.status === "POSTPONED"
              ? "批次已延期，请等待管理员重新发布配送计划。"
              : "此批次仍在计划中，待管理员发布后才能开始配送。"}
      </Notice>
      {role === "ADMIN" && !adminCapable ? (
        <Notice>
          当前管理员账号没有 delivery.manage
          权限，不能开始批次或提交交付；请使用配送员账号或联系超级管理员。
        </Notice>
      ) : null}
      {batch.status === "PUBLISHED" && !DEMO && mayDeliver ? (
        <div className="courier-batch-start">
          <Button
            busy={starting}
            busyLabel="正在开始…"
            onClick={() => void start()}
          >
            <Truck size={18} />
            开始整批配送
          </Button>
          <p className="muted">
            整批原子开始：任何未满足条件的订单都会阻止本次开始，不会部分生效。
          </p>
          {startError ? <Notice type="error">{startError}</Notice> : null}
        </div>
      ) : null}
      {batch.status === "DELIVERING" && startError ? (
        <Notice type="error">{startError}</Notice>
      ) : null}
      {buildings.map((buildingId) => {
        const buildingOrders = orders.filter(
          (o) => o.locationId === buildingId,
        );
        return (
          <section className="courier-building-group" key={buildingId}>
            <h2>
              <MapPin size={19} />
              {buildingName(buildingId)}
              <span>{buildingOrders.length} 单</span>
              {batch.status === "DELIVERING" && !DEMO && mayDeliver ? (
                <Button
                  variant="secondary"
                  className="courier-batch-proof-toggle"
                  onClick={() =>
                    setProofBuilding(
                      proofBuilding === buildingId ? null : buildingId,
                    )
                  }
                >
                  {proofBuilding === buildingId
                    ? "收起批量送达"
                    : "批量送达本楼栋"}
                </Button>
              ) : null}
            </h2>
            {buildingOrders.map((o) => (
              <Link
                className="panel courier-order"
                to={`/courier/orders/${o.id}`}
                key={o.id}
              >
                <div>
                  <strong>{o.pickupCode}</strong>
                  <Badge status={o.status} />
                </div>
                <p>
                  {o.nickname}
                  <span>{o.phone}</span>
                </p>
                <div className="courier-order-bottom">
                  <span>
                    {o.packingStatus === "PACKED" ? "资料已装袋" : "尚未装袋"}
                  </span>
                  <ChevronRight size={18} />
                </div>
              </Link>
            ))}
            {proofBuilding === buildingId ? (
              <BatchProof
                key={`${batch.id}:${buildingId}`}
                batchId={batch.id}
                buildingId={buildingId}
                buildingName={buildingName(buildingId)}
                role={role || "COURIER"}
                limits={locationLimits(data.config)}
                onClose={() => setProofBuilding(null)}
                onConfirmed={async () => {
                  await refresh();
                }}
              />
            ) : null}
          </section>
        );
      })}
      <Notice>
        定位或交付遇到问题时，请申请定位例外或记录异常并联系管理员。
      </Notice>
    </>
  );
}
function useAssignment() {
  const { id } = useParams();
  const store = useStore();
  const order = store.data.orders.find((o) => o.id === id);
  const batch = store.data.batches.find(
    (b) =>
      b.orderIds.includes(id || "") &&
      (store.role === "ADMIN" || !DEMO || b.courierId === "staff-2"),
  );
  return { ...store, order: batch ? order : undefined, batch };
}
export function CourierOrder() {
  const { order, batch, data } = useAssignment();
  if (!order)
    return (
      <Empty title="该配送任务已不可访问" text="请返回工作台确认是否已改派。" />
    );
  return (
    <>
      <Link className="back-link" to={`/courier/batches/${batch?.id}`}>
        <ArrowLeft size={16} />
        返回配送批次
      </Link>
      <PageHeading title="配送订单" action={<Badge status={order.status} />} />
      <Panel className="courier-detail">
        <small>请核对塑封袋上的提取码</small>
        <strong className="courier-pickup-code">{order.pickupCode}</strong>
        <div className="courier-destination">
          <MapPin size={25} />
          <div>
            <span>目标楼栋</span>
            <h2>
              {data.buildings.find((b) => b.id === order.locationId)?.name}
            </h2>
          </div>
        </div>
        <div className="courier-recipient">
          <span className="avatar">
            <User size={22} />
          </span>
          <div>
            <b>{order.nickname}</b>
            <p>{order.phone}</p>
          </div>
          <a
            href={`tel:${order.phone}`}
            className="call-button"
            aria-label={`致电 ${order.nickname}`}
          >
            <Phone size={23} />
          </a>
        </div>
        <dl className="detail-dl">
          <div>
            <dt>当前安排</dt>
            <dd>
              {batch?.date}
              <br />
              {batch?.time}
            </dd>
          </div>
          <div>
            <dt>装袋状态</dt>
            <dd>
              {order.packingStatus === "PACKED" ? "已确认装袋" : "未完成装袋"}
            </dd>
          </div>
        </dl>
      </Panel>
      {order.status === "DELIVERED" ? (
        <Notice>此订单已完成交付，交付时间 {formatDisplayTime(order.proof?.deliveredAt)}。</Notice>
      ) : (
        <div className="courier-action-stack">
          {order.status === "DELIVERING" && order.packingStatus === "PACKED" ? (
            <LinkButton to={`/courier/orders/${order.id}/proof`}>
              <Camera size={19} />
              拍照并完成交付
            </LinkButton>
          ) : (
            <Button disabled>配送尚未开始，暂不能交付</Button>
          )}
          <LinkButton
            variant="secondary"
            to={`/courier/orders/${order.id}/exception`}
          >
            <TriangleAlert size={18} />
            记录配送异常
          </LinkButton>
        </div>
      )}
    </>
  );
}
export function DeliveryProof() {
  const { id } = useParams();
  const { generation } = useIdentity();
  const { role } = useStore();
  return <DeliveryProofForm key={proofScope(id, generation, role)} />;
}
function DeliveryProofForm() {
  const { order, batch, data, role, act, toast, setData, refresh } =
    useAssignment();
  const nav = useNavigate();
  const [position, setPosition] = useState<Position | null>(null);
  const [locating, setLocating] = useState(false);
  const [locationError, setLocationError] = useState("");
  const [useApprovedLocation, setUseApprovedLocation] = useState(false);
  const [photo, setPhoto] = useState<{ id: string; url: string } | null>(null);
  const [uploading, setUploading] = useState(false);
  const [photoError, setPhotoError] = useState("");
  const [skipPhoto, setSkipPhoto] = useState(false);
  const [photoReason, setPhotoReason] = useState("");
  const [skipLocation, setSkipLocation] = useState(false);
  const [locationReason, setLocationReason] = useState("");
  const [adminCode, setAdminCode] = useState<string>(
    LOCATION_FAILURE_CODES[0][0],
  );
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const input = useRef<HTMLInputElement>(null);
  const { generation, staff } = useIdentity();
  const locationTask = useRef(new DeviceTask());
  const photoTask = useRef(new DeviceTask());
  const submitTask = useRef(new DeviceTask());
  const [transfer, setTransfer] = useState<{
    value?: number;
    label: string;
  } | null>(null);
  const [uploadHint, setUploadHint] = useState(false);
  useEffect(() => {
    if (!uploading) {
      setUploadHint(false);
      return;
    }
    const timer = setTimeout(() => setUploadHint(true), 350);
    return () => clearTimeout(timer);
  }, [uploading]);
  useEffect(
    () => () => {
      locationTask.current.cancel();
      photoTask.current.cancel();
      submitTask.current.cancel();
    },
    [order?.id, role, generation],
  );
  if (!order) return <Empty title="无权访问此配送任务" />;
  const delivery = order;
  const assignmentId =
    delivery.deliveryAssignmentId ||
    delivery.assignment?.id ||
    (DEMO ? `demo-assignment-${order.id}` : "");
  const approval = approvedLocationException(delivery);
  const usingApprovedLocation = useApprovedLocation && !!approval;
  const adminCapable = canAdminDeliver(role, staff?.capabilities);
  const limits = locationLimits(data.config);
  const available =
    order.status === "DELIVERING" &&
    order.packingStatus === "PACKED" &&
    batch?.status === "DELIVERING";
  const locate = () => {
    const token = locationTask.current.start();
    if (token === undefined) return;
    setLocating(true);
    setLocationError("");
    if (!navigator.geolocation) {
      locationTask.current.finish(token);
      setLocationError("此浏览器不支持定位，请更换设备或先申请管理员定位例外");
      setLocating(false);
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (p) => {
        if (!locationTask.current.finish(token)) return;
        setPosition({
          latitude: p.coords.latitude,
          longitude: p.coords.longitude,
          accuracyMeters: p.coords.accuracy,
          capturedAt: new Date(p.timestamp).toISOString(),
          coordinateSystem: "WGS84",
        });
        setLocating(false);
        setSkipLocation(false);
        setUseApprovedLocation(false);
      },
      (e) => {
        if (!locationTask.current.finish(token)) return;
        setLocationError(
          e.code === 1
            ? "定位权限被拒绝，请在浏览器设置中允许位置访问"
            : e.code === 3
              ? "定位超时，请到信号良好的位置重试"
              : "暂时无法获取位置，请重试或申请管理员定位例外",
        );
        setPosition(null);
        setLocating(false);
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 },
    );
  };
  const upload = async (file: File) => {
    if (!assignmentId || assignmentId === batch?.id) {
      setPhotoError("服务端未返回真实配送执行ID，请刷新后重试");
      return;
    }
    const token = photoTask.current.start();
    if (token === undefined) return;
    setTransfer(null);
    setUploading(true);
    setPhotoError("");
    try {
      if (!file.type.startsWith("image/"))
        throw new Error("请选择图片格式的交付照片");
      if (DEMO)
        setPhoto({ id: crypto.randomUUID(), url: URL.createObjectURL(file) });
      else {
        const fd = new FormData();
        fd.append("file", file);
        fd.append("purpose", "DELIVERY_PROOF");
        fd.append("deliveryAssignmentId", assignmentId);
        const result = await request<{ assetId: string; url: string }>(
          "/assets",
          {
            method: "POST",
            body: fd,
            onUploadProgress: ({ loaded, total }) => {
              if (photoTask.current.current(token))
                setTransfer(photoTransfer(loaded, total));
            },
            onUploadComplete: () => {
              if (photoTask.current.current(token))
                setTransfer({
                  value: 100,
                  label: "照片已传输，等待服务端确认…",
                });
            },
          },
        );
        if (!photoTask.current.current(token)) return;
        setPhoto({ id: result.assetId, url: result.url });
      }
      if (photoTask.current.current(token)) setSkipPhoto(false);
    } catch (e) {
      if (!photoTask.current.current(token)) return;
      setPhotoError(submissionErrorText(e));
    } finally {
      if (photoTask.current.finish(token)) {
        setUploading(false);
        setTransfer(null);
      }
    }
  };
  const submit = async () => {
    const issues = singleProofErrors(role || "COURIER", {
      photo: !!photo,
      skipPhotoReason: skipPhoto ? photoReason : "",
      position: usingApprovedLocation ? null : position,
      skipLocationReason: adminCapable && skipLocation ? locationReason : "",
      locationFailureCode: adminCapable && skipLocation ? adminCode : undefined,
      canSkipLocation: adminCapable,
      approvedLocation: usingApprovedLocation ? approval : null,
      assignmentId,
      assignmentVersion: delivery.assignment?.version,
      maxAgeSeconds: limits.maxAgeSeconds,
      maxAccuracyMeters: limits.maxAccuracyMeters,
    });
    if (!available) issues.push("配送分配或状态已变化，请返回刷新");
    if (!assignmentId || assignmentId === batch?.id)
      issues.push("服务端未返回真实配送执行ID，不能提交交付");
    setErrors(issues);
    if (issues.length) return;
    const token = submitTask.current.start();
    if (token === undefined) return;
    setBusy(true);
    const payload = {
      expectedVersion: order.version,
      deliveryAssignmentId: assignmentId,
      photoAssetId: photo?.id || null,
      skipPhotoReason: skipPhoto ? photoReason : null,
      position: usingApprovedLocation ? null : position,
      locationFailureCode: adminCapable && skipLocation ? adminCode : null,
      skipLocationReason: adminCapable && skipLocation ? locationReason : null,
      placementNote: note,
    };
    if (DEMO) {
      const proof: Proof = {
        photoUrl: photo?.url,
        skipPhotoReason: skipPhoto ? photoReason : undefined,
        position: usingApprovedLocation ? undefined : position || undefined,
        skipLocationReason:
          adminCapable && skipLocation ? locationReason : undefined,
        placementNote: note,
        deliveredAt: now(),
      };
      if (
        await act(`/courier/orders/${order.id}/proof`, payload, (s) => ({
          ...s,
          orders: s.orders.map((o) =>
            o.id === order.id
              ? {
                  ...o,
                  status: "DELIVERED",
                  proof,
                  version: o.version + 1,
                  history: [
                    {
                      title: "资料已送达",
                      time: proof.deliveredAt,
                      description: note,
                    },
                    ...o.history,
                  ],
                }
              : o,
          ),
        }))
      ) {
        if (!submitTask.current.current(token)) return;
        toast("交付已确认，凭证已更新");
        nav(`/courier/orders/${order.id}`);
      }
      if (submitTask.current.finish(token)) setBusy(false);
      return;
    }
    try {
      const response = await mutation<unknown>(
        `/courier/orders/${order.id}/proof`,
        payload,
      );
      const confirmed = parseProofResponse(response);
      if (!submitTask.current.current(token)) return;
      setData((state) => ({
        ...state,
        orders: state.orders.map((item) =>
          item.id === order.id
            ? applyConfirmedOrder(item, confirmed, photo?.url)
            : item,
        ),
      }));
      toast("服务端已确认交付，凭证已更新");
      nav(`/courier/orders/${order.id}`);
    } catch (error) {
      if (!submitTask.current.current(token)) return;
      const next = [submissionErrorText(error)];
      if (isUncertainSubmission(error)) {
        next.push("网络中断，提交结果未知；未重复提交，正在核对订单实际状态");
        try {
          await refresh();
        } catch {
          next.push("状态刷新失败，请返回订单页重新加载");
        }
      } else if ((error as { status?: number }).status === 409) {
        try {
          await refresh();
        } catch {
          /* keep the server error text */
        }
        next.push("已刷新订单状态，请核对后重试");
      }
      setErrors(next);
    } finally {
      if (submitTask.current.finish(token)) setBusy(false);
    }
  };
  return (
    <>
      <Link className="back-link" to={`/courier/orders/${order.id}`}>
        <ArrowLeft size={16} />
        返回订单
      </Link>
      <PageHeading
        title="完成这次交付"
        description={`${order.pickupCode} · ${data.buildings.find((b) => b.id === order.locationId)?.name}`}
      />
      {!available && (
        <Notice type="error">
          当前不可交付，请确认批次已开始配送、订单已装袋且仍分配给你。
        </Notice>
      )}
      <Panel title="获取实际交付位置">
        <div
          className={`location-result ${position || usingApprovedLocation ? "success" : ""}`}
        >
          <span>
            <LocateFixed size={27} />
          </span>
          <div>
            <b>
              {usingApprovedLocation
                ? "使用服务端批准的定位例外"
                : position
                  ? "已获取设备真实位置"
                  : approval
                    ? "当前配送执行已有定位例外批准"
                    : "请在放置资料的位置获取定位"}
            </b>
            <p>
              {usingApprovedLocation
                ? "提交后由服务端校验批准是否仍绑定当前配送执行与版本"
                : position
                  ? `${position.latitude.toFixed(6)}, ${position.longitude.toFixed(6)} · WGS84`
                  : approval
                    ? "也可改用设备真实定位；服务端批准在本配送执行内有效"
                    : "校园地图热点不能代替设备定位"}
            </p>
            {position && (
              <small>
                精度 ±{Math.round(position.accuracyMeters)} m ·{" "}
                {new Date(position.capturedAt).toLocaleTimeString("zh-CN", {
                  timeZone: "Asia/Shanghai",
                })}
              </small>
            )}
          </div>
        </div>
        <Button
          className="full"
          variant="secondary"
          busy={locating}
          disabled={locating || !available || usingApprovedLocation}
          onClick={locate}
        >
          <LocateFixed size={17} />
          {locating
            ? "正在获取位置…"
            : position
              ? "重新获取位置"
              : "获取当前位置"}
        </Button>
        {locationError && <p className="field-error">{locationError}</p>}
        {role === "ADMIN" ? (
          adminCapable ? (
            <>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={skipLocation}
                  onChange={(e) => {
                    setSkipLocation(e.target.checked);
                    if (e.target.checked) {
                      locationTask.current.cancel();
                      setLocating(false);
                      setPosition(null);
                    }
                  }}
                />
                管理员：填写原因跳过定位（需要 delivery.manage 权限）
              </label>
              {skipLocation && (
                <>
                  <Field label="定位失败代码（必填）">
                    <select
                      value={adminCode}
                      onChange={(e) => setAdminCode(e.target.value)}
                    >
                      {LOCATION_FAILURE_CODES.map(([code, label]) => (
                        <option key={code} value={code}>
                          {label}（{code}）
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="定位例外原因">
                    <textarea
                      value={locationReason}
                      maxLength={1000}
                      onChange={(e) => setLocationReason(e.target.value)}
                      placeholder="说明设备定位失败原因"
                    />
                  </Field>
                </>
              )}
            </>
          ) : (
            <Notice>
              当前管理员账号没有 delivery.manage
              权限，不能跳过定位；请获取真实定位或联系有权限的管理员。
            </Notice>
          )
        ) : (
          <>
            {approval ? (
              <>
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={useApprovedLocation}
                    onChange={(e) => {
                      setUseApprovedLocation(e.target.checked);
                      if (e.target.checked) {
                        locationTask.current.cancel();
                        setLocating(false);
                        setPosition(null);
                        setLocationError("");
                      }
                    }}
                  />
                  使用服务端已批准的定位例外（本次配送执行）
                </label>
                <Notice>
                  服务端批准 #{approval.id}：{approval.reason || "定位例外"}
                  {approval.failureCode ? `（${approval.failureCode}）` : ""}
                  。仅对当前配送执行与版本有效；改派或版本变化后自动失效。
                </Notice>
              </>
            ) : null}
            {!approval && locationError ? (
              <Notice>
                配送员不能自行跳过定位。请重试，或提交定位例外申请并等待管理员批准当前配送执行。
              </Notice>
            ) : null}
            {!approval ? (
              <LocationExceptionRequest
                orderId={order.id}
                expectedVersion={order.version}
                onRequested={() => toast("定位例外申请已提交，等待管理员审批")}
              />
            ) : null}
            {approval && !useApprovedLocation && locationError ? (
              <Notice>
                无法定位时可勾选使用服务端已批准的定位例外；未批准前不能自行跳过定位。
              </Notice>
            ) : null}
          </>
        )}
      </Panel>
      <Panel title="记录交付照片">
        <input
          ref={input}
          type="file"
          hidden
          accept="image/*"
          capture="environment"
          onChange={(e) =>
            e.target.files?.[0] && void upload(e.target.files[0])
          }
        />
        {photo ? (
          <div className="delivery-photo-preview">
            <img src={photo.url} alt="已上传的交付照片" />
            <span>
              <CheckCircle2 size={15} />
              照片已就绪
            </span>
            <button
              className="icon-btn"
              aria-label="移除交付照片"
              onClick={() => {
                if (photo.url.startsWith("blob:"))
                  URL.revokeObjectURL(photo.url);
                setPhoto(null);
              }}
            >
              <X size={17} />
            </button>
          </div>
        ) : (
          <button
            className="capture-area"
            disabled={uploading || !available}
            onClick={() => input.current?.click()}
          >
            <span
              style={{
                width: 35,
                height: 35,
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              {uploading && uploadHint ? (
                <Progress
                  ring
                  value={transfer?.value}
                  label={transfer?.label || "正在准备照片…"}
                />
              ) : (
                <Camera size={35} />
              )}
            </span>
            <strong>拍照记录资料放置位置</strong>
            <span>优先调用相机，也可以从相册选择</span>
          </button>
        )}
        {photoError && <p className="field-error">{photoError}</p>}
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={skipPhoto}
            onChange={(e) => {
              setSkipPhoto(e.target.checked);
              if (e.target.checked) {
                photoTask.current.cancel();
                setUploading(false);
                setPhoto(null);
              }
            }}
          />
          因特殊情况无法拍照，填写免拍照原因
        </label>
        {skipPhoto && (
          <Field label="免拍照原因（必填、独立记录，客户凭证可见）">
            <textarea
              value={photoReason}
              maxLength={1000}
              onChange={(e) => setPhotoReason(e.target.value)}
              placeholder="必须填写有效原因，上传失败不会自动豁免"
            />
          </Field>
        )}
      </Panel>
      <Panel title="补充摆放说明">
        <Field label="方便同学找到资料（选填，客户凭证可见）">
          <textarea
            value={note}
            maxLength={500}
            onChange={(e) => setNote(e.target.value)}
            placeholder="例如：放在一楼收发台左侧"
          />
        </Field>
      </Panel>
      {errors.length > 0 && (
        <Notice type="error">
          {errors.map((e) => (
            <p key={e}>{e}</p>
          ))}
        </Notice>
      )}
      <div className="courier-action-stack">
        <Button
          busy={busy}
          disabled={busy || uploading || !available}
          onClick={submit}
        >
          <CheckCircle2 size={18} />
          {busy ? "等待服务端确认…" : "确认资料已送达"}
        </Button>
        <Link
          to={`/courier/orders/${order.id}/exception`}
          className="text-link"
        >
          遇到问题？记录配送异常
          <ArrowRight size={15} />
        </Link>
      </div>
    </>
  );
}
export function CourierException() {
  const { order, batch, act, toast } = useAssignment();
  const nav = useNavigate();
  const [type, setType] = useState("无法联系收件人");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  if (!order) return <Empty title="无权访问此任务" />;
  return (
    <>
      <Link className="back-link" to={`/courier/orders/${order.id}`}>
        <ArrowLeft size={16} />
        返回订单
      </Link>
      <PageHeading
        title="记录配送异常"
        description={`提取码 ${order.pickupCode}`}
      />
      <Panel>
        <Notice>
          提交后由管理员跟进处理。请妥善保管资料，配送员不能自行改期或重新打印。
        </Notice>
        <Field label="异常类型">
          <select value={type} onChange={(e) => setType(e.target.value)}>
            {[
              "无法联系收件人",
              "无法获取设备定位",
              "照片上传失败",
              "资料损坏或缺失",
              "楼栋暂时无法进入",
              "其他配送问题",
            ].map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
        </Field>
        <Field label="详细原因（必填）">
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={6}
            placeholder="描述现场情况、已尝试的处理方式和资料保管位置"
          />
        </Field>
        <Button
          className="full"
          busy={busy}
          disabled={!reason.trim() || busy || order.status !== "DELIVERING"}
          onClick={async () => {
            setBusy(true);
            if (
              await act(
                `/courier/orders/${order.id}/exception`,
                { expectedVersion: order.version, type, reason },
                (s) => ({
                  ...s,
                  orders: s.orders.map((o) =>
                    o.id === order.id
                      ? { ...o, status: "DELIVERY_EXCEPTION" }
                      : o,
                  ),
                  exceptions: [
                    {
                      id: crypto.randomUUID(),
                      orderId: order.id,
                      type,
                      reason,
                      status: "OPEN",
                      time: now(),
                    },
                    ...s.exceptions,
                  ],
                }),
              )
            ) {
              toast("异常已记录，等待管理员处理");
              nav(`/courier/orders/${order.id}`);
            }
            setBusy(false);
          }}
        >
          <TriangleAlert size={18} />
          提交异常，交由管理员处理
        </Button>
      </Panel>
    </>
  );
}
export function CourierProfile() {
  const { data, role, setRole, setDraft } = useStore();
  const nav = useNavigate();
  return (
    <>
      <PageHeading title="我的账户" />
      <Panel>
        <div className="profile-hero">
          <span className="profile-avatar">
            <User size={39} />
          </span>
          <div>
            <h2>{role === "ADMIN" ? "管理员" : "李同学"}</h2>
            <p>
              {role === "ADMIN"
                ? "管理员 · 可处理全部配送任务"
                : "配送员 · 校园打印工作室"}
            </p>
          </div>
        </div>
        <div className="fixed-rule">
          <ShieldCheck size={20} />
          <span>仅查看和交付分配给自己的订单</span>
        </div>
        <div className="fixed-rule">
          <Camera size={20} />
          <span>照片与定位分别记录，交付结果可追溯</span>
        </div>
      </Panel>
      <Button
        variant="secondary"
        className="full"
        onClick={async () => {
          if (!DEMO) await mutation("/auth/logout", {});
          setRole(null);
          setDraft([]);
          nav("/courier/login");
        }}
      >
        <LogOut size={18} />
        退出工作账号
      </Button>
      <p className="profile-version">DormPrint 配送端 · V1.0</p>
    </>
  );
}
