"""결정적 계산 레이어 — 재무제표 → 표준 지표 → Number Registry 항목.

**모든 수치는 여기서 나온다.** LLM은 계산에 관여하지 않는다 (ARCHITECTURE.md §4.2).

계정과목 매핑이 이 모듈의 최대 리스크다. SEC는 us-gaap XBRL 태그로 표준화돼
있지만 DART의 K-IFRS 계정명은 회사·연도별로 흔들린다. 그래서 매핑을 코드가
아닌 **데이터**(`ACCOUNT_MAP`)로 두고, 2단계로 찾는다:

  1. `account_id` (IFRS 표준계정 코드) — 있으면 가장 신뢰할 만하다
  2. `account_name` 정규화 매칭 — 폴백

찾지 못한 지표는 **추정하지 않고 비운다.** 커버리지 부족은 정직하게 드러낸다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from arc.data.base import FinancialStatement, Provenance
from arc.llm.number_registry import NumberEntry

# ── 표준 지표 정의 ───────────────────────────────────────────────────
# key: 표준 지표명 / account_ids: IFRS 표준계정 코드 후보 / names: 계정명 후보
ACCOUNT_MAP: dict[str, dict[str, tuple[str, ...]]] = {
    "revenue": {
        "account_ids": ("ifrs-full_Revenue", "ifrs_Revenue"),
        "names": ("매출액", "수익(매출액)", "영업수익", "매출", "수익"),
    },
    "operating_income": {
        "account_ids": ("dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"),
        "names": ("영업이익", "영업이익(손실)", "영업손실"),
    },
    "net_income": {
        "account_ids": ("ifrs-full_ProfitLoss",),
        "names": ("당기순이익", "당기순이익(손실)", "당기순손실", "연결당기순이익"),
    },
}

# 손익 관련 지표는 IS(손익계산서)를 우선하고 CIS(포괄손익계산서)로 폴백한다.
_STATEMENT_PREFERENCE = ("IS", "CIS")

_LABELS = {
    "revenue": "매출액",
    "operating_income": "영업이익",
    "net_income": "당기순이익",
}


def _norm(s: str) -> str:
    """계정명 정규화 — 공백·중점 제거."""
    return re.sub(r"[\s·・]", "", s or "")


@dataclass
class MetricValue:
    """표준 지표 1건. 당기·전기 금액과 매칭 근거를 함께 보관한다."""

    key: str
    label: str
    current: int | None
    prior: int | None
    matched_by: str  # "account_id" | "account_name"
    matched_on: str  # 실제로 매칭된 값 (감사용)
    statement_type: str | None


@dataclass
class MetricSet:
    """한 재무제표에서 뽑아낸 표준 지표 묶음 + 커버리지."""

    fiscal_year: int
    values: dict[str, MetricValue] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    @property
    def coverage_ok(self) -> bool:
        """리포트를 쓸 수 있는 최소 커버리지 — 매출과 영업이익."""
        return "revenue" in self.values and "operating_income" in self.values

    def get(self, key: str) -> int | None:
        v = self.values.get(key)
        return v.current if v else None

    def get_prior(self, key: str) -> int | None:
        v = self.values.get(key)
        return v.prior if v else None


def extract_metrics(stmt: FinancialStatement) -> MetricSet:
    """재무제표 → 표준 지표. 못 찾은 것은 `missing`에 남기고 채우지 않는다."""
    result = MetricSet(fiscal_year=stmt.fiscal_year)

    for key, spec in ACCOUNT_MAP.items():
        hit = _find(stmt, spec)
        if hit is None:
            result.missing.append(key)
            continue
        item, matched_by, matched_on = hit
        result.values[key] = MetricValue(
            key=key,
            label=_LABELS.get(key, key),
            current=item.amount,
            prior=item.prior_amount,
            matched_by=matched_by,
            matched_on=matched_on,
            statement_type=item.statement_type,
        )
    return result


def _find(stmt: FinancialStatement, spec: dict[str, tuple[str, ...]]):
    """account_id 우선, account_name 폴백. 손익표(IS)를 CIS보다 우선한다."""
    def by_pref(items):
        return sorted(
            items,
            key=lambda i: _STATEMENT_PREFERENCE.index(i.statement_type)
            if i.statement_type in _STATEMENT_PREFERENCE
            else len(_STATEMENT_PREFERENCE),
        )

    # 1단계: 표준계정 코드
    ids = spec.get("account_ids", ())
    cands = [i for i in stmt.items if i.account_id in ids and i.amount is not None]
    if cands:
        best = by_pref(cands)[0]
        return best, "account_id", best.account_id or ""

    # 2단계: 계정명 정규화 매칭
    names = {_norm(n) for n in spec.get("names", ())}
    cands = [i for i in stmt.items if _norm(i.account_name) in names and i.amount is not None]
    if cands:
        best = by_pref(cands)[0]
        return best, "account_name", best.account_name

    return None


# ── 파생 계산 ────────────────────────────────────────────────────────
def yoy(current: int | None, prior: int | None) -> float | None:
    """전년 대비 증감률(%). 전기가 0이거나 없으면 계산하지 않는다."""
    if current is None or prior is None or prior == 0:
        return None
    return (current - prior) / abs(prior) * 100.0


def margin(numerator: int | None, denominator: int | None) -> float | None:
    """마진(%). 분모가 0이거나 없으면 계산하지 않는다."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator * 100.0


