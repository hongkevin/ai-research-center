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
import re
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

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

# corpCode.xml 디스크 캐시 (D69). 하루면 충분하다 — 신규 상장은 그날 안 쓴다.
CORP_CODE_CACHE_TTL = dt.timedelta(hours=24)

# **요청 사이 최소 간격.** 일 20,000건 한도와 별개로 OpenDART는 순간 요청률에
# WAF가 걸린다 (아래 DartBlockedError 참조). 0.25초면 초당 4건으로, 전 상장사
# 3,981건을 돌려도 17분이다 — 어차피 그럴 일이 없으니 체감 비용이 없다.
MIN_REQUEST_INTERVAL = 0.25

_REPO_ROOT = Path(__file__).resolve().parents[4]


def _cache_dir() -> Path:
    """corpCode 캐시 위치. `ARC_STORE_DIR`을 따른다 (web/app.py와 같은 규칙)."""
    base = os.environ.get("ARC_STORE_DIR") or (_REPO_ROOT / ".arc-store")
    return Path(base) / "cache"


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


def viewer_url(rcept_no: str | None) -> str | None:
    """접수번호 → **사람이 열어 확인할** DART 뷰어 주소.

    API 엔드포인트는 키가 필요해 검토자가 클릭해도 아무것도 안 나온다.
    검증용 링크는 언제나 이 주소다.
    """
    return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else None


