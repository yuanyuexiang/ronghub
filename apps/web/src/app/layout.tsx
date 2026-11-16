import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = { title: "RongHub · 福州市充电监管平台" };
export default function Layout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
