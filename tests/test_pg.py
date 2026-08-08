"""Postgres 저장소 (D80).

**DB가 없으면 건너뛴다.** 테스트 1,189건이 지금 DB 없이 도는데, 그 성질을
잃으면 안 된다 — CI에도 로컬에도 Postgres를 요구하게 되면 그때부터 아무도
전체를 안 돌린다.

여기서 지키는 것:

  1. `DATABASE_URL`이 없으면 **파일로 떨어진다** — 조용히 죽지 않는다
  2. 키는 있는데 드라이버가 없으면 **경고한다** — 설정 실수를 늦게 알면 안 된다
  3. 두 저장소가 **같은 인터페이스**를 갖는다 — 부르는 쪽이 어느 쪽인지 모른다
"""

from __future__ import annotations

import os

import pytest

from arc.store import pg
from arc.store.events import OPENED, EventStore, PgEventStore, open_events


class TestFallback:
    def test_no_url_means_files(self, tmp_path, monkeypatch):
        """**없어도 돈다.** 이게 이 설계의 전제다."""
        monkeypatch.setenv("DATABASE_URL", "")
        assert pg.available() is False
        assert isinstance(open_events(tmp_path, "alice"), EventStore)

    def test_a_url_without_the_driver_warns(self, monkeypatch, caplog):
        """**조용히 파일로 떨어지면 안 된다.**

        키를 넣어 놓고 DB에 안 쌓이는 것을 한참 뒤에 알게 된다. 드라이버가
        없는 것은 설정 실수지 「파일로 돌아라」가 아니다.
        """
        import builtins

        real = builtins.__import__

        def no_psycopg(name, *a, **k):
            if name == "psycopg":
                raise ImportError("없는 셈 치자")
            return real(name, *a, **k)

        monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
        monkeypatch.setattr(builtins, "__import__", no_psycopg)
        with caplog.at_level("WARNING", logger="arc.store.pg"):
            assert pg.available() is False
        assert any("psycopg" in r.message for r in caplog.records)

    def test_the_url_is_trimmed(self, monkeypatch):
        """`.env`에 공백이 붙어 오는 일이 실제로 있다."""
        monkeypatch.setenv("DATABASE_URL", "  postgresql://a/b  ")
        assert pg.database_url() == "postgresql://a/b"


class TestSameInterface:
    """**두 저장소가 같은 모양이어야 한다.** 아니면 갈아끼우는 순간 깨진다."""

    def test_both_have_the_same_methods(self):
        wanted = {"record", "note", "note_text", "note_draft", "read"}
        assert wanted <= set(dir(EventStore))
        assert wanted <= set(dir(PgEventStore))

    def test_pg_binds_the_uid_at_construction(self):
        """**uid를 인자로 받는 메서드가 없다.**

        경로로 가르던 미덕을 여기서 이어받는다 — 거르는 것을 빠뜨릴 수가
        없어야 한다.
        """
        import inspect

        store = PgEventStore("alice-1111")
        assert store.uid == "alice-1111"
        for name in ("record", "note", "read"):
            params = inspect.signature(getattr(store, name)).parameters
            assert "uid" not in params, name

    def test_pg_refuses_unmasked_text_too(self):
        """불변식 1은 저장소를 안 가린다."""
        from arc.store.events import Event

        assert PgEventStore("a").record(Event(kind="edited", text="매출 5,363억원")) is False

    def test_pg_refuses_unknown_kinds_too(self):
        from arc.store.events import Event

        assert PgEventStore("a").record(Event(kind="opnened")) is False


# **DB 테스트는 따로 켠다.** `DATABASE_URL`을 그대로 쓰면 개발자 기계에 DB가
# 꽂혀 있다는 이유만으로 전체 실행이 느려지고(8초 → 40초), 무엇을 검사하는지가
# 기계마다 달라진다. 실제로 파일에 써 놓고 DB에서 읽는 상태가 되어 2건이
# 깨졌다. 이 변수는 conftest가 안 지운다 — 켜는 것이 명시적인 선택이다.
#
#     ARC_TEST_DATABASE_URL="$DATABASE_URL" pytest tests/test_pg.py
_TEST_DB = os.environ.get("ARC_TEST_DATABASE_URL", "")
needs_db = pytest.mark.skipif(not _TEST_DB, reason="ARC_TEST_DATABASE_URL이 없습니다")


