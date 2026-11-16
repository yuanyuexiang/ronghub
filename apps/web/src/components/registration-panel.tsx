"use client";
import { usePageRefresh } from "./page-refresh";
import { useEffect, useRef, useState } from "react";
import { apiFetch } from "../lib/api-fetch";
import { useCapability } from "./permissions";
type Registration = {
  id: number;
  username: string;
  company_name: string;
  credit_code: string;
  registration_address: string;
  contacts: {
    name: string;
    phone: string;
    email?: string;
    primary?: boolean;
  }[];
  status: string;
  version: number;
  history?: { action: string; comment: string; created_at: string }[];
  attachments?: { id: number; filename: string; size: number }[];
};
const states: Record<string, string> = {
  submitted: "待注册审核",
  rejected: "已驳回待补充",
  approved: "审核通过已开通",
  draft: "草稿",
};
export function RegistrationPanel({ csrf }: { csrf: string }) {
  const canReview = useCapability("registrations.review");
  const [items, setItems] = useState<Registration[]>([]),
    [detail, setDetail] = useState<Registration | null>(null),
    [profiles, setProfiles] = useState<
      { id: number; name: string; version: number }[]
    >([]),
    [profile, setProfile] = useState(""),
    [comment, setComment] = useState(""),
    [q, setQ] = useState(""),
    [page, setPage] = useState(1),
    [total, setTotal] = useState(0),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const sequence = useRef(0);
  async function request(url: string, body?: unknown) {
    const r = await apiFetch(
      url,
      body
        ? {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "x-csrf-token": csrf,
            },
            body: JSON.stringify(body),
          }
        : { cache: "no-store" },
    );
    const d = await r.json();
    if (!r.ok)
      throw new Error(
        typeof d.detail === "string" ? d.detail : "请检查审核输入",
      );
    return d;
  }
  const appliedListQuery = useRef({ q: "" });
  async function load() {
    const seq = ++sequence.current;
    try {
      const d = await request(
        "/api/v1/registrations?" +
          new URLSearchParams({
            ...appliedListQuery.current,
            page: String(page),
          }),
      );
      if (canReview) {
        const options = await request("/api/v1/registrations/options");
        if (seq === sequence.current) setProfiles(options.items);
      }
      if (seq === sequence.current) {
        setItems(d.items);
        setTotal(d.total);
      }
    } catch (error) {
      if (seq === sequence.current) throw error;
    }
  }
  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "请求失败");
    } finally {
      setBusy(false);
    }
  }
  async function open(id: number) {
    setDetail(await request("/api/v1/registrations/" + id));
    setComment("");
    setProfile("");
  }
  const pageRefreshRevision = usePageRefresh();
  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, [page, pageRefreshRevision]);

  async function review(action: string) {
    if (!detail) return;
    await run(async () => {
      await request(`/api/v1/registrations/${detail.id}/review`, {
        action,
        version: detail.version,
        comment,
        role_profile_id: action === "approve" ? Number(profile) : null,
        role_profile_version:
          action === "approve"
            ? profiles.find((p) => p.id === Number(profile))?.version
            : null,
      });
      await load();
      await open(detail.id);
    });
  }
  return (
    <section className="panel">
      <div className="panel-title">
        <div>
          <h2>企业注册审核</h2>
          <p>
            核对企业资料与资质，选择账号权限角色；通过后原子创建企业档案与登录账号
          </p>
        </div>
        <form
          className="search"
          onSubmit={(e) => {
            e.preventDefault();
            appliedListQuery.current = { q };
            if (page !== 1) setPage(1);
            else run(load);
          }}
        >
          <input
            aria-label="搜索注册企业"
            value={q}
            maxLength={150}
            placeholder="企业名称"
            onChange={(e) => setQ(e.target.value)}
          />
          <button disabled={busy}>查询注册申请</button>
        </form>
      </div>
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
                "企业名称",
                "信用代码",
                "申请账号",
                "状态",
                "操作",
              ].map((h) => (
                <th key={h}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {items.map((r) => (
              <tr key={r.id}>
                <td>REG-{r.id}</td>
                <td>{r.company_name}</td>
                <td>{r.credit_code}</td>
                <td>{r.username}</td>
                <td>
                  <em className={r.status === "approved" ? "green" : "amber"}>
                    {states[r.status]}
                  </em>
                </td>
                <td>
                  <button
                    className="outline"
                    disabled={busy}
                    onClick={() => run(() => open(r.id))}
                  >
                    查看注册资料
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!items.length && <p className="empty">暂无已提交的注册申请</p>}
      </div>
      <div className="business-form">
        <button
          className="outline"
          disabled={busy || page === 1}
          onClick={() => setPage(page - 1)}
        >
          注册上一页
        </button>
        <span>
          第{page}页 · 共{total}条
        </span>
        <button
          className="outline"
          disabled={busy || page * 20 >= total}
          onClick={() => setPage(page + 1)}
        >
          注册下一页
        </button>
      </div>
      {detail && (
        <div className="application-detail">
          <div className="panel-title">
            <div>
              <h2>
                REG-{detail.id} · {detail.company_name}
              </h2>
              <p>
                {states[detail.status]} · 资料V{detail.version}
              </p>
            </div>
            <button className="outline" onClick={() => setDetail(null)}>
              关闭注册详情
            </button>
          </div>
          <div className="detail-grid">
            <div>
              <h3>企业与联系人</h3>
              <p>信用代码 {detail.credit_code}</p>
              <p>注册地址 {detail.registration_address || "未填写"}</p>
              {detail.contacts.map((c, i) => (
                <article className="timeline" key={i}>
                  <b>
                    {c.name}
                    {c.primary ? " · 主要联系人" : ""}
                  </b>
                  <p>
                    {c.phone} {c.email}
                  </p>
                </article>
              ))}
              <section className="materials">
                <h3>注册资质</h3>
                {detail.attachments?.map((a) => (
                  <div className="material-row" key={a.id}>
                    <a
                      href={`/api/v1/registrations/${detail.id}/attachments/${a.id}/download`}
                    >
                      {a.filename}
                    </a>
                    <small>{Math.ceil(a.size / 1024)}KB</small>
                  </div>
                ))}
                {!detail.attachments?.length && <p className="muted">无材料</p>}
              </section>
            </div>
            <div>
              {canReview && detail.status === "submitted" && (
                <section className="materials">
                  <h3>审核处理</h3>
                  <label>
                    开通账号权限角色
                    <select
                      aria-label="注册开通权限角色"
                      value={profile}
                      disabled={busy}
                      onChange={(e) => setProfile(e.target.value)}
                    >
                      <option value="">请选择企业权限角色</option>
                      {profiles.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name} V{p.version}
                        </option>
                      ))}
                    </select>
                  </label>
                  {!profiles.length && (
                    <p className="muted">
                      暂无可下发的企业权限角色，请联系权限管理员配置。
                    </p>
                  )}
                  <label>
                    审核依据
                    <textarea
                      aria-label="注册审核意见"
                      maxLength={2000}
                      value={comment}
                      disabled={busy}
                      onChange={(e) => setComment(e.target.value)}
                    />
                  </label>
                  <div className="action-row">
                    <button
                      className="outline"
                      disabled={busy || !comment.trim() || !profile}
                      onClick={() => review("approve")}
                    >
                      通过注册并开通账号
                    </button>
                    <button
                      className="outline"
                      disabled={busy || !comment.trim()}
                      onClick={() => review("reject")}
                    >
                      驳回注册申请
                    </button>
                  </div>
                </section>
              )}
              <h3>办理历史</h3>
              {detail.history?.map((h, i) => (
                <article className="timeline" key={i}>
                  <b>
                    {{
                      create: "建立草稿",
                      edit: "补充资料",
                      submit: "提交审核",
                      approve: "审核通过",
                      reject: "审核驳回",
                    }[h.action] || h.action}
                  </b>
                  <p>{h.comment || "无补充说明"}</p>
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
