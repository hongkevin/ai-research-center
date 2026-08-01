"""조사 교정 테스트.

전부 실측에서 나온 케이스다. LLM이 `{{num:...}}으로`라고 쓰면 치환 결과가
`40.0%으로`가 되고, 이건 한국어 리포트에서 즉시 눈에 띄는 오류다.
"""

from __future__ import annotations

import datetime as dt

import pytest

from arc.data.base import Provenance
from arc.llm.josa import replace_particle
from arc.llm.number_registry import NumberEntry, NumberRegistry

PROV = Provenance(source="opendart", retrieved_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC))


def apply(value: str, following: str) -> str:
    particle, consumed = replace_particle(value, following[:3])
    return value + particle + following[consumed:] if particle else value + following


class TestPercentIsOpenSyllable:
    """퍼센트는 '트'로 끝나 받침이 없다. 실측에서 가장 많이 틀린 자리다."""

    @pytest.mark.parametrize(
        ("following", "want"),
        [
            ("으로 개선", "40.0%로 개선"),
            ("을 웃돈다", "40.0%를 웃돈다"),
            ("이 계속", "40.0%가 계속"),
            ("과 순이익률", "40.0%와 순이익률"),
            ("은 전년", "40.0%는 전년"),
        ],
    )
    def test_corrects_to_open_form(self, following, want):
        assert apply("40.0%", following) == want

    @pytest.mark.parametrize(
        "following", ["로 개선", "를 웃돈다", "가 계속", "와 순이익", "는 전년"]
    )
    def test_already_correct_untouched(self, following):
        assert apply("40.0%", following) == "40.0%" + following


class TestWonHasFinalConsonant:
    """'원'은 ㄴ받침이라 반대로 간다."""

    @pytest.mark.parametrize(
        ("following", "want"),
        [
            ("로 증가", "5,363억원으로 증가"),
            ("를 기록", "5,363억원을 기록"),
            ("가 늘었다", "5,363억원이 늘었다"),
            ("와 비교", "5,363억원과 비교"),
            ("는 전년", "5,363억원은 전년"),
        ],
    )
    def test_corrects_to_closed_form(self, following, want):
        assert apply("5,363억원", following) == want

    def test_already_correct_untouched(self):
        assert apply("5,363억원", "으로 증가") == "5,363억원으로 증가"


class TestOtherUnits:
    @pytest.mark.parametrize(
        ("value", "following", "want"),
        [
            ("+4.0pp", "으로 개선", "+4.0pp로 개선"),  # pp = 피피
            ("25.9배", "으로", "25.9배로"),  # 배 = 받침 없음
            ("128,881명", "가 근무", "128,881명이 근무"),  # 명 = ㅇ받침
            ("6,735,612,586주", "을 발행", "6,735,612,586주를 발행"),  # 주 = 받침 없음
            ("1,668원", "를 지급", "1,668원을 지급"),
        ],
    )
    def test_unit_decides(self, value, following, want):
        assert apply(value, following) == want


class TestCopulaIsNotSubjectParticle:
    """`이`는 주격조사일 수도, 서술격조사 `이다`의 어간일 수도 있다."""

    @pytest.mark.parametrize(
        "following", ["이다", "이며 배당", "이라 한다", "이지만 개선", "이고 순이익"]
    )
    def test_copula_left_alone(self, following):
        """받침이 없어도 서술격조사는 원형이 표준이다. 고치면 오히려 나빠진다."""
        assert apply("25.9%", following) == "25.9%" + following

    def test_subject_particle_still_corrected(self):
        assert apply("70.1%", "이 계속 웃돈다") == "70.1%가 계속 웃돈다"


class TestRieulException:
    """ㄹ받침은 '으로'가 아니라 '로'를 쓴다."""

    def test_rieul_takes_ro(self):
        assert apply("1조 2,345억달러", "으로") == "1조 2,345억달러로"

    def test_non_rieul_takes_euro(self):
        assert apply("1,234만", "로") == "1,234만으로"


class TestNoFalsePositives:
    def test_space_after_value_untouched(self):
        assert apply("13.1%", " 개선됐다") == "13.1% 개선됐다"

    def test_non_particle_word_untouched(self):
        assert apply("13.1%", "까지 올랐다") == "13.1%까지 올랐다"

    def test_end_of_text_untouched(self):
        assert apply("13.1%", "") == "13.1%"

    def test_unknown_ending_untouched(self):
        """읽는 법을 모르는 끝글자는 건드리지 않는다 — 틀리게 고치느니 둔다."""
        assert apply("13.1@", "으로") == "13.1@으로"


class TestRenderTextIntegration:
    def _registry(self):
        reg = NumberRegistry()
        reg.register_all(
            [
                NumberEntry(
                    key="operating_margin_2025a",
                    value=40.0,
                    unit="%",
                    display="40.0%",
                    provenance=PROV,
                ),
                NumberEntry(
                    key="revenue_2025a",
                    value=536_289_118_812,
                    unit="원",
                    display="5,363억원",
                    provenance=PROV,
                ),
            ]
        )
        return reg

    def test_fixes_particles_during_substitution(self):
        reg = self._registry()
        out = reg.render_text(
            "영업이익률은 {{num:operating_margin_2025a}}으로 개선됐고 "
            "매출은 {{num:revenue_2025a}}로 늘었다."
        )
        assert out == "영업이익률은 40.0%로 개선됐고 매출은 5,363억원으로 늘었다."

    def test_two_placeholders_in_a_row(self):
        reg = self._registry()
        out = reg.render_text("{{num:revenue_2025a}}과 {{num:operating_margin_2025a}}과")
        assert out == "5,363억원과 40.0%와"

    def test_unregistered_key_left_intact(self):
        reg = self._registry()
        text = "값은 {{num:nope_2025a}}으로 확인된다."
        assert reg.render_text(text) == text

    def test_no_placeholder_text_unchanged(self):
        reg = self._registry()
        text = "플레이스홀더가 없는 문장으로 끝난다."
        assert reg.render_text(text) == text
