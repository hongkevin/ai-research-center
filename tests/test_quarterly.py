"""분기 시계열 — 누적에서 단독 분기를 뽑는다.

DART는 누적만 준다. 단독 분기는 빼서 만드는데, 뺄셈은 두 값이 다 있어야
성립한다. **한 칸이라도 비면 그 분기를 만들지 않는다** — 아직 안 나온
보고서로 분기를 지어내면 거짓이 된다.
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
from arc.finmodel.quarterly import build_quarter_entries, build_quarters

NOW = dt.datetime(2026, 8, 6, tzinfo=dt.UTC)
PROV = Provenance(source="opendart", retrieved_at=NOW)

# 누적 (당기, 전기) — 억원 단위를 원으로
CUM = {
    PeriodType.Q1: (100, 90),
    PeriodType.HALF: (210, 190),
    PeriodType.Q3: (330, 300),
    PeriodType.ANNUAL: (460, 420),
}


class FakeProvider:
    """누적만 주는 DART 흉내. 없는 보고서는 예외를 던진다."""

    def __init__(self, available=tuple(CUM), scale: int = 100_000_000):
        self.available = set(available)
        self.scale = scale
        self.calls = 0

    def get_financials(self, symbol, year, period, consolidation):
        self.calls += 1
        if period not in self.available:
            raise RuntimeError("아직 안 나온 보고서")
        cur, prior = CUM[period]
        return FinancialStatement(
            symbol=symbol,
            fiscal_year=year,
            period=period,
            consolidation=consolidation,
            items=[
                FinancialLineItem(
                    account_id="ifrs-full_Revenue",
                    account_name="수익(매출액)",
                    amount=cur * self.scale,
                    prior_amount=prior * self.scale,
                    prior2_amount=None,
                    currency="KRW",
                    statement_type="IS",
                )
            ],
            provenance=PROV,
        )


def _revenue(series) -> list[int | None]:
    return [None if v is None else v // 100_000_000 for v in series.metric_row("revenue")]


class TestDerivation:
    def test_four_calls_give_eight_quarters(self):
        """**각 보고서가 전년 동기 누적을 함께 준다.** 8번 부를 필요가 없다."""
        p = FakeProvider()
        s = build_quarters("000000", 2025, p, metrics=("revenue",))
        assert p.calls == 4
        assert len(s.points) == 8

    def test_standalone_quarters_come_from_subtraction(self):
        s = build_quarters("000000", 2025, FakeProvider(), metrics=("revenue",))
        # 전년 4분기(90/100/110/120) + 당기 4분기(100/110/120/130)
        assert _revenue(s) == [90, 100, 110, 120, 100, 110, 120, 130]

    def test_labels_are_quarter_and_year(self):
        s = build_quarters("000000", 2025, FakeProvider(), metrics=("revenue",))
        assert [p.label for p in s.points] == [
            "1Q24",
            "2Q24",
            "3Q24",
            "4Q24",
            "1Q25",
            "2Q25",
            "3Q25",
            "4Q25",
        ]

    def test_oldest_first(self):
        s = build_quarters("000000", 2025, FakeProvider(), metrics=("revenue",))
        assert s.points[0].year < s.points[-1].year


class TestMissingReports:
    def test_missing_half_year_drops_the_quarters_that_need_it(self):
        """**반기가 없으면 2·3분기를 만들 수 없다.** 지어내지 않는다."""
        p = FakeProvider(available=(PeriodType.Q1, PeriodType.Q3, PeriodType.ANNUAL))
        s = build_quarters("000000", 2025, p, metrics=("revenue",))
        labels = [x.label for x in s.points]
        assert "2Q25" not in labels and "3Q25" not in labels
        assert "1Q25" in labels and "4Q25" in labels

    def test_only_q1_is_not_usable(self):
        """두 분기로는 추세가 안 보인다."""
        p = FakeProvider(available=(PeriodType.Q1,))
        s = build_quarters("000000", 2025, p, metrics=("revenue",))
        assert not s.usable

    def test_failures_are_recorded_not_swallowed(self):
        p = FakeProvider(available=(PeriodType.Q1,))
        s = build_quarters("000000", 2025, p, metrics=("revenue",))
        assert len(s.problems) == 3


class TestEntries:
    def test_registry_keys_carry_the_quarter(self):
        """**레지스트리를 거쳐야 본문에 쓸 수 있다** (불변식 1)."""
        s = build_quarters("000000", 2025, FakeProvider(), metrics=("revenue",))
        keys = {e.key for e in build_quarter_entries(s, PROV)}
        assert "revenue_1q25" in keys and "revenue_4q24" in keys

    def test_derived_quarters_say_how(self):
        s = build_quarters("000000", 2025, FakeProvider(), metrics=("revenue",))
        by = {e.key: e for e in build_quarter_entries(s, PROV)}
        assert by["revenue_1q25"].formula is None  # 1Q는 누적 그대로
        assert by["revenue_2q25"].formula == "누적 − 직전 누적"

    def test_negative_quarter_is_kept(self):
        """4분기에 비용을 몰아 넣으면 실제로 음수가 된다. 그게 사실이다."""
        import arc.finmodel.quarterly as q

        original = dict(CUM)
        try:
            CUM[PeriodType.ANNUAL] = (300, 420)  # 연간 < 3분기 누적
            s = build_quarters("000000", 2025, FakeProvider(), metrics=("revenue",))
            assert _revenue(s)[-1] == -30
        finally:
            CUM.clear()
            CUM.update(original)
            assert q  # 사용 표시
