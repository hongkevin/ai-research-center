"""주가가 어떻게 움직였나 — 기간별.

왜 필요한가
-----------
지금 이 제품은 **분기에 한 번 찍은 사진**만 보여준다. 그런데 RA의 하루는
그렇게 안 돈다 — 아침에 「어제 뭐가 빠졌나」를 보고, 클라이언트가 「이거 왜
올랐어요」를 묻는다. 커버 종목 옆에 **오늘·1주·1개월·분기·반기·1년**이
없으면 그 대화에 못 들어간다.

**새로 받지 않는다.** `.arc-store/prices`에 2,985종목 × 268거래일이 이미
있고(D67의 금융위 API로 받은 재배포 가능한 것), 여기 필요한 건 나눗셈뿐이다.

거래일로 센다
-------------
「1개월」을 달력으로 세면 공휴일·주말에 따라 구간이 흔들려서 종목마다 다른
날을 비교하게 된다. **거래일 오프셋**으로 고정하고, 실제로 쓴 날짜를 함께
낸다 — 「1개월 +8.2%」만 있으면 언제부터인지 아무도 모른다.
"""

from __future__ import annotations

from dataclasses import dataclass

# 이름 → 거래일 수. 1년은 252가 관례지만 우리 창이 268이라 250으로 둔다.
HORIZONS: tuple[tuple[str, str, int], ...] = (
    ("1d", "1일", 1),
    ("2d", "2일", 2),
    ("1w", "1주", 5),
    ("1m", "1개월", 21),
    ("3m", "3개월", 63),
    ("6m", "6개월", 126),
    ("1y", "1년", 250),
)


@dataclass
class Move:
    """한 구간의 등락. **쓴 날짜를 함께 낸다.**"""

    key: str
    label: str
    change_pct: float | None = None
    from_date: str = ""
    to_date: str = ""
    # 요청한 거래일 수만큼 자료가 없으면 있는 만큼으로 낸다. 그때 몇 일치인지.
    days: int = 0
    # 요청보다 짧게 잡혔는가. 신규 상장이면 「1년」이 실제로는 3개월이다.
    partial: bool = False


@dataclass
class Moves:
    symbol: str
    company: str = ""
    last_close: float | None = None
    last_date: str = ""
    items: list[Move] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.items is None:
            self.items = []

    def get(self, key: str) -> Move | None:
        return next((m for m in self.items if m.key == key), None)


def moves_for(
    series: dict[str, float],
    *,
    symbol: str = "",
    company: str = "",
    horizons: tuple[tuple[str, str, int], ...] = HORIZONS,
) -> Moves:
    """일별 종가 → 기간별 등락률.

    **없는 것은 `None`이지 0.0이 아니다.** 0%로 채우면 「안 움직였다」와
    「모른다」가 같은 값이 되고, 신규 상장 종목의 1년 수익률이 0%로 보인다.
    """
    days = sorted(d for d, v in series.items() if v and v > 0)
    out = Moves(symbol=symbol, company=company)
    if not days:
        out.items = [Move(key=k, label=label) for k, label, _ in horizons]
        return out

    last = days[-1]
    out.last_close = series[last]
    out.last_date = last

    for key, label, back in horizons:
        # 마지막 날에서 `back` 거래일 앞. 자료가 모자라면 가장 오래된 날로.
        idx = len(days) - 1 - back
        partial = idx < 0
        base_idx = max(idx, 0)
        base_day = days[base_idx]
        if base_day == last:
            # 거래일이 하루뿐이면 비교할 것이 없다.
            out.items.append(Move(key=key, label=label))
            continue
        before = series[base_day]
        out.items.append(
            Move(
                key=key,
                label=label,
                change_pct=(series[last] / before - 1.0) * 100.0,
                from_date=base_day,
                to_date=last,
                days=len(days) - 1 - base_idx,
                partial=partial,
            )
        )
    return out


def moves_for_symbols(
    prices: dict[str, dict[str, float]],
    symbols: list[str],
    *,
    names: dict[str, str] | None = None,
) -> list[Moves]:
    """여러 종목을 한 번에. **시세가 없는 종목도 자리를 남긴다** — 빼 버리면
    화면에서 종목이 사라져 「왜 안 나오지」가 된다."""
    names = names or {}
    out: list[Moves] = []
    for symbol in symbols:
        series = prices.get(symbol)
        if series:
            out.append(moves_for(series, symbol=symbol, company=names.get(symbol, "")))
        else:
            out.append(Moves(symbol=symbol, company=names.get(symbol, "")))
    return out


def fmt(pct: float | None) -> str:
    """화면·표에 그대로 쓰는 문자열. **부호를 붙인다** — 등락은 방향이 먼저다."""
    if pct is None:
        return "—"
    return f"{pct:+.1f}%"
