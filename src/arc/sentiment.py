"""시장 센티 — **지금 무슨 말이 도는가.**

왜 브리프가 아니라 따로인가
---------------------------
브리프는 **놓친 것이 없다는 확인**이라 짧아야 한다(시장 → 섹터 → 종목).
센티는 성격이 다르다 — 뒤지는 화면이다. 어느 종목이 왜 도는지, 누가 말했는지,
언제부터 말했는지를 파고든다. 둘을 한 화면에 두면 브리프가 길어져서 아침에
읽히지 않는다.

시간대가 뜻을 가진다
--------------------
같은 「5회 언급」이라도 **언제**냐에 따라 완전히 다른 얘기다:

* **장전(~09:00)** — 밤사이 해외 뉴스·전일 리포트. 오늘 갭을 만든다
* **장중(09:00~15:30)** — 지금 움직이는 것에 대한 반응
* **장후(15:30~)** — 마감 리뷰·다음 날 준비. 증권사 채널이 여기 몰린다

그래서 종목마다 **시간대 분포**를 함께 낸다. 장중에만 몰린 종목과 장전부터
있던 종목은 RA에게 다른 신호다.

**미검증 레인이다**
-------------------
여기 있는 것은 전부 [D45](../../docs/decisions.md#d45)의 미검증 레인이고,
**숫자는 아무것도 안 읽는다.** 언급 횟수는 우리가 센 것이라 사실이지만,
메시지 안의 목표주가·실적 전망은 본문 어디에도 안 들어간다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from arc.ingest.telegram_parse import ChannelKind, Message

# **내 것이 먼저다.** 언급이 아무리 몰려도 내가 안 보는 종목은 그 아래다 —
# 아침에 알고 싶은 것은 「시장에서 뜬 것」이 아니라 「내 것 중에 뜬 것」이다.
#
# 「내 섹터」는 자유 텍스트라 코드가 못 읽는다(D68). 대신 **피어 그룹**을
# 쓴다 — 그게 그 섹터의 실질적 정의이고, 사람이 확정해 고정한 것이다.
MINE_RANK = {"cover": 0, "watch": 1, "peer": 2}

# 한국 시장의 하루. 정규장 09:00~15:30(동시호가 포함 15:20~15:30).
SESSIONS: tuple[tuple[str, str, int, int], ...] = (
    ("pre", "장전", 0, 9),
    ("intra", "장중", 9, 16),
    ("post", "장후", 16, 24),
)


def session_of(when: dt.datetime) -> str:
    """KST 시각 → 장전/장중/장후."""
    h = when.hour
    for key, _, start, end in SESSIONS:
        if start <= h < end:
            return key
    return "post"


@dataclass
class Mention:
    """센티 목록의 한 줄 = 종목 하나."""

    symbol: str
    name: str
    today: int
    baseline_per_day: float
    ratio: float
    channels: tuple[str, ...] = ()
    # 장전/장중/장후 건수. **언제 돌았는지가 절반이다.**
    by_session: dict[str, int] = field(default_factory=dict)
    samples: list[dict] = field(default_factory=list)
    # **내 것인가, 어느 정도로.** 화면이 이걸로 위에 올린다.
    #   cover 내가 리포트를 내는 종목
    #   watch 옆에서 보는 종목
    #   peer  내 피어 그룹 안에 있는 종목 — 「내 섹터」의 실질적 정의다
    #   ""    나머지
    mine: str = ""
    # 어느 피어 그룹에서 왔는가 (`mine == "peer"`일 때).
    via: str = ""

    @property
    def peak(self) -> str:
        """가장 많이 돈 시간대. 비면 빈 문자열."""
        if not self.by_session:
            return ""
        return max(self.by_session.items(), key=lambda kv: kv[1])[0]


@dataclass
class Sentiment:
    """하루치 센티."""

    day: str = ""
    total: int = 0
    # 시간대별 전체 메시지 수 — 그날의 소란 자체가 신호다.
    by_session: dict[str, int] = field(default_factory=dict)
    # 채널 종류별 건수 (broker / research / chatter / bot_feed …)
    by_kind: dict[str, int] = field(default_factory=dict)
    mentions: list[Mention] = field(default_factory=list)
    channels: list[dict] = field(default_factory=list)
    note: str = ""

    @property
    def empty(self) -> bool:
        return self.total == 0


def _sample(msg: Message, text_chars: int = 120) -> dict:
    """한 건의 표시용 조각. **원문을 통째로 싣지 않는다.**

    ②증권사·③리서치 채널은 저작권이 명확해서 요약·발췌만 낸다. 링크를 함께
    내는 것이 그 대가다 — 원문은 거기서 본다.
    """
    text = " ".join(msg.text.split())
    return {
        "channel": msg.chat_name,
        "kind": msg.kind.value if isinstance(msg.kind, ChannelKind) else str(msg.kind),
        "at": msg.at.isoformat(timespec="minutes"),
        "session": session_of(msg.at),
        "excerpt": text[:text_chars] + ("…" if len(text) > text_chars else ""),
        # 앱을 직접 여는 링크와 웹 링크를 **둘 다** 낸다 — 앱이 없으면
        # `tg://`가 아무 일도 안 하기 때문이다.
        "app_link": msg.app_link,
        "web_link": msg.permalink,
    }


def build_sentiment(
    messages: list[Message],
    surges: list,
    *,
    day: dt.date,
    mine: dict[str, str] | None = None,
    peers: dict[str, str] | None = None,
    samples_per_mention: int = 3,
) -> Sentiment:
    """메시지 + 급증 목록 → 화면이 쓸 하루치 센티. **순수 함수다.**

    `mine`은 `{종목코드: "cover"|"watch"}`, `peers`는 `{종목코드: 그룹이름}`.
    **내 것이 먼저다** — 언급이 아무리 몰려도 내가 안 보는 종목은 그 아래다.
    """
    mine = mine or {}
    peers = peers or {}
    today = [m for m in messages if m.day == day]
    out = Sentiment(day=day.isoformat(), total=len(today))

    for m in today:
        key = session_of(m.at)
        out.by_session[key] = out.by_session.get(key, 0) + 1
        kind = m.kind.value if isinstance(m.kind, ChannelKind) else str(m.kind)
        out.by_kind[kind] = out.by_kind.get(kind, 0) + 1

    by_id = {(m.chat_name, m.message_id): m for m in today}
    for surge in surges:
        picked = []
        sessions: dict[str, int] = {}
        for channel, message_id, _link in surge.samples:
            msg = by_id.get((channel, message_id))
            if msg is None:
                continue
            sessions[session_of(msg.at)] = sessions.get(session_of(msg.at), 0) + 1
            if len(picked) < samples_per_mention:
                picked.append(_sample(msg))
        out.mentions.append(
            Mention(
                symbol=surge.symbol,
                name=surge.name,
                today=surge.today,
                baseline_per_day=surge.baseline_per_day,
                ratio=surge.ratio,
                channels=tuple(surge.channels),
                by_session=sessions,
                samples=picked,
                mine=mine.get(surge.symbol) or ("peer" if surge.symbol in peers else ""),
                via=peers.get(surge.symbol, "") if surge.symbol not in mine else "",
            )
        )

    # 커버 → 관심 → 피어 그룹 → 나머지. 그 안에서 급증 배수 순이다.
    out.mentions.sort(key=lambda x: (MINE_RANK.get(x.mine, 9), -x.ratio, -x.today))

    counts: dict[tuple[str, str], int] = {}
    for m in today:
        kind = m.kind.value if isinstance(m.kind, ChannelKind) else str(m.kind)
        counts[(m.chat_name, kind)] = counts.get((m.chat_name, kind), 0) + 1
    out.channels = [
        {"name": name, "kind": kind, "count": n}
        for (name, kind), n in sorted(counts.items(), key=lambda kv: -kv[1])
    ]
    out.note = _note(out)
    return out


def _note(s: Sentiment) -> str:
    """맨 위 한 줄. **없으면 없다고 말한다.**"""
    if s.total == 0:
        return "이 날짜에 받아 둔 메시지가 없습니다 — `arc telegram sync`로 가져옵니다."
    parts = [f"메시지 {s.total:,}건"]
    if s.mentions:
        parts.append(f"급증 {len(s.mentions)}종목")
    owned = sum(1 for m in s.mentions if m.mine in ("cover", "watch"))
    near = sum(1 for m in s.mentions if m.mine == "peer")
    if owned:
        parts.append(f"내 종목 {owned}건")
    if near:
        parts.append(f"내 피어 {near}건")
    return " · ".join(parts)
