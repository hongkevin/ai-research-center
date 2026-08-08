"""신용공여 잔고 — 금융투자협회 종합통계 (data.go.kr).

왜 이것뿐인가
-------------
[D67](../../../docs/decisions.md#d67)에서 실측으로 확인한 것: **종목별 신용잔고는
KRX가 아예 공표하지 않는다.** 종목별을 실제로 주는 곳은 증권사 OpenAPI뿐인데
약관이 제3자 제공을 막는다. RA에게 그 요청이 오는 이유가 바로 이것이다.

그래서 우리가 열 수 있는 것은 **시장 전체 합계**다. 종목별의 대체재가 아니라
**다른 것**이고, 화면이 그렇게 말해야 한다 — 「신용잔고 28조」를 종목 옆에
놓으면 그 종목 얘기로 읽힌다.

무엇에 쓰나
-----------
브리프의 **매크로 칸**이다. 시장에 레버리지가 얼마나 쌓였나는 지수·환율·금리와
같은 층의 정보다 — 개별 종목 판단이 아니라 **판이 어떤 상태인가**다.

이용허락범위에 제한이 없어 재배포가 안전하고 `KRX_API_KEY`를 그대로 쓴다.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import urllib.parse
from dataclasses import dataclass

import httpx

log = logging.getLogger("arc.data.kr.kofia")

BASE_URL = "https://apis.data.go.kr/1160100/service/GetKofiaStatisticsInfoService"

# 응답 필드 → 화면에 세울 이름. **전부 싣지 않는다** — 매크로 칸은 좁다.
FIELDS: tuple[tuple[str, str], ...] = (
    ("crdTrFingWhl", "신용융자"),
    ("crdTrFingScrs", "└ 코스피"),
    ("crdTrFingKosdaq", "└ 코스닥"),
)


class KofiaError(Exception):
    pass


@dataclass
class Credit:
    """하루치 신용공여 잔고. **시장 전체다** — 종목별이 아니다."""

    date: str  # YYYYMMDD
    loan_total: int = 0  # 신용거래융자 전체 (원)
    loan_kospi: int = 0
    loan_kosdaq: int = 0
    short_total: int = 0  # 신용거래대주
    pledge: int = 0  # 예탁증권담보융자
    # 직전 관측 대비. **어제 대비가 아니다** — 자료가 늦으면 직전도 늦다
    prev_total: int | None = None

    @property
    def change(self) -> int | None:
        return None if self.prev_total is None else self.loan_total - self.prev_total

    @property
    def change_pct(self) -> float | None:
        if self.prev_total in (None, 0):
            return None
        return (self.loan_total - self.prev_total) / self.prev_total * 100.0

    @property
    def display(self) -> str:
        """조 단위. 28,417,965,841,579원은 아무도 못 읽는다."""
        return f"{self.loan_total / 1e12:,.1f}조"


def _key(api_key: str | None = None) -> str:
    raw = api_key or os.environ.get("KRX_API_KEY", "")
    if not raw:
        raise KofiaError("KRX_API_KEY가 없습니다 (.env 참조)")
    # Encoding 키는 `%`가 박혀 있어 httpx가 다시 인코딩하면 403이 난다 (D60)
    return urllib.parse.unquote(raw) if "%" in raw else raw


def _as_int(raw) -> int:
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return 0


def fetch_credit(
    *,
    rows: int = 10,
    api_key: str | None = None,
    client: httpx.Client | None = None,
) -> Credit | None:
    """최신 신용공여 잔고. **없으면 None이다** — 가짜 값을 만들지 않는다.

    날짜를 지정하지 않는다. 이 API는 최신순으로 주므로 **가장 최근 것이
    최신**이고, 우리가 달력으로 거래일을 흉내 내면 임시공휴일에서 어긋난다.
    """
    own = client is None
    client = client or httpx.Client(timeout=25.0)
    try:
        res = client.get(
            f"{BASE_URL}/getGrantingOfCreditBalanceInfo",
            params={
                "serviceKey": _key(api_key),
                # **이게 없으면 XML이 온다** — 지수 API에서 같은 것에 물렸다
                "resultType": "json",
                "numOfRows": str(max(2, rows)),
                "pageNo": "1",
            },
        )
        res.raise_for_status()
        body = res.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise KofiaError(f"신용공여 잔고를 못 받았습니다: {exc}") from exc
    finally:
        if own:
            client.close()

    envelope = body.get("response") or body
    header = envelope.get("header") or {}
    if header.get("resultCode") not in (None, "00"):
        raise KofiaError(f"{header.get('resultCode')}: {header.get('resultMsg')}")

    items = ((envelope.get("body") or {}).get("items") or {}).get("item") or []
    if isinstance(items, dict):
        items = [items]
    if not items:
        return None

    # 최신순으로 오지만 **믿지 않고 정렬한다** — 순서가 바뀌면 조용히 틀린다
    items = sorted(items, key=lambda x: str(x.get("basDt", "")), reverse=True)
    top = items[0]
    out = Credit(
        date=str(top.get("basDt", "")),
        loan_total=_as_int(top.get("crdTrFingWhl")),
        loan_kospi=_as_int(top.get("crdTrFingScrs")),
        loan_kosdaq=_as_int(top.get("crdTrFingKosdaq")),
        short_total=_as_int(top.get("crdTrLndrWhl")),
        pledge=_as_int(top.get("dpsgScrtMogFing")),
    )
    if len(items) >= 2:
        out.prev_total = _as_int(items[1].get("crdTrFingWhl")) or None
    return out


def stale_days(credit: Credit, today: dt.date | None = None) -> int | None:
    """며칠 늦은 값인가. 못 읽으면 None."""
    try:
        day = dt.date(int(credit.date[:4]), int(credit.date[4:6]), int(credit.date[6:8]))
    except (ValueError, IndexError):
        return None
    return ((today or dt.datetime.now(dt.UTC).date()) - day).days


def available() -> bool:
    return bool(os.environ.get("KRX_API_KEY", ""))
