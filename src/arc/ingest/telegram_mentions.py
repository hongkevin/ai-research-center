"""메시지 텍스트 → **언급된 종목**, 그리고 채널 ④의 산출물인 **언급 급증**.

오탐이 문제다
-------------
「대상」은 식품회사(001680)이고 동시에 「~을 대상으로」다. 「테스」·「나노」·
「레이」·「우진」·「하림」·「만도」·「선진」은 전부 상장사이면서 흔한 말이다.
영문 이름은 더 나쁘다 — `LG`는 `LG전자`·`LG화학` 안에 들어 있고, `SK`는
`SKT`·`SK온` 안에 있다.

**세 겹으로 막는다.**

1. **가장 긴 이름부터 맞춘다.** `한국전자금융`이 `한국전자`로 쪼개지지 않고,
   `대상홀딩스`가 `대상`으로 떨어지지 않는다.
2. **경계를 본다.** 이름 뒤에 한글이 붙으면 **조사일 때만** 통과시킨다.
   `대상자`·`나노소재`·`레이더`는 여기서 죽는다. 영문 이름 뒤에 한글이나
   영문이 붙으면 무조건 버린다 — `LG전자`는 `LG`가 아니다.
3. **상용어와 겹치는 이름은 증거를 요구한다.** 종목코드·`$태그`·괄호 시세 중
   하나가 있어야 센다. 목록은 **측정으로 만들었고** 아래에 그대로 있다.

**길이로 자르려던 것을 실측이 뒤집었다**
----------------------------------------
처음에는 「한글 2자 이하·영문 3자 이하는 요주의」로 짰다. 그럴듯했고 틀렸다.

| 규칙 | A 매치 | B 매치 | C 재현율 | C 덤 |
|---|---|---|---|---|
| ① 그냥 부분 문자열 | 300 | 77 | 100.0% | 156 |
| ② + 경계·최장 일치 | 171 | 62 | 100.0% (0 놓침) | 52 |
| ③ + **길이**로 요주의(NEAR) | 156 | 61 | **94.3%** (404 놓침) | 35 |
| ④ + **측정한 상용어 목록**(QUOTE) | **152** | **60** | **99.8%** (14 놓침) | 47 |

③이 죽인 404건의 정체를 열어 보니 **기아(53)·F&F(30)·KT(25)·ISC(24)·
풍산(23)·농심(23)·SK(15)·LS(15)** 같은 멀쩡한 대형주였다. 반대로 짧은 이름
87종 중 실제로 오탐을 낸 것은 **`대상`·`진영`·`나무가` 셋뿐**이었다. 길이는
오탐과 상관이 없다 — **그 이름이 일반어인가**가 가른다. 그래서 길이 규칙을
버리고 목록을 쓴다(`COMMON_WORD_NAMES`).

A의 매치를 하나씩 열어 보면(19~20종이라 손으로 된다):

* ② 171건 중 회사 얘기가 아닌 것 **20건** — `대상`15 · `E1`3 · `DB`1 · `한화`1
* ④ 152건 중 **1건** — 「한화 배성조」의 `한화`(한화투자증권을 가리키는 자리)

④가 치른 값은 재현율 14건이고, 그 14건은 전부 **`대상`(10)과 `E1`(4) 자신에
관한 리포트**다. 상용어 목록에 든 회사는 코드나 시세가 안 붙으면 못 잡는다 —
이 목록에 이름을 넣는 것이 그 종목을 어둡게 만드는 일이라는 뜻이다. 함부로
넣으면 안 된다.

측정에 쓴 것
------------
* **이름 목록**: `corpus/**/*.csv`에 실린 실제 상장사 1,130종(이름+종목코드).
  DART corpCode 전량(약 2,800종)이 아니므로 **오탐은 실제로 더 난다.**
* **A · 일반 산문** `docs/*.md` + `README.md` 144,397자 — 회사 얘기가 아닌 글.
* **B · 리포트 본문** `corpus/market/text/*.txt` 66,063자.
* **C · 리포트 제목** 7,077건. 종목코드가 라벨로 붙어 있어 재현율을 잴 수
  있다. 제목의 `(047040)` 표기는 지우고 **이름만으로** 맞히게 했다.

숫자는 `tests/test_telegram_parse.py::TestMeasuredExtraction`이 고정한다.

**목록은 코퍼스만큼만 안다.** 상장사 전체를 넣으면 `유니온`·`대한`·`신라`
같은 것이 더 나온다. 늘리는 방법은 위 절차 그대로다 — 일반 산문에 색인을
돌려 잡히는 이름을 눈으로 보고 넣는다. 호출자는 `extra_risky`로 덧붙인다.

**센티는 개별 요약이 아니라 급증이다**
--------------------------------------
④ 종토방 8개에서 하루 수천 건이 온다. 한 건씩 요약하면 쓰레기가 된다.
쓸모 있는 산출은 **어느 종목이 갑자기 많이 불리는가**다.

* 한 메시지 안에서 같은 종목이 열 번 나와도 **1로 센다.** 도배 방어.
* **채널 하나만 떠들면 급증이 아니다.** 기본값으로 서로 다른 채널 2곳을
  요구한다 — 리딩방 하나가 종목을 미는 것과 시장이 웅성거리는 것은 다르다.
* 기준선은 **일평균**이고, 배수는 `오늘 / (일평균 + 0.5)`다. 0으로 나누는
  것을 막으려는 게 아니라, **오늘 처음 나온 종목이 배수 무한대로 순위를
  독점하는 것**을 막으려는 것이다. 처음 나온 종목은 `min_today`가 거른다.

**여기서 나온 숫자는 본문에 못 들어간다.** 언급 수는 우리가 센 것이지만 그
바탕은 텔레그램이다 — [D45](../../../docs/decisions.md#d45)의 미검증 레인이고,
`Surge.lane`이 그걸 타입으로 들고 있다.
"""

