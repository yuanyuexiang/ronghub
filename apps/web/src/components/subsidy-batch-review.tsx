"use client";

import { useRef, useState } from "react";
import { subsidyApi } from "../lib/business-api";
import type { ApiSchemas } from "../lib/typed-api";

export type SelectedSubsidy = Pick<
  ApiSchemas["SubsidyQueueItem"],
  | "id"
  | "version"
  | "station_name"
  | "operator_name"
  | "adjusted_amount"
  | "start_date"
  | "end_date"
>;
type Pending = {
  action: "approve" | "reject";
  items: SelectedSubsidy[];
  comment: string;
};

export function SubsidyBatchReview({
  selection,
  disabled,
  csrf,
  onChanged,
  onOpenChange,
  onBusyChange,
  onClear,
  onCompleted,
}: {
  selection: SelectedSubsidy[];
  disabled: boolean;
  csrf: string;
  onChanged: () => void;
  onOpenChange: (open: boolean) => void;
  onBusyChange: (busy: boolean) => void;
  onClear: () => void;
  onCompleted: (items: ApiSchemas["SubsidyClaimResponse"][]) => Promise<void>;
}) {
  const [comment, setComment] = useState(""),
    [pending, setPending] = useState<Pending | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const lock = useRef(false);
  const api = subsidyApi(csrf, onChanged);
  function prepare(action: Pending["action"]) {
    if (disabled || busy || pending || !selection.length || !comment.trim())
      return;
    setError("");
    setNotice("");
    setPending({
      action,
      items: selection.map((item) => ({ ...item })),
      comment: comment.trim(),
    });
    onOpenChange(true);
  }
  async function confirm() {
    if (!pending || disabled || lock.current) return;
    lock.current = true;
    setBusy(true);
    onBusyChange(true);
    setError("");
    try {
      const result = await api.batch({
        action: pending.action,
        comment: pending.comment,
        items: pending.items.map(({ id, version }) => ({ id, version })),
      });
      const decision = pending.action === "approve" ? "通过" : "驳回";
      setPending(null);
      onOpenChange(false);
      setComment("");
      setNotice(
        `已批量${decision}${result.total}条补贴申请，逐条办理记录已保存。`,
      );
      await onCompleted(result.items);
    } catch (error) {
      setError(
        error instanceof Error
          ? error.message
          : "未确认办理结果，请刷新记录核对",
      );
    } finally {
      lock.current = false;
      setBusy(false);
      onBusyChange(false);
    }
  }
  return (
    <section className="subsidy-batch" aria-label="补贴批量审核">
      <div className="subsidy-batch-bar">
        <strong>已选{selection.length}/100条待审补贴</strong>
        <button
          type="button"
          className="outline"
          disabled={disabled || busy || !!pending || !selection.length}
          onClick={() => {
            onClear();
            setError("");
            setNotice("");
          }}
        >
          清空补贴选择
        </button>
      </div>
      <p className="muted">
        跨页保留所选版本；查询或重置会清空，刷新不更新已选版本。只办理明确所选记录，每批最多100条。
      </p>
      <label>
        统一审核意见
        <textarea
          aria-label="批量补贴审核意见"
          maxLength={2000}
          value={comment}
          disabled={disabled || busy || !!pending}
          onChange={(event) => setComment(event.target.value)}
        />
      </label>
      <div className="action-row">
        <button
          type="button"
          className="outline"
          disabled={
            disabled ||
            busy ||
            !!pending ||
            !selection.length ||
            !comment.trim()
          }
          onClick={() => prepare("approve")}
        >
          批量通过补贴审核
        </button>
        <button
          type="button"
          className="outline"
          disabled={
            disabled ||
            busy ||
            !!pending ||
            !selection.length ||
            !comment.trim()
          }
          onClick={() => prepare("reject")}
        >
          批量驳回补贴申请
        </button>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {pending && (
        <section
          className="subsidy-batch-confirm"
          aria-label="补贴批量审核确认"
        >
          <h3>
            {pending.action === "approve" ? "确认批量通过" : "确认批量驳回"}
          </h3>
          <p>
            共{pending.items.length}
            条。任一权限、版本或状态冲突，整批不提交。金额按所选版本核对，不调整金额、不登记发放。
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>申请 / 版本</th>
                  <th>场站 / 企业（选择时名称）</th>
                  <th>周期</th>
                  <th>调整后金额（元）</th>
                </tr>
              </thead>
              <tbody>
                {pending.items.map((item) => (
                  <tr key={item.id}>
                    <td>
                      SUB-{item.id} · V{item.version}
                    </td>
                    <td>
                      {item.station_name}
                      <br />
                      <small>{item.operator_name}</small>
                    </td>
                    <td>
                      {item.start_date} / {item.end_date}
                    </td>
                    <td>{item.adjusted_amount}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="subsidy-batch-comment">审核意见：{pending.comment}</p>
          <button
            type="button"
            className="primary"
            disabled={disabled || busy}
            onClick={() => void confirm()}
          >
            {busy
              ? "正在提交整批审核…"
              : pending.action === "approve"
                ? "确认通过所选补贴"
                : "确认驳回所选补贴"}
          </button>
          <button
            type="button"
            className="outline"
            disabled={busy}
            onClick={() => {
              setPending(null);
              onOpenChange(false);
              setError("");
            }}
          >
            取消批量审核
          </button>
          <p className="muted">
            遇到版本冲突，请取消后重新核对和选择；网络中断未收到结果时，先刷新核对办理记录。
          </p>
        </section>
      )}
    </section>
  );
}
