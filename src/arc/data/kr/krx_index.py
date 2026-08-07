"""시장 지수 — 코스피·코스닥.

왜 필요한가
-----------
모닝 브리프는 **시장 → 섹터 → 종목** 순으로 간다. 종목만 나열하면 「이 종목이
5% 빠졌다」가 **시장이 5% 빠져서인지 이 종목만 빠진 것인지** 알 수 없고, 그
둘은 완전히 다른 얘기다.

전에는 전 종목 수익률의 중앙값으로 시장을 흉내 냈다. 그건 지수 API를 못 쓰던
동안의 임시였고([D67](../../../docs/decisions.md#d67)의 403 = 키 미등록),
활용신청이 끝나 **진짜 지수**를 쓴다.

같은 창구, 같은 키
------------------
`금융위원회_지수시세정보`도 data.go.kr이고 이용허락범위에 제한이 없다 —
재배포가 안전하다. `KRX_API_KEY` 그대로 쓴다(Encoding 키의 `unquote`도 동일).

**하루 1콜이면 168개 지수가 다 온다.** 종목축으로 도는 것과 달리 여기는
날짜축이 원래 단위라 요청률 걱정이 없다(D69).
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import urllib.parse

import httpx

log = logging.getLogger("arc.data.kr.krx_index")

BASE_URL = "https://apis.data.go.kr/1160100/service/GetMarketIndexInfoService"

# 브리프에 세울 지수. **코스피·코스닥 둘이면 된다** — 168개를 다 보여주면
# 아침에 볼 것이 늘어날 뿐이다.
MAIN = ("코스피", "코스닥")


class KrxIndexError(Exception):
    pass


def _key(api_key: str | None = None) -> str:
    raw = api_key or os.environ.get("KRX_API_KEY", "")
    # Encoding 키는 `%`가 박혀 있어 httpx가 다시 인코딩하면 403이 난다 (D60).
    return urllib.parse.unquote(raw) if "%" in raw else raw


def fetch_day(
    day: dt.date,
    *,
    api_key: str | None = None,
    client: httpx.Client | None = None,
    names: tuple[str, ...] = MAIN,
) -> dict[str, dict]:
    """하루치 지수. `{지수명: {close, change_pct, ...}}`.

    **휴장일은 빈 dict다.** 예외가 아니다 — 달력으로 거래일을 흉내 내면
    임시공휴일에서 어긋난다.
    """
    key = _key(api_key)
    if not key:
        raise KrxIndexError("KRX_API_KEY가 설정되지 않았습니다 (.env 참조)")
    owned = client is None
    client = client or httpx.Client(base_url=BASE_URL, timeout=30.0)
    try:
        resp = client.get(
            "/getStockMarketIndex",
            params={
                "serviceKey": key,
                # **이게 없으면 XML이 온다.** 기본이 XML이라 `resp.json()`이
                # 「Expecting value: line 1 column 1」로 죽고, 호출자는 그걸
                # 「휴장일」로 읽어 지수가 조용히 사라진다.
                "resultType": "json",
                "basDt": day.strftime("%Y%m%d"),
                "numOfRows": "500",
                "pageNo": "1",
            },
        )
        resp.raise_for_status()
        payload = resp.json()
    finally:
        if owned:
            client.close()
    return parse_indices(payload, names=names)


def parse_indices(payload: dict, *, names: tuple[str, ...] = MAIN) -> dict[str, dict]:
    """응답 → `{지수명: 값}`. **순수 파싱이라 테스트 대상이다.**

    응답 봉투가 두 모양으로 온다 — `{"response": {...}}`와 `{"header":…, "body":…}`.
    실측으로 둘 다 봤다.
    """
    body = (payload.get("response") or payload).get("body") or {}
    header = (payload.get("response") or payload).get("header") or {}
    if header.get("resultCode") not in (None, "00"):
        raise KrxIndexError(f"{header.get('resultCode')}: {header.get('resultMsg')}")

    items = (body.get("items") or {}).get("item") or []
    if isinstance(items, dict):
        items = [items]

    out: dict[str, dict] = {}
    for it in items:
        name = str(it.get("idxNm") or "").strip()
        if name not in names:
            continue
        out[name] = {
            "name": name,
            "date": str(it.get("basDt") or ""),
            "close": _num(it.get("clpr")),
            # **`.26` 같은 형태로 온다.** 앞의 0이 빠져 있어 float()가 그냥
            # 되긴 하지만, 부호가 붙은 `-5.11`도 섞여 있어 그대로 파싱한다.
            "change_pct": _num(it.get("fltRt")),
            "members": _int(it.get("epyItmsCnt")),
        }
    return out


def _num(raw: object) -> float | None:
    try:
        return float(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _int(raw: object) -> int | None:
    try:
        return int(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return None
