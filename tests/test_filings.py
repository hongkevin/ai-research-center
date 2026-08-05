"""공시 목록 → 실제로 존재하는 정기보고서.

기한으로 계산한 목록은 **추측**이다 — 결산월이 다르고, 일찍 낼 수도 늦을 수도
있고, 아예 없을 수도 있다. DART에 뭐가 올라와 있는지 물어보면 목록이 사실이 된다.
"""

from __future__ import annotations

import datetime as dt

from arc.data.base import Disclosure, PeriodType, Provenance
from arc.data.kr.filings import (
    classify,
    is_preliminary,
    periodic_filings,
    preliminary_filings,
)

PROV = Provenance(source="opendart", retrieved_at=dt.datetime(2026, 8, 5, tzinfo=dt.UTC))


def _d(title: str, day: str, rcept: str = "20260101000001") -> Disclosure:
    return Disclosure(
        symbol="005930",
        rcept_no=rcept,
        title=title,
        filed_at=dt.date.fromisoformat(day),
        provenance=PROV.model_copy(update={"verify_url": f"https://dart.fss.or.kr/{rcept}"}),
    )


class TestClassify:
    def test_annual(self):
        assert classify("사업보고서 (2025.12)") == (2025, PeriodType.ANNUAL)

    def test_half(self):
        assert classify("반기보고서 (2026.06)") == (2026, PeriodType.HALF)

    def test_quarter_is_split_by_month(self):
        """분기보고서는 이름만으로 1분기·3분기를 가를 수 없다 — 월을 본다."""
        assert classify("분기보고서 (2026.03)") == (2026, PeriodType.Q1)
        assert classify("분기보고서 (2025.09)") == (2025, PeriodType.Q3)

    def test_non_periodic_is_none(self):
        assert classify("주요사항보고서(자기주식취득결정)") is None
        assert classify("연결재무제표기준영업(잠정)실적(공정공시)") is None


class TestPreliminary:
    def test_detects_preliminary_results(self):
        """**리포트는 여기 붙는다.** 정기보고서보다 먼저 온다."""
        assert is_preliminary("연결재무제표기준영업(잠정)실적(공정공시)")
        assert is_preliminary("매출액또는손익구조30%(대규모법인은15%)이상변동")

    def test_periodic_report_is_not_preliminary(self):
        assert not is_preliminary("사업보고서 (2025.12)")


class TestList:
    def test_only_periodic_survives_and_newest_first(self):
        out = periodic_filings(
            [
                _d("사업보고서 (2025.12)", "2026-03-20"),
                _d("주요사항보고서(유상증자결정)", "2026-04-01"),
                _d("분기보고서 (2026.03)", "2026-05-14"),
                _d("연결재무제표기준영업(잠정)실적(공정공시)", "2026-01-30"),
            ]
        )
        assert [f.label for f in out] == ["2026 1분기보고서", "2025 사업보고서"]

    def test_amended_filing_replaces_the_original(self):
        """정정신고가 올라오면 원본과 함께 뜬다. 최근 제출본만 남긴다."""
        out = periodic_filings(
            [
                _d("사업보고서 (2025.12)", "2026-03-20", "A"),
                _d("[기재정정]사업보고서 (2025.12)", "2026-04-10", "B"),
            ]
        )
        assert len(out) == 1
        assert out[0].rcept_no == "B"

    def test_preliminary_is_kept_separately(self):
        """우리는 아직 잠정실적을 읽지 못한다. 하지만 **있다는 사실은 알려야** 한다."""
        ds = [
            _d("사업보고서 (2025.12)", "2026-03-20"),
            _d("연결재무제표기준영업(잠정)실적(공정공시)", "2026-01-30"),
        ]
        assert [d.filed_at.isoformat() for d in preliminary_filings(ds)] == ["2026-01-30"]

    def test_filing_carries_the_viewer_link(self):
        out = periodic_filings([_d("사업보고서 (2025.12)", "2026-03-20", "20260320000123")])
        assert out[0].url.endswith("20260320000123")
        assert out[0].filed_at == dt.date(2026, 3, 20)
