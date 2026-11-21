"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { enrollmentApi, apiError, type ApiSchemas } from "../lib/typed-api";
type Draft = Pick<
  ApiSchemas["CreateInput"],
  "company_name" | "credit_code" | "registration_address" | "contacts"
>;
type Enrollment = ApiSchemas["EnrollmentProgress"];
type Material = ApiSchemas["EnrollmentMaterial"];
const statuses: Record<string, string> = {
  draft: "草稿待提交",
  submitted: "待监管审核",
  rejected: "已驳回待补充",
  approved: "审核通过，账号已开通",
};
const actions: Record<string, string> = {
  create: "建立注册草稿",
  edit: "修改注册资料",
  submit: "提交审核",
  reject: "审核驳回",
  approve: "审核通过",
};
const blank: Draft = {
  company_name: "",
  credit_code: "",
  registration_address: "",
  contacts: [{ name: "", phone: "", email: "", position: "", primary: true }],
};
export function EnrollmentPortal() {
  const [current, setCurrent] = useState<Enrollment | null>(null),
    [draft, setDraft] = useState<Draft>(blank),
    [username, setUsername] = useState(""),
    [password, setPassword] = useState(""),
    [mode, setMode] = useState<"create" | "login">("create"),
    [materials, setMaterials] = useState<Material[]>([]),
    [loading, setLoading] = useState(true),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [open, setOpen] = useState(true);
  function read<T>(
    result: { data?: T; error?: unknown; response: Response },
    login = false,
  ): T {
    if (result.data === undefined) {
      if (result.response.status === 401 && !login) {
        setCurrent(null);
        setMode("login");
      }
      throw new Error(apiError(result.error, "请检查注册资料"));
    }
    return result.data;
  }
  const headers = { "x-enrollment-csrf": current?.csrf_token || "" };
  async function load() {
    const results = await Promise.all([
      enrollmentApi.GET("/api/v1/enrollment/me", { cache: "no-store" }),
      enrollmentApi.GET("/api/v1/enrollment/me/attachments", {
        cache: "no-store",
      }),
    ]);
    const r = read(results[0]),
      m = read(results[1]);
    setCurrent(r);
    setDraft({
      company_name: r.company_name,
      credit_code: r.credit_code,
      registration_address: r.registration_address,
      contacts: r.contacts,
    });
    setMaterials(m.items);
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
  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const health = await enrollmentApi.GET("/api/v1/health", {
          cache: "no-store",
        });
        if (health.data && !health.data.demo_mode) {
          if (active) setOpen(false);
          return;
        }
        const r = await enrollmentApi.GET("/api/v1/enrollment/me", {
          cache: "no-store",
        });
        if (active && r.data) {
          const d = r.data;
          setCurrent(d);
          setUsername(d.username);
          setDraft({
            company_name: d.company_name,
            credit_code: d.credit_code,
            registration_address: d.registration_address,
            contacts: d.contacts,
          });
          const m = await enrollmentApi.GET(
            "/api/v1/enrollment/me/attachments",
            { cache: "no-store" },
          );
          if (active && m.data) setMaterials(m.data.items);
        }
      } catch {
        if (active) setError("注册服务暂不可用");
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, []);
  const editable = !current || ["draft", "rejected"].includes(current.status);
  const dirty =
    !!current &&
    (draft.company_name !== current.company_name ||
      draft.credit_code !== current.credit_code ||
      draft.registration_address !== current.registration_address ||
      JSON.stringify(draft.contacts) !== JSON.stringify(current.contacts));
  function contact(i: number, field: string, value: string | boolean) {
    setDraft({
      ...draft,
      contacts: draft.contacts.map((c, j) =>
        i === j
          ? { ...c, [field]: value }
          : field === "primary" && value === true
            ? { ...c, primary: false }
            : c,
      ),
    });
  }
  const fields = (
    <>
      <div className="business-form">
        <label>
          企业名称
          <input
            aria-label="注册企业名称"
            required
            maxLength={150}
            value={draft.company_name}
            disabled={busy}
            onChange={(e) =>
              setDraft({ ...draft, company_name: e.target.value })
            }
          />
        </label>
        <label>
          统一社会信用代码
          <input
            aria-label="注册信用代码"
            required
            pattern="[0-9A-Z]{18}"
            minLength={18}
            maxLength={18}
            value={draft.credit_code}
            disabled={busy}
            onChange={(e) =>
              setDraft({ ...draft, credit_code: e.target.value.toUpperCase() })
            }
          />
        </label>
        <label>
          注册地址
          <input
            aria-label="注册企业地址"
            maxLength={255}
            value={draft.registration_address}
            disabled={busy}
            onChange={(e) =>
              setDraft({ ...draft, registration_address: e.target.value })
            }
          />
        </label>
      </div>
      <div className="company-contacts">
        <h3>企业联系人</h3>
        {draft.contacts.map((c, i) => (
          <div className="business-form contact-card" key={i}>
            <label>
              联系人姓名
              <input
                aria-label={`注册联系人${i + 1}姓名`}
                required
                maxLength={100}
                value={c.name}
                disabled={busy}
                onChange={(e) => contact(i, "name", e.target.value)}
              />
            </label>
            <label>
              联系电话
              <input
                aria-label={`注册联系人${i + 1}电话`}
                required
                maxLength={80}
                value={c.phone}
                disabled={busy}
                onChange={(e) => contact(i, "phone", e.target.value)}
              />
            </label>
            <label>
              邮箱
              <input
                aria-label={`注册联系人${i + 1}邮箱`}
                type="email"
                maxLength={150}
                value={c.email || ""}
                disabled={busy}
                onChange={(e) => contact(i, "email", e.target.value)}
              />
            </label>
            <label>
              主要联系人
              <input
                type="checkbox"
                checked={!!c.primary}
                disabled={busy}
                onChange={(e) => contact(i, "primary", e.target.checked)}
              />
            </label>
            {draft.contacts.length > 1 && (
              <button
                type="button"
                className="outline"
                disabled={busy}
                onClick={() =>
                  setDraft({
                    ...draft,
                    contacts: draft.contacts.filter((_, j) => i !== j),
                  })
                }
              >
                移除联系人
              </button>
            )}
          </div>
        ))}
        <button
          type="button"
          className="outline"
          disabled={busy || draft.contacts.length >= 10}
          onClick={() =>
            setDraft({
              ...draft,
              contacts: [
                ...draft.contacts,
                {
                  name: "",
                  phone: "",
                  email: "",
                  position: "",
                  primary: false,
                },
              ],
            })
          }
        >
          增加注册联系人
        </button>
      </div>
    </>
  );
  return (
    <main className="enrollment-page">
      <div className="enrollment-heading">
        <div className="brand">RONGHUB</div>
        <Link href="/">返回平台登录</Link>
      </div>
      <div className="heading">
        <div>
          <p className="eyebrow">ENTERPRISE ENROLLMENT</p>
          <h1>企业注册与进度查询</h1>
          <p className="muted">提交企业资料和资质，审核通过后开通企业账号</p>
        </div>
        {current && (
          <button
            className="outline"
            disabled={busy}
            onClick={() =>
              run(async () => {
                read(
                  await enrollmentApi.POST("/api/v1/enrollment/logout", {
                    headers,
                  }),
                );
                setCurrent(null);
                setMaterials([]);
                setPassword("");
                setMode("login");
              })
            }
          >
            退出进度查询
          </button>
        )}
      </div>
      <div className="demo">
        {open
          ? "验证码、短信及工商核验尚未接入，请使用测试资料。"
          : "企业在线注册暂未开放，请联系平台管理员。"}
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {loading ? (
        <p className="empty">正在读取注册进度…</p>
      ) : !open ? (
        <section className="panel">
          <p className="empty">企业在线注册暂未开放，请联系平台管理员。</p>
        </section>
      ) : current ? (
        <div className="business-stack">
          <section className="panel">
            <div className="panel-title">
              <div>
                <h2>
                  REG-{current.id} · {statuses[current.status]}
                </h2>
                <p>
                  登录账号 {current.username} · 资料版本V{current.version}
                </p>
              </div>
              <button
                className="outline"
                disabled={busy || dirty}
                onClick={() => run(load)}
              >
                刷新注册进度
              </button>
            </div>
            <div className="enrollment-steps">
              <span className="done">1 填报与资质材料</span>
              <span
                className={
                  current.status === "submitted" ||
                  current.status === "approved"
                    ? "done"
                    : ""
                }
              >
                2 监管审核
              </span>
              <span className={current.status === "approved" ? "done" : ""}>
                3 开通账号
              </span>
            </div>
            {current.status === "approved" && (
              <div className="business-form">
                <p>账号已开通，使用注册时的账号和密码登录平台。</p>
                <Link className="outline export-link" href="/">
                  前往平台登录
                </Link>
              </div>
            )}
            {editable ? (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  run(async () => {
                    read(
                      await enrollmentApi.PATCH("/api/v1/enrollment/me", {
                        headers,
                        body: { ...draft, version: current.version },
                      }),
                    );
                    await load();
                  });
                }}
              >
                {fields}
                <div className="business-form">
                  <button className="outline" disabled={busy}>
                    保存注册资料
                  </button>
                  {dirty && (
                    <>
                      <span className="muted">
                        有未保存修改，请先保存再上传或提交。
                      </span>
                      <button
                        type="button"
                        className="outline"
                        disabled={busy}
                        onClick={() =>
                          setDraft({
                            company_name: current.company_name,
                            credit_code: current.credit_code,
                            registration_address: current.registration_address,
                            contacts: current.contacts,
                          })
                        }
                      >
                        还原到已保存资料
                      </button>
                    </>
                  )}
                </div>
              </form>
            ) : (
              <div className="statistics-detail">
                <h3>{current.company_name}</h3>
                <p>信用代码 {current.credit_code}</p>
                <p>{current.registration_address}</p>
                <p>
                  {current.status === "approved"
                    ? "注册档案已归档，后续企业资料可在平台内维护。"
                    : "资料已提交，当前不可修改；如被驳回，可补充后重新提交。"}
                </p>
              </div>
            )}
          </section>
          <section className="panel">
            <div className="panel-title">
              <div>
                <h2>资质材料</h2>
                <p>
                  支持PDF、PNG、JPG；单份5MB，最多10份。演示提交至少需一份材料，正式清单待确认。
                </p>
              </div>
            </div>
            <div className="statistics-detail">
              {materials.map((m) => (
                <div className="material-row" key={m.id}>
                  <a
                    href={`/api/v1/enrollment/me/attachments/${m.id}/download`}
                  >
                    {m.filename}
                  </a>
                  <small>
                    {Math.ceil(m.size / 1024)}KB ·{" "}
                    {m.submitted_version ? "已提交留档" : "未提交"}
                  </small>
                  {editable && !m.submitted_version && (
                    <button
                      className="outline"
                      disabled={busy || dirty}
                      onClick={() =>
                        run(async () => {
                          read(
                            await enrollmentApi.DELETE(
                              "/api/v1/enrollment/me/attachments/{material_id}",
                              {
                                headers,
                                params: { path: { material_id: m.id } },
                              },
                            ),
                          );
                          await load();
                        })
                      }
                    >
                      删除未提交资质
                    </button>
                  )}
                </div>
              ))}
              {!materials.length && <p className="muted">暂无资质材料</p>}
              {editable && (
                <label>
                  上传资质材料
                  <input
                    aria-label="上传注册资质"
                    type="file"
                    accept="application/pdf,image/png,image/jpeg"
                    disabled={busy || dirty}
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) {
                        const form = new FormData();
                        form.set("file", file);
                        run(async () => {
                          read(
                            await enrollmentApi.POST(
                              "/api/v1/enrollment/me/attachments",
                              {
                                headers,
                                body: { file: file.name },
                                bodySerializer: () => form,
                              },
                            ),
                          );
                          await load();
                        });
                      }
                      e.target.value = "";
                    }}
                  />
                </label>
              )}
            </div>
            {editable && (
              <div className="business-form">
                <button
                  className="outline"
                  disabled={busy || dirty || !materials.length}
                  onClick={() =>
                    run(async () => {
                      read(
                        await enrollmentApi.POST(
                          "/api/v1/enrollment/me/submit",
                          { headers, body: { version: current.version } },
                        ),
                      );
                      await load();
                    })
                  }
                >
                  提交注册审核
                </button>
              </div>
            )}
          </section>
          <section className="panel">
            <div className="panel-title">
              <h2>审核与办理记录</h2>
            </div>
            <div className="statistics-detail">
              {current.history?.map((h, i) => (
                <article className="timeline" key={i}>
                  <b>{actions[h.action] || h.action}</b>
                  <p>{h.comment || "无补充说明"}</p>
                  <small>
                    {new Date(h.created_at).toLocaleString("zh-CN")}
                  </small>
                </article>
              ))}
            </div>
          </section>
        </div>
      ) : (
        <section className="panel">
          <div className="panel-title">
            <div>
              <h2>{mode === "create" ? "新企业注册" : "查询已有注册申请"}</h2>
              <p>账号审核通过前仅用于查询和补充注册资料</p>
            </div>
            <button
              className="outline"
              disabled={busy}
              onClick={() => {
                setMode(mode === "create" ? "login" : "create");
                setError("");
                setPassword("");
              }}
            >
              {mode === "create" ? "已有申请，查询进度" : "新建企业注册"}
            </button>
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              run(async () => {
                if (mode === "create")
                  read(
                    await enrollmentApi.POST("/api/v1/enrollment", {
                      body: { ...draft, username, password },
                    }),
                  );
                else
                  read(
                    await enrollmentApi.POST("/api/v1/enrollment/login", {
                      body: { username, password },
                    }),
                    true,
                  );
                setPassword("");
                await load();
              });
            }}
          >
            <div className="business-form">
              <label>
                注册账号
                <input
                  aria-label="企业注册账号"
                  value={username}
                  required
                  minLength={3}
                  maxLength={100}
                  pattern="[A-Za-z0-9_.-]+"
                  disabled={busy}
                  onChange={(e) => setUsername(e.target.value)}
                  autoComplete="username"
                />
              </label>
              <label>
                登录密码
                <input
                  aria-label="企业注册密码"
                  type="password"
                  value={password}
                  required
                  minLength={mode === "create" ? 12 : undefined}
                  maxLength={200}
                  disabled={busy}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete={
                    mode === "create" ? "new-password" : "current-password"
                  }
                />
              </label>
            </div>
            {mode === "create" && fields}
            <div className="business-form">
              <button className="outline" disabled={busy}>
                {mode === "create" ? "建立企业注册草稿" : "登录注册进度"}
              </button>
            </div>
          </form>
        </section>
      )}
    </main>
  );
}
