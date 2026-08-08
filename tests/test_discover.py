"""발굴 — **「숨겨진 ○○ 수혜주」** (D87).

이 파일이 지키는 것:

* **이미 보는 종목은 후보가 아니다.** 나오면 그건 발굴이 아니라 반복이다
* **좁힌 뒤에 상관을 낸다.** 다 계산하고 거르면 `top=12`가 「대형주 10개를
  버리고 남은 2개」가 된다
* **지수는 안 거른다.** 빼면 시장 요인 제거가 「소형주 평균 대비」가 되어
  초과수익의 뜻이 바뀐다
* **아직 안 도는 것과 이미 도는 것을 가른다.** 도는 종목을 「숨겨진」이라고
  부르면 그 말이 뜻을 잃는다
"""

from __future__ import annotations

import json
import math

import pytest

from arc.finmodel import market_facts as mf
from arc.finmodel.discover import MARKET_KEYS, discover


def _walk(seed: int, days: int = 300, beta: float = 0.0, market=None) -> dict[str, float]:
    """되풀이 가능한 가짜 시세. `beta`로 시장과의 동조를 넣는다."""
    out: dict[str, float] = {}
    price = 10_000.0
    for i in range(days):
        wiggle = math.sin(seed * 1.7 + i * 0.37) * 0.01
        pull = 0.0
        if market is not None and beta:
            stamps = sorted(market)
            if i > 0:
                prev, cur = market[stamps[i - 1]], market[stamps[i]]
                pull = beta * ((cur - prev) / prev)
        price *= 1 + wiggle + pull
        out[f"2026{(i // 28) % 12 + 1:02d}{i % 28 + 1:02d}{seed:02d}"[:8]] = round(price, 1)
    return out


@pytest.fixture
def world(tmp_path):
    """소형주 넷 + 대형주 하나 + 지수.

    `A`·`B`가 씨앗이고 `C`는 둘과 같이 움직인다. `D`는 따로 논다.
    `BIG`은 같이 움직이지만 **시총이 커서 걸러져야 한다.**
    """
    market = _walk(1)
    prices = {
        "KOSPI": market,
        "000010": _walk(2, beta=1.4, market=market),  # A — 씨앗
        "000020": _walk(2, beta=1.35, market=market),  # B — 씨앗 (A와 거의 같다)
        "000030": _walk(2, beta=1.3, market=market),  # C — 같이 움직이는 소형주
        "000040": _walk(9, beta=-0.2, market=market),  # D — 따로 논다
        "000050": _walk(2, beta=1.32, market=market),  # BIG — 같이 움직이나 대형
    }
    d = mf.store_dir(tmp_path)
    d.mkdir(parents=True)
    caps = {"000010": 2e11, "000020": 2e11, "000030": 2e11, "000040": 2e11, "000050": 9e13}
    for sym, cap in caps.items():
        rows = {stamp: [100.0, 90.0, 5e9, cap, 1e7] for stamp in prices[sym]}
        (d / f"{sym}.json").write_text(json.dumps(rows), encoding="utf-8")
    (d / mf._LISTING).write_text(
        json.dumps({s: "KOSDAQ" for s in caps}, ensure_ascii=False), encoding="utf-8"
    )
    return tmp_path, prices


class TestWhoIsACandidate:
    def test_my_own_stocks_never_come_back(self, world):
        """**이미 보는 종목이 후보로 나오면 그건 발굴이 아니다.**"""
        base, prices = world
        got = discover(["000010"], base=base, prices=prices, exclude={"000030"})
        assert "000030" not in [f.symbol for f in got.found]

    def test_the_seeds_never_come_back(self, world):
        base, prices = world
        got = discover(["000010", "000020"], base=base, prices=prices)
        found = {f.symbol for f in got.found}
        assert not (found & {"000010", "000020"})

    def test_a_big_cap_is_filtered_before_correlation(self, world):
        """「숨겨진」은 정의상 작다. **상관만 보면 대형주가 위에 앉는다.**

        BIG은 씨앗과 거의 같이 움직이지만 시총이 90조라 후보가 아니다.
        """
        base, prices = world
        got = discover(["000010"], base=base, prices=prices, max_cap=5e11)
        assert "000050" not in [f.symbol for f in got.found]
        assert "000030" in [f.symbol for f in got.found]

    def test_the_universe_is_what_survived_the_screen(self, world):
        """**「2,987개 중에서」가 아니다.** 모수를 부풀리면 목록이 더 대단해 보인다."""
        base, prices = world
        got = discover(["000010"], base=base, prices=prices, max_cap=5e11)
        # A(씨앗) 제외 · BIG 제외 · 지수 제외 → B·C·D 셋
        assert got.universe == 3

    def test_the_index_survives_the_narrowing(self, world):
        """**지수를 빼면 「시장 대비」가 「소형주 평균 대비」가 된다.**

        그러면 초과수익의 뜻이 바뀌는데 화면은 여전히 「시장 대비」라고 쓴다.
        """
        base, prices = world
        from arc.finmodel.discover import _narrow

        narrowed = _narrow(prices, base, mf.Screen(max_cap=5e11), keep={"000010"})
        assert set(MARKET_KEYS) & set(narrowed), "지수가 살아남아야 한다"


class TestWhatItSaysAboutItself:
    def test_it_reports_the_random_baseline_next_to_the_verdict(self, world):
        """**「0.41」만 보면 높은지 모른다.** 무작위 수준을 옆에 놓는다."""
        base, prices = world
        got = discover(["000010", "000020"], base=base, prices=prices)
        assert got.baseline > 0
        assert got.cohesion >= 0

    def test_no_market_data_is_said_out_loud(self, tmp_path):
        """백필 전에는 조건에 맞는 종목이 0이다 — **다음에 할 일을 알린다.**"""
        prices = {"000010": _walk(2), "000030": _walk(3)}
        got = discover(["000010"], base=tmp_path, prices=prices)
        assert got.found == []
        assert "backfill" in got.note

    def test_a_seed_without_prices_is_said_out_loud(self, world):
        base, prices = world
        got = discover(["999999"], base=base, prices=prices)
        assert got.seeds == []
        assert got.note


class TestHiddenMeansUnheard:
    def test_a_stock_already_going_around_is_not_hidden(self, world):
        """**이미 도는 종목을 「숨겨진」이라고 부르면 그 말이 뜻을 잃는다.**

        센티가 오늘 언급을 세고 있으니, 0인 것만 진짜 발굴이다.
        """
        base, prices = world
        got = discover(["000010", "000020"], base=base, prices=prices, mentions={"000030": 14})
        row = next((f for f in got.found if f.symbol == "000030"), None)
        assert row is not None
        assert row.mentions == 14
        assert not row.unheard
        assert row not in got.unheard

    def test_no_mentions_means_hidden(self, world):
        base, prices = world
        got = discover(["000010", "000020"], base=base, prices=prices)
        assert all(f.unheard for f in got.found)


class TestNamesReachTheScreen:
    def test_a_code_only_list_cannot_be_read(self, world):
        """**종목코드 열두 줄은 아무것도 말하지 않는다.**"""
        base, prices = world
        got = discover(["000010"], base=base, prices=prices, names={"000030": "덕양에너젠"})
        row = next(f for f in got.found if f.symbol == "000030")
        assert row.company == "덕양에너젠"
