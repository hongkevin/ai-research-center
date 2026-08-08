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
from arc.llm.josa import attach
from arc.store.profile import COVER, Profile

# 브리프에 세울 등락 구간. **전체 구간을 다 싣지 않는다** — 아침에 보는 것은
# 어제와 최근이지 1년이 아니다. 1년은 카드에서 본다.
BRIEF_KEYS = ("1d", "5d", "1m")


# ── 하루에 세 번 ──────────────────────────────────────────────────────
#
# 요구가 그대로였다: *"지금은 모닝 브리프지만, 장중 브리프(예를 들어
# 점심시간), 마감 후 브리프 등이 있을 수도 있겠다"*.
#
# **셋을 가르는 것은 시각이 아니라 「지금 무엇이 확정됐나」다.** 우리 시세는
# 금융위 EOD라 장중에는 오늘 값이 **없다**. 그때 「1일 -2.4%」를 세우면 그건
# 어제 얘기인데 화면은 오늘로 읽힌다 — 그래서 장중에는 **주가를 아예 뺀다.**
# 대신 장중에 실제로 갱신되는 것(공시·센티)을 앞으로 올린다.
MORNING, MIDDAY, CLOSE = "morning", "midday", "close"

SESSIONS: tuple[tuple[str, str, int, str], ...] = (
    # key, 이름, 시작 시각(KST), 무엇을 보는가
    (MORNING, "모닝 브리프", 0, "어젯밤 사이 무슨 일이 있었나"),
    (MIDDAY, "장중 브리프", 9, "지금 무엇이 나오고 무슨 말이 도나"),
    (CLOSE, "마감 브리프", 16, "오늘 어떻게 끝났나"),
)

# 장중에는 주가를 안 낸다. **오늘 값이 없기 때문이지 덜 중요해서가 아니다.**
PRICELESS = (MIDDAY,)

KST = dt.timezone(dt.timedelta(hours=9))


def current_session(now: dt.datetime | None = None) -> str:
    """지금이 어느 브리프인가. **한국 장 시간 기준이다.**"""
    hour = (now or dt.datetime.now(dt.UTC)).astimezone(KST).hour
    picked = MORNING
    for key, _, start, _ in SESSIONS:
        if hour >= start:
            picked = key
    return picked


