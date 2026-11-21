"use client";
import { usePageRefresh } from "./page-refresh";
import { FormEvent, useEffect, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { useCapability } from "./permissions";
import { QualitySummary } from "./quality-summary";
import { MeterSourcePicker } from "./meter-source-picker";
import { RuntimeAxes, formatObservedNumber as number } from "./runtime-axes";

type Period = "custom" | "month" | "year";
function periodWindow(mode: Exclude<Period, "custom">, value: string) {
  const [year, month = 1] = value.split("-").map(Number);
  if (
    !Number.isInteger(year) ||
    year < 1900 ||
    year > 9998 ||
    !Number.isInteger(month) ||
    month < 1 ||
    month > 12
  )
    return null;
  return {
    start_at: `${year}-${String(mode === "year" ? 1 : month).padStart(2, "0")}-01T00:00`,
    end_at:
      new Date(
        Date.UTC(
          mode === "year" ? year + 1 : year,
          mode === "year" ? 0 : month,
          1,
        ),
      )
        .toISOString()
        .slice(0, 10) + "T00:00",
  };
}
type Filters = {
  source: string;
  group_by: "district" | "operator" | "station";
  asset_scope: "equipment" | "connector";
  q: string;
  district: string;
  start_at: string;
  end_at: string;
};
const groups: Record<Filters["group_by"], string> = {
  district: "按当前区域",
  operator: "按当前运营企业",
  station: "按当前场站",
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
    group_by: "district",
    asset_scope: "connector",
    q: "",
    district: "",
    start_at: `${year}-${String(month).padStart(2, "0")}-01T00:00`,
    end_at:
      new Date(Date.UTC(year, month, 1)).toISOString().slice(0, 10) + "T00:00",
  };
}
function params(filters: Filters) {
  return {
    ...filters,
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
type Stream = ApiSchemas["QualityStream"];
function AxisBrief({
  stream,
  dimension,
}: {
  stream: Stream;
  dimension: "connectivity" | "operation";
}) {
  if (!stream.observed_object_count)
    return <span className="quality-no-data">无运行区间样本</span>;
  const axis = stream.axes.find((value) => value.dimension === dimension)!;
  return (
    <>
      已上送{number(axis.reported_seconds)}秒
      <span>明确未知{number(axis.unknown_seconds)}秒</span>
      <span>观测集合内未上送{number(axis.unreported_seconds)}秒</span>
    </>
  );
}
export function QualityPanel({
  purpose,
}: { purpose?: "alarm" | "utilization" } = {}) {
  const canExport = useCapability("statistics.export");
  const [period, setPeriod] = useState<Period>("custom");
  const [periodValue, setPeriodValue] = useState(() =>
    initial().start_at.slice(0, 7),
  );
  const [draft, setDraft] = useState<Filters>(initial),
    [query, setQuery] = useState<{
      filters: Filters;
      page: number;
      refresh: number;
    } | null>(null);
  const [data, setData] = useState<ApiSchemas["QualityReport"] | null>(null),
    [busy, setBusy] = useState(false),
    [exporting, setExporting] = useState(false),
    [error, setError] = useState("");
  const [focus, setFocus] = useState<{ key: string; demo: boolean } | null>(
    null,
  );
  const pageRefreshRevision = usePageRefresh();
  useEffect(() => {
    if (!query) return;
    const controller = new AbortController();
    setBusy(true);
    setError("");
    setData(null);
    setFocus(null);
    typedApi
      .GET("/api/v1/statistics/quality", {
        params: {
          query: { ...params(query.filters), page: query.page, page_size: 20 },
        },
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "质量观测读取失败"));
        const last = Math.max(1, Math.ceil(data.total / data.page_size));
        if (query.page > last)
          setQuery((current) =>
            current ? { ...current, page: last } : current,
          );
        else setData(data);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "质量观测读取失败");
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
      !draft.source.trim() ||
      Number.isNaN(+start) ||
      Number.isNaN(+end) ||
      start >= end ||
      +end - +start > 366 * 86400000
    ) {
      setError("请选择来源及北京时间窗口，结束晚于开始且最多366天");
      return;
    }
    setQuery({
      filters: { ...draft },
      page: 1,
      refresh: (query?.refresh ?? 0) + 1,
    });
  }
  async function download() {
    if (!data) return;
    setExporting(true);
    setError("");
    try {
      const { data: file, error } = await typedApi.GET(
        "/api/v1/statistics/quality/export",
        {
          params: {
            query: {
              source: data.source,
              group_by: data.group_by,
              asset_scope: data.asset_scope,
              q: data.q,
              district: data.district,
              start_at: data.start_at,
              end_at: data.end_at,
              ...(data.operator_id === null
                ? {}
                : { operator_id: data.operator_id }),
            },
          },
          parseAs: "blob",
        },
      );
      if (!file)
        throw new Error(
          apiError(error, "导出失败，单次最多10000行（含总计），请缩小范围"),
        );
      const url = URL.createObjectURL(file),
        link = document.createElement("a");
      link.href = url;
      link.download = "ronghub-quality-observations.csv";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "导出失败");
    } finally {
      setExporting(false);
    }
  }
  const focusedGroup = data?.items.find((group) => group.key === focus?.key),
    focusedStream = focusedGroup?.streams.find(
      (stream) => stream.is_demo === focus?.demo,
    );
  return (
    <section className="panel quality-panel">
      <div className="panel-title">
        <div>
          <span className="quality-section-label">告警分析</span>
          <h2>
            {purpose === "alarm"
              ? "告警分析汇总"
              : purpose === "utilization"
                ? "设备平均利用率统计"
                : "区域与企业质量观测"}
          </h2>
          <p>结合当前台账、历史状态区间与源报故障，分别核对数据覆盖</p>
        </div>
        <div className="action-row">
          <button
            className="outline"
            disabled={busy || !query}
            onClick={() =>
              setQuery((current) =>
                current
                  ? { ...current, refresh: current.refresh + 1 }
                  : current,
              )
            }
          >
            刷新质量观测
          </button>
          {canExport && (
            <button
              className="outline"
              disabled={busy || exporting || !data}
              onClick={download}
            >
              {exporting ? "正在导出…" : "导出当前质量查询"}
            </button>
          )}
        </div>
      </div>
      <form className="application-filters quality-filters" onSubmit={apply}>
        <label>
          周期选择（北京）
          <select
            aria-label="质量周期方式"
            value={period}
            onChange={(e) => {
              const mode = e.target.value;
              if (mode === "custom" || mode === "month" || mode === "year") {
                setPeriod(mode);
                if (mode !== "custom") {
                  setPeriodValue(
                    draft.start_at.slice(0, mode === "year" ? 4 : 7),
                  );
                  const selected = periodWindow(
                    mode,
                    draft.start_at.slice(0, mode === "year" ? 4 : 7),
                  );
                  if (selected) setDraft({ ...draft, ...selected });
                }
              }
            }}
          >
            <option value="custom">自定义时间窗</option>
            <option value="month">按月</option>
            <option value="year">按年</option>
          </select>
        </label>
        {period !== "custom" && (
          <label>
            {period === "month" ? "月份" : "年份"}
            <input
              aria-label={period === "month" ? "质量月份" : "质量年份"}
              type={period === "month" ? "month" : "number"}
              min={period === "month" ? "1900-01" : "1900"}
              max={period === "month" ? "9998-12" : "9998"}
              required
              value={periodValue}
              onChange={(e) => {
                setPeriodValue(e.target.value);
                const selected = periodWindow(period, e.target.value);
                if (selected) setDraft({ ...draft, ...selected });
              }}
            />
          </label>
        )}
        <MeterSourcePicker
          kind="quality"
          value={draft.source}
          onChange={(source) => setDraft((current) => ({ ...current, source }))}
          district={draft.district}
          operatorId=""
        />
        <label>
          分组方式
          <select
            aria-label="质量分组方式"
            value={draft.group_by}
            onChange={(e) =>
              setDraft({
                ...draft,
                group_by: e.target.value as Filters["group_by"],
              })
            }
          >
            {Object.entries(groups).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          运行区间层级
          <select
            aria-label="质量运行区间层级"
            value={draft.asset_scope}
            onChange={(e) =>
              setDraft({
                ...draft,
                asset_scope: e.target.value as Filters["asset_scope"],
              })
            }
          >
            <option value="connector">单枪源对象（不合计设备本级）</option>
            <option value="equipment">设备本级（不合计枪级）</option>
          </select>
        </label>
        <label>
          场站或企业名称
          <input
            aria-label="质量站企名称筛选"
            value={draft.q}
            maxLength={150}
            onChange={(e) => setDraft({ ...draft, q: e.target.value })}
          />
        </label>
        <label>
          当前区域（精确名称）
          <input
            aria-label="质量区域筛选"
            value={draft.district}
            maxLength={50}
            placeholder="留空查询全部授权区域"
            onChange={(e) => setDraft({ ...draft, district: e.target.value })}
          />
        </label>
        <label>
          开始时间（北京，含）
          <input
            aria-label="质量开始时间"
            type="datetime-local"
            step="1"
            required
            value={draft.start_at}
            onChange={(e) => {
              setPeriod("custom");
              setDraft({ ...draft, start_at: e.target.value });
            }}
          />
        </label>
        <label>
          结束时间（北京，不含）
          <input
            aria-label="质量结束时间"
            type="datetime-local"
            step="1"
            required
            value={draft.end_at}
            onChange={(e) => {
              setPeriod("custom");
              setDraft({ ...draft, end_at: e.target.value });
            }}
          />
        </label>
        <button className="primary" disabled={busy || !draft.source}>
          查询质量观测
        </button>
      </form>
      <p className="muted application-list-note">
        当前台账中没有观测的场站仍保留。源对象数不等于设备总数；故障事件包含所有层级。完整性未核实，当前不计算正式在线率、故障率或利用率。
      </p>
      {purpose && !data && !busy && (
        <div className="table-wrap prototype-quality-empty">
          <table>
            <thead>
              <tr>
                <th>区域／运营商／场站</th>
                <th>设备数</th>
                <th>{purpose === "alarm" ? "在线率" : "设备平均利用率"}</th>
                {purpose === "alarm" && (
                  <>
                    <th>故障率</th>
                    <th>平均修复时间</th>
                  </>
                )}
              </tr>
            </thead>
            <tbody>
              <tr>
                <td colSpan={purpose === "alarm" ? 5 : 3}>
                  请选择来源、统计周期及分组后查询
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
      {error && (
        <p className="error application-list-note" role="alert">
          {error}
        </p>
      )}
      {busy && (
        <p className="empty" role="status">
          正在读取质量观测…
        </p>
      )}
      {!query && !busy && (
        <p className="empty">请选择来源及时间，查询区域与企业质量观测</p>
      )}
      {data && (
        <>
          <div className="runtime-applied">
            <strong>
              {data.source} · {groups[data.group_by]} ·{" "}
              {data.asset_scope === "connector"
                ? "单枪运行区间"
                : "设备本级运行区间"}
            </strong>
            <p>
              {stamp(data.start_at)} 至 {stamp(data.end_at)}（北京，结束不含）
            </p>
            <p>
              当前查询台账{data.selected_station_count}站 · {data.total}个分组
              {data.q ? ` · 站企检索：${data.q}` : ""}
              {data.district ? ` · 区域：${data.district}` : ""}
            </p>
          </div>
          {purpose && (
            <section
              className="panel prototype-quality-table"
              aria-label={
                purpose === "alarm" ? "告警分析主表" : "平均利用率主表"
              }
            >
              <div className="panel-title">
                <div>
                  <h3>
                    {purpose === "alarm"
                      ? "场站与企业告警汇总"
                      : "区域／运营商／场站利用率"}
                  </h3>
                  <p>
                    正式比率和维修工时待完整来源；观测占比及恢复经过时长单独列示，不代替正式指标。
                  </p>
                </div>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>分组名称</th>
                      <th>设备数（当前）</th>
                      <th>数据类别</th>
                      {purpose === "alarm" ? (
                        <>
                          <th>在线率</th>
                          <th>故障率</th>
                          <th>平均修复时间</th>
                          <th>已恢复样本平均经过时长</th>
                        </>
                      ) : (
                        <>
                          <th>设备平均利用率</th>
                          <th>观测充电占比（%）</th>
                        </>
                      )}
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.flatMap((group) =>
                      group.streams.length
                        ? group.streams.map((stream) => (
                            <tr key={group.key + String(stream.is_demo)}>
                              <td>{group.label}</td>
                              <td>{group.inventory_equipment_count}</td>
                              <td>{stream.is_demo ? "演示" : "源报"}</td>
                              {purpose === "alarm" ? (
                                <>
                                  <td>待覆盖确认</td>
                                  <td>待覆盖确认</td>
                                  <td>待维修工单</td>
                                  <td>
                                    {stream.faults
                                      .average_recovery_elapsed_seconds == null
                                      ? "暂无样本"
                                      : `${stream.faults.average_recovery_elapsed_seconds} 秒`}
                                  </td>
                                </>
                              ) : (
                                <>
                                  <td>待可用时长分母</td>
                                  <td>
                                    {stream.axes
                                      .find((a) => a.dimension === "operation")
                                      ?.states.find(
                                        (s) => s.state === "charging",
                                      )?.share_of_reported_pct ?? "暂无样本"}
                                  </td>
                                </>
                              )}
                              <td>
                                <button
                                  className="outline"
                                  onClick={() =>
                                    setFocus({
                                      key: group.key,
                                      demo: stream.is_demo,
                                    })
                                  }
                                >
                                  查看观测明细
                                </button>
                              </td>
                            </tr>
                          ))
                        : [
                            <tr key={group.key}>
                              <td>{group.label}</td>
                              <td>{group.inventory_equipment_count}</td>
                              <td>无观测</td>
                              {purpose === "alarm" ? (
                                <>
                                  <td>待覆盖确认</td>
                                  <td>待覆盖确认</td>
                                  <td>待维修工单</td>
                                  <td>暂无样本</td>
                                </>
                              ) : (
                                <>
                                  <td>待可用时长分母</td>
                                  <td>暂无样本</td>
                                </>
                              )}
                              <td>暂无观测明细</td>
                            </tr>,
                          ],
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          )}
          <QualitySummary summary={data.summary} />
          <details className="quality-method">
            <summary>统计口径与数据覆盖说明</summary>
            <p className="muted">{data.notice}</p>
          </details>
          {!data.items.length ? (
            <p className="empty">当前条件下没有质量观测分组</p>
          ) : (
            <div
              className="table-wrap"
              tabIndex={0}
              role="region"
              aria-label="质量分组明细，可横向滚动"
            >
              <table>
                <thead>
                  <tr>
                    {[
                      "当前分组 / 标识",
                      "台账 / 运行站数",
                      "观测源对象",
                      "连接状态",
                      "业务状态",
                      "源报故障事件",
                      "已恢复样本均值",
                      "详情",
                    ].map((label) => (
                      <th key={label}>{label}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.items.flatMap((group) =>
                    (group.streams.length ? group.streams : [null]).map(
                      (stream) => (
                        <tr
                          key={JSON.stringify([
                            group.key,
                            stream?.is_demo ?? null,
                          ])}
                        >
                          <td>
                            {group.label}
                            <span>
                              {group.key} ·{" "}
                              {stream
                                ? stream.is_demo
                                  ? "演示"
                                  : "源报"
                                : "无观测数据"}
                            </span>
                          </td>
                          <td>
                            {group.station_count}站
                            <span>
                              当前台账设备{group.inventory_equipment_count}台
                            </span>
                            <span>
                              {stream
                                ? `有运行区间${stream.stations_with_runtime}站`
                                : "无运行区间样本"}
                            </span>
                          </td>
                          <td>
                            {stream
                              ? `${stream.observed_object_count}个`
                              : "暂无观测"}
                            {stream && (
                              <span>
                                对象×查询时长{" "}
                                {number(stream.observed_object_window_seconds)}
                                秒
                              </span>
                            )}
                          </td>
                          <td>
                            {stream ? (
                              <AxisBrief
                                stream={stream}
                                dimension="connectivity"
                              />
                            ) : (
                              "暂无观测"
                            )}
                          </td>
                          <td>
                            {stream ? (
                              <AxisBrief
                                stream={stream}
                                dimension="operation"
                              />
                            ) : (
                              "暂无观测"
                            )}
                          </td>
                          <td>
                            {stream ? (
                              <>
                                已观测{stream.faults.observed_events}件
                                <span>
                                  未恢复{stream.faults.active_events} / 已恢复
                                  {stream.faults.recovered_events} / 未知
                                  {stream.faults.unknown_events}
                                </span>
                                <span>不限设备/枪层级</span>
                              </>
                            ) : (
                              "暂无观测"
                            )}
                          </td>
                          <td>
                            {stream?.faults.average_recovery_elapsed_seconds !=
                            null
                              ? `${number(stream.faults.average_recovery_elapsed_seconds)}秒`
                              : "暂无已恢复样本"}
                          </td>
                          <td>
                            {stream && (
                              <button
                                className="outline"
                                aria-label={`查看${group.label}${stream.is_demo ? "演示" : "源报"}状态组成`}
                                onClick={() =>
                                  setFocus({
                                    key: group.key,
                                    demo: stream.is_demo,
                                  })
                                }
                              >
                                查看
                              </button>
                            )}
                          </td>
                        </tr>
                      ),
                    ),
                  )}
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
              质量上一页
            </button>
            <span>
              质量第{data.page}页 · 共{data.total}组
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
              质量下一页
            </button>
          </div>
          {focusedGroup && focusedStream && (
            <section className="runtime-stream quality-details">
              <h3>
                {focusedGroup.label} · {focusedStream.is_demo ? "演示" : "源报"}{" "}
                · 观测状态组成
              </h3>
              <p className="muted">
                本组{focusedStream.observed_object_count}
                个已观测源对象，查询时间格
                {number(focusedStream.observed_object_window_seconds)}
                秒；不包含未上送任何区间的设备，不代表营业可用时长。
              </p>
              {focusedStream.observed_object_count ? (
                <RuntimeAxes
                  axes={focusedStream.axes}
                  windowSeconds={focusedStream.observed_object_window_seconds}
                  demo={focusedStream.is_demo}
                />
              ) : (
                <p className="empty">
                  本组仅有故障事件，没有当前层级的运行区间样本。
                </p>
              )}
              <p className="muted">
                故障恢复均值仅取已恢复样本完整经过时长，进行中/未知不记零，不代表维修工时或完整故障率。
              </p>
            </section>
          )}
        </>
      )}
    </section>
  );
}