@needs_db
class TestAgainstRealDatabase:
    """실제 DB가 있을 때만. **없으면 통째로 건너뛴다.**"""

    @pytest.fixture(autouse=True)
    def _url(self, monkeypatch):
        """conftest가 비워 둔 `DATABASE_URL`을 이 클래스에서만 되살린다."""
        monkeypatch.setenv("DATABASE_URL", _TEST_DB)

    def test_roundtrip(self):
        pg.init_schema()
        store = PgEventStore("test-uid-roundtrip")
        assert store.note(OPENED, "005930", card_id="c1")
        got = store.read(limit=5)
        assert got and got[0].subject == "005930"
        assert got[0].detail.get("card_id") == "c1"

    def test_another_uid_does_not_see_it(self):
        """**남의 기록이 안 보인다.** 이게 옮기면서 지켜야 할 성질이다."""
        pg.init_schema()
        PgEventStore("test-uid-alice").note(OPENED, "111111")
        seen = [e.subject for e in PgEventStore("test-uid-bob").read(limit=50)]
        assert "111111" not in seen

    def test_forging_another_uid_is_refused(self):
        """**남의 uid로 못 쓴다.** 앱이 실수해도 DB가 막는다."""
        import psycopg

        pg.init_schema()
        with pytest.raises(psycopg.Error), pg.connect("test-uid-alice") as conn:
            conn.execute(
                "insert into arc_events (uid, kind, subject) values (%s,%s,%s)",
                ("test-uid-bob", "opened", "999999"),
            )

    def test_rls_is_actually_enforced(self):
        """「켜져 있는가」가 아니라 **「지켜지는가」**를 묻는다.

        `postgres`는 BYPASSRLS를 갖고 있어 enable·force를 다 켜도 정책이 한
        줄도 안 돈다. 그래서 설정이 아니라 동작을 검사한다.
        """
        pg.init_schema()
        assert pg.rls_enforced() is True


class TestProfileInterface:
    """**두 프로필 저장소가 같은 모양이어야 한다.**"""

    def test_same_methods(self):
        from arc.store.profile import PgProfileStore, ProfileStore

        for name in ("load", "save"):
            assert hasattr(ProfileStore, name) and hasattr(PgProfileStore, name)

    def test_pg_ignores_the_uid_argument(self):
        """`load(uid)`는 파일 판과 모양을 맞추려고 받지만 **무시한다.**

        남의 프로필을 읽는 경로를 만들지 않는다.
        """
        from arc.store.profile import PgProfileStore

        assert PgProfileStore("alice").uid == "alice"

    def test_unknown_fields_do_not_kill_it(self):
        """필드를 추가하기 **전에** 저장된 문서 (D65). 카드에서 두 번 밟았다."""
        from arc.store.profile import _from_doc

        got = _from_doc({"sectors": ["조선"], "나중에생긴필드": 1}, "alice")
        assert got.sectors == ["조선"]
        assert got.uid == "alice"

    def test_unknown_stock_fields_are_dropped_too(self):
        from arc.store.profile import _from_doc

        got = _from_doc({"stocks": [{"symbol": "042660", "company": "한화오션", "옛필드": 1}]}, "a")
        assert [s.symbol for s in got.stocks] == ["042660"]


@needs_db
class TestProfileAgainstRealDatabase:
    @pytest.fixture(autouse=True)
    def _url(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", _TEST_DB)

    def test_roundtrip(self):
        from arc.store.profile import COVER, Covered, PgProfileStore, Profile

        pg.init_schema()
        store = PgProfileStore("test-profile-rt")
        store.save(
            Profile(
                uid="test-profile-rt",
                sectors=["조선"],
                stocks=[Covered(symbol="042660", company="한화오션", kind=COVER)],
            )
        )
        got = store.load()
        assert got.sectors == ["조선"]
        assert [s.symbol for s in got.stocks] == ["042660"]

    def test_absent_is_an_empty_profile_not_an_error(self):
        from arc.store.profile import PgProfileStore

        pg.init_schema()
        assert PgProfileStore("test-profile-nobody").load().sectors == []

    def test_another_uid_does_not_see_it(self):
        from arc.store.profile import PgProfileStore, Profile

        pg.init_schema()
        PgProfileStore("test-profile-alice").save(
            Profile(uid="test-profile-alice", sectors=["앨리스섹터"])
        )
        assert PgProfileStore("test-profile-bob").load().sectors == []

    def test_saving_is_loud_when_it_fails(self, monkeypatch):
        """**커버리지 저장 실패는 삼키면 안 된다.**

        사건 로그와 다른 점이다 — 고쳤는데 조용히 안 저장되면 사용자는
        저장된 줄 안다.
        """
        from arc.store import pg as pgmod
        from arc.store.profile import PgProfileStore, Profile

        def boom(_uid):
            raise RuntimeError("연결 끊김")

        monkeypatch.setattr(pgmod, "connect", boom)
        with pytest.raises(RuntimeError):
            PgProfileStore("x").save(Profile(uid="x"))
