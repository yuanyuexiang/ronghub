"use client";
import { useEffect, useRef, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { usePageRefresh } from "./page-refresh";
import { useCapability } from "./permissions";
import { RuntimePanel } from "./runtime-panel";
import { SubsidyDeviceDays } from "./subsidy-device-days";
import { StationMapPanel } from "./station-map-panel";
import { DashboardOperations } from "./dashboard-operations";
function useRead<T>(
  query: object,
  read: (signal: AbortSignal) => Promise<{ data?: T; error?: unknown }>,
) {
  const loader = useRef(read);
  loader.current = read;
  const revision = usePageRefresh();
  const [data, setData] = useState<T | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true);
  useEffect(() => {
    const c = new AbortController();
    setLoading(true);
    setError("");
    setData(null);
    loader
      .current(c.signal)
      .then((r) => {
        if (!r.data) throw Error(apiError(r.error, "读取失败"));
        if (!c.signal.aborted) setData(r.data);
      })
      .catch((e) => {
        if (!c.signal.aborted)
          setError(e instanceof Error ? e.message : "读取失败");
      })
      .finally(() => {
        if (!c.signal.aborted) setLoading(false);
      });
    return () => c.abort();
  }, [query, revision]);
  return { data, error, loading };
}
const stamp = (value: string) =>
  new Date(value).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour12: false,
  });