def session_label(key: str) -> tuple[str, str]:
    """`("장중 브리프", "지금 무엇이 나오고 무슨 말이 도나")`."""
    for k, label, _, why in SESSIONS:
        if k == key:
            return label, why
    return "브리프", ""


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
    """섹터 한 줄. **모수를 밝힌다.**

    진짜 섹터 지수는 라이선스가 걸린다(D67). 대신 종목들의 중앙값을 쓰는데,
    **어느 종목들이냐가 곧 이 줄의 뜻이다**:

    * `basis="peer"` — 그 섹터의 피어 그룹 **전체**. 내가 3개만 커버해도
      섹터는 12개로 움직이므로, 이쪽이 「섹터가 어땠나」에 대한 답이다
    * `basis="mine"` — 피어 그룹이 없어 내 종목만으로 낸 것. 그러면 이 줄은
      「내 것들이 어땠나」지 섹터 얘기가 아니고, **화면이 그렇게 말해야 한다**

    D68의 뒤집기다 — 피어 그룹이 곧 그 섹터의 조작적 정의라면, 섹터 뷰의
    모수도 거기서 와야 한다.
    """

    sector: str
    count: int = 0
    moves: list[Move] = field(default_factory=list)
    excess: dict[str, float] = field(default_factory=dict)
    # 중앙값을 낸 종목 수. `count`(내 종목 수)와 다를 수 있다
    universe: int = 0
    basis: str = "mine"

    @property
    def day_change(self) -> float | None:
        return next((m.change_pct for m in self.moves if m.key == "1d"), None)

    @property
    def basis_label(self) -> str:
        """**모수를 한 마디로.** 없으면 화면이 이 줄을 섹터 지수로 읽는다."""
        if self.basis == "peer":
            return f"피어 {self.universe}종목 중앙값 (내 {self.count})"
        return f"내 {self.count}종목 중앙값"


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
    # 최근 수주. **공시 목록에 섞어 두면 안 보인다** (D87) — 미드스몰캡
    # 애널리스트의 리포트는 수주로 시작하고, 금액보다 「최근 매출 대비 %」가
    # 먼저다. 1,504억은 회사에 따라 사소하기도 하고 회사를 바꾸기도 한다
    contracts: list[dict] = field(default_factory=list)
    # 기사 — **미검증 레인이다.** 숫자를 여기서 읽지 않는다.
    articles: list[dict] = field(default_factory=list)
    # 구간별 시장 대비 초과(%p). 시장 계열이 없으면 빈 dict.
    excess: dict[str, float] = field(default_factory=dict)
    # 오늘 텔레그램에서 몇 번 나왔나. **미검증 레인이다** (D45)
    mention_count: int = 0

    @property
    def day_change(self) -> float | None:
        return next((m.change_pct for m in self.moves if m.key == "1d"), None)

    @property
    def notable(self) -> bool:
        """위로 올릴 만한가. 등락이 크거나, 공시가 났거나, **수주가 났거나.**"""
        d = self.day_change
        return bool(self.filings or self.contracts) or (d is not None and abs(d) >= NOTABLE)


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
    # 환율·금리. 지수와 **날짜가 다를 수 있어** 값마다 자기 날짜를 달고 온다
    macro: list[dict] = field(default_factory=list)
    # 어느 브리프인가 — 아침·장중·마감. **장중에는 주가가 없다**
    session: str = MORNING
    session_label: str = ""
    session_why: str = ""
    # 장중에 갱신되는 것. 주가를 뺀 자리를 이것이 채운다
    mentions: list[dict] = field(default_factory=list)
    # **못 읽은 것.** 이 화면의 존재 이유가 「놓친 것이 없다는 확인」인데,
    # 공시·기사·지수·매크로 실패가 전부 빈 목록으로 삼켜지면 그 확인이
    # 거짓이 된다. 「없다」와 「못 읽었다」는 다른 말이다.
    unavailable: list[str] = field(default_factory=list)
    # 칸마다 맨 위 한 줄. **LLM이 아니라 배열이다** — 아래 숫자를 다시 읽어
    # 문장 꼴로 세운 것이고, 없는 사실은 여기서도 없다
    heads: dict[str, str] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.cover and not self.watch

    @property
    def filing_count(self) -> int:
        return sum(len(x.filings) for x in self.cover)

    @property
    def notable_count(self) -> int:
        return sum(1 for x in self.cover if x.notable)

    @property
    def contract_count(self) -> int:
        return sum(len(x.contracts) for x in self.cover)

    @property
    def contract_lines(self) -> list[Line]:
        """수주가 난 종목만. **맨 위 자기 칸을 가진다** (D87)."""
        return [x for x in self.cover if x.contracts]


def _pick(moves: Moves | None, keys: tuple[str, ...]) -> list[Move]:
    if moves is None:
        return []
    by_key = {m.key: m for m in moves.items}
    return [by_key[k] for k in keys if k in by_key]


def _rank(lines: list[Line]) -> list[Line]:
    """**크게 움직인 것이 위로.** 공시가 난 종목은 그보다 더 위로.

    이름순으로 세우면 매일 같은 순서라 눈이 훑고 지나간다. 아침에 알고 싶은
    것은 「무엇이 달라졌나」이고, 순서가 그 답의 일부다.

    **장중에는 주가가 없다.** 그때 이름순으로 떨어지면 순서가 정보를 잃으므로
    언급 수가 그 자리를 대신한다 — 장중에 실제로 갱신되는 것이 그것이다.
    """
    return sorted(
        lines,
        key=lambda x: (
            0 if x.filings else 1,
            -abs(x.day_change) if x.day_change is not None else 0.0,
            -x.mention_count,
            x.company or x.symbol,
        ),
    )


