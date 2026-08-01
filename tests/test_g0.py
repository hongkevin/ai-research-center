"""G0 하드 게이트 적대적 테스트.

§3 불변식이 코드로 강제되는지 확인한다. 규제 가드레일이라 통과 기준을
완화하지 않는다 — 하나라도 새면 발간물에 규제 리스크가 실린다.
"""

from __future__ import annotations

import datetime as dt

import pytest

from arc.data.base import Provenance
from arc.llm.number_registry import NumberEntry, NumberRegistry
from arc.verify.g0 import G0Gate

PROV = Provenance(source="opendart", retrieved_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC))


def gate() -> G0Gate:
    r = NumberRegistry()
    r.register_all(
        [
            NumberEntry(
                key="rev_2025a",
                value=3_009_100,
                unit="억원",
                display="300조 9,100억원",
                provenance=PROV,
            ),
            NumberEntry(
                key="rev_yoy_2025a", value=12.3, unit="%", display="12.3%", provenance=PROV
            ),
            NumberEntry(
                key="fair_low", value=52_000, unit="원", display="52,000원", provenance=PROV
            ),
            NumberEntry(
                key="fair_high", value=68_000, unit="원", display="68,000원", provenance=PROV
            ),
        ]
    )
    return G0Gate(r)


VALID_REPORT = """# 테스트기업 (000000) — 2025년 연간 실적 리뷰

## 2. 요약

매출은 {{num:rev_2025a}}으로 전년 대비 {{num:rev_yoy_2025a}} 성장했다.

## 3. 사업 이해

의료기기와 화장품을 만들어 국내외에 판다.

## 4. 투자포인트

### 1. 외형 성장

매출 성장률 {{num:rev_yoy_2025a}}가 이를 뒷받침한다.

## 4. 실적 분석

FY2025 기준 매출 {{num:rev_2025a}}.

## 5. 실적 추정

추정 가정을 명시한다.

## 6. 밸류에이션

**산출 산식**: 2026E BPS × 밴드 하단~상단 PBR

적정가치 범위는 {{num:fair_low}} ~ {{num:fair_high}}이다.

## 7. 리스크 요인

- 전방 수요 둔화

## 8. 디스클레이머

1. 본 자료는 자본시장법상 조사분석자료가 아닙니다.
2. 본 자료는 특정 금융투자상품의 매매를 권유하는 투자권유가 아니며, 투자 판단의 최종 책임은 투자자 본인에게 있습니다.
3. 본 자료는 AI(인공지능)를 활용하여 생성되었으며, 사람의 검토를 거쳐 발간되었습니다.
"""


class TestValidReport:
    def test_passes(self):
        r = gate().check(VALID_REPORT)
        assert r.passed, f"통과해야 하는데 차단됨: {[v.detail for v in r.violations]}"
        assert r.summary() == "G0 통과"


class TestNumbers:
    def test_unregistered_literal_blocks(self):
        bad = VALID_REPORT.replace(
            "전년 대비 {{num:rev_yoy_2025a}} 성장", "전년 대비 약 12.3% 성장"
        )
        r = gate().check(bad)
        assert not r.passed
        assert any(v.rule == "unregistered_number" for v in r.violations)

    def test_unknown_placeholder_blocks(self):
        bad = VALID_REPORT.replace("{{num:rev_2025a}}", "{{num:ghost}}")
        r = gate().check(bad)
        assert not r.passed
        assert any(v.rule == "unknown_placeholder" for v in r.violations)


class TestComplianceD4:
    """§3 불변식 1 — 단일 목표주가·투자의견 금지."""

    @pytest.mark.parametrize(
        "inject",
        [
            "목표주가 68,000원을 제시한다.",
            "투자의견 Buy를 제시한다.",
            "Target Price 는 상향 여지가 있다.",
            "상승여력이 충분하다.",
            "비중 확대를 권고한다.",
        ],
    )
    def test_opinion_blocked(self, inject):
        bad = VALID_REPORT.replace("- 전방 수요 둔화", f"- 전방 수요 둔화\n\n{inject}")
        r = gate().check(bad)
        assert not r.passed, f"의견 표현이 통과했습니다: {inject!r}"
        assert any(v.rule in ("banned_opinion", "unregistered_number") for v in r.violations)


class TestComplianceAssertion:
    """§3 불변식 4 — 단정적 가치판단 표현 차단."""

    @pytest.mark.parametrize(
        "inject",
        [
            "실적은 반드시 개선된다.",
            "지금 매수해야 한다.",
            "원금 보장이 되는 구조다.",
            "주가는 확실히 오른다.",
            "실적 급등이 예상된다.",
        ],
    )
    def test_assertion_blocked(self, inject):
        bad = VALID_REPORT.replace("- 전방 수요 둔화", f"- 전방 수요 둔화\n\n{inject}")
        r = gate().check(bad)
        assert not r.passed, f"단정 표현이 통과했습니다: {inject!r}"
        assert any(v.rule == "banned_expression" for v in r.violations)


class TestSections:
    @pytest.mark.parametrize(
        "heading",
        ["## 2. 요약", "## 6. 밸류에이션", "## 7. 리스크 요인", "## 5. 실적 추정"],
    )
    def test_missing_section_blocks(self, heading):
        # 제목을 완전히 다른 문구로 바꿔야 한다 — 섹션명이 남아 있으면 정규식이 여전히 매치된다
        bad = VALID_REPORT.replace(heading, "## 기타")
        r = gate().check(bad)
        assert not r.passed
        assert any(v.rule == "missing_section" for v in r.violations)

    @pytest.mark.parametrize(
        "phrase",
        [
            "본 자료는 자본시장법상 조사분석자료가 아닙니다.",
            "매매를 권유하는 투자권유가 아니며, 투자 판단의 최종 책임은 투자자 본인에게 있습니다.",
            "AI(인공지능)를 활용하여 생성되었으며",
        ],
    )
    def test_missing_disclaimer_blocks(self, phrase):
        bad = VALID_REPORT.replace(phrase, "(삭제됨)")
        r = gate().check(bad)
        assert not r.passed
        assert any(v.rule == "missing_disclaimer" for v in r.violations)


class TestDisclaimerNotSelfFlagging:
    """디스클레이머의 '투자권유가 아니며'가 스스로를 위반으로 잡으면 안 된다."""

    def test_disclaimer_section_excluded_from_compliance_scan(self):
        r = gate().check(VALID_REPORT)
        assert not any(v.rule == "banned_opinion" for v in r.violations)
