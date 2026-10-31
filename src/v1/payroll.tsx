import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Plus,
  RefreshCw,
  Wallet,
} from "lucide-react";

import {
  Badge,
  Button,
  Empty,
  Field,
  Modal,
  Notice,
  PageHeading,
  Panel,
} from "../components/ui";
import {
  AccessState,
  ResourceState,
  SubmitState,
  errorText,
  useAdminAccess,
  useSubmit,
} from "../admin/growth/common";
import { growthRequest, useGrowthResource } from "../growth/client";
import { money } from "../lib/api";
import {
  PAYROLL_PATHS,
  applyPolicyPayload,
  entryFlags,
  entryOriginalDeliveryPeriod,
  manualEntryPayload,
  policyModeLabel,
  policyPayload,
  payrollHundredths,
  settlementPayload,
  settlementSelection,
  shanghaiPeriod,
  suggestedSettlementEntries,
  wageKindLabel,
  type AdminLedger,
  type LedgerSummary,
  type NeedsConfigurationRow,
  type PayrollPolicy,
  type PayrollPolicyMode,
  type PayrollStaffOption,
  type PolicyCatalog,
  type Settlement,
  type StaffLedger,
  type WageEntry,
} from "./payroll-api";
import "./payroll.css";

function timestamp(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const SOURCE_PREFIX_LABEL: Record<string, string> = {
  DELIVERY: "配送",
  REFUND: "退款",
  SUPPLEMENTARY: "补费",
  MANUAL: "手工",
};

function sourceRef(sourceKey: string): string {
  const [prefix, ref] = sourceKey.split(":");
  if (!ref) return sourceKey;
  const short = ref.slice(0, 8);
  const label = SOURCE_PREFIX_LABEL[prefix];
  return label ? `${label} · ${short}` : `#${short}`;
}

function SummaryCards({ summary }: { summary: LedgerSummary }) {
  return (
    <div className="payroll-summary-grid">
      <div className="payroll-summary-card">
        <span>本期净额</span>
        <strong>{money(summary.netCents)}</strong>
        <small>
          {summary.entryCount} 条 · 正向 {money(summary.grossCents)} / 冲减{" "}
          {money(summary.deductionsCents)}
        </small>
      </div>
      <div className="payroll-summary-card">
        <span>未发放</span>
        <strong>{money(summary.unpaidCents)}</strong>
        <small>历史结转 {money(summary.carryForwardCents)}</small>
      </div>
      <div className="payroll-summary-card">
        <span>可发放余额</span>
        <strong>{money(summary.availableCents)}</strong>
        <small>已发放 {money(summary.paidCents)}</small>
      </div>
      <div className="payroll-summary-card">
        <span>待配置</span>
        <strong>{summary.needsConfigurationCount}</strong>
        <small>交付成功但工资策略缺失</small>
      </div>
    </div>
  );
}

function EntryTable({
  entries,
  emptyText = "当前月份没有工资条目。",
}: {
  entries: WageEntry[];
  emptyText?: string;
}) {
  if (!entries.length) return <Empty title="暂无工资条目" text={emptyText} />;
  return (
    <div className="payroll-table-wrap">
      <table className="payroll-table">
        <thead>
          <tr>
            <th>类型</th>
            <th>关联订单</th>
            <th>月份</th>
            <th className="num">金额</th>
            <th>状态</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => {
            const flags = entryFlags(entry);
            const originalPeriod = entryOriginalDeliveryPeriod(entry);
            return (
              <tr key={entry.id}>
                <td>
                  <strong>{wageKindLabel(entry.kind)}</strong>
                  <small className="payroll-mono">{entry.sourceKey}</small>
                </td>
                <td>
                  <span
                    className="payroll-mono"
                    title={entry.orderId || undefined}
                  >
                    {entry.orderId || "—"}
                  </span>
                </td>
                <td>
                  {entry.period}
                  {originalPeriod && originalPeriod !== entry.period && (
                    <small className="payroll-mono">
                      原交付 {originalPeriod}
                    </small>
                  )}
                </td>
                <td
                  className={`num ${entry.amountCents < 0 ? "payroll-negative" : ""}`}
                >
                  {money(entry.amountCents)}
                </td>
                <td>
                  <span className="payroll-flags">
                    {flags.map((flag) => (
                      <span key={flag} className="payroll-flag">
                        {flag}
                      </span>
                    ))}
                  </span>
                </td>
                <td>
                  {entry.reason || "—"}
                  {entry.settledAt && (
                    <small className="payroll-mono">
                      发放 {timestamp(entry.settledAt)}
                    </small>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PolicyEditor({
  title,
  description,
  policy,
  onSave,
  staffId,
}: {
  title: string;
  description: string;
  policy: PayrollPolicy | null;
  staffId?: string;
  onSave: (body: Record<string, unknown>) => Promise<unknown>;
}) {
  const submit = useSubmit();
  const [mode, setMode] = useState<PayrollPolicyMode>(policy?.mode || "FIXED");
  const [fixedCents, setFixedCents] = useState(
    String((policy?.fixedCents ?? 0) / 100),
  );
  const [rateBps, setRateBps] = useState(String((policy?.rateBps ?? 0) / 100));
  const [enabled, setEnabled] = useState(policy?.enabled ?? true);
  const [reason, setReason] = useState("");
  const [localError, setLocalError] = useState("");

  useEffect(() => {
    setMode(policy?.mode || "FIXED");
    setFixedCents(String((policy?.fixedCents ?? 0) / 100));
    setRateBps(String((policy?.rateBps ?? 0) / 100));
    setEnabled(policy?.enabled ?? true);
  }, [
    policy?.id,
    policy?.version,
    policy?.mode,
    policy?.fixedCents,
    policy?.rateBps,
    policy?.enabled,
  ]);

  async function save(event: FormEvent) {
    event.preventDefault();
    setLocalError("");
    let body: Record<string, unknown>;
    try {
      body = policyPayload({
        mode,
        fixedCents:
          mode === "FIXED" ? payrollHundredths(fixedCents, "每单工资") : 0,
        rateBps:
          mode === "PERCENT"
            ? payrollHundredths(rateBps, "计薪比例", 10000)
            : 0,
        enabled,
        expectedVersion: policy?.version,
        reason,
      });
    } catch (error) {
      setLocalError(errorText(error));
      return;
    }
    const result = await submit.run(
      () => onSave(body),
      policy ? "工资策略已更新" : "工资策略已创建",
    );
    if (result.ok) setReason("");
  }

  return (
    <Panel
      title={title}
      action={
        <Badge
          status={policy ? (policy.enabled ? "生效中" : "已停用") : "未配置"}
        />
      }
    >
      <p className="payroll-muted">{description}</p>
      {policy && (
        <p className="payroll-muted">
          {policy.scopeKey} · {policyModeLabel(policy.mode)} · 更新于 {timestamp(policy.updatedAt)}
        </p>
      )}
      <form className="payroll-policy-form" onSubmit={save}>
        <div className="payroll-policy-grid">
          <Field label="计薪方式">
            <select
              value={mode}
              onChange={(event) =>
                setMode(event.target.value as PayrollPolicyMode)
              }
            >
              <option value="FIXED">每单固定金额</option>
              <option value="PERCENT">按实际净收款比例</option>
            </select>
          </Field>
          {mode === "FIXED" ? (
            <Field
              label="每单工资（元）"
              hint="每笔成功交付的订单按此金额计薪。"
            >
              <input
                type="number"
                min={0}
                step="0.01"
                value={fixedCents}
                onChange={(event) => setFixedCents(event.target.value)}
              />
            </Field>
          ) : (
            <Field label="计薪比例（%）" hint="按订单实际净收款计算，0–100%。">
              <input
                type="number"
                min={0}
                max={100}
                step="0.01"
                value={rateBps}
                onChange={(event) => setRateBps(event.target.value)}
              />
            </Field>
          )}
        </div>
        <Field label="变更原因（选填）">
          <input
            value={reason}
            maxLength={500}
            placeholder="例如：调整本月配送计薪标准"
            onChange={(event) => setReason(event.target.value)}
          />
        </Field>
        <label className="payroll-check">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => setEnabled(event.target.checked)}
          />
          启用该策略
        </label>
        {localError && <Notice type="error">{localError}</Notice>}
        <SubmitState state={submit} />
        <div className="payroll-inline">
          <Button type="submit" busy={submit.pending}>
            保存策略
          </Button>
          {staffId && (
            <small className="payroll-muted">员工 ID：{staffId}</small>
          )}
        </div>
      </form>
    </Panel>
  );
}

function ManualEntryModal({
  staffOptions,
  defaultPeriod,
  onClose,
  onSaved,
}: {
  staffOptions: PayrollStaffOption[];
  defaultPeriod: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const submit = useSubmit();
  const [staffId, setStaffId] = useState(staffOptions[0]?.id || "");
  const [orderId, setOrderId] = useState("");
  const [amountCents, setAmountCents] = useState("");
  const [period, setPeriod] = useState(defaultPeriod);
  const [reason, setReason] = useState("");
  const [localError, setLocalError] = useState("");

  async function save(event: FormEvent) {
    event.preventDefault();
    setLocalError("");
    let body: Record<string, unknown>;
    try {
      body = manualEntryPayload({
        staffId,
        orderId,
        amountCents,
        period,
        reason,
      });
    } catch (error) {
      setLocalError(errorText(error));
      return;
    }
    const result = await submit.run(
      () =>
        growthRequest<WageEntry>(PAYROLL_PATHS.entries, {
          method: "POST",
          body,
        }),
      "人工工资条目已创建",
    );
    if (result.ok) {
      onClose();
      onSaved();
    }
  }

  return (
    <Modal title="新增人工工资条目" onClose={onClose}>
      <p className="payroll-muted">
        人工条目必须填写原因，不会修改原交付的骑手归属；负数用于补扣或冲减。
      </p>
      <form className="payroll-form" onSubmit={save}>
        <Field label="员工 ID">
          <input
            list="payroll-manual-staff-options"
            value={staffId}
            required
            placeholder="选择或输入员工 ID"
            onChange={(event) => setStaffId(event.target.value)}
          />
          <datalist id="payroll-manual-staff-options">
            {staffOptions.map((option) => (
              <option key={option.id} value={option.id}>
                {option.name}
              </option>
            ))}
          </datalist>
        </Field>
        <Field label="关联订单 ID（可选）">
          <input
            value={orderId}
            placeholder="关联订单用于保留归属线索"
            onChange={(event) => setOrderId(event.target.value)}
          />
        </Field>
        <Field label="金额（分）" hint="正数补发，负数冲减；只支持整数分">
          <input
            type="number"
            step={1}
            value={amountCents}
            required
            onChange={(event) => setAmountCents(event.target.value)}
          />
        </Field>
        <Field label="工资月份">
          <input
            type="month"
            value={period}
            required
            onChange={(event) => setPeriod(event.target.value)}
          />
        </Field>
        <Field label="原因">
          <textarea
            value={reason}
            required
            maxLength={500}
            placeholder="例如：临时补发搬运补贴"
            onChange={(event) => setReason(event.target.value)}
          />
        </Field>
        {localError && <Notice type="error">{localError}</Notice>}
        <SubmitState state={submit} />
        <div className="payroll-inline payroll-end">
          <Button type="button" variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button type="submit" busy={submit.pending}>
            创建条目
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function ApplyPolicyModal({
  target,
  catalog,
  onClose,
  onSaved,
}: {
  target: NeedsConfigurationRow;
  catalog: PolicyCatalog | null;
  onClose: () => void;
  onSaved: (target: NeedsConfigurationRow) => void;
}) {
  const submit = useSubmit();
  const policies = [
    ...(catalog?.defaultPolicy ? [catalog.defaultPolicy] : []),
    ...(catalog?.overrides || []),
  ];
  const [policyId, setPolicyId] = useState(policies[0]?.id || "");
  const [reason, setReason] = useState("");
  const [localError, setLocalError] = useState("");

  async function save(event: FormEvent) {
    event.preventDefault();
    setLocalError("");
    if (!target.orderId) {
      setLocalError("该待配置记录缺少订单，无法补录。");
      return;
    }
    let body: Record<string, unknown>;
    try {
      body = applyPolicyPayload(policyId, reason);
    } catch (error) {
      setLocalError(errorText(error));
      return;
    }
    const result = await submit.run(
      () =>
        growthRequest<WageEntry>(
          PAYROLL_PATHS.applyPolicy(target.orderId as string),
          {
            method: "POST",
            body,
          },
        ),
      "工资已补录，请继续在「记录工资发放」中确认发放",
    );
    if (result.ok) {
      onClose();
      onSaved(target);
    }
  }

  return (
    <Modal title="补录待配置工资" onClose={onClose}>
      <p className="payroll-muted">
        订单 {target.orderId || "—"} 于 {timestamp(target.deliveredAt)}{" "}
        交付，当时没有可用策略。补录会冻结所选策略并生成工资条目；补录后还需在「记录工资发放」中确认发放，工资才算结清。
      </p>
      <form className="payroll-form" onSubmit={save}>
        {!policies.length && (
          <Notice type="warning">
            请先配置默认工资策略或该员工的覆盖策略。
          </Notice>
        )}
        <Field label="补录策略">
          <select
            value={policyId}
            required
            onChange={(event) => setPolicyId(event.target.value)}
          >
            <option value="">请选择策略</option>
            {policies.map((policy) => (
              <option key={policy.id} value={policy.id}>
                {policy.staffName || policy.scopeKey} ·{" "}
                {policyModeLabel(policy.mode)} ·{" "}
                {policy.mode === "FIXED"
                  ? money(policy.fixedCents)
                  : `${policy.rateBps} bps`}
              </option>
            ))}
          </select>
        </Field>
        <Field label="补录原因">
          <textarea
            value={reason}
            required
            maxLength={500}
            placeholder="例如：新员工默认策略补录"
            onChange={(event) => setReason(event.target.value)}
          />
        </Field>
        {localError && <Notice type="error">{localError}</Notice>}
        <SubmitState state={submit} />
        <div className="payroll-inline payroll-end">
          <Button type="button" variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button
            type="submit"
            busy={submit.pending}
            disabled={!policies.length}
          >
            确认补录
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function SettlementConfirm({
  staffId,
  staffName,
  period,
  entries,
  netCents,
  onClose,
  onSaved,
}: {
  staffId: string;
  staffName: string;
  period: string;
  entries: WageEntry[];
  netCents: number;
  onClose: () => void;
  onSaved: () => void;
}) {
  const submit = useSubmit();
  const [reason, setReason] = useState("");
  const [localError, setLocalError] = useState("");

  async function save(event: FormEvent) {
    event.preventDefault();
    setLocalError("");
    let body: Record<string, unknown>;
    try {
      body = settlementPayload({
        staffId,
        period,
        entryIds: entries.map((entry) => entry.id),
        reason,
      });
    } catch (error) {
      setLocalError(errorText(error));
      return;
    }
    const result = await submit.run(
      () =>
        growthRequest<Settlement>(PAYROLL_PATHS.settlements(), {
          method: "POST",
          body,
        }),
      "已记录工资发放",
    );
    if (result.ok) {
      onClose();
      onSaved();
    }
  }

  return (
    <Modal title="确认记录发放" onClose={onClose}>
      <p className="payroll-muted">
        为 {staffName} 记录 {period} 工资发放 {money(netCents)}，共{" "}
        {entries.length} 条。 该操作只写入月度发放流水，不会发起银行转账。
      </p>
      <form className="payroll-form" onSubmit={save}>
        <Field label="发放原因">
          <textarea
            value={reason}
            required
            maxLength={500}
            placeholder="例如：2026 年 10 月工资月结"
            onChange={(event) => setReason(event.target.value)}
          />
        </Field>
        {localError && <Notice type="error">{localError}</Notice>}
        <SubmitState state={submit} />
        <div className="payroll-inline payroll-end">
          <Button type="button" variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button type="submit" busy={submit.pending}>
            确认发放
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function SettlementPanel({
  ledger,
  period,
  onSaved,
}: {
  ledger: StaffLedger;
  period: string;
  onSaved: () => void;
}) {
  const candidates = useMemo(
    () => suggestedSettlementEntries(ledger),
    [ledger],
  );
  const suggestedKey = candidates.map((entry) => entry.id).join("|");
  const [selected, setSelected] = useState<string[]>(() =>
    candidates.map((entry) => entry.id),
  );
  const [confirm, setConfirm] = useState(false);

  useEffect(() => {
    setSelected(suggestedKey ? suggestedKey.split("|") : []);
  }, [suggestedKey]);

  const mandatoryCarry = (entry: WageEntry) =>
    entry.period < period && entry.amountCents < 0;
  const chosen = candidates.filter(
    (entry) => selected.includes(entry.id) || mandatoryCarry(entry),
  );
  const selection = settlementSelection(chosen, period);
  const blocked = Boolean(selection.error);

  if (!candidates.length)
    return (
      <Panel title="记录工资发放">
        <Empty
          title="没有可发放条目"
          text="当前月份没有未发放工资，或所有条目已发放。历史未发放调整可在后续月份合并发放。"
        />
      </Panel>
    );

  return (
    <Panel
      title="记录工资发放"
      action={
        <span className="payroll-summary-inline">
          已选 {chosen.length} / {candidates.length} 条 · 净额{" "}
          {money(selection.netCents)}
        </span>
      }
    >
      <div className="payroll-settle-body">
        <div className="payroll-toolbar">
          <p className="payroll-muted">
            只能选择未发放条目；净额必须 ≥
            0。退款/补费冲减记入实际发生月份，历史未发放金额一并列出，历史负向调整必须计入本次净额。
          </p>
          <div className="payroll-inline payroll-toolbar-actions">
            <Button
              type="button"
              variant="secondary"
              className="payroll-compact"
              onClick={() => setSelected(candidates.map((entry) => entry.id))}
            >
              全选可发放
            </Button>
            <Button
              type="button"
              variant="text"
              className="payroll-compact"
              onClick={() => setSelected([])}
            >
              清空
            </Button>
          </div>
        </div>
        <div className="payroll-table-wrap">
          <table className="payroll-table">
            <thead>
              <tr>
                <th className="payroll-select">选择</th>
                <th>类型</th>
                <th>月份</th>
                <th className="num">金额</th>
                <th>来源</th>
              </tr>
            </thead>
            <tbody>
              {candidates.map((entry) => {
                const originalPeriod = entryOriginalDeliveryPeriod(entry);
                return (
                  <tr key={entry.id}>
                    <td className="payroll-select">
                      <input
                        type="checkbox"
                        checked={
                          selected.includes(entry.id) || mandatoryCarry(entry)
                        }
                        disabled={mandatoryCarry(entry)}
                        aria-label={`选择 ${entry.sourceKey}`}
                        onChange={(event) =>
                          setSelected((current) =>
                            event.target.checked
                              ? Array.from(new Set([...current, entry.id]))
                              : current.filter((id) => id !== entry.id),
                          )
                        }
                      />
                    </td>
                    <td>{wageKindLabel(entry.kind)}</td>
                    <td>
                      {entry.period}
                      {originalPeriod && originalPeriod !== entry.period && (
                        <small className="payroll-mono">
                          原交付 {originalPeriod}
                        </small>
                      )}
                    </td>
                    <td
                      className={`num ${entry.amountCents < 0 ? "payroll-negative" : ""}`}
                    >
                      {money(entry.amountCents)}
                    </td>
                    <td>
                      <span className="payroll-source" title={entry.sourceKey}>
                        {sourceRef(entry.sourceKey)}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {blocked && <Notice type="warning">{selection.error}</Notice>}
        <div className="payroll-inline payroll-end">
          <Button
            type="button"
            disabled={blocked}
            onClick={() => setConfirm(true)}
          >
            记录发放
          </Button>
        </div>
      </div>
      {confirm && (
        <SettlementConfirm
          staffId={ledger.staff.id}
          staffName={ledger.staff.name}
          period={period}
          entries={chosen}
          netCents={selection.netCents}
          onClose={() => setConfirm(false)}
          onSaved={onSaved}
        />
      )}
    </Panel>
  );
}

function SettlementHistory({ settlements }: { settlements: Settlement[] }) {
  if (!settlements.length)
    return <Empty title="暂无发放记录" text="记录月度发放后会显示在这里。" />;
  return (
    <div className="payroll-table-wrap">
      <table className="payroll-table">
        <thead>
          <tr>
            <th>发放时间</th>
            <th>月份</th>
            <th className="num">金额</th>
            <th>操作人</th>
            <th>原因</th>
          </tr>
        </thead>
        <tbody>
          {settlements.map((settlement) => (
            <tr key={settlement.id}>
              <td>{timestamp(settlement.paidAt)}</td>
              <td>{settlement.period}</td>
              <td className="num">{money(settlement.amountCents)}</td>
              <td>{settlement.paidByName || settlement.paidBy}</td>
              <td>{settlement.reason || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function AdminPayrollPage({
  staff = [],
}: {
  staff?: PayrollStaffOption[];
}) {
  const access = useAdminAccess("payroll.manage");
  const [period, setPeriod] = useState(shanghaiPeriod());
  const [staffFilter, setStaffFilter] = useState("");
  const [overrideStaffId, setOverrideStaffId] = useState("");
  const [manualOpen, setManualOpen] = useState(false);
  const [applyTarget, setApplyTarget] = useState<NeedsConfigurationRow | null>(
    null,
  );
  const [settleHint, setSettleHint] = useState<{
    staffId: string;
    staffName: string;
    period: string;
  } | null>(null);

  const ledgerPath = PAYROLL_PATHS.ledger({
    period,
    staffId: staffFilter || undefined,
  });
  const ledger = useGrowthResource<AdminLedger>(ledgerPath);
  const staffCatalog = useGrowthResource<PayrollStaffOption[]>(
    PAYROLL_PATHS.staff,
  );
  const catalog = useGrowthResource<PolicyCatalog>(PAYROLL_PATHS.policies);
  const settlements = useGrowthResource<Settlement[]>(
    staffFilter
      ? PAYROLL_PATHS.settlements({ period, staffId: staffFilter })
      : null,
  );

  const staffOptions = useMemo(() => {
    const options = new Map<string, PayrollStaffOption>();
    for (const option of staff) options.set(option.id, option);
    for (const option of staffCatalog.data || [])
      options.set(option.id, option);
    for (const summary of ledger.data?.summaries || [])
      options.set(summary.staffId, {
        id: summary.staffId,
        name: summary.staffName,
      });
    return Array.from(options.values());
  }, [staff, staffCatalog.data, ledger.data?.summaries]);

  const overridePolicy =
    catalog.data?.overrides.find(
      (policy) => policy.staffId === overrideStaffId,
    ) || null;
  const overrideName =
    staffOptions.find((option) => option.id === overrideStaffId)?.name ||
    overridePolicy?.staffName ||
    "";

  function reloadAll() {
    void ledger.reload();
    void staffCatalog.reload();
    void catalog.reload();
    if (staffFilter) void settlements.reload();
  }

  if (access.loading || access.error || !access.allowed)
    return (
      <div className="admin-growth v1-admin payroll-page">
        <PageHeading
          title="工资管理"
          description="配置计薪策略、核对交付工资、记录人工调整与月度发放。"
        />
        <AccessState access={access} />
      </div>
    );

  const data = ledger.data;

  return (
    <div className="admin-growth v1-admin payroll-page">
      <PageHeading
        title="工资管理"
        description="交付成功后按冻结策略自动记账；退款、补费与人工调整都以追加条目留痕。"
        action={
          <div className="payroll-inline">
            <Button type="button" variant="secondary" onClick={reloadAll}>
              <RefreshCw size={15} /> 刷新
            </Button>
            <Button type="button" onClick={() => setManualOpen(true)}>
              <Plus size={15} /> 人工条目
            </Button>
          </div>
        }
      />

      <Panel className="payroll-filter-panel" title="账本筛选">
        {staffCatalog.error && (
          <Notice type="warning">
            员工目录加载失败，暂时只能选择账本中出现的员工；请确认当前账号拥有工资管理权限。
          </Notice>
        )}
        <div className="payroll-filter-grid">
          <Field label="工资月份" hint="按中国时区自然月">
            <input
              type="month"
              value={period}
              onChange={(event) => {
                setPeriod(event.target.value || shanghaiPeriod());
                setSettleHint(null);
              }}
            />
          </Field>
          <Field
            label="员工筛选（可选）"
            hint="选择员工可查看个人账本并记录工资发放"
          >
            <select
              value={staffFilter}
              onChange={(event) => {
                setStaffFilter(event.target.value);
                setSettleHint(null);
              }}
            >
              <option value="">全部员工</option>
              {staffOptions.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.name || option.id}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </Panel>

      {settleHint && (
        <Notice type="success">
          <div>
            已为 {settleHint.staffName} 补录 {settleHint.period}{" "}
            的交付工资。补录只生成工资条目，还需在下方「记录工资发放」确认发放，订单/批次才能删除。
          </div>
          <div>
            <button
              type="button"
              className="payroll-link"
              onClick={() =>
                document
                  .getElementById("payroll-settlement")
                  ?.scrollIntoView({ behavior: "smooth", block: "start" })
              }
            >
              去记录工资发放
            </button>
          </div>
        </Notice>
      )}

      <ResourceState
        loading={ledger.loading}
        error={ledger.error}
        hasData={ledger.hasData}
        refreshing={ledger.refreshing}
        reload={ledger.reload}
        empty={!data || (!data.entries.length && !data.summaries.length)}
        variant="cards"
      />

      {data && (
        <>
          <SummaryCards
            summary={{
              staffId: staffFilter,
              staffName: staffFilter
                ? data.summaries[0]?.staffName || staffFilter
                : "全部员工",
              entryCount: data.entries.length,
              grossCents: data.entries
                .filter((entry) => entry.amountCents > 0)
                .reduce((sum, entry) => sum + entry.amountCents, 0),
              deductionsCents: data.entries
                .filter((entry) => entry.amountCents < 0)
                .reduce((sum, entry) => sum + entry.amountCents, 0),
              netCents: data.summaries.reduce(
                (sum, row) => sum + row.netCents,
                0,
              ),
              paidCents: data.summaries.reduce(
                (sum, row) => sum + row.paidCents,
                0,
              ),
              unpaidCents: data.summaries.reduce(
                (sum, row) => sum + row.unpaidCents,
                0,
              ),
              carryForwardCents: data.summaries.reduce(
                (sum, row) => sum + row.carryForwardCents,
                0,
              ),
              availableCents: data.summaries.reduce(
                (sum, row) => sum + row.availableCents,
                0,
              ),
              needsConfigurationCount: data.needsConfiguration.length,
            }}
          />

          {data.summaries.length > 0 && (
            <Panel title="员工汇总">
              <div className="payroll-table-wrap">
                <table className="payroll-table">
                  <thead>
                    <tr>
                      <th>员工</th>
                      <th className="num">本期净额</th>
                      <th className="num">未发放</th>
                      <th className="num">历史结转</th>
                      <th className="num">可发放</th>
                      <th className="num">待配置</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.summaries.map((row) => (
                      <tr key={row.staffId}>
                        <td>
                          <button
                            type="button"
                            className="payroll-link"
                            onClick={() => setStaffFilter(row.staffId)}
                          >
                            {row.staffName || row.staffId}
                          </button>
                          <small className="payroll-mono">{row.staffId}</small>
                        </td>
                        <td className="num">{money(row.netCents)}</td>
                        <td className="num">{money(row.unpaidCents)}</td>
                        <td
                          className={`num ${row.carryForwardCents < 0 ? "payroll-negative" : ""}`}
                        >
                          {money(row.carryForwardCents)}
                        </td>
                        <td className="num">{money(row.availableCents)}</td>
                        <td className="num">{row.needsConfigurationCount}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
          )}

          <Panel title="工资明细">
            <EntryTable entries={data.entries} />
          </Panel>

          {data.needsConfiguration.length > 0 && (
            <Panel
              title={`待配置交付（${data.needsConfiguration.length}）`}
              action={<AlertTriangle size={18} />}
            >
              <p className="payroll-muted">
                这些订单已成功交付，但当时没有可用策略，因此没有生成金额。补录需要明确选择策略并填写原因；补录只生成工资条目，还需在「记录工资发放」中确认发放，订单/批次才能删除。
              </p>
              <div className="payroll-table-wrap">
                <table className="payroll-table">
                  <thead>
                    <tr>
                      <th>订单</th>
                      <th>员工</th>
                      <th>交付时间</th>
                      <th>月份</th>
                      <th className="payroll-actions">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.needsConfiguration.map((row) => (
                      <tr key={row.entryId}>
                        <td>
                          <span
                            className="payroll-mono"
                            title={row.orderId || undefined}
                          >
                            {row.orderId || "—"}
                          </span>
                        </td>
                        <td>{row.staffName || row.staffId}</td>
                        <td>{timestamp(row.deliveredAt)}</td>
                        <td>{row.period}</td>
                        <td className="payroll-actions">
                          <Button
                            type="button"
                            variant="secondary"
                            onClick={() => setApplyTarget(row)}
                          >
                            补录策略
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
          )}

          {staffFilter && ledger.data && (
            <div id="payroll-settlement" className="payroll-settlement-anchor">
              <SettlementPanel
                key={`${staffFilter}:${period}`}
                ledger={{
                  staff: {
                    id: staffFilter,
                    name:
                      data.summaries[0]?.staffName ||
                      staffOptions.find((option) => option.id === staffFilter)
                        ?.name ||
                      staffFilter,
                  },
                  period,
                  summary:
                    data.summaries[0] ||
                    ({
                      staffId: staffFilter,
                      staffName: staffFilter,
                      entryCount: 0,
                      grossCents: 0,
                      deductionsCents: 0,
                      netCents: 0,
                      paidCents: 0,
                      unpaidCents: 0,
                      carryForwardCents: 0,
                      availableCents: 0,
                      needsConfigurationCount: 0,
                    } satisfies LedgerSummary),
                  entries: data.entries.filter(
                    (entry) => entry.staffId === staffFilter,
                  ),
                  carryForward: data.carryForward.filter(
                    (entry) => entry.staffId === staffFilter,
                  ),
                  settlements: settlements.data || [],
                }}
                period={period}
                onSaved={() => {
                  setSettleHint(null);
                  reloadAll();
                }}
              />
            </div>
          )}

          {staffFilter && (
            <Panel
              title={`${staffFilter} 的发放记录`}
              action={<Wallet size={18} />}
            >
              <ResourceState
                loading={settlements.loading}
                error={settlements.error}
                hasData={settlements.hasData}
                refreshing={settlements.refreshing}
                reload={settlements.reload}
                empty={!settlements.data?.length}
              />
              {settlements.data && (
                <SettlementHistory settlements={settlements.data} />
              )}
            </Panel>
          )}
        </>
      )}

      <div className="payroll-policy-columns">
        <PolicyEditor
          title="默认工资策略"
          description="未单独配置的员工在交付成功后按此策略计薪；比例模式只计算原订单实际收到的净现金。"
          policy={catalog.data?.defaultPolicy || null}
          onSave={async (body) => {
            const row = await growthRequest<PayrollPolicy>(
              PAYROLL_PATHS.defaultPolicy,
              {
                method: "PUT",
                body,
              },
            );
            void catalog.reload();
            return row;
          }}
        />
        <Panel title="员工覆盖策略">
          <p className="payroll-muted">
            覆盖策略优先于默认策略。员工专属策略只影响该员工后续交付，历史条目保留当时冻结的策略。
          </p>
          <Field label="员工">
            <input
              list="payroll-override-staff-options"
              value={overrideStaffId}
              placeholder="输入员工 ID 选择覆盖对象"
              onChange={(event) =>
                setOverrideStaffId(event.target.value.trim())
              }
            />
            <datalist id="payroll-override-staff-options">
              {staffOptions.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.name}
                </option>
              ))}
            </datalist>
          </Field>
          {overrideStaffId ? (
            <PolicyEditor
              key={overrideStaffId}
              title={`覆盖策略${overrideName ? ` · ${overrideName}` : ""}`}
              description="该策略仅对所选员工生效，停用后回落到默认策略。"
              policy={overridePolicy}
              staffId={overrideStaffId}
              onSave={async (body) => {
                const row = await growthRequest<PayrollPolicy>(
                  PAYROLL_PATHS.staffPolicy(overrideStaffId),
                  { method: "PUT", body },
                );
                void catalog.reload();
                return row;
              }}
            />
          ) : (
            <Empty
              title="选择员工"
              text="选择员工后可查看或配置专属覆盖策略。"
            />
          )}
        </Panel>
      </div>

      {manualOpen && (
        <ManualEntryModal
          staffOptions={staffOptions}
          defaultPeriod={period}
          onClose={() => setManualOpen(false)}
          onSaved={reloadAll}
        />
      )}
      {applyTarget && (
        <ApplyPolicyModal
          target={applyTarget}
          catalog={catalog.data || null}
          onClose={() => setApplyTarget(null)}
          onSaved={(target) => {
            setStaffFilter(target.staffId);
            setPeriod(target.period);
            setSettleHint({
              staffId: target.staffId,
              staffName: target.staffName || target.staffId,
              period: target.period,
            });
            void catalog.reload();
          }}
        />
      )}
    </div>
  );
}

export function StaffPayrollPanel() {
  const [period, setPeriod] = useState(shanghaiPeriod());
  const ledger = useGrowthResource<StaffLedger>(PAYROLL_PATHS.me(period));
  const data = ledger.data;

  return (
    <div className="payroll-page payroll-employee">
      <PageHeading
        title="我的工资"
        description="仅显示你本人的工资明细与发放记录；金额以分为单位计算。"
        action={
          <Button
            type="button"
            variant="secondary"
            onClick={() => void ledger.reload()}
          >
            <RefreshCw size={15} /> 刷新
          </Button>
        }
      />

      <Panel className="payroll-filter-panel">
        <div className="payroll-filter-grid">
          <Field label="工资月份">
            <input
              type="month"
              value={period}
              onChange={(event) =>
                setPeriod(event.target.value || shanghaiPeriod())
              }
            />
          </Field>
        </div>
      </Panel>

      <ResourceState
        loading={ledger.loading}
        error={ledger.error}
        hasData={ledger.hasData}
        refreshing={ledger.refreshing}
        reload={ledger.reload}
        empty={
          !data ||
          (!data.entries.length &&
            !data.settlements.length &&
            !data.carryForward.length)
        }
        variant="cards"
      />

      {data && (
        <>
          {data.staff?.name && (
            <p className="payroll-muted">
              <CheckCircle2 size={15} /> {data.staff.name} · {data.period}
            </p>
          )}
          <SummaryCards summary={data.summary} />

          {data.carryForward.length > 0 && (
            <Panel title="历史未发放结转">
              <p className="payroll-muted">
                以下条目来自此前月份，仍可在后续发放时合并抵扣。
              </p>
              <EntryTable entries={data.carryForward} />
            </Panel>
          )}

          <Panel title={`${data.period} 工资明细`}>
            <EntryTable entries={data.entries} />
          </Panel>

          <Panel title="发放记录">
            <SettlementHistory settlements={data.settlements} />
          </Panel>

          <Notice>
            工资记录只用于内部核算和人工发放留痕，不会自动发起银行转账。
          </Notice>
        </>
      )}
    </div>
  );
}
