"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { LoginCaptcha } from "./login-captcha";

export function SmsAuthPanel({ purpose }: { purpose: "login" | "recovery" }) {
  const [options, setOptions] = useState<ApiSchemas["SmsAuthOptions"] | null>(
    null,
  );
  const [identifierKind, setIdentifierKind] = useState<"account" | "phone">(
      "account",
    ),
    [identifier, setIdentifier] = useState("");
  const [captcha, setCaptcha] = useState<ApiSchemas["LoginCaptcha"] | null>(
      null,
    ),
    [answer, setAnswer] = useState(""),
    [refresh, setRefresh] = useState(0);
  const [issued, setIssued] = useState<ApiSchemas["SmsIssued"] | null>(null);
  const [code, setCode] = useState(""),
    [password, setPassword] = useState(""),
    [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [retry, setRetry] = useState(0),
    [wait, setWait] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    typedApi
      .GET("/api/v1/auth/sms/options", {
        cache: "no-store",
        signal: controller.signal,
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "通道状态读取失败"));
        setOptions(data);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, [retry]);
  useEffect(() => {
    if (!issued) return;
    const until = Date.now() + issued.retry_after_seconds * 1000;
    setWait(issued.retry_after_seconds);
    const timer = setInterval(
      () => setWait(Math.max(0, Math.ceil((until - Date.now()) / 1000))),
      1000,
    );
    return () => clearInterval(timer);
  }, [issued]);
  async function submit() {
    setBusy(true);
    setError("");
    try {
      if (!issued) {
        const { data, error } = await typedApi.POST(
          "/api/v1/auth/sms/challenges",
          {
            body: {
              purpose,
              identifier_kind: identifierKind,
              identifier,
              captcha_id: captcha?.captcha_id ?? "",
              captcha_answer: answer,
            },
          },
        );
        if (!data) throw new Error(apiError(error, "获取验证码失败"));
        setIssued(data);
        setCode("");
      } else if (purpose === "login") {
        const { data, error } = await typedApi.POST("/api/v1/auth/sms/login", {
          body: { challenge_id: issued.challenge_id, code },
        });
        if (!data) throw new Error(apiError(error, "短信登录失败"));
        setCode("");
        window.location.assign("/");
      } else {
        if (password !== confirmation) throw new Error("两次新密码不一致");
        const { data, error } = await typedApi.POST(
          "/api/v1/auth/sms/recover",
          {
            body: {
              challenge_id: issued.challenge_id,
              code,
              new_password: password,
            },
          },
        );
        if (!data) throw new Error(apiError(error, "密码重置失败"));
        setPassword("");
        setConfirmation("");
        setCode("");
        try {
          sessionStorage.setItem(
            "ronghub:recovery-result",
            JSON.stringify({
              account: identifierKind === "account" ? identifier : "",
              notice: "密码已重置，全部设备已退出。请使用新密码重新登录。",
              expires: Date.now() + 120000,
            }),
          );
        } catch {
          /* Storage is optional; credential replacement already succeeded. */
        }
        window.location.assign("/");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
      if (!issued) setRefresh((v) => v + 1);
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
            <h1>{purpose === "login" ? "短信验证码登录" : "找回密码"}</h1>
            <p>
              {purpose === "login"
                ? "使用账号已绑定的手机验证登录"
                : "通过账号已绑定的手机重置密码，成功后需重新登录"}
            </p>
          </div>
        </div>
        <div className="auth-sms-body">
          <p className="phone-channel-note">
            {options?.sms_mode === "demo"
              ? "当前为模拟短信，未发送真实短信。需先在账号安全完成手机模拟验证，验证码由开发者通过本地模拟工具提供。"
              : "真实短信通道尚未配置；可联系获授权管理员核验身份后协助重置。"}
          </p>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          {!options && (
            <button
              type="button"
              className="outline"
              onClick={() => setRetry((v) => v + 1)}
            >
              重新读取通道状态
            </button>
          )}
          <form
            className="auth-sms-form"
            onSubmit={(e) => {
              e.preventDefault();
              void submit();
            }}
          >
            <label>
              查找方式
              <select
                aria-label="手机验证查找方式"
                value={identifierKind}
                onChange={(e) =>
                  setIdentifierKind(e.target.value as "account" | "phone")
                }
                disabled={busy || !!issued}
              >
                <option value="account">账号</option>
                <option value="phone">已绑定手机号</option>
              </select>
            </label>
            <label>
              {identifierKind === "account" ? "账号" : "已绑定手机号"}
              <input
                aria-label="手机验证账号或手机号"
                value={identifier}
                onChange={(e) => setIdentifier(e.target.value)}
                autoComplete={identifierKind === "account" ? "username" : "tel"}
                required
                maxLength={100}
                disabled={busy || !!issued}
              />
            </label>
            {!issued ? (
              <>
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
                  disabled={busy || !captcha || options?.sms_mode !== "demo"}
                >
                  获取手机验证码
                </button>
              </>
            ) : (
              <>
                <p role="status">
                  {issued.message}{" "}
                  请在当前浏览器完成验证，5分钟有效，最多尝试5次。
                </p>
                <label>
                  手机验证码
                  <input
                    aria-label="登录找回手机验证码"
                    data-sms-challenge-id={issued.challenge_id}
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    pattern="[0-9]{6}"
                    minLength={6}
                    maxLength={6}
                    required
                    disabled={busy}
                  />
                </label>
                {purpose === "recovery" && (
                  <>
                    <label>
                      新密码
                      <input
                        aria-label="找回新密码"
                        type="password"
                        autoComplete="new-password"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        minLength={12}
                        maxLength={200}
                        required
                        disabled={busy}
                      />
                    </label>
                    <label>
                      确认新密码
                      <input
                        aria-label="确认找回新密码"
                        type="password"
                        autoComplete="new-password"
                        value={confirmation}
                        onChange={(e) => setConfirmation(e.target.value)}
                        minLength={12}
                        maxLength={200}
                        required
                        disabled={busy}
                      />
                    </label>
                  </>
                )}
                <button className="primary" disabled={busy}>
                  {purpose === "login" ? "验证并登录" : "确认重置密码"}
                </button>
                <button
                  type="button"
                  className="outline"
                  disabled={busy || wait > 0}
                  onClick={() => {
                    setIssued(null);
                    setCode("");
                    setPassword("");
                    setConfirmation("");
                    setRefresh((v) => v + 1);
                  }}
                >
                  {wait > 0 ? `${wait}秒后可重新获取` : "重新获取验证码"}
                </button>
              </>
            )}
          </form>
          <div className="auth-sms-links">
            <Link href="/">返回账号密码登录</Link>
            <Link href={purpose === "login" ? "/recover" : "/sms-login"}>
              {purpose === "login" ? "找回密码" : "短信验证码登录"}
            </Link>
          </div>
        </div>
      </section>
    </main>
  );
}
