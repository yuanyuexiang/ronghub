"use client";
import { WorkspaceViews } from "./business-workspace";
import { useRecordFocus } from "./use-record-focus";
import {
  ApplicationEvidencePanel,
  type ApplicationReviewState,
} from "./application-evidence-panel";
import { usePageRefresh } from "./page-refresh";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { useCapability } from "./permissions";
import { StationPicker } from "./station-picker";
import { DraftEditor } from "./draft-editor";
import { Attachments } from "./attachments";
import { useEffect, useState, useRef, FormEvent } from "react";
type Application = ApiSchemas["StationApplicationDetail"];
type Query = {
  q: string;
  status: string;
  kind: string;
  application_id: string;
  page: number;
};
const initialQuery: Query = {
  q: "",
  status: "",
  kind: "",
  application_id: "",
  page: 1,
};
const text = (value: unknown, fallback: string) =>
  typeof value === "string" && value ? value : fallback;
const kinds: Record<string, string> = {
  access: "场站接入",
  acceptance: "竣工验收",
  commission: "投运申请",
  decommission: "核销申请",
  change: "信息变更",
};
const statuses: Record<string, string> = {
  draft: "草稿",
  submitted: "待审核",
  rejected: "已驳回",
  approved: "已通过",
  withdrawn: "已撤回",
};
const actions: Record<string, string> = {
  create: "创建申请",
  edit: "编辑申请",
  material_category: "材料分类更新",
  submit: "提交审核",
  withdraw: "撤回申请",
  approve: "审核通过",
  reject: "审核驳回",
};
export function ApplicationPanel({
  role,
  csrf,
  registerLeaveGuard,
}: {
  role: string;
  csrf: string;
  registerLeaveGuard?: (guard: (() => boolean) | null) => void;
}) {
  const canCreate = useCapability("applications.create"),
    canEdit = useCapability("applications.edit"),
    canMaterials = useCapability("applications.materials"),
    canSubmit = useCapability("applications.submit"),
    canWithdraw = useCapability("applications.withdraw"),
    canReview = useCapability("applications.review");
  const [rows, setRows] = useState<ApiSchemas["StationApplicationRecord"][]>(
      [],
    ),
    [sid, setSid] = useState(""),
    [kind, setKind] = useState("access"),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [materialBusy, setMaterialBusy] = useState(false),
    [editorBusy, setEditorBusy] = useState(false),
    [detail, setDetail] = useState<Application | null>(null),
    [comment, setComment] = useState("");
  const [editorDirty, setEditorDirty] = useState(false);
  const [leaveRequested, setLeaveRequested] = useState(false);
  const hasUnsaved = editorDirty || !!comment.trim();
  useEffect(() => {
    if (!hasUnsaved) setLeaveRequested(false);
  }, [hasUnsaved]);
  useEffect(() => {
    registerLeaveGuard?.(
      () =>
        !(busy || materialBusy || editorBusy) &&
        (!hasUnsaved ||
          window.confirm("当前申请有未保存的修改或处理意见，确定离开此模块？")),
    );
    return () => registerLeaveGuard?.(null);
  }, [hasUnsaved, busy, materialBusy, editorBusy, registerLeaveGuard]);
  useEffect(() => {
    if (!hasUnsaved && !busy && !materialBusy && !editorBusy) return;
    const protect = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [hasUnsaved, busy, materialBusy, editorBusy]);
  function closeDetail() {
    setDetail(null);
    ++detailSequence.current;
    setLeaveRequested(false);
    setEditorDirty(false);
    setComment("");
    setError("");
  }
  function requestClose() {
    if (busy || materialBusy || editorBusy) return;
    if (hasUnsaved) setLeaveRequested(true);
    else closeDetail();
  }
  const workspaceRef = useRecordFocus(detail?.id);
  const [applicationView, setApplicationView] = useState("all");
  const canSource = useCapability("stations.read");
  const [materialRevision, setMaterialRevision] = useState(0);
  const [review, setReview] = useState<ApplicationReviewState | null>(null);
  const [query, setQuery] = useState(initialQuery),
    [draft, setDraft] = useState(initialQuery);
  const [total, setTotal] = useState(0),
    [listLoading, setListLoading] = useState(true);
  const listSequence = useRef(0),
    detailSequence = useRef(0);
  const queryRef = useRef(query);
  queryRef.current = query;
  async function load(target = queryRef.current) {
    const sequence = ++listSequence.current;
    setListLoading(true);
    setRows([]);
    try {
      const { data, error } = await typedApi.GET("/api/v1/applications", {
        cache: "no-store",
        params: {
          query: {
            q: target.q,
            status: target.status,
            kind: target.kind,
            application_id: target.application_id
              ? Number(target.application_id)
              : undefined,
            page: target.page,
            page_size: 20,
          },
        },
      });
      if (sequence !== listSequence.current) return;
      if (!data) throw new Error(apiError(error, "申请加载失败"));
      const lastPage = Math.max(1, Math.ceil(data.total / 20));
      if (target.page > lastPage) {
        setQuery({ ...target, page: lastPage });
        return;
      }
      setRows(data.items);
      setTotal(data.total);
    } catch (error) {
      if (sequence === listSequence.current) throw error;
    } finally {
      if (sequence === listSequence.current) setListLoading(false);
    }
  }
  const pageRefreshRevision = usePageRefresh();
  useEffect(() => {
    setError("");
    load(query).catch((e) =>
      setError(e instanceof Error ? e.message : "申请加载失败"),
    );
    return () => {
      ++listSequence.current;
    };
  }, [query, pageRefreshRevision]);
  function search(event: FormEvent) {
    event.preventDefault();
    if (busy || materialBusy || editorBusy) return;
    setDetail(null);
    ++detailSequence.current;
    setApplicationView(draft.kind || "all");
    setQuery({ ...draft, page: 1 });
  }
  async function create() {
    if (busy || materialBusy || editorBusy) return;
    setBusy(true);
    try {
      const { data, error } = await typedApi.POST("/api/v1/applications", {
        headers: { "x-csrf-token": csrf },
        body: {
          station_id: Number(sid),
          kind,
          payload: { note: "演示申请，材料要求待确认" },
        },
      });
      if (!data) throw new Error(apiError(error, "创建申请失败"));
      setApplicationView("all");
      setDraft(initialQuery);
      setQuery({ ...initialQuery });
      setDetail(null);
      ++detailSequence.current;
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建失败");
    } finally {
      setBusy(false);
    }
  }
  async function open(id: number) {
    if (materialBusy) return;
    const sequence = ++detailSequence.current;
    setError("");
    try {
      const { data, error } = await typedApi.GET(
        "/api/v1/applications/{application_id}",
        {
          cache: "no-store",
          params: { path: { application_id: id } },
        },
      );
      if (sequence !== detailSequence.current) return;
      if (!data) throw new Error(apiError(error, "无法读取申请详情"));
      setDetail(data);
      setComment("");
    } catch (e) {
      if (sequence === detailSequence.current)
        setError(e instanceof Error ? e.message : "请求失败");
    }
  }
  async function act(action: string) {
    if (!detail || busy || materialBusy || editorBusy) return;
    if (action === "submit" && editorDirty) {
      setError("请先保存申请信息后再提交");
      return;
    }
    const selectedSequence = detailSequence.current;
    setBusy(true);
    try {
      const { data, error } = await typedApi.POST(
        "/api/v1/applications/{application_id}/actions",
        {
          params: { path: { application_id: detail.id } },
          headers: { "x-csrf-token": csrf },
          body: {
            action,
            version: detail.version,
            comment,
            review_token:
              action === "submit" &&
              review?.applicationId === detail.id &&
              review.version === detail.version &&
              review.revision === materialRevision
                ? review.token
                : null,
          },
        },
      );
      if (!data) throw new Error(apiError(error, "处理失败"));
      await load();
      if (detailSequence.current === selectedSequence) await open(detail.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "处理失败");
      setMaterialRevision((old) => old + 1);
    } finally {
      setBusy(false);
    }
  }
  const renderedDetailSequence = detailSequence.current;
  useEffect(
    () => () => {
      ++detailSequence.current;
    },
    [],
  );
  return (
    <section ref={workspaceRef} className="panel application-workspace">
      <div className="record-pane" hidden={!!detail}>
        <WorkspaceViews
          label="场站申请业务视图"
          value={applicationView}
          disabled={busy || materialBusy || editorBusy || listLoading}
          items={[["all", "全部接入事项"], ...Object.entries(kinds)]}
          onChange={(view) => {
            setApplicationView(view);
            const next = { ...initialQuery, kind: view === "all" ? "" : view };
            setDraft(next);
            setQuery(next);
            if (view !== "all") setKind(view);
          }}
        />
        <div className="panel-title">
          <div>
            <h2>{role === "operator" ? "我的场站申请" : "场站申请审核"}</h2>
            <p>演示状态规则 · 不作为客户已批准流程，正式模式禁用</p>
          </div>
          {canCreate && role === "operator" && (
            <div className="search">
              <StationPicker label="申请场站" value={sid} onChange={setSid} />
              <select
                aria-label="申请类型"
                value={kind}
                onChange={(e) => setKind(e.target.value)}
              >
                {Object.entries(kinds).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
              <button
                disabled={busy || materialBusy || editorBusy || !sid}
                onClick={create}
              >
                新建草稿
              </button>
            </div>
          )}
        </div>
        <form className="application-filters" onSubmit={search}>
          <label>
            场站或企业名称
            <input
              aria-label="检索申请场站或企业"
              maxLength={150}
              value={draft.q}
              onChange={(e) => setDraft({ ...draft, q: e.target.value })}
            />
          </label>
          <label>
            申请编号
            <input
              aria-label="检索申请编号"
              type="number"
              min="1"
              value={draft.application_id}
              onChange={(e) =>
                setDraft({ ...draft, application_id: e.target.value })
              }
            />
          </label>
          <label>
            申请类型
            <select
              aria-label="筛选申请类型"
              value={draft.kind}
              onChange={(e) => setDraft({ ...draft, kind: e.target.value })}
            >
              <option value="">全部类型</option>
              {Object.entries(kinds).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            申请状态
            <select
              aria-label="筛选申请状态"
              value={draft.status}
              onChange={(e) => setDraft({ ...draft, status: e.target.value })}
            >
              <option value="">全部状态</option>
              {Object.entries(statuses).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <button
            className="primary"
            disabled={busy || materialBusy || editorBusy || listLoading}
          >
            查询申请
          </button>
          <button
            type="button"
            className="outline"
            disabled={busy || materialBusy || editorBusy || listLoading}
            onClick={() => {
              setApplicationView("all");
              setDraft(initialQuery);
              setQuery({ ...initialQuery });
              setDetail(null);
              ++detailSequence.current;
            }}
          >
            重置申请筛选
          </button>
        </form>
        <p className="muted application-list-note">
          场站和企业名称为当前台账名称；申请编号与历史记录保留。创建草稿后返回全部申请第一页。
        </p>
        {listLoading && (
          <p className="empty" role="status">
            正在加载申请…
          </p>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {[
                  "申请编号",
                  "场站 / 企业",
                  "申请类型",
                  "状态",
                  "创建时间（北京）",
                  "操作",
                ].map((h) => (
                  <th key={h}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((a) => (
                <tr key={a.id}>
                  <td>APP-{a.id}</td>
                  <td>
                    {a.station_name}
                    <br />
                    <small className="muted">
                      {a.operator_name} · 场站 #{a.station_id}
                    </small>
                  </td>
                  <td>{kinds[a.kind]}</td>
                  <td>
                    <em className={a.status === "approved" ? "green" : "amber"}>
                      {statuses[a.status]}
                    </em>
                  </td>
                  <td>
                    {new Date(a.created_at).toLocaleString("zh-CN", {
                      timeZone: "Asia/Shanghai",
                      hour12: false,
                    })}
                  </td>
                  <td>
                    <button
                      className="outline"
                      disabled={busy || materialBusy || editorBusy}
                      onClick={() => open(a.id)}
                    >
                      查看处理
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!rows.length && !listLoading && !error && (
            <p className="empty">当前条件下没有申请记录</p>
          )}
        </div>
        <div className="action-row application-pagination">
          <button
            className="outline"
            disabled={
              busy ||
              materialBusy ||
              editorBusy ||
              listLoading ||
              query.page === 1
            }
            onClick={() => setQuery({ ...query, page: query.page - 1 })}
          >
            申请上一页
          </button>
          <span>
            申请第{query.page}页 · 共{total}条
          </span>
          <button
            className="outline"
            disabled={
              busy ||
              materialBusy ||
              editorBusy ||
              listLoading ||
              query.page * 20 >= total
            }
            onClick={() => setQuery({ ...query, page: query.page + 1 })}
          >
            申请下一页
          </button>
        </div>
      </div>
      {detail && (
        <div
          className="application-detail record-detail"
          role="region"
          aria-label="申请详情工作区"
        >
          {error && (
            <p className="error application-list-note" role="alert">
              {error}
            </p>
          )}
          {leaveRequested && (
            <div className="record-leave-warning" role="alert">
              <p>
                申请信息或处理意见尚未保存。返回列表将放弃这些修改，已上传材料仍保留。
              </p>
              <button
                className="outline"
                onClick={() => setLeaveRequested(false)}
              >
                继续编辑申请
              </button>
              <button className="outline" onClick={closeDetail}>
                放弃修改并返回申请列表
              </button>
            </div>
          )}
          <div className="record-backline">
            <button
              className="outline"
              disabled={busy || materialBusy || editorBusy}
              onClick={requestClose}
            >
              ← 返回申请列表
            </button>
            <span>
              {role === "operator"
                ? "场站申请 / 申请详情"
                : "接入管理 / 申请详情"}
            </span>
          </div>
          <div className="panel-title">
            <div>
              <h2>
                APP-{detail.id} · {kinds[detail.kind]}
              </h2>
              <p>
                {statuses[detail.status]} · 记录版本 {detail.version}
              </p>
            </div>
            <button
              className="outline"
              disabled={busy || materialBusy || editorBusy}
              onClick={requestClose}
            >
              关闭详情
            </button>
          </div>
          <dl className="record-summary-grid">
            <div>
              <dt>申请场站</dt>
              <dd>{detail.station_name}</dd>
            </div>
            <div>
              <dt>运营商</dt>
              <dd>{detail.operator_name}</dd>
            </div>
            <div>
              <dt>创建时间（北京）</dt>
              <dd>
                {new Date(detail.created_at).toLocaleString("zh-CN", {
                  timeZone: "Asia/Shanghai",
                  hour12: false,
                })}
              </dd>
            </div>
          </dl>
          <div className="detail-grid application-review-layout">
            <div className="application-review-content">
              <h3 className="record-section-title">场站与送审资料</h3>
              <div className="application-evidence-container">
                <ApplicationEvidencePanel
                  key={`evidence-${detail.id}`}
                  applicationId={detail.id}
                  version={detail.version}
                  status={detail.status}
                  revision={materialRevision}
                  onReview={setReview}
                />
              </div>
              {canEdit &&
                role === "operator" &&
                ["draft", "rejected"].includes(detail.status) && (
                  <DraftEditor
                    key={`${detail.id}-${detail.version}`}
                    application={detail}
                    csrf={csrf}
                    parentBusy={busy || materialBusy}
                    onBusy={setEditorBusy}
                    onDirty={setEditorDirty}
                    onSaved={async () => {
                      await load();
                      if (detailSequence.current === renderedDetailSequence)
                        await open(detail.id);
                    }}
                  />
                )}
              <Attachments
                key={detail.id}
                applicationId={detail.id}
                version={detail.version}
                parentBusy={busy || editorBusy}
                onBusy={setMaterialBusy}
                editable={
                  canMaterials &&
                  role === "operator" &&
                  ["draft", "rejected"].includes(detail.status)
                }
                csrf={csrf}
                categories={
                  review?.applicationId === detail.id ? review.categories : {}
                }
                onChanged={() => setMaterialRevision((old) => old + 1)}
                applicationVersion={detail.version}
              />
              <section className="materials">
                <h3>提交内容</h3>
                <p>{text(detail.payload.note, "无申请说明")}</p>
                {detail.kind === "change" && (
                  <p>
                    拟变更名称：{text(detail.payload.name, "未填写")}
                    <br />
                    拟变更地址：{text(detail.payload.address, "未填写")}
                  </p>
                )}
              </section>
            </div>
            <div className="application-review-actions">
              <h3>当前操作</h3>
              <p className="muted">操作受角色、所属企业和记录状态共同约束。</p>
              {(role === "operator" &&
                ((canSubmit && ["draft", "rejected"].includes(detail.status)) ||
                  (canWithdraw &&
                    ["draft", "submitted", "rejected"].includes(
                      detail.status,
                    )))) ||
              (role === "regulator" &&
                canReview &&
                detail.status === "submitted") ? (
                <label>
                  处理意见
                  <textarea
                    aria-label="处理意见"
                    value={comment}
                    onChange={(e) => setComment(e.target.value)}
                    maxLength={2000}
                    placeholder={
                      role === "regulator"
                        ? "审核操作必须填写意见"
                        : "可填写提交或撤回说明"
                    }
                  />
                </label>
              ) : (
                <p className="muted">
                  当前记录无可执行操作，可查看材料和处理历史。
                </p>
              )}
              {editorDirty && (
                <p className="muted">申请信息有未保存修改，请先保存后提交。</p>
              )}
              <div className="action-row">
                {canSubmit &&
                  role === "operator" &&
                  ["draft", "rejected"].includes(detail.status) && (
                    <button
                      disabled={
                        busy ||
                        materialBusy ||
                        editorBusy ||
                        editorDirty ||
                        (canSource &&
                          (!review ||
                            review.applicationId !== detail.id ||
                            review.version !== detail.version ||
                            review.revision !== materialRevision ||
                            review.busy ||
                            !review.token))
                      }
                      className="outline"
                      onClick={() => act("submit")}
                    >
                      提交审核
                    </button>
                  )}
                {canWithdraw &&
                  role === "operator" &&
                  ["draft", "submitted", "rejected"].includes(
                    detail.status,
                  ) && (
                    <button
                      disabled={busy || materialBusy || editorBusy}
                      className="outline"
                      onClick={() => act("withdraw")}
                    >
                      撤回申请
                    </button>
                  )}
                {canReview &&
                  role === "regulator" &&
                  detail.status === "submitted" && (
                    <>
                      <button
                        disabled={
                          busy || materialBusy || editorBusy || !comment.trim()
                        }
                        className="outline"
                        onClick={() => act("approve")}
                      >
                        审核通过
                      </button>
                      <button
                        disabled={
                          busy || materialBusy || editorBusy || !comment.trim()
                        }
                        className="outline"
                        onClick={() => act("reject")}
                      >
                        驳回申请
                      </button>
                    </>
                  )}
              </div>
              <h3>处理记录</h3>
              {detail.history?.map((h, i) => (
                <article className="timeline" key={i}>
                  <b>{actions[h.action] || h.action}</b>
                  <p>{h.comment || "无补充意见"}</p>
                  <small>
                    {new Date(h.created_at).toLocaleString("zh-CN")}
                  </small>
                </article>
              ))}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
