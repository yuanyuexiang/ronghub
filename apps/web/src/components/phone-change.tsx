"use client";
import { useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
export function PhoneChange({
  csrf,
  state,
  onChanged,
}: {
  csrf: string;
  state: ApiSchemas["PhoneState"];
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [oldIssued, setOldIssued] = useState<ApiSchemas["SmsIssued"] | null>(
      null,
    ),
    [newIssued, setNewIssued] = useState<ApiSchemas["SmsIssued"] | null>(null);
  const [password, setPassword] = useState(""),
    [phone, setPhone] = useState(""),
    [oldCode, setOldCode] = useState(""),
    [newCode, setNewCode] = useState("");
  function reset() {
    setOpen(false);
    setOldIssued(null);
    setNewIssued(null);
    setPassword("");
    setPhone("");
    setOldCode("");
    setNewCode("");
    setError("");
  }
  async function submit() {
    setBusy(true);
    setError("");
    try {
      const headers = { "x-csrf-token": csrf };
      if (!oldIssued) {
        if (state.version === null)
          throw new Error("绑定版本未知，请刷新状态后再试");
        const result = await typedApi.POST(
          "/api/v1/auth/phone/change/challenges",
          {
            headers,
            body: { current_password: password, phone_version: state.version },
          },
        );
        if (!result.data)
          throw new Error(
            apiError(result.error, "原手机核验未启动，请重新核对绑定状态"),
          );
        setOldIssued(result.data);
        setPassword("");
      } else if (!newIssued) {
        const result = await typedApi.POST(
          "/api/v1/auth/phone/change/new-challenges",
          {
            headers,
            body: {
              old_challenge_id: oldIssued.challenge_id,
              old_code: oldCode,
              phone,
            },
          },
        );
        if (!result.data)
          throw new Error(
            apiError(
              result.error,
              "新手机验证请求未确认，请核对原手机验证码或重新开始",
            ),
          );
        setNewIssued(result.data);
        setOldCode("");
      } else {
        const result = await typedApi.POST(
          "/api/v1/auth/phone/change/confirm",
          {
            headers,
            body: { challenge_id: newIssued.challenge_id, code: newCode },
          },
        );
        if (!result.data)
          throw new Error(
            apiError(result.error, "换绑结果未确认；请重新登录核对绑定状态"),
          );
        reset();
        onChanged();
      }
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "操作结果不确定，请重新登录核对状态",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="phone-change" aria-label="账号手机换绑">
      <h3>更换账号手机</h3>
      <p className="muted">
        验证当前密码、原手机和新手机后生效，成功后退出全部设备。原手机无法使用时，请联系审核人员另行核验。
      </p>
      {!open ? (
        <button
          type="button"
          className="outline"
          disabled={state.sms_mode !== "demo" || state.version === null}
          onClick={() => setOpen(true)}
        >
          开始更换手机
        </button>
      ) : (
        <form
          className="business-form"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <p className="phone-channel-note">
            模拟短信通道，未发送真实短信。请在原手机验证码生成后5分钟内完成全部核验；重新开始会使先前换绑验证失效。
          </p>
          <p>
            当前绑定：<strong>{state.phone_masked}</strong>
          </p>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          {!oldIssued ? (
            <label>
              验证当前密码
              <input
                aria-label="换绑当前密码"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                maxLength={200}
                required
                disabled={busy}
              />
            </label>
          ) : !newIssued ? (
            <>
              <p role="status">原手机验证码已生成。{oldIssued.message}</p>
              <label>
                原手机验证码
                <input
                  aria-label="原手机换绑验证码"
                  data-sms-challenge-id={oldIssued.challenge_id}
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  pattern="[0-9]{6}"
                  minLength={6}
                  maxLength={6}
                  value={oldCode}
                  onChange={(e) => setOldCode(e.target.value)}
                  required
                  disabled={busy}
                />
              </label>
              <label>
                新手机号
                <input
                  aria-label="换绑新手机号"
                  autoComplete="tel"
                  inputMode="tel"
                  maxLength={20}
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  required
                  disabled={busy}
                  placeholder="中国大陆11位手机号，可带+86"
                />
              </label>
            </>
          ) : (
            <>
              <p role="status">
                原手机已核验，新手机验证码已生成；当前绑定尚未更改。
                {newIssued.message}
              </p>
              <p>待验证新手机：{phone}</p>
              <label>
                新手机验证码
                <input
                  aria-label="新手机换绑验证码"
                  data-sms-challenge-id={newIssued.challenge_id}
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  pattern="[0-9]{6}"
                  minLength={6}
                  maxLength={6}
                  value={newCode}
                  onChange={(e) => setNewCode(e.target.value)}
                  required
                  disabled={busy}
                />
              </label>
            </>
          )}
          <button className="primary" disabled={busy}>
            {busy
              ? "正在核验…"
              : !oldIssued
                ? "获取原手机验证码"
                : !newIssued
                  ? "核验原手机并获取新验证码"
                  : "确认换绑并退出登录"}
          </button>
          <button
            className="outline"
            type="button"
            disabled={busy}
            onClick={reset}
          >
            取消本次换绑
          </button>
        </form>
      )}
    </section>
  );
}
