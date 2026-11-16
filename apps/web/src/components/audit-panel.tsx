"use client";
import { usePageRefresh } from "./page-refresh";
import { FormEvent, useEffect, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";

type Result = ApiSchemas["AuditPage"];
type Filters = {
  actor: string;
  action: string;
  object_id: string;
  start_at: string;
  end_at: string;
};
const empty: Filters = {
  actor: "",
  action: "",
  object_id: "",
  start_at: "",
  end_at: "",
};
export function AuditPanel() {
  const [draft, setDraft] = useState(empty);
  const [query, setQuery] = useState({ filters: empty, page: 1 });
  const [data, setData] = useState<Result | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  const pageRefreshRevision = usePageRefresh();
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    setError("");
    setData(null);
    typedApi
      .GET("/api/v1/audits", {
        params: {
          query: {
            page: query.page,
            page_size: 20,
            actor: query.filters.actor.trim(),
            action: query.filters.action.trim(),
            object_id: query.filters.object_id
              ? Number(query.filters.object_id)
              : undefined,
            start_at: query.filters.start_at
              ? new Date(query.filters.start_at).toISOString()
              : undefined,
            end_at: query.filters.end_at
              ? new Date(query.filters.end_at).toISOString()
              : undefined,
          },
        },
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data: body, error }) => {
        if (!body) throw new Error(apiError(error, "日志查询失败"));
        if (!controller.signal.aborted) setData(body);
      })
      .catch((err) => {
        if (!controller.signal.aborted)
          setError(err instanceof Error ? err.message : "日志加载失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [query, pageRefreshRevision]);
  function submit(event: FormEvent) {
    event.preventDefault();
    if (
      draft.start_at &&
      draft.end_at &&
      new Date(draft.start_at) >= new Date(draft.end_at)
    ) {
      setError("结束时间须晚于开始时间");
      return;
    }
    setQuery({ filters: { ...draft }, page: 1 });
  }
  const input = (key: keyof Filters, label: string, type = "text") => (
    <label>
      {label}
      <input
        aria-label={label}
        type={type}
        min={type === "number" ? 1 : undefined}
        maxLength={100}
        value={draft[key]}
        onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
      />
    </label>
  );
  return (
    <section className="panel audit-panel">
      <h2>操作日志</h2>
      <p className="muted">
        全域监管审计查询。人员账号与动作代码精确匹配；时间按本机时区输入，列表统一显示北京时间。结束时刻不包含在查询范围内。
      </p>
      <form onSubmit={submit} className="audit-filters">
        {input("actor", "操作人账号")}
        {input("action", "动作代码")}
        {input("object_id", "业务对象编号", "number")}
        {input("start_at", "开始时间", "datetime-local")}
        {input("end_at", "结束时间", "datetime-local")}
        <button className="primary" disabled={busy}>
          查询日志
        </button>
        <button
          type="button"
          className="outline"
          disabled={busy}
          onClick={() => {
            setDraft(empty);
            setQuery({ filters: empty, page: 1 });
          }}
        >
          重置条件
        </button>
      </form>
      {error && <p role="alert">{error}</p>}
      {busy && <p role="status">正在加载日志…</p>}
      {data && (
        <>
          <p className="muted">{data.notice}</p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>时间（北京）</th>
                  <th>操作人</th>
                  <th>动作 / 对象</th>
                  <th>记录结果</th>
                  <th>安全详情</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr key={item.id}>
                    <td>
                      {new Intl.DateTimeFormat("zh-CN", {
                        timeZone: "Asia/Shanghai",
                        dateStyle: "short",
                        timeStyle: "medium",
                      }).format(new Date(item.created_at))}
                    </td>
                    <td>{item.actor}</td>
                    <td>
                      {item.action}
                      <br />
                      对象：{item.object_id ?? "—"} · 日志 #{item.id}
                    </td>
                    <td>已提交成功</td>
                    <td>
                      {Object.keys(item.detail).length ? (
                        <details>
                          <summary>查看结构化详情</summary>
                          <pre
                            style={{
                              maxWidth: 360,
                              maxHeight: 320,
                              overflow: "auto",
                              whiteSpace: "pre-wrap",
                              overflowWrap: "anywhere",
                            }}
                          >
                            {JSON.stringify(item.detail, null, 2)}
                          </pre>
                        </details>
                      ) : (
                        "无可展示字段"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!data.items.length && <p>当前条件下没有操作日志</p>}
          <div className="action-row">
            <button
              className="outline"
              disabled={busy || query.page === 1}
              onClick={() => setQuery({ ...query, page: query.page - 1 })}
            >
              日志上一页
            </button>
            <span>
              第{query.page}页 · 共{data.total}条
            </span>
            <button
              className="outline"
              disabled={busy || query.page * 20 >= data.total}
              onClick={() => setQuery({ ...query, page: query.page + 1 })}
            >
              日志下一页
            </button>
          </div>
        </>
      )}
    </section>
  );
}
