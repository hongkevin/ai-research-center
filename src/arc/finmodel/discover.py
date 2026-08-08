"""발굴 — **「숨겨진 ○○ 수혜주」** (D87).

왜 이것이 이 사람의 상품인가
----------------------------
미드스몰캡 애널리스트의 공개 리포트 60여 편을 읽었더니 제목에 같은 꼴이 반복된다:

    숨겨진 HBM 수혜주 · 숨겨진 애플 OLED 확장의 수혜 기업 · 숨겨진 SOFC 수혜주
    숨겨진 ESS·로봇 수혜주 · 숨겨진 데이터센터 수혜주 · 숨겨진 반도체 소부장 저평가주

일이 이렇게 생겼다: **대형 테마가 뜨면**(HBM·데이터센터·SOFC·유리기판·방산·우주)
→ **그 밸류체인에서 아직 안 알려진 코스닥 종목을 찾아** → 탐방하고 → 리포트를 낸다.

그런데 이 제품은 **이미 아는 종목**만 다뤘다. 커버리지·브리프·피어그룹·리포트가
전부 「내가 넣은 종목」에서 시작한다. 그가 돈 버는 순간은 그 목록에 **없던 이름을
찾을 때**인데, 그 자리가 통째로 비어 있었다.

새 엔진을 안 만든다
-------------------
`peer_suggest`가 이미 전 종목을 훑어 **마켓베타를 제거한** 상관을 내고, 무작위
기준선(0.102, 실측)과 비교해 「이 묶음을 믿어도 되는가」까지 판정한다. 없던 것은
**크기 감각**뿐이었다 — 상관만 보면 대형주가 후보 맨 위에 앉는데, 「숨겨진」은
정의상 작고 안 알려진 종목이다. 그 필터를 `market_facts`가 이제 준다.

유니버스를 먼저 좁힌다
----------------------
상관을 다 계산한 뒤 거르면 `top=15`가 「대형주 13개를 버리고 남은 2개」가 된다.
**먼저 좁히고 그 안에서 상관을 낸다** — 그러면 15개가 전부 소형주다.

정직한 한계
-----------
**상관은 「같이 움직였다」이지 「밸류체인에 있다」가 아니다.** 같은 섹터 ETF에
담겼거나 같은 수급에 실렸어도 상관은 올라간다. 그래서:

* 그룹 내부 상관(cohesion)이 무작위 수준이면 **그렇게 말한다** (`suggest_group`)
* 후보마다 **사업 개요와 최근 수주**를 같이 낸다 — 밸류체인 여부는 그것을 보고
  사람이 판단한다. 우리가 「수혜주입니다」라고 말하지 않는다
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from arc.finmodel import market_facts
from arc.finmodel.peer_suggest import (
    RANDOM_BASELINE,
    Candidate,
    load_prices,
    suggest_group,
)

# 스몰캡의 기본 문턱. **고정값이 아니라 출발점이다** — 화면이 고칠 수 있다.
#
# 시총 5,000억은 그의 커버 종목 분포에서 잡았다(씨이랩 990억 · 오스테오닉
# 1,054억 · 코세스 3,524억). 거래대금 5억은 그 아래로 가면 **기관이 못 사는**
# 수준이라 리포트를 써도 소용이 없기 때문이다.
DEFAULT_MAX_CAP = 500_000_000_000.0
DEFAULT_MIN_TURNOVER = 500_000_000.0

# 좁힌 유니버스에서 몇 개를 볼까. 화면에 스무 개를 늘어놓으면 목록이 되고,
# 목록은 「이 중에 있겠지」로 읽힌다.
TOP = 12

# 상관을 낼 수 있는 최소 유니버스. 시장 요인 제거가 **그날 전 종목 중앙값**을
# 쓰는데(`_market_returns`), 좁힌 유니버스로 그걸 내면 「소형주 평균 대비」가
# 되어 시장이 아니다. 그래서 지수 계열을 반드시 남긴다.
MARKET_KEYS = ("KOSPI", "KOSDAQ")


@dataclass
class Found:
    """후보 하나. **판단은 안 붙인다** — 사람이 볼 재료만 모은다."""

    symbol: str
    company: str = ""
    correlation: float = 0.0
    overlap: int = 0
    cap: float | None = None
    cap_display: str = ""
    avg_turnover: float | None = None
    board: str = ""
    # 오늘 텔레그램에서 몇 번 나왔나. **0이 진짜 「숨겨진」 것이다**
    mentions: int = 0

    @property
    def unheard(self) -> bool:
        """아직 안 도는가. 이미 도는 종목은 발굴이 아니라 뒷북이다."""
        return self.mentions == 0


@dataclass
class Discovery:
    """발굴 결과 + **이 목록을 믿어도 되는가**."""

    seeds: list[str] = field(default_factory=list)
    found: list[Found] = field(default_factory=list)
    # 걸러진 뒤 상관을 낸 종목 수. 「2,987개 중에서」가 아니라 이 수가 모수다
    universe: int = 0
    meaningful: bool = False
    cohesion: float = 0.0
    # 무작위로 같은 크기의 묶음을 지었을 때의 내부 상관. **판정을 검산할 수
    # 있게 같이 낸다** — 「0.40」만 보면 높은지 모른다. 실측(2026-08): 전 종목
    # 무작위 13종목 중앙 0.134, 소형주로 좁혀도 0.143이라 크게 안 변한다
    baseline: float = 0.0
    note: str = ""

    @property
    def unheard(self) -> list[Found]:
        return [f for f in self.found if f.unheard]


def _narrow(
    prices: dict[str, dict[str, float]],
    base: str | Path,
    cond: market_facts.Screen,
    *,
    keep: set[str],
) -> dict[str, dict[str, float]]:
    """유니버스를 먼저 좁힌다. **지수와 씨앗은 남긴다.**

    지수를 빼면 시장 요인 제거가 「소형주 평균 대비」가 되어 초과수익의 뜻이
    바뀐다. 씨앗을 빼면 상관을 잴 기준이 사라진다.
    """
    candidates = [s for s in prices if s not in keep and s not in MARKET_KEYS]
    passed = set(market_facts.screen(base, candidates, cond))
    return {s: v for s, v in prices.items() if s in passed or s in keep or s in MARKET_KEYS}


def discover(
    seeds: list[str],
    *,
    base: str | Path,
    prices: dict[str, dict[str, float]] | None = None,
    exclude: set[str] | None = None,
    max_cap: float | None = DEFAULT_MAX_CAP,
    min_turnover: float | None = DEFAULT_MIN_TURNOVER,
    boards: tuple[str, ...] = (market_facts.KOSDAQ,),
    top: int = TOP,
    mentions: dict[str, int] | None = None,
    names: dict[str, str] | None = None,
) -> Discovery:
    """앵커 종목 → **아직 내 목록에 없는 소형주** 후보.

    `exclude`에 내 커버·관심을 넘긴다 — 이미 보는 종목이 후보로 나오면 그건
    발굴이 아니다.

    `mentions`(오늘 텔레그램 언급 수)를 주면 **이미 도는 것과 아직 안 도는
    것을 가른다.** 도는 종목을 「숨겨진」이라고 부르면 그 말이 뜻을 잃는다.
    """
    prices = prices if prices is not None else load_prices(Path(base) / "prices")
    seeds = [s for s in seeds if s in prices]
    out = Discovery(seeds=seeds)
    if not seeds:
        out.note = "씨앗 종목의 시세가 없습니다."
        return out

    exclude = set(exclude or ()) | set(seeds)
    cond = market_facts.Screen(max_cap=max_cap, min_turnover=min_turnover, boards=boards)
    narrowed = _narrow(prices, base, cond, keep=exclude)
    out.universe = len([s for s in narrowed if s not in exclude and s not in MARKET_KEYS])
    if out.universe == 0:
        out.note = (
            "조건에 맞는 종목이 없습니다 — 시장 데이터를 받았는지 확인하십시오"
            " (arc prices backfill)."
        )
        return out

    got = suggest_group(seeds, narrowed, top=top, exclude=exclude)
    out.meaningful, out.cohesion, out.note = got.meaningful, got.cohesion, got.note
    out.baseline = RANDOM_BASELINE
    mentions = mentions or {}
    out.found = [_row(c, base, mentions, names or {}) for c in got.candidates]
    return out


def _row(c: Candidate, base: str | Path, mentions: dict[str, int], names: dict[str, str]) -> Found:
    snap = market_facts.snapshot(base, c.symbol)
    return Found(
        symbol=c.symbol,
        # **이름이 없으면 목록이 안 읽힌다.** 종목코드 열두 줄은 아무것도
        # 말하지 않는다 — 「채비」·「알멕」이라고 써야 사람이 판단한다
        company=names.get(c.symbol) or c.company or c.symbol,
        correlation=c.correlation,
        overlap=c.overlap,
        cap=snap.cap,
        cap_display=market_facts.display_cap(snap.cap),
        avg_turnover=snap.avg_turnover,
        board=snap.board,
        mentions=mentions.get(c.symbol, 0),
    )
