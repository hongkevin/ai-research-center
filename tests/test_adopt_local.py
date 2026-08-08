"""로그인을 켜는 날 (D76).

인증을 켜기 전에 만든 커버리지·카드·채널이 `users/local/`에 있다. 로그인 뒤
uid가 바뀌면 그 전부가 안 보이게 되는데, **사용자에게는 사라진 것과 같다.**

지키는 것 셋:

  1. 한 번만 넘긴다 — 두 번째 사람은 빈 저장소로 시작한다
  2. **자격증명은 안 넘긴다** — 텔레그램 세션은 계정 접근 권한이다
  3. 복사한다, 옮기지 않는다 — 잘못돼도 되돌릴 수 있어야 한다
"""

from __future__ import annotations

from arc.web.identity import CREDENTIALS, adopt_local, claimed_by


def _local(base, *, session: bool = True):
    """로그인 전 저장물을 흉내 낸다."""
    src = base / "users" / "local"
    (src / "cards").mkdir(parents=True)
    (src / "cards" / "abc.json").write_text('{"id":"abc"}', encoding="utf-8")
    (src / "profile.json").write_text('{"sectors":["조선"]}', encoding="utf-8")
    (src / "telegram").mkdir()
    (src / "telegram" / "-100.json").write_text("{}", encoding="utf-8")
    if session:
        (src / "telegram.session").write_bytes(b"SQLite format 3\x00")
    return src


class TestAdopt:
    def test_the_first_person_gets_it(self, tmp_path):
        _local(tmp_path)
        took = adopt_local(tmp_path, "alice-1111")
        assert "cards" in took and "profile.json" in took

        mine = tmp_path / "users" / "alice-1111"
        assert (mine / "cards" / "abc.json").read_text() == '{"id":"abc"}'
        assert (mine / "telegram" / "-100.json").exists()

    def test_credentials_never_follow(self, tmp_path):
        """**텔레그램 세션은 데이터가 아니라 계정 접근 권한이다.**

        따라 옮기면 공유 배포에서 처음 로그인한 사람이 남의 텔레그램 계정을
        쥔다. `arc telegram login`을 한 번 다시 하는 마찰이 그보다 싸다.
        """
        _local(tmp_path)
        took = adopt_local(tmp_path, "alice-1111")
        assert "telegram.session" not in took
        assert not (tmp_path / "users" / "alice-1111" / "telegram.session").exists()
        # 원본은 그대로 — local로 돌아가면 여전히 로그인돼 있다
        assert (tmp_path / "users" / "local" / "telegram.session").exists()

    def test_credential_list_is_not_empty(self):
        """빈 목록이면 이 방어가 조용히 없어진다."""
        assert "telegram.session" in CREDENTIALS

    def test_the_original_survives(self, tmp_path):
        """**복사한다, 옮기지 않는다.** 이 저장소의 카드를 이미 두 번 잃었다."""
        src = _local(tmp_path)
        adopt_local(tmp_path, "alice-1111")
        assert (src / "cards" / "abc.json").exists()
        assert (src / "profile.json").exists()

    def test_only_once(self, tmp_path):
        """두 번째 사람은 **빈 저장소로 시작한다.**"""
        _local(tmp_path)
        assert adopt_local(tmp_path, "alice-1111")
        assert adopt_local(tmp_path, "bob-2222") == []
        assert not (tmp_path / "users" / "bob-2222" / "cards").exists()

    def test_it_records_who_took_it(self, tmp_path):
        _local(tmp_path)
        adopt_local(tmp_path, "alice-1111")
        assert claimed_by(tmp_path) == "alice-1111"

    def test_nothing_claimed_yet_says_so(self, tmp_path):
        _local(tmp_path)
        assert claimed_by(tmp_path) == ""

    def test_existing_data_is_never_overwritten(self, tmp_path):
        """**이미 자기 것이 있으면 그게 맞다.**"""
        _local(tmp_path)
        mine = tmp_path / "users" / "alice-1111" / "cards"
        mine.mkdir(parents=True)
        (mine / "own.json").write_text("mine", encoding="utf-8")

        adopt_local(tmp_path, "alice-1111")
        assert (mine / "own.json").read_text() == "mine"
        assert not (mine / "abc.json").exists()  # 통째로 안 건드렸다

    def test_local_does_not_adopt_itself(self, tmp_path):
        _local(tmp_path)
        assert adopt_local(tmp_path, "local") == []
        assert claimed_by(tmp_path) == ""

    def test_no_local_store_is_not_an_error(self, tmp_path):
        assert adopt_local(tmp_path, "alice-1111") == []

    def test_a_bad_uid_falls_back_and_does_nothing(self, tmp_path):
        """경로 조작이 들어오면 `SOLO`로 떨어지고, SOLO는 자기를 안 가져간다."""
        _local(tmp_path)
        assert adopt_local(tmp_path, "../../etc") == []
        assert not (tmp_path / "etc").exists()


class TestWhoMayAdopt:
    """**「먼저 로그인한 사람」에 맡기지 않는다.**

    배포본이 인증 없이 돌던 동안 `users/local/`에 쌓였을 수 있다 —
    `current_user()`가 `local`이었기 때문이다. 그것을 남이 가져가면 안 된다.
    """

    def test_off_by_default_words(self):
        from arc.web.identity import may_adopt

        for off in ("", "  ", "0", "false", "no", "off", "FALSE"):
            assert may_adopt(off, "alice-1111", "a@b.com") is False, off

    def test_a_uid_lets_only_that_person(self):
        from arc.web.identity import may_adopt

        assert may_adopt("alice-1111", "alice-1111") is True
        assert may_adopt("alice-1111", "bob-2222") is False

    def test_an_email_lets_only_that_person(self):
        """**UUID는 로그인해 보기 전에 모른다.** 이메일로도 지정돼야 한다."""
        from arc.web.identity import may_adopt

        assert may_adopt("me@example.com", "any-uid", "me@example.com") is True
        assert may_adopt("me@example.com", "any-uid", "other@example.com") is False

    def test_email_ignores_case(self):
        from arc.web.identity import may_adopt

        assert may_adopt("Me@Example.COM", "u", "me@example.com") is True

    def test_one_still_means_anyone(self):
        """하위 호환. 로컬 개발에서는 로그인하는 사람이 한 명이라 이게 편하다."""
        from arc.web.identity import may_adopt

        assert may_adopt("1", "whoever") is True
        assert may_adopt("true", "whoever") is True

    def test_a_wrong_name_does_not_fall_open(self):
        """오타를 「누구든」으로 읽으면 안 된다 — 그게 이 스위치의 존재 이유다."""
        from arc.web.identity import may_adopt

        assert may_adopt("alice-1111", "", "") is False
        assert may_adopt("yess", "alice-1111", "a@b.com") is False
