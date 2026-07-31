"""Number Registry 적대적 테스트.

목적은 통과 확인이 아니라 **뚫리는지** 확인하는 것이다.
숫자 환각이 한 건이라도 통과하면 제품의 유일한 차별점이 무너진다.
"""

from __future__ import annotations

import datetime as dt

import pytest

from arc.data.base import Provenance
from arc.llm.number_registry import NumberEntry, NumberRegistry

PROV = Provenance(
    source="opendart",
    retrieved_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC),
    source_ref="20260801000123",
    source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260801000123",
)


def reg() -> NumberRegistry:
    r = NumberRegistry()
    r.register_all(
        [
            NumberEntry(
                key="rev_2025a", value=3_009_100, unit="억원",
                display="300조 9,100억원", provenance=PROV, label="매출액 (2025A)",
            ),
            NumberEntry(
                key="rev_yoy_2025a", value=12.3, unit="%",
                display="12.3%", provenance=PROV, label="매출 YoY (2025A)",
                formula="(rev_2025a - rev_2024a) / rev_2024a", inputs=["rev_2025a", "rev_2024a"],
            ),
            NumberEntry(
                key="op_margin_2025a", value=8.7, unit="%",
                display="8.7%", provenance=PROV, label="영업이익률 (2025A)",
                formula="op_2025a / rev_2025a",
            ),
        ]
    )
    return r


# ── 등록·조회 ───────────────────────────────────────────────────────
class TestRegister:
    def test_duplicate_key_rejected(self):
        r = reg()
        with pytest.raises(ValueError, match="중복"):
            r.register(NumberEntry(key="rev_2025a", value=1, unit="억원", provenance=PROV))

    def test_get_missing_raises(self):
        with pytest.raises(KeyError):
            reg().get("nope")

    def test_catalog_omits_values(self):
        """카탈로그에 값이 들어가면 LLM이 복사해 리터럴로 쓸 수 있다."""
        for row in reg().catalog():
            assert set(row) == {"key", "label", "unit"}
            assert "value" not in row and "display" not in row

    def test_rendered_fallback_format(self):
        e = NumberEntry(key="x", value=1234.5, unit="억원", provenance=PROV)
        assert e.rendered() == "1,234.5억원"


# ── 치환 ────────────────────────────────────────────────────────────
class TestRender:
    def test_substitution(self):
        r = reg()
        out = r.render_text("매출은 {{num:rev_2025a}}, 성장률은 {{num:rev_yoy_2025a}}입니다.")
        assert out == "매출은 300조 9,100억원, 성장률은 12.3%입니다."

    def test_whitespace_tolerated(self):
        assert reg().render_text("{{ num:op_margin_2025a }}") == "8.7%"

    def test_unknown_key_left_intact(self):
        """미등록 key는 조용히 지우지 않는다 — G0가 잡아야 한다."""
        out = reg().render_text("값은 {{num:ghost}}입니다.")
        assert "{{num:ghost}}" in out

    def test_bindings_carry_provenance(self):
        b = reg().bindings("{{num:rev_yoy_2025a}}")
        assert len(b) == 1
        assert b[0]["resolved"] == "12.3%"
        assert b[0]["formula"] is not None
        assert b[0]["provenance"]["source_ref"] == "20260801000123"

    def test_extract_keys_order(self):
        keys = NumberRegistry.extract_keys("{{num:b}} 그리고 {{num:a}}")
        assert keys == ["b", "a"]


# ── 화이트리스트: 숫자여도 허용 ─────────────────────────────────────
class TestWhitelist:
    @pytest.mark.parametrize(
        "text",
        [
            "FY2025 실적입니다.",
            "2025년 실적입니다.",
            "2025 회계연도 기준입니다.",
            "4분기에 확인됩니다.",
            "1Q26 가이던스입니다.",
            "자본시장법 제49조에 따릅니다.",
            "1. 요약\n2. 비용 구조",
            "3개 사업부로 나뉩니다.",
            "두 가지 요인이 있습니다.",
        ],
    )
    def test_allowed(self, text):
        assert reg().find_unregistered_numbers(text) == []

    def test_placeholder_internal_digits_not_flagged(self):
        """플레이스홀더 안의 2025 같은 숫자를 리터럴로 오탐하면 안 된다."""
        assert reg().find_unregistered_numbers("매출은 {{num:rev_2025a}}입니다.") == []


# ── 적대적: 반드시 잡혀야 하는 리터럴 ────────────────────────────────
class TestAdversarialLiterals:
    HIGH_CASES = [
        "매출이 약 3.2% 늘었습니다.",
        "매출은 $94.8B 입니다.",
        "시가총액 1,304억 원 수준입니다.",
        "PER 25배를 적용했습니다.",
        "영업이익률이 2.7pp 하락했습니다.",
        "매출 130,497 백만 원입니다.",
        "원가율은 82% 입니다.",
        "주가는 6,100원입니다.",
        "EPS는 143.73입니다.",
        "59.91x 를 적용합니다.",
        "영업이익 1조 2천억을 기록했습니다.",
    ]

    @pytest.mark.parametrize("text", HIGH_CASES)
    def test_caught_as_high(self, text):
        found = reg().find_unregistered_numbers(text)
        assert found, f"게이트가 뚫렸습니다: {text!r}"
        assert found[0].severity == "high", f"심각도 오분류: {text!r} -> {found[0]}"

    def test_bare_integer_is_medium(self):
        found = reg().find_unregistered_numbers("종목 15 개를 커버합니다.")
        assert found and found[0].severity == "medium"

    def test_mixed_text_flags_only_literal(self):
        r = reg()
        found = r.find_unregistered_numbers(
            "FY2025 매출은 {{num:rev_2025a}}이고, 성장률은 약 12% 입니다."
        )
        assert len(found) == 1
        assert found[0].text == "12%"

    def test_unknown_key_detected(self):
        assert reg().unknown_keys("{{num:rev_2025a}} {{num:ghost}}") == ["ghost"]
