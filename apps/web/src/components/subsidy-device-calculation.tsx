"use client";
import { useEffect, useRef, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { usePageRefresh } from "./page-refresh";
import { useCapability } from "./permissions";
import type { paths } from "@ronghub/api-schema";

type Filters = NonNullable<
  paths["/api/v1/subsidy/claims/{claim_id}/device-lines"]["get"]["parameters"]["query"]
>;
const initial = {
  equipment_type: "",
  equipment_code: "",
  eligibility: "",
  page: 1,
} satisfies Filters;
const equipmentTypes = {
  dc: "直流设备",
  ac: "交流设备",
  swapping: "换电设备",
  unknown: "未识别",
} as const;

const reasons: Record<string, string> = {
  fault_threshold: "故障门槛未达标",
  offline_threshold: "离线门槛未达标",
  both_thresholds: "故障/离线门槛均未达标",
};
export function SubsidyDeviceCalculation({
  claimId,
  calculation,
  reviewed,
}: {
  claimId: number;
  reviewed: boolean;
  calculation: ApiSchemas["DeviceClaimCalculation"];
}) {
  const [draft, setDraft] = useState<Filters>(initial);
  const [query, setQuery] = useState<Filters>(initial);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");
  const canExport = useCapability("subsidy.export");
  const page = query.page || 1;
  const [data, setData] = useState<ApiSchemas["DeviceLinePage"] | null>(null),
    [loading, setLoading] = useState(false),
    [error, setError] = useState("");
  const revision = usePageRefresh(),
    sequence = useRef(0);
  useEffect(() => {
    const controller = new AbortController(),
      current = ++sequence.current;
    setLoading(true);
    setError("");
    typedApi
      .GET("/api/v1/subsidy/claims/{claim_id}/device-lines", {
        cache: "no-store",
        signal: controller.signal,
        params: {
          path: { claim_id: claimId },
          query: { ...query, page_size: 20 },
        },
      })
      .then((result) => {
        if (controller.signal.aborted || current !== sequence.current) return;
        if (!result.data)
          throw new Error(apiError(result.error, "设备核算明细读取失败"));
        const last = Math.max(
          1,
          Math.ceil(result.data.total / result.data.page_size),
        );
        if (page > last) setQuery((current) => ({ ...current, page: last }));
        else setData(result.data);
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
  }, [claimId, page, query, revision]);

  async function download() {
    setExporting(true);
    setExportError("");
    try {
      const { equipment_type, equipment_code, eligibility } = query;
      const { data, error } = await typedApi.GET(
        "/api/v1/subsidy/claims/{claim_id}/device-lines/export",
        {
          params: {
            path: { claim_id: claimId },
            query: { equipment_type, equipment_code, eligibility },
          },
          parseAs: "blob",
          cache: "no-store",
        },
      );
      if (!data) throw new Error(apiError(error, "设备审核明细导出失败"));
      const url = URL.createObjectURL(data);
      const link = document.createElement("a");
      link.href = url;
      link.download = `SUB-${claimId}-设备审核明细.csv`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (reason) {
      setExportError(
        reason instanceof Error ? reason.message : "设备审核明细导出失败",
      );
    } finally {
      setExporting(false);
    }
  }
  const rating = calculation.rating;
  return (
    <section className="device-calculation" aria-label="冻结设备核算依据">
      <h3>设备审核结果</h3>
      <form
        className="ledger-filters"
        aria-label="设备审核结果筛选"
        onSubmit={(event) => {
          event.preventDefault();
          setQuery({ ...draft, page: 1 });
        }}
      >
        <label>
          设备类型
          <select
            value={draft.equipment_type || ""}
            onChange={(event) =>
              setDraft({
                ...draft,
                equipment_type: event.target.value as Filters["equipment_type"],
              })
            }
          >
            <option value="">全部设备类型</option>
            {Object.entries(equipmentTypes).map(([key, name]) => (
              <option key={key} value={key}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label>
          设备编码
          <input
            maxLength={100}
            value={draft.equipment_code || ""}
            onChange={(event) =>
              setDraft({ ...draft, equipment_code: event.target.value })
            }
          />
        </label>
        <label>
          核算结论
          <select
            value={draft.eligibility || ""}
            onChange={(event) =>
              setDraft({
                ...draft,
                eligibility: event.target.value as Filters["eligibility"],
              })
            }
          >
            <option value="">全部核算结论</option>
            <option value="eligible">达标</option>
            <option value="excluded">未达标</option>
          </select>
        </label>
        <div className="action-row">
          <button type="submit" className="primary" disabled={loading}>
            查询设备结果
          </button>
          <button
            type="button"
            className="outline"
            onClick={() => {
              setDraft(initial);
              setQuery({ ...initial });
            }}
          >
            重置设备筛选
          </button>
          {reviewed && canExport && (
            <button
              type="button"
              className="outline"
              disabled={loading || exporting || !data || Boolean(error)}
              onClick={() => void download()}
            >
              导出设备审核明细
            </button>
          )}
        </div>
      </form>
      {exportError && (
        <p className="error" role="alert">
          {exportError}
        </p>
      )}
      <div className="detail-grid">
        <p>
          有效公示：RATE-{rating.application_id} ·{" "}
          {rating.stars === 0 ? "无星" : rating.stars + "星"}
          <small>取值日：{rating.as_of}（北京时间周期末）</small>
          <small>
            公示版本：{rating.published_version} · 有效至 {rating.valid_until}
          </small>
        </p>
        <p>
          汇算补贴 ¥ {calculation.pre_coefficient_amount} × 系数{" "}
          {calculation.star_coefficient}
          <small>系数后初算：¥ {calculation.calculated_amount}</small>
          <small>后续人工调整单独留痕，不改原始计算。</small>
        </p>
      </div>
      <p className="muted">
        设备日核算按申请时冻结的来源记录显示，核算结论与整笔申请的审核状态分别记录。冻结{" "}
        {calculation.row_count} 条设备日记录，{calculation.eligible_rows}{" "}
        条达标、{calculation.excluded_rows} 条不达标；来源设备集合{" "}
        {calculation.scopes.length}{" "}
        天。门槛比较原始秒数，不使用显示百分比。来源后续修订不会改写本快照。
      </p>
      {loading && <p role="status">正在读取冻结设备明细…</p>}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {data && !loading && !error && (
        <>
          <div className="table-wrap">
            <table className="aligned-records">
              <thead>
                <tr>
                  {[
                    "日期",
                    "设备名称 / 编码",
                    "设备类型 / 功率",
                    "生产日期",
                    "计量电量（度）",
                    "故障率",
                    "离线率",
                    "汇算补贴（元）",
                    "核算结论",
                  ].map((label) => (
                    <th key={label}>{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.items.map((line) => {
                  const row = line.snapshot;
                  return (
                    <tr key={line.fact_id}>
                      <td>{row.source.day}</td>
                      <td>
                        {row.source.equipment_name ?? "名称未上报"}
                        <small>{row.source.equipment_external_id}</small>
                      </td>
                      <td>
                        {equipmentTypes[row.source.equipment_type ?? "unknown"]}
                        <small>{row.source.power_kw ?? "未上报"} kW</small>
                      </td>
                      <td>{row.source.production_date ?? "未上报"}</td>
                      <td className="numeric-cell">
                        {row.source.energy_kwh ?? "未上报"}
                      </td>
                      <td className="numeric-cell">
                        {line.fault_pct == null
                          ? "未留存"
                          : `${line.fault_pct}%`}
                      </td>
                      <td className="numeric-cell">
                        {line.offline_pct == null
                          ? "未留存"
                          : `${line.offline_pct}%`}
                      </td>
                      <td className="numeric-cell">
                        ¥ {row.amount}
                        <small>{row.rate} 元/度</small>
                      </td>
                      <td>
                        {row.eligible
                          ? "达标"
                          : (reasons[row.exclusion_reason ?? ""] ?? "未达标")}
                        <small>来源版本 {row.fact_version}</small>
                        <small>范围版本 {row.scope_version}</small>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {!data.total && <p className="empty">当前条件没有设备核算结果</p>}
          <div className="pagination">
            <button
              className="outline"
              disabled={page <= 1}
              onClick={() => setQuery({ ...query, page: page - 1 })}
            >
              设备核算上一页
            </button>
            <span>
              设备核算第{data.page}页 · 共{data.total}行
            </span>
            <button
              className="outline"
              disabled={page * data.page_size >= data.total}
              onClick={() => setQuery({ ...query, page: page + 1 })}
            >
              设备核算下一页
            </button>
          </div>
        </>
      )}
    </section>
  );
}
