"use client";
import { useEffect, useRef, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { useCapability } from "./permissions";
import { usePageRefresh } from "./page-refresh";
const actions: Record<string, string> = {
  review: "人工复核",
  publish: "结果公示",
  reject: "驳回申请",
  delist: "摘牌",
  supersede: "新结果替代",
};
const statuses: Record<string, string> = {
  draft: "草稿",
  submitted: "待评审",
  reviewed: "已复核",
  published: "已公示",
  rejected: "已驳回",
  withdrawn: "已撤回",
  delisted: "已摘牌",
  superseded: "已替代",
};
const clock = (value: string) =>
  new Date(value).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour12: false,
  });
export function RatingProcess({
  onOpen,
  busy,
}: {
  onOpen: (id: number) => void;
  busy: boolean;
}) {
  const [draft, setDraft] = useState({ q: "", action: "" });
  const [query, setQuery] = useState({ ...draft, page: 1 });
  const [data, setData] = useState<ApiSchemas["RatingProcessPage"] | null>(
      null,
    ),
    [loading, setLoading] = useState(true),
    [error, setError] = useState("");
  const canExport = useCapability("ratings.export");
  const [exporting, setExporting] = useState(false);
  async function download() {
    const action = query.action;
    if (
      action !== "" &&
      action !== "review" &&
      action !== "publish" &&
      action !== "reject" &&
      action !== "delist" &&
      action !== "supersede"
    )
      return;
    setExporting(true);
    setError("");
    try {
      const { data, error } = await typedApi.GET(
        "/api/v1/ratings/process/export",
        {
          parseAs: "blob",
          cache: "no-store",
          params: { query: { q: query.q, action } },
        },
      );
      if (!data) throw Error(apiError(error, "评审过程导出失败"));
      const url = URL.createObjectURL(data),
        anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "ronghub-rating-process.csv";
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      setError(error instanceof Error ? error.message : "评审过程导出失败");
    } finally {
      setExporting(false);
    }
  }
  const revision = usePageRefresh(),
    generation = useRef(0);
  useEffect(() => {
    const request = ++generation.current,
      controller = new AbortController();
    setLoading(true);
    setError("");
    setData(null);
    const action = query.action;
    if (
      action !== "" &&
      action !== "review" &&
      action !== "publish" &&
      action !== "reject" &&
      action !== "delist" &&
      action !== "supersede"
    )
      return;
    typedApi
      .GET("/api/v1/ratings/process", {
        signal: controller.signal,
        cache: "no-store",
        params: { query: { ...query, action, page_size: 20 } },
      })
      .then(({ data, error }) => {
        if (!data) throw Error(apiError(error, "评审过程读取失败"));
        if (generation.current === request) {
          const last = Math.max(1, Math.ceil(data.total / 20));
          if (query.page > last) setQuery({ ...query, page: last });
          else setData(data);
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted && generation.current === request)
          setError(e instanceof Error ? e.message : "评审过程读取失败");
      })
      .finally(() => {
        if (!controller.signal.aborted && generation.current === request)
          setLoading(false);
      });
    return () => controller.abort();
  }, [query, revision]);
  return (
    <section className="panel rating-process-panel" aria-label="评审过程记录">
      <div className="panel-title">
        <div>
          <h2>评审过程管理</h2>
          <p>
            按实际评定动作查询，保留办理账号、时间及变更前后快照；未留存的历史不以当前结果补写。
          </p>
        </div>
      </div>
      <form
        className="business-form"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery({ ...draft, page: 1 });
        }}
      >
        <label>
          场站 / 运营商
          <input
            aria-label="评审过程场站企业"
            maxLength={150}
            value={draft.q}
            onChange={(e) => setDraft({ ...draft, q: e.target.value })}
          />
        </label>
        <label>
          评定动作
          <select
            aria-label="评审过程动作"
            value={draft.action}
            onChange={(e) => setDraft({ ...draft, action: e.target.value })}
          >
            <option value="">全部动作</option>
            {Object.entries(actions).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <button className="primary" disabled={loading || busy}>
          查询评审过程
        </button>
        <button
          type="button"
          className="outline"
          disabled={loading || busy}
          onClick={() => {
            setDraft({ q: "", action: "" });
            setQuery({ q: "", action: "", page: 1 });
          }}
        >
          重置评审过程
        </button>
      </form>
      {canExport && (
        <button
          className="outline"
          disabled={busy || loading || exporting}
          onClick={() => void download()}
        >
          导出评审过程
        </button>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              {[
                "场站 / 运营商",
                "申报编号",
                "评定时间（北京）",
                "评定账号",
                "动作",
                "变更前星级 / 有效期",
                "变更后星级 / 有效期",
                "变更后状态",
                "操作",
              ].map((name) => (
                <th key={name}>{name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data?.items.map((row) => (
              <tr key={row.id}>
                <td>
                  {row.station_name}
                  <small className="muted">{row.operator_name}</small>
                </td>
                <td>RATE-{row.application_id}</td>
                <td>{clock(row.created_at)}</td>
                <td>{row.actor_username}</td>
                <td>{actions[row.action] ?? row.action}</td>
                <td>
                  {row.snapshot_recorded
                    ? `${row.before?.stars ?? "未留存"} / ${row.before?.valid_until ?? "未留存"}`
                    : "历史快照未留存"}
                </td>
                <td>
                  {row.snapshot_recorded
                    ? `${row.after?.stars ?? "未留存"} / ${row.after?.valid_until ?? "未留存"}`
                    : "历史快照未留存"}
                </td>
                <td>
                  {row.after?.status
                    ? (statuses[row.after.status] ?? row.after.status)
                    : "未留存"}
                </td>
                <td>
                  <button
                    className="outline"
                    disabled={busy}
                    onClick={() => onOpen(row.application_id)}
                  >
                    查看评定档案
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {loading ? (
        <p role="status" className="empty">
          正在读取评审过程…
        </p>
      ) : data?.total === 0 ? (
        <p className="empty">当前条件下没有评审过程记录</p>
      ) : null}
      <div className="publication-pagination">
        <button
          className="outline"
          disabled={loading || busy || query.page <= 1}
          onClick={() => setQuery({ ...query, page: query.page - 1 })}
        >
          评审过程上一页
        </button>
        <span>
          第{query.page}页 · 共{data?.total ?? 0}条过程记录
        </span>
        <button
          className="outline"
          disabled={loading || busy || query.page * 20 >= (data?.total ?? 0)}
          onClick={() => setQuery({ ...query, page: query.page + 1 })}
        >
          评审过程下一页
        </button>
      </div>
    </section>
  );
}
