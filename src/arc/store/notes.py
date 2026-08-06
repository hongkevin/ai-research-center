"""발간한 노트의 **지문**과, 직전 발간 대비 무엇이 달라졌는가.

왜 필요한가
-----------
RA가 같은 종목의 새 분기 노트를 열었을 때 가장 먼저 묻는 것은 "지난번 내
노트에서 뭐가 바뀌었지?"다. [D25](../../docs/decisions.md#d25)가 *"조정 방향과
시점은 추정치 자체만큼 중요한 기록"*이라고 정한 것이 그 얘기인데, 지금까지
구현된 것은 **추정치 한 종류뿐**이었다(`compare_estimates`). 부문 구성이
바뀌어도, 최대주주 지분이 움직여도, 감사의견이 달라져도 화면에는 아무 표시가
없었다.

레지스트리에는 이미 그 전부가 있다. 발간할 때 통째로 남겨 두면 다음 발간에서
비교가 된다.

키를 어떻게 맞추는가
--------------------
레지스트리 키는 기간이 박혀 있다(`revenue_2026a`). 그대로 비교하면 분기가
바뀔 때마다 모든 키가 "새로 생김/사라짐"이 된다. 그래서 **라벨을 정규화해서**
맞춘다:

* `매출액 (2025A)` → `매출액` — 실적은 연도를 뗀다. 비교 대상이 "직전 노트의
  같은 항목"이지 "같은 해"가 아니다.
* `매출액 (2026E)` → `매출액 (2026E)` — 추정은 연도를 **남긴다.** 2026 추정과
  2027 추정은 다른 것이고, 같은 2026 추정이 어떻게 움직였는지가 핵심이다.

부문은 이름이 라벨에 들어 있어(`건설 매출 비중`) 순번이 바뀌어도 따라간다.

비교의 성격
-----------
**같은 기간을 비교하는 것이 아니다.** 2025 사업보고서 노트와 2026 1분기 노트를
비교하면 매출은 당연히 줄어 보인다(연간 vs 분기). 그래서 화면은 **무엇을
무엇과 비교했는지**를 반드시 함께 낸다 — 그 맥락 없이 증감만 보이면 오독한다.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from arc.llm.number_registry import NumberRegistry

NOTE_DATASET = "note_facts"

# 끝의 연도 표기. 두 형태가 있다:
#   `매출액 (2025A)`                 — 한 시점
#   `원가율 변화 (2024A→2025A)`      — 두 시점의 차이
# 뒤엣것을 안 걷어내면 분기가 바뀔 때마다 이름이 달라져 **전부 「신규」**로
# 뜬다. 실측: 12칸짜리 변화 목록이 이런 항목으로 다 찼다.
_SUFFIX = re.compile(r"\s*\((\d{4})\s*([AE])(?:\s*→\s*(\d{4})\s*([AE]))?\)\s*$")

# 기간에 따라 크기가 달라지는 단위. 연간 노트와 분기 노트를 비교할 때
# 이 단위의 **실적**은 비교하지 않는다 — 40조에서 10조로 줄었다고 내면
# 그건 분기가 짧아서지 회사가 나빠져서가 아니다.
_AMOUNT_UNITS = ("원", "주", "명", "건", "")

# 비중·이익률처럼 **크기가 작아도 의미가 큰** 항목. 퍼센트는 절대 변화로 본다.
_RATIO_UNITS = ("%", "pp", "%p", "배")

# 이만큼 안 움직였으면 안 바뀐 것으로 본다. 소수점 끝자리 흔들림을 「변경」으로
# 올리면 목록이 소음으로 찬다.
_EPS = 1e-9


def normalize_label(label: str | None, key: str) -> tuple[str, str]:
    """라벨 → (비교용 이름, 종류). 종류는 `actual` | `estimate` | `other`.

    실적은 연도를 뗀다 — 비교 대상이 「직전 노트의 같은 항목」이지 「같은 해」가
    아니다. 추정은 연도를 남긴다 — 2026 추정과 2027 추정은 다른 것이고, 같은
    2026 추정이 어떻게 움직였는지가 [D25](../../docs/decisions.md#d25)의 핵심이다.
    """
    if not label:
        return key, "other"
    m = _SUFFIX.search(label)
    if m is None:
        return label.strip(), "other"
    base = label[: m.start()].strip()
    # 마지막 시점이 이 항목의 성격을 정한다 (`2024A→2025E`면 추정)
    mark = m.group(4) or m.group(2)
    year = m.group(3) or m.group(1)
    if mark == "E":
        return f"{base} ({year}E)", "estimate"
    return base, "actual"


@dataclass(frozen=True)
class NoteFacts:
    """발간된 노트 하나가 주장한 것들."""

    symbol: str
    year: int
    period: str
    published_at: str  # ISO 날짜
    values: dict[str, float] = field(default_factory=dict)
    display: dict[str, str] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)
    # 항목별 성격(actual/estimate). **이름에서 다시 뽑으면 안 된다** — 이름은
    # 이미 정규화돼 연도 표기가 없어서 전부 `other`가 된다 (실측으로 밟았다).
    kinds: dict[str, str] = field(default_factory=dict)
    texts: dict[str, str] = field(default_factory=dict)  # 감사의견처럼 숫자가 아닌 것

    @property
    def label(self) -> str:
        return f"{self.year} {PERIOD_LABEL.get(self.period, self.period)}"


PERIOD_LABEL = {
    "ANNUAL": "사업보고서",
    "HALF": "반기보고서",
    "Q1": "1분기보고서",
    "Q3": "3분기보고서",
    "Q2": "2분기",
    "Q4": "4분기",
}


@dataclass(frozen=True)
class NoteChange:
    """직전 노트 대비 항목 하나의 변화."""

    name: str
    kind: str  # actual | estimate | other
    unit: str
    previous: str  # 표시 문자열
    current: str
    change_pct: float | None  # 금액류: 증감률(%)
    change_abs: float | None  # 비율류: 절대 변화(pp)
    direction: str  # 증가 | 감소 | 신규 | 사라짐 | 변경

    @property
    def is_new(self) -> bool:
        return self.direction == "신규"


def facts_from_registry(
    registry: NumberRegistry,
    *,
    symbol: str,
    year: int,
    period: str,
    published_at: dt.date,
    texts: dict[str, str] | None = None,
) -> NoteFacts:
    """레지스트리 → 노트 지문. **내부용 검산값은 뺀다** (독자에게 소음이다)."""
    values: dict[str, float] = {}
    display: dict[str, str] = {}
    units: dict[str, str] = {}
    kinds: dict[str, str] = {}
    for key, entry in registry._entries.items():
        if entry.internal:
            continue
        name, kind = normalize_label(entry.label, key)
        # 같은 이름이 둘이면 먼저 등록된 것을 남긴다 — 레지스트리 순서가
        # 파이프라인 순서이고, 앞쪽이 더 기본적인 지표다.
        if name in values:
            continue
        values[name] = float(entry.value)
        display[name] = entry.rendered()
        units[name] = entry.unit
        kinds[name] = kind
    return NoteFacts(
        symbol=symbol,
        year=year,
        period=period,
        published_at=published_at.isoformat(),
        values=values,
        display=display,
        units=units,
        kinds=kinds,
        texts=dict(texts or {}),
    )


def to_rows(facts: NoteFacts) -> list[dict]:
    """지문 → 스냅샷 행. 한 항목이 한 줄이다."""
    rows = [
        {
            "symbol": facts.symbol,
            "year": facts.year,
            "period": facts.period,
            "published_at": facts.published_at,
            "name": name,
            "kind": facts.kinds.get(name, "other"),
            "value": value,
            "display": facts.display.get(name, ""),
            "unit": facts.units.get(name, ""),
            "text": "",
            "saved_at": "",
        }
        for name, value in facts.values.items()
    ]
    rows += [
        {
            "symbol": facts.symbol,
            "year": facts.year,
            "period": facts.period,
            "published_at": facts.published_at,
            "name": name,
            "kind": "other",
            "value": None,
            "display": text,
            "unit": "",
            "text": text,
            "saved_at": "",
        }
        for name, text in facts.texts.items()
    ]
    return rows


def from_rows(rows: list[dict]) -> NoteFacts | None:
    """한 노트 분량의 행 → 지문."""
    if not rows:
        return None
    head = rows[0]
    values, display, units, kinds, texts = {}, {}, {}, {}, {}
    for r in rows:
        name = str(r.get("name") or "")
        if not name:
            continue
        display[name] = str(r.get("display") or "")
        units[name] = str(r.get("unit") or "")
        kinds[name] = str(r.get("kind") or "other")
        if r.get("text"):
            texts[name] = str(r["text"])
        elif r.get("value") is not None:
            values[name] = float(r["value"])
    return NoteFacts(
        symbol=str(head.get("symbol") or ""),
        year=int(head.get("year") or 0),
        period=str(head.get("period") or "ANNUAL"),
        published_at=str(head.get("published_at") or ""),
        values=values,
        display=display,
        units=units,
        kinds=kinds,
        texts=texts,
    )


def previous_note(
    store: object, symbol: str, *, exclude: tuple[int, str] | None = None
) -> NoteFacts | None:
    """이 종목의 **직전 발간 노트**. 없으면 None.

    `read_as_of`가 아니라 `read_history`를 쓴다 — 전자는 마지막 스냅샷 파일
    하나만 보므로, 다른 종목을 사이에 발간하면 이력이 끊긴다.

    `exclude`로 같은 (연도·기간)을 뺀다. 같은 분기를 다시 발간한 것은
    "직전 분기"가 아니라 재발간이다.
    """
    if store is None:
        return None
    try:
        rows = store.read_history(NOTE_DATASET)
    except Exception:  # noqa: BLE001 — 이력이 없어도 생성은 막지 않는다
        return None
    mine = [r for r in rows if r.get("symbol") == symbol]
    if exclude is not None:
        mine = [r for r in mine if (int(r.get("year") or 0), str(r.get("period") or "")) != exclude]
    if not mine:
        return None

    # **하루에 두 번 발간할 수 있다.** `published_at`은 날짜라 같은 날 낸 두
    # 노트가 동률이 되고, 그러면 두 노트의 행이 섞여 「직전」이 뒤죽박죽이
    # 된다 — 실측: 2025 사업보고서와 2025 1분기를 같은 날 내자 기준이 둘로
    # 섞였다. 그래서 실제로 쓴 시각(`saved_at`)을 함께 남기고 그걸로 가른다.
    def ident(r: dict) -> tuple:
        return (
            str(r.get("published_at") or ""),
            str(r.get("saved_at") or ""),
            int(r.get("year") or 0),
            str(r.get("period") or ""),
        )

    newest = max(ident(r) for r in mine)
    return from_rows([r for r in mine if ident(r) == newest])


def compare_notes(previous: NoteFacts | None, current: NoteFacts) -> list[NoteChange]:
    """직전 노트 대비 변화. **안 바뀐 것은 내지 않는다.**

    금액류는 증감률(%), 비율류(%·pp·배)는 절대 변화로 본다. 영업이익률이
    8.1%에서 9.1%로 가면 「+12.3%」가 아니라 「+1.0pp」다 — 후자가 RA의 말이다.

    **기간이 다르면 실적 금액은 비교하지 않는다.** 2025 사업보고서 노트와
    2026 1분기 노트를 비교하면 매출이 40조에서 10조로 「줄어」 보인다. 그건
    분기가 짧아서지 회사가 나빠져서가 아니다. 비율·구성비·지분·감사의견처럼
    기간에 무관한 것과, **연간 기준으로 세우는 추정**은 그대로 비교한다.

    **「신규」·「사라짐」은 내지 않는다.** 세 번 시도해서 세 번 다 쓰레기가
    나왔다:

    * 연간 노트 vs 분기 노트 — 분기에 없는 EBITDA 마진·부문 이익·배당이
      전부 「사라짐」으로 떴다.
    * 1분기 노트 vs 1분기 노트 — 2025년은 사업보고서가 이미 나와 주식수·
      배당·지분이 실렸고 2026년은 아직 안 나와서, 그 전부가 또 「사라짐」이었다.
    * 추정 — 2025 노트는 2026을, 2026 노트는 2027을 세우니 매번 신규 겸 사라짐.

    셋 다 **회사에 생긴 일이 아니라 그 시점에 공시가 얼마나 나와 있느냐**의
    문제다. 항목의 유무로 사건을 판정할 수 없다.

    구조가 바뀐 것은 `texts`(감사의견·감사인·보고부문 구성)가 「변경」으로
    잡는다. 그쪽은 양쪽 노트에 다 있을 때만 비교하므로 커버리지에 안 흔들린다.
    """
    if previous is None:
        return []
    same_period = previous.period == current.period
    out: list[NoteChange] = []

    def skip(name: str, facts: NoteFacts) -> bool:
        if same_period:
            return False
        if facts.kinds.get(name) != "actual":
            return False  # 추정은 연간 기준이라 기간과 무관하다
        return facts.units.get(name, "") in _AMOUNT_UNITS

    for name, cur in current.values.items():
        if skip(name, current):
            continue
        unit = current.units.get(name, "")
        kind = current.kinds.get(name, "other")
        if name not in previous.values:
            continue
        prev = previous.values[name]
        if abs(cur - prev) <= _EPS:
            continue
        ratio_like = unit in _RATIO_UNITS
        out.append(
            NoteChange(
                name=name,
                kind=kind,
                unit=unit,
                previous=previous.display.get(name, ""),
                current=current.display.get(name, ""),
                change_pct=(
                    None if ratio_like or abs(prev) <= _EPS else (cur - prev) / abs(prev) * 100
                ),
                change_abs=(cur - prev) if ratio_like else None,
                direction="증가" if cur > prev else "감소",
            )
        )

    for name, prev_text in previous.texts.items():
        cur_text = current.texts.get(name)
        if cur_text is not None and cur_text != prev_text:
            out.append(
                NoteChange(
                    name=name,
                    kind="other",
                    unit="",
                    previous=prev_text,
                    current=cur_text,
                    change_pct=None,
                    change_abs=None,
                    direction="변경",
                )
            )

    return out


def rank(changes: list[NoteChange], limit: int = 12) -> list[NoteChange]:
    """볼 순서. **변경(감사의견·보고부문)이 먼저, 그다음 많이 움직인 순.**

    값이 아니라 사실이 바뀐 것은 크기와 무관하게 맨 위여야 한다 — 감사의견이
    적정에서 한정으로 가면 어떤 마진 변화보다 큰 사건이다.
    """
    order = {"변경": 0}

    def weight(c: NoteChange) -> tuple[int, float]:
        bucket = order.get(c.direction, 2)
        size = abs(c.change_abs) if c.change_abs is not None else abs(c.change_pct or 0.0)
        return (bucket, -size)

    return sorted(changes, key=weight)[:limit]
