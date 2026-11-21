import type { Metadata } from "next";
import "./globals.css";
import "./civic-theme.css";
import "./refined-workspace.css";
export const metadata: Metadata = { title: "RongHub · 福州市充电监管平台" };
export default function Layout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body className="refined-workspace">{children}</body>
    </html>
  );
}
