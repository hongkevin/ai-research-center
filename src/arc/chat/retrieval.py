"""질문 → 근거. 임베딩 없이 종목·기간·어휘로 고른다.

왜 임베딩이 아닌가
------------------
RA의 리퀘스트는 열린 질문이 아니라 **정해진 성격**을 갖는다
(`research/10-prism-insight-and-scope.md` §4 축 2): 종목이 박혀 있고, 기간이
박혀 있고, 묻는 지표의 이름이 리포트에 그대로 쓰여 있다. "영업이익률"은
카드 본문에도 레지스트리 라벨에도 "영업이익률"로 적혀 있다. 이 구조에서
벡터 검색이 이기는 자리는 좁고, 새 인프라의 값은 비싸다.

**못 찾으면 못 찾았다고 말한다**
--------------------------------
이 모듈의 가장 중요한 산출은 `unmatched`다. 질문의 내용어 중 어느 카드에서도
걸리지 않은 것을 남긴다. 여기가 비지 않으면 답변은 그 부분을 "확인할 수 있는
근거가 없습니다"로 처리해야 한다. 검색이 조용히 비슷한 것을 집어 오면
「모르면 모른다고 한다」가 성립하지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from arc.chat.evidence import (
    CardRef,
    Passage,
    Query,
    build_registry,
    card_entries,
    card_passages,
    card_ref,
    company_key,
    normalize,
    parse_query,
)
from arc.llm.number_registry import NumberEntry, NumberRegistry
from arc.store.cards import Card

# 카드 선택 가중치. 종목이 맞으면 다른 무엇보다 세다 — 다른 회사의 절이
# 어휘가 겹친다고 섞이면 답변이 조용히 틀린다.
_W_SYMBOL = 100.0
_W_COMPANY = 60.0
_W_YEAR = 8.0
_W_PERIOD = 8.0
_W_TOKEN = 1.0


@dataclass
class Retrieval:
    """이 질문에 답하기 위해 모은 것 전부."""

    question: str
    query: Query
    cards: list[CardRef] = field(default_factory=list)
    passages: list[Passage] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)  # 프롬프트에 낼 수치 키 (꼬리표 포함)
    registry: NumberRegistry = field(default_factory=NumberRegistry)
    matched: list[str] = field(default_factory=list)  # 근거에서 걸린 질문 어휘
    unmatched: list[str] = field(default_factory=list)  # 어디에도 없던 질문 어휘
    reason: str = ""  # 비었을 때 왜 비었는가

    @property
    def empty(self) -> bool:
        return not self.passages and not self.keys

    def card_of(self, tag: str) -> CardRef | None:
        return next((c for c in self.cards if c.tag == tag), None)

    def tags(self) -> list[str]:
        return [c.tag for c in self.cards]


def _usable(cards: Sequence[Card]) -> list[Card]:
    """생성 중이거나 중단된 카드는 근거가 아니다 — 본문이 반쪽이다."""
    return [c for c in cards if not c.running and not c.error and c.assembled.strip()]


def _card_score(card: Card, query: Query) -> tuple[float, bool]:
    """(점수, 종목이 지목됐는가)."""
    score = 0.0
    named = False
    if card.symbol in query.symbols:
        score += _W_SYMBOL
        named = True
    key = company_key(card.company)
    # 질문이 회사를 부르는 방식은 둘이다 — 이름을 통째로 쓰거나 ("현대로템의
    # 매출"), 줄여 쓰거나("로템 실적"). 양방향으로 본다.
    if key and (key in normalize(query.raw) or any(t in key for t in query.tokens)):
        score += _W_COMPANY
        named = True
    if card.year in query.years:
        score += _W_YEAR
    if card.period in query.periods:
        score += _W_PERIOD
    if not named:
        # 종목이 안 불렸을 때만 본문 어휘로 고른다. 불렸으면 그게 답이다.
        body = normalize(card.assembled)
        score += _W_TOKEN * sum(1 for t in query.tokens if normalize(t) in body)
    return score, named


def _score_passage(passage: Passage, tokens: list[str]) -> Passage:
    flat = normalize(passage.text)
    head = normalize(passage.section)
    hits: list[str] = []
    score = 0.0
    for token in tokens:
        norm = normalize(token)
        if norm in head:
            score += 1.5  # 절 제목에 있으면 그 절이 바로 그 얘기다
            hits.append(token)
        elif norm in flat:
            score += 1.0
            hits.append(token)
    return Passage(
        tag=passage.tag,
        section=passage.section,
        text=passage.text,
        score=score,
        matched=tuple(hits),
    )


def _label_hits(entries: list[NumberEntry], tokens: list[str]) -> tuple[list[str], list[str]]:
    """라벨이 질문 어휘를 담은 수치 키. `(키 목록, 걸린 어휘)`.

    본문에 없어도 레지스트리에는 있는 지표가 많다 — 표에만 나오는 계정이
    그렇다. 라벨을 따로 훑지 않으면 "현금흐름 얼마야"에 답하지 못한다.
    """
    keys: list[str] = []
    hits: list[str] = []
    for entry in entries:
        if entry.internal:
            continue  # 감사용 값은 독자용이 아니다 (D17)
        label = normalize(entry.label or entry.key)
        for token in tokens:
            if normalize(token) in label:
                keys.append(entry.key)
                if token not in hits:
                    hits.append(token)
                break
    return keys, hits


def retrieve(
    question: str,
    cards: Sequence[Card],
    *,
    max_cards: int = 2,
    max_passages: int = 8,
    max_keys: int = 60,
) -> Retrieval:
    """질문에 걸리는 카드·발췌·수치를 모은다. 없으면 비운 채 이유를 남긴다."""
    query = parse_query(question)
    pool = _usable(cards)
    out = Retrieval(question=question, query=query)

    if not pool:
        out.reason = "근거로 쓸 수 있는 카드가 없습니다."
        out.unmatched = list(query.tokens)
        return out

    scored = [(*_card_score(c, query), c) for c in pool]
    named = [(s, c) for s, is_named, c in scored if is_named]
    any_named = bool(named)
    if query.symbols or any_named:
        # **종목이 지목되면 그 종목의 카드만 쓴다.** 못 찾으면 다른 회사로
        # 대신 답하지 않고 없다고 말한다.
        chosen = [c for _, c in sorted(named, key=lambda p: -p[0])]
        if not chosen:
            out.reason = f"질문에 나온 종목({', '.join(query.symbols)})의 카드가 없습니다."
            out.unmatched = list(query.tokens)
            return out
    else:
        chosen = [c for s, _, c in sorted(scored, key=lambda p: -p[0]) if s > 0]
        if not chosen:
            out.reason = "질문의 어휘가 어느 카드에도 나오지 않습니다."
            out.unmatched = list(query.tokens)
            return out
    chosen = chosen[:max_cards]

    entries: list[NumberEntry] = []
    passages: list[Passage] = []
    for i, card in enumerate(chosen, 1):
        tag = f"c{i}"
        out.cards.append(card_ref(card, tag))
        entries.extend(card_entries(card, tag))
        passages.extend(card_passages(card, tag))
    out.registry = build_registry(entries)

    # **회사 이름은 발췌를 고르는 데 쓰지 않는다.** 그 카드의 모든 절이 그
    # 회사 얘기라 아무 절이나 걸리고, 정작 무엇을 물었는지가 묻힌다.
    content = [t for t in query.tokens if not _is_name(t, out.cards)]

    ranked = sorted((_score_passage(p, content) for p in passages), key=lambda p: -p.score)
    hit = [p for p in ranked if p.score > 0][:max_passages]
    key_hits, label_tokens = _label_hits(entries, content)
    matched = {t for p in hit for t in p.matched} | set(label_tokens)

    unmatched = [t for t in content if t not in matched]
    if not hit and not key_hits and content:
        # 내용어가 있는데 하나도 안 걸렸다. **비워서 돌려준다** — 비슷한
        # 절을 대신 집어 오면 시스템이 모르는 것을 아는 척하게 된다.
        return _nothing(out, content, "카드에서 «{}»에 해당하는 근거를 찾지 못했습니다.")
    if unmatched and not any_named:
        # 종목을 지목하지 않은 질문인데 남는 말이 있다. **그 말이 우리가 갖고
        # 있지 않은 회사 이름일 수 있다** — 실측: "삼성전자 매출 얼마야"가
        # 「매출」 하나로 현대로템 카드를 집어 왔다. 다른 회사의 숫자로 답하는
        # 것이 이 시스템이 할 수 있는 가장 나쁜 오답이다.
        return _nothing(
            out,
            unmatched,
            "«{}»가 무엇을 가리키는지 카드에서 찾지 못했습니다. 종목명을 함께 적어 주십시오.",
        )
    if not hit:
        # 회사·기간만 물었다면 요약이 답이다 ("현대로템 어때").
        hit = [p for p in ranked if "요약" in p.section][:2]

    out.passages = hit
    body_keys = [k for p in hit for k in NumberRegistry.extract_keys(p.text)]
    out.keys = list(dict.fromkeys(body_keys + key_hits))[:max_keys]
    out.matched = sorted(matched)
    out.unmatched = unmatched
    return out


def _nothing(out: Retrieval, tokens: list[str], template: str) -> Retrieval:
    """근거 없음으로 되돌린다. **모은 것을 비우고 이유만 남긴다.**"""
    out.reason = template.format(", ".join(tokens[:4]))
    out.unmatched = tokens
    out.cards = []
    out.passages = []
    out.keys = []
    out.registry = NumberRegistry()
    return out


def _is_name(token: str, cards: list[CardRef]) -> bool:
    """회사·종목코드를 가리키는 말은 질문의 내용어가 아니다."""
    norm = normalize(token)
    return any(norm in company_key(c.company) or norm == c.symbol for c in cards)
