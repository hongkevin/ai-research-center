"""텔레그램 내보내기 → 레코드·분류·정형 파싱·언급 급증, 그리고 저작권 경계.

여기서 지키는 불변식 셋:

1. **미검증 레인이 타입에 박혀 있다.** 텔레그램에서 온 것은 무엇이든
   `Lane.UNVERIFIED`다 ([D45](../docs/decisions.md#d45)).
2. **재배포 경계를 코드가 지킨다.** 증권사·리서치 원문은 `publish_view()`를
   지나면 사라지고, 그래도 새어 나가면 `find_verbatim_leaks()`가 잡는다.
3. **종목 추출 오탐 방어가 측정으로 고정돼 있다.** 규칙을 느슨하게 하면
   `TestMeasuredExtraction`이 깨진다.

메시지 픽스처는 D66에 적힌 **실제 채널 이름과 실제 예시 문자열**이다.
"""

from __future__ import annotations

import csv
import datetime as dt
import glob
import pathlib
import re

import pytest

from arc.ingest.telegram_mentions import (
    COMMON_WORD_NAMES,
    Evidence,
    NameIndex,
    extract_mentions,
    mention_surges,
    message_symbols,
)
from arc.ingest.telegram_parse import (
    KST,
    ChannelKind,
    Lane,
    Message,
    Redistribution,
    channel_signals,
    classify_channel,
    entity_links,
    entity_text,
    find_verbatim_leaks,
    parse_bot_messages,
    parse_bot_row,
    parse_export,
    parse_messages,
    permalink_for,
    publish_view,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]


# ── 픽스처 만들기 ────────────────────────────────────────────────────


def raw_message(
    msg_id: int,
    text: str | list[dict],
    *,
    unixtime: int = 1_786_074_080,  # 2026-08-07 12:41:20 KST
    sender: str | None = "channel2073492571",
    author: str | None = "GAMBLER NEWS",
    **extra: object,
) -> dict:
    """내보내기 JSON의 메시지 dict 한 건. 실측 필드 존재율에 맞춘다."""
    entities = text if isinstance(text, list) else [{"type": "plain", "text": text}]
    flat = "".join(e.get("text", "") for e in entities)
    out: dict = {
        "id": msg_id,
        "type": "message",
        "date": dt.datetime.fromtimestamp(unixtime, KST).strftime("%Y-%m-%dT%H:%M:%S"),
        "date_unixtime": str(unixtime),
        "text": flat,
        "text_entities": entities,
    }
    if author is not None:
        out["from"] = author
    if sender is not None:
        out["from_id"] = sender
    out.update(extra)
    return out


def channel(
    name: str, texts: list, *, chat_type: str = "public_channel", chat_id: int = 2073492571
):
    return {
        "name": name,
        "type": chat_type,
        "id": chat_id,
        "messages": [raw_message(i, t) for i, t in enumerate(texts, 1)],
    }


# 실제 예시 문자열 (D66 · 사용자 스크린샷)
AWAKE_DISCLOSURE = (
    "2026.08.07 12:41:20 기업명: 신성이엔지(011930) 보고서명: 투자판단관련주요경영사항"
)
AWAKE_TRUNCATED = "2026.08.07 12:41:20 기업명: 신성이엔지("
AWAKE_HIGH52 = "✅ 안국약품(+2.31%) 📁 키워드 콜레스테롤 제네릭"
NOTICE_ENTITIES = [
    {"type": "plain", "text": "투자판단관련주요경영사항 "},
    {
        "type": "text_link",
        "text": "공시 원문",
        "href": "http://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260807000123",
    },
]
NEWS_FEED = "JP모건은 언더라이팅으로 이지젯을 중립으로 하향했다"
BROKER_POST = "[다올투자증권 조선/기계/방산 최광식] 8/7 조선 주간 코멘트입니다"
RESEARCH_POST = "📊 소프트뱅크. NAV 좋고 LTV 개선됐다"
CHATTER_1 = "상승 이유 이걸 이래 엮네요 [특징주] 혜인, AI 데이터센터"
CHATTER_2 = "쫌 되나 싶으면 패댁 어닝 미스나면 -10%"


