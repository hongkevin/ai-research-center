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
class EstimateSet:
    """FY+1 추정. 값은 전부 `assumptions`에서 계산된다."""

    fiscal_year: int  # 추정 연도
    base_year: int  # 기준 실적 연도
    assumptions: list[Assumption] = field(default_factory=list)
    values: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    method: str = "과거 실적의 기계적 연장"

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


def apply_assumptions(ms: MetricSet, assumptions: list[Assumption]) -> EstimateSet:
    """가정 → 추정치. **모든 값이 가정에서 나온다.**"""
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

    est.values["revenue"] = round(revenue * (1 + growth.value / 100))

    for metric, akey in (("operating_income", "operating_margin"), ("net_income", "net_margin")):
        a = by_key.get(akey)
        if a is None:
            est.warnings.append(f"{akey} 가정이 없어 {metric} 추정을 비웠다.")
            continue
        est.values[metric] = round(est.values["revenue"] * a.value / 100)

    if any(a.is_override for a in assumptions):
        est.method = "사용자 지정 가정"
    return est


def build_estimates(ms: MetricSet, overrides: dict[str, float] | None = None) -> EstimateSet:
    """기본 가정 + 사용자 덮어쓰기 → 추정. 덮어쓴 가정은 `is_override`로 남는다."""
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
    return apply_assumptions(ms, assumptions)


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
    """추정치 → NumberEntry. 키 접미사는 `e`(estimate)로 실적(`a`)과 구분한다."""
    y = est.fiscal_year
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

    for metric, label in _REV_LABELS.items():
        v = est.values.get(metric)
        add(
            f"{metric}_{y}e",
            v,
            "원",
            fmt_krw(v),
            f"{label} ({y}E)",
            formula=f"{est.base_year}년 실적 × 가정",
            inputs=[f"{metric}_{est.base_year}a"],
        )

    for a in est.assumptions:
        add(
            f"assume_{a.key}_{y}e",
            a.value,
            a.unit,
            f"{a.value:+.1f}{a.unit}" if a.unit == "%" else f"{a.value}{a.unit}",
            f"가정 · {a.label} ({y}E)",
        )

    for r in revisions:
        add(
            f"{r.metric}_prev_{y}e",
            r.previous,
            "원",
            fmt_krw(r.previous),
            f"{r.label} 직전 추정 ({y}E)",
        )
        add(
            f"{r.metric}_revision_{y}e",
            r.change_pct,
            "%",
            fmt_pct(r.change_pct),
            f"{r.label} 추정 변화 ({y}E)",
            formula="(현재 추정 - 직전 추정) / |직전 추정|",
            inputs=[f"{r.metric}_{y}e"],
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
