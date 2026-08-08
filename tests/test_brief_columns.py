"""브리프 세 칸 — **모수·요약·매크로.**

세 가지를 지킨다:

  1. 섹터 줄이 **모수를 밝힌다** — 피어 그룹으로 냈는지 내 종목으로 냈는지
  2. 칸 요약이 **새 사실을 만들지 않는다** — 아래 숫자를 다시 읽은 것뿐이다
  3. 매크로가 **자기 날짜를 달고 온다** — 지수와 다를 수 있다
"""

from __future__ import annotations

import datetime as dt

from arc.brief import build_brief
from arc.data.kr.ecos import MACRO, Point
from arc.finmodel.moves import Move, Moves
from arc.store.profile import COVER, WATCH, Covered, Profile


def _moves(symbol: str, day: float, company: str = "") -> Moves:
    return Moves(
        symbol=symbol,
        company=company or symbol,
        last_close=10_000.0,
        last_date="20260806",
        items=[
            Move(
                key="1d",
                label="1일",
                change_pct=day,
                from_date="20260805",
                to_date="20260806",
                days=1,
            )
        ],
    )


def _profile(*stocks: Covered) -> Profile:
    p = Profile()
    p.stocks = list(stocks)
    return p


class TestSectorBasis:
    """**모수가 곧 뜻이다.** 내 3종목과 피어 12종목은 다른 얘기다."""

    def test_peer_group_widens_the_universe(self):
        profile = _profile(Covered(symbol="042660", company="한화오션", sector="조선", kind=COVER))
        moves = {
            "042660": _moves("042660", 1.0),
            "010140": _moves("010140", 5.0),  # 내가 커버하지 않는 피어
            "009540": _moves("009540", 9.0),
        }
        brief = build_brief(
            profile,
            moves,
            universe={"조선": ["042660", "010140", "009540"]},
            keys=("1d",),
        )
        row = brief.sectors[0]
        assert row.basis == "peer"
        assert row.universe == 3
        assert row.count == 1  # 내 종목은 하나뿐이다
        # 중앙값은 세 종목의 것이지 내 것이 아니다
        assert row.day_change == 5.0
        assert "피어 3종목" in row.basis_label

    def test_without_a_group_it_says_so(self):
        """**피어 그룹이 없으면 섹터라고 말하지 않는다.**"""
        profile = _profile(Covered(symbol="042660", company="한화오션", sector="조선", kind=COVER))
        brief = build_brief(profile, {"042660": _moves("042660", 1.0)}, keys=("1d",))
        row = brief.sectors[0]
        assert row.basis == "mine"
        assert row.day_change == 1.0
        assert row.basis_label == "내 1종목 중앙값"

    def test_a_universe_symbol_without_prices_is_dropped(self):
        """시세가 없는 구성원은 모수에서 빠진다 — **0으로 채우지 않는다.**"""
        profile = _profile(Covered(symbol="042660", company="한화오션", sector="조선", kind=COVER))
        brief = build_brief(
            profile,
            {"042660": _moves("042660", 1.0)},
            universe={"조선": ["042660", "999999"]},
            keys=("1d",),
        )
        assert brief.sectors[0].universe == 1


class TestHeads:
    """칸 요약 — **아래 숫자를 다시 읽은 것.** 새 사실이 생기면 안 된다."""

    def test_stocks_head_counts_what_is_below(self):
        profile = _profile(
            Covered(symbol="042660", company="한화오션", sector="조선", kind=COVER),
            Covered(symbol="010140", company="삼성중공업", sector="조선", kind=WATCH),
        )
        brief = build_brief(
            profile,
            {"042660": _moves("042660", 6.2, "한화오션"), "010140": _moves("010140", 0.4)},
            keys=("1d",),
        )
        head = brief.heads["stocks"]
        assert "커버 1종목" in head
        assert "관심 1종목" in head
        assert "한화오션" in head and "+6.20%" in head

    def test_quiet_day_says_it_is_quiet(self):
        """**없으면 없다고 한다.** 빈 요약은 「아직 안 읽었나」로 읽힌다."""
        profile = _profile(Covered(symbol="042660", company="한화오션", sector="조선", kind=COVER))
        brief = build_brief(profile, {"042660": _moves("042660", 0.3)}, keys=("1d",))
        assert "움직인 커버 종목 없음" in brief.heads["stocks"]

    def test_no_macro_no_head(self):
        """값이 없으면 그 절이 통째로 빠진다 — 빈 문자열도 아니다."""
        brief = build_brief(_profile(), {}, keys=("1d",))
        assert "macro" not in brief.heads

    def test_macro_head_marks_a_rate_change_not_a_delta(self):
        """계단형은 **「전일 대비」가 아니라 「언제 바뀌었나」**로 적는다."""
        brief = build_brief(
            _profile(),
            {},
            macro=[
                {
                    "label": "기준금리",
                    "display": "2.75%",
                    "change": 0.25,
                    "changed_at": "202607",
                    "digits": 2,
                }
            ],
            keys=("1d",),
        )
        assert "기준금리 2.75% (2026-07 인상)" in brief.heads["macro"]


