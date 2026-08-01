"""산업 서사 레인 테스트 — **미검증 레인의 안전 조건**.

이 레인은 공시 밖 서술이라 출처로 되짚을 수 없다. 그래서 허용하는 조건이
하나 있다: **숫자가 없어야 한다.** 검증 수단이 없는 문단에 숫자가 들어가면
그 숫자는 아무도 확인할 수 없다.

숫자가 섞이면 리포트 전체를 막지 않고 **이 문단만 버린다** — 검증 불가능한
레인 하나 때문에 검증된 나머지를 못 내보내는 건 합리적이지 않다.
"""

from __future__ import annotations

import json

import pytest

from arc.llm.narrate import build_industry_prompt, narrate_industry
from arc.llm.number_registry import NumberRegistry


class _FakeClient:
    """지정한 산업 문단을 돌려주는 대역."""

    name = "fake"

    def __init__(self, text: str | None = None, raise_exc: Exception | None = None):
        self._text = text
        self._raise = raise_exc
        self.calls: list[dict] = []

    def complete(self, *, system, user, tier=None, max_tokens=4096):
        self.calls.append({"system": system, "user": user})
        if self._raise:
            raise self._raise
        from arc.llm.client import Completion

        return Completion(
            text=json.dumps({"industry_context": self._text}, ensure_ascii=False),
            model="fake",
            provider="fake",
        )

    def healthcheck(self):
        return True, "ok"


def _run(text, profile="PDRN 기반 의약품을 만든다.", registry=None):
    return narrate_industry(
        _FakeClient(text),
        company_name="(주)파마리서치",
        profile_text=profile,
        segments=["의약품", "의료기기"],
        registry=registry or NumberRegistry(),
    )


class TestNumberSafety:
    def test_clean_text_accepted(self):
        body, problems = _run("진입장벽은 인허가와 품질관리에서 형성되는 것이 일반적이다.")
        assert body
        assert not problems

    @pytest.mark.parametrize(
        "text",
        [
            "시장 규모는 3조원에 이른다.",
            "연평균 12.5% 성장한다.",
            "점유율이 40%에 달한다.",
            "2025년 기준 확대되고 있다.",
        ],
    )
    def test_any_number_drops_the_paragraph(self, text):
        """검증 수단이 없는 문단의 숫자는 아무도 확인할 수 없다."""
        body, problems = _run(text)
        assert body == ""
        assert problems and "숫자" in problems[0]

    def test_dropping_does_not_raise(self):
        """리포트 전체를 막지 않는다 — 이 레인만 조용히 사라진다."""
        body, problems = _run("점유율 1위다.")
        assert body == ""
        assert isinstance(problems, list)


class TestFailureIsNotFatal:
    def test_no_profile_means_no_call(self):
        body, problems = _run("아무거나", profile="")
        assert body == ""
        assert problems

    def test_provider_error_returns_empty(self):
        body, problems = narrate_industry(
            _FakeClient(raise_exc=RuntimeError("timeout")),
            company_name="x",
            profile_text="의약품을 만든다.",
            segments=[],
            registry=NumberRegistry(),
        )
        assert body == ""
        assert "RuntimeError" in problems[0]

    def test_empty_response_returns_empty(self):
        body, problems = _run("")
        assert body == ""
        assert problems


class TestPromptIsolation:
    def test_no_number_catalog_given(self):
        """수치 카탈로그를 주면 이 레인이 재무 서술로 변질된다."""
        client = _FakeClient("산업 구조를 서술한다.")
        narrate_industry(
            client,
            company_name="(주)파마리서치",
            profile_text="PDRN 기반 의약품",
            segments=["의약품"],
            registry=NumberRegistry(),
        )
        assert "{{num:" not in client.calls[0]["user"]
        assert "카탈로그" not in client.calls[0]["user"]

    def test_prompt_carries_segments_and_profile(self):
        prompt = build_industry_prompt("(주)파마리서치", "PDRN 기반 의약품", ["의약품", "화장품"])
        assert "PDRN" in prompt
        assert "의약품, 화장품" in prompt

    def test_prompt_forbids_numbers_explicitly(self):
        from arc.llm.narrate import INDUSTRY_SYSTEM_PROMPT

        assert "숫자를 일절 쓰지 마십시오" in INDUSTRY_SYSTEM_PROMPT
        assert "삭제됩니다" in INDUSTRY_SYSTEM_PROMPT
