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
  if (!client) client = createClient(URL, ANON, { auth: { flowType: "pkce" } });
  return client;
}

/** API 호출에 붙일 액세스 토큰. 없으면 빈 문자열. */
export async function accessToken(): Promise<string> {
  if (!authEnabled) return "";
  const { data } = await supabase().auth.getSession();
  return data.session?.access_token ?? "";
}

export async function signInWithGoogle(next = "/"): Promise<string | null> {
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
