"use client";
import { usePageRefresh } from "./page-refresh";
import { typedApi, apiError } from "../lib/typed-api";
import { useEffect, useRef, useState } from "react";
type Option = { id: number; name: string; source?: string };
export function StationPicker({
  label,
  value,
  onChange,
  status = "",
  kind = "inventory",
  onSource,
  allowAll = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  status?: string;
  kind?: "inventory" | "runtime" | "alerts" | "inspection";
  onSource?: (source: string) => void;
  allowAll?: boolean;
}) {
  const [items, setItems] = useState<Option[]>([]),
    [selected, setSelected] = useState<Option | null>(null),
    [input, setInput] = useState(""),
    [query, setQuery] = useState(""),
    [page, setPage] = useState(1),
    [total, setTotal] = useState(0),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const current = useRef(value);
  current.current = value;
  const pageRefreshRevision = usePageRefresh();
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    setError("");
    const request =
      kind === "inspection"
        ? typedApi.GET("/api/v1/inspection-plans/stations", {
            params: { query: { q: query, page } },
            cache: "no-store",
            signal: controller.signal,
          })
        : kind === "runtime"
          ? typedApi.GET("/api/v1/statistics/runtime/stations", {
              params: { query: { q: query, page } },
              cache: "no-store",
              signal: controller.signal,
            })
          : kind === "alerts"
            ? typedApi.GET("/api/v1/alerts/configured-observations/stations", {
                params: { query: { q: query, page } },
                cache: "no-store",
                signal: controller.signal,
              })
            : typedApi.GET("/api/v1/stations", {
                params: { query: { q: query, status, page } },
                cache: "no-store",
                signal: controller.signal,
              });
    request
      .then(({ data: d, error }) => {
        if (!d) throw new Error(apiError(error, "场站加载失败"));
        if (controller.signal.aborted) return;
        setItems(d.items);
        setTotal(d.total);
        if (!allowAll && !current.current && d.items[0]) {
          setSelected(d.items[0]);
          onChange(String(d.items[0].id));
          if ("source" in d.items[0] && typeof d.items[0].source === "string")
            onSource?.(d.items[0].source);
        } else {
          const found = d.items.find(
            (s: Option) => String(s.id) === current.current,
          );
          if (found) setSelected(found);
        }
      })
      .catch((e) => {
        if (e.name !== "AbortError") setError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [
    page,
    query,
    status,
    kind,
    onChange,
    onSource,
    allowAll,
    pageRefreshRevision,
  ]);
  return (
    <div className="station-picker">
      <label>
        {label}
        <select
          aria-label={label}
          value={value}
          disabled={busy}
          onChange={(e) => {
            const found = items.find((s) => String(s.id) === e.target.value);
            if (found) setSelected(found);
            onChange(e.target.value);
            if (found?.source !== undefined) onSource?.(found.source);
          }}
        >
          <option value="">{allowAll ? "全部授权场站" : "请选择场站"}</option>
          {selected && !items.some((s) => s.id === selected.id) && (
            <option value={selected.id}>{selected.name}</option>
          )}
          {items.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </label>
      <div className="picker-search">
        <input
          aria-label={`${label}名称筛选`}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              setPage(1);
              setQuery(input);
            }
          }}
          placeholder="输入名称查找场站"
          maxLength={150}
        />
        <button
          type="button"
          className="outline"
          disabled={busy}
          onClick={() => {
            setPage(1);
            setQuery(input);
          }}
        >
          筛选
        </button>
      </div>
      <div className="picker-pages">
        <button
          type="button"
          disabled={busy || page === 1}
          onClick={() => setPage(page - 1)}
          aria-label={`${label}上一页`}
        >
          ←
        </button>
        <small>{busy ? "加载中…" : `第${page}页 / 共${total}个`}</small>
        <button
          type="button"
          disabled={busy || page * 20 >= total}
          onClick={() => setPage(page + 1)}
          aria-label={`${label}下一页`}
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