# ── ① 내보내기 스키마 ───────────────────────────────────────────────


class TestExportSchema:
    def test_text_entities_only(self):
        """서식이 붙어도 분기가 없다. **`text` 필드는 안 쓴다.**"""
        entities = [
            {"type": "plain", "text": "✅ "},
            {"type": "bold", "text": "제목"},
            {"type": "plain", "text": " 본문"},
        ]
        assert entity_text(entities) == "✅ 제목 본문"

    def test_text_link_href_survives(self):
        """봇 채널의 원문 링크가 표시문구 뒤에 있다 — 버리면 링크가 사라진다."""
        assert entity_links(NOTICE_ENTITIES) == (
            "http://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260807000123",
        )

    def test_bare_url_in_text_is_also_a_link(self):
        assert entity_links([{"type": "plain", "text": "보세요 https://a.co/x 끝"}]) == (
            "https://a.co/x",
        )

    def test_service_messages_are_counted_not_dropped(self):
        """`type != "message"`를 조용히 없애면 「몇 건을 봤나」에 답할 수 없다."""
        raw = [
            raw_message(1, "본문"),
            {"id": 2, "type": "service", "action": "pin_message", "date_unixtime": "1"},
        ]
        msgs, skipped = parse_messages(raw, chat_id=1, chat_name="X")
        assert len(msgs) == 1
        assert skipped == 1

    def test_missing_from_is_fine(self):
        """`from`은 99%다 — 없는 1%에서 죽으면 안 된다."""
        raw = [raw_message(1, "본문", author=None, sender=None)]
        msgs, _ = parse_messages(raw, chat_id=1, chat_name="X")
        assert msgs[0].author is None
        assert msgs[0].is_human is False

    def test_unixtime_wins_over_date(self):
        """`date`는 **내보낸 기계의 로컬 시각**이라 믿을 수 없다.

        실측 사례를 그대로 넣는다 — `date`가 08:14인데 `date_unixtime`은
        14:14 KST를 가리켰다. 절대 시각은 후자뿐이다.
        """
        raw = [
            {
                "id": 14,
                "type": "message",
                "date": "2024-10-11T08:14:00",
                "date_unixtime": "1728623640",
                "text": "🔮",
                "text_entities": [{"type": "plain", "text": "🔮"}],
            }
        ]
        msgs, _ = parse_messages(raw, chat_id=1, chat_name="X")
        assert msgs[0].at == dt.datetime(2024, 10, 11, 14, 14, tzinfo=KST)
        assert msgs[0].time_is_local_guess is False

    def test_date_fallback_is_flagged(self):
        raw = [{"id": 1, "type": "message", "date": "2026-08-07T09:00:00", "text_entities": []}]
        msgs, _ = parse_messages(raw, chat_id=1, chat_name="X")
        assert msgs[0].time_is_local_guess is True

    def test_reactions_replace_views(self):
        """조회수는 내보내기에 없다. 반응이 대체 신호다."""
        raw = [
            raw_message(
                1,
                "본문",
                reactions=[{"type": "emoji", "count": 16}, {"type": "emoji", "count": 4}],
            )
        ]
        msgs, _ = parse_messages(raw, chat_id=1, chat_name="X")
        assert msgs[0].reactions == 20

    def test_reply_thread_survives(self):
        raw = [raw_message(9, "네 맞습니다", reply_to_message_id=7)]
        msgs, _ = parse_messages(raw, chat_id=1, chat_name="X")
        assert msgs[0].reply_to == 7

    def test_permalink_private_only(self):
        """공개 채널은 username이 없어 링크를 만들 수 없다 — **없는 게 정상**이다."""
        assert permalink_for(2073492571, 14, "private_channel") == ("https://t.me/c/2073492571/14")
        assert permalink_for(2073492571, 14, "public_channel") is None

    def test_lane_is_a_property_not_a_field(self):
        """레인을 설정할 수 있으면 언젠가 설정된다."""
        msg = parse_messages([raw_message(1, "x")], chat_id=1, chat_name="X")[0][0]
        assert msg.lane is Lane.UNVERIFIED
        with pytest.raises(AttributeError):
            msg.lane = Lane.VERIFIED  # type: ignore[misc]


