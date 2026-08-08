"""대화 저장소 (D82) — **리퀘스트 이력이 브라우저를 떠난다.**

여기서 지키는 것은 셋이다:

  1. **불변식 1.** 저장된 답에는 렌더된 숫자가 없다. 플레이스홀더거나 가린
     것뿐이고, 가리지 않은 텍스트는 애초에 안 받는다. 이게 깨지면 저장된
     대화가 나중에 맥락으로 조립될 때 LLM이 값을 본다
  2. **두 저장소가 같은 모양이다.** 파일과 Postgres를 갈아끼워도 부르는 쪽이
     안 바뀐다
  3. **없어도 돈다.** `DATABASE_URL`이 없으면 파일로 떨어진다 — 테스트와
     로컬 개발이 DB를 요구하면 안 된다

DB 테스트는 `ARC_TEST_DATABASE_URL`로 **따로 켠다** (`test_pg.py`와 같은
이유). `DATABASE_URL`을 그대로 쓰면 개발자 기계에 DB가 꽂혀 있다는 이유만으로
무엇을 검사하는지가 기계마다 달라진다.
"""

from __future__ import annotations

import json
import os

import pytest
from starlette.testclient import TestClient
from tests.test_ask_api import _Fake, _seed

from arc.store import pg
from arc.store.chats import (
    MAX_TURNS,
    ChatStore,
    PgChatStore,
    Session,
    Turn,
    build_turn,
    open_chats,
)
from arc.store.events import SAFE_MASKED, SAFE_NONE, SAFE_PLACEHOLDER


@pytest.fixture
def store(tmp_path) -> ChatStore:
    return ChatStore(tmp_path)


class TestInvariant:
    """**이게 이 파일에서 가장 중요한 것이다.**

    대화 답변에는 렌더된 숫자가 들어 있다. 그대로 남겨 두면 맥락 조립을 타고
    프롬프트에 닿고, 그 순간 이 제품의 전제가 무너진다.
    """

    def test_the_question_is_always_masked(self):
        """사람이 친 것이라 무엇이 들었는지 모른다."""
        turn = build_turn("매출 5,363억원이 맞나요?", text="네.")
        assert "5,363" not in turn.question
        assert "⟨수치⟩" in turn.question

    def test_a_placeholder_answer_is_kept_as_is(self):
        """조립본에서 온 것은 **구조적으로** 값이 없다 — 가릴 필요가 없다."""
        turn = build_turn(
            "영업이익률?",
            template="영업이익률은 {{num:c1.op_margin}}이다 [c1].",
            text="영업이익률은 6.1%이다 [c1].",
            numbers={"c1.op_margin": "6.1%"},
        )
        assert turn.safe == SAFE_PLACEHOLDER
        assert "{{num:c1.op_margin}}" in turn.answer
        assert "6.1%" not in turn.answer

    def test_an_answer_without_a_template_is_masked(self):
        """거부·근거 없음처럼 **조립본이 아닌 문장**이 여기로 온다.

        그때는 애초에 숫자가 없지만, 「없을 것이다」에 기대지 않고 가린다.
        """
        turn = build_turn("얼마야?", text="근거를 못 찾았습니다. 매출 5,363억원은 확인 불가입니다.")
        assert turn.safe == SAFE_MASKED
        assert "5,363" not in turn.answer

    def test_unmasked_text_is_refused(self, store):
        """**가리지 않은 텍스트는 애초에 안 받는다.**

        여기서 통과시키면 언젠가 프롬프트에 닿고 불변식 1이 조용히 깨진다.
        """
        session = store.create()
        raw = Turn(question="q", answer="매출 5,363억원", safe=SAFE_NONE)
        assert store.add_turn(session.id, raw) is False
        assert store.get(session.id).turns == []

    def test_pg_refuses_unmasked_text_too(self):
        """불변식 1은 저장소를 안 가린다 — DB 없이도 거부가 먼저 돈다."""
        raw = Turn(question="q", answer="매출 5,363억원", safe=SAFE_NONE)
        assert PgChatStore("alice").add_turn("0123456789abcdef", raw) is False

    def test_the_value_lives_in_a_table_not_in_the_text(self):
        """값은 **자유 텍스트가 아니라 키 붙은 표**에 있다.

        Number Registry가 하는 일과 같은 모양이다 — 본문에는 키만, 값은 옆에.
        프롬프트로 조립되는 것은 본문뿐이라 표에 값이 있어도 LLM에 닿지 않는다.
        """
        turn = build_turn(
            "영업이익률?",
            template="영업이익률은 {{num:c1.op_margin}}이다.",
            numbers={"c1.op_margin": "6.1%"},
        )
        assert turn.numbers == {"c1.op_margin": "6.1%"}
        assert turn.rendered() == "영업이익률은 6.1%이다."

    def test_rendering_fixes_the_particle(self):
        """치환하면서 조사를 고친다 — 안 하면 다시 연 대화에서만 "6.1%으로"가 된다."""
        turn = build_turn(
            "얼마",
            template="{{num:c1.op_margin}}으로 나왔다.",
            numbers={"c1.op_margin": "6.1%"},
        )
        assert turn.rendered() == "6.1%로 나왔다."

    def test_an_unknown_key_stays_a_placeholder(self):
        """값을 모르면 **지어내지 않는다** — 레지스트리와 같은 규칙이다."""
        turn = build_turn("얼마", template="{{num:c1.gone}}이다.", numbers={"c1.other": "1%"})
        assert "{{num:c1.gone}}" in turn.rendered()


