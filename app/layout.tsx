import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Research Graph · AI/CS 论文搜索与前沿方向",
  description: "搜索 AI 与计算机科学论文，查看相关论文、引用关系和每月更新的研究前沿方向。",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
