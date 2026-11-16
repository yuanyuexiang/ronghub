"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

const Revision = createContext(0);
const RequestRefresh = createContext<() => void>(() => {});

export function PageRefreshProvider({ children }: { children: ReactNode }) {
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  return (
    <Revision.Provider value={revision}>
      <RequestRefresh.Provider value={refresh}>
        {children}
      </RequestRefresh.Provider>
    </Revision.Provider>
  );
}

/** Read-only refresh signal: subscribers retain their own applied queries and form state. */
export function usePageRefresh() {
  return useContext(Revision);
}

export function PageRefreshButton({
  scope,
  onRefresh,
}: {
  scope: string;
  onRefresh: () => Promise<void>;
}) {
  const request = useContext(RequestRefresh);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [failed, setFailed] = useState(false);
  const lock = useRef(false),
    currentScope = useRef(scope),
    mounted = useRef(true);
  currentScope.current = scope;
  useEffect(() => {
    setNotice("");
    setFailed(false);
  }, [scope]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  async function refresh() {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setFailed(false);
    setNotice("");
    const startedScope = scope;
    // Only the mounted page observes this revision; no component key/remount or form reset.
    request();
    try {
      await onRefresh();
      if (mounted.current && startedScope === currentScope.current)
        setNotice(
          "已发起列表与概览刷新，请查看各区块加载或错误状态。详情与未保存内容保留，需更新详情时请重新打开。",
        );
    } catch (error) {
      if (mounted.current && startedScope === currentScope.current) {
        setFailed(true);
        setNotice(
          error instanceof Error
            ? error.message
            : "概览刷新失败，请重试；列表结果请查看各区块状态。",
        );
      }
    } finally {
      lock.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  return (
    <div className="page-refresh-controls">
      <button
        type="button"
        className="outline"
        disabled={busy}
        onClick={() => void refresh()}
        title="刷新当前列表与概览，保留筛选和未保存输入；详情请重新打开。"
      >
        {busy ? "正在刷新概览…" : "刷新数据"}
      </button>
      {notice && (
        <p
          className={failed ? "error" : "muted"}
          role={failed ? "alert" : "status"}
        >
          {notice}
        </p>
      )}
    </div>
  );
}
