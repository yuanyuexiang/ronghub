"use client";
import { useEffect, useRef, useState } from "react";
import type { paths } from "@ronghub/api-schema";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { useCapability } from "./permissions";
import { usePageRefresh } from "./page-refresh";
import { RichEditor } from "./rich-editor";
import { RichContent, type RichDocument } from "./rich-document";

type Detail = ApiSchemas["HazardDetail"];
type Candidate = ApiSchemas["HazardCandidate"];
type Query = NonNullable<
  paths["/api/v1/hazards"]["get"]["parameters"]["query"]
>;
const emptyQuery: Query = { q: "", status: "", progress: "", severity: "" };
const progressName = {
  not_started: "未开始",
  in_progress: "整改中",
  completed: "企业已填报完成",
};
const actionName = {
  publish: "登记发布",
  start: "开始整改",
  reply: "完成反馈",
  close: "关闭跟踪",
};
function clock(value: string | null) {
  return value
    ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })
    : "未留存";
}
async function read<T>(
  request: Promise<{ data?: T; error?: unknown }>,
): Promise<T> {
  const { data, error } = await request;
  if (data === undefined) throw new Error(apiError(error, "隐患督办操作失败"));
  return data;
}

function HazardTarget({
  selected,
  onSelect,
  disabled,
}: {
  selected: Candidate | null;
  onSelect: (item: Candidate) => void;
  disabled: boolean;
}) {
  const [draft, setDraft] = useState(""),
    [q, setQ] = useState(""),
    [page, setPage] = useState(1);
  const [data, setData] = useState<ApiSchemas["HazardCandidates"] | null>(null),
    [loading, setLoading] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    read(
      typedApi.GET("/api/v1/hazards/stations", {
        params: { query: { q, page } },
        signal: controller.signal,
        cache: "no-store",
      }),
    )
      .then((value) => {
        if (!controller.signal.aborted) setData(value);
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setData(null);
          setError(error instanceof Error ? error.message : "目标读取失败");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [q, page]);
  return (
    <section className="hazard-target" aria-label="督办目标选择">
      <h4>选择隐患场站</h4>
      <p className="muted">
        仅显示授权范围内场站。目标企业须为所选场站当前归属运营商；跨企业委派规则待确认。
      </p>
      <div className="filter-row">
        <label>
          场站或运营商
          <input
            aria-label="督办目标检索"
            value={draft}
            disabled={disabled}
            onChange={(e) => setDraft(e.target.value)}
          />
        </label>
        <button
          type="button"
          className="outline"
          disabled={disabled || loading}
          onClick={() => {
            setQ(draft);
            setPage(1);
          }}
        >
          查找督办目标
        </button>
      </div>
      {selected && (
        <p className="notice">
          已选：{selected.name} · {selected.operator_name}
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="hazard-target-options" aria-busy={loading}>
        {data?.items.map((item) => (
          <button
            type="button"
            className="outline"
            key={item.id}
            disabled={disabled || loading}
            aria-pressed={selected?.id === item.id}
            onClick={() => onSelect(item)}
          >
            <strong>{item.name}</strong>
            <span>
              {item.operator_name} · {item.district}
            </span>
          </button>
        ))}
        {!loading && data?.total === 0 && <p>没有符合条件的授权场站</p>}
      </div>
      <div className="pagination">
        <span>
          目标第{page}页 · 共{data?.total ?? 0}条
        </span>
        <button
          type="button"
          className="outline"
          disabled={disabled || loading || page === 1}
          onClick={() => setPage((p) => p - 1)}
        >
          督办目标上一页
        </button>
        <button
          type="button"
          className="outline"
          disabled={
            disabled || loading || !data || page * data.page_size >= data.total
          }
          onClick={() => setPage((p) => p + 1)}
        >
          督办目标下一页
        </button>
      </div>
    </section>
  );
}

export function HazardPanel({ role, csrf }: { role: string; csrf: string }) {
  const canCreate = useCapability("hazards.create"),
    canReply = useCapability("hazards.reply"),
    canClose = useCapability("hazards.close"),
    canMaterials = useCapability("hazards.materials"),
    canExport = useCapability("hazards.export");
  const [draft, setDraft] = useState<Query>({ ...emptyQuery }),
    [applied, setApplied] = useState<Query>({ ...emptyQuery }),
    [page, setPage] = useState(1),
    [revision, setRevision] = useState(0);
  const refresh = usePageRefresh();
  const [list, setList] = useState<ApiSchemas["HazardPage"] | null>(null),
    [loading, setLoading] = useState(false),
    [listError, setListError] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [message, setMessage] = useState("");
  const lock = useRef(false),
    detailRequest = useRef<AbortController | null>(null),
    exportRequest = useRef<AbortController | null>(null),
    downloadUrl = useRef<string | null>(null);
  const [exporting, setExporting] = useState(false),
    exportLock = useRef(false);
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<Candidate | null>(null),
    [target, setTarget] = useState("");
  const [title, setTitle] = useState(""),
    [description, setDescription] = useState(""),
    [requirement, setRequirement] = useState<RichDocument | null>(null),
    [severity, setSeverity] = useState<"normal" | "major">("normal"),
    [due, setDue] = useState("");
  const [comment, setComment] = useState(""),
    [document, setDocument] = useState<RichDocument | null>(null),
    [name, setName] = useState(""),
    [phone, setPhone] = useState(""),
    [performed, setPerformed] = useState(""),
    [closing, setClosing] = useState(false);
  const dirty = Boolean(
    comment.trim() || name.trim() || phone.trim() || performed,
  );
  const [pendingDetail, setPendingDetail] = useState<number | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setListError("");
    read(
      typedApi.GET("/api/v1/hazards", {
        params: { query: { ...applied, page, page_size: 20 } },
        signal: controller.signal,
        cache: "no-store",
      }),
    )
      .then((value) => {
        if (!controller.signal.aborted) setList(value);
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setListError(error instanceof Error ? error.message : "隐患读取失败");
          setList(null);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [applied, page, revision, refresh]);
  useEffect(
    () => () => {
      detailRequest.current?.abort();
      exportRequest.current?.abort();
      if (downloadUrl.current) URL.revokeObjectURL(downloadUrl.current);
    },
    [],
  );
  function clearReply() {
    setComment("");
    setDocument(null);
    setName("");
    setPhone("");
    setPerformed("");
    setClosing(false);
  }
  async function run(operation: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await operation();
    } catch (error) {
      setError(error instanceof Error ? error.message : "操作失败");
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  async function open(id: number, discard = false) {
    if (busy) return;
    if (dirty && !discard) {
      setPendingDetail(id);
      return;
    }
    detailRequest.current?.abort();
    const controller = new AbortController();
    detailRequest.current = controller;
    await run(async () => {
      const value = await read(
        typedApi.GET("/api/v1/hazards/{case_id}", {
          params: { path: { case_id: id } },
          signal: controller.signal,
          cache: "no-store",
        }),
      );
      if (!controller.signal.aborted) {
        setDetail(value);
        clearReply();
        setPendingDetail(null);
      }
    });
  }
  async function submit(action: "start" | "reply" | "close") {
    if (!detail) return;
    const current = detail;
    await run(async () => {
      const value = await read(
        typedApi.POST("/api/v1/hazards/{case_id}/actions", {
          headers: { "x-csrf-token": csrf },
          params: { path: { case_id: current.id } },
          body: {
            version: current.version,
            action,
            comment: action === "start" ? "企业确认开始整改" : comment,
            rich_document: action === "start" ? null : document,
            ...(action === "reply"
              ? {
                  contact: { name, phone },
                  performed_at: performed
                    ? new Date(performed).toISOString()
                    : null,
                }
              : {}),
          },
        }),
      );
      setDetail(value);
      clearReply();
      setRevision((r) => r + 1);
      setMessage(
        action === "close"
          ? "跟踪已关闭，停止后续超期跟踪。"
          : action === "reply"
            ? "完成反馈已留存，监管跟踪仍继续。"
            : "已开始整改。",
      );
    });
  }
  async function upload(file: File) {
    if (!detail) return;
    const id = detail.id;
    await run(async () => {
      const form = new FormData();
      form.set("file", file);
      await read(
        typedApi.POST("/api/v1/hazards/{case_id}/attachments", {
          params: { path: { case_id: id } },
          headers: { "x-csrf-token": csrf },
          body: { file: file.name },
          bodySerializer: () => form,
        }),
      );
      const value = await read(
        typedApi.GET("/api/v1/hazards/{case_id}", {
          params: { path: { case_id: id } },
          cache: "no-store",
        }),
      );
      setDetail(value);
      setMessage("证明材料已上传，提交反馈后冻结。");
    });
  }
  async function remove(id: number) {
    if (!detail) return;
    const caseId = detail.id;
    await run(async () => {
      await read(
        typedApi.DELETE(
          "/api/v1/hazards/{case_id}/attachments/{attachment_id}",
          {
            params: { path: { case_id: caseId, attachment_id: id } },
            headers: { "x-csrf-token": csrf },
          },
        ),
      );
      setDetail(
        await read(
          typedApi.GET("/api/v1/hazards/{case_id}", {
            params: { path: { case_id: caseId } },
            cache: "no-store",
          }),
        ),
      );
    });
  }
  async function download() {
    if (exportLock.current || busy || loading || listError) return;
    exportLock.current = true;
    setExporting(true);
    setError("");
    const controller = new AbortController();
    exportRequest.current = controller;
    try {
      const value = await read(
        typedApi.GET("/api/v1/hazards/export", {
          params: { query: applied },
          parseAs: "blob",
          signal: controller.signal,
          cache: "no-store",
        }),
      );
      if (controller.signal.aborted) return;
      if (value.type.split(";")[0] !== "text/csv")
        throw new Error("未收到CSV文件，请重试");
      if (downloadUrl.current) URL.revokeObjectURL(downloadUrl.current);
      const url = URL.createObjectURL(value);
      downloadUrl.current = url;
      const anchor = window.document.createElement("a");
      anchor.href = url;
      anchor.download = "ronghub-hazards.csv";
      window.document.body.append(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => {
        URL.revokeObjectURL(url);
        if (downloadUrl.current === url) downloadUrl.current = null;
      }, 1000);
    } catch (error) {
      if (!controller.signal.aborted)
        setError(error instanceof Error ? error.message : "导出失败");
    } finally {
      exportLock.current = false;
      if (!controller.signal.aborted) setExporting(false);
    }
  }
  const pending =
    detail?.materials.filter((item) => item.action_id === null) ?? [];
  const images = detail
    ? pending
        .filter((item) =>
          ["image/png", "image/jpeg"].includes(item.content_type),
        )
        .map((item) => ({
          id: item.id,
          label: item.filename,
          url: `/api/v1/hazards/${detail.id}/attachments/${item.id}/download`,
        }))
    : [];
  return (
    <section className="panel hazard-panel" aria-label="隐患督办工作区">
      <div className="section-heading">
        <div>
          <span className="eyebrow">HAZARD SUPERVISION</span>
          <h2>隐患督办</h2>
          <p className="muted">
            整改进度由企业填报，跟踪由监管人员关闭。企业完成整改后，跟踪仍继续。
          </p>
        </div>
        <span className="badge">本地模拟</span>
      </div>
      <div className="hazard-principles">
        <div>
          <strong>01 登记跟踪</strong>
          <span>一般 / 重大挂牌督办</span>
        </div>
        <div>
          <strong>02 企业整改</strong>
          <span>开始整改 · 事实及证明留存</span>
        </div>
        <div>
          <strong>03 监管关闭</strong>
          <span>独立确认 · 停止跟踪告警</span>
        </div>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {message && (
        <p className="notice" role="status">
          {message}
        </p>
      )}
      {canCreate && role === "regulator" && (
        <details
          className="hazard-create"
          open={creating}
          onToggle={(event) => setCreating(event.currentTarget.open)}
        >
          <summary>登记新隐患</summary>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (dirty) {
                setError("请先保留或明确放弃当前督办反馈，再登记新隐患");
                return;
              }
              if (!selected) {
                setError("请选择隐患场站");
                return;
              }
              void run(async () => {
                const value = await read(
                  typedApi.POST("/api/v1/hazards", {
                    headers: { "x-csrf-token": csrf },
                    body: {
                      station_id: selected.id,
                      operator_id: Number(target),
                      title,
                      description,
                      rich_document: requirement,
                      severity,
                      due_at: new Date(due).toISOString(),
                    },
                  }),
                );
                setDetail(value);
                setCreating(false);
                clearReply();
                setTitle("");
                setDescription("");
                setRequirement(null);
                setDue("");
                setSelected(null);
                setTarget("");
                setPage(1);
                setRevision((r) => r + 1);
                setMessage("隐患已登记发布，进入跟踪中。");
              });
            }}
          >
            <fieldset disabled={busy}>
              <HazardTarget
                selected={selected}
                disabled={busy}
                onSelect={(item) => {
                  setSelected(item);
                  setTarget("");
                }}
              />
              <div className="form-grid">
                <label>
                  隐患名称
                  <input
                    aria-label="督办隐患名称"
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    maxLength={150}
                    required
                  />
                </label>
                <label>
                  目标运营商（请确认）
                  <select
                    aria-label="督办目标运营商"
                    value={target}
                    onChange={(e) => setTarget(e.target.value)}
                    required
                  >
                    <option value="">请选择目标运营商</option>
                    {selected && (
                      <option value={selected.operator_id}>
                        {selected.operator_name}
                      </option>
                    )}
                  </select>
                </label>
                <label>
                  隐患分级
                  <select
                    aria-label="督办隐患分级"
                    value={severity}
                    onChange={(e) =>
                      setSeverity(
                        e.target.value === "major" ? "major" : "normal",
                      )
                    }
                  >
                    <option value="normal">一般</option>
                    <option value="major">重大（挂牌督办）</option>
                  </select>
                </label>
                <label>
                  整改期限（设备本地时间）
                  <input
                    aria-label="督办整改期限"
                    type="datetime-local"
                    value={due}
                    onChange={(e) => setDue(e.target.value)}
                    required
                  />
                </label>
              </div>
              <p>隐患说明（最多4000字）</p>
              <RichEditor
                label="督办隐患说明"
                document={requirement}
                body={description}
                articleId={null}
                images={[]}
                disabled={busy}
                onChange={(value, plain) => {
                  setRequirement(value);
                  setDescription(plain);
                }}
              />
              <button
                disabled={busy || !selected || !target || !description.trim()}
                type="submit"
              >
                {busy ? "正在登记…" : "登记并发布督办"}
              </button>
            </fieldset>
          </form>
        </details>
      )}
      <form
        className="hazard-filters"
        aria-label="隐患督办筛选"
        onSubmit={(event) => {
          event.preventDefault();
          setApplied({ ...draft });
          setPage(1);
          setError("");
        }}
      >
        <fieldset disabled={busy || exporting}>
          <div className="filter-row">
            <label>
              隐患 / 场站 / 运营商
              <input
                aria-label="督办检索名称"
                value={draft.q ?? ""}
                onChange={(e) => setDraft({ ...draft, q: e.target.value })}
              />
            </label>
            <label>
              整改进度
              <select
                aria-label="督办整改进度筛选"
                value={draft.progress ?? ""}
                onChange={(e) => {
                  const progress = e.target.value;
                  if (
                    ["", "not_started", "in_progress", "completed"].includes(
                      progress,
                    )
                  )
                    setDraft({
                      ...draft,
                      progress:
                        progress === "completed"
                          ? "completed"
                          : progress === "in_progress"
                            ? "in_progress"
                            : progress === "not_started"
                              ? "not_started"
                              : "",
                    });
                }}
              >
                <option value="">全部进度</option>
                <option value="not_started">未开始</option>
                <option value="in_progress">整改中</option>
                <option value="completed">企业已填报完成</option>
              </select>
            </label>
            <label>
              跟踪状态
              <select
                aria-label="督办跟踪状态筛选"
                value={draft.status ?? ""}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    status:
                      e.target.value === "tracking"
                        ? "tracking"
                        : e.target.value === "closed"
                          ? "closed"
                          : "",
                  })
                }
              >
                <option value="">全部状态</option>
                <option value="tracking">跟踪中</option>
                <option value="closed">已关闭</option>
              </select>
            </label>
            <label>
              创建开始日（北京）
              <input
                aria-label="督办创建开始日"
                type="date"
                value={draft.created_from ?? ""}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    created_from: e.target.value || undefined,
                  })
                }
              />
            </label>
            <label>
              创建结束日（北京）
              <input
                aria-label="督办创建结束日"
                type="date"
                value={draft.created_to ?? ""}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    created_to: e.target.value || undefined,
                  })
                }
              />
            </label>
            <label>
              期限状态
              <select
                aria-label="督办期限状态筛选"
                value={draft.overdue === undefined ? "" : String(draft.overdue)}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    overdue:
                      e.target.value === ""
                        ? undefined
                        : e.target.value === "true",
                  })
                }
              >
                <option value="">全部期限</option>
                <option value="true">超期未完成</option>
                <option value="false">未超期 / 已停止跟踪</option>
              </select>
            </label>
            <button type="submit">查询督办</button>
            <button
              type="button"
              className="outline"
              onClick={() => {
                setDraft({ ...emptyQuery });
                setApplied({ ...emptyQuery });
                setPage(1);
                setError("");
              }}
            >
              重置督办筛选
            </button>
          </div>
        </fieldset>
      </form>
      {listError && (
        <p role="alert" className="error">
          {listError}
        </p>
      )}
      <div className="table-scroll" aria-busy={loading}>
        <table aria-label="隐患督办列表">
          <thead>
            <tr>
              <th>编号 / 隐患</th>
              <th>登记时场站 / 目标运营商</th>
              <th>分级</th>
              <th>整改进度</th>
              <th>跟踪状态</th>
              <th>期限（北京时间）</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {list?.items.map((item) => (
              <tr key={item.id}>
                <td>
                  <strong>HAZ-{item.id}</strong>
                  <p>{item.title}</p>
                  <small>{clock(item.created_at)}</small>
                </td>
                <td>
                  {item.station_name}
                  <p className="muted">{item.operator_name}</p>
                </td>
                <td>
                  <span className="badge">
                    {item.severity === "major" ? "重大 · 挂牌督办" : "一般"}
                  </span>
                </td>
                <td>{progressName[item.progress]}</td>
                <td>{item.status === "tracking" ? "跟踪中" : "已关闭"}</td>
                <td>
                  {clock(item.due_at)}
                  {item.overdue && (
                    <p className="error">超期未完成 · 通知待对接</p>
                  )}
                </td>
                <td>
                  <button
                    className="outline"
                    disabled={busy || loading}
                    onClick={() => void open(item.id)}
                  >
                    查看督办
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!loading && list?.total === 0 && (
          <p className="empty-state">当前查询没有隐患督办记录</p>
        )}
      </div>
      <div className="pagination">
        <span>
          督办第{page}页 · 共{list?.total ?? 0}条
        </span>
        <button
          className="outline"
          disabled={busy || loading || page === 1}
          onClick={() => setPage((p) => p - 1)}
        >
          督办上一页
        </button>
        <button
          className="outline"
          disabled={
            busy || loading || !list || page * list.page_size >= list.total
          }
          onClick={() => setPage((p) => p + 1)}
        >
          督办下一页
        </button>
        {canExport && (
          <button
            className="outline"
            disabled={busy || loading || exporting || Boolean(listError)}
            onClick={() => void download()}
          >
            {exporting ? "正在导出…" : "导出督办 CSV"}
          </button>
        )}
      </div>
      <p className="muted">
        导出使用已应用查询的全部结果，最多10000条。修改筛选后请点击查询；超期标记不是短信已发送的证明。
      </p>
      {dirty && (
        <button
          type="button"
          className="outline"
          disabled={busy}
          onClick={() => {
            clearReply();
            setPendingDetail(null);
          }}
        >
          放弃当前督办反馈
        </button>
      )}
      {pendingDetail !== null && (
        <div className="notice" role="alert">
          <p>当前反馈有未保存修改，切换后将放弃这些输入，已上传材料仍保留。</p>
          <button
            className="outline"
            disabled={busy}
            onClick={() => setPendingDetail(null)}
          >
            保留督办反馈
          </button>
          <button
            disabled={busy}
            onClick={() => void open(pendingDetail, true)}
          >
            放弃反馈并切换督办
          </button>
        </div>
      )}
      {detail && (
        <section className="hazard-detail" aria-label="隐患督办详情">
          <div className="section-heading">
            <div>
              <span className="eyebrow">HAZ-{detail.id}</span>
              <h3>{detail.title}</h3>
            </div>
            <button
              className="outline"
              disabled={busy}
              onClick={() => void open(detail.id)}
            >
              重新载入督办详情
            </button>
          </div>
          <div className="hazard-facts">
            <div>
              <span>企业整改</span>
              <strong>{progressName[detail.progress]}</strong>
            </div>
            <div>
              <span>监管跟踪</span>
              <strong>
                {detail.status === "tracking" ? "跟踪中" : "已关闭"}
              </strong>
            </div>
            <div>
              <span>目标运营商</span>
              <strong>{detail.operator_name}</strong>
            </div>
            <div>
              <span>整改期限（北京）</span>
              <strong>{clock(detail.due_at)}</strong>
            </div>
          </div>
          <p className="muted">
            登记时场站：{detail.station_name}
            。名称为登记快照，归属变更不会自动委派到其他企业。关闭时间（北京）：
            {clock(detail.closed_at)}
          </p>
          <RichContent
            document={detail.rich_document}
            body={detail.description}
            articleId={0}
            manage={false}
          />
          <section className="materials" aria-label="督办证明材料">
            <h4>证明材料</h4>
            <p className="muted">
              PNG、JPG、PDF，单份5MiB，最多30份。每次反馈须提交本次新材料，已归档材料不覆盖。
            </p>
            {detail.materials.map((item) => (
              <div className="material-row" key={item.id}>
                <a
                  href={`/api/v1/hazards/${detail.id}/attachments/${item.id}/download`}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {item.filename}
                </a>
                <span>
                  {item.action_id === null
                    ? "本次未提交"
                    : `已归档 · 动作${item.action_id}`}
                </span>
                {canMaterials &&
                  role === "operator" &&
                  detail.status === "tracking" &&
                  item.action_id === null && (
                    <button
                      className="outline"
                      disabled={busy}
                      onClick={() => void remove(item.id)}
                    >
                      删除督办材料 {item.filename}
                    </button>
                  )}
              </div>
            ))}
            {canMaterials &&
              role === "operator" &&
              detail.status === "tracking" && (
                <label>
                  上传督办证明材料
                  <input
                    type="file"
                    aria-label="上传督办证明材料"
                    accept=".png,.jpg,.jpeg,.pdf"
                    disabled={busy}
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      event.target.value = "";
                      if (file) void upload(file);
                    }}
                  />
                </label>
              )}
          </section>
          {role === "operator" && canReply && detail.status === "tracking" && (
            <section className="hazard-feedback">
              <h4>企业整改反馈</h4>
              {detail.progress === "not_started" && (
                <button
                  className="outline"
                  disabled={busy || dirty}
                  onClick={() => void submit("start")}
                >
                  确认开始整改
                </button>
              )}
              <p className="muted">
                提交表示企业填报已完成；监管仍继续跟踪。执行信息按实际情况填写，不由系统时间代替。
              </p>
              <fieldset disabled={busy}>
                <div className="form-grid">
                  <label>
                    执行联系人
                    <input
                      aria-label="督办执行联系人"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      maxLength={100}
                    />
                  </label>
                  <label>
                    联系方式
                    <input
                      aria-label="督办执行联系方式"
                      value={phone}
                      onChange={(e) => setPhone(e.target.value)}
                      maxLength={80}
                    />
                  </label>
                  <label>
                    完成时间（设备本地时间）
                    <input
                      aria-label="督办完成时间"
                      type="datetime-local"
                      value={performed}
                      onChange={(e) => setPerformed(e.target.value)}
                    />
                  </label>
                </div>
                <RichEditor
                  label="督办整改结果"
                  document={document}
                  body={comment}
                  articleId={null}
                  images={images}
                  disabled={busy}
                  onChange={(value, plain) => {
                    setDocument(value);
                    setComment(plain);
                  }}
                />
                <button
                  disabled={
                    busy ||
                    !name.trim() ||
                    !phone.trim() ||
                    !performed ||
                    !comment.trim() ||
                    pending.length === 0
                  }
                  onClick={() => void submit("reply")}
                >
                  提交完成反馈
                </button>
              </fieldset>
            </section>
          )}
          {role === "regulator" && canClose && detail.status === "tracking" && (
            <section className="hazard-close">
              <h4>关闭监管跟踪</h4>
              <p className="muted">
                可独立于企业整改进度关闭。请填写依据；关闭不代表企业已完成，也不补填完成时间。
              </p>
              <label>
                关闭依据
                <textarea
                  aria-label="督办关闭依据"
                  value={comment}
                  disabled={busy}
                  onChange={(e) => {
                    setComment(e.target.value);
                    setDocument(null);
                    setClosing(false);
                  }}
                  maxLength={4000}
                />
              </label>
              <button
                className="outline"
                disabled={busy || !comment.trim()}
                onClick={() => setClosing(true)}
              >
                关闭督办跟踪
              </button>
              {closing && (
                <div
                  role="alertdialog"
                  aria-label="关闭督办确认"
                  className="notice"
                >
                  <p>
                    关闭后不再进行跟踪告警，企业不能继续回复或上传材料。确认关闭？
                  </p>
                  <button
                    className="outline"
                    disabled={busy}
                    onClick={() => setClosing(false)}
                  >
                    取消关闭督办
                  </button>
                  <button disabled={busy} onClick={() => void submit("close")}>
                    确认关闭督办
                  </button>
                </div>
              )}
            </section>
          )}
          <section className="hazard-history" aria-label="督办办理记录">
            <h4>办理记录</h4>
            {detail.history.map((item) => (
              <article key={item.id}>
                <div className="section-heading">
                  <strong>{actionName[item.action]}</strong>
                  <span>{clock(item.created_at)}（北京）</span>
                </div>
                <RichContent
                  document={item.rich_document}
                  body={item.comment}
                  articleId={0}
                  manage={false}
                  imageSource={(id) =>
                    detail.materials.some(
                      (material) =>
                        material.id === id && material.action_id === item.id,
                    )
                      ? `/api/v1/hazards/${detail.id}/attachments/${id}/download`
                      : undefined
                  }
                />
                {item.action === "reply" && (
                  <p className="muted">
                    执行联系人：{item.contact?.name ?? "未留存"} · 联系方式：
                    {item.contact?.phone ?? "未留存"} ·
                    用户填报完成时间（北京）：{clock(item.performed_at)}
                  </p>
                )}
              </article>
            ))}
          </section>
        </section>
      )}
    </section>
  );
}
