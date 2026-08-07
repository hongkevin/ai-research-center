"""리서치 채팅의 답변 — 카드를 근거로만 말하고, 항목마다 출처를 낸다.

이 파일이 지키는 것 셋
----------------------
1. **근거 밖은 말하지 않는다.** 검색이 비면 LLM을 부르지도 않는다. 부르면
   모델은 아는 것을 말하고, 그 순간 이 시스템은 범용 챗봇이 된다
   (`research/10-prism-insight-and-scope.md` §4 축 2).
2. **투자의견·목표주가·매매 판단을 내지 않는다** ([D4](../../../docs/decisions.md#d4)).
   질문에서 한 번, 답변에서 한 번 — 두 곳에서 막는다.
3. **출처는 항목마다 다르다** ([D36](../../../docs/decisions.md#d36)).
   수치 하나하나가 자기 `Provenance`를 들고 나오므로 `sources`는 답변 전체가
   아니라 **인용된 수치마다** 한 줄이다.

값은 프롬프트에 들어가지 않는다
-------------------------------
카드 본문(`assembled`)의 숫자는 이미 플레이스홀더이고, 카탈로그는 키·라벨·
단위만 준다(`narrate.py`·`revise.py`와 같은 규칙). 그래서 LLM이 답을 써도
**숫자는 구조적으로 지어낼 수 없다** — 지어내면 게이트가 그 문장을 버린다.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field

from arc.chat.evidence import CardRef
from arc.chat.guard import NO_EVIDENCE, POLICY_REFUSAL, asks_for_opinion, check_answer
from arc.chat.retrieval import Retrieval, retrieve
from arc.llm.client import LLMClient, Tier
from arc.llm.narrate import parse_response
from arc.store.cards import Card

SYSTEM_PROMPT = """\
당신은 한국 증권사 리서치센터의 애널리스트를 돕는 리서치 어시스턴트입니다.
**주어진 근거만으로** 질문에 답합니다.

## 절대 규칙 — 모르면 모른다고 합니다

근거에 없는 것은 지어내지 마십시오. 기억하고 있는 시장 지식, 뉴스, 업계
관행도 쓰지 않습니다. 근거가 없는 부분은 그 자리에서 이렇게 씁니다:

    확인할 수 있는 근거가 없습니다.

답의 일부만 근거가 있으면, **있는 부분만 답하고 없는 부분은 `unanswered`에
적으십시오.** 그럴듯하게 메우는 것이 가장 나쁜 답입니다.

## 절대 규칙 — 숫자

본문에 **숫자를 직접 쓰면 안 됩니다.** 수치는 아래 형식으로만 씁니다.

    {{num:키}}

키는 「수치 카탈로그」에 있는 것만 쓸 수 있습니다. 값이 얼마인지는 알려주지
않으며 알 필요도 없습니다. 카탈로그에 없는 키를 지어내거나 숫자를 직접 쓰면
그 문장은 버려집니다.

금지: "매출은 1조 4,575억원이다", "약 20% 늘었다"
허용: "매출은 {{num:c1.revenue_2026a}}이다 [c1]"

연도(2026년), 분기(1분기)는 숫자로 써도 됩니다.

## 절대 규칙 — 투자 판단

목표주가·투자의견·상승여력·매수/매도·비중확대 같은 표현을 **일절 쓰지
않습니다.** "제시하지 않는다"처럼 부정문으로 언급하는 것도 금지입니다.
그런 것을 묻는 질문에는 답하지 않습니다.

단정 표현("반드시", "확실히", "급등")도 금지입니다.

## 절대 규칙 — 문장마다 출처

**모든 사실 주장 끝에 어느 카드에서 왔는지 표시합니다.** 표시는 대괄호에
카드 꼬리표를 넣은 형태입니다.

    영업이익률은 {{num:c1.operating_margin_2026a}}이다 [c1]

주어진 카드 꼬리표만 쓸 수 있습니다. 없는 꼬리표를 쓰면 문장이 버려집니다.

## 쓰는 법

- 결론을 먼저 씁니다. 질문에 대한 답이 첫 문장입니다.
- 근거에 있는 말로 씁니다. 근거를 해석해 새 주장을 만들지 마십시오.
- 3~6문장. 목록이 읽기 좋으면 `- `로 씁니다.
- 근거가 서로 다른 카드에서 왔으면 어느 카드의 얘기인지 분명히 씁니다.

## 출력 형식

아래 JSON만 출력하십시오. 코드펜스나 설명을 붙이지 마십시오.

{
  "answer": "답변 본문",
  "unanswered": ["근거가 없어 답하지 못한 것"]
}

