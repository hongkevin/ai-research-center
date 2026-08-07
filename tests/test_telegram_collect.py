"""Telethon 수집기 — **수집기를 갈아끼워도 그 뒤가 안 바뀐다.**

이 파일이 지키는 것 하나: Telethon 메시지가 **내보내기 JSON과 같은 모양**으로
나온다. 그래야 `parse_messages()`·`parse_export()`가 그대로 돌고, D66의
「수집기 교체 가능」이 말뿐이 아니게 된다.
"""

from __future__ import annotations

import datetime as dt

import pytest

from arc.ingest.telegram_collect import (
    Fetched,
    credentials,
    session_path,
    to_export_dict,
)
from arc.ingest.telegram_parse import parse_export


class _Ent:
    """Telethon 엔티티 대역. 클래스 이름으로 종류를 가른다."""

    def __init__(self, offset: int, length: int, url: str = ""):
        self.offset = offset
        self.length = length
        if url:
            self.url = url


class MessageEntityBold(_Ent):
    pass


class MessageEntityTextUrl(_Ent):
    pass


class _Msg:
    def __init__(self, text: str, *, entities=None, mid: int = 1, sender=None):
        self.id = mid
        self.message = text
        self.entities = entities or []
        self.date = dt.datetime(2026, 8, 7, 3, 41, 20, tzinfo=dt.UTC)
        self.edit_date = None
        self.chat_id = 2073492571
        self.sender_id = sender
        self.reply_to_msg_id = None
        self.forward = None


class TestShape:
    def test_it_looks_like_an_export_message(self):
        d = to_export_dict(_Msg("안녕하세요"), chat_name="AWAKE")
        for key in ("id", "type", "date", "date_unixtime", "text", "text_entities"):
            assert key in d, key
        assert d["type"] == "message"

    def test_unix_time_is_the_source_of_truth(self):
        """내보내기의 `date`는 내보낸 기계의 로컬 시각이라 채널마다 어긋난다."""
        d = to_export_dict(_Msg("x"), chat_name="c")
        assert int(d["date_unixtime"]) == int(
            dt.datetime(2026, 8, 7, 3, 41, 20, tzinfo=dt.UTC).timestamp()
        )

    def test_a_channel_post_is_from_the_channel(self):
        d = to_export_dict(_Msg("x"), chat_name="c")
        assert d["from_id"].startswith("channel")

    def test_a_human_post_is_from_a_user(self):
        """내부 대화방을 가르는 신호다 — `user…`가 여럿이면 채널이 아니다."""
        d = to_export_dict(_Msg("x", sender=777), chat_name="c")
        assert d["from_id"] == "user777"


class TestEntities:
    def test_plain_text_becomes_one_entity(self):
        d = to_export_dict(_Msg("공시가 났습니다"), chat_name="c")
        assert d["text_entities"] == [{"type": "plain", "text": "공시가 났습니다"}]

    def test_a_link_keeps_its_href(self):
        """**봇 채널의 공시 원문 링크가 여기 있다.** 버리면 되짚을 수 없다."""
        text = "공시 원문 보기"
        msg = _Msg(text, entities=[MessageEntityTextUrl(3, 5, url="https://dart.fss.or.kr/x")])
        got = to_export_dict(msg, chat_name="c")["text_entities"]
        link = next(e for e in got if e["type"] == "text_link")
        assert link["href"] == "https://dart.fss.or.kr/x"
        assert "".join(e["text"] for e in got) == text

    def test_offsets_survive_emoji(self):
        """**Telethon 오프셋은 UTF-16 코드 유닛이다.** 이모지가 하나만 있어도
        코드포인트로 자르면 자리가 밀린다."""
        text = "✅ 안국약품 신고가"
        msg = _Msg(text, entities=[MessageEntityBold(2, 4)])
        got = to_export_dict(msg, chat_name="c")["text_entities"]
        bold = next(e for e in got if e["type"] == "bold")
        assert bold["text"] == "안국약품"
        assert "".join(e["text"] for e in got) == text

    def test_an_empty_message_has_no_entities(self):
        assert to_export_dict(_Msg(""), chat_name="c")["text_entities"] == []


class TestRoundTrip:
    def test_the_parser_reads_it_unchanged(self):
        """**이게 이 파일의 존재 이유다.** 수집기를 바꿔도 파서가 그대로 돈다."""
        messages = [
            to_export_dict(
                _Msg("2026.08.07 12:41:20 기업명: 신성이엔지(011930)", mid=1),
                chat_name="AWAKE - 실시간 주식 공시 정리",
            ),
            to_export_dict(
                _Msg("✅ 안국약품(+2.31%) 신고가", mid=2), chat_name="AWAKE - 실시간 주식 공시 정리"
            ),
        ]
        channel = parse_export(
            Fetched(
                chat_id=2073492571,
                name="AWAKE - 실시간 주식 공시 정리",
                chat_type="public_channel",
                messages=messages,
            ).as_export()
        )
        assert len(channel.messages) == 2
        assert channel.messages[0].text.startswith("2026.08.07")
        # 분류까지 그대로 붙는다 — 봇 채널로 읽힌다
        assert channel.kind.value == "bot_feed"

    def test_a_deeplink_is_buildable(self):
        got = parse_export(
            Fetched(
                chat_id=2073492571,
                name="테스트",
                chat_type="private_channel",
                messages=[to_export_dict(_Msg("본문", mid=14), chat_name="테스트")],
            ).as_export()
        )
        assert got.messages[0].permalink.endswith("/2073492571/14")


class TestCredentials:
    def test_it_refuses_without_keys(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
        monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
        with pytest.raises(ValueError, match="my.telegram.org"):
            credentials()

    def test_a_bot_token_shape_is_not_accepted(self, monkeypatch):
        """봇은 남의 채널에 못 들어가고 히스토리도 못 읽는다 (D66)."""
        monkeypatch.setenv("TELEGRAM_API_ID", "123:ABC")
        monkeypatch.setenv("TELEGRAM_API_HASH", "x")
        with pytest.raises(ValueError):
            credentials()

    def test_it_reads_the_pair(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_API_ID", "12345")
        monkeypatch.setenv("TELEGRAM_API_HASH", "deadbeef")
        assert credentials() == (12345, "deadbeef")


def test_the_session_lives_beside_the_cards(tmp_path):
    """세션 파일은 **계정 그 자체다.** 사람별 디렉터리 안이어야 한다."""
    assert session_path(tmp_path).parent == tmp_path
