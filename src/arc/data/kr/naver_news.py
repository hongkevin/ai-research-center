"""네이버 뉴스 검색 API 어댑터.

- 일 25,000회. **스니펫(title/description)만 수집** — 본문 크롤링 금지
  (ARCHITECTURE.md §5.1: 스니펫 + 공시 원문만 근거로 사용).
- 인증: 환경변수 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET.
"""

from __future__ import annotations

import datetime as dt
import html
import os
import re
from email.utils import parsedate_to_datetime

import httpx

from arc.data.base import (
    Company,
    ConsolidationType,
    DataProvider,
    Disclosure,
    FinancialStatement,
    NewsItem,
    PeriodType,
    PricePoint,
    Provenance,
)

BASE_URL = "https://openapi.naver.com/v1/search"
SOURCE = "naver_news"

_TAG_RE = re.compile(r"</?b>")


def _clean(text: str) -> str:
    """네이버 검색 API의 <b> 하이라이트 태그·HTML 엔티티 제거."""
    return html.unescape(_TAG_RE.sub("", text))


class NaverNewsProvider(DataProvider):
    """네이버 뉴스 검색 API 어댑터 — 스니펫 전용."""

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.client_id = client_id or os.environ.get("NAVER_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("NAVER_CLIENT_SECRET", "")
        if not (self.client_id and self.client_secret):
            raise ValueError("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET가 설정되지 않았습니다")
        self._client = client or httpx.Client(base_url=BASE_URL, timeout=30.0)

    def get_news(self, query: str, limit: int = 20) -> list[NewsItem]:
        resp = self._client.get(
            "/news.json",
            params={"query": query, "display": str(min(limit, 100)), "sort": "date"},
            headers={
                "X-Naver-Client-Id": self.client_id,
                "X-Naver-Client-Secret": self.client_secret,
            },
        )
        resp.raise_for_status()
        return self.parse_news(resp.json(), retrieved_at=dt.datetime.now(dt.UTC))[:limit]

    @staticmethod
    def parse_news(payload: dict, retrieved_at: dt.datetime) -> list[NewsItem]:
        """news.json 응답 → NewsItem 목록 (순수 파싱, 테스트 대상)."""
        out: list[NewsItem] = []
        for row in payload.get("items", []):
            url = row.get("originallink") or row.get("link", "")
            published_at: dt.datetime | None = None
            if row.get("pubDate"):
                try:
                    published_at = parsedate_to_datetime(row["pubDate"])
                except (TypeError, ValueError):
                    published_at = None
            out.append(
                NewsItem(
                    title=_clean(row.get("title", "")),
                    snippet=_clean(row.get("description", "")),
                    url=url,
                    published_at=published_at,
                    provenance=Provenance(
                        source=SOURCE,
                        retrieved_at=retrieved_at,
                        source_url=url,
                    ),
                )
            )
        return out

    # ------------------------------------------------------------- 범위 밖

    def get_company(self, symbol: str) -> Company:
        raise NotImplementedError("기업개황은 kr/dart 어댑터를 사용하라")

    def get_financials(
        self,
        symbol: str,
        fiscal_year: int,
        period: PeriodType,
        consolidation: ConsolidationType = ConsolidationType.CONSOLIDATED,
    ) -> FinancialStatement:
        raise NotImplementedError("재무제표는 kr/dart 어댑터를 사용하라")

    def get_prices(self, symbol: str, start: dt.date, end: dt.date) -> list[PricePoint]:
        raise NotImplementedError("시세는 kr/krx_price 어댑터를 사용하라")

    def get_disclosures(self, symbol: str, start: dt.date, end: dt.date) -> list[Disclosure]:
        raise NotImplementedError("공시목록은 kr/dart 어댑터를 사용하라")
