"use client";
import { AlertNotifications } from "./alert-notifications";
import { ConfiguredEventEvidence } from "./configured-event-evidence";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { ConfiguredObservations } from "./configured-observations";
import { AlertPeriodFacts } from "./alert-period-facts";
import { AlertConfiguration } from "./alert-configuration";
import { usePageRefresh } from "./page-refresh";
import {
  useBusinessQueue,
  loadAlertQueue,
  alertQueueParams,
  type AlertCategory,
  QueuePagination,
} from "./business-queue";
import { useCapability, useGlobalScope } from "./permissions";
import { StationPicker } from "./station-picker";
import { useEffect, useId, useState } from "react";
type Rule = ApiSchemas["LegacyAlertRule"];
type Event = ApiSchemas["AlertDetail"];
export function AlertPanel({ role, csrf }: { role: string; csrf: string }) {
  const globalScope = useGlobalScope();
  const workspaceId = useId();
  const [view, setView] = useState("events");
  const canExport = useCapability("statistics.export");
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");
  const canRules = useCapability("alerts.rules");
  const canNotifications = useCapability("alerts.notifications");
  const manageNotifications =
    canNotifications && globalScope && role === "regulator";
  const currentView =
    view === "notifications" && !manageNotifications ? "events" : view;
  const canSamples = useCapability("alerts.samples");
  const canAcknowledge = useCapability("alerts.acknowledge");
  const queue = useBusinessQueue(loadAlertQueue);
  const events = queue.rows;
  const [rules, setRules] = useState<Rule[]>([]),
    [sid, setSid] = useState(""),
    [rid, setRid] = useState(""),
    [target, setTarget] = useState("demo-connector-1"),
    [value, setValue] = useState("40"),
    [detail, setDetail] = useState<Event | null>(null),
    [note, setNote] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [showSamples, setShowSamples] = useState(false),
    [showRules, setShowRules] = useState(false),
    [result, setResult] = useState("");
  function read<T>(result: { data?: T; error?: unknown }): T {
    if (result.data === undefined)
      throw new Error(apiError(result.error, "请检查填写内容"));
    return result.data;
  }
  async function exportEvents() {
    setExportError("");
    const applied = queue.query;
    if (!applied.start_at || !applied.end_at) {
      setExportError("请填写并查询触发开始、结束时间；单次最多两个自然月。");
      return;
    }
    setExporting(true);
    try {
      const result = await typedApi.GET("/api/v1/alerts/export", {
        cache: "no-store",
        parseAs: "blob",
        params: {
          query: {
            ...alertQueueParams(applied),
            start_at: new Date(applied.start_at).toISOString(),
            end_at: new Date(applied.end_at).toISOString(),
          },
        },
      });
      if (!result.data)
        throw new Error(
          apiError(result.error, "告警导出失败，请核对时间范围及权限"),
        );
      const url = URL.createObjectURL(result.data),
        link = document.createElement("a");
      link.href = url;
      link.download = "ronghub-alert-events.csv";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (reason) {
      setExportError(reason instanceof Error ? reason.message : "导出失败");
    } finally {
      setExporting(false);
    }
  }
  async function readDetail(id: number) {
    const result = await typedApi.GET("/api/v1/alerts/{event_id}", {
      cache: "no-store",
      params: { path: { event_id: id } },
    });
    if (!result.data)
      throw new Error(apiError(result.error, "告警详情读取失败"));
    return result.data;
  }
  async function load() {
    await queue.reload();
  }
  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "请求失败");
    } finally {
      setBusy(false);
    }
  }
  const pageRefreshRevision = usePageRefresh();
  useEffect(() => {
    const controller = new AbortController();
    typedApi
      .GET("/api/v1/alerts/rules", {
        cache: "no-store",
        signal: controller.signal,
      })
      .then((result) => {
        const d = read(result);
        if (controller.signal.aborted) return;
        setRules(d.items);
        if (d.items[0]) setRid((current) => current || String(d.items[0].id));
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, [role, pageRefreshRevision]);
  return (
    <div className="business-stack">
      <nav className="alert-workspace-nav" aria-label="告警工作区">
        {[
          ["events", "事件跟踪", "查询、处置与导出"],
          ["rules", "规则配置", "分级、周期与历史版本"],
          ["sources", "来源与观测", "覆盖依据、回执与演示"],
          ...(manageNotifications
            ? [["notifications", "通知设置", "通道、接收人与回执"]]
            : []),
        ].map(([key, title, caption]) => (
          <button
            key={key}
            type="button"
            aria-label={title}
            aria-pressed={currentView === key}
            aria-controls={`${workspaceId}-${key}`}
            onClick={() => setView(key)}
          >
            <strong>{title}</strong>
            <span>{caption}</span>
          </button>
        ))}
      </nav>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div
        id={`${workspaceId}-events`}
        className="alert-workspace-pane business-stack"
        hidden={currentView !== "events"}
      >
        <section className="panel alert-queue">
          <div className="panel-title">
            <div>
              <h2>{role === "regulator" ? "告警事件跟踪" : "企业告警事件"}</h2>
              <p>持续异常合并、恢复后留档，人工确认与设备恢复分别记录。</p>
            </div>
            <button
              className="outline"
              disabled={busy}
              onClick={() => run(load)}
            >
              刷新告警
            </button>
          </div>
          <form
            className="application-filters"
            onSubmit={(e) => {
              e.preventDefault();
              setDetail(null);
              queue.apply();
            }}
          >
            <label>
              对象、场站或企业
              <input
                aria-label="告警检索名称"
                maxLength={150}
                value={queue.draft.q}
                onChange={(e) =>
                  queue.setDraft({ ...queue.draft, q: e.target.value })
                }
              />
            </label>
            <label>
              事件编号
              <input
                aria-label="告警检索编号"
                type="number"
                min="1"
                value={queue.draft.record_id}
                onChange={(e) =>
                  queue.setDraft({ ...queue.draft, record_id: e.target.value })
                }
              />
            </label>
            <label>
              设备事件状态
              <select
                aria-label="告警筛选状态"
                value={queue.draft.status}
                onChange={(e) =>
                  queue.setDraft({ ...queue.draft, status: e.target.value })
                }
              >
                <option value="">全部状态</option>
                <option value="active">持续异常</option>
                <option value="recovered">已恢复</option>
              </select>
            </label>
            <label>
              人工确认
              <select
                aria-label="告警筛选确认"
                value={queue.draft.acknowledged || ""}
                onChange={(e) =>
                  queue.setDraft({
                    ...queue.draft,
                    acknowledged: e.target.value,
                  })
                }
              >
                <option value="">全部确认情况</option>
                <option value="false">未确认</option>
                <option value="true">已确认</option>
              </select>
            </label>
            <label>
              规则版本
              <select
                aria-label="告警筛选规则"
                value={queue.draft.kind}
                onChange={(e) =>
                  queue.setDraft({ ...queue.draft, kind: e.target.value })
                }
              >
                <option value="">全部规则</option>
                {rules.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name} / {r.code} V{r.version}
                  </option>
                ))}
              </select>
            </label>
            <label>
              触发开始时间（本地）
              <input
                aria-label="告警触发开始时间"
                type="datetime-local"
                value={queue.draft.start_at || ""}
                onChange={(e) =>
                  queue.setDraft({ ...queue.draft, start_at: e.target.value })
                }
              />
            </label>
            <label>
              触发结束时间（不含）
              <input
                aria-label="告警触发结束时间"
                type="datetime-local"
                value={queue.draft.end_at || ""}
                onChange={(e) =>
                  queue.setDraft({ ...queue.draft, end_at: e.target.value })
                }
              />
            </label>
            <label>
              告警类型
              <select
                aria-label="告警筛选类型"
                value={queue.draft.category || ""}
                onChange={(e) =>
                  queue.setDraft({
                    ...queue.draft,
                    category: e.target.value as AlertCategory | "",
                  })
                }
              >
                <option value="">全部类型</option>
                <option value="station_metric">场站指标告警</option>
                <option value="device_charging">设备充电告警</option>
                <option value="device_offline">设备离线告警</option>
                <option value="device_fault">设备故障告警</option>
                <option value="energy_power">电量功率异常</option>
                <option value="unconnected_device">未接入设备告警</option>
                <option value="unknown">旧版未分类</option>
              </select>
            </label>
            <button className="primary" disabled={busy || queue.loading}>
              查询告警记录
            </button>
            <button
              className="outline"
              type="button"
              disabled={busy || queue.loading}
              onClick={() => {
                setDetail(null);
                queue.reset();
              }}
            >
              重置告警筛选
            </button>
          </form>
          {canExport && (
            <div className="alert-config-pagination">
              <button
                className="outline"
                disabled={exporting || queue.loading || !!queue.error}
                onClick={exportEvents}
              >
                {exporting ? "正在导出…" : "导出告警CSV"}
              </button>
              <span className="muted">
                使用已查询条件，最多两个自然月、10000条；不含接收人手机号。
              </span>
            </div>
          )}
          {exportError && (
            <p role="alert" className="error">
              {exportError}
            </p>
          )}
          <p className="muted application-list-note">
            按首次触发时间筛选，开始包含、结束不含。人工确认不代表设备恢复；缺少来源的类型不推断为正常。
          </p>
          {queue.loading && (
            <p role="status" className="empty">
              正在加载告警记录…
            </p>
          )}
          {queue.error && (
            <p role="alert" className="error">
              {queue.error}
            </p>
          )}
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {[
                    "事件编号",
                    "监测对象",
                    "场站 / 企业",
                    "状态",
                    "累计异常样本",
                    "最后值",
                    "最近观测",
                    "操作",
                  ].map((h) => (
                    <th key={h}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {events.map((e) => (
                  <tr key={e.id}>
                    <td>ALERT-{e.id}</td>
                    <td>{e.target}</td>
                    <td>
                      {e.station_name}
                      <br />
                      <small>
                        {e.operator_name} / #{e.station_id}
                      </small>
                    </td>
                    <td>
                      <em className={e.status === "active" ? "amber" : "green"}>
                        {e.status === "active" ? "持续异常" : "已恢复"}
                      </em>
                      {e.acknowledged_at && <span>已人工确认</span>}
                    </td>
                    <td>{e.occurrences}</td>
                    <td>
                      {e.last_value} {e.rule_snapshot.unit}
                    </td>
                    <td>
                      {new Date(e.last_observed_at).toLocaleString("zh-CN")}
                    </td>
                    <td>
                      <button
                        className="outline"
                        disabled={busy}
                        onClick={() =>
                          run(async () => {
                            setDetail(await readDetail(e.id));
                            setNote("");
                          })
                        }
                      >
                        告警详情
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!events.length && !queue.loading && !queue.error && (
              <p className="empty">当前条件下没有告警记录</p>
            )}
          </div>
          <QueuePagination queue={queue} label="告警" busy={busy} />
          {detail && (
            <div className="application-detail">
              <div className="panel-title">
                <div>
                  <h2>
                    ALERT-{detail.id} · {detail.target}
                  </h2>
                  <p>
                    规则 {detail.rule_snapshot.code} V
                    {detail.rule_snapshot.version} · 触发
                    {detail.rule_snapshot.configuration?.direction === "lte"
                      ? "≤"
                      : "≥"}
                    {detail.rule_snapshot.trigger} / 恢复
                    {detail.rule_snapshot.configuration?.direction === "lte"
                      ? "≥"
                      : "≤"}
                    {detail.rule_snapshot.recover}
                  </p>
                </div>
                <button
                  className="outline"
                  disabled={busy}
                  onClick={() => setDetail(null)}
                >
                  关闭告警详情
                </button>
              </div>
              {detail.configuration_evidence && (
                <ConfiguredEventEvidence
                  evidence={detail.configuration_evidence}
                  active={detail.status === "active"}
                />
              )}
              <div className="detail-grid">
                <div>
                  <h3>发生与恢复</h3>
                  <p>
                    发生：{new Date(detail.opened_at).toLocaleString("zh-CN")}
                  </p>
                  <p>
                    恢复：
                    {detail.recovered_at
                      ? new Date(detail.recovered_at).toLocaleString("zh-CN")
                      : "尚未恢复"}
                  </p>
                  <h3>站内通知记录</h3>
                  {detail.notices?.map((n, i) => (
                    <article className="timeline" key={i}>
                      <b>{n.message}</b>
                      <p>{new Date(n.created_at).toLocaleString("zh-CN")}</p>
                    </article>
                  ))}
                  {!detail.notices?.length && (
                    <p className="muted">未生成站内通知</p>
                  )}
                </div>
                <div>
                  {detail.acknowledged_at ? (
                    <>
                      <h3>人工确认依据</h3>
                      <p>{detail.acknowledge_note}</p>
                    </>
                  ) : (
                    canAcknowledge &&
                    role === "regulator" && (
                      <>
                        <label>
                          确认依据
                          <textarea
                            aria-label="告警确认依据"
                            disabled={busy}
                            value={note}
                            onChange={(e) => setNote(e.target.value)}
                            maxLength={2000}
                          />
                        </label>
                        <button
                          className="outline"
                          disabled={busy || !note.trim()}
                          onClick={() =>
                            run(async () => {
                              const changed = await typedApi.POST(
                                "/api/v1/alerts/{event_id}/acknowledge",
                                {
                                  params: { path: { event_id: detail.id } },
                                  headers: { "x-csrf-token": csrf },
                                  body: { version: detail.version, note },
                                },
                              );
                              if (!changed.data)
                                throw new Error(
                                  apiError(changed.error, "告警确认失败"),
                                );
                              await load();
                              setDetail(await readDetail(detail.id));
                              setNote("");
                            })
                          }
                        >
                          确认已查看告警
                        </button>
                      </>
                    )
                  )}
                </div>
              </div>
            </div>
          )}
        </section>
      </div>
      <div
        id={`${workspaceId}-rules`}
        className="alert-workspace-pane business-stack"
        hidden={currentView !== "rules"}
      >
        <AlertConfiguration role={role} csrf={csrf} />
        <section className="panel">
          <div className="panel-title">
            <div>
              <h2>告警规则</h2>
              <p>演示阈值规则 · 真实故障、离线及异常指标依赖客户运维接口</p>
            </div>
            {(canRules || canSamples) && role === "regulator" && (
              <div className="action-row">
                {canRules && globalScope && (
                  <button
                    className="outline"
                    onClick={() => setShowRules(!showRules)}
                  >
                    新增规则版本
                  </button>
                )}
                {canSamples && (
                  <button
                    className="outline"
                    onClick={() => setShowSamples(!showSamples)}
                  >
                    演示样本验证
                  </button>
                )}
              </div>
            )}
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {[
                    "规则名称",
                    "指标 / 单位",
                    "触发阈值 ≥",
                    "恢复阈值 ≤",
                    "站内通知",
                  ].map((h) => (
                    <th key={h}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rules.map((r) => (
                  <tr key={r.id}>
                    <td>
                      {r.name}
                      <span>
                        {r.code} / V{r.version}
                      </span>
                    </td>
                    <td>
                      {r.metric} / {r.unit}
                    </td>
                    <td>{r.trigger_value}</td>
                    <td>{r.recover_value}</td>
                    <td>{r.notifications_enabled ? "开启" : "关闭"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!rules.length && <p className="empty">暂无告警规则</p>}
          </div>
          {showRules && canRules && globalScope && role === "regulator" && (
            <form
              className="business-form"
              onSubmit={(e) => {
                e.preventDefault();
                const data = new FormData(e.currentTarget);
                run(async () => {
                  read(
                    await typedApi.POST("/api/v1/alerts/rules", {
                      headers: { "x-csrf-token": csrf },
                      body: {
                        code: String(data.get("code") ?? ""),
                        version: Number(data.get("version")),
                        name: String(data.get("name") ?? ""),
                        metric: String(data.get("metric") ?? ""),
                        unit: String(data.get("unit") ?? ""),
                        trigger_value: String(data.get("trigger") ?? ""),
                        recover_value: String(data.get("recover") ?? ""),
                        notifications_enabled: data.has("notifications"),
                      },
                    }),
                  );
                  const d = read(
                    await typedApi.GET("/api/v1/alerts/rules", {
                      cache: "no-store",
                    }),
                  );
                  setRules(d.items);
                  setShowRules(false);
                });
              }}
            >
              {[
                ["code", "规则代码", "text"],
                ["version", "版本", "number"],
                ["name", "规则名称", "text"],
                ["metric", "指标代码", "text"],
                ["unit", "单位", "text"],
                ["trigger", "触发阈值", "number"],
                ["recover", "恢复阈值", "number"],
              ].map(([key, label, type]) => (
                <label key={key}>
                  {label}
                  <input
                    name={key}
                    required
                    type={type}
                    step={type === "number" ? "any" : undefined}
                    maxLength={key === "name" ? 150 : 60}
                  />
                </label>
              ))}
              <label>
                生成站内通知
                <input type="checkbox" name="notifications" defaultChecked />
              </label>
              <button className="outline" disabled={busy}>
                保存规则版本
              </button>
            </form>
          )}
          {showSamples && canSamples && role === "regulator" && (
            <form
              className="business-form"
              onSubmit={(e) => {
                e.preventDefault();
                run(async () => {
                  const d = read(
                    await typedApi.POST("/api/v1/alerts/samples", {
                      headers: { "x-csrf-token": csrf },
                      body: {
                        station_id: Number(sid),
                        rule_id: Number(rid),
                        target,
                        value,
                        observed_at: new Date().toISOString(),
                      },
                    }),
                  );
                  setResult(
                    (
                      {
                        opened: "已创建告警",
                        recovered: "已记录恢复",
                        merged: "已合并持续异常",
                        normal: "正常样本",
                        ignored_stale: "已忽略重复或迟到样本",
                      } as Record<string, string>
                    )[d.outcome],
                  );
                  await load();
                  if (d.outcome === "opened") queue.reset();
                });
              }}
            >
              <StationPicker
                label="告警演示场站"
                value={sid}
                onChange={setSid}
              />
              <label>
                演示规则
                <select
                  aria-label="告警演示规则"
                  value={rid}
                  onChange={(e) => setRid(e.target.value)}
                >
                  {rules.map((r) => (
                    <option value={r.id} key={r.id}>
                      {r.name} V{r.version}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                监测对象
                <input
                  aria-label="告警监测对象"
                  value={target}
                  onChange={(e) => setTarget(e.target.value)}
                  maxLength={100}
                  required
                />
              </label>
              <label>
                样本值
                <input
                  aria-label="告警样本值"
                  type="number"
                  step="any"
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                  required
                />
              </label>
              <button className="outline" disabled={busy || !sid || !rid}>
                提交演示样本
              </button>
              <span role="status">{result}</span>
            </form>
          )}
        </section>
      </div>
      <div
        id={`${workspaceId}-sources`}
        className="alert-workspace-pane business-stack"
        hidden={currentView !== "sources"}
      >
        <AlertPeriodFacts />
        <ConfiguredObservations role={role} csrf={csrf} onApplied={load} />
      </div>
      {manageNotifications && (
        <div
          id={`${workspaceId}-notifications`}
          className="alert-workspace-pane business-stack"
          hidden={currentView !== "notifications"}
        >
          <AlertNotifications csrf={csrf} />
        </div>
      )}
    </div>
  );
}
