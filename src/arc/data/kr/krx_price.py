"""금융위 주식시세정보 API 어댑터 (data.go.kr getStockPriceInfo).

- EOD 시세(D+1). "이용허락범위 제한 없음" — 재배포 안전 (ARCHITECTURE.md §5.1).
- 실적 리뷰 노트에는 EOD로 충분. 장중 시세는 다루지 않는다.
- 인증: 환경변수 KRX_API_KEY (data.go.kr 일반 인증키, serviceKey).
"""

from __future__ import annotations

import datetime as dt
import os

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

BASE_URL = "https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService"
SOURCE = "krx_price"


class KrxApiError(Exception):
    """data.go.kr 응답 resultCode != '00'."""


class KrxPriceProvider(DataProvider):
    """금융위 주식시세정보 API 어댑터 — EOD 시세 전용."""

    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None) -> None:
        self.api_key = api_key or os.environ.get("KRX_API_KEY", "")
        if not self.api_key:
            raise ValueError("KRX_API_KEY가 설정되지 않았습니다 (.env 참조)")
        self._client = client or httpx.Client(base_url=BASE_URL, timeout=30.0)

    def get_prices(self, symbol: str, start: dt.date, end: dt.date) -> list[PricePoint]:
        """기간 내 EOD 시세 조회. 페이지네이션 처리 포함."""
        points: list[PricePoint] = []
        page_no = 1
        num_rows = 500
        while True:
            resp = self._client.get(
                "/getStockPriceInfo",
                params={
                    "serviceKey": self.api_key,
                    "resultType": "json",
                    "likeSrtnCd": symbol,
                    "beginBasDt": start.strftime("%Y%m%d"),
                    "endBasDt": (end + dt.timedelta(days=1)).strftime("%Y%m%d"),
                    "numOfRows": str(num_rows),
                    "pageNo": str(page_no),
                },
            )
            resp.raise_for_status()
            payload = resp.json()
            body = self._check_body(payload)
            points.extend(self.parse_price_items(body, retrieved_at=dt.datetime.now(dt.UTC)))
            total_count = int(body.get("totalCount", 0))
            if page_no * num_rows >= total_count:
                break
            page_no += 1
        # API는 단축코드 like 검색이므로 정확히 일치하는 종목만 남긴다
        return [p for p in points if p.symbol == symbol]

    @staticmethod
    def _check_body(payload: dict) -> dict:
        header = payload.get("response", {}).get("header", {})
        if header.get("resultCode") != "00":
            raise KrxApiError(
                f"{header.get('resultCode')}: {header.get('resultMsg', 'unknown error')}"
            )
        return payload.get("response", {}).get("body", {})

    @staticmethod
    def parse_price_items(body: dict, retrieved_at: dt.datetime) -> list[PricePoint]:
        """응답 body → PricePoint 목록 (순수 파싱, 테스트 대상)."""
        items = body.get("items", {}).get("item", [])
        if isinstance(items, dict):  # 결과 1건이면 dict로 오는 data.go.kr 특성
            items = [items]
        out: list[PricePoint] = []
        for row in items:
            out.append(
                PricePoint(
                    symbol=row.get("srtnCd", ""),
                    date=dt.datetime.strptime(row["basDt"], "%Y%m%d").date(),
                    open=_to_float(row.get("mkp")),
                    high=_to_float(row.get("hipr")),
                    low=_to_float(row.get("lopr")),
                    close=_to_float(row["clpr"]),
                    volume=_to_int(row.get("trqu")),
                    market_cap=_to_int(row.get("mrktTotAmt")),
                    shares_outstanding=_to_int(row.get("lstgStCnt")),
                    provenance=Provenance(
                        source=SOURCE,
                        retrieved_at=retrieved_at,
                        source_url=f"{BASE_URL}/getStockPriceInfo",
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

    def get_disclosures(self, symbol: str, start: dt.date, end: dt.date) -> list[Disclosure]:
        raise NotImplementedError("공시목록은 kr/dart 어댑터를 사용하라")

    def get_news(self, query: str, limit: int = 20) -> list[NewsItem]:
        raise NotImplementedError("뉴스는 kr/naver_news 어댑터를 사용하라")


def _to_float(raw: str | None) -> float | None:
    if raw in (None, "", "-"):
        return None
    return float(raw)


def _to_int(raw: str | None) -> int | None:
    if raw in (None, "", "-"):
        return None
    return int(raw)
