"""피어 후보 — 업종 분류 대신 「같이 움직이는가」로 찾는다.

KSIC를 버린 근거는 `peer_suggest.py` 독스트링에 실측으로 적혀 있다. 여기서는
상관이 **어떤 거짓말을 하지 않는지**를 지킨다.
"""

from __future__ import annotations

import json
import math

from arc.finmodel.peer_suggest import (
    MIN_OVERLAP,
    Candidate,
    is_common_share,
    load_prices,
    suggest,
)


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
            "047810": _series(base),
            "079550": _series([r * 0.9 for r in base]),  # 거의 같이 움직인다
            "064350": _series(_wave(N, phase=1.7)),  # 위상이 다르다
        }
        got = suggest(["047810"], prices, top=5)
        assert got[0].symbol == "079550"
        assert got[0].correlation > 0.9

    def test_the_seed_is_not_its_own_peer(self):
        prices = {"047810": _series(_wave(N)), "079550": _series(_wave(N))}
        assert "047810" not in [c.symbol for c in suggest(["047810"], prices)]

    def test_excluded_symbols_are_dropped(self):
        """ETF처럼 회사가 아닌 것을 부르는 쪽에서 뺀다 (069500 = KODEX 200)."""
        prices = {"047810": _series(_wave(N)), "069500": _series(_wave(N))}
        assert suggest(["047810"], prices, exclude={"069500"}) == []

    def test_too_little_overlap_is_dropped_not_zeroed(self):
        """0.0으로 목록 끝에 세우면 「상관 없음」과 「모름」이 섞인다."""
        short = _series(_wave(MIN_OVERLAP - 20))
        prices = {"047810": _series(_wave(N)), "448710": short}
        assert [c.symbol for c in suggest(["047810"], prices)] == []

    def test_a_halted_stock_does_not_divide_by_zero(self):
        """거래정지로 종가가 붙박이면 분산이 0이다."""
        prices = {"047810": _series(_wave(N)), "064350": _series([0.0] * N)}
        assert suggest(["047810"], prices) == []  # 상관 0 — 분산이 없어 못 낸다

    def test_two_seeds_average_their_correlations(self):
        base = _wave(N)
        prices = {
            "047810": _series(base),
            "012450": _series([r * 0.95 for r in base]),
            "079550": _series([r * 0.9 for r in base]),
            "064350": _series(_wave(N, phase=1.7)),
        }
        got = suggest(["047810", "012450"], prices, top=5)
        # 씨앗 둘은 후보에서 빠지고, 같이 움직이는 쪽이 위에 선다
        assert [c.symbol for c in got] == ["079550", "064350"]
        assert got[0].correlation > got[1].correlation
        assert 0 < got[0].correlation <= 1

    def test_a_candidate_missing_one_seed_is_dropped(self):
        """씨앗 전부와 견줄 수 있어야 평균이 뜻을 가진다."""
        base = _wave(N)
        prices = {
            "047810": _series(base),
            "012450": _series(base),
            "103140": _series(_wave(MIN_OVERLAP - 20)),
        }
        assert suggest(["047810", "012450"], prices) == []

    def test_unknown_seed_yields_nothing(self):
        assert suggest(["999990"], {"005930": _series(_wave(N))}) == []

    def test_overlap_is_reported(self):
        """**몇 일치로 본 것인가**를 후보마다 들고 있어야 한다."""
        prices = {"047810": _series(_wave(N)), "079550": _series(_wave(N))}
        got = suggest(["047810"], prices, top=1)
        assert got[0].overlap >= MIN_OVERLAP

    def test_no_sector_label_is_invented(self):
        """상관은 산업이 아니라 **지금 같이 움직이는 테마**를 찾는다.

        현대건설 씨앗이 원전 테마를 물어 오는 것이 실측된 동작이다. 여기에
        섹터 이름을 붙이면 「같은 산업」이라는 거짓 약속이 된다.
        """
        prices = {"047810": _series(_wave(N)), "079550": _series(_wave(N))}
        c = suggest(["047810"], prices, top=1)[0]
        assert set(vars(c)) == {"symbol", "correlation", "overlap", "company"}
        assert c.company == ""  # 이름은 부르는 쪽이 채운다. 섹터는 아무도 안 채운다


class TestCommonShares:
    """**우선주는 같은 회사다.** 상관이 높은 게 당연하고 피어로는 정보가 없다."""

    def test_share_class_is_the_last_digit(self):
        assert is_common_share("005930") is True  # 삼성전자
        assert is_common_share("005935") is False  # 삼성전자우
        assert is_common_share("02826K") is False  # 신형우선주
        assert is_common_share("000725") is False  # 현대건설우
        assert is_common_share("00593") is False  # 자릿수가 안 맞는 것

    def test_a_preferred_share_never_becomes_a_peer(self):
        """실측: 삼성전자 씨앗에 삼성전자우가 **0.86으로 1위**였다."""
        base = _wave(N)
        prices = {
            "005930": _series(base),
            "005935": _series([r * 0.99 for r in base]),  # 거의 같이 움직인다
            "000660": _series([r * 0.8 for r in base]),
        }
        got = suggest(["005930"], prices, top=5, market="__none__")
        assert [c.symbol for c in got] == ["000660"]

    def test_it_can_be_turned_off(self):
        prices = {"005930": _series(_wave(N)), "005935": _series(_wave(N))}
        got = suggest(["005930"], prices, top=5, market="__none__", common_only=False)
        assert [c.symbol for c in got] == ["005935"]


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
