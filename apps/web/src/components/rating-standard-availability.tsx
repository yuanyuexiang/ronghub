"use client";
import { useEffect, useRef, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
type Standard = ApiSchemas["RatingStandardResponse"];
export function RatingStandardAvailability({
  standard,
  csrf,
  parentBusy,
  onBusy,
  onChanged,
}: {
  standard: Standard;
  csrf: string;
  parentBusy: boolean;
  onBusy: (busy: boolean) => void;
  onChanged: () => Promise<void>;
}) {
  const [pending, setPending] = useState<{
      row: Standard;
      action: "availability" | "archive";
    } | null>(null),
    [sending, setSending] = useState(false),
    [error, setError] = useState("");
  const owned = useRef(false),
    inFlight = useRef(false);
  function release() {
    if (owned.current) {
      owned.current = false;
      onBusy(false);
    }
  }
  useEffect(
    () => () => {
      if (owned.current) onBusy(false);
    },
    [onBusy],
  );
  function prepare(action: "availability" | "archive") {
    if (parentBusy) return;
    setError("");
    setPending({ row: standard, action });
    owned.current = true;
    onBusy(true);
  }
  async function confirm() {
    if (!pending || inFlight.current) return;
    inFlight.current = true;
    setSending(true);
    setError("");
    try {
      const headers = { "x-csrf-token": csrf },
        id = pending.row.id;
      const response =
        pending.action === "archive"
          ? await typedApi.DELETE("/api/v1/ratings/standards/{standard_id}", {
              headers,
              params: {
                path: { standard_id: id },
                query: { revision: pending.row.revision },
              },
            })
          : await typedApi.POST(
              "/api/v1/ratings/standards/{standard_id}/availability",
              {
                headers,
                params: { path: { standard_id: id } },
                body: {
                  revision: pending.row.revision,
                  enabled: !pending.row.enabled,
                },
              },
            );
      if (!response.data)
        throw new Error(
          apiError(
            response.error,
            "标准状态办理未确认，请核对当前状态与管理修订后再重试",
          ),
        );
      setPending(null);
      release();
      try {
        await onChanged();
      } catch {
        setError("标准状态已更新，但列表刷新失败，请刷新核对");
      }
    } catch (error) {
      setError(error instanceof Error ? error.message : "办理失败");
    } finally {
      inFlight.current = false;
      setSending(false);
    }
  }
  return (
    <div className="standard-availability">
      <button
        className="outline"
        disabled={parentBusy}
        aria-label={`${standard.enabled ? "停用" : "启用"}标准 ${standard.code} V${standard.version}`}
        onClick={() => prepare("availability")}
      >
        {standard.enabled ? "停用" : "启用"}
      </button>
      <button
        className="outline"
        disabled={parentBusy}
        aria-label={`删除标准 ${standard.code} V${standard.version}`}
        onClick={() => prepare("archive")}
      >
        删除
      </button>
      <small>
        管理修订 {standard.revision} · {standard.enabled ? "已启用" : "已停用"}
      </small>
      {pending && (
        <div
          className="standard-confirm"
          role="region"
          aria-label={`标准状态确认 ${pending.row.id}`}
        >
          <b>
            {pending.action === "archive"
              ? "删除归档"
              : pending.row.enabled
                ? "停用"
                : "启用"}{" "}
            {pending.row.name} V{pending.row.version}？
          </b>
          <p>
            固定管理修订 {pending.row.revision}
            。停用只阻止新申请，历史申请沿用原快照；删除仅限未被申请引用的标准，配置与日志留档。网络异常请核对当前状态，不盲目重复办理。
          </p>
          <button
            className="outline"
            disabled={sending}
            onClick={() => {
              setPending(null);
              release();
            }}
          >
            取消标准状态办理
          </button>{" "}
          <button className="primary" disabled={sending} onClick={confirm}>
            {sending ? "正在办理标准状态…" : "确认标准状态办理"}
          </button>
        </div>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
