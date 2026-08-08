"""사건 로그 (D77).

**개인화의 핵심은 목록이 아니라 사건이다.** 커버 종목 목록은 설정이고, 사람이
직접 넣었고, 6개월 뒤에도 넣은 그대로다. 도구가 사람을 알게 되는 것은 「무엇을
고쳤나·무엇을 안 넣었나·무엇을 또 물었나」에서 온다.

여기서 지키는 것 셋:

  1. **가리지 않은 텍스트는 안 받는다** — 사건은 언젠가 프롬프트로 간다
  2. **기록 실패가 본 일을 막지 않는다** — 로그는 부산물이다
  3. **깨진 줄 하나가 전부를 잃게 하지 않는다**
"""

from __future__ import annotations

import datetime as dt

from arc.store.events import (
    ASKED,
    EDITED,
    OPENED,
    PEER_SKIPPED,
    SAFE_MASKED,
    SAFE_PLACEHOLDER,
    Event,
    EventStore,
    summarize,
)


class TestInvariant:
    """불변식 1을 여기서 깨면 안 된다."""

    def test_raw_text_is_refused(self, tmp_path):
        """**가리지 않은 텍스트는 애초에 안 받는다.**

        통과시키면 언젠가 맥락 조립을 타고 프롬프트에 닿고, LLM이 값을 본다.
        """
        store = EventStore(tmp_path)
        assert store.record(Event(kind=EDITED, text="매출은 5,363억원이다")) is False
        assert store.read() == []

    def test_masked_text_is_accepted_and_actually_masked(self, tmp_path):
        store = EventStore(tmp_path)
        assert store.note_text(ASKED, "한화오션 영업이익 5,363억원 맞아?", subject="042660")
        got = store.read()[0]
        assert got.safe == SAFE_MASKED
        assert "5,363" not in got.text
        assert "한화오션" in got.text  # **말은 남는다** — 가리는 것은 숫자뿐이다

    def test_assembled_text_needs_no_masking(self, tmp_path):
        """조립본은 `{{num:key}}` 꼴이라 **구조적으로** 값이 없다."""
        store = EventStore(tmp_path)
        assert store.note_draft(EDITED, "매출은 {{num:revenue_2025a}}이다", subject="c1")
        got = store.read()[0]
        assert got.safe == SAFE_PLACEHOLDER
        assert "{{num:revenue_2025a}}" in got.text

    def test_an_unknown_kind_is_refused(self, tmp_path):
        """오타로 만든 종류는 영영 안 세어진다 — 그때 조용하면 안 된다."""
        store = EventStore(tmp_path)
        assert store.record(Event(kind="opnened")) is False


class TestDurability:
    def test_recording_never_raises(self, tmp_path):
        """**기록은 본 일의 부산물이다.** 못 써서 리포트 생성이 막히면 안 된다."""
        blocked = tmp_path / "events.jsonl"
        blocked.mkdir()  # 파일 자리에 디렉터리 — 쓰기가 반드시 실패한다
        store = EventStore(tmp_path)
        assert store.note(OPENED, "005930") is False  # 예외가 아니라 False

    def test_a_broken_line_does_not_lose_the_rest(self, tmp_path):
        store = EventStore(tmp_path)
        store.note(OPENED, "005930")
        with store.path.open("a", encoding="utf-8") as fh:
            fh.write("{깨진 줄\n")
        store.note(OPENED, "000660")
        assert [e.subject for e in store.read()] == ["000660", "005930"]

    def test_reading_an_absent_log_is_empty_not_an_error(self, tmp_path):
        assert EventStore(tmp_path).read() == []

    def test_newest_first(self, tmp_path):
        store = EventStore(tmp_path)
        for s in ("a", "b", "c"):
            store.note(OPENED, s)
        assert [e.subject for e in store.read()] == ["c", "b", "a"]

    def test_since_cuts_the_tail(self, tmp_path):
        store = EventStore(tmp_path)
        store.record(Event(kind=OPENED, subject="옛것", at="2026-01-01T00:00:00+00:00"))
        store.record(Event(kind=OPENED, subject="새것", at="2026-08-08T00:00:00+00:00"))
        cut = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
        assert [e.subject for e in store.read(since=cut)] == ["새것"]


class TestSummary:
    def _events(self) -> list[Event]:
        return [
            Event(kind=OPENED, subject="005930"),
            Event(kind=OPENED, subject="005930"),
            Event(kind=OPENED, subject="000660"),
            Event(kind=EDITED, subject="c1", detail={"section": "3. 실적"}),
            Event(kind=EDITED, subject="c2", detail={"section": "3. 실적"}),
            Event(kind=EDITED, subject="c3", detail={"section": "5. 추정"}),
            Event(kind=ASKED, subject="042660"),
            Event(kind=ASKED, subject="042660"),
            Event(kind=ASKED, subject="005930"),
            Event(kind=PEER_SKIPPED, subject="005935"),
        ]

    def test_focus_is_what_you_keep_opening(self):
        assert summarize(self._events()).focus[0] == ("005930", 2)

    def test_edits_point_at_weak_sections(self):
        """**편집이 몰린 곳이 생성이 약한 자리다.**"""
        assert summarize(self._events()).edited_sections[0] == ("3. 실적", 2)

    def test_asking_twice_is_a_signal_asking_once_is_not(self):
        """한 번 물은 것은 그냥 질문이고, 두 번은 **답이 부족했다**는 뜻이다."""
        got = summarize(self._events()).repeated
        assert got == [("042660", 2)]

    def test_skipped_peers_are_counted(self):
        """제안했는데 안 넣은 것 — 「이건 내 피어가 아니다」."""
        assert summarize(self._events()).skipped_peers == [("005935", 1)]

    def test_nothing_is_empty_not_zeroes(self):
        assert summarize([]).empty is True
