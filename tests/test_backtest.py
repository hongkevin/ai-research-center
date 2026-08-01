"""추정 백테스트 — 시점 정합성과 요약 통계.

여기서 가장 중요한 테스트는 정확도가 아니라 **시점 오염을 막는가**다.
FY(Y+1)의 정보가 추정에 섞이면 백테스트는 좋은 숫자를 내면서 거짓말을 한다.
"""

from __future__ import annotations

import pytest

from arc.finmodel.backtest import (
    BACKTEST_METRICS,
    ForecastError,
    Skipped,
    build_result,
    describe,
    evaluate,
    run,
    summarize,
)
from arc.finmodel.estimates import EstimateSet, build_estimates
from arc.finmodel.metrics import MetricSet, MetricValue


def _ms(year: int, **series: tuple[int | None, int | None, int | None]) -> MetricSet:
    """(당기, 전기, 전전기)로 MetricSet을 만든다."""
    values = {
        key: MetricValue(key=key, label=key, current=cur, prior=prior, prior2=prior2)
        for key, (cur, prior, prior2) in series.items()
    }
    return MetricSet(fiscal_year=year, values=values)


def _flat(year: int, revenue: int, op: int, net: int) -> MetricSet:
    """성장률 0%·이익률 고정인 단순한 해 — 기준선이 곧 전년과 같아진다."""
    return _ms(
        year,
        revenue=(revenue, revenue, revenue),
        operating_income=(op, op, op),
        net_income=(net, net, net),
    )


# ── 시점 정합성 ──────────────────────────────────────────────────────
def test_estimate_uses_only_the_base_year_filing():
    """기준선은 FY(Y) 보고서의 당기·전기·전전기만으로 만들어진다.
    FY(Y+1) 실적이 무엇이든 추정치가 달라지지 않아야 한다."""
    base = _ms(
        2023,
        revenue=(1000, 800, 640),
        operating_income=(100, 80, 64),
        net_income=(50, 40, 32),
    )
    est = build_estimates(base)
    for actual_revenue in (1, 10**9):
        actual = _flat(2024, actual_revenue, 1, 1)
        errors = evaluate(est, actual, "000000")
        assert isinstance(errors, list)
        rev = next(e for e in errors if e.metric == "revenue")
        assert rev.estimate == est.values["revenue"]  # 실적이 바뀌어도 추정은 그대로


def test_actual_year_must_be_the_year_after_the_base():
    """엉뚱한 해와 대조하고도 숫자가 나오면 백테스트가 거짓말을 한다."""
    est = build_estimates(_flat(2023, 1000, 100, 50))
    assert isinstance(evaluate(est, _flat(2025, 1000, 100, 50), "000000"), Skipped)
    assert isinstance(evaluate(est, _flat(2023, 1000, 100, 50), "000000"), Skipped)
    assert isinstance(evaluate(est, _flat(2024, 1000, 100, 50), "000000"), list)


def test_refusal_to_estimate_is_recorded_as_skipped_not_as_a_hit():
    """D24는 극단적 성장률에서 추정을 내지 않는다. 그걸 오차 0으로 세면
    성적이 좋아 보인다."""
    base = _ms(
        2023,
        revenue=(10_000, 1_000, 100),  # +900%
        operating_income=(1_000, 100, 10),
        net_income=(500, 50, 5),
    )
    est = build_estimates(base)
    assert not est.usable
    out = evaluate(est, _flat(2024, 10_000, 1_000, 500), "000000")
    assert isinstance(out, Skipped)
    assert out.base_year == 2023


def test_run_never_refetches_a_year_shared_by_two_pairs():
    """FY(Y+1)은 (Y,Y+1)의 실적이면서 (Y+1,Y+2)의 기준이다. 다시 받으면
    호출이 두 배가 된다."""
    calls: list[tuple[str, int]] = []

    def fetch(symbol: str, year: int) -> MetricSet:
        calls.append((symbol, year))
        return _flat(year, 1000, 100, 50)

    run(["000000"], [2021, 2022, 2023], fetch)
    assert calls == [("000000", y) for y in (2021, 2022, 2023, 2024)]


