"use client";
import { useEffect, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { usePageRefresh } from "./page-refresh";
type Device = ApiSchemas["InventoryDevice"];
type DevicePage = ApiSchemas["InventoryDevicePage"];
type ConnectorPage = ApiSchemas["InventoryConnectorPage"];
const text = (value: string | null) => value ?? "来源未提供";
function TerminalList({
  stationId,
  deviceId,
  status,
  labels,
}: {
  stationId: number;
  deviceId: number;
  status: string;
  labels: Record<string, string>;
}) {
  const [draft, setDraft] = useState(""),
    [query, setQuery] = useState({
      q: "",
      connector_status: status,
      page: 1,
      page_size: 10,
    }),
    [result, setResult] = useState<ConnectorPage | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const revision = usePageRefresh();
  useEffect(() => {
    setQuery((old) =>
      old.connector_status === status
        ? old
        : { ...old, connector_status: status, page: 1 },
    );
  }, [status]);
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    setError("");
    typedApi
      .GET(
        "/api/v1/stations/{station_id}/equipment/{equipment_id}/connectors",
        {
          params: {
            path: { station_id: stationId, equipment_id: deviceId },
            query,
          },
          signal: controller.signal,
          cache: "no-store",
        },
      )
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "终端读取失败"));
        const last = Math.max(1, Math.ceil(data.total / query.page_size));
        if (query.page > last) {
          setQuery((old) => ({ ...old, page: last }));
          return;
        }
        setResult(data);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "终端读取失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [stationId, deviceId, query, revision]);
  return (
    <div
      className="device-terminals"
      aria-label={`设备终端 ${deviceId}`}
      aria-busy={busy}
    >
      <form
        className="device-query"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery((old) => ({ ...old, q: draft, page: 1 }));
        }}
      >
        <label>
          终端编码或名称
          <input
            aria-label={`检索终端 ${deviceId}`}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            maxLength={150}
          />
        </label>
        <button className="outline" disabled={busy}>
          查询终端
        </button>
      </form>
      {busy && (
        <p role="status" className="muted">
          正在读取终端…
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}；保留上次查询结果。
        </p>
      )}
      {result && (
        <>
          <p className="device-scroll-hint muted">
            左右滚动可查看完整终端信息。
          </p>
          <div
            className="device-terminal-table"
            role="region"
            tabIndex={0}
            aria-label={`终端明细表 ${deviceId}`}
          >
            <table>
              <thead>
                <tr>
                  <th>来源终端编码</th>
                  <th>终端名称</th>
                  <th>接口类型（来源）</th>
                  <th>快照状态</th>
                </tr>
              </thead>
              <tbody>
                {result.items.map((c) => (
                  <tr key={c.id}>
                    <td>{c.external_id}</td>
                    <td>{text(c.name)}</td>
                    <td>{text(c.interface_type)}</td>
                    <td>{labels[c.status] || c.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!result.items.length && (
            <p className="empty">未找到符合条件的终端。</p>
          )}
          <div className="device-pagination">
            <small>
              第{result.page}页 · 匹配{result.total}个终端 / 设备已保存
              {result.inventory_total}个
            </small>
            <div>
              <button
                className="outline"
                aria-label={`终端上一页 ${deviceId}`}
                disabled={busy || query.page <= 1}
                onClick={() =>
                  setQuery((old) => ({ ...old, page: old.page - 1 }))
                }
              >
                上一页
              </button>
              <button
                className="outline"
                aria-label={`终端下一页 ${deviceId}`}
                disabled={busy || query.page * query.page_size >= result.total}
                onClick={() =>
                  setQuery((old) => ({ ...old, page: old.page + 1 }))
                }
              >
                下一页
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
function DeviceCard({
  row,
  stationId,
  status,
  labels,
  types,
  initialOpen,
}: {
  row: Device;
  stationId: number;
  status: string;
  labels: Record<string, string>;
  types: Record<string, string>;
  initialOpen: boolean;
}) {
  const [open, setOpen] = useState(initialOpen);
  return (
    <article
      className="device inventory-device"
      aria-label={`设备 ${row.external_id}`}
    >
      <div className="device-card-title">
        <h4>
          {row.name} <small>{row.power_kw} kW</small>
        </h4>
        <span>
          {row.equipment_type == null
            ? "类型未提供"
            : types[row.equipment_type] || row.equipment_type}
        </span>
      </div>
      <p className="muted">来源设备编码：{row.external_id}</p>
      <div className="device-pagination">
        <small>
          终端 {row.connector_count} 个
          {status ? ` · 匹配状态 ${row.matching_connectors} 个` : ""}
        </small>
        <button
          className="outline"
          type="button"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
        >
          {open ? "收起资料与终端" : "展开资料与终端"}
        </button>
      </div>
      {open && (
        <>
          <dl className="station-facts">
            <div>
              <dt>来源设备编码</dt>
              <dd>{row.external_id}</dd>
            </div>
            <div>
              <dt>型号</dt>
              <dd>{text(row.model_name)}</dd>
            </div>
            <div>
              <dt>来源上报唯一编码</dt>
              <dd>{text(row.source_unique_code)}</dd>
            </div>
            <div>
              <dt>生产日期（来源）</dt>
              <dd>{text(row.manufactured_on)}</dd>
            </div>
          </dl>
          <TerminalList
            stationId={stationId}
            deviceId={row.id}
            status={status}
            labels={labels}
          />
        </>
      )}
    </article>
  );
}
export function DeviceInventory({ stationId }: { stationId: number }) {
  const initial = {
    q: "",
    equipment_type: "",
    connector_status: "",
    page: 1,
    page_size: 10,
  };
  const [draft, setDraft] = useState(initial),
    [query, setQuery] = useState(initial),
    [result, setResult] = useState<{
      data: DevicePage;
      query: typeof initial;
    } | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const revision = usePageRefresh();
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    setError("");
    typedApi
      .GET("/api/v1/stations/{station_id}/equipment/query", {
        params: { path: { station_id: stationId }, query },
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "设备台账读取失败"));
        const last = Math.max(1, Math.ceil(data.total / query.page_size));
        if (query.page > last) {
          setQuery((old) => ({ ...old, page: last }));
          return;
        }
        setResult({ data, query });
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "设备台账读取失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [stationId, query, revision]);
  return (
    <section aria-label="场站设备明细" aria-busy={busy}>
      <h3>设备与终端</h3>
      <p className="muted">
        来源台账与当前状态快照，独立于平台审批及运行历史。
      </p>
      <form
        className="device-query"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery({ ...draft, page: 1 });
        }}
      >
        <label>
          设备编码或名称
          <input
            aria-label="检索场站设备"
            value={draft.q}
            onChange={(e) => setDraft({ ...draft, q: e.target.value })}
            maxLength={150}
          />
        </label>
        <label>
          设备类型
          <select
            aria-label="筛选设备类型"
            value={draft.equipment_type}
            onChange={(e) =>
              setDraft({ ...draft, equipment_type: e.target.value })
            }
          >
            <option value="">全部类型</option>
            <option value="unknown">类型未提供</option>
            {Object.entries(result?.data.equipment_type_options || {}).map(
              ([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ),
            )}
          </select>
        </label>
        <label>
          终端状态
          <select
            aria-label="筛选设备终端状态"
            value={draft.connector_status}
            onChange={(e) =>
              setDraft({ ...draft, connector_status: e.target.value })
            }
          >
            <option value="">全部状态</option>
            {Object.entries(result?.data.connector_status_options || {}).map(
              ([value, label]) => (
                <option key={value} value={value}>
                  {label}（{value}）
                </option>
              ),
            )}
          </select>
        </label>
        <button className="outline" disabled={busy}>
          查询设备
        </button>
        <button
          type="button"
          className="outline"
          disabled={busy}
          onClick={() => {
            setDraft(initial);
            setQuery(initial);
          }}
        >
          重置设备查询
        </button>
      </form>
      {busy && (
        <p role="status" className="muted">
          正在读取设备…
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}；保留上次查询结果。
        </p>
      )}
      {result && (
        <>
          <p className="muted">
            当前场站已保存 {result.data.station_equipment_count} 个设备、
            {result.data.station_connector_count} 个终端；本次匹配{" "}
            {result.data.total} 个设备、{result.data.matching_connector_count}{" "}
            个终端。
          </p>
          {result.data.items.map((row, index) => (
            <DeviceCard
              key={row.id}
              row={row}
              stationId={stationId}
              status={result.query.connector_status}
              labels={result.data.connector_status_options}
              types={result.data.equipment_type_options}
              initialOpen={index === 0}
            />
          ))}
          {!result.data.items.length && (
            <p className="empty">
              {result.data.station_equipment_count === 0
                ? "尚无设备快照，不能据此推断设备离线。"
                : "未找到符合条件的设备，请调整查询条件。"}
            </p>
          )}
          <div className="device-pagination">
            <small>
              第{result.data.page}页 · 共{result.data.total}个设备
            </small>
            <div>
              <button
                className="outline"
                aria-label="场站设备上一页"
                disabled={busy || query.page <= 1}
                onClick={() =>
                  setQuery((old) => ({ ...old, page: old.page - 1 }))
                }
              >
                上一页
              </button>
              <button
                className="outline"
                aria-label="场站设备下一页"
                disabled={
                  busy || query.page * query.page_size >= result.data.total
                }
                onClick={() =>
                  setQuery((old) => ({ ...old, page: old.page + 1 }))
                }
              >
                下一页
              </button>
            </div>
          </div>
          <p className="muted">{result.data.notice}</p>
        </>
      )}
    </section>
  );
}
