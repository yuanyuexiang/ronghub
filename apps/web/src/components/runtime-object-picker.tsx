"use client";
import { useEffect, useRef, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
export type RuntimeObject = ApiSchemas["RuntimeObject"];
function key(row: RuntimeObject) {
  return JSON.stringify([row.equipment_external_id, row.connector_external_id]);
}
export function RuntimeObjectPicker({
  stationId,
  source,
  value,
  onChange,
}: {
  stationId: string;
  source: string;
  value: RuntimeObject | null;
  onChange: (value: RuntimeObject | null) => void;
}) {
  const [input, setInput] = useState(""),
    [query, setQuery] = useState(""),
    [page, setPage] = useState(1);
  const [rows, setRows] = useState<RuntimeObject[]>([]),
    [total, setTotal] = useState(0),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const current = useRef(value);
  current.current = value;
  useEffect(() => {
    if (!stationId || !source.trim()) {
      setRows([]);
      setTotal(0);
      return;
    }
    const controller = new AbortController();
    setBusy(true);
    setError("");
    setRows([]);
    typedApi
      .GET("/api/v1/statistics/runtime/objects", {
        params: {
          query: {
            station_id: Number(stationId),
            source,
            q: query,
            page,
            page_size: 20,
          },
        },
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "源报对象读取失败"));
        const last = Math.max(1, Math.ceil(data.total / data.page_size));
        if (page > last) {
          setPage(last);
          return;
        }
        setRows(data.items);
        setTotal(data.total);
        if (!current.current && data.items[0]) onChange(data.items[0]);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "源报对象读取失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [stationId, source, query, page, onChange]);
  return (
    <div className="station-picker">
      <label>
        源报对象
        <select
          aria-label="历史运行源报对象"
          value={value ? key(value) : ""}
          disabled={busy}
          onChange={(e) =>
            onChange(rows.find((row) => key(row) === e.target.value) ?? null)
          }
        >
          <option value="">请选择有历史上送的对象</option>
          {value && !rows.some((row) => key(row) === key(value)) && (
            <option value={key(value)}>
              {value.equipment_external_id} ·{" "}
              {value.connector_external_id
                ? `枪 ${value.connector_external_id}`
                : "设备本级"}
              （当前选择）
            </option>
          )}
          {rows.map((row) => (
            <option key={key(row)} value={key(row)}>
              {row.equipment_external_id} ·{" "}
              {row.connector_external_id
                ? `枪 ${row.connector_external_id}`
                : "设备本级"}
            </option>
          ))}
        </select>
      </label>
      <div className="picker-search">
        <input
          aria-label="历史运行对象编码筛选"
          value={input}
          maxLength={100}
          placeholder="按源桩或枪编码查找"
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
          disabled={busy}
          onClick={() => {
            setPage(1);
            setQuery(input);
          }}
        >
          筛选对象
        </button>
      </div>
      <div className="picker-pages">
        <button
          type="button"
          aria-label="对象上一页"
          disabled={busy || page === 1}
          onClick={() => setPage(page - 1)}
        >
          ←
        </button>
        <small>
          第{page}页 · {total}个源报对象
        </small>
        <button
          type="button"
          aria-label="对象下一页"
          disabled={busy || page * 20 >= total}
          onClick={() => setPage(page + 1)}
        >
          →
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <small className="muted">
        设备本级与单枪分别查询；源编码尚未认证为本地设备映射。
      </small>
    </div>
  );
}
