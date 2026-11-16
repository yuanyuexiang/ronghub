"use client";
import { usePageRefresh } from "./page-refresh";
import { FormEvent, useEffect, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { useCapability } from "./permissions";
import { MeterSourcePicker } from "./meter-source-picker";
import { TimeSlotResults } from "./time-slot-results";

type Filters = {
  source: string;
  q: string;
  district: string;
  operator_id: string;
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
    source: "",
    q: "",
    district: "",
    operator_id: "",
    start_at: `${year}-${String(month).padStart(2, "0")}-01T00:00`,
    end_at:
      new Date(Date.UTC(year, month, 1)).toISOString().slice(0, 10) + "T00:00",
  };
}
const params = (filters: Filters) => ({
  ...filters,
  operator_id: filters.operator_id ? Number(filters.operator_id) : undefined,
  start_at: new Date(filters.start_at + "+08:00").toISOString(),
  end_at: new Date(filters.end_at + "+08:00").toISOString(),
});
const stamp = (value: string) =>
  new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    dateStyle: "short",
    timeStyle: "short",
    hour12: false,
  }).format(new Date(value));
export function AggregateTimeSlotsPanel() {
  const canExport = useCapability("orders.export");
  const [draft, setDraft] = useState<Filters>(initial),
    [query, setQuery] = useState<{ filters: Filters; refresh: number } | null>(
      null,
    );
  const [operators, setOperators] = useState<
    ApiSchemas["OperatorList"]["items"]
  >([]);
  const [data, setData] = useState<
      ApiSchemas["AggregateTimeSlotReport"] | null
    >(null),
    [busy, setBusy] = useState(false),
    [exporting, setExporting] = useState(false),
    [error, setError] = useState("");
  const pageRefreshRevision = usePageRefresh();
  useEffect(() => {
    const controller = new AbortController();
    typedApi
      .GET("/api/v1/operators", { signal: controller.signal })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "企业选项读取失败"));
        setOperators(data.items);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "企业选项读取失败");
      });
    return () => controller.abort();
  }, [pageRefreshRevision]);
  useEffect(() => {
    if (!query) return;
    const controller = new AbortController();
    setBusy(true);
    setData(null);
    setError("");
    typedApi
      .GET("/api/v1/orders/time-slots/aggregate", {
        params: { query: params(query.filters) },
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "多站时段读取失败"));
        setData(data);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "多站时段读取失败");
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
      !draft.source ||
      Number.isNaN(+start) ||
      Number.isNaN(+end) ||
      start >= end ||
      +end - +start > 366 * 86400000 ||
      start.getUTCMinutes() ||
      end.getUTCMinutes() ||
      start.getUTCSeconds() ||
      end.getUTCSeconds()
    ) {
      setError("请明确来源及北京时间整点区间，单次最多366天");
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
        "/api/v1/orders/time-slots/aggregate/export",
        { params: { query: params(query.filters) }, parseAs: "blob" },
      );
      if (!blob) throw new Error(apiError(error, "多站时段导出失败"));
      const url = URL.createObjectURL(blob),
        link = document.createElement("a");
      link.href = url;
      link.download = "ronghub-multi-station-time-slots.csv";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "多站时段导出失败");
    } finally {
      setExporting(false);
    }
  }
  return (
    <section className="panel time-slots-panel">
      <div className="panel-title">
        <div>
          <h2>授权范围多站时段汇总</h2>
          <p>按当前台账筛选同一来源的场站，仅合计站级独立小时计量</p>
        </div>
        {canExport && (
          <button
            className="outline"
            disabled={!data || busy || exporting}
            onClick={download}
          >
            {exporting ? "正在导出…" : "导出当前多站查询"}
          </button>
        )}
      </div>
      <form className="application-filters" onSubmit={apply}>
        <MeterSourcePicker
          value={draft.source}
          onChange={(source) => setDraft((current) => ({ ...current, source }))}
          district={draft.district}
          operatorId={draft.operator_id}
        />
        <label>
          场站或企业名称
          <input
            aria-label="多站检索名称"
            value={draft.q}
            maxLength={150}
            onChange={(e) => setDraft({ ...draft, q: e.target.value })}
          />
        </label>
        <label>
          区域名称（精确）
          <input
            aria-label="多站筛选区域"
            value={draft.district}
            maxLength={50}
            onChange={(e) =>
              setDraft({ ...draft, district: e.target.value, source: "" })
            }
            placeholder="如鼓楼区"
          />
        </label>
        <label>
          运营企业
          <select
            aria-label="多站筛选企业"
            value={draft.operator_id}
            onChange={(e) =>
              setDraft({ ...draft, operator_id: e.target.value, source: "" })
            }
          >
            <option value="">当前授权全部企业</option>
            {operators.map((operator) => (
              <option key={operator.id} value={String(operator.id)}>
                {operator.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          开始时间（北京时间）
          <input
            aria-label="多站开始时间"
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
            aria-label="多站结束时间"
            type="datetime-local"
            step={3600}
            required
            value={draft.end_at}
            onChange={(e) => setDraft({ ...draft, end_at: e.target.value })}
          />
        </label>
        <button className="primary" disabled={busy || !draft.source}>
          查询多站时段
        </button>
      </form>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {busy && (
        <p className="empty" role="status">
          正在读取多站观测…
        </p>
      )}
      {!query && <p className="empty">选择来源及授权范围后查询多站时段</p>}
      {data && (
        <>
          <p className="application-list-note">
            <strong>当前筛选集合：{data.station_count}个场站</strong> · 来源
            {data.source} · {data.district || "授权全部区域"} ·{" "}
            {stamp(data.start_at)} 至 {stamp(data.end_at)}（北京，结束不含）
          </p>
          <p className="muted application-list-note">{data.notice}</p>
          {!data.streams.length && (
            <p className="empty">当前筛选集合没有多站时段观测</p>
          )}
          <TimeSlotResults streams={data.streams} multi />
        </>
      )}
    </section>
  );
}
