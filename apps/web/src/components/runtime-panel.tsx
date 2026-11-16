"use client";
import { usePageRefresh } from "./page-refresh";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { useCapability } from "./permissions";
import {
  RuntimeAxes,
  runtimeLabels as labels,
  formatObservedNumber as number,
} from "./runtime-axes";
import { QualityPanel } from "./quality-panel";
import { StationPicker } from "./station-picker";
import {
  RuntimeObjectPicker,
  type RuntimeObject,
} from "./runtime-object-picker";

type Filters = {
  station_id: string;
  source: string;
  object: RuntimeObject | null;
  start_at: string;
  end_at: string;
};
function initial(): Filters {
  const parts = new Intl.DateTimeFormat("en", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
  }).formatToParts(new Date());
  const year = Number(parts.find((p) => p.type === "year")!.value),
    month = Number(parts.find((p) => p.type === "month")!.value);
  return {
    station_id: "",
    source: "",
    object: null,
    start_at: `${year}-${String(month).padStart(2, "0")}-01T00:00`,
    end_at:
      new Date(Date.UTC(year, month, 1)).toISOString().slice(0, 10) + "T00:00",
  };
}
function params(filters: Filters) {
  return {
    station_id: Number(filters.station_id),
    source: filters.source,
    equipment_external_id: filters.object!.equipment_external_id,
    connector_external_id: filters.object!.connector_external_id,
    start_at: new Date(filters.start_at + "+08:00").toISOString(),
    end_at: new Date(filters.end_at + "+08:00").toISOString(),
  };
}
function stamp(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    dateStyle: "short",
    timeStyle: "medium",
    hour12: false,
  }).format(new Date(value));
}

