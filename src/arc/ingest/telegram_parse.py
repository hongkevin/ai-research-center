"""텔레그램 내보내기(JSON) → **메시지 레코드**, 채널 분류, 봇 채널 정형 파싱.

수집은 여기 없다
----------------
텔레그램 데스크톱의 「채널별 내보내기」가 만든 `result.json`을 받는다. 최상위가
Chat 객체 하나이고 `messages`에 dict가 줄지어 있다. 이 모듈은 **그 dict 리스트를
받는 데서 시작한다** — 파일을 열거나 zip을 푸는 일은 호출자 몫이다.

산출은 마크다운 블롭이 아니라 **레코드 리스트**다. 시각·채널·메시지 ID(딥링크)·
답글 스레드가 살아 있어야 「이 문장 어디서 나왔죠」에 답할 수 있다.
([D36](../../../docs/decisions.md#d36) — 출처는 항목마다 다르고 파일에도 남는다.)

**텔레그램은 미검증 레인이다**
------------------------------
[D45](../../../docs/decisions.md#d45)의 기사 레인과 같은 취급이다. 여기서 온
숫자는 본문 수치의 출처가 **절대** 될 수 없다. 그래서 레인을 주석이 아니라
**타입**으로 박았다 — `Message.lane`은 읽기 전용 프로퍼티이고 항상
`Lane.UNVERIFIED`다. 설정할 수 있는 필드로 두면 언젠가 누가 설정한다.

`text`를 쓰지 않고 `text_entities`만 쓴다
----------------------------------------
`text`는 서식이 붙으면 문자열이 아니라 배열이 된다 — 호출자마다 분기가 생기고
한쪽이 반드시 빠진다. `text_entities`는 **항상 배열**이고 서식 없는 조각도
`{"type": "plain"}`으로 감싸 나온다. 게다가 `text_link`의 `href`가 거기에만
있다 — 봇 채널(주요공시 알리미)의 원문 링크가 그 자리다.

**`date`는 믿지 않는다.** 실측: `date`가 `2024-10-11T08:14:00`인데
`date_unixtime`은 `1728623640`(= 05:14Z = KST 14:14)이었다. `date`는 **내보낸
컴퓨터의 로컬 시각**이라 타임존이 안 붙어 나오고, 채널마다 다른 기계에서
내보내면 서로 어긋난다. 절대 시각은 `date_unixtime`뿐이다. 우리는 그걸
**KST로** 들고 있는다 — 「오늘 언급 급증」의 「오늘」이 한국 장 기준이라서다.

**조회수는 없다.** 내보내기에 `views`가 안 들어온다. 중요도를 조회수로 매기는
설계는 여기서 못 쓴다. 대신 `reactions`가 81% 들어오므로 그게 대체 신호다.

채널 네 부류 + 대화방
---------------------
처리 방식이 서로 다르다. 요약기에 다 밀어 넣으면 봇 알림 8천 건이 리서치
글 하나를 덮는다.

* ① `BOT_FEED` — 요약 대상이 아니라 **파싱** 대상. 정형 행으로 뽑는다.
* ② `BROKER` — 증권사 공식 채널. 출처는 확실하고 **저작권이 문제**다.
* ③ `RESEARCH` — 비공식 리서치. ②와 같은 저작권 취급.
* ④ `CHATTER` — 종토방·센티. 개별 요약이 아니라 **언급 빈도와 급증**이 산출물.
* ⑤ `INTERNAL` — 내부 대화방. 밖으로 안 나간다.

저작권은 코드의 경계로 만든다
-----------------------------
②③의 원문은 **저장은 하되 재배포용 출력에 그대로 실리면 안 된다.** 「조심하자」로는
안 지켜진다. 그래서 셋을 뒀다:

1. `Message.redistribution` — 채널 종류에서 파생되는 등급. 필드가 아니라
   프로퍼티라 레코드를 만들면서 우회할 수 없다.
2. `publish_view()` — 재배포용 표현을 만드는 **유일한 문**. 인용이 금지된
   채널이면 원문 자리를 비우고 「요약 필요」 플래그를 세운다.
3. `find_verbatim_leaks()` — 내보낼 payload를 원문과 대조해 긴 그대로 베낌을
   찾는다. G0가 숫자에 하는 일을 원문에 한다.

**모르는 채널은 인용 금지로 떨어진다.** 저작권에서 기본값은 안전한 쪽이어야
한다.
"""

from __future__ import annotations

import collections
import datetime as dt
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

# 한국 시장 도구다. 「오늘」은 KST 하루다.
KST = dt.timezone(dt.timedelta(hours=9), "KST")

# 채널 판정에 쓸 표본 상한. 이보다 많이 봐도 통계가 안 바뀐다.
CLASSIFY_SAMPLE = 200


