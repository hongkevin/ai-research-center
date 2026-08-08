"""수주 공시 — **미드스몰캡의 최대 사건** (D87).

왜 이것만 따로 읽나
-------------------
[D87](../../../docs/decisions.md#d87)에서 리포트 60여 편을 읽고 확인한 것:
미드스몰캡 애널리스트의 리포트는 **수주로 시작한다.**

    코세스 (2026-07-22) — "지난 7/13 동사는 블룸에너지향 1,500억원(3GW) 규모의
    SOFC 전극 셀 자동화 장비 수주를 공시했다"
    씨이랩 (2026-08-07) — "26년 확보한 수주잔고는 3,458억원으로, 25년 매출액
    103억원의 34배 수준이다"

그런데 우리 브리프는 공시를 **제목만** 목록에 섞어 낸다. 「단일판매ㆍ공급계약체결」
이라는 여섯 글자가 다른 공시 열 건과 같은 크기로 지나간다.

추측이 아니라 정형 필드다
-------------------------
`chat/market.py`는 공시를 실을 때 *"본문은 안 읽었습니다 · 추측하지 마십시오"*
라고 막는다. 그 경계는 옳지만 **이 공시에는 해당하지 않는다** — 금액도, 매출액
대비 비율도, 계약상대방도 공시 서식의 **칸에 적혀 있다.** 읽는 것이지 해석하는
것이 아니다. 실측(2026-07-13 코세스):

    확정 계약금액      150,420,000,000
    최근 매출액(원)     82,379,163,292
    매출액 대비(%)      183
    계약상대방          Bloom Energy Corporation
    └ 주요사업         고체 산화물 연료전지(SOFC)의 설계, 제조, 판매 및 설치 등

**「매출액 대비 183%」는 그가 손으로 쓰는 문장이 아니라 공시가 계산해 둔 값이다.**

계약상대방이 왜 중요한가
------------------------
숫자가 아니라 **밸류체인 앵커 이름**이다. 그의 논리가 늘 「누구에게 파는가」로
서고(블룸에너지향·짐머향·삼성SDS향), 공시에는 그 상대방의 **주요사업**까지
적혀 있다. 발굴이 여기서 시작한다.

무엇을 안 하나
--------------
**계약의 의미를 판단하지 않는다.** 「독점 공급업체로 올라섰다」는 그의 판단이고
공시에 없다. 여기서 나오는 것은 「누구에게 · 얼마에 · 언제까지 · 최근 매출의
몇 %」까지다.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass

log = logging.getLogger("arc.data.kr.contracts")

# 이 제목이 붙은 공시만 읽는다. 「ㆍ」는 가운뎃점(U+318D)이라 일반 마침표와 다르다
TITLE = "단일판매ㆍ공급계약체결"

# **정정 공시는 원 공시를 갈아 끼운 것이다.** 둘 다 세면 같은 수주가 두 건이 된다.
_AMEND = re.compile(r"^\[(기재정정|첨부정정|정정)\]")

# 같은 사건의 그림자. 수주가 아니라 그 때문에 생긴 거래정지다.
_SHADOW = "주권매매거래정지"

_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


@dataclass
class Contract:
    """수주 한 건. **공시에 적힌 것만 담는다.**"""

    symbol: str
    rcept_no: str
    filed_at: str = ""  # YYYY-MM-DD
    # 누구에게 파는가. **밸류체인 앵커다**
    counterparty: str = ""
    counterparty_business: str = ""
    amount: float | None = None  # 계약금액 총액(원)
    recent_revenue: float | None = None  # 공시가 밝힌 최근 매출액(원)
    ratio_pct: float | None = None  # 매출액 대비(%) — **공시가 계산해 둔 값**
    starts_at: str = ""
    ends_at: str = ""
    amended: bool = False

    @property
    def url(self) -> str:
        """사람이 열어 확인할 곳. **원문이다.**"""
        return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={self.rcept_no}"

    @property
    def display_amount(self) -> str:
        """`150_420_000_000` → `1,504억원`. 억 단위가 이 바닥의 말투다."""
        if not self.amount:
            return ""
        eok = self.amount / 100_000_000
        if eok >= 10_000:
            return f"{eok / 10_000:,.2f}조원"
        return f"{eok:,.0f}억원"

    @property
    def headline(self) -> str:
        """「블룸에너지 1,504억원 (최근 매출 대비 183%)」 한 줄.

        **비율을 빼지 않는다.** 스몰캡에서 1,504억은 회사에 따라 사소하기도
        하고 회사를 바꾸기도 하는데, 그 차이를 말해 주는 것이 비율이다.
        """
        parts = [self.counterparty or "계약상대방 미공개"]
        if self.display_amount:
            parts.append(self.display_amount)
        if self.ratio_pct is not None:
            parts.append(f"(최근 매출 대비 {self.ratio_pct:,.0f}%)")
        return " ".join(parts)


def is_contract(title: str) -> bool:
    """수주 공시인가. **거래정지 그림자를 뺀다.**

    제목이 `주권매매거래정지 (단일판매공급계약)`인 것이 같이 잡히는데, 그건
    수주 때문에 생긴 별개 공시라 세면 한 건이 두 건이 된다.
    """
    if _SHADOW in title:
        return False
    return TITLE in title or "단일판매공급계약" in title


def _cells(html: str) -> list[str]:
    """공시 원문 HTML → 셀 목록. **표를 쓰지 않고 평평하게 읽는다.**

    DART 서식은 `xforms` 스팬이 중첩된 표라 파서를 세우면 서식 개정마다 깨진다.
    라벨과 값이 **바로 이웃**이라는 성질만 쓰면 그 취약함이 사라진다.
    """
    flat = _TAGS.sub("|", html)
    return [_SPACE.sub(" ", c).strip() for c in flat.split("|") if c.strip()]


def _after(cells: list[str], label: str, *, start: int = 0) -> str:
    """라벨 바로 다음 칸. 없으면 빈 문자열."""
    for i in range(start, len(cells)):
        if cells[i] == label:
            return cells[i + 1] if i + 1 < len(cells) else ""
    return ""


def _index_of(cells: list[str], needle: str) -> int:
    for i, c in enumerate(cells):
        if needle in c:
            return i
    return -1


def _num(text: str) -> float | None:
    """`"150,420,000,000"` → 값. `-`나 빈 칸은 `None` — **0이 아니다.**

    조건부 계약금액이 `-`로 오는 것이 정상인데, 그걸 0으로 읽으면 「조건부
    금액 0원」이라는 없는 사실이 생긴다.
    """
    cleaned = text.replace(",", "").replace("원", "").strip()
    if not cleaned or cleaned in ("-", "–", "—"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse(html: str, *, symbol: str, rcept_no: str, filed_at: str = "") -> Contract:
    """공시 원문 → `Contract`. **못 읽은 칸은 비워 둔다.**

    서식이 바뀌어 한 칸을 못 읽어도 나머지는 살린다 — 전부 아니면 전무로 두면
    서식 개정 한 번에 이 기능이 통째로 조용히 꺼진다.
    """
    cells = _cells(html)
    out = Contract(symbol=symbol, rcept_no=rcept_no, filed_at=filed_at)

    out.amount = _num(_after(cells, "계약금액 총액(원)")) or _num(_after(cells, "확정 계약금액"))

    # **「최근 매출액(원)」이 두 번 나온다** — 우리 회사 것과 계약상대방 것.
    # 상대방 쪽은 `3. 계약상대방` 뒤에 있고 라벨에 `-`가 붙는다. 순서로 가르면
    # 서식이 바뀔 때 상대방 매출(코세스 예: 2.9조)을 우리 매출로 읽는다.
    party_at = _index_of(cells, "계약상대방")
    revenue_label = "최근 매출액(원)"
    for i, c in enumerate(cells):
        if c == revenue_label and (party_at < 0 or i < party_at):
            out.recent_revenue = _num(cells[i + 1] if i + 1 < len(cells) else "")
            break

    out.ratio_pct = _num(_after(cells, "매출액 대비(%)"))

    if party_at >= 0:
        out.counterparty = (cells[party_at + 1] if party_at + 1 < len(cells) else "").strip()
        out.counterparty_business = _after(cells, "- 주요사업", start=party_at)

    out.starts_at = _after(cells, "시작일")
    out.ends_at = _after(cells, "종료일")
    return out


def fetch(dart, disclosures: list, *, symbol: str, limit: int = 8) -> list[Contract]:
    """공시 목록 → 수주 목록. **원문을 건마다 받는다.**

    `limit`을 두는 이유는 한 건이 한 콜이기 때문이다 — 20건을 훑으면 20콜이고,
    D69에서 IP가 끊긴 그 종류의 일이 된다. 최신부터 세어 자른다.

    **정정 공시가 원본을 덮는다.** 같은 수주에 대해 원 공시와 `[기재정정]`이
    둘 다 잡히는데, 계약금액이 바뀌었을 수 있으므로 **나중 것이 맞다.**
    """
    from arc.data.kr import dart_document

    picked = [d for d in disclosures if is_contract(getattr(d, "title", ""))]
    picked.sort(key=lambda d: str(getattr(d, "filed_at", "")), reverse=True)

    out: list[Contract] = []
    for d in picked[:limit]:
        rcept_no = str(getattr(d, "rcept_no", ""))
        if not rcept_no:
            continue
        try:
            html, _ = dart_document.fetch_document(dart, rcept_no)
        except Exception as exc:  # noqa: BLE001 — 한 건이 실패해도 나머지는 낸다
            log.warning("수주 공시 원문을 못 받았습니다 (%s): %s", rcept_no, exc)
            continue
        contract = parse(
            html,
            symbol=symbol,
            rcept_no=rcept_no,
            filed_at=str(getattr(d, "filed_at", ""))[:10],
        )
        contract.amended = bool(_AMEND.match(getattr(d, "title", "")))
        # **첨부만 고친 정정에는 표가 없다.** 실측: 코세스 2026-04-30
        # `[첨부정정]`이 상대방도 금액도 없이 파싱돼 「계약상대방 미공개」라는
        # 유령 수주가 한 건 생겼다. 아무것도 안 읽혔으면 그것은 수주가 아니다.
        if contract.amount is None and not contract.counterparty:
            continue
        out.append(contract)
    return _dedupe(out)


def _dedupe(rows: list[Contract]) -> list[Contract]:
    """같은 수주의 정정본을 하나로. **계약상대방 + 계약기간**으로 묶는다.

    접수번호는 정정마다 새로 나오고 금액은 바뀔 수 있으니 열쇠가 못 된다.
    상대방과 납기가 같으면 같은 건으로 본다 — 나중 것(정정본)이 이긴다.
    """
    seen: dict[tuple[str, str, str], Contract] = {}
    for row in sorted(rows, key=lambda r: r.filed_at):
        seen[(row.counterparty, row.starts_at, row.ends_at)] = row
    return sorted(seen.values(), key=lambda r: r.filed_at, reverse=True)


def recent(rows: list[Contract], *, days: int = 90, today: dt.date | None = None) -> list[Contract]:
    """최근 것만. 브리프는 「어젯밤 사이」를 묻지 1년 전 수주를 묻지 않는다."""
    today = today or dt.datetime.now(dt.UTC).date()
    cut = today - dt.timedelta(days=days)
    out = []
    for row in rows:
        try:
            when = dt.date.fromisoformat(row.filed_at)
        except ValueError:
            continue
        if when >= cut:
            out.append(row)
    return out
