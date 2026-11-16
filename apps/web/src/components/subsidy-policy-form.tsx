"use client";

import { useState } from "react";
import type { ApiSchemas } from "../lib/typed-api";

type Input = ApiSchemas["PolicyInput"];
type Period = "day" | "month" | "year";
export function SubsidyPolicyForm({
  busy,
  onSave,
}: {
  busy: boolean;
  onSave: (body: Input) => Promise<void>;
}) {
  const [basis, setBasis] = useState("device"),
    [period, setPeriod] = useState<Period>("day");
  const [executionConfirmed, setExecutionConfirmed] = useState(false);
  const [data, setData] = useState({
    code: "",
    name: "",
    version: "1",
    rate: "",
    effective_from: "",
    effective_to: "",
    swap_rate: "",
    fault_rate_below: "",
    offline_rate_below: "",
  });
  const [coefficients, setCoefficients] = useState<Record<string, string>>({
    "0": "",
    "1": "",
    "2": "",
    "3": "",
    "4": "",
    "5": "",
  });
  return (
    <form
      className="business-form"
      onSubmit={async (event) => {
        event.preventDefault();
        await onSave({
          code: data.code,
          name: data.name,
          version: Number(data.version),
          rate: data.rate,
          effective_from: data.effective_from,
          effective_to: data.effective_to,
          device_rules:
            basis === "legacy"
              ? null
              : basis === "execute"
                ? {
                    configuration_version: 2,
                    generation_period: period,
                    swap_rate: data.swap_rate,
                    fault_rate_below: data.fault_rate_below,
                    offline_rate_below: data.offline_rate_below,
                    star_coefficients: coefficients,
                    metric_basis: "calendar_day_seconds_v1",
                    device_scope_basis: "source_declared_station_day_v1",
                    rating_reference: "period_end_publication_snapshot_v1",
                    rounding: "HALF_UP_PER_DEVICE_DAY_THEN_COEFFICIENT_2DP",
                  }
                : {
                    configuration_version: 1,
                    generation_period: period,
                    swap_rate: data.swap_rate,
                    fault_rate_below: data.fault_rate_below,
                    offline_rate_below: data.offline_rate_below,
                    star_coefficients: coefficients,
                  },
        });
      }}
    >
      <label>
        规则口径
        <select
          aria-label="补贴规则口径"
          value={basis}
          disabled={busy}
          onChange={(event) => setBasis(event.target.value)}
        >
          <option value="device">设备级规则 · 配置版本</option>
          <option value="execute">设备日演示核算 · 明确执行口径</option>
          <option value="legacy">旧站级演示 · 单电价核算</option>
        </select>
      </label>
      {(
        [
          ["code", "政策代码"],
          ["name", "政策名称"],
          ["version", "版本号"],
          ["rate", "充电补贴标准（元/度）"],
          ["effective_from", "生效日期"],
          ["effective_to", "结束日期"],
        ] as const
      ).map(([key, label]) => (
        <label key={key}>
          {label}
          <input
            aria-label={label}
            required
            disabled={busy}
            type={
              key.includes("effective")
                ? "date"
                : key === "version" || key === "rate"
                  ? "number"
                  : "text"
            }
            min={
              key === "version" ? 1 : key === "rate" ? "0.000001" : undefined
            }
            max={key === "version" ? 2147483647 : undefined}
            step={
              key === "version" ? 1 : key === "rate" ? "0.000001" : undefined
            }
            maxLength={key === "code" ? 60 : key === "name" ? 150 : undefined}
            value={data[key]}
            onChange={(event) =>
              setData({ ...data, [key]: event.target.value })
            }
          />
        </label>
      ))}
      {basis !== "legacy" && (
        <fieldset className="wide-field" disabled={busy}>
          <legend>设备补贴与星级系数</legend>
          {basis === "execute" && (
            <div className="inline-note">
              <p>
                演示核算要求来源逐日声明完整设备集合，且设备计量与两轴状态覆盖完整。按北京时间周期结束时最后留存、仍有效的公示快照取星级；缺失、过期或摘牌不当无星处理。
              </p>
              <p>
                按设备每日先按分四舍五入，符合门槛的明细汇总后乘星级系数，再按分四舍五入。仅支持已结束周期，发放仍只登记状态。
              </p>
              <label>
                <input
                  aria-label="确认设备日演示核算口径"
                  type="checkbox"
                  required
                  checked={executionConfirmed}
                  onChange={(event) =>
                    setExecutionConfirmed(event.target.checked)
                  }
                />
                我已确认上述演示执行口径
              </label>
            </div>
          )}
          <p className="muted">
            {basis === "execute"
              ? "本规则仅作演示核算，不代表正式政策或财务认证。未知数据将阻止申报，不按零值或达标处理。"
              : "本配置版本未声明完整执行口径，不能申报；需另建可执行新版本。旧配置不会自动启用。"}
          </p>
          <div className="business-form">
            <label>
              换电补贴标准（元/度）
              <input
                aria-label="换电补贴标准（元/度）"
                type="number"
                min="0"
                step="0.000001"
                required
                value={data.swap_rate}
                onChange={(event) =>
                  setData({ ...data, swap_rate: event.target.value })
                }
              />
            </label>
            <label>
              补贴生成周期
              <select
                aria-label="补贴生成周期"
                value={period}
                onChange={(event) => setPeriod(event.target.value as Period)}
              >
                <option value="day">日（原型建议）</option>
                <option value="month">月</option>
                <option value="year">年</option>
              </select>
            </label>
            {(["fault_rate_below", "offline_rate_below"] as const).map(
              (key) => (
                <label key={key}>
                  {key === "fault_rate_below"
                    ? "故障率低于（%）"
                    : "离线率低于（%）"}
                  <input
                    aria-label={
                      key === "fault_rate_below"
                        ? "故障率门槛（%）"
                        : "离线率门槛（%）"
                    }
                    type="number"
                    min="0"
                    max="100"
                    step="0.0001"
                    required
                    value={data[key]}
                    onChange={(event) =>
                      setData({ ...data, [key]: event.target.value })
                    }
                  />
                </label>
              ),
            )}
            {Object.entries(coefficients).map(([star, value]) => (
              <label key={star}>
                {star === "0" ? "无星" : `${star}星`}系数
                <input
                  aria-label={star === "0" ? "无星系数" : `${star}星系数`}
                  type="number"
                  min="0"
                  step="0.000001"
                  required
                  value={value}
                  onChange={(event) =>
                    setCoefficients({
                      ...coefficients,
                      [star]: event.target.value,
                    })
                  }
                />
              </label>
            ))}
          </div>
          <p className="muted">
            门槛使用严格小于，等于门槛不达标。周期仅保存配置，尚不代表已启用定时生成；系数取值时点与正式统计口径仍待确认。
          </p>
        </fieldset>
      )}
      <button disabled={busy} className="outline">
        保存不可变政策版本
      </button>
    </form>
  );
}
