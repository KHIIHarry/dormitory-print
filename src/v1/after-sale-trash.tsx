import { useState } from "react";
import { Trash2 } from "lucide-react";
import { Button, Field, Modal, Notice } from "../components/ui";
import { useSubmit } from "../admin/growth/common";
import { growthRequest } from "../growth/client";
import { ApiError } from "../lib/api";
import { trashBlockers } from "../lib/trash-blockers";
import type { AfterSaleCase } from "./admin-api";

const ENDED_STATUSES = new Set([
  "COMPLETED",
  "CLOSED",
  "REJECTED",
  "CANCELLED",
  "CANCELED",
  "REFUNDED",
]);

export function AfterSaleDeleteButton({
  item,
  onClick,
}: {
  item: AfterSaleCase;
  onClick: () => void;
}) {
  const ended = ENDED_STATUSES.has(item.status);
  return (
    <div>
      <Button variant="danger" disabled={!ended} onClick={onClick}>
        <Trash2 size={15} />
        删除售后单
      </Button>
      {!ended && <small className="muted">请先结束售后处理</small>}
    </div>
  );
}

export function AfterSaleTrashModal({
  item,
  onClose,
  onDeleted,
  onReload,
}: {
  item: AfterSaleCase;
  onClose: () => void;
  onDeleted: () => void;
  onReload: () => void;
}) {
  const submit = useSubmit();
  const [reason, setReason] = useState("");
  const [failure, setFailure] = useState<unknown>(null);
  const blockers = trashBlockers(failure);
  const blocked =
    failure instanceof ApiError && failure.code === "TRASH_BLOCKED";
  const conflict =
    failure instanceof ApiError && failure.code === "VERSION_CONFLICT";
  const message = blocked
    ? "暂时无法删除，请先处理以下未结业务。"
    : conflict
      ? "售后单已更新，请重新加载售后单并核对后再删除。"
      : submit.error;
  const save = async () => {
    if (!reason.trim() || !ENDED_STATUSES.has(item.status)) return;
    setFailure(null);
    const result = await submit.run(async () => {
      try {
        return await growthRequest("/admin/trash", {
          method: "POST",
          body: {
            targetType: "AFTER_SALE",
            targetId: item.id,
            expectedVersion: item.version,
            reason: reason.trim(),
          },
        });
      } catch (error) {
        setFailure(error);
        throw error;
      }
    }, "售后单已移入 24 小时回收站");
    if (result.ok) onDeleted();
  };
  return (
    <Modal title="删除售后单" onClose={() => !submit.pending && onClose()}>
      <form
        className="form-stack"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <Notice type="warning">
          售后单（{item.orderCode || item.orderId}）将移入回收站，24
          小时内可恢复，到期自动清理且无法恢复。退款记录与订单财务记录保留。
        </Notice>
        <Field label="删除原因" hint="填写业务原因，将写入操作审计。">
          <textarea
            rows={3}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            maxLength={500}
            required
            disabled={submit.pending}
          />
        </Field>
        {!submit.online && (
          <Notice type="warning">当前离线，请连接网络后再删除。</Notice>
        )}
        {message && (
          <Notice type="error">
            <div role="alert">
              {message}
              {blockers.length > 0 && (
                <ul>
                  {blockers.map((blocker) => (
                    <li key={blocker.code}>{blocker.label}</li>
                  ))}
                </ul>
              )}
              {blocked && !blockers.length && (
                <p>
                  {failure instanceof Error
                    ? failure.message
                    : "请核对售后状态和相关业务记录。"}
                </p>
              )}
            </div>
            {conflict && (
              <Button
                type="button"
                variant="secondary"
                disabled={submit.pending}
                onClick={onReload}
              >
                重新加载售后单
              </Button>
            )}
          </Notice>
        )}
        <div className="form-actions">
          <Button
            type="button"
            variant="secondary"
            disabled={submit.pending}
            onClick={onClose}
          >
            取消
          </Button>
          <Button
            type="submit"
            variant="danger"
            busy={submit.pending}
            disabled={!reason.trim() || !submit.online || conflict}
          >
            移入回收站
          </Button>
        </div>
      </form>
    </Modal>
  );
}
