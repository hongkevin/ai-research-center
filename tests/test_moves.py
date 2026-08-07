"""기간별 등락 — RA의 하루는 분기 사진으로 안 돈다.

이 파일이 지키는 것: **없는 것은 0%가 아니다.** 0으로 채우면 「안 움직였다」와
「모른다」가 같은 값이 되고, 신규 상장 종목의 1년 수익률이 0%로 보인다.
"""

from __future__ import annotations

from arc.finmodel.moves import HORIZONS, Moves, fmt, moves_for, moves_for_symbols


def _series(closes: list[float], start_day: int = 1) -> dict[str, float]:
    return {f"{20260000 + start_day + i:08d}": c for i, c in enumerate(closes)}


class TestMoves:
    def test_one_day_change(self):
        m = moves_for(_series([100.0, 110.0]))
        assert round(m.get("1d").change_pct, 6) == 10.0
        assert m.last_close == 110.0

    def test_the_dates_used_are_reported(self):
        """「1개월 +8.2%」만 있으면 언제부터인지 아무도 모른다."""
        m = moves_for(_series([100.0] * 30))
        one = m.get("1d")
        assert one.from_date and one.to_date
        assert one.to_date == m.last_date
        assert one.days == 1

    def test_trading_days_not_calendar(self):
        """달력으로 세면 공휴일에 따라 종목마다 다른 날을 비교하게 된다."""
        closes = [100.0] * 20 + [120.0]
        m = moves_for(_series(closes))
        # 1주 = 5거래일 전(=100) 대비
        assert round(m.get("1w").change_pct, 6) == 20.0

    def test_a_short_history_is_marked_partial(self):
        """신규 상장이면 「1년」이 실제로는 3개월이다. **말해야 한다.**"""
        m = moves_for(_series([100.0, 200.0]))
        year = m.get("1y")
        assert year.partial is True
        assert year.days == 1
        assert round(year.change_pct, 6) == 100.0

    def test_a_full_history_is_not_partial(self):
        m = moves_for(_series([100.0] * 300))
        assert m.get("1y").partial is False

    def test_no_data_is_none_not_zero(self):
        m = moves_for({})
        assert all(x.change_pct is None for x in m.items)
        assert m.last_close is None

    def test_a_single_day_has_nothing_to_compare(self):
        m = moves_for(_series([100.0]))
        assert all(x.change_pct is None for x in m.items)
        assert m.last_close == 100.0

    def test_zero_and_negative_closes_are_skipped(self):
        """거래정지·데이터 오류로 0이 들어오면 나눗셈이 터진다."""
        m = moves_for({"20260101": 0.0, "20260102": 100.0, "20260103": 110.0})
        assert round(m.get("1d").change_pct, 6) == 10.0

    def test_every_horizon_is_present(self):
        m = moves_for(_series([100.0] * 300))
        assert [x.key for x in m.items] == [k for k, _, _ in HORIZONS]


class TestBatch:
    def test_a_symbol_without_prices_keeps_its_place(self):
        """빼 버리면 화면에서 종목이 사라져 「왜 안 나오지」가 된다."""
        prices = {"064350": _series([100.0, 110.0])}
        got = moves_for_symbols(prices, ["064350", "999999"], names={"064350": "현대로템"})
        assert [m.symbol for m in got] == ["064350", "999999"]
        assert got[0].company == "현대로템"
        assert got[1].last_close is None

    def test_empty_input(self):
        assert moves_for_symbols({}, []) == []


class TestFormat:
    def test_the_sign_comes_first(self):
        """등락은 방향이 먼저다."""
        assert fmt(2.34) == "+2.3%"
        assert fmt(-2.34) == "-2.3%"
        assert fmt(0.0) == "+0.0%"

    def test_missing_is_a_dash(self):
        assert fmt(None) == "—"


def test_moves_defaults_to_an_empty_list():
    assert Moves(symbol="064350").items == []
