import { useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  ArrowUpRight,
  Bell,
  Check,
  ChevronDown,
  FileCheck2,
  FileText,
  Image,
  Leaf,
  MapPin,
  PackageCheck,
  Printer,
  ShieldCheck,
  Sparkles,
  Upload,
} from "lucide-react";
import { useStore } from "../lib/store";
import { DEMO, money } from "../lib/api";
import { LinkButton, Notice, Panel, SectionLink } from "../components/ui";
import { RefreshStatus } from "../components/feedback";
import { MotionFold } from "../components/motion-fold";
export function Home() {
  const { data, resources, refresh } = useStore();
  const configState = resources.config;
  const configReady = DEMO || !!configState?.hasData;
  const { config } = data;
  const [faq, setFaq] = useState<number | null>(0);
  const faqs = [
    [
      "上传什么格式的文件比较好？",
      "优先上传 PDF，可以更好地保留原有排版。支持的文件格式与大小限制会在上传页面展示。",
    ],
    [
      "打印完成后怎么领取？",
      "下单时选择校园楼栋和开放的预约时段。管理员安排配送后，可在订单详情查看最新时间与领取地点。",
    ],
    [
      "可以在线支付吗？",
      "目前使用微信联系管理员并线下付款。提交订单后，复制提取码和金额发送给管理员，核对收款后安排打印。",
    ],
  ];
  return (
    <main className="home-page">
      <section className="hero">
        <picture>
          <source
            media="(max-width: 767px)"
            srcSet="/assets/study-desk-mobile.webp"
          />
          <img
            className="hero-image"
            src="/assets/study-desk.webp"
            alt="蓝白兽人角色在书桌旁整理打印资料的手绘插画"
          />
        </picture>
        <div className="hero-shade" />
        <div className="hero-content">
          <div className="open-status">
            <i
              className={configReady && config.serviceEnabled ? "" : "closed"}
            />
            {!configReady
              ? "正在读取营业状态…"
              : config.serviceEnabled
                ? "正在营业，今天也为你认真打印"
                : "暂时休息中，历史订单仍可查询"}
          </div>
          <h1>
            让打印，
            <br />
            成为简单的事<span>。</span>
          </h1>
          <p>
            从课堂笔记到期末复习，
            <br />
            把每一份努力，清晰地留在纸上。
          </p>
          <LinkButton to="/u/new">
            开始我的打印
            <ArrowRight size={19} />
          </LinkButton>
          <div className="hero-trust">
            <span>
              <Check size={14} />
              清晰打印
            </span>
            <span>
              <Check size={14} />
              按需装订
            </span>
            <span>
              <Check size={14} />
              校园直达
            </span>
          </div>
        </div>
        <div className="hero-caption">
          <span>FOR EVERY PAGE OF YOUR CAMPUS LIFE</span>
          <b>一页一页，印下认真。</b>
        </div>
      </section>
      <div className="home-body">
        <div className="announcement">
          <span>
            <Bell size={16} />
            校园公告
          </span>
          <div style={{ flex: 1 }}>
            {configReady ? (
              <p>
                {config.announcement || "欢迎使用宿舍打印，请留意开放时段。"}
              </p>
            ) : (
              <p>
                {configState?.error ? (
                  <>
                    公告暂时无法读取{" "}
                    <button
                      className="text-link"
                      onClick={() => void refresh()}
                    >
                      重试
                    </button>
                  </>
                ) : (
                  "正在读取校园公告…"
                )}
              </p>
            )}
            <RefreshStatus
              loading={!DEMO && !!configState?.loading}
              error={configReady ? configState?.error : undefined}
              retry={refresh}
              label="正在更新校园公告与价目"
            />
          </div>
          <Link to="/u/help" aria-label="查看服务说明">
            <ArrowUpRight size={18} />
          </Link>
        </div>
        <section className="services-section">
          <div className="section-heading">
            <div>
              <h2>你需要打印什么？</h2>
              <p>选择一种服务，剩下的交给我们。</p>
            </div>
            <span className="muted desktop-only">
              小小的纸张，装着大大的认真
            </span>
          </div>
          <div className="service-grid">
            <Link className="service-card" to="/u/new?type=document">
              <div className="service-icon">
                <FileText size={28} />
              </div>
              <div>
                <h3>A4 文档打印</h3>
                <p>课件 · 复习资料 · 论文报告</p>
                <span>黑白 / 彩色 · 单面 / 双面</span>
              </div>
              <div className="service-card-tail">
                <b>
                  {!configReady
                    ? "—"
                    : config.prices.bwSingle !== null
                      ? money(config.prices.bwSingle)
                      : "待配置"}
                  <small>/ 张起</small>
                </b>
                <span className="round-arrow">
                  <ArrowUpRight size={20} />
                </span>
              </div>
            </Link>
            <Link className="service-card" to="/u/new?type=photo">
              <div className="service-icon">
                <Image size={28} />
              </div>
              <div>
                <h3>5 寸照片打印</h3>
                <p>定格校园生活的每个瞬间</p>
                <span>自由裁剪 · 完整保留</span>
              </div>
              <div className="service-card-tail">
                <b>
                  {!configReady
                    ? "—"
                    : config.prices.photo !== null
                      ? money(config.prices.photo)
                      : "待配置"}
                  <small>/ 张</small>
                </b>
                <span className="round-arrow">
                  <ArrowUpRight size={20} />
                </span>
              </div>
            </Link>
          </div>
        </section>
        <section className="home-workflow">
          <div className="section-heading">
            <div>
              <h2>简单四步，纸上见</h2>
              <p>不用排队，把时间留给更重要的事。</p>
            </div>
            <SectionLink to="/u/help">查看使用指南</SectionLink>
          </div>
          <div className="workflow-grid">
            {[
              [Upload, "上传文件", "选择文件，PDF 排版更稳定"],
              [Printer, "设置与预览", "按需设置，逐份核对最终效果"],
              [FileCheck2, "微信确认", "提交订单，联系管理员付款"],
              [PackageCheck, "安心收取", "查看安排，等待资料送达"],
            ].map(([Icon, title, text], i) => (
              <div className="workflow-step" key={String(title)}>
                <div className="workflow-icon">
                  {typeof Icon !== "string" && <Icon size={23} />}
                  <b>{i + 1}</b>
                </div>
                <h3>{String(title)}</h3>
                <p>{String(text)}</p>
              </div>
            ))}
          </div>
        </section>
        <section className="home-bottom">
          <div className="paper-note">
            <div className="paper-illustration">
              <div />
              <div />
              <div>
                <i />
                <i />
                <i />
                <i />
                <i />
              </div>
              <span>
                <Check size={18} />
              </span>
            </div>
            <div>
              <span className="muted">你的每一页，都值得认真对待</span>
              <h2>
                清晰，是我们的
                <br />
                基本功。
              </h2>
              <p>
                打印前确认最终 PDF，
                <br />
                打印后逐份核对、整理、装袋。
              </p>
              <Link to="/u/help" className="text-link">
                了解打印服务
                <ArrowRight size={16} />
              </Link>
            </div>
          </div>
          <div className="faq-block">
            <div className="section-heading">
              <h2>可能你还想知道</h2>
              <SectionLink to="/u/help">全部问题</SectionLink>
            </div>
            {faqs.map(([q, a], i) => (
              <div
                className={`faq-item ${faq === i ? "expanded" : ""}`}
                key={q}
              >
                <button
                  onClick={() => setFaq(faq === i ? null : i)}
                  aria-expanded={faq === i}
                >
                  {q}
                  <ChevronDown size={17} />
                </button>
                <MotionFold open={faq === i}>
                  <p>{a}</p>
                </MotionFold>
              </div>
            ))}
          </div>
        </section>
        <div className="home-promise">
          <span>
            <ShieldCheck size={19} />
            文件仅用于本次打印
          </span>
          <span>
            <Leaf size={19} />
            按需打印，减少浪费
          </span>
          <span>
            <MapPin size={19} />
            校内楼栋，免费配送
          </span>
        </div>
      </div>
    </main>
  );
}
