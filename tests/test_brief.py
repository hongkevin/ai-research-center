"""모닝 브리프 — 아침에 이것만 봐도 되게.

이 파일이 지키는 것 셋:

* **LLM을 안 쓴다.** 브리프는 서술이 아니라 배열이다 — 요약하면 RA가 원문을
  안 보게 되고, 아침에 필요한 것은 판단이 아니라 「놓친 것이 없다」는 확인이다.
* **커버와 관심을 다르게 다룬다.** 관심 종목에까지 공시·기사를 붙이면 호출이
  배로 늘고 화면이 길어져 정작 볼 것을 못 본다.
* **순서가 답의 일부다.** 이름순으로 세우면 매일 같아서 눈이 훑고 지나간다.
"""

from __future__ import annotations

from arc.brief import BRIEF_KEYS, NOTABLE, build_brief
from arc.finmodel.moves import Move, Moves
from arc.store.profile import COVER, WATCH, Covered, Profile, add_stock


def _moves(symbol: str, day: float | None, **rest: float) -> Moves:
    items = [Move(key="1d", label="1일", change_pct=day, from_date="20260805", to_date="20260806")]
    items += [Move(key=k, label=k, change_pct=v) for k, v in rest.items()]
    return Moves(symbol=symbol, last_close=1000.0, last_date="20260806", items=items)


def _profile(*stocks: Covered) -> Profile:
    p = Profile(uid="u1")
    for s in stocks:
        add_stock(p, s)
    return p


class TestSplit:
    def test_cover_and_watch_go_to_different_lists(self):
        p = _profile(
            Covered(symbol="064350", company="현대로템", kind=COVER),
            Covered(symbol="042660", company="한화오션", kind=WATCH),
        )
        b = build_brief(p, {"064350": _moves("064350", -2.0), "042660": _moves("042660", 1.0)})
        assert [x.symbol for x in b.cover] == ["064350"]
        assert [x.symbol for x in b.watch] == ["042660"]

    def test_watch_never_gets_filings_or_articles(self):
        """관심 종목까지 붙이면 호출이 배로 늘고 화면이 길어진다."""
        p = _profile(Covered(symbol="042660", kind=WATCH))
        b = build_brief(
            p,
            {"042660": _moves("042660", 1.0)},
            filings={"042660": [{"title": "무언가"}]},
            articles={"042660": [{"title": "기사"}]},
        )
        assert b.watch[0].filings == []
        assert b.watch[0].articles == []


class TestOrder:
    def test_a_filing_beats_a_big_move(self):
        """공시는 사실이고 등락은 결과다. 아침에 먼저 볼 것은 사실이다."""
        p = _profile(
            Covered(symbol="064350", kind=COVER),
            Covered(symbol="047810", kind=COVER),
        )
        b = build_brief(
            p,
            {"064350": _moves("064350", -0.2), "047810": _moves("047810", -9.0)},
            filings={"064350": [{"title": "단일판매·공급계약"}]},
        )
        assert [x.symbol for x in b.cover] == ["064350", "047810"]

    def test_bigger_moves_come_first(self):
        p = _profile(
            Covered(symbol="064350", kind=COVER),
            Covered(symbol="047810", kind=COVER),
        )
        b = build_brief(
            p, {"064350": _moves("064350", 1.0), "047810": _moves("047810", -8.0)}
        )
        assert [x.symbol for x in b.cover] == ["047810", "064350"]

    def test_direction_does_not_matter_only_size(self):
        """빠진 것만 보면 급등의 이유를 클라이언트가 먼저 묻는다."""
        p = _profile(
            Covered(symbol="064350", kind=COVER),
            Covered(symbol="047810", kind=COVER),
        )
        b = build_brief(
            p, {"064350": _moves("064350", 9.0), "047810": _moves("047810", -2.0)}
        )
        assert [x.symbol for x in b.cover] == ["064350", "047810"]

    def test_a_symbol_without_prices_still_appears(self):
        p = _profile(Covered(symbol="064350", kind=COVER))
        b = build_brief(p, {})
        assert [x.symbol for x in b.cover] == ["064350"]
        assert b.cover[0].day_change is None


class TestNotable:
    def test_a_big_move_is_notable(self):
        p = _profile(Covered(symbol="064350", kind=COVER))
        b = build_brief(p, {"064350": _moves("064350", NOTABLE + 0.1)})
        assert b.cover[0].notable is True

    def test_a_small_move_is_not(self):
        p = _profile(Covered(symbol="064350", kind=COVER))
        b = build_brief(p, {"064350": _moves("064350", NOTABLE - 0.1)})
        assert b.cover[0].notable is False

    def test_a_filing_makes_it_notable_regardless(self):
        p = _profile(Covered(symbol="064350", kind=COVER))
        b = build_brief(
            p, {"064350": _moves("064350", 0.1)}, filings={"064350": [{"title": "x"}]}
        )
        assert b.cover[0].notable is True


class TestNote:
    def test_an_empty_profile_says_what_to_do(self):
        b = build_brief(Profile(), {})
        assert "커버 종목을 먼저" in b.note
        assert b.empty

    def test_a_quiet_morning_says_so(self):
        """**없으면 없다고 말한다.** 빈 화면은 고장과 구분되지 않는다."""
        p = _profile(Covered(symbol="064350", kind=COVER))
        b = build_brief(p, {"064350": _moves("064350", 0.3)}, asof="20260806")
        assert "큰 움직임도 새 공시도 없습니다" in b.note

    def test_counts_are_reported(self):
        p = _profile(
            Covered(symbol="064350", kind=COVER),
            Covered(symbol="047810", kind=COVER),
        )
        b = build_brief(
            p,
            {"064350": _moves("064350", -8.0), "047810": _moves("047810", 0.1)},
            filings={"047810": [{"title": "x"}]},
            asof="20260806",
        )
        assert "공시 1건" in b.note
        assert b.filing_count == 1
        assert b.notable_count == 2

    def test_no_prices_is_said_out_loud(self):
        p = _profile(Covered(symbol="064350", kind=COVER))
        assert "시세를 아직 받지 않아" in build_brief(p, {}).note


class TestHorizons:
    def test_only_the_morning_horizons(self):
        """1년은 카드에서 본다. 아침에 보는 것은 어제와 최근이다."""
        p = _profile(Covered(symbol="064350", kind=COVER))
        b = build_brief(p, {"064350": _moves("064350", 1.0, **{"1w": 2.0, "1m": 3.0, "1y": 9.0})})
        assert [m.key for m in b.cover[0].moves] == list(BRIEF_KEYS)
