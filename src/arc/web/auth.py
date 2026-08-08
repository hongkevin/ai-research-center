"""접근 제어 + 비용 상한.

왜 필요한가
-----------
이 앱은 서버에 **OpenAI 키**를 들고 있다. 인증 없이 공개 URL에 올리면 주소를
아는 누구나 그 키로 생성을 돌릴 수 있고, 요금은 키 주인이 낸다. 동료 몇 명이
테스트하는 용도라도 인증 없이 올려서는 안 된다.

설계
----
* **공유 비밀번호(HTTP Basic).** 사용자 목록·세션·DB가 필요 없다. 동료 테스트
  규모에는 이게 적정하다. 누가 눌렀는지는 알 수 없다 — 그게 필요해지면
  초대 코드나 SSO로 올린다.
* `ARC_PASSWORD`가 **비어 있으면 인증을 걸지 않는다.** 로컬 개발에서 매번
  비밀번호를 치게 만들면 개발이 느려진다. 대신 그 상태로 뜨면 로그에 경고한다.
* `/api/health`는 열어둔다 — 배포 플랫폼의 헬스체크가 인증을 통과할 수 없다.

비용 상한
---------
LLM 생성은 건당 몇 밀리센트지만 **루프를 돌면 요금은 선형으로 는다.** 인증이
있어도 실수로 반복 요청이 나갈 수 있으므로 프로세스 단위 상한을 둔다.
상한에 닿으면 LLM만 끄고 결정론 생성은 계속된다 — 화면이 죽는 것보다 낫다.
"""

from __future__ import annotations

import base64
import hmac
import logging
import os
import threading

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from arc.web.identity import reset_current_user, set_current_user

log = logging.getLogger("arc.web")

# 인증 없이 열어둘 경로 — 플랫폼 헬스체크는 인증을 통과할 수 없다
PUBLIC_PATHS = frozenset({"/api/health"})

_REALM = "AI Research Center"

# Supabase는 로그인한 사용자 토큰에 이 audience를 넣는다
_SUPABASE_AUD = "authenticated"


def _jwks_url(project_url: str) -> str:
    """프로젝트 URL → JWKS 주소. 비어 있거나 형식이 아니면 빈 문자열.

    `https://abcd.supabase.co` → `https://abcd.supabase.co/auth/v1/.well-known/jwks.json`
    """
    url = (project_url or "").strip().rstrip("/")
    if not url.startswith("https://"):
        return ""
    return f"{url}/auth/v1/.well-known/jwks.json"


def _is_progress_stream(path: str) -> bool:
    """`/api/jobs/{id}/events` 인가. **`/api/events`는 아니다.**"""
    return path.startswith("/api/jobs/") and path.endswith("/events")


