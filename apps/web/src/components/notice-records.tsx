"use client";
import { usePageRefresh } from "./page-refresh";
import { useCapability } from "./permissions";
import { useEffect, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";

const states = {
  scheduled: "定时待生效",
  visible: "站内可见",
  withdrawn: "已撤下",
};
export function NoticeRecords({
  manage,
  onOpen,
  refreshKey = 0,
  defaultExpanded = false,
}: {
  manage: boolean;
  refreshKey?: number;
  defaultExpanded?: boolean;
  onOpen: (id: number) => void;
}) {
  const canExport = useCapability("content.export");
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [draft, setDraft] = useState({ q: "", state: "", replied: "" });
  const [query, setQuery] = useState({ ...draft, page: 1 });
  const [data, setData] = useState<ApiSchemas["NoticeRecordPage"] | null>(null);
  const [loading, setLoading] = useState(false),
    [error, setError] = useState("");
  const pageRefreshRevision = usePageRefresh();
  useEffect(() => {
    if (!expanded) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setData(null);
    typedApi
      .GET("/api/v1/content/notice-records", {
        params: {
          query: {
            manage,
            q: query.q,
            state: query.state,
            replied: query.replied ? query.replied === "true" : undefined,
            page: query.page,
            page_size: 20,
          },
        },
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "通知记录读取失败"));
        const last = Math.max(1, Math.ceil(data.total / 20));
        if (query.page > last) {
          setQuery((current) => ({ ...current, page: last }));
          return;
        }
        setData(data);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [manage, expanded, query, refreshKey, pageRefreshRevision]);
  return (
    <section className="panel notice-records">
      <div className="panel-title">
        <div>
          <h2>逐企业通知记录</h2>
          <p>
            只显示明确选定企业后发布的记录；站内可见不代表短信、邮件送达或人工已读。
          </p>
        </div>
        <button
          type="button"
          className="outline"
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? "收起通知记录" : "展开通知记录"}
        </button>
      </div>
      {expanded && (
        <>
          <form
            className="application-filters"
            onSubmit={(e) => {
              e.preventDefault();
              setQuery({ ...draft, page: 1 });
            }}
          >
            <label>
              通知或企业
              <input
                aria-label="通知记录关键词"
                value={draft.q}
                maxLength={150}
                onChange={(e) => setDraft({ ...draft, q: e.target.value })}
              />
            </label>
            <label>
              站内状态
              <select
                aria-label="通知记录状态"
                value={draft.state}
                onChange={(e) => setDraft({ ...draft, state: e.target.value })}
              >
                <option value="">全部状态</option>
                {Object.entries(states).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              企业回复
              <select
                aria-label="通知记录回复"
                value={draft.replied}
                onChange={(e) =>
                  setDraft({ ...draft, replied: e.target.value })
                }
              >
                <option value="">全部回复情况</option>
                <option value="true">已有回复</option>
                <option value="false">尚未回复</option>
              </select>
            </label>
            <button className="primary" disabled={loading}>
              查询通知记录
            </button>
          </form>
          {manage && canExport && data && !loading && (
            <a
              className="outline notice-export-link"
              href={`/api/v1/content/notice-records/export?${new URLSearchParams({ q: query.q, state: query.state, ...(query.replied ? { replied: query.replied } : {}) })}`}
            >
              导出当前查询通知记录
            </a>
          )}
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          {loading && <p role="status">正在读取通知记录…</p>}
          {data && (
            <>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>记录 / 通知</th>
                      <th>收件企业快照</th>
                      <th>计划生效时间（北京时间）</th>
                      <th>站内状态</th>
                      <th>企业回复</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((row) => (
                      <tr key={row.id}>
                        <td>
                          #{row.id}
                          <small>{row.title}</small>
                        </td>
                        <td>{row.operator_name}</td>
                        <td>
                          {new Date(row.scheduled_at).toLocaleString("zh-CN", {
                            timeZone: "Asia/Shanghai",
                          })}
                        </td>
                        <td>{states[row.state]}</td>
                        <td>{row.reply_count}条</td>
                        <td>
                          <button
                            type="button"
                            className="outline"
                            onClick={() => onOpen(row.article_id)}
                          >
                            查看通知正文
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!data.total && <p className="empty">暂无符合条件的逐企业记录</p>}
              <div className="pagination">
                <button
                  type="button"
                  className="outline"
                  disabled={loading || query.page <= 1}
                  onClick={() => setQuery({ ...query, page: query.page - 1 })}
                >
                  上一页通知记录
                </button>
                <span>
                  第{query.page}页 · 共{data.total}条
                </span>
                <button
                  type="button"
                  className="outline"
                  disabled={loading || query.page * 20 >= data.total}
                  onClick={() => setQuery({ ...query, page: query.page + 1 })}
                >
                  下一页通知记录
                </button>
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
}
