"""OpenDART API 어댑터 (ARCHITECTURE.md §5.1 — 핵심 기둥).

- 무료, 일 20,000건 한도, 재배포 안전.
- 구현 범위:
  * corpCode.xml  : 고유번호 zip 다운로드·파싱 (종목코드 → corp_code 매핑)
  * company.json  : 기업개황
  * fnlttSinglAcntAll.json : 정기보고서 재무제표 (단일회사 전체 재무제표)
  * list.json     : 공시목록
- 인증: 환경변수 DART_API_KEY (crtfc_key).
- 시세/뉴스는 이 어댑터 범위 밖 → NotImplementedError (krx_price / naver_news 담당).
"""

from __future__ import annotations

import datetime as dt
import io
import os
import time
import xml.etree.ElementTree as ET
import zipfile

import httpx

from arc.data.base import (
    Company,
    ConsolidationType,
    DataProvider,
    Disclosure,
    FinancialLineItem,
    FinancialStatement,
    Market,
    NewsItem,
    PeriodType,
    PricePoint,
    Provenance,
)

BASE_URL = "https://opendart.fss.or.kr/api"
SOURCE = "opendart"

# DART 정기보고서 코드 (reprt_code)
_REPRT_CODE: dict[PeriodType, str] = {
    PeriodType.Q1: "11013",  # 1분기보고서
    PeriodType.Q2: "11012",  # DART엔 2분기 단일 보고서가 없음 → 반기보고서
    PeriodType.HALF: "11012",  # 반기보고서
    PeriodType.Q3: "11014",  # 3분기보고서
    PeriodType.Q4: "11011",  # 4분기 단일 보고서 없음 → 사업보고서
    PeriodType.ANNUAL: "11011",  # 사업보고서
}

# corp_cls → Market
_CORP_CLS_MARKET: dict[str, Market] = {
    "Y": Market.KOSPI,
    "K": Market.KOSDAQ,
    "N": Market.KONEX,
}


