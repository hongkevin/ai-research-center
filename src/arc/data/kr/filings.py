"""공시 목록 → **실제로 존재하는 정기보고서**.

왜 필요한가
-----------
화면이 「사업연도」 숫자를 받다가 「정기보고서 선택」으로 바뀌었는데, 그 목록을
**법정 제출기한에서 계산**하고 있었다. 그건 추측이다 — 회사마다 결산월이 다르고,
기한보다 일찍 낼 수도 늦을 수도 있고, 아예 없을 수도 있다.

DART에 뭐가 올라와 있는지 물어보면 된다(`list.json`). 그러면 고르는 목록이
**사실**이 되고, 접수일과 접수번호가 함께 오므로 "무엇을 언제 낸 것으로 쓰는지"가
화면에 그대로 드러난다.

잠정실적
--------
같은 목록에 **잠정실적 공시**도 뜬다. 코퍼스 7,410건을 세어보니 리포트 발간은
1월이 최대(1,202건)인데 3월이 최소(335건)다 — 사업보고서가 3월 말에 나오는데도
그렇다. **증권사는 정기보고서가 아니라 1월 말 잠정실적에 붙여 쓴다**
(`corpus/FINDINGS.md`).

ARC는 잠정실적을 아직 읽지 못한다(부문 데이터가 없다). 하지만 **더 최신 실적이
이미 나와 있다는 사실은 알려줘야 한다** — 모르고 옛 보고서로 쓰는 것과, 알고도
그걸 쓰는 것은 다르다.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from arc.data.base import Disclosure, PeriodType

# 「사업보고서 (2025.12)」·「분기보고서 (2026.03)」 — 괄호 안이 결산 기준월이다
_PERIOD_RE = re.compile(r"\((\d{4})[.\-/](\d{2})\)")

_KIND = (
    ("사업보고서", PeriodType.ANNUAL),
    ("반기보고서", PeriodType.HALF),
    ("분기보고서", None),  # 월을 봐야 1분기인지 3분기인지 안다
)

# 잠정실적 — 정기보고서보다 먼저 오고, 리포트는 여기 붙는다
_PRELIMINARY = ("영업(잠정)실적", "손익구조", "매출액또는손익구조")

LABEL = {
    PeriodType.ANNUAL: "사업보고서",
    PeriodType.HALF: "반기보고서",
    PeriodType.Q1: "1분기보고서",
    PeriodType.Q3: "3분기보고서",
}


@dataclass(frozen=True)
class PeriodicFiling:
    """실제로 제출된 정기보고서 1건."""

    year: int
    period: PeriodType
    title: str
    filed_at: dt.date
    rcept_no: str
    url: str
    period_end: dt.date  # 이 보고서가 다루는 기간의 끝 (제목 괄호에서 온다)

    @property
    def label(self) -> str:
        return f"{self.year} {LABEL.get(self.period, self.period.value)}"


def is_preliminary(title: str) -> bool:
    """잠정실적 공시인가. 정기보고서보다 먼저 오는 실적 발표다."""
    t = title.replace(" ", "")
    return any(k in t for k in _PRELIMINARY)


def classify(title: str) -> tuple[int, PeriodType, dt.date] | None:
    """정기보고서 제목 → (사업연도, 기간, 기간 끝). 정기보고서가 아니면 None.

    **12월 결산을 전제한다** — 분기보고서가 1분기인지 3분기인지는 괄호 안 월로
    가르는데, 결산월이 다른 회사는 이 판정이 어긋난다. 국내 상장사 대부분이
    12월 결산이라 우선 이렇게 두고, 어긋나면 목록에서 사람이 고른다.
    """
    t = title.replace(" ", "")
    m = _PERIOD_RE.search(title)
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    end = dt.date(year, month, 1)
    for keyword, period in _KIND:
        if keyword not in t:
            continue
        if period is not None:
            return year, period, end
        return year, (PeriodType.Q1 if month <= 6 else PeriodType.Q3), end
    return None


def periodic_filings(disclosures: list[Disclosure]) -> list[PeriodicFiling]:
    """공시 목록에서 정기보고서만. 최신 제출순.

    같은 (연도·기간)이 여러 번 나오면 **가장 최근 제출본**만 남긴다 —
    정정신고가 올라오면 원본과 정정본이 함께 뜬다.

    **정렬은 제출일이 아니라 기간 끝으로 한다.** 제출일로 세우면 정정신고가
    몇 달 뒤에 올라올 때 순서가 뒤집힌다 — 실측: 노바렉스의 [기재정정]2024
    반기보고서가 2025-01-08에 올라와 2024 3분기보고서보다 위에 섰다. 고르는
    것은 「어느 기간」이지 「언제 냈는지」가 아니다.
    """
    best: dict[tuple[int, PeriodType], PeriodicFiling] = {}
    for d in disclosures:
        got = classify(d.title)
        if got is None:
            continue
        year, period, end = got
        f = PeriodicFiling(
            year=year,
            period=period,
            period_end=end,
            title=d.title.strip(),
            filed_at=d.filed_at,
            rcept_no=d.rcept_no,
            url=d.provenance.verify_url or "",
        )
        prev = best.get((year, period))
        if prev is None or f.filed_at >= prev.filed_at:
            best[(year, period)] = f
    return sorted(best.values(), key=lambda f: f.period_end, reverse=True)


def preliminary_filings(disclosures: list[Disclosure]) -> list[Disclosure]:
    """잠정실적 공시만. 최신순."""
    out = [d for d in disclosures if is_preliminary(d.title)]
    return sorted(out, key=lambda d: d.filed_at, reverse=True)


def unread_preliminary(disclosures: list[Disclosure]) -> Disclosure | None:
    """**정기보고서보다 나중에 나온** 잠정실적. 없으면 None.

    있다는 사실만으로 알리면 거짓말이 된다 — 실측(노바렉스): 잠정실적이
    2026-02-02인데 1분기보고서가 2026-05-15이라, 정기보고서가 더 최신인데도
    "잠정실적이 이미 나왔다"고 띄웠다.

    알려야 하는 경우는 하나다: **우리가 읽을 수 있는 가장 최신 정기보고서보다
    나중에 실적이 나왔을 때.** 그때만 RA가 우리보다 최신 숫자를 갖고 있다.
    """
    pre = preliminary_filings(disclosures)
    if not pre:
        return None
    periodic = periodic_filings(disclosures)
    if not periodic:
        return pre[0]
    newest_filed = max(f.filed_at for f in periodic)
    return pre[0] if pre[0].filed_at > newest_filed else None
