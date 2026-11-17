import type { ApiSchemas } from "../lib/typed-api";
import { alertLevelLabel } from "../lib/alert-labels";
export function ConfiguredEventEvidence({
  evidence,
  active,
}: {
  evidence: ApiSchemas["EventEvidence"];
  active: boolean;
}) {
  const periodic = evidence.first.configuration.period !== "instant";
  return (
    <section
      className="configured-event-evidence"
      aria-label="冻结分级告警依据"
    >
      <h3>冻结分级告警依据 · 演示</h3>
      <p>
        当前等级：
        {evidence.current_level
          ? alertLevelLabel(
              evidence.current_level,
              evidence.first.configuration.period,
            )
          : active
            ? "滞回区间，异常保持"
            : "已恢复"}{" "}
        · 最高等级：
        {alertLevelLabel(
          evidence.highest_level,
          evidence.first.configuration.period,
        )}
      </p>
      <p>
        周期：
        {periodic
          ? `${evidence.first.period_start} 至 ${evidence.first.period_end}（结束不含，北京时间）`
          : "即时设备观测"}
      </p>
      <div className="detail-grid">
        {(
          [
            ["首次触发", evidence.first, evidence.first_observation_id],
            ["最近完整判定", evidence.latest, evidence.latest_observation_id],
          ] as const
        ).map(([label, row, id]) => (
          <article key={label}>
            <h4>
              {label} · OBS-{id}
            </h4>
            <p>
              {new Date(row.observed_at).toLocaleString("zh-CN", {
                timeZone: "Asia/Shanghai",
              })}
            </p>
            <p>
              {row.equipment
                ? `${row.equipment.name} / ${row.equipment.external_id}`
                : "场站周期指标"}
            </p>
            <p>
              来源：{row.station_source} / {row.station_external_id}
            </p>
            {row.source_period && (
              <p>
                自动周期来源：FACT-{row.source_period.fact_id} V
                {row.source_period.fact_version} · 影响 / 应服务秒数：
                {row.source_period.numerator_seconds ?? "未知"} /{" "}
                {row.source_period.denominator_seconds ?? "未知"}
              </p>
            )}
            <p>
              实测值：{row.value ?? "未知"}
              {row.configuration.kind === "device_current" ? " A" : ""}
            </p>
            {row.rated_current_a && (
              <p>来源额定电流：{row.rated_current_a} A</p>
            )}
            <p>比较值：{row.comparison_value ?? "未知"}</p>
            <p>
              规则：{row.rule_code} V{row.rule_version}
            </p>
          </article>
        ))}
      </div>
      <p className="muted">
        按未舍入原值判级，比较值仅展示六位。覆盖未知与迟到观测单独留档，不覆盖本事件最近完整依据；人工确认不代表设备恢复。来源类别与版本以冻结依据为准，包含人工演示与独立适配器演示，均非客户真实监测。
      </p>
    </section>
  );
}
