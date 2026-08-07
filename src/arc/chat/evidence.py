"""질문과 카드를 **맞댈 수 있는 단위**로 자른다.

왜 이 단위인가
--------------
카드의 본문은 `assembled`(치환 **전** 마크다운)다. 여기서 숫자는 이미
`{{num:key}}` 플레이스홀더이므로, 이 텍스트를 그대로 LLM에 보여줘도
**불변식 1이 깨지지 않는다** — 값은 여전히 레지스트리만 안다. 채팅이
`body_html`(치환 후)이나 `vm`이 아니라 `assembled`를 읽는 이유가 이것이다.

여러 카드를 한 답변에 쓰면 키가 부딪힌다(`revenue_2026a`가 카드마다 있다).
그래서 카드에 `c1`·`c2` 꼬리표를 주고 키를 `c1.revenue_2026a`로 바꾼다.
플레이스홀더 문법이 점을 허용하므로 치환·게이트 코드를 그대로 쓸 수 있다.

형태소 분석기는 쓰지 않는다
--------------------------
새 인프라를 세우지 않는 것이 이 코어의 전제다. 대신 **꼬리 조사만 떼고
부분 문자열로 맞춘다.** "영업이익률은" → "영업이익률" → 본문의 "영업이익률이"
안에 들어 있다. 정확하지는 않지만 리서치 어휘는 명사가 길어서 실제로 붙는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from arc.llm.number_registry import (
    PLACEHOLDER_RE,
    NumberEntry,
    NumberRegistry,
    mask_numbers,
)
from arc.store.cards import Card
from arc.store.notes import PERIOD_LABEL

# 한 발췌의 상한. 표가 길어도 프롬프트를 지배하면 안 된다.
MAX_PASSAGE_CHARS = 1200

# 채팅의 근거가 되지 않는 절.
#   · 수치 출처 — 레지스트리를 그대로 편 색인이다. 키워드가 전부 걸려
#     다른 절을 밀어낸다.
#   · 디스클레이머 — 발간물의 법적 고지이지 이 회사에 대한 사실이 아니다.
_SKIP_SECTIONS = ("수치 출처", "디스클레이머")

_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$", re.MULTILINE)
_WORD_RE = re.compile(r"[0-9A-Za-z가-힣]+")
_SYMBOL_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")

# 붙여 쓰는 조사. 긴 것부터 떼야 "으로"가 "로"로 잘리지 않는다.
_PARTICLES = (
    "에서는",
    "으로는",
    "에게는",
    "이라는",
    # 후속 질문이 즐겨 쓰는 꼴. 실측: "부문별로는?"이 통째로 한 토큰이 돼
    # 아무것도 못 찾았다.
    "까지는",
    "부터는",
    "로는",
    "에는",
    "와는",
    "과는",
    "라는",
    "으로",
    "에서",
    "에게",
    "부터",
    "까지",
    "보다",
    "처럼",
    "마다",
    "이나",
    "이란",
    "이라",
    "한테",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "의",
    "에",
    "와",
    "과",
    "도",
    "만",
    "로",
    "나",
    "랑",
    "야",
)

# 질문의 뼈대일 뿐 내용이 아닌 말. 이게 근거 매칭에 끼면 아무 절이나 걸린다.
_STOPWORDS = frozenset(
    word
    for group in (
        "알려줘 알려 주세요 말해 설명 정리 요약해",
        "뭐야 뭔가요 무엇 어때 어떤 어떻게 어디 언제 누구 얼마 얼마나 왜",
        "인가요 인지 있나요 없나요 하나요 했나요 될까요 인가 대해 대한 관련 관해",
        "그리고 그런데 요즘 지금 이번 최근 정도 부분 경우 이거 그거 저거",
        "가장 제일 어느 무슨 각각 전부 모두 많이 적게 크게 작게",
        # 질문의 틀이지 지표 이름이 아니다. 실측: "원인도 같이 알려줘"의 「원인」이
        # 「못 찾은 것」으로 올라가 답이 사과로 끝났다.
        "원인 이유 배경 영향 결과 관점 측면 같이 함께 다시 그냥",
        "우리 회사 종목 카드 리포트 노트 자료 내용 상황 질문 답변 여부",
        # 후속 질문의 접속사·지시어. 이것들이 내용어로 남으면 「못 찾은 것」에
        # 올라가고, 앞 턴의 주제를 이어받아야 할 자리에서 이어받지 못한다.
        "그럼 그러면 그래서 그리고는 거기 여기 저기 그것 이것 저것 걔 얘",
        # 상대 연도. **검색어로 쓰지 않는다** — 카드는 「작년」이 아니라
        # 「2025A」라고 쓴다. `retrieval._RELATIVE_YEAR`가 연도로 바꿔 준다.
        "작년 전년 지난해 재작년 올해 금년 당해",
    )
    for word in group.split()
)

# 서술어 꼬리. 질문의 동사·형용사는 근거 어휘가 아니다 — "됐어"·"늘었나"가
# 「못 찾은 것」으로 올라가면 답변이 매번 사과로 끝난다. 지표 이름은 이 꼴이
# 아니다(매출액·영업이익률·차입금·수급). 완벽한 판별이 아니라 **소음 제거**다.
_PREDICATE_RE = re.compile(
    r"^[가-힣]{1,3}(?:었|았|였|겠)?(?:어|아|나|니|다|죠|지|까|줘|는지|을까|나요|어요|아요)$"
)


@dataclass(frozen=True)
class Query:
    """질문에서 뽑아낸 것. 무엇으로 카드를 고르고 무엇으로 절을 고르는가."""

    raw: str
    symbols: list[str] = field(default_factory=list)  # 6자리 종목코드
    years: list[int] = field(default_factory=list)
    periods: list[str] = field(default_factory=list)  # ANNUAL | Q1 | HALF | Q3
    tokens: list[str] = field(default_factory=list)  # 조사를 뗀 내용어


@dataclass(frozen=True)
class CardRef:
    """답변이 근거로 삼은 카드 하나. `tag`가 본문의 `[c1]` 표시가 된다."""

    tag: str
    id: str
    symbol: str
    company: str
    year: int
    period: str
    column: str
    gate_passed: bool

    @property
    def label(self) -> str:
        return f"{self.company} ({self.symbol}) · {self.year}년 {PERIOD_LABEL.get(self.period, self.period)}"


@dataclass(frozen=True)
class Passage:
    """카드 본문의 한 덩이. 플레이스홀더가 살아 있다."""

    tag: str
    section: str  # "4. 실적 분석 › 4.2 재무상태"
    text: str
    score: float = 0.0
    matched: tuple[str, ...] = ()  # 이 발췌를 고른 질문 어휘


def _strip_particle(token: str) -> str:
    """꼬리 조사를 하나 뗀다. 떼고 나서 두 글자 미만이면 그냥 둔다."""
    for p in _PARTICLES:
        if len(token) > len(p) + 1 and token.endswith(p):
            return token[: -len(p)]
    return token


def normalize(text: str) -> str:
    """비교용 정규화 — 소문자화하고 공백을 지운다.

    공백을 지우는 이유: 질문은 "영업 이익률", 본문은 "영업이익률"로 쓴다.
    한국어 복합명사의 띄어쓰기는 사람마다 다르고, 그 차이로 근거를 놓치면
    시스템이 아는 것을 모른다고 답한다.
    """
    return re.sub(r"\s+", "", text).lower()


def tokenize(text: str) -> list[str]:
    """질문 → 내용어. 조사를 떼고 불용어와 한 글자를 버린다."""
    out: list[str] = []
    for m in _WORD_RE.finditer(text):
        raw = m.group(0)
        if raw in _STOPWORDS:
            continue
        tok = _strip_particle(raw)
        if len(tok) < 2 or tok in _STOPWORDS or _PREDICATE_RE.match(tok):
            continue
        if tok not in out:
            out.append(tok)
    return out


_PERIOD_WORDS: tuple[tuple[str, str], ...] = (
    ("1분기", "Q1"),
    ("일분기", "Q1"),
    ("반기", "HALF"),
    ("2분기", "HALF"),
    ("3분기", "Q3"),
    ("삼분기", "Q3"),
    ("연간", "ANNUAL"),
    ("사업보고서", "ANNUAL"),
    ("온기", "ANNUAL"),
)


def parse_query(question: str) -> Query:
    """질문에서 종목코드·연도·기간·내용어를 뽑는다."""
    flat = normalize(question)
    periods: list[str] = []
    for word, period in _PERIOD_WORDS:
        if word in flat and period not in periods:
            periods.append(period)
    return Query(
        raw=question,
        symbols=list(dict.fromkeys(_SYMBOL_RE.findall(question))),
        years=list(dict.fromkeys(int(y) for y in _YEAR_RE.findall(question))),
        periods=periods,
        tokens=tokenize(question),
    )


# ── 카드 → 근거 ──────────────────────────────────────────────────────
def company_key(name: str) -> str:
    """법인명에서 비교에 방해되는 것을 뗀다. `현대로템(주)` → `현대로템`."""
    return normalize(re.sub(r"\(주\)|주식회사|㈜", "", name or ""))


def namespace_text(text: str, tag: str) -> str:
    """`{{num:revenue_2026a}}` → `{{num:c1.revenue_2026a}}`."""
    return PLACEHOLDER_RE.sub(lambda m: f"{{{{num:{tag}.{m.group(1)}}}}}", text)


def card_ref(card: Card, tag: str) -> CardRef:
    return CardRef(
        tag=tag,
        id=card.id,
        symbol=card.symbol,
        company=card.company or card.symbol,
        year=card.year,
        period=card.period,
        column=card.column,
        gate_passed=bool(card.vm.get("gate_passed")),
    )


def card_entries(card: Card, tag: str) -> list[NumberEntry]:
    """카드의 레지스트리를 꼬리표 붙은 키로 다시 만든다."""
    out: list[NumberEntry] = []
    for record in card.registry:
        try:
            entry = NumberEntry.model_validate(record)
        except ValueError:
            continue  # 깨진 항목 하나가 카드 전체를 막지 않는다
        out.append(entry.model_copy(update={"key": f"{tag}.{entry.key}"}))
    return out


def card_passages(card: Card, tag: str) -> list[Passage]:
    """카드 본문 → 발췌 목록. `##` 절로 자르고 빈 줄로 다시 나눈다.

    **게이트를 통과하지 못한 카드는 숫자를 가려서 넘긴다.** 통과한 조립본에는
    정의상 미등록 숫자가 없지만(G0가 그것을 검사한다), 차단된 카드에는 남아
    있을 수 있고 LLM은 그걸 베낀다. 탐지와 같은 화이트리스트를 쓰는
    `mask_numbers()`를 그대로 재사용한다.
    """
    body = card.assembled
    if not body.strip():
        return []
    safe = body if card.vm.get("gate_passed") else mask_numbers(body)

    heads = [m for m in _HEADING_RE.finditer(safe) if m.group(1) == "##"]
    out: list[Passage] = []

    # 제목 앞의 머리표 — 시장·산업·기준 보고서·회계기준이 여기 있다.
    preamble = safe[: heads[0].start()] if heads else safe
    out.extend(_blocks_to_passages(preamble, tag, "표제"))

    for i, m in enumerate(heads):
        title = m.group(2).strip()
        if any(k in title for k in _SKIP_SECTIONS):
            continue
        end = heads[i + 1].start() if i + 1 < len(heads) else len(safe)
        out.extend(_blocks_to_passages(safe[m.end() : end], tag, title))
    return out


def _blocks_to_passages(chunk: str, tag: str, section: str) -> list[Passage]:
    """빈 줄로 나눈 덩이. `###` 소제목은 그다음 덩이의 이름에 붙인다."""
    out: list[Passage] = []
    sub = ""
    for block in re.split(r"\n\s*\n", chunk):
        block = block.strip()
        if not block:
            continue
        head = _HEADING_RE.fullmatch(block)
        if head is not None and head.group(1) == "###":
            sub = head.group(2).strip()
            continue
        # 소제목이 본문과 한 덩이로 붙어 있는 경우 (빈 줄 없이 이어짐)
        lines = block.split("\n")
        first = _HEADING_RE.fullmatch(lines[0].strip())
        if first is not None and first.group(1) == "###" and len(lines) > 1:
            sub = first.group(2).strip()
            block = "\n".join(lines[1:]).strip()
        if not block:
            continue
        if len(block) > MAX_PASSAGE_CHARS:
            block = block[:MAX_PASSAGE_CHARS] + "\n…(생략)"
        out.append(
            Passage(
                tag=tag,
                section=f"{section} › {sub}" if sub else section,
                text=namespace_text(block, tag),
            )
        )
    return out


def build_registry(entries: list[NumberEntry]) -> NumberRegistry:
    """꼬리표가 붙었으므로 키가 부딪히지 않는다."""
    registry = NumberRegistry()
    registry.register_all(entries)
    return registry
