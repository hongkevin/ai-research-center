"""추정 백테스트 — **우리 기준선이 실제로 얼마나 틀리는가.**

왜 재는가
---------
`what-makes-a-great-research-report.md` §1: FnGuide 베스트 애널리스트 배점의
**40점이 실적 추정 정확도**다. [D12](../../docs/decisions.md)는 이걸 최우선
차별화로 올렸고 [D24](../../docs/decisions.md)는 추정 레이어를 지었다. 그런데
정작 **우리 기준선의 성적은 모른다.** 선언과 실측 사이의 빈칸이다.

여기서 재는 것은 예측력이 아니라 **기준선의 성질**이다. 기계적 연장은 예측이
아니라 출발점이라고 D24가 못 박았으므로, 이 백테스트의 목적은 "얼마나 맞히나"가
아니라 **"어느 방향으로, 얼마나 벗어나는가"**다. 편향이 있으면 그것이 기준선을
읽는 법을 바꾼다.

시점 정합성이 전부다
--------------------
FY(Y+1)을 추정할 때 FY(Y+1)의 정보가 한 톨이라도 섞이면 백테스트는 거짓말이
된다. 이 모듈은 두 가지로 막는다:

1. 추정은 **FY(Y) 사업보고서만**으로 만든다. 그 보고서에 FY(Y)·FY(Y-1)·FY(Y-2)가
   비교표시로 들어 있어 기계적 연장에 필요한 건 다 있다. 뒤 연도 보고서를
   끌어다 쓰지 않는다.
2. 실적은 **FY(Y+1) 보고서의 당기 값**을 쓴다. 뒤 보고서의 비교표시를 쓰면
   재작성(restatement)이 반영돼 "그때 맞혔는가"가 아닌 다른 질문에 답하게 된다.

`evaluate`는 두 해가 실제로 이어지는지 확인하고, 아니면 **거부한다.**

산출 거부는 오차가 아니다
-------------------------
[D24](../../docs/decisions.md)는 성장률이 −50~100% 밖이거나 진폭이 크면
추정을 내지 않는다. 그 경우를 오차 0으로도, 무한대로도 세면 안 된다. 따로
세고 **커버리지로 보고한다** — 40%를 거부하는 기준선과 다 답하는 기준선은
같은 MAPE라도 쓸모가 다르다.

중앙값을 함께 낸다
------------------
예측 오차 분포는 꼬리가 두껍다. 평균만 내면 몇 건의 폭발이 전체를 지배해
"기준선이 쓸모없다"는 잘못된 결론에 이른다. MAPE와 **중앙값 APE**를 함께
낸다. 둘이 크게 벌어지면 그 사실 자체가 결과다.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass, field

from arc.finmodel.estimates import EstimateSet, build_estimates
from arc.finmodel.metrics import MetricSet

# 요약에 넣을 지표와 라벨. 추정 레이어가 내는 것과 같아야 한다.
BACKTEST_METRICS: tuple[tuple[str, str], ...] = (
    ("revenue", "매출액"),
    ("operating_income", "영업이익"),
    ("net_income", "당기순이익"),
)

# "이 정도면 맞혔다"의 경계. 둘 다 낸다 — 하나만 내면 임의로 보인다.
HIT_BANDS: tuple[float, ...] = (10.0, 20.0)

# 이보다 크게 빗나간 건은 "틀렸다"가 아니라 **기준선이 적용되지 않는 회사**다.
# 몇 %가 여기 해당하는지가 기준선의 실제 쓸모를 가른다.
BLOWUP_PCT = 100.0

# 이익 오차를 매출로 나눠 볼 지표 — 분모가 0 근처면 상대오차가 발산한다
_SCALED_METRICS = frozenset({"operating_income", "net_income"})


@dataclass(frozen=True)
class ForecastError:
    """추정 1건의 오차. 부호를 살린다 — **편향은 크기보다 중요하다.**"""

    symbol: str
    base_year: int  # 추정을 만든 기준 실적 연도
    target_year: int  # 추정 대상 (= base_year + 1)
    metric: str
    estimate: int
    actual: int
    actual_revenue: int | None = None  # 같은 해 매출 — 이익 오차의 안정된 분모

    @property
    def error_pct(self) -> float | None:
        """(추정 − 실적) ÷ |실적|. 양수면 과대추정.

        실적이 0이면 상대오차가 정의되지 않는다. 0으로 두면 "정확히 맞혔다"가
        되므로 **비운다.**
        """
        if self.actual == 0:
            return None
        return (self.estimate - self.actual) / abs(self.actual) * 100.0

    @property
    def error_of_revenue_pct(self) -> float | None:
        """(추정 − 실적) ÷ 매출액. **이익 오차는 이걸로 읽어야 한다.**

        영업이익 상대오차는 분모가 0 근처면 발산한다. 실측: 영업이익 5,700만원인
        회사에 −63억을 추정해 오차가 −11,204%로 잡혔다. 숫자가 크다는 것 말고는
        읽을 게 없다.

        매출로 나누면 "매출의 몇 %만큼 이익을 빗맞혔나"가 되어 회사 간 비교가
        되고 크기도 해석된다. 부문 손익 검산([D33](../../docs/decisions.md))에서
        같은 이유로 쓴 척도다.
        """
        if not self.actual_revenue:
            return None
        return (self.estimate - self.actual) / abs(self.actual_revenue) * 100.0

    @property
    def abs_error_pct(self) -> float | None:
        e = self.error_pct
        return abs(e) if e is not None else None

    @property
    def sign_correct(self) -> bool | None:
        """부호를 맞혔는가. 적자를 흑자로 추정했으면 크기와 무관하게 틀린 것이다."""
        if self.actual == 0:
            return None
        return (self.estimate >= 0) == (self.actual >= 0)


@dataclass(frozen=True)
class Skipped:
    """추정을 내지 못한 (종목, 연도). **오차가 아니라 커버리지 문제다.**"""

    symbol: str
    base_year: int
    reason: str


@dataclass
class MetricSummary:
    """지표 1개의 오차 요약.

    **중앙값을 대표값으로 쓴다.** 평균(`mape`·`mean_bias`)도 들고 있지만 읽을
    때 앞세우면 안 된다 — 실측에서 매출 평균 편향이 +118%로 나왔는데 그중
    +20,852% 한 건이 만든 값이었고, 중앙값 편향은 +6.6%였다. 같은 표본을 두고
    "기준선이 두 배 넘게 과대추정한다"와 "7% 정도 위로 기운다"가 갈린다.
    """

    metric: str
    label: str
    n: int = 0
    median_ape: float | None = None  # 대표 오차 — 꼬리에 덜 휘둘린다
    median_bias: float | None = None  # 대표 편향(부호 있는 중앙값)
    mape: float | None = None  # 평균 절대 오차 — 꼬리에 지배된다
    mean_bias: float | None = None  # 부호 있는 평균 — 꼬리에 지배된다
    hit_rates: dict[float, float] = field(default_factory=dict)  # 밴드 → 비중(%)
    over_rate: float | None = None  # 과대추정한 비중(%). 편향의 방향을 다시 확인한다
    blowup_rate: float | None = None  # |오차| > BLOWUP_PCT 비중(%)
    sign_accuracy: float | None = None  # 흑자/적자 방향을 맞힌 비율(%)
    median_of_revenue: float | None = None  # 매출 대비 절대오차 중앙값(pp)

    def describe(self) -> str:
        if self.n == 0:
            return f"{self.label}: 표본 없음"
        bands = " · ".join(f"±{b:.0f}% 내 {self.hit_rates.get(b, 0):.0f}%" for b in HIT_BANDS)
        out = (
            f"{self.label}: n={self.n} · 오차 중앙값 {self.median_ape:.1f}% · "
            f"편향 중앙값 {self.median_bias:+.1f}% (과대 {self.over_rate:.0f}%) · {bands}"
        )
        if self.median_of_revenue is not None:
            out += f" · 매출 대비 {self.median_of_revenue:.1f}%p"
        if self.sign_accuracy is not None:
            out += f" · 흑/적 방향 {self.sign_accuracy:.0f}%"
        out += f" · 폭발(|오차|>{BLOWUP_PCT:.0f}%) {self.blowup_rate:.0f}%"
        return out


@dataclass
class BacktestResult:
    """백테스트 전체 결과. **거부 건수를 함께 들고 다닌다.**"""

    errors: list[ForecastError] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    summaries: dict[str, MetricSummary] = field(default_factory=dict)
    # 두 해 중 한쪽이라도 쓸 만한 공시가 없어 대조 자체가 불가능했던 쌍.
    # 우리 판단이 아니라 데이터의 부재이므로 `skipped`와 섞지 않는다.
    no_data: int = 0

    @property
    def attempted(self) -> int:
        """대조를 시도한 (종목, 연도) 쌍의 수 — 오차 건수가 아니다."""
        return len({(e.symbol, e.base_year) for e in self.errors}) + len(self.skipped)

    @property
    def candidate_pairs(self) -> int:
        """가능했던 쌍 전부. 데이터가 없어 못 본 것까지 센다."""
        return self.attempted + self.no_data

    @property
    def data_coverage_pct(self) -> float | None:
        """두 해 모두 쓸 만한 공시가 있었던 비율.

        **제품 관점에서 이게 첫 번째 숫자다.** 코스닥 무작위 표본에서 이 값이
        낮으면 "추정이 얼마나 맞나"보다 "애초에 몇 곳을 다룰 수 있나"가 먼저다.
        """
        total = self.candidate_pairs
        return self.attempted / total * 100.0 if total else None

    @property
    def coverage_pct(self) -> float | None:
        """대조 가능한 쌍 중 추정을 실제로 낸 비율(= 우리가 보류하지 않은 비율)."""
        total = self.attempted
        if not total:
            return None
        return (total - len(self.skipped)) / total * 100.0

    def for_metric(self, metric: str) -> list[ForecastError]:
        return [e for e in self.errors if e.metric == metric]


def evaluate(
    estimate: EstimateSet,
    actual: MetricSet,
    symbol: str,
) -> list[ForecastError] | Skipped:
    """추정 1건 × 실적 1건 → 지표별 오차. 이어지지 않는 두 해면 **거부한다.**

    `Skipped`를 돌려주는 경우가 결함이 아니라 정상 경로다. 추정을 내지 않기로
    한 판단([D24](../../docs/decisions.md))을 오차 0으로 세면 성적이 좋아 보인다.
    """
    base_year = estimate.base_year
    if not estimate.usable:
        reason = estimate.warnings[0] if estimate.warnings else "추정을 산출하지 않았다."
        return Skipped(symbol=symbol, base_year=base_year, reason=reason)
    if actual.fiscal_year != estimate.fiscal_year:
        # 여기서 막지 않으면 엉뚱한 해와 대조하고도 숫자가 나온다
        return Skipped(
            symbol=symbol,
            base_year=base_year,
            reason=(
                f"추정 대상({estimate.fiscal_year})과 실적 연도({actual.fiscal_year})가 다르다."
            ),
        )
    if actual.fiscal_year != base_year + 1:
        return Skipped(
            symbol=symbol,
            base_year=base_year,
            reason=f"기준 연도({base_year})의 다음 해가 아니다({actual.fiscal_year}).",
        )

    out: list[ForecastError] = []
    actual_revenue = actual.get("revenue")
    for metric, _ in BACKTEST_METRICS:
        est = estimate.values.get(metric)
        act = actual.get(metric)
        if est is None or act is None:
            continue
        out.append(
            ForecastError(
                symbol=symbol,
                base_year=base_year,
                target_year=actual.fiscal_year,
                metric=metric,
                estimate=est,
                actual=act,
                actual_revenue=actual_revenue,
            )
        )
    if not out:
        return Skipped(symbol=symbol, base_year=base_year, reason="대조할 지표가 하나도 없다.")
    return out


def summarize(errors: list[ForecastError]) -> dict[str, MetricSummary]:
    """지표별 오차 요약. **중앙값과 평균을 함께 낸다.**

    둘이 벌어지면 분포가 소수의 폭발에 지배된다는 뜻이고, 그건 "기준선이
    쓸모없다"가 아니라 "어떤 종목에서 깨지는가"라는 다음 질문을 부른다.
    """
    out: dict[str, MetricSummary] = {}
    for metric, label in BACKTEST_METRICS:
        picked = [e for e in errors if e.metric == metric]
        apes = [e.abs_error_pct for e in picked if e.abs_error_pct is not None]
        signed = [e.error_pct for e in picked if e.error_pct is not None]
        signs = [e.sign_correct for e in picked if e.sign_correct is not None]
        summary = MetricSummary(metric=metric, label=label, n=len(apes))
        if apes:
            summary.median_ape = statistics.median(apes)
            summary.median_bias = statistics.median(signed)
            summary.mape = sum(apes) / len(apes)
            summary.mean_bias = sum(signed) / len(signed)
            summary.hit_rates = {
                band: sum(1 for a in apes if a <= band) / len(apes) * 100.0 for band in HIT_BANDS
            }
            summary.over_rate = sum(1 for s in signed if s > 0) / len(signed) * 100.0
            summary.blowup_rate = sum(1 for a in apes if a > BLOWUP_PCT) / len(apes) * 100.0
        if signs:
            summary.sign_accuracy = sum(1 for s in signs if s) / len(signs) * 100.0
        if metric in _SCALED_METRICS:
            scaled = [
                abs(e.error_of_revenue_pct) for e in picked if e.error_of_revenue_pct is not None
            ]
            if scaled:
                summary.median_of_revenue = statistics.median(scaled)
        out[metric] = summary
    return out


def build_result(
    outcomes: list[list[ForecastError] | Skipped],
    no_data: int = 0,
) -> BacktestResult:
    """`evaluate` 결과들을 모아 요약까지 낸다."""
    result = BacktestResult(no_data=no_data)
    for item in outcomes:
        if isinstance(item, Skipped):
            result.skipped.append(item)
        else:
            result.errors.extend(item)
    result.summaries = summarize(result.errors)
    return result


def run(
    symbols: list[str],
    years: list[int],
    fetch: Callable[[str, int], MetricSet | None],
    *,
    on_progress: Callable[[str, int], None] | None = None,
) -> BacktestResult:
    """종목 × 연도를 훑어 백테스트를 돌린다.

    `fetch`를 주입받는 이유는 이 함수가 네트워크를 모르게 하기 위해서다 —
    테스트가 DART 없이 전 경로를 돌릴 수 있어야 한다.

    **연도별 재무제표는 한 번만 받는다.** FY(Y+1)은 (Y, Y+1) 쌍의 실적이면서
    (Y+1, Y+2) 쌍의 기준이기도 하다. 다시 받으면 호출이 두 배가 된다.
    """
    outcomes: list[list[ForecastError] | Skipped] = []
    no_data = 0
    for symbol in symbols:
        cache: dict[int, MetricSet | None] = {}
        for year in sorted(set(years)):
            for y in (year, year + 1):
                if y not in cache:
                    if on_progress is not None:
                        on_progress(symbol, y)
                    cache[y] = fetch(symbol, y)
            base, actual = cache[year], cache[year + 1]
            if base is None or actual is None:
                # 공시가 없는 해는 **거부로 세지 않는다.** 우리 기준선이 판단을
                # 보류한 것이 아니라 대조 자체가 불가능한 것이다. 다만 세지 않고
                # 버리면 "코스닥에서 몇 곳을 다룰 수 있나"라는 더 앞선 질문이
                # 통계에서 사라진다.
                no_data += 1
                continue
            outcomes.append(evaluate(build_estimates(base), actual, symbol))
    return build_result(outcomes, no_data=no_data)


def describe(result: BacktestResult) -> list[str]:
    """사람이 읽을 요약. **커버리지를 먼저 말한다.**"""
    lines: list[str] = []
    data_cov = result.data_coverage_pct
    if data_cov is not None and result.no_data:
        lines.append(
            f"가능한 (종목, 연도) 쌍 {result.candidate_pairs}건 중 두 해 모두 쓸 만한 공시가 "
            f"있었던 것은 {result.attempted}건 ({data_cov:.0f}%). "
            f"나머지 {result.no_data}건은 공시가 없거나 커버리지가 모자라 대조하지 못했다."
        )
    cov = result.coverage_pct
    if cov is not None:
        lines.append(
            f"대조 가능한 {result.attempted}건 중 추정 산출 "
            f"{result.attempted - len(result.skipped)}건 (커버리지 {cov:.0f}%). "
            f"산출하지 않은 {len(result.skipped)}건은 오차에서 제외했다."
        )
    for metric, _ in BACKTEST_METRICS:
        s = result.summaries.get(metric)
        if s is not None:
            lines.append(s.describe())
    return lines
