import { mapPublishError, mapPublishHotspots } from "./map-publish";
import { formatDisplayTime } from "../lib/display-format";
import { AuditPage } from "./audit";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Check,
  ChevronRight,
  Download,
  Image,
  MapPin,
  Plus,
  RotateCcw,
  Save,
  Search,
  Settings2,
  ShieldCheck,
  Trash2,
  Upload,
  User,
  Users,
} from "lucide-react";
import { ADMIN, DEMO, useStore } from "../lib/store";
import { mutation, now, request } from "../lib/api";
import { growthRequest } from "../growth/client";
import type { Building, Config, ExceptionItem, Staff } from "../lib/types";
import {
  RETENTION_POLICY,
  assetContentPath,
  configSectionPayload,
  mergeConfigSection,
  sectionVersions,
  type AdminConfig,
  type ConfigSectionName,
} from "../v1/admin-api";
import {
  Badge,
  Button,
  Empty,
  Field,
  Modal,
  Notice,
  PageHeading,
  Panel,
} from "../components/ui";
import { CampusMap } from "../components/campus-map";
import { Progress, useDelayedBusy } from "../components/feedback";
import { getIdentityGeneration } from "../growth/client";
import { useIdentity } from "../growth/identity";
import {
  AssetUpload,
  qrUploadFile,
  type AssetUploadState,
} from "./image-upload";
export function MapEditor() {
  const { data, act, toast, pendingActions, actionIssues } = useStore();
  const [buildings, setBuildings] = useState(
    data.buildings.map((b) => ({ ...b })),
  );
  const [selected, setSelected] = useState<string | undefined>(
    buildings[0]?.id,
  );
  const [image, setImage] = useState(data.config.mapUrl || "");
  const [mapAssetId, setMapAssetId] = useState<string | undefined>(
    (data.config as Record<string, any>).assetId || undefined,
  );
  const [migrate, setMigrate] = useState(false);
  const [version, setVersion] = useState(
    Number((data.config as Record<string, any>).mapVersion) || 1,
  );
  const [publish, setPublish] = useState(false);
  const [mapBusy, setMapBusy] = useState(false);
  const [mapIssue, setMapIssue] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const mapUpload = useRef(new AssetUpload(getIdentityGeneration));
  const [mapUploadState, setMapUploadState] = useState<AssetUploadState>({
    busy: false,
    label: "",
  });
  const mapUploadHint = useDelayedBusy(mapUploadState.busy, 350);
  const [mapLoading, setMapLoading] = useState(!DEMO);
  const [mapLoadError, setMapLoadError] = useState("");
  useEffect(() => () => mapUpload.current.cancel(), []);
  useEffect(() => {
    if (DEMO) return;
    let active = true;
    setMapLoading(true);
    growthRequest<{
      version: number;
      mapAssetId?: string | null;
      mapUrl?: string | null;
      items: Building[];
      hotspots?: Building[];
    }>("/admin/maps")
      .then((result) => {
        if (!active) return;
        const rows = result.items || result.hotspots || [];
        setBuildings(rows.map((row) => ({ ...row })));
        setSelected(rows[0]?.id);
        setImage(result.mapUrl || "");
        setMapAssetId(result.mapAssetId || undefined);
        setVersion(Number(result.version) || 1);
      })
      .catch((error) => {
        if (active) setMapLoadError((error as Error).message);
      })
      .finally(() => {
        if (active) setMapLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);
  const uploadMap = async (file: File) => {
    if (!file.type.startsWith("image/")) {
      toast("底图必须是图片文件");
      return;
    }
    if (DEMO) {
      if (image.startsWith("blob:")) URL.revokeObjectURL(image);
      setImage(URL.createObjectURL(file));
      setMigrate(false);
      setVersion((value) => value + 1);
      return;
    }
    const fd = new FormData();
    fd.append("file", file);
    fd.append("purpose", "PUBLIC_MAP");
    await mapUpload.current.run(
      async (options) => {
        const result = await request<{ assetId: string }>("/assets", {
          method: "POST",
          body: fd,
          ...options,
        });
        return { url: assetContentPath(result.assetId) };
      },
      setMapUploadState,
      (url) => {
        setImage(url);
        const match = url.match(/\/assets\/([^/]+)\/content/);
        if (match) setMapAssetId(decodeURIComponent(match[1]));
        setMigrate(false);
        setVersion((value) => value + 1);
      },
      (error) => toast((error as Error).message),
    );
  };
  const active = buildings.find((b) => b.id === selected);
  const patch = (p: Partial<Building>) =>
    setBuildings((s) => s.map((b) => (b.id === selected ? { ...b, ...p } : b)));
  const add = (x = 50, y = 50) => {
    const id = crypto.randomUUID();
    setBuildings((s) => [
      ...s,
      {
        id,
        name: "新楼栋",
        type: "宿舍楼",
        x: Math.round(x),
        y: Math.round(y),
        enabled: true,
      },
    ]);
    setSelected(id);
  };
  return (
    <>
      <PageHeading
        title="校园地图"
        description="在底图上标记可配送楼栋，让同学更容易找到目的地。"
        action={
          <div className="button-row">
            <Button
              variant="secondary"
              disabled={mapUploadState.busy}
              onClick={() => input.current?.click()}
            >
              <Upload size={16} />
              {mapUploadHint ? "正在上传底图…" : "更换底图"}
            </Button>
            <Button onClick={() => { setMapIssue(""); setPublish(true); }}>
              <Save size={16} />
              预览并发布
            </Button>
          </div>
        }
      />
      {mapLoadError && (
        <Notice type="error">
          <span role="alert">{mapLoadError}</span>
        </Notice>
      )}
      {mapLoading && <Notice>正在读取管理端地图热点与版本…</Notice>}
      <input
        hidden
        ref={input}
        type="file"
        accept="image/*,.svg"
        disabled={mapUploadState.busy}
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (f) void uploadMap(f);
        }}
      />
      <div className="map-editor-grid">
        <Panel className="map-editor-main">
          <div className="panel-heading">
            <h2>楼栋热点编辑</h2>
            <span className="soft-tag">底图 v{version} · 未发布编辑</span>
          </div>
          <CampusMap
            buildings={buildings}
            value={selected}
            onChange={setSelected}
            editable
            onPlace={add}
            onMove={(id, x, y) =>
              setBuildings((items) =>
                items.map((b) => (b.id === id ? { ...b, x, y } : b)),
              )
            }
            mapUrl={image}
          />
          <div className="map-editor-caption">
            <span>坐标为底图内归一化位置，不是实际经纬度</span>
            <Button variant="secondary" onClick={() => add()}>
              <Plus size={16} />
              新增楼栋
            </Button>
          </div>
          {version > 1 && (
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={migrate}
                onChange={(e) => setMigrate(e.target.checked)}
              />
              我已核对新底图上的全部热点位置，确认迁移
            </label>
          )}
        </Panel>
        <Panel title="楼栋信息">
          {active ? (
            <>
              <Field label="楼栋名称">
                <input
                  value={active.name}
                  onChange={(e) => patch({ name: e.target.value })}
                />
              </Field>
              <Field label="楼栋类型">
                <select
                  value={active.type}
                  onChange={(e) => patch({ type: e.target.value })}
                >
                  <option>宿舍楼</option>
                  <option>教学楼</option>
                </select>
              </Field>
              <div className="form-grid">
                <Field label="横向位置（%）">
                  <input
                    type="number"
                    min="0"
                    max="100"
                    value={active.x}
                    onChange={(e) =>
                      patch({
                        x: Math.max(0, Math.min(100, Number(e.target.value))),
                      })
                    }
                  />
                </Field>
                <Field label="纵向位置（%）">
                  <input
                    type="number"
                    min="0"
                    max="100"
                    value={active.y}
                    onChange={(e) =>
                      patch({
                        y: Math.max(0, Math.min(100, Number(e.target.value))),
                      })
                    }
                  />
                </Field>
              </div>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={active.enabled}
                  onChange={(e) => patch({ enabled: e.target.checked })}
                />
                允许配送至此楼栋
              </label>
              <Button
                variant="danger-outline"
                onClick={() => {
                  if (data.orders.some((o) => o.locationId === active.id)) {
                    toast("楼栋仍被订单引用，请先关闭配送，不可直接删除");
                    return;
                  }
                  setBuildings((s) => s.filter((b) => b.id !== active.id));
                  setSelected(undefined);
                }}
              >
                <Trash2 size={15} />
                删除热点
              </Button>
            </>
          ) : (
            <Empty title="选择一个楼栋热点" />
          )}
        </Panel>
      </div>
      <Panel title={`楼栋列表 · ${buildings.length} 个`}>
        <div className="building-list">
          {buildings.map((b) => (
            <button
              className={selected === b.id ? "selected" : ""}
              key={b.id}
              onClick={() => setSelected(b.id)}
            >
              <MapPin size={17} />
              <b>{b.name}</b>
              <span>{b.type}</span>
              <small>
                {b.x.toFixed(1)}%, {b.y.toFixed(1)}%
              </small>
              <span className={`badge ${b.enabled ? "green" : "gray"}`}>
                {b.enabled ? "可配送" : "已关闭"}
              </span>
            </button>
          ))}
        </div>
      </Panel>
      {publish && (
        <Modal title="发布地图与热点" wide onClose={() => setPublish(false)}>
          <CampusMap buildings={buildings} mapUrl={image} />
          <Notice>
            本次将发布 {buildings.length} 个楼栋热点，其中{" "}
            {buildings.filter((b) => b.enabled).length} 个开放配送。
          </Notice>
          {(mapIssue || actionIssues["/admin/maps"]) && (
            <Notice type="error">
              <span role="alert">{mapIssue || actionIssues["/admin/maps"]}</span>
            </Notice>
          )}
          {version > 1 && !migrate && (
            <p className="field-error">
              更换了底图，请先确认所有热点已迁移或重新定位。
            </p>
          )}
          <Button
            busy={mapBusy}
            disabled={mapUploadState.busy || (version > 1 && !migrate)}
            onClick={async () => {
              if (buildings.some((b) => !b.name.trim())) {
                toast("楼栋名称不能为空");
                return;
              }
              setMapBusy(true);
              setMapIssue("");
              const ok = await act(
                "/admin/maps",
                {
                  version,
                  hotspots: mapPublishHotspots(buildings),
                  migrationConfirmed: migrate,
                  ...(mapAssetId ? { mapAssetId } : {}),
                },
                (s) => ({
                  ...s,
                  buildings,
                  config: { ...s.config, mapUrl: image },
                }),
                "POST",
                (result) => {
                  const next = result as { version?: number };
                  if (typeof next.version === "number")
                    setVersion(next.version);
                },
                (error) => {
                  if ((error as { message?: string })?.message === "输入内容不符合要求")
                    setMapIssue(mapPublishError(error, buildings));
                },
              );
              setMapBusy(false);
              if (ok) {
                setPublish(false);
                toast("地图和楼栋热点已发布");
              }
            }}
          >
            确认发布地图
          </Button>
        </Modal>
      )}
    </>
  );
}
export function Settings() {
  const { generation } = useIdentity();
  return <SettingsForm key={generation} />;
}
function SettingsForm() {
  const { data, act, toast, refresh } = useStore();
  const qrInput = useRef<HTMLInputElement>(null);
  const qrUploadLock = useRef(false);
  const [qrDragging, setQrDragging] = useState(false);
  const imageUpload = useRef(new AssetUpload(getIdentityGeneration));
  const [imageState, setImageState] = useState<AssetUploadState>({
    busy: false,
    label: "",
  });
  const imageHint = useDelayedBusy(imageState.busy, 350);
  useEffect(() => () => imageUpload.current.cancel(), []);
  const [tab, setTab] = useState<ConfigSectionName | "decisions">("prices");
  const [config, setConfig] = useState<Record<string, any>>(
    () => structuredClone(data.config) as Record<string, any>,
  );
  const [versions, setVersions] = useState(() =>
    sectionVersions(data.config as Record<string, any>),
  );
  const [review, setReview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState("");
  const patch = (change: Record<string, unknown>) =>
    setConfig((current) => ({ ...current, ...change }));
  const uploadQrFiles = async (files: File[]) => {
    if (qrUploadLock.current || imageState.busy) return;
    let file: File;
    try {
      file = qrUploadFile(files);
    } catch (error) {
      toast((error as Error).message);
      return;
    }
    qrUploadLock.current = true;
    try {
      if (DEMO) {
        const url = URL.createObjectURL(file);
        if (config.qrUrl?.startsWith("blob:"))
          URL.revokeObjectURL(config.qrUrl);
        patch({ qrUrl: url });
      } else {
        const fd = new FormData();
        fd.append("file", file);
        fd.append("purpose", "PUBLIC_WECHAT_QR");
        await imageUpload.current.run(
          async (options) => {
            const result = await request<{ assetId: string }>("/assets", {
              method: "POST",
              body: fd,
              ...options,
            });
            return { url: assetContentPath(result.assetId) };
          },
          setImageState,
          (url) => patch({ qrUrl: url }),
          (error) => toast((error as Error).message),
        );
      }
    } finally {
      qrUploadLock.current = false;
    }
  };
  const tabs: [ConfigSectionName | "decisions", string][] = [
    ["prices", "打印价目"],
    ["services", "服务与限制"],
    ["photo", "打印文件校准"],
    ["contact", "微信与联系"],
    ["announcement", "公告与营业"],
    ["workflow", "工作流与暂停"],
    ["retention", "保留策略"],
    ["decisions", "业务决策"],
  ];
  const priceLabels = [
    ["bwSingle", "A4 黑白单面"],
    ["bwDuplex", "A4 黑白双面"],
    ["colorSingle", "A4 彩色单面"],
    ["colorDuplex", "A4 彩色双面"],
    ["photo", "5 寸照片"],
    ["corner", "左上角单钉（每册）"],
    ["side", "左侧双钉（每册）"],
  ] as const;
  const marginInputs = (
    value: Config["margins"] | undefined,
    onChange: (next: Config["margins"]) => void,
  ) =>
    (["top", "right", "bottom", "left"] as const).map((key, i) => (
      <Field key={key} label={`${["上", "右", "下", "左"][i]}留白（mm）`}>
        <input
          type="number"
          min="0"
          step="0.1"
          value={value?.[key] ?? ""}
          onChange={(e) =>
            onChange({
              ...(value || { top: 3, bottom: 3, left: 3, right: 3 }),
              [key]: Math.max(0, Number(e.target.value)),
            })
          }
        />
      </Field>
    ));
  const versionAvailable = !!versions[tab as ConfigSectionName]?.version;
  const sectionVersion = versions[tab as ConfigSectionName]?.version;
  const save = async () => {
    if (tab === "decisions") return;
    setBusy(true);
    setSaveError("");
    try {
      if (!DEMO && !sectionVersion)
        throw new Error("配置版本尚未加载，请刷新页面后重试");
      const payload = configSectionPayload(
        tab,
        config,
        DEMO ? 1 : sectionVersion!,
      );
      if (DEMO) {
        const saved = { ...payload } as Record<string, unknown>;
        delete saved.expectedVersion;
        const ok = await act(
          `/admin/config/${tab}`,
          payload,
          (state) => ({
            ...state,
            config: {
              ...mergeConfigSection(
                state.config as unknown as AdminConfig,
                tab,
                saved,
              ),
              sections: {
                ...((state.config as Record<string, any>).sections || {}),
                [tab]: { version: 1, ...saved },
              },
            } as any,
          }),
          "PATCH",
        );
        if (!ok) return;
        setConfig(
          (current) =>
            mergeConfigSection(
              current as unknown as AdminConfig,
              tab,
              saved,
            ) as Record<string, any>,
        );
      } else {
        const result = await growthRequest<{ version: number }>(
          `/admin/config/${tab}`,
          { method: "PATCH", body: payload },
        );
        setConfig(
          (current) =>
            mergeConfigSection(
              current as unknown as AdminConfig,
              tab,
              result as Record<string, unknown>,
            ) as Record<string, any>,
        );
        setVersions((current) => ({
          ...current,
          [tab]: { version: result.version },
        }));
        await refresh();
      }
      setReview(false);
      toast("配置已保存，新操作将使用当前版本");
    } catch (error) {
      setSaveError(
        (error as Error).message || "配置保存失败，请核对版本后重试",
      );
    } finally {
      setBusy(false);
    }
  };
  const priceValue = (key: keyof Config["prices"]) =>
    config.prices?.[key] === null || config.prices?.[key] === undefined
      ? ""
      : config.prices[key]! / 100;
  const setPrice = (key: keyof Config["prices"], raw: string) =>
    patch({
      prices: {
        ...(config.prices || {}),
        [key]: raw === "" ? null : Math.max(0, Math.round(Number(raw) * 100)),
      },
    });
  const formats: string[] = Array.isArray(config.formats) ? config.formats : [];
  const toggleFormat = (value: string) =>
    patch({
      formats: formats.includes(value)
        ? formats.filter((item) => item !== value)
        : [...formats, value],
    });
  const pauses = (config.pauses || {}) as Record<string, boolean>;
  const setPause = (key: string, value: boolean) =>
    patch({ pauses: { ...pauses, [key]: value } });
  return (
    <>
      <PageHeading
        title="系统配置"
        description="按分区保存强类型配置，每次提交携带服务端版本并独立审计。"
        action={
          tab !== "decisions" && (
            <Button
              disabled={imageState.busy || (!DEMO && !versionAvailable)}
              onClick={() => setReview(true)}
            >
              <Save size={16} />
              预览并保存
            </Button>
          )
        }
      />
      <div className="settings-admin-layout">
        <nav className="settings-tabs">
          {tabs.map(([v, l]) => (
            <button
              className={tab === v ? "active" : ""}
              key={v}
              onClick={() => {
                setTab(v);
                setSaveError("");
              }}
            >
              {l}
              <ChevronRight size={15} />
            </button>
          ))}
        </nav>
        <Panel>
          {saveError && (
            <Notice type="error">
              <span role="alert">{saveError}</span>
            </Notice>
          )}
          {tab === "prices" && (
            <>
              <h2>打印价目</h2>
              <p className="muted">
                单、双面均按实际纸张张数收费；装订按完整册收费，3 份即 3
                册。留空表示尚未配置，不能当作免费。
              </p>
              <div className="form-grid price-config">
                {priceLabels.map(([key, label]) => (
                  <Field
                    key={key}
                    label={`${label}（元 / ${key === "corner" || key === "side" ? "册" : "张"}）`}
                  >
                    <input
                      type="number"
                      min="0"
                      step="0.01"
                      placeholder="未配置"
                      value={priceValue(key)}
                      onChange={(e) => setPrice(key, e.target.value)}
                    />
                  </Field>
                ))}
              </div>
              <Notice>
                价格修改仅对新报价生效，不改变历史订单。装订已按 Q07—Q09
                启用：按完整册计费，同文件各分段份数必须一致，超过每册实体张数上限时阻止选择。
              </Notice>
              <div className="fixed-rule">
                <Check size={18} />
                <b>校园配送费用固定为 ¥0.00</b>
                <span>V1 已确认规则</span>
              </div>
            </>
          )}
          {tab === "services" && (
            <>
              <h2>服务与上传限制</h2>
              <div className="form-grid">
                <Field label="单订单最多文件数">
                  <input
                    type="number"
                    min="1"
                    max="100"
                    value={config.maxFiles ?? ""}
                    onChange={(e) =>
                      patch({ maxFiles: Math.max(1, Number(e.target.value)) })
                    }
                  />
                </Field>
                <Field label="单文件大小上限（MB）">
                  <input
                    type="number"
                    min="1"
                    value={config.maxMb ?? ""}
                    onChange={(e) =>
                      patch({ maxMb: Math.max(1, Number(e.target.value)) })
                    }
                  />
                </Field>
                <Field label="单订单总大小上限（MB）">
                  <input
                    type="number"
                    min="1"
                    value={config.maxOrderMb ?? ""}
                    onChange={(e) =>
                      patch({ maxOrderMb: Math.max(1, Number(e.target.value)) })
                    }
                  />
                </Field>
                <Field label="单文件最大页数">
                  <input
                    type="number"
                    min="1"
                    value={config.maxPages ?? ""}
                    onChange={(e) =>
                      patch({ maxPages: Math.max(1, Number(e.target.value)) })
                    }
                  />
                </Field>
                <Field label="单任务最大份数">
                  <input
                    type="number"
                    min="1"
                    value={config.maxCopies ?? ""}
                    onChange={(e) =>
                      patch({ maxCopies: Math.max(1, Number(e.target.value)) })
                    }
                  />
                </Field>
                <Field label="展开后最大页数">
                  <input
                    type="number"
                    min="1"
                    value={config.maxExpandedPages ?? ""}
                    onChange={(e) =>
                      patch({
                        maxExpandedPages: Math.max(1, Number(e.target.value)),
                      })
                    }
                  />
                </Field>
                <Field label="图片最大像素">
                  <input
                    type="number"
                    min="1"
                    value={config.maxPixels ?? ""}
                    onChange={(e) =>
                      patch({ maxPixels: Math.max(1, Number(e.target.value)) })
                    }
                  />
                </Field>
                <Field label="最终 PDF 上限（MB）">
                  <input
                    type="number"
                    min="1"
                    value={config.maxFinalMb ?? ""}
                    onChange={(e) =>
                      patch({ maxFinalMb: Math.max(1, Number(e.target.value)) })
                    }
                  />
                </Field>
              </div>
              <h3>支持格式</h3>
              <div className="format-grid">
                {[
                  ["pdf", "PDF"],
                  ["doc", "DOC"],
                  ["docx", "DOCX"],
                  ["ppt", "PPT"],
                  ["pptx", "PPTX"],
                  ["xls", "XLS"],
                  ["xlsx", "XLSX"],
                  ["jpg", "JPG"],
                  ["jpeg", "JPEG"],
                  ["png", "PNG"],
                  ["webp", "WebP"],
                  ["heic", "HEIC"],
                ].map(([key, label]) => (
                  <label className="checkbox-label" key={key}>
                    <input
                      type="checkbox"
                      checked={formats.includes(key)}
                      onChange={() => toggleFormat(key)}
                    />
                    {label}
                  </label>
                ))}
              </div>
              <h3>装订规则（Q07—Q09）</h3>
              <p className="muted">
                按完整册计费，两种方式独立价格；同文件各分段份数不一致时禁止装订，不自动拆册。上限为每册实体张数，留空表示未设置。
              </p>
              <div className="form-grid">
                {(["CORNER", "SIDE"] as const).map((kind) => (
                  <Field
                    key={kind}
                    label={`${kind === "CORNER" ? "左上角单钉" : "左侧双钉"}每册张数上限`}
                  >
                    <input
                      type="number"
                      min="1"
                      placeholder="未设置"
                      value={config.bindingLimits?.[kind] ?? ""}
                      onChange={(e) =>
                        patch({
                          bindingLimits: {
                            ...(config.bindingLimits || {}),
                            [kind]:
                              e.target.value === ""
                                ? null
                                : Math.max(1, Number(e.target.value)),
                          },
                        })
                      }
                    />
                  </Field>
                ))}
              </div>
              <div className="switch-row">
                <div>
                  <b>开放照片打印</b>
                  <p>启用前须完成实际纸张校准</p>
                </div>
                <button
                  className={`toggle ${config.photoEnabled ? "on" : ""}`}
                  aria-label="切换照片打印"
                  onClick={() => patch({ photoEnabled: !config.photoEnabled })}
                >
                  <i />
                </button>
              </div>
              <div className="switch-row">
                <div>
                  <b>照片纸张已校准</b>
                  <p>未校准时不启用照片服务</p>
                </div>
                <button
                  className={`toggle ${config.photoCalibrated ? "on" : ""}`}
                  aria-label="切换照片校准状态"
                  onClick={() =>
                    patch({ photoCalibrated: !config.photoCalibrated })
                  }
                >
                  <i />
                </button>
              </div>
              <div className="fixed-rule">
                <Check size={18} />
                <b>全部生产完成后必须人工确认装袋</b>
              </div>
            </>
          )}
          {tab === "photo" && (
            <>
              <h2>打印文件校准</h2>
              <p className="muted">
                按实际纸张和打印机的可打印范围校准，不提供无边距承诺。未保存真实校准值前请勿依赖默认留白。
              </p>
              <h3>A4 文件打印</h3>
              <p className="muted">
                A4 纸张固定为 210 × 297 mm。填写打印机无法打印的边缘宽度，文件内容会整体收进留白以内。
              </p>
              <div className="form-grid">
                {marginInputs(config.a4Margins, (a4Margins) =>
                  patch({ a4Margins }),
                )}
              </div>
              <Notice>
                A4 四边初始留白均为 3 mm，可分别调整；保存后仅影响新报价。
              </Notice>
              <h3>5 寸照片校准</h3>
              <p className="muted">
                尺寸以实际纸张和打印机校准为准，不提供无边距承诺。未保存真实校准值前不要启用照片服务。
              </p>
              <div className="form-grid">
                <Field label="纸张宽度（mm）">
                  <input
                    type="number"
                    min="1"
                    value={config.photoWidthMm ?? ""}
                    onChange={(e) =>
                      patch({ photoWidthMm: Number(e.target.value) })
                    }
                  />
                </Field>
                <Field label="纸张高度（mm）">
                  <input
                    type="number"
                    min="1"
                    value={config.photoHeightMm ?? ""}
                    onChange={(e) =>
                      patch({ photoHeightMm: Number(e.target.value) })
                    }
                  />
                </Field>
                {marginInputs(config.margins, (margins) =>
                  patch({ margins }),
                )}
              </div>
              <Notice>
                四边初始留白均为 3 mm，可分别调整；保存后仅影响新报价。
              </Notice>
            </>
          )}
          {tab === "contact" && (
            <>
              <h2>微信与联系</h2>
              <div className="form-grid">
                <Field label="管理员微信号">
                  <input
                    value={config.wechat ?? ""}
                    onChange={(e) => patch({ wechat: e.target.value })}
                    placeholder="填写真实微信号"
                  />
                </Field>
                <Field label="联系电话">
                  <input
                    value={config.phone ?? ""}
                    onChange={(e) => patch({ phone: e.target.value })}
                    placeholder="填写真实联系电话"
                  />
                </Field>
                <Field label="公开展示名称">
                  <input
                    value={config.displayName ?? ""}
                    onChange={(e) => patch({ displayName: e.target.value })}
                    placeholder="DormPrint"
                  />
                </Field>
              </div>
              <Field label="联系引导文案">
                <textarea
                  rows={3}
                  value={config.guide ?? ""}
                  onChange={(e) => patch({ guide: e.target.value })}
                />
              </Field>
              <Field label="管理员微信二维码">
                <div
                  className="qr-upload-region"
                  onDragOver={(e) => {
                    e.preventDefault();
                    if (!imageState.busy) {
                      e.dataTransfer.dropEffect = "copy";
                      setQrDragging(true);
                    }
                  }}
                  onDragLeave={(e) => {
                    if (
                      !e.currentTarget.contains(e.relatedTarget as Node | null)
                    )
                      setQrDragging(false);
                  }}
                  onDrop={(e) => {
                    e.preventDefault();
                    setQrDragging(false);
                    void uploadQrFiles(Array.from(e.dataTransfer.files));
                  }}
                >
                  <button
                    type="button"
                    className={`qr-upload ${qrDragging ? "dragging" : ""}`}
                    aria-label="上传或更换管理员微信二维码"
                    disabled={imageState.busy}
                    onClick={() => {
                      if (!qrUploadLock.current) qrInput.current?.click();
                    }}
                  >
                    {imageHint && (
                      <span style={{ flexShrink: 0, width: 35, height: 35 }}>
                        <Progress
                          ring
                          value={imageState.value}
                          label={imageState.label}
                        />
                      </span>
                    )}
                    <span className="qr-upload-mark">
                      <img
                        className="wechat-logo"
                        src="/assets/wechat.svg"
                        alt=""
                        aria-hidden="true"
                        width={48}
                        height={48}
                      />
                    </span>
                    {config.qrUrl && (
                      <img src={config.qrUrl} alt="已上传的管理员二维码" />
                    )}
                    <span className="qr-upload-copy">
                      <strong>
                        {qrDragging
                          ? "松开以上传二维码"
                          : config.qrUrl
                            ? "更换管理员微信二维码"
                            : "上传管理员微信二维码"}
                      </strong>
                      <small>点击选择，或将一张图片拖到这里</small>
                    </span>
                  </button>
                  <input
                    hidden
                    ref={qrInput}
                    type="file"
                    disabled={imageState.busy}
                    accept="image/*,.svg"
                    aria-label="选择管理员微信二维码图片"
                    onChange={(e) => {
                      const files = Array.from(e.target.files || []);
                      e.target.value = "";
                      if (files.length) void uploadQrFiles(files);
                    }}
                  />
                </div>
              </Field>
              <Notice>
                仅展示添加好友与线下付款引导，不接入微信支付，不自动发送微信消息。二维码必须来自已上传的公开资产，保存失败时保留旧配置。
              </Notice>
            </>
          )}
          {tab === "announcement" && (
            <>
              <h2>公告与营业状态</h2>
              <div className="switch-row">
                <div>
                  <b>开放接单</b>
                  <p>暂停后仍可查询历史订单</p>
                </div>
                <button
                  className={`toggle ${config.serviceEnabled ? "on" : ""}`}
                  aria-label="切换营业状态"
                  onClick={() =>
                    patch({ serviceEnabled: !config.serviceEnabled })
                  }
                >
                  <i />
                </button>
              </div>
              <Field label="首页公告">
                <textarea
                  rows={5}
                  value={config.announcement ?? ""}
                  onChange={(e) => patch({ announcement: e.target.value })}
                  placeholder="如营业说明、节假日安排等"
                />
              </Field>
            </>
          )}
          {tab === "workflow" && (
            <>
              <h2>工作流与四类暂停</h2>
              <p className="muted">
                工作流参数独立于价目与服务限制保存。暂停只阻止新的对应动作，不抹除历史事实，也不冒充取消或成功。
              </p>
              <div className="form-grid">
                <Field label="待付款自动关闭（分钟）">
                  <input
                    type="number"
                    min="5"
                    max="10080"
                    placeholder="留空保持当前期限"
                    value={config.unpaidTimeoutMinutes ?? ""}
                    onChange={(e) =>
                      patch({
                        unpaidTimeoutMinutes:
                          e.target.value === ""
                            ? null
                            : Math.max(5, Number(e.target.value)),
                      })
                    }
                  />
                </Field>
                <Field label="定位最大时效（秒）">
                  <input
                    type="number"
                    min="10"
                    max="3600"
                    value={config.locationMaxAgeSeconds ?? ""}
                    onChange={(e) =>
                      patch({
                        locationMaxAgeSeconds: Math.max(
                          10,
                          Number(e.target.value),
                        ),
                      })
                    }
                  />
                </Field>
                <Field label="定位最大误差（米）">
                  <input
                    type="number"
                    min="1"
                    max="1000"
                    value={config.locationMaxAccuracyMeters ?? ""}
                    onChange={(e) =>
                      patch({
                        locationMaxAccuracyMeters: Math.max(
                          1,
                          Number(e.target.value),
                        ),
                      })
                    }
                  />
                </Field>
                <Field label="提取码长度">
                  <input
                    type="number"
                    min="5"
                    max="12"
                    value={config.shortCodeLength ?? ""}
                    onChange={(e) =>
                      patch({
                        shortCodeLength: Math.max(5, Number(e.target.value)),
                      })
                    }
                  />
                </Field>
              </div>
              <h3>四类暂停</h3>
              {[
                ["newOrders", "暂停新订单", "停止新提交，已下单订单继续履约"],
                [
                  "fileProcessing",
                  "暂停文件处理",
                  "停止新的转换与最终 PDF 生成",
                ],
                [
                  "production",
                  "暂停生产",
                  "阻止新的实际开印/打印尝试，既有事实保留",
                ],
                [
                  "delivery",
                  "暂停配送",
                  "阻止批次开始配送，不抹除已开始的执行",
                ],
              ].map(([key, title, hint]) => (
                <div className="switch-row" key={key}>
                  <div>
                    <b>{title}</b>
                    <p>{hint}</p>
                  </div>
                  <button
                    className={`toggle ${pauses[key] ? "on" : ""}`}
                    aria-label={title}
                    onClick={() => setPause(key, !pauses[key])}
                  >
                    <i />
                  </button>
                </div>
              ))}
              <Notice>
                暂停原因由后端审计记录；当前工作流 DTO 尚未包含独立原因字段，需
                Codex
                补齐后再在表单收集。自动关单期限留空只表示保持当前值，清除能力也需后端
                DTO 支持。
              </Notice>
            </>
          )}
          {tab === "retention" && (
            <>
              <h2>保留与清理策略</h2>
              <p className="muted">
                按 Q29/Q36 与 R01—R05
                定版：业务账目、审计与账号记录不按年龄自动删除；仅下列文件/草稿/导出按固定期限清理，未完成或争议未结不普通清理。
              </p>
              <div className="retention-grid">
                {[
                  ["原件与中间 PDF", `${RETENTION_POLICY.sourceDays} 天`],
                  ["最终 PDF", `${RETENTION_POLICY.finalDays} 天`],
                  ["交付照片与定位", `${RETENTION_POLICY.proofDays} 天`],
                  ["未下单草稿", `${RETENTION_POLICY.draftHours} 小时`],
                  ["导出文件", `${RETENTION_POLICY.exportHours} 小时`],
                  ["回收站恢复", `${RETENTION_POLICY.trashHours} 小时`],
                ].map(([label, value]) => (
                  <div key={label}>
                    <b>{label}</b>
                    <span>{value}</span>
                  </div>
                ))}
              </div>
              <Notice>
                以上为已批准固定策略，不能在本页修改；自动清理例外由维护任务执行并保留最小审计。备份轮换与异地恢复按部署配置单独验收。
              </Notice>
            </>
          )}
          {tab === "decisions" && (
            <>
              <h2>V1 业务决策清单</h2>
              <Notice>
                V1 已定版：D-01 付款后原单内容变更明确排除；D-02
                指定纸张补打、D-04 装订、D-07 打印队列均已批准；D-06C
                手机号/短码公开找回仍排除。
              </Notice>
              <div className="decision-list">
                {[
                  ["D-01", "付款后原单内容变更", "已排除：取消/退款后重新下单"],
                  ["D-02", "整任务与实体纸补打", "已批准"],
                  ["D-03", "文件与备份保留期", "已批准：按 Q29/Q36 固定"],
                  ["D-04", "装订按册与厚度上限", "已批准：Q07—Q09"],
                  ["D-05", "已付款订单删除与退款", "已批准：履约结束后软删除"],
                  ["D-06", "游客链接与邮箱账号", "D-06A/B 已批准；D-06C 排除"],
                  ["D-07", "人工打印队列", "已批准：逐项下一项"],
                  [
                    "D-08",
                    "技术栈与部署",
                    "已批准：FastAPI/PostgreSQL/Redis/Celery",
                  ],
                ].map(([id, title, status]) => (
                  <div key={id}>
                    <b>{id}</b>
                    <span>{title}</span>
                    <Badge status={status} />
                  </div>
                ))}
              </div>
            </>
          )}
        </Panel>
      </div>
      {review && (
        <Modal title="确认配置变更" onClose={() => !busy && setReview(false)}>
          <Notice>
            {tab === "prices"
              ? "价格变更仅影响新报价，既有订单金额和确认的 PDF 不会改变。"
              : tab === "workflow"
                ? "暂停立即影响新的对应动作；不取消已存在的打印、配送或账目事实。"
                : "保存仅提交当前分区，并携带服务端版本；其他分区不会被覆盖。"}
          </Notice>
          <p>
            配置分区：{tabs.find(([value]) => value === tab)?.[1]} · 版本{" "}
            {DEMO ? "演示" : (sectionVersion ?? "未加载")}
          </p>
          {saveError && (
            <Notice type="error">
              <span role="alert">{saveError}</span>
            </Notice>
          )}
          <Button busy={busy} disabled={busy} onClick={save}>
            {busy ? "正在保存…" : "确认保存当前分区"}
          </Button>
        </Modal>
      )}
    </>
  );
}
export function StaffPage() {
  const { data, act, toast, pendingActions } = useStore();
  const [edit, setEdit] = useState<Partial<Staff> | null>(null);
  const [password, setPassword] = useState("");
  const [reset, setReset] = useState<Staff | null>(null);
  return (
    <>
      <PageHeading
        title="员工管理"
        description="为管理员和配送员分别授予需要的业务权限。"
        action={
          <Button
            onClick={() => {
              setEdit({ role: "COURIER", enabled: true });
              setPassword("");
            }}
          >
            <Plus size={17} />
            新增员工
          </Button>
        }
      />
      <Notice>
        配送员只能访问分配给自己的任务、交付与异常提交，不具备收款、打印文件或调度权限。
      </Notice>
      <Panel>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>员工</th>
                <th>工作账号</th>
                <th>联系电话</th>
                <th>角色</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {data.staff.map((s) => (
                <tr key={s.id}>
                  <td>
                    <div className="staff-name">
                      <span className="avatar">{s.name.slice(0, 1)}</span>
                      <b>{s.name}</b>
                    </div>
                  </td>
                  <td>{s.username}</td>
                  <td>{s.phone}</td>
                  <td>
                    <span
                      className={`soft-tag ${s.role === "ADMIN" ? "warm" : ""}`}
                    >
                      {s.role === "ADMIN" ? "管理员" : "配送员"}
                    </span>
                  </td>
                  <td>
                    <Badge status={s.enabled ? "COMPLETED" : "CANCELLED"} />
                  </td>
                  <td>
                    <div className="button-row">
                      <button
                        className="text-button"
                        onClick={() => setEdit(s)}
                      >
                        编辑
                      </button>
                      <button
                        className="text-button"
                        onClick={() => {
                          setReset(s);
                          setPassword("");
                        }}
                      >
                        重置凭证
                      </button>
                      <button
                        className="text-button"
                        disabled={s.role === "ADMIN"}
                        onClick={() =>
                          act(
                            `/admin/staff/${s.id}`,
                            { enabled: !s.enabled },
                            (d) => ({
                              ...d,
                              staff: d.staff.map((x) =>
                                x.id === s.id
                                  ? { ...x, enabled: !x.enabled }
                                  : x,
                              ),
                            }),
                            "PATCH",
                          )
                        }
                      >
                        {s.enabled ? "停用" : "启用"}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      {edit && (
        <Modal
          title={edit.id ? "编辑员工" : "新增员工"}
          onClose={() => setEdit(null)}
        >
          <Field label="姓名">
            <input
              value={edit.name || ""}
              onChange={(e) => setEdit({ ...edit, name: e.target.value })}
            />
          </Field>
          <Field label="工作账号">
            <input
              value={edit.username || ""}
              autoComplete="off"
              onChange={(e) => setEdit({ ...edit, username: e.target.value })}
            />
          </Field>
          <Field label="联系电话">
            <input
              value={edit.phone || ""}
              onChange={(e) => setEdit({ ...edit, phone: e.target.value })}
            />
          </Field>
          <Field label="员工角色">
            <select
              value={edit.role}
              onChange={(e) =>
                setEdit({ ...edit, role: e.target.value as Staff["role"] })
              }
            >
              <option value="COURIER">配送员</option>
              <option value="ADMIN">管理员</option>
            </select>
          </Field>
          {!edit.id && (
            <Field label="初始密码">
              <input
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </Field>
          )}
          <Button
            busy={
              pendingActions[
                edit.id ? `/admin/staff/${edit.id}` : "/admin/staff"
              ]
            }
            onClick={async () => {
              if (
                !edit.name?.trim() ||
                !edit.username?.trim() ||
                (!edit.id && password.length < 8)
              ) {
                toast("姓名、账号必填，初始密码至少 8 位");
                return;
              }
              const id = edit.id || crypto.randomUUID();
              if (
                await act(
                  edit.id ? `/admin/staff/${id}` : "/admin/staff",
                  { ...edit, password: password || undefined },
                  (d) => ({
                    ...d,
                    staff: edit.id
                      ? d.staff.map((s) =>
                          s.id === id ? ({ ...s, ...edit } as Staff) : s,
                        )
                      : [...d.staff, { ...edit, id } as Staff],
                  }),
                  edit.id ? "PATCH" : "POST",
                )
              ) {
                setEdit(null);
                setPassword("");
                toast("员工资料已保存");
              }
            }}
          >
            保存员工
          </Button>
        </Modal>
      )}
      {reset && (
        <Modal
          title={`重置 ${reset.name} 的凭证`}
          onClose={() => setReset(null)}
        >
          <Notice>
            重置后请通过安全渠道将新凭证交给本人，不会在员工列表中回显。
          </Notice>
          <Field label="新密码（至少 8 位）">
            <input
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>
          <Button
            busy={pendingActions[`/admin/staff/${reset.id}`]}
            disabled={password.length < 8}
            onClick={async () => {
              if (
                await act(
                  `/admin/staff/${reset.id}`,
                  { password },
                  (d) => d,
                  "PATCH",
                )
              ) {
                setReset(null);
                setPassword("");
                toast("员工凭证已重置");
              }
            }}
          >
            确认重置凭证
          </Button>
        </Modal>
      )}
    </>
  );
}
export function Exceptions() {
  const { data, act, toast, pendingActions } = useStore();
  const [edit, setEdit] = useState<Record<string, any> | null>(null);
  const [reason, setReason] = useState("");
  const [decision, setDecision] = useState<"APPROVED" | "REJECTED">("APPROVED");
  const [filter, setFilter] = useState("");
  const path = (row: Record<string, any>) =>
    row.type === "LOCATION_EXCEPTION"
      ? `/admin/exceptions/${row.id}/decision`
      : `/admin/exceptions/${row.id}/resolve`;
  return (
    <>
      <PageHeading
        title="异常中心"
        description="配送异常与定位例外由服务端登记；管理员只处理审批/重派，不伪造已送达或打印完成。"
      />
      <div className="tabs">
        {[
          ["", "全部"],
          ["OPEN", "待处理"],
          ["APPROVED", "已批准"],
          ["REJECTED", "已拒绝"],
          ["RESOLVED", "已解决"],
        ].map(([value, label]) => (
          <button
            className={filter === value ? "active" : ""}
            key={value}
            onClick={() => setFilter(value)}
          >
            {label}
          </button>
        ))}
      </div>
      <Notice>
        配送员在配送中提交异常；资料损坏/缺失需先在打印中心完成生产纠错，不能只重新安排配送。
      </Notice>
      <div className="exception-list">
        {data.exceptions
          .filter((row) => !filter || row.status === filter)
          .map((row) => (
            <Panel key={row.id}>
              <div className="exception-heading">
                <span className="exception-icon">!</span>
                <div>
                  <h2>{row.type}</h2>
                  <p>
                    {formatDisplayTime((row as Record<string, any>).createdAt || row.time)} ·{" "}
                    <Link
                      className="text-link"
                      to={`${ADMIN}/orders/${row.orderId}`}
                    >
                      {data.orders.find((order) => order.id === row.orderId)
                        ?.pickupCode || "关联订单"}
                    </Link>
                  </p>
                </div>
                <Badge status={row.status} />
              </div>
              <p className="exception-reason">{row.reason}</p>
              <div className="task-card-actions">
                <span className="muted">
                  {(row as Record<string, any>).blocksDelivery
                    ? "阻断配送"
                    : "不阻断配送"}
                  {(row as Record<string, any>).blocksProduction
                    ? " · 需生产纠错"
                    : ""}
                </span>
                <Button
                  variant="secondary"
                  onClick={() => {
                    setEdit(row as unknown as Record<string, any>);
                    setReason(row.reason);
                    setDecision("APPROVED");
                  }}
                >
                  查看与处理
                  <ChevronRight size={16} />
                </Button>
              </div>
            </Panel>
          ))}
      </div>
      {!data.exceptions.length && (
        <Panel>
          <Empty title="暂无异常记录" />
        </Panel>
      )}
      {edit && (
        <Modal title="处理配送异常" onClose={() => setEdit(null)}>
          <p className="muted">
            {edit.type === "LOCATION_EXCEPTION"
              ? "定位例外仅批准当前订单/配送执行/版本的申请；改派或版本变化后失效。"
              : "配送异常解决只允许重新安排配送；不会自动标记已送达，也不能绕过生产纠错。"}
          </p>
          {edit.type === "LOCATION_EXCEPTION" ? (
            <Field label="审批结果">
              <select
                value={decision}
                onChange={(event) =>
                  setDecision(event.target.value as "APPROVED" | "REJECTED")
                }
              >
                <option value="APPROVED">批准定位例外</option>
                <option value="REJECTED">拒绝申请</option>
              </select>
            </Field>
          ) : (
            <Notice>
              解决动作：重新安排配送（REDELIVER）。若资料损坏/缺失，请先到打印中心建立补打并解决生产异常。
            </Notice>
          )}
          <Field label="处理原因（必填）">
            <textarea
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              rows={4}
            />
          </Field>
          <Button
            busy={!!pendingActions[path(edit)]}
            onClick={async () => {
              if (!reason.trim()) {
                toast("请填写处理原因");
                return;
              }
              const target = path(edit);
              const body =
                edit.type === "LOCATION_EXCEPTION"
                  ? {
                      expectedVersion: edit.version,
                      decision,
                      reason,
                    }
                  : {
                      expectedVersion: edit.version,
                      resolution: "REDELIVER",
                      reason,
                    };
              if (
                await act(target, body, (state) => ({
                  ...state,
                  exceptions: state.exceptions.map((row) =>
                    row.id === edit.id
                      ? {
                          ...row,
                          status:
                            edit.type === "LOCATION_EXCEPTION"
                              ? decision
                              : "RESOLVED",
                        }
                      : row,
                  ),
                }))
              ) {
                setEdit(null);
                toast("异常处理已记录");
              }
            }}
          >
            提交处理结果
          </Button>
        </Modal>
      )}
    </>
  );
}
const TRASH_TYPE_LABELS: Record<string, string> = {
  ORDER: "订单",
  BATCH: "配送批次",
  ASSET: "资产",
  CUSTOMER_ACCOUNT: "客户账号",
};

export function Trash() {
  const { data, act, toast, pendingActions } = useStore();
  const [item, setItem] = useState<(typeof data.trash)[number] | null>(null);
  const [mode, setMode] = useState("");
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [preview, setPreview] = useState(false);
  return (
    <>
      <PageHeading
        title="回收站"
        description="误删可在 24 小时内恢复，过期后由后台清理。"
      />
      <Notice>
        订单与配送批次删除后会进入这里，24 小时内可恢复。履约、售后、退款或工资未结清的对象删除前会被拦下；正在被有效订单引用的资产不可清理。
      </Notice>
      <Panel>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>对象名称 / 类型</th>
                <th>删除人</th>
                <th>删除时间</th>
                <th>删除原因</th>
                <th>剩余恢复时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {data.trash.map((t) => {
                const remaining = Math.max(
                  0,
                  24 - (Date.now() - new Date(t.deletedAt).getTime()) / 3600000,
                );
                return (
                  <tr key={t.id}>
                    <td>
                      <b>{t.name}</b>
                      <small>{TRASH_TYPE_LABELS[t.type] ?? t.type}</small>
                    </td>
                    <td>{t.by}</td>
                    <td>
                      {new Date(t.deletedAt).toLocaleString("zh-CN", {
                        timeZone: "Asia/Shanghai",
                      })}
                    </td>
                    <td>{t.reason}</td>
                    <td>
                      {remaining > 0
                        ? `${Math.floor(remaining)} 小时 ${Math.floor((remaining % 1) * 60)} 分`
                        : "已过期"}
                    </td>
                    <td>
                      <div className="button-row">
                        <button
                          className="text-button"
                          disabled={remaining <= 0}
                          onClick={() => {
                            setItem(t);
                            setMode("restore");
                            setPreview(false);
                            setReason("");
                            setConfirmed(false);
                          }}
                        >
                          恢复
                        </button>
                        <button
                          className="text-button red-text"
                          onClick={() => {
                            setItem(t);
                            setMode("purge");
                            setReason("");
                            setConfirmed(false);
                          }}
                        >
                          永久清理
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {!data.trash.length && (
          <Empty title="回收站是空的" text="删除的可恢复对象会暂存于此。" />
        )}
      </Panel>
      {item && (
        <Modal
          title={`${mode === "restore" ? "恢复" : "永久清理"} · ${item.name}`}
          onClose={() => setItem(null)}
        >
          {mode === "restore" ? (
            <Notice>
              {preview
                ? "恢复预览：恢复批次为规划中，先不关联原订单；避免覆盖新的配送分配。恢复后可重新添加合法订单。"
                : "恢复前先检查与现有对象和关联的冲突。"}
            </Notice>
          ) : (
            <Notice type="error">
              永久清理后无法恢复。仅在不存在有效引用时可以执行。
            </Notice>
          )}
          <Field label="操作原因">
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </Field>
          {mode === "purge" && (
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
              />
              我确认永久清理“{item.name}”，且了解无法恢复
            </label>
          )}
          <Button
            busy={pendingActions[`/admin/trash/${item.id}/${mode}`]}
            variant={mode === "purge" ? "danger" : ""}
            disabled={!reason.trim() || (mode === "purge" && !confirmed)}
            onClick={async () => {
              if (mode === "restore" && !preview) {
                if (!DEMO) {
                  try {
                    await mutation(
                      `/admin/trash/${item.id}/restore-preview`,
                      {},
                    );
                  } catch (e) {
                    toast((e as Error).message);
                    return;
                  }
                }
                setPreview(true);
                return;
              }
              if (
                await act(
                  `/admin/trash/${item.id}/${mode}`,
                  {
                    reason,
                    expectedVersion: (item as Record<string, any>).version ?? 1,
                  },
                  (s) => ({
                    ...s,
                    trash: s.trash.filter((t) => t.id !== item.id),
                    batches:
                      mode === "restore" && item.batchSnapshot
                        ? [
                            ...s.batches,
                            {
                              ...item.batchSnapshot,
                              orderIds: [],
                              status: "PLANNED",
                              version: item.batchSnapshot.version + 1,
                            },
                          ]
                        : s.batches,
                  }),
                )
              ) {
                setItem(null);
                toast(
                  mode === "restore"
                    ? "对象已恢复，关联需再次核对"
                    : "对象已永久清理",
                );
              }
            }}
          >
            {mode === "restore" && !preview
              ? "预览恢复冲突"
              : mode === "restore"
                ? "确认恢复对象"
                : "永久清理此对象"}
          </Button>
        </Modal>
      )}
    </>
  );
}
export function Audit() {
  return <AuditPage />;
}
