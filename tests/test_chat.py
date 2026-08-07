"""리서치 채팅 코어.

이 파일이 지키는 것 셋:

1. **값은 프롬프트에 들어가지 않는다.** 카드 본문의 숫자는 이미
   플레이스홀더라 그대로 넘겨도 되고, 카탈로그는 라벨·단위만 준다.
2. **지어낸 수치는 나가지 못한다.** 미등록 숫자·모르는 키가 든 문장은 버린다.
3. **모르면 모른다고 한다.** 근거가 없으면 LLM을 부르지도 않는다.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from arc.chat import (
    NO_EVIDENCE,
    POLICY_REFUSAL,
    answer_question,
    asks_for_opinion,
    build_prompt,
    card_passages,
    check_answer,
    parse_query,
    retrieve,
)
from arc.chat.evidence import tokenize
from arc.data.base import Provenance
from arc.llm.client import Completion
from arc.store.cards import Card

PROV = Provenance(
    source="opendart",
    retrieved_at=dt.datetime(2026, 8, 7, tzinfo=dt.UTC),
    dataset="재무제표 (전체계정) → 2. 연결재무제표",
    source_url="https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
    verify_url="https://dart.fss.or.kr/report/viewer.do?rcpNo=20260515002876",
    source_ref="20260515002876",
)
DIVIDEND_PROV = Provenance(
    source="opendart",
    retrieved_at=dt.datetime(2026, 8, 7, tzinfo=dt.UTC),
    dataset="정기보고서 · 배당에 관한 사항",
    source_url="https://opendart.fss.or.kr/api/alotMatter.json",
    verify_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260515002876",
    source_ref="20260515002876",
)

DOC = """\
# 현대로템(주) (064350) — 2026년 1분기 누적 실적 리뷰

| 항목 | 내용 |
|---|---|
| 시장 | KOSPI |

## 1. 요약

매출은 {{num:revenue_2026a}}으로 전년 대비 {{num:revenue_yoy_2026a}} 변동했다.
영업이익률은 {{num:operating_margin_2026a}}이다.

## 4. 실적 분석

### 4.4 부문별 수익성

| 부문 | 매출 |
|---|---|
| 방산 부문 | {{num:opseg1_revenue_2026a}} |

## 11. 수치 출처

| 항목 | 값 |
|---|---|
| 매출액 | {{num:revenue_2026a}} |

## 12. 디스클레이머

