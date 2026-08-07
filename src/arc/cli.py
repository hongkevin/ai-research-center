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


def _parse_assumptions(pairs: list[str]) -> dict[str, float]:
    """`key=value` 목록 → 가정 덮어쓰기. 잘못된 형식은 조용히 넘기지 않는다."""
    out: dict[str, float] = {}
    for raw in pairs:
        if "=" not in raw:
            raise typer.BadParameter(f"--assume 형식은 key=value 입니다: {raw!r}")
        key, _, value = raw.partition("=")
        try:
            out[key.strip()] = float(value)
        except ValueError:
            raise typer.BadParameter(f"가정 값이 숫자가 아닙니다: {raw!r}") from None
    return out


@app.command()
def generate(
    symbol: str,
    year: int = typer.Option(..., "--year", "-y", help="사업연도"),
    separate: bool = typer.Option(False, "--separate", help="별도재무제표 사용"),
    out: Path | None = typer.Option(None, "--out", "-o", help="출력 경로"),
    llm: bool = typer.Option(False, "--llm", help="S4 서술을 LLM으로 생성"),
    assume: list[str] = typer.Option(
        None, "--assume", "-a", help="추정 가정 덮어쓰기 (예: -a revenue_growth=12.5)"
    ),
    store_dir: Path | None = typer.Option(
        None, "--store", help="추정 이력 저장소 (지정 시 직전 추정 대비 revision을 표시하고 저장)"
    ),
    published: str | None = typer.Option(
        None, "--published", help="발간일 YYYY-MM-DD (기본 오늘). 추정 이력의 기준 시각이다"
    ),
) -> None:
    """실적 리뷰 노트 초안을 생성한다. G0 통과 시에만 파일로 저장한다."""
    p = _provider()
    overrides = _parse_assumptions(assume or [])
    published_at = dt.date.fromisoformat(published) if published else dt.datetime.now(dt.UTC).date()

    store = None
    if store_dir is not None:
        from arc.store.snapshot import SnapshotStore

        store = SnapshotStore(store_dir)
    # 기본은 연결→별도 자동 폴백 (소형주는 별도만 제출하는 경우가 많다)
    cons = ConsolidationType.SEPARATE if separate else None

    client = None
    if llm:
        from arc.llm.client import get_client

        client = get_client()

    r = build_report(
        symbol,
        year,
        p,
        consolidation=cons,
        published_at=published_at,
        llm=client,
        store=store,
        assumptions=overrides,
    )

    typer.echo(f"\n{r.company.name} ({symbol}) · FY{year}")
    typer.echo(
        f"  지표 {len(r.metrics.values)}개 · 레지스트리 {len(r.registry)}건 · "
        f"플레이스홀더 {len(r.registry.extract_keys(r.assembled))}개"
    )
    if r.metrics.missing:
        typer.secho(f"  미매핑: {', '.join(r.metrics.missing_labels)}", fg=typer.colors.YELLOW)

    est = r.estimates
    if est is not None and est.usable:
        tag = " (사용자 가정)" if any(a.is_override for a in est.assumptions) else ""
        typer.echo(f"  {est.fiscal_year}년 추정 산출{tag}")
        for w in est.warnings:
            typer.secho(f"    ⚠ {w}", fg=typer.colors.YELLOW)
        for rev in r.revisions:
            if rev.direction == "유지":
                continue
            color = typer.colors.RED if rev.direction == "하향" else typer.colors.GREEN
            typer.secho(
                f"    {rev.label} {rev.direction} {rev.change_pct:+.1f}% (직전 추정 대비)", fg=color
            )
    elif est is not None and est.warnings:
        typer.secho(f"  추정 미산출: {est.warnings[0]}", fg=typer.colors.YELLOW)

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

    # 게이트를 통과한 초안의 추정만 이력에 남긴다 — 차단된 초안이 다음
    # 발간의 revision 기준이 되면 이력이 오염된다.
    if store is not None and r.estimates is not None and r.estimates.usable:
        from arc.pipeline.earnings_review import save_estimates

        save_estimates(store, r.estimates, symbol, published_at)
        typer.echo(f"  추정 이력 저장 ({published_at})")

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


