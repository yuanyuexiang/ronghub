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
  onBack,
  active = true,
}: {
  manage: boolean;
  refreshKey?: number;
  onBack?: () => void;
  active?: boolean;
  onOpen: (id: number) => void;
}) {
  const canExport = useCapability("content.export");
  const [expanded, setExpanded] = useState(false);
  const showList = Boolean(onBack) || expanded;
  const [draft, setDraft] = useState({
    q: "",
    state: "",
    replied: "",
    scheduled_from: "",
    scheduled_to: "",
  });
  const [query, setQuery] = useState({ ...draft, page: 1 });
  const [data, setData] = useState<ApiSchemas["NoticeRecordPage"] | null>(null);
  const [loading, setLoading] = useState(false),
    [error, setError] = useState("");
  const pageRefreshRevision = usePageRefresh();
  useEffect(() => {
    if (!active || !showList) return;
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
            scheduled_from: query.scheduled_from || undefined,
            scheduled_to: query.scheduled_to || undefined,
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
  }, [active, manage, showList, query, refreshKey, pageRefreshRevision]);
  const { page: _page, ...filters } = query;
  const exportFilters = new URLSearchParams(
    Object.entries(filters).filter(([, value]) => value),
  );
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
          onClick={onBack ?? (() => setExpanded(!expanded))}
        >
          {onBack ? "返回通知发布" : expanded ? "收起通知记录" : "展开通知记录"}
        </button>
      </div>
      {showList && (
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
            {manage && (
              <>
                <label>
                  计划生效日期起（北京）
                  <input
                    type="date"
                    value={draft.scheduled_from}
                    max={draft.scheduled_to || "9999-12-30"}
                    onChange={(e) =>
                      setDraft({ ...draft, scheduled_from: e.target.value })
                    }
                  />
                </label>
                <label>
                  计划生效日期止（北京）
                  <input
                    type="date"
                    value={draft.scheduled_to}
                    min={draft.scheduled_from || undefined}
                    max="9999-12-30"
                    onChange={(e) =>
                      setDraft({ ...draft, scheduled_to: e.target.value })
                    }
                  />
                </label>
              </>
            )}
            <button className="primary" disabled={loading}>
              查询通知记录
            </button>
          </form>
          {manage && canExport && data && !loading && (
            <a
              className="outline notice-export-link"
              href={`/api/v1/content/notice-records/export?${exportFilters}`}
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
                <table className="aligned-records">
                  <thead>
                    <tr>
                      <th>记录 / 通知</th>
                      <th>收件企业快照</th>
                      <th>计划生效时间（北京时间）</th>
                      <th>站内状态</th>
                      <th className="numeric-cell">企业回复</th>
                      <th>最近回复时间（北京时间）</th>
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
                        <td className="numeric-cell">{row.reply_count}条</td>
                        <td>
                          {row.latest_reply_at
                            ? new Date(row.latest_reply_at).toLocaleString(
                                "zh-CN",
                                {
                                  timeZone: "Asia/Shanghai",
                                  hour12: false,
                                },
                              )
                            : "—"}
                        </td>
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
