"""피어 후보 — 업종 분류 대신 「같이 움직이는가」로 찾는다.

KSIC를 버린 근거는 `peer_suggest.py` 독스트링에 실측으로 적혀 있다. 여기서는
상관이 **어떤 거짓말을 하지 않는지**를 지킨다.
"""

from __future__ import annotations

import json
import math

from arc.finmodel.peer_suggest import MIN_OVERLAP, Candidate, load_prices, suggest


def _series(returns: list[float], start: float = 1000.0) -> dict[str, float]:
    """일간 로그수익률 목록에서 종가 시계열을 만든다.

    날짜는 실제 달력이 아니어도 된다 — `suggest()`는 정렬과 교집합만 쓴다.
    """
    out: dict[str, float] = {}
    price = start
    for i, r in enumerate(returns):
        out[f"{20260000 + i + 1}"] = price
        price *= math.exp(r)
    return out


def _wave(n: int, phase: float = 0.0, amp: float = 0.02) -> list[float]:
    return [amp * math.sin(i / 5 + phase) for i in range(n)]


N = MIN_OVERLAP + 40


class TestSuggest:
    def test_a_mover_together_ranks_above_an_unrelated_one(self):
        base = _wave(N)
        prices = {
            "SEED": _series(base),
            "TWIN": _series([r * 0.9 for r in base]),  # 거의 같이 움직인다
            "OTHER": _series(_wave(N, phase=1.7)),  # 위상이 다르다
        }
        got = suggest(["SEED"], prices, top=5)
        assert got[0].symbol == "TWIN"
        assert got[0].correlation > 0.9

    def test_the_seed_is_not_its_own_peer(self):
        prices = {"SEED": _series(_wave(N)), "TWIN": _series(_wave(N))}
        assert "SEED" not in [c.symbol for c in suggest(["SEED"], prices)]

    def test_excluded_symbols_are_dropped(self):
        """ETF·우선주처럼 회사가 아닌 것을 부르는 쪽에서 뺀다."""
        prices = {"SEED": _series(_wave(N)), "ETF": _series(_wave(N))}
        assert suggest(["SEED"], prices, exclude={"ETF"}) == []

    def test_too_little_overlap_is_dropped_not_zeroed(self):
        """0.0으로 목록 끝에 세우면 「상관 없음」과 「모름」이 섞인다."""
        short = _series(_wave(MIN_OVERLAP - 20))
        prices = {"SEED": _series(_wave(N)), "NEW": short}
        assert [c.symbol for c in suggest(["SEED"], prices)] == []

    def test_a_halted_stock_does_not_divide_by_zero(self):
        """거래정지로 종가가 붙박이면 분산이 0이다."""
        prices = {"SEED": _series(_wave(N)), "HALT": _series([0.0] * N)}
        assert suggest(["SEED"], prices) == []  # 상관 0 — 분산이 없어 못 낸다

    def test_two_seeds_average_their_correlations(self):
        base = _wave(N)
        prices = {
            "S1": _series(base),
            "S2": _series([r * 0.95 for r in base]),
            "TWIN": _series([r * 0.9 for r in base]),
            "OTHER": _series(_wave(N, phase=1.7)),
        }
        got = suggest(["S1", "S2"], prices, top=5)
        # 씨앗 둘은 후보에서 빠지고, 같이 움직이는 쪽이 위에 선다
        assert [c.symbol for c in got] == ["TWIN", "OTHER"]
        assert got[0].correlation > got[1].correlation
        assert 0 < got[0].correlation <= 1

    def test_a_candidate_missing_one_seed_is_dropped(self):
        """씨앗 전부와 견줄 수 있어야 평균이 뜻을 가진다."""
        base = _wave(N)
        prices = {
            "S1": _series(base),
            "S2": _series(base),
            "PARTIAL": _series(_wave(MIN_OVERLAP - 20)),
        }
        assert suggest(["S1", "S2"], prices) == []

    def test_unknown_seed_yields_nothing(self):
        assert suggest(["NOPE"], {"A": _series(_wave(N))}) == []

    def test_overlap_is_reported(self):
        """**몇 일치로 본 것인가**를 후보마다 들고 있어야 한다."""
        prices = {"SEED": _series(_wave(N)), "TWIN": _series(_wave(N))}
        got = suggest(["SEED"], prices, top=1)
        assert got[0].overlap >= MIN_OVERLAP

    def test_no_sector_label_is_invented(self):
        """상관은 산업이 아니라 **지금 같이 움직이는 테마**를 찾는다.

        현대건설 씨앗이 원전 테마를 물어 오는 것이 실측된 동작이다. 여기에
        섹터 이름을 붙이면 「같은 산업」이라는 거짓 약속이 된다.
        """
        prices = {"SEED": _series(_wave(N)), "TWIN": _series(_wave(N))}
        c = suggest(["SEED"], prices, top=1)[0]
        assert set(vars(c)) == {"symbol", "correlation", "overlap", "company"}
        assert c.company == ""  # 이름은 부르는 쪽이 채운다. 섹터는 아무도 안 채운다


class TestLoad:
    def test_reads_the_corpus_shape(self, tmp_path):
        (tmp_path / "005930.json").write_text(
            json.dumps({"20260102": 70000, "20260103": 71000}), encoding="utf-8"
        )
        prices = load_prices(tmp_path)
        assert prices["005930"]["20260103"] == 71000.0

    def test_a_broken_file_does_not_stop_the_load(self, tmp_path):
        (tmp_path / "000660.json").write_text("{oops", encoding="utf-8")
        (tmp_path / "005930.json").write_text(json.dumps({"20260102": 1}), encoding="utf-8")
        assert list(load_prices(tmp_path)) == ["005930"]

    def test_empty_series_is_skipped(self, tmp_path):
        (tmp_path / "000660.json").write_text("{}", encoding="utf-8")
        assert load_prices(tmp_path) == {}


def test_candidate_is_plain_data():
    c = Candidate(symbol="079550", correlation=0.647, overlap=250)
    assert c.symbol == "079550"
    assert c.company == ""
