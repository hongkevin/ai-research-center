"""공시 목차 → **숫자가 있는 자리로 가는 링크**.

왜 필요한가
-----------
노트의 숫자를 눌러 「원문 공시 열기」를 하면 보고서 **첫 화면**으로 갔다.
삼성물산 사업보고서는 8MB짜리 문서라, 부문 매출을 확인하려면 목차에서
「III. 재무에 관한 사항」을 찾아 들어가 주석을 뒤져야 한다. 검증 경로가
있다고 말은 하지만 실제로는 사람이 다시 찾아야 했다.

DART 뷰어는 문서 안 위치를 주소로 받는다::

    https://dart.fss.or.kr/report/viewer.do
        ?rcpNo=…&dcmNo=…&eleId=…&offset=…&length=…&dtd=…

이 네 값은 `dsaf001/main.do` 페이지의 스크립트에 목차 트리로 박혀 있다::

    var node1 = {};
    node1['text'] = "III. 재무에 관한 사항";
    node1['dcmNo'] = "11114893";
    node1['eleId'] = "17";
    node1['offset'] = "485431";
    node1['length'] = "8279578";

그래서 목차를 한 번 읽어 두면 **수치마다 자기 절로 가는 링크**를 만들 수 있다.

한계
----
절(節) 단위까지다. 「III. 재무에 관한 사항」 안에서 영업부문 주석의 몇 번째
표인지까지는 목차에 없다. 그래도 8MB 문서의 첫 장에서 시작하는 것과, 해당
절에서 시작하는 것은 다르다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

MAIN_URL = "https://dart.fss.or.kr/dsaf001/main.do"
VIEWER_URL = "https://dart.fss.or.kr/report/viewer.do"

# node3['text'] = "…" 형태. 변수 이름의 숫자가 곧 목차 깊이다.
_ASSIGN = re.compile(r"node(\d+)\['(\w+)'\]\s*=\s*\"([^\"]*)\"")


@dataclass(frozen=True)
class TocEntry:
    """목차 한 줄 + 그 자리로 가는 좌표."""

    text: str
    depth: int
    rcept_no: str
    dcm_no: str
    ele_id: str
    offset: str
    length: str
    dtd: str

    @property
    def url(self) -> str:
        return (
            f"{VIEWER_URL}?rcpNo={self.rcept_no}&dcmNo={self.dcm_no}&eleId={self.ele_id}"
            f"&offset={self.offset}&length={self.length}&dtd={self.dtd}"
        )


def parse_toc(html: str) -> list[TocEntry]:
    """`dsaf001/main.do` HTML → 목차.

    같은 `nodeN` 변수가 반복 사용된다(`var node2 = {}`로 매번 초기화). `text`가
    한 항목의 첫 대입이므로, `text`를 만나면 앞의 것을 확정하고 새로 연다.
    """
    out: list[TocEntry] = []
    cur: dict[str, str] = {}
    depth = 0

    def flush() -> None:
        if cur.get("text") and cur.get("dcmNo"):
            out.append(
                TocEntry(
                    text=cur["text"].strip(),
                    depth=depth,
                    rcept_no=cur.get("rcpNo", ""),
                    dcm_no=cur["dcmNo"],
                    ele_id=cur.get("eleId", ""),
                    offset=cur.get("offset", ""),
                    length=cur.get("length", ""),
                    dtd=cur.get("dtd", "dart4.xsd"),
                )
            )

    for m in _ASSIGN.finditer(html):
        level, field, value = int(m.group(1)), m.group(2), m.group(3)
        if field == "text":
            flush()
            cur, depth = {"text": value}, level
        else:
            cur[field] = value
    flush()
    return out


def fetch_toc(rcept_no: str, *, timeout: float = 15.0) -> list[TocEntry]:
    """목차 조회. **실패해도 예외를 내지 않는다** — 링크는 향상이지 전제가 아니다."""
    try:
        r = httpx.get(
            MAIN_URL,
            params={"rcpNo": rcept_no},
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ARC/0.1)"},
            follow_redirects=True,
        )
        r.raise_for_status()
    except (httpx.HTTPError, OSError):
        return []
    return parse_toc(r.text)


# 데이터셋 이름에 들어 있는 말 → 목차에서 찾을 말.
#
# **좁은 것이 먼저다.** 실측으로 두 번 어긋났다: 「최대주주 및 특수관계인
# 주식소유 현황」이 "주식" 규칙에 먼저 걸려 「4. 주식의 총수 등」으로 갔고,
# 「주주에 관한 사항」은 목표를 튜플이 아닌 문자열로 써서 "주" 한 글자가
# 걸렸다. 순서와 쉼표 둘 다 의미가 있다.
_ROUTES: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("최대주주", "주주"), ("주주에 관한 사항",)),
    (("영업부문", "부문"), ("재무제표 주석", "연결재무제표 주석", "재무에 관한 사항")),
    (("배당",), ("배당에 관한 사항", "재무에 관한 사항")),
    (("감사", "회계감사"), ("감사인의 감사의견", "감사인에 관한 사항")),
    (("임직원", "직원", "인력"), ("임원 및 직원", "임원 및 직원 등에 관한 사항")),
    (("타법인", "출자"), ("타법인 출자", "그 밖에 투자자 보호")),
    (("제품", "서비스"), ("주요 제품 및 서비스", "사업의 내용")),
    (("수주", "매출 및"), ("매출 및 수주상황", "사업의 내용")),
    (("생산", "원재료", "설비"), ("원재료 및 생산설비", "사업의 내용")),
    (("사업의 개요", "사업 내용"), ("사업의 개요", "사업의 내용")),
    (("주식", "자기주식", "증자", "지분"), ("주식의 총수", "회사의 개요")),
    (("재무제표", "손익", "주요계정"), ("연결재무제표", "재무제표", "재무에 관한 사항")),
]


def locate(toc: list[TocEntry], dataset: str | None) -> TocEntry | None:
    """데이터셋 이름 → 그 숫자가 실린 절. 못 고르면 None.

    **틀린 자리로 보내느니 안 보낸다.** 엉뚱한 절이 열리면 검증 경로가 있다는
    주장 자체가 깨진다 — 그럴 바엔 지금처럼 보고서 첫 장이 낫다.
    """
    if not toc or not dataset:
        return None
    for needles, targets in _ROUTES:
        if not any(n in dataset for n in needles):
            continue
        for target in targets:
            for e in toc:
                if target in e.text:
                    return e
    return None
