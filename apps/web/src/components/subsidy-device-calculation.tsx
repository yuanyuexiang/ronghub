"use client";
import { useEffect, useRef, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { usePageRefresh } from "./page-refresh";

const reasons: Record<string, string> = {
  fault_threshold: "故障门槛未达标",
  offline_threshold: "离线门槛未达标",
  both_thresholds: "故障/离线门槛均未达标",
};
export function SubsidyDeviceCalculation({
  claimId,
  calculation,
}: {
  claimId: number;
  calculation: ApiSchemas["DeviceClaimCalculation"];
}) {
  const [page, setPage] = useState(1),
    [data, setData] = useState<ApiSchemas["DeviceLinePage"] | null>(null),
    [loading, setLoading] = useState(false),
    [error, setError] = useState("");
  const revision = usePageRefresh(),
    sequence = useRef(0);
  useEffect(() => {
    setPage(1);
    setData(null);
  }, [claimId]);
  useEffect(() => {
    const controller = new AbortController(),
      current = ++sequence.current;
    setLoading(true);
    setError("");
    typedApi
      .GET("/api/v1/subsidy/claims/{claim_id}/device-lines", {
        cache: "no-store",
        signal: controller.signal,
        params: { path: { claim_id: claimId }, query: { page, page_size: 20 } },
      })
      .then((result) => {
        if (controller.signal.aborted || current !== sequence.current) return;
        if (!result.data)
          throw new Error(apiError(result.error, "设备核算明细读取失败"));
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
  }, [claimId, page, revision]);
  const rating = calculation.rating;
  return (
    <section className="device-calculation" aria-label="冻结设备核算依据">
      <h3>冻结设备核算依据 · 演示</h3>
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
        冻结 {calculation.row_count} 条设备日记录，{calculation.eligible_rows}{" "}
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
            <table>
              <thead>
                <tr>
                  {[
                    "日期 / 设备",
                    "来源元数据",
                    "日计量kWh / 口径",
                    "故障 / 离线秒数",
                    "标准 / 汇算金额",
                    "门槛结论 / 修订",
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
                      <td>
                        {row.source.day}
                        <small>
                          {row.source.equipment_name ?? "来源名称未上报"}
                        </small>
                        <small>{row.source.equipment_external_id}</small>
                      </td>
                      <td>
                        {row.source.equipment_type}
                        <small>{row.source.power_kw ?? "未知"} kW</small>
                        <small>
                          生产日期：{row.source.production_date ?? "未上报"}
                        </small>
                      </td>
                      <td>
                        {row.source.energy_kwh}
                        <small>
                          {row.source.business_kind === "charging"
                            ? "充电"
                            : "换电"}
                        </small>
                      </td>
                      <td>
                        {row.source.fault_seconds} /{" "}
                        {row.source.offline_seconds}
                        <small>各轴完整86400秒且无未知</small>
                      </td>
                      <td>
                        ¥ {row.rate} / 度<small>¥ {row.amount}</small>
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
          <div className="pagination">
            <button
              className="outline"
              disabled={page <= 1}
              onClick={() => setPage(page - 1)}
            >
              设备核算上一页
            </button>
            <span>
              设备核算第{data.page}页 · 共{data.total}行
            </span>
            <button
              className="outline"
              disabled={page * data.page_size >= data.total}
              onClick={() => setPage(page + 1)}
            >
              设备核算下一页
            </button>
          </div>
        </>
      )}
    </section>
  );
}
