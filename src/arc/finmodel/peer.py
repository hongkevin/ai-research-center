"""피어 비교표 — 여러 종목 카드를 한 표로 세운다.

왜 여기 있는가
--------------
인터뷰에서 RA가 먼저 꺼낸 고통이 **커버 밖 종목**이었다. 섹터를 보려면 커버
종목 옆에 안 보는 종목을 세워야 하는데, 그건 지금 아무 데도 없다.

**숫자를 새로 만들지 않는다**
------------------------------
이 모듈은 계산을 하지 않는다. 각 구성원 카드의 **레지스트리에 이미 있는 표시
문자열**을 꺼내 옆으로 놓을 뿐이다. 그래야:

* 표의 모든 칸이 이미 G0를 통과한 수치다 (불변식 1)
* 칸을 클릭하면 그 종목의 원문 절까지 되짚힌다 (D44)
* 같은 값이 본문과 표에서 갈라질 수 없다

그래서 **평균·중앙값·순위를 내지 않는다.** 내고 싶으면 그건 출처가 있는 새
수치라 레지스트리에 등록돼야 한다 — 여기서 슬쩍 만들면 출처 없는 숫자가
표에 앉는다. v1에서는 나란히 세우는 것까지만 한다.

키의 연도가 카드마다 다르다
---------------------------
레지스트리 키는 `revenue_2026a`처럼 **연도가 박혀 있고**, 그 연도는 카드가
어느 정기보고서로 만들어졌는지에 따라 다르다. 그래서 구성원마다 자기 연도로
키를 조립한다. 연도가 갈리는 것 자체는 결함이 아니지만 **기준 기간이 섞이면
표가 조용히 거짓말을 한다** — 그 판정은 `store.cards.peer_attention_reasons()`가
한다. 여기서는 사실만 싣는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 표의 줄. `(키 앞부분, 이름, 묶음, 연도 모양)`
#
# 증권사 리포트 코퍼스에서 센 것과 맞춘다 — **글보다 표가 많고**, 그 표는
# 규모 → 수익성 → 성장 → 재무 → 밸류 → 추정 순으로 간다.
#
# **피어 비교는 실적 비교가 아니다.** 실적·주가·밸류·추정을 나란히 놓을 때
# 생기는 **간극**이 답이다 — 실적이 비슷한데 주가가 갈렸다면 시장이 무언가를
# 다르게 보고 있다는 뜻이고, 그게 파고들 자리다.
#
# 밸류에이션은 주식수가 필요해 분기 카드에서 자주 비어 있다(분기보고서에
# 주식수가 없다). 없으면 줄이 통째로 빠진다 — 빈칸 격자를 세우지 않는다.
#
# 연도 모양은 `a`=실적 연도,. 연도 모양은 `a`=실적 연도,
# `e`=다음 해 추정 — 레지스트리 키가 `revenue_2026a` / `revenue_2027e`다.
ROWS: tuple[tuple[str, str, str, str], ...] = (
    ("revenue", "매출액", "규모", "a"),
    ("operating_income", "영업이익", "규모", "a"),
    ("net_income", "당기순이익", "규모", "a"),
    ("operating_margin", "영업이익률", "수익성", "a"),
    ("net_margin", "순이익률", "수익성", "a"),
    ("gross_margin", "매출총이익률", "수익성", "a"),
    ("revenue_yoy", "매출액 YoY", "성장", "a"),
    ("operating_income_yoy", "영업이익 YoY", "성장", "a"),
    ("roe", "ROE", "수익성", "a"),
    ("debt_ratio", "부채비율", "재무", "a"),
    ("current_ratio", "유동비율", "재무", "a"),
    ("net_debt_ratio", "순차입금비율", "재무", "a"),
    # **밸류에이션이 실적과 주가 사이의 간극이다.** 실적이 비슷한데 주가가
    # 갈렸다면 시장이 무언가를 다르게 보고 있다는 뜻이고, 그게 RA가 파고들
    # 자리다. 실적만 나란히 놓으면 그 질문 자체가 안 나온다.
    ("price", "주가", "밸류에이션", "a"),
    ("per", "PER", "밸류에이션", "a"),
    ("pbr", "PBR", "밸류에이션", "a"),
    ("market_cap", "시가총액", "밸류에이션", "a"),
    # **추정은 「누구를 더 좋게 보고 있나」다.** 같은 섹터를 놓고 우리가 건
    # 가정이 종목마다 다르면 그 자체가 관점이다 (D34: 사람이 넣은 만큼만 낸다).
    ("revenue", "매출액 (E)", "추정", "e"),
    ("operating_income", "영업이익 (E)", "추정", "e"),
    ("assume_revenue_growth", "가정 매출성장률", "추정", "e"),
    ("assume_operating_margin", "가정 영업이익률", "추정", "e"),
)


@dataclass
class PeerCell:
    """표의 칸 하나. **표시 문자열은 레지스트리에서 그대로 가져온다.**"""

    display: str = "—"
    key: str = ""  # 되짚기용 — 어느 수치인가
    card_id: str = ""  # 되짚기용 — 어느 카드인가
    value: float | None = None  # 정렬용. **표시에는 쓰지 않는다**
    absent: bool = True


@dataclass
class PeerRow:
    label: str
    group: str
    unit: str = ""
    cells: list[PeerCell] = field(default_factory=list)

    @property
    def coverage(self) -> int:
        return sum(0 if c.absent else 1 for c in self.cells)


@dataclass
class PeerColumn:
    """열 머리 — 종목 하나."""

    symbol: str
    company: str = ""
    card_id: str = ""
    year: int = 0
    period: str = "ANNUAL"
    basis: str = ""  # "2026년 1분기 누적"
    ready: bool = False


@dataclass
class PeerTable:
    columns: list[PeerColumn] = field(default_factory=list)
    rows: list[PeerRow] = field(default_factory=list)
    mixed_basis: bool = False
    note: str = ""


_PERIOD_BASIS = {
    "ANNUAL": "연간",
    "HALF": "반기 누적",
    "Q1": "1분기 누적",
    "Q3": "3분기 누적",
}


def _index(registry: list[dict]) -> dict[str, dict]:
    return {e["key"]: e for e in registry if isinstance(e, dict) and "key" in e}


def basis_label(year: int, period: str) -> str:
    if not year:
        return ""
    return f"{year}년 {_PERIOD_BASIS.get(period, period)}"


def build_peer_table(members: list[dict]) -> PeerTable:
    """구성원 카드들을 한 표로.

    `members`는 `store.cards.peer_member()` 모양에 **`registry`를 얹은 것**이다
    — 저장소 접근은 호출자가 한다. 여기는 순수 함수라 테스트가 쉽다.

    준비되지 않은 구성원(`status != "ready"`)도 **열은 만든다.** 빼 버리면
    화면에서 그 종목이 사라져 「왜 안 나오지」가 되고, 무엇이 비었는지가
    표에 안 드러난다.
    """
    columns: list[PeerColumn] = []
    indexes: list[dict[str, dict]] = []

    for m in members:
        year = int(m.get("year") or 0)
        period = str(m.get("period") or "ANNUAL")
        ready = m.get("status") == "ready"
        columns.append(
            PeerColumn(
                symbol=str(m.get("symbol") or ""),
                company=str(m.get("company") or ""),
                card_id=str(m.get("card_id") or ""),
                year=year,
                period=period,
                basis=basis_label(year, period) if ready else "",
                ready=ready,
            )
        )
        indexes.append(_index(m.get("registry") or []) if ready else {})

    rows: list[PeerRow] = []
    for base, label, group, shape in ROWS:
        row = PeerRow(label=label, group=group)
        for col, idx in zip(columns, indexes, strict=True):
            # 추정은 **다음 해**다 — `revenue_2026a`의 짝이 `revenue_2027e`.
            key = f"{base}_{col.year + 1}e" if shape == "e" else f"{base}_{col.year}a"
            entry = idx.get(key) if col.year else None
            if entry is None:
                row.cells.append(PeerCell(card_id=col.card_id))
                continue
            row.unit = row.unit or str(entry.get("unit") or "")
            row.cells.append(
                PeerCell(
                    display=str(entry.get("display") or entry.get("value") or "—"),
                    key=str(entry.get("key") or ""),
                    card_id=col.card_id,
                    value=_as_float(entry.get("value")),
                    absent=False,
                )
            )
        # **한 칸도 없는 줄은 싣지 않는다.** 전부 「—」인 줄이 열두 개 서 있으면
        # 표가 아니라 빈칸 격자가 된다.
        if row.coverage:
            rows.append(row)

    bases = {(c.year, c.period) for c in columns if c.ready}
    mixed = len(bases) > 1
    return PeerTable(
        columns=columns,
        rows=rows,
        mixed_basis=mixed,
        note=_note(columns, rows, mixed),
    )


def _note(columns: list[PeerColumn], rows: list[PeerRow], mixed: bool) -> str:
    """표 아래에 붙는 한 줄. **비어 있는 이유를 말한다.**"""
    if mixed:
        shown = ", ".join(sorted({c.basis for c in columns if c.ready and c.basis}))
        return f"기준 기간이 서로 다릅니다 ({shown}) — 나란히 비교할 수 없습니다."
    not_ready = [c.company or c.symbol for c in columns if not c.ready]
    if not_ready:
        return f"아직 준비되지 않은 종목이 있습니다 — {', '.join(not_ready)}"
    if not rows:
        return "비교할 수치를 찾지 못했습니다."
    basis = next((c.basis for c in columns if c.basis), "")
    return f"{basis} 기준" if basis else ""


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
