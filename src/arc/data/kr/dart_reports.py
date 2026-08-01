"""OpenDART 정기보고서 주요정보 어댑터.

`fnlttSinglAcntAll`(재무제표)와 **다른 API 계열**이다. 재무제표에 없는 것들이
여기 있다:

  * `stockTotqySttus`  주식의 총수 → 밸류에이션의 분모
  * `alotMatter`       배당 → DPS·배당수익률·배당성향
  * `accnutAdtorNmNdAdtOpinion` 감사의견 + **핵심감사사항(KAM)**
  * `empSttus`         직원 현황 → **사업부문별** 인력

왜 중요한가
-----------
재무제표만으로 쓰는 노트는 "재무 기계학"에서 멈춘다. 밸류에이션은 주식수가
없으면 시작조차 안 되고, 리스크 섹션은 공시 밖 근거가 없으면 일반론이 된다.

**KAM은 감사인이 "여기가 위험하다"고 지목한 항목이다.** 우리가 만든 추측이
아니라 외부에서 검증된 서술이므로, 컴플라이언스 부담 없이 리스크 섹션의
근거로 쓸 수 있다.

`empSttus`의 `fo_bbm`은 **사업부문명**이다(삼성전자 = DX/DS). DART에 부문별
매출 API는 없지만 부문별 **인력**은 여기 있다. 완전한 세그먼트는 아니고,
사업 구성을 읽는 첫 단서다.

파싱 주의
---------
* 없는 값은 `"-"` 또는 빈 문자열로 온다 (0이 아니다). 구분해야 한다.
* 금액 단위가 항목명에 들어 있다 ("현금배당금총액(백만원)").
* 감사의견은 3개 사업연도 × 연결/별도로 중복되어 온다.
* KAM 항목 구분자가 개행일 때도, 없을 때도 있다 ("...평가2. 재화의...").
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import ClassVar

from arc.data.base import Provenance
from arc.data.kr.dart import BASE_URL, SOURCE, DartProvider

# 보고서 코드 — 사업보고서만 쓰지만 분기 확장 대비로 남긴다
REPRT_ANNUAL = "11011"
REPRT_H1 = "11012"
REPRT_Q1 = "11013"
REPRT_Q3 = "11014"

_MILLION = 1_000_000


# ── 값 파싱 ──────────────────────────────────────────────────────────
def parse_number(raw: str | None) -> int | None:
    """DART 문자열 숫자 → int. **없음(`-`)과 0을 구분한다.**"""
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "").replace(" ", "")
    if s in {"", "-", "–", "—", "N/A"}:
        return None
    neg = s.startswith("(") and s.endswith(")")  # 회계 음수 표기
    if neg:
        s = s[1:-1]
    try:
        v = int(float(s))
    except ValueError:
        return None
    return -v if neg else v


def parse_ratio(raw: str | None) -> float | None:
    """비율(%) 문자열 → float."""
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "").replace("%", "").replace(" ", "")
    if s in {"", "-", "–", "—"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _norm(s: str | None) -> str:
    """공백·개행 정규화. DART 라벨에는 개행이 섞여 온다."""
    return " ".join(str(s or "").split())


# 공시 표의 각주 참조 — `(*6)`·`(주1)`·`(注2)`. 이름의 일부가 아니다.
_FOOTNOTE_RE = re.compile(r"\s*[(（]\s*[*※주注]\s*\d*\s*[)）]\s*")


def _strip_footnote(name: str) -> str:
    """법인명에서 각주 표시를 뗀다.

    실측: 롯데케미칼의 출자 현황은 `LOTTE Chemical Titan Holding Berhad (*6)`
    처럼 각주 번호를 이름에 붙여 준다. 그대로 본문 표에 실으면 **G0가 미등록
    숫자로 보고 발간을 막는다** — 그리고 그건 옳은 판정이다. 이 6은 레지스트리에
    없는 숫자이고 우리가 뜻을 보장할 수 없다. 게이트를 푸는 대신 이름을 고친다.
    """
    return _FOOTNOTE_RE.sub(" ", name).strip()


# ── 주식의 총수 ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class ShareCounts:
    """주식의 총수 현황.

    `issued`가 상장주식수(시가총액의 분모)이고, `outstanding`은 자기주식을
    뺀 유통주식수다. 시가총액은 관행상 **발행주식총수** 기준이므로 둘을
    섞으면 안 된다.

    `reconciled`는 발행 − 자기주식 = 유통 항등식 검산이다. 어긋나면 필드
    의미를 잘못 읽은 것이므로 표시하고 넘어가지 않는다.
    """

    fiscal_year: int
    issued: int | None  # 발행주식총수 (합계)
    treasury: int | None  # 자기주식 (합계)
    outstanding: int | None  # 유통주식수 (합계)
    common_issued: int | None
    common_treasury: int | None
    common_outstanding: int | None
    preferred_issued: int | None
    reconciled: bool
    rcept_no: str | None
    provenance: Provenance

    @property
    def has_preferred(self) -> bool:
        return bool(self.preferred_issued)


def parse_share_counts(
    rows: list[dict], fiscal_year: int, retrieved_at: dt.datetime
) -> ShareCounts | None:
    """`stockTotqySttus` 응답 → ShareCounts.

    `isu_stock_totqy`는 **발행할** 주식의 총수(정관상 수권주식수)라 쓰면
    안 된다. 실제 발행주식총수는 `istc_totqy`다.
    """
    if not rows:
        return None
    by_se = {_norm(r.get("se")): r for r in rows}
    total = by_se.get("합계")
    common = by_se.get("보통주")
    pref = by_se.get("우선주")
    if total is None and common is None:
        return None
    base = total if total is not None else common
    assert base is not None

    def n(row: dict | None, key: str) -> int | None:
        return parse_number(row.get(key)) if row else None

    issued, treasury, outstanding = (
        n(base, "istc_totqy"),
        n(base, "tesstk_co"),
        n(base, "distb_stock_co"),
    )
    # 자기주식이 없는 회사는 `-`로 온다 (파마리서치 등 — 소형주에 흔하다).
    # 이때 발행 == 유통이므로 0임이 **항등식으로 확정된다.** 추정이 아니다.
    if treasury is None and issued is not None and outstanding is not None:
        treasury = issued - outstanding
    ok = (
        issued is not None
        and treasury is not None
        and outstanding is not None
        and issued - treasury == outstanding
    )
    rcept_no = _norm(base.get("rcept_no")) or None
    return ShareCounts(
        fiscal_year=fiscal_year,
        issued=issued,
        treasury=treasury,
        outstanding=outstanding,
        common_issued=n(common, "istc_totqy"),
        common_treasury=n(common, "tesstk_co"),
        common_outstanding=n(common, "distb_stock_co"),
        preferred_issued=n(pref, "istc_totqy"),
        reconciled=ok,
        rcept_no=rcept_no,
        provenance=Provenance(
            source=SOURCE,
            retrieved_at=retrieved_at,
            source_url=f"{BASE_URL}/stockTotqySttus.json",
            source_ref=rcept_no,
        ),
    )


# ── 배당 ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class DividendInfo:
    """배당에 관한 사항 (당기 기준).

    `implied_price`는 **DPS ÷ 배당수익률로 역산한 주가**다. 시세 API 없이
    얻을 수 있는 유일한 가격 앵커라 유용하지만, DART가 어느 시점 주가로
    수익률을 계산했는지 명시하지 않으므로(통상 배당기준일 전 일정 기간
    종가 평균) **정확한 시세가 아니다.** 참고 앵커로만 쓰고, 시세 어댑터가
    붙으면 대체한다.
    """

    fiscal_year: int
    dps_common: int | None  # 주당 현금배당금 (보통주, 원)
    dps_preferred: int | None
    dividend_yield_common: float | None  # 현금배당수익률 (%)
    payout_ratio: float | None  # (연결)현금배당성향 (%)
    total_cash_dividend: int | None  # 현금배당금총액 (원 — 백만원에서 환산)
    eps_reported: int | None  # (연결)주당순이익 (원)
    par_value: int | None  # 주당 액면가액 (원)
    rcept_no: str | None
    provenance: Provenance

    @property
    def implied_price(self) -> int | None:
        """DPS ÷ 배당수익률 → 역산 주가. 정확한 시세가 아니다 (docstring 참조)."""
        if not self.dps_common or not self.dividend_yield_common:
            return None
        return round(self.dps_common / (self.dividend_yield_common / 100.0))


# 라벨은 회사마다 공백·괄호가 흔들려 부분일치로 찾는다
_DIV_LABELS = {
    "par_value": "주당액면가액",
    "eps_reported": "(연결)주당순이익",
    "total_cash_dividend": "현금배당금총액",
    "payout_ratio": "(연결)현금배당성향",
    "dividend_yield": "현금배당수익률",
    "dps": "주당 현금배당금",
}


def parse_dividend(
    rows: list[dict], fiscal_year: int, retrieved_at: dt.datetime
) -> DividendInfo | None:
    """`alotMatter` 응답 → DividendInfo. 당기(`thstrm`)만 읽는다."""
    if not rows:
        return None

    def find(label_key: str, stock_kind: str | None = None) -> dict | None:
        want = _DIV_LABELS[label_key].replace(" ", "")
        for r in rows:
            se = _norm(r.get("se")).replace(" ", "")
            if want not in se:
                continue
            # "주식배당수익률"이 "현금배당수익률" 검색에 걸리지 않게 접두 확인
            if label_key == "dividend_yield" and se.startswith("주식"):
                continue
            if stock_kind and _norm(r.get("stock_knd")) != stock_kind:
                continue
            return r
        return None

    def num(label_key: str, kind: str | None = None) -> int | None:
        r = find(label_key, kind)
        return parse_number(r.get("thstrm")) if r else None

    def pct(label_key: str, kind: str | None = None) -> float | None:
        r = find(label_key, kind)
        return parse_ratio(r.get("thstrm")) if r else None

    total_mn = num("total_cash_dividend")
    rcept_no = _norm(rows[0].get("rcept_no")) or None
    return DividendInfo(
        fiscal_year=fiscal_year,
        dps_common=num("dps", "보통주") or num("dps"),
        dps_preferred=num("dps", "우선주"),
        dividend_yield_common=pct("dividend_yield", "보통주") or pct("dividend_yield"),
        payout_ratio=pct("payout_ratio"),
        # 공시는 백만원 단위다. 다른 금액(원 단위)과 섞이면 3자리가 어긋난다.
        total_cash_dividend=total_mn * _MILLION if total_mn is not None else None,
        eps_reported=num("eps_reported"),
        par_value=num("par_value"),
        rcept_no=rcept_no,
        provenance=Provenance(
            source=SOURCE,
            retrieved_at=retrieved_at,
            source_url=f"{BASE_URL}/alotMatter.json",
            source_ref=rcept_no,
        ),
    )


# ── 감사의견 · 핵심감사사항 ──────────────────────────────────────────
# "없음"을 값으로 취급하면 "강조사항: 해당사항 없음"처럼 빈 내용이 본문에 실린다
_NO_CONTENT = {"", "-", "–", "—", "해당사항 없음", "해당사항없음", "없음", "N/A"}

_KAM_MARK_RE = re.compile(r"(\d+)\s*[.)]\s*")
# 한글 순서 표기도 쓴다 — SK하이닉스는 "가. (별도재무제표) …" 형식이다
_KAM_HANGUL = "가나다라마바사아자차"
_KAM_HANGUL_RE = re.compile(rf"([{_KAM_HANGUL}])\s*[.)]\s*")


def _sequential_marks(text: str, pattern: re.Pattern, order: str | None) -> list[tuple[int, int]]:
    """`text`에서 **처음부터 순서대로 이어지는** 마커 위치만 고른다.

    본문 안의 숫자를 구분자로 오인하지 않기 위한 장치다. "제3자 배정"의 3은
    앞에 1·2가 없으면 채택되지 않는다.
    """
    marks: list[tuple[int, int]] = []
    idx = 0
    for m in pattern.finditer(text):
        token = m.group(1)
        want = str(idx + 1) if order is None else order[idx]
        if token != want:
            continue
        marks.append((m.start(), m.end()))
        idx += 1
        if order is not None and idx >= len(order):
            break
    return marks


def split_kam(value: str | None) -> list[str]:
    """핵심감사사항 원문 → 항목 리스트.

    구분자가 개행일 때도 있고 없을 때도 있어("...평가2. 재화의...") 개행만으로
    자르면 항목이 붙는다. 그래서 마커로 자르되 **순서대로 이어지는 것만**
    구분자로 인정한다.

    마커는 숫자(1. 2. 3.)와 한글 순서(가. 나. 다.) 둘 다 쓰인다 — 실측으로
    확인됐다(삼성전자=숫자, SK하이닉스=한글).
    """
    if not value or _norm(value) in _NO_CONTENT:
        return []
    text = _norm(value)

    marks = _sequential_marks(text, _KAM_MARK_RE, None)
    if len(marks) < 2:
        hangul = _sequential_marks(text, _KAM_HANGUL_RE, _KAM_HANGUL)
        if len(hangul) > len(marks):
            marks = hangul
    if not marks:
        return [text]

    items: list[str] = []
    for i, (_start, end) in enumerate(marks):
        stop = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        item = text[end:stop].strip(" .,;·")
        if item:
            items.append(item)
    return items or [text]


@dataclass(frozen=True)
class AuditOpinion:
    """감사인·감사의견·핵심감사사항 (당기).

    KAM은 **감사인이 지목한 위험 영역**이다. 우리가 만든 추측이 아니라
    외부 검증된 서술이므로 리스크 섹션의 근거로 쓸 수 있다.
    """

    fiscal_year: int
    period_label: str  # "제57기 (당기)"
    auditor: str | None
    opinion: str | None  # "적정의견" 등
    kam_items: list[str] = field(default_factory=list)
    emphasis: str | None = None  # 강조사항
    rcept_no: str | None = None
    provenance: Provenance | None = None

    @property
    def is_clean(self) -> bool:
        """적정의견인가. 아니면 그 자체가 1급 리스크다."""
        return (
            bool(self.opinion)
            and "적정" in (self.opinion or "")
            and "부" not in (self.opinion or "")
        )


def parse_audit_opinion(
    rows: list[dict], fiscal_year: int, retrieved_at: dt.datetime
) -> AuditOpinion | None:
    """`accnutAdtorNmNdAdtOpinion` 응답 → 당기 AuditOpinion.

    3개 사업연도 × 연결/별도로 중복되어 오므로 **당기 행만** 고른다.
    """
    if not rows:
        return None
    current = next((r for r in rows if "당기" in _norm(r.get("bsns_year"))), rows[0])
    emphasis = _norm(current.get("emphs_matter"))
    rcept_no = _norm(current.get("rcept_no")) or None
    return AuditOpinion(
        fiscal_year=fiscal_year,
        period_label=_norm(current.get("bsns_year")),
        auditor=_norm(current.get("adtor")) or None,
        opinion=_norm(current.get("adt_opinion")) or None,
        kam_items=split_kam(current.get("core_adt_matter")),
        emphasis=None if emphasis in _NO_CONTENT else emphasis,
        rcept_no=rcept_no,
        provenance=Provenance(
            source=SOURCE,
            retrieved_at=retrieved_at,
            source_url=f"{BASE_URL}/accnutAdtorNmNdAdtOpinion.json",
            source_ref=rcept_no,
        ),
    )


# ── 직원 현황 (사업부문별) ───────────────────────────────────────────
@dataclass(frozen=True)
class DivisionHeadcount:
    """사업부문 1개의 인력."""

    division: str  # 삼성전자 = "DX", "DS" …
    headcount: int
    avg_tenure_years: float | None

    @property
    def is_total_row(self) -> bool:
        """집계 행인가.

        삼성전자 `empSttus`에는 DX·DS와 **함께** `성별합계` 행이 온다. 이걸
        부문으로 세면 총원이 정확히 2배가 된다(실측으로 확인).

        `전사`는 제외하지 않는다 — 부문을 나누지 않는 회사가 쓰는 정상적인
        단일 부문명이지 집계 행이 아니다.
        """
        return any(k in self.division for k in ("합계", "소계")) or self.division == "계"


# `fo_bbm` 라벨은 회사가 자유 서술한다. 사업부문으로 쓰는 회사(삼성전자
# DX/DS)도 있고 직군으로 쓰는 회사(셀트리온제약 생산직/영업직)도 있다.
# **구분하지 않고 "사업부문"이라 부르면 리포트에 거짓이 들어간다.**
_JOB_FUNCTION_HINTS = ("생산", "사무", "영업", "연구", "관리", "기술", "판매", "서비스", "지원")
_SINGLE_LABELS = {"전사", "본사", "회사", "전체", "직원"}


@dataclass(frozen=True)
class Workforce:
    """직원 현황.

    **DART에 부문별 매출 API는 없다.** 부문별 *인력*은 여기 있지만, 인력
    비중은 매출 비중이 아니므로 매출 구성으로 옮겨 말하면 안 된다.

    더 조심할 것은 `fo_bbm` 라벨의 의미가 회사마다 다르다는 점이다(실측:
    삼성전자=DX/DS 사업부문, 셀트리온제약=생산직/영업직 직군, SK하이닉스=
    반도체 단일). `grouping`으로 무엇인지 밝히고, 사업부문이 아닐 때는
    사업 구성으로 해석하지 않는다.
    """

    fiscal_year: int
    total: int | None
    divisions: list[DivisionHeadcount] = field(default_factory=list)
    rcept_no: str | None = None
    provenance: Provenance | None = None

    @property
    def division_names(self) -> list[str]:
        return [d.division for d in self.divisions if not d.is_total_row]

    @property
    def grouping(self) -> str:
        """인력 구분의 성격. `"사업부문"`일 때만 사업 구성 단서로 쓸 수 있다."""
        names = self.division_names
        if len(names) <= 1:
            return "단일"
        if all(n.endswith("직") or any(h in n for h in _JOB_FUNCTION_HINTS) for n in names):
            return "직군"
        return "사업부문"

    @property
    def has_segments(self) -> bool:
        """사업부문으로 나눠 공시하는가. 직군 구분은 사업부문이 아니다."""
        return self.grouping == "사업부문" and not any(
            n in _SINGLE_LABELS for n in self.division_names
        )

    def share_of(self, division: str) -> float | None:
        """인력 비중(%). **매출 비중이 아니다.**"""
        if not self.total:
            return None
        for d in self.divisions:
            if d.division == division:
                return d.headcount / self.total * 100.0
        return None


def parse_workforce(
    rows: list[dict], fiscal_year: int, retrieved_at: dt.datetime
) -> Workforce | None:
    """`empSttus` 응답 → Workforce. 성별 행을 부문 단위로 합친다."""
    if not rows:
        return None

    agg: dict[str, dict[str, float]] = {}
    for r in rows:
        div = _norm(r.get("fo_bbm"))
        if not div:
            continue
        n = parse_number(r.get("sm"))
        if n is None:
            continue
        tenure = parse_ratio(r.get("avrg_cnwk_sdytrn"))
        a = agg.setdefault(div, {"n": 0.0, "tenure_w": 0.0, "tenure_n": 0.0})
        a["n"] += n
        if tenure is not None:
            a["tenure_w"] += tenure * n  # 인원 가중평균 — 단순평균은 왜곡된다
            a["tenure_n"] += n

    divisions = [
        DivisionHeadcount(
            division=div,
            headcount=int(a["n"]),
            avg_tenure_years=round(a["tenure_w"] / a["tenure_n"], 1) if a["tenure_n"] else None,
        )
        for div, a in agg.items()
    ]
    divisions = [d for d in divisions if not d.is_total_row]
    divisions.sort(key=lambda d: -d.headcount)
    total = sum(d.headcount for d in divisions) or None
    rcept_no = _norm(rows[0].get("rcept_no")) or None
    return Workforce(
        fiscal_year=fiscal_year,
        total=total,
        divisions=divisions,
        rcept_no=rcept_no,
        provenance=Provenance(
            source=SOURCE,
            retrieved_at=retrieved_at,
            source_url=f"{BASE_URL}/empSttus.json",
            source_ref=rcept_no,
        ),
    )


# ── 지배구조 ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Ownership:
    """최대주주 및 특수관계인 현황.

    소형주에서 특히 중요하다. 오너 지분율이 높으면 승계·배당 유인이 실적과
    별개로 주가에 영향을 주고, 낮으면 경영권 안정성이 변수가 된다.

    `total_stake`는 표의 `계` 행에서 읽는다 — 개별 행을 더하면 우선주가
    섞이거나 중복 보유가 이중 계상된다.
    """

    fiscal_year: int
    principal: str | None  # 최대주주 본인
    principal_stake: float | None  # 본인 지분율 (%)
    total_stake: float | None  # 최대주주 + 특수관계인 합계 (%)
    holder_count: int  # 특수관계인 수 (본인 제외)
    rcept_no: str | None = None
    provenance: Provenance | None = None

    @property
    def is_owner_controlled(self) -> bool:
        """오너 지배 구조인가. 30%는 상법상 주요 의사결정의 실질 기준선이다."""
        return bool(self.total_stake and self.total_stake >= 30.0)


_COMMON_STOCK = {"보통주", "의결권있는주식", "보통주식"}


def parse_ownership(
    rows: list[dict], fiscal_year: int, retrieved_at: dt.datetime
) -> Ownership | None:
    """`hyslrSttus` 응답 → Ownership. **보통주 기준으로만 읽는다.**

    우선주 행이 섞여 있고(파마리서치 10.17%), 합계 행도 주식종류별로 따로
    온다. 섞으면 지분율이 실제와 달라진다.
    """
    if not rows:
        return None

    def is_common(r: dict) -> bool:
        kind = _norm(r.get("stock_knd"))
        return not kind or kind in _COMMON_STOCK

    common = [r for r in rows if is_common(r)] or rows
    total = next((r for r in common if _norm(r.get("nm")) in {"계", "합계", "소계"}), None)
    principal = next(
        (r for r in common if "본인" in _norm(r.get("relate"))),
        common[0] if common else None,
    )
    holders = [
        r
        for r in common
        if _norm(r.get("nm")) not in {"계", "합계", "소계"} and "본인" not in _norm(r.get("relate"))
    ]
    rcept_no = _norm(rows[0].get("rcept_no")) or None
    return Ownership(
        fiscal_year=fiscal_year,
        principal=_norm(principal.get("nm")) if principal else None,
        principal_stake=parse_ratio(principal.get("trmend_posesn_stock_qota_rt"))
        if principal
        else None,
        total_stake=parse_ratio(total.get("trmend_posesn_stock_qota_rt")) if total else None,
        holder_count=len(holders),
        rcept_no=rcept_no,
        provenance=Provenance(
            source=SOURCE,
            retrieved_at=retrieved_at,
            source_url=f"{BASE_URL}/hyslrSttus.json",
            source_ref=rcept_no,
        ),
    )


# ── 타법인 출자 (자회사·관계기업) ────────────────────────────────────
@dataclass(frozen=True)
class Affiliate:
    """출자 법인 1개."""

    name: str
    purpose: str  # 경영참여 / 일반투자 / 단순투자
    stake: float | None  # 지분율 (%)
    book_value: int | None  # 기말 장부가액 (원)

    @property
    def is_operating(self) -> bool:
        """경영참여 = 사업적으로 연결된 곳. 단순투자와 섞으면 사업 구조가 흐려진다."""
        return "경영참여" in self.purpose


@dataclass(frozen=True)
class Affiliates:
    """타법인 출자 현황. SOTP·지주사 판별의 재료다."""

    fiscal_year: int
    entries: list[Affiliate] = field(default_factory=list)
    rcept_no: str | None = None
    provenance: Provenance | None = None

    @property
    def operating(self) -> list[Affiliate]:
        return [e for e in self.entries if e.is_operating]

    @property
    def total_book_value(self) -> int:
        return sum(e.book_value or 0 for e in self.entries)

    def top(self, n: int = 5) -> list[Affiliate]:
        """장부가 상위 n곳. 34~134건을 전부 싣는 리포트는 없다."""
        return sorted(self.entries, key=lambda e: -(e.book_value or 0))[:n]


def parse_affiliates(
    rows: list[dict], fiscal_year: int, retrieved_at: dt.datetime
) -> Affiliates | None:
    """`otrCprInvstmntSttus` 응답 → Affiliates."""
    if not rows:
        return None
    entries: list[Affiliate] = []
    for r in rows:
        name = _strip_footnote(_norm(r.get("inv_prm")))
        if not name or name in {"계", "합계", "소계", "-"}:
            continue
        entries.append(
            Affiliate(
                name=name,
                purpose=_norm(r.get("invstmnt_purps")),
                stake=parse_ratio(r.get("trmend_blce_qota_rt")),
                book_value=parse_number(r.get("trmend_blce_acntbk_amount")),
            )
        )
    if not entries:
        return None
    rcept_no = _norm(rows[0].get("rcept_no")) or None
    return Affiliates(
        fiscal_year=fiscal_year,
        entries=entries,
        rcept_no=rcept_no,
        provenance=Provenance(
            source=SOURCE,
            retrieved_at=retrieved_at,
            source_url=f"{BASE_URL}/otrCprInvstmntSttus.json",
            source_ref=rcept_no,
        ),
    )


# ── 묶음 ─────────────────────────────────────────────────────────────
@dataclass
class PeriodicReportInfo:
    """정기보고서 주요정보 묶음. **없는 항목은 None으로 둔다** (추정 금지)."""

    fiscal_year: int
    shares: ShareCounts | None = None
    dividend: DividendInfo | None = None
    audit: AuditOpinion | None = None
    workforce: Workforce | None = None
    ownership: Ownership | None = None
    affiliates: Affiliates | None = None
    unavailable: list[str] = field(default_factory=list)


class DartReportProvider:
    """정기보고서 주요정보 조회. `DartProvider`의 인증·클라이언트를 재사용한다."""

    ENDPOINTS: ClassVar[dict[str, str]] = {
        "shares": "stockTotqySttus.json",
        "dividend": "alotMatter.json",
        "audit": "accnutAdtorNmNdAdtOpinion.json",
        "workforce": "empSttus.json",
        "ownership": "hyslrSttus.json",
        "affiliates": "otrCprInvstmntSttus.json",
    }

    def __init__(self, dart: DartProvider) -> None:
        self._dart = dart

    def _rows(self, endpoint: str, corp_code: str, year: int, reprt: str) -> list[dict]:
        """조회. status 013(데이터 없음)은 정상적인 '없음'이라 빈 리스트로 돌린다."""
        from arc.data.kr.dart import DartError

        try:
            payload = self._dart._get_json(
                endpoint,
                {
                    "corp_code": corp_code,
                    "bsns_year": str(year),
                    "reprt_code": reprt,
                },
            )
        except DartError as exc:
            if getattr(exc, "status", None) == "013":
                return []
            raise
        return payload.get("list") or []

    def fetch(
        self, symbol: str, fiscal_year: int, *, reprt_code: str = REPRT_ANNUAL
    ) -> PeriodicReportInfo:
        """4개 API를 조회해 묶음으로 돌린다. 실패한 항목은 `unavailable`에 남는다."""
        corp_code = self._dart.corp_code_for(symbol)
        now = self._dart._now()
        info = PeriodicReportInfo(fiscal_year=fiscal_year)

        parsers = {
            "shares": parse_share_counts,
            "dividend": parse_dividend,
            "audit": parse_audit_opinion,
            "workforce": parse_workforce,
            "ownership": parse_ownership,
            "affiliates": parse_affiliates,
        }
        for name, endpoint in self.ENDPOINTS.items():
            rows = self._rows(endpoint, corp_code, fiscal_year, reprt_code)
            parsed = parsers[name](rows, fiscal_year, now) if rows else None
            if parsed is None:
                info.unavailable.append(name)
            setattr(info, name, parsed)
        return info
