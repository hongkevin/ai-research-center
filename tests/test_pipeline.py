"""파이프라인 관통 테스트 (네트워크 없이 페이크 provider로).

실측에서 나온 두 가지를 회귀로 고정한다:
  1. 연결(CFS) 미제출 소형주 → 별도(OFS) 자동 폴백
  2. 금융업처럼 표준 지표가 없는 회사는 커버리지 부족을 정직하게 드러낸다
"""

from __future__ import annotations

import datetime as dt

import pytest

from arc.data.base import (
    Company,
    ConsolidationType,
    DataProvider,
    FinancialLineItem,
    FinancialStatement,
    Market,
    Provenance,
)
from arc.llm.number_registry import NumberRegistry
from arc.pipeline.earnings_review import build_report, fetch_statement

PROV = Provenance(
    source="opendart",
    retrieved_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC),
    source_ref="20260310002820",
)


def _items(rev, op, ni, rev_p, op_p, ni_p):
    return [
        FinancialLineItem(
            account_id="ifrs-full_Revenue",
            account_name="매출액",
            amount=rev,
            prior_amount=rev_p,
            statement_type="IS",
        ),
        FinancialLineItem(
            account_id="ifrs-full_CostOfSales",
            account_name="매출원가",
            amount=int(rev * 0.7),
            prior_amount=int(rev_p * 0.72),
            statement_type="IS",
        ),
        FinancialLineItem(
            account_id="ifrs-full_GrossProfit",
            account_name="매출총이익",
            amount=rev - int(rev * 0.7),
            prior_amount=rev_p - int(rev_p * 0.72),
            statement_type="IS",
        ),
        FinancialLineItem(
            account_id="dart_OperatingIncomeLoss",
            account_name="영업이익",
            amount=op,
            prior_amount=op_p,
            statement_type="IS",
        ),
        FinancialLineItem(
            account_id="ifrs-full_ProfitLoss",
            account_name="당기순이익",
            amount=ni,
            prior_amount=ni_p,
            statement_type="IS",
        ),
    ]


class FakeProvider(DataProvider):
    """연결 제출 여부를 제어할 수 있는 페이크."""

    def __init__(self, *, has_consolidated: bool, items=None):
        self.has_consolidated = has_consolidated
        self.items = (
            items
            if items is not None
            else _items(
                1_000_000_000_000,
                100_000_000_000,
                70_000_000_000,
                900_000_000_000,
                81_000_000_000,
                60_000_000_000,
            )
        )
        self.calls: list[ConsolidationType] = []

    def get_company(self, symbol):
        return Company(symbol=symbol, name="테스트기업", market=Market.KOSDAQ, provenance=PROV)

    def get_financials(
        self, symbol, fiscal_year, period, consolidation=ConsolidationType.CONSOLIDATED
    ):
        self.calls.append(consolidation)
        if consolidation is ConsolidationType.CONSOLIDATED and not self.has_consolidated:
            raise RuntimeError("013 조회된 데이타가 없습니다.")
        return FinancialStatement(
            symbol=symbol,
            fiscal_year=fiscal_year,
            period=period,
            consolidation=consolidation,
            items=self.items,
            rcept_no="20260310002820",
            provenance=PROV,
        )

    def get_prices(self, symbol, start, end):
        raise NotImplementedError

    def get_disclosures(self, symbol, start, end):
        raise NotImplementedError

    def get_news(self, query, limit=20):
        raise NotImplementedError


# ── 연결 → 별도 폴백 ─────────────────────────────────────────────────
class TestConsolidationFallback:
    def test_uses_consolidated_when_available(self):
        p = FakeProvider(has_consolidated=True)
        stmt = fetch_statement("000000", 2025, p)
        assert stmt.consolidation is ConsolidationType.CONSOLIDATED
        assert p.calls == [ConsolidationType.CONSOLIDATED]

    def test_falls_back_to_separate(self):
        """소형주는 연결을 제출하지 않는 경우가 많다 — 실측에서 확인된 실패 모드."""
        p = FakeProvider(has_consolidated=False)
        stmt = fetch_statement("000000", 2025, p)
        assert stmt.consolidation is ConsolidationType.SEPARATE
        assert p.calls == [ConsolidationType.CONSOLIDATED, ConsolidationType.SEPARATE]

    def test_explicit_consolidation_does_not_fall_back(self):
        p = FakeProvider(has_consolidated=False)
        with pytest.raises(RuntimeError):
            fetch_statement("000000", 2025, p, consolidation=ConsolidationType.CONSOLIDATED)

    def test_separate_basis_disclosed_in_report(self):
        """별도 사용 사실을 본문에 밝힌다 — 종속회사 실적이 빠진 수치이므로."""
        r = build_report(
            "000000", 2025, FakeProvider(has_consolidated=False), published_at=dt.date(2026, 8, 1)
        )
        assert r.gate.passed
        assert "별도재무제표" in (r.rendered or "")
        assert "종속회사 실적이 반영되지 않은" in (r.rendered or "")


