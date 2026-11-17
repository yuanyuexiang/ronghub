import { Suspense } from "react";
import { PublicPortal } from "../../components/public-portal";
export const metadata = {
  title: "资讯公告 | RongHub",
  description: "福州市充电监管平台公开资讯与业务入口",
};
export default function PortalPage() {
  return (
    <Suspense
      fallback={<main className="portal-loading">正在加载公开资讯门户…</main>}
    >
      <PublicPortal />
    </Suspense>
  );
}
