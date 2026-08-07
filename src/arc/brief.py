"""모닝 브리프 — **아침에 이것만 봐도 되게.**

왜 필요한가
-----------
인터뷰에서 나온 말이 그대로 요구다: *"이것만 아침에 해줘도 되는데"*. RA의
하루는 새벽 뉴스로 시작해 07:30 회의로 간다. 그 사이에 필요한 것은 리포트가
아니라 **어젯밤 사이 내 종목에 무슨 일이 있었나**다.

**LLM을 안 쓴다**
-----------------
브리프는 **서술이 아니라 배열**이다. 크게 움직인 것을 위로 올리고, 그 옆에
그날 나온 공시와 기사를 놓는 것 — 그게 전부다. 문장으로 요약하면 세 가지를
잃는다: 비용이 들고, 틀릴 여지가 생기고, **RA가 원문을 안 보게 된다.**
아침에 필요한 것은 판단이 아니라 **놓친 것이 없다는 확인**이다.

시장 → 섹터 → 종목
-------------------
아침 회의가 그 순서로 간다 — **오늘 한국 증시가 어땠고, 내 섹터가 어땠고,
그래서 내 종목이 어땠나.** 종목만 나열하면 「이 종목이 5% 빠졌다」가 시장이
5% 빠져서인지 이 종목만 빠진 것인지 알 수 없고, 그 둘은 완전히 다른 얘기다.

**그래서 섹터·종목 줄에 「시장 대비」를 함께 낸다.** 초과수익이야말로 아침에
알고 싶은 것이다.

커버와 관심을 다르게 다룬다
---------------------------
`Profile`의 [커버/관심 축](store/profile.py)이 여기서 값을 한다:

* **커버 종목** — 등락 + 공시 + 기사. 놓치면 사고가 난다
* **관심 종목** — 등락만. 안 봐도 사고가 안 난다

전 종목에 공시·기사를 붙이면 커버 30종목이 API 60콜이 되고([D69](../../docs/decisions.md#d69)
의 차단이 여기서 난다), 무엇보다 **화면이 길어져서 정작 볼 것을 못 본다.**

숫자와 기사는 레인이 다르다
---------------------------
등락은 금융위 시세에서 온 검증 레인이고 기사는 [D45](../../docs/decisions.md#d45)의
미검증 레인이다. 브리프에서도 **섞지 않는다** — 화면이 배지를 붙일 자리를
잃으면 「보도됐다」가 「공시됐다」로 읽힌다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from arc.finmodel.moves import MARKET_LABEL, Move, Moves
from arc.store.profile import COVER, Profile

# 브리프에 세울 등락 구간. **전체 구간을 다 싣지 않는다** — 아침에 보는 것은
# 어제와 최근이지 1년이 아니다. 1년은 카드에서 본다.
BRIEF_KEYS = ("1d", "5d", "1m")

# 「크게 움직였다」의 기준(%). 넘으면 위로 올리고 표시한다. 코스피 일간
# 표준편차가 1% 안팎이라 3%면 눈에 띄는 움직임이다.
NOTABLE = 3.0


# 시세 기준일이 오늘인가 어제인가. **「1일 -2.4%」만 있으면 언제 얘기인지
# 모른다** — EOD 시세라 장 마감 전에는 어제 종가가 최신이다.
_WEEKDAY = ("월", "화", "수", "목", "금", "토", "일")


def when_label(asof: str, today: dt.date | None = None) -> str:
    """`20260806` → 「어제(8/6 목)」. 오늘이면 「오늘」, 더 오래됐으면 날짜만."""
    if len(asof) != 8 or not asof.isdigit():
        return ""
    day = dt.date(int(asof[:4]), int(asof[4:6]), int(asof[6:8]))
    today = today or dt.datetime.now(dt.UTC).date()
    gap = (today - day).days
    stamp = f"{day.month}/{day.day} {_WEEKDAY[day.weekday()]}"
    if gap <= 0:
        return f"오늘({stamp})"
    if gap == 1:
        return f"어제({stamp})"
    return stamp


@dataclass
class SectorLine:
    """섹터 한 줄. **내 종목들의 중앙값**이지 섹터 지수가 아니다.

    진짜 섹터 지수는 라이선스가 걸리고(D67), 무엇보다 RA가 알고 싶은 것은
    「내가 보는 그 종목들이 어땠나」다.
    """

    sector: str
    count: int = 0
    moves: list[Move] = field(default_factory=list)
    excess: dict[str, float] = field(default_factory=dict)

    @property
    def day_change(self) -> float | None:
        return next((m.change_pct for m in self.moves if m.key == "1d"), None)


@dataclass
class Line:
    """브리프 한 줄 = 종목 하나."""

    symbol: str
    company: str = ""
    sector: str = ""
    kind: str = COVER
    last_close: float | None = None
    moves: list[Move] = field(default_factory=list)
    # 어제 나온 공시. **커버 종목만 채운다.**
    filings: list[dict] = field(default_factory=list)
    # 기사 — **미검증 레인이다.** 숫자를 여기서 읽지 않는다.
    articles: list[dict] = field(default_factory=list)
    # 구간별 시장 대비 초과(%p). 시장 계열이 없으면 빈 dict.
    excess: dict[str, float] = field(default_factory=dict)

    @property
    def day_change(self) -> float | None:
        return next((m.change_pct for m in self.moves if m.key == "1d"), None)

    @property
    def notable(self) -> bool:
        """위로 올릴 만한가. 등락이 크거나, 공시가 났거나."""
        d = self.day_change
        return bool(self.filings) or (d is not None and abs(d) >= NOTABLE)


@dataclass
class Brief:
    """오늘 아침의 브리프."""

    asof: str = ""  # 시세 기준일 (YYYYMMDD)
    asof_label: str = ""  # 「어제(8/6 목)」 — 언제 얘기인지가 먼저다
    # 시장 → 섹터 → 종목. 아침 회의가 그 순서로 간다.
    market: list[Move] = field(default_factory=list)
    market_label: str = MARKET_LABEL
    # 코스피·코스닥 실제 지수. **아침 회의가 여기서 시작한다.**
    indices: list[dict] = field(default_factory=list)
    sectors: list[SectorLine] = field(default_factory=list)
    cover: list[Line] = field(default_factory=list)
    watch: list[Line] = field(default_factory=list)
    note: str = ""

    @property
    def empty(self) -> bool:
        return not self.cover and not self.watch

    @property
    def filing_count(self) -> int:
        return sum(len(x.filings) for x in self.cover)

    @property
    def notable_count(self) -> int:
        return sum(1 for x in self.cover if x.notable)


def _pick(moves: Moves | None, keys: tuple[str, ...]) -> list[Move]:
    if moves is None:
        return []
    by_key = {m.key: m for m in moves.items}
    return [by_key[k] for k in keys if k in by_key]


def _rank(lines: list[Line]) -> list[Line]:
    """**크게 움직인 것이 위로.** 공시가 난 종목은 그보다 더 위로.

    이름순으로 세우면 매일 같은 순서라 눈이 훑고 지나간다. 아침에 알고 싶은
    것은 「무엇이 달라졌나」이고, 순서가 그 답의 일부다.
    """
    return sorted(
        lines,
        key=lambda x: (
            0 if x.filings else 1,
            -abs(x.day_change) if x.day_change is not None else 0.0,
            x.company or x.symbol,
        ),
    )


def build_brief(
    profile: Profile,
    moves: dict[str, Moves],
    *,
    filings: dict[str, list[dict]] | None = None,
    articles: dict[str, list[dict]] | None = None,
    market: Moves | None = None,
    indices: list[dict] | None = None,
    asof: str = "",
    today: dt.date | None = None,
    keys: tuple[str, ...] = BRIEF_KEYS,
) -> Brief:
    """프로필 + 시세 + 공시 + 기사 → 브리프. **순수 함수다.**

    가져오는 것은 부르는 쪽이 한다 — 그래야 캐시·한도·실패 처리를 한 군데서
    보고, 이 함수는 테스트가 쉽다.
    """
    filings = filings or {}
    articles = articles or {}
    brief = Brief(asof=asof)

    for stock in profile.stocks:
        line = Line(
            symbol=stock.symbol,
            company=stock.company or stock.symbol,
            sector=stock.sector,
            kind=stock.kind,
            last_close=(moves.get(stock.symbol).last_close if moves.get(stock.symbol) else None),
            moves=_pick(moves.get(stock.symbol), keys),
        )
        if stock.kind == COVER:
            # 관심 종목에는 안 붙인다 — API 호출이 배로 늘고 화면이 길어져
            # 정작 볼 것을 못 본다.
            line.filings = list(filings.get(stock.symbol) or [])
            line.articles = list(articles.get(stock.symbol) or [])
            brief.cover.append(line)
        else:
            brief.watch.append(line)

    brief.cover = _rank(brief.cover)
    brief.watch = _rank(brief.watch)
    brief.asof_label = when_label(asof, today) if asof else ""
    brief.market = _pick(market, keys)
    if market is not None and market.company:
        brief.market_label = market.company
    brief.indices = list(indices or [])
    # **섹터는 커버 종목으로만 낸다.** 관심 종목까지 섞으면 「내 섹터가
    # 어땠나」가 아니라 「내가 보는 것들이 어땠나」가 된다.
    brief.sectors = _sectors(brief.cover, keys)

    # **시장 대비 초과.** 아침에 알고 싶은 것은 이쪽이다 — 5% 빠진 것이
    # 시장이 5% 빠져서인지 이 종목만인지는 완전히 다른 얘기다.
    #
    # 섹터를 **만든 뒤에** 채운다. 순서를 뒤집었다가 빈 목록에 값을 넣고
    # 곧바로 덮어써서 섹터 줄만 초과가 비어 있었다.
    for row in (*brief.cover, *brief.watch, *brief.sectors):
        row.excess = {
            m.key: value for m in row.moves if (value := relative(m, brief.market)) is not None
        }
    brief.note = _note(brief, profile)
    return brief


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    values = sorted(values)
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def _sectors(lines: list[Line], keys: tuple[str, ...]) -> list[SectorLine]:
    """섹터별 중앙값. **평균이 아니라 중앙값이다** — 한 종목이 30% 뛰면
    평균은 그 종목 얘기가 되고 섹터 얘기가 아니게 된다."""
    buckets: dict[str, list[Line]] = {}
    for line in lines:
        buckets.setdefault(line.sector or "미분류", []).append(line)

    out: list[SectorLine] = []
    for sector, group in buckets.items():
        moves: list[Move] = []
        for key in keys:
            picked = [m for line in group for m in line.moves if m.key == key]
            if not picked:
                continue
            value = _median([m.change_pct for m in picked if m.change_pct is not None])
            sample = picked[0]
            moves.append(
                Move(
                    key=key,
                    label=sample.label,
                    change_pct=value,
                    from_date=sample.from_date,
                    to_date=sample.to_date,
                    days=sample.days,
                )
            )
        out.append(SectorLine(sector=sector, count=len(group), moves=moves))
    # 크게 움직인 섹터가 위로. 같은 이유로 이름순이 아니다.
    return sorted(out, key=lambda x: -abs(x.day_change) if x.day_change is not None else 0.0)


def _note(brief: Brief, profile: Profile) -> str:
    """맨 위 한 줄. **없으면 없다고 말한다.**"""
    if not profile.stocks:
        return "커버 종목을 먼저 넣으십시오 — 「내 커버리지」에서 정합니다."
    if not brief.asof:
        return "시세를 아직 받지 않아 등락을 낼 수 없습니다."
    parts = []
    if brief.filing_count:
        parts.append(f"공시 {brief.filing_count}건")
    moved = brief.notable_count
    if moved:
        parts.append(f"{NOTABLE:.0f}% 이상 움직인 종목 {moved}건")
    if not parts:
        return "커버 종목에 큰 움직임도 새 공시도 없습니다."
    return " · ".join(parts)


def index_line(indices: list[dict]) -> str:
    """「코스피 6,296 -4.58% · 코스닥 802 +0.26%」 한 줄."""
    out = []
    for i in indices:
        close = i.get("close")
        pct = i.get("change_pct")
        if close is None:
            continue
        out.append(
            f"{i.get('name', '')} {close:,.0f} {pct:+.2f}%"
            if pct is not None
            else f"{i.get('name', '')} {close:,.0f}"
        )
    return " · ".join(out)


def relative(line_move: Move | None, market: list[Move]) -> float | None:
    """시장 대비 초과. **아침에 알고 싶은 것은 이쪽이다** — 5% 빠진 것이
    시장이 5% 빠져서인지 이 종목만인지는 완전히 다른 얘기다."""
    if line_move is None or line_move.change_pct is None:
        return None
    base = next((m.change_pct for m in market if m.key == line_move.key), None)
    return None if base is None else line_move.change_pct - base
