"""업로드한 **직전 노트**에서 기준선과 구성을 뽑는다.

두 가지를 뽑는다
----------------
1. **기준선** — 그 노트가 본 추정치. 우리 공시 기준선과 어디가 갈리는지
   보여 주는 데 쓴다([D46](../../../docs/decisions.md#d46)의 비교 기계에
   그대로 물린다). 「당신은 2026 매출 12.4조를 봤는데 공시 기준선은 11.2조다」
2. **구성** — 그 노트의 섹션 차례와 길이. 우리 초안을 그 형식에 맞춰 쓴다.
   하우스 스타일이 있는 곳에서는 이게 채택을 가른다.

숫자는 어디까지 가는가
----------------------
**본문에는 안 간다.** 우리가 검산한 값이 아니다 — 기사 레인
([D45](../../../docs/decisions.md#d45))과 같다. 비교 패널에만 나오고,
「업로드 문서의 값이며 우리가 확인하지 않았다」가 항상 함께 붙는다.

왜 LLM으로 뽑는가
-----------------
하우스마다 표 모양이 다르다. 「2026F 매출액」·「26E Revenue」·「매출(십억원)」이
같은 것을 가리킨다. 정규식으로 맞추려면 하우스 수만큼 규칙이 필요하다.
LLM은 표를 읽는 데 강하고, **여기서 나온 숫자는 본문에 안 들어가므로**
불변식을 건드리지 않는다. 틀려도 비교 패널이 이상해질 뿐이다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from arc.finmodel.metrics import fmt_krw
from arc.llm.client import Tier
from arc.store.notes import NoteFacts

# 노트에서 이만큼만 읽어 프롬프트에 넣는다. 리서치 노트의 추정 표는 앞쪽에
# 있고, 뒤는 대개 재무제표 부록이다.
_HEAD_CHARS = 6000

SYSTEM = """\
당신은 증권사 리서치 노트에서 **추정치 표**를 읽어내는 추출기입니다.

## 과업

주어진 노트에서 애널리스트가 제시한 **연도별 추정치**를 찾아 JSON으로
옮기십시오. 실적(과거 확정치)이 아니라 **추정치**입니다 — 보통 `2026F`,
`2026E`, `26E` 처럼 표시됩니다.

## 규칙

- 노트에 **있는 것만** 옮기십시오. 없으면 그 항목을 넣지 마십시오.
- 단위를 반드시 원 단위 숫자로 환산하십시오. 「십억원」 표기면 ×1,000,000,000,
  「억원」이면 ×100,000,000입니다. 환산이 확실하지 않으면 그 항목을 버리십시오.
- 목표주가·투자의견이 있으면 함께 옮기십시오. 없으면 null입니다.
- 추측하지 마십시오. **비어 있는 것이 틀린 것보다 낫습니다.**

## 출력 형식

아래 JSON만 출력하십시오.

