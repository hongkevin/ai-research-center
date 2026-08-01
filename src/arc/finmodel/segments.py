"""부문별 매출 — 사업보고서 원문에서 뽑고 **재무제표로 검산한다.**

왜 검산이 핵심인가
------------------
원문 파싱은 API보다 훨씬 깨지기 쉽다. 회사마다 표 모양이 다르고, 단위가
다르고, 소계 행의 이름이 다르다. 그래서 **파싱 결과를 믿지 않는다.**

부문 매출의 합은 손익계산서의 매출액과 같아야 한다. 이 항등식이 파서의
정확성을 외부에서 검증한다:

    Σ(부문 매출) ≈ 매출액        ← 안 맞으면 쓰지 않는다

마진 브리지([D16](../../docs/decisions.md))와 같은 원리다. 검산이 닫히지
않으면 표시하고 넘어가지 않는다.

두 번째 방어선: 부문별 **비율**의 합이 100%여야 한다. 금액과 비율이 각각
독립적으로 맞아야 통과한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from arc.data.base import Provenance
from arc.data.kr.dart_document import Section, detect_unit_scale
from arc.finmodel.metrics import MetricSet, fmt_krw, fmt_pct
from arc.llm.number_registry import NumberEntry

# 부문 합계와 매출액의 허용 오차(%). 원문은 백만원 단위 반올림이라 정확히
# 일치하지 않는다. 1%를 넘으면 표를 잘못 읽은 것이다.
RECONCILE_TOLERANCE_PCT = 1.0
# 비율 합계 허용 오차(pp)
SHARE_TOLERANCE_PP = 2.0

# 소계 행 — 부문 이름이 아니라 그룹의 합이다
_SUBTOTAL = ("계", "소계", "합계", "총계", "합 계", "소 계")
# 판매 구분 — 부문이 아니다
_CHANNEL = ("내수", "수출", "내 수", "수 출", "국내", "해외")
# 표 전체 합계 행
_GRAND_TOTAL = ("합계", "총계", "합 계", "총 계")

_NUM_RE = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?$")


def _num(cell: str) -> float | None:
    s = cell.strip().replace(" ", "")
    if not s or not _NUM_RE.match(s):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def _norm(s: str) -> str:
    return s.replace(" ", "").strip()


@dataclass(frozen=True)
class SegmentLine:
    """부문 1개의 매출."""

    name: str
    amount: int  # 원 단위
    share: float | None  # 매출 비중 (%)


@dataclass
class SegmentBreakdown:
    """부문별 매출 + 검산 결과.

    `reconciled`가 False면 **쓰지 않는다.** 표를 잘못 읽었다는 뜻이고,
    잘못 읽은 부문 구성이 리포트에 실리면 되돌릴 수 없다.
    """

    fiscal_year: int
    lines: list[SegmentLine] = field(default_factory=list)
    total: int | None = None  # 부문 합계
    revenue: int | None = None  # 손익계산서 매출액 (검산 기준)
    gap_pct: float | None = None
    share_sum: float | None = None
    reconciled: bool = False
    unit_scale: int | None = None
    rcept_no: str | None = None
    note: str = ""

    @property
    def usable(self) -> bool:
        """검산이 닫히면 쓴다. **부문이 하나여도 사실이다** — 단일 사업 구조는
        그 자체로 리포트에 실릴 정보다(SK하이닉스 = 반도체 단일)."""
        return self.reconciled and len(self.lines) >= 1

    @property
    def single_segment(self) -> bool:
        return len(self.lines) == 1

    @property
    def largest(self) -> SegmentLine | None:
        return max(self.lines, key=lambda x: x.amount) if self.lines else None

    @property
    def concentration(self) -> float | None:
        """최대 부문 비중(%). 사업 집중도 — 소형주에서 특히 중요하다."""
        big = self.largest
        if big is None or not self.total:
            return None
        return big.amount / self.total * 100.0


def _first_number_index(row: list[str]) -> int | None:
    """행에서 처음으로 숫자가 나오는 열. 라벨 열의 개수와 같다."""
    for i, cell in enumerate(row):
        if _num(cell) is not None:
            return i
    return None


def _table_shape(grid: list[list[str]]) -> tuple[int, int | None, int | None]:
    """표 구조를 **행에서 추론한다** — (라벨 열 수, 비율 열, 판매구분 열).

    회사마다 라벨 열 개수가 다르다(실측):

        파마리서치   구분 | 판매구분 | 금액 | 비율 | …          → 2
        SK하이닉스   사업부문 | 매출유형 | 품목 | 금액 | …       → 3
        셀트리온제약 사업부문 | 매출유형 | 품목 | 판매구분 | 금액 → 4

    고정 위치를 가정하면 다른 회사에서 열이 밀린다. 데이터 행 다수가 동의하는
    위치를 쓴다.
    """
    counts: dict[int, int] = {}
    for row in grid:
        idx = _first_number_index(row)
        if idx is None or idx == 0:
            continue
        if len(row) < 2:
            continue  # 단독 셀은 캡션이다 ("(단위: 백만원)")
        counts[idx] = counts.get(idx, 0) + 1
    if not counts:
        return 0, None, None
    label_width = max(counts, key=lambda k: (counts[k], -k))

    # 비율 열 — 헤더에 '비율'이 있을 때만 인정한다. 없는데 옆 칸을 비율로 읽으면
    # 전기 금액을 비율로 착각한다(셀트리온제약은 옆 칸이 제25기 금액이다).
    share_col = None
    for row in grid[:4]:
        for i, cell in enumerate(row):
            if _norm(cell) == "비율" and i > label_width - 1:
                share_col = i
                break
        if share_col is not None:
            break

    # 판매구분 열 — **내수/수출이 실제로 있는** 열만 인정한다.
    # 소계 표기만 보고 고르면 전체 합계 행 하나 때문에 부문명 열(0번)이
    # 판매구분으로 잡히고, 그러면 어떤 행도 통과하지 못한다(실측).
    #
    # 숫자에 가까운 쪽부터 본다 — 라벨이 여러 단이면 가장 안쪽이 판매구분이다
    # (셀트리온제약: 사업부문|매출유형|품목|**판매구분**).
    channel_marks = {_norm(x) for x in _CHANNEL}
    subtotal_marks = {_norm(x) for x in _SUBTOTAL}
    channel_col = None
    for c in range(label_width - 1, -1, -1):
        col = [_norm(row[c]) for row in grid if c < len(row)]
        if sum(1 for v in col if v in channel_marks) >= 1 and any(v in subtotal_marks for v in col):
            channel_col = c
            break
    return label_width, share_col, channel_col


def _is_subtotal_label(value: str) -> bool:
    """`소계`·`제품소계`처럼 **부분 일치**로 본다.

    실측: 셀트리온제약은 품목 열에 `제품소계`/`상품소계`를 쓴다. 완전 일치만
    보면 이 행들을 잎(leaf)으로 오인해 부문 소계와 함께 더하게 되고, 합계가
    매출액의 1.77배가 된다.
    """
    v = _norm(value)
    return (not v) or v in {_norm(x) for x in _SUBTOTAL} or "소계" in v


def parse_segment_rows(
    grid: list[list[str]], *, subtotal_only: bool = False, level: int | None = None
) -> list[tuple[str, float, float | None]]:
    """격자 → (부문명, 금액, 비율) 목록. **첫 라벨 열 기준으로 합산한다.**

    셀트리온제약처럼 품목 단위로 잘게 쪼개진 표는 품목이 아니라 **사업부문**이
    부문이다. 품목을 그대로 부문으로 쓰면 20줄짜리 목록이 리포트에 실린다.

    `subtotal_only`는 중간 라벨(매출유형·품목)이 비었거나 소계인 행만 센다.
    품목별 합계와 부문 소계가 **함께** 실린 표에서 둘을 다 더하면 이중
    계상된다(실측: 셀트리온제약이 매출액의 1.77배로 나왔다). 두 방식을 모두
    만들어 두고 검산이 통과하는 쪽을 쓴다.
    """
    label_width, share_col, channel_col = _table_shape(grid)
    if label_width == 0:
        return []

    totals: dict[str, float] = {}
    shares: dict[str, float] = {}
    order: list[str] = []
    for row in grid:
        if len(row) <= label_width:
            continue
        amount = _num(row[label_width])
        if amount is None or amount <= 0:
            continue
        name = _norm(row[0])
        if not name or _num(row[0]) is not None:
            continue
        if name in {_norm(x) for x in _GRAND_TOTAL}:
            continue  # 표 전체 합계는 부문이 아니다
        if channel_col is not None:
            # 판매구분이 있으면 **소계 행만** 센다. 내수·수출을 함께 세면 두 배가 된다.
            if _norm(row[channel_col]) not in {_norm(x) for x in _SUBTOTAL}:
                continue
        elif _norm(row[min(1, label_width - 1)]) in {_norm(x) for x in _SUBTOTAL}:
            continue

        stop = channel_col if channel_col is not None else label_width
        if level is not None:
            # 레벨 L에서 본다: L보다 깊은 라벨이 전부 비었거나 소계인 행만이
            # 그 레벨의 대표값이다. 잎 행과 소계 행을 함께 더하면 이중 계상된다.
            if level >= stop:
                continue
            if not all(_is_subtotal_label(row[c]) for c in range(level + 1, stop) if c < len(row)):
                continue
            name = _norm(row[level])
            if not name or _is_subtotal_label(name):
                continue
        elif subtotal_only:
            if not all(_is_subtotal_label(row[c]) for c in range(1, stop) if c < len(row)):
                continue

        if _is_subtotal_label(name) and level is None:
            continue
        if name not in totals:
            order.append(name)
            totals[name] = 0.0
        totals[name] += amount
        if share_col is not None and share_col < len(row):
            sv = _num(row[share_col])
            if sv is not None:
                shares[name] = shares.get(name, 0.0) + sv

    return [(n, totals[n], shares.get(n)) for n in order]


def build_segments(
    section: Section | None, ms: MetricSet, rcept_no: str | None = None
) -> SegmentBreakdown:
    """원문 섹션 → 부문별 매출. **검산에 실패하면 `usable=False`로 돌려준다.**"""
    y = ms.fiscal_year
    revenue = ms.get("revenue")
    out = SegmentBreakdown(fiscal_year=y, revenue=revenue, rcept_no=rcept_no)
    if section is None:
        out.note = "사업보고서에서 매출 현황 섹션을 찾지 못했다."
        return out

    scale = detect_unit_scale(section.body)
    if scale is None:
        out.note = "매출 표의 금액 단위를 읽지 못해 사용하지 않는다."
        return out
    out.unit_scale = scale

    best: SegmentBreakdown | None = None
    # 후보 순서: 단순 방식(0열 기준) → 레벨별. 거친 레벨을 먼저 본다 —
    # 부문이 품목보다 리포트에 쓸모 있고, 어느 쪽이든 검산이 최종 판정한다.
    candidates: list[tuple[list[list[str]], int | None]] = []
    for grid in section.tables():
        candidates.append((grid, None))
        candidates.extend((grid, lvl) for lvl in range(4))
    for grid, level in candidates:
        rows = parse_segment_rows(grid, level=level)
        rows = [(n, a, s) for n, a, s in rows if n and a > 0]
        if not rows:
            continue
        lines = [SegmentLine(name=n, amount=round(a * scale), share=s) for n, a, s in rows]
        total = sum(x.amount for x in lines)
        shares = [x.share for x in lines if x.share is not None]
        share_sum = sum(shares) if shares else None

        cand = SegmentBreakdown(
            fiscal_year=y,
            lines=lines,
            total=total,
            revenue=revenue,
            share_sum=share_sum,
            unit_scale=scale,
            rcept_no=rcept_no,
        )
        if revenue:
            cand.gap_pct = (total - revenue) / revenue * 100.0
        # 금액과 비율이 **각각** 맞아야 통과한다
        amount_ok = cand.gap_pct is not None and abs(cand.gap_pct) <= RECONCILE_TOLERANCE_PCT
        share_ok = share_sum is None or abs(share_sum - 100.0) <= SHARE_TOLERANCE_PP
        cand.reconciled = bool(amount_ok and share_ok)
        if cand.reconciled:
            return cand
        if best is None or (
            cand.gap_pct is not None and abs(cand.gap_pct) < abs(best.gap_pct or 1e9)
        ):
            best = cand

    if best is None:
        out.note = "매출 표에서 부문 행을 인식하지 못했다."
        return out
    best.note = (
        f"부문 합계가 매출액과 {best.gap_pct:+.1f}% 어긋나 사용하지 않는다."
        if best.gap_pct is not None
        else "매출액을 확인하지 못해 검산할 수 없다."
    )
    return best


# ── Number Registry · 논지 ───────────────────────────────────────────
def _key(name: str, index: int) -> str:
    """부문명은 한글·공백이 섞여 키로 못 쓴다. 순번으로 안정화한다."""
    return f"segment{index + 1}"


def build_segment_entries(seg: SegmentBreakdown, prov: Provenance) -> list[NumberEntry]:
    if not seg.usable:
        return []
    y = seg.fiscal_year
    out: list[NumberEntry] = []
    for i, line in enumerate(seg.lines):
        base = _key(line.name, i)
        out.append(
            NumberEntry(
                key=f"{base}_revenue_{y}a",
                value=line.amount,
                unit="원",
                display=fmt_krw(line.amount),
                provenance=prov,
                label=f"{line.name} 매출 ({y}A)",
            )
        )
        share = line.share if line.share is not None else line.amount / (seg.total or 1) * 100
        out.append(
            NumberEntry(
                key=f"{base}_share_{y}a",
                value=share,
                unit="%",
                display=fmt_pct(share),
                provenance=prov,
                label=f"{line.name} 매출 비중 ({y}A)",
                formula=f"{line.name} 매출 / 매출액",
                inputs=[f"{base}_revenue_{y}a", f"revenue_{y}a"],
            )
        )
    if seg.gap_pct is not None:
        out.append(
            NumberEntry(
                key=f"segment_gap_{y}a",
                value=seg.gap_pct,
                unit="%",
                display=fmt_pct(seg.gap_pct, 2),
                provenance=prov,
                label=f"부문 합계 검산 차이 ({y}A)",
                internal=True,  # 감사용 — 독자용 문장이 아니다
            )
        )
    return out


def build_segment_observations(seg: SegmentBreakdown) -> list[str]:
    """부문 논지. **크기를 쓰지 않는다** (LLM이 리터럴로 베낀다)."""
    if not seg.usable:
        return []
    names = [x.name for x in seg.lines]
    big = seg.largest
    if seg.single_segment:
        return [
            (
                f"공시된 사업부문은 {names[0]} 하나다. 전사 실적이 곧 이 사업의 실적이므로 "
                "부문 구성 변화로 설명할 여지가 없다."
            )
        ]
    obs = [
        (
            f"공시된 부문별 매출 구성은 {', '.join(names)}이다. "
            "이건 인력 구분이 아니라 사업보고서의 **매출** 표이고, 합계가 손익계산서 "
            "매출액과 일치함을 확인했다."
        )
    ]
    conc = seg.concentration
    if big is not None and conc is not None:
        if conc >= 60:
            obs.append(
                f"매출이 {big.name}에 집중돼 있다. 전사 실적은 이 부문의 흐름에 좌우되므로 "
                "이익률 변화도 이 부문을 기준으로 읽어야 한다."
            )
        elif len(names) >= 3:
            obs.append(
                "매출이 여러 부문에 나뉘어 있어 전사 지표만으로는 어느 쪽이 움직였는지 "
                "가려지지 않는다. 부문 구성 변화를 함께 봐야 한다."
            )
    return obs
