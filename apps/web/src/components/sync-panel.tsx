"use client";
import { usePageRefresh } from "./page-refresh";
import { apiFetch } from "../lib/api-fetch";
import { useCapability } from "./permissions";
import { useEffect, useState, useRef } from "react";
type Task = {
  id: number;
  source: string;
  status: string;
  operator_id: number;
  attempts: number;
  error: string | null;
  result: Record<string, number>;
  created_at: string;
};
const statuses: Record<string, string> = {
  pending: "等待执行",
  running: "执行中",
  retry: "等待重试",
  failed: "执行失败",
  succeeded: "同步完成",
};
export function SyncPanel({ csrf }: { csrf: string }) {
  const canWrite = useCapability("sync.write");
  const [tasks, setTasks] = useState<Task[]>([]),
    [operators, setOperators] = useState<{ id: number; name: string }[]>([]),
    [oid, setOid] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const loadSequence = useRef(0);
  async function load() {
    const sequence = ++loadSequence.current;
    try {
      const r = await apiFetch("/api/v1/sync/tasks", { cache: "no-store" });
      if (!r.ok) throw new Error("同步任务加载失败");
      const data = await r.json();
      if (sequence === loadSequence.current) setTasks(data.items);
    } catch (e) {
      if (sequence === loadSequence.current)
        setError(e instanceof Error ? e.message : "请求失败");
    }
  }
  const pageRefreshRevision = usePageRefresh();
  useEffect(() => {
    let active = true;
    apiFetch("/api/v1/operators")
      .then(async (r) => {
        if (!r.ok) throw new Error("运营商加载失败");
        const data = await r.json();
        if (active) {
          setOperators(data.items);
          if (data.items[0])
            setOid((current) => current || String(data.items[0].id));
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    load();
    const timer = setInterval(load, 5000);
    return () => {
      active = false;
      ++loadSequence.current;
      clearInterval(timer);
    };
  }, [pageRefreshRevision]);
  async function submit() {
    setBusy(true);
    setError("");
    try {
      const r = await apiFetch("/api/v1/sync/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-csrf-token": csrf },
        body: JSON.stringify({
          operator_id: Number(oid),
          request_key: crypto.randomUUID(),
        }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || "创建失败");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "请求失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel">
      <div className="panel-title">
        <div>
          <h2>数据同步任务</h2>
          <p>
            演示适配器 · 任务持久保存，由独立 Worker 执行；每5秒更新任务状态
          </p>
        </div>
        <div className="search">
          {canWrite && (
            <>
              <select
                aria-label="同步运营商"
                value={oid}
                onChange={(e) => setOid(e.target.value)}
              >
                {operators.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name}
                  </option>
                ))}
              </select>
              <button disabled={busy || !oid} onClick={submit}>
                {busy ? "正在提交…" : "发起模拟同步"}
              </button>
            </>
          )}
          <button onClick={load}>刷新</button>
        </div>
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
                "任务编号",
                "来源",
                "运营商ID",
                "状态",
                "尝试次数",
                "处理结果 / 错误",
                "创建时间",
              ].map((h) => (
                <th key={h}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {tasks.map((t) => (
              <tr key={t.id}>
                <td>SYNC-{t.id}</td>
                <td>{t.source === "demo" ? "模拟数据" : t.source}</td>
                <td>{t.operator_id}</td>
                <td>
                  <em className={t.status === "succeeded" ? "green" : "amber"}>
                    {statuses[t.status] || t.status}
                  </em>
                </td>
                <td>{t.attempts}</td>
                <td>
                  {t.error ||
                    Object.entries(t.result)
                      .map(([k, v]) => `${k}: ${v}`)
                      .join(" / ") ||
                    "等待Worker处理"}
                </td>
                <td>{new Date(t.created_at).toLocaleString("zh-CN")}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!tasks.length && <p className="empty">尚未发起同步任务</p>}
      </div>
    </section>
  );
}
