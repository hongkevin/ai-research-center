"""피어 후보가 딛고 설 일별 종가 — **재배포 가능한 것만 쓴다.**

왜 새로 받는가
--------------
`corpus/consensus/prices/`에 817종목 × 628거래일이 이미 있지만 **쓸 수 없다.**
둘 다 걸린다:

* `.gitignore:59`가 `corpus/**/prices/`를 막는다 — **배포에 없다**
* 네이버 시세에서 받은 것이라 `ARCHITECTURE.md` §5.1이 배제한 소스다

**금융위 주식시세정보 API로 다시 받는다.** data.go.kr의 이용허락범위에 제한이
없어 재배포가 안전하고([D67](../../../docs/decisions.md#d67)), 우리가 이미 쓰는
키 그대로다.

날짜축으로 받는다
-----------------
`basDt=YYYYMMDD` 하나면 **그날 전 종목**이 온다 — 실측 2,872종목이 0.6초.
종목축으로 3,981번 부르는 대신 날짜축으로 250번이면 1년치다. **16배 싸고**,
무엇보다 [D69](../../../docs/decisions.md#d69)의 요청률 차단을 부르지 않는다.

저장 형태
---------
`{store}/prices/{symbol}.json` → `{"YYYYMMDD": 종가}`. 코퍼스와 같은 모양이라
`peer_suggest.load_prices()`가 그대로 읽는다 — 개발에서는 코퍼스를, 배포에서는
받아 둔 것을 쓴다.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

from arc.finmodel import market_facts

log = logging.getLogger("arc.finmodel.price_store")

# 한 번에 받을 행 수. 실측 전 종목이 2,872이라 3,000이면 한 페이지로 끝난다.
_ROWS = 3000

# 호출 간 최소 간격. 하루 10,000건 한도에는 여유가 있지만 **요청률**이 문제였다
# (D69). 250콜 × 0.2초 = 50초 — 어차피 배치라 체감이 없다.
_INTERVAL = 0.2

# 지수는 종목이 아니다. 시장 요인 제거(`peer_suggest`)에 쓰려고 같은 자리에
# 두지만, 후보 목록에는 나오면 안 된다 — `suggest()`가 `market`으로 뺀다.
# 파일명이 6자리 숫자가 아니라 `is_common_share()`가 후보에서 자동으로 뺀다.
MARKET_KEY = "KOSPI"


def store_dir(base: str | Path) -> Path:
    return Path(base) / "prices"


def available(base: str | Path) -> int:
    """받아 둔 종목 수. 0이면 피어 후보를 낼 수 없다."""
    d = store_dir(base)
    return sum(1 for _ in d.glob("*.json")) if d.is_dir() else 0


def _trading_days(start: dt.date, end: dt.date) -> Iterator[dt.date]:
    """주말은 건너뛴다. **공휴일은 안 거른다** — 그날은 응답이 0건이고,
    그걸 달력으로 흉내 내면 임시공휴일에서 어긋난다."""
    day = start
    while day <= end:
        if day.weekday() < 5:
            yield day
        day += dt.timedelta(days=1)


def backfill(
    base: str | Path,
    *,
    days: int = 400,
    today: dt.date | None = None,
    provider=None,
    interval: float = _INTERVAL,
) -> dict:
    """최근 `days`일치를 날짜축으로 받아 종목별 파일로 쌓는다.

    **이미 받은 날은 건너뛴다.** 매일 한 번 돌리면 하루치만 새로 받는다.

    돌려주는 것은 요약 dict — 화면·CLI가 그대로 쓴다.
    """
    from arc.data.kr.krx_price import KrxPriceProvider

    provider = provider or KrxPriceProvider()
    out = store_dir(base)
    out.mkdir(parents=True, exist_ok=True)

    end = today or dt.datetime.now(dt.UTC).date()
    start = end - dt.timedelta(days=days)
    # **두 저장소의 날짜를 따로 본다.** 시세는 다 받아 뒀는데 시장 데이터만
    # 비어 있는 상태가 정상이다(D87 이전에 받은 것이 그렇다). 시세 기준으로만
    # 건너뛰면 그 상태에서 시장 데이터가 영영 안 채워진다.
    have = _dates_on_disk(out) & market_facts.dates_on_disk(base)

    series: dict[str, dict[str, float]] = {}
    extra: dict[str, dict[str, market_facts.Row]] = {}
    listing: dict[str, str] = {}
    fetched = 0
    empty = 0
    for day in _trading_days(start, end):
        stamp = day.strftime("%Y%m%d")
        if stamp in have:
            continue
        rows = _fetch_day_full(provider, stamp)
        if not rows:
            # 공휴일이거나 아직 안 올라온 날. 정상이다.
            empty += 1
            continue
        fetched += 1
        for symbol, row in rows:
            series.setdefault(symbol, {})[stamp] = row.close
            extra.setdefault(symbol, {})[stamp] = market_facts.Row(
                high=row.high,
                low=row.low,
                turnover=row.turnover,
                cap=row.cap,
                shares=row.shares,
            )
            if row.board:
                listing[symbol] = row.board
        if interval:
            time.sleep(interval)

    written = _merge(out, series)
    market_facts.merge(base, extra, listing)
    return {
        "fetched_days": fetched,
        "empty_days": empty,
        "symbols": written,
        "total_symbols": available(base),
        "market_symbols": market_facts.available(base),
        "path": str(out),
    }


def _num(value) -> float:
    """`"20,760,209,506"` → `20760209506.0`. 못 읽으면 0 — **추정하지 않는다.**"""
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _fetch_day(provider, stamp: str) -> list[tuple[str, float]]:
    """하루치 전 종목의 **종가만**. 옛 호출부를 위한 얇은 껍데기다."""
    return [(code, row.close) for code, row in _fetch_day_full(provider, stamp)]


class _Full(NamedTuple):
    """응답 한 줄에서 우리가 쓰는 것 전부."""

    close: float
    high: float
    low: float
    turnover: float
    cap: float
    shares: float
    board: str


def _fetch_day_full(provider, stamp: str) -> list[tuple[str, _Full]]:
    """하루치 전 종목. **한 콜이다.**

    전에는 여기서 `clpr` 하나만 읽고 나머지를 버렸다. 같은 응답에 시가총액·
    거래대금·상장주식수·시장 구분이 들어 있는데, 그것들이 없어서 스몰캡
    화면도 스크리닝도 못 했다 (D87). **콜은 그대로다.**
    """
    resp = provider._client.get(
        "/getStockPriceInfo",
        params={
            "serviceKey": provider.api_key,
            "resultType": "json",
            "basDt": stamp,
            "numOfRows": str(_ROWS),
            "pageNo": "1",
        },
    )
    resp.raise_for_status()
    body = provider._check_body(resp.json())
    items = body.get("items", {}).get("item", [])
    if isinstance(items, dict):
        items = [items]

    out: list[tuple[str, _Full]] = []
    for it in items:
        code = str(it.get("srtnCd") or "").strip()
        close = _num(it.get("clpr"))
        # **종가가 0이면 그 줄은 버린다.** 지금까지의 규칙 그대로다 — 나머지
        # 필드가 와도 종가 없는 날은 시세 계열에 넣을 수 없다.
        if not code or close <= 0:
            continue
        out.append(
            (
                code,
                _Full(
                    close=close,
                    high=_num(it.get("hipr")),
                    low=_num(it.get("lopr")),
                    turnover=_num(it.get("trPrc")),
                    cap=_num(it.get("mrktTotAmt")),
                    shares=_num(it.get("lstgStCnt")),
                    board=str(it.get("mrktCtg") or "").strip(),
                ),
            )
        )
    return out


def _dates_on_disk(out: Path) -> set[str]:
    """이미 받은 날짜. **아무 종목이나 하나**만 보면 된다 — 그날 응답은
    전 종목이 한 번에 왔으므로 날짜 단위로 있거나 없거나다."""
    for path in out.glob("*.json"):
        try:
            return set(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return set()


def _merge(out: Path, series: dict[str, dict[str, float]]) -> int:
    """받은 것을 종목 파일에 합친다. 원자적 교체 — 쓰다 죽으면 반쪽 JSON이
    남아 그 종목이 영영 안 읽힌다."""
    for symbol, points in series.items():
        path = out / f"{symbol}.json"
        merged: dict[str, float] = {}
        if path.exists():
            try:
                merged = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                log.warning("깨진 시세 파일을 새로 씁니다: %s", path.name)
        merged.update(points)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({k: merged[k] for k in sorted(merged)}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)
    return len(series)


def backfill_indices(
    base: str | Path,
    *,
    days: int = 400,
    today: dt.date | None = None,
    names: tuple[str, ...] = ("코스피", "코스닥"),
    interval: float = _INTERVAL,
) -> dict:
    """지수 일별 종가를 종목과 **같은 자리에** 쌓는다.

    왜 필요한가: 브리프의 「시장 대비 초과」를 내려면 지수도 **기간 시계열**이
    있어야 한다. 지수 API는 하루치 등락만 주므로 날짜를 훑어야 하고, 그건
    시세 백필과 같은 모양이다 — 하루 1콜에 168개 지수가 다 온다.

    `KOSPI.json` / `KOSDAQ.json`으로 저장한다. 6자리 숫자가 아니라
    `is_common_share()`가 피어 후보에서 자동으로 뺀다.
    """
    from arc.data.kr.krx_index import fetch_day

    out = store_dir(base)
    out.mkdir(parents=True, exist_ok=True)
    file_of = {"코스피": "KOSPI", "코스닥": "KOSDAQ"}

    end = today or dt.datetime.now(dt.UTC).date()
    start = end - dt.timedelta(days=days)
    have = _dates_on_disk_for(out, "KOSPI")

    series: dict[str, dict[str, float]] = {}
    fetched = 0
    empty = 0
    for day in _trading_days(start, end):
        stamp = day.strftime("%Y%m%d")
        if stamp in have:
            continue
        try:
            got = fetch_day(day, names=names)
        except Exception as exc:  # noqa: BLE001 — 하루가 실패해도 나머지는 받는다
            log.warning("지수를 못 받았습니다 (%s): %s", stamp, exc)
            continue
        if not got:
            empty += 1
            continue
        fetched += 1
        for name, row in got.items():
            close = row.get("close")
            if close:
                series.setdefault(file_of.get(name, name), {})[stamp] = float(close)
        if interval:
            time.sleep(interval)

    written = _merge(out, series)
    return {"fetched_days": fetched, "empty_days": empty, "indices": written, "path": str(out)}


def _dates_on_disk_for(out: Path, stem: str) -> set[str]:
    """그 계열이 이미 받아 둔 날짜. 종목과 달리 **계열마다 따로** 본다 —
    지수는 두 개뿐이라 아무거나 하나로 대신할 수 없다."""
    path = out / f"{stem}.json"
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()
