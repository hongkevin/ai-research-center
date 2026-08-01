"""OpenDART 어댑터 파싱 로직 테스트 — 전부 목킹/픽스처, 네트워크 호출 없음."""

import datetime as dt
import io
import zipfile

import httpx
import pytest
import respx

from arc.data.base import ConsolidationType, Market, PeriodType
from arc.data.kr.dart import (
    BASE_URL,
    DartError,
    DartProvider,
    DartRateLimitError,
    _parse_amount,
)

NOW = dt.datetime(2026, 7, 25, 12, 0, tzinfo=dt.UTC)


def make_corp_code_zip() -> bytes:
    """OpenDART corpCode.xml 응답 형식의 zip 픽스처."""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<result>
  <list>
    <corp_code>00126380</corp_code>
    <corp_name>삼성전자</corp_name>
    <stock_code>005930</stock_code>
    <modify_date>20260101</modify_date>
  </list>
  <list>
    <corp_code>00999999</corp_code>
    <corp_name>비상장회사</corp_name>
    <stock_code> </stock_code>
    <modify_date>20260101</modify_date>
  </list>
</result>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", xml)
    return buf.getvalue()


# ------------------------------------------------------------------ 순수 파싱


def test_parse_corp_code_zip_keeps_listed_only():
    mapping = DartProvider.parse_corp_code_zip(make_corp_code_zip())
    assert "005930" in mapping
    assert mapping["005930"]["corp_code"] == "00126380"
    assert len(mapping) == 1  # 비상장(stock_code 공백)은 제외


def test_parse_company():
    payload = {
        "status": "000",
        "corp_code": "00126380",
        "corp_name": "삼성전자",
        "corp_cls": "Y",
        "ceo_nm": "홍길동",
        "induty_code": "264",
        "acc_mt": "12",
    }
    company = DartProvider.parse_company(payload, symbol="005930", retrieved_at=NOW)
    assert company.market == Market.KOSPI
    assert company.corp_code == "00126380"
    assert company.provenance.source == "opendart"


def test_parse_financials():
    payload = {
        "status": "000",
        "list": [
            {
                "rcept_no": "20260515000123",
                "account_id": "ifrs-full_Revenue",
                "account_nm": "매출액",
                "thstrm_amount": "1,234,567",
                "currency": "KRW",
                "sj_div": "IS",
            },
            {
                "rcept_no": "20260515000123",
                "account_id": "",
                "account_nm": "영업이익",
                "thstrm_amount": "-",
                "sj_div": "IS",
            },
        ],
    }
    fs = DartProvider.parse_financials(
        payload,
        symbol="005930",
        fiscal_year=2026,
        period=PeriodType.Q1,
        consolidation=ConsolidationType.CONSOLIDATED,
        retrieved_at=NOW,
    )
    assert fs.items[0].amount == 1_234_567
    assert fs.items[1].amount is None  # '-'는 None
    assert fs.rcept_no == "20260515000123"
    assert fs.provenance.source_ref == "20260515000123"  # provenance ↔ 공시번호 연결


def test_parse_disclosures():
    payload = {
        "status": "000",
        "list": [
            {
                "rcept_no": "20260701000001",
                "report_nm": "반기보고서 (2026.06)",
                "rcept_dt": "20260701",
                "flr_nm": "가온그룹",
            }
        ],
    }
    out = DartProvider.parse_disclosures(payload, symbol="078890", retrieved_at=NOW)
    assert out[0].filed_at == dt.date(2026, 7, 1)
    assert "rcpNo=20260701000001" in (out[0].provenance.verify_url or "")
    assert "list.json" in (out[0].provenance.source_url or "")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1,234,567", 1_234_567), ("-1,000", -1_000), ("-", None), ("", None), (None, None)],
)
def test_parse_amount(raw, expected):
    assert _parse_amount(raw) == expected


# ------------------------------------------------------------- HTTP 레이어


def make_provider(**kwargs) -> DartProvider:
    return DartProvider(api_key="test-key", backoff_base=0.0, **kwargs)


@respx.mock
def test_rate_limit_raises_without_retry():
    route = respx.get(f"{BASE_URL}/company.json").mock(
        return_value=httpx.Response(200, json={"status": "020", "message": "사용한도 초과"})
    )
    provider = make_provider()
    with pytest.raises(DartRateLimitError):
        provider._get_json("company.json", {"corp_code": "00126380"})
    assert route.call_count == 1  # 한도 초과는 재시도하지 않는다


@respx.mock
def test_dart_error_status():
    respx.get(f"{BASE_URL}/list.json").mock(
        return_value=httpx.Response(200, json={"status": "013", "message": "조회 데이터 없음"})
    )
    with pytest.raises(DartError) as exc_info:
        make_provider()._get_json("list.json", {})
    assert exc_info.value.status == "013"


@respx.mock
def test_retry_on_5xx_then_success():
    route = respx.get(f"{BASE_URL}/company.json")
    route.side_effect = [
        httpx.Response(500),
        httpx.Response(200, json={"status": "000", "corp_name": "삼성전자"}),
    ]
    payload = make_provider()._get_json("company.json", {})
    assert payload["corp_name"] == "삼성전자"
    assert route.call_count == 2


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("DART_API_KEY", raising=False)
    with pytest.raises(ValueError):
        DartProvider()


# ── 주요계정 폴백 (fnlttSinglAcnt) ───────────────────────────────────
def _major_row(fs_div, sj_div, name, cur, prior="0"):
    return {
        "rcept_no": "20260319001417",
        "fs_div": fs_div,
        "sj_div": sj_div,
        "account_nm": name,
        "thstrm_amount": cur,
        "frmtrm_amount": prior,
        "bfefrmtrm_amount": "0",
        "currency": "KRW",
    }


