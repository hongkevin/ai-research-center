"""최근 이슈 레인 — **공시 밖 서술의 두 번째 형태**.

산업 배경([D31](../docs/decisions.md))과 같은 레인이지만 근거가 다르다. 저쪽은
모델의 기억이고 이쪽은 **날짜와 링크가 붙은 기사**다. 그래서 독자가 되짚을 수
있는데, 되짚을 수 있다는 것과 우리가 검산했다는 것은 다르다.

지켜야 할 것 셋:

* 문단에 숫자가 있으면 **버린다.** 검산 수단이 없는 건 그대로다.
* 프롬프트에 들어가는 스니펫의 숫자는 **가려서** 넣는다. 안 가리면 LLM이
  베끼고, G0가 막고, 재시도만 낭비한다.
* 기사 제목의 숫자도 가려서 싣는다. 우리가 확인하지 않은 숫자를 노트가 그대로
  옮기면 독자는 그걸 우리 주장으로 읽는다.
"""

from __future__ import annotations

import json

from arc.llm.narrate import build_news_prompt, narrate_news
from arc.llm.number_registry import mask_numbers

ARTICLES = [
    {
        "title": "삼성물산, 카타르에서 5,000억원 규모 플랜트 수주",
        "snippet": "삼성물산 건설부문이 카타르 발주처와 계약을 체결했다고 30일 밝혔다.",
    },
    {
        "title": "삼성물산 바이오 자회사 증설 착수",
        "snippet": "4공장 가동에 이어 5공장 투자를 결정했다.",
    },
]


class _Stub:
    """LLM 대역. 무엇을 받았는지 붙잡아 둔다."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.user = ""

    def complete(self, *, system: str, user: str, tier=None):
        self.user = user

        class C:
            text = self.reply

        return C()


class _Boom:
    def complete(self, **_):
        raise RuntimeError("provider down")


def _reply(text: str) -> str:
    return json.dumps({"recent_issues": text}, ensure_ascii=False)


class TestNewsLane:
    def test_keeps_prose_without_numbers(self):
        text, problems = narrate_news(
            _Stub(_reply("카타르 플랜트 수주가 보도됐다. 바이오 증설도 알려졌다.")),
            company_name="삼성물산",
            articles=ARTICLES,
        )
        assert "카타르" in text
        assert problems == []

    def test_drops_the_paragraph_when_a_number_appears(self):
        """**검증 수단이 없는 레인에 숫자를 두지 않는다** (D31)."""
        text, problems = narrate_news(
            _Stub(_reply("5,000억원 규모 수주가 보도됐다.")),
            company_name="삼성물산",
            articles=ARTICLES,
        )
        assert text == ""
        assert problems and "숫자" in problems[0]

    def test_a_year_is_a_number_too(self):
        """연도도 아무도 확인할 수 없다 — 산업 배경과 같은 기준이다."""
        text, _ = narrate_news(
            _Stub(_reply("2026년부터 규제가 바뀐다고 보도됐다.")),
            company_name="삼성물산",
            articles=ARTICLES,
        )
        assert text == ""

    def test_provider_failure_does_not_raise(self):
        """이 문단은 있으면 좋은 것이지 발간의 전제가 아니다."""
        text, problems = narrate_news(_Boom(), company_name="삼성물산", articles=ARTICLES)
        assert text == ""
        assert problems and "RuntimeError" in problems[0]

    def test_no_articles_no_paragraph(self):
        text, problems = narrate_news(_Boom(), company_name="삼성물산", articles=[])
        assert text == ""
        assert problems

    def test_empty_reply_is_allowed(self):
        """쓸 만한 게 없으면 **억지로 채우지 않는다.**"""
        text, problems = narrate_news(_Stub(_reply("")), company_name="삼성물산", articles=ARTICLES)
        assert text == ""
        assert problems


class TestPrompt:
    def test_snippets_reach_the_prompt(self):
        prompt = build_news_prompt("삼성물산", ARTICLES)
        assert "카타르 발주처와 계약" in prompt
        assert "[1]" in prompt and "[2]" in prompt

    def test_masking_removes_amounts_before_the_prompt(self):
        """파이프라인이 넣기 전에 가린다 — 유혹을 애초에 없앤다."""
        masked = mask_numbers(ARTICLES[0]["title"])
        assert "5,000억원" not in masked
        assert "⟨수치⟩" in masked
        assert "카타르" in masked

    def test_masking_is_gate_safe_by_construction(self):
        """가림과 탐지가 **같은 화이트리스트**를 쓴다 — 갈라지면 게이트에 걸린다."""
        from arc.llm.number_registry import NumberRegistry

        masked = mask_numbers("삼성물산, 5,000억원 규모 수주 · 영업이익률 12.3% 기록")
        assert NumberRegistry().find_unregistered_numbers(masked) == []