class DartError(Exception):
    """OpenDART API 오류 (status != '000')."""

    def __init__(self, status: str, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(f"DART API error {status}: {message}")


class DartRateLimitError(DartError):
    """일 20,000건 한도 초과 (status '020'). 재시도해도 소용없다 — 즉시 중단."""


class DartBlockedError(Exception):
    """**연결 자체가 거부된다 — IP가 차단된 상태다.**

    실제로 밟았다 (D69). company.json을 6워커로 병렬 호출하자 OpenDART가 IP를
    끊었고, 그 뒤 `corpCode.xml`까지 TCP 연결이 거부(curl HTTP 000)됐다. 40분
    넘게 안 풀렸다. **일 한도는 20%도 안 썼다** — 한도가 아니라 요청률이다.

    당시 코드가 차단을 증폭시켰다. `_request`가 `httpx.TransportError`를 재시도
    대상으로 잡는데 연결 거부(`ConnectError`·`RemoteProtocolError`)가 전부 그
    하위 클래스다. 즉 **막힌 뒤에도 4번씩 더 두드렸다.** 그래서 전송 오류는
    재시도 예산을 따로 짧게 준다 (`transport_retries`, 기본 1회).

    이 예외를 보면 **재시도하지 말고 기다려야 한다.** 자동 해제형이라 보통
    수십 분~24시간이면 풀리는데, 계속 두드리면 연장된다. 핫스팟으로 IP를 바꾸면
    당장은 되지만 해법이 아니다 — 통신사 공유 IP라 남까지 막고, 배포 환경엔
    핫스팟이 없다.
    """


class DartProvider(DataProvider):
    """OpenDART 어댑터.

    재시도 정책 — **무엇이 재시도로 풀리는가로 가른다.**
      - 5xx·429 → 서버가 "나중에 오라"고 한 것이다. 지수 백오프로 max_retries회.
      - 전송 오류(연결 거부·프로토콜 오류) → 재시도로 안 풀린다. transport_retries
        (기본 1)회만 보고 DartBlockedError로 즉시 중단한다. 여기서 계속 두드리면
        차단이 연장된다 (DartBlockedError 독스트링 참조).
      - status '020'(사용한도 초과) → 재시도 없이 DartRateLimitError
        (재시도는 한도만 더 소모한다)

    그리고 **요청률을 스스로 제한한다** — 동시연결 2, 요청 간 최소 0.25초.
    """

    def __init__(
        self,
        api_key: str | None = None,
        client: httpx.Client | None = None,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        transport_retries: int = 1,
        min_interval: float = MIN_REQUEST_INTERVAL,
        cache_dir: str | Path | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("DART_API_KEY", "")
        if not self.api_key:
            raise ValueError("DART_API_KEY가 설정되지 않았습니다 (.env 참조)")
        # 동시연결을 2로 묶는다. 부르는 쪽이 ThreadPoolExecutor를 쓰더라도
        # 소켓이 그 이상 열리지 않는다 — 병렬 호출로 차단당한 것이 D69다.
        self._client = client or httpx.Client(
            base_url=BASE_URL,
            timeout=30.0,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
        )
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.transport_retries = transport_retries
        self.min_interval = min_interval
        self._corp_code_map: dict[str, dict[str, str]] | None = None  # stock_code → entry
        self._cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._lock = threading.Lock()  # 요청 간격을 스레드 간에도 지킨다
        self._last_request_at = 0.0

    # ------------------------------------------------------------------ HTTP

    def _throttle(self) -> None:
        """요청 간 최소 간격을 지킨다. **차단당하지 않는 것이 가장 싼 재시도다.**"""
        if self.min_interval <= 0:
            return
        with self._lock:
            wait = self._last_request_at + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request_at = time.monotonic()

    def _request(self, path: str, params: dict[str, str]) -> httpx.Response:
        """재시도 래퍼. **429/5xx와 전송 오류를 다르게 다룬다** (D69)."""
        params = {"crtfc_key": self.api_key, **params}
        last_exc: Exception | None = None
        transport_failures = 0
        attempt = 0
        while True:
            try:
                self._throttle()
                resp = self._client.get(f"/{path}", params=params)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"retryable status {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                if code != 429 and code < 500:
                    raise  # 4xx(429 제외)는 재시도 무의미
                last_exc = exc
                if attempt >= self.max_retries:
                    break
            except httpx.TransportError as exc:
                # **연결이 거부되는 것은 재시도로 안 풀린다.** 예산을 따로 짧게
                # 주고, 다 쓰면 "기다려라"라고 말하는 예외로 바꾼다.
                transport_failures += 1
                last_exc = exc
                if transport_failures > self.transport_retries:
                    raise DartBlockedError(
                        f"OpenDART 연결이 거부됐다 ({type(exc).__name__}). "
                        "요청률 초과로 IP가 차단된 상태일 수 있다 — **재시도하지 말고 "
                        "기다려라.** 보통 수십 분~24시간이면 자동 해제된다."
                    ) from exc
            attempt += 1
            time.sleep(self.backoff_base * (2 ** (attempt - 1)))
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

    def corp_code_cache_path(self) -> Path:
        return (self._cache_dir or _cache_dir()) / "corpcode.zip"

    def load_corp_codes(self) -> dict[str, dict[str, str]]:
        """corpCode.xml zip → {종목코드: {corp_code, corp_name, ...}} 매핑.

        상장사(stock_code가 있는 항목)만 보관한다.

        **디스크에 캐시한다 (D69).** 전에는 인스턴스 메모리에만 있어서 프로세스가
        새로 뜰 때마다 수 MB zip을 다시 받았다 — 테스트 한 번, 웹 워커 재시작
        한 번, CLI 한 번마다. 이게 요청률 차단을 부르는 가장 흔한 경로였다.
        캐시가 24시간 안이면 네트워크를 아예 건드리지 않는다.

        캐시를 못 읽거나 못 쓰는 것은 **실패가 아니다** — 네트워크로 넘어간다.
        """
        if self._corp_code_map is not None:
            return self._corp_code_map

        path = self.corp_code_cache_path()
        raw = self._read_cached_corp_codes(path)
        if raw is None:
            raw = self._request("corpCode.xml", {}).content
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".zip.tmp")
                tmp.write_bytes(raw)
                tmp.replace(path)  # 원자적 — 반쯤 쓰인 zip을 다음 프로세스가 읽지 않게
            except OSError:
                pass  # 캐시는 최적화지 요구사항이 아니다

        self._corp_code_map = self.parse_corp_code_zip(raw)
        return self._corp_code_map

    @staticmethod
    def _read_cached_corp_codes(path: Path) -> bytes | None:
        """캐시가 있고 신선하면 바이트를, 아니면 None."""
        try:
            age = time.time() - path.stat().st_mtime
            if age > CORP_CODE_CACHE_TTL.total_seconds():
                return None
            raw = path.read_bytes()
        except OSError:
            return None
        # 깨진 캐시를 신뢰하지 않는다 — zip으로 열리는지 확인한 뒤에만 쓴다
        return raw if raw and zipfile.is_zipfile(io.BytesIO(raw)) else None

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

    def search_companies(self, query: str, limit: int = 12) -> list[dict[str, str]]:
        """회사명·종목코드로 상장사를 찾는다.

        `corpCode.xml`은 전 상장사 목록이라 검색에 그대로 쓸 수 있다. 별도
        검색 API가 필요 없고, 오프라인이며, 종목코드를 외울 필요가 없어진다.
        """
        mapping = self.load_corp_codes()
        return search_corp_index(mapping, query, limit)

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
            short_name=payload.get("stock_name") or None,
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
        """재무제표 조회. 전체계정이 없으면 **주요계정으로 폴백한다.**

        `fnlttSinglAcntAll`(전체 재무제표)과 `fnlttSinglAcnt`(주요계정)은
        커버리지가 다르다. 실측: 파마리서치(214450) FY2025는 사업보고서를
        제출했고 정기보고서 주요정보 API도 응답하는데 **전체계정만 013**이다.

        주요계정은 30개뿐이라 매출원가·판관비가 없어 마진 브리지를 만들 수
        없다. 그래도 매출·영업이익·순이익·자산·부채·자본은 있으므로
        "리포트를 못 쓴다"와 "일부 지표가 없다" 사이에서 후자를 택한다.
        """
        corp_code = self.corp_code_for(symbol)
        params = {
            "corp_code": corp_code,
            "bsns_year": str(fiscal_year),
            "reprt_code": _REPRT_CODE[period],
            "fs_div": consolidation.value,
        }
        try:
            payload = self._get_json("fnlttSinglAcntAll.json", params)
        except DartError as exc:
            if exc.status != "013":
                raise
            payload = self._get_json("fnlttSinglAcnt.json", params)
            return self.parse_major_accounts(
                payload,
                symbol=symbol,
                fiscal_year=fiscal_year,
                period=period,
                consolidation=consolidation,
                retrieved_at=self._now(),
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
    def parse_major_accounts(
        payload: dict,
        symbol: str,
        fiscal_year: int,
        period: PeriodType,
        consolidation: ConsolidationType,
        retrieved_at: dt.datetime,
    ) -> FinancialStatement:
        """fnlttSinglAcnt.json(주요계정) 응답 → FinancialStatement.

        전체계정과 형태가 다르다:

        * `fs_div` 파라미터를 줘도 **CFS·OFS를 모두 돌려준다.** 행의 `fs_div`로
          걸러야 한다. 안 그러면 연결·별도가 섞여 자산총계가 두 개가 된다.
        * `account_id`가 없다. 계정명 매칭에만 의존하게 된다.
        * 같은 계정이 중복으로 온다 (당기순이익 2행).
        """
        want = consolidation.value
        rows = [r for r in payload.get("list", []) if (r.get("fs_div") or want) == want]
        if not rows:  # fs_div 필드가 아예 없는 응답이면 그대로 쓴다
            rows = payload.get("list", [])

        items: list[FinancialLineItem] = []
        seen: set[tuple[str, str | None]] = set()
        for row in rows:
            name = row.get("account_nm", "")
            key = (name, row.get("sj_div"))
            if key in seen:
                continue
            seen.add(key)
            cur, prior, prior2 = _flow_amounts(row)
            items.append(
                FinancialLineItem(
                    account_id=None,  # 주요계정에는 표준계정 코드가 없다
                    account_name=name,
                    amount=cur,
                    prior_amount=prior,
                    prior2_amount=prior2,
                    currency=row.get("currency") or "KRW",
                    statement_type=row.get("sj_div"),
                )
            )

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
                dataset="재무제표 (주요계정)",
                source_url=f"{BASE_URL}/fnlttSinglAcnt.json",
                verify_url=viewer_url(rcept_no),
                source_ref=rcept_no,
            ),
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
        items = []
        for row in rows:
            cur, prior, prior2 = _flow_amounts(row)
            items.append(
                FinancialLineItem(
                    account_id=row.get("account_id") or None,
                    account_name=row.get("account_nm", ""),
                    amount=cur,
                    prior_amount=prior,
                    prior2_amount=prior2,
                    currency=row.get("currency") or "KRW",
                    statement_type=row.get("sj_div"),
                )
            )
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
                dataset="재무제표 (전체계정)",
                source_url=f"{BASE_URL}/fnlttSinglAcntAll.json",
                verify_url=viewer_url(rcept_no),
                source_ref=rcept_no,
            ),
        )

    # ------------------------------------------------------------ list.json

    def get_disclosures(
        self,
        symbol: str,
        start: dt.date,
        end: dt.date,
        pblntf_ty: str | None = None,
    ) -> list[Disclosure]:
        """공시 목록. `pblntf_ty`로 종류를 좁힐 수 있다.

        **좁히지 않으면 대형주에서 수십 페이지를 넘긴다** — 실측: 삼성전자
        900일치가 3,808건 / 39페이지로 8초가 걸렸고, 대부분이
        「임원·주요주주특정증권등소유상황보고서」였다. 정기공시(A)만 받으면
        10건 / 1페이지다.

        종류: A 정기공시 · B 주요사항보고 · C 발행 · D 지분 · E 기타 ·
        F 외부감사 · I 거래소공시(잠정실적이 여기 있다) · J 공정위
        """
        corp_code = self.corp_code_for(symbol)
        disclosures: list[Disclosure] = []
        page_no = 1
        while True:
            params = {
                "corp_code": corp_code,
                "bgn_de": start.strftime("%Y%m%d"),
                "end_de": end.strftime("%Y%m%d"),
                "page_no": str(page_no),
                "page_count": "100",
            }
            if pblntf_ty:
                params["pblntf_ty"] = pblntf_ty
            payload = self._get_json("list.json", params)
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
                        dataset="공시목록",
                        source_url=f"{BASE_URL}/list.json",
                        verify_url=viewer_url(rcept_no),
                        source_ref=rcept_no,
                    ),
                )
            )
        return out

    # ------------------------------------------------------------- 범위 밖

    def get_prices(self, symbol: str, start: dt.date, end: dt.date) -> list[PricePoint]:
        raise NotImplementedError("시세는 kr/krx_price 어댑터를 사용하라")

    def get_news(self, query: str, limit: int = 20) -> list[NewsItem]:
        raise NotImplementedError("뉴스는 kr/naver_news 어댑터를 사용하라")


