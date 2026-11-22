"use client";

import { useSidebarViews } from "./business-view-context";
import { useEffect } from "react";

export type LeaveGuardRegistrar = (guard: (() => boolean) | null) => void;

/** The same protection applies to a detail's return action and module navigation. */
export function useWorkspaceLeave(
  dirty: boolean,
  busy: boolean,
  register?: LeaveGuardRegistrar,
) {
  useEffect(() => {
    register?.(
      () =>
        !busy &&
        (!dirty ||
          window.confirm(
            "当前内容尚未保存，确定放弃修改并离开？已上传材料仍保留。",
          )),
    );
    return () => register?.(null);
  }, [dirty, busy, register]);
  useEffect(() => {
    if (!dirty && !busy) return;
    const protect = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [dirty, busy]);
  return () =>
    !busy &&
    (!dirty ||
      window.confirm(
        "当前内容尚未保存，确定放弃修改并返回？已上传材料仍保留。",
      ));
}

export function WorkspaceViews({
  label,
  value,
  items,
  onChange,
  disabled = false,
}: {
  label: string;
  value: string;
  items: readonly (readonly [string, string])[];
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const sidebar = useSidebarViews();
  if (sidebar) return null;
  return (
    <nav className="business-view-navigation" aria-label={label}>
      {items.map(([key, name]) => (
        <button
          type="button"
          key={key}
          aria-pressed={value === key}
          disabled={disabled}
          onClick={() => onChange(key)}
        >
          {name}
        </button>
      ))}
    </nav>
  );
}
