"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import {
  ArrowRight,
  ChevronRight,
  Download,
  FileText,
  Search,
  Zap,
} from "lucide-react";
import { publicApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { RichContent } from "./rich-document";
const kinds = {
  announcement: "监管公告",
  news: "资讯动态",
  contact: "联系方式",
};
function clock(value: string) {
  return new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" });
}
function routeHref(
  q: string,
  kind: string,
  page = 1,
  article?: number,
  pageSize = 10,
) {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (kind) params.set("kind", kind);
  if (page > 1) params.set("page", String(page));
  if (article) params.set("article", String(article));
  if (pageSize !== 10) params.set("page_size", String(pageSize));
  return "/portal" + (params.size ? "?" + params : "");
}
export function PublicPortal() {
  const router = useRouter(),
    params = useSearchParams();
  const q = params.get("q") ?? "",
    rawKind = params.get("kind") ?? "",
    rawPage = params.get("page") ?? "1",
    rawId = params.get("article");
  const kind =
    rawKind === "news"
      ? "news"
      : rawKind === "announcement"
        ? "announcement"
        : "";
  const rawSize = params.get("page_size") ?? "10";
  const pageSize = rawSize === "20" ? 20 : rawSize === "50" ? 50 : 10;
  const href = (q: string, kind: string, page = 1, article?: number) =>
    routeHref(q, kind, page, article, pageSize);
  const page = Number(rawPage),
    id = rawId === null ? null : Number(rawId);
  const invalid =
    !["10", "20", "50"].includes(rawSize) ||
    !Number.isInteger(page) ||
    page < 1 ||
    page > 2147483647 ||
    !["", "announcement", "news"].includes(rawKind) ||
    (id !== null && (!Number.isInteger(id) || id < 1 || id > 2147483647));
  const [draft, setDraft] = useState(q),
    [revision, setRevision] = useState(0);
  const [info, setInfo] = useState<ApiSchemas["PortalInfo"] | null>(null),
    [infoError, setInfoError] = useState("");
  const [list, setList] = useState<ApiSchemas["PublicArticlePage"] | null>(
      null,
    ),
    [loading, setLoading] = useState(false),
    [error, setError] = useState("");
  const [detail, setDetail] = useState<
      ApiSchemas["PublicArticleDetail"] | null
    >(null),
    [detailLoading, setDetailLoading] = useState(false),
    [detailError, setDetailError] = useState("");
  const [contactError, setContactError] = useState("");
  const [contacts, setContacts] = useState<
    ApiSchemas["PublicArticleSummary"][]
  >([]);
  useEffect(() => setDraft(q), [q]);
  useEffect(() => {
    const controller = new AbortController();
    publicApi
      .GET("/api/v1/public/info", {
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "门户信息读取失败"));
        setInfo(data);
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setInfoError(
            error instanceof Error ? error.message : "门户信息读取失败",
          );
      });
    publicApi
      .GET("/api/v1/public/articles", {
        params: { query: { kind: "contact" } },
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "联系方式读取失败"));
        setContacts(data.items.slice(0, 3));
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setContactError(
            error instanceof Error ? error.message : "联系方式读取失败",
          );
      });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (invalid || id !== null) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setList(null);
    publicApi
      .GET("/api/v1/public/articles", {
        params: { query: { q, kind, page, page_size: pageSize } },
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "资讯读取失败，请重试"));
        const lastPage = Math.max(1, Math.ceil(data.total / pageSize));
        if (page > lastPage) {
          router.replace(href(q, kind, lastPage));
          return;
        }
        setList(data);
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setError(error instanceof Error ? error.message : "资讯读取失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [q, kind, page, pageSize, id, invalid, revision]);
  useEffect(() => {
    if (invalid || id === null) return;
    const controller = new AbortController();
    setDetail(null);
    setDetailLoading(true);
    setDetailError("");
    publicApi
      .GET("/api/v1/public/articles/{article_id}", {
        params: { path: { article_id: id } },
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "资讯未公开或已撤下"));
        setDetail(data);
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setDetailError(
            error instanceof Error ? error.message : "资讯读取失败",
          );
      })
      .finally(() => {
        if (!controller.signal.aborted) setDetailLoading(false);
      });
    return () => controller.abort();
  }, [id, invalid, revision]);
  return (
    <main className="public-portal corrected-public-portal">
      <header className="portal-header">
        <div className="portal-container">
          <Link className="portal-brand" href="/portal">
            <span>
              <Zap size={25} />
            </span>
            <div>
              <strong>{info?.site_name ?? "福州市充电监管平台"}</strong>
              <small>新能源汽车充电基础设施公共服务</small>
            </div>
          </Link>
          <nav aria-label="公共门户导航">
            <Link href="/portal">资讯公告</Link>
            <Link href="/portal/login?service=publications">
              站点公示 · 登录查看
            </Link>
            <Link className="portal-login-link" href="/portal/login">
              管理员登录
              <ArrowRight size={15} />
            </Link>
          </nav>
        </div>
      </header>
      {infoError && (
        <p role="alert" className="portal-container error">
          {infoError}
        </p>
      )}
      {invalid ? (
        <section className="portal-container portal-empty">
          <FileText size={36} />
          <h1>页面参数无效</h1>
          <p>请从资讯列表重新选择内容。</p>
          <Link href="/portal">返回资讯首页</Link>
        </section>
      ) : id === null ? (
        <>
          <section className="portal-hero" aria-label="资讯公告查询">
            <div className="portal-container">
              <div className="portal-hero-copy">
                <h1>充电资讯与公告</h1>
                <p>监管通知 · 政策法规 · 工作动态</p>
                <form
                  className="portal-search"
                  role="search"
                  aria-label="公开资讯查询"
                  onSubmit={(event) => {
                    event.preventDefault();
                    router.push(href(draft.trim(), kind));
                    setRevision((r) => r + 1);
                  }}
                >
                  <label>
                    <Search size={16} />
                    <input
                      aria-label="公开资讯关键词"
                      value={draft}
                      onChange={(event) => setDraft(event.target.value)}
                      maxLength={150}
                      placeholder="搜索资讯标题"
                    />
                  </label>
                  <button type="submit" disabled={loading}>
                    查询资讯
                  </button>
                </form>
              </div>
            </div>
          </section>
          <section
            className="portal-container portal-news"
            aria-label="公开资讯列表"
          >
            <div className="portal-news-heading">
              <h2>资讯公告</h2>
            </div>
            <nav className="portal-tabs" aria-label="资讯分类">
              <Link
                aria-current={kind === "" ? "page" : undefined}
                href={href(q, "")}
              >
                全部资讯
              </Link>
              <Link
                aria-current={kind === "announcement" ? "page" : undefined}
                href={href(q, "announcement")}
              >
                监管公告
              </Link>
              <Link
                aria-current={kind === "news" ? "page" : undefined}
                href={href(q, "news")}
              >
                资讯动态
              </Link>
            </nav>
            {error && (
              <div className="error" role="alert">
                {error}
                <button type="button" onClick={() => setRevision((r) => r + 1)}>
                  重新读取资讯
                </button>
              </div>
            )}
            {loading && (
              <div className="portal-loading" role="status">
                正在读取公开资讯…
              </div>
            )}
            {list?.items.map((item) => (
              <article className="portal-news-card" key={item.id}>
                <div className="portal-news-date">
                  <strong>
                    {new Date(item.published_at)
                      .toLocaleDateString("zh-CN", {
                        timeZone: "Asia/Shanghai",
                        day: "2-digit",
                      })
                      .replace("日", "")}
                  </strong>
                  <span>
                    {new Intl.DateTimeFormat("en-GB", {
                      timeZone: "Asia/Shanghai",
                      year: "numeric",
                      month: "2-digit",
                    })
                      .formatToParts(new Date(item.published_at))
                      .filter((p) => p.type === "year" || p.type === "month")
                      .reverse()
                      .map((p) => p.value)
                      .join(".")}
                  </span>
                </div>
                <div>
                  <span className="portal-category">{kinds[item.kind]}</span>
                  <h3>
                    <Link href={href(q, kind, page, item.id)}>
                      {item.title}
                    </Link>
                  </h3>
                  <p>{item.excerpt}</p>
                  <time
                    className="portal-published-at"
                    dateTime={item.published_at}
                  >
                    发布时间：{clock(item.published_at)}（北京时间）
                  </time>
                  <Link
                    className="portal-read"
                    href={href(q, kind, page, item.id)}
                  >
                    阅读全文
                    <ArrowRight size={14} />
                  </Link>
                </div>
              </article>
            ))}
            {!loading && list?.total === 0 && (
              <div className="portal-empty">
                <FileText size={38} />
                <h3>暂无公开资讯</h3>
                <p>可调整关键词，或等待管理员正式发布公开内容。</p>
                {q && <Link href="/portal">清除检索条件</Link>}
              </div>
            )}
            {list && (
              <div className="portal-paging">
                <span>
                  公开资讯第{page}页 · 共{list.total}条
                </span>
                <label className="portal-page-size">
                  每页条数
                  <select
                    aria-label="公开资讯每页条数"
                    value={pageSize}
                    onChange={(event) =>
                      router.push(
                        routeHref(
                          q,
                          kind,
                          1,
                          undefined,
                          Number(event.target.value),
                        ),
                      )
                    }
                  >
                    <option value="10">10条 / 页</option>
                    <option value="20">20条 / 页</option>
                    <option value="50">50条 / 页</option>
                  </select>
                </label>
                {page > 1 ? (
                  <Link href={href(q, kind, page - 1)}>上一页</Link>
                ) : (
                  <span aria-disabled="true">上一页</span>
                )}
                {page * list.page_size < list.total ? (
                  <Link href={href(q, kind, page + 1)}>下一页</Link>
                ) : (
                  <span aria-disabled="true">下一页</span>
                )}
              </div>
            )}
          </section>
        </>
      ) : (
        <section
          className="portal-container portal-reading"
          aria-label="公开资讯详情"
        >
          <Link className="portal-back" href={href(q, kind, page)}>
            资讯公告 / 返回列表
          </Link>
          {detailLoading && <p role="status">正在读取公开正文…</p>}
          {detailError && (
            <div role="alert" className="error">
              {detailError}
              <button onClick={() => setRevision((r) => r + 1)}>
                重新读取正文
              </button>
            </div>
          )}
          {detail && !detailLoading && (
            <article className="portal-article">
              <span className="portal-category">{kinds[detail.kind]}</span>
              <h1>{detail.title}</h1>
              <p className="portal-article-meta">
                发布时间：{clock(detail.published_at)}（北京时间）
              </p>
              <div className="portal-article-body">
                <RichContent
                  document={detail.rich_document}
                  body={detail.body}
                  articleId={detail.id}
                  manage={false}
                  imageSource={(attachmentId) =>
                    detail.attachments.some(
                      (asset) =>
                        asset.id === attachmentId &&
                        ["image/png", "image/jpeg"].includes(
                          asset.content_type,
                        ),
                    )
                      ? `/api/v1/public/articles/${detail.id}/attachments/${attachmentId}?inline=true`
                      : undefined
                  }
                />
              </div>
              {detail.attachments.length > 0 && (
                <section
                  className="portal-public-files"
                  aria-label="公开资讯附件"
                >
                  <h2>公开附件</h2>
                  {detail.attachments.map((asset) => (
                    <a
                      key={asset.id}
                      href={`/api/v1/public/articles/${detail.id}/attachments/${asset.id}`}
                    >
                      <Download size={17} />
                      <span>
                        {asset.filename}
                        <small>
                          {asset.size < 1024
                            ? `${asset.size} B`
                            : `${(asset.size / 1024).toFixed(1)} KiB`}
                        </small>
                      </span>
                    </a>
                  ))}
                </section>
              )}
              <Link className="portal-back" href={href(q, kind, page)}>
                返回资讯列表
              </Link>
            </article>
          )}
        </section>
      )}
      <footer className="portal-footer">
        <div className="portal-container">
          <div>
            <strong>{info?.site_name ?? "福州市充电监管平台"}</strong>
            <p>新能源汽车充电基础设施公共服务</p>
            <small>公开资讯与业务协同</small>
          </div>
          <div>
            <h3>业务入口</h3>
            <Link href="/portal/login">统一身份认证</Link>
            <Link href="/register">企业注册与进度查询</Link>
            <Link href="/staff-register">工作人员账号申请</Link>
          </div>
          <div className="portal-contact-information">
            <h3>公开联系方式</h3>
            {contactError && <p role="alert">{contactError}</p>}
            {!contactError && contacts.length === 0 && <p>暂未发布联系方式</p>}
            {contacts.map((item) => (
              <Link key={item.id} href={href("", "", 1, item.id)}>
                {item.title}
              </Link>
            ))}
          </div>
          <p className="portal-footer-note">
            本页面仅展示获授权公开的内容。业务资料与站点公示请登录对应工作台查看。
          </p>
        </div>
      </footer>
    </main>
  );
}
