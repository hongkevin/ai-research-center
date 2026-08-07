"""저장소의 사용자 축 — **로그인이 뜻을 갖게 하는 자리.**

`auth.py`가 토큰에서 `sub`를 꺼내 두고 있었지만 아무 데서도 안 썼다.
`CardStore(STORE_DIR)`가 전역이라 RA 둘이 로그인하면 **같은 보드를 봤다.**

여기가 지키는 것은 하나다: **남의 카드가 안 보인다.** 그리고 그건 거르기가
아니라 경로로 지켜져야 한다 — 거르는 코드를 한 군데 빠뜨리면 조용히 샌다.
"""

from __future__ import annotations

import json

from arc.store.cards import Card, CardStore
from arc.web.identity import (
    PERSONAL,
    SOLO,
    current_user,
    migrate_legacy,
    reset_current_user,
    safe_uid,
    set_current_user,
    user_dir,
)


class TestUid:
    def test_a_supabase_sub_passes_through(self):
        uid = "3f2a1b7c-9d4e-4f10-8a2b-1c3d5e7f9a0b"
        assert safe_uid(uid) == uid

    def test_path_traversal_falls_back_to_solo(self):
        """uid가 곧 디렉터리 이름이 된다."""
        for bad in ("../../etc", "a/b", "..", "", "  ", "x" * 200):
            assert safe_uid(bad) == SOLO

    def test_a_weird_token_does_not_kill_the_screen(self):
        """거부하지 않고 SOLO로 떨어뜨린다 — 인증은 이미 auth.py가 했다."""
        assert safe_uid("한글아이디") == SOLO


class TestIsolation:
    def test_two_people_do_not_see_each_other(self, tmp_path):
        a = CardStore(user_dir(tmp_path, "alice"))
        b = CardStore(user_dir(tmp_path, "bob"))
        a.save(Card(id=a.new_id(), symbol="005930", year=2025))
        assert len(a.list()) == 1
        assert b.list() == []

    def test_the_split_is_a_path_not_a_filter(self, tmp_path):
        """**경로가 다르면 빠뜨릴 수가 없다** — 애초에 남의 디렉터리를 안 연다."""
        assert user_dir(tmp_path, "alice") != user_dir(tmp_path, "bob")
        assert user_dir(tmp_path, "alice").is_dir()

    def test_market_data_stays_out_of_the_user_dir(self, tmp_path):
        """시세·corpCode 캐시는 누구의 것도 아니다."""
        assert "prices" not in PERSONAL
        assert "cache" not in PERSONAL
        mine = user_dir(tmp_path, "alice")
        assert (tmp_path / "prices") not in mine.parents


class TestCurrent:
    def test_default_is_solo(self):
        assert current_user() == SOLO

    def test_set_and_reset(self):
        token = set_current_user("alice")
        try:
            assert current_user() == "alice"
        finally:
            reset_current_user(token)
        assert current_user() == SOLO

    def test_user_dir_follows_the_current_user(self, tmp_path):
        token = set_current_user("alice")
        try:
            assert user_dir(tmp_path).name == "alice"
        finally:
            reset_current_user(token)


