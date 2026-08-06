"""분기 시계열 — **누적에서 단독 분기를 뽑는다**.

왜 필요한가
-----------
실제 증권사 리포트는 분기 실적 표를 싣는다(벤치마크 실측: 22행 × 8분기).
어닝 리뷰의 핵심 축인데 우리에겐 없었다.

**호출 4번으로 8분기가 나온다**
--------------------------------
DART는 누적만 준다 — 1분기·반기·3분기·사업보고서가 각각
`Q1 / Q1+Q2 / Q1+Q2+Q3 / 연간` 누적이다. 단독 분기는 빼서 만든다::

    1Q = Q1누적
    2Q = 반기누적 − Q1누적
    3Q = 3분기누적 − 반기누적
    4Q = 연간 − 3분기누적

그리고 **각 보고서가 전년 동기 누적을 함께 준다**(`frmtrm_add_amount`,
[D44](../../../docs/decisions.md#d44)). 그래서 한 해 4건만 받으면 **당기
4분기 + 전년 4분기 = 8분기**가 나온다. 8번 부를 필요가 없다.

무엇을 조심하는가
-----------------
* **한 칸이라도 비면 그 분기는 만들지 않는다.** 누적 뺄셈은 두 값이 다 있어야
  성립한다. 반기보고서가 아직 안 나온 해에 3분기를 만들면 거짓이 된다.
* **분기가 음수일 수 있다.** 4분기에 비용을 몰아 넣으면 실제로 그렇다.
  이상치로 보고 버리지 않는다 — 그게 사실이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from arc.data.base import ConsolidationType, DataProvider, PeriodType
from arc.finmodel.metrics import _LABELS, ACCOUNT_MAP, extract_metrics

# 누적 순서. 앞엣것을 빼서 단독 분기를 만든다.
_LADDER: tuple[tuple[str, PeriodType], ...] = (
    ("1Q", PeriodType.Q1),
    ("2Q", PeriodType.HALF),
    ("3Q", PeriodType.Q3),
    ("4Q", PeriodType.ANNUAL),
)

# 분기 표에 싣는 지표. 재무상태표는 분기 흐름이 아니라 잔액이라 뺀다.
QUARTER_METRICS: tuple[str, ...] = (
    "revenue",
    "operating_income",
    "net_income",
)


@dataclass
class QuarterPoint:
    """분기 한 칸."""

    label: str  # `1Q25`
    year: int
    quarter: int
    values: dict[str, int] = field(default_factory=dict)


@dataclass
class QuarterSeries:
    """분기 시계열. 오래된 것부터."""

    points: list[QuarterPoint] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        # 두 분기로는 추세가 안 보인다
        return len(self.points) >= 4

    def metric_row(self, metric: str) -> list[int | None]:
        return [p.values.get(metric) for p in self.points]


def _diff(current: int | None, previous: int | None) -> int | None:
    """누적 − 직전 누적. **한 칸이라도 비면 만들지 않는다.**"""
    if current is None or previous is None:
        return None
    return current - previous


def build_quarters(
    symbol: str,
    year: int,
    provider: DataProvider,
    *,
    consolidation: ConsolidationType = ConsolidationType.CONSOLIDATED,
    metrics: tuple[str, ...] = QUARTER_METRICS,
) -> QuarterSeries:
    """`year`와 그 전년의 분기 시계열. 호출은 **4번**이다.

    실패한 분기는 조용히 빠진다 — 반기보고서가 아직 안 나왔으면 3·4분기가
    없는 게 맞다.
    """
    series = QuarterSeries()

    # 누적값을 모은다. `cum[period]` = {지표: (당기누적, 전기누적)}
    cum: dict[str, dict[str, tuple[int | None, int | None]]] = {}
    for name, period in _LADDER:
        try:
            stmt = provider.get_financials(symbol, year, period, consolidation)
        except Exception as exc:  # noqa: BLE001 — 아직 안 나온 보고서가 있다
            series.problems.append(f"{year} {name}: {type(exc).__name__}")
            continue
        ms = extract_metrics(stmt)
        cum[name] = {
            m: (ms.values[m].current, ms.values[m].prior) for m in metrics if m in ms.values
        }

    # 누적 → 단독 분기. 당기와 전기를 같은 방식으로 만든다.
    for offset, target_year in ((0, year), (1, year - 1)):
        points: list[QuarterPoint] = []
        for i, (name, _) in enumerate(_LADDER):
            if name not in cum:
                continue
            prior_name = _LADDER[i - 1][0] if i else None
            point = QuarterPoint(
                label=f"{i + 1}Q{target_year % 100:02d}", year=target_year, quarter=i + 1
            )
            for metric in metrics:
                here = cum[name].get(metric)
                if here is None:
                    continue
                value = here[offset]
                if prior_name is not None:
                    before = cum.get(prior_name, {}).get(metric)
                    value = _diff(value, before[offset] if before else None)
                if value is not None:
                    point.values[metric] = int(value)
            if point.values:
                points.append(point)
        series.points = points + series.points  # 전년이 앞에 온다
    return series


def build_quarter_entries(series: QuarterSeries, prov) -> list:
    """분기 시계열 → NumberEntry. 키는 `{metric}_{1q25}`.

    **레지스트리를 거쳐야 본문에 쓸 수 있다** (불변식 1).
    """
    from arc.finmodel.metrics import fmt_krw
    from arc.llm.number_registry import NumberEntry

    out = []
    for p in series.points:
        for metric, value in p.values.items():
            display = fmt_krw(value)
            if display is None:
                continue
            out.append(
                NumberEntry(
                    key=f"{metric}_{p.label.lower()}",
                    value=value,
                    unit="원",
                    display=display,
                    provenance=prov,
                    label=f"{_LABELS.get(metric, metric)} ({p.label})",
                    formula="누적 − 직전 누적" if p.quarter > 1 else None,
                )
            )
    return out


# 계정 매핑이 바뀌면 여기도 따라와야 한다는 표시
assert all(m in ACCOUNT_MAP for m in QUARTER_METRICS)
