import { useEffect, useState } from "react";
import {
  Button,
  Empty,
  Field,
  Notice,
  PageHeading,
  Panel,
} from "../../components/ui";
import { useGrowthResource } from "../../growth/client";
import type { VolumePolicy } from "../../growth/types";
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
  buildVolumePatch,
  dateInput,
  isoDate,
  localDate,
  parseCents,
  parseRate,
  positiveInteger,
  previewVolume,
  validateTiers,
  yuan,
} from "./logic";
type EditableTier = { id: string; minimum: string; rate: string };
type PolicyResponse = VolumePolicy & {
  auditLogs?: {
    id: string;
    actor: string;
    time: string;
    reason: string;
    version: number;
  }[];
};
export function VolumeDiscountsPage() {
  const access = useAdminAccess("promotion.manage"),
    resource = useGrowthResource<PolicyResponse>(
      "/admin/config/volume-discounts",
    ),
    submit = useSubmit();
  const [enabled, setEnabled] = useState(false),
    [description, setDescription] = useState(""),
    [effective, setEffective] = useState(""),
    [tiers, setTiers] = useState<EditableTier[]>([]),
    [confirm, setConfirm] = useState(false);
  const [sheets, setSheets] = useState(""),
    [subtotal, setSubtotal] = useState(""),
    [preview, setPreview] = useState<
      ReturnType<typeof previewVolume> | undefined
    >(),
    [previewError, setPreviewError] = useState("");
  useEffect(() => {
    if (resource.data) {
      const p = resource.data;
      setEnabled(p.enabled);
      setDescription(p.description);
      setEffective(dateInput(p.effectiveAt));
      setTiers(
        p.tiers.map((t) => ({
          id: t.id,
          minimum: String(t.minSheets),
          rate: String(t.payRateBps / 1000),
        })),
      );
      setPreview(undefined);
    }
  }, [resource.data]);
  const parseTiers = () =>
    validateTiers(
      tiers.map((t) => ({
        id: t.id,
        minSheets: positiveInteger(t.minimum, "张数门槛"),
        payRateBps: parseRate(t.rate),
      })),
    );
  const patchTier = (id: string, change: Partial<EditableTier>) => {
    setTiers((old) => old.map((t) => (t.id === id ? { ...t, ...change } : t)));
    setPreview(undefined);
  };
  const disabled =
    !access.allowed ||
    submit.pending ||
    !submit.online ||
    resource.loading ||
    !!resource.error;
  const validate = () => {
    parseTiers();
    if (effective && !Number.isFinite(new Date(effective).getTime()))
      throw new Error("请填写有效的生效时间");
  };
  return (
    <div className="admin-growth">
      <PageHeading
        title="A4 满量折扣"
        description="以单笔订单当前有效 A4 打印任务的实际纸张张数为门槛；所有用户均可享用。"
      />
      <AccessState access={access} />
      <ResourceState {...resource} />
      <SubmitState state={submit} />
      {resource.data && (!resource.error || resource.hasData) && (
        <>
          <Panel title={`规则配置 · 当前版本 ${resource.data.version}`}>
            <form
              className="growth-stack"
              onSubmit={(e) => {
                e.preventDefault();
                try {
                  validate();
                  submit.setError("");
                  setConfirm(true);
                } catch (error) {
                  submit.setError(
                    error instanceof Error ? error.message : "配置校验失败",
                  );
                }
              }}
            >
              <label className="growth-check">
                <input
                  type="checkbox"
                  checked={enabled}
                  onChange={(e) => {
                    setEnabled(e.target.checked);
                    setPreview(undefined);
                  }}
                />
                启用 A4 满量折扣
              </label>
              <Notice>
                只对 A4
                打印小计计算一次折扣并按整数分四舍五入。照片、装订、额外重印耗纸不计门槛；系统与优惠券自动择优，同额优先满量优惠。
              </Notice>
              <div className="growth-tier-heading">
                <h3>折扣档位</h3>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={disabled}
                  onClick={() => {
                    setTiers((old) => [
                      ...old,
                      { id: crypto.randomUUID(), minimum: "", rate: "" },
                    ]);
                    setPreview(undefined);
                  }}
                >
                  添加档位
                </Button>
              </div>
              {tiers.length === 0 && (
                <Empty
                  title="尚未配置档位"
                  text="默认不启用优惠。添加张数门槛和折数后，才会计算对应满量减免。"
                />
              )}
              {tiers.map((t, index) => (
                <div className="growth-tier-row" key={t.id}>
                  <Field label={`档位 ${index + 1} · 最低 A4 张数`}>
                    <input
                      required
                      type="number"
                      min="1"
                      step="1"
                      value={t.minimum}
                      onChange={(e) =>
                        patchTier(t.id, { minimum: e.target.value })
                      }
                    />
                  </Field>
                  <Field
                    label="应付折数（0—10 折）"
                    hint="最多三位小数；9 折表示支付原价的 90%。"
                  >
                    <input
                      required
                      inputMode="decimal"
                      value={t.rate}
                      onChange={(e) =>
                        patchTier(t.id, { rate: e.target.value })
                      }
                    />
                  </Field>
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={disabled}
                    onClick={() => {
                      setTiers((old) => old.filter((row) => row.id !== t.id));
                      setPreview(undefined);
                    }}
                  >
                    移除档位 {index + 1}
                  </Button>
                </div>
              ))}
              <div className="growth-form-grid">
                <Field label="生效时间（留空立即生效）">
                  <input
                    type="datetime-local"
                    value={effective}
                    onChange={(e) => setEffective(e.target.value)}
                  />
                </Field>
                <Field label="公开规则说明">
                  <textarea
                    value={description}
                    maxLength={2000}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder="向客户说明满量口径及适用范围"
                  />
                </Field>
              </div>
              <Notice type="warning">
                保存只影响生效后的新报价。已提交订单保持报价快照；待确认订单修改文件、份数或分段后，按当时规则重新报价并由用户确认。
              </Notice>
              <Button disabled={disabled}>检查并保存规则</Button>
            </form>
          </Panel>
          <Panel title="当前编辑规则的影响预览">
            <form
              className="growth-stack"
              onSubmit={(e) => {
                e.preventDefault();
                try {
                  setPreviewError("");
                  setPreview(
                    previewVolume(
                      parseTiers(),
                      positiveInteger(sheets, "本单 A4 张数"),
                      parseCents(subtotal),
                      enabled,
                    ),
                  );
                } catch (error) {
                  setPreview(undefined);
                  setPreviewError(
                    error instanceof Error ? error.message : "预览失败",
                  );
                }
              }}
            >
              <div className="growth-form-grid">
                <Field label="本单 A4 实际用纸张数">
                  <input
                    type="number"
                    required
                    min="1"
                    step="1"
                    value={sheets}
                    onChange={(e) => {
                      setSheets(e.target.value);
                      setPreview(undefined);
                    }}
                  />
                </Field>
                <Field label="A4 打印小计（元）">
                  <input
                    required
                    inputMode="decimal"
                    value={subtotal}
                    onChange={(e) => {
                      setSubtotal(e.target.value);
                      setPreview(undefined);
                    }}
                  />
                </Field>
              </div>
              <Button type="submit" variant="secondary">
                预览新报价影响
              </Button>
              {previewError && (
                <Notice type="error">
                  <span role="alert">{previewError}</span>
                </Notice>
              )}
              {preview !== undefined && (
                <Notice>
                  {preview ? (
                    <>
                      <b>
                        满量可减 {yuan(preview.discountCents)} · A4 应付{" "}
                        {yuan(preview.payableCents)}
                      </b>
                      <p>
                        命中档位{" "}
                        {tiers.findIndex((t) => t.id === preview.tierId) + 1}
                        。实际结算还会与该客户的有效券比较。
                      </p>
                    </>
                  ) : (
                    <b>
                      当前规则未启用、未命中有效优惠或减免为零；满量减免为 0。
                    </b>
                  )}
                </Notice>
              )}
              <small>
                仅为当前编辑规则的 A4
                预览，不保存配置、不生成正式报价、不修改订单。单面页数×份数；双面逐任务向上取整后合计。生效时间不改变此模拟结果。
              </small>
            </form>
          </Panel>
          <Panel title="版本与操作记录">
            <p>
              已保存版本：{resource.data.version} · 生效时间：
              {resource.data.effectiveAt
                ? localDate(resource.data.effectiveAt)
                : "立即生效"}
            </p>
            {resource.data.auditLogs?.length ? (
              <div className="growth-table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>版本</th>
                      <th>管理员</th>
                      <th>时间</th>
                      <th>变更原因</th>
                    </tr>
                  </thead>
                  <tbody>
                    {resource.data.auditLogs.map((log) => (
                      <tr key={log.id}>
                        <td>v{log.version}</td>
                        <td>{log.actor}</td>
                        <td>{localDate(log.time)}</td>
                        <td>{log.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p>
                服务端当前未返回历史操作记录。保存时会提交原因与版本供后台审计。
              </p>
            )}
          </Panel>
        </>
      )}
      {confirm && resource.data && (
        <ConfirmAction
          title="确认保存满量规则"
          pending={submit.pending}
          error={submit.error}
          onClose={() => setConfirm(false)}
          onConfirm={async (reason) => {
            const result = await submit.run(
              () =>
                mutate(
                  "/admin/config/volume-discounts",
                  buildVolumePatch(
                    resource.data!,
                    {
                      enabled,
                      effectiveAt: isoDate(effective),
                      description,
                      tiers: parseTiers(),
                    },
                    reason,
                  ),
                  "PATCH",
                ),
              "满量折扣规则已保存",
            );
            if (result.ok) resource.reload();
            return result.ok;
          }}
        >
          新规则为{enabled ? "启用" : "停用"}，包含 {tiers.length}{" "}
          个档位。仅影响生效后的新报价，不会追溯修改既有订单。
        </ConfirmAction>
      )}
    </div>
  );
}
