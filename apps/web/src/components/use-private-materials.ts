"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePageRefresh } from "./page-refresh";

/** Readers cancel replaced requests; refreshing metadata never resets the owning form. */
export function usePrivateMaterials<T>(
  read: (signal: AbortSignal) => Promise<T[]>,
) {
  const [items, setItems] = useState<T[]>([]),
    [loading, setLoading] = useState(true),
    [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null),
    mounted = useRef(false);
  const revision = usePageRefresh();
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      controller.current?.abort();
    };
  }, []);
  const reload = useCallback(async () => {
    if (!mounted.current) return;
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setLoading(true);
    try {
      const records = await read(current.signal);
      if (!current.signal.aborted) setItems(records);
    } catch (error) {
      if (!current.signal.aborted) throw error;
    } finally {
      if (!current.signal.aborted) setLoading(false);
    }
  }, [read]);
  useEffect(() => {
    setError("");
    reload().catch((error) => {
      if (mounted.current)
        setError(error instanceof Error ? error.message : "材料读取失败");
    });
    return () => controller.current?.abort();
  }, [reload, revision]);
  return { items, loading, error, setError, reload };
}
