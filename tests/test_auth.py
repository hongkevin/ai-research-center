"""접근 제어 테스트.

서버가 **OpenAI 키를 들고 있다.** 인증 없이 공개 주소에 올리면 주소를 아는
누구나 그 키로 생성을 돌리고 요금은 키 주인이 낸다. 이 파일이 지키는 것은
그 한 가지다.
"""

from __future__ import annotations

import base64

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from arc.web.auth import BasicAuthMiddleware, LLMBudget


def _app(password: str | None):
    async def ok(request):
        return PlainTextResponse("ok")

    app = Starlette(
        routes=[
            Route("/", ok),
            Route("/api/health", ok),
            Route("/api/reports", ok, methods=["POST"]),
        ]
    )
    app.add_middleware(BasicAuthMiddleware, password=password)
    return TestClient(app)


def _auth(user: str, pw: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


class TestBasicAuth:
    def test_blocks_without_credentials(self):
        assert _app("secret").get("/").status_code == 401

    def test_blocks_wrong_password(self):
        assert _app("secret").get("/", headers=_auth("arc", "nope")).status_code == 401

    def test_blocks_wrong_username(self):
        assert _app("secret").get("/", headers=_auth("root", "secret")).status_code == 401

    def test_allows_correct_credentials(self):
        assert _app("secret").get("/", headers=_auth("arc", "secret")).status_code == 200

    def test_api_is_protected_too(self):
        """화면만 막고 API를 열어두면 인증이 없는 것과 같다."""
        assert _app("secret").post("/api/reports").status_code == 401

    def test_health_is_public(self):
        """플랫폼 헬스체크는 인증을 통과할 수 없다."""
        assert _app("secret").get("/api/health").status_code == 200

    def test_challenge_header_sent(self):
        """브라우저가 로그인 창을 띄우려면 이 헤더가 필요하다."""
        r = _app("secret").get("/")
        assert r.headers.get("www-authenticate", "").lower().startswith("basic")

    @pytest.mark.parametrize("header", ["", "Bearer xyz", "Basic !!!notbase64", "Basic"])
    def test_malformed_header_rejected(self, header):
        assert _app("secret").get("/", headers={"Authorization": header}).status_code == 401

    def test_disabled_when_no_password(self):
        """로컬 개발에서 매번 비밀번호를 치게 만들면 개발이 느려진다."""
        assert _app("").get("/").status_code == 200


class TestLLMBudget:
    def test_allows_up_to_limit(self):
        b = LLMBudget(limit=3)
        assert [b.take() for _ in range(3)] == [True, True, True]

    def test_blocks_past_limit(self):
        b = LLMBudget(limit=2)
        b.take()
        b.take()
        assert b.take() is False
        assert b.remaining == 0

    def test_tracks_usage(self):
        b = LLMBudget(limit=5)
        b.take()
        b.take()
        assert b.used == 2
        assert b.remaining == 3

    def test_zero_limit_blocks_everything(self):
        assert LLMBudget(limit=0).take() is False

    def test_env_limit_applied(self, monkeypatch):
        monkeypatch.setenv("ARC_LLM_LIMIT", "7")
        assert LLMBudget().limit == 7

    def test_bad_env_falls_back_to_default(self, monkeypatch):
        """잘못된 값 때문에 상한이 사라지면 안 된다."""
        monkeypatch.setenv("ARC_LLM_LIMIT", "많이")
        assert LLMBudget().limit == 200
