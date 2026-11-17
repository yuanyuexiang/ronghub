"use client";
import { useEffect, useRef, useState } from "react";
import { alertLevelLabel } from "../lib/alert-labels";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { useCapability, useGlobalScope } from "./permissions";
import { usePageRefresh } from "./page-refresh";
type Rule = ApiSchemas["RuleResponse"];
type Configuration = Rule["configuration"];
type Kind = Configuration["kind"];
const kinds: Record<Kind, string> = {
  device_temperature: "设备过温",
  device_current: "设备过流",
  device_leakage: "设备漏电",
  station_utilization: "场站利用率",
  station_fault: "场站故障率",
  station_offline: "场站离线率",
};
const periods = {
  instant: "即时观测",
  natural_week: "自然周（周一至周日）",
  natural_month: "自然月",
};
const levels = { general: "一般", severe: "严重", critical: "紧急 / 重大" };
const units: Record<Kind, string> = {
  device_temperature: "℃",
  device_current: "%额定电流",
  device_leakage: "mA",
  station_utilization: "%",
  station_fault: "%",
  station_offline: "%",
};
function initial() {
  return {
    code: "",
    name: "",
    version: "1",
    kind: "device_temperature" as Kind,
    direction: "gte" as Configuration["direction"],
    period: "instant" as Configuration["period"],
    general: "",
    severe: "",
    critical: "",
    recover: "",
    notifications: true,
  };
}
export function AlertConfiguration({
  role,
  csrf,
}: {
  role: string;
  csrf: string;
}) {
  const canRules = useCapability("alerts.rules");
  const globalScope = useGlobalScope();
  const manage = canRules && globalScope && role === "regulator";
  const revision = usePageRefresh(),
    sequence = useRef(0);
  const [data, setData] = useState<ApiSchemas["RulePage"] | null>(null),
    [loading, setLoading] = useState(false),
    [error, setError] = useState("");
  const [search, setSearch] = useState(""),
    [query, setQuery] = useState({ q: "", page: 1, include_archived: false }),
    [reload, setReload] = useState(0);
  const [selected, setSelected] = useState<Rule | null>(null),
    [form, setForm] = useState(initial),
    [showForm, setShowForm] = useState(false),
    [busy, setBusy] = useState(false),
    [writeError, setWriteError] = useState("");
  const [value, setValue] = useState(""),
    [rated, setRated] = useState(""),
    [start, setStart] = useState(""),
    [end, setEnd] = useState(""),
    [complete, setComplete] = useState(false),
    [preview, setPreview] = useState<ApiSchemas["PreviewResponse"] | null>(
      null,
    );
  const periodic = form.kind.startsWith("station_");
  useEffect(() => {
    const current = ++sequence.current,
      controller = new AbortController();
    setLoading(true);
    setError("");
    typedApi
      .GET("/api/v1/alerts/configured-rules", {
        signal: controller.signal,
        cache: "no-store",
        params: { query: { ...query, page_size: 20 } },
      })
      .then((result) => {
        if (controller.signal.aborted || sequence.current !== current) return;
        if (!result.data)
          throw new Error(apiError(result.error, "配置读取失败"));
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
        if (!controller.signal.aborted && sequence.current === current)
          setError(reason instanceof Error ? reason.message : "读取失败");
      })
      .finally(() => {
        if (!controller.signal.aborted && sequence.current === current)
          setLoading(false);
      });
    return () => controller.abort();
  }, [query, revision, reload]);
  function configuration(): Configuration {
    const thresholds: Configuration["thresholds"] = {};
    for (const key of ["general", "severe", "critical"] as const)
      if (form[key].trim()) thresholds[key] = form[key].trim();
    if (!Object.keys(thresholds).length)
      throw new Error("至少填写一个等级阈值");
    return {
      configuration_version: 1,
      kind: form.kind,
      direction: form.direction,
      period: form.period,
      thresholds,
      recover_value: form.recover,
    };
  }
  async function save() {
    setBusy(true);
    setWriteError("");
    try {
      const result = await typedApi.POST("/api/v1/alerts/configured-rules", {
        headers: { "x-csrf-token": csrf },
        body: {
          code: form.code,
          name: form.name,
          version: Number(form.version),
          configuration: configuration(),
          notifications_enabled: form.notifications,
        },
      });
      if (!result.data)
        throw new Error(
          apiError(result.error, "保存失败，请核对版本及阈值；原输入保留"),
        );
      setSelected(result.data);
      setShowForm(false);
      setQuery({ ...query, page: 1 });
      setReload((v) => v + 1);
    } catch (reason) {
      setWriteError(reason instanceof Error ? reason.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }
  async function test() {
    setBusy(true);
    setWriteError("");
    setPreview(null);
    try {
      const result = await typedApi.POST(
        "/api/v1/alerts/configured-rules/preview",
        {
          headers: { "x-csrf-token": csrf },
          body: {
            configuration: configuration(),
            value: value.trim() || null,
            rated_current_a: form.kind === "device_current" ? rated : null,
            period_start: periodic ? start : null,
            period_end: periodic ? end : null,
            coverage_complete: complete,
          },
        },
      );
      if (!result.data)
        throw new Error(
          apiError(result.error, "预览失败，请核对阈值、完整周期及观测依据"),
        );
      setPreview(result.data);
    } catch (reason) {
      setWriteError(reason instanceof Error ? reason.message : "预览失败");
    } finally {
      setBusy(false);
    }
  }
  async function control(row: Rule, archive = false) {
    setBusy(true);
    setWriteError("");
    try {
      const result = archive
        ? await typedApi.DELETE("/api/v1/alerts/configured-rules/{rule_id}", {
            headers: { "x-csrf-token": csrf },
            params: {
              path: { rule_id: row.id },
              query: { revision: row.revision },
            },
          })
        : await typedApi.PATCH(
            "/api/v1/alerts/configured-rules/{rule_id}/availability",
            {
              headers: { "x-csrf-token": csrf },
              params: { path: { rule_id: row.id } },
              body: { revision: row.revision, enabled: !row.enabled },
            },
          );
      if (!result.data)
        throw new Error(
          apiError(result.error, "状态操作失败，未自动重试；请刷新核对版本"),
        );
      setSelected(result.data);
      setReload((v) => v + 1);
    } catch (reason) {
      setWriteError(reason instanceof Error ? reason.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }
  function copy(row: Rule) {
    const c = row.configuration;
    setForm({
      code: row.code,
      name: row.name,
      version: String(row.version + 1),
      kind: c.kind,
      direction: c.direction,
      period: c.period,
      general: String(c.thresholds.general ?? ""),
      severe: String(c.thresholds.severe ?? ""),
      critical: String(c.thresholds.critical ?? ""),
      recover: String(c.recover_value),
      notifications: row.notifications_enabled,
    });
    setShowForm(true);
    setPreview(null);
    setWriteError("");
  }
  return (
    <section
      className="panel alert-config-panel"
      aria-label="分级与周期告警配置"
    >
      <div className="panel-title">
        <div>
          <h3>分级与周期告警配置</h3>
          <p>明确告警方向、等级与完整监控周期，保存为独立版本</p>
        </div>
        {manage && (
          <button
            className="outline"
            disabled={busy}
            onClick={() => {
              setForm(initial());
              setShowForm(true);
              setPreview(null);
              setWriteError("");
            }}
          >
            新增分级规则
          </button>
        )}
      </div>
      <p className="alert-config-notice">
        配置与预览使用演示口径。周期规则由 Worker
        判定监控起点之后闭合的独立来源数据；设备即时观测仍为人工演示。客户真实数据待接入，站内通知不代表短信已发送。
      </p>
      <form
        className="alert-config-search"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery({ ...query, q: search, page: 1 });
        }}
      >
        <input
          aria-label="分级规则名称或代码"
          placeholder="名称或代码"
          value={search}
          maxLength={150}
          onChange={(e) => setSearch(e.target.value)}
        />
        <button className="outline" disabled={loading || busy}>
          查询分级规则
        </button>
        {manage && (
          <label>
            <input
              type="checkbox"
              checked={query.include_archived}
              onChange={(e) =>
                setQuery({
                  ...query,
                  include_archived: e.target.checked,
                  page: 1,
                })
              }
            />
            包含已归档
          </label>
        )}
      </form>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {writeError && (
        <p className="error" role="alert">
          {writeError}
        </p>
      )}
      {loading && (
        <p className="alert-config-notice" role="status">
          正在读取配置…
        </p>
      )}
      {data && !loading && !error && (
        <>
          <div className="table-wrap">
            <table className="alert-config-table">
              <thead>
                <tr>
                  {[
                    "名称 / 版本",
                    "类型 / 周期",
                    "方向及等级阈值",
                    "状态 / 修改信息",
                    "操作",
                  ].map((label) => (
                    <th key={label}>{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.items.map((row) => (
                  <tr key={row.id}>
                    <td>
                      {row.name}
                      <small>
                        {row.code} / V{row.version}
                      </small>
                    </td>
                    <td>
                      {kinds[row.configuration.kind]}
                      <small>{periods[row.configuration.period]}</small>
                    </td>
                    <td>
                      {Object.entries(row.configuration.thresholds).map(
                        ([level, threshold]) => (
                          <small key={level}>
                            {alertLevelLabel(level, row.configuration.period)}{" "}
                            {row.configuration.direction === "gte" ? "≥" : "≤"}{" "}
                            {threshold} {row.unit}
                          </small>
                        ),
                      )}
                    </td>
                    <td>
                      {row.archived ? "已归档" : row.enabled ? "启用" : "停用"}
                      <small>{row.updated_by_name || "修改人未留存"}</small>
                      <small>
                        {row.updated_at
                          ? new Date(row.updated_at).toLocaleString("zh-CN", {
                              timeZone: "Asia/Shanghai",
                            })
                          : "时间未留存"}
                      </small>
                    </td>
                    <td>
                      <button
                        className="outline"
                        disabled={busy}
                        onClick={() => setSelected(row)}
                        aria-label={`查看分级规则 ${row.code} V${row.version}`}
                      >
                        详情
                      </button>
                      {manage && !row.archived && (
                        <>
                          <button
                            className="outline"
                            disabled={busy}
                            onClick={() => copy(row)}
                          >
                            复制为新版本
                          </button>
                          <button
                            className="outline"
                            disabled={busy}
                            onClick={() => control(row)}
                          >
                            {row.enabled ? "停用分级规则" : "启用分级规则"}
                          </button>
                          <button
                            className="outline danger"
                            disabled={busy}
                            onClick={() => {
                              if (
                                window.confirm(
                                  "归档后配置不再生效，历史依据保留。是否继续？",
                                )
                              )
                                void control(row, true);
                            }}
                          >
                            归档分级规则
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!data.items.length && <p className="empty">没有匹配的分级规则</p>}
          </div>
          <div className="alert-config-pagination">
            <button
              className="outline"
              disabled={busy || data.page <= 1}
              onClick={() => setQuery({ ...query, page: query.page - 1 })}
            >
              分级规则上一页
            </button>
            <span>
              第{data.page}页 · 共{data.total}条
            </span>
            <button
              className="outline"
              disabled={busy || data.page * data.page_size >= data.total}
              onClick={() => setQuery({ ...query, page: query.page + 1 })}
            >
              分级规则下一页
            </button>
          </div>
        </>
      )}
      {selected && (
        <section className="alert-config-detail" aria-label="分级规则详情">
          <div className="panel-title">
            <h3>
              {selected.name} · V{selected.version}
            </h3>
            <button
              className="outline"
              disabled={busy}
              onClick={() => setSelected(null)}
            >
              关闭分级详情
            </button>
          </div>
          <p>
            {kinds[selected.configuration.kind]} ·{" "}
            {periods[selected.configuration.period]} ·{" "}
            {selected.configuration.direction === "gte"
              ? "高值达到阈值"
              : "低值达到阈值"}
          </p>
          <p>
            恢复阈值 {selected.configuration.direction === "gte" ? "≤" : "≥"}{" "}
            {String(selected.configuration.recover_value)} {selected.unit} ·
            站内通知{selected.notifications_enabled ? "开启" : "关闭"} ·
            状态版本 {selected.revision}
          </p>
          <p>
            等级： 周期自动判定：
            {
              {
                instant_manual: "即时观测暂用人工演示",
                disabled: "已停用",
                superseded: "已被更高版本替代",
                missing_start: "缺少监控起点，须明确停用后重新启用",
                eligible: "等待监控起点之后闭合的来源周期",
              }[selected.automatic_status]
            }
            {selected.monitor_from && (
              <>
                {" "}
                · 监控起点：
                {new Date(selected.monitor_from).toLocaleString("zh-CN", {
                  timeZone: "Asia/Shanghai",
                })}
              </>
            )}
          </p>
          <p>
            {Object.entries(selected.configuration.thresholds)
              .map(
                ([level, threshold]) =>
                  `${alertLevelLabel(level, selected.configuration.period)} ${threshold} ${selected.unit}`,
              )
              .join("；")}
          </p>
          <p className="muted">
            配置内容不可覆盖修改；复制新版本后，旧版本和历史事件依据保留。停用或归档不代表设备异常已恢复。
          </p>
        </section>
      )}
      {showForm && manage && (
        <form
          className="alert-config-form"
          aria-label="分级规则表单"
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <fieldset disabled={busy}>
            <legend>新增不可变规则版本</legend>
            <div className="alert-config-fields">
              {[
                ["code", "分级规则代码"],
                ["name", "分级规则名称"],
                ["version", "分级规则版本"],
              ].map(([key, label]) => (
                <label key={key}>
                  {label}
                  <input
                    aria-label={label}
                    required
                    type={key === "version" ? "number" : "text"}
                    min={key === "version" ? 1 : undefined}
                    max={key === "version" ? 2147483647 : undefined}
                    maxLength={key === "name" ? 150 : 60}
                    value={form[key as "code" | "name" | "version"]}
                    onChange={(e) =>
                      setForm({ ...form, [key]: e.target.value })
                    }
                  />
                </label>
              ))}
              <label>
                告警类型
                <select
                  aria-label="分级告警类型"
                  value={form.kind}
                  onChange={(e) => {
                    const kind = e.target.value as Kind;
                    setForm({
                      ...form,
                      kind,
                      period: kind.startsWith("station_")
                        ? "natural_week"
                        : "instant",
                      general: "",
                      severe: "",
                      critical: "",
                      recover: "",
                    });
                    setPreview(null);
                  }}
                >
                  {Object.entries(kinds).map(([key, label]) => (
                    <option value={key} key={key}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                阈值方向
                <select
                  aria-label="分级阈值方向"
                  value={form.direction}
                  onChange={(e) => {
                    setForm({
                      ...form,
                      direction: e.target.value as Configuration["direction"],
                    });
                    setPreview(null);
                  }}
                >
                  <option value="gte">≥ 高值告警</option>
                  <option value="lte">≤ 低值告警</option>
                </select>
              </label>
              {periodic && (
                <label>
                  监控周期
                  <select
                    aria-label="分级监控周期"
                    value={form.period}
                    onChange={(e) => {
                      setForm({
                        ...form,
                        period: e.target.value as Configuration["period"],
                      });
                      setPreview(null);
                    }}
                  >
                    <option value="natural_week">自然周（周一至周日）</option>
                    <option value="natural_month">自然月</option>
                  </select>
                </label>
              )}
              {Object.entries(levels).map(([key, label]) => (
                <label key={key}>
                  {key === "critical"
                    ? alertLevelLabel(key, form.period)
                    : label}
                  阈值（{units[form.kind]}）
                  <input
                    aria-label={`分级${key}阈值`}
                    type="number"
                    step="0.000001"
                    value={form[key as "general" | "severe" | "critical"]}
                    onChange={(e) => {
                      setForm({ ...form, [key]: e.target.value });
                      setPreview(null);
                    }}
                  />
                </label>
              ))}
              <label>
                恢复阈值（{units[form.kind]}）
                <input
                  aria-label="分级恢复阈值"
                  type="number"
                  step="0.000001"
                  required
                  value={form.recover}
                  onChange={(e) => {
                    setForm({ ...form, recover: e.target.value });
                    setPreview(null);
                  }}
                />
              </label>
            </div>
            <p className="muted">
              至少填写一个等级；高值告警越严重阈值越高，低值告警相反。恢复阈值须越过最低触发阈值。
            </p>
            <label>
              <input
                type="checkbox"
                checked={form.notifications}
                onChange={(e) =>
                  setForm({ ...form, notifications: e.target.checked })
                }
              />
              生成站内通知
            </label>
            <div className="alert-config-fields">
              <label>
                实测值（
                {form.kind === "device_current" ? "A" : units[form.kind]}）
                <input
                  aria-label="分级预览观测值"
                  type="number"
                  step="0.000001"
                  value={value}
                  onChange={(e) => {
                    setValue(e.target.value);
                    setPreview(null);
                  }}
                />
              </label>
              {form.kind === "device_current" && (
                <label>
                  额定电流 A
                  <input
                    aria-label="分级预览额定电流"
                    type="number"
                    step="0.000001"
                    value={rated}
                    onChange={(e) => {
                      setRated(e.target.value);
                      setPreview(null);
                    }}
                  />
                </label>
              )}
              {periodic && (
                <>
                  <label>
                    完整周期开始（北京时间）
                    <input
                      aria-label="分级预览周期开始"
                      type="date"
                      value={start}
                      onChange={(e) => {
                        setStart(e.target.value);
                        setPreview(null);
                      }}
                    />
                  </label>
                  <label>
                    周期结束（不含该日）
                    <input
                      aria-label="分级预览周期结束"
                      type="date"
                      value={end}
                      onChange={(e) => {
                        setEnd(e.target.value);
                        setPreview(null);
                      }}
                    />
                  </label>
                </>
              )}
            </div>
            <label>
              <input
                type="checkbox"
                checked={complete}
                onChange={(e) => {
                  setComplete(e.target.checked);
                  setPreview(null);
                }}
              />
              预览观测覆盖完整（仅演示声明）
            </label>
            <div className="action-row">
              <button className="outline" type="button" onClick={test}>
                预览分级判断
              </button>
              <button className="primary" type="submit">
                保存分级规则版本
              </button>
              <button
                className="outline"
                type="button"
                onClick={() => setShowForm(false)}
              >
                取消新增分级规则
              </button>
            </div>
            {preview && (
              <p role="status">
                {preview.verdict === "unknown"
                  ? "覆盖未知"
                  : preview.level
                    ? `触发${alertLevelLabel(preview.level, form.period)}`
                    : "未达到触发阈值"}{" "}
                · 比较值 {preview.comparison_value ?? "未知"} {preview.unit}
                <small>{preview.notice}</small>
              </p>
            )}
          </fieldset>
        </form>
      )}
    </section>
  );
}
