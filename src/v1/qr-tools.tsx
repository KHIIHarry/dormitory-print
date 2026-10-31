import { useState } from "react";
import { QrCode, RotateCcw, ShieldCheck } from "lucide-react";
import { LinkQrButton } from "../components/link-qr";
import { Button, Field, Notice, PageHeading, Panel } from "../components/ui";
import { QrLinkError, validateQrLink } from "../lib/link-qr";
import "./admin.css";

/**
 * Admin-only, local-only QR tool. It deliberately performs no API request and
 * accepts only legal HTTPS links entered by an administrator.
 */
export function AdminQRCodeToolsPage() {
  const [input, setInput] = useState("");
  const [accepted, setAccepted] = useState("");
  const [error, setError] = useState("");

  const check = () => {
    setAccepted("");
    try {
      const value = validateQrLink(input, {
        kind: "custom",
        allowLocalHttp: false,
      });
      setError("");
      setAccepted(value);
    } catch (cause) {
      setError(
        cause instanceof QrLinkError
          ? cause.message
          : "链接校验失败，请检查后重试",
      );
    }
  };

  const reset = () => {
    setInput("");
    setAccepted("");
    setError("");
  };

  return (
    <div className="admin-growth v1-admin">
      <PageHeading
        title="链接转二维码"
        description="把注册、帮助或公告等网页地址做成二维码。别人扫码后，就能打开对应网页。"
      />

      <Panel title="自定义链接二维码">
        <Notice>
          这是网页链接二维码，不是微信好友码、群二维码或收款码。邀请好友页面已经提供专用邀请二维码；添加管理员微信的二维码请在系统配置中上传。
        </Notice>

        <ol className="qr-tool-steps">
          <li>复制你希望扫码打开的 HTTPS 网页链接。</li>
          <li>粘贴到下方，点击“校验并预览”。</li>
          <li>打开二维码预览并下载 PNG，即可放在海报或群公告里。</li>
        </ol>
        <div className="v1-qr-form">
          <Field
            label="HTTPS 链接"
            hint="请填写完整的 HTTPS 网页地址。二维码在本浏览器生成，不会上传到第三方。"
            error={error}
          >
            <input
              type="url"
              inputMode="url"
              autoComplete="off"
              spellCheck={false}
              placeholder="https://你的域名/u/account/register"
              value={input}
              onChange={(event) => {
                setInput(event.target.value);
                setAccepted("");
                setError("");
              }}
              data-testid="admin-qr-input"
            />
          </Field>
          <div className="button-row">
            <Button onClick={check} data-testid="admin-qr-check">
              <QrCode size={16} />
              校验并预览
            </Button>
            <Button variant="secondary" onClick={reset}>
              <RotateCcw size={16} />
              清空
            </Button>
          </div>
          {accepted && (
            <div className="v1-qr-result" data-testid="admin-qr-result">
              <p>
                <ShieldCheck size={16} aria-hidden="true" />
                链接已通过 HTTPS 校验，可展开二维码预览并下载 PNG。
              </p>
              <LinkQrButton value={accepted} label="预览并下载二维码" />
            </div>
          )}
        </div>
      </Panel>
    </div>
  );
}