MAJOR_PAYLOAD = {
    "status": "000",
    "list": [
        _major_row("CFS", "BS", "자산총계", "1,043,888,016,418"),
        _major_row("CFS", "IS", "매출액", "536,289,118,812"),
        _major_row("CFS", "IS", "영업이익", "214,395,758,571"),
        _major_row("CFS", "IS", "당기순이익(손실)", "168,255,434,874"),
        _major_row("CFS", "IS", "당기순이익(손실)", "168,255,434,874"),  # 중복 행
        _major_row("OFS", "BS", "자산총계", "989,620,033,035"),
        _major_row("OFS", "IS", "매출액", "473,789,922,204"),
    ],
}


class TestMajorAccountsFallback:
    """전체계정(013)일 때 주요계정으로 폴백한다 — 소형주 커버리지의 핵심."""

    def _parse(self, consolidation=ConsolidationType.CONSOLIDATED, payload=None):
        return DartProvider.parse_major_accounts(
            payload or MAJOR_PAYLOAD,
            symbol="214450",
            fiscal_year=2025,
            period=PeriodType.ANNUAL,
            consolidation=consolidation,
            retrieved_at=NOW,
        )

    def test_filters_to_requested_basis(self):
        """fs_div를 줘도 CFS·OFS가 **모두** 온다. 안 거르면 자산총계가 두 개다."""
        stmt = self._parse()
        assets = [i for i in stmt.items if i.account_name == "자산총계"]
        assert len(assets) == 1
        assert assets[0].amount == 1_043_888_016_418

    def test_separate_basis_picks_ofs(self):
        stmt = self._parse(ConsolidationType.SEPARATE)
        assets = [i for i in stmt.items if i.account_name == "자산총계"]
        assert len(assets) == 1
        assert assets[0].amount == 989_620_033_035

    def test_duplicate_rows_deduped(self):
        stmt = self._parse()
        ni = [i for i in stmt.items if i.account_name == "당기순이익(손실)"]
        assert len(ni) == 1

    def test_no_account_id_available(self):
        """주요계정에는 표준계정 코드가 없다 — 계정명 매칭에만 의존하게 된다."""
        stmt = self._parse()
        assert all(i.account_id is None for i in stmt.items)

    def test_provenance_separates_what_we_called_from_what_a_human_opens(self):
        """`source_url`은 우리가 호출한 API(재현용), `verify_url`은 사람이 열
        DART 뷰어(검증용)다. 하나로 뭉치면 둘 중 하나는 쓸모없어진다 — API
        엔드포인트는 키가 없으면 열리지 않는다."""
        stmt = self._parse()
        assert stmt.rcept_no == "20260319001417"
        assert stmt.provenance.dataset == "재무제표 (주요계정)"
        assert "fnlttSinglAcnt.json" in (stmt.provenance.source_url or "")
        assert "20260319001417" in (stmt.provenance.verify_url or "")

    def test_missing_fs_div_field_keeps_all_rows(self):
        payload = {"status": "000", "list": [{**_major_row("CFS", "IS", "매출액", "100")}]}
        del payload["list"][0]["fs_div"]
        stmt = self._parse(payload=payload)
        assert len(stmt.items) == 1


class TestFinancialsFallbackRouting:
    def _provider(self):
        p = DartProvider(api_key="x" * 40)
        p._corp_code_map = {"214450": {"corp_code": "00970453", "corp_name": "파마리서치"}}
        return p

    @respx.mock
    def test_falls_back_to_major_accounts_on_013(self):
        """실측: 사업보고서는 있는데 전체계정만 013인 회사가 있다."""
        respx.get(f"{BASE_URL}/fnlttSinglAcntAll.json").mock(
            return_value=httpx.Response(
                200, json={"status": "013", "message": "조회된 데이타가 없습니다."}
            )
        )
        route = respx.get(f"{BASE_URL}/fnlttSinglAcnt.json").mock(
            return_value=httpx.Response(200, json=MAJOR_PAYLOAD)
        )
        stmt = self._provider().get_financials("214450", 2025, PeriodType.ANNUAL)
        assert route.called
        assert any(i.account_name == "매출액" for i in stmt.items)

    @respx.mock
    def test_other_errors_are_not_swallowed(self):
        """013이 아닌 오류까지 폴백하면 진짜 장애가 숨는다."""
        respx.get(f"{BASE_URL}/fnlttSinglAcntAll.json").mock(
            return_value=httpx.Response(200, json={"status": "020", "message": "요청 제한 초과"})
        )
        with pytest.raises(DartError):
            self._provider().get_financials("214450", 2025, PeriodType.ANNUAL)

    @respx.mock
    def test_no_fallback_when_full_accounts_available(self):
        full = {
            "status": "000",
            "list": [
                {
                    "rcept_no": "1",
                    "account_id": "ifrs-full_Revenue",
                    "account_nm": "매출액",
                    "sj_div": "IS",
                    "thstrm_amount": "100",
                    "frmtrm_amount": "90",
                }
            ],
        }
        respx.get(f"{BASE_URL}/fnlttSinglAcntAll.json").mock(
            return_value=httpx.Response(200, json=full)
        )
        route = respx.get(f"{BASE_URL}/fnlttSinglAcnt.json").mock(
            return_value=httpx.Response(200, json=MAJOR_PAYLOAD)
        )
        stmt = self._provider().get_financials("214450", 2025, PeriodType.ANNUAL)
        assert not route.called
        assert stmt.items[0].account_id == "ifrs-full_Revenue"
