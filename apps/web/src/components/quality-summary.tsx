"use client";
import type { ApiSchemas } from "../lib/typed-api";
import { formatObservedNumber as number } from "./runtime-axes";

export function QualitySummary({
  summary,
}: {
  summary: ApiSchemas["QualityGroup"];
}) {
  return (
    <section className="quality-summary" aria-label="质量查询统计汇总">
      <div className="quality-summary-title">
        <div>
          <h3>统计汇总</h3>
          <p>全部已查询结果，包含其他分页；演示与源报分别统计。</p>
        </div>
        <span className="quality-summary-badge">当前台账 · 历史观测</span>
      </div>
      <div className="quality-inventory">
        <article>
          <span>当前台账场站</span>
          <strong>
            {summary.station_count}
            <small>站</small>
          </strong>
        </article>
        <article>
          <span>当前台账设备</span>
          <strong>
            {summary.inventory_equipment_count}
            <small>台</small>
          </strong>
        </article>
        <article>
          <span>正式在线率 / 故障率</span>
          <strong className="quality-pending-rate">待覆盖确认</strong>
        </article>
      </div>
      <p className="muted">
        台账数量不是查询期间的历史设备规模；没有观测不能推断正常。正式比率尚无完整来源及分母。
      </p>
      {summary.streams.map((stream) => {
        const online = stream.axes
          .find((axis) => axis.dimension === "connectivity")
          ?.states.find(
            (state) => state.state === "online",
          )?.share_of_reported_pct;
        return (
          <article
            className="quality-summary-stream"
            key={String(stream.is_demo)}
            aria-label={`${stream.is_demo ? "演示" : "源报"}查询汇总`}
          >
            <h4>{stream.is_demo ? "演示数据" : "源报数据"}</h4>
            <div className="quality-summary-metrics">
              <div>
                <span>观测连接在线占比</span>
                <strong>
                  {online == null ? "暂无样本" : `${number(online)}%`}
                </strong>
                <small>在线秒数 / 本轴已上送秒数（含未知）</small>
              </div>
              <div>
                <span>源报故障事件</span>
                <strong>
                  {stream.faults.observed_events}
                  <small>件</small>
                </strong>
                <small>
                  未恢复 {stream.faults.active_events} · 已恢复{" "}
                  {stream.faults.recovered_events} · 未知{" "}
                  {stream.faults.unknown_events}
                </small>
              </div>
              <div>
                <span>已恢复样本平均经过时长</span>
                <strong>
                  {stream.faults.average_recovery_elapsed_seconds == null
                    ? "暂无样本"
                    : `${number(stream.faults.average_recovery_elapsed_seconds)}秒`}
                </strong>
                <small>全部已恢复事件重新核算，非分组均值的平均</small>
              </div>
              <div>
                <span>已观测源对象</span>
                <strong>
                  {stream.observed_object_count}
                  <small>个</small>
                </strong>
                <small>
                  有运行区间 {stream.stations_with_runtime} 站，非全部设备规模
                </small>
              </div>
            </div>
          </article>
        );
      })}
      {!summary.streams.length && (
        <p className="quality-no-data">
          当前查询没有运行或故障观测，未生成比率或恢复均值。
        </p>
      )}
    </section>
  );
}