# ── 관통 ─────────────────────────────────────────────────────────────
class TestVerticalSlice:
    def _report(self):
        return build_report(
            "000000", 2025, FakeProvider(has_consolidated=True), published_at=dt.date(2026, 8, 1)
        )

    def test_gate_passes(self):
        r = self._report()
        assert r.gate.passed, [v.detail for v in r.gate.violations]
        assert r.publishable

    def test_assembled_has_placeholders_rendered_has_none(self):
        r = self._report()
        assert NumberRegistry.extract_keys(r.assembled), "조립본에 플레이스홀더가 있어야 한다"
        assert not NumberRegistry.extract_keys(r.rendered or ""), "최종본에 미치환이 남으면 안 된다"

    def test_numbers_are_registry_values(self):
        r = self._report()
        assert "1조원" in (r.rendered or "")  # revenue 1,000,000,000,000
        assert "11.1%" in (r.rendered or "")  # revenue YoY
        assert "10.0%" in (r.rendered or "")  # operating margin

    def test_bindings_recorded_for_audit(self):
        r = self._report()
        assert len(r.bindings) == len(NumberRegistry.extract_keys(r.assembled))
        assert all(b["provenance"]["source"] == "opendart" for b in r.bindings)

    def test_all_required_sections_present(self):
        rendered = self._report().rendered or ""
        for s in (
            "요약",
            "투자포인트",
            "실적 분석",
            "실적 추정",
            "밸류에이션",
            "리스크",
            "디스클레이머",
        ):
            assert s in rendered

    def test_no_rating_language_in_body(self):
        """§3 불변식 1 — 본문에 rating·목표주가가 없어야 한다."""
        body = (self._report().rendered or "").split("## 8. 디스클레이머")[0]
        for banned in ("목표주가", "투자의견", "상승여력", "Buy", "매수 추천"):
            assert banned not in body


# ── 커버리지 부족 (금융업 등) ────────────────────────────────────────
class TestInsufficientCoverage:
    def test_financial_holding_lacks_standard_metrics(self):
        """금융지주는 매출·매출원가가 없다. 억지로 채우지 않고 드러낸다 (원칙 3).

        실측: JB금융지주 — revenue/cost_of_sales/gross_profit 전부 미매핑.
        """
        items = [
            FinancialLineItem(
                account_id="ifrs-full_ProfitLoss",
                account_name="당기순이익",
                amount=500_000_000_000,
                prior_amount=450_000_000_000,
                statement_type="IS",
            ),
        ]
        p = FakeProvider(has_consolidated=True, items=items)
        r = build_report("000000", 2025, p, published_at=dt.date(2026, 8, 1))

        assert not r.metrics.coverage_ok
        assert "revenue" in r.metrics.missing
        assert "찾지 못한 지표" in (r.rendered or r.assembled)


