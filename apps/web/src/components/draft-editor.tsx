"use client";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { useState } from "react";
export function DraftEditor({
  application,
  csrf,
  onSaved,
  parentBusy,
  onBusy,
}: {
  application: ApiSchemas["StationApplicationRecord"];
  csrf: string;
  onSaved: () => Promise<void>;
  parentBusy: boolean;
  onBusy: (busy: boolean) => void;
}) {
  const [note, setNote] = useState(
      typeof application.payload.note === "string"
        ? application.payload.note
        : "",
    ),
    [name, setName] = useState(
      typeof application.payload.name === "string"
        ? application.payload.name
        : "",
    ),
    [address, setAddress] = useState(
      typeof application.payload.address === "string"
        ? application.payload.address
        : "",
    ),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function save() {
    if (busy || parentBusy) return;
    setBusy(true);
    onBusy(true);
    setError("");
    try {
      const { data, error } = await typedApi.PATCH(
        "/api/v1/applications/{application_id}",
        {
          params: { path: { application_id: application.id } },
          headers: { "x-csrf-token": csrf },
          body: {
            version: application.version,
            note,
            name: application.kind === "change" ? name : null,
            address: application.kind === "change" ? address : null,
          },
        },
      );
      if (!data) throw new Error(apiError(error, "请检查申请字段"));
      await onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
      onBusy(false);
    }
  }
  return (
    <section className="materials draft-editor">
      <h3>申请信息</h3>
      <label>
        申请说明
        <textarea
          aria-label="申请说明"
          disabled={busy || parentBusy}
          maxLength={2000}
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </label>
      {application.kind === "change" && (
        <>
          <label>
            拟变更场站名称
            <input
              disabled={busy || parentBusy}
              value={name}
              maxLength={150}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label>
            拟变更地址
            <input
              disabled={busy || parentBusy}
              value={address}
              maxLength={255}
              onChange={(e) => setAddress(e.target.value)}
            />
          </label>
        </>
      )}
      <button className="outline" disabled={busy || parentBusy} onClick={save}>
        {busy ? "保存中…" : "保存申请信息"}
      </button>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
