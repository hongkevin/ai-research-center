"""S4 섹션 작성 — LLM 서술 레이어.

핵심 제약: **LLM은 숫자를 볼 수 없다.**

프롬프트에 넣는 것은 Number Registry의 카탈로그(키·라벨·단위)뿐이고 값은
넣지 않는다. 값을 주면 LLM이 그것을 복사해 리터럴로 쓸 수 있고, 그 순간
G0가 차단하거나(운이 좋으면) 통과시킨다(운이 나쁘면 — 화이트리스트 안에
들어가는 형태일 때). 값을 주지 않으면 **플레이스홀더 외에는 쓸 방법이 없다.**

출력은 JSON. 파싱 실패나 스키마 불일치는 재시도하고, 그래도 안 되면
결정론 문장으로 폴백한다 (발간을 막지 않되 조용히 넘어가지도 않는다).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from arc.llm.client import Completion, LLMClient, Tier
from arc.llm.number_registry import NumberRegistry

SYSTEM_PROMPT = """\
당신은 한국 증권사 리서치 노트의 실적 분석 섹션을 쓰는 애널리스트입니다.

## 절대 규칙 — 숫자

본문에 **숫자를 직접 쓰면 안 됩니다.** 수치는 반드시 아래 형식의
플레이스홀더로만 표현합니다.

    {{num:키}}

사용 가능한 키는 아래 "수치 카탈로그"에 있는 것뿐입니다. 카탈로그에 없는
키를 지어내면 발간이 차단됩니다. 값이 무엇인지는 알려주지 않으며, 알 필요도
없습니다. 라벨과 단위만 보고 문장을 구성하십시오.

금지 예: "매출이 12.3% 늘었다", "약 3조원", "2배 증가"
허용 예: "매출은 {{num:revenue_2025a}}으로 전년 대비 {{num:revenue_yoy_2025a}} 변동했다"

연도(2025년), 분기(4분기), 목차 번호는 숫자로 써도 됩니다.

## 절대 규칙 — 투자의견

목표주가·투자의견·상승여력·매수/매도 표현을 **일절 쓰지 않습니다.**
"목표주가를 제시하지 않는다"처럼 부정문으로 언급하는 것도 금지입니다.
아예 그 단어를 쓰지 마십시오.

단정 표현("반드시", "확실히", "급등")도 금지입니다.

## 서술 원칙

- 공시 숫자로 확인되는 것과 추측을 섞지 않습니다. 가격 정책·수요·경쟁
  강도처럼 공시 밖 요인은 단정하지 말고 "추가 확인이 필요하다"로 남깁니다.
- 카탈로그의 `방향` 필드는 결정적 코드가 계산한 사실입니다. 그대로 쓰십시오.
  "매출은 {{num:revenue_yoy_2025a}} 증가했다"처럼 자연스럽게 씁니다.
  방향이 `-`인 항목(절대금액 등)에는 증감 표현을 쓰지 마십시오.
  **크기는 여전히 모릅니다.** "크게", "소폭", "급격히" 같은 정도 표현은 금지입니다.
- **숫자를 나열하지 마십시오.** 지표를 하나씩 읊는 글은 실패입니다.
  각 투자포인트는 하나의 질문에 답해야 합니다. 예: 이익률 변화가 원가에서
  왔는가 판관비에서 왔는가, 외형 성장과 이익 성장이 같은 방향인가.
  지표는 그 질문에 답하는 근거로만 등장합니다.
- 서로 다른 지표를 **연결**해 해석하십시오. 원가율과 영업이익률의 방향을
  비교하면 비용 구조의 어느 쪽이 움직였는지 좁힐 수 있습니다.
- 문장은 간결하게. 한 문단 3~5문장.

## 출력 형식

아래 JSON만 출력하십시오. 코드펜스나 설명을 덧붙이지 마십시오.

{
  "summary": "요약 3~5문장",
  "investment_points": [
    {"title": "제목", "body": "본문 3~5문장"}
  ],
  "earnings_narrative": "실적 표 아래 설명 2~4문장",
  "risks": ["리스크 1", "리스크 2"]
}

