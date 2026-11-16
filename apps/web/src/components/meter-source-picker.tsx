"use client";
import { useEffect, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
export function MeterSourcePicker({
  value,
  onChange,
  district,
  operatorId,
  kind = "orders",
}: {
  value: string;
  onChange: (value: string) => void;
  district: string;
  operatorId: string;
  kind?: "orders" | "quality";
}) {
  const [input, setInput] = useState(""),
    [query, setQuery] = useState(""),
    [page, setPage] = useState(1);
  const [rows, setRows] = useState<ApiSchemas["MeterSourceOption"][]>([]),
    [total, setTotal] = useState(0),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    setError("");
    setRows([]);
    typedApi
      .GET(
        kind === "quality"
          ? "/api/v1/statistics/quality/sources"
          : "/api/v1/orders/time-slots/aggregate/sources",
        {
          params: {
            query: {
              q: query,
              page,
              page_size: 20,
              district,
              operator_id: operatorId ? Number(operatorId) : undefined,
            },
          },
          signal: controller.signal,
        },
      )
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "来源列表读取失败"));
        const last = Math.max(1, Math.ceil(data.total / data.page_size));
        if (page > last) {
          setPage(last);
          return;
        }
        setRows(data.items);
        setTotal(data.total);
        if (!value && data.items[0]) onChange(data.items[0].source);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "来源列表读取失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [query, page, district, operatorId, kind]);
  return (
    <div className="meter-source-picker">
      <label>
        数据来源
        <select
          aria-label={kind === "quality" ? "质量数据来源" : "多站数据来源"}
          value={value}
          disabled={busy}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">请选择来源</option>
          {value && !rows.some((row) => row.source === value) && (
            <option value={value}>{value}（当前选择）</option>
          )}
          {rows.map((row) => (
            <option key={row.source} value={row.source}>
              {row.source} · 来源台账{row.station_count}站
            </option>
          ))}
        </select>
      </label>
      <div className="picker-search">
        <input
          aria-label={kind === "quality" ? "检索质量来源" : "检索多站来源"}
          value={input}
          maxLength={50}
          placeholder="检索来源"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              setPage(1);
              setQuery(input);
            }
          }}
        />
        <button
          type="button"
          className="outline"
          onClick={() => {
            setPage(1);
            setQuery(input);
          }}
        >
          筛选来源
        </button>
      </div>
      <div className="picker-pages">
        <button
          type="button"
          disabled={busy || page <= 1}
          onClick={() => setPage(page - 1)}
          aria-label="来源上一页"
        >
          ←
        </button>
        <small>{busy ? "读取来源…" : `来源第${page}页 / 共${total}个`}</small>
        <button
          type="button"
          disabled={busy || page * 20 >= total}
          onClick={() => setPage(page + 1)}
          aria-label="来源下一页"
        >
          →
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
