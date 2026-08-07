"""섹터 시드 — **정답이 아니라 출발점.**

시드는 화면의 마찰을 없애려고 넣은 것이지 분류를 확정하려는 게 아니다. 그래서
검사할 것은 「이 분류가 맞나」가 아니라 **「이 목록이 스스로 주장하는 근거를
지키고 있나」**다:

  1. 응집도가 무작위(0.102)보다 유의하게 높다 — 이게 목록의 유일한 근거다
  2. 종목코드가 실재하는 보통주 꼴이다 (우선주가 섞이면 D68에서 겪은 오염)
  3. 이름과 종목 수가 짝이 맞는다 — 어긋나면 화면이 엉뚱한 회사명을 붙인다
"""

from __future__ import annotations

import pytest

from arc.data.sectors import SEEDS, seed_for

RANDOM_BASELINE = 0.102


class TestSeeds:
    def test_every_seed_beats_random(self):
        """**무작위보다 못 묶이는 섹터는 시드가 아니다.**

        무작위 8종목의 시장 요인 제거 후 내부 상관이 0.102다. 이보다 낮으면
        「같이 움직인다」는 주장이 성립하지 않는다.
        """
        weak = [(s.name, s.cohesion) for s in SEEDS if s.cohesion < RANDOM_BASELINE * 2]
        assert weak == [], f"무작위의 2배도 안 되는 시드: {weak}"

    def test_names_and_companies_line_up(self):
        """종목 수와 회사명 수가 어긋나면 화면이 엉뚱한 이름을 붙인다."""
        for s in SEEDS:
            assert len(s.symbols) == len(s.companies), s.name

    def test_only_common_shares(self):
        """**우선주가 섞이면 안 된다.**

        상관만 보고 뽑으면 삼성전자우가 삼성전자와 0.86으로 올라온다 —
        피어가 아니라 같은 회사다. 보통주는 코드가 `0`으로 끝난다.
        """
        bad = [
            (s.name, sym)
            for s in SEEDS
            for sym in s.symbols
            if not (len(sym) == 6 and sym.isdigit() and sym.endswith("0"))
        ]
        assert bad == [], f"보통주가 아닌 코드: {bad}"

    def test_no_duplicate_names(self):
        names = [s.name for s in SEEDS]
        assert len(names) == len(set(names))

    def test_a_seed_is_a_group_not_a_pair(self):
        """둘짜리는 피어지 섹터가 아니다 — 화면이 「섹터」라고 부른다."""
        for s in SEEDS:
            assert len(s.symbols) >= 3, s.name

    @pytest.mark.parametrize("name", ["없는섹터", "", "  "])
    def test_unknown_name_is_none(self, name):
        assert seed_for(name) is None

    def test_lookup_finds_what_the_list_holds(self):
        for s in SEEDS:
            assert seed_for(s.name) is s


class TestEndpoint:
    """**프로필을 읽지 않는다.**

    무엇을 이미 넣었는지는 화면이 자기 상태로 안다. 서버가 겹쳐 판단하면
    저장 전 편집과 어긋난다 — 시드를 고르는 것으로는 저장되지 않기 때문이다.
    """

    def _client(self, tmp_path, monkeypatch):
        from starlette.testclient import TestClient

        from arc.web import app as web

        monkeypatch.setattr(web, "STORE_DIR", tmp_path / "store")
        return TestClient(web.app)

    def test_seed_list_carries_its_own_evidence(self, tmp_path, monkeypatch):
        got = self._client(tmp_path, monkeypatch).get("/api/sectors/seed")
        assert got.status_code == 200
        body = got.json()

        assert body["random_baseline"] == RANDOM_BASELINE
        assert len(body["seeds"]) == len(SEEDS)
        # 응집도가 빠지면 화면이 「우리가 정한 분류」로 읽힌다
        assert all(s["cohesion"] > 0 for s in body["seeds"])
        assert all(s["companies"] and s["symbols"] for s in body["seeds"])

    def test_no_taken_flag(self, tmp_path, monkeypatch):
        body = self._client(tmp_path, monkeypatch).get("/api/sectors/seed").json()
        assert "taken" not in body["seeds"][0]