# ── 표시 포맷 ────────────────────────────────────────────────────────
_EOK = 100_000_000  # 1억


def fmt_krw(amount: int | None) -> str | None:
    """원 단위 정수 → 한국 리서치 관행 표기 (조/억)."""
    if amount is None:
        return None
    sign = "-" if amount < 0 else ""
    a = abs(amount)
    eok = a / _EOK
    if eok >= 10_000:  # 1조 이상
        jo, rem = divmod(a, 10_000 * _EOK)
        rem_eok = round(rem / _EOK)
        return f"{sign}{jo}조 {rem_eok:,}억원" if rem_eok else f"{sign}{jo}조원"
    return f"{sign}{round(eok):,}억원"


def fmt_pct(v: float | None, digits: int = 1) -> str | None:
    if v is None:
        return None
    return f"{v:.{digits}f}%"


# ── Number Registry 항목 생성 ────────────────────────────────────────
def build_entries(ms: MetricSet, prov: Provenance) -> list[NumberEntry]:
    """표준 지표 + 파생값 → NumberEntry 목록.

    키 규약: `{metric}_{year}a` (a=actual). 파생은 `{metric}_yoy_{year}a`,
    `{metric}_margin_{year}a`.
    """
    y = ms.fiscal_year
    out: list[NumberEntry] = []

    def add(key: str, value, unit: str, display: str | None, label: str,
            formula: str | None = None, inputs: list[str] | None = None) -> None:
        if value is None or display is None:
            return
        out.append(
            NumberEntry(
                key=key, value=value, unit=unit, display=display,
                provenance=prov, label=label, formula=formula, inputs=inputs or [],
            )
        )

    # 원시 지표 — 당기·전기
    for key, mv in ms.values.items():
        add(f"{key}_{y}a", mv.current, "원", fmt_krw(mv.current), f"{mv.label} ({y}A)")
        add(f"{key}_{y - 1}a", mv.prior, "원", fmt_krw(mv.prior), f"{mv.label} ({y - 1}A)")

    # YoY
    for key, mv in ms.values.items():
        v = yoy(mv.current, mv.prior)
        add(
            f"{key}_yoy_{y}a", v, "%", fmt_pct(v), f"{mv.label} YoY ({y}A)",
            formula=f"({key}_{y}a - {key}_{y - 1}a) / |{key}_{y - 1}a|",
            inputs=[f"{key}_{y}a", f"{key}_{y - 1}a"],
        )

    # 마진
    rev, rev_prior = ms.get("revenue"), ms.get_prior("revenue")
    for key, label in (("operating_income", "영업이익률"), ("net_income", "순이익률")):
        v = margin(ms.get(key), rev)
        add(
            f"{key.split('_')[0]}_margin_{y}a", v, "%", fmt_pct(v), f"{label} ({y}A)",
            formula=f"{key}_{y}a / revenue_{y}a",
            inputs=[f"{key}_{y}a", f"revenue_{y}a"],
        )
        vp = margin(ms.get_prior(key), rev_prior)
        add(
            f"{key.split('_')[0]}_margin_{y - 1}a", vp, "%", fmt_pct(vp), f"{label} ({y - 1}A)",
            formula=f"{key}_{y - 1}a / revenue_{y - 1}a",
            inputs=[f"{key}_{y - 1}a", f"revenue_{y - 1}a"],
        )
        # 마진 변화 (pp)
        if v is not None and vp is not None:
            d = v - vp
            add(
                f"{key.split('_')[0]}_margin_chg_{y}a", d, "pp", f"{d:+.1f}pp",
                f"{label} 변화 ({y - 1}A→{y}A)",
                formula=f"{key.split('_')[0]}_margin_{y}a - {key.split('_')[0]}_margin_{y - 1}a",
                inputs=[f"{key.split('_')[0]}_margin_{y}a", f"{key.split('_')[0]}_margin_{y - 1}a"],
            )

    return out
