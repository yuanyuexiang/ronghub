"use client";
import { FilePicker } from "./ui/file-picker";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { useEffect, useRef, useState } from "react";
import { usePageRefresh } from "./page-refresh";

export function RatingMaterials({
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
  const [items, setItems] = useState<ApiSchemas["RatingMaterialItem"][]>([]);
  const [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(true),
    [error, setError] = useState("");
  const [categories, setCategories] = useState<Record<string, string>>({});
  const [category, setCategory] = useState("inspection_report");
  const [total, setTotal] = useState(0),
    [limit, setLimit] = useState(20 * 1024 * 1024);
  const controller = useRef<AbortController | null>(null),
    lock = useRef(false);
  const revision = usePageRefresh();
  const base = `/api/v1/ratings/applications/${applicationId}/attachments`;
  async function load() {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setLoading(true);
    try {
      const { data, error } = await typedApi.GET(
        "/api/v1/ratings/applications/{application_id}/attachments",
        {
          params: { path: { application_id: applicationId } },
          cache: "no-store",
          signal: current.signal,
        },
      );
      if (current.signal.aborted) return;
      if (!data) throw new Error(apiError(error, "材料读取失败"));
      setItems(data.items);
      setCategories(data.categories);
      setTotal(data.total_bytes);
      setLimit(data.limit_bytes);
    } catch (error) {
      if (!current.signal.aborted) throw error;
    } finally {
      if (!current.signal.aborted) setLoading(false);
    }
  }
  useEffect(() => {
    setError("");
    load().catch((error) => setError(error.message));
    return () => controller.current?.abort();
  }, [applicationId, version, revision]);
  async function run(operation: () => Promise<void>, refresh = true) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    onBusy(true);
    setError("");
    try {
      await operation();
      if (refresh) await load();
    } catch (error) {
      setError(error instanceof Error ? error.message : "材料操作失败");
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
      form.set("category", category);
      const { data, error } = await typedApi.POST(
        "/api/v1/ratings/applications/{application_id}/attachments",
        {
          params: { path: { application_id: applicationId } },
          headers: { "x-csrf-token": csrf },
          // OpenAPI represents binary input as string; the serializer sends the actual File.
          body: { file: file.name, category },
          bodySerializer: () => form,
        },
      );
      if (!data) throw new Error(apiError(error, "材料上传失败"));
    });
  }
  async function remove(material_id: number) {
    await run(async () => {
      const { data, error } = await typedApi.DELETE(
        "/api/v1/ratings/applications/{application_id}/attachments/{material_id}",
        {
          params: { path: { application_id: applicationId, material_id } },
          headers: { "x-csrf-token": csrf },
        },
      );
      if (!data) throw new Error(apiError(error, "材料删除失败"));
    });
  }
  async function downloadAll() {
    await run(async () => {
      const { data, error } = await typedApi.GET(
        "/api/v1/ratings/applications/{application_id}/attachments/archive",
        {
          params: { path: { application_id: applicationId } },
          cache: "no-store",
          parseAs: "blob",
        },
      );
      if (!data) throw new Error(apiError(error, "材料打包失败"));
      const url = URL.createObjectURL(data),
        link = document.createElement("a");
      link.href = url;
      link.download = `ronghub-rating-${applicationId}-materials.zip`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }, false);
  }
  const disabled = busy || loading || parentBusy;
  return (
    <section className="materials">
      <h3>分类申报材料</h3>
      <p className="muted">
        单份及总量上限20MiB，最多30份；支持ZIP、RAR、PNG、JPG及补充PDF。提交后冻结；正式必填清单待确认。
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {loading && (
        <p className="muted" role="status">
          正在读取评星材料…
        </p>
      )}
      <button
        type="button"
        className="outline"
        disabled={disabled || !items.length}
        onClick={() => void downloadAll()}
      >
        下载全部材料
      </button>
      <p className="muted">
        ZIP包含原始材料及摘要清单；校验异常时整包拒绝，已上传压缩文件不解压。
      </p>
      {items.map((material) => (
        <div className="material-row" key={material.id}>
          <a href={`${base}/${material.id}/download`}>{material.filename}</a>
          <small>
            {material.category_name} · {Math.ceil(material.size / 1024)}KB ·{" "}
            {material.submitted_version === null
              ? "待提交"
              : `提交版本 V${material.submitted_version}`}
          </small>
          {editable && material.submitted_version === null && (
            <button
              type="button"
              className="outline"
              disabled={disabled}
              onClick={() => void remove(material.id)}
            >
              删除未提交材料
            </button>
          )}
        </div>
      ))}
      {!loading && !error && !items.length && (
        <p className="muted">暂无评星材料</p>
      )}
      <p className="muted">
        已用 {(total / 1024 / 1024).toFixed(2)} / {limit / 1024 / 1024}{" "}
        MiB（包含历史证据）
      </p>
      {editable && (
        <label>
          材料分类
          <select
            aria-label="评星材料分类"
            value={category}
            disabled={disabled}
            onChange={(event) => setCategory(event.target.value)}
          >
            {Object.entries(categories).map(([key, name]) => (
              <option key={key} value={key}>
                {name}
              </option>
            ))}
          </select>
        </label>
      )}
      {editable && (
        <label>
          上传评星材料
          <FilePicker
            aria-label="上传评星材料"
            type="file"
            accept="image/png,image/jpeg,application/pdf,.zip,.rar"
            disabled={disabled}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void upload(file);
              event.target.value = "";
            }}
          />
        </label>
      )}
    </section>
  );
}