def test_missing_filing_is_counted_separately_from_a_refusal():
    """공시가 없는 해는 우리가 판단을 보류한 것이 아니다. 거부로 세면
    기준선이 실제보다 나빠 보이고, 아예 버리면 "코스닥에서 몇 곳을 다룰 수
    있나"라는 더 앞선 질문이 통계에서 사라진다."""

    def fetch(symbol: str, year: int) -> MetricSet | None:
        return None if year == 2022 else _flat(year, 1000, 100, 50)

    result = run(["000000"], [2021, 2022], fetch)
    assert result.skipped == []
    assert result.attempted == 0
    assert result.no_data == 2
    assert result.candidate_pairs == 2
    assert result.data_coverage_pct == pytest.approx(0.0)


# ── 오차 계산 ────────────────────────────────────────────────────────
def test_error_sign_marks_overestimate_as_positive():
    e = ForecastError("A", 2023, 2024, "revenue", estimate=110, actual=100)
    assert e.error_pct == pytest.approx(10.0)
    assert ForecastError("A", 2023, 2024, "revenue", 90, 100).error_pct == pytest.approx(-10.0)


def test_error_against_a_loss_uses_absolute_denominator():
    """적자를 분모로 쓸 때 부호를 살리면 방향이 뒤집힌다. 실적 -100에
    추정 -50은 **과대추정**(덜 잃을 것으로 봤다)이다."""
    e = ForecastError("A", 2023, 2024, "operating_income", estimate=-50, actual=-100)
    assert e.error_pct == pytest.approx(50.0)


def test_zero_actual_yields_no_error_rather_than_a_perfect_score():
    e = ForecastError("A", 2023, 2024, "operating_income", estimate=10, actual=0)
    assert e.error_pct is None
    assert e.abs_error_pct is None


def test_sign_accuracy_catches_a_profit_forecast_that_turned_into_a_loss():
    assert ForecastError("A", 2023, 2024, "operating_income", 10, -10).sign_correct is False
    assert ForecastError("A", 2023, 2024, "operating_income", -5, -10).sign_correct is True


# ── 요약 ─────────────────────────────────────────────────────────────
def test_median_is_reported_alongside_the_mean():
    """예측 오차는 꼬리가 두껍다. 평균만 내면 한 건의 폭발이 결론을 뒤집는다."""
    errors = [ForecastError("A", 2023, 2024, "revenue", 100 + d, 100) for d in (1, 2, 3, 4, 1000)]
    s = summarize(errors)["revenue"]
    assert s.median_ape == pytest.approx(3.0)
    assert s.mape > 200  # 평균은 폭발 한 건에 지배된다
    assert s.n == 5


def test_median_bias_survives_a_single_blowup_that_destroys_the_mean():
    """실측에서 매출 평균 편향이 +118%로 나왔는데 +20,852% 한 건이 만든
    값이었다. 대표값을 평균으로 두면 결론이 뒤집힌다."""
    errors = [ForecastError(s, 2023, 2024, "revenue", 105, 100) for s in "ABCD"]
    errors.append(ForecastError("E", 2023, 2024, "revenue", 100_000, 100))
    s = summarize(errors)["revenue"]
    assert s.median_bias == pytest.approx(5.0)
    assert s.mean_bias > 1000
    assert s.blowup_rate == pytest.approx(20.0)


