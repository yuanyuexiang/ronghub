"use client";
import { usePageRefresh } from "./page-refresh";
import { useEffect, useRef, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";

type Publication = ApiSchemas["RatingPublication"];
export const resultStates: Record<string, string> = {
  valid: "有效",
  evaluating: "评星中",
  expired: "已到期",
  delisted: "已摘牌",
  validity_unknown: "有效期未确认",
  inactive: "场站不符合条件",
  superseded: "已被新结果替代",
  unpublished: "未公示",
};
export function RatingPublications({
  revision,
  onOpen,
  busy,
  title = "星级公示查询",
  testIdPrefix = "publication",
  layout = "cards",
}: {
  revision: number;
  onOpen: (id: number) => void;
  busy: boolean;
  title?: string;
  layout?: "cards" | "table";
  testIdPrefix?: string;
}) {
  const [query, setQuery] = useState("");
  const [state, setState] = useState("");
  const [stars, setStars] = useState("");
  const [filters, setFilters] = useState({
    q: "",
    result_state: "",
    stars: "",
    station_type: "",
  });
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<Publication[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const sequence = useRef(0);
  const pageRefreshRevision = usePageRefresh();
  useEffect(() => {
    const id = ++sequence.current;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    typedApi
      .GET("/api/v1/ratings/publications", {
        params: {
          query: {
            ...filters,
            page,
            page_size: 6,
            stars: filters.stars ? Number(filters.stars) : undefined,
          },
        },
        cache: "no-store",
        signal: controller.signal,
      })
      .then((result) => {
        if (!result.data)
          throw new Error(apiError(result.error, "公示查询失败"));
        if (id === sequence.current) {
          setItems(result.data.items);
          setTotal(result.data.total);
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted && id === sequence.current)
          setError(e.message);
      })
      .finally(() => {
        if (id === sequence.current) setLoading(false);
      });
    return () => controller.abort();
  }, [filters, page, revision, pageRefreshRevision]);
  return (
    <section
      className={`panel ${layout === "table" ? "rating-result-panel" : ""}`}
    >
      <div className="panel-title">
        <div>
          <h2>{title}</h2>
          <p>当前授权场站的最新结果 · 演示评定 · 到期日当日有效（北京时间）</p>
        </div>
        <span className="pill">{total} 个场站</span>
      </div>
      <div
        className="prototype-shortcuts"
        role="tablist"
        aria-label="公示场站种类"
      >
        {[
          ["", "全部"],
          ["charging", "充电站"],
          ["swapping", "换电站"],
        ].map(([value, label]) => (
          <button
            type="button"
            role="tab"
            aria-selected={filters.station_type === value}
            key={value}
            className="outline"
            onClick={() => {
              setPage(1);
              setFilters({ ...filters, station_type: value });
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <form
        className="business-form"
        onSubmit={(e) => {
          e.preventDefault();
          setPage(1);
          setFilters({ ...filters, q: query, result_state: state, stars });
        }}
      >
        <label>
          场站 / 企业
          <input
            aria-label="公示场站企业"
            value={query}
            maxLength={100}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <label>
          结果状态
          <select
            aria-label="公示结果状态"
            value={state}
            onChange={(e) => setState(e.target.value)}
          >
            <option value="">全部状态</option>
            {[
              "valid",
              "expired",
              "delisted",
              "validity_unknown",
              "inactive",
              "evaluating",
            ].map((key) => (
              <option key={key} value={key}>
                {resultStates[key]}
              </option>
            ))}
          </select>
        </label>
        <label>
          有效星级
          <select
            aria-label="公示有效星级"
            value={stars}
            onChange={(e) => setStars(e.target.value)}
          >
            <option value="">全部星级</option>
            {[0, 1, 2, 3, 4, 5].map((n) => (
              <option key={n} value={n}>
                {n}星
              </option>
            ))}
          </select>
        </label>
        <button className="outline" disabled={loading}>
          查询公示
        </button>
      </form>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {loading ? (
        <p className="empty" role="status">
          正在查询公示结果…
        </p>
      ) : (
        <>
          {layout === "table" ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    {[
                      "场站 / 运营商",
                      "当前有效星级",
                      "评定状态",
                      "有效期至",
                      "操作",
                    ].map((label) => (
                      <th key={label}>{label}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {items.map((row) => (
                    <tr key={row.id} data-testid={`${testIdPrefix}-${row.id}`}>
                      <td>
                        {row.station_name}
                        <small>{row.operator_name}</small>
                      </td>
                      <td>
                        {row.effective_stars === null
                          ? "未确认"
                          : `${row.effective_stars} 星`}
                      </td>
                      <td>{resultStates[row.result_state]}</td>
                      <td>{row.valid_until ?? "未确认"}</td>
                      <td>
                        <button
                          className="outline"
                          disabled={busy}
                          onClick={() => onOpen(row.id)}
                        >
                          管理评定结果
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="publication-grid">
              {items.map((a) => (
                <article
                  className="publication-card"
                  key={a.id}
                  data-testid={`${testIdPrefix}-${a.id}`}
                >
                  <div className="action-row">
                    <span
                      className={
                        a.result_state === "valid" ? "pill green" : "pill amber"
                      }
                    >
                      {resultStates[a.result_state]}
                    </span>
                    <small>{a.district}</small>
                  </div>
                  <h3>{a.station_name}</h3>
                  <p className="publication-stars">
                    {a.effective_stars === null
                      ? "星级待确认"
                      : a.effective_stars > 0
                        ? "★".repeat(a.effective_stars)
                        : "当前有效星级 0"}
                  </p>
                  {a.show_association_mark && (
                    <small>演示评定 {a.effective_stars} 星</small>
                  )}
                  <p>
                    {a.operator_name} ·{" "}
                    {a.station_type === "charging"
                      ? "充电站"
                      : a.station_type === "swapping"
                        ? "换电站"
                        : a.station_type === "mixed"
                          ? "充换电站"
                          : "种类未提供"}
                  </p>
                  <p className="muted">{a.address}</p>
                  <p>有效期至：{a.valid_until ?? "未确认"}</p>
                  <button
                    className="outline"
                    disabled={busy}
                    onClick={() => onOpen(a.id)}
                  >
                    查看评定档案
                  </button>
                </article>
              ))}
            </div>
          )}
          {!items.length && <p className="empty">暂无符合条件的公示结果</p>}
          <div className="publication-pagination">
            <button
              className="outline"
              disabled={page <= 1 || loading}
              onClick={() => setPage(page - 1)}
            >
              上一页
            </button>
            <span>
              第 {page} / {Math.max(1, Math.ceil(total / 6))} 页
            </span>
            <button
              className="outline"
              disabled={page * 6 >= total || loading}
              onClick={() => setPage(page + 1)}
            >
              下一页
            </button>
          </div>
        </>
      )}
    </section>
  );
}
