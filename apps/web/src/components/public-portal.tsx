"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import {
  ArrowRight,
  Building2,
  ChevronRight,
  Download,
  FileText,
  Search,
  ShieldCheck,
  Users,
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
function href(q: string, kind: string, page = 1, article?: number) {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (kind) params.set("kind", kind);
  if (page > 1) params.set("page", String(page));
  if (article) params.set("article", String(article));
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
  const page = Number(rawPage),
    id = rawId === null ? null : Number(rawId);
  const invalid =
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
        params: { query: { q, kind, page } },
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "资讯读取失败，请重试"));
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
  }, [q, kind, page, id, invalid, revision]);
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
    <main className="public-portal">
      <header className="portal-header">
        <div className="portal-container">
          <Link className="portal-brand" href="/portal">
            <span>
              <Zap size={25} />
            </span>
            <div>
              <strong>RONGHUB</strong>
              <small>{info?.site_name ?? "福州市充电监管平台"}</small>
            </div>
          </Link>
          <nav aria-label="公共门户导航">
            <Link href="/portal">资讯公告</Link>
            <Link href="/">站点公示 · 登录查看</Link>
            <Link className="portal-login-link" href="/portal/login">
              平台登录
              <ArrowRight size={15} />
            </Link>
          </nav>
        </div>
      </header>
      {info?.demo_mode && (
        <div className="portal-demo">
          本地演示预览 · 正式资讯以主管部门发布的内容为准
        </div>
      )}
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
          <section className="portal-hero">
            <div className="portal-container">
              <div className="portal-hero-copy">
                <span className="portal-kicker">
                  PUBLIC INFORMATION & SERVICES
                </span>
                <h1>充电资讯与公告</h1>
                <p>
                  监管公告、行业资讯与工作动态。
                  <br />
                  了解已公开的信息，进入对应工作台办理业务。
                </p>
                <div className="portal-hero-tags">
                  <span>
                    <ShieldCheck size={15} />
                    授权公开
                  </span>
                  <span>
                    <FileText size={15} />
                    原文查阅
                  </span>
                  <span>
                    <Users size={15} />
                    三端协同
                  </span>
                </div>
              </div>
              <div className="portal-entries" aria-label="三端工作台入口">
                <Link href="/">
                  <ShieldCheck size={22} />
                  <div>
                    <strong>监管端</strong>
                    <small>接入审核 · 安全监管</small>
                  </div>
                  <ChevronRight size={16} />
                </Link>
                <Link href="/">
                  <Users size={22} />
                  <div>
                    <strong>协会端</strong>
                    <small>标准评审 · 星级评价</small>
                  </div>
                  <ChevronRight size={16} />
                </Link>
                <Link href="/">
                  <Building2 size={22} />
                  <div>
                    <strong>运营商端</strong>
                    <small>场站申报 · 业务办理</small>
                  </div>
                  <ChevronRight size={16} />
                </Link>
                <p>统一账号认证后，按实际授权进入工作台。</p>
              </div>
            </div>
          </section>
          <section
            className="portal-container portal-news"
            aria-label="公开资讯列表"
          >
            <div className="portal-news-heading">
              <div>
                <span className="portal-kicker">LATEST UPDATES</span>
                <h2>资讯公告</h2>
              </div>
              <form
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
                    {new Date(item.published_at).toLocaleDateString("zh-CN", {
                      timeZone: "Asia/Shanghai",
                      day: "2-digit",
                    })}
                  </strong>
                  <span>
                    {new Date(item.published_at).toLocaleDateString("zh-CN", {
                      timeZone: "Asia/Shanghai",
                      year: "numeric",
                      month: "2-digit",
                    })}
                  </span>
                </div>
                <div>
                  <span className="portal-category">
                    {kinds[item.kind]}
                    {item.is_demo ? " · 演示内容" : ""}
                  </span>
                  <h3>
                    <Link href={href(q, kind, page, item.id)}>
                      {item.title}
                    </Link>
                  </h3>
                  <p>{item.excerpt}</p>
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
              <span className="portal-category">
                {kinds[detail.kind]}
                {detail.is_demo ? " · 演示内容" : ""}
              </span>
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
            <strong>RONGHUB</strong>
            <p>福州市充电监管平台</p>
            <small>公开资讯与业务协同</small>
          </div>
          <div>
            <h3>业务入口</h3>
            <Link href="/portal/login">统一身份认证</Link>
            <Link href="/register">企业注册与进度查询</Link>
            <Link href="/staff-register">工作人员账号申请</Link>
          </div>
          {(contacts.length > 0 || contactError) && (
            <div>
              <h3>公开联系方式</h3>
              {contactError && <p role="alert">{contactError}</p>}
              {contacts.map((item) => (
                <Link key={item.id} href={href("", "", 1, item.id)}>
                  {item.title}
                </Link>
              ))}
            </div>
          )}
          <p className="portal-footer-note">
            本页面仅展示获授权公开的内容。业务资料与站点公示请登录对应工作台查看。
          </p>
        </div>
      </footer>
    </main>
  );
}
