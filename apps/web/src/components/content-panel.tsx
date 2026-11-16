"use client";
import { usePageRefresh } from "./page-refresh";
import { NoticeRecords } from "./notice-records";
import { NoticeRecipients } from "./notice-recipients";
import { apiFetch } from "../lib/api-fetch";
import { useCapability, useGlobalScope } from "./permissions";
import { useEffect, useRef, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import dynamic from "next/dynamic";
const RichEditor = dynamic(
  () => import("./rich-editor").then((module) => module.RichEditor),
  { ssr: false, loading: () => <p className="muted">正在加载正文编辑器…</p> },
);
import {
  RichContent,
  documentFromEditor,
  editorDocument,
  type RichDocument,
} from "./rich-document";
type Article = ApiSchemas["ContentArticle"];
type Detail = ApiSchemas["ContentDetail"];
const kinds: Record<string, string> = {
  announcement: "公告",
  news: "资讯",
  contact: "联系方式",
  notice: "通知",
};
const statuses: Record<string, string> = {
  draft: "草稿",
  published: "已发布",
  withdrawn: "已撤下",
  deleted: "已删除（保留历史）",
};
const empty = {
  kind: "announcement",
  title: "",
  body: "",
  rich_document: null as RichDocument | null,
  audience: "all",
  operator_id: "",
  recipient_ids: [] as number[],
};
export function ContentPanel({ role, csrf }: { role: string; csrf: string }) {
  const globalScope = useGlobalScope();
  const canCreate = useCapability("content.create");
  const canEdit = useCapability("content.edit");
  const canPublish = useCapability("content.publish");
  const canWithdraw = useCapability("content.withdraw");
  const canDelete = useCapability("content.delete");
  const canExport = useCapability("content.export");
  const canManage = useCapability("content.manage") && globalScope,
    canReply = useCapability("content.reply");
  const [items, setItems] = useState<Article[]>([]),
    [operators, setOperators] = useState<{ id: number; name: string }[]>([]),
    [draft, setDraft] = useState(empty),
    [editing, setEditing] = useState<Article | null>(null),
    [showForm, setShowForm] = useState(false),
    [manage, setManage] = useState(canManage && role === "regulator"),
    [page, setPage] = useState(1),
    [total, setTotal] = useState(0),
    [q, setQ] = useState(""),
    [appliedQ, setAppliedQ] = useState(""),
    [includeDeleted, setIncludeDeleted] = useState(false),
    [selection, setSelection] = useState<Record<number, number>>({}),
    [batchPending, setBatchPending] = useState<{
      action: "publish" | "delete";
      items: { id: number; version: number }[];
    } | null>(null),
    [batchAt, setBatchAt] = useState(""),
    [recordsVersion, setRecordsVersion] = useState(0),
    [listLoading, setListLoading] = useState(false),
    [detail, setDetail] = useState<Detail | null>(null),
    [draftAssets, setDraftAssets] = useState<ApiSchemas["ContentAttachment"][]>(
      [],
    ),
    [reply, setReply] = useState(""),
    [replyContact, setReplyContact] = useState(""),
    [replyPhone, setReplyPhone] = useState(""),
    [replyFiles, setReplyFiles] = useState<File[]>([]),
    [publishAt, setPublishAt] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const listSequence = useRef(0),
    listController = useRef<AbortController | null>(null);
  const preservedPage = useRef<{
    page: number;
    q: string;
    includeDeleted: boolean;
    manage: boolean;
  } | null>(null);
  async function load() {
    const sequence = ++listSequence.current;
    listController.current?.abort();
    const controller = new AbortController();
    listController.current = controller;
    setListLoading(true);
    try {
      const { data, error } = await typedApi.GET("/api/v1/content", {
        params: {
          query: { manage, page, q: appliedQ, include_deleted: includeDeleted },
        },
        signal: controller.signal,
        cache: "no-store",
      });
      if (sequence !== listSequence.current || controller.signal.aborted)
        return;
      if (!data) throw new Error(apiError(error, "内容列表读取失败"));
      const last = Math.max(1, Math.ceil(data.total / 20));
      if (page > last) {
        preservedPage.current = {
          page: last,
          q: appliedQ,
          includeDeleted,
          manage,
        };
        setPage(last);
        return;
      }
      setItems(data.items);
      setTotal(data.total);
    } catch (error) {
      if (controller.signal.aborted || sequence !== listSequence.current)
        return;
      throw error;
    } finally {
      if (sequence === listSequence.current) setListLoading(false);
    }
  }
  async function applyBatch() {
    if (!batchPending) return;
    await run(async () => {
      const { data, error } = await typedApi.POST(
        "/api/v1/content/batch-actions",
        {
          body: {
            ...batchPending,
            published_at:
              batchPending.action === "publish" && batchAt
                ? new Date(batchAt + "+08:00").toISOString()
                : null,
          },
          headers: { "x-csrf-token": csrf },
        },
      );
      if (!data) throw new Error(apiError(error, "批量操作失败，整批未提交"));
      setBatchPending(null);
      setSelection({});
      setDetail(null);
      setBatchAt("");
      setRecordsVersion((v) => v + 1);
      await load();
    });
  }
  async function getDetail(id: number) {
    const { data, error } = await typedApi.GET("/api/v1/content/{article_id}", {
      params: { path: { article_id: id }, query: { manage } },
      cache: "no-store",
    });
    if (!data) throw new Error(apiError(error, "内容详情读取失败"));
    return data;
  }
  async function open(id: number) {
    setDetail(await getDetail(id));
    setReply("");
    setReplyContact("");
    setReplyPhone("");
    setReplyFiles([]);
    setPublishAt("");
  }
  async function assetChanged() {
    if (!editing) return;
    const data = await getDetail(editing.id);
    setEditing(data);
    setDetail(data);
    setDraftAssets(data.attachments);
  }
  async function uploadAsset(file: File) {
    if (!editing) return;
    await run(async () => {
      const body = new FormData();
      body.set("file", file);
      body.set("version", String(editing.version));
      const response = await apiFetch(
        `/api/v1/content/${editing.id}/attachments`,
        { method: "POST", headers: { "x-csrf-token": csrf }, body },
      );
      if (!response.ok)
        throw new Error(apiError(await response.json(), "附件上传失败"));
      await assetChanged();
    });
  }
  async function deleteAsset(id: number) {
    if (!editing) return;
    await run(async () => {
      const { data, error } = await typedApi.DELETE(
        "/api/v1/content/{article_id}/attachments/{attachment_id}",
        {
          params: {
            path: { article_id: editing.id, attachment_id: id },
            query: { version: editing.version },
          },
          headers: { "x-csrf-token": csrf },
        },
      );
      if (!data) throw new Error(apiError(error, "附件删除失败"));
      await assetChanged();
    });
  }
  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }
  const pageRefreshRevision = usePageRefresh();
  useEffect(() => {
    const kept = preservedPage.current;
    preservedPage.current = null;
    if (
      kept &&
      kept.page === page &&
      kept.q === appliedQ &&
      kept.includeDeleted === includeDeleted &&
      kept.manage === manage
    )
      return;
    setDetail(null);
  }, [manage, page, appliedQ, includeDeleted]);
  useEffect(() => {
    load().catch((e) => setError(e.message));
    return () => {
      ++listSequence.current;
      listController.current?.abort();
    };
  }, [manage, page, appliedQ, includeDeleted, pageRefreshRevision]);
  useEffect(() => {
    if (canManage && role === "regulator")
      typedApi
        .GET("/api/v1/operators")
        .then(({ data, error }) => {
          if (!data) throw new Error(apiError(error, "企业选项读取失败"));
          setOperators(data.items);
        })
        .catch((e) => setError(e.message));
  }, [role, pageRefreshRevision]);
  async function sendReply() {
    if (!detail) return;
    if (replyFiles.length) {
      const body = new FormData();
      body.set("body", reply);
      body.set("contact_name", replyContact);
      body.set("contact_phone", replyPhone);
      replyFiles.forEach((file) => body.append("files", file));
      const response = await apiFetch(
        `/api/v1/content/${detail.id}/replies/with-images`,
        { method: "POST", headers: { "x-csrf-token": csrf }, body },
      );
      const data = await response.json();
      if (!response.ok)
        throw new Error(
          typeof data.detail === "string" ? data.detail : "图文回复提交失败",
        );
    } else {
      const { data, error } = await typedApi.POST(
        "/api/v1/content/{article_id}/replies",
        {
          params: { path: { article_id: detail.id } },
          body: {
            body: reply,
            contact_name: replyContact || null,
            contact_phone: replyPhone || null,
          },
          headers: { "x-csrf-token": csrf },
        },
      );
      if (!data) throw new Error(apiError(error, "通知回复失败"));
    }
    await open(detail.id);
    setRecordsVersion((v) => v + 1);
  }
  async function act(action: string) {
    if (!detail) return;
    await run(async () => {
      const { data, error } = await typedApi.POST(
        "/api/v1/content/{article_id}/actions",
        {
          params: { path: { article_id: detail.id } },
          headers: { "x-csrf-token": csrf },
          body: {
            action,
            version: detail.version,
            published_at:
              action === "publish" && publishAt
                ? new Date(publishAt + "+08:00").toISOString()
                : null,
          },
        },
      );
      if (!data) throw new Error(apiError(error, "内容处理失败"));
      await load();
      setRecordsVersion((v) => v + 1);
      await open(detail.id);
    });
  }
  return (
    <div className="business-stack">
      {((canManage && manage && role === "regulator") ||
        role === "operator") && (
        <NoticeRecords
          refreshKey={recordsVersion}
          manage={manage && role === "regulator"}
          onOpen={(id) => run(() => open(id))}
        />
      )}
      {canManage && role === "regulator" && (
        <section className="panel">
          <div className="panel-title">
            <div>
              <h2>内容发布管理</h2>
              <p>草稿可修改，发布后冻结正文；支持定向企业与定时可见</p>
            </div>
            <div className="action-row">
              <button
                className="outline"
                onClick={() => {
                  setManage(!manage);
                  setPage(1);
                }}
              >
                切换到{manage ? "阅读" : "管理"}视图
              </button>
              {canCreate && (
                <button
                  className="outline"
                  onClick={() => {
                    setManage(true);
                    setDraft(empty);
                    setEditing(null);
                    setShowForm(!showForm);
                  }}
                >
                  新建内容草稿
                </button>
              )}
            </div>
          </div>
          {showForm && (editing ? canEdit : canCreate) && (
            <form
              className="business-form"
              onSubmit={(e) => {
                e.preventDefault();
                run(async () => {
                  if (draft.kind === "notice" && !draft.recipient_ids.length)
                    throw new Error("请至少选择一家通知收件企业");
                  const body = {
                    ...draft,
                    recipient_ids:
                      draft.kind === "notice" ? draft.recipient_ids : null,
                    operator_id:
                      draft.kind !== "notice" &&
                      draft.audience === "operator" &&
                      draft.operator_id
                        ? Number(draft.operator_id)
                        : null,
                  };
                  const result = editing
                    ? await typedApi.PATCH("/api/v1/content/{article_id}", {
                        params: { path: { article_id: editing.id } },
                        body: { ...body, version: editing.version },
                        headers: { "x-csrf-token": csrf },
                      })
                    : await typedApi.POST("/api/v1/content", {
                        body,
                        headers: { "x-csrf-token": csrf },
                      });
                  if (!result.data)
                    throw new Error(apiError(result.error, "草稿保存失败"));
                  const a = result.data;
                  setShowForm(false);
                  await load();
                  await open(a.id);
                });
              }}
            >
              <label>
                内容类型
                <select
                  aria-label="内容类型"
                  value={draft.kind}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      kind: e.target.value,
                      audience:
                        e.target.value === "notice"
                          ? "operator"
                          : draft.audience,
                      recipient_ids: [],
                      operator_id: "",
                    })
                  }
                >
                  {Object.entries(kinds).map(([v, n]) => (
                    <option key={v} value={v}>
                      {n}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                内容标题
                <input
                  aria-label="内容标题"
                  value={draft.title}
                  onChange={(e) =>
                    setDraft({ ...draft, title: e.target.value })
                  }
                  required
                  maxLength={150}
                />
              </label>
              <label>
                可见受众
                <select
                  aria-label="内容受众"
                  disabled={draft.kind === "notice"}
                  value={draft.audience}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      audience: e.target.value,
                      operator_id: "",
                    })
                  }
                >
                  <option value="all">三端共享</option>
                  <option value="regulator">监管人员</option>
                  <option value="association">协会人员</option>
                  <option value="operator">运营商</option>
                </select>
              </label>
              {draft.kind === "notice" && (
                <NoticeRecipients
                  value={draft.recipient_ids}
                  onChange={(recipient_ids) =>
                    setDraft({ ...draft, recipient_ids })
                  }
                  disabled={busy}
                />
              )}
              {draft.kind !== "notice" && draft.audience === "operator" && (
                <label>
                  定向企业
                  <select
                    aria-label="通知定向企业"
                    value={draft.operator_id}
                    onChange={(e) =>
                      setDraft({ ...draft, operator_id: e.target.value })
                    }
                  >
                    <option value="">全部运营商</option>
                    {operators.map((o) => (
                      <option key={o.id} value={o.id}>
                        {o.name}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <div className="wide-field">
                <label>内容正文</label>
                <RichEditor
                  key={editing?.id ?? "new"}
                  document={draft.rich_document}
                  body={draft.body}
                  articleId={editing?.id ?? null}
                  disabled={busy}
                  onChange={(rich_document, body) =>
                    setDraft((current) => ({ ...current, rich_document, body }))
                  }
                />
              </div>
              {editing && (
                <section className="wide-field article-assets">
                  <h3>正文图片与附件</h3>
                  <p className="muted">
                    支持PDF、PNG/JPG、DOCX、XLSX，单份10MiB；每篇最多10份、合计50MiB。发布后正文及附件冻结。
                  </p>
                  <input
                    type="file"
                    aria-label="上传内容附件"
                    disabled={busy}
                    accept=".pdf,.png,.jpg,.jpeg,.docx,.xlsx"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      e.target.value = "";
                      if (file) uploadAsset(file);
                    }}
                  />
                  {draftAssets.map((asset) => (
                    <div className="material-row" key={asset.id}>
                      <span>
                        {asset.filename} · {Math.ceil(asset.size / 1024)}KB
                      </span>
                      {["image/png", "image/jpeg"].includes(
                        asset.content_type,
                      ) && (
                        <button
                          type="button"
                          className="outline"
                          disabled={busy}
                          aria-label={`插入正文图片 ${asset.filename}`}
                          onClick={() =>
                            setDraft((current) => {
                              const doc =
                                current.rich_document ??
                                documentFromEditor(
                                  editorDocument(null, current.body),
                                );
                              return {
                                ...current,
                                rich_document: {
                                  ...doc,
                                  content: [
                                    ...doc.content,
                                    {
                                      type: "privateImage",
                                      attrs: {
                                        attachment_id: asset.id,
                                        alt: asset.filename,
                                      },
                                    },
                                  ],
                                },
                              };
                            })
                          }
                        >
                          插入正文图片
                        </button>
                      )}
                      <button
                        type="button"
                        className="outline"
                        disabled={busy}
                        onClick={() => deleteAsset(asset.id)}
                        aria-label={`删除内容附件 ${asset.filename}`}
                      >
                        删除附件
                      </button>
                    </div>
                  ))}
                </section>
              )}
              <p className="muted wide-field">
                保存后再发布；正在编辑的内容和新插入图片不会自动进入发布版本。
              </p>
              <button className="outline" disabled={busy}>
                保存内容草稿
              </button>
              <button
                type="button"
                className="outline"
                disabled={busy}
                onClick={() => {
                  setShowForm(false);
                  setEditing(null);
                  setDraft(empty);
                  setDraftAssets([]);
                }}
              >
                取消正文编辑
              </button>
            </form>
          )}
        </section>
      )}
      <section className="panel">
        <div className="panel-title">
          <div>
            <h2>{manage ? "发布内容台账" : "公告资讯与通知"}</h2>
            <p>
              {manage
                ? "包含草稿与定时发布记录"
                : "仅展示已到发布时间、当前账号有权查看的内容"}
            </p>
          </div>
          <form
            className="search"
            onSubmit={(e) => {
              e.preventDefault();
              if (page !== 1 || appliedQ !== q) {
                setAppliedQ(q);
                setPage(1);
              } else run(load);
            }}
          >
            <input
              aria-label="搜索内容标题"
              placeholder="搜索内容标题"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <button>查询内容</button>
          </form>
        </div>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {manage && (
          <div className="content-batch-bar">
            <label>
              <input
                type="checkbox"
                aria-label="显示已删除内容"
                checked={includeDeleted}
                onChange={(e) => {
                  setIncludeDeleted(e.target.checked);
                  setPage(1);
                }}
              />
              显示已删除内容
            </label>
            <span>
              已选{Object.keys(selection).length}/100份草稿（跨页保留版本）
            </span>
            {canPublish && (
              <button
                className="outline"
                disabled={busy || showForm || !Object.keys(selection).length}
                onClick={() =>
                  setBatchPending({
                    action: "publish",
                    items: Object.entries(selection).map(([id, version]) => ({
                      id: Number(id),
                      version,
                    })),
                  })
                }
              >
                批量发布草稿
              </button>
            )}
            {canDelete && (
              <button
                className="outline"
                disabled={busy || showForm || !Object.keys(selection).length}
                onClick={() =>
                  setBatchPending({
                    action: "delete",
                    items: Object.entries(selection).map(([id, version]) => ({
                      id: Number(id),
                      version,
                    })),
                  })
                }
              >
                批量删除草稿
              </button>
            )}
            {!!Object.keys(selection).length && (
              <button
                className="outline"
                disabled={busy}
                onClick={() => setSelection({})}
              >
                清空草稿选择
              </button>
            )}
            {canExport && !listLoading && (
              <a
                className="outline"
                href={`/api/v1/content/export?${new URLSearchParams({ q: appliedQ, include_deleted: String(includeDeleted) })}`}
              >
                导出当前查询内容元数据
              </a>
            )}
          </div>
        )}
        {batchPending && (
          <section className="content-batch-confirm" aria-label="批量操作确认">
            <h3>
              {batchPending.action === "publish"
                ? "确认批量发布"
                : "确认删除草稿"}
            </h3>
            <p>
              共{batchPending.items.length}份：
              {batchPending.items
                .map((row) => `#${row.id} V${row.version}`)
                .join("、")}
              。任一版本或状态变化，整批不提交。删除保留正文、附件和历史；发布后正文不可修改。
            </p>
            {batchPending.action === "publish" && (
              <label>
                批量生效时间（北京时间，可选）
                <input
                  type="datetime-local"
                  aria-label="批量生效时间"
                  value={batchAt}
                  onChange={(e) => setBatchAt(e.target.value)}
                  disabled={busy}
                />
              </label>
            )}
            <button className="primary" disabled={busy} onClick={applyBatch}>
              {batchPending.action === "publish"
                ? "确认发布所选草稿"
                : "确认删除所选草稿"}
            </button>
            <button
              className="outline"
              disabled={busy}
              onClick={() => setBatchPending(null)}
            >
              取消批量操作
            </button>
          </section>
        )}
        {listLoading && <p role="status">正在读取内容…</p>}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {["编号", "标题", "类型", "状态", "发布时间", "操作"].map(
                  (h) => (
                    <th key={h}>{h}</th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {items.map((a) => (
                <tr key={a.id}>
                  <td>
                    {manage &&
                      a.status === "draft" &&
                      (canPublish || canDelete) && (
                        <input
                          type="checkbox"
                          aria-label={`选择草稿 ${a.id}`}
                          checked={selection[a.id] !== undefined}
                          disabled={
                            busy ||
                            showForm ||
                            (selection[a.id] === undefined &&
                              Object.keys(selection).length >= 100)
                          }
                          onChange={(e) =>
                            setSelection((current) => {
                              const next = { ...current };
                              if (e.target.checked) next[a.id] = a.version;
                              else delete next[a.id];
                              return next;
                            })
                          }
                        />
                      )}
                    POST-{a.id}
                  </td>
                  <td>{a.title}</td>
                  <td>{kinds[a.kind]}</td>
                  <td>{statuses[a.status]}</td>
                  <td>
                    {a.published_at
                      ? new Date(a.published_at).toLocaleString("zh-CN")
                      : "未发布"}
                  </td>
                  <td>
                    <button
                      className="outline"
                      disabled={busy || showForm}
                      onClick={() => run(() => open(a.id))}
                    >
                      查看内容
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!items.length && <p className="empty">暂无可查看内容</p>}
        </div>
        <div className="business-form">
          <button
            className="outline"
            disabled={page <= 1 || busy}
            onClick={() => setPage(page - 1)}
          >
            上一页
          </button>
          <span>
            第{page}页 · 共{total}条
          </span>
          <button
            className="outline"
            disabled={page * 20 >= total || busy}
            onClick={() => setPage(page + 1)}
          >
            下一页
          </button>
        </div>
        {detail && (
          <div className="application-detail">
            <div className="panel-title">
              <div>
                <h2>{detail.title}</h2>
                <p>
                  {kinds[detail.kind]} · {statuses[detail.status]} · V
                  {detail.version}
                </p>
              </div>
              <button
                className="outline"
                disabled={busy}
                onClick={() => {
                  setDetail(null);
                  setReplyFiles([]);
                  setReply("");
                  setReplyContact("");
                  setReplyPhone("");
                }}
              >
                关闭内容详情
              </button>
            </div>
            <div
              className={
                manage
                  ? "detail-grid content-managing"
                  : "detail-grid content-reading"
              }
            >
              <div>
                <RichContent
                  document={detail.rich_document ?? null}
                  body={detail.body}
                  articleId={detail.id}
                  manage={manage}
                />
                {!!detail.attachments.length && (
                  <section className="article-assets">
                    <h3>内容附件</h3>
                    {detail.attachments.map((asset) => (
                      <div className="material-row" key={asset.id}>
                        <a
                          href={`/api/v1/content/${detail.id}/attachments/${asset.id}?manage=${manage}`}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {asset.filename}
                        </a>
                        <small>
                          {Math.ceil(asset.size / 1024)}KB ·{" "}
                          {asset.frozen ? "发布已冻结" : "草稿附件"}
                        </small>
                      </div>
                    ))}
                  </section>
                )}
                {(detail.kind === "notice" || detail.replies.length > 0) && (
                  <>
                    {!!detail.deliveries.length && (
                      <section>
                        <h3>逐企业站内记录</h3>
                        <p className="muted">
                          企业名称与联系人在发布操作时留存；未接入短信或邮件通道。
                        </p>
                        <div className="table-wrap">
                          <table className="notice-delivery-table">
                            <thead>
                              <tr>
                                <th>企业（发布快照）</th>
                                <th>计划生效时间</th>
                                <th>站内状态</th>
                                <th>回复情况</th>
                                <th>企业联系人（发布快照）</th>
                              </tr>
                            </thead>
                            <tbody>
                              {detail.deliveries.map((row) => (
                                <tr key={row.id}>
                                  <td>
                                    {row.operator_name}
                                    <small>
                                      记录 #{row.id} · 发布V
                                      {row.publication_version}
                                    </small>
                                  </td>
                                  <td>
                                    {new Date(row.scheduled_at).toLocaleString(
                                      "zh-CN",
                                      { timeZone: "Asia/Shanghai" },
                                    )}
                                  </td>
                                  <td>
                                    {
                                      {
                                        scheduled: "定时待生效",
                                        visible: "站内可见",
                                        withdrawn: "已撤下",
                                      }[row.state]
                                    }
                                  </td>
                                  <td>
                                    {row.reply_count}条回复
                                    {row.latest_reply_at && (
                                      <small>
                                        {new Date(
                                          row.latest_reply_at,
                                        ).toLocaleString("zh-CN", {
                                          timeZone: "Asia/Shanghai",
                                        })}
                                      </small>
                                    )}
                                  </td>
                                  <td>
                                    {row.contacts.length
                                      ? row.contacts.map((c, i) => (
                                          <small key={i}>
                                            {c.name ?? "未留存"} ·{" "}
                                            {c.phone ?? "未留存"}
                                          </small>
                                        ))
                                      : "未留存"}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </section>
                    )}
                    {detail.kind === "notice" && !detail.deliveries.length && (
                      <p className="muted">
                        此通知未留存逐企业记录，不补造历史推送结果。
                      </p>
                    )}
                    <h3>企业回复</h3>
                    {detail.replies?.map((r) => (
                      <article className="timeline notice-reply" key={r.id}>
                        <b>{r.operator_name}</b>
                        <small>提交账号：{r.actor_name}</small>
                        <small>
                          回复联系人：{r.contact_name ?? "未留存"} · 联系方式：
                          {r.contact_phone ?? "未留存"}
                        </small>
                        <p className="article-body">{r.body}</p>
                        {!!r.images?.length && (
                          <div className="reply-gallery">
                            {r.images.map((image) => {
                              const url = `/api/v1/content/${detail.id}/replies/${r.id}/images/${image.id}?manage=${manage}`;
                              // Direct private image requests preserve cookies and avoid a shared image-optimizer cache.
                              return (
                                <a
                                  key={image.id}
                                  href={url}
                                  target="_blank"
                                  rel="noreferrer"
                                >
                                  <img
                                    src={url}
                                    alt={`回复图片：${image.filename}`}
                                    loading="lazy"
                                  />
                                  <small>{image.filename}</small>
                                </a>
                              );
                            })}
                          </div>
                        )}
                        <small>
                          {new Date(r.created_at).toLocaleString("zh-CN")}
                        </small>
                      </article>
                    ))}
                    {!detail.replies?.length && (
                      <p className="muted">暂无回复</p>
                    )}
                    {canReply &&
                      role === "operator" &&
                      detail.kind === "notice" && (
                        <>
                          <label>
                            通知回复
                            <textarea
                              aria-label="通知回复"
                              value={reply}
                              onChange={(e) => setReply(e.target.value)}
                              maxLength={4000}
                              disabled={busy}
                            />
                          </label>
                          <label>
                            回复联系人（可选，与联系方式同时填写）
                            <input
                              aria-label="回复联系人"
                              value={replyContact}
                              onChange={(e) => setReplyContact(e.target.value)}
                              maxLength={100}
                              disabled={busy}
                            />
                          </label>
                          <label>
                            回复联系方式
                            <input
                              aria-label="回复联系方式"
                              value={replyPhone}
                              onChange={(e) => setReplyPhone(e.target.value)}
                              maxLength={80}
                              disabled={busy}
                            />
                          </label>
                          <label>
                            回复图片（可选）
                            <input
                              aria-label="通知回复图片"
                              type="file"
                              accept="image/png,image/jpeg"
                              multiple
                              disabled={busy}
                              onChange={(e) => {
                                const files = Array.from(e.target.files ?? []);
                                e.target.value = "";
                                if (
                                  files.some(
                                    (file) =>
                                      file.size > 5 * 1024 * 1024 ||
                                      !["image/png", "image/jpeg"].includes(
                                        file.type,
                                      ),
                                  )
                                ) {
                                  setError(
                                    "回复图片仅支持PNG/JPG，单张不超过5MiB",
                                  );
                                  return;
                                }
                                if (files.length + replyFiles.length > 10) {
                                  setError("每条回复最多10张图片");
                                  return;
                                }
                                setError("");
                                setReplyFiles([...replyFiles, ...files]);
                              }}
                            />
                          </label>
                          <p className="muted">
                            正文与图片一次提交，提交后留档；最多10张，每张5MiB，仅PNG/JPG。
                          </p>
                          {replyFiles.map((file, index) => (
                            <div className="material-row" key={index}>
                              <span>
                                {file.name} · {Math.ceil(file.size / 1024)}
                                KB（未提交）
                              </span>
                              <button
                                className="outline"
                                disabled={busy}
                                onClick={() =>
                                  setReplyFiles(
                                    replyFiles.filter((_, i) => i !== index),
                                  )
                                }
                              >
                                移除选图 {index + 1}
                              </button>
                            </div>
                          ))}
                          <button
                            className="outline"
                            disabled={busy || !reply.trim()}
                            onClick={() => run(sendReply)}
                          >
                            提交通知回复
                          </button>
                        </>
                      )}
                  </>
                )}
              </div>
              {manage && (
                <div>
                  {((detail.status === "draft" &&
                    (canEdit || canPublish || canDelete)) ||
                    (detail.status === "published" && canWithdraw)) && (
                    <h3>发布处理</h3>
                  )}
                  {detail.status === "draft" && (
                    <>
                      {canDelete && (
                        <button
                          className="outline"
                          disabled={busy || showForm}
                          onClick={() =>
                            setBatchPending({
                              action: "delete",
                              items: [
                                { id: detail.id, version: detail.version },
                              ],
                            })
                          }
                        >
                          删除此草稿
                        </button>
                      )}
                      {canEdit && (
                        <button
                          className="outline"
                          disabled={busy}
                          onClick={() => {
                            setEditing(detail);
                            setDraft({
                              kind: detail.kind,
                              title: detail.title,
                              body: detail.body,
                              rich_document: detail.rich_document ?? null,
                              audience:
                                detail.kind === "notice"
                                  ? "operator"
                                  : detail.audience,
                              recipient_ids:
                                detail.recipient_ids ??
                                (detail.operator_id
                                  ? [detail.operator_id]
                                  : []),
                              operator_id: detail.operator_id
                                ? String(detail.operator_id)
                                : "",
                            });
                            setDraftAssets(detail.attachments);
                            setShowForm(true);
                          }}
                        >
                          编辑草稿正文
                        </button>
                      )}
                      {canPublish && (
                        <>
                          <label>
                            发布时间（北京，留空立即发布）
                            <input
                              type="datetime-local"
                              aria-label="内容发布时间"
                              value={publishAt}
                              onChange={(e) => setPublishAt(e.target.value)}
                            />
                          </label>
                          <button
                            className="outline"
                            disabled={busy || showForm}
                            onClick={() => act("publish")}
                          >
                            发布内容
                          </button>
                        </>
                      )}
                    </>
                  )}
                  {canWithdraw && detail.status === "published" && (
                    <button
                      className="outline"
                      disabled={busy}
                      onClick={() => act("withdraw")}
                    >
                      撤下内容
                    </button>
                  )}
                  <h3>操作记录</h3>
                  {detail.history?.map((h, i) => (
                    <article className="timeline" key={i}>
                      <b>
                        {{
                          create: "创建草稿",
                          edit: "修改草稿",
                          publish: "发布",
                          withdraw: "撤下",
                          delete: "删除草稿（保留历史）",
                          attachment_upload: "上传内容附件",
                          attachment_delete: "删除内容附件",
                        }[h.action] || h.action}
                      </b>
                      <p>{new Date(h.created_at).toLocaleString("zh-CN")}</p>
                    </article>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