def test_bias_separates_systematic_overestimation_from_noise():
    """부호를 살린 값이 편향이다. 같은 오차 크기라도 편향이 있으면 기준선을
    읽는 법이 달라진다."""
    noisy = [
        ForecastError("A", 2023, 2024, "revenue", 110, 100),
        ForecastError("B", 2023, 2024, "revenue", 90, 100),
    ]
    biased = [
        ForecastError("A", 2023, 2024, "revenue", 110, 100),
        ForecastError("B", 2023, 2024, "revenue", 110, 100),
    ]
    assert summarize(noisy)["revenue"].mean_bias == pytest.approx(0.0)
    assert summarize(biased)["revenue"].mean_bias == pytest.approx(10.0)
    assert summarize(noisy)["revenue"].over_rate == pytest.approx(50.0)
    assert summarize(biased)["revenue"].over_rate == pytest.approx(100.0)
    assert summarize(noisy)["revenue"].mape == summarize(biased)["revenue"].mape


def test_profit_error_is_also_scaled_by_revenue():
    """영업이익 5,700만원인 회사에 −63억을 추정하면 상대오차가 −11,204%다.
    크다는 것 말고 읽을 게 없다. 매출로 나누면 해석된다."""
    e = ForecastError(
        "A",
        2023,
        2024,
        "operating_income",
        -6_337_600_350,
        57_074_357,
        actual_revenue=10_000_000_000,
    )
    assert e.error_pct < -11_000
    assert e.error_of_revenue_pct == pytest.approx(-63.9, abs=0.1)
    s = summarize([e])["operating_income"]
    assert s.median_of_revenue == pytest.approx(63.9, abs=0.1)


def test_revenue_is_not_scaled_by_itself():
    """매출 오차를 매출로 나누면 같은 값이라 정보가 없다."""
    e = ForecastError("A", 2023, 2024, "revenue", 110, 100, actual_revenue=100)
    assert summarize([e])["revenue"].median_of_revenue is None


def test_hit_rate_counts_both_bands():
    errors = [ForecastError("A", 2023, 2024, "revenue", 100 + d, 100) for d in (5, 15, 50)]
    s = summarize(errors)["revenue"]
    assert s.hit_rates[10.0] == pytest.approx(100 / 3)
    assert s.hit_rates[20.0] == pytest.approx(200 / 3)


def test_empty_sample_summarises_without_crashing():
    s = summarize([])["revenue"]
    assert s.n == 0 and s.mape is None
    assert "표본 없음" in s.describe()


# ── 커버리지 ─────────────────────────────────────────────────────────
def test_coverage_counts_pairs_not_metrics():
    """한 쌍이 지표 3개를 내므로, 오차 건수로 커버리지를 재면 3배로 부풀려진다."""
    ok = evaluate(build_estimates(_flat(2023, 1000, 100, 50)), _flat(2024, 1000, 100, 50), "A")
    result = build_result([ok, Skipped("B", 2023, "산출하지 않았다.")])
    assert len(result.errors) == 3
    assert result.attempted == 2
    assert result.coverage_pct == pytest.approx(50.0)


def test_describe_leads_with_coverage():
    """MAPE만 말하고 커버리지를 빼면 절반만 말하는 것이다."""
    ok = evaluate(build_estimates(_flat(2023, 1000, 100, 50)), _flat(2024, 1000, 100, 50), "A")
    lines = describe(build_result([ok, Skipped("B", 2023, "x")]))
    assert "커버리지" in lines[0]
    assert len(lines) == 1 + len(BACKTEST_METRICS)


def test_a_flat_company_is_forecast_exactly():
    """성장률 0%·이익률 유지면 기계적 연장은 정확해야 한다. 이게 틀리면
    파이프라인 어딘가가 틀린 것이다."""
    ok = evaluate(build_estimates(_flat(2023, 1000, 100, 50)), _flat(2024, 1000, 100, 50), "A")
    assert isinstance(ok, list)
    assert all(e.abs_error_pct == pytest.approx(0.0) for e in ok)


def test_estimate_set_without_values_is_skipped():
    est = EstimateSet(fiscal_year=2024, base_year=2023)
    assert isinstance(evaluate(est, _flat(2024, 1, 1, 1), "A"), Skipped)
