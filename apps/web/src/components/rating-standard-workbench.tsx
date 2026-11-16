"use client";
import { useEffect, useRef, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { RatingStandardForm } from "./rating-standard-form";
import { usePageRefresh } from "./page-refresh";
import { submissionTime } from "./business-queue";
type Draft = ApiSchemas["RatingDraftDetail"];
type Summary = ApiSchemas["RatingDraftSummary"];
type Standard = ApiSchemas["RatingStandardResponse"];
const labels = { draft: "编辑中", published: "已定稿", deleted: "已删除留档" };
function value<T>(
  response: { data?: T; error?: unknown },
  fallback: string,
): T {
  if (response.data === undefined)
    throw new Error(apiError(response.error, fallback));
  return response.data;
}
export function RatingStandardWorkbench({
  csrf,
  initial,
  parentBusy,
  onBusy,
  onCompleted,
  catalogRevision = 0,
}: {
  csrf: string;
  initial?: Standard;
  parentBusy: boolean;
  onBusy: (busy: boolean) => void;
  onCompleted: () => Promise<void>;
  catalogRevision?: number;
}) {
  const revision = usePageRefresh(),
    headers = { "x-csrf-token": csrf };
  const [draft, setDraft] = useState<Draft | null>(null),
    [editorKey, setEditorKey] = useState(0);
  const [dirty, setDirty] = useState(false),
    [networkBusy, setBusy] = useState(false),
    [completed, setCompleted] = useState<Standard | null>(null);
  const [error, setError] = useState(""),
    [readError, setReadError] = useState(""),
    [loading, setLoading] = useState(true);
  const [q, setQ] = useState(""),
    [status, setStatus] = useState<Summary["status"]>("draft");
  const [query, setQuery] = useState({
    q: "",
    status: "draft" as Summary["status"],
    page: 1,
  });
  const [page, setPage] = useState<ApiSchemas["RatingDraftPage"] | null>(null),
    [reload, setReload] = useState(0);
  const [archived, setArchived] = useState<Standard[]>([]);
  const [archiveError, setArchiveError] = useState("");
  const [archiveLoading, setArchiveLoading] = useState(true);
  const [pendingOpen, setPendingOpen] = useState<Summary | null>(null),
    [pendingDelete, setPendingDelete] = useState<Summary | null>(null);
  const inFlight = useRef(false);
  const locked = parentBusy || networkBusy || !!pendingOpen || !!pendingDelete;
  useEffect(() => {
    onBusy(networkBusy || !!pendingOpen || !!pendingDelete);
  }, [networkBusy, pendingOpen, pendingDelete, onBusy]);
  useEffect(() => () => onBusy(false), [onBusy]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setReadError("");
    typedApi
      .GET("/api/v1/ratings/standard-drafts", {
        signal: controller.signal,
        cache: "no-store",
        params: { query: { ...query, page_size: 10 } },
      })
      .then((response) => {
        if (controller.signal.aborted) return;
        const data = value(response, "标准草稿读取失败");
        const last = Math.max(1, Math.ceil(data.total / 10));
        if (query.page > last) {
          setQuery((previous) => ({ ...previous, page: last }));
          return;
        }
        setPage(data);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setReadError(error.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [query, revision, reload]);
  useEffect(() => {
    const controller = new AbortController();
    setArchiveError("");
    setArchiveLoading(true);
    typedApi
      .GET("/api/v1/ratings/standards", {
        signal: controller.signal,
        cache: "no-store",
        params: { query: { include_archived: true } },
      })
      .then((response) => {
        if (!controller.signal.aborted)
          setArchived(
            value(response, "归档标准读取失败").items.filter(
              (row) => row.archived,
            ),
          );
      })
      .catch((error) => {
        if (!controller.signal.aborted) setArchiveError(error.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setArchiveLoading(false);
      });
    return () => controller.abort();
  }, [revision, reload, catalogRevision]);
  async function operation(fn: () => Promise<void>, ownConfirmation = false) {
    if (inFlight.current || (parentBusy && !ownConfirmation)) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (error) {
      setError(
        error instanceof Error
          ? error.message
          : "办理未确认成功，请保留输入并核对记录",
      );
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  async function saveData(data: ApiSchemas["RatingDraftData"]) {
    const saved = draft
      ? value(
          await typedApi.PATCH("/api/v1/ratings/standard-drafts/{draft_id}", {
            headers,
            params: { path: { draft_id: draft.id } },
            body: { version: draft.version, data },
          }),
          "草稿保存失败，请保留输入并核对服务器版本",
        )
      : value(
          await typedApi.POST("/api/v1/ratings/standard-drafts", {
            headers,
            body: { data },
          }),
          "草稿暂存未确认成功，请先核对列表再重试",
        );
    setDraft(saved);
    setDirty(false);
    setReload((previous) => previous + 1);
    return saved;
  }
  async function loadDraft(row: Summary) {
    await operation(async () => {
      const saved = value(
        await typedApi.GET("/api/v1/ratings/standard-drafts/{draft_id}", {
          params: { path: { draft_id: row.id } },
          cache: "no-store",
        }),
        "草稿载入失败",
      );
      setDraft(saved);
      setDirty(false);
      setCompleted(null);
      setEditorKey((previous) => previous + 1);
      setPendingOpen(null);
    }, true);
  }
  function dataFrom(
    body: ApiSchemas["StandardInput"],
  ): ApiSchemas["RatingDraftData"] {
    return {
      code: body.code,
      name: body.name,
      version: String(body.version),
      categories:
        body.categories?.map((item) => ({
          ...item,
          weight: String(item.weight),
        })) ?? [],
      indicators: body.indicators.map((item) => ({
        ...item,
        category: item.category ?? "",
        weight: String(item.weight),
        max_score: String(item.max_score),
        bands: item.bands.map((band) => ({
          ...band,
          lower: String(band.lower),
          upper: band.upper === null ? null : String(band.upper),
          score: String(band.score),
        })),
      })),
      thresholds: body.thresholds.map((item) => ({
        minimum: String(item.minimum),
        stars: String(item.stars),
      })),
    };
  }
  async function finalize(body: ApiSchemas["StandardInput"]) {
    await operation(async () => {
      let standard: Standard;
      if (draft) {
        const current = dirty ? await saveData(dataFrom(body)) : draft;
        const response = value(
          await typedApi.POST(
            "/api/v1/ratings/standard-drafts/{draft_id}/finalize",
            {
              headers,
              params: { path: { draft_id: current.id } },
              body: { version: current.version },
            },
          ),
          "草稿定稿失败，已保存草稿仍保留",
        );
        setDraft(response.draft);
        standard = response.standard;
      } else
        standard = value(
          await typedApi.POST("/api/v1/ratings/standards", { headers, body }),
          "标准保存失败，请检查字段配置",
        );
      setCompleted(standard);
      setDirty(false);
      setReload((previous) => previous + 1);
      try {
        await onCompleted();
      } catch {
        setError("标准已保存，但列表刷新失败，请刷新后核对，勿重复创建");
      }
    });
  }
  return (
    <section className="standard-workbench" aria-label="标准草稿工作区">
      <div className="standard-draft-heading">
        <h3>标准编辑与暂存</h3>
        <p>
          {draft
            ? `草稿 #${draft.id} · ${labels[draft.status]} · 编辑版本 ${draft.version}`
            : "尚未暂存的新配置"}
          {dirty ? " · 有未保存修改" : ""}
        </p>
      </div>
      <p className="inline-note">
        暂存允许资料未完整；定稿会校验全部日期、字段与分档。顶栏刷新保留编辑内容与原编辑版本，冲突时请先核对，系统不会覆盖本地输入。
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {completed && (
        <p role="status">
          标准已保存：{completed.name} V{completed.version}
          。定稿版本保持不可变。
        </p>
      )}
      <RatingStandardForm
        csrf={csrf}
        key={editorKey}
        initial={initial}
        draft={draft?.data}
        busy={locked || !!completed || (!!draft && draft.status !== "draft")}
        onDirty={() => setDirty(true)}
        onDraftSave={async (data) => {
          await operation(async () => {
            await saveData(data);
          });
        }}
        onSave={finalize}
      />
      <div className="standard-draft-list">
        <h3>可恢复草稿与办理记录</h3>
        <form
          className="business-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (!locked) setQuery({ q, status, page: 1 });
          }}
        >
          <label>
            名称或标准代码
            <input
              aria-label="标准草稿检索"
              value={q}
              maxLength={150}
              disabled={locked}
              onChange={(event) => setQ(event.target.value)}
            />
          </label>
          <label>
            草稿状态
            <select
              aria-label="标准草稿状态"
              value={status}
              disabled={locked}
              onChange={(event) => {
                const value = event.target.value;
                if (
                  value === "draft" ||
                  value === "published" ||
                  value === "deleted"
                )
                  setStatus(value);
              }}
            >
              <option value="draft">编辑中</option>
              <option value="published">已定稿</option>
              <option value="deleted">已删除留档</option>
            </select>
          </label>
          <button className="outline" disabled={locked}>
            查询标准草稿
          </button>
        </form>
        {readError && (
          <p className="error" role="alert">
            {readError}
          </p>
        )}
        {loading ? (
          <p className="empty">正在读取标准草稿…</p>
        ) : (
          !readError && (
            <>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      {[
                        "草稿编号/名称",
                        "标准代码",
                        "状态/版本",
                        "更新（北京时间）",
                        "操作",
                      ].map((label) => (
                        <th key={label}>{label}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {page?.items.map((row) => (
                      <tr key={row.id}>
                        <td>
                          #{row.id} · {row.name || "未命名草稿"}
                        </td>
                        <td>{row.code || "尚未填写"}</td>
                        <td>
                          {labels[row.status]} · V{row.version}
                        </td>
                        <td>{submissionTime(row.updated_at)}</td>
                        <td>
                          <button
                            className="outline"
                            disabled={locked}
                            onClick={() => {
                              if (dirty) setPendingOpen(row);
                              else loadDraft(row);
                            }}
                          >
                            载入标准草稿 #{row.id}
                          </button>
                          {row.status === "draft" && (
                            <button
                              className="outline"
                              disabled={locked}
                              onClick={() => setPendingDelete(row)}
                            >
                              删除标准草稿 #{row.id}
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {page?.items.length === 0 && (
                  <p className="empty">当前查询没有标准草稿</p>
                )}
              </div>
              <div className="business-form">
                <button
                  className="outline"
                  disabled={locked || query.page === 1}
                  onClick={() => setQuery({ ...query, page: query.page - 1 })}
                >
                  标准草稿上一页
                </button>
                <span>
                  第{query.page}页 · 共{page?.total ?? 0}条
                </span>
                <button
                  className="outline"
                  disabled={locked || query.page * 10 >= (page?.total ?? 0)}
                  onClick={() => setQuery({ ...query, page: query.page + 1 })}
                >
                  标准草稿下一页
                </button>
              </div>
            </>
          )
        )}
      </div>
      {pendingOpen && (
        <div
          className="standard-confirm"
          role="region"
          aria-label="载入草稿确认"
        >
          <h3>替换当前编辑内容？</h3>
          <p>
            当前有未保存修改。先暂存可保留；确认后载入草稿 #{pendingOpen.id}{" "}
            的服务器内容。
          </p>
          <button
            className="outline"
            disabled={networkBusy}
            onClick={() => setPendingOpen(null)}
          >
            保留当前输入
          </button>{" "}
          <button
            className="primary"
            disabled={networkBusy}
            onClick={() => loadDraft(pendingOpen)}
          >
            确认载入草稿
          </button>
        </div>
      )}
      {pendingDelete && (
        <div
          className="standard-confirm"
          role="region"
          aria-label="删除草稿确认"
        >
          <h3>删除草稿 #{pendingDelete.id}？</h3>
          <p>
            仅删除编辑版本 V{pendingDelete.version}
            ，已保存内容与审计留档。若为当前草稿，未保存修改不会写入，确认后显示服务器留档内容。
          </p>
          <button
            className="outline"
            disabled={networkBusy}
            onClick={() => setPendingDelete(null)}
          >
            取消删除草稿
          </button>{" "}
          <button
            className="primary"
            disabled={networkBusy}
            onClick={() =>
              operation(async () => {
                const removed = value(
                  await typedApi.DELETE(
                    "/api/v1/ratings/standard-drafts/{draft_id}",
                    {
                      headers,
                      params: {
                        path: { draft_id: pendingDelete.id },
                        query: { version: pendingDelete.version },
                      },
                    },
                  ),
                  "草稿删除失败，请核对版本",
                );
                if (draft?.id === removed.id) {
                  setDraft(removed);
                  setDirty(false);
                  setEditorKey((previous) => previous + 1);
                }
                setPendingDelete(null);
                setReload((previous) => previous + 1);
              }, true)
            }
          >
            确认删除标准草稿
          </button>
        </div>
      )}
      <details className="standard-archive-list">
        <summary>
          已删除归档标准（
          {archiveError
            ? "读取失败"
            : archiveLoading
              ? "读取中"
              : archived.length}
          ）
        </summary>
        <p className="inline-note">
          仅管理人员可查看；标准代码与版本保留，不可重新启用或复用。
        </p>
        {archiveError && (
          <p className="error" role="alert">
            {archiveError}
          </p>
        )}
        {!archiveError &&
          !archiveLoading &&
          archived.map((row) => (
            <article key={row.id}>
              <b>
                {row.name} · {row.code} V{row.version}
              </b>
              <p>管理修订 {row.revision} · 已归档</p>
              <details>
                <summary>查看留档配置 #{row.id}</summary>
                {row.rules.indicators?.map((indicator) => (
                  <div key={indicator.code}>
                    <b>{indicator.name}</b>
                    <p>
                      {row.rules.indicator_status_version === 1
                        ? indicator.enabled
                          ? "已启用"
                          : "已停用"
                        : "历史指标状态未留存"}
                    </p>
                    <p>
                      {indicator.description ?? "历史说明未知"} · 生效
                      {indicator.effective_from ?? "未知"}
                    </p>
                    {indicator.bands?.map((band, index) => (
                      <p key={index}>
                        [{band.lower},{band.upper ?? "+∞"}) · {band.label} →{" "}
                        {band.score}分
                      </p>
                    ))}
                  </div>
                ))}
              </details>
            </article>
          ))}
      </details>
    </section>
  );
}
