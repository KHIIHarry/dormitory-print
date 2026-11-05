import { useEffect, useRef, useState } from "react";
import { Plus, RefreshCw } from "lucide-react";
import {
  Button,
  Empty,
  ErrorState,
  Field,
  Loading,
  Notice,
  PageHeading,
  Panel,
} from "../components/ui";
import {
  getIdentityGeneration,
  growthRequest,
  useGrowthResource,
} from "../growth/client";
import { useIdentity } from "../growth/identity";
import { AccessState, useAdminAccess } from "./growth/common";
import { isUncertainSubmission } from "../lib/api";
import {
  expensePayload,
  shanghaiToday,
  type Expense,
  type ExpenseCategory,
  type ExpenseForm,
  type ExpenseList,
  type FinanceSummary,
} from "./finance-api";
import "./finance.css";

const categories: Record<ExpenseCategory, string> = {
  PAPER: "纸张",
  INK: "墨水",
  OTHER: "其他",
};
const money = (cents: number) =>
  new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    minimumFractionDigits: 2,
  }).format(cents / 100);
const initialForm = (): ExpenseForm => ({
  category: "PAPER",
  spentOn: shanghaiToday(),
  amount: "",
  paperSheets: "",
  note: "",
});
export function AdminFinance() {
  const access = useAdminAccess("finance.manage");
  const { generation } = useIdentity();
  const [month, setMonth] = useState(shanghaiToday().slice(0, 7));
  const [selectedMonth, setSelectedMonth] = useState(month);
  const [category, setCategory] = useState("");
  const [page, setPage] = useState(1);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(initialForm);
  const [voiding, setVoiding] = useState<Expense | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const summaryQuery = selectedMonth ? `?month=${selectedMonth}` : "";
  const query = new URLSearchParams({ page: String(page), pageSize: "20" });
  if (selectedMonth) query.set("month", selectedMonth);
  if (category) query.set("category", category);
  const summary = useGrowthResource<FinanceSummary>(
    access.allowed ? `/admin/finance/summary${summaryQuery}` : null,
  );
  const expenses = useGrowthResource<ExpenseList>(
    access.allowed ? `/admin/finance/expenses?${query}` : null,
  );
  useEffect(() => {
    setShowForm(false);
    setForm(initialForm());
    setVoiding(null);
    setReason("");
    setError("");
    setMessage("");
    setBusy(false);
    lock.current = false;
  }, [generation]);
  const changePeriod = (value: string) => {
    setSelectedMonth(value);
    setPage(1);
    setVoiding(null);
    setReason("");
    setError("");
    setMessage("");
  };
  const refresh = async () => {
    await Promise.all([summary.reload(), expenses.reload()]);
  };
  const submit = async (operation: "create" | "void") => {
    if (lock.current || !access.allowed) return;
    setError("");
    setMessage("");
    let body: unknown;
    try {
      body =
        operation === "create"
          ? expensePayload(form)
          : { expectedVersion: voiding?.version, reason: reason.trim() };
      if (operation === "void" && (!voiding || !reason.trim()))
        throw new Error("请填写作废原因");
    } catch (failure) {
      setError((failure as Error).message);
      return;
    }
    lock.current = true;
    setBusy(true);
    const owner = getIdentityGeneration();
    try {
      await growthRequest(
        operation === "create"
          ? "/admin/finance/expenses"
          : `/admin/finance/expenses/${encodeURIComponent(voiding!.id)}/void`,
        { method: "POST", body },
      );
      if (owner !== getIdentityGeneration()) return;
      setForm(initialForm());
      setShowForm(false);
      setVoiding(null);
      setReason("");
      setMessage(
        operation === "create" ? "支出已记录" : "记录已作废，历史已保留",
      );
      // Drain any deduplicated read started before the write, then fetch fresh facts.
      await refresh();
      if (owner === getIdentityGeneration()) await refresh();
    } catch (failure) {
      if (owner === getIdentityGeneration())
        setError(
          isUncertainSubmission(failure)
            ? "提交结果待核实，请刷新后核对，勿重复录入"
            : (failure as Error).message || "保存失败，请重试",
        );
    } finally {
      if (owner === getIdentityGeneration()) {
        lock.current = false;
        setBusy(false);
      }
    }
  };
  const patch = (key: keyof ExpenseForm, value: string) =>
    setForm((old) => ({ ...old, [key]: value }));
  const s = summary.data;
  return (
    <div className="finance-page">
      <PageHeading
        title="耗材与收支"
        description="按实际收付款和采购支出核算。"
        action={
          <Button
            variant="secondary"
            onClick={() => void refresh()}
            disabled={
              !access.allowed ||
              busy ||
              summary.loading ||
              expenses.loading ||
              summary.refreshing ||
              expenses.refreshing
            }
          >
            <RefreshCw size={16} />
            刷新
          </Button>
        }
      />
      <AccessState access={access} />
      {access.allowed && (
        <>
          <div className="finance-toolbar">
            <div className="finance-period" role="group" aria-label="统计范围">
              <Button
                variant={selectedMonth ? "" : "secondary"}
                disabled={busy}
                onClick={() => changePeriod(month)}
              >
                月份
              </Button>
              <Button
                variant={!selectedMonth ? "" : "secondary"}
                disabled={busy}
                onClick={() => changePeriod("")}
              >
                累计
              </Button>
            </div>
            {selectedMonth && (
              <Field label="统计月份">
                <input
                  type="month"
                  value={month}
                  max={shanghaiToday().slice(0, 7)}
                  disabled={busy}
                  onChange={(event) => {
                    const value = event.target.value;
                    if (/^\d{4}-\d{2}$/.test(value)) {
                      setMonth(value);
                      changePeriod(value);
                    }
                  }}
                />
              </Field>
            )}
            <Button
              onClick={() => {
                setShowForm(true);
                setVoiding(null);
                setReason("");
                setError("");
                setMessage("");
              }}
              disabled={busy || showForm}
            >
              <Plus size={16} />
              记录支出
            </Button>
          </div>
          <div aria-live="polite">
            {message && <Notice type="success">{message}</Notice>}
            {error && (
              <div role="alert">
                <Notice type="error">{error}</Notice>
              </div>
            )}
          </div>
          {showForm && (
            <Panel title="记录支出" className="finance-entry">
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  void submit("create");
                }}
                noValidate
              >
                <fieldset disabled={busy} className="finance-form-grid">
                  <Field label="支出日期">
                    <input
                      type="date"
                      required
                      value={form.spentOn}
                      max={shanghaiToday()}
                      onChange={(event) => patch("spentOn", event.target.value)}
                    />
                  </Field>
                  <Field label="类别">
                    <select
                      value={form.category}
                      onChange={(event) =>
                        setForm((old) => ({
                          ...old,
                          category: event.target.value as ExpenseCategory,
                          paperSheets: "",
                        }))
                      }
                    >
                      {Object.entries(categories).map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="金额（元）">
                    <input
                      inputMode="decimal"
                      required
                      placeholder="0.00"
                      value={form.amount}
                      onChange={(event) => patch("amount", event.target.value)}
                    />
                  </Field>
                  {form.category === "PAPER" && (
                    <Field label="纸张张数">
                      <input
                        inputMode="numeric"
                        required
                        placeholder="如 500"
                        value={form.paperSheets}
                        onChange={(event) =>
                          patch("paperSheets", event.target.value)
                        }
                      />
                    </Field>
                  )}
                  <div className="finance-note-field">
                    <Field label="备注（选填）">
                      <textarea
                        rows={2}
                        maxLength={500}
                        value={form.note}
                        onChange={(event) => patch("note", event.target.value)}
                      />
                    </Field>
                  </div>
                </fieldset>
                <div className="finance-actions">
                  <Button type="submit" busy={busy} busyLabel="正在保存">
                    保存支出
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={busy}
                    onClick={() => {
                      setShowForm(false);
                      setError("");
                    }}
                  >
                    取消
                  </Button>
                </div>
              </form>
            </Panel>
          )}
          {summary.error && (
            <ErrorState
              message={summary.error}
              retry={() => void summary.reload()}
            />
          )}
          {summary.loading && <Loading label="正在读取收支汇总" />}
          {s && (
            <Panel
              className="finance-overview"
              title={selectedMonth ? `${selectedMonth} 收支` : "累计收支"}
            >
              <dl className="finance-metrics">
                {[
                  ["净实收", s.netReceivedCents],
                  ["耗材支出", s.paperPurchaseCents + s.inkPurchaseCents],
                  ["耗材后结余", s.consumablesBalanceCents],
                  ["最终结余", s.balanceCents],
                ].map(([label, value]) => (
                  <div key={label}>
                    <dt>{label}</dt>
                    <dd>{money(Number(value))}</dd>
                  </div>
                ))}
              </dl>
              <dl className="finance-details">
                {[
                  ["已确认收款", money(s.confirmedReceivedCents)],
                  ["退款", money(s.refundCents)],
                  ["纸张采购", money(s.paperPurchaseCents)],
                  ["墨水采购", money(s.inkPurchaseCents)],
                  ["已发工资", money(s.paidWagesCents)],
                  ["其他支出", money(s.otherExpenseCents)],
                  ["采购纸张", `${s.paperSheets.toLocaleString("zh-CN")} 张`],
                  [
                    "纸张参考均价",
                    s.paperUnitYuan === null ? "—" : `${s.paperUnitYuan} 元/张`,
                  ],
                ].map(([label, value]) => (
                  <div key={label}>
                    <dt>{label}</dt>
                    <dd>{value}</dd>
                  </div>
                ))}
              </dl>
              {selectedMonth && s.legacyArchiveCount > 0 && (
                <Notice>部分历史归档缺少收付日期，仅计入累计。</Notice>
              )}
              {summary.refreshing && (
                <p className="finance-muted" role="status">
                  正在更新汇总…
                </p>
              )}
            </Panel>
          )}
          <Panel title="支出记录" className="finance-records">
            <div className="finance-list-toolbar">
              <Field label="类别筛选">
                <select
                  value={category}
                  disabled={busy}
                  onChange={(event) => {
                    setCategory(event.target.value);
                    setPage(1);
                    setVoiding(null);
                    setReason("");
                    setError("");
                  }}
                >
                  <option value="">全部类别</option>
                  {Object.entries(categories).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </Field>
              <span className="finance-muted">筛选仅影响记录列表</span>
            </div>
            {expenses.error && (
              <ErrorState
                message={expenses.error}
                retry={() => void expenses.reload()}
              />
            )}
            {expenses.loading ? (
              <Loading label="正在读取支出记录" />
            ) : (
              expenses.data && (
                <>
                  {!expenses.data.items.length ? (
                    <Empty
                      title="暂无支出记录"
                      text="可切换月份或类别，或记录一笔支出。"
                    />
                  ) : (
                    <ul className="finance-list">
                      {expenses.data.items.map((item) => (
                        <li
                          key={item.id}
                          className={
                            item.status === "VOIDED" ? "finance-voided" : ""
                          }
                        >
                          <div className="finance-record-main">
                            <div>
                              <strong>{categories[item.category]}</strong>
                              <span className="finance-muted">
                                {item.spentOn}
                              </span>
                            </div>
                            <strong className="finance-amount">
                              {money(item.amountCents)}
                            </strong>
                          </div>
                          <div className="finance-record-details">
                            {item.paperSheets !== null && (
                              <span>
                                {item.paperSheets.toLocaleString("zh-CN")} 张
                              </span>
                            )}
                            {item.note && <p>{item.note}</p>}
                          </div>
                          <div className="finance-record-footer">
                            {item.status === "VOIDED" && (
                              <span className="finance-muted">已作废</span>
                            )}
                            {item.status === "ACTIVE" && (
                              <Button
                                variant="secondary"
                                disabled={busy}
                                onClick={() => {
                                  setVoiding(item);
                                  setShowForm(false);
                                  setReason("");
                                  setError("");
                                  setMessage("");
                                }}
                              >
                                作废
                              </Button>
                            )}
                          </div>
                          {item.status === "VOIDED" && (
                            <p className="finance-muted">
                              作废原因：{item.voidReason}
                            </p>
                          )}
                          {voiding?.id === item.id && (
                            <form
                              className="finance-void-form"
                              onSubmit={(event) => {
                                event.preventDefault();
                                void submit("void");
                              }}
                              noValidate
                            >
                              <p>作废后不再计入支出，原记录会保留。</p>
                              <Field label="作废原因">
                                <textarea
                                  required
                                  maxLength={500}
                                  rows={2}
                                  value={reason}
                                  disabled={busy}
                                  onChange={(event) =>
                                    setReason(event.target.value)
                                  }
                                />
                              </Field>
                              <div className="finance-actions">
                                <Button
                                  type="submit"
                                  busy={busy}
                                  busyLabel="正在作废"
                                >
                                  确认作废
                                </Button>
                                <Button
                                  type="button"
                                  variant="secondary"
                                  disabled={busy}
                                  onClick={() => {
                                    setVoiding(null);
                                    setReason("");
                                    setError("");
                                  }}
                                >
                                  取消
                                </Button>
                              </div>
                            </form>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                  <div className="finance-pagination">
                    <span>
                      共 {expenses.data.total} 条 · 第 {page} 页
                    </span>
                    <div>
                      <Button
                        variant="secondary"
                        disabled={page <= 1 || busy || expenses.refreshing}
                        onClick={() => {
                          setPage((old) => old - 1);
                          setVoiding(null);
                          setError("");
                        }}
                      >
                        上一页
                      </Button>
                      <Button
                        variant="secondary"
                        disabled={
                          page * expenses.data.pageSize >=
                            expenses.data.total ||
                          busy ||
                          expenses.refreshing
                        }
                        onClick={() => {
                          setPage((old) => old + 1);
                          setVoiding(null);
                          setError("");
                        }}
                      >
                        下一页
                      </Button>
                    </div>
                  </div>
                </>
              )
            )}
            {expenses.refreshing && (
              <p className="finance-muted" role="status">
                正在更新记录…
              </p>
            )}
          </Panel>
        </>
      )}
    </div>
  );
}
