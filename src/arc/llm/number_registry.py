"""Number Registry — 숫자는 코드만 생성한다 (ARCHITECTURE.md §4.2).

수치 환각 대응은 사후 탐지가 아니라 **경로 차단**:
  - 보고서에 등장 가능한 모든 수치는 S1~S3의 결정적 코드가 계산해
    레지스트리에 등록한다 (값 + 단위 + provenance).
  - LLM은 본문에서 숫자 리터럴 대신 플레이스홀더(`{{rev_2026e}}`)만 쓴다.
  - 렌더링 시 레지스트리 값으로 치환하고, 레지스트리에 없는 숫자가
    본문에 나타나면 G0에서 발간 차단한다.

클래스 시그니처까지 확정, 구현은 TODO(5~6주차).
"""

from __future__ import annotations

from pydantic import BaseModel

from arc.data.base import Provenance


class NumberEntry(BaseModel):
    """레지스트리 항목 하나: 값 + 단위 + 표시 형식 + 출처."""

    key: str  # 플레이스홀더 키 (예: "rev_2026e")
    value: float | int
    unit: str  # 예: "억원", "%", "배"
    display: str | None = None  # 렌더링 문자열 (예: "1,234억원"). 없으면 기본 포맷
    provenance: Provenance


class NumberRegistry:
    """보고서 1건에 등장 가능한 모든 수치의 단일 원천(single source of truth)."""

    def __init__(self) -> None:
        self._entries: dict[str, NumberEntry] = {}

    def register(self, entry: NumberEntry) -> None:
        """수치 등록. 같은 key 재등록은 오류 — 값이 두 원천에서 나오면 안 된다."""
        raise NotImplementedError("TODO(5~6주차)")

    def get(self, key: str) -> NumberEntry:
        """key로 항목 조회. 없으면 KeyError."""
        raise NotImplementedError("TODO(5~6주차)")

    def render_text(self, text: str) -> str:
        """본문의 `{{key}}` 플레이스홀더를 레지스트리 값으로 치환."""
        raise NotImplementedError("TODO(5~6주차)")

    def find_unregistered_numbers(self, text: str) -> list[str]:
        """본문에서 레지스트리에 없는 숫자 리터럴을 탐지 (G0 게이트 입력).

        발견 목록이 비어 있지 않으면 G0가 발간을 차단한다.
        """
        raise NotImplementedError("TODO(5~6주차)")
