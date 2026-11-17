"use client";
import { useEffect, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { useCapability } from "./permissions";
import { usePageRefresh } from "./page-refresh";
type Report = ApiSchemas["ChargingReport"];
type Subsidy = ApiSchemas["SubsidyStream"];
const today = () =>
  new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
function Subsidies({ values }: { values: Subsidy[] }) {
  return values.length ? (
    values.map((stream) => (
      <div className="combined-stream" key={String(stream.is_demo)}>
        <strong>
          {stream.is_demo ? "演示核算" : "登记核算"} · {stream.claim_count}
          份申请
        </strong>
        <span>系数前汇算 {stream.base_amount} 元</span>
        <span>
          调整后{" "}
          {stream.adjusted_amount == null
            ? "未分摊，见场站补贴"
            : `${stream.adjusted_amount} 元`}
        </span>
        <span>
          登记发放{" "}
          {stream.disbursed_amount == null
            ? "未分摊"
            : `${stream.disbursed_amount} 元`}
        </span>
      </div>
    ))
  ) : (
    <span className="muted">暂无完整周期的已批准申请</span>
  );
}
export function ChargingSubsidyReport() {
  const statistics = useCapability("statistics.read"),
    orders = useCapability("orders.read"),
    subsidies = useCapability("subsidy.read");
  const exportStats = useCapability("statistics.export"),
    exportOrders = useCapability("orders.export");
  const allowed = statistics && orders && subsidies;
  const [draft, setDraft] = useState<{
    period: Report["period"];
    period_date: string;
    dimension: Report["dimension"];
    q: string;
  }>(() => ({
    period: "month",
    period_date: today(),
    dimension: "station",
    q: "",
  }));
  const [query, setQuery] = useState<
    (typeof draft & { page: number; refresh: number }) | null
  >(null);
  const [data, setData] = useState<Report | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [exporting, setExporting] = useState(false);
  const revision = usePageRefresh();
  useEffect(() => {
    if (!allowed || !query) return;
    const controller = new AbortController();
    setBusy(true);
    setError("");
    setData(null);
    typedApi
      .GET("/api/v1/statistics/charging-subsidy", {
        params: {
          query: {
            period: query.period,
            period_date: query.period_date,
            dimension: query.dimension,
            q: query.q,
            page: query.page,
            page_size: 20,
          },
        },
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "综合报表读取失败"));
        const last = Math.max(1, Math.ceil(data.total / data.page_size));
        if (query.page > last)
          setQuery((current) =>
            current ? { ...current, page: last } : current,
          );
        else setData(data);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "综合报表读取失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [allowed, query, revision]);
  async function download() {
    if (!data || exporting) return;
    setExporting(true);
    setError("");
    try {
      const result = await typedApi.GET(
        "/api/v1/statistics/charging-subsidy/export",
        {
          params: {
            query: {
              period: data.period,
              period_date: data.period_date,
              dimension: data.dimension,
              q: data.q,
            },
          },
          parseAs: "blob",
        },
      );
      if (!result.data)
        throw new Error(apiError(result.error, "综合报表导出失败"));
      if (
        result.response.headers.get("content-type") !==
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
      )
        throw new Error("导出格式不正确，请重试");
      const url = URL.createObjectURL(result.data),
        link = document.createElement("a");
      const encoded = result.response.headers
        .get("content-disposition")
        ?.match(/filename\*=UTF-8''([^;]+)/)?.[1];
      link.download = encoded
        ? decodeURIComponent(encoded)
        : "ronghub-charging-subsidy.xlsx";
      link.href = url;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "综合报表导出失败");
    } finally {
      setExporting(false);
    }
  }
  if (!allowed) return null;
  return (
    <section className="panel combined-report" aria-label="充电与补贴综合报表">
      <div className="panel-title">
        <div>
          <h2>充电与补贴数据统计</h2>
          <p>按同一自然周期核对订单观测与补贴核算，来源和金额分别保留</p>
        </div>
        {exportStats && exportOrders && (
          <button
            className="outline"
            disabled={busy || exporting || !data}
            onClick={download}
          >
            {exporting ? "正在导出…" : "导出综合报表 Excel"}
          </button>
        )}
      </div>
      <form
        className="application-filters"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery({ ...draft, page: 1, refresh: (query?.refresh ?? 0) + 1 });
        }}
      >
        <label>
          汇总维度
          <select
            aria-label="综合汇总维度"
            value={draft.dimension}
            onChange={(e) => {
              if (
                e.target.value === "station" ||
                e.target.value === "equipment"
              )
                setDraft({ ...draft, dimension: e.target.value });
            }}
          >
            <option value="station">场站汇总</option>
            <option value="equipment">电桩汇总</option>
          </select>
        </label>
        <label>
          统计周期
          <select
            aria-label="综合统计周期"
            value={draft.period}
            onChange={(e) => {
              if (
                e.target.value === "day" ||
                e.target.value === "month" ||
                e.target.value === "year"
              )
                setDraft({ ...draft, period: e.target.value });
            }}
          >
            <option value="day">日</option>
            <option value="month">月</option>
            <option value="year">年</option>
          </select>
        </label>
        <label>
          周期内日期（北京）
          <input
            type="date"
            aria-label="综合周期日期"
            value={draft.period_date}
            min="1900-01-01"
            max="9998-12-31"
            required
            onChange={(e) =>
              setDraft({ ...draft, period_date: e.target.value })
            }
          />
        </label>
        <label>
          场站名称
          <input
            aria-label="综合场站名称"
            value={draft.q}
            maxLength={150}
            onChange={(e) => setDraft({ ...draft, q: e.target.value })}
          />
        </label>
        <button className="primary" disabled={busy}>
          查询综合报表
        </button>
      </form>
      {error && (
        <p className="error application-list-note" role="alert">
          {error}
        </p>
      )}
      {busy && (
        <p className="empty" role="status">
          正在生成综合报表…
        </p>
      )}
      {!query && (
        <p className="empty">请选择日、月或年，查询充电与补贴数据。</p>
      )}
      {data && (
        <>
          <div className="runtime-applied">
            <strong>
              {data.start_date} 至 {data.end_date}（北京） ·{" "}
              {data.dimension === "station" ? "场站汇总" : "电桩汇总"}
            </strong>
            <p>
              当前授权 {data.selected_station_count} 站 · 台账设备{" "}
              {data.current_equipment_count} 台 · {data.total} 个汇总对象
            </p>
          </div>
          <details className="quality-method">
            <summary>统计口径与未分摊金额</summary>
            <p>{data.notice}</p>
            <p>
              跨周期未计入 {data.cross_period_claim_count}{" "}
              份；未提供设备分摊的旧申请 {data.legacy_claim_count}{" "}
              份。消费与登记发放均不代表银行资金实付。
            </p>
          </details>
          <section className="combined-overview" aria-label="综合全查询汇总">
            <h3>全查询汇总</h3>
            <div className="combined-cards">
              {data.orders.map((stream) => (
                <article key={JSON.stringify([stream.source, stream.is_demo])}>
                  <h4>
                    {stream.source} · {stream.is_demo ? "演示订单" : "源报订单"}
                  </h4>
                  <strong>
                    已完成 {stream.completed_orders} / 观测{" "}
                    {stream.observed_orders} 单
                  </strong>
                  <p>已知电量 {stream.kwh.value ?? "暂无数据"} kWh</p>
                  <p>已知时长 {stream.duration_hours ?? "暂无数据"} 小时</p>
                  <p>已知消费 {stream.amount.value ?? "暂无数据"} 元</p>
                  <small>
                    完成订单缺失：电量 {stream.kwh.missing_orders} / 时长{" "}
                    {stream.duration_seconds.missing_orders} / 消费{" "}
                    {stream.amount.missing_orders}
                  </small>
                </article>
              ))}
              {!data.orders.length && (
                <p className="muted">暂无来源订单，不能推断充电量为零。</p>
              )}
            </div>
            <Subsidies values={data.subsidies} />
          </section>
          {data.dimension === "equipment" &&
            data.station_subsidies.length > 0 && (
              <details className="quality-method">
                <summary>场站补贴汇总（含未分摊系数和调整）</summary>
                {data.station_subsidies.map((station) => (
                  <article key={station.station_id}>
                    <h4>{station.station_name}</h4>
                    <Subsidies values={station.streams} />
                  </article>
                ))}
              </details>
            )}
          {data.items.length ? (
            <div
              className="table-wrap"
              tabIndex={0}
              role="region"
              aria-label="综合报表明细，可横向滚动"
            >
              <table>
                <thead>
                  <tr>
                    <th>场站 / 运营商</th>
                    <th>设备身份</th>
                    <th>充电业务（分来源）</th>
                    <th>运营补贴核算</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((row) => (
                    <tr key={row.key}>
                      <td>
                        {row.station_name}
                        <span>
                          {row.operator_name} · {row.district}
                        </span>
                      </td>
                      <td>
                        {row.kind === "station"
                          ? "场站汇总"
                          : row.kind === "equipment"
                            ? row.equipment_name
                            : "未匹配台账的源桩引用"}
                        <span>
                          {row.equipment_external_id ??
                            (row.kind === "station" ? "" : "来源未提供编码")}
                        </span>
                        {row.reference_source && (
                          <span>来源：{row.reference_source}</span>
                        )}
                      </td>
                      <td>
                        {row.orders.map((stream) => (
                          <div
                            className="combined-stream"
                            key={JSON.stringify([
                              stream.source,
                              stream.is_demo,
                            ])}
                          >
                            <strong>
                              {stream.source} ·{" "}
                              {stream.is_demo ? "演示" : "源报"}
                            </strong>
                            <span>
                              已完成 {stream.completed_orders} 单 · 已知{" "}
                              {stream.kwh.value ?? "暂无数据"} kWh
                            </span>
                            <span>
                              源报时长 {stream.duration_hours ?? "暂无数据"}{" "}
                              小时
                            </span>
                            <span>
                              源报消费 {stream.amount.value ?? "暂无数据"} 元
                            </span>
                          </div>
                        ))}
                        {!row.orders.length && "暂无来源订单"}
                      </td>
                      <td>
                        <Subsidies values={row.subsidies} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="empty">当前条件下没有汇总对象</p>
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
              综合上一页
            </button>
            <span>
              综合第{data.page}页 · 共{data.total}个对象
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
              综合下一页
            </button>
          </div>
        </>
      )}
    </section>
  );
}
