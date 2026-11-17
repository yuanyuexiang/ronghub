"use client";
import { useEffect, useRef, useState } from "react";
import { typedApi, apiError } from "../lib/typed-api";
import { inspectionQueueParams, type QueueQuery } from "./business-queue";
import { useCapability } from "./permissions";

export function InspectionExport({
  query,
  busy,
}: {
  query: QueueQuery;
  busy: boolean;
}) {
  const allowed = useCapability("statistics.export");
  const [exporting, setExporting] = useState(false),
    [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null),
    lock = useRef(false),
    url = useRef<string | null>(null);
  useEffect(
    () => () => {
      controller.current?.abort();
      if (url.current) URL.revokeObjectURL(url.current);
    },
    [],
  );
  useEffect(() => {
    setError("");
  }, [query]);
  if (!allowed) return null;
  async function download() {
    if (busy || lock.current) return;
    lock.current = true;
    setExporting(true);
    setError("");
    const request = new AbortController();
    controller.current = request;
    try {
      const { data, error } = await typedApi.GET("/api/v1/inspections/export", {
        params: { query: inspectionQueueParams(query) },
        parseAs: "blob",
        cache: "no-store",
        signal: request.signal,
      });
      if (request.signal.aborted) return;
      if (!data)
        throw new Error(apiError(error, "导出失败，请缩小查询范围后重试"));
      if (data.type.split(";")[0] !== "text/csv")
        throw new Error("未收到CSV文件，未执行下载，请重试");
      if (url.current) URL.revokeObjectURL(url.current);
      const downloadUrl = URL.createObjectURL(data);
      url.current = downloadUrl;
      const anchor = document.createElement("a");
      anchor.href = downloadUrl;
      anchor.download = "ronghub-inspections.csv";
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => {
        URL.revokeObjectURL(downloadUrl);
        if (url.current === downloadUrl) url.current = null;
      }, 1000);
    } catch (error) {
      if (!request.signal.aborted)
        setError(error instanceof Error ? error.message : "导出失败");
    } finally {
      lock.current = false;
      if (!request.signal.aborted) setExporting(false);
    }
  }
  return (
    <section className="business-export-controls" aria-label="巡检任务导出">
      <button
        type="button"
        className="outline"
        disabled={busy || exporting}
        onClick={() => void download()}
      >
        {exporting ? "正在导出…" : "导出巡检任务 CSV"}
      </button>
      <p className="muted">
        导出全部已应用筛选结果，不受当前页限制；最多10000条，超限请缩小范围，不截断结果。
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </section>
  );
}