function SingleRuntimePanel() {
  const canExport = useCapability("statistics.export");
  const [draft, setDraft] = useState<Filters>(initial),
    [query, setQuery] = useState<{
      filters: Filters;
      page: number;
      refresh: number;
    } | null>(null);
  const [data, setData] = useState<ApiSchemas["RuntimeReport"] | null>(null),
    [busy, setBusy] = useState(false),
    [exporting, setExporting] = useState(false),
    [error, setError] = useState("");
  const stationChanged = useCallback(
    (station_id: string) =>
      setDraft((current) => ({
        ...current,
        station_id,
        source: "",
        object: null,
      })),
    [],
  );
  const sourceChanged = useCallback(
    (source: string) =>
      setDraft((current) => ({ ...current, source, object: null })),
    [],
  );
  const objectChanged = useCallback(
    (object: RuntimeObject | null) =>
      setDraft((current) => ({ ...current, object })),
    [],
  );
  const pageRefreshRevision = usePageRefresh();
  useEffect(() => {
    if (!query) return;
    const controller = new AbortController();
    setBusy(true);
    setError("");
    setData(null);
    typedApi
      .GET("/api/v1/statistics/runtime", {
        params: {
          query: { ...params(query.filters), page: query.page, page_size: 20 },
        },
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "历史运行状态读取失败"));
        const last = Math.max(1, Math.ceil(data.total / data.page_size));
        if (query.page > last)
          setQuery((current) =>
            current ? { ...current, page: last } : current,
          );
        else setData(data);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "历史运行状态读取失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [query, pageRefreshRevision]);
  function apply(event: FormEvent) {
    event.preventDefault();
    const start = new Date(draft.start_at + "+08:00"),
      end = new Date(draft.end_at + "+08:00");
    if (
      !draft.station_id ||
      !draft.source.trim() ||
      !draft.object ||
      Number.isNaN(+start) ||
      Number.isNaN(+end) ||
      start >= end ||
      +end - +start > 366 * 86400000
    ) {
      setError(
        "请选择场站、来源及源报对象，填写北京时间区间，结束晚于开始且最多366天",
      );
      return;
    }
    setQuery({
      filters: { ...draft },
      page: 1,
      refresh: (query?.refresh ?? 0) + 1,
    });
  }
  async function download() {
    if (!query) return;
    setExporting(true);
    setError("");
    try {
      const { data, error } = await typedApi.GET(
        "/api/v1/statistics/runtime/export",
        { params: { query: params(query.filters) }, parseAs: "blob" },
      );
      if (!data)
        throw new Error(apiError(error, "运行区间导出失败，请缩小范围"));
      const url = URL.createObjectURL(data),
        link = document.createElement("a");
      link.href = url;
      link.download = "ronghub-runtime-intervals.csv";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "导出失败");
    } finally {
      setExporting(false);
    }
  }
  return (
    <section className="panel runtime-panel">
      <div className="panel-title">
        <div>
          <h2>历史运行状态与观测覆盖</h2>
          <p>连接与业务状态分开查看，所有时长只来自明确上送的有限区间</p>
        </div>
        <div className="action-row">
          <button
            className="outline"
            disabled={!query || busy}
            onClick={() =>
              setQuery((current) =>
                current
                  ? { ...current, refresh: current.refresh + 1 }
                  : current,
              )
            }
          >
            刷新运行记录
          </button>
          {canExport && (
            <button
              className="outline"
              disabled={!query || busy || exporting}
              onClick={download}
            >
              {exporting ? "正在导出…" : "导出当前运行区间"}
            </button>
          )}
        </div>
      </div>
      <form className="application-filters runtime-filters" onSubmit={apply}>
        <StationPicker
          label="历史运行场站"
          value={draft.station_id}
          onChange={stationChanged}
          kind="runtime"
          onSource={sourceChanged}
        />
        <label>
          数据来源
          <input
            aria-label="历史运行数据来源"
            value={draft.source}
            maxLength={50}
            onChange={(e) => sourceChanged(e.target.value)}
          />
        </label>
        <RuntimeObjectPicker
          key={`${draft.station_id}-${draft.source}`}
          stationId={draft.station_id}
          source={draft.source}
          value={draft.object}
          onChange={objectChanged}
        />
        <label>
          开始时间（北京，含）
          <input
            aria-label="历史运行开始时间"
            type="datetime-local"
            step="1"
            required
            value={draft.start_at}
            onChange={(e) => setDraft({ ...draft, start_at: e.target.value })}
          />
        </label>
        <label>
          结束时间（北京，不含）
          <input
            aria-label="历史运行结束时间"
            type="datetime-local"
            step="1"
            required
            value={draft.end_at}
            onChange={(e) => setDraft({ ...draft, end_at: e.target.value })}
          />
        </label>
        <button className="primary" disabled={busy || !draft.object}>
          查询历史运行
        </button>
      </form>
      <p className="muted application-list-note">
        先选场站及来源，再选择已有历史上送的设备本级或单枪。查询区间不代表营业/可用时长，明确未知与未上送分开，不据此推导正式在线率、故障率或利用率。
      </p>
      {error && (
        <p className="error application-list-note" role="alert">
          {error}
        </p>
      )}
      {busy && (
        <p className="empty" role="status">
          正在读取历史运行状态…
        </p>
      )}
      {!query && !busy && (
        <p className="empty">请选择对象及时间，查询历史运行状态</p>
      )}
      {data && (
        <>
          <div className="runtime-applied">
            <strong>
              {data.station_name} · {data.source} · {data.equipment_external_id}{" "}
              ·{" "}
              {data.connector_external_id
                ? `枪 ${data.connector_external_id}`
                : "设备本级"}
            </strong>
            <p>
              {stamp(data.start_at)} 至 {stamp(data.end_at)}（北京，结束不含）·
              查询窗{number(data.window_seconds)}秒
            </p>
          </div>
          <p className="muted application-list-note">{data.notice}</p>
          {!data.streams.length && (
            <p className="empty">
              当前条件下没有历史运行区间；未上送不能视为零故障或全在线。
            </p>
          )}
          {data.streams.map((stream) => (
            <div className="runtime-stream" key={String(stream.is_demo)}>
              <h3>
                {stream.is_demo ? "明确模拟数据" : "来源上送数据"} ·{" "}
                {data.source}
              </h3>
              <RuntimeAxes
                axes={stream.axes}
                windowSeconds={data.window_seconds}
                demo={stream.is_demo}
              />
            </div>
          ))}
          {data.total > 0 && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    {[
                      "源区间 / 标识",
                      "维度 / 状态",
                      "源区间开始 / 结束（北京）",
                      "窗口内开始 / 结束（北京）",
                      "窗口内秒数",
                      "源更新时间（北京）",
                    ].map((label) => (
                      <th key={label}>{label}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((item) => (
                    <tr key={item.id}>
                      <td>
                        {item.external_id}
                        <span>
                          RUN-{item.id} · v{item.version} ·{" "}
                          {item.is_demo ? "演示" : "源报"}
                        </span>
                      </td>
                      <td>
                        {labels[item.dimension]}
                        <span>{labels[item.state]}</span>
                      </td>
                      <td>
                        {stamp(item.started_at)}
                        <span>{stamp(item.ended_at)}</span>
                      </td>
                      <td>
                        {stamp(item.clipped_start_at)}
                        <span>{stamp(item.clipped_end_at)}</span>
                      </td>
                      <td>{number(item.observed_seconds)}</td>
                      <td>{stamp(item.source_updated_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="action-row application-pagination">
            <button
              className="outline"
              disabled={busy || data.page <= 1}
              onClick={() =>
                setQuery((current) =>
                  current ? { ...current, page: current.page - 1 } : current,
                )
              }
            >
              运行记录上一页
            </button>
            <span>
              运行记录第{data.page}页 · 共{data.total}个区间
            </span>
            <button
              className="outline"
              disabled={busy || data.page * data.page_size >= data.total}
              onClick={() =>
                setQuery((current) =>
                  current ? { ...current, page: current.page + 1 } : current,
                )
              }
            >
              运行记录下一页
            </button>
          </div>
        </>
      )}
    </section>
  );
}

export function RuntimePanel() {
  const [mode, setMode] = useState<"single" | "quality">("single");
  return (
    <div className="business-stack">
      <section className="panel time-slot-modes">
        <strong>运行分析范围</strong>
        <div className="action-row" role="group" aria-label="运行分析范围">
          <button
            className={mode === "single" ? "primary" : "outline"}
            aria-pressed={mode === "single"}
            onClick={() => setMode("single")}
          >
            单对象区间
          </button>
          <button
            className={mode === "quality" ? "primary" : "outline"}
            aria-pressed={mode === "quality"}
            onClick={() => setMode("quality")}
          >
            区域与企业质量
          </button>
        </div>
      </section>
      {mode === "single" ? <SingleRuntimePanel /> : <QualityPanel />}
    </div>
  );
}
