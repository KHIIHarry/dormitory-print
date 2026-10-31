import { PencilLine } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  Button,
  Field,
  Modal,
  Notice,
  PageHeading,
  Panel,
} from "../../components/ui";
import { useGrowthResource } from "../../growth/client";
import { useIdentity } from "../../growth/identity";
import type { MailConfig, MailTemplate } from "../../growth/types";
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
  buildMailPatch,
  buildTemplatePreview,
  buildTestMailBody,
  localDate,
  mailStatus,
} from "./logic";
type MailTest = {
  status: string;
  time?: string;
  testedAt?: string;
  message?: string;
  code?: string;
  emailJobId?: string;
};
export function MailSettingsPage() {
  const { generation } = useIdentity();
  return <MailSettingsForm key={generation} />;
}
function MailSettingsForm() {
  const access = useAdminAccess("mail.manage"),
    resource = useGrowthResource<MailConfig>("/admin/config/mail"),
    submit = useSubmit();
  const [mailTask, setMailTask] = useState<
    "test-connection" | "test-send" | null
  >(null);
  const [draft, setDraft] = useState<MailConfig | null>(null),
    [secret, setSecret] = useState(""),
    [confirm, setConfirm] = useState<"save" | "clear" | null>(null),
    [testAddress, setTestAddress] = useState(""),
    [test, setTest] = useState<MailTest | null>(null);
  const edited = useRef(false);
  useEffect(() => {
    // Foreground refresh updates the resource, but must not replace an edit.
    // Keep its original version so concurrent server changes still return 409.
    if (resource.data && !edited.current) {
      const received = resource.data;
      setDraft((current) =>
        current && current.version > received.version ? current : { ...received },
      );
    }
  }, [resource.data]);
  const patch = (value: Partial<MailConfig>) => {
    edited.current = true;
    setDraft((d) => (d ? { ...d, ...value } : d));
  };
  const discardAndReload = () => {
    edited.current = false;
    setSecret("");
    setDraft(resource.data ? { ...resource.data } : null);
    submit.setError("");
    void resource.reload();
  };
  const testMail = async (kind: "test-connection" | "test-send") => {
    if (!draft) return;
    setMailTask(kind);
    const result = await submit.run(() =>
      mutate<MailTest>(
        `/admin/mail/${kind}`,
        kind === "test-send" ? buildTestMailBody(testAddress) : {},
      ),
    );
    setMailTask(null);
    if (result.ok) setTest(result.data);
  };
  const disabled =
    !access.allowed ||
    submit.pending ||
    !submit.online ||
    resource.loading ||
    !!resource.error;
  return (
    <div className="admin-growth">
      <PageHeading
        title="邮件设置"
        description="配置 SMTP 发送能力、发件标识和连接验证。"
      />
      <AccessState access={access} />
      <ResourceState {...resource} />
      <SubmitState state={submit} />
      {draft && resource.data && (!resource.error || resource.hasData) && (
        <>
          <Panel title={`SMTP 配置 · 版本 ${draft.version}`}>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                setConfirm("save");
              }}
              className="growth-stack"
            >
              <label className="growth-check">
                <input
                  type="checkbox"
                  checked={draft.enabled}
                  onChange={(e) => patch({ enabled: e.target.checked })}
                />
                启用邮件发送
              </label>
              {!draft.enabled && (
                <Notice type="warning">
                  发送当前停用，邮箱注册、验证与找回流程可能不可用；游客下单仍可继续。
                </Notice>
              )}
              <div className="growth-form-grid">
                <Field label="SMTP 主机">
                  <input
                    required
                    value={draft.host}
                    onChange={(e) => patch({ host: e.target.value })}
                    autoComplete="off"
                  />
                </Field>
                <Field label="端口">
                  <input
                    type="number"
                    required
                    min="1"
                    max="65535"
                    value={draft.port}
                    onChange={(e) => patch({ port: Number(e.target.value) })}
                  />
                </Field>
                <Field label="连接安全">
                  <select
                    value={draft.security}
                    onChange={(e) =>
                      patch({
                        security: e.target.value as MailConfig["security"],
                      })
                    }
                  >
                    <option value="STARTTLS">STARTTLS（强制升级 TLS）</option>
                    <option value="SSL_TLS">SSL / TLS（隐式 TLS）</option>
                    {draft.allowInsecure && (
                      <option value="NONE">NONE（已批准的可信环境）</option>
                    )}
                  </select>
                </Field>
                <Field label="连接超时（秒）">
                  <input
                    required
                    type="number"
                    min="1"
                    value={draft.timeoutSeconds}
                    onChange={(e) =>
                      patch({ timeoutSeconds: Number(e.target.value) })
                    }
                  />
                </Field>
                <Field label="认证用户名">
                  <input
                    value={draft.username}
                    onChange={(e) => patch({ username: e.target.value })}
                    required={draft.authEnabled}
                    autoComplete="off"
                  />
                </Field>
                <Field
                  label="SMTP 授权码 / 密码"
                  hint={
                    draft.secretConfigured
                      ? "已配置密钥；留空保持原密钥。"
                      : "尚未配置；部分服务商须使用独立授权码。"
                  }
                >
                  <input
                    type="password"
                    value={secret}
                    onChange={(e) => {
                      edited.current = true;
                      setSecret(e.target.value);
                    }}
                    autoComplete="new-password"
                  />
                </Field>
                <Field label="发件人名称">
                  <input
                    value={draft.fromName}
                    onChange={(e) => patch({ fromName: e.target.value })}
                    required
                  />
                </Field>
                <Field label="发件邮箱">
                  <input
                    type="email"
                    value={draft.fromAddress}
                    onChange={(e) => patch({ fromAddress: e.target.value })}
                    required
                  />
                </Field>
                <Field label="Reply-To（可选）">
                  <input
                    type="email"
                    value={draft.replyTo}
                    onChange={(e) => patch({ replyTo: e.target.value })}
                  />
                </Field>
              </div>
              <label className="growth-check">
                <input
                  type="checkbox"
                  checked={draft.authEnabled}
                  onChange={(e) => patch({ authEnabled: e.target.checked })}
                />
                使用 SMTP AUTH 身份认证
              </label>
              <div className="button-row">
                <Button type="submit" disabled={disabled}>
                  保存设置
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={
                    submit.pending ||
                    resource.loading ||
                    resource.refreshing ||
                    !submit.online
                  }
                  onClick={discardAndReload}
                >
                  放弃修改并重新加载
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={disabled || !draft.secretConfigured}
                  onClick={() => setConfirm("clear")}
                >
                  显式清除已保存密钥
                </Button>
              </div>
            </form>
          </Panel>
          <Panel title="运维测试">
            <div className="growth-stack">
              <Notice>
                测试使用已保存的配置。请先保存修改，再分别测试连接或发送。SMTP
                接受邮件不代表已到达收件箱。
              </Notice>
              <Button
                variant="secondary"
                busy={submit.pending && mailTask === "test-connection"}
                disabled={disabled}
                onClick={() => testMail("test-connection")}
              >
                测试连接与身份认证
              </Button>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void testMail("test-send");
                }}
                className="growth-inline-form"
              >
                <Field label="授权测试收件地址">
                  <input
                    type="email"
                    value={testAddress}
                    onChange={(e) => setTestAddress(e.target.value)}
                    required
                    placeholder="输入允许接收测试信的邮箱"
                  />
                </Field>
                <Button
                  busy={submit.pending && mailTask === "test-send"}
                  disabled={disabled || !testAddress}
                  type="submit"
                >
                  发送测试邮件
                </Button>
              </form>
              {(test || draft.lastTest) && (
                <Notice>
                  <b>{mailStatus((test || draft.lastTest)?.status)}</b>
                  <p>
                    {localDate(
                      test?.time || test?.testedAt || draft.lastTest?.time,
                    )}{" "}
                    ·{" "}
                    {test?.code ||
                      test?.message ||
                      draft.lastTest?.message ||
                      ""}
                  </p>
                  {test?.emailJobId && (
                    <small>作业编号：{test.emailJobId}</small>
                  )}
                </Notice>
              )}
            </div>
          </Panel>
        </>
      )}
      {confirm && draft && (
        <ConfirmAction
          title={confirm === "clear" ? "清除 SMTP 密钥" : "保存邮件配置"}
          onClose={() => setConfirm(null)}
          pending={submit.pending}
          error={submit.error}
          onConfirm={async (reason) => {
            const result = await submit.run(
              () =>
                mutate<MailConfig>(
                  "/admin/config/mail",
                  buildMailPatch(draft, secret, reason, confirm === "clear"),
                  "PATCH",
                ),
              "邮件配置已保存",
            );
            if (result.ok) {
              edited.current = false;
              setDraft({ ...result.data });
              setSecret("");
              void resource.reload();
            }
            return result.ok;
          }}
        >
          {confirm === "clear"
            ? "确认清除已保存的 SMTP 密钥。依赖身份认证的发信可能立即不可用。"
            : "确认发布当前配置。修改会影响后续邮件发送及账号安全流程。"}
        </ConfirmAction>
      )}
    </div>
  );
}
const templateNames: Record<string, string> = {
  CUSTOMER_VERIFY_EMAIL: "验证邮箱",
  CUSTOMER_RESET_PASSWORD: "用户重置密码",
  STAFF_INVITE_OR_SETUP: "员工初次设置密码",
  STAFF_RESET_PASSWORD: "员工重置密码",
  ORDER_CREATED: "下单回执",
  ORDER_STATUS_UPDATED: "订单进度",
  REFERRAL_REWARD_GRANTED: "邀请奖励到账",
};
type Preview = { subject: string; textBody: string; htmlBody: string };
export function MailTemplatesPage() {
  const access = useAdminAccess("mail.manage"),
    resource = useGrowthResource<{ items: MailTemplate[] }>(
      "/admin/mail/templates",
    ),
    submit = useSubmit();
  const [selected, setSelected] = useState<MailTemplate | null>(null),
    [draft, setDraft] = useState<MailTemplate | null>(null),
    [confirm, setConfirm] = useState<"save" | "restore" | null>(null),
    [preview, setPreview] = useState<Preview | null>(null),
    [testAddress, setTestAddress] = useState(""),
    [test, setTest] = useState<MailTest | null>(null),
    [target, setTarget] = useState<"subject" | "textBody" | "htmlBody">(
      "textBody",
    );
  const open = async (t: MailTemplate) => {
    const result = await submit.run(() =>
      mutate<MailTemplate>(
        `/admin/mail/templates/${encodeURIComponent(t.key)}`,
        undefined,
        "GET",
      ),
    );
    if (result.ok) {
      setSelected(result.data);
      setDraft({ ...result.data });
      setTest(null);
      setPreview(null);
    }
  };
  const body = () =>
    draft
      ? {
          subject: draft.subject,
          textBody: draft.textBody,
          htmlBody: draft.htmlBody,
          enabled: draft.critical ? true : draft.enabled,
          expectedVersion: draft.version,
        }
      : {};
  const disabled = !access.allowed || submit.pending || !submit.online;
  return (
    <div className="admin-growth">
      <PageHeading
        title="邮件模板"
        description="编辑后端提供的中文默认模板；预览和测试只使用安全样例。"
      />
      <AccessState access={access} />
      <ResourceState {...resource} empty={resource.data?.items.length === 0} />
      <SubmitState state={submit} />
      {resource.data && (!resource.error || resource.hasData) && (
        <Panel>
          <div className="growth-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>模板</th>
                  <th>状态</th>
                  <th>版本 / 最后修改</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {resource.data.items.map((t) => (
                  <tr key={t.key}>
                    <td>
                      <b>{templateNames[t.key] || t.name}</b>
                      <small>{t.key}</small>
                    </td>
                    <td>
                      {t.critical
                        ? "关键安全邮件"
                        : t.enabled
                          ? "已启用"
                          : "已停用"}
                    </td>
                    <td>
                      v{t.version} · {t.updatedBy}
                      <small>{localDate(t.updatedAt)}</small>
                    </td>
                    <td>
                      <div className="admin-row-actions">
                        <button
                          type="button"
                          className="icon-btn table-action"
                          aria-label={`编辑邮件模板：${templateNames[t.key] || t.name}`}
                          title="编辑邮件模板"
                          disabled={disabled}
                          onClick={() => open(t)}
                        >
                          <PencilLine size={18} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {Object.keys(templateNames).filter(
            (k) => !resource.data?.items.some((t) => t.key === k),
          ).length > 0 && (
            <Notice type="warning">
              以下默认模板尚未由服务端提供：
              {Object.keys(templateNames)
                .filter((k) => !resource.data?.items.some((t) => t.key === k))
                .map((k) => templateNames[k])
                .join("、")}
              。请检查后端初始化。
            </Notice>
          )}
        </Panel>
      )}
      {draft && selected && (
        <Modal
          wide
          title={`编辑邮件模板 · ${templateNames[draft.key] || draft.name}`}
          onClose={() => {
            if (!submit.pending) {
              setDraft(null);
              setSelected(null);
              setTestAddress("");
            }
          }}
        >
          <form
            className="growth-stack"
            onSubmit={(e: FormEvent) => {
              e.preventDefault();
              setConfirm("save");
            }}
          >
            <SubmitState state={submit} />
            <p>
              版本 {draft.version} · {selected.updatedBy} ·{" "}
              {localDate(selected.updatedAt)}
            </p>
            <Field label="主题">
              <input
                required
                value={draft.subject}
                onFocus={() => setTarget("subject")}
                onChange={(e) =>
                  setDraft({ ...draft, subject: e.target.value })
                }
              />
            </Field>
            <Field label="纯文本正文">
              <textarea
                required
                rows={7}
                value={draft.textBody}
                onFocus={() => setTarget("textBody")}
                onChange={(e) =>
                  setDraft({ ...draft, textBody: e.target.value })
                }
              />
            </Field>
            <Field label="HTML 正文（可选）">
              <textarea
                rows={7}
                value={draft.htmlBody}
                onFocus={() => setTarget("htmlBody")}
                onChange={(e) =>
                  setDraft({ ...draft, htmlBody: e.target.value })
                }
              />
            </Field>
            <Field
              label={`插入白名单变量到${target === "subject" ? "主题" : target === "htmlBody" ? " HTML 正文" : "纯文本正文"}`}
            >
              <select
                value=""
                onChange={(e) => {
                  if (e.target.value)
                    setDraft({
                      ...draft,
                      [target]: draft[target] + `{{${e.target.value}}}`,
                    });
                }}
              >
                <option value="">选择变量</option>
                {draft.variables.map((v) => (
                  <option key={v.key} value={v.key}>
                    {v.key} — {v.description}
                  </option>
                ))}
              </select>
            </Field>
            <label className="growth-check">
              <input
                type="checkbox"
                checked={draft.critical || draft.enabled}
                disabled={draft.critical}
                onChange={(e) =>
                  setDraft({ ...draft, enabled: e.target.checked })
                }
              />
              {draft.critical
                ? "关键安全模板必须启用"
                : "启用业务提醒（仍需用户订阅）"}
            </label>
            <Notice>
              预览由后端校验和渲染；模板不支持任意表达式，测试不会生成可操作的真实凭证。
            </Notice>
            <div className="button-row">
              <Button type="submit" disabled={disabled}>
                保存版本
              </Button>
              <Button
                type="button"
                variant="secondary"
                disabled={disabled}
                onClick={async () => {
                  const result = await submit.run(() =>
                    mutate<Preview>(
                      `/admin/mail/templates/${encodeURIComponent(draft.key)}/preview`,
                      buildTemplatePreview(draft),
                    ),
                  );
                  if (result.ok) setPreview(result.data);
                }}
              >
                预览邮件
              </Button>
              <Button
                type="button"
                variant="secondary"
                disabled={disabled}
                onClick={() => setConfirm("restore")}
              >
                恢复官方默认
              </Button>
            </div>
            <div className="growth-inline-form">
              <Field label="授权测试收件地址">
                <input
                  type="email"
                  value={testAddress}
                  onChange={(e) => setTestAddress(e.target.value)}
                />
              </Field>
              <Button
                type="button"
                disabled={
                  disabled ||
                  !testAddress ||
                  !/^\S+@\S+\.\S+$/.test(testAddress)
                }
                onClick={async () => {
                  const result = await submit.run(() =>
                    mutate<MailTest>(
                      `/admin/mail/templates/${encodeURIComponent(draft.key)}/test-send`,
                      buildTestMailBody(testAddress),
                    ),
                  );
                  if (result.ok) setTest(result.data);
                }}
              >
                发送安全测试邮件
              </Button>
            </div>
            {test && (
              <Notice>
                {mailStatus(test.status)}
                {test.message && <p>{test.message}</p>}
                {test.emailJobId && ` · 作业 ${test.emailJobId}`}
              </Notice>
            )}
          </form>
        </Modal>
      )}
      {preview && (
        <Modal wide title="后端安全渲染预览" onClose={() => setPreview(null)}>
          <div className="growth-stack">
            <h3>{preview.subject}</h3>
            <pre className="growth-mail-text">{preview.textBody}</pre>
            {preview.htmlBody && (
              <iframe
                title="HTML 邮件预览"
                className="growth-mail-preview"
                sandbox=""
                referrerPolicy="no-referrer"
                srcDoc={`<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'none'; base-uri 'none'"><body>${preview.htmlBody}</body>`}
              />
            )}
          </div>
        </Modal>
      )}
      {confirm && draft && (
        <ConfirmAction
          title={confirm === "save" ? "保存模板新版本" : "恢复官方默认模板"}
          pending={submit.pending}
          error={submit.error}
          onClose={() => setConfirm(null)}
          onConfirm={async (reason) => {
            const result = await submit.run(
              () =>
                mutate<MailTemplate>(
                  `/admin/mail/templates/${encodeURIComponent(draft.key)}${confirm === "restore" ? "/restore-default" : ""}`,
                  confirm === "restore"
                    ? { expectedVersion: draft.version, reason }
                    : { ...body(), reason },
                  confirm === "restore" ? "POST" : "PATCH",
                ),
              confirm === "restore" ? "默认模板已恢复" : "模板已保存",
            );
            if (result.ok) {
              resource.reload();
              setDraft(null);
              setSelected(null);
            }
            return result.ok;
          }}
        >
          此次操作将生成新的模板版本；已经排队的邮件继续使用其发送快照。
        </ConfirmAction>
      )}
    </div>
  );
}
