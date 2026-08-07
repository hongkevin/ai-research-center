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


@dataclass
class Candidate:
    symbol: str
    correlation: float
    overlap: int  # 상관을 낸 거래일 수 — 「몇 일치로 본 것인가」
    company: str = ""


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


def suggest(
    seeds: list[str],
    prices: dict[str, dict[str, float]],
    *,
    window: int = WINDOW,
    top: int = 15,
    exclude: set[str] | None = None,
) -> list[Candidate]:
    """씨앗 종목과 **같이 움직이는** 종목을 상관 순으로.

    씨앗이 여럿이면 **씨앗별 상관의 평균**을 쓴다. 하나만 주는 것보다 훨씬
    또렷해진다 — 실측에서 씨앗 하나일 때 top15 중 방산이 8~9개였는데 둘로
    올리자 top8이 사실상 전부 방산이었다.

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

    seed_returns = {s: _returns(prices[s], dates) for s in seeds}
    skip = set(seeds) | (exclude or set())

    out: list[Candidate] = []
    for symbol, series in prices.items():
        if symbol in skip:
            continue
        rets = _returns(series, dates)
        pairs = [_pearson(seed_returns[s], rets) for s in seeds]
        # **씨앗 전부와 견줄 수 있어야** 평균이 뜻을 가진다. 하나라도 못 내면
        # 뺀다 — 남은 것만 평균하면 씨앗 하나짜리 상관이 둘짜리인 척한다.
        if any(c is None for c, _ in pairs):
            continue
        corr = sum(c for c, _ in pairs if c is not None) / len(pairs)
        out.append(Candidate(symbol=symbol, correlation=corr, overlap=min(n for _, n in pairs)))

    out.sort(key=lambda c: c.correlation, reverse=True)
    return out[:top]
