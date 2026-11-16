"use client";
import { usePageRefresh } from "./page-refresh";
import { useEffect, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
export function PhonePanel({
  csrf,
  onBound,
}: {
  csrf: string;
  onBound: () => void;
}) {
  const [state, setState] = useState<ApiSchemas["PhoneState"] | null>(null);
  const [issued, setIssued] = useState<ApiSchemas["SmsIssued"] | null>(null);
  const [phone, setPhone] = useState(""),
    [password, setPassword] = useState(""),
    [code, setCode] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const pageRefreshRevision = usePageRefresh();
  useEffect(() => {
    const controller = new AbortController();
    typedApi
      .GET("/api/v1/auth/phone", {
        cache: "no-store",
        signal: controller.signal,
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "绑定状态读取失败"));
        setState(data);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, [pageRefreshRevision]);
  async function requestCode() {
    setBusy(true);
    setError("");
    try {
      const { data, error } = await typedApi.POST(
        "/api/v1/auth/phone/challenges",
        {
          body: { phone, current_password: password },
          headers: { "x-csrf-token": csrf },
        },
      );
      if (!data) throw new Error(apiError(error, "获取短信验证码失败"));
      setIssued(data);
      setPassword("");
      setCode("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }
  async function confirm() {
    if (!issued) return;
    setBusy(true);
    setError("");
    try {
      const { data, error } = await typedApi.POST(
        "/api/v1/auth/phone/confirm",
        {
          body: { challenge_id: issued.challenge_id, code },
          headers: { "x-csrf-token": csrf },
        },
      );
      if (!data) throw new Error(apiError(error, "手机验证失败"));
      setCode("");
      setPassword("");
      setIssued(null);
      onBound();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel phone-panel">
      <div className="panel-title">
        <div>
          <h2>账号手机验证</h2>
          <p>
            账号手机与企业联系人分开保存；绑定需验证当前密码和手机验证码，成功后退出全部设备。
          </p>
        </div>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!state ? (
        <p className="muted">正在读取绑定状态…</p>
      ) : state.verified ? (
        <div className="business-form">
          <p>
            已验证手机号（模拟验证）：<strong>{state.phone_masked}</strong>
          </p>
          <p>
            验证时间：
            {state.verified_at
              ? new Date(state.verified_at).toLocaleString("zh-CN", {
                  timeZone: "Asia/Shanghai",
                })
              : "未留存"}
          </p>
          <p className="muted">
            此记录仅为模拟验证，不作为生产找回凭据。手机号变更需要额外核验，当前不提供直接覆盖入口。可在登录入口使用模拟短信登录或找回密码；真实通道尚未接入。
          </p>
        </div>
      ) : (
        <>
          <p className="phone-channel-note">
            {state.sms_mode === "demo"
              ? "模拟短信通道：未发送真实短信。请由开发者使用本地模拟验证工具提供本次验证码。"
              : "短信通道尚未配置，暂不能获取验证码。"}
          </p>
          <form
            className="business-form"
            onSubmit={(e) => {
              e.preventDefault();
              void (issued ? confirm() : requestCode());
            }}
          >
            <label>
              绑定手机号
              <input
                aria-label="账号绑定手机号"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                autoComplete="tel"
                inputMode="tel"
                maxLength={20}
                required
                disabled={busy || !!issued}
                placeholder="中国大陆11位手机号，可带+86"
              />
            </label>
            {!issued ? (
              <>
                <label>
                  验证当前密码
                  <input
                    aria-label="绑定手机当前密码"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    type="password"
                    autoComplete="current-password"
                    maxLength={200}
                    required
                    disabled={busy}
                  />
                </label>
                <button
                  className="primary"
                  disabled={busy || state.sms_mode === "disabled"}
                >
                  获取绑定短信验证码
                </button>
              </>
            ) : (
              <>
                <p role="status">{issued.message} 有效期5分钟，最多尝试5次。</p>
                <label>
                  手机短信验证码
                  <input
                    aria-label="手机短信验证码"
                    data-sms-challenge-id={issued.challenge_id}
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    minLength={6}
                    maxLength={6}
                    pattern="[0-9]{6}"
                    required
                    disabled={busy}
                  />
                </label>
                <button className="primary" disabled={busy}>
                  确认验证绑定手机
                </button>
                <button
                  type="button"
                  className="outline"
                  disabled={busy}
                  onClick={() => {
                    setIssued(null);
                    setCode("");
                    setPassword("");
                  }}
                >
                  重新填写绑定信息
                </button>
              </>
            )}
          </form>
        </>
      )}
    </section>
  );
}
