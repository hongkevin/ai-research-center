"""분석 렌즈 — 같은 숫자에 다른 질문 (D35).

여기서 가장 중요한 테스트는 "렌즈가 말하는가"가 아니라 **"근거 없이 말하지
않는가"**다. 섹션을 채우려고 확인되지 않은 판단을 쓰는 것이 이 제품이
피하려는 것 그 자체다.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest

from arc.data.base import Provenance
from arc.finmodel.lenses import (
    CAPITAL_RETURN,
    DURABILITY,
    LensReading,
    LensView,
    build_lens_entries,
    build_lens_observations,
    build_lenses,
    find_tensions,
)
from arc.finmodel.metrics import MetricSet, MetricValue, build_margin_bridge
from arc.finmodel.segment_profit import SegmentProfitLine, SegmentProfitSet
from arc.finmodel.valuation import ValuationSet

PROV = Provenance(
    source="DART",
    source_url="https://opendart.fss.or.kr",
    retrieved_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC),
)


def _ms(**series: tuple[int | None, int | None]) -> MetricSet:
    values = {
        key: MetricValue(key=key, label=key, current=cur, prior=prior)
        for key, (cur, prior) in series.items()
    }
    return MetricSet(fiscal_year=2025, values=values)


def _sp(*lines: SegmentProfitLine) -> SegmentProfitSet:
    return SegmentProfitSet(fiscal_year=2025, lines=list(lines), reconciled=True)


def _seg(name, revenue, op, assets=None, dep=None, rev_prior=None, op_prior=None):
    return SegmentProfitLine(
        name=name,
        revenue=revenue,
        operating_income=op,
        assets=assets,
        depreciation=dep,
        revenue_prior=rev_prior,
        op_prior=op_prior,
    )


# ── 근거가 없으면 침묵한다 ───────────────────────────────────────────
def test_capital_lens_is_silent_without_segment_assets():
    """부문 자산이 없으면 「자본이 어디에 묶여 있는가」에 답할 수 없다.
    전사 자산총계로는 이 질문에 답이 안 나온다."""
    sp = _sp(_seg("A", 1000, 100), _seg("B", 1000, 100))  # assets=None
    lenses = build_lenses(_ms(), segment_profit=sp)
    view = lenses.view(CAPITAL_RETURN)
    assert not view.usable
    assert "부문 자산이 공시되지 않아" in view.silent_reason


def test_durability_lens_is_silent_without_a_margin_bridge():
    lenses = build_lenses(_ms(revenue=(1000, 900)))
    view = lenses.view(DURABILITY)
    assert not view.usable
    assert view.silent_reason


def test_silent_lenses_produce_no_observations_and_no_tension():
    lenses = build_lenses(_ms())
    assert build_lens_observations(lenses) == []
    assert lenses.tensions == []


# ── 부정이 없다는 것은 긍정이 아니다 ─────────────────────────────────
def test_a_neutral_only_lens_has_no_stance():
    """삼성전자는 부문 자산을 아예 공시하지 않아 자본수익률 렌즈가 확인한 게
    없다. 이걸 「긍정」으로 세면 "자본은 버는 곳에 놓여 있다"는 근거 없는
    문장이 리포트에 실린다 — 실제로 그렇게 나왔던 자리다."""
    cap = LensView(key=CAPITAL_RETURN, label="자본수익률", question="q")
    cap.readings.append(
        LensReading(lens=CAPITAL_RETURN, claim="상각 부담이 다르다", stance="neutral")
    )
    dur = LensView(key=DURABILITY, label="재현성", question="q")
    dur.readings.append(LensReading(lens=DURABILITY, claim="원가가 주도했다", stance="negative"))
    assert find_tensions([cap, dur]) == []


def test_tension_needs_both_lenses_to_take_a_stance():
    cap = LensView(key=CAPITAL_RETURN, label="자본수익률", question="q")
    cap.readings.append(LensReading(lens=CAPITAL_RETURN, claim="x", stance="negative"))
    dur = LensView(key=DURABILITY, label="재현성", question="q")  # 판독 없음
    assert find_tensions([cap, dur]) == []


def test_no_tension_when_both_lenses_agree_positively():
    """두 렌즈가 나란히 긍정이면 관전 포인트에 보탤 게 없다."""
    views = []
    for key in (CAPITAL_RETURN, DURABILITY):
        v = LensView(key=key, label=key, question="q")
        v.readings.append(LensReading(lens=key, claim="x", stance="positive"))
        views.append(v)
    assert find_tensions(views) == []


def test_opposing_stances_produce_a_watchpoint():
    cap = LensView(key=CAPITAL_RETURN, label="자본수익률", question="q")
    cap.readings.append(LensReading(lens=CAPITAL_RETURN, claim="x", stance="negative"))
    dur = LensView(key=DURABILITY, label="재현성", question="q")
    dur.readings.append(LensReading(lens=DURABILITY, claim="y", stance="positive"))
    tensions = find_tensions([cap, dur])
    assert len(tensions) == 1
    assert "적자 부문의 자산이 줄어드는지" in tensions[0].text


# ── 적자에서 부등호가 뒤집히는 자리 ──────────────────────────────────
def test_leverage_reading_does_not_fire_on_a_loss():
    """손실 상태에서 레버리지가 높으면 ROE는 ROA보다 **더 낮아진다**(더 큰 음수).
    부호를 안 보면 롯데케미칼에서 반대로 말하게 된다 — 실제로 그랬다."""
    v = ValuationSet(fiscal_year=2025, roe=-3.0, roa=-10.0)
    lenses = build_lenses(_ms(), valuation=v)
    claims = " ".join(r.claim for r in lenses.view(CAPITAL_RETURN).readings)
    assert "부채 사용에서 온다" not in claims


def test_leverage_reading_fires_when_both_returns_are_positive():
    v = ValuationSet(fiscal_year=2025, roe=20.0, roa=5.0)
    lenses = build_lenses(_ms(), valuation=v)
    claims = " ".join(r.claim for r in lenses.view(CAPITAL_RETURN).readings)
    assert "부채 사용에서 온다" in claims


# ── 자본수익률 렌즈 ──────────────────────────────────────────────────
def test_trapped_assets_are_flagged_when_capital_sits_in_a_loss_making_segment():
    """롯데케미칼: 자산 31.1조 중 24.0조가 영업적자 부문에 묶여 있다.
    「싸 보이는 이유가 자산이 안 벌기 때문」 — 전사 지표로는 안 나오는 논지다."""
    sp = _sp(
        _seg("기초화학사업부", 12_480, -857, assets=24_048),
        _seg("첨단소재사업부", 5_086, 123, assets=5_799),
    )
    lenses = build_lenses(_ms(), segment_profit=sp)
    view = lenses.view(CAPITAL_RETURN)
    assert "negative" in view.stances
    assert "기초화학사업부" in view.readings[0].claim


def test_assets_sitting_in_profitable_segments_reads_positive():
    sp = _sp(
        _seg("A", 1000, 100, assets=900),
        _seg("B", 1000, 50, assets=800),
    )
    view = build_lenses(_ms(), segment_profit=sp).view(CAPITAL_RETURN)
    assert "positive" in view.stances


def test_trapped_share_is_measured_against_segments_with_known_assets():
    """자산이 확인된 부문만 분모에 넣는다. LG전자처럼 일부 부문의 자산이
    짝지어지지 않으면 전체 자산으로 나눠 비중이 낮게 나온다."""
    sp = _sp(
        _seg("적자", 100, -10, assets=600),
        _seg("흑자", 100, 10, assets=400),
        _seg("자산모름", 100, 10, assets=None),
    )
    lenses = build_lenses(_ms(), segment_profit=sp)
    entry = next(
        e
        for e in build_lens_entries(lenses, sp, PROV, 2025)
        if e.key == "trapped_asset_share_2025a"
    )
    assert entry.value == pytest.approx(60.0)


# ── 재현성 렌즈 ──────────────────────────────────────────────────────
def _bridge_ms(cost_delta: int, sga_delta: int) -> MetricSet:
    """원가·판관비 중 어느 쪽이 마진을 움직였는지 만들어 낸다."""
    return _ms(
        revenue=(1000, 1000),
        cost_of_sales=(600 + cost_delta, 600),
        sga=(300 + sga_delta, 300),
        operating_income=(100 - cost_delta - sga_delta, 100),
    )


def test_cost_driven_margin_change_reads_as_less_repeatable():
    """원가는 원재료·환율에 좌우된다. 같은 마진 개선이라도 판관비에서 온 것과
    재현성이 다르다."""
    ms = _bridge_ms(cost_delta=-50, sga_delta=0)
    view = build_lenses(ms, bridge=build_margin_bridge(ms)).view(DURABILITY)
    assert "negative" in view.stances
    assert "원가율" in view.readings[0].claim


def test_sga_driven_margin_change_reads_as_more_controllable():
    ms = _bridge_ms(cost_delta=0, sga_delta=-50)
    view = build_lenses(ms, bridge=build_margin_bridge(ms)).view(DURABILITY)
    assert "positive" in view.stances
    assert "판관비율" in view.readings[0].claim


def test_diverging_segment_margins_block_a_company_wide_explanation():
    sp = _sp(
        _seg("A", 1000, 100, rev_prior=1000, op_prior=50),  # 개선
        _seg("B", 1000, 50, rev_prior=1000, op_prior=100),  # 악화
    )
    view = build_lenses(_ms(), segment_profit=sp).view(DURABILITY)
    assert any("방향이 갈렸다" in r.claim for r in view.readings)


def test_operating_and_net_income_moving_apart_is_flagged():
    ms = _ms(operating_income=(80, 100), net_income=(120, 100))
    view = build_lenses(ms).view(DURABILITY)
    assert any("영업외손익" in r.claim for r in view.readings)


# ── 불변식 ───────────────────────────────────────────────────────────
def test_observations_carry_no_magnitudes():
    """프롬프트에 들어간 숫자는 LLM이 리터럴로 베낀다 (D16)."""
    sp = _sp(
        _seg("기초화학", 12_480, -857, assets=24_048, dep=703, rev_prior=13_306, op_prior=-851),
        _seg("첨단소재", 5_086, 123, assets=5_799, dep=164, rev_prior=5_471, op_prior=135),
    )
    ms = _bridge_ms(cost_delta=-50, sga_delta=0)
    lenses = build_lenses(ms, bridge=build_margin_bridge(ms), segment_profit=sp)
    for text in build_lens_observations(lenses):
        assert not re.search(r"\d", text), text


def test_every_lens_number_is_registered():
    """렌즈가 만든 숫자라고 예외가 아니다."""
    sp = _sp(_seg("적자", 100, -10, assets=600), _seg("흑자", 100, 10, assets=400))
    lenses = build_lenses(_ms(), segment_profit=sp)
    keys = {e.key for e in build_lens_entries(lenses, sp, PROV, 2025)}
    assert keys == {"trapped_asset_2025a", "trapped_asset_share_2025a"}


def test_no_entries_without_segment_assets():
    sp = _sp(_seg("A", 100, 10), _seg("B", 100, 10))
    lenses = build_lenses(_ms(), segment_profit=sp)
    assert build_lens_entries(lenses, sp, PROV, 2025) == []
