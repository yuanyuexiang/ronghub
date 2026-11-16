"use client";
import type { ApiSchemas } from "../lib/typed-api";
export const runtimeLabels: Record<string, string> = {
  connectivity: "连接状态",
  operation: "业务状态",
  online: "在线",
  offline: "离线",
  unknown: "明确未知",
  available: "空闲",
  occupied: "占用",
  charging: "充电",
  faulted: "故障",
  maintenance: "维护",
};
const colors: Record<string, string> = {
  online: "#208d81",
  offline: "#ef9856",
  unknown: "#9aa9bc",
  available: "#208d81",
  occupied: "#a18bce",
  charging: "#367acf",
  faulted: "#d65d66",
  maintenance: "#c9973d",
};
export function formatObservedNumber(value: string) {
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 6 }).format(
    Number(value),
  );
}
export function RuntimeAxes({
  axes,
  windowSeconds,
  demo,
}: {
  axes: ApiSchemas["RuntimeAxis"][];
  windowSeconds: string;
  demo: boolean;
}) {
  return (
    <div className="runtime-axes">
      {axes.map((axis) => (
        <article
          className="runtime-axis"
          key={axis.dimension}
          aria-label={`${demo ? "演示" : "源报"}${runtimeLabels[axis.dimension]}覆盖`}
        >
          <h4>{runtimeLabels[axis.dimension]}</h4>
          <div className="runtime-state-bar" aria-hidden="true">
            {axis.states.map((state) => (
              <span
                key={state.state}
                style={{
                  width: `${Number(windowSeconds) ? (Number(state.observed_seconds) / Number(windowSeconds)) * 100 : 0}%`,
                  background: colors[state.state],
                }}
              />
            ))}
            <span
              style={{
                width: `${Number(windowSeconds) ? (Number(axis.unreported_seconds) / Number(windowSeconds)) * 100 : 0}%`,
                background: "#e6edf5",
              }}
            />
          </div>
          <p>
            已上送{formatObservedNumber(axis.reported_seconds)}秒 · 其中已知
            {formatObservedNumber(axis.known_seconds)}秒 / 明确未知
            {formatObservedNumber(axis.unknown_seconds)}秒
          </p>
          <p>未上送查询时长{formatObservedNumber(axis.unreported_seconds)}秒</p>
          <ul>
            {axis.states.map((state) => (
              <li key={state.state}>
                <span>
                  <i style={{ background: colors[state.state] }} />
                  {runtimeLabels[state.state]}
                </span>
                <strong>
                  {formatObservedNumber(state.observed_seconds)}秒
                </strong>
                <small>
                  {state.share_of_reported_pct === null
                    ? "暂无占比"
                    : `占已上送${state.share_of_reported_pct}%`}
                </small>
              </li>
            ))}
          </ul>
          <small className="muted">
            占比只在本维度已上送区间内计算，包含明确未知。
          </small>
        </article>
      ))}
    </div>
  );
}
