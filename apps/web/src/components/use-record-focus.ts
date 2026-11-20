"use client";
import { useEffect, useRef } from "react";

/** Keep the list's state mounted while moving focus to the selected record. */
export function useRecordFocus(identity: number | undefined) {
  const root = useRef<HTMLElement>(null);
  const previous = useRef(identity);
  useEffect(() => {
    if (previous.current === identity) return;
    previous.current = identity;
    const heading = root.current?.querySelector<HTMLElement>(
      identity == null
        ? ".record-pane .panel-title h2"
        : ".record-detail .panel-title h2",
    );
    if (heading) {
      heading.tabIndex = -1;
      heading.focus({ preventScroll: true });
    }
    root.current?.scrollIntoView({ block: "start" });
  }, [identity]);
  return root;
}
