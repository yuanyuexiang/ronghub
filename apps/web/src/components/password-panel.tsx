"use client";
import { useState } from "react";
import { apiFetch } from "../lib/api-fetch";
export function PasswordPanel({
  csrf,
  required,
  onChanged,
}: {
  csrf: string;
  required: boolean;
  onChanged: () => void;
}) {
  const [current, setCurrent] = useState(""),
    [next, setNext] = useState(""),
    [confirm, setConfirm] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <section className="panel">
      <div className="panel-title">
        <div>
          <h2>{required ? "首次登录请修改临时密码" : "修改本人密码"}</h2>
          <p>至少12个字符；成功后退出全部设备，并需重新登录。</p>
        </div>
      </div>
      <form
        className="business-form"
        onSubmit={async (e) => {
          e.preventDefault();
          setError("");
          if (next !== confirm) {
            setError("两次新密码不一致");
            return;
          }
          setBusy(true);
          try {
            const response = await apiFetch("/api/v1/auth/password", {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                "x-csrf-token": csrf,
              },
              body: JSON.stringify({
                current_password: current,
                new_password: next,
              }),
            });
            const data = await response.json();
            if (!response.ok)
              throw new Error(
                typeof data.detail === "string"
                  ? data.detail
                  : "请检查密码长度",
              );
            setCurrent("");
            setNext("");
            setConfirm("");
            onChanged();
          } catch (e) {
            setError(e instanceof Error ? e.message : "修改失败");
          } finally {
            setBusy(false);
          }
        }}
      >
        <label>
          当前密码
          <input
            aria-label="本人当前密码"
            type="password"
            autoComplete="current-password"
            required
            maxLength={200}
            disabled={busy}
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
        </label>
        <label>
          新密码
          <input
            aria-label="本人新密码"
            type="password"
            autoComplete="new-password"
            required
            minLength={12}
            maxLength={200}
            disabled={busy}
            value={next}
            onChange={(e) => setNext(e.target.value)}
          />
        </label>
        <label>
          确认新密码
          <input
            aria-label="确认本人新密码"
            type="password"
            autoComplete="new-password"
            required
            minLength={12}
            maxLength={200}
            disabled={busy}
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
        </label>
        <button className="outline" disabled={busy}>
          {busy ? "正在修改…" : "更新密码并重新登录"}
        </button>
      </form>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
export function AdminPasswordReset({
  account,
  csrf,
  onChanged,
}: {
  account: { id: number; username: string; version: number };
  csrf: string;
  onChanged: () => Promise<void>;
}) {
  const [password, setPassword] = useState(""),
    [temporary, setTemporary] = useState(""),
    [confirm, setConfirm] = useState(""),
    [reason, setReason] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <section className="materials">
      <h3>{account.username} · 协助重置密码</h3>
      <p className="muted">
        请先核验账号持有人身份。重置不修改角色或启用状态；旧会话立即失效，临时密码需通过已核实渠道交付，首次登录必须改密。
      </p>
      <form
        className="business-form"
        onSubmit={async (e) => {
          e.preventDefault();
          setError("");
          if (temporary !== confirm) {
            setError("两次临时密码不一致");
            return;
          }
          setBusy(true);
          try {
            const response = await apiFetch(
              `/api/v1/accounts/${account.id}/password-reset`,
              {
                method: "POST",
                headers: {
                  "Content-Type": "application/json",
                  "x-csrf-token": csrf,
                },
                body: JSON.stringify({
                  version: account.version,
                  admin_password: password,
                  temporary_password: temporary,
                  reason,
                }),
              },
            );
            const data = await response.json();
            if (!response.ok)
              throw new Error(
                typeof data.detail === "string"
                  ? data.detail
                  : "重置失败，请检查输入",
              );
            setPassword("");
            setTemporary("");
            setConfirm("");
            await onChanged();
          } catch (e) {
            setError(e instanceof Error ? e.message : "重置失败");
          } finally {
            setBusy(false);
          }
        }}
      >
        <label>
          管理员当前密码
          <input
            type="password"
            aria-label="重置验证管理员密码"
            autoComplete="current-password"
            required
            maxLength={200}
            disabled={busy}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <label>
          临时密码
          <input
            type="password"
            aria-label="账号临时密码"
            autoComplete="new-password"
            required
            minLength={12}
            maxLength={200}
            disabled={busy}
            value={temporary}
            onChange={(e) => setTemporary(e.target.value)}
          />
        </label>
        <label>
          确认临时密码
          <input
            type="password"
            aria-label="确认账号临时密码"
            autoComplete="new-password"
            required
            minLength={12}
            maxLength={200}
            disabled={busy}
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
        </label>
        <label>
          身份核验与重置依据
          <input
            aria-label="密码重置依据"
            required
            maxLength={1000}
            disabled={busy}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </label>
        <button className="outline" disabled={busy}>
          {busy ? "正在重置…" : "重置并要求首次改密"}
        </button>
      </form>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </section>
  );
}
