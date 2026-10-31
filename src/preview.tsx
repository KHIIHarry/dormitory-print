import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, LayoutDashboard, Truck, User } from "lucide-react";
import { useStore, ADMIN } from "./lib/store";
import { Logo, Notice, Panel, PageHeading, Button } from "./components/ui";

type PreviewEntry = {
  label: string;
  path?: string;
  sample?: "document" | "photo";
};
type PreviewGroup = {
  title: string;
  tone: "caramel" | "sand" | "clay";
  entries: PreviewEntry[];
};

const customerGroups: PreviewGroup[] = [
  {
    title: "打印与设置",
    tone: "caramel",
    entries: [
      { label: "文件上传", path: "/u/new" },
      { label: "文档设置示例", sample: "document" },
      { label: "照片编辑示例", sample: "photo" },
    ],
  },
  {
    title: "订单与进度",
    tone: "sand",
    entries: [
      { label: "我的订单", path: "/u/orders" },
      { label: "订单详情", path: "/u/orders/order-1" },
      {
        label: "下单成功",
        path: "/u/orders/order-1/contact",
      },
    ],
  },
  {
    title: "账户与帮助",
    tone: "clay",
    entries: [
      { label: "我的账户", path: "/u/account" },
      { label: "优惠券", path: "/u/rewards" },
      { label: "邀请好友", path: "/u/invite" },
      { label: "账号安全", path: "/u/account/security" },
      { label: "帮助中心", path: "/u/help" },
    ],
  },
];
const adminGroups: PreviewGroup[] = [
  {title:'客户与权益',tone:'sand',entries:[{label:'客户管理',path:`${ADMIN}/customers`},{label:'优惠活动',path:`${ADMIN}/settings/promotions`},{label:'满量折扣',path:`${ADMIN}/settings/volume-discounts`},{label:'优惠记录',path:`${ADMIN}/promotions/records`}]},
  {title:'邮件服务',tone:'clay',entries:[{label:'邮件配置',path:`${ADMIN}/settings/mail`},{label:'邮件模板',path:`${ADMIN}/settings/mail-templates`},{label:'个人账号',path:`${ADMIN}/account`}]},
  {
    title: "订单管理",
    tone: "caramel",
    entries: [{ label: "订单管理", path: `${ADMIN}/orders` }],
  },
  {
    title: "打印与生产",
    tone: "caramel",
    entries: [
      { label: "打印中心", path: `${ADMIN}/print` },
      { label: "装订中心", path: `${ADMIN}/binding` },
      { label: "装袋中心", path: `${ADMIN}/packing` },
    ],
  },
  {
    title: "配送与调度",
    tone: "sand",
    entries: [
      { label: "配送调度", path: `${ADMIN}/batches` },
      { label: "配送时段", path: `${ADMIN}/slots` },
      { label: "地图编辑", path: `${ADMIN}/map` },
    ],
  },
  {
    title: "系统与团队",
    tone: "clay",
    entries: [
      { label: "系统配置", path: `${ADMIN}/settings` },
      { label: "员工管理", path: `${ADMIN}/staff` },
    ],
  },
  {
    title: "异常与记录",
    tone: "clay",
    entries: [
      { label: "异常中心", path: `${ADMIN}/exceptions` },
      { label: "回收站", path: `${ADMIN}/trash` },
      { label: "操作审计", path: `${ADMIN}/audit` },
    ],
  },
];
const courierGroups: PreviewGroup[] = [
  {
    title: "批次与订单",
    tone: "caramel",
    entries: [
      {
        label: "批次详情",
        path: "/courier/batches/batch-2",
      },
      {
        label: "配送订单",
        path: "/courier/orders/order-4",
      },
    ],
  },
  {
    title: "交付与异常",
    tone: "sand",
    entries: [
      {
        label: "拍照交付",
        path: "/courier/orders/order-4/proof",
      },
      {
        label: "配送异常",
        path: "/courier/orders/order-4/exception",
      },
    ],
  },
  {
    title: "账户管理",
    tone: "clay",
    entries: [{ label: "配送员账户", path: "/courier/account" }],
  },
];

