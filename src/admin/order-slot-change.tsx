import { useEffect, useRef, useState } from "react";
import { Button, Field, Modal, Notice } from "../components/ui";
import { slotCurrentlySelectable } from "../customer/checkout";
import { growthRequest } from "../growth/client";
import { isUncertainSubmission } from "../lib/api";
import { slotsForCampus } from "../lib/campus";
import type { Slot } from "../lib/types";
import { slotChangePayload, type AdminOrderView } from "../v1/admin-api";

export function OrderSlotChangeDialog({
  order,
  slots,
  campusId,
  onClose,
  onSaved,
}: {
  order: AdminOrderView;
  slots: Slot[];
  campusId?: string;
  onClose: () => void;
  onSaved: (order: AdminOrderView) => void;
}) {
  return (
    <Modal title="调整配送时段" onClose={onClose}>
      <OrderSlotChangeForm
        order={order}
        slots={slots}
        campusId={campusId}
        onClose={onClose}
        onSaved={onSaved}
      />
    </Modal>
  );
}

export function OrderSlotChangeForm({
  order,
  slots,
  campusId,
  onClose,
  onSaved,
}: {
  order: AdminOrderView;
  slots: Slot[];
  campusId?: string;
  onClose: () => void;
  onSaved: (order: AdminOrderView) => void;
}) {
  const [slotId, setSlotId] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const actionsRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (error) actionsRef.current?.scrollIntoView({ block: "nearest" });
  }, [error]);

  const legacy = order.slotProposal || order.arrangement?.pendingProposal;
  const currentId =
    order.confirmedSlotId ||
    (order.slotProposal?.confirmed
      ? order.slotProposal.slotId
      : order.requestedSlotId || order.slotId);
  const canKeep = !!(legacy && currentId);
  const available = slotsForCampus(slots, campusId).filter(
    (slot) => slot.id !== currentId && slotCurrentlySelectable(slot),
  );
  const unavailableSelection =
    slotId &&
    slotId !== currentId &&
    !available.some((slot) => slot.id === slotId);

  const submit = async () => {
    if (busy) return;
    if (
      !(canKeep && slotId === currentId) &&
      !available.some(
        (slot) => slot.id === slotId && slotCurrentlySelectable(slot),
      )
    ) {
      setError("请选择该订单校区可预约的时段");
      return;
    }
    setBusy(true);
    setError("");
    let updated: AdminOrderView;
    try {
      const body = slotChangePayload(order, slotId, reason);
      updated = await growthRequest<AdminOrderView>(
        `/admin/orders/${encodeURIComponent(order.id)}/slot-change`,
        { method: "POST", body },
      );
    } catch (cause) {
      setError(
        isUncertainSubmission(cause)
          ? "提交结果待核实，请刷新订单确认配送时段后再操作。"
          : (cause as Error).message,
      );
      return;
    } finally {
      setBusy(false);
    }
    onSaved(updated);
  };

  return (
    <form
      className="form-stack"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <p className="form-hint">
        请先在微信与客户确认。保存后立即更新配送时段。
        {order.status === "PENDING_CONFIRMATION"
          ? "结算时才占用名额。"
          : "已结算金额保持原样。"}
      </p>
      {legacy && (
        <Notice>有一条旧改期建议。请核对微信沟通结果后选择时段保存。</Notice>
      )}
      <Field label="配送时段">
        <select
          value={slotId}
          onChange={(event) => setSlotId(event.target.value)}
        >
          <option value="">请选择配送时段</option>
          {canKeep && <option value={currentId}>保留当前时段</option>}
          {unavailableSelection && (
            <option value={slotId} disabled>
              {slots.find((slot) => slot.id === slotId)?.name || "所选时段"}
              （当前不可预约）
            </option>
          )}
          {available.map((slot) => (
            <option key={slot.id} value={slot.id}>
              {slot.date} · {slot.start}–{slot.end} · {slot.name}
            </option>
          ))}
        </select>
      </Field>
      {!available.length && !canKeep && (
        <p className="form-hint">该订单校区暂无其他可预约时段。</p>
      )}
      <Field label="调整原因">
        <textarea
          rows={3}
          maxLength={300}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
      </Field>
      <div ref={actionsRef}>
        {error && (
          <Notice type="error">
            <span role="alert">{error}</span>
          </Notice>
        )}
        <div className="order-slot-change-actions">
          <Button
            type="button"
            variant="secondary"
            disabled={busy}
            onClick={onClose}
          >
            取消
          </Button>
          <Button
            type="submit"
            busy={busy}
            disabled={!available.length && !canKeep}
          >
            保存配送时段
          </Button>
        </div>
      </div>
    </form>
  );
}