def build_brief(
    profile: Profile,
    moves: dict[str, Moves],
    *,
    filings: dict[str, list[dict]] | None = None,
    # `{종목코드: [수주…]}`. 계약상대방·금액·최근 매출 대비 %는 **공시 서식의
    # 칸에 적힌 값**이지 우리가 계산한 것이 아니다 (`data/kr/contracts.py`)
    contracts: dict[str, list[dict]] | None = None,
    articles: dict[str, list[dict]] | None = None,
    market: Moves | None = None,
    indices: list[dict] | None = None,
    macro: list[dict] | None = None,
    # 부르는 쪽이 못 읽은 것의 이름을 넘긴다 — 「공시」·「기사」·「지수」·「매크로」
    unavailable: list[str] | None = None,
    # `{종목코드: 오늘 언급 수}`. 장중 브리프가 주가 대신 세우는 것
    mentions: dict[str, int] | None = None,
    session: str = "",
    # 섹터별 모수. `{섹터: [종목코드…]}` — **피어 그룹 전체**가 들어온다.
    # 비면 내 종목만으로 떨어지고, 그 사실이 `SectorLine.basis`에 남는다
    universe: dict[str, list[str]] | None = None,
    asof: str = "",
    today: dt.date | None = None,
    keys: tuple[str, ...] = BRIEF_KEYS,
) -> Brief:
    """프로필 + 시세 + 공시 + 기사 → 브리프. **순수 함수다.**

    가져오는 것은 부르는 쪽이 한다 — 그래야 캐시·한도·실패 처리를 한 군데서
    보고, 이 함수는 테스트가 쉽다.
    """
    filings = filings or {}
    contracts = contracts or {}
    articles = articles or {}
    mentions = mentions or {}
    session = session or current_session()
    brief = Brief(asof=asof, session=session)
    brief.session_label, brief.session_why = session_label(session)

    # **장중에는 주가를 안 낸다.** 우리 시세는 EOD라 오늘 값이 없고, 어제
    # 값을 「1일」이라고 세우면 화면은 그걸 오늘로 읽는다.
    priceless = session in PRICELESS
    if priceless:
        moves, market, indices = {}, None, None

    for stock in profile.stocks:
        line = Line(
            symbol=stock.symbol,
            company=stock.company or stock.symbol,
            sector=stock.sector,
            kind=stock.kind,
            last_close=(moves.get(stock.symbol).last_close if moves.get(stock.symbol) else None),
            moves=_pick(moves.get(stock.symbol), keys),
        )
        line.mention_count = mentions.get(stock.symbol, 0)
        if stock.kind == COVER:
            # 관심 종목에는 안 붙인다 — API 호출이 배로 늘고 화면이 길어져
            # 정작 볼 것을 못 본다.
            line.filings = list(filings.get(stock.symbol) or [])
            line.contracts = list(contracts.get(stock.symbol) or [])
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
    # **장중에는 섹터도 안 낸다.** 등락이 없으면 이름만 남은 줄이 서고,
    # 그 빈 줄이 「섹터가 안 움직였다」로 읽힌다.
    brief.sectors = (
        [] if priceless else _sectors(brief.cover, keys, moves=moves, universe=universe or {})
    )

    # **시장 대비 초과.** 아침에 알고 싶은 것은 이쪽이다 — 5% 빠진 것이
    # 시장이 5% 빠져서인지 이 종목만인지는 완전히 다른 얘기다.
    #
    # 섹터를 **만든 뒤에** 채운다. 순서를 뒤집었다가 빈 목록에 값을 넣고
    # 곧바로 덮어써서 섹터 줄만 초과가 비어 있었다.
    for row in (*brief.cover, *brief.watch, *brief.sectors):
        row.excess = {
            m.key: value for m in row.moves if (value := relative(m, brief.market)) is not None
        }
    brief.macro = list(macro or [])
    brief.unavailable = list(unavailable or [])
    brief.mentions = [
        {"symbol": s, "count": n}
        for s, n in sorted(mentions.items(), key=lambda kv: -kv[1])
        if n > 0
    ]
    brief.note = _note(brief, profile)
    brief.heads = _heads(brief)
    return brief


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    values = sorted(values)
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def _sectors(
    lines: list[Line],
    keys: tuple[str, ...],
    *,
    moves: dict[str, Moves] | None = None,
    universe: dict[str, list[str]] | None = None,
) -> list[SectorLine]:
    """섹터별 중앙값. **평균이 아니라 중앙값이다** — 한 종목이 30% 뛰면
    평균은 그 종목 얘기가 되고 섹터 얘기가 아니게 된다.

    **모수는 피어 그룹 전체다.** 내가 조선에서 3종목만 커버해도 섹터가 어땠나는
    12종목이 답한다. 피어 그룹이 없는 섹터만 내 종목으로 떨어지고, 그때는
    `basis="mine"`이 남아 화면이 「섹터」라고 말하지 않는다.
    """
    moves = moves or {}
    universe = universe or {}

    buckets: dict[str, list[Line]] = {}
    for line in lines:
        buckets.setdefault(line.sector or "미분류", []).append(line)

    out: list[SectorLine] = []
    for sector, group in buckets.items():
        # 모수를 고른다. 피어 그룹이 있으면 그것, 없으면 내 종목.
        symbols = [s for s in (universe.get(sector) or []) if s in moves]
        basis = "peer" if symbols else "mine"
        pool: list[list[Move]] = (
            [_pick(moves[s], keys) for s in symbols] if symbols else [x.moves for x in group]
        )

        rows: list[Move] = []
        for key in keys:
            picked = [m for items in pool for m in items if m.key == key]
            if not picked:
                continue
            value = _median([m.change_pct for m in picked if m.change_pct is not None])
            sample = picked[0]
            rows.append(
                Move(
                    key=key,
                    label=sample.label,
                    change_pct=value,
                    from_date=sample.from_date,
                    to_date=sample.to_date,
                    days=sample.days,
                )
            )
        out.append(
            SectorLine(
                sector=sector,
                count=len(group),
                moves=rows,
                universe=len(symbols) if symbols else len(group),
                basis=basis,
            )
        )
    # 크게 움직인 섹터가 위로. 같은 이유로 이름순이 아니다.
    return sorted(out, key=lambda x: -abs(x.day_change) if x.day_change is not None else 0.0)


