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

import collections
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


# 국내 리포트는 첫 장에 「종목명 (123456)」을 거의 반드시 쓴다.
_PAREN_CODE = re.compile(r"\((\d{6})\)")
_BARE_CODE = re.compile(r"(?<!\d)(\d{6})(?!\d)")

# 이만큼만 본다. 뒤로 갈수록 표의 숫자가 6자리로 우연히 걸린다.
# 실측(코퍼스 150편): 2,000자 적중 82% / **4,000자 92%** / 8,000자 92%.
_CODE_WINDOW = 4000


def detect_symbol(markdown: str, listed: set[str] | frozenset[str]) -> str | None:
    """문서에서 종목코드를 읽는다. 못 찾거나 확신이 없으면 None.

    **상장 종목코드 목록으로 검증한다.** 리포트에는 전화번호·사업자번호·날짜가
    6자리로 널려 있어서, 모양만 보면 엉뚱한 것을 집는다. DART 고유번호 표에
    있는 코드만 받는다.

    괄호 표기를 먼저 본다 — 「HMM (011200)」이 제목 형식이라 가장 믿을 만하다.
    없으면 맨 숫자로 물러난다.

    실측(코퍼스 150편, 파일명이 정답): **적중 92% · 오답 4% · 못 찾음 3%**.
    오답이 남으므로 **화면이 사람에게 확인을 받아야 한다.**
    """
    head = markdown[:_CODE_WINDOW]
    for rx in (_PAREN_CODE, _BARE_CODE):
        counts = collections.Counter(c for c in rx.findall(head) if c in listed)
        if counts:
            return counts.most_common(1)[0][0]
    return None


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


# ── 영역별 대조 (D64) ────────────────────────────────────────────────

AREAS_SYSTEM = """\
당신은 한국 증권사 리서치센터의 애널리스트입니다. **직전 리포트가 본 그림이
이번 공시로 유지되는가**를 영역별로 대조합니다.

## 왜 이 일을 하는가

RA가 새 분기 노트를 열고 가장 먼저 묻는 것은 「지난번 내 그림이 아직 맞나」
입니다. 숫자가 얼마 바뀌었는지는 표가 이미 보여 줍니다. 당신이 하는 것은
**무엇이 달라졌는가**입니다.

## 절대 규칙 — 숫자

**숫자를 일절 쓰지 마십시오.** 금액·성장률·비율·연도 어느 것도 쓰지 않습니다.
직전 리포트의 숫자는 우리가 검산하지 않았고, 이번 공시 숫자는 표에 이미
있습니다. 숫자가 하나라도 있으면 이 대조는 통째로 버려집니다.

「늘었다/줄었다/유지됐다/뒤집혔다」로 씁니다.

## 영역

직전 리포트에서 **실제로 다룬 영역만** 고르십시오. 없는 영역을 지어내지
마십시오. 보통 이런 것들입니다:

- 실적 추이 · 수익성(마진) · 사업 구조와 부문 · 재무 안정성
- 성장 동력 · 리스크 · 밸류에이션 근거

## 판정

각 영역을 넷 중 하나로 판정하십시오:

- `유지` — 직전 그림이 이번 공시와 어긋나지 않는다
- `강화` — 직전 그림이 이번 공시로 더 뒷받침된다
- `약화` — 직전 그림과 이번 공시가 어긋나는 방향이다
- `확인불가` — 공시만으로는 판정할 수 없다 (뉴스·업황 근거인 경우)

**확인불가를 두려워하지 마십시오.** 공시 밖 주장은 그렇게 표시하는 것이 맞습니다.

## 출력 형식

아래 JSON만 출력하십시오. 3~6개 영역.

{"areas": [{"area": "수익성", "prior": "직전 리포트가 본 것 (한 문장)",
            "now": "이번 공시가 말하는 것 (한 문장)", "verdict": "유지|강화|약화|확인불가"}]}"""

_VERDICTS = ("유지", "강화", "약화", "확인불가")


@dataclass
class AreaDiff:
    """영역 하나의 대조."""

    area: str
    prior: str
    now: str
    verdict: str


def build_areas_prompt(company: str, prior_markdown: str, observations: list[str]) -> str:
    parts = [f"# 대상\n{company}\n"]
    parts.append(
        "# 직전 리포트 (사용자가 올린 것 · 숫자는 가려져 있습니다)\n" + prior_markdown[:_HEAD_CHARS]
    )
    if observations:
        parts.append(
            "\n# 이번 공시에서 확인된 것 (결정적 계산 결과)\n"
            + "\n".join(f"- {o}" for o in observations[:14])
        )
    parts.append(
        "\n# 과업\n직전 리포트가 본 그림이 이번 공시로 유지되는지 영역별로 "
        "대조하십시오. **숫자는 쓰지 않습니다.** JSON만 출력합니다."
    )
    return "\n".join(parts)


def compare_areas(
    llm: object | None,
    prior_markdown: str,
    observations: list[str],
    *,
    company: str = "",
) -> tuple[list[AreaDiff], list[str]]:
    """직전 리포트 대비 **영역별** 대조. `(영역 목록, 문제 목록)`.

    숫자 비교(D46)는 「얼마가 달라졌나」를 말한다. 이 함수는 **「무엇이
    달라졌나」**를 말한다 — RA가 새 분기 노트를 열고 처음 묻는 것이 그쪽이다.

    **숫자가 하나라도 있으면 통째로 버린다.** 직전 리포트의 숫자는 우리가
    검산하지 않았고([D48](../../../docs/decisions.md#d48)), 이번 공시 숫자는
    표에 이미 있다. 여기서 숫자를 쓰면 검산 안 된 값이 검산된 것처럼 섞인다.
    """
    if llm is None or not prior_markdown.strip():
        return [], ["직전 리포트가 없어 영역 대조를 만들지 않았습니다."]

    from arc.llm.number_registry import mask_numbers

    try:
        completion = llm.complete(
            system=AREAS_SYSTEM,
            user=build_areas_prompt(company, mask_numbers(prior_markdown), observations),
            tier=Tier.WRITE,
        )
        payload = json.loads(_json_of(completion.text))
    except Exception as exc:  # noqa: BLE001 — provider별 예외가 다르다
        return [], [f"영역 대조를 만들지 못했습니다 ({type(exc).__name__})."]

    if not isinstance(payload, dict):
        return [], ["영역 대조 응답 형식이 맞지 않습니다."]

    out: list[AreaDiff] = []
    problems: list[str] = []
    for raw in payload.get("areas") or []:
        if not isinstance(raw, dict):
            continue
        area = str(raw.get("area") or "").strip()
        prior = str(raw.get("prior") or "").strip()
        now = str(raw.get("now") or "").strip()
        verdict = str(raw.get("verdict") or "").strip()
        if not (area and prior and now):
            continue
        if verdict not in _VERDICTS:
            verdict = "확인불가"
        digit = re.search(r"\d", f"{area} {prior} {now}")
        if digit:
            problems.append(f"「{area}」에 숫자가 있어 버렸습니다: {digit.group(0)!r}")
            continue
        out.append(AreaDiff(area=area, prior=prior, now=now, verdict=verdict))
    if not out and not problems:
        problems.append("대조할 영역을 찾지 못했습니다.")
    return out, problems
