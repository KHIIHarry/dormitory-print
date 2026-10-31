import { useEffect, useRef, useState, type CSSProperties } from "react";
import {
  Check,
  MapPin,
  Plus,
  Save,
  Trash2,
  Upload,
} from "lucide-react";
import { DEMO, useStore } from "../lib/store";
import { mutation, request } from "../lib/api";
import { mapPublishError, mapPublishHotspots } from "./map-publish";
import { assetContentPath } from "../v1/admin-api";
import { getIdentityGeneration } from "../growth/client";
import type { AdminCampus, Building, Campus } from "../lib/types";
import {
  Button,
  Empty,
  Field,
  Modal,
  Notice,
  PageHeading,
  Panel,
} from "../components/ui";
import { CampusMap } from "../components/campus-map";
import { useDelayedBusy } from "../components/feedback";
import {
  AssetUpload,
  type AssetUploadState,
} from "./image-upload";

const PALETTE = [
  { value: "#C67C4E", label: "焦糖" },
  { value: "#B5654A", label: "陶土" },
  { value: "#A9784F", label: "赭石" },
  { value: "#7E8A63", label: "橄榄" },
  { value: "#6F8790", label: "雾蓝" },
  { value: "#7A5C4B", label: "可可" },
];

const TYPES = ["宿舍楼", "教学楼", "食堂", "图书馆", "其他"];

const cloneBuildings = (rows: Building[]) => rows.map((row) => ({ ...row }));

const emptyCampus = (input: {
  name: string;
  themeColor: string;
  selectionEnabled: boolean;
  sortOrder: number;
}): AdminCampus => ({
  id: crypto.randomUUID(),
  name: input.name,
  themeColor: input.themeColor,
  selectionEnabled: input.selectionEnabled,
  sortOrder: input.sortOrder,
  version: 1,
  mapVersion: 1,
  mapAssetId: null,
  mapUrl: "",
  publishedAt: null,
  hotspots: [],
});

function asAdminCampus(campus: Campus, buildings: Building[]): AdminCampus {
  return {
    ...campus,
    version: 1,
    hotspots: buildings.filter((item) => item.campusId === campus.id),
  };
}

