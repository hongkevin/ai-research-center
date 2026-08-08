"""시가총액·거래대금·시장 구분 — **버리던 것을 되찾은 자리** (D87).

이 파일이 지키는 것:

* **없으면 없다고 한다.** 백필 전에는 값이 없는 것이 정상이고, 그때 화면은
  「미상」이라고 말해야지 그럴듯한 수를 내면 안 된다
* **구간이 모자라면 「60일 평균」이라고 부르지 않는다.** 20일치 평균에 그
  이름을 붙이면 다른 값이 된다
* **모르는 종목은 스크리닝을 통과하지 못한다.** 「모르니까 통과」로 두면
  「시총 3천억 이하」라는 이름의, 아무것도 안 거른 목록이 나온다
"""

from __future__ import annotations

import json

import pytest

from arc.finmodel import market_facts as mf


def _row(cap: float, turnover: float = 1e9, high: float = 100.0) -> list[float]:
    return [high, high * 0.9, turnover, cap, 1_000_000.0]


@pytest.fixture
def store(tmp_path):
    """종목 셋: 소형·중형·데이터 없음."""
    d = mf.store_dir(tmp_path)
    d.mkdir(parents=True)
    (d / "089890.json").write_text(
        json.dumps(
            {
                f"2026{m:02d}{day:02d}": _row(3e11, 5e9, 100 + day)
                for m in (6, 7)
                for day in range(1, 29)
            }
        ),
        encoding="utf-8",
    )
    (d / "005930.json").write_text(json.dumps({"20260806": _row(4e14, 8e11)}), encoding="utf-8")
    (d / mf._LISTING).write_text(
        json.dumps({"089890": "KOSDAQ", "005930": "KOSPI"}), encoding="utf-8"
    )
    return tmp_path


class TestWhenThereIsNothing:
    def test_a_missing_file_is_not_an_error(self, tmp_path):
        """**백필 전에는 비어 있는 것이 정상이다.**

        여기서 예외를 던지면 시장 데이터 하나 때문에 리포트 전체가 못 나온다.
        """
        assert mf.load_facts(tmp_path, "089890") == {}

    def test_the_snapshot_says_why_it_is_empty(self, tmp_path):
        snap = mf.snapshot(tmp_path, "089890")
        assert snap.empty
        assert snap.cap is None
        assert any("backfill" in u for u in snap.unavailable), "다음에 할 일을 알려야 한다"

    def test_an_unknown_board_is_not_kospi(self, tmp_path):
        """**넘겨짚지 않는다.** 코스닥 종목을 코스피 대비로 재면 그 초과수익은
        시장 얘기지 종목 얘기가 아니다."""
        assert mf.board(tmp_path, "089890") == ""


class TestWhatItReads:
    def test_it_reads_the_latest_cap_and_the_board(self, store):
        snap = mf.snapshot(store, "089890")
        assert snap.board == mf.KOSDAQ
        assert snap.cap == 3e11
        assert snap.asof == "20260728"

    def test_the_52w_high_follows_the_industry_convention(self, store):
        """**종가 기준이다** — 실측으로 정한 것이다 (D87).

        처음에는 장중 고가로 냈다가 씨이랩 리포트와 대조해 틀린 것을 알았다.
        리포트의 「52주 최고가 13,130원」은 2026-07-09 **종가**와 정확히 같고
        같은 날 장중 고가는 16,000원이다. 안 맞으면 화면 전체가 못 믿을 것이
        되므로 관행을 따르고, **무슨 기준인지 라벨로 말한다.**
        """
        facts = mf.load_facts(store, "089890")
        closes = {k: 110.0 for k in facts}
        assert mf.high_52w(facts, closes=closes) == (110.0, "종가 기준")
        # 종가 계열이 없으면 장중 고가로 떨어지되 **그렇다고 말한다**
        assert mf.high_52w(facts) == (128.0, "장중 고가 기준")

    def test_it_refuses_to_call_a_short_window_a_60_day_average(self, store):
        """**20일치에 「60일 평균」이라는 이름을 붙이지 않는다.**"""
        facts = mf.load_facts(store, "005930")
        assert len(facts) == 1
        assert mf.avg_turnover(facts) is None

        snap = mf.snapshot(store, "005930")
        assert snap.avg_turnover is None
        assert any("거래대금" in u for u in snap.unavailable)

    def test_a_full_window_does_average(self, store):
        assert mf.avg_turnover(mf.load_facts(store, "089890")) == pytest.approx(5e9)

    def test_cap_is_spoken_in_eok(self, store):
        """스몰캡의 말투다. `99_012_000_000` → `990억원`."""
        assert mf.display_cap(99_012_000_000) == "990억원"
        assert mf.display_cap(4e14) == "400.0조원"
        assert mf.display_cap(None) == ""


class TestScreening:
    def test_a_symbol_without_data_never_passes(self, store):
        """**「모르니까 통과」는 필터가 아니다.**

        백필 전이라면 전 종목이 통과하고, 그 목록은 「시총 3천억 이하」라고
        이름 붙은 채 아무것도 안 거른 목록이 된다 — 발굴이 거기서 거짓말한다.
        """
        out = mf.screen(store, ["089890", "없는종목"], mf.Screen(max_cap=1e12))
        assert out == ["089890"]

    def test_the_cap_ceiling_drops_the_big_ones(self, store):
        """「숨겨진 수혜주」는 정의상 작다. 상관만 보면 대형주가 맨 위에 앉는다."""
        out = mf.screen(store, ["089890", "005930"], mf.Screen(max_cap=1e12))
        assert out == ["089890"]

    def test_the_board_filter_works(self, store):
        assert mf.screen(store, ["089890", "005930"], mf.Screen(boards=("KOSDAQ",))) == ["089890"]

    def test_liquidity_floor_needs_a_real_average(self, store):
        """**유동성은 스몰캡에서 투자 가능성 자체다.**

        삼성전자는 거래대금이 압도적이지만 하루치뿐이라 60일 평균이 없다.
        평균을 못 내면 문턱을 못 넘는다 — 「모르니까 통과」와 반대다.
        """
        out = mf.screen(store, ["089890", "005930"], mf.Screen(min_turnover=1e9))
        assert out == ["089890"]

    def test_no_condition_filters_nothing(self, store):
        out = mf.screen(store, ["089890", "005930"], mf.Screen())
        assert set(out) == {"089890", "005930"}


class TestWriting:
    def test_merge_keeps_what_was_there(self, tmp_path):
        mf.merge(tmp_path, {"089890": {"20260701": mf.Row(1, 1, 1, 1, 1)}}, {"089890": "KOSDAQ"})
        mf.merge(tmp_path, {"089890": {"20260702": mf.Row(2, 2, 2, 2, 2)}}, {})
        facts = mf.load_facts(tmp_path, "089890")
        assert sorted(facts) == ["20260701", "20260702"]
        assert mf.board(tmp_path, "089890") == "KOSDAQ", "시장 구분이 살아남아야 한다"

    def test_the_listing_file_is_not_a_symbol(self, tmp_path):
        """`_listing.json`을 종목으로 세면 종목 수가 하나 많아진다."""
        mf.merge(tmp_path, {"089890": {"20260701": mf.Row(1, 1, 1, 1, 1)}}, {"089890": "KOSDAQ"})
        assert mf.available(tmp_path) == 1
        assert mf.dates_on_disk(tmp_path) == {"20260701"}
