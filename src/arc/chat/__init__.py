"""리서치 전용 채팅 코어 — 카드를 근거로 답하고, 항목마다 출처를 낸다.

HTTP 레이어는 여기 없다. `answer_question(question, cards, client=...)` 하나가
입구이고, 나머지는 그 안을 들여다보거나 갈아 끼우기 위한 것이다.

    from arc.chat import answer_question
    from arc.store.cards import CardStore

    answer = answer_question("현대로템 영업이익률 어떻게 됐어?", CardStore(".arc-store").list())
    answer.text          # 치환까지 끝난 한국어 본문
    answer.sources       # 인용한 수치마다 한 줄 (dataset·공시번호·검증 링크)
    answer.unanswered    # 근거가 없어 답하지 못한 것
    answer.grounded      # 근거에 연결됐는가 (확신도가 아니다)

설계 배경은 `docs/research/10-prism-insight-and-scope.md` §4 축 2,
불변식은 [D4](../../../docs/decisions.md#d4)·[D36](../../../docs/decisions.md#d36).
"""

from arc.chat.answer import SYSTEM_PROMPT, Answer, Source, answer_question, build_prompt
from arc.chat.evidence import CardRef, Passage, Query, card_passages, parse_query
from arc.chat.guard import (
    NO_EVIDENCE,
    POLICY_REFUSAL,
    Verdict,
    asks_for_opinion,
    check_answer,
)
from arc.chat.retrieval import Retrieval, retrieve

__all__ = [
    "NO_EVIDENCE",
    "POLICY_REFUSAL",
    "SYSTEM_PROMPT",
    "Answer",
    "CardRef",
    "Passage",
    "Query",
    "Retrieval",
    "Source",
    "Verdict",
    "answer_question",
    "asks_for_opinion",
    "build_prompt",
    "card_passages",
    "check_answer",
    "parse_query",
    "retrieve",
]