# ── ② 채널 분류 ─────────────────────────────────────────────────────

# (부류, 이름, chat_type, 예시 메시지). 이름은 실제 구독 채널 그대로다.
CHANNELS: list[tuple[ChannelKind, str, str, list]] = [
    (ChannelKind.BOT_FEED, "AWAKE - 실시간 주식 공시 정리", "public_channel", [AWAKE_DISCLOSURE]),
    (ChannelKind.BOT_FEED, "AWAKE - 52주 신고가 모니터링", "public_channel", [AWAKE_HIGH52]),
    (ChannelKind.BOT_FEED, "주요공시 알리미", "public_channel", [NOTICE_ENTITIES]),
    (ChannelKind.BOT_FEED, "News Feed 🇰🇷(ko)", "public_channel", [NEWS_FEED]),
    (ChannelKind.BOT_FEED, "실시간 속보 단독 뉴스", "public_channel", []),
    (ChannelKind.BROKER, "한화 기계/우주/방산/조선 배성조", "public_channel", []),
    (ChannelKind.BROKER, "DAOL 조선/기계/방산 | 최광식", "public_channel", [BROKER_POST]),
    (ChannelKind.BROKER, "하나 중국/신흥국 전략 김경환", "public_channel", []),
    (ChannelKind.BROKER, "키움증권 미국주식 톡톡", "public_channel", []),
    (ChannelKind.RESEARCH, "Pluto Research", "public_channel", []),
    (ChannelKind.RESEARCH, "그로쓰리서치(Growth Research)", "public_channel", []),
    (ChannelKind.RESEARCH, "스터닝밸류리서치", "public_channel", []),
    (ChannelKind.RESEARCH, "더바이오 뉴스룸", "public_channel", []),
    (ChannelKind.RESEARCH, "投資, 아레테", "public_channel", []),
    (ChannelKind.RESEARCH, "제약/바이오/미용 원리버", "public_channel", []),
    (ChannelKind.RESEARCH, "요약하는 고잉", "public_channel", [RESEARCH_POST]),
    (ChannelKind.CHATTER, "타점 읽어주는 여자(타자)", "public_channel", []),
    (ChannelKind.CHATTER, "루팡", "public_channel", [CHATTER_1, CHATTER_2]),
    (ChannelKind.CHATTER, "주식 급등일보🚀급등테마·대장주", "public_channel", []),
    (ChannelKind.CHATTER, "재야의 고수들", "public_channel", []),
    (ChannelKind.CHATTER, "잠실개미&10X's N.E.R.D.S", "public_channel", []),
    (ChannelKind.CHATTER, "MZ실버만 운동모드 ON", "public_channel", []),
    (ChannelKind.CHATTER, "해기사투자자의 투자공부", "public_channel", []),
    (ChannelKind.CHATTER, "여의도스토리", "public_channel", []),
    (ChannelKind.INTERNAL, "SMIC 49x50 3팀 종토방", "private_group", []),
]

# 이름만으로는 안 갈리는 것. **틀리게 부르느니 모른다고 한다** — UNKNOWN은
# 인용 금지로 떨어지므로 저작권에서 안전한 쪽이다.
UNNAMEABLE = {"요약하는 고잉", "MZ실버만 운동모드 ON"}


