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
    "cost_of_sales": {
        "account_ids": ("ifrs-full_CostOfSales",),
        "names": ("매출원가", "영업비용"),
    },
    "gross_profit": {
        "account_ids": ("ifrs-full_GrossProfit",),
        "names": ("매출총이익", "매출총이익(손실)"),
    },
    "sga": {
        "account_ids": ("dart_TotalSellingGeneralAdministrativeExpenses",),
        "names": ("판매비와관리비", "판매비와일반관리비", "판매관리비"),
    },
    "operating_income": {
        "account_ids": ("dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"),
        "names": ("영업이익", "영업이익(손실)", "영업손실"),
    },
    "pretax_income": {
        "account_ids": ("ifrs-full_ProfitLossBeforeTax",),
        "names": (
            "법인세비용차감전순이익",
            "법인세비용차감전순손익",
            "세전이익",
            "법인세차감전 순이익",  # 주요계정 API 표기 (공백 주의)
        ),
    },
    "net_income": {
        "account_ids": ("ifrs-full_ProfitLoss",),
        "names": ("당기순이익", "당기순이익(손실)", "당기순손실", "연결당기순이익"),
    },
    "eps_diluted": {
        "account_ids": ("ifrs-full_DilutedEarningsLossPerShare",),
        "names": ("희석주당이익", "희석주당순이익", "희석주당이익(손실)"),
    },
    # 지배주주 귀속분 — ROE·EPS의 올바른 분자다. `net_income`(전체)에는
    # 비지배지분이 섞여 있어 지배주주 지표와 섞으면 안 된다. 실측: 삼성전자
    # FY2025 전체 45.2조 vs 지배주주 44.26조(= 배당공시 (연결)당기순이익).
    "net_income_parent": {
        "account_ids": ("ifrs-full_ProfitLossAttributableToOwnersOfParent",),
        "names": (),  # 계정명이 "지배기업 소유주지분"이라 이름 매칭은 위험하다
    },
    # ── 재무상태표 ──
    "total_assets": {
        "account_ids": ("ifrs-full_Assets",),
        "names": ("자산총계",),
    },
    "total_liabilities": {
        "account_ids": ("ifrs-full_Liabilities",),
        "names": ("부채총계",),
    },
    "total_equity": {
        "account_ids": ("ifrs-full_Equity",),
        "names": ("자본총계",),
    },
    "equity_parent": {
        "account_ids": ("ifrs-full_EquityAttributableToOwnersOfParent",),
        "names": (),
    },
}

# 손익 표에 넣을 지표와 순서. 재무상태표 계정이 손익 표에 들어가면 안 된다.
INCOME_STATEMENT_METRICS: tuple[str, ...] = (
    "revenue",
    "cost_of_sales",
    "gross_profit",
    "sga",
    "operating_income",
    "pretax_income",
    "net_income",
    "net_income_parent",
    "eps_diluted",
)

# 손익 관련 지표는 IS(손익계산서)를 우선하고 CIS(포괄손익계산서)로 폴백한다.
_STATEMENT_PREFERENCE = ("IS", "CIS")

_LABELS = {
    "revenue": "매출액",
    "cost_of_sales": "매출원가",
    "gross_profit": "매출총이익",
    "sga": "판매비와관리비",
    "operating_income": "영업이익",
    "pretax_income": "법인세차감전순이익",
    "net_income": "당기순이익",
    "eps_diluted": "희석주당이익",
    "net_income_parent": "지배주주순이익",
    "total_assets": "자산총계",
    "total_liabilities": "부채총계",
    "total_equity": "자본총계",
    "equity_parent": "지배주주지분",
}

# 주당 지표는 원 단위 금액이 아니라 '원/주'다. 조·억 표기를 쓰면 안 된다.
_PER_SHARE = {"eps_diluted"}

