"use client";

import { useEffect, useRef, type RefObject } from "react";
import type { AlertWorkspaceView } from "./workspace-navigation";

/** URLs identify modules only. Private records and unsaved form fields stay out of history. */
export function useWorkspaceRoute({
  identity,
  tab,
  alertView,
  view,
  allowedViews,
  allowed,
  guard,
  onRestore,
}: {
  identity: string | undefined;
  tab: string;
  alertView: AlertWorkspaceView;
  view: string;
  allowedViews: RefObject<Record<string, string[]>>;
  allowed: RefObject<string[]>;
  guard: RefObject<(() => boolean) | null>;
  onRestore: (tab: string, alertView: AlertWorkspaceView, view: string) => void;
}) {
  const ready = useRef(false);
  const restoring = useRef(false);
  const current = useRef({ tab, alertView, view });
  const restore = useRef(onRestore);
  restore.current = onRestore;
  current.current = { tab, alertView, view };
  useEffect(() => {
    if (identity === undefined) {
      ready.current = false;
      return;
    }
    const read = () => {
      const params = new URLSearchParams(window.location.search);
      const requested = params.get("module") ?? "工作台";
      const target = allowed.current.includes(requested) ? requested : "工作台";
      const rawView = params.get("alert") ?? "events";
      const view: AlertWorkspaceView =
        rawView === "rules" ||
        rawView === "sources" ||
        rawView === "notifications"
          ? rawView
          : "events";
      const requestedView = params.get("view") ?? "";
      const workspaceView = allowedViews.current[target]?.includes(
        requestedView,
      )
        ? requestedView
        : "";
      return { tab: target, alertView: view, view: workspaceView };
    };
    const initial = read();
    const initialParams = new URLSearchParams({ module: initial.tab });
    if (initial.view) initialParams.set("view", initial.view);
    if (initial.alertView !== "events")
      initialParams.set("alert", initial.alertView);
    window.history.replaceState(null, "", `/?${initialParams}`);
    restoring.current = true;
    restore.current(initial.tab, initial.alertView, initial.view);
    ready.current = true;
    const pop = () => {
      const target = read();
      if (
        target.tab === current.current.tab &&
        target.alertView === current.current.alertView &&
        target.view === current.current.view
      )
        return;
      if (guard.current && !guard.current()) {
        const params = new URLSearchParams({ module: current.current.tab });
        if (current.current.view) params.set("view", current.current.view);
        if (current.current.alertView !== "events")
          params.set("alert", current.current.alertView);
        window.history.pushState(null, "", `/?${params}`);
        return;
      }
      restoring.current = true;
      restore.current(target.tab, target.alertView, target.view);
    };
    window.addEventListener("popstate", pop);
    return () => {
      ready.current = false;
      window.removeEventListener("popstate", pop);
    };
  }, [identity, allowed, guard]);
  useEffect(() => {
    if (!ready.current || !allowed.current.includes(tab)) return;
    // The URL already selected a module; do not overwrite it with the initial render's default.
    if (restoring.current) {
      restoring.current = false;
      return;
    }
    const params = new URLSearchParams({ module: tab });
    if (view) params.set("view", view);
    if (tab === "告警中心" && alertView !== "events")
      params.set("alert", alertView);
    const next = `/?${params}`;
    if (window.location.pathname + window.location.search !== next)
      window.history.pushState(null, "", next);
  }, [identity, tab, alertView, view, allowed]);
}
