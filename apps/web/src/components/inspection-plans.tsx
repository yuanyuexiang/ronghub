"use client";
import { useEffect, useRef, useState, type SetStateAction } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { usePageRefresh } from "./page-refresh";
import { RichEditor } from "./rich-editor";
import { RichContent } from "./rich-document";

type Detail = ApiSchemas["InspectionPlanDetail"];
type Summary = ApiSchemas["InspectionPlanSummary"];
type Option = ApiSchemas["InspectionPlanStationOption"];
type Form = Omit<ApiSchemas["InspectionPlanEdit"], "version" | "due_at"> & {
  dueLocal: string;
};
const states = { draft: "草稿", published: "已下发", deleted: "已删除" };
const taskStates: Record<string, string> = {
  assigned: "待整改",
  replied: "待复核",
  returned: "退回补充",
  closed: "已关闭",
};
const clock = (value: string | null) =>
  value
    ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" }) +
      "（北京时间）"
    : "未设置";
function localClock(value: string | null) {
  if (!value) return "";
  const date = new Date(value),
    pad = (n: number, length = 2) => String(n).padStart(length, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${pad(date.getMilliseconds(), 3)}`;
}
function editable(row: Detail): Form {
  return {
    title: row.title,
    description: row.description,
    rich_document: row.rich_document,
    station_ids: row.targets.map((t) => t.station_id),
    dueLocal: localClock(row.due_at),
  };
}

function Targets({
  selected,
  disabled,
  onToggle,
}: {
  selected: number[];
  disabled: boolean;
  onToggle: (option: Option, checked: boolean) => void;
}) {
  const [input, setInput] = useState(""),
    [query, setQuery] = useState(""),
    [page, setPage] = useState(1);
  const [data, setData] = useState<
      ApiSchemas["InspectionPlanStationPage"] | null
    >(null),
    [loading, setLoading] = useState(false),
    [error, setError] = useState("");
  const refresh = usePageRefresh();
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    typedApi
      .GET("/api/v1/inspection-plans/stations", {
        params: { query: { q: query, page } },
        cache: "no-store",
        signal: controller.signal,
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "计划场站读取失败"));
        const last = Math.max(1, Math.ceil(data.total / data.page_size));
        if (page > last) {
          setPage(last);
          return;
        }
        setData(data);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "计划场站读取失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [query, page, refresh]);
  return (
    <section
      className="plan-target-picker"
      aria-label="计划目标选择"
      aria-busy={loading}
    >
      <form
        className="plan-search"
        onSubmit={(event) => {
          event.preventDefault();
          setPage(1);
          setQuery(input);
        }}
      >
        <label>
          查找场站或企业
          <input
            aria-label="计划场站筛选"
            value={input}
            maxLength={150}
            onChange={(e) => setInput(e.target.value)}
            disabled={disabled}
          />
        </label>
        <button
          type="submit"
          className="outline"
          disabled={disabled || loading}
        >
          筛选计划场站
        </button>
      </form>
      <p className="muted">
        已应用：{query || "全部授权场站"} · 跨页保留选择，当前已选
        {selected.length}/100站
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {loading && <p role="status">正在读取场站…</p>}
      <div className="plan-target-options">
        {data?.items.map((option) => (
          <label className="plan-target-option" key={option.id}>
            <input
              type="checkbox"
              aria-label={`选择 ${option.name}（场站${option.id}）`}
              checked={selected.includes(option.id)}
              disabled={
                disabled ||
                loading ||
                (selected.length >= 100 && !selected.includes(option.id))
              }
              onChange={(e) => onToggle(option, e.target.checked)}
            />
            <span>
              <b>{option.name}</b>
              <small>
                {option.operator_name} · {option.district} · 场站#{option.id}
              </small>
            </span>
          </label>
        ))}
      </div>
      {!loading && data?.total === 0 && (
        <p className="empty">没有符合条件的授权场站</p>
      )}
      <div className="device-pagination">
        <button
          type="button"
          className="outline"
          aria-label="计划场站上一页"
          disabled={disabled || loading || page === 1}
          onClick={() => setPage(page - 1)}
        >
          上一页
        </button>
        <span>
          场站第{page}页 · 共{data?.total ?? 0}站
        </span>
        <button
          type="button"
          className="outline"
          aria-label="计划场站下一页"
          disabled={
            disabled || loading || !data || page * data.page_size >= data.total
          }
          onClick={() => setPage(page + 1)}
        >
          下一页
        </button>
      </div>
    </section>
  );
}

export function InspectionPlans({
  csrf,
  parentBusy,
  onPublished,
  onOpenTask,
  onDirty,
  onBusy,
}: {
  csrf: string;
  parentBusy: boolean;
  onPublished: () => Promise<void>;
  onOpenTask: (id: number) => Promise<void>;
  onDirty?: (value: boolean) => void;
  onBusy?: (value: boolean) => void;
}) {
  const [queryInput, setQueryInput] = useState(""),
    [query, setQuery] = useState(""),
    [status, setStatus] = useState<Summary["status"]>("draft"),
    [page, setPage] = useState(1),
    [revision, setRevision] = useState(0);
  const [rows, setRows] = useState<ApiSchemas["InspectionPlanPage"] | null>(
      null,
    ),
    [loading, setLoading] = useState(false),
    [listError, setListError] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null),
    [form, setFormState] = useState<Form | null>(null),
    [names, setNames] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [choice, setChoice] = useState<number | "new" | "close" | "delete" | null>(
      null,
    );
  const formRef = useRef<Form | null>(null),
    detailRef = useRef<Detail | null>(null);
  // External editor transactions may precede React's next paint. Commands must
  // use the latest edit, rather than a captured render of the form.
  function setForm(next: SetStateAction<Form | null>) {
    const value = typeof next === "function" ? next(formRef.current) : next;
    formRef.current = value;
    setFormState(value);
  }
  function dirtyNow() {
    return Boolean(
      detailRef.current &&
      formRef.current &&
      JSON.stringify(formRef.current) !==
        JSON.stringify(editable(detailRef.current)),
    );
  }
  const lock = useRef(false),
    creationKey = useRef<string | null>(null);
  const refresh = usePageRefresh();
  const dirty = Boolean(
    detail && form && JSON.stringify(form) !== JSON.stringify(editable(detail)),
  );
  const disabled = busy || parentBusy;
  useEffect(() => {
    onDirty?.(dirty);
    return () => onDirty?.(false);
  }, [dirty, onDirty]);
  useEffect(() => {
    onBusy?.(busy);
    return () => onBusy?.(false);
  }, [busy, onBusy]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setListError("");
    typedApi
      .GET("/api/v1/inspection-plans", {
        params: { query: { q: query, status, page } },
        cache: "no-store",
        signal: controller.signal,
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "计划列表读取失败"));
        const last = Math.max(1, Math.ceil(data.total / data.page_size));
        if (page > last) {
          setPage(last);
          return;
        }
        setRows(data);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setListError(e instanceof Error ? e.message : "计划列表读取失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [page, query, status, revision, refresh]);
  function install(row: Detail) {
    detailRef.current = row;
    setDetail(row);
    setForm(editable(row));
    setNames(
      Object.fromEntries(
        row.targets.map((t) => [
          t.station_id,
          t.accessible
            ? `${t.station_name} · ${t.operator_name}`
            : `场站#${t.station_id}（不可用或超出当前范围）`,
        ]),
      ),
    );
  }
  async function run(action: () => Promise<void>) {
    if (lock.current || parentBusy) return;
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : "计划操作失败");
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  async function select(target: number | "new" | "close") {
    if (disabled) return;
    if (dirtyNow()) {
      setChoice(target);
      return;
    }
    await perform(target);
  }
  async function perform(target: number | "new" | "close") {
    setChoice(null);
    if (target === "close") {
      detailRef.current = null;
      setDetail(null);
      setForm(null);
      return;
    }
    await run(async () => {
      if (target === "new") {
        creationKey.current ??= crypto.randomUUID();
        const { data, error } = await typedApi.POST(
          "/api/v1/inspection-plans",
          {
            headers: { "x-csrf-token": csrf },
            body: { request_key: creationKey.current },
          },
        );
        if (!data)
          throw new Error(apiError(error, "草稿创建失败，重试将继续同次创建"));
        creationKey.current = null;
        install(data);
        setStatus("draft");
        setPage(1);
        setQuery("");
        setQueryInput("");
        setRevision((n) => n + 1);
      } else {
        const { data, error } = await typedApi.GET(
          "/api/v1/inspection-plans/{plan_id}",
          { params: { path: { plan_id: target } }, cache: "no-store" },
        );
        if (!data) throw new Error(apiError(error, "计划读取失败"));
        install(data);
      }
    });
  }
  async function save() {
    const saved = detailRef.current,
      current = formRef.current;
    if (!saved || !current) return;
    await run(async () => {
      const due = current.dueLocal ? new Date(current.dueLocal) : null;
      if (due && !Number.isFinite(due.getTime()))
        throw new Error("请检查计划期限，未知可留空");
      const { data, error } = await typedApi.PUT(
        "/api/v1/inspection-plans/{plan_id}",
        {
          params: { path: { plan_id: saved.id } },
          headers: { "x-csrf-token": csrf },
          body: {
            version: saved.version,
            title: current.title,
            description: current.description,
            rich_document: current.description.trim()
              ? current.rich_document
              : null,
            station_ids: current.station_ids,
            due_at: due?.toISOString() ?? null,
          },
        },
      );
      if (!data)
        throw new Error(apiError(error, "草稿保存失败，本地编辑已保留"));
      install(data);
      setRevision((n) => n + 1);
    });
  }
  async function publish() {
    const saved = detailRef.current;
    if (!saved) return;
    if (dirtyNow()) {
      setError("本地修改尚未保存，请先保存再下发");
      return;
    }
    await run(async () => {
      const { data, error } = await typedApi.POST(
        "/api/v1/inspection-plans/{plan_id}/publish",
        {
          params: { path: { plan_id: saved.id } },
          headers: { "x-csrf-token": csrf },
          body: { version: saved.version },
        },
      );
      if (!data)
        throw new Error(
          apiError(
            error,
            "下发未获得成功回执；可重试同次发布，不会重复生成任务",
          ),
        );
      install(data);
      setStatus("published");
      setPage(1);
      setQuery("");
      setQueryInput("");
      setRevision((n) => n + 1);
      await onPublished();
    });
  }
  async function remove() {
    if (!detail) return;
    setChoice(null);
    await run(async () => {
      const { data, error } = await typedApi.DELETE(
        "/api/v1/inspection-plans/{plan_id}",
        {
          params: {
            path: { plan_id: detail.id },
            query: { version: detail.version },
          },
          headers: { "x-csrf-token": csrf },
        },
      );
      if (!data) throw new Error(apiError(error, "草稿删除失败"));
      install(data);
      setRevision((n) => n + 1);
    });
  }
  const stale = detail?.targets.some(
    (target) => !target.accessible || target.recipient_changed,
  );
  return (
    <section className="panel inspection-plans" aria-label="巡检计划与批量下发">
      <div className="panel-title">
        <div>
          <h2>巡检计划与批量下发</h2>
          <p>
            先保存草稿、核对目标，再为每个场站生成独立任务。未发布内容仅创建者可见。
          </p>
        </div>
        <button
          type="button"
          className="outline"
          disabled={disabled}
          onClick={() => void select("new")}
        >
          新建巡检计划
        </button>
      </div>
      <div className="record-pane" hidden={!!detail}>
        <form
          className="plan-list-filters"
          onSubmit={(e) => {
            e.preventDefault();
            setPage(1);
            setQuery(queryInput);
          }}
        >
          <label>
            计划标题
            <input
              aria-label="计划标题筛选"
              value={queryInput}
              maxLength={150}
              onChange={(e) => setQueryInput(e.target.value)}
            />
          </label>
          <label>
            计划状态
            <select
              aria-label="计划状态筛选"
              value={status}
              onChange={(e) => {
                const v = e.target.value;
                if (v === "draft" || v === "published" || v === "deleted") {
                  setStatus(v);
                  setPage(1);
                }
              }}
            >
              {Object.entries(states).map(([key, value]) => (
                <option key={key} value={key}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <button className="outline" disabled={loading || disabled}>
            查询巡检计划
          </button>
          <button
            type="button"
            className="outline"
            disabled={loading || disabled}
            onClick={() => setRevision((n) => n + 1)}
          >
            刷新计划列表
          </button>
        </form>
        {listError && (
          <p role="alert" className="error">
            {listError}
          </p>
        )}
        {loading && <p role="status">正在读取计划列表…</p>}
        <div className="table-scroll">
          <table aria-label="我的巡检计划">
            <thead>
              <tr>
                <th>计划编号 / 标题</th>
                <th>状态</th>
                <th>目标场站</th>
                <th>最近保存</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {rows?.items.map((row) => (
                <tr key={row.id}>
                  <td>
                    PLAN-{row.id}
                    <small>{row.title || "未命名草稿"}</small>
                  </td>
                  <td>{states[row.status]}</td>
                  <td>{row.target_count}站</td>
                  <td>{clock(row.updated_at)}</td>
                  <td>
                    <button
                      type="button"
                      className="outline"
                      aria-label={`打开计划 PLAN-${row.id}`}
                      disabled={disabled}
                      onClick={() => void select(row.id)}
                    >
                      查看计划
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!loading && rows?.total === 0 && (
          <p className="empty">当前条件下没有我的巡检计划</p>
        )}
        <div className="device-pagination">
          <button
            type="button"
            className="outline"
            aria-label="巡检计划上一页"
            disabled={loading || disabled || page === 1}
            onClick={() => setPage(page - 1)}
          >
            上一页
          </button>
          <span>
            计划第{page}页 · 共{rows?.total ?? 0}条
          </span>
          <button
            type="button"
            className="outline"
            aria-label="巡检计划下一页"
            disabled={
              loading ||
              disabled ||
              !rows ||
              page * rows.page_size >= rows.total
            }
            onClick={() => setPage(page + 1)}
          >
            下一页
          </button>
        </div>
      </div>
      {choice !== null && (
        <div role="alert" className="plan-confirm">
          <p>
            {choice === "delete"
              ? "删除未发布草稿，不会生成企业任务。"
              : "当前计划有未保存修改，是否放弃后继续？"}
          </p>
          <button
            type="button"
            className="outline"
            disabled={disabled}
            onClick={() => setChoice(null)}
          >
            保留当前内容
          </button>
          <button
            type="button"
            className="outline"
            disabled={disabled}
            onClick={() =>
              void (choice === "delete" ? remove() : perform(choice))
            }
          >
            {choice === "delete" ? "确认删除草稿" : "放弃修改并继续"}
          </button>
        </div>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {detail && form && (
        <section
          className="inspection-plan-detail record-detail business-record-detail"
          aria-label="巡检计划详情"
          aria-busy={busy}
        >
          <div className="panel-title">
            <div>
              <h3>
                PLAN-{detail.id} · {detail.title || "未命名草稿"}
              </h3>
              <p>
                {states[detail.status]} · 记录版本 {detail.version} ·{" "}
                {dirty ? "本地修改未保存" : "内容已保存"}
              </p>
            </div>
            <button
              type="button"
              className="outline"
              disabled={disabled}
              onClick={() => void select("close")}
            >
              关闭计划详情
            </button>
          </div>
          {detail.status === "draft" ? (
            <div className="plan-editor">
              <div className="plan-field-grid">
                <label>
                  任务名称
                  <input
                    aria-label="计划任务名称"
                    value={form.title}
                    maxLength={150}
                    disabled={disabled}
                    onChange={(e) =>
                      setForm((old) =>
                        old ? { ...old, title: e.target.value } : old,
                      )
                    }
                  />
                </label>
                <label>
                  任务期限（可选，本地时间）
                  <input
                    aria-label="计划任务期限"
                    type="datetime-local"
                    step="0.001"
                    value={form.dueLocal}
                    disabled={disabled}
                    onChange={(e) =>
                      setForm((old) =>
                        old ? { ...old, dueLocal: e.target.value } : old,
                      )
                    }
                  />
                </label>
              </div>
              <p className="muted">
                可以先保存未填完的草稿；发布前需保存名称、巡检要求和目标。未设置期限的任务不计为逾期。
              </p>
              <RichEditor
                key={`plan-${detail.id}`}
                label="计划巡检要求"
                document={form.rich_document ?? null}
                body={form.description}
                articleId={null}
                images={[]}
                imageHelp="计划要求支持文字、列表和表格，不嵌入图片；可读正文最多4000字。"
                disabled={disabled}
                onChange={(rich_document, description) =>
                  setForm((old) =>
                    old ? { ...old, rich_document, description } : old,
                  )
                }
              />
              <Targets
                selected={form.station_ids}
                disabled={disabled}
                onToggle={(option, checked) => {
                  setForm((old) =>
                    old
                      ? {
                          ...old,
                          station_ids: checked
                            ? old.station_ids.includes(option.id)
                              ? old.station_ids
                              : [...old.station_ids, option.id]
                            : old.station_ids.filter((id) => id !== option.id),
                        }
                      : old,
                  );
                  setNames((old) => ({
                    ...old,
                    [option.id]: `${option.name} · ${option.operator_name}`,
                  }));
                }}
              />
              <section aria-label="已选计划场站">
                <h4>已选目标 {form.station_ids.length}站</h4>
                <ul className="plan-selected">
                  {form.station_ids.map((id) => (
                    <li key={id}>
                      <span>{names[id] || `场站#${id}`}</span>
                      <button
                        type="button"
                        className="outline"
                        aria-label={`移除计划场站 ${id}`}
                        disabled={disabled}
                        onClick={() =>
                          setForm((old) =>
                            old
                              ? {
                                  ...old,
                                  station_ids: old.station_ids.filter(
                                    (value) => value !== id,
                                  ),
                                }
                              : old,
                          )
                        }
                      >
                        移除
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
              {stale && (
                <p className="error">
                  保存的目标中有归属变化或不可访问的场站，请调整目标并重新保存核对。
                </p>
              )}
              {dirty && (
                <p className="muted">
                  修改尚未保存，请保存后再下发；刷新列表不会覆盖本地内容。
                </p>
              )}
              <div className="action-row">
                <button
                  type="button"
                  className="outline"
                  disabled={disabled || form.description.length > 4000}
                  onClick={() => void save()}
                >
                  保存计划草稿
                </button>
                <button
                  type="button"
                  className="outline"
                  disabled={
                    disabled ||
                    dirty ||
                    Boolean(stale) ||
                    !detail.title.trim() ||
                    !detail.description.trim() ||
                    !detail.targets.length
                  }
                  onClick={() => void publish()}
                >
                  批量下发巡检
                </button>
                <button
                  type="button"
                  className="outline"
                  disabled={disabled}
                  onClick={() => void select(detail.id)}
                >
                  重新载入已保存计划
                </button>
                <button
                  type="button"
                  className="outline"
                  disabled={disabled}
                  onClick={() => setChoice("delete")}
                >
                  删除计划草稿
                </button>
              </div>
            </div>
          ) : (
            <div className="plan-receipt">
              <RichContent
                document={detail.rich_document}
                body={detail.description}
                articleId={0}
                manage={false}
                imageSource={() => undefined}
              />
              <p>任务期限：{clock(detail.due_at)}</p>
              {detail.status === "published" && (
                <>
                  <p className="plan-success">
                    已下发{detail.published_count}站 ·{" "}
                    {clock(detail.published_at)}
                  </p>
                  <p className="muted">
                    下列状态为关联任务当前状态；不可访问目标隐藏名称及任务入口。
                  </p>
                  <ul className="plan-published-targets">
                    {detail.targets.map((target) => (
                      <li key={target.station_id}>
                        <span>
                          {target.accessible
                            ? `当前台账：${target.station_name} · ${target.operator_name}`
                            : `场站#${target.station_id}（当前不可访问）`}
                        </span>
                        {target.recipient_changed && (
                          <small className="muted">
                            场站归属已变化；已下发任务仍按原受派企业办理，不自动改派。
                          </small>
                        )}
                        {target.task_id !== null ? (
                          <button
                            type="button"
                            className="outline"
                            disabled={disabled}
                            onClick={() =>
                              void run(() => onOpenTask(target.task_id!))
                            }
                          >
                            办理任务 TASK-{target.task_id} ·{" "}
                            {taskStates[target.task_status || ""] ||
                              target.task_status}
                          </button>
                        ) : (
                          <span className="muted">任务不可访问或已不存在</span>
                        )}
                      </li>
                    ))}
                  </ul>
                </>
              )}
              <button
                type="button"
                className="outline"
                disabled={disabled}
                onClick={() => void select(detail.id)}
              >
                刷新计划回执
              </button>
            </div>
          )}
        </section>
      )}
    </section>
  );
}
