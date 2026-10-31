import { useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  ChevronDown,
  ClipboardList,
  FileText,
  LifeBuoy,
  MessageCircle,
  Printer,
  ShieldCheck,
  User,
} from "lucide-react";
import { useStore } from "../lib/store";
import { DEMO, money } from "../lib/api";
import {
  Button,
  CopyButton,
  Empty,
  LinkButton,
  Notice,
  PageHeading,
  Panel,
  SectionLink,
} from "../components/ui";
import { RefreshStatus } from "../components/feedback";
import { MotionFold } from "../components/motion-fold";
export function Help() {
  const { data, resources, refresh } = useStore();
  const configState = resources.config;
  const configReady = DEMO || !!configState?.hasData;
  const [open, setOpen] = useState<number | null>(0);
  const faqs = [
    [
      "哪些文件可以打印？",
      "支持 A4 文档、A4 图片与 5 寸照片。优先上传 PDF，Word、PPT 由服务端转换后再确认最终 PDF。加密文件需先解除密码。",
    ],
    [
      "图片可以用 A4 纸打印吗？",
      "可以。上传图片后选择 A4 纸或5寸照片纸。A4 纸会完整保留图片，等比居中排成一页，可继续设置黑白/彩色、单双面和份数；选择照片纸则进入照片裁剪与留白设置。",
    ],
    [
      "双面怎么计费？",
      "按每个分段的实际纸张数收费。每份奇数页会补一个空白背面，不与下一份共用纸张；不同分段各自计算。",
    ],
    [
      "可以只打印部分页面吗？",
      "可以。在打印设置中切换“高级分段”，输入例如 1-4,7,9-12。遗漏页需要在“明确跳过”中填写，避免漏印。",
    ],
    [
      "怎样付款和查看进度？",
      "提交订单后，将提取码、金额与微信昵称发送给管理员，线下付款。管理员确认收款后安排打印，可在订单详情查看进度。",
    ],
    [
      "配送送到房间吗？",
      "只配送到你选择的宿舍楼或教学楼公共区域，不填写楼层门牌。请关注最新配送安排与交付凭证。",
    ],
    [
      "可以修改订单吗？",
      "待确认时可以修改文件、打印参数、联系信息和楼栋，修改输出后需要重新预览。付款确认后仅在配送开始前可修改楼栋。",
    ],
    [
      "文件和信息会被离线保存吗？",
      "私有文件、联系方式、交付照片与定位不会进入离线缓存。订单专属链接是访问凭据，请自己妥善保管。",
    ],
  ];
  return (
    <main className="content-page">
      <PageHeading
        title="有什么可以帮到你？"
        description="把疑问留在这里，把资料放心交给我们。"
      />
      <div className="help-layout">
        <Panel>
          <h2>常见问题</h2>
          {faqs.map(([q, a], i) => (
            <div className="faq-item" key={q}>
              <button
                onClick={() => setOpen(open === i ? null : i)}
                aria-expanded={open === i}
              >
                {q}
                <ChevronDown size={17} />
              </button>
              <MotionFold open={open === i}>
                <p>{a}</p>
              </MotionFold>
            </div>
          ))}
        </Panel>
        <aside>
          <Panel title="打印价目">
            {!configReady && configState?.error && (
              <Notice type="error">
                <span role="alert">价目暂时无法读取：{configState.error}</span>{" "}
                <button className="text-link" onClick={() => void refresh()}>
                  重试
                </button>
              </Notice>
            )}
            <RefreshStatus
              loading={!DEMO && !!configState?.loading}
              error={configReady ? configState?.error : undefined}
              retry={refresh}
              label="正在读取打印价目"
            />
            <div className="price-rows">
              {[
                ["A4 黑白单面", "bwSingle"],
                ["A4 黑白双面", "bwDuplex"],
                ["A4 彩色单面", "colorSingle"],
                ["A4 彩色双面", "colorDuplex"],
                ["5 寸照片", "photo"],
              ].map(([label, key]) => (
                <div key={key}>
                  <span>{label}</span>
                  <b>
                    {!configReady
                      ? "—"
                      : data.config.prices[
                            key as keyof typeof data.config.prices
                          ] !== null
                        ? `${money(data.config.prices[key as keyof typeof data.config.prices]!)} / 张`
                        : "暂未配置"}
                  </b>
                </div>
              ))}
            </div>
            <Notice>
              按实际纸张计价，配送费为 0。装订收费规则尚未配置时暂不开放。
            </Notice>
          </Panel>
          <Panel title="还需要帮助？">
            <p className="muted">
              联系管理员时，提供订单提取码，更方便我们帮你核查。
            </p>
            {!configReady ? (
              <p className="muted">
                {configState?.error
                  ? "管理员联系方式暂时无法读取"
                  : "正在读取管理员联系方式…"}
              </p>
            ) : data.config.wechat ? (
              <CopyButton text={data.config.wechat} label="复制管理员微信号" />
            ) : (
              <Notice>管理员联系方式尚未配置，请通过校园服务渠道联系。</Notice>
            )}
          </Panel>
        </aside>
      </div>
    </main>
  );
}
export function Profile() {
  const { data } = useStore();
  return (
    <main className="content-page profile-page">
      <PageHeading title="我的" />
      <Panel>
        <div className="profile-hero">
          <span className="profile-avatar">
            <User size={40} />
          </span>
          <div>
            <h2>你好，同学</h2>
            <p>认真生活的每一页，我们陪你打印。</p>
          </div>
        </div>
        <Notice>
          当前通过安全会话访问本机订单，无需注册。跨设备验证方式尚未开放，请保存订单专属链接。
        </Notice>
        <div className="profile-stats">
          <div>
            <strong>{data.orders.length}</strong>
            <span>我的订单</span>
          </div>
          <div>
            <strong>
              {
                data.orders.filter((o) => o.status === "PENDING_CONFIRMATION")
                  .length
              }
            </strong>
            <span>待确认</span>
          </div>
          <div>
            <strong>
              {data.orders.filter((o) => o.status === "DELIVERED").length}
            </strong>
            <span>已送达</span>
          </div>
        </div>
      </Panel>
      <Panel>
        <Link className="settings-link" to="/u/orders">
          <ClipboardList size={20} />
          <span>全部订单</span>
          <ArrowRight size={18} />
        </Link>
        <Link className="settings-link" to="/u/help">
          <LifeBuoy size={20} />
          <span>帮助与服务说明</span>
          <ArrowRight size={18} />
        </Link>
        <div className="settings-link">
          <ShieldCheck size={20} />
          <span>隐私保护</span>
          <small>私有信息不进入离线缓存</small>
        </div>
      </Panel>
      <p className="profile-version">DormPrint 宿舍打印 · V1.0</p>
    </main>
  );
}