class DartError(Exception):
    """OpenDART API 오류 (status != '000')."""

    def __init__(self, status: str, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(f"DART API error {status}: {message}")


class DartRateLimitError(DartError):
    """일 20,000건 한도 초과 (status '020'). 재시도해도 소용없다 — 즉시 중단."""


class DartProvider(DataProvider):
    """OpenDART 어댑터.

    일 20,000건 한도를 고려한 재시도 정책:
      - 네트워크 오류·5xx·429 → 지수 백오프로 최대 max_retries회 재시도
      - status '020'(사용한도 초과) → 재시도 없이 DartRateLimitError
        (재시도는 한도만 더 소모한다)
    """

    def __init__(
        self,
        api_key: str | None = None,
        client: httpx.Client | None = None,
        max_retries: int = 3,
        backoff_base: float = 1.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("DART_API_KEY", "")
        if not self.api_key:
            raise ValueError("DART_API_KEY가 설정되지 않았습니다 (.env 참조)")
        self._client = client or httpx.Client(base_url=BASE_URL, timeout=30.0)
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._corp_code_map: dict[str, dict[str, str]] | None = None  # stock_code → entry

    # ------------------------------------------------------------------ HTTP

    def _request(self, path: str, params: dict[str, str]) -> httpx.Response:
        """단순 재시도 래퍼. 429/5xx/전송오류만 재시도한다."""
        params = {"crtfc_key": self.api_key, **params}
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._client.get(f"/{path}", params=params)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"retryable status {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )
                resp.raise_for_status()
                return resp
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                if isinstance(exc, httpx.HTTPStatusError):
                    code = exc.response.status_code
                    if code != 429 and code < 500:
                        raise  # 4xx(429 제외)는 재시도 무의미
                last_exc = exc
                if attempt < self.max_retries:
                    time.sleep(self.backoff_base * (2**attempt))
        assert last_exc is not None
        raise last_exc

    def _get_json(self, path: str, params: dict[str, str]) -> dict:
        payload = self._request(path, params).json()
        status = payload.get("status", "")
        if status == "020":
            raise DartRateLimitError(status, payload.get("message", "사용한도 초과"))
        if status != "000":
            raise DartError(status, payload.get("message", ""))
        return payload

    def _now(self) -> dt.datetime:
        return dt.datetime.now(dt.UTC)

    # ------------------------------------------------------- corpCode.xml

    def load_corp_codes(self) -> dict[str, dict[str, str]]:
        """corpCode.xml zip을 내려받아 {종목코드: {corp_code, corp_name, ...}} 매핑 생성.

        상장사(stock_code가 있는 항목)만 보관한다. 결과는 인스턴스에 캐시.
        """
        if self._corp_code_map is not None:
            return self._corp_code_map
        resp = self._request("corpCode.xml", {})
        self._corp_code_map = self.parse_corp_code_zip(resp.content)
        return self._corp_code_map

    @staticmethod
    def parse_corp_code_zip(zip_bytes: bytes) -> dict[str, dict[str, str]]:
        """corpCode.xml zip 바이트 → {stock_code: entry} 매핑 (순수 파싱, 테스트 대상)."""
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            # zip 안의 첫 xml 파일 (통상 CORPCODE.xml)
            xml_name = next(n for n in zf.namelist() if n.lower().endswith(".xml"))
            root = ET.fromstring(zf.read(xml_name))
        mapping: dict[str, dict[str, str]] = {}
        for node in root.iter("list"):
            stock_code = (node.findtext("stock_code") or "").strip()
            if not stock_code:  # 비상장사는 건너뜀
                continue
            mapping[stock_code] = {
                "corp_code": (node.findtext("corp_code") or "").strip(),
                "corp_name": (node.findtext("corp_name") or "").strip(),
                "stock_code": stock_code,
                "modify_date": (node.findtext("modify_date") or "").strip(),
            }
        return mapping

    def corp_code_for(self, symbol: str) -> str:
        """종목코드(6자리) → DART 고유번호(8자리)."""
        mapping = self.load_corp_codes()
        try:
            return mapping[symbol]["corp_code"]
        except KeyError:
            raise KeyError(f"DART corpCode에 없는 종목코드: {symbol}") from None

    # ------------------------------------------------------- company.json

    def get_company(self, symbol: str) -> Company:
        corp_code = self.corp_code_for(symbol)
        payload = self._get_json("company.json", {"corp_code": corp_code})
        return self.parse_company(payload, symbol=symbol, retrieved_at=self._now())

    @staticmethod
    def parse_company(payload: dict, symbol: str, retrieved_at: dt.datetime) -> Company:
        """company.json 응답 → Company (순수 파싱, 테스트 대상)."""
        market = _CORP_CLS_MARKET.get(payload.get("corp_cls", ""), Market.KOSDAQ)
        return Company(
            symbol=symbol,
            name=payload.get("corp_name", ""),
            market=market,
            corp_code=payload.get("corp_code"),
            industry=payload.get("induty_code"),
            ceo_name=payload.get("ceo_nm"),
            fiscal_year_end=payload.get("acc_mt"),
            provenance=Provenance(
                source=SOURCE,
                retrieved_at=retrieved_at,
                source_url=f"{BASE_URL}/company.json?corp_code={payload.get('corp_code', '')}",
            ),
        )

    # -------------------------------------------- fnlttSinglAcntAll.json

    def get_financials(
        self,
        symbol: str,
        fiscal_year: int,
        period: PeriodType,
        consolidation: ConsolidationType = ConsolidationType.CONSOLIDATED,
    ) -> FinancialStatement:
        corp_code = self.corp_code_for(symbol)
        payload = self._get_json(
            "fnlttSinglAcntAll.json",
            {
                "corp_code": corp_code,
                "bsns_year": str(fiscal_year),
                "reprt_code": _REPRT_CODE[period],
                "fs_div": consolidation.value,
            },
        )
        return self.parse_financials(
            payload,
            symbol=symbol,
            fiscal_year=fiscal_year,
            period=period,
            consolidation=consolidation,
            retrieved_at=self._now(),
        )

    @staticmethod
    def parse_financials(
        payload: dict,
        symbol: str,
        fiscal_year: int,
        period: PeriodType,
        consolidation: ConsolidationType,
        retrieved_at: dt.datetime,
    ) -> FinancialStatement:
        """fnlttSinglAcntAll.json 응답 → FinancialStatement (순수 파싱, 테스트 대상)."""
        rows = payload.get("list", [])
        items = [
            FinancialLineItem(
                account_id=row.get("account_id") or None,
                account_name=row.get("account_nm", ""),
                amount=_parse_amount(row.get("thstrm_amount")),
                prior_amount=_parse_amount(row.get("frmtrm_amount")),
                prior2_amount=_parse_amount(row.get("bfefrmtrm_amount")),
                currency=row.get("currency") or "KRW",
                statement_type=row.get("sj_div"),
            )
            for row in rows
        ]
        rcept_no = rows[0].get("rcept_no") if rows else None
        return FinancialStatement(
            symbol=symbol,
            fiscal_year=fiscal_year,
            period=period,
            consolidation=consolidation,
            items=items,
            rcept_no=rcept_no,
            provenance=Provenance(
                source=SOURCE,
                retrieved_at=retrieved_at,
                source_ref=rcept_no,
                source_url=(
                    f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
                    if rcept_no
                    else None
                ),
            ),
        )

    # ------------------------------------------------------------ list.json

    def get_disclosures(self, symbol: str, start: dt.date, end: dt.date) -> list[Disclosure]:
        corp_code = self.corp_code_for(symbol)
        disclosures: list[Disclosure] = []
        page_no = 1
        while True:
            payload = self._get_json(
                "list.json",
                {
                    "corp_code": corp_code,
                    "bgn_de": start.strftime("%Y%m%d"),
                    "end_de": end.strftime("%Y%m%d"),
                    "page_no": str(page_no),
                    "page_count": "100",
                },
            )
            disclosures.extend(
                self.parse_disclosures(payload, symbol=symbol, retrieved_at=self._now())
            )
            if page_no >= int(payload.get("total_page", 1)):
                break
            page_no += 1
        return disclosures

    @staticmethod
    def parse_disclosures(
        payload: dict, symbol: str, retrieved_at: dt.datetime
    ) -> list[Disclosure]:
        """list.json 응답 1페이지 → Disclosure 목록 (순수 파싱, 테스트 대상)."""
        out: list[Disclosure] = []
        for row in payload.get("list", []):
            rcept_no = row.get("rcept_no", "")
            out.append(
                Disclosure(
                    symbol=symbol,
                    rcept_no=rcept_no,
                    title=row.get("report_nm", ""),
                    filed_at=dt.datetime.strptime(row.get("rcept_dt", ""), "%Y%m%d").date(),
                    filer=row.get("flr_nm"),
                    provenance=Provenance(
                        source=SOURCE,
                        retrieved_at=retrieved_at,
                        source_ref=rcept_no,
                        source_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
                    ),
                )
            )
        return out

    # ------------------------------------------------------------- 범위 밖

    def get_prices(self, symbol: str, start: dt.date, end: dt.date) -> list[PricePoint]:
        raise NotImplementedError("시세는 kr/krx_price 어댑터를 사용하라")

    def get_news(self, query: str, limit: int = 20) -> list[NewsItem]:
        raise NotImplementedError("뉴스는 kr/naver_news 어댑터를 사용하라")


def _parse_amount(raw: str | None) -> int | None:
    """DART 금액 문자열('1,234,567' / '-' / '') → int | None."""
    if raw is None:
        return None
    cleaned = raw.replace(",", "").strip()
    if cleaned in ("", "-"):
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None
