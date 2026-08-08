import { readFileSync } from "node:fs";
import { join } from "node:path";

import type { NextConfig } from "next";

/**
 * 정적 익스포트. 빌드 산출물(`out/`)을 FastAPI가 StaticFiles로 서빙한다.
 *
 * 왜 서버 렌더가 아닌가: `.arc-store`(추정 이력)가 영속 볼륨에 있어야 하고
 * corpCode 캐시가 프로세스 메모리에 있어 **컨테이너 하나**를 유지해야 한다
 * (Dockerfile 주석 참조). Node 런타임을 따로 띄우면 서비스가 둘이 된다.
 *
 * 이 앱은 화면 하나짜리 작업대라 SSR로 얻을 것이 거의 없다 — 데이터는
 * 전부 인증 뒤의 `/api/*`에서 온다.
 */

/**
 * 저장소 루트 `.env`에서 **`NEXT_PUBLIC_*`만** 가져온다.
 *
 * `NEXT_PUBLIC_*`는 **빌드 시점에 번들에 박힌다.** 그런데 Next는 자기 디렉터리
 * (`web/`)의 `.env`만 읽고 저장소 루트는 안 본다 — 키는 루트에 있다. 그래서
 * `.env`에 Supabase 키를 넣고 빌드해도 **화면은 로그인이 꺼진 줄 안다.**
 * 그러면 토큰을 안 붙이고, 서버는 401을 내고, 앱이 통째로 안 열린다.
 *
 * **`NEXT_PUBLIC_` 접두사만 넘긴다.** Next가 어차피 그것만 인라인하지만,
 * 여기서 걸러 두면 `ANTHROPIC_API_KEY` 같은 것이 애초에 빌드 프로세스의
 * 환경에 들어가지 않는다 — 실수로 참조해도 새어 나갈 값이 없다.
 *
 * 이미 환경에 있는 값은 **안 덮는다.** CI·배포에서는 대시보드가 정본이다.
 */
function loadPublicEnv(): void {
  let raw: string;
  try {
    raw = readFileSync(join(process.cwd(), "..", ".env"), "utf-8");
  } catch {
    return; // 루트 `.env`가 없는 것은 정상이다 (CI·배포)
  }
  for (const line of raw.split("\n")) {
    const text = line.trim();
    if (!text || text.startsWith("#")) continue;
    const eq = text.indexOf("=");
    if (eq < 1) continue;
    const key = text.slice(0, eq).trim();
    if (!key.startsWith("NEXT_PUBLIC_")) continue;
    if (process.env[key]) continue;
    // 값에 붙은 따옴표만 벗긴다. 그 이상 해석하지 않는다
    process.env[key] = text
      .slice(eq + 1)
      .trim()
      .replace(/^(['"])(.*)\1$/, "$2");
  }
}

loadPublicEnv();

const nextConfig: NextConfig = {
  output: "export",

  // `/note` → `/note/index.html`. StaticFiles(html=True)가 디렉터리
  // 인덱스를 찾으므로 이 형태여야 새로고침이 404가 되지 않는다.
  trailingSlash: true,

  images: {
    // 정적 익스포트에는 이미지 최적화 서버가 없다
    unoptimized: true,
  },
};

export default nextConfig;