class TestFileStore:
    def test_a_session_round_trips(self, store):
        session = store.create()
        assert store.add_turn(
            session.id,
            build_turn(
                "현대로템 영업이익률?", template="{{num:c1.m}}이다.", numbers={"c1.m": "6.1%"}
            ),
            symbols=["064350"],
            year=2026,
        )
        got = store.get(session.id)
        assert [t.rendered() for t in got.turns] == ["6.1%이다."]
        assert got.symbols == ["064350"]
        assert got.year == 2026

    def test_the_first_question_names_the_session(self, store):
        """**목록에서 리퀘스트를 알아보는 유일한 단서다.** 따로 짓게 하지 않는다."""
        session = store.create()
        store.add_turn(session.id, build_turn("한화오션 수주잔고 어떻게 됐어?", text="…"))
        store.add_turn(session.id, build_turn("그럼 작년은?", text="…"))
        assert store.get(session.id).title.startswith("한화오션 수주잔고")

    def test_the_list_leaves_the_body_out(self, store):
        """목록 화면이 쓰는 것은 제목과 턴 수뿐이다."""
        session = store.create()
        store.add_turn(session.id, build_turn("질문", text="답"))
        (only,) = store.list()
        assert only.turns == []
        assert only.turn_count == 1

    def test_the_list_is_newest_first(self, store):
        first = store.create("먼저")
        second = store.create("나중")
        store.add_turn(second.id, build_turn("질문", text="답"))
        listed = [s.id for s in store.list()]
        assert listed[0] == second.id
        assert first.id in listed

    def test_deleting_removes_it(self, store):
        session = store.create()
        assert store.delete(session.id) is True
        assert store.get(session.id) is None
        assert store.delete(session.id) is False

    def test_a_turn_on_a_missing_session_is_refused(self, store):
        assert store.add_turn("0123456789abcdef", build_turn("q", text="a")) is False

    def test_a_forged_id_never_becomes_a_path(self, store):
        """**경로 조작을 막는다.** id는 우리가 만든 16자리 hex만 받는다."""
        with pytest.raises(ValueError, match="잘못된 대화 id"):
            store.get("../../../etc/passwd")

    def test_a_forged_id_does_not_kill_the_answer(self, store):
        """턴 기록은 답변의 부산물이라 여기서는 예외가 올라가면 안 된다."""
        assert store.add_turn("../evil", build_turn("q", text="a")) is False

    def test_a_broken_file_does_not_block_the_list(self, store):
        """깨진 파일 하나가 목록 전체를 막지 않는다 — 카드와 같은 규칙이다."""
        good = store.create("멀쩡한 것")
        (store.dir / "ffffffffffffffff.json").write_text("{깨짐", encoding="utf-8")
        assert [s.id for s in store.list()] == [good.id]

    def test_unknown_fields_do_not_kill_it(self, store):
        """필드를 추가하기 **전에** 저장된 대화 (D65). 카드에서 두 번 밟았다."""
        session = store.create()
        path = store.dir / f"{session.id}.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["나중에생긴필드"] = 1
        raw["turns"] = [{"question": "q", "answer": "a", "safe": SAFE_MASKED, "옛필드": 2}]
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        got = store.get(session.id)
        assert got.id == session.id
        assert [t.question for t in got.turns] == ["q"]

    def test_a_session_stops_growing_forever(self, store):
        """「세션 하나 = 리퀘스트 하나」인데 200턴이면 이미 다른 것이 섞였다."""
        session = store.create()
        loaded = Session(
            id=session.id,
            title="긴 것",
            turns=[Turn(question="q", answer="a", safe=SAFE_MASKED)] * MAX_TURNS,
        )
        store._write(loaded)
        assert store.add_turn(session.id, build_turn("하나 더", text="…")) is False


