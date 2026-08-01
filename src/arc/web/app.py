"""웹 표면 — 실적 리뷰 노트 작업대 (MVP).

왜 서버 렌더인가
----------------
장기 계획은 Next.js + FastAPI다(ARCHITECTURE.md). 다만 이 화면의 목적은
**검토 흐름을 눈으로 확인하는 것**이지 프론트엔드 아키텍처 검증이 아니다.
서버 렌더로 먼저 세우고, 아래 `/api/*`는 나중에 Next.js가 그대로 호출한다 —
즉 버리는 코드가 아니다.

화면이 증명해야 하는 것
-----------------------
1. **숫자를 클릭하면 출처가 나온다.** Number Registry가 없으면 불가능한
   기능이고, 이게 ChatGPT 대비 유일한 구조적 차별점이다.
2. **게이트가 보인다.** 통과/차단과 위반 내역이 화면에 있어야 "사람이 검토
   후 발간"이 성립한다.
3. **가정을 바꾸면 추정이 바뀐다.** 추정이 가정의 함수라는 걸 조작으로 보여준다.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.templating import Jinja2Templates

from arc.data.kr.dart import DartProvider
from arc.pipeline.earnings_review import ReportResult, build_report, save_estimates
from arc.render.charts import Slice, legend, segment_bar, trend_bars
from arc.render.html import binding_rows, render_html
from arc.store.snapshot import SnapshotStore
from arc.web.auth import BasicAuthMiddleware, LLMBudget
from arc.web.jobs import JobStore

REPO_ROOT = Path(__file__).resolve().parents[3]
# uvicorn은 CLI를 거치지 않고 모듈을 직접 import한다 — 여기서도 키를 읽어야 한다
load_dotenv(REPO_ROOT / ".env")

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))

# 추정 이력 — revision을 보여주려면 직전 **발간**이 있어야 한다
STORE_DIR = Path(os.environ.get("ARC_STORE_DIR", REPO_ROOT / ".arc-store"))
DRAFTS_DIR = REPO_ROOT / "drafts"

app = FastAPI(title="AI Research Center", docs_url="/api/docs")
# 공개 주소에 올릴 때 서버의 LLM 키가 무방비가 되면 안 된다 (auth.py 참조)
app.add_middleware(BasicAuthMiddleware)
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
    segment_chart: str = ""  # 인라인 SVG
    segment_legend: str = ""
    trend_chart: str = ""
    trend_legend: str = ""
    industry_context: bool = False  # 미검증 레인이 있었는가
    llm_used: bool = False
    llm_model: str = ""
    llm_cost: float | None = None
    published_path: str = ""
    notice: str = ""
    error: str = ""


# corpCode.xml은 1.5MB zip이라 요청마다 받으면 검색이 못 쓸 만큼 느려진다.
# 프로세스 수명 동안 하나만 둔다.
_SEARCH_PROVIDER: DartProvider | None = None


def _search_provider() -> DartProvider:
    global _SEARCH_PROVIDER
    if _SEARCH_PROVIDER is None:
        _SEARCH_PROVIDER = DartProvider()
        _SEARCH_PROVIDER.load_corp_codes()
    return _SEARCH_PROVIDER


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
    if seg is not None and seg.usable and len(seg.lines) > 1:
        ordered = sorted(seg.lines, key=lambda x: -x.amount)
        names = [x.name for x in ordered]
        shares = [
            Slice(
                label=x.name, share=x.share if x.share is not None else x.amount / seg.total * 100
            )
            for x in ordered
        ]
        v.segment_chart = segment_bar(shares)
        v.segment_legend = legend(names)

    ms = r.metrics
    years = [str(ms.fiscal_year - 2), str(ms.fiscal_year - 1), str(ms.fiscal_year)]
    trend = []
    for key, label in (("revenue", "매출액"), ("operating_income", "영업이익")):
        mv = ms.values.get(key)
        if mv is None:
            continue
        vals = [float(mv.prior2 or 0), float(mv.prior or 0), float(mv.current or 0)]
        if any(vals):
            trend.append((label, vals))
    if trend:
        v.trend_chart = trend_bars(years, trend)
        v.trend_legend = legend([n for n, _ in trend])

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
    use_llm: bool,
    overrides: dict[str, float],
    publish: bool = False,
    on_progress=None,
) -> ViewModel:
    """노트 생성. **생성과 발간은 다르다.**

    생성은 미리보기다 — 이력에 남지 않는다. 발간해야 추정이 스냅샷으로
    저장되고 다음 발간의 revision 기준이 된다. 생성할 때마다 저장하면
    가정을 만지작거린 흔적이 전부 "직전 추정"이 되어 이력이 무의미해진다.
    """
    provider = DartProvider()
    client = None
    budget_exhausted = False
    if use_llm:
        if LLM_BUDGET.take():
            from arc.llm.client import get_client

            client = get_client()
        else:
            # 상한에 닿으면 **LLM만 끈다.** 화면이 죽는 것보다 수치만 나오는 편이 낫다.
            budget_exhausted = True

    store = SnapshotStore(STORE_DIR)
    published_at = dt.datetime.now(dt.UTC).date()
    r = build_report(
        symbol,
        year,
        provider,
        published_at=published_at,
        llm=client,
        store=store,
        assumptions=overrides or None,
        on_progress=on_progress,
    )
    vm = _to_view(r)
    if budget_exhausted:
        vm.notice = (
            f"LLM 생성 한도({LLM_BUDGET.limit}건)에 도달해 결정론 문장으로 생성했습니다. "
            "수치와 게이트는 동일합니다."
        )

    if publish and r.publishable:
        if r.estimates is not None and r.estimates.usable:
            save_estimates(store, r.estimates, symbol, published_at)
        DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
        path = DRAFTS_DIR / f"{symbol}-FY{year}-{published_at.isoformat()}.md"
        path.write_text(r.rendered or "", encoding="utf-8")
        vm.published_path = str(path)
    return vm


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


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return TEMPLATES.TemplateResponse(
        request, "index.html", {"vm": None, "symbol": "", "year": 2025, "assume": ""}
    )


@app.post("/", response_class=HTMLResponse)
def generate(
    request: Request,
    symbol: str = Form(...),
    year: int = Form(2025),
    llm: bool = Form(False),
    assume: str = Form(""),
    action: str = Form("generate"),
):
    symbol = symbol.strip()
    try:
        symbol = _resolve_symbol(symbol)
        vm = _generate(
            symbol,
            year,
            use_llm=llm,
            overrides=_parse_overrides(assume),
            publish=(action == "publish"),
        )
    except Exception as exc:  # noqa: BLE001 — 화면에 원인을 보여주는 게 목적이다
        vm = ViewModel(symbol=symbol, year=year, error=f"{type(exc).__name__}: {exc}")
    return TEMPLATES.TemplateResponse(
        request, "index.html", {"vm": vm, "symbol": symbol, "year": year, "assume": assume}
    )


# ── 비동기 생성 (진행 표시) ──────────────────────────────────────────
@app.post("/api/jobs")
def api_start_job(payload: dict):
    """생성을 백그라운드로 시작하고 job_id를 준다.

    폼 POST를 그대로 두는 이유는 **JS가 없어도 동작해야** 하기 때문이다.
    이 경로는 진행 표시를 위한 향상(progressive enhancement)이다.
    """
    symbol = str(payload.get("symbol", "")).strip()
    year = int(payload.get("year", 2025))
    use_llm = bool(payload.get("llm", False))
    publish = bool(payload.get("publish", False))
    try:
        symbol = _resolve_symbol(symbol)
        overrides = _parse_overrides(str(payload.get("assume", "")))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    def work(job):
        return _generate(
            symbol,
            year,
            use_llm=use_llm,
            overrides=overrides,
            publish=publish,
            on_progress=job.emit,
        )

    return {"job_id": JOBS.start(work).id}


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


@app.get("/report/{job_id}", response_class=HTMLResponse)
def report_page(request: Request, job_id: str):
    """완료된 작업의 결과 페이지. 렌더는 폼 POST와 **같은 템플릿**을 쓴다."""
    job = JOBS.get(job_id)
    if job is None or not job.done:
        return RedirectResponse("/", status_code=303)
    vm = job.result if not job.error else ViewModel(symbol="", year=0, error=job.error)
    return TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {
            "vm": vm,
            "symbol": getattr(vm, "symbol", ""),
            "year": getattr(vm, "year", 2025),
            "assume": "",
        },
    )


# ── API — Next.js가 그대로 쓸 자리 ───────────────────────────────────
@app.post("/api/reports")
def api_reports(payload: dict):
    """리포트 생성. 화면과 **같은 경로**를 탄다."""
    try:
        vm = _generate(
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


@app.get("/api/health")
def api_health():
    """플랫폼 헬스체크용. **인증 없이 열려 있다** (auth.PUBLIC_PATHS)."""
    return {
        "status": "ok",
        "dart_key": bool(os.environ.get("DART_API_KEY")),
        "llm_key": bool(os.environ.get("OPENAI_API_KEY")),
        "auth": bool(os.environ.get("ARC_PASSWORD")),
        "llm_used": LLM_BUDGET.used,
        "llm_limit": LLM_BUDGET.limit,
    }
