import { useState, type FormEvent } from "react";
import {
  Button,
  Field,
  Modal,
  Notice,
  PageHeading,
  Panel,
} from "../../components/ui";
import { useGrowthResource } from "../../growth/client";
import { DEMO } from "../../lib/api";
import type { Campaign, PromotionRecord } from "../../growth/types";
import {
  AccessState,
  ConfirmAction,
  ResourceState,
  SubmitState,
  mutate,
  useAdminAccess,
  useSubmit,
} from "./common";
import {
  dateInput,
  isoDate,
  localDate,
  parseCents,
  positiveInteger,
  safeExportUrl,
  yuan,
} from "./logic";
import { exportReady } from "../../courier/task-state";
const kinds: Record<Campaign["kind"], string> = {
  NEW_CUSTOMER: "新客注册券",
  INVITEE: "受邀新人券",
  REFERRER: "邀请人奖励",
};
const services = { A4: "A4 打印", PHOTO: "照片", ALL: "全部可优惠商品" };
type CampaignDraft = {
  id?: string;
  version?: number;
  name: string;
  kind: Campaign["kind"];
  enabled: boolean;
  amount: string;
  minimum: string;
  service: Campaign["service"];
  validity: string;
  budget: string;
  issuance: string;
  perAccount: string;
  starts: string;
  ends: string;
  description: string;
  qualificationDeadline: string;
  qualificationMinimum: string;
  qualificationService: Campaign["service"];
};
const newCampaign = (): CampaignDraft => ({
  name: "",
  kind: "NEW_CUSTOMER",
  enabled: false,
  amount: "",
  minimum: "0",
  service: "ALL",
  validity: "",
  budget: "",
  issuance: "",
  perAccount: "1",
  starts: "",
  ends: "",
  description: "",
  qualificationDeadline: "",
  qualificationMinimum: "0",
  qualificationService: "ALL",
});
function campaignDraft(c: Campaign): CampaignDraft {
  return {
    id: c.id,
    version: c.version,
    name: c.name,
    kind: c.kind,
    enabled: c.enabled,
    amount: (c.amountCents / 100).toFixed(2),
    minimum: (c.minSpendCents / 100).toFixed(2),
    service: c.service,
    validity: c.validityDays == null ? "" : String(c.validityDays),
    budget: c.budgetCents == null ? "" : (c.budgetCents / 100).toFixed(2),
    issuance: c.issuanceLimit == null ? "" : String(c.issuanceLimit),
    perAccount: String(c.perAccountLimit),
    starts: dateInput(c.startsAt),
    ends: dateInput(c.endsAt),
    description: c.description,
    qualificationDeadline: dateInput(c.qualificationExpiresAt),
    qualificationMinimum: (c.qualificationMinSpendCents / 100).toFixed(2),
    qualificationService: c.qualificationService,
  };
}
function campaignBody(d: CampaignDraft) {
  if (!d.name.trim()) throw new Error("请填写活动名称");
  const amountCents = parseCents(d.amount);
  if (amountCents < 1) throw new Error("券面额必须大于 0 分");
  if (d.starts && d.ends && new Date(d.starts) >= new Date(d.ends))
    throw new Error("结束时间必须晚于开始时间");
  return {
    name: d.name.trim(),
    kind: d.kind,
    enabled: d.enabled,
    amountCents,
    minSpendCents: parseCents(d.minimum),
    service: d.service,
    validityDays: d.validity ? positiveInteger(d.validity, "有效天数") : null,
    budgetCents: d.budget ? parseCents(d.budget) : null,
    issuanceLimit: d.issuance ? positiveInteger(d.issuance, "发行限额") : null,
    perAccountLimit: positiveInteger(d.perAccount, "每账号次数"),
    startsAt: isoDate(d.starts),
    endsAt: isoDate(d.ends),
    description: d.description,
    stackable: false,
    qualificationExpiresAt:
      d.kind === "REFERRER" ? isoDate(d.qualificationDeadline) : null,
    qualificationMinSpendCents:
      d.kind === "REFERRER" ? parseCents(d.qualificationMinimum) : 0,
    qualificationService:
      d.kind === "REFERRER" ? d.qualificationService : "ALL",
    ...(d.id ? { expectedVersion: d.version } : {}),
  };
}
type PromotionStats = {
  issuedCount: number;
  reservedCount: number;
  usedCount: number;
  expiredCount: number;
  couponDiscountCents: number;
  newCustomers?: number;
  verifiedCustomers?: number;
  volumeDiscountCents?: number;
};
export function PromotionSettingsPage() {
  const access = useAdminAccess("promotion.manage"),
    resource = useGrowthResource<{ items: Campaign[]; stats?: PromotionStats }>(
      "/admin/promotions/campaigns",
    ),
    submit = useSubmit();
  const [draft, setDraft] = useState<CampaignDraft | null>(null),
    [confirm, setConfirm] = useState(false);
  const patch = (change: Partial<CampaignDraft>) =>
    setDraft((d) => (d ? { ...d, ...change } : d));
  const disabled = !access.allowed || submit.pending || !submit.online;
  return (
    <div className="admin-growth">
      <PageHeading
        title="营销活动"
        description="新客注册、受邀新人和邀请人奖励均按固定金额配置。"
        action={
          <Button disabled={disabled} onClick={() => setDraft(newCampaign())}>
            新增活动
          </Button>
        }
      />
      <AccessState access={access} />
      <ResourceState {...resource} empty={resource.data?.items.length === 0} />
      <SubmitState state={submit} />
      <Notice>
        每笔订单最多使用一张券；首版不叠加，系统自动比较可用券与 A4
        满量优惠。活动变更只影响新的资格及报价，不改写既有收款事实。
      </Notice>
      {resource.data?.stats && (
        <Panel title="服务端运营统计">
          <dl className="growth-facts">
            <div>
              <dt>发行 / 锁定 / 已核销 / 失效</dt>
              <dd>
                {resource.data.stats.issuedCount} /{" "}
                {resource.data.stats.reservedCount} /{" "}
                {resource.data.stats.usedCount} /{" "}
                {resource.data.stats.expiredCount}
              </dd>
            </div>
            <div>
              <dt>优惠券已让利金额</dt>
              <dd>{yuan(resource.data.stats.couponDiscountCents)}</dd>
            </div>
            {resource.data.stats.newCustomers !== undefined && (
              <div>
                <dt>新注册 / 已验证客户</dt>
                <dd>
                  {resource.data.stats.newCustomers} /{" "}
                  {resource.data.stats.verifiedCustomers ?? "—"}
                </dd>
              </div>
            )}
            {resource.data.stats.volumeDiscountCents !== undefined && (
              <div>
                <dt>A4 满量已让利金额</dt>
                <dd>{yuan(resource.data.stats.volumeDiscountCents)}</dd>
              </div>
            )}
          </dl>
          <small>以上为服务端统计口径；券预算与满量让利分开核算。</small>
        </Panel>
      )}
      {resource.data &&
        (!resource.error || resource.hasData) &&
        resource.data.items.length > 0 && (
          <Panel>
            <div className="growth-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>活动</th>
                    <th>状态 / 版本</th>
                    <th>固定面额 / 门槛</th>
                    <th>适用 / 预算 / 发行</th>
                    <th>时段</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {resource.data.items.map((c) => (
                    <tr key={c.id}>
                      <td>
                        <b>{c.name}</b>
                        <small>{kinds[c.kind]}</small>
                      </td>
                      <td>
                        {c.enabled ? "发放中" : "已停用"} · v{c.version}
                      </td>
                      <td>
                        {yuan(c.amountCents)} / 满 {yuan(c.minSpendCents)}
                      </td>
                      <td>
                        {services[c.service]}
                        <small>
                          预算{" "}
                          {c.budgetCents == null
                            ? "未设置上限"
                            : yuan(c.budgetCents)}{" "}
                          · 发行 {c.issuanceLimit ?? "未设置上限"} 份
                        </small>
                      </td>
                      <td>
                        {localDate(c.startsAt)}
                        <small>至 {localDate(c.endsAt)}</small>
                      </td>
                      <td>
                        <Button
                          variant="secondary"
                          disabled={disabled}
                          onClick={() => setDraft(campaignDraft(c))}
                        >
                          编辑 / 启停
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        )}
      {draft && (
        <Modal
          title={draft.id ? "编辑营销活动" : "新增营销活动"}
          wide
          onClose={() => !submit.pending && setDraft(null)}
        >
          <form
            className="growth-stack"
            onSubmit={(e: FormEvent) => {
              e.preventDefault();
              try {
                campaignBody(draft);
                submit.setError("");
                setConfirm(true);
              } catch (error) {
                submit.setError(
                  error instanceof Error ? error.message : "配置无效",
                );
              }
            }}
          >
            <SubmitState state={submit} />
            <div className="growth-form-grid">
              <Field label="活动名称">
                <input
                  required
                  value={draft.name}
                  onChange={(e) => patch({ name: e.target.value })}
                />
              </Field>
              <Field label="活动类型">
                <select
                  value={draft.kind}
                  disabled={!!draft.id}
                  onChange={(e) =>
                    patch({ kind: e.target.value as Campaign["kind"] })
                  }
                >
                  {Object.entries(kinds).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="固定券面额（元）">
                <input
                  required
                  inputMode="decimal"
                  value={draft.amount}
                  onChange={(e) => patch({ amount: e.target.value })}
                />
              </Field>
              <Field label="最低适用消费（元）">
                <input
                  required
                  inputMode="decimal"
                  value={draft.minimum}
                  onChange={(e) => patch({ minimum: e.target.value })}
                />
              </Field>
              <Field label="适用服务">
                <select
                  value={draft.service}
                  onChange={(e) =>
                    patch({ service: e.target.value as Campaign["service"] })
                  }
                >
                  {Object.entries(services).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="有效天数（留空不限定）">
                <input
                  type="number"
                  min="1"
                  step="1"
                  value={draft.validity}
                  onChange={(e) => patch({ validity: e.target.value })}
                />
              </Field>
              <Field label="总预算（元，留空不限定）">
                <input
                  inputMode="decimal"
                  value={draft.budget}
                  onChange={(e) => patch({ budget: e.target.value })}
                />
              </Field>
              <Field label="总发行份数（留空不限定）">
                <input
                  type="number"
                  min="1"
                  step="1"
                  value={draft.issuance}
                  onChange={(e) => patch({ issuance: e.target.value })}
                />
              </Field>
              <Field label="每账号使用次数">
                <input
                  required
                  type="number"
                  min="1"
                  step="1"
                  value={draft.perAccount}
                  onChange={(e) => patch({ perAccount: e.target.value })}
                />
              </Field>
              <Field label="开始时间">
                <input
                  type="datetime-local"
                  value={draft.starts}
                  onChange={(e) => patch({ starts: e.target.value })}
                />
              </Field>
              <Field label="结束时间">
                <input
                  type="datetime-local"
                  value={draft.ends}
                  onChange={(e) => patch({ ends: e.target.value })}
                />
              </Field>
            </div>
            {draft.kind === "REFERRER" && (
              <Panel title="首笔有效订单资格">
                <Notice>
                  以邀请验证时的规则快照为准。必须是注册账号创建、现金实付大于零且成功送达的首笔达标订单；零元和历史游客导入不计入。
                </Notice>
                <div className="growth-form-grid">
                  <Field label="资格截止时间（留空明确为不限期）">
                    <input
                      type="datetime-local"
                      value={draft.qualificationDeadline}
                      onChange={(e) =>
                        patch({ qualificationDeadline: e.target.value })
                      }
                    />
                  </Field>
                  <Field
                    label="达标商品小计（元）"
                    hint="独立于奖励券自身的使用门槛"
                  >
                    <input
                      required
                      inputMode="decimal"
                      value={draft.qualificationMinimum}
                      onChange={(e) =>
                        patch({ qualificationMinimum: e.target.value })
                      }
                    />
                  </Field>
                  <Field label="资格适用服务">
                    <select
                      value={draft.qualificationService}
                      onChange={(e) =>
                        patch({
                          qualificationService: e.target
                            .value as Campaign["service"],
                        })
                      }
                    >
                      {Object.entries(services).map(([k, v]) => (
                        <option key={k} value={k}>
                          {v}
                        </option>
                      ))}
                    </select>
                  </Field>
                </div>
              </Panel>
            )}
            <Field label="活动说明">
              <textarea
                value={draft.description}
                onChange={(e) => patch({ description: e.target.value })}
              />
            </Field>
            <label className="growth-check">
              <input
                type="checkbox"
                checked={draft.enabled}
                onChange={(e) => patch({ enabled: e.target.checked })}
              />
              启用活动并允许发放
            </label>
            <Notice>
              固定金额优惠 · 每单最多 1 张券 ·
              不与其他优惠叠加。空预算或发行限额表示未设置上限，请核对后确认。
            </Notice>
            <Button disabled={disabled}>检查并保存活动</Button>
          </form>
        </Modal>
      )}
      {confirm && draft && (
        <ConfirmAction
          title="确认发布活动配置"
          pending={submit.pending}
          error={submit.error}
          onClose={() => setConfirm(false)}
          onConfirm={async (reason) => {
            const result = await submit.run(
              () =>
                mutate(
                  `/admin/promotions/campaigns${draft.id ? `/${encodeURIComponent(draft.id)}` : ""}`,
                  { ...campaignBody(draft), reason },
                  draft.id ? "PATCH" : "POST",
                ),
              "活动配置已保存",
            );
            if (result.ok) {
              setDraft(null);
              resource.reload();
            }
            return result.ok;
          }}
        >
          将保存「{draft.name}」，状态为{draft.enabled ? "启用" : "停用"}
          。已锁定及核销的优惠按原业务快照处理。
        </ConfirmAction>
      )}
    </div>
  );
}
type ExportJob = {
  exportJobId?: string;
  id?: string;
  status: string;
  expiresAt?: string;
  downloadUrl?: string;
  error?: string;
  rowCount?: number;
};
function ExportStatus({ id }: { id: string }) {
  const resource = useGrowthResource<ExportJob>(
    `/admin/promotions/exports/${encodeURIComponent(id)}`,
  );
  const job = resource.data,
    url = safeExportUrl(job?.downloadUrl, window.location.origin, DEMO);
  const expired =
    !!job?.expiresAt && new Date(job.expiresAt).getTime() <= Date.now();
  return (
    <Panel
      title="受控导出作业"
      action={
        <Button variant="secondary" onClick={() => resource.reload()}>
          刷新状态
        </Button>
      }
    >
      <ResourceState {...resource} />
      {job && (
        <div className="growth-stack">
          <p>
            作业 {id} ·{" "}
            {(
              {
                QUEUED: "已入队",
                PROCESSING: "生成中",
                COMPLETED: "已完成",
                READY: "可下载",
                FAILED: "失败",
                EXPIRED: "已过期",
              } as Record<string, string>
            )[job.status] || job.status}
          </p>
          {job.error && <Notice type="error">{job.error}</Notice>}
          <small>
            到期时间：{localDate(job.expiresAt)}
            {job.rowCount !== undefined && ` · ${job.rowCount} 行`}
          </small>
          {url && !expired && exportReady(job.status, url) && (
            <a className="btn secondary" href={url} rel="noopener noreferrer">
              下载权限控制的导出文件
            </a>
          )}
          {job.downloadUrl && !url && (
            <Notice type="warning">
              服务端下载地址不符合受控导出路径，请重新生成或联系管理员。
            </Notice>
          )}
          {expired && <Notice type="warning">导出已过期，请重新发起。</Notice>}
        </div>
      )}
    </Panel>
  );
}
export function PromotionRecordsPage() {
  const access = useAdminAccess("promotion.manage"),
    exportAccess = useAdminAccess("promotion.export"),
    submit = useSubmit();
  const [query, setQuery] = useState(""),
    [action, setAction] = useState<{
      record: PromotionRecord;
      kind: "freeze" | "unfreeze" | "revoke";
    } | null>(null),
    [exportOpen, setExportOpen] = useState(false),
    [jobId, setJobId] = useState("");
  const resource = useGrowthResource<{
    items: PromotionRecord[];
    summary?: {
      issued: number;
      reserved: number;
      used: number;
      expired?: number;
      couponDiscountCents: number;
      volumeDiscountCents: number;
    };
  }>(`/admin/promotions/records${query}`);
  const filter = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const params = new URLSearchParams();
    new FormData(e.currentTarget).forEach((v, k) => {
      if (String(v).trim()) params.set(k, String(v).trim());
    });
    setQuery(params.size ? `?${params}` : "");
  };
  const names = {
    freeze: "冻结优惠券",
    unfreeze: "解除风控冻结",
    revoke: "撤销优惠券",
  };
  return (
    <div className="admin-growth">
      <PageHeading
        title="优惠与邀请记录"
        description="检索发放、预约、核销、邀请奖励及风控流水；历史记录只追加审计。"
        action={
          <Button
            disabled={!exportAccess.allowed || submit.pending || !submit.online}
            onClick={() => setExportOpen(true)}
          >
            申请受控导出
          </Button>
        }
      />
      <AccessState access={access} />
      <SubmitState state={submit} />
      <Panel>
        <form onSubmit={filter} className="growth-filter">
          <Field label="客户 / 记录搜索">
            <input name="q" />
          </Field>
          <Field label="活动">
            <input name="campaignId" placeholder="活动编号" />
          </Field>
          <Field label="类型">
            <select name="type">
              <option value="">全部</option>
              {[
                "ISSUED",
                "RESERVED",
                "USED",
                "REFERRAL_REWARD",
                "FREEZE",
                "UNFREEZE",
                "REVOKED",
              ].map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </Field>
          <Field label="券状态">
            <select name="status">
              <option value="">全部</option>
              {["AVAILABLE", "RESERVED", "USED", "EXPIRED", "REVOKED"].map(
                (s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ),
              )}
            </select>
          </Field>
          <Field label="风控">
            <select name="riskHold">
              <option value="">全部</option>
              <option value="NONE">正常</option>
              <option value="FROZEN">冻结</option>
            </select>
          </Field>
          <Field label="开始日期">
            <input type="date" name="from" />
          </Field>
          <Field label="结束日期">
            <input type="date" name="to" />
          </Field>
          <Button>筛选记录</Button>
        </form>
      </Panel>
      <ResourceState {...resource} empty={resource.data?.items.length === 0} />
      {resource.data?.summary && (
        <Panel title="服务端运营汇总">
          <dl className="growth-facts">
            <div>
              <dt>发行 / 锁定 / 已核销</dt>
              <dd>
                {resource.data.summary.issued} /{" "}
                {resource.data.summary.reserved} / {resource.data.summary.used}
              </dd>
            </div>
            {resource.data.summary.expired !== undefined && (
              <div>
                <dt>已失效</dt>
                <dd>{resource.data.summary.expired}</dd>
              </div>
            )}
            <div>
              <dt>优惠券实际让利</dt>
              <dd>{yuan(resource.data.summary.couponDiscountCents)}</dd>
            </div>
            <div>
              <dt>A4 满量实际让利</dt>
              <dd>{yuan(resource.data.summary.volumeDiscountCents)}</dd>
            </div>
          </dl>
          <small>汇总以服务端返回口径为准，不从当前列表推算。</small>
        </Panel>
      )}
      {resource.data &&
        (!resource.error || resource.hasData) &&
        resource.data.items.length > 0 && (
          <Panel>
            <div className="growth-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>客户 / 活动</th>
                    <th>类型 / 状态</th>
                    <th>金额</th>
                    <th>时间 / 原因</th>
                    <th>风控操作</th>
                  </tr>
                </thead>
                <tbody>
                  {resource.data.items.map((r) => (
                    <tr key={r.id}>
                      <td>
                        <b>{r.accountMasked}</b>
                        <small>{r.campaignName}</small>
                      </td>
                      <td>
                        {r.type}
                        <small>
                          {r.status} ·{" "}
                          {r.riskHold === "FROZEN" ? "已冻结" : "正常"}
                        </small>
                      </td>
                      <td>{yuan(r.amountCents)}</td>
                      <td>
                        {localDate(r.time)}
                        <small>{r.reason || "—"}</small>
                      </td>
                      <td>
                        {r.couponGrantId &&
                        ["AVAILABLE", "RESERVED"].includes(r.status) ? (
                          <div className="button-row">
                            <Button
                              variant="secondary"
                              disabled={
                                !access.allowed ||
                                submit.pending ||
                                !submit.online
                              }
                              onClick={() =>
                                setAction({
                                  record: r,
                                  kind:
                                    r.riskHold === "FROZEN"
                                      ? "unfreeze"
                                      : "freeze",
                                })
                              }
                            >
                              {r.riskHold === "FROZEN" ? "解冻" : "冻结"}
                            </Button>
                            <Button
                              variant="secondary"
                              disabled={
                                !access.allowed ||
                                submit.pending ||
                                !submit.online
                              }
                              onClick={() =>
                                setAction({ record: r, kind: "revoke" })
                              }
                            >
                              撤销
                            </Button>
                          </div>
                        ) : (
                          <small>历史事实只读</small>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        )}
      {jobId && <ExportStatus id={jobId} />}
      {action && (
        <ConfirmAction
          title={names[action.kind]}
          pending={submit.pending}
          error={submit.error}
          onClose={() => setAction(null)}
          onConfirm={async (reason) => {
            const result = await submit.run(
              () =>
                mutate(
                  `/admin/promotions/coupons/${encodeURIComponent(action.record.couponGrantId!)}/${action.kind}`,
                  { reason, expectedVersion: action.record.version },
                ),
              `${names[action.kind]}成功`,
            );
            if (result.ok) resource.reload();
            return result.ok;
          }}
        >
          将对 {action.record.accountMasked}{" "}
          的优惠券执行此操作。预约券的风控可能阻止相关订单进入生产，不改写已核销事实。
        </ConfirmAction>
      )}
      {exportOpen && (
        <ConfirmAction
          title="申请优惠记录导出"
          pending={submit.pending}
          error={submit.error}
          onClose={() => setExportOpen(false)}
          onConfirm={async (reason) => {
            const result = await submit.run(
              () =>
                mutate<{ exportJobId: string }>(
                  "/admin/promotions/records/export",
                  {
                    filters: Object.fromEntries(new URLSearchParams(query)),
                    fields: [
                      "accountMasked",
                      "campaignName",
                      "type",
                      "status",
                      "amountCents",
                      "reason",
                      "time",
                    ],
                    reason,
                  },
                ),
              "导出申请已受理",
            );
            if (result.ok) {
              if (!result.data.exportJobId) {
                submit.setError("服务端未返回导出作业编号，请重新加载后核实。");
                return false;
              }
              setJobId(result.data.exportJobId);
            }
            return result.ok;
          }}
        >
          仅导出当前筛选下的白名单业务字段，客户身份保持掩码。服务端会校验权限、行数上限并记录审计；文件生成后需通过受控入口下载。
        </ConfirmAction>
      )}
    </div>
  );
}
