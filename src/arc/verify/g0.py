"""G0 하드 게이트 — 비타협, MVP 필수 (ARCHITECTURE.md §4.3, §3).

검사 항목:
  1. 수치 원천 대조 100%: 본문의 모든 숫자가 Number Registry에 존재해야 한다.
  2. 컴플라이언스 룰 필터 (§3 불변식):
     - 단일 목표주가·Buy/Hold/Sell 표현 금지
     - 단정적 가치판단 표현("매수해야", "확실히 상승" 류) 차단
  3. 필수 섹션 존재: §6의 8개 섹션.
  4. 3중 디스클레이머 존재: ① 조사분석자료 아님 ② 투자권유 아님 ③ AI 생성물 표시.

게이트 인터페이스까지 확정, 구현은 TODO(5~6주차).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from arc.llm.number_registry import NumberRegistry


class GateViolation(BaseModel):
    """게이트 위반 1건."""

    rule: str  # 예: "unregistered_number", "banned_expression", "missing_section"
    detail: str  # 사람이 읽을 설명 (위반 위치·내용)


class GateResult(BaseModel):
    """게이트 실행 결과. passed=False면 발간 차단, S4로 반려."""

    gate: str  # "G0" | "G1" | "G2"
    passed: bool
    violations: list[GateViolation] = Field(default_factory=list)


class G0Gate:
    """G0 하드 게이트. 모든 검사를 통과해야 발간 가능."""

    def __init__(self, registry: NumberRegistry) -> None:
        self.registry = registry

    def check(self, report_markdown: str) -> GateResult:
        """보고서 본문(Markdown) 전체를 검사해 GateResult를 반환한다."""
        raise NotImplementedError("TODO(5~6주차)")

    def check_numbers(self, report_markdown: str) -> list[GateViolation]:
        """수치 원천 대조: 레지스트리에 없는 숫자 리터럴 → 위반."""
        raise NotImplementedError("TODO(5~6주차)")

    def check_compliance(self, report_markdown: str) -> list[GateViolation]:
        """컴플라이언스 룰 필터: 목표주가/투자의견/단정 표현 → 위반."""
        raise NotImplementedError("TODO(5~6주차)")

    def check_sections(self, report_markdown: str) -> list[GateViolation]:
        """필수 섹션(§6 8개)·3중 디스클레이머 존재 확인."""
        raise NotImplementedError("TODO(5~6주차)")
