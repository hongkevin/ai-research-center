"""eval 한 줄 — **값 하나와 그것이 무엇인지** (D89)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Result:
    """측정 하나. `note`에 **N과 한계**를 적는다 — 값만으로는 못 읽는다."""

    suite: str
    metric: str
    value: float
    note: str = ""

    @property
    def key(self) -> str:
        return f"{self.suite}.{self.metric}"


@dataclass(frozen=True)
class Verdict:
    """한 측정에 대한 판정. **바닥이 없으면 판정도 없다** — 그게 report-only다."""

    result: Result
    floor: float | None = None
    ceiling: float | None = None
    gated: bool = True

    @property
    def checked(self) -> bool:
        return self.floor is not None or self.ceiling is not None

    @property
    def passed(self) -> bool:
        if not self.checked:
            return True
        v = self.result.value
        if self.floor is not None and v < self.floor:
            return False
        return not (self.ceiling is not None and v > self.ceiling)

    @property
    def status(self) -> str:
        if not self.checked:
            return "report-only"
        if not self.passed:
            return "FAIL" if self.gated else "over (report-only)"
        return "ok"

    @property
    def bound(self) -> str:
        if self.floor is not None:
            return f"≥ {self.floor}"
        if self.ceiling is not None:
            return f"≤ {self.ceiling}"
        return "—"