from __future__ import annotations

import collections
import datetime as dt
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import IntEnum

from arc.data.kr.news_filter import plain_name
from arc.ingest.telegram_parse import Lane, Message


class Evidence(IntEnum):
    """언급을 뒷받침하는 증거의 세기. **순서가 의미다** — 비교에 쓴다."""

    NAME = 0  # 이름만 나왔다
    NEAR = 1  # 앞뒤 12자 안에 주식 이야기가 있다
    QUOTE = 2  # 이름 바로 뒤 괄호에 시세·등락률
    TAG = 3  # `$대상` · `#대상`
    CODE = 4  # 같은 메시지에 그 종목의 6자리 코드가 있다


@dataclass(frozen=True)
class Mention:
    """텍스트에서 찾은 종목 언급 하나."""

    symbol: str
    name: str
    surface: str  # 실제로 맞은 문자열
    start: int
    evidence: Evidence


# ── 이름 색인 ────────────────────────────────────────────────────────

# 이름 뒤에 붙어도 되는 것 — 조사와 서술격. 여기 없는 한글이 붙으면 다른
# 낱말이다(`대상자`·`나노소재`). 긴 것부터 본다.
_JOSA = (
    "이라는",
    "이라고",
    "으로는",
    "에서는",
    "에게는",
    "이라도",
    "입니다",
    "이라면",
    "한테는",
    "께서는",
    "이라",
    "라는",
    "라고",
    "으로",
    "에서",
    "에게",
    "한테",
    "께서",
    "보다",
    "처럼",
    "부터",
    "까지",
    "마저",
    "조차",
    "밖에",
    "이나",
    "라도",
    "든지",
    "이며",
    "이다",
    "이란",
    "은요",
    "이야",
    "랑은",
    "와는",
    "과는",
    "도는",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "의",
    "에",
    "도",
    "만",
    "과",
    "와",
    "랑",
    "로",
    "요",
    "야",
    "란",
    "님",
)

