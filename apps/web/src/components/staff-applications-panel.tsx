"use client";
import { useRecordFocus } from "./use-record-focus";
import {
  useWorkspaceLeave,
  type LeaveGuardRegistrar,
} from "./business-workspace";
import { usePageRefresh } from "./page-refresh";
import { useEffect, useRef, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { StaffRolePicker } from "./staff-role-picker";
const roles = { regulator: "监管工作人员", association: "协会工作人员" };
const statuses = { pending: "待审核", approved: "已开通", rejected: "已驳回" };
export function StaffApplicationsPanel({
  csrf,
  registerLeaveGuard,
}: {
  csrf: string;
  registerLeaveGuard?: LeaveGuardRegistrar;
}) {
  const [draft, setDraft] = useState({ q: "", status: "", requested_role: "" }),
    [query, setQuery] = useState({ ...draft, page: 1 });
  const [data, setData] = useState<ApiSchemas["StaffApplicationPage"] | null>(
      null,
    ),
    [detail, setDetail] = useState<ApiSchemas["StaffApplicationDetail"] | null>(
      null,
    );
  const [loading, setLoading] = useState(true),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [revision, setRevision] = useState(0);
  const [profile, setProfile] = useState<ApiSchemas["StaffRoleOption"] | null>(
      null,
    ),
    [username, setUsername] = useState(""),
    [password, setPassword] = useState(""),
    [comment, setComment] = useState("");
  const [scope, setScope] = useState(""),
    [districts, setDistricts] = useState("");
  const workspaceRef = useRecordFocus(detail?.id);
  const canLeave = useWorkspaceLeave(
    Boolean(
      detail?.status === "pending" &&
      (profile || username || password || comment.trim() || scope || districts),
    ),
    busy,
    registerLeaveGuard,
  );
  const detailController = useRef<AbortController | null>(null);
  const preservedQuery = useRef<typeof query | null>(null);
  const pageRefreshRevision = usePageRefresh();
  useEffect(() => {
    if (preservedQuery.current === query) {
      preservedQuery.current = null;
      return;
    }
    detailController.current?.abort();
    setDetail(null);
    setPassword("");
  }, [query, revision]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setData(null);
    typedApi
      .GET("/api/v1/staff-applications", {
        params: { query: { ...query, page_size: 20 } },
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "人员申请读取失败"));
        const last = Math.max(1, Math.ceil(data.total / 20));
        if (query.page > last) {
          const next = { ...query, page: last };
          preservedQuery.current = next;
          setQuery(next);
          return;
        }
        setData(data);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [query, revision, pageRefreshRevision]);
  useEffect(() => () => detailController.current?.abort(), []);
  async function open(id: number) {
    detailController.current?.abort();
    const controller = new AbortController();
    detailController.current = controller;
    setError("");
    setProfile(null);
    setPassword("");
    setComment("");
    setUsername("");
    setScope("");
    setDistricts("");
    setDetail(null);
    try {
      const { data, error } = await typedApi.GET(
        "/api/v1/staff-applications/{application_id}",
        {
          params: { path: { application_id: id } },
          cache: "no-store",
          signal: controller.signal,
        },
      );
      if (controller.signal.aborted) return;
      if (!data) throw new Error(apiError(error, "详情读取失败"));
      setDetail(data);
    } catch (e) {
      if (!controller.signal.aborted)
        setError(e instanceof Error ? e.message : "详情读取失败");
    }
  }
  async function review(action: "approve" | "reject") {
    if (!detail) return;
    setError("");
    if (!comment.trim()) {
      setError("请填写反馈给申请人的审核意见（勿填写密码）");
      return;
    }
    let account: ApiSchemas["ProvisionInput"] | undefined;
    if (action === "approve") {
      const values = districts
        .split(/[,，\n]/)
        .map((value) => value.trim())
        .filter(Boolean);
      if (
        !profile ||
        !scope ||
        !username.trim() ||
        password.length < 12 ||
        (scope === "districts" && !values.length)
      ) {
        setError("请明确填写账号、至少12位临时密码、角色和区域范围");
        return;
      }
      account = {
        username: username.trim(),
        temporary_password: password,
        role_profile_id: profile.id,
        role_profile_version: profile.version,
        allowed_districts:
          scope === "global" ? null : scope === "none" ? [] : values,
      };
    }
    setBusy(true);
    detailController.current?.abort();
    try {
      const { data, error } = await typedApi.POST(
        "/api/v1/staff-applications/{application_id}/review",
        {
          params: { path: { application_id: detail.id } },
          body: { action, version: detail.version, comment, account },
          headers: { "x-csrf-token": csrf },
        },
      );
      if (!data) throw new Error(apiError(error, "审核失败，未开通账号"));
      setPassword("");
      setComment("");
      setDetail(null);
      setRevision((value) => value + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "审核失败");
      setProfile(null);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="business-stack" ref={workspaceRef}>
      {detail && error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <section
        className="panel staff-applications record-pane"
        hidden={!!detail}
      >
        <div className="panel-title">
          <div>
            <h2>工作人员账号申请审核</h2>
            <p>
              仅全域账号管理员可审核；需线下核验身份，不自动授予监管权限或验证申请电话。
            </p>
          </div>
          <button
            className="outline"
            disabled={busy}
            onClick={() => setRevision((value) => value + 1)}
          >
            刷新人员申请
          </button>
        </div>
        <form
          className="application-filters"
          onSubmit={(e) => {
            e.preventDefault();
            setQuery({ ...draft, page: 1 });
          }}
        >
          <label>
            姓名、联系电话或开通账号
            <input
              aria-label="人员申请关键词"
              value={draft.q}
              maxLength={150}
              onChange={(e) => setDraft({ ...draft, q: e.target.value })}
              disabled={busy}
            />
          </label>
          <label>
            申请身份
            <select
              aria-label="人员申请身份筛选"
              value={draft.requested_role}
              onChange={(e) =>
                setDraft({ ...draft, requested_role: e.target.value })
              }
              disabled={busy}
            >
              <option value="">全部身份</option>
              <option value="regulator">监管工作人员</option>
              <option value="association">协会工作人员</option>
            </select>
          </label>
          <label>
            审核状态
            <select
              aria-label="人员申请状态筛选"
              value={draft.status}
              onChange={(e) => setDraft({ ...draft, status: e.target.value })}
              disabled={busy}
            >
              <option value="">全部状态</option>
              {Object.entries(statuses).map(([value, label]) => (
                <option value={value} key={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <button className="primary" disabled={busy || loading}>
            查询人员申请
          </button>
        </form>
        {error && (
          <p className="error staff-error" role="alert">
            {error}
          </p>
        )}
        {loading && <p role="status">正在读取申请…</p>}
        {data && (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>申请编号</th>
                    <th>申请人</th>
                    <th>联系电话（未验证）</th>
                    <th>申请身份</th>
                    <th>状态</th>
                    <th>提交时间</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((row) => (
                    <tr key={row.id}>
                      <td>STAFF-{row.id}</td>
                      <td>{row.applicant_name}</td>
                      <td>{row.contact_phone}</td>
                      <td>{roles[row.requested_role]}</td>
                      <td>{statuses[row.status]}</td>
                      <td>
                        {new Date(row.created_at).toLocaleString("zh-CN", {
                          timeZone: "Asia/Shanghai",
                        })}
                      </td>
                      <td>
                        <button
                          className="outline"
                          disabled={busy}
                          onClick={() => void open(row.id)}
                        >
                          查看人员申请
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!data.total && <p className="empty">暂无符合条件的人员账号申请</p>}
            <div className="business-form">
              <button
                className="outline"
                disabled={busy || loading || query.page <= 1}
                onClick={() => setQuery({ ...query, page: query.page - 1 })}
              >
                上一页人员申请
              </button>
              <span>
                第{query.page}页 · 共{data.total}条
              </span>
              <button
                className="outline"
                disabled={busy || loading || query.page * 20 >= data.total}
                onClick={() => setQuery({ ...query, page: query.page + 1 })}
              >
                下一页人员申请
              </button>
            </div>
          </>
        )}
      </section>
      {detail && (
        <section className="panel record-detail">
          <div className="panel-title">
            <div>
              <h2>人员申请 #{detail.id}</h2>
              <p>
                {detail.applicant_name} · {roles[detail.requested_role]} ·{" "}
                {statuses[detail.status]} · V{detail.version}
              </p>
            </div>
            <button
              className="outline"
              disabled={busy}
              onClick={() => {
                if (!canLeave()) return;
                detailController.current?.abort();
                setDetail(null);
                setPassword("");
              }}
            >
              关闭人员申请详情
            </button>
          </div>
          {detail.status === "pending" ? (
            <div className="business-form">
              <p className="wide-field muted">
                请先核验真实身份。通过后创建须首次改密的账号；不会自动发送短信或邮件，也不把申请电话当成验证手机。
              </p>
              <StaffRolePicker
                key={detail.id}
                baseRole={detail.requested_role}
                value={profile}
                onChange={setProfile}
                disabled={busy}
              />
              <label>
                开通账号
                <input
                  aria-label="审核开通账号"
                  value={username}
                  maxLength={100}
                  onChange={(e) => setUsername(e.target.value)}
                  disabled={busy}
                  autoComplete="off"
                />
              </label>
              <label>
                临时密码（另行安全交付）
                <input
                  aria-label="审核临时密码"
                  value={password}
                  type="password"
                  minLength={12}
                  maxLength={200}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={busy}
                  autoComplete="new-password"
                />
              </label>
              <label>
                账号区域范围
                <select
                  aria-label="审核账号区域范围"
                  value={scope}
                  onChange={(e) => setScope(e.target.value)}
                  disabled={busy}
                >
                  <option value="">请明确选择范围</option>
                  <option value="global">全域</option>
                  <option value="districts">指定区域</option>
                  <option value="none">暂不开放场站数据</option>
                </select>
              </label>
              {scope === "districts" && (
                <label>
                  区域名称（逗号分隔）
                  <input
                    aria-label="审核指定区域"
                    value={districts}
                    maxLength={2000}
                    onChange={(e) => setDistricts(e.target.value)}
                    disabled={busy}
                  />
                </label>
              )}
              <label className="wide-field">
                反馈给申请人的审核意见（勿填写密码）
                <textarea
                  aria-label="人员申请审核意见"
                  value={comment}
                  maxLength={1000}
                  onChange={(e) => setComment(e.target.value)}
                  disabled={busy}
                />
              </label>
              <div className="action-row wide-field">
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() => void review("approve")}
                >
                  审核通过并开通账号
                </button>
                <button
                  className="outline"
                  disabled={busy}
                  onClick={() => void review("reject")}
                >
                  驳回人员申请
                </button>
              </div>
            </div>
          ) : (
            <div className="business-form">
              <p>审核反馈：{detail.review_comment}</p>
              <p>开通账号：{detail.username ?? "未开通"}</p>
              {detail.grant_snapshot && (
                <div className="wide-field">
                  <p>
                    审核时角色：{detail.grant_snapshot.profile_name} · V
                    {detail.grant_snapshot.profile_version}
                  </p>
                  <p>
                    审核时区域：
                    {detail.grant_snapshot.allowed_districts === null
                      ? "全域"
                      : detail.grant_snapshot.allowed_districts.join("、") ||
                        "暂不开放场站数据"}
                  </p>
                  <p className="muted">
                    以上是审核时快照；当前权限和启用状态以账号管理为准。
                  </p>
                </div>
              )}
              <p className="wide-field muted">
                通知未发送，初始凭据需通过已核验渠道另行交付，不可从历史记录取回。
              </p>
            </div>
          )}
          <div className="business-form staff-review-history">
            <h3>审核记录</h3>
            {!detail.history.length && <p className="muted">暂无审核记录</p>}
            {detail.history.map((row, index) => (
              <article className="timeline" key={index}>
                <b>
                  {row.action === "approve" ? "审核通过并开通" : "驳回申请"}
                </b>
                <small>审核账号：{row.actor_name}</small>
                <p>
                  {new Date(row.created_at).toLocaleString("zh-CN", {
                    timeZone: "Asia/Shanghai",
                  })}
                </p>
              </article>
            ))}
          </div>
        </section>
      )}
    </section>
  );
}
