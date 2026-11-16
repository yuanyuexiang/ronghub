"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { LoginCaptcha } from "./login-captcha";
const roles = { regulator: "监管工作人员", association: "协会工作人员" };
const statuses = {
  pending: "等待人工审核",
  approved: "审核通过",
  rejected: "未通过审核",
};
export function StaffEnrollmentPanel() {
  const [enabled, setEnabled] = useState(false),
    [application, setApplication] = useState<
      ApiSchemas["StaffApplicantStatus"] | null
    >(null),
    [form, setForm] = useState(true);
  const [role, setRole] = useState<"" | "regulator" | "association">(""),
    [name, setName] = useState(""),
    [phone, setPhone] = useState("");
  const [captcha, setCaptcha] = useState<ApiSchemas["LoginCaptcha"] | null>(
      null,
    ),
    [answer, setAnswer] = useState(""),
    [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      typedApi.GET("/api/v1/auth/staff-enrollment/options", {
        signal: controller.signal,
        cache: "no-store",
      }),
      typedApi.GET("/api/v1/auth/staff-enrollment/me", {
        signal: controller.signal,
        cache: "no-store",
      }),
    ])
      .then(([options, status]) => {
        if (controller.signal.aborted) return;
        if (!options.data)
          throw new Error(apiError(options.error, "申请入口读取失败"));
        setEnabled(options.data.enabled);
        if (status.data) {
          setApplication(status.data);
          setForm(false);
        } else if (status.response.status !== 401)
          throw new Error(apiError(status.error, "进度读取失败"));
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, []);
  async function query() {
    setBusy(true);
    setError("");
    try {
      const { data, error } = await typedApi.GET(
        "/api/v1/auth/staff-enrollment/me",
        { cache: "no-store" },
      );
      if (!data) throw new Error(apiError(error, "当前浏览器无有效申请凭据"));
      setApplication(data);
      setForm(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "查询失败");
    } finally {
      setBusy(false);
    }
  }
  async function submit() {
    setBusy(true);
    setError("");
    try {
      if (role !== "regulator" && role !== "association")
        throw new Error("请选择申请身份");
      const { data, error } = await typedApi.POST(
        "/api/v1/auth/staff-enrollment",
        {
          body: {
            requested_role: role,
            applicant_name: name,
            contact_phone: phone,
            captcha_id: captcha?.captcha_id ?? "",
            captcha_answer: answer,
          },
        },
      );
      if (!data) throw new Error(apiError(error, "申请提交失败"));
      setApplication(data);
      setForm(false);
      setName("");
      setPhone("");
      setAnswer("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "提交失败");
      setRefresh((v) => v + 1);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="auth-sms">
      <Link className="auth-sms-brand" href="/">
        RONGHUB · 福州市充电监管平台
      </Link>
      <section className="panel">
        <div className="panel-title">
          <div>
            <h1>工作人员账号申请</h1>
            <p>监管、协会工作人员提交申请，由授权管理员人工审核。</p>
          </div>
        </div>
        <div className="auth-sms-body">
          <p className="phone-channel-note">
            演示申请流程：提交不会创建账号或授予权限。联系电话尚未做身份验证，不能用于密码找回；审核结果与初始凭据需另行核验交付，未发送短信或邮件。
          </p>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          {!enabled && (
            <p className="muted">当前未开放正式申请，或服务仍在读取中。</p>
          )}
          {application && !form && (
            <section aria-label="本浏览器账号申请进度">
              <h2>{statuses[application.status]}</h2>
              <p>
                申请 #{application.id} · {roles[application.requested_role]}
                {application.is_demo ? " · 演示" : ""}
              </p>
              <p>
                {application.applicant_name} · 联系电话{" "}
                {application.phone_masked}
              </p>
              <p>
                提交时间：
                {new Date(application.created_at).toLocaleString("zh-CN", {
                  timeZone: "Asia/Shanghai",
                })}
              </p>
              {application.review_comment && (
                <p>审核反馈：{application.review_comment}</p>
              )}
              {application.username && (
                <p>
                  开通账号：<strong>{application.username}</strong>
                  。初始凭据请通过已核验渠道向管理员领取，首次登录必须修改临时密码。
                </p>
              )}
              <button
                type="button"
                className="outline"
                disabled={busy}
                onClick={() => void query()}
              >
                刷新本人申请进度
              </button>
              {application.status !== "pending" && (
                <button
                  type="button"
                  className="outline"
                  disabled={busy || !enabled}
                  onClick={() => {
                    setForm(true);
                    setRefresh((v) => v + 1);
                  }}
                >
                  填写新申请
                </button>
              )}
              <p className="muted">
                当前浏览器的进度凭据有效24小时；账号激活、凭据或授权变更、清除浏览器数据、恢复系统备份或超过有效期后，请联系管理员核验查询。
              </p>
            </section>
          )}
          {form && (
            <form
              className="auth-sms-form"
              onSubmit={(e) => {
                e.preventDefault();
                void submit();
              }}
            >
              <label>
                申请身份
                <select
                  aria-label="工作人员申请身份"
                  value={role}
                  onChange={(e) => setRole(e.target.value as typeof role)}
                  required
                  disabled={busy}
                >
                  <option value="">请选择身份</option>
                  <option value="regulator">监管工作人员</option>
                  <option value="association">协会工作人员</option>
                </select>
              </label>
              <label>
                申请人姓名
                <input
                  aria-label="工作人员申请姓名"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                  maxLength={100}
                  disabled={busy}
                  autoComplete="name"
                />
              </label>
              <label>
                申请人联系电话（未验证）
                <input
                  aria-label="工作人员申请联系电话"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  required
                  maxLength={20}
                  inputMode="tel"
                  autoComplete="tel"
                  disabled={busy}
                />
              </label>
              <LoginCaptcha
                value={captcha}
                onReady={setCaptcha}
                answer={answer}
                onAnswer={setAnswer}
                refreshKey={refresh}
                busy={busy}
              />
              <button
                className="primary"
                disabled={busy || !enabled || !captcha}
              >
                提交工作人员账号申请
              </button>
              <button
                type="button"
                className="outline"
                disabled={busy}
                onClick={() => void query()}
              >
                查询本浏览器已有申请
              </button>
            </form>
          )}
          <div className="auth-sms-links">
            <Link href="/">返回登录</Link>
            <Link href="/register">运营企业入驻申请</Link>
          </div>
        </div>
      </section>
    </main>
  );
}
