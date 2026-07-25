"""도메인 모델(arc.data.base) 검증 — 네트워크 불필요."""

import datetime as dt

import pytest
from pydantic import ValidationError

from arc.data.base import (
    Company,
    ConsolidationType,
    FinancialLineItem,
    FinancialStatement,
    Market,
    NewsItem,
    PeriodType,
    PricePoint,
    Provenance,
)

NOW = dt.datetime(2026, 7, 25, 12, 0, tzinfo=dt.timezone.utc)
PROV = Provenance(source="test", retrieved_at=NOW, source_url="https://example.com")


def test_provenance_is_frozen():
    with pytest.raises(ValidationError):
        PROV.source = "changed"  # frozen 모델은 수정 불가


def test_company_requires_provenance():
    """모든 도메인 모델은 provenance 없이 생성될 수 없다 (§4.2)."""
    with pytest.raises(ValidationError):
        Company(symbol="005930", name="삼성전자", market=Market.KOSPI)


def test_financial_statement_roundtrip():
    fs = FinancialStatement(
        symbol="078890",
        fiscal_year=2026,
        period=PeriodType.Q1,
        consolidation=ConsolidationType.CONSOLIDATED,
        items=[FinancialLineItem(account_name="매출액", amount=123_456_000_000)],
        rcept_no="20260515000123",
        provenance=PROV,
    )
    restored = FinancialStatement.model_validate(fs.model_dump())
    assert restored == fs
    assert restored.consolidation.value == "CFS"  # DART fs_div 값과 일치해야 함


def test_price_point_requires_close():
    with pytest.raises(ValidationError):
        PricePoint(symbol="078890", date=dt.date(2026, 7, 24), provenance=PROV)


def test_news_item_holds_snippet_only():
    """NewsItem은 본문 필드가 없어야 한다 — 스니펫만 저장 (§5.1)."""
    NewsItem(title="t", snippet="s", url="https://example.com/a", provenance=PROV)
    assert "body" not in NewsItem.model_fields
    assert "content" not in NewsItem.model_fields
