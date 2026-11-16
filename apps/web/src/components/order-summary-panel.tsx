"use client";
import { usePageRefresh } from "./page-refresh";
import { FormEvent, useEffect, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { useCapability } from "./permissions";

type Filters = {
  q: string;
  source: string;
  equipment_external_id: string;
  status: string;
  start_at: string;
  end_at: string;
  dimension: "station" | "equipment";
  granularity: "day" | "month" | "year";
};
function initial(): Filters {
  const now = new Date();
  const input = (value: Date) =>
    `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-01T00:00`;
  return {
    q: "",
    source: "",
    equipment_external_id: "",
    status: "",
    dimension: "station",
    granularity: "month",
    start_at: input(new Date(now.getFullYear(), now.getMonth(), 1)),
    end_at: input(new Date(now.getFullYear(), now.getMonth() + 1, 1)),
  };
}
const params = (filters: Filters) => ({
  ...filters,
  start_at: new Date(filters.start_at).toISOString(),
  end_at: new Date(filters.end_at).toISOString(),
});
function Metric({
  metric,
  unit,
}: {
  metric: ApiSchemas["OrderMetricCoverage"];
  unit: string;
}) {
  return (
    <>
      <strong>{metric.value ?? "暂无数据"}</strong>
      {metric.value !== null && <span>{unit} · 已知合计</span>}
      <span className={metric.missing_orders ? "metric-missing" : ""}>
        已知{metric.known_orders} · 缺{metric.missing_orders}
      </span>
    </>
  );
}
export function OrderSummaryPanel() {
  const canExport = useCapability("orders.export");
  const [draft, setDraft] = useState<Filters>(initial);
  const [query, setQuery] = useState(() => ({
    filters: initial(),
    page: 1,
    refresh: 0,
  }));
  const [data, setData] = useState<ApiSchemas["OrderSummaryPage"] | null>(null);
  const [busy, setBusy] = useState(false),
    [exporting, setExporting] = useState(false),
    [error, setError] = useState("");
  const pageRefreshRevision = usePageRefresh();
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    setData(null);
    setError("");
    typedApi
      .GET("/api/v1/orders/summary", {
        params: {
          query: { ...params(query.filters), page: query.page, page_size: 20 },
        },
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data: body, error }) => {
        if (controller.signal.aborted) return;
        if (!body) throw new Error(apiError(error, "订单汇总读取失败"));
        const last = Math.max(1, Math.ceil(body.total / body.page_size));
        if (query.page > last)
          setQuery((current) => ({ ...current, page: last }));
        else setData(body);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "订单汇总读取失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [query, pageRefreshRevision]);
  function apply(event: FormEvent) {
    event.preventDefault();
    const start = new Date(draft.start_at),
      end = new Date(draft.end_at);
    if (
      !draft.start_at ||
      !draft.end_at ||
      start >= end ||
      +end - +start > 366 * 86400000
    ) {
      setError("请填写有效开始和结束时间，单次最多366天");
      return;
    }
    setQuery({ filters: { ...draft }, page: 1, refresh: query.refresh + 1 });
  }
  async function download() {
    setExporting(true);
    setError("");
    try {
      const { data: blob, error } = await typedApi.GET(
        "/api/v1/orders/summary/export",
        { params: { query: params(query.filters) }, parseAs: "blob" },
      );
      if (!blob)
        throw new Error(apiError(error, "汇总导出失败，请缩小查询范围"));
      const url = URL.createObjectURL(blob),
        link = document.createElement("a");
      link.href = url;
      link.download = "ronghub-order-summary.csv";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "汇总导出失败");
    } finally {
      setExporting(false);
    }
  }
  return (
    <section className="panel order-summary-panel">
      <div className="panel-title">
        <div>
          <h2>场站与电桩订单汇总</h2>
          <p>按源报订单观察运行情况，指标缺失与零值分别展示</p>
        </div>
        <div className="action-row">
          <button
            className="outline"
            disabled={busy}
            onClick={() =>
              setQuery((current) => ({
                ...current,
                refresh: current.refresh + 1,
              }))
            }
          >
            刷新订单汇总
          </button>
          {canExport && (
            <button
              className="outline"
              disabled={busy || exporting}
              onClick={download}
            >
              {exporting ? "正在导出…" : "导出当前订单汇总"}
            </button>
          )}
        </div>
      </div>
      <form className="application-filters" onSubmit={apply}>
        <label>
          订单号、场站或企业
          <input
            aria-label="汇总检索名称"
            maxLength={150}
            value={draft.q}
            onChange={(e) => setDraft({ ...draft, q: e.target.value })}
          />
        </label>
        <label>
          汇总维度
          <select
            aria-label="订单汇总维度"
            value={draft.dimension}
            onChange={(e) =>
              setDraft({
                ...draft,
                dimension: e.target.value as Filters["dimension"],
              })
            }
          >
            <option value="station">场站汇总</option>
            <option value="equipment">源桩编码汇总</option>
          </select>
        </label>
        <label>
          周期
          <select
            aria-label="订单汇总周期"
            value={draft.granularity}
            onChange={(e) =>
              setDraft({
                ...draft,
                granularity: e.target.value as Filters["granularity"],
              })
            }
          >
            <option value="day">日</option>
            <option value="month">月</option>
            <option value="year">年</option>
          </select>
        </label>
        <label>
          数据来源
          <input
            aria-label="汇总数据来源"
            maxLength={50}
            value={draft.source}
            onChange={(e) => setDraft({ ...draft, source: e.target.value })}
          />
        </label>
        <label>
          开始时间起（本地）
          <input
            aria-label="汇总开始时间"
            type="datetime-local"
            required
            value={draft.start_at}
            onChange={(e) => setDraft({ ...draft, start_at: e.target.value })}
          />
        </label>
        <label>
          开始时间止（不含）
          <input
            aria-label="汇总结束时间"
            type="datetime-local"
            required
            value={draft.end_at}
            onChange={(e) => setDraft({ ...draft, end_at: e.target.value })}
          />
        </label>
        <label>
          源桩编码
          <input
            aria-label="汇总源桩编码"
            maxLength={100}
            value={draft.equipment_external_id}
            onChange={(e) =>
              setDraft({ ...draft, equipment_external_id: e.target.value })
            }
          />
        </label>
        <label>
          订单状态
          <select
            aria-label="汇总订单状态"
            value={draft.status}
            onChange={(e) => setDraft({ ...draft, status: e.target.value })}
          >
            {[
              ["", "全部状态"],
              ["completed", "已完成"],
              ["charging", "充电中"],
              ["interrupted", "中断"],
              ["cancelled", "已取消"],
              ["unknown", "源状态未知"],
            ].map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <button className="primary" disabled={busy}>
          查询订单汇总
        </button>
        <button
          type="button"
          className="outline"
          disabled={busy}
          onClick={() => {
            const filters = initial();
            setDraft(filters);
            setQuery({ filters, page: 1, refresh: query.refresh + 1 });
          }}
        >
          重置汇总筛选
        </button>
      </form>
      <p className="muted application-list-note">
        {data?.notice ??
          "仅合计已完成订单的已知指标，按来源和演示标识分别展示；查询窗口内的日/月/年周期可能不完整。"}
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {busy && (
        <p className="empty" role="status">
          正在读取订单汇总…
        </p>
      )}
      {data && (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {[
                    "周期（北京）",
                    "场站 / 企业",
                    "来源 / 源桩编码",
                    "观测订单",
                    "已知电量",
                    "已知源报时长",
                    "已知消费额",
                  ].map((label) => (
                    <th key={label}>{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.items.map((row, index) => (
                  <tr
                    key={`${row.period_start}-${row.station_id}-${row.source}-${row.equipment_external_id}-${row.is_demo}-${index}`}
                  >
                    <td>
                      {data.granularity === "year"
                        ? row.period_start.slice(0, 4)
                        : data.granularity === "month"
                          ? row.period_start.slice(0, 7)
                          : row.period_start}
                      <span>
                        {data.granularity === "day"
                          ? "日"
                          : data.granularity === "month"
                            ? "月"
                            : "年"}
                        归属
                      </span>
                    </td>
                    <td>
                      {row.station_name}
                      <span>
                        {row.operator_name} · {row.district}
                      </span>
                    </td>
                    <td>
                      {row.source}
                      <span>
                        {data.dimension === "equipment"
                          ? (row.equipment_external_id ?? "未提供源桩编码")
                          : "场站维度"}
                      </span>
                      <span>
                        {row.is_demo ? "明确演示数据" : "未标记为演示"}
                      </span>
                    </td>
                    <td>
                      <strong>{row.observed_orders}</strong>
                      <span>已完成{row.completed_orders}</span>
                      {row.observed_orders !== row.completed_orders && (
                        <details>
                          <summary>其他状态</summary>
                          <span>
                            充电中{row.charging_orders} · 中断
                            {row.interrupted_orders} · 取消
                            {row.cancelled_orders} · 未知{row.unknown_orders}
                          </span>
                        </details>
                      )}
                    </td>
                    <td>
                      <Metric metric={row.kwh} unit="kWh" />
                    </td>
                    <td>
                      <Metric metric={row.duration_seconds} unit="秒" />
                    </td>
                    <td>
                      <Metric metric={row.amount} unit="CNY" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!data.items.length && (
            <p className="empty">当前条件下没有订单汇总记录</p>
          )}
          <div className="action-row application-pagination">
            <button
              className="outline"
              disabled={busy || data.page <= 1}
              onClick={() =>
                setQuery((current) => ({ ...current, page: current.page - 1 }))
              }
            >
              汇总上一页
            </button>
            <span>
              汇总第{data.page}页 · 共{data.total}组
            </span>
            <button
              className="outline"
              disabled={busy || data.page * data.page_size >= data.total}
              onClick={() =>
                setQuery((current) => ({ ...current, page: current.page + 1 }))
              }
            >
              汇总下一页
            </button>
          </div>
        </>
      )}
    </section>
  );
}