def _heads(brief: Brief) -> dict[str, str]:
    """칸마다 맨 위 한 줄. **LLM이 아니라 배열이다.**

    사용자의 요구는 *"한 줄 요약(실제로는 2~3줄?)씩 칸마다 맨 위에 있고, 그
    다음에 밑에 디테일"* 이었다. 그런데 브리프는 [LLM을 안 쓴다](#) — 그래서
    **아래 숫자를 다시 읽어 문장 꼴로 세운다.** 새 사실이 생기지 않고, 틀릴
    여지가 없고, 값이 없으면 그 절이 통째로 빠진다.

    판단(「그래서 무엇을 봐야 하나」)은 여기 없다. 그건 미검증 레인이라
    배지를 달고 따로 나가야 한다 — D45가 정한 경계다.
    """
    out: dict[str, str] = {}

    # ── 매크로 ────────────────────────────────────────────────────
    macro: list[str] = []
    if line := index_line(brief.indices):
        macro.append(line)
    for m in brief.macro:
        piece = f"{m.get('label', '')} {m.get('display', '')}"
        change = m.get("change")
        if m.get("changed_at"):
            when = str(m["changed_at"])
            stamp = f"{when[:4]}-{when[4:6]}" if len(when) >= 6 else when
            direction = "인상" if (change or 0) > 0 else "인하"
            piece += f" ({stamp} {direction})"
        elif change is not None:
            piece += f" {change:+,.{m.get('digits', 2)}f}"
        macro.append(piece)
    if macro:
        out["macro"] = " · ".join(macro)

    # ── 수주 ──────────────────────────────────────────────────────
    # **가장 큰 것 하나를 이름으로 말한다.** 「수주 3건」은 세 건이 다 사소한
    # 경우와 하나가 회사를 바꾸는 경우를 구분 못 한다 — 그 차이를 말하는 것이
    # 「최근 매출 대비 %」다 (D87).
    deals = [(line, c) for line in brief.contract_lines for c in line.contracts]
    if deals:
        top_line, top = max(deals, key=lambda x: x[1].get("ratio_pct") or 0)
        head = f"{top_line.company} {top.get('headline', '')}".strip()
        out["contracts"] = head if len(deals) == 1 else f"{head} 외 {len(deals) - 1}건"

    # ── 섹터 ──────────────────────────────────────────────────────
    ranked = [x for x in brief.sectors if x.day_change is not None]
    if ranked:
        top = max(ranked, key=lambda x: x.day_change or 0.0)
        bottom = min(ranked, key=lambda x: x.day_change or 0.0)
        parts = [f"섹터 {len(brief.sectors)}개"]
        if top is bottom:
            parts.append(f"{top.sector} {top.day_change:+.2f}%")
        else:
            parts.append(f"{top.sector} {top.day_change:+.2f}%로 가장 강하고")
            parts.append(f"{bottom.sector} {bottom.day_change:+.2f}%로 가장 약합니다")
        excess = top.excess.get("1d")
        if excess is not None:
            parts.append(f"— {top.sector}는 시장 대비 {excess:+.2f}%p")
        out["sectors"] = " · ".join(parts[:2]) + (
            " " + " ".join(parts[2:]) if len(parts) > 2 else ""
        )

    # ── 종목 ──────────────────────────────────────────────────────
    stock: list[str] = []
    if brief.cover:
        stock.append(f"커버 {len(brief.cover)}종목")
    if brief.watch:
        stock.append(f"관심 {len(brief.watch)}종목")
    if brief.contract_count:
        stock.append(f"수주 {brief.contract_count}건")
    if brief.filing_count:
        stock.append(f"공시 {brief.filing_count}건")
    if brief.session in PRICELESS:
        # **「움직인 종목 없음」이라고 하지 않는다.** 안 움직인 게 아니라
        # 안 본 것이다 — 장중에는 오늘 시세가 없다.
        said = sum(x["count"] for x in brief.mentions)
        if said:
            stock.append(f"텔레그램 언급 {said}건")
        stock.append("주가는 마감 뒤")
    else:
        moved = [
            x for x in brief.cover if x.day_change is not None and abs(x.day_change) >= NOTABLE
        ]
        if moved:
            big = max(moved, key=lambda x: abs(x.day_change or 0.0))
            stock.append(
                f"{NOTABLE:.0f}% 이상 {len(moved)}건 (최대 {big.company} {big.day_change:+.2f}%)"
            )
        elif not brief.cover and brief.watch:
            # 커버가 비면 잴 대상 자체가 없다 — 「없음」은 잰 뒤에 할 말이다
            stock.append("커버 종목이 없어 등락을 보지 않았습니다")
        elif brief.cover and any(x.day_change is not None for x in brief.cover):
            stock.append(f"{NOTABLE:.0f}% 이상 움직인 커버 종목 없음")
        elif brief.cover:
            # **잰 값이 하나도 없으면 「없다」고 못 한다.** 안 움직인 게 아니라
            # 안 본 것이다. 위쪽 note는 「시세를 아직 받지 않았다」고 말하는데
            # 여기서 「움직인 종목 없음」이라고 하면 한 화면이 모순된다.
            #
            # `asof`가 아니라 **등락값의 유무**로 판단한다 — 기준일이 안 실린
            # 채로 등락만 있는 경우가 있다(테스트가 그 경우를 잡았다).
            stock.append("시세가 없어 등락을 못 냅니다")
    if stock:
        out["stocks"] = " · ".join(stock)
    return out


