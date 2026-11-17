"use client";
import { useEffect, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { usePageRefresh } from "./page-refresh";
import { StationPicker } from "./station-picker";
type Fact = ApiSchemas["PeriodFactResponse"];
const quality: Record<string, string> = {
  incomplete_members: "设备集合未完整返回",
  unknown_service: "服务时长未知",
  partial_coverage: "观测覆盖不足",
  unknown_states: "存在未知状态",
  no_service_denominator: "缺少有效服务分母",
};
const metrics = {
  station_utilization: "利用率",
  station_fault: "故障率",
  station_offline: "离线率",
};
export function AlertPeriodFacts() {
  const refresh = usePageRefresh();
  const [open, setOpen] = useState(false),
    [station, setStation] = useState("");
  const [query, setQuery] = useState({ station: "", page: 1 }),
    [reload, setReload] = useState(0);
  const [data, setData] = useState<ApiSchemas["PeriodFactPage"] | null>(null);
  const [selected, setSelected] = useState<Fact | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    typedApi
      .GET("/api/v1/alerts/period-facts", {
        signal: controller.signal,
        cache: "no-store",
        params: {
          query: {
            station_id: query.station ? Number(query.station) : undefined,
            page: query.page,
            page_size: 20,
          },
        },
      })
      .then((result) => {
        if (controller.signal.aborted) return;
        if (!result.data)
          throw new Error(apiError(result.error, "周期来源读取失败"));
        const last = Math.max(
          1,
          Math.ceil(result.data.total / result.data.page_size),
        );
        if (query.page > last) {
          setQuery({ ...query, page: last });
          return;
        }
        setData(result.data);
      })
      .catch((reason) => {
        if (!controller.signal.aborted)
          setError(reason instanceof Error ? reason.message : "读取失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [open, query, reload, refresh]);
  return (
    <section className="panel" aria-label="独立周期来源">
      <div className="panel-title">
        <div>
          <h3>独立周期来源</h3>
          <p className="muted">查看历史设备集合、覆盖质量及计算依据</p>
        </div>
        <button className="outline" onClick={() => setOpen(!open)}>
          {open ? "收起周期来源" : "查看周期来源"}
        </button>
      </div>
      {open && (
        <>
          <p className="alert-config-notice">
            独立演示数据，非正式核定指标。Worker
            仅判定监控起点之后闭合的周期；历史周期不自动回放，覆盖未知不清除异常。
          </p>
          <form
            className="alert-config-search"
            onSubmit={(e) => {
              e.preventDefault();
              setQuery({ station, page: 1 });
              setReload((x) => x + 1);
            }}
          >
            <StationPicker
              label="周期来源场站"
              allowAll
              kind="alerts"
              value={station}
              onChange={setStation}
            />
            <button className="outline" disabled={loading}>
              查询周期来源
            </button>
          </form>
          {error && <p role="alert">{error}</p>}
          {loading ? (
            <p role="status">正在读取周期来源…</p>
          ) : (
            !error &&
            data && (
              <>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        {[
                          "场站 / 来源版本",
                          "指标 / 周期",
                          "独立覆盖",
                          "判定值",
                          "操作",
                        ].map((x) => (
                          <th key={x}>{x}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {data.items.map((row) => (
                        <tr key={row.id}>
                          <td>
                            {row.station_name}
                            <br />
                            <small>
                              {row.source} · FACT-{row.id} V{row.version}
                            </small>
                          </td>
                          <td>
                            {metrics[row.source_record.metric]} ·{" "}
                            {row.source_record.period === "natural_week"
                              ? "自然周"
                              : "自然月"}
                            <br />
                            <small>
                              {row.source_record.period_start} 至{" "}
                              {row.source_record.period_end}（结束不含）
                            </small>
                          </td>
                          <td>
                            {row.quality_reason
                              ? (quality[row.quality_reason] ?? "未知")
                              : "完整覆盖"}
                            <br />
                            <small>
                              {row.source_record.equipment_external_ids.length}{" "}
                              台来源声明设备
                            </small>
                          </td>
                          <td>
                            {row.percent === null ? "未知" : `${row.percent}%`}
                          </td>
                          <td>
                            <button
                              className="outline"
                              onClick={() => setSelected(row)}
                            >
                              查看依据
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {!data.items.length && (
                  <p className="muted">
                    暂无匹配的独立周期来源；演示同步任务完成后可查询。
                  </p>
                )}
                <div className="alert-config-pagination">
                  <span>
                    共 {data.total} 条 · 第 {query.page} 页
                  </span>
                  <button
                    className="outline"
                    disabled={query.page <= 1}
                    onClick={() => setQuery({ ...query, page: query.page - 1 })}
                  >
                    上一页
                  </button>
                  <button
                    className="outline"
                    disabled={query.page * data.page_size >= data.total}
                    onClick={() => setQuery({ ...query, page: query.page + 1 })}
                  >
                    下一页
                  </button>
                </div>
              </>
            )
          )}
          {selected && (
            <section
              className="configured-event-evidence"
              aria-label="周期来源依据"
            >
              <h3>
                FACT-{selected.id} · 来源版本 {selected.version}
              </h3>
              <p>
                来源声明影响秒数 / 应服务秒数：
                {selected.numerator_seconds ?? "未知"} /{" "}
                {selected.denominator_seconds ?? "未知"}
                。未知覆盖不使用部分汇总计算百分比。
              </p>
              <p>
                修订时间：
                {new Date(
                  selected.source_record.source_updated_at,
                ).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}
              </p>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      {[
                        "历史设备",
                        "应服务秒数",
                        "观测秒数",
                        "未知秒数",
                        "影响秒数",
                      ].map((x) => (
                        <th key={x}>{x}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {selected.source_record.members.map((row) => (
                      <tr key={row.equipment_external_id}>
                        <td>
                          {row.equipment_name ?? row.equipment_external_id}
                          <br />
                          <small>{row.equipment_external_id}</small>
                        </td>
                        {[
                          row.expected_seconds,
                          row.observed_seconds,
                          row.unknown_seconds,
                          row.affected_seconds,
                        ].map((value, i) => (
                          <td key={i}>{value ?? "未知"}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <button className="outline" onClick={() => setSelected(null)}>
                关闭来源依据
              </button>
            </section>
          )}
        </>
      )}
    </section>
  );
}
