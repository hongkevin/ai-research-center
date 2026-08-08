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
  const params = useSearchParams();
  const next = safeNextPath(params.get("next"));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;

    // **제공자가 먼저 거절한 경우.** 이때는 교환할 코드가 아예 없다 —
    // 그대로 교환을 시도하면 「코드가 없다」는 엉뚱한 오류로 덮인다.
    const denied = params.get("error_description") || params.get("error");
    if (denied) {
      location.replace(`/login/?error=${encodeURIComponent(denied)}`);
      return;
    }

    supabase()
      .auth.exchangeCodeForSession(window.location.href)
      .then(({ error: e }) => {
        if (!alive) return;
        // **원인을 버리지 않는다.** 전에는 전부 `error=auth`로 덮어써서
        // 무엇이 틀렸는지 알 방법이 없었다. 여기 오는 것은 설정 오류
        // (리디렉트 URL 불일치·PKCE 경합)이지 비밀이 아니다.
        if (e) location.replace(`/login/?error=${encodeURIComponent(e.message)}`);
        else location.replace(next);
      })
      .catch((e: unknown) =>
        alive
          ? setError(e instanceof Error ? e.message : "로그인을 마치지 못했습니다.")
          : undefined,
      );
    return () => {
      alive = false;
    };
  }, [next, params]);

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
