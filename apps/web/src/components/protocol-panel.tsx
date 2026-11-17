"use client";
import { useEffect, useState } from "react";
import { FileText, Download, ChevronRight } from "lucide-react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { RichContent } from "./rich-document";
import { useCapability } from "./permissions";
import { usePageRefresh } from "./page-refresh";

function clock(value: string) {
  return new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" });
}
export function ProtocolPanel({ onManage }: { onManage: () => void }) {
  const canManage = useCapability("content.manage");
  const [draft, setDraft] = useState(""),
    [q, setQ] = useState(""),
    [page, setPage] = useState(1);
  const [list, setList] = useState<ApiSchemas["ProtocolPage"] | null>(null),
    [loading, setLoading] = useState(false),
    [error, setError] = useState("");
  const [selected, setSelected] = useState<number | null>(null),
    [revision, setRevision] = useState(0);
  const [detail, setDetail] = useState<ApiSchemas["ProtocolDetail"] | null>(
      null,
    ),
    [detailLoading, setDetailLoading] = useState(false),
    [detailError, setDetailError] = useState("");
  const refresh = usePageRefresh();
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    typedApi
      .GET("/api/v1/protocols", {
        params: { query: { q, page } },
        cache: "no-store",
        signal: controller.signal,
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "协议列表读取失败"));
        setList(data);
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setError(error instanceof Error ? error.message : "协议列表读取失败");
          setList(null);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [q, page, refresh]);
  useEffect(() => {
    if (selected === null) return;
    const controller = new AbortController();
    setDetailLoading(true);
    setDetailError("");
    typedApi
      .GET("/api/v1/protocols/{article_id}", {
        params: { path: { article_id: selected } },
        cache: "no-store",
        signal: controller.signal,
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data)
          throw new Error(apiError(error, "协议已撤下或不可访问，请重新查询"));
        setDetail(data);
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setDetail(null);
          setDetailError(
            error instanceof Error ? error.message : "协议读取失败",
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setDetailLoading(false);
      });
    return () => controller.abort();
  }, [selected, revision, refresh]);
  function open(id: number) {
    setSelected(id);
    setRevision((r) => r + 1);
  }
  return (
    <section className="panel protocol-panel" aria-label="监管协议阅读区">
      <div className="protocol-intro">
        <div className="protocol-icon">
          <FileText size={24} />
        </div>
        <div>
          <span className="eyebrow">REGULATORY DOCUMENTS</span>
          <h2>监管协议与接入说明</h2>
          <p className="muted">
            查阅已发布的协议正文与原始附件，接入要求以客户确认的正式版本为准。
          </p>
        </div>
        {canManage && (
          <button type="button" className="outline" onClick={onManage}>
            管理协议内容
          </button>
        )}
      </div>
      {canManage && (
        <p className="protocol-help">
          在“公告与通知”中选择“监管协议”维护正文和附件，核对受众后发布。此处仅展示已发布且对当前身份可见的内容；不预置正式协议文本。
        </p>
      )}
      <form
        className="protocol-search"
        onSubmit={(event) => {
          event.preventDefault();
          setQ(draft);
          setPage(1);
        }}
      >
        <label>
          协议名称
          <input
            aria-label="监管协议检索"
            value={draft}
            maxLength={150}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="输入协议名称或版本关键词"
          />
        </label>
        <button type="submit" disabled={loading}>
          查询协议
        </button>
        <button
          type="button"
          className="outline"
          disabled={loading}
          onClick={() => {
            setDraft("");
            setQ("");
            setPage(1);
          }}
        >
          重置协议检索
        </button>
      </form>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div
        className="protocol-list"
        aria-label="已发布监管协议"
        aria-busy={loading}
      >
        {list?.items.map((item) => (
          <article className="protocol-card" key={item.id}>
            <div>
              <span className="protocol-card-icon">
                <FileText size={20} />
              </span>
              <div>
                <h3>{item.title}</h3>
                <p>
                  发布于 {clock(item.published_at)}（北京） ·{" "}
                  {item.attachment_count}份附件
                  {item.is_demo ? " · 演示数据" : ""}
                </p>
              </div>
            </div>
            <button
              className="outline"
              type="button"
              disabled={loading}
              onClick={() => open(item.id)}
            >
              查看协议
              <ChevronRight size={14} />
            </button>
          </article>
        ))}
        {!loading && list?.total === 0 && (
          <div className="protocol-empty">
            <FileText size={32} />
            <h3>暂无已发布的监管协议</h3>
            <p>请调整检索条件，或等待管理员发布正式内容与附件。</p>
          </div>
        )}
      </div>
      <div className="protocol-pagination">
        <span>
          协议第{page}页 · 共{list?.total ?? 0}条
        </span>
        <button
          type="button"
          className="outline"
          disabled={loading || page === 1}
          onClick={() => setPage((p) => p - 1)}
        >
          协议上一页
        </button>
        <button
          type="button"
          className="outline"
          disabled={loading || !list || page * list.page_size >= list.total}
          onClick={() => setPage((p) => p + 1)}
        >
          协议下一页
        </button>
      </div>
      {detailError && (
        <p className="error" role="alert">
          {detailError}
        </p>
      )}
      {detailLoading && <p role="status">正在核对协议可见范围…</p>}
      {detail && !detailLoading && (
        <section className="protocol-detail" aria-label="监管协议正文">
          <div className="protocol-detail-heading">
            <div>
              <span className="eyebrow">PROTOCOL #{detail.id}</span>
              <h3>{detail.title}</h3>
              <p className="muted">
                发布时间：{clock(detail.published_at)}（北京时间）
                {detail.is_demo ? " · 演示内容" : ""}
              </p>
            </div>
            <button
              className="outline"
              onClick={() => setRevision((r) => r + 1)}
              type="button"
            >
              重新载入协议
            </button>
          </div>
          <RichContent
            document={detail.rich_document}
            body={detail.body}
            articleId={detail.id}
            manage={false}
          />
          <div className="protocol-downloads">
            <h4>协议附件</h4>
            {detail.attachments.length === 0 ? (
              <p className="muted">本版本未附文件，请查阅上方正文。</p>
            ) : (
              detail.attachments.map((item) => (
                <a
                  key={item.id}
                  href={`/api/v1/content/${detail.id}/attachments/${item.id}`}
                >
                  <Download size={16} />
                  <span>
                    {item.filename}
                    <small>
                      {(item.size / 1024).toFixed(1)} KiB · 发布时冻结
                    </small>
                  </span>
                </a>
              ))
            )}
          </div>
        </section>
      )}
    </section>
  );
}
