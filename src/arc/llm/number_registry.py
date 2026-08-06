"""Number Registry — 숫자는 코드만 생성한다 (ARCHITECTURE.md §4.2).

수치 환각 대응은 사후 탐지가 아니라 **경로 차단**:
  - 보고서에 등장 가능한 모든 수치는 S1~S3의 결정적 코드가 계산해
    레지스트리에 등록한다 (값 + 단위 + provenance).
  - LLM은 본문에서 숫자 리터럴 대신 플레이스홀더(`{{num:rev_2026e}}`)만 쓴다.
  - 렌더링 시 레지스트리 값으로 치환하고, 레지스트리에 없는 숫자가
    본문에 나타나면 G0에서 발간 차단한다.

플레이스홀더 문법
-----------------
`{{num:<key>}}` — 설계 문서의 `{{rev_2026e}}`에 `num:` 네임스페이스를 붙였다.

이유: 보고서 템플릿(`templates/*.j2`)이 Jinja2 `{{ }}`를 쓴다. 치환은 Jinja2
렌더링 **전에** 끝나므로 충돌하지 않지만, 네임스페이스가 있으면 코드베이스에서
`{{num:`으로 grep해 수치 플레이스홀더만 골라낼 수 있고 사람이 읽을 때도
템플릿 변수와 헷갈리지 않는다.

키 규약: `{metric}_{period}` — 예 `rev_2025a`(실적), `rev_2026e`(추정),
`op_margin_2025a`, `rev_yoy_2025a`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from pydantic import BaseModel, Field

from arc.data.base import Provenance

# {{num:key}} — 공백 허용, 키는 영숫자·언더스코어·점
PLACEHOLDER_RE = re.compile(r"\{\{\s*num:([A-Za-z0-9_.]+)\s*\}\}")

# 치환 전 리터럴 스캔에서 플레이스홀더를 가리는 마스크 (자릿수가 없어야 한다)
_MASK = "NUMTOKEN"

# ── 화이트리스트: 숫자여도 허용되는 문맥 ──────────────────────────────
# 좁게 유지한다. 애매하면 거부하고 플레이스홀더를 쓰게 하는 쪽이 안전하다.
_WHITELIST: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"FY\s?\d{2,4}"), "회계연도"),
    (re.compile(r"\d{4}\s?년도?"), "연도"),
    (re.compile(r"\d{4}\s?회계연도"), "회계연도"),
    (re.compile(r"\d{1,2}\s?Q\s?\d{0,2}"), "분기"),
    (re.compile(r"[1-4]\s?분기"), "분기"),
    (re.compile(r"제\s?\d+\s?(조|항|호|목|장|절)"), "법령·조항"),
    (re.compile(r"^\s*\d+\s*[.)]\s", re.MULTILINE), "목차 번호"),
    (re.compile(r"[1-9]\s?(개|가지|곳|명|건|차례|번째)"), "소수 개수"),
    # ── 문서 구조에서 오는 숫자 (LLM이 쓴 주장이 아니다) ──
    # 마크다운 제목 번호: "## 2. 요약", "### 1. 외형 성장"
    # 계층 번호("### 2.1 부문 구성")까지 포함해야 한다 — 안 그러면 `2.1`이
    # `\d+\.\d+`(소수) 규칙에 걸려 발간이 차단된다.
    (re.compile(r"^#{1,6}\s*\d+(?:\.\d+)*\s*[.)]?\s", re.MULTILINE), "제목 번호"),
    # 국내 종목코드: "(000000)". 회계상 음수는 6자리면 콤마가 붙으므로(123,456) 구분된다
    (re.compile(r"\(\d{6}\)"), "종목코드"),
    # 추정·실적 표기: "2026E", "2025A" (E=estimate, A=actual)
    (re.compile(r"\d{4}\s?[EAea](?![0-9])"), "추정·실적 연도 표기"),
    # ISO 날짜·타임스탬프 (작성일·조회시각 등 메타데이터. 재무 주장이 아니다)
    (
        re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:\d{2}|Z)?)?"),
        "ISO 날짜·시각",
    ),
    # ── 출처 표기에서 오는 숫자 ──────────────────────────────────────
    # URL 안의 숫자는 **주장이 아니라 주소**다. 「수치 출처」 표가 공시 원문을
    # 링크로 걸면서 필요해졌다. 금액은 콤마가 붙어(`\d{1,3}(,\d{3})+`) 별도로
    # 잡히므로 이 규칙이 크기를 흘려보내지 않는다.
    (re.compile(r"https?://\S+"), "URL"),
    # 도메인만 있는 것도 **주소지 주장이 아니다.** 기사 표의 「매체」 칸이
    # 아는 매체는 한글 이름, 모르는 곳은 도메인을 쓴다(D47). 실측: 매체가
    # `viva100.com`인 기사 하나 때문에 `100`이 미등록 숫자로 잡혀 발간이
    # 막혔다. 알려진 TLD로 끝나는 호스트명만 허용한다 — 금액은 이 모양이
    # 될 수 없다.
    (
        re.compile(
            r"(?<![\w.])(?:[a-z0-9-]+\.)+(?:co\.kr|or\.kr|go\.kr|ne\.kr|kr|com|net|org|io|news|tv)(?![\w])"
        ),
        "도메인",
    ),
    # DART 접수번호 — 14자리 연속 숫자. 금액이면 콤마가 붙는다.
    (re.compile(r"(?<!\d)\d{14}(?!\d)"), "DART 접수번호"),
]

# ── 재무 크기를 나타내는 숫자 = 반드시 잡아야 함 (high) ───────────────
_HIGH: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\d+\.\d+"), "소수"),
    (re.compile(r"\d+\s?(%|％|pp|%p|bp)"), "비율·퍼센트포인트"),
    # `배`에 \b를 붙이면 안 된다 — 한글은 유니코드 단어문자라 "25배를"에서
    # `배`와 `를` 사이에 경계가 없어 매치가 실패한다. \b는 x/X에만 필요하다.
    (re.compile(r"\d+\s?배"), "배수"),
    (re.compile(r"\d+\s?[xX]\b"), "배수"),
    (re.compile(r"[$₩€¥]\s?\d"), "통화"),
    (re.compile(r"\d+\s?(원|달러|USD|KRW|TWD|EUR)"), "통화"),
    (re.compile(r"\d+\s?(억|조|만|천억|백만|십억)"), "금액 단위"),
    (re.compile(r"\d{1,3}(,\d{3})+"), "천단위 구분"),
    (re.compile(r"\d+\s?(B|M|K|bn|mn|tn)\b"), "금액 약어"),
]


def mask_numbers(text: str, placeholder: str = "⟨수치⟩") -> str:
    """화이트리스트 밖의 숫자를 전부 가린다.

    **공시 원문을 프롬프트에 넣을 때 쓴다.** 「사업의 개요」 같은 원문에는
    우리가 등록하지 않은 숫자가 가득하고, LLM은 그걸 리터럴로 베낀다. G0가
    잡아 발간은 막히지만, 재시도를 낭비하고 문장 품질도 떨어진다. 유혹을
    애초에 없애는 편이 맞다.

    탐지(`find_unregistered_numbers`)와 **같은 화이트리스트**를 쓴다 —
    두 규칙이 갈라지면 "가렸는데 게이트에 걸리는" 상황이 생긴다.
    연도·분기·법령 조항은 남는다. 그건 사실 관계이고 게이트도 허용한다.
    """
    allowed: list[tuple[int, int]] = []
    for rx, _ in _WHITELIST:
        allowed.extend((m.start(), m.end()) for m in rx.finditer(text))

    def is_allowed(s: int, e: int) -> bool:
        return any(a <= s and e <= b for a, b in allowed)

    spans: list[tuple[int, int]] = []
    for rx, _ in _HIGH:
        for m in rx.finditer(text):
            if not is_allowed(m.start(), m.end()):
                spans.append((m.start(), m.end()))
    for m in re.finditer(r"\d+", text):
        if is_allowed(m.start(), m.end()):
            continue
        if any(s <= m.start() < e for s, e in spans):
            continue
        spans.append((m.start(), m.end()))

    out: list[str] = []
    pos = 0
    for start, end in sorted(spans):
        if start < pos:
            continue
        out.append(text[pos:start])
        out.append(placeholder)
        pos = end
    out.append(text[pos:])
    return "".join(out)


class UnregisteredNumber(BaseModel):
    """레지스트리에 없는 숫자 리터럴 1건 (G0 입력)."""

    text: str  # 검출된 문자열 (예: "3.2%")
    kind: str  # 분류 (예: "비율·퍼센트포인트")
    severity: str  # "high" | "medium"
    line: int
    excerpt: str  # 주변 문맥


class NumberEntry(BaseModel):
    """레지스트리 항목 하나: 값 + 단위 + 표시 형식 + 출처."""

    key: str  # 플레이스홀더 키 (예: "rev_2026e")
    value: float | int
    unit: str  # 예: "억원", "%", "배"
    display: str | None = None  # 렌더링 문자열 (예: "1,234억원"). 없으면 기본 포맷
    provenance: Provenance

    # 감사·검토 화면용 (§4.2 — 클릭 추적 가능해야 한다)
    label: str | None = None  # 사람이 읽을 이름 (예: "매출액 (2026E)")
    formula: str | None = None  # 계산식. 원시 수치는 None
    inputs: list[str] = Field(default_factory=list)  # 계산 입력이 된 다른 key들

    # 검산값처럼 **감사에는 필요하지만 독자에게는 소음**인 항목. 레지스트리에
    # 남아 치환·바인딩·감사 추적은 되지만 LLM 카탈로그에서는 빠진다.
    # 카탈로그에 두면 LLM이 "검산 차이는 0.0pp로 확인된다" 같은 내부 QA
    # 문장을 독자용 본문에 쓴다 (실측으로 확인됨).
    internal: bool = False

    def direction(self) -> str:
        """부호에서 뽑은 방향. 증감률·변화폭에만 의미가 있다."""
        if not any(k in self.key for k in ("_yoy_", "_chg_")):
            return "-"
        if self.value > 0:
            return "증가"
        if self.value < 0:
            return "감소"
        return "보합"

    def rendered(self) -> str:
        """치환에 쓰일 문자열."""
        if self.display is not None:
            return self.display
        if isinstance(self.value, float):
            return f"{self.value:,.1f}{self.unit}"
        return f"{self.value:,}{self.unit}"


class NumberRegistry:
    """보고서 1건에 등장 가능한 모든 수치의 단일 원천(single source of truth)."""

    def __init__(self) -> None:
        self._entries: dict[str, NumberEntry] = {}

    # ── 등록·조회 ────────────────────────────────────────────────
    def register(self, entry: NumberEntry) -> None:
        """수치 등록. 같은 key 재등록은 오류 — 값이 두 원천에서 나오면 안 된다."""
        if entry.key in self._entries:
            existing = self._entries[entry.key]
            raise ValueError(
                f"key 중복 등록: {entry.key!r} "
                f"(기존 {existing.value}{existing.unit} / 신규 {entry.value}{entry.unit}). "
                "한 수치는 하나의 원천만 가져야 한다."
            )
        self._entries[entry.key] = entry

    def register_all(self, entries: Iterable[NumberEntry]) -> None:
        for e in entries:
            self.register(e)

    def get(self, key: str) -> NumberEntry:
        """key로 항목 조회. 없으면 KeyError."""
        return self._entries[key]

    def keys(self) -> list[str]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: str) -> bool:
        return key in self._entries

    def dump(self) -> list[dict]:
        """레지스트리를 JSON으로.

        카드에 실어 **나중에 다시 게이트·치환**하기 위한 것이다. 코멘트를 받아
        문단을 고쳐 쓰려면 그 시점에 같은 레지스트리가 있어야 한다 — 없으면
        플레이스홀더를 값으로 되돌릴 수 없고, 숫자가 안 바뀌었다는 것도
        증명할 수 없다.
        """
        return [e.model_dump(mode="json") for e in self._entries.values()]

    @classmethod
    def load(cls, records: list[dict]) -> NumberRegistry:
        reg = cls()
        reg.register_all(NumberEntry.model_validate(r) for r in records)
        return reg

    def catalog(self) -> list[dict[str, str | None]]:
        """LLM 프롬프트에 넣을 카탈로그. **값(크기)은 포함하지 않는다.**

        값을 주면 LLM이 그 값을 복사해 리터럴로 쓸 수 있다. 키·라벨·단위만
        주면 플레이스홀더 외에는 쓸 방법이 없다.

        다만 **방향(부호)은 준다.** 방향은 결정적 코드가 부호에서 뽑은 사실이라
        환각이 아니고, 이것이 없으면 LLM이 모든 문장을 "변동했다"로 쓸 수밖에
        없어 읽히지 않는 글이 된다. 크기는 여전히 알 수 없다.

        `internal=True` 항목은 제외한다 — 감사용 값이지 독자용이 아니다.
        """
        return [
            {
                "key": e.key,
                "label": e.label or e.key,
                "unit": e.unit,
                "direction": e.direction(),
            }
            for e in self._entries.values()
            if not e.internal
        ]

    # ── 텍스트 처리 ──────────────────────────────────────────────
    @staticmethod
    def extract_keys(text: str) -> list[str]:
        """본문에 등장한 플레이스홀더 키를 등장 순으로 (중복 포함)."""
        return [m.group(1) for m in PLACEHOLDER_RE.finditer(text)]

    def unknown_keys(self, text: str) -> list[str]:
        """레지스트리에 없는 key를 참조하는 플레이스홀더."""
        return [k for k in self.extract_keys(text) if k not in self._entries]

    def render_text(self, text: str) -> str:
        """본문의 `{{num:key}}` 플레이스홀더를 레지스트리 값으로 치환.

        미등록 key는 치환하지 않고 그대로 둔다 — G0가 잡는다.

        치환하면서 **바로 뒤의 조사를 교정한다.** LLM은 값의 끝소리를 모른 채
        조사를 붙이므로("{{num:...}}으로") 단위에 따라 틀린다("40.0%으로").
        단위는 치환 시점에 확정되므로 이건 판단이 아니라 계산이다
        (`arc.llm.josa` 참조).
        """
        from arc.llm.josa import replace_particle

        out: list[str] = []
        pos = 0
        for m in PLACEHOLDER_RE.finditer(text):
            if m.start() < pos:  # 조사 교정으로 이미 지나친 구간
                continue
            out.append(text[pos : m.start()])
            entry = self._entries.get(m.group(1))
            if entry is None:
                out.append(m.group(0))
                pos = m.end()
                continue
            value = entry.rendered()
            out.append(value)
            particle, consumed = replace_particle(value, text[m.end() : m.end() + 3])
            out.append(particle)
            pos = m.end() + consumed
        out.append(text[pos:])
        return "".join(out)

    def bindings(self, text: str) -> list[dict[str, object]]:
        """치환 감사 기록. 어떤 플레이스홀더가 무슨 값으로 바뀌었는지."""
        out: list[dict[str, object]] = []
        for key in self.extract_keys(text):
            e = self._entries.get(key)
            if e is None:
                continue
            out.append(
                {
                    "key": key,
                    "resolved": e.rendered(),
                    "label": e.label,
                    "formula": e.formula,
                    "inputs": e.inputs,
                    "provenance": e.provenance.model_dump(mode="json"),
                }
            )
        return out

    def find_unregistered_numbers(self, text: str) -> list[UnregisteredNumber]:
        """본문에서 레지스트리에 없는 숫자 리터럴을 탐지 (G0 게이트 입력).

        발견 목록이 비어 있지 않으면 G0가 발간을 차단한다.
        플레이스홀더 내부의 숫자는 마스킹해 오탐하지 않는다.
        """
        masked = PLACEHOLDER_RE.sub(_MASK, text)

        allowed: list[tuple[int, int]] = []
        for rx, _ in _WHITELIST:
            allowed.extend((m.start(), m.end()) for m in rx.finditer(masked))

        def is_allowed(s: int, e: int) -> bool:
            return any(a <= s and e <= b for a, b in allowed)

        found: list[UnregisteredNumber] = []
        claimed: list[tuple[int, int]] = []

        def line_of(pos: int) -> int:
            return masked.count("\n", 0, pos) + 1

        def excerpt(s: int, e: int, pad: int = 28) -> str:
            a, b = max(0, s - pad), min(len(masked), e + pad)
            return masked[a:b].replace("\n", " ").replace(_MASK, "⟨수치⟩").strip()

        for rx, kind in _HIGH:
            for m in rx.finditer(masked):
                if is_allowed(m.start(), m.end()):
                    continue
                if any(s <= m.start() < e for s, e in claimed):
                    continue
                claimed.append((m.start(), m.end()))
                found.append(
                    UnregisteredNumber(
                        text=m.group(0).strip(),
                        kind=kind,
                        severity="high",
                        line=line_of(m.start()),
                        excerpt=excerpt(m.start(), m.end()),
                    )
                )

        for m in re.finditer(r"\d+", masked):
            if is_allowed(m.start(), m.end()):
                continue
            if any(s <= m.start() < e for s, e in claimed):
                continue
            claimed.append((m.start(), m.end()))
            found.append(
                UnregisteredNumber(
                    text=m.group(0),
                    kind="맨 정수",
                    severity="medium",
                    line=line_of(m.start()),
                    excerpt=excerpt(m.start(), m.end()),
                )
            )

        found.sort(key=lambda f: (f.line, f.text))
        return found
