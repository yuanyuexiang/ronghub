"use client";
import { useEffect, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { usePageRefresh } from "./page-refresh";
type Report = ApiSchemas["OperationsDashboard"];
const statuses: Record<string, string> = {
  pending: "待接入",
  access_approved: "接入通过",
  accepted: "验收通过",
  operating: "运营中",
  decommissioned: "已核销",
};
const kinds: Record<string, string> = {
  charging: "充电站",
  swapping: "换电站",
  mixed: "充换电混合",
  unknown: "类型未提供",
};
const noLabels: Record<string, string> = {};
const number = (value: string | null) =>
  value == null
    ? "暂无观测"
    : value.includes(".")
      ? value.replace(/0+$/, "").replace(/\.$/, "")
      : value;
function initial() {
  return {
    period: "day" as Report["period"],
    period_date: new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(new Date()),
  };
}
export function DashboardOperations({
  variant = "operations",
}: {
  variant?: "operations" | "stations";
}) {
  const [draft, setDraft] = useState(initial),
    [query, setQuery] = useState(initial),
    [data, setData] = useState<Report | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const revision = usePageRefresh();
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    setError("");
    setData(null);
    typedApi
      .GET("/api/v1/dashboard/operations", {
        params: { query },
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "运营总览读取失败"));
        setData(data);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "运营总览读取失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [query, revision]);
  return (
    <section
      className="panel operations-dashboard"
      aria-label={variant === "stations" ? "场站统计汇总" : "运营周期总览"}
    >
      <div className="panel-title">
        <div>
          <h2>
            {variant === "stations" ? "场站接入与充电数据汇总" : "运营数据概览"}
          </h2>
          <p>当前场站分布与独立来源的周期观测</p>
        </div>
      </div>
      <form
        className="operations-period"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery({ ...draft });
        }}
      >
        <label>
          观测周期
          <select
            aria-label="总览观测周期"
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
            aria-label="总览周期日期"
            type="date"
            min="1900-01-01"
            max="9998-12-31"
            required
            value={draft.period_date}
            onChange={(e) =>
              setDraft({ ...draft, period_date: e.target.value })
            }
          />
        </label>
        <button className="primary" disabled={busy}>
          查询运营总览
        </button>
      </form>
      {error && (
        <p className="error application-list-note" role="alert">
          {error}
        </p>
      )}
      {busy && (
        <p className="empty" role="status">
          正在读取运营总览…
        </p>
      )}
      {data && (
        <>
          {variant === "stations" ? (
            <div className="operations-stock station-summary-stock">
              {[
                ["台账场站总数", data.current_stations, "个"],
                [
                  "已标注充电站",
                  data.station_types.find((item) => item.key === "charging")
                    ?.count ?? 0,
                  "个",
                ],
                [
                  "已标注换电站",
                  data.station_types.find((item) => item.key === "swapping")
                    ?.count ?? 0,
                  "个",
                ],
                [
                  "设备额定总功率",
                  number(data.current_equipment_power_kw),
                  "kW",
                ],
                [
                  "类型待完善",
                  data.station_types.find((item) => item.key === "unknown")
                    ?.count ?? 0,
                  "个",
                ],
              ].map(([label, value, unit]) => (
                <article key={String(label)}>
                  <span>{label}</span>
                  <strong>
                    {value}
                    <small>{unit}</small>
                  </strong>
                </article>
              ))}
            </div>
          ) : (
            <div className="operations-stock">
              <article>
                <span>当前台账场站</span>
                <strong>
                  {data.current_stations}
                  <small>站</small>
                </strong>
              </article>
              <article>
                <span>当前设备记录</span>
                <strong>
                  {data.current_equipment}
                  <small>台</small>
                </strong>
                <p className="muted">
                  台账额定功率 {number(data.current_equipment_power_kw)} kW
                </p>
              </article>
              <article>
                <span>有场站的运营商</span>
                <strong>
                  {data.operators_with_stations}
                  <small>家</small>
                </strong>
              </article>
              {data.approved_company_records != null && (
                <article>
                  <span>有审核通过记录的企业</span>
                  <strong>
                    {data.approved_company_records}
                    <small>家</small>
                  </strong>
                </article>
              )}
            </div>
          )}
          {variant === "stations" && (
            <p className="station-summary-notice">
              分类数量依据已明确标注的场站记录；充换电混合场站另列分类。电池柜、包数量和正式来源口径仍待对接。
            </p>
          )}
          <div className="operations-distributions">
            {(
              [
                ["场站业务状态", data.statuses, statuses],
                ["场站类型", data.station_types, kinds],
                ["当前区域分布", data.districts, noLabels],
              ] as const
            ).map(([title, groups, labels]) => (
              <article key={title} aria-label={title}>
                <h3>{title}</h3>
                {groups.length ? (
                  groups.map((group) => (
                    <div
                      className="operations-distribution-row"
                      key={group.key}
                    >
                      <span>
                        {(labels[group.key] ?? group.key) || "未标区域"}
                      </span>
                      <strong>
                        {group.count}站 · {group.share_pct ?? "暂无占比"}
                        {group.share_pct == null ? "" : "%"}
                      </strong>
                    </div>
                  ))
                ) : (
                  <p className="muted">当前授权范围暂无场站</p>
                )}
              </article>
            ))}
          </div>
          <div className="runtime-applied">
            <strong>
              观测区间：{data.start_date} 至 {data.end_date}（北京）
            </strong>
            <p>订单按开始时间，日电量按业务日；来源分别显示。</p>
          </div>
          {variant !== "stations" && data.orders !== null && (
            <section
              className="operations-observations"
              aria-label="总览订单观测"
            >
              <h3>充电订单观测</h3>
              <div className="operations-source-grid">
                {data.orders.map((stream) => (
                  <article
                    key={JSON.stringify([stream.source, stream.is_demo])}
                  >
                    <h4>
                      {stream.source} · {stream.is_demo ? "演示" : "源报"}
                    </h4>
                    <strong>
                      已完成 {stream.completed_orders} / 观测{" "}
                      {stream.observed_orders} 单
                    </strong>
                    <p>已知完成电量 {number(stream.kwh.value)} kWh</p>
                    <p>已知源报时长 {number(stream.duration_hours)} 小时</p>
                    <p>已知源报消费 {number(stream.amount.value)} 元</p>
                    <small>
                      完成订单缺失：电量 {stream.kwh.missing_orders} / 时长{" "}
                      {stream.duration_seconds.missing_orders} / 消费{" "}
                      {stream.amount.missing_orders}
                    </small>
                  </article>
                ))}
                {!data.orders.length && (
                  <p className="muted">
                    所选周期暂无来源订单，不推断业务量为零。
                  </p>
                )}
              </div>
            </section>
          )}
          {variant !== "stations" && data.energy !== null && (
            <section
              className="operations-observations"
              aria-label="总览日能量观测"
            >
              <h3>独立日电量观测</h3>
              <div className="operations-source-grid">
                {data.energy.map((stream) => (
                  <article key={stream.source}>
                    <h4>{stream.source}</h4>
                    <strong>{number(stream.kwh)} kWh</strong>
                    <p>
                      {stream.records} 条源记录 · 覆盖{" "}
                      {stream.observed_station_days} 个站日
                    </p>
                    <small>
                      同站日额外记录 {stream.duplicate_station_days}{" "}
                      条；不代表完整历史覆盖。
                    </small>
                  </article>
                ))}
                {!data.energy.length && (
                  <p className="muted">
                    所选周期暂无日电量观测，不由订单电量补造。
                  </p>
                )}
              </div>
            </section>
          )}
          {variant !== "stations" && data.approvals && (
            <section className="operations-approvals">
              <h3>接入申请当前状态</h3>
              <p>
                通过 {data.approvals.approved} · 驳回 {data.approvals.rejected}{" "}
                · 待审核 {data.approvals.submitted} · 其他{" "}
                {data.approvals.other}
              </p>
              <strong>正式审核通过率：待确认</strong>
              <small>{data.approvals.notice}</small>
            </section>
          )}
          {data.energy_trend !== null && (
            <section
              className="operations-observations"
              aria-label="最近12个月日电量趋势"
            >
              <h3>最近12个月日电量观测</h3>
              <p className="muted">
                {data.trend_start_date} 至 {data.trend_end_date}
                ，截至所选日期；空白月份未观测，不填零。
              </p>
              {data.energy_trend.map((stream) => {
                const max = Math.max(
                  ...stream.months.map((month) =>
                    month.kwh == null ? 0 : Number(month.kwh),
                  ),
                  1,
                );
                return (
                  <article className="operations-trend" key={stream.source}>
                    <h4>{stream.source}</h4>
                    <ol>
                      {stream.months.map((month) => (
                        <li
                          key={month.month}
                          title={`${month.month}：${number(month.kwh)} kWh，${month.records}条记录`}
                        >
                          <div className="operations-trend-track">
                            <span
                              data-observed={month.kwh !== null}
                              style={{
                                height:
                                  month.kwh === null
                                    ? "14px"
                                    : `${Math.max(2, (Number(month.kwh) / max) * 100)}%`,
                              }}
                            />
                          </div>
                          <strong>{number(month.kwh)}</strong>
                          <small>{month.month.slice(0, 7)}</small>
                        </li>
                      ))}
                    </ol>
                  </article>
                );
              })}
              {!data.energy_trend.length && (
                <p className="muted">最近12个月暂无日电量来源记录。</p>
              )}
            </section>
          )}
          <details className="quality-method">
            <summary>总览统计口径</summary>
            <p>{data.notice}</p>
          </details>
        </>
      )}
    </section>
  );
}
