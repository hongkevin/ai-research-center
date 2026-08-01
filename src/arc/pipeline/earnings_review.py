"""실적 리뷰 노트 파이프라인 (S1~S6 수직 관통, v0).

    S1 수집        DartProvider.get_financials()
    S3 계산        finmodel.extract_metrics → build_entries → NumberRegistry
    S4 섹션 작성   compose_sections()   ← v0은 결정론 템플릿. LLM은 다음 단계
    S6a 조립       Jinja2 (플레이스홀더 살아 있음)
    S5 검증        G0Gate.check(조립본)
    S6b 치환       NumberRegistry.render_text()

S4가 아직 LLM이 아닌 이유: 게이트·계산·조립이 먼저 검증돼야 LLM 출력의
실패 원인을 분리할 수 있다. 결정론 문장으로 관통을 확인한 뒤 LLM을 끼운다.
그때 바뀌는 것은 compose_sections() 하나뿐이다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from arc.data.base import (
    Company,
    ConsolidationType,
    DataProvider,
    FinancialStatement,
    PeriodType,
)
from arc.data.kr.dart import DartProvider
from arc.data.kr.dart_reports import DartReportProvider, PeriodicReportInfo
from arc.finmodel.metrics import (
    INCOME_STATEMENT_METRICS,
    MetricSet,
    build_entries,
    build_margin_bridge,
    build_observations,
    extract_metrics,
)
from arc.finmodel.valuation import (
    ValuationSet,
    build_valuation,
    build_valuation_entries,
    build_valuation_observations,
)
from arc.llm.number_registry import NumberRegistry
from arc.verify.g0 import G0Gate, GateResult

TEMPLATE_DIR = Path(__file__).resolve().parents[3] / "templates"
TEMPLATE_NAME = "earnings_review.md.j2"


@dataclass
class ReportResult:
    """파이프라인 산출물 전체. 감사에 필요한 중간물을 모두 보관한다."""

    symbol: str
    fiscal_year: int
    company: Company
    statement: FinancialStatement
    metrics: MetricSet
    registry: NumberRegistry
    assembled: str  # 치환 전 조립본 (G0 검사 대상)
    gate: GateResult
    rendered: str | None = None  # 게이트 통과 시에만 채워진다
    bindings: list[dict] = field(default_factory=list)
    narration: object | None = None  # NarrationResult (LLM 사용 시)
    report_info: PeriodicReportInfo | None = None  # 주식수·배당·감사의견·인력
    valuation: ValuationSet | None = None
    info_error: str | None = None  # 주요정보 조회 실패 사유 (조용히 넘기지 않는다)

    @property
    def publishable(self) -> bool:
        return self.gate.passed and self.rendered is not None


# ── S4 섹션 작성 (v0: 결정론) ────────────────────────────────────────
def _ph(key: str) -> str:
    return f"{{{{num:{key}}}}}"


def compose_sections(
    ms: MetricSet,
    registry: NumberRegistry,
    *,
    consolidation: ConsolidationType = ConsolidationType.CONSOLIDATED,
    valuation: ValuationSet | None = None,
    info: PeriodicReportInfo | None = None,
) -> dict[str, object]:
    """지표 → 섹션 본문. **숫자 리터럴을 쓰지 않는다** — 플레이스홀더만 쓴다.

    LLM으로 교체될 자리다. 지금은 계산 결과를 문장으로 옮기기만 한다.
    """
    y = ms.fiscal_year
    has = registry.__contains__
    basis = "연결" if consolidation is ConsolidationType.CONSOLIDATED else "별도"

    def p(key: str) -> str | None:
        return _ph(key) if has(key) else None

    # 요약 — 있는 지표만 문장에 넣는다
    bits: list[str] = []
    if p(f"revenue_{y}a") and p(f"revenue_yoy_{y}a"):
        bits.append(f"매출은 {p(f'revenue_{y}a')}으로 전년 대비 {p(f'revenue_yoy_{y}a')} 변동했다")
    if p(f"operating_income_{y}a") and p(f"operating_margin_{y}a"):
        bits.append(
            f"영업이익은 {p(f'operating_income_{y}a')}, 영업이익률은 {p(f'operating_margin_{y}a')}이다"
        )
    if p(f"operating_margin_chg_{y}a"):
        bits.append(f"영업이익률은 전년 대비 {p(f'operating_margin_chg_{y}a')} 움직였다")
    summary = ". ".join(bits) + "." if bits else "공시 지표가 충분하지 않아 요약을 생성하지 않았다."

    # 투자포인트 — 확인된 지표에서만 만든다
    points: list[dict[str, str]] = []
    bridge = build_margin_bridge(ms)
    if bridge is not None and bridge.reconciled and p(f"bridge_cost_contrib_{y}a"):
        # 브리지가 닫히면 기여도를 직접 말할 수 있다. 추측이 아니라 항등식이다.
        points.append(
            {
                "title": f"이익률 변화는 {bridge.dominant}이 주도했다",
                "body": (
                    f"영업이익률이 {p(f'operating_margin_chg_{y}a')} 움직이는 동안 "
                    f"원가율은 {p(f'bridge_cost_contrib_{y}a')}, "
                    f"판관비율은 {p(f'bridge_sga_contrib_{y}a')}만큼 이익률에 기여했다. "
                    f"두 기여의 합은 이익률 변화와 일치하므로 이번 마진 변화는 "
                    f"{bridge.dominant}에서 설명된다. "
                    "가격 정책·수요·경쟁 강도는 공시 숫자만으로 단정할 수 없어 별도 확인이 필요하다."
                ),
            }
        )
    elif p(f"operating_margin_chg_{y}a") and p(f"cost_ratio_chg_{y}a"):
        points.append(
            {
                "title": "이익률 변화가 원가율에서 왔는지 확인",
                "body": (
                    f"영업이익률이 {p(f'operating_margin_chg_{y}a')} 변하는 동안 "
                    f"원가율은 {p(f'cost_ratio_chg_{y}a')} 움직였다. "
                    "두 방향을 비교하면 이익률 변화가 원가 측면에서 설명되는지, "
                    "판관비 등 다른 요인이 있는지 가를 수 있다. "
                    "가격 정책·수요·경쟁 강도는 공시 숫자만으로 단정할 수 없어 별도 확인이 필요하다."
                ),
            }
        )
    if p(f"revenue_yoy_{y}a") and p(f"operating_income_yoy_{y}a"):
        points.append(
            {
                "title": "외형과 이익의 증가 속도 비교",
                "body": (
                    f"매출은 {p(f'revenue_yoy_{y}a')}, 영업이익은 {p(f'operating_income_yoy_{y}a')} 변동했다. "
                    "이익 증가율이 매출 증가율을 넘어서면 운영 레버리지가 작동한 것이고, "
                    "반대면 비용 구조를 따로 살펴야 한다."
                ),
            }
        )
    if not points:
        points.append(
            {
                "title": "지표 부족",
                "body": "공시에서 확인된 지표가 충분하지 않아 투자포인트를 도출하지 않았다.",
            }
        )

    # 실적 테이블 — **손익 계정만.** 자산·부채·자본이 손익 표에 들어가면 안 된다.
    rows = []
    for key in INCOME_STATEMENT_METRICS:
        mv = ms.values.get(key)
        if mv is None:
            continue
        rows.append(
            {
                "label": mv.label,
                "current": _ph(f"{key}_{y}a") if has(f"{key}_{y}a") else "—",
                "prior": _ph(f"{key}_{y - 1}a") if has(f"{key}_{y - 1}a") else "—",
                "yoy": _ph(f"{key}_yoy_{y}a") if has(f"{key}_yoy_{y}a") else "—",
            }
        )

    narrative = (
        f"위 수치는 모두 {y}년 사업보고서({basis}재무제표)에서 확인된 값이며, "
        f"전기 금액은 같은 공시의 비교표시를 사용했다. "
        "확인되지 않은 계정은 채우지 않고 비워 두었다."
    )
    if consolidation is ConsolidationType.SEPARATE:
        narrative += (
            " 동사는 연결재무제표를 제출하지 않아 별도재무제표를 사용했다. "
            "종속회사 실적이 반영되지 않은 수치다."
        )
    if ms.missing:
        narrative += f" 이번 공시에서 찾지 못한 지표: {', '.join(ms.missing_labels)}."

    return {
        "summary": summary,
        "investment_points": points,
        "earnings": {
            "period_label": f"{y}년",
            "table": rows,
            "narrative": narrative,
        },
        "estimates": {
            "period_label": f"{y + 1}년",
            "assumptions": [
                "추정 모델은 아직 구현되지 않았다 (finmodel 추정 레이어 미착수).",
                "추정치를 임의로 만들지 않는다 — 확인되지 않은 값은 비워 둔다.",
            ],
            "table": [],
            "revision_narrative": (
                "직전 보고서가 없어 추정 변화 추적을 표시하지 않는다. "
                "추정 레이어가 붙으면 이 자리에 전 보고서 대비 변동이 들어간다."
            ),
        },
        "valuation": _valuation_section(y, p, valuation),
        "risks": _risk_lines(valuation, info)
        or ["공시에서 확인된 범위 안에서는 별도로 짚을 회사 리스크가 없다."],
        # 결정론 v0에는 관전 포인트가 없다 — 이건 해석이라 LLM이 채운다.
        "watchpoints": [],
        "method_notes": _method_notes(ms, valuation, info),
    }


def _valuation_section(y: int, p, valuation: ValuationSet | None) -> dict[str, object]:
    """밸류에이션 섹션.

    **본문에서 rating·목표주가를 '언급'조차 하지 않는다.** G0는 부정문을
    구분하지 못하므로("제시하지 않는다"도 걸린다), 정책 문장은 디스클레이머
    섹션에만 둔다.
    """
    empty = {
        "formula": "산출하지 않음 — 주식수·배당 공시를 확인하지 못했다.",
        "bear": {"assumption": "—", "range": "—"},
        "base": {"assumption": "—", "range": "—"},
        "bull": {"assumption": "—", "range": "—"},
        "sensitivity_table": "—",
        "narrative": "밸류에이션 산출에 필요한 공시를 확인하지 못해 이 섹션을 비워 둔다.",
    }
    if valuation is None or not valuation.has_price_anchor:
        return empty

    anchor = "배당수익률에서 역산한 주가" if valuation.is_implied else "종가"
    bits = [f"{anchor}를 기준으로 산출한 참고 배수다."]
    if p(f"per_{y}a"):
        bits.append(f"PER은 {p(f'per_{y}a')}이다.")
    if p(f"pbr_{y}a"):
        bits.append(f"PBR은 {p(f'pbr_{y}a')}이다.")
    if p(f"roe_{y}a") and p(f"pbr_{y}a"):
        bits.append(
            f"자기자본이익률은 {p(f'roe_{y}a')}로, PBR과 함께 보면 "
            "시장이 이 수익성에 어느 정도 값을 매기고 있는지 가늠할 수 있다."
        )
    if p(f"dividend_yield_{y}a") and p(f"payout_ratio_{y}a"):
        bits.append(
            f"현금배당수익률은 {p(f'dividend_yield_{y}a')}, 배당성향은 "
            f"{p(f'payout_ratio_{y}a')}이다."
        )
    if valuation.is_implied:
        bits.append(
            "이 주가는 공시된 주당배당금과 배당수익률에서 역산한 값이라 특정일 종가가 아니다. "
            "시세 시계열이 연결되면 대체한다."
        )

    return {
        "formula": (
            "주가 = 주당현금배당금 / 현금배당수익률 (공시 역산) · "
            "PER = 주가 / 주당순이익 · PBR = 주가 / 주당순자산"
            if valuation.is_implied
            else "PER = 주가 / 주당순이익 · PBR = 주가 / 주당순자산"
        ),
        "bear": {"assumption": "시나리오 미구현 — 추정 레이어 연결 후 산출", "range": "—"},
        "base": {"assumption": "당기 실적 기준 실측 배수", "range": p(f"price_{y}a") or "—"},
        "bull": {"assumption": "시나리오 미구현 — 추정 레이어 연결 후 산출", "range": "—"},
        "sensitivity_table": "—",
        "narrative": " ".join(bits),
    }


def _risk_items(
    valuation: ValuationSet | None, info: PeriodicReportInfo | None
) -> list[tuple[str, str]]:
    """(주제 키워드, 문장) 목록 — **회사의 리스크만.**

    감사 관련 항목의 취급이 갈린다:

    * 비적정 의견·강조사항 → 본문에 쓴다. 재무제표 신뢰성 자체에 관한
      사항이라 실제로 material하고, 증권사 리포트도 이건 다룬다.
    * 적정의견 하의 **KAM은 본문에 인용하지 않는다.** 실제 RA 리포트는
      핵심감사사항을 인용하지 않는다 — "감사인이 ~을 지목했다"가 들어가는
      순간 회계법인 산출물처럼 읽힌다. KAM은 애널리스트에게 주는 단서이므로
      `build_valuation_observations`가 논지로만 넘기고, LLM이 사업 언어로
      옮겨 쓴다("수익인식 기간귀속" → "매출 인식 시점").

    우리 자료의 한계(역산 주가·커버리지)는 `_method_notes`로 분리한다.
    리스크 섹션에 방법론이 섞이면 감사보고서처럼 읽힌다.

    주제 키워드는 LLM 서술과의 중복 제거에 쓴다.
    """
    items: list[tuple[str, str]] = []
    audit = info.audit if info else None
    if audit is not None:
        if not audit.is_clean:
            items.append(
                (
                    "감사의견",
                    (
                        f"감사의견이 적정이 아니다({audit.opinion}). 재무제표 신뢰성 자체에 관한 "
                        "사항이므로 이 노트의 모든 수치를 그 전제 위에서 읽어야 한다."
                    ),
                )
            )
        if audit.emphasis:
            items.append(("강조사항", f"감사보고서 강조사항: {audit.emphasis}."))

    if valuation is not None and valuation.eps_cross_check_ok is False:
        items.append(
            (
                "주당이익",
                (
                    "재무제표 주당이익과 배당 공시 주당순이익이 서로 맞지 않아 "
                    "주당 지표의 신뢰도가 낮다."
                ),
            )
        )
    return items


def _method_notes(
    ms: MetricSet, valuation: ValuationSet | None, info: PeriodicReportInfo | None
) -> list[str]:
    """'작성 기준' 섹션 — **우리 자료의 한계.** 회사 리스크와 섞지 않는다."""
    notes = ["공시 기반 지표만 사용했다. 공시 밖 요인(수요·경쟁·정책)은 반영되지 않았다."]
    if ms.missing:
        notes.append(
            f"이번 공시에서 확인하지 못한 계정: {', '.join(ms.missing_labels)}. "
            "찾지 못한 값은 추정하지 않고 비워 두었다."
        )
    if valuation is not None:
        if valuation.has_price_anchor and valuation.is_implied:
            notes.append(
                "주가는 공시된 주당배당금과 배당수익률에서 역산한 값이며 특정일 종가가 아니다. "
                "시가총액·PER·PBR은 참고치다."
            )
        if valuation.has_preferred and valuation.market_cap is not None:
            notes.append("우선주가 있어 보통주 주가로 계산한 시가총액은 실제와 다를 수 있다.")
        if valuation.shares_issued and not valuation.shares_reconciled:
            notes.append(
                "공시된 발행·자기주식·유통 주식수가 서로 맞지 않아 주식수 기반 지표를 확정하지 못했다."
            )
    notes.append("추정 레이어가 미구현 상태라 이 노트는 실적 확인 목적에 한정된다.")
    if info is not None and info.unavailable:
        notes.append(f"조회하지 못한 공시 항목: {', '.join(info.unavailable)}.")
    return notes


def _risk_lines(valuation: ValuationSet | None, info: PeriodicReportInfo | None) -> list[str]:
    return [text for _, text in _risk_items(valuation, info)]


def merge_risks(
    llm_risks: list[str],
    valuation: ValuationSet | None,
    info: PeriodicReportInfo | None,
) -> list[str]:
    """LLM 리스크 + 결정론 리스크. **같은 주제는 한 번만 싣는다.**

    LLM에게 KAM·역산주가를 논지로 주면 LLM이 그걸 리스크로 쓴다. 거기에
    결정론 문장까지 붙이면 독자는 같은 말을 두 번 읽는다. 결정론 쪽은
    누락 방지용 안전망이므로, LLM이 이미 다뤘으면 뺀다.
    """
    joined = " ".join(llm_risks)
    keep = [text for key, text in _risk_items(valuation, info) if not key or key not in joined]
    merged = [*llm_risks, *keep]
    return merged or ["공시에서 확인된 범위 안에서는 별도로 짚을 회사 리스크가 없다."]


# ── 조립 ─────────────────────────────────────────────────────────────
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        undefined=StrictUndefined,  # 변수 누락을 조용히 넘기지 않는다
        trim_blocks=True,
        lstrip_blocks=True,
    )


def assemble(
    company: Company,
    ms: MetricSet,
    sections: dict,
    *,
    published_at: dt.date,
    valuation: ValuationSet | None = None,
) -> str:
    """Jinja2 조립. 플레이스홀더는 변수 '값'이라 그대로 살아남는다."""
    tpl = _env().get_template(TEMPLATE_NAME)
    y = ms.fiscal_year
    has_price = valuation is not None and valuation.has_price_anchor
    price_label = "역산 주가" if (valuation and valuation.is_implied) else "주가"
    return tpl.render(
        company={
            "name": company.name,
            "symbol": company.symbol,
            "market": company.market.value,
            # DART가 주는 industry는 KSIC 코드(예: "26")다. 코드만 보여주면
            # 독자에게 의미가 없고, 게이트에는 근거 없는 맨 정수로 잡힌다.
            # 코드→업종명 매핑을 붙이기 전까지는 표시하지 않는다.
            "industry": "—",
        },
        report={
            "period_label": f"{ms.fiscal_year}년 연간",
            "published_at": published_at.isoformat(),
            "data_sources": "OpenDART (전자공시시스템)",
            "retrieved_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        },
        header={
            # 헤더도 플레이스홀더로 둔다 — 리터럴을 넣으면 게이트 밖에서
            # 숫자가 새고, 감사 추적(bindings)에도 잡히지 않는다.
            "market_cap": _ph(f"market_cap_{y}a") if has_price else "—",
            "price_date": price_label if has_price else "—",
            "close_price": _ph(f"price_{y}a") if has_price else "—",
        },
        **sections,
    )


# ── 전체 실행 ────────────────────────────────────────────────────────
def fetch_statement(
    symbol: str,
    fiscal_year: int,
    provider: DataProvider,
    *,
    period: PeriodType = PeriodType.ANNUAL,
    consolidation: ConsolidationType | None = None,
) -> FinancialStatement:
    """재무제표 수집. 연결(CFS)이 없으면 별도(OFS)로 자동 폴백한다.

    **소형주 커버리지의 핵심.** 종속회사가 없는 회사는 연결재무제표를 아예
    제출하지 않는다 (DART status 013 "조회된 데이타가 없습니다"). 코스닥
    미커버 종목이 주 타깃이므로 연결 고정은 시장 상당수를 놓친다.

    `consolidation`을 명시하면 폴백하지 않고 그대로 시도한다.
    """
    if consolidation is not None:
        return provider.get_financials(symbol, fiscal_year, period, consolidation)

    last_exc: Exception | None = None
    for cons in (ConsolidationType.CONSOLIDATED, ConsolidationType.SEPARATE):
        try:
            return provider.get_financials(symbol, fiscal_year, period, cons)
        except Exception as exc:  # noqa: BLE001 — 어댑터별 예외 타입이 달라 넓게 잡는다
            # DartError(013 '조회된 데이타가 없습니다') 포함. 다음 구분으로 넘어간다.
            last_exc = exc
    assert last_exc is not None
    raise last_exc


def build_report(
    symbol: str,
    fiscal_year: int,
    provider: DataProvider,
    *,
    period: PeriodType = PeriodType.ANNUAL,
    consolidation: ConsolidationType | None = None,
    published_at: dt.date | None = None,
    llm: object | None = None,
    reports: DartReportProvider | None = None,
) -> ReportResult:
    """S1 → S6b 관통.

    `consolidation`이 None이면 연결→별도 자동 폴백.
    `llm`을 주면 S4 서술을 LLM이 쓰고, 실패하면 결정론 문장으로 폴백한다.
    `reports`가 없고 provider가 DART면 정기보고서 주요정보를 함께 받는다
    (주식수·배당·감사의견·인력 → 밸류에이션과 리스크 근거).
    """
    company = provider.get_company(symbol)
    stmt = fetch_statement(
        symbol, fiscal_year, provider, period=period, consolidation=consolidation
    )

    ms = extract_metrics(stmt)
    registry = NumberRegistry()
    registry.register_all(build_entries(ms, stmt.provenance))

    # 정기보고서 주요정보 — 없어도 실적 노트는 낸다. 다만 조용히 넘기지 않고
    # 실패 사유를 남긴다(커버리지 문제를 숨기면 진단이 불가능해진다).
    if reports is None and isinstance(provider, DartProvider):
        reports = DartReportProvider(provider)
    info: PeriodicReportInfo | None = None
    info_error: str | None = None
    valuation: ValuationSet | None = None
    if reports is not None:
        try:
            info = reports.fetch(symbol, fiscal_year)
        except Exception as exc:  # noqa: BLE001 — 어댑터별 예외 타입이 다르다
            info_error = f"{type(exc).__name__}: {exc}"
        else:
            valuation = build_valuation(ms, info)
            registry.register_all(build_valuation_entries(valuation, info, stmt.provenance))

    sections = compose_sections(
        ms, registry, consolidation=stmt.consolidation, valuation=valuation, info=info
    )

    narration = None
    if llm is not None:
        from arc.llm.narrate import narrate

        basis = "연결" if stmt.consolidation is ConsolidationType.CONSOLIDATED else "별도"
        obs = build_observations(ms, build_margin_bridge(ms))
        if valuation is not None and info is not None:
            obs += build_valuation_observations(valuation, info)
        narration = narrate(
            llm,
            company_name=company.name,
            fiscal_year=fiscal_year,
            basis=basis,
            registry=registry,
            thesis="\n".join(f"- {o}" for o in obs) if obs else None,
        )
        if narration.used_llm:
            # 결정론 골격 위에 LLM 문장만 덮는다. 표·가정·디스클레이머는 그대로.
            n = narration.sections
            sections["summary"] = n["summary"]
            sections["investment_points"] = n["investment_points"]
            sections["earnings"]["narrative"] = (
                n["earnings_narrative"] + " " + sections["earnings"]["narrative"]
            )
            sections["risks"] = merge_risks(list(n["risks"]), valuation, info)
            sections["watchpoints"] = list(n.get("watchpoints") or [])
    assembled = assemble(
        company,
        ms,
        sections,
        published_at=published_at or dt.datetime.now(dt.UTC).date(),
        valuation=valuation,
    )

    gate = G0Gate(registry).check(assembled)
    rendered = registry.render_text(assembled) if gate.passed else None
    bindings = registry.bindings(assembled) if gate.passed else []

    return ReportResult(
        symbol=symbol,
        fiscal_year=fiscal_year,
        company=company,
        statement=stmt,
        metrics=ms,
        registry=registry,
        assembled=assembled,
        gate=gate,
        rendered=rendered,
        bindings=bindings,
        narration=narration,
        report_info=info,
        valuation=valuation,
        info_error=info_error,
    )
