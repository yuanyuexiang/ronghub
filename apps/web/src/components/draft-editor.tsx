"use client";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { useEffect, useState } from "react";
export function DraftEditor({
  application,
  csrf,
  onSaved,
  parentBusy,
  onBusy,
  onDirty,
}: {
  application: ApiSchemas["StationApplicationRecord"];
  csrf: string;
  onSaved: () => Promise<void>;
  parentBusy: boolean;
  onBusy: (busy: boolean) => void;
  onDirty: (dirty: boolean) => void;
}) {
  const rawContact = application.payload.contact;
  const contact =
    rawContact && typeof rawContact === "object" && !Array.isArray(rawContact)
      ? rawContact
      : null;
  const [contactName, setContactName] = useState(
    contact && "name" in contact && typeof contact.name === "string"
      ? contact.name
      : "",
  );
  const [contactPhone, setContactPhone] = useState(
    contact && "phone" in contact && typeof contact.phone === "string"
      ? contact.phone
      : "",
  );
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
  const originalNote =
    typeof application.payload.note === "string"
      ? application.payload.note
      : "";
  const originalName =
    typeof application.payload.name === "string"
      ? application.payload.name
      : "";
  const originalAddress =
    typeof application.payload.address === "string"
      ? application.payload.address
      : "";
  const originalContactName =
    contact && "name" in contact && typeof contact.name === "string"
      ? contact.name
      : "";
  const originalContactPhone =
    contact && "phone" in contact && typeof contact.phone === "string"
      ? contact.phone
      : "";
  const dirty =
    note !== originalNote ||
    name !== originalName ||
    address !== originalAddress ||
    contactName !== originalContactName ||
    contactPhone !== originalContactPhone;
  useEffect(() => {
    onDirty(dirty);
  }, [dirty, onDirty]);
  useEffect(() => () => onDirty(false), [onDirty]);
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
            contact_name: contactName || null,
            contact_phone: contactPhone || null,
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
      <label>
        本次申请联系人（可选，与联系方式同时填写）
        <input
          aria-label="申请联系人"
          value={contactName}
          onChange={(e) => setContactName(e.target.value)}
          maxLength={100}
          disabled={busy || parentBusy}
        />
      </label>
      <label>
        本次申请联系方式
        <input
          aria-label="申请联系方式"
          value={contactPhone}
          onChange={(e) => setContactPhone(e.target.value)}
          maxLength={80}
          disabled={busy || parentBusy}
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