본 자료는 투자권유가 아니며 AI를 활용해 작성했습니다.
"""


def _entry(key, label, unit, display, prov=PROV):
    return {
        "key": key,
        "value": 1.0,
        "unit": unit,
        "display": display,
        "provenance": prov.model_dump(mode="json"),
        "label": label,
        "formula": None,
        "inputs": [],
        "internal": False,
    }


REGISTRY = [
    _entry("revenue_2026a", "매출액 (2026A)", "원", "1조 4,575억원"),
    _entry("revenue_yoy_2026a", "매출액 YoY (2026A)", "%", "23.9%"),
    _entry("operating_margin_2026a", "영업이익률 (2026A)", "%", "6.1%"),
    _entry("opseg1_revenue_2026a", "방산 부문 매출 (2026A)", "원", "8,100억원"),
    _entry("payout_2026a", "배당성향 (2026A)", "%", "12.0%", DIVIDEND_PROV),
]


def _card(**over) -> Card:
    base = {
        "id": "f8e0027bee5346ab",
        "symbol": "064350",
        "year": 2026,
        "period": "Q1",
        "created_at": "2026-08-07T03:35:47+00:00",
        "company": "현대로템(주)",
        "assembled": DOC,
        "registry": [dict(r) for r in REGISTRY],
        "vm": {"gate_passed": True},
    }
    base.update(over)
    return Card(**base)


class _Fake:
    """LLM 대역. 무엇을 돌려줄지 시험이 정한다."""

    def __init__(self, *answers: str, unanswered: list[str] | None = None):
        self.answers = list(answers)
        self.unanswered = unanswered or []
        self.calls: list[str] = []

    def complete(self, *, system, user, tier=None, max_tokens=0):
        self.calls.append(user)
        payload = {"answer": self.answers.pop(0), "unanswered": self.unanswered}
        return Completion(
            text=json.dumps(payload, ensure_ascii=False),
            model="fake-1",
            provider="fake",
            cost_usd=0.0001,
        )


# ── 질문 읽기 ────────────────────────────────────────────────────────
def test_tokenize_strips_particles_and_predicates():
    assert tokenize("영업이익률은 어떻게 됐어?") == ["영업이익률"]
    assert tokenize("부문별 매출이 얼마야") == ["부문별", "매출"]
    # 지표 이름은 서술어 규칙에 걸리지 않는다
    assert "주가" in tokenize("주가 알려줘")
    # 질문의 틀은 「못 찾은 것」이 되면 안 된다
    assert tokenize("영업이익률 원인도 같이 알려줘") == ["영업이익률"]


def test_parse_query_reads_symbol_year_period():
    q = parse_query("064350의 2026년 1분기 매출")
    assert q.symbols == ["064350"]
    assert q.years == [2026]
    assert q.periods == ["Q1"]
    assert "매출" in q.tokens


# ── 카드 → 근거 ──────────────────────────────────────────────────────
def test_card_passages_skip_index_sections():
    sections = {p.section for p in card_passages(_card(), "c1")}
    assert not any("수치 출처" in s for s in sections)
    assert not any("디스클레이머" in s for s in sections)
    assert "1. 요약" in sections
    # `###` 소제목은 그다음 덩이의 이름에 붙는다
    assert "4. 실적 분석 › 4.4 부문별 수익성" in sections


def test_card_passages_namespace_placeholders():
    body = "\n".join(p.text for p in card_passages(_card(), "c2"))
    assert "{{num:c2.revenue_2026a}}" in body
    assert "{{num:revenue_2026a}}" not in body


def test_blocked_card_gets_its_numbers_masked():
    """게이트를 통과 못 한 카드에는 미등록 숫자가 남아 있을 수 있다."""
    card = _card(assembled="## 1. 요약\n\n매출은 1조 4,575억원이다.", vm={"gate_passed": False})
    body = "\n".join(p.text for p in card_passages(card, "c1"))
    assert "1조 4,575억원" not in body
    assert "⟨수치⟩" in body


# ── 검색 ─────────────────────────────────────────────────────────────
def test_retrieve_picks_named_card():
    r = retrieve("현대로템 영업이익률 어떻게 됐어?", [_card()])
    assert r.tags() == ["c1"]
    assert any("요약" in p.section for p in r.passages)
    assert "c1.operating_margin_2026a" in r.keys
    assert r.unmatched == []


def test_retrieve_finds_metric_that_is_only_in_the_registry():
    """본문에 없어도 레지스트리 라벨에 있으면 답할 수 있다."""
    r = retrieve("현대로템 배당성향 얼마야", [_card()])
    assert "c1.payout_2026a" in r.keys


def test_retrieve_gives_nothing_when_the_topic_is_absent():
    r = retrieve("현대로템 신용잔고 뽑아줘", [_card()])
    assert r.empty
    assert "신용잔고" in r.unmatched
    assert "신용잔고" in r.reason


def test_retrieve_refuses_to_answer_about_a_company_we_do_not_have():
    """실측: 「매출」 하나로 다른 회사의 카드가 잡혔다. 가장 나쁜 오답이다."""
    r = retrieve("삼성전자 매출 얼마야", [_card()])
    assert r.empty
    assert "삼성전자" in r.unmatched


def test_retrieve_falls_back_to_summary_when_only_the_name_is_given():
    r = retrieve("현대로템 어때?", [_card()])
    assert r.passages
    assert all("요약" in p.section for p in r.passages)


def test_retrieve_skips_running_and_broken_cards():
    assert retrieve("현대로템 매출", [_card(running=True)]).empty
    assert retrieve("현대로템 매출", [_card(error="중단됨")]).empty


# ── 불변식 1 ─────────────────────────────────────────────────────────
def test_prompt_never_contains_a_value():
    """**이 시험이 이 패키지의 전제다.** 값이 새면 LLM이 리터럴로 베낀다."""
    r = retrieve("현대로템 매출이랑 영업이익률 알려줘", [_card()])
    prompt = build_prompt(r)
    for record in REGISTRY:
        assert record["display"] not in prompt


# ── 답변 ─────────────────────────────────────────────────────────────
def test_answer_substitutes_values_and_lists_each_source():
    client = _Fake("매출은 {{num:c1.revenue_2026a}}이다 [c1].")
    a = answer_question("현대로템 매출 얼마야", [_card()], client=client)

    assert "1조 4,575억원" in a.text
    assert a.grounded is True
    assert a.rejected == []
    number = next(s for s in a.sources if s.kind == "number")
    assert number.key == "revenue_2026a"
    assert number.dataset == "재무제표 (전체계정) → 2. 연결재무제표"
    assert number.verify_url.startswith("https://dart.fss.or.kr/")
    assert number.card_id == "f8e0027bee5346ab"


def test_sources_differ_per_item():
    """D36 — 배당 항목의 출처가 재무제표로 표시되면 검토자에게 틀린 답을 한다."""
    client = _Fake(
        "매출은 {{num:c1.revenue_2026a}}이고 배당성향은 {{num:c1.payout_2026a}}이다 [c1]."
    )
    a = answer_question("현대로템 매출과 배당성향", [_card()], client=client)
    datasets = {s.key: s.dataset for s in a.sources if s.kind == "number"}
    assert datasets["revenue_2026a"] != datasets["payout_2026a"]
    assert "배당" in datasets["payout_2026a"]


def test_invented_number_loses_its_sentence():
    client = _Fake("매출은 {{num:c1.revenue_2026a}}이다 [c1].\n영업이익률은 6.1% 수준이다 [c1].")
    a = answer_question("현대로템 매출", [_card()], client=client)
    assert "6.1%" not in a.text
    assert "1조 4,575억원" in a.text
    assert len(a.rejected) == 1


def test_unknown_key_loses_its_sentence():
    client = _Fake(
        "매출은 {{num:c1.revenue_2026a}}이다 [c1].\n수주잔고는 {{num:c1.backlog}}다 [c1]."
    )
    a = answer_question("현대로템 매출", [_card()], client=client)
    assert "backlog" not in a.text
    assert len(a.rejected) == 1


def test_citing_a_card_that_was_not_given_loses_its_sentence():
    client = _Fake("매출은 {{num:c1.revenue_2026a}}이다 [c1].\n경쟁사는 더 낮다 [c9].")
    a = answer_question("현대로템 매출", [_card()], client=client)
    assert "[c9]" not in a.text
    assert len(a.rejected) == 1


def test_every_sentence_rejected_becomes_a_no_answer():
    client = _Fake("영업이익률은 6.1%다 [c1].")
    a = answer_question("현대로템 매출", [_card()], client=client)
    assert a.text.startswith(NO_EVIDENCE)
    assert a.grounded is False


def test_opinion_in_the_answer_refuses_the_whole_answer():
    """문장만 빼면 남은 문장이 그 판단의 근거로 읽힌다."""
    client = _Fake("매출은 {{num:c1.revenue_2026a}}이다 [c1].\n목표주가는 20만원으로 본다 [c1].")
    a = answer_question("현대로템 매출", [_card()], client=client)
    assert a.text == POLICY_REFUSAL
    assert a.refused
    assert "1조 4,575억원" not in a.text


def test_opinion_question_never_reaches_the_model():
    client = _Fake("답")
    a = answer_question("현대로템 지금 사도 될까요?", [_card()], client=client)
    assert a.text == POLICY_REFUSAL
    assert client.calls == []


@pytest.mark.parametrize(
    "question",
    ["현대로템 목표주가 얼마예요", "현대로템 투자의견 알려줘", "지금 매수해도 되나요"],
)
def test_opinion_questions_are_detected(question):
    assert asks_for_opinion(question)


def test_plain_question_is_not_treated_as_opinion():
    assert asks_for_opinion("현대로템 영업이익률 어떻게 됐어?") == ""


def test_no_evidence_never_reaches_the_model():
    client = _Fake("아무 말")
    a = answer_question("현대로템 신용잔고 뽑아줘", [_card()], client=client)
    assert a.text.startswith(NO_EVIDENCE)
    assert a.unanswered == ["현대로템 신용잔고 뽑아줘"]
    assert a.grounded is False
    assert client.calls == []


def test_without_a_client_the_evidence_still_comes_back():
    """키가 없는 환경에서도 검색과 출처는 그대로 동작해야 한다."""
    a = answer_question("현대로템 매출 얼마야", [_card()], client=None)
    assert a.sources
    assert a.grounded is False
    assert a.used_llm is False


def test_unmatched_terms_are_reported_as_unanswered():
    client = _Fake("매출은 {{num:c1.revenue_2026a}}이다 [c1].")
    a = answer_question("현대로템 매출이랑 신용잔고 알려줘", [_card()], client=client)
    assert any("신용잔고" in u for u in a.unanswered)


def test_sentence_without_a_source_is_flagged_but_kept():
    client = _Fake("매출은 {{num:c1.revenue_2026a}}이다 [c1].\n정리하면 이렇습니다.")
    a = answer_question("현대로템 매출", [_card()], client=client)
    assert "정리하면 이렇습니다." in a.text
    assert a.unsourced == ["정리하면 이렇습니다."]


def test_empty_answer_is_retried_then_given_up():
    client = _Fake("", "")
    a = answer_question("현대로템 매출", [_card()], client=client)
    assert len(client.calls) == 2
    assert a.text.startswith(NO_EVIDENCE)


def test_second_attempt_is_told_what_went_wrong():
    client = _Fake("", "매출은 {{num:c1.revenue_2026a}}이다 [c1].")
    a = answer_question("현대로템 매출", [_card()], client=client)
    assert "직전 시도의 문제" in client.calls[1]
    assert "1조 4,575억원" in a.text


def test_a_marker_after_the_period_still_belongs_to_its_sentence():
    """실측: 모델이 「…없습니다. [c1]」로 쓰자 앞 문장이 출처 없음으로 잡혔다."""
    client = _Fake("매출은 {{num:c1.revenue_2026a}}이다. [c1]")
    a = answer_question("현대로템 매출", [_card()], client=client)
    assert a.unsourced == []
    assert a.grounded is True


def test_line_structure_survives_the_guard():
    client = _Fake(
        "- 매출: {{num:c1.revenue_2026a}} [c1]\n- 영업이익률: {{num:c1.operating_margin_2026a}} [c1]"
    )
    a = answer_question("현대로템 실적", [_card()], client=client)
    assert a.text.count("\n") == 1


# ── 게이트 재사용 ────────────────────────────────────────────────────
def test_check_answer_uses_the_same_rules_as_the_report_gate():
    r = retrieve("현대로템 매출", [_card()])
    v = check_answer("매출은 반드시 늘어난다 [c1].", r)
    assert v.rejected  # 단정 표현 — G0의 §3 불변식 4
    assert v.text == ""
