import { useGrowthResource } from "../growth/client";
import {
  demoDashboardSummary,
  assertDashboardSummary,
  type DashboardSummary,
} from "../growth/dashboard";
import { DEMO } from "../lib/api";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  ArrowUpRight,
  CalendarDays,
  CheckCircle2,
  Clock,
  FileCheck2,
  Files,
  Package,
  Printer,
  Receipt,
  ShoppingBag,
  Truck,
  TriangleAlert,
} from "lucide-react";
import { ADMIN, useStore } from "../lib/store";
import { money } from "../lib/api";
import {
  Badge,
  ErrorState,
  Button,
  Empty,
  PageHeading,
  Panel,
  SectionLink,
  Stat,
} from "../components/ui";
import { ResourceSkeleton, RefreshStatus } from "../components/feedback";
export function Dashboard() {
  const { data, resources, refresh } = useStore();
  const summary = useGrowthResource<DashboardSummary>(
    DEMO ? null : "/admin/dashboard",
  );
  let stats: DashboardSummary | null = null;
  let summaryError = summary.error;
  if (DEMO || summary.data) {
    try {
      stats = assertDashboardSummary(
        DEMO
          ? demoDashboardSummary(
              data.orders,
              data.exceptions.filter((e) => e.status !== "RESOLVED").length,
            )
          : summary.data!,
      );
    } catch (e) {
      summaryError = (e as Error).message;
    }
  }
  const summaryFeedback = (variant: "stats" | "cards") =>
    !summaryError && summary.loading ? (
      <ResourceSkeleton variant={variant} />
    ) : (
      <ErrorState
        message={summaryError || "统计尚未就绪"}
        retry={summary.reload}
      />
    );
  const ready = (name: "batches" | "orders") =>
    DEMO || !resources[name] || resources[name]!.hasData;
  const resourceFeedback = (
    name: "batches" | "orders",
    variant: "cards" | "table",
  ) => {
    const resource = resources[name];
    if (DEMO || !resource) return null;
    if (!resource.hasData)
      return resource.error ? (
        <ErrorState message={resource.error} retry={refresh} />
      ) : (
        <ResourceSkeleton variant={variant} />
      );
    return (
      <RefreshStatus
        loading={resource.loading}
        error={resource.error || undefined}
        retry={refresh}
      />
    );
  };
  const queue = stats
    ? ([
        [
          "待确认收款",
          stats.pendingPaymentCount,
          Receipt,
          "orders?status=PENDING_CONFIRMATION",
          "核对微信付款",
        ],
        [
          "待打印任务",
          stats.pendingPrintTaskCount,
          Printer,
          "print",
          "按任务分别打印",
        ],
        [
          "待装订文件",
          stats.pendingBindingCount,
          Files,
          "binding",
          "整理原始页序",
        ],
        [
          "待装袋订单",
          stats.pendingPackingCount,
          Package,
          "packing",
          "核对齐全后装袋",
        ],
        [
          "已装袋待配送",
          stats.readyForDeliveryCount,
          Truck,
          "batches",
          "按计划出发",
        ],
        [
          "配送异常",
          stats.deliveryExceptionCount,
          TriangleAlert,
          "exceptions",
          "及时跟进处理",
        ],
      ] as const)
    : [];
  return (
    <>
      {!DEMO && stats && (
        <RefreshStatus
          loading={summary.refreshing}
          error={summaryError || undefined}
          retry={summary.reload}
        />
      )}
      <PageHeading
        title="下午好，管理员"
        description="今天也一起，把每一份资料认真交到同学手中。"
        action={
          <Link className="btn secondary" to={`${ADMIN}/batches`}>
            <CalendarDays size={17} />
            今日配送安排
          </Link>
        }
      />
      {stats ? (
        <div className="dashboard-summary">
          <Stat
            label="当前订单"
            value={stats.orderCount}
            icon={<ShoppingBag size={21} />}
            caption="订单总数 · 含待确认订单"
            href={`${ADMIN}/orders`}
          />
          <Stat
            label="待确认付款"
            value={stats.pendingPaymentCount}
            icon={<Receipt size={21} />}
            caption="等待核对线下收款"
            href={`${ADMIN}/orders?status=PENDING_CONFIRMATION`}
          />
          <Stat
            label="原单已确认实收"
            value={money(stats.cashReceivedCents)}
            icon={<FileCheck2 size={21} />}
            caption="原打印单收款，追加费用另列"
            href={`${ADMIN}/orders`}
          />
          <Stat
            label="当前已送达"
            value={stats.deliveredCount}
            icon={<CheckCircle2 size={21} />}
            caption="已经完成的校园配送"
            href={`${ADMIN}/orders?status=DELIVERED`}
          />
        </div>
      ) : (
        summaryFeedback("stats")
      )}
      <Panel title="订单金额快照">
        {stats ? (
          <>
            <div className="financial-summary">
              <div>
                <span>商品原价</span>
                <strong>{money(stats.subtotalCents)}</strong>
              </div>
              <div>
                <span>优惠券让利</span>
                <strong>{money(stats.couponDiscountCents)}</strong>
              </div>
              <div>
                <span>满量让利</span>
                <strong>{money(stats.volumeDiscountCents)}</strong>
              </div>
              <div>
                <span>原打印单应付</span>
                <strong>{money(stats.payableCents)}</strong>
              </div>
              <div>
                <span>零元确认</span>
                <strong>{stats.zeroPayableCount} 单</strong>
              </div>
              <div>
                <span>退款</span>
                <strong>{money(stats.refundCents)}</strong>
              </div>
              <div><span>员工优惠让利</span><strong>{money(stats.staffDiscountCents)}</strong></div>
              <div><span>追加服务费用</span><strong>{money(stats.supplementaryFeesCents)}</strong></div>
              <div><span>追加费用实收</span><strong>{money(stats.supplementaryCashReceivedCents)}</strong></div>
              <div><span>追加费用退款</span><strong>{money(stats.supplementaryRefundCents)}</strong></div>
              <div><span>追加费用待收</span><strong>{money(stats.supplementaryOutstandingCents)}</strong></div>
              <div><span>未匹配收款</span><strong>{money(stats.unmatchedReceiptCents)}</strong></div>
              <div><span>未匹配款已退</span><strong>{money(stats.unmatchedReceiptRefundCents)}</strong></div>
              <div>
                <span>人工调整</span>
                <strong>{money(stats.manualAdjustmentCents)}</strong>
              </div>
            </div>
            <p className="form-hint">
              统计以服务端全量汇总与结算记录为准，不从当前订单列表推算现金收入。
              {DEMO
                ? "开发演示仅统计本次会话的样例账目，未模拟退款或人工调整。"
                : ""}
            </p>
          </>
        ) : (
          summaryFeedback("cards")
        )}
      </Panel>
      <div className="dashboard-grid">
        <Panel
          title="待办工作"
          action={<span className="muted">按生产流程逐步处理</span>}
        >
          {stats ? (
            <div className="queue-grid">
              {queue.map(([label, count, Icon, path, hint]) => (
                <Link
                  key={label}
                  to={`${ADMIN}/${path}`}
                  className="queue-item"
                >
                  <span className="queue-icon">
                    <Icon size={21} />
                  </span>
                  <div>
                    <b>{label}</b>
                    <small>{hint}</small>
                  </div>
                  <strong>{count}</strong>
                  <ArrowRight size={16} />
                </Link>
              ))}
            </div>
          ) : (
            summaryFeedback("cards")
          )}
        </Panel>
        <Panel
          className="today-plan"
          title="今日配送"
          action={<SectionLink to={`${ADMIN}/batches`}>全部</SectionLink>}
        >
          {resourceFeedback("batches", "cards")}
          {ready("batches") &&
            data.batches.map((b) => (
              <Link
                className="mini-batch"
                to={`${ADMIN}/batches/${b.id}`}
                key={b.id}
              >
                <span className="batch-mini-icon">
                  <Truck size={21} />
                </span>
                <div>
                  <strong>{b.name}</strong>
                  <small>
                    {b.time} · {b.orderIds.length} 单
                  </small>
                </div>
                <Badge status={b.status} />
              </Link>
            ))}
        </Panel>
      </div>
      <Panel
        title="最近订单"
        action={<SectionLink to={`${ADMIN}/orders`}>查看全部订单</SectionLink>}
      >
        {resourceFeedback("orders", "table")}
        {ready("orders") && (
          <>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>提取码 / 订单</th>
                    <th>同学</th>
                    <th>打印资料</th>
                    <th>目标楼栋</th>
                    <th>应付金额</th>
                    <th>状态</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {data.orders.slice(0, 5).map((o) => (
                    <tr key={o.id}>
                      <td>
                        <Link
                          className="table-code"
                          to={`${ADMIN}/orders/${o.id}`}
                        >
                          {o.pickupCode}
                        </Link>
                        <small>{o.createdAt}</small>
                      </td>
                      <td>
                        {o.nickname}
                        <small>{o.phone}</small>
                      </td>
                      <td>
                        <span className="table-filename">
                          {o.files[0]?.name}
                        </span>
                        <small>
                          {o.files.length} 个文件 ·{" "}
                          {o.files.reduce((s, f) => s + f.segments.length, 0)}{" "}
                          个任务
                        </small>
                      </td>
                      <td>
                        {
                          data.buildings.find((b) => b.id === o.locationId)
                            ?.name
                        }
                      </td>
                      <td className="numeric">{money(o.totalCents)}</td>
                      <td>
                        <Badge status={o.status} />
                      </td>
                      <td>
                        <Link
                          className="text-link"
                          to={`${ADMIN}/orders/${o.id}`}
                        >
                          查看
                          <ArrowUpRight size={14} />
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!data.orders.length && <Empty title="今天还没有订单" />}
          </>
        )}
      </Panel>
      <div className="dashboard-note">
        <ShieldIcon />
        <span>
          每次打印都需要确认实际结果；全部生产完成后，请人工核对并确认装袋。
        </span>
        <Link to={`${ADMIN}/packing`}>
          去装袋中心
          <ArrowRight size={15} />
        </Link>
      </div>
    </>
  );
}
function ShieldIcon() {
  return <CheckCircle2 size={19} />;
}
