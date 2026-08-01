"""밸류에이션 레이어 + 리스크 병합 테스트.

이 레이어는 **두 원천이 만나는 지점**이다 — 재무제표(자본·순이익)와 정기보고서
주요정보(주식수·배당). 여기서 기준을 섞으면 주당 지표가 조용히 틀린다.
지배주주 vs 전체, 발행 vs 유통, 원 vs 백만원이 그 지점이다.
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
from arc.data.kr.dart_reports import (
    AuditOpinion,
    DividendInfo,
    PeriodicReportInfo,
    ShareCounts,
)
from arc.finmodel.metrics import extract_metrics
from arc.finmodel.valuation import (
    build_valuation,
    build_valuation_entries,
    build_valuation_observations,
)
from arc.llm.number_registry import NumberRegistry
from arc.pipeline.earnings_review import merge_risks

PROV = Provenance(source="opendart", retrieved_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC))


def _stmt(items):
    return FinancialStatement(
        symbol="000000",
        fiscal_year=2025,
        period=PeriodType.ANNUAL,
        consolidation=ConsolidationType.CONSOLIDATED,
        items=items,
        provenance=PROV,
    )


def _li(name, amount, prior=None, sj="IS", account_id=None):
    return FinancialLineItem(
        account_id=account_id,
        account_name=name,
        amount=amount,
        prior_amount=prior,
        statement_type=sj,
    )


def _metrics(**over):
    """지배주주 기준이 전체와 다른 회사. 기준을 섞으면 여기서 드러난다."""
    base = {
        "revenue": (1000, 900),
        "operating_income": (200, 140),
        "net_income": (110, 80),  # 전체 (비지배 포함)
        "net_income_parent": (100, 72),  # 지배주주
        "total_assets": (2000, 1800),
        "total_liabilities": (800, 760),
        "total_equity": (1200, 1040),  # 전체 자본
        "equity_parent": (1000, 900),  # 지배주주 지분
    }
    base.update(over)
    ids = {
        "net_income_parent": "ifrs-full_ProfitLossAttributableToOwnersOfParent",
        "equity_parent": "ifrs-full_EquityAttributableToOwnersOfParent",
    }
    names = {
        "revenue": ("매출액", "IS"),
        "operating_income": ("영업이익", "IS"),
        "net_income": ("당기순이익", "IS"),
        "net_income_parent": ("지배기업 소유주지분", "IS"),
        "total_assets": ("자산총계", "BS"),
        "total_liabilities": ("부채총계", "BS"),
        "total_equity": ("자본총계", "BS"),
        "equity_parent": ("지배기업 소유주지분", "BS"),
    }
    items = []
    for key, (cur, prior) in base.items():
        name, sj = names[key]
        items.append(_li(name, cur, prior, sj=sj, account_id=ids.get(key)))
    return extract_metrics(_stmt(items))


def _shares(issued=100, treasury=0, outstanding=None, preferred=None, reconciled=True):
    out = outstanding if outstanding is not None else issued - treasury
    return ShareCounts(
        fiscal_year=2025,
        issued=issued,
        treasury=treasury,
        outstanding=out,
        common_issued=issued,
        common_treasury=treasury,
        common_outstanding=out,
        preferred_issued=preferred,
        reconciled=reconciled,
        rcept_no="20260311000001",
        provenance=PROV,
    )


def _dividend(dps=10, yield_pct=2.0, payout=25.0, eps=10):
    return DividendInfo(
        fiscal_year=2025,
        dps_common=dps,
        dps_preferred=None,
        dividend_yield_common=yield_pct,
        payout_ratio=payout,
        total_cash_dividend=1000,
        eps_reported=eps,
        par_value=100,
        rcept_no="20260311000001",
        provenance=PROV,
    )


_DEFAULT = object()  # None을 "명시적으로 없음"으로 쓰기 위한 센티널


def _info(shares=_DEFAULT, dividend=_DEFAULT, audit=None):
    return PeriodicReportInfo(
        fiscal_year=2025,
        shares=_shares() if shares is _DEFAULT else shares,
        dividend=_dividend() if dividend is _DEFAULT else dividend,
        audit=audit,
    )


class TestPerShareBasis:
    def test_bps_uses_parent_equity_and_issued_shares(self):
        """지배주주지분 ÷ 발행주식총수. 전체자본이나 유통주식수를 쓰면 안 된다."""
        v = build_valuation(_metrics(), _info(shares=_shares(issued=100, treasury=10)))
        assert v.bps == pytest.approx(1000 / 100)  # 1200/100도 1000/90도 아니다

    def test_roe_uses_parent_income_over_average_parent_equity(self):
        v = build_valuation(_metrics(), _info())
        # 100 / ((1000+900)/2) = 10.526%
        assert v.roe == pytest.approx(100 / 950 * 100)

    def test_roe_falls_back_to_period_end_and_says_so(self):
        ms = _metrics(equity_parent=(1000, None))
        v = build_valuation(ms, _info())
        assert v.roe == pytest.approx(10.0)
        assert any("전기 자본" in u for u in v.unavailable)

    def test_debt_ratio_uses_total_not_parent(self):
        """부채비율은 전체 자본 기준이다 — 주당 지표와 기준이 다르다."""
        v = build_valuation(_metrics(), _info())
        assert v.debt_ratio == pytest.approx(800 / 1200 * 100)

    def test_missing_shares_leaves_bps_empty(self):
        v = build_valuation(_metrics(), _info(shares=None))
        assert v.bps is None
        assert "주식수" in v.unavailable


class TestEpsCrossCheck:
    def test_agreement_passes(self):
        ms = _metrics()
        items = [*ms.values.values()]
        assert items  # 지표가 있어야 의미가 있다
        stmt = _stmt(
            [
                _li("매출액", 1000, 900),
                _li("희석주당이익", 100, 80, account_id="ifrs-full_DilutedEarningsLossPerShare"),
            ]
        )
        v = build_valuation(extract_metrics(stmt), _info(dividend=_dividend(eps=100)))
        assert v.eps_gap_pct == pytest.approx(0.0)
        assert v.eps_cross_check_ok

    def test_disagreement_flagged(self):
        """두 경로가 어긋나면 주식수·순이익 기준을 잘못 잡은 것이다."""
        stmt = _stmt(
            [_li("희석주당이익", 200, None, account_id="ifrs-full_DilutedEarningsLossPerShare")]
        )
        v = build_valuation(extract_metrics(stmt), _info(dividend=_dividend(eps=100)))
        assert v.eps_cross_check_ok is False

    def test_undecidable_when_one_side_missing(self):
        v = build_valuation(_metrics(), _info(dividend=_dividend(eps=None)))
        assert v.eps_cross_check_ok is None

    def test_gap_is_internal_not_in_catalog(self):
        stmt = _stmt(
            [_li("희석주당이익", 105, None, account_id="ifrs-full_DilutedEarningsLossPerShare")]
        )
        info = _info(dividend=_dividend(eps=100))
        v = build_valuation(extract_metrics(stmt), info)
        reg = NumberRegistry()
        reg.register_all(build_valuation_entries(v, info, PROV))
        assert "eps_gap_2025a" in reg
        assert "eps_gap_2025a" not in {r["key"] for r in reg.catalog()}


class TestImpliedPrice:
    def test_market_cap_uses_issued_shares(self):
        v = build_valuation(
            _metrics(), _info(shares=_shares(issued=100, treasury=10), dividend=_dividend())
        )
        assert v.price == 500  # 10 / 2%
        assert v.market_cap == 500 * 100  # 유통 90이 아니다

    def test_per_prefers_disclosed_eps(self):
        v = build_valuation(_metrics(), _info(dividend=_dividend(eps=25)))
        assert v.per == pytest.approx(500 / 25)

    def test_no_dividend_means_no_price_derived_metrics(self):
        """배당이 없으면 앵커가 없다. 임의로 만들지 않는다."""
        v = build_valuation(_metrics(), _info(dividend=_dividend(dps=None, yield_pct=None)))
        assert v.price is None
        assert v.per is None and v.pbr is None and v.market_cap is None
        assert "주가 앵커" in v.unavailable

    def test_implied_flag_shows_in_label(self):
        info = _info()
        v = build_valuation(_metrics(), info)
        entries = {e.key: e for e in build_valuation_entries(v, info, PROV)}
        assert "역산" in (entries["price_2025a"].label or "")
        assert "역산" in (entries["market_cap_2025a"].label or "")


class TestValuationObservations:
    def test_no_magnitudes_leak_into_thesis(self):
        """관찰문은 프롬프트에 그대로 들어간다. 크기가 있으면 LLM이 베낀다."""
        audit = AuditOpinion(
            fiscal_year=2025,
            period_label="제10기 (당기)",
            auditor="한영회계법인",
            opinion="적정의견",
            kam_items=["영업권의 회수가능가액"],
        )
        info = _info(audit=audit)
        text = " ".join(build_valuation_observations(build_valuation(_metrics(), info), info))
        assert not NumberRegistry().find_unregistered_numbers(text)

    def test_non_clean_opinion_surfaces_first(self):
        audit = AuditOpinion(
            fiscal_year=2025,
            period_label="제10기 (당기)",
            auditor="한영회계법인",
            opinion="의견거절",
            kam_items=[],
        )
        info = _info(audit=audit)
        obs = build_valuation_observations(build_valuation(_metrics(), info), info)
        assert any("적정이 아니다" in o for o in obs)

    def test_kam_not_treated_as_distress_when_opinion_clean(self):
        audit = AuditOpinion(
            fiscal_year=2025,
            period_label="제10기 (당기)",
            auditor="한영회계법인",
            opinion="적정의견",
            kam_items=["재고자산 평가"],
        )
        info = _info(audit=audit)
        obs = " ".join(build_valuation_observations(build_valuation(_metrics(), info), info))
        assert "부실로 단정하면 안 된다" in obs


class TestMergeRisks:
    def _info_with_kam(self):
        return _info(
            audit=AuditOpinion(
                fiscal_year=2025,
                period_label="제10기 (당기)",
                auditor="한영회계법인",
                opinion="적정의견",
                kam_items=["수익인식 기간귀속의 적정성"],
            )
        )

    def test_llm_covering_kam_drops_deterministic_duplicate(self):
        """같은 논지를 프롬프트로 주고 결정론 문장도 붙이면 두 번 실린다."""
        info = self._info_with_kam()
        v = build_valuation(_metrics(), info)
        llm = ["감사인이 수익인식 기간귀속의 적정성을 지목한 만큼 확인이 필요하다."]
        merged = merge_risks(llm, v, info)
        assert sum("수익인식 기간귀속" in r for r in merged) == 1

    def test_uncovered_topic_is_kept(self):
        info = self._info_with_kam()
        v = build_valuation(_metrics(), info)
        merged = merge_risks(["전혀 다른 이야기"], v, info)
        assert any("수익인식 기간귀속" in r for r in merged)

    def test_scope_disclaimers_always_kept(self):
        """범위 고지는 LLM이 뭘 쓰든 남아야 한다."""
        info = self._info_with_kam()
        v = build_valuation(_metrics(), info)
        merged = merge_risks(["공시 기반 지표만 사용했다"], v, info)
        assert any("공시 밖 요인" in r for r in merged)
        assert any("추정 레이어" in r for r in merged)

    def test_llm_risks_come_first(self):
        info = self._info_with_kam()
        v = build_valuation(_metrics(), info)
        merged = merge_risks(["LLM이 쓴 첫 줄"], v, info)
        assert merged[0] == "LLM이 쓴 첫 줄"
