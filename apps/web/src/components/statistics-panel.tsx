"use client";
import { ChargingSubsidyReport } from "./charging-subsidy-report";
import { usePageRefresh } from "./page-refresh";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { useCapability } from "./permissions";
import { useEffect, useState } from "react";
type Report = ApiSchemas["EnergyStatistics"];
const labels: Record<string, string> = {
  utilization: "利用率",
  order_count: "充电订单数",
  charging_hours: "充电时段分布",
  year_over_year: "同比",
  online_rate: "在线率",
};
const states: Record<string, string> = {
  operating: "运营中",
  pending: "待接入",
  access_approved: "接入通过",
  accepted: "验收通过",
  decommissioned: "已核销",
};
const grains: Record<string, string> = { day: "日", month: "月", year: "年" };
const dimensions: Record<string, string> = {
  station: "场站",
  district: "区域",
  operator: "运营商",
};
const today = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
}).format(new Date());
const initial = {
  start_date: today.slice(0, 8) + "01",
  end_date: today,
  granularity: "day",
  dimension: "station",
  district: "",
  operator_id: "",
};
export function StatisticsPanel({ role }: { role: string }) {
  const canExport = useCapability("statistics.export");
  const reportOrders = useCapability("orders.read"),
    reportSubsidies = useCapability("subsidy.read"),
    reportStatistics = useCapability("statistics.read");
  const canCombined = reportOrders && reportSubsidies && reportStatistics;
  const [view, setView] = useState<"energy" | "combined">("energy");
  const combined = view === "combined" && canCombined;
  const [draft, setDraft] = useState(initial),
    [filters, setFilters] = useState(initial),
    [refresh, setRefresh] = useState(0),
    [data, setData] = useState<Report | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false),
    [operators, setOperators] = useState<{ id: number; name: string }[]>([]);
  const params = new URLSearchParams(
    Object.entries(filters).filter(([, v]) => v !== ""),
  );
  const pageRefreshRevision = usePageRefresh();
  useEffect(() => {
    const controller = new AbortController();
    typedApi
      .GET("/api/v1/operators", {
        cache: "no-store",
        signal: controller.signal,
      })
      .then((result) => {
        if (!result.data)
          throw new Error(apiError(result.error, "企业筛选项加载失败"));
        if (!controller.signal.aborted) setOperators(result.data.items);
      })
      .catch(() => {
        if (!controller.signal.aborted) setError("企业筛选项加载失败");
      });
    return () => controller.abort();
  }, [pageRefreshRevision]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setData(null);
    typedApi
      .GET("/api/v1/statistics", {
        params: {
          query: {
            ...filters,
            operator_id: filters.operator_id
              ? Number(filters.operator_id)
              : undefined,
          },
        },
        cache: "no-store",
        signal: controller.signal,
      })
      .then((result) => {
        if (!result.data)
          throw new Error(apiError(result.error, "统计读取失败"));
        if (!controller.signal.aborted) setData(result.data);
      })
      .catch((e) => {
        if (!controller.signal.aborted && e.name !== "AbortError")
          setError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [filters, refresh, pageRefreshRevision]);
  const maximum = Math.max(
    1,
    ...(data?.timeline.map((d) => Number(d.kwh || 0)) || []),
  );
  return (
    <div className="business-stack">
      {canCombined && (
        <section className="panel statistics-views" aria-label="统计分析视图">
          <strong>统计分析范围</strong>
          <button
            className={!combined ? "primary" : "outline"}
            aria-pressed={!combined}
            onClick={() => setView("energy")}
          >
            观测电量分析
          </button>
          <button
            className={combined ? "primary" : "outline"}
            aria-pressed={combined}
            onClick={() => setView("combined")}
          >
            充电与补贴报表
          </button>
        </section>
      )}
      <div style={{ display: combined ? "block" : "none" }}>
        <ChargingSubsidyReport />
      </div>
      <div
        className="business-stack"
        style={{ display: combined ? "none" : "grid" }}
      >
        <section className="panel">
          <div className="panel-title">
            <div>
              <h2>统计范围</h2>
              <p>按日、月、年汇总，最多366天；部分周期按所选日期截断</p>
            </div>
            <button
              className="outline"
              disabled={loading}
              onClick={() => setRefresh(refresh + 1)}
            >
              刷新统计
            </button>
          </div>
          <form
            className="business-form"
            onSubmit={(e) => {
              e.preventDefault();
              setFilters({ ...draft });
            }}
          >
            <label>
              开始日期
              <input
                aria-label="统计开始日期"
                type="date"
                value={draft.start_date}
                onChange={(e) =>
                  setDraft({ ...draft, start_date: e.target.value })
                }
                required
              />
            </label>
            <label>
              结束日期
              <input
                aria-label="统计结束日期"
                type="date"
                value={draft.end_date}
                onChange={(e) =>
                  setDraft({ ...draft, end_date: e.target.value })
                }
                required
              />
            </label>
            <label>
              统计周期
              <select
                aria-label="统计周期"
                value={draft.granularity}
                onChange={(e) =>
                  setDraft({ ...draft, granularity: e.target.value })
                }
              >
                {Object.entries(grains).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
            <label>
              排名维度
              <select
                aria-label="统计排名维度"
                value={draft.dimension}
                onChange={(e) =>
                  setDraft({ ...draft, dimension: e.target.value })
                }
              >
                {Object.entries(dimensions).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
            <label>
              区域
              <input
                aria-label="统计区域"
                placeholder="全部区域"
                value={draft.district}
                maxLength={50}
                onChange={(e) =>
                  setDraft({ ...draft, district: e.target.value })
                }
              />
            </label>
            <label>
              运营商
              <select
                aria-label="统计运营商"
                value={draft.operator_id}
                onChange={(e) =>
                  setDraft({ ...draft, operator_id: e.target.value })
                }
              >
                <option value="">授权范围全部</option>
                {operators.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name}
                  </option>
                ))}
              </select>
            </label>
            <button className="outline" disabled={loading}>
              查询统计
            </button>
            {canExport && role !== "association" && data && (
              <a
                className="outline export-link"
                href={"/api/v1/statistics/analysis/export?" + params}
              >
                导出周期分析CSV
              </a>
            )}
            {canExport && role !== "association" && data && (
              <a
                className="outline export-link"
                href={"/api/v1/statistics/export?" + params}
              >
                导出场站电量CSV
              </a>
            )}
          </form>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          {loading && (
            <p className="empty" role="status">
              正在汇总统计数据…
            </p>
          )}
        </section>
        {data && (
          <>
            <section className="metrics">
              {[
                ["授权场站", data.station_count, "个"],
                ["充电接口", data.connectors, "个"],
                ["观测电量", data.total_kwh ?? "暂无数据", "kWh"],
                [
                  "有观测日期",
                  `${data.observed_days} / ${data.requested_days}`,
                  "天",
                ],
              ].map(([label, value, unit]) => (
                <article key={String(label)}>
                  <div>{label}</div>
                  <strong>
                    {value}
                    <small>{unit}</small>
                  </strong>
                  <p>
                    统计区间 {data.start_date} 至 {data.end_date}
                  </p>
                </article>
              ))}
            </section>
            <section className="panel">
              <div className="panel-title">
                <div>
                  <h2>观测电量同比与环比</h2>
                  <p>
                    只在同一授权站点集合两期每日有且仅有一条记录、来源相同且区间已结束时计算变化率；源系统完整性未认证。
                  </p>
                </div>
              </div>
              <div className="business-form">
                {Object.entries(data.comparisons).map(([key, c]) => (
                  <article className="comparison-card" key={key}>
                    <h3>
                      {key === "year_over_year"
                        ? "同比观测变化"
                        : `${grains[data.granularity]}周期环比观测变化`}
                    </h3>
                    <strong>
                      {c.observed_change_percent === null
                        ? "暂不可计算"
                        : `${c.observed_change_percent}%`}
                    </strong>
                    <p>
                      比较期 {c.period.start_date} 至 {c.period.end_date}
                    </p>
                    <p>{c.period.kwh ?? "暂无数据"} kWh</p>
                    <p className="muted">
                      {c.unavailable_reason ??
                        "观测记录满足计算条件，不代表已认证业务增长"}
                    </p>
                  </article>
                ))}
              </div>
            </section>
            <section className="panel">
              <div className="panel-title">
                <div>
                  <h2>按{grains[data.granularity]}电量走势</h2>
                  <p>
                    灰色方块表示该周期无记录；有记录但覆盖不全的周期仍为部分观测，详见明细
                  </p>
                </div>
                <span className="muted">单位 kWh</span>
              </div>
              <div className="energy-chart">
                <svg
                  role="img"
                  aria-label={`$按{grains[data.granularity]}电量走势：蓝色为有观测电量，灰色为缺失周期`}
                  viewBox={`0 0 ${Math.max(720, data.timeline.length * 24)} 200`}
                  style={{ minWidth: Math.max(720, data.timeline.length * 24) }}
                >
                  {data.timeline.map((d, i) => {
                    const width =
                      Math.max(720, data.timeline.length * 24) /
                      data.timeline.length;
                    const barWidth = Math.min(width * 0.6, 54);
                    const height =
                      d.kwh === null ? 0 : (Number(d.kwh) / maximum) * 145;
                    return (
                      <g key={d.period}>
                        <title>
                          {d.period}：{d.kwh ?? "无观测"} kWh，{d.station_days}
                          个有记录场站日
                        </title>
                        <rect
                          x={i * width + width * 0.5 - barWidth * 0.5}
                          y={d.kwh === null ? 166 : 170 - height}
                          width={barWidth}
                          height={d.kwh === null ? 4 : Math.max(1, height)}
                          fill={d.kwh === null ? "#ccd6e2" : "#387ab6"}
                          rx={2}
                        />
                        {(i === 0 ||
                          i === data.timeline.length - 1 ||
                          i % Math.ceil(data.timeline.length / 10) === 0) && (
                          <text
                            x={i * width + width * 0.5}
                            y={192}
                            textAnchor="middle"
                            fontSize="10"
                            fill="#6a7c92"
                          >
                            {data.granularity === "day"
                              ? d.period.slice(5)
                              : d.period}
                          </text>
                        )}
                      </g>
                    );
                  })}
                </svg>
              </div>
              <details className="statistics-detail">
                <summary>查看周期观测与比较明细</summary>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>周期 / 实际日期</th>
                        <th>电量kWh</th>
                        <th>记录数</th>
                        <th>有记录 / 应有场站日</th>
                        <th>同比观测变化</th>
                        <th>环比观测变化</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.timeline.map((d) => (
                        <tr key={d.period} data-testid={`period-${d.period}`}>
                          <td>
                            {d.period}
                            <small className="muted">
                              {" "}
                              · {d.start_date} 至 {d.end_date}
                            </small>
                          </td>
                          <td>{d.kwh ?? "暂无数据"}</td>
                          <td>{d.records}</td>
                          <td>
                            {d.station_days} / {d.expected_station_days}
                          </td>
                          <td>
                            {d.comparisons.year_over_year
                              .observed_change_percent === null
                              ? d.comparisons.year_over_year.unavailable_reason
                              : `${d.comparisons.year_over_year.observed_change_percent}%`}
                          </td>
                          <td>
                            {d.comparisons.period_over_period
                              .observed_change_percent === null
                              ? d.comparisons.period_over_period
                                  .unavailable_reason
                              : `${d.comparisons.period_over_period.observed_change_percent}%`}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            </section>
            <section className="panel">
              <div className="panel-title">
                <div>
                  <h2>{dimensions[data.dimension]}电量排行</h2>
                  <p>有观测的场站按电量排列，缺少记录的场站保留在表尾</p>
                </div>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      {[
                        dimensions[data.dimension],
                        "场站数",
                        "接口数",
                        "总功率kW",
                        "观测电量kWh",
                      ].map((h) => (
                        <th key={h}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.dimension_ranking.map((s) => (
                      <tr key={s.key}>
                        <td>{s.name}</td>
                        <td>{s.station_count}</td>
                        <td>{s.connectors}</td>
                        <td>{s.power_kw}</td>
                        <td>{s.kwh ?? "暂无数据"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!data.ranking.length && (
                  <p className="empty">当前筛选范围没有场站</p>
                )}
              </div>
            </section>
            <section className="panel">
              <div className="panel-title">
                <div>
                  <h2>口径与数据完整性</h2>
                  <p>
                    生成时间{" "}
                    {new Date(data.generated_at).toLocaleString("zh-CN")} · 时区{" "}
                    {data.timezone}
                  </p>
                </div>
              </div>
              <div className="business-form">
                <div>
                  <h3>前一等长区间观测</h3>
                  <p>
                    {data.previous_period.start_date} 至{" "}
                    {data.previous_period.end_date}
                  </p>
                  <p>
                    {data.previous_period.kwh ?? "暂无数据"} kWh · 有记录
                    {data.previous_period.observed_days}天
                  </p>
                  <p className="muted">
                    等长区间仅供观测对照；月年比较窗口见上方定义。
                  </p>
                </div>
                <div>
                  <h3>场站业务状态</h3>
                  {Object.entries(data.status_distribution).map(
                    ([key, count]) => (
                      <p key={key}>
                        {states[key] || key}：{count}个
                      </p>
                    ),
                  )}
                </div>
              </div>
              <div className="statistics-detail">
                <p>{data.basis}</p>
                <p className="muted">{data.comparison_date_basis}</p>
                <p className="muted">{data.energy_date_basis}</p>
                {Object.entries(data.unavailable).map(([key, reason]) => (
                  <p className="muted" key={key}>
                    {labels[key]}：暂无数据，{reason}
                  </p>
                ))}
              </div>
            </section>
          </>
        )}
      </div>
    </div>
  );
}
