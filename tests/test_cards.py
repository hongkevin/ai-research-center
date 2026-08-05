"""카드 저장소 — 작업 중인 리포트가 지속되는가.

생성물이 메모리에 30분만 있으면 화면은 "한 번에 끝내야 하는" 모양을 벗어날 수
없다. 이 파일이 지키는 건 카드가 살아남는지와, **자동 판정이 정상을 확인 필요로
올리지 않는지**다.
"""

from __future__ import annotations

from arc.store.cards import (
    ATTENTION,
    PUBLISHED,
    REVIEW,
    Card,
    CardStore,
    attention_reasons,
    column_for,
    now_iso,
)


def _vm(**over):
    vm = {"gate_passed": True, "violations": [], "stages": [], "error": "", "registry_size": 10}
    vm.update(over)
    return vm


def _stage(status, label="부문별 손익", checks=None, note=""):
    return {"key": "x", "label": label, "status": status, "checks": checks or [], "note": note}


class TestAttention:
    def test_clean_report_needs_nothing(self):
        assert attention_reasons(_vm()) == []

    def test_absent_is_not_a_reason(self):
        """**정상 부재를 확인 필요로 올리면 보드가 늘 빨갛다.**

        단일 부문 회사에 부문 손익이 없는 것은 정상이다 (D33이 정확히 거부한다).
        """
        vm = _vm(stages=[_stage("absent", note="단일 부문이라 전사 손익과 같습니다.")])
        assert attention_reasons(vm) == []

    def test_blocked_gate_is_a_reason(self):
        vm = _vm(gate_passed=False, violations=[{"rule": "unregistered_number"}])
        assert any("G0 차단" in r for r in attention_reasons(vm))

    def test_failed_stage_is_a_reason(self):
        vm = _vm(stages=[_stage("failed", label="정기보고서", note="HTTP 500")])
        assert any("정기보고서" in r for r in attention_reasons(vm))

    def test_reconciliation_mismatch_is_a_reason(self):
        """롯데케미칼: 부문 합계가 매출액과 +8.2% 어긋나 버려졌다. 사람이 볼 카드다."""
        vm = _vm(
            stages=[
                _stage(
                    "partial",
                    label="부문별 매출",
                    checks=[{"label": "부문 합계 vs 매출액", "value": "+8.2000%", "ok": False}],
                )
            ]
        )
        assert any("검산 불일치" in r for r in attention_reasons(vm))

    def test_coverage_note_alone_does_not_stop_a_card(self):
        """「미확인 계정」은 알림이지 멈춰 세울 일이 아니다."""
        vm = _vm(stages=[_stage("partial", label="지표 추출", note="미확인 계정: 희석주당이익")])
        assert attention_reasons(vm) == []


class TestColumn:
    def test_clean_goes_to_review(self):
        assert column_for(_vm(), confirmed=False, published=False) == REVIEW

    def test_problem_goes_to_attention(self):
        vm = _vm(gate_passed=False, violations=[{}])
        assert column_for(vm, confirmed=False, published=False) == ATTENTION

    def test_confirming_moves_it_out(self):
        """옮기는 노동을 만들지 않으면서 사람의 판단은 남긴다."""
        vm = _vm(gate_passed=False, violations=[{}])
        assert column_for(vm, confirmed=True, published=False) == REVIEW

    def test_published_wins(self):
        vm = _vm(gate_passed=False, violations=[{}])
        assert column_for(vm, confirmed=False, published=True) == PUBLISHED


class TestStore:
    def test_roundtrip(self, tmp_path):
        s = CardStore(tmp_path)
        c = Card(id=s.new_id(), symbol="214450", year=2025, created_at=now_iso(), vm=_vm())
        s.save(c)
        got = s.get(c.id)
        assert got is not None
        assert got.symbol == "214450"
        assert got.vm["registry_size"] == 10

    def test_list_is_newest_first(self, tmp_path):
        s = CardStore(tmp_path)
        for i, ts in enumerate(["2026-08-01T00:00:00+00:00", "2026-08-05T00:00:00+00:00"]):
            s.save(Card(id=s.new_id(), symbol=f"00000{i}", year=2025, created_at=ts))
        assert [c.created_at for c in s.list()] == [
            "2026-08-05T00:00:00+00:00",
            "2026-08-01T00:00:00+00:00",
        ]

    def test_broken_file_does_not_block_the_list(self, tmp_path):
        """깨진 카드 하나가 보드 전체를 막으면 안 된다."""
        s = CardStore(tmp_path)
        s.save(Card(id=s.new_id(), symbol="214450", year=2025, created_at=now_iso()))
        (s.dir / "aaaaaaaaaaaaaaaa.json").write_text("{ 깨짐", encoding="utf-8")
        assert len(s.list()) == 1

    def test_id_is_validated(self, tmp_path):
        """id는 우리가 만든 것만 받는다 — 경로 조작을 막는다."""
        import pytest

        s = CardStore(tmp_path)
        with pytest.raises(ValueError):
            s.get("../../etc/passwd")

    def test_summary_drops_the_body(self, tmp_path):
        """카드 하나에 본문 60KB가 붙는다. 목록에 실으면 보드가 무거워진다."""
        s = CardStore(tmp_path)
        c = Card(
            id=s.new_id(),
            symbol="214450",
            year=2025,
            created_at=now_iso(),
            vm=_vm(body_html="x" * 50_000),
        )
        assert "body_html" not in c.summary()
