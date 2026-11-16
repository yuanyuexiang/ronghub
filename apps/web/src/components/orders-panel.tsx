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
  charging: "充电中",
  completed: "已完成",
  interrupted: "中断",
  cancelled: "已取消",
};
function params(filters: Filters) {
  return {
    ...filters,
    start_at: filters.start_at
      ? new Date(filters.start_at).toISOString()
      : undefined,
    end_at: filters.end_at ? new Date(filters.end_at).toISOString() : undefined,
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
export function OrdersPanel() {
  const canExport = useCapability("orders.export");
  const [draft, setDraft] = useState(empty);
  const [query, setQuery] = useState({ filters: empty, page: 1, refresh: 0 });
  const [data, setData] = useState<ApiSchemas["ChargingOrderPage"] | null>(
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
    setError("");
    typedApi
      .GET("/api/v1/orders", {
        params: {
          query: { ...params(query.filters), page: query.page, page_size: 20 },
        },
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data: body, error }) => {
        if (controller.signal.aborted) return;
        if (!body) throw new Error(apiError(error, "订单读取失败"));
        const last = Math.max(1, Math.ceil(body.total / body.page_size));
        if (query.page > last)
          setQuery((current) => ({ ...current, page: last }));
        else setData(body);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "订单读取失败");
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
      new Date(draft.start_at) >= new Date(draft.end_at)
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
      const { data: blob, error } = await typedApi.GET(
        "/api/v1/orders/export",
        { params: { query: params(query.filters) }, parseAs: "blob" },
      );
      if (!blob)
        throw new Error(apiError(error, "订单导出失败，请缩小查询范围后重试"));
      const url = URL.createObjectURL(blob),
        link = document.createElement("a");
      link.href = url;
      link.download = "ronghub-orders.csv";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "订单导出失败");
    } finally {
      setExporting(false);
    }
  }
  return (
    <section className="panel orders-panel">
      <div className="panel-title">
        <div>
          <h2>源报充电订单</h2>
          <p>订单与场站日电量分别保留，当前展示来源提供的观测记录</p>
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
            刷新订单
          </button>
          {canExport && (
            <button
              className="outline"
              disabled={busy || exporting}
              onClick={download}
            >
              {exporting ? "正在导出…" : "导出当前订单查询"}
            </button>
          )}
        </div>
      </div>
      <form className="application-filters" onSubmit={apply}>
        <label>
          订单号、场站或企业
          <input
            aria-label="订单检索名称"
            value={draft.q}
            maxLength={150}
            onChange={(e) => setDraft({ ...draft, q: e.target.value })}
          />
        </label>
        <label>
          源桩编码
          <input
            aria-label="订单源桩编码"
            value={draft.equipment_external_id}
            maxLength={100}
            onChange={(e) =>
              setDraft({ ...draft, equipment_external_id: e.target.value })
            }
          />
        </label>
        <label>
          订单状态
          <select
            aria-label="订单筛选状态"
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
            aria-label="订单数据来源"
            value={draft.source}
            maxLength={50}
            onChange={(e) => setDraft({ ...draft, source: e.target.value })}
          />
        </label>
        <label>
          开始时间起（本地）
          <input
            aria-label="订单开始时间起"
            type="datetime-local"
            value={draft.start_at}
            onChange={(e) => setDraft({ ...draft, start_at: e.target.value })}
          />
        </label>
        <label>
          开始时间止（不含）
          <input
            aria-label="订单开始时间止"
            type="datetime-local"
            value={draft.end_at}
            onChange={(e) => setDraft({ ...draft, end_at: e.target.value })}
          />
        </label>
        <button className="primary" disabled={busy}>
          查询订单
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
          重置订单筛选
        </button>
      </form>
      <p className="muted application-list-note">
        {data?.notice ??
          "按订单开始时间查询，开始包含、结束不含。源报时长、金额缺失时显示暂无数据，未核实来源完整性。"}
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {busy && (
        <p className="empty" role="status">
          正在读取订单…
        </p>
      )}
      {data && (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {[
                    "订单 / 来源",
                    "场站 / 企业",
                    "源桩 / 枪编码",
                    "状态",
                    "开始 / 结束（北京）",
                    "源报时长（秒）",
                    "源报电量（kWh）",
                    "源报消费额（元）",
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
                        ORDER-{order.id} · {order.source}
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
                      <span>{stamp(order.ended_at)}</span>
                    </td>
                    <td>{order.duration_seconds ?? "暂无数据"}</td>
                    <td>{order.kwh ?? "暂无数据"}</td>
                    <td>{order.amount ?? "暂无数据"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!data.items.length && (
            <p className="empty">当前条件下没有订单记录</p>
          )}
          <div className="action-row application-pagination">
            <button
              className="outline"
              disabled={busy || data.page <= 1}
              onClick={() =>
                setQuery((current) => ({ ...current, page: current.page - 1 }))
              }
            >
              订单上一页
            </button>
            <span>
              订单第{data.page}页 · 共{data.total}条
            </span>
            <button
              className="outline"
              disabled={busy || data.page * data.page_size >= data.total}
              onClick={() =>
                setQuery((current) => ({ ...current, page: current.page + 1 }))
              }
            >
              订单下一页
            </button>
          </div>
        </>
      )}
    </section>
  );
}
