"use client";
import { StationObservations } from "./station-observations";
import { DeviceInventory } from "./device-inventory";
import { usePageRefresh } from "./page-refresh";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { apiFetch } from "../lib/api-fetch";
import type { paths } from "@ronghub/api-schema";
import { useCapability } from "./permissions";
import {
  businessStates,
  locationReasons,
  MapPoint,
  MapQueryResponse,
  runtimeStates,
  StationPoint,
  stationTypes,
  Viewport,
} from "./station-map-types";
const MapCanvas = dynamic(() => import("./station-map-canvas"), {
  ssr: false,
  loading: () => <p className="empty">正在加载地图组件…</p>,
});
import { hasBasemap } from "./station-map-config";
const empty = {
  q: "",
  district: "",
  operator_id: "",
  station_type: "",
  status: "",
  runtime_state: "",
  min_power: "",
  max_power: "",
  stars: "",
  usage_type: "",
  construction_site: "",
  site_state: "",
  source_sync_state: "",
};
type MapResponse = MapQueryResponse;
type ExportQuery = NonNullable<
  paths["/api/v1/map/export"]["get"]["parameters"]["query"]
>;
async function read<T>(
  request: Promise<{ data?: T; error?: unknown }>,
): Promise<T> {
  const result = await request;
  if (!result.data) throw new Error(apiError(result.error, "地图数据读取失败"));
  return result.data;
}
export function StationMapPanel() {
  const [classifications, setClassifications] = useState<
    Partial<ApiSchemas["MapClassifications"]>
  >({});
  const canRating = useCapability("ratings.read");
  const canExport = useCapability("stations.export");
  const [exportQuery, setExportQuery] = useState<ExportQuery | null>(null);
  const [exportBusy, setExportBusy] = useState(false);
  const [exportError, setExportError] = useState("");
  const exportController = useRef<AbortController | null>(null);
  useEffect(() => () => exportController.current?.abort(), []);
  const [draft, setDraft] = useState(empty),
    [filters, setFilters] = useState(empty),
    [view, setView] = useState<Viewport | null>(null),
    [data, setData] = useState<MapResponse | null>(null),
    [rows, setRows] = useState<StationPoint[]>([]),
    [total, setTotal] = useState(0),
    [page, setPage] = useState(1),
    [location, setLocation] = useState("view"),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [refresh, setRefresh] = useState(0),
    [reset, setReset] = useState(0),
    [operators, setOperators] = useState<{ id: number; name: string }[]>([]),
    [selected, setSelected] = useState<StationPoint | null>(null),
    [detailLoading, setDetailLoading] = useState(false),
    [focus, setFocus] = useState<{
      latitude: number;
      longitude: number;
      nonce: number;
    } | null>(null),
    [tileError, setTileError] = useState(false);
  const summaryRef = useRef<HTMLElement>(null);
  const mapSectionRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (selected)
      summaryRef.current?.scrollIntoView({
        block: "start",
        behavior: "smooth",
      });
  }, [selected?.id]);
  const sequence = useRef(0),
    selectedSequence = useRef(0);
  const onViewport = useCallback((next: Viewport) => {
    setView((previous) =>
      JSON.stringify(previous) === JSON.stringify(next) ? previous : next,
    );
    setPage(1);
  }, []);
  const onTileError = useCallback(() => setTileError(true), []);
  useEffect(() => {
    read(typedApi.GET("/api/v1/map/options", { cache: "no-store" }))
      .then((data) => setClassifications(data.classifications))
      .catch((e) => setError(e.message));
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible")
        setRefresh((value) => value + 1);
    }, 60_000);
    return () => window.clearInterval(timer);
  }, []);
  const pageRefreshRevision = usePageRefresh();
  useEffect(() => {
    read(typedApi.GET("/api/v1/operators", { cache: "no-store" }))
      .then((d) => setOperators(d.items))
      .catch((e) => setError(e.message));
  }, [pageRefreshRevision]);
  useEffect(() => {
    if (!view) return;
    const controller = new AbortController(),
      id = ++sequence.current;
    setLoading(true);
    setError("");
    setExportError("");
    const query = {
      ...filters,
      ...view,
      operator_id: filters.operator_id
        ? Number(filters.operator_id)
        : undefined,
      stars: filters.stars ? Number(filters.stars) : undefined,
      min_power: filters.min_power || undefined,
      max_power: filters.max_power || undefined,
    };
    Promise.all([
      read<MapResponse>(
        typedApi.GET("/api/v1/map", {
          params: { query },
          cache: "no-store",
          signal: controller.signal,
        }),
      ),
      read(
        typedApi.GET("/api/v1/map/stations", {
          params: { query: { ...query, location, page } },
          cache: "no-store",
          signal: controller.signal,
        }),
      ),
    ])
      .then(([map, list]) => {
        if (id === sequence.current) {
          setData(map);
          setRows(list.items);
          setTotal(list.total);
          setExportQuery({ ...query, location });
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted && id === sequence.current) {
          setError(e.message);
          setData(null);
          setRows([]);
          setTotal(0);
          setExportQuery(null);
        }
      })
      .finally(() => {
        if (id === sequence.current) setLoading(false);
      });
    return () => controller.abort();
  }, [filters, view, page, location, refresh, pageRefreshRevision]);
  async function exportList() {
    if (!exportQuery || loading || exportController.current) return;
    const controller = new AbortController();
    exportController.current = controller;
    setExportBusy(true);
    setExportError("");
    try {
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries(exportQuery)) {
        if (value !== undefined && value !== null && value !== "")
          params.set(key, String(value));
      }
      const response = await apiFetch("/api/v1/map/export?" + params, {
        cache: "no-store",
        signal: controller.signal,
      });
      if (!response.ok)
        throw new Error(
          apiError(await response.json(), "场站导出失败，请重试"),
        );
      if (!response.headers.get("content-type")?.startsWith("text/csv"))
        throw new Error("未收到CSV文件，请重试");
      const file = await response.blob();
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(file);
      const link = document.createElement("a");
      link.href = url;
      link.download = "ronghub-map-stations.csv";
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (reason) {
      if (!controller.signal.aborted)
        setExportError(
          reason instanceof Error ? reason.message : "场站导出失败，请重试",
        );
    } finally {
      if (exportController.current === controller) {
        exportController.current = null;
        setExportBusy(false);
      }
    }
  }
  async function open(point: StationPoint) {
    const id = ++selectedSequence.current;
    setSelected(point);
    setDetailLoading(true);
    setError("");
    if (!point.unplottable_reason)
      setFocus({
        latitude: Number(point.latitude),
        longitude: Number(point.longitude),
        nonce: Date.now(),
      });
    try {
      const station = await read(
        typedApi.GET("/api/v1/stations/{station_id}", {
          params: { path: { station_id: point.id } },
          cache: "no-store",
        }),
      );
      if (id === selectedSequence.current) {
        setSelected({ ...point, ...station });
      }
    } catch (e) {
      if (id === selectedSequence.current)
        setError(e instanceof Error ? e.message : "场站详情读取失败");
    } finally {
      if (id === selectedSequence.current) setDetailLoading(false);
    }
  }
  return (
    <div className="business-stack">
      <section className="panel">
        <div className="panel-title">
          <div>
            <h2>场站分布与筛选</h2>
            <p>
              业务状态与运行快照分开展示；点位和数量均按当前账号授权范围查询
            </p>
          </div>
          <button
            className="outline"
            disabled={loading}
            onClick={() => setRefresh((r) => r + 1)}
          >
            刷新地图数据
          </button>
        </div>
        <form
          className="business-form"
          onSubmit={(e) => {
            e.preventDefault();
            selectedSequence.current++;
            setSelected(null);
            setPage(1);
            setFilters({ ...draft });
          }}
        >
          <label>
            场站名称
            <input
              aria-label="地图场站名称"
              value={draft.q}
              maxLength={150}
              onChange={(e) => setDraft({ ...draft, q: e.target.value })}
            />
          </label>
          <label>
            区域
            <input
              aria-label="地图区域"
              value={draft.district}
              maxLength={50}
              placeholder="授权范围全部"
              onChange={(e) => setDraft({ ...draft, district: e.target.value })}
            />
          </label>
          <label>
            运营商
            <select
              aria-label="地图运营商"
              value={draft.operator_id}
              onChange={(e) =>
                setDraft({ ...draft, operator_id: e.target.value })
              }
            >
              <option value="">全部授权企业</option>
              {operators.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </select>
          </label>
          {(
            [
              ["station_type", "场站类型", stationTypes],
              ["status", "业务状态", businessStates],
              ["runtime_state", "运行快照", runtimeStates],
            ] as const
          ).map(([key, label, options]) => (
            <label key={key}>
              {label}
              <select
                aria-label={`地图${label}`}
                value={draft[key]}
                onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
              >
                <option value="">全部</option>
                {Object.entries(options).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
          ))}
          {(
            [
              ["usage_type", "用途"],
              ["construction_site", "建设场所"],
              ["site_state", "站点运营状态"],
              ["source_sync_state", "来源同步状态"],
            ] as const
          ).map(([key, label]) => (
            <label key={key}>
              {label}
              <select
                aria-label={`地图${label}`}
                value={draft[key]}
                onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
              >
                <option value="">全部</option>
                {Object.entries(classifications[key] ?? {}).map(
                  ([value, text]) => (
                    <option key={value} value={value}>
                      {text}
                    </option>
                  ),
                )}
              </select>
            </label>
          ))}
          <label>
            最小功率kW
            <input
              aria-label="地图最小功率"
              type="number"
              min={0}
              step="0.01"
              value={draft.min_power}
              onChange={(e) =>
                setDraft({ ...draft, min_power: e.target.value })
              }
            />
          </label>
          <label>
            最大功率kW
            <input
              aria-label="地图最大功率"
              type="number"
              min={0}
              step="0.01"
              value={draft.max_power}
              onChange={(e) =>
                setDraft({ ...draft, max_power: e.target.value })
              }
            />
          </label>
          {canRating && (
            <label>
              有效星级
              <select
                aria-label="地图有效星级"
                value={draft.stars}
                onChange={(e) => setDraft({ ...draft, stars: e.target.value })}
              >
                <option value="">不限</option>
                {[0, 1, 2, 3, 4, 5].map((n) => (
                  <option key={n} value={n}>
                    {n}星
                  </option>
                ))}
              </select>
            </label>
          )}
          <button className="outline" disabled={loading}>
            筛选地图
          </button>
          <button
            className="outline"
            type="button"
            onClick={() => {
              setDraft(empty);
              setFilters(empty);
              setPage(1);
              selectedSequence.current++;
              setSelected(null);
            }}
          >
            重置筛选
          </button>
        </form>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </section>
      <section className="panel" ref={mapSectionRef}>
        <div className="panel-title">
          <div>
            <h2>授权场站一张图</h2>
            <p>
              {!hasBasemap
                ? "坐标视图，尚未配置客户底图；不含道路或行政边界"
                : tileError
                  ? "底图暂不可用，点位及查询仍可使用"
                  : "WGS84坐标视图"}{" "}
              · 拖动/缩放及每60秒重新查询快照
            </p>
          </div>
          <button className="outline" onClick={() => setReset((r) => r + 1)}>
            回到福州视窗
          </button>
        </div>
        <div className="map-summary">
          <span>
            筛选结果 <b>{data?.matching ?? "—"}</b>
          </span>
          <span>
            可定位 <b>{data?.mappable ?? "—"}</b>
          </span>
          <span>
            当前视窗 <b>{data?.in_view ?? "—"}</b>
          </span>
          <button
            className="outline"
            onClick={() => {
              setLocation("unlocated");
              setPage(1);
            }}
          >
            待定位 / 待适配 {data?.unplottable ?? "—"}
          </button>
          {loading && <small role="status">正在更新…</small>}
        </div>
        {data?.truncated && (
          <p className="demo">
            当前视窗聚合过多，请放大；分页列表仍提供完整查询结果。
          </p>
        )}
        <div className="map-workspace">
          <div>
            <MapCanvas
              items={data?.items ?? []}
              selectedId={selected?.id ?? null}
              onSelect={open}
              onViewport={onViewport}
              focus={focus}
              reset={reset}
              onTileError={onTileError}
            />
            <div className="map-legend">
              {Object.entries(runtimeStates).map(([k, v]) => (
                <span key={k}>
                  <i className={`map-dot state-${k}`} />
                  {v}
                </span>
              ))}
              <small>聚合数字表示站点数；同坐标点可用列表选择。</small>
            </div>
          </div>
          <div
            role="complementary"
            className="map-station-list"
            aria-label="地图场站列表"
          >
            <label>
              列表范围
              <select
                aria-label="地图列表范围"
                value={location}
                onChange={(e) => {
                  setLocation(e.target.value);
                  setPage(1);
                }}
              >
                <option value="view">当前视窗</option>
                <option value="all">全部筛选结果</option>
                <option value="unlocated">待定位 / 待适配</option>
              </select>
            </label>
            <p className="muted">
              共 {total} 个场站 · 第 {page} 页
            </p>
            {canExport && (
              <div className="map-export">
                <button
                  className="outline"
                  disabled={loading || exportBusy || !exportQuery}
                  onClick={exportList}
                >
                  {exportBusy ? "正在导出…" : "导出当前范围 CSV"}
                </button>
                <small>按已查询条件及列表范围导出全部结果，最多10000条。</small>
                {exportError && (
                  <p className="error" role="alert">
                    {exportError}
                  </p>
                )}
              </div>
            )}
            {rows.map((point) => (
              <button
                className={`map-station-row ${selected?.id === point.id ? "selected" : ""}`}
                key={point.id}
                onClick={() => open(point)}
                aria-label={`查看地图场站：${point.name}`}
              >
                <b>{point.name}</b>
                <small>
                  {point.operator_name} · {point.district}
                </small>
                <span>
                  {businessStates[point.status]} ·{" "}
                  {runtimeStates[point.runtime_state]}
                </span>
                <small>
                  {point.unplottable_reason
                    ? locationReasons[point.unplottable_reason]
                    : point.location_is_demo
                      ? "演示坐标"
                      : "来源坐标"}{" "}
                  · {point.power_kw}kW
                </small>
              </button>
            ))}
            {!rows.length && !loading && (
              <p className="empty">当前范围没有场站</p>
            )}
            <div className="action-row">
              <button
                className="outline"
                aria-label="地图列表上一页"
                disabled={page <= 1 || loading}
                onClick={() => setPage((p) => p - 1)}
              >
                上一页
              </button>
              <button
                className="outline"
                aria-label="地图列表下一页"
                disabled={page * 20 >= total || loading}
                onClick={() => setPage((p) => p + 1)}
              >
                下一页
              </button>
            </div>
          </div>
        </div>
        <p className="map-data-note">
          {data && (
            <>
              查询时间：{new Date(data.generated_at).toLocaleString("zh-CN")}
              （非源数据采集时间）。
            </>
          )}
          运行状态来自最近同步枪口快照，非实时监控；缺失不推断为离线。仅WGS84且投影范围内的来源坐标落点，其他坐标保留在待适配列表。
        </p>
      </section>
      {selected && (
        <section className="panel" ref={summaryRef}>
          <div className="panel-title">
            <div>
              <h2>{selected.name} · 场站摘要</h2>
              <p>
                {selected.operator_name} · {selected.district}
              </p>
            </div>
            <button
              className="outline"
              onClick={() => {
                selectedSequence.current++;
                setSelected(null);
                mapSectionRef.current?.scrollIntoView({
                  block: "start",
                  behavior: "smooth",
                });
              }}
            >
              关闭地图摘要
            </button>
          </div>
          {detailLoading ? (
            <p className="empty">读取场站设备快照…</p>
          ) : (
            <div className="detail-grid">
              <div>
                <p>{selected.address}</p>
                <p>
                  {stationTypes[selected.station_type]} ·{" "}
                  {businessStates[selected.status]} · {selected.power_kw}kW
                </p>
                <p>
                  用途：
                  {classifications.usage_type?.[selected.usage_type] ??
                    "未提供"}{" "}
                  · 建设场所：
                  {classifications.construction_site?.[
                    selected.construction_site
                  ] ?? "未提供"}
                </p>
                <p>
                  站点运营：
                  {classifications.site_state?.[selected.site_state] ??
                    "未提供"}{" "}
                  · 同步：
                  {classifications.source_sync_state?.[
                    selected.source_sync_state
                  ] ?? "未提供"}
                </p>
                <p>
                  来源首次接入时间：
                  {selected.source_first_connected_at
                    ? new Date(
                        selected.source_first_connected_at,
                      ).toLocaleString("zh-CN")
                    : "未提供"}
                </p>
                <p>
                  运行快照：{runtimeStates[selected.runtime_state]}；已观察{" "}
                  {selected.observed_connectors} / 台账 {selected.connectors}{" "}
                  个接口
                </p>
                {canRating && (
                  <p>
                    有效星级：
                    {selected.effective_stars === null
                      ? "未评定或有效期未确认"
                      : `${selected.effective_stars}星`}
                  </p>
                )}
                <p>
                  坐标：{selected.longitude ?? "未提供"}，
                  {selected.latitude ?? "未提供"}（{selected.coordinate_system}
                  ）{selected.location_is_demo ? " · 演示坐标" : ""}
                </p>
                <p className="muted">
                  场站同步时间：
                  {new Date(selected.synced_at).toLocaleString("zh-CN")}
                </p>
              </div>
              <div>
                <StationObservations
                  key={`observations-${selected.id}`}
                  stationId={selected.id}
                />
                <DeviceInventory key={selected.id} stationId={selected.id} />
              </div>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
