"""eval 게이트 자체를 시험한다 (D89).

**막지 못하는 게이트는 게이트가 아니다.** 여기서 지키는 것:

* 바닥을 못 넘기면 **0이 아닌 종료 코드**가 나온다
* 바닥이 없는 측정은 **report-only**이고 실패로 세지 않는다
* 비대칭 바닥에는 `why`가 붙어 있다 — 이유 없는 0은 나중에 아무나 올린다
* 커밋된 바닥이 **지금 측정과 모순되지 않는다** (바닥을 올려 놓고 잊는 일)
"""

from __future__ import annotations

import json
from pathlib import Path

from arc.evals.result import Result, Verdict
from arc.evals.run import BASELINES, judge, load_baselines, main, render

ROOT = Path(__file__).resolve().parents[1]


class TestTheGateCanFail:
    def test_a_value_below_the_floor_fails(self):
        v = Verdict(Result("s", "m", 0.80), floor=0.90)
        assert not v.passed
        assert v.status == "FAIL"

    def test_a_value_above_the_ceiling_fails(self):
        v = Verdict(Result("s", "m", 3.0), ceiling=0.0)
        assert not v.passed

    def test_exactly_on_the_bound_passes(self):
        """**경계는 통과다.** `>=`·`<=`로 적어 놓고 `>`로 검사하면
        「0 이하」 바닥이 0에서 깨진다."""
        assert Verdict(Result("s", "m", 0.9), floor=0.9).passed
        assert Verdict(Result("s", "m", 0.0), ceiling=0.0).passed

    def test_a_metric_without_a_bound_is_report_only(self):
        """바닥이 없으면 판정도 없다 — 통과로 세되 **게이트로 세지 않는다.**"""
        v = Verdict(Result("s", "m", 0.1))
        assert v.passed
        assert not v.checked
        assert v.status == "report-only"

    def test_report_only_does_not_block_even_when_over(self):
        """승격 전 측정이 배포를 막으면, 사람이 바닥을 내려서 게이트를 장식으로 만든다."""
        v = Verdict(Result("s", "m", 99.0), ceiling=1.0, gated=False)
        assert not v.passed
        assert v.status == "over (report-only)"


class TestBaselinesFile:
    def _spec(self) -> dict:
        return json.loads(BASELINES.read_text(encoding="utf-8"))["metrics"]

    def test_every_bound_has_a_reason(self):
        """**이유 없는 바닥은 나중에 아무나 올린다.**

        flightcheck가 `injection_benign_false_positive_max: 0.0` 옆에 이유를
        적어 둔 것과 같은 규칙이다. 특히 0은 왜 「작게」가 아니라 0인지가
        적혀 있어야 한다.
        """
        for key, spec in self._spec().items():
            assert spec.get("why"), f"{key}에 why가 없습니다"
            assert len(spec["why"]) > 40, f"{key}의 why가 너무 짧습니다"

    def test_the_committed_bound_matches_the_committed_measurement(self):
        """**바닥을 올려 놓고 잊으면** 다음 사람이 이유 없이 빨간 게이트를 본다."""
        for key, spec in self._spec().items():
            measured = spec.get("measured")
            if measured is None:
                continue
            if (lo := spec.get("min")) is not None:
                assert measured >= lo, f"{key}: 기록된 측정 {measured} < 바닥 {lo}"
            if (hi := spec.get("max")) is not None:
                assert measured <= hi, f"{key}: 기록된 측정 {measured} > 천장 {hi}"

    def test_the_asymmetric_zero_is_still_zero(self):
        """산문 오탐 허용치는 **0이다.** 올리려면 이 시험을 고쳐야 하고,
        고치는 사람은 왜 0이었는지 읽게 된다."""
        spec = self._spec()["mentions.prose_false_positives"]
        assert spec["max"] == 0.0
        assert "zero" in spec["why"].lower()


class TestRunner:
    def test_judge_marks_unknown_metrics_report_only(self):
        got = judge([Result("s", "m", 1.0)], {"metrics": {}})
        assert not got[0].checked

    def test_render_puts_the_bound_next_to_the_value(self):
        table = render([Verdict(Result("mentions", "recall", 0.998), floor=0.995)])
        assert "0.998" in table and "≥ 0.995" in table

    def test_baselines_load_even_when_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr("arc.evals.run.BASELINES", tmp_path / "nope.json")
        assert load_baselines() == {}

    def test_an_unknown_suite_is_an_error_not_a_pass(self):
        """**모르는 이름을 조용히 넘기면** 오타 하나로 게이트가 통째로 꺼진다."""
        assert main(["--suite", "nonexistent"]) == 2

    def test_the_real_gate_passes_right_now(self):
        """커밋된 바닥으로 지금 저장소가 통과하는가. **여기가 빨간 채로 머물면 안 된다.**"""
        assert main([]) == 0

    def test_no_gate_never_blocks(self):
        assert main(["--no-gate"]) == 0

    def test_a_report_records_what_it_does_not_say(self, tmp_path):
        """리포트에 **한계 절**이 없으면 숫자만 인용된다."""
        out = tmp_path / "r.md"
        main(["--report", str(out)])
        text = out.read_text(encoding="utf-8")
        assert "does not say" in text.lower()
        assert "floor" in text.lower()


class TestSuiteIsWiredUp:
    def test_the_committed_report_exists(self):
        """리포트를 커밋한다 — **공개 숫자가 새 run과 다르면 게이트가 결정한다.**"""
        assert list((ROOT / "evals" / "reports").glob("*.md")), "커밋된 리포트가 없습니다"

    def test_evals_readme_states_what_is_not_covered(self):
        text = (ROOT / "evals" / "README.md").read_text(encoding="utf-8")
        assert "does not cover" in text.lower()
        assert "Retrieval" in text, "가장 큰 구멍을 이름으로 적어야 한다"
