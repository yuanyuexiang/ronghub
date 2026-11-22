"use client";
import { useBusinessView } from "./business-view-context";
import {
  useWorkspaceLeave,
  type LeaveGuardRegistrar,
} from "./business-workspace";
import { useRecordFocus } from "./use-record-focus";
import {
  useBusinessQueue,
  loadInspectionQueue,
  QueuePagination,
} from "./business-queue";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { useCapability } from "./permissions";
import { StationPicker } from "./station-picker";
import { useState } from "react";
import { RichEditor } from "./rich-editor";
import { RichContent, type RichDocument } from "./rich-document";
import { InspectionExport } from "./inspection-export";
import { InspectionPlans } from "./inspection-plans";
import { InspectionMaterials } from "./inspection-materials";
type Task = ApiSchemas["InspectionDetailResponse"];
const states: Record<string, string> = {
  assigned: "待整改",
  replied: "待复核",
  returned: "退回补充",
  closed: "已关闭",
};
const actions: Record<string, string> = {
  assign: "下发任务",
  reply: "整改回复",
  return: "退回补充",
  close: "复核关闭",
};
export function InspectionPanel({
  role,
  csrf,
  registerLeaveGuard,
}: {
  role: string;
  csrf: string;
  registerLeaveGuard?: LeaveGuardRegistrar;
}) {
  const canAssign = useCapability("inspections.assign");
  const canMaterials = useCapability("inspections.materials");
  const canReply = useCapability("inspections.reply");
  const canReturn = useCapability("inspections.return");
  const canClose = useCapability("inspections.close");
  const queue = useBusinessQueue(loadInspectionQueue);
  const tasks = queue.rows;
  const [sid, setSid] = useState(""),
    [title, setTitle] = useState(""),
    [description, setDescription] = useState(""),
    [requirementDocument, setRequirementDocument] =
      useState<RichDocument | null>(null),
    [commentDocument, setCommentDocument] = useState<RichDocument | null>(null),
    [contactName, setContactName] = useState(""),
    [contactPhone, setContactPhone] = useState(""),
    [performedAt, setPerformedAt] = useState(""),
    [images, setImages] = useState<
      { id: number; url: string; label: string }[]
    >([]),
    [severity, setSeverity] = useState("normal"),
    [due, setDue] = useState(""),
    [detail, setDetail] = useState<Task | null>(null),
    [comment, setComment] = useState(""),
    [busy, setBusy] = useState(false),
    [materialBusy, setMaterialBusy] = useState(false),
    [error, setError] = useState("");
  const [view, setView] = useBusinessView("tasks");
  const [taskOrigin, setTaskOrigin] = useState("tasks");
  const [planDirty, setPlanDirty] = useState(false);
  const [planBusy, setPlanBusy] = useState(false);
  const workspaceRef = useRecordFocus(detail?.id, view);
  const dirty = Boolean(
    comment.trim() ||
    contactName.trim() ||
    contactPhone.trim() ||
    performedAt ||
    (view === "quick" &&
      (sid ||
        title.trim() ||
        description.trim() ||
        due ||
        severity !== "normal")) ||
    planDirty,
  );
  const canLeave = useWorkspaceLeave(
    dirty,
    busy || materialBusy || planBusy,
    registerLeaveGuard,
  );
  function resetQuickAssignment() {
    setSid("");
    setTitle("");
    setDescription("");
    setRequirementDocument(null);
    setSeverity("normal");
    setDue("");
  }
  function closeDetail() {
    if (!canLeave()) return;
    setView(taskOrigin);
    setDetail(null);
    setComment("");
    setCommentDocument(null);
    setContactName("");
    setContactPhone("");
    setPerformedAt("");
  }
  function read<T>(result: { data?: T; error?: unknown }): T {
    if (result.data === undefined)
      throw new Error(apiError(result.error, "请检查输入内容"));
    return result.data;
  }
  async function readDetail(id: number) {
    return read(
      await typedApi.GET("/api/v1/inspections/{task_id}", {
        cache: "no-store",
        params: { path: { task_id: id } },
      }),
    );
  }
  async function load() {
    await queue.reload();
  }
  async function run(fn: () => Promise<void>) {
    if (busy || materialBusy) return;
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
  async function act(action: string) {
    if (!detail || busy || materialBusy) return;
    await run(async () => {
      read(
        await typedApi.POST("/api/v1/inspections/{task_id}/actions", {
          headers: { "x-csrf-token": csrf },
          params: { path: { task_id: detail.id } },
          body: {
            action,
            version: detail.version,
            comment,
            rich_document: commentDocument,
            ...(action === "reply"
              ? {
                  contact:
                    contactName.trim() || contactPhone.trim()
                      ? { name: contactName, phone: contactPhone }
                      : null,
                  performed_at: performedAt
                    ? new Date(performedAt).toISOString()
                    : null,
                }
              : {}),
          },
        }),
      );
      await load();
      setDetail(await readDetail(detail.id));
      setComment("");
      setCommentDocument(null);
      setContactName("");
      setContactPhone("");
      setPerformedAt("");
    });
  }
  return (
    <section className="business-stack safety-workspace" ref={workspaceRef}>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="record-pane" hidden={!!detail}>
        <div className="record-pane" hidden={view !== "plans"}>
          {canAssign && role === "regulator" && (
            <InspectionPlans
              csrf={csrf}
              onBack={() => setView("tasks")}
              onDirty={setPlanDirty}
              onBusy={setPlanBusy}
              parentBusy={busy || materialBusy}
              onPublished={load}
              onOpenTask={(id) =>
                run(async () => {
                  setTaskOrigin("plans");
                  setView("tasks");
                  setDetail(await readDetail(id));
                  setComment("");
                  setCommentDocument(null);
                  setContactName("");
                  setContactPhone("");
                  setPerformedAt("");
                })
              }
            />
          )}
        </div>
        <div className="record-pane" hidden={view !== "quick"}>
          {canAssign && role === "regulator" && (
            <section className="panel">
              <div className="panel-title">
                <h2>单站快捷下发</h2>
                <button
                  type="button"
                  className="outline"
                  onClick={() => {
                    if (!canLeave()) return;
                    resetQuickAssignment();
                    setView("tasks");
                  }}
                >
                  返回任务列表
                </button>
              </div>
              <form
                className="business-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  run(async () => {
                    const task = read(
                      await typedApi.POST("/api/v1/inspections", {
                        headers: { "x-csrf-token": csrf },
                        body: {
                          station_id: Number(sid),
                          title,
                          description,
                          rich_document: requirementDocument,
                          severity,
                          due_at: new Date(due).toISOString(),
                        },
                      }),
                    );
                    queue.reset();
                    setTaskOrigin("tasks");
                    setDetail(await readDetail(task.id));
                    resetQuickAssignment();
                  });
                }}
              >
                <StationPicker
                  label="巡检场站"
                  value={sid}
                  onChange={setSid}
                  kind="inspection"
                />
                <label>
                  任务标题
                  <input
                    aria-label="巡检标题"
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    maxLength={150}
                    required
                  />
                </label>
                <label>
                  隐患等级
                  <select
                    value={severity}
                    onChange={(e) => setSeverity(e.target.value)}
                  >
                    <option value="normal">一般巡检</option>
                    <option value="major">重大隐患巡检</option>
                  </select>
                </label>
                <label>
                  整改期限（本地时间）
                  <input
                    aria-label="巡检期限"
                    type="datetime-local"
                    value={due}
                    onChange={(e) => setDue(e.target.value)}
                    required
                  />
                </label>
                <div className="inspection-requirement-editor">
                  <p>任务要求（可读内容最多4000字）</p>
                  <RichEditor
                    document={requirementDocument}
                    body={description}
                    articleId={null}
                    images={[]}
                    label="巡检要求"
                    imageHelp="任务要求暂不嵌入图片，可使用格式化文字、列表或表格。"
                    disabled={busy || materialBusy}
                    onChange={(value, plain) => {
                      setRequirementDocument(value);
                      setDescription(plain);
                    }}
                  />
                </div>
                <button
                  className="outline"
                  disabled={
                    busy ||
                    materialBusy ||
                    !sid ||
                    !title.trim() ||
                    !description.trim() ||
                    description.length > 4000
                  }
                >
                  下发任务
                </button>
              </form>
            </section>
          )}
        </div>
      </div>
      <section className="panel" hidden={!detail && view !== "tasks"}>
        <div className="record-pane" hidden={!!detail || view !== "tasks"}>
          <div className="panel-title">
            <h2>{role === "operator" ? "企业整改任务" : "巡检任务跟踪"}</h2>
            <div className="action-row view-heading-actions">
              {canAssign && role === "regulator" && (
                <>
                  <button
                    type="button"
                    className="primary"
                    disabled={busy || materialBusy || planBusy}
                    onClick={() => setView("plans")}
                  >
                    巡检计划
                  </button>
                  <button
                    type="button"
                    className="outline"
                    disabled={busy || materialBusy || planBusy}
                    onClick={() => setView("quick")}
                  >
                    单站快捷下发
                  </button>
                </>
              )}
              <button
                className="outline"
                onClick={() => run(load)}
                disabled={busy || materialBusy}
              >
                刷新任务
              </button>
            </div>
          </div>
          <form
            className="application-filters"
            onSubmit={(event) => {
              event.preventDefault();
              if (busy || materialBusy) return;
              setDetail(null);
              queue.apply();
            }}
          >
            <label>
              任务、场站或企业
              <input
                aria-label="巡检检索名称"
                maxLength={150}
                value={queue.draft.q}
                onChange={(e) =>
                  queue.setDraft({ ...queue.draft, q: e.target.value })
                }
              />
            </label>
            <label>
              任务编号
              <input
                aria-label="巡检检索编号"
                type="number"
                min="1"
                value={queue.draft.record_id}
                onChange={(e) =>
                  queue.setDraft({ ...queue.draft, record_id: e.target.value })
                }
              />
            </label>
            <label>
              办理状态
              <select
                aria-label="巡检筛选状态"
                value={queue.draft.status}
                onChange={(e) =>
                  queue.setDraft({ ...queue.draft, status: e.target.value })
                }
              >
                <option value="">全部状态</option>
                {Object.entries(states).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              隐患等级
              <select
                aria-label="巡检筛选等级"
                value={queue.draft.kind}
                onChange={(e) =>
                  queue.setDraft({ ...queue.draft, kind: e.target.value })
                }
              >
                <option value="">全部等级</option>
                <option value="normal">一般巡检</option>
                <option value="major">重大隐患</option>
              </select>
            </label>
            <label>
              逾期情况
              <select
                aria-label="巡检筛选逾期"
                value={queue.draft.overdue || ""}
                onChange={(e) =>
                  queue.setDraft({ ...queue.draft, overdue: e.target.value })
                }
              >
                <option value="">全部任务</option>
                <option value="true">仅逾期</option>
                <option value="false">非逾期（含已关闭）</option>
              </select>
            </label>
            <label>
              反馈状态
              <select
                aria-label="巡检反馈状态"
                value={queue.draft.execution_state || ""}
                onChange={(event) => {
                  const value = event.target.value;
                  if (
                    value === "" ||
                    value === "reported" ||
                    value === "not_reported" ||
                    value === "unknown"
                  )
                    queue.setDraft({ ...queue.draft, execution_state: value });
                }}
              >
                <option value="">全部反馈情况</option>
                <option value="not_reported">未反馈</option>
                <option value="reported">已反馈</option>
                <option value="unknown">历史未留存</option>
              </select>
            </label>
            <fieldset className="queue-date-range">
              <legend>任务创建日期（北京时间）</legend>
              <label>
                开始日期
                <input
                  type="date"
                  aria-label="巡检创建开始日期"
                  value={queue.draft.created_from || ""}
                  max={queue.draft.created_to || undefined}
                  onChange={(event) =>
                    queue.setDraft({
                      ...queue.draft,
                      created_from: event.target.value,
                    })
                  }
                />
              </label>
              <label>
                结束日期
                <input
                  type="date"
                  aria-label="巡检创建结束日期"
                  value={queue.draft.created_to || ""}
                  min={queue.draft.created_from || undefined}
                  onChange={(event) =>
                    queue.setDraft({
                      ...queue.draft,
                      created_to: event.target.value,
                    })
                  }
                />
              </label>
            </fieldset>
            <button
              className="primary"
              disabled={busy || materialBusy || queue.loading}
            >
              查询巡检记录
            </button>
            <button
              type="button"
              className="outline"
              disabled={busy || materialBusy || queue.loading}
              onClick={() => {
                setDetail(null);
                queue.reset();
              }}
            >
              重置巡检筛选
            </button>
          </form>
          {(queue.query.created_from ||
            queue.query.created_to ||
            queue.query.execution_state) && (
            <p className="muted application-list-note">
              已查询创建日期 {queue.query.created_from || "不限"} 至{" "}
              {queue.query.created_to || "不限"} · 反馈
              {queue.query.execution_state === "reported"
                ? "已反馈"
                : queue.query.execution_state === "not_reported"
                  ? "未反馈"
                  : queue.query.execution_state === "unknown"
                    ? "历史未留存"
                    : "全部"}
            </p>
          )}
          <InspectionExport
            query={queue.query}
            busy={busy || materialBusy || queue.loading || Boolean(queue.error)}
          />
          {queue.error && (
            <p role="alert" className="error">
              {queue.error}
            </p>
          )}
          {queue.loading && (
            <p role="status" className="empty">
              正在加载巡检任务…
            </p>
          )}
          <div className="table-wrap">
            <table aria-label="巡检任务列表">
              <thead>
                <tr>
                  {[
                    "任务编号",
                    "任务标题",
                    "场站 / 企业",
                    "隐患等级",
                    "任务期限（北京时间）",
                    "办理状态",
                    "反馈状态",
                    "创建时间（北京时间）",
                    "最近反馈 / 填报执行时间（北京时间）",
                    "操作",
                  ].map((h) => (
                    <th key={h}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {tasks.map((t) => (
                  <tr key={t.id}>
                    <td>TASK-{t.id}</td>
                    <td>{t.title}</td>
                    <td>
                      {t.station_name}
                      <br />
                      <small>
                        {t.operator_name} / #{t.station_id}
                      </small>
                    </td>
                    <td>
                      <em
                        className={t.severity === "major" ? "amber" : "green"}
                      >
                        {t.severity === "major" ? "重大挂牌" : "一般巡检"}
                      </em>
                    </td>
                    <td>
                      {t.due_at
                        ? new Date(t.due_at).toLocaleString("zh-CN", {
                            timeZone: "Asia/Shanghai",
                          })
                        : "未设置期限"}
                    </td>
                    <td>
                      {states[t.status]}
                      {t.overdue && <em className="amber">已逾期</em>}
                    </td>
                    <td>
                      {t.execution_state === "reported"
                        ? "已反馈"
                        : t.execution_state === "not_reported"
                          ? "未反馈"
                          : "历史未留存"}
                    </td>
                    <td>
                      {new Date(t.created_at).toLocaleString("zh-CN", {
                        timeZone: "Asia/Shanghai",
                      })}
                    </td>
                    <td>
                      <span>
                        反馈：
                        {t.last_reply_submitted_at
                          ? new Date(t.last_reply_submitted_at).toLocaleString(
                              "zh-CN",
                              { timeZone: "Asia/Shanghai" },
                            )
                          : "未留存"}
                      </span>
                      <br />
                      <small>
                        填报：
                        {t.last_reported_performed_at
                          ? new Date(
                              t.last_reported_performed_at,
                            ).toLocaleString("zh-CN", {
                              timeZone: "Asia/Shanghai",
                            })
                          : "未留存"}
                      </small>
                    </td>
                    <td>
                      <button
                        className="outline"
                        disabled={busy || materialBusy}
                        onClick={() =>
                          run(async () => {
                            setTaskOrigin("tasks");
                            setDetail(await readDetail(t.id));
                            setComment("");
                            setCommentDocument(null);
                            setContactName("");
                            setContactPhone("");
                            setPerformedAt("");
                          })
                        }
                      >
                        处理任务
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!tasks.length && !queue.loading && !queue.error && (
              <p className="empty">当前条件下没有巡检记录</p>
            )}
          </div>
          <QueuePagination
            queue={queue}
            label="巡检"
            busy={busy || materialBusy}
          />
          <details className="table-note">
            <summary>查看任务状态说明</summary>
            <p className="muted">
              查询时按任务期限判断逾期。已关闭或无期限的任务不计逾期。
              反馈状态按留存回复统计，复核结果与实际执行时间在任务详情查看。
            </p>
          </details>
        </div>
        {detail && (
          <div
            className="record-detail business-record-detail"
            aria-label="巡检任务详情"
          >
            <div className="panel-title">
              <div>
                <h2>
                  TASK-{detail.id} · {detail.title}
                </h2>
                <p>{states[detail.status]}</p>
              </div>
              <button
                className="outline"
                disabled={busy || materialBusy}
                onClick={closeDetail}
              >
                关闭任务详情
              </button>
            </div>
            <section
              className="inspection-requirement"
              aria-label="已下发巡检要求"
            >
              <dl className="station-facts">
                <div>
                  <dt>场站</dt>
                  <dd>
                    {tasks.find((task) => task.id === detail.id)
                      ?.station_name ?? `#${detail.station_id}`}
                  </dd>
                </div>
                <div>
                  <dt>目标运营商</dt>
                  <dd>
                    {tasks.find((task) => task.id === detail.id)
                      ?.operator_name ?? `#${detail.operator_id}`}
                  </dd>
                </div>
                <div>
                  <dt>办理状态</dt>
                  <dd>{states[detail.status]}</dd>
                </div>
                <div>
                  <dt>任务期限</dt>
                  <dd>
                    {detail.due_at
                      ? new Date(detail.due_at).toLocaleString("zh-CN", {
                          timeZone: "Asia/Shanghai",
                        })
                      : "未设置"}
                  </dd>
                </div>
              </dl>
              <h3>巡检要求</h3>
              <RichContent
                document={detail.rich_document}
                body={detail.description}
                articleId={0}
                manage={false}
                imageSource={() => undefined}
              />
              <p className="muted">
                下发时间：
                {new Date(detail.created_at).toLocaleString("zh-CN", {
                  timeZone: "Asia/Shanghai",
                })}
                （北京时间）
              </p>
            </section>
            <div className="detail-grid">
              <div>
                <InspectionMaterials
                  key={detail.id}
                  taskId={detail.id}
                  version={detail.version}
                  csrf={csrf}
                  parentBusy={busy}
                  onBusy={setMaterialBusy}
                  onImages={setImages}
                  editable={
                    canMaterials &&
                    role === "operator" &&
                    ["assigned", "returned"].includes(detail.status)
                  }
                />
                <h3>处理记录</h3>
                {detail.history?.map((a) => (
                  <article key={a.id} className="timeline">
                    <b>{actions[a.action]}</b>
                    <RichContent
                      document={a.rich_document}
                      body={a.comment}
                      articleId={0}
                      manage={false}
                      imageSource={(id) =>
                        `/api/v1/inspections/${detail.id}/attachments/${id}/download`
                      }
                    />
                    {a.action === "reply" && (
                      <dl className="station-facts">
                        <div>
                          <dt>填报执行联系人</dt>
                          <dd>{a.contact?.name || "未填报"}</dd>
                        </div>
                        <div>
                          <dt>填报联系方式</dt>
                          <dd>{a.contact?.phone || "未填报"}</dd>
                        </div>
                        <div>
                          <dt>用户填报执行时间</dt>
                          <dd>
                            {a.performed_at
                              ? new Date(a.performed_at).toLocaleString(
                                  "zh-CN",
                                  { timeZone: "Asia/Shanghai" },
                                ) + "（北京时间）"
                              : "未填报"}
                          </dd>
                        </div>
                      </dl>
                    )}
                    <small>
                      系统记录时间：
                      {new Date(a.created_at).toLocaleString("zh-CN", {
                        timeZone: "Asia/Shanghai",
                      })}
                      （北京时间）
                    </small>
                  </article>
                ))}
              </div>
              <div>
                {detail.status === "closed" ? (
                  <p className="muted">任务已关闭，历史记录保留。</p>
                ) : (role === "operator" &&
                    canReply &&
                    ["assigned", "returned"].includes(detail.status)) ||
                  (role === "regulator" &&
                    (canReturn || canClose) &&
                    detail.status === "replied") ? (
                  <>
                    <div>
                      <p>整改或复核说明（可读内容最多4000字）</p>
                      <RichEditor
                        key={`inspection-reply-${detail.id}-${detail.version}`}
                        document={commentDocument}
                        body={comment}
                        articleId={null}
                        images={role === "operator" ? images : []}
                        label="巡检处理说明"
                        imageHelp={
                          role === "operator"
                            ? "可插入本次未提交的整改图片；回复后图片随记录留存。"
                            : "复核说明可使用文字格式、列表或表格。"
                        }
                        disabled={busy || materialBusy}
                        onChange={(value, plain) => {
                          setCommentDocument(value);
                          setComment(plain);
                        }}
                      />
                    </div>
                    {role === "operator" && (
                      <fieldset
                        className="inspection-execution"
                        disabled={busy || materialBusy}
                      >
                        <legend>本次执行信息</legend>
                        <label>
                          * 执行联系人
                          <input
                            aria-label="巡检执行联系人"
                            required
                            value={contactName}
                            maxLength={100}
                            onChange={(e) => setContactName(e.target.value)}
                          />
                        </label>
                        <label>
                          * 联系方式
                          <input
                            aria-label="巡检执行联系方式"
                            required
                            value={contactPhone}
                            maxLength={80}
                            onChange={(e) => setContactPhone(e.target.value)}
                          />
                        </label>
                        <label>
                          * 用户填报执行时间
                          <input
                            aria-label="巡检填报执行时间"
                            required
                            type="datetime-local"
                            value={performedAt}
                            onChange={(e) => setPerformedAt(e.target.value)}
                          />
                        </label>
                        <p className="muted">
                          新回复需填写联系人、联系方式及执行时间，并提交至少一份本次证明材料。时间按设备本地时区录入、按北京时间展示，不代表监管复核或正式完成认定；旧记录未留存时显示未知。
                        </p>
                      </fieldset>
                    )}
                    <div className="action-row">
                      {canReply &&
                        role === "operator" &&
                        ["assigned", "returned"].includes(detail.status) && (
                          <button
                            className="outline"
                            disabled={
                              busy ||
                              materialBusy ||
                              !comment.trim() ||
                              comment.length > 4000 ||
                              !contactName.trim() ||
                              !contactPhone.trim() ||
                              !performedAt
                            }
                            onClick={() => act("reply")}
                          >
                            提交整改回复
                          </button>
                        )}
                      {(canReturn || canClose) &&
                        role === "regulator" &&
                        detail.status === "replied" && (
                          <>
                            {canReturn && (
                              <button
                                className="outline"
                                disabled={
                                  busy ||
                                  materialBusy ||
                                  !comment.trim() ||
                                  comment.length > 4000
                                }
                                onClick={() => act("return")}
                              >
                                退回补充整改
                              </button>
                            )}
                            {canClose && (
                              <button
                                className="outline"
                                disabled={
                                  busy ||
                                  materialBusy ||
                                  !comment.trim() ||
                                  comment.length > 4000
                                }
                                onClick={() => act("close")}
                              >
                                复核关闭任务
                              </button>
                            )}
                          </>
                        )}
                    </div>
                  </>
                ) : (
                  <p className="muted">
                    当前状态无可办理动作，可查阅任务和历史证据。
                  </p>
                )}
              </div>
            </div>
          </div>
        )}
      </section>
    </section>
  );
}
