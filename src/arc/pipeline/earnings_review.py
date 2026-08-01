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
from arc.finmodel.metrics import (
    MetricSet,
    build_entries,
    build_margin_bridge,
    build_observations,
    extract_metrics,
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

    # 실적 테이블 — 확인된 지표만
    rows = []
    for key, mv in ms.values.items():
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
        narrative += f" 이번 공시에서 찾지 못한 지표: {', '.join(ms.missing)}."

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
        "valuation": {
            "formula": "미구현 — 시세·밴드 데이터 연결 후 산출한다.",
            "bear": {"assumption": "—", "range": "—"},
            "base": {"assumption": "—", "range": "—"},
            "bull": {"assumption": "—", "range": "—"},
            "sensitivity_table": "—",
            # 본문에서 rating·목표주가를 '언급'조차 하지 않는다. G0는 부정문을
            # 구분하지 못하므로("제시하지 않는다"도 걸린다), 정책 문장은
            # 디스클레이머 섹션에만 둔다.
            "narrative": (
                "밸류에이션은 시세 시계열이 연결된 뒤에 산출한다. "
                "산출 시에는 시나리오별 적정가치 범위와 산식을 함께 공개한다."
            ),
        },
        "risks": [
            "공시 기반 지표만 사용했으므로 공시 밖 요인(수요·경쟁·정책)은 반영되지 않았다.",
            "추정과 밸류에이션이 미구현 상태라 이 노트는 실적 확인 목적에 한정된다.",
        ],
    }


# ── 조립 ─────────────────────────────────────────────────────────────
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        undefined=StrictUndefined,  # 변수 누락을 조용히 넘기지 않는다
        trim_blocks=True,
        lstrip_blocks=True,
    )


def assemble(company: Company, ms: MetricSet, sections: dict, *, published_at: dt.date) -> str:
    """Jinja2 조립. 플레이스홀더는 변수 '값'이라 그대로 살아남는다."""
    tpl = _env().get_template(TEMPLATE_NAME)
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
            "market_cap": "—",  # 시세 어댑터 연결 후 채운다
            "price_date": "—",
            "close_price": "—",
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
) -> ReportResult:
    """S1 → S6b 관통.

    `consolidation`이 None이면 연결→별도 자동 폴백.
    `llm`을 주면 S4 서술을 LLM이 쓰고, 실패하면 결정론 문장으로 폴백한다.
    """
    company = provider.get_company(symbol)
    stmt = fetch_statement(
        symbol, fiscal_year, provider, period=period, consolidation=consolidation
    )

    ms = extract_metrics(stmt)
    registry = NumberRegistry()
    registry.register_all(build_entries(ms, stmt.provenance))

    sections = compose_sections(ms, registry, consolidation=stmt.consolidation)

    narration = None
    if llm is not None:
        from arc.llm.narrate import narrate

        basis = "연결" if stmt.consolidation is ConsolidationType.CONSOLIDATED else "별도"
        obs = build_observations(ms, build_margin_bridge(ms))
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
            sections["risks"] = list(n["risks"]) + list(sections["risks"])
    assembled = assemble(
        company, ms, sections, published_at=published_at or dt.datetime.now(dt.UTC).date()
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
    )
