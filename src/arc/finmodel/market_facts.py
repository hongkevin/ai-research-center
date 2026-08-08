"""시가총액·거래대금·시장 구분 — **이미 받고 있던 것을 안 버린다.**

왜 새로 만드는가
----------------
[D87](../../../docs/decisions.md#d87)에서 확인한 것: 매일 도는 금융위
`getStockPriceInfo` 응답 한 줄이 이만큼 온다.

    {"srtnCd": "900110", "mrktCtg": "KOSDAQ", "clpr": "1126", "hipr": "1171",
     "lopr": "1094", "trqu": "16498", "trPrc": "18443269",
     "lstgStCnt": "18437131", "mrktTotAmt": "20760209506"}

**우리는 `clpr` 하나만 남기고 나머지를 버렸다**(`price_store._fetch_day`).
그래서 시가총액도, 거래대금도, 코스피인지 코스닥인지도 몰랐다.

미드스몰캡 애널리스트의 리포트 첫 화면이 정확히 이 값들이다:

    STOCK DATA    주가 · KOSDAQ pt · 52주 최고가 · 60일 평균 거래대금
    COMPANY DATA  발행주식수 · 시가총액 · 최대주주 지분율

**API 콜이 안 늘어난다.** 같은 응답에서 필드를 더 읽을 뿐이다.

왜 `prices/`에 안 넣나
----------------------
`prices/{symbol}.json`은 `{"YYYYMMDD": 종가}`고 그 모양에 2,987개 파일과
`peer_suggest.load_prices()`·브리프·차트가 물려 있다. 값을 배열로 바꾸면 전부
같이 고쳐야 하고, **한 군데라도 놓치면 종가 자리에 배열이 들어가 조용히 틀린
등락이 나온다.** 그래서 **병렬 저장소**로 둔다 — 종가는 그대로 `prices/`에서 오고,
여기는 나머지만 맡는다.

없으면 없다고 한다
------------------
백필 전에는 이 저장소가 비어 있고, 그때 `Snapshot`은 값 자리를 `None`으로 두고
`unavailable`에 이유를 적는다. **추정하지 않는다** — `valuation.py`가 지키는 규칙
그대로다. 「시가총액 미상」이 지어낸 시가총액보다 낫다.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

log = logging.getLogger("arc.finmodel.market_facts")

# 60일 평균 거래대금 — 그의 리포트 STOCK DATA에 있는 그 구간이다. 스몰캡에서
# 유동성은 **투자 가능성 자체**라 하루치로는 못 본다.
TURNOVER_DAYS = 60

# 52주 = 거래일 약 250일. 달력 52주로 자르면 휴장일만큼 짧아진다.
HIGH_DAYS = 250

# 시장 구분. 코넥스는 우리 대상이 아니지만 **버리지 않고 그대로 적는다** —
# 「모른다」와 「코넥스다」는 다른 말이다.
KOSPI, KOSDAQ = "KOSPI", "KOSDAQ"

_LISTING = "_listing.json"


class Row(NamedTuple):
    """하루치. **종가는 없다** — 그것은 `prices/`가 갖고 있다."""

    high: float
    low: float
    turnover: float  # 거래대금(원)
    cap: float  # 시가총액(원)
    shares: float  # 상장주식수


@dataclass
class Snapshot:
    """리포트 사이드바 한 뭉치. **없는 값은 `None`이고 이유가 남는다.**"""

    symbol: str
    asof: str = ""
    board: str = ""
    cap: float | None = None
    shares: float | None = None
    avg_turnover: float | None = None
    high_52w: float | None = None
    # **무슨 기준의 최고가인가.** 종가와 장중 고가는 2,870원 차이가 났다
    high_basis: str = ""
    # 왜 비었나. 화면이 이걸 그대로 적는다
    unavailable: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return self.cap is None and self.avg_turnover is None and self.high_52w is None


def store_dir(base: str | Path) -> Path:
    return Path(base) / "market"


def available(base: str | Path) -> int:
    d = store_dir(base)
    return sum(1 for p in d.glob("*.json") if not p.name.startswith("_")) if d.is_dir() else 0


def load_facts(base: str | Path, symbol: str) -> dict[str, Row]:
    """`{"YYYYMMDD": Row}`. 없으면 빈 dict — **예외를 던지지 않는다.**

    백필 전이라 파일이 없는 것은 정상이고, 그때마다 터지면 리포트 전체가 못
    나온다. 값이 없다는 것은 `Snapshot.unavailable`이 말한다.
    """
    path = store_dir(base) / f"{symbol}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, Row] = {}
    for stamp, values in raw.items():
        if isinstance(values, list) and len(values) == 5:
            try:
                out[stamp] = Row(*(float(v) for v in values))
            except (TypeError, ValueError):
                continue
    return out


def load_listing(base: str | Path) -> dict[str, str]:
    """`{종목코드: "KOSDAQ"}`. 종목당 하나뿐이라 시계열로 안 둔다."""
    try:
        raw = json.loads((store_dir(base) / _LISTING).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def board(base: str | Path, symbol: str) -> str:
    """코스피인가 코스닥인가. 모르면 빈 문자열 — **KOSPI로 넘겨짚지 않는다.**

    벤치마크가 여기서 갈린다. 코스닥 종목을 코스피 대비로 재면 그 초과수익은
    시장 얘기지 종목 얘기가 아니다.
    """
    return load_listing(base).get(symbol, "")


def _window(facts: dict[str, Row], days: int) -> list[Row]:
    """최근 `days` 거래일. **달력이 아니라 있는 날로 센다.**"""
    return [facts[k] for k in sorted(facts)[-days:]]


def avg_turnover(facts: dict[str, Row], *, days: int = TURNOVER_DAYS) -> float | None:
    """평균 거래대금(원). **구간이 짧으면 안 낸다.**

    20일치로 「60일 평균」이라고 하면 그건 다른 값이다. 절반을 문턱으로 두는
    이유는 신규 상장·거래정지에서 구간이 원래 짧을 수 있기 때문인데, 그때는
    라벨이 실제 일수를 말해야 한다 — `snapshot()`이 그 일을 한다.
    """
    rows = _window(facts, days)
    if len(rows) < days // 2:
        return None
    return sum(r.turnover for r in rows) / len(rows)


def high_52w(
    facts: dict[str, Row], *, days: int = HIGH_DAYS, closes: dict[str, float] | None = None
) -> tuple[float | None, str]:
    """52주 최고가와 **무슨 기준인지**.

    처음에는 장중 고가(`hipr`)로 냈다가 실측에서 틀린 것을 알았다. 씨이랩
    리포트(2026-08-07)가 적은 「52주 최고가 13,130원」은 우리 데이터에서
    **2026-07-09 종가**와 정확히 같고, 같은 날 장중 고가는 16,000원이다 —
    2,870원 차이다.

    **업계 관행이 종가 기준이다.** 우리 값이 리포트와 안 맞으면 그 순간 이
    화면 전체가 못 믿을 것이 되므로 관행을 따르고, **무슨 기준인지 라벨로
    말한다.** 종가 계열은 `prices/`에 있어 밖에서 넘겨받는다.
    """
    if closes:
        window = [closes[k] for k in sorted(closes)[-days:] if closes[k] > 0]
        if window:
            return max(window), "종가 기준"
    rows = _window(facts, days)
    highs = [r.high for r in rows if r.high > 0]
    return (max(highs), "장중 고가 기준") if highs else (None, "")


def latest(facts: dict[str, Row]) -> tuple[str, Row] | None:
    if not facts:
        return None
    stamp = max(facts)
    return stamp, facts[stamp]


def snapshot(base: str | Path, symbol: str, *, closes: dict[str, float] | None = None) -> Snapshot:
    """STOCK DATA 한 뭉치. **비면 왜 비었는지가 같이 온다.**

    `closes`(=`prices/{symbol}.json`)를 주면 52주 최고가를 **종가 기준**으로
    낸다 — 업계 관행이고, 안 맞으면 화면 전체가 못 믿을 것이 된다(`high_52w`).
    """
    out = Snapshot(symbol=symbol, board=board(base, symbol))
    facts = load_facts(base, symbol)
    if not facts:
        out.unavailable.append("시장 데이터를 아직 받지 않았습니다 (arc prices backfill)")
        return out

    last = latest(facts)
    if last:
        out.asof, row = last
        out.cap = row.cap or None
        out.shares = row.shares or None

    out.avg_turnover = avg_turnover(facts)
    if out.avg_turnover is None:
        out.unavailable.append(f"{TURNOVER_DAYS}일 평균 거래대금 (거래일 {len(facts)}일치뿐)")
    out.high_52w, out.high_basis = high_52w(facts, closes=closes)
    if out.high_52w is None:
        out.unavailable.append("52주 최고가")
    if not out.board:
        out.unavailable.append("시장 구분")
    return out


# ── 유니버스 — 발굴이 딛고 설 곳 ────────────────────────────────────────
#
# `peer_suggest.suggest()`는 전 종목 2,987개를 훑어 상관을 내지만 **크기를
# 모른다.** 「숨겨진 수혜주」는 정의상 작고 안 알려진 종목이라, 상관만으로는
# 대형주가 후보 맨 위에 앉는다. 여기가 그 필터다.


@dataclass
class Screen:
    """스몰캡 유니버스 조건. **전부 선택이다** — 안 주면 안 거른다."""

    max_cap: float | None = None  # 시가총액 상한(원)
    min_cap: float | None = None
    min_turnover: float | None = None  # 60일 평균 거래대금 하한(원)
    boards: tuple[str, ...] = ()  # ("KOSDAQ",) 처럼


def screen(base: str | Path, symbols: list[str], cond: Screen) -> list[str]:
    """조건에 맞는 종목만. **데이터가 없는 종목은 뺀다.**

    「모르니까 통과」로 두면 백필 전에 전 종목이 통과하고, 그 목록은
    「시총 3천억 이하」라고 이름 붙은 채 아무것도 안 거른 목록이 된다.
    """
    listing = load_listing(base)
    out: list[str] = []
    for symbol in symbols:
        if cond.boards and listing.get(symbol, "") not in cond.boards:
            continue
        facts = load_facts(base, symbol)
        last = latest(facts)
        if last is None:
            continue
        cap = last[1].cap
        if cond.max_cap is not None and not (0 < cap <= cond.max_cap):
            continue
        if cond.min_cap is not None and cap < cond.min_cap:
            continue
        if cond.min_turnover is not None:
            turnover = avg_turnover(facts)
            if turnover is None or turnover < cond.min_turnover:
                continue
        out.append(symbol)
    return out


# ── 쓰기 — `price_store`가 부른다 ───────────────────────────────────────


def merge(base: str | Path, rows: dict[str, dict[str, Row]], listing: dict[str, str]) -> int:
    """받은 것을 종목 파일에 합친다. `price_store._merge`와 같은 원자적 교체."""
    out = store_dir(base)
    out.mkdir(parents=True, exist_ok=True)

    for symbol, points in rows.items():
        path = out / f"{symbol}.json"
        merged: dict[str, list[float]] = {}
        if path.exists():
            try:
                merged = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                log.warning("깨진 시장 데이터 파일을 새로 씁니다: %s", path.name)
        merged.update({k: list(v) for k, v in points.items()})
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({k: merged[k] for k in sorted(merged)}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)

    if listing:
        path = out / _LISTING
        have = load_listing(base)
        have.update(listing)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(have, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    return len(rows)


def dates_on_disk(base: str | Path) -> set[str]:
    """이미 받은 날짜. `price_store._dates_on_disk`와 같은 규칙 — 아무 종목이나
    하나면 된다. 그날 응답은 전 종목이 한 번에 왔다."""
    d = store_dir(base)
    if not d.is_dir():
        return set()
    for path in d.glob("*.json"):
        if path.name.startswith("_"):
            continue
        try:
            return set(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return set()


def display_cap(cap: float | None) -> str:
    """`99_012_000_000` → `990억원`. **억 단위로 말한다** — 스몰캡의 말투다."""
    if not cap:
        return ""
    eok = cap / 100_000_000
    if eok >= 10_000:
        return f"{eok / 10_000:,.1f}조원"
    return f"{eok:,.0f}억원"


def stale_days(asof: str, today: dt.date | None = None) -> int | None:
    """기준일이 며칠 묵었나. 화면이 「언제 값인지」를 말할 수 있게."""
    if len(asof) != 8 or not asof.isdigit():
        return None
    day = dt.date(int(asof[:4]), int(asof[4:6]), int(asof[6:]))
    return ((today or dt.datetime.now(dt.UTC).date()) - day).days
