"""리뷰 루프 — 코멘트로 한 섹션을 고쳐 쓴다.

이 파일이 지키는 것은 하나다: **LLM이 문서를 고쳐도 숫자는 바뀌지 않는다.**
LLM은 플레이스홀더만 쓰고 값은 프롬프트에 들어가지도 않으므로 이건 약속이
아니라 구조인데, 구조가 깨지면 제품의 논증 전체가 무너진다.
"""

from __future__ import annotations

import datetime as dt

from arc.data.base import Provenance
from arc.llm.number_registry import NumberEntry, NumberRegistry
from arc.llm.revise import (
    RevisionProposal,
    build_prompt,
    find_section,
    revise_section,
    splice,
    split_sections,
)

PROV = Provenance(source="opendart", retrieved_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC))

DOC = """\
# 파마리서치 (214450) — FY2025 실적 리뷰

## 1. 요약

매출은 {{num:revenue_2025a}}이다.

## 7. 리스크 요인

- 원가 변동

## 11. 수치 출처

| 항목 | 값 |

## 12. 디스클레이머

본 자료는 투자권유가 아니며
"""


def _registry():
    reg = NumberRegistry()
    reg.register(
        NumberEntry(
            key="revenue_2025a",
            value=1,
            unit="원",
            display="5,363억원",
            provenance=PROV,
            label="매출액",
        )
    )
    return reg


class _Fake:
    """LLM 대역. 무엇을 돌려줄지 시험이 정한다."""

    def __init__(self, out: str):
        self.out = out
        self.seen = ""

    def complete(self, *, system, user, tier=None, max_tokens=0):
        self.seen = user
        return type(
            "C", (), {"text": self.out, "model": "fake", "cost_usd": 0.001, "latency_s": 0.1}
        )()

    def healthcheck(self):
        return True, "ok"


class TestSections:
    def test_splits_on_h2(self):
        titles = [s.title for s in split_sections(DOC)]
        assert titles == ["1. 요약", "7. 리스크 요인", "11. 수치 출처", "12. 디스클레이머"]

    def test_rule_kept_sections_are_locked(self):
        """디스클레이머·수치 출처는 규칙과 레지스트리가 만드는 자리다.

        LLM이 손대면 3중 고지(G0 발간 조건)나 출처 표(D36)가 깨진다.
        """
        by = {s.title: s for s in split_sections(DOC)}
        assert by["1. 요약"].editable
        assert by["7. 리스크 요인"].editable
        assert not by["11. 수치 출처"].editable
        assert not by["12. 디스클레이머"].editable

    def test_splice_replaces_only_that_section(self):
        s = find_section(DOC, "1. 요약")
        out = splice(DOC, s, "고쳐 쓴 요약 {{num:revenue_2025a}}.")
        assert "고쳐 쓴 요약" in out
        assert "원가 변동" in out  # 다른 섹션은 그대로
        assert "투자권유가 아니며" in out


class TestPromptCarriesNoValues:
    def test_catalog_has_keys_but_not_sizes(self):
        """**값이 프롬프트에 들어가면 LLM이 숫자를 쓸 수 있게 된다.**"""
        p = build_prompt(
            section_label="1. 요약",
            before="매출은 {{num:revenue_2025a}}이다.",
            comment="더 짧게",
            registry=_registry(),
        )
        assert "{{num:revenue_2025a}}" in p
        assert "매출액" in p
        assert "5,363억원" not in p  # 크기는 절대 안 준다


class TestNumbersCannotChange:
    def test_prose_change_keeps_numbers(self):
        fake = _Fake("이번 실적의 핵심은 외형 성장이다. 매출은 {{num:revenue_2025a}}이다.")
        p = revise_section(
            fake,
            section="1. 요약",
            section_label="1. 요약",
            before="매출은 {{num:revenue_2025a}}이다.",
            comment="두괄식으로",
            registry=_registry(),
        )
        assert p.changed
        assert p.numbers_unchanged
        assert p.problems == []

    def test_dropping_a_number_is_flagged(self):
        fake = _Fake("매출이 늘었다.")
        p = revise_section(
            fake,
            section="1. 요약",
            section_label="1. 요약",
            before="매출은 {{num:revenue_2025a}}이다.",
            comment="짧게",
            registry=_registry(),
        )
        assert not p.numbers_unchanged
        assert any("수치 구성이 바뀌었습니다" in x for x in p.problems)

    def test_invented_key_is_flagged(self):
        """카탈로그 밖의 키는 치환되지 않고 G0가 막는다. 미리 알린다."""
        fake = _Fake("매출은 {{num:made_up_2025a}}이다.")
        p = revise_section(
            fake,
            section="1. 요약",
            section_label="1. 요약",
            before="매출은 {{num:revenue_2025a}}이다.",
            comment="바꿔줘",
            registry=_registry(),
        )
        assert any("카탈로그에 없는 키" in x for x in p.problems)

    def test_llm_failure_leaves_the_text_alone(self):
        class _Boom:
            def complete(self, **_):
                raise RuntimeError("타임아웃")

            def healthcheck(self):
                return False, "x"

        p = revise_section(
            _Boom(),
            section="1. 요약",
            section_label="1. 요약",
            before="원문 그대로 {{num:revenue_2025a}}.",
            comment="고쳐줘",
            registry=_registry(),
        )
        assert not p.changed
        assert p.numbers_unchanged
        assert p.problems


class TestFence:
    def test_code_fence_is_stripped(self):
        fake = _Fake("```markdown\n고친 문장 {{num:revenue_2025a}}.\n```")
        p = revise_section(
            fake,
            section="1. 요약",
            section_label="1. 요약",
            before="원문 {{num:revenue_2025a}}.",
            comment="x",
            registry=_registry(),
        )
        assert not p.after.startswith("```")
        assert p.numbers_unchanged


def test_proposal_reports_no_change_when_identical():
    p = RevisionProposal(section="s", comment="c", before="같다.", after="같다.")
    assert not p.changed
