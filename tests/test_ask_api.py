"""`POST /api/ask` — 리서치 채팅의 웹 경계.

채팅 코어의 규칙은 `test_chat.py`가 지킨다. 여기가 지키는 것은 **경계**다:

* 답을 만들 때 **저장된 대화를 안 읽는다** — 화면이 `context`를 돌려주고
  우리는 그걸 다음 검색의 앵커로 쓴다. 대화는 D82에서 서버에 남기 시작했지만
  (`test_chats.py`), 그걸 다시 읽어 재구성하지는 않는다: 이 좁은 것만 이월하는
  편이 정확하고, `session_id` 없이도 채팅이 그대로 돌아야 한다.
* 피어 카드는 근거가 아니다 — 그쪽은 종목 카드를 **가리키기만** 한다.
* 실패해도 화면이 죽지 않는다.
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient
from tests.test_chat import DOC, REGISTRY

from arc.llm.client import Completion
from arc.store.cards import PEER, Card, CardStore, peer_member


@pytest.fixture
def client(tmp_path, monkeypatch):
    from arc.web import app as web

    monkeypatch.setattr(web, "STORE_DIR", tmp_path / "store")
    monkeypatch.setattr(web, "DRAFTS_DIR", tmp_path / "drafts")
    # **기사 레인은 기본으로 끈다.** 켜면 이 파일이 네트워크를 탄다.
    monkeypatch.setattr(web, "news_available", lambda: False)
    monkeypatch.setattr(web.LLM_BUDGET, "_used", 0, raising=False)
    return TestClient(web.app)


class _Fake:
    """LLM 대역. 검증 레인만 쓴다 — 힌트 레인은 위에서 꺼 뒀다."""

    def __init__(
        self, facts: str = "현대로템의 영업이익률은 {{num:c1.operating_margin_2026a}}이다 [c1]."
    ):
        self.facts = facts
        self.calls: list[str] = []

    def complete(self, *, system, user, tier=None, max_tokens=0):
        self.calls.append(user)
        return Completion(
            text=json.dumps(
                {"facts": self.facts, "analysis": "", "unanswered": []}, ensure_ascii=False
            ),
            model="fake-1",
            provider="fake",
            cost_usd=0.0002,
        )


@pytest.fixture
def fake(monkeypatch):
    stub = _Fake()
    monkeypatch.setattr("arc.llm.client.get_client", lambda: stub)
    return stub


def _store(client) -> CardStore:
    from arc.web.app import _open_cards

    store = _open_cards()
    assert store is not None
    return store


def _seed(client) -> Card:
    """실제 현대로템 카드(2026 1분기)를 그대로 심는다."""
    store = _store(client)
    card = Card(
        id=store.new_id(),
        symbol="064350",
        year=2026,
        period="Q1",
        created_at="2026-08-07T03:35:47+00:00",
        company="현대로템(주)",
        assembled=DOC,
        registry=[dict(r) for r in REGISTRY],
        vm={"gate_passed": True},
    )
    store.save(card)
    return card


class TestAsk:
    def test_a_question_is_answered_from_the_card(self, client, fake):
        _seed(client)
        r = client.post("/api/ask", json={"question": "현대로템 영업이익률 어떻게 됐어?"})
        assert r.status_code == 200
        body = r.json()
        assert body["grounded"] is True
        # **레지스트리에서 치환된 값이지 LLM이 쓴 숫자가 아니다.**
        assert "{{num:" not in body["facts"]
        assert body["sources"]

    def test_the_three_lanes_stay_apart(self, client, fake):
        """합치면 화면이 「미검증」 배지를 붙일 자리를 잃는다."""
        _seed(client)
        body = client.post("/api/ask", json={"question": "현대로템 영업이익률"}).json()
        for key in ("facts", "analysis", "hints", "text"):
            assert key in body
        # `text`는 검증 레인만 담는다
        assert body["text"] == "\n\n".join(p for p in (body["facts"], body["analysis"]) if p)

    def test_an_empty_question_is_refused(self, client, fake):
        assert client.post("/api/ask", json={"question": "   "}).status_code == 400

    def test_a_value_never_reaches_the_prompt(self, client, fake):
        """불변식 1은 웹을 거쳐도 그대로다."""
        _seed(client)
        client.post("/api/ask", json={"question": "현대로템 영업이익률"})
        assert fake.calls
        for sent in fake.calls:
            assert "15.4" not in sent
            assert "1,457" not in sent


class TestContext:
    """**서버는 대화를 안 들고 있다.** 화면이 돌려준다."""

    def test_the_answer_hands_back_what_to_carry(self, client, fake):
        _seed(client)
        body = client.post("/api/ask", json={"question": "현대로템 영업이익률"}).json()
        ctx = body["context"]
        assert set(ctx) == {"symbols", "tokens", "year"}
        assert "064350" in ctx["symbols"]

    def test_a_follow_up_carries_the_subject(self, client, fake):
        _seed(client)
        first = client.post("/api/ask", json={"question": "현대로템 영업이익률"}).json()
        second = client.post(
            "/api/ask", json={"question": "부문별로는?", "context": first["context"]}
        ).json()
        assert second["grounded"] is True
        # **이어받았으면 밝힌다** — 조용히 이어받으면 다른 회사를 생각한
        # 사용자에게 틀린 답을 확신 있게 하게 된다.
        assert second["carried_over"]

    def test_nothing_is_carried_without_a_context(self, client, fake):
        """대조군.

        「근거를 못 찾는다」로는 못 잰다 — 카드가 하나뿐이면 「부문별로는?」이
        맥락 없이도 그 카드의 「부문」에 걸린다(정상 동작이다). 재야 할 것은
        **이어받았는가**다.
        """
        _seed(client)
        body = client.post("/api/ask", json={"question": "부문별로는?"}).json()
        assert body["carried_over"] == []

    def test_a_malformed_context_does_not_500(self, client, fake):
        _seed(client)
        r = client.post(
            "/api/ask",
            json={"question": "현대로템 영업이익률", "context": {"symbols": None, "year": "작년"}},
        )
        assert r.status_code == 200


class TestBoundaries:
    def test_peer_cards_are_not_evidence(self, client, fake):
        """피어 카드는 숫자를 안 들고 있다 — 종목 카드를 가리킬 뿐이다."""
        store = _store(client)
        store.save(
            Card(
                id=store.new_id(),
                symbol="",
                year=0,
                kind=PEER,
                company="방산 4종",
                members=[peer_member("064350", company="현대로템")],
            )
        )
        body = client.post("/api/ask", json={"question": "현대로템 영업이익률"}).json()
        assert body["grounded"] is False  # 종목 카드가 없으니 답할 근거도 없다

    def test_the_budget_is_enforced(self, client, fake, monkeypatch):
        from arc.web import app as web

        _seed(client)
        monkeypatch.setattr(web.LLM_BUDGET, "take", lambda: False)
        r = client.post("/api/ask", json={"question": "현대로템 영업이익률"})
        assert r.status_code == 429

    def test_a_crash_does_not_take_the_screen_down(self, client, monkeypatch):
        _seed(client)

        def boom(*a, **k):
            raise RuntimeError("모델 응답 없음")

        monkeypatch.setattr("arc.web.app.answer_question", boom)
        r = client.post("/api/ask", json={"question": "현대로템 영업이익률"})
        assert r.status_code == 500
        assert "RuntimeError" in r.json()["error"]
