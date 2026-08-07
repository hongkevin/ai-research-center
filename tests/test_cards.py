"""카드 저장소 — 작업 중인 리포트가 지속되는가.

생성물이 메모리에 30분만 있으면 화면은 "한 번에 끝내야 하는" 모양을 벗어날 수
없다. 이 파일이 지키는 건 카드가 살아남는지와, **자동 판정이 정상을 확인 필요로
올리지 않는지**다.
"""

from __future__ import annotations

import json

from arc.store.cards import (
    DRAFT,
    HANDOFF,
    PEER,
    REVIEW,
    SINGLE,
    Card,
    CardStore,
    attention_reasons,
    column_for,
    now_iso,
    peer_attention_reasons,
    peer_member,
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

    def test_blocked_gate_says_what_it_means(self):
        """**`G0`는 내부 코드명이다.** 화면에 나가면 안 된다 (D51)."""
        vm = _vm(gate_passed=False, violations=[{"rule": "unregistered_number"}])
        got = attention_reasons(vm)
        assert any("내보낼 수 없습니다" in r and "1건" in r for r in got)
        assert not any("G0" in r for r in got)

    def test_the_gate_is_not_reported_twice(self):
        """옛 문구는 「G0 차단 1건」과 「발간 전 점검 실패 — 차단 1건」을 둘 다 냈다."""
        vm = _vm(
            gate_passed=False,
            violations=[{}],
            stages=[_stage("failed", label="발간 전 점검", note="차단 1건 — 발간할 수 없습니다.")],
        )
        assert len(attention_reasons(vm)) == 1

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
        got = attention_reasons(vm)
        # **소수점 넷째 자리는 사람이 읽는 숫자가 아니다.**
        assert any("8%" in r for r in got)
        assert not any("8.2000" in r for r in got)
        # 그리고 **뭘 하면 되는지**가 붙는다
        assert any("확인하십시오" in r for r in got)

    def test_internal_qa_stages_are_not_shown(self):
        """「관점 분석」은 우리 내부 QA다. 시스템이 자기 사정을 문학적으로
        말하는 문장이 카드에 뜨면 안 된다 (D51)."""
        vm = _vm(
            stages=[
                _stage(
                    "partial",
                    label="관점 분석",
                    checks=[{"label": "집중", "value": "가리지 못했다", "ok": False}],
                )
            ]
        )
        assert attention_reasons(vm) == []

    def test_coverage_note_alone_does_not_stop_a_card(self):
        """「미확인 계정」은 알림이지 멈춰 세울 일이 아니다."""
        vm = _vm(stages=[_stage("partial", label="지표 추출", note="미확인 계정: 희석주당이익")])
        assert attention_reasons(vm) == []


class TestColumn:
    """**칸은 사람이 옮긴다** (D51).

    예전에는 게이트 통과 여부로 자동 판정했다. 그러면 칸이 「기계의 상태」를
    말하지 「내가 어디까지 봤는가」를 말하지 않는다. 어닝시즌에 여덟 종목이
    굴러갈 때 RA가 알고 싶은 것은 후자다.
    """

    def test_fresh_card_is_a_draft(self):
        assert column_for(_vm(), confirmed=False, published=False) == DRAFT

    def test_a_problem_does_not_move_it_by_itself(self):
        """게이트가 막혀도 칸은 그대로다 — 그건 **배지**로 낸다."""
        vm = _vm(gate_passed=False, violations=[{}])
        assert column_for(vm, confirmed=False, published=False) == DRAFT

    def test_person_starts_review(self):
        assert column_for(_vm(), confirmed=True, published=False) == REVIEW

    def test_handoff_wins(self):
        vm = _vm(gate_passed=False, violations=[{}])
        assert column_for(vm, confirmed=False, published=True) == HANDOFF


class TestLegacyColumns:
    def test_old_names_are_migrated_on_read(self, tmp_path):
        """저장된 카드가 예전 칸 이름을 들고 있다. **읽을 때 옮긴다.**"""
        s = CardStore(tmp_path)
        c = Card(id=s.new_id(), symbol="214450", year=2025, created_at=now_iso())
        c.column = "attention"
        s.save(c)
        assert s.get(c.id).column == REVIEW

    def test_old_running_column_is_not_still_running(self, tmp_path):
        """예전 `running` 칸에 있던 카드는 생성이 끝났거나 중단된 것이다."""
        s = CardStore(tmp_path)
        c = Card(id=s.new_id(), symbol="214450", year=2025, created_at=now_iso())
        c.column = "running"
        s.save(c)
        got = s.get(c.id)
        assert got.column == DRAFT and got.running is False


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


class TestPeerCard:
    """피어 카드 — 여러 종목을 한 표로.

    종목 카드에 없던 결함이 하나 생긴다: **기준 기간이 섞이는 것**. 카드
    하나짜리에서는 섞일 수가 없었다.
    """

    def _member(self, symbol, **over):
        return peer_member(symbol, **over)

    def test_old_cards_become_single_on_read(self, tmp_path):
        """`kind`가 없던 시절 카드를 이관 없이 읽는다."""
        store = CardStore(tmp_path)
        raw = {"id": "a" * 16, "symbol": "005930", "year": 2025}
        (store.dir / f"{'a' * 16}.json").write_text(json.dumps(raw), encoding="utf-8")
        card = store.get("a" * 16)
        assert card is not None
        assert card.kind == SINGLE
        assert card.members == []

    def test_a_healthy_group_needs_nothing(self):
        members = [
            self._member("047810", company="한국항공우주", year=2025, status="ready"),
            self._member("064350", company="현대로템", year=2025, status="ready"),
        ]
        assert peer_attention_reasons(members) == []

    def test_mixed_basis_is_caught(self):
        """**표가 조용히 거짓말하는 자리.** 화면상 아무 이상이 없다."""
        members = [
            self._member("047810", year=2025, period="ANNUAL", status="ready"),
            self._member("064350", year=2025, period="Q3", status="ready"),
        ]
        reasons = peer_attention_reasons(members)
        assert any("기준 기간이 섞여" in r for r in reasons)
        assert any("3분기" in r and "연간" in r for r in reasons)

    def test_pending_members_do_not_count_as_mixed(self):
        """아직 안 만들어진 것의 기간은 **정해지지 않은 것**이지 어긋난 게 아니다."""
        members = [
            self._member("047810", year=2025, period="ANNUAL", status="ready"),
            self._member("064350", status="pending"),
        ]
        reasons = peer_attention_reasons(members)
        assert not any("기준 기간이 섞여" in r for r in reasons)
        assert any("아직 준비되지 않았습니다" in r for r in reasons)

    def test_failed_member_names_the_company(self):
        members = [
            self._member("079550", company="LIG넥스원", status="failed", error="DART 조회 실패 — 없음")
        ]
        assert peer_attention_reasons(members) == [
            "LIG넥스원을(를) 가져오지 못했습니다 — DART 조회 실패"
        ]

    def test_blocked_member_is_reported(self):
        members = [
            {**self._member("047810", year=2025, status="ready"), "gate_passed": False},
            self._member("064350", year=2025, status="ready"),
        ]
        assert any("내보낼 수 없는 상태" in r for r in peer_attention_reasons(members))

    def test_peer_card_reads_members_not_vm(self):
        """피어 카드는 `vm`의 단계 진단이 아니라 구성원을 본다."""
        card = Card(
            id="b" * 16,
            symbol="",
            year=2025,
            kind=PEER,
            company="방산 4종",
            vm=_vm(gate_passed=False, violations=[{"key": "x"}]),
            members=[self._member("047810", year=2025, status="ready")],
        )
        # vm이 막혀 있어도 피어 카드에서는 그게 이유가 아니다
        assert card.attention_now() == []

    def test_summary_carries_the_group_without_the_body(self, tmp_path):
        card = Card(
            id="c" * 16,
            symbol="",
            year=2025,
            kind=PEER,
            company="방산 4종",
            members=[self._member("047810"), self._member("064350")],
        )
        s = card.summary()
        assert s["kind"] == PEER
        assert s["member_count"] == 2
        assert s["member_symbols"] == ["047810", "064350"]

    def test_roundtrip_keeps_members(self, tmp_path):
        store = CardStore(tmp_path)
        card = Card(
            id=store.new_id(),
            symbol="",
            year=2025,
            kind=PEER,
            members=[self._member("047810", company="한국항공우주", card_id="d" * 16)],
        )
        store.save(card)
        back = store.get(card.id)
        assert back is not None
        assert back.kind == PEER
        assert back.members[0]["company"] == "한국항공우주"
        assert back.members[0]["card_id"] == "d" * 16
