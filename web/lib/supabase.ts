"use client";

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

/**
 * Supabase 클라이언트 — **브라우저 전용**.
 *
 * 이 앱은 정적 익스포트라 Next 서버가 없다(D37). 그래서 `@supabase/ssr`의
 * 서버 클라이언트·미들웨어 방식을 쓸 수 없고, 브라우저가 세션을 들고 있다가
 * API 호출마다 액세스 토큰을 붙인다. 검증은 FastAPI가 한다(`web/auth.py`).
 *
 * **문 하나: Google.** 비밀번호도, 도착을 기다려야 하는 메일 링크도 두지
 * 않는다 — 1인 도구에 그 이상은 관리 비용만 는다.
 */

const URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
const ANON = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";

/** 키가 없으면 로그인 자체를 끈다 — 로컬 개발에서 Basic/무인증으로 돌 수 있어야 한다. */
export const authEnabled = Boolean(URL && ANON);

let client: SupabaseClient | null = null;

export function supabase(): SupabaseClient {
  if (!client) {
    client = createClient(URL, ANON, {
      auth: {
        flowType: "pkce",
        // **자동 교환을 끈다.** 기본값 `true`면 클라이언트를 만드는 순간
        // URL의 `?code=`를 스스로 교환하기 시작하고, 콜백 화면이 곧이어
        // 수동으로 또 교환한다. 둘이 경합해서 진 쪽이 「code verifier가
        // 없다」로 실패하고 화면에는 `error=auth`만 남는다.
        //
        // 하나만 하게 만든다 — 교환하는 자리는 `/auth/callback`뿐이다.
        detectSessionInUrl: false,
      },
    });
  }
  return client;
}

/** API 호출에 붙일 액세스 토큰. 없으면 빈 문자열. */
export async function accessToken(): Promise<string> {
  if (!authEnabled) return "";
  const { data } = await supabase().auth.getSession();
  return data.session?.access_token ?? "";
}

/**
 * 시작 전에 **지난 시도의 찌꺼기를 치운다.**
 *
 * PKCE는 시작할 때 검증자(code verifier)를 localStorage에 넣고 콜백에서 꺼내
 * 쓴다. 중간에 실패하면 그 값이 남는데, 다음 시도가 그 위에서 시작하면
 * `invalid flow state, no valid flow state found`가 난다 — 서버가 보기에
 * 짝이 안 맞는 코드다.
 *
 * supabase-js가 이걸 알아서 치우지 않으므로 **누를 때마다 새로 시작한다.**
 * 로그인 버튼은 원래 「처음부터 다시」라는 뜻이다.
 */
function clearStaleFlow(): void {
  try {
    for (const key of Object.keys(localStorage)) {
      if (key.startsWith("sb-") && key.includes("code-verifier")) {
        localStorage.removeItem(key);
      }
    }
  } catch {
    // 사생활 보호 모드 등으로 localStorage가 막혀 있으면 그냥 넘어간다
  }
}

export async function signInWithGoogle(next = "/"): Promise<string | null> {
  clearStaleFlow();
  const { error } = await supabase().auth.signInWithOAuth({
    provider: "google",
    options: {
      redirectTo: `${location.origin}/auth/callback/?next=${encodeURIComponent(next)}`,
    },
  });
  // 성공하면 브라우저가 떠나므로 여기 남는 건 실패 경로뿐이다.
  // 제공자의 원문 오류를 그대로 보여주지 않는다 — 사용자에게는 아무 말도 안
  // 되면서 우리 배선만 드러낸다.
  return error ? "Google에 연결하지 못했습니다. 잠시 후 다시 시도해 주십시오." : null;
}

/**
 * 지금 누구로 보고 있나. 로그인이 꺼져 있으면 빈 문자열.
 *
 * **화면에 이게 없으면 안 됩니다.** 개인화된 도구에서 「이 커버리지가 누구
 * 것인가」를 화면이 말하지 않으면, 남의 계정으로 로그인한 것을 모르고
 * 씁니다 — 그리고 커버 종목을 고칩니다.
 */
export async function currentEmail(): Promise<string> {
  if (!authEnabled) return "";
  const { data } = await supabase().auth.getSession();
  return data.session?.user?.email ?? "";
}

export async function signOut(): Promise<void> {
  if (authEnabled) await supabase().auth.signOut();
}

/** 열린 리다이렉트를 막는다 — 같은 출처의 경로만 받는다. */
export function safeNextPath(raw: string | null): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//")) return "/";
  return raw;
}