class TestClassifyChannel:
    @pytest.mark.parametrize(("want", "name", "chat_type", "texts"), CHANNELS)
    def test_real_channel_names(self, want, name, chat_type, texts):
        msgs = [
            m
            for m in parse_messages(
                [raw_message(i, t) for i, t in enumerate(texts, 1)],
                chat_id=1,
                chat_name=name,
                chat_type=chat_type,
            )[0]
        ]
        got = classify_channel(name, msgs, chat_type=chat_type)
        if name in UNNAMEABLE:
            assert got is ChannelKind.UNKNOWN
        else:
            assert got is want

    def test_no_channel_is_confidently_wrong(self):
        """**틀린 확신이 모르는 것보다 나쁘다.** 25개 중 오판 0."""
        wrong = []
        for want, name, chat_type, texts in CHANNELS:
            msgs, _ = parse_messages(
                [raw_message(i, t) for i, t in enumerate(texts, 1)],
                chat_id=1,
                chat_name=name,
                chat_type=chat_type,
            )
            got = classify_channel(name, msgs, chat_type=chat_type)
            if got is not want and got is not ChannelKind.UNKNOWN:
                wrong.append((name, got))
        assert wrong == []

    def test_group_with_two_humans_is_internal(self):
        """발신자 다양성이 이름을 이긴다 — 「종토방」이 이름에 있어도 대화방이다."""
        raw = [
            raw_message(1, "이게 도박인데", sender="user111", author="김태인"),
            raw_message(2, "저도 그렇게 봅니다", sender="user222", author="이수민"),
        ]
        msgs, _ = parse_messages(
            raw, chat_id=1, chat_name="SMIC 49x50 3팀 종토방", chat_type="private_group"
        )
        assert classify_channel("SMIC 49x50 3팀 종토방", msgs, chat_type="private_group") is (
            ChannelKind.INTERNAL
        )

    def test_name_alone_cannot_tell_a_team_room_from_a_stock_room(self):
        """**이름만으로는 못 한다**는 것을 고정한다. 표본이 왜 필요한지의 근거."""
        assert classify_channel("SMIC 49x50 3팀 종토방") is ChannelKind.CHATTER

    def test_signals_are_inspectable(self):
        msgs, _ = parse_messages(
            [raw_message(i, t) for i, t in enumerate([CHATTER_1, CHATTER_2], 1)],
            chat_id=1,
            chat_name="루팡",
        )
        sig = channel_signals(msgs)
        assert sig.sample == 2
        assert sig.colloquial_ratio == 1.0
        assert sig.human_senders == 0


# ── ③ 봇 채널 정형 파서 ─────────────────────────────────────────────


class TestBotParsers:
    def test_awake_disclosure(self):
        chat = channel("AWAKE - 실시간 주식 공시 정리", [AWAKE_DISCLOSURE])
        row = parse_bot_row(parse_export(chat).messages[0])
        assert row is not None
        assert row.format == "awake_disclosure"
        assert row.company == "신성이엔지"
        assert row.symbol == "011930"
        assert row.headline == "투자판단관련주요경영사항"
        # 본문의 시각을 쓴다 — 메시지 시각과 몇 초 어긋날 수 있다
        assert row.at == dt.datetime(2026, 8, 7, 12, 41, 20, tzinfo=KST)

    def test_awake_disclosure_truncated(self):
        """**알림은 잘려 온다.** 닫는 괄호를 요구하면 실전에서 전부 실패한다."""
        chat = channel("AWAKE - 실시간 주식 공시 정리", [AWAKE_TRUNCATED])
        row = parse_bot_row(parse_export(chat).messages[0])
        assert row is not None
        assert row.company == "신성이엔지"
        assert row.symbol is None

    def test_awake_high52(self):
        chat = channel("AWAKE - 52주 신고가 모니터링", [AWAKE_HIGH52])
        row = parse_bot_row(parse_export(chat).messages[0])
        assert row is not None
        assert row.format == "awake_high52"
        assert row.company == "안국약품"
        assert row.change_pct == 2.31
        assert row.keywords == ("콜레스테롤", "제네릭")

    def test_high52_keeps_the_sign(self):
        chat = channel("AWAKE - 52주 신고가 모니터링", ["🔻 안국약품(-1.80%)"])
        row = parse_bot_row(parse_export(chat).messages[0])
        assert row is not None
        assert row.change_pct == -1.80

    def test_notice_bot_takes_the_href(self):
        chat = channel("주요공시 알리미", [NOTICE_ENTITIES])
        row = parse_bot_row(parse_export(chat).messages[0])
        assert row is not None
        assert row.format == "notice_bot"
        assert row.headline == "투자판단관련주요경영사항"
        assert row.url == "http://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260807000123"

    def test_notice_parser_does_not_eat_link_shares(self):
        """리서치 채널의 링크 공유가 공시 알림으로 둔갑하면 안 된다."""
        chat = channel("Pluto Research", ["오늘 이거 한번 보세요 정말 좋습니다 https://a.co/x"])
        assert parse_bot_row(parse_export(chat).messages[0]) is None

    def test_unparsed_is_kept_with_a_reason(self):
        """**조용히 버리지 않는다.** 봇이 형식을 바꾸면 여기서만 보인다."""
        chat = channel(
            "AWAKE - 실시간 주식 공시 정리",
            [AWAKE_DISCLOSURE, "오늘 서버 점검이 있습니다", ""],
        )
        parsed = parse_bot_messages(parse_export(chat).messages)
        assert len(parsed.rows) == 1
        assert [why for _m, why in parsed.unparsed] == [
            "알려진 형식 아님",
            "본문 없음(사진·서비스 메시지)",
        ]
        assert parsed.rate == pytest.approx(1 / 3)

    def test_bot_rows_are_still_unverified(self):
        """봇이 옮긴 공시라도 **우리가 원문을 읽은 것은 아니다.**"""
        chat = channel("AWAKE - 실시간 주식 공시 정리", [AWAKE_DISCLOSURE])
        row = parse_bot_row(parse_export(chat).messages[0])
        assert row is not None
        assert row.lane is Lane.UNVERIFIED


