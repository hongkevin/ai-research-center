"""시세·공시 레인 (D86).

**리포트가 없어도 답한다.** 병목은 리포트가 아니라 클라이언트 리퀘스트인데
(하루 10~15건, 시간 청구) 채팅이 카드만 근거로 쳐서, 커버 20~30종목의 리포트를
다 쓰기 전까지 위젯이 쓸모없었다.

지키는 것 셋:

  1. **불변식 1** — 값이 프롬프트에 안 들어간다. 플레이스홀더뿐이다
  2. **G0를 통과하는 꼴로 쓴다** — 모델이 되뱉는 것까지 계산에 넣는다
  3. **공시는 제목까지** — 본문 해석은 리포트가 하는 일이다
"""

from __future__ import annotations

from arc.chat.market import MARKET_TAG, build_market_facts, market_prompt
from arc.llm.number_registry import NumberRegistry

MOVES = {
    "last_date": "20260807",
    "items": [
        {"key": "1d", "label": "1일", "change_pct": 0.45},
        {"key": "1m", "label": "1개월", "change_pct": 6.18},
        {"key": "6m", "label": "6개월", "change_pct": 9.84},  # HORIZONS에 없다
        {"key": "5d", "label": "5일", "change_pct": None},  # 값이 없다
    ],
}
FILINGS = [
    {"title": "[발행조건확정]증권신고서(채무증권)", "filed_at": "2026-08-05T00:00:00", "url": "u"},
]


def _facts():
    return build_market_facts("316140", company="우리금융지주", moves=MOVES, filings=FILINGS)


class TestBuild:
    def test_only_the_horizons_we_show(self):
        """**전부 싣지 않는다** — 「왜 올랐나」에 답하는 것은 최근이다."""
        assert [e.key for e in _facts().entries] == [
            f"{MARKET_TAG}.change_1d",
            f"{MARKET_TAG}.change_1m",
        ]

    def test_a_missing_value_is_skipped_not_zeroed(self):
        """**0으로 채우지 않는다.** 5일치가 없는 것은 5일에 안 움직인 것이 아니다."""
        assert all("5d" not in e.key for e in _facts().entries)

    def test_every_number_carries_provenance(self):
        """출처 없는 숫자가 답에 앉으면 이 제품의 전제가 무너진다."""
        for e in _facts().entries:
            assert e.provenance.source == "krx_price"
            assert e.provenance.source_url

    def test_filings_are_titles_not_numbers(self):
        """**본문을 안 읽는다.** 해석은 리포트가 하고, 흉내 내면 검산 없는
        숫자가 답에 앉는다."""
        titles = [t for t, _, _ in _facts().filings]
        assert titles == ["[발행조건확정]증권신고서(채무증권)"]

    def test_nothing_means_empty(self):
        got = build_market_facts("000000")
        assert got.empty is True
        assert market_prompt(got) == ""


class TestPrompt:
    def test_values_never_reach_the_prompt(self):
        """**불변식 1.** 다른 레인과 같은 규칙이다 — 플레이스홀더만 쓴다."""
        text = market_prompt(_facts())
        assert "{{num:" in text
        assert "6.18" not in text
        assert "0.45" not in text

    def test_the_prompt_passes_the_gate(self):
        """**모델이 되뱉는 것까지 계산에 넣는다.**

        실측으로 걸렸던 것들: `20260807`(막힘 → `2026-08-07`로),
        `[316140]`(막힘 → 태그로). 프롬프트에 있는 숫자는 답에 그대로 나올 수
        있고, 그러면 G0가 그 문장을 버린다.
        """
        from arc.chat.guard import _MARKER_RE

        facts = _facts()
        registry = NumberRegistry()
        registry.register_all(facts.entries)
        # **검증기와 같은 방식으로 잰다** — 마커를 떼고 본다. 안 그러면
        # `[m1]`의 `1`이 미등록 숫자로 잡힌다(`guard.check_answer`가 그래서
        # `probe`를 따로 만든다).
        probe = _MARKER_RE.sub("", market_prompt(facts))
        bad = registry.find_unregistered_numbers(probe)
        assert bad == [], [b.text for b in bad]

    def test_it_tells_the_model_how_to_cite(self):
        """`guard._MARKER_RE`가 아는 꼴이어야 한다 — 회사명으로 인용시켰더니
        문장 셋이 전부 「출처 없음」으로 찍혔다."""
        assert f"[{MARKET_TAG}]" in market_prompt(_facts())

    def test_it_forbids_guessing_what_a_filing_means(self):
        assert "추측하지 마십시오" in market_prompt(_facts())


class TestGuardKnowsTheTag:
    def test_the_marker_regex_accepts_it(self):
        """레인이 늘면 검증기도 늘어야 한다. 모르는 마커는 「출처 없음」이 되고,
        근거가 멀쩡한 문장이 의심 목록에 오른다."""
        from arc.chat.guard import _MARKER_RE

        assert _MARKER_RE.findall(f"등락은 이렇다 [{MARKET_TAG}]") == [MARKET_TAG]
        assert _MARKER_RE.findall("카드에서 [c1]") == ["c1"]

    def test_retrieval_can_carry_extra_tags(self):
        from arc.chat.retrieval import Retrieval

        r = Retrieval(question="q", query=None)  # type: ignore[arg-type]
        r.extra_tags = [MARKET_TAG]
        assert MARKET_TAG in r.tags()
