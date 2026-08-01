"""부문별 **이익** — IFRS 8 「영업부문」 주석에서 뽑고 손익계산서로 검산한다.

왜 이게 중요한가
----------------
[D28](../../docs/decisions.md)로 부문별 **매출**은 얻었지만, 리포트가 답하지
못하는 질문이 남아 있었다: **어느 부문이 돈을 버는가.** 매출 1위 부문과
이익 1위 부문이 다르면 전사 이익률은 후자의 흐름에 좌우되는데, 전사 지표만
보면 이 차이가 보이지 않는다.

기업회계기준서 제1108호는 보고부문별 손익을 주석으로 공시하도록 요구한다.
`<TE>` 셀 파싱이 열리면서 이 주석에 닿을 수 있게 됐다.

표의 모양 — 매출 표와 **전치(轉置)**돼 있다
--------------------------------------------
「4. 매출 및 수주상황」은 행이 부문이지만, 부문 주석은 **행이 계정, 열이 부문**이다::

        |          | DX 부문 | DS 부문 | … | 부문 합계 | … | 기업 전체 총계 |
        | 매출액    |  187,967 | 130,128 | … |   363,720 | … |        333,605 |
        | 영업이익  |   12,852 |  24,858 | … |    43,358 | … |         43,601 |

그래서 `segments.py`의 파서를 쓸 수 없다. 제목도 회사마다 다르다(실측):
「30. 부문별 보고」·「3. 부문별정보」·「4. 영업부문」·「41. 영업부문 정보」·「3. 부문정보」.

검산 — 총계 열이 손익계산서와 맞아야 한다
------------------------------------------
주석 표에는 **기업 전체 총계** 열이 있고, 그 값은 손익계산서와 같아야 한다.
이 둘은 완전히 다른 경로로 온다(원문 표 수작업 파싱 vs `fnlttSinglAcntAll` API).
그래서 일치하면 행·열·단위를 모두 맞게 읽었다는 뜻이다::

    총계 열의 매출액   ≈ 손익계산서 매출액
    총계 열의 영업이익 ≈ 손익계산서 영업이익      ← 둘 다 맞아야 통과

실측: 삼성전자·LG전자는 **자릿수까지 정확히** 일치했고 롯데케미칼은 18.5조에
78원 차(반올림)였다.

**부문 합계는 총계와 다르다.** 부문 매출에는 내부거래가 남아 있고(삼성전자는
9% 많다) 별도로 표시하지 않는 「기타」 부문도 있다. 그래서 Σ(부문) = 총계를
요구하지 않는다 — 요구하면 정상 공시를 거부하게 된다. 대신 Σ(부문)이 총계에
크게 못 미치면 **열을 놓친 것**이므로 그건 막는다.

단일 부문 회사는 이 주석이 없다. SK하이닉스는 「단일영업부문」이라 명시한다 —
없는 게 결함이 아니라 사실이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from arc.data.base import Provenance
from arc.data.kr.dart_document import Section, cell_number, detect_unit_scale, norm_cell
from arc.finmodel.metrics import MetricSet, fmt_krw, fmt_pct
from arc.llm.number_registry import NumberEntry

# 총계 열과 손익계산서의 허용 오차(%). 원문은 반올림 단위라 정확히 일치하지
# 않을 수 있다. 실측 최대 오차는 4e-10%였으므로 1%는 넉넉하다.
TOTAL_TOLERANCE_PCT = 1.0
# 영업이익 검산은 매출액 대비로도 본다 — 영업이익이 0에 가까우면 상대오차가
# 발산해서 정상 공시를 거부하게 된다.
OP_TOLERANCE_OF_REVENUE_PCT = 0.1

# Σ(부문 매출) ÷ 총계 매출의 허용 범위. 내부거래가 남아 있어 1을 넘는 것이
# 정상이고(실측 1.00~1.36), 1을 크게 밑돌면 부문 열을 놓친 것이다.
SEGMENT_SUM_MIN_RATIO = 0.95
SEGMENT_SUM_MAX_RATIO = 2.5

# 계정 라벨 — **완전 일치로 본다.** 부분 일치를 쓰면 「매출원가」·「매출총이익」이
# 매출 행으로 잡힌다.
_REVENUE_LABELS = frozenset(
    norm_cell(x)
    for x in ("매출액", "매출", "수익", "수익(매출액)", "영업수익", "순매출액", "매출액(수익)")
)
_PROFIT_LABELS = frozenset(
    norm_cell(x)
    for x in ("영업이익", "영업이익(손실)", "영업손익", "영업이익(영업손실)", "영업손실")
)

# 부문이 아니라 그 합인 열
_TOTAL_MARKS = frozenset(norm_cell(x) for x in ("계", "소계", "합계", "총계"))

# 단위 후보. 캡션을 먼저 믿되, 못 읽거나 캡션이 다른 표의 것이면 검산이 고른다.
_SCALE_CANDIDATES = (1, 1_000, 1_000_000, 100_000_000, 1_000_000_000)


def _is_total_label(value: str) -> bool:
    v = norm_cell(value)
    return v in _TOTAL_MARKS or "합계" in v or "소계" in v


@dataclass(frozen=True)
class SegmentProfitLine:
    """보고부문 1개의 매출과 영업이익.

    전기가 있으면 **이익률의 변화**까지 나온다 — 부문별 이익 공시에서 가장
    값진 수치다. 전사 이익률이 그대로여도 부문별로는 갈릴 수 있다.
    """

    name: str
    revenue: int
    operating_income: int
    revenue_prior: int | None = None
    op_prior: int | None = None

    @property
    def op_margin(self) -> float | None:
        if not self.revenue:
            return None
        return self.operating_income / self.revenue * 100.0

    @property
    def op_margin_prior(self) -> float | None:
        if not self.revenue_prior or self.op_prior is None:
            return None
        return self.op_prior / self.revenue_prior * 100.0

    @property
    def margin_change(self) -> float | None:
        """영업이익률 증감(pp)."""
        cur, prior = self.op_margin, self.op_margin_prior
        if cur is None or prior is None:
            return None
        return cur - prior

    @property
    def revenue_yoy(self) -> float | None:
        if not self.revenue_prior:
            return None
        return (self.revenue - self.revenue_prior) / abs(self.revenue_prior) * 100.0

    @property
    def is_loss(self) -> bool:
        return self.operating_income < 0


@dataclass
class SegmentProfitSet:
    """부문별 손익 + 검산 결과. `usable`이 False면 **쓰지 않는다.**"""

    fiscal_year: int
    lines: list[SegmentProfitLine] = field(default_factory=list)
    revenue_total: int | None = None  # 주석 총계 열
    op_total: int | None = None
    revenue_gap_pct: float | None = None  # 총계 vs 손익계산서
    op_gap_pct: float | None = None
    reconciled: bool = False
    unit_scale: int | None = None
    rcept_no: str | None = None
    section_title: str = ""
    has_prior: bool = False
    note: str = ""

    @property
    def usable(self) -> bool:
        """**부문이 둘 이상일 때만 쓴다.** 하나면 전사 손익과 같은 말이라
        표를 하나 더 실을 이유가 없다 (단일 부문이라는 사실 자체는
        `segments.py`가 이미 서술한다)."""
        return self.reconciled and len(self.lines) >= 2

    @property
    def revenue_leader(self) -> SegmentProfitLine | None:
        return max(self.lines, key=lambda x: x.revenue) if self.lines else None

    @property
    def profit_leader(self) -> SegmentProfitLine | None:
        return max(self.lines, key=lambda x: x.operating_income) if self.lines else None

    @property
    def leaders_differ(self) -> bool:
        """매출 1위와 이익 1위가 다른가. **이 노트가 답하는 새 질문이다.**"""
        r, p = self.revenue_leader, self.profit_leader
        return r is not None and p is not None and r.name != p.name

    @property
    def loss_makers(self) -> list[SegmentProfitLine]:
        return [x for x in self.lines if x.is_loss]

    @property
    def segment_op_sum(self) -> int:
        return sum(x.operating_income for x in self.lines)

    def op_share(self, line: SegmentProfitLine) -> float | None:
        """보고부문 합계 대비 이익 비중.

        **전 부문이 흑자일 때만 낸다.** 적자 부문이 섞이면 분모가 작아지거나
        부호가 뒤집혀 "이 부문이 이익의 105%"처럼 읽는 사람을 오도한다.
        """
        total = self.segment_op_sum
        if total <= 0 or any(x.is_loss for x in self.lines):
            return None
        return line.operating_income / total * 100.0

    def rev_share(self, line: SegmentProfitLine) -> float | None:
        total = sum(x.revenue for x in self.lines)
        return line.revenue / total * 100.0 if total else None


# ── 파싱 ─────────────────────────────────────────────────────────────
def read_segment_grid(
    grid: list[list[str]],
) -> tuple[list[str], list[float | None], list[float | None]] | None:
    """전치 표 → (열별 부문명, 열별 매출, 열별 영업이익).

    **매출 행과 영업이익 행이 둘 다 있어야 한다.** 이 조건 하나가 같은
    주석 안의 다른 표들(지역별 매출·주요 고객·비유동자산)을 전부 걸러낸다 —
    그 표들에는 영업이익 행이 없다.
    """
    rev_idx = op_idx = None
    for i, row in enumerate(grid):
        if not row:
            continue
        label = norm_cell(row[0])
        if rev_idx is None and label in _REVENUE_LABELS:
            rev_idx = i
        elif op_idx is None and label in _PROFIT_LABELS:
            op_idx = i
    if rev_idx is None or op_idx is None:
        return None

    names = _header_names(grid, before=min(rev_idx, op_idx))
    if not names:
        return None

    width = max(len(names), len(grid[rev_idx]), len(grid[op_idx]))

    def column(row: list[str]) -> list[float | None]:
        return [cell_number(row[c]) if c < len(row) else None for c in range(width)]

    names = names + [""] * (width - len(names))
    return names, column(grid[rev_idx]), column(grid[op_idx])


def _header_names(grid: list[list[str]], *, before: int) -> list[str]:
    """부문명 행을 고른다 — **0열이 빈 마지막 머리 행.**

    머리가 여러 단이라 위치를 고정할 수 없다. 0열이 비어 있다는 것이 머리
    행의 표식이다. LG전자는 부문명 행 아래에 「각 보고부문의 약칭」·「주요
    제품 유형」 행이 더 있는데, 그 행들은 0열에 라벨이 있어 걸러진다.
    """
    best: list[str] = []
    for row in grid[:before]:
        if not row or norm_cell(row[0]):
            continue
        if sum(1 for c in row[1:] if norm_cell(c)) < 2:
            continue
        best = [c.strip() for c in row]
    return best


def _select_columns(
    names: list[str],
    revenue: list[float | None],
    profit: list[float | None],
    *,
    scale: int,
    ref_revenue: int,
    ref_op: int,
) -> tuple[int, list[int]] | None:
    """(총계 열, 부문 열들). 검산이 통과하지 못하면 None.

    총계 열은 **매출과 영업이익이 동시에** 손익계산서와 맞는 열이다. 한쪽만
    보면 우연히 맞을 수 있지만, 서로 무관한 두 값이 같은 열에서 동시에
    맞을 확률은 무시할 수 있다. 오른쪽부터 본다 — 기업 전체 총계가 관례상
    맨 오른쪽이고, 그 왼쪽의 「부문 합계」는 내부거래가 남아 있다.
    """
    total_col = None
    for c in range(len(names) - 1, 0, -1):
        rv, pv = revenue[c], profit[c]
        if rv is None or pv is None:
            continue
        if abs(rv * scale - ref_revenue) > abs(ref_revenue) * TOTAL_TOLERANCE_PCT / 100:
            continue
        op_slack = max(
            abs(ref_op) * TOTAL_TOLERANCE_PCT / 100,
            abs(ref_revenue) * OP_TOLERANCE_OF_REVENUE_PCT / 100,
        )
        if abs(pv * scale - ref_op) > op_slack:
            continue
        total_col = c
        break
    if total_col is None:
        return None

    seg_cols: list[int] = []
    for c in range(1, total_col):
        if not norm_cell(names[c]) or _is_total_label(names[c]):
            break  # 합계 열에 닿으면 그 뒤는 조정 열이다 (부문명이 반복된다)
        if revenue[c] is None or profit[c] is None:
            break
        seg_cols.append(c)
    if len(seg_cols) < 2:
        return None

    # 부문 열을 놓치지 않았는지 — 합이 총계에 크게 못 미치면 잘못 읽은 것이다
    seg_sum = sum(revenue[c] or 0 for c in seg_cols) * scale
    if not ref_revenue:
        return None
    ratio = seg_sum / ref_revenue
    if not (SEGMENT_SUM_MIN_RATIO <= ratio <= SEGMENT_SUM_MAX_RATIO):
        return None
    return total_col, seg_cols


@dataclass(frozen=True)
class _Parsed:
    names: list[str]
    revenue: list[int]
    profit: list[int]
    revenue_total: int
    op_total: int
    scale: int
    revenue_gap_pct: float
    op_gap_pct: float


def _parse_table(
    grid: list[list[str]], *, ref_revenue: int | None, ref_op: int | None, caption_scale: int | None
) -> _Parsed | None:
    """표 하나를 기준 손익(당기 또는 전기)에 맞춰 읽는다."""
    if ref_revenue is None or ref_op is None or not ref_revenue:
        return None
    read = read_segment_grid(grid)
    if read is None:
        return None
    names, revenue, profit = read

    scales = [caption_scale] if caption_scale else []
    scales += [s for s in _SCALE_CANDIDATES if s != caption_scale]
    for scale in scales:
        picked = _select_columns(
            names, revenue, profit, scale=scale, ref_revenue=ref_revenue, ref_op=ref_op
        )
        if picked is None:
            continue
        total_col, seg_cols = picked
        rev_total = round((revenue[total_col] or 0) * scale)
        op_total = round((profit[total_col] or 0) * scale)
        return _Parsed(
            names=[names[c].strip() for c in seg_cols],
            revenue=[round((revenue[c] or 0) * scale) for c in seg_cols],
            profit=[round((profit[c] or 0) * scale) for c in seg_cols],
            revenue_total=rev_total,
            op_total=op_total,
            scale=scale,
            revenue_gap_pct=(rev_total - ref_revenue) / abs(ref_revenue) * 100.0,
            op_gap_pct=(op_total - ref_op) / abs(ref_revenue) * 100.0,
        )
    return None


def build_segment_profit(
    sections: list[Section], ms: MetricSet, rcept_no: str | None = None
) -> SegmentProfitSet:
    """부문 주석 후보들 → 부문별 손익. **검산에 실패하면 `usable=False`.**

    후보 섹션을 제목으로 고르지 않는다. 같은 보고서에 연결과 별도가 모두
    실려 있고 어느 쪽이 재무제표와 짝인지는 검산만 안다 — 연결 기준으로
    분석 중이면 별도 주석은 총계가 맞지 않아 저절로 탈락한다.
    """
    y = ms.fiscal_year
    out = SegmentProfitSet(fiscal_year=y, rcept_no=rcept_no)
    ref_revenue, ref_op = ms.get("revenue"), ms.get("operating_income")
    if ref_revenue is None or ref_op is None:
        out.note = "손익계산서의 매출액·영업이익을 확인하지 못해 부문 손익을 검산할 수 없다."
        return out
    if not sections:
        out.note = "사업보고서에서 영업부문 주석을 찾지 못했다."
        return out

    prior_revenue, prior_op = ms.get_prior("revenue"), ms.get_prior("operating_income")
    saw_table = False

    for section in sections:
        caption_scale = detect_unit_scale(section.body)
        grids = section.tables()
        saw_table = saw_table or any(read_segment_grid(g) is not None for g in grids)
        current = current_at = None
        for i, grid in enumerate(grids):
            current = _parse_table(
                grid, ref_revenue=ref_revenue, ref_op=ref_op, caption_scale=caption_scale
            )
            if current is not None:
                current_at = i
                break
        if current is None:
            continue

        # 전기 표 — 같은 섹션 안의 같은 모양 표. 부문 이름이 같아야 하고
        # (지역별 표가 우연히 전기 손익과 맞는 일을 막는다) 당기 표 자신이면
        # 안 된다 — 실적이 전년과 1% 안쪽이면 같은 표가 전기로도 맞는다.
        prior = None
        for i, grid in enumerate(grids):
            if i == current_at:
                continue
            cand = _parse_table(
                grid, ref_revenue=prior_revenue, ref_op=prior_op, caption_scale=caption_scale
            )
            if cand is not None and cand.names == current.names:
                prior = cand
                break

        lines = [
            SegmentProfitLine(
                name=name,
                revenue=current.revenue[i],
                operating_income=current.profit[i],
                revenue_prior=prior.revenue[i] if prior else None,
                op_prior=prior.profit[i] if prior else None,
            )
            for i, name in enumerate(current.names)
        ]
        return SegmentProfitSet(
            fiscal_year=y,
            lines=lines,
            revenue_total=current.revenue_total,
            op_total=current.op_total,
            revenue_gap_pct=current.revenue_gap_pct,
            op_gap_pct=current.op_gap_pct,
            reconciled=True,
            unit_scale=current.scale,
            rcept_no=rcept_no,
            section_title=section.title,
            has_prior=prior is not None,
        )

    # 두 실패를 구분한다 — 커버리지 문제를 파싱 실패로 뭉뚱그리면 진단이 막힌다.
    out.note = (
        "영업부문 주석의 총계가 손익계산서의 매출액·영업이익과 맞지 않아 "
        "부문별 손익을 싣지 않았다. 잘못 읽은 부문 손익은 되돌릴 수 없다."
        if saw_table
        else (
            "동사는 부문별 손익을 공시하지 않았다. 단일 영업부문은 부문별 공시 의무가 "
            "없으므로 수익성은 전사 기준으로만 볼 수 있다."
        )
    )
    return out


# ── Number Registry ──────────────────────────────────────────────────
def _key(index: int) -> str:
    """부문명은 한글·공백·괄호가 섞여 키로 못 쓴다. 순번으로 안정화한다.
    `segment{i}`(매출 표)와 겹치면 안 된다 — 두 표의 부문 분류가 다르다."""
    return f"opseg{index + 1}"


def build_segment_profit_entries(sp: SegmentProfitSet, prov: Provenance) -> list[NumberEntry]:
    if not sp.usable:
        return []
    y = sp.fiscal_year
    out: list[NumberEntry] = []
    for i, line in enumerate(sp.lines):
        base = _key(i)
        out.append(
            NumberEntry(
                key=f"{base}_revenue_{y}a",
                value=line.revenue,
                unit="원",
                display=fmt_krw(line.revenue),
                provenance=prov,
                label=f"{line.name} 매출 ({y}A)",
            )
        )
        out.append(
            NumberEntry(
                key=f"{base}_op_{y}a",
                value=line.operating_income,
                unit="원",
                display=fmt_krw(line.operating_income),
                provenance=prov,
                label=f"{line.name} 영업이익 ({y}A)",
            )
        )
        if line.op_margin is not None:
            out.append(
                NumberEntry(
                    key=f"{base}_margin_{y}a",
                    value=line.op_margin,
                    unit="%",
                    display=fmt_pct(line.op_margin),
                    provenance=prov,
                    label=f"{line.name} 영업이익률 ({y}A)",
                    formula=f"{line.name} 영업이익 / {line.name} 매출",
                    inputs=[f"{base}_op_{y}a", f"{base}_revenue_{y}a"],
                )
            )
        if line.margin_change is not None:
            out.append(
                NumberEntry(
                    key=f"{base}_margin_chg_{y}a",
                    value=line.margin_change,
                    unit="pp",
                    # 증감은 부호를 붙이고 pp로 쓴다 — `%`로 쓰면 수준으로 읽힌다
                    display=f"{line.margin_change:+.1f}pp",
                    provenance=prov,
                    label=f"{line.name} 영업이익률 증감 ({y}A)",
                    formula="당기 부문 영업이익률 - 전기 부문 영업이익률",
                    inputs=[f"{base}_margin_{y}a"],
                )
            )
        if line.revenue_yoy is not None:
            out.append(
                NumberEntry(
                    key=f"{base}_rev_yoy_{y}a",
                    value=line.revenue_yoy,
                    unit="%",
                    display=fmt_pct(line.revenue_yoy),
                    provenance=prov,
                    label=f"{line.name} 매출 YoY ({y}A)",
                    formula=f"({line.name} 당기 매출 - 전기 매출) / |전기 매출|",
                    inputs=[f"{base}_revenue_{y}a"],
                )
            )
        rev_share = sp.rev_share(line)
        if rev_share is not None:
            out.append(
                NumberEntry(
                    key=f"{base}_rev_share_{y}a",
                    value=rev_share,
                    unit="%",
                    display=fmt_pct(rev_share),
                    provenance=prov,
                    label=f"{line.name} 매출 비중 ({y}A)",
                    formula=f"{line.name} 매출 / 보고부문 매출 합계",
                    inputs=[f"{base}_revenue_{y}a"],
                )
            )
        op_share = sp.op_share(line)
        if op_share is not None:
            out.append(
                NumberEntry(
                    key=f"{base}_op_share_{y}a",
                    value=op_share,
                    unit="%",
                    display=fmt_pct(op_share),
                    provenance=prov,
                    label=f"{line.name} 영업이익 비중 ({y}A)",
                    formula=f"{line.name} 영업이익 / 보고부문 영업이익 합계",
                    inputs=[f"{base}_op_{y}a"],
                )
            )

    # 검산값은 감사용이다 (D17) — 카탈로그에서 빠지고 레지스트리에만 남는다
    for key, value, label in (
        ("opseg_revenue_gap", sp.revenue_gap_pct, "부문 주석 매출 총계 검산 차이"),
        ("opseg_op_gap", sp.op_gap_pct, "부문 주석 영업이익 총계 검산 차이"),
    ):
        if value is None:
            continue
        out.append(
            NumberEntry(
                key=f"{key}_{y}a",
                value=value,
                unit="%",
                display=fmt_pct(value, 2),
                provenance=prov,
                label=f"{label} ({y}A)",
                internal=True,
            )
        )
    return out


# ── 논지 ─────────────────────────────────────────────────────────────
def build_segment_profit_observations(sp: SegmentProfitSet) -> list[str]:
    """부문 손익 논지. **크기를 쓰지 않는다** — 프롬프트의 숫자는 LLM이
    리터럴로 베낀다(D16). 방향과 우열만 담고 크기는 플레이스홀더로 간다."""
    if not sp.usable:
        return []
    names = [x.name for x in sp.lines]
    obs = [
        (
            f"영업부문 주석에 부문별 매출과 **영업이익**이 함께 공시돼 있다. "
            f"보고부문은 {', '.join(names)}이며, 부문 손익의 총계가 손익계산서의 "
            "매출액·영업이익과 일치함을 확인했다. 전사 지표만으로는 어느 부문이 "
            "이익을 내는지 알 수 없다."
        )
    ]

    rev_leader, prof_leader = sp.revenue_leader, sp.profit_leader
    if sp.leaders_differ and rev_leader and prof_leader:
        obs.append(
            f"매출이 가장 큰 부문은 {rev_leader.name}이지만 영업이익이 가장 큰 부문은 "
            f"{prof_leader.name}이다. 전사 이익률은 {prof_leader.name}의 흐름에 좌우되므로 "
            f"{rev_leader.name}의 외형 변화만 보면 이익 방향을 놓친다."
        )
    elif rev_leader:
        # 1위가 압도적일 때만 "전사가 이 부문을 따라간다"고 말한다. 6개 부문 중
        # 매출 29%짜리 1위에 그 말을 붙이면 과장이다(LG전자 실측).
        share = sp.rev_share(rev_leader) or 0.0
        obs.append(
            f"{rev_leader.name}이 매출과 영업이익 모두에서 가장 크다. "
            + (
                "전사 실적이 이 부문의 실적과 사실상 같이 움직인다."
                if share >= 60
                else "다만 나머지 부문의 합이 더 크므로 전사 이익을 한 부문으로 설명할 수는 없다."
            )
        )

    margined = [x for x in sp.lines if x.op_margin is not None]
    if len(margined) >= 2:
        best = max(margined, key=lambda x: x.op_margin or 0)
        worst = min(margined, key=lambda x: x.op_margin or 0)
        obs.append(
            f"영업이익률이 가장 높은 부문은 {best.name}이고 가장 낮은 부문은 {worst.name}이다. "
            "부문 구성이 바뀌면 전사 이익률은 실제 수익성 변화 없이도 움직인다."
        )

    losers = sp.loss_makers
    if losers:
        obs.append(
            f"{', '.join(x.name for x in losers)}은 영업적자다. "
            "전사 영업이익은 나머지 부문이 이 적자를 상쇄한 결과이므로, "
            "적자 부문의 방향이 전사 이익의 관건이다."
        )

    changed = [x for x in sp.lines if x.margin_change is not None]
    if len(changed) >= 2:
        up = [x.name for x in changed if (x.margin_change or 0) > 0]
        down = [x.name for x in changed if (x.margin_change or 0) < 0]
        if up and down:
            obs.append(
                f"전기 대비 영업이익률이 개선된 부문은 {', '.join(up)}이고 "
                f"악화된 부문은 {', '.join(down)}이다. 방향이 갈렸으므로 전사 이익률 변화는 "
                "부문 고유 요인으로 설명해야 하고, 전사 공통 요인만으로는 설명되지 않는다."
            )
        elif up and not down:
            obs.append(
                "모든 부문의 영업이익률이 전기 대비 개선됐다. 특정 부문의 사정이 아니라 "
                "전사에 걸친 요인이 작용했을 가능성이 크다."
            )
        elif down and not up:
            obs.append(
                "모든 부문의 영업이익률이 전기 대비 악화됐다. 특정 부문의 사정이 아니라 "
                "전사에 걸친 요인이 작용했을 가능성이 크다."
            )
        # 전사 이익을 움직인 부문은 **이익률 변동 폭이 아니라 이익 변화액**으로
        # 가른다. 매출 비중 2%짜리 부문의 이익률이 10pp 흔들려도 전사에는 거의
        # 영향이 없다 (D28의 기여도 판정과 같은 이유).
        driver = max(changed, key=lambda x: abs(x.operating_income - (x.op_prior or 0)))
        obs.append(
            f"전사 영업이익의 변화를 가장 크게 끌고 간 부문은 {driver.name}이다. "
            "전사 마진 변화를 읽을 때 이 부문을 먼저 봐야 한다."
        )
    elif not sp.has_prior:
        obs.append(
            "부문 주석에서 전기 비교치를 확인하지 못해 부문별 이익률의 변화 방향은 "
            "이번 노트에서 다루지 않는다."
        )
    return obs
