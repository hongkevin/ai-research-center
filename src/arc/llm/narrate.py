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

# 첫 화면 세 줄. **순서가 뜻이다** — 왜 지금 → 무엇이 근거 → 언제 확인.
# 국내 리서치 미드스몰캡 노트의 표준 꼴이고, 읽는 사람이 이 세 줄만 보고
# 넘어가는 일이 많다 (D87에서 리포트 실물 3편으로 확인).
HEADLINE_KEYS = ("signal", "key", "step")
HEADLINE_LABEL = {"signal": "Signal", "key": "Key", "step": "Step"}


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

**"비중 확대"·"비중 축소"도 쓰지 마십시오.** 부문 매출 비중을 말할 때도
마찬가지입니다 — 이 표현은 투자의견(overweight/underweight)이라서 문맥과
무관하게 차단됩니다. "비중이 늘었다", "비중이 커졌다"로 씁니다.

## 서술 원칙

- 공시 숫자로 확인되는 것과 추측을 섞지 않습니다. 가격 정책·수요·경쟁
  강도처럼 공시 밖 요인은 단정하지 않되, **회피하지도 마십시오.** 무엇이
  걸려 있는지는 말할 수 있습니다: "이 개선이 이어질지는 원가율에서 갈린다".
- 카탈로그의 `방향` 필드는 결정적 코드가 계산한 사실입니다. 그대로 쓰십시오.
  "매출은 {{num:revenue_yoy_2025a}} 증가했다"처럼 자연스럽게 씁니다.
  방향이 `-`인 항목(절대금액 등)에는 증감 표현을 쓰지 마십시오.
  **크기는 여전히 모릅니다.** "크게", "소폭", "급격히" 같은 정도 표현은 금지입니다.
- **숫자를 나열하지 마십시오.** 지표를 하나씩 읊는 글은 실패입니다.
  지표는 주장을 떠받치는 근거로만 등장합니다.
- 서로 다른 지표를 **연결**해 해석하십시오. 원가율과 영업이익률의 방향을
  비교하면 비용 구조의 어느 쪽이 움직였는지 좁힐 수 있습니다.
- 문장은 간결하게. 한 문단 3~5문장.

## 출력 형식

아래 JSON만 출력하십시오. 코드펜스나 설명을 덧붙이지 마십시오.

{
  "headline": {
    "signal": "왜 지금인가 — 한 줄",
    "key": "무엇이 그 근거인가 — 한 줄",
    "step": "언제 무엇이 확인되는가 — 한 줄"
  },
  "business_narrative": "이 회사가 무엇을 파는 회사인지 3~5문장",
  "summary": "요약 3~5문장",
  "investment_points": [
    {"title": "제목", "body": "본문 3~5문장"}
  ],
  "earnings_narrative": "실적 표 아래 설명 2~4문장",
  "risks": ["리스크 1", "리스크 2"],
  "watchpoints": ["관전 포인트 1", "관전 포인트 2"]
}

investment_points는 2~3개, risks는 2~4개, watchpoints는 2~3개.

## 투자포인트 쓰는 법

`investment_points[].body`는 **논증**입니다. 관찰의 나열이 아닙니다.
아래 네 요소를 이 순서로, 문단을 나눠 씁니다 (총 3~4문단).

1. **주장** — 한 문장. 이 포인트가 말하려는 것. 두괄식.
2. **근거** — 카탈로그 수치로 뒷받침. 서로 다른 지표를 연결합니다.
   (부문별 매출·성장률이 있으면 전사 지표와 함께 씁니다.)
3. **반론** — 이 주장이 틀릴 수 있는 조건. "다만 ~라면 이 해석은 성립하지 않는다".
   반론 없는 투자포인트는 주장이 아니라 소개입니다.
4. **확인 지점** — 다음 공시에서 무엇을 보면 판가름 나는가.

`title`은 주장을 압축한 것이어야 합니다.
  좋은 예: "이익률 개선은 원가에서 왔고, 재현 가능성이 관건이다"
  나쁜 예: "수익성 개선" (무엇을 주장하는지 없음)

`business_narrative`는 「확인된 관찰」에 실린 **회사의 사업 서술**을 리서치
문체로 다시 쓴 것입니다. 공시 문체("당사는 ~하고 있습니다")를 그대로 옮기지
말고, 무엇을 만들어 누구에게 파는지가 드러나게 씁니다. 관찰에 없는 산업
정보·경쟁사·시장 규모를 지어내면 안 됩니다. 관찰에 사업 서술이 없으면
빈 문자열로 두십시오.

`risks`는 **회사의 리스크**입니다 — 자료의 한계나 방법론을 쓰지 마십시오.

