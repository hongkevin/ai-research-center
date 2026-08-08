"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Brand, BRAND_LINE } from "@/components/workbench/brand";
import { authEnabled, safeNextPath, signInWithGoogle } from "@/lib/supabase";

/**
 * 로그인 — **문 하나: Google.**
 *
 * 비밀번호도, 도착을 기다려야 하는 메일 링크도 두지 않는다. 제품의 유일한
 * 입구를 "메일이 와야 열리는 문"으로 만들면 그 문이 자주 안 열린다.
 */
function Form() {
  const params = useSearchParams();
  const next = safeNextPath(params.get("next"));
  const [pending, setPending] = useState(false);
  // **돌아오면서 실은 오류를 그대로 보여준다.** 전에는 `error=auth`로만
  // 덮여 있어서 무엇이 틀렸는지 화면에서 알 수가 없었다.
  const [error, setError] = useState<string | null>(
    params.get("error") ? decodeURIComponent(params.get("error")!) : null,
  );

  async function go() {
    setError(null);
    setPending(true);
    const message = await signInWithGoogle(next);
    // 성공하면 브라우저가 떠난다 — 여기 남는 건 실패뿐이라 pending을 되돌린다.
    if (message) {
      setError(message);
      setPending(false);
    }
  }

  return (
    <main className="flex min-h-dvh items-center justify-center px-5 py-8">
      <div className="w-full max-w-sm">
        <Brand className="mb-3 block text-[22px]" />
        <p className="mb-10 text-[13px] text-muted-foreground">{BRAND_LINE}</p>

        {authEnabled ? (
          <Button onClick={go} disabled={pending} className="w-full">
            {pending ? "이동 중…" : "Google로 계속하기"}
          </Button>
        ) : (
          <p className="rounded-lg border border-warn bg-warn/10 px-3 py-2 text-[12.5px]">
            로그인이 설정되지 않았습니다. <code>NEXT_PUBLIC_SUPABASE_URL</code>과{" "}
            <code>NEXT_PUBLIC_SUPABASE_ANON_KEY</code>를 넣고 다시 빌드하십시오.
          </p>
        )}

        {error && <p className="mt-3 text-[12.5px] text-bad">{error}</p>}
      </div>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <Form />
    </Suspense>
  );
}