class TestMacroPoint:
    def test_step_series_are_declared_as_such(self):
        assert [s.key for s in MACRO if s.step] == ["base_rate"]
        assert next(s for s in MACRO if s.key == "base_rate").cycle == "M"

    def test_change_is_a_difference_not_a_percent(self):
        """환율은 원, 금리는 %p. **퍼센트로 읽히면 안 된다.**"""
        p = Point(
            key="usdkrw", label="원/달러", value=1418.8, date="20260807", prev=1424.8, digits=1
        )
        assert round(p.change, 1) == -6.0
        assert p.display == "1,418.8"

    def test_monthly_dates_have_no_staleness(self):
        """월별에 「며칠 늦었다」를 붙이면 늘 늦은 것처럼 보인다."""
        assert (
            Point(key="base_rate", label="기준금리", value=2.75, date="202607").stale_days is None
        )

    def test_daily_staleness_is_real_days(self):
        today = dt.datetime.now(dt.UTC).date()
        stamp = (today - dt.timedelta(days=3)).strftime("%Y%m%d")
        assert Point(key="usdkrw", label="원/달러", value=1.0, date=stamp).stale_days == 3


class TestUnavailable:
    """**「없다」와 「못 읽었다」는 다른 말이다** (D85).

    이 화면의 존재 이유가 *"놓친 것이 없다는 확인"*인데, 공시·기사·지수·매크로
    실패가 전부 빈 목록으로 삼켜지면 **그 확인이 거짓이 된다.** 공시 하나를
    놓치면 사고가 나는 직업이다.
    """

    def test_it_refuses_to_say_nothing_happened(self):
        p = _profile(Covered(symbol="042660", company="한화오션", kind=COVER))
        brief = build_brief(
            p,
            {"042660": _moves("042660", 0.3)},
            unavailable=["공시"],
            keys=("1d",),
            asof="20260806",
        )
        assert "없습니다" not in brief.note
        assert "공시" in brief.note and "못 읽었습니다" in brief.note

    def test_a_clean_run_says_nothing_happened(self):
        """못 읽은 것이 없으면 **평소대로 말한다.** 경고가 늘 떠 있으면 안 읽힌다."""
        p = _profile(Covered(symbol="042660", company="한화오션", kind=COVER))
        brief = build_brief(p, {"042660": _moves("042660", 0.3)}, keys=("1d",), asof="20260806")
        assert brief.note == "커버 종목에 큰 움직임도 새 공시도 없습니다."

    def test_findings_and_failures_appear_together(self):
        """찾은 것이 있어도 못 읽은 것을 숨기지 않는다."""
        p = _profile(Covered(symbol="042660", company="한화오션", kind=COVER))
        brief = build_brief(
            p,
            {"042660": _moves("042660", 6.2)},
            unavailable=["기사"],
            keys=("1d",),
            asof="20260806",
        )
        assert "3% 이상" in brief.note
        assert "기사" in brief.note

    def test_no_prices_does_not_claim_nothing_moved(self):
        """**안 움직인 게 아니라 안 본 것이다.**

        시세가 없는데 「3% 이상 움직인 커버 종목 없음」이라고 하면, 같은 화면
        위쪽의 「시세를 아직 받지 않았다」와 정면으로 모순된다.
        """
        p = _profile(Covered(symbol="042660", company="한화오션", kind=COVER))
        brief = build_brief(p, {}, keys=("1d",))
        assert "움직인 커버 종목 없음" not in brief.heads["stocks"]
        assert "시세가 없어" in brief.heads["stocks"]