class Lane(StrEnum):
    """근거의 종류. [D45](../../../docs/decisions.md#d45)."""

    VERIFIED = "verified"  # 공시·재무제표. 검산한 값
    UNVERIFIED = "unverified"  # 기사·업로드 문서·**텔레그램**. 되짚을 수는 있다


class ChannelKind(StrEnum):
    """채널 다섯 부류. 값은 화면·로그에 그대로 쓴다."""

    BOT_FEED = "bot_feed"  # ① 봇·정형 알림
    BROKER = "broker"  # ② 증권사 애널리스트 공식 채널
    RESEARCH = "research"  # ③ 비공식 리서치
    CHATTER = "chatter"  # ④ 종토방·센티
    INTERNAL = "internal"  # ⑤ 내부 대화방
    UNKNOWN = "unknown"


class Redistribution(StrEnum):
    """재배포 등급. **채널 종류에서 파생된다** — 따로 설정하지 않는다."""

    OPEN = "open"  # 사실 알림(공시·신고가). 정형 행으로 다시 만들어 내보낸다
    SUMMARY_ONLY = "summary_only"  # 원문 인용 금지. 요약·링크만
    INTERNAL_ONLY = "internal_only"  # 밖으로 안 나간다


# 채널 종류 → 재배포 등급. **UNKNOWN은 안전한 쪽으로 떨어진다.**
_REDIST: dict[ChannelKind, Redistribution] = {
    ChannelKind.BOT_FEED: Redistribution.OPEN,
    ChannelKind.BROKER: Redistribution.SUMMARY_ONLY,
    ChannelKind.RESEARCH: Redistribution.SUMMARY_ONLY,
    ChannelKind.CHATTER: Redistribution.SUMMARY_ONLY,
    ChannelKind.INTERNAL: Redistribution.INTERNAL_ONLY,
    ChannelKind.UNKNOWN: Redistribution.SUMMARY_ONLY,
}


# ── 레코드 ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Message:
    """메시지 한 건. **미검증 레인**이고 그건 바뀌지 않는다."""

    chat_id: int
    chat_name: str
    message_id: int
    at: dt.datetime  # KST aware
    text: str
    kind: ChannelKind = ChannelKind.UNKNOWN
    links: tuple[str, ...] = ()
    link_labels: tuple[str, ...] = ()  # `text_link`의 표시문구 — 본문이 아니다
    author: str | None = None  # `from` (99%). 서명 채널·대화방에서만 의미 있다
    sender_id: str | None = None  # `from_id` — "channel123" · "user123"
    reply_to: int | None = None
    forwarded_from: str | None = None
    reactions: int = 0
    edited: bool = False
    has_photo: bool = False
    permalink: str | None = None  # 공개 채널은 만들 수 없다 — None이 정상이다
    time_is_local_guess: bool = False  # `date_unixtime`이 없어 `date`로 때운 것

    @property
    def lane(self) -> Lane:
        """항상 미검증. **필드가 아니다** — 설정할 수 있으면 언젠가 설정된다."""
        return Lane.UNVERIFIED

    @property
    def redistribution(self) -> Redistribution:
        return _REDIST[self.kind]

    @property
    def quotable(self) -> bool:
        """원문을 재배포용 출력에 그대로 실어도 되는가."""
        return self.redistribution is Redistribution.OPEN

    @property
    def is_human(self) -> bool:
        """사람이 보낸 것인가. 채널 게시물은 발신자가 채널 자신이다."""
        return bool(self.sender_id and self.sender_id.startswith("user"))

    @property
    def day(self) -> dt.date:
        return self.at.date()


@dataclass(frozen=True)
class Channel:
    """채널 하나 = 판정 + 레코드. 판정을 레코드와 떼어 놓지 않는다."""

    chat_id: int
    name: str
    chat_type: str
    kind: ChannelKind
    messages: tuple[Message, ...]
    skipped: int = 0  # 서비스 메시지 등 `type != "message"`

    @property
    def redistribution(self) -> Redistribution:
        return _REDIST[self.kind]


# ── text_entities ────────────────────────────────────────────────────

_URL_RE = re.compile(r"https?://\S+")


def entity_text(entities: Sequence[Mapping[str, object]] | None) -> str:
    """`text_entities` → 사람이 읽는 한 줄. **`text` 필드는 쓰지 않는다.**"""
    if not entities:
        return ""
    return "".join(str(e.get("text") or "") for e in entities)


def entity_link_labels(entities: Sequence[Mapping[str, object]] | None) -> tuple[str, ...]:
    """`text_link`가 링크 대신 보여 주는 문구. **본문이 아니다.**

    「투자판단관련주요경영사항 [공시 원문]」에서 「공시 원문」은 공시명의 일부가
    아니라 버튼 이름이다. 떼어 내지 않으면 정형 파서가 제목을 잘못 읽는다.
    """
    return tuple(
        str(e.get("text") or "")
        for e in entities or ()
        if e.get("type") == "text_link" and str(e.get("text") or "").strip()
    )