# ── ④ 종목 추출 ─────────────────────────────────────────────────────

SMALL = {
    "대상": "001680",
    "대상홀딩스": "084690",
    "한국전자금융": "063570",
    "신성이엔지": "011930",
    "안국약품": "001540",
    "기아": "000270",
    "삼성전자": "005930",
    "LG": "003550",
    "SK하이닉스": "000660",
    "나노": "187790",
    "유니온": "000910",
}


@pytest.fixture
def index() -> NameIndex:
    return NameIndex.from_names(SMALL)


class TestExtractMentions:
    def test_longest_name_wins(self, index):
        got = extract_mentions("대상홀딩스 실적 발표", index)
        assert [(m.surface, m.symbol) for m in got] == [("대상홀딩스", "084690")]

    def test_containment_does_not_split_a_long_name(self, index):
        """`한국전자금융`이 더 긴 고유명사 안에 있으면 안 센다."""
        assert extract_mentions("한국전자금융산업협회가 발표한 자료", index) == []
        assert [m.symbol for m in extract_mentions("한국전자금융의 실적", index)] == ["063570"]

    def test_josa_is_allowed_other_hangul_is_not(self, index):
        assert [m.symbol for m in extract_mentions("기아가 좋다", index)] == ["000270"]
        assert extract_mentions("나노소재 업종", index) == []

    def test_latin_name_must_not_be_glued(self, index):
        """`LG전자`는 `LG`가 아니다."""
        assert extract_mentions("LG전자 실적", index) == []
        assert [m.symbol for m in extract_mentions("LG 지주 할인", index)] == ["003550"]

    def test_common_word_name_needs_evidence(self, index):
        """「~을 대상으로」가 식품회사가 되면 안 된다."""
        assert "대상" in COMMON_WORD_NAMES
        assert extract_mentions("개인 투자자를 대상으로 한 리포트 발간", index) == []
        assert [m.symbol for m in extract_mentions("대상(001680) 실적", index)] == ["001680"]
        assert [m.symbol for m in extract_mentions("대상(+2.31%) 신고가", index)] == ["001680"]
        assert [m.symbol for m in extract_mentions("$대상 담았습니다", index)] == ["001680"]

    def test_the_cost_of_the_common_word_list_is_named(self, index):
        """목록에 넣으면 **그 종목이 어두워진다.** 값을 치른다는 것을 고정한다."""
        assert extract_mentions("대상 실적 좋네요", index) == []

    def test_code_alone_is_enough(self, index):
        got = extract_mentions("011930 오늘 왜 이래요", index)
        assert [(m.symbol, m.evidence) for m in got] == [("011930", Evidence.CODE)]

    def test_price_like_numbers_are_not_codes(self, index):
        assert extract_mentions("주가가 123,456원까지", index) == []
        assert extract_mentions("2026.08.07 12:41:20", index) == []

    def test_evidence_is_reported(self, index):
        (hit,) = extract_mentions("신성이엔지 목표가 상향", index)
        assert hit.evidence is Evidence.NEAR
        (hit,) = extract_mentions("신성이엔지 관련 이야기", index)
        assert hit.evidence is Evidence.NAME

    def test_one_message_counts_a_symbol_once(self, index):
        chat = channel("루팡", ["기아 기아 기아 기아 기아 가즈아"])
        msg = parse_export(chat).messages[0]
        assert message_symbols(msg, index) == {"000270"}


