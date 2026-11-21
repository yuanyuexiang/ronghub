"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { useCapability } from "./permissions";
import { usePageRefresh } from "./page-refresh";
import { businessStates, stationTypes } from "./station-map-types";

const initial = {
  q: "",
  district: "",
  operator_id: "",
  station_type: "",
  status: "",
  site_state: "",
  source_sync_state: "",
};
type Filters = typeof initial;
type Row = ApiSchemas["MapStation"];
const siteStates: Record<string, string> = {
  unknown: "未提供",
  constructing: "建设中",
  offline: "关闭下线",
  maintenance: "维护中",
  normal: "正常使用",
};
const syncStates: Record<string, string> = {
  unknown: "未提供",
  active: "正常同步",
  removed: "来源已下架",
};
function parameters(value: Filters) {
  return {
    ...value,
    operator_id: value.operator_id ? Number(value.operator_id) : undefined,
    location: "all",
  };
}
const clock = (value: string | null) =>
  value
    ? new Date(value).toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour12: false,
      })
    : "来源未提供";

export function StationDirectory({
  onOpen,
  actionMode,
  onApplication,
}: {
  onOpen: (id: number) => void;
  actionMode?: "access" | "operation" | "operator";
  onApplication?: (id: number, name: string, kind: string) => void;
}) {
  const [draft, setDraft] = useState(initial),
    [applied, setApplied] = useState(initial),
    [page, setPage] = useState(1);
  const [rows, setRows] = useState<Row[]>([]),
    [total, setTotal] = useState(0),
    [loading, setLoading] = useState(true),
    [error, setError] = useState("");
  const [operators, setOperators] = useState<ApiSchemas["OperatorItem"][]>([]);
  const [optionsError, setOptionsError] = useState("");
  const [exporting, setExporting] = useState(false),
    [exportError, setExportError] = useState("");
  const canExport = useCapability("stations.export");
  const revision = usePageRefresh();
  const request = useRef(0);
  const exportController = useRef<AbortController | null>(null);
  useEffect(() => () => exportController.current?.abort(), []);
  useEffect(() => {
    const controller = new AbortController();
    setOptionsError("");
    typedApi
      .GET("/api/v1/operators", {
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data, error }) => {
        if (!controller.signal.aborted) {
          if (data) setOperators(data.items);
          else setOptionsError(apiError(error, "运营商选项读取失败"));
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted) setOptionsError(e.message);
      });
    return () => controller.abort();
  }, [revision]);
  const load = useCallback(
    async (signal: AbortSignal) => {
      const sequence = ++request.current;
      setLoading(true);
      setRows([]);
      setError("");
      setExportError("");
      try {
        const { data, error } = await typedApi.GET("/api/v1/map/stations", {
          signal,
          cache: "no-store",
          params: { query: { ...parameters(applied), page, page_size: 20 } },
        });
        if (signal.aborted || sequence !== request.current) return;
        if (!data) throw new Error(apiError(error, "场站列表读取失败"));
        const last = Math.max(1, Math.ceil(data.total / 20));
        if (page > last) {
          setPage(last);
          return;
        }
        setRows(data.items);
        setTotal(data.total);
      } catch (e) {
        if (!signal.aborted && sequence === request.current) {
          setError(e instanceof Error ? e.message : "场站读取失败");
          setRows([]);
        }
      } finally {
        if (!signal.aborted && sequence === request.current) setLoading(false);
      }
    },
    [applied, page],
  );
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load, revision]);
  async function exportRows() {
    exportController.current?.abort();
    const controller = new AbortController();
    exportController.current = controller;
    setExporting(true);
    setExportError("");
    try {
      const { data, error } = await typedApi.GET("/api/v1/map/export", {
        signal: controller.signal,
        cache: "no-store",
        parseAs: "blob",
        params: { query: parameters(applied) },
      });
      if (controller.signal.aborted) return;
      if (!data) throw new Error(apiError(error, "场站导出失败"));
      const url = URL.createObjectURL(data),
        link = document.createElement("a");
      link.href = url;
      link.download = "ronghub-stations.csv";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      if (!controller.signal.aborted)
        setExportError(e instanceof Error ? e.message : "导出失败");
    } finally {
      if (!controller.signal.aborted) setExporting(false);
    }
  }
  return (
    <section className="panel station-directory" aria-label="场站信息查询">
      <div className="panel-title">
        <div>
          <h2>场站列表</h2>
          <p>查看授权范围内的场站；接入状态、站点运营和来源同步分别列示。</p>
        </div>
      </div>
      <form
        className="directory-filters"
        onSubmit={(e) => {
          e.preventDefault();
          setPage(1);
          setApplied({ ...draft });
        }}
      >
        <label>
          场站名称
          <input
            aria-label="搜索场站"
            placeholder="请输入场站名称"
            maxLength={150}
            value={draft.q}
            onChange={(e) => setDraft({ ...draft, q: e.target.value })}
          />
        </label>
        <label>
          所属区域
          <input
            aria-label="场站区域筛选"
            placeholder="全部授权区域"
            maxLength={50}
            value={draft.district}
            onChange={(e) => setDraft({ ...draft, district: e.target.value })}
          />
        </label>
        <label>
          所属运营商
          <select
            aria-label="场站运营商筛选"
            value={draft.operator_id}
            onChange={(e) =>
              setDraft({ ...draft, operator_id: e.target.value })
            }
          >
            <option value="">全部授权运营商</option>
            {operators.map((operator) => (
              <option key={operator.id} value={operator.id}>
                {operator.name}
              </option>
            ))}
          </select>
        </label>
        {(
          [
            ["station_type", "场站种类", stationTypes],
            ["status", "接入与流转状态", businessStates],
            ["site_state", "站点运营状态", siteStates],
            ["source_sync_state", "场站同步状态", syncStates],
          ] as const
        ).map(([key, label, options]) => (
          <label key={key}>
            {label}
            <select
              aria-label={key === "status" ? "场站状态筛选" : `${label}筛选`}
              value={draft[key]}
              onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
            >
              <option value="">全部</option>
              {Object.entries(options).map(([value, text]) => (
                <option key={value} value={value}>
                  {text}
                </option>
              ))}
            </select>
          </label>
        ))}
        <div className="directory-filter-actions">
          <button className="primary" disabled={loading}>
            查询
          </button>
          <button
            type="button"
            className="outline"
            disabled={loading}
            onClick={() => {
              setDraft(initial);
              setApplied({ ...initial });
              setPage(1);
            }}
          >
            重置场站筛选
          </button>
        </div>
      </form>
      <div className="directory-toolbar">
        <span>
          共 <b>{total}</b> 个场站 · 按已查询条件展示
        </span>
        {canExport && (
          <button
            className="outline"
            disabled={loading || exporting}
            onClick={exportRows}
          >
            {exporting ? "正在导出…" : "导出场站CSV"}
          </button>
        )}
      </div>
      {optionsError && (
        <p role="alert" className="error application-list-note">
          {optionsError}
        </p>
      )}
      {error && (
        <p role="alert" className="error application-list-note">
          {error}
        </p>
      )}
      {exportError && (
        <p role="alert" className="error application-list-note">
          {exportError}
        </p>
      )}
      <div className="table-wrap" aria-busy={loading}>
        <table>
          <thead>
            <tr>
              {[
                "场站名称 / 地址",
                "运营商",
                "所属区域",
                "场站种类",
                "当前星级",
                "接入与流转状态",
                "站点运营状态",
                "场站同步状态",
                "首次接入时间（来源）",
                "操作",
              ].map((label) => (
                <th key={label}>{label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>
                  <b>{row.name}</b>
                  <small className="station-address">{row.address}</small>
                </td>
                <td>{row.operator_name}</td>
                <td>{row.district || "未提供"}</td>
                <td>{stationTypes[row.station_type] || "未提供"}</td>
                <td>
                  {row.effective_stars == null
                    ? "未评定"
                    : `${row.effective_stars}星`}
                </td>
                <td>
                  <em
                    className={row.status === "operating" ? "green" : "amber"}
                  >
                    {businessStates[row.status] || row.status}
                  </em>
                </td>
                <td>{siteStates[row.site_state] || "未提供"}</td>
                <td>{syncStates[row.source_sync_state] || "未提供"}</td>
                <td>{clock(row.source_first_connected_at)}</td>
                <td>
                  {onApplication &&
                    (actionMode === "operation"
                      ? [
                          ["commission", "投运事项"],
                          ["decommission", "核销事项"],
                        ]
                      : [
                          ["access", "接入审核"],
                          ["acceptance", "竣工验收"],
                        ]
                    ).map(([kind, label]) => (
                      <button
                        key={kind}
                        className="outline"
                        onClick={() => onApplication(row.id, row.name, kind)}
                      >
                        {label}
                      </button>
                    ))}
                  <button
                    className="outline"
                    aria-label={`查看详情 ${row.name}`}
                    onClick={() => onOpen(row.id)}
                  >
                    查看详情
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {loading && (
        <p className="empty" role="status">
          正在加载场站…
        </p>
      )}
      {!loading && !error && !rows.length && (
        <p className="empty">当前条件下没有场站数据</p>
      )}
      <div className="business-form directory-pagination">
        <button
          className="outline"
          disabled={loading || page <= 1}
          onClick={() => setPage(page - 1)}
        >
          场站上一页
        </button>
        <span role="status">
          {loading ? "场站加载中…" : `第${page}页 · 共${total}个场站`}
        </span>
        <button
          className="outline"
          disabled={loading || page * 20 >= total}
          onClick={() => setPage(page + 1)}
        >
          场站下一页
        </button>
      </div>
    </section>
  );
}