def _note(brief: Brief, profile: Profile) -> str:
    """맨 위 한 줄. **없으면 없다고 말하되, 못 읽은 것은 못 읽었다고 말한다.**

    둘을 섞으면 이 화면의 약속이 깨진다 — *"놓친 것이 없다는 확인"*이 목적인데,
    실패를 「없음」으로 내면 그 확인이 거짓이 된다.
    """
    if not profile.stocks:
        return "커버 종목을 먼저 넣으십시오 — 「내 커버리지」에서 정합니다."

    body = _note_body(brief)
    if not brief.unavailable:
        return body

    # **경고를 뒤에 붙이지 않고 문장을 바꾼다.** 「…없습니다 ⚠ 공시를 못
    # 읽었습니다」는 앞뒤가 모순이라, 앞 절이 「없다」로 끝나면 그것을 뺀다.
    # 조사를 맞춘다 — 「기사을(를)」은 아침에 읽기 나쁘다. 이 저장소에 이미
    # 교정기가 있다(`llm/josa.py`).
    missing = " · ".join(brief.unavailable)
    warn = f"⚠ {attach(missing, '을', '를')} 못 읽었습니다 — 이 화면이 전부가 아닙니다"
    return warn if body.endswith("없습니다.") else f"{body} · {warn}"


def _note_body(brief: Brief) -> str:
    """못 읽은 것을 빼고, 찾은 것만으로 만든 한 줄."""
    parts = []
    # **수주가 맨 앞이다** (D87). 미드스몰캡에서 이건 다른 공시 열 건보다 크다
    if brief.contract_count:
        parts.append(f"수주 {brief.contract_count}건")
    if brief.filing_count:
        parts.append(f"공시 {brief.filing_count}건")

    # **장중에는 주가로 말하지 않는다.** 오늘 값이 없기 때문이다.
    if brief.session in PRICELESS:
        said = sum(x["count"] for x in brief.mentions)
        if said:
            parts.append(f"텔레그램 언급 {said}건")
        if not parts:
            return "장중에 새로 나온 공시도, 도는 말도 없습니다."
        return " · ".join(parts) + " · 주가는 마감 뒤에 나옵니다"

    if not brief.asof:
        return "시세를 아직 받지 않아 등락을 낼 수 없습니다."
    if moved := brief.notable_count:
        parts.append(f"{NOTABLE:.0f}% 이상 움직인 종목 {moved}건")
    if not parts:
        # **커버가 없으면 「커버에 아무 일 없다」고 말하지 않는다** (D86).
        # 시드를 채택한 직후가 정확히 이 상태다 — 종목이 열둘인데 전부
        # 「관심」이라, 화면은 아무 일도 없었다고 단언하면서 **아무것도 안
        # 보고 있었다.** 위·공시·등락이 전부 커버만 센다(`filing_count`).
        if not brief.cover:
            if brief.watch:
                return "커버로 표시한 종목이 없어 공시·등락을 보지 않았습니다 — 관심 종목을 커버로 옮기십시오."
            return "커버 종목을 먼저 넣으십시오 — 「내 커버리지」에서 정합니다."
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
