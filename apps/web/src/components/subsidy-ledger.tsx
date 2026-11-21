"use client";
import { useEffect, useRef, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { useCapability } from "./permissions";
import { usePageRefresh } from "./page-refresh";
const initial = { q: "", status: "", paid_from: "", paid_to: "", page: 1 };
const time = (value: string | null | undefined) =>
  value
    ? new Date(value).toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour12: false,
      })
    : "未留存";
export function SubsidyLedger({
  onOpen,
  busy,
}: {
  onOpen: (id: number) => void;
  busy: boolean;
}) {
  const [draft, setDraft] = useState(initial),
    [query, setQuery] = useState(initial),
    [data, setData] = useState<ApiSchemas["LedgerPage"] | null>(null),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [exporting, setExporting] = useState(false);
  const sequence = useRef(0),
    revision = usePageRefresh(),
    canExport = useCapability("subsidy.export");
  function params(): {
    q: string;
    status: "" | "approved" | "disbursed";
    paid_from: string | undefined;
    paid_to: string | undefined;
    page: number;
  } {
    const status = query.status;
    if (status !== "" && status !== "approved" && status !== "disbursed")
      throw Error("不支持的状态");
    return {
      ...query,
      status,
      paid_from: query.paid_from || undefined,
      paid_to: query.paid_to || undefined,
    };
  }
  useEffect(() => {
    const current = ++sequence.current,
      controller = new AbortController();
    setLoading(true);
    setError("");
    setData(null);
    typedApi
      .GET("/api/v1/subsidy/ledger", {
        params: { query: { ...params(), page_size: 20 } },
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data, error }) => {
        if (!data) throw Error(apiError(error, "补贴记录读取失败"));
        if (sequence.current === current) {
          const last = Math.max(1, Math.ceil(data.total / 20));
          if (query.page > last) setQuery({ ...query, page: last });
          else setData(data);
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted && sequence.current === current)
          setError(e instanceof Error ? e.message : "读取失败");
      })
      .finally(() => {
        if (!controller.signal.aborted && sequence.current === current)
          setLoading(false);
      });
    return () => controller.abort();
  }, [query, revision]);
  async function download() {
    setExporting(true);
    setError("");
    try {
      const { data, error } = await typedApi.GET(
        "/api/v1/subsidy/ledger/export",
        { params: { query: params() }, parseAs: "blob", cache: "no-store" },
      );
      if (!data) throw Error(apiError(error, "补贴记录导出失败"));
      const url = URL.createObjectURL(data),
        a = document.createElement("a");
      a.href = url;
      a.download = "ronghub-subsidy-ledger.csv";
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "导出失败");
    } finally {
      setExporting(false);
    }
  }
  return (
    <section className="panel subsidy-ledger" aria-label="补贴生成与发放记录">
      <div className="panel-title">
        <div>
          <h2>补贴记录查询与导出</h2>
          <p>已核定记录与发放登记 · 申请办理另见“补贴申请管理”</p>
        </div>
      </div>
      <form
        className="ledger-filters"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery({ ...draft, page: 1 });
        }}
      >
        <label>
          运营商 / 场站
          <input
            aria-label="补贴台账名称"
            value={draft.q}
            maxLength={150}
            onChange={(e) => setDraft({ ...draft, q: e.target.value })}
          />
        </label>
        <label>
          记录状态
          <select
            aria-label="补贴台账状态"
            value={draft.status}
            onChange={(e) => setDraft({ ...draft, status: e.target.value })}
          >
            <option value="">全部核定记录</option>
            <option value="approved">待登记发放</option>
            <option value="disbursed">已登记发放</option>
          </select>
        </label>
        <label>
          发放登记开始日
          <input
            aria-label="台账发放登记开始日"
            type="date"
            max="9999-12-30"
            value={draft.paid_from}
            onChange={(e) => setDraft({ ...draft, paid_from: e.target.value })}
          />
        </label>
        <label>
          发放登记结束日
          <input
            aria-label="台账发放登记结束日"
            type="date"
            max="9999-12-30"
            value={draft.paid_to}
            onChange={(e) => setDraft({ ...draft, paid_to: e.target.value })}
          />
        </label>
        <div className="action-row">
          <button className="primary" disabled={loading || busy}>
            查询补贴台账
          </button>
          <button
            type="button"
            className="outline"
            disabled={loading || busy}
            onClick={() => {
              setDraft(initial);
              setQuery({ ...initial });
            }}
          >
            重置台账筛选
          </button>
          {canExport && (
            <button
              type="button"
              className="outline"
              disabled={loading || busy || exporting}
              onClick={() => void download()}
            >
              导出补贴记录
            </button>
          )}
        </div>
      </form>
      <details className="ledger-notice">
        <summary>时间及金额口径</summary>
        <p>
          本页只展示已核定及已登记发放记录。补贴生成时间、资金实际到账时间尚未留存，不以申请创建、提交或发放登记时间替代。当前按实际核定动作时间由远及近排列，缺历史时间的排在最后。日期筛选使用北京时间的发放登记日，导出使用已应用条件；发放登记不代表资金已到账。
        </p>
      </details>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {loading && (
        <p className="empty" role="status">
          正在读取补贴台账…
        </p>
      )}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              {[
                "记录编号",
                "补贴生成时间",
                "补贴周期",
                "运营商 / 场站",
                "汇算补贴（元）",
                "核定补贴（元）",
                "实际发放时间",
                "发放登记时间",
                "状态",
                "操作",
              ].map((s) => (
                <th key={s}>{s}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data?.items.map((row) => (
              <tr key={row.id}>
                <td>
                  SUB-{row.id}
                  <small>核定：{time(row.approved_at)}</small>
                </td>
                <td>{time(row.generated_at)}</td>
                <td>
                  {row.start_date} 至 {row.end_date}
                </td>
                <td>
                  {row.operator_name}
                  <small>{row.station_name}</small>
                </td>
                <td>{row.base_amount}</td>
                <td>{row.adjusted_amount}</td>
                <td>未留存</td>
                <td>{time(row.recorded_disbursement_at)}</td>
                <td>
                  {row.status === "disbursed" ? "已登记发放" : "待登记发放"}
                </td>
                <td>
                  <button
                    className="outline"
                    disabled={busy || loading}
                    onClick={() => onOpen(row.id)}
                  >
                    补贴明细
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data?.total === 0 && !loading && (
        <p className="empty">当前条件下没有已核定补贴记录</p>
      )}
      <div className="publication-pagination">
        <button
          className="outline"
          disabled={busy || loading || query.page <= 1}
          onClick={() => setQuery({ ...query, page: query.page - 1 })}
        >
          台账上一页
        </button>
        <span>
          第{query.page}页 · 共{data?.total ?? 0}条
        </span>
        <button
          className="outline"
          disabled={busy || loading || query.page * 20 >= (data?.total ?? 0)}
          onClick={() => setQuery({ ...query, page: query.page + 1 })}
        >
          台账下一页
        </button>
      </div>
    </section>
  );
}
