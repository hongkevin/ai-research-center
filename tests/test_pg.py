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


needs_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL이 없습니다")


@needs_db
class TestAgainstRealDatabase:
    """실제 DB가 있을 때만. **없으면 통째로 건너뛴다.**"""

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