function Pages({
  page,
  total,
  onChange,
  disabled,
}: {
  page: number;
  total: number;
  onChange: (page: number) => void;
  disabled: boolean;
}) {
  return (
    <div className="publication-pagination">
      <button
        className="outline"
        disabled={disabled || page <= 1}
        onClick={() => onChange(page - 1)}
      >
        上一页
      </button>
      <span>
        第{page}页 · 共{total}条
      </span>
      <button
        className="outline"
        disabled={disabled || page * 20 >= total}
        onClick={() => onChange(page + 1)}
      >
        下一页
      </button>
    </div>
  );
}
function Feedback({ error, loading }: { error: string; loading: boolean }) {
  return (
    <>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {loading && (
        <p className="empty" role="status">
          正在读取页面数据…
        </p>
      )}
    </>
  );
}
function saveBlob(data: Blob, name: string) {
  const url = URL.createObjectURL(data),
    a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function MonitoringWorkspace() {
  const [draft, setDraft] = useState({
      q: "",
      district: "",
      element: "operating",
    }),
    [query, setQuery] = useState({ ...draft, page: 1, refresh: 0 }),
    [auto, setAuto] = useState(false),
    [exportError, setExportError] = useState(""),
    [exporting, setExporting] = useState(false);
  const canExport = useCapability("stations.export");
  const element =
    query.element === "electrical"
      ? "electrical"
      : query.element === "environmental"
        ? "environmental"
        : "operating";
  const { data, error, loading } = useRead<ApiSchemas["MonitorPage"]>(
    query,
    (signal) =>
      typedApi.GET("/api/v1/page-views/monitoring", {
        params: { query: { ...query, element, page_size: 20 } },
        signal,
        cache: "no-store",
      }),
  );
  useEffect(() => {
    if (data && query.page > Math.max(1, Math.ceil(data.total / 20)))
      setQuery((q) => ({
        ...q,
        page: Math.max(1, Math.ceil(data.total / 20)),
      }));
  }, [data, query.page]);
  useEffect(() => {
    if (!auto) return;
    const timer = setInterval(
      () => setQuery((q) => ({ ...q, refresh: q.refresh + 1 })),
      60000,
    );
    return () => clearInterval(timer);
  }, [auto]);
  async function download() {
    setExporting(true);
    setExportError("");
    try {
      const r = await typedApi.GET("/api/v1/page-views/monitoring/export", {
        params: { query: { q: query.q, district: query.district, element } },
        parseAs: "blob",
      });
      if (!r.data) throw Error(apiError(r.error, "导出失败"));
      saveBlob(r.data, "ronghub-monitoring.csv");
    } catch (e) {
      setExportError(e instanceof Error ? e.message : "导出失败");
    } finally {
      setExporting(false);
    }
  }
  return (
    <div className="business-stack">
      <section className="panel prototype-monitor" aria-label="动态监控工作区">
        <div className="panel-title">
          <div>
            <h2>监控数据</h2>
            <p>运营设备、枪口来源状态与台账同步时间；电气/环境测点待接入</p>
          </div>
          <label>
            <input
              type="checkbox"
              checked={auto}
              onChange={(e) => setAuto(e.target.checked)}
            />
            每分钟刷新
          </label>
        </div>
        <div className="prototype-metric-row">
          {[
            ["充电站", data ? (data.station_types.charging ?? 0) : "—"],
            ["换电站", data ? (data.station_types.swapping ?? 0) : "—"],
            ["运营设备", data?.equipment_count ?? "—"],
            ["电气设备", "待接入"],
            ["非电环境", "待接入"],
          ].map(([k, v]) => (
            <article key={k}>
              <span>{k}</span>
              <strong>{v}</strong>
            </article>
          ))}
        </div>
        <form
          className="prototype-query"
          onSubmit={(e) => {
            e.preventDefault();
            setQuery({ ...draft, page: 1, refresh: 0 });
          }}
        >
          <label>
            场站名称
            <input
              aria-label="监控场站名称"
              value={draft.q}
              onChange={(e) => setDraft({ ...draft, q: e.target.value })}
              maxLength={150}
            />
          </label>
          <label>
            所属区域
            <input
              aria-label="监控所属区域"
              value={draft.district}
              onChange={(e) => setDraft({ ...draft, district: e.target.value })}
              maxLength={50}
            />
          </label>
          <label>
            要素类别
            <select
              aria-label="监控要素类别"
              value={draft.element}
              onChange={(e) => setDraft({ ...draft, element: e.target.value })}
            >
              <option value="operating">运营设备</option>
              <option value="electrical">电气设备</option>
              <option value="environmental">非电环境</option>
            </select>
          </label>
          <button className="primary" disabled={loading}>
            查询监控
          </button>
          <button
            type="button"
            className="outline"
            disabled={loading}
            onClick={() => {
              const reset = { q: "", district: "", element: "operating" };
              setDraft(reset);
              setQuery({ ...reset, page: 1, refresh: 0 });
            }}
          >
            重置监控
          </button>
          {canExport && (
            <button
              type="button"
              className="outline"
              disabled={loading || exporting}
              onClick={() => void download()}
            >
              导出监控清单
            </button>
          )}
        </form>
        <Feedback error={error || exportError} loading={loading} />
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {[
                  "场站名称",
                  "要素类别",
                  "设备名称 / 编码",
                  "枪口编码",
                  "来源状态",
                  "更新时间（台账）",
                  "数据来源",
                ].map((x) => (
                  <th key={x}>{x}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data?.items.map((row) => (
                <tr key={row.id}>
                  <td>
                    {row.station_name}
                    <small>{row.operator_name}</small>
                  </td>
                  <td>运营设备</td>
                  <td>
                    {row.equipment_name}
                    <small>{row.equipment_code}</small>
                  </td>
                  <td>{row.connector_code ?? "未提供"}</td>
                  <td>
                    {(
                      {
                        unknown: "未知",
                        available: "可用",
                        preparing: "准备中",
                        unavailable: "不可用",
                        faulted: "故障",
                        reserved: "已预约",
                        finishing: "结束中",
                        suspended_ev: "车辆暂停",
                        suspended_evse: "设备暂停",
                        charging: "充电",
                        idle: "空闲",
                        offline: "离线",
                        fault: "故障",
                        occupied: "占用",
                      } as Record<string, string>
                    )[row.state] ?? row.state}
                  </td>
                  <td>{stamp(row.synced_at)}</td>
                  <td>{row.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!loading && data?.total === 0 && (
          <p className="empty">
            {element === "operating"
              ? "暂无符合条件的运营设备"
              : "该类测点尚未接入，未以运营设备替代。"}
          </p>
        )}
        <Pages
          page={query.page}
          total={data?.total ?? 0}
          disabled={loading}
          onChange={(page) => setQuery({ ...query, page })}
        />
      </section>
      <details className="prototype-related">
        <summary>历史运行与质量观测</summary>
        <RuntimePanel />
      </details>
    </div>
  );
}
export function DailyEnergyWorkspace() {
  const today = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
  }).format(new Date());
  const [draft, setDraft] = useState({
      start_date: today.slice(0, 8) + "01",
      end_date: today,
      source: "",
    }),
    [query, setQuery] = useState({ ...draft, page: 1 });
  const canExport = useCapability("subsidy.export");
  const [exporting, setExporting] = useState(false),
    [exportError, setExportError] = useState("");
  const { data, error, loading } = useRead<ApiSchemas["DailyEnergyPage"]>(
    query,
    (signal) =>
      typedApi.GET("/api/v1/page-views/daily-energy", {
        params: { query: { ...query, page_size: 20 } },
        signal,
        cache: "no-store",
      }),
  );
  useEffect(() => {
    if (data && query.page > Math.max(1, Math.ceil(data.total / 20)))
      setQuery((q) => ({
        ...q,
        page: Math.max(1, Math.ceil(data.total / 20)),
      }));
  }, [data, query.page]);
  async function download() {
    setExporting(true);
    setExportError("");
    try {
      const r = await typedApi.GET("/api/v1/page-views/daily-energy/export", {
        params: { query },
        parseAs: "blob",
      });
      if (!r.data) throw Error(apiError(r.error, "导出失败"));
      saveBlob(r.data, "ronghub-daily-energy.csv");
    } catch (e) {
      setExportError(e instanceof Error ? e.message : "导出失败");
    } finally {
      setExporting(false);
    }
  }
  return (
    <div className="business-stack">
      <section className="panel" aria-label="电量与补贴日汇总">
        <div className="panel-title">
          <div>
            <h2>电量数据</h2>
            <p>按业务日和独立来源汇总；补贴不按申请区间平均拆分到日期</p>
          </div>
        </div>
        <form
          className="prototype-query"
          onSubmit={(e) => {
            e.preventDefault();
            setQuery({ ...draft, page: 1 });
          }}
        >
          <label>
            开始日期
            <input
              aria-label="日汇总开始日期"
              type="date"
              required
              value={draft.start_date}
              onChange={(e) =>
                setDraft({ ...draft, start_date: e.target.value })
              }
            />
          </label>
          <label>
            结束日期
            <input
              aria-label="日汇总结束日期"
              type="date"
              required
              value={draft.end_date}
              onChange={(e) => setDraft({ ...draft, end_date: e.target.value })}
            />
          </label>
          <label>
            独立来源
            <input
              aria-label="日汇总来源"
              maxLength={50}
              value={draft.source}
              onChange={(e) => setDraft({ ...draft, source: e.target.value })}
            />
          </label>
          <button className="primary" disabled={loading}>
            查询日汇总
          </button>
          {canExport && (
            <button
              type="button"
              className="outline"
              disabled={loading || exporting}
              onClick={() => void download()}
            >
              导出日汇总
            </button>
          )}
        </form>
        <Feedback error={error || exportError} loading={loading} />
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>日期</th>
                <th>独立来源</th>
                <th>电量汇总（度）</th>
                <th>补贴汇总（元）</th>
              </tr>
            </thead>
            <tbody>
              {data?.items.map((row) => (
                <tr key={row.day + row.source}>
                  <td>{row.day}</td>
                  <td>{row.source}</td>
                  <td>{row.kwh}</td>
                  <td>未留存按日生成记录</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!loading && data?.total === 0 && (
          <p className="empty">当前条件下暂无日汇总记录</p>
        )}
        <Pages
          page={query.page}
          total={data?.total ?? 0}
          disabled={loading}
          onChange={(page) => setQuery({ ...query, page })}
        />
      </section>
      <details className="prototype-related">
        <summary>设备日电量与核算证据</summary>
        <SubsidyDeviceDays />
      </details>
    </div>
  );
}
export function OperatorOverview({
  summary,
  onNavigate,
}: {
  summary: ApiSchemas["DashboardSummary"];
  onNavigate: (tab: string) => void;
}) {
  const canStations = useCapability("stations.read"),
    canStats = useCapability("statistics.read");
  const canNotices = useCapability("content.read"),
    canApplications = useCapability("applications.read"),
    canInspections = useCapability("inspections.read"),
    canHazards = useCapability("hazards.read");
  const [page, setPage] = useState(1);
  return (
    <div
      className="business-stack operator-overview"
      aria-label="运营商接入总览"
    >
      <section className="panel">
        <div className="panel-title">
          <div>
            <h2>场站与累计观测概览</h2>
            <p>本企业授权范围；留存观测不代表已认证的全生命周期累计</p>
          </div>
        </div>
        <div className="prototype-metric-row">
          {[
            ["本企业场站", summary.stations],
            ["已留存电量(度)", summary.energy_total ?? "未提供"],
            ["已核定补贴(元)", summary.subsidy_total ?? "无权限"],
            ["待审核申请", summary.applications_pending ?? "无权限"],
          ].map(([k, v]) => (
            <article key={String(k)}>
              <span>{k}</span>
              <strong>
                {typeof v === "string" && /^-?\d+\.\d+$/.test(v)
                  ? v.replace(/0+$/, "").replace(/\.$/, "")
                  : String(v)}
              </strong>
            </article>
          ))}
        </div>
        <div className="prototype-shortcuts">
          {[
            ["监管通知", "公告与通知", canNotices],
            ["审核结果", "场站申请", canApplications],
            ["巡检通知", "整改任务", canInspections],
            ["隐患督办", "隐患督办", canHazards],
          ]
            .filter(([, , allowed]) => allowed)
            .map(([k, v]) => (
              <button
                className="outline"
                key={String(v)}
                onClick={() => onNavigate(String(v))}
              >
                {String(k)}
              </button>
            ))}
        </div>
      </section>
      <div className="operator-overview-main">
        {canStations && (
          <div className="operator-home-map">
            <StationMapPanel compact />
          </div>
        )}
        {canStats && <StationRanking page={page} onPage={setPage} />}
      </div>
      <details className="prototype-related">
        <summary>经营观测与接入记录</summary>
        <DashboardOperations />
      </details>
    </div>
  );
}
function StationRanking({
  page,
  onPage,
}: {
  page: number;
  onPage: (page: number) => void;
}) {
  const query = useRef({ page }).current;
  const [applied, setApplied] = useState(query);
  useEffect(() => setApplied({ page }), [page]);
  const { data, error, loading } = useRead<ApiSchemas["RankingPage"]>(
    applied,
    (signal) =>
      typedApi.GET("/api/v1/page-views/station-ranking", {
        params: { query: { page: applied.page, page_size: 5 } },
        signal,
        cache: "no-store",
      }),
  );
  useEffect(() => {
    if (data && page > Math.max(1, Math.ceil(data.total / 5)))
      onPage(Math.max(1, Math.ceil(data.total / 5)));
  }, [data, page, onPage]);
  return (
    <section className="panel" aria-label="昨日电量排名">
      <div className="panel-title">
        <div>
          <h2>充电站排名</h2>
          <p>昨日电量由高到低，每页5条；不同来源分别排名，缺观测不按零处理</p>
        </div>
      </div>
      <Feedback error={error} loading={loading} />
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>排名</th>
              <th>场站名称</th>
              <th>今日电量（度）</th>
              <th>昨日电量（度）</th>
            </tr>
          </thead>
          <tbody>
            {data?.items.map((r, i) => (
              <tr key={r.station_id + r.source}>
                <td>{(page - 1) * 5 + i + 1}</td>
                <td>
                  {r.station_name}
                  <small className="station-address">{r.source}</small>
                </td>
                <td>
                  {r.today_kwh == null
                    ? "未观测"
                    : r.today_kwh
                        .replace(/(\.\d*?)0+$/, "$1")
                        .replace(/\.$/, "")}
                </td>
                <td>
                  {r.yesterday_kwh == null
                    ? "未观测"
                    : r.yesterday_kwh
                        .replace(/(\.\d*?)0+$/, "$1")
                        .replace(/\.$/, "")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!loading && data?.total === 0 && (
        <p className="empty">昨日/今日暂无独立日电量观测</p>
      )}
      <div className="publication-pagination">
        <button
          className="outline"
          disabled={loading || page <= 1}
          onClick={() => onPage(page - 1)}
        >
          排名上一页
        </button>
        <span>
          第{page}页 · 共{data?.total ?? 0}条
        </span>
        <button
          className="outline"
          disabled={loading || page * 5 >= (data?.total ?? 0)}
          onClick={() => onPage(page + 1)}
        >
          排名下一页
        </button>
      </div>
    </section>
  );
}