class TestMigration:
    def _legacy_card(self, tmp_path) -> None:
        cards = tmp_path / "cards"
        cards.mkdir(parents=True)
        (cards / ("a" * 16 + ".json")).write_text(
            json.dumps({"id": "a" * 16, "symbol": "064350", "year": 2026}), encoding="utf-8"
        )

    def test_legacy_cards_become_visible_to_the_solo_user(self, tmp_path):
        self._legacy_card(tmp_path)
        assert migrate_legacy(tmp_path) == ["cards"]
        assert len(CardStore(user_dir(tmp_path, SOLO)).list()) == 1

    def test_the_original_is_left_alone(self, tmp_path):
        """**옮기지 않고 복사한다.** 이 저장소의 카드를 이미 두 번 잃었다."""
        self._legacy_card(tmp_path)
        migrate_legacy(tmp_path)
        assert (tmp_path / "cards" / ("a" * 16 + ".json")).exists()

    def test_it_does_not_run_twice(self, tmp_path):
        """이미 옮겨 놓은 것을 덮어쓰면 사람이 지운 카드가 되살아난다."""
        self._legacy_card(tmp_path)
        migrate_legacy(tmp_path)
        store = CardStore(user_dir(tmp_path, SOLO))
        store.delete("a" * 16)
        assert migrate_legacy(tmp_path) == []
        assert store.list() == []

    def test_nothing_to_migrate_is_not_an_error(self, tmp_path):
        assert migrate_legacy(tmp_path) == []

    def test_it_only_touches_the_solo_user(self, tmp_path):
        """남의 계정에 내 옛 카드를 심지 않는다."""
        self._legacy_card(tmp_path)
        migrate_legacy(tmp_path)
        assert CardStore(user_dir(tmp_path, "alice")).list() == []


class TestOverHttp:
    """**진짜로 안 보이는가.** 단위 테스트가 아니라 요청으로 확인한다.

    격리는 「거르기를 다 했는가」가 아니라 「경로가 갈렸는가」로 지켜지는데,
    그게 실제 미들웨어를 거쳐서도 성립하는지는 여기서만 알 수 있다.
    """

    SECRET = "test-jwt-secret-do-not-use-0123456789abcdef"

    def _headers(self, sub: str) -> dict:
        import datetime as dt

        import jwt

        token = jwt.encode(
            {
                "sub": sub,
                "aud": "authenticated",
                "exp": dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
            },
            self.SECRET,
            algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}"}

    def _client(self, tmp_path, monkeypatch):
        """**앱을 감싼다, 갈아끼우지 않는다.**

        미들웨어는 만들어질 때 시크릿을 읽는데 모듈 수준 앱은 이미 시작돼
        있어 다시 끼울 수 없다(`Cannot add middleware after an application has
        started`). 새 미들웨어로 감싸면 안쪽 것은 시크릿이 없어 그냥 통과하고
        바깥 것이 검증한다 — 검사 대상은 어차피 바깥이다.
        """
        from starlette.testclient import TestClient

        from arc.web import app as web
        from arc.web.auth import BasicAuthMiddleware

        monkeypatch.setenv("SUPABASE_JWT_SECRET", self.SECRET)
        monkeypatch.setattr(web, "STORE_DIR", tmp_path / "store")
        return TestClient(BasicAuthMiddleware(web.app))

    def test_a_board_is_not_shared(self, tmp_path, monkeypatch):
        client = self._client(tmp_path, monkeypatch)
        alice, bob = self._headers("alice-1111"), self._headers("bob-2222")

        made = client.post(
            "/api/peers", json={"name": "방산", "symbols": ["047810", "012450"]}, headers=alice
        )
        assert made.status_code == 200
        assert len(client.get("/api/cards", headers=alice).json()["cards"]) == 1
        assert client.get("/api/cards", headers=bob).json()["cards"] == []

    def test_knowing_the_id_is_not_enough(self, tmp_path, monkeypatch):
        """**id를 알아도 못 연다.** 남의 디렉터리를 애초에 열지 않기 때문이다."""
        client = self._client(tmp_path, monkeypatch)
        alice, bob = self._headers("alice-1111"), self._headers("bob-2222")
        cid = client.post(
            "/api/peers", json={"name": "방산", "symbols": ["047810"]}, headers=alice
        ).json()["card_id"]
        assert client.get(f"/api/cards/{cid}", headers=alice).status_code == 200
        assert client.get(f"/api/cards/{cid}", headers=bob).status_code == 404

    def test_no_token_no_board(self, tmp_path, monkeypatch):
        client = self._client(tmp_path, monkeypatch)
        assert client.get("/api/cards").status_code == 401