def _is_public(path: str, *, jwt_mode: bool) -> bool:
    """이 경로를 인증 없이 열어줄 것인가.

    **JWT 모드에서는 화면 껍데기가 열려 있어야 한다.** 로그인 페이지 자체가
    그 껍데기 안에 있어서, 정적 파일까지 막으면 로그인할 방법이 없다. 대신
    데이터는 전부 `/api/*` 뒤에 있으므로 그쪽만 잠근다.

    Basic 모드는 지금 동작을 그대로 둔다 — 화면까지 막던 것을 갑자기 열면
    배포된 주소의 노출 범위가 조용히 넓어진다.
    """
    if path in PUBLIC_PATHS:
        return True
    return jwt_mode and not path.startswith("/api/")


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """접근 제어. **설정된 것에 따라 방식을 고른다.**

    1. `SUPABASE_JWT_SECRET`이 있으면 → Supabase 액세스 토큰(Bearer) 검증
    2. 없고 `ARC_PASSWORD`가 있으면 → 공유 비밀번호(HTTP Basic)
    3. 둘 다 없으면 → 통과 (로컬 개발). 뜰 때 경고한다.

    **왜 Basic을 아직 안 지우는가.** 지금 지우면 Supabase 키가 안 꽂힌 동안
    서버가 **OpenAI 키를 들고 무방비로 열린다** — 이 파일이 존재하는 이유가
    그것이다. Supabase가 배포에서 확인된 뒤에 지운다.

    Next 서버가 없어(정적 익스포트, D37) 브라우저가 토큰을 직접 들고 오므로
    **API가 스스로 서명을 확인해야 한다.** Supabase 서버 클라이언트가 쿠키로
    대신해 주는 구조가 여기서는 성립하지 않는다.
    """

    def __init__(self, app, password: str | None = None, username: str = "arc") -> None:
        super().__init__(app)
        self.password = password if password is not None else os.environ.get("ARC_PASSWORD", "")
        # **빈 값은 「설정 안 함」이다.** `os.environ.get(k, 기본값)`은 빈
        # 문자열이 들어 있으면 그걸 그대로 준다 — `ARC_USERNAME=`만 적어 두면
        # 사용자명이 빈 문자열이 되어 아무도 로그인 못 한다.
        self.username = os.environ.get("ARC_USERNAME", "") or username
        self.jwt_secret = os.environ.get("SUPABASE_JWT_SECRET", "")
        # **비대칭 서명이 지금의 기본이다.** 공유 시크릿(HS256)은 하위 호환용이고
        # 2026년 말 폐기 예정이라, 프로젝트 URL만 있으면 JWKS로 검증한다.
        self.jwks_url = _jwks_url(
            os.environ.get("SUPABASE_URL", "") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL", "")
        )
        self._jwks = None
        if self.jwks_url:
            log.info("Supabase 토큰 검증(JWKS)으로 실행됩니다: %s", self.jwks_url)
        elif self.jwt_secret:
            log.info("Supabase 토큰 검증(공유 시크릿)으로 실행됩니다 — 레거시 방식입니다.")
        elif not self.password:
            log.warning(
                "SUPABASE_JWT_SECRET·ARC_PASSWORD가 모두 없어 인증 없이 실행됩니다. "
                "공개 주소에 올릴 때는 반드시 설정하십시오 — 서버의 LLM 키가 무방비가 됩니다."
            )

    def _claims(self, header: str | None) -> dict | None:
        """`Authorization: Bearer <jwt>` → 검증된 클레임. 아니면 None.

        **토큰이 자기 알고리즘을 말한다.** Supabase는 지금 비대칭 서명키
        (ES256/RS256)가 기본이고 공유 시크릿(HS256)은 하위 호환이다. 마이그레이션
        중에는 둘 다 발급될 수 있으므로 헤더의 `alg`를 보고 갈라 준다.

        **실패하면 거부한다, 통과시키지 않는다.** JWKS를 못 받아도 마찬가지다 —
        열려 있는 편이 낫다는 판단은 여기서 절대 하지 않는다. 이 서버는 LLM
        키를 들고 있다.
        """
        if not header or not header.lower().startswith("bearer "):
            return None
        token = header.split(" ", 1)[1].strip()
        try:
            import jwt

            alg = jwt.get_unverified_header(token).get("alg", "")
            if alg == "HS256":
                if not self.jwt_secret:
                    log.debug("토큰이 HS256인데 공유 시크릿이 없습니다")
                    return None
                key, algorithms = self.jwt_secret, ["HS256"]
            else:
                signing = self._signing_key(token)
                if signing is None:
                    return None
                key, algorithms = signing, ["ES256", "RS256"]

            return jwt.decode(token, key, algorithms=algorithms, audience=_SUPABASE_AUD)
        except Exception as exc:  # noqa: BLE001 — 만료·서명불일치·형식오류 전부 거부다
            log.debug("토큰 거부: %s", exc)
            return None

    def _signing_key(self, token: str):
        """JWKS에서 이 토큰의 공개키. **캐시는 PyJWT가 한다.**

        요청마다 Supabase에 물으면 지연이 붙고 그쪽이 죽으면 우리도 죽는다.
        `PyJWKClient`가 `kid`별로 캐시하고 만료되면 다시 받는다.
        """
        if not self.jwks_url:
            log.debug("비대칭 토큰인데 프로젝트 URL이 없어 JWKS를 못 찾습니다")
            return None
        try:
            from jwt import PyJWKClient

            if self._jwks is None:
                self._jwks = PyJWKClient(self.jwks_url, cache_keys=True)
            return self._jwks.get_signing_key_from_jwt(token).key
        except Exception as exc:  # noqa: BLE001 — 네트워크·형식 전부 거부다
            log.warning("JWKS를 못 받아 토큰을 거부합니다: %s", exc)
            return None

    def _ok(self, header: str | None) -> bool:
        if not header or not header.lower().startswith("basic "):
            return False
        try:
            raw = base64.b64decode(header.split(" ", 1)[1]).decode("utf-8")
            user, _, pw = raw.partition(":")
        except Exception:  # noqa: BLE001 — 형식이 틀리면 그냥 거부다
            return False
        # 타이밍 공격 방지. 짧은 비밀번호라도 비교로 정보를 흘리지 않는다.
        return hmac.compare_digest(user, self.username) and hmac.compare_digest(pw, self.password)

    async def dispatch(self, request: Request, call_next) -> Response:
        # JWKS든 공유 시크릿이든 **토큰 검증 모드**다
        jwt_mode = bool(self.jwks_url or self.jwt_secret)
        if _is_public(request.url.path, jwt_mode=jwt_mode):
            return await call_next(request)

        if jwt_mode:
            header = request.headers.get("authorization")
            # **`EventSource`는 헤더를 붙일 수 없다.** 진행 스트림만 쿼리
            # 토큰을 받는다. 쿼리 토큰은 서버 로그에 남을 수 있어 일반적으로
            # 피해야 하지만 다른 방법이 없고, 이 경로가 흘리는 것은 단계
            # 이름뿐이다.
            # **진행 스트림에만 준다.** `/api/events`(사건 로그)가 여기 걸리면
            # 로그 조회가 쿼리 토큰을 받게 되고, 토큰이 서버 로그에 남는다.
            if not header and _is_progress_stream(request.url.path):
                q = request.query_params.get("access_token", "")
                header = f"Bearer {q}" if q else None
            claims = self._claims(header)
            if claims is None:
                # Basic과 달리 `WWW-Authenticate`를 주지 않는다 — 브라우저
                # 기본 로그인 창이 뜨면 우리 로그인 화면으로 갈 수 없다.
                return PlainTextResponse("로그인이 필요합니다.", status_code=401)
            request.state.user_id = claims.get("sub", "")
            request.state.user_email = claims.get("email", "")
            # **여기서부터 저장소가 사람별로 갈린다.** `call_next` 앞에서
            # 세팅해야 하위 태스크가 이 값을 물려받는다.
            token = set_current_user(request.state.user_id)
            try:
                return await call_next(request)
            finally:
                reset_current_user(token)

        if not self.password:
            return await call_next(request)
        if self._ok(request.headers.get("authorization")):
            return await call_next(request)
        return PlainTextResponse(
            "인증이 필요합니다.",
            status_code=401,
            headers={"WWW-Authenticate": f'Basic realm="{_REALM}", charset="UTF-8"'},
        )


class LLMBudget:
    """프로세스 단위 LLM 호출 상한.

    인증이 있어도 실수로 반복 요청이 나갈 수 있다. 상한에 닿으면 **LLM만 끄고**
    결정론 생성은 계속한다 — 화면이 죽는 것보다 수치만 나오는 편이 낫다.

    프로세스가 재시작하면 초기화된다. 정확한 회계가 아니라 폭주 방지 장치다.
    """

    def __init__(self, limit: int | None = None) -> None:
        raw = os.environ.get("ARC_LLM_LIMIT", "") if limit is None else str(limit)
        self.limit = int(raw) if raw.strip().isdigit() else 200
        self._used = 0
        self._lock = threading.Lock()

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self._used)

    def take(self) -> bool:
        """호출권 1건 소비. 남아 있지 않으면 False."""
        with self._lock:
            if self._used >= self.limit:
                return False
            self._used += 1
            return True
