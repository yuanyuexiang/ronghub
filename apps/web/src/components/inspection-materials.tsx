"use client";
import { typedApi, apiError } from "../lib/typed-api";
import { useCallback, useRef, useState } from "react";
import { usePrivateMaterials } from "./use-private-materials";
export function InspectionMaterials({
  taskId,
  version,
  editable,
  csrf,
  parentBusy,
  onBusy,
}: {
  taskId: number;
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
        "/api/v1/inspections/{task_id}/attachments",
        {
          params: { path: { task_id: taskId } },
          cache: "no-store",
          signal,
        },
      );
      if (!data) throw new Error(apiError(error, "整改材料读取失败"));
      return data.items;
    },
    [taskId, version],
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
      setError(error instanceof Error ? error.message : "整改材料操作失败");
    } finally {
      lock.current = false;
      setBusy(false);
      onBusy(false);
    }
  }
  async function upload(file: File) {
    await run(async () => {
      const form = new FormData();
      form.set("file", file);
      const { data, error } = await typedApi.POST(
        "/api/v1/inspections/{task_id}/attachments",
        {
          params: { path: { task_id: taskId } },
          headers: { "x-csrf-token": csrf },
          body: { file: file.name },
          bodySerializer: () => form,
        },
      );
      if (!data) throw new Error(apiError(error, "整改材料上传失败"));
    });
  }
  async function remove(attachment_id: number) {
    await run(async () => {
      const { data, error } = await typedApi.DELETE(
        "/api/v1/inspections/{task_id}/attachments/{attachment_id}",
        {
          params: { path: { task_id: taskId, attachment_id } },
          headers: { "x-csrf-token": csrf },
        },
      );
      if (!data) throw new Error(apiError(error, "整改材料删除失败"));
    });
  }
  const disabled = busy || loading || parentBusy;
  return (
    <section className="materials">
      <h3>整改图片与材料</h3>
      <p className="muted">
        每项任务最多30份，单份5MiB；支持PNG、JPG、PDF。回复后按处理记录冻结保留。
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {loading && (
        <p role="status" className="muted">
          正在读取整改材料…
        </p>
      )}
      {items.map((a) => (
        <div className="material-row" key={a.id}>
          <a
            href={`/api/v1/inspections/${taskId}/attachments/${a.id}/download`}
          >
            {a.filename}
          </a>
          <small>
            {Math.ceil(a.size / 1024)}KB ·{" "}
            {a.action_id === null ? "待提交" : `回复记录 #${a.action_id}`}
          </small>
          {editable && a.action_id === null && (
            <button
              type="button"
              className="outline"
              disabled={disabled}
              onClick={() => void remove(a.id)}
            >
              删除未提交材料
            </button>
          )}
        </div>
      ))}
      {!loading && !error && !items.length && (
        <p className="muted">暂无整改材料</p>
      )}
      {editable && (
        <label>
          上传整改材料
          <input
            aria-label="上传整改材料"
            type="file"
            accept="image/png,image/jpeg,application/pdf"
            disabled={disabled}
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (file) void upload(file);
            }}
          />
        </label>
      )}
    </section>
  );
}
