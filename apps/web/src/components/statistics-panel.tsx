"use client";
import { useBusinessView } from "./business-view-context";
import { WorkspaceViews } from "./business-workspace";
import { QualityPanel } from "./quality-panel";
import { RuntimePanel } from "./runtime-panel";
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
  const [view, setView] = useBusinessView("energy");
  const combined = view === "combined" && canCombined;
  const construction = view === "district" || view === "operator";
  const [draft, setDraft] = useState(initial),
    [filters, setFilters] = useState(initial),
    [data, setData] = useState<Report | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false),
    [operators, setOperators] = useState<{ id: number; name: string }[]>([]);
  const effectiveFilters = {
    ...filters,
    dimension:
      view === "district"
        ? "district"
        : view === "operator"
          ? "operator"
          : filters.dimension,
  };
  const params = new URLSearchParams(
    Object.entries(effectiveFilters).filter(([, v]) => v !== ""),
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
            ...effectiveFilters,
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
  }, [filters, view, pageRefreshRevision]);
  const maximum = Math.max(
    1,
    ...(data?.timeline.map((d) => Number(d.kwh || 0)) || []),
  );
  const ranking = data && (
    <section className="panel">
      <div className="panel-title">
        <div>
          <h2>{dimensions[data.dimension]}电量排行</h2>
        </div>
      </div>
      <div className="table-wrap">
        <table className="aligned-records">
          <thead>
            <tr>
              {[
                dimensions[data.dimension],
                "场站数",
                "接口数",
                "总功率kW",
                "观测电量kWh",
              ].map((heading, column) => (
                <th
                  key={heading}
                  className={column > 0 ? "numeric-cell" : undefined}
                >
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.dimension_ranking.map((s) => (
              <tr key={s.key}>
                <td>{s.name}</td>
                <td className="numeric-cell">{s.station_count}</td>
                <td className="numeric-cell">{s.connectors}</td>
                <td className="numeric-cell">{s.power_kw}</td>
                <td className="numeric-cell">{s.kwh ?? "暂无数据"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!data.ranking.length && <p className="empty">当前筛选范围没有场站</p>}
      </div>
    </section>
  );
  return (
    <div className="business-stack statistics-workspace">
      <WorkspaceViews
        label="统计分析视图"
        value={view}
        onChange={setView}
        items={[
          ["energy", "观测电量分析"],
          ["district", "建站统计（区域）"],
          ["operator", "建站统计（运营商）"],
          ["power", "用电分析"],
          ...(role !== "association"
            ? [["utilization", "利用率统计"] as const]
            : []),
          ...(canCombined ? [["combined", "充电与补贴报表"] as const] : []),
        ]}
      />
      <div hidden={view !== "utilization"}>
        {role !== "association" && (
          <>
            <QualityPanel purpose="utilization" />
            <details className="prototype-related">
              <summary>单对象历史运行</summary>
              <div className="prototype-related-body">
                <RuntimePanel />
              </div>
            </details>
          </>
        )}
      </div>
      <div style={{ display: combined ? "block" : "none" }}>
        <ChargingSubsidyReport />
      </div>
      <div
        className="business-stack"
        style={{
          display: combined || view === "utilization" ? "none" : "grid",
        }}
      >
        <section
          className={construction ? "panel construction-statistics" : "panel"}
        >
          {!construction && (
            <div className="panel-title">
              <h2>统计范围</h2>
            </div>
          )}
          <form
            className={
              construction
                ? "business-form construction-filters"
                : "business-form"
            }
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
            {!construction && (
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
            )}
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
            <div className="statistics-actions">
              <button className="primary" disabled={loading}>
                查询统计
              </button>
              <button
                type="button"
                className="outline"
                disabled={loading}
                onClick={() => {
                  setDraft(initial);
                  setFilters({ ...initial });
                }}
              >
                重置筛选
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
            </div>
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
          {data && construction && (
            <>
              <div className="table-wrap">
                <table
                  className="aligned-records"
                  aria-label={
                    view === "district" ? "区域建站统计" : "运营商建站统计"
                  }
                >
                  <thead>
                    <tr>
                      {[
                        "序号",
                        dimensions[data.dimension],
                        "当前场站数",
                        "当前接口数",
                        "台账额定功率 kW",
                        "区间观测电量 kWh",
                        "建站数量同比 / 环比",
                        "额定功率同比 / 环比",
                      ].map((name, column) => (
                        <th
                          key={name}
                          className={
                            column >= 2 && column <= 5
                              ? "numeric-cell"
                              : undefined
                          }
                        >
                          {name}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.dimension_ranking.map((row, index) => (
                      <tr key={row.key}>
                        <td>{index + 1}</td>
                        <td>{row.name}</td>
                        <td className="numeric-cell">{row.station_count}</td>
                        <td className="numeric-cell">{row.connectors}</td>
                        <td className="numeric-cell">{row.power_kw}</td>
                        <td className="numeric-cell">
                          {row.kwh
                            ?.replace(/(\.\d*?)0+$/, "$1")
                            .replace(/\.$/, "") ?? "暂无观测"}
                        </td>
                        <td>暂无历史数据</td>
                        <td>暂无历史数据</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!data.dimension_ranking.length && (
                  <p className="empty">当前授权范围暂无场站</p>
                )}
              </div>
              <details className="table-note construction-statistics-note">
                <summary>查看统计口径</summary>
                <p className="muted">
                  场站数、接口数及额定功率来自当前台账。区间电量来自所选日期内的观测。
                  场站数和额定功率的同比与环比需有历史快照后计算。
                </p>
              </details>
            </>
          )}
        </section>
        {data && (
          <div
            className="business-stack"
            hidden={!["energy", "power"].includes(view)}
          >
            <p className="muted statistics-period">
              统计区间 {data.start_date} 至 {data.end_date}
            </p>
            {view === "energy" && ranking}
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
                  aria-label={`按{grains[data.granularity]}电量走势：蓝色为有观测电量，灰色为缺失周期`}
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
                  <table className="aligned-records">
                    <thead>
                      <tr>
                        <th>周期 / 实际日期</th>
                        <th className="numeric-cell">电量kWh</th>
                        <th className="numeric-cell">记录数</th>
                        <th className="numeric-cell">有记录 / 应有场站日</th>
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
                          <td className="numeric-cell">
                            {d.kwh ?? "暂无数据"}
                          </td>
                          <td className="numeric-cell">{d.records}</td>
                          <td className="numeric-cell">
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
            {view === "power" && ranking}
            <details className="prototype-related">
              <summary>查看统计口径与数据完整性</summary>
              <div className="prototype-related-body">
                <p className="muted">
                  生成时间{" "}
                  {new Date(data.generated_at).toLocaleString("zh-CN", {
                    timeZone: "Asia/Shanghai",
                  })}{" "}
                  · 时区 {data.timezone}
                </p>
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
              </div>
            </details>
          </div>
        )}
      </div>
    </div>
  );
}
