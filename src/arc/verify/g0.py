"""G0 하드 게이트 — 비타협, MVP 필수 (ARCHITECTURE.md §4.3, §3).

검사 항목:
  1. 수치 원천 대조 100%: 본문의 모든 숫자가 Number Registry에 존재해야 한다.
  2. 컴플라이언스 룰 필터 (§3 불변식):
     - 단일 목표주가·Buy/Hold/Sell 표현 금지 (D4)
     - 단정적 가치판단 표현("매수해야", "확실히 상승" 류) 차단
     - 손실보전·이익보장 표현 차단
  3. 필수 섹션 존재: §6의 8개 섹션.
  4. 3중 디스클레이머 존재: ① 조사분석자료 아님 ② 투자권유 아님 ③ AI 생성물 표시.

실행 시점 — **치환 전 조립본**을 검사한다
--------------------------------------
    S4  섹션 본문 생성 (플레이스홀더 포함)
     ↓
    S6a Jinja2 템플릿 조립 (플레이스홀더는 변수 '값'이라 그대로 살아남는다)
     ↓
    S5  G0.check(조립본)      ← 여기. 4개 검사가 모두 성립한다
     ↓
    S6b NumberRegistry.render_text() 로 치환 → 최종본

치환 후에 검사하면 등록된 수치와 환각 리터럴을 구분할 수 없다. 반대로 조립
전에 검사하면 섹션·디스클레이머(템플릿 소유)를 확인할 수 없다. 조립 후·치환
전이 네 검사가 동시에 성립하는 유일한 시점이다.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from arc.llm.number_registry import NumberRegistry

# ── §3 불변식 1 — 단일 목표주가·투자의견 금지 (D4) ────────────────────
_BANNED_OPINION: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"목표\s?주가|목표가|적정\s?주가|Target\s?Price", re.IGNORECASE), "단일 목표주가"),
    (re.compile(r"투자\s?의견|투자\s?등급"), "투자의견"),
    (
        re.compile(
            r"\b(Strong\s?Buy|Must\s?Buy|Buy|Sell|Hold|Overweight|Underweight)\b", re.IGNORECASE
        ),
        "rating",
    ),
    (re.compile(r"비중\s?(확대|축소)"), "rating"),
    (re.compile(r"상승\s?여력|하락\s?여력|upside|downside", re.IGNORECASE), "기대수익률"),
]

# ── §3 불변식 4 — 단정적 가치판단 표현 차단 ──────────────────────────
_BANNED_ASSERTION: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"반드시|틀림없이|무조건|확실히"), "단정 표현"),
    (re.compile(r"(매수|매도)\s?(하세요|해야|추천|권장|기회)"), "투자권유"),
    (re.compile(r"사야\s?(한다|합니다)|팔아야\s?(한다|합니다)"), "투자권유"),
    (re.compile(r"원금\s?보장|손실\s?(없|걱정\s?없)|수익\s?보장"), "손실보전·이익보장"),
    (re.compile(r"급등|폭등|대박"), "선정적 표현"),
]

# ── §6 필수 섹션 (템플릿 8개) ────────────────────────────────────────
_REQUIRED_SECTIONS: list[tuple[str, re.Pattern[str]]] = [
    ("요약", re.compile(r"^#{1,3}\s*\d*\.?\s*요약", re.MULTILINE)),
    # 사업 이해 — 원문 조회가 실패해도 섹션 자체는 렌더된다(내용에 사유를 적는다).
    # 필수로 두는 이유: 회사가 무엇을 하는지 없는 리포트는 종목 리포트가 아니다.
    ("사업 이해", re.compile(r"^#{1,3}\s*[\d.]*\s*사업\s?이해", re.MULTILINE)),
    ("투자포인트", re.compile(r"^#{1,3}\s*\d*\.?\s*투자\s?포인트", re.MULTILINE)),
    ("실적 분석", re.compile(r"^#{1,3}\s*\d*\.?\s*실적\s?분석", re.MULTILINE)),
    ("실적 추정", re.compile(r"^#{1,3}\s*\d*\.?\s*실적\s?추정", re.MULTILINE)),
    ("밸류에이션", re.compile(r"^#{1,3}\s*\d*\.?\s*밸류에이션", re.MULTILINE)),
    ("리스크 요인", re.compile(r"^#{1,3}\s*\d*\.?\s*리스크", re.MULTILINE)),
    ("디스클레이머", re.compile(r"^#{1,3}\s*\d*\.?\s*디스클레이머", re.MULTILINE)),
]

# ── §3 불변식 3 — 3중 디스클레이머 ───────────────────────────────────
_REQUIRED_DISCLAIMERS: list[tuple[str, re.Pattern[str]]] = [
    ("조사분석자료 아님", re.compile(r"조사\s?분석\s?자료가?\s?아닙니다|조사분석자료가?\s?아님")),
    (
        "투자권유 아님",
        re.compile(r"투자\s?권유가?\s?아니며|투자\s?권유가?\s?아닙니다|투자\s?권유가?\s?아님"),
    ),
    ("AI 생성물 표시", re.compile(r"AI\s?\(?인공지능\)?를?\s?활용|AI\s?생성|인공지능을?\s?활용")),
]


class GateViolation(BaseModel):
    """게이트 위반 1건."""

    rule: str  # 예: "unregistered_number", "banned_expression", "missing_section"
    detail: str  # 사람이 읽을 설명 (위반 위치·내용)
    severity: str = "high"
    line: int | None = None


class GateResult(BaseModel):
    """게이트 실행 결과. passed=False면 발간 차단, S4로 반려."""

    gate: str  # "G0" | "G1" | "G2"
    passed: bool
    violations: list[GateViolation] = Field(default_factory=list)

    def summary(self) -> str:
        if self.passed:
            return f"{self.gate} 통과"
        by_rule: dict[str, int] = {}
        for v in self.violations:
            by_rule[v.rule] = by_rule.get(v.rule, 0) + 1
        parts = ", ".join(f"{k} {n}건" for k, n in sorted(by_rule.items()))
        return f"{self.gate} 차단 — {parts}"


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


class G0Gate:
    """G0 하드 게이트. 모든 검사를 통과해야 발간 가능."""

    def __init__(self, registry: NumberRegistry) -> None:
        self.registry = registry

    def check(self, report_markdown: str) -> GateResult:
        """조립본(치환 전) 전체를 검사해 GateResult를 반환한다."""
        violations = (
            self.check_numbers(report_markdown)
            + self.check_compliance(report_markdown)
            + self.check_sections(report_markdown)
        )
        return GateResult(gate="G0", passed=not violations, violations=violations)

    def check_numbers(self, report_markdown: str) -> list[GateViolation]:
        """수치 원천 대조: 레지스트리에 없는 숫자 리터럴 → 위반."""
        out: list[GateViolation] = []

        for key in self.registry.unknown_keys(report_markdown):
            out.append(
                GateViolation(
                    rule="unknown_placeholder",
                    detail=f"레지스트리에 없는 key 참조: {{{{num:{key}}}}}",
                )
            )

        for n in self.registry.find_unregistered_numbers(report_markdown):
            out.append(
                GateViolation(
                    rule="unregistered_number",
                    detail=f"미등록 숫자 {n.text!r} ({n.kind}) — …{n.excerpt}…",
                    severity=n.severity,
                    line=n.line,
                )
            )
        return out

    def check_compliance(self, report_markdown: str) -> list[GateViolation]:
        """컴플라이언스 룰 필터: 목표주가/투자의견/단정 표현 → 위반."""
        out: list[GateViolation] = []

        # 디스클레이머 안의 "투자권유가 아니며" 는 정상 문구다. 해당 섹션은 제외한다.
        body = re.split(
            r"^#{1,3}\s*\d*\.?\s*디스클레이머", report_markdown, maxsplit=1, flags=re.MULTILINE
        )[0]

        for rx, kind in _BANNED_OPINION:
            for m in rx.finditer(body):
                out.append(
                    GateViolation(
                        rule="banned_opinion",
                        detail=f"{kind} 표현 {m.group(0).strip()!r} — D4 위반 (적정가치 범위만 허용)",
                        line=_line_of(body, m.start()),
                    )
                )
        for rx, kind in _BANNED_ASSERTION:
            for m in rx.finditer(body):
                out.append(
                    GateViolation(
                        rule="banned_expression",
                        detail=f"{kind} {m.group(0).strip()!r} — §3 불변식 4 위반",
                        line=_line_of(body, m.start()),
                    )
                )
        return out

    def check_sections(self, report_markdown: str) -> list[GateViolation]:
        """필수 섹션(§6)·3중 디스클레이머 존재 확인."""
        out: list[GateViolation] = []
        for name, rx in _REQUIRED_SECTIONS:
            if not rx.search(report_markdown):
                out.append(GateViolation(rule="missing_section", detail=f"필수 섹션 누락: {name}"))
        for name, rx in _REQUIRED_DISCLAIMERS:
            if not rx.search(report_markdown):
                out.append(
                    GateViolation(rule="missing_disclaimer", detail=f"디스클레이머 누락: {name}")
                )
        return out