# 매출 대비 비율을 계산할 지표 → (키 접두사, 라벨)
_MARGIN_SPECS: tuple[tuple[str, str, str], ...] = (
    ("gross_profit", "gross_margin", "매출총이익률"),
    ("cost_of_sales", "cost_ratio", "원가율"),
    ("sga", "sga_ratio", "판관비율"),
    ("operating_income", "operating_margin", "영업이익률"),
    ("net_income", "net_margin", "순이익률"),
)


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
    # 전전기 — 성장률을 2개 관측해야 추세의 진폭을 잴 수 있다 (estimates 참조)
    prior2: int | None = None
    matched_by: str = ""  # "account_id" | "account_name"
    matched_on: str = ""  # 실제로 매칭된 값 (감사용)
    statement_type: str | None = None


@dataclass
class MetricSet:
    """한 재무제표에서 뽑아낸 표준 지표 묶음 + 커버리지."""

    fiscal_year: int
    values: dict[str, MetricValue] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    @property
    def missing_labels(self) -> list[str]:
        """못 찾은 지표의 한글 라벨. 독자에게 `cost_of_sales`는 의미가 없다."""
        return [_LABELS.get(k, k) for k in self.missing]

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

    def get_prior2(self, key: str) -> int | None:
        v = self.values.get(key)
        return v.prior2 if v else None


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
            prior2=item.prior2_amount,
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
            key=lambda i: (
                _STATEMENT_PREFERENCE.index(i.statement_type)
                if i.statement_type in _STATEMENT_PREFERENCE
                else len(_STATEMENT_PREFERENCE)
            ),
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


def fmt_per_share(v: float | None) -> str | None:
    """주당 지표는 원/주. 조·억 표기를 쓰면 안 된다."""
    if v is None:
        return None
    return f"{v:,.0f}원"


def fmt_pct(v: float | None, digits: int = 1) -> str | None:
    if v is None:
        return None
    return f"{v:.{digits}f}%"


# ── 마진 브리지 ──────────────────────────────────────────────────────
@dataclass
class MarginBridge:
    """영업이익률 변화를 비용 항목 기여도로 분해한다.

        영업이익률 = 1 - 원가율 - 판관비율

    이므로 변화는 이렇게 갈린다:

        Δ영업이익률 = (-Δ원가율) + (-Δ판관비율)

    비용 비율이 내려가면 마진에 플러스로 기여한다. 부호를 뒤집는 이유다.
    `residual`은 검산값 — 0에 가까워야 한다. 벌어지면 영업이익 정의가
    매출-원가-판관비와 다르다는 뜻이므로 표시하고 넘어가지 않는다.
    """

    fiscal_year: int
    margin_change: float  # pp
    cost_contribution: float  # pp (부호 반전됨)
    sga_contribution: float  # pp (부호 반전됨)
    residual: float  # pp
    reconciled: bool

    @property
    def dominant(self) -> str:
        """어느 쪽이 더 크게 움직였나. 서술의 논지가 되는 부분."""
        if abs(self.cost_contribution) >= abs(self.sga_contribution):
            return "원가율"
        return "판관비율"


def build_margin_bridge(ms: MetricSet, *, tolerance: float = 0.15) -> MarginBridge | None:
    """마진 브리지. 입력이 하나라도 없으면 만들지 않는다 (추정 금지)."""
    rev, rev_p = ms.get("revenue"), ms.get_prior("revenue")
    need = ("cost_of_sales", "sga", "operating_income")
    if not all(ms.get(k) is not None and ms.get_prior(k) is not None for k in need):
        return None
    if rev is None or rev_p is None:
        return None

    om = margin(ms.get("operating_income"), rev)
    om_p = margin(ms.get_prior("operating_income"), rev_p)
    cr, cr_p = margin(ms.get("cost_of_sales"), rev), margin(ms.get_prior("cost_of_sales"), rev_p)
    sr, sr_p = margin(ms.get("sga"), rev), margin(ms.get_prior("sga"), rev_p)
    if None in (om, om_p, cr, cr_p, sr, sr_p):
        return None

    change = om - om_p
    cost_c = -(cr - cr_p)  # 원가율 하락 → 마진에 플러스
    sga_c = -(sr - sr_p)
    residual = change - (cost_c + sga_c)

    return MarginBridge(
        fiscal_year=ms.fiscal_year,
        margin_change=change,
        cost_contribution=cost_c,
        sga_contribution=sga_c,
        residual=residual,
        reconciled=abs(residual) <= tolerance,
    )


