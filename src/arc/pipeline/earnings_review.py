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
import os
from collections.abc import Callable
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
from arc.data.kr.dart_document import (
    fetch_document,
    find_section,
    find_sections,
    section_provenance,
)
from arc.data.kr.dart_reports import DartReportProvider, PeriodicReportInfo
from arc.finmodel.business import (
    BusinessProfile,
    build_business_entries,
    build_business_observations,
    build_business_profile,
)
from arc.finmodel.estimates import (
    ESTIMATE_DATASET,
    EstimateSet,
    Revision,
    build_estimate_entries,
    build_estimate_observations,
    build_estimates,
    compare_estimates,
    from_rows,
    to_rows,
)
from arc.finmodel.lenses import (
    LensSet,
    build_lens_entries,
    build_lens_observations,
    build_lenses,
)
from arc.finmodel.metrics import (
    INCOME_STATEMENT_METRICS,
    MetricSet,
    build_entries,
    build_margin_bridge,
    build_observations,
    extract_metrics,
)
from arc.finmodel.segment_profit import (
    SegmentProfitSet,
    build_segment_profit,
    build_segment_profit_entries,
    build_segment_profit_observations,
)
from arc.finmodel.segments import (
    SegmentBreakdown,
    build_segment_entries,
    build_segment_observations,
    build_segments,
)
from arc.finmodel.valuation import (
    ValuationSet,
    build_forward_entries,
    build_valuation,
    build_valuation_entries,
    build_valuation_observations,
)
from arc.llm.number_registry import NumberRegistry
from arc.verify.g0 import G0Gate, GateResult

TEMPLATE_NAME = "earnings_review.md.j2"


def _template_dir() -> Path:
    """리포트 템플릿 위치.

    개발에서는 저장소 루트의 `templates/`지만, **설치된 패키지에서는 그 경로가
    존재하지 않는다.** wheel은 `src/arc/**`만 담으므로 `parents[3]`가
    site-packages 밖을 가리킨다(실측: wheel 42개 파일에 `.j2`가 없었다).

    순서대로 찾는다:
      1. `ARC_TEMPLATE_DIR` — 배포에서 명시 (Dockerfile이 설정한다)
      2. 저장소 루트 — 개발·editable 설치
      3. 작업 디렉터리 — 컨테이너에서 WORKDIR에 복사된 경우
    """
    env = os.environ.get("ARC_TEMPLATE_DIR")
    candidates = [Path(env)] if env else []
    candidates += [
        Path(__file__).resolve().parents[3] / "templates",
        Path.cwd() / "templates",
    ]
    for path in candidates:
        if (path / TEMPLATE_NAME).is_file():
            return path
    # 못 찾으면 첫 후보를 돌려준다 — Jinja2가 명확한 오류를 낸다
    return candidates[0]


TEMPLATE_DIR = _template_dir()


@dataclass
class StageReport:
    """단계 하나가 무엇을 했는지.

    파이프라인을 **열기 위한** 기록이다. 지금까지 이 시스템은 종목코드를 넣으면
    30초 뒤 완성본을 뱉는 블랙박스였고, 중간에 무엇을 검산했고 무엇을 못 구했는지
    화면에 나오지 않았다. 산출물마다 자기 진단 필드(`reconciled`, `unavailable`,
    `note` …)를 이미 들고 있었는데 쓰이지 않았다.

    **`absent`와 `failed`를 반드시 구분한다.** SK하이닉스에 부문 손익이 없는 것은
    정상이고(단일 부문 — D33이 정확히 거부한다), DART 조회 실패는 결함이다. 둘을
    같은 색으로 칠하면 검토자가 정상을 결함으로 읽는다.
    """

    key: str
    label: str
    status: str = "ok"  # "ok" | "partial" | "absent" | "failed"
    summary: str = ""
    checks: list[dict] = field(default_factory=list)  # {"label", "value", "ok"}
    registered: int = 0  # 이 단계가 레지스트리에 넣은 건수
    note: str = ""  # 비었다면 왜


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:+.4f}%"


def _fill_segment_profit_stage(st: StageReport, sp: SegmentProfitSet | None) -> None:
    """부문별 손익 (D33).

    **없는 것이 정상인 경우를 결함으로 표시하지 않는다.** `usable`은
    `reconciled and len(lines) >= 2`이므로 단일 부문 회사는 정상적으로 못 쓴다 —
    SK하이닉스가 그 자리다(HANDOFF 검증 표).
    """
    if sp is None or not sp.lines:
        st.status = "absent"
        st.summary = "부문 손익 없음"
        st.note = (sp.note if sp else "") or "영업부문 주석이 없습니다 — 단일 부문이면 정상입니다."
        return
    st.checks = [
        {
            "label": "주석 총계 vs 손익계산서 매출액",
            "value": _pct(sp.revenue_gap_pct),
            "ok": sp.reconciled,
        },
        {
            "label": "주석 총계 vs 손익계산서 영업이익",
            "value": _pct(sp.op_gap_pct),
            "ok": sp.reconciled,
        },
    ]
    if sp.section_title:
        st.checks.append({"label": "출처 섹션", "value": sp.section_title, "ok": True})
    if sp.usable:
        st.summary = f"부문 {len(sp.lines)}개" + ("" if sp.has_prior else " · 전기 없음")
        if not sp.has_prior:
            st.status = "partial"
            st.note = "전기 비교표가 없어 증감을 낼 수 없습니다."
    elif sp.reconciled:
        # 검산은 닫혔는데 부문이 하나 — 전사 손익과 같은 말이라 싣지 않는다
        st.status = "absent"
        st.summary = "단일 부문"
        st.note = "부문이 하나라 전사 손익과 같습니다. 표를 더 싣지 않습니다."
    else:
        st.status = "partial"
        st.summary = f"부문 {len(sp.lines)}개 · 검산 불일치"
        st.note = sp.note or "총계 열이 손익계산서와 맞지 않아 쓰지 않습니다."


