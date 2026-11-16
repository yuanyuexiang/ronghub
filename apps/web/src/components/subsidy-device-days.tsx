"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { StationPicker } from "./station-picker";
import { usePageRefresh } from "./page-refresh";
import { useCapability } from "./permissions";

type Query = {
  start_date: string;
  end_date: string;
  station_id: string;
  equipment_external_id: string;
  source: string;
  page: number;
};
const missing: Record<string, string> = {
  missing: "未上报",
  basis_unconfirmed: "口径未确认",
  partial: "部分覆盖",
  unknown_states: "含未知状态",
};
const kinds: Record<string, string> = {
  dc: "直流",
  ac: "交流",
  swapping: "换电",
  charging: "充电",
  unknown: "未知",
};
function initialQuery(): Query {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    })
      .formatToParts(new Date())
      .map((part) => [part.type, part.value]),
  );
  return {
    start_date: `${parts.year}-${parts.month}-01`,
    end_date: `${parts.year}-${parts.month}-${parts.day}`,
    station_id: "",
    equipment_external_id: "",
    source: "",
    page: 1,
  };
}
function params(query: Query) {
  return {
    start_date: query.start_date,
    end_date: query.end_date,
    station_id: query.station_id ? Number(query.station_id) : undefined,
    equipment_external_id: query.equipment_external_id,
    source: query.source,
  };
}
export function SubsidyDeviceDays() {
  const canExport = useCapability("subsidy.export");
  const [draft, setDraft] = useState(initialQuery),
    [applied, setApplied] = useState<Query | null>(null),
    [data, setData] = useState<ApiSchemas["SubsidyDeviceDayPage"] | null>(null),
    [loading, setLoading] = useState(false),
    [error, setError] = useState(""),
    [exporting, setExporting] = useState(false),
    [exportError, setExportError] = useState("");
  const chooseStation = useCallback(
    (station_id: string) => setDraft((current) => ({ ...current, station_id })),
    [],
  );
  const revision = usePageRefresh(),
    sequence = useRef(0);
  useEffect(() => {
    if (!applied) return;
    const current = ++sequence.current,
      controller = new AbortController();
    setLoading(true);
    setError("");
    typedApi
      .GET("/api/v1/subsidy/device-days", {
        signal: controller.signal,
        cache: "no-store",
        params: {
          query: { ...params(applied), page: applied.page, page_size: 20 },
        },
      })
      .then((result) => {
        if (current !== sequence.current || controller.signal.aborted) return;
        if (!result.data)
          throw new Error(apiError(result.error, "设备日观测读取失败，请重试"));
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
      const result = await typedApi.GET("/api/v1/subsidy/device-days/export", {
        parseAs: "blob",
        cache: "no-store",
        params: { query: params(applied) },
      });
      if (!result.data)
        throw new Error(apiError(result.error, "导出失败，请缩小查询范围"));
      const url = URL.createObjectURL(result.data),
        link = document.createElement("a");
      link.href = url;
      link.download = "ronghub-device-days.csv";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (reason) {
      setExportError(reason instanceof Error ? reason.message : "导出失败");
    } finally {
      setExporting(false);
    }
  }
  return (
    <section className="panel device-days-panel" aria-label="设备日观测">
      <div className="panel-title">
        <div>
          <h2>设备日观测</h2>
          <p>
            独立计量及日历日状态覆盖，用于核对设备数据；尚不可据此核算补贴。
          </p>
        </div>
      </div>
      <form
        className="business-form"
        onSubmit={(event) => {
          event.preventDefault();
          setExportError("");
          setApplied({ ...draft, page: 1 });
        }}
      >
        <StationPicker
          label="设备日观测场站"
          value={draft.station_id}
          onChange={chooseStation}
          allowAll
        />
        <label>
          开始日期（北京时间）
          <input
            aria-label="设备日观测开始日期"
            type="date"
            required
            value={draft.start_date}
            onChange={(event) =>
              setDraft({ ...draft, start_date: event.target.value })
            }
          />
        </label>
        <label>
          结束日期（北京时间）
          <input
            aria-label="设备日观测结束日期"
            type="date"
            required
            value={draft.end_date}
            onChange={(event) =>
              setDraft({ ...draft, end_date: event.target.value })
            }
          />
        </label>
        <label>
          来源设备编码
          <input
            aria-label="设备日观测设备编码"
            maxLength={100}
            value={draft.equipment_external_id}
            onChange={(event) =>
              setDraft({ ...draft, equipment_external_id: event.target.value })
            }
          />
        </label>
        <label>
          来源
          <input
            aria-label="设备日观测来源"
            maxLength={50}
            value={draft.source}
            onChange={(event) =>
              setDraft({ ...draft, source: event.target.value })
            }
          />
        </label>
        <button className="outline" disabled={loading || exporting}>
          查询设备日观测
        </button>
        {canExport && (
          <button
            type="button"
            className="outline"
            disabled={!applied || loading || exporting}
            onClick={() => void download()}
          >
            {exporting ? "正在导出…" : "导出设备日观测CSV"}
          </button>
        )}
      </form>
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
      {loading && (
        <p className="empty" role="status">
          正在读取设备日观测…
        </p>
      )}
      {!applied && (
        <p className="empty">
          选择条件后查询；未上报的设备或日期不按零值补齐。
        </p>
      )}
      {data && !loading && !error && (
        <>
          <p className="device-days-notice muted">{data.notice}</p>
          <p className="device-days-notice" aria-label="设备日观测已应用条件">
            已查询：{applied!.start_date} 至 {applied!.end_date} ·{" "}
            {applied!.station_id
              ? `场站编号 ${applied!.station_id}`
              : "全部授权场站"}{" "}
            · 来源：{applied!.source || "全部"} · 设备编码：
            {applied!.equipment_external_id || "全部"}
            。修改输入后请点击查询；导出沿用此条件。
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {[
                    "日期 / 来源",
                    "设备 / 场站",
                    "来源设备资料",
                    "独立日计量 kWh",
                    "故障日历日比例",
                    "离线日历日比例",
                    "覆盖与核验",
                  ].map((label) => (
                    <th key={label}>{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.items.map((row) => (
                  <tr key={row.id}>
                    <td>
                      {row.day}
                      <small>
                        {row.source} · V{row.version}
                        {row.is_demo ? " · 演示" : ""}
                      </small>
                    </td>
                    <td>
                      {row.equipment_name ?? "来源名称未上报"}
                      <small>{row.equipment_external_id}</small>
                      <small>当前台账：{row.current_equipment_name}</small>
                      <small>
                        {row.station_name} · {row.operator_name}
                      </small>
                    </td>
                    <td>
                      {kinds[row.equipment_type]} ·{" "}
                      {row.power_kw === null
                        ? "功率未知"
                        : `${row.power_kw} kW`}
                      <small>生产日期：{row.production_date ?? "未上报"}</small>
                      <small>业务：{kinds[row.business_kind]}</small>
                    </td>
                    <td>
                      {row.energy_kwh ?? "未知"}
                      <small>
                        {row.energy_complete ? "完整日计量" : "日计量未完整"}
                      </small>
                    </td>
                    {(["fault", "offline"] as const).map((axis) => (
                      <td key={axis}>
                        {row[`${axis}_percent`] === null
                          ? missing[row[`${axis}_percent_reason`] ?? "missing"]
                          : `${row[`${axis}_percent`]}%`}
                        <small>
                          上报 {row[`${axis}_observed_seconds`] ?? "未知"} 秒
                        </small>
                        <small>
                          {axis === "fault" ? "故障" : "离线"}{" "}
                          {row[`${axis}_seconds`] ?? "未知"} 秒
                        </small>
                        <small>
                          未知 {row[`${axis}_unknown_seconds`] ?? "未知"} 秒
                        </small>
                      </td>
                    ))}
                    <td>
                      {row.observation_complete
                        ? "计量与状态覆盖齐全"
                        : "观测存在缺项"}
                      <small>精确编码匹配，未作财务认证</small>
                      <small>不可结算</small>
                      <small>
                        源修订：
                        {new Date(row.source_updated_at).toLocaleString(
                          "zh-CN",
                          { timeZone: "Asia/Shanghai" },
                        )}
                      </small>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!data.items.length && (
            <p className="empty">
              当前条件下没有已上报设备日记录；不代表零电量或零故障。
            </p>
          )}
          <div className="pagination">
            <button
              className="outline"
              disabled={exporting || data.page <= 1}
              onClick={() => setApplied({ ...applied!, page: data.page - 1 })}
            >
              设备日观测上一页
            </button>
            <span>
              第{data.page}页 · 共{data.total}条设备日观测
            </span>
            <button
              className="outline"
              disabled={exporting || data.page * data.page_size >= data.total}
              onClick={() => setApplied({ ...applied!, page: data.page + 1 })}
            >
              设备日观测下一页
            </button>
          </div>
        </>
      )}
    </section>
  );
}
