"use client";
import { useEffect, useRef, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
export function LoginCaptcha({
  value,
  onReady,
  answer,
  onAnswer,
  refreshKey,
  busy,
}: {
  value: ApiSchemas["LoginCaptcha"] | null;
  onReady: (value: ApiSchemas["LoginCaptcha"] | null) => void;
  answer: string;
  onAnswer: (value: string) => void;
  refreshKey: number;
  busy: boolean;
}) {
  const inFlight = useRef<Promise<void> | null>(null);
  const [loading, setLoading] = useState(true),
    [error, setError] = useState("");
  function refresh() {
    if (inFlight.current) return inFlight.current;
    setLoading(true);
    setError("");
    onReady(null);
    onAnswer("");
    const pending = typedApi
      .GET("/api/v1/auth/captcha", { cache: "no-store" })
      .then(({ data, error }) => {
        if (!data) throw new Error(apiError(error, "验证码读取失败，请重试"));
        onReady(data);
      })
      .catch((e) => setError(e.message))
      .finally(() => {
        setLoading(false);
        inFlight.current = null;
      });
    inFlight.current = pending;
    return pending;
  }
  useEffect(() => {
    void refresh();
  }, [refreshKey]);
  return (
    <div className="login-captcha">
      <label>
        图片验证码
        <input
          aria-label="图片验证码"
          value={answer}
          onChange={(e) => onAnswer(e.target.value.toUpperCase())}
          required
          maxLength={5}
          minLength={5}
          autoComplete="off"
          spellCheck={false}
          disabled={busy || loading}
          placeholder="请输入图中5位字符"
        />
      </label>
      <div className="captcha-image-row">
        {value && (
          <img
            src={value.image}
            data-captcha-id={value.captcha_id}
            alt="登录图片验证码"
            width={190}
            height={66}
          />
        )}
        <button
          type="button"
          className="outline"
          disabled={busy || loading}
          onClick={() => void refresh()}
        >
          {loading ? "正在读取…" : "换一张验证码"}
        </button>
      </div>
      <p className="muted">
        不区分大小写，2分钟内有效，每张仅可提交一次。无法识别时请刷新或联系管理员协助。
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
