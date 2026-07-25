"""시장 중립 도메인 모델 + DataProvider 추상 인터페이스.

ARCHITECTURE.md §5.1: 시장 중립 인터페이스 뒤에 시장별 어댑터(kr/dart,
kr/krx_price, kr/naver_news, us/edgar)를 붙인다. 도메인 모델은 KR/US 공통.

모든 데이터 포인트는 Provenance(원천, 조회 시각, 원문 URL/공시번호)를 갖는다
(§4.2 — 인간 검토 화면에서 클릭 추적 가능해야 한다).
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Market(StrEnum):
    """시장 구분."""

    KOSPI = "KOSPI"
    KOSDAQ = "KOSDAQ"
    KONEX = "KONEX"
    NYSE = "NYSE"
    NASDAQ = "NASDAQ"


class ConsolidationType(StrEnum):
    """연결/별도 구분 (DART: CFS/OFS)."""

    CONSOLIDATED = "CFS"  # 연결
    SEPARATE = "OFS"  # 별도


class PeriodType(StrEnum):
    """분기/반기/연간 구분."""

    Q1 = "Q1"
    Q2 = "Q2"  # 반기 누적이 아닌 2분기 단일 기간을 뜻할 때 사용
    Q3 = "Q3"
    Q4 = "Q4"
    HALF = "HALF"  # 반기
    ANNUAL = "ANNUAL"  # 연간


class Provenance(BaseModel):
    """데이터 출처 추적. 모든 도메인 모델이 반드시 보유한다.

    - source: 원천 식별자 (예: "opendart", "krx_price", "naver_news", "edgar")
    - retrieved_at: 조회 시각 (UTC 권장)
    - source_url: 원문 URL (있는 경우)
    - source_ref: 원문 참조 번호 — DART 접수번호(rcept_no) 등
    """

    model_config = ConfigDict(frozen=True)

    source: str
    retrieved_at: dt.datetime
    source_url: str | None = None
    source_ref: str | None = None  # 공시번호(rcept_no) 등


class Company(BaseModel):
    """기업 개황 (시장 공통)."""

    symbol: str  # 종목코드 (KR: 6자리, US: ticker)
    name: str
    market: Market
    corp_code: str | None = None  # DART 고유번호(8자리) 등 소스별 기업 식별자
    industry: str | None = None
    ceo_name: str | None = None
    fiscal_year_end: str | None = None  # 결산월 (예: "12")
    provenance: Provenance


class FinancialLineItem(BaseModel):
    """재무제표 개별 계정 항목."""

    account_id: str | None = None  # 표준계정ID (XBRL 태그 등)
    account_name: str  # 계정명 (예: "매출액")
    amount: int | None = None  # 당기 금액
    currency: str = "KRW"
    statement_type: str | None = None  # BS/IS/CIS/CF/SCE


class FinancialStatement(BaseModel):
    """재무제표 (연결/별도 × 분기/연간 구분 포함)."""

    symbol: str
    fiscal_year: int  # 사업연도 (예: 2026)
    period: PeriodType  # 분기/반기/연간
    consolidation: ConsolidationType  # 연결/별도
    items: list[FinancialLineItem] = Field(default_factory=list)
    rcept_no: str | None = None  # 원천 공시 접수번호
    provenance: Provenance


class PricePoint(BaseModel):
    """EOD 시세 1일치."""

    symbol: str
    date: dt.date
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float
    volume: int | None = None
    market_cap: int | None = None  # 시가총액 (원)
    shares_outstanding: int | None = None  # 상장주식수
    provenance: Provenance


class Disclosure(BaseModel):
    """공시 메타데이터."""

    symbol: str
    rcept_no: str  # 접수번호 — DART 원문 조회 키
    title: str
    filed_at: dt.date  # 접수일자
    filer: str | None = None  # 제출인
    provenance: Provenance


class NewsItem(BaseModel):
    """뉴스 스니펫. 본문 크롤링 금지 — 스니펫만 저장한다 (§5.1)."""

    title: str
    snippet: str  # 검색 API가 주는 요약문까지만
    url: str
    published_at: dt.datetime | None = None
    provenance: Provenance


class DataProvider(ABC):
    """시장 중립 데이터 제공자 인터페이스.

    시장별 어댑터(OpenDART, 금융위시세, 네이버뉴스, EDGAR)는 이 인터페이스를
    구현한다. 지원하지 않는 기능은 NotImplementedError를 던진다
    (예: 시세 어댑터는 get_financials 미지원).
    """

    @abstractmethod
    def get_company(self, symbol: str) -> Company:
        """기업 개황 조회."""

    @abstractmethod
    def get_financials(
        self,
        symbol: str,
        fiscal_year: int,
        period: PeriodType,
        consolidation: ConsolidationType = ConsolidationType.CONSOLIDATED,
    ) -> FinancialStatement:
        """재무제표 조회."""

    @abstractmethod
    def get_prices(self, symbol: str, start: dt.date, end: dt.date) -> list[PricePoint]:
        """EOD 시세 조회."""

    @abstractmethod
    def get_disclosures(self, symbol: str, start: dt.date, end: dt.date) -> list[Disclosure]:
        """공시 목록 조회."""

    @abstractmethod
    def get_news(self, query: str, limit: int = 20) -> list[NewsItem]:
        """뉴스 스니펫 검색."""
