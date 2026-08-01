"""분석 렌즈 — 같은 숫자에 다른 질문 (D35).

여기서 가장 중요한 테스트는 "렌즈가 말하는가"가 아니라 두 가지다:

1. **근거 없이 말하지 않는가** — 섹션을 채우려고 확인되지 않은 판단을 쓰는 것이
   이 제품이 피하려는 것 그 자체다.
2. **부차적 관찰이 주된 발견을 뒤집지 않는가** — 초판이 여기서 틀렸다. LG전자에서
   렌즈의 1순위 발견과 정반대 문장이 리포트에 실렸다.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest

from arc.data.base import Provenance
from arc.finmodel.lenses import (
    ADVERSE,
    CAPITAL_RETURN,
    DURABILITY,
    NEUTRAL,
    SUPPORTIVE,
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


def _bridge_ms(cost_delta: int, sga_delta: int) -> MetricSet:
    """원가·판관비 중 어느 쪽이 마진을 움직였는지 만들어 낸다."""
    return _ms(
        revenue=(1000, 1000),
        cost_of_sales=(600 + cost_delta, 600),
        sga=(300 + sga_delta, 300),
        operating_income=(100 - cost_delta - sga_delta, 100),
    )


def _view(key: str, *readings: LensReading, watch: str = "") -> LensView:
    v = LensView(key=key, label=key, question="q")
    v.readings.extend(readings)
    v.watch = watch
    return v


# ── 주된 발견은 사슬의 앞이 정한다 ───────────────────────────────────
def test_headline_is_the_earliest_answered_step_not_the_first_appended():
    """초판은 판독 순서가 코드 작성 순서였다. 설계가 아니라 사고였다."""
    v = _view(
        CAPITAL_RETURN,
        LensReading(step=3, claim="레버리지", direction=ADVERSE),
        LensReading(step=1, claim="자산이 버는 곳에", direction=SUPPORTIVE),
    )
    assert v.headline.step == 1
    assert v.verdict is SUPPORTIVE


def test_a_later_opposing_reading_becomes_a_caveat_not_the_verdict():
    """LG전자에서 실제로 틀렸던 자리 — 주된 발견은 「자산이 흑자 부문에 있다」인데
    부차적인 레버리지 관찰 하나 때문에 렌즈 전체가 부정으로 접혔다."""
    v = _view(
        CAPITAL_RETURN,
        LensReading(step=1, claim="자산이 버는 곳에", direction=SUPPORTIVE),
        LensReading(step=3, claim="수익률이 부채에서 온다", direction=ADVERSE),
    )
    assert v.verdict is SUPPORTIVE
    assert [c.claim for c in v.caveats] == ["수익률이 부채에서 온다"]


def test_a_later_reading_in_the_same_direction_is_not_a_caveat():
    v = _view(
        DURABILITY,
        LensReading(step=2, claim="원가가 주도", direction=ADVERSE),
        LensReading(step=4, claim="영업외 요인", direction=ADVERSE),
    )
    assert v.caveats == []


def test_neutral_readings_never_become_the_headline():
    v = _view(CAPITAL_RETURN, LensReading(step=1, claim="맥락", direction=NEUTRAL))
    assert v.headline is None
    assert v.verdict is None


# ── 근거가 없으면 침묵한다 ───────────────────────────────────────────
def test_capital_lens_is_silent_without_segment_assets():
    """부문 자산이 없으면 「자본이 어디에 있는가」에 답할 수 없다.
    전사 자산총계로는 이 질문에 답이 안 나온다."""
    sp = _sp(_seg("A", 1000, 100), _seg("B", 1000, 100))
    view = build_lenses(_ms(), segment_profit=sp).view(CAPITAL_RETURN)
    assert not view.usable
    assert "부문 자산이 공시되지 않아" in view.silent_reason


def test_unanswered_chain_steps_are_reported():
    """「부문 자산을 공시하지 않는다」는 것 자체가 회사에 대한 사실이다.
    삼성전자가 여기 해당한다 — 감가상각 격차는 말할 수 있어도 1순위 질문에는
    답하지 못한다."""
    sp = _sp(
        _seg("A", 1000, 100, dep=500),  # EBITDA 마진 60% vs 영업이익률 10%
        _seg("B", 1000, 100, dep=10),
    )
    view = build_lenses(_ms(), segment_profit=sp).view(CAPITAL_RETURN)
    assert view.usable  # 상각 격차는 말한다
    assert view.headline is None  # 그러나 결론은 없다
    assert "자본이 어디에 놓여 있는가" in view.unanswered


def test_silent_lenses_produce_no_observations_and_no_tension():
    lenses = build_lenses(_ms())
    assert build_lens_observations(lenses) == []
    assert lenses.tensions == []


# ── 충돌 세 종류 ─────────────────────────────────────────────────────
def test_opposing_verdicts_produce_a_watchpoint_naming_what_to_check():
    cap = _view(
        CAPITAL_RETURN, LensReading(step=1, claim="x", direction=ADVERSE), watch="적자 부문 자산"
    )
    dur = _view(DURABILITY, LensReading(step=2, claim="y", direction=SUPPORTIVE), watch="판관비율")
    tensions = find_tensions([cap, dur])
    assert [t.kind for t in tensions] == ["verdict"]
    assert "적자 부문 자산" in tensions[0].text


def test_same_verdict_with_different_grounds_is_still_a_watchpoint():
    """**초판이 아무 말도 안 하던 자리다.** 둘 다 긍정이면 "합의됐다"로 읽히지만
    실제로는 서로 다른 방식으로 맞을 수 있다는 뜻이다."""
    cap = _view(
        CAPITAL_RETURN, LensReading(step=1, claim="x", direction=SUPPORTIVE), watch="부문 자산"
    )
    dur = _view(DURABILITY, LensReading(step=2, claim="y", direction=SUPPORTIVE), watch="판관비율")
    tensions = find_tensions([cap, dur])
    assert [t.kind for t in tensions] == ["grounds"]
    assert "부문 자산" in tensions[0].text and "판관비율" in tensions[0].text


def test_same_verdict_and_same_watch_produces_nothing():
    """정말로 같은 말을 하면 관전 포인트에 보탤 게 없다."""
    views = [
        _view(k, LensReading(step=1, claim="x", direction=SUPPORTIVE), watch="같은 것")
        for k in (CAPITAL_RETURN, DURABILITY)
    ]
    assert find_tensions(views) == []


def test_a_caveat_becomes_its_own_watchpoint():
    cap = _view(
        CAPITAL_RETURN,
        LensReading(step=1, claim="자산이 버는 곳에", direction=SUPPORTIVE),
        LensReading(step=3, claim="수익률이 부채에서 온다", direction=ADVERSE),
    )
    kinds = [t.kind for t in find_tensions([cap])]
    assert kinds == ["caveat"]


def test_a_lens_without_a_verdict_cannot_clash():
    """근거의 부재는 긍정이 아니다. 삼성전자는 부문 자산을 아예 공시하지 않는데
    "자본은 버는 곳에 놓여 있다"가 나왔던 자리다."""
    cap = _view(CAPITAL_RETURN, LensReading(step=3, claim="상각 격차", direction=NEUTRAL))
    dur = _view(DURABILITY, LensReading(step=2, claim="원가 주도", direction=ADVERSE))
    assert find_tensions([cap, dur]) == []


# ── 적자에서 부등호가 뒤집히는 자리 ──────────────────────────────────
def test_leverage_reading_does_not_fire_on_a_loss():
    """손실 상태에서 레버리지가 높으면 ROE는 ROA보다 **더 낮아진다**(더 큰 음수).
    부호를 안 보면 롯데케미칼에서 반대로 말하게 된다 — 실제로 그랬다."""
    v = ValuationSet(fiscal_year=2025, roe=-3.0, roa=-10.0)
    claims = " ".join(
        r.claim for r in build_lenses(_ms(), valuation=v).view(CAPITAL_RETURN).readings
    )
    assert "부채 사용에서 온다" not in claims


def test_leverage_reading_fires_when_both_returns_are_positive():
    v = ValuationSet(fiscal_year=2025, roe=20.0, roa=5.0)
    claims = " ".join(
        r.claim for r in build_lenses(_ms(), valuation=v).view(CAPITAL_RETURN).readings
    )
    assert "부채 사용에서 온다" in claims


# ── 자본수익률 렌즈 ──────────────────────────────────────────────────
def test_trapped_assets_lead_the_lens_when_capital_sits_in_a_loss_maker():
    """롯데케미칼: 자산 31.1조 중 24.0조가 영업적자 부문에 묶여 있다.
    「싸 보이는 이유가 자산이 안 벌기 때문」 — 전사 지표로는 안 나오는 논지다."""
    sp = _sp(
        _seg("기초화학사업부", 12_480, -857, assets=24_048),
        _seg("첨단소재사업부", 5_086, 123, assets=5_799),
    )
    view = build_lenses(_ms(), segment_profit=sp).view(CAPITAL_RETURN)
    assert view.verdict is ADVERSE
    assert view.headline.step == 1
    assert "기초화학사업부" in view.headline.claim
    assert view.watch == "적자 부문에 묶인 자산이 줄어드는가"


def test_assets_in_profitable_segments_lead_supportively():
    sp = _sp(_seg("A", 1000, 100, assets=900), _seg("B", 1000, 50, assets=800))
    view = build_lenses(_ms(), segment_profit=sp).view(CAPITAL_RETURN)
    assert view.verdict is SUPPORTIVE


def test_second_step_is_not_answered_without_the_first():
    """자본이 어디 있는지 모르면 그게 버는지 말할 수 없다. 임의로 매긴
    우선순위가 아니라 질문에서 도출된 순서다."""
    sp = _sp(_seg("A", 1000, 100), _seg("B", 1000, 50))  # 자산 없음
    view = build_lenses(_ms(), segment_profit=sp).view(CAPITAL_RETURN)
    assert not any(r.step == 2 for r in view.readings)


def test_trapped_share_is_measured_against_segments_with_known_assets():
    """자산이 확인된 부문만 분모에 넣는다. LG전자처럼 일부 부문의 자산이
    짝지어지지 않으면 전체로 나눠 비중이 낮게 나온다."""
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
    assert "자산총계가 아니다" in (entry.formula or "")


# ── 재현성 렌즈 ──────────────────────────────────────────────────────
def test_cost_driven_margin_change_reads_as_less_repeatable():
    ms = _bridge_ms(cost_delta=-50, sga_delta=0)
    view = build_lenses(ms, bridge=build_margin_bridge(ms)).view(DURABILITY)
    assert view.verdict is ADVERSE
    assert view.watch == "원가율이 같은 방향으로 이어지는가"


def test_sga_driven_margin_change_reads_as_more_controllable():
    ms = _bridge_ms(cost_delta=0, sga_delta=-50)
    view = build_lenses(ms, bridge=build_margin_bridge(ms)).view(DURABILITY)
    assert view.verdict is SUPPORTIVE
    assert view.watch == "판관비율의 개선이 유지되는가"


def test_diverging_segment_margins_block_a_company_wide_explanation():
    sp = _sp(
        _seg("A", 1000, 100, rev_prior=1000, op_prior=50),
        _seg("B", 1000, 50, rev_prior=1000, op_prior=100),
    )
    view = build_lenses(_ms(), segment_profit=sp).view(DURABILITY)
    assert any("방향이 갈렸다" in r.claim for r in view.readings)


def test_operating_and_net_income_moving_apart_is_flagged():
    view = build_lenses(_ms(operating_income=(80, 100), net_income=(120, 100))).view(DURABILITY)
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


def test_observations_label_the_headline_and_caveat_separately():
    """평평한 목록으로 주면 LLM이 우선순위를 지어낸다."""
    sp = _sp(_seg("A", 1000, 100, assets=900), _seg("B", 1000, 50, assets=800))
    v = ValuationSet(fiscal_year=2025, roe=20.0, roa=5.0)
    obs = " ".join(build_lens_observations(build_lenses(_ms(), valuation=v, segment_profit=sp)))
    assert "자본수익률·주된 발견" in obs
    assert "자본수익률·단서" in obs
    assert "자본수익률·다음에 볼 것" in obs


def test_every_lens_number_is_registered():
    sp = _sp(_seg("적자", 100, -10, assets=600), _seg("흑자", 100, 10, assets=400))
    lenses = build_lenses(_ms(), segment_profit=sp)
    keys = {e.key for e in build_lens_entries(lenses, sp, PROV, 2025)}
    assert keys == {"trapped_asset_2025a", "trapped_asset_share_2025a"}


def test_no_entries_without_segment_assets():
    sp = _sp(_seg("A", 100, 10), _seg("B", 100, 10))
    assert build_lens_entries(build_lenses(_ms(), segment_profit=sp), sp, PROV, 2025) == []
