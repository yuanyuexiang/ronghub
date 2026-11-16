"use client";
import { typedApi, apiError } from "../lib/typed-api";
import { useCallback, useRef, useState } from "react";
import { usePrivateMaterials } from "./use-private-materials";
export function Attachments({
  applicationId,
  version,
  editable,
  csrf,
  parentBusy,
  onBusy,
}: {
  applicationId: number;
  version: number;
  editable: boolean;
  csrf: string;
  parentBusy: boolean;
  onBusy: (busy: boolean) => void;
}) {
  const [busy, setBusy] = useState(false),
    lock = useRef(false);
  const read = useCallback(
    async (signal: AbortSignal) => {
      const { data, error } = await typedApi.GET(
        "/api/v1/applications/{application_id}/attachments",
        {
          params: { path: { application_id: applicationId } },
          cache: "no-store",
          signal,
        },
      );
      if (!data) throw new Error(apiError(error, "材料列表读取失败"));
      return data.items;
      // Version changes after submit/reject refresh metadata without remounting the detail.
    },
    [applicationId, version],
  );
  const { items, loading, error, setError, reload } = usePrivateMaterials(read);
  async function run(operation: () => Promise<void>) {
    if (lock.current || parentBusy) return;
    lock.current = true;
    setBusy(true);
    onBusy(true);
    setError("");
    try {
      await operation();
      await reload();
    } catch (error) {
      setError(error instanceof Error ? error.message : "材料操作失败");
    } finally {
      lock.current = false;
      setBusy(false);
      onBusy(false);
    }
  }
  async function upload(file: File) {
    if (file.size > 5 * 1024 * 1024) {
      setError("单份材料不能超过5MiB");
      return;
    }
    await run(async () => {
      const form = new FormData();
      form.set("file", file);
      const { data, error } = await typedApi.POST(
        "/api/v1/applications/{application_id}/attachments",
        {
          params: { path: { application_id: applicationId } },
          headers: { "x-csrf-token": csrf },
          body: { file: file.name },
          bodySerializer: () => form,
        },
      );
      if (!data) throw new Error(apiError(error, "上传申请材料失败"));
    });
  }
  async function remove(attachment_id: number) {
    await run(async () => {
      const { data, error } = await typedApi.DELETE(
        "/api/v1/attachments/{attachment_id}",
        {
          params: { path: { attachment_id } },
          headers: { "x-csrf-token": csrf },
        },
      );
      if (!data)
        throw new Error(apiError(error, "删除失败；材料可能已进入审核"));
    });
  }
  const disabled = busy || loading || parentBusy;
  return (
    <section className="materials">
      <h3>申请材料</h3>
      <p className="muted">
        PDF / PNG /
        JPEG，每份不超过5MiB，最多10份；提交后材料锁定。具体必交材料待客户确认。
      </p>
      {loading && <p role="status">正在加载材料…</p>}
      {items.length ? (
        <ul>
          {items.map((a) => (
            <li key={a.id}>
              <a href={`/api/v1/attachments/${a.id}/download`}>{a.filename}</a>
              <small>
                {a.size < 1024
                  ? `${a.size} B`
                  : `${(a.size / 1024).toFixed(1)} KB`}
              </small>
              {editable && (
                <button
                  type="button"
                  disabled={disabled}
                  className="outline"
                  onClick={() => void remove(a.id)}
                >
                  删除
                </button>
              )}
            </li>
          ))}
        </ul>
      ) : (
        !loading && !error && <p className="muted">尚未上传材料</p>
      )}
      {editable && (
        <label className="upload-label">
          {busy ? "正在处理材料…" : "上传申请材料"}
          <input
            type="file"
            aria-label="上传申请材料"
            accept="application/pdf,image/png,image/jpeg"
            disabled={disabled}
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (file) void upload(file);
            }}
          />
        </label>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </section>
  );
}
