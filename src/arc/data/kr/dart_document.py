"""사업보고서 원문(`document.xml`) 조회 + 표 파싱.

왜 원문까지 가는가
------------------
DART에 **부문별 매출 API는 없다**([D18](../../docs/decisions.md)). 재무제표
API는 손익·재무상태만 주고, 정기보고서 주요정보 API는 주식수·배당·인력까지다.
부문별 매출은 사업보고서 본문 「II. 사업의 내용 → 4. 매출 및 수주상황」의
표에만 있다.

이 표가 없으면 노트는 "이 회사가 무엇을 팔아 돈을 버는가"에 답할 수 없다.
재무 기계학에서 멈추는 근본 원인이다.

원문의 형태
-----------
`/api/document.xml?rcept_no=`는 **ZIP**을 준다(Content-Type이 틀리게 온다).
안에 XML이 여러 개 있고 가장 큰 것이 본문이다. 표는 HTML이 아니라 DART 고유
태그(`TABLE/TR/TD` + `ROWSPAN`)이며, 셀 안에 `&cr;` 같은 엔티티가 섞인다.

원문 파싱은 API보다 깨지기 쉽다. 그래서 이 모듈은 **파싱 결과를 믿지 않는다** —
검증은 `finmodel.segments`가 재무제표 매출과 대조해서 한다.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass

_TABLE_RE = re.compile(r"<TABLE\b.*?</TABLE>", re.DOTALL | re.IGNORECASE)
_ROW_RE = re.compile(r"<TR\b[^>]*>(.*?)</TR>", re.DOTALL | re.IGNORECASE)
_CELL_RE = re.compile(r"<T[DHU]\b([^>]*)>(.*?)</T[DHU]>", re.DOTALL | re.IGNORECASE)
# 주석의 표는 본문과 달리 셀을 `<TE>`(table entry)로 쓴다. TD가 하나도 없는
# 행에서만 쓴다 — TD 안에 TE가 중첩된 표에서 둘 다 매칭하면 셀이 쪼개진다.
_ENTRY_RE = re.compile(r"<TE\b([^>]*)>(.*?)</TE>", re.DOTALL | re.IGNORECASE)
_SPAN_RE = re.compile(r'(ROWSPAN|COLSPAN)\s*=\s*"?(\d+)"?', re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<TITLE\b[^>]*>(.*?)</TITLE>", re.DOTALL | re.IGNORECASE)

# 셀 안에 섞여 오는 DART 엔티티
_ENTITIES = {"&cr;": " ", "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"'}


def clean_cell(raw: str) -> str:
    """셀 내용 → 평문. 태그·엔티티·중복 공백을 정리한다."""
    text = _TAG_RE.sub("", raw)
    for k, v in _ENTITIES.items():
        text = text.replace(k, v)
    return " ".join(text.split())


_NUM_RE = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?$")


def cell_number(cell: str) -> float | None:
    """셀 → 숫자. 숫자만 있는 셀이 아니면 None.

    회계 표기의 괄호는 **음수**다. `(1,234)`를 1,234로 읽으면 적자 부문이
    흑자로 뒤집힌다.
    """
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


def norm_cell(s: str) -> str:
    """라벨 비교용 정규화 — 공백을 제거한다 (`소 계` == `소계`)."""
    return s.replace(" ", "").strip()


def extract_main_xml(payload: bytes) -> str:
    """`document.xml` 응답(ZIP) → 본문 XML 문자열.

    ZIP 안에 첨부·별첨이 함께 있어 **가장 큰 파일**이 본문이다.
    인코딩은 UTF-8이지만 깨진 바이트가 섞이는 경우가 있어 대체 문자를 허용한다.
    """
    if payload[:2] != b"PK":
        return payload.decode("utf-8", "replace")
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        if not infos:
            return ""
        main = max(infos, key=lambda i: i.file_size)
        return z.read(main.filename).decode("utf-8", "replace")


def expand_table(table_xml: str) -> list[list[str]]:
    """DART 표 → 2차원 격자. **ROWSPAN·COLSPAN을 펼친다.**

    펼치지 않으면 열이 밀려 값이 엉뚱한 행에 붙는다. 실측: 파마리서치
    매출표는 품목군이 `ROWSPAN="3"`으로 내수/수출/계 세 행을 덮는다.
    """
    grid: list[list[str]] = []
    # (남은 행 수, 열 위치, 값) — 다음 행들에 흘려보낼 셀
    pending: list[list] = []

    for row_xml in _ROW_RE.findall(table_xml):
        row: list[str] = []
        # 이번 행에 먼저 놓아야 할 rowspan 잔여분
        carried = {}
        for item in pending:
            if item[0] > 0:
                carried[item[1]] = item[2]
                item[0] -= 1
        pending = [i for i in pending if i[0] > 0]

        cells = _CELL_RE.findall(row_xml) or _ENTRY_RE.findall(row_xml)
        col = 0
        for attrs, inner in cells:
            while col in carried:
                row.append(carried.pop(col))
                col += 1
            spans = {k.upper(): int(v) for k, v in _SPAN_RE.findall(attrs)}
            value = clean_cell(inner)
            colspan = max(1, spans.get("COLSPAN", 1))
            rowspan = max(1, spans.get("ROWSPAN", 1))
            for c in range(colspan):
                row.append(value if c == 0 else "")
                if rowspan > 1:
                    pending.append([rowspan - 1, col, value if c == 0 else ""])
                col += 1
        # 행 끝에 남은 잔여분
        for c in sorted(carried):
            while len(row) < c:
                row.append("")
            row.append(carried[c])
        grid.append(row)
    return grid


@dataclass(frozen=True)
class Section:
    """본문 내 한 섹션 — 제목과 그 아래 원문 조각."""

    title: str
    start: int
    body: str

    def tables(self) -> list[list[list[str]]]:
        return [expand_table(m.group(0)) for m in _TABLE_RE.finditer(self.body)]


def find_sections(text: str, *keywords: str, span: int = 40_000) -> list[Section]:
    """제목에 `keywords`가 모두 들어간 섹션을 **전부** 돌려준다.

    섹션 끝을 정확히 알 수 없어 다음 제목까지, 없으면 `span`만큼 자른다.
    표 파싱은 어차피 뒤에서 검증되므로 넉넉히 자르는 편이 안전하다.

    복수형이 필요한 이유: 부문 주석의 제목이 회사마다 다르고(「부문별 보고」·
    「부문별정보」·「영업부문」·「부문정보」) 같은 보고서에 **연결과 별도가 모두**
    실린다. 어느 것이 맞는지는 제목으로 가릴 수 없고 검산이 가른다 —
    후보를 다 넘기고 재무제표와 맞는 것을 고른다.
    """
    titles = [(m.start(), clean_cell(m.group(1))) for m in _TITLE_RE.finditer(text)]
    out: list[Section] = []
    for i, (pos, title) in enumerate(titles):
        if all(k in title for k in keywords):
            end = titles[i + 1][0] if i + 1 < len(titles) else pos + span
            out.append(Section(title=title, start=pos, body=text[pos : min(end, pos + span)]))
    return out


def find_section(text: str, *keywords: str, span: int = 40_000) -> Section | None:
    """제목에 `keywords`가 모두 들어간 **첫** 섹션."""
    found = find_sections(text, *keywords, span=span)
    return found[0] if found else None


# 표 위 캡션의 단위 표기 — 금액 스케일을 여기서 읽는다
_UNIT_SCALES = (
    ("십억원", 1_000_000_000),
    ("백만원", 1_000_000),
    ("천원", 1_000),
    ("억원", 100_000_000),
)


def detect_unit_scale(text: str) -> int | None:
    """`(단위 : 백만원, %)` → 1,000,000.

    단위를 못 읽으면 **금액을 쓰지 않는다.** 백만원을 원으로 읽으면 6자리가
    어긋나고, 그 숫자가 리포트에 실린다.
    """
    m = re.search(r"단위\s*[:：]\s*([^)\]]{1,30})", text)
    if not m:
        return None
    label = m.group(1)
    for name, scale in _UNIT_SCALES:
        if name in label:
            return scale
    if "원" in label:
        return 1
    return None


def fetch_document(dart, rcept_no: str) -> tuple[str, str | None]:
    """사업보고서 원문 XML을 가져온다. `(본문, 오류)`.

    본문이 5~8MB라 느리다(실측 3~8초). **한 번만 받아 여러 섹션에 쓴다** —
    섹션마다 다시 받으면 사업의 개요·주요 제품·매출 세 곳을 읽는 데 20초가
    넘게 걸린다.

    실패해도 노트 생성을 막지 않는다 — 원문 정보는 있으면 좋은 것이지
    없으면 못 쓰는 것이 아니다.
    """
    try:
        resp = dart._request("document.xml", {"rcept_no": rcept_no})
        text = extract_main_xml(resp.content)
    except Exception as exc:  # noqa: BLE001 — 어댑터별 예외 타입이 다르다
        return "", f"{type(exc).__name__}: {exc}"
    if not text:
        return "", "사업보고서 원문이 비어 있다."
    return text, None


def fetch_section(dart, rcept_no: str, *keywords: str) -> tuple[Section | None, str | None]:
    """섹션 하나만 필요할 때. 여러 개가 필요하면 `fetch_document`를 쓴다."""
    text, error = fetch_document(dart, rcept_no)
    if error:
        return None, error
    return find_section(text, *keywords), None