def _fill_segments_stage(
    st: StageReport, seg: SegmentBreakdown | None, section: str | None
) -> None:
    """부문별 매출 (D28) — 원문 표에서 뽑고 매출액으로 검산한다."""
    if seg is None or not seg.lines:
        st.status = "absent"
        st.summary = "부문 매출 없음"
        st.note = (seg.note if seg else "") or "매출 현황 표를 찾지 못했습니다."
        return
    st.checks = [
        {
            "label": "부문 합계 vs 손익계산서 매출액",
            "value": _pct(seg.gap_pct),
            "ok": seg.reconciled,
        },
        {
            "label": "비중 합",
            "value": "—" if seg.share_sum is None else f"{seg.share_sum:.1f}%",
            "ok": seg.share_sum is None or abs(seg.share_sum - 100) < 1.0,
        },
    ]
    if section:
        st.checks.append({"label": "출처 섹션", "value": section, "ok": True})
    if seg.usable:
        st.summary = f"부문 {len(seg.lines)}개"
    else:
        st.status = "partial"
        st.summary = f"부문 {len(seg.lines)}개 · 검산 불일치"
        st.note = seg.note or "합계가 매출액과 맞지 않아 쓰지 않습니다."


def _fill_business_stage(st: StageReport, biz: BusinessProfile | None) -> None:
    """사업 이해 (D29) — 리포트가 손익계산서에서 출발하지 않게 하는 자리."""
    if biz is None:
        st.status = "absent"
        st.summary = "사업 개요 없음"
        return
    if biz.note:
        st.status = "absent"
        st.summary = "사업 개요 없음"
        st.note = biz.note
        return
    bits = []
    if biz.signals:
        bits.append(f"축 {len(biz.signals)}개")
    if biz.ownership is not None:
        bits.append("지분")
    if biz.affiliates is not None:
        bits.append("출자")
    st.summary = " · ".join(bits) or "사업 개요"
    if biz.source_title:
        st.checks = [{"label": "출처 섹션", "value": biz.source_title, "ok": True}]


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
    estimates: EstimateSet | None = None
    revisions: list[Revision] = field(default_factory=list)
    segments: SegmentBreakdown | None = None
    segment_profit: SegmentProfitSet | None = None
    lenses: LensSet | None = None
    business: BusinessProfile | None = None
    info_error: str | None = None  # 주요정보 조회 실패 사유 (조용히 넘기지 않는다)
    stages: list[StageReport] = field(default_factory=list)  # 단계별 기록

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
    estimates: EstimateSet | None = None,
    revisions: list[Revision] | None = None,
    segments: SegmentBreakdown | None = None,
    segment_profit: SegmentProfitSet | None = None,
    lenses: LensSet | None = None,
    business: BusinessProfile | None = None,
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
    if segment_profit is not None and segment_profit.usable and segment_profit.leaders_differ:
        # 부문 손익이 공시된 회사에서만 할 수 있는 주장이다 — 전사 지표에는
        # 이 사실이 아예 나타나지 않는다.
        rl, pl = segment_profit.revenue_leader, segment_profit.profit_leader
        points.append(
            {
                "title": f"외형은 {rl.name}이 끌지만 이익은 {pl.name}에서 나온다",
                "body": (
                    f"매출 비중이 가장 큰 부문은 {rl.name}이나, 영업이익이 가장 큰 부문은 "
                    f"{pl.name}이다. {pl.name}의 영업이익률은 {p(f'{_opseg_key(segment_profit, pl)}_margin_{y}a')}로 "
                    f"{rl.name}의 {p(f'{_opseg_key(segment_profit, rl)}_margin_{y}a')}와 다르다. "
                    f"전사 이익률은 {pl.name}의 흐름과 부문 구성비에 좌우되므로, "
                    f"{rl.name}의 외형만 보고 이익 방향을 읽으면 어긋난다. "
                    f"다만 {pl.name}의 이익률이 업황을 타는 것이라면 이 구도는 유지되지 않는다. "
                    "다음 공시에서 부문별 이익률의 방향을 먼저 확인해야 한다."
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

    # 부문 구분을 **한 리포트에 하나만** 싣는다. 영업부문 주석(D33)이 있으면
    # 「매출 및 수주상황」 표(D28)는 내리고 4.1만 남긴다. 실측: 삼성전자는 앞이
    # 제ㆍ상품/용역 2개, 뒤가 DX·DS·SDC·Harman 4개라 같은 문서에서 「부문」이
    # 두 뜻이 됐다. D28은 주석이 없는 회사에서 계속 쓰인다.
    has_operating_segments = segment_profit is not None and segment_profit.usable

    return {
        "summary": summary,
        "investment_points": points,
        "earnings": {
            "period_label": f"{y}년",
            "table": rows,
            "segment_table": _segment_rows(y, p, segments),
            "segment_profit": _segment_profit_section(y, p, segment_profit),
            "narrative": narrative,
        },
        "estimates": _estimates_section(y, p, estimates, revisions or []),
        "business": _business_section(
            y, p, business, None if has_operating_segments else segments, info
        ),
        # LLM이 있으면 덮어쓴다. 없으면 원문을 그대로 싣지 않고 사실만 남긴다 —
        # 공시 문체가 리포트에 섞이면 장르가 깨진다.
        "business_narrative": (
            "회사가 공시한 사업 서술을 확인했다. 서술 요약은 LLM 서술 레이어가 담당한다."
            if business is not None and business.usable
            else "사업 서술을 확인하지 못했다."
        ),
        "valuation": _valuation_section(y, p, valuation, estimates),
        "risks": _risk_lines(valuation, info)
        or ["공시에서 확인된 범위 안에서는 별도로 짚을 회사 리스크가 없다."],
        "lenses": _lens_section(lenses, p),
        # 관전 포인트는 **렌즈가 갈리는 지점**이다 (D35). LLM이 있으면 덮어쓰지만,
        # 없어도 비어 있지 않다 — D22가 이 섹션을 만든 이유가 여기서 채워진다.
        "watchpoints": [t.text for t in lenses.tensions] if lenses else [],
        # 산업 배경도 LLM 전용 레인이다 (미검증). 없으면 섹션이 통째로 빠진다.
        "industry_context": "",
        "method_notes": _method_notes(ms, valuation, info, estimates),
    }


def _estimates_section(
    y: int, p, est: EstimateSet | None, revisions: list[Revision]
) -> dict[str, object]:
    """추정 섹션.

    **모든 추정치는 가정의 함수다.** 가정을 먼저 보여주고 값을 뒤에 둔다 —
    순서가 반대면 독자가 숫자를 전망으로 읽는다.
    """
    if est is None or not est.usable:
        reasons = est.warnings if est else []
        return {
            "period_label": f"{y + 1}년",
            "assumptions": reasons or ["추정에 필요한 지표를 확인하지 못해 산출하지 않았다."],
            "table": [],
            "revision_narrative": "추정을 산출하지 않아 변화 추적도 표시하지 않는다.",
        }

    ey = est.fiscal_year
    # `Assumption.describe()`는 진단용이다 — 리터럴 숫자가 들어 있어 본문에
    # 쓸 수 없다(G0가 잡는다). 가정도 수치이므로 플레이스홀더로 쓴다.
    lines = []
    for a in est.assumptions:
        ph = p(f"assume_{a.key}_{ey}e")
        if ph is None:
            continue
        tag = " (사용자 입력)" if a.is_override else ""
        lines.append(f"{a.label}: {ph} — {a.basis}{tag}")
    lines.append(
        f"산출 방식: {est.method}. 과거 실적에 위 가정을 적용한 **기준선**이며 전망이 아니다."
    )
    lines.extend(est.warnings)

    by_metric = {r.metric: r for r in revisions}
    rows = []
    for metric, label in (
        ("revenue", "매출액"),
        ("operating_income", "영업이익"),
        ("net_income", "당기순이익"),
    ):
        if not p(f"{metric}_{ey}e"):
            continue
        r = by_metric.get(metric)
        rows.append(
            {
                "label": label,
                "current": p(f"{metric}_{ey}e") or "—",
                "previous": p(f"{metric}_prev_{ey}e") or "—",
                "delta": (p(f"{metric}_revision_{ey}e") or "—") if r else "—",
            }
        )

    if not revisions:
        narrative = (
            "직전 보고서가 없어 추정 변화를 표시하지 않는다. "
            "다음 발간부터 이 자리에 전 보고서 대비 변동과 방향이 들어간다."
        )
    else:
        moved = [r for r in revisions if r.direction != "유지"]
        if moved:
            parts = [f"{r.label} {r.direction}" for r in moved]
            narrative = (
                f"직전 보고서 대비 {', '.join(parts)}. "
                "조정 방향과 시점은 추정치 자체만큼 중요한 기록이다."
            )
        else:
            narrative = "직전 보고서 대비 추정에 의미 있는 변화가 없다."

    return {
        "period_label": f"{ey}년",
        "assumptions": lines,
        "table": rows,
        "revision_narrative": narrative,
    }


def _segment_rows(y: int, p, seg: SegmentBreakdown | None) -> list[dict[str, str]]:
    """부문별 매출 표. 검산에 실패한 부문 구성은 **표시하지 않는다.**"""
    if seg is None or not seg.usable:
        return []
    out = []
    for i in range(len(seg.lines)):
        amount = p(f"segment{i + 1}_revenue_{y}a")
        if amount is None:
            continue
        out.append(
            {
                "label": seg.lines[i].name,
                "amount": amount,
                "share": p(f"segment{i + 1}_share_{y}a") or "—",
            }
        )
    return out


def _lens_section(lenses: LensSet | None, p) -> dict[str, object]:
    """관점별 해석. **근거가 없는 렌즈는 침묵하고 그 사실을 적는다.**

    억지로 말하게 하면 이 제품이 피하려는 것 그 자체가 된다 — 섹션을 채우려고
    확인되지 않은 판단을 쓰는 것.
    """
    if lenses is None:
        return {"views": [], "tensions": []}
    # 렌즈가 전부 침묵해도 **섹션은 낸다.** 통째로 사라지면 검토자는 "이 회사엔
    # 볼 관점이 없구나"로 읽는다. 무엇을 못 봤는지 적는 편이 정직하다
    # (`_business_section`과 같은 원칙).
    #
    # 주된 발견·단서·다음에 볼 것을 **나눠서** 넘긴다. 평평한 목록으로 내면
    # 부차적 관찰이 렌즈의 결론처럼 읽힌다 (LG전자에서 실제로 그랬다).
    views = []
    for v in lenses.views:
        head = v.headline
        caveats = v.caveats
        rest = [r for r in v.ordered if r is not head and r not in caveats]
        # 본문은 `report` 템플릿의 슬롯을 플레이스홀더로 채운다 — 그래야 회사마다
        # 다른 글이 된다. 프롬프트로 가는 `claim`은 크기 없는 그대로 둔다.
        views.append(
            {
                "label": v.label,
                "question": v.question,
                "headline": head.report_text(p) if head else "",
                "caveats": [r.report_text(p) for r in caveats],
                "readings": [r.report_text(p) for r in rest],
                "watch": v.watch,
                # 통째로 침묵한 렌즈는 사유 한 줄이면 된다 — 답하지 못한 단계를
                # 또 나열하면 같은 말이 두 번 실린다.
                "unanswered": list(v.unanswered) if v.usable else [],
                "note": v.silent_reason,
            }
        )
    return {"views": views, "tensions": [t.text for t in lenses.tensions]}


def _segment_names(sp: SegmentProfitSet | None, seg: SegmentBreakdown | None) -> list[str]:
    """산업 서사 레인에 넘길 부문명. 영업부문 주석이 있으면 그쪽이 정본이다."""
    if sp is not None and sp.usable:
        return [x.name for x in sp.lines]
    return [x.name for x in seg.lines] if seg is not None and seg.usable else []


def _opseg_key(sp: SegmentProfitSet, line) -> str:
    """부문 → 레지스트리 키 접두사. 순번이 키를 정하므로 목록에서 찾는다."""
    return f"opseg{sp.lines.index(line) + 1}"


def _segment_profit_section(y: int, p, sp: SegmentProfitSet | None) -> dict[str, object]:
    """부문별 수익성 표. **검산이 닫히지 않으면 표를 내지 않는다.**

    표를 못 내는 이유는 남긴다 — 단일 영업부문이라 공시가 없는 것과 파싱이
    깨진 것은 완전히 다른 사실이고, 섹션이 통째로 사라지면 구분되지 않는다.
    """
    out: dict[str, object] = {"table": [], "assets": [], "note": "", "has_prior": False}
    if sp is None:
        return out
    if not sp.usable:
        out["note"] = sp.note
        return out
    out["has_prior"] = sp.has_prior
    rows = []
    assets = []
    for line in sp.lines:
        base = _opseg_key(sp, line)
        rows.append(
            {
                "label": line.name,
                "revenue": p(f"{base}_revenue_{y}a") or "—",
                "rev_share": p(f"{base}_rev_share_{y}a") or "—",
                "op": p(f"{base}_op_{y}a") or "—",
                "margin": p(f"{base}_margin_{y}a") or "—",
                "ebitda_margin": p(f"{base}_ebitda_margin_{y}a") or "—",
                "margin_chg": p(f"{base}_margin_chg_{y}a") or "—",
            }
        )
        # 부문 자산은 공시하는 회사만 있다 (경영위원회에 정기 제공될 때만
        # 기준서가 요구한다). 없으면 표 자체를 내지 않는다.
        if p(f"{base}_assets_{y}a"):
            assets.append(
                {
                    "label": line.name,
                    "assets": p(f"{base}_assets_{y}a"),
                    "asset_return": p(f"{base}_asset_return_{y}a") or "—",
                }
            )
    out["table"] = rows
    out["assets"] = assets
    return out


def _business_section(
    y: int,
    p,
    profile: BusinessProfile | None,
    seg: SegmentBreakdown | None,
    info: PeriodicReportInfo | None,
) -> dict[str, object]:
    """「사업 이해」 섹션 — 이 노트가 재무 기계학에서 벗어나는 자리.

    **항상 렌더한다.** 원문 조회가 실패해도 섹션은 남기고 그 사실을 적는다.
    섹션이 통째로 사라지면 독자는 "이 회사는 사업 설명이 없구나"로 읽는다.
    """
    out: dict[str, object] = {
        "overview": "",
        "signals": [],
        "segment_table": [],
        "ownership": "",
        "affiliates": [],
        "note": "",
    }

    if profile is not None and profile.usable:
        # 원문을 그대로 싣지 않는다 — 공시 문체가 리포트에 섞이면 장르가 깨진다.
        # LLM이 논지로 받아 다시 쓰고, 여기에는 확인된 축만 남긴다.
        out["signals"] = profile.signals
    elif profile is not None:
        out["note"] = profile.note
    else:
        out["note"] = "사업보고서 원문을 조회하지 못해 사업 구조를 확인하지 못했다."

    if seg is not None and seg.usable:
        rows = []
        for i, line in enumerate(seg.lines):
            amount = p(f"segment{i + 1}_revenue_{y}a")
            if amount is None:
                continue
            rows.append(
                {
                    "label": line.name,
                    "amount": amount,
                    "share": p(f"segment{i + 1}_share_{y}a") or "—",
                    "yoy": p(f"segment{i + 1}_yoy_{y}a") or "—",
                }
            )
        out["segment_table"] = rows

    own = info.ownership if info else None
    if own is not None and own.principal:
        stake = p(f"owner_stake_{y}a")
        total = p(f"owner_total_stake_{y}a")
        bits = [f"최대주주는 {own.principal}이다."]
        if stake and total:
            bits.append(f"본인 지분은 {stake}, 특수관계인을 포함하면 {total}이다.")
        if own.is_owner_controlled:
            bits.append("지배주주가 의사결정을 좌우할 수 있는 수준이다.")
        out["ownership"] = " ".join(bits)

    aff = info.affiliates if info else None
    if aff is not None:
        rows = []
        for i, e in enumerate(x for x in aff.top(5) if x.is_operating):
            rows.append(
                {
                    "name": e.name,
                    "purpose": e.purpose,
                    "stake": p(f"affiliate{i + 1}_stake_{y}a") or "—",
                }
            )
        out["affiliates"] = rows
    return out


def _valuation_section(
    y: int, p, valuation: ValuationSet | None, est: EstimateSet | None = None
) -> dict[str, object]:
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
    ey = est.fiscal_year if est is not None else None
    if ey and p(f"per_{ey}e"):
        bits.append(
            f"{ey}년 추정 이익 기준 선행 PER은 {p(f'per_{ey}e')}이다. "
            "추정이 가정에 종속되므로 이 배수도 같은 가정 위에 있다."
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
        # 시나리오는 **가정 범위**가 있어야 만들 수 있다. 지금은 단일 가정
        # 집합뿐이므로 만들지 않는다 — 없는 시나리오를 지어내지 않는다.
        "bear": {"assumption": "가정 범위 미지정 — 보수 시나리오를 만들지 않았다", "range": "—"},
        "base": {
            "assumption": (
                f"{ey}년 추정 가정 기준" if ey and p(f"per_{ey}e") else "당기 실적 기준 실측 배수"
            ),
            "range": p(f"price_{y}a") or "—",
        },
        "bull": {"assumption": "가정 범위 미지정 — 낙관 시나리오를 만들지 않았다", "range": "—"},
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
    ms: MetricSet,
    valuation: ValuationSet | None,
    info: PeriodicReportInfo | None,
    estimates: EstimateSet | None = None,
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
    if estimates is None or not estimates.usable:
        notes.append("추정을 산출하지 못해 이 노트는 실적 확인 목적에 한정된다.")
    else:
        notes.append(
            f"{estimates.fiscal_year}년 추정은 {estimates.base_year}년 실적에 명시된 가정을 "
            "적용해 계산한 기준선이며, 산업·정책 등 공시 밖 요인은 반영되지 않았다."
        )
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
    registry: NumberRegistry | None = None,
) -> str:
    """Jinja2 조립. 플레이스홀더는 변수 '값'이라 그대로 살아남는다.

    **두 번 렌더한다.** 「수치 출처」 표는 *본문에 실제로 등장한* 키만 실어야
    하는데, 그 표 자체가 본문의 일부라 순환이 생긴다. 1차로 표 없이 렌더해
    등장 키를 뽑고, 2차에서 표를 채운다. 템플릿 렌더는 비용이 없다.
    """
    tpl = _env().get_template(TEMPLATE_NAME)
    y = ms.fiscal_year
    has_price = valuation is not None and valuation.has_price_anchor
    price_label = "역산 주가" if (valuation and valuation.is_implied) else "주가"

    def render(sources: list[dict[str, str]]) -> str:
        return tpl.render(
            sources=sources,
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

    body = render(sources=[])
    if registry is None:
        return body
    return render(sources=_source_rows(body, registry))


def _source_rows(body: str, registry: NumberRegistry) -> list[dict[str, str]]:
    """본문에 등장한 수치의 출처표.

    **RA는 출처에 민감하다.** 웹 화면에서는 숫자를 클릭해 확인할 수 있지만
    (D26), 마크다운 파일로 받으면 그 정보가 통째로 사라진다. 발간물이 파일인
    이상 파일 안에 검증 경로가 있어야 한다.

    값은 **플레이스홀더로 넣는다** — 본문과 같은 레지스트리를 거치므로 표와
    본문의 숫자가 갈라질 수 없고, G0도 그대로 통과한다.
    """
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for key in NumberRegistry.extract_keys(body):
        if key in seen:
            continue
        seen.add(key)
        entry = registry._entries.get(key)
        if entry is None or entry.internal:
            continue
        prov = entry.provenance
        doc = (prov.source_ref or "") if prov else ""
        link = (prov.verify_url or "") if prov else ""
        rows.append(
            {
                "label": entry.label or key,
                "value": _ph(key),
                # 산식의 파이프(`|전기|`)를 그대로 두면 **마크다운 표의 셀
                # 구분자로 해석돼** 그 뒤 열이 통째로 밀린다 (이전에 한 번 밟았다).
                "formula": (entry.formula or "—").replace("|", "\\|"),
                "source": prov.describe if prov else "—",
                # 공시번호를 링크로 건다. 검토자가 클릭 한 번에 원문으로 간다.
                "document": f"[{doc}]({link})" if doc and link else (doc or "—"),
            }
        )
    return rows


# ── 추정 이력 (point-in-time) ────────────────────────────────────────
def _load_prior_estimates(
    store: object | None, symbol: str, fiscal_year: int, published_at: dt.date | None
) -> EstimateSet | None:
    """직전 **발간** 시점의 추정을 읽는다.

    경계는 발간일 **끝**(포함)이다. 같은 날 발간한 뒤 가정을 고쳐 다시
    생성하면 그 발간분과 비교돼야 한다.

    자기 자신과 비교할 위험은 없다 — 저장은 **명시적 발간에서만** 일어나고
    (`save_estimates`), 그 시점에 `build_report`는 이미 끝나 있다. 생성할
    때마다 저장했다면 가정을 만지작거린 흔적이 전부 "직전 추정"이 되어
    이력이 무의미해진다.

    저장소가 없거나 이력이 없으면 None — 없는 걸 만들지 않는다.
    """
    if store is None:
        return None
    day = published_at or dt.datetime.now(dt.UTC).date()
    as_of = dt.datetime.combine(day, dt.time.max, tzinfo=dt.UTC)
    try:
        rows = store.read_as_of(ESTIMATE_DATASET, as_of)
    except Exception:  # noqa: BLE001 — 이력이 없어도 발간은 막지 않는다
        return None
    return from_rows(rows, symbol, fiscal_year)


def save_estimates(store: object, est: EstimateSet, symbol: str, published_at: dt.date) -> None:
    """이번 추정을 스냅샷으로 남긴다 — 다음 발간의 revision 기준이 된다.

    스냅샷 시각을 **발간일**로 찍는다. 실행 시각(wall clock)으로 찍으면
    "그 시점에 무엇을 추정했는가"라는 point-in-time 질문에 답할 수 없고,
    발간일 기준 as-of 조회가 어긋난다(실측으로 확인).
    """
    rows = to_rows(est, symbol, published_at)
    if rows:
        store.save_snapshot(
            ESTIMATE_DATASET,
            rows,
            snapshot_at=dt.datetime.combine(published_at, dt.time.min, tzinfo=dt.UTC),
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
    store: object | None = None,
    assumptions: dict[str, float] | None = None,
    # 2년차 이후의 연차별 가정. **기계가 늘리지 않는다** — 사람이 넣은 만큼만
    # 낸다 (D34: 1년차 영업이익 오차가 이미 중앙값 55.9%다).
    forward: list[dict[str, float]] | None = None,
    with_segments: bool = True,
    on_progress: Callable[[str, str], None] | None = None,
) -> ReportResult:
    """S1 → S6b 관통.

    `consolidation`이 None이면 연결→별도 자동 폴백.
    `llm`을 주면 S4 서술을 LLM이 쓰고, 실패하면 결정론 문장으로 폴백한다.
    `reports`가 없고 provider가 DART면 정기보고서 주요정보를 함께 받는다
    (주식수·배당·감사의견·인력 → 밸류에이션과 리스크 근거).

    `on_progress(단계키, 문구)`는 진행 상황을 알린다. LLM까지 켜면 30~40초가
    걸리는데 아무 반응이 없으면 사용자가 멈춘 줄 알고 다시 누른다. 콜백을
    쓰는 쪽(웹)이 이걸 스트리밍한다.
    """

    stages: list[StageReport] = []

    def step(key: str, message: str, label: str | None = None) -> StageReport:
        """진행 상황을 알리고 이 단계의 기록을 연다.

        `on_progress`는 **작업 전에** 쏜다 — 기다리는 동안 보여야 하기 때문이다.
        `StageReport`는 작업 **후에** 채운다. 결과를 알아야 상태를 정할 수 있다.
        기록은 읽기만 한다 — 기존 계산 순서에 개입하지 않는다.
        """
        if on_progress is not None:
            on_progress(key, message)
        s = StageReport(key=key, label=label or message)
        stages.append(s)
        return s

    st = step("company", "회사 정보 조회")
    company = provider.get_company(symbol)
    st.summary = f"{company.name} · {company.market.value}"

    st = step("statement", "재무제표 수집")
    stmt = fetch_statement(
        symbol, fiscal_year, provider, period=period, consolidation=consolidation
    )
    basis_label = "연결" if stmt.consolidation is ConsolidationType.CONSOLIDATED else "별도"
    st.summary = f"{basis_label}재무제표 · {stmt.fiscal_year}"
    # 커버리지가 다르면 낮은 쪽으로 폴백한다(D20). 폴백했다는 사실 자체가
    # 검토자에게 필요한 정보다 — 소형주는 연결이 아예 없다.
    st.checks = [
        {"label": "연결/별도", "value": basis_label, "ok": True},
        {"label": "접수번호", "value": stmt.rcept_no or "없음", "ok": bool(stmt.rcept_no)},
    ]
    if not stmt.rcept_no:
        st.status = "partial"
        st.note = "접수번호가 없어 사업보고서 원문을 열 수 없습니다."

    st = step("metrics", "지표 추출·계산")
    ms = extract_metrics(stmt)
    registry = NumberRegistry()
    registry.register_all(build_entries(ms, stmt.provenance))
    st.registered = len(registry)
    st.summary = f"지표 {len(ms.values)}종"
    if ms.missing_labels:
        st.status = "partial"
        st.note = f"미확인 계정: {', '.join(ms.missing_labels)}"

    # 정기보고서 주요정보 — 없어도 실적 노트는 낸다. 다만 조용히 넘기지 않고
    # 실패 사유를 남긴다(커버리지 문제를 숨기면 진단이 불가능해진다).
    if reports is None and isinstance(provider, DartProvider):
        reports = DartReportProvider(provider)
    info: PeriodicReportInfo | None = None
    info_error: str | None = None
    valuation: ValuationSet | None = None
    if reports is not None:
        st = step("reports", "정기보고서 주요정보 (주식수·배당·감사의견)")
        before = len(registry)
        try:
            info = reports.fetch(symbol, fiscal_year)
        except Exception as exc:  # noqa: BLE001 — 어댑터별 예외 타입이 다르다
            info_error = f"{type(exc).__name__}: {exc}"
            st.status = "failed"
            st.note = info_error
        else:
            valuation = build_valuation(ms, info)
            registry.register_all(build_valuation_entries(valuation, info, stmt.provenance))
            st.registered = len(registry) - before
            got = [
                name
                for name, val in (
                    ("주식수", info.shares),
                    ("배당", info.dividend),
                    ("감사의견", info.audit),
                    ("인력", info.workforce),
                    ("지분", info.ownership),
                    ("출자", info.affiliates),
                )
                if val is not None
            ]
            st.summary = f"{len(got)}/6종 · {', '.join(got)}"
            # **무엇을 못 받았는지는 이미 알고 있다.** 조용히 넘기면 커버리지
            # 문제가 숨는다 — 어댑터가 `unavailable`에 남긴 것을 그대로 낸다.
            if info.unavailable:
                st.status = "partial"
                st.note = f"받지 못한 것: {', '.join(info.unavailable)}"
            st.checks = [
                {
                    "label": "발행 − 자기주식 = 유통",
                    "value": "일치" if valuation.shares_reconciled else "불일치",
                    "ok": valuation.shares_reconciled,
                },
            ]
            # EPS 교차검증 — 재무제표 희석EPS vs 배당공시 주당순이익
            if valuation.eps_stmt is not None and valuation.eps_disclosed is not None:
                same = valuation.eps_stmt == valuation.eps_disclosed
                st.checks.append(
                    {
                        "label": "EPS 교차검증 (재무제표 vs 배당공시)",
                        "value": f"{valuation.eps_stmt:,} vs {valuation.eps_disclosed:,}",
                        "ok": same,
                    }
                )

    # 사업보고서 **원문** — 한 번만 받아 여러 섹션에 쓴다 (5~8MB).
    # 실패해도 노트 생성을 막지 않는다.
    segments: SegmentBreakdown | None = None
    segment_section: str | None = None
    segment_profit: SegmentProfitSet | None = None
    business: BusinessProfile | None = None
    st_doc = st_sp = st_seg = st_biz = None
    if with_segments and isinstance(provider, DartProvider) and stmt.rcept_no:
        st_doc = step("document", "사업보고서 원문 (5~8MB)")
        text, doc_error = fetch_document(provider, stmt.rcept_no)
        if doc_error and info_error is None:
            info_error = doc_error
        if doc_error:
            st_doc.status = "failed"
            st_doc.note = doc_error
        else:
            st_doc.summary = f"{len(text or ''):,}자"

        if text:
            st_sp = step("segment_profit", "부문별 손익 (IFRS 8 주석)")
            st_seg = step("segments", "부문별 매출")
            st_biz = step("business", "사업 이해")
            # 부문별 **손익** — IFRS 8 주석. 제목이 회사마다 다르고 연결·별도가
            # 함께 실려 있어 후보를 전부 넘기고 검산이 고르게 한다.
            # 당기·전기 표가 한 섹션에 있어 40,000자로는 전기가 잘린다(실측).
            segment_profit = build_segment_profit(
                find_sections(text, "부문", span=150_000), ms, stmt.rcept_no
            )

            # 「2. 주요 제품 및 서비스」를 먼저 본다 — 「4. 매출 및 수주상황」보다
            # 표가 단순하고(내수/수출 중첩 없음) 3개년이 나란히 있다.
            for keyword in ("주요 제품", "매출"):
                found = find_section(text, keyword)
                candidate = build_segments(found, ms, stmt.rcept_no)
                if candidate.usable:
                    segments, segment_section = candidate, (found.title if found else None)
                    break
                segments = segments or candidate
                segment_section = segment_section or (found.title if found else None)

            business = build_business_profile(
                find_section(text, "사업의 개요"),
                fiscal_year,
                ownership=info.ownership if info else None,
                affiliates=info.affiliates if info else None,
                total_assets=ms.get("total_assets"),
            )

        # **원문에서 뽑은 값은 어느 섹션인지까지 남긴다.** 접수번호만 있으면
        # 검토자가 300쪽 사업보고서 첫 장에서 표를 직접 찾아야 한다.
        before = len(registry)
        if segments is not None and segments.usable:
            registry.register_all(
                build_segment_entries(
                    segments,
                    section_provenance(stmt.provenance, segment_section, stmt.rcept_no),
                )
            )
        if st_seg is not None:
            st_seg.registered = len(registry) - before
            _fill_segments_stage(st_seg, segments, segment_section)

        before = len(registry)
        if segment_profit is not None and segment_profit.usable:
            registry.register_all(
                build_segment_profit_entries(
                    segment_profit,
                    section_provenance(
                        stmt.provenance, segment_profit.section_title, stmt.rcept_no
                    ),
                )
            )
        if st_sp is not None:
            st_sp.registered = len(registry) - before
            _fill_segment_profit_stage(st_sp, segment_profit)

        before = len(registry)
        if business is not None:
            registry.register_all(
                build_business_entries(
                    business,
                    section_provenance(stmt.provenance, "사업의 개요", stmt.rcept_no),
                )
            )
        if st_biz is not None:
            st_biz.registered = len(registry) - before
            _fill_business_stage(st_biz, business)

    # 추정 — 가정에서 계산된다. 직전 추정이 있으면 revision을 잡는다.
    st = step("estimates", "추정·밸류에이션 산출")
    before = len(registry)
    estimates = build_estimates(ms, assumptions, forward)
    previous = _load_prior_estimates(store, symbol, estimates.fiscal_year, published_at)
    revisions = compare_estimates(previous, estimates)
    if estimates.usable:
        # 추정치는 **공시가 아니라 우리 계산**이다. 원본 공시로 표시하면
        # 검토자가 DART에서 찾으려다 없는 것을 찾게 된다.
        est_prov = stmt.provenance.model_copy(
            update={"dataset": f"추정 (기준선 · {estimates.method})", "source_url": None}
        )
        registry.register_all(build_estimate_entries(estimates, revisions, est_prov))
        if valuation is not None:
            registry.register_all(
                build_forward_entries(
                    valuation, estimates.values, estimates.fiscal_year, stmt.provenance
                )
            )

    # 렌즈 — 같은 숫자에 다른 질문을 던진다 (D35). 앞선 레이어가 모두 끝난 뒤에
    # 돌아야 부문 자산·마진 브리지를 함께 볼 수 있다.
    st.registered = len(registry) - before
    if estimates.usable:
        overridden = [a.label for a in estimates.assumptions if a.is_override]
        st.summary = f"가정 {len(estimates.assumptions)}개 · {estimates.fiscal_year} 추정"
        # **여기가 계산과 판단의 경계다.** 가정은 사람이 바꿀 수 있는 유일한
        # 입력이고, 나머지는 전부 그 함수다 (D24).
        st.checks = [
            {"label": a.label, "value": f"{a.value:+.1f}{a.unit}", "ok": True}
            for a in estimates.assumptions
        ]
        if overridden:
            st.note = f"사용자가 덮어쓴 가정: {', '.join(overridden)}"
        elif estimates.warnings:
            st.status = "partial"
            st.note = " · ".join(estimates.warnings)
        if revisions:
            st.summary += f" · 직전 대비 {len(revisions)}건 변화"
    else:
        st.status = "absent"
        st.summary = "추정 불가"
        st.note = " · ".join(estimates.warnings) or "기준선을 세울 과거 실적이 부족합니다."

    st = step("lenses", "분석 렌즈 (같은 숫자에 다른 질문)")
    before = len(registry)
    lenses = build_lenses(
        ms,
        valuation=valuation,
        bridge=build_margin_bridge(ms),
        segment_profit=segment_profit,
        segments=segments,
        business=business,
        info=info,
    )
    registry.register_all(
        build_lens_entries(
            lenses,
            segment_profit,
            section_provenance(
                stmt.provenance,
                segment_profit.section_title if segment_profit else None,
                stmt.rcept_no,
            ),
            ms.fiscal_year,
        )
    )

    st.registered = len(registry) - before
    spoke = [v for v in lenses.views] if lenses is not None else []
    answered = [v for v in spoke if v.usable]
    if answered:
        st.summary = f"판독 {len(answered)}/{len(spoke)}" + (
            f" · 관점 충돌 {len(lenses.tensions)}건" if lenses.tensions else ""
        )
        # 렌즈는 **1순위에 답하지 못하면 결론을 내지 않는다** (D35). 침묵은
        # 결함이 아니라 그 렌즈가 요구한 데이터가 없다는 뜻이고, 이유가
        # `silent_reason`에 남아 있다.
        st.checks = [
            {
                "label": v.label,
                "value": f"{len(v.readings)}단계 판독" if v.usable else (v.silent_reason or "침묵"),
                "ok": v.usable,
            }
            for v in spoke
        ]
        if len(answered) < len(spoke):
            st.status = "partial"
            st.note = "일부 렌즈는 요구한 데이터가 없어 결론을 내지 않았습니다."
    else:
        st.status = "absent"
        st.summary = "렌즈 판독 없음"
        st.note = "; ".join(v.silent_reason for v in spoke if v.silent_reason) or (
            "렌즈가 요구한 데이터(부문 자산·마진 브리지 등)가 없습니다."
        )

    sections = compose_sections(
        ms,
        registry,
        consolidation=stmt.consolidation,
        valuation=valuation,
        info=info,
        estimates=estimates,
        revisions=revisions,
        segments=segments,
        segment_profit=segment_profit,
        lenses=lenses,
        business=business,
    )

    narration = None
    if llm is not None:
        from arc.llm.narrate import narrate, narrate_industry

        st_llm = step("llm", "LLM 서술 생성 (가장 오래 걸립니다)")
        basis = "연결" if stmt.consolidation is ConsolidationType.CONSOLIDATED else "별도"
        obs = build_observations(ms, build_margin_bridge(ms))
        if valuation is not None and info is not None:
            obs += build_valuation_observations(valuation, info)
        obs += build_estimate_observations(estimates, revisions)
        if business is not None:
            obs += build_business_observations(business)
        # 부문 구분은 하나만 준다 — 두 분류를 함께 주면 LLM이 「부문」을 두 뜻으로
        # 섞어 쓴다(실측). 이익까지 있는 영업부문 주석이 있으면 그쪽만 쓴다.
        if segment_profit is not None and segment_profit.usable:
            obs += build_segment_profit_observations(segment_profit)
        elif segments is not None:
            obs += build_segment_observations(segments)
        obs += build_lens_observations(lenses)
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
            # LLM이 관전 포인트를 못 내면 **렌즈가 찾은 충돌로 되돌린다** (D35).
            # 비워 두면 D22가 이 섹션을 만든 이유가 사라진다.
            sections["watchpoints"] = list(n.get("watchpoints") or []) or [
                t.text for t in lenses.tensions
            ]
            if n.get("business_narrative"):
                sections["business_narrative"] = n["business_narrative"]

        # 산업 배경 — **별도 호출, 별도 규칙.** 수치 카탈로그를 주지 않고
        # 숫자를 아예 금지한다. 숫자가 섞이면 이 문단만 버리고 리포트는 낸다.
        # LLM이 쓴 것과 결정론이 쓴 것을 구분해 남긴다 — **숫자는 어느 쪽이든
        # 레지스트리에서 온다**(불변식 1). 여기서 갈리는 건 문장뿐이다.
        if narration is not None and narration.used_llm:
            c = narration.completion
            st_llm.summary = (c.model if c is not None else "LLM") + " · 문장만 교체"
            st_llm.checks = [{"label": "수치 출처", "value": "레지스트리 (불변식 1)", "ok": True}]
            if c is not None and c.cost_usd is not None:
                st_llm.checks.append(
                    {"label": "건당 비용", "value": f"${c.cost_usd:.4f}", "ok": True}
                )
            if narration.problems:
                st_llm.status = "partial"
                st_llm.note = " · ".join(narration.problems[:3])
        else:
            st_llm.status = "absent"
            st_llm.summary = "결정론 문장"
            st_llm.note = "LLM이 응답하지 않아 결정론 문장으로 냈습니다. 수치는 동일합니다."

        if business is not None and business.usable:
            st_ind = step("industry", "산업 배경 생성 (미검증 레인)")
            industry_text, industry_problems = narrate_industry(
                llm,
                company_name=company.name,
                profile_text=business.overview,
                segments=_segment_names(segment_profit, segments),
                registry=registry,
            )
            sections["industry_context"] = industry_text
            if industry_problems and narration is not None:
                narration.problems.extend(industry_problems)
            # **미검증 레인은 숫자가 하나라도 있으면 문단을 버린다** (D31).
            # 버려진 것도 기록에 남긴다 — 조용히 사라지면 왜 없는지 모른다.
            if industry_text:
                st_ind.summary = f"{len(industry_text)}자"
                st_ind.checks = [
                    {"label": "출처로 되짚을 수 있는가", "value": "아니오 (공시 밖)", "ok": False},
                    {"label": "숫자 없음", "value": "확인", "ok": True},
                ]
            else:
                st_ind.status = "absent"
                st_ind.summary = "문단 버림"
                st_ind.note = " · ".join(industry_problems) or (
                    "숫자가 섞여 D31 규칙으로 버렸습니다. 리포트는 그대로 냅니다."
                )
    assembled = assemble(
        company,
        ms,
        sections,
        published_at=published_at or dt.datetime.now(dt.UTC).date(),
        valuation=valuation,
        registry=registry,
    )

    st = step("gate", "G0 게이트 검증", label="발간 게이트 G0")
    gate = G0Gate(registry).check(assembled)
    rendered = registry.render_text(assembled) if gate.passed else None
    bindings = registry.bindings(assembled) if gate.passed else []
    st.summary = gate.summary()
    if not gate.passed:
        st.status = "failed"
        st.note = f"차단 {len(gate.violations)}건 — 발간할 수 없습니다."
        st.checks = [
            {"label": v.rule, "value": v.detail[:80], "ok": False} for v in gate.violations[:8]
        ]
    else:
        st.checks = [
            {"label": "레지스트리 등록 수치", "value": f"{len(registry)}건", "ok": True},
            {"label": "본문에 등장한 수치", "value": f"{len(bindings)}건", "ok": True},
        ]

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
        segments=segments,
        segment_profit=segment_profit,
        lenses=lenses,
        business=business,
        report_info=info,
        valuation=valuation,
        info_error=info_error,
        estimates=estimates,
        revisions=revisions,
        stages=stages,
    )