@app.command()
def backtest(
    symbols: str = typer.Argument(..., help="종목코드 쉼표 구분. `-`면 표준입력에서 읽는다"),
    start: int = typer.Option(..., "--start", help="첫 기준 연도 (이 해 실적으로 다음 해를 추정)"),
    end: int = typer.Option(..., "--end", help="마지막 기준 연도"),
    separate: bool = typer.Option(False, "--separate", help="별도재무제표 사용"),
    csv_out: Path | None = typer.Option(None, "--csv", help="종목별 오차를 CSV로 저장"),
) -> None:
    """기계적 연장 기준선이 실제로 얼마나 틀리는지 잰다 (Q8).

    FY(Y) 사업보고서만으로 FY(Y+1)을 추정하고 FY(Y+1) 보고서의 당기 값과
    대조한다. 뒤 연도 정보가 섞이면 백테스트가 거짓말이 되므로 두 방향
    모두 코드가 막는다 (`finmodel/backtest.py`).
    """
    import sys

    from arc.data.base import PeriodType
    from arc.finmodel.backtest import BACKTEST_METRICS, describe, run

    provider = _provider()
    raw = sys.stdin.read() if symbols == "-" else symbols
    codes = [s.strip() for s in raw.replace("\n", ",").split(",") if s.strip()]
    if not codes:
        typer.secho("종목코드가 없습니다.", fg=typer.colors.RED)
        raise typer.Exit(1)
    consolidation = ConsolidationType.SEPARATE if separate else ConsolidationType.CONSOLIDATED

    failures: dict[str, int] = {}

    def fetch(symbol: str, year: int):
        """공시가 없거나 커버리지가 모자라면 None — 실패 사유는 세어서 보고한다."""
        try:
            stmt = provider.get_financials(symbol, year, PeriodType.ANNUAL, consolidation)
        except Exception as exc:  # noqa: BLE001 — 어댑터별 예외 타입이 다르다
            failures[type(exc).__name__] = failures.get(type(exc).__name__, 0) + 1
            return None
        ms = extract_metrics(stmt)
        return ms if ms.coverage_ok else None

    total = len(codes)
    seen: set[str] = set()

    def progress(symbol: str, year: int) -> None:
        if symbol not in seen:
            seen.add(symbol)
            typer.echo(f"  [{len(seen):>3}/{total}] {symbol}", nl=False)
        typer.echo(f" {year}", nl=False)
        if year == end + 1:
            typer.echo("")

    result = run(codes, list(range(start, end + 1)), fetch, on_progress=progress)
    typer.echo("")

    if failures:
        typer.secho(
            "  조회 실패: " + ", ".join(f"{k} {v}건" for k, v in sorted(failures.items())),
            fg=typer.colors.YELLOW,
        )
    typer.echo("")
    for line in describe(result):
        typer.echo(f"  {line}")

    if result.skipped:
        typer.echo("\n  산출하지 않은 사유:")
        reasons: dict[str, int] = {}
        for s in result.skipped:
            head = s.reason.split(".")[0][:60]
            reasons[head] = reasons.get(head, 0) + 1
        for reason, n in sorted(reasons.items(), key=lambda x: -x[1]):
            typer.echo(f"    {n:>3}건  {reason}")

    if csv_out is not None:
        import csv

        csv_out.parent.mkdir(parents=True, exist_ok=True)
        with csv_out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(
                ["symbol", "base_year", "target_year", "metric", "estimate", "actual", "error_pct"]
            )
            for e in result.errors:
                w.writerow(
                    [
                        e.symbol,
                        e.base_year,
                        e.target_year,
                        e.metric,
                        e.estimate,
                        e.actual,
                        f"{e.error_pct:.4f}" if e.error_pct is not None else "",
                    ]
                )
        typer.echo(f"\n  → {csv_out} ({len(result.errors)}행)")

    # 지표가 하나도 안 나오면 사람이 알아야 한다 (조용히 0건으로 끝내지 않는다)
    if not any(result.summaries[m].n for m, _ in BACKTEST_METRICS):
        typer.secho("\n  대조된 추정이 하나도 없습니다.", fg=typer.colors.RED)
        raise typer.Exit(1)


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port", "-p"),
    reload: bool = typer.Option(False, "--reload", help="개발 중 자동 재시작"),
) -> None:
    """웹 작업대를 띄운다 (실적 리뷰 노트 검토 화면)."""
    try:
        import uvicorn
    except ImportError:  # pragma: no cover
        typer.secho('웹 의존성이 없습니다: uv pip install -e ".[web]"', fg=typer.colors.RED)
        raise typer.Exit(1) from None

    typer.secho(f"\n  http://{host}:{port}  — Ctrl+C로 종료\n", fg=typer.colors.CYAN)
    uvicorn.run("arc.web.app:app", host=host, port=port, reload=reload)


