"""화면이 엔진을 다 받는가 (D73).

`ReportResult`의 독스트링이 *"감사에 필요한 중간물을 **모두 보관**한다"*이고
실제로 그렇다. 그런데 `_to_view()`가 절반을 버리고 있었다 — 관점·부문 손익·
밸류에이션·주요정보·조회 실패 사유가 통째로.

여기서 지키는 것은 **구분 셋**이다:

  1. 「없다」와 「못 받았다」 — `unavailable`
  2. 「정상 부재」와 「결함」 — 단일 부문은 D33이 정확히 거부한 것이다
  3. 「검산했다」와 「안 했다」 — 통과한 검산도 화면에 나와야 한다
"""

from __future__ import annotations

from arc.finmodel.lenses import LensReading, LensSet, LensView
from arc.finmodel.segment_profit import SegmentProfitLine, SegmentProfitSet
from arc.finmodel.valuation import ValuationSet
from arc.web.app import (
    _business_row,
    _report_info_row,
    _segment_profit_row,
    _valuation_row,
)


class TestReportInfo:
    def test_unavailable_names_what_is_missing(self):
        """**「배당이 없다」와 「배당을 못 받았다」는 다른 얘기다.**"""

        class _Info:
            fiscal_year = 2025
            shares = dividend = audit = workforce = None
            unavailable = ["dividend", "workforce"]

        row = _report_info_row(_Info())
        assert row["unavailable"] == ["dividend", "workforce"]
        assert row["dps"] is None  # 못 받았으니 None — 0이 아니다

    def test_none_is_an_empty_dict_not_a_shape_with_nulls(self):
        """조회 자체를 안 한 것은 **빈 dict**다 — 화면이 구획을 안 그린다."""
        assert _report_info_row(None) == {}


class TestSegmentProfit:
    def _single(self) -> SegmentProfitSet:
        return SegmentProfitSet(
            fiscal_year=2025,
            lines=[SegmentProfitLine(name="반도체", revenue=1_000, operating_income=200)],
            note="동사는 영업부문별 손익을 공시하지 않았다.",
        )

    def test_a_single_segment_is_absent_not_failed(self):
        """**SK하이닉스에 부문 손익이 없는 것은 정상이다** (D33).

        사유가 함께 나와야 화면이 「결함」으로 안 읽는다.
        """
        row = _segment_profit_row(self._single())
        assert row["usable"] is False
        assert "공시하지 않았다" in row["note"]

    def test_a_real_breakdown_carries_its_own_check(self):
        sp = SegmentProfitSet(
            fiscal_year=2025,
            lines=[
                SegmentProfitLine(name="DX", revenue=1_000, operating_income=68),
                SegmentProfitLine(name="DS", revenue=800, operating_income=153),
            ],
            reconciled=True,
            revenue_gap_pct=0.0,
        )
        row = _segment_profit_row(sp)
        assert row["usable"] is True
        assert row["revenue_gap_pct"] == 0.0
        # 마진은 화면이 다시 계산하지 않게 여기서 낸다
        assert [x["margin"] for x in row["lines"]] == [6.8, 19.1]

    def test_margin_is_none_when_there_is_nothing_to_divide(self):
        """**0으로 채우지 않는다.** 영업이익이 없는 부문은 마진도 없다."""
        sp = SegmentProfitSet(
            fiscal_year=2025,
            lines=[SegmentProfitLine(name="기타", revenue=0, operating_income=None)],
        )
        assert _segment_profit_row(sp)["lines"][0]["margin"] is None


class TestValuation:
    def test_the_eps_cross_check_survives(self):
        """**교차검증이 이 표의 존재 이유다.**

        재무제표 희석EPS와 배당공시 주당순이익은 같아야 한다. SK하이닉스에서
        실측 -2.69%가 나왔고, 그건 화면에 보여야 하는 사실이다.
        """
        row = _valuation_row(
            ValuationSet(fiscal_year=2025, eps_stmt=60_378, eps_disclosed=62_044, eps_gap_pct=-2.69)
        )
        assert row["eps_stmt"] == 60_378
        assert row["eps_disclosed"] == 62_044
        assert row["eps_gap_pct"] == -2.69

    def test_shares_reconciled_is_carried_even_when_true(self):
        """통과한 검산도 낸다 — **안 보이면 안 한 것과 구별되지 않는다.**"""
        row = _valuation_row(
            ValuationSet(fiscal_year=2025, shares_issued=100, shares_reconciled=True)
        )
        assert row["shares_reconciled"] is True


class TestLenses:
    def test_a_silent_lens_still_reaches_the_screen(self):
        """**침묵한 렌즈도 낸다.**

        통째로 빠지면 검토자는 「이 회사엔 볼 관점이 없구나」로 읽는다. 못 본
        것을 적는 편이 정직하다.
        """
        from arc.pipeline.earnings_review import _lens_section

        quiet = LensView(
            key="capital",
            label="자본",
            question="투입한 자본이 수익을 내는가",
            silent_reason="자기자본이익률을 계산할 근거를 찾지 못했다.",
        )
        section = _lens_section(LensSet(views=[quiet]), lambda k: None)
        assert len(section["views"]) == 1
        assert section["views"][0]["headline"] == ""
        assert "찾지 못했다" in section["views"][0]["note"]

    def test_a_conclusion_needs_the_earlier_question_answered(self):
        """**모르면 결론짓지 않는다.**

        코스닥 다섯 곳이 글자까지 같은 문장을 받은 사고의 원인이 이것이다 —
        3순위 관찰 하나로 결론을 냈다.
        """
        view = LensView(
            key="capital",
            label="자본",
            question="투입한 자본이 수익을 내는가",
            chain=("a", "b", "c"),
            readings=[LensReading(step=3, claim="세 번째 관찰", direction="긍정")],
            unanswered_steps=[1],
        )
        assert view.headline is None  # 1순위를 모르면 3순위로 결론을 못 낸다


class TestBusiness:
    def test_signals_and_source_come_together(self):
        from arc.finmodel.business import BusinessProfile

        row = _business_row(
            BusinessProfile(
                fiscal_year=2025,
                overview="반도체를 만든다.",
                signals=["단일 사업"],
                source_title="II. 사업의 내용",
            )
        )
        assert row["signals"] == ["단일 사업"]
        assert row["source_title"] == "II. 사업의 내용"

    def test_none_is_empty(self):
        assert _business_row(None) == {}
