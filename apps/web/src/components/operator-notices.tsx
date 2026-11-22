"use client";

import { useEffect, useState } from "react";
import { apiError, typedApi, type ApiSchemas } from "../lib/typed-api";
import { usePageRefresh } from "./page-refresh";
import { useCapability } from "./permissions";

const pageSize = 20;
const initial = { q: "", published_from: "", published_to: "", replied: "" };
const clock = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  dateStyle: "short",
  timeStyle: "short",
  hour12: false,
});

export function OperatorNotices({
  busy,
  onOpen,
  refreshKey,
}: {
  busy: boolean;
  onOpen: (id: number) => void;
  refreshKey: number;
}) {
  const canExport = useCapability("content.notice_export");
  const canReply = useCapability("content.reply");
  const revision = usePageRefresh();
  const [draft, setDraft] = useState(initial);
  const [query, setQuery] = useState({ ...initial, page: 1 });
  const [result, setResult] = useState<ApiSchemas["OperatorNoticePage"]>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const { page, ...filters } = query;
  const exportParams = new URLSearchParams(
    Object.entries(filters).filter(([, value]) => value !== ""),
  );

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setResult(undefined);

    typedApi
      .GET("/api/v1/content/operator-notices", {
        params: {
          query: {
            q: query.q,
            published_from: query.published_from || undefined,
            published_to: query.published_to || undefined,
            replied:
              query.replied === "" ? undefined : query.replied === "true",
            page: query.page,
            page_size: pageSize,
          },
        },
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "通知读取失败"));
        const last = Math.max(1, Math.ceil(data.total / pageSize));
        if (query.page > last) {
          setQuery({ ...query, page: last });
          return;
        }
        setResult(data);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "通知读取失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [query, refreshKey, revision]);

  return (
    <section className="panel operator-notice-inbox" aria-label="通知办理列表">
      <div className="panel-title">
        <h2>通知列表</h2>
        {canExport && result && !loading && (
          <a
            className="outline"
            href={`/api/v1/content/operator-notices/export?${exportParams}`}
          >
            导出通知清单
          </a>
        )}
      </div>
      <form
        className="application-filters"
        onSubmit={(event) => {
          event.preventDefault();
          setQuery({ ...draft, page: 1 });
        }}
      >
        <label>
          通知标题
          <input
            type="search"
            value={draft.q}
            maxLength={150}
            onChange={(e) => setDraft({ ...draft, q: e.target.value })}
          />
        </label>
        <label>
          生效日期起（北京）
          <input
            type="date"
            max={draft.published_to || "9999-12-30"}
            value={draft.published_from}
            onChange={(e) =>
              setDraft({ ...draft, published_from: e.target.value })
            }
          />
        </label>
        <label>
          生效日期止（北京）
          <input
            type="date"
            min={draft.published_from || undefined}
            max="9999-12-30"
            value={draft.published_to}
            onChange={(e) =>
              setDraft({ ...draft, published_to: e.target.value })
            }
          />
        </label>
        <label>
          回复状态
          <select
            value={draft.replied}
            onChange={(e) => setDraft({ ...draft, replied: e.target.value })}
          >
            <option value="">全部</option>
            <option value="false">未回复</option>
            <option value="true">已回复</option>
          </select>
        </label>
        <div className="operator-notice-filter-actions">
          <button className="primary" disabled={busy || loading}>
            查询通知
          </button>
          <button
            type="button"
            className="outline"
            disabled={busy || loading}
            onClick={() => {
              setDraft(initial);
              setQuery({ ...initial, page: 1 });
            }}
          >
            重置
          </button>
        </div>
      </form>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {loading && (
        <p className="empty" role="status">
          正在读取通知…
        </p>
      )}
      {result && (
        <>
          {result.items.length ? (
            <div className="table-wrap">
              <table className="aligned-records" role="table">
                <thead role="rowgroup">
                  <tr role="row">
                    <th scope="col" role="columnheader">
                      通知标题
                    </th>
                    <th scope="col" role="columnheader">
                      站内生效时间（北京）
                    </th>
                    <th scope="col" role="columnheader">
                      回复状态
                    </th>
                    <th scope="col" role="columnheader">
                      最近回复时间（北京）
                    </th>
                    <th scope="col" role="columnheader">
                      操作
                    </th>
                  </tr>
                </thead>
                <tbody role="rowgroup">
                  {result.items.map((row) => (
                    <tr key={row.article_id} role="row">
                      <td role="cell" className="operator-notice-title">
                        {row.title}
                      </td>
                      <td role="cell">
                        <span
                          className="operator-notice-mobile-label"
                          aria-hidden="true"
                        >
                          生效时间（北京）
                        </span>
                        {clock.format(new Date(row.published_at))}
                      </td>
                      <td role="cell" className="operator-notice-state">
                        <em className={row.reply_count ? "green" : "amber"}>
                          {row.reply_count ? "已回复" : "未回复"}
                        </em>
                      </td>
                      <td role="cell">
                        <span
                          className="operator-notice-mobile-label"
                          aria-hidden="true"
                        >
                          最近回复（北京）
                        </span>
                        {row.latest_reply_at
                          ? clock.format(new Date(row.latest_reply_at))
                          : "—"}
                      </td>
                      <td role="cell" className="operator-notice-row-action">
                        <button
                          type="button"
                          className="outline"
                          disabled={busy}
                          onClick={() => onOpen(row.article_id)}
                        >
                          {canReply && !row.reply_count
                            ? "查看并回复"
                            : "查看通知"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="empty">暂无符合条件的通知</p>
          )}
          {result.total > pageSize && (
            <div className="pagination">
              <button
                type="button"
                className="outline"
                disabled={busy || page <= 1}
                onClick={() => setQuery({ ...query, page: page - 1 })}
              >
                上一页
              </button>
              <span>
                第{page}页 · 共{result.total}条
              </span>
              <button
                type="button"
                className="outline"
                disabled={busy || page * pageSize >= result.total}
                onClick={() => setQuery({ ...query, page: page + 1 })}
              >
                下一页
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