telegram_app = typer.Typer(help="텔레그램 — 로그인·채널 목록·수집")
app.add_typer(telegram_app, name="telegram")


def _tg_store() -> Path:
    """세션이 놓일 곳. **사용자 디렉터리 안이다** — 계정은 사람마다다."""
    from arc.web.identity import user_dir

    base = os.environ.get("ARC_STORE_DIR") or str(
        Path(__file__).resolve().parents[2] / ".arc-store"
    )
    return user_dir(base)


def _tg_client():
    """로그인된 Telethon 클라이언트. **CLI에서만 만든다.**

    인증은 전화번호 → 코드 → (2FA면) 비밀번호로 사람이 개입한다. 웹 요청
    안에서 할 수 없고, 세션 파일은 계정 그 자체라 서버가 대신 들고 있을
    물건이 아니다.
    """
    from telethon import TelegramClient

    from arc.ingest.telegram_collect import credentials, session_path

    api_id, api_hash = credentials()
    return TelegramClient(str(session_path(_tg_store())), api_id, api_hash)


@telegram_app.command("login")
def telegram_login() -> None:
    """텔레그램에 로그인한다. **한 번만 하면 세션이 남는다.**"""
    import asyncio

    async def run() -> None:
        client = _tg_client()
        await client.start()  # 전화번호·코드를 여기서 묻는다
        me = await client.get_me()
        typer.secho(
            f"\n  로그인됨: {me.first_name or ''} (@{me.username or '-'})", fg=typer.colors.GREEN
        )
        typer.echo(f"  세션: {_tg_store()}/telegram.session\n")
        await client.disconnect()

    asyncio.run(run())


@telegram_app.command("channels")
def telegram_channels(limit: int = typer.Option(200, "--limit")) -> None:
    """구독 중인 채널 목록. **가져오기 전에 무엇이 있는지 본다.**"""
    import asyncio

    from arc.ingest.telegram_collect import fetch_dialogs
    from arc.ingest.telegram_parse import classify_channel

    async def run() -> None:
        client = _tg_client()
        await client.start()
        rows = await fetch_dialogs(client, limit=limit)
        await client.disconnect()
        typer.echo(f"\n  채널 {len(rows)}개\n")
        for r in rows:
            kind = classify_channel(r["name"], chat_type=r["chat_type"])
            typer.echo(f"  {r['chat_id']!s:>14}  {kind.value:<9} {r['unread']:>6}  {r['name']}")
        typer.echo("")

    asyncio.run(run())


@telegram_app.command("sync")
def telegram_sync(
    chat: list[int] = typer.Option(None, "--chat", help="채널 id (여러 번). 비우면 전부"),
    limit: int = typer.Option(300, "--limit", help="채널당 최근 몇 건"),
    days: int = typer.Option(7, "--days", help="며칠치"),
) -> None:
    """채널에서 메시지를 가져와 **내보내기와 같은 모양**으로 저장한다.

    파서가 그대로 읽는다 — 수집기를 바꿔도 그 뒤가 안 바뀐다 (D66).
    """
    import asyncio
    import datetime as dt
    import json

    from arc.ingest.telegram_collect import fetch_channel, fetch_dialogs

    async def run() -> None:
        client = _tg_client()
        await client.start()
        targets = list(chat or [])
        if not targets:
            targets = [r["chat_id"] for r in await fetch_dialogs(client)]
        since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)

        out_dir = _tg_store() / "telegram"
        out_dir.mkdir(parents=True, exist_ok=True)
        total = 0
        for chat_id in targets:
            try:
                got = await fetch_channel(client, chat_id, limit=limit, since=since)
            except Exception as exc:  # noqa: BLE001 — 한 채널이 실패해도 나머지는 받는다
                typer.secho(f"  건너뜀 {chat_id}: {exc}", fg=typer.colors.YELLOW)
                continue
            if not got.messages:
                continue
            path = out_dir / f"{got.chat_id}.json"
            path.write_text(json.dumps(got.as_export(), ensure_ascii=False), encoding="utf-8")
            total += len(got.messages)
            typer.echo(f"  {len(got.messages):>5}건  {got.name}")
        await client.disconnect()
        typer.secho(f"\n  메시지 {total}건 · {out_dir}\n", fg=typer.colors.GREEN)

    asyncio.run(run())


if __name__ == "__main__":
    app()
