"use client";
import { usePageRefresh } from "./page-refresh";
import { FormEvent, useEffect, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { useCapability } from "./permissions";

type Filters = {
  q: string;
  status: string;
  source: string;
  equipment_external_id: string;
  start_at: string;
  end_at: string;
};
const empty: Filters = {
  q: "",
  status: "",
  source: "",
  equipment_external_id: "",
  start_at: "",
  end_at: "",
};
const states: Record<string, string> = {
  unknown: "源状态未知",
  active: "未恢复",
  recovered: "已恢复",
};
function params(filters: Filters) {
  return {
    ...filters,
    start_at: filters.start_at
      ? new Date(filters.start_at + "+08:00").toISOString()
      : undefined,
    end_at: filters.end_at
      ? new Date(filters.end_at + "+08:00").toISOString()
      : undefined,
  };
}
function stamp(value: string | null) {
  return value
    ? new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai",
        dateStyle: "short",
        timeStyle: "medium",
      }).format(new Date(value))
    : "暂无数据";
}
export function FaultsPanel() {
  const canExport = useCapability("statistics.export");
  const [draft, setDraft] = useState(empty);
  const [query, setQuery] = useState({ filters: empty, page: 1, refresh: 0 });
  const [data, setData] = useState<ApiSchemas["FaultEventPage"] | null>(null);
  const [summary, setSummary] = useState<ApiSchemas["FaultSummary"] | null>(
    null,
  );
  const [busy, setBusy] = useState(false),
    [exporting, setExporting] = useState(false),
    [error, setError] = useState("");
  const pageRefreshRevision = usePageRefresh();
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    setData(null);
    setSummary(null);
    setError("");
    Promise.all([
      typedApi.GET("/api/v1/statistics/faults", {
        params: {
          query: { ...params(query.filters), page: query.page, page_size: 20 },
        },
        signal: controller.signal,
        cache: "no-store",
      }),
      typedApi.GET("/api/v1/statistics/faults/summary", {
        params: { query: params(query.filters) },
        signal: controller.signal,
        cache: "no-store",
      }),
    ])
      .then(([list, summary]) => {
        if (controller.signal.aborted) return;
        if (!list.data) throw new Error(apiError(list.error, "故障读取失败"));
        if (!summary.data)
          throw new Error(apiError(summary.error, "故障汇总失败，请缩小范围"));
        const last = Math.max(
          1,
          Math.ceil(list.data.total / list.data.page_size),
        );
        if (query.page > last)
          setQuery((current) => ({ ...current, page: last }));
        else {
          setData(list.data);
          setSummary(summary.data);
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "故障读取失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [query, pageRefreshRevision]);
  function apply(e: FormEvent) {
    e.preventDefault();
    if (
      draft.start_at &&
      draft.end_at &&
      new Date(draft.start_at + "+08:00") >= new Date(draft.end_at + "+08:00")
    ) {
      setError("结束时间须晚于开始时间");
      return;
    }
    setQuery({ filters: { ...draft }, page: 1, refresh: query.refresh + 1 });
  }
  async function download() {
    setExporting(true);
    setError("");
    try {
      if (!query.filters.start_at || !query.filters.end_at)
        throw new Error(
          "导出须先查询明确的发生时间范围，最多跨2个北京时间自然月",
        );
      const { data: blob, error } = await typedApi.GET(
        "/api/v1/statistics/faults/export",
        { params: { query: params(query.filters) }, parseAs: "blob" },
      );
      if (!blob)
        throw new Error(apiError(error, "故障导出失败，请缩小查询范围后重试"));
      const url = URL.createObjectURL(blob),
        link = document.createElement("a");
      link.href = url;
      link.download = "ronghub-fault-events.csv";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "故障导出失败");
    } finally {
      setExporting(false);
    }
  }
  return (
    <section className="panel faults-panel">
      <div className="panel-title">
        <div>
          <h2>源报故障事件</h2>
          <p>保留来源故障生命周期，与平台告警确认分别查询</p>
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
            刷新故障
          </button>
          {canExport && (
            <button
              className="outline"
              disabled={busy || exporting}
              onClick={download}
            >
              {exporting ? "正在导出…" : "导出当前故障查询"}
            </button>
          )}
        </div>
      </div>
      <form className="application-filters" onSubmit={apply}>
        <label>
          事件号、故障码、场站或企业
          <input
            aria-label="故障检索名称"
            value={draft.q}
            maxLength={150}
            onChange={(e) => setDraft({ ...draft, q: e.target.value })}
          />
        </label>
        <label>
          源桩编码
          <input
            aria-label="故障源桩编码"
            value={draft.equipment_external_id}
            maxLength={100}
            onChange={(e) =>
              setDraft({ ...draft, equipment_external_id: e.target.value })
            }
          />
        </label>
        <label>
          故障状态
          <select
            aria-label="故障筛选状态"
            value={draft.status}
            onChange={(e) => setDraft({ ...draft, status: e.target.value })}
          >
            <option value="">全部状态</option>
            {Object.entries(states).map(([key, value]) => (
              <option key={key} value={key}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label>
          数据来源
          <input
            aria-label="故障数据来源"
            value={draft.source}
            maxLength={50}
            onChange={(e) => setDraft({ ...draft, source: e.target.value })}
          />
        </label>
        <label>
          发生时间起（北京）
          <input
            aria-label="故障开始时间起"
            type="datetime-local"
            value={draft.start_at}
            onChange={(e) => setDraft({ ...draft, start_at: e.target.value })}
          />
        </label>
        <label>
          发生时间止（北京，不含）
          <input
            aria-label="故障开始时间止"
            type="datetime-local"
            value={draft.end_at}
            onChange={(e) => setDraft({ ...draft, end_at: e.target.value })}
          />
        </label>
        <button className="primary" disabled={busy}>
          查询故障
        </button>
        <button
          type="button"
          className="outline"
          disabled={busy}
          onClick={() => {
            setDraft(empty);
            setQuery({ filters: empty, page: 1, refresh: query.refresh + 1 });
          }}
        >
          重置故障筛选
        </button>
      </form>
      <p className="muted application-list-note">
        {data?.notice ??
          "按故障发生时间筛选，开始包含、结束不含；缺失保留未知，恢复经过时长不代表维修工时。"}
      </p>
      <p className="muted application-list-note">
        单次导出须明确发生时间范围，最多跨2个北京时间自然月、10000条；汇总平均值仅取已恢复样本。
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {busy && (
        <p className="empty" role="status">
          正在读取故障…
        </p>
      )}
      {data && (
        <>
          {summary && summary.streams.length > 0 && (
            <div className="fault-summary" aria-label="故障源内观测汇总">
              {summary.streams.map((stream) => (
                <article key={`${stream.source}-${stream.is_demo}`}>
                  <strong>
                    {stream.source} · {stream.is_demo ? "演示数据" : "源报数据"}
                  </strong>
                  <p>
                    观测{stream.observed_events}件 · 未恢复
                    {stream.active_events} · 已恢复{stream.recovered_events} ·
                    未知{stream.unknown_events}
                  </p>
                  <p>
                    已恢复样本平均经过时长：
                    {stream.average_recovery_elapsed_seconds == null
                      ? "暂无样本"
                      : `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 6 }).format(Number(stream.average_recovery_elapsed_seconds))}秒`}
                  </p>
                  <small>
                    仅当前发生时间筛选内的已恢复样本；进行中和未知不参与平均。
                  </small>
                </article>
              ))}
            </div>
          )}
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {[
                    "故障 / 来源",
                    "场站 / 企业",
                    "源桩 / 枪编码",
                    "状态",
                    "发生 / 恢复（北京）",
                    "源故障码 / 等级",
                    "恢复经过时长（秒）",
                  ].map((label) => (
                    <th key={label}>{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.items.map((order) => (
                  <tr key={order.id}>
                    <td>
                      {order.external_id}
                      <span>
                        FAULT-{order.id} · {order.source}
                        {order.is_demo ? " · 演示" : ""}
                      </span>
                    </td>
                    <td>
                      {order.station_name}
                      <span>
                        {order.operator_name} · {order.district}
                      </span>
                    </td>
                    <td>
                      {order.equipment_external_id ?? "暂无数据"}
                      <span>{order.connector_external_id ?? "暂无枪编码"}</span>
                    </td>
                    <td>{states[order.status] ?? "源状态未知"}</td>
                    <td>
                      {stamp(order.started_at)}
                      <span>{stamp(order.recovered_at)}</span>
                    </td>
                    <td>
                      {order.fault_code ?? "暂无故障码"}
                      <span>
                        {
                          {
                            unknown: "未知等级",
                            info: "提示",
                            warning: "警告",
                            critical: "严重",
                          }[order.severity]
                        }
                      </span>
                    </td>
                    <td>{order.recovery_elapsed_seconds ?? "暂无恢复时间"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!data.items.length && (
            <p className="empty">当前条件下没有故障记录</p>
          )}
          <div className="action-row application-pagination">
            <button
              className="outline"
              disabled={busy || data.page <= 1}
              onClick={() =>
                setQuery((current) => ({ ...current, page: current.page - 1 }))
              }
            >
              故障上一页
            </button>
            <span>
              故障第{data.page}页 · 共{data.total}条
            </span>
            <button
              className="outline"
              disabled={busy || data.page * data.page_size >= data.total}
              onClick={() =>
                setQuery((current) => ({ ...current, page: current.page + 1 }))
              }
            >
              故障下一页
            </button>
          </div>
        </>
      )}
    </section>
  );
}
