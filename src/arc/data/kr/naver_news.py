"""네이버 뉴스 검색 API 어댑터.

- 일 25,000회. **스니펫(title/description)만 수집** — 본문 크롤링 금지
  (ARCHITECTURE.md §5.1: 스니펫 + 공시 원문만 근거로 사용).
- 인증: 환경변수 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET.

창구가 두 개다 (2026-07-31 이관)
--------------------------------
네이버가 검색 API를 개발자센터에서 **NAVER API HUB**(네이버 클라우드 플랫폼)로
옮겼다. 일정은 이렇다:

| 시점 | 내용 |
|---|---|
| 2026-06-25 | NAVER API HUB 정식 출시 |
| 2026-07-31 | 개발자센터에서 **신규 신청 차단** |
| 2027-06-30 | 개발자센터 지원 종료 (기존 키도 차단) |

**지금 새로 받으면 API HUB 키다.** 그래서 그쪽이 기본값이다. 응답 본문은
양쪽이 같고 **엔드포인트와 인증 헤더만 다르다** — 그래서 파싱은 하나로 둔다.

| | 개발자센터 (구) | API HUB (신) |
|---|---|---|
| 주소 | `openapi.naver.com/v1/search` | `naverapihub.apigw.ntruss.com/search/v1` |
| 경로 | `/news.json` | `/news` |
| 헤더 | `X-Naver-Client-Id` / `-Secret` | `X-NCP-APIGW-API-KEY-ID` / `-KEY` |
| 계정 | 네이버 | NCP |

2027-06-30 이전에 발급한 구 키를 아직 쓴다면 `NAVER_API_MODE=legacy`.
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

HUB_URL = "https://naverapihub.apigw.ntruss.com/search/v1"
LEGACY_URL = "https://openapi.naver.com/v1/search"
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
        mode: str | None = None,
    ) -> None:
        self.client_id = client_id or os.environ.get("NAVER_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("NAVER_CLIENT_SECRET", "")
        if not (self.client_id and self.client_secret):
            raise ValueError("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET가 설정되지 않았습니다")
        self.mode = (mode or os.environ.get("NAVER_API_MODE", "hub")).lower()
        base = LEGACY_URL if self.mode == "legacy" else HUB_URL
        self._client = client or httpx.Client(base_url=base, timeout=30.0)

    @property
    def _path(self) -> str:
        return "/news.json" if self.mode == "legacy" else "/news"

    @property
    def _headers(self) -> dict[str, str]:
        if self.mode == "legacy":
            return {
                "X-Naver-Client-Id": self.client_id,
                "X-Naver-Client-Secret": self.client_secret,
            }
        return {
            "X-NCP-APIGW-API-KEY-ID": self.client_id,
            "X-NCP-APIGW-API-KEY": self.client_secret,
        }

    def get_news(self, query: str, limit: int = 20) -> list[NewsItem]:
        resp = self._client.get(
            self._path,
            # **정확도순이다.** 최신순으로 100건을 받으면 대형주는 오늘
            # 하루치만 온다 — 실측(삼성물산): `date`는 제목에 회사명이 있는
            # 기사가 11/100, `sim`은 80/100이었다. 날짜는 필터가 자른다.
            params={"query": query, "display": str(min(limit, 100)), "sort": "sim"},
            headers=self._headers,
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