`watchpoints`는 "무엇이 확인되면 판단이 달라지는가"입니다. 투자의견이
아니라 **다음 공시에서 볼 지점**입니다. 검증 가능해야 합니다.
  좋은 예: "원가율 개선이 4분기에도 이어지는지"
  나쁜 예: "지속적인 모니터링이 필요하다" (검증 불가·실사 어투)

## headline — 첫 화면 세 줄

국내 리서치의 미드스몰캡 노트는 본문 앞에 **세 줄**을 세웁니다. 읽는 사람이
그 세 줄만 보고 넘어가는 일이 많으므로, 요약의 요약이 아니라 **글의 뼈대**입니다.

- `signal` — **왜 지금인가.** 국면·변곡을 한 줄로.
- `key` — **무엇이 그 근거인가.** 사업의 어느 축이 그렇게 만드는가.
- `step` — **언제 무엇이 확인되는가.** 시간 순서가 드러나야 합니다.

각 줄은 **한 문장, 40자 안팎**입니다. 마침표로 끝내지 마십시오.

  좋은 예
    signal: 실적과 멀티플이 함께 개선되는 국면 진입
    key:    주력 고객사향 반복 수주와 매출 인식 본격화
    step:   올해는 수주, 내년부터 실적 반영

  나쁜 예
    signal: 투자의견 매수 (← 투자의견 금지. 위 규칙 참조)
    key:    실적이 좋다 (← 무엇이 그렇게 만드는지가 없음)
    step:   지속적인 모니터링 필요 (← 검증 불가·실사 어투)

**세 줄은 본문에서 말한 것만 담습니다.** 본문에 없는 새 주장을 여기서 만들지
마십시오 — 아래 글을 압축한 것이지 별개의 결론이 아닙니다.

