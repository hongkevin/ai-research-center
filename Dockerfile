# AI Research Center — 배포 이미지
#
# 서버리스가 아니라 **상주 서버**로 띄운다. 이유는 두 가지다:
#   1. `.arc-store`(추정 이력)가 영속 디스크에 있어야 revision 추적이 성립한다.
#      컨테이너가 재시작해도 남으려면 볼륨 마운트가 필요하다 (ARC_STORE_DIR).
#   2. corpCode.xml(상장사 3,981개, 1.5MB)을 프로세스 수명 동안 캐시한다.
#      콜드스타트마다 다시 받으면 검색이 매번 1초 이상 느려진다.

# ── 화면 빌드 ────────────────────────────────────────────────────────
# Next.js를 **정적으로 익스포트**한다. Node는 이 스테이지에만 있고 최종
# 이미지에는 남지 않는다 — 런타임은 파이썬 하나다.
FROM node:22-slim AS ui
WORKDIR /ui

# lockfile을 먼저 넣어 레이어 캐시를 살린다 — 화면 코드만 바뀌면 재설치하지 않는다
COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
RUN npm run build

# ── 런타임 ───────────────────────────────────────────────────────────
FROM python:3.12-slim

# 로그가 버퍼에 갇히면 배포 플랫폼에서 아무것도 안 보인다
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 의존성을 먼저 넣어 레이어 캐시를 살린다 — 코드만 바뀌면 재설치하지 않는다
# LICENSE도 필요하다 — pyproject의 license = { file = "LICENSE" } 때문에
# 없으면 메타데이터 생성이 실패한다 (CI가 잡았다)
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN pip install --no-cache-dir ".[web]"

COPY templates/ ./templates/

# 리포트 템플릿은 **wheel에 들어가지 않는다**(src/arc 밖에 있다). 설치된
# 패키지는 저장소 루트를 못 찾으므로 경로를 명시한다 (pipeline/_template_dir).
ENV ARC_TEMPLATE_DIR=/app/templates

# 화면. 위 ui 스테이지의 정적 익스포트를 가져온다 (web/app.py의 STATIC_DIR).
COPY --from=ui /ui/out ./static/
ENV ARC_STATIC_DIR=/app/static

# 추정 이력의 기본 위치. Railway 등에서 이 경로에 볼륨을 붙인다.
ENV ARC_STORE_DIR=/data/arc-store
RUN mkdir -p /data/arc-store

# 플랫폼이 PORT를 주입한다. 없으면 8000.
ENV PORT=8000
EXPOSE 8000

# 헬스체크는 인증 없이 열려 있다 (web/auth.py PUBLIC_PATHS)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",8000)}/api/health', timeout=4)"

# 워커 1개인 이유: corpCode 캐시와 LLM 예산이 **프로세스 메모리**에 있다.
# 워커를 늘리면 캐시가 중복되고 예산이 워커 수만큼 곱해진다.
# 동시 접속이 늘면 그때 캐시·예산을 외부 저장소로 옮기고 워커를 늘린다.
CMD ["sh", "-c", "uvicorn arc.web.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