# ── ⑤ 센티 급증 ─────────────────────────────────────────────────────


def chatter_message(msg_id: int, text: str, day: dt.date, room: str) -> Message:
    at = dt.datetime.combine(day, dt.time(10, 0), tzinfo=KST)
    raw = raw_message(msg_id, text, unixtime=int(at.timestamp()))
    msgs, _ = parse_messages(
        [raw], chat_id=1, chat_name=room, chat_type="public_channel", kind=ChannelKind.CHATTER
    )
    return msgs[0]


class TestSurges:
    TODAY = dt.date(2026, 8, 7)

    def build(self) -> list[Message]:
        msgs: list[Message] = []
        n = 0
        # 기준 기간: 신성이엔지가 조용히 하루 한 건씩
        for d in range(1, 15):
            day = self.TODAY - dt.timedelta(days=d)
            n += 1
            msgs.append(chatter_message(n, "신성이엔지 목표가 어떻게 보세요", day, "루팡"))
        # 오늘: 두 방에서 갑자기
        for room in ("루팡", "재야의 고수들", "여의도스토리"):
            for _ in range(3):
                n += 1
                msgs.append(chatter_message(n, "신성이엔지 상한가 갑니다", self.TODAY, room))
        # 오늘 한 방만 도배 — 급증으로 치지 않는다
        for _ in range(20):
            n += 1
            msgs.append(chatter_message(n, "기아 매수 타점", self.TODAY, "루팡"))
        return msgs

    def test_surge_ratio(self):
        index = NameIndex.from_names(SMALL)
        surges = mention_surges(self.build(), index, on=self.TODAY)
        assert [s.symbol for s in surges] == ["011930"]
        (hit,) = surges
        assert hit.today == 9
        assert hit.baseline_per_day == pytest.approx(1.0)
        assert hit.ratio == pytest.approx(9 / 1.5)
        assert hit.channels == ("루팡", "여의도스토리", "재야의 고수들")

    def test_one_room_shouting_is_not_a_surge(self):
        """리딩방 하나가 미는 것과 시장이 웅성거리는 것은 다르다."""
        index = NameIndex.from_names(SMALL)
        surges = mention_surges(self.build(), index, on=self.TODAY)
        assert "000270" not in {s.symbol for s in surges}
        loose = mention_surges(self.build(), index, on=self.TODAY, min_channels=1)
        assert "000270" in {s.symbol for s in loose}

    def test_first_appearance_does_not_take_infinite_ratio(self):
        index = NameIndex.from_names(SMALL)
        msgs = [
            chatter_message(i, "안국약품 신고가", self.TODAY, room)
            for i, room in enumerate(("루팡", "재야의 고수들", "여의도스토리"), 1)
        ]
        (hit,) = mention_surges(msgs, index, on=self.TODAY)
        assert hit.baseline_per_day == 0.0
        assert hit.ratio == pytest.approx(3 / 0.5)  # 무한대가 아니다

    def test_surge_is_traceable(self):
        index = NameIndex.from_names(SMALL)
        (hit,) = mention_surges(self.build(), index, on=self.TODAY)
        assert len(hit.samples) == 5
        assert all(isinstance(chat, str) and isinstance(mid, int) for chat, mid, _ in hit.samples)
        assert hit.lane is Lane.UNVERIFIED


# ── ⑥ 저작권 경계 ───────────────────────────────────────────────────


