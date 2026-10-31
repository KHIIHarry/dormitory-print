import { useEffect, useState } from "react";
import { Eye, RefreshCw, Search } from "lucide-react";
import { DEMO, useStore } from "../lib/store";
import { useGrowthResource } from "../growth/client";
import {
  Button,
  Empty,
  ErrorState,
  Field,
  Loading,
  Modal,
  Notice,
  PageHeading,
  Panel,
} from "../components/ui";
import { AccessState, useAdminAccess } from "./growth/common";
import {
  auditActionNames,
  auditRow,
  auditTime,
  type AuditRow,
} from "./audit-view";
import { useIdentity } from "../growth/identity";
import "./audit.css";

export function AuditPage() {
  const { data } = useStore();
  const { generation } = useIdentity();
  const access = useAdminAccess("audit.view");
  const [form, setForm] = useState({
    q: "",
    actorId: "",
    dateFrom: "",
    dateTo: "",
  });
  const [filters, setFilters] = useState(form);
  const [cursors, setCursors] = useState([""]);
  const [selected, setSelected] = useState<AuditRow | null>(null);
  useEffect(() => {
    setSelected(null);
    setCursors([""]);
  }, [generation]);
  const query = new URLSearchParams({ limit: "50" });
  if (filters.q) query.set("q", filters.q);
  if (filters.actorId) query.set("actorId", filters.actorId);
  if (filters.dateFrom)
    query.set("createdFrom", `${filters.dateFrom}T00:00:00+08:00`);
  if (filters.dateTo)
    query.set("createdTo", `${filters.dateTo}T23:59:59.999999+08:00`);
  if (cursors.at(-1)) query.set("cursor", cursors.at(-1)!);
  const resource = useGrowthResource<{
    items: unknown[];
    nextCursor?: string | null;
  }>(!DEMO && access.allowed ? `/admin/audit-logs?${query}` : null);
  const rows = (DEMO ? data.audit : resource.data?.items || []).map(auditRow);
  const visible = DEMO
    ? rows.filter(
        (row) =>
          (!filters.q ||
            `${row.action} ${row.reason} ${row.targetId}`
              .toLowerCase()
              .includes(filters.q.toLowerCase())) &&
          (!filters.actorId || row.actorId.includes(filters.actorId)) &&
          (!filters.dateFrom ||
            row.createdAt.slice(0, 10) >= filters.dateFrom) &&
          (!filters.dateTo || row.createdAt.slice(0, 10) <= filters.dateTo),
      )
    : rows;
  const patch = (key: keyof typeof form, value: string) =>
    setForm((old) => ({ ...old, [key]: value }));
  return (
    <div className="audit-page">
      <PageHeading
        title="操作审计"
        description="查看关键操作、操作者、关联对象及已记录的变更事实。审计日志只读。"
        action={
          <Button
            variant="secondary"
            onClick={() => {
              setCursors([""]);
              void resource.reload();
            }}
          >
            <RefreshCw size={16} />
            刷新
          </Button>
        }
      />
      <AccessState access={access} />
      <Panel>
        <form
          className="audit-filter"
          onSubmit={(event) => {
            event.preventDefault();
            setSelected(null);
            setCursors([""]);
            setFilters({ ...form });
          }}
        >
          <Field label="搜索操作">
            <input
              placeholder="动作、对象或原因"
              maxLength={200}
              value={form.q}
              onChange={(e) => patch("q", e.target.value)}
            />
          </Field>
          <Field label="操作者 ID">
            <input
              placeholder="按员工或客户 ID 筛选"
              value={form.actorId}
              onChange={(e) => patch("actorId", e.target.value)}
            />
          </Field>
          <Field label="开始日期">
            <input
              type="date"
              value={form.dateFrom}
              max={form.dateTo || undefined}
              onChange={(e) => patch("dateFrom", e.target.value)}
            />
          </Field>
          <Field label="结束日期">
            <input
              type="date"
              value={form.dateTo}
              min={form.dateFrom || undefined}
              onChange={(e) => patch("dateTo", e.target.value)}
            />
          </Field>
          <Button type="submit">
            <Search size={16} />
            查询
          </Button>
        </form>
      </Panel>
      {resource.error && <Notice type="error">{resource.error}</Notice>}
      {resource.loading ? (
        <Loading label="正在读取审计记录…" />
      ) : resource.error && !resource.hasData ? (
        <ErrorState message={resource.error} retry={resource.reload} />
      ) : (
        access.allowed && (
          <Panel>
            <div className="table-scroll">
              <table className="audit-table">
                <thead>
                  <tr>
                    <th>操作时间</th>
                    <th>操作者</th>
                    <th>操作</th>
                    <th>关联对象</th>
                    <th>原因</th>
                    <th>详情</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((row) => (
                    <tr key={row.id}>
                      <td className="numeric">{auditTime(row.createdAt)}</td>
                      <td>
                        <strong>{row.actorName || "未记录操作者"}</strong>
                        <small>{row.actorId}</small>
                      </td>
                      <td>
                        <strong>
                          {auditActionNames[row.action] ||
                            row.action ||
                            "未记录动作"}
                        </strong>
                        <small>{row.action}</small>
                      </td>
                      <td>
                        <strong>{row.targetType || "对象"}</strong>
                        <small>{row.targetId || "未记录对象 ID"}</small>
                      </td>
                      <td>{row.reason || "未填写原因"}</td>
                      <td>
                        <button
                          type="button"
                          className="icon-btn"
                          title="查看审计详情"
                          aria-label={`查看审计详情：${row.id}`}
                          onClick={() => setSelected(row)}
                        >
                          <Eye size={18} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!visible.length && (
              <Empty
                title="没有符合条件的审计记录"
                text="可调整日期或搜索条件后重试。"
              />
            )}
            <div className="audit-pagination">
              <Button
                variant="secondary"
                disabled={cursors.length <= 1 || resource.loading}
                onClick={() => setCursors((old) => old.slice(0, -1))}
              >
                上一页
              </Button>
              <span>
                第 {cursors.length} 页 · 本页 {visible.length} 条
              </span>
              <Button
                variant="secondary"
                disabled={
                  DEMO || !resource.data?.nextCursor || resource.loading
                }
                onClick={() =>
                  setCursors((old) => [...old, resource.data!.nextCursor!])
                }
              >
                下一页
              </Button>
            </div>
          </Panel>
        )
      )}
      {selected && access.allowed && (
        <Modal
          title={`审计详情 · ${auditActionNames[selected.action] || selected.action}`}
          wide
          onClose={() => setSelected(null)}
        >
          <dl className="detail-dl vertical">
            <div>
              <dt>审计编号</dt>
              <dd>{selected.id}</dd>
            </div>
            <div>
              <dt>操作时间</dt>
              <dd>{auditTime(selected.createdAt)}</dd>
            </div>
            <div>
              <dt>操作者</dt>
              <dd>{selected.actorName || selected.actorId || "未记录"}</dd>
            </div>
            <div>
              <dt>操作者 ID</dt>
              <dd>{selected.actorId || "未记录"}</dd>
            </div>
            <div>
              <dt>动作代码</dt>
              <dd>{selected.action}</dd>
            </div>
            <div>
              <dt>关联对象</dt>
              <dd>
                {selected.targetType} · {selected.targetId}
              </dd>
            </div>
            <div>
              <dt>请求追踪编号</dt>
              <dd>{selected.requestId || "服务端未提供"}</dd>
            </div>
            <div>
              <dt>原因</dt>
              <dd>{selected.reason || "未填写原因"}</dd>
            </div>
          </dl>
          <h3>已记录的变更数据</h3>
          {Object.keys(selected.data).length ? (
            <pre className="audit-data">
              {JSON.stringify(selected.data, null, 2)}
            </pre>
          ) : (
            <Notice>
              这条事件没有记录额外变更数据；页面不会补造修改前后的内容。
            </Notice>
          )}
        </Modal>
      )}
    </div>
  );
}
