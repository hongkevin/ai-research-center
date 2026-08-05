"""추정 레이어 — 가정에서 계산되는 FY+1 추정 + revision 추적.

왜 이게 중요한가
----------------
`what-makes-a-great-research-report.md` §1: FnGuide 베스트 애널리스트 배점의
**40점이 실적 추정 정확도**다. §4: 하향 조정이 상향보다 72일 늦는 것이
신뢰가 무너지는 지점이다. 즉 추정은 리포트의 부속물이 아니라 **평가 대상
그 자체**이고, 초기 추정보다 **수정 이력**이 더 중요하다.

이 모듈의 원칙
--------------
지금까지 이 저장소의 원칙은 "확인된 것만 쓰고 추정하지 않는다"였다. 추정
레이어는 그 원칙의 예외가 아니라 **명시적 적용**이다:

  * 모든 추정치는 **가정의 함수**다. 가정 없이 나오는 숫자는 없다.
  * 가정은 전부 표시된다. 어디서 나왔는지(`basis`)까지 함께.
  * 기본 가정은 **과거 실적의 기계적 연장**이고, 이건 예측이 아니라
    출발점(baseline)이다. 그렇게 라벨링하고 경고한다.
  * 사람이 가정을 덮어쓰면 `is_override`로 구분된다.

기계적 연장을 예측처럼 쓰면 안 되는 이유는 실측에서 바로 드러난다 —
파마리서치 FY2025 매출 성장률은 53.2%다. 이걸 그대로 연장하면 FY2026 매출이
8,215억원이 된다. 근거 없는 숫자다. 그래서 성장률 변동이 크면 경고를 단다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from arc.data.base import Provenance
from arc.finmodel.metrics import MetricSet, fmt_krw, fmt_pct, margin, yoy
from arc.llm.number_registry import NumberEntry

# 성장률이 이보다 크게 흔들리면 기계적 연장이 의미를 잃는다 (pp)
GROWTH_VOLATILITY_LIMIT = 20.0
# 이 범위를 벗어나는 성장률은 그대로 연장하지 않는다 (%)
GROWTH_SANITY_RANGE = (-50.0, 100.0)


@dataclass(frozen=True)
class Assumption:
    """추정 1건을 떠받치는 가정. **어디서 나왔는지까지 남긴다.**"""

    key: str
    label: str
    value: float
    unit: str
    basis: str  # 이 값의 근거
    is_override: bool = False  # 사람이 덮어썼는가

    def describe(self) -> str:
        tag = " (사용자 입력)" if self.is_override else ""
        return f"{self.label}: {self.value:+.1f}{self.unit} — {self.basis}{tag}"


@dataclass
class YearProjection:
    """추정 연도 1개. 값은 전부 그 해의 `assumptions`에서 계산된다."""

    fiscal_year: int
    assumptions: list[Assumption] = field(default_factory=list)
    values: dict[str, int] = field(default_factory=dict)


@dataclass
class EstimateSet:
    """추정. 기본은 FY+1 한 해이고, 사람이 연차를 늘릴 수 있다.

    **기계는 한 해만 세운다.** D34 실측에서 1년차 영업이익 오차가 이미 중앙값
    55.9%였다 — 그 위에 2년차를 기계가 얹으면 그럴듯해 보이는 노이즈가 는다.

    반면 **사람이 연차별 가정을 넣는 것은 다르다.** 55.9%는 우리 기준선의
    성적이지 RA의 가정의 성적이 아니다. 그때 기계는 예측하지 않고 산술만
    한다 — 그게 D24의 계산/판단 경계다.

    `fiscal_year`·`assumptions`·`values`는 **첫 해**를 가리킨다. 기존 호출부가
    그대로 동작하도록 남겨둔 별칭이다.
    """

    fiscal_year: int  # 추정 연도 (첫 해)
    base_year: int  # 기준 실적 연도
    assumptions: list[Assumption] = field(default_factory=list)
    values: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    method: str = "과거 실적의 기계적 연장"
    years: list[YearProjection] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        """매출 추정이 없으면 나머지도 성립하지 않는다."""
        return "revenue" in self.values

    def assumption(self, key: str) -> Assumption | None:
        return next((a for a in self.assumptions if a.key == key), None)


def _growth_history(ms: MetricSet, key: str) -> list[float]:
    """당기·전기 성장률. 전전기가 있으면 두 개, 없으면 하나."""
    cur, prior, prior2 = ms.get(key), ms.get_prior(key), ms.get_prior2(key)
    out = []
    g1 = yoy(cur, prior)
    if g1 is not None:
        out.append(g1)
    g2 = yoy(prior, prior2)
    if g2 is not None:
        out.append(g2)
    return out


def build_baseline_assumptions(ms: MetricSet) -> list[Assumption]:
    """과거 실적에서 기본 가정을 뽑는다. **예측이 아니라 출발점이다.**"""
    out: list[Assumption] = []

    history = _growth_history(ms, "revenue")
    if history:
        if len(history) >= 2:
            g = sum(history) / len(history)
            basis = "최근 2개년 매출 증가율 평균"
        else:
            g = history[0]
            basis = "직전 연도 매출 증가율"
        out.append(Assumption("revenue_growth", "매출 성장률", g, "%", basis))

    om = margin(ms.get("operating_income"), ms.get("revenue"))
    if om is not None:
        out.append(
            Assumption("operating_margin", "영업이익률", om, "%", f"{ms.fiscal_year}년 실적 유지")
        )

    nm = margin(ms.get("net_income"), ms.get("revenue"))
    if nm is not None:
        out.append(Assumption("net_margin", "순이익률", nm, "%", f"{ms.fiscal_year}년 실적 유지"))

    return out


def _project(base_revenue: int, assumptions: list[Assumption]) -> dict[str, int]:
    """한 해치 산술. 매출에 성장률을 곱하고, 거기에 마진을 곱한다."""
    by_key = {a.key: a for a in assumptions}
    growth = by_key.get("revenue_growth")
    if growth is None:
        return {}
    out = {"revenue": round(base_revenue * (1 + growth.value / 100))}
    for metric, akey in (("operating_income", "operating_margin"), ("net_income", "net_margin")):
        a = by_key.get(akey)
        if a is not None:
            out[metric] = round(out["revenue"] * a.value / 100)
    return out


def apply_assumptions(
    ms: MetricSet,
    assumptions: list[Assumption],
    forward: list[list[Assumption]] | None = None,
) -> EstimateSet:
    """가정 → 추정치. **모든 값이 가정에서 나온다.**

    `forward`는 2년차 이후의 연차별 가정이다. 비우면 지금까지처럼 한 해만 낸다.
    """
    est = EstimateSet(fiscal_year=ms.fiscal_year + 1, base_year=ms.fiscal_year)
    est.assumptions = list(assumptions)

    by_key = {a.key: a for a in assumptions}
    revenue = ms.get("revenue")
    growth = by_key.get("revenue_growth")

    if revenue is None or growth is None:
        est.warnings.append("매출 또는 성장률 가정이 없어 추정을 산출하지 않았다.")
        return est

    lo, hi = GROWTH_SANITY_RANGE
    if not (lo <= growth.value <= hi) and not growth.is_override:
        est.warnings.append(
            f"매출 성장률 가정({growth.value:+.1f}%)이 기계적 연장으로 쓰기에 극단적이다. "
            "가정을 직접 지정하지 않으면 추정을 산출하지 않는다."
        )
        return est

    history = _growth_history(ms, "revenue")
    if len(history) >= 2 and abs(history[0] - history[1]) > GROWTH_VOLATILITY_LIMIT:
        est.warnings.append(
            "최근 매출 증가율의 진폭이 커 과거 추세를 그대로 연장하기 어렵다. "
            "이 추정은 참고용 기준선이다."
        )

    est.values = _project(revenue, assumptions)
    for metric, akey in (("operating_income", "operating_margin"), ("net_income", "net_margin")):
        if metric not in est.values:
            est.warnings.append(f"{akey} 가정이 없어 {metric} 추정을 비웠다.")
    est.years = [
        YearProjection(
            fiscal_year=est.fiscal_year, assumptions=list(assumptions), values=est.values
        )
    ]

    # 2년차 이후 — **사람이 넣은 가정으로만 간다.** 기계가 알아서 늘리지 않는다.
    for i, ay in enumerate(forward or [], start=2):
        prev = est.years[-1].values.get("revenue")
        if not prev:
            est.warnings.append(f"{est.fiscal_year + i - 1}년 매출이 없어 그 뒤를 잇지 못했다.")
            break
        est.years.append(
            YearProjection(
                fiscal_year=est.fiscal_year + i - 1,
                assumptions=list(ay),
                values=_project(prev, ay),
            )
        )

    if any(a.is_override for a in assumptions) or forward:
        est.method = "사용자 지정 가정"
    return est


def _merge(base: list[Assumption], overrides: dict[str, float]) -> list[Assumption]:
    known = {a.key for a in base}
    out = [
        Assumption(a.key, a.label, overrides[a.key], a.unit, "사용자 지정", True)
        if a.key in overrides
        else a
        for a in base
    ]
    out += [
        Assumption(k, k, v, "%", "사용자 지정", True)
        for k, v in overrides.items()
        if k not in known
    ]
    return out


def build_estimates(
    ms: MetricSet,
    overrides: dict[str, float] | None = None,
    forward: list[dict[str, float]] | None = None,
) -> EstimateSet:
    """기본 가정 + 사용자 덮어쓰기 → 추정. 덮어쓴 가정은 `is_override`로 남는다.

    `forward`는 2년차 이후의 연차별 덮어쓰기다. 지정하지 않은 항목은 **직전
    해의 가정을 그대로 이어받는다** — 마진을 바꾸지 않겠다는 것도 판단이다.
    """
    assumptions = build_baseline_assumptions(ms)
    if overrides:
        merged: list[Assumption] = []
        for a in assumptions:
            if a.key in overrides:
                merged.append(
                    Assumption(a.key, a.label, overrides[a.key], a.unit, "사용자 지정", True)
                )
            else:
                merged.append(a)
        known = {a.key for a in assumptions}
        for key, value in overrides.items():
            if key not in known:
                merged.append(Assumption(key, key, value, "%", "사용자 지정", True))
        assumptions = merged

    chain: list[list[Assumption]] = []
    prev = assumptions
    for ov in forward or []:
        nxt = _merge(prev, ov or {})
        chain.append(nxt)
        prev = nxt
    return apply_assumptions(ms, assumptions, chain)


# ── revision 추적 ────────────────────────────────────────────────────
@dataclass(frozen=True)
class Revision:
    """직전 추정 대비 변화 1건."""

    metric: str
    label: str
    previous: int
    current: int

    @property
    def change_pct(self) -> float:
        if self.previous == 0:
            return 0.0
        return (self.current - self.previous) / abs(self.previous) * 100.0

    @property
    def direction(self) -> str:
        """상향/하향/유지.

        `what-makes-a-great-research-report.md` §4: 하향 조정이 늦는 것이
        신뢰가 무너지는 지점이다. 방향을 명시적으로 기록해야 그 지연을 잴 수 있다.
        """
        if abs(self.change_pct) < 0.5:
            return "유지"
        return "상향" if self.current > self.previous else "하향"


_REV_LABELS = {
    "revenue": "매출액",
    "operating_income": "영업이익",
    "net_income": "당기순이익",
}


def compare_estimates(previous: EstimateSet | None, current: EstimateSet) -> list[Revision]:
    """직전 추정 대비 변화. 직전이 없으면 빈 목록 — 없는 걸 만들지 않는다."""
    if previous is None or previous.fiscal_year != current.fiscal_year:
        return []
    out: list[Revision] = []
    for metric, label in _REV_LABELS.items():
        prev, cur = previous.values.get(metric), current.values.get(metric)
        if prev is None or cur is None:
            continue
        out.append(Revision(metric=metric, label=label, previous=prev, current=cur))
    return out


# ── 저장 (point-in-time) ─────────────────────────────────────────────
ESTIMATE_DATASET = "estimates"


def to_rows(est: EstimateSet, symbol: str, published_at: dt.date) -> list[dict]:
    """스냅샷 저장용 평탄화. 한 행 = 지표 1개."""
    return [
        {
            "symbol": symbol,
            "fiscal_year": est.fiscal_year,
            "base_year": est.base_year,
            "metric": metric,
            "value": value,
            "method": est.method,
            "published_at": published_at.isoformat(),
        }
        for metric, value in est.values.items()
    ]


def from_rows(rows: list[dict], symbol: str, fiscal_year: int) -> EstimateSet | None:
    """스냅샷 → EstimateSet (값만 복원 — revision 비교에 필요한 건 값이다)."""
    picked = [r for r in rows if r.get("symbol") == symbol and r.get("fiscal_year") == fiscal_year]
    if not picked:
        return None
    est = EstimateSet(
        fiscal_year=fiscal_year,
        base_year=int(picked[0].get("base_year") or fiscal_year - 1),
        method=str(picked[0].get("method") or ""),
    )
    for r in picked:
        value = r.get("value")
        if value is not None:
            est.values[str(r["metric"])] = int(value)
    return est


# ── Number Registry 항목 ─────────────────────────────────────────────
def build_estimate_entries(
    est: EstimateSet, revisions: list[Revision], prov: Provenance
) -> list[NumberEntry]:
    """추정치 → NumberEntry. 키 접미사는 `e`(estimate)로 실적(`a`)과 구분한다.

    연차가 늘어나면 그만큼 낸다 — 키가 연도를 달고 있어(`revenue_2027e`) 해가
    늘어도 충돌하지 않는다. **2년차부터는 산식이 기준 실적이 아니라 직전
    추정을 가리킨다** — 실제로 그 위에 쌓았기 때문이고, 그 사실이 출처에
    드러나야 검토자가 무엇에 기대고 있는지 안다.
    """
    out: list[NumberEntry] = []

    def add(key, value, unit, display, label, formula=None, inputs=None):
        if value is None or display is None:
            return
        out.append(
            NumberEntry(
                key=key,
                value=value,
                unit=unit,
                display=display,
                provenance=prov,
                label=label,
                formula=formula,
                inputs=inputs or [],
            )
        )

    projections = est.years or [
        YearProjection(fiscal_year=est.fiscal_year, assumptions=est.assumptions, values=est.values)
    ]
    for idx, yp in enumerate(projections):
        y = yp.fiscal_year
        base_year = est.base_year if idx == 0 else y - 1
        base_kind, base_suffix = ("실적", "a") if idx == 0 else ("추정", "e")
        for metric, label in _REV_LABELS.items():
            v = yp.values.get(metric)
            add(
                f"{metric}_{y}e",
                v,
                "원",
                fmt_krw(v),
                f"{label} ({y}E)",
                formula=f"{base_year}년 {base_kind} × 가정",
                inputs=[f"{metric}_{base_year}{base_suffix}"],
            )
        for a in yp.assumptions:
            add(
                f"assume_{a.key}_{y}e",
                a.value,
                a.unit,
                f"{a.value:+.1f}{a.unit}" if a.unit == "%" else f"{a.value}{a.unit}",
                f"가정 · {a.label} ({y}E)",
            )

    # revision은 **첫 해에만** 붙는다 — 직전 발간과 비교하는 축이 그것이다.
    y1 = est.fiscal_year
    for r in revisions:
        add(
            f"{r.metric}_prev_{y1}e",
            r.previous,
            "원",
            fmt_krw(r.previous),
            f"{r.label} 직전 추정 ({y1}E)",
        )
        add(
            f"{r.metric}_revision_{y1}e",
            r.change_pct,
            "%",
            fmt_pct(r.change_pct),
            f"{r.label} 추정 변화 ({y1}E)",
            formula="(현재 추정 - 직전 추정) / |직전 추정|",
            inputs=[f"{r.metric}_{y1}e"],
        )

    return out


def build_estimate_observations(est: EstimateSet, revisions: list[Revision]) -> list[str]:
    """추정 논지. **크기를 쓰지 않는다** (LLM이 리터럴로 베낀다)."""
    obs: list[str] = []
    if not est.usable:
        return obs

    obs.append(
        f"{est.fiscal_year}년 추정은 {est.base_year}년 실적에 가정을 적용해 계산한 "
        f"기준선이다({est.method}). 전망이 아니라 출발점이므로 단정적으로 쓰지 않는다."
    )
    obs.extend(est.warnings)

    for r in revisions:
        if r.direction == "유지":
            continue
        obs.append(
            f"{r.label} 추정을 직전 보고서 대비 {r.direction}했다. "
            "조정 방향과 이유를 밝히는 것이 추정치 자체보다 중요하다."
        )
    return obs
