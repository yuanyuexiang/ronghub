"use client";
import { useRecordFocus } from "./use-record-focus";
import { usePageRefresh } from "./page-refresh";
import { companyApi } from "../lib/company-api";
import type { ApiSchemas } from "../lib/typed-api";
import { useCapability, useGlobalScope } from "./permissions";
import { useEffect, useState, useRef } from "react";
type Contact = ApiSchemas["CompanyContact"];
type Company = ApiSchemas["CompanyDetail"];
const emptyContact: Contact = {
  name: "",
  phone: "",
  email: "",
  position: "",
  primary: false,
};
export function CompanyPanel({
  role,
  csrf,
  registerLeaveGuard,
}: {
  role: string;
  csrf: string;
  registerLeaveGuard?: (guard: (() => boolean) | null) => void;
}) {
  const globalScope = useGlobalScope();
  const canEdit = useCapability("companies.write") && globalScope,
    canStatus = useCapability("companies.status") && globalScope,
    canExport = useCapability("companies.export");
  const [items, setItems] = useState<ApiSchemas["CompanySummary"][]>([]),
    [q, setQ] = useState(""),
    [credit, setCredit] = useState(""),
    [page, setPage] = useState(1),
    [total, setTotal] = useState(0),
    [detail, setDetail] = useState<Company | null>(null),
    [editing, setEditing] = useState(false),
    [reason, setReason] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [leaveRequested, setLeaveRequested] = useState(false);
  const savedProfile = useRef("");
  const hasUnsaved =
    (editing && JSON.stringify(detail) !== savedProfile.current) ||
    !!reason.trim();
  useEffect(() => {
    registerLeaveGuard?.(
      () =>
        !hasUnsaved ||
        window.confirm("当前企业资料或启停原因尚未保存，确定离开此模块？"),
    );
    return () => registerLeaveGuard?.(null);
  }, [hasUnsaved, registerLeaveGuard]);
  function closeDetail() {
    setDetail(null);
    setEditing(false);
    setReason("");
    setLeaveRequested(false);
    setError("");
  }
  function requestClose() {
    if (busy) return;
    if (hasUnsaved) setLeaveRequested(true);
    else closeDetail();
  }
  const workspaceRef = useRecordFocus(detail?.id);
  const api = companyApi(csrf);
  const loadSequence = useRef(0);
  const appliedListQuery = useRef({ q: "", credit_code: "" });
  async function load() {
    const sequence = ++loadSequence.current;
    try {
      const d = await api.list({ ...appliedListQuery.current, page });
      if (sequence !== loadSequence.current) return;
      setItems(d.items);
      setTotal(d.total);
    } catch (error) {
      if (sequence === loadSequence.current) throw error;
    }
  }
  async function open(id: number) {
    const value = await api.detail(id);
    savedProfile.current = JSON.stringify(value);
    setDetail(value);
    setLeaveRequested(false);
    setEditing(false);
    setReason("");
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
    load().catch((e) => setError(e.message));
  }, [page, pageRefreshRevision]);
  function contact(index: number, change: Partial<Contact>) {
    if (!detail) return;
    setDetail({
      ...detail,
      contacts: detail.contacts?.map((c, i) =>
        i === index ? { ...c, ...change } : c,
      ),
    });
  }
  return (
    <div className="business-stack">
      <section ref={workspaceRef} className="panel company-workspace">
        <div className="record-pane" hidden={!!detail}>
          <div className="panel-title">
            <div>
              <h2>{role === "operator" ? "本企业资料" : "运营商企业台账"}</h2>
              <p>
                信用代码精确检索，企业名称按字面包含检索；联系人资料仅限监管与所属企业查看。CSV每次最多导出10000家，超限请缩小查询范围。
              </p>
            </div>
            <form
              className="search"
              onSubmit={(e) => {
                e.preventDefault();
                appliedListQuery.current = { q, credit_code: credit };
                if (page !== 1) setPage(1);
                else run(load);
              }}
            >
              <input
                aria-label="搜索企业名称"
                placeholder="企业名称"
                value={q}
                maxLength={150}
                onChange={(e) => setQ(e.target.value)}
              />
              <input
                aria-label="搜索信用代码"
                placeholder="信用代码（精确）"
                value={credit}
                maxLength={18}
                onChange={(e) => setCredit(e.target.value)}
              />
              <button disabled={busy}>查询企业</button>
              {canExport && (
                <a
                  className="outline export-link"
                  href={
                    "/api/v1/companies/export?" +
                    new URLSearchParams(appliedListQuery.current)
                  }
                >
                  导出企业CSV
                </a>
              )}
            </form>
          </div>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {["企业名称", "信用代码", "账号状态", "资料版本", "操作"].map(
                    (h) => (
                      <th key={h}>{h}</th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {items.map((c) => (
                  <tr key={c.id}>
                    <td>{c.name}</td>
                    <td>{c.credit_code || "尚未补充"}</td>
                    <td>
                      <em className={c.enabled ? "green" : "amber"}>
                        {c.enabled ? "正常" : "企业已停用"}
                      </em>
                    </td>
                    <td>V{c.version}</td>
                    <td>
                      <button
                        className="outline"
                        disabled={busy}
                        onClick={() => run(() => open(c.id))}
                      >
                        查看企业资料
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!items.length && <p className="empty">当前条件下没有企业</p>}
          </div>
          <div className="business-form">
            <button
              className="outline"
              disabled={busy || page === 1}
              onClick={() => setPage(page - 1)}
            >
              企业上一页
            </button>
            <span>
              第{page}页 · 共{total}家企业
            </span>
            <button
              className="outline"
              disabled={busy || page * 20 >= total}
              onClick={() => setPage(page + 1)}
            >
              企业下一页
            </button>
          </div>
        </div>
        {detail && (
          <div
            className="application-detail record-detail"
            role="region"
            aria-label="企业详情工作区"
          >
            {error && (
              <p role="alert" className="error application-list-note">
                {error}
              </p>
            )}
            {leaveRequested && (
              <div className="record-leave-warning" role="alert">
                <p>企业资料或启停原因尚未保存。返回列表会放弃这些修改。</p>
                <button
                  className="outline"
                  onClick={() => setLeaveRequested(false)}
                >
                  继续编辑企业资料
                </button>
                <button className="outline" onClick={closeDetail}>
                  放弃修改并返回运营商列表
                </button>
              </div>
            )}
            <div className="record-backline">
              <span>
                {role === "operator"
                  ? "本企业资料 / 企业档案"
                  : "场站运营商管理 / 企业档案"}
              </span>
            </div>
            <div className="panel-title">
              <div>
                <h2>{detail.name} · 企业档案</h2>
                <p>
                  资料版本V{detail.version} ·{" "}
                  {detail.enabled ? "正常" : "企业已停用"}
                </p>
              </div>
              <div className="action-row">
                {canEdit && (
                  <button
                    className="outline"
                    disabled={busy}
                    onClick={() => {
                      if (editing) run(() => open(detail.id));
                      else setEditing(true);
                    }}
                  >
                    {editing ? "取消编辑" : "维护企业资料"}
                  </button>
                )}
                <button
                  className="outline"
                  disabled={busy}
                  onClick={requestClose}
                >
                  {role === "operator" ? "返回企业列表" : "返回运营商列表"}
                </button>
              </div>
            </div>
            {editing ? (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  run(async () => {
                    await api.edit(detail.id, {
                      version: detail.version,
                      name: detail.name,
                      credit_code: detail.credit_code || null,
                      registration_address: detail.registration_address || "",
                      legal_representative: detail.legal_representative || "",
                      service_phone: detail.service_phone || "",
                      contacts: detail.contacts || [],
                    });
                    await load();
                    await open(detail.id);
                  });
                }}
              >
                <div className="business-form">
                  {[
                    ["name", "企业名称", 150],
                    ["credit_code", "信用代码", 18],
                    ["registration_address", "注册地址", 255],
                    ["legal_representative", "法定代表人", 100],
                    ["service_phone", "客服电话", 80],
                  ].map(([field, label, max]) => (
                    <label key={String(field)}>
                      {label}
                      <input
                        aria-label={String(label)}
                        required={field === "name"}
                        maxLength={Number(max)}
                        value={String(detail[field as keyof Company] || "")}
                        onChange={(e) =>
                          setDetail({
                            ...detail,
                            [String(field)]: e.target.value,
                          })
                        }
                      />
                    </label>
                  ))}
                </div>
                <section className="company-contacts">
                  <h3>联系人</h3>
                  {detail.contacts?.map((c, i) => (
                    <div className="business-form contact-card" key={i}>
                      {[
                        ["name", "姓名"],
                        ["phone", "电话"],
                        ["email", "邮箱"],
                        ["position", "职务"],
                      ].map(([field, label]) => (
                        <label key={field}>
                          {label}
                          <input
                            aria-label={`联系人${i + 1}${label}`}
                            required={["name", "phone"].includes(field)}
                            maxLength={
                              field === "email"
                                ? 150
                                : field === "phone"
                                  ? 80
                                  : 100
                            }
                            value={String(c[field as keyof Contact] || "")}
                            onChange={(e) =>
                              contact(i, { [field]: e.target.value })
                            }
                          />
                        </label>
                      ))}
                      <label>
                        主要联系人
                        <input
                          aria-label={`联系人${i + 1}主要联系人`}
                          type="checkbox"
                          checked={c.primary}
                          onChange={(e) =>
                            contact(i, { primary: e.target.checked })
                          }
                        />
                      </label>
                      <button
                        className="outline"
                        type="button"
                        onClick={() =>
                          setDetail({
                            ...detail,
                            contacts: detail.contacts?.filter(
                              (_, j) => j !== i,
                            ),
                          })
                        }
                      >
                        移除此联系人
                      </button>
                    </div>
                  ))}
                  <div className="action-row">
                    <button
                      className="outline"
                      type="button"
                      disabled={(detail.contacts?.length || 0) >= 10}
                      onClick={() =>
                        setDetail({
                          ...detail,
                          contacts: [
                            ...(detail.contacts || []),
                            { ...emptyContact },
                          ],
                        })
                      }
                    >
                      添加联系人
                    </button>
                    <button className="outline" disabled={busy}>
                      保存企业资料
                    </button>
                  </div>
                </section>
              </form>
            ) : (
              <div className="detail-grid">
                <div>
                  <h3>基本资料</h3>
                  <p>信用代码：{detail.credit_code || "尚未补充"}</p>
                  <p>注册地址：{detail.registration_address || "尚未补充"}</p>
                  <p>法定代表人：{detail.legal_representative || "尚未补充"}</p>
                  <p>客服电话：{detail.service_phone || "尚未补充"}</p>
                  <h3>联系人</h3>
                  {detail.contacts?.map((c, i) => (
                    <article className="timeline" key={i}>
                      <b>
                        {c.name}
                        {c.primary ? " · 主要联系人" : ""}
                      </b>
                      <p>
                        {c.position} {c.phone}
                      </p>
                      <p>{c.email}</p>
                    </article>
                  ))}
                  {!detail.contacts?.length && (
                    <p className="muted">暂无联系人</p>
                  )}
                </div>
                <div>
                  {canStatus && role === "regulator" && (
                    <section className="materials">
                      <h3>企业账号启停</h3>
                      <p className="muted">
                        停用将撤销本企业全部账号会话，重新启用后需重新登录。
                      </p>
                      <label>
                        启停依据
                        <textarea
                          aria-label="企业启停原因"
                          disabled={busy}
                          value={reason}
                          onChange={(e) => setReason(e.target.value)}
                          maxLength={1000}
                        />
                      </label>
                      <button
                        className="outline"
                        disabled={busy || !reason.trim()}
                        onClick={() =>
                          run(async () => {
                            await api.status(detail.id, {
                              version: detail.version,
                              enabled: !detail.enabled,
                              reason,
                            });
                            await load();
                            await open(detail.id);
                          })
                        }
                      >
                        {detail.enabled ? "停用企业账号" : "启用企业账号"}
                      </button>
                    </section>
                  )}
                  <h3>资料维护记录</h3>
                  {detail.history?.map((h, i) => (
                    <article className="timeline" key={i}>
                      <b>
                        {h.action === "company.update"
                          ? "更新企业资料"
                          : "变更企业账号状态"}
                      </b>
                      <p>{new Date(h.created_at).toLocaleString("zh-CN")}</p>
                    </article>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
