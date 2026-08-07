"""내 커버리지 — RA가 쌓아 가는 것.

이 파일이 지키는 것 둘:

* **사람이 정한 것만 쌓인다.** LLM 요약을 넣을 자리가 없어야 한다 — 있으면
  언젠가 들어가고, 그러면 출처 없는 것이 사실처럼 굳는다(불변식 1).
* **피어 그룹은 id로만 고정한다.** 내용을 복제하면 카드를 고친 뒤 프로필이
  옛말을 한다 (D51에서 이미 밟았다).
"""

from __future__ import annotations

import json

import pytest

from arc.store.profile import (
    COVER,
    MAX_SECTORS,
    MAX_STOCKS,
    WATCH,
    Covered,
    Profile,
    ProfileStore,
    TgChannel,
    add_stock,
    merge_channels,
    pin_peer,
    remove_stock,
    set_sectors,
    unpin_peer,
)


class TestWhatIsStored:
    def test_no_place_for_a_model_summary(self):
        """**빠뜨린 게 아니라 정한 것이다.**

        필요하면 그때 다시 만들면 된다 — 건당 $0.002고, 쌓아 두면 틀린 것이
        굳는다.
        """
        fields = set(Profile.__dataclass_fields__)
        # **전부 사람이 정한 것이다.** 커버 종목·섹터·고정한 피어 그룹·볼 채널 —
        # 출처가 본인이라 쌓아도 안전하다.
        assert fields == {
            "uid",
            "display_name",
            "sectors",
            "stocks",
            "pinned_peers",
            "channels",
            "updated_at",
        }
        for banned in ("summary", "notes_ai", "insights", "memory"):
            assert banned not in fields

    def test_a_stock_remembers_why_it_is_there(self):
        """코드만 모아 두면 몇 달 뒤에 「이건 왜 넣었지」가 된다."""
        c = Covered(symbol="064350", note="방산 비중 확대 확인용")
        assert c.note


class TestStocks:
    def test_add_and_read_back(self, tmp_path):
        store = ProfileStore(tmp_path)
        p = store.load("u1")
        add_stock(p, Covered(symbol="064350", company="현대로템", kind=COVER))
        add_stock(p, Covered(symbol="042660", company="한화오션", kind=WATCH))
        store.save(p)

        back = store.load("u1")
        assert back.symbols() == ["064350", "042660"]
        assert back.covering() == ["064350"]
        assert back.watching() == ["042660"]
        assert back.covers("064350") and not back.covers("005930")

    def test_cover_and_watch_are_the_axis(self):
        """**「발간 여부」가 아니다.** 커버 종목이면 리포트를 내는 것이 자명해서
        체크박스가 있을 이유가 없었다 — 갈리는 축은 내가 책임지느냐다."""
        p = Profile()
        add_stock(p, Covered(symbol="064350", kind=COVER))
        add_stock(p, Covered(symbol="042660", kind=WATCH))
        assert p.stocks[0].publishes is True
        assert p.stocks[1].publishes is False

    def test_an_unknown_kind_becomes_cover(self):
        p = add_stock(Profile(), Covered(symbol="064350", kind="아무거나"))
        assert p.stocks[0].kind == COVER

    def test_the_same_stock_does_not_go_in_twice(self):
        """두 번 들어가면 브리프가 같은 종목을 두 번 낸다."""
        p = Profile()
        add_stock(p, Covered(symbol="064350", company="현대로템", kind=WATCH))
        add_stock(p, Covered(symbol="064350", company="현대로템(주)", kind=COVER))
        assert p.symbols() == ["064350"]
        assert p.stocks[0].kind == COVER  # 나중 것이 이긴다

    def test_added_at_is_stamped_once(self):
        p = Profile()
        add_stock(p, Covered(symbol="064350"))
        first = p.stocks[0].added_at
        assert first
        add_stock(p, Covered(symbol="047810"))
        assert p.stocks[0].added_at == first

    def test_remove(self):
        p = Profile()
        add_stock(p, Covered(symbol="064350"))
        remove_stock(p, "064350")
        assert p.symbols() == []

    def test_there_is_a_ceiling(self):
        p = Profile()
        for i in range(MAX_STOCKS):
            add_stock(p, Covered(symbol=f"{i:06d}"))
        with pytest.raises(ValueError):
            add_stock(p, Covered(symbol="999999"))


