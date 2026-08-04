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
