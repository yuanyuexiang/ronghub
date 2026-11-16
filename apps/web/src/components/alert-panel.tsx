"use client";
import { usePageRefresh } from "./page-refresh";
import {
  useBusinessQueue,
  loadAlertQueue,
  QueuePagination,
} from "./business-queue";
import { apiFetch } from "../lib/api-fetch";
import { useCapability, useGlobalScope } from "./permissions";
import { StationPicker } from "./station-picker";
import { useEffect, useState } from "react";
type Rule = {
  id: number;
  code: string;
  version: number;
  name: string;
  metric: string;
  unit: string;
  trigger_value: string;
  recover_value: string;
  notifications_enabled: boolean;
};
type Event = {
  id: number;
  station_id: number;
  target: string;
  status: string;
  opened_at: string;
  last_observed_at: string;
  recovered_at: string | null;
  occurrences: number;
  last_value: string;
  rule_snapshot: {
    code: string;
    version: number;
    metric: string;
    unit: string;
    trigger: string;
    recover: string;
  };
  version: number;
  acknowledged_at: string | null;
  acknowledge_note: string;
  notices?: {
    phase: string;
    message: string;
    created_at: string;
    channel: string;
  }[];
};
export function AlertPanel({ role, csrf }: { role: string; csrf: string }) {
  const globalScope = useGlobalScope();
  const canRules = useCapability("alerts.rules");
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
  async function request(url: string, body?: unknown, signal?: AbortSignal) {
    const r = await apiFetch(
      url,
      body
        ? {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "x-csrf-token": csrf,
            },
            body: JSON.stringify(body),
          }
        : { cache: "no-store", signal },
    );
    const d = await r.json();
    if (!r.ok)
      throw new Error(
        typeof d.detail === "string" ? d.detail : "请检查填写内容",
      );
    return d;
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
    request("/api/v1/alerts/rules", undefined, controller.signal)
      .then((d) => {
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
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
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
                await request("/api/v1/alerts/rules", {
                  code: data.get("code"),
                  version: Number(data.get("version")),
                  name: data.get("name"),
                  metric: data.get("metric"),
                  unit: data.get("unit"),
                  trigger_value: data.get("trigger"),
                  recover_value: data.get("recover"),
                  notifications_enabled: data.has("notifications"),
                });
                const d = await request("/api/v1/alerts/rules");
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
                const d = await request("/api/v1/alerts/samples", {
                  station_id: Number(sid),
                  rule_id: Number(rid),
                  target,
                  value,
                  observed_at: new Date().toISOString(),
                });
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
            <StationPicker label="告警演示场站" value={sid} onChange={setSid} />
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
      <section className="panel alert-queue">
        <div className="panel-title">
          <div>
            <h2>{role === "regulator" ? "告警事件跟踪" : "企业告警事件"}</h2>
            <p>
              持续异常合并，恢复后留档；人工确认不代表设备已恢复。通知仅在站内展示。
            </p>
          </div>
          <button className="outline" disabled={busy} onClick={() => run(load)}>
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
                queue.setDraft({ ...queue.draft, acknowledged: e.target.value })
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
        <p className="muted application-list-note">
          按首次触发时间筛选，开始包含、结束不含。人工确认不代表设备恢复；名称为当前台账，刷新保留筛选条件。
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
                          setDetail(await request("/api/v1/alerts/" + e.id));
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
                  {detail.rule_snapshot.version} · 触发≥
                  {detail.rule_snapshot.trigger} / 恢复≤
                  {detail.rule_snapshot.recover}
                </p>
              </div>
              <button className="outline" onClick={() => setDetail(null)}>
                关闭告警详情
              </button>
            </div>
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
                            await request(
                              `/api/v1/alerts/${detail.id}/acknowledge`,
                              { version: detail.version, note },
                            );
                            await load();
                            setDetail(
                              await request("/api/v1/alerts/" + detail.id),
                            );
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
  );
}