class TestSameInterface:
    """**두 저장소가 같은 모양이어야 한다.** 아니면 갈아끼우는 순간 깨진다."""

    def test_both_have_the_same_methods(self):
        wanted = {"create", "add_turn", "get", "list", "delete"}
        assert wanted <= set(dir(ChatStore))
        assert wanted <= set(dir(PgChatStore))

    def test_pg_binds_the_uid_at_construction(self):
        """**uid를 인자로 받는 메서드가 없다.**

        경로로 가르던 미덕을 여기서 이어받는다 — 거르는 것을 빠뜨릴 수가
        없어야 한다.
        """
        import inspect

        store = PgChatStore("alice-1111")
        assert store.uid == "alice-1111"
        for name in ("create", "add_turn", "get", "list", "delete"):
            params = inspect.signature(getattr(store, name)).parameters
            assert "uid" not in params, name

    def test_both_refuse_the_same_forged_id(self):
        """같은 요청이 양쪽에서 같은 응답을 받아야 한다.

        Postgres 판에는 경로가 없어서 id 검증을 뺄 뻔했다 — 그러면 한쪽은
        400, 다른 쪽은 404가 되고 「같은 인터페이스」가 조용히 거짓이 된다.
        """
        with pytest.raises(ValueError):
            PgChatStore("alice").get("../../etc/passwd")
        with pytest.raises(ValueError):
            PgChatStore("alice").delete("nope")


class TestFallback:
    def test_no_url_means_files(self, tmp_path, monkeypatch):
        """**없어도 돈다.** 이게 이 설계의 전제다."""
        monkeypatch.setenv("DATABASE_URL", "")
        assert isinstance(open_chats(tmp_path, "alice"), ChatStore)

    def test_the_schema_carries_both_tables(self):
        """스키마에 표 둘과 **정책 둘**이 다 있어야 한다.

        세션에만 정책을 걸고 턴을 열어 두면 세션 id만 알면 남의 질문과 답을
        읽는다. 외래키는 무결성이지 접근 제어가 아니다.
        """
        for table in ("arc_chat_sessions", "arc_chat_turns"):
            assert f"create table if not exists {table}" in pg.SCHEMA
            assert f"grant select, insert, update, delete on {table} to authenticated" in pg.SCHEMA
            assert f"create policy {table}_own on {table}" in pg.POLICIES

    def test_the_existing_tables_are_untouched(self):
        """**추가만 한다.** 사건 로그·프로필·카드는 이미 돌고 있다."""
        for table in ("arc_events", "arc_profiles", "arc_cards"):
            assert f"create table if not exists {table}" in pg.SCHEMA
            assert f"create policy {table}_own on {table}" in pg.POLICIES

    def test_the_export_carries_the_chat_too(self):
        """**리전을 옮길 때 대화가 빠지면 안 된다.**

        DB에만 있는 것이라 파일에 원본이 없다. 턴은 세션을 외래키로 물으므로
        **세션 뒤**여야 한다 — 순서가 뒤집히면 들여올 때 전부 거절된다.
        """
        assert {"arc_chat_sessions", "arc_chat_turns"} <= set(pg.TABLES)
        assert pg.TABLES.index("arc_chat_sessions") < pg.TABLES.index("arc_chat_turns")


