"""상시 맥락 (D84).

*"커버리지/종목 … 리포트와 또 질문을 주고 받은 대화까지 다 컨텍스트로"*.

지키는 것 셋 — 셋 다 이 제품의 전제에서 나온다:

  1. **배경은 근거가 아니다.** 「당신은 한화오션을 커버합니다」는 출처를 붙일
     수 있는 종류가 아니다. 프롬프트에서 그렇게 못 박아야 한다
  2. **개수를 넣지 않는다.** 종목코드는 G0를 통과하지만 개수는 막힌다 —
     모델이 배경을 되뱉으면 그 문장이 버려진다
  3. **비면 절을 안 넣는다.** 빈 절은 모델이 뭔가로 채우려 한다
"""

from __future__ import annotations

from dataclasses import dataclass, field

from arc.chat.standing import REPEAT_AT, Standing, build_standing, standing_prompt
from arc.llm.number_registry import NumberRegistry
from arc.store.profile import COVER, WATCH, Covered, Profile


@dataclass
class _Event:
    kind: str
    subject: str = ""
    text: str = ""
    detail: dict = field(default_factory=dict)


def _profile() -> Profile:
    p = Profile(sectors=["조선", "방산·우주"])
    p.stocks = [
        Covered(symbol="042660", company="한화오션", sector="조선", kind=COVER),
        Covered(symbol="010140", company="삼성중공업", sector="조선", kind=WATCH),
    ]
    return p


class TestBuild:
    def test_cover_and_watch_are_kept_apart(self):
        """커버는 「리포트를 낸다」, 관심은 「옆에서 본다」 — 다른 얘기다."""
        got = build_standing(_profile())
        assert got.covers == [("042660", "한화오션")]
        assert got.watches == [("010140", "삼성중공업")]

    def test_asking_once_is_not_a_repeat(self):
        """한 번은 그냥 질문이고, **두 번이면 앞의 답이 부족했다.**"""
        rows = [_Event("asked", "042660", "영업이익률 어때")]
        assert build_standing(_profile(), rows, subject="042660").asked_before == []

    def test_asking_twice_surfaces_both(self):
        rows = [
            _Event("asked", "042660", "영업이익률 어때"),
            _Event("asked", "042660", "마진이 왜 이래"),
        ]
        got = build_standing(_profile(), rows, subject="042660")
        assert len(got.asked_before) == REPEAT_AT

    def test_another_stock_is_not_dragged_in(self):
        """**지목된 종목만 센다.** 최근 것을 아무거나 끌어오면 잡음이다."""
        rows = [_Event("asked", "005930", "a"), _Event("asked", "005930", "b")]
        assert build_standing(_profile(), rows, subject="042660").asked_before == []

    def test_focus_only_names_stocks_i_watch(self):
        """내 목록에 없는 것은 배경에 안 넣는다 — 남의 종목 이름이 왜 여기 있나."""
        rows = [_Event("opened", "042660"), _Event("opened", "999999")]
        got = build_standing(_profile(), rows, subject="042660")
        assert got.focus == ["한화오션"]

    def test_no_events_is_fine(self):
        assert build_standing(_profile()).asked_before == []

    def test_symbols_covers_both_kinds(self):
        assert set(build_standing(_profile()).symbols()) == {"042660", "010140"}


class TestPrompt:
    def test_empty_means_no_section(self):
        """**빈 절을 넣으면 모델이 그 자리를 채우려 한다.**"""
        assert standing_prompt(Standing()) == ""

    def test_it_says_this_is_not_evidence(self):
        """이게 없으면 모델이 배경을 사실로 인용한다 — 출처를 붙일 수 없는 것에."""
        out = standing_prompt(build_standing(_profile()))
        assert "근거가 아닙니다" in out

    def test_no_counts_survive_the_gate(self):
        """**실측한 제약이다.** 종목코드는 통과하지만 개수는 막힌다:

            "커버 12종목 · 관심 4종목"  → 막힘 ['12', '4']

        모델이 배경을 되뱉으면 그 문장이 버려지므로, 애초에 개수를 안 넣는다.
        """
        rows = [
            _Event("asked", "042660", "영업이익률 어때"),
            _Event("asked", "042660", "마진이 왜 이래"),
            _Event("opened", "042660"),
        ]
        out = standing_prompt(build_standing(_profile(), rows, subject="042660"))
        bad = NumberRegistry().find_unregistered_numbers(out)
        assert bad == [], [b.text for b in bad]

    def test_names_and_codes_are_there(self):
        out = standing_prompt(build_standing(_profile()))
        assert "한화오션(042660)" in out
        assert "조선" in out

    def test_a_repeat_tells_the_model_not_to_repeat_itself(self):
        rows = [_Event("asked", "042660", "a"), _Event("asked", "042660", "b")]
        out = standing_prompt(build_standing(_profile(), rows, subject="042660"))
        assert "되풀이하지 마십시오" in out
        # **지어내라는 뜻이 아니다** — 없으면 없다고 말하라는 것까지 적혀야 한다
        assert "없다고" in out


class TestSystemRule:
    def test_the_system_prompt_forbids_citing_the_background(self):
        """프롬프트에 배경을 넣기만 하고 규칙을 안 적으면, 모델이 그것을
        출처로 달거나 사실로 쓴다."""
        from arc.chat.answer import SYSTEM_PROMPT

        assert "배경은 근거가 아닙니다" in SYSTEM_PROMPT
        assert "사실을 끌어오지 마십시오" in SYSTEM_PROMPT
