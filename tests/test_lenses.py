"""분석 렌즈 — 같은 숫자에 다른 질문 (D35).

여기서 가장 중요한 테스트는 "렌즈가 말하는가"가 아니라 셋이다:

1. **근거 없이 말하지 않는가** — 섹션을 채우려고 확인되지 않은 판단을 쓰는 것이
   이 제품이 피하려는 것 그 자체다.
2. **1순위에 답하지 못하면 결론을 내지 않는가** — 초판이 여기서 틀렸다. 코스닥
   25곳에서 자본 1순위 질문에 답한 곳이 0인데도 3순위 관찰로 결론을 내
   다섯 곳이 글자까지 같은 문장을 받았다.
3. **부차적 관찰이 주된 발견을 뒤집지 않는가** — LG전자에서 실제로 그랬다.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest

from arc.data.base import Provenance
from arc.data.kr.dart_reports import (
    Affiliate,
    Affiliates,
    Ownership,
    PeriodicReportInfo,
)
from arc.finmodel.business import BusinessProfile
from arc.finmodel.lenses import (
    ADVERSE,
    CAPITAL,
    CONCENTRATION,
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
from arc.finmodel.segments import SegmentBreakdown, SegmentLine
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


def _seg_profit(name, revenue, op, assets=None, dep=None, rev_prior=None, op_prior=None):
    return SegmentProfitLine(
        name=name,
        revenue=revenue,
        operating_income=op,
        assets=assets,
        depreciation=dep,
        revenue_prior=rev_prior,
        op_prior=op_prior,
    )


def _segments(*pairs, total=None) -> SegmentBreakdown:
    """(부문명, 매출, 전기매출) → 매출 표 (D28)."""
    lines = [SegmentLine(name=n, amount=a, share=None, prior=p) for n, a, p in pairs]
    return SegmentBreakdown(
        fiscal_year=2025,
        lines=lines,
        total=total or sum(x.amount for x in lines),
        revenue=total or sum(x.amount for x in lines),
        reconciled=True,
    )


def _info(principal="홍길동", total_stake=60.0, affiliates=None):
    return PeriodicReportInfo(
        fiscal_year=2025,
        ownership=Ownership(
            fiscal_year=2025,
            principal=principal,
            principal_stake=30.0,
            total_stake=total_stake,
            holder_count=3,
        )
        if principal
        else None,
        affiliates=affiliates,
    )


def _business(weight_pct: float | None, top_name="자회사A", total_assets=1000):
    """출자 장부가 비중을 지정한 BusinessProfile."""
    if weight_pct is None:
        return BusinessProfile(fiscal_year=2025, overview="사업 서술")
    book = int(total_assets * weight_pct / 100)
    aff = Affiliates(
        fiscal_year=2025,
        entries=[Affiliate(name=top_name, purpose="경영참여", stake=50.0, book_value=book)],
    )
    return BusinessProfile(
        fiscal_year=2025, overview="사업 서술", affiliates=aff, total_assets=total_assets
    )


def _bridge_ms(cost_delta: int, sga_delta: int) -> MetricSet:
    return _ms(
        revenue=(1000, 1000),
        cost_of_sales=(600 + cost_delta, 600),
        sga=(300 + sga_delta, 300),
        operating_income=(100 - cost_delta - sga_delta, 100),
    )


def _view(key: str, *readings: LensReading, watch: str = "", unanswered=()) -> LensView:
    v = LensView(key=key, label=key, question="q", chain=("a", "b", "c", "d"))
    v.readings.extend(readings)
    v.unanswered_steps.extend(unanswered)
    v.watch = watch
    return v


# ── 1순위에 답하지 못하면 결론을 내지 않는다 ─────────────────────────
def test_a_late_reading_cannot_conclude_when_an_earlier_step_is_unanswered():
    """**2차 수정의 핵심.** 코스닥 25곳에서 자본 1순위 질문에 답한 곳이 0인데도
    초판은 3순위 관찰(ROE vs ROA)로 결론을 내 다섯 곳이 글자까지 같은 문장을
    받았다. 모르면 결론짓지 않는다."""
    v = _view(CAPITAL, LensReading(step=3, claim="레버리지", direction=ADVERSE), unanswered=(1,))
    assert v.verdict is None
    assert v.usable  # 판독은 남되


def test_an_unanswered_later_step_does_not_block_the_headline():
    v = _view(
        CAPITAL, LensReading(step=1, claim="자본이 번다", direction=SUPPORTIVE), unanswered=(3,)
    )
    assert v.verdict is SUPPORTIVE


def test_headline_is_the_earliest_answered_step_not_the_first_appended():
    v = _view(
        CAPITAL,
        LensReading(step=3, claim="레버리지", direction=ADVERSE),
        LensReading(step=1, claim="자본이 번다", direction=SUPPORTIVE),
    )
    assert v.headline.step == 1


def test_a_later_opposing_reading_becomes_a_caveat_not_the_verdict():
    """LG전자에서 실제로 틀렸던 자리."""
    v = _view(
        CAPITAL,
        LensReading(step=1, claim="자본이 번다", direction=SUPPORTIVE),
        LensReading(step=2, claim="자본이 본업 밖에", direction=ADVERSE),
    )
    assert v.verdict is SUPPORTIVE
    assert [c.claim for c in v.caveats] == ["자본이 본업 밖에"]


def test_neutral_readings_never_become_the_headline():
    v = _view(CAPITAL, LensReading(step=1, claim="맥락", direction=NEUTRAL))
    assert v.headline is None


# ── 본문은 회사마다 달라야 한다 ──────────────────────────────────────
def test_report_text_fills_slots_with_placeholders():
    """이름도 숫자도 없는 판독은 상용구다. 슬롯이 회사별 수치를 물고 온다."""
    r = LensReading(
        step=1,
        claim="자기자본이익률이 낮다",
        report="자기자본이익률이 {roe}에 그친다",
        slots={"roe": "roe_2025a"},
    )
    assert (
        r.report_text(lambda k: "{{num:" + k + "}}")
        == "자기자본이익률이 {{num:roe_2025a}}에 그친다"
    )


def test_report_text_falls_back_when_a_slot_is_missing():
    """등록되지 않은 키를 그대로 두면 본문에 `{roe}`가 실린다. 크기 없는
    문장으로 물러난다."""
    r = LensReading(
        step=1, claim="자기자본이익률이 낮다", report="{roe}에 그친다", slots={"roe": "x"}
    )
    assert r.report_text(lambda k: None) == "자기자본이익률이 낮다"


# ── 자본 렌즈 (1순위 = ROE, 코스닥 100%) ─────────────────────────────
def test_capital_lens_answers_its_first_question_from_roe_alone():
    """초판은 부문 자산(코스닥 0%)을 1순위로 놓아 타깃 시장에서 침묵했다."""
    v = build_lenses(_ms(), valuation=ValuationSet(fiscal_year=2025, roe=12.0)).view(CAPITAL)
    assert v.verdict is SUPPORTIVE
    assert v.headline.step == 1


def test_a_loss_making_company_and_a_low_return_company_get_different_sentences():
    """규칙이 발화했다는 것과 회사에 대해 뭔가 말했다는 것은 다르다."""
    loss = build_lenses(_ms(), valuation=ValuationSet(fiscal_year=2025, roe=-8.0)).view(CAPITAL)
    low = build_lenses(_ms(), valuation=ValuationSet(fiscal_year=2025, roe=2.0)).view(CAPITAL)
    high = build_lenses(_ms(), valuation=ValuationSet(fiscal_year=2025, roe=18.0)).view(CAPITAL)
    claims = {loss.headline.claim, low.headline.claim, high.headline.claim}
    assert len(claims) == 3
    assert loss.verdict is ADVERSE and low.verdict is ADVERSE and high.verdict is SUPPORTIVE


def test_capital_lens_names_the_largest_affiliate():
    v = build_lenses(
        _ms(),
        valuation=ValuationSet(fiscal_year=2025, roe=12.0),
        business=_business(35.0, top_name="한빛산업"),
    ).view(CAPITAL)
    assert any("한빛산업" in r.claim for r in v.readings)
    assert v.caveats and v.caveats[0].direction is ADVERSE


def test_capital_lens_is_silent_without_any_return_measure():
    v = build_lenses(_ms()).view(CAPITAL)
    assert not v.usable
    assert v.silent_reason


# ── 집중 렌즈 (1순위 = 부문 매출, 코스닥 82%) ────────────────────────
def test_concentration_lens_names_the_dominant_segment():
    seg = _segments(("의약품", 800, 700), ("화장품", 200, 150))
    v = build_lenses(_ms(), segments=seg, info=_info()).view(CONCENTRATION)
    assert v.verdict is ADVERSE
    assert "의약품" in v.headline.claim


def test_a_diversified_company_reads_supportively():
    seg = _segments(("A", 400, 350), ("B", 350, 300), ("C", 250, 200))
    v = build_lenses(_ms(), segments=seg, info=_info()).view(CONCENTRATION)
    assert v.verdict is SUPPORTIVE


def test_a_single_segment_company_is_flagged_as_having_no_buffer():
    seg = _segments(("반도체", 1000, 900))
    v = build_lenses(_ms(), segments=seg, info=_info()).view(CONCENTRATION)
    assert v.verdict is ADVERSE
    assert "완충할 여지가 없다" in v.headline.claim


def test_concentration_lens_names_the_controlling_shareholder():
    seg = _segments(("A", 600, 500), ("B", 400, 300))
    v = build_lenses(_ms(), segments=seg, info=_info(principal="(주)모회사")).view(CONCENTRATION)
    assert any("(주)모회사" in r.claim for r in v.readings)


def test_ownership_alone_cannot_conclude_without_segment_data():
    """소유 집중은 3순위다. 1순위(매출이 어디 몰렸나)를 모르면 결론이 아니다."""
    v = build_lenses(_ms(), info=_info()).view(CONCENTRATION)
    assert v.usable
    assert v.verdict is None


# ── 재현성 렌즈 ──────────────────────────────────────────────────────
def test_cost_driven_margin_change_reads_as_less_repeatable():
    ms = _bridge_ms(cost_delta=-50, sga_delta=0)
    v = build_lenses(ms, bridge=build_margin_bridge(ms)).view(DURABILITY)
    assert v.verdict is ADVERSE
    assert v.watch == "원가율이 같은 방향으로 이어지는가"


def test_sga_driven_margin_change_reads_as_more_controllable():
    ms = _bridge_ms(cost_delta=0, sga_delta=-50)
    v = build_lenses(ms, bridge=build_margin_bridge(ms)).view(DURABILITY)
    assert v.verdict is SUPPORTIVE


def test_without_a_bridge_the_durability_lens_cannot_conclude():
    sp = _sp(
        _seg_profit("A", 1000, 100, rev_prior=1000, op_prior=50),
        _seg_profit("B", 1000, 50, rev_prior=1000, op_prior=100),
    )
    v = build_lenses(_ms(), segment_profit=sp).view(DURABILITY)
    assert v.usable and v.verdict is None


# ── 충돌 세 종류 ─────────────────────────────────────────────────────
def test_opposing_verdicts_produce_a_watchpoint_naming_what_to_check():
    a = _view(
        CAPITAL, LensReading(step=1, claim="x", direction=ADVERSE), watch="자기자본이익률 회복"
    )
    b = _view(DURABILITY, LensReading(step=1, claim="y", direction=SUPPORTIVE), watch="판관비율")
    t = find_tensions([a, b])
    assert [x.kind for x in t] == ["verdict"]
    assert "자기자본이익률 회복" in t[0].text


def test_same_verdict_with_different_grounds_is_still_a_watchpoint():
    """초판이 아무 말도 안 하던 자리 — 둘 다 긍정이면 "합의됐다"로 읽히지만
    실제로는 서로 다른 방식으로 맞을 수 있다는 뜻이다."""
    a = _view(CAPITAL, LensReading(step=1, claim="x", direction=SUPPORTIVE), watch="ROE 유지")
    b = _view(DURABILITY, LensReading(step=1, claim="y", direction=SUPPORTIVE), watch="판관비율")
    t = find_tensions([a, b])
    assert [x.kind for x in t] == ["grounds"]


def test_a_lens_without_a_verdict_cannot_clash():
    """근거의 부재는 긍정이 아니다."""
    a = _view(CAPITAL, LensReading(step=3, claim="레버리지", direction=ADVERSE), unanswered=(1,))
    b = _view(DURABILITY, LensReading(step=1, claim="원가 주도", direction=ADVERSE))
    assert find_tensions([a, b]) == []


def test_a_caveat_becomes_its_own_watchpoint():
    a = _view(
        CAPITAL,
        LensReading(step=1, claim="자본이 번다", direction=SUPPORTIVE),
        LensReading(step=2, claim="자본이 본업 밖에", direction=ADVERSE),
    )
    assert [x.kind for x in find_tensions([a])] == ["caveat"]


# ── 불변식 ───────────────────────────────────────────────────────────
def test_observations_carry_no_magnitudes():
    """프롬프트에 들어간 숫자는 LLM이 리터럴로 베낀다 (D16)."""
    ms = _bridge_ms(cost_delta=-50, sga_delta=0)
    lenses = build_lenses(
        ms,
        valuation=ValuationSet(fiscal_year=2025, roe=12.0, payout_ratio=30.0),
        bridge=build_margin_bridge(ms),
        segments=_segments(("의약품", 800, 700), ("화장품", 200, 150)),
        business=_business(35.0),
        info=_info(),
    )
    for text in build_lens_observations(lenses):
        assert not re.search(r"\d", text), text


def test_observations_label_the_headline_and_caveat_separately():
    lenses = build_lenses(
        _ms(), valuation=ValuationSet(fiscal_year=2025, roe=12.0), business=_business(35.0)
    )
    obs = " ".join(build_lens_observations(lenses))
    assert "자본·주된 발견" in obs and "자본·단서" in obs and "자본·다음에 볼 것" in obs


def test_unanswered_steps_are_reported_by_name():
    lenses = build_lenses(_ms(), valuation=ValuationSet(fiscal_year=2025, roe=12.0))
    obs = " ".join(build_lens_observations(lenses))
    assert "그 자본이 어디에 놓여 있는가" in obs


def test_every_lens_number_is_registered():
    sp = _sp(_seg_profit("적자", 100, -10, assets=600), _seg_profit("흑자", 100, 10, assets=400))
    lenses = build_lenses(_ms(), segment_profit=sp)
    keys = {e.key for e in build_lens_entries(lenses, sp, PROV, 2025)}
    assert keys == {"trapped_asset_2025a", "trapped_asset_share_2025a"}


def test_no_entries_without_segment_assets():
    sp = _sp(_seg_profit("A", 100, 10), _seg_profit("B", 100, 10))
    assert build_lens_entries(build_lenses(_ms(), segment_profit=sp), sp, PROV, 2025) == []


def test_trapped_share_is_measured_against_segments_with_known_assets():
    sp = _sp(
        _seg_profit("적자", 100, -10, assets=600),
        _seg_profit("흑자", 100, 10, assets=400),
        _seg_profit("자산모름", 100, 10, assets=None),
    )
    lenses = build_lenses(_ms(), segment_profit=sp)
    entry = next(
        e
        for e in build_lens_entries(lenses, sp, PROV, 2025)
        if e.key == "trapped_asset_share_2025a"
    )
    assert entry.value == pytest.approx(60.0)
    assert "자산총계가 아니다" in (entry.formula or "")
