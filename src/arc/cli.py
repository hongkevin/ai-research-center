"""arc CLI — 세미오토 발간 워크플로.

    arc generate 005930 --year 2025    초안 생성 → drafts/
    arc inspect  005930                지표 매핑만 확인 (게이트 전)

발간 승인(approve)은 사이트 빌드가 붙는 다음 단계에서 추가한다.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import typer
from dotenv import load_dotenv

from arc.data.base import ConsolidationType
from arc.data.kr.dart import DartProvider
from arc.finmodel.metrics import extract_metrics, fmt_krw
from arc.pipeline.earnings_review import build_report

app = typer.Typer(add_completion=False, help="AI Research Center — 실적 리뷰 노트 생성")

REPO_ROOT = Path(__file__).resolve().parents[2]
DRAFTS_DIR = REPO_ROOT / "drafts"


def _provider() -> DartProvider:
    load_dotenv(REPO_ROOT / ".env")
    if not os.environ.get("DART_API_KEY"):
        typer.secho(
            "DART_API_KEY가 없습니다. .env.example을 .env로 복사해 채우십시오.", fg=typer.colors.RED
        )
        raise typer.Exit(1)
    return DartProvider()


@app.command()
def inspect(
    symbol: str,
    year: int = typer.Option(..., "--year", "-y", help="사업연도"),
    separate: bool = typer.Option(False, "--separate", help="별도재무제표 사용"),
) -> None:
    """계정과목 매핑 결과만 확인한다 (리포트 생성 전 점검용)."""
    p = _provider()
    from arc.pipeline.earnings_review import fetch_statement

    cons = ConsolidationType.SEPARATE if separate else None
    stmt = fetch_statement(symbol, year, p, consolidation=cons)
    ms = extract_metrics(stmt)

    company = p.get_company(symbol)
    typer.echo(
        f"\n{company.name} ({symbol}) · FY{year} · {stmt.consolidation.value} · 계정 {len(stmt.items)}건"
    )
    typer.echo(f"공시 {stmt.rcept_no}\n")

    for key, mv in ms.values.items():
        typer.echo(
            f"  {key:18} {fmt_krw(mv.current) or '—':>18}  전기 {fmt_krw(mv.prior) or '—':>16}"
        )
        typer.echo(f"  {'':18} ← {mv.matched_by}: {mv.matched_on!r} [{mv.statement_type}]")
    if ms.missing:
        typer.secho(f"\n  미매핑: {', '.join(ms.missing)}", fg=typer.colors.YELLOW)
    status = "OK" if ms.coverage_ok else "부족 — 리포트 생성 불가"
    color = typer.colors.GREEN if ms.coverage_ok else typer.colors.RED
    typer.secho(f"\n  커버리지: {status}", fg=color)


@app.command()
def generate(
    symbol: str,
    year: int = typer.Option(..., "--year", "-y", help="사업연도"),
    separate: bool = typer.Option(False, "--separate", help="별도재무제표 사용"),
    out: Path | None = typer.Option(None, "--out", "-o", help="출력 경로"),
    llm: bool = typer.Option(False, "--llm", help="S4 서술을 LLM으로 생성"),
) -> None:
    """실적 리뷰 노트 초안을 생성한다. G0 통과 시에만 파일로 저장한다."""
    p = _provider()
    # 기본은 연결→별도 자동 폴백 (소형주는 별도만 제출하는 경우가 많다)
    cons = ConsolidationType.SEPARATE if separate else None

    client = None
    if llm:
        from arc.llm.client import get_client

        client = get_client()

    r = build_report(
        symbol, year, p, consolidation=cons, published_at=dt.datetime.now(dt.UTC).date(), llm=client
    )

    typer.echo(f"\n{r.company.name} ({symbol}) · FY{year}")
    typer.echo(
        f"  지표 {len(r.metrics.values)}개 · 레지스트리 {len(r.registry)}건 · "
        f"플레이스홀더 {len(r.registry.extract_keys(r.assembled))}개"
    )
    if r.metrics.missing:
        typer.secho(f"  미매핑: {', '.join(r.metrics.missing)}", fg=typer.colors.YELLOW)

    n = r.narration
    if n is not None:
        if n.used_llm:
            c = n.completion
            tok = f" · 토큰 in {c.input_tokens}/out {c.output_tokens}" if c else ""
            typer.secho(
                f"  LLM 서술 생성 ({c.model if c else '?'}, 시도 {n.attempts}회{tok})",
                fg=typer.colors.CYAN,
            )
        else:
            typer.secho(
                f"  LLM 서술 실패 → 결정론 문장 사용 (시도 {n.attempts}회)", fg=typer.colors.YELLOW
            )
            for prob in n.problems[:4]:
                typer.echo(f"    - {prob[:120]}")

    if not r.gate.passed:
        typer.secho(f"\n  {r.gate.summary()}", fg=typer.colors.RED)
        for v in r.gate.violations[:15]:
            loc = f" (line {v.line})" if v.line else ""
            typer.echo(f"    [{v.rule}]{loc} {v.detail[:120]}")
        if len(r.gate.violations) > 15:
            typer.echo(f"    … 외 {len(r.gate.violations) - 15}건")
        raise typer.Exit(1)

    typer.secho(f"\n  {r.gate.summary()}", fg=typer.colors.GREEN)

    DRAFTS_DIR.mkdir(exist_ok=True)
    path = out or DRAFTS_DIR / f"{symbol}-FY{year}.md"
    path.write_text(r.rendered or "", encoding="utf-8")
    typer.echo(
        f"  → {path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path}"
        f" ({len(r.rendered or ''):,}자, 바인딩 {len(r.bindings)}건)"
    )


@app.command()
def llmcheck(provider: str = typer.Option("", "--provider", help="비우면 키가 있는 전부")) -> None:
    """LLM API 키 유효성을 확인한다 (provider당 최소 요청 1건)."""
    load_dotenv(REPO_ROOT / ".env")
    from arc.llm.client import PROVIDERS, available_providers, get_client

    targets = [provider] if provider else available_providers()
    if not targets:
        typer.secho("키가 설정된 provider가 없습니다.", fg=typer.colors.RED)
        for n, spec in PROVIDERS.items():
            typer.echo(f"  {n:<10} {spec.env_key}")
        raise typer.Exit(1)

    failed = 0
    for name in targets:
        try:
            c = get_client(name)
            ok, msg = c.healthcheck()
        except ValueError as e:
            ok, msg = False, str(e)
        color = typer.colors.GREEN if ok else typer.colors.RED
        typer.secho(f"{'✅' if ok else '❌'} {name:<10} {msg}", fg=color)
        failed += 0 if ok else 1
    if failed:
        raise typer.Exit(1)


@app.command()
def benchmark(
    symbol: str,
    year: int = typer.Option(..., "--year", "-y"),
    runs: int = typer.Option(3, "--runs", "-n", help="provider당 반복 횟수"),
    providers: str = typer.Option("", "--providers", help="쉼표 구분. 비우면 키가 있는 전부"),
    save: bool = typer.Option(
        False, "--save", help="원문을 bench_out/에 저장 (한국어 품질은 눈으로)"
    ),
) -> None:
    """모델별 제약 준수를 게이트로 채점한다.

    한국어 품질 자체는 자동으로 잴 수 없다. 제약 준수(리터럴·키·금지어)만
    100% 자동이며, 이 시스템에서 모델을 고르는 1차 기준이다.
    """
    load_dotenv(REPO_ROOT / ".env")
    from arc.data.base import ConsolidationType
    from arc.finmodel.metrics import build_entries, extract_metrics
    from arc.llm.bench import benchmark as run_bench
    from arc.llm.bench import format_table
    from arc.llm.client import available_providers, get_client
    from arc.llm.number_registry import NumberRegistry
    from arc.pipeline.earnings_review import fetch_statement

    names = [x.strip() for x in providers.split(",") if x.strip()] or available_providers()
    if not names:
        typer.secho("키가 설정된 provider가 없습니다.", fg=typer.colors.RED)
        raise typer.Exit(1)

    dart = _provider()
    stmt = fetch_statement(symbol, year, dart)
    company = dart.get_company(symbol)
    ms = extract_metrics(stmt)
    reg = NumberRegistry()
    reg.register_all(build_entries(ms, stmt.provenance))
    basis = "연결" if stmt.consolidation is ConsolidationType.CONSOLIDATED else "별도"

    typer.echo(
        f"\n{company.name} ({symbol}) FY{year} · 카탈로그 {len(reg)}건 · "
        f"provider {len(names)}개 × {runs}회\n"
    )

    clients = []
    for n in names:
        try:
            clients.append(get_client(n))
        except ValueError as e:
            typer.secho(f"  건너뜀 {n}: {e}", fg=typer.colors.YELLOW)

    scores = run_bench(
        clients,
        company_name=company.name,
        fiscal_year=year,
        basis=basis,
        registry=reg,
        runs=runs,
        save_dir=(REPO_ROOT / "bench_out") if save else None,
    )
    typer.echo(format_table(scores))
    if save:
        typer.echo("\n원문 → bench_out/ (한국어 문장 품질은 직접 읽어 비교하십시오)")


if __name__ == "__main__":
    app()
