class TestStabilityRatios:
    """안정성 지표 — 증권사 「주요 투자지표」 표의 한 블록 (D58).

    코퍼스 189편 집계에서 「안정성/유동비율/부채비율/순차입금」이 증권사 9% ·
    학생 0%로 갈렸다. 재무제표를 이제 읽으므로 계산할 수 있다.
    """

    def _entries(self, **amounts):
        import datetime as dt

        from arc.data.base import (
            ConsolidationType,
            FinancialLineItem,
            FinancialStatement,
            PeriodType,
            Provenance,
        )
        from arc.finmodel.metrics import ACCOUNT_MAP, build_entries, extract_metrics

        prov = Provenance(source="opendart", retrieved_at=dt.datetime(2026, 8, 6, tzinfo=dt.UTC))
        items = [
            FinancialLineItem(
                account_id=ACCOUNT_MAP[k]["account_ids"][0],
                account_name=k,
                amount=v,
                prior_amount=v,
                prior2_amount=None,
                currency="KRW",
                statement_type="BS",
            )
            for k, v in amounts.items()
        ]
        stmt = FinancialStatement(
            symbol="000000",
            fiscal_year=2025,
            period=PeriodType.ANNUAL,
            consolidation=ConsolidationType.CONSOLIDATED,
            items=items,
            provenance=prov,
        )
        return {e.key: e for e in build_entries(extract_metrics(stmt), prov)}

    def test_current_ratio(self):
        got = self._entries(current_assets=200, current_liabilities=100)
        assert got["current_ratio_2025a"].value == 200.0

    def test_debt_ratio(self):
        got = self._entries(total_liabilities=50, total_equity=100)
        assert got["debt_ratio_2025a"].value == 50.0

    def test_missing_denominator_makes_no_entry(self):
        """분모가 없으면 만들지 않는다. 0으로 채우면 거짓이 된다."""
        assert "current_ratio_2025a" not in self._entries(current_assets=200)

    def test_formula_is_recorded(self):
        """감사 추적 — 어떻게 나온 값인지 남아야 한다."""
        got = self._entries(total_liabilities=50, total_equity=100)
        assert got["debt_ratio_2025a"].formula == "부채총계 / 자본총계"

    def test_net_debt_is_not_guessed(self):
        """**순차입금은 아직 안 낸다.** 차입금 계정명이 회사마다 달라
        (`유동성장기차입금` · `사채 및 장기차입금`) 합산을 놓치면 부채가 적어
        보이는 방향으로 틀린다. 틀린 순차입금은 없느니만 못하다."""
        got = self._entries(total_liabilities=50, total_equity=100, cash=30)
        assert not any("net_debt" in k for k in got)