class TestSectors:
    def test_order_is_kept_and_duplicates_dropped(self):
        p = Profile()
        set_sectors(p, ["방산", "조선", " 방산 ", "", "  "])
        assert p.sectors == ["방산", "조선"]

    def test_free_text_on_purpose(self):
        """**표준 분류로 못 적는다는 것이 D68의 결론이다.**

        방산 4종목이 KSIC 어느 자릿수에서도 한 그룹이 안 된다.
        """
        p = set_sectors(Profile(), ["방산·우주", "2차전지/소재"])
        assert p.sectors == ["방산·우주", "2차전지/소재"]

    def test_there_is_a_ceiling(self):
        with pytest.raises(ValueError):
            set_sectors(Profile(), [f"s{i}" for i in range(MAX_SECTORS + 1)])


class TestPinnedPeers:
    def test_only_the_id_is_kept(self):
        """내용을 복제하면 카드를 고친 뒤 프로필이 옛말을 한다."""
        p = pin_peer(Profile(), "abc123")
        assert p.pinned_peers == ["abc123"]
        assert all(isinstance(x, str) for x in p.pinned_peers)

    def test_pinning_twice_is_idempotent(self):
        p = pin_peer(pin_peer(Profile(), "abc123"), "abc123")
        assert p.pinned_peers == ["abc123"]

    def test_unpin(self):
        p = unpin_peer(pin_peer(Profile(), "abc123"), "abc123")
        assert p.pinned_peers == []

    def test_an_empty_id_is_ignored(self):
        assert pin_peer(Profile(), "").pinned_peers == []


class TestStore:
    def test_a_missing_profile_is_not_an_error(self, tmp_path):
        """없는 것은 오류가 아니다 — 처음 온 사람이 그 상태다."""
        p = ProfileStore(tmp_path).load("u1")
        assert p.empty and p.uid == "u1"

    def test_a_broken_file_falls_back_to_empty(self, tmp_path):
        (tmp_path / "profile.json").write_text("{깨짐", encoding="utf-8")
        assert ProfileStore(tmp_path).load("u1").empty

    def test_an_unknown_field_does_not_kill_it(self, tmp_path):
        """필드를 추가하기 **전에** 저장된 프로필. 카드에서 두 번 밟았다(D65)."""
        (tmp_path / "profile.json").write_text(
            json.dumps({"uid": "u1", "sectors": ["방산"], "무슨필드": 1}), encoding="utf-8"
        )
        p = ProfileStore(tmp_path).load("u1")
        assert p.sectors == ["방산"]

    def test_saving_stamps_the_time(self, tmp_path):
        store = ProfileStore(tmp_path)
        saved = store.save(Profile(uid="u1"))
        assert saved.updated_at

    def test_it_lives_beside_the_cards(self, tmp_path):
        """프로필만 따로 빼면 사용자 축이 두 군데가 된다."""
        assert ProfileStore(tmp_path).path.parent == tmp_path


class TestOldSchema:
    """**필드를 바꾸기 전에 저장된 것이 반드시 있다.**

    카드에서 두 번 밟았고(D65) 여기서 세 번째로 밟았다 — 「발간 여부」를
    「커버냐 관심이냐」로 고쳤더니 옛 프로필이 `TypeError`를 내고 화면이
    500이 됐다.
    """

    def _write(self, tmp_path, stock: dict) -> Profile:
        (tmp_path / "profile.json").write_text(
            json.dumps({"uid": "u1", "stocks": [stock]}), encoding="utf-8"
        )
        return ProfileStore(tmp_path).load("u1")

    def test_publishes_true_becomes_cover(self, tmp_path):
        p = self._write(tmp_path, {"symbol": "064350", "publishes": True})
        assert p.stocks[0].kind == COVER

    def test_publishes_false_becomes_watch(self, tmp_path):
        """뜻이 있으니 **옮겨 준다** — 버리면 사용자가 표시해 둔 것이 사라진다."""
        p = self._write(tmp_path, {"symbol": "042660", "publishes": False})
        assert p.stocks[0].kind == WATCH

    def test_an_unknown_field_is_dropped_not_fatal(self, tmp_path):
        p = self._write(tmp_path, {"symbol": "064350", "무슨필드": 1, "kind": COVER})
        assert p.stocks[0].symbol == "064350"
        assert p.stocks[0].kind == COVER

    def test_a_garbage_kind_falls_back_to_cover(self, tmp_path):
        p = self._write(tmp_path, {"symbol": "064350", "kind": "아무거나"})
        assert p.stocks[0].kind == COVER