function PreviewLinks({
  groups,
  onSelect,
  busy,
}: {
  groups: PreviewGroup[];
  onSelect: (entry: PreviewEntry) => void;
  busy: boolean;
}) {
  return (
    <div className="preview-route-groups">
      {groups.map((group) => (
        <section
          className={`preview-function-group tone-${group.tone}`}
          key={group.title}
          aria-label={group.title}
        >
          <div className="preview-function-row">
            {group.entries.map((entry) => (
              <button
                key={entry.label}
                className="preview-route-link"
                disabled={busy}
                onClick={() => onSelect(entry)}
                title={entry.sample ? `加载${entry.label}` : entry.label}
              >
                <span>{entry.label}</span>
              </button>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

export function PreviewHub() {
  const { setRole, setDraft, toast } = useStore();
  const [loadingSample, setLoadingSample] = useState(false);
  const nav = useNavigate();
  const go = (path: string, role: "ADMIN" | "COURIER" | null) => {
    setRole(role);
    nav(path);
  };
  const sample = async (photo = false) => {
    setLoadingSample(true);
    try {
      const m = await import("./lib/demo");
      const file = m.makeFile(photo ? "校园生活.webp" : "英语期末复习资料.pdf");
      if (photo) {
        const response = await fetch("/assets/study-desk.webp");
        if (!response.ok) throw new Error("示例图片暂时无法加载，请稍后重试");
        const blob = await response.blob();
        file.kind = "PHOTO";
        file.pages = 1;
        file.localFile = new File([blob], file.name, { type: "image/webp" });
        file.url = URL.createObjectURL(blob);
        file.photo.sourceWidth = 1536;
        file.photo.sourceHeight = 1024;
      }
      setRole(null);
      setDraft([file]);
      nav(photo ? `/u/new/photo/${file.id}` : "/u/new/settings");
    } catch (error) {
      toast((error as Error).message);
    } finally {
      setLoadingSample(false);
    }
  };
  return (
    <main className="content-page preview-hub">
      <Logo />
      <PageHeading
        title="宿舍打印 · 全端页面预览"
        description="先选择角色，再查看对应页面。所有交互均在内存中演示，刷新重置。"
      />
      <Notice>
        此页面仅在开发 / 演示构建中存在。普通用户首页不显示任何管理或调度入口。
      </Notice>
      <div className="preview-role-grid">
        <Panel>
          <span className="service-icon">
            <User size={28} />
          </span>
          <h2>同学，开始打印吧</h2>
          <p>上传、设置、最终预览、结算和订单查询。</p>
          <Button onClick={() => go("/u/", null)}>
            打开用户端
            <ArrowRight size={16} />
          </Button>
          <PreviewLinks
            groups={customerGroups}
            busy={loadingSample}
            onSelect={(entry) =>
              entry.sample
                ? void sample(entry.sample === "photo")
                : go(entry.path!, null)
            }
          />
        </Panel>
        <Panel>
          <span className="service-icon">
            <LayoutDashboard size={28} />
          </span>
          <h2>管理员工作空间</h2>
          <p>收款、打印、装订、装袋、调度与运营配置。</p>
          <Button onClick={() => go(ADMIN, "ADMIN")}>
            打开管理端
            <ArrowRight size={16} />
          </Button>
          <PreviewLinks
            groups={adminGroups}
            busy={false}
            onSelect={(entry) => go(entry.path!, "ADMIN")}
          />
        </Panel>
        <Panel>
          <span className="service-icon">
            <Truck size={28} />
          </span>
          <h2>把每一份托付送达</h2>
          <p>自己的批次、楼栋任务、现场定位和拍照交付。</p>
          <Button onClick={() => go("/courier/", "COURIER")}>
            打开配送端
            <ArrowRight size={16} />
          </Button>
          <PreviewLinks
            groups={courierGroups}
            busy={false}
            onSelect={(entry) => go(entry.path!, "COURIER")}
          />
        </Panel>
      </div>
    </main>
  );
}