수치를 넣을 수 있으면 플레이스홀더로 넣으십시오. 다만 **억지로 넣지는
마십시오** — 국면을 말하는 자리라 수치가 없는 것이 정상입니다."""


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
    outline: list[str] | None = None,
) -> str:
    """카탈로그(크기 없음) + 논지 + 과업 지시.

    `thesis`는 결정적 코드가 뽑아낸 관찰이다. 이게 없으면 LLM이 쓸 수 있는
    것이 재무 기계학뿐이라 지표를 하나씩 읊는 글이 된다.

    `outline`은 **사용자가 올린 직전 노트의 차례**다([D48](../../../docs/decisions.md#d48)).
    하우스마다 리포트 구성이 다르고, RA는 자기 형식과 다른 초안을 다시 짜야
    한다. 차례를 주면 그 순서와 강조점에 맞춰 쓴다 — **숫자는 여전히
    카탈로그에서만 나온다.**
    """
    lines = []
    for e in registry.catalog():
        d = f", 방향: {e['direction']}" if e["direction"] != "-" else ""
        lines.append(f"- {{{{num:{e['key']}}}}} — {e['label']} (단위: {e['unit']}{d})")
    head = f"# 대상\n{company_name} · {fiscal_year}년 연간 실적 ({basis}재무제표)\n\n"
    if thesis:
        head += f"# 확인된 관찰 (결정적 계산 결과 — 이것을 논지의 축으로 삼으십시오)\n{thesis}\n\n"
    if outline:
        head += (
            "# 이 독자가 쓰는 리포트 구성 (직전 노트의 차례)\n"
            + "\n".join(f"- {s}" for s in outline[:12])
            + "\n\n이 순서와 강조점에 맞춰 쓰십시오. **차례를 그대로 제목으로 쓰지는 "
            "마십시오** — 아래 과업이 요구하는 섹션에 담되, 이 독자가 중요하게 "
            "보는 것이 무엇인지의 단서로 쓰십시오.\n\n"
        )
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

    # business_narrative는 선택 — 원문 조회가 실패하면 만들 수 없다
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

    # headline도 선택이다. **모양이 틀린 것만 잡는다** — 세 줄은 국면을 말하는
    # 자리라 모델이 못 쓸 때가 있고, 그때 재시도로 밀어붙이면 억지 문장이
    # 나온다. 없으면 화면이 그 칸을 안 세운다.
    head = payload.get("headline")
    if head is not None:
        if not isinstance(head, dict):
            problems.append("headline이 객체가 아님")
        else:
            extra = sorted(set(head) - set(HEADLINE_KEYS))
            if extra:
                problems.append(f"headline에 모르는 칸: {', '.join(extra)}")

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
    outline: list[str] | None = None,
    max_attempts: int = 2,
) -> NarrationResult:
    """LLM으로 섹션 본문을 만든다. 실패 시 problems를 채워 반환한다."""
    user = build_user_prompt(company_name, fiscal_year, basis, registry, thesis, outline)
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
                    # **여기 이름이 없으면 그 칸은 조용히 사라진다.** 모델이
                    # 써 보내도 이 목록에 없으면 버려진다 — 스키마에 넣고도
                    # 화면이 비어서 한참 찾았다 (D87).
                    "headline": {
                        k: str(v).strip()
                        for k, v in (payload.get("headline") or {}).items()
                        if k in HEADLINE_KEYS and str(v).strip()
                    },
                    "summary": payload["summary"],
                    "investment_points": payload["investment_points"],
                    "earnings_narrative": payload["earnings_narrative"],
                    "risks": payload["risks"],
                    "watchpoints": payload.get("watchpoints") or [],
                    "business_narrative": payload.get("business_narrative") or "",
                },
                used_llm=True,
                attempts=attempt,
                completion=last,
            )

    return NarrationResult(
        {}, used_llm=False, attempts=max_attempts, completion=last, problems=problems
    )


# ── 산업 서사 레인 (미검증) ──────────────────────────────────────────
INDUSTRY_SYSTEM_PROMPT = """\
당신은 한국 증권사 리서치센터의 애널리스트입니다. 종목 리포트의 **산업 배경**
문단을 씁니다.

## 이 문단의 성격

앞뒤 섹션과 달리 이 문단은 **공시에 근거가 없습니다.** 회사가 속한 산업의
통상적 이해를 서술하는 자리이고, 독자에게 "AI가 생성한 미검증 서술"로
표시됩니다. 그래서 지켜야 할 것이 더 엄격합니다.

## 절대 규칙 — 숫자

**숫자를 일절 쓰지 마십시오.** 시장 규모, 성장률, 점유율, 순위, 금액 —
어느 것도 쓰지 않습니다. 이 문단에는 검증할 수단이 없으므로 숫자가 들어가면
그 숫자는 아무도 확인할 수 없습니다. 숫자가 하나라도 있으면 이 문단은
리포트에서 통째로 삭제됩니다.

연도(2025년)도 쓰지 마십시오.

## 절대 규칙 — 단정과 고유명사

- 특정 경쟁사·거래처의 이름을 지어내지 마십시오.
- "1위", "선도", "독점" 같은 지위 주장을 하지 마십시오.
- 법·제도 변화를 사실로 단정하지 마십시오.
- 목표주가·투자의견·매수/매도 표현은 금지입니다.

## 쓰는 법

- 이 회사가 속한 산업이 **어떤 구조인가**: 수요가 어디서 오고, 진입장벽이
  무엇이고, 가격이 어떻게 결정되는가.
- 제공된 사업 서술에 나온 제품·기술의 **산업적 위치**.
- 확실하지 않은 것은 "일반적으로", "통상"으로 표현하고 단정하지 않습니다.
- 3~5문장. 한 문단.

## 출력 형식

아래 JSON만 출력하십시오.

{"industry_context": "3~5문장"}"""


def build_industry_prompt(company_name: str, profile_text: str, segments: list[str]) -> str:
    parts = [f"# 대상\n{company_name}\n"]
    if profile_text:
        parts.append(f"# 회사가 공시한 사업 서술 (숫자는 가려져 있습니다)\n{profile_text}\n")
    if segments:
        parts.append(f"# 공시된 매출 부문\n{', '.join(segments)}\n")
    parts.append(
        "# 과업\n이 회사가 속한 산업의 구조를 서술하십시오. **숫자는 쓰지 않습니다.** JSON만 출력합니다."
    )
    return "\n".join(parts)


def narrate_industry(
    client: LLMClient,
    *,
    company_name: str,
    profile_text: str,
    segments: list[str],
    registry: NumberRegistry | None = None,
) -> tuple[str, list[str]]:
    """산업 배경 문단. `(본문, 문제 목록)`.

    **실패하면 빈 문자열을 돌려준다 — 리포트를 막지 않는다.** 이 문단은
    있으면 좋은 것이지 없으면 발간 못 하는 것이 아니다.

    숫자가 하나라도 있으면 **문단을 버린다.** 리포트 전체를 차단하지 않는
    이유는, 검증 불가능한 레인 하나 때문에 검증된 나머지를 못 내보내는 것이
    합리적이지 않기 때문이다. 대신 그 레인만 조용히 사라진다.
    """
    if not profile_text:
        return "", ["사업 서술이 없어 산업 배경을 만들지 않았다."]
    try:
        completion = client.complete(
            system=INDUSTRY_SYSTEM_PROMPT,
            user=build_industry_prompt(company_name, profile_text, segments),
            tier=Tier.WRITE,
        )
        payload = parse_response(completion.text)
    except Exception as exc:  # noqa: BLE001 — provider별 예외가 다르다
        return "", [f"{type(exc).__name__}: {exc}"]

    text = str(payload.get("industry_context") or "").strip()
    if not text:
        return "", ["산업 배경이 비어 있다."]

    # 다른 섹션보다 **엄격하다.** G0 화이트리스트는 연도를 허용하지만, 이
    # 레인에는 검증 수단이 없어 "2025년부터 규제가 바뀌었다" 같은 연도 주장도
    # 아무도 확인할 수 없다. 숫자는 하나도 두지 않는다.
    digit = re.search(r"\d", text)
    if digit:
        return "", [f"산업 배경에 검증 불가능한 숫자가 있어 버렸다: {digit.group(0)!r}"]
    return text, []


NEWS_SYSTEM_PROMPT = """\
당신은 한국 증권사 리서치센터의 애널리스트입니다. 종목 리포트의 **최근 이슈**
문단을 씁니다.

## 이 문단의 성격

주어진 **기사 스니펫만** 근거로 씁니다. 공시가 아니라 언론 보도이므로
독자에게 「AI가 정리한 미검증 서술」로 표시되고, 각 기사의 매체·날짜·링크가
문단 아래에 함께 실립니다.

## 절대 규칙 — 숫자

**숫자를 일절 쓰지 마십시오.** 금액·성장률·점유율·순위·연도 어느 것도 쓰지
않습니다. 스니펫의 숫자는 이미 가려져 있습니다(⟨수치⟩). 가려진 자리를
추측해 복원하지 마십시오. 숫자가 하나라도 있으면 이 문단은 통째로 삭제됩니다.

## 절대 규칙 — 주어진 것 밖으로 나가지 않기

- 스니펫에 없는 사실을 덧붙이지 마십시오. 기억에 있는 뉴스도 쓰지 않습니다.
- 보도를 사실로 단정하지 마십시오. "보도됐다", "알려졌다"로 씁니다.
- 목표주가·투자의견·매수/매도 표현은 금지입니다.
- 기사가 회사와 무관하면 그 기사는 버리십시오. 동명이인·동명 회사가 섞입니다.

## 쓰는 법

- **실적 숫자를 되풀이하지 마십시오.** 그건 리포트의 다른 절이 공시로 이미
  말합니다. 여기서는 **공시에 없는 것**을 씁니다 — 수주·계약·규제·소송·
  경영권·설비투자·인허가처럼 다음 분기 숫자를 바꿀 사건.
- 여러 기사가 같은 사건을 말하면 하나로 묶으십시오.
- 쓸 만한 것이 없으면 빈 문자열을 내십시오. **억지로 채우지 않습니다.**
- 3~5문장. 한 문단.

## 출력 형식

아래 JSON만 출력하십시오.

{"recent_issues": "3~5문장 또는 빈 문자열"}"""


def build_news_prompt(company_name: str, articles: list[dict[str, str]]) -> str:
    lines = [f"# 대상\n{company_name}\n", "# 기사 스니펫 (숫자는 가려져 있습니다)"]
    for i, a in enumerate(articles, 1):
        lines.append(f"[{i}] {a['title']}\n    {a['snippet']}")
    lines.append(
        "\n# 과업\n다음 분기 숫자를 바꿀 만한 사건을 정리하십시오. "
        "**숫자는 쓰지 않습니다.** JSON만 출력합니다."
    )
    return "\n".join(lines)


def narrate_news(
    client: LLMClient,
    *,
    company_name: str,
    articles: list[dict[str, str]],
) -> tuple[str, list[str]]:
    """최근 이슈 문단. `(본문, 문제 목록)`.

    **산업 배경과 같은 레인이다** (D31) — 검증 수단이 없으므로 숫자를 두지
    않는다. 다른 점은 근거가 모델의 기억이 아니라 **날짜와 링크가 붙은
    기사**라는 것이다. 그래서 독자가 되짚을 수 있다.

    실패하면 빈 문자열을 준다. 리포트를 막지 않는다.
    """
    if not articles:
        return "", ["기사가 없어 최근 이슈를 만들지 않았다."]
    try:
        completion = client.complete(
            system=NEWS_SYSTEM_PROMPT,
            user=build_news_prompt(company_name, articles),
            tier=Tier.WRITE,
        )
        payload = parse_response(completion.text)
    except Exception as exc:  # noqa: BLE001 — provider별 예외가 다르다
        return "", [f"{type(exc).__name__}: {exc}"]

    text = str(payload.get("recent_issues") or "").strip()
    if not text:
        return "", ["쓸 만한 이슈가 없어 비웠다."]
    digit = re.search(r"\d", text)
    if digit:
        return "", [f"최근 이슈에 검증 불가능한 숫자가 있어 버렸다: {digit.group(0)!r}"]
    return text, []
