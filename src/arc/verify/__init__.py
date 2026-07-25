"""verify — 검증 게이트 G0/G1/G2 (ARCHITECTURE.md §4.3).

  - G0 (하드, 비타협, MVP 필수): 수치 원천 대조 100%(Number Registry 강제),
    컴플라이언스 룰 필터(§3 불변식), 필수 섹션·디스클레이머 존재 확인.
  - G1 (골든셋 후): 문장 단위 grounding 검증 (주장 ↔ 원천 데이터 대응).
  - G2 (골든셋 후): LLM-as-judge 루브릭 — 사실성/논리 일관성/완결성/컴플라이언스.

게이트 실패 시 파이프라인은 S4(섹션 작성)로 반려한다.
"""

from arc.verify.g0 import G0Gate, GateResult, GateViolation

__all__ = ["G0Gate", "GateResult", "GateViolation"]
