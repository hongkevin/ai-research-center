"""카드 API — 특히 **발간**.

발간 버튼이 초안 작성 폼에 있었다. 아직 아무것도 안 만든 상태에서 「검토 완료」를
누를 수 있었고, 누르면 생성 직후의 원본이 그대로 나갔다 — 읽지도 고치지도 않은
글이다. 발간은 읽고 고친 **뒤에** 하는 일이라 카드로 옮겼다.

옮기면서 지켜야 할 것이 둘이다:

* 발간되는 것은 **카드의 현재 본문**이어야 한다. 코멘트로 고친 것이 살아서 나가지
  않으면 리뷰 루프가 무의미하다.
* 점검을 통과하지 못한 초안은 발간되지 않는다.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest
from starlette.testclient import TestClient

from arc.data.base import Provenance
from arc.llm.number_registry import NumberEntry, NumberRegistry
from arc.store.cards import PUBLISHED, REVIEW, Card, CardStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    from arc.web import app as web

    monkeypatch.setattr(web, "STORE_DIR", tmp_path / "store")
    monkeypatch.setattr(web, "DRAFTS_DIR", tmp_path / "drafts")
    return TestClient(web.app)


def _registry() -> NumberRegistry:
    reg = NumberRegistry()
    reg.register(
        NumberEntry(
            key="rev_2025a",
            value=1234.0,
            unit="억원",
            display="1,234억원",
            label="매출액 (2025)",
            provenance=Provenance(
                source="opendart",
                retrieved_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC),
            ),
        )
    )
    return reg


def _card(store: CardStore, *, gate_passed: bool = True, body: str = "") -> Card:
    reg = _registry()
    card = Card(
        id=store.new_id(),
        symbol="214450",
        year=2025,
        column=REVIEW,
        company="(주)파마리서치",
        vm={"gate_passed": gate_passed},
        assembled=body or "## 실적 요약\n\n매출액은 {{num:rev_2025a}}이다.\n",
        registry=reg.dump(),
    )
    store.save(card)
    return card


class TestPublish:
    def test_renders_the_cards_current_body(self, client, tmp_path):
        """**고친 것이 살아서 나간다.** 다시 생성하지 않는다."""
        store = CardStore(tmp_path / "store")
        card = _card(store, body="## 실적 요약\n\n코멘트로 고친 문장. {{num:rev_2025a}}.\n")

        r = client.post(f"/api/cards/{card.id}/publish")
        assert r.status_code == 200

        text = (tmp_path / "drafts").joinpath(r.json()["published_path"].split("/")[-1])
        assert "코멘트로 고친 문장" in text.read_text(encoding="utf-8")

    def test_substitutes_placeholders(self, client, tmp_path):
        """플레이스홀더가 남은 채 나가면 독자에게 `{{num:…}}`이 보인다."""
        store = CardStore(tmp_path / "store")
        card = _card(store)

        r = client.post(f"/api/cards/{card.id}/publish")
        text = (tmp_path / "drafts" / r.json()["published_path"].split("/")[-1]).read_text(
            encoding="utf-8"
        )
        assert "1,234억원" in text
        assert "{{num:" not in text

    def test_moves_the_card_to_published(self, client, tmp_path):
        store = CardStore(tmp_path / "store")
        card = _card(store)

        client.post(f"/api/cards/{card.id}/publish")

        after = store.get(card.id)
        assert after is not None
        assert after.column == PUBLISHED
        assert after.published_path.endswith(".md")

    def test_refuses_when_the_gate_did_not_pass(self, client, tmp_path):
        """점검을 통과하지 못한 초안은 나가지 않는다."""
        store = CardStore(tmp_path / "store")
        card = _card(store, gate_passed=False)

        r = client.post(f"/api/cards/{card.id}/publish")
        assert r.status_code == 409
        assert not (tmp_path / "drafts").exists()
        assert store.get(card.id).column == REVIEW

    def test_missing_card_is_404(self, client):
        assert client.post("/api/cards/" + "0" * 16 + "/publish").status_code == 404

    def test_rejects_an_id_that_is_not_ours(self, client):
        """id는 우리가 만든 16자리 hex만 받는다 — 경로가 되면 안 된다."""
        assert client.post("/api/cards/....etc.passwd/publish").status_code == 400

    def test_snapshots_the_estimate(self, client, tmp_path):
        """발간해야 추정이 이력에 남는다 — 다음 발간의 변화 추적 기준이다 (D27)."""
        store = CardStore(tmp_path / "store")
        card = _card(store)
        card.estimate_snapshot = {
            "fiscal_year": 2026,
            "base_year": 2025,
            "method": "직전 2개년 평균 성장률",
            "values": {"revenue": 1500.0, "operating_income": 300.0},
        }
        store.save(card)

        assert client.post(f"/api/cards/{card.id}/publish").status_code == 200

        from arc.finmodel.estimates import ESTIMATE_DATASET
        from arc.store.snapshot import SnapshotStore

        rows = SnapshotStore(tmp_path / "store").read_as_of(ESTIMATE_DATASET)
        assert {r["metric"] for r in rows} == {"revenue", "operating_income"}

    def test_publishing_twice_is_idempotent_per_day(self, client, tmp_path):
        """같은 날 두 번 눌러도 파일이 늘지 않는다 — 날짜가 파일 이름이다."""
        store = CardStore(tmp_path / "store")
        card = _card(store)

        first = client.post(f"/api/cards/{card.id}/publish").json()["published_path"]
        second = client.post(f"/api/cards/{card.id}/publish").json()["published_path"]

        assert first == second
        assert len(list((tmp_path / "drafts").glob("*.md"))) == 1


class TestCardList:
    def test_list_omits_the_body(self, client, tmp_path):
        """카드 하나에 60KB가 붙어 있다. 목록이 그걸 다 실으면 보드가 느려진다."""
        store = CardStore(tmp_path / "store")
        _card(store)

        rows = client.get("/api/cards").json()["cards"]
        assert len(rows) == 1
        assert "vm" not in json.dumps(rows[0])


class TestViewModelCompleteness:
    """**서버가 항상 완전한 모양을 준다** (D65).

    카드는 만들어진 시점의 `vm`을 그대로 들고 있다. 그 뒤에 필드를 추가하면
    옛 카드에는 그 필드가 없고, 화면이 `vm.areas.length`를 읽다가 터져
    브라우저가 「This page couldn't load」를 띄운다 — 두 번 밟았다.

    필드를 추가할 때마다 화면에 `?.`를 하나씩 다는 것은 못 지킨다.
    """

    def test_old_card_gets_todays_fields(self, client, tmp_path):
        store = CardStore(tmp_path / "store")
        card = _card(store)
        # 필드가 몇 개뿐이던 시절의 카드
        card.vm = {"gate_passed": True, "body_html": "<p>옛 카드</p>"}
        store.save(card)

        vm = client.get(f"/api/cards/{card.id}").json()["vm"]
        for key in ("areas", "changes", "stages", "violations", "segment_items"):
            assert key in vm, key
        assert vm["areas"] == [] and vm["changes"] == []

    def test_stored_values_win_over_defaults(self):
        """채우는 것이지 덮어쓰는 것이 아니다."""
        vm = client_vm({"gate_passed": True, "changes": [{"name": "매출액"}]})
        assert vm["gate_passed"] is True
        assert vm["changes"] == [{"name": "매출액"}]

    def test_empty_vm_stays_empty(self):
        """생성 중인 카드의 빈 `vm`은 그대로 둔다 — 화면이 그걸로 「아직」을 안다."""
        assert client_vm({}) == {}


def client_vm(stored: dict) -> dict:
    from arc.web.app import _complete_vm

    return _complete_vm(stored)
