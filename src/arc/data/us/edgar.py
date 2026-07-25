"""SEC EDGAR 어댑터 스텁 (v2).

ARCHITECTURE.md §5.1 / D2: 아키텍처는 KR+US 추상화, 콘텐츠는 KR 우선.
EDGAR는 퍼블릭 도메인이라 재배포 안전 — v2에서 구현한다.

TODO(v2):
  - company_tickers.json으로 ticker → CIK 매핑
  - companyfacts API(XBRL)로 재무제표 수집
  - full-text search / submissions API로 공시 목록
  - User-Agent 헤더 정책(SEC fair access) 준수, 초당 10요청 제한
"""

from __future__ import annotations

import datetime as dt

from arc.data.base import (
    Company,
    ConsolidationType,
    DataProvider,
    Disclosure,
    FinancialStatement,
    NewsItem,
    PeriodType,
    PricePoint,
)

SOURCE = "edgar"


class EdgarProvider(DataProvider):
    """SEC EDGAR 어댑터 — v2 스텁. 모든 메서드 미구현."""

    def get_company(self, symbol: str) -> Company:
        raise NotImplementedError("EDGAR 어댑터는 v2에서 구현 예정")

    def get_financials(
        self,
        symbol: str,
        fiscal_year: int,
        period: PeriodType,
        consolidation: ConsolidationType = ConsolidationType.CONSOLIDATED,
    ) -> FinancialStatement:
        raise NotImplementedError("EDGAR 어댑터는 v2에서 구현 예정")

    def get_prices(self, symbol: str, start: dt.date, end: dt.date) -> list[PricePoint]:
        raise NotImplementedError("EDGAR 어댑터는 v2에서 구현 예정")

    def get_disclosures(self, symbol: str, start: dt.date, end: dt.date) -> list[Disclosure]:
        raise NotImplementedError("EDGAR 어댑터는 v2에서 구현 예정")

    def get_news(self, query: str, limit: int = 20) -> list[NewsItem]:
        raise NotImplementedError("EDGAR 어댑터는 v2에서 구현 예정")