def entity_links(entities: Sequence[Mapping[str, object]] | None) -> tuple[str, ...]:
    """`text_entities` → URL. `text_link`의 `href`를 버리지 않는다.

    봇 채널의 원문 링크가 표시문구 뒤에 숨어 있다 — `{"type": "text_link",
    "text": "공시 원문", "href": "http://dart..."}`. `href`를 안 읽으면
    「주요공시 알리미」의 링크가 통째로 사라진다.
    """
    out: list[str] = []
    for e in entities or ():
        href = str(e.get("href") or "").strip()
        if href.startswith(("http://", "https://")):
            out.append(href)
            continue
        text = str(e.get("text") or "")
        if e.get("type") in {"link", "url"} and text.startswith(("http://", "https://")):
            out.append(text)
    # 본문에 맨 URL로 박힌 것도 줍는다 — 엔티티가 안 붙어 오는 내보내기가 있다.
    out.extend(m.group(0) for m in _URL_RE.finditer(entity_text(entities)))
    seen: dict[str, None] = {}
    for u in out:
        seen.setdefault(u.rstrip(").,"), None)
    return tuple(seen)


def permalink_for(chat_id: int, message_id: int, chat_type: str) -> str | None:
    """딥링크. **공개 채널은 만들 수 없다.**

    비공개는 `t.me/c/{id}/{msg}`로 조립된다(내보내기의 `id`가 `-100` 접두 없는
    그 값이다). 공개 채널은 내보내기 JSON에 username이 없어서 주소를 지어낼
    방법이 없다 — **없는 것이 정상 상태**이지 파싱 실패가 아니다.
    """
    if chat_type.startswith("private") and chat_id > 0:
        return f"https://t.me/c/{chat_id}/{message_id}"
    return None


def _at(raw: Mapping[str, object]) -> tuple[dt.datetime, bool]:
    """메시지 dict → (KST 시각, 로컬시각으로 때웠는가)."""
    unix = raw.get("date_unixtime")
    if unix not in (None, ""):
        try:
            return dt.datetime.fromtimestamp(int(str(unix)), KST), False
        except (ValueError, OSError, OverflowError):
            pass
    # `date`에는 타임존이 없다. 내보낸 기계의 로컬 시각이라 KST라는 보장이
    # 없다 — 때우되 때웠다고 표시한다.
    try:
        naive = dt.datetime.fromisoformat(str(raw.get("date") or ""))
    except ValueError:
        return dt.datetime.fromtimestamp(0, KST), True
    return naive.replace(tzinfo=KST), True


def parse_messages(
    raw_messages: Iterable[Mapping[str, object]],
    *,
    chat_id: int,
    chat_name: str,
    chat_type: str = "public_channel",
    kind: ChannelKind = ChannelKind.UNKNOWN,
) -> tuple[list[Message], int]:
    """메시지 dict 리스트 → (`Message` 리스트, 건너뛴 수).

    `type`이 `"message"`가 아닌 것(가입·핀 고정 같은 서비스 메시지)은 본문이
    없거나 우리가 쓸 게 없다. **세어서 돌려준다** — 조용히 없애면 「8,638건
    중 몇 건을 봤나」에 답할 수 없다.
    """
    out: list[Message] = []
    skipped = 0
    for raw in raw_messages:
        if raw.get("type") != "message":
            skipped += 1
            continue
        entities = raw.get("text_entities")
        entities = entities if isinstance(entities, list) else []
        at, guessed = _at(raw)
        try:
            message_id = int(str(raw.get("id")))
        except (TypeError, ValueError):
            skipped += 1
            continue
        reactions = raw.get("reactions")
        total = 0
        if isinstance(reactions, list):
            for r in reactions:
                if isinstance(r, Mapping):
                    try:
                        total += int(str(r.get("count") or 0))
                    except ValueError:
                        pass
        sender = raw.get("from_id")
        author = raw.get("from")  # 99%. 없는 경우가 있다
        reply = raw.get("reply_to_message_id")
        out.append(
            Message(
                chat_id=chat_id,
                chat_name=chat_name,
                message_id=message_id,
                at=at,
                text=entity_text(entities),
                kind=kind,
                links=entity_links(entities),
                link_labels=entity_link_labels(entities),
                author=str(author) if author not in (None, "") else None,
                sender_id=str(sender) if sender not in (None, "") else None,
                reply_to=int(str(reply)) if isinstance(reply, int) else None,
                forwarded_from=(
                    str(raw["forwarded_from"])
                    if raw.get("forwarded_from") not in (None, "")
                    else None
                ),
                reactions=total,
                edited=bool(raw.get("edited")),
                has_photo=bool(raw.get("photo")),
                permalink=permalink_for(chat_id, message_id, chat_type),
                time_is_local_guess=guessed,
            )
        )
    return out, skipped


