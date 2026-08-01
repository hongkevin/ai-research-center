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

log = logging.getLogger("arc.web")

# 인증 없이 열어둘 경로 — 플랫폼 헬스체크는 인증을 통과할 수 없다
PUBLIC_PATHS = frozenset({"/api/health"})

_REALM = "AI Research Center"


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """공유 비밀번호. `ARC_PASSWORD`가 비어 있으면 통과시킨다(로컬 개발)."""

    def __init__(self, app, password: str | None = None, username: str = "arc") -> None:
        super().__init__(app)
        self.password = password if password is not None else os.environ.get("ARC_PASSWORD", "")
        self.username = os.environ.get("ARC_USERNAME", username)
        if not self.password:
            log.warning(
                "ARC_PASSWORD가 없어 인증 없이 실행됩니다. "
                "공개 주소에 올릴 때는 반드시 설정하십시오 — 서버의 LLM 키가 무방비가 됩니다."
            )

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
        if not self.password or request.url.path in PUBLIC_PATHS:
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
