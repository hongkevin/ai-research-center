"""eval 러너 — **실패하면 배포를 막는다** (D89).

왜 필요한가
-----------
ARC에는 콘텐츠 게이트(G0)는 있는데 **eval 게이트가 없었다.** `llm/bench.py`는
표를 찍고 끝난다. 표를 찍는 것과 막는 것은 다르다 — 아무도 안 보면 표는 없는
것과 같다.

세 계층
-------
* **gated** — 바닥을 못 넘기면 `exit 1`
* **report-only** — 기록만 한다. **여러 사이클 안 바뀌고 살아남은 뒤에만**
  게이트로 승격한다. 새 측정을 곧바로 게이트에 넣으면, 그 측정이 틀렸을 때
  배포가 막히고 사람은 바닥을 내린다 — 그러면 게이트가 장식이 된다
* **decision probe** — 고르려고 돌리는 것. 여기 없다

**CI에 API 키가 없다.** 그래서 이 러너가 도는 스위트는 전부 무료·결정적이고,
모델이나 네트워크를 부르지 않는다. 돈이 드는 판정(문장 생존율 등)은 릴리스
때 사람이 따로 돌리고 리포트를 커밋한다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from arc.evals.result import Result, Verdict
from arc.evals.suites import mentions

ROOT = Path(__file__).resolve().parents[3]
BASELINES = ROOT / "evals" / "baselines.json"
REPORTS = ROOT / "evals" / "reports"

# 스위트 등록. 새로 만들면 여기 한 줄이다.
SUITES = {mentions.NAME: mentions.run}


def load_baselines() -> dict:
    try:
        return json.loads(BASELINES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def judge(results: list[Result], baselines: dict) -> list[Verdict]:
    """측정 + 바닥 → 판정. **바닥이 없는 측정은 report-only다.**"""
    out: list[Verdict] = []
    for r in results:
        spec = (baselines.get("metrics") or {}).get(r.key) or {}
        out.append(
            Verdict(
                result=r,
                floor=spec.get("min"),
                ceiling=spec.get("max"),
                gated=bool(spec.get("gated", True)),
            )
        )
    return out


def render(verdicts: list[Verdict]) -> str:
    """사람이 읽는 표. **N과 한계가 값 옆에 붙어 있어야 한다.**"""
    lines = [
        "| suite | metric | value | bound | status |",
        "| --- | --- | --- | --- | --- |",
    ]
    for v in verdicts:
        lines.append(
            f"| {v.result.suite} | {v.result.metric} | {v.result.value} | {v.bound} | {v.status} |"
        )
    lines.append("")
    for v in verdicts:
        if v.result.note:
            lines.append(f"- `{v.result.key}` — {v.result.note}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ARC eval gate")
    ap.add_argument("--suite", action="append", help="이것만 돌린다 (기본: 전부)")
    ap.add_argument("--report", metavar="PATH", help="마크다운 리포트를 여기 쓴다")
    ap.add_argument(
        "--no-gate",
        action="store_true",
        help="측정만 하고 실패해도 0으로 끝낸다 (기록용)",
    )
    args = ap.parse_args(argv)

    wanted = args.suite or list(SUITES)
    unknown = [s for s in wanted if s not in SUITES]
    if unknown:
        print(f"모르는 스위트: {', '.join(unknown)}. 가능: {', '.join(SUITES)}", file=sys.stderr)
        return 2

    results: list[Result] = []
    for name in wanted:
        results.extend(SUITES[name]())

    verdicts = judge(results, load_baselines())
    table = render(verdicts)
    print(table)

    failed = [v for v in verdicts if v.gated and not v.passed]
    checked = sum(1 for v in verdicts if v.checked)
    print(
        f"\n{len(verdicts)}개 측정 · 게이트 {checked}개 · 실패 {len(failed)}개",
        file=sys.stderr,
    )
    for v in failed:
        print(f"  FAIL {v.result.key} = {v.result.value} (요구 {v.bound})", file=sys.stderr)

    if args.report:
        stamp = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC")
        head = (
            f"# eval run — {stamp}\n\n"
            "## What this run says, and what it does not\n\n"
            "**Says:** the deterministic suites below were computed against corpora\n"
            "committed in this repository, with no API key and no network.\n\n"
            "**Does not say:** that the classifier is this accurate in production. The\n"
            "name list comes from the corpus (~1,100 listed names), not from the full\n"
            "DART registry (~2,800), so precision measured here is a **floor** — more\n"
            "names produce more false positives. These are tripwires, not estimates.\n\n"
        )
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(head + table + "\n", encoding="utf-8")
        print(f"\n리포트: {args.report}", file=sys.stderr)

    if args.no_gate:
        return 0
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