# ── 웹 경계 ──────────────────────────────────────────────────────────
@pytest.fixture
def client(tmp_path, monkeypatch):
    from arc.web import app as web

    monkeypatch.setattr(web, "STORE_DIR", tmp_path / "store")
    monkeypatch.setattr(web, "DRAFTS_DIR", tmp_path / "drafts")
    # **기사 레인은 기본으로 끈다.** 켜면 이 파일이 네트워크를 탄다.
    monkeypatch.setattr(web, "news_available", lambda: False)
    monkeypatch.setattr(web.LLM_BUDGET, "_used", 0, raising=False)
    return TestClient(web.app)


@pytest.fixture
def fake(monkeypatch):
    stub = _Fake()
    monkeypatch.setattr("arc.llm.client.get_client", lambda: stub)
    return stub


class TestApi:
    def test_a_session_is_created_and_listed(self, client):
        created = client.post("/api/chats", json={}).json()
        assert created["turn_count"] == 0
        assert [s["id"] for s in client.get("/api/chats").json()["sessions"]] == [created["id"]]

    def test_asking_writes_a_turn(self, client, fake):
        """**대화가 서버에 남는다.** 이게 D82의 전부다."""
        _seed(client)
        session = client.post("/api/chats", json={}).json()
        client.post(
            "/api/ask",
            json={"question": "현대로템 영업이익률", "session_id": session["id"]},
        )
        body = client.get(f"/api/chats/{session['id']}").json()
        assert len(body["turns"]) == 1
        assert body["turns"][0]["question"] == "현대로템 영업이익률"
        # 다음 질문의 앵커도 같이 남는다
        assert "064350" in body["context"]["symbols"]

    def test_what_is_stored_has_no_rendered_number(self, client, fake):
        """**불변식 1은 웹을 거쳐도 그대로다.**

        화면에는 값이 보이지만(경계에서 끼운다), 저장된 본문에는 없다.
        """
        from arc.web.app import _chats

        _seed(client)
        session = client.post("/api/chats", json={}).json()
        client.post(
            "/api/ask",
            json={"question": "현대로템 영업이익률", "session_id": session["id"]},
        )
        (turn,) = _chats().get(session["id"]).turns
        assert turn.safe == SAFE_PLACEHOLDER
        assert "{{num:" in turn.answer
        assert "6.1%" not in turn.answer
        # 화면이 받는 것은 값이 끼워진 쪽이다
        assert "6.1%" in client.get(f"/api/chats/{session['id']}").json()["turns"][0]["answer"]

    def test_asking_without_a_session_still_answers(self, client, fake):
        """`session_id` 없이도 채팅이 그대로 돌아야 한다 — 저장은 부산물이다."""
        _seed(client)
        r = client.post("/api/ask", json={"question": "현대로템 영업이익률"})
        assert r.status_code == 200
        assert client.get("/api/chats").json()["sessions"] == []

    def test_a_dead_session_id_does_not_break_the_answer(self, client, fake):
        """기록 실패가 답을 막지 않는다."""
        _seed(client)
        r = client.post(
            "/api/ask",
            json={"question": "현대로템 영업이익률", "session_id": "0123456789abcdef"},
        )
        assert r.status_code == 200
        assert r.json()["grounded"] is True

    def test_deleting_a_session(self, client):
        session = client.post("/api/chats", json={}).json()
        assert client.delete(f"/api/chats/{session['id']}").status_code == 200
        assert client.get(f"/api/chats/{session['id']}").status_code == 404

    def test_a_missing_session_is_404(self, client):
        assert client.get("/api/chats/0123456789abcdef").status_code == 404

    def test_a_forged_id_is_400_not_500(self, client):
        """id는 우리가 만든 16자리 hex만 받는다 — 경로가 될 값이라서다.

        `..%2F..`는 라우터가 먼저 정규화해 여기까지 안 온다. 검증이 실제로
        도는지 보려면 **라우팅되는** 잘못된 id를 써야 한다.
        """
        assert client.get("/api/chats/not-a-hex-id").status_code == 400
        assert client.delete("/api/chats/not-a-hex-id").status_code == 400


# **DB 테스트는 따로 켠다.** `DATABASE_URL`을 그대로 쓰면 개발자 기계에 DB가
# 꽂혀 있다는 이유만으로 무엇을 검사하는지가 기계마다 달라진다. 이 변수는
# conftest가 안 지운다 — 켜는 것이 명시적인 선택이다.
#
#     ARC_TEST_DATABASE_URL="$DATABASE_URL" pytest tests/test_chats.py
_TEST_DB = os.environ.get("ARC_TEST_DATABASE_URL", "")
needs_db = pytest.mark.skipif(not _TEST_DB, reason="ARC_TEST_DATABASE_URL이 없습니다")


