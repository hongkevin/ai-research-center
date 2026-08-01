"""웹 표면 테스트 — 네트워크 없이 파이프라인을 가짜로 채운다.

화면이 증명해야 하는 것을 그대로 검사한다:

  1. 수치가 **출처를 달고** HTML에 나온다 (평문 치환이면 차별점이 사라진다).
  2. 게이트가 막으면 **본문을 렌더하지 않는다** (검토자가 결과로 착각한다).
  3. 생성과 발간은 다르다 — 생성은 이력에 남지 않는다.
"""

from __future__ import annotations

import datetime as dt

import pytest

from arc.data.base import Provenance
from arc.llm.number_registry import NumberEntry, NumberRegistry
from arc.render.html import render_html, substitute_with_spans

PROV = Provenance(
    source="opendart",
    retrieved_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC),
    source_ref="20260319001417",
    source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260319001417",
)


def _registry():
    reg = NumberRegistry()
    reg.register_all(
        [
            NumberEntry(
                key="revenue_2025a",
                value=536_289_118_812,
                unit="원",
                display="5,363억원",
                provenance=PROV,
                label="매출액 (2025A)",
            ),
            NumberEntry(
                key="operating_margin_2025a",
                value=40.0,
                unit="%",
                display="40.0%",
                provenance=PROV,
                label="영업이익률 (2025A)",
                formula="operating_income_2025a / revenue_2025a",
                inputs=["operating_income_2025a", "revenue_2025a"],
            ),
            NumberEntry(
                key="revenue_2026e",
                value=770_000_000_000,
                unit="원",
                display="7,704억원",
                provenance=PROV,
                label="매출액 (2026E)",
            ),
        ]
    )
    return reg


class TestNumbersCarryProvenance:
    def test_value_wrapped_with_source_attributes(self):
        out = substitute_with_spans("매출은 {{num:revenue_2025a}}이다.", _registry())
        assert 'class="num"' in out
        assert 'data-key="revenue_2025a"' in out
        assert 'data-doc="20260319001417"' in out
        assert "5,363억원" in out

    def test_formula_and_inputs_exposed(self):
        out = substitute_with_spans("{{num:operating_margin_2025a}}", _registry())
        assert "data-formula=" in out
        assert "operating_income_2025a" in out

    def test_estimate_marked_differently(self):
        """실적과 추정이 화면에서 구분돼야 한다 — 같아 보이면 독자가 섞어 읽는다."""
        out = substitute_with_spans("{{num:revenue_2026e}}", _registry())
        assert "num--estimate" in out

    def test_particle_correction_matches_plain_render(self):
        """화면과 파일의 문장이 갈라지면 안 된다."""
        reg = _registry()
        text = "영업이익률은 {{num:operating_margin_2025a}}으로 개선됐다."
        assert "40.0%로 개선됐다" in reg.render_text(text)
        assert "40.0%</span>로 개선됐다" in substitute_with_spans(text, reg)

    def test_unregistered_placeholder_left_intact(self):
        out = substitute_with_spans("{{num:nope_2025a}}", _registry())
        assert "{{num:nope_2025a}}" in out

    def test_pipe_in_formula_does_not_break_tables(self):
        """YoY 산식은 절댓값을 `|x|`로 쓴다. 표 안에서 셀 구분자로 해석되면
        뒤 셀이 통째로 이스케이프된 문자열로 렌더된다 (실측)."""
        reg = NumberRegistry()
        reg.register(
            NumberEntry(
                key="revenue_yoy_2025a",
                value=53.2,
                unit="%",
                display="53.2%",
                provenance=PROV,
                label="매출액 YoY",
                formula="(revenue_2025a - revenue_2024a) / |revenue_2024a|",
            )
        )
        md = "| 항목 | 증감률 |\n|---|---|\n| 매출액 | {{num:revenue_yoy_2025a}} |\n"
        html = render_html(md, reg)
        assert "&#124;" in html
        assert "&lt;span" not in html  # 셀이 이스케이프되지 않았다
        assert html.count("<td>") == 2

    def test_value_is_html_escaped(self):
        reg = NumberRegistry()
        reg.register(
            NumberEntry(
                key="x_2025a",
                value=1,
                unit="",
                display="<script>alert(1)</script>",
                provenance=PROV,
            )
        )
        out = substitute_with_spans("{{num:x_2025a}}", reg)
        assert "<script>" not in out
        assert "&lt;script&gt;" in out


class TestMarkdownRendering:
    def test_tables_render_and_spans_survive(self):
        md = "| 항목 | 값 |\n|---|---|\n| 매출 | {{num:revenue_2025a}} |\n"
        html = render_html(md, _registry())
        assert "<table>" in html
        assert 'class="num"' in html

    def test_headings_render(self):
        html = render_html("## 2. 요약\n\n본문.\n", _registry())
        assert "<h2>" in html


class TestViewModel:
    def _result(self, passed=True):
        """파이프라인 결과의 최소 대역 — 웹 레이어만 검사한다."""

        class _Gate:
            def __init__(self, ok):
                self.passed = ok
                self.violations = (
                    []
                    if ok
                    else [
                        type("V", (), {"rule": "unregistered_number", "line": 3, "detail": "x"})()
                    ]
                )

            def summary(self):
                return "G0 통과" if self.passed else "G0 차단 1건"

        class _R:
            symbol, fiscal_year = "214450", 2025
            company = type(
                "C", (), {"name": "(주)파마리서치", "market": type("M", (), {"value": "KOSDAQ"})()}
            )()
            statement = type("S", (), {"consolidation": type("K", (), {"value": "CFS"})()})()
            metrics = type("M", (), {"values": {"revenue": 1}, "missing_labels": ["매출원가"]})()
            registry = _registry()
            assembled = "## 2. 요약\n\n매출은 {{num:revenue_2025a}}이다.\n"
            estimates = None
            revisions: list = []
            narration = None

        r = _R()
        r.gate = _Gate(passed)
        return r

    def test_passing_gate_renders_body(self):
        from arc.web.app import _to_view

        vm = _to_view(self._result(True))
        assert vm.gate_passed
        assert 'class="num"' in vm.body_html
        assert vm.bindings

    def test_blocked_gate_renders_nothing(self):
        """차단된 초안을 보여주면 검토자가 결과로 착각한다."""
        from arc.web.app import _to_view

        vm = _to_view(self._result(False))
        assert not vm.gate_passed
        assert vm.body_html == ""
        assert vm.bindings == []
        assert vm.violations

    def test_missing_metrics_use_korean_labels(self):
        from arc.web.app import _to_view

        assert _to_view(self._result(True)).metrics_missing == ["매출원가"]


class TestAssumptionParsing:
    @pytest.mark.parametrize(
        ("raw", "want"),
        [
            ("revenue_growth=15", {"revenue_growth": 15.0}),
            ("a=1\nb=2", {"a": 1.0, "b": 2.0}),
            ("a=1, b=2", {"a": 1.0, "b": 2.0}),
            ("", {}),
            ("  \n  ", {}),
        ],
    )
    def test_parses(self, raw, want):
        from arc.web.app import _parse_overrides

        assert _parse_overrides(raw) == want

    @pytest.mark.parametrize("raw", ["revenue_growth", "revenue_growth=abc"])
    def test_bad_input_raises_not_silently_ignored(self, raw):
        """조용히 무시하면 사용자가 가정을 지정했다고 착각한 채 발간한다."""
        from arc.web.app import _parse_overrides

        with pytest.raises(ValueError):
            _parse_overrides(raw)
