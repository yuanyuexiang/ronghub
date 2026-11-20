"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { usePrivateMaterials } from "./use-private-materials";

export function SubsidyEvidence({
  claim,
  canEdit,
  canMaterials,
  csrf,
  parentBusy,
  onBusy,
  onDirty,
  onReady,
  onUpdated,
}: {
  claim: ApiSchemas["SubsidyClaimDetail"];
  canEdit: boolean;
  canMaterials: boolean;
  csrf: string;
  parentBusy: boolean;
  onBusy: (busy: boolean) => void;
  onDirty?: (value: boolean) => void;
  onReady: (ready: boolean) => void;
  onUpdated: () => Promise<void>;
}) {
  const [note, setNote] = useState(claim.application_note ?? ""),
    [busy, setBusy] = useState(false),
    [listing, setListing] = useState<{ basis: number; actual: number } | null>(
      null,
    ),
    [limit, setLimit] = useState(20 * 1024 * 1024);
  const lock = useRef(false);
  useEffect(
    () => setNote(claim.application_note ?? ""),
    [claim.id, claim.version, claim.application_note],
  );
  const read = useCallback(
    async (signal: AbortSignal) => {
      const { data, error } = await typedApi.GET(
        "/api/v1/subsidy/claims/{claim_id}/attachments",
        { params: { path: { claim_id: claim.id } }, cache: "no-store", signal },
      );
      if (!data) throw new Error(apiError(error, "申报材料读取失败"));
      if (!signal.aborted) {
        setListing({ basis: claim.version, actual: data.version });
        setLimit(data.limit_bytes);
      }
      return data.items;
    },
    [claim.id, claim.version],
  );
  const { items, loading, error, setError } = usePrivateMaterials(read);
  const dirty = note !== (claim.application_note ?? "");
  useEffect(() => {
    onDirty?.(dirty);
    return () => onDirty?.(false);
  }, [dirty, onDirty]);
  const listedForCurrent = listing?.basis === claim.version;
  const stale =
    !loading && listedForCurrent && listing.actual !== claim.version;
  useEffect(
    () =>
      onReady(
        !dirty &&
          listedForCurrent &&
          !stale &&
          !loading &&
          !error &&
          !!note.trim() &&
          items.length > 0,
      ),
    [
      dirty,
      listedForCurrent,
      stale,
      loading,
      error,
      note,
      items.length,
      onReady,
    ],
  );
  async function run(operation: () => Promise<void>) {
    if (lock.current || parentBusy || stale) return;
    lock.current = true;
    setBusy(true);
    onBusy(true);
    setError("");
    try {
      await operation();
      await onUpdated();
    } catch (error) {
      setError(
        error instanceof Error
          ? error.message
          : "未确认办理结果，请重新打开详情核对",
      );
    } finally {
      lock.current = false;
      setBusy(false);
      onBusy(false);
    }
  }
  async function save() {
    await run(async () => {
      const { data, error } = await typedApi.PATCH(
        "/api/v1/subsidy/claims/{claim_id}",
        {
          params: { path: { claim_id: claim.id } },
          headers: { "x-csrf-token": csrf },
          body: { version: claim.version, application_note: note },
        },
      );
      if (!data) throw new Error(apiError(error, "申请说明保存失败"));
      setListing({ basis: claim.version, actual: data.version });
    });
  }
  async function upload(file: File) {
    await run(async () => {
      const form = new FormData();
      form.set("file", file);
      form.set("version", String(claim.version));
      const { data, error } = await typedApi.POST(
        "/api/v1/subsidy/claims/{claim_id}/attachments",
        {
          params: { path: { claim_id: claim.id } },
          headers: { "x-csrf-token": csrf },
          body: { file: file.name, version: claim.version },
          bodySerializer: () => form,
        },
      );
      if (!data) throw new Error(apiError(error, "ZIP材料上传失败"));
      setListing({ basis: claim.version, actual: data.version });
    });
  }
  async function remove(material_id: number) {
    await run(async () => {
      const { data, error } = await typedApi.DELETE(
        "/api/v1/subsidy/claims/{claim_id}/attachments/{material_id}",
        {
          params: {
            path: { claim_id: claim.id, material_id },
            query: { version: claim.version },
          },
          headers: { "x-csrf-token": csrf },
        },
      );
      if (!data) throw new Error(apiError(error, "材料删除失败"));
      setListing({ basis: claim.version, actual: data.version });
    });
  }
  const disabled = parentBusy || busy || loading || stale;
  return (
    <section
      className="materials subsidy-evidence"
      aria-label="补贴申请说明与材料"
    >
      <h3>申请说明与申报材料</h3>
      {canEdit ? (
        <>
          <label>
            申请说明（提交时必填）
            <textarea
              aria-label="修改补贴申请说明"
              value={note}
              maxLength={2000}
              disabled={disabled}
              onChange={(event) => setNote(event.target.value)}
            />
          </label>
          <button
            type="button"
            className="outline"
            disabled={disabled || !dirty}
            onClick={() => void save()}
          >
            保存补贴申请说明
          </button>
          {dirty && (
            <button
              type="button"
              className="outline"
              disabled={disabled}
              onClick={() => setNote(claim.application_note ?? "")}
            >
              还原未保存说明
            </button>
          )}
        </>
      ) : (
        <p className="subsidy-statement">
          {claim.application_note === null
            ? "历史申请说明未留存"
            : claim.application_note || "未填写申请说明"}
        </p>
      )}
      {stale && (
        <p role="alert" className="error">
          申请已有更新，未保存说明保留；请先复制需要的内容，再重新打开详情核对。
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {loading && <p role="status">正在读取ZIP申报材料…</p>}
      <p className="muted">
        仅ZIP，最多10份、单份及总量{limit / 1024 / 1024}
        MiB；不解压上传内容。提交前必须有说明和材料，提交后冻结。正式大小与清单待确认。
      </p>
      {items.map((item) => (
        <div className="material-row" key={item.id}>
          <a
            href={`/api/v1/subsidy/claims/${claim.id}/attachments/${item.id}/download`}
          >
            {item.filename}
          </a>
          <small>
            {Math.ceil(item.size / 1024)}KB ·{" "}
            {item.submitted_version === null
              ? claim.status === "draft"
                ? "待提交"
                : "未随提交冻结"
              : `提交版本 V${item.submitted_version}`}
          </small>
          {canMaterials && item.submitted_version === null && (
            <button
              type="button"
              className="outline"
              disabled={disabled || dirty}
              onClick={() => void remove(item.id)}
            >
              删除补贴材料
            </button>
          )}
        </div>
      ))}
      {!loading && !error && !items.length && (
        <p className="muted">
          {claim.status === "draft"
            ? "尚未上传ZIP申报材料"
            : claim.snapshot?.submission
              ? "当前未读取到已提交材料，请联系管理员核查"
              : "历史申报材料未留存"}
        </p>
      )}
      {claim.snapshot?.submission && (
        <p className="muted">
          已提交资料快照 V{claim.snapshot.submission.version}
          ；说明与材料摘要随提交保存。
        </p>
      )}
      {canMaterials && (
        <label>
          上传ZIP申报材料
          <input
            type="file"
            aria-label="上传补贴ZIP材料"
            accept=".zip,application/zip"
            disabled={disabled || dirty}
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (file) void upload(file);
            }}
          />
        </label>
      )}
      {dirty && <p className="muted">请先保存申请说明，再维护材料或提交。</p>}
    </section>
  );
}