class TestRedistribution:
    def test_grades_follow_channel_kind(self):
        assert parse_export(channel("주요공시 알리미", [NOTICE_ENTITIES])).redistribution is (
            Redistribution.OPEN
        )
        assert parse_export(
            channel("DAOL 조선/기계/방산 | 최광식", [BROKER_POST])
        ).redistribution is (Redistribution.SUMMARY_ONLY)
        internal = parse_export(
            {
                "name": "SMIC 49x50 3팀 종토방",
                "type": "private_group",
                "id": 7,
                "messages": [
                    raw_message(1, "이게 도박인데", sender="user111", author="김태인"),
                    raw_message(2, "저도요", sender="user222", author="이수민"),
                ],
            }
        )
        assert internal.redistribution is Redistribution.INTERNAL_ONLY

    def test_unknown_channel_falls_to_no_quoting(self):
        """저작권의 기본값은 **안전한 쪽**이다."""
        ch = parse_export(channel("MZ실버만 운동모드 ON", ["오늘 장 좋네요"]))
        assert ch.kind is ChannelKind.UNKNOWN
        assert ch.redistribution is Redistribution.SUMMARY_ONLY
        assert ch.messages[0].quotable is False

    def test_broker_original_never_leaves(self):
        ch = parse_export(channel("DAOL 조선/기계/방산 | 최광식", [BROKER_POST]))
        item = publish_view(ch.messages[0])
        assert item.text is None
        assert item.needs_summary is True
        assert item.lane is Lane.UNVERIFIED

    def test_excerpt_hides_numbers(self):
        """미검증 레인의 숫자는 D45의 `mask_numbers()`를 그대로 지난다."""
        ch = parse_export(
            channel("Pluto Research", ["소프트뱅크 NAV 12.4조, 목표가 21,000원으로 상향"])
        )
        item = publish_view(ch.messages[0])
        assert item.masked_excerpt is not None
        assert "12.4" not in item.masked_excerpt
        assert "21,000" not in item.masked_excerpt
        assert "⟨수치⟩" in item.masked_excerpt

    def test_internal_room_gets_nothing(self):
        internal = parse_export(
            {
                "name": "SMIC 49x50 3팀 종토방",
                "type": "private_group",
                "id": 7,
                "messages": [
                    raw_message(1, "이게 도박인데 https://a.co/x", sender="user111"),
                    raw_message(2, "저도요", sender="user222"),
                ],
            }
        )
        item = publish_view(internal.messages[0])
        assert item.text is None
        assert item.masked_excerpt is None
        assert item.links == ()

    def test_bot_alert_may_be_quoted(self):
        ch = parse_export(channel("주요공시 알리미", [NOTICE_ENTITIES]))
        item = publish_view(ch.messages[0])
        assert item.text is not None
        assert item.needs_summary is False

    def test_verbatim_leak_is_caught(self):
        """요약기가 문장을 그대로 옮기는 일은 실제로 일어난다. G0처럼 막는다."""
        long_post = "조선 업황은 2026년 하반기에도 신조선가 강세가 이어질 것으로 보이며 수주 잔고가 견조하다"
        ch = parse_export(channel("DAOL 조선/기계/방산 | 최광식", [long_post]))
        clean = "다올 최광식 위원은 조선 업황을 긍정적으로 봤습니다. (원문 링크)"
        assert find_verbatim_leaks(clean, ch.messages) == []
        dirty = f"요약: {long_post}"
        leaks = find_verbatim_leaks(dirty, ch.messages)
        assert len(leaks) == 1
        assert leaks[0].chat_name == "DAOL 조선/기계/방산 | 최광식"

    def test_leak_check_ignores_whitespace_reflow(self):
        """줄바꿈만 바꿔 옮긴 것도 베낀 것이다."""
        long_post = "조선 업황은 2026년 하반기에도 신조선가 강세가 이어질 것으로 보인다"
        ch = parse_export(channel("Pluto Research", [long_post]))
        reflowed = long_post.replace(" ", "\n")
        assert find_verbatim_leaks(reflowed, ch.messages) != []

    def test_quotable_messages_are_not_flagged(self):
        ch = parse_export(channel("주요공시 알리미", [NOTICE_ENTITIES]))
        assert find_verbatim_leaks("투자판단관련주요경영사항 " * 3, ch.messages) == []


# ── ⑦ 실측 고정 ─────────────────────────────────────────────────────