def parse_export(chat: Mapping[str, object]) -> Channel:
    """`result.json`의 Chat 객체 → `Channel`. **분류까지 여기서 끝낸다.**

    분류를 밖에 두면 종류가 안 붙은 `Message`가 돌아다니고, 그러면
    `redistribution`이 UNKNOWN으로 고정돼 저작권 경계가 무의미해진다.
    두 번 만드는 값이지만 **판정이 레코드와 함께 다니는 편이** 안전하다.
    """
    chat_id = int(str(chat.get("id") or 0))
    name = str(chat.get("name") or "")
    chat_type = str(chat.get("type") or "public_channel")
    raw = chat.get("messages")
    raw = raw if isinstance(raw, list) else []

    first, skipped = parse_messages(raw, chat_id=chat_id, chat_name=name, chat_type=chat_type)
    # 8,638건짜리 채널의 전량을 형태 통계에 넣을 이유가 없다. **최근 것**을
    # 본다 — 봇이 형식을 바꾸면 옛 메시지가 판정을 과거에 붙들어 둔다.
    kind = classify_channel(name, first[-CLASSIFY_SAMPLE:], chat_type=chat_type)
    stamped = tuple(replace(m, kind=kind) for m in first)
    return Channel(
        chat_id=chat_id,
        name=name,
        chat_type=chat_type,
        kind=kind,
        messages=stamped,
        skipped=skipped,
    )


# ── 채널 분류 ────────────────────────────────────────────────────────
#
# 이름 규칙과 메시지 형태를 **둘 다** 본다. 어느 한쪽만으로는 안 된다:
# 「루팡」·「MZ실버만 운동모드 ON」은 이름에 아무 단서가 없고, 반대로 표본이
# 두세 건뿐인 채널은 형태 통계가 의미가 없다.

# ① 봇·정형 알림. 채널 이름이 「무엇을 흘려보내는지」를 말한다.
_BOT_NAME = re.compile(
    r"(AWAKE|공시|알리미|알림|모니터링|monitor|news ?feed|피드|feed|속보|실시간|"
    r"bot\b|봇\b|시그널|알림방|스캐너|scanner)",
    re.IGNORECASE,
)
# ② 증권사. **이름 맨 앞**에 하우스가 오는 것이 애널리스트 채널의 관례다.
_HOUSES = (
    "한화|다올|DAOL|하나|키움|미래에셋|미래|삼성|NH|KB|신한|메리츠|유안타|대신|현대차|"
    "한국투자|한투|SK|교보|IBK|BNK|DB금융|DS|유진|흥국|부국|상상인|LS|이베스트|"
    "카카오페이|토스|신영|하이|우리|한양|iM|아이엠"
)
_BROKER_NAME = re.compile(rf"^\s*({_HOUSES})\b|증권|리서치센터|securities", re.IGNORECASE)
# 본문 머리표. 「[다올투자증권 조선/기계/방산 최광식] 8/…」
_BROKER_HEAD = re.compile(r"^\s*[\[(【]\s*[^\]\)】]{0,30}(증권|투자증권|자산운용)\s")
# ③ 비공식 리서치.
_RESEARCH_NAME = re.compile(
    r"(리서치|research|뉴스룸|newsroom|投資|애널리틱스|analytics|인사이트|insight|"
    r"밸류|value|캐피탈|capital|랩\b|lab\b)",
    re.IGNORECASE,
)
# ④ 종토방·센티. 시세방의 말투가 이름에 그대로 나온다.
_CHATTER_NAME = re.compile(
    r"(종토방|급등|테마|대장주|고수|재야|개미|타점|여의도|공부|썰\b|단타|스캘|"
    r"수익률|따상|상한가|주주방|투자방|톡방|리딩|N\.?E\.?R\.?D)",
    re.IGNORECASE,
)
# ⑤ 내부 대화방. 이름에서는 **팀 번호**만 구조적 단서다.
_INTERNAL_NAME = re.compile(r"\d\s?팀|\bteam\s?\d")
# 섹터 나열 — 「기계/우주/방산/조선」·「제약/바이오/미용」. 애널리스트·리서치
# 채널의 이름 관례이고, 종토방은 이렇게 안 짓는다.
_SECTORS = re.compile(r"[가-힣A-Za-z]{2,}/[가-힣A-Za-z]{2,}(/[가-힣A-Za-z]{2,})*")
# 구어체 — 종결어미·감탄·자음 반복. 봇과 리서치 글에는 거의 안 나온다.
_COLLOQUIAL = re.compile(
    r"(네요|나요|는데요|던데|잖아|거임|겠죠|죠[.?!\s]|ㅋㅋ|ㅎㅎ|ㅜㅜ|ㅠㅠ|ㄱㄱ|"
    r"쫌|얼마나|가즈아|엮네|싶으면|되나|ㄷㄷ|!!|\?\?|~~)"
)


