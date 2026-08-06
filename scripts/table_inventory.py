"""코퍼스 PDF → **표 인벤토리**.

왜 필요한가
-----------
실제 증권사 리포트는 글보다 표가 많다. 벤치마크 실측: 표 비중 **77% · 71%**.
그런데 「표를 더 넣자」로는 무엇을 넣을지 정해지지 않는다. **어떤 표가
표준인지**를 세어서 알아내야 한다.

무엇을 세는가
-------------
1. 표 비중 (표 안 글자 / 전체 글자) — 학생 리포트와 실제 리포트를 가른다
2. 표의 **머리행**과 **행 라벨** — 어떤 계정·기간축이 표준인가
3. 축 형태 — 몇 개년인가, 분기가 있는가

한계
----
`find_tables()`는 선이 있는 표를 잘 잡고 선 없는 표는 놓친다. 그래서
**표 비중은 하한**이다. 그리고 CMap이 깨진 문서는 라벨이 사문자로 나오므로
`garbled_ratio()`로 걸러낸다.
"""

from __future__ import annotations

import collections
import json
import re
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fitz  # noqa: E402

from arc.ingest.convert import GARBLED_AT, garbled_ratio  # noqa: E402

# 연도·분기 축 탐지. 「2024A」 「2026F」 「1Q25」 「25.3Q」
_YEAR_COL = re.compile(r"(19|20)\d{2}\s*[AFEP]?")
_QUARTER_COL = re.compile(r"[1-4]\s?Q\s?\d{2}|\d{2}\s?\.\s?[1-4]Q")

# 행 라벨 정규화 — 단위 표기와 공백을 떼면 하우스가 달라도 같은 것이 모인다
_UNIT = re.compile(r"\((?:십억원|억원|백만원|원|%|배|천주|주|YoY|QoQ)[^)]*\)")


def norm_label(text: str) -> str:
    s = _UNIT.sub("", str(text or "")).strip()
    s = re.sub(r"\s+", "", s)
    return s[:24]


def scan(path: Path) -> dict | None:
    try:
        doc = fitz.open(path)
    except Exception:
        return None
    text = "".join(p.get_text() for p in doc)
    if garbled_ratio(text) >= GARBLED_AT:
        return None

    chars = len(text.replace("\n", "").replace(" ", ""))
    if chars < 500:
        return None

    tab_chars = 0
    labels: list[str] = []
    headers: list[str] = []
    years = quarters = 0
    ntab = 0
    for page in doc:
        try:
            tables = page.find_tables().tables
        except Exception:
            continue
        for t in tables:
            try:
                rows = t.extract()
            except Exception:
                continue
            if not rows:
                continue
            ntab += 1
            tab_chars += sum(len(str(c) or "") for r in rows for c in r)
            head = " ".join(str(c or "") for c in rows[0])
            headers.append(norm_label(rows[0][0] if rows[0] else ""))
            years = max(years, len(_YEAR_COL.findall(head)))
            quarters = max(quarters, len(_QUARTER_COL.findall(head)))
            # 첫 열이 행 라벨이다
            labels += [norm_label(r[0]) for r in rows[1:] if r and r[0]]
    pages = doc.page_count
    doc.close()
    return {
        "pages": pages,
        "chars": chars,
        "table_chars": tab_chars,
        "tables": ntab,
        "labels": labels,
        "headers": headers,
        "year_cols": years,
        "quarter_cols": quarters,
    }


def main() -> None:
    groups = {"snusmic": "학생(SMIC)", "consensus": "증권사", "market": "증권사(수상)"}
    out: dict[str, list[dict]] = collections.defaultdict(list)
    for group in groups:
        files = sorted(Path("corpus", group).rglob("*.pdf"))
        for i, f in enumerate(files, 1):
            got = scan(f)
            if got:
                out[group].append(got)
            if i % 100 == 0:
                print(f"  {group} {i}/{len(files)}", file=sys.stderr)

    summary = {}
    for group, rows in out.items():
        if not rows:
            continue
        ratios = [r["table_chars"] / r["chars"] for r in rows if r["chars"]]
        ratios.sort()
        labels = collections.Counter(x for r in rows for x in set(r["labels"]) if len(x) > 1)
        summary[group] = {
            "name": groups[group],
            "n": len(rows),
            "table_ratio_median": ratios[len(ratios) // 2],
            "tables_median": sorted(r["tables"] for r in rows)[len(rows) // 2],
            "pages_median": sorted(r["pages"] for r in rows)[len(rows) // 2],
            "with_quarter_axis": sum(1 for r in rows if r["quarter_cols"] >= 2) / len(rows),
            "year_cols_median": sorted(r["year_cols"] for r in rows)[len(rows) // 2],
            "top_labels": labels.most_common(45),
        }
    Path("corpus/table_inventory.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    for g, s in summary.items():
        print(f"\n=== {s['name']}  ({s['n']}편)")
        print(
            f"  표 비중 중앙값 {s['table_ratio_median']:.0%} · 표 {s['tables_median']}개 "
            f"· {s['pages_median']}쪽 · 연도열 {s['year_cols_median']}"
        )
        print(f"  분기축을 가진 비율 {s['with_quarter_axis']:.0%}")
        print("  많이 나오는 행 라벨:")
        for lab, n in s["top_labels"][:18]:
            print(f"     {n * 100 // s['n']:3}%  {lab}")


if __name__ == "__main__":
    main()
