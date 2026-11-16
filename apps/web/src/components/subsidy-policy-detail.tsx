"use client";
import type { ApiSchemas } from "../lib/typed-api";

export function SubsidyPolicyDetail({
  policy,
  onClose,
}: {
  policy: ApiSchemas["SubsidyPolicyResponse"];
  onClose: () => void;
}) {
  const rules = policy.device_rules;
  return (
    <section
      className="business-detail subsidy-policy-detail"
      aria-label="补贴规则详情"
    >
      <div className="panel-title">
        <div>
          <h3>{policy.name}</h3>
          <p>
            {policy.code} / V{policy.version} · {policy.effective_from} 至{" "}
            {policy.effective_to}
          </p>
        </div>
        <button className="outline" onClick={onClose}>
          关闭规则详情
        </button>
      </div>
      <div className="detail-grid">
        <p>充电补贴标准：¥ {policy.rate} / 度</p>
        {rules && (
          <>
            <p>换电补贴标准：¥ {rules.swap_rate} / 度</p>
            <p>
              生成周期：
              {{ day: "日", month: "月", year: "年" }[rules.generation_period]}
              （定时生成待接通）
            </p>
            <p>故障率门槛：严格小于 {rules.fault_rate_below}%</p>
            <p>离线率门槛：严格小于 {rules.offline_rate_below}%</p>
          </>
        )}
      </div>
      {rules ? (
        <>
          <h4>星级系数</h4>
          <dl className="subsidy-star-coefficients">
            {Object.entries(rules.star_coefficients).map(([star, value]) => (
              <div key={star}>
                <dt>{star === "0" ? "无星" : star + "星"}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
          <p className="muted">
            {policy.calculation_basis === "demo_device_day_v2"
              ? "设备日演示核算：按来源明确的逐日设备集合和完整观测；取周期末留存有效公示。逐设备日按分舍入后汇总，再乘星级系数按分舍入。缺失依据阻止申报，非正式结算。"
              : "本版本未声明完整执行口径，暂不可申报；未使用旧站级算法。需另建可执行版本，未知统计不记零或默认达标。"}
          </p>
        </>
      ) : (
        <p className="muted">
          旧站级电量演示。没有设备门槛与星级系数的配置记录，未补写为已确认规则。
        </p>
      )}
    </section>
  );
}