# 회사명 앞뒤의 법인격 표기 — 검색에서는 무시한다 ("(주)파마리서치" == "파마리서치")
_LEGAL_FORM_RE = re.compile(r"\(주\)|\(유\)|주식회사|㈜|㈐|\s")


def normalize_company_name(name: str) -> str:
    return _LEGAL_FORM_RE.sub("", name or "").lower()


def search_corp_index(
    mapping: dict[str, dict[str, str]], query: str, limit: int = 12
) -> list[dict[str, str]]:
    """{종목코드: entry} → 검색 결과.

    순위: 종목코드 완전일치 > 이름 완전일치 > 접두 일치 > 부분 일치.
    접두를 부분보다 위에 두는 이유는 "삼성"을 치면 "삼성전자"가 "제일기획"의
    모회사 표기보다 먼저 와야 하기 때문이다.
    """
    q = query.strip()
    if not q:
        return []
    qn = normalize_company_name(q)
    if not qn:
        return []

    scored: list[tuple[int, int, dict[str, str]]] = []
    for code, entry in mapping.items():
        name = entry.get("corp_name", "")
        nn = normalize_company_name(name)
        if code == q:
            rank = 0
        elif nn == qn:
            rank = 1
        elif nn.startswith(qn):
            rank = 2
        elif qn in nn:
            rank = 3
        elif q.isdigit() and code.startswith(q):
            rank = 4
        else:
            continue
        scored.append((rank, len(nn), {"name": name, "symbol": code}))

    scored.sort(key=lambda x: (x[0], x[1], x[2]["name"]))
    return [item for _, _, item in scored[:limit]]