# ── 수치 출처 (D36) ──────────────────────────────────────────────────
def test_source_table_lists_only_numbers_that_appear_in_the_body():
    """「수치 출처」 표는 본문에 실제로 등장한 키만 싣는다. 등록됐지만 안 쓰인
    수치까지 실으면 독자가 리포트에 없는 값을 찾게 된다."""
    import datetime as dt

    from arc.data.base import Provenance
    from arc.llm.number_registry import NumberEntry, NumberRegistry
    from arc.pipeline.earnings_review import _source_rows

    prov = Provenance(
        source="opendart",
        retrieved_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC),
        dataset="재무제표 (전체계정)",
        verify_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260319001417",
        source_ref="20260319001417",
    )
    reg = NumberRegistry()
    reg.register_all(
        [
            NumberEntry(
                key="a_2025a", value=1, unit="원", display="1원", provenance=prov, label="A"
            ),
            NumberEntry(
                key="b_2025a", value=2, unit="원", display="2원", provenance=prov, label="B"
            ),
            NumberEntry(
                key="c_2025a",
                value=3,
                unit="%",
                display="3%",
                provenance=prov,
                label="C",
                internal=True,
            ),
        ]
    )
    rows = _source_rows("본문 {{num:a_2025a}} 과 {{num:c_2025a}}", reg)
    # b는 본문에 없고, c는 감사용(internal)이라 뺀다 (D17)
    assert [r["label"] for r in rows] == ["A"]
    assert rows[0]["value"] == "{{num:a_2025a}}"
    assert "재무제표 (전체계정)" == rows[0]["source"]
    assert "dsaf001" in rows[0]["document"]


def test_source_table_escapes_pipes_in_formulas():
    """산식의 `|전기|`를 그대로 두면 마크다운 표의 셀 구분자로 해석돼 뒤 열이
    통째로 밀린다."""
    import datetime as dt

    from arc.data.base import Provenance
    from arc.llm.number_registry import NumberEntry, NumberRegistry
    from arc.pipeline.earnings_review import _source_rows

    prov = Provenance(source="opendart", retrieved_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC))
    reg = NumberRegistry()
    reg.register_all(
        [
            NumberEntry(
                key="x_2025a",
                value=1,
                unit="%",
                display="1%",
                provenance=prov,
                label="X",
                formula="(당기 - 전기) / |전기|",
            )
        ]
    )
    assert _source_rows("{{num:x_2025a}}", reg)[0]["formula"] == r"(당기 - 전기) / \|전기\|"


class TestStageReports:
    """파이프라인을 여는 기록.

    이 시스템은 종목코드를 넣으면 30초 뒤 완성본을 뱉는 블랙박스였다. 중간에
    무엇을 검산했고 무엇을 **못 구했는지**가 화면에 나오지 않으면, 검토자는
    없는 것과 실패한 것을 구분할 수 없다.
    """

    def _report(self):
        return build_report(
            "000000", 2025, FakeProvider(has_consolidated=True), published_at=dt.date(2026, 8, 1)
        )

    def test_stages_are_recorded_in_order(self):
        stages = self._report().stages
        keys = [s.key for s in stages]
        assert keys[:3] == ["company", "statement", "metrics"]
        # 게이트는 마지막이다 — 조립본이 있어야 검사할 수 있다
        assert keys[-1] == "gate"

    def test_every_stage_says_what_it_did(self):
        for s in self._report().stages:
            assert s.label, f"{s.key}에 이름이 없다"
            assert s.summary or s.note, f"{s.key}가 아무것도 말하지 않는다"

    def test_registry_contributions_are_attributed(self):
        """어느 단계가 숫자를 만들었는지가 파이프라인을 여는 핵심 정보다."""
        r = self._report()
        by_key = {s.key: s for s in r.stages}
        assert by_key["metrics"].registered > 0
        # 합계가 레지스트리 크기를 넘을 수 없다 (같은 항목을 두 번 세면 넘는다)
        assert sum(s.registered for s in r.stages) <= len(r.registry)

    def test_absent_is_not_failed(self):
        """**없는 것과 실패한 것은 다르다.**

        단일 부문 회사에 부문 손익이 없는 건 정상이고(D33이 정확히 거부한다),
        DART 조회 실패는 결함이다. 같은 색으로 칠하면 정상을 결함으로 읽는다.
        """
        stages = self._report().stages
        assert all(s.status in {"ok", "partial", "absent", "failed"} for s in stages)
        # 픽스처는 원문을 주지 않으므로 부문/사업 단계는 아예 열리지 않거나
        # absent다 — 어느 쪽이든 failed면 안 된다
        for s in stages:
            if s.key in {"segments", "segment_profit", "business"}:
                assert s.status != "failed", f"{s.key}가 정상 부재를 실패로 표시했다"

    def test_gate_stage_carries_the_verdict(self):
        r = self._report()
        gate_stage = next(s for s in r.stages if s.key == "gate")
        assert (gate_stage.status == "failed") is not r.gate.passed


