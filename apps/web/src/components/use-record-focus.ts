"use client";
import { useEffect, useRef } from "react";

/** Keep the list's state mounted while moving focus to the selected record. */
export function useRecordFocus(identity: number | undefined, pane?: string) {
  const root = useRef<HTMLElement>(null);
  const previous = useRef({ identity, pane });
  useEffect(() => {
    if (
      previous.current.identity === identity &&
      previous.current.pane === pane
    )
      return;
    previous.current = { identity, pane };
    const heading = Array.from(
      root.current?.querySelectorAll<HTMLElement>(
        identity == null ? ".record-pane h2" : ".record-detail .panel-title h2",
      ) ?? [],
    ).find((element) => element.getClientRects().length > 0);
    if (heading) {
      heading.tabIndex = -1;
      heading.focus({ preventScroll: true });
    }
    root.current?.scrollIntoView({ block: "start" });
  }, [identity, pane]);
  return root;
}
