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
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from arc.data.kr.dart import DartProvider
from arc.pipeline.earnings_review import ReportResult, build_report, save_estimates
from arc.render.html import binding_rows, render_html
from arc.store.snapshot import SnapshotStore

REPO_ROOT = Path(__file__).resolve().parents[3]
# uvicorn은 CLI를 거치지 않고 모듈을 직접 import한다 — 여기서도 키를 읽어야 한다
load_dotenv(REPO_ROOT / ".env")

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))

# 추정 이력 — revision을 보여주려면 직전 **발간**이 있어야 한다
STORE_DIR = Path(os.environ.get("ARC_STORE_DIR", REPO_ROOT / ".arc-store"))
DRAFTS_DIR = REPO_ROOT / "drafts"

app = FastAPI(title="AI Research Center", docs_url="/api/docs")


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
    llm_used: bool = False
    llm_model: str = ""
    llm_cost: float | None = None
    published_path: str = ""
    error: str = ""


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
) -> ViewModel:
    """노트 생성. **생성과 발간은 다르다.**

    생성은 미리보기다 — 이력에 남지 않는다. 발간해야 추정이 스냅샷으로
    저장되고 다음 발간의 revision 기준이 된다. 생성할 때마다 저장하면
    가정을 만지작거린 흔적이 전부 "직전 추정"이 되어 이력이 무의미해진다.
    """
    provider = DartProvider()
    client = None
    if use_llm:
        from arc.llm.client import get_client

        client = get_client()

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
    )
    vm = _to_view(r)

    if publish and r.publishable:
        if r.estimates is not None and r.estimates.usable:
            save_estimates(store, r.estimates, symbol, published_at)
        DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
        path = DRAFTS_DIR / f"{symbol}-FY{year}-{published_at.isoformat()}.md"
        path.write_text(r.rendered or "", encoding="utf-8")
        vm.published_path = str(path)
    return vm


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


@app.get("/api/health")
def api_health():
    return {"status": "ok", "dart_key": bool(os.environ.get("DART_API_KEY"))}
