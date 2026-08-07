"""피어 후보 — **업종 분류를 쓰지 않는다.**

왜 KSIC를 버렸는가
------------------
실측했다. 방산 4종목의 KSIC(DART `induty_code`):

    한국항공우주 31311 · 현대로템 31910 · 한화에어로 31321 · LIG넥스원 25200

2자리에서 3/4, 3자리에서 2/4. **어느 자릿수에서도 한 그룹이 안 된다.**
이유가 구조적이다 — KSIC는 **제품 형태**로 나누는데 「방산」은 **전방시장**이다.
KAI=항공기, 현대로템=철도차량, LIG넥스원=무기·총포탄. 네 회사가 같은 산업인
이유(정부 방위력개선비)는 KSIC 어디에도 없다.

무엇을 대신 쓰는가
------------------
인터뷰에 답이 있었다 — *"우리 커버리지랑 **같이 움직이는 종목** 골라줘"*.
분류 문제가 아니라 **상관** 문제다. 씨앗 종목을 주면 일간 로그수익률 상관으로
후보를 낸다. 실측(KAI + 한화에어로):

    LIG넥스원 .647 · 한화시스템 .640 · 현대로템 .631 · 풍산 .580
    코츠테크놀로지 .552 · 엠앤씨솔루션 .529

한화시스템·풍산·코츠테크놀로지는 **KSIC로는 절대 한 그룹이 안 된다**
(전자부품 26 · 1차금속 24 · 기계 29). RA가 이름조차 대지 않은 피어를 찾는다.

**한계를 코드가 말하게 한다**
------------------------------
1. **상관은 「산업」이 아니라 지금 주가를 움직이는 테마를 찾는다.** 현대건설
   씨앗을 주면 한전기술·두산에너빌리티가 나온다 — 원전 테마다. 그래서 이
   모듈은 **섹터 이름을 붙이지 않고 상관계수를 함께 낸다.** 이름을 붙이는
   순간 「같은 산업」이라는 거짓 약속이 된다.
2. **시간에 따라 흔들린다.** 같은 씨앗의 기간 간 겹침이 4/15까지 떨어진 예가
   있다. 그래서 **후보일 뿐 그룹이 아니다** — 사람이 고른 것만 카드에 박힌다
   (`store.cards.Card.members`). 매번 재계산하면 표가 조용히 바뀌고 D46
   「직전 대비 변화」가 무의미해진다.
3. **시장의 절반에서 무력하다.** 무작위 8종목의 평균 상관이 **+0.320**인데,
   실측한 섹터 내부 상관이 이렇다:

       2차전지 +0.734 · 자동차 +0.667 · 반도체 +0.604 · 조선 +0.593
       엔터·광고 +0.291 · 미디어 +0.269 · **통신 +0.252**

   통신주가 무작위보다 낮다. 처음엔 이걸 "상관은 내수·디펜시브 섹터에서
   무력하다"로 읽었는데 **틀렸다.** 원인은 섹터가 아니라 **시장 요인**이었다.

3. **그래서 시장 요인을 걷어낸다.** KOSPI에 대한 베타를 회귀로 빼고 남은
   **잔차**끼리 상관을 본다. 같은 그룹을 두 방식으로 재면:

       그룹        원시    시장제거
       무작위      0.318   **0.102**   ← 상관의 거의 전부가 시장 요인이었다
       통신        0.364   **0.323**   ← 무작위의 3.2배. 진짜 섹터가 맞다
       방산        0.636   0.563
       2차전지     0.765   0.658
       은행        0.782   0.729

   **통신은 상관으로 못 찾는 섹터가 아니었다.** 원시 상관에서 안 보였을 뿐이다.

4. **개수를 채우지 않는다.** 통신에서 top-6을 뽑으면 오뚜기·하이트진로가
   섞인다 — 한국 통신주가 셋뿐이라 채울 것이 없기 때문이다. 상관 하한을 두어
   **못 찾으면 적게 내놓는다.** 여덟 칸을 채우면 RA가 그것을 섹터로 읽는다.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

# 겹치는 거래일이 이보다 적으면 상관을 내지 않는다. 신규 상장·거래정지가
# 섞이면 20일짜리 상관이 0.9로 나오고 그게 표 맨 위에 앉는다.
MIN_OVERLAP = 60

# 기본 관측 창. 250거래일 ≈ 1년.
WINDOW = 250

# 시장 요인 계열. 코퍼스에 `KOSPI.json`으로 들어 있다.
MARKET = "KOSPI"

# **무작위 8종목의 상호 상관.** 잔차 기준 실측값이다(원시로는 0.318).
RANDOM_BASELINE = 0.102

# 기준선보다 이만큼은 높아야 "찾았다"고 말한다.
MEANINGFUL_MARGIN = 0.10

# 후보로 내놓을 상관 하한(잔차 기준). 방산 구성원이 0.44~0.59,
# 통신의 실제 동종(LG유플러스)이 0.31, 잡음이 0.26 부근이다.
MIN_CORRELATION = 0.30


@dataclass
class Candidate:
    symbol: str
    correlation: float
    overlap: int  # 상관을 낸 거래일 수 — 「몇 일치로 본 것인가」
    company: str = ""


@dataclass
class Suggestion:
    """후보 목록 + **이 목록을 믿어도 되는가.**"""

    candidates: list[Candidate]
    meaningful: bool = False  # 후보끼리도 같이 움직이는가
    cohesion: float = 0.0  # 그룹 **내부** 상호 상관 평균 — 무작위는 0.320
    top_correlation: float = 0.0  # 씨앗과의 최고 상관. 선택 편향이 있어 판정에 안 쓴다
    note: str = ""


def load_prices(directory: str | Path) -> dict[str, dict[str, float]]:
    """`{symbol}.json` → `{"YYYYMMDD": 종가}` 묶음을 읽는다."""
    out: dict[str, dict[str, float]] = {}
    for path in sorted(Path(directory).glob("*.json")):
        try:
            series = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(series, dict) and series:
            out[path.stem] = {str(k): float(v) for k, v in series.items()}
    return out


def _returns(series: dict[str, float], dates: list[str]) -> dict[str, float]:
    """일간 로그수익률. **가격이 아니라 수익률로 봐야** 규모가 안 섞인다."""
    out: dict[str, float] = {}
    prev_date = ""
    for d in dates:
        price = series.get(d)
        if price is None or price <= 0:
            prev_date = ""
            continue
        if prev_date:
            before = series[prev_date]
            if before > 0:
                out[d] = math.log(price / before)
        prev_date = d
    return out


def _pearson(a: dict[str, float], b: dict[str, float]) -> tuple[float | None, int]:
    """상관과 겹친 거래일 수. **못 낸 경우는 `None`이지 0.0이 아니다.**

    0.0으로 돌려주면 「상관이 없다」와 「상관을 낼 수 없다」가 같은 값이 되고,
    거래정지 종목이 「무관한 종목」인 척 목록에 앉는다.
    """
    common = sorted(a.keys() & b.keys())
    n = len(common)
    if n < MIN_OVERLAP:
        return (None, n)
    xs = [a[d] for d in common]
    ys = [b[d] for d in common]
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        # 거래정지로 종가가 붙박이면 분산이 0이다. 0으로 나누지 않는다.
        return (None, n)
    return (cov / math.sqrt(vx * vy), n)


def _beta_residual(
    rets: dict[str, float], market: dict[str, float]
) -> dict[str, float] | None:
    """시장 요인을 걷어낸 잔차 수익률. `r - β·r_시장`.

    **이걸 안 하면 상관의 대부분이 시장이다** — 무작위 8종목의 상호 상관이
    원시로 0.318인데 잔차로는 0.102다. 즉 원시 상관 0.3대는 「같이 움직인다」가
    아니라 「둘 다 한국 주식이다」라는 뜻이다.
    """
    common = sorted(rets.keys() & market.keys())
    if len(common) < MIN_OVERLAP:
        return None
    xs = [market[d] for d in common]
    ys = [rets[d] for d in common]
    n = len(common)
    mx = sum(xs) / n
    my = sum(ys) / n
    vx = sum((x - mx) ** 2 for x in xs)
    if vx <= 0:
        return None
    beta = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / vx
    return {d: rets[d] - beta * market[d] for d in common}


def suggest(
    seeds: list[str],
    prices: dict[str, dict[str, float]],
    *,
    window: int = WINDOW,
    top: int = 15,
    exclude: set[str] | None = None,
    market: str = MARKET,
    min_correlation: float = MIN_CORRELATION,
) -> list[Candidate]:
    """씨앗 종목과 **같이 움직이는** 종목을 상관 순으로.

    씨앗이 여럿이면 **씨앗별 상관의 평균**을 쓴다. 하나만 주는 것보다 훨씬
    또렷해진다 — 실측에서 씨앗 하나일 때 top15 중 방산이 8~9개였는데 둘로
    올리자 top8이 사실상 전부 방산이었다.

    `market` 계열이 있으면 **잔차 상관**을 쓴다. 없으면 원시 상관으로 떨어지되
    그때는 `min_correlation`이 의미를 잃으므로 하한을 적용하지 않는다 —
    두 공간의 숫자는 서로 견줄 수 없다.

    씨앗 자신과 `exclude`는 결과에서 뺀다. 상관을 못 낸 종목(겹치는 거래일
    부족)도 뺀다 — 0.0으로 목록 끝에 세우면 「상관 없음」과 「모름」이 섞인다.
    """
    seeds = [s for s in seeds if s in prices]
    if not seeds:
        return []

    # 관측 창은 **씨앗이 실제로 거래된 날**로 정한다. 전 종목 합집합으로
    # 잡으면 씨앗이 안 거래된 날이 창을 먹는다.
    seed_dates: set[str] = set()
    for s in seeds:
        seed_dates |= prices[s].keys()
    dates = sorted(seed_dates)[-(window + 1) :]

    market_returns = _returns(prices[market], dates) if market in prices else None
    adjusted = market_returns is not None

    def factor(symbol: str) -> dict[str, float] | None:
        rets = _returns(prices[symbol], dates)
        if market_returns is None:
            return rets
        return _beta_residual(rets, market_returns)

    seed_series: dict[str, dict[str, float]] = {}
    for s in seeds:
        series = factor(s)
        if series:
            seed_series[s] = series
    if not seed_series:
        return []

    skip = set(seeds) | (exclude or set()) | {market}
    floor = min_correlation if adjusted else None

    out: list[Candidate] = []
    for symbol in prices:
        if symbol in skip:
            continue
        series = factor(symbol)
        if not series:
            continue
        pairs = [_pearson(seed_series[s], series) for s in seed_series]
        # **씨앗 전부와 견줄 수 있어야** 평균이 뜻을 가진다. 하나라도 못 내면
        # 뺀다 — 남은 것만 평균하면 씨앗 하나짜리 상관이 둘짜리인 척한다.
        if any(c is None for c, _ in pairs):
            continue
        corr = sum(c for c, _ in pairs if c is not None) / len(pairs)
        # **개수를 채우지 않는다.** 한국 통신주는 셋뿐이라 top-6을 채우면
        # 오뚜기·하이트진로가 섞인다. 못 찾으면 적게 내놓는 게 맞다.
        if floor is not None and corr < floor:
            continue
        out.append(Candidate(symbol=symbol, correlation=corr, overlap=min(n for _, n in pairs)))

    out.sort(key=lambda c: c.correlation, reverse=True)
    return out[:top]


def suggest_group(
    seeds: list[str],
    prices: dict[str, dict[str, float]],
    *,
    window: int = WINDOW,
    top: int = 15,
    exclude: set[str] | None = None,
    market: str = MARKET,
) -> Suggestion:
    """`suggest()` + **이 목록을 믿어도 되는가**.

    상관은 요청한 개수를 채우려 든다. 그러면 RA가 채워진 목록을 섹터로 읽는다.
    **찾지 못했을 때 찾지 못했다고 말하는 것**이 이 함수의 존재 이유다.

    판정은 **씨앗과의 상관이 아니라 그룹 내부의 상호 상관**으로 한다. 처음엔
    top-1 상관을 기준선과 비교했는데 틀렸다 — 800종목 중 최댓값은 선택 편향으로
    늘 높아서 통신 씨앗도 0.558로 통과했고, 정작 그 목록에는 KOSPI 지수와
    삼성생명이 들어 있었다. 진짜 섹터는 후보끼리도 같이 움직인다.
    """
    candidates = suggest(seeds, prices, window=window, top=top, exclude=exclude, market=market)
    if not candidates:
        return Suggestion(
            candidates=[],
            note="같이 움직이는 종목을 찾지 못했습니다.",
        )

    group = [s for s in seeds if s in prices] + [c.symbol for c in candidates]
    cohesion = _cohesion(group, prices, window=window, market=market)
    meaningful = cohesion >= RANDOM_BASELINE + MEANINGFUL_MARGIN
    note = (
        ""
        if meaningful
        else (
            f"후보끼리 같이 움직이지 않습니다 — 내부 상관 {cohesion:.2f}, "
            f"무작위 수준 {RANDOM_BASELINE:.2f}. 이 목록을 섹터로 읽지 마십시오."
        )
    )
    return Suggestion(
        candidates=candidates,
        meaningful=meaningful,
        cohesion=cohesion,
        top_correlation=candidates[0].correlation,
        note=note,
    )


def _cohesion(
    symbols: list[str],
    prices: dict[str, dict[str, float]],
    *,
    window: int,
    market: str = MARKET,
) -> float:
    """종목 묶음의 **상호** 상관 평균.

    `suggest()`와 **같은 공간에서 재야** 기준선과 견줄 수 있다 — 잔차 상관과
    원시 상관은 서로 비교할 수 없는 숫자다(무작위 8종목: 원시 0.318 / 잔차 0.102).
    """
    known = [s for s in symbols if s in prices and s != market]
    if len(known) < 2:
        return 0.0
    dates: set[str] = set()
    for s in known:
        dates |= prices[s].keys()
    window_dates = sorted(dates)[-(window + 1) :]
    market_returns = _returns(prices[market], window_dates) if market in prices else None

    rets: dict[str, dict[str, float]] = {}
    for s in known:
        raw = _returns(prices[s], window_dates)
        series = raw if market_returns is None else _beta_residual(raw, market_returns)
        if series:
            rets[s] = series
    known = list(rets)

    total = 0.0
    pairs = 0
    for i, a in enumerate(known):
        for b in known[i + 1 :]:
            corr, _ = _pearson(rets[a], rets[b])
            if corr is None:
                continue
            total += corr
            pairs += 1
    return total / pairs if pairs else 0.0
