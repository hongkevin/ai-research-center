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
    assert "rcpNo=20260701000001" in out[0].provenance.source_url


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
