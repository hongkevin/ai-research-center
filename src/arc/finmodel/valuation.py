"""밸류에이션 레이어 — 주식수·배당·재무상태표에서 나오는 주당·수익성 지표.

왜 별도 모듈인가
----------------
`metrics.py`는 재무제표 **한 장**에서 나오는 것만 다룬다. 밸류에이션은
재무제표에 없는 **주식수**가 있어야 시작된다(정기보고서 주요정보 API).
두 원천이 만나는 지점이라 분리했다.

가격 없이 어디까지 되는가
-------------------------
시세 API(`KRX_API_KEY`)가 없어도 다음은 확정적으로 나온다:

  BPS, ROE, 부채비율, 배당성향, 배당수익률(공시값)

주가가 필요한 PER·PBR·시가총액은 **DPS ÷ 배당수익률로 역산한 주가**를
앵커로 쓴다. DART가 어느 시점 주가로 수익률을 계산했는지 명시하지 않으므로
(통상 배당기준일 전 일정 기간 종가 평균) 이 값은 **정확한 시세가 아니다.**
그래서 `is_implied=True`로 표시하고, 시세 어댑터가 붙으면 대체한다.
표시하지 않고 조용히 쓰면 독자가 종가로 오인한다.

관행 주의
---------
* 시가총액·BPS의 분모는 **발행주식총수**(자기주식 포함), 유통주식수가 아니다.
* ROE·BPS의 분자는 **지배주주** 기준이다. 전체 자본/순이익에는 비지배지분이
  섞여 있어 주당 지표와 섞으면 안 된다.
* 우선주가 있는 회사는 보통주 주가 × 총발행주식수가 되어 시가총액이
  과대해진다. 관행이 그러하므로 따르되 `has_preferred`로 표시한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from arc.data.base import Provenance
from arc.data.kr.dart_reports import PeriodicReportInfo
from arc.finmodel.metrics import MetricSet, fmt_krw, fmt_pct, fmt_per_share
from arc.llm.number_registry import NumberEntry

# EPS 교차검증 허용 오차(%). 재무제표는 희석, 배당공시는 기본 EPS라 완전히
# 같지 않다. 이 이상 벌어지면 주식수나 순이익 기준을 잘못 잡은 것이다.
EPS_TOLERANCE_PCT = 5.0


def _div(a: float | None, b: float | None) -> float | None:
    """분모가 없거나 0이면 계산하지 않는다."""
    if a is None or b is None or b == 0:
        return None
    return a / b


@dataclass
class ValuationSet:
    """주당·수익성·밸류에이션 지표 묶음. 없는 것은 None으로 둔다."""

    fiscal_year: int

    # 주식수 (분모)
    shares_issued: int | None = None
    shares_outstanding: int | None = None
    has_preferred: bool = False
    shares_reconciled: bool = False

    # 주당 지표
    bps: float | None = None
    eps_stmt: int | None = None  # 재무제표 희석EPS
    eps_disclosed: int | None = None  # 배당공시 (연결)주당순이익
    eps_gap_pct: float | None = None  # 두 값의 괴리

    # 수익성·건전성
    roe: float | None = None
    roa: float | None = None
    debt_ratio: float | None = None

    # 배당
    dps: int | None = None
    dividend_yield: float | None = None
    payout_ratio: float | None = None

    # 가격 (역산 앵커)
    price: int | None = None
    is_implied: bool = True  # 시세 어댑터가 붙으면 False
    price_date: str | None = None  # 실제 종가일 (ISO). 역산이면 None
    # 역산 주가와 실제 종가의 괴리(%). **둘 다 있을 때만 잰다** — Q7의 질문이다.
    implied_gap_pct: float | None = None
    market_cap: int | None = None
    per: float | None = None
    pbr: float | None = None

    unavailable: list[str] = field(default_factory=list)

    @property
    def eps_cross_check_ok(self) -> bool | None:
        """재무제표 EPS와 공시 EPS가 맞는가.

        **주식수·순이익 기준을 잘못 잡았는지 잡아내는 유일한 독립 검산이다.**
        두 값은 다른 경로(재무제표 vs 배당공시)로 오므로 일치하면 양쪽이 맞다.
        """
        if self.eps_gap_pct is None:
            return None
        return abs(self.eps_gap_pct) <= EPS_TOLERANCE_PCT

    @property
    def has_price_anchor(self) -> bool:
        return self.price is not None


def build_valuation(
    ms: MetricSet,
    info: PeriodicReportInfo,
    *,
    close_price: int | None = None,
    close_date: str | None = None,
) -> ValuationSet:
    """지표 + 정기보고서 주요정보 → 밸류에이션 지표.

    **추정하지 않는다.** 입력이 없으면 해당 지표를 비우고 `unavailable`에
    이유를 남긴다.

    `close_price`가 오면 **그것을 쓴다** — 배당 역산은 특정일 종가가 아니라
    회계연도 전체를 뭉갠 값이라([D19](../../../docs/decisions.md#d19)) 시세가
    있으면 언제나 그쪽이 낫다. 다만 역산값도 버리지 않고 **둘의 괴리를 재서**
    남긴다([D60](../../../docs/decisions.md#d60)).
    """
    v = ValuationSet(fiscal_year=ms.fiscal_year)
    shares, div = info.shares, info.dividend

    if shares is None:
        v.unavailable.append("주식수")
    else:
        v.shares_issued = shares.issued
        v.shares_outstanding = shares.outstanding
        v.has_preferred = shares.has_preferred
        v.shares_reconciled = shares.reconciled

    # 기준(지배주주 / 전체)을 **한 번만** 고른다. 당기는 지배주주, 전기는
    # 전체를 쓰면 평균자본이 두 기준의 혼합이 되어 ROE가 조용히 틀린다.
    # 주요계정 폴백(fnlttSinglAcnt)에는 지배주주 계정이 없어 실제로 발생한다.
    equity_key = "equity_parent" if ms.get("equity_parent") is not None else "total_equity"
    income_key = "net_income_parent" if ms.get("net_income_parent") is not None else "net_income"
    equity_parent = ms.get(equity_key)
    ni_parent = ms.get(income_key)

    # BPS — 지배주주지분 ÷ 발행주식총수
    v.bps = _div(equity_parent, v.shares_issued)

    # ROE — 기초·기말 평균 자본. 전기가 없으면 기말로 계산하고 그 사실을 남긴다.
    eq_prior = ms.get_prior(equity_key)
    if equity_parent is not None and eq_prior is not None:
        avg_equity = (equity_parent + eq_prior) / 2
    else:
        avg_equity = equity_parent
        if equity_parent is not None:
            v.unavailable.append("전기 자본(ROE는 기말 기준)")
    roe = _div(ni_parent, avg_equity)
    v.roe = roe * 100.0 if roe is not None else None

    roa = _div(ms.get("net_income"), ms.get("total_assets"))
    v.roa = roa * 100.0 if roa is not None else None

    debt = _div(ms.get("total_liabilities"), ms.get("total_equity"))
    v.debt_ratio = debt * 100.0 if debt is not None else None

    # EPS 교차검증
    v.eps_stmt = ms.get("eps_diluted")
    if div is not None:
        v.eps_disclosed = div.eps_reported
        v.dps = div.dps_common
        v.dividend_yield = div.dividend_yield_common
        v.payout_ratio = div.payout_ratio
        v.price = div.implied_price
    else:
        v.unavailable.append("배당 공시")

    # **실제 종가가 있으면 그것을 쓴다.** 역산값은 괴리를 재는 데 남긴다.
    implied = v.price
    if close_price:
        if implied:
            v.implied_gap_pct = (implied - close_price) / close_price * 100.0
        v.price = close_price
        v.is_implied = False
        v.price_date = close_date

    if v.eps_stmt and v.eps_disclosed:
        v.eps_gap_pct = (v.eps_stmt - v.eps_disclosed) / abs(v.eps_disclosed) * 100.0

    # 가격 기반. `is_implied`면 역산 앵커라 정확한 시세가 아니다.
    if v.price is not None:
        if v.shares_issued:
            v.market_cap = v.price * v.shares_issued
        eps_for_per = v.eps_disclosed or v.eps_stmt
        v.per = _div(v.price, eps_for_per)
        v.pbr = _div(v.price, v.bps)
    else:
        v.unavailable.append("주가 앵커")

    return v


def build_valuation_entries(
    v: ValuationSet, info: PeriodicReportInfo, prov: Provenance
) -> list[NumberEntry]:
    """ValuationSet → NumberEntry 목록. 키 규약은 `metrics.build_entries`와 같다."""
    y = v.fiscal_year
    out: list[NumberEntry] = []

    # **항목마다 출처가 다르다.** 주식수는 `stockTotqySttus`, 배당은 `alotMatter`,
    # BPS·ROE는 재무제표에서 온다. 하나로 뭉뚱그리면 "이 배당성향 어디서
    # 나왔죠?"라는 검토자의 질문에 틀린 답을 하게 된다.
    share_prov = info.shares.provenance if info and info.shares else prov
    div_prov = info.dividend.provenance if info and info.dividend else prov

    def add(
        key, value, unit, display, label, formula=None, inputs=None, internal=False, source=None
    ):
        if value is None or display is None:
            return
        out.append(
            NumberEntry(
                key=f"{key}_{y}a",
                value=value,
                unit=unit,
                display=display,
                provenance=source or prov,
                label=f"{label} ({y}A)",
                formula=formula,
                inputs=inputs or [],
                internal=internal,
            )
        )

    add(
        "shares_issued",
        v.shares_issued,
        "주",
        f"{v.shares_issued:,}주" if v.shares_issued else None,
        "발행주식총수",
        source=share_prov,
    )
    add(
        "shares_outstanding",
        v.shares_outstanding,
        "주",
        f"{v.shares_outstanding:,}주" if v.shares_outstanding else None,
        "유통주식수",
        source=share_prov,
    )

    add(
        "bps",
        v.bps,
        "원",
        fmt_per_share(v.bps),
        "주당순자산(BPS)",
        formula="지배주주지분 / 발행주식총수",
        inputs=[f"equity_parent_{y}a", f"shares_issued_{y}a"],
    )
    add(
        "roe",
        v.roe,
        "%",
        fmt_pct(v.roe),
        "자기자본이익률(ROE)",
        formula="지배주주순이익 / 평균 지배주주지분",
        inputs=[f"net_income_parent_{y}a", f"equity_parent_{y}a"],
    )
    add(
        "roa",
        v.roa,
        "%",
        fmt_pct(v.roa),
        "총자산이익률(ROA)",
        formula="당기순이익 / 자산총계",
        inputs=[f"net_income_{y}a", f"total_assets_{y}a"],
    )
    add(
        "debt_ratio",
        v.debt_ratio,
        "%",
        fmt_pct(v.debt_ratio),
        "부채비율",
        formula="부채총계 / 자본총계",
        inputs=[f"total_liabilities_{y}a", f"total_equity_{y}a"],
    )

    add("dps", v.dps, "원", fmt_per_share(v.dps), "주당 현금배당금(DPS)", source=div_prov)
    add(
        "dividend_yield",
        v.dividend_yield,
        "%",
        fmt_pct(v.dividend_yield, 2),
        "현금배당수익률",
        source=div_prov,
    )
    add(
        "payout_ratio",
        v.payout_ratio,
        "%",
        fmt_pct(v.payout_ratio),
        "현금배당성향",
        source=div_prov,
    )

    # 가격 기반 — 역산 앵커임을 라벨에 명시한다. 독자가 종가로 오인하면 안 된다.
    tag = "역산 " if v.is_implied else ""
    add(
        "price",
        v.price,
        "원",
        fmt_per_share(v.price),
        f"{tag}주가",
        formula="주당현금배당금 / 현금배당수익률" if v.is_implied else None,
        # 역산 주가는 배당공시에서 나온다. 재무제표로 표시하면 검토자가
        # 종가로 오인할 여지가 커진다 (D19).
        source=div_prov if v.is_implied else None,
    )
    add(
        "market_cap",
        v.market_cap,
        "원",
        fmt_krw(v.market_cap),
        f"{tag}시가총액",
        formula="주가 × 발행주식총수",
        inputs=[f"price_{y}a", f"shares_issued_{y}a"],
        source=div_prov if v.is_implied else None,
    )
    add(
        "per",
        v.per,
        "배",
        f"{v.per:.1f}배" if v.per is not None else None,
        f"{tag}PER",
        formula="주가 / 주당순이익",
        inputs=[f"price_{y}a"],
        source=div_prov if v.is_implied else None,
    )
    add(
        "pbr",
        v.pbr,
        "배",
        f"{v.pbr:.2f}배" if v.pbr is not None else None,
        f"{tag}PBR",
        formula="주가 / 주당순자산",
        inputs=[f"price_{y}a", f"bps_{y}a"],
        source=div_prov if v.is_implied else None,
    )

    # 검산값 — 감사용이지 독자용이 아니다
    add(
        "eps_gap",
        v.eps_gap_pct,
        "%",
        fmt_pct(v.eps_gap_pct, 2),
        "EPS 교차검증 괴리",
        formula="(재무제표 희석EPS - 배당공시 EPS) / 배당공시 EPS",
        internal=True,
    )

    return out


def build_valuation_observations(v: ValuationSet, info: PeriodicReportInfo) -> list[str]:
    """밸류에이션·리스크 논지. **크기를 쓰지 않는다** (LLM이 리터럴로 베낀다)."""
    obs: list[str] = []

    if v.eps_cross_check_ok is False:
        obs.append(
            "재무제표 주당이익과 배당 공시 주당순이익이 크게 어긋난다. "
            "주식수 또는 순이익 기준이 다를 수 있어 주당 지표를 단정하지 않는다."
        )
    if v.shares_issued and not v.shares_reconciled:
        obs.append(
            "공시된 발행·자기주식·유통 주식수가 서로 맞지 않아 주식수 기반 지표를 신뢰하지 않는다."
        )
    if v.has_price_anchor and v.is_implied:
        obs.append(
            "주가는 배당수익률에서 역산한 앵커이지 종가가 아니다. "
            "PER·PBR·시가총액은 참고치이며 단정적으로 쓰지 않는다."
        )
    if v.has_preferred and v.market_cap is not None:
        obs.append("우선주가 있어 보통주 주가로 계산한 시가총액은 실제와 다를 수 있다.")

    audit = info.audit
    if audit is not None:
        if not audit.is_clean:
            obs.append(
                f"감사의견이 적정이 아니다({audit.opinion}). 이는 재무제표 신뢰성 자체에 관한 문제이므로 "
                "리스크에서 가장 먼저 다뤄야 한다."
            )
        if audit.kam_items:
            kam = "; ".join(audit.kam_items)
            line = f"감사인이 지목한 핵심감사사항: {kam}. 이는 감사인이 판단한 위험 영역이므로 리스크 서술의 근거로 쓸 수 있다."
            if audit.is_clean:
                # KAM이 있다고 부실은 아니다. 적정의견 하에서도 항상 지정된다.
                line += " 다만 감사의견 자체는 적정이므로 부실로 단정하면 안 된다."
            obs.append(line)

    wf = info.workforce
    if wf is not None and wf.has_segments:
        obs.append(
            f"공시된 사업부문은 {', '.join(wf.division_names)}이다. "
            "다만 이는 **인력** 구분이고 부문별 매출은 공시 API에 없으므로 "
            "매출 구성으로 옮겨 말하면 안 된다."
        )

    return obs


# ── 선행 배수 (추정 연결) ────────────────────────────────────────────
def build_forward_entries(
    v: ValuationSet, est_values: dict[str, int], est_year: int, prov: Provenance
) -> list[NumberEntry]:
    """추정 순이익 + 주식수 → 선행 EPS·PER.

    당기 실적 배수(PER)는 이미 지나간 이익에 값을 매긴 것이라 시장이 실제로
    보는 숫자와 다르다. 추정이 생겼으니 **선행 배수**를 낼 수 있다.

    주가가 역산 앵커라면 선행 배수도 그 한계를 그대로 물려받는다 — 라벨에
    표시한다.
    """
    ni = est_values.get("net_income")
    if ni is None or not v.shares_issued or v.price is None:
        return []

    eps_fwd = ni / v.shares_issued
    per_fwd = v.price / eps_fwd if eps_fwd else None
    if per_fwd is None or per_fwd <= 0:
        return []

    tag = "역산 " if v.is_implied else ""
    return [
        NumberEntry(
            key=f"eps_{est_year}e",
            value=eps_fwd,
            unit="원",
            display=fmt_per_share(eps_fwd),
            provenance=prov,
            label=f"주당순이익 추정 ({est_year}E)",
            formula="추정 당기순이익 / 발행주식총수",
            inputs=[f"net_income_{est_year}e", f"shares_issued_{v.fiscal_year}a"],
        ),
        NumberEntry(
            key=f"per_{est_year}e",
            value=per_fwd,
            unit="배",
            display=f"{per_fwd:.1f}배",
            provenance=prov,
            label=f"{tag}선행 PER ({est_year}E)",
            formula="주가 / 추정 주당순이익",
            inputs=[f"price_{v.fiscal_year}a", f"eps_{est_year}e"],
        ),
    ]