class TestPeriodicInfoCheck:
    """**없는 것과 틀린 것을 구분한다** (D39·D52).

    실측: 삼성전기 분기 카드가 「0/6종」인데 「발행주식 − 자기주식 = 유통주식
    불일치」를 띄웠다. `shares_reconciled`의 기본값이 `False`라 자료를 아예
    못 받아도 어긋난 것처럼 보였다. RA는 있지도 않은 불일치를 찾으러 갔다.
    """

    def test_no_share_data_means_no_check(self):
        """검산할 자료가 없으면 **검산 결과도 없다.**"""
        from arc.pipeline.earnings_review import _shares_detail

        class Sh:
            issued = treasury = outstanding = None
            reconciled = False

        assert _shares_detail(Sh()) == "확인 불가"

    def test_matching_shares_show_the_numbers(self):
        """「일치」만 쓰면 무엇이 일치했는지 모른다."""
        from arc.pipeline.earnings_review import _shares_detail

        class Sh:
            issued, treasury, outstanding = 77_600_680, 2_053_430, 75_547_250
            reconciled = True

        assert _shares_detail(Sh()) == "77,600,680 − 2,053,430 = 75,547,250"

    def test_mismatch_shows_the_gap_so_it_can_be_acted_on(self):
        """「불일치」 한 마디로는 어디를 볼지 알 수 없다. 차이를 적는다."""
        from arc.pipeline.earnings_review import _shares_detail

        class Sh:
            issued, treasury, outstanding = 100_000, 5_000, 90_000
            reconciled = False

        got = _shares_detail(Sh())
        assert "95,000" in got and "+5,000주" in got


class TestHeaderRows:
    """헤더는 **줄마다 따로 확인한다** (D63).

    실측: 분기 카드에서 KRX 종가는 있는데 주식수가 0/6종이라 `market_cap`이
    등록되지 않았고, 헤더가 그 플레이스홀더를 참조해 게이트가
    `unknown_placeholder`로 막았다. D61에서 주가 출처를 배당 공시에서 시세로
    옮기며 생긴 회귀다 — 전에는 주가와 주식수가 같은 공시에서 함께 왔다.
    """

    def _rows(self, registered: set[str], *, implied: bool = False):
        import datetime as dt

        from arc.data.base import (
            Company,
            ConsolidationType,
            Market,
            PeriodType,
            Provenance,
        )
        from arc.finmodel.metrics import MetricSet
        from arc.finmodel.valuation import ValuationSet
        from arc.pipeline.earnings_review import _header_rows

        v = ValuationSet(fiscal_year=2026)
        v.price = 49_000
        v.is_implied = implied
        v.price_date = None if implied else "2026-08-05"
        company = Company(
            symbol="489790",
            name="한화비전(주)",
            market=Market.KOSPI,
            provenance=Provenance(
                source="opendart", retrieved_at=dt.datetime(2026, 8, 6, tzinfo=dt.UTC)
            ),
        )
        return _header_rows(
            company,
            MetricSet(fiscal_year=2026),
            published_at=dt.date(2026, 8, 6),
            period=PeriodType.Q1,
            consolidation=ConsolidationType.CONSOLIDATED,
            valuation=v,
            info=None,
            statement=None,
            has=registered.__contains__,
        )

    def test_price_without_market_cap_does_not_emit_the_missing_row(self):
        """**주가가 있다고 시가총액이 있는 것은 아니다** — 시가총액에는
        발행주식수가 필요한데 그건 사업보고서에만 실린다."""
        labels = [r["label"] for r in self._rows({"price_2026a"})]
        assert any("주가" in x for x in labels)
        assert "시가총액" not in labels

    def test_both_rows_when_both_registered(self):
        labels = [r["label"] for r in self._rows({"price_2026a", "market_cap_2026a"})]
        assert "시가총액" in labels and any("주가" in x for x in labels)

    def test_neither_row_when_nothing_registered(self):
        labels = [r["label"] for r in self._rows(set())]
        assert "시가총액" not in labels and not any("주가" in x for x in labels)

    def test_price_date_is_shown(self):
        """날짜 없는 PER은 검증할 수 없다 (D61)."""
        labels = [r["label"] for r in self._rows({"price_2026a"})]
        assert "주가 (2026-08-05)" in labels

    def test_implied_price_says_so(self):
        labels = [r["label"] for r in self._rows({"price_2026a"}, implied=True)]
        assert "역산 주가" in labels
