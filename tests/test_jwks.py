"""비대칭 서명키 검증 (D78).

Supabase가 2025년에 두 가지를 바꿨다:

  1. API 키 — `anon`(JWT) → `sb_publishable_…` (레거시는 2026년 말 폐기)
  2. **토큰 서명 — 공유 시크릿(HS256) → 비대칭 서명키(ES256/RS256) + JWKS**

우리 검증기는 ①만 알고 있었다. 실제로 이 저장소의 프로젝트는 ES256 하나뿐이고
공유 시크릿이 **아예 없다** — 그런데도 로그인이 되는 것처럼 보였던 이유는
`SUPABASE_JWT_SECRET`에 서명키의 **Key ID(UUID)** 가 들어가 있었기 때문이고,
그 상태에서는 모든 토큰이 조용히 거부된다.
"""

from __future__ import annotations

import pytest

from arc.web.auth import BasicAuthMiddleware, _is_progress_stream, _jwks_url


class TestJwksUrl:
    @pytest.mark.parametrize(
        ("given", "want"),
        [
            (
                "https://abcd.supabase.co",
                "https://abcd.supabase.co/auth/v1/.well-known/jwks.json",
            ),
            # 끝의 슬래시를 지우지 않으면 `//auth/v1/…`이 된다
            (
                "https://abcd.supabase.co/",
                "https://abcd.supabase.co/auth/v1/.well-known/jwks.json",
            ),
        ],
    )
    def test_it_builds_the_discovery_url(self, given, want):
        assert _jwks_url(given) == want

    @pytest.mark.parametrize("given", ["", "  ", "abcd", "abcd.supabase.co", "http://abcd.co"])
    def test_a_project_ref_alone_is_not_a_url(self, given):
        """**프로젝트 ref만 넣는 실수가 실제로 났다.**

        `bygdsevzivdujuipnjwl`만 적혀 있었고, 그러면 JWKS 주소를 못 만든다.
        빈 문자열로 떨어뜨려 「인증 안 켜짐」이 되는 편이, 깨진 주소로
        조회를 시도하는 것보다 낫다 — 후자는 매 요청 타임아웃을 문다.
        """
        assert _jwks_url(given) == ""

    def test_https_only(self):
        """평문 http로 공개키를 받지 않는다."""
        assert _jwks_url("http://abcd.supabase.co") == ""


class TestMode:
    def test_a_project_url_alone_turns_verification_on(self, monkeypatch):
        """**시크릿 없이도 인증이 켜진다.** 지금 Supabase의 기본 형태다."""
        monkeypatch.setenv("SUPABASE_JWT_SECRET", "")
        monkeypatch.setenv("NEXT_PUBLIC_SUPABASE_URL", "https://abcd.supabase.co")
        mw = BasicAuthMiddleware(None)
        assert mw.jwks_url.endswith("/.well-known/jwks.json")
        assert mw.jwt_secret == ""

    def test_neither_means_no_verification(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_JWT_SECRET", "")
        monkeypatch.setenv("NEXT_PUBLIC_SUPABASE_URL", "")
        assert BasicAuthMiddleware(None).jwks_url == ""


class TestClaims:
    def _mw(self, monkeypatch, *, secret="", url=""):
        monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
        monkeypatch.setenv("NEXT_PUBLIC_SUPABASE_URL", url)
        monkeypatch.setenv("SUPABASE_URL", "")
        return BasicAuthMiddleware(None)

    def test_an_hs256_token_is_refused_when_there_is_no_shared_secret(self, monkeypatch):
        """**이게 이번에 물린 함정이다.**

        프로젝트가 ES256 하나뿐인데 `SUPABASE_JWT_SECRET`에 Key ID를 넣어 두면
        HS256 경로가 켜지고, 진짜 ES256 토큰이 전부 거부된다. 반대로 시크릿이
        비어 있으면 위조된 HS256 토큰이 **키가 없어서** 거부된다.
        """
        import jwt

        forged = jwt.encode({"sub": "hacker", "aud": "authenticated"}, "guess", algorithm="HS256")
        mw = self._mw(monkeypatch, url="https://abcd.supabase.co")
        assert mw._claims(f"Bearer {forged}") is None

    def test_es256_without_a_reachable_jwks_is_refused_not_allowed(self, monkeypatch):
        """**JWKS를 못 받으면 거부한다.** 열어 두는 판단은 여기서 안 한다 —
        이 서버는 LLM 키를 들고 있다."""
        mw = self._mw(monkeypatch, url="https://nonexistent-project-xyz.supabase.co")
        # 서명은 못 만들지만 헤더만 ES256인 토큰으로 경로를 태운다
        fake = "eyJhbGciOiJFUzI1NiIsImtpZCI6ImsxIn0.eyJzdWIiOiJhIn0.sig"
        assert mw._claims(f"Bearer {fake}") is None

    @pytest.mark.parametrize("header", [None, "", "Basic abc", "Bearer", "bearer "])
    def test_junk_headers_are_refused(self, monkeypatch, header):
        mw = self._mw(monkeypatch, secret="s", url="")
        assert mw._claims(header) is None


class TestProgressStream:
    """쿼리 토큰은 **SSE에만** 준다 — 토큰이 서버 로그에 남기 때문이다."""

    def test_the_job_stream_gets_it(self):
        assert _is_progress_stream("/api/jobs/abc123/events") is True

    def test_the_event_log_does_not(self):
        """`/api/events`(사건 로그)가 여기 걸리면 조회가 쿼리 토큰을 받는다."""
        assert _is_progress_stream("/api/events") is False

    @pytest.mark.parametrize("path", ["/api/brief", "/api/events?days=30", "/events"])
    def test_nothing_else_does(self, path):
        assert _is_progress_stream(path) is False
