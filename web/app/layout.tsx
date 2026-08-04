import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Research Center",
  description: "코스닥 미커버 종목 실적 리뷰 노트 · 사람이 검토 후 발간",
};

/**
 * 다크 모드를 **페인트 전에** 정한다.
 *
 * shadcn은 `.dark` 클래스로 토큰을 바꾸는데, 이 앱은 지금까지
 * `prefers-color-scheme`만 따랐다(index.html:18). 같은 동작을 유지하려면
 * 클래스를 누가 붙여야 하고, 그 일이 늦으면 밝은 화면이 한 번 번쩍인다.
 * 그래서 블로킹 인라인 스크립트로 처리한다.
 *
 * `localStorage.theme`을 먼저 보므로 나중에 토글을 붙일 자리가 이미 있다.
 */
const THEME_SCRIPT = `
try {
  var t = localStorage.getItem('theme');
  if (t !== 'light' && (t === 'dark' || matchMedia('(prefers-color-scheme: dark)').matches)) {
    document.documentElement.classList.add('dark');
  }
} catch (e) {}
`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko" className="h-full antialiased" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