# ── 관찰(논지) 생성 ─────────────────────────────────────────────────
def build_observations(ms: MetricSet, bridge: MarginBridge | None = None) -> list[str]:
    """계산 결과에서 **구조적 사실**만 뽑아 문장으로 만든다.

    LLM 프롬프트의 논지가 된다. 카탈로그만 주면 LLM이 쓸 수 있는 것이
    "지표 나열"뿐이라 글이 읽히지 않는다. 어느 비용이 마진을 움직였는지,
    외형과 이익이 같은 방향인지는 결정적으로 계산할 수 있고, 이것이
    글의 뼈대가 되어야 한다.

    **크기를 쓰지 않는다.** 여기 쓴 숫자는 LLM이 리터럴로 베낄 수 있다.
    방향과 우열(어느 쪽이 더 컸는가)만 담고, 크기는 플레이스홀더로
    본문에 들어간다.
    """
    obs: list[str] = []

    if bridge is not None:
        d = "개선" if bridge.margin_change > 0 else "악화" if bridge.margin_change < 0 else "보합"
        if bridge.reconciled:
            other = "판관비율" if bridge.dominant == "원가율" else "원가율"
            same = (bridge.cost_contribution >= 0) == (bridge.sga_contribution >= 0)
            obs.append(
                f"영업이익률은 {d}됐고, 이 변화는 원가율과 판관비율의 기여로 "
                f"남김없이 설명된다(검산 완료). 기여가 더 큰 쪽은 {bridge.dominant}이다."
            )
            obs.append(
                f"{other}은 {bridge.dominant}과 "
                + (
                    "같은 방향으로 함께 기여했다."
                    if same
                    else "반대 방향으로 작용해 일부를 상쇄했다."
                )
            )
        else:
            obs.append(
                f"영업이익률은 {d}됐으나 원가율·판관비율 기여의 합이 변화폭과 맞지 않는다. "
                "영업이익 정의에 매출원가·판관비 외 항목이 포함돼 있을 수 있어 단정하지 않는다."
            )

    rv, oi = (
        yoy(ms.get("revenue"), ms.get_prior("revenue")),
        yoy(ms.get("operating_income"), ms.get_prior("operating_income")),
    )
    if rv is not None and oi is not None:
        if oi > rv:
            obs.append("영업이익 증가율이 매출 증가율을 웃돈다 — 운영 레버리지가 작동한 구간이다.")
        elif oi < rv:
            obs.append("영업이익 증가율이 매출 증가율에 못 미친다 — 비용이 외형보다 빨리 늘었다.")
        else:
            obs.append("매출과 영업이익이 같은 속도로 움직였다 — 마진 구조에 변화가 없다.")

    ni = yoy(ms.get("net_income"), ms.get_prior("net_income"))
    if ni is not None and oi is not None and (ni >= 0) != (oi >= 0):
        obs.append(
            "순이익과 영업이익의 증감 방향이 엇갈린다. 영업 외 손익이나 법인세에서 "
            "차이가 났다는 뜻이므로 본업 성과와 분리해 읽어야 한다."
        )

    if ms.missing:
        obs.append(
            f"공시에서 확인되지 않은 계정: {', '.join(ms.missing)}. 이 항목은 언급하지 않는다."
        )

    return obs