def _flow_amounts(row: dict) -> tuple[int | None, int | None, int | None]:
    """행 하나 → (당기, 전기, 전전기). **정기보고서 종류마다 컬럼 이름이 다르다.**

    실측(삼성물산 028260):

    | 보고서 | 당기 | 전기 | 전전기 |
    |---|---|---|---|
    | 사업 | `thstrm_amount` | `frmtrm_amount` | `bfefrmtrm_amount` |
    | 분기·반기 손익 | `thstrm_add_amount`(누적) | `frmtrm_add_amount`(전년 누적) | **없음** |
    | 분기·반기 재무상태 | `thstrm_amount` | `frmtrm_amount` | 없음 |

    한때 `frmtrm_amount`·`bfefrmtrm_amount`만 읽었다. 분기 손익에는 그 두 칸이
    아예 없어서 **전기가 통째로 비었다** — 전년 대비 증감이 안 나오고, 3개년
    추이 차트에 막대가 한 해만 섰다. 어닝 리뷰에서 전년 동기 비교가 없으면
    남는 게 없다.

    **누적을 쓴다.** 분기 손익에는 단독 분기(`thstrm_amount`)와 누적
    (`thstrm_add_amount`)이 함께 오는데, 반기보고서의 `thstrm_amount`는
    2분기 단독이라 「반기」라는 이름과 어긋난다. 고른 기간과 숫자의 기간이
    같아야 한다. 전기도 같은 기준(`frmtrm_add_amount`)이라 비교가 성립한다.

    재무상태표에는 `_add_` 칸이 없어 자동으로 기존 경로를 탄다.
    """
    return (
        _parse_amount(row.get("thstrm_add_amount") or row.get("thstrm_amount")),
        _parse_amount(row.get("frmtrm_add_amount") or row.get("frmtrm_amount")),
        _parse_amount(row.get("bfefrmtrm_amount")),
    )


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