{"target_price": 정수 또는 null,
 "rating": "문자열 또는 null",
 "estimates": [{"year": 2026, "revenue": 정수 또는 null,
                "operating_income": 정수 또는 null, "net_income": 정수 또는 null}]}"""


@dataclass
class PriorNote:
    """업로드한 직전 노트에서 읽어낸 것."""

    source_name: str
    outline: list[str] = field(default_factory=list)  # 섹션 차례
    target_price: int | None = None
    rating: str | None = None
    estimates: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return bool(self.estimates or self.target_price or self.outline)


def outline_of(markdown: str, limit: int = 20) -> list[str]:
    """마크다운 → 섹션 차례. **LLM 없이 된다** — 제목은 이미 구조다."""
    out: list[str] = []
    for line in markdown.split("\n"):
        m = re.match(r"^(#{2,4})\s+(.+?)\s*$", line)
        if m is None:
            continue
        title = m.group(2).strip()
        # 쪽 구분 주석이나 한 글자짜리는 차례가 아니다
        if len(title) < 2 or title.startswith("<!--"):
            continue
        if title in out:
            continue
        out.append(title)
        if len(out) >= limit:
            break
    return out


def _coerce_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        digits = re.sub(r"[^\d-]", "", value)
        return int(digits) if digits not in ("", "-") else None
    return None


def parse_extraction(payload: dict) -> tuple[int | None, str | None, list[dict]]:
    """LLM 응답 → (목표주가, 투자의견, 추정 목록). **모양이 이상하면 버린다.**"""
    target = _coerce_int(payload.get("target_price"))
    rating = payload.get("rating")
    rating = str(rating).strip() if isinstance(rating, str) and rating.strip() else None

    rows: list[dict] = []
    for raw in payload.get("estimates") or []:
        if not isinstance(raw, dict):
            continue
        year = _coerce_int(raw.get("year"))
        # 연도가 아니면 표를 잘못 읽은 것이다
        if year is None or not (2000 <= year <= 2100):
            continue
        row = {"year": year}
        for key in ("revenue", "operating_income", "net_income"):
            value = _coerce_int(raw.get(key))
            if value is not None:
                row[key] = value
        if len(row) > 1:
            rows.append(row)
    rows.sort(key=lambda r: r["year"])
    return target, rating, rows


def read_prior(llm: object | None, markdown: str, source_name: str) -> PriorNote:
    """직전 노트 읽기. **LLM이 없거나 실패해도 차례는 나온다.**

    차례만 있어도 「톤·구성 따라 쓰기」는 성립한다. 추정치 추출이 안 되면
    비교 패널만 비고 나머지는 그대로 간다.
    """
    note = PriorNote(source_name=source_name, outline=outline_of(markdown))
    if llm is None:
        note.problems.append("LLM을 쓰지 않아 추정치는 읽지 않았습니다. 차례만 씁니다.")
        return note
    if not markdown.strip():
        note.problems.append("문서가 비어 있습니다.")
        return note

    try:
        completion = llm.complete(
            system=SYSTEM,
            user=f"# 직전 노트\n\n{markdown[:_HEAD_CHARS]}",
            tier=Tier.WRITE,
        )
        payload = json.loads(_json_of(completion.text))
    except Exception as exc:  # noqa: BLE001 — provider별 예외가 다르다
        note.problems.append(f"추정치를 읽지 못했습니다 ({type(exc).__name__}).")
        return note

    if not isinstance(payload, dict):
        note.problems.append("추정치 응답 형식이 맞지 않습니다.")
        return note
    note.target_price, note.rating, note.estimates = parse_extraction(payload)
    if not note.estimates:
        note.problems.append("이 문서에서 연도별 추정치를 찾지 못했습니다.")
    return note


def _json_of(text: str) -> str:
    """```json 울타리를 벗긴다. 모델이 자주 씌운다."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-z]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped)
    return stripped.strip()


# 우리 레지스트리가 추정에 붙이는 라벨과 **같은 이름**이어야 비교가 물린다
# (`finmodel/estimates.py`: `f"{label} ({y}E)"`).
_METRIC_LABEL = {
    "revenue": "매출액",
    "operating_income": "영업이익",
    "net_income": "당기순이익",
}

# 우리 기간과 절대 같지 않은 값. 같으면 `compare_notes`가 실적 금액까지
# 비교하는데, 업로드 노트의 실적은 어느 기간인지 알 수 없다.
UPLOAD_PERIOD = "UPLOAD"


def as_facts(note: PriorNote, *, symbol: str, year: int) -> NoteFacts | None:
    """직전 노트 → 비교용 지문. 추정치가 없으면 None.

    **추정만 싣는다.** 업로드 노트의 실적치는 어느 기간(연간/분기/누적)인지
    알 수 없어 비교하면 오독을 만든다. 추정은 연간 기준이라 안전하다.
    """
    if not note.estimates:
        return None
    values: dict[str, float] = {}
    display: dict[str, str] = {}
    units: dict[str, str] = {}
    kinds: dict[str, str] = {}
    for row in note.estimates:
        y = row["year"]
        for metric, label in _METRIC_LABEL.items():
            value = row.get(metric)
            if value is None:
                continue
            name = f"{label} ({y}E)"
            values[name] = float(value)
            display[name] = fmt_krw(int(value)) or str(value)
            units[name] = "원"
            kinds[name] = "estimate"
    if not values:
        return None
    return NoteFacts(
        symbol=symbol,
        year=year,
        period=UPLOAD_PERIOD,
        published_at=note.source_name,
        values=values,
        display=display,
        units=units,
        kinds=kinds,
    )
