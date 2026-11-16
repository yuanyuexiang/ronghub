"use client";
import {
  useBusinessQueue,
  loadInspectionQueue,
  QueuePagination,
} from "./business-queue";
import { apiFetch } from "../lib/api-fetch";
import { useCapability } from "./permissions";
import { StationPicker } from "./station-picker";
import { useState } from "react";
import { InspectionMaterials } from "./inspection-materials";
type Task = {
  id: number;
  title: string;
  description: string;
  station_id: number;
  severity: string;
  due_at: string;
  status: string;
  version: number;
  overdue: boolean;
  history?: {
    id: number;
    action: string;
    comment: string;
    created_at: string;
  }[];
};
const states: Record<string, string> = {
  assigned: "待整改",
  replied: "待复核",
  returned: "退回补充",
  closed: "已关闭",
};
const actions: Record<string, string> = {
  assign: "下发任务",
  reply: "整改回复",
  return: "退回补充",
  close: "复核关闭",
};
export function InspectionPanel({
  role,
  csrf,
}: {
  role: string;
  csrf: string;
}) {
  const canAssign = useCapability("inspections.assign");
  const canMaterials = useCapability("inspections.materials");
  const canReply = useCapability("inspections.reply");
  const canReturn = useCapability("inspections.return");
  const canClose = useCapability("inspections.close");
  const queue = useBusinessQueue(loadInspectionQueue);
  const tasks = queue.rows;
  const [sid, setSid] = useState(""),
    [title, setTitle] = useState(""),
    [description, setDescription] = useState(""),
    [severity, setSeverity] = useState("normal"),
    [due, setDue] = useState(""),
    [detail, setDetail] = useState<Task | null>(null),
    [comment, setComment] = useState(""),
    [busy, setBusy] = useState(false),
    [materialBusy, setMaterialBusy] = useState(false),
    [error, setError] = useState("");
  async function request(url: string, body?: unknown) {
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
        : { cache: "no-store" },
    );
    const d = await r.json();
    if (!r.ok)
      throw new Error(
        typeof d.detail === "string" ? d.detail : "请检查输入内容",
      );
    return d;
  }
  async function load() {
    await queue.reload();
  }
  async function run(fn: () => Promise<void>) {
    if (busy || materialBusy) return;
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }
  async function act(action: string) {
    if (!detail || busy || materialBusy) return;
    await run(async () => {
      await request(`/api/v1/inspections/${detail.id}/actions`, {
        action,
        version: detail.version,
        comment,
      });
      await load();
      setDetail(await request("/api/v1/inspections/" + detail.id));
      setComment("");
    });
  }
  return (
    <div className="business-stack">
      {canAssign && role === "regulator" && (
        <section className="panel">
          <div className="panel-title">
            <div>
              <h2>下发巡检督办</h2>
              <p>演示闭环规则 · 任务按场站归属定向到企业；重大隐患醒目标记</p>
            </div>
          </div>
          <form
            className="business-form"
            onSubmit={(e) => {
              e.preventDefault();
              run(async () => {
                const task = await request("/api/v1/inspections", {
                  station_id: Number(sid),
                  title,
                  description,
                  severity,
                  due_at: new Date(due).toISOString(),
                });
                queue.reset();
                setDetail(await request("/api/v1/inspections/" + task.id));
                setTitle("");
                setDescription("");
              });
            }}
          >
            <StationPicker label="巡检场站" value={sid} onChange={setSid} />
            <label>
              任务标题
              <input
                aria-label="巡检标题"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                maxLength={150}
                required
              />
            </label>
            <label>
              隐患等级
              <select
                value={severity}
                onChange={(e) => setSeverity(e.target.value)}
              >
                <option value="normal">一般巡检</option>
                <option value="major">重大隐患挂牌督办</option>
              </select>
            </label>
            <label>
              整改期限（本地时间）
              <input
                aria-label="巡检期限"
                type="datetime-local"
                value={due}
                onChange={(e) => setDue(e.target.value)}
                required
              />
            </label>
            <label>
              任务要求
              <input
                aria-label="巡检要求"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                maxLength={4000}
                required
              />
            </label>
            <button className="outline" disabled={busy || materialBusy || !sid}>
              下发任务
            </button>
          </form>
        </section>
      )}
      <section className="panel">
        <div className="panel-title">
          <div>
            <h2>{role === "operator" ? "企业整改任务" : "巡检任务跟踪"}</h2>
            <p>逾期按服务器当前时间计算，保留回复与复核历史</p>
          </div>
          <button
            className="outline"
            onClick={() => run(load)}
            disabled={busy || materialBusy}
          >
            刷新任务
          </button>
        </div>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <form
          className="application-filters"
          onSubmit={(event) => {
            event.preventDefault();
            if (busy || materialBusy) return;
            setDetail(null);
            queue.apply();
          }}
        >
          <label>
            任务、场站或企业
            <input
              aria-label="巡检检索名称"
              maxLength={150}
              value={queue.draft.q}
              onChange={(e) =>
                queue.setDraft({ ...queue.draft, q: e.target.value })
              }
            />
          </label>
          <label>
            任务编号
            <input
              aria-label="巡检检索编号"
              type="number"
              min="1"
              value={queue.draft.record_id}
              onChange={(e) =>
                queue.setDraft({ ...queue.draft, record_id: e.target.value })
              }
            />
          </label>
          <label>
            办理状态
            <select
              aria-label="巡检筛选状态"
              value={queue.draft.status}
              onChange={(e) =>
                queue.setDraft({ ...queue.draft, status: e.target.value })
              }
            >
              <option value="">全部状态</option>
              {Object.entries(states).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            隐患等级
            <select
              aria-label="巡检筛选等级"
              value={queue.draft.kind}
              onChange={(e) =>
                queue.setDraft({ ...queue.draft, kind: e.target.value })
              }
            >
              <option value="">全部等级</option>
              <option value="normal">一般巡检</option>
              <option value="major">重大隐患</option>
            </select>
          </label>
          <label>
            逾期情况
            <select
              aria-label="巡检筛选逾期"
              value={queue.draft.overdue || ""}
              onChange={(e) =>
                queue.setDraft({ ...queue.draft, overdue: e.target.value })
              }
            >
              <option value="">全部任务</option>
              <option value="true">仅逾期</option>
              <option value="false">非逾期（含已关闭）</option>
            </select>
          </label>
          <button
            className="primary"
            disabled={busy || materialBusy || queue.loading}
          >
            查询巡检记录
          </button>
          <button
            type="button"
            className="outline"
            disabled={busy || materialBusy || queue.loading}
            onClick={() => {
              setDetail(null);
              queue.reset();
            }}
          >
            重置巡检筛选
          </button>
        </form>
        <p className="muted application-list-note">
          当前台账名称；新建返回全部第一页。逾期按查询时刻判断，已关闭任务不记逾期；请刷新查询以更新时间状态。
        </p>
        {queue.error && (
          <p role="alert" className="error">
            {queue.error}
          </p>
        )}
        {queue.loading && (
          <p role="status" className="empty">
            正在加载巡检任务…
          </p>
        )}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {[
                  "任务编号",
                  "任务标题",
                  "场站 / 企业",
                  "隐患等级",
                  "整改期限",
                  "状态",
                  "操作",
                ].map((h) => (
                  <th key={h}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tasks.map((t) => (
                <tr key={t.id}>
                  <td>TASK-{t.id}</td>
                  <td>{t.title}</td>
                  <td>
                    {t.station_name}
                    <br />
                    <small>
                      {t.operator_name} / #{t.station_id}
                    </small>
                  </td>
                  <td>
                    <em className={t.severity === "major" ? "amber" : "green"}>
                      {t.severity === "major" ? "重大挂牌" : "一般巡检"}
                    </em>
                  </td>
                  <td>{new Date(t.due_at).toLocaleString("zh-CN")}</td>
                  <td>
                    {states[t.status]}
                    {t.overdue && <em className="amber">已逾期</em>}
                  </td>
                  <td>
                    <button
                      className="outline"
                      disabled={busy || materialBusy}
                      onClick={() =>
                        run(async () => {
                          setDetail(
                            await request("/api/v1/inspections/" + t.id),
                          );
                          setComment("");
                        })
                      }
                    >
                      处理任务
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!tasks.length && !queue.loading && !queue.error && (
            <p className="empty">当前条件下没有巡检记录</p>
          )}
        </div>
        <QueuePagination
          queue={queue}
          label="巡检"
          busy={busy || materialBusy}
        />
        {detail && (
          <div className="application-detail">
            <div className="panel-title">
              <div>
                <h2>
                  TASK-{detail.id} · {detail.title}
                </h2>
                <p>
                  {states[detail.status]} · {detail.description}
                </p>
              </div>
              <button
                className="outline"
                disabled={busy || materialBusy}
                onClick={() => setDetail(null)}
              >
                关闭任务详情
              </button>
            </div>
            <div className="detail-grid">
              <div>
                <InspectionMaterials
                  key={detail.id}
                  taskId={detail.id}
                  version={detail.version}
                  csrf={csrf}
                  parentBusy={busy}
                  onBusy={setMaterialBusy}
                  editable={
                    canMaterials &&
                    role === "operator" &&
                    ["assigned", "returned"].includes(detail.status)
                  }
                />
                <h3>处理记录</h3>
                {detail.history?.map((a) => (
                  <article key={a.id} className="timeline">
                    <b>{actions[a.action]}</b>
                    <p>{a.comment}</p>
                    <small>
                      {new Date(a.created_at).toLocaleString("zh-CN")}
                    </small>
                  </article>
                ))}
              </div>
              <div>
                {detail.status === "closed" ? (
                  <p className="muted">任务已关闭，历史记录保留。</p>
                ) : (role === "operator" &&
                    canReply &&
                    ["assigned", "returned"].includes(detail.status)) ||
                  (role === "regulator" &&
                    (canReturn || canClose) &&
                    detail.status === "replied") ? (
                  <>
                    <label>
                      整改或复核说明
                      <textarea
                        aria-label="巡检处理说明"
                        disabled={busy || materialBusy}
                        value={comment}
                        onChange={(e) => setComment(e.target.value)}
                        maxLength={4000}
                      />
                    </label>
                    <div className="action-row">
                      {canReply &&
                        role === "operator" &&
                        ["assigned", "returned"].includes(detail.status) && (
                          <button
                            className="outline"
                            disabled={busy || materialBusy || !comment.trim()}
                            onClick={() => act("reply")}
                          >
                            提交整改回复
                          </button>
                        )}
                      {(canReturn || canClose) &&
                        role === "regulator" &&
                        detail.status === "replied" && (
                          <>
                            {canReturn && (
                              <button
                                className="outline"
                                disabled={
                                  busy || materialBusy || !comment.trim()
                                }
                                onClick={() => act("return")}
                              >
                                退回补充整改
                              </button>
                            )}
                            {canClose && (
                              <button
                                className="outline"
                                disabled={
                                  busy || materialBusy || !comment.trim()
                                }
                                onClick={() => act("close")}
                              >
                                复核关闭任务
                              </button>
                            )}
                          </>
                        )}
                    </div>
                  </>
                ) : (
                  <p className="muted">
                    当前状态无可办理动作，可查阅任务和历史证据。
                  </p>
                )}
              </div>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
