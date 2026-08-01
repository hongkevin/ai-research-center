"""모델 비교 하네스 — 게이트를 채점기로 쓴다.

한국어 리서치 문장 품질에 대한 공개 벤치마크는 없다. 그런데 이 시스템에는
**결정론 게이트**가 있으므로, 모델이 제약을 지키는지는 객관적으로 잴 수 있다.

측정 항목
---------
mechanical (자동)
  - gate_pass_1st  : 1차 시도에서 G0를 통과한 비율. **가장 중요한 지표**
  - retries        : 평균 재시도 횟수
  - literal_leak   : 숫자 리터럴을 쓴 비율 (플레이스홀더 제약 위반)
  - unknown_key    : 카탈로그에 없는 키를 지어낸 비율
  - json_fail      : JSON 파싱 실패 비율
  - banned_lang    : rating·단정 표현 사용 비율
  - tokens / cost / latency

editorial (사람이 봐야 함)
  - 한국어 문장이 리서치 노트로 읽히는가. 자동 채점 불가.
    `--save`로 산출물을 남겨 눈으로 비교한다.

품질을 완전히 자동으로 잴 수는 없다. 다만 **제약 준수는 100% 자동**이고,
그것이 이 시스템에서 모델을 고르는 1차 기준이다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from arc.llm.client import Completion, LLMClient, Tier
from arc.llm.narrate import (
    SYSTEM_PROMPT,
    build_user_prompt,
    parse_response,
    validate,
)
from arc.llm.number_registry import NumberRegistry
from arc.verify.g0 import G0Gate


@dataclass
class TrialResult:
    """모델 1회 시도 결과."""

    ok: bool
    json_ok: bool = False
    literal_leak: int = 0  # 미등록 숫자 개수
    unknown_keys: int = 0
    banned: int = 0
    problems: list[str] = field(default_factory=list)
    completion: Completion | None = None
    text: str = ""


@dataclass
class ModelScore:
    """모델 1개의 집계 결과."""

    provider: str
    model: str
    trials: list[TrialResult] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.trials)

    def _rate(self, pred) -> float:
        return sum(1 for t in self.trials if pred(t)) / self.n if self.n else 0.0

    @property
    def gate_pass_1st(self) -> float:
        return self._rate(lambda t: t.ok)

    @property
    def json_fail(self) -> float:
        return self._rate(lambda t: not t.json_ok)

    @property
    def literal_leak(self) -> float:
        return self._rate(lambda t: t.literal_leak > 0)

    @property
    def unknown_key(self) -> float:
        return self._rate(lambda t: t.unknown_keys > 0)

    @property
    def banned_lang(self) -> float:
        return self._rate(lambda t: t.banned > 0)

    def _avg(self, get) -> float | None:
        vals = [get(t.completion) for t in self.trials if t.completion]
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None

    @property
    def avg_cost(self) -> float | None:
        return self._avg(lambda c: c.cost_usd)

    @property
    def avg_latency(self) -> float | None:
        return self._avg(lambda c: c.latency_s)

    @property
    def avg_out_tokens(self) -> float | None:
        return self._avg(lambda c: c.output_tokens)


def _count_banned(text: str, gate: G0Gate) -> int:
    """rating·단정 표현 개수 (디스클레이머 제외 로직을 타지 않는 원문 기준)."""
    return len(gate.check_compliance(text))


def run_trial(
    client: LLMClient,
    *,
    company_name: str,
    fiscal_year: int,
    basis: str,
    registry: NumberRegistry,
    thesis: str | None = None,
) -> TrialResult:
    """1회 생성 → 파싱 → 제약 검사. **재시도 없이** 1차 능력을 잰다."""
    user = build_user_prompt(company_name, fiscal_year, basis, registry, thesis)
    gate = G0Gate(registry)

    try:
        c = client.complete(system=SYSTEM_PROMPT, user=user, tier=Tier.WRITE)
    except Exception as e:  # noqa: BLE001 — provider별 예외가 다르다
        return TrialResult(ok=False, problems=[f"{type(e).__name__}: {e}"])

    try:
        payload = parse_response(c.text)
    except json.JSONDecodeError as e:
        return TrialResult(
            ok=False, json_ok=False, completion=c, text=c.text, problems=[f"JSON 파싱 실패: {e}"]
        )

    blob = json.dumps(payload, ensure_ascii=False)
    problems = validate(payload, registry)
    leaks = registry.find_unregistered_numbers(blob)
    unknown = set(registry.unknown_keys(blob))
    banned = _count_banned(blob, gate)

    return TrialResult(
        ok=not problems and not leaks and not banned,
        json_ok=True,
        literal_leak=len(leaks),
        unknown_keys=len(unknown),
        banned=banned,
        problems=problems + [f"리터럴 {x.text!r}" for x in leaks[:3]],
        completion=c,
        text=c.text,
    )


def benchmark(
    clients: list[LLMClient],
    *,
    company_name: str,
    fiscal_year: int,
    basis: str,
    registry: NumberRegistry,
    thesis: str | None = None,
    runs: int = 3,
    save_dir: Path | None = None,
) -> list[ModelScore]:
    """provider별로 `runs`회 생성해 집계한다."""
    scores: list[ModelScore] = []
    for client in clients:
        model = getattr(client, "model_for", lambda t: "?")(Tier.WRITE)
        sc = ModelScore(provider=client.name, model=model)
        for i in range(runs):
            t = run_trial(
                client,
                company_name=company_name,
                fiscal_year=fiscal_year,
                basis=basis,
                registry=registry,
                thesis=thesis,
            )
            sc.trials.append(t)
            if save_dir and t.text:
                save_dir.mkdir(parents=True, exist_ok=True)
                (save_dir / f"{client.name}-{model}-{i + 1}.txt").write_text(
                    t.text, encoding="utf-8"
                )
        scores.append(sc)
    return scores


def format_table(scores: list[ModelScore]) -> str:
    """비교표. 1차 통과율 내림차순."""
    rows = sorted(scores, key=lambda s: (-s.gate_pass_1st, s.avg_cost or 0))
    head = (
        f"{'provider/model':<34}{'통과':>6}{'리터럴':>7}{'키오류':>7}"
        f"{'금지어':>7}{'JSON실패':>9}{'출력tok':>8}{'지연s':>7}{'건당$':>9}"
    )
    lines = [head, "=" * len(head)]
    for s in rows:
        cost = f"{s.avg_cost:.5f}" if s.avg_cost is not None else "—"
        lat = f"{s.avg_latency:.1f}" if s.avg_latency is not None else "—"
        tok = f"{s.avg_out_tokens:.0f}" if s.avg_out_tokens is not None else "—"
        lines.append(
            f"{s.provider + '/' + s.model:<34}"
            f"{s.gate_pass_1st:>5.0%}{s.literal_leak:>7.0%}{s.unknown_key:>7.0%}"
            f"{s.banned_lang:>7.0%}{s.json_fail:>9.0%}{tok:>8}{lat:>7}{cost:>9}"
        )
    return "\n".join(lines)
