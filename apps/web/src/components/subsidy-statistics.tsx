"use client";
import { useBusinessView } from "./business-view-context";
import { useCallback, useEffect, useRef, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { useCapability } from "./permissions";
import { usePageRefresh } from "./page-refresh";
import { StationPicker } from "./station-picker";

type Dimension = ApiSchemas["StatisticPage"]["dimension"];
type Status = ApiSchemas["StatisticPage"]["status"];
type Filters = {
  start_date: string;
  end_date: string;
  dimension: Dimension;
  status: Status;
  station_id: string;
  operator_id: string;
  page: number;
};
const dimensions: Record<Dimension, string> = {
  operator: "运营商",
  station: "场站",
  equipment: "设备",
  device_day: "设备日明细",
};
const statuses: Record<Status, string> = {
  accepted: "已审定及已登记发放",
  approved: "已审定",
  disbursed: "已登记发放",
  submitted: "待审核",
  draft: "草稿（试算）",
};
function defaults(): Filters {
  const year = new Intl.DateTimeFormat("en", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
  }).format(new Date());
  return {
    start_date: `${year}-01-01`,
    end_date: `${year}-12-31`,
    dimension: "operator",
    status: "accepted",
    station_id: "",
    operator_id: "",
    page: 1,
  };
}
function params(query: Filters) {
  return {
    ...query,
    station_id: query.station_id ? Number(query.station_id) : undefined,
    operator_id: query.operator_id ? Number(query.operator_id) : undefined,
    page_size: 20,
  };
}
export function SubsidyStatistics() {
  const [routeView] = useBusinessView("statistics");
  const [draft, setDraft] = useState<Filters>(() => ({
      ...defaults(),
      dimension:
        routeView === "statistics.station"
          ? ("station" as const)
          : routeView === "statistics.detail"
            ? ("device_day" as const)
            : ("operator" as const),
    })),
    [applied, setApplied] = useState<Filters | null>(null);
  const [data, setData] = useState<ApiSchemas["StatisticPage"] | null>(null),
    [loading, setLoading] = useState(false),
    [error, setError] = useState("");
  const [operators, setOperators] = useState<ApiSchemas["OperatorItem"][]>([]);
  const [operatorError, setOperatorError] = useState("");
  const [exporting, setExporting] = useState(false),
    [exportError, setExportError] = useState("");
  const sequence = useRef(0),
    revision = usePageRefresh(),
    canExport = useCapability("subsidy.export");
  const chooseStation = useCallback(
    (station_id: string) => setDraft((current) => ({ ...current, station_id })),
    [],
  );
  useEffect(() => {
    const controller = new AbortController();
    typedApi
      .GET("/api/v1/subsidy/statistics/operators", {
        cache: "no-store",
        signal: controller.signal,
      })
      .then((result) => {
        if (controller.signal.aborted) return;
        if (!result.data)
          throw new Error(apiError(result.error, "运营商选项读取失败"));
        setOperators(result.data.items);
        setOperatorError("");
      })
      .catch((reason) => {
        if (!controller.signal.aborted)
          setOperatorError(
            reason instanceof Error ? reason.message : "读取失败",
          );
      });
    return () => controller.abort();
  }, [revision]);
  useEffect(() => {
    if (!applied) return;
    const controller = new AbortController(),
      current = ++sequence.current;
    setLoading(true);
    setError("");
    typedApi
      .GET("/api/v1/subsidy/statistics", {
        cache: "no-store",
        signal: controller.signal,
        params: { query: params(applied) },
      })
      .then((result) => {
        if (controller.signal.aborted || current !== sequence.current) return;
        if (!result.data)
          throw new Error(apiError(result.error, "补贴统计读取失败"));
        const last = Math.max(
          1,
          Math.ceil(result.data.total / result.data.page_size),
        );
        if (applied.page > last) {
          setApplied({ ...applied, page: last });
          return;
        }
        setData(result.data);
      })
      .catch((reason) => {
        if (!controller.signal.aborted && current === sequence.current)
          setError(reason instanceof Error ? reason.message : "读取失败");
      })
      .finally(() => {
        if (!controller.signal.aborted && current === sequence.current)
          setLoading(false);
      });
    return () => controller.abort();
  }, [applied, revision]);
  async function download() {
    if (!applied) return;
    setExporting(true);
    setExportError("");
    try {
      const { page: _page, page_size: _size, ...query } = params(applied);
      const result = await typedApi.GET("/api/v1/subsidy/statistics/export", {
        parseAs: "blob",
        cache: "no-store",
        params: { query },
      });
      if (!result.data)
        throw new Error(apiError(result.error, "导出失败，请缩小范围"));
      const url = URL.createObjectURL(result.data),
        link = document.createElement("a");
      link.href = url;
      link.download = "ronghub-subsidy-statistics.csv";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (reason) {
      setExportError(reason instanceof Error ? reason.message : "导出失败");
    } finally {
      setExporting(false);
    }
  }
  const device =
    applied?.dimension === "equipment" || applied?.dimension === "device_day";
  return (
    <section className="panel subsidy-statistics-panel" aria-label="补贴统计">
      <div className="panel-title">
        <div>
          <h3>补贴统计</h3>
          <p>运营商、场站及冻结设备明细 · 演示申请口径</p>
        </div>
      </div>
      <form
        className="subsidy-statistics-filters"
        onSubmit={(event) => {
          event.preventDefault();
          setApplied({ ...draft, page: 1 });
        }}
      >
        <label>
          运营商
          <select
            aria-label="补贴统计运营商"
            value={draft.operator_id}
            onChange={(e) =>
              setDraft({
                ...draft,
                operator_id: e.target.value,
                station_id: "",
              })
            }
          >
            <option value="">全部授权运营商</option>
            {operators.map((row) => (
              <option value={row.id} key={row.id}>
                {row.name}
              </option>
            ))}
          </select>
        </label>
        <StationPicker
          label="补贴统计场站"
          value={draft.station_id}
          onChange={chooseStation}
          allowAll
        />
        <label>
          完整周期开始
          <input
            aria-label="补贴统计开始日期"
            type="date"
            required
            value={draft.start_date}
            onChange={(e) => setDraft({ ...draft, start_date: e.target.value })}
          />
        </label>
        <label>
          完整周期结束
          <input
            aria-label="补贴统计结束日期"
            type="date"
            required
            value={draft.end_date}
            onChange={(e) => setDraft({ ...draft, end_date: e.target.value })}
          />
        </label>
        <label>
          统计维度
          <select
            aria-label="补贴统计维度"
            value={draft.dimension}
            onChange={(e) =>
              setDraft({ ...draft, dimension: e.target.value as Dimension })
            }
          >
            {Object.entries(dimensions).map(([key, label]) => (
              <option value={key} key={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          申请状态
          <select
            aria-label="补贴统计状态"
            value={draft.status}
            onChange={(e) =>
              setDraft({ ...draft, status: e.target.value as Status })
            }
          >
            {Object.entries(statuses).map(([key, label]) => (
              <option value={key} key={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <button
          className="primary"
          type="submit"
          disabled={loading || exporting}
        >
          查询补贴统计
        </button>
        {canExport && (
          <button
            className="outline"
            type="button"
            disabled={!applied || loading || exporting || !!error}
            onClick={download}
          >
            {exporting ? "正在导出…" : "导出补贴统计CSV"}
          </button>
        )}
      </form>
      <div className="subsidy-statistics-results">
        <p className="muted">
          仅统计申请周期完整落入日期范围的记录，不按天拆分金额。设备维度不分摊星级系数与人工差额；历史设备证据缺失时不补零。
        </p>
        {applied && (
          <p>
            已应用：{applied.start_date} 至 {applied.end_date} ·{" "}
            {dimensions[applied.dimension]} · {statuses[applied.status]} ·{" "}
            {applied.operator_id
              ? `运营商 #${applied.operator_id} · `
              : "全部授权运营商 · "}
            {applied.station_id
              ? `场站 #${applied.station_id}`
              : "全部授权场站"}
          </p>
        )}
        {operatorError && (
          <p className="error" role="alert">
            {operatorError}
          </p>
        )}
        {loading && <p role="status">正在汇总补贴统计…</p>}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {exportError && (
          <p className="error" role="alert">
            {exportError}
          </p>
        )}
        {!applied && (
          <p className="muted">
            选择范围后查询，默认仅统计已审定及已登记发放申请。
          </p>
        )}
        {data && !loading && !error && (
          <>
            <div className="detail-grid">
              <p>
                匹配申请 {data.totals.claim_count} 份
                <small>
                  设备口径 {data.totals.device_claim_count} · 旧站级{" "}
                  {data.totals.legacy_claim_count}
                </small>
              </p>
              <p>
                申请核算电量 {data.totals.eligible_kwh} kWh
                <small>系数前汇算 ¥ {data.totals.base_amount}</small>
              </p>
              <p>
                系数后初算 ¥ {data.totals.initial_amount}
                <small>人工差额 ¥ {data.totals.adjustment_amount}</small>
              </p>
              <p>
                调整后申请金额 ¥ {data.totals.adjusted_amount}
                <small>状态登记不代表实际付款</small>
              </p>
            </div>
            {device && (
              <p className="muted">
                下表仅含设备口径申请的冻结行；旧站级{" "}
                {data.totals.legacy_claim_count}{" "}
                份无设备拆分依据。上方总额为完整申请金额，不是设备行金额合计。
              </p>
            )}
            <div className="table-wrap">
              <table className="subsidy-statistics-table">
                <thead>
                  <tr>
                    {[
                      "运营商 / 信用代码",
                      "场站 / 来源编码",
                      ...(device ? ["设备 / 冻结名称", "日期"] : []),
                      "申请数 / 设备日行数",
                      "核算电量 kWh",
                      "系数前汇算 ¥",
                      ...(!device
                        ? ["系数后初算 ¥", "人工差额 ¥", "调整后金额 ¥"]
                        : []),
                    ].map((label) => (
                      <th key={label}>{label}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((row, index) => (
                    <tr
                      key={`${row.operator_id}-${row.station_id}-${row.equipment_id}-${row.day}-${index}`}
                    >
                      <td>
                        {row.operator_name}
                        <small>{row.credit_code || "信用代码未留存"}</small>
                      </td>
                      <td>
                        {row.station_name || "全部匹配场站"}
                        <small>
                          {row.station_code
                            ? `${row.source} / ${row.station_code}`
                            : "按运营商汇总"}
                        </small>
                      </td>
                      {device && (
                        <>
                          <td>
                            {row.equipment_external_id}
                            <small>
                              {row.equipment_name || "来源名称未留存"}
                            </small>
                          </td>
                          <td>{row.day || "所选完整周期"}</td>
                        </>
                      )}
                      <td>
                        {row.claim_count} / {device ? row.device_rows : "—"}
                      </td>
                      <td>{row.eligible_kwh}</td>
                      <td>{row.base_amount}</td>
                      {!device && (
                        <>
                          <td>{row.initial_amount}</td>
                          <td>{row.adjustment_amount}</td>
                          <td>{row.adjusted_amount}</td>
                        </>
                      )}
                    </tr>
                  ))}
                  {!data.items.length && (
                    <tr>
                      <td colSpan={device ? 7 : 8}>该范围没有可汇总记录</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <div className="subsidy-statistics-pagination">
              <button
                type="button"
                disabled={loading || exporting || data.page <= 1}
                onClick={() =>
                  setApplied(applied && { ...applied, page: applied.page - 1 })
                }
              >
                补贴统计上一页
              </button>
              <span>
                补贴统计第{data.page}页 · 共{data.total}组
              </span>
              <button
                type="button"
                disabled={
                  loading ||
                  exporting ||
                  data.page * data.page_size >= data.total
                }
                onClick={() =>
                  setApplied(applied && { ...applied, page: applied.page + 1 })
                }
              >
                补贴统计下一页
              </button>
            </div>
            <p className="muted">{data.notice}</p>
          </>
        )}
      </div>
    </section>
  );
}