class TestChannels:
    """볼 채널도 **커버 종목처럼 고른다.**

    다 긁으면 하루 3,000건이 쏟아지고 그중 대부분은 이미 DART·뉴스 API로
    갖고 있는 것이다(D66).
    """

    def test_nothing_is_on_by_default(self):
        """목록에 나타났다고 자동으로 긁으면 고르는 의미가 없다."""
        p = merge_channels(Profile(), [TgChannel(chat_id=1, name="A")])
        assert p.enabled_channels() == []

    def test_enabling_survives_a_refresh(self):
        """이름·구독자는 갱신하되 **사람이 켜 둔 것은 지킨다** — 안 그러면
        목록을 새로 받을 때마다 다시 골라야 한다."""
        p = merge_channels(Profile(), [TgChannel(chat_id=1, name="A")])
        p.channels[0].enabled = True
        merge_channels(p, [TgChannel(chat_id=1, name="A(개명)", subscribers=100)])
        assert p.enabled_channels() == [1]
        assert p.channels[0].name == "A(개명)"
        assert p.channels[0].subscribers == 100

    def test_a_channel_that_vanished_is_kept_if_it_was_on(self):
        """나갔거나 못 읽은 채널을 조용히 지우면 켜 둔 표시가 사라진다."""
        p = merge_channels(Profile(), [TgChannel(chat_id=1, name="A")])
        p.channels[0].enabled = True
        merge_channels(p, [TgChannel(chat_id=2, name="B")])
        assert 1 in p.enabled_channels()

    def test_a_channel_that_vanished_is_dropped_if_it_was_off(self):
        p = merge_channels(Profile(), [TgChannel(chat_id=1, name="A")])
        merge_channels(p, [TgChannel(chat_id=2, name="B")])
        assert [c.chat_id for c in p.channels] == [2]

    def test_trusted_is_broker_and_research(self):
        """추천 목록의 기준 — 증권사 공식·리서치가 위로 간다."""
        assert TgChannel(chat_id=1, kind="broker").trusted
        assert TgChannel(chat_id=1, kind="research").trusted
        assert not TgChannel(chat_id=1, kind="chatter").trusted
        assert not TgChannel(chat_id=1, kind="bot_feed").trusted

    def test_it_round_trips(self, tmp_path):
        store = ProfileStore(tmp_path)
        p = merge_channels(Profile(uid="u1"), [TgChannel(chat_id=-100123, name="A", kind="broker")])
        p.channels[0].enabled = True
        store.save(p)
        back = store.load("u1")
        assert back.enabled_channels() == [-100123]
        assert back.channels[0].kind == "broker"

    def test_an_old_profile_without_channels_is_fine(self, tmp_path):
        (tmp_path / "profile.json").write_text(
            json.dumps({"uid": "u1", "sectors": ["방산"]}), encoding="utf-8"
        )
        assert ProfileStore(tmp_path).load("u1").channels == []

    def test_a_big_channel_can_be_dead(self):
        """**구독자 수만 보면 시체를 잡는다.**

        실측: 박석중의 글로벌전략 20,437명 · 219일째 정지, wemakebull
        18,638명 · 943일째 정지. 규모는 남고 채널은 죽는다.
        """
        import datetime as dt

        today = dt.date(2026, 8, 7)
        dead = TgChannel(chat_id=1, subscribers=20437, last_post="2025-12-31")
        alive = TgChannel(chat_id=2, subscribers=1917, last_post="2026-08-07")
        assert dead.stale(today) is True
        assert alive.stale(today) is False

    def test_an_unknown_last_post_is_not_dead(self):
        """**모르는 것을 죽었다고 하지 않는다.**"""
        import datetime as dt

        assert TgChannel(chat_id=1).stale(dt.date(2026, 8, 7)) is False
        assert TgChannel(chat_id=1, last_post="깨진값").stale(dt.date(2026, 8, 7)) is False
