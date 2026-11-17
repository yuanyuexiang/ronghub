"use client";
import { usePageRefresh } from "./page-refresh";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { useCapability } from "./permissions";
import { useEffect, useState, useRef } from "react";
type Task = ApiSchemas["SyncTaskItem"];
const statuses: Record<string, string> = {
  pending: "等待执行",
  running: "执行中",
  retry: "等待重试",
  failed: "执行失败",
  succeeded: "同步完成",
};
const countLabels: Record<string, string> = {
  stations: "场站",
  equipment: "设备",
  connectors: "枪口",
  orders: "订单",
  hourly_energy: "分时电量",
  faults: "故障记录",
  runtime_intervals: "运行时段",
  subsidy_device_days: "设备日数据",
};
export function SyncPanel({ csrf }: { csrf: string }) {
  const canWrite = useCapability("sync.write");
  const [tasks, setTasks] = useState<Task[]>([]),
    [operators, setOperators] = useState<ApiSchemas["OperatorList"]["items"]>(
      [],
    ),
    [oid, setOid] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const loadSequence = useRef(0);
  const [pending, setPending] = useState<ApiSchemas["SyncInput"] | null>(null);
  const pendingRef = useRef<ApiSchemas["SyncInput"] | null>(null);
  const inFlight = useRef(false);
  function remember(value: ApiSchemas["SyncInput"] | null) {
    pendingRef.current = value;
    setPending(value);
  }
  async function load() {
    const sequence = ++loadSequence.current;
    try {
      const result = await typedApi.GET("/api/v1/sync/tasks", {
        cache: "no-store",
      });
      if (!result.data)
        throw new Error(apiError(result.error, "同步任务加载失败"));
      if (sequence === loadSequence.current) setTasks(result.data.items);
    } catch (e) {
      if (sequence === loadSequence.current)
        setError(e instanceof Error ? e.message : "请求失败");
    }
  }
  const pageRefreshRevision = usePageRefresh();
  useEffect(() => {
    let active = true;
    typedApi
      .GET("/api/v1/sync/operators", { cache: "no-store" })
      .then((result) => {
        if (!result.data)
          throw new Error(apiError(result.error, "运营商加载失败"));
        const data = result.data;
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
    if (inFlight.current) return;
    inFlight.current = true;
    let received = false;
    setBusy(true);
    setError("");
    try {
      const retry = pendingRef.current !== null;
      const body = pendingRef.current || {
        operator_id: Number(oid),
        request_key: crypto.randomUUID(),
      };
      remember(body);
      const result = await typedApi.POST("/api/v1/sync/tasks", {
        headers: { "x-csrf-token": csrf },
        body,
      });
      received = true;
      if (!result.data) {
        if (
          !retry &&
          result.response.status >= 400 &&
          result.response.status < 500
        )
          remember(null);
        throw new Error(
          apiError(result.error, "提交结果未确认，请按原编号重试或查询任务"),
        );
      }
      remember(null);
      await load();
    } catch (reason) {
      setError(
        received && reason instanceof Error
          ? reason.message
          : "未收到提交回执，请使用原编号重试或查询任务。",
      );
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  async function lookup() {
    if (!pendingRef.current || inFlight.current) return;
    const request = pendingRef.current;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await typedApi.GET("/api/v1/sync/tasks", {
        cache: "no-store",
        params: { query: { request_key: request.request_key } },
      });
      if (!result.data)
        throw new Error(apiError(result.error, "原编号查询失败"));
      const found = result.data.items.find(
        (task) =>
          task.source === "demo" && task.operator_id === request.operator_id,
      );
      if (!found)
        throw new Error(
          "暂未查到匹配任务，可继续使用原编号重试；请勿重复发起。",
        );
      remember(null);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "原编号查询失败");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  return (
    <section className="panel">
      <div className="panel-title">
        <div>
          <h2>数据同步任务</h2>
          <p>模拟数据源 · 任务持久保存，由后台执行；每5秒更新任务状态</p>
        </div>
        <div className="search">
          {canWrite && (
            <>
              <select
                aria-label="同步运营商"
                disabled={busy || !!pending}
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
                {busy
                  ? "正在提交…"
                  : pending
                    ? "按原编号重试同步"
                    : "发起模拟同步"}
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
      {pending && (
        <aside className="sync-request-receipt" aria-label="同步请求回执">
          <p>
            本次请求编号：<code>{pending.request_key}</code> · 运营商 #
            {pending.operator_id}。结果未确认时使用原编号重试或查询。
          </p>
          <div className="action-row">
            <button className="outline" disabled={busy} onClick={lookup}>
              查询原编号任务
            </button>
            <button
              className="outline"
              disabled={busy}
              onClick={() => {
                if (
                  window.confirm(
                    "原任务可能已创建，结束重试不会撤销任务。继续？",
                  )
                ) {
                  remember(null);
                  setError("");
                }
              }}
            >
              结束本次重试
            </button>
          </div>
        </aside>
      )}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              {[
                "任务编号",
                "来源 / 请求编号",
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
                <td>
                  {t.source === "demo" ? "模拟数据" : t.source}
                  <br />
                  <small>{t.request_key}</small>
                </td>
                <td>{t.operator_id}</td>
                <td>
                  <em className={t.status === "succeeded" ? "green" : "amber"}>
                    {statuses[t.status] || t.status}
                  </em>
                </td>
                <td>{t.attempts}</td>
                <td className="sync-result">
                  {t.error ||
                    Object.entries(t.result ?? {})
                      .map(([k, v]) => `${countLabels[k] || k}：${v}`)
                      .join(" / ") ||
                    (["pending", "running", "retry"].includes(t.status)
                      ? "等待后台处理"
                      : "结果未留存")}
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
