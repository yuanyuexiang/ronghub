"use client";
import { useEffect, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";

export function NoticeRecipients({
  value,
  onChange,
  disabled,
}: {
  value: number[];
  onChange: (ids: number[]) => void;
  disabled: boolean;
}) {
  const [q, setQ] = useState(""),
    [applied, setApplied] = useState(""),
    [page, setPage] = useState(1);
  const [data, setData] = useState<ApiSchemas["ContentOperatorPage"] | null>(
    null,
  );
  const [error, setError] = useState(""),
    [loading, setLoading] = useState(true);
  const [names, setNames] = useState<Record<number, string>>({});
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    typedApi
      .GET("/api/v1/content/operator-options", {
        params: { query: { q: applied, page } },
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "收件企业读取失败"));
        const last = Math.max(1, Math.ceil(data.total / data.page_size));
        if (page > last) {
          setPage(last);
          return;
        }
        setData(data);
        setNames((current) => ({
          ...current,
          ...Object.fromEntries(data.items.map((row) => [row.id, row.name])),
        }));
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [applied, page]);
  return (
    <fieldset className="wide-field" disabled={disabled}>
      <legend>通知收件企业（必选，可多选）</legend>
      <p className="muted">
        已选{value.length}
        /100家。发布后生成逐企业记录；不会发送短信或邮件。搜索和翻页保留选择。
      </p>
      <label>
        查找收件企业
        <input
          aria-label="查找收件企业"
          value={q}
          maxLength={150}
          onChange={(e) => setQ(e.target.value)}
        />
      </label>
      <button
        type="button"
        className="outline"
        onClick={() => {
          setApplied(q);
          setPage(1);
        }}
      >
        筛选收件企业
      </button>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {loading ? (
        <p role="status">正在读取企业…</p>
      ) : (
        <div className="notice-company-options">
          {data?.items.map((row) => (
            <label key={row.id}>
              <input
                type="checkbox"
                aria-label={`收件企业：${row.name}`}
                checked={value.includes(row.id)}
                disabled={!value.includes(row.id) && value.length >= 100}
                onChange={(e) =>
                  onChange(
                    e.target.checked
                      ? [...value, row.id]
                      : value.filter((id) => id !== row.id),
                  )
                }
              />
              {row.name}
            </label>
          ))}
        </div>
      )}
      <div className="pagination">
        <button
          type="button"
          className="outline"
          disabled={loading || page <= 1}
          onClick={() => setPage(page - 1)}
        >
          上一页企业
        </button>
        <span>
          第{page}页 · 共{data?.total ?? 0}家
        </span>
        <button
          type="button"
          className="outline"
          disabled={loading || !data || page * 20 >= data.total}
          onClick={() => setPage(page + 1)}
        >
          下一页企业
        </button>
      </div>
      <p>
        已选：
        {value.length
          ? value.map((id) => names[id] ?? `企业 #${id}`).join("、")
          : "尚未选择"}
      </p>
      {value.length > 0 && (
        <button type="button" className="outline" onClick={() => onChange([])}>
          清空收件企业
        </button>
      )}
    </fieldset>
  );
}
