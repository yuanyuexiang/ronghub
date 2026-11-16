"use client";

import { useState } from "react";
import { apiError, typedApi } from "../lib/typed-api";
import { useCapability } from "./permissions";
import { businessDateParams, type QueueQuery } from "./business-queue";

export function BusinessExports({
  module,
  query,
  busy,
}: {
  module: "subsidy" | "ratings";
  query: QueueQuery;
  busy: boolean;
}) {
  const allowed = useCapability(module + ".export");
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState("");
  if (!allowed) return null;
  async function download(process: boolean) {
    setExporting(true);
    setError("");
    const path =
      module === "subsidy"
        ? process
          ? "/api/v1/subsidy/claims/process/export"
          : "/api/v1/subsidy/claims/export"
        : process
          ? "/api/v1/ratings/applications/process/export"
          : "/api/v1/ratings/applications/export";
    try {
      const { data, error } = await typedApi.GET(path, {
        parseAs: "blob",
        cache: "no-store",
        params: {
          query: {
            ...businessDateParams(query, module === "subsidy"),
            q: query.q,
            status: query.status,
            record_id: query.record_id ? Number(query.record_id) : undefined,
            kind: module === "ratings" ? query.kind : undefined,
          },
        },
      });
      if (!data)
        throw new Error(apiError(error, "导出失败，请缩小查询范围后重试"));
      const url = URL.createObjectURL(data);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `ronghub-${module}-${process ? "process" : "applications"}.csv`;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      setError(error instanceof Error ? error.message : "导出失败");
    } finally {
      setExporting(false);
    }
  }
  return (
    <div className="business-export-controls">
      <div className="form-actions">
        <button
          className="outline"
          disabled={busy || exporting}
          onClick={() => void download(false)}
        >
          导出申请清单
        </button>
        <button
          className="outline"
          disabled={busy || exporting}
          onClick={() => void download(true)}
        >
          导出办理过程
        </button>
        <small className="muted">
          按已查询条件导出全部匹配记录，每次最多10000条；过程按动作计数。
        </small>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