# ── Number Registry 항목 생성 ────────────────────────────────────────
def build_entries(ms: MetricSet, prov: Provenance) -> list[NumberEntry]:
    """표준 지표 + 파생값 → NumberEntry 목록.

    키 규약: `{metric}_{year}a` (a=actual). 파생은 `{metric}_yoy_{year}a`,
    `{metric}_margin_{year}a`.
    """
    y = ms.fiscal_year
    out: list[NumberEntry] = []

    def add(
        key: str,
        value,
        unit: str,
        display: str | None,
        label: str,
        formula: str | None = None,
        inputs: list[str] | None = None,
        internal: bool = False,
    ) -> None:
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
                internal=internal,
            )
        )

    # 원시 지표 — 당기·전기
    for key, mv in ms.values.items():
        if key in _PER_SHARE:
            # 주당 지표는 금액 규모가 아니라 원/주다
            add(f"{key}_{y}a", mv.current, "원", fmt_per_share(mv.current), f"{mv.label} ({y}A)")
            add(
                f"{key}_{y - 1}a", mv.prior, "원", fmt_per_share(mv.prior), f"{mv.label} ({y - 1}A)"
            )
        else:
            add(f"{key}_{y}a", mv.current, "원", fmt_krw(mv.current), f"{mv.label} ({y}A)")
            add(f"{key}_{y - 1}a", mv.prior, "원", fmt_krw(mv.prior), f"{mv.label} ({y - 1}A)")

    # YoY
    for key, mv in ms.values.items():
        v = yoy(mv.current, mv.prior)
        add(
            f"{key}_yoy_{y}a",
            v,
            "%",
            fmt_pct(v),
            f"{mv.label} YoY ({y}A)",
            formula=f"({key}_{y}a - {key}_{y - 1}a) / |{key}_{y - 1}a|",
            inputs=[f"{key}_{y}a", f"{key}_{y - 1}a"],
        )

    # 매출 대비 비율 + 전년 대비 변화(pp)
    rev, rev_prior = ms.get("revenue"), ms.get_prior("revenue")
    for src, ratio, label in _MARGIN_SPECS:
        v = margin(ms.get(src), rev)
        add(
            f"{ratio}_{y}a",
            v,
            "%",
            fmt_pct(v),
            f"{label} ({y}A)",
            formula=f"{src}_{y}a / revenue_{y}a",
            inputs=[f"{src}_{y}a", f"revenue_{y}a"],
        )
        vp = margin(ms.get_prior(src), rev_prior)
        add(
            f"{ratio}_{y - 1}a",
            vp,
            "%",
            fmt_pct(vp),
            f"{label} ({y - 1}A)",
            formula=f"{src}_{y - 1}a / revenue_{y - 1}a",
            inputs=[f"{src}_{y - 1}a", f"revenue_{y - 1}a"],
        )
        if v is not None and vp is not None:
            d = v - vp
            add(
                f"{ratio}_chg_{y}a",
                d,
                "pp",
                f"{d:+.1f}pp",
                f"{label} 변화 ({y - 1}A→{y}A)",
                formula=f"{ratio}_{y}a - {ratio}_{y - 1}a",
                inputs=[f"{ratio}_{y}a", f"{ratio}_{y - 1}a"],
            )

    # 마진 브리지 기여도
    br = build_margin_bridge(ms)
    if br is not None:
        add(
            f"bridge_cost_contrib_{y}a",
            br.cost_contribution,
            "pp",
            f"{br.cost_contribution:+.1f}pp",
            f"영업이익률 변화 중 원가율 기여 ({y}A)",
            formula="-(원가율_당기 - 원가율_전기)",
            inputs=[f"cost_ratio_{y}a", f"cost_ratio_{y - 1}a"],
        )
        add(
            f"bridge_sga_contrib_{y}a",
            br.sga_contribution,
            "pp",
            f"{br.sga_contribution:+.1f}pp",
            f"영업이익률 변화 중 판관비율 기여 ({y}A)",
            formula="-(판관비율_당기 - 판관비율_전기)",
            inputs=[f"sga_ratio_{y}a", f"sga_ratio_{y - 1}a"],
        )
        add(
            f"bridge_residual_{y}a",
            br.residual,
            "pp",
            f"{br.residual:+.1f}pp",
            f"마진 브리지 검산 차이 ({y}A)",
            formula="영업이익률변화 - (원가율기여 + 판관비율기여)",
            internal=True,
        )  # 감사용 — 카탈로그·본문에 노출하지 않는다

    return out
