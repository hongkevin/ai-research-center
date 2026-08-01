"""추정 레이어 + revision 추적 테스트.

`what-makes-a-great-research-report.md` §1이 말하듯 추정 정확도가 애널리스트
평가의 40%이고, §4는 **하향 조정 지연**이 신뢰가 무너지는 지점이라고 한다.
그래서 여기서 지키는 것은 두 가지다:

  1. 추정치는 **가정 없이 나오지 않는다** — 가정이 없으면 값도 없다.
  2. revision은 방향까지 기록된다 — 상향/하향을 구분 못 하면 지연을 잴 수 없다.
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
from arc.finmodel.estimates import (
    GROWTH_SANITY_RANGE,
    Assumption,
    EstimateSet,
    Revision,
    apply_assumptions,
    build_baseline_assumptions,
    build_estimate_entries,
    build_estimate_observations,
    build_estimates,
    compare_estimates,
    from_rows,
    to_rows,
)
from arc.finmodel.metrics import extract_metrics
from arc.llm.number_registry import NumberRegistry

PROV = Provenance(source="opendart", retrieved_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC))
TODAY = dt.date(2026, 3, 20)


def _ms(revenue=(1000, 900, 800), oi=(200, 160, 120), ni=(150, 120, 90)):
    """당기·전기·전전기. 전전기가 있어야 성장률을 두 번 관측할 수 있다."""

    def li(name, vals):
        return FinancialLineItem(
            account_name=name,
            amount=vals[0],
            prior_amount=vals[1],
            prior2_amount=vals[2] if len(vals) > 2 else None,
            statement_type="IS",
        )

    stmt = FinancialStatement(
        symbol="000000",
        fiscal_year=2025,
        period=PeriodType.ANNUAL,
        consolidation=ConsolidationType.CONSOLIDATED,
        items=[li("매출액", revenue), li("영업이익", oi), li("당기순이익", ni)],
        provenance=PROV,
    )
    return extract_metrics(stmt)


class TestBaselineAssumptions:
    def test_uses_two_year_average_when_available(self):
        """1000/900 = +11.1%, 900/800 = +12.5% → 평균 +11.8%"""
        a = build_baseline_assumptions(_ms())
        g = next(x for x in a if x.key == "revenue_growth")
        assert g.value == pytest.approx((1000 / 900 - 1 + (900 / 800 - 1)) / 2 * 100, abs=0.01)
        assert "2개년" in g.basis

    def test_falls_back_to_single_year(self):
        a = build_baseline_assumptions(_ms(revenue=(1000, 900)))
        g = next(x for x in a if x.key == "revenue_growth")
        assert g.value == pytest.approx(11.11, abs=0.01)
        assert "직전" in g.basis

    def test_margins_held_from_base_year(self):
        a = {x.key: x for x in build_baseline_assumptions(_ms())}
        assert a["operating_margin"].value == pytest.approx(20.0)
        assert a["net_margin"].value == pytest.approx(15.0)

    def test_no_revenue_no_assumption(self):
        ms = _ms()
        ms.values.pop("revenue")
        assert not [a for a in build_baseline_assumptions(ms) if a.key == "revenue_growth"]


class TestEstimatesAreAFunctionOfAssumptions:
    def test_values_derive_from_assumptions(self):
        est = build_estimates(_ms())
        g = est.assumption("revenue_growth")
        assert g is not None
        assert est.values["revenue"] == round(1000 * (1 + g.value / 100))
        assert est.values["operating_income"] == round(est.values["revenue"] * 0.20)

    def test_no_growth_assumption_no_estimate(self):
        """가정이 없으면 값도 없다. 이게 이 레이어의 전제다."""
        est = apply_assumptions(_ms(), [])
        assert not est.usable
        assert est.warnings

    def test_missing_margin_leaves_that_metric_empty(self):
        assumptions = [Assumption("revenue_growth", "매출 성장률", 10.0, "%", "가정")]
        est = apply_assumptions(_ms(), assumptions)
        assert "revenue" in est.values
        assert "operating_income" not in est.values
        assert any("operating_margin" in w for w in est.warnings)

    def test_estimate_year_is_base_plus_one(self):
        est = build_estimates(_ms())
        assert est.fiscal_year == 2026
        assert est.base_year == 2025


class TestOverrides:
    def test_override_replaces_and_is_marked(self):
        est = build_estimates(_ms(), {"revenue_growth": 5.0})
        g = est.assumption("revenue_growth")
        assert g is not None
        assert g.value == 5.0
        assert g.is_override
        assert est.values["revenue"] == 1050
        assert est.method == "사용자 지정 가정"

    def test_untouched_assumptions_keep_their_basis(self):
        est = build_estimates(_ms(), {"revenue_growth": 5.0})
        om = est.assumption("operating_margin")
        assert om is not None and not om.is_override

    def test_override_bypasses_sanity_range(self):
        """사람이 명시적으로 지정하면 극단값도 허용한다 — 판단의 주체가 다르다."""
        est = build_estimates(_ms(), {"revenue_growth": 300.0})
        assert est.usable
        assert est.values["revenue"] == 4000


class TestMechanicalExtrapolationGuards:
    def test_extreme_growth_refuses_to_extrapolate(self):
        """기계적 연장은 성장률이 극단적이면 의미를 잃는다."""
        lo, hi = GROWTH_SANITY_RANGE
        est = build_estimates(_ms(revenue=(1000, 400, 200)))  # +150%, +100%
        assert not est.usable
        assert any("극단적" in w for w in est.warnings)
        assert (1000 / 400 - 1) * 100 > hi and lo < 0

    def test_volatile_growth_warns_but_still_estimates(self):
        """진폭이 크면 경고하되 값은 낸다 — 사람이 가정을 고칠 수 있어야 한다."""
        est = build_estimates(_ms(revenue=(1000, 900, 500)))  # +11%, +80%
        assert est.usable
        assert any("진폭" in w for w in est.warnings)

    def test_stable_growth_no_warning(self):
        est = build_estimates(_ms())
        assert est.usable
        assert not est.warnings


class TestRevision:
    def _pair(self, prev_rev, cur_rev):
        a = EstimateSet(fiscal_year=2026, base_year=2025, values={"revenue": prev_rev})
        b = EstimateSet(fiscal_year=2026, base_year=2025, values={"revenue": cur_rev})
        return compare_estimates(a, b)

    def test_downward_revision_detected(self):
        """§4: 하향 조정 지연이 신뢰가 무너지는 지점이다. 방향을 못 잡으면 못 잰다."""
        (r,) = self._pair(1000, 800)
        assert r.direction == "하향"
        assert r.change_pct == pytest.approx(-20.0)

    def test_upward_revision_detected(self):
        (r,) = self._pair(1000, 1200)
        assert r.direction == "상향"

    def test_tiny_change_is_held(self):
        (r,) = self._pair(1000, 1002)
        assert r.direction == "유지"

    def test_no_prior_means_no_revision(self):
        assert compare_estimates(None, build_estimates(_ms())) == []

    def test_different_fiscal_year_not_compared(self):
        """다른 연도 추정끼리 비교하면 변화가 아니라 착시다."""
        a = EstimateSet(fiscal_year=2025, base_year=2024, values={"revenue": 1000})
        b = EstimateSet(fiscal_year=2026, base_year=2025, values={"revenue": 1200})
        assert compare_estimates(a, b) == []

    def test_metric_missing_on_one_side_skipped(self):
        a = EstimateSet(fiscal_year=2026, base_year=2025, values={"revenue": 1000})
        b = EstimateSet(
            fiscal_year=2026, base_year=2025, values={"revenue": 1100, "net_income": 100}
        )
        assert [r.metric for r in compare_estimates(a, b)] == ["revenue"]

    def test_zero_previous_does_not_divide_by_zero(self):
        (r,) = self._pair(0, 100)
        assert r.change_pct == 0.0


class TestSnapshotRoundTrip:
    def test_round_trip_preserves_values(self):
        est = build_estimates(_ms())
        rows = to_rows(est, "214450", TODAY)
        restored = from_rows(rows, "214450", est.fiscal_year)
        assert restored is not None
        assert restored.values == est.values
        assert restored.base_year == est.base_year

    def test_other_symbols_ignored(self):
        rows = to_rows(build_estimates(_ms()), "214450", TODAY)
        assert from_rows(rows, "005930", 2026) is None

    def test_other_year_ignored(self):
        rows = to_rows(build_estimates(_ms()), "214450", TODAY)
        assert from_rows(rows, "214450", 2027) is None


class TestRegistryEntries:
    def _registry(self, revisions=()):
        est = build_estimates(_ms())
        reg = NumberRegistry()
        reg.register_all(build_estimate_entries(est, list(revisions), PROV))
        return est, reg

    def test_estimate_keys_use_e_suffix(self):
        """실적(`a`)과 추정(`e`)이 키에서 구분돼야 감사 추적이 성립한다."""
        _, reg = self._registry()
        assert "revenue_2026e" in reg
        assert "revenue_2025a" not in reg

    def test_assumptions_are_registered_numbers(self):
        """가정도 수치다. 리터럴로 본문에 쓰면 G0가 막는다 (실측)."""
        _, reg = self._registry()
        assert "assume_revenue_growth_2026e" in reg
        assert "assume_operating_margin_2026e" in reg

    def test_revision_entries_include_prior_value(self):
        rev = Revision(metric="revenue", label="매출액", previous=1000, current=800)
        _, reg = self._registry([rev])
        assert "revenue_prev_2026e" in reg
        assert "revenue_revision_2026e" in reg
        assert reg.get("revenue_revision_2026e").value == pytest.approx(-20.0)

    def test_estimate_traces_back_to_actual(self):
        _, reg = self._registry()
        assert "revenue_2025a" in reg.get("revenue_2026e").inputs


class TestObservations:
    def test_no_magnitudes_leak_into_thesis(self):
        est = build_estimates(_ms())
        text = " ".join(build_estimate_observations(est, []))
        assert not NumberRegistry().find_unregistered_numbers(text)

    def test_says_baseline_not_forecast(self):
        obs = " ".join(build_estimate_observations(build_estimates(_ms()), []))
        assert "전망이 아니라" in obs

    def test_revision_direction_surfaced(self):
        rev = Revision(metric="revenue", label="매출액", previous=1000, current=800)
        obs = " ".join(build_estimate_observations(build_estimates(_ms()), [rev]))
        assert "하향" in obs

    def test_held_revision_not_narrated(self):
        rev = Revision(metric="revenue", label="매출액", previous=1000, current=1001)
        obs = " ".join(build_estimate_observations(build_estimates(_ms()), [rev]))
        assert "유지" not in obs

    def test_unusable_estimate_produces_nothing(self):
        assert build_estimate_observations(apply_assumptions(_ms(), []), []) == []