@dataclass(frozen=True)
class ChannelSignals:
    """분류가 무엇을 보고 판정했는지. **판정만 돌려주면 고칠 수가 없다.**"""

    sample: int = 0
    bot_parse_ratio: float = 0.0  # 우리 정형 파서가 실제로 뽑아낸 비율
    shape_ratio: float = 0.0  # 같은 머리 모양을 가진 메시지 비율(정형성)
    colloquial_ratio: float = 0.0
    broker_head_ratio: float = 0.0
    human_senders: int = 0  # 서로 다른 `user...` 발신자 수
    median_chars: int = 0


_SHAPE_HEAD = 14


def _shape(text: str) -> str:
    """메시지 머리 14자의 **모양**. 글자 종류만 남긴다.

    봇은 매번 같은 자리에 같은 종류를 쓴다 — `2026.08.07 12:` → `9999.99.99 99:`.
    사람이 쓴 글은 이 모양이 매번 다르다.
    """
    out: list[str] = []
    for ch in text[:_SHAPE_HEAD]:
        if ch.isdigit():
            out.append("9")
        elif "가" <= ch <= "힣":
            out.append("가")
        elif ch.isascii() and ch.isalpha():
            out.append("A")
        elif ch.isspace():
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def channel_signals(messages: Sequence[Message | str]) -> ChannelSignals:
    """표본에서 뽑은 형태 통계."""
    texts = [m if isinstance(m, str) else m.text for m in messages]
    texts = [t for t in texts if t.strip()]
    n = len(texts)
    if n == 0:
        return ChannelSignals()
    shapes = collections.Counter(_shape(t) for t in texts)
    senders = {
        m.sender_id
        for m in messages
        if isinstance(m, Message) and m.sender_id and m.sender_id.startswith("user")
    }
    parsed = sum(1 for m in messages if isinstance(m, Message) and parse_bot_row(m) is not None)
    lengths = sorted(len(t) for t in texts)
    return ChannelSignals(
        sample=n,
        bot_parse_ratio=parsed / n,
        shape_ratio=shapes.most_common(1)[0][1] / n,
        colloquial_ratio=sum(1 for t in texts if _COLLOQUIAL.search(t)) / n,
        broker_head_ratio=sum(1 for t in texts if _BROKER_HEAD.search(t)) / n,
        human_senders=len(senders),
        median_chars=lengths[n // 2],
    )


# 형태만으로 봇이라고 부르려면 표본이 이만큼 필요하다. 세 건으로 「전부 같은
# 모양」은 우연히도 맞는다.
_SHAPE_MIN_SAMPLE = 5
_SHAPE_AT = 0.8


def classify_channel(
    name: str,
    sample_messages: Sequence[Message | str] = (),
    *,
    chat_type: str | None = None,
) -> ChannelKind:
    """채널 이름 + 메시지 표본 → 다섯 부류.

    **순서가 곧 신뢰도다.** 구조적 사실(발신자가 여럿인가, 우리 파서가 실제로
    뽑아내는가)이 이름 규칙보다 위에 있다. 이름은 사람이 마음대로 짓는다.

    1. **대화방** — 사람 발신자가 둘 이상이면 채널이 아니라 대화방이다. 채널
       게시물은 발신자가 채널 자신(`channel...`)이라 이 값이 0이다.
    2. **봇** — 우리 정형 파서가 표본의 절반 이상을 뽑아내면 봇이다. 이름
       규칙보다 강한 증거다.
    3. 이름 규칙 — 봇 → 증권사 → 리서치 → 종토방.
    4. 형태 — 이름에 단서가 없을 때만. 정형성이 높으면 봇, 구어체가 많으면
       종토방.

    **모르면 `UNKNOWN`을 낸다.** 표본 없이 이름만으로 갈리지 않는 채널이
    실제로 있고(「루팡」·「MZ실버만 운동모드 ON」), 거기서 억지로 하나를 고르면
    저작권 등급이 틀린 쪽으로 간다. UNKNOWN은 인용 금지로 떨어지므로 안전하다.
    """
    sig = channel_signals(sample_messages)

    # 1. 구조 — 대화방인가
    group = bool(chat_type and ("group" in chat_type or chat_type == "personal_chat"))
    if sig.human_senders >= 2 or (group and sig.human_senders >= 1):
        return ChannelKind.INTERNAL
    if group and _INTERNAL_NAME.search(name):
        return ChannelKind.INTERNAL

    # 2. 구조 — 우리 파서가 실제로 뽑아내는가. **세 건은 있어야 한다** —
    #    「종목(+5%)」 한두 건은 종토방에서도 나온다.
    if sig.sample >= 3 and sig.bot_parse_ratio >= 0.5:
        return ChannelKind.BOT_FEED

    # 3. 이름
    if _BOT_NAME.search(name):
        return ChannelKind.BOT_FEED
    if _BROKER_NAME.search(name):
        return ChannelKind.BROKER
    if sig.sample and sig.broker_head_ratio >= 0.5:
        return ChannelKind.BROKER
    if _RESEARCH_NAME.search(name):
        return ChannelKind.RESEARCH
    if _CHATTER_NAME.search(name):
        return ChannelKind.CHATTER
    if _INTERNAL_NAME.search(name):
        return ChannelKind.INTERNAL
    # 섹터 나열은 애널리스트·리서치 채널의 이름 관례다. 하우스가 앞에 없으면
    # 비공식 리서치로 본다 — 「제약/바이오/미용 원리버」.
    if _SECTORS.search(name):
        return ChannelKind.RESEARCH

    # 4. 형태 — 이름이 아무 말도 안 할 때만
    if sig.sample >= _SHAPE_MIN_SAMPLE and sig.shape_ratio >= _SHAPE_AT:
        return ChannelKind.BOT_FEED
    if sig.sample >= 2 and sig.colloquial_ratio >= 0.5:
        return ChannelKind.CHATTER
    return ChannelKind.UNKNOWN


# ── ① 봇 채널 정형 파서 ──────────────────────────────────────────────
#
# 봇 채널은 요약할 것이 아니라 **행으로 뽑을 것**이다. 「AWAKE 공시」 하루치를
# LLM에 넣으면 토큰만 태우고, 정작 필요한 건 「어느 종목에 어떤 보고서가
# 언제」라는 표다.


@dataclass(frozen=True)
class BotRow:
    """봇 메시지 한 건 → 구조화된 행."""

    format: str  # awake_disclosure | awake_high52 | notice_bot
    at: dt.datetime
    chat_name: str
    message_id: int
    company: str | None = None
    symbol: str | None = None
    headline: str | None = None  # 보고서명·공시명
    change_pct: float | None = None  # 신고가 알림의 등락률
    keywords: tuple[str, ...] = ()
    url: str | None = None
    fields: dict[str, str] = field(default_factory=dict)  # 라벨: 값 그대로
    permalink: str | None = None

    @property
    def lane(self) -> Lane:
        """봇이 옮긴 공시라도 **우리가 원문을 읽은 것은 아니다.**

        진짜 공시 레인은 DART 어댑터다. 여기서 온 「기업명·보고서명」은 그쪽을
        조회할 **단서**이지 검산된 사실이 아니다.
        """
        return Lane.UNVERIFIED


@dataclass(frozen=True)
class BotParse:
    """봇 채널 파싱 결과. **실패를 조용히 버리지 않는다.**"""

    rows: tuple[BotRow, ...]
    unparsed: tuple[tuple[Message, str], ...]  # (메시지, 왜 못 읽었는가)

    @property
    def rate(self) -> float:
        total = len(self.rows) + len(self.unparsed)
        return len(self.rows) / total if total else 0.0


# 「2026.08.07 12:41:20 기업명: 신성이엔지(011930) 보고서명: …」
_AWAKE_TS = re.compile(
    r"^\s*(?P<y>\d{4})[.\-/](?P<m>\d{1,2})[.\-/](?P<d>\d{1,2})\s+"
    r"(?P<hh>\d{1,2}):(?P<mm>\d{2})(?::(?P<ss>\d{2}))?\s*(?P<rest>.*)",
    re.DOTALL,
)
# 라벨은 한글 2~8자 + 콜론. `http:`는 라틴이라 안 걸린다.
_LABEL = re.compile(r"([가-힣]{2,8})\s*:\s*")
# 이름 뒤 괄호의 6자리 종목코드. **닫는 괄호를 요구하지 않는다** — 알림이
# 길이 제한에 잘려 오는 일이 잦다.
_CODE_IN_NAME = re.compile(r"^(?P<name>[^(\n]+?)\s*\(\s*(?P<code>\d{6})?")

# 「✅ 안국약품(+2.31%) 📁 키워드 콜레스테롤 …」
_HIGH52 = re.compile(
    r"^\s*(?P<mark>[✅❗️🔺🔻⭕️🟢🔴]+)?\s*"
    r"(?P<name>[^()\n]{2,20}?)\s*\(\s*(?P<sign>[+\-−])?\s*(?P<pct>\d+(?:\.\d+)?)\s*%\s*\)?"
    r"(?P<rest>.*)",
    re.DOTALL,
)
_KEYWORDS = re.compile(r"[📁🏷#]\s*(?:키워드|태그)?\s*(?P<kw>.+)", re.DOTALL)

# 「투자판단관련주요경영사항 http://…」 — 공시명 하나 + 링크 하나.
# **좁게 잡는다.** 「제목 + 링크」를 다 받으면 리서치 채널의 링크 공유가
# 전부 공시 알림으로 둔갑한다. 한글·숫자·괄호만, 문장부호 없이, 40자 이내.
_NOTICE_TITLE = re.compile(r"^[가-힣0-9()·\s]{4,40}$")


def _split_labels(rest: str) -> dict[str, str]:
    """「기업명: A 보고서명: B」 → {기업명: A, 보고서명: B}.

    한 줄로 오기도 하고 줄바꿈으로 오기도 한다. 라벨 위치를 찾아 사이를
    잘라내면 둘 다 된다.
    """
    marks = list(_LABEL.finditer(rest))
    out: dict[str, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(rest)
        out[m.group(1)] = rest[m.end() : end].strip()
    return out


def _parse_awake_disclosure(msg: Message) -> BotRow | None:
    m = _AWAKE_TS.match(msg.text)
    if m is None:
        return None
    fields = _split_labels(m.group("rest"))
    if not fields:
        return None
    company = fields.get("기업명") or fields.get("회사명") or fields.get("종목명")
    symbol = None
    if company:
        hit = _CODE_IN_NAME.match(company)
        if hit:
            company = hit.group("name").strip()
            symbol = hit.group("code")
    headline = fields.get("보고서명") or fields.get("공시명") or fields.get("제목")
    try:
        at = dt.datetime(
            int(m.group("y")),
            int(m.group("m")),
            int(m.group("d")),
            int(m.group("hh")),
            int(m.group("mm")),
            int(m.group("ss") or 0),
            tzinfo=KST,
        )
    except ValueError:
        at = msg.at
    return BotRow(
        format="awake_disclosure",
        at=at,
        chat_name=msg.chat_name,
        message_id=msg.message_id,
        company=company or None,
        symbol=symbol,
        headline=headline,
        url=msg.links[0] if msg.links else None,
        fields=fields,
        permalink=msg.permalink,
    )


def _parse_awake_high52(msg: Message) -> BotRow | None:
    m = _HIGH52.match(msg.text)
    if m is None:
        return None
    name = m.group("name").strip()
    if not name or _AWAKE_TS.match(msg.text):  # 공시 알림이 먼저다
        return None
    pct = float(m.group("pct"))
    if m.group("sign") in {"-", "−"}:
        pct = -pct
    rest = m.group("rest")
    kw = _KEYWORDS.search(rest)
    keywords: tuple[str, ...] = ()
    if kw:
        raw = kw.group("kw").replace("\n", " ")
        keywords = tuple(w for w in re.split(r"[,\s·]+", raw) if w)
    return BotRow(
        format="awake_high52",
        at=msg.at,
        chat_name=msg.chat_name,
        message_id=msg.message_id,
        company=name,
        change_pct=pct,
        keywords=keywords,
        url=msg.links[0] if msg.links else None,
        fields={"등락률": f"{pct:+.2f}%"},
        permalink=msg.permalink,
    )


def _parse_notice(msg: Message) -> BotRow | None:
    """공시명 + 링크. 링크는 맨 URL로 오기도 하고 `text_link`로 오기도 한다."""
    if not msg.links:
        return None
    title = msg.text
    for label in msg.link_labels:  # 「공시 원문」 같은 버튼 이름은 제목이 아니다
        title = title.replace(label, " ")
    title = " ".join(_URL_RE.sub(" ", title).split())
    # 공시 보고서명은 공백이 거의 없다. 「오늘 이거 보세요 http://…」를 막는다.
    if not _NOTICE_TITLE.match(title) or title.count(" ") > 2:
        return None
    return BotRow(
        format="notice_bot",
        at=msg.at,
        chat_name=msg.chat_name,
        message_id=msg.message_id,
        headline=title,
        url=msg.links[0],
        permalink=msg.permalink,
    )


# 순서가 있다. 공시 알림이 신고가 알림보다 먼저다 — 앞엣것이 더 좁다.
_PARSERS = (_parse_awake_disclosure, _parse_notice, _parse_awake_high52)


def parse_bot_row(msg: Message) -> BotRow | None:
    """봇 메시지 한 건 → 행. 어느 형식도 아니면 None."""
    for fn in _PARSERS:
        row = fn(msg)
        if row is not None:
            return row
    return None


def parse_bot_messages(messages: Iterable[Message]) -> BotParse:
    """봇 채널 메시지 → 행 + **못 읽은 것**.

    못 읽은 것을 버리면 봇이 형식을 바꿔도 아무도 모른다. 8,638건짜리 채널에서
    파싱률이 조용히 0.4로 떨어지는 것이 이 도구가 실제로 죽는 방식이다.
    """
    rows: list[BotRow] = []
    bad: list[tuple[Message, str]] = []
    for msg in messages:
        if not msg.text.strip():
            bad.append((msg, "본문 없음(사진·서비스 메시지)"))
            continue
        row = parse_bot_row(msg)
        if row is None:
            bad.append((msg, "알려진 형식 아님"))
        else:
            rows.append(row)
    return BotParse(rows=tuple(rows), unparsed=tuple(bad))


# ── 저작권 경계 ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class PublishItem:
    """재배포용 표현. **원문은 `text`에 있을 때만 있다.**"""

    chat_name: str
    at: dt.datetime
    kind: ChannelKind
    redistribution: Redistribution
    lane: Lane
    text: str | None  # 인용 허용일 때만. 아니면 None
    masked_excerpt: str | None  # 요약기에 넘길 것 — 숫자를 가린 발췌
    needs_summary: bool
    links: tuple[str, ...]
    permalink: str | None
    notice: str


# 요약기에 넘길 발췌 길이. 원문 전체를 넘기면 그게 곧 재배포다.
EXCERPT_CHARS = 400


def publish_view(msg: Message, *, excerpt_chars: int = EXCERPT_CHARS) -> PublishItem:
    """레코드 → 밖으로 나갈 표현. **여기를 거치지 않고 나가는 길이 없어야 한다.**

    * `OPEN`(봇 알림) — 사실 통지다. 원문을 실어도 된다. 그래도 실무에서는
      `BotRow`로 다시 만든 표를 쓰는 편이 낫다.
    * `SUMMARY_ONLY`(증권사·리서치·종토방) — 원문 자리를 비우고 **숫자를 가린
      발췌**만 요약기에 넘긴다. 가림은 [D45](../../../docs/decisions.md#d45)의
      `mask_numbers()`를 그대로 쓴다 — 규칙이 갈라지면 한쪽이 반드시 샌다.
    * `INTERNAL_ONLY`(내부 대화방) — 발췌도 안 만든다. 링크만 남는다.
    """
    from arc.llm.number_registry import mask_numbers

    grade = msg.redistribution
    if grade is Redistribution.OPEN:
        return PublishItem(
            chat_name=msg.chat_name,
            at=msg.at,
            kind=msg.kind,
            redistribution=grade,
            lane=msg.lane,
            text=msg.text,
            masked_excerpt=None,
            needs_summary=False,
            links=msg.links,
            permalink=msg.permalink,
            notice="봇 알림 — 사실 통지. 수치는 원문(공시)에서 확인하십시오.",
        )
    if grade is Redistribution.INTERNAL_ONLY:
        return PublishItem(
            chat_name=msg.chat_name,
            at=msg.at,
            kind=msg.kind,
            redistribution=grade,
            lane=msg.lane,
            text=None,
            masked_excerpt=None,
            needs_summary=False,
            links=(),
            permalink=msg.permalink,
            notice="내부 대화방 — 밖으로 내보내지 않습니다.",
        )
    excerpt = mask_numbers(msg.text[:excerpt_chars])
    return PublishItem(
        chat_name=msg.chat_name,
        at=msg.at,
        kind=msg.kind,
        redistribution=grade,
        lane=msg.lane,
        text=None,
        masked_excerpt=excerpt,
        needs_summary=True,
        links=msg.links,
        permalink=msg.permalink,
        notice=f"{msg.chat_name} 원문 — 재배포하지 않습니다. 요약과 링크만 싣습니다.",
    )


@dataclass(frozen=True)
class Leak:
    """원문이 그대로 새어 나간 자리."""

    chat_name: str
    message_id: int
    run: str  # 겹친 문자열
    at_in_payload: int


# 이만큼 연속으로 같으면 「베꼈다」고 본다. 짧게 잡으면 흔한 어절이 걸리고,
# 길게 잡으면 한 문장 인용을 놓친다. 한국어 한 문장이 대개 20~40자다.
LEAK_RUN = 25


def find_verbatim_leaks(
    payload: str, messages: Iterable[Message], *, min_run: int = LEAK_RUN
) -> list[Leak]:
    """내보낼 글에 **인용 금지 원문이 그대로** 들어 있는지 본다.

    G0가 미등록 숫자에 하는 일을 원문에 한다. 요약기가 「요약해 달라」는 말을
    무시하고 문장을 그대로 옮기는 일은 실제로 일어나고, 그때 막을 것이 없으면
    저작권 경계는 문서에만 있는 셈이 된다.

    공백을 지운 뒤 비교한다 — 줄바꿈만 바꿔 옮긴 것도 베낀 것이다.
    """
    flat, index = _flatten(payload)
    grams = {flat[i : i + min_run]: index[i] for i in range(len(flat) - min_run + 1)}
    out: list[Leak] = []
    for msg in messages:
        if msg.quotable or not msg.text.strip():
            continue
        src, src_index = _flatten(msg.text)
        for start in range(len(src) - min_run + 1):
            hit = grams.get(src[start : start + min_run])
            if hit is not None:
                begin = src_index[start]
                out.append(
                    Leak(
                        chat_name=msg.chat_name,
                        message_id=msg.message_id,
                        run=msg.text[begin : begin + min_run + 5].strip(),
                        at_in_payload=hit,
                    )
                )
                break
    return out


def _flatten(text: str) -> tuple[str, list[int]]:
    """공백을 지운 문자열과 원문 위치 대응표."""
    chars: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        if ch.isspace():
            continue
        chars.append(ch)
        index.append(i)
    return "".join(chars), index
