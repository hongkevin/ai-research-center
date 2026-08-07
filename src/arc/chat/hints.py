"""세 번째 레인 — 기사에서 온 **힌트**. 검증된 것이 아니라 되짚을 수 있는 것.

[D45](../../../docs/decisions.md#d45)를 그대로 따른다. 완화하지 않는다.

* 스니펫과 제목의 숫자를 `mask_numbers()`로 가려서 넣는다. 탐지와 같은
  화이트리스트를 쓰므로 가린 텍스트는 게이트를 통과하는 것이 보장된다.
* 나온 문장에 숫자가 하나라도 있으면 **그 힌트를 버린다.** 답변 전체를 막지는
  않는다 — 검증 불가능한 레인 하나 때문에 검증된 나머지를 못 내보내는 것은
  합리적이지 않다.
* **링크가 없는 힌트는 버린다.** 이 레인을 허용할 수 있는 근거가 「독자가
  원문으로 갈 수 있다」 하나뿐이기 때문이다. 링크가 빠지면 그냥 모델의 기억이다.

**별도 호출이다.** 검증 레인과 같은 호출에 섞으면 재무 서술로 변질된다
([D31](../../../docs/decisions.md#d31)). 그리고 섞이면 어느 문장이 어느 레인의
것인지 화면이 가릴 수 없다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from arc.data.base import NewsItem
from arc.data.kr.news_filter import press_name
from arc.llm.client import LLMClient, Tier
from arc.llm.narrate import parse_response
from arc.llm.number_registry import mask_numbers

SYSTEM_PROMPT = """\
당신은 한국 증권사 리서치센터의 애널리스트를 돕는 리서치 어시스턴트입니다.
지금 쓰는 것은 **공시로 확인되지 않은 힌트**입니다.

## 이 레인의 성격

앞선 답변은 공시에서 확인된 숫자였습니다. 여기는 다릅니다 — 근거가 **언론
보도**이고, 우리가 검산한 것이 아닙니다. 독자에게 「AI가 정리한 미검증 서술」로
표시되고 각 힌트에 매체·날짜·링크가 함께 붙습니다.

그래서 이 레인의 값어치는 **되짚을 수 있다**는 것 하나입니다. 주어진 기사
밖으로 한 걸음이라도 나가면 그 값어치가 사라집니다.

## 절대 규칙 — 숫자

**숫자를 일절 쓰지 마십시오.** 금액·성장률·점유율·순위·연도 어느 것도 쓰지
않습니다. 스니펫의 숫자는 이미 가려져 있습니다(⟨수치⟩). 가려진 자리를 추측해
복원하지 마십시오. 숫자가 하나라도 있으면 그 힌트는 통째로 버려집니다.

## 절대 규칙 — 주어진 것 밖으로 나가지 않기

- 스니펫에 없는 사실을 덧붙이지 마십시오. 기억에 있는 뉴스도 쓰지 않습니다.
- 보도를 사실로 단정하지 마십시오. "보도됐다", "알려졌다"로 씁니다.
- 목표주가·투자의견·매수/매도·비중확대 표현은 금지입니다.
- 기사가 회사와 무관하면 버리십시오. 동명 회사가 섞입니다.

## 쓰는 법

- **질문에 걸리는 것만** 씁니다. 질문과 상관없는 기사는 버립니다.
- **공시 숫자를 되풀이하지 마십시오.** 그건 앞 레인이 이미 말했습니다. 여기서는
  **공시에 없는 것**을 씁니다 — 수주·계약·규제·소송·경영권·설비투자·인허가처럼
  다음 분기 숫자를 바꿀 사건.
- 여러 기사가 같은 사건을 말하면 하나로 묶으십시오.
- 각 힌트에 **근거 기사 번호를 반드시** 답니다. 번호를 못 달면 그 힌트를
  쓰지 마십시오.
- 힌트 하나는 1~2문장. 최대 3개.
- 쓸 만한 것이 없으면 빈 배열을 내십시오. **억지로 채우지 않습니다.**

## 출력 형식

아래 JSON만 출력하십시오.

{"hints": [{"text": "1~2문장", "articles": [1, 3]}]}"""


@dataclass(frozen=True)
class Article:
    """힌트가 딛고 선 기사 하나. 제목의 숫자는 가려져 있다."""

    title: str
    url: str
    press: str
    date: str


@dataclass(frozen=True)
class Hint:
    """미검증 힌트 하나. **링크 없이는 존재할 수 없다.**"""

    text: str
    articles: tuple[Article, ...] = field(default_factory=tuple)


@dataclass
class HintResult:
    """힌트 레인의 산출 + 진단 (`narrate.NarrationResult`와 같은 모양)."""

    hints: list[Hint] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    used_llm: bool = False
    model: str = ""
    cost_usd: float | None = None


def to_articles(items: list[NewsItem]) -> list[Article]:
    return [
        Article(
            title=mask_numbers(item.title),
            url=item.url,
            press=press_name(item.url),
            date=item.published_at.date().isoformat() if item.published_at else "—",
        )
        for item in items
    ]


def build_prompt(question: str, company: str, items: list[NewsItem]) -> str:
    lines = [f"# 질문\n{question}\n", f"# 대상\n{company}\n", "# 기사 (숫자는 가려져 있습니다)"]
    for i, item in enumerate(items, 1):
        lines.append(f"[{i}] {mask_numbers(item.title)}\n    {mask_numbers(item.snippet)}")
    lines.append(
        "\n# 과업\n이 질문과 관련해 **다음 분기 숫자를 바꿀 만한 사건**을 정리하십시오. "
        "**숫자는 쓰지 않습니다.** 근거 기사 번호를 반드시 답니다. JSON만 출력합니다."
    )
    return "\n".join(lines)


def build_hints(
    client: LLMClient,
    *,
    question: str,
    company: str,
    items: list[NewsItem],
    max_tokens: int = 1024,
) -> HintResult:
    """기사 → 힌트. **실패해도 답변을 막지 않는다.**"""
    if not items:
        return HintResult(problems=["기사가 없어 힌트를 만들지 않았습니다."])

    try:
        completion = client.complete(
            system=SYSTEM_PROMPT,
            user=build_prompt(question, company, items),
            tier=Tier.WRITE,
            max_tokens=max_tokens,
        )
        payload = parse_response(completion.text)
    except Exception as exc:  # noqa: BLE001 — provider별 예외가 다르다
        return HintResult(problems=[f"{type(exc).__name__}: {exc}"])

    articles = to_articles(items)
    out: list[Hint] = []
    problems: list[str] = []
    for raw in payload.get("hints") or []:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        digit = re.search(r"\d", text)
        if digit:
            problems.append(f"숫자가 있어 힌트를 버렸습니다: {digit.group(0)!r}")
            continue
        cited = [articles[i - 1] for i in _indices(raw.get("articles"), len(articles))]
        if not cited:
            problems.append("근거 기사가 없어 힌트를 버렸습니다.")
            continue
        out.append(Hint(text=text, articles=tuple(cited)))
    return HintResult(
        hints=out[:3],
        problems=problems,
        used_llm=True,
        model=completion.model,
        cost_usd=completion.cost_usd,
    )


def _indices(raw: object, total: int) -> list[int]:
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for value in raw:
        try:
            i = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= i <= total and i not in out:
            out.append(i)
    return out