export function CampusMapAdmin() {
  const { data, setData, toast } = useStore();
  const [campuses, setCampuses] = useState<AdminCampus[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [buildings, setBuildings] = useState<Building[]>([]);
  const [selectedHotspot, setSelectedHotspot] = useState<string>();
  const [image, setImage] = useState("");
  const [mapAssetId, setMapAssetId] = useState<string | undefined>();
  const [mapVersion, setMapVersion] = useState(1);
  const [migrate, setMigrate] = useState(false);
  const [loading, setLoading] = useState(!DEMO);
  const [loadError, setLoadError] = useState("");
  const [mapIssue, setMapIssue] = useState("");

  const [name, setName] = useState("");
  const [themeColor, setThemeColor] = useState(PALETTE[0].value);
  const [selectionEnabled, setSelectionEnabled] = useState(true);
  const [sortOrder, setSortOrder] = useState(0);
  const [settingsIssue, setSettingsIssue] = useState("");
  const [deleteIssue, setDeleteIssue] = useState("");
  const [savingSettings, setSavingSettings] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createColor, setCreateColor] = useState(PALETTE[0].value);
  const [createSelection, setCreateSelection] = useState(true);
  const [createSort, setCreateSort] = useState(0);
  const [createIssue, setCreateIssue] = useState("");
  const [creating, setCreating] = useState(false);

  const [publishOpen, setPublishOpen] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const input = useRef<HTMLInputElement>(null);
  const mapUpload = useRef(new AssetUpload(getIdentityGeneration));
  const [mapUploadState, setMapUploadState] = useState<AssetUploadState>({
    busy: false,
    label: "",
  });
  const uploadHint = useDelayedBusy(mapUploadState.busy, 350);

  const selectedCampus = campuses.find((item) => item.id === selectedId);
  const activeHotspot = buildings.find((item) => item.id === selectedHotspot);
  const assetChanged =
    !!selectedCampus &&
    (mapVersion !== selectedCampus.mapVersion ||
      (mapAssetId || undefined) !== (selectedCampus.mapAssetId || undefined));

  useEffect(() => () => mapUpload.current.cancel(), []);

  const chooseCampus = (campus: AdminCampus) => {
    mapUpload.current.cancel();
    setMapUploadState({ busy: false, label: "" });
    setSelectedId(campus.id);
    setName(campus.name);
    setThemeColor(campus.themeColor);
    setSelectionEnabled(campus.selectionEnabled);
    setSortOrder(campus.sortOrder);
    setBuildings(cloneBuildings(campus.hotspots));
    setSelectedHotspot(campus.hotspots[0]?.id);
    setImage(campus.mapUrl || "");
    setMapAssetId(campus.mapAssetId || undefined);
    setMapVersion(campus.mapVersion);
    setMigrate(false);
    setMapIssue("");
    setSettingsIssue("");
    setDeleteIssue("");
  };

  useEffect(() => {
    if (DEMO) {
      const rows = data.campuses.map((campus) =>
        asAdminCampus(campus, data.buildings),
      );
      setCampuses(rows);
      if (rows[0]) chooseCampus(rows[0]);
      setLoading(false);
      return;
    }
    let active = true;
    setLoading(true);
    request<{ items: AdminCampus[] }>("/admin/campuses")
      .then((result) => {
        if (!active) return;
        const rows = result.items || [];
        setCampuses(rows);
        if (rows[0]) chooseCampus(rows[0]);
      })
      .catch((error) => {
        if (active) setLoadError((error as Error).message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
    // Initial load only; switching campuses is handled locally.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const replaceCampus = (next: AdminCampus) => {
    setCampuses((list) =>
      list.some((item) => item.id === next.id)
        ? list.map((item) => (item.id === next.id ? next : item))
        : [...list, next],
    );
  };

  const syncDemoCampus = (next: AdminCampus, syncMap: boolean) => {
    if (!DEMO) return;
    setData((state) => ({
      ...state,
      campuses: state.campuses.some((item) => item.id === next.id)
        ? state.campuses.map((item) =>
            item.id === next.id
              ? {
                  ...item,
                  name: next.name,
                  themeColor: next.themeColor,
                  selectionEnabled: next.selectionEnabled,
                  sortOrder: next.sortOrder,
                  mapVersion: next.mapVersion,
                  mapAssetId: next.mapAssetId ?? null,
                  mapUrl: next.mapUrl ?? "",
                  publishedAt: next.publishedAt ?? null,
                  items: syncMap
                    ? next.hotspots.filter((hotspot) => hotspot.enabled)
                    : item.items,
                }
              : item,
          )
        : [
            ...state.campuses,
            {
              id: next.id,
              name: next.name,
              themeColor: next.themeColor,
              selectionEnabled: next.selectionEnabled,
              sortOrder: next.sortOrder,
              mapVersion: next.mapVersion,
              mapAssetId: next.mapAssetId ?? null,
              mapUrl: next.mapUrl ?? "",
              publishedAt: next.publishedAt ?? null,
              items: [],
            },
          ],
      buildings: syncMap
        ? [
            ...state.buildings.filter(
              (item) => item.campusId !== next.id,
            ),
            ...next.hotspots,
          ]
        : state.buildings,
    }));
  };

  const applySavedCampus = (next: AdminCampus, syncMap = true) => {
    replaceCampus(next);
    syncDemoCampus(next, syncMap);
    if (next.id !== selectedId) {
      chooseCampus(next);
      return;
    }
    setName(next.name);
    setThemeColor(next.themeColor);
    setSelectionEnabled(next.selectionEnabled);
    setSortOrder(next.sortOrder);
    if (!syncMap) return;
    setBuildings(cloneBuildings(next.hotspots));
    setSelectedHotspot(next.hotspots[0]?.id);
    setImage(next.mapUrl || "");
    setMapAssetId(next.mapAssetId || undefined);
    setMapVersion(next.mapVersion);
    setMigrate(false);
  };

  const createCampus = async () => {
    const trimmed = createName.trim();
    if (!trimmed) {
      setCreateIssue("请填写校区名称");
      return;
    }
    setCreating(true);
    setCreateIssue("");
    try {
      const body = {
        name: trimmed,
        themeColor: createColor,
        selectionEnabled: createSelection,
        sortOrder: createSort,
      };
      const next = DEMO
        ? emptyCampus(body)
        : await mutation<AdminCampus>("/admin/campuses", body);
      if (DEMO) syncDemoCampus(next, false);
      replaceCampus(next);
      chooseCampus(next);
      setCreateOpen(false);
      setCreateName("");
      setCreateIssue("");
      toast("校区已新增");
    } catch (error) {
      setCreateIssue((error as Error).message || "新增校区失败，请重试");
    } finally {
      setCreating(false);
    }
  };

  const saveSettings = async () => {
    if (!selectedCampus) return;
    const trimmed = name.trim();
    if (!trimmed) {
      setSettingsIssue("请填写校区名称");
      return;
    }
    setSavingSettings(true);
    setSettingsIssue("");
    try {
      if (DEMO) {
        const next: AdminCampus = {
          ...selectedCampus,
          name: trimmed,
          themeColor,
          selectionEnabled,
          sortOrder,
          version: selectedCampus.version + 1,
        };
        applySavedCampus(next, false);
        toast("校区设置已保存");
        return;
      }
      const next = await mutation<AdminCampus>(
        `/admin/campuses/${selectedCampus.id}`,
        {
          expectedVersion: selectedCampus.version,
          name: trimmed,
          themeColor,
          selectionEnabled,
          sortOrder,
        },
        "PATCH",
      );
      applySavedCampus(next, false);
      toast("校区设置已保存");
    } catch (error) {
      setSettingsIssue((error as Error).message || "保存失败，请重试");
    } finally {
      setSavingSettings(false);
    }
  };

  const removeCampus = async () => {
    if (!selectedCampus) return;
    setDeleting(true);
    setDeleteIssue("");
    try {
      if (!DEMO)
        await mutation(
          `/admin/campuses/${selectedCampus.id}`,
          {},
          "DELETE",
        );
      else
        setData((state) => ({
          ...state,
          campuses: state.campuses.filter(
            (item) => item.id !== selectedCampus.id,
          ),
          buildings: state.buildings.filter(
            (item) => item.campusId !== selectedCampus.id,
          ),
        }));
      const remaining = campuses.filter((item) => item.id !== selectedCampus.id);
      setCampuses(remaining);
      if (remaining[0]) chooseCampus(remaining[0]);
      else {
        setSelectedId("");
        setBuildings([]);
      }
      setDeleteOpen(false);
      toast("校区已删除");
    } catch (error) {
      setDeleteIssue((error as Error).message || "删除失败，请重试");
    } finally {
      setDeleting(false);
    }
  };

  const uploadMap = async (file: File) => {
    if (!file.type.startsWith("image/")) {
      toast("底图必须是图片文件");
      return;
    }
    if (DEMO) {
      if (image.startsWith("blob:")) URL.revokeObjectURL(image);
      setImage(URL.createObjectURL(file));
      setMapVersion((value) => value + 1);
      setMigrate(false);
      return;
    }
    const form = new FormData();
    form.append("file", file);
    form.append("purpose", "PUBLIC_MAP");
    await mapUpload.current.run(
      async (options) => {
        const result = await request<{ assetId: string }>("/assets", {
          method: "POST",
          body: form,
          ...options,
        });
        return { url: assetContentPath(result.assetId) };
      },
      setMapUploadState,
      (url) => {
        setImage(url);
        const match = url.match(/\/assets\/([^/]+)\/content/);
        if (match) setMapAssetId(decodeURIComponent(match[1]));
        setMapVersion((value) => value + 1);
        setMigrate(false);
      },
      (error) => toast((error as Error).message),
    );
  };

  const patchHotspot = (patch: Partial<Building>) => {
    if (!activeHotspot) return;
    setBuildings((list) =>
      list.map((item) =>
        item.id === activeHotspot.id ? { ...item, ...patch } : item,
      ),
    );
  };

  const addHotspot = (x = 50, y = 50) => {
    if (!selectedCampus) return;
    const id = crypto.randomUUID();
    setBuildings((list) => [
      ...list,
      {
        id,
        name: `新点位 ${list.length + 1}`,
        type: "宿舍楼",
        x: Math.round(Math.max(0, Math.min(100, x))),
        y: Math.round(Math.max(0, Math.min(100, y))),
        enabled: true,
        campusId: selectedCampus.id,
      },
    ]);
    setSelectedHotspot(id);
  };

  const removeHotspot = () => {
    if (!activeHotspot) return;
    setBuildings((list) => list.filter((item) => item.id !== activeHotspot.id));
    setSelectedHotspot(undefined);
  };

  const publish = async () => {
    if (!selectedCampus) return;
    setPublishing(true);
    setMapIssue("");
    try {
      if (DEMO) {
        const next: AdminCampus = {
          ...selectedCampus,
          version: selectedCampus.version + 1,
          mapVersion,
          mapAssetId: mapAssetId || null,
          mapUrl: image,
          publishedAt: new Date().toISOString(),
          hotspots: cloneBuildings(buildings),
        };
        applySavedCampus(next);
        setPublishOpen(false);
        toast("校区地图已发布");
        return;
      }
      const next = await mutation<AdminCampus>(
        `/admin/campuses/${selectedCampus.id}/map`,
        {
          version: mapVersion,
          hotspots: mapPublishHotspots(buildings),
          migrationConfirmed: migrate,
          ...(mapAssetId ? { mapAssetId } : {}),
          expectedVersion: selectedCampus.version,
        },
      );
      applySavedCampus(next);
      setPublishOpen(false);
      toast("校区地图已发布");
    } catch (error) {
      setMapIssue(mapPublishError(error, buildings));
    } finally {
      setPublishing(false);
    }
  };

  return (
    <>
      <PageHeading
        title="校园地图"
        description="按校区配置底图与可配送点位；关闭选点后，该校区在用户下单页会置灰。"
        action={
          <Button onClick={() => setCreateOpen(true)}>
            <Plus size={16} />
            新增校区
          </Button>
        }
      />
      {loadError && (
        <Notice type="error">
          <span role="alert">{loadError}</span>
        </Notice>
      )}
      {loading && <Notice>正在读取校区与地图配置…</Notice>}

      {!loading && !campuses.length && (
        <Empty
          title="还没有校区"
          text="新增第一个校区后，即可上传底图并标记可配送点位。"
          action={
            <Button onClick={() => setCreateOpen(true)}>
              <Plus size={16} />
              新增校区
            </Button>
          }
        />
      )}

      {!!campuses.length && (
        <div className="campus-admin-bar">
          <div className="campus-admin-tabs" role="tablist" aria-label="校区">
            {campuses.map((campus) => (
              <button
                key={campus.id}
                type="button"
                role="tab"
                aria-selected={campus.id === selectedId}
                className={`campus-admin-tab${
                  campus.id === selectedId ? " selected" : ""
                }`}
                style={
                  { "--campus-color": campus.themeColor } as CSSProperties
                }
                onClick={() => chooseCampus(campus)}
              >
                <span className="campus-dot" />
                <span className="campus-admin-tab-name">{campus.name}</span>
                <span className="campus-admin-tab-state">
                  {campus.selectionEnabled ? "开放" : "关闭"}
                </span>
              </button>
            ))}
            <button
              type="button"
              className="campus-admin-add"
              onClick={() => setCreateOpen(true)}
            >
              <Plus size={15} />
              新增
            </button>
          </div>
          <p className="campus-admin-hint">
            切换校区前请先发布当前改动。
          </p>
        </div>
      )}

      {selectedCampus && (
        <>
          <input
            hidden
            ref={input}
            type="file"
            accept="image/*,.svg"
            disabled={mapUploadState.busy}
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (file) void uploadMap(file);
            }}
          />
          <div className="map-editor-grid">
            <Panel className="map-editor-main">
              <div className="panel-heading">
                <h2>楼栋热点编辑</h2>
                <span className="soft-tag warm">
                  底图 v{mapVersion} ·{" "}
                  {selectedCampus.selectionEnabled ? "选点开放" : "选点关闭"}
                </span>
              </div>
              <CampusMap
                buildings={buildings}
                value={selectedHotspot}
                onChange={setSelectedHotspot}
                editable
                onPlace={addHotspot}
                onMove={(id, x, y) =>
                  setBuildings((list) =>
                    list.map((item) =>
                      item.id === id ? { ...item, x, y } : item,
                    ),
                  )
                }
                mapUrl={image}
              />
              <div className="map-editor-caption">
                <span>点击空白处添加点位，拖动点位调整位置；坐标为底图内比例。</span>
                <div className="button-row">
                  <Button
                    variant="secondary"
                    disabled={mapUploadState.busy}
                    onClick={() => input.current?.click()}
                  >
                    <Upload size={15} />
                    {uploadHint ? "正在上传…" : "更换底图"}
                  </Button>
                  <Button
                    onClick={() => {
                      setMapIssue("");
                      setPublishOpen(true);
                    }}
                  >
                    <Save size={15} />
                    预览并发布
                  </Button>
                </div>
              </div>
            </Panel>

            <div className="campus-admin-side">
              <Panel
                title="校区设置"
                action={
                  <span className="soft-tag">v{selectedCampus.version}</span>
                }
              >
                <Field label="校区名称" error={settingsIssue || undefined}>
                  <input
                    value={name}
                    maxLength={120}
                    onChange={(event) => setName(event.target.value)}
                  />
                </Field>
                <div className="field">
                  <label>主题色</label>
                  <div className="campus-palette">
                    {PALETTE.map((color) => (
                      <button
                        key={color.value}
                        type="button"
                        className={`campus-palette-swatch${
                          themeColor === color.value ? " selected" : ""
                        }`}
                        style={{ background: color.value }}
                        aria-label={color.label}
                        aria-pressed={themeColor === color.value}
                        title={color.label}
                        onClick={() => setThemeColor(color.value)}
                      >
                        {themeColor === color.value && <Check size={13} />}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="switch-row">
                  <div>
                    <b>开放选点</b>
                    <p>关闭后，该校区在用户下单页置灰且不可选</p>
                  </div>
                  <button
                    type="button"
                    className={`toggle ${selectionEnabled ? "on" : ""}`}
                    aria-label="切换校区选点状态"
                    aria-pressed={selectionEnabled}
                    onClick={() => setSelectionEnabled((value) => !value)}
                  >
                    <i />
                  </button>
                </div>
                <Field label="排序">
                  <input
                    type="number"
                    min={0}
                    max={10000}
                    value={sortOrder}
                    onChange={(event) =>
                      setSortOrder(Number(event.target.value) || 0)
                    }
                  />
                </Field>
                <div className="button-row">
                  <Button
                    busy={savingSettings}
                    busyLabel="保存中…"
                    onClick={() => void saveSettings()}
                  >
                    保存设置
                  </Button>
                  <Button
                    variant="danger-outline"
                    disabled={campuses.length <= 1}
                    title={
                      campuses.length <= 1 ? "至少保留一个校区" : "删除校区"
                    }
                    onClick={() => {
                      setDeleteIssue("");
                      setDeleteOpen(true);
                    }}
                  >
                    <Trash2 size={15} />
                    删除
                  </Button>
                </div>
              </Panel>

              <Panel title="点位信息">
                {activeHotspot ? (
                  <>
                    <Field label="点位名称">
                      <input
                        value={activeHotspot.name}
                        maxLength={120}
                        onChange={(event) =>
                          patchHotspot({ name: event.target.value })
                        }
                      />
                    </Field>
                    <Field label="类型">
                      <select
                        value={activeHotspot.type}
                        onChange={(event) =>
                          patchHotspot({ type: event.target.value })
                        }
                      >
                        {!TYPES.includes(activeHotspot.type) && (
                          <option value={activeHotspot.type}>
                            {activeHotspot.type}
                          </option>
                        )}
                        {TYPES.map((type) => (
                          <option key={type} value={type}>
                            {type}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <div className="form-grid">
                      <Field label="横向 X">
                        <input
                          type="number"
                          min={0}
                          max={100}
                          step={0.1}
                          value={activeHotspot.x}
                          onChange={(event) =>
                            patchHotspot({ x: Number(event.target.value) || 0 })
                          }
                        />
                      </Field>
                      <Field label="纵向 Y">
                        <input
                          type="number"
                          min={0}
                          max={100}
                          step={0.1}
                          value={activeHotspot.y}
                          onChange={(event) =>
                            patchHotspot({ y: Number(event.target.value) || 0 })
                          }
                        />
                      </Field>
                    </div>
                    <div className="switch-row">
                      <div>
                        <b>允许选择</b>
                        <p>关闭后用户看不到这个点位</p>
                      </div>
                      <button
                        type="button"
                        className={`toggle ${activeHotspot.enabled ? "on" : ""}`}
                        aria-label="切换点位可用状态"
                        aria-pressed={activeHotspot.enabled}
                        onClick={() =>
                          patchHotspot({ enabled: !activeHotspot.enabled })
                        }
                      >
                        <i />
                      </button>
                    </div>
                    <Button variant="danger-outline" onClick={removeHotspot}>
                      <Trash2 size={15} />
                      删除点位
                    </Button>
                  </>
                ) : (
                  <p className="form-hint">
                    点击地图空白处添加点位，或从下方列表中选择一个点位。
                  </p>
                )}
              </Panel>


            </div>
          </div>

          <Panel className="campus-list-panel" title={`点位列表 · ${buildings.length}`}>
                {buildings.length ? (
                  <div className="building-list">
                    {buildings.map((item) => (
                      <button
                        key={item.id}
                        type="button"
                        className={
                          item.id === selectedHotspot ? "selected" : ""
                        }
                        onClick={() => setSelectedHotspot(item.id)}
                      >
                        <MapPin size={14} />
                        <b>{item.name}</b>
                        <span>
                          {item.enabled ? item.type : "已停用"} · {item.x}/
                          {item.y}
                        </span>
                      </button>
                    ))}
                  </div>
                ) : (
                  <p className="form-hint">还没有点位，点击地图空白处添加。</p>
                )}
              </Panel>
        </>
      )}

      {createOpen && (
        <Modal
          title="新增校区"
          onClose={() => {
            setCreateOpen(false);
            setCreateIssue("");
          }}
        >
          <Field label="校区名称" error={createIssue || undefined}>
            <input
              value={createName}
              maxLength={120}
              placeholder="如：东校区"
              onChange={(event) => setCreateName(event.target.value)}
            />
          </Field>
          <div className="field">
            <label>主题色</label>
            <div className="campus-palette">
              {PALETTE.map((color) => (
                <button
                  key={color.value}
                  type="button"
                  className={`campus-palette-swatch${
                    createColor === color.value ? " selected" : ""
                  }`}
                  style={{ background: color.value }}
                  aria-label={color.label}
                  aria-pressed={createColor === color.value}
                  title={color.label}
                  onClick={() => setCreateColor(color.value)}
                >
                  {createColor === color.value && <Check size={13} />}
                </button>
              ))}
            </div>
          </div>
          <div className="switch-row">
            <div>
              <b>开放选点</b>
              <p>关闭后，该校区在用户下单页置灰且不可选</p>
            </div>
            <button
              type="button"
              className={`toggle ${createSelection ? "on" : ""}`}
              aria-label="切换校区选点状态"
              aria-pressed={createSelection}
              onClick={() => setCreateSelection((value) => !value)}
            >
              <i />
            </button>
          </div>
          <Field label="排序">
            <input
              type="number"
              min={0}
              max={10000}
              value={createSort}
              onChange={(event) =>
                setCreateSort(Number(event.target.value) || 0)
              }
            />
          </Field>
          <div className="button-row">
            <Button
              variant="secondary"
              onClick={() => {
                setCreateOpen(false);
                setCreateIssue("");
              }}
            >
              取消
            </Button>
            <Button
              busy={creating}
              busyLabel="正在新增…"
              onClick={() => void createCampus()}
            >
              新增校区
            </Button>
          </div>
        </Modal>
      )}

      {publishOpen && selectedCampus && (
        <Modal
          title="预览并发布"
          onClose={() => {
            setPublishOpen(false);
            setMapIssue("");
          }}
          wide
        >
          <CampusMap buildings={buildings} mapUrl={image} />
          <div className="publish-summary">
            <span>
              校区：<b>{selectedCampus.name}</b>
            </span>
            <span>
              点位：<b>{buildings.length}</b> 个
            </span>
            <span>
              可选：<b>{buildings.filter((item) => item.enabled).length}</b> 个
            </span>
            <span>
              选点：<b>{selectedCampus.selectionEnabled ? "开放" : "关闭"}</b>
            </span>
          </div>
          {assetChanged && (
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={migrate}
                onChange={(event) => setMigrate(event.target.checked)}
              />
              我已核对旧订单的送达点位，确认可以迁移
            </label>
          )}
          {mapIssue && (
            <Notice type="error">
              <span role="alert">{mapIssue}</span>
            </Notice>
          )}
          <div className="button-row">
            <Button
              variant="secondary"
              onClick={() => {
                setPublishOpen(false);
                setMapIssue("");
              }}
            >
              取消
            </Button>
            <Button
              busy={publishing}
              busyLabel="正在发布…"
              disabled={assetChanged && !migrate}
              onClick={() => void publish()}
            >
              确认发布
            </Button>
          </div>
        </Modal>
      )}

      {deleteOpen && selectedCampus && (
        <Modal
          title="删除校区"
          onClose={() => {
            setDeleteOpen(false);
            setDeleteIssue("");
          }}
        >
          <p className="form-hint">
            将删除「{selectedCampus.name}」。仅当校区内没有点位时可以删除；已有订单引用的点位不能删除。
          </p>
          {deleteIssue && (
            <Notice type="error">
              <span role="alert">{deleteIssue}</span>
            </Notice>
          )}
          <div className="button-row">
            <Button
              variant="secondary"
              onClick={() => {
                setDeleteOpen(false);
                setDeleteIssue("");
              }}
            >
              取消
            </Button>
            <Button
              variant="danger"
              busy={deleting}
              busyLabel="正在删除…"
              onClick={() => void removeCampus()}
            >
              <Trash2 size={15} />
              确认删除
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}
