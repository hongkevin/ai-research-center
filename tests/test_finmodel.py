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
    build_margin_bridge,
    build_observations,
    extract_metrics,
    fmt_krw,
    fmt_pct,
    margin,
    yoy,
)
from arc.llm.number_registry import NumberRegistry

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
        account_id=account_id,
        account_name=name,
        amount=amount,
        prior_amount=prior,
        statement_type=sj,
    )


# ── 계정과목 매핑 ────────────────────────────────────────────────────
class TestAccountMapping:
    def test_by_account_id_preferred(self):
        s = stmt(
            [
                li("아무거나", 100, 90, account_id="ifrs-full_Revenue"),
                li("매출액", 999, 888),  # 이름 매칭 후보지만 id 매칭이 이긴다
            ]
        )
        m = extract_metrics(s)
        assert m.values["revenue"].current == 100
        assert m.values["revenue"].matched_by == "account_id"

    @pytest.mark.parametrize("name", ["매출액", "수익(매출액)", "영업수익", "매출", "수익"])
    def test_revenue_name_variants(self, name):
        m = extract_metrics(stmt([li(name, 500, 400)]))
        assert m.values["revenue"].current == 500
        assert m.values["revenue"].matched_by == "account_name"

    @pytest.mark.parametrize("name", ["영업이익", "영업이익(손실)", "영업손실"])
    def test_operating_income_variants(self, name):
        m = extract_metrics(stmt([li(name, 50, 40)]))
        assert m.values["operating_income"].current == 50

    @pytest.mark.parametrize("name", ["당기순이익", "당기순이익(손실)", "연결당기순이익"])
    def test_net_income_variants(self, name):
        m = extract_metrics(stmt([li(name, 30, 20)]))
        assert m.values["net_income"].current == 30

    def test_whitespace_normalized(self):
        m = extract_metrics(stmt([li("영업 이익", 50, 40)]))
        assert m.values["operating_income"].current == 50

    def test_is_preferred_over_cis(self):
        s = stmt(
            [
                li("매출액", 200, 180, sj="CIS"),
                li("매출액", 100, 90, sj="IS"),
            ]
        )
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
        s = stmt(
            [
                li("매출액", 1_000_000_000_000, 900_000_000_000),
                li("영업이익", 100_000_000_000, 81_000_000_000),
            ]
        )
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

        s = stmt(
            [
                li("매출액", 1_000_000_000_000, 900_000_000_000),
                li("영업이익", 100_000_000_000, 81_000_000_000),
                li("당기순이익", 70_000_000_000, 60_000_000_000),
            ]
        )
        r = NumberRegistry()
        r.register_all(build_entries(extract_metrics(s), PROV))  # 중복이면 ValueError
        assert len(r) > 8


# ── 마진 브리지 ──────────────────────────────────────────────────────
def _bridge_stmt(rev, cos, sga, oi, rev_p, cos_p, sga_p, oi_p):
    return stmt(
        [
            li("매출액", rev, rev_p),
            li("매출원가", cos, cos_p),
            li("판매비와관리비", sga, sga_p),
            li("영업이익", oi, oi_p),
        ]
    )


