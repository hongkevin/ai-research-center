"""리서치 채팅의 답변 — **레인 셋**. 확인된 사실 · 그것을 읽은 분석 · 기사 힌트.

왜 하나가 아니라 셋인가
-----------------------
처음에는 레인이 하나였고, 근거에 없는 것은 전부 "확인할 수 있는 근거가
없습니다"로 떨어졌다. 실측에서 그 결과가 나왔다 — 부문 표를 통째로 주고도
"어디가 제일 버는지 모릅니다"로 끝났다. 담을 자리가 없어서지 몰라서가 아니었다.

RA가 듣고 싶은 것은 성격이 다른 셋이고, **신뢰 수준도 셋이 다르다.**

    사실(facts)     공시 수치. 항목마다 출처가 붙고 숫자는 레지스트리가 만든다
    분석(analysis)  그 사실들을 연결해 읽은 것. 새 사실은 없고 근거는 위와 같다
    힌트(hints)     기사에서 온 추측. 검증한 것이 아니라 **되짚을 수 있는** 것

**셋을 한 문자열로 합치지 않는다.** 합치면 화면이 「⚠ 미검증」 배지를 붙일
자리를 잃고, 세 번째 레인의 문장이 첫 번째 레인의 신뢰도로 읽힌다. 그것이
[D31](../../../docs/decisions.md#d31)·[D45](../../../docs/decisions.md#d45)가
경계한 바로 그 사고다. `text`는 검증 레인만 담고 `hints`는 따로 나간다.

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

분석 레인이 크기를 못 봐서 벙어리가 되는 자리는 `observations.py`가 메운다 —
결정적 코드가 **순서**를 계산해 넘긴다. 순서는 크기가 아니다.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from arc.chat.evidence import CardRef
from arc.chat.guard import NO_EVIDENCE, POLICY_REFUSAL, asks_for_opinion, check_answer
from arc.chat.hints import Hint, HintResult, build_hints
from arc.chat.observations import rank_observations
from arc.chat.retrieval import Retrieval, retrieve
from arc.data.base import NewsItem
from arc.llm.client import LLMClient, Tier
from arc.llm.narrate import parse_response
from arc.store.cards import Card

# 회사명 → 기사. **주입한다** — 키가 없는 환경에서 테스트가 돌아야 하고,
# 넣지 않으면 힌트 레인이 통째로 꺼진다 (D45: 못 하면 못 한다고 쓴다).
NewsFetcher = Callable[[str], list[NewsItem]]

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

## 두 부분으로 나눠 씁니다

**`facts` — 확인된 사실.** 질문이 물은 수치를 그대로 전달합니다. 해석을 섞지
마십시오. 2~4문장, 또는 항목이 여럿이면 `- ` 목록.

    매출은 {{num:c1.revenue_2026a}}, 전년 대비 {{num:c1.revenue_yoy_2026a}} 변동했다 [c1]

**`analysis` — 그 사실들을 읽은 것.** 여기서는 **자신 있게 씁니다.** 다만
자신감의 대상은 판단이 아니라 **읽기**입니다.

- 서로 다른 지표를 **연결**하십시오. 하나씩 읊는 글은 실패입니다.
  "외형보다 이익이 빠르게 늘어 영업 레버리지가 작동했다 [c1]"
- 「확인된 관찰」에 순서가 주어졌으면 그것을 근거로 쓰십시오. 순서는
  결정적 코드가 계산한 사실입니다.
- **"추가 확인이 필요하다"를 쓰지 마십시오.** 실사 보고서 어투입니다. 같은
  불확실성은 "관건은 ~다", "~에서 갈린다"로 씁니다.
- 근거에 없는 사실을 만들지 마십시오. 연결과 해석은 하되 **재료는 위와 같습니다.**
- 2~4문장.

**크기 비교는 함부로 하지 마십시오.** 카탈로그는 크기를 주지 않습니다. 순서가
「확인된 관찰」에 주어진 것만 비교할 수 있고, 없으면 `unanswered`에 적습니다.
"크게", "소폭", "급격히" 같은 정도 표현도 금지입니다.

## 쓰는 법

- 결론을 먼저 씁니다. 질문에 대한 답이 `facts`의 첫 문장입니다.
- 근거가 서로 다른 카드에서 왔으면 어느 카드의 얘기인지 분명히 씁니다.

## 출력 형식

아래 JSON만 출력하십시오. 코드펜스나 설명을 붙이지 마십시오.

{
  "facts": "확인된 수치 전달",
  "analysis": "그 수치를 읽은 것",
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
    """질의응답 1건의 결과. **레인 셋이 섞이지 않은 채로** 나간다."""

    # 검증 레인 = `facts` + `analysis`. 여기 숫자는 전부 레지스트리가 만들었고
    # 게이트를 통과했다. 화면은 이것을 그대로 본문으로 쓴다.
    text: str
    facts: str = ""
    analysis: str = ""
    # **미검증 레인.** 절대 `text`에 합치지 않는다 — 합치면 배지를 붙일 자리가
    # 없어지고 기사에서 온 말이 공시에서 온 말처럼 읽힌다 (D31·D45).
    hints: list[Hint] = field(default_factory=list)
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

    # 결정적 코드가 계산한 순서. 크기를 안 주면서 비교를 가능하게 하는 유일한
    # 재료다 (`observations.py`).
    ranks = rank_observations(retrieval.registry, retrieval.keys)
    if ranks:
        parts.append("# 확인된 관찰 (결정적 계산 결과 — 이것은 사실입니다)")
        parts.extend(f"- {line}" for line in ranks)
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


def _no_evidence(question: str, retrieval: Retrieval, hint: HintResult | None = None) -> Answer:
    """공시 근거가 없다. **그래도 힌트 레인은 살아 있을 수 있다.**

    「공시에서 확인할 수 없다」와 「아무것도 말할 게 없다」는 다르다. 수주·계약·
    규제처럼 공시에 아직 없는 것을 물었을 때가 기사 힌트가 가장 쓸모 있는
    순간이고, 그것이 미검증 표시를 달고 나가는 한 정직하다.
    """
    reason = retrieval.reason or "질문과 겹치는 근거가 카드에 없습니다."
    hint = hint or HintResult()
    return Answer(
        text=f"{NO_EVIDENCE} {reason}",
        hints=hint.hints,
        unanswered=[question],
        grounded=False,
        used_llm=hint.used_llm,
        model=hint.model,
        cost_usd=hint.cost_usd,
        problems=[reason, *hint.problems],
    )


def answer_question(
    question: str,
    cards: Sequence[Card],
    *,
    client: LLMClient | None = None,
    news: NewsFetcher | None = None,
    max_cards: int = 2,
    max_passages: int = 8,
    max_attempts: int = 2,
    max_tokens: int = 1536,
) -> Answer:
    """카드를 근거로 질문에 답한다. 근거가 없으면 없다고 답한다.

    `client`가 없으면 **문장을 만들지 않고** 찾은 근거만 돌려준다. 키 없는
    환경에서도 검색·출처는 그대로 동작해야 한다.

    `news`를 넣으면 **세 번째 레인**이 열린다 — 기사에서 온 힌트. 호출은
    검증 레인과 분리된다(D31). 넣지 않으면 그 레인은 꺼진 채로 있고, 그
    사실이 `problems`에 남는다.
    """
    refusal = asks_for_opinion(question)
    if refusal:
        return Answer(text=POLICY_REFUSAL, grounded=False, refused=refusal, unanswered=[question])

    retrieval = retrieve(question, cards, max_cards=max_cards, max_passages=max_passages)
    if retrieval.empty:
        if client is None:
            return _no_evidence(question, retrieval)
        return _no_evidence(question, retrieval, _hints_lane(question, retrieval, client, news))

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
        if str(payload.get("facts") or "").strip():
            problems = []
            break
        problems = ["facts가 비어 있습니다."]
        payload = {}

    facts_raw = str(payload.get("facts") or "").strip()
    if not facts_raw:
        return Answer(
            text=f"{NO_EVIDENCE} 서술 레이어가 답을 만들지 못했습니다.",
            cards=retrieval.cards,
            unanswered=[question],
            problems=problems,
        )
    analysis_raw = str(payload.get("analysis") or "").strip()

    # **두 레인을 따로 검사한다.** 분석이 걸려도 사실은 남아야 한다 — 사실이
    # 이 답변의 값어치이고, 해석 하나 때문에 그것까지 버릴 이유가 없다.
    verdict = check_answer(facts_raw, retrieval)
    analysis_verdict = check_answer(analysis_raw, retrieval) if analysis_raw else None
    model = last.model if last else ""
    cost = last.cost_usd if last else None

    if analysis_verdict is not None and analysis_verdict.refused and not verdict.refused:
        verdict.refused = analysis_verdict.refused

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

    keys = list(verdict.keys)
    markers = list(verdict.markers)
    rejected = list(verdict.rejected)
    unsourced = list(verdict.unsourced)
    analysis = ""
    if analysis_verdict is not None:
        keys += [k for k in analysis_verdict.keys if k not in keys]
        markers += [m for m in analysis_verdict.markers if m not in markers]
        rejected += analysis_verdict.rejected
        unsourced += analysis_verdict.unsourced
        # 마지막에 치환한다. 조사 교정도 여기서 함께 일어난다 (D23).
        analysis = retrieval.registry.render_text(analysis_verdict.text)

    facts = retrieval.registry.render_text(verdict.text)
    hint = _hints_lane(question, retrieval, client, news)

    sources = _sources_for(retrieval, keys, markers)
    return Answer(
        # **검증 레인만** 담는다. 힌트는 `hints`로 따로 나간다.
        text="\n\n".join(p for p in (facts, analysis) if p),
        facts=facts,
        analysis=analysis,
        hints=hint.hints,
        sources=sources,
        unanswered=list(dict.fromkeys(unanswered)),
        grounded=bool(sources),
        rejected=rejected,
        unsourced=unsourced,
        cards=retrieval.cards,
        used_llm=True,
        model=model,
        # 두 레인의 호출을 합산한다. 화면에 한 질문의 값이 나와야 한다.
        cost_usd=_add(cost, hint.cost_usd),
        problems=problems + hint.problems,
    )


def _add(a: float | None, b: float | None) -> float | None:
    return None if a is None and b is None else (a or 0.0) + (b or 0.0)


def _hints_lane(
    question: str,
    retrieval: Retrieval,
    client: LLMClient,
    news: NewsFetcher | None,
) -> HintResult:
    """세 번째 레인. **실패해도 앞의 두 레인을 막지 않는다.**"""
    if news is None:
        return HintResult(problems=["기사 검색이 연결되지 않아 힌트 레인은 꺼져 있습니다."])
    if retrieval.subject is None:
        return HintResult(problems=["어느 회사인지 가리지 못해 기사를 찾지 않았습니다."])
    company = retrieval.subject.company
    try:
        items = news(company)
    except Exception as exc:  # noqa: BLE001 — 어댑터별 예외가 다르다
        return HintResult(problems=[f"기사 조회 실패: {type(exc).__name__}: {exc}"])
    return build_hints(client, question=question, company=company, items=items)