@needs_db
class TestAgainstRealDatabase:
    """실제 DB가 있을 때만. **없으면 통째로 건너뛴다.**"""

    @pytest.fixture(autouse=True)
    def _url(self, monkeypatch):
        """conftest가 비워 둔 `DATABASE_URL`을 이 클래스에서만 되살린다."""
        monkeypatch.setenv("DATABASE_URL", _TEST_DB)
        pg.init_schema()

    def test_roundtrip(self):
        store = PgChatStore("test-chat-roundtrip")
        session = store.create()
        assert store.add_turn(
            session.id,
            build_turn("영업이익률?", template="{{num:c1.m}}이다.", numbers={"c1.m": "6.1%"}),
            symbols=["064350"],
            year=2026,
        )
        got = store.get(session.id)
        assert [t.rendered() for t in got.turns] == ["6.1%이다."]
        assert got.symbols == ["064350"]
        assert got.year == 2026
        assert got.title.startswith("영업이익률")

    def test_another_uid_does_not_see_it(self):
        """**남의 대화가 안 보인다.** 이게 옮기면서 지켜야 할 성질이다."""
        mine = PgChatStore("test-chat-alice").create("앨리스의 리퀘스트")
        assert PgChatStore("test-chat-bob").get(mine.id) is None
        assert mine.id not in [s.id for s in PgChatStore("test-chat-bob").list()]

    def test_another_uid_cannot_append_to_it(self):
        """**세션 id를 알아도 못 붙인다.** 외래키가 uid까지 같이 문다."""
        mine = PgChatStore("test-chat-alice2").create()
        assert PgChatStore("test-chat-bob2").add_turn(mine.id, build_turn("q", text="a")) is False

    def test_forging_another_uid_is_refused(self):
        """**남의 uid로 못 쓴다.** 앱이 실수해도 DB가 막는다."""
        import psycopg

        with pytest.raises(psycopg.Error), pg.connect("test-chat-alice3") as conn:
            conn.execute(
                "insert into arc_chat_sessions (uid, id, title) values (%s,%s,%s)",
                ("test-chat-bob3", "ffffffffffffffff", "남의 것"),
            )

    def test_deleting_takes_the_turns_with_it(self):
        """지우다 만 대화를 안 남긴다 — `on delete cascade`."""
        store = PgChatStore("test-chat-delete")
        session = store.create()
        store.add_turn(session.id, build_turn("q", text="a"))
        assert store.delete(session.id) is True
        assert store.get(session.id) is None

    def test_the_list_counts_turns_without_the_body(self):
        store = PgChatStore("test-chat-list")
        session = store.create()
        store.add_turn(session.id, build_turn("질문", text="답"))
        (row,) = [s for s in store.list() if s.id == session.id]
        assert row.turns == []
        assert row.turn_count == 1

    def test_rls_is_actually_enforced(self):
        """「켜져 있는가」가 아니라 **「지켜지는가」**를 묻는다."""
        assert pg.rls_enforced() is True


@needs_db
class TestMigration:
    @pytest.fixture(autouse=True)
    def _url(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", _TEST_DB)
        pg.init_schema()

    def test_it_copies_and_keeps_the_original(self, tmp_path, monkeypatch):
        """**복사한다, 지우지 않는다.** 이 저장소의 카드를 이미 두 번 잃었다."""
        from arc.store.chats import migrate_chats

        monkeypatch.setenv("DATABASE_URL", "")  # 파일 판으로 먼저 쌓는다
        source = ChatStore(tmp_path)
        session = source.create()
        source.add_turn(session.id, build_turn("현대로템 영업이익률?", text="답"))

        monkeypatch.setenv("DATABASE_URL", _TEST_DB)
        assert migrate_chats(tmp_path, "test-chat-migrate") == 1
        # **id를 그대로 들고 간다** — 새로 발급하면 화면이 들고 있던 세션이 죽는다
        moved = PgChatStore("test-chat-migrate").get(session.id)
        assert moved is not None
        assert len(moved.turns) == 1
        assert source.get(session.id) is not None  # 원본은 그대로
