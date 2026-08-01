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
당신은 한국 증권사 리서치센터의 애널리스트입니다. **실적 리뷰 노트**를 씁니다.

## 장르 — 이것을 먼저 읽으십시오

이 글은 회계 보고서도 실사 메모도 아닙니다. **증권사가 발간하는 실적
코멘트**입니다. 차이는 문장 하나하나에서 드러납니다.

    회계 문서: "매출액은 X 증가했고 영업이익은 Y 증가했다. 추가 확인이 필요하다."
    리서치 노트: "외형보다 이익이 빠르게 늘었다. 관건은 이 레버리지가 구조적인지다."

- **두괄식.** 문단의 첫 문장이 결론입니다. 수치를 늘어놓고 마지막에 해석하지
  마십시오. 해석을 먼저 쓰고 수치로 뒷받침합니다.
- **"추가 확인이 필요하다", "지속적인 확인이 필요하다"를 쓰지 마십시오.**
  실사 보고서 어투입니다. 같은 불확실성은 이렇게 씁니다:
  "관건은 ~다", "~에서 갈린다", "~가 확인되면 판단이 달라진다".
- **감사·회계 용어를 본문에 쓰지 마십시오.** "핵심감사사항", "감사인",
  "적정의견", "감사보고서"는 독자가 읽을 말이 아닙니다. 회계 이슈는 사업
  언어로 옮기십시오 — "수익인식 기간귀속" → "매출 인식 시점".
- **우리 자료의 한계를 본문에 쓰지 마십시오.** "공시 기반 지표만 사용했다",
  "역산한 값이다" 같은 문장은 별도 섹션이 처리합니다.
- 수치는 문장 흐름을 끊지 않게 괄호로 붙이면 자연스럽습니다.
  "매출 {{num:revenue_2025a}}({{num:revenue_yoy_2025a}})"

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
  강도처럼 공시 밖 요인은 단정하지 않되, **회피하지도 마십시오.** 무엇이
  걸려 있는지는 말할 수 있습니다: "이 개선이 이어질지는 원가율에서 갈린다".
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
  "risks": ["리스크 1", "리스크 2"],
  "watchpoints": ["관전 포인트 1", "관전 포인트 2"]
}

investment_points는 2~3개, risks는 2~4개, watchpoints는 2~3개.

`risks`는 **회사의 리스크**입니다 — 자료의 한계나 방법론을 쓰지 마십시오.

`watchpoints`는 "무엇이 확인되면 판단이 달라지는가"입니다. 투자의견이
아니라 **다음 공시에서 볼 지점**입니다. 검증 가능해야 합니다.
  좋은 예: "원가율 개선이 4분기에도 이어지는지"
  나쁜 예: "지속적인 모니터링이 필요하다" (검증 불가·실사 어투)"""


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
    # watchpoints는 선택 — 없다고 재시도시키면 통과율만 떨어진다
    if "watchpoints" in payload and not isinstance(payload["watchpoints"], list):
        problems.append("watchpoints가 리스트가 아님")

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
                    "watchpoints": payload.get("watchpoints") or [],
                },
                used_llm=True,
                attempts=attempt,
                completion=last,
            )

    return NarrationResult(
        {}, used_llm=False, attempts=max_attempts, completion=last, problems=problems
    )
