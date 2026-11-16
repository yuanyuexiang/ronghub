"use client";
import { usePageRefresh } from "./page-refresh";
import { FormEvent, useEffect, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { useCapability } from "./permissions";
import { TimeSlotResults } from "./time-slot-results";
import { AggregateTimeSlotsPanel } from "./aggregate-time-slots-panel";
import { StationPicker } from "./station-picker";

type Filters = {
  station_id: string;
  source: string;
  start_at: string;
  end_at: string;
  meter_scope: "station" | "equipment";
  equipment_external_id: string;
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
    start_at: `${year}-${String(month).padStart(2, "0")}-01T00:00`,
    end_at:
      new Date(Date.UTC(year, month, 1)).toISOString().slice(0, 10) + "T00:00",
    meter_scope: "station",
    equipment_external_id: "",
  };
}
function params(filters: Filters) {
  return {
    ...filters,
    station_id: Number(filters.station_id),
    start_at: new Date(filters.start_at + "+08:00").toISOString(),
    end_at: new Date(filters.end_at + "+08:00").toISOString(),
    equipment_external_id:
      filters.meter_scope === "equipment" ? filters.equipment_external_id : "",
  };
}
function beijingStamp(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    dateStyle: "short",
    timeStyle: "short",
    hour12: false,
  }).format(new Date(value));
}
function SingleStationTimeSlotsPanel() {
  const canExport = useCapability("orders.export");
  const [draft, setDraft] = useState<Filters>(initial);
  const [query, setQuery] = useState<{
    filters: Filters;
    refresh: number;
  } | null>(null);
  const [data, setData] = useState<ApiSchemas["OrderTimeSlotReport"] | null>(
    null,
  );
  const [busy, setBusy] = useState(false),
    [sourceBusy, setSourceBusy] = useState(false),
    [exporting, setExporting] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    if (!draft.station_id) return;
    const controller = new AbortController(),
      sid = draft.station_id;
    setSourceBusy(true);
    typedApi
      .GET("/api/v1/stations/{station_id}", {
        params: { path: { station_id: Number(sid) } },
        signal: controller.signal,
      })
      .then(({ data: station, error }) => {
        if (controller.signal.aborted) return;
        if (!station) throw new Error(apiError(error, "场站来源读取失败"));
        setDraft((current) =>
          current.station_id === sid
            ? { ...current, source: station.source }
            : current,
        );
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "场站来源读取失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setSourceBusy(false);
      });
    return () => controller.abort();
  }, [draft.station_id]);
  const pageRefreshRevision = usePageRefresh();
  useEffect(() => {
    if (!query) return;
    const controller = new AbortController();
    setBusy(true);
    setData(null);
    setError("");
    typedApi
      .GET("/api/v1/orders/time-slots", {
        params: { query: params(query.filters) },
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data: body, error }) => {
        if (controller.signal.aborted) return;
        if (!body) throw new Error(apiError(error, "时段观测读取失败"));
        setData(body);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "时段观测读取失败");
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
      Number.isNaN(+start) ||
      Number.isNaN(+end) ||
      start >= end ||
      +end - +start > 366 * 86400000 ||
      start.getUTCMinutes() ||
      end.getUTCMinutes() ||
      start.getUTCSeconds() ||
      end.getUTCSeconds()
    ) {
      setError("请选场站及来源，并填写北京时间整点区间，单次最多366天");
      return;
    }
    if (
      draft.meter_scope === "equipment" &&
      !draft.equipment_external_id.trim()
    ) {
      setError("桩级查询须明确唯一源桩编码");
      return;
    }
    setQuery({ filters: { ...draft }, refresh: (query?.refresh ?? 0) + 1 });
  }
  async function download() {
    if (!query) return;
    setExporting(true);
    setError("");
    try {
      const { data: blob, error } = await typedApi.GET(
        "/api/v1/orders/time-slots/export",
        { params: { query: params(query.filters) }, parseAs: "blob" },
      );
      if (!blob) throw new Error(apiError(error, "时段导出失败"));
      const url = URL.createObjectURL(blob),
        link = document.createElement("a");
      link.href = url;
      link.download = "ronghub-order-time-slots.csv";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "时段导出失败");
    } finally {
      setExporting(false);
    }
  }
  return (
    <section className="panel time-slots-panel">
      <div className="panel-title">
        <div>
          <h2>订单开始分布与源报小时电量</h2>
          <p>以北京时间整点查询单站或指定源桩，分别查看订单事件与独立计量</p>
        </div>
        {canExport && (
          <button
            className="outline"
            disabled={!data || busy || exporting}
            onClick={download}
          >
            {exporting ? "正在导出…" : "导出当前时段查询"}
          </button>
        )}
      </div>
      <form className="application-filters" onSubmit={apply}>
        <StationPicker
          label="时段分析场站"
          value={draft.station_id}
          onChange={(station_id) =>
            setDraft((current) => ({ ...current, station_id, source: "" }))
          }
        />
        <label>
          数据来源
          <input
            aria-label="时段数据来源"
            value={draft.source}
            onChange={(e) => setDraft({ ...draft, source: e.target.value })}
            maxLength={50}
            required
            disabled={sourceBusy}
          />
        </label>
        <label>
          计量层级
          <select
            aria-label="时段计量层级"
            value={draft.meter_scope}
            onChange={(e) =>
              setDraft({
                ...draft,
                meter_scope: e.target.value as Filters["meter_scope"],
              })
            }
          >
            <option value="station">单站总表计量</option>
            <option value="equipment">指定源桩计量</option>
          </select>
        </label>
        <label>
          源桩编码
          <input
            aria-label="时段源桩编码"
            disabled={draft.meter_scope === "station"}
            required={draft.meter_scope === "equipment"}
            maxLength={100}
            value={draft.equipment_external_id}
            onChange={(e) =>
              setDraft({ ...draft, equipment_external_id: e.target.value })
            }
            placeholder={
              draft.meter_scope === "station"
                ? "站级不合并桩级计量"
                : "输入唯一源桩编码"
            }
          />
        </label>
        <label>
          开始时间（北京时间）
          <input
            aria-label="时段开始时间"
            type="datetime-local"
            step={3600}
            required
            value={draft.start_at}
            onChange={(e) => setDraft({ ...draft, start_at: e.target.value })}
          />
        </label>
        <label>
          结束时间（不含，北京时间）
          <input
            aria-label="时段结束时间"
            type="datetime-local"
            step={3600}
            required
            value={draft.end_at}
            onChange={(e) => setDraft({ ...draft, end_at: e.target.value })}
          />
        </label>
        <button
          className="primary"
          disabled={busy || sourceBusy || !draft.station_id || !draft.source}
        >
          查询充电时段
        </button>
      </form>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {busy && (
        <p className="empty" role="status">
          正在读取时段观测…
        </p>
      )}
      {!query && <p className="empty">选择场站与时间区间后查询时段观测</p>}
      {data && (
        <>
          <p className="application-list-note">
            <strong>
              已应用：{data.station_name} · {data.source}
            </strong>{" "}
            ·{" "}
            {data.meter_scope === "station"
              ? "站级独立计量"
              : `源桩 ${data.equipment_external_id}`}
            <span className="muted">
              {" "}
              · {beijingStamp(data.start_at)} 至 {beijingStamp(data.end_at)}
              （北京，结束不含）
            </span>
          </p>
          <p className="muted application-list-note">{data.notice}</p>
          {!data.streams.length && (
            <p className="empty">当前条件下没有时段观测记录</p>
          )}
          <TimeSlotResults streams={data.streams} />
        </>
      )}
    </section>
  );
}

export function OrderTimeSlotsPanel() {
  const [mode, setMode] = useState<"single" | "multi">("single");
  return (
    <div className="business-stack">
      <section className="panel time-slot-modes">
        <strong>分析范围</strong>
        <div className="action-row" role="group" aria-label="时段统计范围">
          <button
            className={mode === "single" ? "primary" : "outline"}
            aria-pressed={mode === "single"}
            onClick={() => setMode("single")}
          >
            单站查询
          </button>
          <button
            className={mode === "multi" ? "primary" : "outline"}
            aria-pressed={mode === "multi"}
            onClick={() => setMode("multi")}
          >
            多站汇总
          </button>
        </div>
      </section>
      {mode === "single" ? (
        <SingleStationTimeSlotsPanel />
      ) : (
        <AggregateTimeSlotsPanel />
      )}
    </div>
  );
}