# ── 상용어와 겹치는 상장사 이름 ──────────────────────────────────────
#
# **측정으로 만든 목록이다.** 길이 규칙을 대신한다(모듈 docstring의 표).
# 여기 있는 이름은 종목코드·태그·괄호 시세 중 하나가 붙어야 언급으로 센다.
COMMON_WORD_NAMES = frozenset(
    {
        # 실측 — 코퍼스에서 오탐을 낸 것
        "대상",  # 「~을 대상으로」. A에서 15건 전부 회사가 아니었다
        "진영",  # 「스타링크 진영」 — 편·캠프
        "나무가",  # 「나무가 아닌 숲을」 — 「나무」 + 조사 「가」
        "DB",  # 데이터베이스
        "E1",  # 문항 번호 (`E1.` `E2.`)
        # 코퍼스에는 안 나왔지만 뜻이 정면으로 겹치는 것
        "유니온",  # 노조·연합 (사용자가 지목한 이름)
        "대한",  # 「~에 대한」
    }
)


def _is_hangul(ch: str) -> bool:
    return "가" <= ch <= "힣"


def _is_wordish(ch: str) -> bool:
    """이름에 이어 붙으면 다른 낱말이 되는 글자."""
    return _is_hangul(ch) or ch.isdigit() or (ch.isascii() and ch.isalpha())


@dataclass(frozen=True)
class NameIndex:
    """종목명 → 종목코드. **새로 만들지 않고 DART corpCode에서 만든다.**"""

    by_name: Mapping[str, str]  # 표기 이름 → 6자리 종목코드
    by_code: Mapping[str, str]  # 6자리 종목코드 → 대표 이름
    risky: frozenset[str]  # 이름만으로는 안 세는 것
    max_len: int
    # 이름의 첫 글자 모음. 8,638건짜리 채널을 훑을 때 위치마다 최장 길이만큼
    # 사전을 뒤지면 느리다 — 첫 글자로 대부분의 위치를 먼저 걸러낸다.
    first_chars: frozenset[str] = frozenset()

    @classmethod
    def from_names(cls, names: Mapping[str, str], *, extra_risky: Iterable[str] = ()) -> NameIndex:
        """{이름: 종목코드} → 색인. 이름은 `plain_name()`으로 법인 표기를 뗀다."""
        by_name: dict[str, str] = {}
        by_code: dict[str, str] = {}
        for raw, code in names.items():
            name = plain_name(raw)
            if len(name) < 2 or not code:
                continue
            by_name.setdefault(name, code)
            by_code.setdefault(code, name)
            packed = name.replace(" ", "")
            if packed != name:
                by_name.setdefault(packed, code)
        risky = {n for n in by_name if n in COMMON_WORD_NAMES}
        risky.update(plain_name(x) for x in extra_risky)
        return cls(
            by_name=by_name,
            by_code=by_code,
            risky=frozenset(risky),
            max_len=max((len(n) for n in by_name), default=0),
            first_chars=frozenset(n[0] for n in by_name),
        )

    @classmethod
    def from_corp_codes(cls, corp_codes: Mapping[str, Mapping[str, str]]) -> NameIndex:
        """`DartProvider.load_corp_codes()` 결과를 그대로 받는다.

        `{종목코드: {corp_code, corp_name, stock_code, ...}}`. **매핑을 새로
        만들지 않는다** — 상장사 목록은 이미 그쪽에 있고 캐시도 그쪽에 있다.
        """
        return cls.from_names(
            {e.get("corp_name", ""): code for code, e in corp_codes.items() if e.get("corp_name")}
        )


# ── 증거 ─────────────────────────────────────────────────────────────

