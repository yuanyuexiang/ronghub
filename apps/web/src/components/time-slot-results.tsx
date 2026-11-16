"use client";
import { type ApiSchemas } from "../lib/typed-api";
type Stream = ApiSchemas["OrderTimeSlotStream"] &
  Partial<
    Pick<
      ApiSchemas["AggregateTimeSlotStream"],
      "stations_with_order_observations" | "stations_with_meter_records"
    >
  >;
export function TimeSlotResults({
  streams,
  multi = false,
}: {
  streams: readonly Stream[];
  multi?: boolean;
}) {
  return (
    <>
      {streams.map((stream) => {
        const max = Math.max(
          1,
          ...stream.slots.map((point) => point.observed_order_starts),
        );
        return (
          <section key={String(stream.is_demo)} className="time-slot-stream">
            <div className="panel-title">
              <div>
                <h3>
                  {stream.is_demo ? "明确演示数据" : "未标记为演示的数据"}
                </h3>
                <p>
                  本来源流共观测到{stream.observed_order_starts}
                  条订单开始记录；柱形仅表示观测数
                </p>
                {stream.stations_with_order_observations !== undefined && (
                  <p>
                    有订单观测{stream.stations_with_order_observations}站 ·
                    有站级计量记录{stream.stations_with_meter_records}站
                  </p>
                )}
              </div>
            </div>
            <div className="time-slot-chart-wrap">
              <div
                className="time-slot-chart"
                role="img"
                aria-label={`北京时间24小时已观测订单开始数，${stream.is_demo ? "演示" : "未标记演示"}`}
              >
                {stream.slots.map((point) => (
                  <div
                    className="time-slot-column"
                    key={point.hour}
                    title={`${point.label}：已观测开始${point.observed_order_starts}条`}
                  >
                    <span className="time-slot-count">
                      {point.observed_order_starts || ""}
                    </span>
                    <div className="time-slot-track">
                      <div
                        className="time-slot-bar"
                        style={{
                          height: `${(point.observed_order_starts / max) * 100}%`,
                        }}
                      />
                    </div>
                    <small>{String(point.hour).padStart(2, "0")}</small>
                  </div>
                ))}
              </div>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    {[
                      "时段（北京）",
                      multi ? "查询场站小时格" : "查询槽位",
                      "已观测开始数",
                      "观测占比",
                      "已知小时电量(kWh)",
                      "已知槽位",
                      "明确未知",
                      multi ? "未返回（本流）" : "未返回",
                    ].map((label) => (
                      <th key={label}>{label}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {stream.slots.map((point) => (
                    <tr key={point.hour}>
                      <td>{point.label}</td>
                      <td>{point.selected_slots}</td>
                      <td>
                        {point.observed_order_starts}
                        <span>已完成{point.completed_order_starts}</span>
                      </td>
                      <td>
                        {point.order_start_share_pct === null
                          ? "暂无占比"
                          : `${point.order_start_share_pct}%`}
                      </td>
                      <td>{point.kwh ?? "暂无计量数据"}</td>
                      <td>{point.known_meter_slots}</td>
                      <td>{point.unknown_meter_slots}</td>
                      <td>{point.unreported_meter_slots}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        );
      })}
    </>
  );
}
