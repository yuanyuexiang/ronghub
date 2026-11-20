"use client";
import { usePageRefresh } from "./page-refresh";
import {
  Dispatch,
  SetStateAction,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import type { paths } from "@ronghub/api-schema";
import { typedApi, apiError } from "../lib/typed-api";
export type AlertCategory = NonNullable<
  NonNullable<paths["/api/v1/alerts"]["get"]["parameters"]["query"]>["category"]
>;
type InspectionParams = NonNullable<
  paths["/api/v1/inspections"]["get"]["parameters"]["query"]
>;
export type InspectionExecution = NonNullable<
  InspectionParams["execution_state"]
>;
export type QueueQuery = {
  category?: AlertCategory | "";
  q: string;
  status: string;
  kind: string;
  record_id: string;
  overdue?: string;
  acknowledged?: string;
  start_at?: string;
  end_at?: string;
  execution_state?: InspectionExecution;
  created_from?: string;
  created_to?: string;
  submitted_from?: string;
  submitted_to?: string;
  period_from?: string;
  period_to?: string;
  page: number;
};
const initial: QueueQuery = {
  q: "",
  status: "",
  kind: "",
  record_id: "",
  page: 1,
};
export function businessDateParams(query: QueueQuery, subsidy: boolean) {
  return {
    submitted_from: query.submitted_from || undefined,
    submitted_to: query.submitted_to || undefined,
    period_from: subsidy ? query.period_from || undefined : undefined,
    period_to: subsidy ? query.period_to || undefined : undefined,
  };
}
export function submissionTime(value: string | null) {
  return value
    ? new Date(value).toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour12: false,
      })
    : "无提交时间记录";
}
function params(query: QueueQuery) {
  return {
    q: query.q,
    status: query.status,
    record_id: query.record_id ? Number(query.record_id) : undefined,
    page: query.page,
    page_size: 20,
  };
}
export async function loadSubsidyQueue(query: QueueQuery, signal: AbortSignal) {
  const { data, error } = await typedApi.GET("/api/v1/subsidy/claims", {
    cache: "no-store",
    signal,
    params: { query: { ...params(query), ...businessDateParams(query, true) } },
  });
  if (!data) throw new Error(apiError(error, "补贴记录加载失败"));
  return data;
}
export async function loadRatingQueue(query: QueueQuery, signal: AbortSignal) {
  const { data, error } = await typedApi.GET("/api/v1/ratings/applications", {
    cache: "no-store",
    signal,
    params: {
      query: {
        ...params(query),
        kind: query.kind,
        ...businessDateParams(query, false),
      },
    },
  });
  if (!data) throw new Error(apiError(error, "星级记录加载失败"));
  return data;
}
export function inspectionQueueParams(
  query: QueueQuery,
): Omit<InspectionParams, "page" | "page_size"> {
  const status = query.status;
  if (
    status !== "" &&
    status !== "assigned" &&
    status !== "replied" &&
    status !== "returned" &&
    status !== "closed"
  )
    throw new Error("不支持的巡检办理状态");
  const severity = query.kind;
  if (severity !== "" && severity !== "normal" && severity !== "major")
    throw new Error("不支持的巡检等级");
  return {
    q: query.q,
    status,
    severity,
    record_id: query.record_id ? Number(query.record_id) : undefined,
    overdue: query.overdue ? query.overdue === "true" : undefined,
    execution_state: query.execution_state || undefined,
    created_from: query.created_from || undefined,
    created_to: query.created_to || undefined,
  };
}
export async function loadInspectionQueue(
  query: QueueQuery,
  signal: AbortSignal,
) {
  const { data, error } = await typedApi.GET("/api/v1/inspections", {
    cache: "no-store",
    signal,
    params: {
      query: {
        ...inspectionQueueParams(query),
        page: query.page,
        page_size: 20,
      },
    },
  });
  if (!data) throw new Error(apiError(error, "巡检任务加载失败"));
  return data;
}
export function alertQueueParams(query: QueueQuery) {
  return {
    ...params(query),
    category: query.category || undefined,
    rule_id: query.kind ? Number(query.kind) : undefined,
    acknowledged: query.acknowledged
      ? query.acknowledged === "true"
      : undefined,
    start_at: query.start_at
      ? new Date(query.start_at).toISOString()
      : undefined,
    end_at: query.end_at ? new Date(query.end_at).toISOString() : undefined,
  };
}
export async function loadAlertQueue(query: QueueQuery, signal: AbortSignal) {
  const { data, error } = await typedApi.GET("/api/v1/alerts", {
    cache: "no-store",
    signal,
    params: { query: alertQueueParams(query) },
  });
  if (!data) throw new Error(apiError(error, "告警记录加载失败"));
  return data;
}
type Page<T> = { items: T[]; total: number; page: number; page_size: number };
export function useBusinessQueue<T>(
  loadPage: (query: QueueQuery, signal: AbortSignal) => Promise<Page<T>>,
  defaults: Partial<QueueQuery> = {},
) {
  const [baseline] = useState(() => ({ ...initial, ...defaults }));
  const [query, setQuery] = useState(baseline),
    [draft, setDraft] = useState(baseline),
    [rows, setRows] = useState<T[]>([]);
  const [total, setTotal] = useState(0),
    [loading, setLoading] = useState(true),
    [error, setError] = useState("");
  const queryRef = useRef(query),
    sequence = useRef(0),
    controller = useRef<AbortController | null>(null);
  queryRef.current = query;
  const reload = useCallback(
    async (target = queryRef.current) => {
      const current = ++sequence.current;
      controller.current?.abort();
      const abort = new AbortController();
      controller.current = abort;
      setLoading(true);
      setRows([]);
      setError("");
      try {
        const page = await loadPage(target, abort.signal);
        if (current !== sequence.current) return;
        const last = Math.max(1, Math.ceil(page.total / 20));
        if (target.page > last) {
          setQuery({ ...target, page: last });
          return;
        }
        setRows(page.items);
        setTotal(page.total);
      } catch (error) {
        if (current === sequence.current && !abort.signal.aborted)
          setError(error instanceof Error ? error.message : "查询失败");
      } finally {
        if (current === sequence.current) setLoading(false);
      }
    },
    [loadPage],
  );
  const pageRefreshRevision = usePageRefresh();
  useEffect(() => {
    void reload(query);
    return () => {
      ++sequence.current;
      controller.current?.abort();
    };
  }, [query, reload, pageRefreshRevision]);
  const reset = () => {
    setDraft(baseline);
    setQuery({ ...baseline });
  };
  return {
    rows,
    total,
    loading,
    error,
    query,
    draft,
    setDraft,
    reload,
    reset,
    apply: (patch?: Partial<QueueQuery>) => {
      const next = { ...draft, ...patch, page: 1 };
      setDraft(next);
      setQuery(next);
    },
    goToPage: (page: number) => setQuery({ ...query, page }),
  };
}
type Controls = {
  draft: QueueQuery;
  query: QueueQuery;
  setDraft: Dispatch<SetStateAction<QueueQuery>>;
  loading: boolean;
  error: string;
  total: number;
  apply: () => void;
  reset: () => void;
  goToPage: (page: number) => void;
};
export function QueueFilters({
  queue,
  label,
  statuses,
  kinds,
  busy,
  onSearch,
  period = false,
}: {
  queue: Controls;
  label: string;
  statuses: Record<string, string>;
  kinds?: Record<string, string>;
  busy: boolean;
  onSearch: () => void;
  period?: boolean;
}) {
  return (
    <>
      <form
        className="application-filters"
        onSubmit={(event) => {
          event.preventDefault();
          if (busy) return;
          onSearch();
          queue.apply();
        }}
      >
        <label>
          场站或企业名称
          <input
            aria-label={`${label}检索名称`}
            maxLength={150}
            value={queue.draft.q}
            onChange={(event) =>
              queue.setDraft({ ...queue.draft, q: event.target.value })
            }
          />
        </label>
        <label>
          申报编号
          <input
            aria-label={`${label}检索编号`}
            type="number"
            min="1"
            value={queue.draft.record_id}
            onChange={(event) =>
              queue.setDraft({ ...queue.draft, record_id: event.target.value })
            }
          />
        </label>
        <label>
          办理状态
          <select
            aria-label={`${label}筛选状态`}
            value={queue.draft.status}
            onChange={(event) =>
              queue.setDraft({ ...queue.draft, status: event.target.value })
            }
          >
            <option value="">全部状态</option>
            {Object.entries(statuses).map(([key, name]) => (
              <option key={key} value={key}>
                {name}
              </option>
            ))}
          </select>
        </label>
        {kinds && (
          <label>
            申报类型
            <select
              aria-label={`${label}筛选类型`}
              value={queue.draft.kind}
              onChange={(event) =>
                queue.setDraft({ ...queue.draft, kind: event.target.value })
              }
            >
              <option value="">全部类型</option>
              {Object.entries(kinds).map(([key, name]) => (
                <option key={key} value={key}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        )}
        <fieldset className="queue-date-range">
          <legend>最近提交日期（北京时间）</legend>
          <label>
            开始日期
            <input
              aria-label={`${label}提交开始日期`}
              type="date"
              max={queue.draft.submitted_to || undefined}
              value={queue.draft.submitted_from || ""}
              onChange={(e) =>
                queue.setDraft({
                  ...queue.draft,
                  submitted_from: e.target.value,
                })
              }
            />
          </label>
          <label>
            结束日期
            <input
              aria-label={`${label}提交结束日期`}
              type="date"
              min={queue.draft.submitted_from || undefined}
              max="9999-12-30"
              value={queue.draft.submitted_to || ""}
              onChange={(e) =>
                queue.setDraft({ ...queue.draft, submitted_to: e.target.value })
              }
            />
          </label>
        </fieldset>
        {period && (
          <fieldset className="queue-date-range">
            <legend>补贴申请周期（与所选区间相交）</legend>
            <label>
              开始日期
              <input
                aria-label="补贴周期开始日期"
                type="date"
                max={queue.draft.period_to || undefined}
                value={queue.draft.period_from || ""}
                onChange={(e) =>
                  queue.setDraft({
                    ...queue.draft,
                    period_from: e.target.value,
                  })
                }
              />
            </label>
            <label>
              结束日期
              <input
                aria-label="补贴周期结束日期"
                type="date"
                min={queue.draft.period_from || undefined}
                value={queue.draft.period_to || ""}
                onChange={(e) =>
                  queue.setDraft({ ...queue.draft, period_to: e.target.value })
                }
              />
            </label>
          </fieldset>
        )}
        <button className="primary" disabled={busy || queue.loading}>
          查询{label}记录
        </button>
        <button
          className="outline"
          type="button"
          disabled={busy || queue.loading}
          onClick={() => {
            onSearch();
            queue.reset();
          }}
        >
          重置{label}筛选
        </button>
      </form>
      <p className="muted application-list-note">
        名称为当前台账；编号保留。新建申报返回全部记录第一页。最近提交取实际提交记录，缺失不以创建时间代替；日期含结束日。
        {kinds
          ? "办理状态与星级有效性分别判断。"
          : "金额采用原核算精度展示，不代表正式结算。"}
      </p>
      {queue.error && (
        <p className="error" role="alert">
          {queue.error}
        </p>
      )}
      {queue.loading && (
        <p className="empty" role="status">
          正在加载{label}记录…
        </p>
      )}
    </>
  );
}
export function QueuePagination({
  queue,
  label,
  busy,
}: {
  queue: Controls;
  label: string;
  busy: boolean;
}) {
  return (
    <div className="action-row application-pagination">
      <button
        className="outline"
        disabled={busy || queue.loading || queue.query.page === 1}
        onClick={() => queue.goToPage(queue.query.page - 1)}
      >
        {label}上一页
      </button>
      <span>
        {label}第{queue.query.page}页 · 共{queue.total}条
      </span>
      <button
        className="outline"
        disabled={busy || queue.loading || queue.query.page * 20 >= queue.total}
        onClick={() => queue.goToPage(queue.query.page + 1)}
      >
        {label}下一页
      </button>
    </div>
  );
}