# 주변에 이게 있으면 주식 이야기다.
_STOCK_WORDS = re.compile(
    r"(주가|실적|공시|매수|매도|목표가|목표주가|수급|차트|상한가|하한가|신고가|신저가|"
    r"영업이익|매출|어닝|컨센|시총|시가총액|거래량|급등|급락|반등|조정|물량|"
    r"수주|계약|증자|감자|배당|자사주|분할|합병|테마|대장|밸류|PER|PBR|ROE|EPS|"
    r"상장|따상|장중|종가|시초가|호가|체결|보유|비중|편입|편출|리포트|커버)"
)
# 이름 바로 뒤 괄호에 든 시세·등락률 — 「안국약품(+2.31%)」·「신성이엔지(011930)」
_QUOTE_AFTER = re.compile(r"^\s*\(\s*(?:[+\-−]?\d|\d{6})")
_TAGGED = re.compile(r"[$#]$")
# 6자리 종목코드. 앞뒤에 숫자·콤마·소수점이 붙으면 가격이나 날짜다.
_CODE_RE = re.compile(r"(?<![\d,.])\d{6}(?![\d,.])")

# 주변을 이만큼 본다. 한 어절 반쯤 — 넓히면 종토방에서는 전부 통과한다.
NEAR_CHARS = 12


def codes_in(text: str, index: NameIndex) -> set[str]:
    """텍스트에 든 6자리 종목코드 중 **실제 상장 코드인 것만**."""
    return {m.group(0) for m in _CODE_RE.finditer(text) if m.group(0) in index.by_code}


def _tail_ok(text: str, end: int) -> bool:
    """이름 뒤 경계. 한글이면 **조사일 때만** 통과."""
    if end >= len(text):
        return True
    nxt = text[end]
    if not _is_wordish(nxt):
        return True
    if not _is_hangul(nxt):
        return False  # 숫자·영문이 이어 붙었다 (`SKC`)
    for j in _JOSA:
        if text.startswith(j, end):
            after = end + len(j)
            if after >= len(text) or not _is_wordish(text[after]):
                return True
    return False


def _head_ok(text: str, start: int) -> bool:
    return start == 0 or not _is_wordish(text[start - 1])


def _evidence(text: str, start: int, end: int, symbol: str, codes: set[str]) -> Evidence:
    if symbol in codes:
        return Evidence.CODE
    if start > 0 and _TAGGED.search(text[start - 1 : start]):
        return Evidence.TAG
    if _QUOTE_AFTER.match(text[end : end + 10]):
        return Evidence.QUOTE
    if _STOCK_WORDS.search(text[max(0, start - NEAR_CHARS) : end + NEAR_CHARS]):
        return Evidence.NEAR
    return Evidence.NAME


def extract_mentions(
    text: str,
    index: NameIndex,
    *,
    min_evidence_for_risky: Evidence = Evidence.QUOTE,
    check_boundary: bool = True,
) -> list[Mention]:
    """텍스트 → 언급된 종목. **겹치지 않게, 가장 긴 이름부터.**

    기본값이 `QUOTE`인 이유: `NEAR`로 두면 상용어 이름이 그대로 새어 나온다.
    실측(코퍼스 A) — 「대상」이 `NEAR`에서 4건 살아남았고 `QUOTE`에서 0건이
    됐다. 종토방 메시지는 어차피 전부 주식 이야기라 「주변에 주식어가 있다」가
    증거 구실을 못 한다.

    `min_evidence_for_risky=Evidence.NAME`·`check_boundary=False`는 방어를 끄는
    설정이다 — **측정용**이지 운영값이 아니다.
    """
    if not text or index.max_len == 0:
        return []
    codes = codes_in(text, index)
    found: list[Mention] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] not in index.first_chars or (check_boundary and not _head_ok(text, i)):
            i += 1
            continue
        hit: Mention | None = None
        for length in range(min(index.max_len, n - i), 1, -1):
            piece = text[i : i + length]
            symbol = index.by_name.get(piece)
            if symbol is None:
                continue
            if check_boundary and not _tail_ok(text, i + length):
                continue
            ev = _evidence(text, i, i + length, symbol, codes)
            if piece in index.risky and ev < min_evidence_for_risky:
                continue
            hit = Mention(
                symbol=symbol,
                name=index.by_code.get(symbol, piece),
                surface=piece,
                start=i,
                evidence=ev,
            )
            break
        if hit is None:
            i += 1
        else:
            found.append(hit)
            i += len(hit.surface)

    # 코드만 나오고 이름이 없는 경우 — 「011930 좋네요」
    named = {m.symbol for m in found}
    for code in sorted(codes - named):
        pos = text.find(code)
        found.append(
            Mention(
                symbol=code,
                name=index.by_code[code],
                surface=code,
                start=pos,
                evidence=Evidence.CODE,
            )
        )
    found.sort(key=lambda m: m.start)
    return found