def _listed_names() -> dict[str, str]:
    """`corpus/**/*.csv`에 실린 실제 상장사 이름 → 종목코드."""
    names: dict[str, str] = {}
    for path in glob.glob(str(ROOT / "corpus" / "**" / "*.csv"), recursive=True):
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            cols = reader.fieldnames or []
            name_col = next((c for c in cols if c in ("company", "dart_name")), None)
            code_col = next((c for c in cols if c in ("code", "symbol")), None)
            if not name_col or not code_col:
                continue
            for row in reader:
                name = (row.get(name_col) or "").strip()
                code = (row.get(code_col) or "").strip()
                if name and re.fullmatch(r"\d{6}", code):
                    names.setdefault(name, code)
    return names


def _titles() -> list[tuple[str, str]]:
    """리포트 제목 + 라벨. **제목의 종목코드 표기는 지운다** — 이름으로 맞혀야 한다."""
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for name in (
        "market/stock_reports_3names",
        "market/award_winner_reports",
        "consensus/labeled_clean",
    ):
        path = ROOT / "corpus" / f"{name}.csv"
        if not path.exists():
            continue
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                title = (row.get("title") or "").strip()
                code = (row.get("code") or "").strip()
                if not title or not re.fullmatch(r"\d{6}", code):
                    continue
                clean = re.sub(r"\(?\b\d{6}\b\)?", " ", title)
                if (clean, code) in seen:
                    continue
                seen.add((clean, code))
                out.append((clean, code))
    return out


class TestMeasuredExtraction:
    """방어를 느슨하게 하면 **여기가 깨진다.**

    측정에 쓴 코퍼스는 저장소에 있는 것뿐이다 (상장사 1,130종 · 리포트 제목
    7,077건 · `docs/*.md` 14만 자). DART corpCode 전량이 아니므로 **실제
    오탐은 더 난다** — 이 표는 하한이다.
    """

    def test_corpora_exist(self):
        assert len(_listed_names()) > 1000
        assert len(_titles()) > 5000

    def test_recall_stays_near_perfect(self):
        """상용어 목록의 값은 **14건**이고 그 14건이 무엇인지도 안다."""
        index = NameIndex.from_names(_listed_names())
        titles = _titles()
        missed = [
            (t, c) for t, c in titles if c not in {m.symbol for m in extract_mentions(t, index)}
        ]
        assert len(missed) == 14
        # 놓친 것은 전부 상용어 목록에 든 회사 자신에 관한 리포트다
        assert {index.by_code[c] for _t, c in missed} <= COMMON_WORD_NAMES

    def test_defenses_reduce_extras_monotonically(self):
        """① 부분 문자열 → ② 경계·최장 → ③ 상용어 목록. 매 단계가 덤을 줄인다."""
        index = NameIndex.from_names(_listed_names())
        titles = _titles()

        def extras(**kw) -> int:
            total = 0
            for title, code in titles:
                total += len({m.symbol for m in extract_mentions(title, index, **kw)} - {code})
            return total

        naive = extras(check_boundary=False, min_evidence_for_risky=Evidence.NAME)
        bounded = extras(check_boundary=True, min_evidence_for_risky=Evidence.NAME)
        guarded = extras()
        assert (naive, bounded, guarded) == (156, 52, 47)

    def test_common_word_names_are_silent_in_ordinary_prose(self):
        """일반 산문에서 상용어 이름이 회사로 잡히면 0건이어야 한다.

        `docs/*.md`는 계속 자라므로 총 매치 수는 고정하지 않는다. 고정하는
        것은 **상용어 이름의 매치가 0**이라는 사실이다 — 경계 규칙만 켠
        상태에서는 실측 19건이었다.
        """
        index = NameIndex.from_names(_listed_names())
        prose = [p.read_text(encoding="utf-8") for p in sorted((ROOT / "docs").glob("*.md"))]
        risky_hits = sum(
            1 for text in prose for m in extract_mentions(text, index) if m.surface in index.risky
        )
        assert risky_hits == 0
        loose = sum(
            1
            for text in prose
            for m in extract_mentions(text, index, min_evidence_for_risky=Evidence.NAME)
            if m.surface in index.risky
        )
        assert loose > 0  # 방어가 실제로 일을 하고 있다는 증거