class TestMarginBridge:
    def test_identity_closes_exactly(self):
        """영업이익 = 매출 - 원가 - 판관비인 회사는 잔차가 0이어야 한다.

        이건 근사가 아니라 항등식이다. 잔차가 생기면 산식이 틀린 것이다.
        """
        b = build_margin_bridge(
            extract_metrics(_bridge_stmt(1000, 600, 200, 200, 900, 570, 190, 140))
        )
        assert b is not None
        assert b.reconciled
        assert abs(b.residual) < 1e-9
        # 20.0% - 15.56% = +4.44pp
        assert b.margin_change == pytest.approx(4.444, abs=0.01)
        assert b.cost_contribution + b.sga_contribution == pytest.approx(b.margin_change)

    def test_cost_ratio_fall_contributes_positively(self):
        """비용 비율이 내려가면 마진에 **플러스**로 기여한다 (부호 반전)."""
        b = build_margin_bridge(
            extract_metrics(_bridge_stmt(1000, 600, 200, 200, 1000, 650, 200, 150))
        )
        assert b is not None
        assert b.cost_contribution > 0  # 원가율 65% → 60%
        assert b.sga_contribution == pytest.approx(0.0)
        assert b.dominant == "원가율"

    def test_dominant_picks_larger_absolute_contribution(self):
        b = build_margin_bridge(
            extract_metrics(_bridge_stmt(1000, 600, 150, 250, 1000, 610, 200, 190))
        )
        assert b is not None
        assert b.dominant == "판관비율"  # 판관비 -5.0pp vs 원가 -1.0pp

    def test_unreconciled_flagged_not_hidden(self):
        """영업이익이 매출-원가-판관비와 다르면 통과시키지 않고 표시한다."""
        b = build_margin_bridge(
            extract_metrics(_bridge_stmt(1000, 600, 200, 100, 1000, 600, 200, 200))
        )
        assert b is not None
        assert not b.reconciled
        assert abs(b.residual) > 0.15

    @pytest.mark.parametrize("drop", ["매출액", "매출원가", "판매비와관리비", "영업이익"])
    def test_missing_input_returns_none_never_estimates(self, drop):
        items = [
            li("매출액", 1000, 900),
            li("매출원가", 600, 570),
            li("판매비와관리비", 200, 190),
            li("영업이익", 200, 140),
        ]
        items = [i for i in items if i.account_name != drop]
        assert build_margin_bridge(extract_metrics(stmt(items))) is None

    def test_missing_prior_returns_none(self):
        """전기가 없으면 변화를 계산할 수 없다. 0으로 두지 않는다."""
        s = _bridge_stmt(1000, 600, 200, 200, None, None, None, None)
        assert build_margin_bridge(extract_metrics(s)) is None

    def test_residual_is_internal_not_in_catalog(self):
        """검산값은 감사용이다. 카탈로그에 두면 LLM이 본문에 QA 문장을 쓴다."""
        ms = extract_metrics(_bridge_stmt(1000, 600, 200, 200, 900, 570, 190, 140))
        reg = NumberRegistry()
        reg.register_all(build_entries(ms, PROV))
        assert "bridge_residual_2025a" in reg  # 치환·감사에는 남아 있고
        keys = {r["key"] for r in reg.catalog()}
        assert "bridge_residual_2025a" not in keys  # 카탈로그에는 없다
        assert "bridge_cost_contrib_2025a" in keys


# ── 관찰(논지) ───────────────────────────────────────────────────────
class TestObservations:
    def test_no_numeric_magnitudes_leak_into_thesis(self):
        """관찰문은 프롬프트에 그대로 들어간다. 크기가 있으면 LLM이 베낀다."""
        ms = extract_metrics(_bridge_stmt(1000, 600, 200, 200, 900, 570, 190, 140))
        text = " ".join(build_observations(ms, build_margin_bridge(ms)))
        reg = NumberRegistry()
        assert not reg.find_unregistered_numbers(text)

    def test_operating_leverage_detected(self):
        ms = extract_metrics(_bridge_stmt(1000, 600, 200, 200, 900, 570, 190, 140))
        obs = " ".join(build_observations(ms, build_margin_bridge(ms)))
        assert "운영 레버리지" in obs

    def test_cost_growth_outpacing_revenue_detected(self):
        ms = extract_metrics(_bridge_stmt(1000, 700, 200, 100, 900, 600, 190, 110))
        obs = " ".join(build_observations(ms, build_margin_bridge(ms)))
        assert "비용이 외형보다 빨리" in obs

    def test_unreconciled_bridge_does_not_assert_dominance(self):
        """검산이 안 맞으면 '어느 쪽이 주도했다'고 말하면 안 된다."""
        ms = extract_metrics(_bridge_stmt(1000, 600, 200, 100, 1000, 600, 200, 200))
        obs = " ".join(build_observations(ms, build_margin_bridge(ms)))
        assert "단정하지 않는다" in obs
        assert "기여가 더 큰 쪽은" not in obs

    def test_missing_metrics_told_not_to_mention(self):
        ms = extract_metrics(stmt([li("매출액", 1000, 900), li("영업이익", 200, 140)]))
        obs = " ".join(build_observations(ms, None))
        assert "확인되지 않은 계정" in obs

    def test_no_bridge_still_produces_observations(self):
        ms = extract_metrics(stmt([li("매출액", 1000, 900), li("영업이익", 200, 140)]))
        assert build_observations(ms, None)