def message_symbols(
    msg: Message,
    index: NameIndex,
    *,
    min_evidence_for_risky: Evidence = Evidence.QUOTE,
) -> set[str]:
    """메시지 한 건에 나온 **서로 다른** 종목. 도배해도 한 번이다."""
    return {
        m.symbol
        for m in extract_mentions(msg.text, index, min_evidence_for_risky=min_evidence_for_risky)
    }


# ── ④ 센티 집계 ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Surge:
    """종목별 언급 급증 한 줄. **미검증 레인**이다."""

    symbol: str
    name: str
    today: int  # 오늘 이 종목을 말한 메시지 수
    baseline_per_day: float  # 기준 기간 일평균
    ratio: float
    channels: tuple[str, ...]  # 오늘 말한 채널들
    samples: tuple[tuple[str, int, str | None], ...]  # (채널, 메시지 ID, 딥링크)

    @property
    def lane(self) -> Lane:
        return Lane.UNVERIFIED


# 기준선에 더하는 값. 오늘 처음 나온 종목이 배수 무한대가 되는 것을 막는다.
BASELINE_PRIOR = 0.5


def mention_surges(
    messages: Sequence[Message],
    index: NameIndex,
    *,
    on: dt.date,
    baseline_days: int = 14,
    min_today: int = 3,
    min_channels: int = 2,
    limit: int = 20,
    min_evidence_for_risky: Evidence = Evidence.NEAR,
) -> list[Surge]:
    """④ 종토방 메시지 → **오늘 갑자기 많이 불린 종목**.

    개별 메시지를 요약하지 않는다. 하루 수천 건에서 사람이 쓸 수 있는 산출은
    「무엇이 달라졌는가」뿐이다.
    """
    # 기준 기간은 [on - baseline_days, on - 1] — 오늘은 빼고 정확히 그만큼.
    start = on - dt.timedelta(days=baseline_days)
    today_msgs: dict[str, list[Message]] = collections.defaultdict(list)
    base: collections.Counter[str] = collections.Counter()

    for msg in messages:
        day = msg.day
        if day > on or day < start:
            continue
        syms = message_symbols(msg, index, min_evidence_for_risky=min_evidence_for_risky)
        if day == on:
            for s in syms:
                today_msgs[s].append(msg)
        else:
            base.update(syms)

    out: list[Surge] = []
    for symbol, msgs in today_msgs.items():
        channels = tuple(sorted({m.chat_name for m in msgs}))
        if len(msgs) < min_today or len(channels) < min_channels:
            continue
        per_day = base[symbol] / baseline_days
        out.append(
            Surge(
                symbol=symbol,
                name=index.by_code.get(symbol, symbol),
                today=len(msgs),
                baseline_per_day=per_day,
                ratio=len(msgs) / (per_day + BASELINE_PRIOR),
                channels=channels,
                samples=tuple(
                    (m.chat_name, m.message_id, m.permalink)
                    for m in sorted(msgs, key=lambda m: m.at)[:5]
                ),
            )
        )
    out.sort(key=lambda s: (-s.ratio, -s.today, s.symbol))
    return out[:limit]