`unanswered`는 답한 것이 아니라 **답하지 못한 것**을 적는 자리입니다.
전부 답했으면 빈 배열입니다."""


@dataclass(frozen=True)
class Source:
    """답변이 인용한 것 하나. 수치면 항목별 출처, 카드면 어느 절인지."""

    kind: str  # "number" | "card"
    marker: str  # 본문에 붙은 표시 — "[c1]"
    card_id: str
    symbol: str
    company: str
    period_label: str
    key: str = ""  # 레지스트리 키 (꼬리표를 뗀 원래 키)
    label: str = ""
    value: str = ""  # 치환된 표시 문자열
    formula: str = ""
    sections: tuple[str, ...] = ()  # 근거로 쓴 카드 안의 절
    dataset: str = ""  # 사람이 읽는 출처 이름 (D36)
    document: str = ""  # DART 접수번호
    verify_url: str = ""  # 사람이 열어 확인할 링크
    source_url: str = ""  # 우리가 호출한 곳


@dataclass
class Answer:
    """질의응답 1건의 결과."""

    text: str
    sources: list[Source] = field(default_factory=list)
    unanswered: list[str] = field(default_factory=list)
    # **확신도가 아니다.** "이 답변의 문장이 근거에 연결돼 있는가"라는 사실
    # 판정이다. 확률을 내면 읽는 사람이 그것을 정확도로 오해한다.
    grounded: bool = False
    rejected: list[str] = field(default_factory=list)  # 검증에서 버린 문장
    unsourced: list[str] = field(default_factory=list)  # 출처 표시가 없던 문장
    refused: str = ""  # 답을 내지 않은 이유 (D4 등)
    cards: list[CardRef] = field(default_factory=list)
    used_llm: bool = False
    model: str = ""
    cost_usd: float | None = None
    problems: list[str] = field(default_factory=list)  # 진단 (사용자용 아님)


def build_prompt(retrieval: Retrieval) -> str:
    """근거 카드 · 발췌 · 수치 카탈로그. **값은 넣지 않는다.**"""
    parts = [f"# 질문\n{retrieval.question}\n"]

    parts.append("# 근거 카드")
    for card in retrieval.cards:
        parts.append(f"- [{card.tag}] {card.label}")
    parts.append("")

    if retrieval.passages:
        parts.append("# 근거 발췌 (카드 본문. 숫자는 플레이스홀더로 들어 있습니다)")
        for p in retrieval.passages:
            parts.append(f"\n## [{p.tag}] {p.section}\n{p.text}")
        parts.append("")

    lines = []
    for key in retrieval.keys:
        entry = retrieval.registry._entries.get(key)
        if entry is None or entry.internal:
            continue
        direction = entry.direction()
        suffix = f", 방향: {direction}" if direction != "-" else ""
        lines.append(f"- {{{{num:{key}}}}} — {entry.label or key} (단위: {entry.unit}{suffix})")
    if lines:
        parts.append("# 수치 카탈로그 (크기는 제공하지 않습니다. 키만 쓰십시오)")
        parts.extend(lines)
        parts.append("")

    parts.append(
        "# 과업\n위 근거만으로 질문에 답하십시오. 근거에 없는 것은 "
        f"«{NO_EVIDENCE}»라고 쓰고 `unanswered`에 적습니다. JSON만 출력합니다."
    )
    return "\n".join(parts)


def _sources_for(retrieval: Retrieval, keys: list[str], markers: list[str]) -> list[Source]:
    """인용된 수치와 카드를 출처 줄로. **수치가 먼저** — 항목별 출처가 핵심이다."""
    out: list[Source] = []
    cited_tags: list[str] = []

    for key in keys:
        entry = retrieval.registry._entries.get(key)
        if entry is None:
            continue
        tag, _, plain = key.partition(".")
        card = retrieval.card_of(tag)
        if card is None:
            continue
        cited_tags.append(tag)
        prov = entry.provenance
        out.append(
            Source(
                kind="number",
                marker=f"[{tag}]",
                card_id=card.id,
                symbol=card.symbol,
                company=card.company,
                period_label=card.label,
                key=plain,
                label=entry.label or plain,
                value=entry.rendered(),
                formula=entry.formula or "",
                dataset=prov.describe if prov else "",
                document=(prov.source_ref or "") if prov else "",
                verify_url=(prov.verify_url or "") if prov else "",
                source_url=(prov.source_url or "") if prov else "",
            )
        )

    for tag in dict.fromkeys(markers + cited_tags):
        card = retrieval.card_of(tag)
        if card is None:
            continue
        sections = tuple(dict.fromkeys(p.section for p in retrieval.passages if p.tag == tag))
        out.append(
            Source(
                kind="card",
                marker=f"[{tag}]",
                card_id=card.id,
                symbol=card.symbol,
                company=card.company,
                period_label=card.label,
                label=card.label,
                sections=sections,
            )
        )
    return out


def _no_evidence(question: str, retrieval: Retrieval) -> Answer:
    reason = retrieval.reason or "질문과 겹치는 근거가 카드에 없습니다."
    return Answer(
        text=f"{NO_EVIDENCE} {reason}",
        unanswered=[question],
        grounded=False,
        problems=[reason],
    )


def answer_question(
    question: str,
    cards: Sequence[Card],
    *,
    client: LLMClient | None = None,
    max_cards: int = 2,
    max_passages: int = 8,
    max_attempts: int = 2,
    max_tokens: int = 1536,
) -> Answer:
    """카드를 근거로 질문에 답한다. 근거가 없으면 없다고 답한다.

    `client`가 없으면 **문장을 만들지 않고** 찾은 근거만 돌려준다. 키 없는
    환경에서도 검색·출처는 그대로 동작해야 한다.
    """
    refusal = asks_for_opinion(question)
    if refusal:
        return Answer(text=POLICY_REFUSAL, grounded=False, refused=refusal, unanswered=[question])

    retrieval = retrieve(question, cards, max_cards=max_cards, max_passages=max_passages)
    if retrieval.empty:
        return _no_evidence(question, retrieval)

    if client is None:
        return Answer(
            text="서술 레이어(LLM)가 꺼져 있어 문장을 만들지 않았습니다. 아래 근거를 확인하십시오.",
            sources=_sources_for(retrieval, retrieval.keys, retrieval.tags()),
            unanswered=[question],
            grounded=False,
            cards=retrieval.cards,
            problems=["LLM 클라이언트가 없습니다."],
        )

    user = build_prompt(retrieval)
    problems: list[str] = []
    last = None
    payload: dict = {}

    for attempt in range(1, max_attempts + 1):
        prompt = user
        if problems:
            prompt += (
                "\n\n# 직전 시도의 문제\n"
                + "\n".join(f"- {p}" for p in problems)
                + "\n이 문제를 고쳐 다시 출력하십시오."
            )
        try:
            last = client.complete(
                system=SYSTEM_PROMPT, user=prompt, tier=Tier.WRITE, max_tokens=max_tokens
            )
            payload = parse_response(last.text)
        except json.JSONDecodeError as exc:
            problems = [f"JSON 파싱 실패: {exc}"]
            continue
        except Exception as exc:  # noqa: BLE001 — provider별 예외가 다르다
            return Answer(
                text=f"{NO_EVIDENCE} 서술 레이어 호출이 실패했습니다.",
                cards=retrieval.cards,
                unanswered=[question],
                problems=[f"{type(exc).__name__}: {exc}"],
            )
        if str(payload.get("answer") or "").strip():
            problems = []
            break
        problems = ["answer가 비어 있습니다."]
        payload = {}

    raw = str(payload.get("answer") or "").strip()
    if not raw:
        return Answer(
            text=f"{NO_EVIDENCE} 서술 레이어가 답을 만들지 못했습니다.",
            cards=retrieval.cards,
            unanswered=[question],
            problems=problems,
        )

    verdict = check_answer(raw, retrieval)
    model = last.model if last else ""
    cost = last.cost_usd if last else None

    if verdict.refused:
        return Answer(
            text=POLICY_REFUSAL,
            refused=verdict.refused,
            cards=retrieval.cards,
            unanswered=[question],
            used_llm=True,
            model=model,
            cost_usd=cost,
        )
    if not verdict.text.strip():
        return Answer(
            text=f"{NO_EVIDENCE} 답변의 모든 문장이 근거 검증에서 걸러졌습니다.",
            rejected=verdict.rejected,
            cards=retrieval.cards,
            unanswered=[question],
            used_llm=True,
            model=model,
            cost_usd=cost,
            problems=problems,
        )

    unanswered = [str(u).strip() for u in (payload.get("unanswered") or []) if str(u).strip()]
    if retrieval.unmatched:
        unanswered.append(
            f"«{', '.join(retrieval.unmatched)}» — 카드에서 해당 근거를 찾지 못했습니다."
        )

    sources = _sources_for(retrieval, verdict.keys, verdict.markers)
    return Answer(
        # 마지막에 치환한다. 조사 교정도 여기서 함께 일어난다 (D23).
        text=retrieval.registry.render_text(verdict.text),
        sources=sources,
        unanswered=list(dict.fromkeys(unanswered)),
        grounded=bool(sources),
        rejected=verdict.rejected,
        unsourced=verdict.unsourced,
        cards=retrieval.cards,
        used_llm=True,
        model=model,
        cost_usd=cost,
        problems=problems,
    )
