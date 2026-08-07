"""하루에 세 번 (D74).

요구가 그대로였다: *"지금은 모닝 브리프지만, 장중 브리프(예를 들어 점심시간),
마감 후 브리프 등이 있을 수도 있겠다"*.

**셋을 가르는 것은 시각이 아니라 「지금 무엇이 확정됐나」다.** 우리 시세는
금융위 EOD라 장중에는 오늘 값이 **없다.** 그때 「1일 -2.4%」를 세우면 그건
어제 얘기인데 화면은 오늘로 읽는다.
"""

from __future__ import annotations

import datetime as dt

from arc.brief import CLOSE, KST, MIDDAY, MORNING, build_brief, current_session, session_label
from arc.finmodel.moves import Move, Moves
from arc.store.profile import COVER, Covered, Profile


def _profile() -> Profile:
    p = Profile()
    p.stocks = [Covered(symbol="042660", company="한화오션", sector="조선", kind=COVER)]
    return p


def _moves() -> dict[str, Moves]:
    return {
        "042660": Moves(
            symbol="042660",
            company="한화오션",
            last_close=10_000.0,
            last_date="20260806",
            items=[
                Move(
                    key="1d",
                    label="1일",
                    change_pct=6.2,
                    from_date="20260805",
                    to_date="20260806",
                    days=1,
                )
            ],
        )
    }


def _at(hour: int) -> dt.datetime:
    return dt.datetime(2026, 8, 7, hour, 0, tzinfo=KST).astimezone(dt.UTC)


class TestWhichSession:
    def test_the_clock_picks_it(self):
        assert current_session(_at(7)) == MORNING
        assert current_session(_at(12)) == MIDDAY
        assert current_session(_at(18)) == CLOSE

    def test_boundaries_belong_to_the_later_one(self):
        """9시 정각은 장중이다 — 개장이 그때다."""
        assert current_session(_at(8)) == MORNING
        assert current_session(_at(9)) == MIDDAY
        assert current_session(_at(15)) == MIDDAY
        assert current_session(_at(16)) == CLOSE

    def test_midnight_is_morning_not_close(self):
        """자정은 **다음 날 아침**이다 — 마감 브리프가 밤새 남아 있으면 안 된다."""
        assert current_session(_at(0)) == MORNING

    def test_each_says_what_it_is_for(self):
        label, why = session_label(MIDDAY)
        assert label == "장중 브리프"
        assert why  # **무엇을 보는 자리인지 화면이 말해야 한다**


class TestMidday:
    """장중 — **주가를 뺀다.** 오늘 값이 없어서지 덜 중요해서가 아니다."""

    def test_prices_are_dropped(self):
        brief = build_brief(_profile(), _moves(), session=MIDDAY, keys=("1d",))
        assert brief.cover[0].moves == []
        assert brief.sectors == []

    def test_it_does_not_claim_nothing_moved(self):
        """**「움직인 종목 없음」이라고 하면 안 된다.**

        안 움직인 게 아니라 안 본 것이다. 그 둘을 섞으면 화면이 거짓말을 한다.
        """
        brief = build_brief(_profile(), _moves(), session=MIDDAY, keys=("1d",))
        assert "움직인 커버 종목 없음" not in brief.heads.get("stocks", "")
        assert "주가는 마감 뒤" in brief.heads["stocks"]

    def test_mentions_take_the_empty_seat(self):
        brief = build_brief(_profile(), {}, mentions={"042660": 7}, session=MIDDAY, keys=("1d",))
        assert brief.mentions == [{"symbol": "042660", "count": 7}]
        assert brief.cover[0].mention_count == 7
        assert "언급 7건" in brief.note

    def test_a_quiet_midday_says_so(self):
        brief = build_brief(_profile(), {}, session=MIDDAY, keys=("1d",))
        assert "도는 말도 없습니다" in brief.note

    def test_zero_mentions_are_not_listed(self):
        """**0으로 채우지 않는다.** 언급 0건은 줄이 아니다."""
        brief = build_brief(_profile(), {}, mentions={"042660": 0}, session=MIDDAY, keys=("1d",))
        assert brief.mentions == []


class TestMorningAndClose:
    def test_prices_survive(self):
        for session in (MORNING, CLOSE):
            brief = build_brief(_profile(), _moves(), session=session, keys=("1d",))
            assert brief.cover[0].day_change == 6.2, session
            assert brief.sectors, session

    def test_ranking_uses_mentions_when_prices_are_gone(self):
        """장중에 이름순으로 떨어지면 **순서가 정보를 잃는다.**"""
        p = Profile()
        p.stocks = [
            Covered(symbol="000001", company="가나", kind=COVER),
            Covered(symbol="000002", company="다라", kind=COVER),
        ]
        brief = build_brief(p, {}, mentions={"000002": 9}, session=MIDDAY, keys=("1d",))
        assert [x.symbol for x in brief.cover] == ["000002", "000001"]
