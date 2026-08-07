"""웹 표면 — 실적 리뷰 노트 작업대.

구성
----
화면은 `web/`의 Next.js(App Router + shadcn/ui)이고, 여기서는 **API만** 낸다.
빌드 산출물(`web/out`)을 이 앱이 정적 파일로 서빙하므로 컨테이너는 하나다 —
`.arc-store`가 볼륨에 있어야 추정 이력(revision)과 corpCode 캐시가 살아남는다.
서비스를 둘로 쪼개면 둘 다 깨진다 (Dockerfile 주석 참조).

corpCode 캐시는 D69에서 **프로세스 메모리에서 `.arc-store/cache`로 내렸다.**
전에는 워커가 재시작할 때마다 수 MB zip을 다시 받아 OpenDART 요청률 차단을
불렀다 — 볼륨이 없으면 그 동작으로 되돌아간다.

서버 렌더 Jinja 화면이 먼저 있었고 `/api/*`는 그때부터 이 이관을 전제로 열어둔
것이다. 옮기면서 버린 코드는 없다.

화면이 증명해야 하는 것
-----------------------
1. **숫자를 클릭하면 출처가 나온다.** Number Registry가 없으면 불가능한
   기능이고, 이게 ChatGPT 대비 유일한 구조적 차별점이다.
2. **게이트가 보인다.** 통과/차단과 위반 내역이 화면에 있어야 "사람이 검토
   후 발간"이 성립한다.
3. **가정을 바꾸면 추정이 바뀐다.** 추정이 가정의 함수라는 걸 조작으로 보여준다.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import logging
import os
import re
import threading
import urllib.parse
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import (
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from arc.chat import answer_question
from arc.chat.retrieval import Context
from arc.data.kr.dart import DartProvider
from arc.finmodel.peer import build_peer_table
from arc.finmodel.peer_suggest import load_prices, suggest_group
from arc.ingest.convert import ConvertError, convert
from arc.ingest.model_fill import fill_model
from arc.ingest.prior import (
    UPLOAD_PERIOD,
    as_facts,
    detect_symbol,
    outline_of,
    read_prior,
)
from arc.llm.number_registry import NumberRegistry
from arc.pipeline.earnings_review import ReportResult, build_report, save_estimates
from arc.render.charts import (
    Slice,
    legend,
    margin_line,
    palette,
    quarter_bars,
    segment_bar,
    trend_bars,
)
from arc.render.docx import markdown_to_docx
from arc.render.html import binding_rows, render_html
from arc.render.xlsx import collect_series, note_to_xlsx
from arc.store.cards import (
    DRAFT,
    HANDOFF,
    PEER,
    SINGLE,
    Card,
    CardStore,
    attention_reasons,
    column_for,
    next_version,
    now_iso,
    peer_attention_reasons,
    peer_member,
    resolve_peer_members,
)
from arc.store.notes import (
    NOTE_DATASET,
    compare_notes,
    facts_from_registry,
    previous_note,
    rank,
)
from arc.store.notes import to_rows as note_rows
from arc.store.snapshot import SnapshotStore
from arc.web.auth import BasicAuthMiddleware, LLMBudget
from arc.web.jobs import JobStore

REPO_ROOT = Path(__file__).resolve().parents[3]
# uvicorn은 CLI를 거치지 않고 모듈을 직접 import한다 — 여기서도 키를 읽어야 한다
load_dotenv(REPO_ROOT / ".env")

WEB_DIR = Path(__file__).resolve().parent

# Next.js 정적 익스포트. 로컬은 `web/out`, 컨테이너는 `/app/static`(Dockerfile).
STATIC_DIR = Path(os.environ.get("ARC_STATIC_DIR", REPO_ROOT / "web" / "out"))

# 추정 이력 — revision을 보여주려면 직전 **발간**이 있어야 한다
STORE_DIR = Path(os.environ.get("ARC_STORE_DIR", REPO_ROOT / ".arc-store"))
DRAFTS_DIR = REPO_ROOT / "drafts"

log = logging.getLogger("arc.web")


def _reap_running_cards() -> None:
    """시작할 때 **「생성 중」에 갇힌 카드를 풀어 준다.**

    작업 큐가 프로세스 메모리에 있어서(워커 1개 전제) 서버가 재시작하면
    돌던 작업이 사라진다. 그런데 카드는 `running`으로 남아 보드에서 영영
    「생성 중…」이다 — 실측으로 삼성전자 카드 하나가 그렇게 갇혔다.

    지우지 않고 **확인 필요로 내려놓고 이유를 적는다.** 사람이 다시 돌릴지
    지울지 고르면 된다. 조용히 지우면 무엇이 있었는지 알 수 없다.
    """
    cards = _open_cards()
    if cards is None:
        return
    for card in cards.list():
        if not card.running:
            continue
        card.running = False
        card.column = DRAFT
        card.error = card.error or "서버가 재시작돼 생성이 중단됐습니다. 다시 만들어 주십시오."
        card.attention = card.attention or ["생성이 중단됐습니다 — 다시 만들어 주십시오"]
        cards.save(card)
        log.info("중단된 카드를 확인 필요로 옮겼습니다: %s %s", card.symbol, card.id)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """시작할 때 corpCode를 **백그라운드로** 미리 받는다.

    첫 요청이 1.5MB 다운로드를 뒤집어쓰면 그 사람만 유독 느리다. 시작을
    붙들지 않는 이유는 헬스체크 때문이다 — 기다리게 하면 플랫폼이 배포를
    실패로 본다.
    """

    def warm() -> None:
        try:
            _shared_provider()
            log.info("corpCode 캐시 준비 완료")
        except Exception as exc:  # noqa: BLE001 — 실패해도 첫 요청이 다시 시도한다
            log.warning("corpCode 캐시 준비 실패: %s", exc)

    threading.Thread(target=warm, daemon=True, name="arc-warm").start()
    _reap_running_cards()
    yield


app = FastAPI(title="AI Research Center", docs_url="/api/docs", lifespan=_lifespan)
# 공개 주소에 올릴 때 서버의 LLM 키가 무방비가 되면 안 된다 (auth.py 참조)
app.add_middleware(BasicAuthMiddleware)

# 개발 전용. `next dev`(3000)와 이 서버(8000)를 따로 띄울 때만 필요하다 —
# 배포는 정적 익스포트를 같은 출처에서 서빙하므로 CORS가 필요 없다.
# **기본은 꺼져 있다.** 켜진 채로 배포되면 아무 사이트나 이 API를 부를 수 있다.
if os.environ.get("ARC_DEV_ORIGIN"):
    from fastapi.middleware.cors import CORSMiddleware

    # BasicAuth보다 나중에 추가해야 바깥에 놓여 preflight가 인증에 막히지 않는다
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[os.environ["ARC_DEV_ORIGIN"]],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
LLM_BUDGET = LLMBudget()
JOBS = JobStore()


@dataclass
class ViewModel:
    """화면이 필요로 하는 것만 담는다. `ReportResult`를 그대로 넘기지 않는다."""

    symbol: str
    year: int
    company: str = ""
    market: str = ""
    basis: str = ""
    body_html: str = ""
    bindings: list[dict] = field(default_factory=list)
    gate_passed: bool = False
    gate_summary: str = ""
    violations: list[dict] = field(default_factory=list)
    metrics_found: int = 0
    metrics_missing: list[str] = field(default_factory=list)
    registry_size: int = 0
    assumptions: list[dict] = field(default_factory=list)
    revisions: list[dict] = field(default_factory=list)
    estimate_warnings: list[str] = field(default_factory=list)
    # 연차별 추정. 첫 해는 `assumptions`와 같다 — 화면이 표로 낸다.
    estimate_years: list[dict] = field(default_factory=list)
    # 파이프라인 단계 기록 — 무엇을 검산했고 무엇을 못 구했는지.
    # 이게 없으면 화면은 종목코드를 넣으면 완성본이 나오는 블랙박스다.
    stages: list[dict] = field(default_factory=list)
    segment_chart: str = ""  # 인라인 SVG
    segment_legend: str = ""
    # 범례에 **숫자를 싣는다.** 막대만 있으면 "감으로만" 보인다는 지적이 나왔다.
    # 값은 전부 레지스트리에서 꺼낸 표시 문자열이라 본문과 갈라질 수 없다.
    segment_items: list[dict] = field(default_factory=list)
    trend_chart: str = ""
    trend_legend: str = ""
    # 분기 추이 — **차트 그리기는 RA의 업무 카테고리다** (D55 동료 인풋).
    quarter_chart: str = ""
    quarter_margin_chart: str = ""
    quarter_note: str = ""
    trend_note: str = ""  # 해가 모자랄 때 왜 그런지
    industry_context: bool = False  # 미검증 레인이 있었는가
    llm_used: bool = False
    llm_model: str = ""
    llm_cost: float | None = None
    published_path: str = ""
    # 직전 발간 노트 대비 무엇이 달라졌는가 (D46). 비교 대상이 없으면 빈 목록.
    changes: list[dict] = field(default_factory=list)
    changes_basis: str = ""
    # **「얼마가」가 아니라 「무엇이」** 달라졌는가 (D64). 올린 직전 리포트가
    # 본 그림이 이번 공시로 유지되는지 영역별로 대조한 것.
    areas: list[dict] = field(default_factory=list)
    areas_basis: str = ""
    areas_note: str = ""
    notice: str = ""
    error: str = ""


# corpCode.xml은 1.5MB zip이고 파싱까지 하면 무겁다. **프로세스 수명 동안
# 하나만 둔다** — 검색뿐 아니라 생성도 같은 인스턴스를 쓴다.
#
# 요청마다 DartProvider()를 새로 만들면 종목코드→corp_code를 찾으려고 매번
# 1.5MB를 다시 받는다. 로컬(한국)에서는 1초라 안 보이지만 배포 리전이 멀면
# 그대로 드러난다 — 실측: Railway에서 회사 정보 조회 한 단계에 8.9초.
_PROVIDER: DartProvider | None = None
_PROVIDER_LOCK = threading.Lock()


def _shared_provider() -> DartProvider:
    """생성·검색이 함께 쓰는 DART 클라이언트.

    작업이 백그라운드 스레드에서 도니 락으로 초기화를 한 번만 보장한다.
    `DartProvider` 자체는 조회만 하므로 스레드 간 공유해도 안전하다.
    """
    global _PROVIDER
    with _PROVIDER_LOCK:
        if _PROVIDER is None:
            provider = DartProvider()
            provider.load_corp_codes()
            _PROVIDER = provider
    return _PROVIDER


def _search_provider() -> DartProvider:
    return _shared_provider()


def _note_texts(r) -> dict[str, str]:
    """숫자가 아닌 사실. **감사의견이 바뀌는 것은 어느 수치 변화보다 크다.**"""
    out: dict[str, str] = {}
    info = r.report_info
    audit = info.audit if info is not None else None
    if audit is not None and audit.opinion:
        out["감사의견"] = audit.opinion
        if audit.auditor:
            out["감사인"] = audit.auditor
    if r.segments is not None and r.segments.usable:
        out["보고부문"] = " · ".join(x.name for x in r.segments.lines)
    return out


def _note_facts(r, published_at: dt.date):
    return facts_from_registry(
        r.registry,
        symbol=r.symbol,
        year=r.fiscal_year,
        period=r.statement.period.value,
        published_at=published_at,
        texts=_note_texts(r),
    )


def _shown(registry: NumberRegistry, key: str) -> str:
    """레지스트리에 있으면 표시 문자열, 없으면 빈 문자열.

    **화면이 숫자를 직접 포맷하지 않는다.** 여기서 f-string을 쓰면 본문과 다른
    반올림이 생기고, 그 숫자는 게이트도 감사 추적도 거치지 않는다.
    """
    entry = registry._entries.get(key)
    return entry.rendered() if entry is not None else ""


def _to_view(r: ReportResult) -> ViewModel:
    v = ViewModel(symbol=r.symbol, year=r.fiscal_year)
    v.company = r.company.name
    v.market = r.company.market.value
    v.basis = "연결" if r.statement.consolidation.value == "CFS" else "별도"
    v.gate_passed = r.gate.passed
    v.gate_summary = r.gate.summary()
    v.violations = [
        {"rule": x.rule, "line": x.line, "detail": x.detail} for x in r.gate.violations[:40]
    ]
    v.metrics_found = len(r.metrics.values)
    v.metrics_missing = r.metrics.missing_labels
    v.registry_size = len(r.registry)

    # 단계 기록은 **게이트가 막아도 낸다.** 차단됐을 때야말로 어느 단계에서
    # 무엇이 어긋났는지 봐야 한다 — 본문만 숨기고 과정은 남긴다.
    v.stages = [
        {
            "key": s.key,
            "label": s.label,
            "status": s.status,
            "summary": s.summary,
            "checks": s.checks,
            "registered": s.registered,
            "note": s.note,
        }
        for s in r.stages
    ]

    # 게이트가 막으면 본문을 렌더하지 않는다 — 차단된 초안을 보여주면
    # 검토자가 그걸 결과로 착각한다.
    if r.gate.passed:
        v.body_html = render_html(r.assembled, r.registry)
        v.bindings = [b for b in binding_rows(r) if not b["internal"]]

    if r.estimates is not None:
        v.assumptions = [
            {
                "key": a.key,
                "label": a.label,
                "value": round(a.value, 2),
                "unit": a.unit,
                "basis": a.basis,
                "override": a.is_override,
            }
            for a in r.estimates.assumptions
        ]
        v.estimate_warnings = r.estimates.warnings
        v.estimate_years = [
            {
                "fiscal_year": y.fiscal_year,
                "values": y.values,
                "assumptions": [
                    {
                        "key": a.key,
                        "label": a.label,
                        "value": round(a.value, 2),
                        "unit": a.unit,
                        "basis": a.basis,
                        "override": a.is_override,
                    }
                    for a in y.assumptions
                ],
            }
            for y in r.estimates.years
        ]
    v.revisions = [
        {
            "label": x.label,
            "previous": x.previous,
            "current": x.current,
            "change": round(x.change_pct, 1),
            "direction": x.direction,
        }
        for x in r.revisions
    ]

    seg = r.segments
    y = r.metrics.fiscal_year
    if seg is not None and seg.usable and len(seg.lines) > 1:
        # 정렬해도 **레지스트리 키는 원래 순번**이라 인덱스를 들고 다닌다.
        ordered = sorted(enumerate(seg.lines), key=lambda kv: -kv[1].amount)
        shares = [
            Slice(
                label=x.name, share=x.share if x.share is not None else x.amount / seg.total * 100
            )
            for _, x in ordered
        ]
        v.segment_chart = segment_bar(shares)
        v.segment_legend = legend([x.name for _, x in ordered])
        v.segment_items = [
            {
                "name": x.name,
                "color": palette(rank),
                "amount": _shown(r.registry, f"segment{i + 1}_revenue_{y}a"),
                "share": _shown(r.registry, f"segment{i + 1}_share_{y}a"),
            }
            for rank, (i, x) in enumerate(ordered)
        ]

    ms = r.metrics
    # **있는 해만 그린다.** 분기보고서에는 전전기 손익이 아예 없어서, 3칸을
    # 고정으로 그리면 막대가 한 해에만 서 있는 빈 차트가 나온다.
    cols = [
        (str(ms.fiscal_year - 2), "prior2"),
        (str(ms.fiscal_year - 1), "prior"),
        (str(ms.fiscal_year), "current"),
    ]
    series: dict[str, dict[str, float]] = {}
    for key, label in (("revenue", "매출액"), ("operating_income", "영업이익")):
        mv = ms.values.get(key)
        if mv is None:
            continue
        got = {
            name: float(getattr(mv, attr)) for name, attr in cols if getattr(mv, attr) is not None
        }
        if got:
            series[label] = got
    years = [name for name, _ in cols if any(name in g for g in series.values())]
    if series and years:
        trend = [(label, [g.get(name, 0.0) for name in years]) for label, g in series.items()]
        v.trend_chart = trend_bars(years, trend)
        v.trend_legend = legend([n for n, _ in trend])
        if len(years) < 3:
            v.trend_note = (
                "분기·반기보고서에는 전전기 손익이 공시되지 않아 "
                f"{len(years)}개년만 그렸습니다. 3개년은 사업보고서에서 나옵니다."
            )

    # 분기 막대 + 이익률 선. **비율은 막대가 아니라 선이다** — 크기가 아니라
    # 수준이라, 막대로 그리면 8%와 9%가 거의 같아 보인다.
    q = getattr(r, "quarters", None)
    if q is not None and q.points:
        labels = [x.label for x in q.points]
        rev = [None if v is None else float(v) for v in q.metric_row("revenue")]
        op = q.metric_row("operating_income")
        v.quarter_chart = quarter_bars(labels, rev, highlight_from=max(len(labels) - 4, 0))
        margins = [
            None if (o is None or not rv) else round(o / rv * 100, 1)
            for o, rv in zip(op, q.metric_row("revenue"), strict=False)
        ]
        v.quarter_margin_chart = margin_line(labels, margins)
        v.quarter_note = "막대는 매출, 선은 영업이익률입니다. 최근 4개 분기가 진합니다."

    n = r.narration
    if n is not None:
        v.llm_used = bool(n.used_llm)
        if n.completion is not None:
            v.llm_model = n.completion.model
            v.llm_cost = n.completion.cost_usd
    return v


def _generate(
    symbol: str,
    year: int,
    *,
    period: str = "ANNUAL",
    use_llm: bool,
    search: bool = False,
    prior_markdown: str = "",
    prior_name: str = "",
    overrides: dict[str, float],
    forward: list[dict[str, float]] | None = None,
    publish: bool = False,
    on_progress=None,
) -> tuple[ViewModel, dict]:
    """노트 생성. 화면용 ViewModel과 **문서 상태**를 함께 돌려준다.

    문서 상태(치환 전 조립본 + 레지스트리)가 있어야 나중에 코멘트를 받아
    문단을 고쳐 쓰고 다시 게이트를 돌릴 수 있다. 없으면 카드는 읽기 전용
    스냅샷이고 리뷰 루프가 성립하지 않는다.

    **생성과 발간은 다르다.**

    생성은 미리보기다 — 이력에 남지 않는다. 발간해야 추정이 스냅샷으로
    저장되고 다음 발간의 revision 기준이 된다. 생성할 때마다 저장하면
    가정을 만지작거린 흔적이 전부 "직전 추정"이 되어 이력이 무의미해진다.
    """
    provider = _shared_provider()
    client = None
    budget_exhausted = False
    if use_llm:
        if LLM_BUDGET.take():
            from arc.llm.client import get_client

            client = get_client()
        else:
            # 상한에 닿으면 **LLM만 끈다.** 화면이 죽는 것보다 수치만 나오는 편이 낫다.
            budget_exhausted = True

    # 최근 기사 — **LLM이 있을 때만 의미가 있다.** 스니펫을 문단으로 만드는
    # 게 이 레인의 전부라, 문장을 안 쓰면 링크 목록만 남는다.
    news = None
    news_error = ""
    if search and client is not None:
        try:
            news = _search_news(symbol)
        except Exception as exc:  # noqa: BLE001 — 검색 실패가 생성을 막지 않는다
            news_error = f"기사 검색 실패: {type(exc).__name__}"
            log.warning("뉴스 검색 실패 (%s): %s", symbol, exc)

    # 사용자가 올린 직전 노트 (D48). 차례는 LLM 없이 나오므로 LLM이 꺼져
    # 있어도 「구성 따라 쓰기」는 산다.
    prior = None
    if prior_markdown.strip():
        prior = read_prior(client, prior_markdown, prior_name or "업로드 문서")

    # 저장소를 못 쓰면 **이력 없이 계속한다.** 볼륨이 안 붙었거나 경로가
    # 틀렸다고 생성이 죽으면 안 된다 — 추정 이력은 향상이지 필수가 아니다.
    store = _open_store()
    published_at = dt.datetime.now(dt.UTC).date()
    from arc.data.base import PeriodType

    r = build_report(
        symbol,
        year,
        provider,
        period=PeriodType(period),
        published_at=published_at,
        llm=client,
        store=store,
        news=news,
        outline=prior.outline if prior is not None else None,
        assumptions=overrides or None,
        forward=forward or None,
        on_progress=on_progress,
    )
    vm = _to_view(r)

    # 직전 발간 노트 대비 변화 (D46). **발간된 것만 비교 대상이다** — 만지작
    # 거린 미리보기까지 세면 「직전」이 무엇인지 알 수 없다 (D27).
    prev = previous_note(store, symbol, exclude=(r.fiscal_year, r.statement.period.value))
    # **발간 이력이 없으면 업로드한 노트가 기준선이 된다** (D48). 첫 노트를
    # 쓰는 사람에게 「직전 대비」가 비어 있는 것이 지금까지의 한계였다.
    prior_facts = as_facts(prior, symbol=symbol, year=r.fiscal_year) if prior else None
    if prev is None and prior_facts is not None:
        prev = prior_facts
    if prev is not None:
        changes = rank(compare_notes(prev, _note_facts(r, published_at)))
        vm.changes = [
            {
                "name": c.name,
                "kind": c.kind,
                "previous": c.previous,
                "current": c.current,
                "direction": c.direction,
                # 화면이 숫자를 다시 만들지 않도록 **문자열로 굳혀서** 준다.
                "change": (
                    f"{c.change_abs:+.1f}pp"
                    if c.change_abs is not None
                    else (f"{c.change_pct:+.1f}%" if c.change_pct is not None else "")
                ),
            }
            for c in changes
        ]
        if prev.period == UPLOAD_PERIOD:
            basis = f"업로드한 노트 대비 — {prev.published_at}"
            basis += " · 업로드 문서의 값은 우리가 검산하지 않았습니다"
        else:
            basis = f"{prev.label} 노트 대비 · 발간 {prev.published_at}"
        if prev.period != UPLOAD_PERIOD and prev.period != r.statement.period.value:
            # 기간이 다르면 실적 금액은 빠진다. **왜 빠졌는지 안 쓰면**
            # "매출이 왜 목록에 없지"가 된다.
            basis += " · 기간이 달라 실적 금액은 빼고 비율·구성·추정만 비교했습니다"
        vm.changes_basis = basis

    if news_error:
        vm.notice = (vm.notice + " " if vm.notice else "") + news_error
    if budget_exhausted:
        vm.notice = (
            f"LLM 생성 한도({LLM_BUDGET.limit}건)에 도달해 기본 문장으로 냈습니다. "
            "수치와 점검 결과는 동일합니다."
        )

    if publish and r.publishable:
        if store is not None and r.estimates is not None and r.estimates.usable:
            save_estimates(store, r.estimates, symbol, published_at)
        DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
        path = DRAFTS_DIR / f"{symbol}-FY{year}-{published_at.isoformat()}.md"
        path.write_text(r.rendered or "", encoding="utf-8")
        vm.published_path = str(path)
    est = r.estimates
    return vm, {
        "assembled": r.assembled,
        "registry": r.registry.dump(),
        # 발간할 때 남길 노트 지문 (D46). 발간은 읽고 고친 뒤에 하는 일이라
        # 그때까지 카드가 들고 있어야 한다 — 추정 스냅샷과 같은 이유다.
        "note_facts": note_rows(_note_facts(r, published_at)),
        "prior_note": (
            {
                "source_name": prior.source_name,
                "outline": prior.outline,
                "target_price": prior.target_price,
                "rating": prior.rating,
                "estimates": prior.estimates,
                "problems": prior.problems,
                "markdown": prior_markdown,
            }
            if prior is not None
            else {}
        ),
        "estimate_snapshot": (
            {
                "fiscal_year": est.fiscal_year,
                "base_year": est.base_year,
                "values": est.values,
                "method": est.method,
            }
            if est is not None and est.usable
            else {}
        ),
    }


def news_available() -> bool:
    """기사 검색을 켤 수 있는가. 키가 없으면 체크박스를 눌러도 아무 일이 없다."""
    return bool(os.environ.get("NAVER_CLIENT_ID") and os.environ.get("NAVER_CLIENT_SECRET"))


# 종목·날짜당 한 번만 부른다. 같은 종목을 하루에 여러 번 생성해도 API는
# 한 번만 나간다 — 가정을 만지작거리며 다시 계산하는 게 이 도구의 일이라
# 캐시가 없으면 호출이 그 횟수만큼 늘어난다. (워커 1개 전제, jobs.py와 같다)
# 값은 (회사명, 원본 기사 목록). 회사명은 「검색어가 스쳤을 뿐인 기사」를
# 거를 때 다시 필요하다.
_NEWS_CACHE: dict[tuple[str, str], tuple[str, list]] = {}


def _search_news(symbol: str, *, months: int = 3, limit: int = 10):
    """종목의 최근 기사. **한 번 부르고 여기서 거른다.**

    네이버 검색 API는 한 번에 100건을 최신순으로 준다. 페이지를 넘기지 않고
    100건을 받아 `news_filter.select()`로 좁힌다 — 날짜 창 · 매체 · 소음 ·
    회사 무관 · 중복을 차례로 걷어낸다. 호출은 종목당 1회다.

    본문은 가져오지 않는다(스니펫 전용, ARCHITECTURE §5.1). 우리가 하는 일은
    "무엇이 있었는지"를 링크와 함께 보여 주는 것이지 기사를 재생산하는 게 아니다.
    """
    if not news_available():
        return None
    from arc.data.kr.naver_news import NaverNewsProvider
    from arc.data.kr.news_filter import plain_name, select

    now = dt.datetime.now(dt.UTC)
    ck = (symbol, now.date().isoformat())
    cached = _NEWS_CACHE.get(ck)
    if cached is None:
        # **상장 종목명으로 검색한다.** 법인명은 DART가 영문을 한글로 음차해
        # 둬서 기사와 안 맞는다 — 실측: 「에이치디현대중공업」으로 검색하니
        # 거른 뒤 1건, 「HD현대중공업」은 정상이었다.
        company = _shared_provider().get_company(symbol)
        name = plain_name(company.short_name or company.name)
        cached = (name, NaverNewsProvider().get_news(name, limit=100))
        _NEWS_CACHE[ck] = cached
    name, raw = cached
    return select(raw, now=now, months=months, limit=limit, company=name) or None


def _news_by_name(company: str):
    """회사 **이름**으로 기사를 찾는다. `_search_news()`와 달리 DART를 안 탄다.

    채팅의 힌트 레인이 주는 것은 종목코드가 아니라 카드에 적힌 회사명이다.
    이름이 이미 있는데 코드로 되돌려 DART에 다시 묻는 것은 낭비이고,
    무엇보다 **DART가 막혀도 채팅은 살아야 한다**([D69](../../docs/decisions.md)).
    """
    from arc.data.kr.naver_news import NaverNewsProvider
    from arc.data.kr.news_filter import plain_name, select

    name = plain_name(company)
    if not name:
        return []
    now = dt.datetime.now(dt.UTC)
    key = (f"name:{name}", now.date().isoformat())
    cached = _NEWS_CACHE.get(key)
    if cached is None:
        cached = (name, NaverNewsProvider().get_news(name, limit=100))
        _NEWS_CACHE[key] = cached
    _, raw = cached
    return list(select(raw, now=now, months=3, limit=10, company=name) or [])


def _open_store() -> SnapshotStore | None:
    """추정 이력 저장소. 쓸 수 없으면 None.

    Railway에서 볼륨을 안 붙였거나 마운트 경로가 `ARC_STORE_DIR`과 다르면
    쓰기가 실패한다. 그때 500을 내면 **생성 자체가 막힌다** — 이력은
    revision 추적을 위한 향상이지 리포트 생성의 전제가 아니다.
    """
    try:
        return SnapshotStore(STORE_DIR)
    except OSError as exc:
        log.warning("추정 이력 저장소를 열지 못했습니다 (%s): %s", STORE_DIR, exc)
        return None


def _company_name(symbol: str) -> str:
    """종목코드 → 회사명. 실패해도 생성을 막지 않는다 — 이름은 표시용이다."""
    try:
        return _shared_provider().get_company(symbol).name
    except Exception as exc:  # noqa: BLE001
        log.warning("회사명을 못 읽었습니다 (%s): %s", symbol, exc)
        return ""


def _open_cards() -> CardStore | None:
    """카드 저장소. 볼륨이 없으면 None — 생성은 계속되고 이력만 안 남는다.

    `_open_store()`와 같은 판단이다. 저장이 실패했다고 리포트 생성을 막으면
    안 된다.
    """
    try:
        return CardStore(STORE_DIR)
    except OSError as exc:
        log.warning("카드 저장소를 열지 못했습니다 (%s): %s", STORE_DIR, exc)
        return None


def _store_status() -> dict[str, object]:
    """볼륨이 제대로 붙었는지 — 배포 직후 확인용."""
    store = _open_store()
    if store is None:
        return {"writable": False, "path": str(STORE_DIR), "reason": "디렉터리를 만들 수 없음"}
    probe = store.base_dir / ".write-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return {"writable": False, "path": str(STORE_DIR), "reason": str(exc)}
    return {"writable": True, "path": str(STORE_DIR)}


def _resolve_symbol(value: str) -> str:
    """입력이 종목코드가 아니면 회사명으로 보고 찾는다.

    자동완성을 안 쓰고 이름만 타이핑해도 동작해야 한다. 결과가 여럿이면
    **고르지 않고 알린다** — 임의로 하나를 고르면 사용자가 다른 회사의
    리포트를 자기 것으로 착각한다.
    """
    v = value.strip()
    if re.fullmatch(r"\d{6}", v):
        return v
    hits = _search_provider().search_companies(v, limit=6)
    if not hits:
        raise ValueError(f"'{v}'에 해당하는 상장사를 찾지 못했습니다.")
    if len(hits) > 1 and hits[0]["name"] != v:
        names = ", ".join(f"{h['name']}({h['symbol']})" for h in hits)
        raise ValueError(f"'{v}'와 일치하는 회사가 여럿입니다 — 하나를 골라 주세요: {names}")
    return hits[0]["symbol"]


def _parse_overrides(raw: str) -> dict[str, float]:
    """`key=value` 줄바꿈/콤마 목록 → 가정. 잘못된 줄은 무시하지 않고 알린다."""
    out: dict[str, float] = {}
    for chunk in raw.replace(",", "\n").splitlines():
        chunk = chunk.strip()
        if not chunk:
            continue
        key, _, value = chunk.partition("=")
        if not value:
            raise ValueError(f"가정 형식은 key=value 입니다: {chunk!r}")
        try:
            out[key.strip()] = float(value)
        except ValueError:
            raise ValueError(f"가정 값이 숫자가 아닙니다: {chunk!r}") from None
    return out


# ── 비동기 생성 (진행 표시) ──────────────────────────────────────────
@app.post("/api/jobs")
def api_start_job(payload: dict):
    """생성을 백그라운드로 시작하고 job_id를 준다.

    폼 POST를 그대로 두는 이유는 **JS가 없어도 동작해야** 하기 때문이다.
    이 경로는 진행 표시를 위한 향상(progressive enhancement)이다.
    """
    symbol = str(payload.get("symbol", "")).strip()
    year = int(payload.get("year", 2025))
    period = str(payload.get("period", "ANNUAL"))
    use_llm = bool(payload.get("llm", False))
    search = bool(payload.get("search", False))
    prior_markdown = str(payload.get("prior_markdown", "") or "")
    prior_name = str(payload.get("prior_name", "") or "")
    publish = bool(payload.get("publish", False))
    try:
        symbol = _resolve_symbol(symbol)
        overrides = _parse_overrides(str(payload.get("assume", "")))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    # **같은 보고서를 두 번 만들지 않는다** (D51). RA는 한 종목에 초안 두
    # 개를 동시에 굴리지 않는다 — 보드에 같은 카드가 둘이면 어느 쪽이 최신인지
    # 알 수 없고, 고쳐 놓은 쪽을 버리게 된다. 이미 있으면 그 카드를 돌려준다.
    cards = _open_cards()
    if cards is not None:
        for existing in cards.list():
            if (
                existing.symbol == symbol
                and existing.year == year
                and existing.period == period
                and existing.column != HANDOFF
            ):
                return JSONResponse(
                    {
                        "error": "같은 보고서로 만든 초안이 이미 있습니다.",
                        "existing_card_id": existing.id,
                        "existing_column": existing.column,
                    },
                    status_code=409,
                )

    # 카드를 **먼저** 만든다. 생성이 30초 걸려도 보드에는 바로 나타나야
    # "입력 → 대기 → 툭"을 벗어난다 — 사람은 기다리지 않고 다른 일을 한다.
    card_id = ""
    if cards is not None:
        card_id = cards.new_id()
        cards.save(
            Card(
                id=card_id,
                symbol=symbol,
                year=year,
                period=period,
                created_at=now_iso(),
                column=DRAFT,
                running=True,
                # **이름을 먼저 채운다.** 생성이 30초 걸리는데 그동안 보드에
                # 종목코드만 떠 있으면 무슨 카드인지 알 수 없다. corpCode가
                # 프로세스에 캐시돼 있어 실측 52~115ms다.
                company=_company_name(symbol),
            )
        )

    def work(job):
        try:
            vm, doc = _generate(
                symbol,
                year,
                period=period,
                use_llm=use_llm,
                search=search,
                prior_markdown=prior_markdown,
                prior_name=prior_name,
                overrides=overrides,
                publish=publish,
                on_progress=job.emit,
            )
        except Exception as exc:
            # 실패한 카드가 `running`에 영영 남으면 보드가 거짓말을 한다.
            # 확인 필요로 내려놓고 예외는 그대로 올려 작업 기록에 남긴다.
            failed = ViewModel(symbol=symbol, year=year, error=f"{type(exc).__name__}: {exc}")
            _land_card(cards, card_id, failed, {}, published=False)
            raise
        _land_card(cards, card_id, vm, doc, published=publish)
        return vm

    return {"job_id": JOBS.start(work).id, "card_id": card_id}


def _land_card(
    cards: CardStore | None, card_id: str, vm: ViewModel, doc: dict, *, published: bool
) -> None:
    """생성이 끝난 카드를 칸에 놓는다. **자동 판정한다** (store/cards.py 참조)."""
    if cards is None or not card_id:
        return
    card = cards.get(card_id)
    if card is None:
        return
    data = vm.__dict__
    card.vm = data
    card.assembled = doc.get("assembled", "")
    card.registry = doc.get("registry", [])
    card.estimate_snapshot = doc.get("estimate_snapshot", {})
    card.note_facts = doc.get("note_facts", [])
    card.prior_note = doc.get("prior_note", {})
    card.company = vm.company
    card.error = vm.error
    card.attention = attention_reasons(data)
    card.running = False
    card.column = column_for(data, confirmed=card.confirmed, published=bool(vm.published_path))
    cards.save(card)


@app.get("/api/jobs/{job_id}/events")
async def api_job_events(job_id: str):
    """진행 단계를 SSE로 흘려보낸다."""
    import asyncio

    job = JOBS.get(job_id)
    if job is None:
        return JSONResponse({"error": "없는 작업입니다."}, status_code=404)

    async def stream():
        sent = 0
        while True:
            for key, message in job.snapshot(sent):
                sent += 1
                yield f"event: step\ndata: {json.dumps({'key': key, 'message': message}, ensure_ascii=False)}\n\n"
            if job.done:
                payload = {"ok": not job.error, "error": job.error}
                yield f"event: done\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                return
            await asyncio.sleep(0.25)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        # 프록시가 버퍼링하면 진행 표시가 한꺼번에 도착한다
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/jobs/{job_id}/result")
def api_job_result(job_id: str):
    """완료된 작업의 ViewModel을 JSON으로.

    SSE가 `done`을 알린 뒤 화면이 이걸 읽어 간다. `/api/reports`로는 대신할 수
    없다 — 그쪽은 동기라 생성이 끝날 때까지 30~40초를 붙들고, 그러면 진행
    표시를 붙인 이유가 사라진다.

    아직 끝나지 않은 작업에 200을 주면 화면이 빈 결과를 결과로 받는다.
    상태를 구분해서 알린다.
    """
    job = JOBS.get(job_id)
    if job is None:
        # TTL(30분)이 지나 정리됐을 수도 있다 — 화면이 다시 생성하도록 안내한다
        return JSONResponse({"error": "없는 작업입니다. 다시 생성해 주세요."}, status_code=404)
    if not job.done:
        return JSONResponse({"error": "아직 생성 중입니다."}, status_code=409)
    if job.error:
        return JSONResponse({"error": job.error}, status_code=400)
    if job.result is None:
        return JSONResponse({"error": "결과가 비어 있습니다."}, status_code=500)
    return JSONResponse(job.result.__dict__)


# ── API ──────────────────────────────────────────────────────────────
@app.post("/api/reports")
def api_reports(payload: dict):
    """리포트 생성. 화면과 **같은 경로**를 탄다."""
    try:
        vm, _doc = _generate(
            str(payload.get("symbol", "")).strip(),
            int(payload.get("year", 2025)),
            use_llm=bool(payload.get("llm", False)),
            overrides={k: float(v) for k, v in (payload.get("assumptions") or {}).items()},
            publish=bool(payload.get("publish", False)),
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=400)
    return JSONResponse(vm.__dict__)


@app.get("/api/search")
def api_search(q: str = "", limit: int = 10):
    """회사명·종목코드 검색. 종목코드를 외우지 않아도 되게 한다."""
    try:
        return {"results": _search_provider().search_companies(q, limit)}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}", "results": []}, 503)


@app.get("/api/company/{symbol}")
def api_company(symbol: str):
    """회사 한 줄 — 이름·종목코드·**시장 구분**.

    코스피·코스닥·코넥스는 읽는 사람에게 전혀 다른 맥락이다(유동성·공시 수준·
    커버리지). 고른 직후에 보여주지 않으면 다 만들고 나서야 알게 된다.

    `corpCode.xml`에는 시장 구분이 없어 `company.json`을 한 번 친다.
    """
    try:
        c = _shared_provider().get_company(symbol.strip())
    except Exception as exc:  # noqa: BLE001 — 화면에 원인을 보여주는 게 목적이다
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=404)
    return {"symbol": c.symbol, "name": c.name, "market": c.market.value}


@app.get("/api/company/{symbol}/reports")
def api_company_reports(symbol: str, back_days: int = 900):
    """이 회사에 **실제로 올라와 있는** 정기보고서.

    기한으로 계산한 목록은 추측이다 — 결산월이 다르고, 일찍 낼 수도 늦을 수도
    있고, 아예 없을 수도 있다. DART에 물어보면 목록이 사실이 되고 접수일·
    접수번호가 함께 온다.

    **잠정실적도 함께 준다.** 우리는 아직 그걸 읽지 못하지만, 더 최신 실적이
    이미 나와 있다는 사실은 알려줘야 한다 — 모르고 옛 보고서로 쓰는 것과
    알고도 그걸 쓰는 것은 다르다 (corpus/FINDINGS.md).
    """
    from arc.data.kr.filings import periodic_filings, unread_preliminary

    end = dt.datetime.now(dt.UTC).date()
    start = end - dt.timedelta(days=max(90, min(back_days, 1800)))
    # **종류를 좁혀서 받는다.** 안 좁히면 삼성전자가 39페이지 8초다.
    #   A 정기공시   — 사업·반기·분기보고서
    #   I 거래소공시 — 잠정실적이 여기 있다
    p = _shared_provider()
    sym = symbol.strip()
    try:
        periodic_src = p.get_disclosures(sym, start, end, pblntf_ty="A")
        market_src = p.get_disclosures(sym, start, end, pblntf_ty="I")
    except Exception as exc:  # noqa: BLE001 — 화면에 원인을 보여주는 게 목적이다
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=502)
    ds = periodic_src + market_src

    return {
        "periodic": [
            {
                "year": f.year,
                "period": f.period.value,
                "label": f.label,
                "title": f.title,
                "filed_at": f.filed_at.isoformat(),
                "rcept_no": f.rcept_no,
                "url": f.url,
            }
            for f in periodic_filings(ds)
        ],
        # **정기보고서보다 나중에 나온** 잠정실적만. 그때만 RA가 우리보다
        # 최신 숫자를 갖고 있다 — 그냥 있다고 알리면 거짓말이 된다.
        "preliminary": [
            {
                "title": d.title,
                "filed_at": d.filed_at.isoformat(),
                "url": d.provenance.verify_url or "",
            }
            for d in ([unread_preliminary(ds)] if unread_preliminary(ds) else [])
        ],
    }


@app.post("/api/convert")
async def api_convert(request: Request):
    """업로드 문서 → 마크다운. **저장하지 않고 돌려만 준다.**

    변환 결과를 사람이 먼저 본다. 오래된 PDF는 글자가 부분적으로 깨져 나올 수
    있는데(실측: 2009년 리포트에서 「매출액」이 「매춗액」으로) 통계로는 정상
    문서와 안 갈린다. 자동 판정을 붙여 거짓 경고를 내느니 **보고 넘기게** 한다.
    """
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        return JSONResponse({"error": "파일이 없습니다."}, status_code=400)
    data = await upload.read()
    try:
        got = convert(data, getattr(upload, "filename", "") or "upload")
    except ConvertError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    # **회사를 먼저 읽어 준다.** 올리자마자 종목코드를 다시 치게 하면 업로드가
    # 편의가 아니라 일이 하나 는 것이다. 실측 적중 92%라 **사람이 확인**한다.
    company: dict | None = None
    try:
        provider = _shared_provider()
        symbol = detect_symbol(got.markdown, frozenset(provider.load_corp_codes()))
        if symbol:
            c = provider.get_company(symbol)
            company = {
                "symbol": symbol,
                "name": c.name,
                "short_name": c.short_name or c.name,
                "market": c.market.value,
            }
    except Exception as exc:  # noqa: BLE001 — 못 읽어도 변환은 성립한다
        log.warning("업로드 문서에서 종목을 읽지 못했습니다: %s", exc)

    return {
        "markdown": got.markdown,
        "source_name": got.source_name,
        "kind": got.kind,
        "pages": got.pages,
        "chars": got.chars,
        "warnings": got.warnings,
        "outline": outline_of(got.markdown),
        "company": company,
    }


@app.get("/api/cards/{card_id}.md", response_class=PlainTextResponse)
def api_card_markdown(card_id: str):
    """노트를 **마크다운 원문**으로. 화면 주소에 `.md`를 붙이면 나온다.

    이 제품이 내는 것이 원래 마크다운이다. 사람이 그걸 그대로 가져가 자기
    도구에 붙일 수 있어야 한다 — 발간 파일을 찾아 들어가지 않고.
    """
    _, card, err = _load_card(card_id)
    if err is not None:
        return PlainTextResponse("찾을 수 없습니다.", status_code=404)
    if not card.assembled:
        return PlainTextResponse("아직 본문이 없습니다.", status_code=409)
    registry = NumberRegistry.load(card.registry)
    return registry.render_text(card.assembled)


def _download_name(card, ext: str) -> str:
    """`삼성물산_028260_FY2025_2026-08-06.md`. **사람이 찾을 수 있는 이름**이다.

    회사명이 앞이라 파일 목록에서 눈으로 찾히고, 종목코드가 있어 검색되고,
    날짜가 뒤라 같은 종목이 시간순으로 선다.
    """
    name = re.sub(r'[\\/:*?"<>|]', "", (card.company or card.symbol)).strip() or card.symbol
    stamp = (card.created_at or "")[:10] or dt.datetime.now(dt.UTC).date().isoformat()
    return f"{name}_{card.symbol}_FY{card.year}_{stamp}.{ext}"


@app.get("/api/cards/{card_id}/download")
def api_download(card_id: str, format: str = "md"):
    """노트를 파일로 내려받는다 — 마크다운 또는 Word.

    **증권사에서 리포트가 오가는 형식은 Word다.** 초안을 애널리스트에게 넘길
    때([D51](../../docs/decisions.md#d51)의 「넘김」) 마크다운을 주면 받는
    쪽이 다시 변환해야 한다.
    """
    _, card, err = _load_card(card_id)
    if err is not None:
        return err
    if not card.assembled:
        return JSONResponse({"error": "아직 본문이 없습니다."}, status_code=409)

    rendered = NumberRegistry.load(card.registry).render_text(card.assembled)
    if format == "md":
        return Response(
            rendered.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers=_attachment(_download_name(card, "md")),
        )
    if format == "xlsx":
        try:
            data = note_to_xlsx(
                card.registry,
                company=card.company,
                symbol=card.symbol,
                market=str(card.vm.get("market") or ""),
                basis=str(card.vm.get("basis") or ""),
                period_label=f"{card.year} {card.period}",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("엑셀 변환 실패 (%s): %s", card_id, exc)
            return JSONResponse({"error": f"엑셀 변환에 실패했습니다: {exc}"}, status_code=500)
        return Response(
            data,
            media_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            headers=_attachment(_download_name(card, "xlsx")),
        )
    if format == "docx":
        try:
            data = markdown_to_docx(rendered, title=f"{card.company} {card.year}")
        except Exception as exc:  # noqa: BLE001 — 변환 실패가 발간을 되돌리진 않는다
            log.warning("Word 변환 실패 (%s): %s", card_id, exc)
            return JSONResponse({"error": f"Word 변환에 실패했습니다: {exc}"}, status_code=500)
        return Response(
            data,
            media_type=("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            headers=_attachment(_download_name(card, "docx")),
        )
    return JSONResponse({"error": "md · docx · xlsx만 됩니다."}, status_code=400)


def _attachment(filename: str) -> dict[str, str]:
    """한글 파일명은 `filename*`(RFC 5987)로 보내야 안 깨진다."""
    quoted = urllib.parse.quote(filename)
    return {"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"}


@app.post("/api/cards/{card_id}/fill-model")
async def api_fill_model(card_id: str, request: Request):
    """올린 **엑셀 모델**에 이 카드의 공시 실적을 채워 돌려준다 (D62).

    **남의 파일에 쓰는 일이다.** 수식 셀은 건드리지 않고, 원본을 고치지 않고
    사본을 주며, 무엇을 어디에 썼는지 전부 돌려준다.

    `unit`은 모델의 단위 — 백만원 모델이면 `1000000`. 기본은 원 단위다.
    """
    _, card, err = _load_card(card_id)
    if err is not None:
        return err
    if not card.registry:
        return JSONResponse({"error": "이 카드에는 수치가 없습니다."}, status_code=409)

    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        return JSONResponse({"error": "엑셀 파일이 없습니다."}, status_code=400)
    raw = await upload.read()
    name = getattr(upload, "filename", "") or "model.xlsx"
    if not name.lower().endswith((".xlsx", ".xlsm")):
        return JSONResponse(
            {"error": "엑셀(.xlsx · .xlsm)만 됩니다. 구형 .xls는 변환해 주십시오."},
            status_code=400,
        )
    try:
        unit = float(form.get("unit") or 1)
    except (TypeError, ValueError):
        unit = 1.0

    values, _, _ = collect_series(card.registry)
    try:
        got = fill_model(raw, values, unit=unit or 1.0)
    except Exception as exc:  # noqa: BLE001 — 손상된 파일 형태가 다양하다
        log.warning("모델 채우기 실패 (%s): %s", card_id, exc)
        return JSONResponse(
            {"error": f"엑셀을 열지 못했습니다: {type(exc).__name__}"}, status_code=400
        )

    if not got.usable:
        return JSONResponse(
            {
                "error": "채울 자리를 찾지 못했습니다. 행 라벨(매출액·영업이익 등)과 "
                "연도 머리행(2025A 등)이 있는 시트인지 확인해 주십시오.",
                "sheets": got.sheets_scanned,
            },
            status_code=422,
        )

    # 채운 내역을 헤더로 함께 준다 — 파일만 주면 무엇이 바뀌었는지 알 수 없다.
    summary = json.dumps(
        {
            "written": [
                {
                    "sheet": w.sheet,
                    "cell": w.cell,
                    "label": w.label,
                    "year": w.year,
                    "before": w.before,
                    "after": w.after,
                }
                for w in got.written
            ],
            "skipped": [
                {"sheet": s.sheet, "cell": s.cell, "label": s.label, "reason": s.reason}
                for s in got.skipped
            ],
        },
        ensure_ascii=False,
    )
    headers = _attachment(name.rsplit(".", 1)[0] + "_ARC업데이트.xlsx")
    headers["X-Arc-Fill-Summary"] = urllib.parse.quote(summary)
    return Response(
        got.data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@app.get("/api/health")
def api_health():
    """플랫폼 헬스체크용. **인증 없이 열려 있다** (auth.PUBLIC_PATHS)."""
    return {
        "status": "ok",
        # **배포된 것이 최신인지 알 방법이 있어야 한다.** 실서버에서 어떤
        # 코드가 도는지 모르면 「고쳤는데 그대로다」의 원인을 못 가른다.
        # Railway가 주입하는 값이고, 로컬에서는 비어 있다.
        "commit": (os.environ.get("RAILWAY_GIT_COMMIT_SHA") or "")[:7],
        "deployed_at": os.environ.get("RAILWAY_DEPLOYMENT_ID", "")[:8],
        "dart_key": bool(os.environ.get("DART_API_KEY")),
        "llm_key": bool(os.environ.get("OPENAI_API_KEY")),
        # 기사 검색 체크박스를 켤 수 있는가. 없으면 화면이 이유를 적는다.
        "news_key": news_available(),
        "auth": bool(os.environ.get("ARC_PASSWORD")),
        "llm_used": LLM_BUDGET.used,
        "llm_limit": LLM_BUDGET.limit,
        # 볼륨이 붙었는지. false면 생성은 되지만 revision 추적이 죽는다.
        "store": _store_status(),
    }


# ── 보드 (작업 중인 리포트 = 카드) ───────────────────────────────────
@app.get("/api/cards")
def api_cards():
    """보드 목록. **본문은 빼고 준다** — 카드 하나에 60KB가 붙어 있다."""
    cards = _open_cards()
    if cards is None:
        return {"cards": [], "note": "저장소를 열 수 없어 이력이 남지 않습니다."}
    out = []
    for c in cards.list():
        s = c.summary()
        if c.kind == PEER:
            # 피어 카드는 구성원 상태가 곧 카드 상태다. 저장된 값이 아니라
            # 지금 저장소를 보고 말한다 — 종목 카드를 방금 만들었으면 그게
            # 바로 반영돼야 한다.
            resolved = resolve_peer_members(cards, c.members)
            s["attention"] = peer_attention_reasons(resolved)
            s["member_ready"] = sum(1 for m in resolved if m["status"] == "ready")
        out.append(s)
    return {"cards": out}


@app.get("/api/cards/{card_id}")
def api_card(card_id: str):
    cards = _open_cards()
    if cards is None:
        return JSONResponse({"error": "저장소를 열 수 없습니다."}, status_code=503)
    try:
        card = cards.get(card_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if card is None:
        return JSONResponse({"error": "없는 카드입니다."}, status_code=404)
    body = dict(card.__dict__, vm=_complete_vm(card.vm))
    if card.kind == PEER:
        resolved = resolve_peer_members(cards, card.members)
        table = build_peer_table(resolved)
        body["members"] = [{k: v for k, v in m.items() if k != "registry"} for m in resolved]
        body["peer_table"] = dataclasses.asdict(table)
        body["attention"] = peer_attention_reasons(resolved)
    return body


@app.post("/api/ask")
def api_ask(payload: dict):
    """리서치 채팅 — **카드를 근거로 답하고 출처를 낸다.**

    인터뷰가 준 가장 큰 발견이 여기 있다: *"리포트 쓰는 시간보다 훨씬 많은
    비중은 클라이언트 리퀘스트"* — 하루 10~15건. 일반 챗봇이 아니라 **모르면
    모른다고 하는** 것이 설계 원칙이다. 답을 지어내면 청구가 안 되고, 한 번
    틀리면 다음부터 안 쓴다.

    **대화를 서버가 들고 있지 않다.** 직전 턴이 남긴 `context`(종목·주제·연도
    셋뿐)를 화면이 돌려주고 우리는 그걸 그대로 다음 검색의 앵커로 쓴다.
    세션 상태를 서버에 두면 워커가 늘어나는 순간 대화가 갈라진다 — 그리고
    이 좁은 것만 이월하는 편이 대화 전체를 이고 다니는 것보다 정확하다.
    """
    question = str(payload.get("question", "")).strip()
    if not question:
        return JSONResponse({"error": "질문을 입력하십시오."}, status_code=400)

    cards = _open_cards()
    if cards is None:
        return JSONResponse({"error": "저장소를 열 수 없습니다."}, status_code=503)

    raw = payload.get("context") or {}
    context = (
        Context(
            symbols=tuple(str(s) for s in (raw.get("symbols") or [])),
            tokens=tuple(str(t) for t in (raw.get("tokens") or [])),
            year=raw.get("year") if isinstance(raw.get("year"), int) else None,
        )
        if isinstance(raw, dict) and raw
        else None
    )

    if not LLM_BUDGET.take():
        return JSONResponse(
            {"error": f"LLM 호출 한도({LLM_BUDGET.limit}건)에 도달했습니다."}, status_code=429
        )
    from arc.llm.client import get_client

    # 기사 레인은 **켤 수 있으면 켠다.** 없으면 답변이 그 사실을 `problems`에
    # 남기고 앞의 두 레인은 그대로 돈다.
    news = _news_by_name if news_available() else None
    try:
        answer = answer_question(
            question,
            [c for c in cards.list() if c.kind == SINGLE],
            client=get_client(),
            news=news,
            context=context,
        )
    except Exception as exc:
        # 채팅 실패가 화면을 죽이지 않는다.
        log.exception("답변 생성 실패")
        return JSONResponse(
            {"error": f"답변을 만들지 못했습니다 — {type(exc).__name__}"}, status_code=500
        )
    return dataclasses.asdict(answer)


def _price_source() -> tuple[dict, str]:
    """피어 후보가 딛고 설 시세. **받아 둔 것을 먼저 본다.**

    개발 기기에는 `corpus/consensus/prices/`가 있지만 배포에는 없고(gitignore)
    네이버에서 받은 것이라 재배포도 안 된다(D67). 그래서 금융위 API로 받아
    `.arc-store/prices`에 쌓은 것을 정본으로 쓰고, 없을 때만 코퍼스로 떨어진다.
    """
    from arc.finmodel.price_store import available, store_dir

    if available(STORE_DIR):
        return load_prices(store_dir(STORE_DIR)), "store"
    corpus = REPO_ROOT / "corpus" / "consensus" / "prices"
    if corpus.is_dir():
        return load_prices(corpus), "corpus"
    return {}, "none"


def _names_for(symbols: list[str]) -> dict[str, str]:
    """종목코드 → 회사명. **corpCode 한 번으로 전부 푼다.**

    `_company_name()`을 후보마다 부르면 종목당 API 호출이 하나씩 나간다 —
    15종목이면 15콜이고, 그게 D69의 차단을 부른 종류의 일이다.
    """
    try:
        table = _shared_provider().load_corp_codes()
    except Exception as exc:  # noqa: BLE001 — 이름은 표시용이라 없어도 된다
        log.warning("회사명을 못 읽었습니다: %s", exc)
        return {}
    out: dict[str, str] = {}
    for s in symbols:
        row = table.get(s) or {}
        out[s] = str(row.get("stock_name") or row.get("corp_name") or "")
    return out


@app.post("/api/peers/suggest")
def api_suggest_peers(payload: dict):
    """씨앗 종목과 **같이 움직이는** 종목을 낸다.

    업종 분류로는 안 된다 — 방산 4종목이 KSIC 어느 자릿수에서도 한 그룹이
    되지 않는다([D68](../../docs/decisions.md#d68)). 대신 인터뷰의 말을 그대로
    쓴다: *"우리 커버리지랑 같이 움직이는 종목 골라줘"*.

    **확정하지 않는다.** 여기서 나오는 것은 후보이고, 사람이 고른 것만 카드에
    박힌다. 상관은 산업이 아니라 지금 같이 움직이는 테마를 찾으므로(현대건설
    씨앗 → 원전 테마) 상관계수를 함께 내서 사람이 판단하게 한다.
    """
    raw = payload.get("seeds") or []
    if not isinstance(raw, list) or not raw:
        return JSONResponse({"error": "씨앗 종목을 하나 이상 지정하십시오."}, status_code=400)
    seeds: list[str] = []
    for item in raw[:4]:
        try:
            seeds.append(_resolve_symbol(str(item).strip()))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    prices, source = _price_source()
    if not prices:
        return JSONResponse(
            {
                "error": "시세를 아직 받지 않았습니다.",
                "hint": "`arc prices backfill`로 일별 시세를 받은 뒤 다시 시도하십시오.",
            },
            status_code=503,
        )

    missing = [s for s in seeds if s not in prices]
    if missing:
        return JSONResponse(
            {"error": f"시세가 없는 종목입니다 — {', '.join(missing)}"}, status_code=400
        )

    top = payload.get("top")
    group = suggest_group(seeds, prices, top=int(top) if isinstance(top, int) else 15)
    names = _names_for([c.symbol for c in group.candidates] + seeds)
    return {
        "seeds": [{"symbol": s, "company": names.get(s, "")} for s in seeds],
        "candidates": [
            {
                "symbol": c.symbol,
                "company": names.get(c.symbol, ""),
                "correlation": round(c.correlation, 3),
                "overlap": c.overlap,
            }
            for c in group.candidates
        ],
        "meaningful": group.meaningful,
        "cohesion": round(group.cohesion, 3),
        "note": group.note,
        "universe": len(prices),
        "source": source,
    }


@app.post("/api/peers")
def api_create_peer(payload: dict):
    """피어 카드를 만든다 — 여러 종목을 한 표로.

    **여기서 종목 카드를 만들지 않는다.** 구성원은 저장소에 이미 있는 종목
    카드를 읽을 때마다 찾아 붙인다(`resolve_peer_members`). 없는 종목은
    `pending`으로 남고, 사람이 평소대로 그 종목 카드를 만들면 다음에 열 때
    표에 들어와 있다.

    생성까지 여기서 떠맡으면 「N종목 동시 생성」이라는 다른 기능이 되고,
    실패·중복·비용 처리를 전부 다시 만들게 된다. 이미 `/api/jobs`가 그걸 한다.
    """
    name = str(payload.get("name", "")).strip()
    raw = payload.get("symbols") or []
    if not isinstance(raw, list) or not raw:
        return JSONResponse({"error": "종목을 하나 이상 지정하십시오."}, status_code=400)
    if len(raw) > 12:
        # 표의 열이 열둘을 넘으면 화면에서 못 읽는다. 코퍼스의 피어 표도
        # 대개 4~8종목이다.
        return JSONResponse({"error": "한 표에 12종목까지 세울 수 있습니다."}, status_code=400)

    codes: list[str] = []
    seen: set[str] = set()
    for item in raw:
        try:
            symbol = _resolve_symbol(str(item).strip())
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if symbol in seen:
            continue
        seen.add(symbol)
        codes.append(symbol)

    # **상장 종목명을 쓴다.** 법인명은 DART가 영문을 한글로 음차해 둬서
    # 「엘아이지디펜스앤에어로스페이스(주)」가 되고, 표의 열 머리로 못 쓴다.
    # 그리고 corpCode 한 번으로 전부 풀어 종목당 API 호출을 없앤다(D69).
    names = _names_for(codes)
    members = [peer_member(c, company=names.get(c, "")) for c in codes]

    cards = _open_cards()
    if cards is None:
        return JSONResponse({"error": "저장소를 열 수 없습니다."}, status_code=503)

    card = Card(
        id=cards.new_id(),
        symbol="",  # 피어 카드는 종목 하나에 매이지 않는다
        year=0,
        created_at=now_iso(),
        column=DRAFT,
        kind=PEER,
        company=name
        or f"{members[0]['company'] or members[0]['symbol']} 외 {len(members) - 1}종목",
        members=members,
    )
    cards.save(card)
    return {"card_id": card.id, "members": card.members}


@app.post("/api/cards/{card_id}/members")
def api_set_peer_members(card_id: str, payload: dict):
    """피어 카드의 구성원을 바꾼다 — 종목을 넣거나 뺀다."""
    cards = _open_cards()
    if cards is None:
        return JSONResponse({"error": "저장소를 열 수 없습니다."}, status_code=503)
    try:
        card = cards.get(card_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if card is None:
        return JSONResponse({"error": "없는 카드입니다."}, status_code=404)
    if card.kind != PEER:
        return JSONResponse({"error": "피어 카드가 아닙니다."}, status_code=400)

    raw = payload.get("symbols")
    if not isinstance(raw, list):
        return JSONResponse({"error": "종목 목록이 필요합니다."}, status_code=400)
    if len(raw) > 12:
        return JSONResponse({"error": "한 표에 12종목까지 세울 수 있습니다."}, status_code=400)

    codes: list[str] = []
    seen: set[str] = set()
    for item in raw:
        try:
            symbol = _resolve_symbol(str(item).strip())
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if symbol in seen:
            continue
        seen.add(symbol)
        codes.append(symbol)

    # **상장 종목명을 쓴다.** 법인명은 DART가 영문을 한글로 음차해 둬서
    # 「엘아이지디펜스앤에어로스페이스(주)」가 되고, 표의 열 머리로 못 쓴다.
    # 그리고 corpCode 한 번으로 전부 풀어 종목당 API 호출을 없앤다(D69).
    names = _names_for(codes)
    members = [peer_member(c, company=names.get(c, "")) for c in codes]

    card.members = members
    cards.save(card)
    resolved = resolve_peer_members(cards, card.members)
    return {
        "members": [{k: v for k, v in m.items() if k != "registry"} for m in resolved],
        "attention": peer_attention_reasons(resolved),
    }


@app.post("/api/cards/{card_id}/confirm")
def api_confirm_card(card_id: str):
    """「확인함」 — 확인 필요를 벗어난다.

    **옮기는 노동을 만들지 않는다.** 칸 배정은 자동이고, 사람은 "봤다"만
    남긴다. 수동으로 임의의 칸에 옮기는 것은 열어뒀지만 아직 만들지 않았다
    (D40) — 자동 판정이 얼마나 틀리는지 보고 붙이는 편이 낫다.
    """
    cards = _open_cards()
    if cards is None:
        return JSONResponse({"error": "저장소를 열 수 없습니다."}, status_code=503)
    try:
        card = cards.get(card_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if card is None:
        return JSONResponse({"error": "없는 카드입니다."}, status_code=404)
    card.confirmed = True
    card.column = column_for(card.vm, confirmed=True, published=bool(card.vm.get("published_path")))
    cards.save(card)
    return card.summary()


def _complete_vm(vm: dict) -> dict:
    """저장된 ViewModel에 **지금 ViewModel의 빈 기본값을 채워** 돌려준다.

    카드는 만들어진 시점의 `vm`을 그대로 들고 있다. 그 뒤에 필드를 추가하면
    **옛 카드에는 그 필드가 없고**, 화면이 `vm.areas.length`를 읽다가 터져
    브라우저가 「This page couldn't load」를 띄운다 — 두 번 밟았다(D63, D65).

    필드를 추가할 때마다 화면에 `?.`를 하나씩 다는 것은 못 지킨다. **서버가
    항상 완전한 모양을 준다**고 정하면 그 종류의 버그가 끝난다.
    """
    if not vm:
        return vm
    full = dataclasses.asdict(ViewModel(symbol="", year=0))
    return {**full, **vm}


def _load_card(card_id: str):
    """카드 + 저장소. 실패하면 (None, 응답)을 준다."""
    cards = _open_cards()
    if cards is None:
        return None, None, JSONResponse({"error": "저장소를 열 수 없습니다."}, status_code=503)
    try:
        card = cards.get(card_id)
    except ValueError as exc:
        return None, None, JSONResponse({"error": str(exc)}, status_code=400)
    if card is None:
        return None, None, JSONResponse({"error": "없는 카드입니다."}, status_code=404)
    return cards, card, None


@app.get("/api/cards/{card_id}/sections")
def api_card_sections(card_id: str):
    """섹션 목록 + **원문**(플레이스홀더 살아 있음).

    원문을 함께 주는 이유는 직접 편집 때문이다. 사람이 본문을 직접 고칠 때
    숫자를 그냥 타이핑하면 G0가 막는데, 그 순간 불변식이 처음으로 **사람에게
    보인다** — "이 숫자는 레지스트리에 없다".

    잠긴 섹션도 함께 준다. 왜 못 고치는지도 정보다.
    """
    from arc.llm.revise import split_sections

    _, card, err = _load_card(card_id)
    if err is not None:
        return err
    return {
        "version": card.version,
        "sections": [
            {"title": s.title, "editable": s.editable, "chars": len(s.text), "text": s.text}
            for s in split_sections(card.assembled)
        ],
    }


@app.post("/api/cards/{card_id}/revise")
def api_revise(card_id: str, payload: dict):
    """코멘트대로 한 섹션을 고쳐 쓴다. **제안일 뿐 채택되지 않는다.**

    LLM은 플레이스홀더만 쓰고 값은 프롬프트에 들어가지 않으므로, 이 호출이
    문서를 고쳐도 **숫자는 구조적으로 바뀔 수 없다.** diff는 문장에만 생긴다.
    """
    from arc.llm.revise import find_section, revise_section

    _, card, err = _load_card(card_id)
    if err is not None:
        return err

    title = str(payload.get("section", "")).strip()
    comment = str(payload.get("comment", "")).strip()
    if not comment:
        return JSONResponse({"error": "코멘트가 비어 있습니다."}, status_code=400)

    section = find_section(card.assembled, title)
    if section is None:
        return JSONResponse({"error": f"없는 섹션입니다: {title!r}"}, status_code=404)
    if not section.editable:
        return JSONResponse(
            {"error": f"「{title}」은(는) 규칙이 지키는 자리라 고칠 수 없습니다."}, status_code=400
        )

    if not LLM_BUDGET.take():
        return JSONResponse(
            {"error": f"LLM 호출 한도({LLM_BUDGET.limit}건)에 도달했습니다."}, status_code=429
        )
    from arc.llm.client import get_client

    p = revise_section(
        get_client(),
        section=title,
        section_label=title,
        before=section.text,
        comment=comment,
        registry=NumberRegistry.load(card.registry),
    )
    return {
        "section": title,
        "comment": comment,
        "before": p.before,
        "after": p.after,
        "changed": p.changed,
        "numbers_unchanged": p.numbers_unchanged,
        "numbers": p.numbers_after,
        "problems": p.problems,
        "used_llm": p.used_llm,
        "model": p.model,
        "cost_usd": p.cost_usd,
    }


@app.post("/api/cards/{card_id}/accept")
def api_accept_revision(card_id: str, payload: dict):
    """제안을 채택한다 → 버전이 올라간다.

    **채택 전에 G0를 다시 돌린다.** 게이트를 건너뛰면 리뷰 루프가 불변식을
    우회하는 뒷문이 된다.
    """
    from arc.llm.revise import find_section, splice
    from arc.verify.g0 import G0Gate

    cards, card, err = _load_card(card_id)
    if err is not None:
        return err

    title = str(payload.get("section", "")).strip()
    after = str(payload.get("after", ""))
    comment = str(payload.get("comment", ""))
    section = find_section(card.assembled, title)
    if section is None:
        return JSONResponse({"error": f"없는 섹션입니다: {title!r}"}, status_code=404)
    if not section.editable:
        return JSONResponse({"error": "고칠 수 없는 섹션입니다."}, status_code=400)

    registry = NumberRegistry.load(card.registry)
    candidate = splice(card.assembled, section, after)
    gate = G0Gate(registry).check(candidate)
    if not gate.passed:
        # 막힌 수정은 저장하지 않는다. 차단된 본문이 카드에 남으면 다음 수정이
        # 그 위에 쌓인다.
        return JSONResponse(
            {
                "error": "G0가 막았습니다 — 채택하지 않았습니다.",
                "violations": [{"rule": v.rule, "detail": v.detail} for v in gate.violations[:20]],
            },
            status_code=409,
        )

    card.assembled = candidate
    card.vm["body_html"] = render_html(candidate, registry)
    card.vm["gate_passed"] = True
    card.vm["gate_summary"] = gate.summary()
    card.versions.append(
        {
            "version": next_version(card.version),
            "created_at": now_iso(),
            "section": title,
            "comment": comment,
            "before": section.text,
            "after": after,
        }
    )
    card.version = next_version(card.version)
    cards.save(card)
    return {"version": card.version, "revision_count": len(card.versions)}


@app.post("/api/cards/{card_id}/recompute")
def api_recompute(card_id: str, payload: dict):
    """가정을 바꿔 **다시 계산**한다 → 버전이 오른다.

    가정은 초안을 보기 전에 정할 수 없다 — 「최근 2개년 평균이 12.3%였다」를
    모르는 상태에서 15를 넣으라는 것이 이전 폼이었다. 그래서 이 조작은
    카드에 있다 ([D24](../../docs/decisions.md#d24)의 계산/판단 경계).

    문장 수정과 달리 **숫자가 바뀐다.** 그래서 LLM 서술은 버리고 결정론
    문장으로 다시 만든다 — 옛 문장이 새 숫자를 설명한다고 우길 수 없다.
    """
    cards, card, err = _load_card(card_id)
    if err is not None:
        return err

    raw = payload.get("assumptions") or {}
    raw_fwd = payload.get("forward") or []
    try:
        overrides = {str(k): float(v) for k, v in raw.items()}
        forward = [{str(k): float(v) for k, v in (y or {}).items()} for y in raw_fwd]
    except (TypeError, ValueError):
        return JSONResponse({"error": "가정 값은 숫자여야 합니다."}, status_code=400)
    if len(forward) > 4:
        return JSONResponse({"error": "연차는 5년까지만 냅니다."}, status_code=400)

    try:
        vm, doc = _generate(
            card.symbol,
            card.year,
            period=card.period,
            use_llm=False,
            overrides=overrides,
            forward=forward,
        )
    except Exception as exc:  # noqa: BLE001 — 화면에 원인을 보여주는 게 목적이다
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=400)

    changed = [f"{a['label']} {a['value']}{a['unit']}" for a in vm.assumptions if a.get("override")]
    card.vm = vm.__dict__
    card.assembled = doc.get("assembled", "")
    card.registry = doc.get("registry", [])
    card.estimate_snapshot = doc.get("estimate_snapshot", {})
    card.note_facts = doc.get("note_facts", [])
    card.prior_note = doc.get("prior_note", {})
    card.attention = attention_reasons(card.vm)
    card.column = column_for(card.vm, confirmed=card.confirmed, published=False)
    card.versions.append(
        {
            "version": next_version(card.version),
            "created_at": now_iso(),
            "section": "추정 가정",
            "comment": "가정 변경 — " + (", ".join(changed) or "기준선으로 되돌림"),
            "before": "",
            "after": "",
        }
    )
    card.version = next_version(card.version)
    cards.save(card)
    return {"version": card.version, "assumptions": vm.assumptions}


@app.post("/api/cards/{card_id}/publish")
def api_publish(card_id: str):
    """카드를 발간한다.

    **생성과 발간은 다르다** ([D27](../../docs/decisions.md#d27)). 생성은
    미리보기라 이력에 남지 않고, 발간해야 추정이 스냅샷으로 저장돼 다음 발간의
    변화 추적 기준이 된다.

    한때 이 버튼이 초안 작성 폼에 있었다 — 아직 아무것도 안 만들었는데
    「검토 완료」를 누를 수 있었다. 발간은 읽고 고친 **뒤에** 하는 일이라
    카드에 있어야 한다.

    **카드의 현재 본문을 그대로 낸다** — 코멘트로 고친 것도, 직접 편집한 것도
    살아서 나간다. 다시 생성하면 그 수정이 전부 사라진다.
    """
    cards, card, err = _load_card(card_id)
    if err is not None:
        return err
    if not card.vm.get("gate_passed"):
        return JSONResponse(
            {"error": "발간 전 점검을 통과하지 못한 초안은 발간할 수 없습니다."}, status_code=409
        )

    published_at = dt.datetime.now(dt.UTC).date()
    registry = NumberRegistry.load(card.registry)
    rendered = registry.render_text(card.assembled)

    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    path = DRAFTS_DIR / f"{card.symbol}-FY{card.year}-{published_at.isoformat()}.md"
    try:
        path.write_text(rendered, encoding="utf-8")
    except OSError as exc:
        return JSONResponse({"error": f"파일을 쓰지 못했습니다: {exc}"}, status_code=500)

    # 추정 이력 — 다음 발간에서 이 값 대비 변화가 표시된다
    snap = card.estimate_snapshot
    store = _open_store()
    if store is not None and snap.get("values"):
        from arc.finmodel.estimates import ESTIMATE_DATASET

        rows = [
            {
                "symbol": card.symbol,
                "fiscal_year": snap["fiscal_year"],
                "base_year": snap["base_year"],
                "metric": metric,
                "value": value,
                "method": snap.get("method", ""),
                "published_at": published_at.isoformat(),
            }
            for metric, value in snap["values"].items()
        ]
        store.save_snapshot(
            ESTIMATE_DATASET,
            rows,
            snapshot_at=dt.datetime.combine(published_at, dt.time.min, tzinfo=dt.UTC),
        )

    # 노트 지문 — 다음 발간에서 「직전 대비 무엇이 달라졌는가」의 기준 (D46).
    # 실패해도 발간은 성립한다. 파일은 이미 나갔다.
    if store is not None and card.note_facts:
        try:
            store.save_snapshot(
                NOTE_DATASET,
                [
                    dict(
                        row,
                        published_at=published_at.isoformat(),
                        # 실제로 쓴 시각. 같은 날 두 번 발간해도 순서가 남는다.
                        saved_at=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                    )
                    for row in card.note_facts
                ],
                snapshot_at=dt.datetime.combine(published_at, dt.time.min, tzinfo=dt.UTC),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("노트 지문을 남기지 못했습니다 (%s): %s", card.symbol, exc)

    card.published_path = str(path)
    card.vm["published_path"] = str(path)
    card.column = HANDOFF
    cards.save(card)
    return {"published_path": str(path), "version": card.version}


@app.delete("/api/cards/{card_id}")
def api_delete_card(card_id: str):
    cards = _open_cards()
    if cards is None:
        return JSONResponse({"error": "저장소를 열 수 없습니다."}, status_code=503)
    try:
        ok = cards.delete(card_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if not ok:
        return JSONResponse({"error": "없는 카드입니다."}, status_code=404)
    return {"deleted": card_id}


# ── 화면 (Next.js 정적 익스포트) ─────────────────────────────────────
#
# **반드시 마지막에 마운트한다.** `/`에 붙으므로 위의 `/api/*`보다 먼저
# 등록되면 API 요청을 정적 파일 조회가 가로챈다.
#
# `html=True`는 디렉터리에서 `index.html`을 찾고 404를 그 파일로 돌려준다 —
# 클라이언트 라우팅에서 새로고침이 깨지지 않게 하는 설정이다.
if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="ui")
else:
    # 프런트를 빌드하지 않고 API만 띄우는 경우가 있다(테스트·CLI 개발).
    # 죽이지 않고 알린다 — API는 그대로 동작한다.
    log.warning(
        "화면 빌드를 찾지 못했습니다 (%s). API만 제공합니다. "
        "`cd web && npm run build` 후 다시 띄우십시오.",
        STATIC_DIR,
    )