investment_points는 2~3개. risks는 2~4개."""


@dataclass
class NarrationResult:
    """S4 산출물 + 진단."""

    sections: dict[str, object]
    used_llm: bool
    attempts: int = 0
    completion: Completion | None = None
    problems: list[str] = field(default_factory=list)


def build_user_prompt(
    company_name: str,
    fiscal_year: int,
    basis: str,
    registry: NumberRegistry,
    thesis: str | None = None,
) -> str:
    """카탈로그(크기 없음) + 논지 + 과업 지시.

    `thesis`는 결정적 코드가 뽑아낸 관찰이다. 이게 없으면 LLM이 쓸 수 있는
    것이 재무 기계학뿐이라 지표를 하나씩 읊는 글이 된다.
    """
    lines = []
    for e in registry.catalog():
        d = f", 방향: {e['direction']}" if e["direction"] != "-" else ""
        lines.append(f"- {{{{num:{e['key']}}}}} — {e['label']} (단위: {e['unit']}{d})")
    head = f"# 대상\n{company_name} · {fiscal_year}년 연간 실적 ({basis}재무제표)\n\n"
    if thesis:
        head += f"# 확인된 관찰 (결정적 계산 결과 — 이것을 논지의 축으로 삼으십시오)\n{thesis}\n\n"
    return (
        head
        + "# 수치 카탈로그 (크기는 제공하지 않습니다. 키와 방향만 쓰십시오)\n"
        + "\n".join(lines)
        + "\n\n# 과업\n위 카탈로그의 플레이스홀더만 사용해 실적 리뷰 노트의 "
        "요약·투자포인트·실적 설명·리스크를 작성하십시오. JSON만 출력합니다."
    )


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


def parse_response(text: str) -> dict:
    """JSON 파싱. 모델이 코드펜스를 붙이는 경우가 흔해 벗겨낸다."""
    cleaned = _FENCE_RE.sub("", text).strip()
    # 앞뒤에 설명이 붙은 경우 첫 { 부터 마지막 } 까지
    if not cleaned.startswith("{"):
        s, e = cleaned.find("{"), cleaned.rfind("}")
        if s >= 0 and e > s:
            cleaned = cleaned[s : e + 1]
    return json.loads(cleaned)


def validate(payload: dict, registry: NumberRegistry) -> list[str]:
    """스키마 + 카탈로그 위반 확인. G0 앞단의 1차 방어선."""
    problems: list[str] = []

    for key in ("summary", "investment_points", "earnings_narrative", "risks"):
        if key not in payload:
            problems.append(f"필드 누락: {key}")
    if problems:
        return problems

    if not isinstance(payload["investment_points"], list) or not payload["investment_points"]:
        problems.append("investment_points가 비었거나 리스트가 아님")
    for i, p in enumerate(payload.get("investment_points") or []):
        if not isinstance(p, dict) or "title" not in p or "body" not in p:
            problems.append(f"investment_points[{i}]에 title/body 없음")
    if not isinstance(payload.get("risks"), list) or not payload["risks"]:
        problems.append("risks가 비었거나 리스트가 아님")

    # 카탈로그에 없는 키를 지어냈는지 — G0도 잡지만 여기서 재시도로 흡수한다
    blob = json.dumps(payload, ensure_ascii=False)
    unknown = sorted(set(registry.unknown_keys(blob)))
    if unknown:
        problems.append(f"카탈로그에 없는 키 사용: {', '.join(unknown)}")

    return problems


def narrate(
    client: LLMClient,
    *,
    company_name: str,
    fiscal_year: int,
    basis: str,
    registry: NumberRegistry,
    thesis: str | None = None,
    max_attempts: int = 2,
) -> NarrationResult:
    """LLM으로 섹션 본문을 만든다. 실패 시 problems를 채워 반환한다."""
    user = build_user_prompt(company_name, fiscal_year, basis, registry, thesis)
    problems: list[str] = []
    last: Completion | None = None

    for attempt in range(1, max_attempts + 1):
        prompt = user
        if problems:
            prompt += (
                "\n\n# 직전 시도의 문제\n"
                + "\n".join(f"- {p}" for p in problems)
                + "\n이 문제를 고쳐 다시 출력하십시오."
            )
        try:
            last = client.complete(system=SYSTEM_PROMPT, user=prompt, tier=Tier.WRITE)
            payload = parse_response(last.text)
        except json.JSONDecodeError as e:
            problems = [f"JSON 파싱 실패: {e}"]
            continue
        except Exception as e:  # noqa: BLE001 — provider별 예외가 달라 넓게 잡는다
            return NarrationResult(
                {}, used_llm=False, attempts=attempt, problems=[f"{type(e).__name__}: {e}"]
            )

        problems = validate(payload, registry)
        if not problems:
            return NarrationResult(
                sections={
                    "summary": payload["summary"],
                    "investment_points": payload["investment_points"],
                    "earnings_narrative": payload["earnings_narrative"],
                    "risks": payload["risks"],
                },
                used_llm=True,
                attempts=attempt,
                completion=last,
            )

    return NarrationResult(
        {}, used_llm=False, attempts=max_attempts, completion=last, problems=problems
    )
