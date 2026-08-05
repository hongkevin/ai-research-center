"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";

import { safeNextPath, supabase } from "@/lib/supabase";

/**
 * OAuth 콜백 — **클라이언트에서 처리한다.**
 *
 * 보통은 서버 라우트 핸들러가 코드를 세션으로 바꾸지만, 이 앱은 정적
 * 익스포트라 Next 서버가 없다(D37). 브라우저가 직접 교환하고 세션을 들고
 * 있다가 API 호출마다 토큰을 붙인다.
 */
function Exchange() {
  const next = safeNextPath(useSearchParams().get("next"));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    supabase()
      .auth.exchangeCodeForSession(window.location.href)
      .then(({ error: e }) => {
        if (!alive) return;
        // 실패해도 원문 오류를 띄우지 않는다 — 로그인 화면으로 돌려보낸다.
        if (e) location.replace("/login/?error=auth");
        else location.replace(next);
      })
      .catch(() => alive && setError("로그인을 마치지 못했습니다."));
    return () => {
      alive = false;
    };
  }, [next]);

  return (
    <main className="flex min-h-dvh items-center justify-center px-5">
      <p className="text-[13px] text-muted-foreground">
        {error ?? "로그인을 마치는 중…"}
      </p>
    </main>
  );
}

export default function CallbackPage() {
  return (
    <Suspense>
      <Exchange />
    </Suspense>
  );
}
