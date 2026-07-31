"""finmodel 계산 레이어 테스트.

계정과목 매핑이 최대 리스크(ARCHITECTURE.md §5.1)이므로 실제 DART 응답에서
관측되는 변형들을 고정한다. 못 찾은 지표를 추정으로 채우지 않는 것도 검증한다.
"""

from __future__ import annotations

import datetime as dt

import pytest

from arc.data.base import (
    ConsolidationType,
    FinancialLineItem,
    FinancialStatement,
    PeriodType,
    Provenance,
)
from arc.finmodel.metrics import (
    build_entries,
    extract_metrics,
    fmt_krw,
    fmt_pct,
    margin,
    yoy,
)

PROV = Provenance(source="opendart", retrieved_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC))


def stmt(items: list[FinancialLineItem], year: int = 2025) -> FinancialStatement:
    return FinancialStatement(
        symbol="000000",
        fiscal_year=year,
        period=PeriodType.ANNUAL,
        consolidation=ConsolidationType.CONSOLIDATED,
        items=items,
        provenance=PROV,
    )


def li(name, amount, prior=None, account_id=None, sj="IS"):
    return FinancialLineItem(
        account_id=account_id, account_name=name,
        amount=amount, prior_amount=prior, statement_type=sj,
    )


# ── 계정과목 매핑 ────────────────────────────────────────────────────
class TestAccountMapping:
    def test_by_account_id_preferred(self):
        s = stmt([
            li("아무거나", 100, 90, account_id="ifrs-full_Revenue"),
            li("매출액", 999, 888),  # 이름 매칭 후보지만 id 매칭이 이긴다
        ])
        m = extract_metrics(s)
        assert m.values["revenue"].current == 100
        assert m.values["revenue"].matched_by == "account_id"

    @pytest.mark.parametrize(
        "name", ["매출액", "수익(매출액)", "영업수익", "매출", "수익"]
    )
    def test_revenue_name_variants(self, name):
        m = extract_metrics(stmt([li(name, 500, 400)]))
        assert m.values["revenue"].current == 500
        assert m.values["revenue"].matched_by == "account_name"

    @pytest.mark.parametrize("name", ["영업이익", "영업이익(손실)", "영업손실"])
    def test_operating_income_variants(self, name):
        m = extract_metrics(stmt([li(name, 50, 40)]))
        assert m.values["operating_income"].current == 50

    @pytest.mark.parametrize(
        "name", ["당기순이익", "당기순이익(손실)", "연결당기순이익"]
    )
    def test_net_income_variants(self, name):
        m = extract_metrics(stmt([li(name, 30, 20)]))
        assert m.values["net_income"].current == 30

    def test_whitespace_normalized(self):
        m = extract_metrics(stmt([li("영업 이익", 50, 40)]))
        assert m.values["operating_income"].current == 50

    def test_is_preferred_over_cis(self):
        s = stmt([
            li("매출액", 200, 180, sj="CIS"),
            li("매출액", 100, 90, sj="IS"),
        ])
        assert extract_metrics(s).values["revenue"].current == 100

    def test_missing_metric_not_fabricated(self):
        """못 찾은 지표는 추정하지 않고 missing에 남긴다."""
        m = extract_metrics(stmt([li("매출액", 100, 90)]))
        assert "operating_income" in m.missing
        assert "operating_income" not in m.values
        assert m.get("operating_income") is None
        assert not m.coverage_ok

    def test_coverage_ok_requires_revenue_and_op(self):
        m = extract_metrics(stmt([li("매출액", 100, 90), li("영업이익", 10, 8)]))
        assert m.coverage_ok

    def test_null_amount_ignored(self):
        m = extract_metrics(stmt([li("매출액", None, None), li("영업수익", 300, 250)]))
        assert m.values["revenue"].current == 300


# ── 파생 계산 ────────────────────────────────────────────────────────
class TestDerived:
    def test_yoy(self):
        assert yoy(110, 100) == pytest.approx(10.0)
        assert yoy(90, 100) == pytest.approx(-10.0)

    def test_yoy_from_negative_base_uses_abs(self):
        """적자 → 흑자. 부호가 뒤집혀도 크기 기준으로 계산한다."""
        assert yoy(50, -100) == pytest.approx(150.0)

    @pytest.mark.parametrize("cur,pri", [(None, 100), (100, None), (100, 0)])
    def test_yoy_undefined(self, cur, pri):
        assert yoy(cur, pri) is None

    def test_margin(self):
        assert margin(15, 100) == pytest.approx(15.0)

    @pytest.mark.parametrize("num,den", [(None, 100), (10, None), (10, 0)])
    def test_margin_undefined(self, num, den):
        assert margin(num, den) is None


# ── 표시 포맷 ────────────────────────────────────────────────────────
class TestFormat:
    @pytest.mark.parametrize(
        "amount,expected",
        [
            (1_234_500_000_000, "1조 2,345억원"),
            (300_910_000_000_000, "300조 9,100억원"),  # 삼성전자 매출 규모
            (52_300_000_000, "523억원"),
            (-1_500_000_000, "-15억원"),
            (100_000_000, "1억원"),
        ],
    )
    def test_krw(self, amount, expected):
        assert fmt_krw(amount) == expected

    def test_exact_jo_omits_remainder(self):
        assert fmt_krw(2 * 10_000 * 100_000_000) == "2조원"

    def test_none_passthrough(self):
        assert fmt_krw(None) is None and fmt_pct(None) is None

    def test_pct(self):
        assert fmt_pct(12.34) == "12.3%"


# ── Registry 항목 생성 ───────────────────────────────────────────────
class TestBuildEntries:
    def _entries(self):
        s = stmt([
            li("매출액", 1_000_000_000_000, 900_000_000_000),
            li("영업이익", 100_000_000_000, 81_000_000_000),
        ])
        return {e.key: e for e in build_entries(extract_metrics(s), PROV)}

    def test_keys_follow_convention(self):
        e = self._entries()
        assert "revenue_2025a" in e
        assert "revenue_2024a" in e
        assert "revenue_yoy_2025a" in e
        assert "operating_margin_2025a" in e
        assert "operating_margin_chg_2025a" in e

    def test_values_correct(self):
        e = self._entries()
        assert e["revenue_yoy_2025a"].display == "11.1%"
        assert e["operating_margin_2025a"].display == "10.0%"
        assert e["operating_margin_2024a"].display == "9.0%"
        assert e["operating_margin_chg_2025a"].display == "+1.0pp"

    def test_formula_and_inputs_preserved(self):
        e = self._entries()["revenue_yoy_2025a"]
        assert e.formula is not None
        assert set(e.inputs) == {"revenue_2025a", "revenue_2024a"}

    def test_provenance_attached(self):
        assert all(e.provenance.source == "opendart" for e in self._entries().values())

    def test_missing_metric_produces_no_entry(self):
        s = stmt([li("매출액", 100_000_000_000, 90_000_000_000)])
        keys = {e.key for e in build_entries(extract_metrics(s), PROV)}
        assert not any("operating" in k for k in keys)

    def test_entries_registrable_without_duplicate(self):
        """build_entries 결과에 key 중복이 없어야 레지스트리에 그대로 넣을 수 있다."""
        from arc.llm.number_registry import NumberRegistry

        s = stmt([
            li("매출액", 1_000_000_000_000, 900_000_000_000),
            li("영업이익", 100_000_000_000, 81_000_000_000),
            li("당기순이익", 70_000_000_000, 60_000_000_000),
        ])
        r = NumberRegistry()
        r.register_all(build_entries(extract_metrics(s), PROV))  # 중복이면 ValueError
        assert len(r) > 8
