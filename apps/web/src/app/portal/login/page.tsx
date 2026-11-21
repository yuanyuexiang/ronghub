import Link from "next/link";
import {
  ArrowLeft,
  ArrowRight,
  Building2,
  ShieldCheck,
  Users,
  Zap,
} from "lucide-react";
export const metadata = { title: "统一身份入口 | 福州市充电监管平台" };
export default async function PortalLogin({
  searchParams,
}: {
  searchParams: Promise<{ service?: string }>;
}) {
  const service = (await searchParams).service === "publications";
  return (
    <main className="public-portal portal-entry-page corrected-entry-page">
      <header className="portal-header">
        <div className="portal-container">
          <Link href="/portal" className="portal-brand">
            <span>
              <Zap size={25} />
            </span>
            <div>
              <strong>福州市充电监管平台</strong>
              <small>统一身份认证</small>
            </div>
          </Link>
          <Link href="/portal">
            <ArrowLeft size={16} />
            返回资讯门户
          </Link>
        </div>
      </header>
      <section className="portal-container portal-login-choices">
        <span className="portal-kicker">充电运营监管平台</span>
        <h1>选择您的业务入口</h1>
        {service && (
          <p className="portal-service-note" role="status">
            站点公示需登录后查看，请选择您的业务入口。
          </p>
        )}
        <p>使用已有账号完成统一身份认证，按账号实际授权进入对应工作台。</p>
        <div>
          <Link href="/?entry=regulator">
            <ShieldCheck size={32} />
            <h2>监管端</h2>
            <p>场站审核、安全监管、补贴与统计</p>
            <span>
              进入监管端登录
              <ArrowRight size={17} />
            </span>
          </Link>
          <Link href="/?entry=operator">
            <Building2 size={32} />
            <h2>运营商端</h2>
            <p>场站资料、业务申报与整改反馈</p>
            <span>
              进入运营商端登录
              <ArrowRight size={17} />
            </span>
          </Link>
          <Link href="/?entry=association">
            <Users size={32} />
            <h2>协会端</h2>
            <p>评价标准、申报评审与结果管理</p>
            <span>
              进入协会端登录
              <ArrowRight size={17} />
            </span>
          </Link>
        </div>
        <p>
          <Link href="/register">申请企业账号</Link> ·{" "}
          <Link href="/staff-register">申请工作人员账号</Link>
        </p>
      </section>
    </main>
  );
}
